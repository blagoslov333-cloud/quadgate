"""Abstract HarnessAdapter interface translating harness payloads to GateEvent/GateVerdict."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from quadgate.contracts import GateEvent, GateType, GateVerdict, HarnessType


class HarnessAdapter(ABC):
    """Translates harness-specific hook payloads & exit codes to canonical QuadGate envelopes."""

    @property
    @abstractmethod
    def harness(self) -> HarnessType:
        """Canonical harness identifier."""

    @abstractmethod
    def parse_event(self, gate: GateType, raw_payload: dict[str, Any]) -> GateEvent:
        """Normalize harness stdin/IPC JSON into a canonical GateEvent."""

    @abstractmethod
    def format_verdict(
        self,
        gate: GateType,
        verdict: GateVerdict,
    ) -> tuple[int, dict[str, Any] | str, str]:
        """Convert GateVerdict into `(exit_code, stdout_payload, stderr_text)`."""

    @abstractmethod
    def install_hooks(self, workspace_root: Path) -> Path:
        """Generate or safely merge native hook configuration in the target workspace."""
