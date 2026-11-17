"""Reproducible A/B sandbox benchmark runner (`quadgate bench`)."""

from __future__ import annotations

import asyncio
import csv
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Mapping
import uuid

import aiohttp
from pydantic import BaseModel, ConfigDict, Field

from quadgate.contracts import (
    GateEvent,
    GateType,
    HarnessType,
    ModelTier,
    VerdictAction,
)
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.gates import GateEngine, TierModelConfig, load_cartridge
from quadgate.kernel.healing import SelfHealingEngine

_DEFAULT_SUITES_DIR = Path(__file__).resolve().parent / "suites"
_EXCLUDED_SANDBOX_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
)
_MD_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|[^\]\n]+)?\]\]")
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<!\w)(\(?[-+]?[$€£]?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?|\(?[-+]?[$€£]?\s*\d+(?:\.\d+)?)\)?(?!\w)"
)

# Published per-1M token USD rates (input, cache_read, cache_write, output).
MODEL_PRICING_PER_1M: dict[str, dict[str, float]] = {
    "claude-opus-4-1": {
        "input": 15.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
        "output": 75.00,
    },
    "claude-sonnet-4-5": {
        "input": 3.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.00,
    },
    "claude-3-5-haiku": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.00,
        "output": 4.00,
    },
    "gpt-4o": {
        "input": 2.50,
        "cache_read": 1.25,
        "cache_write": 2.50,
        "output": 10.00,
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "cache_read": 0.075,
        "cache_write": 0.15,
        "output": 0.60,
    },
    "o3": {
        "input": 2.00,
        "cache_read": 0.50,
        "cache_write": 2.00,
        "output": 8.00,
    },
    "gemini-2.5-pro": {
        "input": 1.25,
        "cache_read": 0.3125,
        "cache_write": 1.25,
        "output": 10.00,
    },
    "gemini-2.5-flash": {
        "input": 0.30,
        "cache_read": 0.075,
        "cache_write": 0.30,
        "output": 2.50,
    },
    "gemini-2.5-flash-lite": {
        "input": 0.10,
        "cache_read": 0.025,
        "cache_write": 0.10,
        "output": 0.40,
    },
}

_MODEL_ALIAS_MAP: dict[str, str] = {
    "opus": "claude-opus-4-1",
    "sonnet": "claude-sonnet-4-5",
    "haiku": "claude-3-5-haiku",
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
    "flash_lite": "gemini-2.5-flash-lite",
    "flash-lite": "gemini-2.5-flash-lite",
}
_DEFAULT_MODEL_PRICING: dict[str, float] = MODEL_PRICING_PER_1M["claude-sonnet-4-5"]

# Two-tailed 95% Student's t critical values (alpha = 0.05) for df = 1..30.
_T_CRITICAL_95: dict[int, float] = {
    1: 12.7062,
    2: 4.3027,
    3: 3.1824,
    4: 2.7764,
    5: 2.5706,
    6: 2.4469,
    7: 2.3646,
    8: 2.3060,
    9: 2.2622,
    10: 2.2281,
    11: 2.2010,
    12: 2.1788,
    13: 2.1604,
    14: 2.1448,
    15: 2.1314,
    16: 2.1199,
    17: 2.1098,
    18: 2.1009,
    19: 2.0930,
    20: 2.0860,
    21: 2.0796,
    22: 2.0739,
    23: 2.0687,
    24: 2.0639,
    25: 2.0595,
    26: 2.0555,
    27: 2.0518,
    28: 2.0484,
    29: 2.0452,
    30: 2.0423,
}

_PYTEST_WORKER_CODE = """\
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import pytest

while True:
    raw_line = sys.stdin.readline()
    if not raw_line:
        break
    raw_line = raw_line.strip()
    if not raw_line:
        continue
    req = json.loads(raw_line)
    sandbox = req["sandbox"]
    target = req["target"]
    result_file = req["result_file"]
    sb_real = os.path.normcase(os.path.realpath(sandbox))
    old_cwd = os.getcwd()
    old_path = list(sys.path)
    before_mods = set(sys.modules.keys())
    buf = io.StringIO()
    rc = 1
    try:
        os.chdir(sandbox)
        if sandbox not in sys.path:
            sys.path.insert(0, sandbox)
        importlib.invalidate_caches()
        for cache_k in list(sys.path_importer_cache.keys()):
            if cache_k == sandbox or os.path.normcase(os.path.realpath(cache_k)).startswith(sb_real):
                sys.path_importer_cache.pop(cache_k, None)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = int(
                pytest.main(
                    [
                        "--no-header",
                        "--no-summary",
                        "-p",
                        "no:cacheprovider",
                        "--noconftest",
                        "-q",
                        target,
                    ]
                )
            )
    except Exception as exc:
        buf.write(f"Worker error: {exc}\\n")
        rc = 1
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        for mod_name, mod in list(sys.modules.items()):
            if mod_name in before_mods:
                continue
            mod_file = getattr(mod, "__file__", None)
            if mod_file and os.path.normcase(os.path.realpath(mod_file)).startswith(sb_real):
                del sys.modules[mod_name]
                continue
            mod_paths = getattr(mod, "__path__", None)
            if mod_paths and any(
                os.path.normcase(os.path.realpath(str(p))).startswith(sb_real)
                for p in mod_paths
            ):
                del sys.modules[mod_name]
        importlib.invalidate_caches()
    Path(result_file).write_text(
        json.dumps({"rc": rc, "out": buf.getvalue()}),
        encoding="utf-8",
    )
    sys.stdout.write("OK\\n")
    sys.stdout.flush()
"""


def _safe_int(val: Any) -> int:
    if isinstance(val, bool) or val is None:
        return 0
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 0


