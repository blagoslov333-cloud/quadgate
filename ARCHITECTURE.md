# QuadGate (`quadgate`): Model-Agnostic & Harness-Agnostic 4-Gate Architecture

**Project Mission:** An open-source, empirically verifiable discriminative control plane that sits between any AI agent harness (**Antigravity**, **Claude Code**, **OpenAI Codex CLI**, **MCP Clients**) and any generative LLM provider, using ultra-fast classifier models (**Decision 1**, **JEV**, **LAYAA**, **ModernBERT/DeBERTa ONNX**, or zero-output logprob routers) to increase execution speed, maximize token efficiency, and cut compute spend without degrading output quality.

---

## Part I: Realistic Step-by-Step Stress Test & Problem Resolution

Before locking a single line of architecture, every component must survive harsh engineering scrutiny. Below is the realistic interrogation of all nine stages of the system—evaluating whether each step actually beats the un-gated baseline, what breaks in real production, and how v2 solves each flaw.

---

### Step 1: Persistent Local Daemon & Synchronous Hook Interception
* **What it does:** Intercepts agent lifecycle events over localhost (`127.0.0.1:8787`) instead of booting heavy scripts on every tool call.
* **Is it better than what it used to be?**
  * **Yes.** Un-gated agents burn thousands of tokens discovering boundaries via trial-and-error, while naive cold-boot Python hooks add $350\text{ ms}$ to $600\text{ ms}$ of interpreter startup lag per call. A persistent daemon drops local IPC latency to $< 5\text{ ms}$.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **IDE Freeze Hazard:** If the daemon crashes, hangs on a network timeout, or hits a port conflict, synchronous hooks block the agent harness and brick the user's coding session.
  2. **Zombie Processes & Multi-Repo Collisions:** Opening three workspaces at once causes port clashes or cross-contaminates project indexes if one daemon serves the wrong repository root.
  3. **Cross-OS Friction:** Unix domain sockets fail on standard Windows setups without WSL, hurting open-source adoption.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Split Fail-Open / Fail-Closed Micro-Shim:** The hook invokes a zero-dependency thin client with a hard $450\text{ ms}$ socket deadline. Optimization gates (Gates 1, 3, and 4) **fail-open** (if the daemon is unreachable, the agent continues normally with zero downtime). Hard security rules in Gate 2 (path escapes, `.env` reads, destructive shell commands) are evaluated via deterministic regex inside the micro-shim itself so safety **fails-closed** even if the daemon is dead.
  * **Workspace-Keyed Multiplexing:** A single user-space daemon binds `127.0.0.1:8787` with an authentication token stored in `~/.quadgate/run.token` and namespaces every SQLite index by canonical `realpath(workspace_root)` hash. Auto-spawns lazily if not running; auto-exits after 30 minutes of idle time.

---

### Step 2: Pass-0 Local Index & The One-Line Gist Engine
* **What it does:** Pre-filters local files/records in $< 4\text{ ms}$ via SQLite FTS5 and feeds compact gists to the classifier instead of full files.
* **Is it better than what it used to be?**
  * **Conditionally yes**—it eliminates blind directory crawling, **but in v1 it fails onboarding** if humans or LLMs have to manually write summaries first.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **The Cold-Start Tax (OSS Adoption Killer):** Nobody cloning a GitHub tool will wait 20 minutes or pay `$2.00` in API tokens to summarize a 5,000-file monorepo before running their first prompt.
  2. **Stale Gist Drift:** Developers switch Git branches (`git checkout feature-x`) or edit 30 files; stale gists trick Gate 1 into injecting deleted symbols or missing new ones.
  3. **The "Line-600 Blind Spot":** One-line summaries omit internal helper functions, error codes, and edge-case constants buried deep inside files.
  4. **Lexical Mismatch in Pass-0:** SQLite FTS5 uses BM25 keyword matching. If the user says *"fix login throttling"* and the file is named `rate_limiter.ts`, pure keyword FTS5 can miss the candidate before the classifier ever sees it.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Zero-Token AST Cold-Start (`tree-sitter`):** On `quadgate init`, deterministic `tree-sitter` parsers extract file paths, exported symbol signatures, class/function names, and docstrings/headings in $< 1.5\text{ seconds}$ for 5,000 files at `$0.00` LLM cost. LLM-generated prose gists are generated *lazily* only for hot files touched more than 3 times.
  * **Content-Addressed Git Sync:** Every indexed file is keyed by `(workspace_hash, file_path, blake3_hash)`. Before Pass-0 runs, `quadgate` checks file `mtime` + `blake3` deltas in $< 3\text{ ms}$ and re-parses modified files on the fly—making `git checkout` instant.
  * **Two-Tier Structural Gists + Hybrid BM25/Trigram:** Each record stores both a 1-line semantic gist *and* a compressed symbol table (exported functions, error classes, config keys), plus SQLite trigram/synonym expansion so `rate_limiter.ts` surfaces even on fuzzy wording.

