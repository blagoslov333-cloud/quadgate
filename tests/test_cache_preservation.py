"""Tests for 4-Gate engine, ECC governor, and 0% KV prefix-cache mutation on turns 2..N."""

from __future__ import annotations

from pathlib import Path

import pytest

from quadgate.contracts import (
    CartridgeSpec,
    ClassifierRequest,
    ContextUnitSpec,
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    InvariantSpec,
    ModelTier,
    TruthOracleSpec,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.drivers.base import ClassifierDriver, DriverCapabilities
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.cochange import CoChangeGraph
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.gates import GateEngine, load_cartridge
from quadgate.kernel.healing import SelfHealingEngine

REPO_ROOT = Path(__file__).resolve().parent.parent


class _ScriptedDriver(ClassifierDriver):
    """Deterministic in-memory driver for testing gate scoring and cloud air-gaps."""

    def __init__(
        self,
        *,
        is_cloud: bool = False,
        max_candidates: int = 256,
        score_map: dict[str, float] | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self._is_cloud = is_cloud
        self._max_candidates = max_candidates
        self._score_map = score_map or {}
        self._raise_on_call = raise_on_call
        self.requests: list[ClassifierRequest] = []
        super().__init__()

    @property
    def driver_id(self) -> str:
        return "scripted_cloud" if self._is_cloud else "scripted_local"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=self._is_cloud,
            supports_parallel_heads=True,
            max_candidates_per_pass=self._max_candidates,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        self.requests.append(request)
        if self._raise_on_call is not None:
            raise self._raise_on_call
        out: dict[str, float] = {}
        for head in request.heads:
            if head.head_id in self._score_map:
                out[head.head_id] = self._score_map[head.head_id]
            else:
                rel_path = str(head.metadata.get("rel_path", ""))
                if rel_path in self._score_map:
                    out[head.head_id] = self._score_map[rel_path]
                elif head.head_id == "pragmatic:directive":
                    out[head.head_id] = 0.95
                elif head.head_id == "pragmatic:counterfactual":
                    out[head.head_id] = 0.05
                else:
                    out[head.head_id] = 0.10
        return out

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class _MockHealing:
    """Duck-typed self-healing stub matching SelfHealingEngine contracts."""

    def __init__(
        self,
        *,
        associated: dict[str, float] | None = None,
        failure_rates: dict[str, float] | None = None,
    ) -> None:
        self.associated = associated or {}
        self.failure_rates = failure_rates or {}
        self.turn1_recorded: list[tuple[str, str, list[str]]] = []
        self.observed_events: list[GateEvent] = []
        self.grey_zone_logs: list[tuple[str, float, str, str]] = []
        self.gate4_failures: list[tuple[str, str]] = []
        self.file_outcomes: list[tuple[list[str], bool]] = []

    def record_turn1_hints(
        self, session_id: str, workspace_root: str, hinted_paths: list[str]
    ) -> None:
        self.turn1_recorded.append((session_id, workspace_root, list(hinted_paths)))

    def get_associated_paths(
        self, source_paths: list[str], *, workspace_root: str = ""
    ) -> dict[str, float]:
        return dict(self.associated)

    async def observe_tool_event(self, event: GateEvent) -> list[tuple[str, str]]:
        self.observed_events.append(event)
        return []

    async def log_grey_zone_decision(
        self,
        event: GateEvent,
        score: float,
        head_id: str,
        *,
        verdict_action: str = "allow",
    ) -> bool:
        self.grey_zone_logs.append((event.session_id, score, head_id, verdict_action))
        return True

    def get_failure_rate(self, rel_path: str, *, workspace_root: str = "") -> float:
        return float(self.failure_rates.get(rel_path, 0.0))

    async def handle_gate4_failure(
        self, event: GateEvent, oracle_error: str
    ) -> GateVerdict:
        self.gate4_failures.append((event.session_id, oracle_error))
        if event.strike_count == 0 and event.current_tier in {ModelTier.FLOOR, ModelTier.MID}:
            next_tier = (
                ModelTier.MID if event.current_tier == ModelTier.FLOOR else ModelTier.FRONTIER
            )
            next_model = "flash" if next_tier == ModelTier.MID else "pro"
            return GateVerdict(
                action=VerdictAction.PROMOTE,
                gate=GateType.STOP_AUDIT,
                target_tier=next_tier,
                updated_model=next_model,
                reason=f"Mock healing promoted: {oracle_error}",
            )
        return GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.STOP_AUDIT,
            violation_receipt=ViolationReceipt(
                rule_id="GATE4_ONE_STRIKE_CIRCUIT_BREAKER",
                score=1.0,
                stage="deterministic_2a",
                remediation=oracle_error,
                strike_count=max(2, event.strike_count + 1),
                escalate_to_human=True,
            ),
            reason=oracle_error,
        )

    def record_file_outcome(
        self, rel_paths: list[str], *, passed: bool, workspace_root: str = ""
    ) -> dict[str, float]:
        self.file_outcomes.append((list(rel_paths), passed))
        return {}


def test_load_cartridge_all_four_and_errors() -> None:
    for name in (
        "software_engineering",
        "knowledge_vault",
        "financial_ops",
        "legal_redline",
    ):
        spec = load_cartridge(name)
        assert isinstance(spec, CartridgeSpec)
        assert spec.name == name

    yaml_path = REPO_ROOT / "quadgate" / "cartridges" / "software_engineering.yaml"
    spec_from_path = load_cartridge(yaml_path)
    assert spec_from_path.name == "software_engineering"

    with pytest.raises(FileNotFoundError):
        load_cartridge("nonexistent_cartridge_xyz")


async def test_turn_gt_1_preserves_prefix_untouched(tmp_path: Path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "jwt_validator.py").write_text(
        '"""Validate JWT login tokens and expiration leeway."""\n'
        "def verify_token(token: str, leeway_sec: int = 30) -> bool:\n"
        "    return bool(token) and leeway_sec >= 0\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "stripe_webhook.py").write_text(
        '"""Handle Stripe billing invoice webhooks."""\n'
        "def handle_invoice_paid(payload: dict) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    spec = load_cartridge("software_engineering")
    indexer = AstGistIndexer(tmp_path, cartridge=spec, use_tree_sitter=False)
    gists = indexer.index_workspace()
    fts = SqliteFtsStore(":memory:")
    fts.upsert_gists(gists)

    spy_driver = _ScriptedDriver(
        is_cloud=False,
        score_map={"src/auth/jwt_validator.py": 0.91, "src/billing/stripe_webhook.py": 0.12},
    )
    engine = GateEngine(cartridge=spec, driver=spy_driver, fts_store=fts, indexer=indexer)

    system_prompt = "STATIC_SYSTEM_BLUEPRINT_v1: Never leak credentials or break tests."
    turn1_user = "Update src/auth/jwt_validator.py to enforce token expiration leeway."

    v1 = await engine.evaluate(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-cache-1",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text=turn1_user,
        )
    )
    assert v1.action == VerdictAction.AUGMENT
    assert "src/auth/jwt_validator.py" in v1.tail_hints
    assert len(spy_driver.requests) == 1

    # Tail hint injection appends ONLY to the Turn-1 user message; system prompt is 100% immutable.
    tail_block = "\n[QUADGATE_CONTEXT_HINTS]\n- " + v1.tail_hints[0]
    conversation_prefix = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": turn1_user + tail_block},
        {"role": "assistant", "content": "Inspecting jwt_validator.py now."},
    ]
    baseline_prefix_bytes = repr(conversation_prefix).encode("utf-8")
    baseline_system_bytes = system_prompt.encode("utf-8")

    # Turns 2 through 20 MUST return ALLOW with zero tail_hints, zero reason, and 0 driver calls.
    for turn in range(2, 21):
        verdict = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="sess-cache-1",
                turn_index=turn,
                workspace_root=str(tmp_path),
                prompt_text=f"Continue refactoring step {turn}",
            )
        )
        assert verdict.action == VerdictAction.ALLOW
        assert verdict.tail_hints == []
        assert verdict.reason is None
        assert repr(conversation_prefix).encode("utf-8") == baseline_prefix_bytes
        assert system_prompt.encode("utf-8") == baseline_system_bytes

    assert len(spy_driver.requests) == 1

    # Explicit /compact reset re-arms Gate 1 cleanly.
    v_compact = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-cache-1",
            turn_index=21,
            workspace_root=str(tmp_path),
            prompt_text="Update src/auth/jwt_validator.py after compact",
            metadata={"compact_reset": True},
        )
    )
    assert v_compact.action == VerdictAction.AUGMENT
    assert "src/auth/jwt_validator.py" in v_compact.tail_hints
    assert len(spy_driver.requests) == 2
    fts.close()


