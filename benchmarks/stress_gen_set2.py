"""Generator and empirical validator for QuadGate Stress Set 2.

Set 2 (`stress_tool_cache_routing.jsonl`) covers 35 multi-turn tool-use,
KV-cache preservation, compact-reset, Decision-1 pragmatic edge cases,
multi-clause Gate-4 completeness, and 2-strike / Gate-3 ECC tier escalation
scenarios across coding and knowledge cartridges:

- STRESS_TC_01 .. STRESS_TC_10: pragmatic_ambiguity (5 counterfactual + 5 tricky directive)
- STRESS_TC_11 .. STRESS_TC_20: kv_cache_and_compact_reset (8-12 turn chains + Turn 7 reset)
- STRESS_TC_21 .. STRESS_TC_28: multi_clause_completeness (5-7 numbered atomic requirements)
- STRESS_TC_29 .. STRESS_TC_35: two_strike_and_tier_escalation (>=4 callers vs 0-caller leaf)

Usage:
    python benchmarks/stress_gen_set2.py
    python benchmarks/stress_gen_set2.py --verify
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT_PATH = (
    REPO_ROOT / "benchmarks" / "suites" / "stress_tool_cache_routing.jsonl"
)

SUITE_NAME = "stress_tool_cache_routing"
EXPECTED_TASK_IDS = [f"STRESS_TC_{idx:02d}" for idx in range(1, 36)]


def _py_test_preamble() -> str:
    return (
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\n"
    )


def _usage_turn(
    provider: str,
    prompt_tokens: int,
    cache_read: int,
    cache_creation: int,
    output_tokens: int,
) -> dict[str, Any]:
    cache_read_clamped = max(0, min(prompt_tokens, cache_read))
    if provider == "anthropic":
        non_cached = max(0, prompt_tokens - cache_read_clamped - cache_creation)
        return {
            "input_tokens": non_cached,
            "cache_read_input_tokens": cache_read_clamped,
            "cache_creation_input_tokens": max(0, cache_creation),
            "output_tokens": output_tokens,
        }
    if provider == "openai":
        return {
            "prompt_tokens": prompt_tokens,
            "prompt_tokens_details": {"cached_tokens": cache_read_clamped},
            "completion_tokens": output_tokens,
        }
    if provider == "gemini":
        return {
            "promptTokenCount": prompt_tokens,
            "cachedContentTokenCount": cache_read_clamped,
            "candidatesTokenCount": output_tokens,
        }
    raise ValueError(f"Unsupported provider: {provider!r}")


def _model_for(provider: str, tier: str) -> str:
    models = {
        "anthropic": {
            "floor": "claude-3-5-haiku",
            "mid": "claude-sonnet-4-5",
            "frontier": "claude-opus-4-1",
        },
        "openai": {
            "floor": "gpt-4o-mini",
            "mid": "gpt-4o",
            "frontier": "gpt-4o",
        },
        "gemini": {
            "floor": "gemini-2.5-flash",
            "mid": "gemini-2.5-pro",
            "frontier": "gemini-2.5-pro",
        },
    }
    return models[provider][tier]


def _make_replay_trace(
    *,
    provider: str,
    expected_tier: str,
    num_turns: int,
    classifier_driver: str = "onnx_local",
    simulates_one_strike: bool = False,
    compact_reset_turn: int | None = None,
    seed_idx: int = 1,
) -> dict[str, Any]:
    base_prefix = 3600 + (seed_idx % 5) * 240
    qg_prefix = base_prefix + 420
    baseline_model = _model_for(provider, "frontier")
    qg_model = _model_for(provider, expected_tier)
    frontier_model = _model_for(provider, "frontier")

    baseline_turns: list[dict[str, Any]] = []
    quadgate_turns: list[dict[str, Any]] = []

    for turn in range(1, num_turns + 1):
        turn_growth = (turn - 1) * 260
        out_tok = 190 + ((seed_idx + turn) % 4) * 35

        # Baseline suffers frequent prefix invalidation across tool iterations.
        b_prompt = base_prefix + turn_growth + 320
        if turn == 1:
            b_read = 0
            b_create = b_prompt
        elif turn in (3, 6, 9):
            b_read = int(b_prompt * 0.18)
            b_create = b_prompt - b_read
        else:
            b_read = int(b_prompt * 0.56)
            b_create = 280
        baseline_turns.append(
            {
                "turn_index": turn,
                "model": baseline_model,
                "latency_ms": float(920 + turn * 45 + (seed_idx % 5) * 20),
                "usage": _usage_turn(provider, b_prompt, b_read, b_create, out_tok + 45),
            }
        )

        # QuadGate pins prefix across turns 2..N unless explicit compact_reset occurs.
        q_prompt = qg_prefix + (turn - 1) * 210 + (180 if turn == 1 else 140)
        if turn == 1:
            q_read = 0
            q_create = q_prompt
        elif compact_reset_turn is not None and turn == compact_reset_turn:
            # Explicit compaction resets prefix once, keeping cache-read share healthy.
            q_read = int(q_prompt * 0.55)
            q_create = q_prompt - q_read
        else:
            q_read = int(q_prompt * 0.94)
            q_create = 0
        quadgate_turns.append(
            {
                "turn_index": turn,
                "model": qg_model,
                "latency_ms": float(580 + turn * 25 + (seed_idx % 4) * 15),
                "usage": _usage_turn(provider, q_prompt, q_read, q_create, out_tok),
            }
        )

    retry_turns: list[dict[str, Any]] = []
    if simulates_one_strike:
        retry_prompt = qg_prefix + num_turns * 180
        retry_turns.append(
            {
                "turn_index": num_turns + 1,
                "model": frontier_model,
                "latency_ms": 1140.0,
                "usage": _usage_turn(
                    provider,
                    retry_prompt,
                    int(retry_prompt * 0.85),
                    0,
                    240,
                ),
            }
        )
    clf_cost = 0.00018 if classifier_driver == "decision1" else 0.0
    clf_latency = 42.5 if classifier_driver == "decision1" else 14.2

    return {
        "baseline": {
            "provider": provider,
            "model": baseline_model,
            "wall_clock_ms": 1480 + num_turns * 340 + seed_idx * 17,
            "turns": baseline_turns,
            "retry_turns": [],
        },
        "quadgate": {
            "provider": provider,
            "model": qg_model,
            "classifier_driver": classifier_driver,
            "classifier_cost_usd": clf_cost,
            "hook_ipc_ms": 1.85,
            "classifier_latency_ms": clf_latency,
            "wall_clock_ms": 820 + num_turns * 210 + (260 if simulates_one_strike else 0),
            "turns": quadgate_turns,
            "retry_turns": retry_turns,
        },
    }


def _provider_for(idx: int) -> str:
    return ("anthropic", "openai", "gemini")[(idx - 1) % 3]


# ---------------------------------------------------------------------------
# Category 1: STRESS_TC_01 .. STRESS_TC_10 — pragmatic_ambiguity
# ---------------------------------------------------------------------------


def _build_pragmatic_ambiguity_tasks() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        # 5 Counterfactual / Brainstorm prompts -> expected_advisory = True
        {
            "id": "STRESS_TC_01",
            "expected_advisory": True,
            "target": "src/ratelimit.py",
            "pytest": "tests/test_tc01.py",
            "tier": "floor",
            "prompt": (
                "Just wondering aloud before touching anything: hypothetically, if we "
                "allowed burst refills in src/ratelimit.py to exceed capacity on clock skew, "
                "what would happen to TokenBucket.consume? Compare pros and cons only and do "
                "not edit files yet."
            ),
            "files": {
                "src/__init__.py": "",
                "src/ratelimit.py": (
                    "class TokenBucket:\n"
                    "    def __init__(self, capacity: float, refill_per_sec: float) -> None:\n"
                    "        self.capacity = capacity\n"
                    "        self.refill_per_sec = refill_per_sec\n"
                    "        self.tokens = capacity\n"
                    "        self.last_ts = 0.0\n\n"
                    "    def consume(self, amount: float, now: float) -> bool:\n"
                    "        elapsed = now - self.last_ts\n"
                    "        self.tokens = self.tokens + elapsed * self.refill_per_sec\n"
                    "        self.last_ts = now\n"
                    "        if self.tokens >= amount:\n"
                    "            self.tokens -= amount\n"
                    "            return True\n"
                    "        return False\n"
                ),
                "src/config.py": "DEFAULT_BURST_LIMIT = 10.0\n",
                "tests/test_tc01.py": (
                    _py_test_preamble()
                    + "from src.ratelimit import TokenBucket\n\n"
                    "def test_bucket_clamps_capacity_and_handles_clock_skew():\n"
                    "    tb = TokenBucket(capacity=5.0, refill_per_sec=1.0)\n"
                    "    assert tb.consume(2.0, now=10.0) is True\n"
                    "    assert tb.consume(1.0, now=5.0) is True\n"
                    "    assert tb.tokens <= 5.0\n"
                    "    assert tb.consume(10.0, now=100.0) is False\n"
                ),
            },
            "solution_files": {
                "src/ratelimit.py": (
                    "class TokenBucket:\n"
                    "    def __init__(self, capacity: float, refill_per_sec: float) -> None:\n"
                    "        self.capacity = capacity\n"
                    "        self.refill_per_sec = refill_per_sec\n"
                    "        self.tokens = capacity\n"
                    "        self.last_ts = 0.0\n\n"
                    "    def consume(self, amount: float, now: float) -> bool:\n"
                    "        elapsed = max(0.0, now - self.last_ts)\n"
                    "        self.tokens = min(self.capacity, self.tokens + elapsed * "
                    "self.refill_per_sec)\n"
                    "        self.last_ts = max(self.last_ts, now)\n"
                    "        if self.tokens >= amount:\n"
                    "            self.tokens -= amount\n"
                    "            return True\n"
                    "        return False\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_02",
            "expected_advisory": True,
            "target": "src/cache.py",
            "pytest": "tests/test_tc02.py",
            "tier": "floor",
            "prompt": (
                "Hypothetically explore the tradeoffs if src/cache.py invalidated expired "
                "keys lazily inside BoundedCache.get instead of eagerly in put. What would "
                "fail under capacity pressure? Only analyze options and do not modify code."
            ),
            "files": {
                "src/__init__.py": "",
                "src/cache.py": (
                    "from collections import OrderedDict\n\n"
                    "class BoundedCache:\n"
                    "    def __init__(self, max_size: int) -> None:\n"
                    "        self.max_size = max_size\n"
                    "        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()\n\n"
                    "    def put(self, key: str, value: str, expires_at: float) -> None:\n"
                    "        self._items[key] = (value, expires_at)\n\n"
                    "    def get(self, key: str, now: float) -> str | None:\n"
                    "        entry = self._items.get(key)\n"
                    "        return entry[0] if entry else None\n"
                ),
                "src/store_cfg.py": "MAX_ENTRIES = 128\n",
                "tests/test_tc02.py": (
                    _py_test_preamble()
                    + "from src.cache import BoundedCache\n\n"
                    "def test_cache_evicts_expired_and_enforces_lru():\n"
                    "    c = BoundedCache(max_size=2)\n"
                    "    c.put('a', '1', expires_at=5.0)\n"
                    "    c.put('b', '2', expires_at=50.0)\n"
                    "    assert c.get('a', now=10.0) is None\n"
                    "    c.put('c', '3', expires_at=60.0)\n"
                    "    assert c.get('b', now=15.0) == '2'\n"
                    "    assert c.get('c', now=15.0) == '3'\n"
                ),
            },
            "solution_files": {
                "src/cache.py": (
                    "from collections import OrderedDict\n\n"
                    "class BoundedCache:\n"
                    "    def __init__(self, max_size: int) -> None:\n"
                    "        self.max_size = max_size\n"
                    "        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()\n\n"
                    "    def put(self, key: str, value: str, expires_at: float) -> None:\n"
                    "        if key in self._items:\n"
                    "            self._items.move_to_end(key)\n"
                    "        self._items[key] = (value, expires_at)\n"
                    "        while len(self._items) > self.max_size:\n"
                    "            self._items.popitem(last=False)\n\n"
                    "    def get(self, key: str, now: float) -> str | None:\n"
                    "        entry = self._items.get(key)\n"
                    "        if entry is None:\n"
                    "            return None\n"
                    "        val, exp = entry\n"
                    "        if now >= exp:\n"
                    "            del self._items[key]\n"
                    "            return None\n"
                    "        self._items.move_to_end(key)\n"
                    "        return val\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_03",
            "expected_advisory": True,
            "target": "src/backoff.py",
            "pytest": "tests/test_tc03.py",
            "tier": "floor",
            "prompt": (
                "Should we consider switching src/backoff.py from capped exponential delay "
                "to decorrelated jitter when retry attempt exceeds five? Let's brainstorm pros "
                "and cons first without making any edits."
            ),
            "files": {
                "src/__init__.py": "",
                "src/backoff.py": (
                    "def compute_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> "
                    "float:\n"
                    "    return base * (2 ** attempt)\n"
                ),
                "src/http_opts.py": "DEFAULT_RETRIES = 6\n",
                "tests/test_tc03.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.backoff import compute_delay\n\n"
                    "def test_backoff_respects_cap_and_rejects_negative():\n"
                    "    assert compute_delay(0, base=1.0, cap=5.0) == 1.0\n"
                    "    assert compute_delay(10, base=1.0, cap=5.0) == 5.0\n"
                    "    with pytest.raises(ValueError):\n"
                    "        compute_delay(-1)\n"
                ),
            },
            "solution_files": {
                "src/backoff.py": (
                    "def compute_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> "
                    "float:\n"
                    "    if attempt < 0:\n"
                    "        raise ValueError('attempt must be non-negative')\n"
                    "    return min(cap, base * (2 ** min(attempt, 30)))\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_04",
            "expected_advisory": True,
            "target": "src/csv_stream.py",
            "pytest": "tests/test_tc04.py",
            "tier": "floor",
            "prompt": (
                "Could you imagine what would happen if src/csv_stream.py split lines naively "
                "without tracking open quote state? Walk through the edge cases hypothetically; "
                "read-only exploration, no edits yet."
            ),
            "files": {
                "src/__init__.py": "",
                "src/csv_stream.py": (
                    "def parse_rows(raw: str) -> list[list[str]]:\n"
                    "    return [line.split(',') for line in raw.strip().splitlines() if line]\n"
                ),
                "src/dialect.py": "DELIMITER = ','\n",
                "tests/test_tc04.py": (
                    _py_test_preamble()
                    + "from src.csv_stream import parse_rows\n\n"
                    "def test_parse_rows_handles_quoted_commas():\n"
                    "    rows = parse_rows('id,note\\n1,\"alpha, beta\"\\n')\n"
                    "    assert rows == [['id', 'note'], ['1', 'alpha, beta']]\n"
                ),
            },
            "solution_files": {
                "src/csv_stream.py": (
                    "import csv\n"
                    "import io\n\n"
                    "def parse_rows(raw: str) -> list[list[str]]:\n"
                    "    reader = csv.reader(io.StringIO(raw))\n"
                    "    return [row for row in reader if row]\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_05",
            "expected_advisory": True,
            "target": "src/semver_cmp.py",
            "pytest": "tests/test_tc05.py",
            "tier": "floor",
            "prompt": (
                "Just asking for design feedback: suppose we changed compare_versions in "
                "src/semver_cmp.py so numeric prerelease identifiers compare lexicographically "
                "instead of numerically — what breaks? Explore pros and cons only and do not "
                "touch any files."
            ),
            "files": {
                "src/__init__.py": "",
                "src/semver_cmp.py": (
                    "def compare_versions(a: str, b: str) -> int:\n"
                    "    return -1 if a < b else (1 if a > b else 0)\n"
                ),
                "src/pkg_meta.py": "MIN_SUPPORTED = '1.2.0'\n",
                "tests/test_tc05.py": (
                    _py_test_preamble()
                    + "from src.semver_cmp import compare_versions\n\n"
                    "def test_numeric_semver_ordering():\n"
                    "    assert compare_versions('1.10.0', '1.2.0') == 1\n"
                    "    assert compare_versions('2.0.0-alpha.2', '2.0.0-alpha.10') == -1\n"
                    "    assert compare_versions('2.0.0-rc.1', '2.0.0') == -1\n"
                ),
            },
            "solution_files": {
                "src/semver_cmp.py": (
                    "def _parse(v: str) -> tuple[tuple[int, int, int], bool, "
                    "tuple[tuple[int, int | str], ...]]:\n"
                    "    core, sep, pre = v.partition('-')\n"
                    "    maj, mn, pat = (int(x) for x in core.split('.'))\n"
                    "    pre_parts: list[tuple[int, int | str]] = []\n"
                    "    if sep:\n"
                    "        for part in pre.split('.'):\n"
                    "            if part.isdigit():\n"
                    "                pre_parts.append((0, int(part)))\n"
                    "            else:\n"
                    "                pre_parts.append((1, part))\n"
                    "    return ((maj, mn, pat), not bool(sep), tuple(pre_parts))\n\n"
                    "def compare_versions(a: str, b: str) -> int:\n"
                    "    pa, pb = _parse(a), _parse(b)\n"
                    "    return -1 if pa < pb else (1 if pa > pb else 0)\n"
                ),
            },
        },
        # 5 Tricky Directive prompts with hypothetical/negated sub-clauses -> advisory = False
        {
            "id": "STRESS_TC_06",
            "expected_advisory": False,
            "target": "src/tax_alloc.py",
            "pytest": "tests/test_tc06.py",
            "tier": "floor",
            "prompt": (
                "Execute immediately without speculating: directly fix allocate_tax_cents in "
                "src/tax_alloc.py so rounding remainders go to the largest line items; even "
                "if zero-amount lines were present, do not assign pennies to them, and run pytest."
            ),
            "files": {
                "src/__init__.py": "",
                "src/tax_alloc.py": (
                    "def allocate_tax_cents(lines: list[int], total_tax: int) -> list[int]:\n"
                    "    s = sum(lines)\n"
                    "    if s == 0:\n"
                    "        return [0] * len(lines)\n"
                    "    return [int(x * total_tax / s) for x in lines]\n"
                ),
                "src/currency.py": "SCALE = 100\n",
                "tests/test_tc06.py": (
                    _py_test_preamble()
                    + "from src.tax_alloc import allocate_tax_cents\n\n"
                    "def test_exact_remainder_distribution_skips_zeros():\n"
                    "    res = allocate_tax_cents([0, 100, 200], 10)\n"
                    "    assert res[0] == 0\n"
                    "    assert sum(res) == 10\n"
                    "    assert res == [0, 3, 7]\n"
                ),
            },
            "solution_files": {
                "src/tax_alloc.py": (
                    "def allocate_tax_cents(lines: list[int], total_tax: int) -> list[int]:\n"
                    "    s = sum(lines)\n"
                    "    if s <= 0:\n"
                    "        return [0] * len(lines)\n"
                    "    raw = [(x * total_tax) / s if x > 0 else 0.0 for x in lines]\n"
                    "    floors = [int(v) for v in raw]\n"
                    "    rem = total_tax - sum(floors)\n"
                    "    order = sorted(\n"
                    "        [i for i, x in enumerate(lines) if x > 0],\n"
                    "        key=lambda i: (raw[i] - floors[i], lines[i]),\n"
                    "        reverse=True,\n"
                    "    )\n"
                    "    for idx in order[:rem]:\n"
                    "        floors[idx] += 1\n"
                    "    return floors\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_07",
            "expected_advisory": False,
            "target": "src/cron_parse.py",
            "pytest": "tests/test_tc07.py",
            "tier": "floor",
            "prompt": (
                "Stop exploring tradeoffs and implement strict bounds checking in "
                "src/cron_parse.py for parse_cron_field: if someone were to pass an "
                "out-of-range hour like 24 or step 0, raise ValueError rather than wrapping "
                "silently, then run pytest."
            ),
            "files": {
                "src/__init__.py": "",
                "src/cron_parse.py": (
                    "def parse_cron_field(expr: str, min_val: int, max_val: int) -> list[int]:\n"
                    "    if expr == '*':\n"
                    "        return list(range(min_val, max_val + 1))\n"
                    "    return [int(expr)]\n"
                ),
                "src/tz.py": "UTC = 'UTC'\n",
                "tests/test_tc07.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.cron_parse import parse_cron_field\n\n"
                    "def test_cron_step_and_bounds_validation():\n"
                    "    assert parse_cron_field('*/6', 0, 23) == [0, 6, 12, 18]\n"
                    "    with pytest.raises(ValueError):\n"
                    "        parse_cron_field('24', 0, 23)\n"
                    "    with pytest.raises(ValueError):\n"
                    "        parse_cron_field('*/0', 0, 23)\n"
                ),
            },
            "solution_files": {
                "src/cron_parse.py": (
                    "def parse_cron_field(expr: str, min_val: int, max_val: int) -> list[int]:\n"
                    "    base, _, step_s = expr.partition('/')\n"
                    "    step = 1\n"
                    "    if step_s or '/' in expr:\n"
                    "        step = int(step_s)\n"
                    "        if step <= 0:\n"
                    "            raise ValueError('step must be positive')\n"
                    "    if base == '*':\n"
                    "        start, end = min_val, max_val\n"
                    "    elif '-' in base:\n"
                    "        lo_s, hi_s = base.split('-', 1)\n"
                    "        start, end = int(lo_s), int(hi_s)\n"
                    "    else:\n"
                    "        val = int(base)\n"
                    "        start, end = val, val\n"
                    "    if start < min_val or end > max_val or start > end:\n"
                    "        raise ValueError('cron field out of bounds')\n"
                    "    return list(range(start, end + 1, step))\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_08",
            "expected_advisory": False,
            "target": "src/dag_sort.py",
            "pytest": "tests/test_tc08.py",
            "tier": "floor",
            "prompt": (
                "Execute this edit immediately in src/dag_sort.py: update topological_sort "
                "to use Kahn's algorithm with deterministic lexicographical tie-breaking; "
                "should a dependency cycle exist in the graph, raise CycleError instead of "
                "looping, and verify with pytest."
            ),
            "files": {
                "src/__init__.py": "",
                "src/dag_sort.py": (
                    "class CycleError(ValueError):\n"
                    "    pass\n\n"
                    "def topological_sort(deps: dict[str, list[str]]) -> list[str]:\n"
                    "    return sorted(deps.keys())\n"
                ),
                "src/graph_io.py": "FORMAT = 'adj'\n",
                "tests/test_tc08.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.dag_sort import CycleError, topological_sort\n\n"
                    "def test_kahn_tie_break_and_cycle():\n"
                    "    order = topological_sort({'b': ['a'], 'c': ['a'], 'a': []})\n"
                    "    assert order == ['a', 'b', 'c']\n"
                    "    with pytest.raises(CycleError):\n"
                    "        topological_sort({'a': ['b'], 'b': ['a']})\n"
                ),
            },
            "solution_files": {
                "src/dag_sort.py": (
                    "import heapq\n\n"
                    "class CycleError(ValueError):\n"
                    "    pass\n\n"
                    "def topological_sort(deps: dict[str, list[str]]) -> list[str]:\n"
                    "    nodes = set(deps.keys())\n"
                    "    for parents in deps.values():\n"
                    "        nodes.update(parents)\n"
                    "    indeg = {n: 0 for n in nodes}\n"
                    "    children: dict[str, list[str]] = {n: [] for n in nodes}\n"
                    "    for node, parents in deps.items():\n"
                    "        for p in parents:\n"
                    "            children[p].append(node)\n"
                    "            indeg[node] += 1\n"
                    "    heap = [n for n, d in indeg.items() if d == 0]\n"
                    "    heapq.heapify(heap)\n"
                    "    out: list[str] = []\n"
                    "    while heap:\n"
                    "        cur = heapq.heappop(heap)\n"
                    "        out.append(cur)\n"
                    "        for nxt in children[cur]:\n"
                    "            indeg[nxt] -= 1\n"
                    "            if indeg[nxt] == 0:\n"
                    "                heapq.heappush(heap, nxt)\n"
                    "    if len(out) != len(nodes):\n"
                    "        raise CycleError('dependency cycle detected')\n"
                    "    return out\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_09",
            "expected_advisory": False,
            "target": "src/cidr_acl.py",
            "pytest": "tests/test_tc09.py",
            "tier": "floor",
            "prompt": (
                "Go ahead and modify is_ip_allowed in src/cidr_acl.py right now: evaluate "
                "deny rules before allow rules, and even if a CIDR entry omits prefix length "
                "or looks malformed, reject invalid networks cleanly; verify via pytest."
            ),
            "files": {
                "src/__init__.py": "",
                "src/cidr_acl.py": (
                    "def is_ip_allowed(ip: str, allow: list[str], deny: list[str]) -> bool:\n"
                    "    return ip in allow\n"
                ),
                "src/net_tags.py": "ZONE = 'prod'\n",
                "tests/test_tc09.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.cidr_acl import is_ip_allowed\n\n"
                    "def test_deny_precedes_allow_and_validates_cidr():\n"
                    "    assert is_ip_allowed('10.0.1.5', ['10.0.0.0/16'], ['10.0.1.0/24']) "
                    "is False\n"
                    "    assert is_ip_allowed('10.0.2.5', ['10.0.0.0/16'], ['10.0.1.0/24']) "
                    "is True\n"
                    "    with pytest.raises(ValueError):\n"
                    "        is_ip_allowed('10.0.2.5', ['not-a-cidr'], [])\n"
                ),
            },
            "solution_files": {
                "src/cidr_acl.py": (
                    "import ipaddress\n\n"
                    "def is_ip_allowed(ip: str, allow: list[str], deny: list[str]) -> bool:\n"
                    "    addr = ipaddress.ip_address(ip)\n"
                    "    deny_nets = [ipaddress.ip_network(n, strict=False) for n in deny]\n"
                    "    allow_nets = [ipaddress.ip_network(n, strict=False) for n in allow]\n"
                    "    if any(addr in net for net in deny_nets):\n"
                    "        return False\n"
                    "    return any(addr in net for net in allow_nets)\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_10",
            "expected_advisory": False,
            "target": "src/circuit.py",
            "pytest": "tests/test_tc10.py",
            "tier": "floor",
            "prompt": (
                "Implement the fix immediately in src/circuit.py so CircuitBreaker "
                "transitions from OPEN to HALF_OPEN once cooldown elapses; were a single "
                "probe to succeed it must reset failure_count and close without dropping "
                "pending state, then run pytest."
            ),
            "files": {
                "src/__init__.py": "",
                "src/circuit.py": (
                    "class CircuitBreaker:\n"
                    "    def __init__(self, threshold: int, cooldown: float) -> None:\n"
                    "        self.threshold = threshold\n"
                    "        self.cooldown = cooldown\n"
                    "        self.state = 'CLOSED'\n"
                    "        self.failure_count = 0\n"
                    "        self.opened_at = 0.0\n\n"
                    "    def allow_request(self, now: float) -> bool:\n"
                    "        return self.state == 'CLOSED'\n\n"
                    "    def record_failure(self, now: float) -> None:\n"
                    "        self.failure_count += 1\n\n"
                    "    def record_success(self) -> None:\n"
                    "        pass\n"
                ),
                "src/probes.py": "PROBE_TIMEOUT = 1.5\n",
                "tests/test_tc10.py": (
                    _py_test_preamble()
                    + "from src.circuit import CircuitBreaker\n\n"
                    "def test_half_open_probe_and_recovery():\n"
                    "    cb = CircuitBreaker(threshold=2, cooldown=10.0)\n"
                    "    cb.record_failure(1.0)\n"
                    "    cb.record_failure(2.0)\n"
                    "    assert cb.state == 'OPEN'\n"
                    "    assert cb.allow_request(5.0) is False\n"
                    "    assert cb.allow_request(13.0) is True\n"
                    "    assert cb.state == 'HALF_OPEN'\n"
                    "    cb.record_success()\n"
                    "    assert cb.state == 'CLOSED'\n"
                    "    assert cb.failure_count == 0\n"
                ),
            },
            "solution_files": {
                "src/circuit.py": (
                    "class CircuitBreaker:\n"
                    "    def __init__(self, threshold: int, cooldown: float) -> None:\n"
                    "        self.threshold = threshold\n"
                    "        self.cooldown = cooldown\n"
                    "        self.state = 'CLOSED'\n"
                    "        self.failure_count = 0\n"
                    "        self.opened_at = 0.0\n\n"
                    "    def allow_request(self, now: float) -> bool:\n"
                    "        if self.state == 'OPEN' and (now - self.opened_at) >= "
                    "self.cooldown:\n"
                    "            self.state = 'HALF_OPEN'\n"
                    "            return True\n"
                    "        return self.state != 'OPEN'\n\n"
                    "    def record_failure(self, now: float) -> None:\n"
                    "        self.failure_count += 1\n"
                    "        if self.state == 'HALF_OPEN' or self.failure_count >= "
                    "self.threshold:\n"
                    "            self.state = 'OPEN'\n"
                    "            self.opened_at = now\n\n"
                    "    def record_success(self) -> None:\n"
                    "        self.failure_count = 0\n"
                    "        self.state = 'CLOSED'\n"
                ),
            },
        },
    ]

    out: list[dict[str, Any]] = []
    for idx, s in enumerate(specs, start=1):
        provider = _provider_for(idx)
        target = s["target"]
        out.append(
            {
                "task_id": s["id"],
                "suite": SUITE_NAME,
                "task_kind": "coding",
                "category": "pragmatic_ambiguity",
                "cartridge": "software_engineering",
                "prompt": s["prompt"],
                "files": s["files"],
                "flawed_draft_files": {},
                "solution_files": s["solution_files"],
                "expected_touched_files": [target],
                "pytest_target": s["pytest"],
                "expected_tier": s["tier"],
                "simulates_one_strike_promotion": False,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=s["tier"],
                    num_turns=4,
                    classifier_driver="decision1",
                    simulates_one_strike=False,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_gate1_counterfactual": {
                        "expected_advisory": s["expected_advisory"]
                    },
                    "assert_pass0_recall": {
                        "expected_any": [target],
                        "top_k": 10,
                    },
                },
            }
        )
    return out


# ---------------------------------------------------------------------------
# Category 2: STRESS_TC_11 .. STRESS_TC_20 — kv_cache_and_compact_reset
# ---------------------------------------------------------------------------


def _build_turn_assertions() -> list[dict[str, Any]]:
    return [
        {
            "turn_index": 2,
            "tool_name": "read_file",
            "compact_reset": False,
            "expect_empty_hints": True,
        },
        {
            "turn_index": 4,
            "tool_name": "replace_file_content",
            "compact_reset": False,
            "expect_empty_hints": True,
        },
        {
            "turn_index": 7,
            "tool_name": "compact_reset",
            "compact_reset": True,
            "expect_hints_nonempty": True,
        },
        {
            "turn_index": 8,
            "tool_name": "read_file",
            "compact_reset": False,
            "expect_empty_hints": True,
        },
    ]


def _build_kv_cache_and_compact_tasks() -> list[dict[str, Any]]:
    coding_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_11",
            "turns": 9,
            "target": "src/pool.py",
            "pytest": "tests/test_tc11.py",
            "tier": "mid",
            "prompt": (
                "Fix ConnectionPool acquire and release in src/pool.py enforcing max_size "
                "and PoolExhaustedError"
            ),
            "files": {
                "src/__init__.py": "",
                "src/pool.py": (
                    '"""ConnectionPool acquire release checkout idle max_size."""\n\n'
                    "class PoolExhaustedError(RuntimeError):\n"
                    "    pass\n\n"
                    "class ConnectionPool:\n"
                    "    def __init__(self, max_size: int) -> None:\n"
                    "        self.max_size = max_size\n"
                    "        self._idle = [f'conn-{i}' for i in range(max_size)]\n"
                    "        self._in_use: set[str] = set()\n\n"
                    "    def acquire(self) -> str:\n"
                    "        return 'conn-0'\n\n"
                    "    def release(self, conn: str) -> None:\n"
                    "        pass\n"
                ),
                "src/config.py": "POOL_TIMEOUT_MS = 250\n",
                "src/metrics.py": "def record_wait(ms: float) -> None:\n    return None\n",
                "src/errors.py": "class ConnectionBrokenError(Exception):\n    pass\n",
                "tests/test_tc11.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.pool import ConnectionPool, PoolExhaustedError\n\n"
                    "def test_pool_checkout_release_and_exhaustion():\n"
                    "    p = ConnectionPool(2)\n"
                    "    c1 = p.acquire()\n"
                    "    c2 = p.acquire()\n"
                    "    assert c1 != c2\n"
                    "    with pytest.raises(PoolExhaustedError):\n"
                    "        p.acquire()\n"
                    "    p.release(c1)\n"
                    "    assert p.acquire() == c1\n"
                    "    with pytest.raises(ValueError):\n"
                    "        p.release('unknown')\n"
                ),
            },
            "solution_files": {
                "src/pool.py": (
                    "class PoolExhaustedError(RuntimeError):\n"
                    "    pass\n\n"
                    "class ConnectionPool:\n"
                    "    def __init__(self, max_size: int) -> None:\n"
                    "        self.max_size = max_size\n"
                    "        self._idle = [f'conn-{i}' for i in range(max_size)]\n"
                    "        self._in_use: set[str] = set()\n\n"
                    "    def acquire(self) -> str:\n"
                    "        if not self._idle:\n"
                    "            raise PoolExhaustedError('pool exhausted')\n"
                    "        conn = self._idle.pop(0)\n"
                    "        self._in_use.add(conn)\n"
                    "        return conn\n\n"
                    "    def release(self, conn: str) -> None:\n"
                    "        if conn not in self._in_use:\n"
                    "            raise ValueError(f'unknown handle: {conn}')\n"
                    "        self._in_use.remove(conn)\n"
                    "        self._idle.append(conn)\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_12",
            "turns": 10,
            "target": "src/token_auth.py",
            "pytest": "tests/test_tc12.py",
            "tier": "mid",
            "prompt": (
                "Fix sign_session_token and verify_session_token in src/token_auth.py "
                "for kid HMAC rotation exp and leeway"
            ),
            "files": {
                "src/__init__.py": "",
                "src/token_auth.py": (
                    '"""Session token sign_session_token verify_session_token HMAC kid '
                    'rotation exp leeway."""\n\n'
                    "def sign_session_token(sub: str, exp: int, kid: str, "
                    "keys: dict[str, str]) -> str:\n"
                    "    return f'{kid}:{sub}:{exp}:bad'\n\n"
                    "def verify_session_token(token: str, keys: dict[str, str], now: int, "
                    "leeway: int = 5) -> str:\n"
                    "    return token.split(':')[1]\n"
                ),
                "src/keyring.py": "ACTIVE_KID = 'k2'\n",
                "src/audit.py": "def log_auth_event(sub: str) -> None:\n    pass\n",
                "src/middleware.py": "HEADER_NAME = 'X-Session-Token'\n",
                "tests/test_tc12.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.token_auth import sign_session_token, verify_session_token\n\n"
                    "def test_hmac_kid_rotation_and_leeway():\n"
                    "    keys = {'k1': 'old-secret', 'k2': 'new-secret'}\n"
                    "    tok = sign_session_token('alice', exp=100, kid='k1', keys=keys)\n"
                    "    assert verify_session_token(tok, keys, now=103, leeway=5) == 'alice'\n"
                    "    with pytest.raises(ValueError):\n"
                    "        verify_session_token(tok, keys, now=106, leeway=5)\n"
                    "    tampered = tok[:-1] + ('0' if tok[-1] != '0' else '1')\n"
                    "    with pytest.raises(ValueError):\n"
                    "        verify_session_token(tampered, keys, now=90)\n"
                ),
            },
            "solution_files": {
                "src/token_auth.py": (
                    "import hashlib\n"
                    "import hmac\n\n"
                    "def sign_session_token(sub: str, exp: int, kid: str, "
                    "keys: dict[str, str]) -> str:\n"
                    "    secret = keys[kid].encode('utf-8')\n"
                    "    payload = f'{kid}:{sub}:{exp}'\n"
                    "    sig = hmac.new(secret, payload.encode('utf-8'), "
                    "hashlib.sha256).hexdigest()\n"
                    "    return f'{payload}:{sig}'\n\n"
                    "def verify_session_token(token: str, keys: dict[str, str], now: int, "
                    "leeway: int = 5) -> str:\n"
                    "    parts = token.split(':')\n"
                    "    if len(parts) != 4:\n"
                    "        raise ValueError('malformed token')\n"
                    "    kid, sub, exp_s, sig = parts\n"
                    "    if kid not in keys:\n"
                    "        raise ValueError('unknown key id')\n"
                    "    payload = f'{kid}:{sub}:{exp_s}'\n"
                    "    expected = hmac.new(keys[kid].encode('utf-8'), "
                    "payload.encode('utf-8'), hashlib.sha256).hexdigest()\n"
                    "    if not hmac.compare_digest(sig, expected):\n"
                    "        raise ValueError('invalid signature')\n"
                    "    if now > int(exp_s) + leeway:\n"
                    "        raise ValueError('token expired')\n"
                    "    return sub\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_13",
            "turns": 11,
            "target": "src/scheduler.py",
            "pytest": "tests/test_tc13.py",
            "tier": "floor",
            "prompt": (
                "Fix TaskScheduler add_job mark_done and pop_ready in src/scheduler.py "
                "by priority FIFO and deps"
            ),
            "files": {
                "src/__init__.py": "",
                "src/scheduler.py": (
                    '"""TaskScheduler add_job mark_done pop_ready priority FIFO deps."""\n\n'
                    "class TaskScheduler:\n"
                    "    def __init__(self) -> None:\n"
                    "        self._jobs: list[str] = []\n\n"
                    "    def add_job(self, job_id: str, priority: int, "
                    "deps: list[str] | None = None) -> None:\n"
                    "        self._jobs.append(job_id)\n\n"
                    "    def mark_done(self, job_id: str) -> None:\n"
                    "        pass\n\n"
                    "    def pop_ready(self) -> str | None:\n"
                    "        return self._jobs.pop(0) if self._jobs else None\n"
                ),
                "src/queue.py": "MAX_DEPTH = 64\n",
                "src/worker.py": "WORKER_THREADS = 4\n",
                "src/backoff_cfg.py": "RETRY_LIMIT = 3\n",
                "tests/test_tc13.py": (
                    _py_test_preamble()
                    + "from src.scheduler import TaskScheduler\n\n"
                    "def test_priority_fifo_and_deps():\n"
                    "    s = TaskScheduler()\n"
                    "    s.add_job('db', priority=5)\n"
                    "    s.add_job('api', priority=10, deps=['db'])\n"
                    "    s.add_job('lint', priority=5)\n"
                    "    assert s.pop_ready() == 'db'\n"
                    "    assert s.pop_ready() == 'lint'\n"
                    "    assert s.pop_ready() is None\n"
                    "    s.mark_done('db')\n"
                    "    assert s.pop_ready() == 'api'\n"
                ),
            },
            "solution_files": {
                "src/scheduler.py": (
                    "class TaskScheduler:\n"
                    "    def __init__(self) -> None:\n"
                    "        self._pending: list[tuple[int, int, str, set[str]]] = []\n"
                    "        self._done: set[str] = set()\n"
                    "        self._seq = 0\n\n"
                    "    def add_job(self, job_id: str, priority: int, "
                    "deps: list[str] | None = None) -> None:\n"
                    "        self._pending.append((-priority, self._seq, job_id, "
                    "set(deps or [])))\n"
                    "        self._seq += 1\n\n"
                    "    def mark_done(self, job_id: str) -> None:\n"
                    "        self._done.add(job_id)\n\n"
                    "    def pop_ready(self) -> str | None:\n"
                    "        ready = [item for item in self._pending if "
                    "item[3].issubset(self._done)]\n"
                    "        if not ready:\n"
                    "            return None\n"
                    "        best = min(ready, key=lambda x: (x[0], x[1]))\n"
                    "        self._pending.remove(best)\n"
                    "        return best[2]\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_14",
            "turns": 8,
            "target": "src/url_norm.py",
            "pytest": "tests/test_tc14.py",
            "tier": "floor",
            "prompt": (
                "Fix normalize_request_url in src/url_norm.py for scheme host "
                "path traversal query ports"
            ),
            "files": {
                "src/__init__.py": "",
                "src/url_norm.py": (
                    '"""normalize_request_url scheme host path traversal query ports."""\n\n'
                    "def normalize_request_url(url: str) -> str:\n"
                    "    return url.strip()\n"
                ),
                "src/router.py": "ROUTES = ['/v1/items']\n",
                "src/headers.py": "HOP_HEADERS = {'connection'}\n",
                "src/mime.py": "JSON_MIME = 'application/json'\n",
                "tests/test_tc14.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.url_norm import normalize_request_url\n\n"
                    "def test_url_canonicalization_and_traversal():\n"
                    "    u = normalize_request_url('HTTPS://Example.COM:443/a/b/../c?z=1&a=2')\n"
                    "    assert u == 'https://example.com/a/c?a=2&z=1'\n"
                    "    with pytest.raises(ValueError):\n"
                    "        normalize_request_url('https://example.com/../../etc/passwd')\n"
                ),
            },
            "solution_files": {
                "src/url_norm.py": (
                    "import posixpath\n"
                    "from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse\n\n"
                    "def normalize_request_url(url: str) -> str:\n"
                    "    parsed = urlparse(url.strip())\n"
                    "    scheme = parsed.scheme.lower()\n"
                    "    host = (parsed.hostname or '').lower()\n"
                    "    port = parsed.port\n"
                    "    if (scheme == 'http' and port == 80) or (scheme == 'https' and "
                    "port == 443):\n"
                    "        port = None\n"
                    "    netloc = f'{host}:{port}' if port is not None else host\n"
                    "    parts = [p for p in parsed.path.split('/') if p and p != '.']\n"
                    "    stack: list[str] = []\n"
                    "    for seg in parts:\n"
                    "        if seg == '..':\n"
                    "            if not stack:\n"
                    "                raise ValueError('path traversal above root')\n"
                    "            stack.pop()\n"
                    "        else:\n"
                    "            stack.append(seg)\n"
                    "    norm_path = '/' + posixpath.join(*stack) if stack else '/'\n"
                    "    q = urlencode(sorted(parse_qsl(parsed.query, "
                    "keep_blank_values=True)))\n"
                    "    return urlunparse((scheme, netloc, norm_path, '', q, ''))\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_15",
            "turns": 12,
            "target": "src/window_agg.py",
            "pytest": "tests/test_tc15.py",
            "tier": "floor",
            "prompt": (
                "Implement RollingWindowStats record and summary in src/window_agg.py "
                "for window_sec count mean p95"
            ),
            "files": {
                "src/__init__.py": "",
                "src/window_agg.py": (
                    '"""RollingWindowStats record summary window_sec count mean p95."""\n\n'
                    "class RollingWindowStats:\n"
                    "    def __init__(self, window_sec: float) -> None:\n"
                    "        self.window_sec = window_sec\n"
                    "        self._events: list[tuple[float, float]] = []\n\n"
                    "    def record(self, ts: float, value: float) -> None:\n"
                    "        self._events.append((ts, value))\n\n"
                    "    def summary(self, now: float) -> dict[str, float]:\n"
                    "        return {'count': 0.0, 'mean': 0.0, 'p95': 0.0}\n"
                ),
                "src/emitter.py": "FLUSH_INTERVAL = 5.0\n",
                "src/schema.py": "FIELDS = ('ts', 'value')\n",
                "src/storage.py": "RETENTION_HOURS = 24\n",
                "tests/test_tc15.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.window_agg import RollingWindowStats\n\n"
                    "def test_rolling_window_eviction_and_p95():\n"
                    "    w = RollingWindowStats(window_sec=10.0)\n"
                    "    w.record(1.0, 999.0)\n"
                    "    for i, val in enumerate([10.0, 20.0, 30.0, 40.0, 100.0], start=15):\n"
                    "        w.record(float(i), val)\n"
                    "    s = w.summary(now=20.0)\n"
                    "    assert s['count'] == 5\n"
                    "    assert s['mean'] == pytest.approx(40.0)\n"
                    "    assert s['p95'] == pytest.approx(100.0)\n"
                ),
            },
            "solution_files": {
                "src/window_agg.py": (
                    "import math\n\n"
                    "class RollingWindowStats:\n"
                    "    def __init__(self, window_sec: float) -> None:\n"
                    "        self.window_sec = window_sec\n"
                    "        self._events: list[tuple[float, float]] = []\n\n"
                    "    def record(self, ts: float, value: float) -> None:\n"
                    "        self._events.append((ts, value))\n\n"
                    "    def summary(self, now: float) -> dict[str, float]:\n"
                    "        cutoff = now - self.window_sec\n"
                    "        self._events = [(t, v) for t, v in self._events if t >= cutoff]\n"
                    "        if not self._events:\n"
                    "            return {'count': 0.0, 'mean': 0.0, 'p95': 0.0}\n"
                    "        vals = sorted(v for _, v in self._events)\n"
                    "        mean = sum(vals) / len(vals)\n"
                    "        idx = max(0, min(len(vals) - 1, math.ceil(0.95 * len(vals)) - 1))\n"
                    "        return {'count': float(len(vals)), 'mean': mean, 'p95': vals[idx]}\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_16",
            "turns": 9,
            "target": "src/cfg_merge.py",
            "pytest": "tests/test_tc16.py",
            "tier": "floor",
            "prompt": (
                "Fix deep_merge_configs in src/cfg_merge.py for recursive dict "
                "base override merge"
            ),
            "files": {
                "src/__init__.py": "",
                "src/cfg_merge.py": (
                    '"""deep_merge_configs recursive dict base override merge."""\n\n'
                    "from typing import Any\n\n"
                    "def deep_merge_configs(base: dict[str, Any], override: dict[str, Any]) -> "
                    "dict[str, Any]:\n"
                    "    base.update(override)\n"
                    "    return base\n"
                ),
                "src/env_loader.py": "ENV_PREFIX = 'APP_'\n",
                "src/defaults.py": "BASE_LOG_LEVEL = 'INFO'\n",
                "src/validator.py": "STRICT_SCHEMA = True\n",
                "tests/test_tc16.py": (
                    _py_test_preamble()
                    + "from src.cfg_merge import deep_merge_configs\n\n"
                    "def test_non_mutating_recursive_merge():\n"
                    "    base = {'db': {'host': 'localhost', 'port': 5432}, 'tags': ['a']}\n"
                    "    ovr = {'db': {'port': 6432}, 'tags': ['b']}\n"
                    "    merged = deep_merge_configs(base, ovr)\n"
                    "    assert merged == {'db': {'host': 'localhost', 'port': 6432}, "
                    "'tags': ['b']}\n"
                    "    assert base['db']['port'] == 5432\n"
                ),
            },
            "solution_files": {
                "src/cfg_merge.py": (
                    "from copy import deepcopy\n"
                    "from typing import Any\n\n"
                    "def deep_merge_configs(base: dict[str, Any], override: dict[str, Any]) -> "
                    "dict[str, Any]:\n"
                    "    out = deepcopy(base)\n"
                    "    for key, val in override.items():\n"
                    "        if isinstance(val, dict) and isinstance(out.get(key), dict):\n"
                    "            out[key] = deep_merge_configs(out[key], val)\n"
                    "        else:\n"
                    "            out[key] = deepcopy(val)\n"
                    "    return out\n"
                ),
            },
        },
    ]

    knowledge_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_17",
            "turns": 10,
            "cartridge": "knowledge_vault",
            "category": "multi_doc_audit",
            "target": "notes/incident_auth.md",
            "output_file": "reports/q3_audit.md",
            "tier": "mid",
            "prompt": (
                "Audit notes/incident_auth.md JWKS auth downtime minutes and SLA credit"
            ),
            "files": {
                "notes/incident_auth.md": (
                    "# Q3 Auth Gateway Incident\n"
                    "## JWKS auth downtime minutes SLA credit us-east-1\n"
                    "Date: 2026-08-14. Region: us-east-1.\n"
                    "Total auth downtime was 18 minutes due to stale JWKS cache.\n"
                    "See [[db_failover]] and [[sla_policy]].\n"
                ),
                "notes/db_failover.md": (
                    "# DB Failover Log\n"
                    "Secondary replica lag added 4 minutes during failover.\n"
                    "Affected tenants: 14 enterprise accounts.\n"
                ),
                "notes/sla_policy.md": (
                    "# Enterprise SLA Policy\n"
                    "Incidents between 15 and 30 minutes trigger a 10% monthly SLA credit.\n"
                ),
                "notes/runbook_cache.md": "# Runbook Cache\nWarm JWKS before rotation.\n",
                "notes/postmortem_index.md": "# Index\nTrack [[incident_auth]] reviews.\n",
            },
            "solution_files": {
                "reports/q3_audit.md": (
                    "# Q3 Incident Synthesis Audit\n\n"
                    "## Summary\n"
                    "- Primary auth downtime: 18 minutes in us-east-1 "
                    "(`notes/incident_auth.md`, [[incident_auth]]).\n"
                    "- Failover propagation lag: 4 minutes impacting 14 enterprise accounts "
                    "(`notes/db_failover.md`, [[db_failover]]).\n"
                    "- Contractual SLA credit owed: 10% (`notes/sla_policy.md`, "
                    "[[sla_policy]]).\n"
                    "- **auth_downtime_minutes**: 18\n"
                    "- **sla_credit_percent**: 10\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "notes/incident_auth.md",
                    "notes/db_failover.md",
                    "notes/sla_policy.md",
                ],
                "expected_numeric_balances": {
                    "auth_downtime_minutes": 18,
                    "sla_credit_percent": 10,
                },
                "forbidden_leaks": ["PRIVATE_SIGNING_SALT"],
                "required_clauses": ["18 minutes", "10%"],
            },
        },
        {
            "id": "STRESS_TC_18",
            "turns": 11,
            "cartridge": "knowledge_vault",
            "category": "multi_doc_audit",
            "target": "notes/rfc_grpc.md",
            "output_file": "reports/migration_brief.md",
            "tier": "mid",
            "prompt": (
                "Review notes/rfc_grpc.md RFC gRPC Migration unary p99 latency budget edge PoPs"
            ),
            "files": {
                "notes/rfc_grpc.md": (
                    "# RFC gRPC Migration\n"
                    "## unary p99 latency budget edge PoPs\n"
                    "Target unary p99 latency budget is 45 ms across edge PoPs.\n"
                    "Coordinates with [[auth_gateway]] and [[deprecation_plan]].\n"
                ),
                "notes/auth_gateway.md": (
                    "# Auth Gateway mTLS\n"
                    "mTLS rotation cadence is every 72 hours across 32 edge nodes.\n"
                ),
                "notes/deprecation_plan.md": (
                    "# REST v1 Deprecation\n"
                    "Sunset window is 90 days post GA cutover.\n"
                ),
                "notes/telemetry_spec.md": "# Telemetry\nOTLP export over gRPC.\n",
                "notes/glossary.md": "# Glossary\nStandard service terms.\n",
            },
            "solution_files": {
                "reports/migration_brief.md": (
                    "# gRPC Edge Migration Brief\n\n"
                    "- Target unary p99 latency budget: 45 ms (`notes/rfc_grpc.md`, "
                    "[[rfc_grpc]]).\n"
                    "- Auth gateway mTLS rotation: 72 hours across 32 edge nodes "
                    "(`notes/auth_gateway.md`, [[auth_gateway]]).\n"
                    "- REST v1 deprecation sunset window: 90 days "
                    "(`notes/deprecation_plan.md`, [[deprecation_plan]]).\n"
                    "- **p99_budget_ms**: 45\n"
                    "- **sunset_days**: 90\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "notes/rfc_grpc.md",
                    "notes/auth_gateway.md",
                    "notes/deprecation_plan.md",
                ],
                "expected_numeric_balances": {
                    "p99_budget_ms": 45,
                    "sunset_days": 90,
                },
                "forbidden_leaks": ["INTERNAL_ROOT_CA_KEY"],
                "required_clauses": ["45 ms", "90 days"],
            },
        },
        {
            "id": "STRESS_TC_19",
            "turns": 8,
            "cartridge": "financial_ops",
            "category": "financial_reconciliation",
            "target": "ledger/treasury_policy.md",
            "output_file": "reports/clearing_recon.md",
            "tier": "mid",
            "prompt": (
                "Reconcile ledger/treasury_policy.md Treasury Intercompany Clearing Policy "
                "Debit Credit FX rates"
            ),
            "files": {
                "ledger/treasury_policy.md": (
                    "# Treasury Intercompany Clearing Policy\n"
                    "## Debit Credit FX rates clearing balance\n"
                    "Clearing account must balance Debit and Credit entries to $0.00 net.\n"
                    "Spot EUR->USD policy rate is published in [[fx_rates]].\n"
                ),
                "ledger/fx_rates.md": (
                    "# Quarter Close FX Rates\n"
                    "EUR/USD closing spot rate = 1.10.\n"
                    "EUR 20,000.00 translates to $22,000.00 USD.\n"
                ),
                "ledger/cash_journal.csv": (
                    "entry_id,account,debit_usd,credit_usd\n"
                    "J-101,Intercompany Cash,22000.00,0.00\n"
                    "J-102,Due From Subsidiary,0.00,22000.00\n"
                ),
                "ledger/clearing_ar.csv": "invoice,usd\nINV-9,22000.00\n",
                "ledger/notes.md": "# Notes\nVerify USD 2-decimal precision.\n",
            },
            "solution_files": {
                "reports/clearing_recon.md": (
                    "# Intercompany Clearing Reconciliation\n\n"
                    "Sources: `ledger/treasury_policy.md`, `ledger/fx_rates.md`, "
                    "`ledger/cash_journal.csv`.\n\n"
                    "- Translated EUR clearing amount: $22,000.00\n"
                    "- Debit: $22,000.00 (Intercompany Cash)\n"
                    "- Credit: $22,000.00 (Due From Subsidiary)\n"
                    "- Net Clearing Variance: $0.00\n"
                    "- **clearing_usd**: $22,000.00\n"
                    "- **net_variance_usd**: $0.00\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "ledger/treasury_policy.md",
                    "ledger/fx_rates.md",
                    "ledger/cash_journal.csv",
                ],
                "expected_numeric_balances": {
                    "clearing_usd": 22000.00,
                    "net_variance_usd": 0.00,
                },
                "forbidden_leaks": ["CORPORATE_TAX_EIN"],
                "required_clauses": ["$22,000.00", "$0.00"],
            },
        },
        {
            "id": "STRESS_TC_20",
            "turns": 12,
            "cartridge": "legal_redline",
            "category": "policy_compliance",
            "target": "contracts/playbook_rules.md",
            "output_file": "reports/dpa_redline_memo.md",
            "tier": "mid",
            "prompt": (
                "Review contracts/playbook_rules.md Standard Legal Playbook Liability Cap "
                "Breach Notice Governing Law"
            ),
            "files": {
                "contracts/playbook_rules.md": (
                    "# Standard Legal Playbook\n"
                    "## Section 1.1 Liability Cap\n"
                    "Aggregate cap shall not exceed 12 months fees paid.\n"
                    "## Section 1.2 Breach Notice\n"
                    "Security breach notification must occur within 48 hours.\n"
                    "## Section 1.3 Governing Law\n"
                    "Disputes governed by State of Delaware law.\n"
                ),
                "contracts/dpa_schedule.md": (
                    "# Vendor DPA Schedule\n"
                    "## Section 2.1 Notification Window\n"
                    "Draft currently proposes 72 hours for breach notice.\n"
                ),
                "contracts/msa_draft.md": (
                    "# MSA Draft\n"
                    "## Section 3.1 Indemnification\n"
                    "Draft proposes uncapped general indemnity.\n"
                ),
                "contracts/subprocessors.md": "# Subprocessors\nAWS us-east-1.\n",
            },
            "solution_files": {
                "reports/dpa_redline_memo.md": (
                    "# DPA and MSA Redline Compliance Memo\n\n"
                    "Reviewed `contracts/playbook_rules.md`, `contracts/dpa_schedule.md`, "
                    "and `contracts/msa_draft.md`.\n\n"
                    "## Section 1.1 Liability Cap\n"
                    "Reject uncapped indemnity and restore the 12 months "
                    "aggregate liability cap per Section 1.1.\n\n"
                    "## Section 1.2 Breach Notice\n"
                    "Reduce draft notification window down to 48 hours per Section 1.2.\n\n"
                    "## Section 1.3 Governing Law\n"
                    "Confirm Governing Law as Delaware per Section 1.3.\n\n"
                    "- **cap_months**: 12\n"
                    "- **breach_notice_hours**: 48\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "contracts/playbook_rules.md",
                    "contracts/dpa_schedule.md",
                    "contracts/msa_draft.md",
                ],
                "expected_numeric_balances": {
                    "cap_months": 12,
                    "breach_notice_hours": 48,
                },
                "forbidden_leaks": ["ATTORNEY_CLIENT_PRIVILEGE_KEY"],
                "required_clauses": ["Liability Cap", "48 hours", "Delaware"],
            },
        },
    ]

    out: list[dict[str, Any]] = []
    idx = 11
    for s in coding_specs:
        provider = _provider_for(idx)
        target = s["target"]
        out.append(
            {
                "task_id": s["id"],
                "suite": SUITE_NAME,
                "task_kind": "coding",
                "category": "kv_cache_and_compact_reset",
                "cartridge": "software_engineering",
                "prompt": s["prompt"],
                "files": s["files"],
                "flawed_draft_files": {},
                "solution_files": s["solution_files"],
                "expected_touched_files": [target],
                "pytest_target": s["pytest"],
                "expected_tier": s["tier"],
                "simulates_one_strike_promotion": False,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=s["tier"],
                    num_turns=s["turns"],
                    classifier_driver="onnx_local",
                    simulates_one_strike=False,
                    compact_reset_turn=7,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_gate1_turns": _build_turn_assertions(),
                    "assert_pass0_recall": {
                        "expected_any": [target],
                        "top_k": 10,
                    },
                },
            }
        )
        idx += 1

    for ks in knowledge_specs:
        provider = _provider_for(idx)
        target = ks["target"]
        out.append(
            {
                "task_id": ks["id"],
                "suite": SUITE_NAME,
                "task_kind": "knowledge",
                "category": "kv_cache_and_compact_reset",
                "cartridge": ks["cartridge"],
                "prompt": ks["prompt"],
                "files": ks["files"],
                "flawed_draft_files": {},
                "solution_files": ks["solution_files"],
                "output_file": ks["output_file"],
                "expected_touched_files": [ks["output_file"]],
                "ground_truth": ks["ground_truth"],
                "expected_tier": ks["tier"],
                "simulates_one_strike_promotion": False,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=ks["tier"],
                    num_turns=ks["turns"],
                    classifier_driver="onnx_local",
                    simulates_one_strike=False,
                    compact_reset_turn=7,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_gate1_turns": _build_turn_assertions(),
                    "assert_pass0_recall": {
                        "expected_any": [target],
                        "top_k": 10,
                    },
                },
            }
        )
        idx += 1

    return out


