"""Deterministic offline replay and connection-pooling tests for cloud ClassifierDrivers."""

from __future__ import annotations

import asyncio
import math
import sys
from typing import Any

import aiohttp
from aiohttp import web
import httpx
import pytest

from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory
from quadgate.drivers.base import CalibrationMethod, CalibrationProfile
from quadgate.drivers.decision1 import Decision1Driver
from quadgate.drivers.jev import JevDriver
from quadgate.drivers.logprob_fallback import LogprobFallbackDriver


def _make_heads(
    count: int,
    *,
    category: HeadCategory = HeadCategory.CANDIDATE,
    prefix: str = "h",
) -> list[ClassifierHead]:
    return [
        ClassifierHead(
            head_id=f"{prefix}_{i}",
            candidate_text=f"candidate_{prefix}_{i}",
            category=category,
        )
        for i in range(count)
    ]


def test_zero_arg_instantiation_and_capabilities() -> None:
    d1 = Decision1Driver()
    assert d1.driver_id == "decision1"
    assert d1.capabilities.is_cloud is True
    assert d1.capabilities.supports_parallel_heads is True
    assert d1.capabilities.max_candidates_per_pass == 1000
    assert d1.capabilities.scores_are_logits is False

    jev = JevDriver()
    assert jev.driver_id == "jev"
    assert jev.capabilities.is_cloud is True
    assert jev.capabilities.supports_parallel_heads is True
    assert jev.capabilities.max_candidates_per_pass == 800
    assert jev.capabilities.scores_are_logits is True

    lp = LogprobFallbackDriver()
    assert lp.driver_id == "logprob_fallback"
    assert lp.capabilities.is_cloud is True
    assert lp.capabilities.supports_parallel_heads is False
    assert lp.capabilities.max_candidates_per_pass == 64
    assert lp.capabilities.scores_are_logits is False


async def test_decision1_slot_allocation_bands_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION1_API_KEY", "d1-env-secret")
    captured: list[tuple[str, str, dict[str, str], dict[str, Any] | None, float]] = []

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        captured.append((method, url, headers, body, timeout_s))
        assert body is not None
        scores = {entry["head_id"]: 0.75 for entry in body["heads"]}
        return 200, {"scores": scores}

    driver = Decision1Driver(transport=transport)
    heads = [
        ClassifierHead(
            head_id="inv_auto_1",
            candidate_text="no secrets",
            category=HeadCategory.INVARIANT,
        ),
        ClassifierHead(
            head_id="inv_explicit",
            candidate_text="cage check",
            category=HeadCategory.INVARIANT,
            slot_index=1,
        ),
        ClassifierHead(
            head_id="prag_1",
            candidate_text="directive",
            category=HeadCategory.PRAGMATIC,
        ),
        ClassifierHead(
            head_id="prag_explicit",
            candidate_text="counterfactual",
            category=HeadCategory.PRAGMATIC,
            slot_index=160,
        ),
        ClassifierHead(
            head_id="cand_1",
            candidate_text="auth.py",
            category=HeadCategory.CANDIDATE,
        ),
        ClassifierHead(
            head_id="cand_explicit",
            candidate_text="jwt.py",
            category=HeadCategory.CANDIDATE,
            slot_index=500,
        ),
    ]
    req = ClassifierRequest(query_text="fix auth bug", heads=heads, timeout_ms=250)
    resp = await driver.classify(req)

    assert len(captured) == 1
    method, url, headers, body, timeout_s = captured[0]
    assert method == "POST"
    assert url == "https://api.decision1.ai/v1/score"
    assert headers["Authorization"] == "Bearer d1-env-secret"
    assert timeout_s == pytest.approx(0.25)
    assert body is not None
    assert body["max_output_tokens"] == 0
    assert body["query"] == "fix auth bug"

    slots_by_id = {entry["head_id"]: entry["slot"] for entry in body["heads"]}
    assert slots_by_id["inv_explicit"] == 1
    assert slots_by_id["inv_auto_1"] == 2  # avoids collision with explicit slot 1
    assert slots_by_id["prag_1"] == 81
    assert slots_by_id["prag_explicit"] == 160
    assert slots_by_id["cand_1"] == 161
    assert slots_by_id["cand_explicit"] == 500
    assert resp.raw_scores["inv_auto_1"] == pytest.approx(0.75)


