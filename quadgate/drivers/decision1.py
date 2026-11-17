"""Decision 1 cloud 1,000-head HTTP/2 classifier driver."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities

TransportFn = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, float],
    Awaitable[tuple[int, Any]],
]

_SLOT_BANDS: dict[HeadCategory, tuple[int, int]] = {
    HeadCategory.INVARIANT: (1, 80),
    HeadCategory.PRAGMATIC: (81, 160),
    HeadCategory.CANDIDATE: (161, 1000),
}


class _CloudHttpPool:
    """Lazy persistent HTTP/2 (httpx + h2) / aiohttp connection pool with replay hook."""

    def __init__(self, transport: TransportFn | None = None) -> None:
        self._transport = transport
        self._client: Any = None
        self._backend: str | None = None

    @property
    def client(self) -> Any:
        """Return the active underlying HTTP client/session if created."""
        return self._client

    def _ensure_client(self) -> tuple[str, Any]:
        if self._client is not None:
            if self._backend == "httpx" and not getattr(self._client, "is_closed", False):
                return "httpx", self._client
            if self._backend == "aiohttp" and not getattr(self._client, "closed", False):
                return "aiohttp", self._client
            self._client = None
            self._backend = None

        try:
            import h2  # noqa: F401
            import httpx
        except ImportError:
            httpx = None  # type: ignore[assignment]

        if httpx is not None:
            self._client = httpx.AsyncClient(
                http2=True,
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            )
            self._backend = "httpx"
            return "httpx", self._client

        import aiohttp

        self._client = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=64))
        self._backend = "aiohttp"
        return "aiohttp", self._client

    @staticmethod
    def _decode_body(text: str) -> Any:
        stripped = text.strip()
        if not stripped:
            return {}
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return {"_raw_text": text}

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, Any]:
        """Execute an HTTP request via replay transport or persistent connection pool."""
        if self._transport is not None:
            result = self._transport(method, url, headers, json_body, timeout_s)
            if inspect.isawaitable(result):
                status, payload = await result
            else:
                status, payload = result
            return int(status), payload

        backend, client = self._ensure_client()
        if backend == "httpx":
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout_s,
            )
            return int(response.status_code), self._decode_body(response.text)

        import aiohttp

        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with client.request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=timeout,
        ) as response:
            text = await response.text()
            return int(response.status), self._decode_body(text)

    async def close(self) -> None:
        """Idempotently close the underlying pooled HTTP client or session."""
        client = self._client
        backend = self._backend
        self._client = None
        self._backend = None
        if client is None:
            return
        if backend == "httpx" or hasattr(client, "aclose"):
            if not getattr(client, "is_closed", False):
                await client.aclose()
        elif hasattr(client, "close"):
            if not getattr(client, "closed", False):
                await client.close()


def _derive_health_url(
    endpoint: str,
    *,
    suffix_replacements: tuple[str, ...] = ("/score", "/classify", "/rerank"),
) -> str:
    """Derive a GET health-check URL from a scoring endpoint."""
    parts = urlsplit(endpoint)
    path = parts.path.rstrip("/")
    if not path:
        health_path = "/health"
    elif path.endswith("/health"):
        health_path = path
    else:
        health_path = f"{path}/health"
        for suffix in suffix_replacements:
            if path.endswith(suffix):
                prefix = path[: -len(suffix)]
                health_path = f"{prefix}/health" if prefix else "/health"
                break
    return urlunsplit((parts.scheme, parts.netloc, health_path, "", ""))


def _is_healthy_response(status: int, data: Any) -> bool:
    """Interpret HTTP status and optional status JSON for health_check()."""
    if not 200 <= status < 300:
        return False
    if isinstance(data, dict):
        if data.get("healthy") is False or data.get("ok") is False:
            return False
        status_val = str(data.get("status", "")).strip().lower()
        if status_val in {"unhealthy", "error", "down", "fail", "degraded"}:
            return False
    return True


def _allocate_slots(heads: list[ClassifierHead]) -> list[tuple[ClassifierHead, int]]:
    """Enforce Decision 1's 3-band slot layout across invariant, pragmatic, and candidate heads."""
    counts = {category: 0 for category in _SLOT_BANDS}
    for head in heads:
        counts[head.category] += 1

    for category, (start, end) in _SLOT_BANDS.items():
        capacity = end - start + 1
        if counts[category] > capacity:
            raise ValueError(
                f"Decision 1 allows at most {capacity} {category.value} heads "
                f"(slots {start}..{end}); got {counts[category]}."
            )

    used_slots: set[int] = set()
    assigned: dict[str, int] = {}

    # Pass 1: validate and reserve explicit slot assignments within category bands.
    for head in heads:
        if head.slot_index is None:
            continue
        start, end = _SLOT_BANDS[head.category]
        if not start <= head.slot_index <= end:
            raise ValueError(
                f"Head {head.head_id!r} in category {head.category.value!r} requires "
                f"slot_index in {start}..{end}; got {head.slot_index}."
            )
        if head.slot_index in used_slots:
            raise ValueError(
                f"Duplicate slot_index {head.slot_index} assigned to head {head.head_id!r}."
            )
        used_slots.add(head.slot_index)
        assigned[head.head_id] = head.slot_index

    # Pass 2: pack remaining heads sequentially into unused slots of their band.
    cursors = {category: start for category, (start, _) in _SLOT_BANDS.items()}
    result: list[tuple[ClassifierHead, int]] = []
    for head in heads:
        slot = assigned.get(head.head_id)
        if slot is None:
            start, end = _SLOT_BANDS[head.category]
            cursor = cursors[head.category]
            while cursor in used_slots:
                cursor += 1
            if cursor > end:
                raise ValueError(
                    f"No available slot remaining in {head.category.value!r} band {start}..{end}."
                )
            slot = cursor
            used_slots.add(slot)
            cursors[head.category] = cursor + 1
        result.append((head, slot))
    return result


