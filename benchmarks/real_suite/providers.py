"""Live multi-model LLM & Decision 1 client with exact token/cost telemetry."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

import aiohttp

from quadgate.contracts import ClassifierRequest, ClassifierResponse
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities
from quadgate.drivers.decision1 import Decision1Driver

LIVE_MODEL_PRICING: dict[str, dict[str, Any]] = {
    "gemma-4-31b-it": {
        "provider": "google",
        "display_name": "Gemma 4 31B IT",
        "tier_label": "Open-Weights 31B",
        "is_frontier": False,
        "input": 0.0,
        "cache_read": 0.0,
        "output": 0.0,
        "self_hosted_equiv_input": 0.10,
        "self_hosted_equiv_output": 0.30,
        "concurrency": 2,
        "supports_json_mime": False,
        "supports_system_instruction": True,
    },
    "gemini-3.1-flash-lite": {
        "provider": "google",
        "display_name": "Gemini 3.1 Flash-Lite",
        "tier_label": "Budget / Floor",
        "is_frontier": False,
        "input": 0.25,
        "cache_read": 0.025,
        "output": 1.50,
        "concurrency": 6,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-3.8-flash": {
        "provider": "google",
        "display_name": "Gemini 3.8 Flash",
        "tier_label": "Mid / Velocity",
        "is_frontier": False,
        "input": 0.75,
        "cache_read": 0.075,
        "output": 3.75,
        "concurrency": 5,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-4-hydrogen": {
        "provider": "google",
        "display_name": "Gemini 4 Hydrogen",
        "tier_label": "Frontier Efficiency",
        "is_frontier": True,
        "input": 1.50,
        "cache_read": 0.075,
        "output": 7.50,
        "concurrency": 4,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-3.1-pro-preview": {
        "provider": "google",
        "display_name": "Gemini 3.1 Pro",
        "tier_label": "Frontier 3.1 Pro",
        "is_frontier": True,
        "input": 2.00,
        "cache_read": 0.20,
        "output": 12.00,
        "concurrency": 4,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-4-carbon": {
        "provider": "google",
        "display_name": "Gemini 4 Carbon",
        "tier_label": "Frontier Flagship",
        "is_frontier": True,
        "input": 2.00,
        "cache_read": 0.10,
        "output": 10.00,
        "concurrency": 4,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-4-argon": {
        "provider": "google",
        "display_name": "Gemini 4 Argon",
        "tier_label": "Frontier Reasoning",
        "is_frontier": True,
        "input": 2.00,
        "cache_read": 0.10,
        "output": 10.00,
        "concurrency": 4,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
    "gemini-4-neon": {
        "provider": "google",
        "display_name": "Gemini 4 Neon",
        "tier_label": "Frontier Coding",
        "is_frontier": True,
        "input": 3.00,
        "cache_read": 0.15,
        "output": 15.00,
        "concurrency": 4,
        "supports_json_mime": True,
        "supports_system_instruction": True,
    },
}

BENCHMARK_MODELS: tuple[str, ...] = (
    "gemma-4-31b-it",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-3.1-pro-preview",
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-4-neon",
)

_RETRY_IN_RE = re.compile(r"retry\s+in\s+(\d+(?:\.\d+)?)s", re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)


def resolve_live_google_api_key() -> str:
    """Resolve the active Google Gemini/Decision1 key verified on generativelanguage API."""
    for env_name in ("DECISION1_API_KEY", "GEMINI_API_KEY"):
        val = os.environ.get(env_name, "").strip()
        if val and (env_name == "DECISION1_API_KEY" or not val.startswith("AQ.")):
            return val
    raise RuntimeError(
        "No valid Google API key found (expected DECISION1_API_KEY or non-AQ GEMINI_API_KEY)."
    )


def compute_google_call_cost_usd(
    model: str,
    uncached_input_tokens: int,
    cached_input_tokens: int,
    billable_output_tokens: int,
) -> float:
    """Compute exact USD cost using verified per-1M rates (including thoughts tokens)."""
    spec = LIVE_MODEL_PRICING.get(model)
    if not spec:
        return 0.0
    u_in = max(0, int(uncached_input_tokens))
    c_in = max(0, int(cached_input_tokens))
    out = max(0, int(billable_output_tokens))
    return (
        u_in * float(spec["input"])
        + c_in * float(spec["cache_read"])
        + out * float(spec["output"])
    ) / 1_000_000.0


class BudgetExceededError(RuntimeError):
    """Raised before dispatch when remaining spend headroom cannot cover a call."""


class BillingDepletedError(RuntimeError):
    """Raised immediately on HTTP 402 / payment-required errors to abort further calls."""


def _is_billing_402_error(status: int | None, body_text: str) -> bool:
    # Never inspect successful HTTP payloads (e.g. benchmark texts mentioning "billing" or "required").
    if status is not None and status < 400:
        return False
    if status == 402:
        return True
    if status is not None and status not in (400, 403):
        return False
    low = body_text.lower()
    return (
        "payment required" in low
        or "http 402" in low
        or "insufficient credit" in low
        or "insufficient balance" in low
        or ("billing" in low and ("disabled" in low or "delinquent" in low or "required" in low))
    )


def estimate_worst_case_call_usd(
    model: str,
    *,
    system_prompt: str | None,
    messages: list[dict[str, Any]],
    max_output_tokens: int,
) -> float:
    """Compute a conservative upper bound (USD) for pre-dispatch budget reservation."""
    spec = LIVE_MODEL_PRICING.get(model)
    if not spec:
        return 0.0
    total_bytes = len((system_prompt or "").encode("utf-8"))
    for msg in messages:
        total_bytes += len(str(msg.get("text", "")).encode("utf-8")) + 64
    # UTF-8 byte count + 512 framing tokens strictly upper-bounds BPE prompt tokens.
    est_input_tokens = max(512, total_bytes + 512)
    # 2x margin covers both candidatesTokenCount and any thoughtsTokenCount slack.
    est_output_tokens = max(1, int(max_output_tokens * 2.0) + 128)
    return (
        est_input_tokens * float(spec["input"])
        + est_output_tokens * float(spec["output"])
    ) / 1_000_000.0


class LiveBudgetGuard:
    """Thread-safe reservation & settlement ledger enforcing a hard USD cap and 402 breaker."""

    def __init__(self, max_spend_usd: float | None = None) -> None:
        self.max_spend_usd: float | None = (
            max(0.0, float(max_spend_usd)) if max_spend_usd is not None else None
        )
        self._lock = threading.Lock()
        self.spent_usd: float = 0.0
        self.reserved_usd: float = 0.0
        self.calls_committed: int = 0
        self.calls_rejected_budget: int = 0
        self.aborted_reason: str | None = None
        self.aborted_detail: str = ""
        self._next_res_id: int = 1
        self._reservations: dict[int, float] = {}

    def remaining_headroom_usd(self) -> float:
        """Return remaining unreserved budget in USD (inf if uncapped)."""
        with self._lock:
            if self.max_spend_usd is None:
                return float("inf")
            return max(0.0, self.max_spend_usd - (self.spent_usd + self.reserved_usd))

    def is_billing_aborted(self) -> bool:
        with self._lock:
            return self.aborted_reason == "billing_402"

    def check_before_job(self, min_headroom_usd: float = 0.0) -> None:
        """Verify guard is not billing-aborted and still has headroom >= min_headroom_usd."""
        with self._lock:
            if self.aborted_reason == "billing_402":
                raise BillingDepletedError(
                    self.aborted_detail or "HTTP 402 billing depletion circuit breaker tripped."
                )
            if self.max_spend_usd is None:
                return
            headroom = self.max_spend_usd - (self.spent_usd + self.reserved_usd)
            if headroom <= 1e-12 or headroom + 1e-12 < float(min_headroom_usd):
                raise BudgetExceededError(
                    f"Budget exhausted: spent=${self.spent_usd:.6f}, "
                    f"reserved=${self.reserved_usd:.6f}, cap=${self.max_spend_usd:.6f}"
                )

    def try_reserve(self, estimated_max_usd: float) -> int:
        """Atomically reserve worst-case cost before dispatching a live API call."""
        est = max(0.0, float(estimated_max_usd))
        with self._lock:
            if self.aborted_reason == "billing_402":
                raise BillingDepletedError(
                    self.aborted_detail or "HTTP 402 billing depletion circuit breaker tripped."
                )
            if self.max_spend_usd is not None:
                projected = self.spent_usd + self.reserved_usd + est
                if projected > self.max_spend_usd + 1e-12:
                    self.calls_rejected_budget += 1
                    raise BudgetExceededError(
                        f"Call reserve ${est:.6f} would exceed cap ${self.max_spend_usd:.6f} "
                        f"(spent=${self.spent_usd:.6f}, reserved=${self.reserved_usd:.6f})"
                    )
            res_id = self._next_res_id
            self._next_res_id += 1
            self._reservations[res_id] = est
            self.reserved_usd = sum(self._reservations.values())
            return res_id

    def settle(self, res_id: int, actual_usd: float) -> None:
        """Release reservation res_id and record actual USD cost atomically."""
        actual = max(0.0, float(actual_usd))
        with self._lock:
            self._reservations.pop(res_id, 0.0)
            self.reserved_usd = sum(self._reservations.values()) if self._reservations else 0.0
            self.spent_usd += actual
            self.calls_committed += 1

    def release(self, res_id: int) -> None:
        """Release reservation res_id with zero spend on aborted/unbilled failures."""
        with self._lock:
            self._reservations.pop(res_id, 0.0)
            self.reserved_usd = sum(self._reservations.values()) if self._reservations else 0.0

    def trip_billing_402(self, detail: str) -> None:
        """Latch instant circuit breaker on HTTP 402 / billing errors."""
        with self._lock:
            if self.aborted_reason != "billing_402":
                self.aborted_reason = "billing_402"
                self.aborted_detail = str(detail)[:300]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rem = (
                max(0.0, self.max_spend_usd - (self.spent_usd + self.reserved_usd))
                if self.max_spend_usd is not None
                else None
            )
            return {
                "max_spend_usd": self.max_spend_usd,
                "spent_usd": self.spent_usd,
                "reserved_usd": self.reserved_usd,
                "remaining_usd": rem,
                "calls_committed": self.calls_committed,
                "calls_rejected_budget": self.calls_rejected_budget,
                "aborted_reason": self.aborted_reason,
                "aborted_detail": self.aborted_detail,
            }


@dataclass(slots=True)
class LLMCallResult:
    """Structured telemetry & text output from a single live API call."""

    model: str
    text: str
    thought_text: str = ""
    uncached_input_tokens: int = 0
    cached_input_tokens: int = 0
    candidates_output_tokens: int = 0
    thoughts_output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = ""
    raw_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def total_input_tokens(self) -> int:
        return self.uncached_input_tokens + self.cached_input_tokens

    @property
    def billable_output_tokens(self) -> int:
        return self.candidates_output_tokens + self.thoughts_output_tokens


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Robustly extract a JSON object from raw model output (fences or surrounding prose)."""
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty model response when JSON object expected.")

    # Direct parse first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fenced blocks (prefer last valid JSON dict in case thought prose came first)
    blocks = _CODE_BLOCK_RE.findall(text)
    for blk in reversed(blocks):
        try:
            parsed = json.loads(blk.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # Balanced brace scan from the first '{' or last valid '{...}'
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        pos = text.find("{", idx)
        if pos == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(text, pos)
            if isinstance(obj, dict):
                candidates.append(obj)
            idx = max(pos + 1, end_idx)
        except json.JSONDecodeError:
            idx = pos + 1
    if candidates:
        return candidates[-1]
    raise ValueError(f"Could not extract JSON object from output: {text[:240]!r}")


def extract_python_code(raw_text: str) -> str:
    """Extract Python source code from model text (handles fenced code or Gemma reasoning)."""
    text = raw_text.strip()
    if not text:
        return ""
    blocks = _CODE_BLOCK_RE.findall(text)
    if blocks:
        # Prefer longest python block or last block
        py_blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if py_blocks:
            return py_blocks[-1].strip() + "\n"
        return blocks[-1].strip() + "\n"

    # Strip common Gemma preambles if unfenced
    lines = text.splitlines()
    code_start = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("def ", "import ", "from ", "class ", "@")):
            code_start = i
            break
    return "\n".join(lines[code_start:]).strip() + "\n"


