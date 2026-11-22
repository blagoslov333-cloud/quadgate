"""Regression tests for daemon resync deduplication, delta FTS sync, CLI status, and indexer bounds."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from quadgate.cli import _is_pid_alive
from quadgate.cli import main as cli_main
from quadgate.contracts import GateEvent, GateType, HarnessType, IndexedGist
from quadgate.index.ast_gists import _EXCLUDED_DIRS, AstGistIndexer, _read_bytes
from quadgate.kernel.daemon import DaemonConfig, QuadGateDaemon, _workspace_hash_for_path
from quadgate.kernel.gates import load_cartridge


def test_read_bytes_cap_and_excluded_dirs_and_simple_exts(tmp_path: Path) -> None:
    expected_excluded = {
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".cache",
        ".turbo",
        ".vercel",
        ".parcel-cache",
        "out",
        "coverage",
        "vendor",
        ".cargo",
        ".gradle",
        "Pods",
        "DerivedData",
        "bin",
        "obj",
        "site-packages",
        "env",
        "tmp",
        ".idea",
        ".vscode",
        "playwright-report",
        "storybook-static",
    }
    assert expected_excluded.issubset(_EXCLUDED_DIRS)

    for excluded in (".next", ".cache", "out", "coverage", "vendor", "playwright-report"):
        d = tmp_path / excluded
        d.mkdir(parents=True, exist_ok=True)
        (d / "bundle.py").write_text("def should_not_index(): pass\n", encoding="utf-8")

    huge_file = tmp_path / "huge_generated.py"
    prefix = "def fast_header(x: int) -> int:\n    return x\n\n"
    huge_payload = prefix + ("# " + ("x" * 78) + "\n") * 10_000
    huge_file.write_text(huge_payload, encoding="utf-8")
    assert huge_file.stat().st_size > 524_288

    capped = _read_bytes(str(huge_file))
    assert len(capped) == 524_288

    # Also include markdown files with wikilinks to verify precomputed stem map resolution.
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "TargetDoc.md").write_text("# Target Document\n", encoding="utf-8")
    (docs_dir / "Index.md").write_text(
        "# Index\nSee [[TargetDoc]] and [[docs/TargetDoc.md]].\n",
        encoding="utf-8",
    )

    cartridge = load_cartridge("software_engineering")
    indexer = AstGistIndexer(tmp_path, cartridge=cartridge)
    assert indexer._simple_include_exts == frozenset(
        {".py", ".ts", ".tsx", ".rs", ".go", ".md"}
    )

    gists = {g.rel_path: g for g in indexer.index_workspace(incremental=False)}
    assert set(gists.keys()) == {
        "docs/Index.md",
        "docs/TargetDoc.md",
        "huge_generated.py",
    }
    assert any("fast_header" in s for s in gists["huge_generated.py"].symbols)
    assert gists["docs/TargetDoc.md"].fan_in_count == 1
    assert "docs/TargetDoc.md" in gists["docs/Index.md"].imports


def test_incremental_delta_upsert_and_delete_gists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta() -> int:\n    return 2\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def gamma() -> int:\n    return 3\n", encoding="utf-8")

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    indexer = AstGistIndexer(tmp_path, cartridge=daemon.cartridge)

    # 1. Initial sync upserts all 3 files.
    daemon._sync_workspace_index(indexer, None)
    assert len(indexer.last_changed_gists) == 3
    assert indexer.last_removed_paths == []

    # 2. No-op incremental sync has empty delta lists and performs 0 upserts/deletes.
    upsert_batches: list[list[str]] = []
    delete_batches: list[list[str]] = []
    real_upsert = daemon._fts_store.upsert_gists
    real_delete = daemon._fts_store.delete_gists

    def tracked_upsert(gists: list[IndexedGist]) -> int:
        upsert_batches.append([g.rel_path for g in gists])
        return real_upsert(gists)

    def tracked_delete(ws_hash: str, rel_paths: Any) -> int:
        delete_batches.append(list(rel_paths))
        return real_delete(ws_hash, rel_paths)

    monkeypatch.setattr(daemon._fts_store, "upsert_gists", tracked_upsert)
    monkeypatch.setattr(daemon._fts_store, "delete_gists", tracked_delete)

    daemon._sync_workspace_index(indexer, None)
    assert indexer.last_changed_gists == []
    assert indexer.last_removed_paths == []
    assert upsert_batches == []
    assert delete_batches == []

    # 3. Edit only b.py -> only b.py is upserted.
    b_path = tmp_path / "b.py"
    b_path.write_text("def beta_updated() -> int:\n    return 22\n", encoding="utf-8")
    st = os.stat(b_path)
    os.utime(b_path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))

    daemon._sync_workspace_index(indexer, None)
    assert [g.rel_path for g in indexer.last_changed_gists] == ["b.py"]
    assert indexer.last_removed_paths == []
    assert upsert_batches == [["b.py"]]
    assert delete_batches == []

    res_updated = daemon._fts_store.search_candidates("beta_updated", indexer.workspace_hash)
    assert [g.rel_path for g in res_updated.gists] == ["b.py"]

    # 4. Delete c.py -> delete_gists removes c.py from gists, gists_fts, and gists_trigram.
    upsert_batches.clear()
    delete_batches.clear()
    (tmp_path / "c.py").unlink()

    daemon._sync_workspace_index(indexer, None)
    assert indexer.last_changed_gists == []
    assert indexer.last_removed_paths == ["c.py"]
    assert upsert_batches == []
    assert delete_batches == [["c.py"]]

    res_deleted = daemon._fts_store.search_candidates("gamma", indexer.workspace_hash)
    assert res_deleted.gists == []
    res_trigram = daemon._fts_store.search_candidates("amm", indexer.workspace_hash)
    assert res_trigram.gists == []
    daemon._fts_store.close()


async def test_concurrent_turn1_intake_deduplicates_resync_and_keeps_health_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    for idx in range(6):
        (ws / f"mod_{idx}.py").write_text(
            f"def handler_{idx}(val: int) -> int:\n    return val + {idx}\n",
            encoding="utf-8",
        )

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    await daemon.start()
    try:
        # Warm workspace first.
        warm_event = GateEvent(
            gate=GateType.INTAKE,
            session_id="warm-init",
            turn_index=1,
            workspace_root=str(ws),
            harness=HarnessType.GENERIC,
            prompt_text="Update handler_0 in mod_0.py",
        )
        await daemon.handle_evaluate(warm_event)

        _, ws_hash = _workspace_hash_for_path(ws)
        ctx = daemon._workspaces[ws_hash]

        # Modify a file and wrap index_workspace with artificial delay + concurrency counter.
        mod0 = ws / "mod_0.py"
        mod0.write_text(
            "def handler_0_v2(val: int) -> int:\n    return val + 100\n",
            encoding="utf-8",
        )
        st = os.stat(mod0)
        os.utime(mod0, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
        ctx.last_root_mtime_ns = -1  # Force dirty classification

        active_calls = 0
        max_concurrent_calls = 0
        total_index_calls = 0
        counter_lock = threading.Lock()
        real_index_ws = ctx.indexer.index_workspace

        def slow_index_workspace(incremental: bool = True) -> list[IndexedGist]:
            nonlocal active_calls, max_concurrent_calls, total_index_calls
            with counter_lock:
                active_calls += 1
                total_index_calls += 1
                max_concurrent_calls = max(max_concurrent_calls, active_calls)
            try:
                time.sleep(0.18)
                return real_index_ws(incremental=incremental)
            finally:
                with counter_lock:
                    active_calls -= 1

        monkeypatch.setattr(ctx.indexer, "index_workspace", slow_index_workspace)

        base = f"http://127.0.0.1:{daemon.bound_port}"
        headers = {"Authorization": f"Bearer {daemon.auth_token}"}

        async with aiohttp.ClientSession() as session:
            # Warm persistent HTTP connection for /v1/health.
            async with session.get(f"{base}/v1/health", headers=headers) as w_resp:
                assert w_resp.status == 200
                await w_resp.read()

            intake_tasks = [
                asyncio.create_task(
                    daemon.handle_evaluate(
                        GateEvent(
                            gate=GateType.INTAKE,
                            session_id=f"conc-{i}",
                            turn_index=1,
                            workspace_root=str(ws),
                            harness=HarnessType.GENERIC,
                            prompt_text="Check handler_0_v2 in mod_0.py",
                        )
                    )
                )
                for i in range(4)
            ]

            # Wait briefly so the thread-offloaded resync is actively running.
            await asyncio.sleep(0.03)
            t_health0 = time.perf_counter()
            async with session.get(f"{base}/v1/health", headers=headers) as h_resp:
                assert h_resp.status == 200
                await h_resp.json()
            health_ms = (time.perf_counter() - t_health0) * 1000.0
            assert health_ms < 100.0, f"Expected /v1/health < 100ms during resync, got {health_ms:.2f}ms"

            verdicts = await asyncio.gather(*intake_tasks)
            assert len(verdicts) == 4

        assert max_concurrent_calls == 1
        assert total_index_calls == 1

        # Verify periodic resync (>= 30s elapsed, mtime unchanged) schedules background task without blocking.
        ctx.last_synced_monotonic = time.monotonic() - 35.0
        assert daemon._classify_resync_need(ctx, time.monotonic()) == "periodic"

        t_periodic0 = time.perf_counter()
        await daemon.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="periodic-turn1",
                turn_index=1,
                workspace_root=str(ws),
                harness=HarnessType.GENERIC,
                prompt_text="Quick check on handler_1 in mod_1.py",
            )
        )
        periodic_eval_ms = (time.perf_counter() - t_periodic0) * 1000.0
        # Because periodic sync runs in background, Gate 1 does not wait 180ms for slow_index_workspace.
        assert periodic_eval_ms < 120.0
        if ctx.pending_sync_task is not None:
            await ctx.pending_sync_task
        assert total_index_calls == 2
    finally:
        await daemon.stop()


def test_cli_status_degraded_when_pid_alive_and_stopped_when_pid_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home_dir = tmp_path / "qg_home"
    home_dir.mkdir(parents=True)
    state_path = home_dir / "daemon.json"
    token_path = home_dir / "run.token"
    token_path.write_text("dummy-token\n", encoding="utf-8")

    monkeypatch.setenv("QUADGATE_HOME", str(home_dir))
    monkeypatch.delenv("QUADGATE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("QUADGATE_STATE_PATH", raising=False)

    # Spawn a live sleeper subprocess whose PID is alive, pointing daemon.json at an unused closed port.
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _is_pid_alive(sleeper.pid) is True
        state_path.write_text(
            json.dumps(
                {
                    "host": "127.0.0.1",
                    "port": 9,  # discard/unresponsive port
                    "pid": sleeper.pid,
                    "token_path": str(token_path),
                }
            ),
            encoding="utf-8",
        )

        rc_deg = cli_main(["status", "--json"])
        assert rc_deg == 0
        deg_out = json.loads(capsys.readouterr().out)
        assert deg_out["status"] == "degraded"
        assert deg_out["stale_state"] is False
        assert deg_out["pid"] == sleeper.pid
        assert "unresponsive" in deg_out["health_error"] or "timed out" in deg_out["health_error"]

        rc_deg_text = cli_main(["status"])
        assert rc_deg_text == 0
        deg_text = capsys.readouterr().out
        assert f"QuadGate daemon: degraded (pid={sleeper.pid}, /v1/health unresponsive)" in deg_text
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            sleeper.kill()
            sleeper.wait(timeout=3.0)

    assert _is_pid_alive(sleeper.pid) is False

    # Now with dead PID, status must report stopped with stale_state=True.
    rc_stop = cli_main(["status", "--json"])
    assert rc_stop == 0
    stop_out = json.loads(capsys.readouterr().out)
    assert stop_out["status"] == "stopped"
    assert stop_out["stale_state"] is True