async def test_gate1_brainstorm_cochange_and_associative_boost(tmp_path: Path) -> None:
    spec = load_cartridge("software_engineering")

    # 1. Brainstorm advisory via default ZeroDepDriver
    engine_zd = GateEngine(cartridge=spec)
    v_brainstorm = await engine_zd.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-brainstorm",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="What if we eventually migrated our database to Rust next year?",
        )
    )
    assert v_brainstorm.action == VerdictAction.AUGMENT
    assert v_brainstorm.reason == (
        "[QUADGATE_MODE: Read-only brainstorm detected; confirm before mutating files]"
    )

    # 2. Co-change boost (w > 0.40) + associative miss boost + hint recording
    cochange = CoChangeGraph(tmp_path)
    # 5 commits touching jwt.py; 4 also touch session.py (w=0.80); 1 touches billing.py (w=0.20)
    raw_log = "\n".join(
        [
            "__QG_COMMIT__",
            "src/auth/jwt.py",
            "src/auth/session.py",
            "__QG_COMMIT__",
            "src/auth/jwt.py",
            "src/auth/session.py",
            "__QG_COMMIT__",
            "src/auth/jwt.py",
            "src/auth/session.py",
            "__QG_COMMIT__",
            "src/auth/jwt.py",
            "src/auth/session.py",
            "__QG_COMMIT__",
            "src/auth/jwt.py",
            "src/billing.py",
        ]
    )
    cochange.build_from_log_text(raw_log)
    assert cochange.weight("src/auth/jwt.py", "src/auth/session.py") == pytest.approx(0.80)
    assert cochange.weight("src/auth/jwt.py", "src/billing.py") == pytest.approx(0.20)

    driver = _ScriptedDriver(
        score_map={
            "src/auth/jwt.py": 0.90,
            "src/auth/session.py": 0.72,  # 0.72 + 0.12*0.80 = 0.816 >= 0.80
            "src/billing.py": 0.72,       # w=0.20 <= 0.40 -> stays 0.72 < 0.80
        }
    )
    healing = _MockHealing(associated={"src/config/keys.py": 0.60})
    engine = GateEngine(cartridge=spec, driver=driver, cochange=cochange, healing=healing)

    verdict = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-boost",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="Fix JWT validation logic",
            metadata={
                "candidates": [
                    "src/auth/jwt.py",
                    "src/auth/session.py",
                    "src/billing.py",
                ]
            },
        )
    )
    assert verdict.action == VerdictAction.AUGMENT
    assert verdict.reason is None
    assert "src/auth/jwt.py" in verdict.tail_hints
    assert "src/auth/session.py" in verdict.tail_hints
    assert "src/config/keys.py" in verdict.tail_hints
    assert "src/billing.py" not in verdict.tail_hints
    assert len(healing.turn1_recorded) == 1
    assert healing.turn1_recorded[0][2] == verdict.tail_hints


