# QuadGate Empirical A/B Proof Scorecard

- **Generated (UTC):** `2026-11-17 20:15:56Z`
- **Total Receipts:** `210` (`105` matched Baseline vs. QuadGate pairs)

## Executive Summary (Aggregate Baseline vs. QuadGate)

| Metric | Baseline | QuadGate | Delta |
| :--- | ---: | ---: | ---: |
| Runs (`n`) | 105 | 105 | — |
| Pass@1 (%) | 74.3% | 100.0% | +25.7 pp |
| Uncached Input Tokens | 5,842,464 | 806,580 | -5,035,884 |
| Cache Read Input Tokens | 1,967,700 | 2,064,759 | +97,059 |
| Cache Creation Input Tokens | 193,413 | 350,475 | +157,062 |
| Output Tokens | 599,970 | 171,258 | -428,712 |
| Warm KV-Cache Hit Rate (%) | 24.6% | 64.1% | +39.5 pp |
| Generative Cost (incl. Retry Tax) | `$36.8723` | `$7.0906` | `-$29.7817` |
| Classifier Cost | `$0.0000` | `$0.0404` | `$0.0404` |
| Retry Tax (USD / ms) | `$0.0000` / 0.0 ms | `$0.2002` / 51969.0 ms | `$0.2002` |
| Total Cost | `$36.8723` | `$7.1310` | `-$29.7413` |
| Wall-Clock Latency p50 / p90 / mean (ms) | 31434.9 / 44000.0 / 33163.0 | 15110.7 / 31654.1 / 16890.8 | -16272.2 ms mean |

## Paired 95% Confidence Intervals (Baseline minus QuadGate)

| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |
| :--- | ---: | ---: | ---: | ---: |
| Total Input Token Savings | 105 | 45,540.6 tok | [41,760.6, 49,320.6] tok | 59.7% ([54.8%, 64.7%]) |
| Total Cost Reduction | 105 | `$0.2833` | [`$0.2145`, `$0.3520`] | 80.7% ([61.1%, 100.2%]) |
| Wall-Clock Latency Reduction | 105 | 16,272.2 ms | [13,977.0, 18,567.3] ms | 49.1% ([42.1%, 56.0%]) |

## Release Readiness Gate (Four Release Criteria Verification)

| Criterion | Target | Baseline | QuadGate | Verdict |
| :--- | :--- | ---: | ---: | :---: |
| Quality Parity or Gain (`Pass@1`) | `>= Baseline` (`+10%..+25%` target) | 74.3% | 100.0% (+25.7 pp) | PASS |
| Cache Integrity (Turns `2..N`) | `>= 75.0%` `cache_read` share | 28.4% | 95.1% (+66.7 pp) | PASS |
| Net Cost Compression | `>= 60.0%` total spend drop | `$36.8723` | `$7.1310` (-80.7%) | PASS |
| Wall-Clock Acceleration (`p50`) | `>= 1.8x` median speedup | 31434.9 ms | 15110.7 ms (2.08x) | PASS |

## Per-Suite Breakdown

### Suite: `stress_long_context` (`105` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 74.3% | 100.0% | +25.7 pp |
| Warm KV-Cache Hit Rate (%) | 24.6% | 64.1% | 59.7% input saved ([54.8%, 64.7%]) |
| Total Cost | `$36.8723` | `$7.1310` | 80.7% ([61.1%, 100.2%]) |
| Wall-Clock p50 / p90 / mean (ms) | 31434.9 / 44000.0 / 33163.0 | 15110.7 / 31654.1 / 16890.8 | 49.1% ([42.1%, 56.0%]) |

## Stress Kernel & Security Diagnostics

| Kernel Check Category | Passed | Total | Pass Rate (%) |
| :--- | ---: | ---: | ---: |
| `airgap_zero_egress` | 45 | 45 | 100.0% |
| `gate1_cache_turns` | 105 | 105 | 100.0% |
| `gate3_tier` | 21 | 21 | 100.0% |
| `gate4_incomplete_rejected` | 27 | 27 | 100.0% |
| `pass0_recall` | 105 | 105 | 100.0% |

## Methodology & Honest Accounting

- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.
- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.
- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.
- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.

## Tamper-Evident SHA-256 Receipt Manifest

| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |
| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |
| `STRESS_LC_01` | `stress_long_context` | `baseline` | 1 | PASS | no | `$1.1580` | 26069.9 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `0f4b0c96843ee1ee6cdff4057e1f348153fa1cdb74c1fea6c1ddd77b73568255` |
| `STRESS_LC_01` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.3074` | 26915.1 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `995f34d841ab7a2979a633795f7b493a49db8e30c5df492173e8274cd64e0f10` |
| `STRESS_LC_02` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2153` | 31823.2 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `9e84d1477cdf3ded0c099a8b96d006e6383856e54707a53af15d26a1b590ff7c` |
| `STRESS_LC_02` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0789` | 24729.7 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `1e17fee6fb36b4d191068656ad5c0ab08de6181bd8ab3c2cea6bb923c67d554a` |
| `STRESS_LC_03` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.0376` | 38985.9 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `51f46125edd23118bf21716bacecd1ef31ed787306e366c11061dfe2d3343dc5` |
| `STRESS_LC_03` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0065` | 15870.4 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `a0071bd691d8e888a7b4e9ac31e6311cc214c40e7f8d933db3fa324845018384` |
| `STRESS_LC_04` | `stress_long_context` | `baseline` | 1 | PASS | no | `$1.7985` | 45674.0 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `ec458cd3e6b266909c1a7a05e8d3ed634cedaf25e6d102682c9e3862ae50c2c6` |
| `STRESS_LC_04` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.3485` | 22157.9 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `cbc1c65b7e298bf15d0e8a3ab4cde4c18ad90d12c012a1de20c2658a5789d63b` |
| `STRESS_LC_05` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.1571` | 25692.0 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `8ba8b1cc609f5a69badd1d571d183765ea3dbdfd07f4081dba1b2d8e600e91a8` |
| `STRESS_LC_05` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0875` | 17276.6 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `16a38f0d20c6b7c6fe47216542c8f4f06562bd5596a92d7e5c0e36e6350de470` |
| `STRESS_LC_06` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1092` | 25560.4 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `b337bdde0478d8b4885b8bdb180a712e72d2dd38b03bca3839232ee98799e942` |
| `STRESS_LC_06` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0061` | 14679.4 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `6d06199eecde0ba8eb8ab7ac17e45b004d54d2b82205b06faf6f2fb2d2cfe484` |
| `STRESS_LC_07` | `stress_long_context` | `baseline` | 1 | PASS | no | `$1.3565` | 31896.2 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `d630661208d3094029bce420b92ee66e5c7581a03d2594f57abdf92630e57aab` |
| `STRESS_LC_07` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0619` | 19265.8 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `a6ba323bf13ef5d08c1b43cf868007da78ad4b5b55df8e202b237ffcf04f05f1` |
| `STRESS_LC_08` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2449` | 38309.4 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `2f5c2e7c2b126b7c6446ffac4b0697623362c628466ff9d1d0dd1ee25445b2fa` |
| `STRESS_LC_08` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0925` | 51733.3 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `95eb2d46bdefcc3451aa63c04dd8a2f91f14bf1e16e392aa7105c81b7f7e31d3` |
| `STRESS_LC_09` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.1765` | 45748.0 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `b0bb60c0f945a048cf4119679a8d8e05c91baed2298665dc5a1a2e261168110f` |
| `STRESS_LC_09` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0068` | 32675.0 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `e29500997f9119394720695c64cc07f15d59deced37f936629aec466a21eeb19` |
| `STRESS_LC_10` | `stress_long_context` | `baseline` | 1 | PASS | no | `$1.0078` | 25344.0 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `a888e0a55ed72fef71a0fa4e8520396140a7bd697a4d5cd7d54ed5766e1cb07e` |
| `STRESS_LC_10` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0688` | 42893.2 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `26935286c8939b00dfe87ce687ffb3182442b19b461d0edde5cbbc389884d33e` |
| `STRESS_LC_11` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1099` | 25254.9 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `96fbfd2888bec993c6c39bb3ba39b97307c4271393d8018a8cfaa1d6cfaa4721` |
| `STRESS_LC_11` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0030` | 40719.7 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `4be1c242e50be1945415c840ab2c1b7515510d97e4b0dfea1fd7e6dff15c2de0` |
| `STRESS_LC_12` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2769` | 31885.2 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `a62479316b80dde400def918e13b9e02374d3ace0246802fa13dc9c01768af7e` |
| `STRESS_LC_12` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0571` | 15548.6 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `da7d150ef935fbaf2d7944a7b1ec5ea2a64b02d1ee034180f592375e1922753f` |
| `STRESS_LC_13` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.2462` | 37632.9 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `429d602fc5cce95b897e7290c23b407c17aae2c15e47f4b2a2ac2c2a4e39757b` |
| `STRESS_LC_13` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0607` | 23071.3 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `552a929e518ab39786f194d4d691c67b60935d31c6bd1f19ce52ea826ef9d29d` |
| `STRESS_LC_14` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1679` | 44634.0 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `7785d9b8a96c9b198f18b6bc83a2dd015f628cfb612b58b5fd997e1de9a5b5ec` |
| `STRESS_LC_14` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0430` | 29285.1 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `bbe97cd1fabd4b1a5ecdb9fbf3a966b9d3e3d4ad18a2169863b3927afaea6d4e` |
| `STRESS_LC_15` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$1.0308` | 25248.0 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `d46c3f476b57c0c94815fef823da882452255ea23add0d3f42e2edf51612ac1b` |
| `STRESS_LC_15` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0158` | 23077.4 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `a2e7d946456e0e90d7a40466cb5be073ec98ce943489323c2f34466789096eb5` |
| `STRESS_LC_16` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1726` | 25237.4 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `ab6404c5c0e43512cdf11c589421b54eead6c4dee1eb1c9cb0dee37a63123c47` |
| `STRESS_LC_16` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0559` | 21331.5 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `ddf06210afec0703dd645d76a68ee3cbe2c5bc2687de4f683186c595b2268c3f` |
| `STRESS_LC_17` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1298` | 31166.2 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `45a201fe0d2ed740422195cbbe2978bfe62a1a0170e35e2cdac9de6bfd765c86` |
| `STRESS_LC_17` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0419` | 22861.2 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `9d7a7eebf0a1eac6d5a1246c4c8e0bffa2b98573d6fedcb941f4054c64ea57bb` |
| `STRESS_LC_18` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.3216` | 37664.4 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `7629f878e5f3629805a9811e7cea339e18e369c4d0170cd03046a05c51f1874d` |
| `STRESS_LC_18` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0534` | 17922.5 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `e4d12ec34c39be4d1e89e14251e4675b603356134248c9bac60e09cf2b284edf` |
| `STRESS_LC_19` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2849` | 44000.0 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `a635eff6d590a1d78858bf97c825d1088ed6691fa4e4ed87e578e58ecd212cd4` |
| `STRESS_LC_19` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0629` | 16553.4 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `ac7e3d94926e343b4c62e338211ad7e079a01f67cbc0a47edec15587f8f2edca` |
| `STRESS_LC_20` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.0955` | 24900.0 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `f558fa1f07565b0aa867b490891deb2229fe1d9ff548950d9bde84d0f39479e8` |
| `STRESS_LC_20` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0435` | 41724.6 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `eef5f2b0f2ac3acde18fb2ec0a79e5151138c244ecc43b50fa24a7a343aa7436` |
| `STRESS_LC_21` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2498` | 31434.9 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `d05c789a52cef1bc41b362d062304b39dc7f50550cba786d210ef4f102656336` |
| `STRESS_LC_21` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0645` | 4114.0 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `da94964a0da345011d8f09dc2fe58e4f6f9d4a95a3bd679ad7db6f70780eb367` |
| `STRESS_LC_22` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2178` | 30447.2 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `720ff54b4ed71ccc58b2bbe55d2b19f6ed6df0f3c4540335fd1df19c16e016e4` |
| `STRESS_LC_22` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0624` | 6571.6 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `5ef00195bbc0515e4ae4de65aaf675a61f1e3f3856f4c7492cc3aaf03c25d5bb` |
| `STRESS_LC_23` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1502` | 36915.9 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `c4acb37dc269dd611eb354acc5287a5ff5a989a0e4899fa98dfafcbb6ce0a4f1` |
| `STRESS_LC_23` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0424` | 8646.0 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `d5ef2a50a30d56cb3fb90c192db65e00d3fe83a6edf25775b69982916c96584f` |
| `STRESS_LC_24` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.3578` | 43618.0 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `27a1c7345f94711cad4bc28a747cd7b916e06c7486a051f69258316fbab3ce66` |
| `STRESS_LC_24` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0662` | 32252.3 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `1135f2ef62d82eeca40634110a6726e856d4397f8af4924906a09b745fc65f99` |
| `STRESS_LC_25` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.1621` | 25044.0 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `bd8903b97f551aa3c3a03808ada87d381f3763898c3d8e720e325ca845c05ca8` |
| `STRESS_LC_25` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0696` | 6144.8 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `48c64e8c5e4a73202288d2344d4757716711e491cfe3299a84f9a6b790e8e1f5` |
| `STRESS_LC_26` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1199` | 31129.4 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `ebd00a80595327350d420aaf4a41bec309cb15f6ef5a1134cc3957c1d1c5a38a` |
| `STRESS_LC_26` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0462` | 9051.9 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `67daf319ab0a25249322201ccc3e0c511a5ebe860644da1abb6f2a735bb90ac1` |
| `STRESS_LC_27` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2806` | 30436.2 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `22af1383ac23e4cb6a81671c8fe90e91178a70bd893b7d54e51c7e5b2ef70169` |
| `STRESS_LC_27` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0561` | 4645.1 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `7016b44279584a56f13ce6bc73ce0db44d0fba9067df143828c85deff868c4b4` |
| `STRESS_LC_28` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2449` | 36491.4 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `86bc77bc19d75b5ebb1bb4e387a6a6ba9a14c65380043e8b8c3c5dd120f13c5c` |
| `STRESS_LC_28` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0726` | 32096.7 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `0fe243a7084c8805ea024727be5af68b63b0d02036e95d27700ac464378576cc` |
| `STRESS_LC_29` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.1723` | 43440.0 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `beed7e978645abdb2bed5208d03d1fc157dd45bbed159a82969cb455d5b4a2f2` |
| `STRESS_LC_29` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0449` | 9564.2 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `dba70736854605216e000b613dd05e3e607195c1a7ad2477e101e7edf529c988` |
| `STRESS_LC_30` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.2081` | 24696.0 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `f854c08bf2299d0d8f358b897a078921117981bc038140f2eb9870ebf17d0a99` |
| `STRESS_LC_30` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0622` | 4320.0 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `049cc1848f85f99d4e5e220933850f0dc15519166ba994a885da4c54a3f89cd1` |
| `STRESS_LC_31` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2012` | 30619.9 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `c7930e2b073dc8102d27a16cd0e2ad27877a96a545d93f36e7247f22bce15c04` |
| `STRESS_LC_31` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0701` | 6733.7 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `50c7eb77c0740f7a1e6b47539a5a2011fc5abcce511b93bf620d672f5296dfc7` |
| `STRESS_LC_32` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.1286` | 30053.2 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `c5a716ecc833c132e2a85c4bcd27c44e9964ec98f5b40e30cfc3f3fa6d350572` |
| `STRESS_LC_32` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0410` | 8291.0 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `17fa752d91fa5385f2ab01822d40d14e5735f214c79b6f66ef1657df1a6187ec` |
| `STRESS_LC_33` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.3109` | 35814.9 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `91e84d43285706e4c4f78d139f891247ba54f6fc8b2b331b70f9f265bc597cc8` |
| `STRESS_LC_33` | `stress_long_context` | `quadgate` | 1 | PASS | yes | `$0.0642` | 26669.7 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `f4f749e36fec7fe2193edf9067a13f90ee2015f345643b50b1e8cf34139e1f22` |
| `STRESS_LC_34` | `stress_long_context` | `baseline` | 1 | PASS | no | `$0.2901` | 43490.0 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `e3c79ba34c87df2dfedaf1e9ffa1008d4ac7411a6d8558b02f36bde40c8f544a` |
| `STRESS_LC_34` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0676` | 6458.8 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `4fcdf1d85be573987c761ec07b7a9a566ebff3017c30d661f1dd71993ef2b0cd` |
| `STRESS_LC_35` | `stress_long_context` | `baseline` | 1 | FAIL | no | `$0.0989` | 24348.0 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `6debc80aeb7a05763e11a3222eb8912e3fba3eab01451729f8d895af37becc18` |
| `STRESS_LC_35` | `stress_long_context` | `quadgate` | 1 | PASS | no | `$0.0448` | 9926.6 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `e45b0ee0e4d40bc1ec71413396349288ffe313d81fb340cf84542e605005daec` |
| `STRESS_LC_01` | `stress_long_context` | `baseline` | 2 | PASS | no | `$1.1580` | 26069.9 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `261dfa525c6058cd9d75ce55514b700634b92244c4795215f07a6cb45d106b4c` |
| `STRESS_LC_01` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.3074` | 33140.0 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `22721e331205ef09f2d15111138dd5fb641a6dffecf10dbd7cd3d321dee22fe4` |
| `STRESS_LC_02` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2153` | 31823.2 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `dbdf607b47bf48f6cbd041be77864004033bb3152198c38fd5212098b22ed5ad` |
| `STRESS_LC_02` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0789` | 39221.8 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `4bed569c31cd5a56637b955ed9aac152cb0df28a99b2190651b81e8a6e354131` |
| `STRESS_LC_03` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.0376` | 38985.9 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `4f8efc0dacf91815e20f0b66b100a0b3d2a8137fe0cd7022490892db61c2fd58` |
| `STRESS_LC_03` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0065` | 34668.7 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `889fed4812d695b529892f070059ce5eb85f221cb01183fd7a4665f93c407317` |
| `STRESS_LC_04` | `stress_long_context` | `baseline` | 2 | PASS | no | `$1.7985` | 45674.0 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `28b70a9fc95d0dc6a99d32f07c147f7a4584ce80ebefc7ccf59727857dddf4f2` |
| `STRESS_LC_04` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.3485` | 28709.6 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `a78c4babcb09d682da605a2bb413105d5b6dcbe17ef6ea9cd0351645d737b6ce` |
| `STRESS_LC_05` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.1571` | 25692.0 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `b06728f2f1b35c12b8f27f302db892bda1f5dc9609005a952330fcdc16f2e66f` |
| `STRESS_LC_05` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0875` | 24521.7 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `8b0626483c396915a590e56e2c203bf1df6f77968534736cf55049b1ca735bb4` |
| `STRESS_LC_06` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1092` | 25560.4 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `8d1885cc3e98647cd4e6cb84b782095e97916d90b87209eb9aa0f8032e0a5d0f` |
| `STRESS_LC_06` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0061` | 13963.4 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `eacfc498e8589f9368a3e28d7f8c62ec007ccd5f2c85f1c7ec793551183a8ea1` |
| `STRESS_LC_07` | `stress_long_context` | `baseline` | 2 | PASS | no | `$1.3565` | 31896.2 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `51d16b13d2a27dcb0e7e87f63bb3243e132e052dc19bea0b68b0ad076614a569` |
| `STRESS_LC_07` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0619` | 14054.5 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `a4d935e16fa92745275cb8ca174f2a3324de01589b0c08722ce4dd631d4713dc` |
| `STRESS_LC_08` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2449` | 38309.4 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `d46a07aadc8253aea1792b23a9bb881d054f4052fe811a77fae3c8590cbb1961` |
| `STRESS_LC_08` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0925` | 31946.8 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `098c511c9fb4026ac06a9b013ec27e5fea7aa1361677f1a563bc4f6924514e11` |
| `STRESS_LC_09` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.1765` | 45748.0 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `1ff3198e4e293936c462b8fc2e2bf37156c15348449201c47f19bff0b98aad52` |
| `STRESS_LC_09` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0068` | 22813.3 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `0cdc7ade80004c57c28c7fb964ce015151da81b3bac88c04daf73e8f2a5e3d83` |
| `STRESS_LC_10` | `stress_long_context` | `baseline` | 2 | PASS | no | `$1.0078` | 25344.0 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `92c84a710abac42a28d248d459e9c57aefbf90bc82807c3107926d23de4ee313` |
| `STRESS_LC_10` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0688` | 16473.3 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `30e804e4744250acaca2ddd2ae7aafd26332a2111aeca2d772055333b4fe9baa` |
| `STRESS_LC_11` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1099` | 25254.9 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `1a4215564b2f0fedfadc836a7e893d249c8b05b74a8c509fe150a928508ce363` |
| `STRESS_LC_11` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0030` | 26273.6 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `75bafa7ea2c3d416262a196209e7c1a71195d55b347dc5c32637e56ed1a92cc3` |
| `STRESS_LC_12` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2769` | 31885.2 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `6ad148890a6afe481e004794cb1937c6b814be5a4d09736cee5d4e67cfdb3961` |
| `STRESS_LC_12` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0571` | 25356.9 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `5bfcbc27a5cb5e38d7e8793917ceff9c694716cfe427523e7263c72e9f7cc780` |
| `STRESS_LC_13` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.2462` | 37632.9 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `c205b88c2abb070537dbbc82381331c4a08670ed1f0c8c538562f02d76f23286` |
| `STRESS_LC_13` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0607` | 15110.7 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `c75f709d661ec83c036ef340824c17ec76f8ac79caa48381351fa60b6b545318` |
| `STRESS_LC_14` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1679` | 44634.0 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `cc05400c8051a5de6b7d6e47cb956ae2e3e315dbc1426444e3dae62de535da48` |
| `STRESS_LC_14` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0430` | 23621.8 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `75764ccba7e8daf9945fa4d3cafa2ba09ce0e7a693a05cee0e4e53fca6a177b7` |
| `STRESS_LC_15` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$1.0308` | 25248.0 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `1df64194c9349d6d8882fcf670a8b4d5f769a80d60b7bc7cdbefb6799aae4c96` |
| `STRESS_LC_15` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0158` | 17554.8 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `07584e7320471b0f4aee7fa54378aa98e7a6225824f084127ff3164cfd9cf742` |
| `STRESS_LC_16` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1726` | 25237.4 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `06125022de4d03d302fdfaf6e1067d1a5fcaf75cb5d12ea7580a23d4c4efa6f0` |
| `STRESS_LC_16` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0559` | 16611.7 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `ad07a41f6e467f4089e5630bdf6df9bedb2c0d6639a74c18765ff3677e5d5f3b` |
| `STRESS_LC_17` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1298` | 31166.2 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `7c0f30c67d9a76b60836a1cc0ae023836fefdc81bf32aff9e8f10ee759178a3a` |
| `STRESS_LC_17` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0419` | 19212.2 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `cee7c1beaf091880febe9f235d8a0fccdfc0596e02215c5c3c06b2b48f258e34` |
| `STRESS_LC_18` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.3216` | 37664.4 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `54212446007577463aeaa63aabdcce9d34acfad79b9de7176c48972c3888110a` |
| `STRESS_LC_18` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0534` | 14206.7 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `eca201d5dead02a82662bac74e42f99350745a11bba684e0bd6685d61dce0a2f` |
| `STRESS_LC_19` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2849` | 44000.0 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `23aafa062131946e3eb5843fb02586f5cb5ec2a5d901505a840347508d3989b4` |
| `STRESS_LC_19` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0629` | 11025.2 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `a252c207384c5317ffc500091f01ab412a684c1d24d8ef583b7de6069c6cbf28` |
| `STRESS_LC_20` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.0955` | 24900.0 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `36f23cc8d055fe377dabebd1f453a4125bf1cc79a7ee3c20110e65e3ff6aa7aa` |
| `STRESS_LC_20` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0435` | 31215.0 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `8e9da07e46ba9afc2bd594ff82ab352920a4cd6189512812130f587fbb357949` |
| `STRESS_LC_21` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2498` | 31434.9 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `a5899409c47603c4f524b6660b43013b94562f64c4a4dec0214eda192bfff711` |
| `STRESS_LC_21` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0645` | 4205.0 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `304d5e26fa3304532a0b0bd803b6c6ba84edbaae07e28e9e37f6b2df7c9243cc` |
| `STRESS_LC_22` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2178` | 30447.2 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `48effa7ec8395374200a42639b14671b5bf8b785429532c60f2bfd0e5d647bb7` |
| `STRESS_LC_22` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0624` | 7099.0 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `478023e26341bbff6483d2dffbd38a551ecc1e6951ce2ded8389f2b3376382c8` |
| `STRESS_LC_23` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1502` | 36915.9 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `e3f6278a00fd7895c998154f7faf4da2c6cf9e7ad497910682eeba01345db28f` |
| `STRESS_LC_23` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0424` | 8614.2 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `07048b06f2229b349293d42e7d05da2fdd488c3a4ebd33da550ab60ce20ec58b` |
| `STRESS_LC_24` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.3578` | 43618.0 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `5680a55ab4a8546d87636bad5cf8cacd22c68f5ace5a73fa8a825887e2e24934` |
| `STRESS_LC_24` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0662` | 21115.0 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `65f8b68de58adc2330f6f71d03aa05d97603c5c8af59b67aa97467a7538328af` |
| `STRESS_LC_25` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.1621` | 25044.0 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `7e2e2aa5870d25baffe78f5ffe3da826e71d39f808826f4b20fca17ec04cfdf5` |
| `STRESS_LC_25` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0696` | 6087.6 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `b3fb108bbd4a9af4051137f0dc4e06046f2fca50d8ecc45237496598a51bcc37` |
| `STRESS_LC_26` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1199` | 31129.4 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `8cf4c5b2a3c1470c5ccf63c31786ac81af78f7d951f3652a7480c0fe16c4a0b3` |
| `STRESS_LC_26` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0462` | 8900.4 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `36dfe9dd46793be3670f868e86b81fed32a0151c266f3bdebb518ea6606003d9` |
| `STRESS_LC_27` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2806` | 30436.2 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `63c13b71d5441734881987c34b737b8d8a4b73ce8dcfd17d290459ccee2ad32a` |
| `STRESS_LC_27` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0561` | 4596.6 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `d123a8f4fd41b11a934f96e0c24eb72a6edd95aaed69cd496bad47ca18626276` |
| `STRESS_LC_28` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2449` | 36491.4 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `d2d36665fd67c796507f72a186777c10a7a72a1f9e41a4299277bb0a63e3d422` |
| `STRESS_LC_28` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0726` | 21236.2 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `79c282fe5a85bfd2cb4f264a470b6d72e13e341cf6b52f0d82d4fc0645c8a5da` |
| `STRESS_LC_29` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.1723` | 43440.0 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `ab7fe969d176a7698ad5a7830cd3dea8172b48bc0cdab0946e80fd118fdf6afd` |
| `STRESS_LC_29` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0449` | 9633.6 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `006ae8b58ec87f12e8ccfae7c8e81d63b652a393352d926a2c0c515d1a654102` |
| `STRESS_LC_30` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.2081` | 24696.0 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `a3b1872c35726c9defe5425e5c3db7ca671f2ef9134c7345ae6d8fa4eb9956c7` |
| `STRESS_LC_30` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0622` | 4487.8 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `e7d6efc3090b3dae85bbfda15d6fe7a2a97058a5d777cfc35bc7976a6c845444` |
| `STRESS_LC_31` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2012` | 30619.9 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `2897cff364b35549e227bb918d9b1f839abfabb2914d329fd6dc1a0756f5dc69` |
| `STRESS_LC_31` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0701` | 7399.5 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `4829ad90fd2434a57b14999abb56d7ba9f14cf2c8a34f54d361f9a34e319f46c` |
| `STRESS_LC_32` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.1286` | 30053.2 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `c0dd5ef5a145ffcf92e806e18c2001a92c0964f5e22bf0db22b7352ecdb1d89e` |
| `STRESS_LC_32` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0410` | 8573.0 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `ff9ea90b86855307c15b784d28ea3d6626c3e6e5d852523c5f6457347e27016f` |
| `STRESS_LC_33` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.3109` | 35814.9 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `f198a0efb52fafc987c889959fa0f5549e4d85b6ebd701af30b2717a31e80996` |
| `STRESS_LC_33` | `stress_long_context` | `quadgate` | 2 | PASS | yes | `$0.0642` | 14982.4 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `7709441637996997ef8ab07aa37efece2f9456e213501dc93bf38d0cc9b05ea7` |
| `STRESS_LC_34` | `stress_long_context` | `baseline` | 2 | PASS | no | `$0.2901` | 43490.0 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `511d0f58cbeb5b019f4d30e2ae7b2bbdafe144dfea4cc901d639cf87c0826f94` |
| `STRESS_LC_34` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0676` | 6159.1 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `caca280ac93c9814c3cb37f1b88cbc204ca2a1ca821d45b0c5b3b07573b5357f` |
| `STRESS_LC_35` | `stress_long_context` | `baseline` | 2 | FAIL | no | `$0.0989` | 24348.0 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `a6c8b411917c2acd403dec368ae31137ae3e05535ee859f60123b5bb969cc7cd` |
| `STRESS_LC_35` | `stress_long_context` | `quadgate` | 2 | PASS | no | `$0.0448` | 8845.2 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `df76de67e406e52a60e5f5e40cfa1c7bfc435970a55ff7907c3a417ca06ece11` |
| `STRESS_LC_01` | `stress_long_context` | `baseline` | 3 | PASS | no | `$1.1580` | 26069.9 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `24b701b73a019e8d308499e6fe9ef4e1aa430448de265601a6d616a1f608cb4a` |
| `STRESS_LC_01` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.3074` | 25129.6 | `40d82ab9935559a87b7ad922a9703256ae5a404a4d741311976bb57f07acb3b8` | `72b388d287fdf71df27699226340f009592fecf6d273ac51307a6c5ef486432e` |
| `STRESS_LC_02` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2153` | 31823.2 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `cfbded56e70bb024b5518e0826fddf75019ddce9bf0605c8ef2e1927226c649b` |
| `STRESS_LC_02` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0789` | 19490.2 | `27b4d9fa7716e1e262f147bdfcc1b78008b81fd4b79cb36f218c4952c61e07cf` | `718622cd9d8ba2235b75f87ea36459525047fbbf555ca482a4389a9108a4263f` |
| `STRESS_LC_03` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.0376` | 38985.9 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `409dbf07db7d3eb6d0d3a348420c55d20a4a32285f37585edc1590626f24c89a` |
| `STRESS_LC_03` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0065` | 22198.6 | `4de78adce4ef05d50f6d44e416254a863f17ade94b430906875992a9ef01fd64` | `fbdde7080ce8f894f731727ec97376a905b0f4254612758f415a9d79bb6f02f8` |
| `STRESS_LC_04` | `stress_long_context` | `baseline` | 3 | PASS | no | `$1.7985` | 45674.0 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `5ea35a629c64f60b2e2b5c8b814819475304c852f9ecd93eaf4b8406f391bbfb` |
| `STRESS_LC_04` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.3485` | 20714.0 | `d2ae69a895d0f89cbf3fa71bbe3bc9436212c9c21810957aa69c32b55057147c` | `4cf2ba6a9ab26948f5e3f8ad32c8b1cfa99c8a4f8fb5c12fa925fac1ef1cfef5` |
| `STRESS_LC_05` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.1571` | 25692.0 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `520473e57a784c2c526b42414e42c96bb3d5aac11728780154200714b28f527c` |
| `STRESS_LC_05` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0875` | 24068.2 | `da6becfe828e48f1435732e3f0b9b8d0e08feceee276a0504509a77c5fc0d9a9` | `e5b17f6d93efbe26cf58a5f3a54cb0a9cb0f946fd472adc8d1e89276263f1db2` |
| `STRESS_LC_06` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1092` | 25560.4 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `be13cc589411c01e4d52714823f3e2aa617c11fce95ea282bbb77a0f491bd2b5` |
| `STRESS_LC_06` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0061` | 19494.6 | `57fc1c0a7ec0035215e1ccb9bc778bc951830d36dc764d990b63c60418912f27` | `9c5a6fed9ed3ba498bc53f332ec7819277a20d7d8167c65137dff3dec31f619a` |
| `STRESS_LC_07` | `stress_long_context` | `baseline` | 3 | PASS | no | `$1.3565` | 31896.2 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `00e8d845ffe9869f61896399cb06ccccccf6dfe2003b5baf3f8bf17cc5ce20ae` |
| `STRESS_LC_07` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0619` | 19011.6 | `6c1bb8ed1b66892297d8e6132466ecdc9a32cfcd89c0155001340e7c0bbf2bc7` | `f54f8abbbd5771008bf5b95399beffd0fac90ce9527366e69c840ad47d5c3698` |
| `STRESS_LC_08` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2449` | 38309.4 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `11d5e5f66e4ab53a44eeaaf4eb9f2754e001b5f18814281537dff0f06d174fce` |
| `STRESS_LC_08` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0925` | 25553.1 | `52d7764152f20eb298b154fa6220091d5d35b16d6a7ea24ed49fd1834b56e394` | `864582396530d211bb9e2967f55725dad049fb5acb40c55ba43da9dd2adebbaf` |
| `STRESS_LC_09` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.1765` | 45748.0 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `8ae152d91c87ba2da653eff6dd85b8ed7b9722a2d4fd24816649558f55f4fead` |
| `STRESS_LC_09` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0068` | 18195.3 | `ca9be8b08b069eb42f2880d928045183e35bc43f3b97729da7329c9d342060f5` | `7d541375b4043b6542aacbe3f265af74434a434a635490d082e91fd8e992b354` |
| `STRESS_LC_10` | `stress_long_context` | `baseline` | 3 | PASS | no | `$1.0078` | 25344.0 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `004140321fceb9f970671cc0a226779054bcd900e71dfced8b1b9fc4937be846` |
| `STRESS_LC_10` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0688` | 14878.2 | `b8cf64b2ae37d55895b7886c56900273fa994fd99009793cc6471d623e590d2f` | `b7484d3a5761e07690f3560102389d0bb772a867b8e04fb50a420e9337d4c9fa` |
| `STRESS_LC_11` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1099` | 25254.9 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `398c8d633aab9d2647c5791cab29fe84608178ffc641d05030ae0f4f6015bf42` |
| `STRESS_LC_11` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0030` | 15422.1 | `5342b40842345f2da9ee1be9e37e80259c3e119c35087a30164a995f852d4218` | `2033428b7de4c5d9e0aca7a233df5db3aa7dac72d00c6638f3e445059e787d63` |
| `STRESS_LC_12` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2769` | 31885.2 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `0efbc1a412a3ec3b13cb8c0c91054c72e477bf7077811e1b192b1e518edcd588` |
| `STRESS_LC_12` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0571` | 15021.6 | `289fffed54e724c95e336148ccf91abde0e7626a2894a51b9b1862ee04feec06` | `2719a88ddebbb30e3833ef0fb2edb72908c03755b950c671729f7b895805b816` |
| `STRESS_LC_13` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.2462` | 37632.9 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `e5cf8b29d7aba453bbe2c5af0784f4889f66f4a89acb9e70260ae8d141d9b6d8` |
| `STRESS_LC_13` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0607` | 13797.0 | `8a7b3c9b12a02d55530da5b7a2188daa667ae445a8b63c47cf787f603c99f78c` | `f94d57bb481d33ca1eb5c66f724714c01fcc84a74a9226aba40febdffcdac14b` |
| `STRESS_LC_14` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1679` | 44634.0 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `4049e8f6c891ae5f864cbdb800575129db4bff8e65ebbd09d5fb995bba3ab442` |
| `STRESS_LC_14` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0430` | 14835.1 | `b54abf146a5051f1ea4a85f3799fff2e89c87d7f88aa0b3dfc130313d386169e` | `2cf10560453a81823ddc7a062cd36ec198dc3b294d8cf58b286aafc026a703cd` |
| `STRESS_LC_15` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$1.0308` | 25248.0 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `2e746420c00e18293f1453893e56eaeed690422171d09b30b4b015fb77350bb4` |
| `STRESS_LC_15` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0158` | 11665.1 | `1076105d7bdf98cb5c5e69b7e13ff38ebaaad3a9c9587a84b32458e883a5fd2e` | `dd3f3ed13ffb0a9be152b786cf31e4c483f588296217c456fbe37ca771c207cf` |
| `STRESS_LC_16` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1726` | 25237.4 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `936a4db1dd3d1dd2cc4c10278beb48248ac54e9fbd5efe637a038a146bf897a5` |
| `STRESS_LC_16` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0559` | 18327.2 | `be6174da706fbaf5662b30a529faada2bf95cc30dd0a7aa50e1ce2af61aaa5d0` | `33734101105501c7d84435f761707da6b2ca77342481bb3e8f3e02971f8ee0d3` |
| `STRESS_LC_17` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1298` | 31166.2 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `511dd69f41e13c9b57bbcde1ea2af683420ad480381e73e9134c686ad7c45245` |
| `STRESS_LC_17` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0419` | 18582.4 | `81e090474ed53f2ffcd665bf98da4572c0aed1f584bce1a932fa4e4a3649c15b` | `71f137f2d248187943d37eb7f388cb01cf7f1ea4645b5d2ae12637ab88007849` |
| `STRESS_LC_18` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.3216` | 37664.4 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `f9f22bba263a9a37061f6c1db0fe29f6a60cc83a3fccd3fdae3c634ffcfded21` |
| `STRESS_LC_18` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0534` | 14068.1 | `bf2be8a1fbc02ba09af64fc1cbd580fa2130b73f1dcc7b27217d0f74da1973eb` | `8b5e6d319d304ddcd4d9b49cdc8d3dc29a4d75315cb9253e22316c26860bae31` |
| `STRESS_LC_19` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2849` | 44000.0 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `f840685309dbab42ad1192e35474cfaecc42d21f8210f4ffc285d294a60c41fa` |
| `STRESS_LC_19` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0629` | 12892.3 | `d20b7589df7008afb2b241f20a96c5b9c1faa1ee5fd36a048038304b06d4ab32` | `6e3f923abe776fbf0a8669ee350e002dd2f7b286123d3059e38238b1a9f75597` |
| `STRESS_LC_20` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.0955` | 24900.0 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `bbaa9031c7cae12e8b24ca35a86a7017e82c1b72de29ff5396deec612d6b52ab` |
| `STRESS_LC_20` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0435` | 17540.8 | `8448d327445c32c3ce95e4584d379371ed3edf1cde9885a1890abcffa755e490` | `312239e1262852de29ab2b5f863dd5a0739f9a1da8f5eb1723ceca9ea3310c22` |
| `STRESS_LC_21` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2498` | 31434.9 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `467716f044d1af1e67e17e67b08817fc0d468ab0d249fa9313eafe470823b793` |
| `STRESS_LC_21` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0645` | 4092.4 | `fc0ab029ad5f64d8ffaab8e485902deeb3ea32d380c6887b90dc231c193e81a9` | `08aa54053ac3992edcf5ce118697e131101afae66f7eb185cfb207c3b15f92c9` |
| `STRESS_LC_22` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2178` | 30447.2 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `e92687e504b7e7840ff98f99fa978cd9b05ced1457ca4dd67a75a01b8acb1b1e` |
| `STRESS_LC_22` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0624` | 6693.7 | `34df7ed3b9a25b0550beb74d50e926eb1c1e611c3a802740abf225e81c90bc75` | `9e76bc85f29c7db1da62abe78d2e3dbf5652f3361b979d4cd5953b0fe80a9d91` |
| `STRESS_LC_23` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1502` | 36915.9 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `e34df4b89ac8f40fb3819ef5173754002194b96d3c1b2dadaa081726d4125bf9` |
| `STRESS_LC_23` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0424` | 8270.5 | `ab14b7deffee54d4048a648a2c130899def32a5f69176ad9824eb40e395810e7` | `9231e857f38fb8a30d299eaa9e0eafad1830527ded08ac8a27ae28fdae63fd29` |
| `STRESS_LC_24` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.3578` | 43618.0 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `adce331357ebc02a3dc4ff7f22c3204f2c8abfd470bfe73f211ae1725df4943d` |
| `STRESS_LC_24` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0662` | 11393.8 | `2437170d394144a76f26c9733d9e79f7c1550cef157b67ed0157aa4331dcb101` | `09d779fbb559f069cd306ef57cfdde3cded9f6bbda15ed43cd46905386f0cba3` |
| `STRESS_LC_25` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.1621` | 25044.0 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `44c001837573fe7c23062fd82c96654a7cf3ee0e1fd7cc554b9a85e2453cdc1c` |
| `STRESS_LC_25` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0696` | 6145.4 | `08a27f8d79c76e72120c8be928f6742311a8139646aed5847844b8a6f15c70f0` | `27433dc6906fe7014a61be21c2708af74cd8b38344bd320fa1366344f9569256` |
| `STRESS_LC_26` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1199` | 31129.4 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `58fd8c7ef59fc88a6ed7157263596accc5e75f7e0997dc13ba08e5b21362fa36` |
| `STRESS_LC_26` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0462` | 8803.9 | `9175482dabed8435bc21ca0ba4052de0a8b6c2c20373d6ea638a88808b24114b` | `e635a003fec336caa35b41d1d3318a4aff351ba94f353c0cd56561fb6b593300` |
| `STRESS_LC_27` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2806` | 30436.2 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `8e01a11f6814565267893379c432ccea1e08f0a041ca584dd073a4d1201a33c0` |
| `STRESS_LC_27` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0561` | 4701.8 | `d9c4592bb081c57d92ccea7c82d3aa24c3c6693c1c05b2e63506374cfb554190` | `d99aaabada07ee5511a8c8b666a116cd4ec2d95a00abe4e30ae1ce5b69d0f23f` |
| `STRESS_LC_28` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2449` | 36491.4 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `9317e9b999188c99226f9a2b73e30d6816477d7e9c259aeeab25acb30b774cf7` |
| `STRESS_LC_28` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0726` | 13157.4 | `c3c0b3da337d10d1e92e64f34a78df02f5a3d2f1de4f6b7679985ef358e11503` | `055a32c7915f2b06e65d9e66b9c29c488f9a86d3bf5eac5c6be4ce4672e66c10` |
| `STRESS_LC_29` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.1723` | 43440.0 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `43275429fdb3e228c5dbb501036360d883a8af0dcb554a433e853759aa6d9e5d` |
| `STRESS_LC_29` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0449` | 9594.4 | `d1009b2d9360527e051dd80ef6b274c7d7388c7fb71c6ebff3419266a4f7901b` | `a9bf4462d69c5f5b4ae29e2602b0eefc2033ec2b0cdcf8d4d9b146f1bc789ff4` |
| `STRESS_LC_30` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.2081` | 24696.0 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `af0d3e8e2ee30925b463fbcf87a9d9474141abc904ab2f613f54fe2bf79294d0` |
| `STRESS_LC_30` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0622` | 4229.0 | `542f0e5d797e544e973ee4fa888b237274e44556e3c7b8035e0618df71976251` | `67b34470a7b5b52336639bd78fa4484342d02014ba9cd806d538560b3a98ff68` |
| `STRESS_LC_31` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2012` | 30619.9 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `de7b3dcc6c231543c6d5c64287f25e6830f4b76806c9ecec9a29efdef3525f05` |
| `STRESS_LC_31` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0701` | 6637.3 | `3136955155d635e57e302bdf163998586f3d66d6d59153b45bc535b45530b25b` | `bcab214f41a86b687d404494eb8e7c5b1739c7f64ed52342496b847f830cbb49` |
| `STRESS_LC_32` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.1286` | 30053.2 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `882cefd8a71d3130f11f4cfdd3494aec9eab35988d9f35d043baf1c57d7e3521` |
| `STRESS_LC_32` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0410` | 8297.1 | `8923dcdfe181f148eb5ec7987141162c723d308571cedafa64a29a5305ce866a` | `8f1cbd547e365c027ea0f5fe9f9def103d396d0fd908e8d65607e00d2b0839de` |
| `STRESS_LC_33` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.3109` | 35814.9 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `05f492c65b57519806496311139809b4aeb7471ea03bb18bf21c30833b8a22ef` |
| `STRESS_LC_33` | `stress_long_context` | `quadgate` | 3 | PASS | yes | `$0.0642` | 7835.7 | `3f7a7d8579edcb9508e7170719c2d32d301a5fc0a20a95138575098f4ccbc4f4` | `2491d3c94227e1dc914d1770447a74bd3bf0061febe030c5c8fbdeffe801ccd4` |
| `STRESS_LC_34` | `stress_long_context` | `baseline` | 3 | PASS | no | `$0.2901` | 43490.0 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `9cd541fa593a6beed60b52d467cca4c47fca3fcdb8d8186219ad76e1ebd314c7` |
| `STRESS_LC_34` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0676` | 6282.1 | `ad63626f9755a24da0afad6d62efb77f328eded8815d04d5dd3dc73d57c8e942` | `94482cf9bf7f49f5a640612a284b0fde856066d647805bf3ce81e5aa35d1ef5c` |
| `STRESS_LC_35` | `stress_long_context` | `baseline` | 3 | FAIL | no | `$0.0989` | 24348.0 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `e11f286c86e9a07d4fca8cb71f9ad10ecb51d0ff554e56c01c4147140de2dfde` |
| `STRESS_LC_35` | `stress_long_context` | `quadgate` | 3 | PASS | no | `$0.0448` | 9103.3 | `038c6b5df90801bae7000494652e7b049999166ccddc24703e92f61098ce0b68` | `72514551874490150e50f7fc5aa646b4d2ff617b9eada580ac8cad0ac68a6fb5` |
