"""Wave 0 contract lock unit tests for QuadGate."""

from __future__ import annotations

import ast
import importlib
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from quadgate.contracts import (
    CartridgeSpec,
    ClassifierHead,
    ClassifierRequest,
    ClassifierResponse,
    GateEvent,
    GateType,
    GateVerdict,
    HarnessType,
    HeadCategory,
    ModelTier,
    VerdictAction,
    ViolationReceipt,
)
from quadgate.drivers.base import (
    CalibrationMethod,
    CalibrationProfile,
    ClassifierDriver,
    DriverCapabilities,
)
from quadgate.kernel.calibration import CalibrationSample
from quadgate.kernel.daemon import DaemonConfig
from quadgate.kernel.gates import ThresholdConfig, TierModelConfig
from quadgate.shim import parse_shim_args

REPO_ROOT = Path(__file__).resolve().parent.parent


class _DummyDriver(ClassifierDriver):
    """Test-only concrete ClassifierDriver subclass verifying abstract contract."""

    def __init__(
        self,
        *,
        is_cloud: bool = True,
        max_candidates: int = 8,
        scores_are_logits: bool = True,
        calibration_profile: CalibrationProfile | None = None,
    ) -> None:
        self._is_cloud = is_cloud
        self._max_candidates = max_candidates
        self._scores_are_logits = scores_are_logits
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "dummy_test"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=self._is_cloud,
            supports_parallel_heads=True,
            max_candidates_per_pass=self._max_candidates,
            scores_are_logits=self._scores_are_logits,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        return {head.head_id: float(idx) for idx, head in enumerate(request.heads)}

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_enums_str_values() -> None:
    assert [g.value for g in GateType] == [
        "intake",
        "firewall",
        "subagent_governor",
        "stop_audit",
    ]
    assert [h.value for h in HarnessType] == [
        "antigravity",
        "claude_code",
        "codex_cli",
        "mcp",
        "generic",
    ]
    assert [a.value for a in VerdictAction] == [
        "allow",
        "block",
        "augment",
        "modify",
        "promote",
    ]
    assert [t.value for t in ModelTier] == ["floor", "mid", "frontier"]
    assert [c.value for c in HeadCategory] == ["invariant", "pragmatic", "candidate"]
    assert isinstance(GateType.INTAKE, str)


def test_gate_event_validation_and_roundtrip() -> None:
    ev = GateEvent(
        gate=GateType.FIREWALL,
        session_id="sess-01",
        turn_index=2,
        workspace_root="/repo",
        harness="claude-code",
        tool_name="Write",
        tool_args={"path": "app.py"},
        prompt_text="Update auth check",
    )
    assert ev.harness == HarnessType.CLAUDE_CODE

    ev_codex = GateEvent(
        session_id="sess-02",
        workspace_root="/repo",
        harness="codex",
    )
    assert ev_codex.harness == HarnessType.CODEX_CLI

    dumped = ev.model_dump_json()
    reloaded = GateEvent.model_validate_json(dumped)
    assert reloaded == ev

    with pytest.raises(ValidationError):
        GateEvent(session_id="sess-01", turn_index=0, workspace_root="/repo")

    with pytest.raises(ValidationError):
        GateEvent(session_id="sess-01", workspace_root="/repo", unknown_field=True)


def test_gate_verdict_actions_and_invariants() -> None:
    v_allow = GateVerdict(action=VerdictAction.ALLOW, gate=GateType.INTAKE, latency_ms=1.2)
    assert v_allow.verdict == VerdictAction.ALLOW

    v_augment = GateVerdict(
        verdict=VerdictAction.AUGMENT,
        gate=GateType.INTAKE,
        tail_hints=["src/auth/jwt_validator.py"],
    )
    assert v_augment.action == VerdictAction.AUGMENT

    v_modify = GateVerdict(
        action=VerdictAction.MODIFY,
        gate=GateType.SUBAGENT_GOVERNOR,
        updated_model="flash_lite",
        target_tier=ModelTier.FLOOR,
        updated_tool_args={"Model": "flash_lite"},
    )
    assert v_modify.target_tier == ModelTier.FLOOR

    receipt = ViolationReceipt(
        rule_id="INV_01_SECRET_EXPOSURE",
        score=0.95,
        stage="deterministic_2a",
        remediation="Use environment variables.",
        violated_path=".env",
    )
    v_block = GateVerdict(
        action=VerdictAction.BLOCK,
        gate=GateType.FIREWALL,
        violation_receipt=receipt,
    )
    assert GateVerdict.model_validate_json(v_block.model_dump_json()) == v_block

    v_promote = GateVerdict(
        action=VerdictAction.PROMOTE,
        gate=GateType.STOP_AUDIT,
        updated_model="pro",
        target_tier=ModelTier.FRONTIER,
    )
    assert v_promote.action == VerdictAction.PROMOTE

    with pytest.raises(ValidationError):
        GateVerdict(action=VerdictAction.BLOCK, gate=GateType.FIREWALL)

    with pytest.raises(ValidationError):
        GateVerdict(action=VerdictAction.PROMOTE, gate=GateType.STOP_AUDIT)


