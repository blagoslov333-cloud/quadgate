"""Generic stdio MCP server bridge (`quadgate mcp`) and `.cursor/mcp.json` installer."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Final, TextIO

from pydantic import ValidationError

from quadgate.adapters.base import HarnessAdapter
from quadgate.contracts import (
    CartridgeSpec,
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    IndexedGist,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.index.ast_gists import AstGistIndexer
from quadgate.index.sqlite_fts import SqliteFtsStore
from quadgate.kernel.gates import GateEngine, load_cartridge
from quadgate.shim import check_stage2a_deterministic_security

MCP_PROTOCOL_VERSION: Final[str] = "2024-11-05"
MCP_SERVER_NAME: Final[str] = "quadgate-mcp"
MCP_SERVER_VERSION: Final[str] = "0.1.1"

_SUBAGENT_TOOL_NAMES: Final[frozenset[str]] = frozenset({"invoke_subagent", "Task"})

TOOLS: list[dict[str, Any]] = [
    {
        "name": "quadgate_context_sieve",
        "description": (
            "Evaluate Gate 1 (intake) context sieve, Tier-1 privacy air-gap, and Turn-1 "
            "tail-hint retrieval while preserving KV prefix caches on turns > 1."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_text": {
                    "type": "string",
                    "description": "User prompt or task text to evaluate at intake.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Workspace root directory path.",
                },
                "turn_index": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "1-based turn index (tail hints inject only on turn 1).",
                },
                "session_id": {
                    "type": "string",
                    "default": "mcp-session",
                    "description": "Session identifier.",
                },
                "candidates": {
                    "type": "array",
                    "description": "Optional explicit candidate file paths or gist dicts.",
                },
                "compact_reset": {
                    "type": "boolean",
                    "default": False,
                    "description": "True if conversation context was just compacted.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional additional GateEvent metadata.",
                },
            },
            "required": ["prompt_text"],
        },
    },
    {
        "name": "quadgate_verify_mutation",
        "description": (
            "Run Stage-2A deterministic security checks and Gate 2 pre-mutation firewall "
            "verification, plus optional Gate 3 subagent tier recommendation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the mutation, command, or subagent tool.",
                },
                "tool_args": {
                    "type": "object",
                    "description": "Tool argument payload.",
                },
                "diff_text": {
                    "type": "string",
                    "description": "Proposed diff or content mutation text.",
                },
                "touched_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths touched by the proposed operation.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Workspace root directory path.",
                },
                "prompt_text": {
                    "type": "string",
                    "description": "Active user or subagent prompt text.",
                },
                "assigned_file_cage": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Allowed file paths or directory globs for subagent edits.",
                },
                "bypass_gates": {
                    "type": "boolean",
                    "default": False,
                    "description": "Bypass Stage-2B semantic invariants (never bypasses Stage-2A).",
                },
                "subagent_model": {
                    "type": "string",
                    "description": "Requested subagent model; triggers Gate 3 tier evaluation.",
                },
                "current_tier": {
                    "type": "string",
                    "enum": ["floor", "mid", "frontier"],
                    "description": "Current execution tier; triggers Gate 3 tier evaluation.",
                },
                "session_id": {
                    "type": "string",
                    "default": "mcp-session",
                    "description": "Session identifier.",
                },
                "turn_index": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "1-based turn index.",
                },
                "strike_count": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Prior strike count for escalation tracking.",
                },
            },
        },
    },
    {
        "name": "quadgate_audit_completion",
        "description": (
            "Run Gate 4 completion audit combining sub-second domain truth oracles and "
            "directive completeness verification."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_text": {
                    "type": "string",
                    "description": "Original prompt whose clauses must be completed.",
                },
                "touched_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modified or generated workspace file paths.",
                },
                "diff_text": {
                    "type": "string",
                    "description": "Combined diff or output text.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Workspace root directory path.",
                },
                "current_tier": {
                    "type": "string",
                    "enum": ["floor", "mid", "frontier"],
                    "description": "Current model tier for 1-strike auto-promotion.",
                },
                "strike_count": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Prior Gate 4 failure strike count.",
                },
                "continuation_active": {
                    "type": "boolean",
                    "default": False,
                    "description": "True if a continuation pass was already requested.",
                },
                "session_id": {
                    "type": "string",
                    "default": "mcp-session",
                    "description": "Session identifier.",
                },
            },
        },
    },
]

_TOOL_NAMES: Final[frozenset[str]] = frozenset(tool["name"] for tool in TOOLS)


def _canonical_workspace_key(workspace_root: str | Path) -> str:
    """Return canonical case-normalized realpath string for engine caching."""
    raw = str(workspace_root or ".").strip() or "."
    return os.path.normcase(os.path.realpath(os.path.expanduser(raw)))


def _infer_tier_from_model(model_name: str | None) -> ModelTier | None:
    """Best-effort mapping from a model identifier string to a canonical ModelTier."""
    if not model_name or not isinstance(model_name, str):
        return None
    low = model_name.strip().lower()
    if not low:
        return None
    if low in {"floor", "flash_lite", "flash-lite"} or any(
        tok in low for tok in ("flash_lite", "flash-lite", "haiku", "mini", "nano", "lite")
    ):
        return ModelTier.FLOOR
    if low in {"mid", "flash"} or any(tok in low for tok in ("flash", "sonnet")):
        return ModelTier.MID
    if low in {"frontier", "pro"} or any(tok in low for tok in ("pro", "opus", "argon")):
        return ModelTier.FRONTIER
    return None


def _coerce_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Field {field_name!r} must be a string.")
    return value


def _coerce_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Field {field_name!r} must be an integer.")
    return value


def _coerce_bool(value: Any, field_name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"Field {field_name!r} must be a boolean.")
    return value


def _coerce_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"Field {field_name!r} must be a list of strings.")
    items: list[str] = []
    for elem in value:
        if not isinstance(elem, str):
            raise ValueError(f"Elements of {field_name!r} must be strings.")
        items.append(elem)
    return items


def _atomic_write_text(path: Path, content: str) -> None:
    """Write UTF-8 text atomically using a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class McpBridgeAdapter(HarnessAdapter):
    """MCP bridge exposing `quadgate_context_sieve`, `quadgate_verify_mutation`, and audit tools."""

    TOOLS: list[dict[str, Any]] = TOOLS

    def __init__(
        self,
        *,
        cartridge: str | CartridgeSpec = "software_engineering",
        gate_engine: GateEngine | None = None,
    ) -> None:
        self._default_gate_engine: GateEngine | None = gate_engine
        if gate_engine is not None:
            self._cartridge_input: str | Path = gate_engine.cartridge.name
            self._cartridge_spec: CartridgeSpec | None = gate_engine.cartridge
            self._cartridge_name: str = gate_engine.cartridge.name
        elif isinstance(cartridge, CartridgeSpec):
            self._cartridge_input = cartridge.name
            self._cartridge_spec = cartridge
            self._cartridge_name = cartridge.name
        else:
            self._cartridge_input = cartridge
            self._cartridge_spec = None
            self._cartridge_name = str(cartridge)

        self._engine_cache: dict[tuple[str, str], GateEngine] = {}
        self._pass0_cache: dict[str, tuple[AstGistIndexer, SqliteFtsStore]] = {}
        self._fallback_strikes: dict[tuple[str, str], int] = {}

    @property
    def harness(self) -> HarnessType:
        return HarnessType.MCP

    def _get_cartridge(self) -> CartridgeSpec:
        if self._cartridge_spec is None:
            self._cartridge_spec = load_cartridge(self._cartridge_input)
            self._cartridge_name = self._cartridge_spec.name
        return self._cartridge_spec

    def _get_engine(self, workspace_root: str) -> GateEngine:
        if self._default_gate_engine is not None:
            return self._default_gate_engine

        ws_key = _canonical_workspace_key(workspace_root)
        cartridge = self._get_cartridge()
        cache_key = (ws_key, cartridge.name)
        engine = self._engine_cache.get(cache_key)
        if engine is not None:
            return engine

        ws_path = Path(os.path.realpath(os.path.expanduser(workspace_root or ".")))
        indexer = AstGistIndexer(ws_path, cartridge=cartridge)
        fts_store = SqliteFtsStore(":memory:")
        fts_store.initialize()
        engine = GateEngine(
            cartridge=cartridge,
            fts_store=fts_store,
            indexer=indexer,
        )
        self._engine_cache[cache_key] = engine
        return engine

    def _get_pass0_pair(
        self,
        workspace_root: str,
        cartridge: CartridgeSpec,
    ) -> tuple[AstGistIndexer, SqliteFtsStore]:
        ws_key = _canonical_workspace_key(workspace_root)
        cached = self._pass0_cache.get(ws_key)
        if cached is not None:
            return cached
        ws_path = Path(os.path.realpath(os.path.expanduser(workspace_root or ".")))
        indexer = AstGistIndexer(ws_path, cartridge=cartridge)
        fts_store = SqliteFtsStore(":memory:")
        fts_store.initialize()
        self._pass0_cache[ws_key] = (indexer, fts_store)
        return indexer, fts_store

    def close(self) -> None:
        """Close any cached in-memory SQLite FTS stores."""
        for engine in self._engine_cache.values():
            if engine.fts_store is not None:
                engine.fts_store.close()
        self._engine_cache.clear()
        for _, fts_store in self._pass0_cache.values():
            fts_store.close()
        self._pass0_cache.clear()

    def parse_event(self, gate: GateType, raw_payload: dict[str, Any]) -> GateEvent:
        """Translate MCP tool arguments or call envelopes into a canonical GateEvent."""
        if not isinstance(raw_payload, dict):
            raise ValueError("MCP payload must be a JSON object.")

        canonical_gate = GateType(gate)
        payload = raw_payload
        if isinstance(payload.get("params"), dict) and "arguments" in payload["params"]:
            payload = payload["params"]

        if (
            isinstance(payload.get("arguments"), dict)
            and isinstance(payload.get("name"), str)
            and payload["name"] in _TOOL_NAMES
        ):
            merged: dict[str, Any] = dict(payload["arguments"])
            for k, v in payload.items():
                if k not in {"name", "arguments", "params"} and k not in merged:
                    merged[k] = v
            payload = merged

        session_raw = _coerce_optional_str(payload.get("session_id"), "session_id")
        session_id = (session_raw or "").strip() or "mcp-session"

        turn_index = _coerce_int(payload.get("turn_index"), "turn_index", 1)
        strike_count = _coerce_int(payload.get("strike_count"), "strike_count", 0)

        ws_raw = _coerce_optional_str(payload.get("workspace_root"), "workspace_root")
        workspace_root = (ws_raw or "").strip() or "."

        tool_name = _coerce_optional_str(payload.get("tool_name"), "tool_name")

        raw_tool_args = payload.get("tool_args")
        if raw_tool_args is None:
            for alt_key in ("tool_input", "arguments"):
                if isinstance(payload.get(alt_key), dict):
                    raw_tool_args = payload[alt_key]
                    break
        if raw_tool_args is None:
            tool_args: dict[str, Any] = {}
        elif isinstance(raw_tool_args, dict):
            tool_args = dict(raw_tool_args)
        else:
            raise ValueError("Field 'tool_args' must be a dict.")

        prompt_text = _coerce_optional_str(payload.get("prompt_text"), "prompt_text")
        diff_text = _coerce_optional_str(payload.get("diff_text"), "diff_text")

        touched_files = _coerce_str_list(payload.get("touched_files"), "touched_files")

        raw_cage = payload.get("assigned_file_cage")
        if raw_cage is None:
            assigned_file_cage: list[str] | None = None
        elif isinstance(raw_cage, str):
            assigned_file_cage = [raw_cage]
        else:
            assigned_file_cage = _coerce_str_list(raw_cage, "assigned_file_cage")

        raw_tier = payload.get("current_tier")
        subagent_model = _coerce_optional_str(payload.get("subagent_model"), "subagent_model")
        if raw_tier is not None:
            if isinstance(raw_tier, ModelTier):
                current_tier: ModelTier | None = raw_tier
            elif isinstance(raw_tier, str):
                current_tier = ModelTier(raw_tier.strip().lower())
            else:
                raise ValueError("Field 'current_tier' must be a valid tier string.")
        else:
            current_tier = _infer_tier_from_model(subagent_model)

        continuation_active = _coerce_bool(
            payload.get("continuation_active"),
            "continuation_active",
            False,
        )
        bypass_gates = _coerce_bool(payload.get("bypass_gates"), "bypass_gates", False)

        raw_meta = payload.get("metadata")
        if raw_meta is None:
            metadata: dict[str, Any] = {}
        elif isinstance(raw_meta, dict):
            metadata = dict(raw_meta)
        else:
            raise ValueError("Field 'metadata' must be a dict.")

        if "candidates" in payload and payload["candidates"] is not None:
            raw_candidates = payload["candidates"]
            if not isinstance(raw_candidates, (list, tuple)):
                raise ValueError("Field 'candidates' must be a list.")
            metadata["candidates"] = list(raw_candidates)
        if "compact_reset" in payload and payload["compact_reset"] is not None:
            metadata["compact_reset"] = _coerce_bool(
                payload["compact_reset"],
                "compact_reset",
                False,
            )
        if subagent_model is not None:
            metadata.setdefault("subagent_model", subagent_model)
        for extra_meta_key in ("required_clauses", "blast_radius", "r_hist"):
            if extra_meta_key in payload and payload[extra_meta_key] is not None:
                metadata.setdefault(extra_meta_key, payload[extra_meta_key])

        return GateEvent(
            gate=canonical_gate,
            session_id=session_id,
            turn_index=turn_index,
            workspace_root=workspace_root,
            harness=HarnessType.MCP,
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
        """Serialize GateVerdict into `(exit_code, payload_dict, stderr_text)`."""
        canonical_gate = GateType(gate)
        payload_dict = verdict.model_dump(mode="json")
        payload_dict["gate"] = (verdict.gate or canonical_gate).value

        if verdict.action in {VerdictAction.BLOCK, VerdictAction.PROMOTE}:
            exit_code = 2
            if verdict.violation_receipt is not None:
                vr = verdict.violation_receipt
                stderr_text = f"[{vr.rule_id}] {vr.remediation} ({vr.bypass_instruction})"
            elif verdict.action == VerdictAction.PROMOTE:
                stderr_text = (
                    verdict.reason
                    or (
                        f"Gate {canonical_gate.value} requires promotion to "
                        f"tier={verdict.target_tier} model={verdict.updated_model}."
                    )
                )
            else:
                stderr_text = verdict.reason or f"Blocked by QuadGate {canonical_gate.value}."
            return exit_code, payload_dict, stderr_text

        return 0, payload_dict, ""

    def install_hooks(self, workspace_root: Path) -> Path:
        """Create or merge `<workspace_root>/.cursor/mcp.json` idempotently."""
        ws_path = Path(workspace_root)
        mcp_path = ws_path / ".cursor" / "mcp.json"

        data: dict[str, Any] = {}
        if mcp_path.exists():
            raw = mcp_path.read_text(encoding="utf-8")
            if raw.strip():
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError(f"{mcp_path} must contain a JSON object.")
                data = loaded

        servers_raw = data.get("mcpServers")
        if servers_raw is None:
            servers: dict[str, Any] = {}
        elif isinstance(servers_raw, dict):
            servers = dict(servers_raw)
        else:
            raise ValueError(f"{mcp_path} field 'mcpServers' must be a JSON object.")

        target_entry = {"command": "quadgate", "args": ["mcp"]}
        if servers.get("quadgate") == target_entry and "mcpServers" in data:
            return mcp_path

        servers["quadgate"] = target_entry
        data["mcpServers"] = servers
        serialized = json.dumps(data, indent=2) + "\n"
        _atomic_write_text(mcp_path, serialized)
        return mcp_path

    def _record_stage2a_strike(
        self,
        engine: GateEngine,
        session_id: str,
        rule_id: str,
        event_strike: int,
    ) -> int:
        recorder = getattr(engine, "_record_gate2_strike", None)
        if callable(recorder):
            return int(recorder(session_id, rule_id, event_strike))
        key = (session_id, rule_id)
        prev = max(self._fallback_strikes.get(key, 0), event_strike)
        new_strike = prev + 1
        self._fallback_strikes[key] = new_strike
        return new_strike

    @staticmethod
    def _serialize_mutation_verdict(
        verdict: GateVerdict,
        elapsed_ms: float,
        *,
        reason_override: str | None = None,
    ) -> dict[str, Any]:
        allowed = verdict.action in {
            VerdictAction.ALLOW,
            VerdictAction.AUGMENT,
            VerdictAction.MODIFY,
        }
        return {
            "action": verdict.action.value,
            "allowed": allowed,
            "violation_receipt": (
                verdict.violation_receipt.model_dump(mode="json")
                if verdict.violation_receipt is not None
                else None
            ),
            "updated_model": verdict.updated_model,
            "target_tier": (
                verdict.target_tier.value if verdict.target_tier is not None else None
            ),
            "updated_tool_args": verdict.updated_tool_args,
            "reason": reason_override if reason_override is not None else verdict.reason,
            "latency_ms": elapsed_ms,
        }

    async def _run_context_sieve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        event = self.parse_event(GateType.INTAKE, arguments)
        engine = self._get_engine(event.workspace_root)

        is_intake_turn = event.turn_index == 1 or event.metadata.get("compact_reset") is True
        if is_intake_turn:
            ws_path = Path(event.workspace_root)
            if engine.indexer is not None and ws_path.is_dir():
                indexer_key = _canonical_workspace_key(engine.indexer.workspace_root)
                event_key = _canonical_workspace_key(event.workspace_root)
                if indexer_key == event_key:
                    gists = engine.indexer.index_workspace(incremental=True)
                    if engine.fts_store is not None and gists:
                        engine.fts_store.upsert_gists(gists)
            elif (
                engine.indexer is None
                and engine.fts_store is None
                and not event.metadata.get("candidates")
                and ws_path.is_dir()
            ):
                indexer, fts_store = self._get_pass0_pair(event.workspace_root, engine.cartridge)
                gists = indexer.index_workspace(incremental=True)
                if gists:
                    fts_store.upsert_gists(gists)
                query = (event.prompt_text or "").strip()
                selected_gists: list[IndexedGist] = []
                if query:
                    res = fts_store.search_candidates(query, indexer.workspace_hash, limit=64)
                    selected_gists = res.gists
                if not selected_gists:
                    selected_gists = gists
                if selected_gists:
                    new_meta = dict(event.metadata)
                    new_meta["candidates"] = selected_gists
                    event = event.model_copy(update={"metadata": new_meta})

        verdict = await engine.evaluate(event)
        return {
            "action": verdict.action.value,
            "tail_hints": list(verdict.tail_hints),
            "reason": verdict.reason,
            "cloud_egress_allowed": verdict.cloud_egress_allowed,
            "latency_ms": verdict.latency_ms,
        }

    async def _run_verify_mutation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        event = self.parse_event(GateType.FIREWALL, arguments)
        engine = self._get_engine(event.workspace_root)

        # Stage-2A deterministic security ALWAYS runs first and never fails open or bypasses.
        stage2a_payload: dict[str, object] = {
            "tool_name": event.tool_name,
            "tool_args": dict(event.tool_args),
            "touched_files": list(event.touched_files),
        }
        if event.diff_text:
            stage2a_payload["diff_text"] = event.diff_text

        receipt_dict = check_stage2a_deterministic_security(stage2a_payload, event.workspace_root)
        if receipt_dict is not None:
            rule_id = str(receipt_dict.get("rule_id") or "STAGE2A_SECURITY_BLOCK")
            strike = self._record_stage2a_strike(
                engine, event.session_id, rule_id, event.strike_count
            )
            escalate = strike >= 2
            remediation = str(receipt_dict.get("remediation") or "Blocked by Stage-2A security.")
            if escalate:
                remediation = (
                    f"HUMAN CONFIRMATION REQUIRED (strike {strike} on {rule_id}): "
                    f"{remediation} Confirm human override before proceeding."
                )
            raw_score = receipt_dict.get("score", 1.0)
            score = float(raw_score) if isinstance(raw_score, (int, float)) else 1.0
            raw_vpath = receipt_dict.get("violated_path")
            receipt = ViolationReceipt(
                rule_id=rule_id,
                score=score,
                stage="deterministic_2a",
                remediation=remediation,
                strike_count=strike,
                escalate_to_human=escalate,
                violated_path=str(raw_vpath) if raw_vpath is not None else None,
            )
            verdict = GateVerdict(
                action=VerdictAction.BLOCK,
                gate=GateType.FIREWALL,
                violation_receipt=receipt,
                reason=remediation,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return self._serialize_mutation_verdict(verdict, elapsed_ms)

        gate2_verdict = await engine.evaluate(event)
        if gate2_verdict.action == VerdictAction.BLOCK:
            elapsed_ms = max(gate2_verdict.latency_ms, (time.perf_counter() - t0) * 1000.0)
            return self._serialize_mutation_verdict(gate2_verdict, elapsed_ms)

        has_subagent_model = bool(
            isinstance(arguments.get("subagent_model"), str)
            and arguments["subagent_model"].strip()
        )
        has_current_tier = arguments.get("current_tier") is not None
        should_eval_gate3 = (
            has_subagent_model
            or has_current_tier
            or (event.tool_name in _SUBAGENT_TOOL_NAMES)
        )

        if should_eval_gate3:
            gate3_event = event.model_copy(update={"gate": GateType.SUBAGENT_GOVERNOR})
            gate3_verdict = await engine.evaluate(gate3_event)
            elapsed_ms = max(
                gate2_verdict.latency_ms + gate3_verdict.latency_ms,
                (time.perf_counter() - t0) * 1000.0,
            )
            if gate2_verdict.reason and gate3_verdict.reason:
                combined_reason: str | None = f"{gate2_verdict.reason} | {gate3_verdict.reason}"
            else:
                combined_reason = gate3_verdict.reason or gate2_verdict.reason
            return self._serialize_mutation_verdict(
                gate3_verdict,
                elapsed_ms,
                reason_override=combined_reason,
            )

        elapsed_ms = max(gate2_verdict.latency_ms, (time.perf_counter() - t0) * 1000.0)
        return self._serialize_mutation_verdict(gate2_verdict, elapsed_ms)

    async def _run_audit_completion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        event = self.parse_event(GateType.STOP_AUDIT, arguments)
        engine = self._get_engine(event.workspace_root)
        verdict = await engine.evaluate(event)
        return {
            "action": verdict.action.value,
            "passed": verdict.action == VerdictAction.ALLOW,
            "unaddressed_clauses": list(verdict.unaddressed_clauses),
            "updated_model": verdict.updated_model,
            "target_tier": (
                verdict.target_tier.value if verdict.target_tier is not None else None
            ),
            "violation_receipt": (
                verdict.violation_receipt.model_dump(mode="json")
                if verdict.violation_receipt is not None
                else None
            ),
            "reason": verdict.reason,
            "latency_ms": verdict.latency_ms,
        }

    @staticmethod
    def _rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    async def handle_rpc_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 MCP request dict."""
        if not isinstance(request, dict):
            return self._rpc_error(None, -32600, "Invalid Request: expected JSON object.")

        has_id = "id" in request
        req_id = request.get("id")

        if "jsonrpc" in request and request["jsonrpc"] != "2.0":
            return self._rpc_error(
                req_id if has_id else None,
                -32600,
                "Invalid Request: 'jsonrpc' must be '2.0'.",
            )

        method = request.get("method")
        if not isinstance(method, str) or not method:
            if not has_id:
                return None
            return self._rpc_error(req_id, -32600, "Invalid Request: 'method' must be a string.")

        if method == "notifications/initialized" or not has_id:
            return None

        if method == "initialize":
            return self._rpc_result(
                req_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": MCP_SERVER_VERSION,
                    },
                },
            )

        if method == "ping":
            return self._rpc_result(req_id, {})

        if method == "tools/list":
            return self._rpc_result(req_id, {"tools": copy.deepcopy(self.TOOLS)})

        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict):
                return self._rpc_error(
                    req_id,
                    -32602,
                    "Invalid params: 'params' must be an object.",
                )

            tool_name = params.get("name")
            if not isinstance(tool_name, str) or tool_name not in _TOOL_NAMES:
                return self._rpc_error(
                    req_id,
                    -32602,
                    f"Invalid params: unknown tool {tool_name!r}.",
                )

            raw_args = params.get("arguments")
            if raw_args is None:
                arguments: dict[str, Any] = {}
            elif isinstance(raw_args, dict):
                arguments = raw_args
            else:
                return self._rpc_error(
                    req_id,
                    -32602,
                    "Invalid params: 'arguments' must be a JSON object.",
                )

            try:
                if tool_name == "quadgate_context_sieve":
                    result = await self._run_context_sieve(arguments)
                elif tool_name == "quadgate_verify_mutation":
                    result = await self._run_verify_mutation(arguments)
                else:
                    result = await self._run_audit_completion(arguments)
            except (ValueError, TypeError, ValidationError) as exc:
                return self._rpc_error(req_id, -32602, f"Invalid params: {exc}")
            except Exception as exc:
                return self._rpc_result(
                    req_id,
                    {
                        "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                        "isError": True,
                    },
                )

            return self._rpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "isError": False,
                },
            )

        return self._rpc_error(req_id, -32601, f"Method not found: {method}")

    async def serve_stdio(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> int:
        """Run newline-delimited JSON-RPC 2.0 MCP loop over stdio until EOF."""
        in_stream = sys.stdin if stdin is None else stdin
        out_stream = sys.stdout if stdout is None else stdout
        is_memory_stream = isinstance(in_stream, io.StringIO)

        while True:
            if is_memory_stream:
                line = in_stream.readline()
            else:
                line = await asyncio.to_thread(in_stream.readline)

            if line == "":
                break

            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                err_resp = self._rpc_error(None, -32700, f"Parse error: {exc}")
                out_stream.write(json.dumps(err_resp, separators=(",", ":")) + "\n")
                out_stream.flush()
                continue

            response = await self.handle_rpc_request(parsed)
            if response is not None:
                out_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
                out_stream.flush()

        return 0

    def run_stdio(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> int:
        """Synchronous convenience wrapper running `serve_stdio`."""
        return asyncio.run(self.serve_stdio(stdin=stdin, stdout=stdout))


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_NAME",
    "MCP_SERVER_VERSION",
    "McpBridgeAdapter",
    "TOOLS",
]