---

### Step 3: Model-Agnostic Classifier Layer (Decision 1, JEV, LAYAA, Local ONNX)
* **What it does:** Scores candidate files, invariants, and routing rubrics in a single discriminative pass with zero generative output tokens.
* **Is it better than what it used to be?**
  * **Dramatically better** than generative router prompts, which cost input + output tokens and add $1.5\text{ s}$ to $4.0\text{ s}$ of latency.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **Severe Probability Miscalibration Across Models:** Decision 1 returns calibrated probabilities where $0.82 \approx 82\%$ true confidence. Open-source models like **LAYAA**, **JEV**, `DeBERTa-v3`, or `BGE-reranker` return raw logits or skewed sigmoid scores where a strong match might be $0.48$ on one model and $0.97$ on another. Hardcoding `threshold = 0.82` breaks every swap!
  2. **Head-Count & Context Limits:** Decision 1 evaluates up to 1,000 parallel heads in $\approx 215\text{ ms}$. Local CPU classifiers running 1,000 pairwise cross-encoder passes sequentially will take $4,000\text{ ms}$ and make the system *slower* than no gates at all.
  3. **Cross-Head Blindness:** Parallel heads cannot reason about dependencies *between* two candidates ($A$ requires $B$).
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Mandatory Conformal Calibration Layer (`CalibrationProfile`):** Every `ClassifierDriver` (`Decision1Driver`, `JevDriver`, `LayaaDriver`, `LocalOnnxDriver`, `LogprobApiDriver`) runs a one-time 64-sample Golden Calibration Benchmark on install. QuadGate fits isotonic regression / temperature scaling ($p_{\text{cal}} = \sigma(z / T)$) so gates operate on **calibrated quantiles and Expected Calibration Error ($ECE \le 0.04$)** rather than raw model numbers.
  * **Hardware-Aware Candidate Budgeting:** Cloud parallel-head drivers (`Decision1`, `JEV` server) receive up to $N = 800$ candidates per pass; local CPU/GPU drivers (`LAYAA`, `ONNX` bi-encoder + cross-encoder cascade) automatically narrow via Pass-0 bi-encoder embeddings to top $K = 32$ candidates so local CPU latency stays $< 65\text{ ms}$.
  * **Deterministic Co-Change Graph Boost:** Host algebra augments independent head probabilities with Git co-change edges (files historically committed together get a deterministic adjacency boost $\Delta p$) to cure cross-head blindness without extra LLM hops.

---