async def test_decision1_full_1000_head_batch_and_results_parsing() -> None:
    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        assert body is not None
        assert body["max_output_tokens"] == 0
        assert len(body["heads"]) == 1000
        results = [
            {
                "slot": item["slot"],
                "head_id": item["head_id"],
                "score": 0.9 if item["slot"] == 1 else 0.1,
            }
            for item in body["heads"]
        ]
        return 200, {"results": results}

    driver = Decision1Driver(api_key="explicit-key", transport=transport)
    all_heads = (
        _make_heads(80, category=HeadCategory.INVARIANT, prefix="inv")
        + _make_heads(80, category=HeadCategory.PRAGMATIC, prefix="prag")
        + _make_heads(840, category=HeadCategory.CANDIDATE, prefix="cand")
    )
    req = ClassifierRequest(query_text="full capacity test", heads=all_heads)
    scores = await driver.score_raw(req)
    assert len(scores) == 1000
    assert scores["inv_0"] == pytest.approx(0.9)
    assert scores["cand_839"] == pytest.approx(0.1)


async def test_decision1_band_overflow_and_out_of_band_slots_raise() -> None:
    async def never_called(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Transport must not be called on invalid slot allocations")

    driver = Decision1Driver(transport=never_called)

    with pytest.raises(ValueError, match="invariant"):
        inv_over = _make_heads(81, category=HeadCategory.INVARIANT, prefix="inv")
        await driver.score_raw(ClassifierRequest(query_text="q", heads=inv_over))

    with pytest.raises(ValueError, match="pragmatic"):
        prag_over = _make_heads(81, category=HeadCategory.PRAGMATIC, prefix="prag")
        await driver.score_raw(ClassifierRequest(query_text="q", heads=prag_over))

    with pytest.raises(ValueError, match="candidate"):
        cand_over = _make_heads(841, category=HeadCategory.CANDIDATE, prefix="cand")
        await driver.score_raw(ClassifierRequest(query_text="q", heads=cand_over))

    for category, bad_slot in [
        (HeadCategory.INVARIANT, 81),
        (HeadCategory.PRAGMATIC, 80),
        (HeadCategory.PRAGMATIC, 161),
        (HeadCategory.CANDIDATE, 160),
    ]:
        bad_head = ClassifierHead(
            head_id="bad",
            candidate_text="x",
            category=category,
            slot_index=bad_slot,
        )
        with pytest.raises(ValueError, match="slot_index"):
            await driver.score_raw(ClassifierRequest(query_text="q", heads=[bad_head]))


async def test_decision1_airgap_blocks_cloud_egress() -> None:
    called = False

    async def transport(*args: Any) -> tuple[int, dict[str, Any]]:
        nonlocal called
        called = True
        return 200, {"scores": {"h0": 0.5}}

    driver = Decision1Driver(transport=transport)
    req = ClassifierRequest(
        query_text="read private vault",
        heads=_make_heads(1),
        cloud_egress_allowed=False,
    )
    with pytest.raises(PermissionError, match="Privacy Air-Gap"):
        await driver.classify(req)
    with pytest.raises(PermissionError, match="Privacy Air-Gap"):
        await driver.score_raw(req)
    assert called is False


async def test_http2_client_lazy_creation_reuse_and_idempotent_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_clients: list[Any] = []
    requests_seen: list[tuple[str, str]] = []

    class _FakeResponse:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

    class _FakeHttpxClient:
        def __init__(self, *, http2: bool, limits: httpx.Limits) -> None:
            self.http2 = http2
            self.limits = limits
            self.is_closed = False
            self.close_count = 0
            created_clients.append(self)

        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any] | None,
            timeout: float,
        ) -> _FakeResponse:
            requests_seen.append((method, url))
            if method == "GET":
                return _FakeResponse(200, '{"status": "ok"}')
            return _FakeResponse(200, '{"scores": {"h_0": 0.88}}')

        async def aclose(self) -> None:
            self.close_count += 1
            self.is_closed = True

    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)

    driver = Decision1Driver(api_key="pool-key")
    assert driver._client is None  # lazily initialized on first call

    req = ClassifierRequest(query_text="pool check", heads=_make_heads(1))
    await driver.score_raw(req)
    first_client = driver._client
    assert first_client is not None
    assert first_client.http2 is True
    assert first_client.limits.max_connections == 64
    assert first_client.limits.max_keepalive_connections == 32

    await driver.score_raw(req)
    assert await driver.health_check() is True
    assert driver._client is first_client
    assert len(created_clients) == 1
    assert len(requests_seen) == 3

    await driver.close()
    await driver.close()  # idempotent
    assert first_client.close_count == 1
    assert driver._client is None


