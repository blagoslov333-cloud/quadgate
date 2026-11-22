"""Generate interactive dashboard, compact inline embed card, and SVG benchmark banners."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
from typing import Any

from benchmarks.real_suite.stats_and_report import (
    DEFAULT_RESULTS_PATH,
    DEFAULT_SUMMARY_JSON,
    SUITE_LABELS,
)

DEFAULT_DASHBOARD_HTML = Path("benchmarks/dashboard/index.html")
DEFAULT_WIDE_SVG = Path("assets/benchmark-banner-wide.svg")
DEFAULT_SOCIAL_SVG = Path("assets/benchmark-banner-social.svg")


def _fmt_usd(val: float) -> str:
    if val == 0.0:
        return "$0.00"
    if val < 0.01:
        return f"${val:.4f}"
    return f"${val:.3f}"


def _build_chart_series_payload(summary: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pack compact normalized JSON payload consumed by the dashboard and inline embed."""
    suites = summary["suites"]
    models = summary["models"]
    by_sm = summary["by_suite_model"]
    deltas = summary["paired_deltas"]
    mtotals = summary["model_totals"]

    suite_rows: list[dict[str, Any]] = []
    for s in suites:
        label = SUITE_LABELS.get(s, s)
        per_model: dict[str, Any] = {}
        for m in models:
            entry = by_sm.get(s, {}).get(m)
            if not entry:
                continue
            b = entry.get("baseline")
            q = entry.get("quadgate")
            d = deltas.get(s, {}).get(m, {})
            b_metric = b["pass_at_1_pct"] if b else None
            q_metric = q["pass_at_1_pct"] if q else None
            b_cache = b.get("kv_turns", {}).get("turns_2_to_n_cache_hit_pct", b["warm_cache_hit_pct"]) if b else None
            q_cache = q.get("kv_turns", {}).get("turns_2_to_n_cache_hit_pct", q["warm_cache_hit_pct"]) if q else None
            per_model[m] = {
                "base_pass": b_metric,
                "qg_pass": q_metric,
                "delta_pp": d.get("pass_delta_pp"),
                "mcnemar_p": d.get("mcnemar_p"),
                "base_cost": b["total_cost_usd"] if b else None,
                "qg_cost": q["total_cost_usd"] if q else None,
                "cost_savings_pct": d.get("cost_savings_pct"),
                "base_p50_ms": b["p50_ms"] if b else None,
                "qg_p50_ms": q["p50_ms"] if q else None,
                "base_cache_pct": b_cache,
                "qg_cache_pct": q_cache,
                "n": (q or b)["n"],
                "security_base": b.get("security") if b else None,
                "security_qg": q.get("security") if q else None,
                "kv_turns_base": b.get("kv_turns") if b else None,
                "kv_turns_qg": q.get("kv_turns") if q else None,
            }
        suite_rows.append({"id": s, "label": label, "models": per_model})

    frontier_set = set(summary.get("frontier_models", []))
    model_cards: list[dict[str, Any]] = []
    for m in models:
        mt = mtotals[m]
        b = mt.get("baseline")
        q = mt.get("quadgate")
        model_cards.append(
            {
                "id": m,
                "name": mt["display_name"],
                "tier": mt["tier_label"],
                "is_frontier": m in frontier_set,
                "base_pass": b["pass_at_1_pct"] if b else None,
                "qg_pass": q["pass_at_1_pct"] if q else None,
                "base_cost": b["total_cost_usd"] if b else None,
                "qg_cost": q["total_cost_usd"] if q else None,
                "cls_cost": q["classifier_cost_usd"] if q else 0.0,
                "base_cache_pct": b["warm_cache_hit_pct"] if b else None,
                "qg_cache_pct": q["warm_cache_hit_pct"] if q else None,
                "base_p50_ms": b["p50_ms"] if b else None,
                "qg_p50_ms": q["p50_ms"] if q else None,
                "n": (q or b)["n"],
                "routed_counts": q.get("routed_model_counts", {}) if q else {},
            }
        )

    compact_receipts = [
        {
            "suite": r["suite"],
            "task_id": r["task_id"],
            "model": r["model"],
            "mode": r["mode"],
            "passed": r["passed"],
            "cost": round(float(r["total_cost_usd"]), 5),
            "cls_cost": round(float(r.get("classifier_cost_usd", 0.0)), 6),
            "ms": round(float(r["wall_clock_ms"]), 0),
            "u_in": int(r["uncached_input_tokens"]),
            "c_in": int(r["cached_input_tokens"]),
            "out": int(r["candidates_output_tokens"]) + int(r["thoughts_output_tokens"]),
            "turns": int(r["turns"]),
            "routed": r.get("routed_model", r["model"]),
            "sha": str(r["receipt_sha256"])[:12],
        }
        for r in receipts
    ]

    return {
        "generated_utc": summary["generated_utc"],
        "total_receipts": summary["total_receipts"],
        "verified_receipts": summary["sha256_verified_receipts"],
        "total_spend": summary["total_live_spend_usd"],
        "cls_spend": summary["total_classifier_spend_usd"],
        "suite_rows": suite_rows,
        "model_cards": model_cards,
        "frontier_models": summary.get("frontier_models", []),
        "frontier_summary": summary.get("frontier_summary", {}),
        "security_by_family": summary.get("security_by_family", {}),
        "ecc_comparisons": summary.get("ecc_comparisons", {}),
        "receipts": compact_receipts,
    }


