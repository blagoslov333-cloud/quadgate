"""Production-readiness end-to-end and integration test suite for QuadGate.

Models realistic production workloads with zero external cloud calls:
1. Multi-module SaaS billing, authentication, storage, and observability Python repository
   (12+ source/config/test files) with SQL injection, `.env.production`, and traversal traps.
2. Clinical HIPAA triage vault and financial double-entry ledger markdown knowledge vaults
   with errata addenda and `[[wikilink]]` dependency graphs.
3. Mocked HTTP payloads for `gemini-decision-1`, OpenAI (`gpt-4o`), and Anthropic
   (`claude-opus-4-1` / `claude-sonnet-4-5`) via `MockTransport` and localhost HTTP servers.
4. Full lifecycle validation: `Decision1Driver` calibration, `qg-shim` CLI subprocess JSON
   hook protocol, SQLite FTS5 + AST gist indexing, CLI/daemon lifecycle, corrupt-DB fail-open
   resilience, Turn 1 -> Turn 2..N KV-cache prefix lock, `compact_reset=True`, Gate 3 ECC
   tier routing, and Gate 4 1-strike promotion + 2-strike git rollback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from quadgate.cli import main as cli_main
from quadgate.contracts import (
    CartridgeSpec,
    GateEvent,
    GateType,
    HarnessType,
    InvariantSpec,
    ModelTier,
    VerdictAction,
)
from quadgate.drivers.base import CalibrationMethod
from quadgate.drivers.decision1 import Decision1Driver
from quadgate.drivers.logprob_fallback import LogprobFallbackDriver
from quadgate.drivers.onnx_local import ZeroDepDriver
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.cochange import CoChangeGraph
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.calibration import CalibrationEngine, CalibrationSample
from quadgate.kernel.daemon import DaemonConfig, QuadGateDaemon
from quadgate.kernel.gates import GateEngine, TierModelConfig, load_cartridge
from quadgate.kernel.healing import SelfHealingEngine
from quadgate.kernel.proxy import ColdSessionProxy
from quadgate.shim import check_stage2a_deterministic_security, run_shim

REPO_ROOT = Path(__file__).resolve().parent.parent
GEMINI_DECISIONS_URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
GEMINI_MODEL_META_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-decision-1"
)
SYNTHETIC_D1_TOKEN = "AQ.DummyExampleDecisionToken00000000000000000000000"
SYNTHETIC_OPENAI_TOKEN = "sk-proj-DummyExampleToken00000000000000"
SYNTHETIC_ANTHROPIC_TOKEN = "sk-ant-api03-DummyExampleToken000000000000"
SYNTHETIC_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SYNTHETIC_GOOGLE_KEY = "AIzaSyDummyExampleKey000000000000000000"
SYNTHETIC_GITHUB_PAT = "github_pat_DummyExample0000000000000000000000"


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _init_git_repo(repo: Path) -> None:
    _run_git(repo, "init")
    _run_git(repo, "add", ".")
    _run_git(
        repo,
        "-c",
        "user.email=release-qa@acme-billing.example",
        "-c",
        "user.name=Acme Release Bot",
        "commit",
        "-m",
        "chore: initial multi-module billing/auth/observability repo",
    )


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _build_saas_billing_auth_repo(root: Path, *, init_git: bool = False) -> Path:
    """Create a realistic 14-file billing/auth/observability Python repo with security traps."""
    files: dict[str, str] = {
        "src/__init__.py": '"""Acme cloud billing and authentication platform."""\n',
        "src/storage/__init__.py": "",
        "src/storage/db_pool.py": (
            '"""Parameterized SQLite/Postgres connection pool with statement timeouts."""\n\n'
            "import sqlite3\n"
            "from contextlib import contextmanager\n"
            "from typing import Any, Iterator\n\n"
            "class TenantConnectionPool:\n"
            "    def __init__(self, dsn: str, timeout_ms: int = 2500) -> None:\n"
            "        self.dsn = dsn\n"
            "        self.timeout_ms = timeout_ms\n\n"
            "    @contextmanager\n"
            "    def connection(self) -> Iterator[sqlite3.Connection]:\n"
            "        conn = sqlite3.connect(self.dsn, timeout=self.timeout_ms / 1000.0)\n"
            "        try:\n"
            "            yield conn\n"
            "        finally:\n"
            "            conn.close()\n\n"
            "def execute_parameterized(\n"
            "    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]\n"
            ") -> list[tuple[Any, ...]]:\n"
            '    """Execute a strictly parameterized SQL query without interpolation."""\n'
            "    return conn.execute(sql, params).fetchall()\n"
        ),
        "src/observability/__init__.py": "",
        "src/observability/audit_logger.py": (
            '"""Structured security and billing audit logger with token redaction."""\n\n'
            "import json\n"
            "from src.storage.db_pool import TenantConnectionPool, execute_parameterized\n\n"
            "def redact_sensitive_fields(payload: dict[str, object]) -> dict[str, object]:\n"
            '    """Strip bearer tokens and secret keys prior to log emission."""\n'
            "    redacted: dict[str, object] = {}\n"
            "    for key, value in payload.items():\n"
            '        low = key.lower()\n'
            '        if any(s in low for s in ("secret", "token", "password", "api_key")):\n'
            '            redacted[key] = "[REDACTED]"\n'
            "        else:\n"
            "            redacted[key] = value\n"
            "    return redacted\n\n"
            "def emit_security_event(event_type: str, details: dict[str, object]) -> str:\n"
            '    """Serialize an immutable JSON audit log record."""\n'
            "    clean = redact_sensitive_fields(details)\n"
            '    return json.dumps({"event": event_type, "details": clean}, sort_keys=True)\n'
        ),
        "src/observability/metrics.py": (
            '"""Prometheus-compatible histogram and counter registry for service SLOs."""\n\n'
            "class MetricsRegistry:\n"
            "    def __init__(self) -> None:\n"
            "        self.counters: dict[str, int] = {}\n"
            "        self.latencies_ms: dict[str, list[float]] = {}\n\n"
            "    def inc_counter(self, name: str, delta: int = 1) -> int:\n"
            "        self.counters[name] = self.counters.get(name, 0) + delta\n"
            "        return self.counters[name]\n\n"
            "    def observe_latency_ms(self, metric: str, value_ms: float) -> None:\n"
            "        self.latencies_ms.setdefault(metric, []).append(float(value_ms))\n"
        ),
        "src/auth/__init__.py": "",
        "src/auth/jwt_validator.py": (
            '"""JWT bearer token verification, clock-skew leeway, and claim decoding."""\n\n'
            "import time\n"
            "from src.observability.audit_logger import emit_security_event\n\n"
            "class TokenExpiredError(Exception):\n"
            '    """Raised when exp + leeway_sec is earlier than current epoch."""\n\n'
            "class InvalidSignatureError(Exception):\n"
            '    """Raised when HMAC/RSA signature verification fails."""\n\n'
            "def verify_bearer_token(\n"
            "    claims: dict[str, object], leeway_sec: int = 30, now_epoch: int | None = None\n"
            ") -> bool:\n"
            '    """Validate JWT exp/nbf claims honoring bounded clock leeway."""\n'
            "    now = int(time.time()) if now_epoch is None else now_epoch\n"
            '    exp_raw = claims.get("exp")\n'
            "    if not isinstance(exp_raw, int):\n"
            '        emit_security_event("jwt_missing_exp", {"sub": claims.get("sub")})\n'
            '        raise InvalidSignatureError("Token missing integer exp claim.")\n'
            "    if now > exp_raw + max(0, leeway_sec):\n"
            '        emit_security_event("jwt_expired", {"sub": claims.get("sub")})\n'
            '        raise TokenExpiredError("Bearer token expired past clock leeway.")\n'
            "    return True\n"
        ),
        "src/auth/rbac_middleware.py": (
            '"""Tenant-scoped RBAC authorization middleware for billing API routes."""\n\n'
            "from src.auth.jwt_validator import verify_bearer_token\n"
            "from src.observability.audit_logger import emit_security_event\n\n"
            "class PermissionDeniedError(Exception):\n"
            '    """Raised when caller lacks required tenant role."""\n\n'
            "def require_role(claims: dict[str, object], required_role: str) -> None:\n"
            "    verify_bearer_token(claims)\n"
            '    roles = claims.get("roles", [])\n'
            "    if not isinstance(roles, list) or required_role not in roles:\n"
            '        emit_security_event("rbac_denied", {"required_role": required_role})\n'
            '        raise PermissionDeniedError(f"Missing role: {required_role}")\n'
        ),
        "src/auth/session_store.py": (
            '"""Session revocation cache backed by TenantConnectionPool and JWT IDs."""\n\n'
            "from src.auth.jwt_validator import verify_bearer_token\n"
            "from src.observability.metrics import MetricsRegistry\n"
            "from src.storage.db_pool import TenantConnectionPool, execute_parameterized\n\n"
            "def is_session_active(\n"
            "    pool: TenantConnectionPool, jti: str, metrics: MetricsRegistry | None = None\n"
            ") -> bool:\n"
            "    with pool.connection() as conn:\n"
            "        rows = execute_parameterized(\n"
            '            conn, "SELECT revoked FROM sessions WHERE jti = ?", (jti,)\n'
            "        )\n"
            "    if metrics is not None:\n"
            '        metrics.inc_counter("auth.session_lookup")\n'
            "    return not bool(rows and rows[0][0])\n"
        ),
        "src/billing/__init__.py": "",
        "src/billing/customer_queries.py": (
            '"""Parameterized billing queries for tenant invoices and subscriptions."""\n\n'
            "from src.storage.db_pool import TenantConnectionPool, execute_parameterized\n\n"
            "def fetch_customer_invoices(\n"
            "    pool: TenantConnectionPool, customer_id: str\n"
            ") -> list[tuple[object, ...]]:\n"
            '    """Fetch finalized invoices for a tenant using SQL placeholders."""\n'
            "    with pool.connection() as conn:\n"
            "        return execute_parameterized(\n"
            "            conn,\n"
            '            "SELECT invoice_id, amount_cents, currency FROM invoices '
            'WHERE customer_id = ?",\n'
            "            (customer_id,),\n"
            "        )\n"
        ),
        "src/billing/invoice_ledger.py": (
            '"""Double-entry invoice ledger posting and reconciliation."""\n\n'
            "from dataclasses import dataclass\n"
            "from decimal import Decimal\n"
            "from src.observability.audit_logger import emit_security_event\n"
            "from src.storage.db_pool import TenantConnectionPool\n\n"
            "@dataclass(frozen=True)\n"
            "class InvoiceLineItem:\n"
            "    account: str\n"
            "    debit_usd: Decimal\n"
            "    credit_usd: Decimal\n\n"
            "def reconcile_payout(lines: list[InvoiceLineItem]) -> Decimal:\n"
            '    """Compute net ledger balance (must equal 0.00 for balanced entries)."""\n'
            '    debits = sum((line.debit_usd for line in lines), Decimal("0.00"))\n'
            '    credits = sum((line.credit_usd for line in lines), Decimal("0.00"))\n'
            "    delta = debits - credits\n"
            '    emit_security_event("ledger_reconciled", {"delta": str(delta)})\n'
            "    return delta\n"
        ),
        "src/billing/stripe_webhook.py": (
            '"""Stripe billing webhook signature verification and invoice settlement."""\n\n'
            "import hashlib\n"
            "import hmac\n"
            "from decimal import Decimal\n"
            "from src.billing.invoice_ledger import InvoiceLineItem, reconcile_payout\n"
            "from src.observability.audit_logger import emit_security_event\n\n"
            "class WebhookSignatureError(Exception):\n"
            '    """Raised on invalid Stripe-Signature header."""\n\n'
            "def verify_stripe_signature(\n"
            "    payload_bytes: bytes, signature_hex: str, signing_secret: str\n"
            ") -> bool:\n"
            "    digest = hmac.new(\n"
            '        signing_secret.encode("utf-8"), payload_bytes, hashlib.sha256\n'
            "    ).hexdigest()\n"
            "    if not hmac.compare_digest(digest, signature_hex):\n"
            '        raise WebhookSignatureError("Stripe webhook HMAC mismatch.")\n'
            "    return True\n\n"
            "def handle_invoice_paid(invoice_id: str, amount_usd: str) -> Decimal:\n"
            "    amt = Decimal(amount_usd)\n"
            "    lines = [\n"
            '        InvoiceLineItem("cash_clearing", amt, Decimal("0.00")),\n'
            '        InvoiceLineItem("accounts_receivable", Decimal("0.00"), amt),\n'
            "    ]\n"
            '    emit_security_event("invoice_paid", {"invoice_id": invoice_id})\n'
            "    return reconcile_payout(lines)\n"
        ),
        "src/api/__init__.py": "",
        "src/api/export_handler.py": (
            '"""Leaf CSV export formatting utility with path traversal protection."""\n\n'
            "from pathlib import Path\n\n"
            "def format_csv_header(columns: list[str]) -> str:\n"
            '    """Render canonical CSV header line for invoice exports."""\n'
            '    return ",".join(col.strip() for col in columns if col.strip())\n\n'
            "def resolve_export_path(export_root: Path, filename: str) -> Path:\n"
            "    candidate = (export_root / filename).resolve()\n"
            "    root_resolved = export_root.resolve()\n"
            "    candidate.relative_to(root_resolved)\n"
            "    return candidate\n"
        ),
        "tests/test_billing_auth.py": (
            '"""Unit checks for JWT leeway and balanced Stripe invoice settlement."""\n\n'
            "from decimal import Decimal\n"
            "from src.auth.jwt_validator import verify_bearer_token\n"
            "from src.billing.stripe_webhook import handle_invoice_paid\n\n"
            "def test_jwt_and_invoice_settlement() -> None:\n"
            '    claims = {"sub": "usr_101", "exp": 2_000_000_000}\n'
            "    assert verify_bearer_token(claims, now_epoch=1_900_000_000)\n"
            '    assert handle_invoice_paid("in_9001", "149.99") == Decimal("0.00")\n'
        ),
        ".env.production": (
            "STRIPE_SIGNING_SECRET=whsec_DummyExampleSecret000000000000\n"
            "DATABASE_URL=postgresql://billing_svc:DummyPass@db.internal:5432/prod_ledger\n"
        ),
        ".env.example": (
            "STRIPE_SIGNING_SECRET=replace_with_stripe_webhook_secret\n"
            "DATABASE_URL=sqlite:///./dev_billing.db\n"
        ),
    }
    _write_files(root, files)
    if init_git:
        _init_git_repo(root)
    return root


def _build_clinical_hipaa_vault(root: Path, *, include_private_dir: bool = True) -> Path:
    """Create a realistic clinical HIPAA triage markdown knowledge vault with errata."""
    files: dict[str, str] = {
        "protocols/sepsis_triage_v2.md": (
            "# Emergency Department Sepsis Triage Protocol v2\n\n"
            "Screen adult ED arrivals with suspected infection for qSOFA >= 2 or serum lactate\n"
            ">= 2.0 mmol/L within 15 minutes of arrival. Obtain two sets of blood cultures prior\n"
            "to empiric antimicrobial infusion and consult [[antibiotic_dosing_errata]] for\n"
            "renal clearance adjustments. Escalate shock to [[icu_escalation_matrix]].\n\n"
            "References: doi:10.1001/jama.2024.1120 | https://med.example.org/sepsis-v2\n"
        ),
        "protocols/antibiotic_dosing_errata.md": (
            "# Errata Addendum: Renal Antibiotic Dosing (Supersedes Sepsis v2 Table 3)\n\n"
            "Correction to [[sepsis_triage_v2]]: administer vancomycin 25 mg/kg IV loading dose\n"
            "based on actual body weight (capped at 3,000 mg). For CrCl < 30 mL/min, extend\n"
            "maintenance interval to q24h and obtain trough prior to second dose.\n\n"
            "Source: https://formulary.hospital.example/vanc-renal-errata\n"
        ),
        "protocols/icu_escalation_matrix.md": (
            "# ICU Escalation & Vasopressor Matrix\n\n"
            "Initiate peripheral norepinephrine if MAP < 65 mmHg persists after 30 mL/kg fluid\n"
            "bolus per [[sepsis_triage_v2]]. Page critical care fellow immediately when lactate\n"
            "exceeds 4.0 mmol/L on repeat draw.\n\n"
            "Citation: https://icu.hospital.example/vasopressor-matrix\n"
        ),
        "protocols/ed_bed_capacity.md": (
            "# Emergency Department Surge Capacity & Ambulance Diversion Policy\n\n"
            "Activate internal surge staffing when waiting room boarding exceeds 18 patients.\n"
            "Reference: https://ops.hospital.example/surge-policy\n"
        ),
        "docs/shift_handoff_triage_note.md": (
            "# ED Night Shift Triage Handoff Note\n\n"
            "- Patient SSN: 527-49-8316\n"
            "- MRN: PHI-MRN-88412\n"
            "- Chief Complaint: Fever 39.4C, lactate 4.6 mmol/L, CrCl 22 mL/min\n"
            "- Action: Follow [[antibiotic_dosing_errata]] and [[icu_escalation_matrix]].\n"
        ),
    }
    if include_private_dir:
        files["vault/private/patient_0418_hipaa.md"] = (
            "# Restricted HIPAA Patient Intake Record 0418\n\n"
            "- Patient SSN: 527-49-8316\n"
            "- MRN: PHI-MRN-88412\n"
            "- Attending: Dr. Elena Vasquez, Emergency Medicine\n"
        )
    _write_files(root, files)
    return root


def _build_financial_ledger_vault(root: Path) -> Path:
    """Create a realistic financial operations ledger and dual-control policy vault."""
    balanced_payload = {
        "batch_id": "SETTLE-2026-Q4-019",
        "currency": "USD",
        "entries": [
            {"account": "1010-Cash-Clearing", "debit": "148500.00", "credit": "0.00"},
            {"account": "2010-Merchant-Payable", "debit": "0.00", "credit": "144250.00"},
            {"account": "4020-Platform-Fees", "debit": "0.00", "credit": "4250.00"},
        ],
    }
    imbalanced_payload = {
        "batch_id": "SETTLE-2026-Q4-BAD",
        "currency": "USD",
        "entries": [
            {"account": "1010-Cash-Clearing", "debit": "148500.00", "credit": "0.00"},
            {"account": "2010-Merchant-Payable", "debit": "0.00", "credit": "148250.00"},
        ],
    }
    files: dict[str, str] = {
        "policies/treasury_dual_control.md": (
            "# Treasury Dual-Control Payout Policy\n\n"
            "All outbound ACH or wire transfers >= $50,000.00 require dual treasury controller\n"
            "sign-off and balanced journal proof per [[errata_fx_reconciliation]].\n\n"
            "Source: https://treasury.internal.example/dual-control-v4\n"
        ),
        "ledger/errata_fx_reconciliation.md": (
            "# Q4 FX Settlement Variance Errata Addendum\n\n"
            "Updated clearing tolerance for EUR/USD conversion batches linked from\n"
            "[[treasury_dual_control]]: round settled USD lines strictly to 2 decimal places.\n\n"
            "Reference: https://treasury.internal.example/fx-2026q4\n"
        ),
        "ledger/q4_settlement_balanced.json": json.dumps(balanced_payload, indent=2) + "\n",
        "ledger/q4_settlement_imbalanced.json": json.dumps(imbalanced_payload, indent=2) + "\n",
    }
    _write_files(root, files)
    return root


class _MockGeminiDecision1Transport:
    """Deterministic local MockTransport emulating `gemini-decision-1` HTTP payloads."""

    def __init__(self, *, score_overrides: dict[str, float] | None = None) -> None:
        self.score_overrides = score_overrides or {}
        self.calls: list[dict[str, Any]] = []
        self.health_calls: int = 0

    @staticmethod
    def _token_overlap(a: str, b: str) -> float:
        stop = {
            "the", "and", "for", "with", "from", "into", "this", "that", "does",
            "should", "please", "update", "file", "files", "candidate", "requirement",
            "src", "tests", "def", "class",
        }

        def _parts(text: str) -> set[str]:
            spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ")
            out: set[str] = set()
            for tok in re.findall(r"[a-z0-9]{2,}", spaced.lower()):
                if tok in stop:
                    continue
                out.add(tok[:5] if len(tok) >= 5 else tok)
            return out

        toks_a = _parts(a)
        toks_b = _parts(b)
        if not toks_a:
            return 0.0
        return len(toks_a & toks_b) / len(toks_a)

    def _score_question(self, input_text: str, instructions: str) -> float:
        low_in = input_text.lower()
        low_q = instructions.lower()

        for marker, forced in self.score_overrides.items():
            if marker.lower() in low_q:
                return forced

        # Calibration & Gate 1 pragmatic heads
        if "counterfactual" in low_q or "hypothetical" in low_q or "brainstorm" in low_q:
            is_cf = any(
                cue in low_in
                for cue in ("what if", "hypothetically", "suppose", "next year", "brainstorm")
            )
            return 0.96 if is_cf else 0.03

        if "actionable directive" in low_q or "directive" in low_q:
            is_cf = any(
                cue in low_in
                for cue in ("what if", "hypothetically", "suppose", "next year", "brainstorm")
            )
            return 0.06 if is_cf else 0.96

        # Gate 2B semantic invariants
        if "sql" in low_q and (
            "interpolat" in low_q or "f-string" in low_q or "injection" in low_q
        ):
            has_sqli = bool(
                re.search(r"""f["'].*select\b.*\{""", low_in)
                or ("select " in low_in and ".format(" in low_in)
            )
            return 0.94 if has_sqli else 0.04

        if "inv_01_secret_exposure" in low_q or "exfiltrat" in low_q or "secrets" in low_q:
            has_secret = (
                ".env.production" in low_in
                or "webhook.site" in low_in
                or "akia" in low_in
                or "sk-ant-" in low_in
                or "sk-proj-" in low_in
            )
            return 0.97 if has_secret else 0.03

        if "invariant" in low_q:
            return 0.04

        # Gate 3 ECC tier heads
        if "floor-tier" in low_q:
            is_complex = any(
                k in low_in
                for k in ("architect", "zero-downtime", "distributed", "db_pool", "security")
            )
            return 0.35 if is_complex else 0.94
        if "mid-tier" in low_q:
            is_complex = any(k in low_in for k in ("architect", "zero-downtime", "distributed"))
            return 0.45 if is_complex else 0.92
        if "frontier-tier" in low_q:
            return 0.97

        # Gate 4 requirement clauses (clause text extracted after Requirement:/Heading:)
        if "requirement:" in low_q or "heading:" in low_q or "deliverable artifact" in low_q:
            marker = "Heading:" if "Heading:" in instructions else "Requirement:"
            clause = instructions.split(marker, 1)[-1].strip()
            overlap = self._token_overlap(clause, input_text)
            return 0.94 if overlap >= 0.45 else 0.25

        # Gate 1 candidate relevance / calibration candidates
        candidate = instructions.split("Candidate:", 1)[-1].strip()
        overlap = self._token_overlap(candidate, input_text)
        if overlap >= 0.35 or any(
            stem in low_in for stem in re.findall(r"[a-z0-9_]+\.py", candidate.lower())
        ):
            return 0.95
        return 0.04

    async def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        if method == "GET" and url == GEMINI_MODEL_META_URL:
            self.health_calls += 1
            return 200, {"name": "models/gemini-decision-1", "status": "ok"}

        assert method == "POST"
        assert url == GEMINI_DECISIONS_URL
        assert body is not None
        assert body.get("model") == "gemini-decision-1"
        self.calls.append(
            {"url": url, "headers": dict(headers), "body": body, "timeout_s": timeout_s}
        )

        input_text = str(body.get("input", ""))
        questions = body.get("questions", {})
        answers: dict[str, dict[str, float]] = {}
        for slot_key, q_spec in questions.items():
            score = self._score_question(input_text, str(q_spec.get("instructions", "")))
            answers[str(slot_key)] = {"noul": round(score, 4)}

        token_est = max(64, len(input_text.split()) * 2 + len(questions) * 18)
        return 200, {"answers": answers, "usage": {"total_input_tokens": token_est}}