async def test_aiohttp_fallback_against_local_server_when_h2_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "h2", None)
    call_count = 0

    async def handle_score(request: web.Request) -> web.Response:
        nonlocal call_count
        call_count += 1
        assert request.headers.get("Authorization") == "Bearer local-test-key"
        body = await request.json()
        assert body["max_output_tokens"] == 0
        return web.json_response({"scores": {h["head_id"]: 0.64 for h in body["heads"]}})

    async def handle_health(_: web.Request) -> web.Response:
        return web.json_response({"status": "healthy"})

    app = web.Application()
    app.router.add_post("/v1/score", handle_score)
    app.router.add_get("/v1/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    driver = Decision1Driver(
        endpoint=f"http://127.0.0.1:{port}/v1/score",
        api_key="local-test-key",
    )
    try:
        req = ClassifierRequest(query_text="fallback test", heads=_make_heads(2))
        scores1 = await driver.score_raw(req)
        session = driver._client
        assert isinstance(session, aiohttp.ClientSession)
        assert session.connector is not None
        assert session.connector.limit == 64

        scores2 = await driver.score_raw(req)
        assert driver._client is session
        assert await driver.health_check() is True
        assert call_count == 2
        assert scores1 == {"h_0": pytest.approx(0.64), "h_1": pytest.approx(0.64)}
        assert scores2 == scores1
    finally:
        await driver.close()
        await driver.close()
        await runner.cleanup()


async def test_jev_env_loading_url_normalization_and_800_head_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_BASE_URL", "http://127.0.0.1:9091")
    monkeypatch.setenv("JEV_API_KEY", "jev-env-token")

    captured: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, Any]:
        captured.append((method, url, headers, body))
        if method == "GET":
            return 200, {"status": "ok"}
        assert body is not None
        assert len(body["candidates"]) == 800
        assert len(body["pairs"]) == 800
        return 200, {"scores": [float(i - 400) / 100.0 for i in range(800)]}

    driver = JevDriver(transport=transport)
    assert driver.endpoint == "http://127.0.0.1:9091/v1/classify"
    assert driver.api_key == "jev-env-token"
    assert driver.capabilities.max_candidates_per_pass == 800

    heads = _make_heads(800)
    resp = await driver.classify(ClassifierRequest(query_text="search auth", heads=heads))
    assert resp.raw_scores["h_0"] == pytest.approx(-4.0)
    assert resp.raw_scores["h_799"] == pytest.approx(3.99)
    assert captured[0][1] == "http://127.0.0.1:9091/v1/classify"
    assert captured[0][2]["Authorization"] == "Bearer jev-env-token"
    assert await driver.health_check() is True
    await driver.close()

    with pytest.raises(ValueError, match="at most 800"):
        await driver.classify(ClassifierRequest(query_text="too many", heads=_make_heads(801)))


async def test_jev_response_formats_and_airgap_behavior() -> None:
    heads = _make_heads(3)
    responses: list[Any] = [
        {"scores": {"h_0": 1.5, "h_1": -0.5, "h_2": 0.25}},
        {"scores": [1.5, -0.5, 0.25]},
        [
            {"index": 2, "score": 0.25},
            {"index": 0, "score": 1.5},
            {"index": 1, "score": -0.5},
        ],
        {
            "results": [
                {"index": 1, "relevance_score": -0.5},
                {"index": 0, "score": 1.5},
                {"index": 2, "score": 0.25},
            ]
        },
    ]
    idx = 0

    async def transport(*args: Any) -> tuple[int, Any]:
        nonlocal idx
        payload = responses[idx]
        idx += 1
        return 200, payload

    cloud_jev = JevDriver(
        endpoint="http://127.0.0.1:9091/rerank",
        is_cloud=True,
        transport=transport,
    )
    assert cloud_jev.endpoint == "http://127.0.0.1:9091/rerank"
    expected_scores = {
        "h_0": pytest.approx(1.5),
        "h_1": pytest.approx(-0.5),
        "h_2": pytest.approx(0.25),
    }
    for _ in responses:
        parsed = await cloud_jev.score_raw(ClassifierRequest(query_text="q", heads=heads))
        assert parsed == expected_scores

    airgap_req = ClassifierRequest(query_text="secret", heads=heads, cloud_egress_allowed=False)
    with pytest.raises(PermissionError, match="Privacy Air-Gap"):
        await cloud_jev.classify(airgap_req)

    idx = 0
    local_jev = JevDriver(is_cloud=False, transport=transport)
    local_resp = await local_jev.classify(airgap_req)
    assert local_resp.raw_scores["h_0"] == pytest.approx(1.5)