# ---------------------------------------------------------------------------
# Category 3: STRESS_TC_21 .. STRESS_TC_28 — multi_clause_completeness
# ---------------------------------------------------------------------------


def _build_multi_clause_tasks() -> list[dict[str, Any]]:
    coding_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_21",
            "target": "src/webhook_sig.py",
            "pytest": "tests/test_tc21.py",
            "tier": "mid",
            "prompt": (
                "Implement webhook signature verification in src/webhook_sig.py:\n"
                "1. Implement sign_webhook_payload computing HMAC SHA256 hex digest\n"
                "2. Implement parse_signature_header extracting timestamp and v1 signature\n"
                "3. Implement verify_replay_window rejecting stale timestamps over max_age\n"
                "4. Implement compare_digest_safe using constant-time hmac comparison\n"
                "5. Implement build_signed_headers returning X-Webhook-Signature header\n"
                "6. Implement verify_webhook raising WebhookSignatureError on invalid inputs\n"
            ),
            "files": {
                "src/__init__.py": "",
                "src/webhook_sig.py": (
                    "class WebhookSignatureError(ValueError):\n"
                    "    pass\n\n"
                    "def verify_webhook(body: bytes, header: str, secret: str, "
                    "now: int) -> bool:\n"
                    "    return False\n"
                ),
                "tests/test_tc21.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.webhook_sig import (\n"
                    "    WebhookSignatureError,\n"
                    "    build_signed_headers,\n"
                    "    compare_digest_safe,\n"
                    "    parse_signature_header,\n"
                    "    sign_webhook_payload,\n"
                    "    verify_replay_window,\n"
                    "    verify_webhook,\n"
                    ")\n\n"
                    "def test_all_six_webhook_clauses():\n"
                    "    headers = build_signed_headers(b'{}', secret='sec', ts=100)\n"
                    "    sig_hdr = headers['X-Webhook-Signature']\n"
                    "    ts, v1 = parse_signature_header(sig_hdr)\n"
                    "    assert ts == 100\n"
                    "    assert v1 == sign_webhook_payload(b'{}', ts=100, secret='sec')\n"
                    "    assert compare_digest_safe(v1, v1) is True\n"
                    "    assert verify_replay_window(100, now=150, max_age=300) is True\n"
                    "    assert verify_webhook(b'{}', sig_hdr, 'sec', now=150) is True\n"
                    "    with pytest.raises(WebhookSignatureError):\n"
                    "        verify_webhook(b'{}', sig_hdr, 'sec', now=900)\n"
                ),
            },
            # Missing requirements 3 (verify_replay_window) & 5 (build_signed_headers)
            "flawed_draft_files": {
                "src/webhook_sig.py": (
                    "import hashlib\n"
                    "import hmac\n\n"
                    "class WebhookSignatureError(ValueError):\n"
                    "    pass\n\n"
                    "def sign_webhook_payload(body: bytes, ts: int, secret: str) -> str:\n"
                    "    msg = f'{ts}.'.encode('utf-8') + body\n"
                    "    return hmac.new(secret.encode('utf-8'), msg, "
                    "hashlib.sha256).hexdigest()\n\n"
                    "def parse_signature_header(header: str) -> tuple[int, str]:\n"
                    "    parts = dict(p.split('=', 1) for p in header.split(','))\n"
                    "    return int(parts['t']), parts['v1']\n\n"
                    "def compare_digest_safe(a: str, b: str) -> bool:\n"
                    "    return hmac.compare_digest(a, b)\n\n"
                    "def verify_webhook(body: bytes, header: str, secret: str, "
                    "now: int) -> bool:\n"
                    "    return False\n"
                ),
            },
            "solution_files": {
                "src/webhook_sig.py": (
                    '"""Implement webhook signature verification helpers."""\n\n'
                    "import hashlib\n"
                    "import hmac\n\n"
                    "class WebhookSignatureError(ValueError):\n"
                    "    pass\n\n"
                    "def sign_webhook_payload(body: bytes, ts: int, secret: str) -> str:\n"
                    '    """Compute HMAC SHA256 hex digest payload signature."""\n'
                    "    msg = f'{ts}.'.encode('utf-8') + body\n"
                    "    return hmac.new(secret.encode('utf-8'), msg, "
                    "hashlib.sha256).hexdigest()\n\n"
                    "def parse_signature_header(header: str) -> tuple[int, str]:\n"
                    '    """Extract timestamp and v1 signature from header."""\n'
                    "    try:\n"
                    "        parts = dict(p.split('=', 1) for p in header.split(','))\n"
                    "        return int(parts['t']), parts['v1']\n"
                    "    except (ValueError, KeyError) as exc:\n"
                    "        raise WebhookSignatureError('bad header') from exc\n\n"
                    "def verify_replay_window(ts: int, now: int, "
                    "max_age: int = 300) -> bool:\n"
                    '    """Reject stale timestamps over max_age replay window."""\n'
                    "    return abs(now - ts) <= max_age\n\n"
                    "def compare_digest_safe(a: str, b: str) -> bool:\n"
                    '    """Constant-time hmac digest comparison."""\n'
                    "    return hmac.compare_digest(a, b)\n\n"
                    "def build_signed_headers(body: bytes, secret: str, ts: int) -> "
                    "dict[str, str]:\n"
                    '    """Return X-Webhook-Signature signed headers."""\n'
                    "    v1 = sign_webhook_payload(body, ts=ts, secret=secret)\n"
                    "    return {'X-Webhook-Signature': f't={ts},v1={v1}'}\n\n"
                    "def verify_webhook(body: bytes, header: str, secret: str, "
                    "now: int, max_age: int = 300) -> bool:\n"
                    '    """Verify webhook and raise WebhookSignatureError on invalid inputs."""\n'
                    "    ts, v1 = parse_signature_header(header)\n"
                    "    if not verify_replay_window(ts, now=now, max_age=max_age):\n"
                    "        raise WebhookSignatureError('stale timestamp')\n"
                    "    expected = sign_webhook_payload(body, ts=ts, secret=secret)\n"
                    "    if not compare_digest_safe(v1, expected):\n"
                    "        raise WebhookSignatureError('signature mismatch')\n"
                    "    return True\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_22",
            "target": "src/retry_policy.py",
            "pytest": "tests/test_tc22.py",
            "tier": "mid",
            "prompt": (
                "Implement HTTP retry engine in src/retry_policy.py:\n"
                "1. Implement classify_status_code returning True for 429 and 5xx codes\n"
                "2. Implement parse_retry_after parsing integer seconds or returning None\n"
                "3. Implement compute_backoff_ms applying capped exponential growth\n"
                "4. Implement should_retry_method restricting retries to idempotent verbs\n"
                "5. Implement track_retry_budget enforcing max_total_delay_ms limit\n"
                "6. Implement decide_next_retry returning wait_ms when retry is allowed\n"
            ),
            "files": {
                "src/__init__.py": "",
                "src/retry_policy.py": (
                    "def decide_next_retry(method: str, status: int, attempt: int, "
                    "spent_ms: int) -> int | None:\n"
                    "    return None\n"
                ),
                "tests/test_tc22.py": (
                    _py_test_preamble()
                    + "from src.retry_policy import (\n"
                    "    classify_status_code,\n"
                    "    compute_backoff_ms,\n"
                    "    decide_next_retry,\n"
                    "    parse_retry_after,\n"
                    "    should_retry_method,\n"
                    "    track_retry_budget,\n"
                    ")\n\n"
                    "def test_all_six_retry_clauses():\n"
                    "    assert classify_status_code(503) is True\n"
                    "    assert classify_status_code(400) is False\n"
                    "    assert parse_retry_after('12') == 12\n"
                    "    assert compute_backoff_ms(2, base_ms=100, cap_ms=1000) == 400\n"
                    "    assert should_retry_method('GET') is True\n"
                    "    assert should_retry_method('POST') is False\n"
                    "    assert track_retry_budget(200, 400, max_total_delay_ms=1000) is True\n"
                    "    assert decide_next_retry('GET', 429, attempt=1, spent_ms=100) == 200\n"
                ),
            },
            # Missing parse_retry_after, should_retry_method, track_retry_budget
            "flawed_draft_files": {
                "src/retry_policy.py": (
                    "def classify_status_code(status: int) -> bool:\n"
                    "    return status == 429 or 500 <= status < 600\n\n"
                    "def compute_backoff_ms(attempt: int, base_ms: int = 100, "
                    "cap_ms: int = 5000) -> int:\n"
                    "    return min(cap_ms, base_ms * (2 ** attempt))\n\n"
                    "def decide_next_retry(method: str, status: int, attempt: int, "
                    "spent_ms: int) -> int | None:\n"
                    "    if classify_status_code(status):\n"
                    "        return compute_backoff_ms(attempt)\n"
                    "    return None\n"
                ),
            },
            "solution_files": {
                "src/retry_policy.py": (
                    '"""Implement HTTP retry engine policy."""\n\n'
                    "IDEMPOTENT_VERBS = {'GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS'}\n\n"
                    "def classify_status_code(status: int) -> bool:\n"
                    '    """Return True for 429 and 5xx status codes."""\n'
                    "    return status == 429 or 500 <= status < 600\n\n"
                    "def parse_retry_after(header_val: str | None) -> int | None:\n"
                    '    """Parse integer seconds header or return None."""\n'
                    "    if header_val is not None and header_val.strip().isdigit():\n"
                    "        return int(header_val.strip())\n"
                    "    return None\n\n"
                    "def compute_backoff_ms(attempt: int, base_ms: int = 100, "
                    "cap_ms: int = 5000) -> int:\n"
                    '    """Apply capped exponential backoff growth in ms."""\n'
                    "    return min(cap_ms, base_ms * (2 ** max(0, attempt)))\n\n"
                    "def should_retry_method(method: str) -> bool:\n"
                    '    """Restrict retries to idempotent HTTP verbs."""\n'
                    "    return method.upper() in IDEMPOTENT_VERBS\n\n"
                    "def track_retry_budget(spent_ms: int, next_ms: int, "
                    "max_total_delay_ms: int = 10000) -> bool:\n"
                    '    """Enforce max_total_delay_ms retry budget limit."""\n'
                    "    return spent_ms + next_ms <= max_total_delay_ms\n\n"
                    "def decide_next_retry(\n"
                    "    method: str,\n"
                    "    status: int,\n"
                    "    attempt: int,\n"
                    "    spent_ms: int,\n"
                    "    retry_after: str | None = None,\n"
                    "    max_total_delay_ms: int = 10000,\n"
                    ") -> int | None:\n"
                    '    """Return wait_ms when next retry is allowed."""\n'
                    "    if not should_retry_method(method) or not "
                    "classify_status_code(status):\n"
                    "        return None\n"
                    "    ra = parse_retry_after(retry_after)\n"
                    "    wait_ms = ra * 1000 if ra is not None else "
                    "compute_backoff_ms(attempt)\n"
                    "    if not track_retry_budget(spent_ms, wait_ms, max_total_delay_ms):\n"
                    "        return None\n"
                    "    return wait_ms\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_23",
            "target": "src/pagination.py",
            "pytest": "tests/test_tc23.py",
            "tier": "mid",
            "prompt": (
                "Build opaque cursor pagination helpers in src/pagination.py:\n"
                "1. Implement encode_cursor serializing offset and sort_key via base64\n"
                "2. Implement decode_cursor validating payload checksum and returning fields\n"
                "3. Implement clamp_page_size bounding requested limit between 1 and max_limit\n"
                "4. Implement slice_page returning items slice and next_cursor token\n"
                "5. Implement build_page_envelope returning items, next_cursor, and has_more\n"
            ),
            "files": {
                "src/__init__.py": "",
                "src/pagination.py": (
                    "def build_page_envelope(items: list[str], cursor: str | None, "
                    "limit: int) -> dict[str, object]:\n"
                    "    return {'items': [], 'next_cursor': None, 'has_more': False}\n"
                ),
                "tests/test_tc23.py": (
                    _py_test_preamble()
                    + "import pytest\n"
                    "from src.pagination import (\n"
                    "    build_page_envelope,\n"
                    "    clamp_page_size,\n"
                    "    decode_cursor,\n"
                    "    encode_cursor,\n"
                    "    slice_page,\n"
                    ")\n\n"
                    "def test_all_five_pagination_clauses():\n"
                    "    tok = encode_cursor(offset=2, sort_key='id')\n"
                    "    assert decode_cursor(tok) == (2, 'id')\n"
                    "    with pytest.raises(ValueError):\n"
                    "        decode_cursor('corrupt-token')\n"
                    "    assert clamp_page_size(500, max_limit=50) == 50\n"
                    "    sl, nxt = slice_page(['a', 'b', 'c', 'd'], offset=1, limit=2)\n"
                    "    assert sl == ['b', 'c'] and nxt is not None\n"
                    "    env = build_page_envelope(['a', 'b', 'c'], cursor=None, limit=2)\n"
                    "    assert env['items'] == ['a', 'b'] and env['has_more'] is True\n"
                ),
            },
            # Missing clamp_page_size and build_page_envelope logic
            "flawed_draft_files": {
                "src/pagination.py": (
                    "import base64\n"
                    "import json\n\n"
                    "def encode_cursor(offset: int, sort_key: str) -> str:\n"
                    "    raw = json.dumps({'offset': offset, 'sort_key': sort_key})\n"
                    "    return base64.urlsafe_b64encode(raw.encode('utf-8')).decode('ascii')\n\n"
                    "def decode_cursor(token: str) -> tuple[int, str]:\n"
                    "    data = json.loads(base64.urlsafe_b64decode(token).decode('utf-8'))\n"
                    "    return int(data['offset']), str(data['sort_key'])\n\n"
                    "def slice_page(items: list[str], offset: int, limit: int) -> "
                    "tuple[list[str], str | None]:\n"
                    "    return items[offset:offset + limit], None\n"
                ),
            },
            "solution_files": {
                "src/pagination.py": (
                    '"""Opaque cursor pagination helpers."""\n\n'
                    "import base64\n"
                    "import hashlib\n"
                    "import json\n\n"
                    "def _checksum(offset: int, sort_key: str) -> str:\n"
                    "    return hashlib.sha256(f'{offset}:{sort_key}'.encode()).hexdigest()[:8]\n\n"
                    "def encode_cursor(offset: int, sort_key: str) -> str:\n"
                    '    """Serialize offset and sort_key via base64."""\n'
                    "    payload = {\n"
                    "        'offset': offset,\n"
                    "        'sort_key': sort_key,\n"
                    "        'sig': _checksum(offset, sort_key),\n"
                    "    }\n"
                    "    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')\n"
                    "    return base64.urlsafe_b64encode(raw).decode('ascii')\n\n"
                    "def decode_cursor(token: str) -> tuple[int, str]:\n"
                    '    """Validate payload checksum and return fields."""\n'
                    "    try:\n"
                    "        b = base64.urlsafe_b64decode(token.encode('ascii'))\n"
                    "        raw = b.decode('utf-8')\n"
                    "        data = json.loads(raw)\n"
                    "        offset = int(data['offset'])\n"
                    "        sort_key = str(data['sort_key'])\n"
                    "        if data.get('sig') != _checksum(offset, sort_key):\n"
                    "            raise ValueError('cursor checksum mismatch')\n"
                    "        return offset, sort_key\n"
                    "    except Exception as exc:\n"
                    "        raise ValueError('invalid cursor') from exc\n\n"
                    "def clamp_page_size(limit: int, max_limit: int = 100) -> int:\n"
                    '    """Bound requested limit between 1 and max_limit."""\n'
                    "    return max(1, min(max_limit, limit))\n\n"
                    "def slice_page(\n"
                    "    items: list[str],\n"
                    "    offset: int,\n"
                    "    limit: int,\n"
                    "    sort_key: str = 'id',\n"
                    ") -> tuple[list[str], str | None]:\n"
                    '    """Return items slice and next_cursor token."""\n'
                    "    end = offset + limit\n"
                    "    chunk = items[offset:end]\n"
                    "    nxt = encode_cursor(end, sort_key) if end < len(items) else None\n"
                    "    return chunk, nxt\n\n"
                    "def build_page_envelope(items: list[str], cursor: str | None, "
                    "limit: int) -> dict[str, object]:\n"
                    '    """Return items, next_cursor, and has_more envelope."""\n'
                    "    offset, sort_key = decode_cursor(cursor) if cursor else (0, 'id')\n"
                    "    safe_limit = clamp_page_size(limit)\n"
                    "    chunk, nxt = slice_page(items, offset, safe_limit, sort_key=sort_key)\n"
                    "    return {'items': chunk, 'next_cursor': nxt, "
                    "'has_more': nxt is not None}\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_24",
            "target": "src/feature_flags.py",
            "pytest": "tests/test_tc24.py",
            "tier": "mid",
            "prompt": (
                "Implement deterministic feature flag evaluation in src/feature_flags.py:\n"
                "1. Implement hash_bucket computing stable percentage bucket 0 to 99\n"
                "2. Implement match_explicit_allowlist checking user_id override rules\n"
                "3. Implement match_denylist ensuring excluded users always receive False\n"
                "4. Implement check_rollout_percent evaluating user hash_bucket below pct\n"
                "5. Implement evaluate_attribute_rules matching tenant and plan filters\n"
                "6. Implement is_feature_enabled combining deny, allow, attributes, and pct\n"
                "7. Implement describe_decision returning matched rule reason string\n"
            ),
            "files": {
                "src/__init__.py": "",
                "src/feature_flags.py": (
                    "def is_feature_enabled(flag: dict[str, object], "
                    "ctx: dict[str, str]) -> bool:\n"
                    "    return False\n"
                ),
                "tests/test_tc24.py": (
                    _py_test_preamble()
                    + "from src.feature_flags import (\n"
                    "    check_rollout_percent,\n"
                    "    describe_decision,\n"
                    "    evaluate_attribute_rules,\n"
                    "    hash_bucket,\n"
                    "    is_feature_enabled,\n"
                    "    match_denylist,\n"
                    "    match_explicit_allowlist,\n"
                    ")\n\n"
                    "def test_all_seven_feature_flag_clauses():\n"
                    "    assert 0 <= hash_bucket('flagA', 'u1') < 100\n"
                    "    assert match_denylist('u9', {'u9'}) is True\n"
                    "    assert match_explicit_allowlist('u1', {'u1'}) is True\n"
                    "    assert check_rollout_percent('flagA', 'u1', 100) is True\n"
                    "    assert evaluate_attribute_rules({'plan': 'pro'}, "
                    "{'plan': 'pro'}) is True\n"
                    "    flag = {'name': 'f1', 'deny': ['u9'], 'allow': ['u2'], "
                    "'attrs': {'plan': 'pro'}, 'percent': 0}\n"
                    "    assert is_feature_enabled(flag, {'user_id': 'u9', 'plan': 'pro'}) "
                    "is False\n"
                    "    assert is_feature_enabled(flag, {'user_id': 'u2', 'plan': 'free'}) "
                    "is True\n"
                    "    assert describe_decision(flag, {'user_id': 'u2'}) == 'allowlist'\n"
                ),
            },
            # Missing evaluate_attribute_rules & describe_decision
            "flawed_draft_files": {
                "src/feature_flags.py": (
                    "import hashlib\n\n"
                    "def hash_bucket(flag_name: str, user_id: str) -> int:\n"
                    "    d = hashlib.sha256(f'{flag_name}:{user_id}'.encode()).hexdigest()\n"
                    "    return int(d[:8], 16) % 100\n\n"
                    "def match_explicit_allowlist(user_id: str, allow: set[str]) -> bool:\n"
                    "    return user_id in allow\n\n"
                    "def match_denylist(user_id: str, deny: set[str]) -> bool:\n"
                    "    return user_id in deny\n\n"
                    "def check_rollout_percent(flag_name: str, user_id: str, pct: int) -> "
                    "bool:\n"
                    "    return hash_bucket(flag_name, user_id) < pct\n\n"
                    "def is_feature_enabled(flag: dict[str, object], "
                    "ctx: dict[str, str]) -> bool:\n"
                    "    return False\n"
                ),
            },
            "solution_files": {
                "src/feature_flags.py": (
                    '"""Deterministic feature flag evaluation helpers."""\n\n'
                    "import hashlib\n\n"
                    "def hash_bucket(flag_name: str, user_id: str) -> int:\n"
                    '    """Compute stable percentage bucket 0 to 99."""\n'
                    "    d = hashlib.sha256(f'{flag_name}:{user_id}'.encode()).hexdigest()\n"
                    "    return int(d[:8], 16) % 100\n\n"
                    "def match_explicit_allowlist(user_id: str, allow: set[str]) -> bool:\n"
                    '    """Check user_id explicit allowlist override rules."""\n'
                    "    return user_id in allow\n\n"
                    "def match_denylist(user_id: str, deny: set[str]) -> bool:\n"
                    '    """Ensure excluded users in denylist receive False."""\n'
                    "    return user_id in deny\n\n"
                    "def check_rollout_percent(flag_name: str, user_id: str, pct: int) -> "
                    "bool:\n"
                    '    """Evaluate user hash_bucket below pct."""\n'
                    "    return hash_bucket(flag_name, user_id) < pct\n\n"
                    "def evaluate_attribute_rules(rules: dict[str, str], "
                    "ctx: dict[str, str]) -> bool:\n"
                    '    """Match tenant and plan attribute filters."""\n'
                    "    if not rules:\n"
                    "        return False\n"
                    "    return all(ctx.get(k) == v for k, v in rules.items())\n\n"
                    "def describe_decision(flag: dict[str, object], ctx: dict[str, str]) -> "
                    "str:\n"
                    '    """Return matched rule reason string."""\n'
                    "    uid = ctx.get('user_id', '')\n"
                    "    deny = set(flag.get('deny', []))  # type: ignore[arg-type]\n"
                    "    allow = set(flag.get('allow', []))  # type: ignore[arg-type]\n"
                    "    attrs = dict(flag.get('attrs', {}))  # type: ignore[arg-type]\n"
                    "    pct = int(flag.get('percent', 0))  # type: ignore[arg-type]\n"
                    "    name = str(flag.get('name', ''))\n"
                    "    if match_denylist(uid, deny):\n"
                    "        return 'denylist'\n"
                    "    if match_explicit_allowlist(uid, allow):\n"
                    "        return 'allowlist'\n"
                    "    if evaluate_attribute_rules(attrs, ctx):\n"
                    "        return 'attributes'\n"
                    "    if check_rollout_percent(name, uid, pct):\n"
                    "        return 'rollout'\n"
                    "    return 'off'\n\n"
                    "def is_feature_enabled(flag: dict[str, object], "
                    "ctx: dict[str, str]) -> bool:\n"
                    '    """Combine deny, allow, attributes, and pct rules."""\n'
                    "    return describe_decision(flag, ctx) in {\n"
                    "        'allowlist',\n"
                    "        'attributes',\n"
                    "        'rollout',\n"
                    "    }\n"
                ),
            },
        },
    ]

    knowledge_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_25",
            "cartridge": "knowledge_vault",
            "category": "multi_doc_audit",
            "target": "notes/shard_capacity.md",
            "output_file": "reports/shard_plan.md",
            "tier": "mid",
            "prompt": (
                "Synthesize reports/shard_plan.md from vault notes ensuring all 5 clauses:\n"
                "1. State total primary shard target 64 from notes/shard_capacity.md\n"
                "2. Document 3x replication factor from notes/replication_tier.md\n"
                "3. Specify 120 GB per-node SSD headroom from notes/disk_limits.md\n"
                "4. Specify 15 minute rebalance throttle window from notes/disk_limits.md\n"
                "5. Link valid wikilinks [[shard_capacity]], [[replication_tier]], "
                "[[disk_limits]]\n"
            ),
            "files": {
                "notes/shard_capacity.md": "# Shard Capacity\nPrimary shard count: 64.\n",
                "notes/replication_tier.md": "# Replication Tier\nFactor: 3x replicas.\n",
                "notes/disk_limits.md": (
                    "# Disk Limits\nReserve 120 GB SSD headroom; throttle 15 minute rebalance.\n"
                ),
            },
            # Missing clauses 3 & 4 (120 GB headroom and 15 minute throttle)
            "flawed_draft_files": {
                "reports/shard_plan.md": (
                    "# Shard Plan Draft\n"
                    "- Total primary shards: 64 (notes/shard_capacity.md, [[shard_capacity]]).\n"
                    "- Replication factor: 3x (notes/replication_tier.md, "
                    "[[replication_tier]]).\n"
                ),
            },
            "solution_files": {
                "reports/shard_plan.md": (
                    "# Cluster Shard Rebalance Plan from Vault Notes\n\n"
                    "Synthesize reports/shard_plan.md covering all 5 clauses:\n\n"
                    "1. Total primary shard target: 64 (`notes/shard_capacity.md`).\n"
                    "2. Cluster replication factor: 3x replicas (`notes/replication_tier.md`).\n"
                    "3. Per-node SSD headroom: 120 GB (`notes/disk_limits.md`).\n"
                    "4. Rebalance throttle window: 15 minute cadence (`notes/disk_limits.md`).\n"
                    "5. Link valid wikilinks: [[shard_capacity]], [[replication_tier]], "
                    "[[disk_limits]].\n"
                    "- **primary_shards**: 64\n"
                    "- **ssd_headroom_gb**: 120\n"
                    "- **rebalance_minutes**: 15\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "notes/shard_capacity.md",
                    "notes/replication_tier.md",
                    "notes/disk_limits.md",
                ],
                "expected_numeric_balances": {
                    "primary_shards": 64,
                    "ssd_headroom_gb": 120,
                    "rebalance_minutes": 15,
                },
                "forbidden_leaks": ["PRIVATE_CLUSTER_SECRET"],
                "required_clauses": ["64", "3x", "120 GB", "15 minute"],
            },
        },
        {
            "id": "STRESS_TC_26",
            "cartridge": "financial_ops",
            "category": "financial_reconciliation",
            "target": "ledger/ap_cutoff.md",
            "output_file": "reports/q4_accrual_recon.md",
            "tier": "mid",
            "prompt": (
                "Produce reports/q4_accrual_recon.md satisfying all 6 accounting clauses:\n"
                "1. Record Cloud Infrastructure Accrual Debit of $14,500.00\n"
                "2. Record Accrued Liabilities Credit of $14,500.00\n"
                "3. Record Hardware Lease Accrual Debit of $3,250.50\n"
                "4. Record Lease Payable Credit of $3,250.50\n"
                "5. Verify Total Debits and Total Credits balance cleanly at $17,750.50\n"
                "6. Cite ledger/accrual_schedule.csv and ledger/ap_cutoff.md\n"
            ),
            "files": {
                "ledger/accrual_schedule.csv": (
                    "item,debit,credit\n"
                    "Cloud Infrastructure Accrual,14500.00,0.00\n"
                    "Accrued Liabilities,0.00,14500.00\n"
                    "Hardware Lease Accrual,3250.50,0.00\n"
                    "Lease Payable,0.00,3250.50\n"
                ),
                "ledger/ap_cutoff.md": (
                    "# AP Quarter Cutoff\nVerify double-entry Debits equal Credits at close.\n"
                ),
            },
            # Missing Hardware Lease Accrual debit/credit and fails balance/currency check
            "flawed_draft_files": {
                "reports/q4_accrual_recon.md": (
                    "# Q4 Accrual Draft\n"
                    "1. Cloud Infrastructure Accrual Debit: $14,500.00\n"
                    "2. Accrued Liabilities Credit: $14,500.00\n"
                    "3. Unbalanced extra Debit: $3,250.50\n"
                ),
            },
            "solution_files": {
                "reports/q4_accrual_recon.md": (
                    "# Q4 Accrual Reconciliation Report\n\n"
                    "Sources: `ledger/accrual_schedule.csv` and `ledger/ap_cutoff.md`.\n\n"
                    "1. Cloud Infrastructure Accrual Debit: $14,500.00\n"
                    "2. Accrued Liabilities Credit: $14,500.00\n"
                    "3. Hardware Lease Accrual Debit: $3,250.50\n"
                    "4. Lease Payable Credit: $3,250.50\n"
                    "5. Balanced Total Debits and Total Credits: $17,750.50\n"
                    "- **cloud_accrual**: $14,500.00\n"
                    "- **lease_accrual**: $3,250.50\n"
                    "- **total_balanced**: $17,750.50\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "ledger/accrual_schedule.csv",
                    "ledger/ap_cutoff.md",
                ],
                "expected_numeric_balances": {
                    "cloud_accrual": 14500.00,
                    "lease_accrual": 3250.50,
                    "total_balanced": 17750.50,
                },
                "forbidden_leaks": ["PAYROLL_SSN_HASH"],
                "required_clauses": ["$14,500.00", "$3,250.50", "$17,750.50"],
            },
        },
        {
            "id": "STRESS_TC_27",
            "cartridge": "legal_redline",
            "category": "policy_compliance",
            "target": "contracts/redline_spec.md",
            "output_file": "reports/vendor_redline_final.md",
            "tier": "mid",
            "prompt": (
                "Draft reports/vendor_redline_final.md covering all 6 legal clauses:\n"
                "1. Include Section 4.1 Limitation of Liability capping exposure at $500,000\n"
                "2. Include Section 4.2 Confidentiality survival period of 5 years\n"
                "3. Include Section 4.3 Subprocessor notice period of 30 days\n"
                "4. Include Section 4.4 Data Deletion SLA within 15 days of termination\n"
                "5. Include Section 4.5 Governing Law specifying New York\n"
                "6. Cite contracts/redline_spec.md and contracts/msa_base.md\n"
            ),
            "files": {
                "contracts/redline_spec.md": (
                    "# Legal Redline Spec\n"
                    "## Section 4.1 Limitation of Liability\nCap at $500,000.\n"
                    "## Section 4.2 Confidentiality\n5 years survival.\n"
                    "## Section 4.3 Subprocessor\n30 days prior notice.\n"
                    "## Section 4.4 Data Deletion SLA\n15 days post termination.\n"
                    "## Section 4.5 Governing Law\nState of New York.\n"
                ),
                "contracts/msa_base.md": "# MSA Base\nBaseline commercial terms.\n",
            },
            # Missing Data Deletion SLA (Section 4.4) and Governing Law (Section 4.5)
            "flawed_draft_files": {
                "reports/vendor_redline_final.md": (
                    "# Draft Redline Memo\n"
                    "## Section 4.1 Limitation of Liability\n$500,000 cap.\n"
                    "## Section 4.2 Confidentiality\n5 years survival.\n"
                    "## Section 4.3 Subprocessor\n30 days notice.\n"
                ),
            },
            "solution_files": {
                "reports/vendor_redline_final.md": (
                    "# Final Vendor MSA Redline Memo\n\n"
                    "Verified against `contracts/redline_spec.md` and "
                    "`contracts/msa_base.md`.\n\n"
                    "## Section 4.1 Limitation of Liability\n"
                    "Exposure capped at $500,000.\n\n"
                    "## Section 4.2 Confidentiality\n"
                    "Confidentiality obligations survive for 5 years.\n\n"
                    "## Section 4.3 Subprocessor\n"
                    "Subprocessor changes require 30 days advance notice.\n\n"
                    "## Section 4.4 Data Deletion SLA\n"
                    "Customer records deleted within 15 days of contract termination.\n\n"
                    "## Section 4.5 Governing Law\n"
                    "Governed exclusively by New York law.\n\n"
                    "- **liability_cap_usd**: $500,000\n"
                    "- **deletion_days**: 15\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "contracts/redline_spec.md",
                    "contracts/msa_base.md",
                ],
                "expected_numeric_balances": {
                    "liability_cap_usd": 500000,
                    "deletion_days": 15,
                },
                "forbidden_leaks": ["OUTSIDE_COUNSEL_RETAINER_ID"],
                "required_clauses": [
                    "Limitation of Liability",
                    "Confidentiality",
                    "30 days",
                    "15 days",
                    "New York",
                ],
            },
        },
        {
            "id": "STRESS_TC_28",
            "cartridge": "financial_ops",
            "category": "financial_reconciliation",
            "target": "ledger/escrow_rules.md",
            "output_file": "reports/escrow_release_audit.md",
            "tier": "mid",
            "prompt": (
                "Generate reports/escrow_release_audit.md fulfilling all 5 escrow clauses:\n"
                "1. Record Escrow Release Debit of $48,000.00 from Holdback Trust\n"
                "2. Record Seller Settlement Credit of $45,600.00 to Operating Bank\n"
                "3. Record Platform Fee Credit of $2,400.00 to Fee Revenue\n"
                "4. Confirm net Debit minus Credit variance equals $0.00 with 2 decimals\n"
                "5. Cite ledger/escrow_rules.md and ledger/wire_batch.csv\n"
            ),
            "files": {
                "ledger/escrow_rules.md": (
                    "# Escrow Release Policy\n"
                    "Platform fee is 5.00% ($2,400.00 on $48,000.00).\n"
                ),
                "ledger/wire_batch.csv": (
                    "ref,account,debit,credit\n"
                    "W-1,Holdback Trust,48000.00,0.00\n"
                    "W-2,Operating Bank,0.00,45600.00\n"
                    "W-3,Fee Revenue,0.00,2400.00\n"
                ),
            },
            # Missing Platform Fee Credit and unbalanced totals
            "flawed_draft_files": {
                "reports/escrow_release_audit.md": (
                    "# Escrow Release Draft\n"
                    "1. Escrow Release Debit: $48,000.00\n"
                    "2. Seller Settlement Credit: $45,600.00\n"
                ),
            },
            "solution_files": {
                "reports/escrow_release_audit.md": (
                    "# Escrow Release Audit\n\n"
                    "Sources: `ledger/escrow_rules.md` and `ledger/wire_batch.csv`.\n\n"
                    "1. Escrow Release Debit: $48,000.00 (Holdback Trust)\n"
                    "2. Seller Settlement Credit: $45,600.00 (Operating Bank)\n"
                    "3. Platform Fee Credit: $2,400.00 (Fee Revenue)\n"
                    "4. Total Debit $48,000.00 equals Total Credit $48,000.00; "
                    "net variance $0.00.\n"
                    "- **escrow_gross**: $48,000.00\n"
                    "- **seller_payout**: $45,600.00\n"
                    "- **platform_fee**: $2,400.00\n"
                ),
            },
            "ground_truth": {
                "required_citations": [
                    "ledger/escrow_rules.md",
                    "ledger/wire_batch.csv",
                ],
                "expected_numeric_balances": {
                    "escrow_gross": 48000.00,
                    "seller_payout": 45600.00,
                    "platform_fee": 2400.00,
                },
                "forbidden_leaks": ["ROUTING_PIN_9981"],
                "required_clauses": ["$48,000.00", "$45,600.00", "$2,400.00", "$0.00"],
            },
        },
    ]

    out: list[dict[str, Any]] = []
    idx = 21
    for s in coding_specs:
        provider = _provider_for(idx)
        target = s["target"]
        out.append(
            {
                "task_id": s["id"],
                "suite": SUITE_NAME,
                "task_kind": "coding",
                "category": "multi_clause_completeness",
                "cartridge": "software_engineering",
                "prompt": s["prompt"],
                "files": s["files"],
                "flawed_draft_files": s["flawed_draft_files"],
                "solution_files": s["solution_files"],
                "expected_touched_files": [target],
                "pytest_target": s["pytest"],
                "expected_tier": s["tier"],
                "simulates_one_strike_promotion": True,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=s["tier"],
                    num_turns=6,
                    classifier_driver="onnx_local",
                    simulates_one_strike=True,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_gate4_incomplete_rejected": True,
                    "assert_gate4_complete_accepted": True,
                    "assert_pass0_recall": {"expected_any": [target], "top_k": 10},
                },
            }
        )
        idx += 1

    for ks in knowledge_specs:
        provider = _provider_for(idx)
        target = ks["target"]
        out.append(
            {
                "task_id": ks["id"],
                "suite": SUITE_NAME,
                "task_kind": "knowledge",
                "category": "multi_clause_completeness",
                "cartridge": ks["cartridge"],
                "prompt": ks["prompt"],
                "files": ks["files"],
                "flawed_draft_files": ks["flawed_draft_files"],
                "solution_files": ks["solution_files"],
                "output_file": ks["output_file"],
                "expected_touched_files": [ks["output_file"]],
                "ground_truth": ks["ground_truth"],
                "expected_tier": ks["tier"],
                "simulates_one_strike_promotion": True,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=ks["tier"],
                    num_turns=6,
                    classifier_driver="onnx_local",
                    simulates_one_strike=True,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_gate4_incomplete_rejected": True,
                    "assert_gate4_complete_accepted": True,
                    "assert_pass0_recall": {"expected_any": [target], "top_k": 10},
                },
            }
        )
        idx += 1

    return out