class _MockOpenAILogprobTransport:
    """Deterministic MockTransport simulating OpenAI `gpt-4o` single-token logprob responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        del timeout_s
        if method == "GET":
            return 200, {"status": "ok"}
        assert body is not None
        assert body.get("model") == "gpt-4o"
        assert body.get("max_tokens") == 1
        assert body.get("temperature") == 0
        assert body.get("logprobs") is True
        assert body.get("top_logprobs") == 5
        self.calls.append({"url": url, "headers": dict(headers), "body": body})

        user_msg = str(body["messages"][-1]["content"])
        query_part = ""
        cand_part = user_msg
        if "Candidate:" in user_msg:
            before, after = user_msg.split("Candidate:", 1)
            query_part = before
            cand_part = after.split("\nDoes this candidate", 1)[0]
        q_low = query_part.lower()
        c_low = cand_part.lower()
        is_match = (
            ("jwt" in q_low and "jwt_validator.py" in c_low)
            or ("stripe" in q_low and "stripe_webhook.py" in c_low)
            or (".env.production" in q_low and "secret" in c_low)
        )
        top_lps = (
            [{"token": "YES", "logprob": -0.04}, {"token": "NO", "logprob": -3.25}]
            if is_match
            else [{"token": "NO", "logprob": -0.05}, {"token": "YES", "logprob": -3.05}]
        )
        return 200, {
            "id": "chatcmpl-mock-gpt4o",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": top_lps[0]["token"]},
                    "logprobs": {
                        "content": [{"token": top_lps[0]["token"], "top_logprobs": top_lps}]
                    },
                }
            ],
            "usage": {"prompt_tokens": 110, "completion_tokens": 1, "total_tokens": 111},
        }


async def test_e2e_saas_ast_fts5_cochange_and_git_delta_sync(tmp_path: Path) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "saas_repo", init_git=False)
    cartridge = load_cartridge("software_engineering")

    indexer = AstGistIndexer(repo, cartridge=cartridge)
    gists = indexer.index_workspace(incremental=False)
    by_path = {g.rel_path: g for g in gists}

    assert len(gists) >= 12
    assert "src/storage/db_pool.py" in by_path
    assert "src/observability/audit_logger.py" in by_path
    assert "src/api/export_handler.py" in by_path
    assert by_path["src/storage/db_pool.py"].fan_in_count >= 3
    assert by_path["src/observability/audit_logger.py"].fan_in_count >= 3
    assert by_path["src/api/export_handler.py"].fan_in_count == 0

    blast_core = indexer.compute_blast_radius(["src/storage/db_pool.py"])
    blast_leaf = indexer.compute_blast_radius(["src/api/export_handler.py"])
    assert blast_core > 0.30
    assert blast_leaf == 0.0

    # Persistent WAL SQLite FTS5 store + camelCase & trigram queries
    db_path = tmp_path / "state" / "pass0.db"
    fts = SqliteFtsStore(db_path)
    try:
        fts.initialize()
        inserted = fts.upsert_gists(gists)
        assert inserted == len(gists)

        t0 = time.perf_counter()
        res_jwt = fts.search_candidates("verifyBearerToken leeway", indexer.workspace_hash, limit=8)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 25.0
        assert any(g.rel_path == "src/auth/jwt_validator.py" for g in res_jwt.gists[:3])
        assert "src/auth/jwt_validator.py" in res_jwt.scores
        assert 0.0 < res_jwt.scores["src/auth/jwt_validator.py"] <= 1.0

        res_tri = fts.search_candidates("reconcil", indexer.workspace_hash, limit=8)
        assert any(g.rel_path == "src/billing/invoice_ledger.py" for g in res_tri.gists)

        # Incremental delta sync after modifying one file on disk
        webhook_path = repo / "src/billing/stripe_webhook.py"
        webhook_path.write_text(
            webhook_path.read_text(encoding="utf-8")
            + "\ndef verify_dispute_timestamp(ts: int) -> bool:\n    return ts > 0\n",
            encoding="utf-8",
        )
        delta_gists = indexer.index_workspace(incremental=True)
        updated_count = fts.upsert_gists(delta_gists)
        assert updated_count == 1
        res_dispute = fts.search_candidates("verify_dispute_timestamp", indexer.workspace_hash)
        assert res_dispute.gists
        assert res_dispute.gists[0].rel_path == "src/billing/stripe_webhook.py"

        # Workspace isolation against a second vault hash
        other_res = fts.search_candidates("verifyBearerToken", "other-workspace-hash-000")
        assert other_res.gists == []
    finally:
        fts.close()

    # CoChangeGraph parsed from commit log history
    cochange = CoChangeGraph(repo)
    cochange.build_from_log_text(
        "\n".join(
            [
                "__QG_COMMIT__",
                "src/auth/jwt_validator.py",
                "src/auth/session_store.py",
                "__QG_COMMIT__",
                "src/auth/jwt_validator.py",
                "src/auth/session_store.py",
                "__QG_COMMIT__",
                "src/auth/jwt_validator.py",
                "src/billing/stripe_webhook.py",
            ]
        )
    )
    assert cochange.weight(
        "src/auth/jwt_validator.py", "src/auth/session_store.py"
    ) == pytest.approx(2 / 3)


async def test_e2e_decision1_calibration_and_4gate_lifecycle(tmp_path: Path) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "repo")
    # Remove .env.production from this public-coding workspace so Gate 1 exercises cloud Decision 1
    (repo / ".env.production").unlink()

    transport = _MockGeminiDecision1Transport()
    driver = Decision1Driver(
        endpoint=GEMINI_DECISIONS_URL,
        api_key=SYNTHETIC_D1_TOKEN,
        transport=transport,
    )
    try:
        assert await driver.health_check() is True
        assert transport.health_calls == 1

        # 1. Calibrate Decision1Driver against golden dataset & persist profile
        cal_engine = CalibrationEngine(profile_dir=tmp_path / "cal_profiles", max_allowed_ece=0.05)
        gold_path = REPO_ROOT / "benchmarks" / "calibration_gold.jsonl"
        profile = await cal_engine.fit_driver(
            driver,
            dataset_path=gold_path,
            method=CalibrationMethod.TEMPERATURE_SCALING,
            persist=True,
        )
        assert profile.driver_id == "decision1"
        assert profile.sample_count >= 6
        assert profile.ece <= 0.05
        assert profile.passes_ece_gate is True
        assert cal_engine.load_profile("decision1") == profile

        # Also validate a 64-sample multi-domain calibration pass
        multi_samples: list[CalibrationSample] = []
        for idx in range(64):
            pos = idx % 2 == 0
            multi_samples.append(
                CalibrationSample(
                    id=f"prod_cal_{idx:02d}",
                    task="Fix JWT leeway expiration in src/auth/jwt_validator.py",
                    candidate=(
                        f"src/auth/jwt_validator.py: verify_bearer_token #{idx}"
                        if pos
                        else f"src/api/export_handler.py: format_csv_header #{idx}"
                    ),
                    label=(1 if pos else 0),
                )
            )
        profile_64 = await cal_engine.fit_driver(
            driver,
            samples=multi_samples,
            method=CalibrationMethod.TEMPERATURE_SCALING,
            persist=False,
        )
        assert profile_64.sample_count == 64
        assert profile_64.ece <= 0.05
        assert profile_64.passes_ece_gate is True

        # Re-attach gold profile and build GateEngine with SQLite FTS5 + CoChange + SQLi invariant
        driver.set_calibration_profile(profile)
        base_cart = load_cartridge("software_engineering")
        ext_cart = CartridgeSpec(
            name=base_cart.name,
            version=base_cart.version,
            context_unit=base_cart.context_unit,
            mutation_tools=base_cart.mutation_tools,
            invariants=[
                *base_cart.invariants,
                InvariantSpec(
                    id="INV_04_SQL_INJECTION",
                    gist=(
                        "Never build SQL queries via string interpolation or f-strings; "
                        "use placeholders."
                    ),
                    remediation="Pass query parameters as a tuple to execute_parameterized().",
                    scope_globs=["src/billing/*.py", "src/storage/*.py"],
                ),
            ],
            truth_oracle=base_cart.truth_oracle,
        )
        indexer = AstGistIndexer(repo, cartridge=ext_cart)
        gists = indexer.index_workspace(incremental=False)
        fts = SqliteFtsStore(":memory:")
        fts.upsert_gists(gists)
        cochange = CoChangeGraph(repo)
        cochange.build_from_log_text(
            "__QG_COMMIT__\nsrc/auth/jwt_validator.py\nsrc/auth/session_store.py\n"
        )
        engine = GateEngine(
            cartridge=ext_cart,
            driver=driver,
            fts_store=fts,
            indexer=indexer,
            cochange=cochange,
            tiers=TierModelConfig(
                frontier="claude-opus-4-1",
                mid="claude-sonnet-4-5",
                floor="claude-haiku-4-5",
            ),
        )

        # 2. Gate 1 Turn 1 -> injects jwt_validator.py (+ cochange boost) via gemini-decision-1
        calls_before = len(transport.calls)
        v_t1 = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="d1-sess-1",
                turn_index=1,
                workspace_root=str(repo),
                harness=HarnessType.CLAUDE_CODE,
                prompt_text=(
                    "Update verify_bearer_token in src/auth/jwt_validator.py to support leeway"
                ),
            )
        )
        assert v_t1.action == VerdictAction.AUGMENT
        assert "src/auth/jwt_validator.py" in v_t1.tail_hints
        assert v_t1.cloud_egress_allowed is True
        assert len(transport.calls) == calls_before + 1
        assert transport.calls[-1]["headers"]["x-goog-api-key"] == SYNTHETIC_D1_TOKEN

        # 2b. Zero false-positive air-gap when code/prompt uses `.key` attribute access
        v_attr = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="d1-sess-attr-key",
                turn_index=1,
                workspace_root=str(repo),
                prompt_text="Refactor redact_sensitive_fields in audit_logger.py to check item.key",
            )
        )
        assert v_attr.cloud_egress_allowed is True
        assert any("audit_logger.py" in h for h in v_attr.tail_hints)

        # 3. Turns 2..8 strictly preserve KV prefix cache with 0 Decision1 HTTP calls
        calls_after_t1 = len(transport.calls)
        for turn in range(2, 9):
            v_tn = await engine.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id="d1-sess-1",
                    turn_index=turn,
                    workspace_root=str(repo),
                    prompt_text=f"Continue step {turn}",
                )
            )
            assert v_tn.action == VerdictAction.ALLOW
            assert v_tn.tail_hints == []
            assert v_tn.reason is None
        assert len(transport.calls) == calls_after_t1

        # 4. compact_reset=True re-arms Gate 1 cleanly
        v_reset = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="d1-sess-1",
                turn_index=9,
                workspace_root=str(repo),
                prompt_text="Update verify_bearer_token in src/auth/jwt_validator.py after compact",
                metadata={"compact_reset": True},
            )
        )
        assert v_reset.action == VerdictAction.AUGMENT
        assert "src/auth/jwt_validator.py" in v_reset.tail_hints
        assert len(transport.calls) == calls_after_t1 + 1

        # 5. Brainstorm advisory detection
        v_cf = await engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="d1-sess-cf",
                turn_index=1,
                workspace_root=str(repo),
                prompt_text=(
                    "What if we hypothetically migrated invoice_ledger.py to Rust next year?"
                ),
            )
        )
        assert v_cf.action == VerdictAction.AUGMENT
        assert v_cf.reason is not None and "brainstorm" in v_cf.reason.lower()

        # 6. Gate 2 Stage-2B blocks SQL injection f-string interpolation
        v_sqli = await engine.evaluate_gate2_firewall(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="d1-sess-1",
                turn_index=3,
                workspace_root=str(repo),
                tool_name="Edit",
                tool_args={
                    "file_path": "src/billing/customer_queries.py",
                    "new_string": (
                        'sql = f"SELECT * FROM invoices WHERE customer_id = \'{customer_id}\'"\n'
                    ),
                },
            )
        )
        assert v_sqli.action == VerdictAction.BLOCK
        assert v_sqli.violation_receipt is not None
        assert v_sqli.violation_receipt.rule_id == "INV_04_SQL_INJECTION"
        assert v_sqli.violation_receipt.stage == "semantic_2b"

        # Explicit [bypass-gates] overrides Stage-2B semantic warning (unlike Stage-2A)
        v_sqli_bypass = await engine.evaluate_gate2_firewall(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="d1-sess-1-bypass",
                turn_index=3,
                workspace_root=str(repo),
                tool_name="Edit",
                tool_args={
                    "file_path": "src/billing/customer_queries.py",
                    "new_string": (
                        'sql = f"SELECT * FROM invoices WHERE customer_id = \'{customer_id}\'"\n'
                    ),
                },
                bypass_gates=True,
            )
        )
        assert v_sqli_bypass.action == VerdictAction.ALLOW

        # 7. Gate 4 completeness rejects missing clause and passes complete multi-clause diff
        multi_clause_prompt = (
            "1. Update verify_bearer_token leeway_sec validation in jwt_validator.py.\n"
            "2. Emit jwt_expired audit event in audit_logger.py.\n"
            "3. Enforce role check in rbac_middleware.py.\n"
            "4. Verify session revocation in session_store.py.\n"
            "5. Add unit test coverage for expired tokens in test_billing_auth.py."
        )
        v_g4_bad = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="d1-sess-g4-bad",
                turn_index=4,
                workspace_root=str(repo),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                prompt_text=multi_clause_prompt,
                touched_files=["src/auth/jwt_validator.py"],
                diff_text="def verify_bearer_token(claims, leeway_sec=30):\n    return True\n",
            )
        )
        assert v_g4_bad.action in {VerdictAction.AUGMENT, VerdictAction.PROMOTE}

        v_g4_ok = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="d1-sess-g4-ok",
                turn_index=4,
                workspace_root=str(repo),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                prompt_text=multi_clause_prompt,
                touched_files=[
                    "src/auth/jwt_validator.py",
                    "src/observability/audit_logger.py",
                    "src/auth/rbac_middleware.py",
                    "src/auth/session_store.py",
                    "tests/test_billing_auth.py",
                ],
                diff_text=(
                    "Updated verify_bearer_token leeway_sec validation in jwt_validator.py.\n"
                    "Emitted jwt_expired audit event in audit_logger.py.\n"
                    "Enforced role check in rbac_middleware.py.\n"
                    "Verified session revocation in session_store.py.\n"
                    "Added unit test coverage for expired tokens in test_billing_auth.py.\n"
                ),
            )
        )
        assert v_g4_ok.action == VerdictAction.ALLOW
        assert any(
            str(c["body"].get("input", "")).startswith("[COMPLETED_ARTIFACT]")
            for c in transport.calls
        )
        fts.close()
    finally:
        await driver.close()


async def test_e2e_openai_gpt4o_logprob_driver_calibration_and_scoring(
    tmp_path: Path,
) -> None:
    oa_transport = _MockOpenAILogprobTransport()
    oa_driver = LogprobFallbackDriver(
        endpoint="https://api.openai.com/v1/chat/completions",
        model="gpt-4o",
        api_key=SYNTHETIC_OPENAI_TOKEN,
        transport=oa_transport,
    )
    try:
        from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory

        cal_samples = [
            CalibrationSample(
                id="oa_pos_1",
                task="Fix JWT leeway in jwt_validator.py",
                candidate="src/auth/jwt_validator.py: verify_bearer_token",
                label=1,
            ),
            CalibrationSample(
                id="oa_neg_1",
                task="Fix JWT leeway in jwt_validator.py",
                candidate="src/api/export_handler.py: format_csv_header",
                label=0,
            ),
            CalibrationSample(
                id="oa_pos_2",
                task="Fix Stripe signature verification in stripe_webhook.py",
                candidate="src/billing/stripe_webhook.py: verify_stripe_signature",
                label=1,
            ),
            CalibrationSample(
                id="oa_neg_2",
                task="Fix Stripe signature verification in stripe_webhook.py",
                candidate="src/observability/metrics.py: observe_latency_ms",
                label=0,
            ),
        ]
        cal_engine = CalibrationEngine(profile_dir=tmp_path / "oa_cal")
        oa_profile = await cal_engine.fit_driver(
            oa_driver,
            samples=cal_samples,
            method=CalibrationMethod.TEMPERATURE_SCALING,
            persist=True,
        )
        assert oa_profile.driver_id == "logprob_fallback"
        assert oa_profile.passes_ece_gate is True

        calls_before = len(oa_transport.calls)
        req = ClassifierRequest(
            query_text="Fix JWT token expiration leeway in jwt_validator.py",
            heads=[
                ClassifierHead(
                    head_id="h_jwt",
                    candidate_text="src/auth/jwt_validator.py: verify_bearer_token",
                    category=HeadCategory.CANDIDATE,
                ),
                ClassifierHead(
                    head_id="h_csv",
                    candidate_text="src/api/export_handler.py: format_csv_header",
                    category=HeadCategory.CANDIDATE,
                ),
            ],
        )
        resp = await oa_driver.classify(req)
        assert resp.is_calibrated is True
        assert resp.calibrated_probabilities["h_jwt"] > 0.90
        assert resp.calibrated_probabilities["h_csv"] < 0.10
        assert resp.tokens_used == 222
        assert len(oa_transport.calls) == calls_before + 2
        assert (
            oa_transport.calls[-1]["headers"]["Authorization"]
            == f"Bearer {SYNTHETIC_OPENAI_TOKEN}"
        )
    finally:
        await oa_driver.close()


async def test_e2e_anthropic_and_openai_proxy_ecc_routing_and_kv_cache_lock(
    tmp_path: Path,
) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "proxy_repo")
    cartridge = load_cartridge("software_engineering")
    indexer = AstGistIndexer(repo, cartridge=cartridge)
    indexer.index_workspace(incremental=False)
    healing = SelfHealingEngine(db_path=tmp_path / "proxy_heal.db", workspace_root=repo)
    healing.record_file_outcome(
        ["src/billing/stripe_webhook.py"], passed=False, workspace_root=str(repo)
    )
    healing.record_file_outcome(
        ["src/billing/stripe_webhook.py"], passed=False, workspace_root=str(repo)
    )

    tiers = TierModelConfig(
        frontier="claude-opus-4-1",
        mid="claude-sonnet-4-5",
        floor="claude-haiku-4-5",
    )
    engine = GateEngine(cartridge=cartridge, tiers=tiers, indexer=indexer, healing=healing)

    upstream_calls: list[dict[str, Any]] = []

    async def handle_anthropic_messages(request: web.Request) -> web.Response:
        body = await request.json()
        upstream_calls.append({"path": request.path, "body": body})
        return web.json_response(
            {
                "id": "msg_mock_01",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [{"type": "text", "text": "Processed cleanly."}],
                "usage": {
                    "input_tokens": 1800,
                    "cache_read_input_tokens": 1440 if len(body.get("messages", [])) >= 3 else 0,
                    "output_tokens": 120,
                },
            }
        )

    async def handle_openai_chat(request: web.Request) -> web.Response:
        body = await request.json()
        upstream_calls.append({"path": request.path, "body": body})
        return web.json_response(
            {
                "id": "chatcmpl_mock_01",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}}],
                "usage": {
                    "prompt_tokens": 1600,
                    "prompt_tokens_details": {
                        "cached_tokens": 1280 if len(body.get("messages", [])) >= 3 else 0
                    },
                    "completion_tokens": 90,
                },
            }
        )

    up_app = web.Application()
    up_app.router.add_post("/v1/messages", handle_anthropic_messages)
    up_app.router.add_post("/v1/chat/completions", handle_openai_chat)
    up_runner = web.AppRunner(up_app, access_log=None)
    await up_runner.setup()
    up_site = web.TCPSite(up_runner, "127.0.0.1", 0)
    await up_site.start()
    up_port = int(up_runner.addresses[0][1])

    proxy = ColdSessionProxy(
        host="127.0.0.1",
        port=0,
        upstream_base_url=f"http://127.0.0.1:{up_port}/v1",
        gate_engine=engine,
        workspace_root=repo,
        tiers=tiers,
    )
    await proxy.start()
    try:
        async with aiohttp.ClientSession() as session:
            # Cold leaf chore -> ECC routes claude-opus-4-1 down to claude-haiku-4-5
            cold_leaf = {
                "model": "claude-opus-4-1",
                "system": "You are a concise subagent helper.",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Add docstring and type hint to format_csv_header "
                            "in src/api/export_handler.py"
                        ),
                    }
                ],
            }
            async with session.post(f"{proxy.base_url}/v1/messages", json=cold_leaf) as r_leaf:
                assert r_leaf.status == 200
                b_leaf = await r_leaf.json()
                assert b_leaf["model"] == "claude-haiku-4-5"

            # Cold core high blast-radius + architectural prompt -> remains claude-opus-4-1
            cold_core = {
                "model": "claude-opus-4-1",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Architect zero-downtime concurrency migration for "
                            "TenantConnectionPool in src/storage/db_pool.py"
                        ),
                    }
                ],
            }
            async with session.post(f"{proxy.base_url}/v1/messages", json=cold_core) as r_core:
                assert r_core.status == 200
                b_core = await r_core.json()
                assert b_core["model"] == "claude-opus-4-1"

            # Cold high-R_hist file -> avoids floor downgrade
            cold_rhist = {
                "model": "claude-opus-4-1",
                "messages": [
                    {
                        "role": "user",
                        "content": "Update handle_invoice_paid in src/billing/stripe_webhook.py",
                    }
                ],
            }
            async with session.post(f"{proxy.base_url}/v1/messages", json=cold_rhist) as r_rh:
                assert r_rh.status == 200
                b_rh = await r_rh.json()
                assert b_rh["model"] != "claude-haiku-4-5"

            # Warm multi-turn Anthropic & OpenAI sessions preserve byte-identical prefix
            warm_anthropic = {
                "model": "claude-sonnet-4-5",
                "system": "STATIC_PRODUCTION_BLUEPRINT_v2",
                "messages": [
                    {"role": "user", "content": "Inspect verify_bearer_token"},
                    {"role": "assistant", "content": "Checking src/auth/jwt_validator.py."},
                    {"role": "user", "content": "Now tighten leeway_sec to 15 seconds."},
                ],
            }
            warm_digest_ant = hashlib.sha256(
                json.dumps(warm_anthropic["messages"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            async with session.post(f"{proxy.base_url}/v1/messages", json=warm_anthropic) as r_wa:
                assert r_wa.status == 200
                b_wa = await r_wa.json()
                assert b_wa["model"] == "claude-sonnet-4-5"
                assert b_wa["usage"]["cache_read_input_tokens"] == 1440

            warm_openai = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "STATIC_OPENAI_SYSTEM_PROMPT"},
                    {"role": "user", "content": "Review reconcile_payout"},
                    {"role": "assistant", "content": "Reviewed src/billing/invoice_ledger.py."},
                    {"role": "user", "content": "Run unit tests."},
                ],
            }
            warm_digest_oa = hashlib.sha256(
                json.dumps(warm_openai["messages"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            async with session.post(
                f"{proxy.base_url}/v1/chat/completions", json=warm_openai
            ) as r_wo:
                assert r_wo.status == 200
                b_wo = await r_wo.json()
                assert b_wo["model"] == "gpt-4o"
                assert b_wo["usage"]["prompt_tokens_details"]["cached_tokens"] == 1280

        assert proxy.cold_routed_requests == 3
        assert proxy.warm_bypassed_requests == 2
        assert (
            hashlib.sha256(
                json.dumps(upstream_calls[-2]["body"]["messages"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            == warm_digest_ant
        )
        assert (
            hashlib.sha256(
                json.dumps(upstream_calls[-1]["body"]["messages"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            == warm_digest_oa
        )
    finally:
        await proxy.stop()
        await up_runner.cleanup()
        healing.close()


async def test_e2e_security_firewall_sql_dotenv_traversal_and_2strike_escalation(
    tmp_path: Path,
) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "sec_repo")
    engine = GateEngine(cartridge=load_cartridge("software_engineering"), driver=ZeroDepDriver())

    # 1. Stage-2A deterministic blocks (secrets, .env.production, ZWSP, base64, traversal)
    blocked_probes: list[tuple[str, dict[str, Any]]] = [
        ("run_command", {"CommandLine": "cat .env.production"}),
        ("Write", {"file_path": str(repo / ".env.production"), "content": "STRIPE_KEY=abc"}),
        ("run_command", {"CommandLine": "cat .e\u200bnv.production"}),
        ("run_command", {"CommandLine": "rm\u200b -rf /"}),
        ("run_command", {"CommandLine": "echo Y2F0IC5lbnYucHJvZHVjdGlvbg== | base64 -d | sh"}),
        ("Edit", {"file_path": "../../etc/passwd", "new_string": "root::0:0"}),
        ("Edit", {"file_path": "..\\..\\Windows\\win.ini", "new_string": "[fonts]"}),
        ("Write", {"file_path": "src/auth/keys.py", "content": f"AWS = '{SYNTHETIC_AWS_KEY}'\n"}),
        (
            "Write",
            {
                "file_path": "src/auth/keys.py",
                "content": f"ANTHROPIC_KEY = '{SYNTHETIC_ANTHROPIC_TOKEN}'\n",
            },
        ),
        (
            "Write",
            {
                "file_path": "src/auth/keys.py",
                "content": f"GOOGLE_KEY = '{SYNTHETIC_GOOGLE_KEY}'\n",
            },
        ),
        (
            "Write",
            {
                "file_path": "src/auth/keys.py",
                "content": 'GOOGLE_TRAIL_HYPHEN = "AIzaSyDummyExampleKey00000000000000000-"\n',
            },
        ),
        (
            "Write",
            {"file_path": "src/auth/keys.py", "content": f"GH_PAT = '{SYNTHETIC_GITHUB_PAT}'\n"},
        ),
    ]
    for idx, (tool_name, tool_args) in enumerate(blocked_probes):
        verdict = await engine.evaluate_gate2_firewall(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id=f"sec-probe-{idx}",
                turn_index=2,
                workspace_root=str(repo),
                tool_name=tool_name,
                tool_args=tool_args,
                bypass_gates=True,  # [bypass-gates] MUST NEVER bypass Stage-2A
            )
        )
        assert verdict.action == VerdictAction.BLOCK, f"Expected Stage-2A block for {tool_args!r}"
        assert verdict.violation_receipt is not None
        assert verdict.violation_receipt.stage == "deterministic_2a"

    # 2. Safe .env.example template control MUST be allowed
    v_example = await engine.evaluate_gate2_firewall(
        GateEvent(
            gate=GateType.FIREWALL,
            session_id="sec-safe-env-example",
            turn_index=2,
            workspace_root=str(repo),
            tool_name="Edit",
            tool_args={
                "file_path": str(repo / ".env.example"),
                "new_string": "STRIPE_SIGNING_SECRET=replace_me\n",
            },
        )
    )
    assert v_example.action == VerdictAction.ALLOW

    # 3. Symlink escape verification (best-effort on OS privilege configuration)
    outside_secret = tmp_path / "outside_host_credentials.txt"
    outside_secret.write_text("TOP_SECRET_OUTSIDE=1\n", encoding="utf-8")
    escape_link = repo / "src" / "api" / "escape_link.txt"
    try:
        os.symlink(outside_secret, escape_link)
        has_symlink = True
    except OSError:
        has_symlink = False
    if has_symlink:
        v_sym = await engine.evaluate_gate2_firewall(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="sec-symlink",
                turn_index=2,
                workspace_root=str(repo),
                tool_name="Write",
                tool_args={"file_path": "src/api/escape_link.txt", "content": "overwrite"},
            )
        )
        assert v_sym.action == VerdictAction.BLOCK
        assert v_sym.violation_receipt is not None

    # 4. Large 130KB clean diff stays fast (< 25 ms Stage-2A SLA)
    large_clean_diff = "\n".join(
        f"def compute_tier_{i}(amount_cents: int) -> int:\n    return amount_cents + {i}"
        for i in range(2100)
    )
    assert len(large_clean_diff.encode("utf-8")) > 120_000
    t0 = time.perf_counter()
    s2a_res = check_stage2a_deterministic_security(
        {
            "tool_name": "write_to_file",
            "touched_files": ["src/billing/invoice_ledger.py"],
            "diff_text": large_clean_diff,
        },
        str(repo),
    )
    stage2a_ms = (time.perf_counter() - t0) * 1000.0
    assert s2a_res is None
    assert stage2a_ms < 25.0

    # 5. Subagent assigned_file_cage 1st strike -> block, 2nd strike -> escalate_to_human=True
    cage_event = GateEvent(
        gate=GateType.FIREWALL,
        session_id="sec-cage-2strike",
        turn_index=2,
        workspace_root=str(repo),
        assigned_file_cage=["src/observability/metrics.py"],
        tool_name="Edit",
        tool_args={
            "file_path": "src/billing/stripe_webhook.py",
            "new_string": "def handle_invoice_paid(invoice_id, amount_usd): return 0",
        },
    )
    v_s1 = await engine.evaluate_gate2_firewall(cage_event)
    assert v_s1.action == VerdictAction.BLOCK
    assert v_s1.violation_receipt is not None
    assert v_s1.violation_receipt.rule_id == "INV_02_CAGE_CONTAINMENT"
    assert v_s1.violation_receipt.strike_count == 1
    assert v_s1.violation_receipt.escalate_to_human is False

    v_s2 = await engine.evaluate_gate2_firewall(cage_event)
    assert v_s2.action == VerdictAction.BLOCK
    assert v_s2.violation_receipt is not None
    assert v_s2.violation_receipt.rule_id == "INV_02_CAGE_CONTAINMENT"
    assert v_s2.violation_receipt.strike_count == 2
    assert v_s2.violation_receipt.escalate_to_human is True


async def test_e2e_clinical_hipaa_vault_airgap_wikilinks_and_compact_reset(
    tmp_path: Path,
) -> None:
    # 1. Clinical HIPAA triage vault (tested with inline PHI docs and private dir)
    hipaa_vault = _build_clinical_hipaa_vault(tmp_path / "hipaa_vault", include_private_dir=False)
    kv_cart = load_cartridge("knowledge_vault")
    kv_indexer = AstGistIndexer(hipaa_vault, cartridge=kv_cart)
    kv_gists = kv_indexer.index_workspace(incremental=False)
    kv_by_path = {g.rel_path: g for g in kv_gists}

    # Verify [[wikilink]] reverse graph on sepsis_triage_v2.md and inline HIPAA PHI flag
    assert kv_by_path["protocols/sepsis_triage_v2.md"].fan_in_count >= 2
    assert kv_by_path["docs/shift_handoff_triage_note.md"].is_private_tier1 is True
    assert kv_by_path["protocols/sepsis_triage_v2.md"].is_private_tier1 is False
    assert kv_indexer.has_private_files() is True

    kv_fts = SqliteFtsStore(":memory:")
    kv_fts.upsert_gists(kv_gists)
    assert kv_fts.has_private_gists(kv_indexer.workspace_hash) is True

    d1_transport = _MockGeminiDecision1Transport()
    cloud_spy = Decision1Driver(
        endpoint=GEMINI_DECISIONS_URL,
        api_key=SYNTHETIC_D1_TOKEN,
        transport=d1_transport,
    )
    try:
        kv_engine = GateEngine(
            cartridge=kv_cart,
            driver=cloud_spy,
            fts_store=kv_fts,
            indexer=kv_indexer,
        )

        # Turn 1 must enforce air-gap zero-egress (0 cloud calls) & inject errata hints
        v_hipaa_t1 = await kv_engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="hipaa-sess-1",
                turn_index=1,
                workspace_root=str(hipaa_vault),
                prompt_text=(
                    "Summarize vancomycin CrCl dosing errata and sepsis triage protocol "
                    "for handoff review"
                ),
            )
        )
        assert v_hipaa_t1.cloud_egress_allowed is False
        assert len(d1_transport.calls) == 0
        assert any("antibiotic_dosing_errata.md" in h for h in v_hipaa_t1.tail_hints)

        # Turn 2 locks empty hints; Turn 6 compact_reset=True re-injects hints under air-gap
        v_hipaa_t2 = await kv_engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="hipaa-sess-1",
                turn_index=2,
                workspace_root=str(hipaa_vault),
                prompt_text="Continue formatting table.",
            )
        )
        assert v_hipaa_t2.action == VerdictAction.ALLOW
        assert v_hipaa_t2.tail_hints == []

        v_hipaa_reset = await kv_engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="hipaa-sess-1",
                turn_index=6,
                workspace_root=str(hipaa_vault),
                prompt_text="Summarize vancomycin CrCl dosing errata and sepsis triage protocol",
                metadata={"compact_reset": True},
            )
        )
        assert v_hipaa_reset.cloud_egress_allowed is False
        assert len(d1_transport.calls) == 0
        assert any("antibiotic_dosing_errata.md" in h for h in v_hipaa_reset.tail_hints)

        # Gate 4 knowledge_vault truth oracle catches broken wikilink & missing citation
        bad_note = hipaa_vault / "protocols" / "handoff_summary.md"
        bad_note.write_text(
            "# Summary\n\nSee [[nonexistent_sepsis_protocol_v9]] for dosing.\n"
            "Source: https://med.example.org/sepsis-v2\n",
            encoding="utf-8",
        )
        v_kv_bad = await kv_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="hipaa-g4-bad",
                turn_index=3,
                workspace_root=str(hipaa_vault),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                prompt_text="Summarize sepsis triage and antibiotic dosing errata.",
                touched_files=["protocols/handoff_summary.md"],
            )
        )
        assert v_kv_bad.action == VerdictAction.PROMOTE
        assert v_kv_bad.target_tier == ModelTier.MID

        good_note = hipaa_vault / "protocols" / "handoff_summary.md"
        good_note.write_text(
            "# Clinical Sepsis & Antibiotic Dosing Summary\n\n"
            "Apply [[sepsis_triage_v2]] screening and [[antibiotic_dosing_errata]] "
            "(vancomycin 25 mg/kg loading, q24h for CrCl < 30 mL/min).\n\n"
            "Source: doi:10.1001/jama.2024.1120\n",
            encoding="utf-8",
        )
        v_kv_ok = await kv_engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="hipaa-g4-ok",
                turn_index=3,
                workspace_root=str(hipaa_vault),
                current_tier=ModelTier.MID,
                strike_count=0,
                prompt_text="Summarize sepsis triage and antibiotic dosing errata.",
                touched_files=["protocols/handoff_summary.md"],
            )
        )
        assert v_kv_ok.action == VerdictAction.ALLOW
        assert len(d1_transport.calls) == 0

        # Also verify vault/private/ glob air-gap + compact_reset=True hint re-injection
        priv_vault = _build_clinical_hipaa_vault(
            tmp_path / "hipaa_priv_vault", include_private_dir=True
        )
        priv_idx = AstGistIndexer(priv_vault, cartridge=kv_cart)
        priv_fts = SqliteFtsStore(":memory:")
        priv_fts.upsert_gists(priv_idx.index_workspace(incremental=False))
        priv_engine = GateEngine(
            cartridge=kv_cart,
            driver=cloud_spy,
            fts_store=priv_fts,
            indexer=priv_idx,
        )
        v_priv_t1 = await priv_engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="hipaa-priv-1",
                turn_index=1,
                workspace_root=str(priv_vault),
                prompt_text="Check sepsis triage v2 and antibiotic dosing errata",
            )
        )
        assert v_priv_t1.cloud_egress_allowed is False
        assert v_priv_t1.tail_hints
        assert not any("vault/private" in h for h in v_priv_t1.tail_hints)
        v_priv_reset = await priv_engine.evaluate_gate1_intake(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="hipaa-priv-1",
                turn_index=6,
                workspace_root=str(priv_vault),
                prompt_text="Check sepsis triage v2 and antibiotic dosing errata",
                metadata={"compact_reset": True},
            )
        )
        assert v_priv_reset.cloud_egress_allowed is False
        assert v_priv_reset.tail_hints
        assert not any("vault/private" in h for h in v_priv_reset.tail_hints)
        assert len(d1_transport.calls) == 0
        priv_fts.close()
    finally:
        kv_fts.close()
        await cloud_spy.close()


async def test_e2e_financial_ledger_vault_double_entry_and_precision_oracles(
    tmp_path: Path,
) -> None:
    fin_vault = _build_financial_ledger_vault(tmp_path / "fin_vault")
    fin_cart = load_cartridge("financial_ops")
    fin_idx = AstGistIndexer(fin_vault, cartridge=fin_cart)
    fin_fts = SqliteFtsStore(":memory:")
    fin_fts.upsert_gists(fin_idx.index_workspace(incremental=False))
    fin_engine = GateEngine(
        cartridge=fin_cart,
        driver=ZeroDepDriver(),
        fts_store=fin_fts,
        indexer=fin_idx,
    )
    v_fin_g1 = await fin_engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="fin-g1",
            turn_index=1,
            workspace_root=str(fin_vault),
            prompt_text="Reconcile Q4 FX settlement variance errata against dual control policy",
        )
    )
    assert v_fin_g1.cloud_egress_allowed is True
    assert any("errata_fx_reconciliation.md" in h for h in v_fin_g1.tail_hints)

    # Adding payroll/ confidential record immediately engages financial_ops Tier-1 air-gap
    (fin_vault / "payroll").mkdir(parents=True)
    (fin_vault / "payroll" / "exec_comp.csv").write_text(
        "emp_id,salary\nE01,250000\n", encoding="utf-8"
    )
    fin_engine._ws_has_private_cache.clear()
    v_fin_priv = await fin_engine.evaluate_gate1_intake(
        GateEvent(
            gate=GateType.INTAKE,
            session_id="fin-g1-priv",
            turn_index=1,
            workspace_root=str(fin_vault),
            prompt_text="Reconcile Q4 FX settlement variance errata against dual control policy",
        )
    )
    assert v_fin_priv.cloud_egress_allowed is False
    assert not any("payroll" in h for h in v_fin_priv.tail_hints)
    fin_fts.close()

    v_fin_ok = await fin_engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="fin-ok",
            turn_index=2,
            workspace_root=str(fin_vault),
            current_tier=ModelTier.MID,
            strike_count=0,
            prompt_text=(
                "Verify balanced Q4 settlement journal entries in q4_settlement_balanced.json."
            ),
            touched_files=["ledger/q4_settlement_balanced.json"],
        )
    )
    assert v_fin_ok.action == VerdictAction.ALLOW

    v_fin_imbal = await fin_engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="fin-imbal",
            turn_index=2,
            workspace_root=str(fin_vault),
            current_tier=ModelTier.FLOOR,
            strike_count=0,
            prompt_text="Verify Q4 settlement ledger entries.",
            touched_files=["ledger/q4_settlement_imbalanced.json"],
        )
    )
    assert v_fin_imbal.action == VerdictAction.PROMOTE
    assert v_fin_imbal.target_tier == ModelTier.MID

    prec_bad = fin_vault / "ledger" / "fx_payout_draft.md"
    prec_bad.write_text(
        "# FX Payout Draft\n\nNet settlement payout variance: $1420.559 USD.\n",
        encoding="utf-8",
    )
    v_fin_prec = await fin_engine.evaluate_gate4_stop(
        GateEvent(
            gate=GateType.STOP_AUDIT,
            session_id="fin-prec",
            turn_index=2,
            workspace_root=str(fin_vault),
            current_tier=ModelTier.MID,
            strike_count=1,
            prompt_text="Reconcile FX variance payout amount.",
            touched_files=["ledger/fx_payout_draft.md"],
        )
    )
    assert v_fin_prec.action == VerdictAction.BLOCK
    assert v_fin_prec.violation_receipt is not None
    assert v_fin_prec.violation_receipt.escalate_to_human is True
    assert "precision" in v_fin_prec.violation_receipt.remediation.lower()


async def test_e2e_gate4_1strike_promotion_and_2strike_git_rollback(tmp_path: Path) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "git_heal_repo", init_git=True)
    assert isinstance(CoChangeGraph(repo).build_from_git_log(), dict)
    webhook = repo / "src" / "billing" / "stripe_webhook.py"
    original_code = webhook.read_text(encoding="utf-8")

    tiers = TierModelConfig(
        frontier="claude-opus-4-1",
        mid="claude-sonnet-4-5",
        floor="claude-haiku-4-5",
    )
    healing = SelfHealingEngine(
        db_path=tmp_path / "state" / "healing.db",
        workspace_root=repo,
        tiers=tiers,
    )
    try:
        engine = GateEngine(
            cartridge=load_cartridge("software_engineering"),
            tiers=tiers,
            driver=ZeroDepDriver(),
            healing=healing,
        )

        # Strike 0 (FLOOR tier) writes broken Python syntax -> PROMOTE to MID + revert hunk
        webhook.write_text("def handle_invoice_paid(invoice_id:\n    return (\n", encoding="utf-8")
        v_s0 = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="heal-sess-1",
                turn_index=2,
                workspace_root=str(repo),
                current_tier=ModelTier.FLOOR,
                strike_count=0,
                prompt_text="Fix handle_invoice_paid validation in src/billing/stripe_webhook.py",
                touched_files=["src/billing/stripe_webhook.py"],
            )
        )
        assert v_s0.action == VerdictAction.PROMOTE
        assert v_s0.target_tier == ModelTier.MID
        assert v_s0.updated_model == "claude-sonnet-4-5"
        assert webhook.read_text(encoding="utf-8") == original_code
        assert _run_git(repo, "status", "--porcelain").stdout.strip() == ""
        r_hist_1 = healing.get_failure_rate(
            "src/billing/stripe_webhook.py", workspace_root=str(repo)
        )
        assert 0.0 < r_hist_1 < 1.0

        # Strike 1 (promoted MID) halts via 1-strike circuit breaker & keeps artifact for review
        webhook.write_text(
            "def handle_invoice_paid(invoice_id):\n    return 1 + +\n", encoding="utf-8"
        )
        v_s1 = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="heal-sess-1",
                turn_index=3,
                workspace_root=str(repo),
                current_tier=ModelTier.MID,
                strike_count=1,
                prompt_text="Fix handle_invoice_paid validation in src/billing/stripe_webhook.py",
                touched_files=["src/billing/stripe_webhook.py"],
            )
        )
        assert v_s1.action == VerdictAction.BLOCK
        assert v_s1.violation_receipt is not None
        assert v_s1.violation_receipt.rule_id == "GATE4_ONE_STRIKE_CIRCUIT_BREAKER"
        assert v_s1.violation_receipt.escalate_to_human is True
        assert v_s1.violation_receipt.strike_count >= 2
        assert "1 + +" in webhook.read_text(encoding="utf-8")
        r_hist_2 = healing.get_failure_rate(
            "src/billing/stripe_webhook.py", workspace_root=str(repo)
        )
        assert r_hist_2 > r_hist_1

        # Clean valid update passes Gate 4 ALLOW cleanly
        webhook.write_text(
            original_code
            + "\ndef is_supported_currency(ccy: str) -> bool:\n    return ccy == 'USD'\n",
            encoding="utf-8",
        )
        v_clean = await engine.evaluate_gate4_stop(
            GateEvent(
                gate=GateType.STOP_AUDIT,
                session_id="heal-sess-clean",
                turn_index=2,
                workspace_root=str(repo),
                current_tier=ModelTier.MID,
                strike_count=0,
                prompt_text="Add is_supported_currency helper in src/billing/stripe_webhook.py",
                touched_files=["src/billing/stripe_webhook.py"],
                diff_text="def is_supported_currency(ccy: str) -> bool:\n    return ccy == 'USD'\n",
            )
        )
        assert v_clean.action == VerdictAction.ALLOW
    finally:
        healing.close()


async def test_e2e_daemon_sqlite_persistence_and_corrupt_db_fail_open_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sqlite3

    repo = _build_saas_billing_auth_repo(tmp_path / "persist_repo")
    qg_home = tmp_path / "persist_qg_home"

    # Session 1: start daemon_1, evaluate Turn 1, record context-miss link in WAL healing.db
    daemon_1 = QuadGateDaemon(DaemonConfig(host="127.0.0.1", port=0), home_dir=qg_home)
    await daemon_1.start()
    try:
        v1 = await daemon_1.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="persist-s1",
                turn_index=1,
                workspace_root=str(repo),
                prompt_text="Update verify_bearer_token leeway in src/auth/jwt_validator.py",
            )
        )
        assert "src/auth/jwt_validator.py" in v1.tail_hints

        # Read and mutate non-hinted rbac_middleware.py -> links jwt_validator -> rbac_middleware
        await daemon_1.handle_evaluate(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="persist-s1",
                turn_index=2,
                workspace_root=str(repo),
                tool_name="view_file",
                tool_args={"Path": "src/auth/rbac_middleware.py"},
            )
        )
        await daemon_1.handle_evaluate(
            GateEvent(
                gate=GateType.FIREWALL,
                session_id="persist-s1",
                turn_index=3,
                workspace_root=str(repo),
                tool_name="replace_file_content",
                tool_args={
                    "TargetFile": "src/auth/rbac_middleware.py",
                    "ReplacementContent": "# role audit\n",
                },
            )
        )
        assert (qg_home / "pass0.db").is_file()
    finally:
        await daemon_1.stop()

    # Session 2: restart daemon_2 on same qg_home -> persisted WAL link boosts rbac_middleware
    daemon_2 = QuadGateDaemon(DaemonConfig(host="127.0.0.1", port=0), home_dir=qg_home)
    await daemon_2.start()
    try:
        v2 = await daemon_2.handle_evaluate(
            GateEvent(
                gate=GateType.INTAKE,
                session_id="persist-s2",
                turn_index=1,
                workspace_root=str(repo),
                prompt_text="Update verify_bearer_token leeway in src/auth/jwt_validator.py",
            )
        )
        assert "src/auth/jwt_validator.py" in v2.tail_hints
        assert "src/auth/rbac_middleware.py" in v2.tail_hints

        # Simulate mid-flight SQLite corruption inside daemon FTS store -> shim fails open on Gate 1
        monkeypatch.setenv("QUADGATE_DAEMON_HOST", "127.0.0.1")
        monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(daemon_2.bound_port))
        monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(daemon_2.token_path))
        monkeypatch.setenv("QUADGATE_WORKSPACE_ROOT", str(repo))
        monkeypatch.setenv("QUADGATE_SHIM_TIMEOUT_MS", "3000")

        def _raise_corrupt_db(*_args: object, **_kwargs: object) -> Any:
            raise sqlite3.DatabaseError("database disk image is malformed")

        monkeypatch.setattr(daemon_2._fts_store, "search_candidates", _raise_corrupt_db)

        rc_open = await asyncio.to_thread(
            run_shim,
            "antigravity",
            "intake",
            json.dumps(
                {
                    "conversationId": "corrupt-db-s3",
                    "turnIndex": 1,
                    "workspaceRoot": str(repo),
                    "lastUserInput": "Check verify_bearer_token",
                }
            ),
        )
        assert rc_open == 0
        assert json.loads(capsys.readouterr().out.strip()) == {}

        # Stage-2A security STILL fails closed even while daemon DB is corrupted
        rc_closed = await asyncio.to_thread(
            run_shim,
            "claude_code",
            "firewall",
            json.dumps(
                {
                    "session_id": "corrupt-db-s3",
                    "cwd": str(repo),
                    "tool_name": "Bash",
                    "tool_input": {"command": "cat .env.production"},
                }
            ),
        )
        assert rc_closed == 2
        assert "INV_01" in capsys.readouterr().err
    finally:
        await daemon_2.stop()


async def test_e2e_qg_shim_subprocess_over_live_daemon_and_corrupt_db_fail_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "shim_repo")
    qg_home = tmp_path / "qg_home"

    daemon = QuadGateDaemon(
        DaemonConfig(host="127.0.0.1", port=0),
        home_dir=qg_home,
        tiers=TierModelConfig(
            frontier="claude-opus-4-1",
            mid="claude-sonnet-4-5",
            floor="claude-haiku-4-5",
        ),
    )

    def _run_shim_sub(
        harness: str,
        gate: str,
        payload: dict[str, Any],
        *,
        port: int,
        token_path: Path,
        timeout_ms: int = 3000,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["QUADGATE_DAEMON_HOST"] = "127.0.0.1"
        env["QUADGATE_DAEMON_PORT"] = str(port)
        env["QUADGATE_TOKEN_PATH"] = str(token_path)
        env["QUADGATE_WORKSPACE_ROOT"] = str(repo)
        env["QUADGATE_SHIM_TIMEOUT_MS"] = str(timeout_ms)
        existing_pp = env.get("PYTHONPATH", "")
        repo_str = str(REPO_ROOT)
        if repo_str not in existing_pp.split(os.pathsep):
            env["PYTHONPATH"] = f"{repo_str}{os.pathsep}{existing_pp}" if existing_pp else repo_str
        return subprocess.run(
            [sys.executable, "-m", "quadgate.shim", "--harness", harness, "--gate", gate],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
            check=False,
        )

    await daemon.start()
    try:
        await daemon._get_or_create_workspace(str(repo))
        port = daemon.bound_port
        token_path = daemon.token_path
        monkeypatch.setenv("QUADGATE_DAEMON_HOST", "127.0.0.1")
        monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(port))
        monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(token_path))
        monkeypatch.setenv("QUADGATE_WORKSPACE_ROOT", str(repo))
        monkeypatch.setenv("QUADGATE_SHIM_TIMEOUT_MS", "3000")

        # 1. Antigravity Turn 1 intake via real `python -m quadgate.shim` subprocess over live TCP
        p_t1 = await asyncio.to_thread(
            _run_shim_sub,
            "antigravity",
            "intake",
            {
                "conversationId": "sub-e2e-1",
                "turnIndex": 1,
                "workspaceRoot": str(repo),
                "lastUserInput": (
                    "Fix verify_bearer_token clock leeway bug in src/auth/jwt_validator.py"
                ),
            },
            port=port,
            token_path=token_path,
        )
        assert p_t1.returncode == 0
        out_t1 = json.loads(p_t1.stdout.strip())
        assert out_t1["decision"] == "allow"
        assert "jwt_validator.py" in out_t1["additionalContext"]

        # 2. Turn 2 KV-cache lock + Turn 3 compact_reset=True over live daemon TCP
        rc_t2 = await asyncio.to_thread(
            run_shim,
            "antigravity",
            "intake",
            json.dumps(
                {
                    "conversationId": "sub-e2e-1",
                    "turnIndex": 2,
                    "workspaceRoot": str(repo),
                    "lastUserInput": "Proceed with the edit.",
                }
            ),
        )
        assert rc_t2 == 0
        assert json.loads(capsys.readouterr().out.strip()) == {}

        rc_reset = await asyncio.to_thread(
            run_shim,
            "antigravity",
            "intake",
            json.dumps(
                {
                    "conversationId": "sub-e2e-1",
                    "turnIndex": 3,
                    "workspaceRoot": str(repo),
                    "lastUserInput": "Fix verify_bearer_token in src/auth/jwt_validator.py",
                    "metadata": {"compact_reset": True},
                }
            ),
        )
        assert rc_reset == 0
        reset_out = json.loads(capsys.readouterr().out.strip())
        assert "jwt_validator.py" in reset_out["additionalContext"]

        # 3. Claude Code subagent_governor via `python -m quadgate.shim` -> ECC downgrade
        p_g3 = await asyncio.to_thread(
            _run_shim_sub,
            "claude_code",
            "subagent_governor",
            {
                "session_id": "sub-e2e-1",
                "cwd": str(repo),
                "tool_name": "Task",
                "tool_input": {
                    "model": "claude-opus-4-1",
                    "prompt": "Add docstring to format_csv_header in src/api/export_handler.py",
                },
            },
            port=port,
            token_path=token_path,
        )
        assert p_g3.returncode == 0
        out_g3 = json.loads(p_g3.stdout.strip())
        assert out_g3["hookSpecificOutput"]["updatedInput"]["model"] == "claude-haiku-4-5"

        # 4. Corrupt workspace noise / syntax error / corrupt sqlite -> fail-open indexing + intake
        (repo / ".quadgate").mkdir(exist_ok=True)
        (repo / ".quadgate" / "corrupt_index.sqlite").write_bytes(
            b"CORRUPT_SQLITE_HEADER_\x00\xff\xfe" * 20
        )
        (repo / "broken_noise.py").write_text("def broken(x:\n    return (((\n", encoding="utf-8")
        (repo / "blob.bin").write_bytes(b"\x00\x01\xfe\xffPNG\r\n" * 16)

        resilient_idx = AstGistIndexer(repo, cartridge=daemon.cartridge)
        gists_with_noise = resilient_idx.index_workspace(incremental=False)
        assert any(g.rel_path == "src/auth/jwt_validator.py" for g in gists_with_noise)

        rc_noise = await asyncio.to_thread(
            run_shim,
            "antigravity",
            "intake",
            json.dumps(
                {
                    "conversationId": "sub-e2e-noise",
                    "turnIndex": 1,
                    "workspaceRoot": str(repo),
                    "lastUserInput": "Inspect verify_bearer_token in src/auth/jwt_validator.py",
                }
            ),
        )
        assert rc_noise == 0
        assert "jwt_validator.py" in json.loads(capsys.readouterr().out.strip()).get(
            "additionalContext", ""
        )
    finally:
        await daemon.stop()

    # 5. Daemon dead -> Gate 1 fails open (exit 0, {}), Stage-2A fails closed (exit 2 subprocess)
    dead_port = _unused_tcp_port()
    monkeypatch.setenv("QUADGATE_DAEMON_PORT", str(dead_port))
    monkeypatch.setenv("QUADGATE_TOKEN_PATH", str(qg_home / "missing.token"))
    monkeypatch.setenv("QUADGATE_SHIM_TIMEOUT_MS", "120")
    rc_dead_intake = await asyncio.to_thread(
        run_shim,
        "antigravity",
        "intake",
        json.dumps(
            {
                "conversationId": "sub-dead-1",
                "workspaceRoot": str(repo),
                "lastUserInput": "Check auth",
            }
        ),
    )
    assert rc_dead_intake == 0
    assert json.loads(capsys.readouterr().out.strip()) == {}

    p_dead_sec = await asyncio.to_thread(
        _run_shim_sub,
        "claude_code",
        "firewall",
        {
            "session_id": "sub-dead-2",
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "cat .env.production"},
        },
        port=dead_port,
        token_path=qg_home / "missing.token",
        timeout_ms=120,
    )
    assert p_dead_sec.returncode == 2
    assert "INV_01" in p_dead_sec.stderr


def test_e2e_cli_init_daemon_lifecycle_status_stats_and_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _build_saas_billing_auth_repo(tmp_path / "cli_repo")
    qg_home = tmp_path / "cli_qg_home"
    monkeypatch.setenv("QUADGATE_HOME", str(qg_home))
    monkeypatch.delenv("QUADGATE_TOKEN_PATH", raising=False)
    monkeypatch.delenv("QUADGATE_STATE_PATH", raising=False)

    assert (
        cli_main(
            [
                "init",
                "--workspace",
                str(repo),
                "--harness",
                "claude-code",
                "--cartridge",
                "software_engineering",
                "--json",
            ]
        )
        == 0
    )
    init_out = json.loads(capsys.readouterr().out)
    assert init_out["status"] == "ok"
    assert init_out["indexed_files"] >= 11
    assert (repo / ".claude" / "settings.json").is_file()
    assert (repo / ".quadgate" / "pass0.db").is_file()

    try:
        assert cli_main(["daemon", "start", "--port", "0", "--json"]) == 0
        start_out = json.loads(capsys.readouterr().out)
        assert start_out["status"] == "started"
        assert start_out["port"] > 0

        assert cli_main(["status", "--json"]) == 0
        status_out = json.loads(capsys.readouterr().out)
        assert status_out["status"] == "running"

        assert cli_main(["stats", "--json"]) == 0
        stats_out = json.loads(capsys.readouterr().out)
        assert stats_out["status"] == "ok"
        assert stats_out["total_requests"] == 0

        assert cli_main(["daemon", "stop", "--json"]) == 0
        stop_out = json.loads(capsys.readouterr().out)
        assert stop_out["status"] == "stopped"
        assert stop_out["was_running"] is True

        assert cli_main(["daemon", "status", "--json"]) == 0
        after_out = json.loads(capsys.readouterr().out)
        assert after_out["status"] == "stopped"
    finally:
        if (qg_home / "daemon.json").exists():
            cli_main(["daemon", "stop"])

    assert cli_main(["doctor", "--json"]) == 0
    doc_out = json.loads(capsys.readouterr().out)
    assert doc_out["status"] == "ok"
    assert doc_out["health_latency_ms"] < 10.0
    assert doc_out["clean_shutdown"] is True
