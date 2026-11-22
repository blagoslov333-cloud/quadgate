"""Regression tests for Gate 4 oracle accuracy, file-scoped healing, and Neon tier inference."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from quadgate.adapters.antigravity import AntigravityAdapter, _infer_model_tier
from quadgate.adapters.antigravity_plugin import _HOOK_SCRIPT_PY
from quadgate.contracts import (
    CartridgeSpec,
    ContextUnitSpec,
    GateEvent,
    GateType,
    ModelTier,
    TruthOracleSpec,
    VerdictAction,
)
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.kernel.gates import GateEngine, load_cartridge
from quadgate.kernel.healing import SelfHealingEngine


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _init_git_repo(repo: Path, files: dict[str, str]) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@quadgate.dev")
    _git(repo, "config", "user.name", "QuadGate Test")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Initial commit")


@pytest.mark.asyncio
async def test_gate4_oracle_timeout_is_inconclusive_and_does_not_trip_breaker_or_revert(
    tmp_path: Path,
) -> None:
    """Oracle command timeout must NOT trigger circuit breaker, revert files, or record failures."""
    repo = tmp_path / "timeout_repo"
    repo.mkdir()
    orig_code = "export const value: number = 1;\n"
    updated_code = "export const value: number = 2;\n"
    _init_git_repo(repo, {"app.ts": orig_code, "tsconfig.json": "{}\n"})
    (repo / "app.ts").write_text(updated_code, encoding="utf-8")

    cartridge = CartridgeSpec(
        name="ts_timeout_test",
        context_unit=ContextUnitSpec(indexer="tree_sitter_ast", include_globs=["**/*.ts"]),
        mutation_tools=["write_file"],
        invariants=[],
        truth_oracle=TruthOracleSpec(
            timeout_ms=60,
            commands_by_extension={
                ".ts": ["python", "-c", "import time; time.sleep(2.0)"],
            },
            check_clause_completeness=True,
        ),
    )
    healing = SelfHealingEngine(db_path=tmp_path / "healing_timeout.db", workspace_root=repo)
    engine = GateEngine(cartridge=cartridge, healing=healing)
    try:
        err_msg, failed_files, timed_out = await engine._run_extension_oracle_commands_detailed(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-timeout",
                workspace_root=str(repo),
                current_tier=ModelTier.FRONTIER,
                strike_count=1,
                touched_files=["app.ts"],
            )
        )
        assert timed_out is True
        assert failed_files == []
        assert err_msg is not None and "timed out" in err_msg

        verdict = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-timeout",
                workspace_root=str(repo),
                current_tier=ModelTier.FRONTIER,
                strike_count=1,
                touched_files=["app.ts"],
                prompt_text="Update value in app.ts",
                diff_text=updated_code,
            )
        )
        assert verdict.action == VerdictAction.ALLOW
        assert verdict.violation_receipt is None
        assert (repo / "app.ts").read_text(encoding="utf-8") == updated_code

        summary = healing.get_telemetry_summary()
        assert summary["circuit_breaker_halts"] == 0
        assert summary["sessions_with_strikes"] == 0
        assert summary["total_file_failures"] == 0
        assert healing.get_failure_rate("app.ts", workspace_root=str(repo)) == 0.0
    finally:
        healing.close()


@pytest.mark.asyncio
async def test_gate4_single_syntax_error_file_only_penalizes_and_reverts_failing_file(
    tmp_path: Path,
) -> None:
    """Only the failing file among multiple touched_files is marked failed and reverted."""
    repo = tmp_path / "multi_file_repo"
    repo.mkdir()
    clean_orig = "def clean_fn() -> int:\n    return 1\n"
    clean_new = "def clean_fn() -> int:\n    return 42\n"
    bad_orig = "def target_fn() -> int:\n    return 10\n"
    bad_new = "def target_fn(:\n    return 10\n"
    _init_git_repo(repo, {"clean.py": clean_orig, "broken.py": bad_orig})

    (repo / "clean.py").write_text(clean_new, encoding="utf-8")
    (repo / "broken.py").write_text(bad_new, encoding="utf-8")

    healing = SelfHealingEngine(db_path=tmp_path / "healing_multi.db", workspace_root=repo)
    engine = GateEngine(cartridge=load_cartridge("software_engineering"), healing=healing)
    try:
        verdict = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-multi",
                workspace_root=str(repo),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                touched_files=["clean.py", "broken.py"],
                prompt_text="Update clean_fn and target_fn",
            )
        )
        assert verdict.action == VerdictAction.PROMOTE
        assert verdict.target_tier == ModelTier.MID
        assert healing.get_failure_rate("broken.py", workspace_root=str(repo)) > 0.0
        assert healing.get_failure_rate("clean.py", workspace_root=str(repo)) == 0.0

        # Only broken.py was reverted to HEAD; clean.py edits remain intact.
        assert (repo / "broken.py").read_text(encoding="utf-8") == bad_orig
        assert (repo / "clean.py").read_text(encoding="utf-8") == clean_new

        summary = healing.get_telemetry_summary()
        assert summary["tracked_files_count"] == 1
        assert summary["total_file_failures"] == 1
    finally:
        healing.close()


@pytest.mark.asyncio
async def test_gate4_directive_completeness_below_060_emits_augment_without_circuit_breaker(
    tmp_path: Path,
) -> None:
    """Completeness < 0.60 with continuation_active=False and evidence emits AUGMENT, 0 halts/failures."""
    repo = tmp_path / "comp_augment_repo"
    repo.mkdir()
    orig_code = "def login() -> bool:\n    return False\n"
    new_code = "def login() -> bool:\n    return True\n"
    _init_git_repo(repo, {"auth.py": orig_code})
    (repo / "auth.py").write_text(new_code, encoding="utf-8")

    healing = SelfHealingEngine(db_path=tmp_path / "healing_comp_aug.db", workspace_root=repo)
    engine = GateEngine(cartridge=load_cartridge("software_engineering"), healing=healing)
    try:
        prompt = (
            "1. Implement login in auth.py\n"
            "2. Configure Kubernetes Helm chart values in deploy/helm/values.yaml\n"
            "3. Add Prometheus alert rules in monitoring/alerts.yml\n"
            "4. Document disaster recovery runbook in docs/dr_playbook.md"
        )
        verdict = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-comp-low",
                workspace_root=str(repo),
                current_tier=ModelTier.FRONTIER,
                strike_count=0,
                continuation_active=False,
                touched_files=["auth.py"],
                prompt_text=prompt,
                diff_text=new_code,
            )
        )
        assert verdict.action == VerdictAction.AUGMENT
        assert len(verdict.unaddressed_clauses) >= 2
        assert verdict.violation_receipt is None
        assert (repo / "auth.py").read_text(encoding="utf-8") == new_code

        summary = healing.get_telemetry_summary()
        assert summary["circuit_breaker_halts"] == 0
        assert summary["sessions_with_strikes"] == 0
        assert summary["total_file_failures"] == 0
        assert healing.get_failure_rate("auth.py", workspace_root=str(repo)) == 0.0
    finally:
        healing.close()


@pytest.mark.asyncio
async def test_gate4_directive_completeness_continuation_active_promotes_without_revert_or_failure(
    tmp_path: Path,
) -> None:
    """Completeness failure with continuation_active=True promotes FLOOR->MID without revert or file failure."""
    repo = tmp_path / "comp_cont_repo"
    repo.mkdir()
    orig_code = "def verify_token(token: str) -> bool:\n    return False\n"
    new_code = "def verify_token(token: str) -> bool:\n    return bool(token)\n"
    _init_git_repo(repo, {"auth.py": orig_code})
    (repo / "auth.py").write_text(new_code, encoding="utf-8")

    healing = SelfHealingEngine(db_path=tmp_path / "healing_comp_cont.db", workspace_root=repo)
    engine = GateEngine(cartridge=load_cartridge("software_engineering"), healing=healing)
    try:
        prompt = (
            "1. Update verify_token in auth.py\n"
            "2. Add enforce_rate_limit in gateway.py\n"
            "3. Write changelog notes in CHANGELOG.md"
        )
        verdict = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-comp-cont",
                workspace_root=str(repo),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                continuation_active=True,
                touched_files=["auth.py"],
                prompt_text=prompt,
                diff_text=new_code,
            )
        )
        assert verdict.action == VerdictAction.PROMOTE
        assert verdict.target_tier == ModelTier.MID
        # Valid compiling code in auth.py must NOT be reverted or marked as a file failure!
        assert (repo / "auth.py").read_text(encoding="utf-8") == new_code
        assert healing.get_failure_rate("auth.py", workspace_root=str(repo)) == 0.0
        summary = healing.get_telemetry_summary()
        assert summary["total_file_failures"] == 0
    finally:
        healing.close()


@pytest.mark.asyncio
async def test_gate4_early_allow_paths_record_clean_pass_in_file_telemetry(
    tmp_path: Path,
) -> None:
    """Early ALLOW (no clauses or check_clause_completeness=False) records passed=True in file_telemetry."""
    ws = tmp_path / "early_allow_ws"
    ws.mkdir()
    (ws / "mod.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    (ws / "answer.json").write_text('{"final_answer": 42}\n', encoding="utf-8")

    healing = SelfHealingEngine(db_path=tmp_path / "healing_early.db", workspace_root=ws)
    try:
        # 1. No clauses in prompt_text
        se_engine = GateEngine(cartridge=load_cartridge("software_engineering"), healing=healing)
        v_no_clauses = await se_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-early-1",
                workspace_root=str(ws),
                touched_files=["mod.py"],
                prompt_text="",
            )
        )
        assert v_no_clauses.action == VerdictAction.ALLOW

        # 2. check_clause_completeness=False (general_reasoning cartridge)
        gr_engine = GateEngine(cartridge=load_cartridge("general_reasoning"), healing=healing)
        v_no_chk = await gr_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-early-2",
                workspace_root=str(ws),
                touched_files=["answer.json"],
                prompt_text="Compute 6 * 7 and write JSON answer.",
            )
        )
        assert v_no_chk.action == VerdictAction.ALLOW

        summary = healing.get_telemetry_summary()
        assert summary["tracked_files_count"] == 2
        assert summary["total_file_runs"] == 2
        assert summary["total_file_failures"] == 0
    finally:
        healing.close()


def test_neon_model_names_infer_frontier_tier() -> None:
    """Verify 'Orchestrator (Neon)' and 'Gemini 4 Neon' infer ModelTier.FRONTIER across adapters."""
    assert _infer_model_tier("Orchestrator (Neon)") == ModelTier.FRONTIER
    assert _infer_model_tier("Gemini 4 Neon") == ModelTier.FRONTIER
    assert _infer_model_tier("gemini-4-neon-high") == ModelTier.FRONTIER

    adapter = AntigravityAdapter()
    ev = adapter.parse_event(
        GateType.STOP_AUDIT,
        {
            "hookEventName": "Stop",
            "modelName": "Gemini 4 Neon",
            "workspacePaths": ["."],
        },
    )
    assert ev.current_tier == ModelTier.FRONTIER

    ns: dict[str, Any] = {}
    exec(_HOOK_SCRIPT_PY, ns)
    infer_stdlib = ns["_infer_tier_stdlib"]
    assert infer_stdlib("Orchestrator (Neon)") == "frontier"
    assert infer_stdlib("Gemini 4 Neon") == "frontier"
    assert infer_stdlib("gemini-4-neon-high") == "frontier"


def test_workspace_has_private_files_no_double_walk_and_immediate_dotenv_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _workspace_has_private_files trusts indexer.has_private_files() False without os.walk and invalidates on .env."""
    ws = tmp_path / "clean_proj"
    ws.mkdir()
    (ws / "main.py").write_text("def run() -> int:\n    return 0\n", encoding="utf-8")

    spec = load_cartridge("software_engineering")
    indexer = AstGistIndexer(ws, cartridge=spec, use_tree_sitter=False)
    indexer.index_workspace(incremental=False)
    engine = GateEngine(cartridge=spec, indexer=indexer)

    walk_calls: list[str] = []
    real_walk = os.walk

    def _spy_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
        walk_calls.append(str(top))
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", _spy_walk)

    assert engine._workspace_has_private_files(str(ws)) is False
    assert walk_calls == [], "Expected 0 os.walk calls when indexer.has_private_files() returns False"
    assert engine._collect_private_tier1_secret_tokens(str(ws)) == {}
    assert walk_calls == []

    # Adding a .env file must invalidate the 30s cache immediately!
    (ws / ".env").write_text("API_SECRET_TOKEN=sk-live-secret-99887766\n", encoding="utf-8")
    assert engine._workspace_has_private_files(str(ws)) is True
    tokens = engine._collect_private_tier1_secret_tokens(str(ws))
    assert "sk-live-secret-99887766" in tokens


@pytest.mark.asyncio
async def test_gate4_local_tsc_binary_bypasses_npx_overhead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ["npx", "--no-install", "tsc", ...] uses local node_modules/.bin/tsc directly."""
    ws = tmp_path / "ts_proj"
    bin_dir = ws / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (ws / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    (ws / "index.ts").write_text("export const x: number = 1;\n", encoding="utf-8")

    local_tsc = bin_dir / ("tsc.cmd" if os.name == "nt" else "tsc")
    local_tsc.write_text("echo ok\n", encoding="utf-8")

    captured_argv: list[tuple[str, ...]] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    async def _fake_exec(*argv: str, **kwargs: Any) -> _FakeProc:
        captured_argv.append(tuple(argv))
        return _FakeProc()

    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    engine = GateEngine(cartridge=load_cartridge("software_engineering"))
    err, failed, timed_out = await engine._run_extension_oracle_commands_detailed(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-tsc",
            workspace_root=str(ws),
            touched_files=["index.ts"],
        )
    )
    assert err is None
    assert failed == []
    assert timed_out is False
    assert len(captured_argv) == 1
    assert captured_argv[0][0] == str(local_tsc)
    assert "--noEmit" in captured_argv[0][1:]

