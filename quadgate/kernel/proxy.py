"""Optional cold-session LLM API reverse proxy (`127.0.0.1:8788`)."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from quadgate.contracts import CartridgeSpec, GateEvent, GateType, HarnessType
from quadgate.drivers.onnx_local import ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.kernel.daemon import load_cartridge_spec
from quadgate.kernel.gates import GateEngine, TierModelConfig

UpstreamHandler = Callable[..., Any | Awaitable[Any]]

_WARM_ROLES = frozenset({"assistant", "tool", "function"})
_COLD_PREFIX_ROLES = frozenset({"system", "developer"})
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
_FILE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}\b")


def _extract_content_text(content: object) -> tuple[str, bool]:
    """Extract text from string or block-list message content and flag tool blocks."""
    if isinstance(content, str):
        return content, False
    if isinstance(content, list):
        chunks: list[str] = []
        has_tool_block = False
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, Mapping):
                block_type = str(item.get("type", "")).strip().lower()
                if block_type in {"tool_use", "tool_result", "function_call"}:
                    has_tool_block = True
                text_val = item.get("text")
                if isinstance(text_val, str):
                    chunks.append(text_val)
                elif isinstance(item.get("content"), str):
                    chunks.append(str(item["content"]))
        return "\n".join(chunks), has_tool_block
    return "", False


def _extract_file_references(text: str) -> list[str]:
    """Extract unique relative-looking file references from prompt text."""
    seen: list[str] = []
    for match in _FILE_REF_RE.finditer(text):
        cleaned = match.group(0).replace("\\", "/").lstrip("./")
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen[:32]


def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _strip_hop_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        str(k): str(v)
        for k, v in headers.items()
        if str(k).lower() not in _HOP_BY_HOP_HEADERS
    }


def _join_upstream_url(upstream_base_url: str, path_qs: str) -> str:
    """Safely join configured upstream base URL with incoming request path+query."""
    base_parts = urlsplit(upstream_base_url.rstrip("/"))
    req_parts = urlsplit(path_qs)
    base_path = base_parts.path.rstrip("/")
    req_path = req_parts.path if req_parts.path.startswith("/") else f"/{req_parts.path}"

    if base_path.endswith("/v1") and req_path.startswith("/v1/"):
        merged_path = f"{base_path}{req_path[3:]}"
    elif base_path.endswith("/v1") and req_path == "/v1":
        merged_path = base_path
    else:
        merged_path = f"{base_path}{req_path}" if base_path else req_path

    return urlunsplit((base_parts.scheme, base_parts.netloc, merged_path, req_parts.query, ""))


class ColdSessionProxy:
    """Localhost HTTP reverse proxy applying Gate 3 routing solely on cold single-turn calls."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8788,
        *,
        upstream_base_url: str | None = None,
        upstream_handler: UpstreamHandler | None = None,
        gate_engine: GateEngine | None = None,
        workspace_root: Path | str | None = None,
        tiers: TierModelConfig | None = None,
        cartridge: CartridgeSpec | str | Path | None = None,
        request_timeout_s: float = 120.0,
    ) -> None:
        self.host = host
        self.port = port
        self.bound_port: int = port
        self.upstream_base_url = (
            upstream_base_url
            or os.environ.get("QUADGATE_UPSTREAM_URL")
            or os.environ.get("OPENAI_UPSTREAM_BASE_URL")
            or "https://api.openai.com"
        )
        self._upstream_handler = upstream_handler
        self._gate_engine = gate_engine
        self._workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self._tiers = tiers or TierModelConfig()
        self._cartridge_arg = cartridge
        self._request_timeout_s = float(request_timeout_s)

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._running: bool = False
        self._lifecycle_lock: asyncio.Lock | None = None

        self.total_requests: int = 0
        self.cold_routed_requests: int = 0
        self.warm_bypassed_requests: int = 0

    @property
    def base_url(self) -> str:
        """Return the base HTTP URL for the bound reverse proxy."""
        return f"http://{self.host}:{self.bound_port}"

    @property
    def is_running(self) -> bool:
        """Return True while the reverse proxy HTTP listener is active."""
        return self._running

    def _get_lifecycle_lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _get_gate_engine(self) -> GateEngine:
        if self._gate_engine is None:
            cartridge = load_cartridge_spec(self._cartridge_arg)
            indexer = AstGistIndexer(self._workspace_root, cartridge=cartridge)
            if self._workspace_root.exists():
                indexer.index_workspace(incremental=True)
            self._gate_engine = GateEngine(
                cartridge,
                tiers=self._tiers,
                driver=ZeroDepDriver(),
                indexer=indexer,
            )
        return self._gate_engine

    def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(limit=64)
            timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)
            self._http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._http_session

    @staticmethod
    def _is_cold_single_turn(
        payload: Mapping[str, object],
        norm_headers: Mapping[str, str],
    ) -> tuple[bool, str, str]:
        """Strictly distinguish cold single-turn worker requests from warm multi-turn sessions."""
        if norm_headers.get("x-quadgate-preserve-model", "").strip().lower() in {"1", "true", "yes"}:
            return False, "", ""

        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list) or len(raw_messages) == 0:
            return False, "", ""
        if len(raw_messages) > 2:
            return False, "", ""

        roles: list[str] = []
        user_count = 0
        user_text = ""
        system_chunks: list[str] = []

        top_system = payload.get("system")
        if top_system is not None:
            sys_txt, sys_tool = _extract_content_text(top_system)
            if sys_tool:
                return False, "", ""
            if sys_txt:
                system_chunks.append(sys_txt)

        for msg in raw_messages:
            if not isinstance(msg, Mapping):
                return False, "", ""
            role = str(msg.get("role", "")).strip().lower()
            if not role or role in _WARM_ROLES:
                return False, "", ""
            if msg.get("tool_calls") or msg.get("function_call"):
                return False, "", ""

            msg_text, has_tool_block = _extract_content_text(msg.get("content", ""))
            if has_tool_block:
                return False, "", ""

            roles.append(role)
            if role == "user":
                user_count += 1
                user_text = msg_text
            elif role in _COLD_PREFIX_ROLES:
                if msg_text:
                    system_chunks.append(msg_text)
            else:
                return False, "", ""

        if user_count != 1:
            return False, "", ""
        if len(roles) == 1 and roles[0] == "user":
            return True, user_text, "\n".join(system_chunks)
        if len(roles) == 2 and roles[0] in _COLD_PREFIX_ROLES and roles[1] == "user":
            return True, user_text, "\n".join(system_chunks)
        return False, "", ""

    async def route_request_model(
        self,
        payload: dict[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Inspect request payload and rewrite model strictly on cold single-turn worker calls."""
        norm_headers = _normalize_headers(headers)
        is_cold, user_text, system_text = self._is_cold_single_turn(payload, norm_headers)
        if not is_cold:
            self.warm_bypassed_requests += 1
            return dict(payload)

        engine = self._get_gate_engine()
        workspace_root = norm_headers.get("x-quadgate-workspace") or str(self._workspace_root)
        session_id = (
            norm_headers.get("x-session-id")
            or norm_headers.get("x-quadgate-session-id")
            or f"proxy-{uuid.uuid4().hex[:12]}"
        )
        orig_model = payload.get("model")
        prompt_text = user_text.strip() or system_text.strip() or "worker task"

        event = GateEvent(
            gate=GateType.SUBAGENT_GOVERNOR,
            session_id=session_id,
            turn_index=1,
            workspace_root=workspace_root,
            harness=HarnessType.CODEX_CLI,
            tool_name="llm_proxy",
            tool_args={"model": orig_model} if orig_model is not None else {},
            prompt_text=prompt_text,
            touched_files=_extract_file_references(prompt_text),
            metadata={
                "via_proxy": True,
                "original_model": orig_model,
                "message_count": len(payload.get("messages", [])),  # type: ignore[arg-type]
                **({"system_prompt": system_text} if system_text else {}),
            },
        )
        verdict = await engine.evaluate_gate3_subagent(event)
        selected_model = verdict.updated_model
        if not selected_model and verdict.target_tier is not None:
            selected_model = getattr(engine.tiers, verdict.target_tier.value, None)

        self.cold_routed_requests += 1
        updated = dict(payload)
        if selected_model:
            updated["model"] = selected_model
        return updated

    async def _invoke_upstream_handler(
        self,
        request: web.Request,
        routed_payload: dict[str, object],
    ) -> web.StreamResponse:
        assert self._upstream_handler is not None
        handler = self._upstream_handler
        req_headers = dict(request.headers)

        sig = inspect.signature(handler)
        pos_params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if has_varargs or len(pos_params) >= 4:
            res = handler(request.method, request.path_qs, req_headers, routed_payload)
        elif len(pos_params) == 3:
            res = handler(request.path_qs, req_headers, routed_payload)
        elif len(pos_params) == 2:
            res = handler(routed_payload, req_headers)
        elif len(pos_params) == 1:
            res = handler(routed_payload)
        else:
            res = handler()

        if inspect.isawaitable(res):
            res = await res

        if isinstance(res, web.StreamResponse):
            return res
        if isinstance(res, tuple) and len(res) == 3:
            status, resp_headers, body = res
            clean_headers = _strip_hop_headers(resp_headers or {})
            if isinstance(body, (dict, list)):
                return web.json_response(body, status=int(status), headers=clean_headers)
            if isinstance(body, (bytes, bytearray)):
                return web.Response(body=bytes(body), status=int(status), headers=clean_headers)
            return web.Response(text=str(body), status=int(status), headers=clean_headers)
        if isinstance(res, tuple) and len(res) == 2:
            status, body = res
            if isinstance(body, (dict, list)):
                return web.json_response(body, status=int(status))
            if isinstance(body, (bytes, bytearray)):
                return web.Response(body=bytes(body), status=int(status))
            return web.Response(text=str(body), status=int(status))
        if isinstance(res, (dict, list)):
            return web.json_response(res, status=200)
        if isinstance(res, (bytes, bytearray)):
            return web.Response(body=bytes(res), status=200, content_type="application/json")
        return web.Response(text=str(res), status=200, content_type="application/json")

    async def _forward_live_upstream(
        self,
        request: web.Request,
        routed_payload: dict[str, object],
    ) -> web.StreamResponse:
        target_url = _join_upstream_url(self.upstream_base_url, request.path_qs)
        forward_headers = _strip_hop_headers(request.headers)
        forward_headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(routed_payload, separators=(",", ":")).encode("utf-8")

        session = self._get_http_session()
        async with session.request(
            request.method,
            target_url,
            headers=forward_headers,
            data=body_bytes,
        ) as upstream_resp:
            out_headers = _strip_hop_headers(upstream_resp.headers)
            content_type = upstream_resp.headers.get("Content-Type", "").lower()
            if "text/event-stream" in content_type:
                stream = web.StreamResponse(status=upstream_resp.status, headers=out_headers)
                await stream.prepare(request)
                async for chunk in upstream_resp.content.iter_any():
                    if chunk:
                        await stream.write(chunk)
                await stream.write_eof()
                return stream

            resp_body = await upstream_resp.read()
            return web.Response(
                body=resp_body,
                status=upstream_resp.status,
                headers=out_headers,
            )

    async def _handle_proxy_post(self, request: web.Request) -> web.StreamResponse:
        self.total_requests += 1
        try:
            raw_body = await request.read()
            payload = json.loads(raw_body)
        except Exception as exc:
            return web.json_response({"error": f"Invalid JSON payload: {exc}"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Request body must be a JSON object"}, status=400)

        routed_payload = await self.route_request_model(payload, headers=request.headers)
        if self._upstream_handler is not None:
            return await self._invoke_upstream_handler(request, routed_payload)
        return await self._forward_live_upstream(request, routed_payload)

    async def _handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "status": "ok",
                "port": self.bound_port,
                "total_requests": self.total_requests,
                "cold_routed_requests": self.cold_routed_requests,
                "warm_bypassed_requests": self.warm_bypassed_requests,
            },
            status=200,
        )

    async def start(self) -> None:
        """Start the reverse proxy listener."""
        async with self._get_lifecycle_lock():
            if self._running:
                return
            app = web.Application()
            app.router.add_post("/v1/chat/completions", self._handle_proxy_post)
            app.router.add_post("/chat/completions", self._handle_proxy_post)
            app.router.add_post("/v1/messages", self._handle_proxy_post)
            app.router.add_post("/messages", self._handle_proxy_post)
            app.router.add_get("/v1/health", self._handle_health)
            app.router.add_get("/health", self._handle_health)

            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            try:
                site = web.TCPSite(runner, self.host, self.port)
                await site.start()
            except Exception:
                await runner.cleanup()
                raise

            self._runner = runner
            self._site = site
            addresses = runner.addresses
            self.bound_port = int(addresses[0][1]) if addresses else self.port
            self._running = True

    async def stop(self) -> None:
        """Stop the reverse proxy listener and close pooled HTTP sessions."""
        async with self._get_lifecycle_lock():
            self._running = False
            runner = self._runner
            self._runner = None
            self._site = None
            if runner is not None:
                await runner.cleanup()

            session = self._http_session
            self._http_session = None
            if session is not None and not session.closed:
                await session.close()

            if self._gate_engine is not None:
                drv = getattr(self._gate_engine, "driver", None)
                if drv is not None and hasattr(drv, "close"):
                    with contextlib.suppress(Exception):
                        await drv.close()


__all__ = ["ColdSessionProxy", "UpstreamHandler"]
