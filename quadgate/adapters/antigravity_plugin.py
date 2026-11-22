"""Antigravity 2.0 global plugin packager, installer, and exporter for QuadGate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from quadgate import __version__

PLUGIN_NAME = "quadgate"
PLUGIN_VERSION = __version__

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_PLUGIN_MANIFEST: dict[str, Any] = {
    "name": PLUGIN_NAME,
    "displayName": "QuadGate 4-Gate Control Plane",
    "version": PLUGIN_VERSION,
    "description": (
        "4-gate discriminative control plane for Antigravity and Swarms: "
        "Pass-0 context sieve, Stage-2A/2B mutation firewall, ECC Swarm/subagent "
        "model router, and Stop truth-oracle audit."
    ),
    "logo": "assets/logo.svg",
    "suggestedPrompts": [
        "Check live QuadGate daemon health, active classifier driver, and gate telemetry",
        "Audit a proposed Swarm worker roster with QuadGate Gate 3 ECC routing",
        "Run QuadGate doctor and inspect workspace index coverage",
    ],
}

_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="qg-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0F172A"/>
      <stop offset="100%" stop-color="#1E293B"/>
    </linearGradient>
    <linearGradient id="qg-shield" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#2563EB"/>
    </linearGradient>
  </defs>
  <rect x="12" y="12" width="104" height="104" rx="24" fill="url(#qg-bg)" stroke="#38BDF8" stroke-opacity="0.45" stroke-width="3"/>
  <path d="M64 26 L92 38 V63 C92 81 79 94 64 102 C49 94 36 81 36 63 V38 Z" fill="none" stroke="url(#qg-shield)" stroke-width="6" stroke-linejoin="round"/>
  <rect x="47" y="45" width="14" height="14" rx="3" fill="#38BDF8"/>
  <rect x="67" y="45" width="14" height="14" rx="3" fill="#FBBF24"/>
  <rect x="47" y="65" width="14" height="14" rx="3" fill="#34D399"/>
  <rect x="67" y="65" width="14" height="14" rx="3" fill="#F472B6"/>
</svg>
"""

_RULES_AGENTS_MD = """# QuadGate 4-Gate Kernel Rules

When QuadGate hooks and MCP tools are active in Antigravity:

1. **Gate 1 (`[QUADGATE_CONTEXT_HINTS]`):** Prioritize inspecting files listed in `[QUADGATE_CONTEXT_HINTS]` at Turn 1 instead of running broad directory scans. Keep conversation prefixes immutable across turns so KV caches stay warm.
2. **Gate 2 (Security & Containment Firewall):** In default `assistant` firewall mode, solo chats honor your Antigravity `nonWorkspaceFileAccessPolicy` (`allow`/`ask`/`deny`) while using native `"allow"` (localhost), `"ask"` (`.env` and known AI/cloud dev APIs), `"force_ask"` (untrusted outbound secret network calls), and hard `"deny"` for disk wipes, `main`/`master` force-pushes, direct edits to `~/.gemini/config/config.json`, and Swarm `assigned_file_cage` escapes. In `strict` mode (`QUADGATE_FIREWALL_MODE=strict`), out-of-workspace and `.env` operations hard-deny unless `[bypass-gates]` or safe `~/.gemini` subtrees apply.
3. **Gate 3 (Swarm & Subagent Cost-Risk Routing):** Choose Swarm worker models deliberately when calling `swarm(action="add", ...)`:
   - Route mechanical single-file/leaf tasks to `gemini-3.8-flash-low` or `gemini-3.8-flash-high`.
   - Route high-blast-radius, security-sensitive, kernel, or multi-file architecture tasks to `gemini-4-argon-high` or `gemini-4-carbon-high`.
   - Let Gate 3 automatically rewrite `model` via `overwrite` when a task's blast radius or historical failure rate (`R_hist`) warrants promotion or floor downgrade.
4. **Gate 4 (Truth Oracle & Completeness Audit):** Verify syntax/lint checks cleanly and ensure every clause of the user request is addressed before ending turns that perform work.
"""

_SKILL_MD = """---
name: quadgate
description: Inspect, diagnose, calibrate, and operate the QuadGate 4-Gate discriminative control plane daemon, Decision 1 classifier telemetry, and Antigravity/Swarm gating hooks.
---

# QuadGate Control Plane

Use this skill when the user asks to check QuadGate health, inspect live gate stats, verify Swarm model routing (`Gate 3`), troubleshoot a blocked tool call (`Gate 2`), or recalibrate drivers.

## Quick Diagnostics

Run these CLI commands with `run_command`:

```powershell
# Live daemon health, active driver (`decision1` vs fallback), and port
python -m quadgate.cli status --json

# Live request counts, p50 latency, self-healing state, and verdict breakdown
python -m quadgate.cli stats --json

# Full loopback doctor check (<10ms health target + sample Gate 1 eval)
python -m quadgate.cli doctor --json
```

If the daemon is stopped, start it cleanly with your global config:

```powershell
python -m quadgate.cli daemon start --json
```

## Configuration & Data Locations

- Global runtime config: `~/.quadgate/config.toml`
- Live daemon state & auth token: `~/.quadgate/daemon.json`, `~/.quadgate/run.token`
- Active Decision 1 API key env var: `DECISION1_API_KEY` (with automatic fallback to `onnx_local` / `zero_dep` on Tier-1 air-gapped workspaces or offline mode)

## MCP Tools (`quadgate-kernel`)

When enabled, use:
- `quadgate_context_sieve`: Test Pass-0 retrieval & Turn-1 tail hints on a prompt
- `quadgate_verify_mutation`: Pre-flight check a proposed edit, command, or Swarm worker spawn
- `quadgate_audit_completion`: Run Gate 4 syntax/completeness oracle audit before finalizing
"""

_README_MD = """# QuadGate Antigravity Plugin

Global 4-gate control plane integration for **Antigravity 2.0** and native **Swarms**.

## What It Connects

1. **SessionStart**: Ensures the persistent QuadGate daemon (`127.0.0.1:8787`) is running with `decision1` + offline fallback.
2. **PreInvocation (Gate 1)**: Injects compact Pass-0 `[QUADGATE_CONTEXT_HINTS]` on Turn 1 and protects Tier-1 private files.
3. **PreToolUse Firewall (Gate 2)**: Runs in `assistant` mode by default (`[antigravity] firewall_mode = "assistant"` in `~/.quadgate/config.toml`; set `"strict"` or `QUADGATE_FIREWALL_MODE=strict` for untrusted lockdown). Honors `userSettings.nonWorkspaceFileAccessPolicy` for solo chats, keeps Swarm `assigned_file_cage` strict, allows safe `~/.gemini` workflows and localhost calls, prompts via `"ask"` / `"force_ask"` for `.env` / API-key / untrusted external exfil, and hard-denies disk wipes, `main`/`master` force-pushes, and direct edits to `~/.gemini/config/config.json`.
4. **PreToolUse Governor (Gate 3)**: Evaluates `invoke_subagent` and `swarm(action="add", ...)` worker spawns using graph blast radius + historical failure rate (`R_hist`) and routes `model` accordingly.
5. **Stop Audit (Gate 4)**: Verifies truth-oracle checks and prompt completeness on actionable work turns with a strict 1-strike circuit breaker.
6. **MCP Bridge (`quadgate-kernel`)**: Exposes `quadgate_context_sieve`, `quadgate_verify_mutation`, and `quadgate_audit_completion`.

## Installing on Another Machine

1. Copy this `quadgate` plugin directory to `~/.gemini/config/plugins/quadgate/`.
2. Install QuadGate in the same Python environment on `PATH`:
   ```bash
   pip install quadgate   # or: pip install -e /path/to/quadgate
   ```
3. Optional cloud fast-path: set `DECISION1_API_KEY` in your user environment (otherwise QuadGate runs offline using `onnx_local` / `zero_dep`), then reload plugins or start a new Antigravity chat.
"""

