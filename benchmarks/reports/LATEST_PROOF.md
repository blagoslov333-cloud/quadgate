# QuadGate Empirical A/B Proof Scorecard

- **Generated (UTC):** `2026-11-17 03:12:29Z`
- **Total Receipts:** `70` (`35` matched Baseline vs. QuadGate pairs)

## Executive Summary (Aggregate Baseline vs. QuadGate)

| Metric | Baseline | QuadGate | Delta |
| :--- | ---: | ---: | ---: |
| Runs (`n`) | 35 | 35 | — |
| Pass@1 (%) | 88.6% | 100.0% | +11.4 pp |
| Uncached Input Tokens | 1,012,819 | 159,999 | -852,820 |
| Cache Read Input Tokens | 306,357 | 298,867 | -7,490 |
| Cache Creation Input Tokens | 40,701 | 71,634 | +30,933 |
| Output Tokens | 128,531 | 38,877 | -89,654 |
| Warm KV-Cache Hit Rate (%) | 22.5% | 56.3% | +33.8 pp |
| Generative Cost (incl. Retry Tax) | `$8.0989` | `$1.8666` | `-$6.2323` |
| Classifier Cost | `$0.0000` | `$0.0028` | `$0.0028` |
| Retry Tax (USD / ms) | `$0.0000` / 0.0 ms | `$0.0301` / 8502.8 ms | `$0.0301` |
| Total Cost | `$8.0989` | `$1.8694` | `-$6.2295` |
| Wall-Clock Latency p50 / p90 / mean (ms) | 19335.5 / 23681.1 / 19364.4 | 3722.2 / 5833.1 / 4259.2 | -15105.2 ms mean |

## Paired 95% Confidence Intervals (Baseline minus QuadGate)

| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |
| :--- | ---: | ---: | ---: | ---: |
| Total Input Token Savings | 35 | 23,696.5 tok | [21,315.9, 26,077.0] tok | 61.0% ([54.9%, 67.1%]) |
| Total Cost Reduction | 35 | `$0.1780` | [`$0.1047`, `$0.2513`] | 76.9% ([45.2%, 108.6%]) |
| Wall-Clock Latency Reduction | 35 | 15,105.2 ms | [13,908.0, 16,302.4] ms | 78.0% ([71.8%, 84.2%]) |

## Per-Suite Breakdown

### Suite: `coding` (`20` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 90.0% | 100.0% | +10.0 pp |
| Warm KV-Cache Hit Rate (%) | 21.4% | 55.0% | 63.5% input saved ([55.5%, 71.6%]) |
| Total Cost | `$4.9650` | `$0.8439` | 83.0% ([36.3%, 129.7%]) |
| Wall-Clock p50 / p90 / mean (ms) | 19449.2 / 23583.5 / 19343.3 | 3663.4 / 5811.9 / 4227.9 | 78.1% ([69.7%, 86.6%]) |

### Suite: `knowledge` (`15` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 86.7% | 100.0% | +13.3 pp |
| Warm KV-Cache Hit Rate (%) | 23.9% | 57.8% | 57.8% input saved ([47.2%, 68.3%]) |
| Total Cost | `$3.1339` | `$1.0254` | 67.3% ([25.7%, 108.8%]) |
| Wall-Clock p50 / p90 / mean (ms) | 19335.5 / 23154.8 / 19392.6 | 3837.2 / 6026.7 / 4300.9 | 77.8% ([67.6%, 88.1%]) |

## Methodology & Honest Accounting

- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.
- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.
- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.
- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.

## Tamper-Evident SHA-256 Receipt Manifest

| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |
| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |
| `CODING_01` | `coding` | `baseline` | 1 | PASS | no | `$0.7580` | 17820.5 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `a3d993a35460554ccb17de813db2b33c2ec33504b60237c8488762648d9a8ec1` |
| `CODING_01` | `coding` | `quadgate` | 1 | PASS | no | `$0.1779` | 3722.2 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `0f26e74dc881497f8affa015e0cf223ca5717da2ab451dbc3132d961b5a8d9de` |
| `CODING_02` | `coding` | `baseline` | 1 | PASS | no | `$0.1417` | 22197.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `39adec1d1aacddf4adf666921f212286b568572f8eef1c1e1f72714199b61b9e` |
| `CODING_02` | `coding` | `quadgate` | 1 | PASS | no | `$0.0406` | 5807.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `bf94635d931167a28621fab25171730a5cd9b214637a8b955528b69a84714940` |
| `CODING_03` | `coding` | `baseline` | 1 | PASS | no | `$0.0610` | 15590.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `294ef4254ad325aaf225f98511f2cad9218fc9be6626c3d9b944b0118ff600cc` |
| `CODING_03` | `coding` | `quadgate` | 1 | PASS | no | `$0.0186` | 2953.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `3624bc5857cae42d385b49841d728fabc92aab8370221d591de269b964bcbc89` |
| `CODING_04` | `coding` | `baseline` | 1 | PASS | no | `$0.1564` | 19966.5 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `b8f5e66aaeb0252e84530b27cc4d9d7016d7fc53b0d01199f7c574776f98b3e3` |
| `CODING_04` | `coding` | `quadgate` | 1 | PASS | no | `$0.0479` | 5764.7 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `c42c2572c9d7f090659469e490cc23b79e939e17ac90b9a6f6e0323738c137f9` |
| `CODING_05` | `coding` | `baseline` | 1 | PASS | no | `$0.1430` | 24809.0 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `563f81ce3e24e6e6055773357027c244109765110b735e01df24a1d9934a7913` |
| `CODING_05` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0405` | 5453.4 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `d19ec9c7b5fb51d8039dc3c290ea21a93826ca40731295d5ce064bdff1a7c19c` |
| `CODING_06` | `coding` | `baseline` | 1 | PASS | no | `$0.0576` | 13740.2 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `1ed67434722603b4a6ce1380deade16a74a009a6064a3a684be4212b336d64e0` |
| `CODING_06` | `coding` | `quadgate` | 1 | PASS | no | `$0.0243` | 4629.2 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `ef2649dcca614aadcbebb5462cd0c281fcb174b4d7f4e9161deea90119100875` |
| `CODING_07` | `coding` | `baseline` | 1 | PASS | no | `$0.7606` | 18217.8 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `fb3daf2cd064fa2e865d2a6cd01064386ec5117f2dddb623fb26b308270bcc29` |
| `CODING_07` | `coding` | `quadgate` | 1 | PASS | no | `$0.1779` | 3604.6 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `906ce1a0479f8a189b10e809e06d064c7ebbd2ab68d068b6bb472b4bca7fadf4` |
| `CODING_08` | `coding` | `baseline` | 1 | PASS | no | `$0.1392` | 22587.4 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `28fa71964f82e10c469abd08a80c8f1ab819cd71fb0275404ef86dcc7c25cdd0` |
| `CODING_08` | `coding` | `quadgate` | 1 | PASS | no | `$0.0417` | 5850.1 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `81ff22113e31ba35ebcf21e86b7142aac861e4cf489e48c7e0b9746e8b90abe9` |
| `CODING_09` | `coding` | `baseline` | 1 | PASS | no | `$0.0616` | 15995.7 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `81aca98ae6d03283a92647db7345d635460feccef10723f8b532828092861cae` |
| `CODING_09` | `coding` | `quadgate` | 1 | PASS | no | `$0.0183` | 2708.4 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `e03874cdd92fc2b18af8fefbdd29865c2874f665ce041d5c9a6e5a4c9db7f7cb` |
| `CODING_10` | `coding` | `baseline` | 1 | FAIL | no | `$0.1604` | 20444.2 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `2958df31b4e30b341a21485b5ad56c0f3a2e9d966ddc1e1222450d0ea723dd8e` |
| `CODING_10` | `coding` | `quadgate` | 1 | PASS | no | `$0.0496` | 5806.9 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `0ea38c22caf89b5b50d0ec209a8f38e224a41c7c0857212b5a7d33303c242998` |
| `CODING_11` | `coding` | `baseline` | 1 | PASS | no | `$0.1459` | 25198.8 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `e9ee36d1cc80b0cfcc778baf6afaa0d0059b093b414a980764ee6c6060fb6664` |
| `CODING_11` | `coding` | `quadgate` | 1 | PASS | no | `$0.0315` | 3284.9 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `079874ffc1302030c917f1c74df9d033ec3058c091ec1e5efaca959df6b05f6a` |
| `CODING_12` | `coding` | `baseline` | 1 | PASS | no | `$0.1240` | 14271.6 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `022611d73be560b1d8e597f39ff8fa8411849273c4410f9f15dae0dc03e85823` |
| `CODING_12` | `coding` | `quadgate` | 1 | PASS | no | `$0.0466` | 5755.2 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `bbcd7adbd9e28faf7b0dc6fd294fc19c3e67477793a36097734c8e0d0ab02f5d` |
| `CODING_13` | `coding` | `baseline` | 1 | PASS | no | `$0.0717` | 18304.7 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `0fba09e527cbaf6a09a4d3a1f46dc7437516c330aa7e28ccf377e00e1754e70b` |
| `CODING_13` | `coding` | `quadgate` | 1 | PASS | no | `$0.0171` | 2761.2 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `0540a63a328b17c6d9e2170bff0acd5a2767aed50bcc9837f91598f5d79f7055` |
| `CODING_14` | `coding` | `baseline` | 1 | PASS | no | `$0.1832` | 22977.1 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `a9d6009285bc60d3ee4f6384c139869a6f696a8edc1a78c0f2bf004fbbe8a15f` |
| `CODING_14` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0515` | 7200.7 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `fd684ebb46a0e84c54048fe363e5f2dfc5465f1b84fb360accbde48495fae821` |
| `CODING_15` | `coding` | `baseline` | 1 | PASS | no | `$0.0995` | 16527.1 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `a266f5d14d541cffef44ff0767fd8f49c0e317933c75eb2eb1043f0ea815d90a` |
| `CODING_15` | `coding` | `quadgate` | 1 | PASS | no | `$0.0313` | 3473.6 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `612e3a6653897e59acaba2afe6f317c7ec6663808ebe55ea757d94737ff80db9` |
| `CODING_16` | `coding` | `baseline` | 1 | PASS | no | `$0.7742` | 20450.5 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `e98b8ff8f8b18044dbda8cc92139faf4868acecbfcf9484ee0e17849340b19f7` |
| `CODING_16` | `coding` | `quadgate` | 1 | PASS | no | `$0.0104` | 2802.2 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `6e08e3a845bda54c92e47c76d91c53db20e15663d7e83aa269ab8c6f2c4d241a` |
| `CODING_17` | `coding` | `baseline` | 1 | PASS | no | `$0.1324` | 20709.5 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `34a3b31ff38212d117cdf964521a2e02cf992449365b57d4f403869b386d51e7` |
| `CODING_17` | `coding` | `quadgate` | 1 | PASS | no | `$0.0018` | 2681.1 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `3928fac5efd3c8fdaaf5a9843164e3fa5fad0394e5140b787f1f3d4ab1b78515` |
| `CODING_18` | `coding` | `baseline` | 1 | FAIL | no | `$0.0608` | 14676.5 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `22442620f96b4d29c9945069af431362cef93e2f5e9452630d1df663ac2df976` |
| `CODING_18` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0046` | 4407.6 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `849c86ded2bd54347cc045c8eb9c03d3eb8ec6a2907cd22c400d94d4c6e21617` |
| `CODING_19` | `coding` | `baseline` | 1 | PASS | no | `$0.7939` | 18932.0 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `6d1648ad8295621a6335014174bc222585cb5c7b9f310cd18e6ee2f6a4ca447a` |
| `CODING_19` | `coding` | `quadgate` | 1 | PASS | no | `$0.0095` | 2995.4 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `a927969ab4176a8b00a851e4b4dbfb6613c19b1211fbccd5d8c88c6d61717fb2` |
| `CODING_20` | `coding` | `baseline` | 1 | PASS | no | `$0.1398` | 23447.3 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `3daa4b2be0b9d499649fd6224451eec5d4676c85ebcec11519cb29baa5a0cb6b` |
| `CODING_20` | `coding` | `quadgate` | 1 | PASS | no | `$0.0021` | 2895.0 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `886878c1ff1e34aec0283e157c5a6d244cbd85334172008dbcbfe385c5ef8f9a` |
| `KNOWLEDGE_01` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6533` | 16311.0 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `c3d3b1f8e06b816cce3837266e3fc33cbc98656f099c34dde768c079932c426d` |
| `KNOWLEDGE_01` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2154` | 3837.2 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `d5eccb7bb5541e0f1dc01c5fe63157ecca357c8042c6481b8e868b577d5fb735` |
| `KNOWLEDGE_02` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1251` | 20974.3 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `ab75210fef1c4349866108e74cab67121b8fea347c42e7a70e689b6b049c548e` |
| `KNOWLEDGE_02` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0498` | 5299.2 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `fc64c8070b9f626918de462f77409fa237428ad9f5aa8749c86897870b2e0725` |
| `KNOWLEDGE_03` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0907` | 21225.7 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `78992a5a166ff33fae9908d607493e5c83893f31accd3c6e7a1a80d2918bf62a` |
| `KNOWLEDGE_03` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0188` | 3029.2 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `143eeae6996bb15830c4f24a2855602f93e88817088053b5b03199286c088843` |
| `KNOWLEDGE_04` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1237` | 14690.5 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `45e256d1b03cb2e4a432b36d8c48d1e557745f809278ce0fde4ea20b47684de8` |
| `KNOWLEDGE_04` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0487` | 5273.7 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `7bee009c5ee7392b65b71b553123789790746ce1677ef5e2cb491f24b4ffa8e1` |
| `KNOWLEDGE_05` | `knowledge` | `baseline` | 1 | FAIL | no | `$0.1202` | 18938.2 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `71a6e8528c1412166e3bce5525eaa97e5810868fe8f91ff9a475d6bcb946bda7` |
| `KNOWLEDGE_05` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0323` | 3695.7 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `395db55badcfcb6ca4aceec779de01cf86d6d6a02926521580a1bab43df04c7d` |
| `KNOWLEDGE_06` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0223` | 23837.0 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `2adc62f9aa9e7bbf45e65e2591fd4188e929f7544dfe8e03c19e727be4a20a54` |
| `KNOWLEDGE_06` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0068` | 4232.1 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `88a29ce08b09b9679eed40fff73c3177f94f76ae37221f85d4a04ccb3480f2d9` |
| `KNOWLEDGE_07` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6504` | 16405.4 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `1efdebe42857ddf627c368e66a0bd9f77f2164f9586718c1dceacd8dd4e2f0d7` |
| `KNOWLEDGE_07` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2035` | 3560.2 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `23fbd6f0c8c73042632ae0303399742b02bb6464dc4d9e30d747416ad216ff48` |
| `KNOWLEDGE_08` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1228` | 21371.7 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `3aa3508d6d3b6a12e1365e11049421bc42f7fdab48297a5bc3cb8800bd8f4a29` |
| `KNOWLEDGE_08` | `knowledge` | `quadgate` | 1 | PASS | yes | `$0.0584` | 7032.2 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `576e6e9b3b903776d8bbbf7a993239adcf2e5b1ec6bc691209888c582a2fa60e` |
| `KNOWLEDGE_09` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0911` | 21741.8 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `bc868859a91195e2ba396b93f30ca7e7c7738933d589f6c0c05cfaa456a383b4` |
| `KNOWLEDGE_09` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0187` | 2943.9 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `91717d68d08a16574217726d5657265b9180b4d58fe2e78104af3aed3e681cb5` |
| `KNOWLEDGE_10` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1282` | 15175.9 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `d64d95f1da04142b46df4a12487cff4381281477375f6c18a94d2447686bf121` |
| `KNOWLEDGE_10` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0494` | 5106.3 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `2a175117144de355c45839b81875a9b1fd24fff1b02f5cc52e55ab71aaa14c72` |
| `KNOWLEDGE_11` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1231` | 19335.5 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `f24258081f81ad76178613783d68ca3e97b8736fb7feee7769789fe7ec8970cc` |
| `KNOWLEDGE_11` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0324` | 3584.7 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `07a82546f12a11f05ce5b12a33c60318ddb203b7b24f9d4f4b0c850fe9f12cd9` |
| `KNOWLEDGE_12` | `knowledge` | `baseline` | 1 | FAIL | no | `$0.0224` | 24353.2 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `2cefbdc654511f3f80fa8d4547e6d50b221e2af28ea97abd926add08aff0f719` |
| `KNOWLEDGE_12` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0064` | 3980.9 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `1a3855b04f0f8308c95b61fb95cd7b465460587df7d521407ec57b542b6d317b` |
| `KNOWLEDGE_13` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6526` | 16810.3 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `ebd04b82f8798a4c4a1ac73fa259ce56b24da89d2749a40e1191104a867dfcce` |
| `KNOWLEDGE_13` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2155` | 3589.1 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `021c05b2919c67f2cf670c31e962b402cd89fcd395ceb728ab90d7ad6bdeb084` |
| `KNOWLEDGE_14` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1164` | 17587.0 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `a62f99eccb147c10c83d86ed7c8d6ca5bc28d7bc98cad7f73a2925b1d9865750` |
| `KNOWLEDGE_14` | `knowledge` | `quadgate` | 1 | PASS | yes | `$0.0506` | 6511.7 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `f9cd2809e62aefb3734dc30d9846b5d836ab31079c826b6993498d2748bb9cae` |
| `KNOWLEDGE_15` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0918` | 22131.5 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `d8a5bc21c4886e323e9704096571ad3d625624b0720bb8dccfb7704fbd0d5de5` |
| `KNOWLEDGE_15` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0188` | 2837.9 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `169f8bcb301783a4f288f0771e41839ab9d1e205041279396b46efc9b3d4e708` |
