# QuadGate Empirical A/B Proof Scorecard

- **Generated (UTC):** `2026-11-17 17:57:22Z`
- **Total Receipts:** `210` (`105` matched Baseline vs. QuadGate pairs)

## Executive Summary (Aggregate Baseline vs. QuadGate)

| Metric | Baseline | QuadGate | Delta |
| :--- | ---: | ---: | ---: |
| Runs (`n`) | 105 | 105 | — |
| Pass@1 (%) | 88.6% | 100.0% | +11.4 pp |
| Uncached Input Tokens | 3,038,457 | 479,997 | -2,558,460 |
| Cache Read Input Tokens | 919,071 | 896,601 | -22,470 |
| Cache Creation Input Tokens | 122,103 | 214,902 | +92,799 |
| Output Tokens | 385,593 | 116,631 | -268,962 |
| Warm KV-Cache Hit Rate (%) | 22.5% | 56.3% | +33.8 pp |
| Generative Cost (incl. Retry Tax) | `$24.2967` | `$5.5998` | `-$18.6969` |
| Classifier Cost | `$0.0000` | `$0.0091` | `$0.0091` |
| Retry Tax (USD / ms) | `$0.0000` / 0.0 ms | `$0.0902` / 25508.2 ms | `$0.0902` |
| Total Cost | `$24.2967` | `$5.6089` | `-$18.6879` |
| Wall-Clock Latency p50 / p90 / mean (ms) | 19335.5 / 23837.0 / 19364.4 | 4889.3 / 8870.1 / 5605.1 | -13759.3 ms mean |

## Paired 95% Confidence Intervals (Baseline minus QuadGate)

| Savings Dimension | Pairs (`n`) | Mean Reduction | 95% CI | Relative Savings (%) |
| :--- | ---: | ---: | ---: | ---: |
| Total Input Token Savings | 105 | 23,696.5 tok | [22,368.3, 25,024.7] tok | 61.0% ([57.6%, 64.4%]) |
| Total Cost Reduction | 105 | `$0.1780` | [`$0.1371`, `$0.2189`] | 76.9% ([59.2%, 94.6%]) |
| Wall-Clock Latency Reduction | 105 | 13,759.3 ms | [12,999.7, 14,518.9] ms | 71.1% ([67.1%, 75.0%]) |

## Release Readiness Gate (Four Release Criteria Verification)

| Criterion | Target | Baseline | QuadGate | Verdict |
| :--- | :--- | ---: | ---: | :---: |
| Quality Parity or Gain (`Pass@1`) | `>= Baseline` (`+10%..+25%` target) | 88.6% | 100.0% (+11.4 pp) | PASS |
| Cache Integrity (Turns `2..N`) | `>= 75.0%` `cache_read` share | 26.8% | 93.2% (+66.4 pp) | PASS |
| Net Cost Compression | `>= 60.0%` total spend drop | `$24.2967` | `$5.6089` (-76.9%) | PASS |
| Wall-Clock Acceleration (`p50`) | `>= 1.8x` median speedup | 19335.5 ms | 4889.3 ms (3.95x) | PASS |

## Per-Suite Breakdown

### Suite: `coding` (`60` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 90.0% | 100.0% | +10.0 pp |
| Warm KV-Cache Hit Rate (%) | 21.4% | 55.0% | 63.5% input saved ([59.1%, 67.9%]) |
| Total Cost | `$14.8950` | `$2.5325` | 83.0% ([57.6%, 108.4%]) |
| Wall-Clock p50 / p90 / mean (ms) | 19449.2 / 23583.5 / 19343.3 | 5952.2 / 8891.8 / 6154.5 | 68.2% ([63.3%, 73.1%]) |

### Suite: `knowledge` (`45` pairs)

| Metric | Baseline | QuadGate | Paired 95% Reduction CI |
| :--- | ---: | ---: | ---: |
| Pass@1 (%) | 86.7% | 100.0% | +13.3 pp |
| Warm KV-Cache Hit Rate (%) | 23.9% | 57.8% | 57.8% input saved ([52.2%, 63.3%]) |
| Total Cost | `$9.4018` | `$3.0763` | 67.3% ([45.2%, 89.3%]) |
| Wall-Clock p50 / p90 / mean (ms) | 19335.5 / 23837.0 / 19392.6 | 3814.2 / 7951.5 / 4872.5 | 74.9% ([68.4%, 81.4%]) |

## Methodology & Honest Accounting

- **Exact Token & Pricing Accounting:** Zero hardcoded savings multipliers. Every turn's uncached input, KV-cache read, KV-cache creation, and output tokens are parsed via `extract_provider_usage` from raw Anthropic/OpenAI/Gemini `usage` payloads and priced via `MODEL_PRICING_PER_1M`.
- **Retry Tax & Classifier Overhead Included:** When Gate 4 1-strike promotion fires, `retry_turns` token spend (`retry_tax_usd`) is included inside `generative_cost_usd`, `retry_tax_ms` is included inside `wall_clock_ms`, and `classifier_cost_usd` + `hook_ipc_ms` + `classifier_latency_ms` are accounted in `total_cost_usd` and `wall_clock_ms`.
- **Isolated Ground-Truth Verification:** Coding tasks execute inside detached `git worktree` sandboxes verified by `pytest`; knowledge tasks execute in isolated temp sandboxes verified deterministically for citations, `[[wikilink]]` resolution, numeric balance tolerance (`0.01`), zero PII/secret leaks, required clauses, and Cartridge Gate 4 oracles.
- **Statistical Rigor:** Paired 95% Confidence Intervals use Student's `t` critical values (`df = n - 1`) across matched `(task_id, suite, run_index)` pairs.

## Tamper-Evident SHA-256 Receipt Manifest

