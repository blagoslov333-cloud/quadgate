"""Unit and performance tests for quadgate.index.sqlite_fts."""

from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path

import pytest

from quadgate.contracts import IndexedGist
from quadgate.index.sqlite_fts import Pass0CandidateResult, SqliteFtsStore


def _make_gist(
    workspace_hash: str,
    rel_path: str,
    *,
    content_hash: str = "hash_v1",
    language: str = "python",
    semantic_gist: str = "Module summary.",
    symbols: list[str] | None = None,
    imports: list[str] | None = None,
    fan_in_count: int = 0,
    mtime_ns: int = 1_700_000_000_000_000_000,
    is_private_tier1: bool = False,
) -> IndexedGist:
    return IndexedGist(
        workspace_hash=workspace_hash,
        rel_path=rel_path,
        content_hash=content_hash,
        language=language,
        semantic_gist=semantic_gist,
        symbols=symbols or [],
        imports=imports or [],
        fan_in_count=fan_in_count,
        mtime_ns=mtime_ns,
        is_private_tier1=is_private_tier1,
    )


def _seed_2000_gists(workspace_hash: str = "ws-bench") -> list[IndexedGist]:
    domains = [
        ("billing", "invoice", "ledger", "Calculates invoice totals and payment settlements."),
        ("storage", "blob", "bucket", "Streams encrypted object storage chunks and metadata."),
        ("metrics", "telemetry", "histogram", "Aggregates latency histograms and trace spans."),
        ("router", "dispatch", "endpoint", "Maps HTTP routes to controller handlers."),
        ("search", "indexer", "inverted", "Maintains document postings and scoring tables."),
        ("cache", "eviction", "ringbuf", "Manages LRU cache eviction and TTL sweeps."),
        ("parser", "grammar", "syntax", "Tokenizes AST declarations and scope trees."),
        ("mailer", "template", "smtp", "Renders transactional email templates and queues."),
        ("worker", "scheduler", "backoff", "Coordinates background retry queues and timers."),
        ("catalog", "inventory", "sku", "Tracks warehouse SKU reservations and stock."),
    ]
    gists: list[IndexedGist] = []
    for i in range(2000):
        if i == 317:
            gists.append(
                _make_gist(
                    workspace_hash,
                    "src/auth/jwt_validator.py",
                    content_hash="blake3_jwt_1",
                    language="python",
                    semantic_gist="Validates JWT signatures and checks token expiration timestamps.",
                    symbols=["verify_token", "JWTValidator", "checkTokenExpiration"],
                    imports=["cryptography", "time"],
                    fan_in_count=18,
                )
            )
            continue
        if i == 1492:
            gists.append(
                _make_gist(
                    workspace_hash,
                    "src/middleware/rate_limiter.ts",
                    content_hash="blake3_rl_1",
                    language="typescript",
                    semantic_gist="Enforces login throttling and per-IP sliding window rate limits.",
                    symbols=["RateLimiter", "SlidingWindowLimiter", "checkThrottleQuota"],
                    imports=["redis"],
                    fan_in_count=22,
                )
            )
            continue

        domain, noun, detail, summary = domains[i % len(domains)]
        gists.append(
            _make_gist(
                workspace_hash,
                f"src/{domain}/mod_{i // 50}/{noun}_{detail}_{i}.py",
                content_hash=f"hash_{i}",
                language="python",
                semantic_gist=f"{summary} Component #{i}.",
                symbols=[
                    f"handle_{noun}_{i}",
                    f"{domain.capitalize()}{noun.capitalize()}Service{i}",
                    f"process{detail.capitalize()}Item{i}",
                ],
                imports=[f"quadgate.{domain}.base"],
                fan_in_count=i % 7,
            )
        )
    return gists


