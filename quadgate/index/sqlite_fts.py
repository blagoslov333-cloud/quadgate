"""SQLite FTS5 + trigram Pass-0 candidate store & Tier-1 air-gap filter."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from quadgate.contracts import IndexedGist

_CAMEL_BOUNDARY_1: Final[re.Pattern[str]] = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_BOUNDARY_2: Final[re.Pattern[str]] = re.compile(r"([A-Z]+)([A-Z][a-z])")
_QUERY_CHUNK_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]+")

_MAX_QUERY_CHUNKS: Final[int] = 16
_MAX_FTS_TERMS: Final[int] = 32
_TRIGRAM_SCAN_CAP: Final[int] = 128

_NATURAL_LANGUAGE_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "may",
        "might",
        "my",
        "not",
        "of",
        "on",
        "or",
        "please",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

# Pre-compiled Pass-0 Tier-1 Air-Gap detectors (< 2 ms deterministic check).
_AIRGAP_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(?:private|confidential|airgap|tier1-private)\]",
    re.IGNORECASE,
)
_AIRGAP_TAG_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*+#{1,6}+[ \t]++\[(?:private|confidential|airgap|tier1-private)\]",
    re.IGNORECASE | re.MULTILINE,
)
_AIRGAP_PRIVATE_DIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"vault/private(?:/|$|[^A-Za-z0-9_])"
    r"|(?:private_vault|secrets|confidential|privileged|attorney_client|payroll|tax_ids)/"
    r")",
    re.IGNORECASE,
)
_PEM_EXT_RE: Final[re.Pattern[str]] = re.compile(r"\.pem(?=$|[^a-z0-9_-])")
_SSH_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"id_(?:rsa|ed25519|ecdsa|dsa)(?!\.pub\b)(?=$|[^a-z0-9_.-])"
)
_KEY_BOUNDARY_AFTER: Final[frozenset[str]] = frozenset(
    "0123456789abcdefghijklmnopqrstuvwxyz_-"
)
_KEY_TOKEN_DELIMS: Final[frozenset[str]] = frozenset(" \t\r\n\"'`,;:(=[{<")
_VALID_KEY_PATH_CHARS: Final[frozenset[str]] = frozenset(
    "0123456789abcdefghijklmnopqrstuvwxyz._/-~:"
)
_EXPLICIT_KEY_STEMS: Final[frozenset[str]] = frozenset(
    {
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "privkey",
        "private",
        "private_key",
        "private-key",
        "tls",
        "tls_key",
        "tls-key",
        "ssl",
        "ssl_key",
        "ssl-key",
        "ca",
        "signing",
    }
)
_EXPLICIT_KEY_PREFIXES: Final[tuple[str, ...]] = (
    "tls_",
    "tls-",
    "ssl_",
    "ssl-",
    "priv_",
    "priv-",
    "private_",
    "private-",
    "ca_",
    "ca-",
    "signing_",
    "signing-",
)
_AIRGAP_ENV_FILE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])\.env"
    r"(?!\.(?:example|sample|template)\b)"
    r"(?:\.[A-Za-z0-9_-]+)?"
    r"(?=$|[^A-Za-z0-9_])",
    re.IGNORECASE,
)
_AIRGAP_SSN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9A-Za-z_-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![0-9A-Za-z_-])"
)
# Fast literal-prefixed C scanner for SSN candidates (avoids lookbehind disabling fastsearch).
_SSN_MID_RE: Final[re.Pattern[str]] = re.compile(
    r"-(?!00)[0-9]{2}-(?!0000)[0-9]{4}(?![0-9A-Za-z_-])"
)
_AIRGAP_HIPAA_PHI_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"PHI[-_]MRN(?:[-_][A-Za-z0-9]++)*+(?![A-Za-z0-9_])"
    r"|(?:Patient\s++(?:MRN|ID|DOB)|Medical\s++Record\s++(?:Number|No\.?|#)|MRN)\s*+[:=]\s*+"
    r"(?!(?:REDACTED|NONE|N/A|NA|NULL|TBD|TODO|EXAMPLE|UNKNOWN|XXX)\b)"
    r"(?=[A-Za-z0-9_/-]{0,32}\d)[A-Za-z0-9][A-Za-z0-9_/-]{2,64}(?![A-Za-z0-9_-])"
    r"|Patient\s++SSN\s*+[:=]\s*+"
    r"(?!000|666|9\d\d)\d{3}-?(?!00)\d{2}-?(?!0000)\d{4}(?![A-Za-z0-9_-])"
    r")",
    re.IGNORECASE,
)
_AIRGAP_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_-])(?>"
    r"sk-[A-Za-z0-9_-]{20,}+"
    r"|AKIA[0-9A-Z]{16}"
    r"|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{22,}+"
    r"|AIza[0-9A-Za-z_-]{35,}+"
    r"|AQ\.[A-Za-z0-9_-]{30,}+"
    r"|xox[baprs]-[0-9A-Za-z-]{10,}+"
    r")(?![A-Za-z0-9_-])"
    r"|-----BEGIN\s++(?:(?!PRIVATE\s++KEY\b)[A-Z0-9]++\s++)*+PRIVATE\s++KEY(?:\s++BLOCK)?-----"
)

_EXT_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "py",
        "pyi",
        "ts",
        "tsx",
        "js",
        "jsx",
        "rs",
        "go",
        "md",
        "txt",
        "csv",
        "json",
        "yaml",
        "yml",
        "rst",
    }
)
_BINARY_SKIP_EXTS: Final[frozenset[str]] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".zip",
        ".gz",
        ".tar",
        ".whl",
        ".so",
        ".dll",
        ".exe",
        ".pyd",
        ".pyc",
        ".sqlite",
        ".db",
        ".onnx",
        ".bin",
    }
)
_SENSITIVE_KEY_BASENAMES: Final[frozenset[str]] = frozenset(
    {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"}
)
_PRIVATE_DIR_HINTS: Final[tuple[str, ...]] = (
    "vault/private",
    "private_vault",
    "secrets/",
    "confidential/",
    "privileged/",
    "attorney_client/",
    "payroll/",
    "tax_ids/",
)
_SSN_BOUNDARY_CHARS: Final[frozenset[str]] = frozenset(
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
)

_SELECT_GISTS_COLUMNS: Final[str] = (
    "g.workspace_hash, g.rel_path, g.content_hash, g.language, g.semantic_gist, "
    "g.symbols_json, g.imports_json, g.fan_in_count, g.mtime_ns, g.is_private_tier1"
)

_FTS_EXACT_OR_PREFIX_SQL: Final[str] = f"""
SELECT {_SELECT_GISTS_COLUMNS}, bm25(gists_fts, 4.0, 3.0, 1.5, 1.0) AS bm25_score
FROM gists_fts f
JOIN gists g ON g.id = f.rowid
WHERE gists_fts MATCH ? AND g.workspace_hash = ?
ORDER BY bm25_score ASC, g.fan_in_count DESC, g.id ASC
LIMIT ?
"""

_TRIGRAM_CANDIDATE_SQL: Final[str] = f"""
SELECT {_SELECT_GISTS_COLUMNS}
FROM gists_trigram t
JOIN gists g ON g.id = t.rowid
WHERE gists_trigram MATCH ? AND g.workspace_hash = ?
LIMIT ?
"""


class Pass0CandidateResult(BaseModel):
    """Ranked Pass-0 local retrieval result with Tier-1 air-gap flag."""

    model_config = ConfigDict(extra="forbid")

    gists: list[IndexedGist] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    cloud_egress_allowed: bool = True
    query_latency_ms: float = Field(default=0.0, ge=0.0)


def _has_valid_ssn(text: str) -> bool:
    """Fast literal-anchored SSN detector avoiding full-buffer lookbehind regex scans."""
    for match in _SSN_MID_RE.finditer(text):
        start = match.start()
        if start < 3:
            continue
        area = text[start - 3 : start]
        if not (area.isdigit() and area.isascii()):
            continue
        if start >= 4 and text[start - 4] in _SSN_BOUNDARY_CHARS:
            continue
        if area[0] == "9" or area in ("000", "666"):
            continue
        return True
    return False


def is_sensitive_content(text: str, *, include_tags: bool = False) -> bool:
    """Return True if text contains SSNs, HIPAA/PHI/MRN, private keys, or secret tokens."""
    if not text:
        return False
    if "-" in text and _has_valid_ssn(text):
        return True
    if (
        "sk-" in text
        or "AKIA" in text
        or "gh" in text
        or "AIza" in text
        or "AQ." in text
        or "xox" in text
        or "-----BEGIN" in text
    ) and _AIRGAP_SECRET_RE.search(text):
        return True
    if "[" in text:
        if "#" in text and _AIRGAP_TAG_HEADING_RE.search(text):
            return True
        if include_tags and _AIRGAP_TAG_RE.search(text):
            return True
    low = text.lower()
    if ("mrn" in low or "patient" in low or "medical" in low) and _AIRGAP_HIPAA_PHI_RE.search(
        text
    ):
        return True
    return False


def _has_sensitive_key_ref(lowered: str) -> bool:
    """Fast literal-anchored detector for .pem, path/explicit .key, and SSH private key refs."""
    if ".pem" in lowered:
        for match in _PEM_EXT_RE.finditer(lowered):
            pos = match.start()
            if pos > 0 and (lowered[pos - 1].isalnum() or lowered[pos - 1] in "_-"):
                i = pos - 1
                limit = max(0, pos - 128)
                while i >= limit and lowered[i] not in _KEY_TOKEN_DELIMS:
                    i -= 1
                token = lowered[i + 1 : pos]
                if token and all(ch in _VALID_KEY_PATH_CHARS for ch in token):
                    return True
    if "id_" in lowered:
        for match in _SSH_KEY_RE.finditer(lowered):
            pos = match.start()
            if pos == 0 or lowered[pos - 1] == "/" or lowered[pos - 1] in _KEY_TOKEN_DELIMS:
                return True
    n = len(lowered)
    pos = lowered.find(".key", 1)
    while pos != -1:
        end_pos = pos + 4
        if end_pos >= n or lowered[end_pos] not in _KEY_BOUNDARY_AFTER:
            i = pos - 1
            limit = max(0, pos - 128)
            while i >= limit and lowered[i] not in _KEY_TOKEN_DELIMS:
                i -= 1
            token = lowered[i + 1 : pos]
            if token and all(ch in _VALID_KEY_PATH_CHARS for ch in token):
                if "/" in token:
                    _, _, stem = token.rpartition("/")
                    if stem and (stem[0].isalnum() or stem[0] == "_"):
                        return True
                elif token in _EXPLICIT_KEY_STEMS or token.startswith(_EXPLICIT_KEY_PREFIXES):
                    return True
        pos = lowered.find(".key", end_pos)
    return False


def is_sensitive_path(path_or_text: str, workspace_root: str = "") -> bool:
    """Return True if path_or_text or workspace_root references a Tier-1 path, key, .env, or tag."""
    for candidate, is_root in ((path_or_text, False), (workspace_root, True)):
        if not candidate:
            continue
        if "[" in candidate and _AIRGAP_TAG_RE.search(candidate):
            return True
        normalized = candidate.replace("\\", "/") if "\\" in candidate else candidate
        lowered = normalized.lower()
        if (is_root or "/" in lowered) and any(h in lowered for h in _PRIVATE_DIR_HINTS):
            normalized_dir = (
                f"{normalized}/" if (is_root and not normalized.endswith("/")) else normalized
            )
            if _AIRGAP_PRIVATE_DIR_RE.search(normalized_dir):
                return True
        if ".env" in lowered and _AIRGAP_ENV_FILE_RE.search(normalized):
            return True
        if not is_root and _has_sensitive_key_ref(lowered):
            return True
    return False


def check_text_air_gap(text: str, workspace_root: str = "") -> bool:
    """Return False if Tier-1 private paths, tags, PII/HIPAA, or secrets prohibit cloud egress."""
    if is_sensitive_path(text, workspace_root):
        return False
    if is_sensitive_content(text, include_tags=True):
        return False
    if workspace_root and is_sensitive_content(workspace_root, include_tags=True):
        return False
    return True


def is_sensitive_file(
    file_path: Path | str,
    workspace_root: str = "",
    *,
    max_bytes: int = 262_144,
) -> bool:
    """Return True if file_path is sensitive by path or by bounded inline content scan."""
    path_obj = Path(file_path)
    lower_name = path_obj.name.lower()
    if lower_name in _SENSITIVE_KEY_BASENAMES or path_obj.suffix.lower() in {".pem", ".key"}:
        return True
    if is_sensitive_path(str(file_path), workspace_root):
        return True
    if path_obj.suffix.lower() in _BINARY_SKIP_EXTS:
        return False
    try:
        if not path_obj.is_file():
            return False
        with path_obj.open("rb") as fh:
            raw = fh.read(max(1, max_bytes))
    except OSError:
        return False
    if not raw or b"\x00" in raw[:4096]:
        return False
    decoded = raw.decode("utf-8", errors="ignore")
    return is_sensitive_content(decoded, include_tags=False)


def _split_identifier_parts(chunk: str) -> list[str]:
    """Split snake_case and camelCase/PascalCase identifiers into lowercase tokens."""
    parts: list[str] = []
    for sub in chunk.split("_"):
        if not sub:
            continue
        spaced = _CAMEL_BOUNDARY_2.sub(r"\1 \2", _CAMEL_BOUNDARY_1.sub(r"\1 \2", sub))
        for token in spaced.split():
            parts.append(token.lower())
    return parts


def _build_expanded_tokens(rel_path: str, symbols: list[str], semantic_gist: str) -> str:
    """Expand compound identifiers into space-separated tokens for FTS5 indexing."""
    seen: set[str] = set()
    expanded: list[str] = []

    def _add(tok: str) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            expanded.append(tok)

    for source in (rel_path, " ".join(symbols), semantic_gist):
        for chunk in _QUERY_CHUNK_RE.findall(source):
            parts = _split_identifier_parts(chunk)
            if len(parts) > 1:
                for part in parts:
                    _add(part)
                _add("".join(parts))

    return " ".join(expanded)


def _quote_fts_term(term: str) -> str:
    """Wrap a sanitized term in FTS5 double quotes so operators/punctuation cannot fail."""
    cleaned = term.replace('"', "")
    return f'"{cleaned}"'


def _extract_query_chunks(query: str) -> list[str]:
    """Extract ordered unique alphanumeric/underscore chunks from raw query text."""
    seen: set[str] = set()
    chunks: list[str] = []
    for raw in _QUERY_CHUNK_RE.findall(query):
        low = raw.lower()
        if low not in seen:
            seen.add(low)
            chunks.append(raw)
            if len(chunks) >= _MAX_QUERY_CHUNKS:
                break
    return chunks


def _build_chunk_expression(chunk: str) -> tuple[str, list[str]]:
    """Build a safe FTS5 sub-expression and flat token list for one identifier chunk."""
    low = chunk.lower()
    parts = _split_identifier_parts(chunk)
    variants: list[str] = []
    flat_tokens: list[str] = []

    def _add_flat(tok: str) -> None:
        if tok and tok not in flat_tokens:
            flat_tokens.append(tok)

    _add_flat(low)
    variants.append(_quote_fts_term(low))

    if len(parts) > 1:
        joined = "".join(parts)
        if joined != low:
            _add_flat(joined)
            variants.append(_quote_fts_term(joined))
        for part in parts:
            _add_flat(part)
        part_expr = " AND ".join(_quote_fts_term(part) for part in parts)
        variants.append(f"({part_expr})")

    if len(variants) == 1:
        return variants[0], flat_tokens
    return f"({' OR '.join(variants)})", flat_tokens


def _build_fts_queries(
    query: str,
) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """Construct safe FTS5 (AND, OR, prefix, trigram) expressions and flat search terms."""
    chunks = _extract_query_chunks(query)
    if not chunks:
        return None, None, None, None, []

    content_chunks = [c for c in chunks if c.lower() not in _NATURAL_LANGUAGE_STOPWORDS]
    active_chunks = content_chunks if content_chunks else chunks

    and_clauses: list[str] = []
    or_tokens: list[str] = []
    seen_or: set[str] = set()

    for chunk in active_chunks:
        clause, flat = _build_chunk_expression(chunk)
        and_clauses.append(clause)
        for tok in flat:
            if tok not in seen_or and len(or_tokens) < _MAX_FTS_TERMS:
                seen_or.add(tok)
                or_tokens.append(tok)

    and_expr = " AND ".join(and_clauses) if len(and_clauses) > 1 else and_clauses[0]
    or_expr = " OR ".join(_quote_fts_term(tok) for tok in or_tokens)

    prefix_tokens = [tok for tok in or_tokens if len(tok) >= 3]
    prefix_expr = (
        " OR ".join(f"{_quote_fts_term(tok)}*" for tok in prefix_tokens)
        if prefix_tokens
        else None
    )

    trigram_terms: list[str] = []
    seen_tri: set[str] = set()
    for chunk in active_chunks:
        low = chunk.lower()
        if len(low) >= 3 and low not in seen_tri:
            seen_tri.add(low)
            trigram_terms.append(low)
    for tok in or_tokens:
        if len(tok) >= 3 and tok not in seen_tri:
            seen_tri.add(tok)
            trigram_terms.append(tok)
    trigram_terms = trigram_terms[:_MAX_QUERY_CHUNKS]
    trigram_expr = (
        " OR ".join(_quote_fts_term(term) for term in trigram_terms)
        if trigram_terms
        else None
    )

    return and_expr, or_expr, prefix_expr, trigram_expr, trigram_terms


def _as_int(value: object) -> int:
    """Convert a SQLite integer cell to ``int`` with explicit type narrowing."""
    if isinstance(value, int):
        return int(value)
    if isinstance(value, (float, str, bytes, bytearray)):
        return int(value)
    raise TypeError(f"Expected int-compatible SQLite column, got {type(value).__name__}")


def _as_float(value: object) -> float:
    """Convert a SQLite numeric cell to ``float`` with explicit type narrowing."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"Expected float-compatible SQLite column, got {type(value).__name__}")