async def test_gate1_airgap_blocks_cloud_driver_and_uses_local_zero_dep(tmp_path: Path) -> None:
    kv_spec = load_cartridge("knowledge_vault")
    cloud_driver = _ScriptedDriver(
        is_cloud=True,
        raise_on_call=AssertionError("Cloud driver must never run when air-gapped!"),
    )
    engine = GateEngine(cartridge=kv_spec, driver=cloud_driver)

    # Trigger via [private] tag
    v_tag = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-airgap-1",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="[private] Summarize my tax memo",
            metadata={"candidates": ["notes/tax_memo.md"]},
        )
    )
    assert v_tag.cloud_egress_allowed is False
    assert len(cloud_driver.requests) == 0

    # Trigger via cartridge private_tier1_globs (`**/vault/private/**` & `**/*[private]*`)
    v_glob = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-airgap-2",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="Check vault/private/health_records.md",
            metadata={"candidates": ["vault/private/health_records.md"]},
        )
    )
    assert v_glob.cloud_egress_allowed is False
    assert len(cloud_driver.requests) == 0

    # Normal public note in knowledge_vault MUST allow cloud driver (no false match on [private])
    public_cloud = _ScriptedDriver(
        is_cloud=True,
        score_map={"notes/architecture.md": 0.90},
    )
    engine_pub = GateEngine(cartridge=kv_spec, driver=public_cloud)
    v_pub = await engine_pub.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="sess-pub",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="Summarize notes/architecture.md",
            metadata={"candidates": ["notes/architecture.md"]},
        )
    )
    assert v_pub.cloud_egress_allowed is True
    assert len(public_cloud.requests) == 1


