"""Comprehensive unit and sandbox integration tests for `benchmarks.runner`."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError
import pytest

from benchmarks.runner import (
    MODEL_PRICING_PER_1M,
    BenchmarkRunner,
    TaskBenchmarkReceipt,
    compute_receipt_sha256,
    compute_task_sha256,
    compute_turn_cost_usd,
    extract_provider_usage,
    verify_coding_ground_truth,
    verify_knowledge_ground_truth,
    verify_receipt_sha256,
)


def test_task_benchmark_receipt_contract_and_hashes() -> None:
    receipt = TaskBenchmarkReceipt(
        task_id="CODING_01",
        task_sha256="abc123",
        mode="quadgate",
        suite="coding",
        category="bugfix",
        run_index=1,
        uncached_input_tokens=1000,
        cache_read_input_tokens=4000,
        cache_creation_input_tokens=500,
        output_tokens=200,
        generative_cost_usd=0.012,
        classifier_cost_usd=0.001,
        retry_tax_usd=0.003,
        retry_tax_ms=120.0,
        hook_ipc_ms=1.5,
        classifier_latency_ms=4.2,
        wall_clock_ms=450.0,
        promoted=True,
        passed=True,
    )
    assert receipt.total_input_tokens == 5500
    # retry_tax_usd is already included inside generative_cost_usd
    assert receipt.total_cost_usd == pytest.approx(0.013)
    assert receipt.warm_cache_hit_rate == pytest.approx(4000 / 5500)

    digest = compute_receipt_sha256(receipt)
    signed = receipt.model_copy(update={"receipt_sha256": digest})
    assert verify_receipt_sha256(signed) is True

    tampered = signed.model_copy(update={"passed": False})
    assert verify_receipt_sha256(tampered) is False

    zero_in = receipt.model_copy(
        update={
            "uncached_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    )
    assert zero_in.warm_cache_hit_rate == 0.0

    with pytest.raises(ValidationError):
        TaskBenchmarkReceipt(
            task_id="CODING_01",
            task_sha256="abc123",
            mode="quadgate",
            suite="coding",
            passed=True,
            unexpected_extra_field=1,  # type: ignore[call-arg]
        )


def test_extract_provider_usage_all_providers() -> None:
    # Anthropic
    assert extract_provider_usage(
        {
            "input_tokens": 1200,
            "cache_read_input_tokens": 3500,
            "cache_creation_input_tokens": 400,
            "output_tokens": 250,
        }
    ) == (1200, 3500, 400, 250)

    # OpenAI Chat Completions
    assert extract_provider_usage(
        {
            "prompt_tokens": 5000,
            "prompt_tokens_details": {"cached_tokens": 3200},
            "completion_tokens": 300,
        }
    ) == (1800, 3200, 0, 300)

    # Gemini generateContent usageMetadata
    assert extract_provider_usage(
        {
            "promptTokenCount": 4200,
            "cachedContentTokenCount": 3000,
            "candidatesTokenCount": 190,
        }
    ) == (1200, 3000, 0, 190)

    assert extract_provider_usage({}) == (0, 0, 0, 0)


def test_compute_turn_cost_usd_exact_pricing_and_prefix_matching() -> None:
    sonnet = MODEL_PRICING_PER_1M["claude-sonnet-4-5"]
    expected_sonnet = (
        1000 * sonnet["input"]
        + 2000 * sonnet["cache_read"]
        + 500 * sonnet["cache_write"]
        + 400 * sonnet["output"]
    ) / 1_000_000.0
    assert compute_turn_cost_usd("claude-sonnet-4-5", 1000, 2000, 500, 400) == pytest.approx(
        expected_sonnet
    )

    # Prefix matching: gpt-4o-mini must NOT match gpt-4o
    mini_cost = compute_turn_cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 0, 0, 0)
    assert mini_cost == pytest.approx(MODEL_PRICING_PER_1M["gpt-4o-mini"]["input"])

    flash_lite_cost = compute_turn_cost_usd("gemini-2.5-flash-lite-001", 1_000_000, 0, 0, 0)
    assert flash_lite_cost == pytest.approx(MODEL_PRICING_PER_1M["gemini-2.5-flash-lite"]["input"])

    opus_cost = compute_turn_cost_usd("claude-opus-4-1", 0, 1_000_000, 0, 1_000_000)
    assert opus_cost == pytest.approx(
        MODEL_PRICING_PER_1M["claude-opus-4-1"]["cache_read"]
        + MODEL_PRICING_PER_1M["claude-opus-4-1"]["output"]
    )

    # Fallback on unknown model
    unknown_cost = compute_turn_cost_usd("custom-unknown-model", 1_000_000, 0, 0, 0)
    assert unknown_cost > 0.0


def test_verify_knowledge_ground_truth_cases(tmp_path: Path) -> None:
    sb = tmp_path / "kv_sandbox"
    (sb / "notes").mkdir(parents=True)
    (sb / "reports").mkdir(parents=True)
    (sb / "notes" / "q4_revenue.md").write_text("# Q4 Revenue\nNet revenue: $12,500.50\n", encoding="utf-8")

    task: dict[str, Any] = {
        "task_id": "KN_UNIT_01",
        "suite": "knowledge",
        "category": "multi_doc_audit",
        "cartridge": "knowledge_vault",
        "output_file": "reports/summary.md",
        "ground_truth": {
            "required_citations": ["[[q4_revenue]]", "Source: notes/q4_revenue.md"],
            "expected_numeric_balances": {"net_revenue": 12500.50},
            "forbidden_leaks": ["123-45-6789", "SECRET_PROJECT_ORION"],
            "required_clauses": ["Executive Summary", "Risk Mitigation"],
        },
    }

    out_path = sb / "reports" / "summary.md"

    # 1. Passing case
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]], Net Revenue is $12,500.50.\n\n"
        "## Risk Mitigation\nAll PII redacted.\n\n"
        "Source: notes/q4_revenue.md\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is True, err

    # 2. Missing citation
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]], Net Revenue is $12,500.50.\n\n"
        "## Risk Mitigation\nAll PII redacted.\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is False
    assert "citation" in err.lower()

    # 3. Broken wikilink
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]] and [[nonexistent_doc]], Net Revenue is $12,500.50.\n\n"
        "## Risk Mitigation\nAll PII redacted.\n\n"
        "Source: notes/q4_revenue.md\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is False
    assert "wikilink" in err.lower()

    # 4. Wrong numeric balance
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]], Net Revenue is $9,999.00.\n\n"
        "## Risk Mitigation\nAll PII redacted.\n\n"
        "Source: notes/q4_revenue.md\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is False
    assert "balance" in err.lower()

    # 5. Forbidden leak (case-insensitive check)
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]], Net Revenue is $12,500.50 for secret_project_orion.\n\n"
        "## Risk Mitigation\nAll PII redacted.\n\n"
        "Source: notes/q4_revenue.md\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is False
    assert "leak" in err.lower()

    # 6. Missing required clause
    out_path.write_text(
        "# Audit Report\n\n## Executive Summary\n"
        "Per [[q4_revenue]], Net Revenue is $12,500.50.\n\n"
        "Source: notes/q4_revenue.md\n",
        encoding="utf-8",
    )
    ok, err = verify_knowledge_ground_truth(sb, task)
    assert ok is False
    assert "clause" in err.lower()


def _build_synthetic_suites(suites_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    suites_dir.mkdir(parents=True, exist_ok=True)

    coding_tasks: list[dict[str, Any]] = [
        {
            "task_id": "SYN_CODE_01",
            "suite": "coding",
            "category": "subagent_chore",
            "cartridge": "software_engineering",
            "prompt": "Fix add and multiply in pkg_sc01/calc.py so tests pass.",
            "files": {
                "pkg_sc01/__init__.py": "",
                "pkg_sc01/calc.py": (
                    "def add(a: int, b: int) -> int:\n"
                    "    return a - b\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a + b\n"
                ),
                "tests/test_sc01.py": (
                    "from pkg_sc01.calc import add, multiply\n\n"
                    "def test_calc() -> None:\n"
                    "    assert add(2, 3) == 5\n"
                    "    assert multiply(3, 4) == 12\n"
                ),
            },
            "flawed_draft_files": {
                "pkg_sc01/calc.py": (
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a * b  # syntax error ->\n"
                    "def broken(:\n"
                ),
            },
            "baseline_solution_files": {
                "pkg_sc01/calc.py": (
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a + b  # forgot secondary requirement\n"
                ),
            },
            "solution_files": {
                "pkg_sc01/calc.py": (
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a * b\n"
                ),
            },
            "pytest_target": "tests/test_sc01.py",
            "expected_touched_files": ["pkg_sc01/calc.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": True,
            "replay_trace": {
                "baseline": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-1",
                    "wall_clock_ms": 1800.0,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "claude-opus-4-1",
                            "latency_ms": 950.0,
                            "usage": {
                                "input_tokens": 4000,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 1000,
                                "output_tokens": 350,
                            },
                        },
                        {
                            "turn_index": 2,
                            "model": "claude-opus-4-1",
                            "latency_ms": 850.0,
                            "usage": {
                                "input_tokens": 3000,
                                "cache_read_input_tokens": 1500,
                                "cache_creation_input_tokens": 0,
                                "output_tokens": 250,
                            },
                        },
                    ],
                    "retry_turns": [],
                },
                "quadgate": {
                    "provider": "anthropic",
                    "model": "claude-3-5-haiku",
                    "classifier_driver": "onnx_local",
                    "classifier_cost_usd": 0.0001,
                    "hook_ipc_ms": 1.2,
                    "classifier_latency_ms": 3.8,
                    "wall_clock_ms": 885.0,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "claude-3-5-haiku",
                            "latency_ms": 360.0,
                            "usage": {
                                "input_tokens": 1200,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 800,
                                "output_tokens": 180,
                            },
                        },
                    ],
                    "retry_turns": [
                        {
                            "turn_index": 2,
                            "model": "claude-sonnet-4-5",
                            "latency_ms": 520.0,
                            "usage": {
                                "input_tokens": 400,
                                "cache_read_input_tokens": 1800,
                                "cache_creation_input_tokens": 0,
                                "output_tokens": 140,
                            },
                        },
                    ],
                },
            },
        },
        {
            "task_id": "SYN_CODE_02",
            "suite": "coding",
            "category": "bugfix",
            "cartridge": "software_engineering",
            "prompt": "Fix slugify whitespace collapsing in pkg_sc02/slug.py.",
            "files": {
                "pkg_sc02/__init__.py": "",
                "pkg_sc02/slug.py": "def slugify(text: str) -> str:\n    return text.lower()\n",
                "tests/test_sc02.py": (
                    "from pkg_sc02.slug import slugify\n\n"
                    "def test_slugify() -> None:\n"
                    "    assert slugify('Hello   World') == 'hello-world'\n"
                ),
            },
            "flawed_draft_files": {},
            "solution_files": {
                "pkg_sc02/slug.py": (
                    "import re\n\n"
                    "def slugify(text: str) -> str:\n"
                    "    return re.sub(r'\\s+', '-', text.strip().lower())\n"
                ),
            },
            "pytest_target": "tests/test_sc02.py",
            "expected_touched_files": ["pkg_sc02/slug.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "replay_trace": {
                "baseline": {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "wall_clock_ms": 1100.0,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "gpt-4o",
                            "latency_ms": 1100.0,
                            "usage": {
                                "prompt_tokens": 3500,
                                "prompt_tokens_details": {"cached_tokens": 500},
                                "completion_tokens": 220,
                            },
                        }
                    ],
                    "retry_turns": [],
                },
                "quadgate": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "classifier_driver": "onnx_local",
                    "classifier_cost_usd": 0.0,
                    "hook_ipc_ms": 1.0,
                    "classifier_latency_ms": 2.5,
                    "wall_clock_ms": 423.5,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "gpt-4o-mini",
                            "latency_ms": 420.0,
                            "usage": {
                                "prompt_tokens": 1800,
                                "prompt_tokens_details": {"cached_tokens": 1200},
                                "completion_tokens": 120,
                            },
                        }
                    ],
                    "retry_turns": [],
                },
            },
        },
    ]

    knowledge_tasks: list[dict[str, Any]] = [
        {
            "task_id": "SYN_KN_01",
            "suite": "knowledge",
            "category": "financial_reconciliation",
            "cartridge": "financial_ops",
            "prompt": "Reconcile the Q1 ledger into ledgers/reconciled_01.json with balanced debits and credits.",
            "files": {
                "vault/private/payroll_tax.txt": "SSN: 987-65-4321\nTax ID: 99-8877665\n",
                "data/raw_entries.csv": "account,debit,credit\nCash,1500.00,0.00\nRevenue,0.00,1500.00\n",
            },
            "output_file": "ledgers/reconciled_01.json",
            "flawed_draft_files": {
                "ledgers/reconciled_01.json": json.dumps(
                    {
                        "entries": [
                            {"account": "Cash", "debit": 1500.00, "credit": 0.00},
                            {"account": "Revenue", "debit": 0.00, "credit": 1200.00},
                        ],
                        "net_cash": 1200.00,
                    }
                )
            },
            "solution_files": {
                "ledgers/reconciled_01.json": json.dumps(
                    {
                        "source": "data/raw_entries.csv",
                        "entries": [
                            {"account": "Cash", "debit": 1500.00, "credit": 0.00},
                            {"account": "Revenue", "debit": 0.00, "credit": 1500.00},
                        ],
                        "net_cash": 1500.00,
                        "status": "Reconciled Double-Entry Ledger",
                    }
                )
            },
            "ground_truth": {
                "required_citations": ["data/raw_entries.csv"],
                "expected_numeric_balances": {"net_cash": 1500.00},
                "forbidden_leaks": ["987-65-4321", "99-8877665"],
                "required_clauses": ["Reconciled Double-Entry Ledger"],
            },
            "expected_touched_files": ["ledgers/reconciled_01.json"],
            "simulates_one_strike_promotion": True,
            "replay_trace": {
                "baseline": {
                    "provider": "gemini",
                    "model": "gemini-2.5-pro",
                    "wall_clock_ms": 1400.0,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "gemini-2.5-pro",
                            "latency_ms": 1400.0,
                            "usage": {
                                "promptTokenCount": 4000,
                                "cachedContentTokenCount": 0,
                                "candidatesTokenCount": 300,
                            },
                        }
                    ],
                    "retry_turns": [],
                },
                "quadgate": {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash-lite",
                    "classifier_driver": "onnx_local",
                    "classifier_cost_usd": 0.0,
                    "hook_ipc_ms": 1.1,
                    "classifier_latency_ms": 2.9,
                    "wall_clock_ms": 744.0,
                    "turns": [
                        {
                            "turn_index": 1,
                            "model": "gemini-2.5-flash-lite",
                            "latency_ms": 340.0,
                            "usage": {
                                "promptTokenCount": 1600,
                                "cachedContentTokenCount": 1000,
                                "candidatesTokenCount": 150,
                            },
                        }
                    ],
                    "retry_turns": [
                        {
                            "turn_index": 2,
                            "model": "gemini-2.5-flash",
                            "latency_ms": 400.0,
                            "usage": {
                                "promptTokenCount": 1700,
                                "cachedContentTokenCount": 1300,
                                "candidatesTokenCount": 160,
                            },
                        }
                    ],
                },
            },
        },
    ]

    (suites_dir / "coding_tasks.jsonl").write_text(
        "\n".join(json.dumps(t) for t in coding_tasks) + "\n",
        encoding="utf-8",
    )
    (suites_dir / "knowledge_tasks.jsonl").write_text(
        "\n".join(json.dumps(t) for t in knowledge_tasks) + "\n",
        encoding="utf-8",
    )
    return coding_tasks, knowledge_tasks


async def test_benchmark_runner_dry_replay_git_worktree_and_report(tmp_path: Path) -> None:
    suites_dir = tmp_path / "suites"
    reports_dir = tmp_path / "reports"
    coding_tasks, knowledge_tasks = _build_synthetic_suites(suites_dir)

    # Verify default suites fallback when reports_dir lacks sibling/child suites
    orphan_reports = tmp_path / "isolated" / "reports"
    orphan_runner = BenchmarkRunner(reports_dir=orphan_reports)
    assert orphan_runner.suites_dir == (
        Path(__file__).resolve().parent.parent / "benchmarks" / "suites"
    )

    runner = BenchmarkRunner(reports_dir=reports_dir)
    assert runner.suites_dir == suites_dir

    receipts = await runner.run_suite(suite="all", runs=1, dry_replay=True)
    expected_total = (len(coding_tasks) + len(knowledge_tasks)) * 2
    assert len(receipts) == expected_total

    by_id_mode = {(r.task_id, r.mode): r for r in receipts}
    # SYN_CODE_01: baseline misses secondary requirement -> fails; quadgate heals via 1-strike -> passes
    assert by_id_mode[("SYN_CODE_01", "baseline")].passed is False
    qg_c1 = by_id_mode[("SYN_CODE_01", "quadgate")]
    assert qg_c1.passed is True
    assert qg_c1.promoted is True
    assert qg_c1.retry_tax_usd > 0.0
    assert qg_c1.retry_tax_ms == pytest.approx(520.0)
    assert qg_c1.generative_cost_usd > qg_c1.retry_tax_usd
    assert qg_c1.wall_clock_ms >= 360.0 + 520.0

    # SYN_CODE_02: both pass
    assert by_id_mode[("SYN_CODE_02", "baseline")].passed is True
    assert by_id_mode[("SYN_CODE_02", "quadgate")].passed is True
    assert by_id_mode[("SYN_CODE_02", "quadgate")].promoted is False

    # SYN_KN_01: financial_ops 1-strike promotion
    qg_k1 = by_id_mode[("SYN_KN_01", "quadgate")]
    assert qg_k1.passed is True
    assert qg_k1.promoted is True
    assert qg_k1.retry_tax_usd > 0.0
    assert qg_k1.retry_tax_ms == pytest.approx(400.0)

    # Verify SHA-256 task + receipt signatures & run_*.jsonl output
    for r in receipts:
        assert len(r.task_sha256) == 64
        assert verify_receipt_sha256(r) is True
    assert by_id_mode[("SYN_CODE_01", "baseline")].task_sha256 == compute_task_sha256(
        coding_tasks[0]
    )

    jsonl_files = list(reports_dir.glob("run_*.jsonl"))
    assert len(jsonl_files) == 1
    reloaded_lines = [
        TaskBenchmarkReceipt.model_validate_json(ln)
        for ln in jsonl_files[0].read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(reloaded_lines) == expected_total
    assert all(verify_receipt_sha256(r) for r in reloaded_lines)

    # Verify LATEST_PROOF.md scorecard sections and formatting
    proof_path = reports_dir / "LATEST_PROOF.md"
    assert proof_path.is_file()
    md = proof_path.read_text(encoding="utf-8")
    assert "Paired 95% Confidence Intervals" in md
    assert "Total Input Token Savings" in md
    assert "Total Cost Reduction" in md
    assert "Wall-Clock Latency Reduction" in md
    assert "Warm KV-Cache Hit Rate (%)" in md
    assert "p50 / p90 / mean (ms)" in md
    assert "Suite: `coding`" in md
    assert "Suite: `knowledge`" in md
    assert "Tamper-Evident SHA-256 Receipt Manifest" in md
    assert qg_c1.receipt_sha256 in md
    # Ensure no unescaped paired $...$ KaTeX math hazards exist outside backticks
    stripped_backticks = re.sub(r"`[^`]*`", "", md)
    for line in stripped_backticks.splitlines():
        assert line.count("$") <= 1, f"Potential unescaped KaTeX currency collision: {line}"


async def test_benchmark_runner_live_mode_requires_keys_and_supports_injected_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suites_dir = tmp_path / "suites"
    reports_dir = tmp_path / "reports"
    _build_synthetic_suites(suites_dir)

    for env_key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env_key, raising=False)

    runner_no_key = BenchmarkRunner(reports_dir=reports_dir, suites_dir=suites_dir)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await runner_no_key.run_suite(suite="coding", runs=1, dry_replay=False)

    calls: list[tuple[str, str]] = []

    async def fake_live_runner(task: dict[str, Any], mode: str) -> dict[str, Any]:
        calls.append((str(task["task_id"]), mode))
        return {
            "model": "gpt-4o-mini" if mode == "quadgate" else "gpt-4o",
            "candidate_files": dict(task["solution_files"]),
            "turns": [
                {
                    "model": "gpt-4o-mini" if mode == "quadgate" else "gpt-4o",
                    "latency_ms": 200.0 if mode == "quadgate" else 600.0,
                    "usage": {
                        "prompt_tokens": 1200 if mode == "quadgate" else 3000,
                        "prompt_tokens_details": {
                            "cached_tokens": 900 if mode == "quadgate" else 0
                        },
                        "completion_tokens": 100 if mode == "quadgate" else 220,
                    },
                }
            ],
            "retry_turns": [],
            "classifier_cost_usd": 0.0002 if mode == "quadgate" else 0.0,
            "wall_clock_ms": 200.0 if mode == "quadgate" else 600.0,
        }

    custom_report = tmp_path / "custom_proof.md"
    runner_live = BenchmarkRunner(
        reports_dir=reports_dir,
        suites_dir=suites_dir,
        live_runner=fake_live_runner,
    )
    receipts = await runner_live.run_suite(
        suite="coding",
        runs=1,
        dry_replay=False,
        output_path=custom_report,
    )
    assert len(receipts) == 4
    assert len(calls) == 4
    assert all(r.passed for r in receipts)
    assert custom_report.is_file()

    qg_receipts = [r for r in receipts if r.mode == "quadgate"]
    assert all(r.classifier_cost_usd == pytest.approx(0.0002) for r in qg_receipts)
    # Measured GateEngine latency must be added into wall_clock_ms in live mode
    assert all(r.wall_clock_ms > 200.0 for r in qg_receipts)
    assert all((r.hook_ipc_ms + r.classifier_latency_ms) > 0.0 for r in qg_receipts)
    assert all(verify_receipt_sha256(r) for r in receipts)


async def test_verify_coding_ground_truth_standalone(tmp_path: Path) -> None:
    sb = tmp_path / "standalone_code"
    sb.mkdir()
    (sb / "standalone_mod.py").write_text("def double(x: int) -> int:\n    return x * 2\n", encoding="utf-8")
    (sb / "test_standalone_mod.py").write_text(
        "from standalone_mod import double\n\ndef test_double() -> None:\n    assert double(4) == 8\n",
        encoding="utf-8",
    )
    ok, out = await verify_coding_ground_truth(sb, {"pytest_target": "test_standalone_mod.py"})
    assert ok is True, out

    (sb / "standalone_mod.py").write_text("def double(x: int) -> int:\n    return x + 2\n", encoding="utf-8")
    ok_fail, _ = await verify_coding_ground_truth(sb, {"pytest_target": "test_standalone_mod.py"})
    assert ok_fail is False

    ok_missing, err_missing = await verify_coding_ground_truth(sb, {"pytest_target": "missing.py"})
    assert ok_missing is False
    assert "Missing pytest target" in err_missing


async def test_benchmark_runner_live_http_provider_via_local_mock_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiohttp import web

    suites_dir = tmp_path / "suites"
    reports_dir = tmp_path / "reports"
    _, knowledge_tasks = _build_synthetic_suites(suites_dir)
    solution_json = json.dumps({"files": knowledge_tasks[0]["solution_files"]})

    async def handle_chat(request: web.Request) -> web.Response:
        body = await request.json()
        is_cached = any(
            "[QUADGATE_CONTEXT_HINTS]" in str(m.get("content", ""))
            for m in body.get("messages", [])
        )
        return web.json_response(
            {
                "choices": [{"message": {"content": solution_json}}],
                "usage": {
                    "prompt_tokens": 1100 if is_cached else 2400,
                    "prompt_tokens_details": {"cached_tokens": 800 if is_cached else 0},
                    "completion_tokens": 180,
                },
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle_chat)
    runner_web = web.AppRunner(app)
    await runner_web.setup()
    site = web.TCPSite(runner_web, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site, "_server", None)
    assert sockets is not None
    port = sockets.sockets[0].getsockname()[1]

    try:
        for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")

        runner = BenchmarkRunner(reports_dir=reports_dir, suites_dir=suites_dir)
        receipts = await runner.run_suite(suite="knowledge", runs=1, dry_replay=False)
        assert len(receipts) == 2
        assert all(r.passed for r in receipts)
        assert all(verify_receipt_sha256(r) for r in receipts)
        assert sum(r.total_input_tokens for r in receipts) > 0
    finally:
        await runner_web.cleanup()
