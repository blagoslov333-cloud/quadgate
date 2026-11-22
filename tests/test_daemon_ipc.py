"""Wave 3 tests for QuadGateDaemon IPC, ColdSessionProxy, and CLI lifecycle commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from quadgate.cli import main as cli_main
from quadgate.contracts import GateEvent, GateType, GateVerdict, HarnessType, VerdictAction
from quadgate.kernel.calibration import CalibrationEngine
from quadgate.kernel.daemon import DaemonConfig, QuadGateDaemon
from quadgate.kernel.gates import GateEngine, TierModelConfig, load_cartridge
from quadgate.kernel.proxy import ColdSessionProxy
from quadgate.shim import run_shim

REPO_ROOT = Path(__file__).resolve().parent.parent


async def test_daemon_health_latency_and_auth_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home_dir = tmp_path / "qg_home"
    monkeypatch.setenv("QUADGATE_HOME", str(home_dir))
    monkeypatch.delenv("QUADGATE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("QUADGATE_STATE_PATH", raising=False)

    daemon = QuadGateDaemon(DaemonConfig(host="127.0.0.1", port=0))
    await daemon.start()
    try:
        assert daemon.bound_port > 0
        assert daemon.token_path == home_dir / "run.token"
        assert daemon.state_path == home_dir / "daemon.json"
        assert daemon.token_path.is_file()
        assert daemon.state_path.is_file()

        state = json.loads(daemon.state_path.read_text(encoding="utf-8"))
        assert state["host"] == "127.0.0.1"
        assert state["port"] == daemon.bound_port
        assert state["proxy_port"] == 8788
        assert isinstance(state["pid"], int) and state["pid"] > 0
        assert isinstance(state["started_at"], str) and state["started_at"]

        base = f"http://127.0.0.1:{daemon.bound_port}"
        async with aiohttp.ClientSession() as session:
            # Warm TCP connection, then measure unauthenticated /v1/health latency.
            async with session.get(f"{base}/v1/health") as warm:
                assert warm.status == 200

            t0 = time.perf_counter()
            async with session.get(f"{base}/v1/health") as resp:
                assert resp.status == 200
                data = await resp.json()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            assert latency_ms < 10.0
            assert data["status"] == "ok"
            assert data["version"] == "0.1.2"
            assert data["port"] == daemon.bound_port
            assert data["active_driver"] == "zero_dep"
            assert data["active_cartridge"] == "software_engineering"
            assert data["uptime_s"] >= 0.0

            # Valid Bearer token on /v1/health succeeds; invalid Bearer token returns 401.
            async with session.get(
                f"{base}/v1/health",
                headers={"Authorization": f"Bearer {daemon.auth_token}"},
            ) as auth_ok:
                assert auth_ok.status == 200

            async with session.get(
                f"{base}/v1/health",
                headers={"Authorization": "Bearer wrong-token"},
            ) as auth_bad:
                assert auth_bad.status == 401
    finally:
        await daemon.stop()

    assert not daemon.state_path.exists()
    assert not daemon.is_running


async def test_daemon_bearer_token_rejection_and_shutdown(tmp_path: Path) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "app.py").write_text("def hello() -> str:\n    return 'hi'\n", encoding="utf-8")

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    await daemon.start()
    try:
        base = f"http://127.0.0.1:{daemon.bound_port}"
        event_payload = GateEvent(
            gate=GateType.INTAKE,
            session_id="auth-test-1",
            turn_index=1,
            workspace_root=str(ws),
            harness=HarnessType.GENERIC,
            prompt_text="Update hello in app.py",
        ).model_dump(mode="json")

        async with aiohttp.ClientSession() as session:
            # Missing and wrong tokens rejected with 401 across protected endpoints.
            for headers in ({}, {"Authorization": "Bearer invalid-secret"}):
                async with session.post(f"{base}/v1/evaluate", json=event_payload, headers=headers) as r1:
                    assert r1.status == 401
                async with session.get(f"{base}/v1/stats", headers=headers) as r2:
                    assert r2.status == 401
                async with session.post(f"{base}/v1/shutdown", headers=headers) as r3:
                    assert r3.status == 401

            valid_headers = {"Authorization": f"Bearer {daemon.auth_token}"}
            async with session.post(
                f"{base}/v1/evaluate",
                json=event_payload,
                headers=valid_headers,
            ) as eval_resp:
                assert eval_resp.status == 200
                verdict = GateVerdict.model_validate(await eval_resp.json())
                assert verdict.gate == GateType.INTAKE

            async with session.get(f"{base}/v1/stats", headers=valid_headers) as stats_resp:
                assert stats_resp.status == 200
                stats = await stats_resp.json()
                assert stats["total_requests"] == 1
                assert stats["mean_latency_ms"] >= 0.0
                assert stats["p50_latency_ms"] >= 0.0
                assert "verdict_breakdown" in stats
                assert "self_healing" in stats

            async with session.post(f"{base}/v1/shutdown", headers=valid_headers) as shut_resp:
                assert shut_resp.status == 200
                assert (await shut_resp.json())["status"] == "shutting_down"

        await asyncio.wait_for(daemon.wait_until_stopped(), timeout=2.0)
        assert not daemon.state_path.exists()
    finally:
        await daemon.stop()


async def test_shim_end_to_end_over_real_tcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = tmp_path / "repo"
    (ws / "src" / "auth").mkdir(parents=True)
    (ws / "src" / "billing").mkdir(parents=True)
    (ws / "src" / "auth" / "jwt_validator.py").write_text(
        '"""JWT token verification and expiration leeway checks."""\n\n'
        "def verify_token(token: str, leeway_sec: int = 5) -> bool:\n"
        "    return bool(token) and leeway_sec >= 0\n",
        encoding="utf-8",
    )
    (ws / "src" / "billing" / "stripe_webhook.py").write_text(
        '"""Stripe billing webhook handler."""\n\n'
        "def handle_invoice_paid(payload: dict) -> None:\n"
        "    pass\n",
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

        intake_stdin = json.dumps(
            {
                "conversationId": "shim-e2e-1",
                "turnIndex": 1,
                "workspaceRoot": str(ws),
                "lastUserInput": "Fix JWT token expiration leeway bug in verify_token inside jwt_validator.py",
            }
        )
        rc = await asyncio.to_thread(run_shim, "antigravity", "intake", intake_stdin)
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["decision"] == "allow"
        assert "jwt_validator.py" in out.get("additionalContext", "")

        # Clean write passes Gate 2 over real TCP.
        clean_fw = json.dumps(
            {
                "session_id": "shim-e2e-1",
                "cwd": str(ws),
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(ws / "src" / "auth" / "jwt_validator.py"),
                    "content": "def verify_token(token: str, leeway_sec: int = 10) -> bool:\n    return bool(token)\n",
                },
            }
        )
        rc_fw = await asyncio.to_thread(run_shim, "claude_code", "firewall", clean_fw)
        assert rc_fw == 0
        capsys.readouterr()

        # Assigned file-cage violation triggers Gate 2 block over real TCP (exit code 2).
        cage_fw = json.dumps(
            {
                "session_id": "shim-e2e-1",
                "cwd": str(ws),
                "assigned_file_cage": ["src/auth/jwt_validator.py"],
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(ws / "src" / "billing" / "stripe_webhook.py"),
                    "content": "def handle_invoice_paid(payload: dict) -> None:\n    pass\n",
                },
            }
        )
        rc_cage = await asyncio.to_thread(run_shim, "claude_code", "firewall", cage_fw)
        assert rc_cage == 2
        captured_cage = capsys.readouterr()
        assert "INV_02_CAGE_CONTAINMENT" in captured_cage.err

        # Verify daemon recorded all three shim IPC requests.
        stats: dict[str, Any] = await daemon.stats()
        assert stats["total_requests"] == 3
        gate_breakdown: dict[str, int] = stats["gate_breakdown"]
        assert gate_breakdown["intake"] == 1
        assert gate_breakdown["firewall"] == 2
    finally:
        await daemon.stop()


async def test_daemon_workspace_multiplexing(tmp_path: Path) -> None:
    ws_alpha = tmp_path / "ws_alpha"
    ws_beta = tmp_path / "ws_beta"
    ws_alpha.mkdir()
    ws_beta.mkdir()

    (ws_alpha / "alpha_auth.py").write_text(
        '"""Alpha authentication module."""\n\ndef alpha_login_token(user_id: str) -> str:\n    return user_id\n',
        encoding="utf-8",
    )
    (ws_beta / "beta_ledger.py").write_text(
        '"""Beta ledger reconciliation module."""\n\ndef beta_invoice_balance(amount: int) -> int:\n    return amount\n',
        encoding="utf-8",
    )

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
    )
    await daemon.start()
    try:
        v_alpha = await daemon.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="ws-a-1",
                turn_index=1,
                workspace_root=str(ws_alpha),
                prompt_text="Fix alpha_login_token in alpha_auth.py",
            )
        )
        v_beta = await daemon.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="ws-b-1",
                turn_index=1,
                workspace_root=str(ws_beta),
                prompt_text="Update beta_invoice_balance in beta_ledger.py",
            )
        )

        assert any("alpha_auth.py" in h for h in v_alpha.tail_hints)
        assert any("beta_ledger.py" in h for h in v_beta.tail_hints)
        for hint in v_alpha.tail_hints:
            assert "beta_ledger" not in hint
        for hint in v_beta.tail_hints:
            assert "alpha_auth" not in hint

        stats: dict[str, Any] = await daemon.stats()
        assert stats["workspaces_active"] == 2
        assert stats["total_requests"] == 2
        healing_summary: dict[str, Any] = stats["self_healing"]
        assert len(healing_summary["workspaces"]) == 2
    finally:
        await daemon.stop()


async def test_daemon_idle_auto_shutdown(tmp_path: Path) -> None:
    ws = tmp_path / "idle_ws"
    ws.mkdir()
    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=tmp_path / "qg_home",
        idle_timeout_seconds=0.25,
    )
    await daemon.start()
    try:
        await asyncio.sleep(0.12)
        assert daemon.is_running
        # Activity resets idle countdown.
        await daemon.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="idle-keepalive",
                turn_index=2,
                workspace_root=str(ws),
                prompt_text="Keep alive",
            )
        )
        await asyncio.sleep(0.14)
        assert daemon.is_running

        await asyncio.wait_for(daemon.wait_until_stopped(), timeout=2.5)
        assert not daemon.is_running
        assert not daemon.state_path.exists()
    finally:
        await daemon.stop()


async def test_cold_session_proxy_warm_immutability_and_cold_routing(tmp_path: Path) -> None:
    ws = tmp_path / "proxy_ws"
    ws.mkdir()
    (ws / "leaf.py").write_text("def format_row(x: str) -> str:\n    return x.strip()\n", encoding="utf-8")

    captured_requests: list[tuple[str, dict[str, Any]]] = []

    async def fake_upstream(
        method: str,
        path_qs: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        del method, headers
        captured_requests.append((path_qs, payload))
        return 200, {"id": "mock-resp", "echo_model": payload.get("model"), "messages": payload.get("messages")}

    cartridge = load_cartridge("software_engineering")
    engine = GateEngine(
        cartridge,
        tiers=TierModelConfig(frontier="frontier-opus", mid="mid-sonnet", floor="floor-haiku"),
    )
    proxy = ColdSessionProxy(
        host="127.0.0.1",
        port=0,
        upstream_handler=fake_upstream,
        gate_engine=engine,
        workspace_root=ws,
    )
    await proxy.start()
    try:
        # 1. Warm-Session Immutability across len == 3, 4, 10, assistant/tool messages, and >1 user messages.
        warm_cases: list[dict[str, Any]] = [
            {
                "model": "claude-opus-4-6",
                "messages": [
                    {"role": "system", "content": "System instructions"},
                    {"role": "user", "content": "Turn 1"},
                    {"role": "assistant", "content": "Turn 1 reply"},
                ],
            },
            {
                "model": "gpt-5-pro",
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "Turn 1"},
                    {"role": "assistant", "content": "Reply 1"},
                    {"role": "user", "content": "Turn 2"},
                ],
            },
            {
                "model": "claude-opus-4-6",
                "messages": [
                    {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
                    for i in range(10)
                ],
            },
            {
                "model": "claude-opus-4-6",
                "messages": [
                    {"role": "assistant", "content": "Prior assistant output"},
                    {"role": "user", "content": "Continue"},
                ],
            },
            {
                "model": "gpt-5-pro",
                "messages": [
                    {"role": "tool", "content": "tool output"},
                    {"role": "user", "content": "Use tool output"},
                ],
            },
            {
                "model": "gpt-5-pro",
                "messages": [
                    {"role": "user", "content": "First user msg"},
                    {"role": "user", "content": "Second user msg"},
                ],
            },
        ]

        async with aiohttp.ClientSession() as session:
            for idx, warm_payload in enumerate(warm_cases):
                direct = await proxy.route_request_model(warm_payload)
                assert direct["model"] == warm_payload["model"]
                assert direct["messages"] == warm_payload["messages"]

                endpoint = "/v1/messages" if idx % 2 == 0 else "/v1/chat/completions"
                async with session.post(f"{proxy.base_url}{endpoint}", json=warm_payload) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["echo_model"] == warm_payload["model"]
                    assert body["messages"] == warm_payload["messages"]

            # 2. Cold 1-turn [user] and [system, user] requests route model via Gate 3.
            cold_user_only: dict[str, Any] = {
                "model": "frontier-opus",
                "messages": [{"role": "user", "content": "Format markdown table in leaf.py"}],
            }
            async with session.post(f"{proxy.base_url}/chat/completions", json=cold_user_only) as r_cold1:
                assert r_cold1.status == 200
                b1 = await r_cold1.json()
                assert b1["echo_model"] in {"floor-haiku", "mid-sonnet", "frontier-opus"}
                assert b1["echo_model"] == "floor-haiku"
                assert b1["messages"] == cold_user_only["messages"]

            cold_sys_user: dict[str, Any] = {
                "model": "frontier-opus",
                "messages": [
                    {"role": "system", "content": "You are a concise coding worker."},
                    {"role": "user", "content": "Add docstring to format_row in leaf.py"},
                ],
            }
            async with session.post(f"{proxy.base_url}/messages", json=cold_sys_user) as r_cold2:
                assert r_cold2.status == 200
                b2 = await r_cold2.json()
                assert b2["echo_model"] in {"floor-haiku", "mid-sonnet", "frontier-opus"}
                assert b2["echo_model"] == "floor-haiku"
                assert b2["messages"] == cold_sys_user["messages"]

        assert len(captured_requests) == len(warm_cases) + 2
        assert proxy.cold_routed_requests >= 2
        assert proxy.warm_bypassed_requests >= len(warm_cases)
    finally:
        await proxy.stop()


async def test_cold_session_proxy_live_http_upstream_forwarding(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    async def upstream_endpoint(request: web.Request) -> web.Response:
        data = await request.json()
        seen.append({"path": request.path, "payload": data})
        return web.json_response({"ok": True, "received_model": data.get("model")}, status=200)

    upstream_app = web.Application()
    upstream_app.router.add_post("/v1/chat/completions", upstream_endpoint)
    upstream_runner = web.AppRunner(upstream_app, access_log=None)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = int(upstream_runner.addresses[0][1])

    proxy = ColdSessionProxy(
        host="127.0.0.1",
        port=0,
        upstream_base_url=f"http://127.0.0.1:{upstream_port}/v1",
        workspace_root=tmp_path,
    )
    await proxy.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{proxy.base_url}/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [{"role": "user", "content": "Sort imports in helper.py"}],
                },
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body["ok"] is True
                assert body["received_model"] in {"flash_lite", "flash", "pro"}
        assert len(seen) == 1
        assert seen[0]["path"] == "/v1/chat/completions"
    finally:
        await proxy.stop()
        await upstream_runner.cleanup()


async def test_cli_init_local_calibrate_stats_and_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home_dir = tmp_path / "qg_home"
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "service.py").write_text(
        '"""Service layer helper."""\n\ndef compute_total(a: int, b: int) -> int:\n    return a + b\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("QUADGATE_HOME", str(home_dir))
    monkeypatch.delenv("QUADGATE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("QUADGATE_STATE_PATH", raising=False)

    # Serve fake ONNX model + tokenizer assets over local loopback HTTP for `init --local`.
    async def serve_asset(request: web.Request) -> web.Response:
        return web.Response(body=f"mock-asset:{request.path}".encode("utf-8"), status=200)

    asset_app = web.Application()
    asset_app.router.add_get("/model.onnx", serve_asset)
    asset_app.router.add_get("/tokenizer.json", serve_asset)
    asset_runner = web.AppRunner(asset_app, access_log=None)
    await asset_runner.setup()
    asset_site = web.TCPSite(asset_runner, "127.0.0.1", 0)
    await asset_site.start()
    asset_port = int(asset_runner.addresses[0][1])

    try:
        monkeypatch.setenv("QUADGATE_ONNX_MODEL_URL", f"http://127.0.0.1:{asset_port}/model.onnx")
        monkeypatch.setenv(
            "QUADGATE_ONNX_TOKENIZER_URL",
            f"http://127.0.0.1:{asset_port}/tokenizer.json",
        )
        rc_init = await asyncio.to_thread(
            cli_main,
            ["init", "--workspace", str(ws), "--local", "--json"],
        )
        assert rc_init == 0
        init_out = json.loads(capsys.readouterr().out)
        assert init_out["indexed_files"] >= 1
        assert (ws / "quadgate.toml").is_file()
        assert (ws / ".quadgate" / "logs").is_dir()
        assert (ws / ".quadgate" / "pass0.db").is_file()
        assert (home_dir / "models" / "modernbert-int8.onnx").is_file()
        assert (home_dir / "models" / "tokenizer.json").is_file()
        # Remove dummy non-protobuf mock assets after verifying `init --local` download
        # so offline calibration below exercises LocalOnnxDriver's zero_dep fallback cleanly
        # even when `onnxruntime` and `tokenizers` (`[local]`) are installed.
        (home_dir / "models" / "modernbert-int8.onnx").unlink()
        (home_dir / "models" / "tokenizer.json").unlink()
    finally:
        await asset_runner.cleanup()

    # `quadgate calibrate` works offline against calibration_gold.jsonl.
    gold_path = REPO_ROOT / "benchmarks" / "calibration_gold.jsonl"
    rc_cal = await asyncio.to_thread(
        cli_main,
        ["calibrate", "--driver", "onnx_local", "--dataset", str(gold_path), "--json"],
    )
    assert rc_cal == 0
    cal_out = json.loads(capsys.readouterr().out)
    assert cal_out["driver_id"] == "onnx_local"
    assert cal_out["sample_count"] >= 6
    saved_profile = CalibrationEngine(profile_dir=home_dir / "calibration").load_profile("onnx_local")
    assert saved_profile is not None

    # `quadgate stats` when daemon is offline returns offline summary cleanly.
    rc_stats = await asyncio.to_thread(cli_main, ["stats", "--json"])
    assert rc_stats == 0
    stats_out = json.loads(capsys.readouterr().out)
    assert stats_out["status"] == "offline"

    # `quadgate doctor` boots local daemon on 127.0.0.1, checks <10ms health, evaluates, and shuts down.
    rc_doc = await asyncio.to_thread(cli_main, ["doctor", "--json"])
    assert rc_doc == 0
    doc_out = json.loads(capsys.readouterr().out)
    assert doc_out["status"] == "ok"
    assert doc_out["host"] == "127.0.0.1"
    assert doc_out["health_latency_ms"] < 10.0
    assert doc_out["health_under_10ms"] is True
    assert doc_out["clean_shutdown"] is True


def test_cli_daemon_start_status_stop_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home_dir = tmp_path / "qg_home"
    monkeypatch.setenv("QUADGATE_HOME", str(home_dir))
    monkeypatch.delenv("QUADGATE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("QUADGATE_STATE_PATH", raising=False)

    try:
        rc_start = cli_main(["daemon", "start", "--port", "0", "--json"])
        assert rc_start == 0
        start_out = json.loads(capsys.readouterr().out)
        assert start_out["status"] == "started"
        assert start_out["port"] > 0
        assert (home_dir / "daemon.json").is_file()
        assert (home_dir / "run.token").is_file()

        rc_dstat = cli_main(["daemon", "status", "--json"])
        assert rc_dstat == 0
        dstat_out = json.loads(capsys.readouterr().out)
        assert dstat_out["status"] == "running"
        assert dstat_out["port"] == start_out["port"]

        rc_top_stat = cli_main(["status", "--json"])
        assert rc_top_stat == 0
        top_stat_out = json.loads(capsys.readouterr().out)
        assert top_stat_out["status"] == "running"

        rc_live_stats = cli_main(["stats", "--json"])
        assert rc_live_stats == 0
        live_stats_out = json.loads(capsys.readouterr().out)
        assert live_stats_out["status"] == "ok"
        assert live_stats_out["total_requests"] == 0

        rc_stop = cli_main(["daemon", "stop", "--json"])
        assert rc_stop == 0
        stop_out = json.loads(capsys.readouterr().out)
        assert stop_out["status"] == "stopped"
        assert stop_out["was_running"] is True
        assert not (home_dir / "daemon.json").exists()

        rc_after = cli_main(["daemon", "status", "--json"])
        assert rc_after == 0
        after_out = json.loads(capsys.readouterr().out)
        assert after_out["status"] == "stopped"
    finally:
        if (home_dir / "daemon.json").exists():
            cli_main(["daemon", "stop"])