async def test_gate2_ast_and_markdown_enclosing_context_extraction(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    py_file = tmp_path / "src" / "auth.py"
    py_file.write_text(
        "def helper_before():\n"
        "    return 'before'\n\n"
        "class AuthService:\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        secret_marker = 'enclosing_ok'\n"
        "        return bool(token)\n\n"
        "def helper_after():\n"
        "    return 'after'\n",
        encoding="utf-8",
    )

    md_file = tmp_path / "playbook.md"
    md_file.write_text(
        "# Security Playbook\n\n"
        "## Auth Rules\n"
        "Always validate JWT signatures.\n\n"
        "## Billing Rules\n"
        "Never skip invoice validation.\n",
        encoding="utf-8",
    )

    driver = _ScriptedDriver(score_map={})
    engine = GateEngine(cartridge=load_cartridge("software_engineering"), driver=driver)

    v_py = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-ast",
            workspace_root=str(tmp_path),
            tool_name="replace_file_content",
            tool_args={
                "TargetFile": "src/auth.py",
                "TargetContent": "        return bool(token)",
                "ReplacementContent": "        return len(token) > 10",
            },
        )
    )
    assert v_py.action == VerdictAction.ALLOW
    py_query = driver.requests[-1].query_text
    assert "class AuthService:" in py_query
    assert "def verify_token" in py_query
    assert "secret_marker" in py_query
    assert "helper_after" not in py_query

    v_md = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-ast",
            workspace_root=str(tmp_path),
            tool_name="replace_file_content",
            tool_args={
                "TargetFile": "playbook.md",
                "TargetContent": "Always validate JWT signatures.",
                "ReplacementContent": "Always validate JWT signatures and audience.",
            },
        )
    )
    assert v_md.action == VerdictAction.ALLOW
    md_query = driver.requests[-1].query_text
    assert "## Auth Rules" in md_query
    assert "Billing Rules" not in md_query


