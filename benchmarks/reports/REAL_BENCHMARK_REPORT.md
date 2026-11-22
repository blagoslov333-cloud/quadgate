# QuadGate Real Live Multi-Model Benchmark Report

- **Generated (UTC):** `2026-11-22 01:13:35Z`
- **Verified Live Receipts:** `1007/1007` (100% SHA-256 tamper-evident, zero synthetic traces)
- **Total Live API Spend (Generative + Decision1 Classifier):** `$12.2028` (`$0.0706` Decision 1 spend)
- **Benchmarked Frontier Models (5):** `gemini-4-hydrogen, gemini-3.1-pro-preview, gemini-4-carbon, gemini-4-argon, gemini-4-neon`

## 1. Overall Per-Model Impact (All Suites Combined)

| Model | Tier | Base Pass@1 | +QuadGate Pass@1 | Delta | Base Spend | +QuadGate Spend (incl. D1) | Cost Delta | Base Warm Cache | +QG Warm Cache |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `Gemma 4 31B IT` | Open-Weights 31B | 76.9% | 94.9% | +18.0 pp | `$0.0000` | `$0.0030` | -0.0% | 0.0% | 0.0% |
| `Gemini 3.1 Flash-Lite` | Budget / Floor | 72.5% | 79.8% | +7.3 pp | `$0.1679` | `$0.1175` | -30.1% | 0.0% | 59.6% |
| `Gemini 3.8 Flash` | Mid / Velocity | 79.8% | 79.8% | +0.0 pp | `$0.7630` | `$0.5997` | -21.4% | 2.7% | 62.5% |
| `Gemini 4 Hydrogen` | Frontier Efficiency | 92.0% | 92.6% | +0.6 pp | `$0.8795` | `$0.3729` | -57.6% | 0.0% | 68.8% |
| `Gemini 3.1 Pro` | Frontier 3.1 Pro | 88.5% | 96.2% | +7.7 pp | `$1.2321` | `$0.6516` | -47.1% | 0.0% | 63.9% |
| `Gemini 4 Carbon` | Frontier Flagship | 87.2% | 84.4% | -2.8 pp | `$1.7469` | `$1.2071` | -30.9% | 0.0% | 61.0% |
| `Gemini 4 Argon` | Frontier Reasoning | 92.3% | 100.0% | +7.7 pp | `$1.0668` | `$0.3772` | -64.6% | 0.0% | 69.2% |
| `Gemini 4 Neon` | Frontier Coding | 96.0% | 100.0% | +4.0 pp | `$1.8066` | `$0.6227` | -65.5% | 0.0% | 69.0% |
| `QuadGate ECC Router` | Adaptive Gate-3 ECC | — | 77.2% | Adaptive | — | `$0.5882` | Routed ({'gemini-4-carbon': 33, 'gemini-3.8-flash': 30, 'gemini-3.1-flash-lite': 16}) | — | 0.0% |

### Frontier Aggregate (All Live Frontier Models Combined)
- **Frontier Pass@1:** `89.6%` -> **`90.2%`** (`+0.6 pp` across `203` paired frontier runs)
- **Frontier Total Spend:** `$6.7320` -> **`$3.2316`** (`-52.0%`)
- **Frontier Warm Cache Hit:** `0.0%` -> **`66.2%`**

## 2. Gate-3 Adaptive ECC Router vs. Fixed Standalone Models

- **ECC Router (`quadgate-ecc-router`):** `77.2%` Pass@1 across `79` tasks at `$0.5882` total spend (routing mix: `{'gemini-4-carbon': 33, 'gemini-3.8-flash': 30, 'gemini-3.1-flash-lite': 16}`).

