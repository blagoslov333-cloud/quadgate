"""4-Gate algebraic engine, ECC governor, and warm prefix-cache guard."""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import inspect
import io
import json
import os
import py_compile
import re
import shutil
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from quadgate.contracts import (
    CartridgeSpec,
    ClassifierHead,
    ClassifierRequest,
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    HeadCategory,
    IndexedGist,
    InvariantSpec,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.drivers.base import ClassifierDriver
from quadgate.drivers.onnx_local import ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.cochange import CoChangeGraph
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.calibration import CalibrationEngine
from quadgate.shim import check_stage2a_deterministic_security

_CARTRIDGES_DIR = Path(__file__).resolve().parent.parent / "cartridges"
_SAFE_DOTENV_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})

_TIER_RUN_COST: dict[ModelTier, float] = {
    ModelTier.FLOOR: 0.10,
    ModelTier.MID: 0.30,
    ModelTier.FRONTIER: 1.00,
}
_FRONTIER_PROMOTE_COST = 1.00
_COCHANGE_BOOST_THRESHOLD = 0.40
_COCHANGE_BOOST_WEIGHT = 0.12
_ECC_SAVINGS_FACTOR = 0.75  # Require >= 25% ECC savings margin over FRONTIER.

_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "view_file",
        "view_file_outline",
        "view_code_item",
        "read",
        "grep_search",
        "grep",
        "find_by_name",
        "glob",
        "list_dir",
        "ls",
    }
)
_PATH_ARG_KEYS = (
    "Path",
    "TargetFile",
    "file_path",
    "path",
    "target_file",
    "AbsolutePath",
    "TargetPath",
    "dest",
    "destination",
)
_EDIT_CONTAINER_KEYS = ("edits", "ReplacementChunks", "changes", "files")
_DIFF_OR_CONTENT_KEYS = (
    "ReplacementContent",
    "new_string",
    "new_text",
    "CodeContent",
    "content",
    "replacement",
    "text",
    "CommandLine",
    "command",
    "cmd",
    "script",
)
_OLD_CONTENT_KEYS = ("TargetContent", "old_string", "old_text", "target")

_TOKEN_SPLIT_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_BOUNDARY_1_RE = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_BOUNDARY_2_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_PATH_MENTION_RE = re.compile(
    r"(?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}"
)
_DIFF_HUNK_LINE_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@", re.MULTILINE)
_MD_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_MD_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|[^\]\n]+)?\]\]")
_MD_LINK_RE = re.compile(r"""(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+"[^"]*")?\)""")
_CITATION_MARKER_RE = re.compile(
    r"(?:\[\^\w+\]|\[\d+\]|\bhttps?://\S+|\bdoi:\s*\S+|\bISBN\b|"
    r"\b(?:Source|Sources|Reference|References|Citation|Citations)\s*:)",
    re.IGNORECASE,
)
_CITATION_NEEDED_RE = re.compile(r"(?:\[|\{\{)\s*citation\s+needed\s*(?:\]|\}\})", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(
    r"(?:\[(?:TBD|TODO|INSERT(?:\s+[^\]]+)?|PLACEHOLDER)\])",
    re.IGNORECASE,
)
_CLAUSE_CONJUNCTION_SPLIT_RE = re.compile(
    r"(?:\s*,\s*|\s+)and(?:\s+then)?\s+"
    r"(?=(?:please\s+)?(?:add|build|check|configure|create|delete|document|edit|"
    r"ensure|fix|implement|include|migrate|modify|patch|refactor|remove|rename|"
    r"rewrite|run|test|update|validate|verify|write)\b)",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "please",
        "should",
        "that",
        "the",
        "their",
        "then",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)
_CLAUSE_FILLER_VERBS = frozenset(
    {
        "add",
        "also",
        "check",
        "create",
        "delete",
        "edit",
        "ensure",
        "fix",
        "implement",
        "include",
        "make",
        "modify",
        "patch",
        "remove",
        "rewrite",
        "run",
        "update",
        "verify",
        "write",
    }
)
_HIGH_COMPLEXITY_RE = re.compile(
    r"\b(?:architect(?:ure)?|distributed|consensus|concurrency|race\s+condition|"
    r"deadlock|cryptograph\w*|security\s+vulnerabilit\w*|zero[-\s]?downtime|"
    r"multi[-\s]?(?:service|region|module)|kernel|compiler|scheduler|migration)\b",
    re.IGNORECASE,
)
_LOW_COMPLEXITY_RE = re.compile(
    r"\b(?:boilerplate|docstring|comment|typo|rename|format|lint|readme|changelog|"
    r"extract\s+table|unit\s+test|stub|type\s+hint|helper)\b",
    re.IGNORECASE,
)
_EXCLUDED_WALK_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".pytest_cache"}
)
_ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND"})
_THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})
_SECTION_DEF_RE = re.compile(
    r"(?:^[ \t]{0,3}#{1,6}[ \t]*|^[ \t]*)"
    r"(?:(Section|Clause|Article|Exhibit|Schedule|Appendix)\s+([IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])\b"
    r"|(\d+(?:\.\d+)*)[.):])",
    re.IGNORECASE | re.MULTILINE,
)
_CROSS_REF_RE = re.compile(
    r"\b(Section|Clause|Article|Exhibit|Schedule|Appendix)\s+([IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])\b"
    r"(?:\s+of\s+(?:the|this)\s+(?:[A-Z][A-Za-z0-9_-]*\s+){0,4}"
    r"(?:Act|Code|Statute|Regulation|Directive|GDPR|CCPA|U\.S\.C\.))?",
    re.IGNORECASE,
)
_SECTION_SYMBOL_REF_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")


class ThresholdConfig(BaseModel):
    """Calibrated probability thresholds governing Gates 1-4."""

    model_config = ConfigDict(extra="forbid")

    gate1_context_hint_min: float = Field(default=0.80, ge=0.0, le=1.0)
    gate1_actionability_min: float = Field(default=0.30, ge=0.0, le=1.0)
    gate2_invariant_block: float = Field(default=0.78, ge=0.0, le=1.0)
    gate3_floor_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    gate3_mid_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    gate4_completeness_min: float = Field(default=0.85, ge=0.0, le=1.0)
    grey_zone_low: float = Field(default=0.60, ge=0.0, le=1.0)
    grey_zone_high: float = Field(default=0.84, ge=0.0, le=1.0)


class TierModelConfig(BaseModel):
    """Same-family model identifier mapping across capability tiers."""

    model_config = ConfigDict(extra="forbid")

    frontier: str = "pro"
    mid: str = "flash"
    floor: str = "flash_lite"


def load_cartridge(name_or_path: str | Path) -> CartridgeSpec:
    """Load and validate a Domain Cartridge from a file path or built-in name."""
    raw_str = str(name_or_path).strip()
    if not raw_str:
        raise ValueError("Cartridge name or path must not be empty.")

    candidate = Path(raw_str).expanduser()
    resolved: Path | None = None
    if candidate.is_file():
        resolved = candidate
    else:
        for suffix in ("", ".yaml", ".yml"):
            probe = _CARTRIDGES_DIR / f"{raw_str}{suffix}"
            if probe.is_file():
                resolved = probe
                break

    if resolved is None:
        raise FileNotFoundError(f"Domain cartridge not found: {name_or_path!r}")

    loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Cartridge YAML {resolved} must deserialize to a mapping.")
    return CartridgeSpec.model_validate(loaded)