async def test_gate2_two_strike_escalation_bypass_and_cage(tmp_path: Path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "billing").mkdir(parents=True)

    # 1. Stage-2A Cage containment + [bypass-gates] cannot bypass Stage-2A + read-only allowed
    engine_2a = GateEngine(cartridge=load_cartridge("software_engineering"))
    v_out = await engine_2a.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-cage",
            workspace_root=str(tmp_path),
            tool_name="Edit",
            tool_args={"file_path": "src/billing/invoice.py", "new_string": "x = 1"},
            assigned_file_cage=["src/auth/*.py"],
            bypass_gates=True,  # Must NOT bypass Stage-2A!
        )
    )
    assert v_out.action == VerdictAction.BLOCK
    assert v_out.violation_receipt is not None
    assert v_out.violation_receipt.rule_id == "INV_02_CAGE_CONTAINMENT"
    assert v_out.violation_receipt.stage == "deterministic_2a"

    # Read-only inspection outside write cage is allowed (except .env / path escapes)
    v_read = await engine_2a.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-cage",
            workspace_root=str(tmp_path),
            tool_name="view_file",
            tool_args={"AbsolutePath": str(tmp_path / "src" / "billing" / "invoice.py")},
            assigned_file_cage=["src/auth/*.py"],
        )
    )
    assert v_read.action == VerdictAction.ALLOW

    # Stage-2A .env read is still blocked even with view_file and bypass_gates=True
    v_env = await engine_2a.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-cage",
            workspace_root=str(tmp_path),
            tool_name="view_file",
            tool_args={"AbsolutePath": str(tmp_path / ".env")},
            bypass_gates=True,
        )
    )
    assert v_env.action == VerdictAction.BLOCK
    assert v_env.violation_receipt is not None
    assert v_env.violation_receipt.rule_id == "INV_01_SECRET_EXPOSURE"

    # Write inside assigned cage succeeds
    v_in = await engine_2a.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-cage",
            workspace_root=str(tmp_path),
            tool_name="Edit",
            tool_args={"file_path": "src/auth/jwt.py", "new_string": "def ok(): return True"},
            assigned_file_cage=["src/auth/*.py"],
        )
    )
    assert v_in.action == VerdictAction.ALLOW

    # 2. Stage-2B grey-zone logging, 2-strike escalation, [bypass-gates], and scope_globs
    custom_cartridge = CartridgeSpec(
        name="custom_scoped",
        context_unit=ContextUnitSpec(indexer="tree_sitter_ast", include_globs=["**/*.py"]),
        mutation_tools=["Write", "Edit"],
        invariants=[
            InvariantSpec(
                id="INV_PY_RULE",
                gist="Python architectural boundary invariant",
                remediation="Use service interface instead of direct import.",
                scope_globs=["**/*.py"],
            ),
            InvariantSpec(
                id="INV_SQL_ONLY",
                gist="SQL schema migration rule",
                remediation="Add rollback clause.",
                scope_globs=["**/*.sql"],
            ),
        ],
        truth_oracle=TruthOracleSpec(),
    )
    healing = _MockHealing()

    # Grey-zone allow (0.72 in [0.60, 0.84], < 0.78)
    grey_driver = _ScriptedDriver(score_map={"INV_PY_RULE": 0.72, "INV_SQL_ONLY": 0.99})
    engine_grey = GateEngine(cartridge=custom_cartridge, driver=grey_driver, healing=healing)
    v_grey = await engine_grey.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-grey",
            workspace_root=str(tmp_path),
            tool_name="Write",
            tool_args={"file_path": "src/auth/app.py", "content": "x = 1"},
        )
    )
    assert v_grey.action == VerdictAction.ALLOW
    # INV_SQL_ONLY was scoped out so never triggered!
    assert len(healing.grey_zone_logs) == 1
    assert healing.grey_zone_logs[0] == ("sess-grey", 0.72, "INV_PY_RULE", "allow")

    # 2-strike anti-loop escalation (score 0.82 >= 0.78)
    block_driver = _ScriptedDriver(score_map={"INV_PY_RULE": 0.82})
    engine_2b = GateEngine(cartridge=custom_cartridge, driver=block_driver, healing=healing)
    bad_ev = GateEvent(
        gate=GateType.FIREWALL,
        session_id="sess-2strike",
        workspace_root=str(tmp_path),
        tool_name="Write",
        tool_args={"file_path": "src/auth/app.py", "content": "direct_import = True"},
    )
    strike1 = await engine_2b.evaluate_gate2_firewall(bad_ev)
    assert strike1.action == VerdictAction.BLOCK
    assert strike1.violation_receipt is not None
    assert strike1.violation_receipt.stage == "semantic_2b"
    assert strike1.violation_receipt.strike_count == 1
    assert strike1.violation_receipt.escalate_to_human is False

    strike2 = await engine_2b.evaluate_gate2_firewall(bad_ev)
    assert strike2.action == VerdictAction.BLOCK
    assert strike2.violation_receipt is not None
    assert strike2.violation_receipt.strike_count == 2
    assert strike2.violation_receipt.escalate_to_human is True
    assert "HUMAN CONFIRMATION REQUIRED" in strike2.violation_receipt.remediation

    # [bypass-gates] bypasses Stage-2B cleanly
    bypass_ev = bad_ev.model_copy(
        update={"prompt_text": "Intentional legacy migration [bypass-gates]"}
    )
    v_bypass = await engine_2b.evaluate_gate2_firewall(bypass_ev)
    assert v_bypass.action == VerdictAction.ALLOW


