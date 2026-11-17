# Security Policy

## Supported Versions

Security updates are applied to the current release line:

| Version | Supported |
| :--- | :--- |
| `0.1.x` | Yes |

## Reporting a Vulnerability

**Please do NOT open public GitHub issues for security vulnerabilities, credential-containment bypasses, or privilege-escalation findings.**

Report all security issues privately through GitHub Security Advisories:

- **Private Advisory Form:** https://github.com/blagoslov333-cloud/quadgate/security/advisories/new

Include in your report:

1. Affected component (for example `quadgate/shim.py` Stage-2A containment, `quadgate/kernel/daemon.py` loopback auth, `quadgate/kernel/proxy.py`, or a specific harness adapter).
2. Minimal reproduction payload or command sequence.
3. Expected invariant behavior versus observed behavior.
4. Any suggested mitigation or patch details.

### Response Timeline

- **Acknowledgment:** Within 48 hours of report receipt.
- **Triage & Severity Assessment:** Within 7 days.
- **Remediation & Coordinated Disclosure:** Fix released with advisory credit (unless anonymity is requested) once verified against the adversarial test suite.

## Security Model & Scope Boundaries

QuadGate separates defense-in-depth guarantees across two runtime boundaries:

- **Offline Fail-Closed Stage-2A (`qg-shim`):** Zero-dependency rules run inside `quadgate/shim.py` before daemon IPC and block `.env` reads/writes (except `.env.example`, `.env.sample`, `.env.template`), symlink/junction workspace escapes, encoded/piped secret exfiltration, hardcoded keys/tokens, destructive filesystem wipes, and force-pushes to `main`/`master`.
- **Loopback Daemon Authentication:** `QuadGateDaemon` binds exclusively to `127.0.0.1:8787` (proxy `127.0.0.1:8788`) and protects `/v1/evaluate`, `/v1/stats`, and `/v1/shutdown` with a per-launch random Bearer token stored at `~/.quadgate/run.token` (`0600` permissions on POSIX).
- **Pass-0 Tier-1 Privacy Air-Gap:** Matching `private_tier1_globs`, `.env*` secret paths, or `[private]`/`[confidential]`/`[airgap]` markers sets `cloud_egress_allowed = False`, preventing cloud classifier egress in `ClassifierDriver.classify()`.
- **Non-Goals:** QuadGate is an agent control plane and hook firewall, not an OS container or kernel sandbox. Production deployments running untrusted shell code should pair QuadGate with OS-level isolation.
