"""Deterministic generator for QuadGate Stress Set 1 (stress_long_context.jsonl)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _sys_path_header() -> str:
    return (
        "import sys\n"
        "from pathlib import Path\n\n"
        "_ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(_ROOT))\n\n"
    )


def _make_usage(
    provider: str,
    uncached_in: int,
    cache_read: int,
    cache_write: int,
    output_tokens: int,
) -> dict[str, Any]:
    if provider == "anthropic":
        return {
            "input_tokens": int(uncached_in),
            "cache_read_input_tokens": int(cache_read),
            "cache_creation_input_tokens": int(cache_write),
            "output_tokens": int(output_tokens),
        }
    if provider == "openai":
        return {
            "prompt_tokens": int(uncached_in + cache_read),
            "prompt_tokens_details": {"cached_tokens": int(cache_read)},
            "completion_tokens": int(output_tokens),
        }
    if provider == "gemini":
        return {
            "promptTokenCount": int(uncached_in + cache_read),
            "cachedContentTokenCount": int(cache_read),
            "candidatesTokenCount": int(output_tokens),
        }
    raise ValueError(f"Unsupported provider: {provider!r}")


_MODEL_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "frontier": "claude-opus-4-1",
        "mid": "claude-sonnet-4-5",
        "floor": "claude-3-5-haiku",
    },
    "openai": {
        "frontier": "gpt-4o",
        "mid": "gpt-4o",
        "floor": "gpt-4o-mini",
    },
    "gemini": {
        "frontier": "gemini-2.5-pro",
        "mid": "gemini-2.5-flash",
        "floor": "gemini-2.5-flash-lite",
    },
}


def build_stress_replay_trace(
    idx: int,
    *,
    provider: str,
    task_kind: str,
    subagent_chore: bool = False,
    leaf_chore: bool = False,
    simulates_promotion: bool = False,
    air_gapped: bool = False,
    prefer_decision1: bool = False,
) -> dict[str, Any]:
    """Generate realistic multi-turn long-context baseline vs quadgate replay traces."""
    models = _MODEL_TIERS[provider]
    if leaf_chore:
        baseline_model = models["frontier"]
        quadgate_model = models["mid"] if simulates_promotion else models["floor"]
        retry_model = models["floor"]
    elif subagent_chore:
        baseline_model = models["frontier"]
        quadgate_model = models["frontier"] if simulates_promotion else models["mid"]
        retry_model = models["mid"]
    else:
        baseline_model = models["frontier"] if idx % 3 != 0 else models["mid"]
        quadgate_model = baseline_model
        retry_model = models["mid"] if quadgate_model == models["frontier"] else models["floor"]

    v1 = (idx * 89) % 470
    v2 = (idx * 151) % 340
    v3 = (idx * 67) % 210

    # Baseline: 6-10 turns of blind exploration across 25-36 files / 10-14 docs.
    baseline_turn_count = 6 + (idx % 5)  # 6, 7, 8, 9, or 10 turns
    base_prefix = 6200 + v1 * 5 + (1400 if task_kind == "knowledge" else 0)
    baseline_turns: list[dict[str, Any]] = []
    for t_idx in range(1, baseline_turn_count + 1):
        if t_idx == 1:
            uncached = base_prefix + 1450 + v2
            cache_read = 0
            cache_write = 1800 + v3 if provider == "anthropic" else 0
            out_tok = 680 + (v3 % 210)
        else:
            cache_read = int((base_prefix + t_idx * 420) * (0.22 + 0.03 * (idx % 4)))
            uncached = 3400 + (t_idx * 560) + v2 + (v1 // 2)
            cache_write = 450 + (v3 // 2) if provider == "anthropic" else 0
            out_tok = 520 + ((t_idx * 79 + v2) % 380)
        latency = round(1750.0 + t_idx * 230.0 + (v1 * 1.9) + (out_tok * 1.2), 2)
        baseline_turns.append(
            {
                "turn_index": t_idx,
                "model": baseline_model,
                "latency_ms": latency,
                "usage": _make_usage(provider, uncached, cache_read, cache_write, out_tok),
            }
        )
    baseline_wall_ms = round(sum(t["latency_ms"] for t in baseline_turns) + 180.0 + idx * 9.0, 2)

    # QuadGate: 2-4 turns with Pass-0 + Turn-1 [QUADGATE_CONTEXT_HINTS] and >= 85% cache hit on turns 2..N.
    qg_turn_count = 2 + (idx % 3)  # 2, 3, or 4 turns
    qg_prefix = base_prefix + 380
    qg_turns: list[dict[str, Any]] = []
    for t_idx in range(1, qg_turn_count + 1):
        if t_idx == 1:
            uncached = 1280 + (v1 // 2) if provider == "anthropic" else qg_prefix + 560 + (v2 // 2)
            cache_read = 0
            cache_write = qg_prefix if provider == "anthropic" else 0
            out_tok = 460 + (v3 % 180)
        else:
            warm_total = qg_prefix + (t_idx - 1) * 780
            cache_read = int(warm_total * 0.91) + 920
            uncached = 240 + (t_idx * 65) + (v3 % 90)
            cache_write = 160 + (v3 % 55) if provider == "anthropic" else 0
            out_tok = 360 + ((t_idx * 53 + v1) % 230)
        speed_factor = 0.72 if leaf_chore else (0.85 if subagent_chore else 0.93)
        latency = round((1220.0 + t_idx * 165.0 + (v2 * 1.15) + (out_tok * 0.95)) * speed_factor, 2)
        qg_turns.append(
            {
                "turn_index": t_idx,
                "model": quadgate_model,
                "latency_ms": latency,
                "usage": _make_usage(provider, uncached, cache_read, cache_write, out_tok),
            }
        )

    retry_turns: list[dict[str, Any]] = []
    if simulates_promotion:
        retry_uncached = 1720 + v2 if provider != "anthropic" else 880 + (v2 // 2)
        retry_cache_write = 3100 + v1 if provider == "anthropic" else 0
        retry_out = 430 + (v3 % 130)
        retry_latency = round(1280.0 + (v1 * 0.85) + (retry_out * 0.9), 2)
        retry_turns.append(
            {
                "turn_index": 1,
                "model": retry_model,
                "latency_ms": retry_latency,
                "usage": _make_usage(provider, retry_uncached, 0, retry_cache_write, retry_out),
            }
        )

    if air_gapped or not prefer_decision1:
        classifier_driver = "onnx_local"
        classifier_cost_usd = 0.0
        classifier_latency_ms = round(29.0 + ((idx * 1.7) % 24.0), 2)
    else:
        classifier_driver = "decision1"
        classifier_cost_usd = round(0.00024 + ((idx % 6) * 0.00004), 6)
        classifier_latency_ms = round(192.0 + ((idx * 11.3) % 38.0), 2)

    hook_ipc_ms = round(3.2 + (qg_turn_count * 1.65) + ((idx % 5) * 0.45), 2)
    qg_wall_ms = round(
        sum(t["latency_ms"] for t in qg_turns)
        + sum(t["latency_ms"] for t in retry_turns)
        + hook_ipc_ms
        + classifier_latency_ms
        + 42.0,
        2,
    )

    return {
        "baseline": {
            "provider": provider,
            "model": baseline_model,
            "wall_clock_ms": baseline_wall_ms,
            "turns": baseline_turns,
            "retry_turns": [],
        },
        "quadgate": {
            "provider": provider,
            "model": quadgate_model,
            "classifier_driver": classifier_driver,
            "classifier_cost_usd": classifier_cost_usd,
            "hook_ipc_ms": hook_ipc_ms,
            "classifier_latency_ms": classifier_latency_ms,
            "wall_clock_ms": qg_wall_ms,
            "turns": qg_turns,
            "retry_turns": retry_turns,
        },
    }


def build_coding_workspace(
    slug: str,
    domain_topic: str,
    *,
    core_import_target: str,
    core_symbol: str,
    domain_import_target: str,
    domain_symbol: str,
    service_import_target: str,
    service_symbol: str,
    high_fanin_targets: list[tuple[str, str]] | None = None,
    extra_files: dict[str, str],
    broken_neighbor: bool = False,
    distractor_count: int = 4,
) -> dict[str, str]:
    """Build a 27-33 file realistic multi-layer workspace (>=18KB) with 5-hop imports."""
    files: dict[str, str] = {}
    for pkg in ("core", "domain", "services", "adapters", "infra", "legacy_v1", "tests"):
        files[f"{pkg}/__init__.py"] = f'"""Package {pkg} for {slug} ({domain_topic})."""\n'

    fanin_imports = ""
    if high_fanin_targets:
        fanin_imports = "\n".join(
            f"from {mod} import {sym}" for mod, sym in high_fanin_targets
        ) + "\n"

    files[f"core/{slug}_config.py"] = (
        f'"""Runtime configuration and envelope defaults for {domain_topic}."""\n\n'
        "from dataclasses import dataclass, field\n\n"
        "@dataclass(frozen=True)\n"
        "class SubsystemConfig:\n"
        "    \"\"\"Immutable runtime bounds for multi-hop execution.\"\"\"\n\n"
        f"    subsystem: str = '{slug}'\n"
        "    max_batch_size: int = 128\n"
        "    strict_mode: bool = True\n"
        "    timeout_ms: int = 2500\n"
        "    retry_limit: int = 3\n"
        "    region: str = 'us-east-1'\n"
        "    feature_flags: tuple[str, ...] = ('pass0', 'warm_kv', 'strict_oracle')\n\n"
        "    def effective_timeout_s(self) -> float:\n"
        "        \"\"\"Return bounded timeout in seconds.\"\"\"\n"
        "        return max(0.05, float(self.timeout_ms) / 1000.0)\n\n"
        "    def supports_flag(self, name: str) -> bool:\n"
        "        \"\"\"Check whether a runtime feature flag is enabled.\"\"\"\n"
        "        return name in self.feature_flags\n\n"
        "def default_subsystem_config() -> SubsystemConfig:\n"
        "    \"\"\"Return validated SubsystemConfig instance.\"\"\"\n"
        "    return SubsystemConfig()\n"
    )
    files[f"core/{slug}_errors.py"] = (
        f'"""Typed error hierarchy for {domain_topic}."""\n\n'
        "class SubsystemError(RuntimeError):\n"
        "    \"\"\"Base error raised by core and domain operations.\"\"\"\n\n"
        "    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:\n"
        "        super().__init__(message)\n"
        "        self.code = code\n"
        "        self.message = message\n"
        "        self.retryable = retryable\n\n"
        "    def to_dict(self) -> dict[str, object]:\n"
        "        \"\"\"Serialize error metadata for structured telemetry logs.\"\"\"\n"
        "        return {'code': self.code, 'message': self.message, 'retryable': self.retryable}\n\n"
        "class ValidationSubsystemError(SubsystemError):\n"
        "    \"\"\"Raised when domain invariants reject an operation.\"\"\"\n\n"
        "class ConflictSubsystemError(SubsystemError):\n"
        "    \"\"\"Raised on state conflict or stale sequence mismatch.\"\"\"\n\n"
        "class BoundarySubsystemError(SubsystemError):\n"
        "    \"\"\"Raised when adapter boundary validation rejects payload fields.\"\"\"\n"
    )
    files[f"core/{slug}_telemetry.py"] = (
        f'"""Deterministic metric accumulator for {domain_topic}."""\n\n'
        f"from core.{slug}_config import default_subsystem_config\n"
        f"{fanin_imports}\n"
        "class CoreTelemetryCounter:\n"
        "    \"\"\"Tracks latency histograms and counter totals per subsystem.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.config = default_subsystem_config()\n"
        "        self.counts: dict[str, int] = {}\n"
        "        self.samples_ms: list[float] = []\n"
        "        self.tags: dict[str, str] = {'subsystem': self.config.subsystem, 'region': self.config.region}\n\n"
        "    def increment(self, metric_name: str, delta: int = 1) -> int:\n"
        "        \"\"\"Increment counter metric_name by delta and return updated total.\"\"\"\n"
        "        self.counts[metric_name] = self.counts.get(metric_name, 0) + delta\n"
        "        return self.counts[metric_name]\n\n"
        "    def observe_ms(self, latency_ms: float) -> None:\n"
        "        \"\"\"Record a non-negative latency observation in milliseconds.\"\"\"\n"
        "        if latency_ms >= 0.0:\n"
        "            self.samples_ms.append(float(latency_ms))\n\n"
        "    def summary(self) -> dict[str, object]:\n"
        "        \"\"\"Return summary counts and average observed latency.\"\"\"\n"
        "        avg = sum(self.samples_ms) / len(self.samples_ms) if self.samples_ms else 0.0\n"
        "        return {'counts': dict(self.counts), 'avg_ms': round(avg, 3), 'tags': dict(self.tags)}\n"
    )
    files[f"domain/{slug}_audit_log.py"] = (
        f'"""Domain audit journal linking core types for {domain_topic}."""\n\n'
        f"from core.{slug}_errors import ValidationSubsystemError\n"
        f"from {core_import_target} import {core_symbol}\n"
        f"{fanin_imports}\n"
        "class DomainAuditJournal:\n"
        "    \"\"\"Append-only structural audit log for domain transitions.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.entries: list[tuple[str, str]] = []\n"
        f"        self._core_ref = {core_symbol}\n\n"
        "    def append(self, actor: str, action: str) -> None:\n"
        "        \"\"\"Validate non-empty actor/action and append immutable audit tuple.\"\"\"\n"
        "        if not actor.strip() or not action.strip():\n"
        "            raise ValidationSubsystemError('EMPTY_AUDIT', 'actor and action are required')\n"
        "        self.entries.append((actor.strip(), action.strip()))\n\n"
        "    def recent(self, limit: int = 10) -> list[tuple[str, str]]:\n"
        "        \"\"\"Return up to limit most recent audit entries.\"\"\"\n"
        "        return list(self.entries[-max(1, limit):])\n\n"
        "    def count_by_actor(self, actor: str) -> int:\n"
        "        \"\"\"Count logged transitions triggered by actor.\"\"\"\n"
        "        target = actor.strip()\n"
        "        return sum(1 for who, _ in self.entries if who == target)\n"
    )
    files[f"domain/{slug}_policy_checks.py"] = (
        f'"""Domain invariant helpers for {domain_topic}."""\n\n'
        f"from core.{slug}_config import SubsystemConfig\n"
        f"from domain.{slug}_audit_log import DomainAuditJournal\n"
        f"{fanin_imports}\n"
        "def verify_batch_bounds(count: int, config: SubsystemConfig) -> bool:\n"
        "    \"\"\"Verify batch size stays within SubsystemConfig bounds.\"\"\"\n"
        "    return 0 <= count <= config.max_batch_size\n\n"
        "def verify_strict_flags(config: SubsystemConfig, required: tuple[str, ...]) -> list[str]:\n"
        "    \"\"\"Return any missing required feature flags from SubsystemConfig.\"\"\"\n"
        "    return [flag for flag in required if not config.supports_flag(flag)]\n\n"
        "def record_policy_check(journal: DomainAuditJournal, check_name: str, passed: bool) -> None:\n"
        "    \"\"\"Record policy outcome to DomainAuditJournal.\"\"\"\n"
        "    journal.append('policy_guard', f'{check_name}:{passed}')\n"
    )
    files[f"services/{slug}_dispatcher.py"] = (
        f'"""Service dispatcher layer importing domain and core layers for {domain_topic}."""\n\n'
        f"from core.{slug}_telemetry import CoreTelemetryCounter\n"
        f"from {domain_import_target} import {domain_symbol}\n"
        f"{fanin_imports}\n"
        "class ServiceHealthDispatcher:\n"
        "    \"\"\"Coordinates health checks across service and domain components.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.telemetry = CoreTelemetryCounter()\n"
        f"        self.domain_symbol = {domain_symbol}\n"
        "        self.registered_components: list[str] = []\n\n"
        "    def register(self, component: str) -> None:\n"
        "        \"\"\"Register a named subsystem component for health polling.\"\"\"\n"
        "        cleaned = component.strip()\n"
        "        if cleaned and cleaned not in self.registered_components:\n"
        "            self.registered_components.append(cleaned)\n\n"
        "    def heartbeat(self, component: str) -> dict[str, object]:\n"
        "        \"\"\"Emit sequential heartbeat telemetry for component.\"\"\"\n"
        "        self.register(component)\n"
        "        count = self.telemetry.increment(f'hb.{component}')\n"
        "        return {'component': component, 'sequence': count, 'status': 'ok'}\n"
    )
    files[f"services/{slug}_diagnostics.py"] = (
        f'"""Service diagnostic snapshot builder for {domain_topic}."""\n\n'
        f"from domain.{slug}_policy_checks import verify_batch_bounds, verify_strict_flags\n"
        f"from {service_import_target} import {service_symbol}\n"
        f"{fanin_imports}\n"
        "class ServiceDiagnostics:\n"
        "    \"\"\"Summarizes runtime invariants across service calls.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        f"        self.target_ref = {service_symbol}\n"
        "        self.bound_check = verify_batch_bounds\n"
        "        self.flag_check = verify_strict_flags\n\n"
        "    def snapshot(self) -> dict[str, str]:\n"
        "        \"\"\"Return diagnostic metadata dictionary for active service target.\"\"\"\n"
        f"        return {{'slug': '{slug}', 'service': str(getattr(self.target_ref, '__name__', 'svc'))}}\n"
    )
    files[f"adapters/{slug}_http_adapter.py"] = (
        f'"""HTTP boundary adapter translating requests into service calls for {domain_topic}."""\n\n'
        f"from services.{slug}_dispatcher import ServiceHealthDispatcher\n"
        f"from {service_import_target} import {service_symbol}\n"
        f"{fanin_imports}\n"
        "class HttpBoundaryAdapter:\n"
        "    \"\"\"Normalizes inbound HTTP headers and delegates to service layer.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.dispatcher = ServiceHealthDispatcher()\n"
        f"        self.service_ref = {service_symbol}\n\n"
        "    def normalize_headers(self, headers: dict[str, str]) -> dict[str, str]:\n"
        "        \"\"\"Lowercase header keys and trim leading/trailing whitespace.\"\"\"\n"
        "        return {k.strip().lower(): v.strip() for k, v in headers.items() if k.strip()}\n\n"
        "    def handle_health(self, request_id: str) -> dict[str, object]:\n"
        "        \"\"\"Process synthetic HTTP health probe and return status dictionary.\"\"\"\n"
        "        payload = self.dispatcher.heartbeat('http')\n"
        "        payload['request_id'] = request_id\n"
        "        return payload\n"
    )
    files[f"adapters/{slug}_cli_adapter.py"] = (
        f'"""CLI adapter entrypoint wrapping HTTP and service layers for {domain_topic}."""\n\n'
        f"from adapters.{slug}_http_adapter import HttpBoundaryAdapter\n"
        f"{fanin_imports}\n"
        "class CliCommandAdapter:\n"
        "    \"\"\"Formats deterministic CLI diagnostics from HttpBoundaryAdapter.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.http_adapter = HttpBoundaryAdapter()\n\n"
        "    def format_status(self, request_id: str) -> str:\n"
        "        \"\"\"Format single-line health probe summary string.\"\"\"\n"
        "        info = self.http_adapter.handle_health(request_id)\n"
        "        return f\"[{info['component']}] req={info['request_id']} seq={info['sequence']}\"\n\n"
        "    def parse_kv_flags(self, argv: list[str]) -> dict[str, str]:\n"
        "        \"\"\"Parse --key=value CLI overrides into a clean dictionary.\"\"\"\n"
        "        parsed: dict[str, str] = {}\n"
        "        for tok in argv:\n"
        "            if tok.startswith('--') and '=' in tok:\n"
        "                k, v = tok[2:].split('=', 1)\n"
        "                parsed[k.strip()] = v.strip()\n"
        "        return parsed\n"
    )
    files[f"adapters/{slug}_webhook_adapter.py"] = (
        f'"""Outbound webhook envelope serializer for {domain_topic}."""\n\n'
        f"from adapters.{slug}_cli_adapter import CliCommandAdapter\n"
        f"{fanin_imports}\n"
        "class WebhookEnvelopeAdapter:\n"
        "    \"\"\"Builds signed webhook envelopes from upstream adapters.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.cli = CliCommandAdapter()\n"
        "        self.sent_events: list[str] = []\n\n"
        "    def build_envelope(self, event_id: str) -> dict[str, str]:\n"
        "        \"\"\"Create deterministic outbound webhook envelope dictionary.\"\"\"\n"
        "        self.sent_events.append(event_id)\n"
        "        return {'event_id': event_id, 'summary': self.cli.format_status(event_id)}\n"
    )
    files[f"infra/{slug}_memory_repo.py"] = (
        f'"""In-memory infrastructure repository for {domain_topic}."""\n\n'
        f"from adapters.{slug}_webhook_adapter import WebhookEnvelopeAdapter\n"
        f"from core.{slug}_errors import ConflictSubsystemError\n"
        f"{fanin_imports}\n"
        "class InMemoryRecordRepo:\n"
        "    \"\"\"Stores versioned key-value records with optimistic revision check.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self._rows: dict[str, tuple[int, dict[str, str]]] = {}\n"
        "        self._webhook = WebhookEnvelopeAdapter()\n\n"
        "    def put(self, key: str, expected_rev: int, value: dict[str, str]) -> int:\n"
        "        \"\"\"Store value if current revision matches expected_rev, else raise ConflictSubsystemError.\"\"\"\n"
        "        cur_rev, _ = self._rows.get(key, (0, {}))\n"
        "        if cur_rev != expected_rev:\n"
        "            raise ConflictSubsystemError('REV_MISMATCH', f'Expected {expected_rev}, got {cur_rev}')\n"
        "        new_rev = cur_rev + 1\n"
        "        self._rows[key] = (new_rev, dict(value))\n"
        "        return new_rev\n\n"
        "    def get(self, key: str) -> dict[str, str] | None:\n"
        "        \"\"\"Retrieve stored record copy for key or None if absent.\"\"\"\n"
        "        row = self._rows.get(key)\n"
        "        return dict(row[1]) if row is not None else None\n\n"
        "    def keys(self) -> list[str]:\n"
        "        \"\"\"Return sorted stored keys.\"\"\"\n"
        "        return sorted(self._rows)\n"
    )
    files[f"infra/{slug}_spool.py"] = (
        f'"""Buffered local event spool for {domain_topic}."""\n\n'
        f"from infra.{slug}_memory_repo import InMemoryRecordRepo\n"
        f"{fanin_imports}\n"
        "class LocalEventSpool:\n"
        "    \"\"\"Queues outbound records before checkpoint commit.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.repo = InMemoryRecordRepo()\n"
        "        self.pending: list[dict[str, str]] = []\n\n"
        "    def enqueue(self, item: dict[str, str]) -> int:\n"
        "        \"\"\"Append item copy to spool queue and return queue length.\"\"\"\n"
        "        self.pending.append(dict(item))\n"
        "        return len(self.pending)\n\n"
        "    def drain(self) -> list[dict[str, str]]:\n"
        "        \"\"\"Drain all pending spooled items in FIFO order.\"\"\"\n"
        "        out = list(self.pending)\n"
        "        self.pending.clear()\n"
        "        return out\n"
    )
    files[f"infra/{slug}_checkpoint.py"] = (
        f'"""Checkpoint registry coordinating LocalEventSpool for {domain_topic}."""\n\n'
        f"from infra.{slug}_spool import LocalEventSpool\n"
        f"{fanin_imports}\n"
        "class CheckpointRegistry:\n"
        "    \"\"\"Tracks monotonic checkpoint offsets per partition.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.spool = LocalEventSpool()\n"
        "        self.offsets: dict[str, int] = {}\n\n"
        "    def advance(self, partition: str, offset: int) -> int:\n"
        "        \"\"\"Advance partition offset monotonically and return stored offset.\"\"\"\n"
        "        prev = self.offsets.get(partition, 0)\n"
        "        if offset < prev:\n"
        "            return prev\n"
        "        self.offsets[partition] = offset\n"
        "        return offset\n\n"
        "    def snapshot_offsets(self) -> dict[str, int]:\n"
        "        \"\"\"Return copy of recorded partition offsets.\"\"\"\n"
        "        return dict(self.offsets)\n"
    )

    for d_idx in range(1, max(5, distractor_count) + 1):
        files[f"legacy_v1/v1_{slug}_compat_{d_idx}.py"] = (
            f'"""Legacy v1 deprecated compatibility shim #{d_idx} (do not edit for v2 {domain_topic})."""\n\n'
            f"class LegacyV1CompatHandler{d_idx}:\n"
            f"    \"\"\"Deprecated v1 handler #{d_idx} retained solely for archival reference.\"\"\"\n\n"
            "    def __init__(self, label: str = 'v1') -> None:\n"
            "        self.label = label\n"
            "        self.invocations = 0\n\n"
            "    def legacy_process(self, payload: dict[str, object]) -> dict[str, object]:\n"
            "        \"\"\"Execute deprecated v1 pass-through transform.\"\"\"\n"
            "        self.invocations += 1\n"
            f"        return {{'legacy_version': 1, 'slot': {d_idx}, 'calls': self.invocations, 'echo': dict(payload)}}\n\n"
            f"    def legacy_validate_{d_idx}(self, value: object) -> bool:\n"
            "        \"\"\"Check non-null placeholder invariant for legacy v1.\"\"\"\n"
            "        return value is not None\n"
        )

    if broken_neighbor:
        files["legacy_v1/deprecated_broken_stub.py"] = (
            f'"""Unmaintained deprecated stub with intentional SyntaxError ({slug})."""\n\n'
            "def broken_legacy_entry(items:\n"
            "    return [x for x in items\n"
        )

    files[f"tests/test_{slug}_smoke.py"] = (
        _sys_path_header()
        + f"from infra.{slug}_checkpoint import CheckpointRegistry\n\n"
        + "def test_subsystem_smoke_wiring() -> None:\n"
        + "    reg = CheckpointRegistry()\n"
        + "    assert reg.advance('p0', 5) == 5\n"
        + "    assert reg.advance('p0', 3) == 5\n"
    )

    files.update(extra_files)
    return files