async def test_gate3_ecc_equation_and_blast_radius_routing(tmp_path: Path) -> None:
    # Build multi-file repo where core.py has 4 importers (B = 1.0) and leaf.py has 0 (B = 0.0)
    (tmp_path / "core.py").write_text("def core_fn():\n    return 1\n", encoding="utf-8")
    for idx in range(4):
        (tmp_path / f"mod_{idx}.py").write_text(
            "from core import core_fn\n", encoding="utf-8"
        )
    (tmp_path / "leaf.py").write_text("def leaf_fn():\n    return 2\n", encoding="utf-8")

    spec = load_cartridge("software_engineering")
    indexer = AstGistIndexer(tmp_path, cartridge=spec, use_tree_sitter=False)
    indexer.index_workspace()

    # Check direct compute_ecc formula properties
    healing = _MockHealing(failure_rates={"flaky.py": 0.65})
    engine = GateEngine(cartridge=spec, indexer=indexer, healing=healing)
    ecc_low = engine.compute_ecc(ModelTier.FLOOR, p_pass=0.90, blast_radius=0.0, r_hist=0.0)
    ecc_high_b = engine.compute_ecc(ModelTier.FLOOR, p_pass=0.90, blast_radius=0.8, r_hist=0.0)
    ecc_frontier = engine.compute_ecc(ModelTier.FRONTIER, p_pass=0.98, blast_radius=0.0, r_hist=0.0)
    assert ecc_low == pytest.approx(0.20)
    assert ecc_high_b > ecc_low
    assert ecc_low < 0.75 * ecc_frontier

    # 1. Simple leaf task (B=0.0, R_hist=0.0) -> routes to FLOOR ("flash_lite")
    v_floor = await engine.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="sess-g3-1",
            workspace_root=str(tmp_path),
            harness=HarnessType.ANTIGRAVITY,
            tool_name="invoke_subagent",
            prompt_text="Add a unit test helper in leaf.py",
            touched_files=["leaf.py"],
        )
    )
    assert v_floor.action == VerdictAction.MODIFY
    assert v_floor.target_tier == ModelTier.FLOOR
    assert v_floor.updated_model == "flash_lite"
    assert v_floor.updated_tool_args is not None
    assert v_floor.updated_tool_args["Model"] == "flash_lite"

    # 2. High blast-radius task (core.py imported by 4/5 other files -> B=0.80) -> FRONTIER ("pro")
    v_frontier_b = await engine.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="sess-g3-2",
            workspace_root=str(tmp_path),
            harness=HarnessType.CLAUDE_CODE,
            tool_name="Task",
            prompt_text="Update core_fn return signature in core.py",
            touched_files=["core.py"],
        )
    )
    assert v_frontier_b.action == VerdictAction.MODIFY
    assert v_frontier_b.target_tier == ModelTier.FRONTIER
    assert v_frontier_b.updated_model == "pro"
    assert v_frontier_b.updated_tool_args is not None
    assert v_frontier_b.updated_tool_args["model"] == "pro"

    # 3. High historical failure rate R_hist=0.65 -> P_eff <= 0.35 -> FRONTIER ("pro")
    (tmp_path / "flaky.py").write_text("def shaky():\n    return 0\n", encoding="utf-8")
    indexer.index_workspace()
    v_frontier_r = await engine.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="sess-g3-3",
            workspace_root=str(tmp_path),
            tool_name="Task",
            prompt_text="Update helper in flaky.py",
            touched_files=["flaky.py"],
        )
    )
    assert v_frontier_r.action == VerdictAction.MODIFY
    assert v_frontier_r.target_tier == ModelTier.FRONTIER
    assert v_frontier_r.updated_model == "pro"

    # 4. Invariant risk max(I_m) >= 0.50 -> blocks downgrade and picks FRONTIER
    risky_driver = _ScriptedDriver(
        score_map={
            "tier:floor": 0.95,
            "tier:mid": 0.96,
            "tier:frontier": 0.99,
            "inv:INV_01_SECRET_EXPOSURE": 0.65,
        }
    )
    engine_risk = GateEngine(cartridge=spec, driver=risky_driver)
    v_risk = await engine_risk.evaluate_gate3_subagent(
        GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id="sess-g3-4",
            workspace_root=str(tmp_path),
            prompt_text="Update config",
        )
    )
    assert v_risk.target_tier == ModelTier.FRONTIER


