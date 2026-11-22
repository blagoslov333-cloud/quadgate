# QuadGate Antigravity Plugin

Global 4-gate control plane integration for **Antigravity 2.0** and native **Swarms**.

## What It Connects

1. **SessionStart**: Ensures the persistent QuadGate daemon (`127.0.0.1:8787`) is running with `decision1` + offline fallback.
2. **PreInvocation (Gate 1)**: Injects compact Pass-0 `[QUADGATE_CONTEXT_HINTS]` on Turn 1 and protects Tier-1 private files.
3. **PreToolUse Firewall (Gate 2)**: Runs in `assistant` mode by default (`[antigravity] firewall_mode = "assistant"` in `~/.quadgate/config.toml`; set `"strict"` or `QUADGATE_FIREWALL_MODE=strict` for untrusted lockdown). Honors `userSettings.nonWorkspaceFileAccessPolicy` for solo chats, keeps Swarm `assigned_file_cage` strict, allows safe `~/.gemini` workflows and localhost calls, prompts via `"ask"` / `"force_ask"` for `.env` / API-key / untrusted external exfil, and hard-denies disk wipes, `main`/`master` force-pushes, and direct edits to `~/.gemini/config/config.json`.
4. **PreToolUse Governor (Gate 3)**: Evaluates `invoke_subagent` and `swarm(action="add", ...)` worker spawns using graph blast radius + historical failure rate (`R_hist`) and routes `model` accordingly.
5. **Stop Audit (Gate 4)**: Verifies truth-oracle checks and prompt completeness on actionable work turns with a strict 1-strike circuit breaker.
6. **MCP Bridge (`quadgate-kernel`)**: Exposes `quadgate_context_sieve`, `quadgate_verify_mutation`, and `quadgate_audit_completion`.

## Prerequisites

Even when installed as an Antigravity plugin, `hooks.json` and `mcp_config.json` execute your machine's local `python` on `PATH` (`python hooks/antigravity_hook.py` and `python -m quadgate.cli mcp`). Ensure the following are ready on your host before enabling the plugin:

- **Python `3.11` or `3.12` (recommended)** (`>= 3.11` required): Must be available as `python` on `PATH` (on Windows, check *"Add python.exe to PATH"* in the installer). Using `3.11` or `3.12` ensures pre-built binary wheels with no C++ compilation.
- **`pip >= 23.0`** (or [`uv`](https://docs.astral.sh/uv/) if you do not keep a global Python toolchain installed).
- **Git `>= 2.25`**: Required when cloning/installing from source and for Gate 1 co-change / Gate 4 revert checks.
- **C++ Build Tools (Windows fallback only)**: Not needed on Python `3.11` / `3.12`. Only required on Windows if installing on Python `3.13+` where pre-built wheels are unavailable yet (or install the **Microsoft Visual C++ Redistributable** if adding the optional `[local]` ONNX runtime).

## Installing on Another Machine

1. Verify `python --version` (`3.11` or `3.12` recommended) and `git --version`, or run `python scripts/check_prereqs.py` from the repo root.
2. Copy this `quadgate` plugin directory to `~/.gemini/config/plugins/quadgate/`.
3. Install QuadGate in the same Python environment on `PATH`:
   ```bash
   python -m pip install --upgrade pip
   pip install -e /path/to/quadgate   # or: pip install quadgate
   # Optional local INT8 ONNX runtime:
   # pip install -e "/path/to/quadgate[local]"
   ```
4. Verify the installation and reload plugins (or start a new Antigravity chat):
   ```bash
   python -m quadgate.cli doctor
   ```
5. Optional cloud fast-path: set `DECISION1_API_KEY` in your user environment (otherwise QuadGate runs offline using `onnx_local` / `zero_dep`).
