"""Async localhost HTTP/IPC kernel daemon (`127.0.0.1:8787`)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import statistics
import tempfile
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import quadgate.kernel.gates as _gates_mod
from quadgate.contracts import (
    CartridgeSpec,
    GateEvent,
    GateType,
    GateVerdict,
    VerdictAction,
)
from quadgate.drivers.base import ClassifierDriver
from quadgate.drivers.decision1 import Decision1Driver
from quadgate.drivers.jev import JevDriver
from quadgate.drivers.layaa import LayaaDriver
from quadgate.drivers.logprob_fallback import LogprobFallbackDriver
from quadgate.drivers.onnx_local import LocalOnnxDriver, ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.cochange import CoChangeGraph
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.calibration import CalibrationEngine
from quadgate.kernel.gates import GateEngine, ThresholdConfig, TierModelConfig
from quadgate.kernel.healing import SelfHealingEngine

DAEMON_VERSION = "0.1.1"
_CARTRIDGES_DIR = Path(__file__).resolve().parent.parent / "cartridges"
_DEFAULT_LATENCY_WINDOW = 10_000


class DaemonConfig(BaseModel):
    """Runtime network and timeout settings for the persistent daemon."""

    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=0, le=65535)
    proxy_port: int = Field(default=8788, ge=0, le=65535)
    shim_timeout_ms: int = Field(default=450, ge=50, le=5000)
    idle_shutdown_minutes: int = Field(default=30, ge=1)


@dataclass(slots=True)
class _WorkspaceContext:
    """Isolated per-workspace state cached by canonical realpath hash."""

    workspace_hash: str
    workspace_root: Path
    cartridge: CartridgeSpec
    indexer: AstGistIndexer
    cochange: CoChangeGraph
    healing: SelfHealingEngine
    engine: GateEngine
    last_used_monotonic: float
    last_synced_monotonic: float = 0.0
    last_root_mtime_ns: int = -1


def _safe_stat_mtime_ns(path: Path | str) -> int:
    """Return st_mtime_ns for path, or -1 if missing/inaccessible."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def resolve_quadgate_home(home_dir: Path | str | None = None) -> Path:
    """Resolve the QuadGate runtime home directory (`QUADGATE_HOME` or `~/.quadgate`)."""
    if home_dir is not None:
        return Path(home_dir).expanduser()
    env_home = os.environ.get("QUADGATE_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path("~/.quadgate").expanduser()


def resolve_token_path(
    *,
    home_dir: Path | str | None = None,
    token_path: Path | str | None = None,
) -> Path:
    """Resolve the Bearer token file path honoring explicit args, env, and home dir."""
    if token_path is not None:
        return Path(token_path).expanduser()
    if home_dir is not None:
        return Path(home_dir).expanduser() / "run.token"
    env_token = os.environ.get("QUADGATE_TOKEN_PATH")
    if env_token:
        return Path(env_token).expanduser()
    return resolve_quadgate_home() / "run.token"


def resolve_state_path(
    *,
    home_dir: Path | str | None = None,
    state_path: Path | str | None = None,
) -> Path:
    """Resolve the runtime `daemon.json` state file path."""
    if state_path is not None:
        return Path(state_path).expanduser()
    if home_dir is not None:
        return Path(home_dir).expanduser() / "daemon.json"
    env_state = os.environ.get("QUADGATE_STATE_PATH")
    if env_state:
        return Path(env_state).expanduser()
    return resolve_quadgate_home() / "daemon.json"


def load_toml_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load `quadgate.toml` when present, returning an empty dict if missing."""
    if config_path is None:
        return {}
    path = Path(config_path).expanduser()
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data if isinstance(data, dict) else {}


def load_cartridge_spec(name_or_path: CartridgeSpec | str | Path | None = None) -> CartridgeSpec:
    """Load a Domain Cartridge via `quadgate.kernel.gates.load_cartridge` or YAML fallback."""
    if isinstance(name_or_path, CartridgeSpec):
        return name_or_path
    target = name_or_path if name_or_path is not None else "software_engineering"
    loader = getattr(_gates_mod, "load_cartridge", None)
    if callable(loader):
        spec = loader(target)
        if isinstance(spec, CartridgeSpec):
            return spec

    candidate = Path(target).expanduser()
    if not candidate.is_file():
        stem = str(target).strip()
        if stem.endswith(".yaml"):
            candidate = _CARTRIDGES_DIR / stem
        else:
            candidate = _CARTRIDGES_DIR / f"{stem}.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"Domain cartridge not found: {target!r}")
    raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    return CartridgeSpec.model_validate(raw)


def _attach_saved_calibration(
    driver: ClassifierDriver,
    calibration_dir: Path,
) -> ClassifierDriver:
    """Attach an existing calibration profile for `driver.driver_id` if present on disk."""
    if driver.calibration_profile is not None:
        return driver
    try:
        profile = CalibrationEngine(profile_dir=calibration_dir).load_profile(driver.driver_id)
    except Exception:
        profile = None
    if profile is not None:
        driver.set_calibration_profile(profile)
    return driver


def build_driver_from_config(
    driver_name: str | None = None,
    *,
    toml_data: dict[str, Any] | None = None,
    calibration_dir: Path | None = None,
) -> ClassifierDriver:
    """Instantiate a configured ClassifierDriver with offline zero-dep fallback safety."""
    cfg = toml_data or {}
    classifier_cfg = cfg.get("classifier", {}) if isinstance(cfg.get("classifier"), dict) else {}

    active_cfg = classifier_cfg.get("active_driver")
    requested = (
        driver_name
        or (str(active_cfg) if active_cfg else None)
        or "zero_dep"
    ).strip().lower()
    fallback_name = str(classifier_cfg.get("fallback_driver", "zero_dep")).strip().lower()
    cal_dir = calibration_dir or (resolve_quadgate_home() / "calibration")

    driver: ClassifierDriver
    if requested == "zero_dep":
        driver = ZeroDepDriver()
    elif requested == "onnx_local":
        default_model = str(cal_dir.parent / "models" / "modernbert-int8.onnx")
        onnx_cfg = classifier_cfg.get("onnx_local", {})
        model_path = (
            str(onnx_cfg.get("model_path", default_model))
            if isinstance(onnx_cfg, dict)
            else default_model
        )
        device = str(onnx_cfg.get("device", "cpu")) if isinstance(onnx_cfg, dict) else "cpu"
        resolved_model = Path(model_path).expanduser()
        has_ort = False
        try:
            import importlib.util

            has_ort = importlib.util.find_spec("onnxruntime") is not None
        except Exception:
            has_ort = False
        if resolved_model.is_file() and has_ort:
            driver = LocalOnnxDriver(model_path=model_path, device=device)
        elif fallback_name == "zero_dep":
            driver = ZeroDepDriver()
        else:
            driver = LocalOnnxDriver(
                model_path=model_path,
                device=device,
                fallback_to_zero_dep=True,
            )
    elif requested == "layaa":
        layaa_cfg = classifier_cfg.get("layaa", {})
        if isinstance(layaa_cfg, dict):
            driver = LayaaDriver(
                model_path=str(layaa_cfg.get("model_path", "~/.quadgate/models/layaa-int8.onnx")),
                device=str(layaa_cfg.get("device", "cpu")),
                endpoint=layaa_cfg.get("endpoint"),
            )
        else:
            driver = LayaaDriver()
    elif requested == "decision1":
        d1_cfg = classifier_cfg.get("decision1", {})
        if isinstance(d1_cfg, dict):
            api_key_env = str(d1_cfg.get("api_key_env", "DECISION1_API_KEY"))
            driver = Decision1Driver(
                endpoint=str(d1_cfg.get("endpoint", "https://api.decision1.ai/v1/score")),
                api_key=os.environ.get(api_key_env),
                max_heads=int(d1_cfg.get("max_heads", 1000)),
            )
        else:
            driver = Decision1Driver()
    elif requested == "jev":
        jev_cfg = classifier_cfg.get("jev", {})
        if isinstance(jev_cfg, dict):
            api_key_env = str(jev_cfg.get("api_key_env", "JEV_API_KEY"))
            driver = JevDriver(
                endpoint=jev_cfg.get("endpoint"),
                api_key=os.environ.get(api_key_env),
                max_heads=int(jev_cfg.get("max_heads", 800)),
            )
        else:
            driver = JevDriver()
    elif requested == "logprob_fallback":
        driver = LogprobFallbackDriver()
    else:
        raise ValueError(f"Unsupported classifier driver: {requested!r}")

    return _attach_saved_calibration(driver, cal_dir)


def _atomic_write_text(path: Path, content: str, *, mode: int | None = None) -> None:
    """Atomically write UTF-8 text to `path` via a sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if mode is not None and os.name != "nt":
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _workspace_hash_for_path(workspace_root: str | Path) -> tuple[Path, str]:
    """Return canonical realpath and 128-bit blake2b workspace hash."""
    resolved = Path(os.path.realpath(str(workspace_root)))
    norm = os.path.normcase(str(resolved))
    ws_hash = hashlib.blake2b(norm.encode("utf-8"), digest_size=16).hexdigest()
    return resolved, ws_hash


class QuadGateDaemon:
    """Async HTTP/IPC control-plane server handling `/v1/evaluate`, `/v1/health`, `/v1/stats`."""

    def __init__(
        self,
        config: DaemonConfig | None = None,
        *,
        home_dir: Path | str | None = None,
        token_path: Path | str | None = None,
        state_path: Path | str | None = None,
        cartridge: CartridgeSpec | str | Path | None = None,
        driver: ClassifierDriver | None = None,
        driver_name: str | None = None,
        thresholds: ThresholdConfig | None = None,
        tiers: TierModelConfig | None = None,
        idle_timeout_seconds: float | None = None,
        calibration_dir: Path | str | None = None,
    ) -> None:
        self.config = config or DaemonConfig()
        self.home_dir = resolve_quadgate_home(home_dir)
        self.token_path = resolve_token_path(home_dir=home_dir, token_path=token_path)
        self.state_path = resolve_state_path(home_dir=home_dir, state_path=state_path)
        self.calibration_dir = (
            Path(calibration_dir).expanduser()
            if calibration_dir is not None
            else self.home_dir / "calibration"
        )

        self.cartridge = load_cartridge_spec(cartridge)
        self.thresholds = thresholds or ThresholdConfig()
        self.tiers = tiers or TierModelConfig()

        if driver is not None:
            self.driver: ClassifierDriver = _attach_saved_calibration(driver, self.calibration_dir)
        elif driver_name is not None:
            self.driver = build_driver_from_config(
                driver_name,
                calibration_dir=self.calibration_dir,
            )
        else:
            self.driver = _attach_saved_calibration(ZeroDepDriver(), self.calibration_dir)

        if idle_timeout_seconds is not None:
            if idle_timeout_seconds <= 0:
                raise ValueError(
                    f"idle_timeout_seconds must be > 0; got {idle_timeout_seconds!r}."
                )
            self._idle_timeout_seconds = float(idle_timeout_seconds)
        else:
            self._idle_timeout_seconds = float(self.config.idle_shutdown_minutes) * 60.0

        self.bound_port: int = self.config.port
        self._auth_token: str | None = None
        self._wrote_token: bool = False
        self._started_at_iso: str | None = None
        self._started_monotonic: float | None = None
        self._last_activity_monotonic: float = time.monotonic()

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._lifecycle_lock: asyncio.Lock | None = None
        self._workspace_lock: asyncio.Lock | None = None
        self._stopped_event: asyncio.Event | None = None

        self._fts_store = SqliteFtsStore(self.home_dir / "pass0.db")
        self._fts_initialized: bool = False
        self._workspaces: dict[str, _WorkspaceContext] = {}

        # Telemetry metrics
        self._inflight_requests: int = 0
        self._total_requests: int = 0
        self._latency_sum_ms: float = 0.0
        self._latencies_ms: deque[float] = deque(maxlen=_DEFAULT_LATENCY_WINDOW)
        self._verdict_counts: dict[str, int] = {action.value: 0 for action in VerdictAction}
        self._gate_counts: dict[str, int] = {gate.value: 0 for gate in GateType}

    @classmethod
    def from_toml(
        cls,
        config_path: Path | str | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        idle_shutdown_minutes: int | None = None,
        idle_timeout_seconds: float | None = None,
        home_dir: Path | str | None = None,
        token_path: Path | str | None = None,
        state_path: Path | str | None = None,
    ) -> QuadGateDaemon:
        """Construct a daemon configured from `quadgate.toml` and optional CLI overrides."""
        data = load_toml_config(config_path)
        daemon_raw = dict(data.get("daemon", {})) if isinstance(data.get("daemon"), dict) else {}
        if host is not None:
            daemon_raw["host"] = host
        if port is not None:
            daemon_raw["port"] = port
        if idle_shutdown_minutes is not None:
            daemon_raw["idle_shutdown_minutes"] = idle_shutdown_minutes
        daemon_cfg = DaemonConfig.model_validate(daemon_raw)

        thresholds_raw = data.get("thresholds", {})
        thresholds = (
            ThresholdConfig.model_validate(thresholds_raw)
            if isinstance(thresholds_raw, dict) and thresholds_raw
            else ThresholdConfig()
        )

        tiers_section = data.get("tiers", {})
        models_raw = (
            tiers_section.get("models", {}) if isinstance(tiers_section, dict) else {}
        )
        tiers = (
            TierModelConfig.model_validate(models_raw)
            if isinstance(models_raw, dict) and models_raw
            else TierModelConfig()
        )

        cart_section = data.get("cartridge", {})
        cartridge_name = (
            str(cart_section.get("active", "software_engineering"))
            if isinstance(cart_section, dict)
            else "software_engineering"
        )

        resolved_home = resolve_quadgate_home(home_dir)
        driver = build_driver_from_config(
            toml_data=data,
            calibration_dir=resolved_home / "calibration",
        )
        return cls(
            daemon_cfg,
            home_dir=home_dir,
            token_path=token_path,
            state_path=state_path,
            cartridge=cartridge_name,
            driver=driver,
            thresholds=thresholds,
            tiers=tiers,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    @property
    def base_url(self) -> str:
        """Return the base HTTP URL for the bound daemon listener."""
        return f"http://{self.config.host}:{self.bound_port}"

    @property
    def auth_token(self) -> str | None:
        """Return the active Bearer authentication token if started."""
        return self._auth_token

    @property
    def is_running(self) -> bool:
        """Return True while the daemon HTTP server is actively serving."""
        return self._running

    def _get_lifecycle_lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _get_workspace_lock(self) -> asyncio.Lock:
        if self._workspace_lock is None:
            self._workspace_lock = asyncio.Lock()
        return self._workspace_lock

    def _get_stopped_event(self) -> asyncio.Event:
        if self._stopped_event is None:
            self._stopped_event = asyncio.Event()
        return self._stopped_event

    def _uptime_s(self) -> float:
        if self._started_monotonic is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_monotonic)

    def _health_payload(self) -> dict[str, object]:
        return {
            "status": "ok",
            "version": DAEMON_VERSION,
            "port": self.bound_port,
            "active_driver": self.driver.driver_id,
            "active_cartridge": self.cartridge.name,
            "uptime_s": round(self._uptime_s(), 3),
        }

    def _verify_bearer_header(self, request: web.Request) -> bool:
        expected = self._auth_token
        if not expected:
            return False
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return False
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        provided = parts[1].strip().replace("\r", "").replace("\n", "")
        if not provided:
            return False
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

    def _sync_workspace_index(
        self,
        indexer: AstGistIndexer,
        cochange: CoChangeGraph | None = None,
    ) -> None:
        if not self._fts_initialized or getattr(self._fts_store, "_conn", None) is None:
            self._fts_store.initialize()
            self._fts_initialized = True
        prev_cached = getattr(indexer, "_cached_result", None)
        gists = indexer.index_workspace(incremental=True)
        curr_cached = getattr(indexer, "_cached_result", None)
        if gists and (prev_cached is None or curr_cached is not prev_cached):
            self._fts_store.upsert_gists(gists)
        if cochange is not None:
            cochange.build_from_git_log()

    @staticmethod
    def _needs_workspace_resync(ctx: _WorkspaceContext, now_mono: float) -> bool:
        if (now_mono - ctx.last_synced_monotonic) >= 0.5:
            return True
        cur_root_mtime = _safe_stat_mtime_ns(ctx.workspace_root)
        if cur_root_mtime == -1 or cur_root_mtime != ctx.last_root_mtime_ns:
            return True
        dir_mtimes: dict[str, int] | None = getattr(ctx.indexer, "_dir_mtimes", None)
        if isinstance(dir_mtimes, dict) and 0 < len(dir_mtimes) <= 8:
            ws_root_str = str(ctx.workspace_root)
            for rel_dir, prev_mtime in dir_mtimes.items():
                if not rel_dir:
                    continue
                sub_path = os.path.join(ws_root_str, rel_dir)
                if _safe_stat_mtime_ns(sub_path) != prev_mtime:
                    return True
        return False

    async def _get_or_create_workspace(
        self,
        workspace_root: str,
        *,
        reindex_if_intake_turn1: bool = False,
    ) -> _WorkspaceContext:
        resolved, ws_hash = _workspace_hash_for_path(workspace_root)
        ctx = self._workspaces.get(ws_hash)
        if ctx is not None:
            now_mono = time.monotonic()
            ctx.last_used_monotonic = now_mono
            if reindex_if_intake_turn1 and self._needs_workspace_resync(ctx, now_mono):
                await asyncio.to_thread(self._sync_workspace_index, ctx.indexer, None)
                ctx.last_synced_monotonic = time.monotonic()
                ctx.last_root_mtime_ns = _safe_stat_mtime_ns(ctx.workspace_root)
            return ctx

        async with self._get_workspace_lock():
            ctx = self._workspaces.get(ws_hash)
            if ctx is not None:
                ctx.last_used_monotonic = time.monotonic()
                return ctx

            indexer = AstGistIndexer(resolved, cartridge=self.cartridge)
            cochange = CoChangeGraph(resolved)
            await asyncio.to_thread(self._sync_workspace_index, indexer, cochange)

            ws_state_dir = self.home_dir / "workspaces" / ws_hash
            healing_db = ws_state_dir / "healing.db"
            grey_zone_log = (
                resolved / ".quadgate" / "logs" / "grey_zone.jsonl"
                if resolved.exists()
                else ws_state_dir / "grey_zone.jsonl"
            )
            healing = SelfHealingEngine(
                db_path=healing_db,
                workspace_root=resolved,
                grey_zone_log_path=grey_zone_log,
                tiers=self.tiers,
            )
            engine = GateEngine(
                self.cartridge,
                thresholds=self.thresholds,
                tiers=self.tiers,
                driver=self.driver,
                fts_store=self._fts_store,
                indexer=indexer,
                cochange=cochange,
                healing=healing,
            )
            sync_mono = time.monotonic()
            ctx = _WorkspaceContext(
                workspace_hash=ws_hash,
                workspace_root=resolved,
                cartridge=self.cartridge,
                indexer=indexer,
                cochange=cochange,
                healing=healing,
                engine=engine,
                last_used_monotonic=sync_mono,
                last_synced_monotonic=sync_mono,
                last_root_mtime_ns=_safe_stat_mtime_ns(resolved),
            )
            self._workspaces[ws_hash] = ctx
            return ctx

    def _record_stats(self, event: GateEvent, verdict: GateVerdict, elapsed_ms: float) -> None:
        self._total_requests += 1
        self._latency_sum_ms += elapsed_ms
        self._latencies_ms.append(elapsed_ms)
        action_key = verdict.action.value
        self._verdict_counts[action_key] = self._verdict_counts.get(action_key, 0) + 1
        gate_key = event.gate.value
        self._gate_counts[gate_key] = self._gate_counts.get(gate_key, 0) + 1

    async def _http_health(self, request: web.Request) -> web.Response:
        if "Authorization" in request.headers and not self._verify_bearer_header(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        body = json.dumps(self._health_payload(), separators=(",", ":"))
        return web.Response(text=body, status=200, content_type="application/json")

    async def _http_stats(self, request: web.Request) -> web.Response:
        if not self._verify_bearer_header(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        payload = await self.stats()
        return web.json_response(payload, status=200)

    async def _http_evaluate(self, request: web.Request) -> web.Response:
        if not self._verify_bearer_header(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            raw = await request.read()
            body = json.loads(raw)
        except Exception as exc:
            return web.json_response({"error": f"Invalid JSON payload: {exc}"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Request body must be a JSON object"}, status=400)
        try:
            event = GateEvent.model_validate(body)
        except ValidationError as exc:
            return web.json_response(
                {"error": "Invalid GateEvent", "details": exc.errors()},
                status=400,
            )

        verdict = await self.handle_evaluate(event)
        return web.Response(
            text=verdict.model_dump_json(),
            status=200,
            content_type="application/json",
        )

    async def _delayed_stop(self) -> None:
        await asyncio.sleep(0.02)
        await self.stop()

    async def _http_shutdown(self, request: web.Request) -> web.Response:
        if not self._verify_bearer_header(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self._shutdown_task is None or self._shutdown_task.done():
            self._shutdown_task = asyncio.create_task(self._delayed_stop())
        return web.json_response({"status": "shutting_down"}, status=200)

    async def _idle_monitor_loop(self) -> None:
        interval = max(0.01, min(5.0, self._idle_timeout_seconds / 4.0))
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            if self._inflight_requests > 0:
                continue
            idle_for = time.monotonic() - self._last_activity_monotonic
            if idle_for >= self._idle_timeout_seconds:
                await self.stop()
                break

    async def start(self) -> None:
        """Bind localhost server, generate Bearer token, and persist daemon metadata."""
        async with self._get_lifecycle_lock():
            if self._running:
                return

            self._fts_store.initialize()
            self._fts_initialized = True
            self._auth_token = secrets.token_urlsafe(32)
            _atomic_write_text(self.token_path, self._auth_token + "\n", mode=0o600)
            self._wrote_token = True

            app = web.Application()
            app.router.add_post("/v1/evaluate", self._http_evaluate)
            app.router.add_get("/v1/health", self._http_health)
            app.router.add_get("/v1/stats", self._http_stats)
            app.router.add_post("/v1/shutdown", self._http_shutdown)

            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            try:
                site = web.TCPSite(runner, self.config.host, self.config.port)
                await site.start()
            except Exception:
                await runner.cleanup()
                if self._wrote_token:
                    self.token_path.unlink(missing_ok=True)
                    self._wrote_token = False
                raise

            self._runner = runner
            self._site = site
            addresses = runner.addresses
            if addresses:
                self.bound_port = int(addresses[0][1])
            else:
                self.bound_port = self.config.port

            now_iso = datetime.now(timezone.utc).isoformat()
            now_mono = time.monotonic()
            self._started_at_iso = now_iso
            self._started_monotonic = now_mono
            self._last_activity_monotonic = now_mono
            self._running = True
            self._get_stopped_event().clear()

            state_payload = {
                "host": self.config.host,
                "port": self.bound_port,
                "proxy_port": self.config.proxy_port,
                "pid": os.getpid(),
                "started_at": now_iso,
                "token_path": str(self.token_path),
                "active_driver": self.driver.driver_id,
                "active_cartridge": self.cartridge.name,
            }
            _atomic_write_text(
                self.state_path,
                json.dumps(state_payload, indent=2) + "\n",
                mode=0o600,
            )

            self._idle_task = asyncio.create_task(self._idle_monitor_loop())

    async def stop(self) -> None:
        """Flush telemetry, shut down server, and remove runtime state cleanly."""
        async with self._get_lifecycle_lock():
            current = asyncio.current_task()
            was_running = self._running or self._runner is not None
            self._running = False

            idle_task = self._idle_task
            self._idle_task = None
            if idle_task is not None and idle_task is not current and not idle_task.done():
                idle_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await idle_task

            shutdown_task = self._shutdown_task
            self._shutdown_task = None
            if (
                shutdown_task is not None
                and shutdown_task is not current
                and not shutdown_task.done()
            ):
                shutdown_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await shutdown_task

            runner = self._runner
            self._runner = None
            self._site = None
            if runner is not None:
                await runner.cleanup()

            for ctx in list(self._workspaces.values()):
                with contextlib.suppress(Exception):
                    ctx.healing.close()
            self._workspaces.clear()

            with contextlib.suppress(Exception):
                self._fts_store.close()
            self._fts_initialized = False

            if was_running:
                with contextlib.suppress(Exception):
                    await self.driver.close()

            self.state_path.unlink(missing_ok=True)
            if self._wrote_token and self._auth_token is not None:
                try:
                    if self.token_path.is_file():
                        on_disk = self.token_path.read_text(encoding="utf-8").strip()
                        if on_disk == self._auth_token:
                            self.token_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._wrote_token = False

            self._get_stopped_event().set()

    async def wait_until_stopped(self) -> None:
        """Await daemon shutdown (via `/v1/shutdown`, idle timeout, or `stop()`)."""
        await self._get_stopped_event().wait()

    async def handle_evaluate(self, event: GateEvent) -> GateVerdict:
        """Route a canonical GateEvent through Gates 1-4 and return GateVerdict."""
        self._inflight_requests += 1
        self._last_activity_monotonic = time.monotonic()
        t0 = time.perf_counter()

        try:
            is_turn1_intake = event.gate == GateType.INTAKE and (
                event.turn_index == 1 or event.metadata.get("compact_reset") is True
            )
            ctx = await self._get_or_create_workspace(
                event.workspace_root,
                reindex_if_intake_turn1=is_turn1_intake,
            )
            verdict = await ctx.engine.evaluate(event)
            elapsed_ms = max(0.0, (time.perf_counter() - t0) * 1000.0)
            if verdict.latency_ms <= 0.0:
                verdict = verdict.model_copy(update={"latency_ms": round(elapsed_ms, 4)})
            self._record_stats(event, verdict, elapsed_ms)
            return verdict
        finally:
            self._inflight_requests = max(0, self._inflight_requests - 1)
            self._last_activity_monotonic = time.monotonic()

    async def health(self) -> dict[str, object]:
        """Return daemon liveness, uptime, and active driver metadata."""
        return self._health_payload()

    async def stats(self) -> dict[str, object]:
        """Return evaluation telemetry, latency percentiles, and SelfHealingEngine summary."""
        count = self._total_requests
        mean_ms = (self._latency_sum_ms / count) if count > 0 else 0.0
        samples = sorted(self._latencies_ms)
        if samples:
            p50_ms = float(statistics.median(samples))
            p95_idx = min(len(samples) - 1, max(0, int(0.95 * (len(samples) - 1))))
            p95_ms = float(samples[p95_idx])
        else:
            p50_ms = 0.0
            p95_ms = 0.0

        healing_totals: dict[str, Any] = {}
        healing_by_ws: dict[str, dict[str, Any]] = {}
        for ctx in self._workspaces.values():
            summary = ctx.healing.get_telemetry_summary()
            healing_by_ws[str(ctx.workspace_root)] = summary
            for key, val in summary.items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    healing_totals[key] = healing_totals.get(key, 0) + val
                elif key not in healing_totals:
                    healing_totals[key] = val

        return {
            "status": "ok" if self._running else "stopped",
            "version": DAEMON_VERSION,
            "port": self.bound_port,
            "uptime_s": round(self._uptime_s(), 3),
            "active_driver": self.driver.driver_id,
            "active_cartridge": self.cartridge.name,
            "total_requests": count,
            "request_count": count,
            "verdict_breakdown": dict(self._verdict_counts),
            "gate_breakdown": dict(self._gate_counts),
            "mean_latency_ms": round(mean_ms, 4),
            "p50_latency_ms": round(p50_ms, 4),
            "p95_latency_ms": round(p95_ms, 4),
            "workspaces_active": len(self._workspaces),
            "self_healing": {
                **healing_totals,
                "workspaces": healing_by_ws,
            },
        }


__all__ = [
    "DAEMON_VERSION",
    "DaemonConfig",
    "QuadGateDaemon",
    "build_driver_from_config",
    "load_cartridge_spec",
    "load_toml_config",
    "resolve_quadgate_home",
    "resolve_state_path",
    "resolve_token_path",
]
