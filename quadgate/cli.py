"""QuadGate command-line entrypoint (`quadgate`)."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiohttp

from quadgate.adapters.antigravity import AntigravityAdapter
from quadgate.adapters.base import HarnessAdapter
from quadgate.adapters.claude_code import ClaudeCodeAdapter
from quadgate.adapters.codex_cli import CodexCliAdapter
from quadgate.adapters.mcp_bridge import McpBridgeAdapter
from quadgate.contracts import GateEvent, GateType, GateVerdict, HarnessType
from quadgate.drivers.base import CalibrationMethod, ClassifierDriver
from quadgate.drivers.decision1 import Decision1Driver
from quadgate.drivers.jev import JevDriver
from quadgate.drivers.layaa import LayaaDriver
from quadgate.drivers.logprob_fallback import LogprobFallbackDriver
from quadgate.drivers.onnx_local import LocalOnnxDriver, ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.calibration import CalibrationEngine
from quadgate.kernel.daemon import (
    DaemonConfig,
    QuadGateDaemon,
    load_cartridge_spec,
    load_toml_config,
    resolve_quadgate_home,
    resolve_state_path,
    resolve_token_path,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_TOML_TEMPLATE = """[daemon]
host = "127.0.0.1"
port = 8787
proxy_port = 8788
shim_timeout_ms = 450
idle_shutdown_minutes = 30

[classifier]
active_driver = "onnx_local"
fallback_driver = "zero_dep"
max_calibration_ece = 0.05

[classifier.decision1]
endpoint = "https://api.decision1.ai/v1/score"
api_key_env = "DECISION1_API_KEY"
max_heads = 1000

[classifier.jev]
endpoint = "http://127.0.0.1:9091/v1/classify"
api_key_env = "JEV_API_KEY"
max_heads = 512

[classifier.layaa]
model_path = "~/.quadgate/models/layaa-int8.onnx"
device = "cpu"
max_candidates_per_pass = 32

[cartridge]
active = "software_engineering"

[thresholds]
gate1_context_hint_min = 0.80
gate1_actionability_min = 0.30
gate2_invariant_block = 0.78
gate3_floor_confidence = 0.82
gate3_mid_confidence = 0.60
gate4_completeness_min = 0.85
grey_zone_low = 0.60
grey_zone_high = 0.84