| Reference Standalone Baseline | Ref Pass@1 | ECC Delta | Ref Cost | ECC Cost | ECC Cost Savings |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | 73.3% | +3.9 pp | `$0.0000` | `$0.5882` | +0.0% |
| `gemini-3.1-flash-lite` | 72.2% | +5.1 pp | `$0.0354` | `$0.5882` | -1561.4% |
| `gemini-3.8-flash` | 74.7% | +2.5 pp | `$0.3294` | `$0.5882` | -78.6% |
| `gemini-4-hydrogen` | 0.0% | +77.2 pp | `$0.0111` | `$0.5882` | -5176.0% |
| `gemini-4-carbon` | 83.5% | -6.3 pp | `$0.6213` | `$0.5882` | +5.3% |

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
| `gemma-4-31b-it` | `baseline` | 16,672 | 0 | 5,884 | 17,181 | `$0.0000` | `$0.0000` |
| `gemma-4-31b-it` | `quadgate` | 17,093 | 0 | 6,838 | 21,384 | `$0.0000` | `$0.0030` |
| `gemini-3.1-flash-lite` | `baseline` | 559,323 | 0 | 18,742 | 0 | `$0.1679` | `$0.0000` |
| `gemini-3.1-flash-lite` | `quadgate` | 231,892 | 341,640 | 24,031 | 0 | `$0.1026` | `$0.0149` |
| `gemini-3.8-flash` | `baseline` | 543,682 | 15,010 | 23,356 | 71,069 | `$0.7630` | `$0.0000` |
| `gemini-3.8-flash` | `quadgate` | 216,293 | 360,238 | 29,054 | 76,998 | `$0.5869` | `$0.0127` |
| `gemini-4-hydrogen` | `baseline` | 521,798 | 0 | 2,454 | 10,456 | `$0.8795` | `$0.0000` |
| `gemini-4-hydrogen` | `quadgate` | 163,366 | 360,075 | 3,244 | 9,677 | `$0.3690` | `$0.0040` |
| `gemini-3.1-pro-preview` | `baseline` | 517,699 | 0 | 1,927 | 14,468 | `$1.2321` | `$0.0000` |
| `gemini-3.1-pro-preview` | `quadgate` | 186,656 | 330,195 | 2,142 | 15,215 | `$0.6476` | `$0.0040` |
| `gemini-4-carbon` | `baseline` | 579,014 | 0 | 25,450 | 33,437 | `$1.7469` | `$0.0000` |
| `gemini-4-carbon` | `quadgate` | 229,838 | 360,090 | 25,471 | 44,418 | `$1.1946` | `$0.0125` |
| `gemini-4-argon` | `baseline` | 522,356 | 0 | 1,716 | 494 | `$1.0668` | `$0.0000` |
| `gemini-4-argon` | `quadgate` | 159,859 | 360,070 | 1,344 | 412 | `$0.3733` | `$0.0039` |
| `gemini-4-neon` | `baseline` | 523,559 | 0 | 1,798 | 13,933 | `$1.8066` | `$0.0000` |
| `gemini-4-neon` | `quadgate` | 134,808 | 300,058 | 1,350 | 9,990 | `$0.6195` | `$0.0032` |
| `quadgate-ecc-router` | `quadgate` | 58,914 | 0 | 27,819 | 55,720 | `$0.5757` | `$0.0124` |

## 5. Per-Benchmark Suite Breakdown

### SWE-Agent Multi-File Bugfix (`swe_agent`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3 | 100.0% | — | `$0.0000` | 209890 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 3 | 100.0% | +0.0 pp (p=1.000) | `$0.0013` | 212252 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 8 | 62.5% | — | `$0.0140` | 7553 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 8 | 62.5% | +0.0 pp (p=1.000) | `$0.0200` | 9842 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 8 | 25.0% | — | `$0.0630` | 22073 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 8 | 12.5% | -12.5 pp (p=1.000) | `$0.0384` | 17007 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 8 | 37.5% | — | `$0.0787` | 159920 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 8 | 0.0% | -37.5 pp (p=0.250) | `$0.0771` | 172334 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 8 | 12.5% | — | `$0.0716` | 16057 ms | 0.0% | 1 |

### HumanEval+ (EvalPlus) (`humaneval_plus`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3 | 100.0% | — | `$0.0000` | 36307 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 3 | 100.0% | +0.0 pp (p=1.000) | `$0.0006` | 54362 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 20 | 95.0% | — | `$0.0053` | 1545 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 20 | 95.0% | +0.0 pp (p=1.000) | `$0.0097` | 3355 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 20 | 100.0% | — | `$0.0505` | 7245 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 20 | 100.0% | +0.0 pp (p=1.000) | `$0.0616` | 9147 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 20 | 95.0% | — | `$0.1309` | 19966 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 20 | 95.0% | +0.0 pp (p=1.000) | `$0.1347` | 22871 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 20 | 95.0% | — | `$0.0312` | 6614 ms | 0.0% | 0 |

### BigCodeBench v0.1.4 (`bigcodebench`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3 | 66.7% | — | `$0.0000` | 93068 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 3 | 100.0% | +33.3 pp (p=1.000) | `$0.0006` | 91128 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 14 | 64.3% | — | `$0.0045` | 1485 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 14 | 64.3% | +0.0 pp (p=1.000) | `$0.0076` | 3106 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 14 | 57.1% | — | `$0.0970` | 13208 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 14 | 57.1% | +0.0 pp (p=1.000) | `$0.1140` | 17148 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 14 | 78.6% | — | `$0.1169` | 20702 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 14 | 71.4% | -7.1 pp (p=1.000) | `$0.1425` | 25737 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 14 | 64.3% | — | `$0.1263` | 15980 ms | 0.0% | 2 |

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