### Step 4: Gate 1 — Turn-1 Intake, Privacy Air-Gap & Context Sieve
* **What it does:** Intercepts Turn 1 of a session, enforces local privacy rules, dampens casual brainstorming from triggering edits, and injects only the top matching files and rules.
* **Is it better than what it used to be?**
  * **Yes.** Cuts 60% to 75% of blind exploratory file reads (`ls`, `grep`, `view_file` loops) and prevents `always_on` rule bloat.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **False-Negative Context Starvation:** If Gate 1 injects 3 files and locks out search, and the real bug is in a 4th file, the agent hallucinates a broken fix.
  2. **Prompt Prefix-Cache Pollution:** If Gate 1 mutates system instructions on Turn 2, Turn 3, and Turn 4, it invalidates the LLM provider's KV prefix cache ($75\%$ to $90\%$ input token discount lost!), **increasing** cost instead of cutting it.
  3. **Over-Aggressive Brainstorm Blocking:** Misclassifying an urgent bug description phrased politely (*"Could we maybe fix auth?"*) as counterfactual brainstorming locks write tools unfairly.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Soft-Hint Injection (Never Blindfold Search):** Gate 1 *appends* high-confidence file paths and snippets (`p_cal >= 0.80`) at the **tail** of the Turn-1 user message as `[QUADGATE_CONTEXT_HINTS]` while leaving read/search tools enabled. If the hints suffice, the LLM skips exploration naturally; if a 4th file is needed, the LLM reads it immediately and the daemon logs a **Miss Event** to auto-link those files for next time.
  * **Strict Turn-1 Pinning:** Gate 1 executes **only** on `turn_index == 1` (or explicit `/compact` resets). Turns $2 \dots N$ pass straight through untouched so 100% of the conversation prefix remains warm-cached.

---

### Step 5: Gate 2 — Pre-Mutation Invariant Firewall
* **What it does:** Evaluates proposed file edits and shell commands right before execution against safety, architecture, and scope invariants.
* **Is it better than what it used to be?**
  * **Yes.** Catches bad writes and out-of-cage edits in $\approx 50\text{ ms}$ to $215\text{ ms}$ instead of letting broken code trigger 3 turns of test-failure stack traces.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **Infinite Block Loops (Token Incinerator):** Gate 2 blocks a write with a vague message (*"Invariant violation 0.81"*); the LLM retries practically the same edit 4 times in a row, burning massive output tokens while going nowhere.
  2. **The "Over-Strict Bouncer" on Refactors:** Legitimate architectural migrations intentionally break legacy patterns and get blocked repeatedly.
  3. **Diff Fragmentation Blindness:** Checking a 5-line `replace_file_content` patch without surrounding AST context produces false alarms.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Actionable Violation Receipts + 2-Strike Escalation:** Every block returns the exact violated rule ID, its plain-English remediation directive, and an inline bypass token instruction. If the same rule blocks **twice** in one session, Gate 2 stops looping and immediately surfaces a single user confirmation prompt (or escalates cleanly).
  * **Two-Stage Deterministic + Semantic Check:** Stage 2A runs $1\text{ ms}$ deterministic AST/path-cage/secret checks (`0` false negatives on `.env`, `rm -rf`, unauthorized directories). Stage 2B runs classifier semantic invariants *only* on files inside active rule scopes, passing the modified function's full enclosing AST block rather than raw line fragments.

---

