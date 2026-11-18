# QuadGate 4-Gate Kernel Rules

When QuadGate hooks and MCP tools are active in Antigravity:

1. **Gate 1 (`[QUADGATE_CONTEXT_HINTS]`):** Prioritize inspecting files listed in `[QUADGATE_CONTEXT_HINTS]` at Turn 1 instead of running broad directory scans. Keep conversation prefixes immutable across turns so KV caches stay warm.
2. **Gate 2 (Security & Containment Firewall):** Never retry blocked `.env`, private key, or out-of-workspace writes without removing the secret or obtaining explicit `[bypass-gates]` user intent (Stage-2A security blocks are deterministic and never bypassed). Stay inside any assigned Swarm file cage.
3. **Gate 3 (Swarm & Subagent Cost-Risk Routing):** Choose Swarm worker models deliberately when calling `swarm(action="add", ...)`:
   - Route mechanical single-file/leaf tasks to `gemini-3.8-flash-low` or `gemini-3.8-flash-high`.
   - Route high-blast-radius, security-sensitive, kernel, or multi-file architecture tasks to `gemini-4-argon-high` or `gemini-4-carbon-high`.
   - Let Gate 3 automatically rewrite `model` via `overwrite` when a task's blast radius or historical failure rate (`R_hist`) warrants promotion or floor downgrade.
4. **Gate 4 (Truth Oracle & Completeness Audit):** Verify syntax/lint checks cleanly and ensure every clause of the user request is addressed before ending turns that perform work.
