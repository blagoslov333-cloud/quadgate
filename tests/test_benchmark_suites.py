"""Validation tests for QuadGate coding and knowledge benchmark suites."""

from __future__ import annotations

import ast
import json
import py_compile
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from quadgate.contracts import GateEvent, GateType, ModelTier, VerdictAction
from quadgate.kernel.gates import GateEngine, TierModelConfig, load_cartridge

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUITES_DIR = _REPO_ROOT / "benchmarks" / "suites"
_CODING_JSONL = _SUITES_DIR / "coding_tasks.jsonl"
_KNOWLEDGE_JSONL = _SUITES_DIR / "knowledge_tasks.jsonl"

_CODING_CATEGORIES = ("bugfix", "refactor", "invariant_protected", "subagent_chore")
_KNOWLEDGE_CATEGORY_CARTRIDGES = {
    "multi_doc_audit": "knowledge_vault",
    "financial_reconciliation": "financial_ops",
    "policy_compliance": "legal_redline",
}
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|[^\]\n]+)?\]\]")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    assert path.is_file(), f"Missing benchmark suite file: {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines, f"Suite file is empty: {path}"
    records: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        assert line.strip(), f"Blank line {idx} in {path}"
        obj = json.loads(line)
        assert isinstance(obj, dict), f"Line {idx} in {path} must be a JSON object"
        records.append(obj)
    return records


def _write_workspace(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run_pytest_target(root: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "--no-summary",
            "-p",
            "no:cacheprovider",
            target,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _assert_valid_provider_usage(provider: str, usage: dict[str, Any]) -> None:
    if provider == "anthropic":
        assert set(usage.keys()) == {
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        }
        assert all(isinstance(v, int) and v >= 0 for v in usage.values())
    elif provider == "openai":
        assert set(usage.keys()) == {"prompt_tokens", "prompt_tokens_details", "completion_tokens"}
        details = usage["prompt_tokens_details"]
        assert isinstance(details, dict) and set(details.keys()) == {"cached_tokens"}
        assert isinstance(usage["prompt_tokens"], int)
        assert isinstance(details["cached_tokens"], int)
        assert isinstance(usage["completion_tokens"], int)
        assert usage["prompt_tokens"] >= details["cached_tokens"] >= 0
        assert usage["completion_tokens"] >= 0
    elif provider == "gemini":
        assert set(usage.keys()) == {
            "promptTokenCount",
            "cachedContentTokenCount",
            "candidatesTokenCount",
        }
        assert all(isinstance(v, int) and v >= 0 for v in usage.values())
        assert usage["promptTokenCount"] >= usage["cachedContentTokenCount"] >= 0
    else:
        raise AssertionError(f"Unsupported provider in replay_trace: {provider!r}")


def _assert_valid_replay_trace(
    trace: dict[str, Any],
    *,
    expect_retry: bool,
    require_local_classifier: bool = False,
) -> None:
    assert set(trace.keys()) == {"baseline", "quadgate"}
    base = trace["baseline"]
    qg = trace["quadgate"]
    assert base["provider"] in {"anthropic", "openai", "gemini"}
    assert qg["provider"] in {"anthropic", "openai", "gemini"}
    assert isinstance(base["wall_clock_ms"], (int, float)) and base["wall_clock_ms"] > 0
    assert isinstance(qg["wall_clock_ms"], (int, float)) and qg["wall_clock_ms"] > 0
    assert isinstance(base["turns"], list) and len(base["turns"]) >= 2
    assert isinstance(qg["turns"], list) and len(qg["turns"]) >= 2
    assert base["retry_turns"] == []
    assert qg["classifier_driver"] in {"onnx_local", "decision1"}
    if require_local_classifier:
        assert qg["classifier_driver"] == "onnx_local"
        assert float(qg["classifier_cost_usd"]) == 0.0
    if qg["classifier_driver"] == "onnx_local":
        assert float(qg["classifier_cost_usd"]) == 0.0
    else:
        assert float(qg["classifier_cost_usd"]) > 0.0
    assert float(qg["hook_ipc_ms"]) > 0.0
    assert float(qg["classifier_latency_ms"]) > 0.0

    for turn in base["turns"]:
        _assert_valid_provider_usage(base["provider"], turn["usage"])
    for turn in qg["turns"]:
        _assert_valid_provider_usage(qg["provider"], turn["usage"])
    if expect_retry:
        assert isinstance(qg["retry_turns"], list) and len(qg["retry_turns"]) >= 1
        for turn in qg["retry_turns"]:
            _assert_valid_provider_usage(qg["provider"], turn["usage"])
    else:
        assert qg["retry_turns"] == []


def _extract_numeric_for_key(text: str, key: str, ext: str) -> float | None:
    if ext == ".json":
        data = json.loads(text)
        if key in data and isinstance(data[key], (int, float)):
            return float(data[key])
        return None
    for line in text.splitlines():
        if key in line:
            after = line.split(key, 1)[1]
            matches = re.findall(r"[-+]?\d+(?:\.\d+)?", after)
            if matches:
                return float(matches[-1])
    return None


def _check_knowledge_ground_truth(
    source_files: dict[str, str],
    output_file: str,
    output_text: str,
    gt: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not output_text.strip():
        errors.append("output_file is empty")
        return errors

    stems = {Path(p).stem.lower() for p in source_files}
    rel_paths = {p.replace("\\", "/").lower() for p in source_files}
    for cit in gt["required_citations"]:
        if cit not in output_text:
            errors.append(f"missing required citation: {cit!r}")
        for m in _WIKILINK_RE.finditer(cit):
            ref = m.group(1).strip().replace("\\", "/").lower()
            if ref not in rel_paths and f"{ref}.md" not in rel_paths and ref not in stems:
                errors.append(f"broken wikilink citation: {cit!r}")

    ext = Path(output_file).suffix.lower()
    for key, expected in gt["expected_numeric_balances"].items():
        actual = _extract_numeric_for_key(output_text, key, ext)
        if actual is None or abs(actual - float(expected)) > 0.01:
            errors.append(f"balance mismatch for {key}: expected={expected}, actual={actual}")

    lowered = output_text.lower()
    for leak in gt["forbidden_leaks"]:
        if leak.lower() in lowered:
            errors.append(f"forbidden leak present: {leak!r}")

    for clause in gt["required_clauses"]:
        if clause.lower() not in lowered:
            errors.append(f"missing required clause: {clause!r}")
    return errors


def test_coding_suite_schema_ast_and_uniqueness() -> None:
    """Validate coding_tasks.jsonl count, categories, AST validity, and naming isolation."""
    tasks = _load_jsonl(_CODING_JSONL)
    assert len(tasks) == 20
    assert [t["task_id"] for t in tasks] == [f"CODING_{i:02d}" for i in range(1, 21)]

    counts = {cat: 0 for cat in _CODING_CATEGORIES}
    seen_packages: set[str] = set()
    seen_targets: set[str] = set()
    chore_ids: set[str] = set()
    promo_ids: set[str] = set()
    baseline_diff_ids: set[str] = set()

    for task in tasks:
        assert task["suite"] == "coding"
        assert task["cartridge"] == "software_engineering"
        cat = task["category"]
        assert cat in counts
        counts[cat] += 1

        files = task["files"]
        sol = task["solution_files"]
        base = task["baseline_solution_files"]
        flaw = task["flawed_draft_files"]
        target = task["pytest_target"]
        touched = task["expected_touched_files"]

        assert isinstance(files, dict) and len(files) >= 3
        py_sources = [p for p in files if p.endswith(".py") and not p.startswith("tests/")]
        assert len(py_sources) >= 2
        assert target in files and target.endswith(".py")
        assert target not in seen_targets
        seen_targets.add(target)

        pkgs = {Path(p).parts[0] for p in py_sources if len(Path(p).parts) > 1}
        assert len(pkgs) == 1
        pkg_name = next(iter(pkgs))
        assert pkg_name not in seen_packages
        seen_packages.add(pkg_name)

        assert isinstance(sol, dict) and sol
        assert isinstance(touched, list) and set(touched) == set(sol.keys())
        for rel, code in sol.items():
            assert rel in files
            ast.parse(code, filename=rel)

        if base != sol:
            baseline_diff_ids.add(task["task_id"])

        if task["subagent_chore"]:
            chore_ids.add(task["task_id"])

        if task["simulates_one_strike_promotion"]:
            promo_ids.add(task["task_id"])
            assert isinstance(flaw, dict) and flaw
        else:
            assert flaw == {}

        _assert_valid_replay_trace(
            task["replay_trace"],
            expect_retry=bool(task["simulates_one_strike_promotion"]),
        )

    assert counts == {cat: 5 for cat in _CODING_CATEGORIES}
    assert {f"CODING_{i:02d}" for i in range(16, 21)}.issubset(chore_ids)
    assert promo_ids == {"CODING_05", "CODING_14", "CODING_18"}
    assert baseline_diff_ids == {"CODING_10", "CODING_18"}


def test_coding_suite_pytest_execution_and_gates(tmp_path: Path) -> None:
    """Execute pytest and py_compile across pre-fix, solution, baseline, and flawed drafts."""
    tasks = _load_jsonl(_CODING_JSONL)
    jobs: list[tuple[str, str, Path, str]] = []

    for task in tasks:
        tid = task["task_id"]
        pre_dir = tmp_path / f"{tid}_pre"
        _write_workspace(pre_dir, task["files"])
        jobs.append((tid, "pre", pre_dir, task["pytest_target"]))

        sol_dir = tmp_path / f"{tid}_sol"
        _write_workspace(sol_dir, {**task["files"], **task["solution_files"]})
        for rel in task["solution_files"]:
            py_compile.compile(str(sol_dir / rel), doraise=True)
        jobs.append((tid, "sol", sol_dir, task["pytest_target"]))

        base_dir = tmp_path / f"{tid}_base"
        _write_workspace(base_dir, {**task["files"], **task["baseline_solution_files"]})
        jobs.append((tid, "base", base_dir, task["pytest_target"]))

        if task["flawed_draft_files"]:
            flaw_dir = tmp_path / f"{tid}_flaw"
            _write_workspace(flaw_dir, {**task["files"], **task["flawed_draft_files"]})
            jobs.append((tid, "flaw", flaw_dir, task["pytest_target"]))

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: (j[0], j[1], _run_pytest_target(j[2], j[3])), jobs))

    for tid, mode, proc in results:
        if mode == "pre":
            assert proc.returncode != 0, f"{tid} pre-fix passed unexpectedly:\n{proc.stdout}"
        elif mode == "sol":
            assert proc.returncode == 0, f"{tid} solution failed:\n{proc.stdout}\n{proc.stderr}"
        elif mode == "base":
            if tid in {"CODING_10", "CODING_18"}:
                assert proc.returncode != 0, f"{tid} baseline should fail secondary check"
            else:
                assert proc.returncode == 0, f"{tid} baseline failed:\n{proc.stdout}\n{proc.stderr}"
        elif mode == "flaw":
            assert proc.returncode != 0, f"{tid} flawed draft passed unexpectedly"


async def test_coding_suite_real_gate_engine(tmp_path: Path) -> None:
    """Verify GateEngine Gate 2, Gate 3, and Gate 4 verdicts across coding tasks."""
    tasks = _load_jsonl(_CODING_JSONL)
    engine = GateEngine(
        load_cartridge("software_engineering"),
        tiers=TierModelConfig(frontier="pro", mid="flash", floor="flash_lite"),
    )

    for task in tasks:
        tid = task["task_id"]
        sol_dir = tmp_path / f"{tid}_gate_sol"
        _write_workspace(sol_dir, {**task["files"], **task["solution_files"]})

        for rel, content in task["solution_files"].items():
            v2 = await engine.evaluate_gate2_firewall(
                GateEvent(
                    gate=GateType.FIREWALL,
                    session_id=f"suite-g2-{tid}",
                    turn_index=2,
                    workspace_root=str(sol_dir),
                    tool_name="write_to_file",
                    tool_args={"TargetFile": rel, "CodeContent": content},
                    touched_files=[rel],
                    diff_text=content,
                )
            )
            assert v2.action == VerdictAction.ALLOW, f"{tid} Gate 2 blocked {rel}: {v2.reason}"

        if task["subagent_chore"]:
            v3 = await engine.evaluate_gate3_subagent(
                GateEvent(
                    gate=GateType.SUBAGENT_GOVERNOR,
                    session_id=f"suite-g3-{tid}",
                    turn_index=1,
                    workspace_root=str(sol_dir),
                    tool_name="invoke_subagent",
                    tool_args={
                        "brief": task["prompt"],
                        "target_paths": task["expected_touched_files"],
                    },
                    prompt_text=task["prompt"],
                    assigned_file_cage=task["expected_touched_files"],
                )
            )
            assert v3.target_tier in {ModelTier.FLOOR, ModelTier.MID}

        v4 = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id=f"suite-g4-{tid}",
                turn_index=2,
                workspace_root=str(sol_dir),
                prompt_text=task["prompt"],
                touched_files=task["expected_touched_files"],
            )
        )
        assert v4.action == VerdictAction.ALLOW, f"{tid} Gate 4 failed: {v4.reason}"

        if task["simulates_one_strike_promotion"]:
            flaw_dir = tmp_path / f"{tid}_gate_flaw"
            _write_workspace(flaw_dir, {**task["files"], **task["flawed_draft_files"]})
            v4_flaw = await engine.evaluate_gate4_stop(
                GateEvent(
                    gate=GateType.STOP_AUDIT,
                    session_id=f"suite-g4-flaw-{tid}",
                    turn_index=1,
                    workspace_root=str(flaw_dir),
                    prompt_text=task["prompt"],
                    touched_files=list(task["flawed_draft_files"].keys()),
                    current_tier=ModelTier.FLOOR,
                    strike_count=0,
                )
            )
            assert v4_flaw.action == VerdictAction.PROMOTE


