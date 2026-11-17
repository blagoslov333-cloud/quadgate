"""Antigravity `.agents/hooks.json` adapter and payload translator."""

from __future__ import annotations

import json
import re
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

DEFAULT_SESSION_ID = "antigravity-session"
FIREWALL_MATCHER = (
    "^(write_to_file|replace_file_content|multi_replace_file_content|"
    "write_file|edit_file|delete_file|run_command)$"
)
SUBAGENT_MATCHER = "^invoke_subagent$"

_INTAKE_COMMAND = "qg-shim --harness antigravity --gate intake"
_FIREWALL_COMMAND = "qg-shim --harness antigravity --gate firewall"
_SUBAGENT_COMMAND = "qg-shim --harness antigravity --gate subagent_governor"
_STOP_COMMAND = "qg-shim --harness antigravity --gate stop_audit"

_MODEL_TOKEN_SPLIT_RE = re.compile(r"[-_./\s]+")
_TOUCHED_ARG_KEYS = (
    "TargetFile",
    "file_path",
    "path",
    "AbsolutePath",
    "TargetPath",
    "Path",
    "target_file",
)
_DIFF_ARG_KEYS = (
    "diff_text",
    "CodeContent",
    "ReplacementContent",
    "diff",
    "patch",
    "content",
    "new_string",
    "new_text",
    "replacement",
)


def _first_str(mapping: dict[str, Any], *keys: str, strip_value: bool = True) -> str | None:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip() if strip_value else val
    return None


