"""Release polish validation tests for README, docs, community health, assets, and CI."""

from __future__ import annotations

import ast
import json
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from quadgate.contracts import CartridgeSpec
from quadgate.drivers.base import ClassifierDriver

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LATEST_PROOF_PATH = REPO_ROOT / "benchmarks" / "reports" / "LATEST_PROOF.md"
LLMS_TXT_PATH = REPO_ROOT / "llms.txt"
LLMS_FULL_TXT_PATH = REPO_ROOT / "llms-full.txt"
SECURITY_PATH = REPO_ROOT / "SECURITY.md"
COC_PATH = REPO_ROOT / "CODE_OF_CONDUCT.md"
BUG_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
FEATURE_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml"
PR_TEMPLATE_PATH = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
SOCIAL_SVG_PATH = REPO_ROOT / "assets" / "social-preview.svg"
SOCIAL_PNG_PATH = REPO_ROOT / "assets" / "social-preview.png"
DEMO_TAPE_PATH = REPO_ROOT / "assets" / "demo.tape"
DEMO_SVG_PATH = REPO_ROOT / "assets" / "demo.svg"
EXAMPLES_DIR = REPO_ROOT / "examples"

_FENCED_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_-]+)\r?\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _extract_fenced_blocks(markdown_text: str, language: str) -> list[str]:
    return [
        body.strip("\r\n")
        for lang, body in _FENCED_BLOCK_RE.findall(markdown_text)
        if lang.strip().lower() == language.lower()
    ]


def test_release_files_exist() -> None:
    assert README_PATH.is_file(), "README.md must exist at repository root."
    assert CONTRIBUTING_PATH.is_file(), "CONTRIBUTING.md must exist at repository root."
    assert CI_WORKFLOW_PATH.is_file(), ".github/workflows/ci.yml must exist."
    assert LATEST_PROOF_PATH.is_file(), "benchmarks/reports/LATEST_PROOF.md must exist."


def test_readme_architecture_harnesses_drivers_and_benchmarks() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    proof = LATEST_PROOF_PATH.read_text(encoding="utf-8")

    # 4-Gate architecture pillars
    for marker in (
        "[QUADGATE_CONTEXT_HINTS]",
        "tree-sitter",
        "FTS5",
        "2..N",
        "< 2 ms",
        "qg-shim",
        "Stage-2A",
        "Stage-2B",
        "127.0.0.1:8788",
        "ECC",
        "R_hist",
        "< 800 ms",
        "1-Strike",
    ):
        assert marker in readme, f"README.md missing architecture marker: {marker!r}"

    # 4 Harness quickstarts & commands
    for cmd in (
        "quadgate init --harness antigravity",
        ".agents/hooks.json",
        "quadgate daemon start",
        "quadgate doctor",
        "quadgate init --harness claude-code",
        ".claude/settings.json",
        "updatedInput",
        "quadgate init --harness codex",
        "quadgate exec-codex",
        "OPENAI_BASE_URL=http://127.0.0.1:8788/v1",
        "quadgate init --harness mcp",
        ".cursor/mcp.json",
        "quadgate mcp",
        "quadgate_context_sieve",
        "quadgate_verify_mutation",
        "quadgate_audit_completion",
    ):
        assert cmd in readme, f"README.md missing harness/command reference: {cmd!r}"

    # Classifier swap table + calibration + config snippet
    for driver_id in (
        "decision1",
        "jev",
        "layaa",
        "onnx_local",
        "logprob_fallback",
        "zero_dep",
    ):
        assert driver_id in readme, f"README.md missing classifier driver: {driver_id!r}"

    for cfg_marker in (
        "800",
        "32",
        "quadgate calibrate --driver",
        "quadgate.toml",
        "active_driver",
        "max_calibration_ece",
    ):
        assert cfg_marker in readme, f"README.md missing classifier config marker: {cfg_marker!r}"

    # Empirical A/B proof link, reproduction commands, and exact LATEST_PROOF.md metrics
    assert "benchmarks/reports/LATEST_PROOF.md" in readme
    assert "quadgate bench --dry-replay" in readme
    assert "quadgate bench --live --suite all --runs 3" in readme

    exact_proof_metrics = (
        "88.6%",
        "100.0%",
        "+11.4 pp",
        "22.5%",
        "56.3%",
        "+33.8 pp",
        "61.0%",
        "[57.6%, 64.4%]",
        "$24.2967",
        "$5.6089",
        "$0.0091",
        "$0.0902",
        "76.9%",
        "[59.2%, 94.6%]",
        "19335.5 / 23837.0 / 19364.4",
        "4889.3 / 8870.1 / 5605.1",
        "71.1%",
        "[67.1%, 75.0%]",
        "90.0%",
        "21.4%",
        "55.0%",
        "63.5%",
        "$14.8950",
        "$2.5325",
        "83.0%",
        "19449.2 / 23583.5 / 19343.3",
        "5952.2 / 8891.8 / 6154.5",
        "68.2%",
        "86.7%",
        "23.9%",
        "57.8%",
        "$9.4018",
        "$3.0763",
        "67.3%",
        "19335.5 / 23837.0 / 19392.6",
        "3814.2 / 7951.5 / 4872.5",
        "74.9%",
    )
    for metric in exact_proof_metrics:
        assert metric in proof, f"Sanity check failed: {metric!r} not in LATEST_PROOF.md"
        assert metric in readme, f"README.md missing exact proof metric: {metric!r}"

    # Ensure currency '$' signs in README prose/tables are wrapped in code spans (no stray math '$' pairs)
    no_fences = _FENCED_BLOCK_RE.sub("", readme)
    no_inline = _INLINE_CODE_RE.sub("", no_fences)
    assert "$" not in no_inline, (
        "README.md contains unescaped '$' outside code spans, which risks Markdown math misparsing."
    )


