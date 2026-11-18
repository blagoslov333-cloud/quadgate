"""Conformal calibration engine (Temperature Scaling, Isotonic Regression, ECE)."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory
from quadgate.drivers.base import CalibrationMethod, CalibrationProfile, ClassifierDriver

_TEMP_MIN = 0.02
_TEMP_MAX = 50.0
_TEMP_GRID_POINTS = 128
_GOLDEN_STEPS = 60
_GOLDEN_RATIO = (math.sqrt(5.0) - 1.0) / 2.0
_PROB_CLIP_EPS = 1e-6
_LOCAL_CROSS_ENCODER_IDS = frozenset({"layaa", "onnx_local"})
_CLOUD_PARALLEL_DRIVER_IDS = frozenset({"decision1", "jev"})


class CalibrationSample(BaseModel):
    """Single golden benchmark pair loaded from `benchmarks/calibration_gold.jsonl`."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    label: Literal[0, 1]
    head_type: str | None = None


@dataclass(slots=True)
class _PavaBlock:
    """Weighted block accumulated during Pool-Adjacent-Violators isotonic regression."""

    sum_x: float
    sum_y: int
    count: int


def _validate_driver_id(driver_id: str) -> str:
    if (
        not driver_id
        or not driver_id.strip()
        or any(ch in driver_id for ch in ("/", "\\", "\x00"))
        or driver_id in {".", ".."}
    ):
        raise ValueError(f"Invalid driver_id for calibration profile: {driver_id!r}")
    return driver_id


def _validate_scores_and_labels(
    raw_scores: Sequence[float],
    labels: Sequence[int],
) -> tuple[list[float], list[int]]:
    if len(raw_scores) == 0:
        raise ValueError("Calibration inputs must not be empty.")
    if len(raw_scores) != len(labels):
        raise ValueError(
            f"raw_scores ({len(raw_scores)}) and labels ({len(labels)}) must have equal length."
        )

    cleaned_scores: list[float] = []
    for idx, score in enumerate(raw_scores):
        if isinstance(score, bool) or not math.isfinite(float(score)):
            raise ValueError(f"raw_scores[{idx}]={score!r} must be a finite number.")
        cleaned_scores.append(float(score))

    cleaned_labels: list[int] = []
    for idx, label in enumerate(labels):
        if isinstance(label, bool) or label not in (0, 1):
            raise ValueError(f"labels[{idx}]={label!r} must be 0 or 1.")
        cleaned_labels.append(int(label))

    return cleaned_scores, cleaned_labels


def _prob_to_clamped_logit(prob: float) -> float:
    """Convert a probability to a clamped logit matching `CalibrationProfile.apply`."""
    clipped = min(1.0 - _PROB_CLIP_EPS, max(_PROB_CLIP_EPS, prob))
    return math.log(clipped / (1.0 - clipped))


def _mean_binary_cross_entropy(
    logits: Sequence[float],
    labels: Sequence[int],
    temperature: float,
) -> float:
    """Compute numerically stable mean NLL for logistic scaling `sigmoid(z / T)`."""
    inv_t = 1.0 / temperature
    exp = math.exp
    log1p = math.log1p
    total = 0.0
    for z, y in zip(logits, labels):
        u = z * inv_t
        if u >= 0.0:
            total += (1 - y) * u + log1p(exp(-u))
        else:
            total += log1p(exp(u)) - y * u
    return total / len(labels)


def _category_from_head_type(head_type: str | None) -> HeadCategory:
    if not head_type:
        return HeadCategory.CANDIDATE
    lowered = head_type.strip().lower()
    if lowered.startswith("inv") or "invariant" in lowered or "secret" in lowered:
        return HeadCategory.INVARIANT
    if (
        "pragmatic" in lowered
        or "counterfactual" in lowered
        or "brainstorm" in lowered
        or "directive" in lowered
        or "completeness" in lowered
    ):
        return HeadCategory.PRAGMATIC
    return HeadCategory.CANDIDATE


