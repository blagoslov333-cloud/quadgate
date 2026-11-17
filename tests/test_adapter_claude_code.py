"""Unit tests for `ClaudeCodeAdapter` hook translation and `.claude/settings.json` merging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quadgate.adapters.base import HarnessAdapter
from quadgate.adapters.claude_code import ClaudeCodeAdapter
from quadgate.contracts import (
    GateType,
    GateVerdict,
    HarnessType,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_claude_code_adapter_identity_and_infer_gate() -> None:
    adapter = ClaudeCodeAdapter()
    assert isinstance(adapter, HarnessAdapter)
    assert adapter.harness == HarnessType.CLAUDE_CODE

    assert adapter.infer_gate({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}) == (
        GateType.INTAKE
    )
    assert adapter.infer_gate(
        {"hook_event_name": "PreToolUse", "tool_name": "Task", "tool_input": {"prompt": "test"}}
    ) == GateType.SUBAGENT_GOVERNOR

    for tool_name in ("Write", "Edit", "MultiEdit", "Bash"):
        assert (
            adapter.infer_gate({"hook_event_name": "PreToolUse", "tool_name": tool_name})
            == GateType.FIREWALL
        )

    assert adapter.infer_gate({"hook_event_name": "Stop"}) == GateType.STOP_AUDIT
    assert adapter.infer_gate({"hook_event_name": "SubagentStop"}) == GateType.STOP_AUDIT

    with pytest.raises(ValueError, match="Unsupported"):
        adapter.infer_gate({"hook_event_name": "PostToolUse"})

    with pytest.raises(ValueError, match="empty"):
        adapter.infer_gate({})


def test_parse_event_defaults_and_user_prompt_submit(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()

    ev_default = adapter.parse_event(GateType.INTAKE, {"turn_index": 0, "prompt": "List files"})
    assert ev_default.gate == GateType.INTAKE
    assert ev_default.harness == HarnessType.CLAUDE_CODE
    assert ev_default.session_id == "claude-session"
    assert ev_default.turn_index == 1
    assert ev_default.workspace_root == "."
    assert ev_default.prompt_text == "List files"
    assert ev_default.bypass_gates is False

    ev_bypass = adapter.parse_event(
        GateType.INTAKE,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "cc-42",
            "turn_index": 3,
            "cwd": str(tmp_path),
            "prompt_text": "Refactor auth [bypass-gates] now",
        },
    )
    assert ev_bypass.session_id == "cc-42"
    assert ev_bypass.turn_index == 3
    assert ev_bypass.workspace_root == str(tmp_path)
    assert ev_bypass.bypass_gates is True
    assert ev_bypass.metadata["hook_event_name"] == "UserPromptSubmit"

    ev_flag_bypass = adapter.parse_event(
        GateType.INTAKE,
        {"workspace_root": str(tmp_path), "prompt": "Normal text", "bypass_gates": True},
    )
    assert ev_flag_bypass.workspace_root == str(tmp_path)
    assert ev_flag_bypass.bypass_gates is True


def test_parse_event_pre_tool_use_firewall_tools(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()

    ev_write = adapter.parse_event(
        GateType.FIREWALL,
        {
            "hook_event_name": "PreToolUse",
            "session_id": "cc-w",
            "cwd": str(tmp_path),
            "tool_name": "Write",
            "tool_input": {"file_path": "src/app.py", "content": "print('ok')\n"},
        },
    )
    assert ev_write.tool_name == "Write"
    assert ev_write.tool_args == {"file_path": "src/app.py", "content": "print('ok')\n"}
    assert ev_write.touched_files == ["src/app.py"]
    assert ev_write.diff_text == "print('ok')\n"

    ev_edit = adapter.parse_event(
        GateType.FIREWALL,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/auth.py",
                "old_string": "x = 1",
                "new_string": "x = 2",
            },
        },
    )
    assert ev_edit.touched_files == ["src/auth.py"]
    assert ev_edit.diff_text == "x = 2"

    ev_multi = adapter.parse_event(
        GateType.FIREWALL,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "MultiEdit",
            "tool_input": {
                "path": "src/routes.py",
                "paths": ["src/routes.py", "src/models.py"],
                "edits": [
                    {"old_string": "a = 1", "new_string": "a = 10"},
                    {"old_string": "b = 2", "new_string": "b = 20"},
                ],
            },
        },
    )
    assert ev_multi.touched_files == ["src/routes.py", "src/models.py"]
    assert ev_multi.diff_text == "a = 10\nb = 20"

    ev_bash = adapter.parse_event(
        GateType.FIREWALL,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_args": {"command": "pytest tests/ -q"},
        },
    )
    assert ev_bash.tool_name == "Bash"
    assert ev_bash.diff_text == "pytest tests/ -q"


@pytest.mark.parametrize(
    ("model_alias", "expected_tier"),
    [
        ("haiku", ModelTier.FLOOR),
        ("flash_lite", ModelTier.FLOOR),
        ("claude-3-5-haiku-latest", ModelTier.FLOOR),
        ("sonnet", ModelTier.MID),
        ("flash", ModelTier.MID),
        ("claude-sonnet-4-5", ModelTier.MID),
        ("opus", ModelTier.FRONTIER),
        ("pro", ModelTier.FRONTIER),
        ("claude-opus-4-1", ModelTier.FRONTIER),
        ("inherit", None),
    ],
)
def test_parse_event_task_model_tier_mapping(
    model_alias: str,
    expected_tier: ModelTier | None,
) -> None:
    adapter = ClaudeCodeAdapter()
    ev = adapter.parse_event(
        GateType.SUBAGENT_GOVERNOR,
        {
            "hook_event_name": "PreToolUse",
            "session_id": "cc-task",
            "tool_name": "Task",
            "tool_input": {
                "description": "Write unit tests",
                "prompt": "Write unit tests for auth",
                "model": model_alias,
            },
        },
    )
    assert ev.gate == GateType.SUBAGENT_GOVERNOR
    assert ev.metadata["subagent_model"] == model_alias
    assert ev.current_tier == expected_tier
    assert ev.prompt_text == "Write unit tests for auth"

    # Explicit current_tier takes precedence over Task model alias.
    ev_explicit = adapter.parse_event(
        GateType.SUBAGENT_GOVERNOR,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Task",
            "current_tier": "mid",
            "tool_input": {"prompt": "Work", "model": "haiku"},
        },
    )
    assert ev_explicit.current_tier == ModelTier.MID
    assert ev_explicit.metadata["subagent_model"] == "haiku"


def test_parse_event_stop_and_subagent_stop() -> None:
    adapter = ClaudeCodeAdapter()

    ev_stop = adapter.parse_event(
        GateType.STOP_AUDIT,
        {
            "hook_event_name": "Stop",
            "session_id": "cc-stop",
            "cwd": "/repo",
            "prompt": "Fix bug and add tests",
            "touched_files": ["src/a.py", "tests/test_a.py"],
            "diff_text": "+def test_a(): pass",
            "stop_hook_active": True,
        },
    )
    assert ev_stop.gate == GateType.STOP_AUDIT
    assert ev_stop.prompt_text == "Fix bug and add tests"
    assert ev_stop.touched_files == ["src/a.py", "tests/test_a.py"]
    assert ev_stop.diff_text == "+def test_a(): pass"
    assert ev_stop.continuation_active is True
    assert ev_stop.metadata["continuation_active"] is True
    assert ev_stop.metadata["hook_event_name"] == "Stop"

    ev_sub_stop = adapter.parse_event(
        GateType.STOP_AUDIT,
        {
            "hook_event_name": "SubagentStop",
            "session_id": "cc-substop",
            "continuation_active": False,
        },
    )
    assert ev_sub_stop.continuation_active is False
    assert ev_sub_stop.metadata["continuation_active"] is False
    assert ev_sub_stop.metadata["hook_event_name"] == "SubagentStop"


def test_format_verdict_user_prompt_submit_intake() -> None:
    adapter = ClaudeCodeAdapter()

    code_allow, out_allow, err_allow = adapter.format_verdict(
        GateType.INTAKE,
        GateVerdict(action=VerdictAction.ALLOW, gate=GateType.INTAKE),
    )
    assert (code_allow, out_allow, err_allow) == (0, "", "")

    code_aug, out_aug, err_aug = adapter.format_verdict(
        GateType.INTAKE,
        GateVerdict(
            action=VerdictAction.AUGMENT,
            gate=GateType.INTAKE,
            reason="[QUADGATE_MODE: Read-only brainstorm detected; confirm before mutating files]",
            tail_hints=["quadgate/contracts.py", "quadgate/adapters/claude_code.py"],
        ),
    )
    assert code_aug == 0
    assert err_aug == ""
    assert isinstance(out_aug, str)
    assert "[QUADGATE_MODE: Read-only brainstorm detected" in out_aug
    assert "[QUADGATE_CONTEXT_HINTS]" in out_aug
    assert "- quadgate/contracts.py" in out_aug
    assert "- quadgate/adapters/claude_code.py" in out_aug

    receipt = ViolationReceipt(
        rule_id="INV_01_SECRET_EXPOSURE",
        score=1.0,
        stage="deterministic_2a",
        remediation="Remove secret from prompt.",
    )
    code_blk, out_blk, err_blk = adapter.format_verdict(
        GateType.INTAKE,
        GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.INTAKE,
            violation_receipt=receipt,
            reason="Private key detected",
        ),
    )
    assert code_blk == 2
    assert out_blk == ""
    assert "INV_01_SECRET_EXPOSURE" in err_blk
    assert "Remove secret from prompt." in err_blk


def test_format_verdict_pre_tool_use_firewall_and_task() -> None:
    adapter = ClaudeCodeAdapter()

    assert adapter.format_verdict(
        GateType.FIREWALL,
        GateVerdict(action=VerdictAction.ALLOW, gate=GateType.FIREWALL),
    ) == (0, "", "")

    receipt = ViolationReceipt(
        rule_id="INV_02_CAGE_CONTAINMENT",
        score=0.95,
        stage="deterministic_2a",
        remediation="Keep edits inside assigned workspace files.",
        violated_path="../outside.py",
    )
    code_blk, out_blk, err_blk = adapter.format_verdict(
        GateType.FIREWALL,
        GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.FIREWALL,
            violation_receipt=receipt,
            reason="Path escapes assigned file cage",
        ),
    )
    assert code_blk == 2
    assert out_blk == ""
    assert err_blk.startswith("[QUADGATE BLOCK] INV_02_CAGE_CONTAINMENT:")
    assert "Keep edits inside assigned workspace files." in err_blk
    assert "(Reason: Path escapes assigned file cage)" in err_blk
    assert receipt.bypass_instruction in err_blk

    code_mod, out_mod, err_mod = adapter.format_verdict(
        GateType.SUBAGENT_GOVERNOR,
        GateVerdict(
            action=VerdictAction.MODIFY,
            gate=GateType.SUBAGENT_GOVERNOR,
            updated_tool_args={"prompt": "Write unit test", "subagent_type": "general-purpose"},
            updated_model="haiku",
            target_tier=ModelTier.FLOOR,
        ),
    )
    assert code_mod == 0
    assert err_mod == ""
    assert out_mod == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "prompt": "Write unit test",
                "subagent_type": "general-purpose",
                "model": "haiku",
            },
        }
    }

    code_pro, out_pro, err_pro = adapter.format_verdict(
        GateType.SUBAGENT_GOVERNOR,
        GateVerdict(
            action=VerdictAction.PROMOTE,
            gate=GateType.SUBAGENT_GOVERNOR,
            target_tier=ModelTier.FRONTIER,
            updated_model="opus",
            reason="High blast radius requires frontier model.",
        ),
    )
    assert code_pro == 2
    assert out_pro == ""
    assert "QUADGATE_PROMOTE" in err_pro
    assert "frontier" in err_pro
    assert "opus" in err_pro


def test_format_verdict_stop_and_subagent_stop() -> None:
    adapter = ClaudeCodeAdapter()

    assert adapter.format_verdict(
        GateType.STOP_AUDIT,
        GateVerdict(action=VerdictAction.ALLOW, gate=GateType.STOP_AUDIT),
    ) == (0, "", "")

    code_clauses, out_clauses, err_clauses = adapter.format_verdict(
        GateType.STOP_AUDIT,
        GateVerdict(
            action=VerdictAction.AUGMENT,
            gate=GateType.STOP_AUDIT,
            unaddressed_clauses=["Add unit tests", "Update changelog"],
            reason="Directive completeness 0.67 < 0.85",
        ),
    )
    assert code_clauses == 2
    assert isinstance(out_clauses, dict)
    assert out_clauses["decision"] == "block"
    assert out_clauses["reason"] == err_clauses
    assert "Add unit tests" in err_clauses
    assert "Update changelog" in err_clauses
    assert "Directive completeness 0.67 < 0.85" in err_clauses

    code_promo, out_promo, err_promo = adapter.format_verdict(
        GateType.STOP_AUDIT,
        GateVerdict(
            action=VerdictAction.PROMOTE,
            gate=GateType.STOP_AUDIT,
            target_tier=ModelTier.MID,
            updated_model="sonnet",
            reason="Gate 4 py_compile failed on floor tier.",
        ),
    )
    assert code_promo == 2
    assert isinstance(out_promo, dict)
    assert out_promo == {"decision": "block", "reason": err_promo}
    assert "QUADGATE_PROMOTE" in err_promo
    assert "sonnet" in err_promo
    assert "py_compile failed" in err_promo

    receipt = ViolationReceipt(
        rule_id="GATE4_ONE_STRIKE_CIRCUIT_BREAKER",
        score=1.0,
        stage="deterministic_2a",
        remediation="Circuit breaker halted after promoted tier failure.",
        strike_count=2,
        escalate_to_human=True,
    )
    code_blk, out_blk, err_blk = adapter.format_verdict(
        GateType.STOP_AUDIT,
        GateVerdict(
            action=VerdictAction.BLOCK,
            gate=GateType.STOP_AUDIT,
            violation_receipt=receipt,
            unaddressed_clauses=["Fix syntax error"],
            reason="Human review required",
        ),
    )
    assert code_blk == 2
    assert isinstance(out_blk, dict)
    assert out_blk == {"decision": "block", "reason": err_blk}
    assert "GATE4_ONE_STRIKE_CIRCUIT_BREAKER" in err_blk
    assert "Fix syntax error" in err_blk
    assert "Human review required" in err_blk


def test_install_hooks_fresh_merge_and_idempotent(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()

    # 1. Fresh workspace installation
    fresh_ws = tmp_path / "fresh_repo"
    fresh_ws.mkdir()
    settings_path = adapter.install_hooks(fresh_ws)
    assert settings_path == fresh_ws / ".claude" / "settings.json"
    assert settings_path.is_file()

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert len(hooks["UserPromptSubmit"]) == 1
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["command"] == (
        "qg-shim --harness claude_code --gate intake"
    )
    assert len(hooks["PreToolUse"]) == 2
    assert hooks["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit|Bash"
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == (
        "qg-shim --harness claude_code --gate firewall"
    )
    assert hooks["PreToolUse"][1]["matcher"] == "Task"
    assert hooks["PreToolUse"][1]["hooks"][0]["command"] == (
        "qg-shim --harness claude_code --gate subagent_governor"
    )
    assert hooks["Stop"][0]["hooks"][0]["command"] == (
        "qg-shim --harness claude_code --gate stop_audit"
    )
    assert hooks["SubagentStop"][0]["hooks"][0]["command"] == (
        "qg-shim --harness claude_code --gate stop_audit"
    )

    # 2. Existing settings + custom hooks preserved and idempotent on repeated calls
    existing_ws = tmp_path / "existing_repo"
    (existing_ws / ".claude").mkdir(parents=True)
    seed_settings: dict[str, Any] = {
        "permissions": {"allow": ["Bash(pytest *)"], "deny": ["Read(.env)"]},
        "env": {"CUSTOM_VAR": "1"},
        "model": "sonnet",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Read",
                    "hooks": [{"type": "command", "command": "echo audit-read"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "ruff check"}],
                }
            ],
        },
    }
    target = existing_ws / ".claude" / "settings.json"
    target.write_text(json.dumps(seed_settings, indent=2), encoding="utf-8")

    adapter.install_hooks(existing_ws)
    first_pass_text = target.read_text(encoding="utf-8")
    first_pass = json.loads(first_pass_text)

    assert first_pass["permissions"] == seed_settings["permissions"]
    assert first_pass["env"] == seed_settings["env"]
    assert first_pass["model"] == "sonnet"
    assert first_pass["hooks"]["PostToolUse"] == seed_settings["hooks"]["PostToolUse"]
    assert first_pass["hooks"]["PreToolUse"][0] == seed_settings["hooks"]["PreToolUse"][0]
    assert len(first_pass["hooks"]["PreToolUse"]) == 3
    assert len(first_pass["hooks"]["UserPromptSubmit"]) == 1
    assert len(first_pass["hooks"]["Stop"]) == 1
    assert len(first_pass["hooks"]["SubagentStop"]) == 1

    # Repeated calls must be byte-for-byte idempotent.
    adapter.install_hooks(existing_ws)
    assert target.read_text(encoding="utf-8") == first_pass_text

    # 3. Merging over a copy of repo `.claude/settings.json` adds SubagentStop cleanly.
    repo_copy_ws = tmp_path / "repo_copy"
    (repo_copy_ws / ".claude").mkdir(parents=True)
    repo_settings_text = (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    (repo_copy_ws / ".claude" / "settings.json").write_text(repo_settings_text, encoding="utf-8")

    merged_path = adapter.install_hooks(repo_copy_ws)
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert len(merged["hooks"]["UserPromptSubmit"]) == 1
    assert len(merged["hooks"]["PreToolUse"]) == 2
    assert len(merged["hooks"]["Stop"]) == 1
    assert len(merged["hooks"]["SubagentStop"]) == 1