def test_ci_workflow_matrix_and_steps() -> None:
    raw_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed: Any = yaml.safe_load(raw_text)
    assert isinstance(parsed, dict)

    # PyYAML parses unquoted YAML 1.1 `on:` key as True
    triggers = parsed.get("on") if "on" in parsed else parsed.get(True)
    assert isinstance(triggers, dict), "ci.yml must define `on` triggers."
    assert "push" in triggers and "pull_request" in triggers
    push_branches = triggers["push"].get("branches", [])
    assert "main" in push_branches and "master" in push_branches

    jobs = parsed.get("jobs", {})
    assert isinstance(jobs, dict) and jobs, "ci.yml must define at least one job."
    job = next(iter(jobs.values()))
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert set(str(v) for v in matrix["python-version"]) >= {"3.11", "3.12"}

    steps = job["steps"]
    assert isinstance(steps, list)
    uses_list = [s.get("uses", "") for s in steps if isinstance(s, dict)]
    assert any(u.startswith("actions/checkout@v4") for u in uses_list)
    setup_py = [
        s for s in steps if isinstance(s, dict) and s.get("uses", "").startswith("actions/setup-python@v5")
    ]
    assert setup_py, "ci.yml must use actions/setup-python@v5."
    assert setup_py[0].get("with", {}).get("cache") == "pip"

    run_commands = "\n".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
    assert 'pip install -e ".[dev]"' in run_commands
    assert "mypy --follow-imports=silent quadgate benchmarks" in run_commands
    assert "pytest -v" in run_commands
    assert "python -m quadgate.cli doctor" in run_commands
    assert "python -m quadgate.cli bench --dry-replay" in run_commands


def test_contributing_driver_and_cartridge_examples_valid_and_under_100_lines() -> None:
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")

    for req_ref in (
        "quadgate/drivers/base.py",
        "quadgate/drivers/__init__.py",
        "quadgate/cli.py",
        "quadgate calibrate --driver",
        "benchmarks/calibration_gold.jsonl",
        "pytest -v",
        "mypy",
        "quadgate doctor",
        "quadgate bench --dry-replay",
    ):
        assert req_ref in contributing, f"CONTRIBUTING.md missing reference: {req_ref!r}"

    py_blocks = _extract_fenced_blocks(contributing, "python")
    assert py_blocks, "CONTRIBUTING.md must include a ```python``` ClassifierDriver block."
    driver_code = py_blocks[0]
    driver_lines = driver_code.splitlines()
    assert 1 <= len(driver_lines) < 100, (
        f"ClassifierDriver snippet must be < 100 lines, got {len(driver_lines)}."
    )

    tree = ast.parse(driver_code)
    assert isinstance(tree, ast.Module)
    compiled = compile(tree, "<contributing_driver_example>", "exec")
    namespace: dict[str, Any] = {}
    exec(compiled, namespace)  # noqa: S102 - validates documented contributor example

    driver_subclasses = [
        obj
        for obj in namespace.values()
        if isinstance(obj, type)
        and issubclass(obj, ClassifierDriver)
        and obj is not ClassifierDriver
    ]
    assert len(driver_subclasses) == 1, "Python block must define exactly one ClassifierDriver subclass."
    driver_cls = driver_subclasses[0]
    assert not getattr(driver_cls, "__abstractmethods__", set())
    instance = driver_cls()
    assert isinstance(instance.driver_id, str) and instance.driver_id
    assert instance.capabilities.max_candidates_per_pass >= 1

    yaml_blocks = _extract_fenced_blocks(contributing, "yaml")
    assert yaml_blocks, "CONTRIBUTING.md must include a ```yaml``` DomainCartridge block."
    cartridge_yaml = yaml_blocks[0]
    cartridge_lines = cartridge_yaml.splitlines()
    assert 1 <= len(cartridge_lines) < 100, (
        f"DomainCartridge YAML snippet must be < 100 lines, got {len(cartridge_lines)}."
    )

    loaded_yaml = yaml.safe_load(cartridge_yaml)
    assert isinstance(loaded_yaml, dict)
    spec = CartridgeSpec.model_validate(loaded_yaml)
    assert spec.name
    assert spec.version
    assert spec.mutation_tools
    assert spec.invariants
    assert spec.truth_oracle.timeout_ms <= 10000