def test_memory_and_file_backed_wal_persistence_and_lazy_init(tmp_path: Path) -> None:
    # 1. In-memory lazy initialization without explicit initialize()
    mem_store = SqliteFtsStore(":memory:")
    g1 = _make_gist(
        "ws-mem",
        "src/auth/jwt_validator.py",
        semantic_gist="Verifies JWT bearer tokens.",
        symbols=["verify_token"],
    )
    assert mem_store.upsert_gists([g1]) == 1
    mem_res = mem_store.search_candidates("verify_token", "ws-mem")
    assert isinstance(mem_res, Pass0CandidateResult)
    assert len(mem_res.gists) == 1
    assert mem_res.gists[0] == g1
    mem_store.close()
    mem_store.close()  # idempotent close

    # 2. File-backed WAL-mode persistence across close/reopen
    db_file = tmp_path / "nested" / "pass0.db"
    disk_store = SqliteFtsStore(db_file)
    disk_store.initialize()
    disk_store.initialize()  # idempotent initialize

    assert disk_store._conn is not None
    journal_mode = disk_store._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    synchronous = disk_store._conn.execute("PRAGMA synchronous;").fetchone()[0]
    temp_store = disk_store._conn.execute("PRAGMA temp_store;").fetchone()[0]
    cache_size = disk_store._conn.execute("PRAGMA cache_size;").fetchone()[0]
    assert str(journal_mode).lower() == "wal"
    assert synchronous == 1  # NORMAL
    assert temp_store == 2  # MEMORY
    assert cache_size == -16000

    g2 = _make_gist(
        "ws-disk",
        "src/middleware/rate_limiter.ts",
        language="typescript",
        semantic_gist="Protects login endpoints against brute-force bursts.",
        symbols=["RateLimiter"],
        fan_in_count=5,
    )
    assert disk_store.upsert_gists([g2]) == 1
    disk_store.close()

    # Reopen via lazy search_candidates
    reopened = SqliteFtsStore(db_file)
    reopened_res = reopened.search_candidates("RateLimiter", "ws-disk")
    assert len(reopened_res.gists) == 1
    assert reopened_res.gists[0] == g2
    reopened.close()


def test_idempotent_upsert_no_fts_duplication_or_stale_tokens(tmp_path: Path) -> None:
    db_file = tmp_path / "upsert.db"
    with SqliteFtsStore(db_file) as store:
        v1 = _make_gist(
            "ws1",
            "src/service.py",
            content_hash="h_v1",
            semantic_gist="Legacy alpha_obsolete_widget implementation.",
            symbols=["legacyAlphaSymbol"],
        )
        assert store.upsert_gists([v1]) == 1
        # Identical re-upsert should skip writes cleanly.
        assert store.upsert_gists([v1]) == 0

        # Update with new content_hash and new symbols/gist.
        v2 = _make_gist(
            "ws1",
            "src/service.py",
            content_hash="h_v2",
            semantic_gist="Modern beta_refreshed_handler module.",
            symbols=["modernBetaHandler"],
            fan_in_count=9,
        )
        # Also test duplicate key inside same batch (last wins).
        assert store.upsert_gists([v1, v2]) == 1

        assert store._conn is not None
        g_count = store._conn.execute("SELECT COUNT(*) FROM gists").fetchone()[0]
        fts_count = store._conn.execute("SELECT COUNT(*) FROM gists_fts").fetchone()[0]
        tri_count = store._conn.execute("SELECT COUNT(*) FROM gists_trigram").fetchone()[0]
        assert g_count == 1
        assert fts_count == 1
        assert tri_count == 1

        # Old tokens must be completely gone from both FTS5 and trigram.
        assert store.search_candidates("alpha_obsolete_widget", "ws1").gists == []
        assert store.search_candidates("legacyAlphaSymbol", "ws1").gists == []
        assert store.search_candidates("obsol", "ws1").gists == []

        # New tokens must resolve to updated v2.
        res_new = store.search_candidates("modernBetaHandler", "ws1")
        assert len(res_new.gists) == 1
        assert res_new.gists[0].content_hash == "h_v2"
        assert res_new.gists[0].fan_in_count == 9

        res_tri = store.search_candidates("efresh", "ws1")
        assert len(res_tri.gists) == 1
        assert res_tri.gists[0].content_hash == "h_v2"