[tiers.models]
frontier = "pro"
mid = "flash"
floor = "flash_lite"
"""


def _connect_host(host: str | None) -> str:
    """Resolve explicit IPv4 loopback address to prevent Windows IPv6 DNS latency."""
    if not host or host in {"localhost", "0.0.0.0", "::", "::1"}:
        return "127.0.0.1"
    return host


def _read_token(token_path: Path) -> str:
    try:
        return token_path.read_text(encoding="utf-8").strip().replace("\r", "").replace("\n", "")
    except OSError:
        return ""


def _read_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _http_json_request(
    method: str,
    host: str,
    port: int,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 2.0,
) -> tuple[int, dict[str, Any] | None]:
    """Perform a synchronous loopback HTTP/1.1 JSON call without DNS resolution."""
    conn_host = _connect_host(host)
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body_bytes: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    conn = http.client.HTTPConnection(conn_host, int(port), timeout=timeout_s)
    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        parsed: dict[str, Any] | None = None
        if raw.strip():
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    parsed = loaded
            except json.JSONDecodeError:
                parsed = None
        return int(resp.status), parsed
    except OSError:
        return 0, None
    finally:
        conn.close()


def _probe_running_daemon(
    state_path: Path,
    token_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return `(state_dict, health_dict)` if a live daemon responds on `/v1/health`."""
    state = _read_state(state_path)
    if state is None:
        return None, None
    host = _connect_host(str(state.get("host", "127.0.0.1")))
    try:
        port = int(state["port"])
    except (KeyError, TypeError, ValueError):
        return state, None
    tok_file = Path(str(state.get("token_path") or token_path))
    token = _read_token(tok_file)
    status, health = _http_json_request(
        "GET",
        host,
        port,
        "/v1/health",
        token=token or None,
        timeout_s=1.0,
    )
    if status == 200 and health is not None:
        return state, health
    return state, None


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _render_output(data: dict[str, Any], lines: list[str], *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        for line in lines:
            sys.stdout.write(line + "\n")


def _default_quadgate_toml_text(cartridge_name: str | None = None) -> str:
    example_path = _REPO_ROOT / "quadgate.example.toml"
    base_text = (
        example_path.read_text(encoding="utf-8")
        if example_path.is_file()
        else _DEFAULT_TOML_TEMPLATE
    )
    if cartridge_name and cartridge_name != "software_engineering":
        base_text = base_text.replace(
            'active = "software_engineering"',
            f'active = "{cartridge_name}"',
            1,
        )
    return base_text


def _get_harness_adapter(harness_name: str) -> HarnessAdapter:
    """Instantiate the HarnessAdapter matching a CLI `--harness` selection."""
    normalized = harness_name.strip().lower().replace("_", "-")
    if normalized == "antigravity":
        return AntigravityAdapter()
    if normalized == "claude-code":
        return ClaudeCodeAdapter()
    if normalized in {"codex", "codex-cli"}:
        return CodexCliAdapter()
    if normalized == "mcp":
        return McpBridgeAdapter()
    raise ValueError(f"Unsupported harness: {harness_name!r}")


async def _cmd_init(ns: argparse.Namespace) -> int:
    workspace = Path(ns.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    toml_path = workspace / "quadgate.toml"
    created_toml = False
    if not toml_path.is_file() or getattr(ns, "force", False):
        toml_path.write_text(
            _default_quadgate_toml_text(getattr(ns, "cartridge", None)),
            encoding="utf-8",
        )
        created_toml = True

    logs_dir = workspace / ".quadgate" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    toml_data = load_toml_config(toml_path)
    cart_section = toml_data.get("cartridge", {})
    cart_name = (
        str(cart_section.get("active", "software_engineering"))
        if isinstance(cart_section, dict)
        else "software_engineering"
    )
    cartridge = load_cartridge_spec(cart_name)

    indexer = AstGistIndexer(workspace, cartridge=cartridge)
    gists = indexer.index_workspace(incremental=False)
    fts_db = workspace / ".quadgate" / "pass0.db"
    store = SqliteFtsStore(fts_db)
    try:
        store.initialize()
        upserted = store.upsert_gists(gists)
    finally:
        store.close()

    adapter = _get_harness_adapter(str(ns.harness))
    hooks_path = adapter.install_hooks(workspace)

    downloaded_model: str | None = None
    if ns.local:
        model_path = resolve_quadgate_home() / "models" / "modernbert-int8.onnx"
        onnx_driver = LocalOnnxDriver(model_path=str(model_path))
        try:
            dest = await onnx_driver.ensure_model_downloaded()
            downloaded_model = str(dest)
        finally:
            await onnx_driver.close()

    summary: dict[str, Any] = {
        "status": "ok",
        "workspace": str(workspace),
        "harness": ns.harness,
        "cartridge": cartridge.name,
        "config_path": str(toml_path),
        "config_created": created_toml,
        "hooks_path": str(hooks_path),
        "logs_dir": str(logs_dir),
        "fts_db_path": str(fts_db),
        "indexed_files": len(gists),
        "upserted_rows": upserted,
        "local_model_path": downloaded_model,
    }
    lines = [
        f"Initialized QuadGate workspace: {workspace}",
        f"  config: {toml_path} ({'created' if created_toml else 'existing'})",
        f"  hooks:  {hooks_path}",
        f"  logs:   {logs_dir}",
        f"  index:  {fts_db} ({len(gists)} files indexed)",
        f"  harness:{ns.harness} | cartridge: {cartridge.name}",
    ]
    if downloaded_model:
        lines.append(f"  local:  {downloaded_model}")
    _render_output(summary, lines, as_json=bool(getattr(ns, "json", False)))
    return 0


async def _run_daemon_foreground(ns: argparse.Namespace) -> int:
    config_path = Path(ns.config)
    daemon = QuadGateDaemon.from_toml(
        config_path if config_path.is_file() else None,
        host=getattr(ns, "host", None),
        port=getattr(ns, "port", None),
        idle_shutdown_minutes=getattr(ns, "idle_minutes", None),
    )
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))
            except (NotImplementedError, RuntimeError):
                pass

    await daemon.start()
    try:
        await daemon.wait_until_stopped()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await daemon.stop()
    return 0