async def test_knowledge_suite_schema_ground_truth_and_oracles(tmp_path: Path) -> None:
    """Validate knowledge_tasks.jsonl count, schema, ground truth, and Cartridge Gate 4 oracles."""
    tasks = _load_jsonl(_KNOWLEDGE_JSONL)
    assert len(tasks) == 15
    assert [t["task_id"] for t in tasks] == [f"KNOWLEDGE_{i:02d}" for i in range(1, 16)]

    counts = {cat: 0 for cat in _KNOWLEDGE_CATEGORY_CARTRIDGES}
    promo_ids: set[str] = set()
    baseline_fail_ids: set[str] = set()

    for task in tasks:
        tid = task["task_id"]
        assert task["suite"] == "knowledge"
        cat = task["category"]
        assert cat in _KNOWLEDGE_CATEGORY_CARTRIDGES
        assert task["cartridge"] == _KNOWLEDGE_CATEGORY_CARTRIDGES[cat]
        counts[cat] += 1

        files = task["files"]
        out_file = task["output_file"]
        sol = task["solution_files"]
        base = task["baseline_solution_files"]
        flaw = task["flawed_draft_files"]
        gt = task["ground_truth"]

        assert isinstance(files, dict) and len(files) >= 2
        assert out_file in sol and task["expected_touched_files"] == [out_file]
        assert len(gt["forbidden_leaks"]) >= 1
        assert len(gt["required_clauses"]) >= 2

        joined_sources = "\n".join(files.values())
        for leak in gt["forbidden_leaks"]:
            assert leak in joined_sources, (
                f"{tid} forbidden leak {leak!r} missing from source files"
            )

        sol_errors = _check_knowledge_ground_truth(files, out_file, sol[out_file], gt)
        assert not sol_errors, f"{tid} solution failed ground truth: {sol_errors}"

        base_errors = _check_knowledge_ground_truth(files, out_file, base[out_file], gt)
        if base_errors:
            baseline_fail_ids.add(tid)

        _assert_valid_replay_trace(
            task["replay_trace"],
            expect_retry=bool(task["simulates_one_strike_promotion"]),
            require_local_classifier=True,
        )

        engine = GateEngine(
            load_cartridge(task["cartridge"]),
            tiers=TierModelConfig(frontier="pro", mid="flash", floor="flash_lite"),
        )
        ws_sol = tmp_path / f"{tid}_sol"
        _write_workspace(ws_sol, {**files, **sol})
        v4 = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id=f"suite-k-{tid}",
                turn_index=2,
                workspace_root=str(ws_sol),
                prompt_text=task["prompt"],
                touched_files=[out_file],
            )
        )
        assert v4.action == VerdictAction.ALLOW, (
            f"{tid} Gate 4 oracle rejected solution: {v4.reason}"
        )
        assert v4.cloud_egress_allowed is False

        if task["simulates_one_strike_promotion"]:
            promo_ids.add(tid)
            assert isinstance(flaw, dict) and out_file in flaw
            ws_flaw = tmp_path / f"{tid}_flaw"
            _write_workspace(ws_flaw, {**files, **flaw})
            v4_flaw = await engine.evaluate_gate4_stop(
                GateEvent(
                    gate=GateType.STOP_AUDIT,
                    session_id=f"suite-k-flaw-{tid}",
                    turn_index=1,
                    workspace_root=str(ws_flaw),
                    prompt_text=task["prompt"],
                    touched_files=[out_file],
                    current_tier=ModelTier.MID,
                    strike_count=0,
                )
            )
            assert v4_flaw.action == VerdictAction.PROMOTE
        else:
            assert flaw == {}

    assert counts == {cat: 5 for cat in _KNOWLEDGE_CATEGORY_CARTRIDGES}
    assert promo_ids == {"KNOWLEDGE_08", "KNOWLEDGE_14"}
    assert baseline_fail_ids == {"KNOWLEDGE_05", "KNOWLEDGE_12"}
