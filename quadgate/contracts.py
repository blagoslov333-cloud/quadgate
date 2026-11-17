"""Canonical harness-neutral and model-agnostic QuadGate data contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class GateType(StrEnum):
    """Canonical lifecycle gates evaluated by the QuadGate kernel."""

    INTAKE = "intake"
    FIREWALL = "firewall"
    SUBAGENT_GOVERNOR = "subagent_governor"
    STOP_AUDIT = "stop_audit"


class HarnessType(StrEnum):
    """Supported agent harness integrations."""

    ANTIGRAVITY = "antigravity"
    CLAUDE_CODE = "claude_code"
    CODEX_CLI = "codex_cli"
    MCP = "mcp"
    GENERIC = "generic"


class VerdictAction(StrEnum):
    """Canonical verdict actions returned by QuadGate gates."""

    ALLOW = "allow"
    BLOCK = "block"
    AUGMENT = "augment"
    MODIFY = "modify"
    PROMOTE = "promote"


class ModelTier(StrEnum):
    """Model cost/capability tiers within the active provider family."""

    FLOOR = "floor"
    MID = "mid"
    FRONTIER = "frontier"


class HeadCategory(StrEnum):
    """Logical classifier slot groups across the 1,000-head budget."""

    INVARIANT = "invariant"
    PRAGMATIC = "pragmatic"
    CANDIDATE = "candidate"


class ViolationReceipt(BaseModel):
    """Actionable receipt returned when Gate 2 blocks a mutation or command."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    stage: Literal["deterministic_2a", "semantic_2b"] = "deterministic_2a"
    remediation: str = Field(min_length=1)
    bypass_instruction: str = "Include [bypass-gates] in prompt or confirm human override."
    strike_count: int = Field(default=1, ge=1)
    escalate_to_human: bool = False
    violated_path: str | None = None


