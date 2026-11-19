"""Fresh-memory adversarial red-team audit tests for QuadGate.

Covers:
1. Prefix-cache immutability across Gate 1 (turns 2..50), qg-shim harness turn keys,
   and ColdSessionProxy warm multi-turn sessions.
2. Latency SLAs: shim stdlib-only AST cage, <25ms cold import, TCP_NODELAY + token
   mtime caching, daemon FTS/workspace sync deduplication, and 40 live Pass-0 round-trips
   (median < 15ms, p95 < 25ms).
3. Stage-2A security bypasses: symlink/NTFS-junction escapes & .env targets in paths
   and run_command, pure-Python base64/hex/chr/escape/quote/glob .env exfiltration,
   and live-to-dead daemon kill resilience (fail-open optimization vs fail-closed Stage-2A).
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import copy
import hashlib
import io
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest

import quadgate.shim as shim_mod
from quadgate.contracts import (
    ClassifierRequest,
    GateEvent,
    GateType,
    HarnessType,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.drivers.base import ClassifierDriver, DriverCapabilities
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.daemon import DaemonConfig, QuadGateDaemon
from quadgate.kernel.gates import GateEngine, TierModelConfig, load_cartridge
from quadgate.kernel.proxy import ColdSessionProxy
from quadgate.shim import check_stage2a_deterministic_security, forward_to_daemon, run_shim

REPO_ROOT = Path(__file__).resolve().parent.parent


class _SpyLocalDriver(ClassifierDriver):
    """Deterministic local driver counting invocations for cache-audit proofs."""

    def __init__(self, score_map: dict[str, float] | None = None) -> None:
        self._score_map = score_map or {}
        self.calls: list[ClassifierRequest] = []
        super().__init__()

    @property
    def driver_id(self) -> str:
        return "redteam_spy_local"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=False,
            supports_parallel_heads=True,
            max_candidates_per_pass=128,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        self.calls.append(request)
        scores: dict[str, float] = {}
        for head in request.heads:
            rel_path = str(head.metadata.get("rel_path", ""))
            if head.head_id in self._score_map:
                scores[head.head_id] = self._score_map[head.head_id]
            elif rel_path in self._score_map:
                scores[head.head_id] = self._score_map[rel_path]
            elif head.head_id == "pragmatic:directive":
                scores[head.head_id] = 0.94
            elif head.head_id == "pragmatic:counterfactual":
                scores[head.head_id] = 0.04
            else:
                scores[head.head_id] = 0.08
        return scores

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def _try_create_dir_link(link_path: Path, target_dir: Path) -> bool:
    """Create a real directory symlink or NTFS junction (`mklink /J` on Windows)."""
    try:
        os.symlink(target_dir, link_path, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and os.path.lexists(link_path)
    return False


def _remove_dir_link(link_path: Path) -> None:
    """Safely remove a directory symlink or Windows NTFS junction without touching target."""
    if not os.path.lexists(link_path):
        return
    try:
        if os.path.islink(link_path):
            link_path.unlink()
        else:
            os.rmdir(link_path)
    except OSError:
        pass


def _digest_obj(obj: object) -> str:
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_shim_ast_cage_and_cold_start_under_25ms() -> None:
    shim_path = REPO_ROOT / "quadgate" / "shim.py"
    tree = ast.parse(shim_path.read_text(encoding="utf-8"), filename=str(shim_path))

    allowed_all = {"sys", "json", "os", "re", "urllib", "urllib.request", "socket"}
    allowed_top = {"os", "re", "sys"}

    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                assert alias.name in allowed_top, f"Disallowed top-level import: {alias.name}"
        elif isinstance(stmt, ast.ImportFrom):
            raise AssertionError(f"Disallowed top-level from-import: {stmt.module}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_all, f"Disallowed import in shim.py: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod in allowed_all, f"Disallowed from-import in shim.py: {mod}"

    probe = (
        "import os, re, sys, time; "
        "pre = set(sys.modules); "
        "t0 = time.perf_counter(); "
        "import quadgate.shim as s; "
        "rc = s.main(['--help']); "
        "dt_ms = (time.perf_counter() - t0) * 1000.0; "
        "forbidden = {'base64', 'binascii', 'codecs', 'pathlib', 'json', 'socket', "
        "'pydantic', 'yaml', 'aiohttp', 'tree_sitter', 'typing', 'argparse'}; "
        "loaded = forbidden & (set(sys.modules) - pre); "
        "assert rc == 0 and not loaded, f'rc={rc} loaded={loaded}'; "
        "print(f'{dt_ms:.4f}')"
    )
    # Warm .pyc cache once, then measure 5 cold-interpreter imports.
    subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, check=True)

    cold_samples_ms: list[float] = []
    for _ in range(5):
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        cold_samples_ms.append(float(lines[-1]))

    median_cold_ms = statistics.median(cold_samples_ms)
    assert median_cold_ms < 25.0, (
        f"Cold import + --help took median {median_cold_ms:.2f} ms (samples={cold_samples_ms})"
    )

    # Verify CLI `python -m quadgate.shim --help` exits 0 and runs in <25ms inside runpy.
    cli_proc = subprocess.run(
        [sys.executable, "-m", "quadgate.shim", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "usage: qg-shim" in cli_proc.stdout

    runpy_probe = (
        "import os, re, runpy, sys, time\n"
        "sys.argv = ['quadgate.shim', '--help']\n"
        "t0 = time.perf_counter()\n"
        "try:\n"
        "    runpy.run_module('quadgate.shim', run_name='__main__', alter_sys=True)\n"
        "except SystemExit as exc:\n"
        "    assert exc.code == 0\n"
        "dt_ms = (time.perf_counter() - t0) * 1000.0\n"
        "print(f'{dt_ms:.4f}')\n"
    )
    cli_samples_ms: list[float] = []
    for _ in range(3):
        r_proc = subprocess.run(
            [sys.executable, "-c", runpy_probe],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        r_lines = [ln.strip() for ln in r_proc.stdout.splitlines() if ln.strip()]
        cli_samples_ms.append(float(r_lines[-1]))

    net_cli_overhead_ms = statistics.median(cli_samples_ms)
    assert net_cli_overhead_ms < 25.0, (
        f"CLI -m quadgate.shim --help execution {net_cli_overhead_ms:.2f} ms >= 25.0 ms"
    )


async def test_gate1_prefix_cache_immutability_turns_2_through_50(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "token_guard.py").write_text(
        '"""Validate JWT bearer session signatures."""\n'
        "def verify_jwt_signature(tok: str) -> bool:\n"
        "    return bool(tok)\n",
        encoding="utf-8",
    )

    spec = load_cartridge("software_engineering")
    indexer = AstGistIndexer(tmp_path, cartridge=spec, use_tree_sitter=False)
    fts = SqliteFtsStore(":memory:")
    fts.upsert_gists(indexer.index_workspace())

    spy_driver = _SpyLocalDriver(score_map={"src/token_guard.py": 0.93})
    engine = GateEngine(cartridge=spec, driver=spy_driver, fts_store=fts, indexer=indexer)

    system_msg = {"role": "system", "content": "IMMUTABLE_SYSTEM_PROMPT_V1: Follow strict rules."}
    turn1_prompt = "Update verify_jwt_signature in src/token_guard.py"

    v1 = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="audit-prefix-50",
            turn_index=1,
            workspace_root=str(tmp_path),
            prompt_text=turn1_prompt,
        )
    )
    assert v1.action == VerdictAction.AUGMENT
    assert v1.tail_hints == ["src/token_guard.py"]
    assert len(spy_driver.calls) == 1

    history: list[dict[str, str]] = [
        copy.deepcopy(system_msg),
        {
            "role": "user",
            "content": f"{turn1_prompt}\n[QUADGATE_CONTEXT_HINTS]\n- src/token_guard.py",
        },
        {"role": "assistant", "content": "Inspecting src/token_guard.py."},
    ]
    frozen_prefix_digest = _digest_obj(history[:3])
    frozen_sys_digest = _digest_obj(system_msg)

    non_true_compact_variants: tuple[dict[str, object], ...] = (
        {},
        {"compact_reset": False},
        {"compact_reset": 0},
        {"compact_reset": "true"},
        {"compact_reset": None},
    )

    for turn in range(2, 51):
        meta = dict(non_true_compact_variants[turn % len(non_true_compact_variants)])
        verdict = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="audit-prefix-50",
                turn_index=turn,
                workspace_root=str(tmp_path),
                prompt_text=f"Follow-up refinement on turn {turn} for src/token_guard.py",
                metadata=meta,
            )
        )
        assert verdict.action == VerdictAction.ALLOW
        assert verdict.tail_hints == []
        assert verdict.reason is None
        assert len(spy_driver.calls) == 1
        assert _digest_obj(history[:3]) == frozen_prefix_digest
        assert _digest_obj(history[0]) == frozen_sys_digest

        history.append({"role": "user", "content": f"Turn {turn} prompt"})
        history.append({"role": "assistant", "content": f"Turn {turn} response"})

    v_reset = await engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="audit-prefix-50",
            turn_index=51,
            workspace_root=str(tmp_path),
            prompt_text="Update verify_jwt_signature in src/token_guard.py after compact",
            metadata={"compact_reset": True},
        )
    )
    assert v_reset.action == VerdictAction.AUGMENT
    assert v_reset.tail_hints == ["src/token_guard.py"]
    assert len(spy_driver.calls) == 2
    fts.close()


async def test_shim_and_daemon_multiturn_prefix_preservation_across_harnesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = tmp_path / "ws_multiturn"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "jwt_validator.py").write_text(
        '"""JWT token expiration validator."""\n'
        "def verify_token(token: str) -> bool:\n"
        "    return bool(token)\n",
        encoding="utf-8",
    )

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    await daemon.start()
    try:
        monkeypatch.setenv("QUADGATE_DAEMON_HOST", "127.0.0.1")
        monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(daemon.bound_port))
        monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(daemon.token_path))

        # Turn 1 via Antigravity `invocationNum: 1` + `workspacePaths`
        t1_ag = json.dumps(
            {
                "conversationId": "ag-conv-50",
                "invocationNum": 1,
                "workspacePaths": [str(ws)],
                "lastUserInput": "verify_token in jwt_validator.py",
            }
        )
        assert await asyncio.to_thread(run_shim, "antigravity", "intake", t1_ag) == 0
        out_t1 = json.loads(capsys.readouterr().out)
        assert out_t1["decision"] == "allow"
        assert "jwt_validator.py" in out_t1["additionalContext"]

        # Turns 2..50 across Antigravity (`invocationNum`, `executionNum`, `stepIdx`) & Claude Code
        for turn in range(2, 51):
            if turn % 3 == 0:
                ag_turn_field = {"executionNum": turn}
            elif turn % 3 == 1:
                ag_turn_field = {"stepIdx": turn}
            else:
                ag_turn_field = {"invocationNum": turn}

            ag_payload = json.dumps(
                {
                    "conversationId": "ag-conv-50",
                    **ag_turn_field,
                    "workspacePaths": [str(ws)],
                    "lastUserInput": f"verify_token in jwt_validator.py step {turn}",
                }
            )
            assert await asyncio.to_thread(run_shim, "antigravity", "intake", ag_payload) == 0
            assert json.loads(capsys.readouterr().out) == {}

            if turn <= 12:
                cc_payload = json.dumps(
                    {
                        "session_id": "cc-conv-50",
                        "turn_index": turn,
                        "cwd": str(ws),
                        "prompt": f"verify_token in jwt_validator.py step {turn}",
                    }
                )
                assert await asyncio.to_thread(run_shim, "claude_code", "intake", cc_payload) == 0
                assert capsys.readouterr().out == ""

        # Explicit top-level `compactReset: True` re-arms Gate 1 cleanly on turn 51.
        compact_payload = json.dumps(
            {
                "conversationId": "ag-conv-50",
                "invocationNum": 51,
                "compactReset": True,
                "workspacePaths": [str(ws)],
                "lastUserInput": "verify_token in jwt_validator.py",
            }
        )
        assert await asyncio.to_thread(run_shim, "antigravity", "intake", compact_payload) == 0
        out_compact = json.loads(capsys.readouterr().out)
        assert "jwt_validator.py" in out_compact["additionalContext"]
    finally:
        await daemon.stop()


async def test_cold_session_proxy_warm_immutability_all_multiturn_shapes(tmp_path: Path) -> None:
    ws = tmp_path / "proxy_audit"
    ws.mkdir()
    (ws / "helper.py").write_text(
        "def trim(s: str) -> str:\n    return s.strip()\n",
        encoding="utf-8",
    )

    upstream_received: list[dict[str, Any]] = []

    async def upstream_echo(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        upstream_received.append(copy.deepcopy(payload))
        return 200, {"ok": True, "model": payload.get("model")}

    engine = GateEngine(
        load_cartridge("software_engineering"),
        tiers=TierModelConfig(frontier="frontier-pro", mid="mid-flash", floor="floor-lite"),
    )
    proxy = ColdSessionProxy(
        host="127.0.0.1",
        port=0,
        upstream_handler=upstream_echo,
        gate_engine=engine,
        workspace_root=ws,
    )
    await proxy.start()
    try:
        warm_payloads: list[tuple[dict[str, Any], dict[str, str]]] = [
            # 50-message warm history with system prompt & tools
            (
                {
                    "model": "frontier-pro",
                    "system": "STATIC_SYSTEM_PROMPT_DO_NOT_MUTATE",
                    "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
                    "messages": [
                        {"role": "system", "content": "Developer blueprint"},
                        *[
                            {
                                "role": "user" if idx % 2 == 0 else "assistant",
                                "content": f"Turn message {idx} touching helper.py",
                            }
                            for idx in range(49)
                        ],
                    ],
                },
                {},
            ),
            # Assistant role in 2-message array
            (
                {
                    "model": "frontier-pro",
                    "messages": [
                        {"role": "assistant", "content": "Ready"},
                        {"role": "user", "content": "Add docstring in helper.py"},
                    ],
                },
                {},
            ),
            # Tool role in 2-message array
            (
                {
                    "model": "frontier-pro",
                    "messages": [
                        {"role": "tool", "content": "ok"},
                        {"role": "user", "content": "Format helper.py"},
                    ],
                },
                {},
            ),
            # Multiple user turns (>1 user message)
            (
                {
                    "model": "frontier-pro",
                    "messages": [
                        {"role": "user", "content": "First instruction"},
                        {"role": "user", "content": "Add docstring in helper.py"},
                    ],
                },
                {},
            ),
            # Single message containing tool_result block
            (
                {
                    "model": "frontier-pro",
                    "system": [{"type": "text", "text": "Anthropic cached system block"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "tool_result", "tool_use_id": "t1", "content": "done"},
                                {"type": "text", "text": "Continue helper.py"},
                            ],
                        }
                    ],
                },
                {},
            ),
            # Explicit preserve header on otherwise-cold 1-turn request
            (
                {
                    "model": "frontier-pro",
                    "messages": [{"role": "user", "content": "Add docstring in helper.py"}],
                },
                {"X-QuadGate-Preserve-Model": "true"},
            ),
        ]

        async with aiohttp.ClientSession() as session:
            for idx, (payload, extra_headers) in enumerate(warm_payloads):
                before_snapshot = copy.deepcopy(payload)
                before_digest = _digest_obj(before_snapshot)

                routed = await proxy.route_request_model(payload, headers=extra_headers)
                assert _digest_obj(payload) == before_digest
                assert _digest_obj(routed) == before_digest

                ep = "/v1/messages" if idx % 2 == 0 else "/v1/chat/completions"
                async with session.post(
                    f"{proxy.base_url}{ep}",
                    json=payload,
                    headers=extra_headers,
                ) as resp:
                    assert resp.status == 200
                    assert _digest_obj(upstream_received[-1]) == before_digest

            # Cold 1-turn request routes ONLY `model`, preserving `system` & `messages` untouched
            cold_payload: dict[str, Any] = {
                "model": "frontier-pro",
                "system": "IMMUTABLE_COLD_SYSTEM_PROMPT",
                "messages": [
                    {"role": "developer", "content": "Developer prefix"},
                    {"role": "user", "content": "Add docstring to trim in helper.py"},
                ],
            }
            cold_orig = copy.deepcopy(cold_payload)
            routed_cold = await proxy.route_request_model(cold_payload)
            assert _digest_obj(cold_payload) == _digest_obj(cold_orig)
            assert routed_cold["model"] == "floor-lite"
            assert routed_cold["system"] == cold_orig["system"]
            assert routed_cold["messages"] == cold_orig["messages"]
    finally:
        await proxy.stop()


async def test_shim_tcp_nodelay_token_cache_and_daemon_fts_sync_optimizations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "opt_ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "calc.py").write_text(
        '"""Arithmetic helper."""\ndef add_nums(a: int, b: int) -> int:\n    return a + b\n',
        encoding="utf-8",
    )

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    init_calls = 0
    orig_init = daemon._fts_store.initialize

    def _counted_init() -> None:
        nonlocal init_calls
        init_calls += 1
        orig_init()

    monkeypatch.setattr(daemon._fts_store, "initialize", _counted_init)
    await daemon.start()
    assert daemon._fts_initialized is True
    assert init_calls == 1

    try:
        real_create_conn = socket.create_connection
        observed_nodelay: list[int] = []

        class _SocketProxy:
            def __init__(self, inner: socket.socket) -> None:
                self._inner = inner

            def setsockopt(self, level: int, optname: int, value: int) -> None:
                if level == socket.IPPROTO_TCP and optname == socket.TCP_NODELAY:
                    observed_nodelay.append(value)
                self._inner.setsockopt(level, optname, value)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def __enter__(self) -> _SocketProxy:
                return self

            def __exit__(self, *exc: object) -> None:
                del exc
                self._inner.close()

        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *a, **kw: _SocketProxy(real_create_conn(*a, **kw)),
        )

        shim_mod._TOKEN_CACHE.clear()
        token_opens = 0
        real_open = builtins.open
        target_tok_str = str(daemon.token_path)

        def _counting_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal token_opens
            if str(file) == target_tok_str and (not args or "r" in str(args[0])):
                token_opens += 1
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _counting_open)

        upsert_calls = 0
        orig_upsert = daemon._fts_store.upsert_gists

        def _counting_upsert(gists: Any) -> int:
            nonlocal upsert_calls
            upsert_calls += 1
            return orig_upsert(gists)

        monkeypatch.setattr(daemon._fts_store, "upsert_gists", _counting_upsert)

        ev_dict: dict[str, object] = {
            "gate": "intake",
            "session_id": "opt-1",
            "turn_index": 1,
            "workspace_root": str(ws),
            "harness": "antigravity",
            "prompt_text": "add_nums in calc.py",
        }
        for i in range(10):
            req = {**ev_dict, "session_id": f"opt-{i}"}
            resp = await asyncio.to_thread(
                forward_to_daemon,
                req,
                host="127.0.0.1",
                port=daemon.bound_port,
                token_path=target_tok_str,
            )
            assert resp is not None
            assert resp["action"] in {"augment", "allow"}

        assert len(observed_nodelay) == 10
        assert all(v == 1 for v in observed_nodelay)
        # Token file opened ONLY once on cold cache, then served from (path, mtime_ns, size) cache!
        assert token_opens == 1
        # SqliteFtsStore.initialize() called ONLY once during daemon.start()!
        assert init_calls == 1
        # upsert_gists called ONLY once on initial workspace creation!
        assert upsert_calls == 1

        # Adding a new file in `ws/src` updates directory mtime and triggers immediate re-index!
        (ws / "src" / "extra_mod.py").write_text(
            '"""Extra module."""\ndef extra_helper_fn() -> int:\n    return 42\n',
            encoding="utf-8",
        )
        v_new = await daemon.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="opt-new-file",
                turn_index=1,
                workspace_root=str(ws),
                prompt_text="extra_helper_fn in extra_mod.py",
            )
        )
        assert "src/extra_mod.py" in v_new.tail_hints
        assert upsert_calls == 2
        assert init_calls == 1
    finally:
        await daemon.stop()


async def test_pass0_roundtrip_latency_40_sequential_under_25ms_p95(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "bench_ws"
    (ws / "src" / "auth").mkdir(parents=True)
    for idx in range(12):
        (ws / "src" / f"service_{idx}.py").write_text(
            f'"""Service module {idx}."""\ndef handle_item_{idx}(val: int) -> int:\n'
            f"    return val + {idx}\n",
            encoding="utf-8",
        )
    (ws / "src" / "auth" / "jwt_validator.py").write_text(
        '"""JWT token expiration leeway verifier."""\n'
        "def verify_token(token: str, leeway_sec: int = 5) -> bool:\n"
        "    return bool(token) and leeway_sec >= 0\n",
        encoding="utf-8",
    )

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    await daemon.start()
    monkeypatch.setenv("QUADGATE_DAEMON_HOST", "127.0.0.1")
    monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(daemon.bound_port))
    monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(daemon.token_path))
    monkeypatch.setenv("QUADGATE_SHIM_TIMEOUT_MS", "2000")

    def _call_shim_quiet(raw_payload: str) -> tuple[int, str, float]:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            t0 = time.perf_counter()
            rc = run_shim("antigravity", "intake", raw_payload)
            dt_ms = (time.perf_counter() - t0) * 1000.0
        finally:
            sys.stdout = old_stdout
        return rc, buf.getvalue(), dt_ms

    try:
        warmup_json = json.dumps(
            {
                "conversationId": "warmup",
                "invocationNum": 1,
                "workspaceRoot": str(ws),
                "lastUserInput": "verify_token leeway in jwt_validator.py",
            }
        )
        for _ in range(2):
            w_rc, w_out, _ = await asyncio.to_thread(_call_shim_quiet, warmup_json)
            assert w_rc == 0
            assert "jwt_validator.py" in w_out

        latencies_ms: list[float] = []
        for idx in range(40):
            req_json = json.dumps(
                {
                    "conversationId": f"pass0-bench-{idx}",
                    "invocationNum": 1,
                    "workspaceRoot": str(ws),
                    "lastUserInput": "verify_token leeway in jwt_validator.py",
                }
            )
            rc, out, dt_ms = await asyncio.to_thread(_call_shim_quiet, req_json)
            assert rc == 0
            assert "jwt_validator.py" in out, (
                f"Pass-0 iteration idx={idx} failed open or missed hint "
                f"(dt_ms={dt_ms:.2f}, out={out!r})"
            )
            latencies_ms.append(dt_ms)

        ordered = sorted(latencies_ms)
        median_ms = statistics.median(ordered)
        p95_ms = ordered[int(0.95 * (len(ordered) - 1))]
        assert median_ms < 15.0, (
            f"Pass-0 round-trip median {median_ms:.2f} ms exceeded 15.0 ms (p95={p95_ms:.2f} ms)"
        )
        assert p95_ms < 25.0, (
            f"Pass-0 round-trip p95 {p95_ms:.2f} ms exceeded 25.0 ms (median={median_ms:.2f} ms)"
        )
    finally:
        await daemon.stop()


def test_stage2a_symlink_and_junction_escapes_in_paths_and_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "ws"
    subdir = ws / "subdir"
    outside = tmp_path / "outside_vault"
    env_dir = ws / ".env.secrets"
    subdir.mkdir(parents=True)
    outside.mkdir(parents=True)
    env_dir.mkdir(parents=True)

    (outside / "secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")
    (ws / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
    (env_dir / "vars.txt").write_text("DB_PASS=secret", encoding="utf-8")
    (ws / "safe.py").write_text("x = 1\n", encoding="utf-8")

    ws_str = str(ws)
    shim_mod._WS_REALPATH_CACHE.clear()

    created_dir_links: list[Path] = []
    root_evil_dir = ws / "evil_dir"
    nested_evil_dir = subdir / "evil_dir"
    nested_env_dir = subdir / "env_link_dir"

    try:
        has_root_link = _try_create_dir_link(root_evil_dir, outside)
        if has_root_link:
            created_dir_links.append(root_evil_dir)
        has_nested_link = _try_create_dir_link(nested_evil_dir, outside)
        if has_nested_link:
            created_dir_links.append(nested_evil_dir)
        has_env_dir_link = _try_create_dir_link(nested_env_dir, env_dir)
        if has_env_dir_link:
            created_dir_links.append(nested_env_dir)

        # At least one real OS symlink or Windows NTFS junction must succeed on standard hosts
        assert has_root_link and has_nested_link and has_env_dir_link

        # 1. Real filesystem symlink/junction escapes in `paths` and `run_command`
        escape_vectors: list[dict[str, object]] = [
            {"tool_name": "Read", "tool_input": {"file_path": "evil_dir/secret.txt"}},
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "evil_dir/new_escape.txt", "content": "x"},
            },
            {"tool_name": "Read", "tool_input": {"file_path": "subdir/evil_dir/secret.txt"}},
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "evil_dir/../outside_vault/secret.txt"},
            },
            {"tool_name": "run_command", "tool_args": {"CommandLine": "cat evil_dir"}},
            {"tool_name": "run_command", "tool_args": {"CommandLine": "cat evil_dir/secret.txt"}},
            {
                "tool_name": "run_command",
                "tool_args": {"CommandLine": "cat ./subdir/evil_dir/secret.txt"},
            },
            {
                "tool_name": "run_command",
                "tool_args": {"CommandLine": "cd subdir && cat evil_dir/secret.txt"},
            },
            {
                "tool_name": "run_command",
                "tool_args": {
                    "CommandLine": "python -c \"print(open('evil_dir/secret.txt').read())\""
                },
            },
        ]
        # Warm reparse-point handle once, then verify every escape vector & <2.0ms mean budget
        check_stage2a_deterministic_security(escape_vectors[0], ws_str)
        t0 = time.perf_counter()
        for vec in escape_vectors:
            receipt = check_stage2a_deterministic_security(vec, ws_str)
            assert receipt is not None, f"Expected INV_02 block for {vec}"
            assert ViolationReceipt.model_validate(receipt).rule_id == "INV_02_CAGE_CONTAINMENT"
        mean_escape_ms = ((time.perf_counter() - t0) / len(escape_vectors)) * 1000.0
        assert mean_escape_ms < 2.0, (
            f"Mean symlink/junction Stage-2A latency {mean_escape_ms:.3f} ms >= 2.0 ms"
        )

        # 2. Real directory link resolving to `.env.secrets` -> INV_01_SECRET_EXPOSURE
        env_dir_vectors: list[dict[str, object]] = [
            {"tool_name": "Read", "tool_input": {"file_path": "subdir/env_link_dir/vars.txt"}},
            {
                "tool_name": "run_command",
                "tool_args": {"CommandLine": "cat ./subdir/env_link_dir/vars.txt"},
            },
            {"tool_name": "run_command", "tool_args": {"CommandLine": "cat subdir/env_link_dir"}},
        ]
        for vec in env_dir_vectors:
            receipt = check_stage2a_deterministic_security(vec, ws_str)
            assert receipt is not None, f"Expected INV_01 block for {vec}"
            assert ViolationReceipt.model_validate(receipt).rule_id == "INV_01_SECRET_EXPOSURE"

        # 3. File-level symlink to `.env` & outside file (real `os.symlink` if permitted, plus
        #    deterministic portable symlink mapping so assertions NEVER skip on non-admin Windows!)
        file_env_link = ws / "evil_env_link"
        file_out_link = ws / "evil_file_link"
        created_real_file_sym = False
        try:
            os.symlink(ws / ".env", file_env_link)
            os.symlink(outside / "secret.txt", file_out_link)
            created_real_file_sym = True
        except OSError:
            created_real_file_sym = False

        if not created_real_file_sym:
            real_lexists = os.path.lexists
            real_realpath = os.path.realpath
            env_link_norm = os.path.normcase(os.path.normpath(str(file_env_link)))
            out_link_norm = os.path.normcase(os.path.normpath(str(file_out_link)))
            venv_py = ws / ".venv" / "bin" / "python"
            venv_norm = os.path.normcase(os.path.normpath(str(venv_py)))

            def _sim_lexists(p: Any) -> bool:
                np = os.path.normcase(os.path.normpath(str(p)))
                if np in {env_link_norm, out_link_norm, venv_norm}:
                    return True
                return real_lexists(p)

            def _sim_realpath(p: Any, *args: Any, **kwargs: Any) -> str:
                np = os.path.normcase(os.path.normpath(str(p)))
                if np == env_link_norm:
                    return real_realpath(str(ws / ".env"))
                if np == out_link_norm:
                    return real_realpath(str(outside / "secret.txt"))
                if np == venv_norm:
                    return real_realpath(str(outside / "python3"))
                return real_realpath(p, *args, **kwargs)

            monkeypatch.setattr(os.path, "lexists", _sim_lexists)
            monkeypatch.setattr(os.path, "realpath", _sim_realpath)

        file_env_vectors: list[dict[str, object]] = [
            {"tool_name": "Read", "tool_input": {"file_path": "evil_env_link"}},
            {"tool_name": "run_command", "tool_args": {"CommandLine": "cat evil_env_link"}},
            {"tool_name": "run_command", "tool_args": {"CommandLine": "head -n 5 ./evil_env_link"}},
        ]
        for env_vec in file_env_vectors:
            r_env = check_stage2a_deterministic_security(env_vec, ws_str)
            assert r_env is not None, f"Expected INV_01 for {env_vec}"
            assert r_env["rule_id"] == "INV_01_SECRET_EXPOSURE"

        file_out_vectors: list[dict[str, object]] = [
            {"tool_name": "Read", "tool_input": {"file_path": "evil_file_link"}},
            {"tool_name": "run_command", "tool_args": {"CommandLine": "cat evil_file_link"}},
        ]
        for out_vec in file_out_vectors:
            r_out = check_stage2a_deterministic_security(out_vec, ws_str)
            assert r_out is not None, f"Expected INV_02 for {out_vec}"
            assert r_out["rule_id"] == "INV_02_CAGE_CONTAINMENT"

        # Toolchain pos-0 symlink (.venv/bin/python) and safe in-workspace commands pass cleanly
        assert (
            check_stage2a_deterministic_security(
                {
                    "tool_name": "run_command",
                    "tool_args": {"CommandLine": ".venv/bin/python -m pytest safe.py"},
                },
                ws_str,
            )
            is None
        )
    finally:
        for lnk in (ws / "evil_env_link", ws / "evil_file_link"):
            if os.path.islink(lnk):
                lnk.unlink()
        for d_lnk in reversed(created_dir_links):
            _remove_dir_link(d_lnk)


def test_stage2a_base64_hex_chr_and_outbound_env_exfiltration(tmp_path: Path) -> None:
    ws = tmp_path / "obf_ws"
    ws.mkdir()
    (ws / ".env").write_text("SECRET=1\n", encoding="utf-8")
    ws_str = str(ws)

    malicious_commands = [
        # Base64 / URL-safe / unpadded / split-concat / UTF-16LE PowerShell -EncodedCommand
        "echo LmVudg== | base64 -d | xargs cat",
        "cat $(printf 'LmVudg==' | base64 --decode)",
        "echo LmVudg | base64 -d | xargs cat",
        "echo Li8uZW52Lm92ZXJyaWRl | base64 -d | xargs cat",
        "python -c \"import base64; print(open(base64.b64decode(b'LmVudg==').decode()).read())\"",
        (
            "python -c \"import base64; "
            "print(open(base64.b64decode(b'LmVu' + b'dg==').decode()).read())\""
        ),
        (
            "powershell -c "
            "\"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('LmVudg=='))\""
        ),
        "powershell -EncodedCommand YwBhAHQAIAAuAGUAbgB2AA==",
        # Hex / octal / URL / Unicode escapes & hex decoders
        r"python -c \"print(open('\x2e\x65\x6e\x76').read())\"",
        r"python -c \"print(open('\u002e\u0065\u006e\u0076').read())\"",
        r"cat $(printf '\056\145\156\166')",
        "python -c \"import urllib.parse; open(urllib.parse.unquote('%2e%65%6e%76')).read()\"",
        "python -c \"print(open(bytes.fromhex('2e656e76').decode()).read())\"",
        "python -c \"print(open(bytes.fromhex('2e 65 6e 76').decode()).read())\"",
        "echo 2e656e76 | xxd -r -p | xargs cat",
        # chr() / [char] / fromCharCode / bytes([...])
        "python -c \"print(open(chr(46)+chr(101)+chr(110)+chr(118)).read())\"",
        "python -c \"print(open(chr(0x2e)+chr(0x65)+chr(0x6e)+chr(0x76)).read())\"",
        "powershell -c \"Get-Content ([char]46+[char]101+[char]110+[char]118)\"",
        "node -e \"console.log(fs.readFileSync(String.fromCharCode(46,101,110,118),'utf8'))\"",
        "python -c \"print(open(bytes([46, 101, 110, 118]).decode()).read())\"",
        # Quote-splitting & wildcard globs
        "cat '.'env",
        "cat .e'n'v",
        'cat ".e""nv"',
        "cat .en?",
        "cat .[e]nv",
        # Outbound encoding of .env
        "base64 .env",
        "base64 < .env",
        "certutil -encode .env out.txt",
        "openssl base64 -in .env",
    ]

    # Warm-up compilation
    check_stage2a_deterministic_security(
        {"tool_name": "run_command", "tool_args": {"CommandLine": malicious_commands[0]}},
        ws_str,
    )

    t0 = time.perf_counter()
    for cmd in malicious_commands:
        receipt = check_stage2a_deterministic_security(
            {"tool_name": "run_command", "tool_args": {"CommandLine": cmd}},
            ws_str,
        )
        assert receipt is not None, f"Missed obfuscated .env attack: {cmd}"
        validated = ViolationReceipt.model_validate(receipt)
        assert validated.rule_id == "INV_01_SECRET_EXPOSURE"
        assert validated.stage == "deterministic_2a"
    mean_ms = ((time.perf_counter() - t0) / len(malicious_commands)) * 1000.0
    assert mean_ms < 2.0, f"Mean obfuscation check took {mean_ms:.3f} ms >= 2.0 ms"

    safe_commands = [
        "pytest tests/test_foo.py -v",
        "base64 .env.example",
        "base64 < .env.sample",
        "certutil -encode .env.template out.txt",
        "openssl base64 -in .env.example",
        "python -c \"import base64; print(base64.b64decode(b'LmVudi5leGFtcGxl').decode())\"",
        "python -c \"print(bytes.fromhex('2e656e762e6578616d706c65').decode())\"",
        "python -c \"print(chr(46)+chr(101)+chr(110)+chr(118)+'.example')\"",
        "git show 9f8a2e656e761b2c3d4e5f60718293a4b5c6d7e8",
    ]
    for safe_cmd in safe_commands:
        assert (
            check_stage2a_deterministic_security(
                {"tool_name": "run_command", "tool_args": {"CommandLine": safe_cmd}},
                ws_str,
            )
            is None
        ), f"False positive on safe command: {safe_cmd}"


async def test_daemon_kill_resilience_fail_open_vs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = tmp_path / "kill_ws"
    outside = tmp_path / "kill_outside"
    (ws / "src").mkdir(parents=True)
    outside.mkdir(parents=True)
    (ws / "src" / "auth.py").write_text(
        '"""Auth token helper."""\ndef check_auth_token(tok: str) -> bool:\n    return bool(tok)\n',
        encoding="utf-8",
    )
    (outside / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

    evil_link = ws / "evil_escape_dir"
    has_link = _try_create_dir_link(evil_link, outside)
    assert has_link

    try:
        daemon = QuadGateDaemon(
            DaemonConfig(host="127.0.0.1", port=0),
            home_dir=tmp_path / "qg_home",
        )
        await daemon.start()
        dead_port = daemon.bound_port
        token_path = daemon.token_path
        state_path = daemon.state_path

        monkeypatch.setenv("QUADGATE_DAEMON_HOST", "127.0.0.1")
        monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(dead_port))
        monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(token_path))

        # 1. Verify live request succeeds while daemon is running
        live_in = json.dumps(
            {
                "conversationId": "live-1",
                "invocationNum": 1,
                "workspaceRoot": str(ws),
                "lastUserInput": "check_auth_token in auth.py",
            }
        )
        assert await asyncio.to_thread(run_shim, "antigravity", "intake", live_in) == 0
        live_out = json.loads(capsys.readouterr().out)
        assert "auth.py" in live_out["additionalContext"]

        # 2. Kill / stop daemon and ensure state + token files are removed & socket closed
        await daemon.stop()
        token_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        assert not daemon.is_running
        assert not state_path.exists()

        # 3. Optimization gates (`intake`, `subagent_governor`, `stop_audit`) & safe `firewall`
        #    MUST fail-open (`exit 0`, `< 450 ms`) while daemon is dead!
        fail_open_cases = [
            (
                "intake",
                {"session_id": "dead-1", "cwd": str(ws), "prompt": "check_auth_token in auth.py"},
            ),
            (
                "subagent_governor",
                {
                    "session_id": "dead-2",
                    "cwd": str(ws),
                    "tool_name": "Task",
                    "tool_input": {"prompt": "write unit tests"},
                },
            ),
            (
                "stop_audit",
                {"session_id": "dead-3", "cwd": str(ws), "touched_files": ["src/auth.py"]},
            ),
            (
                "firewall",
                {
                    "session_id": "dead-4",
                    "cwd": str(ws),
                    "tool_name": "Bash",
                    "tool_input": {"command": "pytest src/auth.py -q"},
                },
            ),
        ]
        for gate_name, body in fail_open_cases:
            raw = json.dumps(body)
            t0 = time.perf_counter()
            rc_cc = run_shim("claude_code", gate_name, raw)
            dt_cc_ms = (time.perf_counter() - t0) * 1000.0
            cap_cc = capsys.readouterr()
            assert rc_cc == 0
            assert cap_cc.out == ""
            assert cap_cc.err == ""
            assert dt_cc_ms < 450.0

            t1 = time.perf_counter()
            rc_ag = run_shim("antigravity", gate_name, raw)
            dt_ag_ms = (time.perf_counter() - t1) * 1000.0
            cap_ag = capsys.readouterr()
            assert rc_ag == 0
            assert json.loads(cap_ag.out) == {}
            assert dt_ag_ms < 450.0

        # 4. Dangerous Stage-2A vectors MUST fail-closed (`exit 2` / `decision: "deny"`, `< 5 ms`)
        #    offline without ever attempting TCP connection to the dead daemon!
        fail_closed_vectors = [
            ("cat evil_escape_dir/secret.txt", "INV_02_CAGE_CONTAINMENT"),
            ("echo LmVudg== | base64 -d | xargs cat", "INV_01_SECRET_EXPOSURE"),
            ("python -c \"open(bytes.fromhex('2e656e76')).read()\"", "INV_01_SECRET_EXPOSURE"),
            (
                "python -c \"open(chr(46)+chr(101)+chr(110)+chr(118)).read()\"",
                "INV_01_SECRET_EXPOSURE",
            ),
            ("rm -rf /", "STAGE2A_DESTRUCTIVE_COMMAND"),
            ("git push --force origin main", "STAGE2A_FORCE_PUSH_MAIN"),
        ]
        # Warm Stage-2A regex cache once before <5ms per-call timing assertions
        warm_fw = json.dumps(
            {"cwd": str(ws), "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        )
        run_shim("claude_code", "firewall", warm_fw)
        capsys.readouterr()

        for bad_cmd, expected_rule in fail_closed_vectors:
            cc_raw = json.dumps(
                {
                    "session_id": "dead-sec",
                    "cwd": str(ws),
                    "tool_name": "Bash",
                    "tool_input": {"command": bad_cmd},
                }
            )
            t0 = time.perf_counter()
            rc_cc = run_shim("claude_code", "firewall", cc_raw)
            dt_cc_ms = (time.perf_counter() - t0) * 1000.0
            cap_cc = capsys.readouterr()
            assert rc_cc == 2, f"Expected exit 2 for {bad_cmd}"
            assert expected_rule in cap_cc.err
            assert dt_cc_ms < 5.0, f"Offline Stage-2A block took {dt_cc_ms:.2f} ms >= 5.0 ms"

            ag_raw = json.dumps(
                {
                    "conversationId": "dead-sec",
                    "workspaceRoot": str(ws),
                    "toolCall": {"name": "run_command", "args": {"CommandLine": bad_cmd}},
                }
            )
            t1 = time.perf_counter()
            rc_ag = run_shim("antigravity", "firewall", ag_raw)
            dt_ag_ms = (time.perf_counter() - t1) * 1000.0
            cap_ag = capsys.readouterr()
            assert rc_ag == 0
            ag_json = json.loads(cap_ag.out)
            assert ag_json["decision"] == "deny"
            assert expected_rule in ag_json["reason"]
            assert dt_ag_ms < 5.0, f"Offline Antigravity block took {dt_ag_ms:.2f} ms >= 5.0 ms"
    finally:
        _remove_dir_link(evil_link)
