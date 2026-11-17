"""Abstract ClassifierDriver protocol and conformal CalibrationProfile contract."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from bisect import bisect_right
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quadgate.contracts import ClassifierRequest, ClassifierResponse


def _sigmoid(value: float) -> float:
    """Numerically stable logistic sigmoid."""
    if value >= 0.0:
        exp_neg = math.exp(-value)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(value)
    return exp_pos / (1.0 + exp_pos)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


class CalibrationMethod(StrEnum):
    """Supported post-hoc probability calibration transforms."""

    IDENTITY = "identity"
    TEMPERATURE_SCALING = "temperature_scaling"
    ISOTONIC = "isotonic"
    PLATT = "platt"


class CalibrationProfile(BaseModel):
    """Serializable per-driver conformal calibration profile."""

    model_config = ConfigDict(extra="forbid")

    driver_id: str = Field(min_length=1)
    method: CalibrationMethod = CalibrationMethod.TEMPERATURE_SCALING
    temperature: float = Field(default=1.0, gt=0.0)
    platt_scale: float = 1.0
    platt_bias: float = 0.0
    isotonic_x_knots: list[float] = Field(default_factory=list)
    isotonic_y_knots: list[float] = Field(default_factory=list)
    ece: float = Field(default=0.0, ge=0.0, le=1.0)
    max_allowed_ece: float = Field(default=0.05, gt=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    dataset_sha256: str | None = None
    created_at_iso: str | None = None

    @model_validator(mode="after")
    def _validate_isotonic_knots(self) -> CalibrationProfile:
        nx = len(self.isotonic_x_knots)
        ny = len(self.isotonic_y_knots)
        if nx != ny:
            raise ValueError("isotonic_x_knots and isotonic_y_knots must have equal length.")
        if nx > 0:
            if nx < 2:
                raise ValueError("Isotonic knots require at least 2 points when provided.")
            for i in range(nx):
                if not 0.0 <= self.isotonic_y_knots[i] <= 1.0:
                    raise ValueError("isotonic_y_knots values must lie in [0.0, 1.0].")
                if i > 0:
                    if self.isotonic_x_knots[i] < self.isotonic_x_knots[i - 1]:
                        raise ValueError("isotonic_x_knots must be monotonically non-decreasing.")
                    if self.isotonic_y_knots[i] < self.isotonic_y_knots[i - 1]:
                        raise ValueError("isotonic_y_knots must be monotonically non-decreasing.")
        return self

    @property
    def passes_ece_gate(self) -> bool:
        """Return True when fitted on >= 1 sample and ECE meets threshold."""
        return self.sample_count > 0 and self.ece <= self.max_allowed_ece

    def apply(self, raw_score: float, *, scores_are_logits: bool = True) -> float:
        """Map a raw driver logit/score into a calibrated probability in [0.0, 1.0]."""
        if self.method == CalibrationMethod.IDENTITY:
            return _sigmoid(raw_score) if scores_are_logits else _clamp01(raw_score)

        if self.method == CalibrationMethod.TEMPERATURE_SCALING:
            if scores_are_logits:
                return _clamp01(_sigmoid(raw_score / self.temperature))
            clipped = min(1.0 - 1e-6, max(1e-6, raw_score))
            logit = math.log(clipped / (1.0 - clipped))
            return _clamp01(_sigmoid(logit / self.temperature))

        if self.method == CalibrationMethod.PLATT:
            return _clamp01(_sigmoid(self.platt_scale * raw_score + self.platt_bias))

        if self.method == CalibrationMethod.ISOTONIC:
            if len(self.isotonic_x_knots) < 2:
                raise ValueError("Isotonic calibration requires at least 2 fitted knots.")
            xs = self.isotonic_x_knots
            ys = self.isotonic_y_knots
            if raw_score <= xs[0]:
                return _clamp01(ys[0])
            if raw_score >= xs[-1]:
                return _clamp01(ys[-1])
            idx = bisect_right(xs, raw_score) - 1
            x0, x1 = xs[idx], xs[idx + 1]
            y0, y1 = ys[idx], ys[idx + 1]
            if x1 == x0:
                return _clamp01(y1)
            ratio = (raw_score - x0) / (x1 - x0)
            return _clamp01(y0 + ratio * (y1 - y0))

        raise ValueError(f"Unsupported calibration method: {self.method}")


class DriverCapabilities(BaseModel):
    """Static hardware/egress capability declaration for a ClassifierDriver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_cloud: bool
    supports_parallel_heads: bool = True
    max_candidates_per_pass: int = Field(ge=1, le=1000)
    scores_are_logits: bool = True