| Task ID | Suite | Mode | Run | Pass | Promoted | Total Cost | Wall Clock (ms) | `task_sha256` | `receipt_sha256` |
| :--- | :--- | :--- | ---: | :---: | :---: | ---: | ---: | :--- | :--- |
| `CODING_01` | `coding` | `baseline` | 1 | PASS | no | `$0.7580` | 17820.5 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `a3d993a35460554ccb17de813db2b33c2ec33504b60237c8488762648d9a8ec1` |
| `CODING_01` | `coding` | `quadgate` | 1 | PASS | no | `$0.1779` | 6615.3 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `2746160f3642f15a33057c8198c2a85bb6390c84bf84f9fec6921409712ecc4e` |
| `CODING_02` | `coding` | `baseline` | 1 | PASS | no | `$0.1417` | 22197.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `39adec1d1aacddf4adf666921f212286b568572f8eef1c1e1f72714199b61b9e` |
| `CODING_02` | `coding` | `quadgate` | 1 | PASS | no | `$0.0408` | 7820.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `e4549e3714c575ea69e5e4fc60ad7606e5278dd21cfc6020257fc62540c28152` |
| `CODING_03` | `coding` | `baseline` | 1 | PASS | no | `$0.0610` | 15590.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `294ef4254ad325aaf225f98511f2cad9218fc9be6626c3d9b944b0118ff600cc` |
| `CODING_03` | `coding` | `quadgate` | 1 | PASS | no | `$0.0185` | 4664.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `403655d09dece4cca1a17b98ea66d433b3a846135e1bfb2d2532257a7618e57c` |
| `CODING_04` | `coding` | `baseline` | 1 | PASS | no | `$0.1564` | 19966.5 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `b8f5e66aaeb0252e84530b27cc4d9d7016d7fc53b0d01199f7c574776f98b3e3` |
| `CODING_04` | `coding` | `quadgate` | 1 | PASS | no | `$0.0480` | 7580.9 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `544672d1ea7a80aaf8d263d6fe24cd3fc32b207fb42d8320466a8b6dd7c3de1d` |
| `CODING_05` | `coding` | `baseline` | 1 | PASS | no | `$0.1430` | 24809.0 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `563f81ce3e24e6e6055773357027c244109765110b735e01df24a1d9934a7913` |
| `CODING_05` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0403` | 8858.2 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `e7257c793f6deda83772949a9f58c08cbc99f36fb2bbbf7ae851f38285bfec8c` |
| `CODING_06` | `coding` | `baseline` | 1 | PASS | no | `$0.0576` | 13740.2 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `1ed67434722603b4a6ce1380deade16a74a009a6064a3a684be4212b336d64e0` |
| `CODING_06` | `coding` | `quadgate` | 1 | PASS | no | `$0.0245` | 5797.9 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `c4a3be30c0a747388e0a3bcf65d0ddf1b433fe89c835ef37ed748ce41ce044e6` |
| `CODING_07` | `coding` | `baseline` | 1 | PASS | no | `$0.7606` | 18217.8 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `fb3daf2cd064fa2e865d2a6cd01064386ec5117f2dddb623fb26b308270bcc29` |
| `CODING_07` | `coding` | `quadgate` | 1 | PASS | no | `$0.1779` | 4463.5 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `cb64c2ee93c9dd7c9025db255fd5588f7eb1b0bc68ad7138db9c8ec64bfc6687` |
| `CODING_08` | `coding` | `baseline` | 1 | PASS | no | `$0.1392` | 22587.4 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `28fa71964f82e10c469abd08a80c8f1ab819cd71fb0275404ef86dcc7c25cdd0` |
| `CODING_08` | `coding` | `quadgate` | 1 | PASS | no | `$0.0417` | 7125.7 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `f74b34099ad73a461928201ac1b5ef9dc414566f39a11b31458abd097fef9472` |
| `CODING_09` | `coding` | `baseline` | 1 | PASS | no | `$0.0616` | 15995.7 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `81aca98ae6d03283a92647db7345d635460feccef10723f8b532828092861cae` |
| `CODING_09` | `coding` | `quadgate` | 1 | PASS | no | `$0.0185` | 3994.8 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `f06490d8efd6beabd009dc139a650bd01b674899cd0e5a3c97a2235f9394b93e` |
| `CODING_10` | `coding` | `baseline` | 1 | FAIL | no | `$0.1604` | 20444.2 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `2958df31b4e30b341a21485b5ad56c0f3a2e9d966ddc1e1222450d0ea723dd8e` |
| `CODING_10` | `coding` | `quadgate` | 1 | PASS | no | `$0.0494` | 6413.7 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `66c39774e3251caf0e527ad8ce0f85af0cdec19bd5dc1f621a3ff49dae6cc4eb` |
| `CODING_11` | `coding` | `baseline` | 1 | PASS | no | `$0.1459` | 25198.8 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `e9ee36d1cc80b0cfcc778baf6afaa0d0059b093b414a980764ee6c6060fb6664` |
| `CODING_11` | `coding` | `quadgate` | 1 | PASS | no | `$0.0317` | 3719.5 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `0fd9ed1417a5f4d3efb33c3030cefbc25b7bf447ff1ea9d63205e704eee7c027` |
| `CODING_12` | `coding` | `baseline` | 1 | PASS | no | `$0.1240` | 14271.6 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `022611d73be560b1d8e597f39ff8fa8411849273c4410f9f15dae0dc03e85823` |
| `CODING_12` | `coding` | `quadgate` | 1 | PASS | no | `$0.0468` | 6287.1 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `8dbe89cc43736849553b6c66aa8e0f90874e1e37f746d716570258e76a231ac4` |
| `CODING_13` | `coding` | `baseline` | 1 | PASS | no | `$0.0717` | 18304.7 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `0fba09e527cbaf6a09a4d3a1f46dc7437516c330aa7e28ccf377e00e1754e70b` |
| `CODING_13` | `coding` | `quadgate` | 1 | PASS | no | `$0.0169` | 3267.3 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `0230a645ffe933d5126e57f5749d4e61af2763bb8f0481e504afd7e458534e6c` |
| `CODING_14` | `coding` | `baseline` | 1 | PASS | no | `$0.1832` | 22977.1 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `a9d6009285bc60d3ee4f6384c139869a6f696a8edc1a78c0f2bf004fbbe8a15f` |
| `CODING_14` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0515` | 9553.9 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `3fac6f469c663ffca53750074829090118317e3649f61f8f1e473531f9ea5d2e` |
| `CODING_15` | `coding` | `baseline` | 1 | PASS | no | `$0.0995` | 16527.1 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `a266f5d14d541cffef44ff0767fd8f49c0e317933c75eb2eb1043f0ea815d90a` |
| `CODING_15` | `coding` | `quadgate` | 1 | PASS | no | `$0.0312` | 4422.7 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `2b539c4c5e2097fd14bc1efb9cd596da3d1fde396a68d2521351783806f3617e` |
| `CODING_16` | `coding` | `baseline` | 1 | PASS | no | `$0.7742` | 20450.5 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `e98b8ff8f8b18044dbda8cc92139faf4868acecbfcf9484ee0e17849340b19f7` |
| `CODING_16` | `coding` | `quadgate` | 1 | PASS | no | `$0.0106` | 3721.5 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `4e78babd490a9eeb49aa9ad44ad250f01598ec86cdaed590f1534e8f41328fa0` |
| `CODING_17` | `coding` | `baseline` | 1 | PASS | no | `$0.1324` | 20709.5 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `34a3b31ff38212d117cdf964521a2e02cf992449365b57d4f403869b386d51e7` |
| `CODING_17` | `coding` | `quadgate` | 1 | PASS | no | `$0.0017` | 5617.5 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `275b3f29e130c44aebbc5210569e37bc0a7470dff26c045dc6e18381dc1d0d22` |
| `CODING_18` | `coding` | `baseline` | 1 | FAIL | no | `$0.0608` | 14676.5 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `22442620f96b4d29c9945069af431362cef93e2f5e9452630d1df663ac2df976` |
| `CODING_18` | `coding` | `quadgate` | 1 | PASS | yes | `$0.0047` | 9067.7 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `3b06790d01182cc621d711e5f58880f3f9edfd7c11e82cfaf30a9840e308958f` |
| `CODING_19` | `coding` | `baseline` | 1 | PASS | no | `$0.7939` | 18932.0 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `6d1648ad8295621a6335014174bc222585cb5c7b9f310cd18e6ee2f6a4ca447a` |
| `CODING_19` | `coding` | `quadgate` | 1 | PASS | no | `$0.0097` | 4437.9 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `4b455532287bce29b21d20ef70604d944c0dcda8bf780eb68c55979e7c4e2250` |
| `CODING_20` | `coding` | `baseline` | 1 | PASS | no | `$0.1398` | 23447.3 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `3daa4b2be0b9d499649fd6224451eec5d4676c85ebcec11519cb29baa5a0cb6b` |
| `CODING_20` | `coding` | `quadgate` | 1 | PASS | no | `$0.0019` | 3701.8 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `d4d77ffda5cc8042727b6f230d918257499fed7dc5934c5988a8112e73e3a5b1` |
| `KNOWLEDGE_01` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6533` | 16311.0 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `c3d3b1f8e06b816cce3837266e3fc33cbc98656f099c34dde768c079932c426d` |
| `KNOWLEDGE_01` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2154` | 3811.1 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `73507634043c27d119edaac4475ad10afc36f2d9086a7a4f02acb5f5d42d8527` |
| `KNOWLEDGE_02` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1251` | 20974.3 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `ab75210fef1c4349866108e74cab67121b8fea347c42e7a70e689b6b049c548e` |
| `KNOWLEDGE_02` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0498` | 5291.9 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `8f3cefd6a728162434cd30f5e15d2388c97b2c03c58e8cec58af40e35b8ac82b` |
| `KNOWLEDGE_03` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0907` | 21225.7 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `78992a5a166ff33fae9908d607493e5c83893f31accd3c6e7a1a80d2918bf62a` |
| `KNOWLEDGE_03` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0188` | 3043.7 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `b04e853ac8969a6abae80a97e1d922f9aa03ed8a6c40bd9848cac368024248a6` |
| `KNOWLEDGE_04` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1237` | 14690.5 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `45e256d1b03cb2e4a432b36d8c48d1e557745f809278ce0fde4ea20b47684de8` |
| `KNOWLEDGE_04` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0487` | 5244.3 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `a5cdae505b3ef36e778ab70736e9ffd62e983a395a84744d9c74e88d33815602` |
| `KNOWLEDGE_05` | `knowledge` | `baseline` | 1 | FAIL | no | `$0.1202` | 18938.2 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `71a6e8528c1412166e3bce5525eaa97e5810868fe8f91ff9a475d6bcb946bda7` |
| `KNOWLEDGE_05` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0323` | 3698.3 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `2094ecff640586e2008e8f733bce26fbf2aff91a1524fd5e6d1ed5efaa02df04` |
| `KNOWLEDGE_06` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0223` | 23837.0 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `2adc62f9aa9e7bbf45e65e2591fd4188e929f7544dfe8e03c19e727be4a20a54` |
| `KNOWLEDGE_06` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0068` | 4265.0 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `be842ad23bd356bf56463ff9f309f50f49f636dd6fa4ef9e89cd5e6d2091d8b8` |
| `KNOWLEDGE_07` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6504` | 16405.4 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `1efdebe42857ddf627c368e66a0bd9f77f2164f9586718c1dceacd8dd4e2f0d7` |
| `KNOWLEDGE_07` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2035` | 3584.5 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `a5e697d5e75da6091cfd924bbbcfc67269f346cefd182691a229ed9c9d047669` |
| `KNOWLEDGE_08` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1228` | 21371.7 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `3aa3508d6d3b6a12e1365e11049421bc42f7fdab48297a5bc3cb8800bd8f4a29` |
| `KNOWLEDGE_08` | `knowledge` | `quadgate` | 1 | PASS | yes | `$0.0584` | 14902.3 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `6d808912d3a2221076c4ea5ffbe10b7827bb24a3ff41b74a21eeedf07323fbe5` |
| `KNOWLEDGE_09` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0911` | 21741.8 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `bc868859a91195e2ba396b93f30ca7e7c7738933d589f6c0c05cfaa456a383b4` |
| `KNOWLEDGE_09` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0187` | 3233.4 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `19268051515003d9f6af05c0d1b5c9186b28b03d3eabd3cc1a54d1bbbf7db448` |
| `KNOWLEDGE_10` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1282` | 15175.9 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `d64d95f1da04142b46df4a12487cff4381281477375f6c18a94d2447686bf121` |
| `KNOWLEDGE_10` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0494` | 5143.4 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `8956be21aa2549745195b362fb189bb1a0420b9adc23ac9359e62b1caebd0143` |
| `KNOWLEDGE_11` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1231` | 19335.5 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `f24258081f81ad76178613783d68ca3e97b8736fb7feee7769789fe7ec8970cc` |
| `KNOWLEDGE_11` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0324` | 3533.2 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `a9825ebd89e9a317f5636a0ba1628996f6ebf54614748e261d2dcd74c48235e6` |
| `KNOWLEDGE_12` | `knowledge` | `baseline` | 1 | FAIL | no | `$0.0224` | 24353.2 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `2cefbdc654511f3f80fa8d4547e6d50b221e2af28ea97abd926add08aff0f719` |
| `KNOWLEDGE_12` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0064` | 3938.4 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `a32d53dd75ce2c9d54860d52a6d2342a46bd25b4a50f799d3b4a1d73fa122c2b` |
| `KNOWLEDGE_13` | `knowledge` | `baseline` | 1 | PASS | no | `$0.6526` | 16810.3 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `ebd04b82f8798a4c4a1ac73fa259ce56b24da89d2749a40e1191104a867dfcce` |
| `KNOWLEDGE_13` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.2155` | 3814.2 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `fc29aa9206239ca1c1a343c13b7565db688b9a6ed2ca7c8102d0930739265bf3` |
| `KNOWLEDGE_14` | `knowledge` | `baseline` | 1 | PASS | no | `$0.1164` | 17587.0 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `a62f99eccb147c10c83d86ed7c8d6ca5bc28d7bc98cad7f73a2925b1d9865750` |
| `KNOWLEDGE_14` | `knowledge` | `quadgate` | 1 | PASS | yes | `$0.0506` | 12845.3 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `70b2a050a25010621f7e2602c37ccbd763655777d9031c57aaf0326c291dd1b9` |
| `KNOWLEDGE_15` | `knowledge` | `baseline` | 1 | PASS | no | `$0.0918` | 22131.5 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `d8a5bc21c4886e323e9704096571ad3d625624b0720bb8dccfb7704fbd0d5de5` |
| `KNOWLEDGE_15` | `knowledge` | `quadgate` | 1 | PASS | no | `$0.0188` | 2811.7 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `0a1a985cc60229b0650a8c8390ded06c550710d245807c201f9a07bee9618889` |
| `CODING_01` | `coding` | `baseline` | 2 | PASS | no | `$0.7580` | 17820.5 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `fe110880660cfedcd658b6d67a418bf3ff91f36cddbdb38938c159bb27be3a6a` |
| `CODING_01` | `coding` | `quadgate` | 2 | PASS | no | `$0.1779` | 4455.0 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `ee91e130648b235fefc234ae0bd8753f805b068f62af89ef6e9768231da4e216` |
| `CODING_02` | `coding` | `baseline` | 2 | PASS | no | `$0.1417` | 22197.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `732f6f749827d8a04f6acf348c9fa6148597a941735d0b2d0b3da7fb9aeac25e` |
| `CODING_02` | `coding` | `quadgate` | 2 | PASS | no | `$0.0408` | 6744.9 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `f47f62536da6ce7769432fc6872a0d6b210420915928e6dcbd8027abb4d50b3c` |
| `CODING_03` | `coding` | `baseline` | 2 | PASS | no | `$0.0610` | 15590.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `0d4fdad5656f267ad93d599f883076177f2921499ba40bc9159272235254a0b3` |
| `CODING_03` | `coding` | `quadgate` | 2 | PASS | no | `$0.0185` | 3908.1 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `022ddb9264cc37126a64ebf5a1f8ad1d2dfbdf000a9d99f14b36f852bcdb535f` |
| `CODING_04` | `coding` | `baseline` | 2 | PASS | no | `$0.1564` | 19966.5 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `27619592462bfe0590efd64b91b9bde73e464e93107ab27d40a25760de9ca76b` |
| `CODING_04` | `coding` | `quadgate` | 2 | PASS | no | `$0.0480` | 6811.0 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `4fdd4f4c33a5f04dcfd65faba08b5c2f67e15c55cb4ebe6251bafabd22b2f04d` |
| `CODING_05` | `coding` | `baseline` | 2 | PASS | no | `$0.1430` | 24809.0 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `c5ac5dd441f45d9dddcd6c284e446d1d688fc304cd420e069ba2fb0a09298514` |
| `CODING_05` | `coding` | `quadgate` | 2 | PASS | yes | `$0.0403` | 8045.2 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `694658d2f95df80d422a516e63415fbb45b43808cf6e5cef7b4c34d3607abdbe` |
| `CODING_06` | `coding` | `baseline` | 2 | PASS | no | `$0.0576` | 13740.2 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `dbdc10924bcc00a9a806291898b4443f34c995d1d7997b332007ec23ffeef2ad` |
| `CODING_06` | `coding` | `quadgate` | 2 | PASS | no | `$0.0245` | 5280.3 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `c4bba3e485c4512d4f0a651471c3ca638ab9dca890776a3f5c56928d019c48c7` |
| `CODING_07` | `coding` | `baseline` | 2 | PASS | no | `$0.7606` | 18217.8 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `bbc1d8782be60743affd1125fb213f3bc163f9084bba9b2fa4769701e4ecb9d1` |
| `CODING_07` | `coding` | `quadgate` | 2 | PASS | no | `$0.1779` | 4955.7 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `d22af5e6b455a1d7337914cdd4cb58a2c4be014ac25e2bacd5bbee58f2d7d866` |
| `CODING_08` | `coding` | `baseline` | 2 | PASS | no | `$0.1392` | 22587.4 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `4ed9820e8cafbd513c865280a32b1e231bb929165e565ca149d482e1949ca331` |
| `CODING_08` | `coding` | `quadgate` | 2 | PASS | no | `$0.0417` | 7441.1 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `4944bb1264ab31360457f53af344d3bb45e34285dc4b5a96275814b888461805` |
| `CODING_09` | `coding` | `baseline` | 2 | PASS | no | `$0.0616` | 15995.7 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `a49a1259585517bc9837dbc6416d8d90a2cb87dcada4ecd86b33de78f9f79716` |
| `CODING_09` | `coding` | `quadgate` | 2 | PASS | no | `$0.0185` | 6106.4 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `622efdc9e60a2e657c67a1e4b90e8267d01ed9edcbef5e830393c1a8506488b1` |
| `CODING_10` | `coding` | `baseline` | 2 | FAIL | no | `$0.1604` | 20444.2 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `5a4085694a01efe1ee2f594c5a8ce05b5d6bf16d01ccaff3bb9f0099a5a4c1b5` |
| `CODING_10` | `coding` | `quadgate` | 2 | PASS | no | `$0.0494` | 8878.1 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `9c6d6fb58550a0ab3b2e27f419abdaba59d56a3060caa1a3081942878f9efece` |
| `CODING_11` | `coding` | `baseline` | 2 | PASS | no | `$0.1459` | 25198.8 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `a4a61dea5d17a74dda80629e12c2cbdc1eaa2044ace50017d63786727e19a95c` |
| `CODING_11` | `coding` | `quadgate` | 2 | PASS | no | `$0.0317` | 6195.1 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `c040e96f9e5f53ef7cdbc3bfc1189a68f43fc95486477d55b83bdd7a97e74227` |
| `CODING_12` | `coding` | `baseline` | 2 | PASS | no | `$0.1240` | 14271.6 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `486c5511d6a4d3e41bc82c1b6a1dab77d35d8aec824040da9eab6c045848c29d` |
| `CODING_12` | `coding` | `quadgate` | 2 | PASS | no | `$0.0468` | 8047.4 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `928929c25c13c08eec6324de158ee31ea7118593132e39236395a35a224eafc4` |
| `CODING_13` | `coding` | `baseline` | 2 | PASS | no | `$0.0717` | 18304.7 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `5cd0aeb1988ebe17f83055827b87d8054dc59dc01f182215ed9ec6b16cb9d520` |
| `CODING_13` | `coding` | `quadgate` | 2 | PASS | no | `$0.0169` | 4143.4 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `ef6fb28005ed8f1588bd7ebb11162708b041e1e6030a116539a4deb911b981fe` |
| `CODING_14` | `coding` | `baseline` | 2 | PASS | no | `$0.1832` | 22977.1 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `6ec228e11687215640512cac5fd628113b6d9914ca0b146a9a288175e75abb87` |
| `CODING_14` | `coding` | `quadgate` | 2 | PASS | yes | `$0.0515` | 10985.3 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `0fd47dc5f27878bc3ee67c279e79c5f6e5a55efab27f538248ae0a7906e47623` |
| `CODING_15` | `coding` | `baseline` | 2 | PASS | no | `$0.0995` | 16527.1 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `6c7efc3ffafe4a086a4ef462683f633380f8a449deb4f63a2749d1462f3fac75` |
| `CODING_15` | `coding` | `quadgate` | 2 | PASS | no | `$0.0312` | 4133.9 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `11e26dc2ae9dae0c28204b6e68b5d61752c15956f88ce141d48533389a21551e` |
| `CODING_16` | `coding` | `baseline` | 2 | PASS | no | `$0.7742` | 20450.5 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `7f95e3d8c8b64ea2b1d2ed9b459bcbe01f69a1e74f6dad5f3f3707d1f09486af` |
| `CODING_16` | `coding` | `quadgate` | 2 | PASS | no | `$0.0106` | 3568.3 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `2de0efb024c2fd658886a1c2ff6665c8a19b33de299737313d7abf9916d5c1db` |
| `CODING_17` | `coding` | `baseline` | 2 | PASS | no | `$0.1324` | 20709.5 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `93eae453ab59713654ed921a4a5d6d71344c8a21c8be6061b93d00eca2c1b3b4` |
| `CODING_17` | `coding` | `quadgate` | 2 | PASS | no | `$0.0017` | 3399.4 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `663c8f77b373ded1bddd93ec888a518c90a58875ec567390f83250b2c580e903` |
| `CODING_18` | `coding` | `baseline` | 2 | FAIL | no | `$0.0608` | 14676.5 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `4587e27522531a6b051c9a18c5caec9fb612629459cf66dd3853cac74c4102c0` |
| `CODING_18` | `coding` | `quadgate` | 2 | PASS | yes | `$0.0047` | 8113.1 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `39249487f4a7338b8c6886439cca63f86d136f6541eb21ff4006cdaceeb51998` |
| `CODING_19` | `coding` | `baseline` | 2 | PASS | no | `$0.7939` | 18932.0 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `3ba672145e41d8a13f38e4bb411a45d2fc2c7cf98898f9fe1cc6035cc765c52b` |
| `CODING_19` | `coding` | `quadgate` | 2 | PASS | no | `$0.0097` | 7192.7 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `bebed1cc0980ebc2b0b82ad319b464c7c1cc6d78c2353988506208ed1f112425` |
| `CODING_20` | `coding` | `baseline` | 2 | PASS | no | `$0.1398` | 23447.3 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `2cd37f5f2a9c5ba1993d979bd1b1913e3050fecd704c0b13bf5e3cc59fb5c86d` |
| `CODING_20` | `coding` | `quadgate` | 2 | PASS | no | `$0.0019` | 5530.4 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `ce13b2c9f00f591624c2c92e5ea6130fbb079aa784cd6da1ca148451ec80a8df` |
| `KNOWLEDGE_01` | `knowledge` | `baseline` | 2 | PASS | no | `$0.6533` | 16311.0 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `52493a4de7269f2c07962ccb1d8970f77bc742ed49fc9780f1d2f9fda0634f2f` |
| `KNOWLEDGE_01` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.2154` | 4189.5 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `4b4e700a3782fbd5b56b760c8f832890d5588cb97ed5eb000ba2326f5858a86e` |
| `KNOWLEDGE_02` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1251` | 20974.3 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `e969190a7cd925cd69baed48426fbc4c4da5301e0d82471fcb92df85cf78e40d` |
| `KNOWLEDGE_02` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0498` | 5299.7 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `ae33fedfff194c0caad72b86bad1fe9c0329e1d6a419f342642d18dbe72c2ee1` |
| `KNOWLEDGE_03` | `knowledge` | `baseline` | 2 | PASS | no | `$0.0907` | 21225.7 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `ee750879ac01ecd088442a43ca5cb04a8af1b5480a4c00c67ef6e0a9210c53b2` |
| `KNOWLEDGE_03` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0188` | 3057.4 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `533e48a903ce42a6164e8b058ee5c2e1d504c30d56ef9a38d5e68d6331d38a38` |
| `KNOWLEDGE_04` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1237` | 14690.5 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `67334b33ccbf22c2f0890ebb6beb1d76dd580d8c807371248411a622aaa42899` |
| `KNOWLEDGE_04` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0487` | 5234.3 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `cd3ba6bcf891c63d2a1af6214d7ee3a13eb64d608eb1068bac4d1580a62e8b28` |
| `KNOWLEDGE_05` | `knowledge` | `baseline` | 2 | FAIL | no | `$0.1202` | 18938.2 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `eb5198fec0caef9679b2e27230af5b4424afa6eff8bc47d6a674be9cad7d6bcc` |
| `KNOWLEDGE_05` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0323` | 3727.4 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `dd36872fb072120a1e399a3a71b8cbbb3ad5f59db4f3c56305cbf81e860c019b` |
| `KNOWLEDGE_06` | `knowledge` | `baseline` | 2 | PASS | no | `$0.0223` | 23837.0 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `182d9ef4ab97ddf04c1ca726b3f97a40f82b738f1713f9ab3a5fdeb5680eca37` |
| `KNOWLEDGE_06` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0068` | 4227.6 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `fa8c0a257c2619788d0c7e1284aa65080395e3820b87bf8fc6148a5483c50221` |
| `KNOWLEDGE_07` | `knowledge` | `baseline` | 2 | PASS | no | `$0.6504` | 16405.4 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `8ad4e1b5475068545d1c47936a25af1784bf56370dea73abc9b62c624fbbe29d` |
| `KNOWLEDGE_07` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.2035` | 3570.5 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `0d3fcdd95bf34b30ce46a6668ba43555862d8e40e35b63fd4e82c0034d6a42a7` |
| `KNOWLEDGE_08` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1228` | 21371.7 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `c0e88eb387fcc09ac77de4194e35dc90f19c99a9d07c8ee1eb250d3cf142c3de` |
| `KNOWLEDGE_08` | `knowledge` | `quadgate` | 2 | PASS | yes | `$0.0584` | 11583.9 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `45bf690f7c7039e73faa78a0b01de42f172b23dca99be1a30be06b0094aa336d` |
| `KNOWLEDGE_09` | `knowledge` | `baseline` | 2 | PASS | no | `$0.0911` | 21741.8 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `306a6a93d35b29c80a6380b285b75d70d472c32d2d17e2507c3ccb2e0cc40565` |
| `KNOWLEDGE_09` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0187` | 2933.8 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `a6da9b5d3b05a3f4cd4b12ffa58bd078df4cadb8dc3c871c149de497ed80dba1` |
| `KNOWLEDGE_10` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1282` | 15175.9 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `79089917340e8197d449559406388a5362116e4ea51bdb0643dc348a5d4b32e6` |
| `KNOWLEDGE_10` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0494` | 5172.0 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `3c2180c4f6588373226332c27d9d8b764bc352d59e4ce27aec16ca5e4d036fe2` |
| `KNOWLEDGE_11` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1231` | 19335.5 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `ee70c4785c271e4ad591d5d2c24800b63caa4cb8cd653ce7cbdb59cd4fd0e292` |
| `KNOWLEDGE_11` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0324` | 3538.3 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `bf0a2d14df4061751db06409347368a73634fafcde5ca6bfc4fcbdb91afe4e8d` |
| `KNOWLEDGE_12` | `knowledge` | `baseline` | 2 | FAIL | no | `$0.0224` | 24353.2 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `ce1aa54881f2df0a410d8a6398cdc9a33310e6fc26ef0f1d30222a29cc5aec0d` |
| `KNOWLEDGE_12` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0064` | 3965.6 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `02c070a19bc438bdf5dbf10da569cd7a2d5a47e7fe7a671509ac08db0cd51bd8` |
| `KNOWLEDGE_13` | `knowledge` | `baseline` | 2 | PASS | no | `$0.6526` | 16810.3 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `33cee60fcd0d5d23291605cb49d8bbd96028ae4d5abde18e60d2c1a2e6fd9721` |
| `KNOWLEDGE_13` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.2155` | 3542.4 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `7720fe382873a0c35e0b8a83ade8403400cb746451c0c5ab46b03de90ab05bb8` |
| `KNOWLEDGE_14` | `knowledge` | `baseline` | 2 | PASS | no | `$0.1164` | 17587.0 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `db56efb2281a50564fed860dccc71e93c0664d1091fac05ea6964e52b780d54e` |
| `KNOWLEDGE_14` | `knowledge` | `quadgate` | 2 | PASS | yes | `$0.0506` | 10064.1 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `c716febb57204688d8eb6389384a43c5c06e2aea14da3cbfca230079d0116865` |
| `KNOWLEDGE_15` | `knowledge` | `baseline` | 2 | PASS | no | `$0.0918` | 22131.5 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `f87e56fff721975affce471a0bbcb95c8bb121c233c50207b68c829d403fb755` |
| `KNOWLEDGE_15` | `knowledge` | `quadgate` | 2 | PASS | no | `$0.0188` | 2814.6 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `889625b2f573aa689787ede24d2abd8badb3ac318c93a6566f0364024a13f704` |
| `CODING_01` | `coding` | `baseline` | 3 | PASS | no | `$0.7580` | 17820.5 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `1027c7b7f409a9090ff0e3e94129bc13747447a35b756769502d72552232c7c0` |
| `CODING_01` | `coding` | `quadgate` | 3 | PASS | no | `$0.1779` | 6492.4 | `6da517bac96745fb0ba55a8e45ba426506c46bda47ac339b51ee73584cc3286e` | `294cd8e41d6c7211d025320ca5370c9955244dfd779c444dec5ddaed021d4a6d` |
| `CODING_02` | `coding` | `baseline` | 3 | PASS | no | `$0.1417` | 22197.7 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `4846c3b11189bf3b9850e6b4157202a0e3db62fd684ecc304413241ae523889b` |
| `CODING_02` | `coding` | `quadgate` | 3 | PASS | no | `$0.0408` | 9015.8 | `9db41e2ae327bff2bd98b5172e48673ddaef55cf1a78fa79263c2fbd130c7097` | `149a15e115a46b12d0a0c8a003c5fc73b9fe5e5c45ec21467b6e575218265614` |
| `CODING_03` | `coding` | `baseline` | 3 | PASS | no | `$0.0610` | 15590.8 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `f9f9ddde07919e4e226f6fc95fcb3b2bcf36ce52ba8478033202a4de8cc5e8f0` |
| `CODING_03` | `coding` | `quadgate` | 3 | PASS | no | `$0.0185` | 4746.5 | `b4c61eb99a2f1b85f77278dcc25ffc126c77bdaa16c04d38e9cc72a5d6333101` | `a9d68bdf63bced18a1a121dcf050afa5dfbe30d8b984ea6ffcf2099541a40d3d` |
| `CODING_04` | `coding` | `baseline` | 3 | PASS | no | `$0.1564` | 19966.5 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `9cf9134f77e0d990e28df68e19260cc40342c3eef8deb701b63ffe4ece3def13` |
| `CODING_04` | `coding` | `quadgate` | 3 | PASS | no | `$0.0480` | 8436.0 | `c778d16687594cc6bdcb2b3f6f015c63c6944b687217732d488b87e41c5fe150` | `145669714dfd3541edac27ac469f21abbf00ea01f26eda321d2b2041619f4d07` |
| `CODING_05` | `coding` | `baseline` | 3 | PASS | no | `$0.1430` | 24809.0 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `08f045a33e39e661987eeaf6170a78ce821a1f000d2532663fb55e76cc2b5635` |
| `CODING_05` | `coding` | `quadgate` | 3 | PASS | yes | `$0.0403` | 11189.0 | `6ef3d2e5cf4f2f5842ef8c8eddc55eb34d78b2be47284d157ae7f033b36229bd` | `55d3bde5af6ac426756bbfea2427ebb329264f02f39d039ce6e088f552ded91f` |
| `CODING_06` | `coding` | `baseline` | 3 | PASS | no | `$0.0576` | 13740.2 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `bbdf693a7da04dacb2b3266739cfef01855f94311707e8a9bc8f709c5c790190` |
| `CODING_06` | `coding` | `quadgate` | 3 | PASS | no | `$0.0245` | 5607.3 | `ec285e0b6ee189ca27753a3aefc37930eb93afe2bea7ef238428b6355c61a5c2` | `46544a61488ca91ddd629473bdeac69ebad9bd29c14b16c09b68c3c1806d1c5c` |
| `CODING_07` | `coding` | `baseline` | 3 | PASS | no | `$0.7606` | 18217.8 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `f682e0a7b00918dc5c985b9d70580454a42c51e29c81663fcf39311e07eec337` |
| `CODING_07` | `coding` | `quadgate` | 3 | PASS | no | `$0.1779` | 6691.1 | `774b4958b5e181e2267c7c6dd2f2af30a6b15b18ef03bdcbbbc50b322de9288e` | `ad48325168f47cf78adf524db237c6f4fbe69558a40d2078490f41e1ace1acec` |
| `CODING_08` | `coding` | `baseline` | 3 | PASS | no | `$0.1392` | 22587.4 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `d6a68799ac262fd433c51f07bcf73ce84f1ba6362c4e78a3cf01d62e508f7cfa` |
| `CODING_08` | `coding` | `quadgate` | 3 | PASS | no | `$0.0417` | 8717.0 | `d226a0b9a81a74396ba2dde86af48d76a7463592627f641a2462c1607ebc585d` | `23e41d254228f740515eb7f686e48585e6cf47185c7ea0c9e5db47af3577a234` |
| `CODING_09` | `coding` | `baseline` | 3 | PASS | no | `$0.0616` | 15995.7 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `963d222584457e667114b845304e9c14a6580df91baef4c5a72004ed0ccb427f` |
| `CODING_09` | `coding` | `quadgate` | 3 | PASS | no | `$0.0185` | 4889.3 | `5fc57bbce142fe6b2780aad476dc81f94779291d2942d1b0bf03e42b3c27a80b` | `dde8bf2d2f08a91fee47dd35aae818e7d6ba4e6b094ada5234c1586707d5a01f` |
| `CODING_10` | `coding` | `baseline` | 3 | FAIL | no | `$0.1604` | 20444.2 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `dd6ac07f62001f1fe6d6163666bcd9757589ecef40857aabd5a8088e89a14ca3` |
| `CODING_10` | `coding` | `quadgate` | 3 | PASS | no | `$0.0494` | 8153.0 | `320cbe2eaedf6cc8a4070d4072dcc7e403ef2ca05c39702703f55afa68179e23` | `41cc21260fd4b32c5a17f934e31cc3d5d250fb24d1519a2fd575fa11a6fefb64` |
| `CODING_11` | `coding` | `baseline` | 3 | PASS | no | `$0.1459` | 25198.8 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `c8215cea29de07f7a838ee8a362228be07c9dc89fc13a76774e858d8dee03059` |
| `CODING_11` | `coding` | `quadgate` | 3 | PASS | no | `$0.0317` | 4034.4 | `ea69b87d427f4bd67c56bd9e7aa7dc33ebf62f41a42a7beecf3c4d923c4cdc9e` | `888e2f07632653973595e247e728f5a2357fd4fc7420141957921b512f9577b4` |
| `CODING_12` | `coding` | `baseline` | 3 | PASS | no | `$0.1240` | 14271.6 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `8e9b0cade67513b97bd39f6baafcfbf41bec9e58ea7fc92210f7a2545c454ab2` |
| `CODING_12` | `coding` | `quadgate` | 3 | PASS | no | `$0.0468` | 6431.7 | `f583d6e973bb1f74e80b1eac8bec64efef4c38c7b7bf0c34e21f107ac6326c10` | `49c6e4b171d1674a37a964b02cd868b15ba3cb8beb8b7ae6680cf30a7e96bc98` |
| `CODING_13` | `coding` | `baseline` | 3 | PASS | no | `$0.0717` | 18304.7 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `d1d353a305b5d13c8cc5d1ef0e6e90defc0aa8af04fce54eca8c251b59bfa4f5` |
| `CODING_13` | `coding` | `quadgate` | 3 | PASS | no | `$0.0169` | 5268.9 | `ccfb6eae50abc751d22afd33d5cf67fb25014f9977a4db0e46fcba71eb00687c` | `2bac536511f969e958e7f0479a5bfbcd925dfc818e12c4465a5f88f1c7b83edc` |
| `CODING_14` | `coding` | `baseline` | 3 | PASS | no | `$0.1832` | 22977.1 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `d058fae432f84a9dfc547bf986ed58d55df4fe67641356e8f0b92c6d982cb5e3` |
| `CODING_14` | `coding` | `quadgate` | 3 | PASS | yes | `$0.0515` | 12172.2 | `b83d56a2ebe62cadb4f89a64cd951e09c244afa7dec5bb49156b3e4732c1d7e7` | `6f575f740881140729aae402088229741bd2bcf0ed58485a864164b393c056e2` |
| `CODING_15` | `coding` | `baseline` | 3 | PASS | no | `$0.0995` | 16527.1 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `24c61e1d13204e30603606fea955f4e766b998cf579ee6ad8d4ed9e1a6a33ba7` |
| `CODING_15` | `coding` | `quadgate` | 3 | PASS | no | `$0.0312` | 4512.2 | `de11aa522112787f517e3390e936dbe54d0e231b5e65f11ef02b74b895d27150` | `6bda64ee8fd9739b3ae45297b94efb6f0a1d8f7155ad4f3da8f4352527bd29a2` |
| `CODING_16` | `coding` | `baseline` | 3 | PASS | no | `$0.7742` | 20450.5 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `af76904c049367259c70284032461646acd6daefd13d7630ad72adc17c94d3f8` |
| `CODING_16` | `coding` | `quadgate` | 3 | PASS | no | `$0.0106` | 3493.1 | `a28bb74c7aa60ced1fd0b48f48ce85461e958c1491a641da429c837066802e64` | `005cca18595a98d82519d434abf74a264d91ccff540caef18266283caf12507b` |
| `CODING_17` | `coding` | `baseline` | 3 | PASS | no | `$0.1324` | 20709.5 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `894b5bda237b2a16eaea1a7fa61bad84ef96a09eb499c8725eee3ed4b800a92e` |
| `CODING_17` | `coding` | `quadgate` | 3 | PASS | no | `$0.0017` | 3084.2 | `292a12820637aaeea399f8f9c564c4fca2e852a89c531a38fbc5399ebedab26d` | `731a0a57862e0dce729e03e7e539663fc66d96f6082853f2d986f0548a4b577a` |
| `CODING_18` | `coding` | `baseline` | 3 | FAIL | no | `$0.0608` | 14676.5 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `b50b4923fb189befe61d8025633ceb2b4254ddb89698853b7f0c7500a6fae843` |
| `CODING_18` | `coding` | `quadgate` | 3 | PASS | yes | `$0.0047` | 8076.8 | `25c352a3d4c7b273d77bd83b29d610f04cc0b410d025ee0410bcfe3201fc0b99` | `500cf8f27610889c0ee49590dd4588895a2d22baa9edf45bf676ac202033f8c3` |
| `CODING_19` | `coding` | `baseline` | 3 | PASS | no | `$0.7939` | 18932.0 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `fbc62042ad1308b73589be6cc4c11c2f16b834abbbf636b631d21858fefed5c8` |
| `CODING_19` | `coding` | `quadgate` | 3 | PASS | no | `$0.0097` | 3494.8 | `c558fcfa2c371e7a1c2f59fe713e980350fc5b5b667b4d034633b547690b1ec5` | `9ba035d7942dbb3a68b67e2a8ce7cf7dc176f51ab0ae65e012ab93d35e21f14e` |
| `CODING_20` | `coding` | `baseline` | 3 | PASS | no | `$0.1398` | 23447.3 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `4580bae899a2d38624aa750a4b4536d2f880a6e1d0d08882dd11f2a53ad4c479` |
| `CODING_20` | `coding` | `quadgate` | 3 | PASS | no | `$0.0019` | 3694.5 | `c0fc091e750e0d132bd0d5cfe2c5a7efde6c4a5a3cb09988d7629a4816911830` | `8071b6e598d84826f8638d21c289642e6568f0fe7048481e4eca626d55135de0` |
| `KNOWLEDGE_01` | `knowledge` | `baseline` | 3 | PASS | no | `$0.6533` | 16311.0 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `fffc4638f722aa9a57041d8e64132992f4ebb133ef500efcaa6e4ddfa5ee54cb` |
| `KNOWLEDGE_01` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.2154` | 3809.8 | `bf304209185e1cee5b4cf92915f012c9bf5470414012d19048de255b5d42e483` | `746e9e6311222c1d5b8f3704a0418609456cf3125192a85423757e53cdd7e28d` |
| `KNOWLEDGE_02` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1251` | 20974.3 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `af763a1f94b55711cd663d80939b97cf39a805dd67f3b8e92cbc72ff11ce48b6` |
| `KNOWLEDGE_02` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0498` | 5338.7 | `7e50577d1159f3063f75a61e92ade547829f8623eae3776dd14ce9e8bf5bb822` | `446512bf918631afa003860ed2ff57ac13da1fe8d7e265e1816e7b425e257083` |
| `KNOWLEDGE_03` | `knowledge` | `baseline` | 3 | PASS | no | `$0.0907` | 21225.7 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `e90d0681b9169aedd94395e7a89434a941a92b650ab930e9d4663fbeeb6eb498` |
| `KNOWLEDGE_03` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0188` | 3070.2 | `2e5553ca29f3aba3031f16608f1857e0f816ba61645aecb8c4e1e0ae06745c98` | `2f156471dc0909f228d2c679c73fc688670ee900801e475d20918011803e674f` |
| `KNOWLEDGE_04` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1237` | 14690.5 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `6d32845c7b460c4b1260139d306360c2014590d58fe3daaa14142c393daab4e5` |
| `KNOWLEDGE_04` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0487` | 5367.9 | `a8245af2d45d2066d487756a613c673ca11ea8fab2540137e438d8fa027fa0d2` | `5e2d6f4705392cbda22def8b2bd786dda3419ff2b8be8ab53636bd5eaa48161d` |
| `KNOWLEDGE_05` | `knowledge` | `baseline` | 3 | FAIL | no | `$0.1202` | 18938.2 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `b1be378d0ee70094258c4c8932532fb2cbff85ec465a0a8c81a1ab5ab7c89617` |
| `KNOWLEDGE_05` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0323` | 3778.0 | `a69ddf979ea7b9dcb99ea7930ff8c9f3a2ba7f21632b1211833b579c75bb3e77` | `8756b927411d594d9ffc6c03bb2bfaaca4eb8b0971852dfd8e2322e98bb08702` |
| `KNOWLEDGE_06` | `knowledge` | `baseline` | 3 | PASS | no | `$0.0223` | 23837.0 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `e70985281be8fb76224e51a8d7a862fbc76af72cc1f1d1180b44e1be5b849d17` |
| `KNOWLEDGE_06` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0068` | 4275.2 | `68471bac23d5b2fa523fd6678c658cb4551f68ed3f6b0844b68af0318d6676ee` | `98e34820aefcdb9bc46b03cc9a555cf3e44c02186648a5a9f7132cce7ce294b1` |
| `KNOWLEDGE_07` | `knowledge` | `baseline` | 3 | PASS | no | `$0.6504` | 16405.4 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `7f352c2187f8ccdab68de1c353128d2254663ffeabe70c1b6addc3ba1ac4a188` |
| `KNOWLEDGE_07` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.2035` | 3813.2 | `2d784a98f9a529d606c5cd360729e0146231b7594c98cd58e6a66a86ff2f79f1` | `9bb071c3437d7b2397827777b9c82472a131127dc9773c2da98bfc6bd7a04140` |
| `KNOWLEDGE_08` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1228` | 21371.7 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `4b661eee2a38fbe122b6bd41b80e73354dd7d4f3630a4068fa1e77dfae4421e7` |
| `KNOWLEDGE_08` | `knowledge` | `quadgate` | 3 | PASS | yes | `$0.0584` | 8612.8 | `e59c5d0dff16205df1494506f023de28d484409825b51d28afb0582d2225f5a6` | `6cc110cbca05052a4480e138cc09a4f45a281542d51c4bbda727eb3b22ae4ae1` |
| `KNOWLEDGE_09` | `knowledge` | `baseline` | 3 | PASS | no | `$0.0911` | 21741.8 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `725beb1701b10c28bff8f1bae1f916c58a0d2a8aa59ca2eeaba1dcb1d4db4bef` |
| `KNOWLEDGE_09` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0187` | 3024.7 | `a731bad3ce50910a41852507073fac5614c72538d80f03b36bcf1f8686a34a46` | `016c6b84832d9732190a288b279b78980d2dca44470219c3970c8dfd082cf617` |
| `KNOWLEDGE_10` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1282` | 15175.9 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `886ead623be75b96307a72ab1b3146560cf2165b2a0df1a1f6459ea85e75aa2a` |
| `KNOWLEDGE_10` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0494` | 5176.8 | `f9b87004333504c1c506c50fbd76ee699a837f8204df493706608c7bb3e9fa2a` | `794928f32469e29076aa496bf06c5c992a55714a1fd51b3ea454e06634ad79c3` |
| `KNOWLEDGE_11` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1231` | 19335.5 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `81ab57a0c5505eab02ac9a64599416c83682cf8cf9ada9938c539b0b966eeba1` |
| `KNOWLEDGE_11` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0324` | 3529.2 | `37b40ea04681a85957c67913e762629c9b47ca208fee18c2be5b16dcd363710f` | `0f3238f9ea5ea37055926ef5b536cbb11af1f2f54f6566ce3a250be1448c91f5` |
| `KNOWLEDGE_12` | `knowledge` | `baseline` | 3 | FAIL | no | `$0.0224` | 24353.2 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `a0750e38a1fa481b461c6e5526060141da34bab7f2292ec9ecb38198da89d745` |
| `KNOWLEDGE_12` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0064` | 3937.1 | `a205de23816fd75792702ccb451a0763b6ce84f8cfb4ae5585a43ce72d9dec7d` | `1a222292ab5f73635882f3a476d5f2990c468bbe679e39d13dc29758fae9e4e2` |
| `KNOWLEDGE_13` | `knowledge` | `baseline` | 3 | PASS | no | `$0.6526` | 16810.3 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `892e4c3481878ad98071000193a8c2d0ae7b287f5a6fb70ecbb8a1f4ccf09565` |
| `KNOWLEDGE_13` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.2155` | 3563.9 | `8b21e66b36e69214ae5833b52fbd36bf7d1026db77454514b5ba5234138c7ad5` | `830e5cfff47a193e7b7397a4bfd539589883ac5e327382a3981c9e081710977d` |
| `KNOWLEDGE_14` | `knowledge` | `baseline` | 3 | PASS | no | `$0.1164` | 17587.0 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `5a95fcce2cf4526d4d4a4157a5ffe1ebc6aac4e925e8e22af151d9618c8a493c` |
| `KNOWLEDGE_14` | `knowledge` | `quadgate` | 3 | PASS | yes | `$0.0506` | 6959.5 | `e329cf45bbf919203cd16142a483d529d4611dea7ee3e5bc3c13771e219cc000` | `453220a1bcb5be3eda3e3cdc3024cf22c5d0a8d6882b6c8ea37a30f879e839cb` |
| `KNOWLEDGE_15` | `knowledge` | `baseline` | 3 | PASS | no | `$0.0918` | 22131.5 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `edb6a0c2d98ab9bfd8ece3811d23e87e027691594747945e606292379a7bf2d0` |
| `KNOWLEDGE_15` | `knowledge` | `quadgate` | 3 | PASS | no | `$0.0188` | 2925.8 | `a520b05db05075e8e6337579df5d41243ac300e32da83d7b9cd9ebfec954a3d3` | `a9dd26a7af61a8bff976d2e271a3eada4bca4701d98fe4abede445b0d5c4f4c6` |