def _compute_headline_kpis(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute aggregate headline figures strictly from real summary payload."""
    cards = [c for c in payload["model_cards"] if c["base_pass"] is not None and c["qg_pass"] is not None]
    avg_base_pass = sum(c["base_pass"] for c in cards) / len(cards) if cards else 0.0
    avg_qg_pass = sum(c["qg_pass"] for c in cards) / len(cards) if cards else 0.0

    fs = payload.get("frontier_summary") or {}
    f_b = fs.get("baseline") or {}
    f_q = fs.get("quadgate") or {}
    f_base_pass = float(f_b.get("pass_at_1_pct", avg_base_pass))
    f_qg_pass = float(f_q.get("pass_at_1_pct", avg_qg_pass))
    f_base_cost = float(f_b.get("total_cost_usd", 0.0))
    f_qg_cost = float(f_q.get("total_cost_usd", 0.0))
    frontier_cnt = len(fs.get("models") or payload.get("frontier_models") or [])

    sec_row = next((r for r in payload["suite_rows"] if r["id"] == "agentic_security"), None)
    sec_base_vals = []
    sec_qg_vals = []
    if sec_row:
        for md in sec_row["models"].values():
            if md.get("security_base"):
                sec_base_vals.append(md["security_base"]["attack_defense_pct"])
            if md.get("security_qg"):
                sec_qg_vals.append(md["security_qg"]["attack_defense_pct"])
    avg_sec_base = sum(sec_base_vals) / len(sec_base_vals) if sec_base_vals else 0.0
    avg_sec_qg = sum(sec_qg_vals) / len(sec_qg_vals) if sec_qg_vals else 0.0

    kv_row = next((r for r in payload["suite_rows"] if r["id"] == "long_context_kv"), None)
    kv_base_vals = []
    kv_qg_vals = []
    if kv_row:
        for m_id, md in kv_row["models"].items():
            if m_id == "gemma-4-31b-it":
                continue
            if md.get("base_cache_pct") is not None:
                kv_base_vals.append(md["base_cache_pct"])
            if md.get("qg_cache_pct") is not None:
                kv_qg_vals.append(md["qg_cache_pct"])
    avg_kv_base = sum(kv_base_vals) / len(kv_base_vals) if kv_base_vals else 0.0
    avg_kv_qg = sum(kv_qg_vals) / len(kv_qg_vals) if kv_qg_vals else 0.0

    ecc = payload.get("ecc_comparisons", {})
    ecc_vs_carbon = ecc.get("vs_gemini-4-carbon_baseline", {})
    has_ecc_pair = bool(ecc_vs_carbon and float(ecc_vs_carbon.get("n", 0)) > 0)
    f_cost_drop = ((f_base_cost - f_qg_cost) / f_base_cost * 100.0) if f_base_cost > 0 else 0.0
    return {
        "model_count": len(cards),
        "frontier_count": frontier_cnt,
        "avg_base_pass": round(avg_base_pass, 1),
        "avg_qg_pass": round(avg_qg_pass, 1),
        "avg_pass_delta": round(avg_qg_pass - avg_base_pass, 1),
        "frontier_base_pass": round(f_base_pass, 1),
        "frontier_qg_pass": round(f_qg_pass, 1),
        "frontier_pass_delta": round(f_qg_pass - f_base_pass, 1),
        "frontier_base_cost": round(f_base_cost, 4),
        "frontier_qg_cost": round(f_qg_cost, 4),
        "frontier_cost_drop_pct": round(f_cost_drop, 1),
        "avg_sec_base": round(avg_sec_base, 1),
        "avg_sec_qg": round(avg_sec_qg, 1),
        "avg_kv_base": round(avg_kv_base, 1),
        "avg_kv_qg": round(avg_kv_qg, 1),
        "has_ecc_pair": has_ecc_pair,
        "ecc_vs_carbon_cost_drop": round(float(ecc_vs_carbon.get("ecc_cost_savings_pct", 0.0)), 1),
        "ecc_vs_carbon_pass_delta": round(float(ecc_vs_carbon.get("ecc_pass_delta_pp", 0.0)), 1),
    }


def render_wide_banner_svg(payload: dict[str, Any]) -> str:
    """Render 1600x900 publication SVG banner showing real AI-video-style benchmark comparisons."""
    kpis = _compute_headline_kpis(payload)
    # Build grouped benchmark averages across the 4 live models (excluding ECC standalone column for Base vs QG bars)
    short_labels = {
        "swe_agent": "SWE-Agent",
        "humaneval_plus": "HumanEval+",
        "bigcodebench": "BigCodeBench",
        "long_context_kv": "Long-Ctx KV",
        "agentic_security": "Sec Defense",
        "gsm8k": "GSM8K Math",
        "enterprise_knowledge": "Enterprise",
    }
    bars: list[tuple[str, float, float, str]] = []
    for row in payload["suite_rows"]:
        sid = row["id"]
        b_vals = []
        q_vals = []
        for mid, md in row["models"].items():
            if mid == "quadgate-ecc-router":
                continue
            if sid == "long_context_kv":
                if mid != "gemma-4-31b-it" and md.get("base_cache_pct") is not None and md.get("qg_cache_pct") is not None:
                    b_vals.append(md["base_cache_pct"])
                    q_vals.append(md["qg_cache_pct"])
            elif sid == "agentic_security":
                if md.get("security_base") and md.get("security_qg"):
                    b_vals.append(md["security_base"]["attack_defense_pct"])
                    q_vals.append(md["security_qg"]["attack_defense_pct"])
            else:
                if md.get("base_pass") is not None and md.get("qg_pass") is not None:
                    b_vals.append(md["base_pass"])
                    q_vals.append(md["qg_pass"])
        if b_vals and q_vals:
            b_avg = sum(b_vals) / len(b_vals)
            q_avg = sum(q_vals) / len(q_vals)
            metric_tag = "KV Hit%" if sid == "long_context_kv" else ("Block%" if sid == "agentic_security" else "Pass@1")
            bars.append((short_labels.get(sid, sid), b_avg, q_avg, metric_tag))

    # Chart geometry: x=70..1020, y=230..730 (height 500 -> bottom at y=710, scale 0..100 -> 380px)
    chart_bottom = 700
    chart_height = 360
    bar_svg_parts: list[str] = []
    for pct in (0, 25, 50, 75, 100):
        gy = chart_bottom - (pct / 100.0) * chart_height
        bar_svg_parts.append(
            f'<line x1="90" y1="{gy:.1f}" x2="1010" y2="{gy:.1f}" stroke="#1E293B" stroke-width="1" stroke-dasharray="4 4"/>'
            f'<text x="78" y="{gy + 4:.1f}" text-anchor="end" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="13">{pct}%</text>'
        )

    slot_w = 900.0 / max(1, len(bars))
    for i, (lbl, b_val, q_val, m_tag) in enumerate(bars):
        cx = 105.0 + i * slot_w + slot_w / 2.0
        bw = 40.0
        gap = 8.0
        bh = max(4.0, (b_val / 100.0) * chart_height)
        qh = max(4.0, (q_val / 100.0) * chart_height)
        bx = cx - bw - gap / 2.0
        qx = cx + gap / 2.0
        by = chart_bottom - bh
        qy = chart_bottom - qh
        delta = q_val - b_val
        delta_txt = f"{delta:+.1f}pp"
        d_col = "#34D399" if delta >= 0 else "#FB7185"
        bar_svg_parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" fill="#475569"/>'
            f'<rect x="{qx:.1f}" y="{qy:.1f}" width="{bw:.1f}" height="{qh:.1f}" rx="4" fill="#10B981"/>'
            f'<text x="{bx + bw/2:.1f}" y="{by - 8:.1f}" text-anchor="middle" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="13" font-weight="600">{b_val:.1f}%</text>'
            f'<text x="{qx + bw/2:.1f}" y="{qy - 8:.1f}" text-anchor="middle" fill="#E2E8F0" font-family="ui-monospace,Consolas,monospace" font-size="13" font-weight="700">{q_val:.1f}%</text>'
            f'<text x="{cx:.1f}" y="{min(by, qy) - 28:.1f}" text-anchor="middle" fill="{d_col}" font-family="ui-monospace,Consolas,monospace" font-size="12" font-weight="700">{delta_txt}</text>'
            f'<text x="{cx:.1f}" y="{chart_bottom + 24}" text-anchor="middle" fill="#E2E8F0" font-family="-apple-system,Segoe UI,sans-serif" font-size="14" font-weight="600">{escape(lbl)}</text>'
            f'<text x="{cx:.1f}" y="{chart_bottom + 42}" text-anchor="middle" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="11">{escape(m_tag)}</text>'
        )

    # Right-hand model cards (up to 9 cards fit cleanly between y=225..785)
    card_svg_parts: list[str] = []
    cards = payload["model_cards"]
    compact = len(cards) > 5
    step_y = 62 if compact else 108
    card_h = 56 if compact else 92
    cy = 225
    for mc in cards:
        f_tag = " ★" if mc.get("is_frontier") else ""
        name = escape(mc["name"] + f_tag)
        if mc["base_pass"] is not None and mc["qg_pass"] is not None:
            delta = mc["qg_pass"] - mc["base_pass"]
            stat_line = f"{mc['base_pass']:.1f}% -> {mc['qg_pass']:.1f}% ({delta:+.1f}pp)"
            cost_line = f"{_fmt_usd(mc['base_cost'])} -> {_fmt_usd(mc['qg_cost'])}"
            accent = "#34D399" if delta >= 0 else "#F59E0B"
        elif mc["qg_pass"] is not None:
            stat_line = (
                f"Adaptive Pass@1: {mc['qg_pass']:.1f}%"
                if kpis["has_ecc_pair"]
                else f"ECC Run (n={mc['n']}): {mc['qg_pass']:.1f}%"
            )
            cost_line = (
                f"{_fmt_usd(mc['qg_cost'] or 0.0)} (vs Carbon {kpis['ecc_vs_carbon_cost_drop']:+.1f}%)"
                if kpis["has_ecc_pair"]
                else f"{_fmt_usd(mc['qg_cost'] or 0.0)} (402 paused)"
            )
            accent = "#38BDF8"
        else:
            b_val = f"{mc['base_pass']:.1f}%" if mc["base_pass"] is not None else "Pending"
            stat_line = f"Baseline Pass@1: {b_val} (+QG pending)"
            cost_line = f"{_fmt_usd(mc['base_cost'] or 0.0)}"
            accent = "#64748B"
        if compact:
            card_svg_parts.append(
                f'<rect x="1060" y="{cy}" width="470" height="{card_h}" rx="8" fill="#141923" stroke="#252E3E" stroke-width="1.2"/>'
                f'<rect x="1060" y="{cy}" width="4" height="{card_h}" rx="2" fill="{accent}"/>'
                f'<text x="1078" y="{cy + 21}" fill="#F1F5F9" font-family="-apple-system,Segoe UI,sans-serif" font-size="14" font-weight="700">{name}</text>'
                f'<text x="1515" y="{cy + 21}" text-anchor="end" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="11">{escape(mc["tier"])}</text>'
                f'<text x="1078" y="{cy + 43}" fill="{accent}" font-family="ui-monospace,Consolas,monospace" font-size="13" font-weight="700">{escape(stat_line)}</text>'
                f'<text x="1515" y="{cy + 43}" text-anchor="end" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="12">{escape(cost_line)}</text>'
            )
        else:
            card_svg_parts.append(
                f'<rect x="1060" y="{cy}" width="470" height="{card_h}" rx="10" fill="#141923" stroke="#252E3E" stroke-width="1.5"/>'
                f'<rect x="1060" y="{cy}" width="5" height="{card_h}" rx="2" fill="{accent}"/>'
                f'<text x="1084" y="{cy + 30}" fill="#F1F5F9" font-family="-apple-system,Segoe UI,sans-serif" font-size="17" font-weight="700">{name}</text>'
                f'<text x="1510" y="{cy + 30}" text-anchor="end" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="12">{escape(mc["tier"])}</text>'
                f'<text x="1084" y="{cy + 56}" fill="{accent}" font-family="ui-monospace,Consolas,monospace" font-size="15" font-weight="700">{escape(stat_line)}</text>'
                f'<text x="1084" y="{cy + 78}" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="13">{escape(cost_line)}</text>'
            )
        cy += step_y

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900" role="img" aria-label="QuadGate Live Multi-Model Benchmark Comparison Banner">
  <defs>
    <pattern id="bgGrid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#131924" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="1600" height="900" fill="#0B0E14"/>
  <rect width="1600" height="900" fill="url(#bgGrid)"/>

  <!-- Header -->
  <g transform="translate(70, 56)">
    <rect x="0" y="0" width="10" height="42" rx="3" fill="#38BDF8"/>
    <rect x="15" y="0" width="10" height="42" rx="3" fill="#0EA5E9"/>
    <rect x="30" y="0" width="10" height="42" rx="3" fill="#10B981"/>
    <rect x="45" y="0" width="10" height="42" rx="3" fill="#34D399"/>
  </g>
  <text x="145" y="88" fill="#F1F5F9" font-family="-apple-system,Segoe UI,sans-serif" font-size="38" font-weight="800" letter-spacing="-0.5">QuadGate Live Benchmarks: Standalone vs. +QuadGate</text>
  <text x="145" y="118" fill="#94A3B8" font-family="-apple-system,Segoe UI,sans-serif" font-size="18">Real API telemetry across {kpis["model_count"]} models ({kpis["frontier_count"]} Frontier ★, Flash/Lite, Gemma 4) &amp; Gate-3 ECC Router</text>

  <!-- Provenance Pill -->
  <rect x="1165" y="54" width="365" height="44" rx="22" fill="#0F2922" stroke="#10B981" stroke-width="1.5"/>
  <text x="1347" y="81" text-anchor="middle" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="14" font-weight="700">{payload["verified_receipts"]}/{payload["total_receipts"]} LIVE SHA-256 RECEIPTS</text>

  <!-- KPI Strip -->
  <rect x="70" y="146" width="235" height="58" rx="8" fill="#141923" stroke="#252E3E"/>
  <text x="86" y="168" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="11">OVERALL PASS@1 DELTA</text>
  <text x="86" y="192" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="20" font-weight="700">{kpis["avg_base_pass"]:.1f}% -> {kpis["avg_qg_pass"]:.1f}% ({kpis["avg_pass_delta"]:+.1f}pp)</text>

  <rect x="322" y="146" width="235" height="58" rx="8" fill="#141923" stroke="#252E3E"/>
  <text x="338" y="168" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="11">KV-CACHE HIT (TURNS 2..5)</text>
  <text x="338" y="192" fill="#38BDF8" font-family="ui-monospace,Consolas,monospace" font-size="20" font-weight="700">{kpis["avg_kv_base"]:.1f}% -> {kpis["avg_kv_qg"]:.1f}%</text>

  <rect x="574" y="146" width="235" height="58" rx="8" fill="#141923" stroke="#252E3E"/>
  <text x="590" y="168" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="11">ATTACK DEFENSE RATE</text>
  <text x="590" y="192" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="20" font-weight="700">{kpis["avg_sec_base"]:.1f}% -> {kpis["avg_sec_qg"]:.1f}%</text>

  <rect x="826" y="146" width="204" height="58" rx="8" fill="#141923" stroke="#252E3E"/>
  <text x="842" y="168" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="11">{"ECC VS CARBON SPEND" if kpis["has_ecc_pair"] else "FRONTIER SPEND SAVED"}</text>
  <text x="842" y="192" fill="#F59E0B" font-family="ui-monospace,Consolas,monospace" font-size="20" font-weight="700">{(f"{kpis['ecc_vs_carbon_cost_drop']:+.1f}% saved" if kpis["has_ecc_pair"] else f"-{kpis['frontier_cost_drop_pct']:.1f}% (${kpis['frontier_qg_cost']:.2f})")}</text>

  <!-- Main Grouped Bar Chart Frame -->
  <rect x="70" y="225" width="960" height="555" rx="12" fill="#141923" stroke="#252E3E" stroke-width="1.5"/>
  <text x="96" y="262" fill="#F1F5F9" font-family="-apple-system,Segoe UI,sans-serif" font-size="19" font-weight="700">Benchmark Suite Comparison ({kpis["model_count"]}-Model Mean: Model Alone vs. +QuadGate)</text>
  <rect x="735" y="246" width="14" height="14" rx="3" fill="#475569"/>
  <text x="756" y="258" fill="#94A3B8" font-family="-apple-system,Segoe UI,sans-serif" font-size="13">Model Alone</text>
  <rect x="865" y="246" width="14" height="14" rx="3" fill="#10B981"/>
  <text x="886" y="258" fill="#E2E8F0" font-family="-apple-system,Segoe UI,sans-serif" font-size="13" font-weight="600">+QuadGate</text>

  {"".join(bar_svg_parts)}

  <!-- Right Column: Per-Model Summary Cards -->
  {"".join(card_svg_parts)}

  <!-- Footer -->
  <line x1="70" y1="815" x2="1530" y2="815" stroke="#1E293B"/>
  <text x="70" y="848" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="13">Methodology: Zero synthetic traces. Real Google API usageMetadata (incl. thoughtsTokenCount) + live Decision1Driver ($0.10/1M) + hidden pytest/EvalPlus/BigCodeBench grading.</text>
  <text x="1530" y="848" text-anchor="end" fill="#64748B" font-family="ui-monospace,Consolas,monospace" font-size="13">UTC {escape(payload["generated_utc"])} | Live Spend: ${payload["total_spend"]:.2f}</text>
</svg>
'''


def render_social_banner_svg(payload: dict[str, Any]) -> str:
    """Render tight 1280x640 social/GitHub OpenGraph banner from real results."""
    kpis = _compute_headline_kpis(payload)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640" width="1280" height="640" role="img" aria-label="QuadGate Real Benchmark Social Card">
  <defs>
    <pattern id="sGrid" width="36" height="36" patternUnits="userSpaceOnUse">
      <path d="M 36 0 L 0 0 0 36" fill="none" stroke="#131924" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="1280" height="640" fill="#0B0E14"/>
  <rect width="1280" height="640" fill="url(#sGrid)"/>
  <rect x="56" y="52" width="1168" height="536" rx="18" fill="#141923" stroke="#252E3E" stroke-width="2"/>
  <rect x="56" y="52" width="1168" height="5" rx="2" fill="#10B981"/>

  <g transform="translate(104, 102)">
    <rect x="0" y="0" width="11" height="46" rx="3" fill="#38BDF8"/>
    <rect x="17" y="0" width="11" height="46" rx="3" fill="#0EA5E9"/>
    <rect x="34" y="0" width="11" height="46" rx="3" fill="#10B981"/>
    <rect x="51" y="0" width="11" height="46" rx="3" fill="#34D399"/>
  </g>
  <text x="190" y="140" fill="#F1F5F9" font-family="-apple-system,Segoe UI,sans-serif" font-size="48" font-weight="800">QuadGate Real Benchmarks</text>
  <text x="104" y="186" fill="#94A3B8" font-family="-apple-system,Segoe UI,sans-serif" font-size="22">Live A/B evaluation across {kpis["model_count"]} foundation models ({kpis["frontier_count"]} Frontier) + Gate-3 ECC adaptive routing</text>

  <!-- 3 Hero Metric Blocks -->
  <rect x="104" y="232" width="340" height="190" rx="12" fill="#0B0E14" stroke="#252E3E"/>
  <text x="130" y="272" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="14">MEAN PASS@1 GAIN</text>
  <text x="130" y="340" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="46" font-weight="800">{kpis["avg_pass_delta"]:+.1f} pp</text>
  <text x="130" y="388" fill="#E2E8F0" font-family="ui-monospace,Consolas,monospace" font-size="16">{kpis["avg_base_pass"]:.1f}% base -> {kpis["avg_qg_pass"]:.1f}% +QG</text>

  <rect x="470" y="232" width="340" height="190" rx="12" fill="#0B0E14" stroke="#252E3E"/>
  <text x="496" y="272" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="14">KV-CACHE HIT (TURNS 2..5)</text>
  <text x="496" y="340" fill="#38BDF8" font-family="ui-monospace,Consolas,monospace" font-size="46" font-weight="800">{kpis["avg_kv_qg"]:.1f}%</text>
  <text x="496" y="388" fill="#E2E8F0" font-family="ui-monospace,Consolas,monospace" font-size="16">vs {kpis["avg_kv_base"]:.1f}% un-gated baseline</text>

  <rect x="836" y="232" width="340" height="190" rx="12" fill="#0B0E14" stroke="#252E3E"/>
  <text x="862" y="272" fill="#94A3B8" font-family="ui-monospace,Consolas,monospace" font-size="14">ATTACK DEFENSE RATE</text>
  <text x="862" y="340" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="46" font-weight="800">{kpis["avg_sec_qg"]:.1f}%</text>
  <text x="862" y="388" fill="#E2E8F0" font-family="ui-monospace,Consolas,monospace" font-size="16">vs {kpis["avg_sec_base"]:.1f}% model alone</text>

  <rect x="104" y="458" width="1072" height="72" rx="10" fill="#0F2922" stroke="#10B981" stroke-width="1.5"/>
  <text x="640" y="502" text-anchor="middle" fill="#34D399" font-family="ui-monospace,Consolas,monospace" font-size="20" font-weight="700">{payload["verified_receipts"]} Live SHA-256 Receipts • {(f"ECC Router Saves {kpis['ecc_vs_carbon_cost_drop']:.1f}% vs Carbon" if kpis["has_ecc_pair"] else f"{kpis['frontier_count']} Frontier Models: -{kpis['frontier_cost_drop_pct']:.1f}% Spend (${kpis['frontier_base_cost']:.2f}->${kpis['frontier_qg_cost']:.2f})")} • Zero Mocked Traces</text>
</svg>
'''


def render_standalone_dashboard_html(
    payload: dict[str, Any],
    *,
    antigravity_tokens: bool = False,
    max_receipts_embed: int | None = None,
) -> str:
    """Render full-page interactive dashboard HTML (no raw arbitrary Tailwind hexes)."""
    kpis = _compute_headline_kpis(payload)
    embed_payload = (
        {**payload, "receipts": payload["receipts"][:max_receipts_embed]}
        if max_receipts_embed is not None
        else payload
    )
    data_json = json.dumps(embed_payload, separators=(",", ":"))

    # Use clean CSS variables on custom classes for standalone browser, or host tokens in side-pane artifact
    css_vars = (
        """
        .qg-page { background: var(--background); color: var(--foreground); }
        .qg-card { background: var(--card); border: 1px solid var(--border); }
        .qg-subcard { background: var(--content, var(--background)); border: 1px solid var(--card-border, var(--border)); }
        .qg-muted { color: var(--muted-foreground); }
        .qg-accent-qg { color: var(--success, #34D399); }
        .qg-accent-sky { color: var(--primary, #38BDF8); }
        .qg-accent-warn { color: var(--warning, #F59E0B); }
        .qg-btn { background: var(--card); color: var(--foreground); border: 1px solid var(--border); }
        .qg-btn-active { background: var(--primary); color: var(--primary-foreground); border-color: var(--primary); }
        """
        if antigravity_tokens
        else """
        body.qg-page { background: #0B0E14; color: #F1F5F9; }
        .qg-card { background: #141923; border: 1px solid #252E3E; }
        .qg-subcard { background: #0B0E14; border: 1px solid #1E293B; }
        .qg-muted { color: #94A3B8; }
        .qg-accent-qg { color: #34D399; }
        .qg-accent-sky { color: #38BDF8; }
        .qg-accent-warn { color: #F59E0B; }
        .qg-btn { background: #141923; color: #E2E8F0; border: 1px solid #252E3E; }
        .qg-btn-active { background: #38BDF8; color: #062030; border-color: #38BDF8; font-weight: 700; }
        """
    )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>QuadGate Live Multi-Model Benchmark Dashboard</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    {css_vars}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }}
    button:focus-visible, input:focus-visible, select:focus-visible {{ outline: 2px solid #38BDF8; outline-offset: 2px; }}
    .bar-fill {{ transition: width 220ms ease, height 220ms ease; }}
  </style>
</head>
<body class="qg-page antialiased min-h-screen p-4 md:p-8">
  <div class="max-w-7xl mx-auto space-y-6">
    <!-- Header -->
    <header class="qg-card rounded-xl p-5 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
      <div class="space-y-1">
        <div class="flex items-center gap-3">
          <div class="flex gap-1" aria-hidden="true">
            <span class="w-2 h-6 rounded-sm inline-block" style="background:#38BDF8"></span>
            <span class="w-2 h-6 rounded-sm inline-block" style="background:#0EA5E9"></span>
            <span class="w-2 h-6 rounded-sm inline-block" style="background:#10B981"></span>
            <span class="w-2 h-6 rounded-sm inline-block" style="background:#34D399"></span>
          </div>
          <h1 class="text-xl md:text-2xl font-bold tracking-tight">QuadGate Live Multi-Model Benchmark Observatory</h1>
        </div>
        <p class="text-sm qg-muted">Head-to-head A/B verification: identical models &amp; prompts with vs. without QuadGate 4-Gate Control Plane</p>
      </div>
      <div class="flex flex-wrap items-center gap-3 mono text-xs">
        <span class="qg-subcard px-3 py-1.5 rounded-full qg-accent-qg font-semibold">{payload["verified_receipts"]}/{payload["total_receipts"]} SHA-256 Receipts</span>
        <span class="qg-subcard px-3 py-1.5 rounded-full qg-muted">Spend: ${payload["total_spend"]:.4f} (D1: ${payload["cls_spend"]:.4f})</span>
        <span class="qg-subcard px-3 py-1.5 rounded-full qg-muted">UTC {escape(payload["generated_utc"])}</span>
      </div>
    </header>

    <!-- KPI Strip -->
    <section class="grid grid-cols-2 lg:grid-cols-5 gap-4" aria-label="Headline Metrics">
      <div class="qg-card rounded-xl p-4 space-y-1">
        <div class="text-xs mono qg-muted">{kpis["model_count"]}-MODEL PASS@1 IMPACT</div>
        <div class="text-2xl font-bold mono qg-accent-qg">{kpis["avg_pass_delta"]:+.1f} pp</div>
        <div class="text-xs mono qg-muted">{kpis["avg_base_pass"]:.1f}% Base vs {kpis["avg_qg_pass"]:.1f}% +QuadGate</div>
      </div>
      <div class="qg-card rounded-xl p-4 space-y-1">
        <div class="text-xs mono qg-muted">{kpis["frontier_count"]}-FRONTIER MEAN DELTA</div>
        <div class="text-2xl font-bold mono qg-accent-qg">{kpis["frontier_pass_delta"]:+.1f} pp</div>
        <div class="text-xs mono qg-muted">{kpis["frontier_base_pass"]:.1f}% → {kpis["frontier_qg_pass"]:.1f}% (${kpis["frontier_base_cost"]:.2f} → ${kpis["frontier_qg_cost"]:.2f})</div>
      </div>
      <div class="qg-card rounded-xl p-4 space-y-1">
        <div class="text-xs mono qg-muted">KV-CACHE HIT (TURNS 2..5)</div>
        <div class="text-2xl font-bold mono qg-accent-sky">{kpis["avg_kv_qg"]:.1f}%</div>
        <div class="text-xs mono qg-muted">vs {kpis["avg_kv_base"]:.1f}% un-gated baseline</div>
      </div>
      <div class="qg-card rounded-xl p-4 space-y-1">
        <div class="text-xs mono qg-muted">SECURITY DEFENSE RATE</div>
        <div class="text-2xl font-bold mono qg-accent-qg">{kpis["avg_sec_qg"]:.1f}%</div>
        <div class="text-xs mono qg-muted">vs {kpis["avg_sec_base"]:.1f}% un-gated models</div>
      </div>
      <div class="qg-card rounded-xl p-4 space-y-1 col-span-2 lg:col-span-1">
        <div class="text-xs mono qg-muted">{"GATE-3 ECC VS CARBON" if kpis["has_ecc_pair"] else "FRONTIER SPEND DROP"}</div>
        <div class="text-2xl font-bold mono qg-accent-warn">{(f"{kpis['ecc_vs_carbon_cost_drop']:+.1f}% cost" if kpis["has_ecc_pair"] else f"-{kpis['frontier_cost_drop_pct']:.1f}%")}</div>
        <div class="text-xs mono qg-muted">{(f"Quality delta: {kpis['ecc_vs_carbon_pass_delta']:+.1f} pp" if kpis["has_ecc_pair"] else f"${kpis['frontier_base_cost']:.2f} → ${kpis['frontier_qg_cost']:.2f}")}</div>
      </div>
    </section>

    <!-- Interactive Model & Metric Controls + Grouped Bar Chart -->
    <section class="qg-card rounded-xl p-5 space-y-4">
      <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 border-b pb-4" style="border-color: rgba(148,163,184,0.2)">
        <div>
          <h2 class="text-base font-bold">Benchmark Suite Comparison ("Without vs. +QuadGate")</h2>
          <p class="text-xs qg-muted">Toggle any model or metric to inspect exact real API measurements per benchmark suite.</p>
        </div>
        <div class="flex flex-wrap gap-2 items-center">
          <div id="modelTabs" class="flex flex-wrap gap-1.5" role="group" aria-label="Model selector"></div>
          <span class="qg-muted px-1">|</span>
          <div id="metricTabs" class="flex flex-wrap gap-1.5" role="group" aria-label="Metric selector"></div>
        </div>
      </div>
      <div id="groupedChart" class="space-y-3"></div>
    </section>

    <!-- Row 2: Pareto Scatter + Turn-by-Turn KV Cache Curve -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="qg-card rounded-xl p-5 space-y-3">
        <div>
          <h2 class="text-base font-bold">Quality vs. Spend Pareto Frontier</h2>
          <p class="text-xs qg-muted">Each vector connects a standalone model (hollow circle) to +QuadGate (solid) and the Adaptive ECC Router.</p>
        </div>
        <div id="paretoChart" class="w-full"></div>
      </div>
      <div class="qg-card rounded-xl p-5 space-y-3">
        <div>
          <h2 class="text-base font-bold">Live Google KV-Cache Hit Rate by Turn (13k-Token Repos)</h2>
          <p class="text-xs qg-muted">Measured directly from Google API `cachedContentTokenCount` across Turns 1 to 5.</p>
        </div>
        <div id="kvTurnChart" class="space-y-2.5"></div>
      </div>
    </section>

    <!-- Full Model x Suite Matrix -->
    <section class="qg-card rounded-xl p-5 space-y-3">
      <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h2 class="text-base font-bold">Full Multi-Model × Benchmark Suite Matrix</h2>
          <p class="text-xs qg-muted">Every cell shows Standalone → +QuadGate score &amp; delta across all tested models (★ = Frontier tier).</p>
        </div>
        <div class="flex gap-2">
          <button type="button" id="exportJsonBtn" class="qg-btn px-3 py-1.5 rounded text-xs mono cursor-pointer">Export JSON</button>
          <button type="button" id="exportCsvBtn" class="qg-btn px-3 py-1.5 rounded text-xs mono cursor-pointer">Export CSV</button>
        </div>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs mono border-collapse" id="matrixTable"></table>
      </div>
    </section>

    <!-- Security Breakdown & Raw Receipt Explorer -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="qg-card rounded-xl p-5 space-y-3">
        <h2 class="text-base font-bold">Agentic Security &amp; False Positives</h2>
        <p class="text-xs qg-muted">16 prompt-injection / secret-exfil / escape / 2B attacks + 8 benign controls.</p>
        <div id="securityCards" class="space-y-2.5"></div>
      </div>
      <div class="qg-card rounded-xl p-5 space-y-3 lg:col-span-2">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div>
            <h2 class="text-base font-bold">Verified Receipt Ledger (Sample &amp; Filter)</h2>
            <p class="text-xs qg-muted">Every row carries live token counts, classifier cost, and SHA-256 checksum.</p>
          </div>
          <div class="flex gap-2">
            <label class="sr-only" for="suiteFilter">Filter by suite</label>
            <select id="suiteFilter" class="qg-subcard rounded px-2.5 py-1.5 text-xs mono"></select>
            <label class="sr-only" for="modelFilter">Filter by model</label>
            <select id="modelFilter" class="qg-subcard rounded px-2.5 py-1.5 text-xs mono"></select>
          </div>
        </div>
        <div class="overflow-x-auto max-h-80 overflow-y-auto">
          <table class="w-full text-left text-xs mono border-collapse">
            <thead>
              <tr class="border-b qg-muted" style="border-color: rgba(148,163,184,0.2)">
                <th class="py-2 pr-3">Task</th>
                <th class="py-2 pr-3">Model</th>
                <th class="py-2 pr-3">Mode</th>
                <th class="py-2 pr-3">Pass</th>
                <th class="py-2 pr-3">In (Unc/Cch)</th>
                <th class="py-2 pr-3">Out+Th</th>
                <th class="py-2 pr-3">Cost</th>
                <th class="py-2 pr-3">Latency</th>
                <th class="py-2">SHA-256</th>
              </tr>
            </thead>
            <tbody id="receiptRows"></tbody>
          </table>
        </div>
      </div>
    </section>
  </div>

  <script>
    const DATA = {data_json};
    let activeModel = "ALL_AVG";
    let activeMetric = "pass";

    const modelOptions = [
      {{"id": "ALL_AVG", "name": `All Mean (${{DATA.model_cards.filter(c => c.base_pass !== null).length}})`}},
      {{"id": "ALL_FRONTIER", "name": `Frontier Mean (${{DATA.frontier_models.length}})`}},
    ].concat(
      DATA.model_cards.filter(c => c.base_pass !== null).map(c => ({{id: c.id, name: c.name + (c.is_frontier ? " ★" : "")}}))
    );
    const metricOptions = [
      {{id: "pass", name: "Accuracy / Pass@1 %"}},
      {{id: "cost", name: "Total Spend ($)"}},
      {{id: "cache", name: "Warm KV-Cache Hit %"}},
      {{id: "latency", name: "p50 Latency (ms)"}}
    ];

    function renderButtons() {{
      const mEl = document.getElementById("modelTabs");
      mEl.innerHTML = modelOptions.map(o =>
        `<button type="button" data-model="${{o.id}}" class="px-2.5 py-1 rounded text-xs cursor-pointer ${{activeModel === o.id ? 'qg-btn-active' : 'qg-btn'}}">${{o.name}}</button>`
      ).join("");
      mEl.querySelectorAll("button").forEach(b => b.onclick = () => {{ activeModel = b.dataset.model; renderButtons(); renderGroupedChart(); }});

      const kEl = document.getElementById("metricTabs");
      kEl.innerHTML = metricOptions.map(o =>
        `<button type="button" data-metric="${{o.id}}" class="px-2.5 py-1 rounded text-xs cursor-pointer ${{activeMetric === o.id ? 'qg-btn-active' : 'qg-btn'}}">${{o.name}}</button>`
      ).join("");
      kEl.querySelectorAll("button").forEach(b => b.onclick = () => {{ activeMetric = b.dataset.metric; renderButtons(); renderGroupedChart(); }});
    }}

    function extractPair(row) {{
      const frontierSet = new Set(DATA.frontier_models || []);
      const keys = activeModel === "ALL_AVG"
        ? Object.keys(row.models).filter(k => k !== "quadgate-ecc-router")
        : activeModel === "ALL_FRONTIER"
        ? Object.keys(row.models).filter(k => frontierSet.has(k))
        : [activeModel];
      const valsB = [], valsQ = [];
      for (const k of keys) {{
        const md = row.models[k];
        if (!md || md.base_pass === null || md.qg_pass === null) continue;
        if (activeMetric === "pass") {{
          if (row.id === "agentic_security" && md.security_base && md.security_qg) {{
            valsB.push(md.security_base.attack_defense_pct);
            valsQ.push(md.security_qg.attack_defense_pct);
          }} else {{
            valsB.push(md.base_pass);
            valsQ.push(md.qg_pass);
          }}
        }} else if (activeMetric === "cost") {{
          valsB.push(md.base_cost);
          valsQ.push(md.qg_cost);
        }} else if (activeMetric === "cache") {{
          if (row.id === "long_context_kv" && k === "gemma-4-31b-it" && (activeModel === "ALL_AVG" || activeModel === "ALL_FRONTIER")) continue;
          valsB.push(md.base_cache_pct);
          valsQ.push(md.qg_cache_pct);
        }} else {{
          valsB.push(md.base_p50_ms);
          valsQ.push(md.qg_p50_ms);
        }}
      }}
      if (!valsB.length) return null;
      const avg = arr => arr.reduce((a, b) => a + b, 0) / arr.length;
      const ecc = row.models["quadgate-ecc-router"];
      let eccVal = null;
      if (ecc) {{
        eccVal = activeMetric === "pass" ? ecc.qg_pass
               : activeMetric === "cost" ? ecc.qg_cost
               : activeMetric === "cache" ? ecc.qg_cache_pct
               : ecc.qg_p50_ms;
      }}
      return {{ base: avg(valsB), qg: avg(valsQ), ecc: eccVal }};
    }}

    function renderGroupedChart() {{
      const host = document.getElementById("groupedChart");
      const pairs = DATA.suite_rows.map(r => ({{ row: r, p: extractPair(r) }})).filter(x => x.p !== null);
      const maxVal = (activeMetric === "pass" || activeMetric === "cache")
        ? 100
        : Math.max(0.001, ...pairs.flatMap(x => [x.p.base, x.p.qg, x.p.ecc || 0])) * 1.12;

      const fmt = v => activeMetric === "cost" ? (v === 0 ? "$0.00" : `$${{v.toFixed(4)}}`)
                     : activeMetric === "latency" ? `${{Math.round(v)}} ms`
                     : `${{v.toFixed(1)}}%`;

      host.innerHTML = pairs.map(({{ row, p }}) => {{
        const bw = Math.min(100, (p.base / maxVal) * 100).toFixed(1);
        const qw = Math.min(100, (p.qg / maxVal) * 100).toFixed(1);
        const diff = p.qg - p.base;
        const lowerBetter = activeMetric === "cost" || activeMetric === "latency";
        const good = lowerBetter ? diff <= 0 : diff >= 0;
        const deltaStr = activeMetric === "cost"
          ? `${{p.base > 0 ? (((p.qg - p.base) / p.base) * 100).toFixed(1) : "0.0"}}%`
          : activeMetric === "latency"
          ? `${{diff >= 0 ? "+" : ""}}${{Math.round(diff)}} ms`
          : `${{diff >= 0 ? "+" : ""}}${{diff.toFixed(1)}} pp`;
        const eccLine = (p.ecc !== null && (activeModel === "ALL_AVG" || activeModel === "ALL_FRONTIER"))
          ? `<span class="ml-2 px-1.5 py-0.5 rounded text-xs qg-subcard qg-accent-warn">ECC: ${{fmt(p.ecc)}}</span>`
          : "";

        return `
          <div class="qg-subcard rounded-lg p-3 space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span class="font-semibold text-sm">${{row.label}}</span>
              <div class="mono flex items-center gap-2">
                <span class="${{good ? 'qg-accent-qg' : 'qg-muted'}} font-bold">${{deltaStr}}</span>
                ${{eccLine}}
              </div>
            </div>
            <div class="space-y-1.5 text-xs mono">
              <div class="flex items-center gap-2">
                <span class="w-24 qg-muted shrink-0">Model Alone</span>
                <div class="flex-1 h-4 rounded overflow-hidden" style="background: rgba(148,163,184,0.12)">
                  <div class="h-full rounded bar-fill" style="width:${{bw}}%; background:#64748B"></div>
                </div>
                <span class="w-20 text-right">${{fmt(p.base)}}</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="w-24 qg-accent-qg font-semibold shrink-0">+QuadGate</span>
                <div class="flex-1 h-4 rounded overflow-hidden" style="background: rgba(16,185,129,0.12)">
                  <div class="h-full rounded bar-fill" style="width:${{qw}}%; background:#10B981"></div>
                </div>
                <span class="w-20 text-right font-bold">${{fmt(p.qg)}}</span>
              </div>
            </div>
          </div>
        `;
      }}).join("");
    }}

    function renderPareto() {{
      const pts = DATA.model_cards.filter(c => (c.base_cost || c.qg_cost) !== null);
      const maxCost = Math.max(0.01, ...pts.flatMap(c => [c.base_cost || 0, c.qg_cost || 0])) * 1.15;
      const W = 520, H = 260, padL = 48, padB = 34, padT = 18, padR = 20;
      const sx = c => padL + (c / maxCost) * (W - padL - padR);
      const sy = p => H - padB - (p / 100.0) * (H - padT - padB);
      let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" class="w-full h-auto" role="img" aria-label="Pareto Cost vs Accuracy Scatter">`;
      for (const yv of [0, 25, 50, 75, 100]) {{
        const y = sy(yv);
        svg += `<line x1="${{padL}}" y1="${{y}}" x2="${{W - padR}}" y2="${{y}}" stroke="rgba(148,163,184,0.18)" stroke-dasharray="3 3"/>`;
        svg += `<text x="${{padL - 6}}" y="${{y + 4}}" text-anchor="end" fill="#94A3B8" font-size="10" class="mono">${{yv}}%</text>`;
      }}
      svg += `<text x="${{W/2}}" y="${{H - 4}}" text-anchor="middle" fill="#94A3B8" font-size="11" class="mono">Total Live Spend (USD)</text>`;
      const colors = ["#38BDF8", "#A78BFA", "#F59E0B", "#FB7185", "#34D399", "#F472B6", "#2DD4BF", "#FBBF24", "#60A5FA"];
      const placedLabels = [];
      const placeY = (lx, rawY) => {{
        let y = Math.max(padT + 10, Math.min(H - padB - 4, rawY));
        for (let step = 0; step < 8; step++) {{
          const hit = placedLabels.some(p => Math.abs(p.x - lx) < 88 && Math.abs(p.y - y) < 12);
          if (!hit) break;
          y = Math.max(padT + 10, Math.min(H - padB - 4, y + (step % 2 === 0 ? -12 : 12) * Math.ceil((step + 1) / 2)));
        }}
        placedLabels.push({{x: lx, y}});
        return y;
      }};
      pts.forEach((c, i) => {{
        const col = colors[i % colors.length];
        if (c.base_cost !== null && c.base_pass !== null && c.qg_cost !== null && c.qg_pass !== null) {{
          const x1 = sx(c.base_cost), y1 = sy(c.base_pass);
          const x2 = sx(c.qg_cost), y2 = sy(c.qg_pass);
          const lblX = x2 + 8 > W - padR - 65 ? x2 - 68 : x2 + 8;
          const lblY = placeY(lblX, y2 + 4);
          svg += `<line x1="${{x1}}" y1="${{y1}}" x2="${{x2}}" y2="${{y2}}" stroke="${{col}}" stroke-width="1.8" stroke-dasharray="4 2"/>`;
          svg += `<circle cx="${{x1}}" cy="${{y1}}" r="5" fill="none" stroke="${{col}}" stroke-width="2"><title>${{c.name}} Base: ${{c.base_pass}}% ($${{c.base_cost.toFixed(4)}})</title></circle>`;
          svg += `<circle cx="${{x2}}" cy="${{y2}}" r="6" fill="${{col}}"><title>${{c.name}} +QuadGate: ${{c.qg_pass}}% ($${{c.qg_cost.toFixed(4)}})</title></circle>`;
          svg += `<text x="${{lblX}}" y="${{lblY}}" fill="#E2E8F0" font-size="10" class="mono">${{c.name.replace("Gemini ", "G")}}</text>`;
        }} else if (c.qg_cost !== null && c.qg_pass !== null) {{
          const x = sx(c.qg_cost), y = sy(c.qg_pass);
          const lblX = x + 9 > W - padR - 65 ? x - 68 : x + 9;
          const lblY = placeY(lblX, y + 4);
          svg += `<polygon points="${{x}},${{y-7}} ${{x+7}},${{y}} ${{x}},${{y+7}} ${{x-7}},${{y}}" fill="#34D399"><title>${{c.name}}: ${{c.qg_pass}}% ($${{c.qg_cost.toFixed(4)}})</title></polygon>`;
          svg += `<text x="${{lblX}}" y="${{lblY}}" fill="#34D399" font-size="10" font-weight="bold" class="mono">ECC Router</text>`;
        }}
      }});
      svg += `</svg>`;
      document.getElementById("paretoChart").innerHTML = svg;
    }}

    function renderKvTurns() {{
      const kv = DATA.suite_rows.find(r => r.id === "long_context_kv");
      const el = document.getElementById("kvTurnChart");
      if (!kv) {{ el.innerHTML = "<p class='text-xs qg-muted'>No KV data.</p>"; return; }}
      const html = Object.entries(kv.models).filter(([m]) => m !== "gemma-4-31b-it").map(([m, md]) => {{
        const qTurns = (md.kv_turns_qg && md.kv_turns_qg.per_turn) || [];
        const bTurns = (md.kv_turns_base && md.kv_turns_base.per_turn) || [];
        const cols = qTurns.map((t, idx) => {{
          const bt = bTurns[idx] ? bTurns[idx].cache_hit_pct : 0;
          return `
            <div class="flex-1 qg-subcard rounded p-2 text-center mono text-xs">
              <div class="qg-muted">T${{t.turn}}</div>
              <div class="font-bold qg-accent-sky">${{t.cache_hit_pct.toFixed(0)}}%</div>
              <div class="text-xs qg-muted">base ${{bt.toFixed(0)}}%</div>
            </div>
          `;
        }}).join("");
        return `
          <div class="space-y-1">
            <div class="flex justify-between text-xs mono">
              <span class="font-semibold">${{m}}</span>
              <span class="qg-accent-qg">Turns 2..5 Hit: ${{md.qg_cache_pct}}% (vs ${{md.base_cache_pct}}%)</span>
            </div>
            <div class="flex gap-1.5">${{cols}}</div>
          </div>
        `;
      }}).join("");
      el.innerHTML = html;
    }}

    function renderMatrix() {{
      const tbl = document.getElementById("matrixTable");
      if (!tbl) return;
      const cards = DATA.model_cards;
      const head = `<thead><tr class="border-b qg-muted" style="border-color: rgba(148,163,184,0.2)">
        <th class="py-2 pr-3 sticky left-0">Suite</th>
        ${{cards.map(c => `<th class="py-2 px-2 text-right">${{c.name.replace("Gemini ","G").replace("QuadGate ","")}}${{c.is_frontier ? " ★" : ""}}</th>`).join("")}}
      </tr></thead>`;
      const body = DATA.suite_rows.map(row => {{
        const cells = cards.map(c => {{
          const md = row.models[c.id];
          if (!md) return `<td class="py-2 px-2 text-right qg-muted">—</td>`;
          if (c.id === "quadgate-ecc-router") {{
            return md.qg_pass !== null
              ? `<td class="py-2 px-2 text-right font-bold qg-accent-sky">${{md.qg_pass.toFixed(1)}}%</td>`
              : `<td class="py-2 px-2 text-right qg-muted">—</td>`;
          }}
          let b = md.base_pass, q = md.qg_pass;
          if (row.id === "long_context_kv") {{ b = md.base_cache_pct; q = md.qg_cache_pct; }}
          else if (row.id === "agentic_security" && md.security_base && md.security_qg) {{ b = md.security_base.attack_defense_pct; q = md.security_qg.attack_defense_pct; }}
          if (b === null || q === null || b === undefined || q === undefined) return `<td class="py-2 px-2 text-right qg-muted">—</td>`;
          const d = q - b;
          const cls = d > 0.05 ? "qg-accent-qg font-bold" : (d < -0.05 ? "qg-accent-warn" : "");
          return `<td class="py-2 px-2 text-right ${{cls}}">${{b.toFixed(0)}}→${{q.toFixed(0)}}%</td>`;
        }}).join("");
        return `<tr class="border-b" style="border-color: rgba(148,163,184,0.12)"><td class="py-2 pr-3 font-semibold">${{row.label}}</td>${{cells}}</tr>`;
      }}).join("");
      const totalCells = cards.map(c => {{
        if (c.id === "quadgate-ecc-router") {{
          return c.qg_pass !== null
            ? `<td class="py-2 px-2 text-right font-bold qg-accent-sky">${{c.qg_pass.toFixed(1)}}% ($${{(c.qg_cost || 0).toFixed(2)}})</td>`
            : `<td class="py-2 px-2 text-right qg-muted">—</td>`;
        }}
        if (c.base_pass === null || c.qg_pass === null) return `<td class="py-2 px-2 text-right qg-muted">—</td>`;
        const d = c.qg_pass - c.base_pass;
        return `<td class="py-2 px-2 text-right font-bold ${{d>=0?'qg-accent-qg':'qg-accent-warn'}}">${{c.base_pass.toFixed(1)}}→${{c.qg_pass.toFixed(1)}}% (${{d>=0?'+':''}}${{d.toFixed(1)}})</td>`;
      }}).join("");
      const foot = `<tfoot><tr class="font-bold"><td class="py-2 pr-3">All Suites Macro</td>${{totalCells}}</tr></tfoot>`;
      tbl.innerHTML = head + "<tbody>" + body + "</tbody>" + foot;
    }}

    function renderSecurity() {{
      const sec = DATA.suite_rows.find(r => r.id === "agentic_security");
      const el = document.getElementById("securityCards");
      if (!sec) return;
      const modelCards = Object.entries(sec.models).map(([m, md]) => {{
        const sb = md.security_base || {{}};
        const sq = md.security_qg || {{}};
        return `
          <div class="qg-subcard rounded-lg p-3 space-y-1 text-xs mono">
            <div class="flex justify-between font-bold">
              <span>${{m}}</span>
              <span class="qg-accent-qg">${{sb.attack_defense_pct ?? 0}}% -> ${{sq.attack_defense_pct ?? 0}}%</span>
            </div>
            <div class="qg-muted flex justify-between">
              <span>Gate2 blocked: ${{sq.quadgate_blocked_n ?? 0}} attacks</span>
              <span>Benign FPR: ${{sq.benign_fpr_pct ?? 0}}%</span>
            </div>
          </div>
        `;
      }}).join("");
      const famEntries = Object.entries(DATA.security_by_family || {{}});
      const famBlock = famEntries.length ? `<div class="pt-2 border-t text-xs mono space-y-1" style="border-color: rgba(148,163,184,0.16)"><div class="qg-muted font-semibold">By Attack Family (All Models)</div>` + famEntries.map(([fam, st]) => {{
        const bp = st.base_n ? (st.base_pass / st.base_n * 100).toFixed(0) : 0;
        const qp = st.qg_n ? (st.qg_pass / st.qg_n * 100).toFixed(0) : 0;
        return `<div class="flex justify-between"><span>${{fam}}</span><span class="qg-accent-qg">${{bp}}% -> ${{qp}}% (G2:${{st.gate2_blocked}})</span></div>`;
      }}).join("") + `</div>` : "";
      el.innerHTML = modelCards + famBlock;
    }}

    function wireExports() {{
      const dl = (name, text, mime) => {{
        const blob = new Blob([text], {{type: mime}});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      }};
      const jb = document.getElementById("exportJsonBtn");
      const cb = document.getElementById("exportCsvBtn");
      if (jb) jb.onclick = () => dl("quadgate_live_benchmark_data.json", JSON.stringify(DATA, null, 2), "application/json");
      if (cb) cb.onclick = () => {{
        const hdr = ["suite","task_id","model","mode","passed","u_in","c_in","out","cost","cls_cost","ms","routed","sha"];
        const lines = [hdr.join(",")].concat(DATA.receipts.map(r => hdr.map(k => JSON.stringify(r[k] ?? "")).join(",")));
        dl("quadgate_live_receipts.csv", lines.join("\\n"), "text/csv");
      }};
    }}

    function initReceiptsTable() {{
      const sf = document.getElementById("suiteFilter");
      const mf = document.getElementById("modelFilter");
      sf.innerHTML = `<option value="ALL">All Suites</option>` + DATA.suite_rows.map(r => `<option value="${{r.id}}">${{r.id}}</option>`).join("");
      mf.innerHTML = `<option value="ALL">All Models</option>` + DATA.model_cards.map(c => `<option value="${{c.id}}">${{c.id}}</option>`).join("");
      const draw = () => {{
        const sv = sf.value, mv = mf.value;
        const rows = DATA.receipts.filter(r => (sv === "ALL" || r.suite === sv) && (mv === "ALL" || r.model === mv)).slice(0, 120);
        document.getElementById("receiptRows").innerHTML = rows.map(r => `
          <tr class="border-b" style="border-color: rgba(148,163,184,0.12)">
            <td class="py-1.5 pr-3">${{r.task_id}}</td>
            <td class="py-1.5 pr-3">${{r.model}}</td>
            <td class="py-1.5 pr-3">${{r.mode}}</td>
            <td class="py-1.5 pr-3 font-bold ${{r.passed ? 'qg-accent-qg' : ''}}">${{r.passed ? 'PASS' : 'FAIL'}}</td>
            <td class="py-1.5 pr-3">${{r.u_in}}/${{r.c_in}}</td>
            <td class="py-1.5 pr-3">${{r.out}}</td>
            <td class="py-1.5 pr-3">$${{r.cost.toFixed(4)}}</td>
            <td class="py-1.5 pr-3">${{r.ms}}ms</td>
            <td class="py-1.5 qg-muted">${{r.sha}}</td>
          </tr>
        `).join("");
      }};
      sf.onchange = draw;
      mf.onchange = draw;
      draw();
    }}

    renderButtons();
    renderGroupedChart();
    renderPareto();
    renderKvTurns();
    renderMatrix();
    renderSecurity();
    initReceiptsTable();
    wireExports();
  </script>
</body>
</html>
'''


def render_inline_card_html(payload: dict[str, Any]) -> str:
    """Render compact <=420px flush inline widget for chat <agent-embed> using host CSS vars."""
    kpis = _compute_headline_kpis(payload)
    slim_suites = []
    for r in payload["suite_rows"]:
        sid = r["id"]
        slim_models = {}
        for mid, md in r["models"].items():
            if mid == "quadgate-ecc-router":
                continue
            if sid == "long_context_kv":
                b_v, q_v = md.get("base_cache_pct"), md.get("qg_cache_pct")
            elif sid == "agentic_security" and md.get("security_base") and md.get("security_qg"):
                b_v = md["security_base"].get("attack_defense_pct")
                q_v = md["security_qg"].get("attack_defense_pct")
            else:
                b_v, q_v = md.get("base_pass"), md.get("qg_pass")
            if b_v is not None and q_v is not None:
                slim_models[mid] = {"b": b_v, "q": q_v, "n": md.get("n", 0)}
        slim_suites.append({"id": sid, "models": slim_models})
    slim_cards = [
        {"id": c["id"], "name": c["name"], "ok": c["base_pass"] is not None and c["qg_pass"] is not None}
        for c in payload["model_cards"]
        if c["base_pass"] is not None and c["qg_pass"] is not None
    ]
    data_json = json.dumps(
        {
            "suite_rows": slim_suites,
            "model_cards": slim_cards,
            "frontier_models": payload.get("frontier_models", []),
        },
        separators=(",", ":"),
    )
    footer_right = (
        f"ECC vs Carbon: {kpis['ecc_vs_carbon_cost_drop']:+.1f}% spend"
        if kpis["has_ecc_pair"]
        else f"5 Frontier Spend: -{kpis['frontier_cost_drop_pct']:.1f}% (${kpis['frontier_base_cost']:.2f} → ${kpis['frontier_qg_cost']:.2f})"
    )
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
</head>
<body class="bg-transparent text-[var(--foreground)] antialiased p-0 m-0">
  <div class="py-2 space-y-2">
    <div class="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-[var(--card-border)] text-xs">
      <div class="flex flex-wrap items-center gap-3 font-mono tabular-nums">
        <span>All({kpis["model_count"]}): <strong class="text-[var(--success)]">{kpis["avg_base_pass"]:.1f}% → {kpis["avg_qg_pass"]:.1f}% ({kpis["avg_pass_delta"]:+.1f}pp)</strong></span>
        <span>Frontier({kpis["frontier_count"]}): <strong class="text-[var(--success)]">{kpis["frontier_base_pass"]:.1f}% → {kpis["frontier_qg_pass"]:.1f}%</strong></span>
        <span>KV T2+: <strong class="text-[var(--primary)]">{kpis["avg_kv_qg"]:.1f}%</strong></span>
        <span>Sec: <strong class="text-[var(--success)]">{kpis["avg_sec_qg"]:.1f}%</strong></span>
      </div>
      <div id="tabs" class="flex flex-wrap gap-1" role="group" aria-label="Model filter"></div>
    </div>
    <div id="bars" class="space-y-1.5"></div>
    <div class="flex items-center justify-between pt-1 border-t border-[var(--card-border)] text-xs text-[var(--muted-foreground)] font-mono">
      <span>{payload["verified_receipts"]}/{payload["total_receipts"]} Live SHA-256 Receipts</span>
      <span>{footer_right}</span>
    </div>
  </div>
  <script>
    const D = {data_json};
    let sel = "FRONTIER";
    const opts = [
      {{id:"FRONTIER", n:`Frontier (${{D.frontier_models.length}})`}},
      {{id:"ALL", n:`All (${{D.model_cards.length}})`}},
    ].concat(D.model_cards.map(c=>({{id:c.id, n:c.name.replace("Gemini ","").replace(" 31B IT","")}})));
    const short = {{swe_agent:"SWE-Agent", humaneval_plus:"HumanEval+", bigcodebench:"BigCodeBench", long_context_kv:"KV Cache T2+", agentic_security:"Security", gsm8k:"GSM8K", enterprise_knowledge:"Enterprise"}};
    function draw() {{
      const fSet = new Set(D.frontier_models || []);
      if (!fSet.size && sel === "FRONTIER") sel = "ALL";
      document.getElementById("tabs").innerHTML = opts.map(o =>
        `<button type="button" onclick="sel='${{o.id}}';draw()" class="px-1.5 py-0.5 rounded border border-[var(--card-border)] cursor-pointer ${{sel===o.id ? 'bg-[var(--primary)] text-[var(--primary-foreground)] font-semibold' : 'text-[var(--muted-foreground)]'}}">${{o.n}}</button>`
      ).join("");
      document.getElementById("bars").innerHTML = D.suite_rows.map(r => {{
        const ks = sel==="ALL" ? Object.keys(r.models)
                 : sel==="FRONTIER" ? Object.keys(r.models).filter(k=>fSet.has(k))
                 : [sel];
        const b=[], q=[], ns=[];
        ks.forEach(k => {{
          const m = r.models[k];
          if (!m) return;
          if (r.id==="long_context_kv" && sel==="ALL" && k==="gemma-4-31b-it") return;
          b.push(m.b); q.push(m.q); ns.push(m.n||0);
        }});
        if (!b.length) return "";
        const bv = b.reduce((a,c)=>a+c,0)/b.length, qv = q.reduce((a,c)=>a+c,0)/q.length, d = qv - bv;
        const maxN = Math.max(...ns);
        const lbl = (short[r.id]||r.id) + (maxN < 5 ? ` (n=${{maxN}})` : "");
        return `
          <div class="grid grid-cols-12 items-center gap-2 text-xs font-mono tabular-nums">
            <span class="col-span-3 truncate">${{lbl}}</span>
            <div class="col-span-6 space-y-0.5">
              <div class="h-1.5 rounded bg-[var(--card-border)] overflow-hidden"><div class="h-full bg-[var(--muted-foreground)]" style="width:${{bv.toFixed(1)}}%"></div></div>
              <div class="h-2 rounded bg-[var(--card-border)] overflow-hidden"><div class="h-full bg-[var(--chart-2,var(--success))]" style="width:${{qv.toFixed(1)}}%"></div></div>
            </div>
            <span class="col-span-3 text-right">${{bv.toFixed(0)}}%→<strong>${{qv.toFixed(0)}}%</strong> <span class="${{d>=0?'text-[var(--success)]':'text-[var(--muted-foreground)]'}}">(${{d>=0?'+':''}}${{d.toFixed(1)}})</span></span>
          </div>`;
      }}).join("");
    }}
    draw();
  </script>
</body>
</html>
'''


def build_all_visuals(
    summary_path: Path = DEFAULT_SUMMARY_JSON,
    results_path: Path = DEFAULT_RESULTS_PATH,
    dashboard_path: Path = DEFAULT_DASHBOARD_HTML,
    wide_svg_path: Path = DEFAULT_WIDE_SVG,
    social_svg_path: Path = DEFAULT_SOCIAL_SVG,
) -> dict[str, Any]:
    """Generate dashboard HTML and SVG banners from real benchmark outputs."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    receipts = [
        json.loads(ln)
        for ln in results_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    payload = _build_chart_series_payload(summary, receipts)

    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(
        render_standalone_dashboard_html(payload, antigravity_tokens=False),
        encoding="utf-8",
    )
    wide_svg_path.parent.mkdir(parents=True, exist_ok=True)
    wide_svg_path.write_text(render_wide_banner_svg(payload), encoding="utf-8")
    social_svg_path.write_text(render_social_banner_svg(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build QuadGate live benchmark dashboard and SVG banners.")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD_HTML)
    parser.add_argument("--wide-svg", type=Path, default=DEFAULT_WIDE_SVG)
    parser.add_argument("--social-svg", type=Path, default=DEFAULT_SOCIAL_SVG)
    args = parser.parse_args()
    payload = build_all_visuals(
        summary_path=args.summary,
        results_path=args.results,
        dashboard_path=args.dashboard,
        wide_svg_path=args.wide_svg,
        social_svg_path=args.social_svg,
    )
    print(f"Built visuals from {payload['total_receipts']} receipts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