### Step 6: Gate 3 — Cold-Boundary Subagent Tier Governor
* **What it does:** Intercepts new subagent / worker spawns (which always start with a cold empty KV cache) and assigns the lowest-cost model tier capable of first-pass completion.
* **Is it better than what it used to be?**
  * **Massively better** than spawning frontier models (`Opus` / `Argon` / `GPT-5-Pro`) for mechanical leaf chores like writing boilerplate tests, updating types, or extracting markdown tables.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **Hidden Complexity Underestimation:** A short subagent prompt (*"Update User schema email field"*) looks simple in text length, yet touches 14 downstream foreign keys—causing a floor-tier model to fail catastrophically.
  2. **Harness Limitation:** Not every agent harness exposes a native `invoke_subagent` JSON argument hook (e.g., Codex CLI or custom MCP runners).
  3. **Cross-Vendor Prompt Fragility:** Rewriting an Anthropic-tuned subagent prompt to a tiny open-weight or Gemini Flash-Lite endpoint can break tool-calling formatting.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Graph-Weighted Expected Cost of Completion ($ECC$):** Gate 3 never routes on prompt text alone. It queries the local `tree-sitter` import graph to compute **fan-in / blast radius** ($d_{\text{imports}}$) and historical file failure rate ($r_{\text{fail}}$), routing to model $M_k$ only when:
    $$\text{ECC}(M_k) = C_{\text{run}}(M_k) + \left(1 - P_{\text{pass}}(M_k \mid \text{task}, d_{\text{imports}}, r_{\text{fail}})\right) \cdot C_{\text{promote}}(M_{\text{frontier}})$$
    strictly beats the frontier baseline by at least a $25\%$ safety margin.
  * **Same-Family Default + Gateway Fallback:** By default, Gate 3 tiers *within* the active provider family (`Opus` $\rightarrow$ `Sonnet` $\rightarrow$ `Haiku`, or `Argon` $\rightarrow$ `Carbon` $\rightarrow$ `Hydrogen`/`Flash-Lite`) to eliminate prompt-format breakage. For harnesses without subagent hooks, QuadGate provides an optional lightweight localhost LLM API proxy (`127.0.0.1:8788`) that inspects cold-session system headers (`messages.length == 1` + worker tag) and routes model IDs transparently at the HTTP layer.

---

### Step 7: Gate 4 — Epistemic Exit Inspector & Self-Healing Loop
* **What it does:** Runs when an agent or subagent attempts to finish (`Stop`), pairing fast deterministic truth checks with classifier completeness scoring and automatic promotion on failure.
* **Is it better than what it used to be?**
  * **Yes.** Eliminates silent half-finished work ("I updated 2 of the 4 endpoints and stopped") without paying for a frontier LLM critic on every clean turn.
* **Downsides, Weaknesses & Problems That Will Arise:**
  1. **Slow Test Suites Kill Speed:** Running a 3-minute integration test suite inside a `Stop` hook destroys the speed advantage.
  2. **Non-Coding Truth Vacuum:** Strategy docs, legal redlines, and research syntheses have no compiler; weak classifier heads can rubber-stamp shallow prose.
  3. **Runaway Self-Healing Loops:** Auto-promoting failed tasks without a hard cap risks infinite retry spend if the user's original specification itself is contradictory.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Tiered Sub-Second Oracle (`< 800 ms` Budget):** Gate 4 runs incremental single-file checks only (`ruff`/`tsc --incremental`/AST parse for code; deterministic citation-existence, numeric-sum, and required-section checks for knowledge work), paired with one classifier pass checking prompt-directive coverage.
  * **Strict One-Strike Circuit Breaker:** Each task gets **at most 1 automatic promotion** (`Floor` $\rightarrow$ `Mid/Frontier`, discarding broken git hunks cleanly via shadow worktree/stash). If the promoted model also fails verification, the circuit breaker halts immediately and hands a crisp diagnostic report to the human.

---

### Step 8: Harness-Agnostic Multi-Agent Translation
* **What it does:** Allows the exact same `quadgate` daemon and Domain Cartridges to run identically on Antigravity, Claude Code, OpenAI Codex CLI, Cursor/Cline, and custom Python/TS swarms.
* **Downsides, Weaknesses & Problems That Will Arise:**
  * Every harness uses completely different JSON payloads, environment variables, hook names, and exit-code conventions (e.g., Claude Code uses exit code `2` + `stderr` to block a tool call, whereas Antigravity expects structured JSON on `stdout`).
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Canonical Gate Envelope (`GateEvent` $\leftrightarrow$ `GateVerdict`):** All logic inside the daemon speaks a single harness-neutral protocol. Thin 60-line **Harness Adapters** (`adapters/antigravity.py`, `adapters/claude_code.py`, `adapters/codex_cli.py`, `adapters/mcp_proxy.py`) translate harness-specific stdin/exit codes into `GateEvent` and back.

---