def test_workspace_hash_multi_repo_isolation() -> None:
    with SqliteFtsStore(":memory:") as store:
        repo_a = _make_gist(
            "repo-alpha",
            "src/auth/validator.py",
            content_hash="alpha_1",
            semantic_gist="Alpha repo OAuth validator.",
            symbols=["alphaValidate"],
        )
        repo_b = _make_gist(
            "repo-beta",
            "src/auth/validator.py",
            content_hash="beta_1",
            semantic_gist="Beta repo OAuth validator.",
            symbols=["betaValidate"],
        )
        assert store.upsert_gists([repo_a, repo_b]) == 2

        res_a = store.search_candidates("validator", "repo-alpha")
        res_b = store.search_candidates("validator", "repo-beta")
        res_c = store.search_candidates("validator", "repo-gamma")

        assert [g.content_hash for g in res_a.gists] == ["alpha_1"]
        assert [g.content_hash for g in res_b.gists] == ["beta_1"]
        assert res_c.gists == []


def test_query_sanitization_punctuation_and_fts_operators() -> None:
    with SqliteFtsStore(":memory:") as store:
        store.upsert_gists(
            [
                _make_gist(
                    "ws1",
                    "src/middleware/rate_limiter.ts",
                    language="typescript",
                    semantic_gist="Enforces login throttling and sliding window limits.",
                    symbols=["RateLimiter", "checkQuota"],
                )
            ]
        )

        adversarial_queries = [
            "",
            "   ",
            "??? *** \"\" '' :: -- // (( )) [[ ]]",
            "AND OR NOT NEAR",
            '"unclosed quote (paren) [bracket]: *wild? -dash /slash',
            "How do we fix login throttling in rate_limiter.ts? (urgent!) [check: ^1]",
        ]
        for q in adversarial_queries:
            res = store.search_candidates(q, "ws1")
            assert isinstance(res, Pass0CandidateResult)

        nl_res = store.search_candidates(
            'Can you check "RateLimiter" in src/middleware/rate_limiter.ts? [urgent: *quota* -fix]',
            "ws1",
        )
        assert nl_res.gists
        assert nl_res.gists[0].rel_path == "src/middleware/rate_limiter.ts"


@pytest.mark.parametrize(
    ("text", "workspace_root", "expected"),
    [
        ("Fix token validation in jwt_validator.py", "/repo/quadgate", True),
        ("Copy .env.example to local config", "", True),
        ("Copy .env.example to .env", "", False),
        ("Inspect .env.sample and .env.template defaults", "", True),
        ("Use flask-sqlalchemy-extension for ORM", "", True),
        ("Ignore invalid SSN 000-12-3456 or 666-12-3456 or 901-12-3456", "", True),
        ("Ignore invalid SSN 123-00-4567 and 123-45-0000", "", True),
        # Confidential tags
        ("Summarize this [private] note", "", False),
        ("Draft [CONFIDENTIAL] board memo", "", False),
        ("Keep [AirGap] docs local only", "", False),
        ("Mark [Tier1-Private] ledger", "", False),
        # Private paths
        ("Read vault/private/salary.md", "", False),
        (r"Read vault\private\salary.md", "", False),
        ("Open private_vault/keys.md", "", False),
        ("Read .env values", "", False),
        ("Cat config/.env.production", "", False),
        ("Load secrets/prod_keys.yaml", "", False),
        ("Check privileged/counsel_memo.docx", "", False),
        ("Open attorney_client/strategy.md", "", False),
        ("Audit payroll/2026_q1.csv", "", False),
        ("Export tax_ids/employees.csv", "", False),
        ("Normal prompt", r"C:\Users\dev\vault\private", False),
        # SSNs & credentials
        ("Employee SSN 123-45-6789 on file", "", False),
        ("OPENAI_API_KEY=sk-proj1234567890abcdefghij", "", False),
        ("AWS_KEY=AKIAIOSFODNN7EXAMPLE", "", False),
        ("GITHUB=ghp_1234567890abcdef1234567890abcdef1234", "", False),
        ("GOOGLE=AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGew12", "", False),
        ("SLACK=xoxb-1234567890-abcdef123456", "", False),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "", False),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "", False),
        ("-----BEGIN PRIVATE KEY-----", "", False),
    ],
)
def test_air_gap_classification_rules(
    text: str, workspace_root: str, expected: bool
) -> None:
    store = SqliteFtsStore(":memory:")
    assert store.check_air_gap(text, workspace_root) is expected