class LiveLLMClient:
    """Async live HTTP client with per-model concurrency limits and retry/backoff."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com",
        request_timeout_s: float = 120.0,
        max_attempts: int = 7,
        budget_guard: LiveBudgetGuard | None = None,
        max_output_tokens_cap: int | None = None,
    ) -> None:
        self.api_key = (api_key or resolve_live_google_api_key()).strip()
        self.base_url = base_url.rstrip("/")
        self.request_timeout_s = float(request_timeout_s)
        self.max_attempts = max(1, int(max_attempts))
        self.budget_guard = budget_guard
        self.max_output_tokens_cap = (
            max(1, int(max_output_tokens_cap)) if max_output_tokens_cap is not None else None
        )
        self._sessions: WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.ClientSession] = (
            WeakKeyDictionary()
        )
        self._sems: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
            WeakKeyDictionary()
        )

    def _get_session(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        sess = self._sessions.get(loop)
        if sess is None or sess.closed:
            connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=self.request_timeout_s)
            sess = aiohttp.ClientSession(connector=connector, timeout=timeout)
            self._sessions[loop] = sess
        return sess

    def _get_semaphore(self, model: str) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        per_model = self._sems.get(loop)
        if per_model is None:
            per_model = {}
            self._sems[loop] = per_model
        sem = per_model.get(model)
        if sem is None:
            limit = int(LIVE_MODEL_PRICING.get(model, {}).get("concurrency", 4))
            sem = asyncio.Semaphore(max(1, limit))
            per_model[model] = sem
        return sem

    async def close(self) -> None:
        for sess in list(self._sessions.values()):
            if not sess.closed:
                await sess.close()
        self._sessions.clear()

    async def generate(
        self,
        model: str,
        *,
        system_prompt: str | None = None,
        messages: list[dict[str, Any]],
        json_mode: bool = False,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> LLMCallResult:
        """Execute a live `generateContent` call and parse usage + non-thought output."""
        spec = LIVE_MODEL_PRICING.get(model, {})
        supports_json_mime = bool(spec.get("supports_json_mime", True))
        supports_sys = bool(spec.get("supports_system_instruction", True))

        contents: list[dict[str, Any]] = []
        effective_system = system_prompt.strip() if system_prompt else None

        if effective_system and not supports_sys:
            # Inline system instruction cleanly into first user turn if model disallows systemInstruction
            prepended = False
            for m in messages:
                role = m.get("role", "user")
                text = str(m.get("text", ""))
                if not prepended and role == "user":
                    text = f"[INSTRUCTIONS]\n{effective_system}\n\n[USER]\n{text}"
                    prepended = True
                contents.append({"role": role, "parts": [{"text": text}]})
            effective_system = None
        else:
            for m in messages:
                role = m.get("role", "user")
                text = str(m.get("text", ""))
                contents.append({"role": role, "parts": [{"text": text}]})

        effective_max_out = (
            min(int(max_output_tokens), int(self.max_output_tokens_cap))
            if self.max_output_tokens_cap is not None
            else int(max_output_tokens)
        )
        gen_cfg: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": effective_max_out,
        }
        if json_mode and supports_json_mime:
            gen_cfg["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        if effective_system:
            payload["systemInstruction"] = {"parts": [{"text": effective_system}]}

        url = f"{self.base_url}/v1beta/models/{model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        sem = self._get_semaphore(model)
        session = self._get_session()
        last_err: Exception | None = None

        res_id: int | None = None
        if self.budget_guard is not None:
            est_usd = estimate_worst_case_call_usd(
                model,
                system_prompt=system_prompt,
                messages=messages,
                max_output_tokens=effective_max_out,
            )
            res_id = self.budget_guard.try_reserve(est_usd)
        settled = False

        try:
            for attempt in range(self.max_attempts):
                if self.budget_guard is not None and self.budget_guard.is_billing_aborted():
                    raise BillingDepletedError(
                        self.budget_guard.aborted_detail
                        or "HTTP 402 billing depletion circuit breaker tripped."
                    )
                t0 = time.perf_counter()
                try:
                    async with sem, session.post(url, headers=headers, json=payload) as resp:
                        raw_body = await resp.text()
                        wall_ms = (time.perf_counter() - t0) * 1000.0
                        status = resp.status
                        retry_after_hdr = resp.headers.get("Retry-After")
                except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                    last_err = exc
                    if attempt + 1 < self.max_attempts:
                        delay = min(30.0, (1.5 ** attempt) + random.uniform(0.1, 0.7))
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(f"Network error calling {model}: {exc}") from exc

                try:
                    body = json.loads(raw_body) if raw_body.strip() else {}
                except json.JSONDecodeError:
                    body = {"_raw": raw_body[:500]}

                if _is_billing_402_error(status, raw_body):
                    detail = f"{model} HTTP {status}: {str(body)[:300]}"
                    if self.budget_guard is not None:
                        self.budget_guard.trip_billing_402(detail)
                    raise BillingDepletedError(detail)

                if status == 400 and json_mode and supports_json_mime and "response_mime_type" in raw_body.lower():
                    # Graceful one-time fallback if a model rejects responseMimeType dynamically
                    gen_cfg.pop("responseMimeType", None)
                    supports_json_mime = False
                    continue

                if status in (429, 500, 502, 503, 504):
                    last_err = RuntimeError(f"{model} HTTP {status}: {str(body)[:240]}")
                    if attempt + 1 < self.max_attempts:
                        hint_s = None
                        if retry_after_hdr:
                            try:
                                hint_s = float(retry_after_hdr)
                            except ValueError:
                                hint_s = None
                        if hint_s is None:
                            retry_match = _RETRY_IN_RE.search(raw_body)
                            if retry_match:
                                hint_s = float(retry_match.group(1))
                        base = (hint_s + 1.0) if hint_s is not None else min(30.0, 2.0 * (1.6 ** attempt))
                        await asyncio.sleep(min(45.0, base + random.uniform(0.1, 0.8)))
                        continue
                    raise last_err

                if status >= 400:
                    raise RuntimeError(f"{model} HTTP {status}: {str(body)[:400]}")

                candidates = body.get("candidates") or []
                parts: list[dict[str, Any]] = []
                finish_reason = ""
                if candidates and isinstance(candidates[0], dict):
                    finish_reason = str(candidates[0].get("finishReason") or "")
                    content = candidates[0].get("content") or {}
                    parts = [p for p in (content.get("parts") or []) if isinstance(p, dict)]

                ans_chunks: list[str] = []
                thought_chunks: list[str] = []
                for p in parts:
                    txt = str(p.get("text") or "")
                    if p.get("thought"):
                        thought_chunks.append(txt)
                    else:
                        ans_chunks.append(txt)

                text = "".join(ans_chunks).strip()

                usage = body.get("usageMetadata")
                usage_dict = usage if isinstance(usage, dict) else {}
                prompt_tok = max(0, int(usage_dict.get("promptTokenCount") or 0))
                cached_tok = max(0, int(usage_dict.get("cachedContentTokenCount") or 0))
                uncached_in = max(0, prompt_tok - cached_tok)
                cand_out = max(0, int(usage_dict.get("candidatesTokenCount") or 0))
                thoughts_out = max(0, int(usage_dict.get("thoughtsTokenCount") or 0))
                billable_out = cand_out + thoughts_out
                cost_usd = compute_google_call_cost_usd(model, uncached_in, cached_tok, billable_out)

                if not text and ((json_mode and supports_json_mime) or attempt + 1 < self.max_attempts):
                    last_err = RuntimeError(
                        f"{model} returned empty response (finish_reason={finish_reason!r})"
                    )
                    if self.budget_guard is not None and res_id is not None:
                        self.budget_guard.settle(res_id, cost_usd)
                        res_id = None
                        res_id = self.budget_guard.try_reserve(est_usd)
                    if json_mode and supports_json_mime:
                        gen_cfg.pop("responseMimeType", None)
                        supports_json_mime = False
                    else:
                        await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
                    continue

                if self.budget_guard is not None and res_id is not None:
                    self.budget_guard.settle(res_id, cost_usd)
                    settled = True

                return LLMCallResult(
                    model=model,
                    text=text,
                    thought_text="".join(thought_chunks).strip(),
                    finish_reason=finish_reason,
                    uncached_input_tokens=uncached_in,
                    cached_input_tokens=cached_tok,
                    candidates_output_tokens=cand_out,
                    thoughts_output_tokens=thoughts_out,
                    cost_usd=cost_usd,
                    latency_ms=wall_ms,
                    raw_usage=usage_dict,
                )

            raise RuntimeError(f"Exhausted retries calling {model}: {last_err}")
        finally:
            if not settled and self.budget_guard is not None and res_id is not None:
                self.budget_guard.release(res_id)


class SpyDecision1Driver(ClassifierDriver):
    """Wraps live Decision1Driver to record real classifier calls, tokens, latency, and USD cost."""

    def __init__(
        self,
        inner: Decision1Driver,
        *,
        budget_guard: LiveBudgetGuard | None = None,
    ) -> None:
        self._inner = inner
        self.budget_guard = budget_guard
        self.calls: int = 0
        self.tokens_used: int = 0
        self.cost_usd: float = 0.0
        self.driver_latency_ms: float = 0.0
        self.wall_ms: float = 0.0
        super().__init__(calibration_profile=inner.calibration_profile)

    def _estimate_d1_reserve_usd(self, request: ClassifierRequest) -> float:
        q_bytes = len(str(getattr(request, "query_text", "") or "").encode("utf-8"))
        head_bytes = sum(
            len(str(getattr(h, "candidate_text", "") or "").encode("utf-8")) + 16
            for h in (getattr(request, "heads", None) or ())
        )
        return max(0.001, (q_bytes + head_bytes + 512) * 0.10 / 1_000_000.0)

    def reset_counters(self) -> None:
        self.calls = 0
        self.tokens_used = 0
        self.cost_usd = 0.0
        self.driver_latency_ms = 0.0
        self.wall_ms = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "driver_latency_ms": self.driver_latency_ms,
            "wall_ms": self.wall_ms,
        }

    @property
    def driver_id(self) -> str:
        return self._inner.driver_id

    @property
    def capabilities(self) -> DriverCapabilities:
        return self._inner.capabilities

    async def _call_guarded(
        self,
        request: ClassifierRequest,
        fn: Any,
        extract_metrics: Any,
    ) -> Any:
        res_id: int | None = None
        if self.budget_guard is not None:
            res_id = self.budget_guard.try_reserve(self._estimate_d1_reserve_usd(request))
        settled = False
        try:
            t0 = time.perf_counter()
            out = await fn(request)
            self.wall_ms += (time.perf_counter() - t0) * 1000.0
            tokens, call_cost, driver_ms = extract_metrics(out)
            self.calls += 1
            self.tokens_used += int(tokens)
            self.cost_usd += float(call_cost)
            self.driver_latency_ms += float(driver_ms)
            if self.budget_guard is not None and res_id is not None:
                self.budget_guard.settle(res_id, float(call_cost))
                settled = True
            return out
        except Exception as exc:
            if _is_billing_402_error(None, str(exc)):
                if self.budget_guard is not None:
                    self.budget_guard.trip_billing_402(f"Decision1: {exc}")
                raise BillingDepletedError(f"Decision1 billing depleted: {exc}") from exc
            raise
        finally:
            if not settled and self.budget_guard is not None and res_id is not None:
                self.budget_guard.release(res_id)

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        return await self._call_guarded(
            request,
            self._inner.score_raw,
            lambda _out: (self._inner.last_tokens_used, self._inner.last_cost_usd, 0.0),
        )

    async def classify(self, request: ClassifierRequest) -> ClassifierResponse:
        if self.capabilities.is_cloud and not request.cloud_egress_allowed:
            raise PermissionError("Cloud classifier egress blocked by Tier-1 Privacy Air-Gap.")
        return await self._call_guarded(
            request,
            self._inner.classify,
            lambda resp: (resp.tokens_used, resp.estimated_cost_usd, resp.driver_latency_ms),
        )

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def close(self) -> None:
        await self._inner.close()


def build_spy_decision1_driver(
    *,
    calibration_path: Path | None = None,
    retry_429_max_wait_s: float = 65.0,
    budget_guard: LiveBudgetGuard | None = None,
) -> SpyDecision1Driver:
    """Instantiate a calibrated live Decision1Driver wrapped in telemetry instrumentation."""
    cal_path = calibration_path or Path("benchmarks/reports/calibration_decision1.json")
    profile: CalibrationProfile | None = None
    if cal_path.is_file():
        profile = CalibrationProfile.model_validate_json(cal_path.read_text(encoding="utf-8"))
    key = resolve_live_google_api_key()
    inner = Decision1Driver(
        endpoint="https://generativelanguage.googleapis.com/v1beta/decisions",
        api_key=key,
        calibration_profile=profile,
        max_heads=1000,
        retry_429_max_wait_s=retry_429_max_wait_s,
    )
    return SpyDecision1Driver(inner, budget_guard=budget_guard)
