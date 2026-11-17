"""Single-token max_tokens=1 logprob fallback classifier driver."""

from __future__ import annotations

import asyncio
import contextvars
import math
import os
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from quadgate.contracts import ClassifierHead, ClassifierRequest, ClassifierResponse
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities
from quadgate.drivers.decision1 import (
    TransportFn,
    _CloudHttpPool,
    _derive_health_url,
    _is_healthy_response,
)

DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

_SYSTEM_PROMPT = (
    "You are a strict binary discriminative classifier. "
    "Respond with exactly one token: YES or NO."
)

_ACTIVE_TOKEN_ACCUMULATOR: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "_quadgate_logprob_token_acc",
    default=None,
)


def _compute_yes_probability(
    entries: list[tuple[str, float]],
    *,
    fallback_text: str | None = None,
) -> float:
    """Compute P(YES) = sum(exp(l_y)) / (sum(exp(l_y)) + sum(exp(l_n))) safely in [0, 1]."""
    yes_lps: list[float] = []
    no_lps: list[float] = []
    for raw_token, logprob in entries:
        norm = raw_token.strip(" \t\r\n\u2581.,!?:;\"'").lower()
        if norm == "yes":
            yes_lps.append(float(logprob))
        elif norm == "no":
            no_lps.append(float(logprob))

    if yes_lps or no_lps:
        if not yes_lps:
            return 0.0
        if not no_lps:
            return 1.0
        max_lp = max(max(yes_lps), max(no_lps))
        sum_yes = sum(math.exp(lp - max_lp) for lp in yes_lps)
        sum_no = sum(math.exp(lp - max_lp) for lp in no_lps)
        denom = sum_yes + sum_no
        if denom <= 0.0:
            return 0.5
        return min(1.0, max(0.0, sum_yes / denom))

    if fallback_text is not None:
        norm_text = fallback_text.strip(" \t\r\n.,!?:;\"'").lower()
        if norm_text.startswith("yes"):
            return 1.0
        if norm_text.startswith("no"):
            return 0.0
    return 0.5