def test_classifier_request_and_response_contracts() -> None:
    heads = [
        ClassifierHead(head_id="h1", candidate_text="auth.py", category=HeadCategory.CANDIDATE),
        ClassifierHead(head_id="h2", candidate_text="INV_01", category=HeadCategory.INVARIANT),
    ]
    req = ClassifierRequest(query_text="fix auth", heads=heads)
    assert len(req.heads) == 2

    with pytest.raises(ValidationError):
        ClassifierRequest(query_text="fix auth", heads=[heads[0], heads[0]])

    resp = ClassifierResponse(
        driver_id="onnx_local",
        raw_scores={"h1": 2.0, "h2": -1.0},
        calibrated_probabilities={"h1": 0.88, "h2": 0.27},
        driver_latency_ms=4.5,
        is_calibrated=True,
    )
    assert resp.head_ids == ["h1", "h2"]

    with pytest.raises(ValidationError):
        ClassifierResponse(
            driver_id="onnx_local",
            head_ids=["h1", "h2"],
            raw_scores={"h1": 1.0},
            calibrated_probabilities={"h1": 0.5, "h2": 0.5},
            driver_latency_ms=1.0,
        )

    with pytest.raises(ValidationError):
        ClassifierResponse(
            driver_id="onnx_local",
            head_ids=["h1"],
            raw_scores={"h1": 1.0},
            calibrated_probabilities={"h1": 1.5},
            driver_latency_ms=1.0,
        )


def test_calibration_profile_transforms_and_validation() -> None:
    temp_profile = CalibrationProfile(
        driver_id="dummy_test",
        method=CalibrationMethod.TEMPERATURE_SCALING,
        temperature=2.0,
        ece=0.03,
        sample_count=64,
    )
    assert temp_profile.passes_ece_gate
    assert temp_profile.apply(0.0, scores_are_logits=True) == pytest.approx(0.5)
    assert temp_profile.apply(2.0, scores_are_logits=True) == pytest.approx(
        1.0 / (1.0 + math.exp(-1.0))
    )

    platt_profile = CalibrationProfile(
        driver_id="dummy_test",
        method=CalibrationMethod.PLATT,
        platt_scale=2.0,
        platt_bias=0.0,
    )
    assert platt_profile.apply(0.0) == pytest.approx(0.5)
    assert platt_profile.apply(1.0) > 0.85

    iso_profile = CalibrationProfile(
        driver_id="dummy_test",
        method=CalibrationMethod.ISOTONIC,
        isotonic_x_knots=[0.0, 0.5, 1.0],
        isotonic_y_knots=[0.1, 0.5, 0.9],
        ece=0.04,
        sample_count=64,
    )
    assert iso_profile.apply(-1.0) == pytest.approx(0.1)
    assert iso_profile.apply(0.25) == pytest.approx(0.3)
    assert iso_profile.apply(2.0) == pytest.approx(0.9)

    id_profile = CalibrationProfile(driver_id="dummy_test", method=CalibrationMethod.IDENTITY)
    assert id_profile.apply(0.75, scores_are_logits=False) == pytest.approx(0.75)

    with pytest.raises(ValidationError):
        CalibrationProfile(
            driver_id="dummy_test",
            method=CalibrationMethod.ISOTONIC,
            isotonic_x_knots=[0.0, 1.0],
            isotonic_y_knots=[0.8, 0.2],
        )