def _first_not_none(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _extract_clamped_int(
    mapping: dict[str, Any],
    keys: tuple[str, ...],
    *,
    minimum: int,
    default: int,
) -> int:
    for key in keys:
        val = mapping.get(key)
        if val is None or isinstance(val, bool):
            continue
        try:
            return max(minimum, int(val))
        except (TypeError, ValueError):
            continue
    return default


def _extract_workspace_root(raw_payload: dict[str, Any]) -> str:
    ws_paths = raw_payload.get("workspacePaths")
    if isinstance(ws_paths, list):
        for entry in ws_paths:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
            if isinstance(entry, Path):
                text = str(entry).strip()
                if text:
                    return text
    return _first_str(raw_payload, "cwd", "workspace_root", "workspaceRoot") or "."


def _infer_model_tier(value: Any) -> ModelTier | None:
    if isinstance(value, ModelTier):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized or normalized == "inherit":
        return None

    direct_map = {
        "floor": ModelTier.FLOOR,
        "flash_lite": ModelTier.FLOOR,
        "mid": ModelTier.MID,
        "flash": ModelTier.MID,
        "frontier": ModelTier.FRONTIER,
        "pro": ModelTier.FRONTIER,
    }
    if normalized in direct_map:
        return direct_map[normalized]

    tokens = {tok for tok in _MODEL_TOKEN_SPLIT_RE.split(normalized) if tok}
    if "flash" in tokens and "lite" in tokens:
        return ModelTier.FLOOR
    if tokens & {"haiku", "mini", "nano"}:
        return ModelTier.FLOOR
    if tokens & {"flash", "sonnet"}:
        return ModelTier.MID
    if tokens & {"pro", "opus"}:
        return ModelTier.FRONTIER
    return None


def _coerce_args_dict(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        stripped = raw_args.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return {}
            if isinstance(parsed, dict):
                return parsed
    return {}


def _extract_tool_call(
    raw_payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any], str | None]:
    tool_call = raw_payload.get("toolCall") or raw_payload.get("tool_call")
    if isinstance(tool_call, dict):
        tc_id_raw = tool_call.get("id")
        tc_id = str(tc_id_raw).strip() if tc_id_raw is not None and str(tc_id_raw).strip() else None
        raw_name = (
            tool_call.get("name")
            or raw_payload.get("tool_name")
            or raw_payload.get("toolName")
        )
        tool_name = (
            str(raw_name).strip() if isinstance(raw_name, str) and raw_name.strip() else None
        )
        raw_args = _first_not_none(tool_call, "args", "arguments", "input")
        tool_args = _coerce_args_dict(raw_args)
        return tool_name, tool_args, tc_id

    tool_name = _first_str(raw_payload, "tool_name", "toolName")
    raw_args = _first_not_none(
        raw_payload,
        "tool_args",
        "tool_input",
        "args",
        "arguments",
        "input",
    )
    return tool_name, _coerce_args_dict(raw_args), None


def _collect_string_list(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for entry in raw_value:
        if isinstance(entry, str):
            text = entry.strip()
            if text and text not in seen:
                seen.add(text)
                items.append(text)
    return items


def _extract_diff_from_tool_args(tool_args: dict[str, Any]) -> str | None:
    direct = _first_str(tool_args, *_DIFF_ARG_KEYS, strip_value=False)
    if direct is not None:
        return direct
    chunks = tool_args.get("ReplacementChunks")
    if isinstance(chunks, list):
        parts = [
            chunk["ReplacementContent"]
            for chunk in chunks
            if isinstance(chunk, dict)
            and isinstance(chunk.get("ReplacementContent"), str)
            and chunk["ReplacementContent"].strip()
        ]
        if parts:
            return "\n".join(parts)
    return None


def _format_block_reason(
    verdict: GateVerdict,
    default_msg: str = "Operation blocked by QuadGate policy.",
) -> str:
    receipt = verdict.violation_receipt
    if receipt is None:
        return verdict.reason.strip() if verdict.reason and verdict.reason.strip() else default_msg

    remediation = receipt.remediation.strip()
    if remediation.startswith(f"{receipt.rule_id}:"):
        parts = [remediation]
    else:
        parts = [f"{receipt.rule_id}: {remediation}"]

    if receipt.violated_path and receipt.violated_path not in parts[0]:
        parts.append(f"(path: {receipt.violated_path})")
    if receipt.bypass_instruction and receipt.bypass_instruction not in parts[0]:
        parts.append(receipt.bypass_instruction.strip())
    return " ".join(parts)


def _format_stop_reason(verdict: GateVerdict) -> str:
    parts: list[str] = []
    if verdict.violation_receipt is not None:
        parts.append(_format_block_reason(verdict))
        if verdict.reason and verdict.reason.strip():
            reason_clean = verdict.reason.strip()
            if (
                reason_clean not in parts[0]
                and verdict.violation_receipt.remediation not in reason_clean
            ):
                parts.append(reason_clean)
    elif verdict.reason and verdict.reason.strip():
        parts.append(verdict.reason.strip())

    clauses = [c.strip() for c in verdict.unaddressed_clauses if isinstance(c, str) and c.strip()]
    if clauses:
        joined = " ".join(parts)
        missing = [c for c in clauses if c not in joined]
        if missing:
            parts.append("Unaddressed clauses: " + "; ".join(missing))

    if parts:
        return " ".join(parts)
    if verdict.action == VerdictAction.PROMOTE:
        return "Promote next turn to higher tier and re-verify completion."
    return "Continue until all requested clauses and checks pass."


def _entry_commands(entry: Any) -> set[str]:
    cmds: set[str] = set()
    if not isinstance(entry, dict):
        return cmds
    cmd = entry.get("command")
    if isinstance(cmd, str) and cmd.strip():
        cmds.add(cmd.strip())
    nested = entry.get("hooks")
    if isinstance(nested, list):
        for item in nested:
            if isinstance(item, dict):
                sub_cmd = item.get("command")
                if isinstance(sub_cmd, str) and sub_cmd.strip():
                    cmds.add(sub_cmd.strip())
    return cmds


def _merge_hook_entries(
    existing: Any,
    required_entries: list[dict[str, str]],
) -> list[Any]:
    req_by_cmd = {entry["command"]: entry for entry in required_entries}
    merged: list[Any] = []
    seen_commands: set[str] = set()

    if isinstance(existing, list):
        for item in existing:
            cmds = _entry_commands(item)
            matched_req_cmds = cmds & req_by_cmd.keys()
            if not matched_req_cmds:
                merged.append(item)
                continue
            if len(matched_req_cmds) == 1 and isinstance(item, dict) and "command" in item:
                cmd = next(iter(matched_req_cmds))
                if cmd in seen_commands:
                    continue
                updated = dict(item)
                req = req_by_cmd[cmd]
                if "matcher" in req:
                    updated["matcher"] = req["matcher"]
                merged.append(updated)
                seen_commands.add(cmd)
            else:
                merged.append(item)
                seen_commands.update(matched_req_cmds)

    for req in required_entries:
        if req["command"] not in seen_commands:
            merged.append(dict(req))
            seen_commands.add(req["command"])

    return merged


class AntigravityAdapter(HarnessAdapter):
    """Adapter for Antigravity lifecycle hooks (`PreInvocation`, `PreToolUse`, `Stop`)."""

    @property
    def harness(self) -> HarnessType:
        return HarnessType.ANTIGRAVITY

    def infer_gate(self, raw_payload: dict[str, Any]) -> GateType:
        """Infer the canonical QuadGate lifecycle gate from an Antigravity hook payload."""
        hook_event = _first_str(raw_payload, "hookEventName", "hook_event_name")
        tool_name, _, _ = _extract_tool_call(raw_payload)

        if hook_event is not None:
            if hook_event == "PreInvocation":
                return GateType.INTAKE
            if hook_event == "PreToolUse":
                return (
                    GateType.SUBAGENT_GOVERNOR
                    if tool_name == "invoke_subagent"
                    else GateType.FIREWALL
                )
            if hook_event == "Stop":
                return GateType.STOP_AUDIT
            raise ValueError(f"Unsupported Antigravity hookEventName: {hook_event!r}")

        if tool_name == "invoke_subagent":
            return GateType.SUBAGENT_GOVERNOR
        if tool_name is not None or isinstance(
            raw_payload.get("toolCall") or raw_payload.get("tool_call"),
            dict,
        ):
            return GateType.FIREWALL
        if any(
            key in raw_payload
            for key in (
                "continuationActive",
                "continuation_active",
                "terminationReason",
                "termination_reason",
                "touchedFiles",
                "touched_files",
            )
        ):
            return GateType.STOP_AUDIT
        return GateType.INTAKE

    def parse_event(self, gate: GateType, raw_payload: dict[str, Any]) -> GateEvent:
        """Normalize an Antigravity hook JSON payload into a canonical `GateEvent`."""
        gate = GateType(gate)
        session_id = (
            _first_str(
                raw_payload,
                "conversationId",
                "session_id",
                "conversation_id",
                "sessionId",
            )
            or DEFAULT_SESSION_ID
        )
        turn_index = _extract_clamped_int(
            raw_payload,
            ("invocationNum", "executionNum", "stepIdx", "turn_index"),
            minimum=1,
            default=1,
        )
        strike_count = _extract_clamped_int(
            raw_payload,
            ("strike_count", "strikeCount"),
            minimum=0,
            default=0,
        )
        workspace_root = _extract_workspace_root(raw_payload)

        metadata: dict[str, Any] = {}
        if isinstance(raw_payload.get("metadata"), dict):
            metadata.update(raw_payload["metadata"])
        hook_event_name = _first_str(raw_payload, "hookEventName", "hook_event_name")
        if hook_event_name is not None:
            metadata.setdefault("hook_event_name", hook_event_name)

        prompt_text = _first_str(
            raw_payload,
            "lastUserInput",
            "prompt_text",
            "prompt",
            "userPrompt",
            strip_value=False,
        )

        raw_cage = _first_not_none(raw_payload, "assigned_file_cage", "assignedFileCage")
        assigned_file_cage = _collect_string_list(raw_cage) if isinstance(raw_cage, list) else None

        tool_name: str | None = None
        tool_args: dict[str, Any] = {}
        touched_files = _collect_string_list(
            _first_not_none(raw_payload, "touchedFiles", "touched_files")
        )
        diff_text = _first_str(raw_payload, "diffText", "diff_text", strip_value=False)
        continuation_active = bool(
            _first_not_none(raw_payload, "continuationActive", "continuation_active") or False
        )
        current_tier = _infer_model_tier(
            _first_not_none(raw_payload, "current_tier", "currentTier")
        )

        if gate in {GateType.FIREWALL, GateType.SUBAGENT_GOVERNOR}:
            tool_name, tool_args, tc_id = _extract_tool_call(raw_payload)
            if tc_id is not None:
                metadata["tool_call_id"] = tc_id

            if prompt_text is None and gate == GateType.SUBAGENT_GOVERNOR:
                prompt_text = _first_str(
                    tool_args,
                    "Prompt",
                    "prompt",
                    "brief",
                    "Task",
                    "task",
                    "description",
                    strip_value=False,
                )

            seen_touched = set(touched_files)
            for key in _TOUCHED_ARG_KEYS:
                val = tool_args.get(key)
                if isinstance(val, str):
                    cleaned = val.strip()
                    if cleaned and cleaned not in seen_touched:
                        seen_touched.add(cleaned)
                        touched_files.append(cleaned)

            if diff_text is None:
                diff_text = _extract_diff_from_tool_args(tool_args)

            subagent_model = _first_str(tool_args, "Model", "model")
            if subagent_model is not None:
                metadata["subagent_model"] = subagent_model
                if current_tier is None:
                    current_tier = _infer_model_tier(subagent_model)

        elif gate == GateType.STOP_AUDIT:
            termination_reason = _first_not_none(
                raw_payload,
                "terminationReason",
                "termination_reason",
            )
            model_name = _first_not_none(raw_payload, "modelName", "model_name")
            metadata["continuation_active"] = continuation_active
            metadata["termination_reason"] = (
                str(termination_reason)
                if isinstance(termination_reason, str) and termination_reason
                else termination_reason
            )
            metadata["model_name"] = (
                str(model_name)
                if isinstance(model_name, str) and model_name
                else model_name
            )
            if current_tier is None:
                current_tier = _infer_model_tier(model_name)

        elif gate == GateType.INTAKE and current_tier is None:
            current_tier = _infer_model_tier(
                _first_not_none(raw_payload, "modelName", "model_name")
            )

        bypass_flag = bool(
            _first_not_none(raw_payload, "bypass_gates", "bypassGates") or False
        )
        bypass_gates = bypass_flag or (
            prompt_text is not None and "[bypass-gates]" in prompt_text.lower()
        )

        return GateEvent(
            gate=gate,
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
        """Convert a canonical `GateVerdict` into `(0, stdout_json_dict, '')`."""
        gate = GateType(gate)

        if gate == GateType.INTAKE:
            if verdict.action == VerdictAction.AUGMENT:
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
                injected_text = "\n".join(parts)
                if not injected_text:
                    return 0, {}, ""
                return (
                    0,
                    {
                        "injectSteps": [{"userMessage": injected_text}],
                        "additionalContext": injected_text,
                        "terminationBehavior": "",
                    },
                    "",
                )
            if verdict.action == VerdictAction.BLOCK:
                return (
                    0,
                    {
                        "decision": "deny",
                        "terminationBehavior": "terminate",
                        "reason": _format_block_reason(verdict),
                    },
                    "",
                )
            return 0, {}, ""

        if gate in {GateType.FIREWALL, GateType.SUBAGENT_GOVERNOR}:
            if verdict.action == VerdictAction.BLOCK:
                return (
                    0,
                    {
                        "decision": "deny",
                        "reason": _format_block_reason(verdict),
                    },
                    "",
                )
            if verdict.action == VerdictAction.MODIFY:
                overwrite_args = dict(verdict.updated_tool_args or {})
                if verdict.updated_model:
                    overwrite_args["Model"] = verdict.updated_model
                payload: dict[str, Any] = {
                    "decision": "allow",
                    "overwrite": overwrite_args,
                    "updatedInput": dict(overwrite_args),
                }
                if verdict.updated_model:
                    payload["updatedModel"] = verdict.updated_model
                if verdict.reason:
                    payload["reason"] = verdict.reason
                return 0, payload, ""
            if verdict.action == VerdictAction.PROMOTE:
                payload = {
                    "decision": "deny",
                    "action": "promote",
                    "reason": verdict.reason or "Promote subagent tier",
                }
                if verdict.updated_model:
                    payload["updatedModel"] = verdict.updated_model
                if verdict.target_tier is not None:
                    payload["targetTier"] = verdict.target_tier.value
                return 0, payload, ""
            return 0, {"decision": "allow"}, ""

        if gate == GateType.STOP_AUDIT:
            if verdict.action in {VerdictAction.BLOCK, VerdictAction.PROMOTE} or (
                verdict.action == VerdictAction.AUGMENT and verdict.unaddressed_clauses
            ):
                payload = {
                    "decision": "continue",
                    "reason": _format_stop_reason(verdict),
                    "unaddressed_clauses": list(verdict.unaddressed_clauses),
                    "promote_next_turn_to_frontier": verdict.action == VerdictAction.PROMOTE,
                }
                if verdict.updated_model:
                    payload["updatedModel"] = verdict.updated_model
                if verdict.target_tier is not None:
                    payload["targetTier"] = verdict.target_tier.value
                return 0, payload, ""
            return 0, {"decision": "allow"}, ""

        return 0, {}, ""

    def install_hooks(self, workspace_root: Path) -> Path:
        """Create or safely merge QuadGate hooks into `<workspace_root>/.agents/hooks.json`."""
        ws_root = Path(workspace_root)
        hooks_path = ws_root / ".agents" / "hooks.json"
        hooks_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}
        if hooks_path.is_file():
            raw_text = hooks_path.read_text(encoding="utf-8-sig")
            if raw_text.strip():
                try:
                    loaded = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {hooks_path}: {exc}") from exc
                if not isinstance(loaded, dict):
                    raise ValueError(f"Expected JSON object in {hooks_path}")
                data = loaded

        data.setdefault("enabled", True)
        data["PreInvocation"] = _merge_hook_entries(
            data.get("PreInvocation"),
            [{"command": _INTAKE_COMMAND}],
        )
        data["PreToolUse"] = _merge_hook_entries(
            data.get("PreToolUse"),
            [
                {"matcher": FIREWALL_MATCHER, "command": _FIREWALL_COMMAND},
                {"matcher": SUBAGENT_MATCHER, "command": _SUBAGENT_COMMAND},
            ],
        )
        data["Stop"] = _merge_hook_entries(
            data.get("Stop"),
            [{"command": _STOP_COMMAND}],
        )

        hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return hooks_path