class LogprobFallbackDriver(ClassifierDriver):
    """Fallback classifier using logprobs from an OpenAI-compatible or Gemini API."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        calibration_profile: CalibrationProfile | None = None,
        *,
        provider: Literal["openai", "gemini", "auto"] = "auto",
        max_concurrency: int = 8,
        transport: TransportFn | None = None,
    ) -> None:
        if provider not in {"openai", "gemini", "auto"}:
            raise ValueError(
                f"Unsupported provider {provider!r}; expected 'openai', 'gemini', or 'auto'."
            )
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1; got {max_concurrency}.")

        self.endpoint = endpoint
        self.model = model
        self._explicit_api_key = api_key
        self.provider: Literal["openai", "gemini", "auto"] = provider
        self.max_concurrency = max_concurrency
        self._http = _CloudHttpPool(transport=transport)
        self._last_tokens_used: int = 0

        resolved = self._resolve_provider()
        self.api_key = api_key if api_key is not None else self._read_env_api_key(resolved)
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "logprob_fallback"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=False,
            max_candidates_per_pass=64,
            scores_are_logits=False,
        )

    @property
    def _client(self) -> Any:
        """Expose underlying pooled client for diagnostics and lifecycle verification."""
        return self._http.client

    @property
    def last_tokens_used(self) -> int:
        """Return total tokens reported during the most recent scoring pass."""
        return self._last_tokens_used

    @staticmethod
    def _read_env_api_key(resolved_provider: Literal["openai", "gemini"]) -> str | None:
        if resolved_provider == "gemini":
            return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return os.environ.get("OPENAI_API_KEY")

    def _resolve_provider(self) -> Literal["openai", "gemini"]:
        if self.provider != "auto":
            return self.provider
        if self.endpoint:
            lower_ep = self.endpoint.lower()
            if (
                "generativelanguage.googleapis.com" in lower_ep
                or ":generatecontent" in lower_ep
                or "gemini" in lower_ep
            ):
                return "gemini"
            if (
                "api.openai.com" in lower_ep
                or "/chat/completions" in lower_ep
                or "/completions" in lower_ep
                or "openai" in lower_ep
            ):
                return "openai"
        if self.model:
            lower_model = self.model.lower()
            if "gemini" in lower_model:
                return "gemini"
            if lower_model.startswith(("gpt-", "o1", "o3", "o4")):
                return "openai"
        if self._explicit_api_key and self._explicit_api_key.startswith("AIza"):
            return "gemini"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            return "gemini"
        return "openai"

    def _resolve_model(self, resolved_provider: Literal["openai", "gemini"]) -> str:
        if self.model is not None:
            return self.model
        return DEFAULT_GEMINI_MODEL if resolved_provider == "gemini" else DEFAULT_OPENAI_MODEL

    def _resolve_api_key(self, resolved_provider: Literal["openai", "gemini"]) -> str | None:
        if self.api_key is not None:
            return self.api_key
        return self._read_env_api_key(resolved_provider)

    def _resolve_endpoint(
        self,
        resolved_provider: Literal["openai", "gemini"],
        resolved_model: str,
    ) -> str:
        if self.endpoint is None:
            if resolved_provider == "gemini":
                return (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{resolved_model}:generateContent"
                )
            return DEFAULT_OPENAI_ENDPOINT

        parts = urlsplit(self.endpoint)
        path = parts.path.rstrip("/")
        if resolved_provider == "gemini":
            if not path:
                target_path = f"/v1beta/models/{resolved_model}:generateContent"
                return urlunsplit(
                    (parts.scheme, parts.netloc, target_path, parts.query, parts.fragment)
                )
            return self.endpoint

        if not path:
            target_path = "/v1/chat/completions"
            return urlunsplit(
                (parts.scheme, parts.netloc, target_path, parts.query, parts.fragment)
            )
        if path.endswith("/v1"):
            target_path = f"{path}/chat/completions"
            return urlunsplit(
                (parts.scheme, parts.netloc, target_path, parts.query, parts.fragment)
            )
        return self.endpoint

    def _build_headers(
        self,
        resolved_provider: Literal["openai", "gemini"],
        target_url: str,
        *,
        include_content_type: bool,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        token = self._resolve_api_key(resolved_provider)
        if token:
            if resolved_provider == "gemini":
                if "key=" not in target_url:
                    headers["x-goog-api-key"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _format_user_prompt(query_text: str, head: ClassifierHead) -> str:
        return (
            f"Category: {head.category.value}\n"
            f"Query: {query_text}\n"
            f"Candidate: {head.candidate_text}\n"
            "Does this candidate match or apply to the query? Answer YES or NO:"
        )

    @classmethod
    def _build_payload(
        cls,
        resolved_provider: Literal["openai", "gemini"],
        resolved_model: str,
        query_text: str,
        head: ClassifierHead,
    ) -> dict[str, Any]:
        user_prompt = cls._format_user_prompt(query_text, head)
        if resolved_provider == "gemini":
            return {
                "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 1,
                    "temperature": 0.0,
                    "responseLogprobs": True,
                    "logprobs": 5,
                },
            }
        return {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 5,
        }

    @staticmethod
    def _parse_openai_response(data: dict[str, Any]) -> tuple[float, int]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"OpenAI response missing 'choices': {data!r}")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("OpenAI choice entry must be a JSON object.")

        message = choice.get("message") or {}
        fallback_text = message.get("content") if isinstance(message, dict) else choice.get("text")

        entries: list[tuple[str, float]] = []
        logprobs_obj = choice.get("logprobs")
        if isinstance(logprobs_obj, dict):
            content_lps = logprobs_obj.get("content")
            if isinstance(content_lps, list) and content_lps:
                first_tok = content_lps[0]
                if isinstance(first_tok, dict):
                    top_lps = first_tok.get("top_logprobs")
                    if isinstance(top_lps, list) and top_lps:
                        for item in top_lps:
                            if isinstance(item, dict) and "token" in item and "logprob" in item:
                                entries.append((str(item["token"]), float(item["logprob"])))
                    elif "token" in first_tok and "logprob" in first_tok:
                        entries.append((str(first_tok["token"]), float(first_tok["logprob"])))
            elif (
                isinstance(logprobs_obj.get("top_logprobs"), list)
                and logprobs_obj["top_logprobs"]
            ):
                legacy_top = logprobs_obj["top_logprobs"][0]
                if isinstance(legacy_top, dict):
                    for tok, lp in legacy_top.items():
                        entries.append((str(tok), float(lp)))

        usage = data.get("usage")
        tokens = 0
        if isinstance(usage, dict):
            if usage.get("total_tokens") is not None:
                tokens = int(usage["total_tokens"])
            else:
                tokens = int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))

        prob = _compute_yes_probability(
            entries,
            fallback_text=str(fallback_text) if fallback_text is not None else None,
        )
        return prob, max(0, tokens)

    @staticmethod
    def _parse_gemini_response(data: dict[str, Any]) -> tuple[float, int]:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"Gemini response missing 'candidates': {data!r}")
        cand = candidates[0]
        if not isinstance(cand, dict):
            raise ValueError("Gemini candidate entry must be a JSON object.")

        fallback_text: str | None = None
        content = cand.get("content")
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], dict):
                raw_txt = parts[0].get("text")
                if raw_txt is not None:
                    fallback_text = str(raw_txt)

        entries: list[tuple[str, float]] = []
        lp_result = cand.get("logprobsResult") or cand.get("logprobs_result")
        if isinstance(lp_result, dict):
            top_cands = lp_result.get("topCandidates") or lp_result.get("top_candidates")
            if isinstance(top_cands, list) and top_cands and isinstance(top_cands[0], dict):
                cand_list = top_cands[0].get("candidates")
                if isinstance(cand_list, list):
                    for item in cand_list:
                        if not isinstance(item, dict):
                            continue
                        lp_val = item.get("logProbability", item.get("logprob"))
                        if "token" in item and lp_val is not None:
                            entries.append((str(item["token"]), float(lp_val)))
            if not entries:
                chosen = lp_result.get("chosenCandidates") or lp_result.get("chosen_candidates")
                if isinstance(chosen, list) and chosen and isinstance(chosen[0], dict):
                    lp_val = chosen[0].get("logProbability", chosen[0].get("logprob"))
                    if "token" in chosen[0] and lp_val is not None:
                        entries.append((str(chosen[0]["token"]), float(lp_val)))

        usage = data.get("usageMetadata") or data.get("usage_metadata")
        tokens = 0
        if isinstance(usage, dict):
            if usage.get("totalTokenCount") is not None:
                tokens = int(usage["totalTokenCount"])
            else:
                tokens = int(usage.get("promptTokenCount", 0)) + int(
                    usage.get("candidatesTokenCount", 0)
                )

        prob = _compute_yes_probability(entries, fallback_text=fallback_text)
        return prob, max(0, tokens)

    async def _score_single_head(
        self,
        semaphore: asyncio.Semaphore,
        resolved_provider: Literal["openai", "gemini"],
        resolved_model: str,
        target_url: str,
        query_text: str,
        head: ClassifierHead,
        timeout_s: float,
    ) -> tuple[str, float, int]:
        headers = self._build_headers(resolved_provider, target_url, include_content_type=True)
        payload = self._build_payload(resolved_provider, resolved_model, query_text, head)
        async with semaphore:
            status, data = await self._http.request(
                "POST",
                target_url,
                headers=headers,
                json_body=payload,
                timeout_s=timeout_s,
            )
        if not 200 <= status < 300:
            raise RuntimeError(
                f"LogprobFallback ({resolved_provider}) endpoint returned HTTP {status}: {data}"
            )
        if not isinstance(data, dict):
            raise ValueError(
                f"LogprobFallback ({resolved_provider}) expected JSON object, "
                f"got {type(data).__name__}."
            )
        if resolved_provider == "gemini":
            prob, tokens = self._parse_gemini_response(data)
        else:
            prob, tokens = self._parse_openai_response(data)
        return head.head_id, prob, tokens

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        """Score each head concurrently via single-token YES/NO logprob requests."""
        if not request.cloud_egress_allowed:
            raise PermissionError(
                "Cloud classifier egress blocked by Pass-0 Tier-1 Privacy Air-Gap."
            )
        if len(request.heads) > self.capabilities.max_candidates_per_pass:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most "
                f"{self.capabilities.max_candidates_per_pass} heads per pass; "
                f"got {len(request.heads)}."
            )

        resolved_provider = self._resolve_provider()
        resolved_model = self._resolve_model(resolved_provider)
        target_url = self._resolve_endpoint(resolved_provider, resolved_model)
        timeout_s = request.timeout_ms / 1000.0
        semaphore = asyncio.Semaphore(self.max_concurrency)

        results = await asyncio.gather(
            *(
                self._score_single_head(
                    semaphore,
                    resolved_provider,
                    resolved_model,
                    target_url,
                    request.query_text,
                    head,
                    timeout_s,
                )
                for head in request.heads
            )
        )

        scores: dict[str, float] = {}
        total_tokens = 0
        for head_id, prob, tokens in results:
            scores[head_id] = prob
            total_tokens += tokens

        self._last_tokens_used = total_tokens
        acc = _ACTIVE_TOKEN_ACCUMULATOR.get()
        if acc is not None:
            acc[0] += total_tokens
        return scores

    async def classify(self, request: ClassifierRequest) -> ClassifierResponse:
        """Score heads, apply conformal calibration, and populate tokens_used."""
        token_acc = [0]
        token_var = _ACTIVE_TOKEN_ACCUMULATOR.set(token_acc)
        try:
            response = await super().classify(request)
        finally:
            _ACTIVE_TOKEN_ACCUMULATOR.reset(token_var)
        return response.model_copy(update={"tokens_used": token_acc[0]})

    def _derive_provider_health_url(
        self,
        resolved_provider: Literal["openai", "gemini"],
        resolved_model: str,
    ) -> str:
        target_url = self._resolve_endpoint(resolved_provider, resolved_model)
        parts = urlsplit(target_url)
        path = parts.path.rstrip("/")
        if resolved_provider == "openai" and path.endswith("/chat/completions"):
            health_path = f"{path[: -len('/chat/completions')]}/models"
            return urlunsplit((parts.scheme, parts.netloc, health_path, "", ""))
        if resolved_provider == "gemini" and path.endswith(":generateContent"):
            health_path = path[: -len(":generateContent")]
            return urlunsplit((parts.scheme, parts.netloc, health_path, "", ""))
        return _derive_health_url(target_url)

    async def health_check(self) -> bool:
        """Probe OpenAI/Gemini endpoint readiness without raising on failure."""
        resolved_provider = self._resolve_provider()
        resolved_model = self._resolve_model(resolved_provider)
        health_url = self._derive_provider_health_url(resolved_provider, resolved_model)
        headers = self._build_headers(resolved_provider, health_url, include_content_type=False)
        try:
            status, data = await self._http.request(
                "GET",
                health_url,
                headers=headers,
                json_body=None,
                timeout_s=2.0,
            )
        except Exception:
            return False
        return _is_healthy_response(status, data)

    async def close(self) -> None:
        """Idempotently close the persistent HTTP connection pool."""
        await self._http.close()