# ---------------------------------------------------------------------------
# Category 4: STRESS_TC_29 .. STRESS_TC_35 — two_strike_and_tier_escalation
# ---------------------------------------------------------------------------


def _build_two_strike_and_tier_tasks() -> list[dict[str, Any]]:
    # 29, 30 (coding high blast >=4 callers -> mid/frontier)
    # 31, 32 (coding isolated leaf 0 callers -> floor/mid)
    # 33, 34 (knowledge high blast >=4 importers -> mid/frontier)
    # 35     (knowledge isolated leaf 0 importers -> floor/mid)

    tc29_files: dict[str, str] = {
        "src/__init__.py": "",
        "src/core/__init__.py": "",
        "src/core/money.py": (
            "def add_cents(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def split_even_cents(total: int, ways: int) -> list[int]:\n"
            "    return [total // ways] * ways\n"
        ),
        "src/billing.py": "from src.core.money import split_even_cents\n",
        "src/orders.py": "from src.core.money import add_cents, split_even_cents\n",
        "src/invoices.py": "from src.core.money import add_cents\n",
        "src/payouts.py": "from src.core.money import split_even_cents\n",
        "src/refunds.py": "from src.core.money import add_cents\n",
        "src/ledger_sync.py": "from src.core.money import split_even_cents\n",
        "tests/test_tc29.py": (
            _py_test_preamble()
            + "import pytest\n"
            "from src.core.money import split_even_cents\n\n"
            "def test_exact_split_even_cents():\n"
            "    assert split_even_cents(10, 3) == [4, 3, 3]\n"
            "    with pytest.raises(ValueError):\n"
            "        split_even_cents(10, 0)\n"
        ),
    }

    tc30_files: dict[str, str] = {
        "src/__init__.py": "",
        "src/core/__init__.py": "",
        "src/core/auth_ctx.py": (
            "def parse_roles(raw: str) -> tuple[str, ...]:\n"
            "    return tuple(raw.split(','))\n"
        ),
        "src/api_users.py": "from src.core.auth_ctx import parse_roles\n",
        "src/api_admin.py": "from src.core.auth_ctx import parse_roles\n",
        "src/api_billing.py": "from src.core.auth_ctx import parse_roles\n",
        "src/api_webhooks.py": "from src.core.auth_ctx import parse_roles\n",
        "src/api_reports.py": "from src.core.auth_ctx import parse_roles\n",
        "tests/test_tc30.py": (
            _py_test_preamble()
            + "from src.core.auth_ctx import parse_roles\n\n"
            "def test_dedup_sorted_normalized_roles():\n"
            "    assert parse_roles(' Admin, viewer ,admin, ') == ('admin', 'viewer')\n"
        ),
    }

    tc31_files: dict[str, str] = {
        "src/__init__.py": "",
        "src/leaf_slug.py": (
            "def to_slug(text: str) -> str:\n"
            "    return text.lower()\n"
        ),
        "src/alpha.py": "VAL_A = 1\n",
        "src/beta.py": "VAL_B = 2\n",
        "src/gamma.py": "VAL_C = 3\n",
        "src/delta.py": "VAL_D = 4\n",
        "src/epsilon.py": "VAL_E = 5\n",
        "tests/test_tc31.py": (
            _py_test_preamble()
            + "from src.leaf_slug import to_slug\n\n"
            "def test_to_slug_normalizes_punctuation():\n"
            "    assert to_slug('  Hello, World!! 2026 ') == 'hello-world-2026'\n"
        ),
    }

    tc32_files: dict[str, str] = {
        "src/__init__.py": "",
        "src/leaf_bytes.py": (
            "def format_bytes(num: int) -> str:\n"
            "    return f'{num} B'\n"
        ),
        "src/mod_one.py": "ONE = 1\n",
        "src/mod_two.py": "TWO = 2\n",
        "src/mod_three.py": "THREE = 3\n",
        "src/mod_four.py": "FOUR = 4\n",
        "src/mod_five.py": "FIVE = 5\n",
        "tests/test_tc32.py": (
            _py_test_preamble()
            + "from src.leaf_bytes import format_bytes\n\n"
            "def test_format_bytes_binary_units():\n"
            "    assert format_bytes(512) == '512 B'\n"
            "    assert format_bytes(1536) == '1.5 KiB'\n"
            "    assert format_bytes(1048576) == '1.0 MiB'\n"
        ),
    }

    coding_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_29",
            "target": "src/core/money.py",
            "pytest": "tests/test_tc29.py",
            "caller_count": 6,
            "allowed_tiers": ["mid", "frontier"],
            "tier": "frontier",
            "prompt": (
                "Update split_even_cents in high-impact core module src/core/money.py so "
                "remainders distribute cleanly across initial shares without losing cents and "
                "ways <= 0 raises ValueError; verify via pytest."
            ),
            "files": tc29_files,
            "flawed_draft_files": {
                "src/core/money.py": (
                    "def add_cents(a: int, b: int) -> int:\n"
                    "    return a + b\n\n"
                    "def split_even_cents(total: int, ways: int) -> list[int]:\n"
                    "    return [total // max(1, ways)] * max(1, ways)\n"
                ),
            },
            "solution_files": {
                "src/core/money.py": (
                    "def add_cents(a: int, b: int) -> int:\n"
                    "    return a + b\n\n"
                    "def split_even_cents(total: int, ways: int) -> list[int]:\n"
                    "    if ways <= 0:\n"
                    "        raise ValueError('ways must be positive')\n"
                    "    base, rem = divmod(total, ways)\n"
                    "    return [base + (1 if i < rem else 0) for i in range(ways)]\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_30",
            "target": "src/core/auth_ctx.py",
            "pytest": "tests/test_tc30.py",
            "caller_count": 5,
            "allowed_tiers": ["mid", "frontier"],
            "tier": "frontier",
            "prompt": (
                "Update parse_roles in high-impact core module src/core/auth_ctx.py to trim "
                "whitespace, lowercase entries, drop blanks, deduplicate, and return sorted "
                "role tuples; run pytest."
            ),
            "files": tc30_files,
            "flawed_draft_files": {
                "src/core/auth_ctx.py": (
                    "def parse_roles(raw: str) -> tuple[str, ...]:\n"
                    "    return tuple(r.strip() for r in raw.split(',') if r.strip())\n"
                ),
            },
            "solution_files": {
                "src/core/auth_ctx.py": (
                    "def parse_roles(raw: str) -> tuple[str, ...]:\n"
                    "    cleaned = {part.strip().lower() for part in raw.split(',') if "
                    "part.strip()}\n"
                    "    return tuple(sorted(cleaned))\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_31",
            "target": "src/leaf_slug.py",
            "pytest": "tests/test_tc31.py",
            "caller_count": 0,
            "allowed_tiers": ["floor", "mid"],
            "tier": "floor",
            "prompt": (
                "Fix isolated leaf helper to_slug in src/leaf_slug.py to lowercase ASCII text, "
                "collapse non-alphanumeric separators to single hyphens, and strip leading/"
                "trailing hyphens; verify with pytest."
            ),
            "files": tc31_files,
            "flawed_draft_files": {
                "src/leaf_slug.py": (
                    "def to_slug(text: str) -> str:\n"
                    "    return text.strip().lower().replace(' ', '-')\n"
                ),
            },
            "solution_files": {
                "src/leaf_slug.py": (
                    "import re\n\n"
                    "def to_slug(text: str) -> str:\n"
                    "    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n"
                ),
            },
        },
        {
            "id": "STRESS_TC_32",
            "target": "src/leaf_bytes.py",
            "pytest": "tests/test_tc32.py",
            "caller_count": 0,
            "allowed_tiers": ["floor", "mid"],
            "tier": "floor",
            "prompt": (
                "Fix isolated leaf formatter format_bytes in src/leaf_bytes.py to scale across "
                "B, KiB, MiB, GiB units with one decimal place above bytes; verify with pytest."
            ),
            "files": tc32_files,
            "flawed_draft_files": {
                "src/leaf_bytes.py": (
                    "def format_bytes(num: int) -> str:\n"
                    "    return f'{num // 1024} KiB' if num >= 1024 else f'{num} B'\n"
                ),
            },
            "solution_files": {
                "src/leaf_bytes.py": (
                    "def format_bytes(num: int) -> str:\n"
                    "    if num < 1024:\n"
                    "        return f'{num} B'\n"
                    "    val = float(num)\n"
                    "    for unit in ('KiB', 'MiB', 'GiB', 'TiB'):\n"
                    "        val /= 1024.0\n"
                    "        if val < 1024.0 or unit == 'TiB':\n"
                    "            return f'{val:.1f} {unit}'\n"
                    "    return f'{num} B'\n"
                ),
            },
        },
    ]

    knowledge_specs: list[dict[str, Any]] = [
        {
            "id": "STRESS_TC_33",
            "cartridge": "knowledge_vault",
            "category": "multi_doc_audit",
            "target": "docs/core_glossary.md",
            "output_file": "docs/core_glossary.md",
            "caller_count": 6,
            "allowed_tiers": ["mid", "frontier"],
            "tier": "frontier",
            "prompt": (
                "Update high-fanout canonical vault document docs/core_glossary.md defining "
                "canonical RPO 5 minutes and RTO 15 minutes across 6 linked service notes."
            ),
            "files": {
                "docs/core_glossary.md": (
                    "# Core Vault Glossary\n"
                    "RPO TBD.\n"
                ),
                "docs/svc_auth.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
                "docs/svc_pay.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
                "docs/svc_orders.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
                "docs/svc_search.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
                "docs/svc_notify.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
                "docs/svc_ledger.md": (
                    "See [[core_glossary]] and [Glossary](docs/core_glossary.md).\n"
                ),
            },
            "flawed_draft_files": {
                "docs/core_glossary.md": (
                    "# Core Vault Glossary Draft\n"
                    "RPO 10 minutes. Missing citation.\n"
                ),
            },
            "solution_files": {
                "docs/core_glossary.md": (
                    "# Core Vault Glossary\n\n"
                    "- Canonical Recovery Point Objective (RPO): 5 minutes "
                    "(`docs/svc_auth.md`, [[svc_auth]]).\n"
                    "- Canonical Recovery Time Objective (RTO): 15 minutes "
                    "(`docs/svc_ledger.md`, [[svc_ledger]]).\n"
                    "- **rpo_min**: 5\n"
                    "- **rto_min**: 15\n"
                ),
            },
            "ground_truth": {
                "required_citations": ["docs/svc_auth.md", "docs/svc_ledger.md"],
                "expected_numeric_balances": {"rpo_min": 5, "rto_min": 15},
                "forbidden_leaks": ["ROOT_VAULT_PASSPHRASE"],
                "required_clauses": ["5 minutes", "15 minutes"],
            },
        },
        {
            "id": "STRESS_TC_34",
            "cartridge": "legal_redline",
            "category": "policy_compliance",
            "target": "clauses/master_definitions.md",
            "output_file": "clauses/master_definitions.md",
            "caller_count": 5,
            "allowed_tiers": ["mid", "frontier"],
            "tier": "frontier",
            "prompt": (
                "Revise heavily cross-referenced master contract definitions in "
                "clauses/master_definitions.md setting Liability Cap to $250,000 and Cure "
                "Period to 30 days across all 5 dependent schedules."
            ),
            "files": {
                "clauses/master_definitions.md": (
                    "# Master Definitions\n## Section 1.1 Definitions\nPending.\n"
                ),
                "clauses/sched_sla.md": (
                    "Depends on [[master_definitions]] ([Master](clauses/master_definitions.md)).\n"
                ),
                "clauses/sched_dpa.md": (
                    "Depends on [[master_definitions]] ([Master](clauses/master_definitions.md)).\n"
                ),
                "clauses/sched_sec.md": (
                    "Depends on [[master_definitions]] ([Master](clauses/master_definitions.md)).\n"
                ),
                "clauses/sched_fees.md": (
                    "Depends on [[master_definitions]] ([Master](clauses/master_definitions.md)).\n"
                ),
                "clauses/sched_support.md": (
                    "Depends on [[master_definitions]] ([Master](clauses/master_definitions.md)).\n"
                ),
            },
            "flawed_draft_files": {
                "clauses/master_definitions.md": (
                    "# Master Definitions Draft\n"
                    "Liability is uncapped.\n"
                ),
            },
            "solution_files": {
                "clauses/master_definitions.md": (
                    "# Master Contract Definitions\n\n"
                    "Cross-referenced by `clauses/sched_sla.md` and `clauses/sched_dpa.md`.\n\n"
                    "## Section 1.1 Liability Cap\n"
                    "Aggregate liability cap equals $250,000.\n\n"
                    "## Section 1.2 Cure Period\n"
                    "Material breach cure period equals 30 days.\n\n"
                    "- **cap_usd**: $250,000\n"
                    "- **cure_days**: 30\n"
                ),
            },
            "ground_truth": {
                "required_citations": ["clauses/sched_sla.md", "clauses/sched_dpa.md"],
                "expected_numeric_balances": {"cap_usd": 250000, "cure_days": 30},
                "forbidden_leaks": ["INTERNAL_SIGNING_SECRET"],
                "required_clauses": ["Liability Cap", "$250,000", "30 days"],
            },
        },
        {
            "id": "STRESS_TC_35",
            "cartridge": "financial_ops",
            "category": "financial_reconciliation",
            "target": "notes/standalone_fx_faq.md",
            "output_file": "notes/standalone_fx_faq.md",
            "caller_count": 0,
            "allowed_tiers": ["floor", "mid"],
            "tier": "floor",
            "prompt": (
                "Update isolated leaf note notes/standalone_fx_faq.md documenting a balanced "
                "sample petty cash replenishment Debit $500.00 and Credit $500.00 citing "
                "ledger/petty_cash.csv."
            ),
            "files": {
                "notes/standalone_fx_faq.md": "# Standalone FAQ\nDraft.\n",
                "ledger/petty_cash.csv": (
                    "tx,account,debit,credit\n"
                    "PC-1,Petty Cash,500.00,0.00\n"
                    "PC-2,Bank Operating,0.00,500.00\n"
                ),
                "notes/other_a.md": "# Note A\nUnrelated.\n",
                "notes/other_b.md": "# Note B\nUnrelated.\n",
                "notes/other_c.md": "# Note C\nUnrelated.\n",
                "notes/other_d.md": "# Note D\nUnrelated.\n",
            },
            "flawed_draft_files": {
                "notes/standalone_fx_faq.md": (
                    "# Standalone FAQ Draft\n"
                    "Debit: $500.00 only.\n"
                ),
            },
            "solution_files": {
                "notes/standalone_fx_faq.md": (
                    "# Standalone Petty Cash FX FAQ\n\n"
                    "Source reference: `ledger/petty_cash.csv`.\n\n"
                    "- Petty Cash Debit: $500.00\n"
                    "- Bank Operating Credit: $500.00\n"
                    "- Net Imbalance Variance: $0.00\n"
                    "- **petty_cash_usd**: $500.00\n"
                ),
            },
            "ground_truth": {
                "required_citations": ["ledger/petty_cash.csv"],
                "expected_numeric_balances": {"petty_cash_usd": 500.00},
                "forbidden_leaks": ["TREASURY_WIRE_TOKEN"],
                "required_clauses": ["$500.00", "$0.00"],
            },
        },
    ]

    out: list[dict[str, Any]] = []
    idx = 29
    for s in coding_specs:
        provider = _provider_for(idx)
        target = s["target"]
        out.append(
            {
                "task_id": s["id"],
                "suite": SUITE_NAME,
                "task_kind": "coding",
                "category": "two_strike_and_tier_escalation",
                "cartridge": "software_engineering",
                "prompt": s["prompt"],
                "files": s["files"],
                "flawed_draft_files": s["flawed_draft_files"],
                "solution_files": s["solution_files"],
                "expected_touched_files": [target],
                "pytest_target": s["pytest"],
                "expected_tier": s["tier"],
                "simulates_one_strike_promotion": True,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=s["tier"],
                    num_turns=6,
                    classifier_driver="onnx_local",
                    simulates_one_strike=True,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_two_strike_escalation": True,
                    "assert_gate3_tier": {
                        "target_path": target,
                        "caller_count": s["caller_count"],
                        "expected_allowed_tiers": s["allowed_tiers"],
                    },
                    "assert_pass0_recall": {"expected_any": [target], "top_k": 10},
                },
            }
        )
        idx += 1

    for ks in knowledge_specs:
        provider = _provider_for(idx)
        target = ks["target"]
        out.append(
            {
                "task_id": ks["id"],
                "suite": SUITE_NAME,
                "task_kind": "knowledge",
                "category": "two_strike_and_tier_escalation",
                "cartridge": ks["cartridge"],
                "prompt": ks["prompt"],
                "files": ks["files"],
                "flawed_draft_files": ks["flawed_draft_files"],
                "solution_files": ks["solution_files"],
                "output_file": ks["output_file"],
                "expected_touched_files": [ks["output_file"]],
                "ground_truth": ks["ground_truth"],
                "expected_tier": ks["tier"],
                "simulates_one_strike_promotion": True,
                "replay_trace": _make_replay_trace(
                    provider=provider,
                    expected_tier=ks["tier"],
                    num_turns=6,
                    classifier_driver="onnx_local",
                    simulates_one_strike=True,
                    seed_idx=idx,
                ),
                "stress_checks": {
                    "assert_two_strike_escalation": True,
                    "assert_gate3_tier": {
                        "target_path": target,
                        "caller_count": ks["caller_count"],
                        "expected_allowed_tiers": ks["allowed_tiers"],
                    },
                    "assert_pass0_recall": {"expected_any": [target], "top_k": 10},
                },
            }
        )
        idx += 1

    return out


def build_all_tasks() -> list[dict[str, Any]]:
    """Build all 35 unique Set 2 stress tasks in deterministic order."""
    tasks = (
        _build_pragmatic_ambiguity_tasks()
        + _build_kv_cache_and_compact_tasks()
        + _build_multi_clause_tasks()
        + _build_two_strike_and_tier_tasks()
    )
    if len(tasks) != 35:
        raise RuntimeError(f"Expected 35 tasks, built {len(tasks)}")
    ids = [t["task_id"] for t in tasks]
    if ids != EXPECTED_TASK_IDS:
        raise RuntimeError(f"Unexpected task IDs: {ids}")
    return tasks


def write_jsonl(out_path: Path = DEFAULT_OUT_PATH) -> Path:
    """Write all 35 tasks to JSONL."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tasks = build_all_tasks()
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for task in tasks:
            fh.write(json.dumps(task, ensure_ascii=False) + "\n")
    return out_path


# ---------------------------------------------------------------------------
# Empirical verification against real QuadGate engines, pytest, and runners
# ---------------------------------------------------------------------------


def _materialize_workspace(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")


async def _verify_single_task(task: dict[str, Any]) -> list[str]:
    from benchmarks.runner import (
        extract_provider_usage,
        verify_coding_ground_truth,
        verify_knowledge_ground_truth,
    )
    from quadgate.contracts import GateEvent, GateType, ModelTier, VerdictAction
    from quadgate.index.ast_gists import AstGistIndexer
    from quadgate.index.sqlite_fts import SqliteFtsStore
    from quadgate.kernel.gates import GateEngine, load_cartridge
    from quadgate.kernel.healing import SelfHealingEngine

    tid = task["task_id"]
    errs: list[str] = []

    # 1. Validate replay_trace schema and Turn 2..N cache read ratio.
    from tests.test_benchmark_suites import _assert_valid_replay_trace

    trace = task["replay_trace"]
    _assert_valid_replay_trace(
        trace,
        expect_retry=bool(task["simulates_one_strike_promotion"]),
    )
    for side in ("baseline", "quadgate"):
        rec = trace[side]
        for t in rec["turns"]:
            unc, cr, cc, out_tok = extract_provider_usage(t["usage"])
            total_in = unc + cr + cc
            if total_in <= 0 or out_tok <= 0:
                errs.append(f"{tid}: {side} invalid token counts in={total_in} out={out_tok}")
    qg_turns = trace["quadgate"]["turns"]
    if len(qg_turns) > 1:
        ratios = []
        for t in qg_turns[1:]:
            unc, cr, cc, _ = extract_provider_usage(t["usage"])
            total_in = unc + cr + cc
            ratios.append(cr / max(1, total_in))
        avg_share = sum(ratios) / len(ratios)
        if avg_share < 0.75:
            errs.append(f"{tid}: quadgate Turn 2..N cache share {avg_share:.3f} < 0.75")
    if task["category"] == "kv_cache_and_compact_reset":
        if not (8 <= len(trace["baseline"]["turns"]) <= 12):
            errs.append(f"{tid}: baseline turns={len(trace['baseline']['turns'])} not in 8..12")
        if not (8 <= len(qg_turns) <= 12):
            errs.append(f"{tid}: quadgate turns={len(qg_turns)} not in 8..12")
    if task["simulates_one_strike_promotion"] and not trace["quadgate"]["retry_turns"]:
        errs.append(f"{tid}: simulates_one_strike_promotion=True requires retry_turns")

    cartridge = load_cartridge(task["cartridge"])

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ws = Path(tmp)
        _materialize_workspace(ws, task["files"])

        # 2. Verify initial buggy files fail & solution files pass real checks.
        if task["task_kind"] == "coding":
            ok_init, msg_init = await verify_coding_ground_truth(ws, task)
            if ok_init:
                errs.append(f"{tid}: initial files unexpectedly passed pytest ({msg_init})")

            if task["flawed_draft_files"]:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as ftmp:
                    fws = Path(ftmp)
                    merged_flawed = {**task["files"], **task["flawed_draft_files"]}
                    _materialize_workspace(fws, merged_flawed)
                    ok_flaw, msg_flaw = await verify_coding_ground_truth(fws, task)
                    if ok_flaw:
                        errs.append(
                            f"{tid}: flawed_draft_files unexpectedly passed pytest ({msg_flaw})"
                        )

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as stmp:
                sws = Path(stmp)
                merged_sol = {**task["files"], **task["solution_files"]}
                _materialize_workspace(sws, merged_sol)
                ok_sol, msg_sol = await verify_coding_ground_truth(sws, task)
                if not ok_sol:
                    errs.append(f"{tid}: solution_files failed pytest: {msg_sol}")
        else:
            ok_init, _ = verify_knowledge_ground_truth(ws, task)
            if ok_init:
                errs.append(f"{tid}: initial knowledge files unexpectedly passed")

            if task["flawed_draft_files"]:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as ftmp:
                    fws = Path(ftmp)
                    merged_flawed = {**task["files"], **task["flawed_draft_files"]}
                    _materialize_workspace(fws, merged_flawed)
                    ok_flaw, _ = verify_knowledge_ground_truth(fws, task)
                    if ok_flaw:
                        errs.append(f"{tid}: flawed knowledge draft unexpectedly passed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as stmp:
                sws = Path(stmp)
                merged_sol = {**task["files"], **task["solution_files"]}
                _materialize_workspace(sws, merged_sol)
                ok_sol, msg_sol = verify_knowledge_ground_truth(sws, task)
                if not ok_sol:
                    errs.append(f"{tid}: knowledge solution failed: {msg_sol}")

        # 3. Index initial workspace for Gate 1 & Gate 3 evaluations.
        indexer = AstGistIndexer(ws)
        gists = indexer.index_workspace()
        fts = SqliteFtsStore(":memory:")
        fts.upsert_gists(gists)
        healing = SelfHealingEngine(":memory:", workspace_root=ws)
        engine = GateEngine(
            cartridge=cartridge,
            fts_store=fts,
            indexer=indexer,
            healing=healing,
        )

        checks = task["stress_checks"]

        # Pass-0 recall assertion
        if "assert_pass0_recall" in checks:
            recall_cfg = checks["assert_pass0_recall"]
            res = fts.search_candidates(
                task["prompt"],
                indexer.workspace_hash,
                limit=recall_cfg["top_k"],
            )
            hit_paths = {g.rel_path for g in res.gists}
            if not (hit_paths & set(recall_cfg["expected_any"])):
                errs.append(
                    f"{tid}: pass0 recall missed {recall_cfg['expected_any']} in {hit_paths}"
                )

        # Gate 1 counterfactual advisory check
        if "assert_gate1_counterfactual" in checks:
            expected_adv = checks["assert_gate1_counterfactual"]["expected_advisory"]
            v1 = await engine.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"{tid}-g1",
                    turn_index=1,
                    workspace_root=str(ws),
                    prompt_text=task["prompt"],
                )
            )
            is_adv = bool(v1.reason and "Read-only brainstorm" in v1.reason)
            if is_adv != expected_adv:
                errs.append(
                    f"{tid}: Gate1 advisory={is_adv} != expected={expected_adv} "
                    f"(reason={v1.reason!r})"
                )

        # Gate 1 multi-turn warm-cache & compact_reset checks
        if "assert_gate1_turns" in checks:
            v1 = await engine.evaluate_gate1_intake(
                GateEvent(
                    gate=GateType.INTAKE,
                    session_id=f"{tid}-turns",
                    turn_index=1,
                    workspace_root=str(ws),
                    prompt_text=task["prompt"],
                )
            )
            if not v1.tail_hints:
                errs.append(f"{tid}: Turn 1 produced empty tail_hints (reason={v1.reason!r})")
            for chk in checks["assert_gate1_turns"]:
                vt = await engine.evaluate_gate1_intake(
                    GateEvent(
                        gate=GateType.INTAKE,
                        session_id=f"{tid}-turns",
                        turn_index=chk["turn_index"],
                        workspace_root=str(ws),
                        prompt_text=task["prompt"],
                        metadata={"compact_reset": chk["compact_reset"]},
                    )
                )
                if chk.get("expect_empty_hints") and vt.tail_hints:
                    errs.append(
                        f"{tid}: Turn {chk['turn_index']} expected empty hints, "
                        f"got {vt.tail_hints}"
                    )
                if chk.get("expect_hints_nonempty") and not vt.tail_hints:
                    errs.append(
                        f"{tid}: Turn {chk['turn_index']} compact_reset=True returned empty hints"
                    )

        # Gate 4 multi-clause completeness checks
        if checks.get("assert_gate4_incomplete_rejected"):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as ftmp:
                fws = Path(ftmp)
                merged_flawed = {**task["files"], **task["flawed_draft_files"]}
                _materialize_workspace(fws, merged_flawed)
                flawed_text = "\n".join(task["flawed_draft_files"].values())
                f_engine = GateEngine(cartridge=cartridge)
                vf = await f_engine.evaluate_gate4_stop(
                    GateEvent(
                        gate=GateType.STOP_AUDIT,
                        session_id=f"{tid}-flaw",
                        workspace_root=str(fws),
                        prompt_text=task["prompt"],
                        touched_files=list(task["flawed_draft_files"].keys()),
                        diff_text=flawed_text,
                        current_tier=ModelTier.MID,
                    )
                )
                if vf.action == VerdictAction.ALLOW:
                    errs.append(
                        f"{tid}: Gate4 unexpectedly ALLOWED flawed draft "
                        f"(reason={vf.reason!r})"
                    )

        if checks.get("assert_gate4_complete_accepted"):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as stmp:
                sws = Path(stmp)
                merged_sol = {**task["files"], **task["solution_files"]}
                _materialize_workspace(sws, merged_sol)
                sol_text = "\n".join(task["solution_files"].values())
                s_engine = GateEngine(cartridge=cartridge)
                vs = await s_engine.evaluate_gate4_stop(
                    GateEvent(
                        gate=GateType.STOP_AUDIT,
                        session_id=f"{tid}-sol",
                        workspace_root=str(sws),
                        prompt_text=task["prompt"],
                        touched_files=list(task["solution_files"].keys()),
                        diff_text=sol_text,
                        current_tier=ModelTier.MID,
                    )
                )
                if vs.action != VerdictAction.ALLOW:
                    errs.append(
                        f"{tid}: Gate4 rejected complete solution: "
                        f"action={vs.action}, reason={vs.reason!r}, "
                        f"unaddressed={vs.unaddressed_clauses}"
                    )

        # Gate 3 ECC tier routing check
        if "assert_gate3_tier" in checks:
            g3_cfg = checks["assert_gate3_tier"]
            tpath = g3_cfg["target_path"]
            v3 = await engine.evaluate_gate3_subagent(
                GateEvent(
                    gate=GateType.SUBAGENT_GOVERNOR,
                    session_id=f"{tid}-g3",
                    workspace_root=str(ws),
                    prompt_text=task["prompt"],
                    touched_files=[tpath],
                    current_tier=ModelTier.FRONTIER,
                )
            )
            chosen = v3.target_tier.value if v3.target_tier else None
            if chosen not in g3_cfg["expected_allowed_tiers"]:
                b_val = indexer.compute_blast_radius([tpath])
                errs.append(
                    f"{tid}: Gate3 chose {chosen!r} (action={v3.action}, "
                    f"updated_model={v3.updated_model!r}), "
                    f"expected {g3_cfg['expected_allowed_tiers']} "
                    f"(blast={b_val:.3f}, reason={v3.reason!r})"
                )

        # 2-strike / one-strike circuit breaker escalation check
        if checks.get("assert_two_strike_escalation"):
            s0 = await healing.handle_gate4_failure(
                GateEvent(
                    gate=GateType.STOP_AUDIT,
                    session_id=f"{tid}-heal",
                    workspace_root=str(ws),
                    touched_files=list(task["solution_files"].keys()),
                    current_tier=ModelTier.FLOOR,
                    strike_count=0,
                ),
                "Oracle failure on floor draft",
            )
            if s0.action != VerdictAction.PROMOTE:
                errs.append(f"{tid}: Healing strike 0 expected PROMOTE, got {s0.action}")
            s1 = await healing.handle_gate4_failure(
                GateEvent(
                    gate=GateType.STOP_AUDIT,
                    session_id=f"{tid}-heal",
                    workspace_root=str(ws),
                    touched_files=list(task["solution_files"].keys()),
                    current_tier=ModelTier.MID,
                    strike_count=1,
                ),
                "Oracle failure on promoted draft",
            )
            escalated = bool(
                s1.violation_receipt and s1.violation_receipt.escalate_to_human
            )
            if s1.action != VerdictAction.BLOCK or not escalated:
                errs.append(
                    f"{tid}: Healing strike 1 expected BLOCK+escalate, "
                    f"got action={s1.action}, escalate={escalated}"
                )

    return errs


async def verify_suite(jsonl_path: Path) -> int:
    """Verify all 35 tasks in JSONL concurrently with real pytest & GateEngine."""
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 35:
        print(f"ERROR: Expected 35 lines in {jsonl_path}, found {len(lines)}")
        return 1
    tasks = [json.loads(line) for line in lines]
    ids = [t["task_id"] for t in tasks]
    if ids != EXPECTED_TASK_IDS:
        print(f"ERROR: Mismatched task IDs: {ids}")
        return 1

    sem = asyncio.Semaphore(4)

    async def _bounded(t: dict[str, Any]) -> list[str]:
        async with sem:
            return await _verify_single_task(t)

    results = await asyncio.gather(*(_bounded(t) for t in tasks))
    all_errors = [err for sub in results for err in sub]
    if all_errors:
        for err in all_errors:
            print(f"FAIL: {err}", flush=True)
        print(
            f"Verification failed with {len(all_errors)} error(s) across 35 tasks.",
            flush=True,
        )
        return 1

    print(
        f"VERIFIED: {len(tasks)}/35 tasks in {jsonl_path} passed pytest, "
        "knowledge ground truth, Pass-0 recall, Gate 1/3/4, and healing escalation checks.",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and verify QuadGate Stress Set 2.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run full empirical verification after generating the JSONL.",
    )
    args = parser.parse_args()
    written = write_jsonl(args.out)
    print(f"Wrote 35 tasks to {written}")
    if args.verify:
        return asyncio.run(verify_suite(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