def _safe_float(val: Any) -> float:
    if isinstance(val, bool) or val is None:
        return 0.0
    try:
        num = float(val)
        return num if math.isfinite(num) and num >= 0.0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def extract_provider_usage(
    raw_usage: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Extract `(uncached_in, cache_read_in, cache_creation_in, out)` tokens from API usage."""
    if not raw_usage:
        return 0, 0, 0, 0

    # Gemini usageMetadata
    if (
        "promptTokenCount" in raw_usage
        or "candidatesTokenCount" in raw_usage
        or "cachedContentTokenCount" in raw_usage
    ):
        prompt_tokens = _safe_int(raw_usage.get("promptTokenCount"))
        cached = _safe_int(raw_usage.get("cachedContentTokenCount"))
        uncached = max(0, prompt_tokens - cached)
        output = _safe_int(raw_usage.get("candidatesTokenCount"))
        return uncached, cached, 0, output

    # OpenAI Chat Completions usage
    if "prompt_tokens" in raw_usage or "completion_tokens" in raw_usage:
        prompt_tokens = _safe_int(raw_usage.get("prompt_tokens"))
        details = raw_usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            cached = _safe_int(details.get("cached_tokens"))
        else:
            cached = _safe_int(raw_usage.get("cached_tokens"))
        uncached = max(0, prompt_tokens - cached)
        output = _safe_int(raw_usage.get("completion_tokens"))
        return uncached, cached, 0, output

    # OpenAI Responses API variant (input_tokens_details without cache_read_input_tokens)
    if (
        "input_tokens_details" in raw_usage
        and "cache_read_input_tokens" not in raw_usage
        and "cache_creation_input_tokens" not in raw_usage
    ):
        total_in = _safe_int(raw_usage.get("input_tokens"))
        details = raw_usage.get("input_tokens_details")
        cached = _safe_int(details.get("cached_tokens")) if isinstance(details, Mapping) else 0
        uncached = max(0, total_in - cached)
        output = _safe_int(raw_usage.get("output_tokens"))
        return uncached, cached, 0, output

    # Anthropic Messages API usage
    if (
        "input_tokens" in raw_usage
        or "output_tokens" in raw_usage
        or "cache_read_input_tokens" in raw_usage
        or "cache_creation_input_tokens" in raw_usage
    ):
        uncached = _safe_int(raw_usage.get("input_tokens"))
        cache_read = _safe_int(raw_usage.get("cache_read_input_tokens"))
        cache_creation = _safe_int(raw_usage.get("cache_creation_input_tokens"))
        output = _safe_int(raw_usage.get("output_tokens"))
        return uncached, cache_read, cache_creation, output

    return 0, 0, 0, 0


def _resolve_model_pricing(model: str) -> dict[str, float]:
    """Match model identifier against `MODEL_PRICING_PER_1M` using exact/prefix/alias lookup."""
    norm = model.strip().lower()
    if not norm:
        return _DEFAULT_MODEL_PRICING

    if norm in MODEL_PRICING_PER_1M:
        return MODEL_PRICING_PER_1M[norm]

    aliased = _MODEL_ALIAS_MAP.get(norm)
    if aliased is not None:
        return MODEL_PRICING_PER_1M[aliased]

    # Longest-key prefix/substring matching prevents `gpt-4o` shadowing `gpt-4o-mini`.
    for key in sorted(MODEL_PRICING_PER_1M.keys(), key=len, reverse=True):
        if norm.startswith(key):
            return MODEL_PRICING_PER_1M[key]
    for key in sorted(MODEL_PRICING_PER_1M.keys(), key=len, reverse=True):
        if key in norm:
            return MODEL_PRICING_PER_1M[key]

    return _DEFAULT_MODEL_PRICING


def compute_turn_cost_usd(
    model: str,
    uncached_in: int,
    cache_read_in: int,
    cache_write_in: int,
    out_tok: int,
) -> float:
    """Compute exact USD cost for a single turn from token counts and published 1M rates."""
    pricing = _resolve_model_pricing(model)
    u_in = max(0, int(uncached_in))
    c_read = max(0, int(cache_read_in))
    c_write = max(0, int(cache_write_in))
    out = max(0, int(out_tok))
    cost = (
        u_in * pricing["input"]
        + c_read * pricing["cache_read"]
        + c_write * pricing["cache_write"]
        + out * pricing["output"]
    ) / 1_000_000.0
    return max(0.0, cost)


class TaskBenchmarkReceipt(BaseModel):
    """Tamper-evident telemetry receipt for a single benchmark task execution."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    task_sha256: str = Field(min_length=1)
    mode: Literal["baseline", "quadgate"]
    suite: Literal["coding", "knowledge"]
    uncached_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    generative_cost_usd: float = Field(default=0.0, ge=0.0)
    classifier_cost_usd: float = Field(default=0.0, ge=0.0)
    retry_tax_usd: float = Field(default=0.0, ge=0.0)
    retry_tax_ms: float = Field(default=0.0, ge=0.0)
    wall_clock_ms: float = Field(default=0.0, ge=0.0)
    passed: bool
    category: str = ""
    run_index: int = Field(default=1, ge=1)
    hook_ipc_ms: float = Field(default=0.0, ge=0.0)
    classifier_latency_ms: float = Field(default=0.0, ge=0.0)
    promoted: bool = False
    receipt_sha256: str = ""

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens billed across uncached, cache-read, and cache-creation buckets."""
        return (
            self.uncached_input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def total_cost_usd(self) -> float:
        """Total USD spend (`generative_cost_usd`, which includes `retry_tax_usd`, + classifier)."""
        return self.generative_cost_usd + self.classifier_cost_usd

    @property
    def warm_cache_hit_rate(self) -> float:
        """Fraction of total input tokens served from KV prefix cache in `[0.0, 1.0]`."""
        total = self.total_input_tokens
        if total <= 0:
            return 0.0
        return self.cache_read_input_tokens / float(total)


def compute_task_sha256(task: Mapping[str, Any]) -> str:
    """Compute canonical SHA-256 over the immutable task definition fields."""
    canonical = {
        "task_id": task.get("task_id", ""),
        "suite": task.get("suite", ""),
        "category": task.get("category", ""),
        "prompt": task.get("prompt", ""),
        "files": task.get("files", {}),
        "solution_files": task.get("solution_files", {}),
        "ground_truth": task.get("ground_truth"),
        "pytest_target": task.get("pytest_target"),
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_receipt_sha256(receipt: TaskBenchmarkReceipt) -> str:
    """Compute deterministic SHA-256 over all canonical receipt telemetry fields."""
    canonical = {
        "task_id": receipt.task_id,
        "task_sha256": receipt.task_sha256,
        "mode": receipt.mode,
        "suite": receipt.suite,
        "category": receipt.category,
        "run_index": receipt.run_index,
        "uncached_input_tokens": receipt.uncached_input_tokens,
        "cache_read_input_tokens": receipt.cache_read_input_tokens,
        "cache_creation_input_tokens": receipt.cache_creation_input_tokens,
        "output_tokens": receipt.output_tokens,
        "generative_cost_usd": round(receipt.generative_cost_usd, 10),
        "classifier_cost_usd": round(receipt.classifier_cost_usd, 10),
        "retry_tax_usd": round(receipt.retry_tax_usd, 10),
        "retry_tax_ms": round(receipt.retry_tax_ms, 6),
        "hook_ipc_ms": round(receipt.hook_ipc_ms, 6),
        "classifier_latency_ms": round(receipt.classifier_latency_ms, 6),
        "wall_clock_ms": round(receipt.wall_clock_ms, 6),
        "promoted": receipt.promoted,
        "passed": receipt.passed,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_receipt_sha256(receipt: TaskBenchmarkReceipt) -> bool:
    """Verify that `receipt.receipt_sha256` matches the canonical telemetry digest."""
    return bool(receipt.receipt_sha256) and receipt.receipt_sha256 == compute_receipt_sha256(receipt)


def _resolve_sandbox_path(sandbox_dir: Path, rel_path: str) -> Path:
    """Resolve `rel_path` strictly inside `sandbox_dir`, raising ValueError on escape."""
    cleaned = rel_path.replace("\\", "/").lstrip("/")
    if not cleaned:
        raise ValueError("Empty sandbox file path.")
    base = sandbox_dir.resolve()
    target = (base / cleaned).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ValueError(f"Sandbox path escape rejected: {rel_path!r}")
    return target


def _write_sandbox_files(sandbox_dir: Path, files: Mapping[str, str]) -> None:
    """Write relative file mapping safely into `sandbox_dir`."""
    for rel_path, content in files.items():
        target = _resolve_sandbox_path(sandbox_dir, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _infer_knowledge_cartridge(task: Mapping[str, Any]) -> str:
    explicit = str(task.get("cartridge") or "").strip()
    if explicit:
        return explicit
    category = str(task.get("category") or "").strip().lower()
    mapping = {
        "multi_doc_audit": "knowledge_vault",
        "financial_reconciliation": "financial_ops",
        "policy_compliance": "legal_redline",
    }
    return mapping.get(category, "")


def _collect_sandbox_targets(sandbox_dir: Path) -> tuple[set[str], set[str]]:
    rel_paths: set[str] = set()
    stems: set[str] = set()
    if not sandbox_dir.is_dir():
        return rel_paths, stems
    for root, dirs, files in os.walk(sandbox_dir):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_SANDBOX_DIRS]
        for fname in files:
            fpath = Path(root) / fname
            try:
                rel = fpath.relative_to(sandbox_dir).as_posix().lower()
            except ValueError:
                continue
            rel_paths.add(rel)
            stems.add(fpath.stem.lower())
    return rel_paths, stems


def _parse_numeric_token(raw: str) -> float | None:
    s = raw.strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    elif s.startswith("("):
        negative = True
        s = s[1:].strip()
    s = s.replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def _normalize_key_id(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _extract_json_key_numbers(obj: Any, target_key: str) -> list[float]:
    target_norm = _normalize_key_id(target_key)
    found: list[float] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                k_str = str(k)
                if k_str == target_key or k_str.lower() == target_key.lower() or (
                    target_norm and _normalize_key_id(k_str) == target_norm
                ):
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        found.append(float(v))
                    elif isinstance(v, str):
                        parsed = _parse_numeric_token(v)
                        if parsed is not None:
                            found.append(parsed)
                if isinstance(v, str) and (
                    v == target_key or v.lower() == target_key.lower() or (
                        target_norm and _normalize_key_id(v) == target_norm
                    )
                ):
                    for sibling_k, sibling_v in node.items():
                        if sibling_k == k:
                            continue
                        if isinstance(sibling_v, (int, float)) and not isinstance(sibling_v, bool):
                            found.append(float(sibling_v))
                        elif isinstance(sibling_v, str):
                            parsed = _parse_numeric_token(sibling_v)
                            if parsed is not None:
                                found.append(parsed)
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return found


def _verify_numeric_balance_in_text(
    content: str,
    ext: str,
    key: str,
    expected_val: float,
) -> bool:
    """Verify `(key, expected_val)` in JSON, CSV, or Markdown within `0.01`."""
    if ext == ".json" or content.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(content)
            candidates = _extract_json_key_numbers(parsed, key)
            if any(abs(c - expected_val) <= 0.01 for c in candidates):
                return True
            if ext == ".json":
                return False
        except json.JSONDecodeError:
            if ext == ".json":
                return False

    if ext in {".csv", ".tsv"}:
        delim = "\t" if ext == ".tsv" else ","
        rows = list(csv.reader(io.StringIO(content.strip()), delimiter=delim))
        if rows:
            headers = [_normalize_key_id(h) for h in rows[0]]
            key_norm = _normalize_key_id(key)
            if key_norm and key_norm in headers:
                col_idx = headers.index(key_norm)
                for row in rows[1:]:
                    if col_idx < len(row):
                        num = _parse_numeric_token(row[col_idx])
                        if num is not None and abs(num - expected_val) <= 0.01:
                            return True

    # Markdown / text: look for key pattern (`key`, `key_name`, `key name`, `key-name`)
    parts = [re.escape(p) for p in re.split(r"[_\s-]+", key.strip()) if p]
    if not parts:
        return False
    key_pattern = re.compile(r"(?<![A-Za-z0-9_])" + r"[\s_-]+".join(parts) + r"(?![A-Za-z0-9_])", re.I)

    for match in key_pattern.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_end = content.find("\n", match.end())
        if line_end == -1:
            line_end = len(content)
        after_on_line = content[match.end() : line_end]
        window_after = content[match.end() : min(len(content), match.end() + 96)]
        for span in (after_on_line, window_after):
            for tok_match in _NUMERIC_TOKEN_RE.finditer(span):
                num = _parse_numeric_token(tok_match.group(0))
                if num is not None and abs(num - expected_val) <= 0.01:
                    return True
        # Also check full line (e.g. `| $1,234.50 | net_balance |`)
        full_line = content[line_start:line_end]
        for tok_match in _NUMERIC_TOKEN_RE.finditer(full_line):
            num = _parse_numeric_token(tok_match.group(0))
            if num is not None and abs(num - expected_val) <= 0.01:
                return True

    return False


def verify_knowledge_ground_truth(
    sandbox_dir: Path,
    task: Mapping[str, Any],
) -> tuple[bool, str]:
    """Deterministically verify knowledge task output against ground truth & Cartridge oracles."""
    output_file = str(task.get("output_file") or "").strip()
    if not output_file:
        touched = task.get("expected_touched_files")
        if isinstance(touched, list) and touched:
            output_file = str(touched[0]).strip()
    if not output_file:
        return False, "Task specifies no output_file."

    try:
        target_path = _resolve_sandbox_path(sandbox_dir, output_file)
    except ValueError as exc:
        return False, str(exc)

    if not target_path.is_file():
        return False, f"Missing output file: {output_file!r}"

    content = target_path.read_text(encoding="utf-8", errors="replace")
    content_lower = content.lower()
    gt_raw = task.get("ground_truth")
    gt: Mapping[str, Any] = gt_raw if isinstance(gt_raw, Mapping) else {}

    # 1. Required citations & [[wikilink]] existence inside sandbox_dir
    rel_paths, stems = _collect_sandbox_targets(sandbox_dir)
    for citation in gt.get("required_citations") or []:
        cit_str = str(citation).strip()
        if not cit_str:
            continue
        if cit_str not in content and cit_str.lower() not in content_lower:
            return False, f"Missing required citation: {cit_str!r}"

    stripped_content = _MD_FENCE_RE.sub("", content)
    for wiki_match in _WIKILINK_RE.finditer(stripped_content):
        raw_target = wiki_match.group(1).strip().replace("\\", "/").lstrip("./")
        low = raw_target.lower()
        target_stem = Path(low).stem
        if not (
            low in rel_paths
            or f"{low}.md" in rel_paths
            or low in stems
            or target_stem in stems
        ):
            return False, f"Broken wikilink [[{raw_target}]] in {output_file!r}"

    # 2. Numeric balances verification (within 0.01 tolerance)
    ext = target_path.suffix.lower()
    balances = gt.get("expected_numeric_balances") or {}
    if isinstance(balances, Mapping):
        for key, expected_raw in balances.items():
            try:
                expected_val = float(expected_raw)
            except (TypeError, ValueError):
                return False, f"Invalid expected numeric balance for {key!r}: {expected_raw!r}"
            if not _verify_numeric_balance_in_text(content, ext, str(key), expected_val):
                return (
                    False,
                    f"Missing or mismatched numeric balance for {key!r} (expected {expected_val})",
                )

    # 3. Forbidden leaks (case-insensitive secret/PII safety)
    for leak in gt.get("forbidden_leaks") or []:
        leak_str = str(leak)
        if not leak_str:
            continue
        if leak_str in content or leak_str.lower() in content_lower:
            return False, f"Forbidden leak detected in {output_file!r}: {leak_str!r}"

    # 4. Required clauses (case-insensitive)
    for clause in gt.get("required_clauses") or []:
        clause_str = str(clause).strip()
        if clause_str and clause_str.lower() not in content_lower:
            return False, f"Missing required clause: {clause_str!r}"

    # 5. Cartridge Gate 4 built-in truth oracles
    cartridge_name = _infer_knowledge_cartridge(task)
    if cartridge_name:
        cartridge = load_cartridge(cartridge_name)
        oracle_engine = GateEngine(cartridge=cartridge)
        req_clauses = gt.get("required_clauses")
        oracle_event = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="knowledge-verify",
            turn_index=1,
            workspace_root=str(sandbox_dir),
            harness=HarnessType.GENERIC,
            touched_files=[output_file],
            metadata={
                "required_clauses": list(req_clauses) if isinstance(req_clauses, list) else []
            },
        )
        artifacts = [(output_file, content)]
        for check_name in cartridge.truth_oracle.checks:
            err = oracle_engine._run_builtin_check(check_name, oracle_event, artifacts)
            if err is not None:
                return False, err

    return True, ""


class _PytestWorkerPool:
    """Pool of persistent isolated Python worker subprocesses running `pytest.main`."""

    def __init__(self, size: int = 4) -> None:
        self._size = max(1, size)
        self._queue: asyncio.Queue[asyncio.subprocess.Process] = asyncio.Queue()
        self._workers: list[asyncio.subprocess.Process] = []
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None = None

    async def __aenter__(self) -> _PytestWorkerPool:
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="qg-pytest-pool-")
        env = os.environ.copy()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        for _ in range(self._size):
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _PYTEST_WORKER_CODE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._workers.append(proc)
            self._queue.put_nowait(proc)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        for proc in self._workers:
            try:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (ProcessLookupError, asyncio.TimeoutError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        self._workers.clear()
        if self._tmp_dir is not None:
            self._tmp_dir.cleanup()
            self._tmp_dir = None

    async def run_pytest(
        self,
        sandbox_dir: Path,
        target: str,
        *,
        timeout_s: float = 30.0,
    ) -> tuple[bool, str]:
        if self._tmp_dir is None:
            raise RuntimeError("_PytestWorkerPool must be entered via async with.")
        proc = await self._queue.get()
        result_file = Path(self._tmp_dir.name) / f"res_{uuid.uuid4().hex}.json"
        recreate = False
        try:
            if proc.returncode is not None or proc.stdin is None or proc.stdout is None:
                recreate = True
                return await _run_pytest_subprocess(sandbox_dir, target, timeout_s=timeout_s)

            payload = json.dumps(
                {
                    "sandbox": str(sandbox_dir),
                    "target": target,
                    "result_file": str(result_file),
                }
            )
            proc.stdin.write((payload + "\n").encode("utf-8"))
            await proc.stdin.drain()
            ack = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout_s)
            if not ack:
                recreate = True
                return await _run_pytest_subprocess(sandbox_dir, target, timeout_s=timeout_s)
            raw = result_file.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            rc = int(parsed.get("rc", 1))
            out = str(parsed.get("out", "")).strip()
            return rc == 0, out
        except (asyncio.TimeoutError, OSError, json.JSONDecodeError):
            recreate = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return await _run_pytest_subprocess(sandbox_dir, target, timeout_s=timeout_s)
        finally:
            result_file.unlink(missing_ok=True)
            if recreate:
                env = os.environ.copy()
                env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                replacement = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    _PYTEST_WORKER_CODE,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                self._workers.append(replacement)
                self._queue.put_nowait(replacement)
            else:
                self._queue.put_nowait(proc)


async def _run_pytest_subprocess(
    sandbox_dir: Path,
    target: str,
    *,
    timeout_s: float = 30.0,
    disable_autoload: bool = True,
) -> tuple[bool, str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{sandbox_dir}{os.pathsep}{existing_pp}" if existing_pp else str(sandbox_dir)
    if disable_autoload:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "--no-header",
        "--no-summary",
        "-p",
        "no:cacheprovider",
        "--noconftest",
        "-q",
        target,
        cwd=str(sandbox_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return False, f"pytest timed out after {timeout_s:.1f}s on {target}"

    out = (
        stdout_b.decode("utf-8", errors="replace").strip()
        or stderr_b.decode("utf-8", errors="replace").strip()
    )
    return proc.returncode == 0, out


async def verify_coding_ground_truth(
    sandbox_dir: Path,
    task: Mapping[str, Any],
    *,
    worker_pool: _PytestWorkerPool | None = None,
    semaphore: asyncio.Semaphore | None = None,
    timeout_s: float = 30.0,
) -> tuple[bool, str]:
    """Verify a coding task by executing `pytest` inside `sandbox_dir`."""
    target = str(task.get("pytest_target") or "").strip()
    if not target:
        discovered = sorted(sandbox_dir.rglob("test_*.py"))
        if not discovered:
            return False, "No pytest_target specified and no test_*.py found in sandbox."
        target = discovered[0].relative_to(sandbox_dir).as_posix()

    try:
        target_path = _resolve_sandbox_path(sandbox_dir, target)
    except ValueError as exc:
        return False, str(exc)

    if not target_path.exists():
        return False, f"Missing pytest target: {target!r}"

    target_text = ""
    if target_path.is_file():
        target_text = target_path.read_text(encoding="utf-8", errors="replace")
    uses_async = "async def test_" in target_text

    if worker_pool is not None and not uses_async:
        return await worker_pool.run_pytest(sandbox_dir, target, timeout_s=timeout_s)

    if semaphore is not None:
        async with semaphore:
            return await _run_pytest_subprocess(
                sandbox_dir,
                target,
                timeout_s=timeout_s,
                disable_autoload=not uses_async,
            )
    return await _run_pytest_subprocess(
        sandbox_dir,
        target,
        timeout_s=timeout_s,
        disable_autoload=not uses_async,
    )


class _GitWorktreeManager:
    """Manages a per-run base git repository and isolated `git worktree` sandboxes."""

    def __init__(self, max_git_concurrency: int = 4) -> None:
        self._tmp_root: tempfile.TemporaryDirectory[str] | None = None
        self._base_repo: Path | None = None
        self._worktrees_dir: Path | None = None
        self.available: bool = False
        self._sem = asyncio.Semaphore(max(1, max_git_concurrency))

    async def __aenter__(self) -> _GitWorktreeManager:
        self._tmp_root = tempfile.TemporaryDirectory(prefix="qg-bench-git-")
        root = Path(self._tmp_root.name)
        base_repo = root / "base_repo"
        worktrees_dir = root / "worktrees"
        base_repo.mkdir(parents=True, exist_ok=True)
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._base_repo = base_repo
        self._worktrees_dir = worktrees_dir

        try:
            (base_repo / ".quadgate_seed").write_text("seed\n", encoding="utf-8")
            for args in (
                ["init"],
                ["add", ".quadgate_seed"],
                [
                    "-c",
                    "user.email=bench@quadgate.dev",
                    "-c",
                    "user.name=QuadGate Benchmark",
                    "commit",
                    "-m",
                    "Seed commit",
                ],
            ):
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    *args,
                    cwd=str(base_repo),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode != 0:
                    self.available = False
                    return self
            self.available = True
        except OSError:
            self.available = False
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.available and self._base_repo is not None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "worktree",
                    "prune",
                    cwd=str(self._base_repo),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            except OSError:
                pass
        if self._tmp_root is not None:
            self._tmp_root.cleanup()
            self._tmp_root = None

    @asynccontextmanager
    async def sandbox(self, label: str) -> AsyncIterator[tuple[Path, bool]]:
        """Yield `(sandbox_dir, is_git_worktree)` and force-remove the worktree on exit."""
        if self._worktrees_dir is None:
            raise RuntimeError("_GitWorktreeManager must be entered via async with.")

        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
        wt_dir = self._worktrees_dir / f"{safe_label}_{uuid.uuid4().hex[:8]}"
        created_worktree = False

        if self.available and self._base_repo is not None:
            async with self._sem:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(wt_dir),
                    cwd=str(self._base_repo),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                created_worktree = proc.returncode == 0 and wt_dir.is_dir()

        if not created_worktree:
            wt_dir.mkdir(parents=True, exist_ok=True)

        try:
            yield wt_dir, created_worktree
        finally:
            if created_worktree and self._base_repo is not None:
                async with self._sem:
                    proc = await asyncio.create_subprocess_exec(
                        "git",
                        "worktree",
                        "remove",
                        "--force",
                        str(wt_dir),
                        cwd=str(self._base_repo),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
            shutil.rmtree(wt_dir, ignore_errors=True)


async def _commit_worktree_files(wt_dir: Path) -> None:
    """Commit initial task files on detached worktree HEAD so Gate 4 revert restores them."""
    for args in (
        ["add", "-A"],
        [
            "-c",
            "user.email=bench@quadgate.dev",
            "-c",
            "user.name=QuadGate Benchmark",
            "commit",
            "-m",
            "Task initial state",
        ],
    ):
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(wt_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()


async def _exercise_quadgate_pipeline(
    sandbox_dir: Path,
    task: Mapping[str, Any],
    candidate_files: Mapping[str, str],
    *,
    is_coding: bool,
    is_git_worktree: bool = False,
    worker_pool: _PytestWorkerPool | None = None,
    pytest_sem: asyncio.Semaphore | None = None,
) -> tuple[bool, float, float, list[str]]:
    """Run real Gate 1-4 + Pass-0 + SelfHealingEngine on the task sandbox."""
    cart_name = "software_engineering" if is_coding else _infer_knowledge_cartridge(task)
    if not cart_name:
        cart_name = "knowledge_vault"
    cartridge = load_cartridge(cart_name)

    t_ipc_start = time.perf_counter()
    indexer = AstGistIndexer(sandbox_dir, cartridge=cartridge)
    gists = indexer.index_workspace(incremental=False)
    fts_store = SqliteFtsStore(":memory:")
    fts_store.initialize()
    fts_store.upsert_gists(gists)
    healing = SelfHealingEngine(
        db_path=":memory:",
        workspace_root=sandbox_dir,
        tiers=TierModelConfig(frontier="pro", mid="flash", floor="flash_lite"),
    )
    engine = GateEngine(
        cartridge=cartridge,
        fts_store=fts_store,
        indexer=indexer,
        healing=healing,
    )
    classifier_ms = 0.0
    promoted = False
    tail_hints: list[str] = []
    session_id = f"bench-{task.get('task_id', 'task')}-{uuid.uuid4().hex[:6]}"
    prompt_text = str(task.get("prompt") or "")
    gt_raw = task.get("ground_truth")
    gt: Mapping[str, Any] = gt_raw if isinstance(gt_raw, Mapping) else {}
    req_clauses = gt.get("required_clauses")
    gate4_meta = {"required_clauses": list(req_clauses)} if isinstance(req_clauses, list) else {}

    try:
        # Gate 1: Turn-1 Intake & Context Sieve (plus Tier-1 air-gap detection)
        ev1 = GateEvent(
            gate=GateType.INTAKE,
            session_id=session_id,
            turn_index=1,
            workspace_root=str(sandbox_dir),
            harness=HarnessType.GENERIC,
            prompt_text=prompt_text or "Execute benchmark task",
        )
        v1 = await engine.evaluate_gate1_intake(ev1)
        classifier_ms += v1.latency_ms
        tail_hints = list(v1.tail_hints)

        # Gate 3: Cold-boundary subagent tier governor on subagent chores
        if bool(task.get("subagent_chore")):
            targets = list(task.get("expected_touched_files") or candidate_files.keys())
            ev3 = GateEvent(
                gate=GateType.SUBAGENT_GOVERNOR,
                session_id=session_id,
                turn_index=1,
                workspace_root=str(sandbox_dir),
                harness=HarnessType.GENERIC,
                tool_name="invoke_subagent",
                prompt_text=prompt_text,
                touched_files=[str(t) for t in targets],
                tool_args={"brief": prompt_text, "files": [str(t) for t in targets]},
            )
            v3 = await engine.evaluate_gate3_subagent(ev3)
            classifier_ms += v3.latency_ms

        # 1-strike promotion + revert simulation when flawed_draft_files are provided
        flawed_raw = task.get("flawed_draft_files")
        flawed_files: Mapping[str, str] = (
            flawed_raw if isinstance(flawed_raw, Mapping) else {}
        )
        if bool(task.get("simulates_one_strike_promotion")) and flawed_files:
            if is_coding and is_git_worktree:
                await _commit_worktree_files(sandbox_dir)
            _write_sandbox_files(sandbox_dir, flawed_files)
            flawed_paths = list(flawed_files.keys())
            flawed_diff = "\n".join(flawed_files.values())
            ev4_flawed = GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id=session_id,
                turn_index=2,
                workspace_root=str(sandbox_dir),
                harness=HarnessType.GENERIC,
                prompt_text=prompt_text,
                touched_files=flawed_paths,
                diff_text=flawed_diff,
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                metadata=gate4_meta,
            )
            v4_flawed = await engine.evaluate_gate4_stop(ev4_flawed)
            classifier_ms += v4_flawed.latency_ms

            if v4_flawed.action != VerdictAction.PROMOTE:
                if is_coding:
                    ok_flawed, err_flawed = await verify_coding_ground_truth(
                        sandbox_dir,
                        task,
                        worker_pool=worker_pool,
                        semaphore=pytest_sem,
                    )
                else:
                    ok_flawed, err_flawed = verify_knowledge_ground_truth(sandbox_dir, task)
                if not ok_flawed:
                    v4_flawed = await healing.handle_gate4_failure(
                        ev4_flawed,
                        err_flawed or "Floor draft failed ground truth.",
                    )
                    classifier_ms += v4_flawed.latency_ms

            promoted = v4_flawed.action == VerdictAction.PROMOTE

            # Restore clean initial files (for any untracked/non-git flawed artifacts)
            initial_files = task.get("files") or {}
            for rel_path in flawed_files:
                if rel_path not in initial_files:
                    try:
                        _resolve_sandbox_path(sandbox_dir, rel_path).unlink(missing_ok=True)
                    except ValueError:
                        pass
            if isinstance(initial_files, Mapping):
                _write_sandbox_files(sandbox_dir, initial_files)

        # Gate 2: Pre-mutation firewall over candidate edits
        for rel_path, new_content in candidate_files.items():
            ev2 = GateEvent(
                gate=GateType.FIREWALL,
                session_id=session_id,
                turn_index=2,
                workspace_root=str(sandbox_dir),
                harness=HarnessType.GENERIC,
                tool_name="write_to_file",
                tool_args={"TargetFile": rel_path, "CodeContent": new_content},
                touched_files=[rel_path],
                diff_text=new_content,
                prompt_text=prompt_text,
            )
            v2 = await engine.evaluate_gate2_firewall(ev2)
            classifier_ms += v2.latency_ms

        # Apply candidate files & run final Gate 4 stop audit
        _write_sandbox_files(sandbox_dir, candidate_files)
        cand_paths = list(candidate_files.keys())
        ev4_final = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id=session_id,
            turn_index=3 if promoted else 2,
            workspace_root=str(sandbox_dir),
            harness=HarnessType.GENERIC,
            prompt_text=prompt_text,
            touched_files=cand_paths,
            diff_text="\n".join(candidate_files.values()),
            current_tier=ModelTier.MID if promoted else ModelTier.FRONTIER,
            strike_count=1 if promoted else 0,
            continuation_active=promoted,
            metadata=gate4_meta,
        )
        v4_final = await engine.evaluate_gate4_stop(ev4_final)
        classifier_ms += v4_final.latency_ms
    finally:
        healing.close()
        fts_store.close()

    total_gate_ms = (time.perf_counter() - t_ipc_start) * 1000.0
    hook_ipc_ms = max(0.0, total_gate_ms - classifier_ms)
    return promoted, hook_ipc_ms, classifier_ms, tail_hints


def _aggregate_trace_metrics(
    trace: Mapping[str, Any],
    *,
    measured_hook_ipc_ms: float = 0.0,
    measured_classifier_ms: float = 0.0,
    is_live: bool = False,
) -> dict[str, Any]:
    """Aggregate honest token counts, costs, retry tax, and timings from trace turns."""
    default_model = str(trace.get("model") or "claude-sonnet-4-5")
    turns = trace.get("turns") or []
    retry_turns = trace.get("retry_turns") or []

    uncached_in = 0
    cache_read_in = 0
    cache_write_in = 0
    out_tok = 0
    base_gen_cost = 0.0
    base_turns_ms = 0.0

    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        usage = turn.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        u, cr, cw, o = extract_provider_usage(usage_map)
        t_model = str(turn.get("model") or default_model)
        uncached_in += u
        cache_read_in += cr
        cache_write_in += cw
        out_tok += o
        base_gen_cost += compute_turn_cost_usd(t_model, u, cr, cw, o)
        base_turns_ms += _safe_float(turn.get("latency_ms"))

    retry_tax_usd = 0.0
    retry_tax_ms = 0.0
    for turn in retry_turns:
        if not isinstance(turn, Mapping):
            continue
        usage = turn.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        u, cr, cw, o = extract_provider_usage(usage_map)
        t_model = str(turn.get("model") or default_model)
        uncached_in += u
        cache_read_in += cr
        cache_write_in += cw
        out_tok += o
        t_cost = compute_turn_cost_usd(t_model, u, cr, cw, o)
        retry_tax_usd += t_cost
        retry_tax_ms += _safe_float(turn.get("latency_ms"))

    generative_cost_usd = base_gen_cost + retry_tax_usd
    classifier_cost_usd = _safe_float(trace.get("classifier_cost_usd"))

    has_replay_hook = "hook_ipc_ms" in trace and trace.get("hook_ipc_ms") is not None
    has_replay_cls = (
        "classifier_latency_ms" in trace and trace.get("classifier_latency_ms") is not None
    )

    if is_live:
        hook_ipc_ms = _safe_float(trace.get("hook_ipc_ms")) if has_replay_hook else measured_hook_ipc_ms
        classifier_latency_ms = (
            _safe_float(trace.get("classifier_latency_ms"))
            if has_replay_cls
            else measured_classifier_ms
        )
    else:
        hook_ipc_ms = (
            _safe_float(trace.get("hook_ipc_ms"))
            if has_replay_hook and _safe_float(trace.get("hook_ipc_ms")) > 0.0
            else measured_hook_ipc_ms
        )
        classifier_latency_ms = (
            _safe_float(trace.get("classifier_latency_ms"))
            if has_replay_cls and _safe_float(trace.get("classifier_latency_ms")) > 0.0
            else measured_classifier_ms
        )

    raw_wall_ms = _safe_float(trace.get("wall_clock_ms"))
    overhead_added = (
        (0.0 if has_replay_hook else hook_ipc_ms)
        + (0.0 if has_replay_cls else classifier_latency_ms)
    )
    computed_sum_ms = base_turns_ms + retry_tax_ms + hook_ipc_ms + classifier_latency_ms
    if raw_wall_ms > 0.0:
        wall_clock_ms = max(raw_wall_ms + (overhead_added if is_live else 0.0), computed_sum_ms)
    else:
        wall_clock_ms = computed_sum_ms

    return {
        "uncached_input_tokens": uncached_in,
        "cache_read_input_tokens": cache_read_in,
        "cache_creation_input_tokens": cache_write_in,
        "output_tokens": out_tok,
        "generative_cost_usd": generative_cost_usd,
        "classifier_cost_usd": classifier_cost_usd,
        "retry_tax_usd": retry_tax_usd,
        "retry_tax_ms": retry_tax_ms,
        "hook_ipc_ms": hook_ipc_ms,
        "classifier_latency_ms": classifier_latency_ms,
        "wall_clock_ms": wall_clock_ms,
        "had_retry_turns": len(retry_turns) > 0,
    }


def _extract_json_files_payload(raw_text: str) -> dict[str, str]:
    """Extract `{"files": {"path": "content"}}` or `{"path": "content"}` from LLM output."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        return {}
    nested = parsed.get("files")
    if isinstance(nested, dict):
        return {str(k): str(v) for k, v in nested.items()}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}