class CalibrationEngine:
    """Fits per-driver calibration profiles, computes ECE, and enforces candidate budgets."""

    def __init__(
        self,
        profile_dir: Path | str | None = None,
        max_allowed_ece: float = 0.05,
    ) -> None:
        if isinstance(max_allowed_ece, bool) or not math.isfinite(max_allowed_ece) or not (
            0.0 < max_allowed_ece <= 1.0
        ):
            raise ValueError(f"max_allowed_ece={max_allowed_ece!r} must lie in (0.0, 1.0].")
        self.profile_dir: Path = (
            Path("~/.quadgate/calibration").expanduser()
            if profile_dir is None
            else Path(profile_dir).expanduser()
        )
        self.max_allowed_ece: float = float(max_allowed_ece)

    @staticmethod
    def load_golden_samples(
        dataset_path: Path | str = "benchmarks/calibration_gold.jsonl",
    ) -> tuple[list[CalibrationSample], str]:
        """Load and validate JSONL calibration samples plus hex SHA-256 of the file."""
        path = Path(dataset_path).expanduser()
        raw_bytes = path.read_bytes()
        dataset_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        samples: list[CalibrationSample] = []
        for line_no, raw_line in enumerate(raw_bytes.decode("utf-8").splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no} of {path}: {exc}") from exc
            samples.append(CalibrationSample.model_validate(row))

        if not samples:
            raise ValueError(f"No calibration samples found in {path}.")
        return samples, dataset_sha256

    @staticmethod
    def compute_ece(
        probabilities: Sequence[float],
        labels: Sequence[int],
        num_bins: int = 10,
    ) -> float:
        """Compute Expected Calibration Error across equal-width reliability bins on [0.0, 1.0]."""
        if isinstance(num_bins, bool) or not isinstance(num_bins, int) or num_bins < 1:
            raise ValueError(f"num_bins={num_bins!r} must be an integer >= 1.")
        if len(probabilities) == 0:
            raise ValueError("probabilities must not be empty.")
        if len(probabilities) != len(labels):
            raise ValueError(
                f"probabilities ({len(probabilities)}) and labels ({len(labels)}) "
                "must have equal length."
            )

        bin_counts = [0] * num_bins
        bin_prob_sums = [0.0] * num_bins
        bin_label_sums = [0] * num_bins

        for idx, (prob, label) in enumerate(zip(probabilities, labels)):
            if isinstance(prob, bool):
                raise ValueError(f"probabilities[{idx}]={prob!r} must be a float in [0.0, 1.0].")
            p = float(prob)
            if not math.isfinite(p) or not (0.0 <= p <= 1.0):
                raise ValueError(f"probabilities[{idx}]={prob!r} must lie in [0.0, 1.0].")
            if isinstance(label, bool) or label not in (0, 1):
                raise ValueError(f"labels[{idx}]={label!r} must be 0 or 1.")

            bin_idx = min(num_bins - 1, int(p * num_bins))
            bin_counts[bin_idx] += 1
            bin_prob_sums[bin_idx] += p
            bin_label_sums[bin_idx] += int(label)

        n_total = len(probabilities)
        ece = 0.0
        for count, prob_sum, label_sum in zip(bin_counts, bin_prob_sums, bin_label_sums):
            if count == 0:
                continue
            acc = label_sum / count
            conf = prob_sum / count
            ece += (count / n_total) * abs(acc - conf)

        return min(1.0, max(0.0, ece))

    def fit_temperature(
        self,
        driver_id: str,
        raw_scores: Sequence[float],
        labels: Sequence[int],
        *,
        scores_are_logits: bool = True,
        dataset_sha256: str | None = None,
    ) -> CalibrationProfile:
        """Fit temperature scaling parameter T in [0.02, 50.0] via log-grid + Golden-Section."""
        _validate_driver_id(driver_id)
        cleaned_scores, cleaned_labels = _validate_scores_and_labels(raw_scores, labels)

        if scores_are_logits:
            logits = cleaned_scores
        else:
            logits = [_prob_to_clamped_logit(score) for score in cleaned_scores]

        if all(z == 0.0 for z in logits):
            best_t = 1.0
        else:
            log_min = math.log(_TEMP_MIN)
            log_max = math.log(_TEMP_MAX)
            step = (log_max - log_min) / (_TEMP_GRID_POINTS - 1)
            grid_u = [log_min + i * step for i in range(_TEMP_GRID_POINTS)]
            grid_u[-1] = log_max

            grid_losses = [
                _mean_binary_cross_entropy(logits, cleaned_labels, math.exp(u)) for u in grid_u
            ]
            best_idx = min(range(_TEMP_GRID_POINTS), key=grid_losses.__getitem__)

            left_u = grid_u[max(0, best_idx - 1)]
            right_u = grid_u[min(_TEMP_GRID_POINTS - 1, best_idx + 1)]

            c = right_u - _GOLDEN_RATIO * (right_u - left_u)
            d = left_u + _GOLDEN_RATIO * (right_u - left_u)
            fc = _mean_binary_cross_entropy(logits, cleaned_labels, math.exp(c))
            fd = _mean_binary_cross_entropy(logits, cleaned_labels, math.exp(d))

            for _ in range(_GOLDEN_STEPS):
                if fc < fd:
                    right_u = d
                    d = c
                    fd = fc
                    c = right_u - _GOLDEN_RATIO * (right_u - left_u)
                    fc = _mean_binary_cross_entropy(logits, cleaned_labels, math.exp(c))
                else:
                    left_u = c
                    c = d
                    fc = fd
                    d = left_u + _GOLDEN_RATIO * (right_u - left_u)
                    fd = _mean_binary_cross_entropy(logits, cleaned_labels, math.exp(d))

            candidates_t = [
                _TEMP_MIN,
                _TEMP_MAX,
                math.exp(grid_u[best_idx]),
                math.exp(left_u),
                math.exp(right_u),
                math.exp(0.5 * (left_u + right_u)),
            ]
            best_t = min(
                candidates_t,
                key=lambda t: _mean_binary_cross_entropy(logits, cleaned_labels, t),
            )
            best_t = min(_TEMP_MAX, max(_TEMP_MIN, best_t))

        if not scores_are_logits and len(cleaned_labels) < 100:
            raw_ece = self.compute_ece(
                [min(1.0, max(0.0, score)) for score in cleaned_scores],
                cleaned_labels,
                num_bins=10,
            )
            if raw_ece <= min(0.04, self.max_allowed_ece):
                best_t = 1.0

        profile = CalibrationProfile(
            driver_id=driver_id,
            method=CalibrationMethod.TEMPERATURE_SCALING,
            temperature=best_t,
            ece=0.0,
            max_allowed_ece=self.max_allowed_ece,
            sample_count=len(cleaned_labels),
            dataset_sha256=dataset_sha256,
            created_at_iso=datetime.now(timezone.utc).isoformat(),
        )
        calibrated = [
            profile.apply(score, scores_are_logits=scores_are_logits) for score in cleaned_scores
        ]
        ece = self.compute_ece(calibrated, cleaned_labels, num_bins=10)
        return profile.model_copy(update={"ece": ece})

    def fit_isotonic(
        self,
        driver_id: str,
        raw_scores: Sequence[float],
        labels: Sequence[int],
        *,
        dataset_sha256: str | None = None,
    ) -> CalibrationProfile:
        """Fit piecewise-linear isotonic calibration via exact pure-Python PAVA."""
        _validate_driver_id(driver_id)
        cleaned_scores, cleaned_labels = _validate_scores_and_labels(raw_scores, labels)

        sorted_pairs = sorted(zip(cleaned_scores, cleaned_labels), key=lambda item: item[0])
        min_x = sorted_pairs[0][0]
        max_x = sorted_pairs[-1][0]

        # Collapse tied raw_score values into initial weighted blocks.
        blocks: list[_PavaBlock] = []
        idx = 0
        n_samples = len(sorted_pairs)
        while idx < n_samples:
            x_val = sorted_pairs[idx][0]
            sum_y = sorted_pairs[idx][1]
            count = 1
            idx += 1
            while idx < n_samples and sorted_pairs[idx][0] == x_val:
                sum_y += sorted_pairs[idx][1]
                count += 1
                idx += 1

            blocks.append(_PavaBlock(sum_x=x_val * count, sum_y=sum_y, count=count))
            # Pool adjacent violators using integer cross-multiplication to prevent float drift.
            while len(blocks) >= 2:
                prev = blocks[-2]
                curr = blocks[-1]
                if prev.sum_y * curr.count > curr.sum_y * prev.count:
                    prev.sum_x += curr.sum_x
                    prev.sum_y += curr.sum_y
                    prev.count += curr.count
                    blocks.pop()
                else:
                    break

        if len(blocks) == 1:
            y0 = min(1.0, max(0.0, blocks[0].sum_y / blocks[0].count))
            if min_x < max_x:
                x_knots = [min_x, max_x]
            else:
                delta = max(_PROB_CLIP_EPS, abs(min_x) * _PROB_CLIP_EPS)
                x_knots = [min_x - delta, min_x + delta]
            y_knots = [y0, y0]
        else:
            x_knots = []
            y_knots = []
            for block in blocks:
                x_mean = block.sum_x / block.count
                y_mean = min(1.0, max(0.0, block.sum_y / block.count))
                if x_knots:
                    if x_mean < x_knots[-1]:
                        x_mean = x_knots[-1]
                    if y_mean < y_knots[-1]:
                        y_mean = y_knots[-1]
                x_knots.append(x_mean)
                y_knots.append(y_mean)

        profile = CalibrationProfile(
            driver_id=driver_id,
            method=CalibrationMethod.ISOTONIC,
            isotonic_x_knots=x_knots,
            isotonic_y_knots=y_knots,
            ece=0.0,
            max_allowed_ece=self.max_allowed_ece,
            sample_count=len(cleaned_labels),
            dataset_sha256=dataset_sha256,
            created_at_iso=datetime.now(timezone.utc).isoformat(),
        )
        calibrated = [profile.apply(score) for score in cleaned_scores]
        ece = self.compute_ece(calibrated, cleaned_labels, num_bins=10)
        return profile.model_copy(update={"ece": ece})

    def save_profile(self, profile: CalibrationProfile) -> Path:
        """Persist a CalibrationProfile atomically to `<profile_dir>/<driver_id>.json`."""
        driver_id = _validate_driver_id(profile.driver_id)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        target = self.profile_dir / f"{driver_id}.json"
        tmp_path = self.profile_dir / f".{driver_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(target)
        return target

    def load_profile(self, driver_id: str) -> CalibrationProfile | None:
        """Load `<profile_dir>/<driver_id>.json` if present, otherwise return None."""
        safe_id = _validate_driver_id(driver_id)
        target = self.profile_dir / f"{safe_id}.json"
        if not target.is_file():
            return None
        return CalibrationProfile.model_validate_json(target.read_text(encoding="utf-8"))

    @staticmethod
    def candidate_budget_for_driver(driver: ClassifierDriver) -> int:
        """Return hardware-aware max candidates (up to 800 cloud / 32 local CPU)."""
        caps = driver.capabilities
        if driver.driver_id in _LOCAL_CROSS_ENCODER_IDS or (
            not caps.is_cloud and not caps.supports_parallel_heads
        ):
            return min(32, caps.max_candidates_per_pass)
        if driver.driver_id in _CLOUD_PARALLEL_DRIVER_IDS or (
            caps.is_cloud and caps.supports_parallel_heads
        ):
            return min(800, caps.max_candidates_per_pass)
        return min(caps.max_candidates_per_pass, 800 if caps.supports_parallel_heads else 64)

    @staticmethod
    def budget_candidates(driver: ClassifierDriver, candidates: Sequence[Any]) -> list[Any]:
        """Slice candidate items to the driver's hardware-aware candidate budget."""
        budget = CalibrationEngine.candidate_budget_for_driver(driver)
        return list(candidates[:budget])

    async def fit_driver(
        self,
        driver: ClassifierDriver,
        samples: list[CalibrationSample] | None = None,
        method: CalibrationMethod = CalibrationMethod.TEMPERATURE_SCALING,
        *,
        dataset_path: Path | str = "benchmarks/calibration_gold.jsonl",
        persist: bool = True,
    ) -> CalibrationProfile:
        """Score calibration samples on driver, fit CalibrationProfile, attach, and persist."""
        dataset_sha256: str | None = None
        if samples is None:
            samples, dataset_sha256 = self.load_golden_samples(dataset_path)
        elif not samples:
            raise ValueError("samples must not be empty when provided.")

        grouped_indices: dict[str, list[int]] = {}
        for idx, sample in enumerate(samples):
            grouped_indices.setdefault(sample.task, []).append(idx)

        budget = max(1, self.candidate_budget_for_driver(driver))
        ordered_scores: list[float] = [0.0] * len(samples)
        ordered_labels: list[int] = [int(sample.label) for sample in samples]

        for task, indices in grouped_indices.items():
            for start in range(0, len(indices), budget):
                chunk_indices = indices[start : start + budget]
                heads: list[ClassifierHead] = []
                head_to_sample_idx: list[tuple[str, int]] = []
                seen_ids: set[str] = set()

                for pos, sample_idx in enumerate(chunk_indices):
                    sample = samples[sample_idx]
                    hid = sample.id if sample.id not in seen_ids else f"{sample.id}__{pos}"
                    seen_ids.add(hid)
                    metadata: dict[str, Any] = {"sample_id": sample.id}
                    if sample.head_type is not None:
                        metadata["head_type"] = sample.head_type
                    heads.append(
                        ClassifierHead(
                            head_id=hid,
                            candidate_text=sample.candidate,
                            category=_category_from_head_type(sample.head_type),
                            metadata=metadata,
                        )
                    )
                    head_to_sample_idx.append((hid, sample_idx))

                req = ClassifierRequest(
                    query_text=task,
                    heads=heads,
                    max_candidates=len(heads),
                )
                raw_scores = await driver.score_raw(req)
                for hid, sample_idx in head_to_sample_idx:
                    if hid not in raw_scores:
                        raise ValueError(
                            f"Driver {driver.driver_id!r} omitted requested head_id {hid!r}."
                        )
                    ordered_scores[sample_idx] = float(raw_scores[hid])

        caps = driver.capabilities
        if method == CalibrationMethod.TEMPERATURE_SCALING:
            profile = self.fit_temperature(
                driver.driver_id,
                ordered_scores,
                ordered_labels,
                scores_are_logits=caps.scores_are_logits,
                dataset_sha256=dataset_sha256,
            )
        elif method == CalibrationMethod.ISOTONIC:
            profile = self.fit_isotonic(
                driver.driver_id,
                ordered_scores,
                ordered_labels,
                dataset_sha256=dataset_sha256,
            )
        else:
            raise ValueError(f"Unsupported calibration fitting method: {method!r}")

        driver.set_calibration_profile(profile)
        if persist:
            self.save_profile(profile)
        return profile


__all__ = [
    "CalibrationEngine",
    "CalibrationSample",
]