class Decision1Driver(ClassifierDriver):
    """Cloud parallel-head classifier driver for Decision 1 endpoints."""

    def __init__(
        self,
        endpoint: str = "https://api.decision1.ai/v1/score",
        api_key: str | None = None,
        calibration_profile: CalibrationProfile | None = None,
        *,
        max_heads: int = 1000,
        transport: TransportFn | None = None,
    ) -> None:
        if not 1 <= max_heads <= 1000:
            raise ValueError(f"max_heads must lie in 1..1000; got {max_heads}.")
        self.endpoint = endpoint
        self.api_key = api_key if api_key is not None else os.environ.get("DECISION1_API_KEY")
        self.max_heads = max_heads
        self._http = _CloudHttpPool(transport=transport)
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "decision1"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=True,
            max_candidates_per_pass=self.max_heads,
            scores_are_logits=False,
        )

    @property
    def _client(self) -> Any:
        """Expose underlying pooled client for diagnostics and lifecycle verification."""
        return self._http.client

    def _build_headers(self, *, include_content_type: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        token = self.api_key if self.api_key is not None else os.environ.get("DECISION1_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _parse_scores(
        data: Any,
        allocated_heads: list[tuple[ClassifierHead, int]],
    ) -> dict[str, float]:
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected Decision 1 response payload type: {type(data).__name__}")

        slot_to_head = {slot: head.head_id for head, slot in allocated_heads}
        raw_scores: dict[str, float] = {}

        if isinstance(data.get("scores"), dict):
            for key, value in data["scores"].items():
                raw_scores[str(key)] = float(value)
        elif isinstance(data.get("results"), list):
            for entry in data["results"]:
                if not isinstance(entry, dict):
                    raise ValueError("Decision 1 results entries must be JSON objects.")
                head_id = entry.get("head_id")
                if head_id is None and "slot" in entry:
                    slot = int(entry["slot"])
                    if slot not in slot_to_head:
                        raise ValueError(f"Decision 1 response returned unknown slot {slot}.")
                    head_id = slot_to_head[slot]
                if head_id is None:
                    raise ValueError("Decision 1 result entry missing both 'head_id' and 'slot'.")
                score_val = entry.get("score", entry.get("probability", entry.get("prob")))
                if score_val is None:
                    raise ValueError(f"Decision 1 result entry for {head_id!r} missing score.")
                raw_scores[str(head_id)] = float(score_val)
        else:
            raise ValueError("Decision 1 response must contain a 'scores' dict or 'results' list.")

        expected_ids = [head.head_id for head, _ in allocated_heads]
        missing = [hid for hid in expected_ids if hid not in raw_scores]
        if missing:
            raise ValueError(f"Decision 1 response omitted requested heads: {missing}")
        return {hid: raw_scores[hid] for hid in expected_ids}

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        """Batch all heads into a single zero-output Decision 1 discriminative pass."""
        if not request.cloud_egress_allowed:
            raise PermissionError(
                "Cloud classifier egress blocked by Pass-0 Tier-1 Privacy Air-Gap."
            )
        if len(request.heads) > self.max_heads:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most {self.max_heads} heads per pass; "
                f"got {len(request.heads)}."
            )

        allocated_heads = _allocate_slots(request.heads)
        payload: dict[str, Any] = {
            "query": request.query_text,
            "max_output_tokens": 0,
            "heads": [
                {
                    "slot": slot,
                    "head_id": head.head_id,
                    "category": head.category.value,
                    "text": head.candidate_text,
                    **({"metadata": head.metadata} if head.metadata else {}),
                }
                for head, slot in allocated_heads
            ],
        }
        status, data = await self._http.request(
            "POST",
            self.endpoint,
            headers=self._build_headers(include_content_type=True),
            json_body=payload,
            timeout_s=request.timeout_ms / 1000.0,
        )
        if not 200 <= status < 300:
            raise RuntimeError(f"Decision 1 endpoint returned HTTP {status}: {data}")
        return self._parse_scores(data, allocated_heads)

    async def health_check(self) -> bool:
        """Probe Decision 1 health endpoint without raising on failure."""
        health_url = _derive_health_url(self.endpoint)
        try:
            status, data = await self._http.request(
                "GET",
                health_url,
                headers=self._build_headers(include_content_type=False),
                json_body=None,
                timeout_s=2.0,
            )
        except Exception:
            return False
        return _is_healthy_response(status, data)

    async def close(self) -> None:
        """Idempotently close the persistent HTTP connection pool."""
        await self._http.close()