def _pick_live_provider_and_model(
    task: Mapping[str, Any],
    mode: str,
) -> tuple[str, str, str]:
    """Select `(provider, model, api_key)` from available environment keys."""
    env_keys = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
        "gemini": os.environ.get("GEMINI_API_KEY", "").strip(),
    }
    default_models = {
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-4o",
        "gemini": "gemini-2.5-flash",
    }

    replay_trace = task.get("replay_trace")
    if isinstance(replay_trace, Mapping):
        mode_trace = replay_trace.get(mode)
        if isinstance(mode_trace, Mapping):
            hinted_provider = str(mode_trace.get("provider") or "").strip().lower()
            hinted_model = str(mode_trace.get("model") or "").strip()
            if hinted_provider in env_keys and env_keys[hinted_provider]:
                return (
                    hinted_provider,
                    hinted_model or default_models[hinted_provider],
                    env_keys[hinted_provider],
                )

    for prov in ("anthropic", "openai", "gemini"):
        if env_keys[prov]:
            return prov, default_models[prov], env_keys[prov]

    raise RuntimeError(
        "Live benchmark mode (--live) requires ANTHROPIC_API_KEY, OPENAI_API_KEY, "
        "GEMINI_API_KEY, or an injected live_runner."
    )


async def _call_live_provider_once(
    session: aiohttp.ClientSession,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    *,
    use_prompt_cache: bool,
) -> tuple[dict[str, str], dict[str, Any], float]:
    """Make one real HTTP request to Anthropic, OpenAI, or Gemini and return parsed files+usage."""
    t0 = time.perf_counter()
    candidate_files: dict[str, str] = {}
    usage: dict[str, Any] = {}

    if provider == "anthropic":
        sys_block: Any = (
            [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
            if use_prompt_cache
            else system_prompt
        )
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": sys_block,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        anth_base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        async with session.post(
            f"{anth_base}/v1/messages",
            headers=headers,
            json=payload,
        ) as resp:
            body = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"Anthropic API error HTTP {resp.status}: {body}")
            raw_usage = body.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
            content_blocks = body.get("content") or []
            text_parts = [
                str(b.get("text", ""))
                for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if text_parts:
                try:
                    candidate_files = _extract_json_files_payload("\n".join(text_parts))
                except json.JSONDecodeError:
                    candidate_files = {}

    elif provider == "openai":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        oai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        oai_url = (
            f"{oai_base}/chat/completions"
            if oai_base.endswith("/v1")
            else f"{oai_base}/v1/chat/completions"
        )
        async with session.post(
            oai_url,
            headers=headers,
            json=payload,
        ) as resp:
            body = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"OpenAI API error HTTP {resp.status}: {body}")
            raw_usage = body.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
            choices = body.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                raw_text = str(msg.get("content") or "")
                if raw_text:
                    try:
                        candidate_files = _extract_json_files_payload(raw_text)
                    except json.JSONDecodeError:
                        candidate_files = {}

    elif provider == "gemini":
        gem_base = os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        url = f"{gem_base}/v1beta/models/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        async with session.post(
            url,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
        ) as resp:
            body = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"Gemini API error HTTP {resp.status}: {body}")
            raw_usage = body.get("usageMetadata")
            if isinstance(raw_usage, dict):
                usage = raw_usage
            candidates = body.get("candidates") or []
            if candidates and isinstance(candidates[0], dict):
                parts = (candidates[0].get("content") or {}).get("parts") or []
                raw_text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                if raw_text:
                    try:
                        candidate_files = _extract_json_files_payload(raw_text)
                    except json.JSONDecodeError:
                        candidate_files = {}
    else:
        raise RuntimeError(f"Unsupported live provider: {provider!r}")

    latency_ms = (time.perf_counter() - t0) * 1000.0
    return candidate_files, usage, latency_ms


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _t_critical_95(df: int) -> float:
    if df <= 0:
        return 0.0
    if df in _T_CRITICAL_95:
        return _T_CRITICAL_95[df]
    # Cornish-Fisher approximation for t_{0.975, df} when df > 30 (accuracy < 2e-4)
    z = 1.959963984540054
    return z + (z**3 + z) / (4.0 * df) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * df * df)


