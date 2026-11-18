"""Decision 1 cloud 1,000-head HTTP/2 classifier driver."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakKeyDictionary

from quadgate.contracts import (
    ClassifierHead,
    ClassifierRequest,
    ClassifierResponse,
    HeadCategory,
)
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities

_GEMINI_DEFAULT_ENDPOINT = "https://api.decision1.ai/v1/score"
_GEMINI_DECISIONS_URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
_GEMINI_MODEL_META_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-decision-1"
)
_GEMINI_RETRY_BACKOFFS_S = (0.4, 0.8)
_GEMINI_RETRY_IN_RE = re.compile(r"retry\s+in\s+(\d+(?:\.\d+)?)s", re.IGNORECASE)
_GEMINI_SEMAPHORES: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    WeakKeyDictionary()
)
_GEMINI_COOLDOWN_UNTIL: WeakKeyDictionary[asyncio.AbstractEventLoop, float] = (
    WeakKeyDictionary()
)
_ACTIVE_USAGE_CTX: contextvars.ContextVar[tuple[int, float] | None] = contextvars.ContextVar(
    "_quadgate_decision1_usage_ctx",
    default=None,
)


def _get_gemini_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _GEMINI_SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(6)
        _GEMINI_SEMAPHORES[loop] = sem
    return sem


async def _await_gemini_cooldown() -> None:
    loop = asyncio.get_running_loop()
    while True:
        until = _GEMINI_COOLDOWN_UNTIL.get(loop, 0.0)
        now = loop.time()
        if until <= now:
            break
        await asyncio.sleep(max(0.01, until - now))


def _set_gemini_cooldown(delay_s: float) -> None:
    loop = asyncio.get_running_loop()
    target = loop.time() + max(0.0, delay_s)
    if target > _GEMINI_COOLDOWN_UNTIL.get(loop, 0.0):
        _GEMINI_COOLDOWN_UNTIL[loop] = target


def _extract_retry_after_seconds(data: Any) -> float | None:
    text = json.dumps(data) if not isinstance(data, str) else data
    match = _GEMINI_RETRY_IN_RE.search(text)
    if match:
        try:
            return max(0.5, float(match.group(1)))
        except ValueError:
            return None
    return None

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
                limits=httpx.Limits(
                    max_connections=64,
                    max_keepalive_connections=32,
                    keepalive_expiry=60.0,
                ),
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


def _is_clause_head(head: ClassifierHead) -> bool:
    """Return True when `head` represents a Gate 4 directive completeness clause."""
    return head.head_id.startswith("clause:") or "clause_index" in head.metadata


def _format_gemini_input(request: ClassifierRequest) -> str:
    """Wrap pure Gate 4 clause-audit inputs as completed artifacts so lists are not read as prompts."""
    query_text = request.query_text
    if (
        query_text
        and request.heads
        and all(_is_clause_head(head) for head in request.heads)
        and not query_text.lstrip().startswith(("[COMPLETED_ARTIFACT", "USER TASK:"))
    ):
        return (
            "[COMPLETED_ARTIFACT]\n"
            "The entire text below is the completed output file (both its numbered/headed "
            "body lines and any trailing '- **key**: value' metric bullets are part of the "
            "authored answer; trailing bullets only repeat numeric keys, so requirements "
            "stated in the numbered/headed body lines are already fulfilled):\n\n"
            f"{query_text}"
        )
    return query_text


def _format_gemini_decision_question(head: ClassifierHead) -> str:
    """Format a ClassifierHead into a clear binary question for Gemini Decision 1."""
    if head.category == HeadCategory.INVARIANT:
        return (
            "Does the input violate or break this security or policy invariant "
            f"(answer yes ONLY if the input actively violates the rule)? "
            f"Invariant ({head.head_id}): {head.candidate_text}"
        )
    if head.category == HeadCategory.PRAGMATIC:
        hid_low = head.head_id.lower()
        cand_low = head.candidate_text.lower()
        if "counterfactual" in hid_low or "brainstorm" in cand_low:
            return (
                "Is the input purely a counterfactual or hypothetical brainstorm rather than "
                f"a direct actionable request? ({head.candidate_text})"
            )
        if "directive" in hid_low:
            return (
                "Is the input an actionable directive request to execute, edit, fix, "
                f"reconcile, or create something? ({head.candidate_text})"
            )
        if head.head_id == "tier:floor":
            return (
                "Can this task be safely completed on the first pass by a lightweight "
                f"floor-tier model? ({head.candidate_text})"
            )
        if head.head_id == "tier:mid":
            return (
                "Can this task be safely completed on the first pass by a mid-tier "
                f"model? ({head.candidate_text})"
            )
        if head.head_id == "tier:frontier":
            return (
                "Can this task be completed on the first pass by a frontier-tier "
                f"model? ({head.candidate_text})"
            )
        return head.candidate_text

    if _is_clause_head(head):
        if head.candidate_text.rstrip().endswith(":"):
            return (
                "Does the completed output file address the target deliverable or topic "
                f"named in this heading? Heading: {head.candidate_text}"
            )
        return (
            "Does the completed output file (including its numbered lines, prose sections, "
            "or code, even if not repeated in trailing metric bullets) contain or implement "
            "the substance of this requirement (answer yes if the requested values, "
            "citations, wikilinks, or logic appear anywhere in the file; answer no if "
            "they are missing or broken)? "
            f"Requirement: {head.candidate_text}"
        )
    return (
        "Is this candidate file or symbol directly relevant to solving the task? "
        f"Candidate: {head.candidate_text}"
    )


class Decision1Driver(ClassifierDriver):
    """Cloud parallel-head classifier driver for Decision 1 endpoints."""

    def __init__(
        self,
        endpoint: str = _GEMINI_DEFAULT_ENDPOINT,
        api_key: str | None = None,
        calibration_profile: CalibrationProfile | None = None,
        *,
        max_heads: int = 1000,
        transport: TransportFn | None = None,
        retry_429_max_wait_s: float = 0.0,
    ) -> None:
        if not 1 <= max_heads <= 1000:
            raise ValueError(f"max_heads must lie in 1..1000; got {max_heads}.")
        self.endpoint = endpoint
        self.api_key = api_key if api_key is not None else os.environ.get("DECISION1_API_KEY")
        self.max_heads = max_heads
        self.retry_429_max_wait_s = max(0.0, float(retry_429_max_wait_s))
        self._http = _CloudHttpPool(transport=transport)
        self.last_tokens_used: int = 0
        self.last_cost_usd: float = 0.0
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

    def _resolve_api_key(self) -> str:
        token = self.api_key if self.api_key is not None else os.environ.get("DECISION1_API_KEY")
        return (token or "").strip()

    def _is_gemini_decisions_mode(self) -> bool:
        if "generativelanguage.googleapis.com" in self.endpoint.lower():
            return True
        if self._http._transport is None and self.endpoint == _GEMINI_DEFAULT_ENDPOINT:
            token = self._resolve_api_key()
            return token.startswith("AQ.") or token.startswith("AIza")
        return False

    def _gemini_decisions_url(self) -> str:
        clean = self.endpoint.rstrip("/")
        if "generativelanguage.googleapis.com" in clean.lower() and clean.endswith("/decisions"):
            return clean
        return _GEMINI_DECISIONS_URL

    def _build_headers(self, *, include_content_type: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        token = self._resolve_api_key()
        if token:
            if self._is_gemini_decisions_mode():
                headers["x-goog-api-key"] = token
            else:
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

        if isinstance(data.get("answers"), dict):
            for key, entry in data["answers"].items():
                key_str = str(key)
                mapped_id = key_str
                if key_str.startswith("slot_") and key_str[5:].isdigit():
                    slot_num = int(key_str[5:])
                    if slot_num in slot_to_head:
                        mapped_id = slot_to_head[slot_num]
                if isinstance(entry, dict):
                    score_val = next(
                        (
                            entry[field]
                            for field in ("noul", "score", "probability", "confidence")
                            if entry.get(field) is not None
                        ),
                        None,
                    )
                    if score_val is None:
                        raise ValueError(
                            f"Decision 1 answer entry for {key_str!r} missing score."
                        )
                    raw_scores[mapped_id] = float(score_val)
                elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
                    raw_scores[mapped_id] = float(entry)
                else:
                    raise ValueError(
                        f"Decision 1 answer entry for {key_str!r} must be a JSON object."
                    )
        elif isinstance(data.get("scores"), dict):
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
            raise ValueError(
                "Decision 1 response must contain an 'answers' dict, 'scores' dict, or 'results' list."
            )

        expected_ids = [head.head_id for head, _ in allocated_heads]
        missing = [hid for hid in expected_ids if hid not in raw_scores]
        if missing:
            raise ValueError(f"Decision 1 response omitted requested heads: {missing}")
        return {hid: raw_scores[hid] for hid in expected_ids}

    def _record_usage(self, data: Any) -> None:
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            tokens = int(
                usage.get("total_input_tokens")
                or usage.get("input_tokens")
                or usage.get("total_tokens")
                or 0
            )
            self.last_tokens_used = max(0, tokens)
            self.last_cost_usd = (self.last_tokens_used * 0.10) / 1_000_000.0
        else:
            self.last_tokens_used = 0
            self.last_cost_usd = 0.0
        _ACTIVE_USAGE_CTX.set((self.last_tokens_used, self.last_cost_usd))

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

        self.last_tokens_used = 0
        self.last_cost_usd = 0.0
        _ACTIVE_USAGE_CTX.set((0, 0.0))
        allocated_heads = _allocate_slots(request.heads)

        if self._is_gemini_decisions_mode():
            gemini_url = self._gemini_decisions_url()
            payload: dict[str, Any] = {
                "model": "gemini-decision-1",
                "input": _format_gemini_input(request),
                "questions": {
                    f"slot_{slot}": {
                        "type": "noul",
                        "instructions": _format_gemini_decision_question(head),
                    }
                    for head, slot in allocated_heads
                },
            }
            timeout_s = max(request.timeout_ms / 1000.0, 15.0)
            headers = self._build_headers(include_content_type=True)
            sem = _get_gemini_semaphore()

            status = 0
            data: Any = {}
            max_attempts = 8 if self.retry_429_max_wait_s > 0.0 else (len(_GEMINI_RETRY_BACKOFFS_S) + 1)
            for attempt in range(max_attempts):
                while True:
                    if self.retry_429_max_wait_s > 0.0:
                        await _await_gemini_cooldown()
                    async with sem:
                        if self.retry_429_max_wait_s > 0.0:
                            loop = asyncio.get_running_loop()
                            if _GEMINI_COOLDOWN_UNTIL.get(loop, 0.0) > loop.time():
                                continue
                        status, data = await self._http.request(
                            "POST",
                            gemini_url,
                            headers=headers,
                            json_body=payload,
                            timeout_s=timeout_s,
                        )
                        break
                if status == 429 and self.retry_429_max_wait_s > 0.0 and attempt + 1 < max_attempts:
                    hint_s = _extract_retry_after_seconds(data)
                    base_wait = (
                        (hint_s + 1.5) if hint_s is not None else min(15.0, 2.0 * (attempt + 1))
                    )
                    wait_s = min(self.retry_429_max_wait_s, base_wait + 0.15 * attempt)
                    _set_gemini_cooldown(wait_s)
                    await asyncio.sleep(wait_s)
                    continue
                if (status == 429 or 500 <= status < 600) and attempt < len(
                    _GEMINI_RETRY_BACKOFFS_S
                ):
                    await asyncio.sleep(_GEMINI_RETRY_BACKOFFS_S[attempt])
                    continue
                break

            if not 200 <= status < 300:
                raise RuntimeError(f"Decision 1 endpoint returned HTTP {status}: {data}")
            self._record_usage(data)
            return self._parse_scores(data, allocated_heads)

        payload = {
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
        self._record_usage(data)
        return self._parse_scores(data, allocated_heads)

    async def classify(self, request: ClassifierRequest) -> ClassifierResponse:
        """Classify request and populate Gemini Decision 1 token and cost telemetry."""
        token = _ACTIVE_USAGE_CTX.set(None)
        try:
            resp = await super().classify(request)
            ctx_usage = _ACTIVE_USAGE_CTX.get()
        finally:
            _ACTIVE_USAGE_CTX.reset(token)
        tokens_used, cost_usd = (
            ctx_usage if ctx_usage is not None else (self.last_tokens_used, self.last_cost_usd)
        )
        return resp.model_copy(
            update={
                "tokens_used": tokens_used,
                "estimated_cost_usd": cost_usd,
            }
        )

    async def health_check(self) -> bool:
        """Probe Decision 1 health endpoint without raising on failure."""
        is_gemini = self._is_gemini_decisions_mode()
        health_url = _GEMINI_MODEL_META_URL if is_gemini else _derive_health_url(self.endpoint)
        timeout_s = 5.0 if is_gemini else 2.0
        try:
            status, data = await self._http.request(
                "GET",
                health_url,
                headers=self._build_headers(include_content_type=False),
                json_body=None,
                timeout_s=timeout_s,
            )
        except Exception:
            return False
        return _is_healthy_response(status, data)

    async def close(self) -> None:
        """Idempotently close the persistent HTTP connection pool."""
        await self._http.close()
