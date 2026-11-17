"""Git log co-change adjacency matrix builder."""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

_COMMIT_MARKER = "__QG_COMMIT__"


class CoChangeGraph:
    """Computes normalized pairwise file co-change weights w(i, j) in [0.0, 1.0]."""

    def __init__(
        self,
        workspace_root: Path,
        max_commits: int = 500,
        max_files_per_commit: int = 50,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.max_commits = max_commits
        self.max_files_per_commit = max_files_per_commit
        self._weights: dict[tuple[str, str], float] = {}

    @staticmethod
    def _normalize_path(path: str) -> str:
        norm = path.strip().replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        return norm

    def build_from_log_text(self, raw_log: str) -> dict[tuple[str, str], float]:
        """Parse formatted git log output into normalized co-change weights."""
        if not raw_log or not raw_log.strip():
            self._weights = {}
            return {}

        commits: list[list[str]] = []
        current_files: list[str] | None = None

        for raw_line in raw_log.splitlines():
            line = raw_line.strip()
            if line == _COMMIT_MARKER:
                if current_files is not None:
                    commits.append(current_files)
                current_files = []
                continue
            if not line or current_files is None:
                continue
            norm = self._normalize_path(line)
            if norm:
                current_files.append(norm)

        if current_files is not None:
            commits.append(current_files)

        commit_counts: Counter[str] = Counter()
        pair_counts: Counter[tuple[str, str]] = Counter()

        for raw_files in commits:
            unique_files = list(dict.fromkeys(raw_files))
            if not unique_files or len(unique_files) > self.max_files_per_commit:
                continue
            for f in unique_files:
                commit_counts[f] += 1
            if len(unique_files) < 2:
                continue
            for idx, file_i in enumerate(unique_files):
                for jdx, file_j in enumerate(unique_files):
                    if idx != jdx:
                        pair_counts[(file_i, file_j)] += 1

        weights: dict[tuple[str, str], float] = {}
        for (file_i, file_j), shared in pair_counts.items():
            denom = commit_counts[file_i]
            if denom > 0:
                weights[(file_i, file_j)] = min(1.0, max(0.0, shared / float(denom)))

        self._weights = weights
        return dict(weights)

    def build_from_git_log(self) -> dict[tuple[str, str], float]:
        """Parse `git log` history into normalized co-change adjacency weights."""
        if not self.workspace_root.exists():
            self._weights = {}
            return {}

        ws_resolved = self.workspace_root.resolve()
        has_git = (ws_resolved / ".git").exists() or any(
            (parent / ".git").exists() for parent in ws_resolved.parents
        )
        if not has_git:
            self._weights = {}
            return {}

        cmd = [
            "git",
            "log",
            "-n",
            str(self.max_commits),
            "--name-only",
            f"--pretty=format:{_COMMIT_MARKER}",
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            self._weights = {}
            return {}

        if proc.returncode != 0 or not proc.stdout:
            self._weights = {}
            return {}

        return self.build_from_log_text(proc.stdout)

    def weight(self, path_a: str, path_b: str) -> float:
        """Return historical co-change affinity between two repository paths."""
        norm_a = self._normalize_path(path_a)
        norm_b = self._normalize_path(path_b)
        if not norm_a or not norm_b or norm_a == norm_b:
            return 0.0
        return self._weights.get((norm_a, norm_b), 0.0)


__all__ = ["CoChangeGraph"]
