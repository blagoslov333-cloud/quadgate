"""Unit tests for hard live USD budget cap, instant HTTP 402 breaker, and Option-1 job scheduling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp.web as web
import pytest

from benchmarks.real_suite.harness import (
    ECC_ROUTER_MODEL_ID,
    _build_pending_jobs,
    _interleave_suite_tasks,
    _select_stratified_ek_tasks,
    compute_receipt_sha256,
    eval_swe_agent_task,
    run_benchmarks,
)
from benchmarks.real_suite.providers import (
    BillingDepletedError,
    BudgetExceededError,
    LLMCallResult,
    LiveBudgetGuard,
    LiveLLMClient,
    SpyDecision1Driver,
    compute_google_call_cost_usd,
    estimate_worst_case_call_usd,
)
from quadgate.contracts import ClassifierHead, ClassifierRequest, ClassifierResponse
from quadgate.drivers.base import CalibrationProfile, DriverCapabilities


async def _start_mock_server(handler: Any) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/v1beta/models/{model}:generateContent", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{port}"


def test_budget_guard_concurrent_invariant() -> None:
    async def _exercise() -> None:
        guard = LiveBudgetGuard(max_spend_usd=0.50)
        peak_committed_plus_reserved = 0.0

        async def _worker(idx: int) -> None:
            nonlocal peak_committed_plus_reserved
            est = 0.08 if idx % 3 == 0 else 0.04
            try:
                res_id = guard.try_reserve(est)
            except BudgetExceededError:
                return
            snap = guard.snapshot()
            peak_committed_plus_reserved = max(
                peak_committed_plus_reserved,
                float(snap["spent_usd"]) + float(snap["reserved_usd"]),
            )
            assert peak_committed_plus_reserved <= 0.50 + 1e-12
            await asyncio.sleep(0.002)
            guard.settle(res_id, est * 0.5)

        await asyncio.gather(*[_worker(i) for i in range(40)])
        final_snap = guard.snapshot()
        assert final_snap["reserved_usd"] == 0.0
        assert final_snap["spent_usd"] <= 0.50 + 1e-12
        assert final_snap["calls_rejected_budget"] > 0

    asyncio.run(_exercise())


def test_live_llm_client_hard_cap_and_zero_overrun_over_http() -> None:
    server_hits = 0
    server_billed_usd = 0.0

    async def _handler(_request: web.Request) -> web.Response:
        nonlocal server_hits, server_billed_usd
        server_hits += 1
        await asyncio.sleep(0.005)
        # gemini-4-carbon: $2.00/1M in, $10.00/1M out -> 5000 in ($0.01) + 1500 out ($0.015) = $0.025/call
        server_billed_usd += 0.025
        return web.json_response(
            {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 5000,
                    "cachedContentTokenCount": 0,
                    "candidatesTokenCount": 500,
                    "thoughtsTokenCount": 1000,
                },
            }
        )

    async def _exercise() -> None:
        runner, base_url = await _start_mock_server(_handler)
        guard = LiveBudgetGuard(max_spend_usd=0.20)
        client = LiveLLMClient(
            api_key="test-key",
            base_url=base_url,
            max_attempts=2,
            budget_guard=guard,
            max_output_tokens_cap=2048,
        )
        try:
            results = await asyncio.gather(
                *[
                    client.generate(
                        "gemini-4-carbon",
                        messages=[{"role": "user", "text": "x" * 1000}],
                        max_output_tokens=8192,
                    )
                    for _ in range(20)
                ],
                return_exceptions=True,
            )
            ok = [r for r in results if not isinstance(r, Exception)]
            rejected = [r for r in results if isinstance(r, BudgetExceededError)]
            assert len(ok) > 0
            assert len(rejected) > 0
            assert len(ok) + len(rejected) == 20
            snap = guard.snapshot()
            assert snap["reserved_usd"] == 0.0
            assert snap["spent_usd"] <= 0.20 + 1e-12
            assert abs(snap["spent_usd"] - server_billed_usd) < 1e-12
            assert server_billed_usd <= 0.20 + 1e-12
            assert server_hits == len(ok)
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(_exercise())


def test_live_llm_client_aborts_immediately_on_402_without_retries() -> None:
    server_hits = 0

    async def _handler(_request: web.Request) -> web.Response:
        nonlocal server_hits
        server_hits += 1
        return web.json_response(
            {"error": {"code": 402, "message": "Payment Required: credits depleted"}},
            status=402,
        )

    async def _exercise() -> None:
        runner, base_url = await _start_mock_server(_handler)
        guard = LiveBudgetGuard(max_spend_usd=5.00)
        client = LiveLLMClient(
            api_key="test-key",
            base_url=base_url,
            max_attempts=5,
            budget_guard=guard,
        )
        try:
            with pytest.raises(BillingDepletedError):
                await client.generate(
                    "gemini-3.8-flash",
                    messages=[{"role": "user", "text": "hello"}],
                )
            assert server_hits == 1
            assert guard.is_billing_aborted()
            assert guard.snapshot()["reserved_usd"] == 0.0

            # Subsequent call must fail cleanly BEFORE hitting HTTP server
            with pytest.raises(BillingDepletedError):
                await client.generate(
                    "gemma-4-31b-it",
                    messages=[{"role": "user", "text": "second call"}],
                )
            assert server_hits == 1
            with pytest.raises(BillingDepletedError):
                guard.check_before_job()
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(_exercise())


def test_http_200_prose_mentioning_billing_required_does_not_trip_402() -> None:
    async def _handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "Vendor SLA audit: billing notice required within 15 days; "
                                        "payment required only after signoff, not HTTP 402."
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 500,
                    "cachedContentTokenCount": 0,
                    "candidatesTokenCount": 40,
                    "thoughtsTokenCount": 0,
                },
            }
        )

    async def _exercise() -> None:
        runner, base_url = await _start_mock_server(_handler)
        guard = LiveBudgetGuard(max_spend_usd=5.00)
        client = LiveLLMClient(
            api_key="test-key",
            base_url=base_url,
            max_attempts=2,
            budget_guard=guard,
        )
        try:
            res = await client.generate(
                "gemma-4-31b-it",
                messages=[{"role": "user", "text": "audit SLA"}],
            )
            assert "billing notice required" in res.text
            assert not guard.is_billing_aborted()
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(_exercise())


def test_spy_decision1_driver_budget_and_402() -> None:
    class _FakeDecision1Inner:
        driver_id = "decision1-test"
        calibration_profile = CalibrationProfile(driver_id="decision1-test")
        capabilities = DriverCapabilities(
            is_cloud=True,
            supports_parallel_heads=True,
            max_candidates_per_pass=100,
        )

        def __init__(self, *, fail_402: bool = False) -> None:
            self.fail_402 = fail_402
            self.last_tokens_used = 1500
            self.last_cost_usd = 0.00015
            self.calls = 0

        async def score_raw(self, _req: ClassifierRequest) -> dict[str, float]:
            self.calls += 1
            if self.fail_402:
                raise RuntimeError("HTTP 402 Payment Required from Decision1")
            return {"h1": 0.25}

        async def classify(self, _req: ClassifierRequest) -> ClassifierResponse:
            self.calls += 1
            if self.fail_402:
                raise RuntimeError("HTTP 402 Payment Required from Decision1")
            return ClassifierResponse(
                driver_id="decision1-test",
                head_ids=["h1"],
                raw_scores={"h1": 0.25},
                calibrated_probabilities={"h1": 0.8},
                tokens_used=2000,
                estimated_cost_usd=0.0002,
                driver_latency_ms=12.0,
            )

        async def health_check(self) -> bool:
            return True

        async def close(self) -> None:
            return None

    async def _exercise() -> None:
        guard = LiveBudgetGuard(max_spend_usd=0.005)
        inner_ok = _FakeDecision1Inner()
        spy = SpyDecision1Driver(inner_ok, budget_guard=guard)  # type: ignore[arg-type]
        req = ClassifierRequest(
            query_text="fix the function",
            heads=[ClassifierHead(head_id="h1", candidate_text="def solve(): return 1")],
            cloud_egress_allowed=True,
        )
        await spy.classify(req)
        await spy.score_raw(req)
        assert abs(guard.snapshot()["spent_usd"] - 0.00035) < 1e-12
        assert guard.snapshot()["reserved_usd"] == 0.0

        inner_402 = _FakeDecision1Inner(fail_402=True)
        spy_402 = SpyDecision1Driver(inner_402, budget_guard=guard)  # type: ignore[arg-type]
        with pytest.raises(BillingDepletedError):
            await spy_402.classify(req)
        assert guard.is_billing_aborted()

    asyncio.run(_exercise())


def test_stratified_ek_interleave_and_gemma_cap() -> None:
    ek_tasks = [
        {"task_id": f"KNOWLEDGE_{i:02d}", "cartridge": cart}
        for cart in ("knowledge_vault", "financial_ops", "legal_redline")
        for i in range(1, 6)
    ]
    selected_12 = _select_stratified_ek_tasks(ek_tasks, 12)
    assert len(selected_12) == 12
    counts: dict[str, int] = {}
    for t in selected_12:
        counts[t["cartridge"]] = counts.get(t["cartridge"], 0) + 1
    assert counts == {"knowledge_vault": 4, "financial_ops": 4, "legal_redline": 4}
    assert {t["cartridge"] for t in selected_12[:3]} == {
        "knowledge_vault",
        "financial_ops",
        "legal_redline",
    }

    loaded = {
        "swe_agent": [{"task_id": f"SWE_{i}"} for i in range(8)],
        "enterprise_knowledge": selected_12,
        "bigcodebench": [{"task_id": f"BCB_{i}"} for i in range(14)],
        "humaneval_plus": [{"task_id": f"HE_{i}"} for i in range(20)],
        "gsm8k": [{"task_id": f"GSM_{i}"} for i in range(25)],
    }
    interleaved = _interleave_suite_tasks(loaded)
    assert len(interleaved) == 79
    first_10_suites = {s for s, _, _ in interleaved[:10]}
    assert first_10_suites == {"swe_agent", "enterprise_knowledge", "bigcodebench", "humaneval_plus", "gsm8k"}

    jobs = _build_pending_jobs(
        loaded,
        models=["gemma-4-31b-it", "gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-4-carbon"],
        include_ecc=True,
        completed=set(),
        interleave=True,
        gemma_max_per_suite=3,
    )
    gemma_jobs = [j for j in jobs if j[2] == "gemma-4-31b-it"]
    assert len(gemma_jobs) == 30
    non_gemma_jobs = [j for j in jobs if j[2] != "gemma-4-31b-it"]
    assert len(non_gemma_jobs) == 553
    assert len(jobs) == 583


def test_estimate_worst_case_covers_max_output_tokens() -> None:
    est = estimate_worst_case_call_usd(
        "gemini-4-carbon",
        system_prompt="sys",
        messages=[{"role": "user", "text": "hello world"}],
        max_output_tokens=2048,
    )
    assert est > 0.04224


def test_harness_budget_stop_preserves_existing_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "results.jsonl"
    existing_rec = {
        "suite": "long_context_kv",
        "task_id": "KV_01",
        "task_sha256": "abc",
        "category": "kv",
        "model": "gemini-4-carbon",
        "mode": "baseline",
        "passed": True,
        "uncached_input_tokens": 100,
        "cached_input_tokens": 0,
        "candidates_output_tokens": 10,
        "thoughts_output_tokens": 0,
        "generative_cost_usd": 0.001,
        "classifier_cost_usd": 0.0,
        "classifier_tokens": 0,
        "classifier_calls": 0,
        "retry_tax_usd": 0.0,
        "total_cost_usd": 0.001,
        "wall_clock_ms": 100.0,
        "gate_overhead_ms": 0.0,
        "turns": 1,
        "promoted": False,
        "routed_model": None,
        "detail": {},
    }
    existing_rec["receipt_sha256"] = compute_receipt_sha256(existing_rec)
    out_file.write_text(json.dumps(existing_rec) + "\n", encoding="utf-8")

    monkeypatch.setenv("DECISION1_API_KEY", "test-key")
    monkeypatch.setattr(
        "benchmarks.real_suite.harness.load_gsm8k_tasks",
        lambda limit=25: [
            {"task_id": f"GSM_{i}", "question": f"What is {i}+{i}?", "gold_value": i + i}
            for i in range(1, limit + 1)
        ],
    )
    monkeypatch.setattr(
        "benchmarks.real_suite.harness.build_spy_decision1_driver",
        lambda budget_guard=None: SimpleNamespace(
            close=lambda: asyncio.sleep(0),
            snapshot=lambda: {
                "calls": 0,
                "tokens_used": 0,
                "cost_usd": 0.0,
                "driver_latency_ms": 0.0,
                "wall_ms": 0.0,
            },
        ),
    )

    calls_seen = 0

    async def _fake_eval_gsm(
        client: LiveLLMClient,
        _driver: Any,
        task: dict[str, Any],
        model: str,
        mode: str,
        is_ecc_router: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal calls_seen
        assert client.budget_guard is not None
        res_id = client.budget_guard.try_reserve(0.05)
        client.budget_guard.settle(res_id, 0.04)
        calls_seen += 1
        rec = {
            "suite": "gsm8k",
            "task_id": task["task_id"],
            "task_sha256": "h",
            "category": "math_reasoning",
            "model": model,
            "mode": mode,
            "passed": True,
            "uncached_input_tokens": 100,
            "cached_input_tokens": 0,
            "candidates_output_tokens": 20,
            "thoughts_output_tokens": 0,
            "generative_cost_usd": 0.04,
            "classifier_cost_usd": 0.0,
            "classifier_tokens": 0,
            "classifier_calls": 0,
            "retry_tax_usd": 0.0,
            "total_cost_usd": 0.04,
            "wall_clock_ms": 50.0,
            "gate_overhead_ms": 0.0,
            "turns": 1,
            "promoted": False,
            "routed_model": None,
            "detail": {},
        }
        rec["receipt_sha256"] = compute_receipt_sha256(rec)
        return rec

    monkeypatch.setattr("benchmarks.real_suite.harness.eval_gsm8k_task", _fake_eval_gsm)

    snap = asyncio.run(
        run_benchmarks(
            suites=["gsm8k"],
            models=["gemini-4-carbon"],
            include_ecc=False,
            output_path=out_file,
            concurrency=2,
            gsm_limit=5,
            max_spend_usd=0.10,
        )
    )
    assert snap["spent_usd"] <= 0.10 + 1e-12
    assert calls_seen == 2
    lines = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0] == existing_rec
    for row in lines:
        assert row["receipt_sha256"] == compute_receipt_sha256(row)


def test_finish_reason_populated_in_llm_call_result() -> None:
    res_default = LLMCallResult("model", "text")
    assert res_default.finish_reason == ""

    async def _handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "hello"}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "cachedContentTokenCount": 0,
                    "candidatesTokenCount": 10,
                    "thoughtsTokenCount": 0,
                },
            }
        )

    async def _exercise() -> None:
        runner, base_url = await _start_mock_server(_handler)
        client = LiveLLMClient(api_key="test-key", base_url=base_url)
        try:
            res = await client.generate(
                "gemini-3.8-flash",
                messages=[{"role": "user", "text": "hi"}],
            )
            assert res.finish_reason == "STOP"
            assert res.text == "hello"
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(_exercise())


def test_empty_response_mime_type_retry_billing_accuracy() -> None:
    attempts: list[dict[str, Any]] = []

    async def _handler(request: web.Request) -> web.Response:
        payload = await request.json()
        attempts.append(payload)
        gen_cfg = payload.get("generationConfig", {})
        if len(attempts) == 1:
            assert gen_cfg.get("responseMimeType") == "application/json"
            return web.json_response(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [{"thought": True, "text": "Thinking hard..."}]
                            },
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1000,
                        "cachedContentTokenCount": 0,
                        "candidatesTokenCount": 0,
                        "thoughtsTokenCount": 400,
                    },
                }
            )
        assert "responseMimeType" not in gen_cfg
        return web.json_response(
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": '{"result": 42}'}]
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1000,
                    "cachedContentTokenCount": 0,
                    "candidatesTokenCount": 50,
                    "thoughtsTokenCount": 0,
                },
            }
        )

    async def _exercise() -> None:
        runner, base_url = await _start_mock_server(_handler)
        guard = LiveBudgetGuard(max_spend_usd=1.00)
        client = LiveLLMClient(
            api_key="test-key",
            base_url=base_url,
            budget_guard=guard,
            max_attempts=3,
        )
        try:
            res = await client.generate(
                "gemini-3.8-flash",
                messages=[{"role": "user", "text": "compute"}],
                json_mode=True,
            )
            assert res.text == '{"result": 42}'
            assert res.finish_reason == "STOP"
            assert len(attempts) == 2

            cost1 = compute_google_call_cost_usd("gemini-3.8-flash", 1000, 0, 400)
            cost2 = compute_google_call_cost_usd("gemini-3.8-flash", 1000, 0, 50)
            expected_total = cost1 + cost2

            snap = guard.snapshot()
            assert snap["calls_committed"] == 2
            assert snap["reserved_usd"] == 0.0
            assert abs(snap["spent_usd"] - expected_total) < 1e-12
        finally:
            await client.close()
            await runner.cleanup()

    asyncio.run(_exercise())


def test_build_pending_jobs_filtering_modes_and_ecc_only() -> None:
    loaded = {
        "swe_agent": [{"task_id": "SWE_1"}, {"task_id": "SWE_2"}],
        "humaneval_plus": [{"task_id": "HE_1"}, {"task_id": "HE_2"}],
        "long_context_kv": [{"task_id": "KV_1"}, {"task_id": "KV_2"}],
    }

    # 1. modes=["quadgate"] only
    qg_jobs = _build_pending_jobs(
        loaded,
        models=["gemini-4-carbon"],
        include_ecc=False,
        completed=set(),
        interleave=False,
        gemma_max_per_suite=None,
        modes=["quadgate"],
    )
    assert len(qg_jobs) == 6
    assert all(j[3] == "quadgate" for j in qg_jobs)

    # 2. modes=["baseline"] only
    base_jobs = _build_pending_jobs(
        loaded,
        models=["gemini-4-carbon"],
        include_ecc=True,
        completed=set(),
        interleave=False,
        gemma_max_per_suite=None,
        modes=["baseline"],
    )
    assert len(base_jobs) == 6
    assert all(j[3] == "baseline" for j in base_jobs)
    assert all(j[2] != ECC_ROUTER_MODEL_ID for j in base_jobs)

    # 3. ecc_only=True
    ecc_jobs = _build_pending_jobs(
        loaded,
        models=["gemini-4-carbon", "gemini-3.8-flash"],
        include_ecc=False,
        completed=set(),
        interleave=False,
        gemma_max_per_suite=None,
        ecc_only=True,
    )
    # SWE (2) + HE (2) = 4 jobs. KV is not in ecc_suites.
    assert len(ecc_jobs) == 4
    assert all(j[2] == ECC_ROUTER_MODEL_ID for j in ecc_jobs)
    assert all(j[3] == "quadgate" for j in ecc_jobs)
    assert all(j[4] is True for j in ecc_jobs)


def test_swe_agent_turn1_preloaded_files_and_turn_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_messages: list[list[dict[str, Any]]] = []

    class _MockClient:
        budget_guard = None

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
            captured_messages.append([dict(m) for m in messages])
            turn_no = len(captured_messages)
            if turn_no == 1:
                return LLMCallResult(
                    model=model,
                    text=json.dumps({"tool": "write_file", "path": "pkg/calc.py", "content": "def calc(): return 100\n"}),
                    finish_reason="STOP",
                    uncached_input_tokens=200,
                    candidates_output_tokens=30,
                    cost_usd=0.001,
                    latency_ms=100.0,
                )
            return LLMCallResult(
                model=model,
                text=json.dumps({"tool": "finish", "summary": "done"}),
                finish_reason="STOP",
                uncached_input_tokens=250,
                candidates_output_tokens=15,
                cost_usd=0.0005,
                latency_ms=80.0,
            )

    task = {
        "task_id": "SWE_TEST_01",
        "task_sha256": "swe_sha_123",
        "category": "bugfix",
        "prompt": "Fix bug in calc",
        "visible_files": {
            "pkg/calc.py": "def calc(): return 0\n",
            "pkg/util.py": "def util(): return 'ok'\n",
            ".env": "SECRET_KEY=leak\n",
        },
        "hidden_test_files": {
            "test_calc.py": "from pkg.calc import calc\ndef test_calc(): assert calc() == 100\n",
        },
        "expected_touched_files": ["pkg/calc.py"],
    }

    class _MockVerdict:
        def __init__(self, hints: list[str]) -> None:
            self.tail_hints = hints
            self.action = None
            self.reason = ""
            self.unaddressed_clauses = ""

    class _MockEngine:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def _is_private_hint_path(self, rel: str, workspace_root: str) -> bool:
            return rel.startswith(".") or "secret" in rel

        async def evaluate_gate1_intake(self, _event: Any) -> Any:
            return _MockVerdict(["pkg/calc.py", "pkg/util.py", ".env"])

        async def evaluate_gate2_firewall(self, _event: Any) -> Any:
            v = _MockVerdict([])
            from quadgate.contracts import VerdictAction
            v.action = VerdictAction.ALLOW
            return v

        async def evaluate_gate4_stop(self, _event: Any) -> Any:
            v = _MockVerdict([])
            from quadgate.contracts import VerdictAction
            v.action = VerdictAction.ALLOW
            return v

    async def _mock_verify(*args: Any, **kwargs: Any) -> tuple[bool, str]:
        return True, "PASSED"

    monkeypatch.setattr(
        "benchmarks.real_suite.harness._build_engine",
        lambda *args, **kwargs: (_MockEngine(), None, None, None),
    )
    monkeypatch.setattr(
        "benchmarks.real_suite.harness.verify_coding_ground_truth",
        _mock_verify,
    )

    driver = SimpleNamespace(
        reset_counters=lambda: None,
        snapshot=lambda: {"calls": 0, "tokens_used": 0, "cost_usd": 0.0},
    )

    rec = asyncio.run(
        eval_swe_agent_task(
            _MockClient(),  # type: ignore[arg-type]
            driver,  # type: ignore[arg-type]
            task,
            model="gemini-3.8-flash",
            mode="quadgate",
        )
    )

    # 1. Preloaded files check
    t1_text = captured_messages[0][0]["text"]
    assert "[QUADGATE_PRELOADED_FILES]" in t1_text
    assert "File: pkg/calc.py" in t1_text
    assert "def calc(): return 0" in t1_text
    assert "File: pkg/util.py" in t1_text
    assert "File: .env" not in t1_text
    assert "SECRET_KEY" not in t1_text
    preloaded_section = t1_text.split("[QUADGATE_PRELOADED_FILES]")[1]
    assert ".env" not in preloaded_section
    assert "You have at most 6 turns total; patch the hinted file(s) directly with write_file and call finish." in t1_text

    # 2. turn_trace check
    trace = rec["detail"]["turn_trace"]
    assert len(trace) == 2
    assert trace[0] == {
        "t": 1,
        "tool": "write_file",
        "finish": "STOP",
        "cand": 30,
        "len": len(json.dumps({"tool": "write_file", "path": "pkg/calc.py", "content": "def calc(): return 100\n"})),
        "blocked": False,
    }
    assert trace[1] == {
        "t": 2,
        "tool": "finish",
        "finish": "STOP",
        "cand": 15,
        "len": len(json.dumps({"tool": "finish", "summary": "done"})),
        "blocked": False,
    }