def get_stress_coding_part1() -> list[dict[str, Any]]:
    """Build STRESS_LC_01 through STRESS_LC_10 (long_context_multi_file & deep_fanin_refactor)."""
    tasks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # STRESS_LC_01: Ledger Precision & Double-Entry Balancing
    # ------------------------------------------------------------------
    lc01_money_bug = (
        '"""Currency rounding and decimal arithmetic helpers (lc01)."""\n\n'
        "def round_currency(amount: float) -> float:\n"
        "    \"\"\"Round currency amount to 2 decimal places.\"\"\"\n"
        "    # BUG: float arithmetic causes binary rounding drift on 2.675 / 0.1+0.2\n"
        "    return round(float(amount), 2)\n\n"
        "def apply_fee_bps(gross: float, bps: int) -> float:\n"
        "    \"\"\"Compute basis-point fee from gross amount.\"\"\"\n"
        "    return round_currency(gross * (bps / 10000.0))\n"
    )
    lc01_money_fix = (
        '"""Decimal ROUND_HALF_UP currency arithmetic helpers (lc01)."""\n\n'
        "from decimal import Decimal, ROUND_HALF_UP\n\n"
        "_CENT = Decimal('0.01')\n\n"
        "def round_currency(amount: Decimal | str | float | int) -> Decimal:\n"
        "    \"\"\"Round currency amount to 2 decimal places using ROUND_HALF_UP.\"\"\"\n"
        "    return Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)\n\n"
        "def apply_fee_bps(gross: Decimal | str | float | int, bps: int) -> Decimal:\n"
        "    \"\"\"Compute basis-point fee using exact Decimal arithmetic.\"\"\"\n"
        "    if bps < 0:\n"
        "        raise ValueError('bps must be non-negative')\n"
        "    raw = Decimal(str(gross)) * Decimal(bps) / Decimal('10000')\n"
        "    return raw.quantize(_CENT, rounding=ROUND_HALF_UP)\n"
    )
    lc01_settle_bug = (
        '"""Multi-leg settlement journal generator (lc01)."""\n\n'
        "from domain.lc01_money import apply_fee_bps, round_currency\n\n"
        "def build_settlement_journal(gross_str: str, fee_bps: int) -> dict[str, object]:\n"
        "    \"\"\"Build balanced debit/credit settlement journal entries.\"\"\"\n"
        "    gross = round_currency(float(gross_str))\n"
        "    fee = apply_fee_bps(gross, fee_bps)\n"
        "    net = round_currency(gross - fee)\n"
        "    entries = [\n"
        "        {'account': 'clearing', 'debit': gross, 'credit': 0.0},\n"
        "        {'account': 'merchant', 'debit': 0.0, 'credit': net},\n"
        "    ]\n"
        "    # BUG: omits fee credit line so debits != credits\n"
        "    return {'gross': gross, 'fee': fee, 'net': net, 'entries': entries}\n"
    )
    lc01_settle_fix = (
        '"""Balanced Decimal settlement journal generator (lc01)."""\n\n'
        "from decimal import Decimal\n"
        "from domain.lc01_money import apply_fee_bps, round_currency\n\n"
        "def build_settlement_journal(gross_str: str, fee_bps: int) -> dict[str, object]:\n"
        "    \"\"\"Build balanced debit/credit settlement journal entries with Decimal precision.\"\"\"\n"
        "    gross = round_currency(gross_str)\n"
        "    fee = apply_fee_bps(gross, fee_bps)\n"
        "    net = (gross - fee).quantize(Decimal('0.01'))\n"
        "    entries = [\n"
        "        {'account': 'clearing', 'debit': gross, 'credit': Decimal('0.00')},\n"
        "        {'account': 'merchant', 'debit': Decimal('0.00'), 'credit': net},\n"
        "        {'account': 'platform_fee', 'debit': Decimal('0.00'), 'credit': fee},\n"
        "    ]\n"
        "    return {'gross': gross, 'fee': fee, 'net': net, 'entries': entries}\n"
    )
    lc01_test = (
        _sys_path_header()
        + "from decimal import Decimal\n"
        + "from domain.lc01_money import apply_fee_bps, round_currency\n"
        + "from services.lc01_settlement import build_settlement_journal\n\n"
        + "def test_decimal_half_up_avoids_float_drift() -> None:\n"
        + "    assert round_currency('2.675') == Decimal('2.68')\n"
        + "    assert apply_fee_bps('100.05', 250) == Decimal('2.50')\n\n"
        + "def test_settlement_journal_preserves_double_entry_balance() -> None:\n"
        + "    res = build_settlement_journal('1234.55', 275)\n"
        + "    entries = res['entries']\n"
        + "    debits = sum(e['debit'] for e in entries)\n"
        + "    credits = sum(e['credit'] for e in entries)\n"
        + "    assert debits == credits == Decimal('1234.55')\n"
        + "    assert res['fee'] == Decimal('33.95')\n"
        + "    assert res['net'] == Decimal('1200.60')\n"
    )
    lc01_files = build_coding_workspace(
        "lc01",
        "settlement ledger precision",
        core_import_target="core.lc01_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc01_money",
        domain_symbol="round_currency",
        service_import_target="services.lc01_settlement",
        service_symbol="build_settlement_journal",
        extra_files={
            "domain/lc01_money.py": lc01_money_bug,
            "services/lc01_settlement.py": lc01_settle_bug,
            "tests/test_lc01.py": lc01_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_01",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "long_context_multi_file",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc01_money.py round_currency and apply_fee_bps using Decimal ROUND_HALF_UP, "
                "and update services/lc01_settlement.py build_settlement_journal so debit and credit entries balance."
            ),
            "files": lc01_files,
            "solution_files": {
                "domain/lc01_money.py": lc01_money_fix,
                "services/lc01_settlement.py": lc01_settle_fix,
            },
            "baseline_solution_files": {
                "domain/lc01_money.py": lc01_money_fix,
                "services/lc01_settlement.py": lc01_settle_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc01.py",
            "expected_touched_files": ["domain/lc01_money.py", "services/lc01_settlement.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc01_money.py", "services/lc01_settlement.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                1, provider="anthropic", task_kind="coding", prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_02: Thread-Safe Token Bucket Refill & Atomic Multi-Token Reserve
    # ------------------------------------------------------------------
    lc02_clock_bug = (
        '"""Monotonic clock abstraction and bucket record (lc02)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class TokenBucketState:\n"
        "    \"\"\"Mutable bucket token count and last_refill timestamp.\"\"\"\n\n"
        "    tokens: float\n"
        "    last_refill: float\n\n"
        "def compute_refill(tokens: float, elapsed: float, rate: float, capacity: float) -> float:\n"
        "    \"\"\"Compute refilled tokens.\"\"\"\n"
        "    # BUG: allows negative elapsed or exceeding capacity\n"
        "    return tokens + elapsed * rate\n"
    )
    lc02_clock_fix = (
        '"""Monotonic clock abstraction and bounded refill helper (lc02)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class TokenBucketState:\n"
        "    \"\"\"Mutable bucket token count and last_refill timestamp.\"\"\"\n\n"
        "    tokens: float\n"
        "    last_refill: float\n\n"
        "def compute_refill(tokens: float, elapsed: float, rate: float, capacity: float) -> float:\n"
        "    \"\"\"Refill tokens for non-negative elapsed seconds capped at capacity.\"\"\"\n"
        "    safe_elapsed = max(0.0, float(elapsed))\n"
        "    return min(float(capacity), float(tokens) + safe_elapsed * float(rate))\n"
    )
    lc02_lim_bug = (
        '"""Concurrent token bucket limiter (lc02)."""\n\n'
        "from typing import Callable\n"
        "from core.lc02_clock import TokenBucketState, compute_refill\n\n"
        "class ConcurrentRateLimiter:\n"
        "    \"\"\"Rate limiter supporting atomic reserve across concurrent callers.\"\"\"\n\n"
        "    def __init__(self, capacity: float, refill_rate: float, clock: Callable[[], float]) -> None:\n"
        "        self.capacity = float(capacity)\n"
        "        self.refill_rate = float(refill_rate)\n"
        "        self._clock = clock\n"
        "        self._buckets: dict[str, TokenBucketState] = {}\n\n"
        "    def reserve(self, key: str, cost: float = 1.0) -> bool:\n"
        "        # BUG: lacks thread lock and rejects exact cost == tokens\n"
        "        now = float(self._clock())\n"
        "        st = self._buckets.setdefault(key, TokenBucketState(self.capacity, now))\n"
        "        st.tokens = compute_refill(st.tokens, now - st.last_refill, self.refill_rate, self.capacity)\n"
        "        st.last_refill = now\n"
        "        if st.tokens > cost:\n"
        "            st.tokens -= cost\n"
        "            return True\n"
        "        return False\n"
    )
    lc02_lim_fix = (
        '"""Thread-safe concurrent token bucket limiter (lc02)."""\n\n'
        "import threading\n"
        "from typing import Callable\n"
        "from core.lc02_clock import TokenBucketState, compute_refill\n\n"
        "class ConcurrentRateLimiter:\n"
        "    \"\"\"Thread-safe rate limiter using RLock and atomic reserve.\"\"\"\n\n"
        "    def __init__(self, capacity: float, refill_rate: float, clock: Callable[[], float]) -> None:\n"
        "        if capacity <= 0 or refill_rate < 0:\n"
        "            raise ValueError('capacity must be positive and refill_rate non-negative')\n"
        "        self.capacity = float(capacity)\n"
        "        self.refill_rate = float(refill_rate)\n"
        "        self._clock = clock\n"
        "        self._lock = threading.RLock()\n"
        "        self._buckets: dict[str, TokenBucketState] = {}\n\n"
        "    def reserve(self, key: str, cost: float = 1.0) -> bool:\n"
        "        \"\"\"Thread-safe atomic reserve checking tokens >= cost after bounded compute_refill.\"\"\"\n"
        "        if cost <= 0:\n"
        "            raise ValueError('cost must be positive')\n"
        "        with self._lock:\n"
        "            now = float(self._clock())\n"
        "            st = self._buckets.get(key)\n"
        "            if st is None:\n"
        "                st = TokenBucketState(self.capacity, now)\n"
        "                self._buckets[key] = st\n"
        "            st.tokens = compute_refill(st.tokens, now - st.last_refill, self.refill_rate, self.capacity)\n"
        "            st.last_refill = now\n"
        "            if st.tokens >= cost:\n"
        "                st.tokens -= cost\n"
        "                return True\n"
        "            return False\n"
    )
    lc02_test = (
        _sys_path_header()
        + "from concurrent.futures import ThreadPoolExecutor\n"
        + "from services.lc02_limiter import ConcurrentRateLimiter\n\n"
        + "def test_exact_capacity_and_cap_bounds() -> None:\n"
        + "    now = [10.0]\n"
        + "    lim = ConcurrentRateLimiter(5.0, 2.0, lambda: now[0])\n"
        + "    assert lim.reserve('k', 5.0) is True\n"
        + "    assert lim.reserve('k', 1.0) is False\n"
        + "    now[0] = 50.0\n"
        + "    assert lim.reserve('k', 5.0) is True\n"
        + "    assert lim.reserve('k', 0.1) is False\n\n"
        + "def test_concurrent_reserve_never_oversubscribes() -> None:\n"
        + "    lim = ConcurrentRateLimiter(10.0, 0.0, lambda: 1.0)\n"
        + "    with ThreadPoolExecutor(max_workers=8) as pool:\n"
        + "        outcomes = list(pool.map(lambda _: lim.reserve('shared', 1.0), range(30)))\n"
        + "    assert sum(1 for ok in outcomes if ok) == 10\n"
    )
    lc02_files = build_coding_workspace(
        "lc02",
        "concurrent token bucket",
        core_import_target="core.lc02_clock",
        core_symbol="compute_refill",
        domain_import_target="domain.lc02_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc02_limiter",
        service_symbol="ConcurrentRateLimiter",
        extra_files={
            "core/lc02_clock.py": lc02_clock_bug,
            "services/lc02_limiter.py": lc02_lim_bug,
            "tests/test_lc02.py": lc02_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_02",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "long_context_multi_file",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix core/lc02_clock.py compute_refill to clamp elapsed and cap at capacity, "
                "and update services/lc02_limiter.py ConcurrentRateLimiter.reserve with RLock and tokens >= cost."
            ),
            "files": lc02_files,
            "solution_files": {
                "core/lc02_clock.py": lc02_clock_fix,
                "services/lc02_limiter.py": lc02_lim_fix,
            },
            "baseline_solution_files": {
                "core/lc02_clock.py": lc02_clock_fix,
                "services/lc02_limiter.py": lc02_lim_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc02.py",
            "expected_touched_files": ["core/lc02_clock.py", "services/lc02_limiter.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc02_clock.py", "services/lc02_limiter.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                2, provider="openai", task_kind="coding", prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_03 (baseline incomplete): DAG Topological Sort & Cycle Path Extraction
    # ------------------------------------------------------------------
    lc03_graph_bug = (
        '"""Directed dependency graph helper (lc03)."""\n\n'
        "def build_adjacency(edges: list[tuple[str, str]]) -> dict[str, set[str]]:\n"
        "    \"\"\"Build directed dependency map node -> prerequisites.\"\"\"\n"
        "    adj: dict[str, set[str]] = {}\n"
        "    for node, dep in edges:\n"
        "        adj.setdefault(node, set()).add(dep)\n"
        "        adj.setdefault(dep, set())\n"
        "    return adj\n\n"
        "def find_cycle(adj: dict[str, set[str]]) -> list[str]:\n"
        "    \"\"\"Return cycle path if graph has a cycle.\"\"\"\n"
        "    # BUG: never detects cycles\n"
        "    return []\n"
    )
    lc03_graph_fix = (
        '"""Directed dependency graph helper with deterministic cycle detection (lc03)."""\n\n'
        "def build_adjacency(edges: list[tuple[str, str]]) -> dict[str, set[str]]:\n"
        "    \"\"\"Build directed dependency map node -> prerequisites.\"\"\"\n"
        "    adj: dict[str, set[str]] = {}\n"
        "    for node, dep in edges:\n"
        "        adj.setdefault(node, set()).add(dep)\n"
        "        adj.setdefault(dep, set())\n"
        "    return adj\n\n"
        "def find_cycle(adj: dict[str, set[str]]) -> list[str]:\n"
        "    \"\"\"Detect directed cycles including self-loops using 3-color DFS.\"\"\"\n"
        "    state: dict[str, int] = {}\n"
        "    stack: list[str] = []\n\n"
        "    def _dfs(node: str) -> list[str]:\n"
        "        state[node] = 1\n"
        "        stack.append(node)\n"
        "        for nxt in sorted(adj.get(node, ())):\n"
        "            nxt_state = state.get(nxt, 0)\n"
        "            if nxt_state == 1:\n"
        "                idx = stack.index(nxt)\n"
        "                return stack[idx:] + [nxt]\n"
        "            if nxt_state == 0:\n"
        "                cyc = _dfs(nxt)\n"
        "                if cyc:\n"
        "                    return cyc\n"
        "        stack.pop()\n"
        "        state[node] = 2\n"
        "        return []\n\n"
        "    for node in sorted(adj):\n"
        "        if state.get(node, 0) == 0:\n"
        "            cyc = _dfs(node)\n"
        "            if cyc:\n"
        "                return cyc\n"
        "    return []\n"
    )
    lc03_sched_bug = (
        '"""Topological task scheduler (lc03)."""\n\n'
        "from core.lc03_graph import build_adjacency, find_cycle\n\n"
        "def topological_order(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:\n"
        "    \"\"\"Schedule nodes respecting prerequisites.\"\"\"\n"
        "    adj = build_adjacency(edges)\n"
        "    for n in nodes:\n"
        "        adj.setdefault(n, set())\n"
        "    return sorted(adj)\n"
    )
    lc03_sched_base = (
        '"""Topological task scheduler without lexicographic tie-breaking (lc03)."""\n\n'
        "from core.lc03_graph import build_adjacency, find_cycle\n\n"
        "def topological_order(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:\n"
        "    adj = build_adjacency(edges)\n"
        "    for n in nodes:\n"
        "        adj.setdefault(n, set())\n"
        "    cyc = find_cycle(adj)\n"
        "    if cyc:\n"
        "        raise ValueError(f'Cycle detected: {cyc}')\n"
        "    # BASELINE OMISSION: uses LIFO pop without min-heap lexicographic order\n"
        "    indeg = {n: len(deps) for n, deps in adj.items()}\n"
        "    rev: dict[str, list[str]] = {n: [] for n in adj}\n"
        "    for n, deps in adj.items():\n"
        "        for d in deps:\n"
        "            rev[d].append(n)\n"
        "    ready = [n for n, deg in indeg.items() if deg == 0]\n"
        "    order: list[str] = []\n"
        "    while ready:\n"
        "        cur = ready.pop()\n"
        "        order.append(cur)\n"
        "        for child in rev[cur]:\n"
        "            indeg[child] -= 1\n"
        "            if indeg[child] == 0:\n"
        "                ready.append(child)\n"
        "    return order\n"
    )
    lc03_sched_fix = (
        '"""Deterministic lexicographic topological task scheduler (lc03)."""\n\n'
        "import heapq\n"
        "from core.lc03_graph import build_adjacency, find_cycle\n\n"
        "def topological_order(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:\n"
        "    \"\"\"Return lexicographic topological_order or raise ValueError on find_cycle.\"\"\"\n"
        "    adj = build_adjacency(edges)\n"
        "    for n in nodes:\n"
        "        adj.setdefault(n, set())\n"
        "    cyc = find_cycle(adj)\n"
        "    if cyc:\n"
        "        raise ValueError(f'Cycle detected: {cyc}')\n"
        "    indeg = {n: len(deps) for n, deps in adj.items()}\n"
        "    rev: dict[str, list[str]] = {n: [] for n in adj}\n"
        "    for n, deps in adj.items():\n"
        "        for d in deps:\n"
        "            rev[d].append(n)\n"
        "    heap = [n for n, deg in indeg.items() if deg == 0]\n"
        "    heapq.heapify(heap)\n"
        "    order: list[str] = []\n"
        "    while heap:\n"
        "        cur = heapq.heappop(heap)\n"
        "        order.append(cur)\n"
        "        for child in rev[cur]:\n"
        "            indeg[child] -= 1\n"
        "            if indeg[child] == 0:\n"
        "                heapq.heappush(heap, child)\n"
        "    return order\n"
    )
    lc03_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from core.lc03_graph import build_adjacency, find_cycle\n"
        + "from services.lc03_scheduler import topological_order\n\n"
        + "def test_lexicographic_topological_order() -> None:\n"
        + "    order = topological_order(['z', 'a', 'm', 'b'], [('m', 'a'), ('z', 'b')])\n"
        + "    assert order == ['a', 'b', 'm', 'z']\n\n"
        + "def test_cycle_and_self_loop_raise_value_error() -> None:\n"
        + "    assert find_cycle(build_adjacency([('a', 'b'), ('b', 'a')])) == ['a', 'b', 'a']\n"
        + "    with pytest.raises(ValueError):\n"
        + "        topological_order(['x'], [('x', 'x')])\n"
    )
    lc03_files = build_coding_workspace(
        "lc03",
        "dag topological scheduler",
        core_import_target="core.lc03_graph",
        core_symbol="find_cycle",
        domain_import_target="domain.lc03_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc03_scheduler",
        service_symbol="topological_order",
        extra_files={
            "core/lc03_graph.py": lc03_graph_bug,
            "services/lc03_scheduler.py": lc03_sched_bug,
            "tests/test_lc03.py": lc03_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_03",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "long_context_multi_file",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix core/lc03_graph.py find_cycle to detect directed cycles including self-loops, "
                "and update services/lc03_scheduler.py topological_order using heapq lexicographic ordering."
            ),
            "files": lc03_files,
            "solution_files": {
                "core/lc03_graph.py": lc03_graph_fix,
                "services/lc03_scheduler.py": lc03_sched_fix,
            },
            "baseline_solution_files": {
                "core/lc03_graph.py": lc03_graph_fix,
                "services/lc03_scheduler.py": lc03_sched_base,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc03.py",
            "expected_touched_files": ["core/lc03_graph.py", "services/lc03_scheduler.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc03_graph.py", "services/lc03_scheduler.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                3, provider="gemini", task_kind="coding", prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_04 (simulates_one_strike_promotion=true): Circuit Breaker State Machine
    # ------------------------------------------------------------------
    lc04_state_bug = (
        '"""Circuit breaker state descriptor (lc04)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class BreakerSnapshot:\n"
        "    \"\"\"Circuit breaker transition counters.\"\"\"\n\n"
        "    state: str = 'closed'\n"
        "    failures: int = 0\n"
        "    half_open_successes: int = 0\n"
        "    opened_at: float = 0.0\n"
    )
    lc04_state_fix = (
        '"""Circuit breaker state descriptor with validation (lc04)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class BreakerSnapshot:\n"
        "    \"\"\"Circuit breaker state: closed, open, or half_open.\"\"\"\n\n"
        "    state: str = 'closed'\n"
        "    failures: int = 0\n"
        "    half_open_successes: int = 0\n"
        "    opened_at: float = 0.0\n\n"
        "    def reset_closed(self) -> None:\n"
        "        \"\"\"Reset BreakerSnapshot back to closed state.\"\"\"\n"
        "        self.state = 'closed'\n"
        "        self.failures = 0\n"
        "        self.half_open_successes = 0\n"
    )
    lc04_brk_bug = (
        '"""Fault-tolerant circuit breaker service (lc04)."""\n\n'
        "from typing import Callable\n"
        "from domain.lc04_breaker_state import BreakerSnapshot\n\n"
        "class CircuitBreaker:\n"
        "    \"\"\"Three-state circuit breaker controller.\"\"\"\n\n"
        "    def __init__(\n"
        "        self,\n"
        "        failure_threshold: int,\n"
        "        cooldown_s: float,\n"
        "        half_open_success_threshold: int,\n"
        "        clock: Callable[[], float],\n"
        "    ) -> None:\n"
        "        self.failure_threshold = failure_threshold\n"
        "        self.cooldown_s = float(cooldown_s)\n"
        "        self.half_open_success_threshold = half_open_success_threshold\n"
        "        self._clock = clock\n"
        "        self.snap = BreakerSnapshot()\n\n"
        "    def allow_request(self) -> bool:\n"
        "        return self.snap.state != 'open'\n\n"
        "    def record_success(self) -> None:\n"
        "        self.snap.failures = 0\n\n"
        "    def record_failure(self) -> None:\n"
        "        self.snap.failures += 1\n"
        "        if self.snap.failures >= self.failure_threshold:\n"
        "            self.snap.state = 'open'\n"
    )
    lc04_brk_fix = (
        '"""Fault-tolerant three-state circuit breaker service (lc04)."""\n\n'
        "from typing import Callable\n"
        "from domain.lc04_breaker_state import BreakerSnapshot\n\n"
        "class CircuitBreaker:\n"
        "    \"\"\"Manages closed, open, and half_open circuit breaker state transitions.\"\"\"\n\n"
        "    def __init__(\n"
        "        self,\n"
        "        failure_threshold: int,\n"
        "        cooldown_s: float,\n"
        "        half_open_success_threshold: int,\n"
        "        clock: Callable[[], float],\n"
        "    ) -> None:\n"
        "        self.failure_threshold = failure_threshold\n"
        "        self.cooldown_s = float(cooldown_s)\n"
        "        self.half_open_success_threshold = half_open_success_threshold\n"
        "        self._clock = clock\n"
        "        self.snap = BreakerSnapshot()\n\n"
        "    def allow_request(self) -> bool:\n"
        "        \"\"\"Allow traffic when closed, half_open, or cooldown_s elapsed.\"\"\"\n"
        "        if self.snap.state == 'open':\n"
        "            if float(self._clock()) - self.snap.opened_at >= self.cooldown_s:\n"
        "                self.snap.state = 'half_open'\n"
        "                self.snap.half_open_successes = 0\n"
        "                return True\n"
        "            return False\n"
        "        return True\n\n"
        "    def record_success(self) -> None:\n"
        "        \"\"\"Transition half_open to closed via reset_closed after threshold successes.\"\"\"\n"
        "        if self.snap.state == 'half_open':\n"
        "            self.snap.half_open_successes += 1\n"
        "            if self.snap.half_open_successes >= self.half_open_success_threshold:\n"
        "                self.snap.reset_closed()\n"
        "        else:\n"
        "            self.snap.failures = 0\n\n"
        "    def record_failure(self) -> None:\n"
        "        \"\"\"Open circuit immediately from half_open or at failure_threshold.\"\"\"\n"
        "        now = float(self._clock())\n"
        "        if self.snap.state == 'half_open':\n"
        "            self.snap.state = 'open'\n"
        "            self.snap.opened_at = now\n"
        "            self.snap.half_open_successes = 0\n"
        "            return\n"
        "        self.snap.failures += 1\n"
        "        if self.snap.failures >= self.failure_threshold:\n"
        "            self.snap.state = 'open'\n"
        "            self.snap.opened_at = now\n"
    )
    lc04_brk_flawed = (
        "from domain.lc04_breaker_state import BreakerSnapshot\n\n"
        "class CircuitBreaker:\n"
        "    def allow_request(self) -> bool:\n"
        "        return (self.snap.state != 'open'\n"
    )
    lc04_test = (
        _sys_path_header()
        + "from services.lc04_breaker import CircuitBreaker\n\n"
        + "def test_circuit_breaker_half_open_recovery_and_reopen() -> None:\n"
        + "    now = [0.0]\n"
        + "    cb = CircuitBreaker(2, 10.0, 2, lambda: now[0])\n"
        + "    cb.record_failure()\n"
        + "    cb.record_failure()\n"
        + "    assert cb.snap.state == 'open' and cb.allow_request() is False\n"
        + "    now[0] = 10.0\n"
        + "    assert cb.allow_request() is True and cb.snap.state == 'half_open'\n"
        + "    cb.record_failure()\n"
        + "    assert cb.snap.state == 'open' and cb.allow_request() is False\n"
        + "    now[0] = 20.0\n"
        + "    assert cb.allow_request() is True\n"
        + "    cb.record_success()\n"
        + "    assert cb.snap.state == 'half_open'\n"
        + "    cb.record_success()\n"
        + "    assert cb.snap.state == 'closed' and cb.snap.failures == 0\n"
    )
    lc04_files = build_coding_workspace(
        "lc04",
        "circuit breaker state machine",
        core_import_target="core.lc04_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc04_breaker_state",
        domain_symbol="BreakerSnapshot",
        service_import_target="services.lc04_breaker",
        service_symbol="CircuitBreaker",
        extra_files={
            "domain/lc04_breaker_state.py": lc04_state_bug,
            "services/lc04_breaker.py": lc04_brk_bug,
            "tests/test_lc04.py": lc04_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_04",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "long_context_multi_file",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc04_breaker_state.py BreakerSnapshot.reset_closed and services/lc04_breaker.py "
                "CircuitBreaker allow_request, record_success, and record_failure for open and half_open transitions."
            ),
            "files": lc04_files,
            "solution_files": {
                "domain/lc04_breaker_state.py": lc04_state_fix,
                "services/lc04_breaker.py": lc04_brk_fix,
            },
            "baseline_solution_files": {
                "domain/lc04_breaker_state.py": lc04_state_fix,
                "services/lc04_breaker.py": lc04_brk_fix,
            },
            "flawed_draft_files": {"services/lc04_breaker.py": lc04_brk_flawed},
            "pytest_target": "tests/test_lc04.py",
            "expected_touched_files": ["domain/lc04_breaker_state.py", "services/lc04_breaker.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc04_breaker_state.py", "services/lc04_breaker.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                4, provider="anthropic", task_kind="coding", simulates_promotion=True, prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_05 (baseline incomplete): Async Single-Flight Coalescing Cache
    # ------------------------------------------------------------------
    lc05_inflight_bug = (
        '"""Async inflight future registry (lc05)."""\n\n'
        "import asyncio\n\n"
        "class InflightRegistry:\n"
        "    \"\"\"Tracks inflight asyncio Futures keyed by cache key.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.pending: dict[str, asyncio.Future[object]] = {}\n\n"
        "    def get_or_create(self, key: str) -> tuple[asyncio.Future[object], bool]:\n"
        "        fut = asyncio.get_running_loop().create_future()\n"
        "        return fut, True\n"
    )
    lc05_inflight_fix = (
        '"""Async single-flight inflight future registry (lc05)."""\n\n'
        "import asyncio\n\n"
        "class InflightRegistry:\n"
        "    \"\"\"Tracks inflight asyncio Futures keyed by cache key.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self.pending: dict[str, asyncio.Future[object]] = {}\n\n"
        "    def get_or_create(self, key: str) -> tuple[asyncio.Future[object], bool]:\n"
        "        \"\"\"Return (future, is_owner) coalescing concurrent callers on the same key.\"\"\"\n"
        "        existing = self.pending.get(key)\n"
        "        if existing is not None:\n"
        "            return existing, False\n"
        "        fut = asyncio.get_running_loop().create_future()\n"
        "        self.pending[key] = fut\n"
        "        return fut, True\n\n"
        "    def release(self, key: str) -> None:\n"
        "        \"\"\"Remove completed or failed inflight key.\"\"\"\n"
        "        self.pending.pop(key, None)\n"
    )
    lc05_cache_bug = (
        '"""Async single-flight coalescing cache (lc05)."""\n\n'
        "from typing import Awaitable, Callable\n"
        "from infra.lc05_inflight import InflightRegistry\n\n"
        "class CoalescingCache:\n"
        "    \"\"\"Cache coalescing concurrent async fetches.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self._values: dict[str, object] = {}\n"
        "        self._inflight = InflightRegistry()\n\n"
        "    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[object]]) -> object:\n"
        "        if key in self._values:\n"
        "            return self._values[key]\n"
        "        val = await loader()\n"
        "        self._values[key] = val\n"
        "        return val\n"
    )
    lc05_cache_base = (
        '"""Async coalescing cache leaking failed inflight futures (lc05)."""\n\n'
        "import asyncio\n"
        "from typing import Awaitable, Callable\n"
        "from infra.lc05_inflight import InflightRegistry\n\n"
        "class CoalescingCache:\n"
        "    def __init__(self) -> None:\n"
        "        self._values: dict[str, object] = {}\n"
        "        self._inflight = InflightRegistry()\n\n"
        "    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[object]]) -> object:\n"
        "        if key in self._values:\n"
        "            return self._values[key]\n"
        "        fut, owner = self._inflight.get_or_create(key)\n"
        "        if not owner:\n"
        "            return await asyncio.shield(fut)\n"
        "        # BASELINE OMISSION: forgets finally release(key) on exception\n"
        "        val = await loader()\n"
        "        self._values[key] = val\n"
        "        fut.set_result(val)\n"
        "        self._inflight.release(key)\n"
        "        return val\n"
    )
    lc05_cache_fix = (
        '"""Async single-flight coalescing cache with error cleanup (lc05)."""\n\n'
        "import asyncio\n"
        "from typing import Awaitable, Callable\n"
        "from infra.lc05_inflight import InflightRegistry\n\n"
        "class CoalescingCache:\n"
        "    \"\"\"Coalesce concurrent get_or_load calls via InflightRegistry and release on error.\"\"\"\n\n"
        "    def __init__(self) -> None:\n"
        "        self._values: dict[str, object] = {}\n"
        "        self._inflight = InflightRegistry()\n\n"
        "    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[object]]) -> object:\n"
        "        \"\"\"Return cached value or run one owner loader while waiters await future.\"\"\"\n"
        "        if key in self._values:\n"
        "            return self._values[key]\n"
        "        fut, owner = self._inflight.get_or_create(key)\n"
        "        if not owner:\n"
        "            return await asyncio.shield(fut)\n"
        "        try:\n"
        "            val = await loader()\n"
        "        except BaseException as exc:\n"
        "            if not fut.done():\n"
        "                fut.set_exception(exc)\n"
        "            self._inflight.release(key)\n"
        "            raise\n"
        "        self._values[key] = val\n"
        "        if not fut.done():\n"
        "            fut.set_result(val)\n"
        "        self._inflight.release(key)\n"
        "        return val\n"
    )
    lc05_test = (
        _sys_path_header()
        + "import asyncio\n"
        + "import pytest\n"
        + "from services.lc05_cache import CoalescingCache\n\n"
        + "def test_single_flight_coalescing_and_failure_cleanup() -> None:\n"
        + "    async def _run() -> None:\n"
        + "        cache = CoalescingCache()\n"
        + "        calls = [0]\n"
        + "        async def ok_loader() -> str:\n"
        + "            calls[0] += 1\n"
        + "            await asyncio.sleep(0.02)\n"
        + "            return 'fresh'\n"
        + "        res = await asyncio.gather(*(cache.get_or_load('k1', ok_loader) for _ in range(5)))\n"
        + "        assert res == ['fresh'] * 5 and calls[0] == 1\n"
        + "        async def bad_loader() -> str:\n"
        + "            calls[0] += 1\n"
        + "            raise RuntimeError('boom')\n"
        + "        with pytest.raises(RuntimeError):\n"
        + "            await cache.get_or_load('k2', bad_loader)\n"
        + "        assert await cache.get_or_load('k2', ok_loader) == 'fresh'\n"
        + "        assert calls[0] == 3\n"
        + "    asyncio.run(_run())\n"
    )
    lc05_files = build_coding_workspace(
        "lc05",
        "async coalescing cache",
        core_import_target="core.lc05_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc05_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc05_cache",
        service_symbol="CoalescingCache",
        extra_files={
            "infra/lc05_inflight.py": lc05_inflight_bug,
            "services/lc05_cache.py": lc05_cache_bug,
            "tests/test_lc05.py": lc05_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_05",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "long_context_multi_file",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix infra/lc05_inflight.py InflightRegistry get_or_create and release, and update "
                "services/lc05_cache.py CoalescingCache.get_or_load to coalesce concurrent callers and clean up failed futures."
            ),
            "files": lc05_files,
            "solution_files": {
                "infra/lc05_inflight.py": lc05_inflight_fix,
                "services/lc05_cache.py": lc05_cache_fix,
            },
            "baseline_solution_files": {
                "infra/lc05_inflight.py": lc05_inflight_fix,
                "services/lc05_cache.py": lc05_cache_base,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc05.py",
            "expected_touched_files": ["infra/lc05_inflight.py", "services/lc05_cache.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["infra/lc05_inflight.py", "services/lc05_cache.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                5, provider="openai", task_kind="coding", prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_06 (deep_fanin_refactor, subagent_chore=true, B>=0.40 -> mid/frontier)
    # ------------------------------------------------------------------
    lc06_ctx_bug = (
        '"""Core RequestContext envelope imported across all layers (lc06)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class RequestContext:\n"
        "    \"\"\"Cross-layer request metadata.\"\"\"\n\n"
        "    request_id: str\n"
        "    tenant_id: str = 'default'\n\n"
        "    @classmethod\n"
        "    def from_headers(cls, headers: dict[str, str]) -> 'RequestContext':\n"
        "        return cls(request_id=headers.get('x-request-id', ''))\n"
    )
    lc06_ctx_fix = (
        '"""Core RequestContext envelope with normalized tenant_id across layers (lc06)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class RequestContext:\n"
        "    \"\"\"Normalized cross-layer request metadata.\"\"\"\n\n"
        "    request_id: str\n"
        "    tenant_id: str\n\n"
        "    @classmethod\n"
        "    def from_headers(cls, headers: dict[str, str]) -> 'RequestContext':\n"
        "        \"\"\"Normalize x-request-id and x-tenant-id headers, rejecting blank tenant_id.\"\"\"\n"
        "        lowered = {k.lower(): v.strip() for k, v in headers.items()}\n"
        "        req = lowered.get('x-request-id', '')\n"
        "        tenant = lowered.get('x-tenant-id', '').lower()\n"
        "        if not req or not tenant:\n"
        "            raise ValueError('x-request-id and x-tenant-id are required')\n"
        "        return cls(request_id=req, tenant_id=tenant)\n"
    )
    lc06_gw_bug = (
        '"""Gateway adapter creating RequestContext (lc06)."""\n\n'
        "from core.lc06_context import RequestContext\n\n"
        "def dispatch_request(headers: dict[str, str]) -> dict[str, str]:\n"
        "    ctx = RequestContext.from_headers(headers)\n"
        "    return {'request_id': ctx.request_id, 'tenant_id': ctx.tenant_id}\n"
    )
    lc06_gw_fix = (
        '"""Gateway adapter propagating normalized RequestContext (lc06)."""\n\n'
        "from core.lc06_context import RequestContext\n\n"
        "def dispatch_request(headers: dict[str, str]) -> dict[str, str]:\n"
        "    \"\"\"Extract normalized RequestContext from headers and return context dict.\"\"\"\n"
        "    ctx = RequestContext.from_headers(headers)\n"
        "    return {'request_id': ctx.request_id, 'tenant_id': ctx.tenant_id}\n"
    )
    lc06_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from adapters.lc06_gateway import dispatch_request\n"
        + "from core.lc06_context import RequestContext\n\n"
        + "def test_request_context_normalizes_tenant_and_requires_headers() -> None:\n"
        + "    out = dispatch_request({'X-Request-Id': ' req-9 ', 'X-Tenant-ID': ' ACME '})\n"
        + "    assert out == {'request_id': 'req-9', 'tenant_id': 'acme'}\n"
        + "    with pytest.raises(ValueError):\n"
        + "        RequestContext.from_headers({'x-request-id': 'req-1'})\n"
    )
    lc06_files = build_coding_workspace(
        "lc06",
        "cross-layer request context refactor",
        core_import_target="core.lc06_context",
        core_symbol="RequestContext",
        domain_import_target="domain.lc06_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc06_dispatcher",
        service_symbol="ServiceHealthDispatcher",
        high_fanin_targets=[("core.lc06_context", "RequestContext")],
        extra_files={
            "core/lc06_context.py": lc06_ctx_bug,
            "adapters/lc06_gateway.py": lc06_gw_bug,
            "tests/test_lc06.py": lc06_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_06",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "deep_fanin_refactor",
            "cartridge": "software_engineering",
            "prompt": (
                "Refactor core/lc06_context.py RequestContext.from_headers to normalize x-request-id and x-tenant-id "
                "and update adapters/lc06_gateway.py dispatch_request."
            ),
            "files": lc06_files,
            "solution_files": {
                "core/lc06_context.py": lc06_ctx_fix,
                "adapters/lc06_gateway.py": lc06_gw_fix,
            },
            "baseline_solution_files": {
                "core/lc06_context.py": lc06_ctx_fix,
                "adapters/lc06_gateway.py": lc06_gw_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc06.py",
            "expected_touched_files": ["core/lc06_context.py", "adapters/lc06_gateway.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc06_context.py", "adapters/lc06_gateway.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["core/lc06_context.py"],
                    "expected_tiers": ["mid", "frontier"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                6, provider="gemini", task_kind="coding", subagent_chore=True, prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_07 (deep_fanin_refactor, subagent_chore=true): HMAC Pagination Cursor Codec
    # ------------------------------------------------------------------
    lc07_cur_bug = (
        '"""Core pagination cursor codec (lc07)."""\n\n'
        "import base64\n\n"
        "def encode_signed_cursor(offset: int, secret: str) -> str:\n"
        "    return base64.urlsafe_b64encode(str(offset).encode('utf-8')).decode('ascii').rstrip('=')\n\n"
        "def decode_signed_cursor(token: str, secret: str) -> int:\n"
        "    return int(base64.urlsafe_b64decode(token.encode('ascii')).decode('utf-8'))\n"
    )
    lc07_cur_fix = (
        '"""HMAC-SHA256 pagination cursor codec with constant-time compare (lc07)."""\n\n'
        "import base64\n"
        "import hashlib\n"
        "import hmac\n\n"
        "def _sign(offset: int, secret: str) -> str:\n"
        "    return hmac.new(secret.encode('utf-8'), str(offset).encode('utf-8'), hashlib.sha256).hexdigest()[:16]\n\n"
        "def encode_signed_cursor(offset: int, secret: str) -> str:\n"
        "    \"\"\"Encode non-negative offset with HMAC signature into urlsafe base64 token.\"\"\"\n"
        "    if offset < 0:\n"
        "        raise ValueError('offset must be non-negative')\n"
        "    raw = f'{offset}:{_sign(offset, secret)}'.encode('utf-8')\n"
        "    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')\n\n"
        "def decode_signed_cursor(token: str, secret: str) -> int:\n"
        "    \"\"\"Restore base64 padding and verify HMAC signature using hmac.compare_digest.\"\"\"\n"
        "    padded = token + '=' * (-len(token) % 4)\n"
        "    try:\n"
        "        decoded = base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')\n"
        "        off_str, sig = decoded.split(':', 1)\n"
        "        offset = int(off_str)\n"
        "    except Exception as exc:\n"
        "        raise ValueError('Malformed cursor token') from exc\n"
        "    expected = _sign(offset, secret)\n"
        "    if offset < 0 or not hmac.compare_digest(sig, expected):\n"
        "        raise ValueError('Invalid cursor signature')\n"
        "    return offset\n"
    )
    lc07_pag_bug = (
        '"""Keyset paginator service (lc07)."""\n\n'
        "from core.lc07_cursor import decode_signed_cursor, encode_signed_cursor\n\n"
        "def fetch_page(items: list[str], limit: int, secret: str, cursor: str | None = None) -> dict[str, object]:\n"
        "    offset = decode_signed_cursor(cursor, secret) if cursor else 0\n"
        "    page = items[offset:offset + limit - 1]\n"
        "    nxt = encode_signed_cursor(offset + len(page), secret) if offset + len(page) < len(items) else None\n"
        "    return {'items': page, 'next_cursor': nxt}\n"
    )
    lc07_pag_fix = (
        '"""Keyset paginator service using HMAC cursors (lc07)."""\n\n'
        "from core.lc07_cursor import decode_signed_cursor, encode_signed_cursor\n\n"
        "def fetch_page(items: list[str], limit: int, secret: str, cursor: str | None = None) -> dict[str, object]:\n"
        "    \"\"\"Slice exact limit items and return signed next_cursor.\"\"\"\n"
        "    if limit <= 0:\n"
        "        raise ValueError('limit must be positive')\n"
        "    offset = decode_signed_cursor(cursor, secret) if cursor else 0\n"
        "    page = items[offset:offset + limit]\n"
        "    next_off = offset + len(page)\n"
        "    nxt = encode_signed_cursor(next_off, secret) if next_off < len(items) else None\n"
        "    return {'items': page, 'next_cursor': nxt}\n"
    )
    lc07_test = (
        _sys_path_header()
        + "import base64\n"
        + "import pytest\n"
        + "from core.lc07_cursor import decode_signed_cursor, encode_signed_cursor\n"
        + "from services.lc07_paginator import fetch_page\n\n"
        + "def test_signed_cursor_pagination_and_tamper_rejection() -> None:\n"
        + "    sec = 'demo-signing-secret'\n"
        + "    data = ['r1', 'r2', 'r3', 'r4', 'r5']\n"
        + "    p1 = fetch_page(data, 2, sec)\n"
        + "    assert p1['items'] == ['r1', 'r2']\n"
        + "    p2 = fetch_page(data, 2, sec, str(p1['next_cursor']))\n"
        + "    assert p2['items'] == ['r3', 'r4']\n"
        + "    p3 = fetch_page(data, 2, sec, str(p2['next_cursor']))\n"
        + "    assert p3['items'] == ['r5'] and p3['next_cursor'] is None\n"
        + "    forged = base64.urlsafe_b64encode(b'1:badmac').decode('ascii').rstrip('=')\n"
        + "    with pytest.raises(ValueError):\n"
        + "        decode_signed_cursor(forged, sec)\n"
    )
    lc07_files = build_coding_workspace(
        "lc07",
        "hmac cursor pagination",
        core_import_target="core.lc07_cursor",
        core_symbol="encode_signed_cursor",
        domain_import_target="domain.lc07_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc07_paginator",
        service_symbol="fetch_page",
        high_fanin_targets=[("core.lc07_cursor", "encode_signed_cursor")],
        extra_files={
            "core/lc07_cursor.py": lc07_cur_bug,
            "services/lc07_paginator.py": lc07_pag_bug,
            "tests/test_lc07.py": lc07_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_07",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "deep_fanin_refactor",
            "cartridge": "software_engineering",
            "prompt": (
                "Refactor core/lc07_cursor.py encode_signed_cursor and decode_signed_cursor with hmac.compare_digest "
                "and padding recovery, and update services/lc07_paginator.py fetch_page."
            ),
            "files": lc07_files,
            "solution_files": {
                "core/lc07_cursor.py": lc07_cur_fix,
                "services/lc07_paginator.py": lc07_pag_fix,
            },
            "baseline_solution_files": {
                "core/lc07_cursor.py": lc07_cur_fix,
                "services/lc07_paginator.py": lc07_pag_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc07.py",
            "expected_touched_files": ["core/lc07_cursor.py", "services/lc07_paginator.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc07_cursor.py", "services/lc07_paginator.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["core/lc07_cursor.py"],
                    "expected_tiers": ["mid", "frontier"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                7, provider="anthropic", task_kind="coding", subagent_chore=True, prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_08 (deep_fanin_refactor, subagent_chore=true, 1-strike): RBAC Policy Evaluator
    # ------------------------------------------------------------------
    lc08_pol_bug = (
        '"""Core RBAC role graph and policy evaluator (lc08)."""\n\n'
        "def evaluate_rbac(roles: list[str], role_parents: dict[str, list[str]], rules: list[tuple[str, str, str]], action: str) -> bool:\n"
        "    # BUG: ignores role inheritance and explicit deny precedence\n"
        "    return any(r in roles and pat == action and effect == 'allow' for r, effect, pat in rules)\n"
    )
    lc08_pol_fix = (
        '"""Core RBAC evaluator with transitive role inheritance and explicit deny precedence (lc08)."""\n\n'
        "from fnmatch import fnmatch\n\n"
        "def expand_roles(roles: list[str], role_parents: dict[str, list[str]]) -> set[str]:\n"
        "    \"\"\"Collect transitive closure of roles via role_parents.\"\"\"\n"
        "    seen: set[str] = set()\n"
        "    stack = list(roles)\n"
        "    while stack:\n"
        "        cur = stack.pop()\n"
        "        if cur in seen:\n"
        "            continue\n"
        "        seen.add(cur)\n"
        "        stack.extend(role_parents.get(cur, ()))\n"
        "    return seen\n\n"
        "def evaluate_rbac(roles: list[str], role_parents: dict[str, list[str]], rules: list[tuple[str, str, str]], action: str) -> bool:\n"
        "    \"\"\"Evaluate rules with wildcard fnmatch and explicit deny precedence over allow.\"\"\"\n"
        "    effective = expand_roles(roles, role_parents)\n"
        "    allowed = False\n"
        "    for role, effect, pattern in rules:\n"
        "        if role not in effective or not fnmatch(action, pattern):\n"
        "            continue\n"
        "        if effect == 'deny':\n"
        "            return False\n"
        "        if effect == 'allow':\n"
        "            allowed = True\n"
        "    return allowed\n"
    )
    lc08_pol_flawed = (
        "def evaluate_rbac(roles, role_parents, rules, action):\n"
        "    return [r for r in rules if\n"
    )
    lc08_authz_bug = (
        '"""Authorization service enforcing core RBAC policy (lc08)."""\n\n'
        "from core.lc08_policy import evaluate_rbac\n\n"
        "def authorize_action(roles: list[str], role_parents: dict[str, list[str]], rules: list[tuple[str, str, str]], action: str) -> None:\n"
        "    if not evaluate_rbac(roles, role_parents, rules, action):\n"
        "        raise PermissionError(f'Action denied: {action}')\n"
    )
    lc08_authz_fix = (
        '"""Authorization service enforcing hierarchical RBAC policy (lc08)."""\n\n'
        "from core.lc08_policy import evaluate_rbac\n\n"
        "def authorize_action(roles: list[str], role_parents: dict[str, list[str]], rules: list[tuple[str, str, str]], action: str) -> None:\n"
        "    \"\"\"Raise PermissionError unless evaluate_rbac grants action.\"\"\"\n"
        "    if not evaluate_rbac(roles, role_parents, rules, action):\n"
        "        raise PermissionError(f'Action denied: {action}')\n"
    )
    lc08_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from core.lc08_policy import evaluate_rbac\n"
        + "from services.lc08_authz import authorize_action\n\n"
        + "def test_rbac_inheritance_wildcard_and_explicit_deny() -> None:\n"
        + "    parents = {'eng_lead': ['developer'], 'developer': ['reader']}\n"
        + "    rules = [('reader', 'allow', 'repo.read'), ('developer', 'allow', 'repo.*'), ('eng_lead', 'deny', 'repo.delete')]\n"
        + "    assert evaluate_rbac(['eng_lead'], parents, rules, 'repo.read') is True\n"
        + "    assert evaluate_rbac(['eng_lead'], parents, rules, 'repo.write') is True\n"
        + "    assert evaluate_rbac(['eng_lead'], parents, rules, 'repo.delete') is False\n"
        + "    with pytest.raises(PermissionError):\n"
        + "        authorize_action(['eng_lead'], parents, rules, 'repo.delete')\n"
    )
    lc08_files = build_coding_workspace(
        "lc08",
        "rbac policy evaluator",
        core_import_target="core.lc08_policy",
        core_symbol="evaluate_rbac",
        domain_import_target="domain.lc08_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc08_authz",
        service_symbol="authorize_action",
        high_fanin_targets=[("core.lc08_policy", "evaluate_rbac")],
        extra_files={
            "core/lc08_policy.py": lc08_pol_bug,
            "services/lc08_authz.py": lc08_authz_bug,
            "tests/test_lc08.py": lc08_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_08",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "deep_fanin_refactor",
            "cartridge": "software_engineering",
            "prompt": (
                "Refactor core/lc08_policy.py expand_roles and evaluate_rbac with wildcard fnmatch and explicit deny precedence, "
                "and verify services/lc08_authz.py authorize_action."
            ),
            "files": lc08_files,
            "solution_files": {
                "core/lc08_policy.py": lc08_pol_fix,
                "services/lc08_authz.py": lc08_authz_fix,
            },
            "baseline_solution_files": {
                "core/lc08_policy.py": lc08_pol_fix,
                "services/lc08_authz.py": lc08_authz_fix,
            },
            "flawed_draft_files": {"core/lc08_policy.py": lc08_pol_flawed},
            "pytest_target": "tests/test_lc08.py",
            "expected_touched_files": ["core/lc08_policy.py", "services/lc08_authz.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc08_policy.py", "services/lc08_authz.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["core/lc08_policy.py"],
                    "expected_tiers": ["mid", "frontier"],
                },
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                8, provider="openai", task_kind="coding", subagent_chore=True, simulates_promotion=True, prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_09 (deep_fanin_refactor, subagent_chore=true, baseline incomplete): Idempotency Store
    # ------------------------------------------------------------------
    lc09_idem_bug = (
        '"""Core idempotency key store (lc09)."""\n\n'
        "def payload_digest(payload: dict[str, object]) -> str:\n"
        "    return str(payload)\n"
    )
    lc09_idem_base = (
        '"""Core idempotency store without canonical key sorting (lc09)."""\n\n'
        "import hashlib\n"
        "import json\n\n"
        "def payload_digest(payload: dict[str, object]) -> str:\n"
        "    # BASELINE OMISSION: forgets sort_keys=True so equivalent dicts mismatch\n"
        "    return hashlib.sha256(json.dumps(payload).encode('utf-8')).hexdigest()\n"
    )
    lc09_idem_fix = (
        '"""Core idempotency payload_digest helper with canonical JSON sorting (lc09)."""\n\n'
        "import hashlib\n"
        "import json\n\n"
        "def payload_digest(payload: dict[str, object]) -> str:\n"
        "    \"\"\"Return deterministic SHA-256 hex digest using sort_keys=True.\"\"\"\n"
        "    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')\n"
        "    return hashlib.sha256(raw).hexdigest()\n"
    )
    lc09_ord_bug = (
        '"""Idempotent order creation service (lc09)."""\n\n'
        "from typing import Callable\n"
        "from core.lc09_idempotency import payload_digest\n\n"
        "class IdempotentOrderService:\n"
        "    def __init__(self, ttl_s: float, clock: Callable[[], float]) -> None:\n"
        "        self.ttl_s = float(ttl_s)\n"
        "        self._clock = clock\n"
        "        self._records: dict[str, tuple[str, dict[str, object], float]] = {}\n\n"
        "    def submit(self, key: str, payload: dict[str, object]) -> dict[str, object]:\n"
        "        return dict(payload)\n"
    )
    lc09_ord_fix = (
        '"""Idempotent order creation service enforcing digest conflict & TTL (lc09)."""\n\n'
        "from typing import Callable\n"
        "from core.lc09_errors import ConflictSubsystemError\n"
        "from core.lc09_idempotency import payload_digest\n\n"
        "class IdempotentOrderService:\n"
        "    \"\"\"Deduplicate submit calls by idempotency key with conflict detection and TTL expiry.\"\"\"\n\n"
        "    def __init__(self, ttl_s: float, clock: Callable[[], float]) -> None:\n"
        "        self.ttl_s = float(ttl_s)\n"
        "        self._clock = clock\n"
        "        self._records: dict[str, tuple[str, dict[str, object], float]] = {}\n"
        "        self._seq = 0\n\n"
        "    def submit(self, key: str, payload: dict[str, object]) -> dict[str, object]:\n"
        "        \"\"\"Return cached order on matching digest within TTL, raise ConflictSubsystemError on mismatch.\"\"\"\n"
        "        now = float(self._clock())\n"
        "        digest = payload_digest(payload)\n"
        "        existing = self._records.get(key)\n"
        "        if existing is not None:\n"
        "            old_digest, cached_resp, expires_at = existing\n"
        "            if now < expires_at:\n"
        "                if old_digest != digest:\n"
        "                    raise ConflictSubsystemError('IDEM_CONFLICT', f'Payload mismatch for {key}')\n"
        "                return dict(cached_resp)\n"
        "        self._seq += 1\n"
        "        resp = {'order_id': f'ORD-{self._seq}', 'amount': payload.get('amount')}\n"
        "        self._records[key] = (digest, resp, now + self.ttl_s)\n"
        "        return dict(resp)\n"
    )
    lc09_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from core.lc09_errors import ConflictSubsystemError\n"
        + "from services.lc09_orders import IdempotentOrderService\n\n"
        + "def test_idempotent_replay_reordered_keys_and_conflict() -> None:\n"
        + "    now = [0.0]\n"
        + "    svc = IdempotentOrderService(30.0, lambda: now[0])\n"
        + "    r1 = svc.submit('k1', {'sku': 'A', 'amount': 10})\n"
        + "    r2 = svc.submit('k1', {'amount': 10, 'sku': 'A'})\n"
        + "    assert r1 == r2\n"
        + "    with pytest.raises(ConflictSubsystemError):\n"
        + "        svc.submit('k1', {'sku': 'A', 'amount': 99})\n"
        + "    now[0] = 31.0\n"
        + "    r3 = svc.submit('k1', {'sku': 'A', 'amount': 99})\n"
        + "    assert r3['amount'] == 99 and r3['order_id'] != r1['order_id']\n"
    )
    lc09_files = build_coding_workspace(
        "lc09",
        "idempotency key guard",
        core_import_target="core.lc09_idempotency",
        core_symbol="payload_digest",
        domain_import_target="domain.lc09_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc09_orders",
        service_symbol="IdempotentOrderService",
        high_fanin_targets=[("core.lc09_idempotency", "payload_digest")],
        extra_files={
            "core/lc09_idempotency.py": lc09_idem_bug,
            "services/lc09_orders.py": lc09_ord_bug,
            "tests/test_lc09.py": lc09_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_09",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "deep_fanin_refactor",
            "cartridge": "software_engineering",
            "prompt": (
                "Refactor core/lc09_idempotency.py payload_digest with canonical JSON sort_keys=True, "
                "and update services/lc09_orders.py IdempotentOrderService.submit for TTL expiry and ConflictSubsystemError."
            ),
            "files": lc09_files,
            "solution_files": {
                "core/lc09_idempotency.py": lc09_idem_fix,
                "services/lc09_orders.py": lc09_ord_fix,
            },
            "baseline_solution_files": {
                "core/lc09_idempotency.py": lc09_idem_base,
                "services/lc09_orders.py": lc09_ord_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc09.py",
            "expected_touched_files": ["core/lc09_idempotency.py", "services/lc09_orders.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc09_idempotency.py", "services/lc09_orders.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["core/lc09_idempotency.py"],
                    "expected_tiers": ["mid", "frontier"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                9, provider="gemini", task_kind="coding", subagent_chore=True, prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_10 (deep_fanin_refactor, subagent_chore=true): Structured Error & Retryability Envelope
    # ------------------------------------------------------------------
    lc10_err_bug = (
        '"""Core retryable error classifier (lc10)."""\n\n'
        "def classify_rpc_error(status_code: int, message: str) -> dict[str, object]:\n"
        "    return {'status_code': status_code, 'retryable': False, 'message': message}\n"
    )
    lc10_err_fix = (
        '"""Core retryable error classifier with backoff_ms hint (lc10)."""\n\n'
        "_RETRYABLE_CODES = frozenset({408, 429, 500, 502, 503, 504})\n\n"
        "def classify_rpc_error(status_code: int, message: str) -> dict[str, object]:\n"
        "    \"\"\"Classify HTTP/RPC status_code into retryable envelope with backoff_ms.\"\"\"\n"
        "    retryable = status_code in _RETRYABLE_CODES\n"
        "    backoff_ms = 1000 if status_code == 429 else (250 if retryable else 0)\n"
        "    return {\n"
        "        'status_code': status_code,\n"
        "        'retryable': retryable,\n"
        "        'backoff_ms': backoff_ms,\n"
        "        'message': message.strip(),\n"
        "    }\n"
    )
    lc10_rpc_bug = (
        '"""RPC error boundary adapter (lc10)."""\n\n'
        "from core.lc10_retry_policy import classify_rpc_error\n\n"
        "def wrap_rpc_failure(status_code: int, message: str) -> dict[str, object]:\n"
        "    return classify_rpc_error(status_code, message)\n"
    )
    lc10_rpc_fix = (
        '"""RPC error boundary adapter formatting structured envelopes (lc10)."""\n\n'
        "from core.lc10_retry_policy import classify_rpc_error\n\n"
        "def wrap_rpc_failure(status_code: int, message: str) -> dict[str, object]:\n"
        "    \"\"\"Return structured RPC error envelope with retry_after_ms.\"\"\"\n"
        "    info = classify_rpc_error(status_code, message)\n"
        "    return {\n"
        "        'ok': False,\n"
        "        'code': info['status_code'],\n"
        "        'retryable': info['retryable'],\n"
        "        'retry_after_ms': info['backoff_ms'],\n"
        "        'detail': info['message'],\n"
        "    }\n"
    )
    lc10_test = (
        _sys_path_header()
        + "from adapters.lc10_rpc import wrap_rpc_failure\n\n"
        + "def test_rpc_error_classification_and_envelope() -> None:\n"
        + "    rate = wrap_rpc_failure(429, ' slow down ')\n"
        + "    assert rate == {'ok': False, 'code': 429, 'retryable': True, 'retry_after_ms': 1000, 'detail': 'slow down'}\n"
        + "    bad = wrap_rpc_failure(400, 'bad input')\n"
        + "    assert bad['retryable'] is False and bad['retry_after_ms'] == 0\n"
        + "    assert wrap_rpc_failure(503, 'busy')['retry_after_ms'] == 250\n"
    )
    lc10_files = build_coding_workspace(
        "lc10",
        "rpc retryability classifier",
        core_import_target="core.lc10_retry_policy",
        core_symbol="classify_rpc_error",
        domain_import_target="domain.lc10_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc10_dispatcher",
        service_symbol="ServiceHealthDispatcher",
        high_fanin_targets=[("core.lc10_retry_policy", "classify_rpc_error")],
        extra_files={
            "core/lc10_retry_policy.py": lc10_err_bug,
            "adapters/lc10_rpc.py": lc10_rpc_bug,
            "tests/test_lc10.py": lc10_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_10",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "deep_fanin_refactor",
            "cartridge": "software_engineering",
            "prompt": (
                "Refactor core/lc10_retry_policy.py classify_rpc_error to classify 429/5xx retryable status_code and backoff_ms, "
                "and update adapters/lc10_rpc.py wrap_rpc_failure."
            ),
            "files": lc10_files,
            "solution_files": {
                "core/lc10_retry_policy.py": lc10_err_fix,
                "adapters/lc10_rpc.py": lc10_rpc_fix,
            },
            "baseline_solution_files": {
                "core/lc10_retry_policy.py": lc10_err_fix,
                "adapters/lc10_rpc.py": lc10_rpc_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc10.py",
            "expected_touched_files": ["core/lc10_retry_policy.py", "adapters/lc10_rpc.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc10_retry_policy.py", "adapters/lc10_rpc.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["core/lc10_retry_policy.py"],
                    "expected_tiers": ["mid", "frontier"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                10, provider="anthropic", task_kind="coding", subagent_chore=True, prefer_decision1=True
            ),
        }
    )

    return tasks


def get_stress_coding_part2() -> list[dict[str, Any]]:
    """Build STRESS_LC_11 through STRESS_LC_20 (noisy_distractor_graph & broken_neighbor_resilience)."""
    tasks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # STRESS_LC_11 (noisy_distractor_graph, leaf subagent_chore=true -> floor/mid)
    # ------------------------------------------------------------------
    lc11_leaf_bug = (
        '"""Sliding-window percentile and EWMA leaf stats helper (lc11)."""\n\n'
        "def nearest_rank_percentile(values: list[float], pct: float) -> float:\n"
        "    if not values:\n"
        "        return 0.0\n"
        "    # BUG: forgets to sort values and drops rounding bounds\n"
        "    idx = int(len(values) * pct / 100.0)\n"
        "    return float(values[min(len(values) - 1, idx)])\n\n"
        "def prune_window(samples: list[tuple[float, float]], now: float, window_s: float) -> list[tuple[float, float]]:\n"
        "    return list(samples)\n"
    )
    lc11_leaf_fix = (
        '"""Sliding-window percentile and EWMA leaf stats helper (lc11)."""\n\n'
        "import math\n\n"
        "def nearest_rank_percentile(values: list[float], pct: float) -> float:\n"
        "    \"\"\"Compute nearest-rank percentile on sorted values for 0 < pct <= 100.\"\"\"\n"
        "    if not 0.0 < pct <= 100.0:\n"
        "        raise ValueError('pct must lie in (0, 100]')\n"
        "    if not values:\n"
        "        return 0.0\n"
        "    ordered = sorted(float(v) for v in values)\n"
        "    rank = max(1, math.ceil((pct / 100.0) * len(ordered)))\n"
        "    return ordered[rank - 1]\n\n"
        "def prune_window(samples: list[tuple[float, float]], now: float, window_s: float) -> list[tuple[float, float]]:\n"
        "    \"\"\"Keep only (ts, val) tuples within [now - window_s, now].\"\"\"\n"
        "    cutoff = float(now) - float(window_s)\n"
        "    return [(ts, val) for ts, val in samples if cutoff <= ts <= now]\n"
    )
    lc11_exp_bug = (
        '"""Metrics exporter calling leaf percentile helper (lc11)."""\n\n'
        "from infra.lc11_leaf_stats import nearest_rank_percentile, prune_window\n\n"
        "def export_p95(samples: list[tuple[float, float]], now: float, window_s: float) -> float:\n"
        "    active = prune_window(samples, now, window_s)\n"
        "    return nearest_rank_percentile([v for _, v in active], 95.0)\n"
    )
    lc11_exp_fix = (
        '"""Metrics exporter formatting p95 from pruned sliding window (lc11)."""\n\n'
        "from infra.lc11_leaf_stats import nearest_rank_percentile, prune_window\n\n"
        "def export_p95(samples: list[tuple[float, float]], now: float, window_s: float) -> float:\n"
        "    \"\"\"Prune stale samples via prune_window and return nearest_rank_percentile p95.\"\"\"\n"
        "    active = prune_window(samples, now, window_s)\n"
        "    return nearest_rank_percentile([v for _, v in active], 95.0)\n"
    )
    lc11_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from adapters.lc11_exporter import export_p95\n"
        + "from infra.lc11_leaf_stats import nearest_rank_percentile\n\n"
        + "def test_sliding_window_p95_sorts_and_prunes() -> None:\n"
        + "    samples = [(1.0, 999.0), (15.0, 50.0), (16.0, 10.0), (17.0, 30.0), (18.0, 20.0), (19.0, 40.0)]\n"
        + "    assert export_p95(samples, now=20.0, window_s=5.0) == 50.0\n"
        + "    assert nearest_rank_percentile([100.0, 10.0, 20.0, 30.0], 50.0) == 20.0\n"
        + "    with pytest.raises(ValueError):\n"
        + "        nearest_rank_percentile([1.0], 0.0)\n"
    )
    lc11_files = build_coding_workspace(
        "lc11",
        "sliding window metrics helper",
        core_import_target="core.lc11_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc11_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc11_dispatcher",
        service_symbol="ServiceHealthDispatcher",
        distractor_count=6,
        extra_files={
            "infra/lc11_leaf_stats.py": lc11_leaf_bug,
            "adapters/lc11_exporter.py": lc11_exp_bug,
            "tests/test_lc11.py": lc11_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_11",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "noisy_distractor_graph",
            "cartridge": "software_engineering",
            "prompt": (
                "Implement helper nearest_rank_percentile and prune_window in infra/lc11_leaf_stats.py "
                "and export_p95 in adapters/lc11_exporter.py."
            ),
            "files": lc11_files,
            "solution_files": {
                "infra/lc11_leaf_stats.py": lc11_leaf_fix,
                "adapters/lc11_exporter.py": lc11_exp_fix,
            },
            "baseline_solution_files": {
                "infra/lc11_leaf_stats.py": lc11_leaf_fix,
                "adapters/lc11_exporter.py": lc11_exp_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc11.py",
            "expected_touched_files": ["infra/lc11_leaf_stats.py", "adapters/lc11_exporter.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["infra/lc11_leaf_stats.py", "adapters/lc11_exporter.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["infra/lc11_leaf_stats.py"],
                    "expected_tiers": ["floor", "mid"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                11, provider="gemini", task_kind="coding", subagent_chore=True, leaf_chore=True, prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_12 (noisy_distractor_graph, 1-strike): Semver Constraint Solver
    # ------------------------------------------------------------------
    lc12_sem_bug = (
        '"""Semantic version parser with numeric prerelease identifiers (lc12)."""\n\n'
        "import re\n\n"
        "_RE = re.compile(r'^(\\d+)\\.(\\d+)\\.(\\d+)(?:-([0-9A-Za-z.-]+))?$')\n\n"
        "def semver_key(raw: str) -> tuple[int, int, int, int, tuple[tuple[int, object], ...]]:\n"
        "    m = _RE.match(raw.strip())\n"
        "    if not m:\n"
        "        raise ValueError(raw)\n"
        "    # BUG: puts prerelease above final release\n"
        "    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 1 if m.group(4) else 0, ())\n"
    )
    lc12_sem_fix = (
        '"""Semantic version parser with SemVer 2.0 prerelease ordering (lc12)."""\n\n'
        "import re\n\n"
        "_RE = re.compile(r'^(\\d+)\\.(\\d+)\\.(\\d+)(?:-([0-9A-Za-z.-]+))?$')\n\n"
        "def semver_key(raw: str) -> tuple[int, int, int, int, tuple[tuple[int, object], ...]]:\n"
        "    \"\"\"Compute SemVer 2.0 comparison key where prerelease < final and numeric segments compare numerically.\"\"\"\n"
        "    m = _RE.match(raw.strip())\n"
        "    if not m:\n"
        "        raise ValueError(f'Invalid semver: {raw!r}')\n"
        "    maj, mino, pat = int(m.group(1)), int(m.group(2)), int(m.group(3))\n"
        "    pre = m.group(4) or ''\n"
        "    if not pre:\n"
        "        return (maj, mino, pat, 1, ())\n"
        "    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split('.'))\n"
        "    return (maj, mino, pat, 0, parts)\n"
    )
    lc12_sem_flawed = (
        "def semver_key(raw: str):\n"
        "    return tuple(int(x) for x in raw.split('.'\n"
    )
    lc12_res_bug = (
        '"""Caret version constraint resolver (lc12)."""\n\n'
        "from domain.lc12_semver import semver_key\n\n"
        "def best_caret_match(candidates: list[str], caret_constraint: str) -> str | None:\n"
        "    base = semver_key(caret_constraint.lstrip('^'))\n"
        "    upper = (base[0] + 1, 0, 0, 0, ())\n"
        "    hits = [c for c in candidates if base <= semver_key(c) < upper]\n"
        "    return max(hits, key=semver_key) if hits else None\n"
    )
    lc12_res_fix = (
        '"""Caret version constraint resolver honoring 0.x minor/patch locks (lc12)."""\n\n'
        "from domain.lc12_semver import semver_key\n\n"
        "def best_caret_match(candidates: list[str], caret_constraint: str) -> str | None:\n"
        "    \"\"\"Select highest matching version under caret upper bounds.\"\"\"\n"
        "    base = semver_key(caret_constraint.lstrip('^'))\n"
        "    maj, mino, pat, _, _ = base\n"
        "    if maj > 0:\n"
        "        upper = (maj + 1, 0, 0, 0, ())\n"
        "    elif mino > 0:\n"
        "        upper = (0, mino + 1, 0, 0, ())\n"
        "    else:\n"
        "        upper = (0, 0, pat + 1, 0, ())\n"
        "    hits: list[str] = []\n"
        "    for cand in candidates:\n"
        "        k = semver_key(cand)\n"
        "        if k[3] == 0 and k[:3] != (maj, mino, pat):\n"
        "            continue\n"
        "        if base <= k < upper:\n"
        "            hits.append(cand)\n"
        "    return max(hits, key=semver_key) if hits else None\n"
    )
    lc12_test = (
        _sys_path_header()
        + "from domain.lc12_semver import semver_key\n"
        + "from services.lc12_resolver import best_caret_match\n\n"
        + "def test_numeric_prerelease_and_zero_caret() -> None:\n"
        + "    assert semver_key('1.0.0-alpha.2') < semver_key('1.0.0-alpha.10') < semver_key('1.0.0')\n"
        + "    assert best_caret_match(['0.2.3', '0.2.9', '0.3.0', '1.0.0-rc.1'], '^0.2.3') == '0.2.9'\n"
        + "    assert best_caret_match(['1.2.0-beta.1', '1.2.0'], '^1.0.0') == '1.2.0'\n"
    )
    lc12_files = build_coding_workspace(
        "lc12",
        "semver constraint solver",
        core_import_target="core.lc12_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc12_semver",
        domain_symbol="semver_key",
        service_import_target="services.lc12_resolver",
        service_symbol="best_caret_match",
        distractor_count=6,
        extra_files={
            "domain/lc12_semver.py": lc12_sem_bug,
            "services/lc12_resolver.py": lc12_res_bug,
            "tests/test_lc12.py": lc12_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_12",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "noisy_distractor_graph",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc12_semver.py semver_key for SemVer 2.0 numeric prerelease ordering "
                "and services/lc12_resolver.py best_caret_match for 0.x upper bounds."
            ),
            "files": lc12_files,
            "solution_files": {
                "domain/lc12_semver.py": lc12_sem_fix,
                "services/lc12_resolver.py": lc12_res_fix,
            },
            "baseline_solution_files": {
                "domain/lc12_semver.py": lc12_sem_fix,
                "services/lc12_resolver.py": lc12_res_fix,
            },
            "flawed_draft_files": {"domain/lc12_semver.py": lc12_sem_flawed},
            "pytest_target": "tests/test_lc12.py",
            "expected_touched_files": ["domain/lc12_semver.py", "services/lc12_resolver.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc12_semver.py", "services/lc12_resolver.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                12, provider="anthropic", task_kind="coding", simulates_promotion=True, prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_13 (noisy_distractor_graph, baseline incomplete): Cron Expression Matcher
    # ------------------------------------------------------------------
    lc13_cron_bug = (
        '"""Cron field parser supporting *, ranges, lists, and steps (lc13)."""\n\n'
        "def expand_cron_field(expr: str, min_val: int, max_val: int) -> set[int]:\n"
        "    if expr == '*':\n"
        "        return set(range(min_val, max_val + 1))\n"
        "    return {int(expr)}\n"
    )
    lc13_cron_base = (
        '"""Cron field parser missing */step modulo offset (lc13)."""\n\n'
        "def expand_cron_field(expr: str, min_val: int, max_val: int) -> set[int]:\n"
        "    out: set[int] = set()\n"
        "    for part in expr.split(','):\n"
        "        part = part.strip()\n"
        "        if part == '*':\n"
        "            out.update(range(min_val, max_val + 1))\n"
        "        elif '-' in part:\n"
        "            lo, hi = (int(x) for x in part.split('-', 1))\n"
        "            out.update(range(lo, hi + 1))\n"
        "        elif part.isdigit():\n"
        "            out.add(int(part))\n"
        "    # BASELINE OMISSION: ignores '/step' expressions like '*/15' or '2-10/2'\n"
        "    return out\n"
    )
    lc13_cron_fix = (
        '"""Cron field parser supporting *, ranges, lists, and /step intervals (lc13)."""\n\n'
        "def expand_cron_field(expr: str, min_val: int, max_val: int) -> set[int]:\n"
        "    \"\"\"Expand cron field expression into valid integer set within [min_val, max_val].\"\"\"\n"
        "    out: set[int] = set()\n"
        "    for raw_part in expr.split(','):\n"
        "        part = raw_part.strip()\n"
        "        if '/' in part:\n"
        "            base_str, step_str = part.split('/', 1)\n"
        "            step = int(step_str)\n"
        "            if step <= 0:\n"
        "                raise ValueError('step must be positive')\n"
        "        else:\n"
        "            base_str, step = part, 1\n"
        "        if base_str == '*':\n"
        "            lo, hi = min_val, max_val\n"
        "        elif '-' in base_str:\n"
        "            lo_s, hi_s = base_str.split('-', 1)\n"
        "            lo, hi = int(lo_s), int(hi_s)\n"
        "        else:\n"
        "            lo = hi = int(base_str)\n"
        "            if '/' in part:\n"
        "                hi = max_val\n"
        "        if lo < min_val or hi > max_val or lo > hi:\n"
        "            raise ValueError(f'Out of range cron field: {part!r}')\n"
        "        out.update(range(lo, hi + 1, step))\n"
        "    return out\n"
    )
    lc13_tick_bug = (
        '"""Cron next-tick matcher service (lc13)."""\n\n'
        "from datetime import datetime, timedelta\n"
        "from domain.lc13_cron import expand_cron_field\n\n"
        "def next_matching_minute(cron_expr: str, start: datetime) -> datetime:\n"
        "    return start\n"
    )
    lc13_tick_fix = (
        '"""Cron next-tick matcher service using expand_cron_field (lc13)."""\n\n'
        "from datetime import datetime, timedelta\n"
        "from domain.lc13_cron import expand_cron_field\n\n"
        "def next_matching_minute(cron_expr: str, start: datetime) -> datetime:\n"
        "    \"\"\"Find next datetime >= start matching 5-field cron_expr.\"\"\"\n"
        "    fields = cron_expr.split()\n"
        "    if len(fields) != 5:\n"
        "        raise ValueError('Expected 5-field cron expression')\n"
        "    mins = expand_cron_field(fields[0], 0, 59)\n"
        "    hours = expand_cron_field(fields[1], 0, 23)\n"
        "    doms = expand_cron_field(fields[2], 1, 31)\n"
        "    months = expand_cron_field(fields[3], 1, 12)\n"
        "    dows = expand_cron_field(fields[4], 0, 6)\n"
        "    cur = start.replace(second=0, microsecond=0)\n"
        "    for _ in range(60 * 24 * 62):\n"
        "        cron_dow = (cur.weekday() + 1) % 7\n"
        "        if cur.minute in mins and cur.hour in hours and cur.day in doms and cur.month in months and cron_dow in dows:\n"
        "            return cur\n"
        "        cur += timedelta(minutes=1)\n"
        "    raise ValueError('No matching tick within search window')\n"
    )
    lc13_test = (
        _sys_path_header()
        + "from datetime import datetime\n"
        + "from domain.lc13_cron import expand_cron_field\n"
        + "from services.lc13_ticker import next_matching_minute\n\n"
        + "def test_cron_steps_ranges_and_next_tick() -> None:\n"
        + "    assert expand_cron_field('*/15', 0, 59) == {0, 15, 30, 45}\n"
        + "    assert expand_cron_field('2-8/3,10', 0, 59) == {2, 5, 8, 10}\n"
        + "    nxt = next_matching_minute('*/20 9-10 * * *', datetime(2026, 3, 1, 8, 50))\n"
        + "    assert nxt == datetime(2026, 3, 1, 9, 0)\n"
    )
    lc13_files = build_coding_workspace(
        "lc13",
        "cron schedule matcher",
        core_import_target="core.lc13_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc13_cron",
        domain_symbol="expand_cron_field",
        service_import_target="services.lc13_ticker",
        service_symbol="next_matching_minute",
        distractor_count=6,
        extra_files={
            "domain/lc13_cron.py": lc13_cron_bug,
            "services/lc13_ticker.py": lc13_tick_bug,
            "tests/test_lc13.py": lc13_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_13",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "noisy_distractor_graph",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc13_cron.py expand_cron_field to support lists, ranges, and /step intervals, "
                "and implement services/lc13_ticker.py next_matching_minute."
            ),
            "files": lc13_files,
            "solution_files": {
                "domain/lc13_cron.py": lc13_cron_fix,
                "services/lc13_ticker.py": lc13_tick_fix,
            },
            "baseline_solution_files": {
                "domain/lc13_cron.py": lc13_cron_base,
                "services/lc13_ticker.py": lc13_tick_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc13.py",
            "expected_touched_files": ["domain/lc13_cron.py", "services/lc13_ticker.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc13_cron.py", "services/lc13_ticker.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                13, provider="openai", task_kind="coding", prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_14 (noisy_distractor_graph, 1-strike): WAL Checksum & Compactor
    # ------------------------------------------------------------------
    lc14_wal_bug = (
        '"""Write-ahead log CRC32 frame codec (lc14)."""\n\n'
        "import json\n\n"
        "def encode_wal_frame(op: str, key: str, value: object) -> str:\n"
        "    return json.dumps({'op': op, 'key': key, 'value': value})\n\n"
        "def decode_wal_frame(line: str) -> dict[str, object]:\n"
        "    return json.loads(line)\n"
    )
    lc14_wal_fix = (
        '"""Write-ahead log CRC32 frame encoder and checksum validator (lc14)."""\n\n'
        "import json\n"
        "import zlib\n\n"
        "def encode_wal_frame(op: str, key: str, value: object) -> str:\n"
        "    \"\"\"Encode WAL frame with deterministic CRC32 checksum prefix.\"\"\"\n"
        "    body = json.dumps({'op': op, 'key': key, 'value': value}, sort_keys=True, separators=(',', ':'))\n"
        "    crc = zlib.crc32(body.encode('utf-8')) & 0xFFFFFFFF\n"
        "    return f'{crc:08x}|{body}'\n\n"
        "def decode_wal_frame(line: str) -> dict[str, object]:\n"
        "    \"\"\"Verify CRC32 frame checksum and return parsed payload.\"\"\"\n"
        "    crc_hex, body = line.strip().split('|', 1)\n"
        "    expected = zlib.crc32(body.encode('utf-8')) & 0xFFFFFFFF\n"
        "    if int(crc_hex, 16) != expected:\n"
        "        raise ValueError('WAL frame CRC32 mismatch')\n"
        "    return dict(json.loads(body))\n"
    )
    lc14_wal_flawed = (
        "def encode_wal_frame(op, key, value):\n"
        "    return f'{op}|{key}'\n"
        "def decode_wal_frame(line:\n"
        "    return {}\n"
    )
    lc14_cmp_bug = (
        '"""WAL tombstone compactor service (lc14)."""\n\n'
        "from infra.lc14_wal import decode_wal_frame\n\n"
        "def compact_wal_lines(lines: list[str]) -> dict[str, object]:\n"
        "    out: dict[str, object] = {}\n"
        "    for ln in lines:\n"
        "        frame = decode_wal_frame(ln)\n"
        "        out[str(frame['key'])] = frame.get('value')\n"
        "    return out\n"
    )
    lc14_cmp_fix = (
        '"""WAL tombstone compactor applying last-write-wins and delete tombstones (lc14)."""\n\n'
        "from infra.lc14_wal import decode_wal_frame\n\n"
        "def compact_wal_lines(lines: list[str]) -> dict[str, object]:\n"
        "    \"\"\"Replay verified WAL frames and purge deleted tombstone keys.\"\"\"\n"
        "    state: dict[str, object] = {}\n"
        "    for ln in lines:\n"
        "        if not ln.strip():\n"
        "            continue\n"
        "        frame = decode_wal_frame(ln)\n"
        "        key = str(frame['key'])\n"
        "        if frame['op'] == 'del':\n"
        "            state.pop(key, None)\n"
        "        elif frame['op'] == 'put':\n"
        "            state[key] = frame.get('value')\n"
        "        else:\n"
        "            raise ValueError(f\"Unknown WAL op: {frame['op']!r}\")\n"
        "    return state\n"
    )
    lc14_test = (
        _sys_path_header()
        + "import pytest\n"
        + "from infra.lc14_wal import decode_wal_frame, encode_wal_frame\n"
        + "from services.lc14_compactor import compact_wal_lines\n\n"
        + "def test_wal_compaction_tombstones_and_crc_verification() -> None:\n"
        + "    lines = [\n"
        + "        encode_wal_frame('put', 'a', 1),\n"
        + "        encode_wal_frame('put', 'b', 2),\n"
        + "        encode_wal_frame('del', 'a', None),\n"
        + "        encode_wal_frame('put', 'b', 9),\n"
        + "    ]\n"
        + "    assert compact_wal_lines(lines) == {'b': 9}\n"
        + "    corrupt = lines[0].replace('\"a\"', '\"z\"')\n"
        + "    with pytest.raises(ValueError):\n"
        + "        decode_wal_frame(corrupt)\n"
    )
    lc14_files = build_coding_workspace(
        "lc14",
        "wal checksum compactor",
        core_import_target="core.lc14_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc14_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc14_compactor",
        service_symbol="compact_wal_lines",
        distractor_count=6,
        extra_files={
            "infra/lc14_wal.py": lc14_wal_bug,
            "services/lc14_compactor.py": lc14_cmp_bug,
            "tests/test_lc14.py": lc14_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_14",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "noisy_distractor_graph",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix infra/lc14_wal.py encode_wal_frame and decode_wal_frame with CRC32 verification, "
                "and update services/lc14_compactor.py compact_wal_lines for put and del tombstones."
            ),
            "files": lc14_files,
            "solution_files": {
                "infra/lc14_wal.py": lc14_wal_fix,
                "services/lc14_compactor.py": lc14_cmp_fix,
            },
            "baseline_solution_files": {
                "infra/lc14_wal.py": lc14_wal_fix,
                "services/lc14_compactor.py": lc14_cmp_fix,
            },
            "flawed_draft_files": {"infra/lc14_wal.py": lc14_wal_flawed},
            "pytest_target": "tests/test_lc14.py",
            "expected_touched_files": ["infra/lc14_wal.py", "services/lc14_compactor.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["infra/lc14_wal.py", "services/lc14_compactor.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                14, provider="gemini", task_kind="coding", simulates_promotion=True, prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_15 (noisy_distractor_graph, baseline incomplete, leaf subagent_chore=true -> floor/mid)
    # ------------------------------------------------------------------
    lc15_leaf_bug = (
        '"""Markdown frontmatter and wikilink stem leaf helper (lc15)."""\n\n'
        "def extract_wikilink_stems(markdown: str) -> list[str]:\n"
        "    return []\n"
    )
    lc15_leaf_base = (
        '"""Markdown wikilink extractor missing heading/alias stripping (lc15)."""\n\n'
        "import re\n\n"
        "_WIKI = re.compile(r'\\[\\[([^\\]\\n]+)\\]\\]')\n\n"
        "def extract_wikilink_stems(markdown: str) -> list[str]:\n"
        "    # BASELINE OMISSION: forgets to strip #heading and |alias\n"
        "    return [m.group(1).strip().lower() for m in _WIKI.finditer(markdown)]\n"
    )
    lc15_leaf_fix = (
        '"""Markdown frontmatter and wikilink stem leaf helper (lc15)."""\n\n'
        "import re\n\n"
        "_WIKI = re.compile(r'\\[\\[([^\\]|#\\n]+)(?:#[^\\]|\\n]+)?(?:\\|[^\\]\\n]+)?\\]\\]')\n\n"
        "def extract_wikilink_stems(markdown: str) -> list[str]:\n"
        "    \"\"\"Extract deduplicated lowercase wikilink stems ignoring #heading and |alias.\"\"\"\n"
        "    seen: set[str] = set()\n"
        "    stems: list[str] = []\n"
        "    for m in _WIKI.finditer(markdown):\n"
        "        stem = m.group(1).strip().rsplit('/', 1)[-1].lower()\n"
        "        if stem.endswith('.md'):\n"
        "            stem = stem[:-3]\n"
        "        if stem and stem not in seen:\n"
        "            seen.add(stem)\n"
        "            stems.append(stem)\n"
        "    return stems\n"
    )
    lc15_sync_bug = (
        '"""Vault sync adapter using leaf wikilink helper (lc15)."""\n\n'
        "from infra.lc15_leaf_links import extract_wikilink_stems\n\n"
        "def find_broken_links(markdown: str, known_stems: set[str]) -> list[str]:\n"
        "    return [s for s in extract_wikilink_stems(markdown) if s not in known_stems]\n"
    )
    lc15_sync_fix = (
        '"""Vault sync adapter verifying wikilink targets via leaf helper (lc15)."""\n\n'
        "from infra.lc15_leaf_links import extract_wikilink_stems\n\n"
        "def find_broken_links(markdown: str, known_stems: set[str]) -> list[str]:\n"
        "    \"\"\"Return missing stems using extract_wikilink_stems against lowercase known_stems.\"\"\"\n"
        "    norm_known = {s.lower() for s in known_stems}\n"
        "    return [s for s in extract_wikilink_stems(markdown) if s not in norm_known]\n"
    )
    lc15_test = (
        _sys_path_header()
        + "from adapters.lc15_vault_sync import find_broken_links\n"
        + "from infra.lc15_leaf_links import extract_wikilink_stems\n\n"
        + "def test_wikilink_stem_canonicalization_and_broken_check() -> None:\n"
        + "    md = 'See [[docs/SLA_Policy.md#Credit|SLA]] and [[Incident_Log#Q3]] and [[SLA_Policy]] and [[Missing]].'\n"
        + "    assert extract_wikilink_stems(md) == ['sla_policy', 'incident_log', 'missing']\n"
        + "    assert find_broken_links(md, {'SLA_Policy', 'incident_log'}) == ['missing']\n"
    )
    lc15_files = build_coding_workspace(
        "lc15",
        "wikilink stem helper",
        core_import_target="core.lc15_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc15_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc15_dispatcher",
        service_symbol="ServiceHealthDispatcher",
        distractor_count=6,
        extra_files={
            "infra/lc15_leaf_links.py": lc15_leaf_bug,
            "adapters/lc15_vault_sync.py": lc15_sync_bug,
            "tests/test_lc15.py": lc15_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_15",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "noisy_distractor_graph",
            "cartridge": "software_engineering",
            "prompt": (
                "Implement helper extract_wikilink_stems in infra/lc15_leaf_links.py "
                "and find_broken_links in adapters/lc15_vault_sync.py."
            ),
            "files": lc15_files,
            "solution_files": {
                "infra/lc15_leaf_links.py": lc15_leaf_fix,
                "adapters/lc15_vault_sync.py": lc15_sync_fix,
            },
            "baseline_solution_files": {
                "infra/lc15_leaf_links.py": lc15_leaf_base,
                "adapters/lc15_vault_sync.py": lc15_sync_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc15.py",
            "expected_touched_files": ["infra/lc15_leaf_links.py", "adapters/lc15_vault_sync.py"],
            "subagent_chore": True,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["infra/lc15_leaf_links.py", "adapters/lc15_vault_sync.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate3_tier": {
                    "targets": ["infra/lc15_leaf_links.py"],
                    "expected_tiers": ["floor", "mid"],
                },
            },
            "replay_trace": build_stress_replay_trace(
                15, provider="anthropic", task_kind="coding", subagent_chore=True, leaf_chore=True, prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_16 (broken_neighbor_resilience): Priority Work Queue & Lease Expiry
    # ------------------------------------------------------------------
    lc16_lease_bug = (
        '"""Queue item lease descriptor (lc16)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class LeaseItem:\n"
        "    job_id: str\n"
        "    priority: int\n"
        "    attempts: int = 0\n"
        "    lease_until: float = 0.0\n"
    )
    lc16_lease_fix = (
        '"""Queue item lease descriptor with expiration check (lc16)."""\n\n'
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class LeaseItem:\n"
        "    \"\"\"Tracked work item with priority, attempts, and lease_until.\"\"\"\n\n"
        "    job_id: str\n"
        "    priority: int\n"
        "    attempts: int = 0\n"
        "    lease_until: float = 0.0\n\n"
        "    def is_expired(self, now: float) -> bool:\n"
        "        \"\"\"Return True when active lease_until <= now.\"\"\"\n"
        "        return float(now) >= self.lease_until\n"
    )
    lc16_q_bug = (
        '"""Priority lease work queue with dead-letter queue (lc16)."""\n\n'
        "from typing import Callable\n"
        "from domain.lc16_lease import LeaseItem\n\n"
        "class LeaseWorkQueue:\n"
        "    def __init__(self, max_attempts: int, lease_s: float, clock: Callable[[], float]) -> None:\n"
        "        self.max_attempts = max_attempts\n"
        "        self.lease_s = float(lease_s)\n"
        "        self._clock = clock\n"
        "        self.ready: list[LeaseItem] = []\n"
        "        self.inflight: dict[str, LeaseItem] = {}\n"
        "        self.dlq: list[str] = []\n\n"
        "    def enqueue(self, job_id: str, priority: int) -> None:\n"
        "        self.ready.append(LeaseItem(job_id=job_id, priority=priority))\n\n"
        "    def claim(self) -> LeaseItem | None:\n"
        "        return self.ready.pop(0) if self.ready else None\n\n"
        "    def sweep_expired(self) -> None:\n"
        "        pass\n"
    )
    lc16_q_fix = (
        '"""Priority lease work queue with sweep_expired and DLQ routing (lc16)."""\n\n'
        "from typing import Callable\n"
        "from domain.lc16_lease import LeaseItem\n\n"
        "class LeaseWorkQueue:\n"
        "    \"\"\"Manages priority enqueue, claim, ack, and sweep_expired to DLQ.\"\"\"\n\n"
        "    def __init__(self, max_attempts: int, lease_s: float, clock: Callable[[], float]) -> None:\n"
        "        self.max_attempts = max_attempts\n"
        "        self.lease_s = float(lease_s)\n"
        "        self._clock = clock\n"
        "        self.ready: list[LeaseItem] = []\n"
        "        self.inflight: dict[str, LeaseItem] = {}\n"
        "        self.dlq: list[str] = []\n\n"
        "    def enqueue(self, job_id: str, priority: int) -> None:\n"
        "        \"\"\"Add job_id with priority to ready queue.\"\"\"\n"
        "        self.ready.append(LeaseItem(job_id=job_id, priority=priority))\n\n"
        "    def sweep_expired(self) -> None:\n"
        "        \"\"\"Requeue expired leases under max_attempts or move exhausted jobs to dlq.\"\"\"\n"
        "        now = float(self._clock())\n"
        "        expired_ids = [jid for jid, item in self.inflight.items() if item.is_expired(now)]\n"
        "        for jid in expired_ids:\n"
        "            item = self.inflight.pop(jid)\n"
        "            if item.attempts >= self.max_attempts:\n"
        "                self.dlq.append(jid)\n"
        "            else:\n"
        "                self.ready.append(item)\n\n"
        "    def claim(self) -> LeaseItem | None:\n"
        "        \"\"\"Sweep expired leases and claim highest-priority ready item.\"\"\"\n"
        "        self.sweep_expired()\n"
        "        if not self.ready:\n"
        "            return None\n"
        "        self.ready.sort(key=lambda it: -it.priority)\n"
        "        item = self.ready.pop(0)\n"
        "        item.attempts += 1\n"
        "        item.lease_until = float(self._clock()) + self.lease_s\n"
        "        self.inflight[item.job_id] = item\n"
        "        return item\n\n"
        "    def ack(self, job_id: str) -> bool:\n"
        "        \"\"\"Acknowledge completion and remove job_id from inflight.\"\"\"\n"
        "        return self.inflight.pop(job_id, None) is not None\n"
    )
    lc16_test = (
        _sys_path_header()
        + "from services.lc16_queue import LeaseWorkQueue\n\n"
        + "def test_priority_claim_lease_expiry_and_dlq() -> None:\n"
        + "    now = [0.0]\n"
        + "    q = LeaseWorkQueue(max_attempts=2, lease_s=5.0, clock=lambda: now[0])\n"
        + "    q.enqueue('low', 1)\n"
        + "    q.enqueue('high', 10)\n"
        + "    first = q.claim()\n"
        + "    assert first is not None and first.job_id == 'high' and first.attempts == 1\n"
        + "    now[0] = 5.0\n"
        + "    second = q.claim()\n"
        + "    assert second is not None and second.job_id == 'high' and second.attempts == 2\n"
        + "    now[0] = 10.0\n"
        + "    third = q.claim()\n"
        + "    assert third is not None and third.job_id == 'low'\n"
        + "    assert q.dlq == ['high']\n"
    )
    lc16_files = build_coding_workspace(
        "lc16",
        "priority lease queue",
        core_import_target="core.lc16_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc16_lease",
        domain_symbol="LeaseItem",
        service_import_target="services.lc16_queue",
        service_symbol="LeaseWorkQueue",
        broken_neighbor=True,
        extra_files={
            "domain/lc16_lease.py": lc16_lease_bug,
            "services/lc16_queue.py": lc16_q_bug,
            "tests/test_lc16.py": lc16_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_16",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "broken_neighbor_resilience",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc16_lease.py LeaseItem.is_expired and services/lc16_queue.py LeaseWorkQueue "
                "claim, sweep_expired, and ack for priority ordering and dlq routing."
            ),
            "files": lc16_files,
            "solution_files": {
                "domain/lc16_lease.py": lc16_lease_fix,
                "services/lc16_queue.py": lc16_q_fix,
            },
            "baseline_solution_files": {
                "domain/lc16_lease.py": lc16_lease_fix,
                "services/lc16_queue.py": lc16_q_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc16.py",
            "expected_touched_files": ["domain/lc16_lease.py", "services/lc16_queue.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc16_lease.py", "services/lc16_queue.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                16, provider="openai", task_kind="coding", prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_17 (broken_neighbor_resilience, 1-strike): Consistent Hash Ring
    # ------------------------------------------------------------------
    lc17_ring_bug = (
        '"""Consistent hash ring with virtual nodes (lc17)."""\n\n'
        "def hash_token(label: str) -> int:\n"
        "    return len(label)\n"
    )
    lc17_ring_fix = (
        '"""Deterministic SHA-256 hash ring helper (lc17)."""\n\n'
        "import hashlib\n\n"
        "def hash_token(label: str) -> int:\n"
        "    \"\"\"Return deterministic 64-bit integer hash from SHA-256 digest of label.\"\"\"\n"
        "    digest = hashlib.sha256(label.encode('utf-8')).digest()\n"
        "    return int.from_bytes(digest[:8], 'big')\n"
    )
    lc17_ring_flawed = (
        "import hashlib\n\n"
        "def hash_token(label: str) -> int:\n"
        "    return int.from_bytes(hashlib.sha256(label.encode('utf-8')).digest()[:8], 'big'\n"
    )
    lc17_rtr_bug = (
        '"""Consistent hash ring router (lc17)."""\n\n'
        "from domain.lc17_ring import hash_token\n\n"
        "class HashRingRouter:\n"
        "    def __init__(self, nodes: list[str], vnodes: int = 8) -> None:\n"
        "        self.nodes = list(nodes)\n"
        "        self.vnodes = vnodes\n\n"
        "    def route_key(self, key: str) -> str:\n"
        "        return self.nodes[0]\n"
    )
    lc17_rtr_fix = (
        '"""Consistent hash ring router using bisect_left and wrap-around (lc17)."""\n\n'
        "from bisect import bisect_left\n"
        "from domain.lc17_ring import hash_token\n\n"
        "class HashRingRouter:\n"
        "    \"\"\"Route keys onto virtual node ring using bisect_left with wrap-around.\"\"\"\n\n"
        "    def __init__(self, nodes: list[str], vnodes: int = 16) -> None:\n"
        "        if not nodes or vnodes <= 0:\n"
        "            raise ValueError('nodes and vnodes must be non-empty/positive')\n"
        "        self.ring: list[tuple[int, str]] = []\n"
        "        for node in sorted(set(nodes)):\n"
        "            for vn in range(vnodes):\n"
        "                self.ring.append((hash_token(f'{node}#{vn}'), node))\n"
        "        self.ring.sort(key=lambda item: item[0])\n"
        "        self._positions = [pos for pos, _ in self.ring]\n\n"
        "    def route_key(self, key: str) -> str:\n"
        "        \"\"\"Select owning physical node for key via bisect_left wrap-around.\"\"\"\n"
        "        target = hash_token(key)\n"
        "        idx = bisect_left(self._positions, target)\n"
        "        if idx >= len(self.ring):\n"
        "            idx = 0\n"
        "        return self.ring[idx][1]\n"
    )
    lc17_test = (
        _sys_path_header()
        + "from domain.lc17_ring import hash_token\n"
        + "from services.lc17_router import HashRingRouter\n\n"
        + "def test_consistent_hash_ring_deterministic_and_balanced() -> None:\n"
        + "    assert hash_token('a') != hash_token('b')\n"
        + "    r1 = HashRingRouter(['n1', 'n2', 'n3'], vnodes=32)\n"
        + "    r2 = HashRingRouter(['n3', 'n1', 'n2'], vnodes=32)\n"
        + "    keys = [f'user:{i}' for i in range(120)]\n"
        + "    assert [r1.route_key(k) for k in keys] == [r2.route_key(k) for k in keys]\n"
        + "    dist = {r1.route_key(k) for k in keys}\n"
        + "    assert dist == {'n1', 'n2', 'n3'}\n"
    )
    lc17_files = build_coding_workspace(
        "lc17",
        "consistent hash ring",
        core_import_target="core.lc17_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc17_ring",
        domain_symbol="hash_token",
        service_import_target="services.lc17_router",
        service_symbol="HashRingRouter",
        broken_neighbor=True,
        extra_files={
            "domain/lc17_ring.py": lc17_ring_bug,
            "services/lc17_router.py": lc17_rtr_bug,
            "tests/test_lc17.py": lc17_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_17",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "broken_neighbor_resilience",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix domain/lc17_ring.py hash_token using SHA-256 and update services/lc17_router.py "
                "HashRingRouter.route_key using bisect_left virtual node wrap-around."
            ),
            "files": lc17_files,
            "solution_files": {
                "domain/lc17_ring.py": lc17_ring_fix,
                "services/lc17_router.py": lc17_rtr_fix,
            },
            "baseline_solution_files": {
                "domain/lc17_ring.py": lc17_ring_fix,
                "services/lc17_router.py": lc17_rtr_fix,
            },
            "flawed_draft_files": {"domain/lc17_ring.py": lc17_ring_flawed},
            "pytest_target": "tests/test_lc17.py",
            "expected_touched_files": ["domain/lc17_ring.py", "services/lc17_router.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc17_ring.py", "services/lc17_router.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                17, provider="gemini", task_kind="coding", simulates_promotion=True, prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_18 (broken_neighbor_resilience): Merkle Tree Block Diff
    # ------------------------------------------------------------------
    lc18_mk_bug = (
        '"""Merkle tree root builder (lc18)."""\n\n'
        "import hashlib\n\n"
        "def merkle_root(chunks: list[bytes]) -> str:\n"
        "    if not chunks:\n"
        "        return hashlib.sha256(b'').hexdigest()\n"
        "    return hashlib.sha256(b''.join(chunks)).hexdigest()\n"
    )
    lc18_mk_fix = (
        '"""Pairwise SHA-256 Merkle tree root and level builder (lc18)."""\n\n'
        "import hashlib\n\n"
        "def _h(data: bytes) -> str:\n"
        "    return hashlib.sha256(data).hexdigest()\n\n"
        "def build_merkle_levels(chunks: list[bytes]) -> list[list[str]]:\n"
        "    \"\"\"Build bottom-up Merkle tree levels with odd-leaf duplication.\"\"\"\n"
        "    if not chunks:\n"
        "        return [[_h(b'')]]\n"
        "    level = [_h(c) for c in chunks]\n"
        "    levels = [level]\n"
        "    while len(level) > 1:\n"
        "        padded = list(level) if len(level) % 2 == 0 else list(level) + [level[-1]]\n"
        "        level = [\n"
        "            _h((padded[i] + padded[i + 1]).encode('ascii'))\n"
        "            for i in range(0, len(padded), 2)\n"
        "        ]\n"
        "        levels.append(level)\n"
        "    return levels\n\n"
        "def merkle_root(chunks: list[bytes]) -> str:\n"
        "    \"\"\"Return top-level Merkle root hex digest.\"\"\"\n"
        "    return build_merkle_levels(chunks)[-1][0]\n"
    )
    lc18_sync_bug = (
        '"""Merkle chunk diff service (lc18)."""\n\n'
        "from core.lc18_merkle import merkle_root\n\n"
        "def diff_chunk_indices(left: list[bytes], right: list[bytes]) -> list[int]:\n"
        "    return []\n"
    )
    lc18_sync_fix = (
        '"""Merkle chunk diff localization service (lc18)."""\n\n'
        "from core.lc18_merkle import build_merkle_levels, merkle_root\n\n"
        "def diff_chunk_indices(left: list[bytes], right: list[bytes]) -> list[int]:\n"
        "    \"\"\"Return sorted indices where left and right chunks differ using Merkle levels.\"\"\"\n"
        "    if len(left) == len(right) and merkle_root(left) == merkle_root(right):\n"
        "        return []\n"
        "    l_leaves = build_merkle_levels(left)[0] if left else []\n"
        "    r_leaves = build_merkle_levels(right)[0] if right else []\n"
        "    limit = max(len(left), len(right))\n"
        "    changed: list[int] = []\n"
        "    for idx in range(limit):\n"
        "        lh = l_leaves[idx] if idx < len(left) else None\n"
        "        rh = r_leaves[idx] if idx < len(right) else None\n"
        "        if lh != rh:\n"
        "            changed.append(idx)\n"
        "    return changed\n"
    )
    lc18_test = (
        _sys_path_header()
        + "from core.lc18_merkle import build_merkle_levels, merkle_root\n"
        + "from services.lc18_sync import diff_chunk_indices\n\n"
        + "def test_merkle_odd_leaf_and_diff_indices() -> None:\n"
        + "    a = [b'c0', b'c1', b'c2']\n"
        + "    b = [b'c0', b'XX', b'c2']\n"
        + "    assert len(build_merkle_levels(a)) == 3\n"
        + "    assert merkle_root(a) != merkle_root(b)\n"
        + "    assert diff_chunk_indices(a, a) == []\n"
        + "    assert diff_chunk_indices(a, b) == [1]\n"
        + "    assert diff_chunk_indices(a, a + [b'c3']) == [3]\n"
    )
    lc18_files = build_coding_workspace(
        "lc18",
        "merkle block diff",
        core_import_target="core.lc18_merkle",
        core_symbol="merkle_root",
        domain_import_target="domain.lc18_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc18_sync",
        service_symbol="diff_chunk_indices",
        broken_neighbor=True,
        extra_files={
            "core/lc18_merkle.py": lc18_mk_bug,
            "services/lc18_sync.py": lc18_sync_bug,
            "tests/test_lc18.py": lc18_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_18",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "broken_neighbor_resilience",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix core/lc18_merkle.py build_merkle_levels and merkle_root with odd-leaf duplication, "
                "and implement services/lc18_sync.py diff_chunk_indices."
            ),
            "files": lc18_files,
            "solution_files": {
                "core/lc18_merkle.py": lc18_mk_fix,
                "services/lc18_sync.py": lc18_sync_fix,
            },
            "baseline_solution_files": {
                "core/lc18_merkle.py": lc18_mk_fix,
                "services/lc18_sync.py": lc18_sync_fix,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc18.py",
            "expected_touched_files": ["core/lc18_merkle.py", "services/lc18_sync.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc18_merkle.py", "services/lc18_sync.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                18, provider="anthropic", task_kind="coding", prefer_decision1=True
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_19 (broken_neighbor_resilience, baseline incomplete): Saga Reverse LIFO Rollback
    # ------------------------------------------------------------------
    lc19_saga_bug = (
        '"""Saga step descriptor (lc19)."""\n\n'
        "from dataclasses import dataclass\n"
        "from typing import Callable\n\n"
        "@dataclass(frozen=True)\n"
        "class SagaStep:\n"
        "    name: str\n"
        "    execute: Callable[[], None]\n"
        "    compensate: Callable[[], None]\n"
    )
    lc19_saga_fix = (
        '"""Saga step descriptor and outcome record (lc19)."""\n\n'
        "from dataclasses import dataclass\n"
        "from typing import Callable\n\n"
        "@dataclass(frozen=True)\n"
        "class SagaStep:\n"
        "    \"\"\"Paired forward execute and rollback compensate actions.\"\"\"\n\n"
        "    name: str\n"
        "    execute: Callable[[], None]\n"
        "    compensate: Callable[[], None]\n\n"
        "@dataclass(frozen=True)\n"
        "class SagaOutcome:\n"
        "    \"\"\"Execution summary including compensated steps and failed compensations.\"\"\"\n\n"
        "    succeeded: bool\n"
        "    compensated: list[str]\n"
        "    compensation_errors: list[str]\n"
    )
    lc19_coord_bug = (
        '"""Two-phase saga coordinator (lc19)."""\n\n'
        "from domain.lc19_saga import SagaStep\n\n"
        "def run_saga(steps: list[SagaStep]) -> dict[str, object]:\n"
        "    for step in steps:\n"
        "        step.execute()\n"
        "    return {'succeeded': True, 'compensated': [], 'compensation_errors': []}\n"
    )
    lc19_coord_base = (
        '"""Two-phase saga coordinator using wrong FIFO rollback order (lc19)."""\n\n'
        "from domain.lc19_saga import SagaOutcome, SagaStep\n\n"
        "def run_saga(steps: list[SagaStep]) -> SagaOutcome:\n"
        "    completed: list[SagaStep] = []\n"
        "    try:\n"
        "        for step in steps:\n"
        "            step.execute()\n"
        "            completed.append(step)\n"
        "    except Exception:\n"
        "        compensated: list[str] = []\n"
        "        errors: list[str] = []\n"
        "        # BASELINE OMISSION: compensates in forward FIFO order instead of reverse LIFO\n"
        "        for done in completed:\n"
        "            try:\n"
        "                done.compensate()\n"
        "                compensated.append(done.name)\n"
        "            except Exception:\n"
        "                errors.append(done.name)\n"
        "        return SagaOutcome(False, compensated, errors)\n"
        "    return SagaOutcome(True, [], [])\n"
    )
    lc19_coord_fix = (
        '"""Two-phase saga coordinator executing strict reverse LIFO rollback (lc19)."""\n\n'
        "from domain.lc19_saga import SagaOutcome, SagaStep\n\n"
        "def run_saga(steps: list[SagaStep]) -> SagaOutcome:\n"
        "    \"\"\"Run steps and execute compensate in reverse LIFO order on failure, recording compensation_errors.\"\"\"\n"
        "    completed: list[SagaStep] = []\n"
        "    try:\n"
        "        for step in steps:\n"
        "            step.execute()\n"
        "            completed.append(step)\n"
        "    except Exception:\n"
        "        compensated: list[str] = []\n"
        "        errors: list[str] = []\n"
        "        for done in reversed(completed):\n"
        "            try:\n"
        "                done.compensate()\n"
        "                compensated.append(done.name)\n"
        "            except Exception:\n"
        "                errors.append(done.name)\n"
        "        return SagaOutcome(False, compensated, errors)\n"
        "    return SagaOutcome(True, [], [])\n"
    )
    lc19_test = (
        _sys_path_header()
        + "from domain.lc19_saga import SagaStep\n"
        + "from services.lc19_coordinator import run_saga\n\n"
        + "def test_saga_lifo_compensation_and_error_isolation() -> None:\n"
        + "    log: list[str] = []\n"
        + "    def boom() -> None:\n"
        + "        raise RuntimeError('step3 failed')\n"
        + "    steps = [\n"
        + "        SagaStep('s1', lambda: log.append('exec1'), lambda: log.append('comp1')),\n"
        + "        SagaStep('s2', lambda: log.append('exec2'), lambda: boom()),\n"
        + "        SagaStep('s3', lambda: boom(), lambda: log.append('comp3')),\n"
        + "    ]\n"
        + "    res = run_saga(steps)\n"
        + "    assert res.succeeded is False\n"
        + "    assert res.compensated == ['s1']\n"
        + "    assert res.compensation_errors == ['s2']\n"
        + "    assert log == ['exec1', 'exec2', 'comp1']\n"
    )
    lc19_files = build_coding_workspace(
        "lc19",
        "saga rollback coordinator",
        core_import_target="core.lc19_config",
        core_symbol="SubsystemConfig",
        domain_import_target="domain.lc19_saga",
        domain_symbol="SagaStep",
        service_import_target="services.lc19_coordinator",
        service_symbol="run_saga",
        broken_neighbor=True,
        extra_files={
            "domain/lc19_saga.py": lc19_saga_bug,
            "services/lc19_coordinator.py": lc19_coord_bug,
            "tests/test_lc19.py": lc19_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_19",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "broken_neighbor_resilience",
            "cartridge": "software_engineering",
            "prompt": (
                "Update domain/lc19_saga.py to define SagaOutcome and fix services/lc19_coordinator.py "
                "run_saga to execute reverse LIFO compensate rollback and track compensation_errors."
            ),
            "files": lc19_files,
            "solution_files": {
                "domain/lc19_saga.py": lc19_saga_fix,
                "services/lc19_coordinator.py": lc19_coord_fix,
            },
            "baseline_solution_files": {
                "domain/lc19_saga.py": lc19_saga_fix,
                "services/lc19_coordinator.py": lc19_coord_base,
            },
            "flawed_draft_files": {},
            "pytest_target": "tests/test_lc19.py",
            "expected_touched_files": ["domain/lc19_saga.py", "services/lc19_coordinator.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": False,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["domain/lc19_saga.py", "services/lc19_coordinator.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
            },
            "replay_trace": build_stress_replay_trace(
                19, provider="openai", task_kind="coding", prefer_decision1=False
            ),
        }
    )

    # ------------------------------------------------------------------
    # STRESS_LC_20 (broken_neighbor_resilience, 1-strike): Deep Merge & Masking Diff
    # ------------------------------------------------------------------
    lc20_mrg_bug = (
        '"""Non-mutating hierarchical config deep merge (lc20)."""\n\n'
        "def deep_merge_config(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:\n"
        "    # BUG: shallow copy mutates nested dicts in base\n"
        "    out = dict(base)\n"
        "    out.update(override)\n"
        "    return out\n"
    )
    lc20_mrg_fix = (
        '"""Non-mutating hierarchical config deep_merge_config helper (lc20)."""\n\n'
        "from copy import deepcopy\n\n"
        "def deep_merge_config(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:\n"
        "    \"\"\"Deep-merge override into base without mutating either input mapping.\"\"\"\n"
        "    out: dict[str, object] = deepcopy(base)\n"
        "    for key, val in override.items():\n"
        "        cur = out.get(key)\n"
        "        if isinstance(cur, dict) and isinstance(val, dict):\n"
        "            out[key] = deep_merge_config(cur, val)\n"
        "        else:\n"
        "            out[key] = deepcopy(val)\n"
        "    return out\n"
    )
    lc20_mrg_flawed = (
        "def deep_merge_config(base, override):\n"
        "    return {**base, **override\n"
    )
    lc20_env_bug = (
        '"""Config masking adapter for diagnostic diffs (lc20)."""\n\n'
        "from core.lc20_merge import deep_merge_config\n\n"
        "def render_masked_config(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:\n"
        "    return deep_merge_config(base, override)\n"
    )
    lc20_env_fix = (
        '"""Config masking adapter protecting sensitive fields in diffs (lc20)."""\n\n'
        "from core.lc20_merge import deep_merge_config\n\n"
        "_MASKED_KEYS = frozenset({'api_token', 'password', 'signing_key', 'auth_header'})\n\n"
        "def _mask_tree(node: dict[str, object]) -> dict[str, object]:\n"
        "    out: dict[str, object] = {}\n"
        "    for k, v in node.items():\n"
        "        if k.lower() in _MASKED_KEYS:\n"
        "            out[k] = '[REDACTED]'\n"
        "        elif isinstance(v, dict):\n"
        "            out[k] = _mask_tree(v)\n"
        "        else:\n"
        "            out[k] = v\n"
        "    return out\n\n"
        "def render_masked_config(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:\n"
        "    \"\"\"Merge base and override via deep_merge_config and redact sensitive keys.\"\"\"\n"
        "    return _mask_tree(deep_merge_config(base, override))\n"
    )
    lc20_test = (
        _sys_path_header()
        + "from adapters.lc20_env_loader import render_masked_config\n"
        + "from core.lc20_merge import deep_merge_config\n\n"
        + "def test_deep_merge_non_mutating_and_masked() -> None:\n"
        + "    base = {'db': {'host': 'localhost', 'port': 5432}, 'auth': {'api_token': 'demo-t1'}}\n"
        + "    ovr = {'db': {'port': 6432}, 'auth': {'region': 'eu'}}\n"
        + "    merged = deep_merge_config(base, ovr)\n"
        + "    assert merged['db'] == {'host': 'localhost', 'port': 6432}\n"
        + "    assert base['db']['port'] == 5432\n"
        + "    masked = render_masked_config(base, ovr)\n"
        + "    assert masked['auth'] == {'api_token': '[REDACTED]', 'region': 'eu'}\n"
    )
    lc20_files = build_coding_workspace(
        "lc20",
        "config merge masking",
        core_import_target="core.lc20_merge",
        core_symbol="deep_merge_config",
        domain_import_target="domain.lc20_audit_log",
        domain_symbol="DomainAuditJournal",
        service_import_target="services.lc20_dispatcher",
        service_symbol="ServiceHealthDispatcher",
        broken_neighbor=True,
        extra_files={
            "core/lc20_merge.py": lc20_mrg_bug,
            "adapters/lc20_env_loader.py": lc20_env_bug,
            "tests/test_lc20.py": lc20_test,
        },
    )
    tasks.append(
        {
            "task_id": "STRESS_LC_20",
            "task_kind": "coding",
            "suite": "stress_long_context",
            "category": "broken_neighbor_resilience",
            "cartridge": "software_engineering",
            "prompt": (
                "Fix core/lc20_merge.py deep_merge_config using deepcopy without mutating inputs, "
                "and update adapters/lc20_env_loader.py render_masked_config to redact sensitive keys."
            ),
            "files": lc20_files,
            "solution_files": {
                "core/lc20_merge.py": lc20_mrg_fix,
                "adapters/lc20_env_loader.py": lc20_env_fix,
            },
            "baseline_solution_files": {
                "core/lc20_merge.py": lc20_mrg_fix,
                "adapters/lc20_env_loader.py": lc20_env_fix,
            },
            "flawed_draft_files": {"core/lc20_merge.py": lc20_mrg_flawed},
            "pytest_target": "tests/test_lc20.py",
            "expected_touched_files": ["core/lc20_merge.py", "adapters/lc20_env_loader.py"],
            "subagent_chore": False,
            "simulates_one_strike_promotion": True,
            "stress_checks": {
                "assert_pass0_recall": {
                    "expected_any": ["core/lc20_merge.py", "adapters/lc20_env_loader.py"],
                    "top_k": 10,
                },
                "assert_gate1_turns": [
                    {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                    {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                ],
                "assert_gate4_incomplete_rejected": True,
            },
            "replay_trace": build_stress_replay_trace(
                20, provider="gemini", task_kind="coding", simulates_promotion=True, air_gapped=True
            ),
        }
    )

    return tasks


def _pad_context_section(topic: str, doc_role: str, ref_code: str, lines: list[str]) -> str:
    """Build a realistic ~2KB structured markdown source document without filler noise."""
    bullet_rows = "\n".join(f"- {ln}" for ln in lines)
    controls = "\n".join(
        [
            f"| Control-{idx:02d} | {topic} | {doc_role} | Verified ({ref_code}) |"
            for idx in range(1, 13)
        ]
    )
    notes = "\n".join(
        [
            f"### {doc_role} Operational Trace Block {b}\n"
            f"Reference `{ref_code}` records cross-team reconciliation checkpoints for `{topic}` "
            f"covering boundary validation, auditability, change-control timestamps, and escalation ownership "
            f"across regional sub-ledgers and upstream compliance manifests (checkpoint batch {ref_code}-B{b})."
            for b in range(1, 5)
        ]
    )
    return (
        f"# {topic} — {doc_role} ({ref_code})\n\n"
        f"## Core Findings and Recorded Figures\n{bullet_rows}\n\n"
        f"## Verification Control Matrix\n"
        f"| Control ID | Domain | Artifact Role | Status |\n"
        f"| :--- | :--- | :--- | :--- |\n"
        f"{controls}\n\n"
        f"{notes}\n"
    )


def _build_knowledge_files(
    prefix: str,
    topic: str,
    *,
    baseline_doc: tuple[str, str],
    addendum_doc: tuple[str, str],
    secondary_doc: tuple[str, str],
    private_doc: tuple[str, str],
    extra_docs: list[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """Assemble 11-12 interconnected docs totaling >= 21KB per knowledge task."""
    files: dict[str, str] = {
        baseline_doc[0]: baseline_doc[1],
        addendum_doc[0]: addendum_doc[1],
        secondary_doc[0]: secondary_doc[1],
        private_doc[0]: private_doc[1],
        f"docs/{prefix}_overview.md": _pad_context_section(
            topic,
            "Architecture and Program Overview",
            f"{prefix.upper()}-OVR",
            [
                f"Primary baseline artifact: {baseline_doc[0]}",
                f"Superseding 2026 addendum: {addendum_doc[0]}",
                f"Operational log artifact: {secondary_doc[0]}",
                "All downstream syntheses must prefer errata/2026_addendum.md figures over 2025 baseline values.",
            ],
        ),
        f"notes/{prefix}_q4_review_minutes.md": _pad_context_section(
            topic,
            "Q4 Governance Committee Minutes",
            f"{prefix.upper()}-MIN",
            [
                "Committee flagged stale 2025 estimates pending formal 2026 addendum publication.",
                "Action item: require explicit citation to errata/2026_addendum.md in all final deliverables.",
                "Privacy officer confirmed Tier-1 restricted records remain strictly air-gapped on local classifiers.",
            ],
        ),
        f"notes/{prefix}_signoff_checklist.md": _pad_context_section(
            topic,
            "Executive Sign-Off Checklist",
            f"{prefix.upper()}-CHK",
            [
                "Confirm numeric precision within 0.01 tolerance and max 2 decimals for USD/EUR items.",
                "Confirm zero unresolved placeholders or unredacted personal identifiers in published artifacts.",
                "Confirm every cross-reference and source path maps cleanly to existing workspace files.",
            ],
        ),
        f"archive/{prefix}_2024_historical.md": _pad_context_section(
            topic,
            "2024 Historical Baseline Archive (Superseded)",
            f"{prefix.upper()}-ARC24",
            [
                "Historical 2024 figures retained strictly for year-over-year lineage comparison.",
                "Legacy metrics in this archive (9999.00 placeholder baseline) are obsolete.",
                "Do not report 2024 archive values as current 2026 audited figures.",
            ],
        ),
        f"archive/{prefix}_2025_preliminary_draft.md": _pad_context_section(
            topic,
            "2025 Preliminary Draft Notes (Superseded)",
            f"{prefix.upper()}-ARC25",
            [
                "Early 2025 working draft prior to audit adjustments in errata/2026_addendum.md.",
                "Unverified provisional estimates superseded by 2026 addendum revisions.",
                "Retained in archive/ to validate long-context noise rejection.",
            ],
        ),
        f"sources/{prefix}_controls_matrix.md": _pad_context_section(
            topic,
            "Operational Controls Matrix",
            f"{prefix.upper()}-CTRL",
            [
                "Control owner attestation logged for continuous compliance verification.",
                "Dual-control escalation threshold enforcement mapped to domain cartridge policy.",
                "Traceability established across baseline specification, addendum, and operational logs.",
            ],
        ),
        f"sources/{prefix}_regional_schedule.csv": (
            "region,quarter,status,verified_weight,notes\n"
            + "\n".join(
                f"region_{r:02d},2026-Q1,verified,{100 + r * 7}.00,{topic} schedule checkpoint {r}"
                for r in range(1, 25)
            )
            + "\n"
        ),
    }
    if extra_docs:
        for path, body in extra_docs:
            files[path] = body
    return files


def get_stress_knowledge_tasks() -> list[dict[str, Any]]:
    """Build STRESS_LC_21 through STRESS_LC_35 across 3 domain cartridges."""
    tasks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # KNOWLEDGE VAULT (STRESS_LC_21 .. STRESS_LC_25)
    # ------------------------------------------------------------------
    vault_specs: list[dict[str, Any]] = [
        {
            "idx": 21,
            "tid": "STRESS_LC_21",
            "slug": "lc21",
            "topic": "Multi-Region Cloud SLA Outage Audit",
            "base_file": "docs/lc21_sla_baseline_2025.md",
            "sec_file": "docs/lc21_outage_events.md",
            "priv_file": "vault/private/lc21_responder_roster.md",
            "leaks": ["214-88-3901", "priv-pager-lc21-884"],
            "old_vals": {"uptime_pct": 99.65, "sla_credit_usd": 3200.00},
            "new_vals": {"uptime_pct": 99.18, "sla_credit_usd": 7450.00},
            "clauses": ["Executive Summary", "Addendum Reconciliation", "Redacted Privacy Controls"],
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 22,
            "tid": "STRESS_LC_22",
            "slug": "lc22",
            "topic": "Zero-Trust IAM Privileged Access Audit",
            "base_file": "docs/lc22_iam_baseline_2025.md",
            "sec_file": "docs/lc22_token_inventory.md",
            "priv_file": "vault/private/lc22_contractor_vault.md",
            "leaks": ["356-42-7718", "priv-iam-lc22-910"],
            "old_vals": {"dormant_keys_count": 12.0, "mfa_coverage_pct": 91.50},
            "new_vals": {"dormant_keys_count": 4.0, "mfa_coverage_pct": 98.40},
            "clauses": ["Access Audit Overview", "Addendum Reconciliation", "Air-Gap Privacy Notice"],
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 23,
            "tid": "STRESS_LC_23",
            "slug": "lc23",
            "topic": "Data Center Cooling & Egress Recovery Audit",
            "base_file": "docs/lc23_capacity_baseline_2025.md",
            "sec_file": "docs/lc23_egress_telemetry.md",
            "priv_file": "vault/private/lc23_facility_custodians.md",
            "leaks": ["472-61-2290", "priv-facility-lc23-33"],
            "old_vals": {"pue_ratio": 1.48, "reclaimed_egress_usd": 4100.00},
            "new_vals": {"pue_ratio": 1.29, "reclaimed_egress_usd": 6890.50},
            "clauses": ["Facility Audit Summary", "Addendum Reconciliation", "Confidentiality Safeguards"],
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 24,
            "tid": "STRESS_LC_24",
            "slug": "lc24",
            "topic": "Supply-Chain Artifact Provenance & SBOM Audit",
            "base_file": "docs/lc24_sbom_baseline_2025.md",
            "sec_file": "docs/lc24_signature_scan.md",
            "priv_file": "vault/private/lc24_signing_officers.md",
            "leaks": ["583-19-6642", "priv-sbom-lc24-702"],
            "old_vals": {"signed_artifacts_pct": 84.00, "remediation_cost_usd": 2900.00},
            "new_vals": {"signed_artifacts_pct": 99.25, "remediation_cost_usd": 1425.75},
            "clauses": ["Provenance Findings", "Addendum Reconciliation", "Redacted Officer Attestation"],
            "baseline_fails": False,
            "promote": True,
        },
        {
            "idx": 25,
            "tid": "STRESS_LC_25",
            "slug": "lc25",
            "topic": "DR Failover Recovery & Backup Integrity Audit",
            "base_file": "docs/lc25_dr_baseline_2025.md",
            "sec_file": "docs/lc25_restore_drill.md",
            "priv_file": "vault/private/lc25_drill_leads.md",
            "leaks": ["694-55-8013", "priv-dr-lc25-518"],
            "old_vals": {"rto_minutes": 45.0, "verified_restore_pct": 94.10},
            "new_vals": {"rto_minutes": 14.50, "verified_restore_pct": 99.85},
            "clauses": ["Resilience Summary", "Addendum Reconciliation", "Privacy Air-Gap Attestation"],
            "baseline_fails": True,
            "promote": False,
        },
    ]

    for sp in vault_specs:
        slug = sp["slug"]
        k_keys = list(sp["new_vals"].keys())
        base_doc = (
            sp["base_file"],
            _pad_context_section(
                sp["topic"],
                "2025 Baseline Specification (Check errata/2026_addendum.md for Updates)",
                f"{slug.upper()}-BASE25",
                [
                    f"Initial 2025 {k_keys[0]}: {sp['old_vals'][k_keys[0]]:.2f}",
                    f"Initial 2025 {k_keys[1]}: {sp['old_vals'][k_keys[1]]:.2f}",
                    "Note: superseded by dated errata/2026_addendum.md published 2026-02-15.",
                ],
            ),
        )
        add_doc = (
            "errata/2026_addendum.md",
            _pad_context_section(
                sp["topic"],
                "2026 Superseding Addendum (Effective 2026-02-15)",
                f"{slug.upper()}-ERR26",
                [
                    f"SUPERSEDING CORRECTION: Effective 2026-02-15, replace 2025 baseline values in {sp['base_file']}.",
                    f"Updated audited {k_keys[0]}: {sp['new_vals'][k_keys[0]]:.2f}",
                    f"Updated audited {k_keys[1]}: {sp['new_vals'][k_keys[1]]:.2f}",
                    "All compliance and audit reports must cite [[2026_addendum]] and use these corrected numbers.",
                ],
            ),
        )
        sec_doc = (
            sp["sec_file"],
            _pad_context_section(
                sp["topic"],
                "Verified Telemetry & Incident Log",
                f"{slug.upper()}-LOG",
                [
                    f"Cross-checked field telemetry aligning with errata/2026_addendum.md.",
                    f"Corroborated {k_keys[0]}={sp['new_vals'][k_keys[0]]:.2f} and {k_keys[1]}={sp['new_vals'][k_keys[1]]:.2f}.",
                    "Operator escalation records stored strictly under Tier-1 air-gap controls.",
                ],
            ),
        )
        priv_doc = (
            sp["priv_file"],
            (
                f"# [private] {sp['topic']} Tier-1 Restricted Roster\n\n"
                f"CONFIDENTIAL AIR-GAPPED RECORDS — NEVER TRANSMIT OUTSIDE LOCAL ENCLAVE.\n"
                f"- Primary Custodian Identifier: {sp['leaks'][0]}\n"
                f"- Internal Enclave Token: {sp['leaks'][1]}\n\n"
                + _pad_context_section(
                    sp["topic"],
                    "Tier-1 Air-Gap Custody Log",
                    f"{slug.upper()}-PRIV",
                    [
                        "All personal identifiers in this file must be [REDACTED] in external audit markdown.",
                        "Local onnx_local classifier processing enforced.",
                    ],
                )
            ),
        )
        files = _build_knowledge_files(
            slug,
            sp["topic"],
            baseline_doc=base_doc,
            addendum_doc=add_doc,
            secondary_doc=sec_doc,
            private_doc=priv_doc,
        )
        base_stem = Path(sp["base_file"]).stem
        sec_stem = Path(sp["sec_file"]).stem
        out_file = f"reports/{slug}_audit.md"
        sol_md = (
            f"# {sp['topic']}\n\n"
            f"## {sp['clauses'][0]}\n"
            f"Synthesized {sp['base_file']} ([[{base_stem}]]) and {sp['sec_file']} ([[{sec_stem}]]) "
            f"with superseding corrections from errata/2026_addendum.md ([[2026_addendum]]).\n\n"
            f"## {sp['clauses'][1]}\n"
            f"Applied dated 2026 addendum overrides replacing obsolete 2025 baseline estimates:\n"
            f"- **{k_keys[0]}**: {sp['new_vals'][k_keys[0]]:.2f}\n"
            f"- **{k_keys[1]}**: {sp['new_vals'][k_keys[1]]:.2f}\n\n"
            f"## {sp['clauses'][2]}\n"
            f"Audited {sp['priv_file']} under local air-gap rules; all private identifiers are [REDACTED]. "
            f"Citations: [[2026_addendum]], [[{base_stem}]], [[{sec_stem}]].\n"
        )
        if sp["baseline_fails"]:
            base_md = (
                f"# {sp['topic']}\n\n"
                f"## {sp['clauses'][0]}\n"
                f"Synthesized {sp['base_file']} ([[{base_stem}]]) without checking 2026 addendum.\n\n"
                f"## {sp['clauses'][1]}\n"
                f"- **{k_keys[0]}**: {sp['old_vals'][k_keys[0]]:.2f}\n"
                f"- **{k_keys[1]}**: {sp['old_vals'][k_keys[1]]:.2f}\n\n"
                f"## {sp['clauses'][2]}\n"
                f"Reviewed {sp['priv_file']} (custodian {sp['leaks'][0]}).\n"
            )
        else:
            base_md = sol_md

        flawed: dict[str, str] = {}
        if sp["promote"]:
            flawed = {
                out_file: (
                    f"# {sp['topic']} Draft\n\n"
                    f"## {sp['clauses'][0]}\n"
                    "Preliminary notes [citation needed] referencing [[nonexistent_2026_note]].\n"
                )
            }

        prompt = (
            f"Write {out_file} synthesizing errata/2026_addendum.md, {sp['base_file']}, and {sp['sec_file']} "
            f"with {sp['clauses'][0]}, {sp['clauses'][1]} ({k_keys[0]}, {k_keys[1]}), "
            f"{sp['clauses'][2]} redacting {sp['priv_file']}, and wikilink citations."
        )
        tasks.append(
            {
                "task_id": sp["tid"],
                "task_kind": "knowledge",
                "suite": "stress_long_context",
                "category": "multi_doc_audit",
                "cartridge": "knowledge_vault",
                "prompt": prompt,
                "files": files,
                "output_file": out_file,
                "solution_files": {out_file: sol_md},
                "baseline_solution_files": {out_file: base_md},
                "flawed_draft_files": flawed,
                "ground_truth": {
                    "required_citations": ["[[2026_addendum]]", f"[[{base_stem}]]", f"[[{sec_stem}]]"],
                    "expected_numeric_balances": sp["new_vals"],
                    "forbidden_leaks": sp["leaks"],
                    "required_clauses": sp["clauses"],
                },
                "expected_touched_files": [out_file],
                "simulates_one_strike_promotion": sp["promote"],
                "stress_checks": {
                    "assert_airgap_zero_egress": True,
                    "assert_pass0_recall": {
                        "expected_any": ["errata/2026_addendum.md", sp["base_file"]],
                        "top_k": 10,
                    },
                    "assert_gate1_turns": [
                        {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                        {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                    ],
                    **({"assert_gate4_incomplete_rejected": True} if sp["promote"] else {}),
                },
                "replay_trace": build_stress_replay_trace(
                    sp["idx"],
                    provider=("anthropic", "openai", "gemini")[sp["idx"] % 3],
                    task_kind="knowledge",
                    simulates_promotion=sp["promote"],
                    air_gapped=True,
                ),
            }
        )

    # ------------------------------------------------------------------
    # FINANCIAL OPS (STRESS_LC_26 .. STRESS_LC_30)
    # ------------------------------------------------------------------
    fin_specs: list[dict[str, Any]] = [
        {
            "idx": 26,
            "tid": "STRESS_LC_26",
            "slug": "lc26",
            "topic": "Q1 Treasury Settlement & FX Fee Adjustment",
            "base_file": "sources/lc26_bank_ledger_2025.csv",
            "sec_file": "sources/lc26_clearing_summary.md",
            "priv_file": "payroll/lc26_treasury_signers.csv",
            "leaks": ["712-34-9081", "pay-wire-lc26-441"],
            "old_vals": {"total_debits_usd": 92000.00, "net_settled_usd": 90850.00},
            "new_vals": {"total_debits_usd": 96450.50, "wire_fee_usd": 245.50, "net_settled_usd": 96205.00},
            "entries": [
                {"account": "1010-Treasury-Cash", "currency": "USD", "debit": 96205.00, "credit": 0.00},
                {"account": "6120-Wire-Fees", "currency": "USD", "debit": 245.50, "credit": 0.00},
                {"account": "1200-Merchant-Clearing", "currency": "USD", "debit": 0.00, "credit": 96450.50},
            ],
            "clauses": ["Double-Entry Balance Verification", "Addendum FX Fee Tie-Out"],
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 27,
            "tid": "STRESS_LC_27",
            "slug": "lc27",
            "topic": "AP Vendor Rebate & Credit Memo Reconciliation",
            "base_file": "sources/lc27_ap_schedule_2025.csv",
            "sec_file": "sources/lc27_vendor_rebates.md",
            "priv_file": "tax_ids/lc27_vendor_w9.txt",
            "leaks": ["829-17-4450", "w9-priv-lc27-880"],
            "old_vals": {"gross_ap_usd": 51000.00, "net_payable_usd": 49000.00},
            "new_vals": {"gross_ap_usd": 54800.40, "rebate_credit_usd": 3150.40, "net_payable_usd": 51650.00},
            "entries": [
                {"account": "5310-Vendor-Compute", "currency": "USD", "debit": 54800.40, "credit": 0.00},
                {"account": "2000-AP-Payable", "currency": "USD", "debit": 0.00, "credit": 51650.00},
                {"account": "5315-Vendor-Rebates", "currency": "USD", "debit": 0.00, "credit": 3150.40},
            ],
            "clauses": ["Double-Entry Balance Verification", "Vendor Tax ID Redaction"],
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 28,
            "tid": "STRESS_LC_28",
            "slug": "lc28",
            "topic": "Deferred Enterprise SaaS Revenue Recognition Close",
            "base_file": "sources/lc28_saas_bookings_2025.csv",
            "sec_file": "sources/lc28_asc606_rules.md",
            "priv_file": "tax_ids/lc28_customer_tax.txt",
            "leaks": ["341-78-9205", "ein-priv-lc28-119"],
            "old_vals": {"total_billed_usd": 120000.00, "recognized_q1_usd": 30000.00},
            "new_vals": {"total_billed_usd": 128400.00, "recognized_q1_usd": 32100.00, "deferred_balance_usd": 96300.00},
            "entries": [
                {"account": "1100-AR-Enterprise", "currency": "USD", "debit": 128400.00, "credit": 0.00},
                {"account": "4010-Recognized-Revenue", "currency": "USD", "debit": 0.00, "credit": 32100.00},
                {"account": "2400-Deferred-Revenue", "currency": "USD", "debit": 0.00, "credit": 96300.00},
            ],
            "clauses": ["ASC 606 Revenue Tie-Out", "Double-Entry Balance Verification"],
            "baseline_fails": False,
            "promote": True,
        },
        {
            "idx": 29,
            "tid": "STRESS_LC_29",
            "slug": "lc29",
            "topic": "Payroll Tax Withholding & Benefits Clearing Close",
            "base_file": "sources/lc29_payroll_summary_2025.csv",
            "sec_file": "sources/lc29_benefits_memo.md",
            "priv_file": "payroll/lc29_employee_ssns.csv",
            "leaks": ["498-23-7741", "pay-priv-lc29-604"],
            "old_vals": {"gross_payroll_usd": 180000.00, "net_employee_pay_usd": 135000.00},
            "new_vals": {"gross_payroll_usd": 186500.80, "tax_withheld_usd": 48120.30, "net_employee_pay_usd": 138380.50},
            "entries": [
                {"account": "5200-Payroll-Wages", "currency": "USD", "debit": 186500.80, "credit": 0.00},
                {"account": "2150-Payroll-Tax-Payable", "currency": "USD", "debit": 0.00, "credit": 48120.30},
                {"account": "2100-Net-Wages-Payable", "currency": "USD", "debit": 0.00, "credit": 138380.50},
            ],
            "clauses": ["Payroll Withholding Tie-Out", "Employee Privacy Redaction"],
            "baseline_fails": True,
            "promote": False,
        },
        {
            "idx": 30,
            "tid": "STRESS_LC_30",
            "slug": "lc30",
            "topic": "Corporate Card Chargeback & Travel Expense Close",
            "base_file": "sources/lc30_card_feed_2025.csv",
            "sec_file": "sources/lc30_chargeback_log.md",
            "priv_file": "payroll/lc30_cardholders.csv",
            "leaks": ["605-92-1347", "card-priv-lc30-287"],
            "old_vals": {"gross_spend_usd": 24000.00, "net_expense_usd": 23500.00},
            "new_vals": {"gross_spend_usd": 27410.65, "chargeback_credit_usd": 1160.65, "net_expense_usd": 26250.00},
            "entries": [
                {"account": "6400-Travel-Expense", "currency": "USD", "debit": 27410.65, "credit": 0.00},
                {"account": "2110-Corp-Card-Payable", "currency": "USD", "debit": 0.00, "credit": 26250.00},
                {"account": "6400-Travel-Expense", "currency": "USD", "debit": 0.00, "credit": 1160.65},
            ],
            "clauses": ["Card Close Balance Check", "Dual-Control Threshold Sign-Off"],
            "baseline_fails": True,
            "promote": False,
        },
    ]

    for sp in fin_specs:
        slug = sp["slug"]
        n_keys = list(sp["new_vals"].keys())
        o_keys = list(sp["old_vals"].keys())
        base_doc = (
            sp["base_file"],
            "line_id,metric,currency,amount,note\n"
            + f"1,{o_keys[0]},USD,{sp['old_vals'][o_keys[0]]:.2f},2025 preliminary figure superseded by errata/2026_addendum.md\n"
            + f"2,{o_keys[1]},USD,{sp['old_vals'][o_keys[1]]:.2f},2025 preliminary figure superseded by errata/2026_addendum.md\n"
            + "\n".join(f"{r},aux_line_{r},USD,{100 + r}.00,supporting schedule row" for r in range(3, 28)),
        )
        add_doc = (
            "errata/2026_addendum.md",
            _pad_context_section(
                sp["topic"],
                "2026 Financial Close Superseding Addendum",
                f"{slug.upper()}-FIN26",
                [
                    f"SUPERSEDING ADJUSTMENT: Replace preliminary figures in {sp['base_file']} with audited 2026 values:",
                    *(f"{k}: {v:.2f} USD" for k, v in sp["new_vals"].items()),
                    "Journal entries must preserve exact double-entry debit == credit balance and 2-decimal precision.",
                ],
            ),
        )
        sec_doc = (
            sp["sec_file"],
            _pad_context_section(
                sp["topic"],
                "Reconciliation Support Memo",
                f"{slug.upper()}-SUP",
                [
                    *(f"Audited {k}: {v:.2f} USD" for k, v in sp["new_vals"].items()),
                    "Verified against errata/2026_addendum.md and dual-control approval policy.",
                ],
            ),
        )
        priv_doc = (
            sp["priv_file"],
            (
                f"# [confidential] Tier-1 Restricted Financial Identifiers ({sp['topic']})\n"
                f"identifier_1={sp['leaks'][0]}\n"
                f"identifier_2={sp['leaks'][1]}\n"
                + "\n".join(f"masked_placeholder_row_{r}=REDACTED_{r:03d}" for r in range(1, 35))
                + "\n"
            ),
        )
        files = _build_knowledge_files(
            slug,
            sp["topic"],
            baseline_doc=base_doc,
            addendum_doc=add_doc,
            secondary_doc=sec_doc,
            private_doc=priv_doc,
        )
        out_file = f"ledgers/{slug}_reconciled.json"
        sol_obj = {
            "task_id": sp["tid"],
            "citations": ["errata/2026_addendum.md", sp["base_file"], sp["sec_file"]],
            "reconciliation_clauses": sp["clauses"],
            "summary": (
                f"Reconcile errata/2026_addendum.md, {sp['base_file']}, and {sp['sec_file']} "
                f"into balanced JSON {out_file}; {', '.join(n_keys)} verified and {sp['priv_file']} redacted."
            ),
            **sp["new_vals"],
            "entries": sp["entries"],
        }
        sol_json = json.dumps(sol_obj, indent=2)
        if sp["baseline_fails"]:
            base_obj = {
                "task_id": sp["tid"],
                "citations": [sp["base_file"]],
                "reconciliation_clauses": [sp["clauses"][0]],
                "summary": f"Used preliminary 2025 values and exposed {sp['leaks'][0]}",
                **sp["old_vals"],
                "entries": sp["entries"],
            }
            base_json = json.dumps(base_obj, indent=2)
        else:
            base_json = sol_json

        flawed = {}
        if sp["promote"]:
            flawed = {
                out_file: json.dumps(
                    {
                        "task_id": sp["tid"],
                        "citations": [sp["base_file"]],
                        "entries": [
                            {"account": "1100-AR", "currency": "USD", "debit": 128400.005, "credit": 0.00},
                            {"account": "2400-Def", "currency": "USD", "debit": 0.00, "credit": 120000.00},
                        ],
                    },
                    indent=2,
                )
            }

        prompt = (
            f"Write balanced JSON {out_file} reconciling errata/2026_addendum.md, {sp['base_file']}, "
            f"and {sp['sec_file']} with {', '.join(n_keys)}, {' and '.join(sp['clauses'])}, "
            f"and private identifiers in {sp['priv_file']} redacted."
        )
        tasks.append(
            {
                "task_id": sp["tid"],
                "task_kind": "knowledge",
                "suite": "stress_long_context",
                "category": "financial_reconciliation",
                "cartridge": "financial_ops",
                "prompt": prompt,
                "files": files,
                "output_file": out_file,
                "solution_files": {out_file: sol_json},
                "baseline_solution_files": {out_file: base_json},
                "flawed_draft_files": flawed,
                "ground_truth": {
                    "required_citations": ["errata/2026_addendum.md", sp["base_file"], sp["sec_file"]],
                    "expected_numeric_balances": sp["new_vals"],
                    "forbidden_leaks": sp["leaks"],
                    "required_clauses": sp["clauses"],
                },
                "expected_touched_files": [out_file],
                "simulates_one_strike_promotion": sp["promote"],
                "stress_checks": {
                    "assert_airgap_zero_egress": True,
                    "assert_pass0_recall": {
                        "expected_any": ["errata/2026_addendum.md", sp["sec_file"]],
                        "top_k": 10,
                    },
                    "assert_gate1_turns": [
                        {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                        {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                    ],
                    **({"assert_gate4_incomplete_rejected": True} if sp["promote"] else {}),
                },
                "replay_trace": build_stress_replay_trace(
                    sp["idx"],
                    provider=("anthropic", "openai", "gemini")[sp["idx"] % 3],
                    task_kind="knowledge",
                    simulates_promotion=sp["promote"],
                    air_gapped=True,
                ),
            }
        )

    # ------------------------------------------------------------------
    # LEGAL REDLINE (STRESS_LC_31 .. STRESS_LC_35)
    # ------------------------------------------------------------------
    legal_specs: list[dict[str, Any]] = [
        {
            "idx": 31,
            "tid": "STRESS_LC_31",
            "slug": "lc31",
            "topic": "Enterprise Cloud MSA Liability & Cure Window Redline",
            "base_file": "policies/lc31_msa_playbook_2025.md",
            "sec_file": "contracts/lc31_vendor_draft.md",
            "priv_file": "privileged/lc31_counsel_strategy.md",
            "leaks": ["AC-PRIV-LC31-7712", "441-82-3905"],
            "old_vals": {"liability_cap_usd": 250000.00, "cure_days": 15.0},
            "new_vals": {"liability_cap_usd": 600000.00, "cure_days": 30.0},
            "extra_clause": "Grey-Zone Escalation",
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 32,
            "tid": "STRESS_LC_32",
            "slug": "lc32",
            "topic": "GDPR & Subprocessor DPA Breach SLA Redline",
            "base_file": "policies/lc32_dpa_standard_2025.md",
            "sec_file": "contracts/lc32_dpa_proposal.md",
            "priv_file": "attorney_client/lc32_litigation_memo.md",
            "leaks": ["AC-PRIV-LC32-9940", "518-63-2049"],
            "old_vals": {"breach_cap_usd": 150000.00, "notice_hours": 72.0},
            "new_vals": {"breach_cap_usd": 500000.00, "notice_hours": 24.0},
            "extra_clause": "Subprocessor Audit SLA",
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 33,
            "tid": "STRESS_LC_33",
            "slug": "lc33",
            "topic": "Contractor IP Assignment & Indemnity Redline",
            "base_file": "policies/lc33_ip_playbook_2025.md",
            "sec_file": "contracts/lc33_contractor_sow.md",
            "priv_file": "privileged/lc33_inventor_records.md",
            "leaks": ["AC-PRIV-LC33-3108", "629-47-8812"],
            "old_vals": {"ip_indemnity_usd": 300000.00, "survival_years": 2.0},
            "new_vals": {"ip_indemnity_usd": 850000.00, "survival_years": 5.0},
            "extra_clause": "IP Infringement Indemnity",
            "baseline_fails": False,
            "promote": True,
        },
        {
            "idx": 34,
            "tid": "STRESS_LC_34",
            "slug": "lc34",
            "topic": "SaaS Auto-Renewal Uplift & Termination Notice Redline",
            "base_file": "policies/lc34_procurement_2025.md",
            "sec_file": "contracts/lc34_order_form.md",
            "priv_file": "attorney_client/lc34_walkaway_brief.md",
            "leaks": ["AC-PRIV-LC34-5521", "734-19-6052"],
            "old_vals": {"max_uplift_pct": 8.0, "notice_days": 60.0},
            "new_vals": {"max_uplift_pct": 4.0, "notice_days": 30.0},
            "extra_clause": "Renewal Price Uplift Cap",
            "baseline_fails": False,
            "promote": False,
        },
        {
            "idx": 35,
            "tid": "STRESS_LC_35",
            "slug": "lc35",
            "topic": "Reseller Cyber Insurance & Audit Rights Redline",
            "base_file": "policies/lc35_reseller_standard_2025.md",
            "sec_file": "contracts/lc35_reseller_rider.md",
            "priv_file": "privileged/lc35_partner_notes.md",
            "leaks": ["AC-PRIV-LC35-8462", "851-36-7490"],
            "old_vals": {"cyber_insurance_usd": 1000000.00, "audit_notice_days": 30.0},
            "new_vals": {"cyber_insurance_usd": 2500000.00, "audit_notice_days": 15.0},
            "extra_clause": "Annual Audit Rights",
            "baseline_fails": True,
            "promote": False,
        },
    ]

    for sp in legal_specs:
        slug = sp["slug"]
        n_keys = list(sp["new_vals"].keys())
        base_doc = (
            sp["base_file"],
            _pad_context_section(
                sp["topic"],
                "2025 Legal Playbook Standard (Check errata/2026_addendum.md)",
                f"{slug.upper()}-LEG25",
                [
                    f"Legacy 2025 {n_keys[0]}: {sp['old_vals'][n_keys[0]]:.2f}",
                    f"Legacy 2025 {n_keys[1]}: {sp['old_vals'][n_keys[1]]:.2f}",
                    "Mandatory provisions: Limitation of Liability, Termination, Governing Law, Confidentiality.",
                ],
            ),
        )
        add_doc = (
            "errata/2026_addendum.md",
            _pad_context_section(
                sp["topic"],
                "2026 Legal Playbook Addendum (Effective 2026-03-01)",
                f"{slug.upper()}-ERR26",
                [
                    f"SUPERSEDING MANDATE: Override 2025 limits in {sp['base_file']} with 2026 thresholds:",
                    f"Updated required {n_keys[0]}: {sp['new_vals'][n_keys[0]]:.2f}",
                    f"Updated required {n_keys[1]}: {sp['new_vals'][n_keys[1]]:.2f}",
                    f"Include mandatory {sp['extra_clause']} and resolve all placeholder markers.",
                ],
            ),
        )
        sec_doc = (
            sp["sec_file"],
            _pad_context_section(
                sp["topic"],
                "Vendor Draft Contract Markup",
                f"{slug.upper()}-CON",
                [
                    "Vendor draft omits required Limitation of Liability caps and uses short Termination cures.",
                    "Must be redlined to conform with errata/2026_addendum.md.",
                ],
            ),
        )
        priv_doc = (
            sp["priv_file"],
            (
                f"# [privileged] Attorney-Client Negotiation Memorandum ({sp['topic']})\n\n"
                f"- Privileged Counsel Marker: {sp['leaks'][0]}\n"
                f"- Reviewer Tax/Personal Identifier: {sp['leaks'][1]}\n\n"
                + _pad_context_section(
                    sp["topic"],
                    "Privileged Air-Gapped Counsel Guidance",
                    f"{slug.upper()}-PRIV",
                    [
                        "Attorney-client work product strictly excluded from external redline memoranda.",
                        "Zero cloud egress permitted.",
                    ],
                )
            ),
        )
        files = _build_knowledge_files(
            slug,
            sp["topic"],
            baseline_doc=base_doc,
            addendum_doc=add_doc,
            secondary_doc=sec_doc,
            private_doc=priv_doc,
        )
        out_file = f"redlines/{slug}_memo.md"
        clauses = ["Limitation of Liability", "Termination", "Governing Law", "Confidentiality", sp["extra_clause"]]
        sol_md = (
            f"# {sp['topic']} Memorandum\n\n"
            f"Citations: errata/2026_addendum.md, {sp['base_file']}, and {sp['sec_file']} "
            f"(with {sp['priv_file']} kept air-gapped and redacted).\n\n"
            f"## Section 1: Limitation of Liability\n"
            f"Restore Limitation of Liability coverage per errata/2026_addendum.md and {sp['base_file']}.\n"
            f"- **{n_keys[0]}**: {sp['new_vals'][n_keys[0]]:.2f}\n\n"
            f"## Section 2: Termination\n"
            f"Enforce Termination and {sp['extra_clause']} cure mechanics aligning with {sp['sec_file']} redlines.\n"
            f"- **{n_keys[1]}**: {sp['new_vals'][n_keys[1]]:.2f}\n\n"
            f"## Section 3: Governing Law\n"
            f"Establish State of Delaware Governing Law and dispute venue referencing Section 1 and Section 2.\n\n"
            f"## Section 4: Confidentiality\n"
            f"Enforce mutual Confidentiality survival obligations and confirm zero privileged marker exposure.\n"
        )
        if sp["baseline_fails"]:
            base_md = (
                f"# {sp['topic']} Memorandum\n\n"
                f"Citations: {sp['base_file']}.\n\n"
                f"## Section 2: Termination\n"
                f"- **{n_keys[0]}**: {sp['old_vals'][n_keys[0]]:.2f}\n"
                f"- **{n_keys[1]}**: {sp['old_vals'][n_keys[1]]:.2f}\n"
                f"Internal counsel marker: {sp['leaks'][0]}.\n"
            )
        else:
            base_md = sol_md

        flawed = {}
        if sp["promote"]:
            flawed = {
                out_file: (
                    f"# {sp['topic']} Draft\n\n"
                    f"## Section 1: Limitation of Liability\n"
                    "Liability limit is [TBD] pending Section 99 counsel sign-off.\n"
                )
            }

        prompt = (
            f"Write {out_file} redlining {sp['sec_file']} against errata/2026_addendum.md and {sp['base_file']}. "
            f"Include Section 1 Limitation of Liability ({n_keys[0]}), Section 2 Termination ({n_keys[1]}), "
            f"Section 3 Governing Law, Section 4 Confidentiality, {sp['extra_clause']}, and redact {sp['priv_file']}."
        )
        tasks.append(
            {
                "task_id": sp["tid"],
                "task_kind": "knowledge",
                "suite": "stress_long_context",
                "category": "policy_compliance",
                "cartridge": "legal_redline",
                "prompt": prompt,
                "files": files,
                "output_file": out_file,
                "solution_files": {out_file: sol_md},
                "baseline_solution_files": {out_file: base_md},
                "flawed_draft_files": flawed,
                "ground_truth": {
                    "required_citations": ["errata/2026_addendum.md", sp["base_file"], sp["sec_file"]],
                    "expected_numeric_balances": sp["new_vals"],
                    "forbidden_leaks": sp["leaks"],
                    "required_clauses": clauses,
                },
                "expected_touched_files": [out_file],
                "simulates_one_strike_promotion": sp["promote"],
                "stress_checks": {
                    "assert_airgap_zero_egress": True,
                    "assert_pass0_recall": {
                        "expected_any": ["errata/2026_addendum.md", sp["base_file"]],
                        "top_k": 10,
                    },
                    "assert_gate1_turns": [
                        {"turn_index": 2, "compact_reset": False, "expect_empty_hints": True},
                        {"turn_index": 6, "compact_reset": True, "expect_hints_nonempty": True},
                    ],
                    **({"assert_gate4_incomplete_rejected": True} if sp["promote"] else {}),
                },
                "replay_trace": build_stress_replay_trace(
                    sp["idx"],
                    provider=("anthropic", "openai", "gemini")[sp["idx"] % 3],
                    task_kind="knowledge",
                    simulates_promotion=sp["promote"],
                    air_gapped=True,
                ),
            }
        )

    return tasks


def generate_stress_set1(output_path: Path | None = None) -> list[dict[str, Any]]:
    """Build and write all 35 Stress Set 1 tasks to stress_long_context.jsonl."""
    target = output_path or (
        Path(__file__).resolve().parent / "suites" / "stress_long_context.jsonl"
    )
    tasks = get_stress_coding_part1() + get_stress_coding_part2() + get_stress_knowledge_tasks()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(json.dumps(t, separators=(",", ":"), ensure_ascii=False) for t in tasks) + "\n",
        encoding="utf-8",
    )
    return tasks


def main() -> int:
    """CLI entrypoint generating benchmarks/suites/stress_long_context.jsonl."""
    tasks = generate_stress_set1()
    coding_n = sum(1 for t in tasks if t["task_kind"] == "coding")
    knowledge_n = sum(1 for t in tasks if t["task_kind"] == "knowledge")
    print(f"Generated {len(tasks)} stress_long_context tasks ({coding_n} coding, {knowledge_n} knowledge).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
