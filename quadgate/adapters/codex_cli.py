"""OpenAI Codex CLI wrapper, proxy env builder, and Stage-2A/Gate-4 adapter."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from quadgate.adapters.base import HarnessAdapter
from quadgate.contracts import (
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.kernel.gates import GateEngine, load_cartridge
from quadgate.shim import check_stage2a_deterministic_security

_T = TypeVar("_T")

DEFAULT_PROXY_URL = "http://127.0.0.1:8788/v1"
_MANAGED_START = "# >>> quadgate managed block >>>"
_MANAGED_END = "# <<< quadgate managed block <<<"
_MANAGED_LINES: tuple[str, ...] = (
    _MANAGED_START,
    (
        "# Routes cold sub-worker requests through the QuadGate local proxy "
        "while guarding shell mutations"
    ),
    f'export OPENAI_BASE_URL="{DEFAULT_PROXY_URL}"',
    'quadgate exec-codex -- "$@"',
    _MANAGED_END,
)

_CODEX_VALUE_FLAGS = frozenset(
    {
        "-m",
        "--model",
        "-p",
        "--profile",
        "-s",
        "--sandbox",
        "-a",
        "--ask-for-approval",
        "-C",
        "--cd",
        "--config",
        "-i",
        "--image",
        "-o",
        "--output-last-message",
        "--color",
        "--local-provider",
        "--add-dir",
    }
)
_CODEX_BOOL_FLAGS = frozenset(
    {
        "--full-auto",
        "--yolo",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--json",
        "--oss",
        "--search",
        "-h",
        "--help",
        "-V",
        "--version",
    }
)
_SHELL_CMD_FLAGS = frozenset({"-c", "-lc", "/c", "-command", "--command"})
_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh", "dash", "pwsh", "powershell", "cmd"})


def _run_coro_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Execute an async coroutine cleanly from sync code or inside an active event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _extract_command_string(clean_args: Sequence[str]) -> str:
    """Extract the effective shell/execution command string from cleaned Codex CLI args."""
    tokens = [str(arg) for arg in clean_args if str(arg)]
    if not tokens:
        return ""

    def _skip_codex_flags(pos: int) -> int:
        while pos < len(tokens):
            tok = tokens[pos]
            if tok in _CODEX_BOOL_FLAGS:
                pos += 1
                continue
            if tok in _CODEX_VALUE_FLAGS:
                pos += 2
                continue
            if any(tok.startswith(flag + "=") for flag in _CODEX_VALUE_FLAGS):
                pos += 1
                continue
            if (
                tok == "-c"
                and pos + 1 < len(tokens)
                and "=" in tokens[pos + 1]
                and not any(ch in tokens[pos + 1] for ch in (" ", "\t", ";", "|", "&"))
            ):
                pos += 2
                continue
            break
        return pos

    idx = _skip_codex_flags(0)
    if idx < len(tokens) and tokens[idx] == "exec":
        idx += 1
        if idx < len(tokens) and tokens[idx] == "--":
            idx += 1
        idx = _skip_codex_flags(idx)

    rest = tokens[idx:]
    if not rest:
        return ""

    first_lower = rest[0].lower()
    if first_lower in _SHELL_CMD_FLAGS:
        return " ".join(rest[1:]).strip()
    if first_lower.startswith(("--command=", "-c=")):
        return rest[0].split("=", 1)[1].strip()

    exe_base = os.path.basename(rest[0].replace("\\", "/")).lower()
    if exe_base.endswith(".exe"):
        exe_base = exe_base[:-4]
    if exe_base in _SHELL_WRAPPERS and len(rest) >= 3 and rest[1].lower() in _SHELL_CMD_FLAGS:
        return " ".join(rest[2:]).strip()

    return " ".join(rest).strip()


def _parse_porcelain_paths(stdout: str) -> list[str]:
    """Parse `git status --porcelain` lines without stripping leading status whitespace."""
    paths: list[str] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        raw_path = line[3:].strip()
        if " -> " in raw_path and ("R" in status or "C" in status):
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        if len(raw_path) >= 2 and raw_path.startswith('"') and raw_path.endswith('"'):
            raw_path = raw_path[1:-1]
        if raw_path and raw_path not in seen:
            seen.add(raw_path)
            paths.append(raw_path)
    return paths


def _collect_git_diff_and_status(workspace_root: Path) -> tuple[list[str], str]:
    """Inspect workspace via git status/diff, falling back gracefully on non-repos or empty HEAD."""
    if not workspace_root.is_dir():
        return [], ""

    def _run_git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    status_proc = _run_git("status", "--porcelain", "-uall")
    if status_proc is None or status_proc.returncode != 0:
        return [], ""

    touched = _parse_porcelain_paths(status_proc.stdout)

    diff_proc = _run_git("diff", "HEAD")
    if diff_proc is not None and diff_proc.returncode == 0:
        return touched, diff_proc.stdout

    # Fresh `git init` repository without a HEAD commit: combine staged and unstaged diffs.
    diff_chunks: list[str] = []
    for diff_args in (("diff", "--cached"), ("diff",)):
        proc = _run_git(*diff_args)
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            diff_chunks.append(proc.stdout)
    return touched, "\n".join(diff_chunks)


class CodexCliAdapter(HarnessAdapter):
    """Adapter for OpenAI Codex CLI (`quadgate exec-codex` + `OPENAI_BASE_URL` proxy)."""

    @property
    def harness(self) -> HarnessType:
        return HarnessType.CODEX_CLI

    def parse_event(self, gate: GateType, raw_payload: dict[str, Any]) -> GateEvent:
        """Translate Codex CLI wrapper payloads into a canonical `GateEvent`."""
        resolved_gate = gate if isinstance(gate, GateType) else GateType(str(gate))

        raw_session = (
            raw_payload.get("session_id")
            or raw_payload.get("sessionId")
            or raw_payload.get("conversation_id")
            or raw_payload.get("conversationId")
        )
        session_id = str(raw_session).strip() if raw_session is not None else ""
        if not session_id:
            session_id = "codex-session"

        raw_turn = raw_payload.get("turn_index")
        if raw_turn is None:
            raw_turn = raw_payload.get("turnIndex")
        if raw_turn is None:
            raw_turn = raw_payload.get("turn")
        try:
            turn_index = max(1, int(raw_turn)) if raw_turn is not None else 1
        except (TypeError, ValueError):
            turn_index = 1

        raw_ws = (
            raw_payload.get("workspace_root")
            or raw_payload.get("workspaceRoot")
            or raw_payload.get("cwd")
        )
        workspace_root = str(raw_ws).strip() if raw_ws is not None else ""
        if not workspace_root:
            workspace_root = "."

        raw_tool_args = raw_payload.get("tool_args")
        if not isinstance(raw_tool_args, Mapping):
            for alt_key in ("tool_input", "args", "arguments"):
                candidate = raw_payload.get(alt_key)
                if isinstance(candidate, Mapping):
                    raw_tool_args = candidate
                    break
        tool_args: dict[str, Any] = (
            dict(raw_tool_args) if isinstance(raw_tool_args, Mapping) else {}
        )

        raw_tool_name = raw_payload.get("tool_name") or raw_payload.get("toolName")
        tool_name: str | None = (
            str(raw_tool_name).strip()
            if isinstance(raw_tool_name, str) and raw_tool_name.strip()
            else None
        )

        raw_cmd = raw_payload.get("command")
        if raw_cmd is None:
            raw_cmd = raw_payload.get("cmd")
        if isinstance(raw_cmd, (list, tuple)):
            cmd_str = " ".join(str(part) for part in raw_cmd if str(part)).strip()
        elif isinstance(raw_cmd, str):
            cmd_str = raw_cmd
        else:
            cmd_str = ""

        if cmd_str:
            if tool_name is None:
                tool_name = "shell"
            tool_args.setdefault("CommandLine", cmd_str)
        elif tool_name is None:
            for cmd_key in ("CommandLine", "command", "cmd"):
                nested_cmd = tool_args.get(cmd_key)
                if isinstance(nested_cmd, str) and nested_cmd.strip():
                    tool_name = "shell"
                    tool_args.setdefault("CommandLine", nested_cmd)
                    break

        raw_prompt = raw_payload.get("prompt_text")
        if raw_prompt is None:
            raw_prompt = raw_payload.get("prompt")
        if raw_prompt is None:
            raw_prompt = raw_payload.get("message")
        prompt_text = str(raw_prompt) if raw_prompt is not None else None

        raw_cage = raw_payload.get("assigned_file_cage")
        assigned_file_cage = (
            [str(item) for item in raw_cage if isinstance(item, str)]
            if isinstance(raw_cage, list)
            else None
        )

        raw_tier = raw_payload.get("current_tier")
        current_tier: ModelTier | None = None
        if isinstance(raw_tier, ModelTier):
            current_tier = raw_tier
        elif isinstance(raw_tier, str) and raw_tier.strip().lower() in {
            ModelTier.FLOOR.value,
            ModelTier.MID.value,
            ModelTier.FRONTIER.value,
        }:
            current_tier = ModelTier(raw_tier.strip().lower())

        raw_strike = raw_payload.get("strike_count", 0)
        try:
            strike_count = max(0, int(raw_strike))
        except (TypeError, ValueError):
            strike_count = 0

        raw_touched = raw_payload.get("touched_files")
        touched_files = (
            [str(item) for item in raw_touched if isinstance(item, str) and item.strip()]
            if isinstance(raw_touched, list)
            else []
        )

        raw_diff = raw_payload.get("diff_text")
        diff_text = raw_diff if isinstance(raw_diff, str) else None

        continuation_active = bool(raw_payload.get("continuation_active", False))
        bypass_gates = bool(raw_payload.get("bypass_gates", False)) or (
            prompt_text is not None and "[bypass-gates]" in prompt_text.lower()
        )

        raw_meta = raw_payload.get("metadata")
        metadata: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}

        return GateEvent(
            gate=resolved_gate,
            session_id=session_id,
            turn_index=turn_index,
            workspace_root=workspace_root,
            harness=HarnessType.CODEX_CLI,
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
        """Convert canonical `GateVerdict` to Codex CLI exit code, stdout payload, and stderr."""
        del gate
        action = verdict.action

        if action == VerdictAction.BLOCK:
            receipt = verdict.violation_receipt
            lines: list[str] = []
            if receipt is not None:
                headline = f"[{receipt.rule_id}] {receipt.remediation}"
                if receipt.violated_path:
                    headline += f" (path: {receipt.violated_path})"
                lines.append(headline)
                if (
                    verdict.reason
                    and verdict.reason.strip()
                    and verdict.reason.strip() not in headline
                ):
                    lines.append(f"Reason: {verdict.reason.strip()}")
                if receipt.bypass_instruction and receipt.bypass_instruction.strip():
                    lines.append(f"Bypass: {receipt.bypass_instruction.strip()}")
            else:
                reason_str = (verdict.reason or "Operation blocked by QuadGate policy.").strip()
                lines.append(f"[QUADGATE_BLOCK] {reason_str}")
            if verdict.unaddressed_clauses:
                lines.append("Unaddressed: " + "; ".join(verdict.unaddressed_clauses))
            return 2, "", "\n".join(lines)

        if action == VerdictAction.PROMOTE:
            tier_val = verdict.target_tier.value if verdict.target_tier else None
            tier_label = tier_val or ModelTier.FRONTIER.value
            model_label = verdict.updated_model or "pro"
            reason_str = (
                verdict.reason or f"Promote to tier={tier_label} model={model_label}."
            ).strip()
            promo_lines = [
                f"QUADGATE_PROMOTE (tier={tier_label}, model={model_label}): {reason_str}"
            ]
            if verdict.unaddressed_clauses:
                promo_lines.append("Unaddressed: " + "; ".join(verdict.unaddressed_clauses))
            payload: dict[str, Any] = {
                "action": "promote",
                "target_tier": tier_val,
                "updated_model": verdict.updated_model,
                "reason": verdict.reason,
            }
            return 2, payload, "\n".join(promo_lines)

        if action == VerdictAction.AUGMENT:
            return (
                0,
                {
                    "action": "augment",
                    "tail_hints": list(verdict.tail_hints),
                    "unaddressed_clauses": list(verdict.unaddressed_clauses),
                    "reason": verdict.reason,
                },
                "",
            )

        if action == VerdictAction.MODIFY:
            return (
                0,
                {
                    "action": "modify",
                    "updated_tool_args": dict(verdict.updated_tool_args)
                    if verdict.updated_tool_args
                    else {},
                    "updated_model": verdict.updated_model,
                    "reason": verdict.reason,
                },
                "",
            )

        return 0, {"action": "allow"}, ""

    def build_proxy_env(
        self,
        base_env: Mapping[str, str] | None = None,
        *,
        proxy_url: str = DEFAULT_PROXY_URL,
    ) -> dict[str, str]:
        """Return an environment mapping routed through the QuadGate local LLM proxy."""
        env = dict(os.environ if base_env is None else base_env)
        env["OPENAI_BASE_URL"] = proxy_url
        env["QUADGATE_HARNESS"] = self.harness.value
        return env

    def evaluate_command_guard(
        self,
        command: str,
        workspace_root: Path,
        *,
        session_id: str = "codex-session",
        gate_engine: GateEngine | None = None,
        bypass_gates: bool = False,
    ) -> GateVerdict:
        """Run offline Stage-2A deterministic security checks first, then Gate-2 firewall."""
        ws = Path(workspace_root)
        stage2a_payload: dict[str, object] = {"CommandLine": command}
        receipt = check_stage2a_deterministic_security(stage2a_payload, str(ws))
        if receipt is not None:
            return GateVerdict(
                action=VerdictAction.BLOCK,
                gate=GateType.FIREWALL,
                violation_receipt=ViolationReceipt.model_validate(receipt),
                reason=str(receipt["remediation"]),
            )

        engine = (
            gate_engine
            if gate_engine is not None
            else GateEngine(load_cartridge("software_engineering"))
        )
        event = GateEvent(
            gate=GateType.FIREWALL,
            session_id=session_id,
            workspace_root=str(ws),
            harness=HarnessType.CODEX_CLI,
            tool_name="run_command",
            tool_args={"CommandLine": command},
            prompt_text=command,
            bypass_gates=bypass_gates,
        )
        return _run_coro_sync(engine.evaluate_gate2_firewall(event))

    def verify_post_turn_diff(
        self,
        workspace_root: Path,
        prompt_text: str,
        *,
        session_id: str = "codex-session",
        touched_files: list[str] | None = None,
        diff_text: str | None = None,
        current_tier: ModelTier | None = None,
        strike_count: int = 0,
        gate_engine: GateEngine | None = None,
    ) -> GateVerdict:
        """Inspect post-turn git workspace state and evaluate Gate 4 stop audit."""
        ws = Path(workspace_root)
        if touched_files is None or diff_text is None:
            git_touched, git_diff = _collect_git_diff_and_status(ws)
            resolved_touched = list(touched_files) if touched_files is not None else git_touched
            resolved_diff = diff_text if diff_text is not None else git_diff
        else:
            resolved_touched = list(touched_files)
            resolved_diff = diff_text

        engine = (
            gate_engine
            if gate_engine is not None
            else GateEngine(load_cartridge("software_engineering"))
        )
        event = GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id=session_id,
            workspace_root=str(ws),
            harness=HarnessType.CODEX_CLI,
            prompt_text=prompt_text,
            current_tier=current_tier,
            strike_count=strike_count,
            touched_files=resolved_touched,
            diff_text=resolved_diff,
        )
        return _run_coro_sync(engine.evaluate_gate4_stop(event))

    def run_exec_codex(
        self,
        codex_args: Sequence[str],
        *,
        workspace_root: Path | None = None,
        proxy_url: str = DEFAULT_PROXY_URL,
        prompt_text: str = "",
        runner: Callable[[list[str], dict[str, str], Path], int] | None = None,
        verify_diff: bool = True,
        gate_engine: GateEngine | None = None,
    ) -> int:
        """Guard, proxy-wrap, execute, and post-turn verify a Codex CLI invocation."""
        ws = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()

        clean_args = list(codex_args)
        if clean_args and clean_args[0] == "--":
            clean_args = clean_args[1:]

        command_str = _extract_command_string(clean_args)
        if command_str:
            guard_verdict = self.evaluate_command_guard(
                command_str,
                ws,
                gate_engine=gate_engine,
            )
            if guard_verdict.action in {VerdictAction.BLOCK, VerdictAction.PROMOTE}:
                _, _, err_msg = self.format_verdict(GateType.FIREWALL, guard_verdict)
                fallback = f"Blocked by QuadGate firewall: {guard_verdict.reason or ''}".strip()
                msg = err_msg or fallback
                sys.stderr.write(msg if msg.endswith("\n") else f"{msg}\n")
                return 2

        env = self.build_proxy_env(os.environ, proxy_url=proxy_url)
        argv = ["codex", *clean_args]

        if runner is not None:
            returncode = int(runner(argv, env, ws))
        else:
            try:
                completed = subprocess.run(argv, cwd=ws, env=env, check=False)
                returncode = int(completed.returncode)
            except FileNotFoundError:
                sys.stderr.write("codex executable not found on PATH.\n")
                return 127

        if returncode == 0 and verify_diff:
            audit_prompt = prompt_text or " ".join(clean_args)
            diff_verdict = self.verify_post_turn_diff(
                ws,
                audit_prompt,
                gate_engine=gate_engine,
            )
            if diff_verdict.action in {VerdictAction.BLOCK, VerdictAction.PROMOTE}:
                _, _, err_msg = self.format_verdict(GateType.STOP_AUDIT, diff_verdict)
                fallback = (
                    f"Post-turn verification failed ({diff_verdict.action.value}): "
                    f"{diff_verdict.reason or ''}"
                ).strip()
                msg = err_msg or fallback
                sys.stderr.write(msg if msg.endswith("\n") else f"{msg}\n")
                return 2

        return returncode

    def install_hooks(self, workspace_root: Path) -> Path:
        """Create or idempotently update `<workspace_root>/.codex/profile.sh`."""
        profile_path = Path(workspace_root) / ".codex" / "profile.sh"
        profile_path.parent.mkdir(parents=True, exist_ok=True)

        if not profile_path.exists():
            content = "\n".join(["#!/usr/bin/env bash", *_MANAGED_LINES]) + "\n"
            profile_path.write_text(content, encoding="utf-8", newline="\n")
            if os.name != "nt":
                profile_path.chmod(0o755)
            return profile_path

        existing_lines = profile_path.read_text(encoding="utf-8").splitlines()
        start_idx: int | None = None
        end_idx: int | None = None
        for idx, line in enumerate(existing_lines):
            stripped = line.strip()
            if stripped == _MANAGED_START and start_idx is None:
                start_idx = idx
            elif stripped == _MANAGED_END and start_idx is not None:
                end_idx = idx
                break

        if start_idx is not None and end_idx is not None:
            updated_lines = [
                *existing_lines[:start_idx],
                *_MANAGED_LINES,
                *existing_lines[end_idx + 1 :],
            ]
        else:
            # Strip legacy unmanaged QuadGate lines before appending the canonical managed block.
            filtered_lines: list[str] = []
            for line in existing_lines:
                stripped = line.strip()
                if stripped.startswith(
                    (
                        "export OPENAI_BASE_URL=",
                        "OPENAI_BASE_URL=",
                        "export QUADGATE_HARNESS=",
                        "QUADGATE_HARNESS=",
                    )
                ):
                    continue
                if stripped.startswith("quadgate exec-codex"):
                    continue
                if stripped == _MANAGED_LINES[1]:
                    continue
                filtered_lines.append(line)

            while filtered_lines and not filtered_lines[-1].strip():
                filtered_lines.pop()

            if not filtered_lines:
                updated_lines = ["#!/usr/bin/env bash", *_MANAGED_LINES]
            else:
                updated_lines = [*filtered_lines, *_MANAGED_LINES]

        profile_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8", newline="\n")
        if os.name != "nt":
            try:
                profile_path.chmod(profile_path.stat().st_mode | 0o111)
            except OSError:
                pass
        return profile_path


__all__ = ["CodexCliAdapter"]