def test_readme_conversion_wireframe_and_hero_links() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    for marker in (
        "# QuadGate",
        "blagoslov333-cloud/quadgate",
        "Apache-2.0",
        "## What Is QuadGate?",
        "assets/demo.svg",
        "## Reproducible Benchmarks",
        "## 60-Second Quickstart",
        "## Why Existing Approaches Fail",
        "## Architecture",
        "flowchart LR",
        "## Drop-In Integrations & Ecosystem Presets",
        "## Feature Comparison",
        "## Roadmap & Contributing",
        "## License",
        "examples/antigravity/hooks.json",
        "examples/claude-code/settings.json",
        "examples/codex/profile.sh",
        "examples/cursor/mcp.json",
        "python -m quadgate.cli bench --dry-replay",
    ):
        assert marker in readme, f"README.md missing wireframe element: {marker!r}"

    # Benchmarks must appear above Quickstart and Architecture (above-the-fold conversion order)
    idx_bench = readme.index("## Reproducible Benchmarks")
    idx_quick = readme.index("## 60-Second Quickstart")
    idx_arch = readme.index("## Architecture")
    assert idx_bench < idx_quick < idx_arch


def test_llms_txt_and_llms_full_txt() -> None:
    assert LLMS_TXT_PATH.is_file(), "llms.txt must exist at repository root."
    assert LLMS_FULL_TXT_PATH.is_file(), "llms-full.txt must exist at repository root."

    llms_txt = LLMS_TXT_PATH.read_text(encoding="utf-8")
    words = llms_txt.split()
    assert 50 <= len(words) < 1500, f"llms.txt must stay concise (<2000 tokens), got {len(words)} words."
    assert len(llms_txt) < 8000, f"llms.txt exceeds concise char budget ({len(llms_txt)} chars)."

    for ref in (
        "# QuadGate",
        "https://github.com/blagoslov333-cloud/quadgate",
        "https://raw.githubusercontent.com/blagoslov333-cloud/quadgate/main/README.md",
        "https://raw.githubusercontent.com/blagoslov333-cloud/quadgate/main/llms-full.txt",
        "https://raw.githubusercontent.com/blagoslov333-cloud/quadgate/main/CONTRIBUTING.md",
        "Apache-2.0",
    ):
        assert ref in llms_txt, f"llms.txt missing reference: {ref!r}"

    llms_full = LLMS_FULL_TXT_PATH.read_text(encoding="utf-8")
    for ref in (
        "quadgate init",
        "quadgate daemon",
        "quadgate calibrate",
        "quadgate doctor",
        "quadgate bench",
        "quadgate exec-codex",
        "quadgate mcp",
        "quadgate.toml",
        "GateEvent",
        "GateVerdict",
        "CartridgeSpec",
        "6.19 ms",
        "7.92 ms",
        "9.17 ms",
        "decision1",
        "jev",
        "layaa",
        "onnx_local",
        "logprob_fallback",
        "zero_dep",
        ".agents/hooks.json",
        ".claude/settings.json",
        ".codex/profile.sh",
        ".cursor/mcp.json",
    ):
        assert ref in llms_full, f"llms-full.txt missing reference: {ref!r}"


