"""Harness-agnostic adapter interfaces and translators."""

from quadgate.adapters.antigravity import AntigravityAdapter
from quadgate.adapters.base import HarnessAdapter
from quadgate.adapters.claude_code import ClaudeCodeAdapter
from quadgate.adapters.codex_cli import CodexCliAdapter
from quadgate.adapters.mcp_bridge import McpBridgeAdapter

__all__ = [
    "AntigravityAdapter",
    "ClaudeCodeAdapter",
    "CodexCliAdapter",
    "HarnessAdapter",
    "McpBridgeAdapter",
]
