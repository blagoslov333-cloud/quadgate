"""Wave 2 unit and parity tests for quadgate.kernel.calibration."""

from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path

import pytest

from quadgate.contracts import ClassifierRequest, HeadCategory
from quadgate.drivers.base import (
    CalibrationMethod,
    CalibrationProfile,
    ClassifierDriver,
    DriverCapabilities,
)
from quadgate.kernel.calibration import CalibrationEngine, CalibrationSample


def _sigmoid(x: float) -> float:
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


class _ConfigurableDriver(ClassifierDriver):
    """Deterministic in-memory ClassifierDriver for calibration, chunking, and budget tests."""

    def __init__(
        self,
        driver_id: str,
        *,
        is_cloud: bool,
        supports_parallel_heads: bool,
        max_candidates_per_pass: int,
        scores_are_logits: bool = True,
        score_map: dict[str, float] | None = None,
        calibration_profile: CalibrationProfile | None = None,
    ) -> None:
        self._driver_id = driver_id
        self._caps = DriverCapabilities(
            is_cloud=is_cloud,
            supports_parallel_heads=supports_parallel_heads,
            max_candidates_per_pass=max_candidates_per_pass,
            scores_are_logits=scores_are_logits,
        )
        self._score_map = score_map or {}
        self.recorded_requests: list[ClassifierRequest] = []
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return self._driver_id

    @property
    def capabilities(self) -> DriverCapabilities:
        return self._caps

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        self.recorded_requests.append(request)
        scores: dict[str, float] = {}
        for head in request.heads:
            if head.head_id in self._score_map:
                scores[head.head_id] = self._score_map[head.head_id]
            else:
                sample_id = str(head.metadata.get("sample_id", head.head_id))
                scores[head.head_id] = self._score_map.get(sample_id, 0.0)
        return scores

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_default_profile_dir_and_validation() -> None:
    engine = CalibrationEngine()
    assert engine.profile_dir == Path("~/.quadgate/calibration").expanduser()
    assert engine.max_allowed_ece == pytest.approx(0.05)

    with pytest.raises(ValueError, match="max_allowed_ece"):
        CalibrationEngine(max_allowed_ece=0.0)
    with pytest.raises(ValueError, match="max_allowed_ece"):
        CalibrationEngine(max_allowed_ece=1.2)


def test_compute_ece_analytical_and_edge_cases() -> None:
    # Exact single-bin and boundary perfect calibration
    assert CalibrationEngine.compute_ece([0.0, 1.0], [0, 1]) == pytest.approx(0.0)
    assert CalibrationEngine.compute_ece([0.25, 0.25, 0.25, 0.25], [1, 0, 0, 0]) == pytest.approx(
        0.0
    )

    # Analytical multi-bin results
    assert CalibrationEngine.compute_ece([0.9, 0.1], [1, 0]) == pytest.approx(0.10)
    assert CalibrationEngine.compute_ece([0.15, 0.15, 0.85, 0.85], [0, 0, 1, 1]) == pytest.approx(
        0.15
    )

    # Weighted 2-bin example:
    # Bin [0.7, 0.8): [0.72, 0.74, 0.76, 0.78] with labels [1, 1, 1, 0] -> |0.75 - 0.75| = 0.0
    # Bin [0.2, 0.3): [0.25, 0.25] with labels [1, 1] -> |1.0 - 0.25| = 0.75
    # ECE = (4/6)*0.0 + (2/6)*0.75 = 0.25
    ece = CalibrationEngine.compute_ece(
        [0.72, 0.74, 0.76, 0.78, 0.25, 0.25],
        [1, 1, 1, 0, 1, 1],
        num_bins=10,
    )
    assert ece == pytest.approx(0.25)

    # Boundary total miscalibration and custom num_bins
    assert CalibrationEngine.compute_ece([0.0, 1.0], [1, 0]) == pytest.approx(1.0)
    assert CalibrationEngine.compute_ece([0.2, 0.8], [1, 0], num_bins=1) == pytest.approx(0.0)

    # Input validation edge cases
    with pytest.raises(ValueError, match="empty"):
        CalibrationEngine.compute_ece([], [])
    with pytest.raises(ValueError, match="equal length"):
        CalibrationEngine.compute_ece([0.5, 0.5], [1])
    with pytest.raises(ValueError, match="num_bins"):
        CalibrationEngine.compute_ece([0.5], [1], num_bins=0)
    with pytest.raises(ValueError, match="num_bins"):
        CalibrationEngine.compute_ece([0.5], [1], num_bins=True)
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        CalibrationEngine.compute_ece([-0.01], [0])
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        CalibrationEngine.compute_ece([1.01], [1])
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        CalibrationEngine.compute_ece([float("nan")], [1])
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        CalibrationEngine.compute_ece([float("inf")], [1])
    with pytest.raises(ValueError, match="0 or 1"):
        CalibrationEngine.compute_ece([0.5], [2])


