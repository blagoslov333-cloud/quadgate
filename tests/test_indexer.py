"""Wave 1 unit & performance tests for AstGistIndexer and CoChangeGraph."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from quadgate.contracts import CartridgeSpec
from quadgate.index import ast_gists as ast_gists_mod
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.cochange import CoChangeGraph


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.parametrize("use_tree_sitter", [True, False])
def test_multilang_extraction_tree_sitter_and_fallback(
    tmp_path: Path, use_tree_sitter: bool
) -> None:
    py_file = tmp_path / "src" / "auth" / "service.py"
    py_file.parent.mkdir(parents=True)
    py_file.write_text(
        '"""Auth validator module."""\n'
        "import os\n"
        "from pathlib import Path\n"
        "from .tokens import verify_jwt\n"
        "from ..core import config\n\n"
        "class AuthService:\n"
        "    def verify_user(self, token: str, strict: bool = True) -> bool:\n"
        "        return True\n\n"
        "async def refresh_token(user_id: int) -> str:\n"
        '    return "tok"\n\n'
        "def _private_helper() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    ts_file = tmp_path / "src" / "web" / "limiter.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text(
        "/** Sliding window rate limiter. */\n"
        "import { Clock } from './clock';\n"
        "import type { User } from '../models/user';\n"
        "import * as path from 'path';\n\n"
        "export interface LimiterConfig {\n"
        "  maxRequests: number;\n"
        "}\n\n"
        "export class RateLimiter {\n"
        "  check(key: string): boolean { return true; }\n"
        "}\n\n"
        "export async function verifyQuota(id: string): Promise<boolean> {\n"
        "  return true;\n"
        "}\n\n"
        "export const computeBucket = (ts: number): number => ts;\n",
        encoding="utf-8",
    )

    tsx_file = tmp_path / "src" / "web" / "Banner.tsx"
    tsx_file.write_text(
        "// Top banner component.\n"
        "import React from 'react';\n"
        "import { Button } from './Button';\n\n"
        "export interface BannerProps { title: string; }\n"
        "export function Banner({ title }: BannerProps) {\n"
        "  return <div>{title}</div>;\n"
        "}\n",
        encoding="utf-8",
    )

    js_file = tmp_path / "src" / "web" / "legacy.js"
    js_file.write_text(
        "// Legacy widget helpers.\n"
        "import { fmt } from './fmt.js';\n"
        "const cfg = require('./cfg');\n\n"
        "export class Widget {\n"
        "  render() {}\n"
        "}\n"
        "export function createWidget(id) { return id; }\n",
        encoding="utf-8",
    )

    rs_file = tmp_path / "crates" / "guard.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text(
        "//! Guard verification primitives.\n"
        "use std::collections::HashMap;\n"
        "use crate::tokens::verify_jwt;\n"
        "use super::config;\n"
        "mod helpers;\n\n"
        "pub struct AuthGuard {\n"
        "    pub active: bool,\n"
        "}\n\n"
        "pub enum Role { Admin, User }\n"
        "pub trait Authenticator {\n"
        "    fn check(&self, token: &str) -> bool;\n"
        "}\n\n"
        "impl AuthGuard {\n"
        "    pub fn new(active: bool) -> Self { Self { active } }\n"
        "}\n\n"
        "pub async fn validate_session(id: u64) -> Result<bool, String> {\n"
        "    Ok(true)\n"
        "}\n",
        encoding="utf-8",
    )

    go_file = tmp_path / "cmd" / "auth.go"
    go_file.parent.mkdir(parents=True)
    go_file.write_text(
        "// Package auth implements token verification.\n"
        "package auth\n\n"
        "import (\n"
        '    "fmt"\n'
        '    "myapp/internal/tokens"\n'
        '    cfg "myapp/pkg/config"\n'
        ")\n\n"
        "type AuthService struct {\n"
        "    Secret string\n"
        "}\n\n"
        "type Verifier interface {\n"
        "    Verify(token string) bool\n"
        "}\n\n"
        "func NewAuthService(secret string) *AuthService {\n"
        "    return &AuthService{Secret: secret}\n"
        "}\n\n"
        "func (s *AuthService) Check(token string) bool {\n"
        "    return true\n"
        "}\n",
        encoding="utf-8",
    )

    md_file = tmp_path / "docs" / "overview.md"
    md_file.parent.mkdir(parents=True)
    md_file.write_text(
        "# Architecture Overview\n\n"
        "See [Auth](./auth.md) and [Tokens](../tokens/spec.md#claims) "
        "and [Ext](https://example.com).\n\n"
        "```markdown\n"
        "# Ignored Code Heading\n"
        "[Ignored](./ignored.md)\n"
        "```\n\n"
        "## Gate 1 Design\n\n"
        "### Pass-0 Index\n",
        encoding="utf-8",
    )

    indexer = AstGistIndexer(tmp_path, use_tree_sitter=use_tree_sitter)

    g_py = indexer.index_file(py_file)
    assert g_py.rel_path == "src/auth/service.py"
    assert g_py.language == "python"
    assert "Auth validator module." in g_py.semantic_gist
    py_syms = " | ".join(g_py.symbols)
    assert "AuthService" in g_py.symbols
    assert "verify_user" in py_syms
    assert "refresh_token" in py_syms
    assert "_private_helper" not in py_syms
    assert {"os", "pathlib", ".tokens", "..core"}.issubset(set(g_py.imports))

    g_ts = indexer.index_file(ts_file)
    assert g_ts.rel_path == "src/web/limiter.ts"
    assert g_ts.language == "typescript"
    assert "Sliding window rate limiter." in g_ts.semantic_gist
    ts_syms = " | ".join(g_ts.symbols)
    assert "LimiterConfig" in g_ts.symbols
    assert "RateLimiter" in g_ts.symbols
    assert "check" in ts_syms
    assert "verifyQuota" in ts_syms
    assert "computeBucket" in ts_syms
    assert {"./clock", "../models/user", "path"}.issubset(set(g_ts.imports))

    g_tsx = indexer.index_file(tsx_file)
    assert g_tsx.language == "tsx"
    assert "BannerProps" in g_tsx.symbols
    assert any("Banner" in s for s in g_tsx.symbols)
    assert {"react", "./Button"}.issubset(set(g_tsx.imports))

    g_js = indexer.index_file(js_file)
    assert g_js.language == "javascript"
    assert "Widget" in g_js.symbols
    assert any("createWidget" in s for s in g_js.symbols)
    assert {"./fmt.js", "./cfg"}.issubset(set(g_js.imports))

    g_rs = indexer.index_file(rs_file)
    assert g_rs.language == "rust"
    assert "Guard verification primitives." in g_rs.semantic_gist
    rs_syms = " | ".join(g_rs.symbols)
    assert {"AuthGuard", "Role", "Authenticator"}.issubset(set(g_rs.symbols))
    assert "new" in rs_syms
    assert "validate_session" in rs_syms
    assert {
        "std::collections::HashMap",
        "crate::tokens::verify_jwt",
        "super::config",
        "mod::helpers",
    }.issubset(set(g_rs.imports))

    g_go = indexer.index_file(go_file)
    assert g_go.language == "go"
    assert "Package auth implements token verification." in g_go.semantic_gist
    go_syms = " | ".join(g_go.symbols)
    assert {"AuthService", "Verifier"}.issubset(set(g_go.symbols))
    assert "NewAuthService" in go_syms
    assert "Check" in go_syms
    assert {"fmt", "myapp/internal/tokens", "myapp/pkg/config"}.issubset(set(g_go.imports))

    g_md = indexer.index_file(md_file)
    assert g_md.language == "markdown"
    assert "Architecture Overview" in g_md.semantic_gist
    assert "Gate 1 Design" in g_md.semantic_gist
    assert "Pass-0 Index" in g_md.semantic_gist
    assert "Ignored Code Heading" not in g_md.semantic_gist
    assert {"./auth.md", "../tokens/spec.md"}.issubset(set(g_md.imports))
    assert "./ignored.md" not in g_md.imports
    assert "https://example.com" not in g_md.imports


def test_tree_sitter_parser_cached_and_missing_pack_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tree_sitter_language_pack as tslp

    for i in range(4):
        (tmp_path / f"m{i}.py").write_text(f"def fn_{i}(x: int) -> int:\n    return x\n")
        (tmp_path / f"t{i}.ts").write_text(
            f"export function ts_{i}(x: number): number {{ return x; }}\n"
        )

    saved_cache = dict(ast_gists_mod._PARSER_CACHE)
    try:
        ast_gists_mod._PARSER_CACHE.clear()
        calls: list[str] = []
        real_get_parser = tslp.get_parser

        def tracked_get_parser(lang: Any) -> Any:
            calls.append(str(lang))
            return real_get_parser(lang)

        monkeypatch.setattr(tslp, "get_parser", tracked_get_parser)
        idx = AstGistIndexer(tmp_path, use_tree_sitter=True)
        gists = idx.index_workspace(incremental=False)
        assert len(gists) == 8
        assert calls.count("python") == 1
        assert calls.count("typescript") == 1

        # Simulate tree_sitter_language_pack not installed
        ast_gists_mod._PARSER_CACHE.clear()
        monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
        idx_fb = AstGistIndexer(tmp_path, use_tree_sitter=True)
        fb_gists = idx_fb.index_workspace(incremental=False)
        assert len(fb_gists) == 8
        assert any("fn_0" in s for g in fb_gists if g.rel_path == "m0.py" for s in g.symbols)
    finally:
        ast_gists_mod._PARSER_CACHE.clear()
        ast_gists_mod._PARSER_CACHE.update(saved_cache)


def test_private_tier1_detection_and_cartridge_globs(tmp_path: Path) -> None:
    cartridge_path = (
        Path(__file__).resolve().parent.parent
        / "quadgate"
        / "cartridges"
        / "software_engineering.yaml"
    )
    spec = CartridgeSpec.model_validate(yaml.safe_load(cartridge_path.read_text(encoding="utf-8")))

    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret-12345\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=changeme\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "keys.py").write_text("def load_key(): pass\n", encoding="utf-8")
    (tmp_path / "private_vault").mkdir()
    (tmp_path / "private_vault" / "memo.md").write_text("# Top Secret\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run(): pass\n", encoding="utf-8")

    indexer = AstGistIndexer(tmp_path, cartridge=spec)
    env_gist = indexer.index_file(tmp_path / ".env")
    assert env_gist.is_private_tier1 is True
    assert "sk-secret-12345" not in env_gist.semantic_gist

    example_gist = indexer.index_file(tmp_path / ".env.example")
    assert example_gist.is_private_tier1 is False

    gists = {g.rel_path: g for g in indexer.index_workspace(incremental=False)}
    assert gists["secrets/keys.py"].is_private_tier1 is True
    assert gists["private_vault/memo.md"].is_private_tier1 is True
    assert gists["src/app.py"].is_private_tier1 is False

    # Custom cartridge private_tier1_globs
    custom_spec = spec.model_copy(
        update={
            "context_unit": spec.context_unit.model_copy(
                update={"private_tier1_globs": ["**/restricted/**"]}
            )
        }
    )
    (tmp_path / "restricted").mkdir()
    (tmp_path / "restricted" / "policy.py").write_text("def check(): pass\n", encoding="utf-8")
    custom_idx = AstGistIndexer(tmp_path, cartridge=custom_spec)
    custom_gists = {g.rel_path: g for g in custom_idx.index_workspace(incremental=False)}
    assert custom_gists["restricted/policy.py"].is_private_tier1 is True


def test_import_fan_in_and_blast_radius_across_languages(tmp_path: Path) -> None:
    # Python graph: core.py <- tokens.py <- service.py <- api.py; core.py <- cli.py; leaf.py (0)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "core.py").write_text("def hash_data(): pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "tokens.py").write_text(
        "from .core import hash_data\nfrom pkg.core import hash_data as h2\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "service.py").write_text(
        "from .tokens import hash_data\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.service import hash_data\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "cli.py").write_text("import pkg.core\n", encoding="utf-8")
    (tmp_path / "pkg" / "leaf.py").write_text("import os\n", encoding="utf-8")

    # TypeScript graph: utils.ts <- auth/index.ts <- app.ts
    (tmp_path / "web" / "auth").mkdir(parents=True)
    (tmp_path / "web" / "utils.ts").write_text("export function clean() {}\n", encoding="utf-8")
    (tmp_path / "web" / "auth" / "index.ts").write_text(
        "import { clean } from '../utils.js';\nexport const ok = true;\n",
        encoding="utf-8",
    )
    (tmp_path / "web" / "app.ts").write_text(
        "import { ok } from './auth';\n", encoding="utf-8"
    )

    # Rust graph: src/lib.rs declares mod tokens; src/main.rs uses crate::tokens::verify
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tokens.rs").write_text("pub fn verify() -> bool { true }\n")
    (tmp_path / "src" / "lib.rs").write_text("pub mod tokens;\n")
    (tmp_path / "src" / "main.rs").write_text("use crate::tokens::verify;\nfn main() {}\n")

    # Go graph: internal/tok/tok.go imported by cmd/srv/main.go
    (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
    (tmp_path / "internal" / "tok").mkdir(parents=True)
    (tmp_path / "internal" / "tok" / "tok.go").write_text("package tok\nfunc Sign() {}\n")
    (tmp_path / "cmd" / "srv").mkdir(parents=True)
    (tmp_path / "cmd" / "srv" / "main.go").write_text(
        'package main\nimport "example.com/app/internal/tok"\nfunc main() {}\n'
    )

    # Markdown graph: docs/guide.md -> docs/ref.md
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ref.md").write_text("# Reference\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text(
        "# Guide\nSee [Ref](./ref.md#top).\n", encoding="utf-8"
    )

    indexer = AstGistIndexer(tmp_path, use_tree_sitter=True)
    gists = {g.rel_path: g for g in indexer.index_workspace(incremental=False)}

    # Distinct importers of pkg/core.py: pkg/tokens.py (1, deduped!) and pkg/cli.py (1) => 2
    assert gists["pkg/core.py"].fan_in_count == 2
    assert "pkg/core.py" in gists["pkg/tokens.py"].imports
    assert gists["pkg/tokens.py"].fan_in_count == 1
    assert gists["pkg/service.py"].fan_in_count == 1
    assert gists["pkg/leaf.py"].fan_in_count == 0

    assert gists["web/utils.ts"].fan_in_count == 1
    assert gists["web/auth/index.ts"].fan_in_count == 1
    assert "web/auth/index.ts" in gists["web/app.ts"].imports

    assert gists["src/tokens.rs"].fan_in_count == 2
    assert gists["internal/tok/tok.go"].fan_in_count == 1
    assert gists["docs/ref.md"].fan_in_count == 1

    # Blast radius normalization & monotonicity
    assert indexer.compute_blast_radius([]) == 0.0
    assert indexer.compute_blast_radius(["missing.py"]) == 0.0
    assert indexer.compute_blast_radius(["pkg/leaf.py"]) == 0.0

    b_core = indexer.compute_blast_radius(["pkg/core.py"])
    b_service = indexer.compute_blast_radius(["pkg/service.py"])
    assert 0.0 < b_service < b_core <= 1.0
    # Backslash normalization
    assert indexer.compute_blast_radius(["pkg\\core.py"]) == pytest.approx(b_core)


def test_excluded_dirs_and_incremental_delta_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for d in (
        ".git",
        "node_modules/dep",
        ".venv/lib",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
    ):
        p = tmp_path / d
        p.mkdir(parents=True)
        (p / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")

    (tmp_path / "core.py").write_text("def core_fn(): pass\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import core\ndef main(): pass\n", encoding="utf-8")

    indexer = AstGistIndexer(tmp_path, use_tree_sitter=True)
    first = {g.rel_path: g for g in indexer.index_workspace(incremental=True)}
    assert set(first.keys()) == {"app.py", "core.py"}
    assert first["core.py"].fan_in_count == 1

    # Verify unchanged incremental scan does not re-parse files and completes in < 5 ms
    parse_calls: list[str] = []
    real_parse_data = indexer._parse_data

    def tracked_parse_data(rel_path: str, data: bytes, mtime_ns: int) -> Any:
        parse_calls.append(rel_path)
        return real_parse_data(rel_path, data, mtime_ns)

    monkeypatch.setattr(indexer, "_parse_data", tracked_parse_data)

    latencies_ms: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter()
        unchanged = indexer.index_workspace(incremental=True)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert len(unchanged) == 2

    assert parse_calls == []
    assert min(latencies_ms) < 5.0

    # Modify core.py (bump mtime explicitly for coarse filesystem timestamp granularity)
    core_path = tmp_path / "core.py"
    core_path.write_text(
        "def core_v2(flag: bool) -> bool:\n    return flag\n", encoding="utf-8"
    )
    st = os.stat(core_path)
    os.utime(core_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    after_mod = {g.rel_path: g for g in indexer.index_workspace(incremental=True)}
    assert parse_calls == ["core.py"]
    assert after_mod["core.py"].content_hash != first["core.py"].content_hash
    assert any("core_v2" in s for s in after_mod["core.py"].symbols)

    # Add worker.py importing core.py -> fan_in_count updates to 2
    parse_calls.clear()
    (tmp_path / "worker.py").write_text("import core\ndef work(): pass\n", encoding="utf-8")
    after_add = {g.rel_path: g for g in indexer.index_workspace(incremental=True)}
    assert parse_calls == ["worker.py"]
    assert after_add["core.py"].fan_in_count == 2

    # Delete app.py -> fan_in_count drops back to 1
    parse_calls.clear()
    (tmp_path / "app.py").unlink()
    after_del = {g.rel_path: g for g in indexer.index_workspace(incremental=True)}
    assert parse_calls == []
    assert set(after_del.keys()) == {"core.py", "worker.py"}
    assert after_del["core.py"].fan_in_count == 1


def test_cold_indexing_throughput_over_1000_files_per_sec(tmp_path: Path) -> None:
    file_count = 260
    created_paths: list[Path] = []
    for d in range(10):
        pkg = tmp_path / f"pkg_{d}"
        pkg.mkdir()
        init_p = pkg / "__init__.py"
        init_p.write_text('"""Package root."""\n', encoding="utf-8")
        created_paths.append(init_p)
        for f in range(25):
            mod_p = pkg / f"mod_{f}.py"
            mod_p.write_text(
                f'"""Module {d}_{f}."""\n'
                "from . import mod_0\n\n"
                f"class Handler_{f}:\n"
                "    def handle(self, req: str) -> bool:\n"
                "        return True\n\n"
                f"def run_{f}(x: int) -> int:\n"
                f"    return x + {f}\n",
                encoding="utf-8",
            )
            created_paths.append(mod_p)

    # Warm parser cache & OS page cache on newly created files so AV/first-write flush
    # does not distort cold indexing throughput.
    ast_gists_mod._get_ts_parser("python")
    for _ in range(2):
        for p in created_paths:
            p.read_bytes()

    cold_throughputs: list[float] = []
    indexer: AstGistIndexer | None = None
    for _ in range(3):
        indexer = AstGistIndexer(tmp_path, use_tree_sitter=True)
        t0 = time.perf_counter()
        gists = indexer.index_workspace(incremental=False)
        elapsed = time.perf_counter() - t0
        assert len(gists) == file_count
        cold_throughputs.append(len(gists) / elapsed)

    assert max(cold_throughputs) > 1000.0, (
        f"Expected > 1000 files/sec, got {cold_throughputs}"
    )

    assert indexer is not None
    # Also verify incremental unchanged scan on 260 files stays < 5 ms
    inc_latencies: list[float] = []
    for _ in range(5):
        t_inc0 = time.perf_counter()
        inc_gists = indexer.index_workspace(incremental=True)
        inc_latencies.append((time.perf_counter() - t_inc0) * 1000.0)
        assert len(inc_gists) == file_count
    assert min(inc_latencies) < 5.0, (
        f"Expected incremental scan < 5 ms, got {inc_latencies}"
    )


def test_cochange_graph_real_git_repo_and_empty_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "QuadGate Test")
    _git(repo, "config", "user.email", "test@quadgate.local")

    # Unborn repo (0 commits) must gracefully return {}
    graph_empty = CoChangeGraph(repo)
    assert graph_empty.build_from_git_log() == {}
    assert graph_empty.weight("src/auth.py", "src/tokens.py") == 0.0

    (repo / "src").mkdir()
    # Commit 1: auth.py, tokens.py, README.md
    (repo / "src" / "auth.py").write_text("v1\n", encoding="utf-8")
    (repo / "src" / "tokens.py").write_text("v1\n", encoding="utf-8")
    (repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    # Commit 2: auth.py, tokens.py
    (repo / "src" / "auth.py").write_text("v2\n", encoding="utf-8")
    (repo / "src" / "tokens.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update auth and tokens")

    # Commit 3: solo commit on auth.py
    (repo / "src" / "auth.py").write_text("v3\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "solo auth tweak")

    # Commit 4: billing.py, README.md
    (repo / "src" / "billing.py").write_text("v1\n", encoding="utf-8")
    (repo / "README.md").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "billing docs")

    graph = CoChangeGraph(repo)
    weights = graph.build_from_git_log()
    assert weights
    assert all(0.0 <= w <= 1.0 for w in weights.values())

    # C(auth)=3, C(tokens)=2, C(README)=2, C(billing)=1
    assert graph.weight("src/auth.py", "src/tokens.py") == pytest.approx(2 / 3)
    assert graph.weight("src/tokens.py", "src/auth.py") == pytest.approx(1.0)
    assert graph.weight("src/auth.py", "README.md") == pytest.approx(1 / 3)
    assert graph.weight("README.md", "src/auth.py") == pytest.approx(0.5)
    assert graph.weight("src/billing.py", "README.md") == pytest.approx(1.0)
    assert graph.weight("src/auth.py", "src/billing.py") == 0.0
    assert graph.weight("src/auth.py", "src/auth.py") == 0.0
    assert graph.weight("src/auth.py", "missing.py") == 0.0
    assert graph.weight("src\\auth.py", "src\\tokens.py") == pytest.approx(2 / 3)

    # max_files_per_commit filters out Commit 1 (3 files) when set to 2
    filtered = CoChangeGraph(repo, max_files_per_commit=2)
    filtered.build_from_git_log()
    assert filtered.weight("src/auth.py", "README.md") == 0.0
    assert filtered.weight("src/auth.py", "src/tokens.py") == pytest.approx(0.5)

    # Non-git & missing directories return {} gracefully
    non_git = tmp_path / "plain_dir"
    non_git.mkdir()
    assert CoChangeGraph(non_git).build_from_git_log() == {}
    assert CoChangeGraph(tmp_path / "does_not_exist").build_from_git_log() == {}


def test_cochange_build_from_log_text() -> None:
    raw = (
        "__QG_COMMIT__\n"
        "a.py\n"
        "b.py\n"
        "\n"
        "__QG_COMMIT__\n"
        "a.py\n"
        "c.py\n"
        "d.py\n"
    )
    graph = CoChangeGraph(Path("."), max_files_per_commit=2)
    weights = graph.build_from_log_text(raw)
    assert weights == {("a.py", "b.py"): 1.0, ("b.py", "a.py"): 1.0}