### Step 9: Open-Source Proof & Credibility Engine
* **What it does:** Proves to skeptical open-source engineers that QuadGate genuinely increases speed and efficiency while cutting cost—without cherry-picking or degrading quality.
* **Downsides, Weaknesses & Problems That Will Arise:**
  * Simulated spreadsheets do not convince senior engineers. Without reproducible A/B benchmarks measuring **cache hits, retry taxes, classifier latency, and blind quality pass rates**, the project will be dismissed as hype.
* **How We Overcome Them & Make It Even Better (v2 Design):**
  * **Built-In `quadgate bench` Harness with Cryptographic Receipts:** Ships in the repo from Day 1. Runs identical task suites (coding tasks with unit-test verification + knowledge tasks with blind rubric verification) in `baseline` vs. `quadgate` Docker/git-worktree sandboxes, capturing exact provider token headers (`input_tokens`, `cache_read_input_tokens`, `output_tokens`), wall-clock p50/p95 latency, classifier compute cost, and pass@1 quality—emitting signed JSONL receipts and reproducible Markdown scorecards.

---

## Part II: The Hardened v2 Agnostic Architecture

### 1. System Topology Diagram

```text
+-----------------------------------------------------------------------------------+
|                            AGENT HARNESS LAYER (AGNOSTIC)                         |
|  [ Antigravity ]      [ Claude Code ]      [ OpenAI Codex CLI ]    [ MCP Agents ] |
|  .agents/hooks.json   .claude/settings.json  codex-exec / proxy     mcp-gateway   |
+---------+--------------------+----------------------+--------------------+--------+
          |                    |                      |                    |
          +--------------------+-----------+----------+--------------------+
                                           |
                        Stdin/HTTP (< 2 ms | Fail-Open Timeout: 450 ms)
                                           v
+-----------------------------------------------------------------------------------+
|                     HARNESS ADAPTER & DETERMINISTIC MICRO-SHIM                    |
|  * Normalizes payloads into Canonical GateEvent                                   |
|  * Enforces hard 0-ms security blocks (.env, path escape, rm -rf) even offline    |
+------------------------------------------+----------------------------------------+
                                           | Local IPC (127.0.0.1:8787)
                                           v
+-----------------------------------------------------------------------------------+
|                        QUADGATE PERSISTENT KERNEL DAEMON                          |
|                                                                                   |
|  +-----------------------------+       +---------------------------------------+  |
|  | PASS-0 LOCAL ENGINE (<4 ms) |       | STATE & WARM-CACHE GUARD              |  |
|  | * Privacy Tier-1 Air-Gap    |       | * Turn-1 Session Lock (Protects KV)   |  |
|  | * Tree-Sitter AST Gists     |       | * Git Co-Change Adjacency Graph       |  |
|  | * SQLite FTS5 + Trigrams    |       | * 1-Strike Promotion Circuit Breaker  |  |
|  +--------------+--------------+       +-------------------+-------------------+  |
|                 |                                          |                      |
|                 +--------------------+---------------------+                      |
|                                      v                                            |
|  +-----------------------------------------------------------------------------+  |
|  |                    DOMAIN CARTRIDGE + 4-GATE ALGEBRA                        |  |
|  |  [Gate 1: Sieve]  [Gate 2: Firewall]  [Gate 3: ECC Governor]  [Gate 4: Exit]|  |
|  +-----------------------------------+-----------------------------------------+  |
|                                      |                                            |
|  +-----------------------------------v-----------------------------------------+  |
|  |           CONFORMAL CALIBRATION LAYER (Isotonic / Temp Scaling, ECE <= 0.04)|  |
|  +-----------------------------------+-----------------------------------------+  |
+--------------------------------------+--------------------------------------------+
                                       |
         +-----------------------------+-----------------------------+
         |                             |                             |
         v                             v                             v
+-----------------+         +---------------------+       +-----------------------+
| CLOUD PARALLEL  |         | OPEN-SOURCE SERVER  |       | LOCAL ZERO-COST       |
| * Decision 1    |         | * JEV Classifier    |       | * LAYAA (ONNX / GGUF) |
|   (1000 heads,  |         | * vLLM / TEI Cross- |       | * ModernBERT / BGE    |
|    ~215 ms)     |         |   Encoder Endpoints |       |   (< 55 ms CPU/GPU)   |
+-----------------+         +---------------------+       +-----------------------+
```