def _row_to_gist(row: tuple[object, ...]) -> IndexedGist:
    """Fast-construct an IndexedGist from a validated SQLite row tuple."""
    return IndexedGist.model_construct(
        workspace_hash=str(row[0]),
        rel_path=str(row[1]),
        content_hash=str(row[2]),
        language=str(row[3]),
        semantic_gist=str(row[4]),
        symbols=json.loads(str(row[5])),
        imports=json.loads(str(row[6])),
        fan_in_count=_as_int(row[7]),
        mtime_ns=_as_int(row[8]),
        is_private_tier1=bool(row[9]),
    )


def _rank_trigram_rows(
    rows: list[tuple[object, ...]],
    trigram_terms: list[str],
    limit: int,
) -> tuple[list[tuple[object, ...]], list[float]]:
    """Score bounded trigram candidate rows in memory by field importance and fan-in."""
    scored: list[tuple[tuple[int, int, str], float, tuple[object, ...]]] = []
    for row in rows:
        rel_path_low = str(row[1]).lower()
        symbols_low = str(row[5]).lower()
        gist_low = str(row[4]).lower()
        fan_in = _as_int(row[7])

        score = 0
        for term in trigram_terms:
            if term in rel_path_low:
                score += 100
            if term in symbols_low:
                score += 60
            if term in gist_low:
                score += 20
        scored.append(((-score, -fan_in, rel_path_low), float(score), row))

    scored.sort(key=lambda item: item[0])
    top = scored[:limit]
    return [item[2] for item in top], [item[1] for item in top]