def _cmd_daemon_start(ns: argparse.Namespace) -> int:
    if bool(getattr(ns, "foreground", False)):
        try:
            return asyncio.run(_run_daemon_foreground(ns))
        except KeyboardInterrupt:
            return 0

    home_dir = resolve_quadgate_home()
    state_path = resolve_state_path()
    token_path = resolve_token_path()
    as_json = bool(getattr(ns, "json", False))

    existing_state, existing_health = _probe_running_daemon(state_path, token_path)
    if existing_state is not None and existing_health is not None:
        payload = {
            "status": "already_running",
            "host": existing_state.get("host", "127.0.0.1"),
            "port": existing_state.get("port"),
            "pid": existing_state.get("pid"),
            "token_path": existing_state.get("token_path", str(token_path)),
            "state_path": str(state_path),
            "health": existing_health,
        }
        _render_output(
            payload,
            [
                f"QuadGate daemon already running on "
                f"{payload['host']}:{payload['port']} (pid={payload['pid']})"
            ],
            as_json=as_json,
        )
        return 0

    if existing_state is not None and existing_health is None:
        state_path.unlink(missing_ok=True)

    home_dir.mkdir(parents=True, exist_ok=True)
    log_path = home_dir / "daemon.log"

    cfg_path = Path(str(ns.config))
    resolved_cfg = str(cfg_path.resolve()) if cfg_path.is_file() else str(ns.config)
    cmd = [
        sys.executable,
        "-m",
        "quadgate.cli",
        "daemon",
        "start",
        "--foreground",
        "--config",
        resolved_cfg,
    ]
    if getattr(ns, "host", None) is not None:
        cmd.extend(["--host", str(ns.host)])
    if getattr(ns, "port", None) is not None:
        cmd.extend(["--port", str(ns.port)])
    if getattr(ns, "idle_minutes", None) is not None:
        cmd.extend(["--idle-minutes", str(ns.idle_minutes)])

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    repo_str = str(_REPO_ROOT)
    if repo_str not in existing_pp.split(os.pathsep):
        env["PYTHONPATH"] = f"{repo_str}{os.pathsep}{existing_pp}" if existing_pp else repo_str

    popen_kwargs: dict[str, Any] = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    with log_path.open("a", encoding="utf-8") as log_fh:
        popen_kwargs["stdout"] = log_fh
        popen_kwargs["stderr"] = subprocess.STDOUT
        proc = subprocess.Popen(cmd, **popen_kwargs)

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_tail = ""
            if log_path.is_file():
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:]
            sys.stderr.write(
                f"QuadGate daemon exited prematurely (code={proc.returncode}).\n{log_tail}\n"
            )
            return 1
        state, health = _probe_running_daemon(state_path, token_path)
        if state is not None and health is not None and int(state.get("pid", -1)) == proc.pid:
            payload = {
                "status": "started",
                "host": state.get("host", "127.0.0.1"),
                "port": state.get("port"),
                "pid": proc.pid,
                "token_path": state.get("token_path", str(token_path)),
                "state_path": str(state_path),
                "health": health,
            }
            _render_output(
                payload,
                [
                    f"QuadGate daemon started on {payload['host']}:{payload['port']} "
                    f"(pid={proc.pid})"
                ],
                as_json=as_json,
            )
            return 0
        time.sleep(0.03)

    sys.stderr.write("Timed out waiting for QuadGate daemon /v1/health readiness.\n")
    return 1