### GSM8K Reasoning (`gsm8k`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3 | 100.0% | — | `$0.0000` | 49031 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 3 | 100.0% | +0.0 pp (p=1.000) | `$0.0002` | 11868 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 25 | 96.0% | — | `$0.0044` | 846 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 25 | 96.0% | +0.0 pp (p=1.000) | `$0.0097` | 1923 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 25 | 96.0% | — | `$0.0266` | 3115 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 25 | 92.0% | -4.0 pp (p=1.000) | `$0.0580` | 6570 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 25 | 100.0% | — | `$0.0634` | 14355 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 25 | 100.0% | +0.0 pp (p=1.000) | `$0.1786` | 34675 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 25 | 100.0% | — | `$0.1084` | 21304 ms | 0.0% | 25 |

### Enterprise Finance/Legal/Vault (`enterprise_knowledge`)

| Model | Mode | n | Pass@1 | Delta (McNemar p) | Total Cost | p50 Latency | Cache Hit % | Promoted |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemma-4-31b-it` | `baseline` | 3 | 0.0% | — | `$0.0000` | 61224 ms | 0.0% | 0 |
| `gemma-4-31b-it` | `quadgate` | 3 | 33.3% | +33.3 pp (p=1.000) | `$0.0000` | 96681 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `baseline` | 12 | 0.0% | — | `$0.0072` | 1690 ms | 0.0% | 0 |
| `gemini-3.1-flash-lite` | `quadgate` | 12 | 0.0% | +0.0 pp (p=1.000) | `$0.0107` | 2062 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `baseline` | 12 | 41.7% | — | `$0.0922` | 16580 ms | 0.0% | 0 |
| `gemini-3.8-flash` | `quadgate` | 12 | 50.0% | +8.3 pp (p=1.000) | `$0.1270` | 16391 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `baseline` | 1 | 0.0% | — | `$0.0111` | 6437 ms | 0.0% | 0 |
| `gemini-4-hydrogen` | `quadgate` | 2 | 0.0% | +0.0 pp (p=1.000) | `$0.0243` | 7363 ms | 0.0% | 0 |
| `gemini-3.1-pro-preview` | `quadgate` | 1 | 0.0% | — | `$0.0207` | 9285 ms | 0.0% | 0 |
| `gemini-4-carbon` | `baseline` | 12 | 66.7% | — | `$0.2315` | 34084 ms | 0.0% | 0 |
| `gemini-4-carbon` | `quadgate` | 12 | 66.7% | +0.0 pp (p=1.000) | `$0.2346` | 29136 ms | 0.0% | 0 |
| `quadgate-ecc-router` | `quadgate` | 12 | 58.3% | — | `$0.2507` | 32731 ms | 0.0% | 0 |

## 6. Methodology & Honest Caveats

- **Live Coverage Status:** Complete across `long_context_kv` (`7` Gemini models, excluding Gemma Free-Tier 15k TPM) and multi-model `agentic_security`; remaining suites/jobs (`enterprise_knowledge` (54 runs)) halted cleanly when AI Studio prepayment billing credits hit `HTTP 402: Your prepayment credits are depleted` at `$9.42` logged spend and can resume automatically via `python -m benchmarks.real_suite.harness --all` once topped up.
- **Zero Mocked Traces / Zero Hardcoded Savings:** All input, cached, candidate output, and thinking tokens come directly from live API `usageMetadata`.
- **Thoughts Tokens Billed:** Reasoning tokens (`thoughtsTokenCount`) are billed at each model's full output rate, and `Decision1Driver` compute (`$0.10/1M` input tokens) plus 1-Strike retry turns are included inside `+QuadGate` costs.
- **No Ground-Truth Leakage:** Gate 4 only executes `py_compile`, `ruff` undefined-name checks, cartridge domain checks (double-entry balance, wikilinks, required clauses), and `Decision1Driver` clause completeness. Hidden `pytest` / `EvalPlus` / `BigCodeBench` test suites run strictly post-completion.
- **Gemma 4 31B Free-Tier Limit:** Google offers `gemma-4-31b-it` at `$0.00` on the Gemini API with a 15,000 input-TPM cap; therefore `long_context_kv` (~16.9k tokens/turn) is evaluated across all 7 Gemini models while Gemma 4 is evaluated across the remaining 6 suites.
- **External Cross-Provider Keys:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, and `DEEPSEEK_API_KEY` were unset in the execution environment; all 8 live models (`5` Frontier + `2` Flash + `1` Open-Weights) + Gate-3 ECC ran live via the Google Gemini/Decision1 API.
- **Single-Turn Overhead Honesty:** On short single-turn prompts without prefix reuse, Gate 1/4 classifier calls and 1-Strike checks add modest latency and `$0.00003` classifier spend; QuadGate's economic gains concentrate on multi-turn agent loops, long-context KV prefix reuse, security containment, enterprise domain oracles, and Gate-3 ECC adaptive routing.