class ClassifierDriver(ABC):
    """Model-agnostic discriminative classifier interface."""

    def __init__(self, calibration_profile: CalibrationProfile | None = None) -> None:
        self._calibration_profile: CalibrationProfile | None = None
        if calibration_profile is not None:
            self.set_calibration_profile(calibration_profile)

    @property
    def calibration_profile(self) -> CalibrationProfile | None:
        """Return the active CalibrationProfile if loaded."""
        return self._calibration_profile

    def set_calibration_profile(self, profile: CalibrationProfile) -> None:
        """Attach a validated CalibrationProfile matching this driver."""
        if profile.driver_id != self.driver_id:
            raise ValueError(
                f"CalibrationProfile driver_id={profile.driver_id!r} "
                f"does not match driver {self.driver_id!r}."
            )
        self._calibration_profile = profile

    @property
    @abstractmethod
    def driver_id(self) -> str:
        """Unique stable identifier for the classifier backend."""

    @property
    @abstractmethod
    def capabilities(self) -> DriverCapabilities:
        """Hardware, egress, and candidate budget constraints."""

    @abstractmethod
    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        """Score all heads in request and return a mapping {head_id: raw_score}."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify driver readiness without modifying external state."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying HTTP pools or inference sessions."""

    async def classify(self, request: ClassifierRequest) -> ClassifierResponse:
        """Enforce air-gap & candidate budget, score heads, and apply calibration."""
        caps = self.capabilities
        if caps.is_cloud and not request.cloud_egress_allowed:
            raise PermissionError(
                "Cloud classifier egress blocked by Pass-0 Tier-1 Privacy Air-Gap."
            )
        if len(request.heads) > caps.max_candidates_per_pass:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most "
                f"{caps.max_candidates_per_pass} heads per pass; got {len(request.heads)}."
            )

        t0 = time.perf_counter()
        raw_scores = await self.score_raw(request)
        latency_ms = max(0.0, (time.perf_counter() - t0) * 1000.0)

        head_ids = [head.head_id for head in request.heads]
        missing = [hid for hid in head_ids if hid not in raw_scores]
        if missing:
            raise ValueError(f"Driver {self.driver_id!r} omitted requested heads: {missing}")

        ordered_raw = {hid: float(raw_scores[hid]) for hid in head_ids}
        profile = self._calibration_profile
        if profile is not None:
            calibrated = {
                hid: profile.apply(score, scores_are_logits=caps.scores_are_logits)
                for hid, score in ordered_raw.items()
            }
            is_calibrated = True
        else:
            calibrated = {
                hid: (_sigmoid(score) if caps.scores_are_logits else _clamp01(score))
                for hid, score in ordered_raw.items()
            }
            is_calibrated = False

        return ClassifierResponse(
            driver_id=self.driver_id,
            head_ids=head_ids,
            raw_scores=ordered_raw,
            calibrated_probabilities=calibrated,
            driver_latency_ms=latency_ms,
            is_calibrated=is_calibrated,
        )


__all__ = [
    "CalibrationMethod",
    "CalibrationProfile",
    "ClassifierDriver",
    "DriverCapabilities",
]