def _cmd_daemon_stop(ns: argparse.Namespace) -> int:
    state_path = resolve_state_path()
    token_path = resolve_token_path()
    as_json = bool(getattr(ns, "json", False))

    state = _read_state(state_path)
    if state is None:
        _render_output(
            {"status": "stopped", "was_running": False, "state_path": str(state_path)},
            ["QuadGate daemon is not running."],
            as_json=as_json,
        )
        return 0

    host = _connect_host(str(state.get("host", "127.0.0.1")))
    port_raw = state.get("port")
    pid_raw = state.get("pid")
    tok_file = Path(str(state.get("token_path") or token_path))
    token = _read_token(tok_file)

    if isinstance(port_raw, int) or (isinstance(port_raw, str) and port_raw.isdigit()):
        port = int(port_raw)
        _http_json_request(
            "POST",
            host,
            port,
            "/v1/shutdown",
            token=token or None,
            timeout_s=2.0,
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not state_path.is_file():
                break
            status, _ = _http_json_request(
                "GET",
                host,
                port,
                "/v1/health",
                token=token or None,
                timeout_s=0.3,
            )
            if status == 0:
                break
            time.sleep(0.03)

    if isinstance(pid_raw, int) and pid_raw != os.getpid() and _is_pid_alive(pid_raw):
        sig = getattr(signal, "SIGTERM", signal.SIGINT)
        try:
            os.kill(pid_raw, sig)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and _is_pid_alive(pid_raw):
                time.sleep(0.03)
        except OSError:
            pass

    state_path.unlink(missing_ok=True)
    _render_output(
        {
            "status": "stopped",
            "was_running": True,
            "pid": pid_raw,
            "state_path": str(state_path),
        },
        [f"QuadGate daemon stopped (pid={pid_raw})."],
        as_json=as_json,
    )
    return 0


def _cmd_status(ns: argparse.Namespace) -> int:
    state_path = resolve_state_path()
    token_path = resolve_token_path()
    as_json = bool(getattr(ns, "json", False))

    state, health = _probe_running_daemon(state_path, token_path)
    if state is not None and health is not None:
        payload = {
            "status": "running",
            "host": state.get("host", "127.0.0.1"),
            "port": state.get("port"),
            "pid": state.get("pid"),
            "started_at": state.get("started_at"),
            "state_path": str(state_path),
            "health": health,
        }
        lines = [
            f"QuadGate daemon: running ({payload['host']}:{payload['port']}, pid={payload['pid']})",
            f"  driver:    {health.get('active_driver')}",
            f"  cartridge: {health.get('active_cartridge')}",
            f"  uptime_s:  {health.get('uptime_s')}",
        ]
        _render_output(payload, lines, as_json=as_json)
        return 0

    stale = state is not None
    payload = {
        "status": "stopped",
        "stale_state": stale,
        "state_path": str(state_path),
        "home_dir": str(resolve_quadgate_home()),
    }
    _render_output(
        payload,
        [f"QuadGate daemon: stopped{' (stale state file found)' if stale else ''}"],
        as_json=as_json,
    )
    return 0


def _build_calibration_driver(driver_name: str, home_dir: Path) -> ClassifierDriver:
    normalized = driver_name.strip().lower()
    if normalized == "zero_dep":
        return ZeroDepDriver()
    if normalized == "onnx_local":
        model_path = home_dir / "models" / "modernbert-int8.onnx"
        return LocalOnnxDriver(model_path=str(model_path), fallback_to_zero_dep=True)
    if normalized == "layaa":
        model_path = home_dir / "models" / "layaa-int8.onnx"
        return LayaaDriver(model_path=str(model_path))
    if normalized == "decision1":
        return Decision1Driver()
    if normalized == "jev":
        return JevDriver()
    if normalized == "logprob_fallback":
        return LogprobFallbackDriver()
    raise ValueError(f"Unsupported driver for calibration: {driver_name!r}")


async def _cmd_calibrate(ns: argparse.Namespace) -> int:
    home_dir = resolve_quadgate_home()
    profile_dir = (
        Path(ns.profile_dir).expanduser()
        if getattr(ns, "profile_dir", None)
        else home_dir / "calibration"
    )

    dataset_path = Path(ns.dataset)
    if not dataset_path.is_file() and ns.dataset == "benchmarks/calibration_gold.jsonl":
        repo_dataset = _REPO_ROOT / "benchmarks" / "calibration_gold.jsonl"
        if repo_dataset.is_file():
            dataset_path = repo_dataset
    if not dataset_path.is_file():
        raise FileNotFoundError(f"Calibration dataset not found: {dataset_path}")

    method_str = str(getattr(ns, "method", "temperature_scaling"))
    method = CalibrationMethod(method_str)
    driver = _build_calibration_driver(str(ns.driver), home_dir)
    engine = CalibrationEngine(profile_dir=profile_dir)
    try:
        profile = await engine.fit_driver(
            driver,
            method=method,
            dataset_path=dataset_path,
            persist=True,
        )
    finally:
        await driver.close()

    saved_path = profile_dir / f"{profile.driver_id}.json"
    payload = {
        "status": "ok",
        "driver_id": profile.driver_id,
        "method": profile.method.value,
        "sample_count": profile.sample_count,
        "ece": round(profile.ece, 6),
        "passes_ece_gate": profile.passes_ece_gate,
        "profile_path": str(saved_path),
    }
    lines = [
        f"Calibrated driver={profile.driver_id} method={profile.method.value}",
        f"  samples: {profile.sample_count} | ECE: {profile.ece:.4f} | passes_ece_gate: {profile.passes_ece_gate}",
        f"  profile: {saved_path}",
    ]
    _render_output(payload, lines, as_json=bool(getattr(ns, "json", False)))
    return 0


def _cmd_stats(ns: argparse.Namespace) -> int:
    state_path = resolve_state_path()
    token_path = resolve_token_path()
    as_json = bool(getattr(ns, "json", False))

    state = _read_state(state_path)
    if state is not None:
        host = _connect_host(str(state.get("host", "127.0.0.1")))
        try:
            port = int(state["port"])
        except (KeyError, TypeError, ValueError):
            port = 0
        if port > 0:
            tok_file = Path(str(state.get("token_path") or token_path))
            token = _read_token(tok_file)
            status, live_stats = _http_json_request(
                "GET",
                host,
                port,
                "/v1/stats",
                token=token or None,
                timeout_s=1.5,
            )
            if status == 200 and live_stats is not None:
                lines = [
                    f"QuadGate live stats ({host}:{port}):",
                    f"  requests:     {live_stats.get('total_requests', 0)}",
                    f"  mean_ms:      {live_stats.get('mean_latency_ms', 0.0)}",
                    f"  p50_ms:       {live_stats.get('p50_latency_ms', 0.0)}",
                    f"  verdicts:     {live_stats.get('verdict_breakdown', {})}",
                    f"  self_healing: {live_stats.get('self_healing', {})}",
                ]
                _render_output(live_stats, lines, as_json=as_json)
                return 0

    grey_log = Path.cwd() / ".quadgate" / "logs" / "grey_zone.jsonl"
    grey_count = 0
    if grey_log.is_file():
        grey_count = sum(1 for ln in grey_log.read_text(encoding="utf-8").splitlines() if ln.strip())
    offline_payload = {
        "status": "offline",
        "total_requests": 0,
        "verdict_breakdown": {},
        "mean_latency_ms": 0.0,
        "p50_latency_ms": 0.0,
        "grey_zone_logged_offline": grey_count,
    }
    _render_output(
        offline_payload,
        [
            "QuadGate daemon is offline; no live stats available.",
            f"  offline grey-zone entries: {grey_count}",
        ],
        as_json=as_json,
    )
    return 0


async def _cmd_doctor(ns: argparse.Namespace) -> int:
    requested_port = int(getattr(ns, "port", 0) or 0)
    as_json = bool(getattr(ns, "json", False))

    with tempfile.TemporaryDirectory(prefix="quadgate-doctor-") as tmp_dir:
        tmp_home = Path(tmp_dir)
        probe_ws = tmp_home / "doctor_ws"
        probe_ws.mkdir(parents=True, exist_ok=True)
        (probe_ws / "probe.py").write_text(
            "def check_health() -> bool:\n    return True\n",
            encoding="utf-8",
        )
        daemon = QuadGateDaemon(
            DaemonConfig(host="127.0.0.1", port=requested_port),
            home_dir=tmp_home,
            token_path=tmp_home / "run.token",
            state_path=tmp_home / "daemon.json",
        )
        await daemon.start()
        try:
            # Always connect explicitly to 127.0.0.1 (never "localhost") to avoid IPv6 DNS lag on Windows.
            base = f"http://127.0.0.1:{daemon.bound_port}"
            headers = {"Authorization": f"Bearer {daemon.auth_token}"}
            timeout = aiohttp.ClientTimeout(total=10.0)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Warm persistent TCP connection before timing health latency.
                async with session.get(f"{base}/v1/health", headers=headers) as warm_resp:
                    if warm_resp.status != 200:
                        raise RuntimeError(f"Doctor warmup /v1/health returned HTTP {warm_resp.status}")
                    await warm_resp.read()

                t0 = time.perf_counter()
                async with session.get(f"{base}/v1/health", headers=headers) as health_resp:
                    health_data = await health_resp.json()
                    if health_resp.status != 200:
                        raise RuntimeError(f"Doctor /v1/health returned HTTP {health_resp.status}")
                health_latency_ms = (time.perf_counter() - t0) * 1000.0

                sample_event = GateEvent(
                    gate=GateType.INTAKE,
                    session_id="quadgate-doctor-sanity",
                    turn_index=1,
                    workspace_root=str(probe_ws),
                    harness=HarnessType.GENERIC,
                    prompt_text="Check health of QuadGate daemon",
                )
                t_eval0 = time.perf_counter()
                async with session.post(
                    f"{base}/v1/evaluate",
                    headers=headers,
                    json=sample_event.model_dump(mode="json"),
                ) as eval_resp:
                    eval_body = await eval_resp.json()
                    if eval_resp.status != 200:
                        raise RuntimeError(f"Doctor /v1/evaluate returned HTTP {eval_resp.status}")
                evaluate_latency_ms = (time.perf_counter() - t_eval0) * 1000.0
                verdict = GateVerdict.model_validate(eval_body)

                async with session.get(f"{base}/v1/stats", headers=headers) as stats_resp:
                    if stats_resp.status != 200:
                        raise RuntimeError(f"Doctor /v1/stats returned HTTP {stats_resp.status}")
                    stats_data = await stats_resp.json()
        finally:
            await daemon.stop()

    health_ok = health_latency_ms < 10.0 and health_data.get("status") == "ok"
    clean_shutdown = not daemon.state_path.exists() and not daemon.is_running
    ok = health_ok and clean_shutdown and int(stats_data.get("total_requests", 0)) >= 1

    report = {
        "status": "ok" if ok else "degraded",
        "host": "127.0.0.1",
        "port": daemon.bound_port,
        "health_latency_ms": round(health_latency_ms, 3),
        "health_under_10ms": health_latency_ms < 10.0,
        "evaluate_latency_ms": round(evaluate_latency_ms, 3),
        "evaluate_action": verdict.action.value,
        "active_driver": health_data.get("active_driver"),
        "active_cartridge": health_data.get("active_cartridge"),
        "clean_shutdown": clean_shutdown,
    }
    lines = [
        f"QuadGate doctor: {report['status'].upper()}",
        f"  health_latency_ms:   {report['health_latency_ms']:.3f} ms (<10ms: {report['health_under_10ms']})",
        f"  evaluate_latency_ms: {report['evaluate_latency_ms']:.3f} ms (action={report['evaluate_action']})",
        f"  active_driver:       {report['active_driver']} | cartridge={report['active_cartridge']}",
        f"  clean_shutdown:      {report['clean_shutdown']}",
    ]
    _render_output(report, lines, as_json=as_json)
    return 0 if ok else 1


async def _cmd_bench(ns: argparse.Namespace) -> int:
    repo_str = str(_REPO_ROOT)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    from benchmarks.runner import BenchmarkRunner

    dry_replay = False if bool(getattr(ns, "live", False)) else True
    output_path = Path(str(getattr(ns, "output", "benchmarks/reports/LATEST_PROOF.md")))
    runner = BenchmarkRunner(reports_dir=output_path.parent)
    receipts = await runner.run_suite(
        suite=ns.suite,
        runs=int(ns.runs),
        dry_replay=dry_replay,
        output_path=output_path,
    )

    base_r = [r for r in receipts if r.mode == "baseline"]
    qg_r = [r for r in receipts if r.mode == "quadgate"]

    def _mode_summary(items: list[Any]) -> tuple[float, float, float]:
        if not items:
            return 0.0, 0.0, 0.0
        pass_pct = (sum(1 for r in items if r.passed) / float(len(items))) * 100.0
        total_in = sum(r.total_input_tokens for r in items)
        cache_hit = (
            (sum(r.cache_read_input_tokens for r in items) / float(total_in)) * 100.0
            if total_in > 0
            else 0.0
        )
        total_cost = sum(r.total_cost_usd for r in items)
        return pass_pct, cache_hit, total_cost

    base_pass, base_cache, base_cost = _mode_summary(base_r)
    qg_pass, qg_cache, qg_cost = _mode_summary(qg_r)

    summary = {
        "status": "ok",
        "suite": str(ns.suite),
        "runs": int(ns.runs),
        "dry_replay": dry_replay,
        "total_receipts": len(receipts),
        "baseline_pass_rate_pct": round(base_pass, 2),
        "quadgate_pass_rate_pct": round(qg_pass, 2),
        "baseline_warm_cache_hit_pct": round(base_cache, 2),
        "quadgate_warm_cache_hit_pct": round(qg_cache, 2),
        "baseline_total_cost_usd": round(base_cost, 6),
        "quadgate_total_cost_usd": round(qg_cost, 6),
        "cost_delta_usd": round(qg_cost - base_cost, 6),
        "report_path": str(output_path),
    }
    lines = [
        f"QuadGate bench ({ns.suite}, runs={ns.runs}, dry_replay={dry_replay}):",
        f"  receipts:  {len(receipts)} ({len(qg_r)} paired tasks)",
        f"  pass@1:    baseline={base_pass:.1f}% vs quadgate={qg_pass:.1f}%",
        f"  KV cache:  baseline={base_cache:.1f}% vs quadgate={qg_cache:.1f}%",
        f"  cost USD:  baseline=${base_cost:.4f} vs quadgate=${qg_cost:.4f} "
        f"(delta={qg_cost - base_cost:+.4f})",
        f"  report:    {output_path}",
    ]
    _render_output(summary, lines, as_json=bool(getattr(ns, "json", False)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for all QuadGate subcommands."""
    parser = argparse.ArgumentParser(
        prog="quadgate",
        description="Model-agnostic & harness-agnostic 4-gate discriminative control plane.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_p = subparsers.add_parser("init", help="Initialize workspace index and harness hooks.")
    init_p.add_argument(
        "--harness",
        choices=["antigravity", "claude-code", "codex", "mcp"],
        default="antigravity",
    )
    init_p.add_argument("--local", action="store_true", help="Prepare local ONNX model assets.")
    init_p.add_argument("--workspace", default=".")
    init_p.add_argument("--cartridge", default="software_engineering")
    init_p.add_argument("--force", action="store_true")
    init_p.add_argument("--json", action="store_true")

    daemon_p = subparsers.add_parser("daemon", help="Manage the persistent local kernel daemon.")
    daemon_p.add_argument("action", choices=["start", "stop", "status"])
    daemon_p.add_argument("--config", default="quadgate.toml")
    daemon_p.add_argument("--foreground", action="store_true")
    daemon_p.add_argument("--host", default=None)
    daemon_p.add_argument("--port", type=int, default=None)
    daemon_p.add_argument("--idle-minutes", type=int, default=None)
    daemon_p.add_argument("--json", action="store_true")

    cal_p = subparsers.add_parser("calibrate", help="Fit conformal calibration profile for a driver.")
    cal_p.add_argument("--driver", default="onnx_local")
    cal_p.add_argument("--dataset", default="benchmarks/calibration_gold.jsonl")
    cal_p.add_argument(
        "--method",
        choices=["temperature_scaling", "isotonic"],
        default="temperature_scaling",
    )
    cal_p.add_argument("--profile-dir", default=None)
    cal_p.add_argument("--json", action="store_true")

    status_p = subparsers.add_parser("status", help="Show active daemon, cartridge, and calibration status.")
    status_p.add_argument("--config", default="quadgate.toml")
    status_p.add_argument("--json", action="store_true")

    stats_p = subparsers.add_parser("stats", help="Show gate evaluation and promotion telemetry.")
    stats_p.add_argument("--config", default="quadgate.toml")
    stats_p.add_argument("--json", action="store_true")

    doctor_p = subparsers.add_parser("doctor", help="Run end-to-end health and latency diagnostics.")
    doctor_p.add_argument("--config", default="quadgate.toml")
    doctor_p.add_argument("--port", type=int, default=0)
    doctor_p.add_argument("--json", action="store_true")

    bench_p = subparsers.add_parser("bench", help="Run reproducible A/B benchmark suite.")
    bench_p.add_argument(
        "--suite",
        choices=[
            "coding",
            "knowledge",
            "all",
            "stress_long_context",
            "stress_tool_cache_routing",
            "stress_adversarial_resilience",
            "stress_all",
            "everything",
        ],
        default="all",
    )
    bench_p.add_argument("--runs", type=int, default=1)
    bench_p.add_argument("--dry-replay", action="store_true")
    bench_p.add_argument("--live", action="store_true")
    bench_p.add_argument("--output", default="benchmarks/reports/LATEST_PROOF.md")
    bench_p.add_argument("--json", action="store_true")

    codex_p = subparsers.add_parser("exec-codex", help="Run OpenAI Codex CLI under QuadGate guards.")
    codex_p.add_argument("codex_args", nargs=argparse.REMAINDER)

    subparsers.add_parser("mcp", help="Run stdio MCP server bridge.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the requested subcommand."""
    parser = build_parser()
    ns = parser.parse_args(list(argv) if argv is not None else None)
    if ns.command == "init":
        return asyncio.run(_cmd_init(ns))
    if ns.command == "daemon":
        if ns.action == "start":
            return _cmd_daemon_start(ns)
        if ns.action == "stop":
            return _cmd_daemon_stop(ns)
        if ns.action == "status":
            return _cmd_status(ns)
    if ns.command == "status":
        return _cmd_status(ns)
    if ns.command == "stats":
        return _cmd_stats(ns)
    if ns.command == "calibrate":
        return asyncio.run(_cmd_calibrate(ns))
    if ns.command == "doctor":
        return asyncio.run(_cmd_doctor(ns))
    if ns.command == "exec-codex":
        return CodexCliAdapter().run_exec_codex(list(getattr(ns, "codex_args", []) or []))
    if ns.command == "mcp":
        mcp_adapter = McpBridgeAdapter()
        try:
            return asyncio.run(mcp_adapter.serve_stdio())
        finally:
            mcp_adapter.close()
    if ns.command == "bench":
        return asyncio.run(_cmd_bench(ns))
    raise NotImplementedError(f"Unsupported command: {ns.command}")


if __name__ == "__main__":
    raise SystemExit(main())