async def test_logprob_fallback_openai_mode_payload_math_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    captured: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        captured.append((method, url, headers, body))
        if method == "GET":
            return 200, {"data": []}
        assert body is not None
        user_msg = body["messages"][1]["content"]
        if "cand_yes" in user_msg:
            top_lps = [
                {"token": "YES", "logprob": -0.2},
                {"token": " yes", "logprob": -2.5},
                {"token": "NO", "logprob": -1.8},
            ]
        else:
            top_lps = [
                {"token": "NO", "logprob": -0.1},
                {"token": "YES", "logprob": -3.0},
            ]
        return 200, {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "YES"},
                    "logprobs": {
                        "content": [{"token": "YES", "logprob": -0.2, "top_logprobs": top_lps}]
                    },
                }
            ],
            "usage": {"prompt_tokens": 30, "completion_tokens": 1, "total_tokens": 31},
        }

    driver = LogprobFallbackDriver(provider="openai", transport=transport)
    heads = [
        ClassifierHead(head_id="h_yes", candidate_text="cand_yes"),
        ClassifierHead(head_id="h_no", candidate_text="cand_no"),
    ]
    resp = await driver.classify(ClassifierRequest(query_text="match?", heads=heads))

    expected_yes = (math.exp(-0.2) + math.exp(-2.5)) / (
        math.exp(-0.2) + math.exp(-2.5) + math.exp(-1.8)
    )
    expected_no = math.exp(-3.0) / (math.exp(-3.0) + math.exp(-0.1))

    assert resp.raw_scores["h_yes"] == pytest.approx(expected_yes, rel=1e-6)
    assert resp.raw_scores["h_no"] == pytest.approx(expected_no, rel=1e-6)
    assert resp.calibrated_probabilities["h_yes"] == pytest.approx(expected_yes, rel=1e-6)
    assert resp.tokens_used == 62

    post_calls = [c for c in captured if c[0] == "POST"]
    assert len(post_calls) == 2
    for _, url, headers, body in post_calls:
        assert url == "https://api.openai.com/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-test-openai"
        assert body is not None
        assert body["max_tokens"] == 1
        assert body["temperature"] == 0
        assert body["logprobs"] is True
        assert body["top_logprobs"] == 5

    assert await driver.health_check() is True
    assert captured[-1][0] == "GET"
    assert captured[-1][1] == "https://api.openai.com/v1/models"
    await driver.close()
    await driver.close()