---

### 2. Canonical Gate Lifecycle & Equations

#### Gate 1: Turn-1 Intake & Context Sieve (`intake`)
* **Trigger:** `SessionStart` / `UserPromptSubmit` strictly when `turn_index == 1`.
* **Execution Flow:**
  1. **Air-Gap Check:** If prompt or workspace matches Tier-1 confidential patterns, set `cloud_classifier_allowed = False` and route strictly through local FTS5 / local ONNX.
  2. **Candidate Retrieval:** Query `tree-sitter` + FTS5 hybrid index for top $N$ file/record gists and `.agents/rules` (or `CLAUDE.md` modular slices).
  3. **Discriminative Pass:** Score pragmatic intent heads (`p_directive`, `p_counterfactual`) and candidate relevance heads (`p_rel_i`).
  4. **Actionability Formula:**
     $$A = p_{\text{directive}} \cdot (1 - p_{\text{counterfactual}})$$
     If $A < 0.30$, append `[QUADGATE_MODE: Read-only brainstorm detected; confirm before mutating files]`.
  5. **Co-Change Boost & Tail Hint Injection:** Boost candidate $j$ if co-committed $> 40\%$ of the time with top hit $i$:
     $$p^*_j = \min\left(1.0,\; p_{\text{cal}, j} + 0.12 \cdot w_{\text{cochange}}(i, j)\right)$$
     Append top 1 to 4 paths with $p^*_j \ge 0.80$ to the **end** of the Turn-1 user message so system prompt caches remain untouched.

#### Gate 2: Pre-Mutation Invariant Firewall (`firewall`)
* **Trigger:** `PreToolUse` matching write, patch, delete, shell execution, or outbound webhook/API mutation tools.
* **Execution Flow:**
  1. **Stage 2A (Deterministic $0\text{ ms}$):** Verify path stays inside workspace cage, block forbidden secret patterns, and check destructive command denylist.
  2. **Stage 2B (Semantic Classifier):** Extract enclosing AST symbol / document section around the proposed diff and score against active Cartridge Invariant Heads ($I_1 \dots I_M$).
  3. **Verdict Logic:**
     * $\max(I_m) \ge 0.78$: **Block** (first strike returns exact rule remediation; second consecutive strike on same rule triggers explicit human escalation instead of an infinite LLM retry loop).
     * $\max(I_m) < 0.78$: **Allow**.

#### Gate 3: Cold-Boundary Subagent ECC Governor (`subagent_governor`)
* **Trigger:** `PreToolUse` on `invoke_subagent` / `Task` tool (Claude Code) / cold worker spawn via localhost LLM proxy (`127.0.0.1:8788`).
* **Execution Flow:**
  1. Compute structural blast radius $B \in [0, 1]$ from `tree-sitter` import fan-in of referenced files plus historical file promotion rate $R_{\text{hist}}$.
  2. Classifier predicts calibrated first-pass success $P_{\text{pass}}(M_k)$ for `floor`, `mid`, and `frontier` tiers.
  3. **Expected Cost of Completion Selection:** Choose the lowest-cost tier $M_k \in \{\text{floor}, \text{mid}, \text{frontier}\}$ satisfying:
     $$\max(I_m) < 0.50 \quad \land \quad P_{\text{pass}}(M_k) \cdot (1 - 0.5 B) \cdot (1 - R_{\text{hist}}) \ge \tau_k$$
     where calibrated defaults are $\tau_{\text{floor}} = 0.82$ and $\tau_{\text{mid}} = 0.60$.

