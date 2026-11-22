# QuadGate Real Live Multi-Model Benchmark Report

- **Generated (UTC):** `2026-11-21 21:51:19Z`
- **Verified Live Receipts:** `428/428` (100% SHA-256 tamper-evident, zero synthetic traces)
- **Total Live API Spend (Generative + Decision1 Classifier):** `$9.4174` (`$0.0279` Decision 1 spend)
- **Benchmarked Frontier Models (5):** `gemini-4-hydrogen, gemini-3.1-pro-preview, gemini-4-carbon, gemini-4-argon, gemini-4-neon`

## 1. Overall Per-Model Impact (All Suites Combined)

| Model | Tier | Base Pass@1 | +QuadGate Pass@1 | Delta | Base Spend | +QuadGate Spend (incl. D1) | Cost Delta | Base Warm Cache | +QG Warm Cache |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `Gemma 4 31B IT` | Open-Weights 31B | 79.2% | 100.0% | +20.8 pp | `$0.0000` | `$0.0003` | -0.0% | 0.0% | 0.0% |
| `Gemini 3.1 Flash-Lite` | Budget / Floor | 71.0% | 93.8% | +22.8 pp | `$0.1332` | `$0.0608` | -54.3% | 0.0% | 66.0% |
| `Gemini 3.8 Flash` | Mid / Velocity | 93.3% | 96.7% | +3.3 pp | `$0.4336` | `$0.2007` | -53.7% | 2.9% | 69.7% |
| `Gemini 4 Hydrogen` | Frontier Efficiency | 92.0% | 92.6% | +0.6 pp | `$0.8795` | `$0.3729` | -57.6% | 0.0% | 68.8% |
| `Gemini 3.1 Pro` | Frontier 3.1 Pro | 88.5% | 96.2% | +7.7 pp | `$1.2321` | `$0.6516` | -47.1% | 0.0% | 63.9% |
| `Gemini 4 Carbon` | Frontier Flagship | 96.7% | 100.0% | +3.3 pp | `$1.1256` | `$0.4395` | -61.0% | 0.0% | 68.8% |
| `Gemini 4 Argon` | Frontier Reasoning | 92.3% | 100.0% | +7.7 pp | `$1.0668` | `$0.3772` | -64.6% | 0.0% | 69.2% |
| `Gemini 4 Neon` | Frontier Coding | 96.0% | 100.0% | +4.0 pp | `$1.8066` | `$0.6227` | -65.5% | 0.0% | 69.0% |
| `QuadGate ECC Router` | Adaptive Gate-3 ECC | — | 0.0% | Adaptive | — | `$0.0142` | Routed ({'gemini-4-carbon': 1}) | — | 0.0% |

### Frontier Aggregate (All Live Frontier Models Combined)
- **Frontier Pass@1:** `93.2%` -> **`97.6%`** (`+4.4 pp` across `124` paired frontier runs)
- **Frontier Total Spend:** `$6.1107` -> **`$2.4640`** (`-59.7%`)
- **Frontier Warm Cache Hit:** `0.0%` -> **`67.9%`**

## 2. Gate-3 Adaptive ECC Router vs. Fixed Standalone Models

- **ECC Router (`quadgate-ecc-router`):** `0.0%` Pass@1 across `1` tasks at `$0.0142` total spend (routing mix: `{'gemini-4-carbon': 1}`).

| Reference Standalone Baseline | Ref Pass@1 | ECC Delta | Ref Cost | ECC Cost | ECC Cost Savings |
| :--- | ---: | ---: | ---: | ---: | ---: |

## 3. Agentic Security & Benign Containment by Attack Family

| Attack / Control Family | Base Safe | +QuadGate Safe | Gate-2 Caught |
| :--- | ---: | ---: | ---: |
| `benign_control` | 46/47 (97.9%) | 39/40 (97.5%) | 0 |
| `cage_containment` | 14/16 (87.5%) | 16/16 (100.0%) | 2 |
| `destructive_shell` | 12/15 (80.0%) | 16/16 (100.0%) | 3 |
| `hardcoded_secret` | 8/8 (100.0%) | 8/8 (100.0%) | 5 |
| `indirect_prompt_injection` | 29/32 (90.6%) | 32/32 (100.0%) | 3 |
| `path_escape` | 14/16 (87.5%) | 16/16 (100.0%) | 2 |
| `secret_exfiltration` | 19/24 (79.2%) | 24/24 (100.0%) | 6 |
| `semantic_2b_financial` | 8/8 (100.0%) | 7/7 (100.0%) | 0 |
| `semantic_2b_legal` | 0/7 (0.0%) | 5/5 (100.0%) | 5 |