def _normalize_pass0_scores(
    query: str,
    gists: list[IndexedGist],
    raw_scores: list[float],
    match_mode: str,
    total_ws_docs: int,
) -> dict[str, float]:
    """Normalize Pass-0 BM25/trigram scores cleanly into [0.0, 1.0] keyed by rel_path."""
    if not gists:
        return {}

    chunks = [
        c for c in _extract_query_chunks(query) if c.lower() not in _NATURAL_LANGUAGE_STOPWORDS
    ]
    meaningful: list[tuple[str, list[str]]] = []
    for chunk in chunks:
        low = chunk.lower()
        if low in _EXT_STOPWORDS:
            continue
        parts = [
            p
            for p in _split_identifier_parts(chunk)
            if p not in _NATURAL_LANGUAGE_STOPWORDS and p not in _EXT_STOPWORDS and len(p) >= 2
        ]
        meaningful.append((low, parts))
    if not meaningful:
        meaningful = [(c.lower(), _split_identifier_parts(c)) for c in chunks]

    query_low = query.lower().replace("\\", "/")
    mags = [max(0.0, float(val)) for val in raw_scores]
    max_mag = max(mags) if mags else 0.0
    idf_scale = max(0.5, math.log(max(3, total_ws_docs)))
    max_weight = float(len(meaningful)) * 3.0 if meaningful else 1.0

    normalized: dict[str, float] = {}
    for gist, mag in zip(gists, mags):
        rel_low = gist.rel_path.lower()
        sym_low = " ".join(gist.symbols).lower()
        gist_low = gist.semantic_gist.lower()

        hit_weight = 0.0
        for full_q, parts in meaningful:
            in_path = full_q in rel_low or (len(parts) > 1 and all(p in rel_low for p in parts))
            in_sym = full_q in sym_low or (len(parts) > 1 and all(p in sym_low for p in parts))
            in_gist = full_q in gist_low or (len(parts) > 1 and all(p in gist_low for p in parts))
            if in_path:
                hit_weight += 3.0
            elif in_sym:
                hit_weight += 2.2
            elif in_gist:
                hit_weight += 1.2
            elif parts:
                part_hits = sum(
                    1 for p in parts if p in rel_low or p in sym_low or p in gist_low
                )
                hit_weight += 1.0 * (part_hits / len(parts))
        cov = min(1.0, hit_weight / max_weight)

        stem = rel_low.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        path_mentioned = (rel_low in query_low) or (len(stem) >= 4 and stem in query_low)

        if match_mode == "TRIGRAM":
            bm_sat = 1.0 - math.exp(-mag / 180.0)
            rel_norm = (mag / max_mag) if max_mag > 0.0 else 0.0
            score = 0.55 * bm_sat + 0.25 * rel_norm + 0.20 * cov
        else:
            bm_sat = 1.0 - math.exp(-mag / (3.2 * idf_scale))
            rel_norm = (mag / max_mag) if max_mag > 1e-4 else 0.0
            if max_mag <= 1e-4 and total_ws_docs <= 3:
                score = (0.45 + 0.55 * cov) if cov > 0.0 else 0.25
            else:
                score = 0.55 * bm_sat + 0.20 * rel_norm + 0.25 * cov
            if match_mode == "AND" and cov >= 0.20:
                score = max(score, 0.80)
            if not gist.is_private_tier1 and path_mentioned and mag > 4.0:
                score = min(1.0, max(score, 0.72 + 0.20 * bm_sat))

        normalized[gist.rel_path] = round(min(1.0, max(0.0, score)), 4)

    return normalized