def test_cross_model_temperature_scaling_parity(tmp_path: Path) -> None:
    rng = random.Random(42)
    n_cal = 2400
    true_logits = [rng.uniform(-3.5, 3.5) for _ in range(n_cal)]
    labels = [1 if rng.random() < _sigmoid(z) else 0 for z in true_logits]

    # Overconfident model (4.0 * z) vs underconfident model (0.25 * z)
    logits_a = [4.0 * z for z in true_logits]
    logits_b = [0.25 * z for z in true_logits]

    raw_ece_a = CalibrationEngine.compute_ece([_sigmoid(z) for z in logits_a], labels)
    raw_ece_b = CalibrationEngine.compute_ece([_sigmoid(z) for z in logits_b], labels)
    assert raw_ece_a > 0.10
    assert raw_ece_b > 0.10

    engine = CalibrationEngine(profile_dir=tmp_path)
    profile_a = engine.fit_temperature("model_overconfident", logits_a, labels)
    profile_b = engine.fit_temperature("model_underconfident", logits_b, labels)

    assert 3.2 <= profile_a.temperature <= 4.8
    assert 0.18 <= profile_b.temperature <= 0.32
    assert profile_a.ece <= 0.05
    assert profile_b.ece <= 0.05
    assert profile_a.passes_ece_gate
    assert profile_b.passes_ece_gate

    # Evaluate on held-out test inputs across [-3.0, 3.0]
    test_z = [-3.0 + 0.06 * i for i in range(101)]
    for z in test_z:
        p_cal_a = profile_a.apply(4.0 * z, scores_are_logits=True)
        p_cal_b = profile_b.apply(0.25 * z, scores_are_logits=True)
        assert abs(p_cal_a - p_cal_b) <= 0.05

    # Held-out Bernoulli ECE verification
    n_test = 1000
    holdout_z = [rng.uniform(-3.5, 3.5) for _ in range(n_test)]
    holdout_y = [1 if rng.random() < _sigmoid(z) else 0 for z in holdout_z]
    holdout_ece_a = CalibrationEngine.compute_ece(
        [profile_a.apply(4.0 * z, scores_are_logits=True) for z in holdout_z],
        holdout_y,
    )
    holdout_ece_b = CalibrationEngine.compute_ece(
        [profile_b.apply(0.25 * z, scores_are_logits=True) for z in holdout_z],
        holdout_y,
    )
    assert holdout_ece_a <= 0.05
    assert holdout_ece_b <= 0.05

    # Also verify scores_are_logits=False path matches CalibrationProfile.apply
    probs_a = [_sigmoid(3.0 * z) for z in true_logits]
    probs_b = [_sigmoid(0.35 * z) for z in true_logits]
    prob_prof_a = engine.fit_temperature(
        "prob_over", probs_a, labels, scores_are_logits=False
    )
    prob_prof_b = engine.fit_temperature(
        "prob_under", probs_b, labels, scores_are_logits=False
    )
    assert prob_prof_a.ece <= 0.05
    assert prob_prof_b.ece <= 0.05
    for z in test_z:
        pa = prob_prof_a.apply(_sigmoid(3.0 * z), scores_are_logits=False)
        pb = prob_prof_b.apply(_sigmoid(0.35 * z), scores_are_logits=False)
        assert abs(pa - pb) <= 0.05


