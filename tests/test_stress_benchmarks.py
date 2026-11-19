"""Unit tests for QuadGate stress-benchmark execution and kernel diagnostic checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
from typing import Any

import pytest

from benchmarks.runner import (
    BenchmarkRunner,
    _SpyCloudDriver,
    _exercise_quadgate_pipeline,
    _is_coding_task,
    _run_stress_checks,
    verify_knowledge_ground_truth,
)
from quadgate.contracts import (
    ClassifierHead,
    ClassifierRequest,
    ClassifierResponse,
    GateEvent,
    GateType,
    HarnessType,
    HeadCategory,
    ModelTier,
    VerdictAction,
)
from quadgate.drivers.base import ClassifierDriver, DriverCapabilities
from quadgate.drivers.decision1 import Decision1Driver
from quadgate.drivers.onnx_local import LocalOnnxDriver, ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.sqlite_fts import SqliteFtsStore, check_text_air_gap
from quadgate.kernel.gates import (
    GateEngine,
    TierModelConfig,
    _score_zero_dep_clause_coverage,
    load_cartridge,
)
from quadgate.kernel.healing import SelfHealingEngine
from quadgate.shim import check_stage2a_deterministic_security

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUITES_DIR = _REPO_ROOT / "benchmarks" / "suites"


class _DummyCloudDriver(ClassifierDriver):
    """Minimal cloud-flagged test driver without network I/O."""

    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self._scores = scores or {}
        super().__init__(calibration_profile=None)

    @property
    def driver_id(self) -> str:
        return "dummy_cloud"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=True,
            max_candidates_per_pass=64,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        out: dict[str, float] = {}
        for h in request.heads:
            if h.head_id in self._scores:
                out[h.head_id] = self._scores[h.head_id]
            elif h.head_id == "pragmatic:directive":
                out[h.head_id] = 0.92
            elif h.head_id == "pragmatic:counterfactual":
                out[h.head_id] = 0.05
            else:
                out[h.head_id] = 0.85
        return out

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_is_coding_task_distinguishes_stress_task_kind() -> None:
    assert _is_coding_task({"suite": "coding"}) is True
    assert _is_coding_task({"suite": "knowledge"}) is False
    assert _is_coding_task({"suite": "stress_long_context", "task_kind": "coding"}) is True
    assert _is_coding_task({"suite": "stress_long_context", "task_kind": "knowledge"}) is False


@pytest.mark.asyncio
async def test_spy_cloud_driver_counts_and_blocks_disallowed_egress() -> None:
    spy = _SpyCloudDriver(_DummyCloudDriver())
    req = ClassifierRequest(
        query_text="hello",
        heads=[
            ClassifierHead(head_id="h1", candidate_text="test", category=HeadCategory.CANDIDATE)
        ],
        cloud_egress_allowed=False,
    )
    with pytest.raises(PermissionError):
        await spy.classify(req)
    assert spy.cloud_calls == 0

    req_ok = req.model_copy(update={"cloud_egress_allowed": True})
    resp = await spy.classify(req_ok)
    assert spy.cloud_calls == 1
    assert resp.calibrated_probabilities["h1"] >= 0.5


@pytest.mark.asyncio
async def test_runner_stress_checks_pass_and_catch_failures(tmp_path: Path) -> None:
    suites_dir = tmp_path / "suites"
    reports_dir = tmp_path / "reports"
    suites_dir.mkdir(parents=True)

    task_base: dict[str, Any] = {
        "task_id": "STRESS_UNIT_01",
        "suite": "stress_adversarial_resilience",
        "task_kind": "coding",
        "category": "security_evasion_probe",
        "cartridge": "software_engineering",
        "prompt": "Fix pkg_u/mod.py add function.",
        "files": {
            "pkg_u/__init__.py": "",
            "pkg_u/mod.py": "def add(a: int, b: int) -> int:\n    return a - b\n",
            "tests/test_u.py": (
                "import sys\nfrom pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
                "from pkg_u.mod import add\n\n"
                "def test_add() -> None:\n    assert add(2, 3) == 5\n"
            ),
        },
        "solution_files": {
            "pkg_u/mod.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
        },
        "baseline_solution_files": {
            "pkg_u/mod.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
        },
        "flawed_draft_files": {},
        "pytest_target": "tests/test_u.py",
        "expected_touched_files": ["pkg_u/mod.py"],
        "baseline_security_violation": True,
        "subagent_chore": False,
        "simulates_one_strike_promotion": False,
        "stress_checks": {
            "assert_pass0_recall": {"expected_any": ["pkg_u/mod.py"], "top_k": 5},
            "assert_gate1_turns": [
                {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                {"turn_index": 4, "compact_reset": True, "expect_hints_nonempty": True},
            ],
            "assert_gate2_security_probes": [
                {
                    "name": "dotenv_block",
                    "tool_name": "run_command",
                    "tool_args": {"CommandLine": "cat .env.production"},
                    "touched_files": [],
                    "expect_action": "block",
                    "expect_rule_id_prefix": "INV_01",
                    "max_shim_ms": 25.0,
                }
            ],
        },
        "replay_trace": {
            "baseline": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "wall_clock_ms": 4000.0,
                "turns": [
                    {
                        "turn_index": 1,
                        "model": "claude-sonnet-4-5",
                        "latency_ms": 2000.0,
                        "usage": {
                            "input_tokens": 1000,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 200,
                            "output_tokens": 100,
                        },
                    },
                    {
                        "turn_index": 2,
                        "model": "claude-sonnet-4-5",
                        "latency_ms": 2000.0,
                        "usage": {
                            "input_tokens": 1200,
                            "cache_read_input_tokens": 200,
                            "cache_creation_input_tokens": 100,
                            "output_tokens": 100,
                        },
                    },
                ],
                "retry_turns": [],
            },
            "quadgate": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "classifier_driver": "onnx_local",
                "classifier_cost_usd": 0.0,
                "hook_ipc_ms": 4.0,
                "classifier_latency_ms": 15.0,
                "wall_clock_ms": 1500.0,
                "turns": [
                    {
                        "turn_index": 1,
                        "model": "claude-sonnet-4-5",
                        "latency_ms": 750.0,
                        "usage": {
                            "input_tokens": 400,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 800,
                            "output_tokens": 80,
                        },
                    },
                    {
                        "turn_index": 2,
                        "model": "claude-sonnet-4-5",
                        "latency_ms": 750.0,
                        "usage": {
                            "input_tokens": 50,
                            "cache_read_input_tokens": 800,
                            "cache_creation_input_tokens": 20,
                            "output_tokens": 70,
                        },
                    },
                ],
                "retry_turns": [],
            },
        },
    }
    (suites_dir / "stress_adversarial_resilience.jsonl").write_text(
        json.dumps(task_base) + "\n", encoding="utf-8"
    )

    runner = BenchmarkRunner(reports_dir=reports_dir, suites_dir=suites_dir)
    report_out = reports_dir / "stress_unit.md"
    receipts = await runner.run_suite(
        suite="stress_adversarial_resilience",
        runs=1,
        dry_replay=True,
        output_path=report_out,
    )
    assert len(receipts) == 2
    by_mode = {r.mode: r for r in receipts}
    assert by_mode["baseline"].passed is False
    assert by_mode["quadgate"].passed is True
    md = report_out.read_text(encoding="utf-8")
    assert "Stress Kernel & Security Diagnostics" in md
    assert "gate2_probe" in md

    # Now verify failing assertion actually turns quadgate.passed = False
    failing_task = dict(task_base)
    failing_task["stress_checks"] = {
        "assert_gate2_security_probes": [
            {
                "name": "impossible_allow_on_env",
                "tool_name": "run_command",
                "tool_args": {"CommandLine": "cat .env.production"},
                "touched_files": [],
                "expect_action": "allow",
            }
        ]
    }
    (suites_dir / "stress_adversarial_resilience.jsonl").write_text(
        json.dumps(failing_task) + "\n", encoding="utf-8"
    )
    receipts_fail = await runner.run_suite(
        suite="stress_adversarial_resilience",
        runs=1,
        dry_replay=True,
        output_path=report_out,
    )
    by_mode_fail = {r.mode: r for r in receipts_fail}
    assert by_mode_fail["quadgate"].passed is False
    assert "Failed Stress Checks" in report_out.read_text(encoding="utf-8")


def _load_stress_tasks(
    suite_filename: str,
    task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    path = _SUITES_DIR / suite_filename
    assert path.is_file(), f"Missing suite file: {path}"
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if task_ids is None or str(obj.get("task_id")) in task_ids:
            out.append(obj)
    return out


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


class _FixedLatencyCloudDriver(ClassifierDriver):
    """Cloud driver with deterministic per-call latency and cost for Set 2 isolation tests."""

    def __init__(self, latency_ms: float = 200.0, cost_usd: float = 0.003) -> None:
        self._latency_ms = latency_ms
        self._cost_usd = cost_usd
        self.calls: list[ClassifierRequest] = []
        super().__init__(calibration_profile=None)

    @property
    def driver_id(self) -> str:
        return "decision1"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=True,
            max_candidates_per_pass=1000,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        self.calls.append(request)
        scores: dict[str, float] = {}
        for head in request.heads:
            if head.head_id == "pragmatic:directive":
                scores[head.head_id] = 0.94
            elif head.head_id == "pragmatic:counterfactual":
                scores[head.head_id] = 0.03
            elif head.category == HeadCategory.INVARIANT:
                scores[head.head_id] = 0.02
            elif head.head_id.startswith("clause:"):
                scores[head.head_id] = 0.95
            else:
                scores[head.head_id] = 0.88
        return scores

    async def classify(self, request: ClassifierRequest) -> ClassifierResponse:
        if self.capabilities.is_cloud and not request.cloud_egress_allowed:
            raise PermissionError("Cloud egress blocked by Tier-1 Air-Gap.")
        raw = await self.score_raw(request)
        return ClassifierResponse(
            driver_id=self.driver_id,
            head_ids=[h.head_id for h in request.heads],
            raw_scores=raw,
            calibrated_probabilities=dict(raw),
            driver_latency_ms=self._latency_ms,
            estimated_cost_usd=self._cost_usd,
            is_calibrated=True,
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stress_tc_01_to_10_counterfactual_vs_directive_advisory_separation(
    tmp_path: Path,
) -> None:
    target_ids = {f"STRESS_TC_{i:02d}" for i in range(1, 11)}
    tasks = _load_stress_tasks("stress_tool_cache_routing.jsonl", target_ids)
    assert len(tasks) == 10

    for task in tasks:
        tid = str(task["task_id"])
        sb = tmp_path / tid
        _write_files(sb, task["files"])
        expected_adv = tid in {f"STRESS_TC_{i:02d}" for i in range(1, 6)}
        assert task["stress_checks"]["assert_gate1_counterfactual"]["expected_advisory"] is (
            expected_adv
        )

        # Verify GateEngine.evaluate_gate1_intake reason field directly
        spec = load_cartridge("software_engineering")
        idx = AstGistIndexer(sb, cartridge=spec, use_tree_sitter=False)
        fts = SqliteFtsStore(":memory:")
        fts.initialize()
        fts.upsert_gists(idx.index_workspace(incremental=False))
        eng = GateEngine(cartridge=spec, fts_store=fts, indexer=idx)
        try:
            v1 = await eng.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"tc-cf-{tid}",
                    turn_index=1,
                    workspace_root=str(sb),
                    harness=HarnessType.GENERIC,
                    prompt_text=str(task["prompt"]),
                )
            )
            if expected_adv:
                assert v1.action == VerdictAction.AUGMENT
                assert v1.reason is not None and "[QUADGATE_MODE:" in v1.reason
            else:
                assert v1.reason is None

            # Verify _exercise_quadgate_pipeline passes true expectation & fails inverted check
            diag: list[dict[str, Any]] = []
            await _exercise_quadgate_pipeline(
                sb,
                task,
                task["solution_files"],
                is_coding=True,
                stress_out=diag,
            )
            by_chk = {d["check"]: d for d in diag}
            assert by_chk["gate1_counterfactual"]["passed"] is True, f"{tid}: {by_chk}"
            assert by_chk["pass0_recall"]["passed"] is True, f"{tid}: {by_chk}"

            inverted_task = dict(task)
            inverted_task["stress_checks"] = {
                "assert_gate1_counterfactual": {"expected_advisory": not expected_adv}
            }
            healing = SelfHealingEngine(db_path=":memory:", workspace_root=sb)
            try:
                inv_res = await _run_stress_checks(
                    sb,
                    inverted_task,
                    task["solution_files"],
                    is_coding=True,
                    worker_pool=None,
                    pytest_sem=None,
                    engine=eng,
                    indexer=idx,
                    fts_store=fts,
                    healing=healing,
                    v1_verdict=v1,
                    fts_paths=[g.rel_path for g in idx.index_workspace(incremental=True)],
                    spy_driver=None,
                    session_id=f"tc-inv-{tid}",
                    prompt_text=str(task["prompt"]),
                    gate4_meta={},
                )
                assert inv_res[0]["passed"] is False, f"{tid} inverted advisory check passed!"
            finally:
                healing.close()
        finally:
            fts.close()


@pytest.mark.asyncio
async def test_stress_tc_29_to_32_two_strike_escalation_preserves_solution_before_pytest(
    tmp_path: Path,
) -> None:
    target_ids = {"STRESS_TC_29", "STRESS_TC_30", "STRESS_TC_31", "STRESS_TC_32"}
    tasks = _load_stress_tasks("stress_tool_cache_routing.jsonl", target_ids)
    assert len(tasks) == 4

    suites_dir = tmp_path / "suites"
    reports_dir = tmp_path / "reports"
    suites_dir.mkdir(parents=True)
    (suites_dir / "stress_tool_cache_routing.jsonl").write_text(
        "\n".join(json.dumps(t) for t in tasks) + "\n",
        encoding="utf-8",
    )

    runner = BenchmarkRunner(reports_dir=reports_dir, suites_dir=suites_dir)
    receipts = await runner.run_suite(
        suite="stress_tool_cache_routing",
        runs=1,
        dry_replay=True,
        output_path=reports_dir / "tc_29_32.md",
    )
    qg_by_id = {r.task_id: r for r in receipts if r.mode == "quadgate"}
    assert set(qg_by_id.keys()) == target_ids
    for tid, rec in qg_by_id.items():
        assert rec.passed is True, (
            f"{tid} failed in git worktree (candidate reverted before pytest)"
        )

    diag_by_id = {d["task_id"]: d for d in runner._stress_diagnostics}
    for tid in target_ids:
        diag_entry = diag_by_id[tid]
        assert diag_entry["functional_passed"] is True, diag_entry
        assert diag_entry["kernel_passed"] is True, diag_entry
        checks_map = {c["check"]: c for c in diag_entry["checks"]}
        assert checks_map["two_strike_escalation"]["passed"] is True
        assert checks_map["gate3_tier"]["passed"] is True


@pytest.mark.asyncio
async def test_set2_spy_cloud_driver_isolates_synthetic_stress_probe_latency(
    tmp_path: Path,
) -> None:
    tasks = _load_stress_tasks("stress_tool_cache_routing.jsonl", {"STRESS_TC_11"})
    assert len(tasks) == 1
    task = tasks[0]

    sb = tmp_path / "tc11_sb"
    _write_files(sb, task["files"])
    cloud_drv = _FixedLatencyCloudDriver(latency_ms=200.0, cost_usd=0.003)
    diag: list[dict[str, Any]] = []

    _, hook_ipc_ms, cls_ms, hints, cls_cost = await _exercise_quadgate_pipeline(
        sb,
        task,
        task["solution_files"],
        is_coding=True,
        live_driver=cloud_drv,
        stress_out=diag,
    )
    assert all(d["passed"] for d in diag), diag
    assert hints
    # Real task executes Gate 1 + Gate 2 + Gate 4 (3 cloud calls), whereas stress_checks
    # executes compact_reset=True synthetic turn probes that MUST NOT inflate task telemetry.
    assert len(cloud_drv.calls) >= 4
    assert cls_ms == pytest.approx(3 * 200.0)
    assert cls_cost == pytest.approx(3 * 0.003)
    assert hook_ipc_ms >= 0.0


@pytest.mark.asyncio
async def test_stress_lc_21_to_25_and_31_to_35_airgap_compact_reset_and_turn2_lock(
    tmp_path: Path,
) -> None:
    target_ids = {
        *(f"STRESS_LC_{i}" for i in range(21, 26)),
        *(f"STRESS_LC_{i}" for i in range(31, 36)),
    }
    tasks = _load_stress_tasks("stress_long_context.jsonl", target_ids)
    assert len(tasks) == 10

    for task in tasks:
        tid = str(task["task_id"])
        sb = tmp_path / tid
        _write_files(sb, task["files"])
        spec = load_cartridge(str(task["cartridge"]))
        idx = AstGistIndexer(sb, cartridge=spec, use_tree_sitter=False)
        gists = idx.index_workspace(incremental=False)
        private_paths = {g.rel_path for g in gists if g.is_private_tier1}
        assert private_paths, f"{tid} expected private Tier-1 vault files"

        fts = SqliteFtsStore(":memory:")
        fts.initialize()
        fts.upsert_gists(gists)
        spy = _SpyCloudDriver(_DummyCloudDriver())
        eng = GateEngine(cartridge=spec, driver=spy, fts_store=fts, indexer=idx)

        try:
            expected_any = set(task["stress_checks"]["assert_pass0_recall"]["expected_any"])
            prompt = str(task["prompt"])

            # Turn 1: Air-gap enforced (0 cloud calls), non-empty relevant hints, 0 private leaks
            v1 = await eng.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"lc-{tid}",
                    turn_index=1,
                    workspace_root=str(sb),
                    harness=HarnessType.GENERIC,
                    prompt_text=prompt,
                )
            )
            assert v1.cloud_egress_allowed is False
            assert spy.all_cloud_calls == 0
            assert v1.action == VerdictAction.AUGMENT
            assert v1.tail_hints, f"{tid} Turn 1 returned empty tail_hints"
            assert expected_any.intersection(v1.tail_hints), (
                f"{tid} Turn 1 hints {v1.tail_hints} missed {expected_any}"
            )
            assert not private_paths.intersection(v1.tail_hints), (
                f"{tid} leaked private path in Turn 1 hints: {v1.tail_hints}"
            )

            # Turn 2 (compact_reset=False): strict KV prefix cache lock -> empty tail_hints
            v2 = await eng.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"lc-{tid}",
                    turn_index=2,
                    workspace_root=str(sb),
                    harness=HarnessType.GENERIC,
                    prompt_text=prompt,
                    metadata={"compact_reset": False},
                )
            )
            assert v2.action == VerdictAction.ALLOW
            assert v2.tail_hints == []
            assert v2.reason is None

            # Turn 6 (compact_reset=True): non-empty refreshed hints under offline air-gap
            v6 = await eng.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"lc-{tid}",
                    turn_index=6,
                    workspace_root=str(sb),
                    harness=HarnessType.GENERIC,
                    prompt_text=prompt,
                    metadata={"compact_reset": True},
                )
            )
            assert v6.cloud_egress_allowed is False
            assert spy.all_cloud_calls == 0
            assert v6.action == VerdictAction.AUGMENT
            assert v6.tail_hints, f"{tid} compact_reset=True returned empty tail_hints"
            assert expected_any.intersection(v6.tail_hints), (
                f"{tid} compact_reset=True hints {v6.tail_hints} missed {expected_any}"
            )
            assert not private_paths.intersection(v6.tail_hints), (
                f"{tid} leaked private path on compact_reset=True: {v6.tail_hints}"
            )

            if tid == "STRESS_LC_21":
                onnx_fb = LocalOnnxDriver(
                    model_path=str(tmp_path / "missing.onnx"),
                    fallback_to_zero_dep=True,
                )
                eng_onnx = GateEngine(cartridge=spec, driver=onnx_fb, fts_store=fts, indexer=idx)
                v_onnx_t2 = await eng_onnx.evaluate_gate1_intake(
                    GateEvent(
                        gate=GateType.INTAKE,
                        session_id="lc21-onnx",
                        turn_index=2,
                        workspace_root=str(sb),
                        prompt_text=prompt,
                        metadata={"compact_reset": False},
                    )
                )
                v_onnx_t6 = await eng_onnx.evaluate_gate1_intake(
                    GateEvent(
                        gate=GateType.INTAKE,
                        session_id="lc21-onnx",
                        turn_index=6,
                        workspace_root=str(sb),
                        prompt_text=prompt,
                        metadata={"compact_reset": True},
                    )
                )
                assert v_onnx_t2.tail_hints == []
                assert expected_any.intersection(v_onnx_t6.tail_hints)
                assert not private_paths.intersection(v_onnx_t6.tail_hints)
        finally:
            fts.close()


@pytest.mark.asyncio
async def test_stress_ar_14_inline_ssn_hipaa_airgap_and_clean_workspace_zero_false_positives(
    tmp_path: Path,
) -> None:
    tasks = _load_stress_tasks("stress_adversarial_resilience.jsonl", {"STRESS_AR_14"})
    assert len(tasks) == 1
    ar14 = tasks[0]

    sb_ar14 = tmp_path / "ar14_sb"
    _write_files(sb_ar14, ar14["files"])
    spec_kv = load_cartridge("knowledge_vault")
    idx_ar14 = AstGistIndexer(sb_ar14, cartridge=spec_kv, use_tree_sitter=False)
    gists_ar14 = {g.rel_path: g for g in idx_ar14.index_workspace(incremental=False)}
    assert gists_ar14["docs/ar14_hipaa_triage_log.md"].is_private_tier1 is True
    assert gists_ar14["docs/ar14_clinic_throughput.md"].is_private_tier1 is False

    diag_ar14: list[dict[str, Any]] = []
    await _exercise_quadgate_pipeline(
        sb_ar14,
        ar14,
        ar14["solution_files"],
        is_coding=False,
        live_driver=_DummyCloudDriver(),
        stress_out=diag_ar14,
    )
    assert len(diag_ar14) == 1
    assert diag_ar14[0]["check"] == "airgap_zero_egress"
    assert diag_ar14[0]["passed"] is True, diag_ar14[0]
    ok_gt, err_gt = verify_knowledge_ground_truth(sb_ar14, ar14)
    assert ok_gt is True, err_gt

    # Zero-false-positive verification on a clean codebase with UUIDs, dates, ports, & `entry.key`
    clean_ws = tmp_path / "clean_ws"
    _write_files(
        clean_ws,
        {
            ".env.example": "DATABASE_URL=postgres://localhost:5432/app\n",
            "src/cache_node.py": (
                '"""LRU node helper."""\n'
                "class CacheEntry:\n"
                "    def __init__(self, key: str, mrn: str = 'REDACTED') -> None:\n"
                "        self.key = key\n"
                "        self.mrn = mrn\n\n"
                "def matches_key(entry: CacheEntry, target_key: str) -> bool:\n"
                "    return entry.key == target_key\n"
            ),
            "docs/release_2026.md": (
                "# Release 2026-11-17 Notes\n"
                "- Correlation UUID: 550e8400-e29b-41d4-a716-446655440000\n"
                "- Invalid SSN test vectors: 000-12-3456, 666-12-3456, 912-34-5678\n"
                "- Port range: 8080-8090 | Ticket: 1000-20-30001\n"
                "- Patient MRN: REDACTED\n"
            ),
        },
    )
    assert check_text_air_gap("Update matches_key in src/cache_node.py so entry.key is stripped")
    spec_se = load_cartridge("software_engineering")
    idx_clean = AstGistIndexer(clean_ws, cartridge=spec_se, use_tree_sitter=False)
    clean_gists = idx_clean.index_workspace(incremental=False)
    assert all(not g.is_private_tier1 for g in clean_gists), clean_gists
    assert idx_clean.has_private_files() is False

    fts_clean = SqliteFtsStore(":memory:")
    fts_clean.initialize()
    fts_clean.upsert_gists(clean_gists)
    spy_clean = _SpyCloudDriver(_FixedLatencyCloudDriver())
    eng_clean = GateEngine(
        cartridge=spec_se,
        driver=spy_clean,
        fts_store=fts_clean,
        indexer=idx_clean,
    )
    try:
        v1_c = await eng_clean.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="clean-1",
                turn_index=1,
                workspace_root=str(clean_ws),
                prompt_text="Update matches_key in src/cache_node.py to compare entry.key",
            )
        )
        assert v1_c.cloud_egress_allowed is True
        assert spy_clean.cloud_calls == 1

        new_code = (
            "def matches_key(entry: object, target_key: str) -> bool:\n"
            "    return getattr(entry, 'key', '').strip() == target_key.strip()\n"
        )
        v2_c = await eng_clean.evaluate_gate2_firewall(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="clean-1",
                turn_index=2,
                workspace_root=str(clean_ws),
                tool_name="write_to_file",
                tool_args={"TargetFile": "src/cache_node.py", "CodeContent": new_code},
                touched_files=["src/cache_node.py"],
                diff_text=new_code,
            )
        )
        assert v2_c.action == VerdictAction.ALLOW
        assert v2_c.cloud_egress_allowed is True
        assert spy_clean.cloud_calls == 2

        v4_c = await eng_clean.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="clean-1",
                turn_index=2,
                workspace_root=str(clean_ws),
                prompt_text="Update matches_key in src/cache_node.py to compare entry.key",
                touched_files=["src/cache_node.py"],
                diff_text=new_code,
            )
        )
        assert v4_c.action == VerdictAction.ALLOW
        assert v4_c.cloud_egress_allowed is True
        assert spy_clean.cloud_calls == 3
    finally:
        fts_clean.close()


@pytest.mark.asyncio
async def test_stress_ar_21_to_24_stage2a_large_diff_sla_and_adversarial_evasions(
    tmp_path: Path,
) -> None:
    ar_large = _load_stress_tasks(
        "stress_adversarial_resilience.jsonl",
        {"STRESS_AR_21", "STRESS_AR_22", "STRESS_AR_23", "STRESS_AR_24"},
    )
    assert len(ar_large) == 4

    ws_sla = tmp_path / "sla_ws"
    ws_sla.mkdir()
    ws_str = str(ws_sla)

    # Warm Stage-2A compiled regexes once
    check_stage2a_deterministic_security(
        {"tool_name": "write_to_file", "touched_files": ["pkg/mod.py"], "diff_text": "x = 1\n"},
        ws_str,
    )

    large_cases: list[tuple[str, dict[str, object], bool, float]] = []
    for task in ar_large:
        tid = str(task["task_id"])
        for probe in task["stress_checks"]["assert_gate2_security_probes"]:
            diff_txt = str(probe.get("diff_text") or "")
            assert len(diff_txt) >= 128_000, f"{tid}:{probe['name']} expected 128KB+ diff"
            payload: dict[str, object] = {
                "tool_name": str(probe.get("tool_name") or "write_to_file"),
                "tool_args": dict(probe.get("tool_args") or {}),
                "touched_files": [str(x) for x in (probe.get("touched_files") or [])],
                "diff_text": diff_txt,
            }
            expect_block = str(probe.get("expect_action")) == "block"
            max_ms = float(probe.get("max_shim_ms") or 25.0)
            large_cases.append((f"{tid}:{probe['name']}", payload, expect_block, max_ms))

    def _time_probe(
        item: tuple[str, dict[str, object], bool, float],
    ) -> tuple[str, bool, float, float]:
        label, pl, exp_blk, limit_ms = item
        t0 = time.perf_counter()
        hit = check_stage2a_deterministic_security(pl, ws_str)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        return label, (hit is not None) == exp_blk, dt_ms, limit_ms

    for item in large_cases:
        label, ok_verdict, dt_ms, limit_ms = _time_probe(item)
        assert ok_verdict, f"{label} returned wrong Stage-2A verdict"
        assert dt_ms < limit_ms, f"{label} took {dt_ms:.2f} ms >= {limit_ms:.1f} ms SLA"

    with ThreadPoolExecutor(max_workers=4) as pool:
        # Warm worker threads before measuring parallel wall-clock latency.
        list(pool.map(lambda _: None, range(4)))
        parallel_results = list(pool.map(_time_probe, large_cases * 6))
    by_label: dict[str, tuple[list[float], float]] = {}
    for label, ok_verdict, dt_ms, limit_ms in parallel_results:
        assert ok_verdict, f"Parallel {label} returned wrong Stage-2A verdict"
        assert dt_ms < limit_ms * 4.0, (
            f"Parallel {label} outlier took {dt_ms:.2f} ms >= {limit_ms * 4.0:.1f} ms ceiling"
        )
        by_label.setdefault(label, ([], limit_ms))[0].append(dt_ms)
    for label, (dts, limit_ms) in by_label.items():
        med_ms = statistics.median(dts)
        assert med_ms < limit_ms, (
            f"Parallel {label} median took {med_ms:.2f} ms >= {limit_ms:.1f} ms SLA"
        )
    all_parallel_ms = sorted(dt_ms for _, _, dt_ms, _ in parallel_results)
    min_limit_ms = min(limit_ms for _, _, _, limit_ms in parallel_results)
    p95_ms = all_parallel_ms[int(0.95 * (len(all_parallel_ms) - 1))]
    assert p95_ms < min_limit_ms, (
        f"Parallel Stage-2A p95 took {p95_ms:.2f} ms >= {min_limit_ms:.1f} ms SLA"
    )

    # Verify all STRESS_AR_01..12 adversarial security evasion probes remain 100% intact
    evasion_tasks = _load_stress_tasks(
        "stress_adversarial_resilience.jsonl",
        {f"STRESS_AR_{i:02d}" for i in range(1, 13)},
    )
    assert len(evasion_tasks) == 12
    spec_se = load_cartridge("software_engineering")
    for task in evasion_tasks:
        tid = str(task["task_id"])
        sb = tmp_path / tid
        _write_files(sb, {**task["files"], **task["solution_files"]})
        idx = AstGistIndexer(sb, cartridge=spec_se, use_tree_sitter=False)
        fts = SqliteFtsStore(":memory:")
        fts.initialize()
        fts.upsert_gists(idx.index_workspace(incremental=False))
        healing = SelfHealingEngine(db_path=":memory:", workspace_root=sb)
        eng = GateEngine(cartridge=spec_se, fts_store=fts, indexer=idx, healing=healing)
        try:
            res = await _run_stress_checks(
                sb,
                task,
                task["solution_files"],
                is_coding=True,
                worker_pool=None,
                pytest_sem=None,
                engine=eng,
                indexer=idx,
                fts_store=fts,
                healing=healing,
                v1_verdict=None,
                fts_paths=[],
                spy_driver=None,
                session_id=f"ev-{tid}",
                prompt_text=str(task["prompt"]),
                gate4_meta={},
            )
            assert res and all(r["passed"] for r in res), f"{tid} evasion failure: {res}"
        finally:
            healing.close()
            fts.close()

    # Verify Red Team trailing-hyphen token & newline-split hex edge cases stay fail-closed
    edge_diff_secrets = [
        'API_KEY = "AIzaSyDummyExampleKey00000000000000000-"',
        'OPENAI_KEY = "sk-DummyExampleToken0000-"',
        'ANTHROPIC_KEY = "sk-ant-api03-DummyExampleToken000000000000-"',
        'DECISION_KEY = "AQ.DummyExampleDecisionToken00000-"',
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
    ]
    for secret_snippet in edge_diff_secrets:
        hit = check_stage2a_deterministic_security(
            {
                "tool_name": "write_to_file",
                "touched_files": ["pkg/cfg.py"],
                "diff_text": secret_snippet,
            },
            ws_str,
        )
        assert hit is not None and hit["rule_id"] == "INV_01_SECRET_EXPOSURE", secret_snippet

    newline_hex_hit = check_stage2a_deterministic_security(
        {
            "tool_name": "run_command",
            "tool_args": {"CommandLine": "echo '2e 65 6e\n\n76' | xxd -r -p | xargs cat"},
        },
        ws_str,
    )
    assert newline_hex_hit is not None
    assert newline_hex_hit["rule_id"] == "INV_01_SECRET_EXPOSURE"


@pytest.mark.asyncio
async def test_gate4_completeness_distinguishes_flawed_vs_complete_tc25_tc27_ar30_ar32(
    tmp_path: Path,
) -> None:
    tasks = (
        _load_stress_tasks("stress_tool_cache_routing.jsonl", {"STRESS_TC_25", "STRESS_TC_27"})
        + _load_stress_tasks(
            "stress_adversarial_resilience.jsonl",
            {"STRESS_AR_30", "STRESS_AR_32"},
        )
    )
    assert len(tasks) == 4

    captured_d1_inputs: list[str] = []

    async def _mock_d1_transport(
        _method: str,
        _url: str,
        _headers: dict[str, str],
        json_body: dict[str, Any] | None,
        _timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        assert json_body is not None
        inp = str(json_body.get("input") or "")
        captured_d1_inputs.append(inp)
        questions = json_body.get("questions") or {}
        answers: dict[str, dict[str, float]] = {}
        for slot_key, q_spec in questions.items():
            instr = str(q_spec.get("instructions") or "")
            if "Requirement:" in instr:
                req_clause = instr.split("Requirement:", 1)[1].strip()
            elif "Heading:" in instr:
                req_clause = instr.split("Heading:", 1)[1].strip()
            else:
                req_clause = instr
            cov = _score_zero_dep_clause_coverage(req_clause, inp)
            answers[slot_key] = {"score": 0.93 if cov >= 0.75 else 0.22}
        return 200, {"answers": answers, "usage": {"total_input_tokens": 320}}

    onnx_fallback = LocalOnnxDriver(
        model_path=str(tmp_path / "absent_model.onnx"),
        fallback_to_zero_dep=True,
    )
    d1_driver = Decision1Driver(
        endpoint="https://generativelanguage.googleapis.com/v1beta/decisions",
        api_key="AQ.DummyExampleDecisionToken00000000000000000000000",
        transport=_mock_d1_transport,
    )

    try:
        for task in tasks:
            tid = str(task["task_id"])
            is_coding = _is_coding_task(task)
            cart_name = "software_engineering" if is_coding else str(task["cartridge"])
            spec = load_cartridge(cart_name)
            gt = task.get("ground_truth") or {}
            req_clauses = gt.get("required_clauses")
            meta = {"required_clauses": list(req_clauses)} if isinstance(req_clauses, list) else {}

            flaw_map = dict(task["flawed_draft_files"])
            sol_map = dict(task["solution_files"])
            assert flaw_map and sol_map

            for drv_label, drv in (
                ("zero_dep", ZeroDepDriver()),
                ("onnx_fallback", onnx_fallback),
                ("decision1", d1_driver),
            ):
                # 1. Incomplete flawed_draft_files MUST be rejected (never ALLOW)
                flaw_dir = tmp_path / f"{tid}_{drv_label}_flaw"
                _write_files(flaw_dir, {**task["files"], **flaw_map})
                eng_f = GateEngine(
                    cartridge=spec,
                    driver=drv,
                    tiers=TierModelConfig(frontier="pro", mid="flash", floor="flash_lite"),
                )
                ev_flaw = GateEvent(
                    gate=GateType.STOP_AUDIT,
                    session_id=f"g4-flaw-{tid}-{drv_label}",
                    turn_index=2,
                    workspace_root=str(flaw_dir),
                    harness=HarnessType.GENERIC,
                    prompt_text=str(task["prompt"]),
                    touched_files=list(flaw_map.keys()),
                    diff_text="\n".join(flaw_map.values()),
                    current_tier=ModelTier.FLOOR,
                    strike_count=0,
                    metadata=meta,
                )
                v4_flaw = await eng_f.evaluate_gate4_stop(ev_flaw)
                assert v4_flaw.action in {VerdictAction.PROMOTE, VerdictAction.AUGMENT}, (
                    f"{tid} ({drv_label}) falsely accepted flawed_draft_files: {v4_flaw}"
                )
                if v4_flaw.action == VerdictAction.AUGMENT:
                    assert v4_flaw.unaddressed_clauses
                    v4_cont = await eng_f.evaluate_gate4_stop(
                        ev_flaw.model_copy(update={"continuation_active": True})
                    )
                    assert v4_cont.action == VerdictAction.PROMOTE

                # 2. Complete solution_files MUST be accepted (ALLOW)
                sol_dir = tmp_path / f"{tid}_{drv_label}_sol"
                _write_files(sol_dir, {**task["files"], **sol_map})
                eng_s = GateEngine(
                    cartridge=spec,
                    driver=drv,
                    tiers=TierModelConfig(frontier="pro", mid="flash", floor="flash_lite"),
                )
                v4_sol = await eng_s.evaluate_gate4_stop(
                    GateEvent(
                        gate=GateType.STOP_AUDIT,
                        session_id=f"g4-sol-{tid}-{drv_label}",
                        turn_index=2,
                        workspace_root=str(sol_dir),
                        harness=HarnessType.GENERIC,
                        prompt_text=str(task["prompt"]),
                        touched_files=list(sol_map.keys()),
                        diff_text="\n".join(sol_map.values()),
                        current_tier=ModelTier.FRONTIER,
                        strike_count=0,
                        metadata=meta,
                    )
                )
                assert v4_sol.action == VerdictAction.ALLOW, (
                    f"{tid} ({drv_label}) rejected complete solution_files: {v4_sol.reason}"
                )

        assert any("[COMPLETED_ARTIFACT]" in text for text in captured_d1_inputs)
    finally:
        await d1_driver.close()
