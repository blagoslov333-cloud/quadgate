"""JEV cloud or self-hosted HTTP classifier driver."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from quadgate.contracts import ClassifierRequest
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities
from quadgate.drivers.decision1 import (
    TransportFn,
    _CloudHttpPool,
    _derive_health_url,
    _is_healthy_response,
)

DEFAULT_JEV_ENDPOINT = "http://127.0.0.1:9091/v1/classify"


def _normalize_jev_endpoint(raw_endpoint: str) -> str:
    """Append '/v1/classify' when a base JEV URL has no path component."""
    cleaned = raw_endpoint.strip()
    if not cleaned:
        return DEFAULT_JEV_ENDPOINT
    parts = urlsplit(cleaned)
    if parts.path in {"", "/"}:
        return urlunsplit((parts.scheme, parts.netloc, "/v1/classify", parts.query, parts.fragment))
    return cleaned


class JevDriver(ClassifierDriver):
    """Classifier driver for JEV cloud or self-hosted HTTP endpoints."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        is_cloud: bool = True,
        calibration_profile: CalibrationProfile | None = None,
        *,
        max_heads: int = 800,
        transport: TransportFn | None = None,
    ) -> None:
        if not 1 <= max_heads <= 1000:
            raise ValueError(f"max_heads must lie in 1..1000; got {max_heads}.")
        raw_endpoint = endpoint if endpoint is not None else os.environ.get("JEV_BASE_URL")
        self.endpoint = (
            _normalize_jev_endpoint(raw_endpoint)
            if raw_endpoint is not None
            else DEFAULT_JEV_ENDPOINT
        )
        self.api_key = api_key if api_key is not None else os.environ.get("JEV_API_KEY")
        self._is_cloud = is_cloud
        self.max_heads = max_heads
        self._http = _CloudHttpPool(transport=transport)
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "jev"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=self._is_cloud,
            supports_parallel_heads=True,
            max_candidates_per_pass=self.max_heads,
            scores_are_logits=True,
        )

    @property
    def _client(self) -> Any:
        """Expose underlying pooled client for diagnostics and lifecycle verification."""
        return self._http.client

    def _build_headers(self, *, include_content_type: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        token = self.api_key if self.api_key is not None else os.environ.get("JEV_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _parse_rerank_items(items: list[Any], head_ids: list[str]) -> dict[str, float]:
        parsed: dict[str, float] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("JEV rerank result entries must be JSON objects.")
            score_val = item.get(
                "score",
                item.get("relevance_score", item.get("logit", item.get("value"))),
            )
            if score_val is None:
                raise ValueError(f"JEV rerank entry missing score field: {item!r}")

            head_id = item.get("head_id")
            if head_id is None:
                if "index" not in item:
                    raise ValueError(f"JEV rerank entry missing 'index' and 'head_id': {item!r}")
                idx = int(item["index"])
                if not 0 <= idx < len(head_ids):
                    raise ValueError(
                        f"JEV rerank index {idx} out of range for {len(head_ids)} heads."
                    )
                head_id = head_ids[idx]
            parsed[str(head_id)] = float(score_val)
        return parsed

    @classmethod
    def _parse_scores(cls, data: Any, head_ids: list[str]) -> dict[str, float]:
        raw_scores: dict[str, float] = {}

        if isinstance(data, list):
            raw_scores = cls._parse_rerank_items(data, head_ids)
        elif isinstance(data, dict):
            scores_field = data.get("scores")
            if isinstance(scores_field, dict):
                raw_scores = {str(k): float(v) for k, v in scores_field.items()}
            elif isinstance(scores_field, list):
                if len(scores_field) != len(head_ids):
                    raise ValueError(
                        f"JEV ordered scores length {len(scores_field)} != "
                        f"requested head count {len(head_ids)}."
                    )
                raw_scores = {
                    hid: float(val) for hid, val in zip(head_ids, scores_field, strict=True)
                }
            elif isinstance(data.get("results"), list):
                raw_scores = cls._parse_rerank_items(data["results"], head_ids)
            elif isinstance(data.get("logits"), dict):
                raw_scores = {str(k): float(v) for k, v in data["logits"].items()}
            elif isinstance(data.get("logits"), list):
                if len(data["logits"]) != len(head_ids):
                    raise ValueError(
                        f"JEV logits length {len(data['logits'])} != "
                        f"requested head count {len(head_ids)}."
                    )
                raw_scores = {
                    hid: float(val) for hid, val in zip(head_ids, data["logits"], strict=True)
                }
            else:
                raise ValueError(
                    "JEV response must provide 'scores' (dict/list), "
                    "'results' list, or rerank list."
                )
        else:
            raise ValueError(f"Unexpected JEV response payload type: {type(data).__name__}")

        missing = [hid for hid in head_ids if hid not in raw_scores]
        if missing:
            raise ValueError(f"JEV response omitted requested heads: {missing}")
        return {hid: raw_scores[hid] for hid in head_ids}

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        """Score a batch of query-candidate pairs via JEV or vLLM/TEI rerank endpoint."""
        if self._is_cloud and not request.cloud_egress_allowed:
            raise PermissionError(
                "Cloud classifier egress blocked by Pass-0 Tier-1 Privacy Air-Gap."
            )
        if len(request.heads) > self.max_heads:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most {self.max_heads} heads per pass; "
                f"got {len(request.heads)}."
            )

        head_ids = [head.head_id for head in request.heads]
        payload: dict[str, Any] = {
            "query": request.query_text,
            "candidates": [
                {
                    "head_id": head.head_id,
                    "text": head.candidate_text,
                    "category": head.category.value,
                    "index": idx,
                }
                for idx, head in enumerate(request.heads)
            ],
            "pairs": [[request.query_text, head.candidate_text] for head in request.heads],
            "raw_scores": True,
        }
        status, data = await self._http.request(
            "POST",
            self.endpoint,
            headers=self._build_headers(include_content_type=True),
            json_body=payload,
            timeout_s=request.timeout_ms / 1000.0,
        )
        if not 200 <= status < 300:
            raise RuntimeError(f"JEV endpoint returned HTTP {status}: {data}")
        return self._parse_scores(data, head_ids)

    async def health_check(self) -> bool:
        """Probe JEV health endpoint without raising on failure."""
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