def _compute_paired_ci(diffs: list[float], baseline_mean: float) -> dict[str, float]:
    n = len(diffs)
    if n == 0:
        return {
            "n": 0.0,
            "mean": 0.0,
            "half_width": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "pct_mean": 0.0,
            "pct_low": 0.0,
            "pct_high": 0.0,
        }
    mean_diff = sum(diffs) / float(n)
    if n == 1:
        half = 0.0
    else:
        var = sum((x - mean_diff) ** 2 for x in diffs) / float(n - 1)
        se = math.sqrt(var / float(n))
        half = _t_critical_95(n - 1) * se

    ci_low = mean_diff - half
    ci_high = mean_diff + half
    if baseline_mean > 0.0:
        pct_mean = (mean_diff / baseline_mean) * 100.0
        pct_low = (ci_low / baseline_mean) * 100.0
        pct_high = (ci_high / baseline_mean) * 100.0
    else:
        pct_mean = pct_low = pct_high = 0.0

    return {
        "n": float(n),
        "mean": mean_diff,
        "half_width": half,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "pct_mean": pct_mean,
        "pct_low": pct_low,
        "pct_high": pct_high,
    }


def _aggregate_mode_stats(receipts: list[TaskBenchmarkReceipt]) -> dict[str, float]:
    n = len(receipts)
    if n == 0:
        return {
            "n": 0.0,
            "pass_rate_pct": 0.0,
            "uncached_input": 0.0,
            "cache_read": 0.0,
            "cache_creation": 0.0,
            "total_input": 0.0,
            "output_tokens": 0.0,
            "warm_cache_hit_pct": 0.0,
            "generative_cost_usd": 0.0,
            "classifier_cost_usd": 0.0,
            "retry_tax_usd": 0.0,
            "retry_tax_ms": 0.0,
            "total_cost_usd": 0.0,
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "mean_ms": 0.0,
        }

    pass_count = sum(1 for r in receipts if r.passed)
    uncached = sum(r.uncached_input_tokens for r in receipts)
    cache_read = sum(r.cache_read_input_tokens for r in receipts)
    cache_creation = sum(r.cache_creation_input_tokens for r in receipts)
    total_input = sum(r.total_input_tokens for r in receipts)
    output_tokens = sum(r.output_tokens for r in receipts)
    warm_hit_pct = (cache_read / float(total_input) * 100.0) if total_input > 0 else 0.0

    gen_cost = sum(r.generative_cost_usd for r in receipts)
    cls_cost = sum(r.classifier_cost_usd for r in receipts)
    retry_usd = sum(r.retry_tax_usd for r in receipts)
    retry_ms = sum(r.retry_tax_ms for r in receipts)
    total_cost = sum(r.total_cost_usd for r in receipts)

    latencies = sorted(r.wall_clock_ms for r in receipts)
    return {
        "n": float(n),
        "pass_rate_pct": (pass_count / float(n)) * 100.0,
        "uncached_input": float(uncached),
        "cache_read": float(cache_read),
        "cache_creation": float(cache_creation),
        "total_input": float(total_input),
        "output_tokens": float(output_tokens),
        "warm_cache_hit_pct": warm_hit_pct,
        "generative_cost_usd": gen_cost,
        "classifier_cost_usd": cls_cost,
        "retry_tax_usd": retry_usd,
        "retry_tax_ms": retry_ms,
        "total_cost_usd": total_cost,
        "p50_ms": _percentile(latencies, 50.0),
        "p90_ms": _percentile(latencies, 90.0),
        "mean_ms": sum(latencies) / float(n),
    }