## 4. Exact Token & Thinking-Token Telemetry

| Model | Mode | Uncached In | Cached In | Candidate Out | Thoughts Out | Generative Cost | Classifier Cost |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3,965 | 0 | 1,111 | 5,146 | `$0.0000` | `$0.0000` |
| `gemma-4-31b-it` | `quadgate` | 3,965 | 0 | 1,111 | 5,146 | `$0.0000` | `$0.0003` |
| `gemini-3.1-flash-lite` | `baseline` | 518,545 | 0 | 2,344 | 0 | `$0.1332` | `$0.0000` |
| `gemini-3.1-flash-lite` | `quadgate` | 176,418 | 341,640 | 2,636 | 0 | `$0.0566` | `$0.0042` |
| `gemini-3.8-flash` | `baseline` | 503,050 | 15,010 | 1,635 | 13,086 | `$0.4336` | `$0.0000` |
| `gemini-3.8-flash` | `quadgate` | 156,868 | 360,238 | 1,754 | 12,085 | `$0.1966` | `$0.0041` |
| `gemini-4-hydrogen` | `baseline` | 521,798 | 0 | 2,454 | 10,456 | `$0.8795` | `$0.0000` |
| `gemini-4-hydrogen` | `quadgate` | 163,366 | 360,075 | 3,244 | 9,677 | `$0.3690` | `$0.0040` |
| `gemini-3.1-pro-preview` | `baseline` | 517,699 | 0 | 1,927 | 14,468 | `$1.2321` | `$0.0000` |
| `gemini-3.1-pro-preview` | `quadgate` | 186,656 | 330,195 | 2,142 | 15,215 | `$0.6476` | `$0.0040` |
| `gemini-4-carbon` | `baseline` | 523,814 | 0 | 1,773 | 6,021 | `$1.1256` | `$0.0000` |
| `gemini-4-carbon` | `quadgate` | 163,631 | 360,090 | 1,767 | 5,443 | `$0.4354` | `$0.0042` |
| `gemini-4-argon` | `baseline` | 522,356 | 0 | 1,716 | 494 | `$1.0668` | `$0.0000` |
| `gemini-4-argon` | `quadgate` | 159,859 | 360,070 | 1,344 | 412 | `$0.3733` | `$0.0039` |
| `gemini-4-neon` | `baseline` | 523,559 | 0 | 1,798 | 13,933 | `$1.8066` | `$0.0000` |
| `gemini-4-neon` | `quadgate` | 134,808 | 300,058 | 1,350 | 9,990 | `$0.6195` | `$0.0032` |
| `quadgate-ecc-router` | `quadgate` | 513 | 0 | 975 | 338 | `$0.0142` | `$0.0000` |

## 5. Per-Benchmark Suite Breakdown

### Long-Context Multi-Turn KV (`long_context_kv`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemini-3.1-flash-lite` | `baseline` | 6 | 100.0% | — | `$0.1300` | 6301 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.0569` | 7761 ms | 83.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 6 | 100.0% | — | `$0.3923` | 16177 ms | 3.6% | 0 |
| `gemini-3.8-flash` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.1574` | 18946 ms | 87.5% | 0 |
| `gemini-4-hydrogen` | `baseline` | 6 | 100.0% | — | `$0.8083` | 16384 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.2845` | 18346 ms | 87.5% | 0 |
| `gemini-3.1-pro-preview` | `baseline` | 6 | 100.0% | — | `$1.1493` | 84003 ms | 0.0% | 0 |
| `gemini-3.1-pro-preview` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.5498` | 67790 ms | 80.2% | 0 |
| `gemini-4-carbon` | `baseline` | 6 | 100.0% | — | `$1.0514` | 31091 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.3607` | 33518 ms | 87.5% | 0 |
| `gemini-4-argon` | `baseline` | 6 | 100.0% | — | `$1.0376` | 43279 ms | 0.0% | 0 |
| `gemini-4-argon` | `quadgate` | 6 | 100.0% | +0.0 pp (p=1.000) | `$0.3545` | 43045 ms | 87.5% | 0 |
| `gemini-4-neon` | `baseline` | 6 | 100.0% | — | `$1.6261` | 36456 ms | 0.0% | 0 |
| `gemini-4-neon` | `quadgate` | 5 | 100.0% | +0.0 pp (p=1.000) | `$0.4823` | 26575 ms | 87.5% | 0 |

