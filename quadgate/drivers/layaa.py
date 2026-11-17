"""LAYAA open-source local classifier driver (ONNX weights or local HTTP endpoint)."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from quadgate.contracts import ClassifierRequest
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities
from quadgate.drivers.onnx_local import (
    extract_logits_from_outputs,
    load_pair_tokenizer,
    resolve_onnx_session,
    run_onnx_cross_encoder,
)


def _normalize_layaa_endpoint(endpoint: str | None) -> str | None:
    """Resolve explicit or `LAYAA_ENDPOINT` environment URL if non-empty."""
    raw = endpoint if endpoint is not None else os.environ.get("LAYAA_ENDPOINT")
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


def _build_classify_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.path in {"", "/"}:
        return f"{endpoint.rstrip('/')}/v1/classify"
    return endpoint


def _build_health_urls(classify_url: str) -> list[str]:
    parsed = urlsplit(classify_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [f"{base}/health", f"{base}/v1/health"]
    seen: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.append(url)
    return seen


def _extract_http_logits(data: Any, head_ids: list[str]) -> dict[str, float]:
    """Parse raw logits from common LAYAA HTTP JSON response schemas."""
    if isinstance(data, (bytes, bytearray)):
        data = json.loads(data.decode("utf-8"))
    elif isinstance(data, str):
        data = json.loads(data)

    n = len(head_ids)
    if isinstance(data, dict):
        for key in ("scores", "logits"):
            if key in data:
                val = data[key]
                if isinstance(val, dict):
                    missing = [hid for hid in head_ids if hid not in val]
                    if missing:
                        raise ValueError(f"LAYAA endpoint omitted head_ids: {missing}")
                    return {hid: float(val[hid]) for hid in head_ids}
                if isinstance(val, list):
                    logits = extract_logits_from_outputs(val, n)
                    return {hid: logits[idx] for idx, hid in enumerate(head_ids)}
        if "results" in data and isinstance(data["results"], list):
            return _extract_http_logits(data["results"], head_ids)
        raise ValueError(f"Unrecognized LAYAA HTTP response dict keys: {list(data.keys())}")

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            by_id: dict[str, float] = {}
            by_idx: dict[int, float] = {}
            for pos, item in enumerate(data):
                score_val = item.get("logit", item.get("score"))
                if score_val is None:
                    raise ValueError(f"LAYAA result item missing score/logit: {item!r}")
                if "head_id" in item:
                    by_id[str(item["head_id"])] = float(score_val)
                elif "index" in item:
                    by_idx[int(item["index"])] = float(score_val)
                else:
                    by_idx[pos] = float(score_val)
            if by_id:
                missing = [hid for hid in head_ids if hid not in by_id]
                if missing:
                    raise ValueError(f"LAYAA endpoint omitted head_ids: {missing}")
                return {hid: by_id[hid] for hid in head_ids}
            return {hid: by_idx[idx] for idx, hid in enumerate(head_ids)}

        logits = extract_logits_from_outputs(data, n)
        return {hid: logits[idx] for idx, hid in enumerate(head_ids)}

    raise ValueError(f"Unsupported LAYAA HTTP response type: {type(data).__name__}")


async def _invoke_http_transport(
    transport: Any,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout_s: float,
) -> tuple[int, Any]:
    """Invoke a custom/mock HTTP transport callable or client cleanly."""
    if callable(transport):
        sig = inspect.signature(transport)
        pos_params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if has_varargs or len(pos_params) >= 5:
            res = transport(method, url, headers, payload, timeout_s)
        elif len(pos_params) == 3:
            res = transport(method, url, payload)
        elif len(pos_params) == 2:
            res = transport(url, payload)
        else:
            res = transport(url)
    elif hasattr(transport, "request"):
        res = transport.request(method, url, headers=headers, json=payload, timeout=timeout_s)
    else:
        raise TypeError(f"Unsupported http_transport type: {type(transport).__name__}")

    if inspect.isawaitable(res):
        res = await res

    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], int):
        return int(res[0]), res[1]
    return 200, res


class LayaaDriver(ClassifierDriver):
    """Local ONNX weights or local HTTP server LAYAA classifier driver."""

    def __init__(
        self,
        model_path: str = "~/.quadgate/models/layaa-int8.onnx",
        device: str = "cpu",
        calibration_profile: CalibrationProfile | None = None,
        *,
        endpoint: str | None = None,
        session_factory: Any = None,
        tokenizer: Any = None,
        http_transport: Any = None,
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.endpoint = _normalize_layaa_endpoint(endpoint)
        self._session_factory = session_factory
        self._tokenizer = tokenizer
        self._http_transport = http_transport
        self._session: Any | None = None
        self._http_session: Any | None = None
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "layaa"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=False,
            supports_parallel_heads=False,
            max_candidates_per_pass=32,
            scores_are_logits=True,
        )

    @property
    def is_http_mode(self) -> bool:
        """Return True when operating against a local HTTP LAYAA endpoint."""
        return self.endpoint is not None

    @property
    def _resolved_model_path(self) -> Path:
        return Path(self.model_path).expanduser()

    @property
    def _resolved_tokenizer_path(self) -> Path:
        return self._resolved_model_path.parent / "tokenizer.json"

    def _get_session(self) -> tuple[Any | None, str | None]:
        if self._session is not None:
            return self._session, None
        session, reason = resolve_onnx_session(
            self._resolved_model_path,
            self.device,
            session_factory=self._session_factory,
        )
        if session is not None:
            self._session = session
        return session, reason

    def _get_tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._tokenizer = load_pair_tokenizer(None, self._resolved_tokenizer_path)
        return self._tokenizer

    def _get_http_session(self) -> Any:
        import aiohttp

        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(limit=32)
            self._http_session = aiohttp.ClientSession(connector=connector)
        return self._http_session

    async def _score_http(self, request: ClassifierRequest) -> dict[str, float]:
        assert self.endpoint is not None
        url = _build_classify_url(self.endpoint)
        head_ids = [head.head_id for head in request.heads]
        payload = {
            "query": request.query_text,
            "candidates": [head.candidate_text for head in request.heads],
            "head_ids": head_ids,
            "pairs": [[request.query_text, head.candidate_text] for head in request.heads],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout_s = max(0.05, request.timeout_ms / 1000.0)

        if self._http_transport is not None:
            status, body = await _invoke_http_transport(
                self._http_transport,
                "POST",
                url,
                headers,
                payload,
                timeout_s,
            )
            if status >= 400:
                raise RuntimeError(f"LAYAA endpoint returned HTTP {status}: {body!r}")
            return _extract_http_logits(body, head_ids)

        import aiohttp

        session = self._get_http_session()
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
            raw_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"LAYAA endpoint returned HTTP {resp.status}: {raw_text}")
            data = json.loads(raw_text)
        return _extract_http_logits(data, head_ids)

    async def _score_local_onnx(self, request: ClassifierRequest) -> dict[str, float]:
        session, missing_reason = self._get_session()
        if session is None:
            raise RuntimeError(
                f"LayaaDriver local ONNX mode unavailable: {missing_reason} "
                "Install `pip install quadgate[local]` with valid model weights or set LAYAA_ENDPOINT."
            )
        tokenizer = self._get_tokenizer()
        return await asyncio.to_thread(
            run_onnx_cross_encoder,
            session,
            tokenizer,
            request.query_text,
            request.heads,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        if len(request.heads) > self.capabilities.max_candidates_per_pass:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most "
                f"{self.capabilities.max_candidates_per_pass} heads per pass; "
                f"got {len(request.heads)}."
            )

        if self.is_http_mode:
            return await self._score_http(request)
        return await self._score_local_onnx(request)

    async def health_check(self) -> bool:
        if self.is_http_mode:
            assert self.endpoint is not None
            classify_url = _build_classify_url(self.endpoint)
            health_urls = _build_health_urls(classify_url)
            headers = {"Accept": "application/json"}
            try:
                if self._http_transport is not None:
                    for url in health_urls:
                        status, body = await _invoke_http_transport(
                            self._http_transport,
                            "GET",
                            url,
                            headers,
                            None,
                            2.0,
                        )
                        if status == 404:
                            continue
                        if status >= 400:
                            return False
                        if isinstance(body, dict):
                            if body.get("healthy") is False or body.get("ok") is False:
                                return False
                            if str(body.get("status", "")).lower() in {"error", "unhealthy", "down"}:
                                return False
                        return True
                    return False

                import aiohttp

                session = self._get_http_session()
                timeout = aiohttp.ClientTimeout(total=2.0)
                for url in health_urls:
                    async with session.get(url, headers=headers, timeout=timeout) as resp:
                        if resp.status == 404:
                            continue
                        if resp.status >= 400:
                            return False
                        text = await resp.text()
                        if text.strip():
                            try:
                                body = json.loads(text)
                                if isinstance(body, dict):
                                    if body.get("healthy") is False or body.get("ok") is False:
                                        return False
                                    if str(body.get("status", "")).lower() in {
                                        "error",
                                        "unhealthy",
                                        "down",
                                    }:
                                        return False
                            except json.JSONDecodeError:
                                pass
                        return True
                return False
            except Exception:
                return False

        try:
            session, _ = self._get_session()
            return session is not None
        except Exception:
            return False

    async def close(self) -> None:
        self._session = None
        if self._http_session is not None:
            if not self._http_session.closed:
                await self._http_session.close()
            self._http_session = None


__all__ = ["LayaaDriver"]
