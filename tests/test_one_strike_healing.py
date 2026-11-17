"""End-to-end tests for Wave 3 SelfHealingEngine (3 closed-loop self-healing loops)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from quadgate.contracts import GateEvent, GateType, ModelTier, VerdictAction
from quadgate.kernel.gates import TierModelConfig
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


async def test_loop1_context_miss_tracker_persists_wal_and_surfaces_on_next_session(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    db_path = tmp_path / "state" / "healing.db"

    engine = SelfHealingEngine(db_path=db_path, workspace_root=repo)
    try:
        # Verify WAL mode is active on file-backed SQLite DB.
        with sqlite3.connect(db_path) as check_conn:
            mode = check_conn.execute("PRAGMA journal_mode;").fetchone()
            assert mode is not None
            assert str(mode[0]).lower() == "wal"

        # Turn 1 hints {A, B}.
        engine.record_turn1_hints("sess-1", str(repo), ["src/a.py", "src/b.py"])

        # Agent reads C and D (D is never edited).
        read_c = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-1",
            turn_index=2,
            workspace_root=str(repo),
            tool_name="view_file",
            tool_args={"AbsolutePath": str(repo / "src" / "c.py")},
        )
        read_d = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-1",
            turn_index=2,
            workspace_root=str(repo),
            tool_name="read_file",
            tool_args={"Path": "src/d.py"},
        )
        assert await engine.observe_tool_event(read_c) == []
        assert await engine.observe_tool_event(read_d) == []

        # Editing a hinted file (A) or an unread file (E) must not record associative links.
        edit_a = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-1",
            turn_index=3,
            workspace_root=str(repo),
            tool_name="replace_file_content",
            tool_args={"TargetFile": "src/a.py"},
        )
        edit_e = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-1",
            turn_index=3,
            workspace_root=str(repo),
            tool_name="write_to_file",
            tool_args={"TargetFile": "src/e.py"},
        )
        assert await engine.observe_tool_event(edit_a) == []
        assert await engine.observe_tool_event(edit_e) == []

        # Editing read non-hinted file C records (A, C) and (B, C) automatically.
        edit_c = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-1",
            turn_index=4,
            workspace_root=str(repo),
            tool_name="replace_file_content",
            tool_args={"TargetFile": str(repo / "src" / "c.py")},
        )
        links = await engine.observe_tool_event(edit_c)
        assert links == [("src/a.py", "src/c.py"), ("src/b.py", "src/c.py")]

        # Repeated edits to C within the same session do not double-count miss_count.
        assert await engine.observe_tool_event(edit_c) == []
    finally:
        engine.close()

    # Re-open from disk on the next session and confirm C is surfaced automatically.
    next_engine = SelfHealingEngine(db_path=db_path, workspace_root=repo)
    try:
        boosts = next_engine.get_associated_paths(["src/a.py", "src/b.py"])
        assert "src/c.py" in boosts
        assert boosts["src/c.py"] == pytest.approx(0.6)  # min(1.0, 0.4 + 0.2 * 1)
        assert "src/d.py" not in boosts
        assert "src/e.py" not in boosts

        # Verify SQLite table schema & row contents directly.
        with sqlite3.connect(db_path) as raw_conn:
            rows = raw_conn.execute(
                "SELECT source_path, target_path, miss_count "
                "FROM associative_links ORDER BY source_path"
            ).fetchall()
            assert rows == [
                ("src/a.py", "src/c.py", 1),
                ("src/b.py", "src/c.py", 1),
            ]

        # Second session (Claude Code Read/Edit style) increments miss_count to 2 -> weight 0.8.
        next_engine.record_turn1_hints("sess-2", str(repo), ["src/a.py", "src/b.py"])
        assert (
            await next_engine.observe_tool_event(
                GateEvent(
                    gate=GateType.FIREWALL,
                    session_id="sess-2",
                    turn_index=2,
                    workspace_root=str(repo),
                    tool_name="Read",
                    tool_args={"file_path": "./src/c.py"},
                )
            )
            == []
        )
        links_s2 = await next_engine.observe_tool_event(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="sess-2",
                turn_index=3,
                workspace_root=str(repo),
                tool_name="Edit",
                tool_args={"file_path": "./src/c.py"},
            )
        )
        assert links_s2 == [("src/a.py", "src/c.py"), ("src/b.py", "src/c.py")]
        boosted = next_engine.get_associated_paths(["src/a.py", "src/b.py"])
        assert boosted["src/c.py"] == pytest.approx(0.8)

        # Third miss via direct API saturates weight at 1.0.
        await next_engine.record_context_miss(
            "sess-3",
            ["src/a.py", "src/c.py"],
            "src/c.py",
            workspace_root=str(repo),
        )
        assert next_engine.get_associated_paths(["src/a.py"])["src/c.py"] == pytest.approx(1.0)
    finally:
        next_engine.close()


async def test_loop1_workspace_isolation(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    engine = SelfHealingEngine(db_path=tmp_path / "iso.db")
    try:
        await engine.record_context_miss("s1", ["a.py"], "c.py", workspace_root=str(repo_a))
        assert engine.get_associated_paths(["a.py"], workspace_root=str(repo_a)) == {
            "c.py": pytest.approx(0.6)
        }
        assert engine.get_associated_paths(["a.py"], workspace_root=str(repo_b)) == {}
    finally:
        engine.close()


async def test_loop2_one_strike_promotes_once_reverts_git_then_halts(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo"
    repo.mkdir()
    original_code = (
        'def add(a: int, b: int) -> int:\n'
        '    """Add two integers."""\n'
        '    return a + b\n'
    )
    _init_git_repo(repo, {"app.py": original_code, "README.md": "# App\n"})

    db_path = tmp_path / "healing_git.db"
    engine = SelfHealingEngine(db_path=db_path, workspace_root=repo)
    try:
        assert engine.get_failure_rate("app.py", workspace_root=str(repo)) == 0.0

        # Cheap FLOOR subagent writes broken syntax to app.py.
        (repo / "app.py").write_text("def add(a, b):\n    return (\n", encoding="utf-8")

        strike0_event = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-heal",
            turn_index=1,
            workspace_root=str(repo),
            current_tier=ModelTier.FLOOR,
            strike_count=0,
            touched_files=["app.py"],
        )
        verdict = await engine.handle_gate4_failure(
            strike0_event,
            oracle_error="SyntaxError: '(' was never closed (app.py, line 2)",
        )

        # Strike 0 reverts dirty git hunk, promotes FLOOR -> MID ("flash"), increases R_hist.
        assert verdict.action == VerdictAction.PROMOTE
        assert verdict.gate == GateType.STOP_AUDIT
        assert verdict.target_tier == ModelTier.MID
        assert verdict.updated_model == "flash"
        assert (repo / "app.py").read_text(encoding="utf-8") == original_code
        assert _git(repo, "status", "--porcelain").stdout.strip() == ""

        r_hist_after_1 = engine.get_failure_rate("app.py", workspace_root=str(repo))
        assert 0.0 < r_hist_after_1 < 1.0

        # Promoted MID model fails Gate 4 again on strike 1 -> one-strike circuit breaker halts.
        (repo / "app.py").write_text("def add(a, b):\n    return a + +\n", encoding="utf-8")
        strike1_event = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-heal",
            turn_index=1,
            workspace_root=str(repo),
            current_tier=ModelTier.MID,
            strike_count=1,
            touched_files=["app.py"],
        )
        verdict2 = await engine.handle_gate4_failure(
            strike1_event,
            oracle_error="SyntaxError: invalid syntax (app.py, line 2)",
        )

        assert verdict2.action == VerdictAction.BLOCK
        assert verdict2.gate == GateType.STOP_AUDIT
        assert verdict2.updated_model is None
        assert verdict2.target_tier is None
        assert verdict2.violation_receipt is not None
        assert verdict2.violation_receipt.rule_id == "GATE4_ONE_STRIKE_CIRCUIT_BREAKER"
        assert verdict2.violation_receipt.score == 1.0
        assert verdict2.violation_receipt.stage == "deterministic_2a"
        assert verdict2.violation_receipt.escalate_to_human is True
        assert verdict2.violation_receipt.strike_count >= 2

        r_hist_after_2 = engine.get_failure_rate("app.py", workspace_root=str(repo))
        assert r_hist_after_2 > r_hist_after_1
    finally:
        engine.close()


async def test_loop2_mid_to_frontier_and_db_strike_guard(tmp_path: Path) -> None:
    tiers = TierModelConfig(floor="haiku", mid="sonnet", frontier="opus")
    engine = SelfHealingEngine(db_path=tmp_path / "tiers.db", tiers=tiers)

    try:
        # MID at strike 0 promotes once to FRONTIER / "opus".
        ev_mid = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-mid",
            turn_index=1,
            workspace_root=str(tmp_path),
            current_tier=ModelTier.MID,
            strike_count=0,
            touched_files=["mod.py"],
        )
        v1 = await engine.handle_gate4_failure(ev_mid, oracle_error="TypeCheckError")
        assert v1.action == VerdictAction.PROMOTE
        assert v1.target_tier == ModelTier.FRONTIER
        assert v1.updated_model == "opus"

        # Even if a caller erroneously passes strike_count=0 again, DB strike=1 halts cleanly.
        v2 = await engine.handle_gate4_failure(ev_mid, oracle_error="Still failing")
        assert v2.action == VerdictAction.BLOCK
        assert v2.violation_receipt is not None
        assert v2.violation_receipt.rule_id == "GATE4_ONE_STRIKE_CIRCUIT_BREAKER"
        assert v2.violation_receipt.escalate_to_human is True

        # FRONTIER or None tier at strike 0 cannot promote further -> BLOCK immediately.
        for tier in (ModelTier.FRONTIER, None):
            ev = GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id=f"sess-{tier}",
                turn_index=1,
                workspace_root=str(tmp_path),
                current_tier=tier,
                strike_count=0,
            )
            blocked = await engine.handle_gate4_failure(ev, oracle_error="Oracle fail")
            assert blocked.action == VerdictAction.BLOCK
            assert blocked.violation_receipt is not None
            assert blocked.violation_receipt.escalate_to_human is True
    finally:
        engine.close()


async def test_loop2_git_revert_staged_untracked_and_non_git_unborn_safe(tmp_path: Path) -> None:
    # 1. Committed repo with staged modification + newly created untracked file.
    repo = tmp_path / "committed"
    repo.mkdir()
    _init_git_repo(repo, {"keep.py": "VALUE = 1\n"})
    (repo / "keep.py").write_text("VALUE = BROKEN(\n", encoding="utf-8")
    _git(repo, "add", "keep.py")
    (repo / "untracked_junk.py").write_text("BROKEN = (\n", encoding="utf-8")

    engine = SelfHealingEngine(db_path=tmp_path / "safe.db")
    try:
        ev = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-staged",
            turn_index=1,
            workspace_root=str(repo),
            current_tier=ModelTier.FLOOR,
            strike_count=0,
            touched_files=["keep.py", "untracked_junk.py", "../outside.py"],
        )
        verdict = await engine.handle_gate4_failure(ev, oracle_error="SyntaxError")
        assert verdict.action == VerdictAction.PROMOTE
        assert (repo / "keep.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert not (repo / "untracked_junk.py").exists()

        # 2. Unborn git repo (0 commits) must never throw or delete files.
        unborn = tmp_path / "unborn"
        unborn.mkdir()
        _git(unborn, "init")
        (unborn / "draft.py").write_text("x = 1\n", encoding="utf-8")
        ev_unborn = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-unborn",
            turn_index=1,
            workspace_root=str(unborn),
            current_tier=ModelTier.FLOOR,
            strike_count=0,
            touched_files=["draft.py"],
        )
        v_unborn = await engine.handle_gate4_failure(ev_unborn, oracle_error="Fail")
        assert v_unborn.action == VerdictAction.PROMOTE
        assert (unborn / "draft.py").exists()

        # 3. Non-git directory must never throw or delete files.
        non_git = tmp_path / "plain_dir"
        non_git.mkdir()
        (non_git / "plain.py").write_text("x = 1\n", encoding="utf-8")
        ev_plain = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="sess-plain",
            turn_index=1,
            workspace_root=str(non_git),
            current_tier=ModelTier.FLOOR,
            strike_count=0,
            touched_files=["plain.py"],
        )
        v_plain = await engine.handle_gate4_failure(ev_plain, oracle_error="Fail")
        assert v_plain.action == VerdictAction.PROMOTE
        assert (non_git / "plain.py").exists()

        # 4. record_file_outcome increases R_hist on failures and decreases on passes.
        ws = str(repo)
        r1 = engine.record_file_outcome(["mod.py"], passed=False, workspace_root=ws)["mod.py"]
        r2 = engine.record_file_outcome(["mod.py"], passed=False, workspace_root=ws)["mod.py"]
        r3 = engine.record_file_outcome(["mod.py"], passed=True, workspace_root=ws)["mod.py"]
        assert 0.0 < r1 < r2 < 1.0
        assert r3 < r2
    finally:
        engine.close()


async def test_loop3_grey_zone_logger_filters_and_appends_jsonl(tmp_path: Path) -> None:
    engine = SelfHealingEngine(db_path=tmp_path / "gz.db")
    log_path = tmp_path / ".quadgate" / "logs" / "grey_zone.jsonl"

    base_event = GateEvent(
        gate=GateType.FIREWALL,
        session_id="sess-gz",
        turn_index=2,
        workspace_root=str(tmp_path),
        tool_name="replace_file_content",
        touched_files=["src/auth.py"],
    )

    try:
        # Outside [0.60, 0.84] must be ignored without creating file.
        assert await engine.log_grey_zone_decision(base_event, 0.20, "INV_01") is False
        assert await engine.log_grey_zone_decision(base_event, 0.599, "INV_01") is False
        assert await engine.log_grey_zone_decision(base_event, 0.841, "INV_01") is False
        assert await engine.log_grey_zone_decision(base_event, 0.95, "INV_01") is False
        assert not log_path.exists()

        # Inside [0.60, 0.84] (inclusive boundaries) must append valid JSONL entries.
        assert (
            await engine.log_grey_zone_decision(
                base_event, 0.60, "INV_01", verdict_action="allow"
            )
            is True
        )
        assert (
            await engine.log_grey_zone_decision(
                base_event, 0.73, "INV_02", verdict_action="allow"
            )
            is True
        )
        assert (
            await engine.log_grey_zone_decision(
                base_event, 0.84, "INV_03", verdict_action="block"
            )
            is True
        )

        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        records = [json.loads(line) for line in lines]
        assert [r["score"] for r in records] == [0.6, 0.73, 0.84]
        assert [r["head_id"] for r in records] == ["INV_01", "INV_02", "INV_03"]
        assert [r["verdict_action"] for r in records] == ["allow", "allow", "block"]
        assert all(r["session_id"] == "sess-gz" for r in records)
        assert all(r["touched_files"] == ["src/auth.py"] for r in records)
    finally:
        engine.close()


async def test_telemetry_summary_and_concurrent_access(tmp_path: Path) -> None:
    custom_gz = tmp_path / "custom_gz.jsonl"
    engine = SelfHealingEngine(
        db_path=tmp_path / "concurrent.db",
        workspace_root=tmp_path,
        grey_zone_log_path=custom_gz,
    )
    try:
        ev = GateEvent(
            gate=GateType.FIREWALL,
            session_id="sess-conc",
            turn_index=1,
            workspace_root=str(tmp_path),
            touched_files=["a.py"],
        )

        await asyncio.gather(
            *(
                [
                    engine.record_context_miss(f"s-{i}", ["a.py"], f"t{i % 3}.py")
                    for i in range(10)
                ]
                + [engine.log_grey_zone_decision(ev, 0.70, f"H_{i}") for i in range(10)]
            )
        )
        engine.record_file_outcome(["a.py"], passed=False)
        await engine.handle_gate4_failure(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-s0",
                turn_index=1,
                workspace_root=str(tmp_path),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                touched_files=["a.py"],
            ),
            oracle_error="Fail",
        )
        await engine.handle_gate4_failure(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="sess-s0",
                turn_index=1,
                workspace_root=str(tmp_path),
                current_tier=ModelTier.MID,
                strike_count=1,
                touched_files=["a.py"],
            ),
            oracle_error="Fail",
        )

        summary = engine.get_telemetry_summary()
        assert summary["associative_links_count"] == 3
        assert summary["total_context_misses"] == 10
        assert summary["tracked_files_count"] == 1
        assert summary["total_file_runs"] == 3
        assert summary["total_file_failures"] == 3
        assert summary["sessions_with_strikes"] == 1
        assert summary["circuit_breaker_halts"] == 1
        assert summary["grey_zone_decisions_logged"] == 10
        assert len(custom_gz.read_text(encoding="utf-8").splitlines()) == 10
    finally:
        engine.close()
        engine.close()  # idempotent
