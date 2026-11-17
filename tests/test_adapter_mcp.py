"""Tests for McpBridgeAdapter: contracts, hooks, tools, JSON-RPC 2.0, and stdio loop."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from quadgate.adapters.mcp_bridge import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
    TOOLS,
    McpBridgeAdapter,
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


def test_harness_property_and_parse_event(tmp_path: Path) -> None:
    adapter = McpBridgeAdapter()
    assert adapter.harness == HarnessType.MCP

    event = adapter.parse_event(
        GateType.FIREWALL,
        {
            "session_id": "mcp-s1",
            "turn_index": 3,
            "workspace_root": str(tmp_path),
            "tool_name": "Edit",
            "tool_args": {"file_path": "src/app.py", "new_string": "x = 1"},
            "prompt_text": "Update app.py",
            "touched_files": ["src/app.py"],
            "diff_text": "+ x = 1",
            "current_tier": "mid",
            "strike_count": 1,
            "continuation_active": True,
            "bypass_gates": True,
            "assigned_file_cage": ["src/app.py"],
            "metadata": {"source": "cursor"},
        },
    )
    assert event.gate == GateType.FIREWALL
    assert event.harness == HarnessType.MCP
    assert event.session_id == "mcp-s1"
    assert event.turn_index == 3
    assert event.workspace_root == str(tmp_path)
    assert event.tool_name == "Edit"
    assert event.tool_args == {"file_path": "src/app.py", "new_string": "x = 1"}
    assert event.prompt_text == "Update app.py"
    assert event.touched_files == ["src/app.py"]
    assert event.diff_text == "+ x = 1"
    assert event.current_tier == ModelTier.MID
    assert event.strike_count == 1
    assert event.continuation_active is True
    assert event.bypass_gates is True
    assert event.assigned_file_cage == ["src/app.py"]
    assert event.metadata == {"source": "cursor"}

    # Defaults and wrapped MCP call payload
    minimal = adapter.parse_event(
        GateType.INTAKE,
        {
            "name": "quadgate_context_sieve",
            "arguments": {"prompt_text": "Inspect auth.py", "workspace_root": str(tmp_path)},
        },
    )
    assert minimal.harness == HarnessType.MCP
    assert minimal.session_id == "mcp-session"
    assert minimal.turn_index == 1
    assert minimal.prompt_text == "Inspect auth.py"
    adapter.close()


def test_format_verdict_exit_codes_and_payloads() -> None:
    adapter = McpBridgeAdapter()

    allow_verdict = GateVerdict(
        action=VerdictAction.ALLOW,
        gate=GateType.FIREWALL,
        latency_ms=1.25,
    )
    code, payload, stderr = adapter.format_verdict(GateType.FIREWALL, allow_verdict)
    assert code == 0
    assert stderr == ""
    assert isinstance(payload, dict)
    assert payload["action"] == "allow"
    assert payload["gate"] == "firewall"
    assert GateVerdict.model_validate(payload).action == VerdictAction.ALLOW

    augment_verdict = GateVerdict(
        action=VerdictAction.AUGMENT,
        gate=GateType.INTAKE,
        tail_hints=["src/auth.py"],
        latency_ms=2.0,
    )
    code, payload, stderr = adapter.format_verdict(GateType.INTAKE, augment_verdict)
    assert code == 0
    assert stderr == ""
    assert isinstance(payload, dict)
    assert payload["tail_hints"] == ["src/auth.py"]

    modify_verdict = GateVerdict(
        action=VerdictAction.MODIFY,
        gate=GateType.SUBAGENT_GOVERNOR,
        updated_model="flash_lite",
        target_tier=ModelTier.FLOOR,
        updated_tool_args={"model": "flash_lite"},
    )
    code, payload, stderr = adapter.format_verdict(GateType.SUBAGENT_GOVERNOR, modify_verdict)
    assert code == 0
    assert stderr == ""
    assert isinstance(payload, dict)
    assert payload["updated_model"] == "flash_lite"

    block_verdict = GateVerdict(
        action=VerdictAction.BLOCK,
        gate=GateType.FIREWALL,
        violation_receipt=ViolationReceipt(
            rule_id="INV_01_SECRET_EXPOSURE",
            score=1.0,
            stage="deterministic_2a",
            remediation="Do not read .env files.",
            violated_path=".env",
        ),
        reason="Blocked .env access",
    )
    code, payload, stderr = adapter.format_verdict(GateType.FIREWALL, block_verdict)
    assert code == 2
    assert "INV_01_SECRET_EXPOSURE" in stderr
    assert "Do not read .env files." in stderr
    assert isinstance(payload, dict)
    assert payload["action"] == "block"

    promote_verdict = GateVerdict(
        action=VerdictAction.PROMOTE,
        gate=GateType.STOP_AUDIT,
        updated_model="flash",
        target_tier=ModelTier.MID,
        reason="Syntax error on floor tier",
    )
    code, payload, stderr = adapter.format_verdict(GateType.STOP_AUDIT, promote_verdict)
    assert code == 2
    assert "Syntax error on floor tier" in stderr
    assert isinstance(payload, dict)
    assert payload["action"] == "promote"
    adapter.close()


def test_install_hooks_merges_existing_cursor_mcp_idempotently(tmp_path: Path) -> None:
    adapter = McpBridgeAdapter()
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    mcp_file = cursor_dir / "mcp.json"
    existing_cfg: dict[str, Any] = {
        "customTopLevel": {"enabled": True},
        "mcpServers": {
            "postgres": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
            "github": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "${env:GITHUB_TOKEN}"}},
        },
    }
    mcp_file.write_text(json.dumps(existing_cfg, indent=2), encoding="utf-8")

    returned_path = adapter.install_hooks(tmp_path)
    assert returned_path == mcp_file
    merged = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert merged["customTopLevel"] == {"enabled": True}
    assert merged["mcpServers"]["postgres"] == existing_cfg["mcpServers"]["postgres"]
    assert merged["mcpServers"]["github"] == existing_cfg["mcpServers"]["github"]
    assert merged["mcpServers"]["quadgate"] == {"command": "quadgate", "args": ["mcp"]}

    # Idempotent on repeated calls
    before_text = mcp_file.read_text(encoding="utf-8")
    returned_again = adapter.install_hooks(tmp_path)
    assert returned_again == mcp_file
    assert mcp_file.read_text(encoding="utf-8") == before_text

    # Fresh workspace without .cursor directory
    fresh_ws = tmp_path / "fresh_ws"
    fresh_ws.mkdir()
    fresh_path = adapter.install_hooks(fresh_ws)
    assert fresh_path == fresh_ws / ".cursor" / "mcp.json"
    fresh_cfg = json.loads(fresh_path.read_text(encoding="utf-8"))
    assert fresh_cfg == {"mcpServers": {"quadgate": {"command": "quadgate", "args": ["mcp"]}}}
    adapter.close()


async def test_rpc_lifecycle_initialize_ping_tools_list_and_errors() -> None:
    adapter = McpBridgeAdapter()

    init_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
        }
    )
    assert init_resp == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        },
    }

    # Notification produces no response
    notif_resp = await adapter.handle_rpc_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notif_resp is None

    # Notification without id on any method produces no response
    ping_notif = await adapter.handle_rpc_request({"jsonrpc": "2.0", "method": "ping"})
    assert ping_notif is None

    # Request with id=0 must still respond (falsy id check safety)
    ping_resp = await adapter.handle_rpc_request({"jsonrpc": "2.0", "id": 0, "method": "ping"})
    assert ping_resp == {"jsonrpc": "2.0", "id": 0, "result": {}}

    # tools/list exposes all 3 required tools and schemas
    list_resp = await adapter.handle_rpc_request(
        {"jsonrpc": "2.0", "id": "tools-1", "method": "tools/list"}
    )
    assert list_resp is not None
    tools = list_resp["result"]["tools"]
    assert len(tools) == len(TOOLS) == 3
    names = [t["name"] for t in tools]
    assert names == [
        "quadgate_context_sieve",
        "quadgate_verify_mutation",
        "quadgate_audit_completion",
    ]
    for tool in tools:
        assert isinstance(tool["description"], str) and tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]

    # Unknown method -> -32601
    unknown_resp = await adapter.handle_rpc_request(
        {"jsonrpc": "2.0", "id": 9, "method": "resources/list"}
    )
    assert unknown_resp is not None
    assert unknown_resp["id"] == 9
    assert unknown_resp["error"]["code"] == -32601

    # Unknown tool -> -32602
    bad_tool_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
    )
    assert bad_tool_resp is not None
    assert bad_tool_resp["error"]["code"] == -32602

    # Malformed params (non-dict arguments, invalid turn_index) -> -32602
    bad_args_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "quadgate_context_sieve", "arguments": ["bad"]},
        }
    )
    assert bad_args_resp is not None
    assert bad_args_resp["error"]["code"] == -32602

    bad_turn_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {"prompt_text": "hello", "turn_index": 0},
            },
        }
    )
    assert bad_turn_resp is not None
    assert bad_turn_resp["error"]["code"] == -32602
    adapter.close()


async def test_tool_context_sieve_turn1_hints_and_turn2_cache_preservation(tmp_path: Path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "jwt_validator.py").write_text(
        '"""Validate JWT authentication tokens and expiration leeway."""\n'
        "def verify_jwt_token(token: str, leeway_sec: int = 30) -> bool:\n"
        "    return bool(token) and leeway_sec >= 0\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "stripe_webhook.py").write_text(
        '"""Process Stripe billing webhook invoices."""\n'
        "def handle_stripe_invoice(payload: dict) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    adapter = McpBridgeAdapter()

    # Turn 1 injects matching tail hints
    turn1_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {
                    "prompt_text": (
                        "Update verify_jwt_token in src/auth/jwt_validator.py "
                        "for expiration leeway"
                    ),
                    "workspace_root": str(tmp_path),
                    "turn_index": 1,
                    "session_id": "mcp-cache-sess",
                },
            },
        }
    )
    assert turn1_resp is not None
    assert turn1_resp["result"]["isError"] is False
    data1 = json.loads(turn1_resp["result"]["content"][0]["text"])
    assert data1["action"] == "augment"
    assert "src/auth/jwt_validator.py" in data1["tail_hints"]
    assert data1["cloud_egress_allowed"] is True
    assert data1["latency_ms"] >= 0.0

    # Turn 2 preserves KV prefix cache (zero tail_hints, zero reason, ALLOW)
    turn2_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {
                    "prompt_text": "Now update stripe_webhook.py as well",
                    "workspace_root": str(tmp_path),
                    "turn_index": 2,
                    "session_id": "mcp-cache-sess",
                },
            },
        }
    )
    assert turn2_resp is not None
    assert turn2_resp["result"]["isError"] is False
    data2 = json.loads(turn2_resp["result"]["content"][0]["text"])
    assert data2["action"] == "allow"
    assert data2["tail_hints"] == []
    assert data2["reason"] is None

    # Air-gap test on confidential marker
    airgap_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {
                    "prompt_text": "[private] Inspect jwt_validator.py",
                    "workspace_root": str(tmp_path),
                    "turn_index": 1,
                    "session_id": "mcp-airgap-sess",
                },
            },
        }
    )
    assert airgap_resp is not None
    data3 = json.loads(airgap_resp["result"]["content"][0]["text"])
    assert data3["cloud_egress_allowed"] is False

    # Bare injected GateEngine without fts_store/indexer still indexes workspace cleanly
    bare_engine = GateEngine(cartridge=load_cartridge("software_engineering"))
    bare_adapter = McpBridgeAdapter(gate_engine=bare_engine)
    bare_resp = await bare_adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {
                    "prompt_text": "Fix verify_jwt_token in src/auth/jwt_validator.py",
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert bare_resp is not None
    bare_data = json.loads(bare_resp["result"]["content"][0]["text"])
    assert "src/auth/jwt_validator.py" in bare_data["tail_hints"]

    adapter.close()
    bare_adapter.close()


async def test_tool_verify_mutation_stage2a_gate2_and_gate3(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    app_file = src_dir / "app.py"
    app_file.write_text("def greet() -> str:\n    return 'hello'\n", encoding="utf-8")

    adapter = McpBridgeAdapter()

    # 1. Safe edit allowed
    safe_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Edit",
                    "tool_args": {
                        "file_path": "src/app.py",
                        "new_string": "def greet() -> str:\n    return 'world'\n",
                    },
                    "touched_files": ["src/app.py"],
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert safe_resp is not None
    assert safe_resp["result"]["isError"] is False
    safe_data = json.loads(safe_resp["result"]["content"][0]["text"])
    assert safe_data["action"] == "allow"
    assert safe_data["allowed"] is True
    assert safe_data["violation_receipt"] is None

    # 2. Stage-2A .env block (cannot be bypassed even with bypass_gates=True)
    env_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Write",
                    "tool_args": {"file_path": ".env", "content": "DB_PASS=1234"},
                    "workspace_root": str(tmp_path),
                    "bypass_gates": True,
                    "session_id": "sec-sess",
                },
            },
        }
    )
    assert env_resp is not None
    env_data = json.loads(env_resp["result"]["content"][0]["text"])
    assert env_data["action"] == "block"
    assert env_data["allowed"] is False
    assert env_data["violation_receipt"]["rule_id"] == "INV_01_SECRET_EXPOSURE"
    assert env_data["violation_receipt"]["stage"] == "deterministic_2a"
    assert env_data["violation_receipt"]["strike_count"] == 1
    assert env_data["violation_receipt"]["escalate_to_human"] is False

    # Second strike on same rule escalates to human
    env_resp2 = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Bash",
                    "tool_args": {"command": "cat .env"},
                    "workspace_root": str(tmp_path),
                    "session_id": "sec-sess",
                },
            },
        }
    )
    assert env_resp2 is not None
    env_data2 = json.loads(env_resp2["result"]["content"][0]["text"])
    assert env_data2["allowed"] is False
    assert env_data2["violation_receipt"]["strike_count"] == 2
    assert env_data2["violation_receipt"]["escalate_to_human"] is True

    # 3. Stage-2A path escape blocked
    escape_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Write",
                    "tool_args": {"file_path": "../../outside.py", "content": "x = 1"},
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert escape_resp is not None
    escape_data = json.loads(escape_resp["result"]["content"][0]["text"])
    assert escape_data["action"] == "block"
    assert escape_data["allowed"] is False
    assert escape_data["violation_receipt"]["rule_id"] == "INV_02_CAGE_CONTAINMENT"

    # 4. Gate 2 assigned_file_cage enforcement
    cage_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Edit",
                    "tool_args": {"file_path": "src/other.py", "new_string": "y = 2"},
                    "assigned_file_cage": ["src/app.py"],
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert cage_resp is not None
    cage_data = json.loads(cage_resp["result"]["content"][0]["text"])
    assert cage_data["action"] == "block"
    assert cage_data["allowed"] is False
    assert cage_data["violation_receipt"]["rule_id"] == "INV_02_CAGE_CONTAINMENT"

    # 5. Gate 3 subagent tier recommendation (subagent_model / current_tier / invoke_subagent)
    leaf_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "invoke_subagent",
                    "tool_args": {"brief": "Add docstring and unit test helper in src/app.py"},
                    "touched_files": ["src/app.py"],
                    "workspace_root": str(tmp_path),
                    "subagent_model": "pro",
                },
            },
        }
    )
    assert leaf_resp is not None
    leaf_data = json.loads(leaf_resp["result"]["content"][0]["text"])
    assert leaf_data["action"] == "modify"
    assert leaf_data["allowed"] is True
    assert leaf_data["target_tier"] == "floor"
    assert leaf_data["updated_model"] == "flash_lite"
    assert leaf_data["updated_tool_args"]["model"] == "flash_lite"

    hard_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Task",
                    "prompt_text": (
                        "Architect a distributed consensus scheduler and zero-downtime kernel "
                        "concurrency migration across multi-region services"
                    ),
                    "workspace_root": str(tmp_path),
                    "current_tier": "floor",
                },
            },
        }
    )
    assert hard_resp is not None
    hard_data = json.loads(hard_resp["result"]["content"][0]["text"])
    assert hard_data["action"] == "modify"
    assert hard_data["allowed"] is True
    assert hard_data["target_tier"] == "frontier"
    assert hard_data["updated_model"] == "pro"
    adapter.close()


async def test_tool_audit_completion_oracle_and_clauses(tmp_path: Path) -> None:
    ok_py = tmp_path / "ok.py"
    ok_py.write_text(
        "def verify_token(token: str) -> bool:\n    return bool(token)\n",
        encoding="utf-8",
    )
    broken_py = tmp_path / "broken.py"
    broken_py.write_text("def broken(:\n    pass\n", encoding="utf-8")

    adapter = McpBridgeAdapter()

    # 1. Clean completion passes
    clean_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "quadgate_audit_completion",
                "arguments": {
                    "prompt_text": "Implement verify_token in ok.py",
                    "touched_files": ["ok.py"],
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert clean_resp is not None
    clean_data = json.loads(clean_resp["result"]["content"][0]["text"])
    assert clean_data["action"] == "allow"
    assert clean_data["passed"] is True
    assert clean_data["unaddressed_clauses"] == []

    # 2. Syntax oracle failure on floor tier (strike 0) -> PROMOTE
    promote_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "quadgate_audit_completion",
                "arguments": {
                    "touched_files": ["broken.py"],
                    "workspace_root": str(tmp_path),
                    "current_tier": "floor",
                    "strike_count": 0,
                },
            },
        }
    )
    assert promote_resp is not None
    promote_data = json.loads(promote_resp["result"]["content"][0]["text"])
    assert promote_data["action"] == "promote"
    assert promote_data["passed"] is False
    assert promote_data["target_tier"] == "mid"
    assert promote_data["updated_model"] == "flash"

    # 3. Syntax oracle failure on strike >= 1 -> BLOCK with violation_receipt
    block_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "quadgate_audit_completion",
                "arguments": {
                    "touched_files": ["broken.py"],
                    "workspace_root": str(tmp_path),
                    "current_tier": "mid",
                    "strike_count": 1,
                },
            },
        }
    )
    assert block_resp is not None
    block_data = json.loads(block_resp["result"]["content"][0]["text"])
    assert block_data["action"] == "block"
    assert block_data["passed"] is False
    assert block_data["violation_receipt"]["rule_id"] == "GATE4_TRUTH_ORACLE_FAILURE"
    assert block_data["violation_receipt"]["escalate_to_human"] is True

    # 4. Incomplete directive clauses -> AUGMENT with unaddressed_clauses
    prompt_3 = (
        "1. Update verify_token in auth.py\n"
        "2. Add enforce_rate_limit in gateway.py\n"
        "3. Write changelog notes in CHANGELOG.md"
    )
    partial_diff = (
        "--- a/auth.py\n+++ b/auth.py\n"
        "def verify_token(token: str) -> bool:\n    return bool(token)\n"
        "--- a/gateway.py\n+++ b/gateway.py\n"
        "def enforce_rate_limit(ip: str) -> bool:\n    return True\n"
    )
    partial_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "quadgate_audit_completion",
                "arguments": {
                    "prompt_text": prompt_3,
                    "diff_text": partial_diff,
                    "workspace_root": str(tmp_path),
                    "continuation_active": False,
                },
            },
        }
    )
    assert partial_resp is not None
    partial_data = json.loads(partial_resp["result"]["content"][0]["text"])
    assert partial_data["action"] == "augment"
    assert partial_data["passed"] is False
    assert len(partial_data["unaddressed_clauses"]) == 1
    assert "CHANGELOG.md" in partial_data["unaddressed_clauses"][0]
    adapter.close()


async def test_serve_stdio_end_to_end_and_parse_errors(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    adapter = McpBridgeAdapter()

    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "{invalid json line",
        "",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "quadgate_verify_mutation",
                    "arguments": {
                        "tool_name": "Write",
                        "tool_args": {"file_path": ".env", "content": "SECRET=1"},
                        "workspace_root": str(tmp_path),
                    },
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "unknown/method"}),
    ]
    stdin = io.StringIO("\n".join(requests) + "\n")
    stdout = io.StringIO()

    rc = await adapter.serve_stdio(stdin=stdin, stdout=stdout)
    assert rc == 0

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    # 6 responses: initialize(1), parse error(-32700), ping(2), tools/list(3), verify(4), unknown(5)
    # notifications/initialized and blank line produce no output
    assert len(lines) == 6
    responses = [json.loads(ln) for ln in lines]

    assert responses[0]["id"] == 1
    assert responses[0]["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == -32700

    assert responses[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}

    assert responses[3]["id"] == 3
    assert len(responses[3]["result"]["tools"]) == 3

    assert responses[4]["id"] == 4
    verify_res = json.loads(responses[4]["result"]["content"][0]["text"])
    assert verify_res["action"] == "block"
    assert verify_res["allowed"] is False

    assert responses[5]["id"] == 5
    assert responses[5]["error"]["code"] == -32601
    adapter.close()


def test_run_stdio_sync_wrapper() -> None:
    adapter = McpBridgeAdapter()
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    rc = adapter.run_stdio(stdin=stdin, stdout=stdout)
    assert rc == 0
    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    assert json.loads(out_lines[0]) == {"jsonrpc": "2.0", "id": 7, "result": {}}
    adapter.close()


async def test_verify_mutation_secret_diff_destructive_cmd_and_subagent_model_trigger(
    tmp_path: Path,
) -> None:
    adapter = McpBridgeAdapter()

    # Hardcoded secret in diff_text caught by Stage-2A
    secret_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Edit",
                    "diff_text": "+ AWS_ACCESS_KEY = 'AKIA1234567890ABCDEF'\n",
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert secret_resp is not None
    secret_data = json.loads(secret_resp["result"]["content"][0]["text"])
    assert secret_data["action"] == "block"
    assert secret_data["allowed"] is False
    assert secret_data["violation_receipt"]["rule_id"] == "INV_01_SECRET_EXPOSURE"

    # Destructive command caught by Stage-2A
    rm_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "Bash",
                    "tool_args": {"command": "rm -rf /"},
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert rm_resp is not None
    rm_data = json.loads(rm_resp["result"]["content"][0]["text"])
    assert rm_data["action"] == "block"
    assert rm_data["allowed"] is False
    assert rm_data["violation_receipt"]["rule_id"] == "STAGE2A_DESTRUCTIVE_COMMAND"

    # subagent_model alone triggers Gate 3 even if tool_name is generic
    sub_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "quadgate_verify_mutation",
                "arguments": {
                    "tool_name": "custom_worker",
                    "prompt_text": "Update README typo and docstring comment",
                    "subagent_model": "pro",
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert sub_resp is not None
    sub_data = json.loads(sub_resp["result"]["content"][0]["text"])
    assert sub_data["action"] == "modify"
    assert sub_data["allowed"] is True
    assert sub_data["target_tier"] == "floor"
    assert sub_data["updated_model"] == "flash_lite"

    # Explicit candidates in context_sieve + malformed params validation
    cand_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "quadgate_context_sieve",
                "arguments": {
                    "prompt_text": "Fix retry backoff in worker_pool.py",
                    "workspace_root": str(tmp_path),
                    "candidates": [
                        {
                            "rel_path": "src/worker_pool.py",
                            "semantic_gist": "Retry backoff worker pool",
                            "symbols": ["retry_with_backoff"],
                        }
                    ],
                },
            },
        }
    )
    assert cand_resp is not None
    cand_data = json.loads(cand_resp["result"]["content"][0]["text"])
    assert "src/worker_pool.py" in cand_data["tail_hints"]

    bad_tier_resp = await adapter.handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "quadgate_audit_completion",
                "arguments": {"current_tier": "nonexistent"},
            },
        }
    )
    assert bad_tier_resp is not None
    assert bad_tier_resp["error"]["code"] == -32602
    adapter.close()

