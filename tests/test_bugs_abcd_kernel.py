"""Comprehensive unit and integration tests for Bugs A, C, and Calibration D."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from quadgate.contracts import (
    CartridgeSpec,
    ClassifierHead,
    ClassifierRequest,
    GateEvent,
    GateType,
    ModelTier,
    TruthOracleSpec,
    VerdictAction,
)
from quadgate.drivers.base import ClassifierDriver, DriverCapabilities
from quadgate.drivers.onnx_local import ZeroDepDriver
from quadgate.kernel.gates import (
    GateEngine,
    ThresholdConfig,
    TierModelConfig,
    _zero_dep_tier_pass_priors,
    load_calibrated_priors,
    load_cartridge,
)


class _MockCloudDriver(ClassifierDriver):
    """Mock cloud classifier driver for testing Gate 3 Decision1 blending."""

    def __init__(
        self,
        score_map: dict[str, float] | None = None,
        default_score: float = 0.50,
    ) -> None:
        self._score_map = score_map or {
            "tier:floor": 0.85,
            "tier:mid": 0.90,
            "tier:frontier": 0.98,
        }
        self._default_score = default_score
        super().__init__()

    @property
    def driver_id(self) -> str:
        return "mock_cloud"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=True,
            max_candidates_per_pass=256,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        res: dict[str, float] = {}
        for h in request.heads:
            if h.head_id in self._score_map:
                res[h.head_id] = self._score_map[h.head_id]
            elif h.head_id.startswith("inv:"):
                res[h.head_id] = self._score_map.get("inv", self._default_score)
            else:
                res[h.head_id] = self._default_score
        return res

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Bug A: general_reasoning Cartridge & Gate 4 JSON/Numeric Oracle
# ---------------------------------------------------------------------------


def test_bug_a_truth_oracle_spec_default() -> None:
    """Verify TruthOracleSpec has check_clause_completeness: bool = True by default."""
    spec = TruthOracleSpec()
    assert spec.check_clause_completeness is True
    assert spec.timeout_ms == 800
    assert spec.checks == []


def test_bug_a_general_reasoning_cartridge_schema() -> None:
    """Verify general_reasoning.yaml desugars into valid CartridgeSpec with check_clause_completeness=False."""
    spec = load_cartridge("general_reasoning")
    assert isinstance(spec, CartridgeSpec)
    assert spec.name == "general_reasoning"
    assert spec.version == "1.0"
    assert spec.context_unit.indexer == "markdown_sections"
    assert "**/*.md" in spec.context_unit.include_globs
    assert "**/.git/**" in spec.context_unit.exclude_globs
    assert "**/.env*" in spec.context_unit.private_tier1_globs
    assert "write_to_file" in spec.mutation_tools
    assert "Write" in spec.mutation_tools

    inv_ids = [inv.id for inv in spec.invariants]
    assert "INV_GEN_01_SECRET_EXPOSURE" in inv_ids

    oracle = spec.truth_oracle
    assert oracle.timeout_ms == 500
    assert "verify_valid_json_answer" in oracle.checks
    assert oracle.check_clause_completeness is False


@pytest.mark.asyncio
async def test_bug_a_gate4_valid_json_and_numeric_answers(tmp_path: Path) -> None:
    """Verify verify_valid_json_answer oracle and skipped clause splitting."""
    cartridge = load_cartridge("general_reasoning")
    engine = GateEngine(cartridge=cartridge)

    # 1. Non-empty JSON with final_answer numeric
    ans_file = tmp_path / "answer.json"
    ans_file.write_text(
        json.dumps({"reasoning": "Step 1: 2+2=4", "final_answer": 4}),
        encoding="utf-8",
    )
    v_json = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-1",
            workspace_root=str(tmp_path),
            prompt_text="Solve 2+2 and provide final_answer",
            touched_files=["answer.json"],
        )
    )
    assert v_json.action == VerdictAction.ALLOW

    # 2. Standalone numeric answer
    ans_file.write_text("#### 42\n", encoding="utf-8")
    v_num = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-2",
            workspace_root=str(tmp_path),
            prompt_text="What is the answer?",
            touched_files=["answer.json"],
        )
    )
    assert v_num.action == VerdictAction.ALLOW

    # 3. Plain float standalone answer
    ans_file.write_text("3.14159", encoding="utf-8")
    v_float = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-3",
            workspace_root=str(tmp_path),
            prompt_text="Compute pi",
            touched_files=["answer.json"],
        )
    )
    assert v_float.action == VerdictAction.ALLOW

    # 4. Empty JSON dict -> failure
    ans_file.write_text("{}", encoding="utf-8")
    v_empty = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-4",
            workspace_root=str(tmp_path),
            prompt_text="Solve task",
            touched_files=["answer.json"],
            strike_count=1,
        )
    )
    assert v_empty.action == VerdictAction.BLOCK

    # 5. Non-JSON prose with no numbers -> failure
    ans_file.write_text("I could not figure out this problem at all.", encoding="utf-8")
    v_prose = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-5",
            workspace_root=str(tmp_path),
            prompt_text="Solve problem",
            touched_files=["answer.json"],
            strike_count=1,
        )
    )
    assert v_prose.action == VerdictAction.BLOCK

    # 6. JSON with non-finite / boolean final_answer -> failure
    ans_file.write_text(json.dumps({"final_answer": True}), encoding="utf-8")
    v_bool = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="gen-6",
            workspace_root=str(tmp_path),
            prompt_text="Solve problem",
            touched_files=["answer.json"],
            strike_count=1,
        )
    )
    assert v_bool.action == VerdictAction.BLOCK


# ---------------------------------------------------------------------------
# Bug C: knowledge_vault Prompt Wikilinks & Stage-2A Tier-1 Private Leak Guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bug_c_prompt_wikilinks_gate4(tmp_path: Path) -> None:
    """Verify prompt-requested wikilinks check missing source references in Gate 4."""
    cartridge = load_cartridge("knowledge_vault")
    engine = GateEngine(cartridge=cartridge)

    # Setup workspace documents
    doc_a = tmp_path / "notes" / "alpha_protocol.md"
    doc_a.parent.mkdir(parents=True, exist_ok=True)
    doc_a.write_text("# Alpha Protocol\nClinical trial protocol details.\n", encoding="utf-8")

    doc_b = tmp_path / "notes" / "beta_dosage.md"
    doc_b.write_text("# Beta Dosage\nRecommended dosage errata.\n", encoding="utf-8")

    summary = tmp_path / "summary.md"

    # Case 1: Prompt explicitly requests wikilinks; output note missing beta_dosage
    summary.write_text(
        "# Synthesis\n"
        "Reference [[alpha_protocol]] for screening guidelines.\n"
        "Source: internal audit report\n",
        encoding="utf-8",
    )
    v_missing = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="kv-wl-1",
            workspace_root=str(tmp_path),
            prompt_text="Synthesize notes/alpha_protocol.md and notes/beta_dosage.md with required [[wikilink]] citations.",
            touched_files=["summary.md"],
            current_tier=ModelTier.MID,
            strike_count=1,
        )
    )
    assert v_missing.action == VerdictAction.BLOCK
    assert "Missing required [[wikilink]] citation(s) for referenced source(s): beta_dosage" in (v_missing.reason or "")

    # Case 2: Output note includes both wikilinks -> ALLOW
    summary.write_text(
        "# Synthesis of Notes\n"
        "Synthesize notes/alpha_protocol.md and notes/beta_dosage.md with required wikilink citations.\n"
        "Reference [[alpha_protocol]] for screening and [[beta_dosage]] for dosing.\n"
        "Source: internal audit report\n",
        encoding="utf-8",
    )
    v_ok = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="kv-wl-2",
            workspace_root=str(tmp_path),
            prompt_text="Synthesize notes/alpha_protocol.md and notes/beta_dosage.md with required [[wikilink]] citations.",
            touched_files=["summary.md"],
            current_tier=ModelTier.MID,
            strike_count=0,
        )
    )
    assert v_ok.action == VerdictAction.ALLOW

    # Case 3: Prompt does not request wikilinks -> passes standard citation check
    v_noprompt = await engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="kv-wl-3",
            workspace_root=str(tmp_path),
            prompt_text=None,
            touched_files=["summary.md"],
        )
    )
    assert v_noprompt.action == VerdictAction.ALLOW


@pytest.mark.asyncio
async def test_bug_c_stage2a_tier1_private_leak_guard(tmp_path: Path) -> None:
    """Verify 0 ms Stage-2A deterministic block when mutating non-private files with Tier-1 secrets."""
    cartridge = load_cartridge("software_engineering")
    engine = GateEngine(cartridge=cartridge)

    # Create private .env with structured secret and KV secret
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=localhost\n"
        "API_SECRET_TOKEN=sk-live-secret-99482910\n"
        "AWS_KEY=AKIA-PROD-SEC-9988\n",
        encoding="utf-8",
    )

    # Attempt to write the secret token into a non-private file (e.g. app.py)
    leak_code = 'TOKEN = "sk-live-secret-99482910"\n'
    v_leak = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sec-leak-1",
            workspace_root=str(tmp_path),
            tool_name="write_to_file",
            tool_args={"TargetFile": "app.py", "CodeContent": leak_code},
            touched_files=["app.py"],
            diff_text=leak_code,
            strike_count=0,
        )
    )
    assert v_leak.action == VerdictAction.BLOCK
    assert v_leak.violation_receipt is not None
    assert v_leak.violation_receipt.rule_id == "INV_01_PRIVATE_TIER1_LEAK"
    assert v_leak.violation_receipt.stage == "deterministic_2a"
    assert v_leak.violation_receipt.strike_count == 1
    assert v_leak.violation_receipt.escalate_to_human is False
    # Remediation cites source without echoing the secret token
    assert ".env" in v_leak.violation_receipt.remediation
    assert "sk-live-secret-99482910" not in v_leak.violation_receipt.remediation

    # Strike 2 escalates to human confirmation
    v_leak2 = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sec-leak-1",
            workspace_root=str(tmp_path),
            tool_name="write_to_file",
            tool_args={"TargetFile": "app.py", "CodeContent": leak_code},
            touched_files=["app.py"],
            diff_text=leak_code,
            strike_count=1,
        )
    )
    assert v_leak2.action == VerdictAction.BLOCK
    assert v_leak2.violation_receipt is not None
    assert v_leak2.violation_receipt.strike_count == 2
    assert v_leak2.violation_receipt.escalate_to_human is True
    assert "HUMAN CONFIRMATION REQUIRED" in v_leak2.violation_receipt.remediation

    # Non-private file without secrets is ALLOWED
    clean_code = 'TOKEN = os.getenv("API_SECRET_TOKEN")\n'
    v_clean = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sec-clean-1",
            workspace_root=str(tmp_path),
            tool_name="write_to_file",
            tool_args={"TargetFile": "app.py", "CodeContent": clean_code},
            touched_files=["app.py"],
            diff_text=clean_code,
            strike_count=0,
        )
    )
    assert v_clean.action == VerdictAction.ALLOW


# ---------------------------------------------------------------------------
# Calibration D: Continuous Logistic Routing & Configurable ECC
# ---------------------------------------------------------------------------


def test_calibration_d_default_ecc_preservation() -> None:
    """Verify default compute_ecc preserves exactly 0.20 when p_pass=0.90."""
    cartridge = load_cartridge("software_engineering")
    engine = GateEngine(cartridge=cartridge)
    ecc = engine.compute_ecc(ModelTier.FLOOR, p_pass=0.90)
    assert ecc == pytest.approx(0.20)


def test_calibration_d_custom_tier_costs_and_ecc_savings_factor() -> None:
    """Verify custom tier_run_costs and ecc_savings_factor are respected."""
    cartridge = load_cartridge("software_engineering")
    custom_costs = {
        ModelTier.FLOOR: 0.05,
        ModelTier.MID: 0.20,
        ModelTier.FRONTIER: 0.80,
    }
    engine = GateEngine(
        cartridge=cartridge,
        tier_run_costs=custom_costs,
        ecc_savings_factor=0.65,
    )
    # p_pass=0.90 -> 0.05 + (1 - 0.90) * 1.00 = 0.15
    assert engine.compute_ecc(ModelTier.FLOOR, p_pass=0.90) == pytest.approx(0.15)
    assert engine._ecc_savings_factor == pytest.approx(0.65)


def test_calibration_d_continuous_logistic_smooth_64_to_67_words() -> None:
    """Verify continuous logistic curve eliminates the 65-word cliff: |P_floor(64) - P_floor(67)| < 0.03."""
    words_64 = " ".join(["execute"] * 64)
    words_67 = " ".join(["execute"] * 67)

    p_64 = _zero_dep_tier_pass_priors(words_64, ["target.py"])
    p_67 = _zero_dep_tier_pass_priors(words_67, ["target.py"])

    delta = abs(p_64[ModelTier.FLOOR] - p_67[ModelTier.FLOOR])
    assert delta < 0.03, f"Step cliff detected between 64 and 67 words: delta={delta:.5f} >= 0.03"


@pytest.mark.asyncio
async def test_calibration_d_load_calibrated_priors_and_active_routing(tmp_path: Path) -> None:
    """Verify load_calibrated_priors and empirical priors routing under active calibration."""
    priors_data = {
        "schema_version": "1.0",
        "calibrated_on_receipts": 1007,
        "simulations": 20000,
        "seed": 42,
        "tier_run_costs": {"floor": 0.08, "mid": 0.25, "frontier": 0.95},
        "ecc_savings_factor": 0.70,
        "thresholds": {"gate3_floor_confidence": 0.80, "gate3_mid_confidence": 0.58},
        "cartridge_priors": {
            "general_reasoning": {"floor": 0.92, "mid": 0.96, "frontier": 0.99},
            "software_engineering": {"floor": 0.88, "mid": 0.94, "frontier": 0.98},
            "knowledge_vault": {"floor": 0.25, "mid": 0.85, "frontier": 0.98},
            "financial_ops": {"floor": 0.20, "mid": 0.80, "frontier": 0.98},
            "legal_redline": {"floor": 0.20, "mid": 0.80, "frontier": 0.98},
        },
        "logistic_features": {
            "multi_lib_complexity_re": r"\b(?:numpy|pandas|scipy|torch|bigcodebench)\b",
            "floor_logit_weights": {
                "intercept": 3.2,
                "log_word_count": -0.70,
                "has_high_complexity": -2.8,
                "has_multi_lib": -2.8,
                "has_low_complexity": 1.5,
                "target_file_count_over_3": -2.2,
            },
            "mid_logit_weights": {
                "intercept": 4.0,
                "log_word_count": -0.65,
                "has_high_complexity": -2.8,
                "has_multi_lib": -2.8,
                "has_low_complexity": 1.2,
                "target_file_count_over_3": -1.8,
            },
            "cloud_blend_alpha": 0.5,
        },
    }
    priors_file = tmp_path / "gate3_calibrated_priors.json"
    priors_file.write_text(json.dumps(priors_data, indent=2), encoding="utf-8")

    loaded = load_calibrated_priors(priors_file)
    assert loaded["calibrated_on_receipts"] == 1007

    cart_se = load_cartridge("software_engineering")
    engine = GateEngine(cartridge=cart_se, calibrated_priors=loaded)

    # Verify overrides applied from calibrated_priors
    assert engine._tier_run_cost[ModelTier.FLOOR] == pytest.approx(0.08)
    assert engine._ecc_savings_factor == pytest.approx(0.70)
    assert engine.thresholds.gate3_floor_confidence == pytest.approx(0.80)

    # 1. Leaf task routes to FLOOR
    v_leaf = await engine.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="g3-leaf",
            workspace_root=str(tmp_path),
            prompt_text="Add helper in leaf.py",
            touched_files=["leaf.py"],
        )
    )
    assert v_leaf.target_tier == ModelTier.FLOOR

    # 2. Multi-lib code routes to FRONTIER
    v_multi = await engine.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="g3-multi",
            workspace_root=str(tmp_path),
            prompt_text="Process tensor data using numpy, pandas, and torch pipelines",
            touched_files=["pipeline.py"],
        )
    )
    assert v_multi.target_tier == ModelTier.FRONTIER

    # 3. Knowledge vault cartridge ceiling prevents routing to FLOOR
    cart_kv = load_cartridge("knowledge_vault")
    engine_kv = GateEngine(cartridge=cart_kv, calibrated_priors=loaded)
    v_kv = await engine_kv.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="g3-kv",
            workspace_root=str(tmp_path),
            prompt_text="Summarize public notes",
            touched_files=["notes.md"],
        )
    )
    assert v_kv.target_tier != ModelTier.FLOOR

    # 4. Cloud Decision1Driver blending keeps ceilings
    cloud_driver = _MockCloudDriver()
    engine_cloud_kv = GateEngine(cartridge=cart_kv, driver=cloud_driver, calibrated_priors=loaded)
    v_cloud_kv = await engine_cloud_kv.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="g3-cloud-kv",
            workspace_root=str(tmp_path),
            prompt_text="Summarize documentation",
            touched_files=["doc.md"],
        )
    )
    assert v_cloud_kv.target_tier != ModelTier.FLOOR


@pytest.mark.asyncio
async def test_calibration_d_real_priors_file_with_low_floor_cloud_driver() -> None:
    """Verify calibrated empirical priors govern routing even with low cloud driver scores."""
    priors_path = Path("benchmarks/reports/gate3_calibrated_priors.json")
    if not priors_path.is_file():
        pytest.skip("benchmarks/reports/gate3_calibrated_priors.json not present")
    priors = load_calibrated_priors(priors_path)
    driver_low = _MockCloudDriver(
        {"tier:floor": 0.10, "tier:mid": 0.20, "tier:frontier": 0.90, "inv": 0.05}
    )

    # 1. 52-word GSM8K prompt (general_reasoning) -> FLOOR
    cart_gr = load_cartridge("general_reasoning")
    eng_gr = GateEngine(cartridge=cart_gr, driver=driver_low, calibrated_priors=priors)
    gsm_prompt = (
        "Janet buys 3 packs of stickers with 12 stickers in each pack. She gives 5 stickers to her "
        "friend and loses 2 stickers on the playground. How many stickers does Janet have left after "
        "giving some to her friend and losing some at school? Please calculate the total stickers carefully."
    )
    v_gsm = await eng_gr.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="gsm",
            workspace_root=".",
            prompt_text=gsm_prompt,
            touched_files=["answer.json"],
        )
    )
    assert v_gsm.target_tier == ModelTier.FLOOR

    # 2. 100-word HumanEval+ prompt (software_engineering) -> FLOOR
    cart_swe = load_cartridge("software_engineering")
    eng_swe = GateEngine(cartridge=cart_swe, driver=driver_low, calibrated_priors=priors)
    he_prompt = (
        "Write a python function find_common_elements(list1: list[int], list2: list[int]) -> list[int] "
        "that takes two integer lists as input and returns a sorted list containing the unique common elements "
        "present in both input arrays. The implementation should be efficient, handle duplicate values "
        "gracefully by eliminating redundancies, and handle edge cases such as empty lists or negative values. "
        "Ensure that you do not import external third-party packages and rely purely on standard library builtins. "
        "Include descriptive docstrings and type hints for clarity and readability in production code."
    )
    v_he = await eng_swe.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="he",
            workspace_root=".",
            prompt_text=he_prompt,
            touched_files=["solution.py"],
        )
    )
    assert v_he.target_tier == ModelTier.FLOOR

    # 3. Leaf SWE prompt (software_engineering, blast_radius=0.048) -> FLOOR
    v_swe_leaf = await eng_swe.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="swe_leaf",
            workspace_root=".",
            prompt_text="Fix typo in helper.py",
            touched_files=["helper.py"],
            metadata={"blast_radius": 0.048},
        )
    )
    assert v_swe_leaf.target_tier == ModelTier.FLOOR

    # 4. Multi-lib prompt (numpy, itertools, software_engineering) -> FRONTIER
    v_multi = await eng_swe.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="multi",
            workspace_root=".",
            prompt_text="Process numerical matrix with numpy and itertools combinations",
            touched_files=["pipeline.py"],
        )
    )
    assert v_multi.target_tier == ModelTier.FRONTIER

    # 5. knowledge_vault, financial_ops, legal_redline -> FRONTIER
    for cname in ["knowledge_vault", "financial_ops", "legal_redline"]:
        c_inst = load_cartridge(cname)
        eng_c = GateEngine(cartridge=c_inst, driver=driver_low, calibrated_priors=priors)
        v_c = await eng_c.evaluate_gate3_subagent(
            GateEvent(
                gate=GateType.SUBAGENT_GOVERNOR,
                session_id=f"test_{cname}",
                workspace_root=".",
                prompt_text="Perform routine check",
                touched_files=["file.txt"],
            )
        )
        assert v_c.target_tier == ModelTier.FRONTIER

    # 6. High invariant violation (max_inv=0.80) on software_engineering -> FRONTIER
    driver_high_inv = _MockCloudDriver(
        {"tier:floor": 0.10, "tier:mid": 0.20, "tier:frontier": 0.90, "inv": 0.80}
    )
    eng_high_inv = GateEngine(cartridge=cart_swe, driver=driver_high_inv, calibrated_priors=priors)
    v_inv = await eng_high_inv.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="high_inv",
            workspace_root=".",
            prompt_text="Fix typo in helper.py",
            touched_files=["helper.py"],
            metadata={"blast_radius": 0.048},
        )
    )
    assert v_inv.target_tier == ModelTier.FRONTIER


@pytest.mark.asyncio
async def test_calibration_d_float_literals_not_treated_as_paths() -> None:
    """Float decimals in code/math docstrings (e.g. 1.0, 0.5, 2.8) must not inflate target_paths."""
    priors_path = Path("benchmarks/reports/gate3_calibrated_priors.json")
    if not priors_path.is_file():
        pytest.skip("benchmarks/reports/gate3_calibrated_priors.json not present")
    priors = load_calibrated_priors(priors_path)
    driver_low = _MockCloudDriver(
        {"tier:floor": 0.10, "tier:mid": 0.20, "tier:frontier": 0.90, "inv": 0.05}
    )
    cart_swe = load_cartridge("software_engineering")
    eng_swe = GateEngine(cartridge=cart_swe, driver=driver_low, calibrated_priors=priors)

    prompt = (
        "from typing import List\n"
        "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
        '    """ Check if in given list of numbers, are any two numbers closer to each other than\n'
        "    given threshold.\n"
        "    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)\n"
        "    False\n"
        "    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)\n"
        "    True\n"
        '    """\n'
    )
    ev = GateEvent(
        gate=GateType.SUBAGENT_GOVERNOR,
        session_id="he-0-floats",
        workspace_root=".",
        prompt_text=prompt,
        touched_files=["solution.py"],
    )
    targets = eng_swe._resolve_gate3_targets(ev, prompt)
    assert targets == ["solution.py"]
    v = await eng_swe.evaluate_gate3_subagent(ev)
    assert v.target_tier == ModelTier.FLOOR