async def test_classifier_driver_abc_airgap_and_budget() -> None:
    with pytest.raises(TypeError):
        ClassifierDriver()  # type: ignore[abstract]

    wrong_profile = CalibrationProfile(driver_id="other_driver")
    with pytest.raises(ValueError, match="does not match"):
        _DummyDriver(calibration_profile=wrong_profile)

    profile = CalibrationProfile(driver_id="dummy_test", temperature=1.0, sample_count=10, ece=0.02)
    cloud_driver = _DummyDriver(is_cloud=True, max_candidates=2, calibration_profile=profile)

    req_ok = ClassifierRequest(
        query_text="find validator",
        heads=[
            ClassifierHead(head_id="a", candidate_text="a.py"),
            ClassifierHead(head_id="b", candidate_text="b.py"),
        ],
        cloud_egress_allowed=True,
    )
    resp = await cloud_driver.classify(req_ok)
    assert resp.is_calibrated is True
    assert resp.driver_latency_ms >= 0.0
    assert resp.calibrated_probabilities["a"] == pytest.approx(0.5)

    req_airgap = ClassifierRequest(
        query_text="secret check",
        heads=[ClassifierHead(head_id="a", candidate_text="a.py")],
        cloud_egress_allowed=False,
    )
    with pytest.raises(PermissionError, match="Privacy Air-Gap"):
        await cloud_driver.classify(req_airgap)

    req_over_budget = ClassifierRequest(
        query_text="too many",
        heads=[
            ClassifierHead(head_id="1", candidate_text="1"),
            ClassifierHead(head_id="2", candidate_text="2"),
            ClassifierHead(head_id="3", candidate_text="3"),
        ],
    )
    with pytest.raises(ValueError, match="at most 2"):
        await cloud_driver.classify(req_over_budget)


def test_cartridges_gold_dataset_and_example_toml_parse() -> None:
    cartridge_files = sorted((REPO_ROOT / "quadgate" / "cartridges").glob("*.yaml"))
    assert len(cartridge_files) == 5
    for path in cartridge_files:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = CartridgeSpec.model_validate(raw)
        assert spec.name

    gold_path = REPO_ROOT / "benchmarks" / "calibration_gold.jsonl"
    lines = [ln for ln in gold_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 6
    for line in lines:
        sample = CalibrationSample.model_validate(json.loads(line))
        assert sample.id

    example_toml = tomllib.loads((REPO_ROOT / "quadgate.example.toml").read_text(encoding="utf-8"))
    daemon_cfg = DaemonConfig.model_validate(example_toml["daemon"])
    thresh_cfg = ThresholdConfig.model_validate(example_toml["thresholds"])
    tiers_cfg = TierModelConfig.model_validate(example_toml["tiers"]["models"])
    assert daemon_cfg.port == 8787
    assert thresh_cfg.gate2_invariant_block == pytest.approx(0.78)
    assert tiers_cfg.floor == "flash_lite"


def test_shim_stdlib_only_ast_and_cold_boot() -> None:
    shim_path = REPO_ROOT / "quadgate" / "shim.py"
    tree = ast.parse(shim_path.read_text(encoding="utf-8"), filename=str(shim_path))
    allowed_imports = {"sys", "json", "os", "re", "urllib", "urllib.request", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_imports, f"Disallowed import in shim.py: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod in allowed_imports, f"Disallowed from-import in shim.py: {mod}"

    assert parse_shim_args(["--harness", "antigravity", "--gate", "intake"]) == (
        "antigravity",
        "intake",
    )
    assert parse_shim_args(["--harness=claude_code", "--gate=firewall"]) == (
        "claude_code",
        "firewall",
    )
    with pytest.raises(ValueError):
        parse_shim_args(["--harness", "antigravity", "--gate", "bogus"])

    probe = (
        "import sys, quadgate.shim; "
        "forbidden = {'pydantic', 'yaml', 'aiohttp', 'tree_sitter'} & set(sys.modules); "
        "assert not forbidden, f'Third-party modules loaded: {forbidden}'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_all_repository_modules_import_cleanly() -> None:
    discovered: list[str] = []
    for root_name in ("quadgate", "benchmarks"):
        root_dir = REPO_ROOT / root_name
        for py_file in sorted(root_dir.rglob("*.py")):
            rel = py_file.relative_to(REPO_ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            mod_name = ".".join(parts)
            discovered.append(mod_name)
            importlib.import_module(mod_name)
    assert "quadgate.contracts" in discovered
    assert "quadgate.drivers.base" in discovered
    assert "quadgate.shim" in discovered
    assert "benchmarks.runner" in discovered


def test_cli_parser_and_unbuilt_command_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    from quadgate.cli import build_parser, main as cli_main

    parser = build_parser()
    ns = parser.parse_args(["init", "--harness", "claude-code", "--local"])
    assert ns.command == "init"
    assert ns.harness == "claude-code"
    assert ns.local is True

    bench_ns = parser.parse_args(
        ["bench", "--suite", "coding", "--runs", "2", "--dry-replay", "--output", "out.md"]
    )
    assert bench_ns.command == "bench"
    assert bench_ns.suite == "coding"
    assert bench_ns.runs == 2
    assert bench_ns.dry_replay is True
    assert bench_ns.live is False
    assert bench_ns.output == "out.md"

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert cli_main(["mcp"]) == 0

    monkeypatch.chdir(tmp_path)
    assert cli_main(["exec-codex", "--", "cat", ".env"]) == 2

