"""Claude Code `.claude/settings.json` and hook payload adapter."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from quadgate.adapters.base import HarnessAdapter
from quadgate.contracts import (
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    ModelTier,
    VerdictAction,
)

_QUADGATE_HOOK_SPECS: tuple[tuple[str, str | None, str], ...] = (
    ("UserPromptSubmit", None, "qg-shim --harness claude_code --gate intake"),
    ("PreToolUse", "Write|Edit|MultiEdit|Bash", "qg-shim --harness claude_code --gate firewall"),
    ("PreToolUse", "Task", "qg-shim --harness claude_code --gate subagent_governor"),
    ("Stop", None, "qg-shim --harness claude_code --gate stop_audit"),
    ("SubagentStop", None, "qg-shim --harness claude_code --gate stop_audit"),
)

_TIER_EXACT_ALIASES: dict[str, ModelTier] = {
    "floor": ModelTier.FLOOR,
    "haiku": ModelTier.FLOOR,
    "flash_lite": ModelTier.FLOOR,
    "mid": ModelTier.MID,
    "sonnet": ModelTier.MID,
    "flash": ModelTier.MID,
    "frontier": ModelTier.FRONTIER,
    "opus": ModelTier.FRONTIER,
    "pro": ModelTier.FRONTIER,
}

_PATH_FIELD_KEYS = ("file_path", "path", "target_file", "paths")
_EDIT_TEXT_KEYS = ("new_string", "content", "new_text", "replacement")


def _first_non_empty_str(*values: Any) -> str | None:
    for val in values:
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _first_str(*values: Any) -> str | None:
    for val in values:
        if isinstance(val, str):
            return val
    return None


def _parse_turn_index(raw_value: Any) -> int:
    if isinstance(raw_value, bool) or raw_value is None:
        return 1
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return 1


def _parse_strike_count(raw_value: Any) -> int:
    if isinstance(raw_value, bool) or raw_value is None:
        return 0
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 0


def _infer_model_tier(raw_model: Any) -> ModelTier | None:
    if isinstance(raw_model, ModelTier):
        return raw_model
    if not isinstance(raw_model, str):
        return None
    norm = raw_model.strip().lower().replace("-", "_")
    if not norm or norm == "inherit":
        return None
    if norm in _TIER_EXACT_ALIASES:
        return _TIER_EXACT_ALIASES[norm]

    tokens = [tok for tok in norm.replace("/", "_").replace(".", "_").split("_") if tok]
    if "flash_lite" in norm or "haiku" in tokens or "floor" in tokens:
        return ModelTier.FLOOR
    if "flash" in tokens or "sonnet" in tokens or "mid" in tokens:
        return ModelTier.MID
    if "opus" in tokens or "pro" in tokens or "frontier" in tokens:
        return ModelTier.FRONTIER
    return None


def _collect_touched_files(raw_payload: dict[str, Any], tool_args: dict[str, Any]) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    def _add_value(value: Any) -> None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                collected.append(stripped)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    stripped = item.strip()
                    if stripped and stripped not in seen:
                        seen.add(stripped)
                        collected.append(stripped)

    _add_value(raw_payload.get("touched_files"))
    for key in _PATH_FIELD_KEYS:
        _add_value(tool_args.get(key))

    edits = tool_args.get("edits")
    if isinstance(edits, list):
        for item in edits:
            if isinstance(item, dict):
                for key in _PATH_FIELD_KEYS:
                    _add_value(item.get(key))

    for key in _PATH_FIELD_KEYS:
        _add_value(raw_payload.get(key))

    return collected


def _extract_edits_text(raw_edits: Any) -> str | None:
    if isinstance(raw_edits, str) and raw_edits:
        return raw_edits
    if not isinstance(raw_edits, list):
        return None
    chunks: list[str] = []
    for item in raw_edits:
        if isinstance(item, str) and item:
            chunks.append(item)
        elif isinstance(item, dict):
            extracted = _first_str(*(item.get(k) for k in _EDIT_TEXT_KEYS))
            if extracted is not None:
                chunks.append(extracted)
    if chunks:
        return "\n".join(chunks)
    return None


def _extract_diff_text(
    gate: GateType,
    raw_payload: dict[str, Any],
    tool_args: dict[str, Any],
) -> str | None:
    explicit = _first_str(raw_payload.get("diff_text"), tool_args.get("diff_text"))
    if explicit is not None:
        return explicit

    if gate == GateType.STOP_AUDIT:
        return _first_str(raw_payload.get("diff"), raw_payload.get("patch"))

    for key in _EDIT_TEXT_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str):
            return val

    edits_text = _extract_edits_text(tool_args.get("edits"))
    if edits_text is not None:
        return edits_text

    for key in ("command", "cmd"):
        val = tool_args.get(key)
        if isinstance(val, str):
            return val

    for key in _EDIT_TEXT_KEYS:
        val = raw_payload.get(key)
        if isinstance(val, str):
            return val

    top_edits = _extract_edits_text(raw_payload.get("edits"))
    if top_edits is not None:
        return top_edits

    for key in ("command", "cmd"):
        val = raw_payload.get(key)
        if isinstance(val, str):
            return val

    return None


def _format_block_message(verdict: GateVerdict) -> str:
    receipt = verdict.violation_receipt
    if receipt is not None:
        msg = f"[QUADGATE BLOCK] {receipt.rule_id}: {receipt.remediation}"
        if verdict.reason and verdict.reason.strip():
            msg += f" (Reason: {verdict.reason.strip()})"
        if receipt.violated_path and receipt.violated_path not in msg:
            msg += f" [path: {receipt.violated_path}]"
        if receipt.escalate_to_human:
            msg += f" [ESCALATE TO HUMAN: strike {receipt.strike_count}]"
        if receipt.bypass_instruction and receipt.bypass_instruction.strip():
            msg += f" — {receipt.bypass_instruction.strip()}"
        return msg

    reason = verdict.reason.strip() if verdict.reason and verdict.reason.strip() else None
    return f"[QUADGATE BLOCK] {reason or 'Operation blocked by QuadGate policy.'}"


def _format_promote_message(verdict: GateVerdict) -> str:
    tier_str = verdict.target_tier.value if verdict.target_tier else "frontier"
    model_str = verdict.updated_model or "pro"
    reason = (
        verdict.reason.strip()
        if verdict.reason and verdict.reason.strip()
        else "Promote task to higher model tier."
    )
    return f"[QUADGATE_PROMOTE] tier={tier_str} model={model_str}: {reason}"


def _format_augment_text(verdict: GateVerdict) -> str:
    parts: list[str] = []
    if verdict.reason and verdict.reason.strip():
        parts.append(verdict.reason.strip())

    hint_lines = [
        f"- {hint.strip()}"
        for hint in verdict.tail_hints
        if isinstance(hint, str) and hint.strip()
    ]
    if hint_lines:
        parts.append("[QUADGATE_CONTEXT_HINTS]\n" + "\n".join(hint_lines))

    clause_lines = [
        f"- {clause.strip()}"
        for clause in verdict.unaddressed_clauses
        if isinstance(clause, str) and clause.strip()
    ]
    if clause_lines:
        parts.append("[QUADGATE_UNADDRESSED]\n" + "\n".join(clause_lines))

    return "\n".join(parts)


def _format_stop_block_reason(verdict: GateVerdict) -> str:
    parts: list[str] = []

    if verdict.action == VerdictAction.PROMOTE:
        parts.append(_format_promote_message(verdict))
    elif verdict.violation_receipt is not None:
        receipt = verdict.violation_receipt
        line = f"[{receipt.rule_id}] {receipt.remediation}"
        if receipt.escalate_to_human and "escalat" not in line.lower():
            line += f" (human escalation required, strike {receipt.strike_count})"
        parts.append(line)

    if verdict.reason and verdict.reason.strip():
        reason_clean = verdict.reason.strip()
        if not any(reason_clean in existing for existing in parts):
            parts.append(reason_clean)

    clauses = [
        clause.strip()
        for clause in verdict.unaddressed_clauses
        if isinstance(clause, str) and clause.strip()
    ]
    if clauses:
        parts.append("Unaddressed clauses: " + "; ".join(clauses))

    if not parts:
        return "QuadGate Stop audit blocked completion."
    return " | ".join(parts)


def _has_hook_entry(
    entries: list[Any],
    matcher: str | None,
    command: str,
) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if matcher is not None and entry.get("matcher") != matcher:
            continue
        if matcher is None and entry.get("matcher") not in (None, ""):
            continue
        nested = entry.get("hooks")
        if not isinstance(nested, list):
            continue
        for hook in nested:
            if isinstance(hook, dict) and str(hook.get("command", "")).strip() == command:
                return True
    return False


class ClaudeCodeAdapter(HarnessAdapter):
    """Adapter for Claude Code hook payloads (`UserPromptSubmit`, `PreToolUse`, `Stop`)."""

    @property
    def harness(self) -> HarnessType:
        """Return the canonical Claude Code harness identifier."""
        return HarnessType.CLAUDE_CODE

    def infer_gate(self, raw_payload: dict[str, Any]) -> GateType:
        """Infer the canonical QuadGate lifecycle gate from a Claude Code hook payload."""
        raw_event_name = raw_payload.get("hook_event_name")
        if raw_event_name is None:
            raw_event_name = raw_payload.get("hookEventName")
        event_name = raw_event_name.strip() if isinstance(raw_event_name, str) else ""

        if event_name == "UserPromptSubmit":
            return GateType.INTAKE
        if event_name == "PreToolUse":
            tool_name = _first_non_empty_str(
                raw_payload.get("tool_name"),
                raw_payload.get("toolName"),
            )
            if tool_name == "Task":
                return GateType.SUBAGENT_GOVERNOR
            return GateType.FIREWALL
        if event_name in {"Stop", "SubagentStop"}:
            return GateType.STOP_AUDIT
        if event_name:
            raise ValueError(f"Unsupported Claude Code hook_event_name: {event_name!r}")

        raw_gate = raw_payload.get("gate")
        if isinstance(raw_gate, str) and raw_gate.strip():
            return GateType(raw_gate.strip())

        tool_name = _first_non_empty_str(raw_payload.get("tool_name"), raw_payload.get("toolName"))
        if tool_name == "Task":
            return GateType.SUBAGENT_GOVERNOR
        if (
            tool_name is not None
            or isinstance(raw_payload.get("tool_input"), dict)
            or isinstance(raw_payload.get("tool_args"), dict)
        ):
            return GateType.FIREWALL
        if "stop_hook_active" in raw_payload or "continuation_active" in raw_payload:
            return GateType.STOP_AUDIT
        if "prompt" in raw_payload or "prompt_text" in raw_payload:
            return GateType.INTAKE
        raise ValueError("Cannot infer QuadGate gate from empty Claude Code payload.")

    def parse_event(self, gate: GateType, raw_payload: dict[str, Any]) -> GateEvent:
        """Normalize Claude Code hook JSON into a canonical `GateEvent`."""
        resolved_gate = GateType(gate)
        session_id = (
            _first_non_empty_str(raw_payload.get("session_id"), raw_payload.get("sessionId"))
            or "claude-session"
        )
        turn_index = _parse_turn_index(
            raw_payload.get("turn_index")
            if raw_payload.get("turn_index") is not None
            else raw_payload.get("turnIndex")
        )
        workspace_root = (
            _first_non_empty_str(
                raw_payload.get("cwd"),
                raw_payload.get("workspace_root"),
                raw_payload.get("workspaceRoot"),
            )
            or "."
        )

        raw_tool_name = _first_non_empty_str(
            raw_payload.get("tool_name"),
            raw_payload.get("toolName"),
        )
        tool_name = (
            raw_tool_name
            if resolved_gate in {GateType.FIREWALL, GateType.SUBAGENT_GOVERNOR} or raw_tool_name
            else None
        )

        tool_args: dict[str, Any] = {}
        if isinstance(raw_payload.get("tool_input"), dict):
            tool_args = dict(raw_payload["tool_input"])
        elif isinstance(raw_payload.get("tool_args"), dict):
            tool_args = dict(raw_payload["tool_args"])

        if resolved_gate == GateType.INTAKE:
            prompt_text = _first_non_empty_str(
                raw_payload.get("prompt"),
                raw_payload.get("prompt_text"),
            )
            if prompt_text is None:
                prompt_text = _first_str(raw_payload.get("prompt"), raw_payload.get("prompt_text"))
        elif resolved_gate == GateType.STOP_AUDIT:
            prompt_text = _first_non_empty_str(
                raw_payload.get("prompt_text"),
                raw_payload.get("prompt"),
            )
            if prompt_text is None:
                prompt_text = _first_str(raw_payload.get("prompt_text"), raw_payload.get("prompt"))
        else:
            prompt_text = _first_non_empty_str(
                raw_payload.get("prompt"),
                raw_payload.get("prompt_text"),
            )
            if prompt_text is None:
                prompt_text = _first_str(raw_payload.get("prompt"), raw_payload.get("prompt_text"))
            if prompt_text is None and tool_name == "Task":
                prompt_text = _first_non_empty_str(
                    tool_args.get("prompt"),
                    tool_args.get("brief"),
                    tool_args.get("description"),
                    tool_args.get("task"),
                )

        raw_cage = raw_payload.get("assigned_file_cage")
        assigned_file_cage = (
            [item for item in raw_cage if isinstance(item, str) and item.strip()]
            if isinstance(raw_cage, list)
            else None
        )

        metadata: dict[str, Any] = {}
        if isinstance(raw_payload.get("metadata"), dict):
            metadata.update(raw_payload["metadata"])

        hook_event_name = _first_non_empty_str(
            raw_payload.get("hook_event_name"),
            raw_payload.get("hookEventName"),
        )
        if hook_event_name is not None:
            metadata.setdefault("hook_event_name", hook_event_name)

        current_tier = _infer_model_tier(raw_payload.get("current_tier"))
        if tool_name == "Task":
            subagent_model = _first_non_empty_str(tool_args.get("model"))
            if subagent_model is not None:
                metadata["subagent_model"] = subagent_model
                if current_tier is None:
                    current_tier = _infer_model_tier(subagent_model)

        strike_count = _parse_strike_count(raw_payload.get("strike_count"))
        touched_files = _collect_touched_files(raw_payload, tool_args)
        diff_text = _extract_diff_text(resolved_gate, raw_payload, tool_args)

        raw_continuation = raw_payload.get("stop_hook_active")
        if raw_continuation is None:
            raw_continuation = raw_payload.get("continuation_active", False)
        continuation_active = bool(raw_continuation)

        if resolved_gate == GateType.STOP_AUDIT:
            metadata["continuation_active"] = continuation_active
            metadata.setdefault("hook_event_name", "Stop")

        bypass_gates = bool(raw_payload.get("bypass_gates", False)) or (
            prompt_text is not None and "[bypass-gates]" in prompt_text.lower()
        )

        return GateEvent(
            gate=resolved_gate,
            session_id=session_id,
            turn_index=turn_index,
            workspace_root=workspace_root,
            harness=self.harness,
            tool_name=tool_name,
            tool_args=tool_args,
            prompt_text=prompt_text,
            assigned_file_cage=assigned_file_cage,
            current_tier=current_tier,
            strike_count=strike_count,
            touched_files=touched_files,
            diff_text=diff_text,
            continuation_active=continuation_active,
            bypass_gates=bypass_gates,
            metadata=metadata,
        )

    def format_verdict(
        self,
        gate: GateType,
        verdict: GateVerdict,
    ) -> tuple[int, dict[str, Any] | str, str]:
        """Convert `GateVerdict` into Claude Code `(exit_code, stdout_payload, stderr_text)`."""
        resolved_gate = GateType(gate)

        if resolved_gate == GateType.INTAKE:
            if verdict.action == VerdictAction.BLOCK:
                return (2, "", _format_block_message(verdict))
            if verdict.action == VerdictAction.AUGMENT:
                return (0, _format_augment_text(verdict), "")
            if verdict.action == VerdictAction.PROMOTE:
                return (2, "", _format_promote_message(verdict))
            return (0, "", "")

        if resolved_gate in {GateType.FIREWALL, GateType.SUBAGENT_GOVERNOR}:
            if verdict.action == VerdictAction.BLOCK:
                return (2, "", _format_block_message(verdict))
            if verdict.action == VerdictAction.MODIFY:
                updated_input = dict(verdict.updated_tool_args or {})
                if verdict.updated_model is not None and verdict.updated_model.strip():
                    updated_input["model"] = verdict.updated_model
                return (
                    0,
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": updated_input,
                        }
                    },
                    "",
                )
            if verdict.action == VerdictAction.PROMOTE:
                return (2, "", _format_promote_message(verdict))
            if verdict.action == VerdictAction.AUGMENT:
                return (0, _format_augment_text(verdict), "")
            return (0, "", "")

        # GateType.STOP_AUDIT (`Stop` / `SubagentStop`)
        if verdict.action in {VerdictAction.BLOCK, VerdictAction.PROMOTE} or (
            verdict.action == VerdictAction.AUGMENT and bool(verdict.unaddressed_clauses)
        ):
            stop_reason = _format_stop_block_reason(verdict)
            return (2, {"decision": "block", "reason": stop_reason}, stop_reason)

        return (0, "", "")

    def install_hooks(self, workspace_root: Path) -> Path:
        """Merge QuadGate hooks idempotently into `<workspace_root>/.claude/settings.json`."""
        ws_root = Path(workspace_root)
        settings_path = ws_root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        settings: dict[str, Any] = {}
        if settings_path.is_file():
            raw_text = settings_path.read_text(encoding="utf-8")
            if raw_text.strip():
                loaded = json.loads(raw_text)
                if not isinstance(loaded, dict):
                    raise ValueError(
                        f"Expected JSON object in {settings_path}, got {type(loaded).__name__}."
                    )
                settings = copy.deepcopy(loaded)

        raw_hooks = settings.get("hooks")
        if raw_hooks is None:
            hooks: dict[str, Any] = {}
        elif isinstance(raw_hooks, dict):
            hooks = dict(raw_hooks)
        else:
            raise ValueError(
                f"Expected 'hooks' to be a JSON object in {settings_path}, "
                f"got {type(raw_hooks).__name__}."
            )

        for event_name, matcher, command in _QUADGATE_HOOK_SPECS:
            raw_entries = hooks.get(event_name)
            if raw_entries is None:
                entries: list[Any] = []
            elif isinstance(raw_entries, list):
                entries = list(raw_entries)
            else:
                raise ValueError(
                    f"Expected hooks.{event_name!r} to be a JSON list in {settings_path}."
                )

            if not _has_hook_entry(entries, matcher, command):
                entry: dict[str, Any] = {}
                if matcher is not None:
                    entry["matcher"] = matcher
                entry["hooks"] = [{"type": "command", "command": command}]
                entries.append(entry)

            hooks[event_name] = entries

        settings["hooks"] = hooks
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return settings_path


__all__ = ["ClaudeCodeAdapter"]
