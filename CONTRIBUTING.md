# Contributing to QuadGate

QuadGate separates its 4-gate control-plane algebra from classifier models and domain policies. You can add a new `ClassifierDriver` or author a declarative `DomainCartridge` in under 100 lines without modifying the core kernel (`quadgate/kernel/gates.py`).

---

## 1. Adding a `ClassifierDriver` (< 100 Lines)

Every classifier backend subclasses `ClassifierDriver` (`quadgate/drivers/base.py`), which enforces Pass-0 Tier-1 Privacy Air-Gap egress rules, candidate budgets, and conformal calibration (`CalibrationProfile`) inside `classify()`.

Create `quadgate/drivers/<name>.py` implementing `driver_id`, `capabilities`, `score_raw`, `health_check`, and `close`:

```python
"""Custom HTTP reranker ClassifierDriver example (< 100 lines)."""

from __future__ import annotations

import os

import aiohttp

from quadgate.contracts import ClassifierRequest
from quadgate.drivers.base import (
    CalibrationProfile,
    ClassifierDriver,
    DriverCapabilities,
)


class CustomRerankDriver(ClassifierDriver):
    """Minimal async HTTP classifier backend for QuadGate."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:9092/v1/score",
        api_key: str | None = None,
        calibration_profile: CalibrationProfile | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("CUSTOM_RERANK_KEY")
        self._session: aiohttp.ClientSession | None = None
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "custom_rerank"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=False,
            supports_parallel_heads=True,
            max_candidates_per_pass=64,
            scores_are_logits=True,
        )

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "query": request.query_text,
            "candidates": [
                {"head_id": head.head_id, "text": head.candidate_text}
                for head in request.heads
            ],
        }
        timeout = aiohttp.ClientTimeout(total=request.timeout_ms / 1000.0)
        session = self._get_session()
        async with session.post(
            self.endpoint, json=payload, headers=headers, timeout=timeout
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return {head.head_id: float(data["scores"][head.head_id]) for head in request.heads}

    async def health_check(self) -> bool:
        try:
            session = self._get_session()
            timeout = aiohttp.ClientTimeout(total=2.0)
            async with session.get(f"{self.endpoint}/health", timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
```

### Registration & Calibration Steps

1. **Export the driver** in `quadgate/drivers/__init__.py` (import `CustomRerankDriver` and add it to `__all__`).
2. **Wire CLI & daemon instantiation**:
   - Add a `"custom_rerank"` branch in `_build_calibration_driver()` inside `quadgate/cli.py`.
   - Add a matching `"custom_rerank"` branch in `build_driver_from_config()` inside `quadgate/kernel/daemon.py` so `quadgate.toml` (`[classifier] active_driver = "custom_rerank"`) selects it automatically.
3. **Fit conformal calibration** against the 64-pair golden benchmark and verify `ECE <= 0.05`:

```bash
quadgate calibrate --driver custom_rerank --dataset benchmarks/calibration_gold.jsonl --method temperature_scaling
```

This persists `~/.quadgate/calibration/custom_rerank.json`, which `QuadGateDaemon` attaches on startup.

---

## 2. Authoring a `DomainCartridge` (< 100 Lines)

Domain Cartridges live in `quadgate/cartridges/<name>.yaml` and validate strictly against `CartridgeSpec` (`quadgate/contracts.py`, `extra="forbid"`). Required top-level keys are `name`, `version`, `context_unit`, `mutation_tools`, `invariants`, and `truth_oracle`.

Save a file such as `quadgate/cartridges/support_ops.yaml`:

```yaml
name: support_ops
version: "1.0"
context_unit:
  indexer: markdown_sections
  include_globs: ["**/*.md", "**/*.txt", "**/*.json"]
  exclude_globs: ["**/.git/**", "**/node_modules/**"]
  private_tier1_globs: ["**/customer_pii/**", "**/tickets_private/**", "**/.env*"]

mutation_tools:
  - write_to_file
  - replace_file_content
  - multi_replace_file_content
  - Write
  - Edit

invariants:
  - id: INV_SUP_01_PII_REDACTION
    gist: "Never copy customer emails, tokens, or Tier-1 ticket records into public runbooks."
    remediation: "Redact customer identifiers and reference anonymized ticket IDs."
    scope_globs: ["runbooks/**/*.md", "docs/**/*.md"]
  - id: INV_SUP_02_SOURCE_CITATION
    gist: "Every troubleshooting resolution must cite an existing runbook note or verified URL."
    remediation: "Link each diagnostic step to an existing [[runbook]] note or canonical source."

truth_oracle:
  timeout_ms: 800
  commands_by_extension:
    ".py": ["python", "-m", "py_compile", "{file}"]
  checks:
    - "verify_wikilinks_exist"
    - "verify_citations_present"
```

Activate your cartridge via `quadgate init --cartridge support_ops` or by setting `[cartridge] active = "support_ops"` in `quadgate.toml`. Built-in Gate 4 `truth_oracle.checks` include `verify_wikilinks_exist`, `verify_citations_present`, `verify_double_entry_sum`, `verify_currency_precision`, `verify_required_clauses_present`, and `verify_cross_references`.

---

## 3. Local Verification Checklist

Before opening a pull request, verify all tests, static types, daemon diagnostics, and deterministic A/B dry-replay pass:

```bash
pip install -e ".[dev]"
pip install mypy
pytest -v
mypy --follow-imports=silent quadgate benchmarks
quadgate doctor
quadgate bench --dry-replay
```

All PRs run these checks across Linux (`ubuntu-latest`), macOS (`macos-latest`), and Windows (`windows-latest`) on Python `3.11` and `3.12` via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
