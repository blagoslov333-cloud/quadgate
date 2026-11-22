"""1-strike promotion circuit breaker, miss-event linker, and grey-zone logger."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import posixpath
import sqlite3
import subprocess
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from quadgate.contracts import (
    GateEvent,
    GateType,
    GateVerdict,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.kernel.gates import TierModelConfig

_R_HIST_SMOOTHING_PRIOR: Final[float] = 2.0
_GIT_TIMEOUT_SECONDS: Final[float] = 5.0

_PATH_KEYS: Final[frozenset[str]] = frozenset(
    {
        "Path",
        "TargetFile",
        "file_path",
        "path",
        "target_file",
        "AbsolutePath",
        "TargetPath",
        "dest",
        "destination",
        "file",
        "filename",
    }
)
_NESTED_PATH_CONTAINER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "edits",
        "ReplacementChunks",
        "changes",
        "files",
        "targets",
        "paths",
        "tool_input",
        "args",
        "input",
        "arguments",
    }
)

_READ_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "view_file",
        "read_file",
        "read",
        "view_file_outline",
        "view_code_item",
        "open_file",
        "get_file",
        "view",
        "cat",
    }
)
_MUTATION_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "write_file",
        "edit_file",
        "patch_file",
        "apply_patch",
        "create_file",
        "modify_file",
        "write",
        "edit",
        "multiedit",
        "notebook_edit",
        "notebookedit",
        "patch",
        "delete_file",
        "remove_file",
    }
)


def _compute_workspace_hash(workspace_root: str) -> str:
    """Compute canonical 128-bit blake2b workspace hash matching AstGistIndexer."""
    if not workspace_root:
        return hashlib.blake2b(b"__default__", digest_size=16).hexdigest()
    ws_real = os.path.realpath(workspace_root)
    ws_norm = os.path.normcase(ws_real)
    return hashlib.blake2b(ws_norm.encode("utf-8"), digest_size=16).hexdigest()


def _is_within_root(root_real_norm: str, target_real_norm: str) -> bool:
    """Return True if target_real_norm equals or lies beneath root_real_norm."""
    try:
        return os.path.commonpath([root_real_norm, target_real_norm]) == root_real_norm
    except ValueError:
        return False


def _normalize_rel_path(path: str, workspace_root: str = "") -> str:
    """Normalize a path into a clean forward-slash workspace-relative path."""
    raw = path.strip()
    if not raw:
        return ""

    slash_path = raw.replace("\\", "/")
    is_abs_like = os.path.isabs(raw) or slash_path.startswith("/")

    if workspace_root and is_abs_like:
        ws_real = os.path.realpath(workspace_root)
        target_real = os.path.realpath(raw)
        if _is_within_root(os.path.normcase(ws_real), os.path.normcase(target_real)):
            rel = os.path.relpath(target_real, ws_real).replace("\\", "/")
            return "" if rel == "." else rel
        return posixpath.normpath(slash_path)

    while slash_path.startswith("./"):
        slash_path = slash_path[2:]
    norm = posixpath.normpath(slash_path)
    return "" if norm == "." else norm


def _dedupe_paths(paths: Sequence[str], workspace_root: str = "") -> list[str]:
    """Normalize and deduplicate paths while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in paths:
        norm = _normalize_rel_path(raw, workspace_root)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def _collect_paths_from_obj(obj: Any, out: list[str], *, depth: int = 0) -> None:
    """Bounded recursive extractor for file paths in tool argument structures."""
    if depth > 4 or obj is None:
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _PATH_KEYS and isinstance(val, str) and val.strip():
                out.append(val)
            elif key in _NESTED_PATH_CONTAINER_KEYS:
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, str) and item.strip():
                            out.append(item)
                        else:
                            _collect_paths_from_obj(item, out, depth=depth + 1)
                elif isinstance(val, dict):
                    _collect_paths_from_obj(val, out, depth=depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_paths_from_obj(item, out, depth=depth + 1)


def _extract_event_paths(event: GateEvent, workspace_root: str) -> list[str]:
    """Extract normalized paths from GateEvent tool_args and touched_files."""
    raw_paths: list[str] = []
    _collect_paths_from_obj(event.tool_args, raw_paths)
    raw_paths.extend(event.touched_files)
    return _dedupe_paths(raw_paths, workspace_root)


def _classify_tool(tool_name: str | None) -> str | None:
    """Return 'read', 'mutation', or None for an agent tool name."""
    if not tool_name:
        return None
    norm = tool_name.strip().lower().replace("-", "_")
    base = norm.rsplit(".", 1)[-1].rsplit("__", 1)[-1]
    if norm in _READ_TOOLS or base in _READ_TOOLS:
        return "read"
    if norm in _MUTATION_TOOLS or base in _MUTATION_TOOLS:
        return "mutation"
    return None


def _compute_r_hist(failures: int, total_runs: int) -> float:
    """Compute Laplace-smoothed historical failure rate R_hist in [0.0, 1.0)."""
    if failures <= 0 or total_runs <= 0:
        return 0.0
    rate = float(failures) / (float(total_runs) + _R_HIST_SMOOTHING_PRIOR)
    return min(1.0, max(0.0, rate))


def _run_git(ws_path: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a bounded git command inside ws_path without raising on OS/git errors."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _revert_touched_files_sync(workspace_root: str, rel_paths: list[str]) -> list[str]:
    """Safely discard dirty git hunks on touched_files inside committed git repos."""
    if not workspace_root or not rel_paths:
        return []

    ws_path = Path(workspace_root)
    if not ws_path.is_dir():
        return []

    head_proc = _run_git(ws_path, ["rev-parse", "--verify", "HEAD"])
    if head_proc is None or head_proc.returncode != 0:
        return []

    ws_real = os.path.realpath(str(ws_path))
    ws_real_norm = os.path.normcase(ws_real)
    reverted: list[str] = []

    for rel in rel_paths:
        if (
            not rel
            or rel == ".git"
            or rel.startswith(".git/")
            or rel.startswith("../")
            or rel == ".."
        ):
            continue
        target_real = os.path.realpath(os.path.join(ws_real, rel.replace("/", os.sep)))
        target_real_norm = os.path.normcase(target_real)
        if target_real_norm == ws_real_norm or not _is_within_root(ws_real_norm, target_real_norm):
            continue

        in_head_proc = _run_git(ws_path, ["ls-tree", "-r", "--name-only", "HEAD", "--", rel])
        if in_head_proc is None:
            continue

        if in_head_proc.returncode == 0 and in_head_proc.stdout.strip():
            # Tracked in HEAD: restore both staged index and worktree cleanly to HEAD.
            restore_proc = _run_git(
                ws_path,
                ["restore", "--source=HEAD", "--staged", "--worktree", "--", rel],
            )
            if restore_proc is not None and restore_proc.returncode == 0:
                reverted.append(rel)
                continue

            _run_git(ws_path, ["reset", "-q", "HEAD", "--", rel])
            co_proc = _run_git(ws_path, ["checkout", "HEAD", "--", rel])
            if co_proc is not None and co_proc.returncode == 0:
                reverted.append(rel)
            continue

        # Not present in HEAD: unstage newly added index entry and clean non-ignored untracked file.
        _run_git(ws_path, ["reset", "-q", "HEAD", "--", rel])
        untracked_proc = _run_git(
            ws_path,
            ["ls-files", "--others", "--exclude-standard", "--", rel],
        )
        if (
            untracked_proc is not None
            and untracked_proc.returncode == 0
            and untracked_proc.stdout.strip()
        ):
            clean_proc = _run_git(ws_path, ["clean", "-f", "--", rel])
            if clean_proc is not None and clean_proc.returncode == 0:
                reverted.append(rel)
                continue
            target_file = Path(target_real)
            if target_file.is_file():
                try:
                    target_file.unlink()
                    reverted.append(rel)
                except OSError:
                    pass

    return reverted


class SelfHealingEngine:
    """Closed-loop recovery, context-miss linker, and grey-zone telemetry engine."""

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        workspace_root: Path | str | None = None,
        grey_zone_log_path: Path | str | None = None,
        tiers: TierModelConfig | None = None,
        grey_zone_low: float = 0.60,
        grey_zone_high: float = 0.84,
    ) -> None:
        if (
            isinstance(grey_zone_low, bool)
            or isinstance(grey_zone_high, bool)
            or not math.isfinite(grey_zone_low)
            or not math.isfinite(grey_zone_high)
            or not (0.0 <= grey_zone_low <= grey_zone_high <= 1.0)
        ):
            raise ValueError(
                f"Invalid grey-zone bounds: low={grey_zone_low!r}, high={grey_zone_high!r}"
            )

        self.db_path = db_path
        self._default_workspace_root = (
            str(workspace_root).strip() if workspace_root is not None else ""
        )
        self.workspace_root = self._default_workspace_root or None
        self.grey_zone_log_path = (
            Path(grey_zone_log_path) if grey_zone_log_path is not None else None
        )
        self.tiers = tiers or TierModelConfig()
        self.grey_zone_low = float(grey_zone_low)
        self.grey_zone_high = float(grey_zone_high)

        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._initialize_db()

    def __enter__(self) -> SelfHealingEngine:
        with self._lock:
            self._ensure_conn()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _initialize_db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn

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
            conn.execute("PRAGMA busy_timeout=5000;")

            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS associative_links (
                        workspace_hash TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        target_path TEXT NOT NULL,
                        miss_count INTEGER NOT NULL DEFAULT 1,
                        updated_at_ns INTEGER NOT NULL,
                        PRIMARY KEY (workspace_hash, source_path, target_path)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_hints (
                        session_id TEXT NOT NULL,
                        workspace_hash TEXT NOT NULL,
                        hinted_path TEXT NOT NULL,
                        hint_order INTEGER NOT NULL,
                        updated_at_ns INTEGER NOT NULL,
                        PRIMARY KEY (session_id, workspace_hash, hinted_path)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_reads (
                        session_id TEXT NOT NULL,
                        workspace_hash TEXT NOT NULL,
                        read_path TEXT NOT NULL,
                        linked INTEGER NOT NULL DEFAULT 0,
                        updated_at_ns INTEGER NOT NULL,
                        PRIMARY KEY (session_id, workspace_hash, read_path)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS file_telemetry (
                        workspace_hash TEXT NOT NULL,
                        rel_path TEXT NOT NULL,
                        total_runs INTEGER NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        r_hist REAL NOT NULL DEFAULT 0.0,
                        updated_at_ns INTEGER NOT NULL,
                        PRIMARY KEY (workspace_hash, rel_path)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_strikes (
                        session_id TEXT PRIMARY KEY,
                        workspace_hash TEXT NOT NULL,
                        strike_count INTEGER NOT NULL DEFAULT 0,
                        last_tier TEXT,
                        last_oracle_error TEXT,
                        updated_at_ns INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS grey_zone_counters (
                        workspace_hash TEXT PRIMARY KEY,
                        logged_count INTEGER NOT NULL DEFAULT 0,
                        updated_at_ns INTEGER NOT NULL
                    )
                    """
                )

            self._conn = conn
            return conn

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            return self._initialize_db()
        return self._conn

    def _resolve_workspace(self, workspace_root: Path | str = "") -> tuple[str, str]:
        raw = str(workspace_root).strip() if workspace_root is not None else ""
        effective = raw if raw else self._default_workspace_root
        return effective, _compute_workspace_hash(effective)

    def _resolve_read_workspace_hash(
        self,
        conn: sqlite3.Connection,
        table: str,
        workspace_root: Path | str = "",
    ) -> tuple[str, str]:
        effective, ws_hash = self._resolve_workspace(workspace_root)
        if effective:
            return effective, ws_hash

        # When workspace_root is omitted and no default was configured on the engine,
        # transparently scope to the sole recorded workspace if exactly one exists.
        rows = conn.execute(f"SELECT DISTINCT workspace_hash FROM {table} LIMIT 2").fetchall()
        if len(rows) == 1:
            sole_hash = str(rows[0][0])
            return effective, sole_hash
        return effective, ws_hash

    def record_turn1_hints(
        self,
        session_id: str,
        workspace_root: Path | str,
        hinted_paths: list[str],
    ) -> None:
        """Persist Turn-1 hinted file paths for subsequent context-miss detection."""
        effective_ws, ws_hash = self._resolve_workspace(workspace_root)
        normalized = _dedupe_paths(hinted_paths, effective_ws)
        now_ns = time.time_ns()

        with self._lock:
            conn = self._ensure_conn()
            with conn:
                conn.execute(
                    "DELETE FROM session_hints WHERE session_id = ? AND workspace_hash = ?",
                    (session_id, ws_hash),
                )
                conn.execute(
                    "DELETE FROM session_reads WHERE session_id = ? AND workspace_hash = ?",
                    (session_id, ws_hash),
                )
                for idx, path in enumerate(normalized):
                    conn.execute(
                        """
                        INSERT INTO session_hints (
                            session_id, workspace_hash, hinted_path, hint_order, updated_at_ns
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (session_id, ws_hash, path, idx, now_ns),
                    )

    async def record_context_miss(
        self,
        session_id: str,
        hinted_paths: list[str],
        opened_path: str,
        *,
        workspace_root: Path | str = "",
    ) -> None:
        """Persist an associative edge between Turn-1 hints and a subsequently opened file."""
        effective_ws, ws_hash = self._resolve_workspace(workspace_root)
        target = _normalize_rel_path(opened_path, effective_ws)
        if not target:
            return

        sources = [p for p in _dedupe_paths(hinted_paths, effective_ws) if p != target]
        if not sources:
            return

        now_ns = time.time_ns()
        with self._lock:
            conn = self._ensure_conn()
            with conn:
                for source in sources:
                    conn.execute(
                        """
                        INSERT INTO associative_links (
                            workspace_hash, source_path, target_path, miss_count, updated_at_ns
                        ) VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT(workspace_hash, source_path, target_path) DO UPDATE SET
                            miss_count = associative_links.miss_count + 1,
                            updated_at_ns = excluded.updated_at_ns
                        """,
                        (ws_hash, source, target, now_ns),
                    )

    async def observe_tool_event(self, event: GateEvent) -> list[tuple[str, str]]:
        """Track reads after Turn-1 hints and link non-hinted read files when mutated."""
        tool_kind = _classify_tool(event.tool_name)
        if tool_kind is None:
            return []

        effective_ws, ws_hash = self._resolve_workspace(event.workspace_root)
        paths = _extract_event_paths(event, effective_ws)
        if not paths:
            return []

        now_ns = time.time_ns()
        recorded_links: list[tuple[str, str]] = []

        with self._lock:
            conn = self._ensure_conn()
            if tool_kind == "read":
                with conn:
                    for path in paths:
                        conn.execute(
                            """
                            INSERT INTO session_reads (
                                session_id, workspace_hash, read_path, linked, updated_at_ns
                            ) VALUES (?, ?, ?, 0, ?)
                            ON CONFLICT(session_id, workspace_hash, read_path) DO UPDATE SET
                                updated_at_ns = excluded.updated_at_ns
                            """,
                            (event.session_id, ws_hash, path, now_ns),
                        )
                return []

            hint_rows = conn.execute(
                """
                SELECT hinted_path
                FROM session_hints
                WHERE session_id = ? AND workspace_hash = ?
                ORDER BY hint_order ASC
                """,
                (event.session_id, ws_hash),
            ).fetchall()
            hints = [str(r[0]) for r in hint_rows]
            if not hints:
                meta_hints = event.metadata.get("hinted_paths")
                if isinstance(meta_hints, list):
                    hints = _dedupe_paths(
                        [str(h) for h in meta_hints if isinstance(h, str)],
                        effective_ws,
                    )
            if not hints:
                return []

            hint_set = set(hints)
            read_rows = conn.execute(
                """
                SELECT read_path, linked
                FROM session_reads
                WHERE session_id = ? AND workspace_hash = ?
                """,
                (event.session_id, ws_hash),
            ).fetchall()
            read_state = {str(r[0]): int(r[1]) for r in read_rows}

            with conn:
                for target in paths:
                    if target in hint_set:
                        continue
                    linked_flag = read_state.get(target)
                    if linked_flag is None or linked_flag != 0:
                        continue

                    for source in hints:
                        if source == target:
                            continue
                        conn.execute(
                            """
                            INSERT INTO associative_links (
                                workspace_hash, source_path, target_path, miss_count, updated_at_ns
                            ) VALUES (?, ?, ?, 1, ?)
                            ON CONFLICT(workspace_hash, source_path, target_path) DO UPDATE SET
                                miss_count = associative_links.miss_count + 1,
                                updated_at_ns = excluded.updated_at_ns
                            """,
                            (ws_hash, source, target, now_ns),
                        )
                        recorded_links.append((source, target))

                    conn.execute(
                        """
                        UPDATE session_reads
                        SET linked = 1, updated_at_ns = ?
                        WHERE session_id = ? AND workspace_hash = ? AND read_path = ?
                        """,
                        (now_ns, event.session_id, ws_hash, target),
                    )
                    read_state[target] = 1

        return recorded_links

    def get_associated_paths(
        self,
        source_paths: list[str],
        *,
        workspace_root: Path | str = "",
    ) -> dict[str, float]:
        """Return normalized boost weights min(1.0, 0.4 + 0.2 * miss_count) for linked files."""
        with self._lock:
            conn = self._ensure_conn()
            effective_ws, ws_hash = self._resolve_read_workspace_hash(
                conn,
                "associative_links",
                workspace_root,
            )
            normalized_sources = _dedupe_paths(source_paths, effective_ws)
            if not normalized_sources:
                return {}

            placeholders = ",".join("?" for _ in normalized_sources)
            rows = conn.execute(
                f"""
                SELECT target_path, MAX(miss_count)
                FROM associative_links
                WHERE workspace_hash = ? AND source_path IN ({placeholders})
                GROUP BY target_path
                """,
                (ws_hash, *normalized_sources),
            ).fetchall()

        source_set = set(normalized_sources)
        boosts: dict[str, float] = {}
        for target_raw, miss_count_raw in rows:
            target = str(target_raw)
            if target in source_set:
                continue
            miss_count = int(miss_count_raw)
            boosts[target] = min(1.0, 0.4 + 0.2 * miss_count)
        return boosts

    def get_failure_rate(
        self,
        rel_path: str,
        *,
        workspace_root: Path | str = "",
    ) -> float:
        """Return historical failure/promotion rate R_hist in [0.0, 1.0] for rel_path."""
        with self._lock:
            conn = self._ensure_conn()
            effective_ws, ws_hash = self._resolve_read_workspace_hash(
                conn,
                "file_telemetry",
                workspace_root,
            )
            norm_path = _normalize_rel_path(rel_path, effective_ws)
            if not norm_path:
                return 0.0

            row = conn.execute(
                """
                SELECT r_hist
                FROM file_telemetry
                WHERE workspace_hash = ? AND rel_path = ?
                """,
                (ws_hash, norm_path),
            ).fetchone()

        if row is None:
            return 0.0
        return float(row[0])

    def record_file_outcome(
        self,
        rel_paths: list[str],
        *,
        passed: bool,
        workspace_root: Path | str = "",
    ) -> dict[str, float]:
        """Update file_telemetry pass/failure counters and return updated R_hist values."""
        effective_ws, ws_hash = self._resolve_workspace(workspace_root)
        normalized = _dedupe_paths(rel_paths, effective_ws)
        if not normalized:
            return {}

        now_ns = time.time_ns()
        fail_inc = 0 if passed else 1
        updated: dict[str, float] = {}

        with self._lock:
            conn = self._ensure_conn()
            with conn:
                for rel in normalized:
                    row = conn.execute(
                        """
                        SELECT total_runs, failures
                        FROM file_telemetry
                        WHERE workspace_hash = ? AND rel_path = ?
                        """,
                        (ws_hash, rel),
                    ).fetchone()
                    if row is None:
                        total_runs = 1
                        failures = fail_inc
                    else:
                        total_runs = int(row[0]) + 1
                        failures = int(row[1]) + fail_inc

                    r_hist = _compute_r_hist(failures, total_runs)
                    conn.execute(
                        """
                        INSERT INTO file_telemetry (
                            workspace_hash, rel_path, total_runs, failures, r_hist, updated_at_ns
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(workspace_hash, rel_path) DO UPDATE SET
                            total_runs = excluded.total_runs,
                            failures = excluded.failures,
                            r_hist = excluded.r_hist,
                            updated_at_ns = excluded.updated_at_ns
                        """,
                        (ws_hash, rel, total_runs, failures, r_hist, now_ns),
                    )
                    updated[rel] = r_hist

        return updated

    async def handle_gate4_failure(
        self,
        event: GateEvent,
        oracle_error: str,
        *,
        failed_files: Sequence[str] | None = None,
        record_file_failure: bool = True,
        revert_files: bool = True,
    ) -> GateVerdict:
        """Revert failed floor/mid edits on strike 0 and promote once, or halt at strike >= 1."""
        t0 = time.perf_counter()
        effective_ws, ws_hash = self._resolve_workspace(event.workspace_root)
        touched_rel = _dedupe_paths(event.touched_files, effective_ws)
        target_rel = (
            _dedupe_paths(failed_files, effective_ws)
            if failed_files is not None
            else touched_rel
        )
        oracle_msg = oracle_error.strip() or "Truth oracle verification failed"

        with self._lock:
            conn = self._ensure_conn()
            row = conn.execute(
                "SELECT strike_count FROM session_strikes WHERE session_id = ?",
                (event.session_id,),
            ).fetchone()
            db_strike = int(row[0]) if row is not None else 0

        effective_strike = max(event.strike_count, db_strike)
        if effective_strike == 0 and (
            event.current_tier == ModelTier.FLOOR or event.current_tier == ModelTier.MID
        ):
            reverted: list[str] = []
            if revert_files and target_rel:
                reverted = await asyncio.to_thread(
                    _revert_touched_files_sync,
                    effective_ws,
                    target_rel,
                )
            current_tier = event.current_tier
            next_tier = ModelTier.MID if current_tier == ModelTier.FLOOR else ModelTier.FRONTIER
            next_model = self.tiers.mid if next_tier == ModelTier.MID else self.tiers.frontier
            now_ns = time.time_ns()

            with self._lock:
                conn = self._ensure_conn()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO session_strikes (
                            session_id, workspace_hash, strike_count,
                            last_tier, last_oracle_error, updated_at_ns
                        ) VALUES (?, ?, 1, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            workspace_hash = excluded.workspace_hash,
                            strike_count = MAX(session_strikes.strike_count, 1),
                            last_tier = excluded.last_tier,
                            last_oracle_error = excluded.last_oracle_error,
                            updated_at_ns = excluded.updated_at_ns
                        """,
                        (
                            event.session_id,
                            ws_hash,
                            next_tier.value,
                            oracle_msg,
                            now_ns,
                        ),
                    )
                if record_file_failure and target_rel:
                    self.record_file_outcome(
                        target_rel,
                        passed=False,
                        workspace_root=effective_ws,
                    )

            revert_note = f" Reverted: {', '.join(reverted)}." if reverted else ""
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return GateVerdict(
                action=VerdictAction.PROMOTE,
                gate=GateType.STOP_AUDIT,
                target_tier=next_tier,
                updated_model=next_model,
                latency_ms=elapsed_ms,
                reason=(
                    f"Gate 4 oracle failed ({oracle_msg}); promoted "
                    f"{current_tier.value} -> {next_tier.value} ({next_model}).{revert_note}"
                ),
            )

        # Strike >= 1, FRONTIER tier, or unknown tier: halt cleanly via one-strike circuit breaker.
        final_strike = max(2, effective_strike + 1)
        now_ns = time.time_ns()
        with self._lock:
            conn = self._ensure_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO session_strikes (
                        session_id, workspace_hash, strike_count,
                        last_tier, last_oracle_error, updated_at_ns
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        workspace_hash = excluded.workspace_hash,
                        strike_count = MAX(session_strikes.strike_count, excluded.strike_count),
                        last_tier = excluded.last_tier,
                        last_oracle_error = excluded.last_oracle_error,
                        updated_at_ns = excluded.updated_at_ns
                    """,
                    (
                        event.session_id,
                        ws_hash,
                        final_strike,
                        event.current_tier.value if event.current_tier is not None else None,
                        oracle_msg,
                        now_ns,
                    ),
                )
            if record_file_failure and target_rel:
                self.record_file_outcome(
                    target_rel,
                    passed=False,
                    workspace_root=effective_ws,
                )

        violated_path = (
            target_rel[0]
            if target_rel
            else (touched_rel[0] if touched_rel else None)
        )
        remediation = (
            f"One-strike circuit breaker halted promotion after Gate 4 failure ({oracle_msg}). "
            "Inspect failing artifacts and resolve manually before retrying."
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.STOP_AUDIT,
            violation_receipt=ViolationReceipt(
                rule_id="GATE4_ONE_STRIKE_CIRCUIT_BREAKER",
                score=1.0,
                stage="deterministic_2a",
                remediation=remediation,
                strike_count=final_strike,
                escalate_to_human=True,
                violated_path=violated_path,
            ),
            latency_ms=elapsed_ms,
            reason=(
                f"Gate 4 one-strike circuit breaker halted after strike "
                f"{effective_strike}: {oracle_msg}"
            ),
        )

    async def log_grey_zone_decision(
        self,
        event: GateEvent,
        score: float,
        head_id: str,
        *,
        verdict_action: str = "allow",
    ) -> bool:
        """Append grey-zone decision (0.60 <= p_cal <= 0.84) to JSONL for rule crystallization."""
        if isinstance(score, bool):
            return False
        numeric_score = float(score)
        if not math.isfinite(numeric_score):
            return False
        if not (self.grey_zone_low <= numeric_score <= self.grey_zone_high):
            return False

        effective_ws, ws_hash = self._resolve_workspace(event.workspace_root)
        target_path = (
            self.grey_zone_log_path
            if self.grey_zone_log_path is not None
            else Path(event.workspace_root) / ".quadgate" / "logs" / "grey_zone.jsonl"
        )
        now_ns = time.time_ns()
        record = {
            "timestamp_ns": now_ns,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "session_id": event.session_id,
            "workspace_root": event.workspace_root,
            "workspace_hash": ws_hash,
            "gate": event.gate.value,
            "turn_index": event.turn_index,
            "harness": event.harness.value,
            "tool_name": event.tool_name,
            "head_id": head_id,
            "score": round(numeric_score, 6),
            "verdict_action": verdict_action,
            "touched_files": _dedupe_paths(event.touched_files, effective_ws),
            "grey_zone_low": self.grey_zone_low,
            "grey_zone_high": self.grey_zone_high,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"

        with self._lock:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()

            conn = self._ensure_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO grey_zone_counters (workspace_hash, logged_count, updated_at_ns)
                    VALUES (?, 1, ?)
                    ON CONFLICT(workspace_hash) DO UPDATE SET
                        logged_count = grey_zone_counters.logged_count + 1,
                        updated_at_ns = excluded.updated_at_ns
                    """,
                    (ws_hash, now_ns),
                )

        return True

    def get_telemetry_summary(self) -> dict[str, Any]:
        """Return aggregate SQLite telemetry counters across all three self-healing loops."""
        with self._lock:
            conn = self._ensure_conn()
            links_row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(miss_count), 0) FROM associative_links"
            ).fetchone()
            files_row = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(total_runs), 0), COALESCE(SUM(failures), 0)
                FROM file_telemetry
                """
            ).fetchone()
            strikes_row = conn.execute(
                """
                SELECT
                    COUNT(CASE WHEN strike_count >= 1 THEN 1 END),
                    COUNT(CASE WHEN strike_count >= 2 THEN 1 END)
                FROM session_strikes
                """
            ).fetchone()
            grey_row = conn.execute(
                "SELECT COALESCE(SUM(logged_count), 0) FROM grey_zone_counters"
            ).fetchone()

        return {
            "associative_links_count": int(links_row[0]) if links_row else 0,
            "total_context_misses": int(links_row[1]) if links_row else 0,
            "tracked_files_count": int(files_row[0]) if files_row else 0,
            "total_file_runs": int(files_row[1]) if files_row else 0,
            "total_file_failures": int(files_row[2]) if files_row else 0,
            "sessions_with_strikes": int(strikes_row[0]) if strikes_row else 0,
            "circuit_breaker_halts": int(strikes_row[1]) if strikes_row else 0,
            "grey_zone_decisions_logged": int(grey_row[0]) if grey_row else 0,
        }

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = ["SelfHealingEngine"]