def test_community_health_and_github_templates() -> None:
    assert SECURITY_PATH.is_file(), "SECURITY.md must exist."
    assert COC_PATH.is_file(), "CODE_OF_CONDUCT.md must exist."
    assert BUG_TEMPLATE_PATH.is_file(), "bug_report.yml must exist."
    assert FEATURE_TEMPLATE_PATH.is_file(), "feature_request.yml must exist."
    assert PR_TEMPLATE_PATH.is_file(), "PULL_REQUEST_TEMPLATE.md must exist."

    sec_text = SECURITY_PATH.read_text(encoding="utf-8")
    assert "https://github.com/blagoslov333-cloud/quadgate/security/advisories/new" in sec_text
    assert "C:\\Users\\" not in sec_text
    assert "/Users/" not in sec_text

    coc_text = COC_PATH.read_text(encoding="utf-8")
    assert "Contributor Covenant" in coc_text
    assert "2.1" in coc_text

    for tmpl_path in (BUG_TEMPLATE_PATH, FEATURE_TEMPLATE_PATH):
        parsed: Any = yaml.safe_load(tmpl_path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        assert isinstance(parsed.get("name"), str) and parsed["name"]
        assert isinstance(parsed.get("description"), str) and parsed["description"]
        assert isinstance(parsed.get("body"), list) and parsed["body"]

    pr_text = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
    for check in ("pytest -v", "mypy", "doctor", "bench --dry-replay"):
        assert check in pr_text, f"PULL_REQUEST_TEMPLATE.md missing check: {check!r}"


def test_visual_assets_social_preview_and_demo() -> None:
    for asset in (SOCIAL_SVG_PATH, SOCIAL_PNG_PATH, DEMO_TAPE_PATH, DEMO_SVG_PATH):
        assert asset.is_file(), f"Missing visual asset: {asset}"

    svg_root = ET.fromstring(SOCIAL_SVG_PATH.read_text(encoding="utf-8"))
    assert svg_root.attrib.get("width") == "1280"
    assert svg_root.attrib.get("height") == "640"
    svg_raw = SOCIAL_SVG_PATH.read_text(encoding="utf-8")
    assert "#080707" in svg_raw
    assert "#F59E0B" in svg_raw
    svg_texts = [
        "".join(elem.itertext()).strip()
        for elem in svg_root.iter()
        if elem.tag.endswith("text")
    ]
    svg_joined = " ".join(svg_texts)
    for marker in ("-62.1%", "-39.1%", "3.1x", "100%", "1,293", "QuadGate"):
        assert marker in svg_joined, f"Social preview SVG missing verified metric/marker: {marker!r}"

    png_bytes = SOCIAL_PNG_PATH.read_bytes()
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", "Invalid PNG signature."
    assert png_bytes[12:16] == b"IHDR", "Missing PNG IHDR chunk."
    width, height = struct.unpack(">II", png_bytes[16:24])
    assert (width, height) == (1280, 640), f"Expected 1280x640 PNG, got {width}x{height}."
    assert len(png_bytes) < 250_000, f"Social preview PNG too large: {len(png_bytes)} bytes."

    tape_text = DEMO_TAPE_PATH.read_text(encoding="utf-8")
    for marker in (
        "Set Width 1200",
        "Set Height 620",
        "Set FontSize 20",
        'Set Theme "GitHub Dark"',
        "quadgate init",
        "quadgate doctor",
        "quadgate bench --dry-replay",
    ):
        assert marker in tape_text, f"assets/demo.tape missing: {marker!r}"

    demo_svg_raw = DEMO_SVG_PATH.read_text(encoding="utf-8")
    demo_root = ET.fromstring(demo_svg_raw)
    assert demo_root.tag.endswith("svg")
    demo_rendered = "".join(demo_root.itertext())
    for marker in (
        "QuadGate doctor: OK",
        "0.822 ms",
        "quadgate bench --dry-replay",
        "$8.0989",
        "$1.8694",
    ):
        assert marker in demo_rendered, f"assets/demo.svg missing observed metric/output: {marker!r}"


def test_ecosystem_example_presets_valid() -> None:
    ag_path = EXAMPLES_DIR / "antigravity" / "hooks.json"
    cc_path = EXAMPLES_DIR / "claude-code" / "settings.json"
    codex_path = EXAMPLES_DIR / "codex" / "profile.sh"
    cursor_path = EXAMPLES_DIR / "cursor" / "mcp.json"

    for p in (ag_path, cc_path, codex_path, cursor_path):
        assert p.is_file(), f"Missing example preset: {p}"

    ag_data = json.loads(ag_path.read_text(encoding="utf-8"))
    assert ag_data.get("enabled") is True
    assert ag_data["PreInvocation"][0]["command"] == "qg-shim --harness antigravity --gate intake"
    assert len(ag_data["PreToolUse"]) == 2
    assert ag_data["Stop"][0]["command"] == "qg-shim --harness antigravity --gate stop_audit"

    cc_data = json.loads(cc_path.read_text(encoding="utf-8"))
    cc_hooks = cc_data["hooks"]
    for key in ("UserPromptSubmit", "PreToolUse", "Stop", "SubagentStop"):
        assert key in cc_hooks and isinstance(cc_hooks[key], list)

    codex_text = codex_path.read_text(encoding="utf-8")
    assert codex_text.startswith("#!/usr/bin/env bash")
    assert 'OPENAI_BASE_URL="http://127.0.0.1:8788/v1"' in codex_text
    assert "quadgate exec-codex" in codex_text

    cursor_data = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor_data["mcpServers"]["quadgate"] == {"command": "quadgate", "args": ["mcp"]}
