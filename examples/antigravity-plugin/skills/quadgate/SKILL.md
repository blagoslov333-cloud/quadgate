---
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