async def test_logprob_fallback_gemini_mode_payload_math_and_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini-secret")

    in_flight = 0
    max_seen_in_flight = 0
    captured_bodies: list[dict[str, Any]] = []
    captured_urls: list[str] = []
    captured_headers: list[dict[str, str]] = []

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        nonlocal in_flight, max_seen_in_flight
        captured_urls.append(url)
        captured_headers.append(headers)
        if method == "GET":
            return 200, {"name": "models/gemini-2.5-flash"}
        assert body is not None
        captured_bodies.append(body)
        in_flight += 1
        max_seen_in_flight = max(max_seen_in_flight, in_flight)
        try:
            await asyncio.sleep(0.01)
        finally:
            in_flight -= 1

        return 200, {
            "candidates": [
                {
                    "content": {"parts": [{"text": "YES"}]},
                    "logprobsResult": {
                        "topCandidates": [
                            {
                                "candidates": [
                                    {"token": "YES", "logProbability": -0.4},
                                    {"token": "NO", "logProbability": -1.2},
                                ]
                            }
                        ]
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 25,
                "candidatesTokenCount": 1,
                "totalTokenCount": 26,
            },
        }

    profile = CalibrationProfile(
        driver_id="logprob_fallback",
        method=CalibrationMethod.IDENTITY,
        sample_count=10,
        ece=0.01,
    )
    driver = LogprobFallbackDriver(
        provider="auto",
        calibration_profile=profile,
        max_concurrency=3,
        transport=transport,
    )
    heads = _make_heads(7)
    resp = await driver.classify(ClassifierRequest(query_text="gemini check", heads=heads))

    expected_p = math.exp(-0.4) / (math.exp(-0.4) + math.exp(-1.2))
    assert max_seen_in_flight <= 3
    assert resp.is_calibrated is True
    assert resp.tokens_used == 7 * 26
    assert all(score == pytest.approx(expected_p) for score in resp.raw_scores.values())

    for url in captured_urls:
        assert "generativelanguage.googleapis.com" in url
        assert url.endswith(":generateContent")
    for hdrs in captured_headers:
        assert hdrs["x-goog-api-key"] == "AIza-gemini-secret"
    for body in captured_bodies:
        assert body["generationConfig"] == {
            "maxOutputTokens": 1,
            "temperature": 0.0,
            "responseLogprobs": True,
            "logprobs": 5,
        }

    assert await driver.health_check() is True
    assert captured_urls[-1].endswith("/models/gemini-2.5-flash")
    await driver.close()


async def test_health_check_never_raises_on_transport_error() -> None:
    async def failing_transport(*args: Any) -> tuple[int, dict[str, Any]]:
        raise ConnectionError("network unreachable")

    async def unhealthy_transport(*args: Any) -> tuple[int, dict[str, Any]]:
        return 503, {"status": "down"}

    for cls in (Decision1Driver, JevDriver, LogprobFallbackDriver):
        assert await cls(transport=failing_transport).health_check() is False
        assert await cls(transport=unhealthy_transport).health_check() is False


async def test_real_httpx_pool_against_local_server() -> None:
    seen_auth: list[str | None] = []

    async def handle_score(request: web.Request) -> web.Response:
        seen_auth.append(request.headers.get("Authorization"))
        body = await request.json()
        results = [{"slot": h["slot"], "score": 0.42} for h in body["heads"]]
        return web.json_response({"results": results})

    async def handle_health(_: web.Request) -> web.Response:
        return web.Response(status=200, text="ok")

    app = web.Application()
    app.router.add_post("/v1/score", handle_score)
    app.router.add_get("/v1/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    driver = Decision1Driver(
        endpoint=f"http://127.0.0.1:{port}/v1/score",
        api_key="live-httpx-key",
    )
    try:
        heads = _make_heads(2)
        s1 = await driver.score_raw(ClassifierRequest(query_text="q1", heads=heads))
        client = driver._client
        assert isinstance(client, httpx.AsyncClient)
        s2 = await driver.score_raw(ClassifierRequest(query_text="q2", heads=heads))
        assert driver._client is client
        assert await driver.health_check() is True
        assert s1 == {"h_0": pytest.approx(0.42), "h_1": pytest.approx(0.42)}
        assert s2 == s1
        assert seen_auth == ["Bearer live-httpx-key", "Bearer live-httpx-key"]
    finally:
        await driver.close()
        await driver.close()
        await runner.cleanup()


async def test_decision1_duplicate_slot_and_http_error_raise() -> None:
    async def error_transport(*args: Any) -> tuple[int, dict[str, Any]]:
        return 500, {"error": "backend failure"}

    driver = Decision1Driver(transport=error_transport)
    dup_heads = [
        ClassifierHead(
            head_id="a",
            candidate_text="a",
            category=HeadCategory.INVARIANT,
            slot_index=5,
        ),
        ClassifierHead(
            head_id="b",
            candidate_text="b",
            category=HeadCategory.INVARIANT,
            slot_index=5,
        ),
    ]
    with pytest.raises(ValueError, match="Duplicate slot_index"):
        await driver.score_raw(ClassifierRequest(query_text="q", heads=dup_heads))

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await driver.score_raw(ClassifierRequest(query_text="q", heads=_make_heads(1)))


async def test_logprob_fallback_edge_cases_and_concurrent_token_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-google-key")

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        assert headers["x-goog-api-key"] == "AIza-google-key"
        assert body is not None
        prompt = body["contents"][0]["parts"][0]["text"]
        if "only_yes" in prompt:
            top = [{"token": "YES", "logProbability": -0.05}]
            toks = 10
        elif "only_no" in prompt:
            top = [{"token": "NO", "logProbability": -0.05}]
            toks = 20
        else:
            top = []
            toks = 30
        await asyncio.sleep(0.005)
        return 200, {
            "candidates": [
                {
                    "content": {"parts": [{"text": "NO"}]},
                    "logprobsResult": {"topCandidates": [{"candidates": top}]} if top else {},
                }
            ],
            "usageMetadata": {"promptTokenCount": toks - 1, "candidatesTokenCount": 1},
        }

    driver = LogprobFallbackDriver(
        endpoint="http://127.0.0.1:8888",
        provider="auto",
        transport=transport,
    )
    assert driver._resolve_provider() == "gemini"

    req_a = ClassifierRequest(
        query_text="q",
        heads=[
            ClassifierHead(head_id="y", candidate_text="only_yes"),
            ClassifierHead(head_id="n", candidate_text="only_no"),
        ],
    )
    req_b = ClassifierRequest(
        query_text="q",
        heads=[ClassifierHead(head_id="fb", candidate_text="fallback_txt")],
    )
    resp_a, resp_b = await asyncio.gather(driver.classify(req_a), driver.classify(req_b))

    assert resp_a.raw_scores["y"] == pytest.approx(1.0)
    assert resp_a.raw_scores["n"] == pytest.approx(0.0)
    assert resp_a.tokens_used == 30
    assert resp_b.raw_scores["fb"] == pytest.approx(0.0)
    assert resp_b.tokens_used == 30

    with pytest.raises(PermissionError, match="Privacy Air-Gap"):
        await driver.classify(
            ClassifierRequest(
                query_text="secret",
                heads=_make_heads(1),
                cloud_egress_allowed=False,
            )
        )
    with pytest.raises(ValueError, match="at most 64"):
        await driver.classify(ClassifierRequest(query_text="too many", heads=_make_heads(65)))


async def test_decision1_gemini_decisions_mode_payload_answers_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION1_API_KEY", "AIza-fake-gemini-key")
    captured: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    async def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        captured.append((method, url, headers, body))
        if method == "GET":
            return 200, {"name": "models/gemini-decision-1"}
        assert body is not None
        assert body["model"] == "gemini-decision-1"
        assert body["input"] == "Fix JWT leeway bug"
        assert "slot_1" in body["questions"]
        assert "slot_161" in body["questions"]
        return 200, {
            "answers": {
                "slot_1": {"type": "noul", "noul": 0.02, "confidence": 0.98},
                "slot_161": {"type": "noul", "noul": 0.95, "confidence": 0.95},
            },
            "usage": {"total_input_tokens": 200, "total_output_tokens": 0, "total_tokens": 200},
        }

    driver = Decision1Driver(
        endpoint="https://generativelanguage.googleapis.com/v1beta/decisions",
        transport=transport,
    )
    heads = [
        ClassifierHead(
            head_id="INV_01_SECRET_EXPOSURE",
            candidate_text="Never expose secrets",
            category=HeadCategory.INVARIANT,
        ),
        ClassifierHead(
            head_id="cand:0",
            candidate_text="auth/jwt.py",
            category=HeadCategory.CANDIDATE,
        ),
    ]
    resp = await driver.classify(ClassifierRequest(query_text="Fix JWT leeway bug", heads=heads))
    assert resp.raw_scores["INV_01_SECRET_EXPOSURE"] == pytest.approx(0.02)
    assert resp.raw_scores["cand:0"] == pytest.approx(0.95)
    assert resp.tokens_used == 200
    assert resp.estimated_cost_usd == pytest.approx(200 * 0.10 / 1_000_000.0)
    assert captured[0][1] == "https://generativelanguage.googleapis.com/v1beta/decisions"
    assert captured[0][2]["x-goog-api-key"] == "AIza-fake-gemini-key"
    assert await driver.health_check() is True
    assert captured[-1][1] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-decision-1"
