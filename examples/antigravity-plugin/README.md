# QuadGate Antigravity Plugin

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
