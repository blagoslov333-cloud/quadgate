"""Tests for `quadgate.adapters.antigravity.AntigravityAdapter`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quadgate.adapters.antigravity import (
    FIREWALL_MATCHER,
    SUBAGENT_MATCHER,
    AntigravityAdapter,
)
from quadgate.contracts import (
    GateType,
    GateVerdict,
    HarnessType,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.kernel.gates import GateEngine, load_cartridge


@pytest.fixture
def adapter() -> AntigravityAdapter:
    return AntigravityAdapter()


def test_harness_and_infer_gate(adapter: AntigravityAdapter) -> None:
    assert adapter.harness == HarnessType.ANTIGRAVITY

    assert adapter.infer_gate({"hookEventName": "PreInvocation"}) == GateType.INTAKE
    assert (
        adapter.infer_gate(
            {
                "hookEventName": "PreToolUse",
                "toolCall": {"name": "invoke_subagent", "args": {"Prompt": "Add tests"}},
            }
        )
        == GateType.SUBAGENT_GOVERNOR
    )
    assert (
        adapter.infer_gate(
            {
                "hookEventName": "PreToolUse",
                "tool_name": "invoke_subagent",
            }
        )
        == GateType.SUBAGENT_GOVERNOR
    )
    assert (
        adapter.infer_gate(
            {
                "hookEventName": "PreToolUse",
                "toolCall": {"name": "write_to_file", "args": {"TargetFile": "src/a.py"}},
            }
        )
        == GateType.FIREWALL
    )
    assert (
        adapter.infer_gate({"hookEventName": "PreToolUse", "tool_name": "run_command"})
        == GateType.FIREWALL
    )
    assert adapter.infer_gate({"hookEventName": "Stop"}) == GateType.STOP_AUDIT

    with pytest.raises(ValueError, match="Unsupported Antigravity hookEventName"):
        adapter.infer_gate({"hookEventName": "UnknownHook"})


def test_parse_event_intake_and_common_fallbacks(adapter: AntigravityAdapter) -> None:
    event = adapter.parse_event(
        GateType.INTAKE,
        {
            "hookEventName": "PreInvocation",
            "conversationId": "conv-42",
            "invocationNum": 3,
            "workspacePaths": ["f:/workspaces/demo", "f:/workspaces/other"],
            "lastUserInput": "Refactor parser [bypass-gates]",
        },
    )
    assert event.gate == GateType.INTAKE
    assert event.harness == HarnessType.ANTIGRAVITY
    assert event.session_id == "conv-42"
    assert event.turn_index == 3
    assert event.workspace_root == "f:/workspaces/demo"
    assert event.prompt_text == "Refactor parser [bypass-gates]"
    assert event.bypass_gates is True
    assert event.metadata["hook_event_name"] == "PreInvocation"

    fallback_event = adapter.parse_event(
        GateType.INTAKE,
        {
            "session_id": "sess-fallback",
            "stepIdx": 0,
            "workspacePaths": [],
            "cwd": "f:/fallback-cwd",
            "prompt": "Explain module",
        },
    )
    assert fallback_event.session_id == "sess-fallback"
    assert fallback_event.turn_index == 1
    assert fallback_event.workspace_root == "f:/fallback-cwd"
    assert fallback_event.prompt_text == "Explain module"
    assert fallback_event.bypass_gates is False

    minimal_event = adapter.parse_event(GateType.INTAKE, {"turn_index": -4, "bypass_gates": True})
    assert minimal_event.session_id == "antigravity-session"
    assert minimal_event.turn_index == 1
    assert minimal_event.workspace_root == "."
    assert minimal_event.bypass_gates is True


def test_parse_event_firewall_and_subagent_governor(adapter: AntigravityAdapter) -> None:
    fw_event = adapter.parse_event(
        GateType.FIREWALL,
        {
            "hookEventName": "PreToolUse",
            "conversationId": "conv-fw",
            "executionNum": 2,
            "workspace_root": "/repo",
            "toolCall": {
                "id": "call-99",
                "name": "write_to_file",
                "args": {
                    "TargetFile": "src/service.py",
                    "CodeContent": "def run():\n    return 1\n",
                },
            },
        },
    )
    assert fw_event.gate == GateType.FIREWALL
    assert fw_event.turn_index == 2
    assert fw_event.workspace_root == "/repo"
    assert fw_event.tool_name == "write_to_file"
    assert fw_event.tool_args["TargetFile"] == "src/service.py"
    assert fw_event.touched_files == ["src/service.py"]
    assert fw_event.diff_text == "def run():\n    return 1\n"
    assert fw_event.metadata["tool_call_id"] == "call-99"

    top_level_fw = adapter.parse_event(
        GateType.FIREWALL,
        {
            "tool_name": "replace_file_content",
            "tool_args": {
                "file_path": "src/helper.py",
                "ReplacementContent": "VALUE = 42",
            },
        },
    )
    assert top_level_fw.tool_name == "replace_file_content"
    assert top_level_fw.touched_files == ["src/helper.py"]
    assert top_level_fw.diff_text == "VALUE = 42"

    tier_cases = [
        ("flash_lite", ModelTier.FLOOR),
        ("flash", ModelTier.MID),
        ("pro", ModelTier.FRONTIER),
        ("floor", ModelTier.FLOOR),
        ("mid", ModelTier.MID),
        ("frontier", ModelTier.FRONTIER),
        ("inherit", None),
    ]
    for model_str, expected_tier in tier_cases:
        sub_event = adapter.parse_event(
            GateType.SUBAGENT_GOVERNOR,
            {
                "hookEventName": "PreToolUse",
                "toolCall": {
                    "id": f"sub-{model_str}",
                    "name": "invoke_subagent",
                    "arguments": {
                        "Model": model_str,
                        "Prompt": "Write unit tests",
                        "path": "tests/test_app.py",
                    },
                },
            },
        )
        assert sub_event.gate == GateType.SUBAGENT_GOVERNOR
        assert sub_event.tool_name == "invoke_subagent"
        assert sub_event.metadata["subagent_model"] == model_str
        assert sub_event.current_tier == expected_tier
        assert sub_event.prompt_text == "Write unit tests"
        assert sub_event.touched_files == ["tests/test_app.py"]


def test_parse_event_stop_audit(adapter: AntigravityAdapter) -> None:
    event = adapter.parse_event(
        GateType.STOP_AUDIT,
        {
            "hookEventName": "Stop",
            "conversationId": "conv-stop",
            "stepIdx": 4,
            "workspacePaths": ["/workspace"],
            "lastUserInput": "Implement login and write tests",
            "touchedFiles": ["src/auth.py", "tests/test_auth.py"],
            "diffText": "+def login(): pass",
            "continuationActive": True,
            "terminationReason": "end_turn",
            "modelName": "flash_lite",
        },
    )
    assert event.gate == GateType.STOP_AUDIT
    assert event.turn_index == 4
    assert event.prompt_text == "Implement login and write tests"
    assert event.touched_files == ["src/auth.py", "tests/test_auth.py"]
    assert event.diff_text == "+def login(): pass"
    assert event.continuation_active is True
    assert event.current_tier == ModelTier.FLOOR
    assert event.metadata["continuation_active"] is True
    assert event.metadata["termination_reason"] == "end_turn"
    assert event.metadata["model_name"] == "flash_lite"


def test_format_verdict_intake_roundtrip(adapter: AntigravityAdapter) -> None:
    allow_verdict = GateVerdict(action=VerdictAction.ALLOW, gate=GateType.INTAKE)
    assert adapter.format_verdict(GateType.INTAKE, allow_verdict) == (0, {}, "")

    empty_augment = GateVerdict(action=VerdictAction.AUGMENT, gate=GateType.INTAKE)
    assert adapter.format_verdict(GateType.INTAKE, empty_augment) == (0, {}, "")

    aug_verdict = GateVerdict(
        action=VerdictAction.AUGMENT,
        gate=GateType.INTAKE,
        reason="[QUADGATE_MODE: Read-only brainstorm detected; confirm before mutating files]",
        tail_hints=["quadgate/contracts.py", "quadgate/adapters/antigravity.py"],
    )
    code, payload, stderr = adapter.format_verdict(GateType.INTAKE, aug_verdict)
    assert code == 0
    assert stderr == ""
    assert isinstance(payload, dict)
    assert payload["terminationBehavior"] == ""
    assert "[QUADGATE_MODE:" in payload["additionalContext"]
    assert "[QUADGATE_CONTEXT_HINTS]" in payload["additionalContext"]
    assert "- quadgate/contracts.py" in payload["additionalContext"]
    assert payload["injectSteps"] == [{"userMessage": payload["additionalContext"]}]

    blk_verdict = GateVerdict(
        action=VerdictAction.BLOCK,
        gate=GateType.INTAKE,
        violation_receipt=ViolationReceipt(
            rule_id="INV_AIRGAP_BLOCK",
            score=1.0,
            remediation="Keep confidential tokens local.",
        ),
    )
    code_b, payload_b, stderr_b = adapter.format_verdict(GateType.INTAKE, blk_verdict)
    assert (code_b, stderr_b) == (0, "")
    assert isinstance(payload_b, dict)
    assert payload_b["decision"] == "deny"
    assert payload_b["terminationBehavior"] == "terminate"
    assert "INV_AIRGAP_BLOCK" in payload_b["reason"]
    assert "Keep confidential tokens local." in payload_b["reason"]


def test_format_verdict_firewall_and_subagent_roundtrip(adapter: AntigravityAdapter) -> None:
    fw_allow = GateVerdict(action=VerdictAction.ALLOW, gate=GateType.FIREWALL)
    assert adapter.format_verdict(GateType.FIREWALL, fw_allow) == (0, {"decision": "allow"}, "")

    receipt = ViolationReceipt(
        rule_id="INV_01_SECRET_EXPOSURE",
        score=0.98,
        stage="deterministic_2a",
        remediation="Never write credentials to .env files.",
        bypass_instruction="Include [bypass-gates] in prompt or confirm human override.",
        violated_path=".env",
    )
    fw_block = GateVerdict(
        action=VerdictAction.BLOCK,
        gate=GateType.FIREWALL,
        violation_receipt=receipt,
    )
    code, payload, stderr = adapter.format_verdict(GateType.FIREWALL, fw_block)
    assert (code, stderr) == (0, "")
    assert isinstance(payload, dict)
    assert payload["decision"] == "deny"
    assert "INV_01_SECRET_EXPOSURE" in payload["reason"]
    assert "Never write credentials to .env files." in payload["reason"]
    assert receipt.bypass_instruction in payload["reason"]

    sub_modify = GateVerdict(
        action=VerdictAction.MODIFY,
        gate=GateType.SUBAGENT_GOVERNOR,
        updated_tool_args={"Prompt": "Add helper unit test"},
        updated_model="flash_lite",
        target_tier=ModelTier.FLOOR,
        reason="Gate3 ECC selected tier=floor model=flash_lite",
    )
    m_code, m_payload, m_stderr = adapter.format_verdict(GateType.SUBAGENT_GOVERNOR, sub_modify)
    assert (m_code, m_stderr) == (0, "")
    assert isinstance(m_payload, dict)
    assert m_payload["decision"] == "allow"
    assert m_payload["overwrite"] == {"Prompt": "Add helper unit test", "Model": "flash_lite"}
    assert m_payload["updatedInput"] == {"Prompt": "Add helper unit test", "Model": "flash_lite"}
    assert m_payload["updatedModel"] == "flash_lite"
    assert m_payload["reason"] == "Gate3 ECC selected tier=floor model=flash_lite"

    sub_promote = GateVerdict(
        action=VerdictAction.PROMOTE,
        gate=GateType.SUBAGENT_GOVERNOR,
        updated_model="pro",
        target_tier=ModelTier.FRONTIER,
        reason="High blast radius requires frontier tier.",
    )
    p_code, p_payload, p_stderr = adapter.format_verdict(GateType.SUBAGENT_GOVERNOR, sub_promote)
    assert (p_code, p_stderr) == (0, "")
    assert p_payload == {
        "decision": "deny",
        "action": "promote",
        "reason": "High blast radius requires frontier tier.",
        "updatedModel": "pro",
        "targetTier": "frontier",
    }


def test_format_verdict_stop_audit_roundtrip(adapter: AntigravityAdapter) -> None:
    allow_verdict = GateVerdict(action=VerdictAction.ALLOW, gate=GateType.STOP_AUDIT)
    assert adapter.format_verdict(GateType.STOP_AUDIT, allow_verdict) == (
        0,
        {"decision": "allow"},
        "",
    )

    aug_no_clauses = GateVerdict(
        action=VerdictAction.AUGMENT,
        gate=GateType.STOP_AUDIT,
        unaddressed_clauses=[],
    )
    assert adapter.format_verdict(GateType.STOP_AUDIT, aug_no_clauses) == (
        0,
        {"decision": "allow"},
        "",
    )

    aug_incomplete = GateVerdict(
        action=VerdictAction.AUGMENT,
        gate=GateType.STOP_AUDIT,
        reason="Directive completeness 0.65 < 0.85.",
        unaddressed_clauses=["Add integration test", "Update changelog"],
    )
    a_code, a_payload, a_stderr = adapter.format_verdict(GateType.STOP_AUDIT, aug_incomplete)
    assert (a_code, a_stderr) == (0, "")
    assert isinstance(a_payload, dict)
    assert a_payload["decision"] == "continue"
    assert a_payload["promote_next_turn_to_frontier"] is False
    assert a_payload["unaddressed_clauses"] == ["Add integration test", "Update changelog"]
    assert "Directive completeness 0.65 < 0.85." in a_payload["reason"]
    assert "Add integration test" in a_payload["reason"]

    promote_verdict = GateVerdict(
        action=VerdictAction.PROMOTE,
        gate=GateType.STOP_AUDIT,
        updated_model="pro",
        target_tier=ModelTier.FRONTIER,
        reason="Gate 4 oracle failed on mid (strike 0): SyntaxError.",
        unaddressed_clauses=["Fix syntax error in app.py"],
    )
    p_code, p_payload, p_stderr = adapter.format_verdict(GateType.STOP_AUDIT, promote_verdict)
    assert (p_code, p_stderr) == (0, "")
    assert p_payload == {
        "decision": "continue",
        "reason": (
            "Gate 4 oracle failed on mid (strike 0): SyntaxError. "
            "Unaddressed clauses: Fix syntax error in app.py"
        ),
        "unaddressed_clauses": ["Fix syntax error in app.py"],
        "promote_next_turn_to_frontier": True,
        "updatedModel": "pro",
        "targetTier": "frontier",
    }

    block_verdict = GateVerdict(
        action=VerdictAction.BLOCK,
        gate=GateType.STOP_AUDIT,
        violation_receipt=ViolationReceipt(
            rule_id="GATE4_ONE_STRIKE_CIRCUIT_BREAKER",
            score=1.0,
            remediation="One-strike circuit breaker halted promotion.",
        ),
    )
    b_code, b_payload, b_stderr = adapter.format_verdict(GateType.STOP_AUDIT, block_verdict)
    assert (b_code, b_stderr) == (0, "")
    assert isinstance(b_payload, dict)
    assert b_payload["decision"] == "continue"
    assert b_payload["promote_next_turn_to_frontier"] is False
    assert "GATE4_ONE_STRIKE_CIRCUIT_BREAKER" in b_payload["reason"]


def test_install_hooks_fresh_workspace_and_idempotency(
    adapter: AntigravityAdapter,
    tmp_path: Path,
) -> None:
    hooks_path = adapter.install_hooks(tmp_path)
    assert hooks_path == tmp_path / ".agents" / "hooks.json"
    assert hooks_path.is_file()

    first_text = hooks_path.read_text(encoding="utf-8")
    first_data = json.loads(first_text)
    assert first_data == {
        "enabled": True,
        "PreInvocation": [{"command": "qg-shim --harness antigravity --gate intake"}],
        "PreToolUse": [
            {
                "matcher": FIREWALL_MATCHER,
                "command": "qg-shim --harness antigravity --gate firewall",
            },
            {
                "matcher": SUBAGENT_MATCHER,
                "command": "qg-shim --harness antigravity --gate subagent_governor",
            },
        ],
        "Stop": [{"command": "qg-shim --harness antigravity --gate stop_audit"}],
    }

    second_path = adapter.install_hooks(tmp_path)
    assert second_path == hooks_path
    assert hooks_path.read_text(encoding="utf-8") == first_text


def test_install_hooks_preserves_existing_user_hooks_and_metadata(
    adapter: AntigravityAdapter,
    tmp_path: Path,
) -> None:
    hooks_dir = tmp_path / ".agents"
    hooks_dir.mkdir(parents=True)
    hooks_file = hooks_dir / "hooks.json"
    existing_config = {
        "$schema": "https://antigravity.dev/schemas/hooks.json",
        "description": "Custom team workspace hooks",
        "enabled": False,
        "hooks": {"customNamespace": {"keep": True}},
        "PreInvocation": [{"command": "python scripts/pre_invoke.py"}],
        "PreToolUse": [
            {"matcher": "^run_command$", "command": "python scripts/audit_cmd.py"},
            {
                "matcher": "^(write_to_file|delete_file)$",
                "command": "qg-shim --harness antigravity --gate firewall",
            },
        ],
        "Stop": [{"command": "python scripts/post_stop.py"}],
    }
    hooks_file.write_text(json.dumps(existing_config, indent=2), encoding="utf-8")

    result_path = adapter.install_hooks(tmp_path)
    assert result_path == hooks_file

    after_first = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert after_first["$schema"] == existing_config["$schema"]
    assert after_first["description"] == existing_config["description"]
    assert after_first["enabled"] is False
    assert after_first["hooks"] == {"customNamespace": {"keep": True}}

    assert after_first["PreInvocation"] == [
        {"command": "python scripts/pre_invoke.py"},
        {"command": "qg-shim --harness antigravity --gate intake"},
    ]
    assert after_first["PreToolUse"] == [
        {"matcher": "^run_command$", "command": "python scripts/audit_cmd.py"},
        {
            "matcher": FIREWALL_MATCHER,
            "command": "qg-shim --harness antigravity --gate firewall",
        },
        {
            "matcher": SUBAGENT_MATCHER,
            "command": "qg-shim --harness antigravity --gate subagent_governor",
        },
    ]
    assert after_first["Stop"] == [
        {"command": "python scripts/post_stop.py"},
        {"command": "qg-shim --harness antigravity --gate stop_audit"},
    ]

    snapshot = hooks_file.read_text(encoding="utf-8")
    adapter.install_hooks(tmp_path)
    assert hooks_file.read_text(encoding="utf-8") == snapshot


async def test_antigravity_adapter_gate_engine_roundtrip(
    adapter: AntigravityAdapter,
    tmp_path: Path,
) -> None:
    target = tmp_path / "helper.py"
    target.write_text("def greet() -> str:\n    return 'hi'\n", encoding="utf-8")

    engine = GateEngine(load_cartridge("software_engineering"))
    subagent_payload = {
        "hookEventName": "PreToolUse",
        "conversationId": "e2e-conv",
        "workspacePaths": [str(tmp_path)],
        "toolCall": {
            "id": "tc-sub-1",
            "name": "invoke_subagent",
            "args": {
                "Model": "pro",
                "Prompt": "Add a unit test and type hint helper in helper.py",
                "TargetFile": "helper.py",
            },
        },
    }
    inferred_gate = adapter.infer_gate(subagent_payload)
    assert inferred_gate == GateType.SUBAGENT_GOVERNOR

    event = adapter.parse_event(inferred_gate, subagent_payload)
    verdict = await engine.evaluate(event)
    exit_code, out_payload, stderr_text = adapter.format_verdict(inferred_gate, verdict)

    assert exit_code == 0
    assert stderr_text == ""
    assert isinstance(out_payload, dict)
    assert out_payload["decision"] == "allow"
    assert "Model" in out_payload["overwrite"]
    assert out_payload["updatedInput"] == out_payload["overwrite"]