async def test_gate4_truth_oracles_all_4_cartridges_and_completeness(tmp_path: Path) -> None:
    # 1. software_engineering: py_compile pass vs fail + healing delegation
    se_dir = tmp_path / "se"
    se_dir.mkdir()
    good_py = se_dir / "ok.py"
    good_py.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    bad_py = se_dir / "broken.py"
    bad_py.write_text("def broken(:\n    pass\n", encoding="utf-8")

    healing = _MockHealing()
    se_engine = GateEngine(cartridge=load_cartridge("software_engineering"), healing=healing)

    v_se_ok = await se_engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="se-ok",
            workspace_root=str(se_dir),
            touched_files=["ok.py"],
            prompt_text="Add add function in ok.py",
        )
    )
    assert v_se_ok.action == VerdictAction.ALLOW

    v_se_promote = await se_engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="se-bad-0",
            workspace_root=str(se_dir),
            touched_files=["broken.py"],
            current_tier=ModelTier.FLOOR,
            strike_count=0,
        )
    )
    assert v_se_promote.action == VerdictAction.PROMOTE
    assert v_se_promote.target_tier == ModelTier.MID
    assert v_se_promote.updated_model == "flash"
    assert len(healing.gate4_failures) == 1

    # Built-in strike fallback without healing attached
    se_no_heal = GateEngine(cartridge=load_cartridge("software_engineering"))
    v_se_block = await se_no_heal.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="se-bad-1",
            workspace_root=str(se_dir),
            touched_files=["broken.py"],
            current_tier=ModelTier.MID,
            strike_count=1,
        )
    )
    assert v_se_block.action == VerdictAction.BLOCK
    assert v_se_block.violation_receipt is not None
    assert v_se_block.violation_receipt.escalate_to_human is True

    # 2. knowledge_vault: verify_wikilinks_exist + verify_citations_present
    kv_dir = tmp_path / "kv"
    kv_dir.mkdir()
    (kv_dir / "source_note.md").write_text(
        "# Source\nFact ([RFC](https://example.com)).\n",
        encoding="utf-8",
    )
    (kv_dir / "good_summary.md").write_text(
        "# Summary\nToken validation uses leeway from [[source_note]].\n",
        encoding="utf-8",
    )
    (kv_dir / "broken_link.md").write_text(
        "# Broken\nSee [[nonexistent_note]] for details.\n",
        encoding="utf-8",
    )
    (kv_dir / "no_cite.md").write_text(
        "# Unsupported\nThis claim asserts a critical production metric without any source link.\n",
        encoding="utf-8",
    )

    kv_engine = GateEngine(cartridge=load_cartridge("knowledge_vault"))
    assert (
        await kv_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="kv-1",
                workspace_root=str(kv_dir),
                touched_files=["good_summary.md"],
            )
        )
    ).action == VerdictAction.ALLOW
    assert (
        await kv_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="kv-2",
                workspace_root=str(kv_dir),
                touched_files=["broken_link.md"],
                current_tier=ModelTier.FLOOR,
                strike_count=0,
            )
        )
    ).action == VerdictAction.PROMOTE
    assert (
        await kv_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="kv-3",
                workspace_root=str(kv_dir),
                touched_files=["no_cite.md"],
            )
        )
    ).action == VerdictAction.BLOCK

    # 3. financial_ops: verify_double_entry_sum + verify_currency_precision
    fin_dir = tmp_path / "fin"
    fin_dir.mkdir()
    (fin_dir / "balanced.csv").write_text(
        "account,debit,credit,currency\n"
        "Cash,150.25,0.00,USD\n"
        "Revenue,0.00,150.25,USD\n",
        encoding="utf-8",
    )
    (fin_dir / "unbalanced.csv").write_text(
        "account,debit,credit,currency\n"
        "Cash,150.25,0.00,USD\n"
        "Revenue,0.00,140.00,USD\n",
        encoding="utf-8",
    )
    (fin_dir / "bad_precision.csv").write_text(
        "account,debit,credit,currency\n"
        "Cash,10.125,0.00,USD\n"
        "Revenue,0.00,10.125,USD\n",
        encoding="utf-8",
    )

    fin_engine = GateEngine(cartridge=load_cartridge("financial_ops"))
    assert (
        await fin_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="fin-1",
                workspace_root=str(fin_dir),
                touched_files=["balanced.csv"],
            )
        )
    ).action == VerdictAction.ALLOW
    assert (
        await fin_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="fin-2",
                workspace_root=str(fin_dir),
                touched_files=["unbalanced.csv"],
            )
        )
    ).action == VerdictAction.BLOCK
    assert (
        await fin_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="fin-3",
                workspace_root=str(fin_dir),
                touched_files=["bad_precision.csv"],
            )
        )
    ).action == VerdictAction.BLOCK

    # 4. legal_redline: verify_required_clauses_present + verify_cross_references
    leg_dir = tmp_path / "legal"
    leg_dir.mkdir()
    (leg_dir / "valid_msa.md").write_text(
        "# Master Services Agreement\n"
        "## Section 1. Confidentiality\n"
        "Confidential information shall be protected.\n"
        "## Section 2. Limitation of Liability\n"
        "Liability is capped at fees paid under Section 1.\n"
        "## Section 3. Termination\n"
        "Either party may terminate for cause.\n"
        "## Section 4. Governing Law\n"
        "Governed by the laws of Delaware.\n",
        encoding="utf-8",
    )
    (leg_dir / "missing_clause.md").write_text(
        "# NDA\n"
        "## Section 1. Confidentiality\n"
        "Keep secrets confidential.\n"
        "## Section 2. Termination\n"
        "Ends in 1 year.\n",
        encoding="utf-8",
    )
    (leg_dir / "broken_xref.md").write_text(
        "# Master Services Agreement\n"
        "## Section 1. Confidentiality\nProtected.\n"
        "## Section 2. Limitation of Liability\nSee Section 9 for exceptions.\n"
        "## Section 3. Termination\nMay terminate.\n"
        "## Section 4. Governing Law\nDelaware law applies.\n",
        encoding="utf-8",
    )

    leg_engine = GateEngine(cartridge=load_cartridge("legal_redline"))
    assert (
        await leg_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="leg-1",
                workspace_root=str(leg_dir),
                touched_files=["valid_msa.md"],
            )
        )
    ).action == VerdictAction.ALLOW
    assert (
        await leg_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="leg-2",
                workspace_root=str(leg_dir),
                touched_files=["missing_clause.md"],
            )
        )
    ).action == VerdictAction.BLOCK
    assert (
        await leg_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="leg-3",
                workspace_root=str(leg_dir),
                touched_files=["broken_xref.md"],
            )
        )
    ).action == VerdictAction.BLOCK

    # 5. Directive completeness & unaddressed_clauses (AUGMENT -> continuation escalation)
    prompt_3_clauses = (
        "1. Update verify_token in auth.py\n"
        "2. Add enforce_rate_limit in gateway.py\n"
        "3. Write changelog notes in CHANGELOG.md"
    )
    partial_diff = (
        "--- a/auth.py\n+++ b/auth.py\n"
        "def verify_token(token: str) -> bool:\n    return bool(token)\n"
        "--- a/gateway.py\n+++ b/gateway.py\n"
        "def enforce_rate_limit(ip: str) -> bool:\n    return True\n"
    )
    full_diff = partial_diff + (
        "\n--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n"
        "- Updated verify_token and rate limit changelog notes.\n"
    )

    v_complete = await se_no_heal.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="comp-1",
            workspace_root=str(se_dir),
            prompt_text=prompt_3_clauses,
            diff_text=full_diff,
        )
    )
    assert v_complete.action == VerdictAction.ALLOW

    v_partial = await se_no_heal.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="comp-2",
            workspace_root=str(se_dir),
            prompt_text=prompt_3_clauses,
            diff_text=partial_diff,
            continuation_active=False,
        )
    )
    assert v_partial.action == VerdictAction.AUGMENT
    assert len(v_partial.unaddressed_clauses) == 1
    assert "CHANGELOG.md" in v_partial.unaddressed_clauses[0]

    # When continuation_active=True, do NOT emit AUGMENT again
    v_cont = await se_no_heal.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="comp-2",
            workspace_root=str(se_dir),
            prompt_text=prompt_3_clauses,
            diff_text=partial_diff,
            continuation_active=True,
            current_tier=ModelTier.FLOOR,
            strike_count=0,
        )
    )
    assert v_cont.action == VerdictAction.PROMOTE
    assert v_cont.target_tier == ModelTier.MID