def test_cross_model_isotonic_parity_and_pava_edge_cases(tmp_path: Path) -> None:
    rng = random.Random(123)
    n_cal = 5000
    true_probs = [rng.uniform(0.01, 0.99) for _ in range(n_cal)]
    labels = [1 if rng.random() < p else 0 for p in true_probs]

    # Model 1: right-skewed non-linear power transform p^3.0
    # Model 2: left-skewed shifted logit 2.5 * logit(p) + 3.5
    scores_1 = [p**3.0 for p in true_probs]
    scores_2 = [2.5 * _logit(p) + 3.5 for p in true_probs]

    engine = CalibrationEngine(profile_dir=tmp_path)
    profile_1 = engine.fit_isotonic("skewed_right", scores_1, labels)
    profile_2 = engine.fit_isotonic("skewed_left_shifted", scores_2, labels)

    assert profile_1.method == CalibrationMethod.ISOTONIC
    assert profile_2.method == CalibrationMethod.ISOTONIC
    assert len(profile_1.isotonic_x_knots) >= 2
    assert len(profile_2.isotonic_x_knots) >= 2
    assert profile_1.ece <= 0.05
    assert profile_2.ece <= 0.05
    assert profile_1.passes_ece_gate
    assert profile_2.passes_ece_gate

    # Evaluate across interior test quantiles [0.10, 0.90]
    interior_quantiles = [0.10 + 0.01 * i for i in range(81)]
    diffs = [
        abs(profile_1.apply(q**3.0) - profile_2.apply(2.5 * _logit(q) + 3.5))
        for q in interior_quantiles
    ]
    mean_diff = sum(diffs) / len(diffs)
    max_diff = max(diffs)
    assert mean_diff <= 0.05
    assert max_diff <= 0.05

    # Held-out test ECE check
    n_test = 1500
    holdout_p = [rng.uniform(0.01, 0.99) for _ in range(n_test)]
    holdout_y = [1 if rng.random() < p else 0 for p in holdout_p]
    holdout_ece_1 = CalibrationEngine.compute_ece(
        [profile_1.apply(p**3.0) for p in holdout_p],
        holdout_y,
    )
    holdout_ece_2 = CalibrationEngine.compute_ece(
        [profile_2.apply(2.5 * _logit(p) + 3.5) for p in holdout_p],
        holdout_y,
    )
    assert holdout_ece_1 <= 0.05
    assert holdout_ece_2 <= 0.05

    # PAVA edge cases: identical scores -> 2 knots around x0; inverted -> 1 pooled block [min_x, max_x]
    identical = engine.fit_isotonic("iso_identical", [0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1])
    assert len(identical.isotonic_x_knots) == 2
    assert identical.isotonic_x_knots[0] < identical.isotonic_x_knots[1]
    assert identical.isotonic_y_knots == pytest.approx([0.5, 0.5])
    assert identical.apply(0.5) == pytest.approx(0.5)

    collapsed = engine.fit_isotonic("iso_collapsed", [0.1, 0.2, 0.3, 0.4], [1, 1, 0, 0])
    assert collapsed.isotonic_x_knots == pytest.approx([0.1, 0.4])
    assert collapsed.isotonic_y_knots == pytest.approx([0.5, 0.5])

    tied = engine.fit_isotonic("iso_tied", [0.8, 0.2, 0.8, 0.2], [1, 0, 1, 1])
    assert tied.isotonic_x_knots == pytest.approx([0.2, 0.8])
    assert tied.isotonic_y_knots == pytest.approx([0.5, 1.0])


async def test_fit_driver_end_to_end_gold_dataset_and_persistence(tmp_path: Path) -> None:
    gold_path = Path(__file__).resolve().parent.parent / "benchmarks" / "calibration_gold.jsonl"
    samples, expected_sha256 = CalibrationEngine.load_golden_samples(gold_path)
    assert len(samples) >= 6
    assert expected_sha256 == hashlib.sha256(gold_path.read_bytes()).hexdigest()

    # Overconfident logits: +4.0 for positives, -4.0 for negatives -> calibrated probability near 0.98/0.02
    score_map = {s.id: (4.0 if s.label == 1 else -4.0) for s in samples}
    cloud_driver = _ConfigurableDriver(
        "decision1",
        is_cloud=True,
        supports_parallel_heads=True,
        max_candidates_per_pass=1000,
        scores_are_logits=True,
        score_map=score_map,
    )

    engine = CalibrationEngine(profile_dir=tmp_path / "profiles")
    assert engine.load_profile("decision1") is None

    profile = await engine.fit_driver(
        cloud_driver,
        samples=None,
        method=CalibrationMethod.TEMPERATURE_SCALING,
        dataset_path=gold_path,
        persist=True,
    )
    assert cloud_driver.calibration_profile == profile
    assert profile.driver_id == "decision1"
    assert profile.sample_count == len(samples)
    assert profile.dataset_sha256 == expected_sha256
    assert profile.ece <= 0.05
    assert profile.passes_ece_gate

    persisted_path = tmp_path / "profiles" / "decision1.json"
    assert persisted_path.is_file()
    reloaded = engine.load_profile("decision1")
    assert reloaded == profile

    # Verify task grouping and head category mapping on recorded requests
    assert len(cloud_driver.recorded_requests) == len({s.task for s in samples})
    all_heads = [head for req in cloud_driver.recorded_requests for head in req.heads]
    categories_by_id = {head.head_id: head.category for head in all_heads}
    assert categories_by_id["cal_01"] == HeadCategory.CANDIDATE
    assert categories_by_id["cal_03"] == HeadCategory.PRAGMATIC
    assert categories_by_id["cal_05"] == HeadCategory.INVARIANT

    # Verify Driver.classify uses the attached CalibrationProfile
    resp = await cloud_driver.classify(cloud_driver.recorded_requests[0])
    assert resp.is_calibrated is True
    for hid, raw in resp.raw_scores.items():
        assert resp.calibrated_probabilities[hid] == pytest.approx(
            profile.apply(raw, scores_are_logits=True)
        )

    # Verify persist=False skips disk write and ISOTONIC fits cleanly
    local_driver = _ConfigurableDriver(
        "onnx_local",
        is_cloud=False,
        supports_parallel_heads=False,
        max_candidates_per_pass=32,
        scores_are_logits=True,
        score_map=score_map,
    )
    iso_profile = await engine.fit_driver(
        local_driver,
        samples=samples,
        method=CalibrationMethod.ISOTONIC,
        persist=False,
    )
    assert iso_profile.method == CalibrationMethod.ISOTONIC
    assert iso_profile.dataset_sha256 is None
    assert iso_profile.ece <= 0.05
    assert not (tmp_path / "profiles" / "onnx_local.json").exists()

    # Verify chunking respects hardware-aware candidate budget (70 shared-task samples -> 32, 32, 6)
    shared_samples = [
        CalibrationSample(
            id=f"chunk_{i:02d}",
            task="shared task for chunking",
            candidate=f"file_{i:02d}.py",
            label=(1 if i % 2 == 0 else 0),
        )
        for i in range(70)
    ]
    chunk_score_map = {s.id: (2.0 if s.label == 1 else -2.0) for s in shared_samples}
    chunk_driver = _ConfigurableDriver(
        "layaa",
        is_cloud=False,
        supports_parallel_heads=False,
        max_candidates_per_pass=32,
        scores_are_logits=True,
        score_map=chunk_score_map,
    )
    await engine.fit_driver(chunk_driver, samples=shared_samples, persist=False)
    assert [len(req.heads) for req in chunk_driver.recorded_requests] == [32, 32, 6]