#### Gate 4: Epistemic Exit & Self-Healing Loop (`stop_audit`)
* **Trigger:** `Stop` / `SubagentStop` event.
* **Execution Flow:**
  1. **Sub-Second Domain Truth Oracle:** Run cartridge verifier on touched artifacts only (e.g., `ruff check`, `tsc --noEmit`, JSON/SQL schema check, or markdown citation/numeric sum check).
  2. **Directive Coverage Pass:** Classifier scores every atomic requirement extracted at Turn 1 against the resulting git diff / output artifact.
  3. **Self-Healing Verdict:**
     * If Oracle fails **and** current worker was downgraded by Gate 3 (`strike_count == 0`): Revert dirty hunk, increment `strike_count = 1`, record file promotion telemetry, and **auto-promote** task to the next model tier.
     * If Oracle passes **and** `p_completeness >= 0.85` with `max(I_m) < 0.40`: **Allow immediate close** (skipping expensive generative review).
     * If `0.60 <= p_completeness < 0.85`: Return targeted list of unaddressed prompt clauses so the active warm-cached model finishes them before stopping.

---

### 3. Model-Agnostic `ClassifierDriver` Specification

QuadGate decouples gate math from any specific model vendor. Any backend implementing these three methods plugs straight into the kernel:

| Driver Name | Execution Mode | Typical Latency | Best Use Case |
| :--- | :--- | :--- | :--- |
| `decision1` | Cloud HTTP/2 Parallel Heads | $180\text{--}230\text{ ms}$ | Maximum head capacity ($1,000$ heads), zero local GPU/RAM |
| `jev` | Cloud or Self-Hosted vLLM/HTTP | $90\text{--}190\text{ ms}$ | High-throughput enterprise & self-hosted GPU clusters |
| `layaa` | Local ONNX / GGUF / PyTorch | $25\text{--}75\text{ ms}$ | 100% offline open-source execution, `$0.00` marginal API cost |
| `modernbert_onnx` | Local Quantized INT8 ONNX | $18\text{--}55\text{ ms}$ | Default out-of-the-box CPU classifier (`pip install quadgate`) |
| `logprob_api` | Single-Token `max_tokens=1` API | $220\text{--}380\text{ ms}$ | Zero-install fallback using existing OpenAI/Gemini/Anthropic key |

**Why Shipping `modernbert_onnx` / `layaa` Locally Matters for GitHub Adoption:** Anyone who runs `pip install quadgate && quadgate init` gets immediate offline speed and cost savings in 60 seconds with **zero new API signups**, and can plug in `decision1` or `jev` via a single config line for massive 1,000-head scale.

---

### 4. Harness-Agnostic Compatibility Matrix

| Agent Harness | Integration Mechanism | Gate 1 (Sieve) | Gate 2 (Firewall) | Gate 3 (Subagent Tier) | Gate 4 (Exit Audit) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Antigravity** | `.agents/hooks.json` | `PreInvocation` | `PreToolUse` | `PreToolUse` (`invoke_subagent`) | `Stop` |
| **Claude Code** | `.claude/settings.json` | `UserPromptSubmit` | `PreToolUse` (exit `2`) | `PreToolUse` (`Task`) + Proxy | `Stop` / `SubagentStop` |
| **OpenAI Codex CLI** | Wrapper + `OPENAI_BASE_URL` Proxy | Turn-1 Request Hook | Exec Sandbox Hook | Cold-Request Model Router | Post-Turn Diff Check |
| **Cursor / Cline / MCP** | `quadgate-mcp` + API Proxy | MCP Resource Hint | MCP Tool Wrapper | Proxy Sub-Call Router | Task Completion Tool |

---

### 5. Domain Cartridge Architecture (Beyond Coding)

To apply QuadGate to Legal, Finance, Support, Research, or Strategy without changing kernel code, users select or author a declarative YAML cartridge (`cartridges/*.yaml`):