async def test_gate_engine_with_real_self_healing_engine(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    a_file = tmp_path / "src" / "auth.py"
    c_file = tmp_path / "src" / "token_store.py"
    a_file.write_text("def verify():\n    return True\n", encoding="utf-8")
    c_file.write_text("TOKEN_TTL = 60\n", encoding="utf-8")

    spec = load_cartridge("software_engineering")
    healing = SelfHealingEngine(db_path=tmp_path / "healing.db", workspace_root=tmp_path)
    driver = _ScriptedDriver(score_map={"src/auth.py": 0.90, "src/token_store.py": 0.10})
    engine = GateEngine(cartridge=spec, driver=driver, healing=healing)

    # Turn 1 hints src/auth.py in session 1
    v1 = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="heal-s1",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="Update src/auth.py",
            metadata={"candidates": ["src/auth.py", "src/token_store.py"]},
        )
    )
    assert v1.tail_hints == ["src/auth.py"]

    # Agent reads then edits unhinted src/token_store.py via Gate 2
    await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="heal-s1",
            workspace_root=str(tmp_path),
            tool_name="view_file",
            tool_args={"AbsolutePath": str(c_file)},
        )
    )
    await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="heal-s1",
            workspace_root=str(tmp_path),
            tool_name="Edit",
            tool_args={"file_path": "src/token_store.py", "new_string": "TOKEN_TTL = 120"},
        )
    )

    # Next session automatically surfaces src/token_store.py via associative boost
    v2 = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="heal-s2",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text="Update src/auth.py",
            metadata={"candidates": ["src/auth.py", "src/token_store.py"]},
        )
    )
    assert "src/auth.py" in v2.tail_hints
    assert "src/token_store.py" in v2.tail_hints
    healing.close()
