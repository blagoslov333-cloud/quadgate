# QuadGate Empirical A/B Proof Scorecard

- **Generated (UTC):** `2026-11-17 20:14:26Z`
- **Total Receipts:** `210` (`105` matched Baseline vs. QuadGate pairs)

## Executive Summary (Aggregate Baseline vs. QuadGate)

| Metric | Baseline | QuadGate | Delta |
| :--- | ---: | ---: | ---: |
| Runs (`n`) | 105 | 105 | — |
| Pass@1 (%) | 100.0% | 100.0% | +0.0 pp |
| Uncached Input Tokens | 1,779,627 | 595,083 | -1,184,544 |
| Cache Read Input Tokens | 1,345,020 | 3,128,784 | +1,783,764 |
| Cache Creation Input Tokens | 483,813 | 193,743 | -290,070 |
| Output Tokens | 198,375 | 178,125 | -20,250 |
| Warm KV-Cache Hit Rate (%) | 37.3% | 79.9% | +42.6 pp |
| Generative Cost (incl. Retry Tax) | `$23.2109` | `$5.0593` | `-$18.1516` |
| Classifier Cost | `$0.0000` | `$0.0269` | `$0.0269` |
| Retry Tax (USD / ms) | `$0.0000` / 0.0 ms | `$0.7953` / 51300.0 ms | `$0.7953` |
| Total Cost | `$23.2109` | `$5.0862` | `-$18.1247` |
| Wall-Clock Latency p50 / p90 / mean (ms) | 6705.0 / 13750.0 / 7555.3 | 11886.8 / 16826.0 / 11433.0 | +3877.7 ms mean |

## Paired 95% Confidence Intervals (Baseline minus QuadGate)

| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |
| :--- | ---: | ---: | ---: | ---: |
| Total Input Token Savings | 105 | -2,944.3 tok | [-3,515.3, -2,373.3] tok | -8.6% ([-10.2%, -6.9%]) |
| Total Cost Reduction | 105 | `$0.1726` | [`$0.1290`, `$0.2162`] | 78.1% ([58.4%, 97.8%]) |
| Wall-Clock Latency Reduction | 105 | -3,877.7 ms | [-4,878.6, -2,876.9] ms | -51.3% ([-64.6%, -38.1%]) |

## Release Readiness Gate (Four Release Criteria Verification)

| Criterion | Target | Baseline | QuadGate | Verdict |
| :--- | :--- | ---: | ---: | :---: |
| Quality Parity or Gain (`Pass@1`) | `>= Baseline` (`+10%..+25%` target) | 100.0% | 100.0% (+0.0 pp) | PASS |
| Cache Integrity (Turns `2..N`) | `>= 75.0%` `cache_read` share | 42.7% | 91.3% (+48.6 pp) | PASS |
| Net Cost Compression | `>= 60.0%` total spend drop | `$23.2109` | `$5.0862` (-78.1%) | PASS |
| Wall-Clock Acceleration (`p50`) | `>= 1.8x` median speedup | 6705.0 ms | 11886.8 ms (0.56x) | FAIL |

## Per-Suite Breakdown

### Suite: `stress_tool_cache_routing` (`105` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 100.0% | 100.0% | +0.0 pp |
| Warm KV-Cache Hit Rate (%) | 37.3% | 79.9% | -8.6% input saved ([-10.2%, -6.9%]) |
| Total Cost | `$23.2109` | `$5.0862` | 78.1% ([58.4%, 97.8%]) |
| Wall-Clock p50 / p90 / mean (ms) | 6705.0 / 13750.0 / 7555.3 | 11886.8 / 16826.0 / 11433.0 | -51.3% ([-64.6%, -38.1%]) |

## Stress Kernel & Security Diagnostics

| Kernel Check Category | Passed | Total | Pass Rate (%) |
| :--- | ---: | ---: | ---: |
| `gate1_cache_turns` | 30 | 30 | 100.0% |
| `gate1_counterfactual` | 30 | 30 | 100.0% |
| `gate3_tier` | 21 | 21 | 100.0% |
| `gate4_complete_accepted` | 24 | 24 | 100.0% |
| `gate4_incomplete_rejected` | 24 | 24 | 100.0% |
| `pass0_recall` | 105 | 105 | 100.0% |
| `two_strike_escalation` | 21 | 21 | 100.0% |

## Methodology & Honest Accounting

- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.
- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.
- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.
- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.

## Tamper-Evident SHA-256 Receipt Manifest

| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |
| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.3092` | 4210.0 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `45459df47abb5d26bfa2e170140cd7c065c48dcfb1960dab943763f14ee5f19a` |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0103` | 11690.6 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `0a03794b931ca1c073383fdf06ea1480edcf62ed742fe0ed3ebde236d53ba45e` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0514` | 4290.0 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `12ad9dccaad9d979116ef74c3091a0e75fd23261a40991483e7265a1c325c1db` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0027` | 4840.4 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `950ae30632a89436bb668cf63e4823d0063502630afa406a1694ef34d9c79bbc` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0304` | 4370.0 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `980a955605392a92a949d07fb90e4525bcd2347c32b74c2c633a7153344b863d` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0055` | 6390.6 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `dc26aa5bb8b9f089196a11425b718bcab312fc1ebff5023fccbced0dfc60d4c2` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.3447` | 4450.0 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `266b07f39da672eb4f1054143061c9cec5e65d65b8990c21a81fd1369e615cbe` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0112` | 6333.2 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `45fa22bf4c9d901d6b63595579ca3a9bddc91bdf0af8f3679fd0a6baeb260275` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0474` | 4130.0 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `fe4a13839471cc6425a10b8b9fad7324501f3d4cda077e36c9648c84ed6045be` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0025` | 4235.7 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `187ba4607d31505af6c8c61305873efad12854c0344200368a482d62ff2774bf` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0285` | 4210.0 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `96e0f8264ae2ee49ef8799a384f5d62921afeac353f00f259ac72d2f182b7ef2` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0053` | 3933.7 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `e6e63588a7f9cce29de2b49beb3d6fc64279316ba73d4a5afd6bbbc9197ec29c` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.3211` | 4290.0 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `12c3df8aecb2a47498a5fde7828b8544d3e7cf32b7f04fb6d1c2da03001aa0b9` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0107` | 4047.0 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `4f716bbb31a710e1180ace8256e637003cf25bb0e4885206df5a7a39d6498b5f` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0534` | 4370.0 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `995948580633acdfbbb1ca57b894dfb64994d279bf84e7cdad88c4b16f142556` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0028` | 4758.1 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `624853fe0bc5f6fda800118cca2a714e14df9cbc8073fb6f2a43db087a4cd9fc` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0313` | 4450.0 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `e6c011c868d265f3f8bebf10ba12df25c214e3db36b1e5a19cdbe63468d09d48` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0056` | 4889.0 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `660399b70676203a49043d311906c25c4ef4dcf75f93e0208b1e895c0f8b2e0d` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.2974` | 4130.0 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `83bdc2a0cc18ef041d11b1761e990331dbea5db651e7ba30d8e893fac0ef1eed` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0100` | 7217.3 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `10a485514a011c0b0a82a1ca44a5c90be569b8e6aa3a9e57cefe16f8057eb2a1` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.1203` | 10485.0 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `d8141ef23f8a1d96bdb5319724b215e0011e7a25a425424553f36dbd3059bcb7` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0920` | 10500.1 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `daaec21fe6c72b309ad1c8f235f7e494a34afdc07835e3d8a597299442551ea1` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0775` | 12075.0 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `4a9776c7219f343e652f3871c5a0392a682788f9ac5b33a42779eb9f4619222b` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0514` | 9227.2 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `68d4997ca2017860f861234bb589b461c20af350fdd45f13115f6bbea0d122f0` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.9307` | 13750.0 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `c0f572666110e3c402b9c314575b5a57819205262ff61eb21ec8e218a6862571` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0256` | 9465.3 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `ce3b14673b22cc6dc2eda7e2f4eb9f61efc3418bd28c5bc76a9f8268a72cb9b6` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.1153` | 9620.0 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `5c7ed96c49da15b1f70239060d4d1294be9fde2f448a2ac2650c165fec930447` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0057` | 10303.9 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `1d9601a93fb7c95d7ee6f9f0069d5f004773fd8fc731e7c1c96a0c7103310a45` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0887` | 14550.0 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `df3fa32ca7876ab4062be71e3a1996c7f3da0e4b7f301bef111cdca748e64b16` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0145` | 14201.7 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `4dc4dc1038f635a507ffb49eb2f671fe21d101eb769211676902645aa819ac73` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.7277` | 10485.0 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `6a9df13b8a879eb6efe583831dcfb3392ca66861980efd748e226ae7b041c4e9` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0206` | 9037.6 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `3be888f69ca1a83a9562bac03074de6e3f8aec07ae3021db622f841d9e19d43a` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.1409` | 12075.0 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `e7cb2680997a6dd9d36990f77b75b88608991e4285274ff74ffff5e6b3c36e87` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.1075` | 13620.7 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `085d9ad20fa32171182d588ec4a9f9fd5e8ad6812630c0b1507d5b30ae369cb1` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0876` | 13750.0 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `68908f2b18b8186906c91bc735006daf3ead782dd62c32de01243df7c863b2d9` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0573` | 14557.8 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `6e04bab167f7ae6a16bf3eb9acb18d5ef023f02f88d0c674a3cd5a2ea19b9a05` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.6726` | 9620.0 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `00dc2b92ee2fce515b4b176a2745b047cd9a0d0ed0036bb567a3971c7f2eb8d7` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.0768` | 12160.1 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `5be8d70c41458f7a8ca0b7e7aea9a0467d41ab2aa933e7a433dc0af63a6cec71` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.1602` | 14550.0 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `bbcd4df3d24ab90c49fc0b8ce75b3e297f9e8cd212cfe5632d709576243dc3a7` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | no | `$0.1214` | 12702.1 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `62ad2313c7227b1eb160bf6bf6c5add271acf2180641c269da8014a9874609f6` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0447` | 6585.0 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `494ebb1e9bf457cc7440664b7b393746e43e5f8b2c152805c2087f6da654290f` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0354` | 25735.5 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `8ebd16bdde11fbd5c1fde21edfcd32b6277270f69760bc8655d57f6a49fbe2be` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.4949` | 6705.0 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `f7e0a64fb601ac49f3ddcd3aa31f98d54729f859c08d6bbf8a6abbc1ed6b63a3` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0897` | 21116.0 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `e28a0396084ceeaad0d90838f94561ccbf4166384181bdd076adb4676a3194d4` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0822` | 6825.0 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `301e42e4668eebc47850f77751896b6b87f3dae23fa68ed55def5a1b1c2d23ae` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0740` | 21172.1 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `1f3eb071ee88a5b606497a3b63a3cffebb589ee50b1faccb75c597063df8a791` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0480` | 6945.0 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `fca879a6c00e8a5e8b3246c29f4fff3f42b3b045d5e978b4a59f12b758cb7ca8` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0373` | 28359.4 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `b960ef8bb0aad510ec74145d399a6ef6dbed94a0ecce3248027b8f9671310456` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.4654` | 6465.0 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `ab064e8d22418a9e1d71da1b0968e027d2ca026b074332fe8560f711b9836ee2` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0860` | 14818.9 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `ab48a348fc7cc9b62987c98c07b8f68e6dcbe62e3f1ffc272ae54c2daffe567b` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0769` | 6585.0 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `d36b3fb1953c0d99244eee8478493f02b50b1e5c1c0c8ec0eb7f2413024c253a` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0694` | 14765.1 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `5d7dbeb279b36c0802b81330cc5bfaee546c8648f9fe7cf09a1bfa77eb3feefd` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0446` | 6705.0 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `3ae22236f820540f48730a172aacce2564c91f942dbf01a7a3c7feef5b366adc` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0346` | 12205.2 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `22501d6dfc693ed28da2899a0e02c8a3c13718cebc5c52868a311db22a49f872` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.5123` | 6825.0 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `28f95f00d6d92a77a12d86f5f6f0f9be4823ce9c5911b4a56a864f82875a737f` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0918` | 16371.6 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `e557c35e2227ef952bfac8f513dad528d708c8c72a42001f318644f4e57656ab` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0866` | 6945.0 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `8d07ad3dcdee82e5accb0a6440d195bca31924fdf28a75d35f72740584d6d174` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0776` | 20177.1 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `49aca870113d5f96363bfe94b2d9ee8e300fb3df5d3e9ce268d9e38d9b490fdc` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0426` | 6465.0 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `9f5420acecfa7fd9edc6836555714d18c45e5696e4231ab5850177ed3c827495` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0336` | 13760.7 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `17cc3aefd8b131b8c16986b324015a532dde6ded8908b1b9d2fa0ee51829c922` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.4723` | 6585.0 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `c7ae559122697312b844b539eb47cc231e30b68f315b6ba5b269622a22e3abab` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0501` | 12306.3 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `ea139c2cfc99b2b51c6b359f3fab77802f6bff6062c765c5df04fb9436815fd3` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0799` | 6705.0 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `2cdba227ed736c73548cd6a42ecfa6ac9ec1ecdb32e6ed9850aebf0cdf6b86d7` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0143` | 11215.6 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `cd4af6c83255c61549fef80eaa175d64ef59603faa6df28a0da223fd94f9ad6a` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0474` | 6825.0 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `619e1abc8d3f5e6dc5b927d252272852e9a47a4190dbf34b53b0adbcedc774b4` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0368` | 13980.1 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `44ef8877d92bc0b4fe84bbf8fafb40c402c717587d010cab2d45dca01bc940de` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.5296` | 6945.0 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `217698036f7c4ced5e06894458864c453f7b620976d32c048d2a0e2c56937f5e` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.3118` | 12889.5 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `177704861d0d66851d16b303ed3e38b0b27de21555930b12f4f9db66c360ede9` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `baseline` | 1 | PASS | no | `$0.0732` | 6465.0 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `1f85734c55b278bdae75b8bb7e113b2cdc39aca310348ff97b7c535ff87652cb` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `quadgate` | 1 | PASS | yes | `$0.0133` | 13061.4 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `d2586a9287d0b43bae7b85563ad7dc430f6c55933d1e5d0f731191d433cd4d97` |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.3092` | 4210.0 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `c585f177e03bdef8be06e1ee18774d931b01a03000d7f597ff4366765db1eeac` |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0103` | 4659.6 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `57d6c1e41ddd99f65f3aac29ecaf1224f9bb9b132c76381192b5d3567a6e2558` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0514` | 4290.0 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `7a24646d27b6af31597944cf22eb05e20a154273cfa6487acc074382d7f18737` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0027` | 4953.9 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `ea304c42e4351ce9e4fb4f37bcbcca1122ec9e8f299c69a920f6c03be7039a58` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0304` | 4370.0 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `ff05276bed6cc9ce2066a0f29531e076e158b80787249bda89861e8dae6d2b41` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0055` | 4261.2 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `55ec855a2bdcd4a8bc690dd545f2a75fc38623bdc557b6f18666d15141d886b0` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.3447` | 4450.0 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `8685867aca4f876ec8710c822ec97dfb3f1201ed56e07ef63fcab2930d27e825` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0112` | 4032.3 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `9f19c0f2cbe216024ea453cf168f87d440a3db92fda21b44504af9bff35b544f` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0474` | 4130.0 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `2cf563c62f1400ee6cbafc006b44a56cade18a8bb7ed45838ac01dee59ebc1ec` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0025` | 4022.7 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `55a682f3b0b0b2afce78bd3afb32d7f7cad0b338424f8453af11fb4aae5c0d0a` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0285` | 4210.0 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `e99ecc2b049aae395f737c8cde8851de3f56d52e2560d008f5125f0a8f60e602` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0053` | 5092.7 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `b2f470da6f96361ab188313b5acd460f62506c81eb375fb3ade96b49eb5bfcda` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.3211` | 4290.0 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `0541537210d35a211b143041093f176378a6d16116b0386e37f1a733c6133003` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0107` | 4962.6 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `243c1896baf7702c38dba9b6798288c1e4261496d194325a71b8007dd6fc8759` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0534` | 4370.0 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `01bdbbd2bb38461d3dfa3b798f6ae51147606eb1a841c20309bd4d7680903360` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0028` | 5788.8 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `4163314e2311dd23acc30c83ba0f771db69ca7bee2f6c80d828a6b5fa2e87b90` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0313` | 4450.0 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `c7a316b2131ea004730de0a8e38531d3cbfd78c6126614ce934c569745878a57` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0056` | 5599.6 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `fa60aed55a437ccbba45cb4402dbb1d6ef296191bc6926467ec13baf34ab8f6e` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.2974` | 4130.0 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `2b32cd301da4fafcaaddfbbf170c245d0952509d4a56043eb2ea451d9daf5240` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0100` | 6755.6 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `d04dd58608b00629066ccd0088579a2ca2cca9e4a49e87c0259e44f85b0c8981` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.1203` | 10485.0 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `b2966c3c412ddd998cd0aa8d6be81e71c0a8b7f6ccba78aa6d9e06cd8516be73` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0920` | 9213.7 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `59fe9d0ad02a499eb688b4889514f7ec9302d84502e3e4561dd43fd4e19e1f0b` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0775` | 12075.0 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `c605a4892cf26f3ffb740fdc3a011704ff4d6e7f8a03691a558b926e9223bee4` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0514` | 11712.9 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `8d3dfbdddeb1dff3ea8c6fd1fb00a19f354489f34aa0ccbc02a76409ce7bb7e9` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.9307` | 13750.0 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `c52d08dbe80b2cd0d53a0ae313868d23d1bc173008e5b447e54709ad845fba96` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0256` | 11341.2 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `7980f8dee9f1ba30f45b6e260b822b509eb3f8ad1d6cd9897ba2077843feae38` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.1153` | 9620.0 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `27bcba8ccdc604fa62a3f0f927507c8605c1ef0e4e81b5d312bf093e4303b56c` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0057` | 7787.6 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `2ed3e1a1d0552deed11a29c2322c04194ee39ba82dc3d1482f3191762a09c9c0` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0887` | 14550.0 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `888db87253472c3abe3ba19bf28d71bf8c7dda42baa48c00ee25c3966cdf9b89` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0145` | 12049.0 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `5e5845a397529d627252ee41f81195f57ef05b1f5c0cc5701de6b90878d6cbaf` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.7277` | 10485.0 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `f9e3391ee3d87b6f0b67f79e5101588b780916feebc3780845522f0f2195ea40` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0206` | 10876.6 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `f98bd89c8736aa80bee1b5b41c897ab5893e7d1c21f4c98afdc6c19987165cd1` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.1409` | 12075.0 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `a4ebb3c9f06db3fd547c99d444f854ba25c2075fc06598f316af794c0b369c62` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.1075` | 13579.0 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `718d938d091622a5155f484e22a92903af7e7042bab40e925b62de39b2828c46` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0876` | 13750.0 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `b2e0371ca356899e52d6488b9fbc7390bde571383ffb1f0677d9e08dfd44748e` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0573` | 11879.5 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `8c740d163b31b0d9763bd95c2e5b60272bb03d34de028cef6e39aaefafa72484` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.6726` | 9620.0 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `43c1dd1a93618d942476441024cbd3ccae1997b555693c62aeaa15a39874162b` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.0768` | 10560.0 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `db71368735b0860a635dfff4013757045701e4cae84cc1d1df43d899f4fa29e9` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.1602` | 14550.0 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `2800dd6b956b8555201f93df905db15765d8662a9fd3783136e26a56040443e7` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | no | `$0.1214` | 12697.6 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `7a6ceb31050a39201cd149930805276d47a69fdfa9d9bbd3f4e06316f9d1fd12` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0447` | 6585.0 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `d5c486e5e6d1c2fa7878d2f05bb2818a73849e3b8b7f7d71a4be3f5ef8cf6ee9` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0354` | 17770.6 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `9129fae41d99eb36bb968d643a9ebdb32859e9f61f2a2718f3b7d146deb122f7` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.4949` | 6705.0 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `774e632176d22f0f6db906f860aa2a86285860ff417e41333a85086520189b41` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0897` | 17129.0 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `00e9013f0e9a492c29b7193d814b883d181723b0fa12b95c2e4e2a80f3aaa024` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0822` | 6825.0 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `f389936ee63131a078067ed69a94f0af6fe3f52d2b56831c14eb8bec9916d9c3` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0740` | 15833.2 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `98d719c4f06008de6499773f939b515db010c6a00d200f879008a57e3dbb9dd6` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0480` | 6945.0 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `78ea19764aea0beaa303af645c2d55156fc4773fc8931a1d39f22eddf2d40dea` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0373` | 14328.5 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `7eb2d279199cb9d1b326d566c6e745be55184162e127cd6d820a18a95cee9354` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.4654` | 6465.0 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `11ada322ad40d712ed4ad74d2aa27de16a6415bc75f27c0c156ce73d1fcc5d73` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0860` | 13390.7 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `7d65be810363d525df2e109ace378e59feab80cf01901caa88e435b711043269` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0769` | 6585.0 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `22df4f825cf76343a5adb09091a2aa7d8333be159e8162bf6b1f866f8157526c` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0694` | 13390.6 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `66646f7908412f78e694a0254c3db0a5f8822b1d48f7e651c9d5c26fd04670f4` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0446` | 6705.0 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `e0346e24d9dcc31b1d013ad4a940020a60fe6f841c8969654925a2fad5f3687a` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0346` | 12461.9 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `1292b2d3bcf5c2a94ccd0a0e8ecaf1a9ab578812b0e37ff4c3bbcf00a4a208fd` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.5123` | 6825.0 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `8d964d6e0f30e7e0b83afb58d594e5f0410aef52fbb562f6c62fa283c1b51907` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0918` | 13058.2 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `0054b9857d2c98ec32e7a745682a7c4c4d296486cc4b421c969d9da3c336cb45` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0866` | 6945.0 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `a7c610fb110c522698d75ee69b2b84a468a4c8b37817723ec6c8abb9542ec2ae` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0776` | 19474.5 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `1f8bde583c24d80a0a91498a136549f3f3329146092db73add21cc26541435b0` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0426` | 6465.0 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `afd08def0875237ad297acfe8b50fb05e2b0f3ce502a2c198167e3154e462bcb` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0336` | 15803.7 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `6833a16da737bf62b1972d4bac690bcf427cb5cd65984ac5daf614b4c73f0d47` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.4723` | 6585.0 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `d865b86e3252f61bfa1116a03e3cd71c89564748c16e97d2d36fc875db39ca54` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0501` | 24551.8 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `d574f70be6a4a73564ad3f3bd779a17b4a164674f000d7bf9a0ff5044e6af3d4` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0799` | 6705.0 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `55972a66f3b6513d4ee19feff8f0b76de2f7947c293677414641b408e8aa28d9` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0143` | 20847.1 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `f5f3c8455fa295eeb4b0a99c6a6bd4d712e18d063f057959e2275f896fe70364` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0474` | 6825.0 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `297981e0cb57cf875d421e85569a31a8b2002d10ccfe31b1f8bb8edefe206ab9` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0368` | 11587.5 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `9123048405a04868ce6595b2387e5161f3d2f247211b9f1569f6bf22b09de0f1` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.5296` | 6945.0 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `295c093466298bb54cfdd8289305dd177047b6ddf6f65ab941d9bf51009f0b7f` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.3118` | 11606.5 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `81f406449d28f5610649255921075cbc45313db7ec9e5e859af1e710298a21ce` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `baseline` | 2 | PASS | no | `$0.0732` | 6465.0 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `bb796b2181166ed7eed49bec23c7d2477c17d62f59d5d7835d14c8573151d66f` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `quadgate` | 2 | PASS | yes | `$0.0133` | 15022.9 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `8539b8fb938c8b1bd51addba75b48f6cdf1820a634724d3acf2c246204f6a3c5` |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.3092` | 4210.0 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `6ad54c1a3bb5f97307f262f87a15f607812208ca6a1d7d3baaaf9e93fad1b8eb` |
| `STRESS_TC_01` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0103` | 6496.4 | `e1c1c5de7d18ef3f517cbd38871ecf086e983f08539486790abea67e128762a4` | `b5b58aaa1ec915022473e45b4ea794a99a2c9e1ec218fa37f494979f84288313` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0514` | 4290.0 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `70e20e50bd276d14f9073d675c9fdd1e6008ae9550b45a1487925cbd728c8a83` |
| `STRESS_TC_02` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0027` | 6067.3 | `3ef340ab0cc29dca8d4e801970ab435223f0da9b21ab9e34cd2745deb4ea2121` | `75682aacfab10f7caeb870e957999493262c0089a101adfd12b6d51ab3952005` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0304` | 4370.0 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `302d3fe96dcd696c4b1e30f104d29f56ef5e23d074266a53cf2a306d29dab2dd` |
| `STRESS_TC_03` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0055` | 7363.8 | `d92740534d234fc8cf4d236985627700aeaca8f163f26f8e8ce89d10e9abe05a` | `043c1727da17ef3df300eea407343d0e864c55eabac7cc0f957774e3e9c75222` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.3447` | 4450.0 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `6d17c0cd576a4ae44670751c1e8efa4617f80406983a2af7cfc14e4f9df5883f` |
| `STRESS_TC_04` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0112` | 6327.4 | `d7647fb9a20b74df0a03acc58ff0fe58e5da9708e8e0250a1579d9c04afa1ccf` | `e9351c786e2080fa82c93aeedf2ad86454bed2fe212c400860af191b802d8435` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0474` | 4130.0 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `8400008b9c6faef07ef248ee89f882ce78cea9e0bfa552a2baeddf7be7d044d4` |
| `STRESS_TC_05` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0025` | 4444.1 | `2fb991161934ad8fbdaddcb78da1644e55f97df9b022fe50ede031a578fd5584` | `7812e643c9bba77c5137e371cde959885a51d3421174794ffe176de3acc5b215` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0285` | 4210.0 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `5c37dc824a484b93da3a07a71a7dc6d589064771741010a7110f904bb6840e0c` |
| `STRESS_TC_06` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0053` | 6477.1 | `c6f141b7fa5b2cce33c54fa12ef05dc7dd28c4aa9f94a57a27f27d22760eb8bd` | `b19adb1f2a8afa25eefd7ee8bab295101053262bf2c715e5198f81e43d59f7d9` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.3211` | 4290.0 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `f329d25e1b76f9b44e23149968f11652ceede70e7b5d6771a35e5db9bea71b22` |
| `STRESS_TC_07` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0107` | 4383.6 | `e44802d57f74cd9b613a9adaa696e274f8cb020f305b901c96106bddfc239da9` | `c3ad71c28e972bc9f9b462fbd59112972a8248cb781e4391a0c51da4ebcbcff7` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0534` | 4370.0 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `117a277980c29dd8926da3fcfefa23e945f3f9c9f91b5a143e099c5ac8ffce34` |
| `STRESS_TC_08` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0028` | 4242.9 | `16252094b995816b0fe8f94272219f3624be56ad40b887ec2262ea3af652a0e1` | `85fb6989708af165141e607dd90473fef3b2f33e2841b7f91b1b629173b6cbac` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0313` | 4450.0 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `c46eef5409dbfa0013ecdec9a0f125db0327293b4aac473de5f07dff7d237bfa` |
| `STRESS_TC_09` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0056` | 4473.6 | `4aa9186e898bb35262593d1324d4747f27d08208699f9070e4f41f61b0a85c04` | `81f1188308049dd84ce391ab4e83f07fcefac6bc19f6db3b28d7fecf3cb60cb4` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.2974` | 4130.0 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `160af7900709e58afc4fa6d9678a7194fb0557261c48596bb8a5223cf8e1e3c4` |
| `STRESS_TC_10` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0100` | 4178.6 | `acd3963b3dfddcc5de9352aad38a5523a666a5efa7ab8caedc25766f942065db` | `922ba31fac452a7942b6cfff8477eae14a8f16c7a13a44249f0b52a333827ddc` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.1203` | 10485.0 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `6349c8451961d84a91255f90c8d1090710bf275389da286a5b16533e88e7f83e` |
| `STRESS_TC_11` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0920` | 11567.9 | `f717023490b34c04c7f0aae7055fe25a02671d815bfde918c658b51479cfb9ba` | `401f5426e69c54278258ca774ac21eca15316f1afdafae169f64f28472d0ca80` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0775` | 12075.0 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `ab295ea507ce1eab789616d51413f1ef382b5e19a5bc1681fbd84f50adcfa998` |
| `STRESS_TC_12` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0514` | 13201.7 | `63cf2839a1b11778a3d63dc64c4ee6c90a5ac64430e58c262bdd2a132bbdc9e0` | `4dbfbfbc0010ca142ebb60cf36b292163fcaeddb5e491bf773c10c046f54ff6a` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.9307` | 13750.0 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `b756f6a709bb548a7f009276f39d529363f771b0e895e078e54f66a071cf5a4a` |
| `STRESS_TC_13` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0256` | 12040.2 | `1bf4aef79374d6285c44d8b700f4a0c752c6c766529de223117cf0fe314d2e8a` | `6bfa0cd1b2b72213b6fdd953bf37f43d3809463f0661b40b77c9b68049db1f7e` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.1153` | 9620.0 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `5df050671b279d035912b4d0a12a891f87c273509a5abf20294259aae85dea1f` |
| `STRESS_TC_14` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0057` | 9110.0 | `275b78ce37f94e77ecea09d7b7bd78846af0a4bdea84ef542a9a59b3f1ebbd84` | `0de9e64703a93085cbe2ab5026410bf58457e1b0f48cf5216e0cd0d927b5f911` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0887` | 14550.0 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `96c7c7989098a490f505c3f42eb14fbff38fdee86b4e56e93ed7a86cc4ffb3f3` |
| `STRESS_TC_15` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0145` | 11615.7 | `2b10d13fce732050f7c8ca33b1d06aab1feed143df26f1477c92bc88a5c76637` | `2a2c5a0b500e1f16318d2af86cea24455fee2efd91108fde3676c08e122f4bb3` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.7277` | 10485.0 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `dbef6ed70158b7e4bc297e4ee45e22e8558bb49b14df57c66e7307a08077ac85` |
| `STRESS_TC_16` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0206` | 7764.5 | `2ef750c1428cbfe3ef4c3850dfef84768048f10c312a10c9af35c3d76201bc47` | `6663c1c024a3d890598d15d17b87aeb05637816fbb5c68da5e4cb943d1c10380` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.1409` | 12075.0 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `3afc874a8a95b286e916ca7d60c17e73518299b020309d28f7a193c210555659` |
| `STRESS_TC_17` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.1075` | 12091.3 | `059b581a3d88c3827f026567a408c5e0e39af8cf72268d9afec8573b7db20f38` | `e19548704d4b3475afe8c49c1c3b26ad576e9a7ccbf670aadc85d133a2748714` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0876` | 13750.0 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `41f893321f8fee329a9712950d7998b178747c43f9f6f18eb2f7e2f3a7720d9a` |
| `STRESS_TC_18` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0573` | 12824.2 | `4bfdd89da007f9265551b4489406de0fd2b5fd1872ba4af23ea8e039f340ee02` | `99523b30e9ec366b41da8c1e902a4566c31cca8c409ae20f95420b177acc7300` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.6726` | 9620.0 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `d4b891b5a8bbd77940cd7249af7a9739d070bfc12c6e4ffacb5ecc040285d337` |
| `STRESS_TC_19` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.0768` | 10984.0 | `5c35d667e823cf399fee02c3139c8c47741a3f62c4f7b9a5aa876f0d71d65e82` | `543d99bfddf3b6993fd4262788b4e0c0462ab6d1be4691fc487f6fb110ee6e34` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.1602` | 14550.0 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `88d44865dbe53e34004b7a6e14fc0433047f1865299f18760b623fd7a15ceb05` |
| `STRESS_TC_20` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | no | `$0.1214` | 12306.6 | `04d75f34d1b6abe266349ecf353649fb10a8273b74ad497e3ae5da48211b1be9` | `70fa5ad65e2c2dfd0f556ddef3588a0ba6ba694833347297f93391a35c155ba3` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0447` | 6585.0 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `1d92e64e0e9afdc77f244ee4934f22ecfa8d15a073e31a41428154fd930f3473` |
| `STRESS_TC_21` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0354` | 14281.0 | `100f42988c7524f19f2d154ee2b8214d9f110e590bdda4db028fb6dcf03bc6d0` | `774711cf61e954850a7b83f79fa328afe7e11d6aef5cd3422a63c3f1b4ec27be` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.4949` | 6705.0 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `8a66316e1ebbe35d721c7303fa2d0922b2924932ace8c2524acd27ee5f8eb9d8` |
| `STRESS_TC_22` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0897` | 12562.2 | `0c1c48358925ca889509899b614070bc8139540b326deb0747132297f6be5d9d` | `6d9671b2f2107319b6b8d4a8f3d60ece252adcc53d4e2e1bdf3f77e37f252f21` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0822` | 6825.0 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `8abbaddead8df0b9209963f1456388d2036bd3110abbf20b2aab83eac161bb0c` |
| `STRESS_TC_23` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0740` | 14564.7 | `647b0bf4b7fc2f60fc4f536b2f1c79d90d7cd6662087f7b168dd29e391d0da8b` | `b0f96d2c6997736e810f498391198fccac4b863ac48f4540043eb1399ef06be7` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0480` | 6945.0 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `f4d6b86a3ce868a717ca5c8e3d83924773e091b2dd35b5cbf95e4a34dfb7e003` |
| `STRESS_TC_24` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0373` | 17784.6 | `0cc046513d6a9ac28517147268d1cb9c3961c1fdeca92e4c7176b11a06897d1c` | `c6b99e2e2bc2f5853a9d5096cb3ea9a5f082da35424a5743c8283d1df39739e1` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.4654` | 6465.0 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `69454b680538d4a5d5f362fdc4b548e5675f5727b5f5dd8eada798c897cbe2c6` |
| `STRESS_TC_25` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0860` | 13186.9 | `ff6de2763d0af0ca37ca8d7587aa5a4774c45d6c89dc5400b42185a6322c73e1` | `e46392e160efb71b66d41df1fd6287ebeb0288fba0c94887f32086ce1ee011c4` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0769` | 6585.0 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `ce3cfc21a1ec2d63617e455af4fc192249b270885eb4f3fcf54fda32460ed49c` |
| `STRESS_TC_26` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0694` | 11886.8 | `f3eeae9fedfd2bca5ce0db04dab29d568ccb96abaa47fa5e19f202c414bce133` | `c199b0bfd0109c60b68739b5209399c8fedfe84d435dfe1de2f70260f5f593ea` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0446` | 6705.0 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `58498fbc976ee41b26babc59b02b41f2a49edb2993dbe51c947562d9e49c87a0` |
| `STRESS_TC_27` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0346` | 10994.8 | `a9756f330a89bc5d8f52bd8bdc9acf146b803395c737e440719682a50121f365` | `731746f5a92323988a78491378676a84d2e34900b239b30d8b73ee538d569419` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.5123` | 6825.0 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `2c8a1c4a7b9a02bd1f585786410b2ff3174099707d4cba0f41adacb0f7c63365` |
| `STRESS_TC_28` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0918` | 15940.7 | `2ca99380c177154e1c7a0b19a1099fa6afd664aee8ce748cf0f7ce3d46c932d3` | `d8c271c397d08e7d5febdd083951cde825e6e0b9ed1043aa19198dbccfe7fd1a` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0866` | 6945.0 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `7fd2372b2d9e56c6b25b4df6e8cc2953ae176290e19abb47f2b557e286115875` |
| `STRESS_TC_29` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0776` | 16063.7 | `d4690aa55b45409a8d082ea1ef95f3e548e01330a0767f71d65ea0d7b41b9b37` | `433b0ea515622737d93e46a028c65e904c675a22be9ffee46e2945776ff4fe4f` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0426` | 6465.0 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `50ab903250054df9e0b372383ecc5bff299d028f101b34629a7a85b99cf94095` |
| `STRESS_TC_30` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0336` | 15066.2 | `f4c36377211a31abb2126e8a176357190dab52edba02ac5e61855b37467d57eb` | `ba68242b14cda49015b00b982ebea36d12b945c3d223fea17b0ad372fe1f1c5e` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.4723` | 6585.0 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `50296b435fe8ec45b05801ef4f839d96a1b37e91dfa8ada421839a0f83fbc5e5` |
| `STRESS_TC_31` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0501` | 14040.9 | `e3331a1588d7dd4dc3ef34e2360ac5ab1be46fbaa25d2b9e78d862de2b74228e` | `a3e71a0b9ff4b01760822b655150f3c076f7172bfd21d68f593a3ccfbb0c3cd5` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0799` | 6705.0 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `5a15d02176ba8f03980464fd1cf671bdc598afbed2ba5a1aaafeed7d853a6614` |
| `STRESS_TC_32` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0143` | 11198.2 | `4f4e3ac14571c138af062b8f33dafcd6e6b916e3d3d584e8badc447c847f2c93` | `838ea291c36be462ff49389162f025e105f1ed0f38381ec8e83c8cdfc73c18b3` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0474` | 6825.0 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `4cac2c72fa889cbac3eba50b6516e3b8f658ab549d068b774566d5be81c21c0f` |
| `STRESS_TC_33` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0368` | 14451.3 | `0b973124af56084f006a1d7c496d202fe4c59ff809576a7dba5238a9119d03a0` | `660ef4791b2437bc4ca7209393f347666258409fc5f4201b3228b8a886f7a2dc` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.5296` | 6945.0 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `a27a2677985eed00c2c68ac78456bb4081e012866b1cfc0a7439e158ac04bacd` |
| `STRESS_TC_34` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.3118` | 13698.0 | `02394fe139f175e7e7b17c7ad5a72465c431e5d001d772bbbfc36d9673cd9943` | `74851a5aef7c7dc29a7a63b6c91e9f42cded6cabf5d34d4b87d4c1a4d9453d3b` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `baseline` | 3 | PASS | no | `$0.0732` | 6465.0 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `8798af0f36c7039c759a03bffa9607b07607cdb358fc01fa4becdf36eda2d0d8` |
| `STRESS_TC_35` | `stress_tool_cache_routing` | `quadgate` | 3 | PASS | yes | `$0.0133` | 12658.4 | `d10c391019a5cae01e622fd0d31a6b78a46eae6232aac4e71cee73e214345650` | `2c743379706572575bb3a679f5a4b81709f2cb2baefb4622bc5b0e581e9a7b72` |
