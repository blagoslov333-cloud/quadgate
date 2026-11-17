"""SQLite FTS5 + trigram Pass-0 candidate store & Tier-1 air-gap filter."""

from __future__ import annotations

import json
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
_AIRGAP_PRIVATE_DIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])(?:vault/private(?:/|$|[^A-Za-z0-9_])|(?:private_vault|secrets|privileged|attorney_client|payroll|tax_ids)/)",
    re.IGNORECASE,
)
_AIRGAP_ENV_FILE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])\.env"
    r"(?!\.(?:example|sample|template)\b)"
    r"(?:\.[A-Za-z0-9_-]+)?"
    r"(?=$|[^A-Za-z0-9_])",
    re.IGNORECASE,
)
_AIRGAP_SSN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
)
_AIRGAP_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|xox[baprs]-[0-9A-Za-z-]{10,}"
    r")"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)

_SELECT_GISTS_COLUMNS: Final[str] = (
    "g.workspace_hash, g.rel_path, g.content_hash, g.language, g.semantic_gist, "
    "g.symbols_json, g.imports_json, g.fan_in_count, g.mtime_ns, g.is_private_tier1"
)

_FTS_EXACT_OR_PREFIX_SQL: Final[str] = f"""
SELECT {_SELECT_GISTS_COLUMNS}
FROM gists_fts f
JOIN gists g ON g.id = f.rowid
WHERE gists_fts MATCH ? AND g.workspace_hash = ?
ORDER BY bm25(gists_fts, 4.0, 3.0, 1.5, 1.0) ASC, g.fan_in_count DESC, g.id ASC
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
    cloud_egress_allowed: bool = True
    query_latency_ms: float = Field(default=0.0, ge=0.0)


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
) -> list[tuple[object, ...]]:
    """Score bounded trigram candidate rows in memory by field importance and fan-in."""

    def _score_key(row: tuple[object, ...]) -> tuple[int, int, str]:
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
        return (-score, -fan_in, rel_path_low)

    return sorted(rows, key=_score_key)[:limit]


class SqliteFtsStore:
    """WAL-mode SQLite FTS5 + trigram candidate store (<4ms lookup)."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._initialized: bool = False
        self._lock = threading.RLock()

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

            return written

    def check_air_gap(self, text: str, workspace_root: str = "") -> bool:
        """Return False if Tier-1 private markers/secrets prohibit cloud egress."""
        for candidate, is_root in ((text, False), (workspace_root, True)):
            if not candidate:
                continue
            normalized = candidate.replace("\\", "/")
            if is_root and not normalized.endswith("/"):
                normalized_dir = f"{normalized}/"
            else:
                normalized_dir = normalized

            if _AIRGAP_TAG_RE.search(candidate):
                return False
            if _AIRGAP_PRIVATE_DIR_RE.search(normalized_dir):
                return False
            if _AIRGAP_ENV_FILE_RE.search(normalized):
                return False
            if _AIRGAP_SSN_RE.search(candidate):
                return False
            if _AIRGAP_SECRET_RE.search(candidate):
                return False
        return True

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
                    cloud_egress_allowed=cloud_egress_allowed,
                    query_latency_ms=elapsed_ms,
                )

            rows = conn.execute(
                _FTS_EXACT_OR_PREFIX_SQL,
                (and_expr, workspace_hash, limit),
            ).fetchall()

            if not rows and or_expr is not None and or_expr != and_expr:
                rows = conn.execute(
                    _FTS_EXACT_OR_PREFIX_SQL,
                    (or_expr, workspace_hash, limit),
                ).fetchall()

            if not rows and prefix_expr is not None:
                rows = conn.execute(
                    _FTS_EXACT_OR_PREFIX_SQL,
                    (prefix_expr, workspace_hash, limit),
                ).fetchall()

            if not rows and trigram_expr is not None:
                scan_limit = max(limit * 4, _TRIGRAM_SCAN_CAP)
                raw_trigram_rows = conn.execute(
                    _TRIGRAM_CANDIDATE_SQL,
                    (trigram_expr, workspace_hash, scan_limit),
                ).fetchall()
                if raw_trigram_rows:
                    rows = _rank_trigram_rows(raw_trigram_rows, trigram_terms, limit)

        gists = [_row_to_gist(row) for row in rows[:limit]]
        if cloud_egress_allowed and any(gist.is_private_tier1 for gist in gists):
            cloud_egress_allowed = False

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Pass0CandidateResult(
            gists=gists,
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