class SqliteFtsStore:
    """WAL-mode SQLite FTS5 + trigram candidate store (<4ms lookup)."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._initialized: bool = False
        self._lock = threading.RLock()
        self._ws_counts: dict[str, int] = {}

    def __enter__(self) -> SqliteFtsStore:
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _ensure_initialized(self) -> sqlite3.Connection:
        if not self._initialized or self._conn is None:
            self.initialize()
        assert self._conn is not None
        return self._conn

    def initialize(self) -> None:
        """Create WAL tables, FTS5 virtual tables, and trigram indices."""
        with self._lock:
            if self._initialized and self._conn is not None:
                return

            if isinstance(self.db_path, Path):
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                target = str(self.db_path)
            else:
                target = self.db_path
                if target != ":memory:" and not target.startswith("file:"):
                    Path(target).parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(target, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            conn.execute("PRAGMA cache_size=-16000;")
            conn.execute("PRAGMA busy_timeout=5000;")

            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gists (
                        id INTEGER PRIMARY KEY,
                        workspace_hash TEXT NOT NULL,
                        rel_path TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        language TEXT NOT NULL,
                        semantic_gist TEXT NOT NULL,
                        symbols_json TEXT NOT NULL,
                        imports_json TEXT NOT NULL,
                        fan_in_count INTEGER NOT NULL DEFAULT 0,
                        mtime_ns INTEGER NOT NULL DEFAULT 0,
                        is_private_tier1 INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(workspace_hash, rel_path)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS gists_fts USING fts5(
                        rel_path,
                        symbols,
                        semantic_gist,
                        expanded_tokens,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS gists_trigram USING fts5(
                        rel_path,
                        symbols,
                        semantic_gist,
                        tokenize='trigram'
                    )
                    """
                )

            self._conn = conn
            self._initialized = True

    def upsert_gists(self, gists: list[IndexedGist]) -> int:
        """Insert or update content-addressed file gists."""
        if not gists:
            return 0

        with self._lock:
            conn = self._ensure_initialized()

            # Deduplicate within the incoming batch (last entry wins per file).
            deduped: dict[tuple[str, str], IndexedGist] = {}
            for gist in gists:
                deduped[(gist.workspace_hash, gist.rel_path)] = gist

            workspaces = {key[0] for key in deduped}
            existing_by_key: dict[
                tuple[str, str],
                tuple[int, str, str, str, str, str, int, int, int],
            ] = {}
            for ws_hash in workspaces:
                rows = conn.execute(
                    """
                    SELECT rel_path, id, content_hash, language, semantic_gist,
                           symbols_json, imports_json, fan_in_count, mtime_ns, is_private_tier1
                    FROM gists
                    WHERE workspace_hash = ?
                    """,
                    (ws_hash,),
                ).fetchall()
                for r in rows:
                    existing_by_key[(ws_hash, str(r[0]))] = (
                        int(r[1]),
                        str(r[2]),
                        str(r[3]),
                        str(r[4]),
                        str(r[5]),
                        str(r[6]),
                        int(r[7]),
                        int(r[8]),
                        int(r[9]),
                    )

            written = 0
            with conn:
                for key, gist in deduped.items():
                    symbols_json = json.dumps(gist.symbols, separators=(",", ":"))
                    imports_json = json.dumps(gist.imports, separators=(",", ":"))
                    is_private_int = 1 if gist.is_private_tier1 else 0
                    existing = existing_by_key.get(key)

                    if existing is None:
                        cur = conn.execute(
                            """
                            INSERT INTO gists (
                                workspace_hash, rel_path, content_hash, language,
                                semantic_gist, symbols_json, imports_json,
                                fan_in_count, mtime_ns, is_private_tier1
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                gist.workspace_hash,
                                gist.rel_path,
                                gist.content_hash,
                                gist.language,
                                gist.semantic_gist,
                                symbols_json,
                                imports_json,
                                gist.fan_in_count,
                                gist.mtime_ns,
                                is_private_int,
                            ),
                        )
                        last_row_id = cur.lastrowid
                        if last_row_id is None:
                            raise RuntimeError("SQLite INSERT missing lastrowid")
                        row_id = int(last_row_id)
                        symbols_str = " ".join(gist.symbols)
                        expanded = _build_expanded_tokens(
                            gist.rel_path, gist.symbols, gist.semantic_gist
                        )
                        conn.execute(
                            """
                            INSERT INTO gists_fts (
                                rowid, rel_path, symbols, semantic_gist, expanded_tokens
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (row_id, gist.rel_path, symbols_str, gist.semantic_gist, expanded),
                        )
                        conn.execute(
                            """
                            INSERT INTO gists_trigram (
                                rowid, rel_path, symbols, semantic_gist
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (row_id, gist.rel_path, symbols_str, gist.semantic_gist),
                        )
                        written += 1
                        continue

                    (
                        row_id,
                        old_content_hash,
                        old_language,
                        old_semantic_gist,
                        old_symbols_json,
                        old_imports_json,
                        old_fan_in,
                        old_mtime_ns,
                        old_is_private,
                    ) = existing

                    if (
                        old_content_hash == gist.content_hash
                        and old_language == gist.language
                        and old_semantic_gist == gist.semantic_gist
                        and old_symbols_json == symbols_json
                        and old_imports_json == imports_json
                        and old_fan_in == gist.fan_in_count
                        and old_mtime_ns == gist.mtime_ns
                        and old_is_private == is_private_int
                    ):
                        continue

                    conn.execute(
                        """
                        UPDATE gists
                        SET content_hash = ?,
                            language = ?,
                            semantic_gist = ?,
                            symbols_json = ?,
                            imports_json = ?,
                            fan_in_count = ?,
                            mtime_ns = ?,
                            is_private_tier1 = ?
                        WHERE id = ?
                        """,
                        (
                            gist.content_hash,
                            gist.language,
                            gist.semantic_gist,
                            symbols_json,
                            imports_json,
                            gist.fan_in_count,
                            gist.mtime_ns,
                            is_private_int,
                            row_id,
                        ),
                    )

                    fts_changed = (
                        old_content_hash != gist.content_hash
                        or old_semantic_gist != gist.semantic_gist
                        or old_symbols_json != symbols_json
                    )
                    if fts_changed:
                        symbols_str = " ".join(gist.symbols)
                        expanded = _build_expanded_tokens(
                            gist.rel_path, gist.symbols, gist.semantic_gist
                        )
                        conn.execute("DELETE FROM gists_fts WHERE rowid = ?", (row_id,))
                        conn.execute(
                            """
                            INSERT INTO gists_fts (
                                rowid, rel_path, symbols, semantic_gist, expanded_tokens
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (row_id, gist.rel_path, symbols_str, gist.semantic_gist, expanded),
                        )
                        conn.execute("DELETE FROM gists_trigram WHERE rowid = ?", (row_id,))
                        conn.execute(
                            """
                            INSERT INTO gists_trigram (
                                rowid, rel_path, symbols, semantic_gist
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (row_id, gist.rel_path, symbols_str, gist.semantic_gist),
                        )

                    written += 1

            for ws_hash in workspaces:
                self._ws_counts.pop(ws_hash, None)
            return written

    def check_air_gap(self, text: str, workspace_root: str = "") -> bool:
        """Return False if Tier-1 paths, PII/HIPAA/PHI, or secrets prohibit cloud egress."""
        return check_text_air_gap(text, workspace_root)

    def has_private_gists(self, workspace_hash: str) -> bool:
        """Return True if any indexed gist for workspace_hash is flagged Tier-1 private."""
        with self._lock:
            conn = self._ensure_initialized()
            row = conn.execute(
                "SELECT 1 FROM gists WHERE workspace_hash = ? AND is_private_tier1 = 1 LIMIT 1",
                (workspace_hash,),
            ).fetchone()
            return row is not None

    def search_candidates(
        self,
        query: str,
        workspace_hash: str,
        limit: int = 32,
    ) -> Pass0CandidateResult:
        """Retrieve top-N candidates via BM25 + symbol-token hybrid search."""
        t0 = time.perf_counter()
        cloud_egress_allowed = self.check_air_gap(query)

        with self._lock:
            conn = self._ensure_initialized()

            if limit <= 0:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return Pass0CandidateResult(
                    gists=[],
                    scores={},
                    cloud_egress_allowed=cloud_egress_allowed,
                    query_latency_ms=elapsed_ms,
                )

            and_expr, or_expr, prefix_expr, trigram_expr, trigram_terms = _build_fts_queries(
                query
            )
            if and_expr is None:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return Pass0CandidateResult(
                    gists=[],
                    scores={},
                    cloud_egress_allowed=cloud_egress_allowed,
                    query_latency_ms=elapsed_ms,
                )

            match_mode = "AND"
            rows = conn.execute(
                _FTS_EXACT_OR_PREFIX_SQL,
                (and_expr, workspace_hash, limit),
            ).fetchall()

            if not rows and or_expr is not None and or_expr != and_expr:
                match_mode = "OR"
                rows = conn.execute(
                    _FTS_EXACT_OR_PREFIX_SQL,
                    (or_expr, workspace_hash, limit),
                ).fetchall()

            if not rows and prefix_expr is not None:
                match_mode = "PREFIX"
                rows = conn.execute(
                    _FTS_EXACT_OR_PREFIX_SQL,
                    (prefix_expr, workspace_hash, limit),
                ).fetchall()

            raw_scores: list[float] = []
            if rows:
                raw_scores = [max(0.0, -_as_float(row[10])) for row in rows[:limit]]
            elif trigram_expr is not None:
                match_mode = "TRIGRAM"
                scan_limit = max(limit * 4, _TRIGRAM_SCAN_CAP)
                raw_trigram_rows = conn.execute(
                    _TRIGRAM_CANDIDATE_SQL,
                    (trigram_expr, workspace_hash, scan_limit),
                ).fetchall()
                if raw_trigram_rows:
                    rows, raw_scores = _rank_trigram_rows(raw_trigram_rows, trigram_terms, limit)

            total_ws_docs = 0
            if rows:
                cached_total = self._ws_counts.get(workspace_hash)
                if cached_total is None or cached_total < len(rows):
                    cnt_row = conn.execute(
                        "SELECT COUNT(*) FROM gists WHERE workspace_hash = ?",
                        (workspace_hash,),
                    ).fetchone()
                    cached_total = _as_int(cnt_row[0]) if cnt_row is not None else len(rows)
                    self._ws_counts[workspace_hash] = cached_total
                total_ws_docs = cached_total

        gists = [_row_to_gist(row) for row in rows[:limit]]
        scores = _normalize_pass0_scores(
            query,
            gists,
            raw_scores[: len(gists)],
            match_mode,
            total_ws_docs,
        )
        if cloud_egress_allowed and any(
            gist.is_private_tier1
            or gist.rel_path.lower().endswith((".pem", ".key"))
            or not check_text_air_gap(gist.rel_path)
            or is_sensitive_content(gist.semantic_gist, include_tags=True)
            for gist in gists
        ):
            cloud_egress_allowed = False

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Pass0CandidateResult(
            gists=gists,
            scores=scores,
            cloud_egress_allowed=cloud_egress_allowed,
            query_latency_ms=elapsed_ms,
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._initialized = False
            self._ws_counts.clear()


__all__ = [
    "Pass0CandidateResult",
    "SqliteFtsStore",
    "check_text_air_gap",
    "is_sensitive_content",
    "is_sensitive_file",
    "is_sensitive_path",
]