def test_hardware_aware_candidate_budgeting() -> None:
    d1 = _ConfigurableDriver(
        "decision1",
        is_cloud=True,
        supports_parallel_heads=True,
        max_candidates_per_pass=1000,
    )
    jev_800 = _ConfigurableDriver(
        "jev",
        is_cloud=True,
        supports_parallel_heads=True,
        max_candidates_per_pass=800,
    )
    jev_1000 = _ConfigurableDriver(
        "jev",
        is_cloud=True,
        supports_parallel_heads=True,
        max_candidates_per_pass=1000,
    )
    jev_local = _ConfigurableDriver(
        "jev",
        is_cloud=False,
        supports_parallel_heads=True,
        max_candidates_per_pass=900,
    )
    layaa = _ConfigurableDriver(
        "layaa",
        is_cloud=False,
        supports_parallel_heads=False,
        max_candidates_per_pass=32,
    )
    layaa_high_cap = _ConfigurableDriver(
        "layaa",
        is_cloud=False,
        supports_parallel_heads=False,
        max_candidates_per_pass=128,
    )
    onnx_local = _ConfigurableDriver(
        "onnx_local",
        is_cloud=False,
        supports_parallel_heads=False,
        max_candidates_per_pass=32,
    )
    zero_dep = _ConfigurableDriver(
        "zero_dep",
        is_cloud=False,
        supports_parallel_heads=True,
        max_candidates_per_pass=256,
        scores_are_logits=False,
    )
    logprob = _ConfigurableDriver(
        "logprob_fallback",
        is_cloud=True,
        supports_parallel_heads=False,
        max_candidates_per_pass=64,
        scores_are_logits=False,
    )

    assert CalibrationEngine.candidate_budget_for_driver(d1) == 800
    assert CalibrationEngine.candidate_budget_for_driver(jev_800) == 800
    assert CalibrationEngine.candidate_budget_for_driver(jev_1000) == 800
    assert CalibrationEngine.candidate_budget_for_driver(jev_local) == 800
    assert CalibrationEngine.candidate_budget_for_driver(layaa) == 32
    assert CalibrationEngine.candidate_budget_for_driver(layaa_high_cap) == 32
    assert CalibrationEngine.candidate_budget_for_driver(onnx_local) == 32
    assert CalibrationEngine.candidate_budget_for_driver(zero_dep) == 256
    assert CalibrationEngine.candidate_budget_for_driver(logprob) == 64

    candidates = list(range(1000))
    sliced_cloud = CalibrationEngine.budget_candidates(d1, candidates)
    sliced_layaa = CalibrationEngine.budget_candidates(layaa, candidates)
    sliced_onnx = CalibrationEngine.budget_candidates(onnx_local, candidates)
    assert len(sliced_cloud) == 800
    assert sliced_cloud == candidates[:800]
    assert len(sliced_layaa) == 32
    assert sliced_layaa == candidates[:32]
    assert len(sliced_onnx) == 32
    assert sliced_onnx == candidates[:32]