# ---------------------------------------------------------------------------
# Strict Air-Gap Verification
# ---------------------------------------------------------------------------


def test_strict_airgap_zero_egress(tmp_path: Path) -> None:
    """Verify strict zero-egress air-gap when private files exist in workspace."""
    cartridge = load_cartridge("knowledge_vault")
    engine = GateEngine(cartridge=cartridge)

    clean_ws = tmp_path / "clean"
    clean_ws.mkdir(parents=True, exist_ok=True)
    priv_ws = tmp_path / "priv"
    priv_ws.mkdir(parents=True, exist_ok=True)

    # Workspace without private files allows cloud driver if text is clean
    assert engine._check_air_gap(
        text="Normal public text",
        workspace_root=str(clean_ws),
    ) is True

    # Add private tier-1 vault file
    priv_file = priv_ws / "vault" / "private" / "patient_record.md"
    priv_file.parent.mkdir(parents=True, exist_ok=True)
    priv_file.write_text("# Confidential HIPAA Note\nSSN: 000-11-2222\n", encoding="utf-8")

    # Air-gap strictly blocks cloud egress
    assert engine._check_air_gap(
        text="Query referencing notes",
        workspace_root=str(priv_ws),
    ) is False
    assert engine._select_active_driver(cloud_egress_allowed=False) is engine._airgap_driver
