"""Unit tests for the real live benchmark harness, pricing math, and receipt integrity."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.real_suite.datasets import (
    load_agentic_security_tasks,
    load_enterprise_knowledge_tasks,
    load_long_context_kv_tasks,
    load_swe_agent_tasks,
)
from benchmarks.real_suite.harness import (
    _is_tool_call_unsafe,
    _make_receipt,
    compute_receipt_sha256,
)
from benchmarks.real_suite.providers import (
    LLMCallResult,
    compute_google_call_cost_usd,
    extract_json_object,
    extract_python_code,
)
from benchmarks.real_suite.stats_and_report import analyze_results


def test_google_pricing_bills_cached_and_thoughts_accurately() -> None:
    cost_carbon = compute_google_call_cost_usd("gemini-4-carbon", 2000, 8000, 2000)
    expected_carbon = (2000 * 2.00 + 8000 * 0.10 + 2000 * 10.00) / 1_000_000.0
    assert abs(cost_carbon - expected_carbon) < 1e-12
    cost_pro = compute_google_call_cost_usd("gemini-3.1-pro-preview", 2000, 8000, 2000)
    expected_pro = (2000 * 2.00 + 8000 * 0.20 + 2000 * 12.00) / 1_000_000.0
    assert abs(cost_pro - expected_pro) < 1e-12
    assert compute_google_call_cost_usd("gemma-4-31b-it", 5000, 0, 1000) == 0.0


def test_extractors_handle_fences_and_gemma_prose() -> None:
    raw = (
        "Let me analyze carefully.\n"
        '{"tool": "write_file", "path": "a.py", "content": "x = 1"}\n'
    )
    obj = extract_json_object(raw)
    assert obj["tool"] == "write_file"
    assert obj["path"] == "a.py"

    py_raw = "Here is the code:\n```python\ndef f(x: int) -> int:\n    return x + 1\n```\n"
    assert "def f(x: int) -> int:" in extract_python_code(py_raw)


def test_swe_agent_hides_pytest_files_from_visible_repo() -> None:
    swe = load_swe_agent_tasks(limit=3)
    assert len(swe) == 3
    for t in swe:
        assert all("test_" not in k for k in t["visible_files"])
        assert t["hidden_test_files"]
        assert len(t["visible_files"]) >= 18


def test_long_context_kv_sessions_and_security_suite_counts() -> None:
    kv = load_long_context_kv_tasks(limit=6)
    assert len(kv) == 6
    assert all(len(s["turns"]) == 5 for s in kv)
    assert all(len(s["files"]) >= 24 for s in kv)

    sec = load_agentic_security_tasks()
    attacks = [s for s in sec if s["is_attack"]]
    benign = [s for s in sec if not s["is_attack"]]
    assert len(attacks) == 16
    assert len(benign) == 8

    kn = load_enterprise_knowledge_tasks(limit=15)
    assert len(kn) == 15


def test_security_oracle_detects_unsafe_vs_benign(tmp_path: Path) -> None:
    sec = {s["task_id"]: s for s in load_agentic_security_tasks()}
    assert _is_tool_call_unsafe(
        "read_file", {"Path": ".env.local"}, sec["SEC_CRED_01"], tmp_path
    )
    assert _is_tool_call_unsafe(
        "write_to_file",
        {"TargetFile": "core/kernel_auth.py", "CodeContent": "ALLOW_ANON=True"},
        sec["SEC_CAGE_01"],
        tmp_path,
    )
    assert not _is_tool_call_unsafe(
        "write_to_file",
        {"TargetFile": ".env.example", "CodeContent": "CACHE_TTL_SECONDS=300\n"},
        sec["BENIGN_01"],
        tmp_path,
    )


def test_receipt_sha256_and_stats_aggregation(tmp_path: Path) -> None:
    task = {"task_id": "T1", "task_sha256": "abc123", "category": "bugfix"}
    c_base = LLMCallResult(
        model="gemini-3.8-flash",
        text="ok",
        uncached_input_tokens=4000,
        cached_input_tokens=0,
        candidates_output_tokens=200,
        thoughts_output_tokens=100,
        cost_usd=compute_google_call_cost_usd("gemini-3.8-flash", 4000, 0, 300),
        latency_ms=1200.0,
    )
    c_qg = LLMCallResult(
        model="gemini-3.8-flash",
        text="ok",
        uncached_input_tokens=1000,
        cached_input_tokens=3000,
        candidates_output_tokens=150,
        thoughts_output_tokens=50,
        cost_usd=compute_google_call_cost_usd("gemini-3.8-flash", 1000, 3000, 200),
        latency_ms=700.0,
    )
    r1 = _make_receipt(
        suite="swe_agent",
        task=task,
        model="gemini-3.8-flash",
        mode="baseline",
        passed=False,
        calls=[c_base],
        wall_clock_ms=1200.0,
    )
    r2 = _make_receipt(
        suite="swe_agent",
        task=task,
        model="gemini-3.8-flash",
        mode="quadgate",
        passed=True,
        calls=[c_qg],
        driver_snap={"calls": 2, "tokens_used": 300, "cost_usd": 0.00003},
        wall_clock_ms=900.0,
        gate_overhead_ms=190.0,
    )
    assert r1["receipt_sha256"] == compute_receipt_sha256(r1)
    assert r2["receipt_sha256"] == compute_receipt_sha256(r2)

    jsonl_p = tmp_path / "res.jsonl"
    jsonl_p.write_text(json.dumps(r1) + "\n" + json.dumps(r2) + "\n", encoding="utf-8")
    summary = analyze_results(jsonl_p)
    assert summary["total_receipts"] == 2
    assert summary["sha256_verified_receipts"] == 2
    delta = summary["paired_deltas"]["swe_agent"]["gemini-3.8-flash"]
    assert delta["pass_delta_pp"] == 100.0
    assert delta["qg_rescued"] == 1


def test_build_visuals_renders_banners_dashboard_and_inline_card(tmp_path: Path) -> None:
    from benchmarks.real_suite.build_visuals import (
        build_all_visuals,
        render_inline_card_html,
        render_standalone_dashboard_html,
    )

    task = {"task_id": "T1", "task_sha256": "abc123", "category": "bugfix"}
    r1 = _make_receipt(
        suite="swe_agent",
        task=task,
        model="gemini-4-carbon",
        mode="baseline",
        passed=False,
        calls=[LLMCallResult("gemini-4-carbon", "fail", 3000, 0, 200, 50, 0.008, 900.0)],
        wall_clock_ms=900.0,
    )
    r2 = _make_receipt(
        suite="swe_agent",
        task=task,
        model="gemini-4-carbon",
        mode="quadgate",
        passed=True,
        calls=[LLMCallResult("gemini-4-carbon", "ok", 1000, 2000, 150, 50, 0.004, 600.0)],
        driver_snap={"calls": 2, "tokens_used": 250, "cost_usd": 0.00003},
        wall_clock_ms=700.0,
        gate_overhead_ms=100.0,
    )
    res_p = tmp_path / "results.jsonl"
    res_p.write_text(json.dumps(r1) + "\n" + json.dumps(r2) + "\n", encoding="utf-8")
    summary = analyze_results(res_p)
    sum_p = tmp_path / "summary.json"
    sum_p.write_text(json.dumps(summary), encoding="utf-8")

    dash_p = tmp_path / "index.html"
    wide_p = tmp_path / "wide.svg"
    social_p = tmp_path / "social.svg"
    payload = build_all_visuals(
        summary_path=sum_p,
        results_path=res_p,
        dashboard_path=dash_p,
        wide_svg_path=wide_p,
        social_svg_path=social_p,
    )
    assert payload["total_receipts"] == 2
    assert "Frontier Mean" in dash_p.read_text(encoding="utf-8")
    assert "Gemini 4 Carbon" in wide_p.read_text(encoding="utf-8")
    assert "2 Live SHA-256" in social_p.read_text(encoding="utf-8")
    art_dash = render_standalone_dashboard_html(payload, antigravity_tokens=True)
    assert "var(--background)" in art_dash
    inline = render_inline_card_html(payload)
    assert "Frontier (" in inline
    assert ":root" not in inline


def test_wide_banner_renders_all_nine_model_cards_within_bounds(tmp_path: Path) -> None:
    from benchmarks.real_suite.build_visuals import build_all_visuals
    from benchmarks.real_suite.providers import BENCHMARK_MODELS
    from benchmarks.real_suite.stats_and_report import ECC_ROUTER_MODEL_ID

    task = {"task_id": "T1", "task_sha256": "abc123", "category": "bugfix"}
    rows = []
    for m in BENCHMARK_MODELS:
        for mode_lit, passed in (("baseline", False), ("quadgate", True)):
            rows.append(
                json.dumps(
                    _make_receipt(
                        suite="swe_agent",
                        task=task,
                        model=m,
                        mode=mode_lit,
                        passed=passed,
                        calls=[LLMCallResult(m, "ok", 1000, 0, 100, 0, 0.002, 500.0)],
                        wall_clock_ms=500.0,
                    )
                )
            )
    rows.append(
        json.dumps(
            _make_receipt(
                suite="swe_agent",
                task=task,
                model=ECC_ROUTER_MODEL_ID,
                mode="quadgate",
                passed=True,
                calls=[LLMCallResult("gemini-4-carbon", "ok", 1000, 0, 100, 0, 0.002, 500.0)],
                wall_clock_ms=500.0,
            )
        )
    )
    res_p = tmp_path / "r9.jsonl"
    res_p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    summary = analyze_results(res_p)
    sum_p = tmp_path / "s9.json"
    sum_p.write_text(json.dumps(summary), encoding="utf-8")
    wide_p = tmp_path / "wide9.svg"
    build_all_visuals(
        summary_path=sum_p,
        results_path=res_p,
        dashboard_path=tmp_path / "d9.html",
        wide_svg_path=wide_p,
        social_svg_path=tmp_path / "soc9.svg",
    )
    svg = wide_p.read_text(encoding="utf-8")
    assert "Gemini 4 Argon" in svg
    assert "Gemini 4 Neon" in svg
    assert "Gemini 4 Hydrogen" in svg
    assert "Gemini 3.1 Pro" in svg
    assert "y=\"721\"" in svg  # 9th card starts at 225 + 8*62 = 721, ends at 777 (< 815 footer)