def _extract_matched_pairs(
    receipts: list[TaskBenchmarkReceipt],
) -> list[tuple[TaskBenchmarkReceipt, TaskBenchmarkReceipt]]:
    by_key: dict[tuple[str, str, int], dict[str, TaskBenchmarkReceipt]] = {}
    for rec in receipts:
        pair_key: tuple[str, str, int] = (rec.task_id, str(rec.suite), rec.run_index)
        by_key.setdefault(pair_key, {})[rec.mode] = rec
    pairs: list[tuple[TaskBenchmarkReceipt, TaskBenchmarkReceipt]] = []
    for pair_key in sorted(by_key.keys()):
        modes = by_key[pair_key]
        if "baseline" in modes and "quadgate" in modes:
            pairs.append((modes["baseline"], modes["quadgate"]))
    return pairs


def _fmt_usd(val: float) -> str:
    return f"`${val:.4f}`"


def _fmt_usd_signed(val: float) -> str:
    sign = "-" if val < 0 else ""
    return f"`{sign}${abs(val):.4f}`"


class BenchmarkRunner:
    """Executes paired baseline vs. quadgate benchmarks in isolated sandboxes."""

    def __init__(
        self,
        reports_dir: Path | None = None,
        suites_dir: Path | None = None,
        live_runner: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.reports_dir = Path(reports_dir) if reports_dir is not None else Path("benchmarks/reports")
        if suites_dir is not None:
            self.suites_dir = Path(suites_dir)
        elif reports_dir is None:
            self.suites_dir = _DEFAULT_SUITES_DIR
        else:
            parent_suites = self.reports_dir.parent / "suites"
            direct_suites = self.reports_dir / "suites"
            if parent_suites.is_dir():
                self.suites_dir = parent_suites
            elif direct_suites.is_dir():
                self.suites_dir = direct_suites
            else:
                self.suites_dir = _DEFAULT_SUITES_DIR
        self.live_runner = live_runner

    def _load_tasks(self, suite: Literal["coding", "knowledge", "all"]) -> list[dict[str, Any]]:
        selected_files: list[Path] = []
        if suite in ("coding", "all"):
            selected_files.append(self.suites_dir / "coding_tasks.jsonl")
        if suite in ("knowledge", "all"):
            selected_files.append(self.suites_dir / "knowledge_tasks.jsonl")

        tasks: list[dict[str, Any]] = []
        for path in selected_files:
            if not path.is_file():
                raise FileNotFoundError(f"Benchmark suite file not found: {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    tasks.append(obj)
        return tasks

    async def _run_live_http_task(
        self,
        task: dict[str, Any],
        mode: Literal["baseline", "quadgate"],
        sandbox_dir: Path,
        tail_hints: list[str],
    ) -> dict[str, Any]:
        provider, model, api_key = _pick_live_provider_and_model(task, mode)
        files = task.get("files") or {}
        files_blob = "\n\n".join(
            f"### {p}\n```\n{c}\n```" for p, c in files.items() if isinstance(p, str)
        )
        system_prompt = (
            "You are an expert software and knowledge engineer. "
            'Return ONLY valid JSON of the form {"files": {"relative/path": "full updated text"}}.'
        )
        if mode == "quadgate" and not tail_hints:
            cart_name = (
                "software_engineering"
                if str(task.get("suite")) == "coding"
                else (_infer_knowledge_cartridge(task) or "knowledge_vault")
            )
            cartridge = load_cartridge(cart_name)
            idx = AstGistIndexer(sandbox_dir, cartridge=cartridge)
            gists = idx.index_workspace(incremental=False)
            fts = SqliteFtsStore(":memory:")
            fts.initialize()
            fts.upsert_gists(gists)
            try:
                g_eng = GateEngine(cartridge=cartridge, fts_store=fts, indexer=idx)
                v1 = await g_eng.evaluate_gate1_intake(
                    GateEvent(
                        gate=GateType.INTAKE,
                        session_id=f"live-{task.get('task_id', 't')}",
                        turn_index=1,
                        workspace_root=str(sandbox_dir),
                        harness=HarnessType.GENERIC,
                        prompt_text=str(task.get("prompt") or "Execute task"),
                    )
                )
                tail_hints = list(v1.tail_hints)
            finally:
                fts.close()

        user_prompt = f"{task.get('prompt', '')}\n\nWorkspace files:\n{files_blob}"
        if mode == "quadgate" and tail_hints:
            user_prompt += f"\n\n[QUADGATE_CONTEXT_HINTS]: {', '.join(tail_hints)}"

        timeout = aiohttp.ClientTimeout(total=60.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            cand_files, usage, latency_ms = await _call_live_provider_once(
                session,
                provider,
                model,
                api_key,
                system_prompt,
                user_prompt,
                use_prompt_cache=(mode == "quadgate"),
            )
            turns = [{"turn_index": 1, "model": model, "latency_ms": latency_ms, "usage": usage}]
            return {
                "provider": provider,
                "model": model,
                "candidate_files": cand_files,
                "turns": turns,
                "retry_turns": [],
                "classifier_cost_usd": 0.0,
                "wall_clock_ms": latency_ms,
            }

    async def _execute_single_task_mode(
        self,
        task: dict[str, Any],
        mode: Literal["baseline", "quadgate"],
        run_index: int,
        *,
        dry_replay: bool,
        git_mgr: _GitWorktreeManager | None,
        worker_pool: _PytestWorkerPool | None,
        pytest_sem: asyncio.Semaphore,
    ) -> TaskBenchmarkReceipt:
        task_id = str(task["task_id"])
        suite_name: Literal["coding", "knowledge"] = (
            "coding" if str(task.get("suite")) == "coding" else "knowledge"
        )
        category = str(task.get("category") or "")
        task_sha256 = compute_task_sha256(task)
        is_coding = suite_name == "coding"

        if is_coding and git_mgr is not None:
            sandbox_ctx = git_mgr.sandbox(f"{task_id}_{mode}_r{run_index}")
        else:
            sandbox_ctx = _temp_sandbox_dir(f"{task_id}_{mode}_r{run_index}")

        async with sandbox_ctx as (sandbox_dir, is_git_wt):
            initial_files = task.get("files")
            if isinstance(initial_files, Mapping):
                _write_sandbox_files(sandbox_dir, initial_files)

            promoted = False
            hook_ipc_ms = 0.0
            classifier_ms = 0.0

            if dry_replay:
                solution_map = task.get("solution_files") or {}
                baseline_map = task.get("baseline_solution_files")
                if mode == "baseline" and isinstance(baseline_map, Mapping) and baseline_map:
                    candidate_files: dict[str, str] = {
                        str(k): str(v) for k, v in baseline_map.items()
                    }
                elif isinstance(solution_map, Mapping):
                    candidate_files = {str(k): str(v) for k, v in solution_map.items()}
                else:
                    candidate_files = {}

                replay_all = task.get("replay_trace")
                replay_mode = (
                    replay_all.get(mode) if isinstance(replay_all, Mapping) else None
                )
                trace_dict: dict[str, Any] = (
                    dict(replay_mode) if isinstance(replay_mode, Mapping) else {}
                )
            else:
                if self.live_runner is not None:
                    live_res = await self.live_runner(task, mode)
                    trace_dict = dict(live_res)
                    raw_cands = live_res.get("candidate_files")
                    if isinstance(raw_cands, Mapping):
                        candidate_files = {str(k): str(v) for k, v in raw_cands.items()}
                    else:
                        candidate_files = {}
                else:
                    live_res = await self._run_live_http_task(task, mode, sandbox_dir, [])
                    trace_dict = dict(live_res)
                    candidate_files = dict(live_res.get("candidate_files") or {})

            if mode == "quadgate":
                promoted, hook_ipc_ms, classifier_ms, _ = await _exercise_quadgate_pipeline(
                    sandbox_dir,
                    task,
                    candidate_files,
                    is_coding=is_coding,
                    is_git_worktree=is_git_wt,
                    worker_pool=worker_pool,
                    pytest_sem=pytest_sem,
                )
            else:
                _write_sandbox_files(sandbox_dir, candidate_files)

            if is_coding:
                passed, _ = await verify_coding_ground_truth(
                    sandbox_dir,
                    task,
                    worker_pool=worker_pool,
                    semaphore=pytest_sem,
                )
            else:
                passed, _ = verify_knowledge_ground_truth(sandbox_dir, task)

        metrics = _aggregate_trace_metrics(
            trace_dict,
            measured_hook_ipc_ms=hook_ipc_ms if mode == "quadgate" else 0.0,
            measured_classifier_ms=classifier_ms if mode == "quadgate" else 0.0,
            is_live=not dry_replay,
        )
        promoted_flag = (
            promoted
            or bool(metrics["had_retry_turns"])
            or metrics["retry_tax_usd"] > 0.0
            or metrics["retry_tax_ms"] > 0.0
        )

        receipt = TaskBenchmarkReceipt(
            task_id=task_id,
            task_sha256=task_sha256,
            mode=mode,
            suite=suite_name,
            category=category,
            run_index=run_index,
            uncached_input_tokens=int(metrics["uncached_input_tokens"]),
            cache_read_input_tokens=int(metrics["cache_read_input_tokens"]),
            cache_creation_input_tokens=int(metrics["cache_creation_input_tokens"]),
            output_tokens=int(metrics["output_tokens"]),
            generative_cost_usd=float(metrics["generative_cost_usd"]),
            classifier_cost_usd=float(metrics["classifier_cost_usd"]),
            retry_tax_usd=float(metrics["retry_tax_usd"]),
            retry_tax_ms=float(metrics["retry_tax_ms"]),
            hook_ipc_ms=float(metrics["hook_ipc_ms"]),
            classifier_latency_ms=float(metrics["classifier_latency_ms"]),
            wall_clock_ms=float(metrics["wall_clock_ms"]),
            promoted=promoted_flag,
            passed=passed,
        )
        return receipt.model_copy(update={"receipt_sha256": compute_receipt_sha256(receipt)})

    async def run_suite(
        self,
        suite: Literal["coding", "knowledge", "all"] = "all",
        runs: int = 1,
        dry_replay: bool = True,
        output_path: Path | None = None,
    ) -> list[TaskBenchmarkReceipt]:
        """Run the requested benchmark suite and return tamper-evident receipts."""
        if runs < 1:
            raise ValueError(f"runs must be >= 1, got {runs}")
        if suite not in ("coding", "knowledge", "all"):
            raise ValueError(f"Unsupported suite: {suite!r}")

        if not dry_replay and self.live_runner is None:
            has_key = any(
                bool(os.environ.get(k, "").strip())
                for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")
            )
            if not has_key:
                raise RuntimeError(
                    "Live benchmark mode (--live) requires ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                    "GEMINI_API_KEY, or an injected live_runner."
                )

        tasks = self._load_tasks(suite)
        coding_tasks = [t for t in tasks if t.get("suite") == "coding"]
        knowledge_tasks = [t for t in tasks if t.get("suite") == "knowledge"]
        pytest_sem = asyncio.Semaphore(4)
        receipts: list[TaskBenchmarkReceipt] = []

        modes: tuple[Literal["baseline", "quadgate"], ...] = ("baseline", "quadgate")

        async def _run_coding_batch() -> list[TaskBenchmarkReceipt]:
            if not coding_tasks:
                return []
            worker_count = min(4, max(1, len(coding_tasks) * runs))
            git_mgr = _GitWorktreeManager(max_git_concurrency=4)
            worker_pool = _PytestWorkerPool(size=worker_count)
            async with AsyncExitStack() as stack:
                await asyncio.gather(
                    stack.enter_async_context(git_mgr),
                    stack.enter_async_context(worker_pool),
                )
                coding_coros: list[Awaitable[TaskBenchmarkReceipt]] = []
                for run_idx in range(1, runs + 1):
                    for task in coding_tasks:
                        for mode_lit in modes:
                            coding_coros.append(
                                self._execute_single_task_mode(
                                    task,
                                    mode_lit,
                                    run_idx,
                                    dry_replay=dry_replay,
                                    git_mgr=git_mgr,
                                    worker_pool=worker_pool,
                                    pytest_sem=pytest_sem,
                                )
                            )
                return list(await asyncio.gather(*coding_coros))

        async def _run_knowledge_batch() -> list[TaskBenchmarkReceipt]:
            if not knowledge_tasks:
                return []
            kn_sem = asyncio.Semaphore(8)

            async def _bounded_knowledge(
                t: dict[str, Any],
                m: Literal["baseline", "quadgate"],
                r_idx: int,
            ) -> TaskBenchmarkReceipt:
                async with kn_sem:
                    return await self._execute_single_task_mode(
                        t,
                        m,
                        r_idx,
                        dry_replay=dry_replay,
                        git_mgr=None,
                        worker_pool=None,
                        pytest_sem=pytest_sem,
                    )

            kn_coros: list[Awaitable[TaskBenchmarkReceipt]] = []
            for run_idx in range(1, runs + 1):
                for task in knowledge_tasks:
                    for mode_lit_k in modes:
                        kn_coros.append(_bounded_knowledge(task, mode_lit_k, run_idx))
            return list(await asyncio.gather(*kn_coros))

        receipts.extend(await _run_coding_batch())
        receipts.extend(await _run_knowledge_batch())

        # Preserve deterministic ordering by (run_index, suite, task_id, mode)
        receipts.sort(key=lambda r: (r.run_index, r.suite, r.task_id, r.mode))

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        jsonl_path = self.reports_dir / f"run_{stamp}.jsonl"
        jsonl_lines = "".join(r.model_dump_json() + "\n" for r in receipts)
        jsonl_path.write_text(jsonl_lines, encoding="utf-8")

        report_path = output_path if output_path is not None else self.reports_dir / "LATEST_PROOF.md"
        self.generate_markdown_report(receipts, report_path)
        return receipts

    def generate_markdown_report(
        self,
        receipts: list[TaskBenchmarkReceipt],
        output_path: Path,
    ) -> Path:
        """Emit publication-ready Markdown proof scorecard from receipts."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        base_all = [r for r in receipts if r.mode == "baseline"]
        qg_all = [r for r in receipts if r.mode == "quadgate"]
        base_stats = _aggregate_mode_stats(base_all)
        qg_stats = _aggregate_mode_stats(qg_all)
        pairs = _extract_matched_pairs(receipts)

        n_pairs = len(pairs)
        token_diffs = [float(b.total_input_tokens - q.total_input_tokens) for b, q in pairs]
        cost_diffs = [b.total_cost_usd - q.total_cost_usd for b, q in pairs]
        lat_diffs = [b.wall_clock_ms - q.wall_clock_ms for b, q in pairs]

        base_mean_in = (
            sum(b.total_input_tokens for b, _ in pairs) / float(n_pairs) if n_pairs else 0.0
        )
        base_mean_cost = (
            sum(b.total_cost_usd for b, _ in pairs) / float(n_pairs) if n_pairs else 0.0
        )
        base_mean_ms = (
            sum(b.wall_clock_ms for b, _ in pairs) / float(n_pairs) if n_pairs else 0.0
        )

        tok_ci = _compute_paired_ci(token_diffs, base_mean_in)
        cost_ci = _compute_paired_ci(cost_diffs, base_mean_cost)
        lat_ci = _compute_paired_ci(lat_diffs, base_mean_ms)

        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        lines: list[str] = [
            "# QuadGate Empirical A/B Proof Scorecard",
            "",
            f"- **Generated (UTC):** `{generated_at}`",
            f"- **Total Receipts:** `{len(receipts)}` (`{n_pairs}` matched Baseline vs. QuadGate pairs)",
            "",
            "## Executive Summary (Aggregate Baseline vs. QuadGate)",
            "",
            "| Metric | Baseline | QuadGate | Delta |",
            "| :--- | ---: | ---: | ---: |",
            (
                f"| Runs (`n`) | {int(base_stats['n'])} | {int(qg_stats['n'])} | — |"
            ),
            (
                f"| Pass@1 (%) | {base_stats['pass_rate_pct']:.1f}% | "
                f"{qg_stats['pass_rate_pct']:.1f}% | "
                f"{qg_stats['pass_rate_pct'] - base_stats['pass_rate_pct']:+.1f} pp |"
            ),
            (
                f"| Uncached Input Tokens | {int(base_stats['uncached_input']):,} | "
                f"{int(qg_stats['uncached_input']):,} | "
                f"{int(qg_stats['uncached_input'] - base_stats['uncached_input']):+,} |"
            ),
            (
                f"| Cache Read Input Tokens | {int(base_stats['cache_read']):,} | "
                f"{int(qg_stats['cache_read']):,} | "
                f"{int(qg_stats['cache_read'] - base_stats['cache_read']):+,} |"
            ),
            (
                f"| Cache Creation Input Tokens | {int(base_stats['cache_creation']):,} | "
                f"{int(qg_stats['cache_creation']):,} | "
                f"{int(qg_stats['cache_creation'] - base_stats['cache_creation']):+,} |"
            ),
            (
                f"| Output Tokens | {int(base_stats['output_tokens']):,} | "
                f"{int(qg_stats['output_tokens']):,} | "
                f"{int(qg_stats['output_tokens'] - base_stats['output_tokens']):+,} |"
            ),
            (
                f"| Warm KV-Cache Hit Rate (%) | {base_stats['warm_cache_hit_pct']:.1f}% | "
                f"{qg_stats['warm_cache_hit_pct']:.1f}% | "
                f"{qg_stats['warm_cache_hit_pct'] - base_stats['warm_cache_hit_pct']:+.1f} pp |"
            ),
            (
                f"| Generative Cost (incl. Retry Tax) | "
                f"{_fmt_usd(base_stats['generative_cost_usd'])} | "
                f"{_fmt_usd(qg_stats['generative_cost_usd'])} | "
                f"{_fmt_usd_signed(qg_stats['generative_cost_usd'] - base_stats['generative_cost_usd'])} |"
            ),
            (
                f"| Classifier Cost | {_fmt_usd(base_stats['classifier_cost_usd'])} | "
                f"{_fmt_usd(qg_stats['classifier_cost_usd'])} | "
                f"{_fmt_usd_signed(qg_stats['classifier_cost_usd'] - base_stats['classifier_cost_usd'])} |"
            ),
            (
                f"| Retry Tax (USD / ms) | "
                f"{_fmt_usd(base_stats['retry_tax_usd'])} / {base_stats['retry_tax_ms']:.1f} ms | "
                f"{_fmt_usd(qg_stats['retry_tax_usd'])} / {qg_stats['retry_tax_ms']:.1f} ms | "
                f"{_fmt_usd_signed(qg_stats['retry_tax_usd'] - base_stats['retry_tax_usd'])} |"
            ),
            (
                f"| Total Cost | {_fmt_usd(base_stats['total_cost_usd'])} | "
                f"{_fmt_usd(qg_stats['total_cost_usd'])} | "
                f"{_fmt_usd_signed(qg_stats['total_cost_usd'] - base_stats['total_cost_usd'])} |"
            ),
            (
                f"| Wall-Clock Latency p50 / p90 / mean (ms) | "
                f"{base_stats['p50_ms']:.1f} / {base_stats['p90_ms']:.1f} / {base_stats['mean_ms']:.1f} | "
                f"{qg_stats['p50_ms']:.1f} / {qg_stats['p90_ms']:.1f} / {qg_stats['mean_ms']:.1f} | "
                f"{qg_stats['mean_ms'] - base_stats['mean_ms']:+.1f} ms mean |"
            ),
            "",
            "## Paired 95% Confidence Intervals (Baseline minus QuadGate)",
            "",
            "| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |",
            "| :--- | ---: | ---: | ---: | ---: |",
            (
                f"| Total Input Token Savings | {n_pairs} | {tok_ci['mean']:,.1f} tok | "
                f"[{tok_ci['ci_low']:,.1f}, {tok_ci['ci_high']:,.1f}] tok | "
                f"{tok_ci['pct_mean']:.1f}% ([{tok_ci['pct_low']:.1f}%, {tok_ci['pct_high']:.1f}%]) |"
            ),
            (
                f"| Total Cost Reduction | {n_pairs} | {_fmt_usd_signed(cost_ci['mean'])} | "
                f"[{_fmt_usd_signed(cost_ci['ci_low'])}, {_fmt_usd_signed(cost_ci['ci_high'])}] | "
                f"{cost_ci['pct_mean']:.1f}% ([{cost_ci['pct_low']:.1f}%, {cost_ci['pct_high']:.1f}%]) |"
            ),
            (
                f"| Wall-Clock Latency Reduction | {n_pairs} | {lat_ci['mean']:,.1f} ms | "
                f"[{lat_ci['ci_low']:,.1f}, {lat_ci['ci_high']:,.1f}] ms | "
                f"{lat_ci['pct_mean']:.1f}% ([{lat_ci['pct_low']:.1f}%, {lat_ci['pct_high']:.1f}%]) |"
            ),
            "",
            "## Per-Suite Breakdown",
            "",
        ]

        for suite_name in ("coding", "knowledge"):
            s_receipts = [r for r in receipts if r.suite == suite_name]
            if not s_receipts:
                continue
            s_base = _aggregate_mode_stats([r for r in s_receipts if r.mode == "baseline"])
            s_qg = _aggregate_mode_stats([r for r in s_receipts if r.mode == "quadgate"])
            s_pairs = _extract_matched_pairs(s_receipts)
            sn = len(s_pairs)
            s_tok_ci = _compute_paired_ci(
                [float(b.total_input_tokens - q.total_input_tokens) for b, q in s_pairs],
                sum(b.total_input_tokens for b, _ in s_pairs) / float(sn) if sn else 0.0,
            )
            s_cost_ci = _compute_paired_ci(
                [b.total_cost_usd - q.total_cost_usd for b, q in s_pairs],
                sum(b.total_cost_usd for b, _ in s_pairs) / float(sn) if sn else 0.0,
            )
            s_lat_ci = _compute_paired_ci(
                [b.wall_clock_ms - q.wall_clock_ms for b, q in s_pairs],
                sum(b.wall_clock_ms for b, _ in s_pairs) / float(sn) if sn else 0.0,
            )
            lines.extend(
                [
                    f"### Suite: `{suite_name}` (`{sn}` pairs)",
                    "",
                    "| Metric | Baseline | QuadGate | Paired 95% Reduction CI |",
                    "| :--- | ---: | ---: | ---: |",
                    (
                        f"| Pass@1 (%) | {s_base['pass_rate_pct']:.1f}% | "
                        f"{s_qg['pass_rate_pct']:.1f}% | "
                        f"{s_qg['pass_rate_pct'] - s_base['pass_rate_pct']:+.1f} pp |"
                    ),
                    (
                        f"| Warm KV-Cache Hit Rate (%) | {s_base['warm_cache_hit_pct']:.1f}% | "
                        f"{s_qg['warm_cache_hit_pct']:.1f}% | "
                        f"{s_tok_ci['pct_mean']:.1f}% input saved ([{s_tok_ci['pct_low']:.1f}%, {s_tok_ci['pct_high']:.1f}%]) |"
                    ),
                    (
                        f"| Total Cost | {_fmt_usd(s_base['total_cost_usd'])} | "
                        f"{_fmt_usd(s_qg['total_cost_usd'])} | "
                        f"{s_cost_ci['pct_mean']:.1f}% ([{s_cost_ci['pct_low']:.1f}%, {s_cost_ci['pct_high']:.1f}%]) |"
                    ),
                    (
                        f"| Wall-Clock p50 / p90 / mean (ms) | "
                        f"{s_base['p50_ms']:.1f} / {s_base['p90_ms']:.1f} / {s_base['mean_ms']:.1f} | "
                        f"{s_qg['p50_ms']:.1f} / {s_qg['p90_ms']:.1f} / {s_qg['mean_ms']:.1f} | "
                        f"{s_lat_ci['pct_mean']:.1f}% ([{s_lat_ci['pct_low']:.1f}%, {s_lat_ci['pct_high']:.1f}%]) |"
                    ),
                    "",
                ]
            )

        lines.extend(
            [
                "## Methodology & Honest Accounting",
                "",
                "- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.",
                "- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.",
                "- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.",
                "- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.",
                "",
                "## Tamper-Evident SHA-256 Receipt Manifest",
                "",
                "| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |",
                "| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |",
            ]
        )

        for r in receipts:
            lines.append(
                f"| `{r.task_id}` | `{r.suite}` | `{r.mode}` | {r.run_index} | "
                f"{'PASS' if r.passed else 'FAIL'} | {'yes' if r.promoted else 'no'} | "
                f"{_fmt_usd(r.total_cost_usd)} | {r.wall_clock_ms:.1f} | "
                f"`{r.task_sha256}` | `{r.receipt_sha256}` |"
            )

        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out


@asynccontextmanager
async def _temp_sandbox_dir(label: str) -> AsyncIterator[tuple[Path, bool]]:
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    with tempfile.TemporaryDirectory(prefix=f"qg-sb-{safe_label}-") as tmp:
        yield Path(tmp), False


__all__ = [
    "BenchmarkRunner",
    "MODEL_PRICING_PER_1M",
    "TaskBenchmarkReceipt",
    "compute_receipt_sha256",
    "compute_task_sha256",
    "compute_turn_cost_usd",
    "extract_provider_usage",
    "verify_coding_ground_truth",
    "verify_knowledge_ground_truth",
    "verify_receipt_sha256",
]
