"""Unit and latency tests for local classifier drivers (`ZeroDepDriver`, `LocalOnnxDriver`, `LayaaDriver`)."""

from __future__ import annotations

import ast
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from aiohttp import web
from tokenizers import Tokenizer, models, pre_tokenizers  # type: ignore[import-untyped]

from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory
from quadgate.drivers.base import CalibrationMethod, CalibrationProfile
from quadgate.drivers.layaa import LayaaDriver
from quadgate.drivers.onnx_local import LocalOnnxDriver, ZeroDepDriver
from quadgate.kernel.calibration import CalibrationEngine

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = REPO_ROOT / "benchmarks" / "calibration_gold.jsonl"


class _MockNodeArg:
    def __init__(self, name: str) -> None:
        self.name = name


class _MockOnnxSession:
    """In-memory mock matching `onnxruntime.InferenceSession` contract."""

    def __init__(
        self,
        *,
        mode: str = "2class",
        include_token_type_ids: bool = True,
    ) -> None:
        self.mode = mode
        self.include_token_type_ids = include_token_type_ids
        self.calls: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[_MockNodeArg]:
        inputs = [_MockNodeArg("input_ids"), _MockNodeArg("attention_mask")]
        if self.include_token_type_ids:
            inputs.append(_MockNodeArg("token_type_ids"))
        return inputs

    def run(self, output_names: Any, input_feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert output_names is None
        self.calls.append({k: np.array(v, copy=True) for k, v in input_feed.items()})
        input_ids = input_feed["input_ids"]
        batch_size = input_ids.shape[0]
        # Produce deterministic logits derived from batch index and non-pad token counts.
        pos_scores = np.array(
            [1.5 - 0.1 * i + 0.01 * float(np.sum(input_feed["attention_mask"][i])) for i in range(batch_size)],
            dtype=np.float32,
        )
        if self.mode == "1d":
            return [pos_scores]
        if self.mode == "1col":
            return [pos_scores[:, None]]
        if self.mode == "2class":
            neg_scores = np.zeros_like(pos_scores) - 0.25
            return [np.stack([neg_scores, pos_scores], axis=1)]
        raise ValueError(f"Unknown mock mode: {self.mode}")


def _make_heads(count: int, *, prefix: str = "cand") -> list[ClassifierHead]:
    return [
        ClassifierHead(
            head_id=f"{prefix}_{idx}",
            candidate_text=f"src/module_{idx}/service_{idx}.py: process_item_{idx}(payload)",
            category=HeadCategory.CANDIDATE,
        )
        for idx in range(count)
    ]


def _write_temp_wordpiece_tokenizer(dest: Path) -> Path:
    vocab = {
        "[PAD]": 0,
        "[UNK]": 1,
        "[CLS]": 2,
        "[SEP]": 3,
        "fix": 4,
        "jwt": 5,
        "token": 6,
        "auth": 7,
        "login": 8,
        "verify": 9,
        "billing": 10,
        "stripe": 11,
    }
    tok = Tokenizer(models.WordPiece(vocab=vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    tok.enable_truncation(max_length=64)
    tok.save(str(dest))
    return dest


# ---------------------------------------------------------------------------
# Optional Dependency / Lazy Import Rule Tests
# ---------------------------------------------------------------------------


def test_onnxruntime_not_imported_at_module_top_level() -> None:
    for rel in ("quadgate/drivers/onnx_local.py", "quadgate/drivers/layaa.py"):
        file_path = REPO_ROOT / rel
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("onnxruntime"), (
                        f"Top-level import of {alias.name} forbidden in {rel}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("onnxruntime"), (
                    f"Top-level from-import of {mod} forbidden in {rel}"
                )

    probe = (
        "import sys; "
        "import quadgate.drivers.onnx_local, quadgate.drivers.layaa; "
        "assert 'onnxruntime' not in sys.modules"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ---------------------------------------------------------------------------
# ZeroDepDriver Tests
# ---------------------------------------------------------------------------


async def test_zero_dep_contracts_airgap_and_budget() -> None:
    driver = ZeroDepDriver()
    assert driver.driver_id == "zero_dep"
    assert driver.capabilities.is_cloud is False
    assert driver.capabilities.supports_parallel_heads is True
    assert driver.capabilities.max_candidates_per_pass == 256
    assert driver.capabilities.scores_are_logits is False
    assert await driver.health_check() is True

    req_airgap = ClassifierRequest(
        query_text="Fix service_0 process_item_0 payload validation",
        heads=_make_heads(4),
        cloud_egress_allowed=False,
    )
    resp = await driver.classify(req_airgap)
    assert resp.driver_id == "zero_dep"
    assert all(0.0 <= prob <= 1.0 for prob in resp.calibrated_probabilities.values())
    assert resp.calibrated_probabilities["cand_0"] > resp.calibrated_probabilities["cand_3"]

    over_budget = ClassifierRequest(query_text="test", heads=_make_heads(257))
    with pytest.raises(ValueError, match="256"):
        await driver.classify(over_budget)

    await driver.close()


async def test_zero_dep_calibration_gold_discrimination() -> None:
    driver = ZeroDepDriver()
    rows = [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 6

    scores_by_id: dict[str, float] = {}
    for row in rows:
        head_type = row.get("head_type") or ""
        category = (
            HeadCategory.PRAGMATIC
            if "counterfactual" in head_type
            else HeadCategory.INVARIANT
            if head_type.startswith("INV_")
            else HeadCategory.CANDIDATE
        )
        req = ClassifierRequest(
            query_text=row["task"],
            heads=[
                ClassifierHead(
                    head_id=row["id"],
                    candidate_text=row["candidate"],
                    category=category,
                )
            ],
            cloud_egress_allowed=False,
        )
        raw = await driver.score_raw(req)
        score = raw[row["id"]]
        assert 0.0 <= score <= 1.0
        scores_by_id[row["id"]] = score
        if row["label"] == 1:
            assert score >= 0.55, f"Expected positive {row['id']} >= 0.55, got {score}"
        else:
            assert score < 0.35, f"Expected negative {row['id']} < 0.35, got {score}"

    # Verify pairwise ordering across all 3 gold pairs
    assert scores_by_id["cal_01"] > scores_by_id["cal_02"]
    assert scores_by_id["cal_03"] > scores_by_id["cal_04"]
    assert scores_by_id["cal_05"] > scores_by_id["cal_06"]

    # Also test cal_01 vs cal_02 scored jointly in one request + camelCase/snake_case matching
    joint_req = ClassifierRequest(
        query_text="Fix JWT token expiration leeway bug in login verification",
        heads=[
            ClassifierHead(head_id="cal_01", candidate_text=rows[0]["candidate"]),
            ClassifierHead(head_id="cal_02", candidate_text=rows[1]["candidate"]),
        ],
    )
    joint_scores = await driver.score_raw(joint_req)
    assert joint_scores["cal_01"] > joint_scores["cal_02"]

    camel_req = ClassifierRequest(
        query_text="fix rate limiter throttling",
        heads=[
            ClassifierHead(
                head_id="limiter",
                candidate_text="src/gateway/HttpRateLimiter.ts: checkRateLimiterWindow(clientId)",
            ),
            ClassifierHead(
                head_id="billing",
                candidate_text="src/billing/InvoicePdfRenderer.ts: renderMonthlyPdf(acct)",
            ),
        ],
    )
    camel_scores = await driver.score_raw(camel_req)
    assert camel_scores["limiter"] > camel_scores["billing"]

    # End-to-end calibration engine integration with ZeroDepDriver on gold JSONL
    engine = CalibrationEngine(max_allowed_ece=0.15)
    profile = await engine.fit_driver(
        driver,
        method=CalibrationMethod.TEMPERATURE_SCALING,
        dataset_path=GOLD_PATH,
        persist=False,
    )
    assert profile.driver_id == "zero_dep"
    assert profile.sample_count >= 6
    assert driver.calibration_profile is profile


async def test_zero_dep_32_candidate_latency() -> None:
    driver = ZeroDepDriver()
    heads = _make_heads(32)
    heads[12] = ClassifierHead(
        head_id="cand_12",
        candidate_text="src/auth/jwt_validator.py: verify_token(token, leeway_sec), TokenExpiredError",
    )
    req = ClassifierRequest(
        query_text="Fix JWT token expiration leeway bug in login verification",
        heads=heads,
        cloud_egress_allowed=False,
    )
    # Warm-up
    await driver.score_raw(req)

    latencies_ms: list[float] = []
    for _ in range(15):
        t0 = time.perf_counter()
        scores = await driver.score_raw(req)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert max(scores, key=scores.__getitem__) == "cand_12"

    assert statistics.mean(latencies_ms) < 25.0
    assert statistics.median(latencies_ms) < 25.0
    assert max(latencies_ms) < 65.0


# ---------------------------------------------------------------------------
# LocalOnnxDriver Tests
# ---------------------------------------------------------------------------


async def test_local_onnx_batch_scoring_shapes_and_latency(tmp_path: Path) -> None:
    tok_path = _write_temp_wordpiece_tokenizer(tmp_path / "tokenizer.json")
    model_path = tmp_path / "modernbert-int8.onnx"

    # [B, 2] positive-minus-negative logit mode with real tokenizers.Tokenizer
    sess_2class = _MockOnnxSession(mode="2class", include_token_type_ids=False)
    driver_2c = LocalOnnxDriver(
        model_path=str(model_path),
        tokenizer_path=str(tok_path),
        session_factory=lambda mp, dev: sess_2class,
    )
    assert driver_2c.driver_id == "onnx_local"
    assert driver_2c.capabilities.is_cloud is False
    assert driver_2c.capabilities.supports_parallel_heads is False
    assert driver_2c.capabilities.max_candidates_per_pass == 32
    assert driver_2c.capabilities.scores_are_logits is True
    assert await driver_2c.health_check() is True

    req_2 = ClassifierRequest(
        query_text="fix jwt token login",
        heads=[
            ClassifierHead(head_id="h1", candidate_text="verify jwt token auth"),
            ClassifierHead(head_id="h2", candidate_text="stripe billing"),
        ],
        cloud_egress_allowed=False,
    )
    scores_2c = await driver_2c.score_raw(req_2)
    assert len(sess_2class.calls) == 1
    call_feed = sess_2class.calls[0]
    assert set(call_feed.keys()) == {"input_ids", "attention_mask"}
    assert call_feed["input_ids"].shape[0] == 2
    assert call_feed["input_ids"].dtype == np.int64
    # Expected positive - (-0.25) = pos + 0.25
    mask0_sum = float(np.sum(call_feed["attention_mask"][0]))
    mask1_sum = float(np.sum(call_feed["attention_mask"][1]))
    assert scores_2c["h1"] == pytest.approx((1.5 + 0.01 * mask0_sum) - (-0.25), abs=1e-4)
    assert scores_2c["h2"] == pytest.approx((1.4 + 0.01 * mask1_sum) - (-0.25), abs=1e-4)

    # [B, 1] logit mode with fallback tokenizer and 32 candidates latency check
    sess_1col = _MockOnnxSession(mode="1col", include_token_type_ids=True)
    driver_1c = LocalOnnxDriver(
        model_path=str(model_path),
        session_factory=sess_1col,
        calibration_profile=CalibrationProfile(
            driver_id="onnx_local",
            method=CalibrationMethod.TEMPERATURE_SCALING,
            temperature=1.0,
        ),
    )
    heads_32 = _make_heads(32)
    req_32 = ClassifierRequest(
        query_text="fix jwt token validation",
        heads=heads_32,
        cloud_egress_allowed=False,
    )
    # Warm fallback tokenizer and threadpool once before timing steady-state calls.
    warm_1c = await driver_1c.classify(req_32)
    assert len(warm_1c.raw_scores) == 32

    latencies_ms: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        resp = await driver_1c.classify(req_32)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert len(resp.raw_scores) == 32
        assert resp.is_calibrated is True
        assert all(0.0 <= p <= 1.0 for p in resp.calibrated_probabilities.values())

    assert "token_type_ids" in sess_1col.calls[0]
    assert sess_1col.calls[0]["input_ids"].shape[0] == 32
    median_ms = statistics.median(latencies_ms)
    mean_ms = statistics.mean(latencies_ms)
    assert mean_ms < 65.0
    assert median_ms < 65.0

    # [B] 1D logit mode
    sess_1d = _MockOnnxSession(mode="1d")
    driver_1d = LocalOnnxDriver(session_factory=lambda: sess_1d)
    scores_1d = await driver_1d.score_raw(req_2)
    assert set(scores_1d.keys()) == {"h1", "h2"}

    # >32 head budget enforcement
    req_33 = ClassifierRequest(query_text="too many", heads=_make_heads(33))
    with pytest.raises(ValueError, match="32"):
        await driver_1c.classify(req_33)
    with pytest.raises(ValueError, match="32"):
        await driver_1c.score_raw(req_33)

    await driver_2c.close()
    await driver_1c.close()
    await driver_1d.close()


async def test_local_onnx_atomic_download_and_missing_runtime_behavior(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "modernbert-int8.onnx"
    tok_path = tmp_path / "models" / "tokenizer.json"

    # Missing model/runtime should not auto-download, health_check=False, score_raw raises
    driver = LocalOnnxDriver(
        model_path=str(model_path),
        tokenizer_path=str(tok_path),
    )
    assert await driver.health_check() is False
    assert not model_path.exists()

    req = ClassifierRequest(
        query_text="Fix JWT token expiration leeway bug in login verification",
        heads=[
            ClassifierHead(
                head_id="cal_01",
                candidate_text="src/auth/jwt_validator.py: verify_token(token, leeway_sec)",
            ),
            ClassifierHead(
                head_id="cal_02",
                candidate_text="src/billing/stripe_webhook.py: handle_invoice_paid(payload)",
            ),
        ],
        cloud_egress_allowed=False,
    )
    with pytest.raises(RuntimeError, match=r"pip install quadgate\[local\] && quadgate init --local"):
        await driver.score_raw(req)

    # Fallback mode when runtime/weights are missing
    fallback_driver = LocalOnnxDriver(
        model_path=str(model_path),
        tokenizer_path=str(tok_path),
        fallback_to_zero_dep=True,
    )
    assert await fallback_driver.health_check() is False
    fb_resp = await fallback_driver.classify(req)
    assert fb_resp.raw_scores["cal_01"] > fb_resp.raw_scores["cal_02"]
    assert fb_resp.calibrated_probabilities["cal_01"] > fb_resp.calibrated_probabilities["cal_02"]
    await fallback_driver.close()

    # Atomic download failure leaves no leftover target or temp files
    async def failing_downloader(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial-bytes")
        raise ConnectionError("simulated download failure")

    with pytest.raises(ConnectionError, match="simulated download failure"):
        await driver.ensure_model_downloaded(downloader=failing_downloader)
    assert not model_path.exists()
    if model_path.parent.exists():
        assert list(model_path.parent.iterdir()) == []

    # Atomic download success writes both model and tokenizer.json cleanly
    seen_urls: list[str] = []

    async def mock_downloader(url: str, dest: Path) -> None:
        seen_urls.append(url)
        assert dest.name.endswith(".tmp")
        if url.endswith("tokenizer.json"):
            _write_temp_wordpiece_tokenizer(dest)
        else:
            dest.write_bytes(b"FAKE_ONNX_MODEL_BYTES")

    downloaded_path = await driver.ensure_model_downloaded(downloader=mock_downloader)
    assert downloaded_path == model_path
    assert model_path.is_file() and model_path.read_bytes() == b"FAKE_ONNX_MODEL_BYTES"
    assert tok_path.is_file()
    assert len(seen_urls) == 2
    assert not any(p.name.endswith(".tmp") for p in model_path.parent.iterdir())
    await driver.close()


# ---------------------------------------------------------------------------
# LayaaDriver Tests (Local ONNX Mode + Local HTTP Endpoint Mode)
# ---------------------------------------------------------------------------


async def test_layaa_local_onnx_mode_and_missing_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAYAA_ENDPOINT", raising=False)
    model_path = tmp_path / "layaa-int8.onnx"
    tok_path = _write_temp_wordpiece_tokenizer(tmp_path / "tokenizer.json")
    assert tok_path.is_file()

    # Missing runtime / weights -> health_check False and RuntimeError
    unavail = LayaaDriver(model_path=str(model_path))
    assert unavail.is_http_mode is False
    assert await unavail.health_check() is False
    req_small = ClassifierRequest(query_text="jwt login", heads=_make_heads(2))
    with pytest.raises(RuntimeError, match="LayaaDriver"):
        await unavail.score_raw(req_small)
    await unavail.close()

    # Mock ONNX mode
    mock_sess = _MockOnnxSession(mode="2class")
    driver = LayaaDriver(
        model_path=str(model_path),
        session_factory=lambda mp, dev: mock_sess,
    )
    assert driver.driver_id == "layaa"
    assert driver.capabilities.is_cloud is False
    assert driver.capabilities.supports_parallel_heads is False
    assert driver.capabilities.max_candidates_per_pass == 32
    assert driver.capabilities.scores_are_logits is True
    assert driver.is_http_mode is False
    assert await driver.health_check() is True

    req_32 = ClassifierRequest(
        query_text="fix jwt token auth",
        heads=_make_heads(32),
        cloud_egress_allowed=False,
    )
    # Warm lazy tokenizer load and threadpool once before measuring steady-state latency.
    warm_resp = await driver.classify(req_32)
    assert len(warm_resp.raw_scores) == 32

    layaa_latencies_ms: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        resp = await driver.classify(req_32)
        layaa_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert len(resp.raw_scores) == 32

    assert statistics.mean(layaa_latencies_ms) < 65.0
    assert statistics.median(layaa_latencies_ms) < 65.0
    assert mock_sess.calls[0]["input_ids"].shape[0] == 32

    with pytest.raises(ValueError, match="32"):
        await driver.classify(ClassifierRequest(query_text="too many", heads=_make_heads(33)))

    await driver.close()


async def test_layaa_http_endpoint_mode_real_server_and_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payloads: list[dict[str, Any]] = []

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "healthy": True})

    async def handle_classify(request: web.Request) -> web.Response:
        body = await request.json()
        captured_payloads.append(body)
        candidates = body["candidates"]
        # Return [B, 2] raw logits to exercise positive-minus-negative extraction over HTTP too.
        logits = [[0.1 * i, 2.0 - 0.1 * i] for i in range(len(candidates))]
        return web.json_response({"logits": logits})

    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/classify", handle_classify)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        sockets = site._server.sockets  # type: ignore[union-attr]
        port = sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        monkeypatch.setenv("LAYAA_ENDPOINT", base_url)
        driver = LayaaDriver()
        try:
            assert driver.is_http_mode is True
            assert await driver.health_check() is True

            # Warm persistent ClientSession and benchmark 32-candidate batch scoring
            heads_32 = _make_heads(32)
            req_32 = ClassifierRequest(
                query_text="locate service_0 module",
                heads=heads_32,
                cloud_egress_allowed=False,
            )
            await driver.score_raw(req_32)
            first_session = driver._http_session
            assert first_session is not None

            latencies_ms: list[float] = []
            for _ in range(5):
                t0 = time.perf_counter()
                resp = await driver.classify(req_32)
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                assert driver._http_session is first_session
                assert len(resp.raw_scores) == 32
                assert resp.raw_scores["cand_0"] == pytest.approx(2.0, abs=1e-5)
                assert resp.raw_scores["cand_1"] == pytest.approx(1.8, abs=1e-5)

            assert max(latencies_ms) < 65.0
            assert captured_payloads[0]["query"] == "locate service_0 module"
            assert len(captured_payloads[0]["candidates"]) == 32
            assert len(captured_payloads[0]["pairs"]) == 32
        finally:
            await driver.close()
        assert driver._http_session is None
    finally:
        await runner.cleanup()

    # After server shutdown, health_check() returns False cleanly without raising
    dead_driver = LayaaDriver(endpoint=f"http://127.0.0.1:{port}/v1/classify")
    assert await dead_driver.health_check() is False
    await dead_driver.close()

    # Also test injected http_transport with dict-keyed scores schema
    async def mock_transport(
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {"status": "ok"}
        assert payload is not None
        return 200, {"scores": {hid: float(idx) for idx, hid in enumerate(payload["head_ids"])}}

    transport_driver = LayaaDriver(
        endpoint="http://127.0.0.1:9999/score",
        http_transport=mock_transport,
    )
    assert await transport_driver.health_check() is True
    tr_scores = await transport_driver.score_raw(
        ClassifierRequest(query_text="test", heads=_make_heads(3))
    )
    assert tr_scores == {"cand_0": 0.0, "cand_1": 1.0, "cand_2": 2.0}
    await transport_driver.close()

    # Unhealthy / HTTP 500 handling via http_transport
    async def error_transport(
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {"healthy": False}
        return 500, {"error": "boom"}

    err_driver = LayaaDriver(
        endpoint="http://127.0.0.1:9999/score",
        http_transport=error_transport,
    )
    assert await err_driver.health_check() is False
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await err_driver.score_raw(ClassifierRequest(query_text="test", heads=_make_heads(2)))
    await err_driver.close()