def test_air_gap_latency_and_search_tier1_propagation() -> None:
    with SqliteFtsStore(":memory:") as store:
        store.upsert_gists(
            [
                _make_gist(
                    "ws-sec",
                    "src/auth/jwt_validator.py",
                    semantic_gist="Validates public JWT issuer claims.",
                    symbols=["verify_token"],
                    is_private_tier1=False,
                ),
                _make_gist(
                    "ws-sec",
                    "vault/private/executive_comp.md",
                    language="markdown",
                    semantic_gist="Executive bonus ledger and compensation schedule.",
                    symbols=["bonus_schedule"],
                    is_private_tier1=True,
                ),
            ]
        )

        # Normal query hitting public gist -> cloud egress allowed
        pub_res = store.search_candidates("verify_token", "ws-sec")
        assert pub_res.gists
        assert pub_res.cloud_egress_allowed is True

        # Benign query hitting is_private_tier1=True gist -> cloud egress blocked
        priv_hit = store.search_candidates("bonus_schedule", "ws-sec")
        assert priv_hit.gists
        assert priv_hit.gists[0].is_private_tier1 is True
        assert priv_hit.cloud_egress_allowed is False

        # Air-gapped query hitting public gist -> cloud egress blocked
        tagged_res = store.search_candidates("[private] verify_token", "ws-sec")
        assert tagged_res.gists
        assert tagged_res.cloud_egress_allowed is False

        # Benchmark check_air_gap (< 2 ms per call)
        samples = [
            "Normal prompt about verify_token and rate_limiter.ts",
            "Read vault/private/doc.md with [confidential] tag",
            "Check sk-proj1234567890abcdefghij and 123-45-6789",
            "Copy .env.example without reading .env",
        ] * 125
        latencies_ms: list[float] = []
        for s in samples:
            t0 = time.perf_counter()
            store.check_air_gap(s, "/repo/app")
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        p95_ms = sorted(latencies_ms)[int(0.95 * len(latencies_ms))]
        assert statistics.mean(latencies_ms) < 2.0
        assert statistics.median(latencies_ms) < 2.0
        assert p95_ms < 2.0


def test_2000_record_ranking_and_sub_4ms_latency_benchmark(tmp_path: Path) -> None:
    db_file = tmp_path / "bench_2000.db"
    gists = _seed_2000_gists("ws-bench")

    with SqliteFtsStore(db_file) as store:
        inserted = store.upsert_gists(gists)
        assert inserted == 2000

        queries_and_expected = [
            ("jwt", "src/auth/jwt_validator.py"),
            ("verify_token", "src/auth/jwt_validator.py"),
            ("token expiration", "src/auth/jwt_validator.py"),
            ("How does JWT checkTokenExpiration work?", "src/auth/jwt_validator.py"),
            ("validat", "src/auth/jwt_validator.py"),
            ("limiter", "src/middleware/rate_limiter.ts"),
            ("RateLimiter", "src/middleware/rate_limiter.ts"),
            ("fix login throttling", "src/middleware/rate_limiter.ts"),
            ("ratelim", "src/middleware/rate_limiter.ts"),
            ("imit", "src/middleware/rate_limiter.ts"),
        ]

        # Verify top-hit ranking accuracy on all query modes
        for query, expected_path in queries_and_expected:
            res = store.search_candidates(query, "ws-bench", limit=32)
            assert res.gists, f"Expected candidates for query {query!r}"
            assert res.gists[0].rel_path == expected_path, (
                f"Query {query!r} ranked {res.gists[0].rel_path} ahead of {expected_path}"
            )
            assert res.cloud_egress_allowed is True
            assert res.query_latency_ms >= 0.0

        # Benchmark repeated runs across all query types
        all_latencies_ms: list[float] = []
        for _ in range(10):
            for query, _ in queries_and_expected:
                t0 = time.perf_counter()
                res = store.search_candidates(query, "ws-bench", limit=32)
                dt_ms = (time.perf_counter() - t0) * 1000.0
                all_latencies_ms.append(dt_ms)
                assert res.gists

        mean_ms = statistics.mean(all_latencies_ms)
        median_ms = statistics.median(all_latencies_ms)
        assert median_ms < 4.0, f"Median query latency {median_ms:.3f} ms exceeded 4.0 ms"
        assert mean_ms < 4.0, f"Mean query latency {mean_ms:.3f} ms exceeded 4.0 ms"
