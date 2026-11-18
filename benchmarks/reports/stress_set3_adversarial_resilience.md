# QuadGate Empirical A/B Proof Scorecard

- **Generated (UTC):** `2026-11-17 20:16:54Z`
- **Total Receipts:** `210` (`105` matched Baseline vs. QuadGate pairs)

## Executive Summary (Aggregate Baseline vs. QuadGate)

| Metric | Baseline | QuadGate | Delta |
| :--- | ---: | ---: | ---: |
| Runs (`n`) | 105 | 105 | — |
| Pass@1 (%) | 34.3% | 99.0% | +64.8 pp |
| Uncached Input Tokens | 4,367,658 | 521,769 | -3,845,889 |
| Cache Read Input Tokens | 1,148,280 | 880,224 | -268,056 |
| Cache Creation Input Tokens | 124,560 | 221,625 | +97,065 |
| Output Tokens | 431,970 | 113,346 | -318,624 |
| Warm KV-Cache Hit Rate (%) | 20.4% | 54.2% | +33.9 pp |
| Generative Cost (incl. Retry Tax) | `$30.0802` | `$6.2900` | `-$23.7902` |
| Classifier Cost | `$0.0000` | `$0.0203` | `$0.0203` |
| Retry Tax (USD / ms) | `$0.0000` / 0.0 ms | `$0.0190` / 20274.0 ms | `$0.0190` |
| Total Cost | `$30.0802` | `$6.3102` | `-$23.7700` |
| Wall-Clock Latency p50 / p90 / mean (ms) | 24658.5 / 29816.0 / 24750.8 | 7902.4 / 25821.9 / 12872.5 | -11878.3 ms mean |

## Paired 95% Confidence Intervals (Baseline minus QuadGate)

| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |
| :--- | ---: | ---: | ---: | ---: |
| Total Input Token Savings | 105 | 38,256.0 tok | [36,250.0, 40,262.0] tok | 71.2% ([67.5%, 74.9%]) |
| Total Cost Reduction | 105 | `$0.2264` | [`$0.1750`, `$0.2778`] | 79.0% ([61.1%, 97.0%]) |
| Wall-Clock Latency Reduction | 105 | 11,878.3 ms | [9,794.4, 13,962.1] ms | 48.0% ([39.6%, 56.4%]) |

## Release Readiness Gate (Four Release Criteria Verification)

| Criterion | Target | Baseline | QuadGate | Verdict |
| :--- | :--- | ---: | ---: | :---: |
| Quality Parity or Gain (`Pass@1`) | `>= Baseline` (`+10%..+25%` target) | 34.3% | 99.0% (+64.8 pp) | PASS |
| Cache Integrity (Turns `2..N`) | `>= 75.0%` `cache_read` share | 23.0% | 93.0% (+70.1 pp) | PASS |
| Net Cost Compression | `>= 60.0%` total spend drop | `$30.0802` | `$6.3102` (-79.0%) | PASS |
| Wall-Clock Acceleration (`p50`) | `>= 1.8x` median speedup | 24658.5 ms | 7902.4 ms (3.12x) | PASS |

## Per-Suite Breakdown

### Suite: `stress_adversarial_resilience` (`105` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 34.3% | 99.0% | +64.8 pp |
| Warm KV-Cache Hit Rate (%) | 20.4% | 54.2% | 71.2% input saved ([67.5%, 74.9%]) |
| Total Cost | `$30.0802` | `$6.3102` | 79.0% ([61.1%, 97.0%]) |
| Wall-Clock p50 / p90 / mean (ms) | 24658.5 / 29816.0 / 24750.8 | 7902.4 / 25821.9 / 12872.5 | 48.0% ([39.6%, 56.4%]) |

## Stress Kernel & Security Diagnostics

| Kernel Check Category | Passed | Total | Pass Rate (%) |
| :--- | ---: | ---: | ---: |
| `airgap_zero_egress` | 24 | 24 | 100.0% |
| `failopen_corrupt_index` | 12 | 12 | 100.0% |
| `gate2_probe` | 128 | 129 | 99.2% |
| `gate4_complete_accepted` | 21 | 21 | 100.0% |
| `gate4_incomplete_rejected` | 12 | 12 | 100.0% |
| `pass0_recall` | 6 | 6 | 100.0% |

### Failed Stress Checks (1 total)

| Suite | Task | Run | Check | Detail |
| :--- | :--- | ---: | :--- | :--- |
| `stress_adversarial_resilience` | `STRESS_AR_21` | 2 | `gate2_probe:clean_130kb_python_diff_under_25ms` | expected=allow actual=allow rule='' s2a=False shim_ms=48.41 link=none |

## Methodology & Honest Accounting

- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.
- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.
- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.
- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.

## Tamper-Evident SHA-256 Receipt Manifest

| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |
| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.9640` | 22831.5 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `f52740cd83a854b17b2b64acff2ae76120175175a29f7270fd35b3153a9cce92` |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.1949` | 7717.7 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `783ba8869bcd8a157834542c32431049cc762de151eed2c7f8d2225f27e12415` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1780` | 27032.0 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `157f4415e42817e4e1fbb87406a605389526c49801703632897ba79fc5cdae79` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0300` | 17882.2 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `79ad8097c268a40ef3befa317e2a51680783b9e533a71dd5fd5d38b38f09eafe` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0776` | 19136.0 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `f0a47e40acadbe31958ef2433fe3262026aec45d2c4fa6b23c3553797b35259e` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0236` | 8559.6 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `11bf62d48d0a292850a000b4283f61aac087bb2dda19b04671f26bbccbb9b119` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1950` | 23136.0 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `de0e0d5ce30d355d6c6103a85395241ab6d89da67476e01f785cd3f020bf5439` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0397` | 6323.4 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `fb372adb4bde5b94dada503421921c7598d2eb5e234ca046f1dcb90c082bc62a` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1788` | 27380.0 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `e153ecfd54b62b31c4b10ea2a5c3e1419aa78e04f4c6f227da678d8b55b4b2b2` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0295` | 4702.5 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `0bed8d457b54ed236c9579b5bbdddebf8756505679f55e96d785d7af2941fd4f` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0195` | 19397.0 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `23361015a38376261336325f8ad5507bbe0285dafeddc030ad6fda3b1ffffc0f` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0062` | 8294.5 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `a0ff879f67f09d50306752b6a66ff96ab2717a218d0ecb946e501c401568d1e6` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.9769` | 23440.5 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `54822e14f49dec219b1720d0496e1b61d21b6e083f20cd4e0178ba3384af5a41` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.2031` | 5538.1 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `71bce8bc94da5b4baef36a54a31facd9713bd6a88011893f0a0994ddd513304d` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1805` | 27728.0 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `552450905fb8c23daa32372a2d8fd5ae81bbb85c6e157a9338bb960b90d025e2` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0301` | 5668.3 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `b6600ea5511863ebc80910ad343cfed4f7cbe78c0ace1820236ca5a8b6cdf627` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0785` | 19658.0 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `6a435bcf6296b434cbfa5a78c82b1e4a780af448914e60060e3fdeb65dfe2718` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0246` | 7262.7 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `6f99c6132fba3b24d891ba85917e8474e5296b482d89838f542d70deef999eae` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1976` | 23745.0 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `e55096f7c48405018d37e7d7999ab040cde35cbb3f236a1bb0eeab37ce8bcdd0` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0400` | 4493.9 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `ad70f7f3497c5a429cbae3ce800efd568fd19635cdac9e742b9ae0aef64dde5d` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1813` | 28076.0 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `474296d20ccbca71b27bbaa9f7aa40d86868e23b83d904ccaf3f7d276edd9ce4` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0308` | 6935.1 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `92c14fdf293e591c4ba54cc30f2d13157dd9435985a9293fca8b23c4389fb84f` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0197` | 19919.0 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `c056856cbd57820a558c96e187c20dbfbbf9450148abc9eb97280613462596a1` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0062` | 11177.2 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `da10d65426c87e6a97395b43a79e5b914f7666ea645050349ebcf60a795eaabf` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.9993` | 24049.5 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `9b939b5c7f6c35d1e9c9f2e8dbbe23d577b91d4d87b3af1114b7dee26e2228b6` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.2023` | 3446.8 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `29f482c45885f668768dabfdfcbe8105f21206b5f0dc3a4ea1482b9b4c4b52a1` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1830` | 28424.0 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `fc18af25395fae7eedf7b75bf45d138df9507d2a1ee65a5a43afb3c836d4b4a1` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0311` | 3440.6 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `1b9acaea7a3a8ece78e44eb59795f1697f3efee2c816ba76726e51b9716aa208` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0793` | 20180.0 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `9f879fcca18707c34ee5588d904bc46d0b3b7860768546b27f497fbf51cc98ab` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0246` | 5374.7 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `e4f39169eb6c9f851e6c6afdabfaf1f2a4067a9ba828b33bb4fccbc2c3058faa` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.2002` | 24354.0 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `87253d9ed87770b82ed8d91a1ca9e708e554e49d94905d10283056ddff5b204a` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0415` | 3480.1 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `ed2c79d2341184cae651741e392e1e079f48255182d4b2d6ebe7cc17072b7792` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.1838` | 28772.0 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `1eb904a22ab6a10e27fbabf9e4efd1f616664e4e2075c53541ee32271a3bf53e` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | yes | `$0.0449` | 7902.4 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `a065890896090b44152dc186b6a2bfa8aab4f817b5af44acb09010bb71b54931` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.0199` | 20441.0 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `d78c38376b3bd79acfbe4d776e6dfcba72ebd50d63a04abddc028359cdee8283` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0060` | 6843.2 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `c528e6e244b7d0bac8693f4c2c69f036a4556d5215631bcc88e8d4b693af6583` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$1.0123` | 24658.5 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `e1a01d45dd2c267f4a92b1ac1baa287f9e982be320e23e87699e7c71b9384dd8` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.2107` | 3595.0 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `11ef757ce90951913679038438b86c9a042574b6e72677e0105122e7f3ad3480` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.1867` | 29120.0 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `a43c784150573f56ec3f95157bd66e0d020d93e2d35101499aab68415c500f72` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0315` | 3561.2 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `767a71f751c041736ff49d66dc591204860fc05110ea53c568dde68c5e74d1f7` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.0802` | 20702.0 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `65225391f7246f67405b0a451a949eace5fa518bc26454669ebdd4fde7a90baa` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0259` | 10088.0 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `98f38728d46be67be6dd7c53bf604c8a6e9ae1146afb672dbe7c46a5a222b3dc` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.2028` | 24963.0 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `adb295a94f6544407a36d41b68f2656b2a57413f9084f57ebad9c7eecea413de` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0434` | 39591.8 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `c4104596f0e429a861a266fe4dcf39f9350b7505b24e5c42a55f0a3a654a4fd9` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.1863` | 29468.0 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `2e5deb9b9f5a09bb14a6012cab89c27a371a5622766d735c97cdd442a735122d` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0322` | 15680.8 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `ddd24ab3874031e2bfcc447073f0ffd50155fbc06b649867a37ee75a87ef8355` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.0201` | 20963.0 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `5254724c36788b0f08b7c71ba7c2fe8eba1b9f26a2cf160cc41e99401ff3eaf6` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0064` | 21152.0 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `b4bef015ef0a2261b0561019f6845becbfe5cedbb4a744b77078fb4fc8e6552b` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$1.0252` | 25267.5 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `238ab2a124a6ed03446b4b1c990326a52a94bfc192a0d254f0606094da645b16` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.2122` | 43193.9 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `d68d0a0e7d00eddc418246f603cada4ca84064ecf6f9500b6034b97adcf77893` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.1892` | 29816.0 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `fe5b7daf8c4498925055a9b9c349d9b12910ff9a330ddd633d1d01684fa96e15` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0330` | 41198.5 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `d2d0e53c76924818ea2da1a046bf2150435dc97f22bb50854639bc9a54490b8d` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.0823` | 21224.0 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `b4e19c0656357522e6728f1bbc41e888e49449e3c4b8062e5b05a362d6f5b237` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0258` | 29228.5 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `f06b14d48a7e5a93a10a07949db22c18c0a3d131569725e80a5a853ee608766f` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `baseline` | 1 | PASS | no | `$0.2054` | 25572.0 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `71847cad844355677147dba928cff33ee0ba007cf33bf37baf170a3a3c8efbec` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0433` | 21967.0 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `3feaf16cd95d81cf6d2a219a66cd6be0839dd8b78cb651ac240df88b68a9ed4f` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1888` | 30164.0 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `c6e9f3acacf145b23c1f32d84a3921a8706162499e53de3f584c861ac2e40e7b` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0336` | 20296.1 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `1f47ee0d0a4a464a4fb5b071bd5bbefbf83a275eeda215ea715feecf49a22640` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0203` | 21485.0 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `4f37089eb2f2168b70ae6bb54c1e817d2bd89d7f237c1cf69a8a3a7879256d6a` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | yes | `$0.0069` | 25221.3 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `318af3a3906799b756aa242c2dec1fa04e3828dc7c827d502855686d8810c540` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$1.0382` | 25876.5 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `e88e5ea580cf122c0f69f4d0ae02c3ec5d3bebd4ec11b35ceb214cf498496737` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.2208` | 19685.0 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `414eaecb5adfc5579ad0c16e0f54538bfdf7eb3d0fe69003f169d0967d5a2906` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1917` | 30512.0 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `927de758683a57bd62c507ddbb12cad1e715f7963bf865edea862bebde075b69` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | yes | `$0.0482` | 23767.9 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `5e3d5220128955df56975792b921aab3d65524313cb2e68d4488025057a87d6b` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.0832` | 21746.0 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `4ec8b65ef50b7f94bb4c0fd767a0b2feefc04e01f8de302ec35b8d05da213869` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0259` | 13543.8 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `333cb0802788841797d74cc5c5618a0d8e0560b49a40436476b7c09080cf4bff` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.2099` | 26181.0 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `b276e397d90e0fbd8696a7f5fea9f4b75011ee93d710e6db5097935cd23c6216` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | yes | `$0.0602` | 17352.3 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `6fb2d2bf54fe4165359eb0ae78f4809511af25bfe623798e1944e2a8bd80aa60` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `baseline` | 1 | FAIL | no | `$0.1913` | 30860.0 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `4368ca4c46688038a47d95596d8f38390dcceb795830c639faa4586df0e3e0c5` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `quadgate` | 1 | PASS | no | `$0.0341` | 11759.9 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `a1c3e5e1d03c554692a1503c5c67d721877777e5cb55fd844c80ab5ba8ff98ce` |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.9640` | 22831.5 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `c0974dbd003c7d1dc896e533bb60fe6971645ce4ffc6a1f93e20ab4258c307af` |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.1949` | 11938.0 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `bac0caed5cd6f6e8035772d6720f2e211642f07f1bf74e75653b5d88a641b164` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1780` | 27032.0 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `550601c122001166d53437170783e5d4a221192aa3dfb613ce2dd4187e7bcf5a` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0300` | 7864.3 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `780abd46c221a90cf6d6d41aaeca5275fe60cf0b90850730a6291bd90d56954e` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0776` | 19136.0 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `7e3a4605834fc1c17710b6dd14f283dcbc2fc597751598135c25bd16ea962343` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0236` | 11411.8 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `1cf12a4b5e081cb0607e41401bd83c4e1b8747ccd1e684ae032a46d5dd18cc60` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1950` | 23136.0 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `571fb395558350a9149507f7f28088021898ca042e7619261bb117fd6aabf68c` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0397` | 8813.3 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `17a26a95b429b4e03934ec3840090f132ffaaa057209d91d113d9a325f1512a5` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1788` | 27380.0 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `50692aa1318e3744d5faf98ee2371b99f8334d7f7f6d735b49bb583fb3dc5352` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0295` | 7351.8 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `39bcce80cb7b57df849c16d0560dfc709bca5e3f2e60cc67007851f66c1226f2` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0195` | 19397.0 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `39fd2d401c3b01e7a77a9be7a27716dcad2ab53dcf4a4be2bf6affbbc28cb31a` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0062` | 8792.6 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `f3063f50569ed6200d9c433657bab8129ad33a5dc1579ea498ba3cc7ecb1f9ea` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.9769` | 23440.5 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `501a104dff9c82d4f0e8d7e891dfbe363d3731d4797a10d7b1d0814958a22d36` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.2031` | 7053.0 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `e4128f3f9540d10fc2f751b8e4597b62d23646b052da741cc9490e6270ffb417` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1805` | 27728.0 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `dec6b63da98bc6975c483399271725eadcf1bcf350dda36b244410053507b909` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0301` | 6120.4 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `f16f25dd59cc6ef09de3861db7d7198145cb1a2bff0d87bd8751ec39564ac464` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0785` | 19658.0 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `75e6dbbef5295d4129cc08cf1306f46b9ab93228a692c0f3ad5fdf0ecf37c916` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0246` | 7257.0 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `6f59a9a1dc2115633bcb85ece6b0f8d1be71ff33cec719b6b1b0675a04eca803` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1976` | 23745.0 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `1929ca7c3f8bf325b3a7a5c2ca7d6d1c695e3edcebd6c1e672e6705569f204de` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0400` | 4859.0 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `4d61550773fc9bbb5dd07cdad8ced985a5a58681c63a76d4ef7482ffd21c6f65` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1813` | 28076.0 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `57707313dd6e7a11ff6701f7161854125fdc3a7e2902ff43a17b8f6ba54c866d` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0308` | 4662.5 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `eb50fc46092a620389b010aa1d1dff979aa032cd20c6d7927baaf193fa7e17a4` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0197` | 19919.0 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `710b62cb8b6260cfbee77c356b81ca4e0fdcec20fa7e28642d498ff5878dbea8` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0062` | 7657.0 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `d07066eb0977b4d97a2ac393a442d5529c375de3899b53282a35c0206c55935e` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.9993` | 24049.5 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `48e8bed12e6989c5f3c98d08ebda3ee3d7269aa3ca07dd8a72b7066d523bf84f` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.2023` | 3382.1 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `5d0a896590c7e690cd9cf8dc0be3398d749355af0b57cebb4fceb24501a11e78` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1830` | 28424.0 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `a17054497b3b57df3013d49e011e54ae4d4c4f3503ae35e7ee2acdf8eba67cfd` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0311` | 3391.5 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `68637261a9cf17591b15d208bda91235c45134aca46d0d5aad11e13ef75ffb65` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0793` | 20180.0 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `41a11d06a4eef3f8522cc9f56d6b94e195bc62a65183c5379dba694bf911c48d` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0246` | 5313.7 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `c4690ed0898343b04831e659c3c3518afef3886d104463d490113f991e4c2f96` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.2002` | 24354.0 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `3a2aa3d10c40f68a8f4b921c377dab6b0fb679543814267992ffec1e144a8c5e` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0415` | 3429.4 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `726240edb48c2609808d338f8394d920375902e2a05d01805039808dce05fb74` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.1838` | 28772.0 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `9fca21835e3702f09e8b803a90fd6de2f8a4e639ee31139ae75abc3f7cb167bd` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | yes | `$0.0449` | 7576.8 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `94b5628e786649497e2aa7cce699070d6e3d8488477da2f41f90f38937550890` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.0199` | 20441.0 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `f518f8064c9472a66cab8b649bfc15925b5ab1d7dbb1282f627ffd57a654a6d7` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0060` | 5451.0 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `3566fcf88ed1e894e86b1bc7a5a54c593f8747d36fc76c005639aa5a8f1e7094` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$1.0123` | 24658.5 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `65b0b4019d2c75dab8e155023a1a4ec8321e6d92d9fb3c77907fa203f0b0adf1` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.2107` | 3480.2 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `220923485d5bf0e5b614c60e0a934918e13efa141f57ee3f4dc7c76de6e8d02c` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.1867` | 29120.0 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `45e90d976674f9f0bcf5a10cba1b576bf5dea57fd765df2e413cd55aad0a6990` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0315` | 3499.5 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `10b445503dc2bac33fd63ed64417ef5c7261b9ce3d0490a7022185fec671b62e` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.0802` | 20702.0 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `1f4d7487d8c6115e40fa8d46ac10fd599331c32bb11130414a97836abc35d967` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `quadgate` | 2 | FAIL | no | `$0.0259` | 28183.2 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `447d4f48a929b0c4ed382f15875b78cff6d57beebe6da0b0e3506b525ea74261` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.2028` | 24963.0 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `e3e7c3b5d436da2566f2bba6751d63b92ad9639fffbeac9408b5a8a12028df0d` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0434` | 21701.0 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `4c3d8081d196add51cbd003f26553e09ab216885d6acfd9966292a5075ff6552` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.1863` | 29468.0 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `3ee7a58d06b49885a2a0a3b87b2b2e37b4290cfdc1523f1f770fea8253a7fb90` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0322` | 37956.9 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `f8c76ac81311afbc633aa8f3816040cffe4f2a5cef175a9d42443e8654ebcdcb` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.0201` | 20963.0 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `0827a8802a04d9330818db36145a382bf64b90b907198c99b03c9c1675513ab2` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0064` | 6858.4 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `445dd29a59676ab9398557f00c5f7bbc70da6a6b42fd37719f3f7b6730a8e517` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$1.0252` | 25267.5 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `3d3d332a1465bd0ef4146958b0c637f76a211958077c3a023060f615f716c82f` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.2122` | 48038.3 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `52a03976994c2585ab2dd5a16ad70b2e36b3a9c9ea4d883246d10478e60930ad` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.1892` | 29816.0 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `b5b08b100ae4331ef05c1752848176a1fe3057e45f91aa28f5c169b5d4506b15` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0330` | 51648.7 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `fb62382f78693ca74f9c6039d24cecb4a22395a94162ef64039c6c0250ae0d64` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.0823` | 21224.0 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `42b9edc2f111fe7099310ea733743e6e31765dfcf2d9d76b4b13c6e6be8b224c` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0258` | 39933.6 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `df4e6353d1f79013ff1cc3adc563e62aa9b65c36c2dcfca7ddbb71d2f3c35153` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `baseline` | 2 | PASS | no | `$0.2054` | 25572.0 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `b641992889bb3792eb4db685283ff347a30850f00265dc0c591c54e218bc16ce` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0433` | 14256.3 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `7ab2d80550a001dbf33027aeb8b13b2869a986aa5506a595d4904f00cba68aea` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1888` | 30164.0 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `ce6aa67fb7a79e1a3cf45008fa0fac1b4a1f94f67c346d228c608a91c26b61ae` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0336` | 16959.2 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `fda7cab103c3869109de7ee4a7597a8c4781e8516f91884798b81213abaf6b4a` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0203` | 21485.0 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `7f9ad59ba2fcce2948f8e26249ffc0c9cd61b378025696ed1faf3f332bcba6d4` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | yes | `$0.0069` | 23786.3 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `76baaee03fd8c2abf6a237f2447fcc5e69aaa4c194c1059e5422aeee8836834b` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$1.0382` | 25876.5 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `2a775cb531614739725eb253cc948e526144fe6e19cc8b87fd46b4b5fd93af0c` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.2208` | 17725.8 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `27cbcceee3c067b723bc3615af2f60ce249ab2376ad700fcc45b3ed3505e863b` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1917` | 30512.0 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `ae2975ac21554a321b9c3c1bf700b3c1b46523974523e3dbbb69299752065061` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | yes | `$0.0482` | 20696.0 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `cdbda959a4260075e75a6c6c98055251a53c00302de6690d2871a20498c3c533` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.0832` | 21746.0 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `a094a1b47fe74bab329d2293d0d08c609366fde929cb888c7b4b3360a87feb6f` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0259` | 17232.6 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `56937424114e76964c15bebadb1e4dbb26ff526646f55cceefd597c58d0c243f` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.2099` | 26181.0 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `dc737b50038af3a71b8b25fcbc335a21cd0bc2df699dc2e93734be7363b9817c` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | yes | `$0.0602` | 16087.1 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `fb46c66fb362aa6b07df908a999f2835ada7ead0712789b205525ca8cad41a00` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `baseline` | 2 | FAIL | no | `$0.1913` | 30860.0 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `ad0a817dd2109a826360c9c6229860e4612343a79a2d2691126ca048514c783f` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `quadgate` | 2 | PASS | no | `$0.0341` | 10339.1 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `41f6848ca9239e46e5ffc870e4921b96eb121e8d8a7cec6966de18eb4819312a` |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.9640` | 22831.5 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `c2001f71623e31e8557219926c7a9fdb429c78c6579e49ad7c48638d6073b6be` |
| `STRESS_AR_01` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.1949` | 7652.5 | `273257226cb9bbd73085eb66ab7ee89d3ae4828f34020a4fd3bf9e2190ad0ccc` | `61b61ddb8705ca6a23ab13613d92c0393f23ceb6521a897ec81a7a4150d74762` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1780` | 27032.0 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `fa271ae760879b671778ac435e49eb8e4aab6e19e67bd7eecdd64b1511efc91a` |
| `STRESS_AR_02` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0300` | 4784.6 | `fd89b5b8c37e8511a99416b1ff0ef10332adb41635d3f59a9b6c481141230c3a` | `1aeafe7da385a0f9a4c4c102aaab240e909d837df73b4e3e4f955580b21e56f8` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0776` | 19136.0 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `9b3f1761a16e6ad1a3ebcaaba09fb7847000bb592de0a3936af5b3940b3d3ffd` |
| `STRESS_AR_03` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0236` | 6886.2 | `3b3ccc76727d2418efc0fae8564d2951cf83a03895647c6ce2ba118fbe944746` | `15e1b3497d5e0c0886c37b6f88a01389fb4c38fd0a55a6f8ed584ec3bf269538` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1950` | 23136.0 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `553b904490de6470a92e8f055bedba9f4d87b41a5994ffa41ac4a4aea4af0b4b` |
| `STRESS_AR_04` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0397` | 4972.4 | `e822c4ad9b9de8f6f941d7f427c766bdeedbb8a8669ccc6ea8d0bcac7c24a6d3` | `cd877377e62afc0b2b382d6dd96362c2dab2920a44dd25f6069f0ebaf9932ac4` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1788` | 27380.0 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `27e4d4357cd8376967ad47deb6a45d3341aed43184216213a4a1d13e5f14719c` |
| `STRESS_AR_05` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0295` | 5166.1 | `8a3d0c79fe65fa9494dce8b631456c31484ea45d59ca87390b3f26843d6001e9` | `d3af0337a46ba6beccac6f83eaa5f21a7846c867b5d5e9703c11e939cf031fea` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0195` | 19397.0 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `7be094183ef8e94f7561e5e87e7d3ef06665cada0f7a6ad5f467afd792d78908` |
| `STRESS_AR_06` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0062` | 9157.3 | `cd1131a9baab6b64fd16d7a699a9230689b208a4903539740a87a8a9c5c2d86c` | `0aaa57d7a2fd8eec1df129a537a087a34e6a35f60615f1a5656ffb91be5e0f8b` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.9769` | 23440.5 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `6cf54f7df032caaf826e1e210e85109ae1df40437c5331c3f0af4b9370690791` |
| `STRESS_AR_07` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.2031` | 6787.8 | `41ce2dabc372ce4e4fc6fdfa8f935c8813d4214372606581c4f508cb36438752` | `71237df2bcf532eec9a4e86b3cf77aae3a7ed0071613b8607d348650295c9142` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1805` | 27728.0 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `8d6d191341d0477ba9a14e84f44d868b15fead754c258a29bfd75468f7767234` |
| `STRESS_AR_08` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0301` | 6594.9 | `1900516ef3a33be66af926eee8008ed38dd8a1de674218f845bdfac0a974f04c` | `e64802d8e7933b8f21736b262f7b9b9bae95d4c662fbef1e62dc1169026f1bd8` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0785` | 19658.0 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `4677db207144ff95504b2a9e782cda49a402ed66b4028fc51f96f0bd3decefbf` |
| `STRESS_AR_09` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0246` | 7246.5 | `3c4df79d22e2c25398213c4b9c33db9548d27a0a7b96f742dc2ef2ae2ef74675` | `0d9aacf0cd1cc998a1ad1a737ea6b16360c08d9da5aaec90874f65de446492ea` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1976` | 23745.0 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `5030364c8265cd439bdbe87c80cadb88447d6f783f3300f1e69019173e2dba02` |
| `STRESS_AR_10` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0400` | 5752.7 | `d489b601437784241a04415338b474b54238d808b55213b44116afe87fe57ab7` | `9d710a9a1464db4cb1cafdb7db2e40f9f28b775039b8b114804641d5bb0bbe71` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1813` | 28076.0 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `935ce5d08d81c9c655e2903d7defd4d65c4c3c66a7ba9ca1192c002d44ef2752` |
| `STRESS_AR_11` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0308` | 5299.4 | `2705bf7fea7e33eca619d73eda52cb7f30dd17df8f45f876f1243593ce41c55d` | `f6b07e52328f38757d7ddee23db8d11b8e92705fd7fe2cfc2cff83dd151773e5` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0197` | 19919.0 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `72eaea5c9ce270386d173ba1cbf2a48fc885b1cf9cc090fa764989967d8cb9c1` |
| `STRESS_AR_12` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0062` | 8246.4 | `a9b97fa668441516a10125d613f06e53df5e4f08193629ff242d6269d3049c6a` | `61e33a7698f03b8aa370b69dac9d8fd0ddff1114adcc1f05e5d10b313472c965` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.9993` | 24049.5 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `1c495b1da9f52eb3cb0843c6f90e015f42da504526a0a75ebb2f19632edabdf0` |
| `STRESS_AR_13` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.2023` | 3388.8 | `9271add9001d91f96208f968e431f1ae99a79fe12a317db3c3ffdf9c0cad45b4` | `992a98d5cedc405899ee4f23ceea999fe01ae92a4bd2ef4b47345af9592adf65` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1830` | 28424.0 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `dc1595ba36304638a89f3d8e9b061dcb9cf9050074b73db7d603f07ca1899016` |
| `STRESS_AR_14` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0311` | 3393.7 | `5247515578587ef15d9b1a8797c7a164b37bd6d96d43d912b007235cd00fc82b` | `8fa33865bafb724e0cbe1b5d44a8ff89e4987f274a7f6326b967feda8af10a5d` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0793` | 20180.0 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `1f4e0df2b730254986c2f1fea76c72a97be63e2dccb679c231a26ab72b82178f` |
| `STRESS_AR_15` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0246` | 5332.3 | `a6ad4edcc53509b4ec15defb569a792a927c57de97325e6df0ec0dba56a2161b` | `a9cb79e617a5cbcb6576f9fa60b289a56afd0b2ff83fff7a23cab301dd452bc6` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.2002` | 24354.0 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `32e9176aec688d698a35b85a55fff7491462eaa4a8fbdd92056c956461cd01c2` |
| `STRESS_AR_16` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0415` | 3446.8 | `4eed308a1c08b8b4b25752cd693df7731d3652cd6ca3410e39070918709d7703` | `f49ecde5ecdcb238b63d7ef87634cb5f2c16bb73cbb9c0e1e7451c3ef89a9245` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.1838` | 28772.0 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `f05112cb1724d52777984850ab913b480d32ef7e73548b135220934821e281db` |
| `STRESS_AR_17` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | yes | `$0.0449` | 7183.8 | `7aea2607dd86ab4edd4d024a221cbddc83d9d49a967a22f7ceecce159439d6f2` | `a66eb4d6dbce303eaa55fa0c17b4f4c07846fe1095b07a2ca2d42e2859ace115` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.0199` | 20441.0 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `00740c43a78ab02b6985f31ad5b1970bad07c14084d9f661c4a483af54a5e020` |
| `STRESS_AR_18` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0060` | 5422.5 | `029427ad5c3dc1aae964487f75a25e8acb4bd909f4fe7456e30b3cb4dcc04792` | `1b352368e2ecf11cf88b221cd1e2a5419176fec83c78a1aed8e64cf5423d464b` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$1.0123` | 24658.5 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `e7c546e25b97b279cf40715036807e25bfac1e0f324ba6e5be1b18cbfbc1121c` |
| `STRESS_AR_19` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.2107` | 3493.1 | `4523691eef1a7033176283404df82ebb3fc01f555175b6b951bdd81bec8a58aa` | `c771dc14aff8eec38e9a889bea46896ab110dff73fa0bb4dd6c033c09ef124fd` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.1867` | 29120.0 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `37f506c4c92059848472191e0205a728d40e44ecde584fdd48694f182a5e744b` |
| `STRESS_AR_20` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0315` | 3496.4 | `9351c7e16072c62754e9dfd9d0d5f70a3a5b6998d49db224328e1aa692ec364a` | `3540669e3390c8c70c8a4bfff5f336754028c1890f2da218a133fa4955ce2b8c` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.0802` | 20702.0 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `45c210f5d44b7845ef44b8e33c01e17cca0edc2ed206f1ca8f9d9a277759f8c7` |
| `STRESS_AR_21` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0259` | 6709.3 | `fcf2c1f3d700d4759d281a93d53f527a077f81207ad0aad30a74961a2dc792e8` | `f2c45c96cd1c18474649e2d04e42731bc14eb48e387a47773206faf1358d90d9` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.2028` | 24963.0 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `cf92e6e80d923560a388d67ca8f07ddf34545b8939236fcaea501e1a73ce706e` |
| `STRESS_AR_22` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0434` | 12410.1 | `89c0cfe36089c5f2cc08843d9d7172ada1e3c37c9298a8aabf59fdb7a18aa0ff` | `b651a387fa58fb4ffd59048d9d0d0678c1b0dadbee763130611cb59ca3258bc4` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.1863` | 29468.0 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `556ef9e30f3c932563554a72fb8424360a1e12c15fc33125677141b661127c88` |
| `STRESS_AR_23` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0322` | 22218.0 | `556e48bc0a4e9a023a46abb32220fcf58489ab687c5c3acf6a54693fde2c89bc` | `caab4ab98fc9d176cf614168544f5d3da7e8c4bd12e124a0e991e48a49e1fcd7` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.0201` | 20963.0 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `bd96281b5a90c8e7cef6494d4878e5a7f0b6acac5f0d572a0082b58a26728c3e` |
| `STRESS_AR_24` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0064` | 13287.5 | `d0bf9ff1d3dfb534a99196e0c98ff5a74086e31fe9e1e7edb758507717b21693` | `d9ae88039018d122324c33e253fd649242a6dde14c9039d66b0f0ba3541dac41` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$1.0252` | 25267.5 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `86f7f4a311386ed7ba341ca2871151f63db62eddf140ace49d73e4ebc60ab389` |
| `STRESS_AR_25` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.2122` | 26222.4 | `c5ef57976ac7e0694517cb34edc3b3003616f8c67872f59ec989c5947e58b429` | `f0f612420cefb4045731ae22b5e16e347c67893a9ce63e94ac3a8630744fa807` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.1892` | 29816.0 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `744ca611b201518902ad9b449628a8191de4b9ee54f0213e96bcfaa916044a29` |
| `STRESS_AR_26` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0330` | 24213.4 | `ebb1ebd6c25598686bcd49b9a98e058f84cac09a2944e41932da1793af4015d6` | `be4dfe090656c32b8587c64832d6bbc02985b29d292a83955657d305e00e4f7c` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.0823` | 21224.0 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `b38b93ea531c9af0c47e64c15cec87931620bd2a03562fa7e77cf61db36c2e42` |
| `STRESS_AR_27` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0258` | 26671.4 | `c5a4465ba4456c4b26fc8edf9f133b9ce8cc958135ca4858f6b6676d8a0a8711` | `3d7e7e0098304c1f79e1be4bb8a199d50d33cff68e5e80d06a00322e4e405061` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `baseline` | 3 | PASS | no | `$0.2054` | 25572.0 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `cebd45773057dda68f1903baa47662d29476e3828703dd305a64c1fa9189af22` |
| `STRESS_AR_28` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0433` | 12494.6 | `02c38acf912ad326f78007e40feb4c106f72a3f4a81da35fe1a96a2417ffe8e3` | `6394dcfc2bf621a6270d6b85d7150586a21c45e75656753e25a4bce3d6c062a4` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1888` | 30164.0 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `a560c0e62bc734277c517cfc88517f5a798b2ea35be9a2656305d04a8e6e84bd` |
| `STRESS_AR_29` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0336` | 19559.9 | `11f516ab0fd7598f6cfc457b3b01368cdad407d1d1bf2fd3d499fcab424f52bb` | `f9b49683ebbed14b001b5e2687a604d7b4711a4c52daaa81e6de8218a6bd9788` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0203` | 21485.0 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `db3301e5c9813d64278d3fddd47ba0a24c53a161c21c7fb994f6b38017e82e63` |
| `STRESS_AR_30` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | yes | `$0.0069` | 21918.2 | `60f5cacfe4f2a73413e9145d0646ba369d06234ce492f1933660057d18c3afd2` | `33f1dd4a1a04b664b1c95b46a9023a5d3081d1b2094e3c4a4893677194c98eeb` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$1.0382` | 25876.5 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `c81bf413a8f5fd36c889f7c0183f4d795a68b074b9953e260e607d98dd05bf5b` |
| `STRESS_AR_31` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.2208` | 17228.8 | `987622d6e8c0ebe50013428ab39a50d4d97237467d8b565d57a07f6e016ed2db` | `d11f5663a6acd9cfc4777910872278c72141ccd076b58fef38b0a2ec41aadf51` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1917` | 30512.0 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `c821ebd5199a15a250c862f9d840b6163ffbdf83b25f3f3dab01ba2572691685` |
| `STRESS_AR_32` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | yes | `$0.0482` | 18341.7 | `2733bc16bd0467d5a7fc584f27e3fed50abe3630b30c09504e685e6c5bbea295` | `d083103390145c3e87cbc522e0fcb3641f6a9c2c20cad6ab9e88d254932608df` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.0832` | 21746.0 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `6039919e906f2bd58eed56071709d56315b117076604b2ca1f6a353b5d91add0` |
| `STRESS_AR_33` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0259` | 11260.2 | `08bd64cf0a8ab66369f10c8819fb5a53354cf355c68065b5f3cd0616f775b2bf` | `ec73da97fbde9109102c0d35f1102563212b17e64285b5b2b490e4a315a916a8` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.2099` | 26181.0 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `8e78c95b1b2116c81df84db26b86c1df0ed5d8487bb8381fafafb92e4c356657` |
| `STRESS_AR_34` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | yes | `$0.0602` | 12619.4 | `9305d488fab8bb97c4892a6703b3c507dab518443fc95ed8382fceb554c9f5bc` | `652a5782351ec6e43307e185b5d7f93f0da4cf8b7bfe5051977e1cb2d353f1e4` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `baseline` | 3 | FAIL | no | `$0.1913` | 30860.0 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `b65f8f97efd0a0db40f5abbcf3b6215bb9b6ac71837055cbf8ae0e8fe39eb176` |
| `STRESS_AR_35` | `stress_adversarial_resilience` | `quadgate` | 3 | PASS | no | `$0.0341` | 6124.1 | `fb556acae03eb1b1f20614fbdb64ce137d82a34707fa57776cf1d1652a25cb13` | `a067a1a83306527439a837bb0ca374964c3556aeae81038d8f09226a6415a3f3` |