_HOOK_SCRIPT_PY = r'''"""Antigravity 2.0 global lifecycle hook runner for QuadGate.

Translates Antigravity `SessionStart`, `PreInvocation`, `PreToolUse`, and `Stop`
camelCase payloads into canonical QuadGate kernel checks and emits strict
Antigravity protojson responses on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_VALID_EVENTS = {"session_start", "pre_invocation", "firewall", "subagent_governor", "stop"}
_MUTATING_TOOLS = {
    "write_file",
    "edit_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "delete_file",
}
_WORK_TOOLS = _MUTATING_TOOLS | {
    "run_command",
    "invoke_subagent",
    "swarm",
    "run_workflow",
    "call_mcp_tool",
    "manage_task",
    "manage_subagents",
    "send_message",
    "post_message",
}
_PATH_ARG_KEYS = ("Path", "path", "TargetFile", "file_path", "AbsolutePath", "TargetPath")
_TALKER_USER_LINE_RE = re.compile(r'^\s*(?:\[[^\]]+\]\s*)?user:\s*"(.*)"\s*$', re.MULTILINE)
_NO_ACTION_PROMPT_RE = re.compile(
    r"\b(?:don'?t|do\s+not)\s+do\s+anything\b|\bdo\s+nothing\b",
    re.IGNORECASE,
)
_CFG_WRITE_CMD_RE = re.compile(
    r"(?:>>?\s*['\"]?[^\s;|&>]*\.gemini[/\\]config[/\\]config\.json"
    r"|\b(?:set-content|out-file|add-content|remove-item|del|rm|erase|tee)\b"
    r"(?:\s+-(?:path|literalpath|filepath))?\s+['\"]?[^\n;|&()]*\.gemini[/\\]config[/\\]config\.json)",
    re.IGNORECASE,
)
_FIREWALL_MODE_RE = re.compile(
    r'^\s*firewall_mode\s*=\s*["\'](strict|assistant)["\']',
    re.MULTILINE | re.IGNORECASE,
)
_URL_HOST_RE = re.compile(
    r"""https?://(\[[0-9a-fA-F:]+\]|[^:/\s"'`;|&<>()]+)""",
    re.IGNORECASE,
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"}
_TRUSTED_DEV_API_HOSTS = {
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "api.openai.com",
    "api.anthropic.com",
    "api.github.com",
}


def _resolve_quadgate_home() -> Path:
    env_home = os.environ.get("QUADGATE_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".quadgate"


def _bootstrap_environment(home_dir: Path) -> None:
    os.environ.setdefault("QUADGATE_HOME", str(home_dir))
    os.environ.setdefault("QUADGATE_SHIM_TIMEOUT_MS", "3500")

    try:
        import quadgate  # noqa: F401
    except ImportError:
        for candidate in (Path("f:/quadgate"), Path.home() / "quadgate"):
            if (candidate / "quadgate" / "__init__.py").is_file():
                cand_str = str(candidate)
                if cand_str not in sys.path:
                    sys.path.insert(0, cand_str)
                break

    if not os.environ.get("DECISION1_API_KEY", "").strip():
        creds_path = home_dir / "credentials.json"
        if creds_path.is_file():
            try:
                creds = json.loads(creds_path.read_text(encoding="utf-8"))
                if isinstance(creds, dict):
                    d1_key = str(creds.get("DECISION1_API_KEY") or "").strip()
                    if d1_key:
                        os.environ["DECISION1_API_KEY"] = d1_key
            except (OSError, ValueError):
                pass


def _connect_host(host: str | None) -> str:
    if not host or host in {"localhost", "0.0.0.0", "::", "::1"}:
        return "127.0.0.1"
    return host


def _read_daemon_state(home_dir: Path) -> dict[str, Any]:
    state_path = Path(os.environ.get("QUADGATE_STATE_PATH") or (home_dir / "daemon.json"))
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                return state
        except (OSError, ValueError):
            pass
    return {}


def _read_daemon_connection(home_dir: Path) -> tuple[str, int, str]:
    host = "127.0.0.1"
    port = 8787
    token_path = str(home_dir / "run.token")
    state = _read_daemon_state(home_dir)
    if state:
        host = _connect_host(str(state.get("host") or host))
        if state.get("port") is not None:
            try:
                port = int(state["port"])
            except (ValueError, TypeError):
                pass
        if isinstance(state.get("token_path"), str) and state["token_path"].strip():
            token_path = state["token_path"].strip()
    return host, port, token_path


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _is_daemon_healthy(host: str, port: int, token_path: str) -> bool:
    token = ""
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip().replace("\r", "").replace("\n", "")
    except OSError:
        token = ""
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection(host, int(port), timeout=0.20)
    try:
        conn.request("GET", "/v1/health", headers=headers)
        resp = conn.getresponse()
        resp.read()
        return int(resp.status) == 200
    except OSError:
        return False
    finally:
        conn.close()


def _ensure_daemon_running(home_dir: Path) -> tuple[str, int, str]:
    host, port, token_path = _read_daemon_connection(home_dir)
    state = _read_daemon_state(home_dir)
    pid_raw = state.get("pid")
    if isinstance(pid_raw, int) and _is_pid_alive(pid_raw):
        return host, port, token_path
    if _is_daemon_healthy(host, port, token_path):
        return host, port, token_path

    cfg_path = home_dir / "config.toml"
    cmd = [sys.executable, "-m", "quadgate.cli", "daemon", "start", "--foreground"]
    if cfg_path.is_file():
        cmd.extend(["--config", str(cfg_path)])

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    repo_fallback = Path("f:/quadgate")
    if repo_fallback.is_dir():
        repo_str = str(repo_fallback)
        if repo_str not in existing_pp.split(os.pathsep):
            env["PYTHONPATH"] = f"{repo_str}{os.pathsep}{existing_pp}" if existing_pp else repo_str

    popen_kwargs: dict[str, Any] = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        base_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        popen_kwargs["creationflags"] = base_flags | 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    else:
        popen_kwargs["start_new_session"] = True

    try:
        try:
            subprocess.Popen(cmd, **popen_kwargs)
        except OSError:
            if os.name == "nt":
                popen_kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
                subprocess.Popen(cmd, **popen_kwargs)
            else:
                raise
        deadline = time.monotonic() + 4.5
        while time.monotonic() < deadline:
            h, p, tp = _read_daemon_connection(home_dir)
            if Path(tp).is_file() and _is_daemon_healthy(h, p, tp):
                return h, p, tp
            time.sleep(0.08)
    except (OSError, subprocess.SubprocessError):
        pass
    return _read_daemon_connection(home_dir)


def _clean_user_prompt(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip()
    if "<talker_agent_context>" in text or "<voice_instructions>" in text:
        utterances = [
            m.group(1).strip()
            for m in _TALKER_USER_LINE_RE.finditer(text)
            if m.group(1).strip()
        ]
        if utterances:
            return utterances[-1]
        cleaned = re.sub(r"<voice_instructions>[\s\S]*?</voice_instructions>", "", text)
        cleaned = re.sub(
            r"</?(?:talker_agent_context|USER_REQUEST|ADDITIONAL_METADATA)>", "", cleaned
        )
        return cleaned.strip()
    return text


def _extract_tool_info(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    tool_call = payload.get("toolCall") or payload.get("tool_call")
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        raw_args = tool_call.get("args") or tool_call.get("arguments") or tool_call.get("input")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        return (str(name).strip() if isinstance(name, str) and name.strip() else None), args
    name = payload.get("tool_name") or payload.get("toolName")
    raw_args = payload.get("tool_args") or payload.get("tool_input") or {}
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    return (str(name).strip() if isinstance(name, str) and name.strip() else None), args


def _gemini_root_dir() -> Path:
    return Path.home() / ".gemini"


def _resolve_plugin_dir() -> Path:
    plugin_root_env = os.environ.get("PLUGIN_ROOT", "").strip()
    if plugin_root_env:
        return Path(plugin_root_env).expanduser()
    return Path(__file__).resolve().parent.parent


def _is_antigravity_app_or_plugin_dir(path_str: str) -> bool:
    raw = str(path_str or "").strip()
    if not raw:
        return True
    p_norm = os.path.normcase(os.path.abspath(os.path.expanduser(raw))).rstrip("\\/")
    plugins_dir = os.path.normcase(
        os.path.abspath(str(_gemini_root_dir() / "config" / "plugins"))
    ).rstrip("\\/")
    if p_norm == plugins_dir or p_norm.startswith(plugins_dir + os.sep):
        return True
    try:
        if (Path(raw) / "Antigravity.exe").is_file() or (Path(raw) / "Antigravity").is_file():
            return True
    except OSError:
        pass
    segs = [seg for seg in p_norm.replace("\\", "/").split("/") if seg]
    parent_roots = {"programs", "program files", "program files (x86)", "appdata", "applications"}
    if "antigravity" in segs and any(root in segs for root in parent_roots):
        return True
    return False


def _is_non_workspace_default_dir(path_str: str) -> bool:
    if _is_antigravity_app_or_plugin_dir(path_str):
        return True
    p_norm = os.path.normcase(os.path.abspath(os.path.expanduser(str(path_str).strip()))).rstrip("\\/")
    home_norm = os.path.normcase(os.path.abspath(str(Path.home()))).rstrip("\\/")
    gem_norm = os.path.normcase(os.path.abspath(str(_gemini_root_dir()))).rstrip("\\/")
    sys_dirs = {
        home_norm,
        gem_norm,
        "/",
        os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")).rstrip("\\/"),
        os.path.normcase(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")),
    }
    return p_norm in sys_dirs or p_norm.startswith(gem_norm + os.sep)


def _resolve_active_workspace(payload: dict[str, Any]) -> str | None:
    for key in ("workspace_root", "workspaceRoot"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip() and not _is_antigravity_app_or_plugin_dir(val):
            return val.strip()

    if "workspacePaths" in payload:
        ws_paths = payload.get("workspacePaths")
        if isinstance(ws_paths, list):
            for entry in ws_paths:
                text = str(entry).strip() if isinstance(entry, (str, Path)) else ""
                if text and not _is_antigravity_app_or_plugin_dir(text):
                    return text
        return None

    for key in ("cwd",):
        val = payload.get(key)
        if isinstance(val, str) and val.strip() and not _is_non_workspace_default_dir(val):
            return val.strip()
    env_ws = os.environ.get("QUADGATE_WORKSPACE_ROOT", "").strip()
    if env_ws and not _is_non_workspace_default_dir(env_ws):
        return env_ws
    return None


def _is_plugin_disabled_live(home_dir: Path) -> bool:
    if os.environ.get("QUADGATE_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        if (home_dir / "disabled").exists():
            return True
    except OSError:
        pass

    plugin_dir = _resolve_plugin_dir()
    plugin_dir_name = plugin_dir.name
    explicit_config_enabled: bool | None = None

    cfg_path = _gemini_root_dir() / "config" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        if isinstance(cfg, dict):
            disabled_list = cfg.get("disabledPlugins")
            if isinstance(disabled_list, list) and (
                plugin_dir_name in disabled_list or "quadgate" in disabled_list
            ):
                return True
            plugins_map = cfg.get("plugins")
            if isinstance(plugins_map, dict):
                for key in (plugin_dir_name, "quadgate"):
                    if key not in plugins_map:
                        continue
                    entry = plugins_map[key]
                    if entry is False:
                        return True
                    if isinstance(entry, dict):
                        if entry.get("enabled") is False or entry.get("disabled") is True:
                            return True
                        if entry.get("enabled") is True or entry.get("disabled") is False:
                            explicit_config_enabled = True
                    break

    hooks_path = plugin_dir / "hooks.json"
    if hooks_path.is_file():
        try:
            hooks_cfg = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hooks_cfg = {}
        if isinstance(hooks_cfg, dict):
            if hooks_cfg.get("enabled") is False:
                return True
            qg_block = hooks_cfg.get("quadgate")
            if isinstance(qg_block, dict) and qg_block.get("enabled") is False:
                return True

    if explicit_config_enabled is True:
        return False

    manifest_path = plugin_dir / "plugin.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        if isinstance(manifest, dict) and (
            manifest.get("disabled") is True or manifest.get("enabled") is False
        ):
            return True
    return False


def _targets_protected_gemini_config_write(
    payload: dict[str, Any],
    *,
    is_direct_mutation: bool,
) -> bool:
    from quadgate.shim import _fast_realpath, _extract_tool_inputs, _resolve_within_workspace

    paths, commands, _ = _extract_tool_inputs(payload)
    cfg_path = _gemini_root_dir() / "config" / "config.json"
    cfg_real = os.path.normcase(_fast_realpath(str(cfg_path)))
    gem_base = _fast_realpath(str(_gemini_root_dir()))
    gem_norm = os.path.normcase(gem_base)

    if is_direct_mutation:
        for raw_p in paths:
            inside_gem, resolved_gem = _resolve_within_workspace(raw_p, gem_base, gem_norm)
            if inside_gem and os.path.normcase(resolved_gem) == cfg_real:
                return True

    for cmd in commands:
        if _CFG_WRITE_CMD_RE.search(cmd):
            return True
    return False


def _is_safe_antigravity_internal_path(
    raw_path: str,
    workspace_root: str,
    *,
    is_direct_mutation: bool = True,
) -> bool:
    """Return True if `raw_path` resolves inside `workspace_root` OR safe `~/.gemini` subtrees."""
    from quadgate.shim import (
        _fast_realpath,
        _resolve_within_workspace,
        _resolved_has_forbidden_env,
    )

    ws_base = _fast_realpath(os.path.expanduser(workspace_root or "."))
    ws_norm = os.path.normcase(ws_base)
    inside_ws, resolved_ws = _resolve_within_workspace(raw_path, ws_base, ws_norm)
    if _resolved_has_forbidden_env(resolved_ws, ws_base, inside_ws):
        return False
    if inside_ws:
        return True

    gemini_dir = _gemini_root_dir()
    gem_base = _fast_realpath(str(gemini_dir))
    gem_norm = os.path.normcase(gem_base)
    inside_gem, resolved_gem = _resolve_within_workspace(raw_path, gem_base, gem_norm)
    if not inside_gem:
        return False

    resolved_norm = os.path.normcase(resolved_gem)
    base_name = os.path.basename(resolved_norm)
    if base_name == ".env" or (
        base_name.startswith(".env.")
        and base_name not in {".env.example", ".env.sample", ".env.template"}
    ):
        return False
    if base_name in {"id_rsa", "id_ed25519"}:
        return False

    if is_direct_mutation:
        protected_cfg = os.path.normcase(_fast_realpath(str(gemini_dir / "config" / "config.json")))
        if resolved_norm == protected_cfg:
            return False
        if base_name == "credentials.json" or base_name.endswith((".pem", ".key")):
            return False
    return True


def _resolve_firewall_mode(home_dir: Path) -> str:
    env_mode = os.environ.get("QUADGATE_FIREWALL_MODE", "").strip().lower()
    if env_mode in {"strict", "assistant"}:
        return env_mode
    cfg_path = home_dir / "config.toml"
    if cfg_path.is_file():
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = _FIREWALL_MODE_RE.search(text)
        if m:
            return m.group(1).lower()
    return "assistant"


def _read_non_workspace_policy() -> str:
    cfg_path = _gemini_root_dir() / "config" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        if isinstance(cfg, dict):
            user_settings = cfg.get("userSettings")
            if isinstance(user_settings, dict):
                pol = str(user_settings.get("nonWorkspaceFileAccessPolicy") or "").upper()
                if "DENY" in pol:
                    return "deny"
                if "ASK" in pol:
                    return "ask"
    return "allow"


def _violates_assigned_cage(
    payload: dict[str, Any],
    workspace_root: str,
    *,
    is_direct_mutation: bool,
) -> str | None:
    raw_cage = payload.get("assigned_file_cage")
    if not is_direct_mutation or not isinstance(raw_cage, list) or not raw_cage:
        return None
    from quadgate.shim import _extract_tool_inputs, _fast_realpath, _resolve_within_workspace

    paths, _, _ = _extract_tool_inputs(payload)
    if not paths:
        return None

    ws_base = _fast_realpath(os.path.expanduser(workspace_root or "."))
    ws_norm = os.path.normcase(ws_base)
    cage_norms: list[str] = []
    for item in raw_cage:
        if not isinstance(item, str) or not item.strip():
            continue
        expanded = os.path.expanduser(item.strip())
        if os.path.isabs(expanded) or bool(os.path.splitdrive(expanded)[0]):
            cage_abs = _fast_realpath(expanded)
        else:
            cage_abs = _fast_realpath(os.path.join(ws_base, expanded))
        cage_norms.append(os.path.normcase(cage_abs))

    if not cage_norms:
        return None

    for raw_p in paths:
        _, resolved_p = _resolve_within_workspace(raw_p, ws_base, ws_norm)
        resolved_norm = os.path.normcase(resolved_p)
        in_any = False
        for c_norm in cage_norms:
            try:
                if os.path.commonpath([c_norm, resolved_norm]) == c_norm:
                    in_any = True
                    break
            except ValueError:
                continue
        if not in_any:
            return raw_p
    return None


def _classify_assistant_secret_decision(
    payload: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    from quadgate.shim import (
        _ensure_stage2a_compiled,
        _extract_tool_inputs,
        _path_has_forbidden_env,
    )

    paths, commands, _ = _extract_tool_inputs(payload)
    s2a = _ensure_stage2a_compiled()
    rule_id = str(receipt.get("rule_id") or "INV_01_SECRET_EXPOSURE")
    remediation = str(
        receipt.get("remediation") or "Tool accesses secret credentials or .env files."
    )

    hosts: list[str] = []
    for cmd in commands:
        for m in _URL_HOST_RE.finditer(cmd):
            hosts.append(m.group(1).lower())

    has_outbound_cmd = any(
        bool(s2a.outbound_http_re.search(c) or s2a.pipe_env_exfil_re.search(c)) for c in commands
    )
    has_pipe_exfil = any(bool(s2a.pipe_env_exfil_re.search(c)) for c in commands)
    touches_env_or_ssh = any(_path_has_forbidden_env(p) for p in paths) or any(
        bool(s2a.wildcard_env_re.search(c))
        or ".env" in c.lower()
        or ".ssh" in c.lower()
        or "id_rsa" in c.lower()
        or "id_ed25519" in c.lower()
        for c in commands
    )

    if (
        commands
        and hosts
        and all(h in _LOCAL_HOSTS for h in hosts)
        and not has_pipe_exfil
        and not touches_env_or_ssh
        and not paths
    ):
        return {"decision": "allow"}

    if (
        has_pipe_exfil
        or (has_outbound_cmd and not hosts)
        or any(h not in (_LOCAL_HOSTS | _TRUSTED_DEV_API_HOSTS) for h in hosts)
    ):
        return {
            "decision": "force_ask",
            "reason": (
                f"QuadGate Security Check ({rule_id}): Outbound network command references "
                f"secrets or .env data. {remediation}"
            ),
        }

    return {
        "decision": "ask",
        "reason": f"QuadGate ({rule_id}): Confirm .env or API key access. {remediation}",
    }


def _tokenize_shell_preserving_quotes(seg: str) -> list[str]:
    tokens: list[str] = []
    n = len(seg)
    i = 0
    sep_chars = set(" \t\r\n,;|&<>()")
    while i < n:
        while i < n and seg[i] in sep_chars:
            i += 1
        if i >= n:
            break
        buf: list[str] = []
        had_quoted_cmd = False
        while i < n and seg[i] not in sep_chars:
            ch = seg[i]
            if ch == "'":
                j = seg.find("'", i + 1)
                if j == -1:
                    buf.append(seg[i + 1 :])
                    i = n
                else:
                    buf.append(seg[i + 1 : j])
                    i = j + 1
            elif ch == '"':
                j = seg.find('"', i + 1)
                if j == -1:
                    inner = seg[i + 1 :]
                    i = n
                else:
                    inner = seg[i + 1 : j]
                    i = j + 1
                if (
                    not buf
                    and (i == n or seg[i] in sep_chars)
                    and (
                        "'" in inner
                        or bool(re.search(r"\s(?:[A-Za-z]:[/\\]|~[/\\]|--?[A-Za-z])", inner))
                    )
                ):
                    tokens.extend(_tokenize_shell_preserving_quotes(inner))
                    had_quoted_cmd = True
                    break
                buf.append(inner)
            else:
                buf.append(ch)
                i += 1
        if not had_quoted_cmd and buf:
            tokens.append("".join(buf))
    return tokens


def _all_outside_paths_are_safe_antigravity(
    payload: dict[str, Any],
    workspace_root: str,
    *,
    is_direct_mutation: bool = False,
) -> bool:
    """Verify every non-workspace path/command token sits strictly inside safe `~/.gemini`."""
    from quadgate.shim import (
        _CMD_POS0_TOOLCHAIN,
        _SAFE_DEV_PATHS,
        _ensure_stage2a_compiled,
        _extract_tool_inputs,
        _unwrap_shell_subcommands,
    )

    paths, commands, _ = _extract_tool_inputs(payload)
    for raw_p in paths:
        if not _is_safe_antigravity_internal_path(
            raw_p, workspace_root, is_direct_mutation=is_direct_mutation
        ):
            return False

    s2a = _ensure_stage2a_compiled()
    for cmd in commands:
        if _CFG_WRITE_CMD_RE.search(cmd):
            return False
        for seg in _unwrap_shell_subcommands(cmd):
            raw_tokens = _tokenize_shell_preserving_quotes(seg)
            for idx, raw_tok in enumerate(raw_tokens):
                tok = raw_tok.strip("\"'")
                if not tok or "://" in tok:
                    continue
                cand = (
                    tok.split("=", 1)[1].strip("\"'").lstrip("@")
                    if ("=" in tok and not tok.startswith("="))
                    else tok.lstrip("@")
                )
                cand = cand.strip("()\"';,")
                if not cand or "://" in cand or cand in _SAFE_DEV_PATHS:
                    continue
                is_dotdot = (
                    cand == ".."
                    or cand.startswith(("../", "..\\"))
                    or "/../" in cand
                    or "\\..\\" in cand
                    or cand.endswith(("/..", "\\.."))
                )
                is_tilde = cand == "~" or cand.startswith(("~/", "~\\"))
                is_drive = bool(s2a.drive_path_re.match(cand))
                is_unix_abs = bool(s2a.unix_sensitive_abs_re.match(cand))
                if not is_unix_abs and os.sep == "/" and s2a.unix_two_seg_abs_re.match(cand):
                    first_seg = cand.split("/", 2)[1]
                    if first_seg and os.path.exists("/" + first_seg):
                        is_unix_abs = True
                if is_dotdot or is_tilde or is_drive or is_unix_abs:
                    if idx == 0 and (is_drive or is_unix_abs) and not is_dotdot:
                        base = os.path.basename(cand.replace("\\", "/")).lower()
                        if base in _CMD_POS0_TOOLCHAIN:
                            continue
                    if not _is_safe_antigravity_internal_path(
                        cand, workspace_root, is_direct_mutation=False
                    ):
                        return False
    return bool(paths or commands)


def _read_transcript_records(transcript_path: str | None) -> list[dict[str, Any]]:
    if not transcript_path:
        return []
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - 196_608)
            fh.seek(start)
            raw_bytes = fh.read()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        if start > 0 and "\n" in raw_text:
            raw_text = raw_text.split("\n", 1)[1]
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        try:
            obj = json.loads(line_s)
        except ValueError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _extract_turn_slice(
    records: list[dict[str, Any]],
) -> tuple[str | None, list[str], list[str], list[str], bool]:
    last_user_idx = -1
    prompt_from_transcript: str | None = None
    for idx, rec in enumerate(records):
        if str(rec.get("type") or "").upper() == "USER_INPUT":
            last_user_idx = idx
            cleaned = _clean_user_prompt(str(rec.get("content") or ""))
            if cleaned:
                prompt_from_transcript = cleaned

    turn_records = records[last_user_idx + 1 :] if last_user_idx >= 0 else records
    touched: list[str] = []
    seen_touched: set[str] = set()
    work_tools: list[str] = []
    snippets: list[str] = []
    last_tool_idx = -1
    last_planner_text_idx = -1

    for pos, rec in enumerate(turn_records):
        rec_type = str(rec.get("type") or "").upper()
        if rec_type == "PLANNER_RESPONSE":
            content = rec.get("content")
            if isinstance(content, str) and content.strip():
                snippets.append(content.strip()[:2500])
                last_planner_text_idx = pos
        tool_calls = rec.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tname = str(tc.get("name") or "").strip()
                if tname in _WORK_TOOLS:
                    work_tools.append(tname)
                    last_tool_idx = pos
                targs = tc.get("arguments") or tc.get("args") or {}
                if tname in _MUTATING_TOOLS and isinstance(targs, dict):
                    for key in _PATH_ARG_KEYS:
                        val = targs.get(key)
                        if isinstance(val, str) and val.strip():
                            p_clean = val.strip()
                            if p_clean not in seen_touched:
                                seen_touched.add(p_clean)
                                touched.append(p_clean)
                            break

    has_trailing_reply = last_planner_text_idx >= 0 and last_planner_text_idx >= last_tool_idx
    return prompt_from_transcript, touched, work_tools, snippets, has_trailing_reply


def _collect_transcript_turn_context(
    transcript_path: str | None,
) -> tuple[str | None, list[str], list[str], str | None, bool]:
    records = _read_transcript_records(transcript_path)
    if not records:
        return None, [], [], None, False

    prompt, touched, work_tools, snippets, has_trailing_reply = _extract_turn_slice(records)
    if work_tools and not has_trailing_reply:
        # Give transcript writer 60ms to flush the final planner response on tool turns.
        time.sleep(0.06)
        records = _read_transcript_records(transcript_path)
        prompt, touched, work_tools, snippets, has_trailing_reply = _extract_turn_slice(records)

    evidence = "\n\n".join(snippets[-2:]) if snippets else None
    return prompt, touched, work_tools, evidence, has_trailing_reply


def _strike_key(session_id: str, prompt_text: str) -> str:
    digest = hashlib.sha1(prompt_text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{session_id}:{digest}"


def _has_stop_strike(home_dir: Path, key: str) -> bool:
    strike_file = home_dir / "stop_strikes.json"
    if not strike_file.is_file():
        return False
    try:
        data = json.loads(strike_file.read_text(encoding="utf-8"))
        return isinstance(data, dict) and key in data
    except (OSError, ValueError):
        return False


def _record_stop_strike(home_dir: Path, key: str) -> None:
    strike_file = home_dir / "stop_strikes.json"
    data: dict[str, float] = {}
    if strike_file.is_file():
        try:
            loaded = json.loads(strike_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = {str(k): float(v) for k, v in loaded.items() if isinstance(v, (int, float))}
        except (OSError, ValueError):
            data = {}
    data[key] = time.time()
    if len(data) > 200:
        newest = sorted(data.items(), key=lambda kv: kv[1])[-200:]
        data = dict(newest)
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        strike_file.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _forward(event_payload: dict[str, Any], home_dir: Path) -> dict[str, Any] | None:
    from quadgate.shim import forward_to_daemon

    host, port, token_path = _read_daemon_connection(home_dir)
    if not Path(token_path).is_file():
        return None
    return forward_to_daemon(
        event_payload,
        timeout_s=4.0,
        host=host,
        port=port,
        token_path=token_path,
    )


_MODEL_SPLIT_RE = re.compile(r"[-_./\s]+")
_DIFF_ARG_KEYS = ("diff_text", "CodeContent", "ReplacementContent", "Replacement", "diff", "patch", "content", "new_string")


def _infer_tier_stdlib(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    norm = value.strip().lower().replace("-", "_")
    if not norm or norm == "inherit":
        return None
    direct = {
        "floor": "floor",
        "flash_lite": "floor",
        "mid": "mid",
        "flash": "mid",
        "frontier": "frontier",
        "pro": "frontier",
    }
    if norm in direct:
        return direct[norm]
    tokens = {tok for tok in _MODEL_SPLIT_RE.split(norm) if tok}
    if "flash" in tokens and ("lite" in tokens or "low" in tokens):
        return "floor"
    if tokens & {"haiku", "mini", "nano"}:
        return "floor"
    if tokens & {"flash", "sonnet"} or norm == "gpt_oss_120b_medium":
        return "mid"
    if tokens & {"pro", "opus", "argon", "carbon", "hydrogen", "orchestrator"}:
        return "frontier"
    return None


def _build_gate_event_dict(gate_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    from quadgate.shim import _normalize_to_gate_event

    ev = _normalize_to_gate_event("antigravity", gate_name, payload)
    tool_args = ev.get("tool_args") if isinstance(ev.get("tool_args"), dict) else {}

    touched = list(ev.get("touched_files") or [])
    seen = set(touched)
    for key in ("touchedFiles", "touched_files"):
        raw_list = payload.get(key)
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, str) and item.strip() and item.strip() not in seen:
                    seen.add(item.strip())
                    touched.append(item.strip())
    if gate_name in {"firewall", "subagent_governor"}:
        for key in _PATH_ARG_KEYS:
            val = tool_args.get(key)
            if isinstance(val, str) and val.strip() and val.strip() not in seen:
                seen.add(val.strip())
                touched.append(val.strip())
    ev["touched_files"] = touched

    if not ev.get("diff_text"):
        diff_val = payload.get("diffText")
        if isinstance(diff_val, str) and diff_val:
            ev["diff_text"] = diff_val
        elif gate_name in {"firewall", "subagent_governor"}:
            for key in _DIFF_ARG_KEYS:
                d_arg = tool_args.get(key)
                if isinstance(d_arg, str) and d_arg.strip():
                    ev["diff_text"] = d_arg
                    break

    if payload.get("continuationActive") is True or payload.get("continuation_active") is True:
        ev["continuation_active"] = True

    meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    if gate_name == "subagent_governor":
        if not ev.get("prompt_text"):
            for p_key in ("Prompt", "prompt", "brief", "Task", "task", "description"):
                p_val = tool_args.get(p_key)
                if isinstance(p_val, str) and p_val.strip():
                    ev["prompt_text"] = p_val
                    break
        if ev.get("tool_name") == "swarm" and isinstance(tool_args.get("tasks"), list):
            tasks = [str(t).strip() for t in tool_args["tasks"] if isinstance(t, str) and t.strip()]
            if tasks:
                cur_p = str(ev.get("prompt_text") or "")
                ev["prompt_text"] = f"{cur_p}\nTasks: {'; '.join(tasks)}" if cur_p else f"Tasks: {'; '.join(tasks)}"
        sub_model = tool_args.get("Model") or tool_args.get("model")
        if isinstance(sub_model, str) and sub_model.strip():
            meta["subagent_model"] = sub_model.strip()
            if ev.get("current_tier") is None:
                ev["current_tier"] = _infer_tier_stdlib(sub_model)
    elif gate_name == "stop_audit" and ev.get("current_tier") is None:
        raw_tier = payload.get("currentTier") or payload.get("modelName") or payload.get("model_name")
        ev["current_tier"] = _infer_tier_stdlib(raw_tier)
    ev["metadata"] = meta
    return ev


def _format_block_reason_dict(
    verdict: dict[str, Any],
    default_msg: str = "Operation blocked by QuadGate policy.",
) -> str:
    receipt = verdict.get("violation_receipt")
    if not isinstance(receipt, dict):
        reason = verdict.get("reason")
        return str(reason).strip() if isinstance(reason, str) and reason.strip() else default_msg

    rule_id = str(receipt.get("rule_id") or "QUADGATE_BLOCK").strip()
    remediation = str(receipt.get("remediation") or default_msg).strip()
    parts = [remediation if remediation.startswith(f"{rule_id}:") else f"{rule_id}: {remediation}"]
    violated_path = receipt.get("violated_path")
    if isinstance(violated_path, str) and violated_path and violated_path not in parts[0]:
        parts.append(f"(path: {violated_path})")
    bypass = receipt.get("bypass_instruction")
    if isinstance(bypass, str) and bypass.strip() and bypass.strip() not in parts[0]:
        parts.append(bypass.strip())
    return " ".join(parts)


def _format_stop_reason_dict(verdict: dict[str, Any]) -> str:
    parts: list[str] = []
    receipt = verdict.get("violation_receipt")
    reason = verdict.get("reason")
    reason_clean = str(reason).strip() if isinstance(reason, str) else ""
    if isinstance(receipt, dict):
        blk = _format_block_reason_dict(verdict)
        parts.append(blk)
        rem = str(receipt.get("remediation") or "")
        if reason_clean and reason_clean not in blk and (not rem or rem not in reason_clean):
            parts.append(reason_clean)
    elif reason_clean:
        parts.append(reason_clean)

    raw_clauses = verdict.get("unaddressed_clauses")
    clauses = [str(c).strip() for c in raw_clauses if isinstance(c, str) and c.strip()] if isinstance(raw_clauses, list) else []
    if clauses:
        joined = " ".join(parts)
        missing = [c for c in clauses if c not in joined]
        if missing:
            parts.append("Unaddressed clauses: " + "; ".join(missing))

    if parts:
        return " ".join(parts)
    if str(verdict.get("action") or "").strip().lower() == "promote":
        return "Promote next turn to higher tier and re-verify completion."
    return "Continue until all requested clauses and checks pass."


def _format_protojson_dict(
    hook_event_name: str,
    verdict: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> dict[str, Any]:
    action = str(verdict.get("action") or verdict.get("verdict") or "allow").strip().lower()
    if hook_event_name == "PreInvocation":
        if action == "block":
            return {
                "injectSteps": [{"errorMessage": {"userMessage": _format_block_reason_dict(verdict)}}],
                "terminationBehavior": "terminate",
            }
        if action == "augment":
            parts: list[str] = []
            reason = verdict.get("reason")
            if isinstance(reason, str) and reason.strip():
                parts.append(reason.strip())
            tail = verdict.get("tail_hints")
            if isinstance(tail, list):
                lines = [f"- {h.strip()}" for h in tail if isinstance(h, str) and h.strip()]
                if lines:
                    parts.append("[QUADGATE_CONTEXT_HINTS]\n" + "\n".join(lines))
            text = "\n".join(parts)
            return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]} if text else {}
        return {}

    if hook_event_name == "PreToolUse":
        if action in {"block", "promote"}:
            reason_val = verdict.get("reason")
            msg = (
                _format_block_reason_dict(verdict)
                if action == "block"
                else (str(reason_val).strip() if isinstance(reason_val, str) and reason_val.strip() else "Promote subagent tier.")
            )
            return {"decision": "deny", "reason": msg}
        if action == "modify":
            overwrite_args = dict(verdict.get("updated_tool_args") or {}) if isinstance(verdict.get("updated_tool_args"), dict) else {}
            model_key = "model" if tool_name == "swarm" else "Model"
            upd_model = verdict.get("updated_model")
            if isinstance(upd_model, str) and upd_model:
                overwrite_args[model_key] = upd_model
            if tool_name == "swarm":
                overwrite_args.pop("Model", None)
            out: dict[str, Any] = {"decision": "allow", "overwrite": overwrite_args}
            reason_val = verdict.get("reason")
            if isinstance(reason_val, str) and reason_val.strip():
                out["reason"] = reason_val.strip()
            return out
        return {"decision": "allow"}

    if hook_event_name == "Stop":
        clauses = verdict.get("unaddressed_clauses")
        has_clauses = isinstance(clauses, list) and any(isinstance(c, str) and c.strip() for c in clauses)
        if action in {"block", "promote"} or (action == "augment" and has_clauses):
            return {"decision": "continue", "reason": _format_stop_reason_dict(verdict)}
        return {}

    return {}


def _handle_event(event_name: str, payload: dict[str, Any], home_dir: Path) -> dict[str, Any]:
    active_ws = _resolve_active_workspace(payload)
    has_cage = bool(payload.get("assigned_file_cage"))

    if event_name == "session_start":
        if active_ws is None:
            return {}
        _ensure_daemon_running(home_dir)
        return {}

    from quadgate.shim import _has_direct_tool_fields, check_stage2a_deterministic_security

    if event_name == "pre_invocation":
        if active_ws is None:
            return {}
        cleaned_prompt = _clean_user_prompt(
            str(
                payload.get("lastUserInput")
                or payload.get("prompt_text")
                or payload.get("prompt")
                or ""
            )
        )
        if not cleaned_prompt:
            return {}
        work_payload = dict(payload)
        work_payload["workspace_root"] = active_ws
        work_payload["lastUserInput"] = cleaned_prompt
        eval_payload = _build_gate_event_dict("intake", work_payload)
        raw_verdict = _forward(eval_payload, home_dir)
        if not raw_verdict:
            return {}
        return _format_protojson_dict("PreInvocation", raw_verdict)

    if event_name == "firewall":
        tool_name, _ = _extract_tool_info(payload)
        is_direct_mutation = bool(tool_name in _MUTATING_TOOLS)

        if _targets_protected_gemini_config_write(payload, is_direct_mutation=is_direct_mutation):
            return {
                "decision": "deny",
                "reason": "INV_02_CAGE_CONTAINMENT: Direct edits to ~/.gemini/config/config.json are blocked.",
            }

        if has_cage:
            cage_violation = _violates_assigned_cage(
                payload,
                str(active_ws or "."),
                is_direct_mutation=is_direct_mutation,
            )
            if cage_violation is not None:
                return {
                    "decision": "deny",
                    "reason": (
                        f"INV_02_CAGE_CONTAINMENT: Target {cage_violation!r} is outside "
                        "assigned Swarm file cage."
                    ),
                }

        fw_mode = _resolve_firewall_mode(home_dir)
        work_payload = dict(payload)
        if active_ws is not None:
            work_payload["workspace_root"] = active_ws
        eval_payload = _build_gate_event_dict("firewall", work_payload)
        ws_root = str(active_ws or eval_payload.get("workspace_root") or ".")
        has_bypass = bool(eval_payload.get("bypass_gates")) or (
            "[bypass-gates]" in str(payload.get("lastUserInput") or "").lower()
        )
        if bool(eval_payload.get("tool_args")) or _has_direct_tool_fields(work_payload):
            receipt = check_stage2a_deterministic_security(work_payload, ws_root)
            if receipt is not None:
                rule_id = str(receipt.get("rule_id") or "STAGE2A_SECURITY_BLOCK")
                remediation = str(
                    receipt.get("remediation")
                    or "Blocked by QuadGate deterministic security."
                )
                if rule_id in {"STAGE2A_DESTRUCTIVE_COMMAND", "STAGE2A_FORCE_PUSH_MAIN"}:
                    return {"decision": "deny", "reason": f"{rule_id}: {remediation}"}
                if rule_id == "INV_02_CAGE_CONTAINMENT":
                    if active_ws is None and not has_cage:
                        return {"decision": "allow"}
                    if has_cage:
                        return {"decision": "deny", "reason": f"{rule_id}: {remediation}"}
                    if _all_outside_paths_are_safe_antigravity(
                        work_payload, ws_root, is_direct_mutation=is_direct_mutation
                    ) or has_bypass:
                        return {"decision": "allow"}
                    if fw_mode == "assistant":
                        pol = _read_non_workspace_policy()
                        if pol == "allow":
                            return {"decision": "allow"}
                        if pol == "ask":
                            return {
                                "decision": "ask",
                                "reason": f"QuadGate ({rule_id}): Confirm access outside workspace ({remediation})",
                            }
                    return {"decision": "deny", "reason": f"{rule_id}: {remediation}"}
                if rule_id == "INV_01_SECRET_EXPOSURE":
                    if fw_mode == "assistant":
                        return _classify_assistant_secret_decision(
                            work_payload if active_ws is not None else payload,
                            receipt,
                        )
                    return {"decision": "deny", "reason": f"{rule_id}: {remediation}"}
                return {"decision": "deny", "reason": f"{rule_id}: {remediation}"}

        if active_ws is None and not has_cage:
            return {"decision": "allow"}

        raw_verdict = _forward(eval_payload, home_dir)
        if not raw_verdict:
            return {"decision": "allow"}
        if (
            fw_mode == "assistant"
            and not has_cage
            and str(raw_verdict.get("action") or "").lower() == "block"
        ):
            v_rec = raw_verdict.get("violation_receipt")
            if isinstance(v_rec, dict) and (
                v_rec.get("escalate_to_human") is True or int(v_rec.get("strike_count") or 0) >= 2
            ):
                return {"decision": "ask", "reason": _format_block_reason_dict(raw_verdict)}
        t_name = eval_payload.get("tool_name")
        return _format_protojson_dict(
            "PreToolUse",
            raw_verdict,
            tool_name=str(t_name) if isinstance(t_name, str) else None,
        )

    if event_name == "subagent_governor":
        if active_ws is None and not has_cage:
            return {"decision": "allow"}
        tool_name, tool_args = _extract_tool_info(payload)
        if tool_name == "swarm":
            action_val = str(tool_args.get("action") or "").strip().lower()
            if action_val != "add":
                return {"decision": "allow"}
        if tool_name == "invoke_subagent":
            type_val = str(tool_args.get("TypeName") or "").strip().lower()
            if type_val == "browser":
                return {"decision": "allow"}

        work_payload = dict(payload)
        if active_ws is not None:
            work_payload["workspace_root"] = active_ws
        eval_payload = _build_gate_event_dict("subagent_governor", work_payload)
        raw_verdict = _forward(eval_payload, home_dir)
        if not raw_verdict:
            return {"decision": "allow"}
        t_name = eval_payload.get("tool_name")
        resolved_tname = str(t_name) if isinstance(t_name, str) else tool_name
        proto = _format_protojson_dict("PreToolUse", raw_verdict, tool_name=resolved_tname)
        if proto.get("decision") == "allow" and isinstance(proto.get("overwrite"), dict):
            model_key = "model" if resolved_tname == "swarm" else "Model"
            orig_model = tool_args.get(model_key)
            new_model = proto["overwrite"].get(model_key)
            if isinstance(new_model, str) and new_model and new_model != orig_model:
                proto["overwrite"] = {model_key: new_model}
            else:
                proto.pop("overwrite", None)
                proto.pop("reason", None)
        return proto

    if event_name == "stop":
        if active_ws is None and not has_cage:
            return {}
        if payload.get("continuationActive") is True:
            return {}
        try:
            exec_num = int(payload.get("executionNum") or 0)
        except (TypeError, ValueError):
            exec_num = 0
        if exec_num > 1:
            return {}

        term_reason = str(payload.get("terminationReason") or "NO_TOOL_CALL").strip()
        if term_reason not in {"", "NO_TOOL_CALL", "end_turn"}:
            return {}

        cleaned_prompt = _clean_user_prompt(
            str(payload.get("lastUserInput") or payload.get("prompt_text") or "")
        )
        transcript_path = (
            payload.get("transcriptPath")
            if isinstance(payload.get("transcriptPath"), str)
            else None
        )
        tr_prompt, tr_touched, tr_work_tools, tr_evidence, has_trailing_reply = (
            _collect_transcript_turn_context(transcript_path)
        )
        if not cleaned_prompt and tr_prompt:
            cleaned_prompt = tr_prompt
        if not cleaned_prompt or _NO_ACTION_PROMPT_RE.search(cleaned_prompt):
            return {}

        session_id = str(payload.get("conversationId") or payload.get("session_id") or "ag-session")
        s_key = _strike_key(session_id, cleaned_prompt)
        if _has_stop_strike(home_dir, s_key):
            return {}

        work_payload = dict(payload)
        if active_ws is not None:
            work_payload["workspace_root"] = active_ws
        work_payload["lastUserInput"] = cleaned_prompt

        explicit_touched = payload.get("touchedFiles") or payload.get("touched_files")
        merged_touched: list[str] = []
        if isinstance(explicit_touched, list):
            for entry in explicit_touched:
                if isinstance(entry, str) and entry.strip() and entry.strip() not in merged_touched:
                    merged_touched.append(entry.strip())
        for entry in tr_touched:
            if entry not in merged_touched:
                merged_touched.append(entry)
        if merged_touched:
            work_payload["touchedFiles"] = merged_touched

        explicit_work = payload.get("workToolsExecuted")
        all_work_tools = list(tr_work_tools)
        if isinstance(explicit_work, list):
            all_work_tools.extend(str(x) for x in explicit_work if isinstance(x, str) and x.strip())

        explicit_diff = payload.get("diffText") or payload.get("diff_text")
        has_explicit_diff = isinstance(explicit_diff, str) and bool(explicit_diff.strip())

        if not merged_touched and not has_explicit_diff and not all_work_tools:
            return {}

        if not has_explicit_diff:
            evidence_parts: list[str] = []
            if all_work_tools:
                evidence_parts.append("Executed tools: " + ", ".join(dict.fromkeys(all_work_tools)))
            if tr_evidence:
                evidence_parts.append(tr_evidence)
            if not merged_touched and not has_trailing_reply and tr_work_tools:
                return {}
            if evidence_parts:
                work_payload["diffText"] = "\n\n".join(evidence_parts)

        has_diff = isinstance(work_payload.get("diffText"), str) and bool(
            work_payload["diffText"].strip()
        )
        if not merged_touched and not has_diff:
            return {}

        eval_payload = _build_gate_event_dict("stop_audit", work_payload)
        raw_verdict = _forward(eval_payload, home_dir)
        if not raw_verdict:
            return {}
        proto = _format_protojson_dict("Stop", raw_verdict)
        if proto.get("decision") == "continue":
            _record_stop_strike(home_dir, s_key)
        return proto

    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--event", required=True)
    ns, _ = parser.parse_known_args(argv)
    event_name = str(ns.event).strip().lower()
    fallback = {"decision": "allow"} if event_name in {"firewall", "subagent_governor"} else {}

    if event_name not in _VALID_EVENTS:
        sys.stdout.write(json.dumps(fallback) + "\n")
        return 0

    try:
        raw_stdin = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        try:
            raw_stdin = sys.stdin.read()
        except Exception:
            raw_stdin = ""

    payload: dict[str, Any] = {}
    if raw_stdin.strip():
        try:
            loaded = json.loads(raw_stdin)
            if isinstance(loaded, dict):
                payload = loaded
        except ValueError:
            payload = {}

    home_dir = _resolve_quadgate_home()
    if _is_plugin_disabled_live(home_dir):
        sys.stdout.write(json.dumps(fallback) + "\n")
        return 0

    try:
        _bootstrap_environment(home_dir)
        response = _handle_event(event_name, payload, home_dir)
    except Exception:
        response = fallback

    sys.stdout.write(json.dumps(response) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _build_hooks_json(python_cmd: str) -> dict[str, Any]:
    prefix = f"{python_cmd} hooks/antigravity_hook.py --event"
    return {
        "quadgate": {
            "enabled": True,
            "SessionStart": [
                {"type": "command", "command": f"{prefix} session_start", "timeout": 15}
            ],
            "PreInvocation": [
                {"type": "command", "command": f"{prefix} pre_invocation", "timeout": 12}
            ],
            "PreToolUse": [
                {
                    "matcher": (
                        "^(write_to_file|replace_file_content|multi_replace_file_content|"
                        "write_file|edit_file|delete_file|run_command)$"
                    ),
                    "hooks": [
                        {"type": "command", "command": f"{prefix} firewall", "timeout": 12}
                    ],
                },
                {
                    "matcher": "^(invoke_subagent|swarm)$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{prefix} subagent_governor",
                            "timeout": 12,
                        }
                    ],
                },
            ],
            "Stop": [
                {"type": "command", "command": f"{prefix} stop", "timeout": 15}
            ],
        }
    }


def _build_mcp_config_json(
    python_cmd: str,
    *,
    portable: bool,
    repo_root: Path | None = None,
    home_dir: Path | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "command": python_cmd,
        "args": ["-m", "quadgate.cli", "mcp"],
    }
    if not portable:
        env_map: dict[str, str] = {}
        if repo_root is not None:
            env_map["PYTHONPATH"] = repo_root.as_posix()
        if home_dir is not None:
            env_map["QUADGATE_HOME"] = home_dir.as_posix()
        if env_map:
            entry["env"] = env_map
    return {"mcpServers": {"quadgate-kernel": entry}}


def write_plugin_bundle(
    target_dir: Path,
    *,
    portable: bool,
    python_cmd: str | None = None,
    repo_root: Path | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Write a complete Antigravity 2.0 QuadGate plugin bundle to `target_dir`."""
    dest = Path(target_dir).expanduser()
    resolved_py = python_cmd or ("python" if portable else Path(sys.executable).as_posix())

    (dest / "assets").mkdir(parents=True, exist_ok=True)
    (dest / "hooks").mkdir(parents=True, exist_ok=True)
    (dest / "rules").mkdir(parents=True, exist_ok=True)
    (dest / "skills" / "quadgate").mkdir(parents=True, exist_ok=True)

    (dest / "plugin.json").write_text(
        json.dumps(_PLUGIN_MANIFEST, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "assets" / "logo.svg").write_text(_LOGO_SVG, encoding="utf-8")
    (dest / "hooks.json").write_text(
        json.dumps(_build_hooks_json(resolved_py), indent=2) + "\n", encoding="utf-8"
    )
    (dest / "mcp_config.json").write_text(
        json.dumps(
            _build_mcp_config_json(
                resolved_py,
                portable=portable,
                repo_root=repo_root,
                home_dir=home_dir,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (dest / "hooks" / "antigravity_hook.py").write_text(_HOOK_SCRIPT_PY, encoding="utf-8")
    (dest / "rules" / "AGENTS.md").write_text(_RULES_AGENTS_MD, encoding="utf-8")
    (dest / "skills" / "quadgate" / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (dest / "README.md").write_text(_README_MD, encoding="utf-8")
    return dest


def export_plugin_zip(source_dir: Path, zip_path: Path) -> Path:
    """Package a portable `quadgate/` plugin folder into a distributable ZIP archive."""
    src = Path(source_dir)
    out = Path(zip_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(src.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(src).as_posix()
                zf.write(file_path, arcname=f"{PLUGIN_NAME}/{rel}")
    return out


def install_global_plugin() -> Path:
    """Install/update the live user-global Antigravity QuadGate plugin."""
    target = Path.home() / ".gemini" / "config" / "plugins" / PLUGIN_NAME
    qg_home = Path.home() / ".quadgate"
    return write_plugin_bundle(
        target,
        portable=False,
        python_cmd=Path(sys.executable).as_posix(),
        repo_root=_REPO_ROOT,
        home_dir=qg_home,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or export the QuadGate Antigravity plugin.")
    parser.add_argument("--install-global", action="store_true")
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--export-zip", default="")
    parser.add_argument("--copy-zip-to", default="")
    ns = parser.parse_args(argv)

    result: dict[str, Any] = {"status": "ok"}
    if ns.install_global:
        installed = install_global_plugin()
        result["installed_global"] = str(installed)

    export_source: Path | None = None
    if ns.export_dir:
        exported = write_plugin_bundle(Path(ns.export_dir), portable=True, python_cmd="python")
        result["exported_dir"] = str(exported)
        export_source = exported

    if ns.export_zip:
        if export_source is None:
            tmp_stage = Path(ns.export_zip).parent / "_stage_quadgate"
            export_source = write_plugin_bundle(tmp_stage, portable=True, python_cmd="python")
            zipped = export_plugin_zip(export_source, Path(ns.export_zip))
            shutil.rmtree(tmp_stage, ignore_errors=True)
        else:
            zipped = export_plugin_zip(export_source, Path(ns.export_zip))
        result["exported_zip"] = str(zipped)
        if ns.copy_zip_to:
            dst_copy = Path(ns.copy_zip_to)
            dst_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zipped, dst_copy)
            result["copied_zip"] = str(dst_copy)

    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "export_plugin_zip",
    "install_global_plugin",
    "write_plugin_bundle",
]