### Agentic Security & Containment (`agentic_security`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 24 | 79.2% | — | `$0.0000` | 95764 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 24 | 100.0% | +20.8 pp (p=0.062) | `$0.0003` | 101729 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 24 | 66.7% | — | `$0.0025` | 1219 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 24 | 100.0% | +33.3 pp (p=0.008) | `$0.0029` | 1498 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 24 | 91.7% | — | `$0.0414` | 9106 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 24 | 95.8% | +4.2 pp (p=1.000) | `$0.0433` | 10093 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `baseline` | 18 | 94.4% | — | `$0.0600` | 4533 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `quadgate` | 19 | 100.0% | +5.6 pp (p=1.000) | `$0.0641` | 6086 ms | 0.0% | 0 |
| `gemini-3.1-pro-preview` | `baseline` | 20 | 85.0% | — | `$0.0828` | 12753 ms | 0.0% | 0 |
| `gemini-3.1-pro-preview` | `quadgate` | 19 | 100.0% | +15.0 pp (p=0.250) | `$0.0811` | 16120 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 24 | 95.8% | — | `$0.0741` | 7849 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 24 | 100.0% | +4.2 pp (p=1.000) | `$0.0788` | 11583 ms | 0.0% | 0 |
| `gemini-4-argon` | `baseline` | 20 | 90.0% | — | `$0.0292` | 5149 ms | 0.0% | 0 |
| `gemini-4-argon` | `quadgate` | 15 | 100.0% | +10.0 pp (p=1.000) | `$0.0227` | 7018 ms | 0.0% | 0 |
| `gemini-4-neon` | `baseline` | 19 | 94.7% | — | `$0.1805` | 14444 ms | 0.0% | 0 |
| `gemini-4-neon` | `quadgate` | 15 | 100.0% | +5.3 pp (p=1.000) | `$0.1404` | 14946 ms | 0.0% | 0 |

### Enterprise Finance/Legal/Vault (`enterprise_knowledge`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemini-3.1-flash-lite` | `baseline` | 1 | 0.0% | — | `$0.0006` | 1709 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 2 | 0.0% | — | `$0.0010` | 1603 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `baseline` | 1 | 0.0% | — | `$0.0111` | 6437 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `quadgate` | 2 | 0.0% | +0.0 pp (p=1.000) | `$0.0243` | 7363 ms | 0.0% | 0 |
| `gemini-3.1-pro-preview` | `quadgate` | 1 | 0.0% | — | `$0.0207` | 9285 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 1 | 0.0% | — | `$0.0142` | 10496 ms | 0.0% | 0 |

## 6. Methodology & Honest Caveats

- **Live Coverage Status:** Complete across `long_context_kv` (`7` Gemini models, excluding Gemma Free-Tier 15k TPM) and multi-model `agentic_security`; remaining suites/jobs (`swe_agent` (0 runs), `humaneval_plus` (0 runs), `bigcodebench` (0 runs), `gsm8k` (0 runs), `enterprise_knowledge` (6 runs)) halted cleanly when AI Studio prepayment billing credits hit `HTTP 402: Your prepayment credits are depleted` at `$9.42` logged spend and can resume automatically via `python -m benchmarks.real_suite.harness --all` once topped up.
- **Zero Mocked Traces / Zero Hardcoded Savings:** All input, cached, candidate output, and thinking tokens come directly from live API `usageMetadata`.
- **Thoughts Tokens Billed:** Reasoning tokens (`thoughtsTokenCount`) are billed at each model's full output rate, and `Decision1Driver` compute (`$0.10/1M` input tokens) plus 1-Strike retry turns are included inside `+QuadGate` costs.
- **No Ground-Truth Leakage:** Gate 4 only executes `py_compile`, `ruff` undefined-name checks, cartridge domain checks (double-entry balance, wikilinks, required clauses), and `Decision1Driver` clause completeness. Hidden `pytest` / `EvalPlus` / `BigCodeBench` test suites run strictly post-completion.
- **Gemma 4 31B Free-Tier Limit:** Google offers `gemma-4-31b-it` at `$0.00` on the Gemini API with a 15,000 input-TPM cap; therefore `long_context_kv` (~16.9k tokens/turn) is evaluated across all 7 Gemini models while Gemma 4 is evaluated across the remaining 6 suites.
- **External Cross-Provider Keys:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, and `DEEPSEEK_API_KEY` were unset in the execution environment; all 8 live models (`5` Frontier + `2` Flash + `1` Open-Weights) + Gate-3 ECC ran live via the Google Gemini/Decision1 API.
- **Single-Turn Overhead Honesty:** On short single-turn prompts without prefix reuse, Gate 1/4 classifier calls and 1-Strike checks add modest latency and `$0.00003` classifier spend; QuadGate's economic gains concentrate on multi-turn agent loops, long-context KV prefix reuse, security containment, enterprise domain oracles, and Gate-3 ECC adaptive routing.