* `cartridges/software_engineering.yaml` — `tree-sitter` gists, AST/shell firewall, `pytest`/`tsc`/`ruff` truth oracle.
* `cartridges/knowledge_vault.yaml` — Markdown section gists, Tier-1 PII air-gap, citation & broken Wiki-Link truth oracle.
* `cartridges/financial_ops.yaml` — Ledger/CSV schema gists, payout threshold firewall, deterministic double-entry sum oracle.
* `cartridges/legal_redline.yaml` — Clause-boundary gists, statutory/playbook liability heads, mandatory human escalation on $0.60\text{--}0.84$ ambiguity band.

---

### 6. Target Open-Source Repository Layout (`github.com/<your-org>/quadgate`)

```text
quadgate/
├── README.md                        # Value proposition, quickstart, live A/B proof badges
├── LICENSE                          # Apache-2.0
├── pyproject.toml                   # Python 3.11+ package (CLI: `quadgate` & `qg-shim`)
├── ARCHITECTURE.md                  # This specification
├── quadgate/
│   ├── cli.py                       # `init`, `daemon`, `calibrate`, `status`, `bench`, `doctor`
│   ├── shim.py                      # Zero-dep <450ms fail-open/fail-closed hook client
│   ├── kernel/
│   │   ├── daemon.py                # Async localhost HTTP/IPC server (127.0.0.1:8787)
│   │   ├── proxy.py                 # Optional LLM API cold-session router (127.0.0.1:8788)
│   │   ├── gates.py                 # Gate 1-4 algebra, ECC equation, warm-cache guard
│   │   ├── healing.py               # 1-strike promotion circuit breaker & telemetry logger
│   │   └── calibration.py           # Isotonic / temperature scaling & ECE verifier
│   ├── index/
│   │   ├── sqlite_fts.py            # SQLite FTS5 + trigram Pass-0 store (<4ms)
│   │   ├── ast_gists.py             # Zero-token tree-sitter gist & import fan-in builder
│   │   └── cochange.py              # Git log co-change adjacency matrix
│   ├── drivers/
│   │   ├── base.py                  # Abstract `ClassifierDriver` protocol
│   │   ├── decision1.py             # Decision 1 1000-head HTTP/2 driver
│   │   ├── jev.py                   # JEV classifier driver
│   │   ├── layaa.py                 # LAYAA open-source classifier driver
│   │   ├── onnx_local.py            # Quantized local ONNX cross-encoder driver
│   │   └── logprob_fallback.py      # Zero-output LLM logprob fallback driver
│   ├── adapters/
│   │   ├── antigravity.py           # Antigravity `.agents/hooks.json` translator
│   │   ├── claude_code.py           # Claude Code `.claude/settings.json` translator
│   │   ├── codex_cli.py             # OpenAI Codex CLI wrapper + proxy translator
│   │   └── mcp_bridge.py            # Generic MCP tool & context gateway
│   └── cartridges/
│       ├── software_engineering.yaml
│       ├── knowledge_vault.yaml
│       ├── financial_ops.yaml
│       └── legal_redline.yaml
├── benchmarks/
│   ├── runner.py                    # Reproducible A/B sandbox runner (baseline vs quadgate)
│   ├── calibration_gold.jsonl       # 64-pair golden calibration dataset
│   ├── suites/
│   │   ├── coding_tasks.jsonl       # 50 verifiable repo bugfix/feature/refactor tasks
│   │   └── knowledge_tasks.jsonl    # 30 verifiable synthesis/audit tasks with truth checks
│   └── reports/                     # Signed JSONL receipts & auto-generated proof charts
└── tests/
    ├── test_cache_preservation.py   # Proves 0% KV prefix-cache invalidation on turns 2..N
    ├── test_fail_open_resilience.py # Proves daemon crash never freezes IDE, still blocks .env
    ├── test_calibration_ece.py      # Proves cross-model score parity (Decision1 vs LAYAA vs JEV)
    └── test_one_strike_healing.py   # Proves bad cheap drafts revert & promote cleanly
```