def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a workspace glob pattern to a regex treating `[` / `]` literally."""
    norm = pattern.replace("\\", "/").lstrip("./")
    parts: list[str] = ["^"]
    idx = 0
    length = len(norm)
    while idx < length:
        if norm[idx:idx + 3] == "**/":
            parts.append("(?:.+/)?")
            idx += 3
        elif norm[idx:idx + 3] == "/**":
            parts.append("(?:/.*)?")
            idx += 3
        elif norm[idx:idx + 2] == "**":
            parts.append(".*")
            idx += 2
        elif norm[idx] == "*":
            parts.append("[^/]*")
            idx += 1
        elif norm[idx] == "?":
            parts.append("[^/]")
            idx += 1
        else:
            parts.append(re.escape(norm[idx]))
            idx += 1
    parts.append("$")
    return re.compile("".join(parts))


def _compute_workspace_hash(workspace_root: str) -> str:
    real_norm = os.path.normcase(os.path.realpath(str(Path(workspace_root or "."))))
    return hashlib.blake2b(real_norm.encode("utf-8"), digest_size=16).hexdigest()


def _normalize_rel_posix(raw_path: str, workspace_root: str) -> str:
    cleaned = raw_path.strip()
    if not cleaned:
        return ""
    ws_real = os.path.realpath(str(Path(workspace_root or ".")))
    ws_norm = os.path.normcase(ws_real)
    candidate = Path(cleaned)
    if candidate.is_absolute():
        target_real = os.path.realpath(str(candidate))
        target_norm = os.path.normcase(target_real)
        try:
            if os.path.commonpath([ws_norm, target_norm]) == ws_norm:
                return os.path.relpath(target_real, ws_real).replace("\\", "/")
        except ValueError:
            pass
    norm = cleaned.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _is_safe_dotenv(path_str: str) -> bool:
    base = path_str.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base in _SAFE_DOTENV_NAMES


def _is_forbidden_dotenv(path_str: str) -> bool:
    base = path_str.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base == ".env" or base.startswith(".env."):
        return base not in _SAFE_DOTENV_NAMES
    return False


def _is_read_only_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    return tool_name.strip().lower() in _READ_ONLY_TOOLS


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


def _extract_target_paths(event: GateEvent) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def _add(val: object) -> None:
        if isinstance(val, str):
            stripped = val.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                paths.append(stripped)

    for key in _PATH_ARG_KEYS:
        _add(event.tool_args.get(key))

    for container_key in _EDIT_CONTAINER_KEYS:
        nested = event.tool_args.get(container_key)
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    for key in _PATH_ARG_KEYS:
                        _add(item.get(key))
                elif isinstance(item, str):
                    _add(item)
        elif isinstance(nested, dict):
            for key in _PATH_ARG_KEYS:
                _add(nested.get(key))

    for tf in event.touched_files:
        _add(tf)
    return paths


def _extract_proposed_change_text(event: GateEvent) -> str:
    parts: list[str] = []
    if event.diff_text:
        parts.append(event.diff_text)

    for key in _DIFF_OR_CONTENT_KEYS:
        val = event.tool_args.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)

    chunks = event.tool_args.get("ReplacementChunks") or event.tool_args.get("edits")
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                for key in _DIFF_OR_CONTENT_KEYS:
                    val = chunk.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val)

    return "\n".join(parts)


def _locate_edit_line(event: GateEvent, file_text: str) -> int:
    for key in ("StartLine", "start_line", "line"):
        raw = event.tool_args.get(key)
        if isinstance(raw, int) and raw >= 1:
            return raw

    chunks = event.tool_args.get("ReplacementChunks") or event.tool_args.get("edits")
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                for key in ("StartLine", "start_line", "line"):
                    raw = chunk.get(key)
                    if isinstance(raw, int) and raw >= 1:
                        return raw
                for key in _OLD_CONTENT_KEYS:
                    old = chunk.get(key)
                    if isinstance(old, str) and old:
                        idx = file_text.find(old)
                        if idx != -1:
                            return file_text[:idx].count("\n") + 1

    for key in _OLD_CONTENT_KEYS:
        old = event.tool_args.get(key)
        if isinstance(old, str) and old:
            idx = file_text.find(old)
            if idx != -1:
                return file_text[:idx].count("\n") + 1

    if event.diff_text:
        hunk_match = _DIFF_HUNK_LINE_RE.search(event.diff_text)
        if hunk_match:
            return max(1, int(hunk_match.group(1)))
        for line in event.diff_text.splitlines():
            if line.startswith((" ", "-")) and not line.startswith("---"):
                probe = line[1:].strip()
                if len(probe) >= 6:
                    idx = file_text.find(probe)
                    if idx != -1:
                        return file_text[:idx].count("\n") + 1

    return 1


def _extract_python_enclosing_block(file_text: str, target_line: int) -> str | None:
    try:
        tree = ast.parse(file_text)
    except SyntaxError:
        return None

    lines = file_text.splitlines()
    if not lines:
        return None

    enclosing_class: ast.ClassDef | None = None
    enclosing_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None

    def _visit(nodes: list[ast.stmt], cur_class: ast.ClassDef | None) -> None:
        nonlocal enclosing_class, enclosing_func
        for node in nodes:
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None or end is None or not (start <= target_line <= end):
                continue
            if isinstance(node, ast.ClassDef):
                enclosing_class = node
                _visit(node.body, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing_func = node
                if cur_class is not None:
                    enclosing_class = cur_class
                _visit(node.body, cur_class)

    _visit(tree.body, None)

    if enclosing_func is not None:
        start_idx = max(0, enclosing_func.lineno - 1)
        end_idx = min(len(lines), enclosing_func.end_lineno or enclosing_func.lineno)
        block = "\n".join(lines[start_idx:end_idx])
        if enclosing_class is not None:
            cls_header = lines[max(0, enclosing_class.lineno - 1)]
            return f"{cls_header}\n{block}"
        return block

    if enclosing_class is not None:
        start_idx = max(0, enclosing_class.lineno - 1)
        end_idx = min(len(lines), enclosing_class.end_lineno or enclosing_class.lineno)
        return "\n".join(lines[start_idx:end_idx])

    return None


def _extract_markdown_enclosing_section(file_text: str, target_line: int) -> str | None:
    lines = file_text.splitlines()
    if not lines:
        return None

    clamped = min(len(lines), max(1, target_line))
    heading_positions: list[tuple[int, int]] = []  # (1-based line, level)
    for idx, line in enumerate(lines, start=1):
        match = _MD_HEADING_RE.match(line)
        if match:
            heading_positions.append((idx, len(match.group(1))))

    if not heading_positions:
        # Paragraph fallback around target_line
        start_idx = clamped - 1
        while start_idx > 0 and lines[start_idx - 1].strip():
            start_idx -= 1
        end_idx = clamped
        while end_idx < len(lines) and lines[end_idx].strip():
            end_idx += 1
        return "\n".join(lines[start_idx:end_idx])

    active_start = 1
    active_level = 7
    next_heading_line = len(lines) + 1
    for pos_idx, (hline, level) in enumerate(heading_positions):
        if hline <= clamped:
            active_start = hline
            active_level = level
        elif pos_idx > 0 and level <= active_level:
            next_heading_line = hline
            break
        elif hline > clamped and active_start == 1 and heading_positions[0][0] > clamped:
            next_heading_line = hline
            break

    return "\n".join(lines[active_start - 1:next_heading_line - 1]).strip()


def _extract_brace_or_window_block(file_text: str, target_line: int, window: int = 20) -> str:
    lines = file_text.splitlines()
    if not lines:
        return ""
    clamped = min(len(lines), max(1, target_line))
    start_idx = max(0, clamped - 1 - window)
    end_idx = min(len(lines), clamped + window)
    return "\n".join(lines[start_idx:end_idx])


def _extract_enclosing_context(event: GateEvent, target_paths: list[str]) -> str:
    ws_path = Path(event.workspace_root or ".")
    blocks: list[str] = []

    for raw in target_paths[:2]:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ws_path / candidate
        try:
            real_ws = os.path.normcase(os.path.realpath(str(ws_path)))
            real_cand = os.path.normcase(os.path.realpath(str(candidate)))
            if os.path.commonpath([real_ws, real_cand]) != real_ws:
                continue
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            file_text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not file_text.strip():
            continue

        target_line = _locate_edit_line(event, file_text)
        ext = candidate.suffix.lower()
        extracted: str | None = None
        if ext in {".py", ".pyi"}:
            extracted = _extract_python_enclosing_block(file_text, target_line)
        elif ext in {".md", ".markdown", ".mdx", ".txt", ".rst"}:
            extracted = _extract_markdown_enclosing_section(file_text, target_line)

        if not extracted:
            extracted = _extract_brace_or_window_block(file_text, target_line)
        if extracted:
            blocks.append(extracted[:2500])

    return "\n\n".join(blocks)


def _tokenize_clause_words(text: str) -> tuple[list[str], set[str]]:
    tokens: list[str] = []
    stems: set[str] = set()
    for match in _PATH_MENTION_RE.finditer(text):
        raw_path = match.group(0).replace("\\", "/").lower()
        base = raw_path.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        if stem and not stem.startswith("."):
            stems.add(stem)

    for chunk in _TOKEN_SPLIT_RE.findall(text):
        for part in chunk.split("_"):
            if not part:
                continue
            spaced = _CAMEL_BOUNDARY_2_RE.sub(r"\1 \2", _CAMEL_BOUNDARY_1_RE.sub(r"\1 \2", part))
            for sub in spaced.split():
                low = sub.lower()
                if len(low) <= 1 and not low.isdigit():
                    continue
                if low in _STOPWORDS:
                    continue
                tokens.append(low)
    return tokens, stems


def _extract_atomic_clauses(prompt_text: str) -> list[str]:
    if not prompt_text or not prompt_text.strip():
        return []

    clauses: list[str] = []
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:\d+[.)]|[-*•])\s+", "", line).strip()
        if not line:
            continue
        for semi_part in line.split(";"):
            semi = semi_part.strip()
            if not semi:
                continue
            # Split multi-sentence lines cleanly while preserving file names like auth.py
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", semi)
            for sent in sentences:
                sent_clean = sent.strip()
                if not sent_clean:
                    continue
                # Split comma-separated imperative lists (", update X", ", and add Y")
                parts = _CLAUSE_CONJUNCTION_SPLIT_RE.split(sent_clean)
                for part in parts:
                    sub_parts = re.split(
                        r",\s+(?=(?:add|build|check|configure|create|delete|document|edit|"
                        r"ensure|fix|implement|include|migrate|modify|patch|refactor|"
                        r"remove|rename|rewrite|run|test|update|validate|verify|write)\b)",
                        part,
                        flags=re.IGNORECASE,
                    )
                    for sp in sub_parts:
                        cleaned = sp.strip().strip(".").strip()
                        cleaned = re.sub(r"^(?:and|then|also)\s+", "", cleaned, flags=re.IGNORECASE)
                        if len(cleaned) >= 4:
                            clauses.append(cleaned)
    return list(dict.fromkeys(clauses))[:16]


def _score_zero_dep_clause_coverage(clause: str, evidence_text: str) -> float:
    if not evidence_text.strip():
        return 0.0
    c_tokens, c_stems = _tokenize_clause_words(clause)
    e_tokens, e_stems = _tokenize_clause_words(evidence_text)
    e_token_set = set(e_tokens)
    if e_token_set & {"def", "fn", "func"}:
        e_token_set.update({"function", "method"})
    if e_token_set & {"class", "struct"}:
        e_token_set.update({"type", "class"})
    if e_token_set & {"raise", "except", "valueerror", "typeerror", "runtimeerror"}:
        e_token_set.update({"error", "exception"})
    if e_stems:
        e_token_set.update({"file", "module"})

    key_tokens = (
        c_tokens[1:]
        if len(c_tokens) > 1 and c_tokens[0] in _CLAUSE_FILLER_VERBS
        else c_tokens
    )
    if not key_tokens:
        return 1.0

    hits = 0
    for tok in key_tokens:
        if tok in e_token_set or any(
            (tok in et or et in tok) for et in e_token_set if len(tok) >= 4 and len(et) >= 4
        ):
            hits += 1
    tok_recall = hits / len(key_tokens)

    if c_stems:
        stem_recall = len(c_stems & e_stems) / len(c_stems)
        if stem_recall == 0.0:
            # Clause explicitly targeted a file stem absent from the evidence.
            return min(0.45, tok_recall * 0.6)
        return min(1.0, tok_recall * 0.90 + 0.20 * stem_recall)

    return min(1.0, tok_recall)


def _zero_dep_tier_pass_priors(query_text: str, target_paths: list[str]) -> dict[ModelTier, float]:
    """Deterministic complexity prior when using ZeroDepDriver in Gate 3."""
    has_high = bool(_HIGH_COMPLEXITY_RE.search(query_text))
    has_low = bool(_LOW_COMPLEXITY_RE.search(query_text))
    word_count = len(query_text.split())
    many_files = len(target_paths) >= 4

    if has_high or (word_count > 65 and not has_low) or many_files:
        return {
            ModelTier.FLOOR: 0.42,
            ModelTier.MID: 0.56,
            ModelTier.FRONTIER: 0.96,
        }
    if word_count > 35 and not has_low:
        return {
            ModelTier.FLOOR: 0.70,
            ModelTier.MID: 0.86,
            ModelTier.FRONTIER: 0.97,
        }
    return {
        ModelTier.FLOOR: 0.90,
        ModelTier.MID: 0.95,
        ModelTier.FRONTIER: 0.99,
    }


def _parse_decimal_amount(raw: Any) -> Decimal | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    s = s.replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()
    if not s:
        return None
    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    return -val if negative else val


def _exceeds_currency_precision(val: Decimal, currency: str | None = None) -> bool:
    cur = (currency or "").strip().upper()
    if cur in _ZERO_DECIMAL_CURRENCIES:
        allowed = 0
    elif cur in _THREE_DECIMAL_CURRENCIES:
        allowed = 3
    else:
        allowed = 2
    normalized = val.normalize()
    exp = normalized.as_tuple().exponent
    if isinstance(exp, int) and exp < -allowed:
        return True
    return False


def _collect_ledger_records(ext: str, text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if ext in {".csv", ".tsv"}:
        delimiter = "\t" if ext == ".tsv" else ","
        reader = csv.DictReader(io.StringIO(text.strip()), delimiter=delimiter)
        for row in reader:
            if row:
                records.append({(k or "").strip().lower(): v for k, v in row.items()})
    elif ext == ".json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    records.append({str(k).strip().lower(): v for k, v in item.items()})
        elif isinstance(parsed, dict):
            for key in ("entries", "lines", "rows", "journal", "ledger", "items"):
                sub = parsed.get(key)
                if isinstance(sub, list):
                    for item in sub:
                        if isinstance(item, dict):
                            records.append({str(k).strip().lower(): v for k, v in item.items()})
            txs = parsed.get("transactions")
            if isinstance(txs, list):
                for tx in txs:
                    if isinstance(tx, dict):
                        lines = tx.get("lines") or tx.get("entries")
                        if isinstance(lines, list):
                            for item in lines:
                                if isinstance(item, dict):
                                    records.append(
                                        {str(k).strip().lower(): v for k, v in item.items()}
                                    )
                        else:
                            records.append({str(k).strip().lower(): v for k, v in tx.items()})
    elif ext in {".md", ".markdown"}:
        table_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
        if len(table_lines) >= 3:
            headers = [h.strip().lower() for h in table_lines[0].strip("|").split("|")]
            for row_line in table_lines[2:]:
                cells = [c.strip() for c in row_line.strip("|").split("|")]
                if len(cells) == len(headers):
                    records.append(dict(zip(headers, cells)))
    return records


class GateEngine:
    """Evaluates Gates 1-4 while preserving KV prefix caches across turns 2..N."""

    def __init__(
        self,
        cartridge: CartridgeSpec,
        thresholds: ThresholdConfig | None = None,
        tiers: TierModelConfig | None = None,
        *,
        driver: ClassifierDriver | None = None,
        fts_store: SqliteFtsStore | None = None,
        indexer: AstGistIndexer | None = None,
        cochange: CoChangeGraph | None = None,
        healing: Any | None = None,
    ) -> None:
        self.cartridge = cartridge
        self.thresholds = thresholds or ThresholdConfig()
        self.tiers = tiers or TierModelConfig()
        self.driver: ClassifierDriver = driver if driver is not None else ZeroDepDriver()
        self.fts_store = fts_store
        self.indexer = indexer
        self.cochange = cochange
        self.healing = healing
        self._airgap_driver: ClassifierDriver = (
            self.driver if type(self.driver) is ZeroDepDriver else ZeroDepDriver()
        )
        self._airgap_checker = SqliteFtsStore(":memory:")
        self._private_globs: list[tuple[str, re.Pattern[str]]] = [
            (glob_pat, _compile_glob(glob_pat))
            for glob_pat in cartridge.context_unit.private_tier1_globs
        ]
        self._gate2_strikes: dict[tuple[str, str], int] = {}

    def _matches_private_glob(self, path_str: str, workspace_root: str) -> bool:
        norm = _normalize_rel_posix(path_str, workspace_root)
        if not norm or _is_safe_dotenv(norm):
            return False
        if _is_forbidden_dotenv(norm):
            return True
        for raw_glob, rx in self._private_globs:
            if rx.match(norm):
                if _is_safe_dotenv(norm) and ".env*" in raw_glob:
                    continue
                return True
        return False

    def _check_air_gap(
        self,
        *,
        text: str,
        workspace_root: str,
        paths: list[str] | None = None,
        gists: list[IndexedGist] | None = None,
    ) -> bool:
        checker = self.fts_store if self.fts_store is not None else self._airgap_checker
        if not checker.check_air_gap(text, workspace_root):
            return False

        for raw in paths or []:
            if not checker.check_air_gap(raw, workspace_root):
                return False
            if self._matches_private_glob(raw, workspace_root):
                return False

        for match in _PATH_MENTION_RE.finditer(text or ""):
            if self._matches_private_glob(match.group(0), workspace_root):
                return False

        for tok in (text or "").split():
            cleaned = tok.strip("`'\",()[]{};:")
            if cleaned and self._matches_private_glob(cleaned, workspace_root):
                return False

        for gist in gists or []:
            if gist.is_private_tier1 or self._matches_private_glob(gist.rel_path, workspace_root):
                return False

        return True

    def _select_active_driver(self, cloud_egress_allowed: bool) -> ClassifierDriver:
        if not cloud_egress_allowed and self.driver.capabilities.is_cloud:
            return self._airgap_driver
        return self.driver

    def _filter_active_invariants(
        self,
        target_paths: list[str],
        workspace_root: str,
    ) -> list[InvariantSpec]:
        norm_targets = [_normalize_rel_posix(p, workspace_root) for p in target_paths if p.strip()]
        active: list[InvariantSpec] = []
        for inv in self.cartridge.invariants:
            if not inv.scope_globs:
                active.append(inv)
                continue
            if not norm_targets:
                continue
            compiled = [_compile_glob(pat) for pat in inv.scope_globs]
            if any(rx.match(target) for target in norm_targets for rx in compiled):
                active.append(inv)
        return active

    async def _classify_with_budget(
        self,
        driver: ClassifierDriver,
        query_text: str,
        heads: list[ClassifierHead],
        *,
        cloud_egress_allowed: bool,
    ) -> dict[str, float]:
        if not heads:
            return {}
        max_per_pass = max(1, driver.capabilities.max_candidates_per_pass)
        merged: dict[str, float] = {}
        for start in range(0, len(heads), max_per_pass):
            batch = heads[start:start + max_per_pass]
            req = ClassifierRequest(
                query_text=query_text,
                heads=batch,
                cloud_egress_allowed=cloud_egress_allowed,
                max_candidates=len(batch),
            )
            resp = await driver.classify(req)
            merged.update(resp.calibrated_probabilities)
        return merged

    def _path_in_assigned_cage(
        self,
        raw_path: str,
        cage_entries: list[str],
        workspace_root: str,
    ) -> bool:
        if not cage_entries:
            return False
        target_rel = _normalize_rel_posix(raw_path, workspace_root)
        target_case = os.path.normcase(target_rel)
        ws_path = Path(workspace_root or ".")

        for entry in cage_entries:
            cleaned = entry.strip()
            if not cleaned:
                continue
            cage_rel = _normalize_rel_posix(cleaned, workspace_root)
            cage_case = os.path.normcase(cage_rel)
            if target_case == cage_case:
                return True
            if any(ch in cage_rel for ch in ("*", "?")):
                if _compile_glob(cage_rel).match(target_rel):
                    return True
                continue
            stripped_dir = cage_rel.rstrip("/")
            if stripped_dir and (
                cage_rel.endswith("/") or (ws_path / stripped_dir).is_dir()
            ):
                prefix = os.path.normcase(stripped_dir + "/")
                if target_case.startswith(prefix):
                    return True
        return False

    def _record_gate2_strike(self, session_id: str, rule_id: str, event_strike: int) -> int:
        key = (session_id, rule_id)
        prev = max(self._gate2_strikes.get(key, 0), event_strike)
        new_strike = prev + 1
        self._gate2_strikes[key] = new_strike
        return new_strike

    async def evaluate(self, event: GateEvent) -> GateVerdict:
        """Dispatch GateEvent to the matching gate handler."""
        if event.gate == GateType.INTAKE:
            return await self.evaluate_gate1_intake(event)
        if event.gate == GateType.FIREWALL:
            return await self.evaluate_gate2_firewall(event)
        if event.gate == GateType.SUBAGENT_GOVERNOR:
            return await self.evaluate_gate3_subagent(event)
        if event.gate == GateType.STOP_AUDIT:
            return await self.evaluate_gate4_stop(event)
        raise ValueError(f"Unsupported gate type: {event.gate!r}")

    def _coerce_metadata_candidates(
        self,
        raw_candidates: Any,
        workspace_hash: str,
        workspace_root: str,
    ) -> list[IndexedGist]:
        if not isinstance(raw_candidates, list):
            return []
        gists: list[IndexedGist] = []
        for item in raw_candidates:
            if isinstance(item, IndexedGist):
                gists.append(item)
            elif isinstance(item, dict):
                if "workspace_hash" in item and "content_hash" in item and "language" in item:
                    gists.append(IndexedGist.model_validate(item))
                else:
                    rel = str(item.get("rel_path") or item.get("path") or "").strip()
                    if not rel:
                        continue
                    norm_rel = _normalize_rel_posix(rel, workspace_root)
                    raw_symbols = item.get("symbols")
                    symbols = raw_symbols if isinstance(raw_symbols, list) else []
                    raw_imports = item.get("imports")
                    imports = raw_imports if isinstance(raw_imports, list) else []
                    gists.append(
                        IndexedGist(
                            workspace_hash=str(item.get("workspace_hash") or workspace_hash),
                            rel_path=norm_rel,
                            content_hash=str(item.get("content_hash") or "meta"),
                            language=str(item.get("language") or "text"),
                            semantic_gist=str(
                                item.get("semantic_gist") or item.get("gist") or norm_rel
                            ),
                            symbols=[str(s) for s in symbols],
                            imports=[str(i) for i in imports],
                            is_private_tier1=bool(item.get("is_private_tier1", False))
                            or self._matches_private_glob(norm_rel, workspace_root),
                        )
                    )
            elif isinstance(item, str) and item.strip():
                norm_rel = _normalize_rel_posix(item, workspace_root)
                gists.append(
                    IndexedGist(
                        workspace_hash=workspace_hash,
                        rel_path=norm_rel,
                        content_hash="meta",
                        language="text",
                        semantic_gist=norm_rel,
                        is_private_tier1=self._matches_private_glob(norm_rel, workspace_root),
                    )
                )
        return gists

    @staticmethod
    def _format_candidate_text(gist: IndexedGist) -> str:
        symbols_str = ", ".join(gist.symbols[:8])
        if symbols_str and gist.semantic_gist:
            return f"{gist.rel_path}: {symbols_str} — {gist.semantic_gist}"
        if symbols_str:
            return f"{gist.rel_path}: {symbols_str}"
        if gist.semantic_gist:
            return f"{gist.rel_path}: {gist.semantic_gist}"
        return gist.rel_path

    async def _call_healing_get_associated_paths(
        self,
        source_paths: list[str],
        workspace_root: str,
    ) -> dict[str, float]:
        if (
            self.healing is None
            or not source_paths
            or not hasattr(self.healing, "get_associated_paths")
        ):
            return {}
        fn = self.healing.get_associated_paths
        try:
            sig = inspect.signature(fn)
            if "workspace_root" in sig.parameters:
                res = await _maybe_await(fn(source_paths, workspace_root=workspace_root))
            else:
                res = await _maybe_await(fn(source_paths))
        except (TypeError, ValueError):
            res = await _maybe_await(fn(source_paths))
        if not isinstance(res, dict):
            return {}
        return {str(k): float(v) for k, v in res.items()}

    async def evaluate_gate1_intake(self, event: GateEvent) -> GateVerdict:
        """Execute Turn-1 privacy air-gap, Pass-0 retrieval, and tail-hint injection."""
        t0 = time.perf_counter()

        # Strict Warm-Cache Guard: preserve KV prefix caches untouched across turns 2..N.
        if event.turn_index > 1 and event.metadata.get("compact_reset") is not True:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.INTAKE,
                tail_hints=[],
                reason=None,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        prompt_text = (event.prompt_text or "").strip()
        if not prompt_text:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.INTAKE,
                tail_hints=[],
                reason=None,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        ws_hash = str(
            event.metadata.get("workspace_hash")
            or (
                self.indexer.workspace_hash
                if self.indexer is not None
                else _compute_workspace_hash(event.workspace_root)
            )
        )

        # Pass-0 air-gap check on prompt and workspace.
        cloud_egress_allowed = self._check_air_gap(
            text=prompt_text,
            workspace_root=event.workspace_root,
            paths=event.touched_files,
        )

        # Retrieve Pass-0 candidates from fts_store, metadata, or indexer fallback.
        raw_candidates: list[IndexedGist] = []
        seen_paths: set[str] = set()

        def _append_unique(items: list[IndexedGist]) -> None:
            for g in items:
                if g.rel_path not in seen_paths:
                    seen_paths.add(g.rel_path)
                    raw_candidates.append(g)

        if self.fts_store is not None:
            fts_res = self.fts_store.search_candidates(prompt_text, ws_hash, limit=64)
            if not fts_res.cloud_egress_allowed:
                cloud_egress_allowed = False
            _append_unique(fts_res.gists)

        meta_gists = self._coerce_metadata_candidates(
            event.metadata.get("candidates"),
            ws_hash,
            event.workspace_root,
        )
        _append_unique(meta_gists)

        if not raw_candidates and self.indexer is not None:
            ws_path = Path(event.workspace_root)
            if ws_path.exists():
                _append_unique(self.indexer.index_workspace(incremental=True))

        if not self._check_air_gap(
            text=prompt_text,
            workspace_root=event.workspace_root,
            gists=raw_candidates,
        ):
            cloud_egress_allowed = False

        active_driver = self._select_active_driver(cloud_egress_allowed)
        budgeted = CalibrationEngine.budget_candidates(active_driver, raw_candidates)

        pragmatic_heads = [
            ClassifierHead(
                head_id="pragmatic:directive",
                candidate_text="Directive actionable instruction to execute edit or mutation",
                category=HeadCategory.PRAGMATIC,
                metadata={"head_type": "pragmatic:directive"},
            ),
            ClassifierHead(
                head_id="pragmatic:counterfactual",
                candidate_text=(
                    "Counterfactual hypothetical brainstorm read-only exploration "
                    "that should not mutate files"
                ),
                category=HeadCategory.PRAGMATIC,
                metadata={"head_type": "pragmatic:counterfactual"},
            ),
        ]

        candidate_heads: list[ClassifierHead] = []
        head_to_path: dict[str, str] = {}
        for idx, gist in enumerate(budgeted):
            hid = f"cand:{idx}"
            head_to_path[hid] = gist.rel_path
            candidate_heads.append(
                ClassifierHead(
                    head_id=hid,
                    candidate_text=self._format_candidate_text(gist),
                    category=HeadCategory.CANDIDATE,
                    metadata={"rel_path": gist.rel_path},
                )
            )

        max_heads = active_driver.capabilities.max_candidates_per_pass
        if len(pragmatic_heads) + len(candidate_heads) <= max_heads:
            probs = await self._classify_with_budget(
                active_driver,
                prompt_text,
                pragmatic_heads + candidate_heads,
                cloud_egress_allowed=cloud_egress_allowed,
            )
        else:
            probs = await self._classify_with_budget(
                active_driver,
                prompt_text,
                pragmatic_heads,
                cloud_egress_allowed=cloud_egress_allowed,
            )
            if candidate_heads:
                cand_probs = await self._classify_with_budget(
                    active_driver,
                    prompt_text,
                    candidate_heads,
                    cloud_egress_allowed=cloud_egress_allowed,
                )
                probs.update(cand_probs)

        p_directive = probs.get("pragmatic:directive", 0.5)
        p_counterfactual = probs.get("pragmatic:counterfactual", 0.0)
        actionability = p_directive * (1.0 - p_counterfactual)
        advisory: str | None = None
        if actionability < self.thresholds.gate1_actionability_min:
            advisory = (
                "[QUADGATE_MODE: Read-only brainstorm detected; confirm before mutating files]"
            )

        base_scores: dict[str, float] = {}
        for hid, rel_path in head_to_path.items():
            base_scores[rel_path] = probs.get(hid, 0.0)

        boosted_scores = dict(base_scores)
        top_hit_path: str | None = None
        if base_scores:
            top_hit_path = max(base_scores, key=lambda p: base_scores[p])

        # Git co-change boost: p*_j = min(1.0, p_cal_j + 0.12 * w_cochange(i*, j)) when w > 0.40
        if self.cochange is not None and top_hit_path is not None:
            for cand_path in list(boosted_scores.keys()):
                if cand_path == top_hit_path:
                    continue
                w = self.cochange.weight(top_hit_path, cand_path)
                if w > _COCHANGE_BOOST_THRESHOLD:
                    boosted_scores[cand_path] = min(
                        1.0,
                        boosted_scores[cand_path] + _COCHANGE_BOOST_WEIGHT * w,
                    )

        # Associative context-miss boost from SelfHealingEngine
        if self.healing is not None and boosted_scores:
            seed_paths = [
                p
                for p, sc in sorted(boosted_scores.items(), key=lambda kv: -kv[1])
                if sc >= self.thresholds.gate1_context_hint_min
            ]
            if not seed_paths and top_hit_path is not None and boosted_scores[top_hit_path] >= 0.50:
                seed_paths = [top_hit_path]
            if seed_paths:
                assoc = await self._call_healing_get_associated_paths(
                    seed_paths,
                    event.workspace_root,
                )
                for assoc_path, w_assoc in assoc.items():
                    norm_assoc = _normalize_rel_posix(assoc_path, event.workspace_root)
                    if not norm_assoc or _is_forbidden_dotenv(norm_assoc):
                        continue
                    cur_val = boosted_scores.get(norm_assoc, 0.0)
                    floor_val = 0.56 + 0.40 * w_assoc if w_assoc >= 0.50 else 0.0
                    boosted_scores[norm_assoc] = min(
                        1.0,
                        max(cur_val + 0.35 * w_assoc, floor_val),
                    )

        qualified = [
            path
            for path, score in sorted(
                boosted_scores.items(),
                key=lambda kv: (-kv[1], -base_scores.get(kv[0], 0.0), kv[0]),
            )
            if score >= self.thresholds.gate1_context_hint_min and not _is_forbidden_dotenv(path)
        ]
        tail_hints = qualified[:4]

        if (
            self.healing is not None
            and tail_hints
            and hasattr(self.healing, "record_turn1_hints")
        ):
            await _maybe_await(
                self.healing.record_turn1_hints(
                    event.session_id,
                    event.workspace_root,
                    list(tail_hints),
                )
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if tail_hints or advisory is not None:
            return GateVerdict(
                action=VerdictAction.AUGMENT,
                gate=GateType.INTAKE,
                tail_hints=tail_hints,
                reason=advisory,
                cloud_egress_allowed=cloud_egress_allowed,
                latency_ms=elapsed_ms,
            )

        return GateVerdict(
            action=VerdictAction.ALLOW,
            gate=GateType.INTAKE,
            tail_hints=[],
            reason=None,
            cloud_egress_allowed=cloud_egress_allowed,
            latency_ms=elapsed_ms,
        )

    async def _call_log_grey_zone(
        self,
        event: GateEvent,
        score: float,
        head_id: str,
        verdict_action: str,
    ) -> None:
        if self.healing is None or not hasattr(self.healing, "log_grey_zone_decision"):
            return
        fn = self.healing.log_grey_zone_decision
        try:
            sig = inspect.signature(fn)
            if "verdict_action" in sig.parameters:
                await _maybe_await(fn(event, score, head_id, verdict_action=verdict_action))
                return
        except (TypeError, ValueError):
            pass
        await _maybe_await(fn(event, score, head_id))

    async def evaluate_gate2_firewall(self, event: GateEvent) -> GateVerdict:
        """Execute Stage-2A/2B pre-mutation invariant checks and 2-strike escalation."""
        t0 = time.perf_counter()

        if self.healing is not None and hasattr(self.healing, "observe_tool_event"):
            await _maybe_await(self.healing.observe_tool_event(event))

        target_paths = _extract_target_paths(event)
        read_only = _is_read_only_tool(event.tool_name)

        # Stage-2A (0 ms deterministic, NEVER bypassed)
        stage2a_payload: dict[str, object] = {
            "tool_name": event.tool_name,
            "tool_args": dict(event.tool_args),
            "touched_files": list(event.touched_files),
        }
        if event.diff_text:
            stage2a_payload["diff_text"] = event.diff_text

        receipt_dict = check_stage2a_deterministic_security(stage2a_payload, event.workspace_root)
        if receipt_dict is not None:
            rule_id = str(receipt_dict.get("rule_id") or "STAGE2A_SECURITY_BLOCK")
            strike = self._record_gate2_strike(event.session_id, rule_id, event.strike_count)
            escalate = strike >= 2
            remediation = str(receipt_dict.get("remediation") or "Blocked by Stage-2A security.")
            if escalate:
                remediation = (
                    f"HUMAN CONFIRMATION REQUIRED (strike {strike} on {rule_id}): "
                    f"{remediation} Confirm human override before proceeding."
                )
            receipt = ViolationReceipt(
                rule_id=rule_id,
                score=float(receipt_dict.get("score", 1.0)),  # type: ignore[arg-type]
                stage="deterministic_2a",
                remediation=remediation,
                strike_count=strike,
                escalate_to_human=escalate,
                violated_path=(
                    str(receipt_dict["violated_path"])
                    if receipt_dict.get("violated_path") is not None
                    else None
                ),
            )
            return GateVerdict(
                action=VerdictAction.BLOCK,
                gate=GateType.FIREWALL,
                violation_receipt=receipt,
                reason=remediation,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # Assigned file cage containment (Stage-2A deterministic, never bypassed)
        if not read_only and event.assigned_file_cage is not None:
            for raw_target in target_paths:
                if not self._path_in_assigned_cage(
                    raw_target,
                    event.assigned_file_cage,
                    event.workspace_root,
                ):
                    rule_id = "INV_02_CAGE_CONTAINMENT"
                    strike = self._record_gate2_strike(
                        event.session_id,
                        rule_id,
                        event.strike_count,
                    )
                    escalate = strike >= 2
                    remediation = (
                        f"Target path {raw_target!r} is outside assigned_file_cage "
                        f"{event.assigned_file_cage!r}."
                    )
                    if escalate:
                        remediation = (
                            f"HUMAN CONFIRMATION REQUIRED (strike {strike} on {rule_id}): "
                            f"{remediation} Request Orchestrator approval to expand the cage."
                        )
                    receipt = ViolationReceipt(
                        rule_id=rule_id,
                        score=1.0,
                        stage="deterministic_2a",
                        remediation=remediation,
                        strike_count=strike,
                        escalate_to_human=escalate,
                        violated_path=raw_target,
                    )
                    return GateVerdict(
                        action=VerdictAction.BLOCK,
                        gate=GateType.FIREWALL,
                        violation_receipt=receipt,
                        reason=remediation,
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                    )

        # [bypass-gates] bypasses Stage-2B semantic invariants cleanly.
        if event.bypass_gates or (
            event.prompt_text is not None and "[bypass-gates]" in event.prompt_text.lower()
        ):
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.FIREWALL,
                reason="Stage-2B semantic invariants bypassed via [bypass-gates].",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        if read_only:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.FIREWALL,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        active_invariants = self._filter_active_invariants(target_paths, event.workspace_root)
        if not active_invariants:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.FIREWALL,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        enclosing_context = _extract_enclosing_context(event, target_paths)
        proposed_change = _extract_proposed_change_text(event)
        query_parts: list[str] = []
        if target_paths:
            query_parts.append(f"Target: {', '.join(target_paths)}")
        if enclosing_context:
            query_parts.append(f"[ENCLOSING_CONTEXT]\n{enclosing_context}")
        if proposed_change:
            query_parts.append(f"[PROPOSED_CHANGE]\n{proposed_change}")
        if not proposed_change and event.prompt_text:
            query_parts.append(event.prompt_text)

        query_text = "\n\n".join(query_parts).strip()
        if not query_text:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.FIREWALL,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        cloud_egress_allowed = self._check_air_gap(
            text=query_text,
            workspace_root=event.workspace_root,
            paths=target_paths,
        )
        active_driver = self._select_active_driver(cloud_egress_allowed)

        inv_heads = [
            ClassifierHead(
                head_id=inv.id,
                candidate_text=inv.gist,
                category=HeadCategory.INVARIANT,
                metadata={"head_type": inv.id},
            )
            for inv in active_invariants
        ]
        scores = await self._classify_with_budget(
            active_driver,
            query_text,
            inv_heads,
            cloud_egress_allowed=cloud_egress_allowed,
        )

        max_score = max(scores.values(), default=0.0)
        will_block = max_score >= self.thresholds.gate2_invariant_block
        grey_action = "block" if will_block else "allow"

        for inv in active_invariants:
            score = scores.get(inv.id, 0.0)
            if self.thresholds.grey_zone_low <= score <= self.thresholds.grey_zone_high:
                await self._call_log_grey_zone(event, score, inv.id, grey_action)

        violated_inv: InvariantSpec | None = None
        violated_score = -1.0
        for inv in active_invariants:
            score = scores.get(inv.id, 0.0)
            if score >= self.thresholds.gate2_invariant_block and score > violated_score:
                violated_inv = inv
                violated_score = score

        if violated_inv is None:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.FIREWALL,
                cloud_egress_allowed=cloud_egress_allowed,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        strike = self._record_gate2_strike(
            event.session_id,
            violated_inv.id,
            event.strike_count,
        )
        escalate = strike >= 2
        if escalate:
            remediation = (
                f"HUMAN CONFIRMATION REQUIRED (strike {strike} on {violated_inv.id}): "
                f"{violated_inv.remediation} Confirm human override or include "
                "[bypass-gates] in prompt to proceed."
            )
        else:
            remediation = violated_inv.remediation

        receipt = ViolationReceipt(
            rule_id=violated_inv.id,
            score=min(1.0, max(0.0, violated_score)),
            stage="semantic_2b",
            remediation=remediation,
            strike_count=strike,
            escalate_to_human=escalate,
            violated_path=target_paths[0] if target_paths else None,
        )
        return GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.FIREWALL,
            violation_receipt=receipt,
            reason=f"{violated_inv.id}: {remediation}",
            cloud_egress_allowed=cloud_egress_allowed,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    def compute_ecc(
        self,
        tier: ModelTier,
        p_pass: float,
        blast_radius: float = 0.0,
        r_hist: float = 0.0,
    ) -> float:
        """Compute Expected Cost of Completion for tier M_k given B and R_hist."""
        p = min(1.0, max(0.0, float(p_pass)))
        b = min(1.0, max(0.0, float(blast_radius)))
        r = min(1.0, max(0.0, float(r_hist)))
        p_eff = p * (1.0 - 0.5 * b) * (1.0 - r)
        return _TIER_RUN_COST[tier] + (1.0 - p_eff) * _FRONTIER_PROMOTE_COST

    def _resolve_gate3_targets(self, event: GateEvent, query_text: str) -> list[str]:
        candidates = _extract_target_paths(event)
        if event.assigned_file_cage:
            for item in event.assigned_file_cage:
                if item and item not in candidates:
                    candidates.append(item)

        for key in ("files", "targets", "target_paths"):
            val = event.tool_args.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.strip() and item not in candidates:
                        candidates.append(item.strip())

        for match in _PATH_MENTION_RE.finditer(query_text):
            tok = match.group(0)
            if tok not in candidates:
                candidates.append(tok)

        if self.indexer is not None:
            gists_by_path = getattr(self.indexer, "_gists_by_path", None)
            if not gists_by_path and Path(event.workspace_root).exists():
                self.indexer.index_workspace(incremental=True)
                gists_by_path = getattr(self.indexer, "_gists_by_path", {})
            if isinstance(gists_by_path, dict) and gists_by_path:
                resolved: list[str] = []
                for raw in candidates:
                    norm = _normalize_rel_posix(raw, event.workspace_root)
                    if norm in gists_by_path:
                        resolved.append(norm)
                    else:
                        base = norm.rsplit("/", 1)[-1]
                        matched = [
                            gp
                            for gp in gists_by_path
                            if gp == base or gp.rsplit("/", 1)[-1] == base
                        ]
                        resolved.extend(matched or [norm])
                return list(dict.fromkeys(resolved))

        return [
            _normalize_rel_posix(c, event.workspace_root)
            for c in candidates
            if c.strip()
        ]

    async def _get_failure_rate_for_path(self, rel_path: str, workspace_root: str) -> float:
        if self.healing is None or not hasattr(self.healing, "get_failure_rate"):
            return 0.0
        fn = self.healing.get_failure_rate
        try:
            sig = inspect.signature(fn)
            if "workspace_root" in sig.parameters:
                val = await _maybe_await(fn(rel_path, workspace_root=workspace_root))
            else:
                val = await _maybe_await(fn(rel_path))
        except (TypeError, ValueError):
            val = await _maybe_await(fn(rel_path))
        return min(1.0, max(0.0, float(val)))

    def _model_for_tier(self, tier: ModelTier) -> str:
        if tier == ModelTier.FLOOR:
            return self.tiers.floor
        if tier == ModelTier.MID:
            return self.tiers.mid
        return self.tiers.frontier

    async def evaluate_gate3_subagent(self, event: GateEvent) -> GateVerdict:
        """Select lowest-cost model tier satisfying graph-weighted ECC equation."""
        t0 = time.perf_counter()

        subagent_prompt = (
            event.prompt_text
            or str(event.tool_args.get("brief") or "")
            or str(event.tool_args.get("prompt") or "")
            or str(event.tool_args.get("description") or "")
            or str(event.tool_args.get("task") or "")
            or "Execute subagent task"
        ).strip()

        target_paths = self._resolve_gate3_targets(event, subagent_prompt)

        if self.indexer is not None and target_paths:
            raw_blast = float(self.indexer.compute_blast_radius(target_paths))
            blast_radius = min(1.0, max(0.0, raw_blast))
        else:
            raw_b = event.metadata.get("blast_radius", 0.0)
            blast_radius = min(1.0, max(0.0, float(raw_b)))

        if self.healing is not None and target_paths and hasattr(self.healing, "get_failure_rate"):
            rates = [
                await self._get_failure_rate_for_path(p, event.workspace_root)
                for p in target_paths
            ]
            r_hist = max(rates, default=0.0)
        else:
            raw_r = event.metadata.get("r_hist", 0.0)
            r_hist = min(1.0, max(0.0, float(raw_r)))

        cloud_egress_allowed = self._check_air_gap(
            text=subagent_prompt,
            workspace_root=event.workspace_root,
            paths=target_paths,
        )
        active_driver = self._select_active_driver(cloud_egress_allowed)

        tier_heads = [
            ClassifierHead(
                head_id="tier:floor",
                candidate_text="Simple mechanical leaf task safe for floor tier model",
                category=HeadCategory.PRAGMATIC,
                metadata={"head_type": "tier:floor"},
            ),
            ClassifierHead(
                head_id="tier:mid",
                candidate_text="Moderate multi-step task suitable for mid tier model",
                category=HeadCategory.PRAGMATIC,
                metadata={"head_type": "tier:mid"},
            ),
            ClassifierHead(
                head_id="tier:frontier",
                candidate_text="Complex high-risk architectural task requiring frontier tier model",
                category=HeadCategory.PRAGMATIC,
                metadata={"head_type": "tier:frontier"},
            ),
        ]
        active_invariants = self._filter_active_invariants(target_paths, event.workspace_root)
        inv_heads = [
            ClassifierHead(
                head_id=f"inv:{inv.id}",
                candidate_text=inv.gist,
                category=HeadCategory.INVARIANT,
                metadata={"head_type": inv.id},
            )
            for inv in active_invariants
        ]

        probs = await self._classify_with_budget(
            active_driver,
            subagent_prompt,
            tier_heads + inv_heads,
            cloud_egress_allowed=cloud_egress_allowed,
        )

        max_inv = max((probs.get(h.head_id, 0.0) for h in inv_heads), default=0.0)

        if type(active_driver) is ZeroDepDriver:
            p_pass_by_tier = _zero_dep_tier_pass_priors(subagent_prompt, target_paths)
        else:
            p_pass_by_tier = {
                ModelTier.FLOOR: probs.get("tier:floor", 0.0),
                ModelTier.MID: probs.get("tier:mid", 0.0),
                ModelTier.FRONTIER: probs.get("tier:frontier", 0.95),
            }

        ecc_frontier = self.compute_ecc(
            ModelTier.FRONTIER,
            p_pass_by_tier[ModelTier.FRONTIER],
            blast_radius=blast_radius,
            r_hist=r_hist,
        )

        tier_order = [ModelTier.FLOOR, ModelTier.MID, ModelTier.FRONTIER]
        if event.strike_count >= 1 and event.current_tier is not None:
            min_idx = tier_order.index(event.current_tier)
            tier_order = tier_order[min_idx:]

        tau_by_tier = {
            ModelTier.FLOOR: self.thresholds.gate3_floor_confidence,
            ModelTier.MID: self.thresholds.gate3_mid_confidence,
        }

        selected_tier = ModelTier.FRONTIER
        selected_p_eff = (
            p_pass_by_tier[ModelTier.FRONTIER] * (1.0 - 0.5 * blast_radius) * (1.0 - r_hist)
        )
        selected_ecc = ecc_frontier

        for tier in tier_order:
            if tier == ModelTier.FRONTIER:
                selected_tier = ModelTier.FRONTIER
                selected_p_eff = (
                    p_pass_by_tier[ModelTier.FRONTIER]
                    * (1.0 - 0.5 * blast_radius)
                    * (1.0 - r_hist)
                )
                selected_ecc = ecc_frontier
                break

            p_pass_k = p_pass_by_tier[tier]
            p_eff_k = p_pass_k * (1.0 - 0.5 * blast_radius) * (1.0 - r_hist)
            ecc_k = self.compute_ecc(tier, p_pass_k, blast_radius=blast_radius, r_hist=r_hist)
            tau_k = tau_by_tier[tier]

            if (
                max_inv < 0.50
                and p_eff_k >= tau_k
                and ecc_k <= _ECC_SAVINGS_FACTOR * ecc_frontier
            ):
                selected_tier = tier
                selected_p_eff = p_eff_k
                selected_ecc = ecc_k
                break

        selected_model = self._model_for_tier(selected_tier)
        updated_tool_args = dict(event.tool_args)
        if event.harness == HarnessType.ANTIGRAVITY or "Model" in updated_tool_args:
            updated_tool_args["Model"] = selected_model
        if event.harness != HarnessType.ANTIGRAVITY or "model" in updated_tool_args:
            updated_tool_args["model"] = selected_model

        return GateVerdict(
            action=VerdictAction.MODIFY,
            gate=GateType.SUBAGENT_GOVERNOR,
            updated_model=selected_model,
            target_tier=selected_tier,
            updated_tool_args=updated_tool_args,
            cloud_egress_allowed=cloud_egress_allowed,
            reason=(
                f"Gate3 ECC selected tier={selected_tier.value} model={selected_model} "
                f"(B={blast_radius:.2f}, R_hist={r_hist:.2f}, P_eff={selected_p_eff:.3f}, "
                f"ECC={selected_ecc:.3f} vs frontier={ecc_frontier:.3f}, max_I={max_inv:.3f})"
            ),
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def _run_extension_oracle_commands(self, event: GateEvent) -> str | None:
        cmds_by_ext = self.cartridge.truth_oracle.commands_by_extension
        if not cmds_by_ext:
            return None

        ws_path = Path(event.workspace_root or ".")
        timeout_s = max(0.05, self.cartridge.truth_oracle.timeout_ms / 1000.0)
        targets = event.touched_files or _extract_target_paths(event)
        executed_global_cmds: set[tuple[str, ...]] = set()

        for raw in targets:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = ws_path / candidate
            if not candidate.is_file():
                continue

            ext = candidate.suffix.lower()
            template = cmds_by_ext.get(ext)
            if not template:
                continue

            if (
                len(template) == 4
                and template[:3] in (["python", "-m", "py_compile"], ["python3", "-m", "py_compile"])
            ):
                try:
                    await asyncio.to_thread(py_compile.compile, str(candidate), doraise=True)
                except (py_compile.PyCompileError, SyntaxError, OSError) as exc:
                    return f"Truth oracle failed for {raw!r}: {exc}"
                continue

            uses_file_placeholder = any("{file}" in tok or "{path}" in tok for tok in template)
            argv = [
                tok.replace("{file}", str(candidate)).replace("{path}", str(candidate))
                for tok in template
            ]
            if argv[0] in {"python", "python3"}:
                argv[0] = sys.executable
            else:
                which_exe = shutil.which(argv[0])
                if which_exe is None:
                    continue
                argv[0] = which_exe

            if not uses_file_placeholder:
                cmd_key = tuple(argv)
                if cmd_key in executed_global_cmds:
                    continue
                executed_global_cmds.add(cmd_key)
                if ext in {".ts", ".tsx"} and not (ws_path / "tsconfig.json").is_file():
                    continue

            cwd = str(ws_path) if ws_path.is_dir() else None
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return (
                    f"Truth oracle timed out after {self.cartridge.truth_oracle.timeout_ms}ms "
                    f"for {raw!r}."
                )

            if proc.returncode != 0:
                err_msg = (
                    stderr_b.decode("utf-8", errors="replace").strip()
                    or stdout_b.decode("utf-8", errors="replace").strip()
                    or f"exit code {proc.returncode}"
                )
                return f"Truth oracle failed for {raw!r}: {err_msg}"

        return None

    def _gather_oracle_artifacts(self, event: GateEvent) -> list[tuple[str, str]]:
        ws_path = Path(event.workspace_root or ".")
        artifacts: list[tuple[str, str]] = []
        targets = event.touched_files or _extract_target_paths(event)

        for raw in targets:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = ws_path / candidate
            if candidate.is_file():
                try:
                    artifacts.append((raw, candidate.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue

        if not artifacts:
            fallback_text = _extract_proposed_change_text(event)
            if fallback_text.strip():
                synthetic_name = targets[0] if targets else "diff_artifact.txt"
                artifacts.append((synthetic_name, fallback_text))

        return artifacts

    def _list_workspace_note_targets(self, workspace_root: str) -> tuple[set[str], set[str]]:
        ws_path = Path(workspace_root or ".")
        rel_paths: set[str] = set()
        stems: set[str] = set()

        if self.indexer is not None:
            gists = getattr(self.indexer, "_gists_by_path", None)
            if isinstance(gists, dict):
                for gp in gists:
                    low = gp.lower()
                    rel_paths.add(low)
                    stems.add(Path(low).stem)

        if ws_path.is_dir():
            for root, dirs, files in os.walk(ws_path):
                dirs[:] = [d for d in dirs if d not in _EXCLUDED_WALK_DIRS]
                for fname in files:
                    fpath = Path(root) / fname
                    try:
                        rel = fpath.relative_to(ws_path).as_posix().lower()
                    except ValueError:
                        continue
                    rel_paths.add(rel)
                    stems.add(fpath.stem.lower())
        return rel_paths, stems

    def _check_wikilinks_exist(
        self,
        event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        rel_paths, stems = self._list_workspace_note_targets(event.workspace_root)
        for name, text in artifacts:
            stripped = _MD_FENCE_RE.sub("", text)
            for m in _WIKILINK_RE.finditer(stripped):
                raw_target = m.group(1).strip().replace("\\", "/").lstrip("./")
                low = raw_target.lower()
                target_stem = Path(low).stem
                exists = (
                    low in rel_paths
                    or f"{low}.md" in rel_paths
                    or low in stems
                    or target_stem in stems
                )
                if not exists:
                    return f"Broken Wiki-Link [[{raw_target}]] in {name!r}."
        return None

    def _check_citations_present(
        self,
        _event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        for name, text in artifacts:
            ext = Path(name).suffix.lower()
            if ext and ext not in {".md", ".markdown", ".mdx", ".txt", ".rst"}:
                continue
            if _CITATION_NEEDED_RE.search(text):
                return f"Unresolved [citation needed] marker in {name!r}."
            stripped = _MD_FENCE_RE.sub("", text)
            body_lines = [
                ln.strip()
                for ln in stripped.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if len("\n".join(body_lines)) < 20:
                continue
            has_citation = (
                bool(_WIKILINK_RE.search(stripped))
                or bool(_MD_LINK_RE.search(stripped))
                or bool(_CITATION_MARKER_RE.search(stripped))
            )
            if not has_citation:
                return f"Missing grounded citations or source links in {name!r}."
        return None

    def _check_double_entry_sum(
        self,
        _event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        for name, text in artifacts:
            ext = Path(name).suffix.lower()
            if not ext and text.lstrip().startswith(("{", "[")):
                ext = ".json"
            records = _collect_ledger_records(ext, text)
            if not records:
                continue
            debit_total = Decimal("0")
            credit_total = Decimal("0")
            signed_total = Decimal("0")
            saw_dc = False
            saw_signed = False
            for rec in records:
                d_raw = rec.get("debit") or rec.get("debits") or rec.get("debit_amount")
                c_raw = rec.get("credit") or rec.get("credits") or rec.get("credit_amount")
                if d_raw is not None or c_raw is not None:
                    saw_dc = True
                    debit_total += _parse_decimal_amount(d_raw) or Decimal("0")
                    credit_total += _parse_decimal_amount(c_raw) or Decimal("0")
                    continue
                side_raw = str(
                    rec.get("side") or rec.get("type") or rec.get("dc") or ""
                ).strip().lower()
                amt_val = _parse_decimal_amount(rec.get("amount"))
                if amt_val is None:
                    continue
                if side_raw in {"debit", "dr"}:
                    saw_dc = True
                    debit_total += abs(amt_val)
                elif side_raw in {"credit", "cr"}:
                    saw_dc = True
                    credit_total += abs(amt_val)
                else:
                    saw_signed = True
                    signed_total += amt_val
            if saw_dc and abs(debit_total - credit_total) > Decimal("0.0001"):
                return (
                    f"Double-entry imbalance in {name!r}: "
                    f"debits={debit_total} != credits={credit_total}."
                )
            if saw_signed and not saw_dc and abs(signed_total) > Decimal("0.0001"):
                return f"Unbalanced signed ledger entries in {name!r}: net={signed_total}."
        return None

    def _check_currency_precision(
        self,
        _event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        money_keys = (
            "debit",
            "debits",
            "debit_amount",
            "credit",
            "credits",
            "credit_amount",
            "amount",
            "balance",
            "total",
            "price",
            "payout",
            "fee",
            "tax",
        )
        for name, text in artifacts:
            ext = Path(name).suffix.lower()
            if not ext and text.lstrip().startswith(("{", "[")):
                ext = ".json"
            records = _collect_ledger_records(ext, text)
            for rec in records:
                currency = str(rec.get("currency") or rec.get("ccy") or "")
                for mkey in money_keys:
                    if mkey not in rec:
                        continue
                    val = _parse_decimal_amount(rec[mkey])
                    if val is not None and _exceeds_currency_precision(val, currency):
                        return (
                            f"Currency precision violation in {name!r} "
                            f"({mkey}={rec[mkey]!r})."
                        )
            for match in re.finditer(r"[$€£]\s*(\d+(?:,\d{3})*\.\d{3,})", text):
                val = _parse_decimal_amount(match.group(1))
                if val is not None and _exceeds_currency_precision(val):
                    return f"Currency precision violation in {name!r}: {match.group(0)!r}."
        return None

    def _check_required_clauses_present(
        self,
        event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        custom_required = event.metadata.get("required_clauses")
        default_groups: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("Liability", ("limitation of liability", "liability", "indemnif")),
            ("Termination", ("termination", "term and termination")),
            (
                "Governing Law",
                ("governing law", "jurisdiction", "choice of law", "dispute resolution"),
            ),
            (
                "Confidentiality",
                ("confidential", "non-disclosure", "nondisclosure", "warrant"),
            ),
        )
        for name, text in artifacts:
            if _PLACEHOLDER_RE.search(text):
                return f"Unresolved placeholder clause detected in {name!r}."
            lowered = text.lower()
            if isinstance(custom_required, list) and custom_required:
                missing = [
                    str(c)
                    for c in custom_required
                    if str(c).strip() and str(c).strip().lower() not in lowered
                ]
                if missing:
                    return (
                        f"Missing required legal clause(s) in {name!r}: "
                        f"{', '.join(missing)}."
                    )
                continue
            missing_groups = [
                label
                for label, keywords in default_groups
                if not any(kw in lowered for kw in keywords)
            ]
            if missing_groups:
                return (
                    f"Missing required legal clause(s) in {name!r}: "
                    f"{', '.join(missing_groups)}."
                )
        return None

    def _check_cross_references(
        self,
        _event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        for name, text in artifacts:
            defined_ids: set[str] = set()
            for m in _SECTION_DEF_RE.finditer(text):
                if m.group(3) is not None:
                    defined_ids.add(m.group(3).strip().upper())
                else:
                    kind = (m.group(1) or "").strip().lower()
                    ident = (m.group(2) or "").strip().upper()
                    if kind and ident:
                        defined_ids.add(f"{kind}:{ident}")
                    if ident:
                        defined_ids.add(ident)

            for m in _CROSS_REF_RE.finditer(text):
                full_span = m.group(0)
                if re.search(
                    r"\bof\s+(?:the|this)\s+.*(?:Act|Code|Statute|Regulation|GDPR|CCPA)\b",
                    full_span,
                    re.IGNORECASE,
                ):
                    continue
                kind = m.group(1).strip().lower()
                ident = m.group(2).strip().upper()
                if f"{kind}:{ident}" not in defined_ids and ident not in defined_ids:
                    return (
                        f"Broken internal cross-reference '{m.group(1)} {m.group(2)}' "
                        f"in {name!r}."
                    )

            for m in _SECTION_SYMBOL_REF_RE.finditer(text):
                ident = m.group(1).strip().upper()
                if ident not in defined_ids and f"section:{ident}" not in defined_ids:
                    return f"Broken internal cross-reference '§ {m.group(1)}' in {name!r}."
        return None

    def _run_builtin_check(
        self,
        check_name: str,
        event: GateEvent,
        artifacts: list[tuple[str, str]],
    ) -> str | None:
        if not artifacts:
            return None
        handlers = {
            "verify_wikilinks_exist": self._check_wikilinks_exist,
            "verify_citations_present": self._check_citations_present,
            "verify_double_entry_sum": self._check_double_entry_sum,
            "verify_currency_precision": self._check_currency_precision,
            "verify_required_clauses_present": self._check_required_clauses_present,
            "verify_cross_references": self._check_cross_references,
        }
        handler = handlers.get(check_name)
        if handler is None:
            return None
        return handler(event, artifacts)

    async def _handle_gate4_failure_verdict(
        self,
        event: GateEvent,
        error_msg: str,
        *,
        unaddressed_clauses: list[str] | None = None,
        cloud_egress_allowed: bool = True,
        t0: float,
    ) -> GateVerdict:
        clauses = unaddressed_clauses or []
        if self.healing is not None and hasattr(self.healing, "handle_gate4_failure"):
            verdict: GateVerdict = await _maybe_await(
                self.healing.handle_gate4_failure(event, error_msg)
            )
            updates: dict[str, Any] = {
                "cloud_egress_allowed": cloud_egress_allowed,
                "latency_ms": max(verdict.latency_ms, (time.perf_counter() - t0) * 1000.0),
            }
            if clauses and not verdict.unaddressed_clauses:
                updates["unaddressed_clauses"] = clauses
            return verdict.model_copy(update=updates)

        if event.strike_count == 0 and event.current_tier in {ModelTier.FLOOR, ModelTier.MID}:
            next_tier = (
                ModelTier.MID if event.current_tier == ModelTier.FLOOR else ModelTier.FRONTIER
            )
            next_model = self._model_for_tier(next_tier)
            return GateVerdict(
                action=VerdictAction.PROMOTE,
                gate=GateType.STOP_AUDIT,
                target_tier=next_tier,
                updated_model=next_model,
                unaddressed_clauses=clauses,
                reason=f"Gate 4 failure on {event.current_tier.value} (strike 0): {error_msg}",
                cloud_egress_allowed=cloud_egress_allowed,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        strike = max(1, event.strike_count + 1)
        receipt = ViolationReceipt(
            rule_id="GATE4_TRUTH_ORACLE_FAILURE",
            score=1.0,
            stage="deterministic_2a",
            remediation=error_msg,
            strike_count=strike,
            escalate_to_human=event.strike_count >= 1 or event.current_tier == ModelTier.FRONTIER,
            violated_path=event.touched_files[0] if event.touched_files else None,
        )
        return GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.STOP_AUDIT,
            violation_receipt=receipt,
            unaddressed_clauses=clauses,
            reason=error_msg,
            cloud_egress_allowed=cloud_egress_allowed,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def evaluate_gate4_stop(self, event: GateEvent) -> GateVerdict:
        """Execute sub-second truth oracle and directive completeness verification."""
        t0 = time.perf_counter()

        # 1. Sub-second Truth Oracle: extension commands + built-in cartridge checks
        cmd_error = await self._run_extension_oracle_commands(event)
        if cmd_error is not None:
            return await self._handle_gate4_failure_verdict(event, cmd_error, t0=t0)

        artifacts = self._gather_oracle_artifacts(event)
        for check_name in self.cartridge.truth_oracle.checks:
            check_error = self._run_builtin_check(check_name, event, artifacts)
            if check_error is not None:
                return await self._handle_gate4_failure_verdict(event, check_error, t0=t0)

        # 2. Directive completeness verification
        clauses = _extract_atomic_clauses(event.prompt_text or "")
        if not clauses:
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.STOP_AUDIT,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        evidence_parts: list[str] = []
        if event.diff_text and event.diff_text.strip():
            evidence_parts.append(event.diff_text)
        if event.touched_files:
            evidence_parts.append("\n".join(event.touched_files))
        for name, content in artifacts:
            if content and content not in (event.diff_text or ""):
                evidence_parts.append(f"{name}\n{content[:4000]}")
        evidence_text = "\n\n".join(evidence_parts).strip()

        cloud_egress_allowed = self._check_air_gap(
            text=f"{event.prompt_text or ''}\n{evidence_text}",
            workspace_root=event.workspace_root,
            paths=event.touched_files,
        )
        active_driver = self._select_active_driver(cloud_egress_allowed)

        if not evidence_text:
            p_completeness = 0.0
            unaddressed_clauses = list(clauses)
        else:
            clause_heads = [
                ClassifierHead(
                    head_id=f"clause:{idx}",
                    candidate_text=clause,
                    category=HeadCategory.CANDIDATE,
                    metadata={"clause_index": idx},
                )
                for idx, clause in enumerate(clauses)
            ]
            driver_probs = await self._classify_with_budget(
                active_driver,
                evidence_text,
                clause_heads,
                cloud_egress_allowed=cloud_egress_allowed,
            )

            clause_scores: list[float] = []
            for idx, clause in enumerate(clauses):
                dp = driver_probs.get(f"clause:{idx}", 0.0)
                if type(active_driver) is ZeroDepDriver:
                    cov = _score_zero_dep_clause_coverage(clause, evidence_text)
                    clause_scores.append(min(1.0, 0.30 * dp + 0.70 * cov))
                else:
                    clause_scores.append(dp)

            p_completeness = sum(clause_scores) / len(clause_scores)
            unaddressed_clauses = [
                clauses[idx]
                for idx, score in enumerate(clause_scores)
                if score < 0.60
            ]
            if (
                0.60 <= p_completeness < self.thresholds.gate4_completeness_min
                and not unaddressed_clauses
            ):
                unaddressed_clauses = [
                    clauses[idx]
                    for idx, score in enumerate(clause_scores)
                    if score < self.thresholds.gate4_completeness_min
                ]

        if p_completeness >= self.thresholds.gate4_completeness_min:
            if (
                self.healing is not None
                and event.touched_files
                and hasattr(self.healing, "record_file_outcome")
            ):
                fn = self.healing.record_file_outcome
                try:
                    sig = inspect.signature(fn)
                    if "workspace_root" in sig.parameters:
                        await _maybe_await(
                            fn(
                                list(event.touched_files),
                                passed=True,
                                workspace_root=event.workspace_root,
                            )
                        )
                    else:
                        await _maybe_await(fn(list(event.touched_files), passed=True))
                except (TypeError, ValueError):
                    pass
            return GateVerdict(
                action=VerdictAction.ALLOW,
                gate=GateType.STOP_AUDIT,
                cloud_egress_allowed=cloud_egress_allowed,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        if (
            0.60 <= p_completeness < self.thresholds.gate4_completeness_min
            and not event.continuation_active
        ):
            return GateVerdict(
                action=VerdictAction.AUGMENT,
                gate=GateType.STOP_AUDIT,
                unaddressed_clauses=unaddressed_clauses,
                reason=(
                    f"Directive completeness {p_completeness:.2f} < "
                    f"{self.thresholds.gate4_completeness_min:.2f}; address remaining clauses."
                ),
                cloud_egress_allowed=cloud_egress_allowed,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        return await self._handle_gate4_failure_verdict(
            event,
            f"Incomplete directive coverage (p_completeness={p_completeness:.2f}).",
            unaddressed_clauses=unaddressed_clauses,
            cloud_egress_allowed=cloud_egress_allowed,
            t0=t0,
        )


__all__ = [
    "GateEngine",
    "ThresholdConfig",
    "TierModelConfig",
    "load_cartridge",
]