class GateEvent(BaseModel):
    """Canonical harness-neutral input envelope sent to the QuadGate daemon."""

    model_config = ConfigDict(extra="forbid")

    gate: GateType = GateType.INTAKE
    session_id: str = Field(min_length=1)
    turn_index: int = Field(default=1, ge=1)
    workspace_root: str = Field(min_length=1)
    harness: HarnessType = HarnessType.GENERIC
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    prompt_text: str | None = None
    assigned_file_cage: list[str] | None = None
    current_tier: ModelTier | None = None
    strike_count: int = Field(default=0, ge=0)
    touched_files: list[str] = Field(default_factory=list)
    diff_text: str | None = None
    continuation_active: bool = False
    bypass_gates: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("harness", mode="before")
    @classmethod
    def _normalize_harness(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if normalized == "codex":
                return HarnessType.CODEX_CLI.value
            return normalized
        return value


class GateVerdict(BaseModel):
    """Canonical decision envelope emitted by a QuadGate gate evaluation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: VerdictAction = Field(validation_alias=AliasChoices("action", "verdict"))
    gate: GateType | None = None
    tail_hints: list[str] = Field(default_factory=list)
    updated_tool_args: dict[str, Any] | None = None
    updated_model: str | None = None
    target_tier: ModelTier | None = None
    violation_receipt: ViolationReceipt | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    reason: str | None = None
    cloud_egress_allowed: bool = True
    unaddressed_clauses: list[str] = Field(default_factory=list)

    @property
    def verdict(self) -> VerdictAction:
        """Return the verdict action alias for downstream consumers."""
        return self.action

    @model_validator(mode="after")
    def _validate_action_requirements(self) -> GateVerdict:
        if self.action == VerdictAction.BLOCK and self.violation_receipt is None:
            raise ValueError("GateVerdict with action='block' requires a violation_receipt.")
        if (
            self.action == VerdictAction.PROMOTE
            and self.updated_model is None
            and self.target_tier is None
        ):
            raise ValueError(
                "GateVerdict with action='promote' requires updated_model or target_tier."
            )
        return self


class ClassifierHead(BaseModel):
    """Single discriminative head candidate scored against a task/query."""

    model_config = ConfigDict(extra="forbid")

    head_id: str = Field(min_length=1)
    candidate_text: str = Field(min_length=1)
    category: HeadCategory = HeadCategory.CANDIDATE
    slot_index: int | None = Field(default=None, ge=1, le=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClassifierRequest(BaseModel):
    """Model-agnostic batch scoring request passed to a ClassifierDriver."""

    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1)
    heads: list[ClassifierHead] = Field(min_length=1)
    cloud_egress_allowed: bool = True
    max_candidates: int | None = Field(default=None, ge=1, le=1000)
    timeout_ms: int = Field(default=350, ge=1, le=10000)

    @field_validator("heads")
    @classmethod
    def _validate_unique_head_ids(cls, heads: list[ClassifierHead]) -> list[ClassifierHead]:
        seen: set[str] = set()
        for head in heads:
            if head.head_id in seen:
                raise ValueError(f"Duplicate head_id in ClassifierRequest: {head.head_id!r}")
            seen.add(head.head_id)
        return heads


class ClassifierResponse(BaseModel):
    """Calibrated and raw scores returned by a ClassifierDriver."""

    model_config = ConfigDict(extra="forbid")

    driver_id: str = Field(min_length=1)
    head_ids: list[str] = Field(default_factory=list)
    raw_scores: dict[str, float]
    calibrated_probabilities: dict[str, float]
    driver_latency_ms: float = Field(ge=0.0)
    is_calibrated: bool = False
    tokens_used: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_score_alignment(self) -> ClassifierResponse:
        if not self.head_ids and self.calibrated_probabilities:
            self.head_ids = list(self.calibrated_probabilities.keys())
        if len(set(self.head_ids)) != len(self.head_ids):
            raise ValueError("head_ids must not contain duplicates.")
        expected = set(self.head_ids)
        if set(self.raw_scores.keys()) != expected:
            raise ValueError("raw_scores keys must match head_ids exactly.")
        if set(self.calibrated_probabilities.keys()) != expected:
            raise ValueError("calibrated_probabilities keys must match head_ids exactly.")
        for head_id, prob in self.calibrated_probabilities.items():
            if not 0.0 <= prob <= 1.0:
                raise ValueError(
                    f"calibrated_probabilities[{head_id!r}]={prob} must lie in [0.0, 1.0]."
                )
        return self


class IndexedGist(BaseModel):
    """Pass-0 AST/document gist record stored in SQLite FTS5."""

    model_config = ConfigDict(extra="forbid")

    workspace_hash: str = Field(min_length=1)
    rel_path: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    language: str = Field(min_length=1)
    semantic_gist: str
    symbols: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    fan_in_count: int = Field(default=0, ge=0)
    mtime_ns: int = Field(default=0, ge=0)
    is_private_tier1: bool = False


class ContextUnitSpec(BaseModel):
    """Indexing and privacy air-gap rules for a declarative Domain Cartridge."""

    model_config = ConfigDict(extra="forbid")

    indexer: str = Field(min_length=1)
    include_globs: list[str] = Field(min_length=1)
    exclude_globs: list[str] = Field(default_factory=list)
    private_tier1_globs: list[str] = Field(default_factory=list)


class InvariantSpec(BaseModel):
    """Single declarative safety or policy invariant head in a Domain Cartridge."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    gist: str = Field(min_length=1)
    remediation: str = Field(min_length=1)
    scope_globs: list[str] = Field(default_factory=list)


class TruthOracleSpec(BaseModel):
    """Sub-second verification specification executed by Gate 4."""

    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = Field(default=800, ge=50, le=10000)
    commands_by_extension: dict[str, list[str]] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)


class CartridgeSpec(BaseModel):
    """Complete declarative Domain Cartridge schema."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = "1.0"
    context_unit: ContextUnitSpec
    mutation_tools: list[str]
    invariants: list[InvariantSpec]
    truth_oracle: TruthOracleSpec


__all__ = [
    "CartridgeSpec",
    "ClassifierHead",
    "ClassifierRequest",
    "ClassifierResponse",
    "ContextUnitSpec",
    "GateEvent",
    "GateType",
    "GateVerdict",
    "HarnessType",
    "HeadCategory",
    "IndexedGist",
    "InvariantSpec",
    "ModelTier",
    "TruthOracleSpec",
    "VerdictAction",
    "ViolationReceipt",
]
