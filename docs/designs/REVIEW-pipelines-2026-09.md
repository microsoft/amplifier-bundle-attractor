# Review: the issue → attractor → PR pipelines against current doctrine

**Date:** 2026-09-06 · **Scope:** `.github/capsule-pipeline/*.dot` ·
**Status:** review; no `.dot` file is modified by this PR. After the
compat-window deletion (`HISTORY-MAP.md`) this repo is the **pattern layer** —
it authors graphs that run on `amplifier-bundle-dot-runner`'s engine. This review
does *not* re-review the engine; it reviews THIS repo's graphs, citing dot-runner
(`@e5c5274`) where the binding clause or implementing line lives there.

## 1. Census

Graphs this repo executes in CI, and the workflow that drives each:

| `.dot` | driven by | `--worker` | `max_iterations` | `max_duration` |
|---|---|---|---|---|
| `.github/capsule-pipeline/capsule.dot` | `capsule-specify.yml:509` | `coding-agent` | 6 (`:517`) | `19800s` (`:98`) |
| `.github/capsule-pipeline/feature-capsule.dot` | `feature-specify.yml:691` | `coding-agent` | 8 (`:702`) | `12000s` (`:113`) |
| `.github/capsule-pipeline/task-runner.dot` | `capsule-implement.yml:569` | `coding-agent` | 6 (`:575`) | `14400s` (`:142`) |
`.github/actions-key-smoke/actions-key-smoke.dot` (50 lines) is a credential
smoke test, not a pipeline. `ci.yml` executes no graph — `dot-render-gate`
(`ci.yml:69-143`) only proves every tracked `.dot` renders. No `examples/` graph
runs in CI.

All three share one shape: `default_max_retries=2`, `default_fidelity="compact"`,
`max_pipeline_duration="$max_duration"`, `retry_target=author|attempt`
(`capsule.dot:112-119`, `feature-capsule.dot:330-337`, `task-runner.dot:176-183`).
**No node in any of the three declares `worker=`, and none declares
`reasoning_effort=`.** One node per graph pins a provider — `critique` /
`critique_b`, `llm_provider="openai", llm_model="gpt-[5-9]*"` (`capsule.dot:582`,
`feature-capsule.dot:655-656`, `task-runner.dot:298`).

### Measured evidence

Two read-only runs of `capsule.dot` on issue #64:

| | run 1 | run 2 |
|---|---|---|
| wall clock | **19,800.0 s** (5h30m) | **11,304.6 s** (3h08m) |
| terminated by | `max_pipeline_duration_exceeded` mid-`critique` (`run1/iteration_1/critique/status.json`) | own `triage: exhausted` → `postmortem` → `abandon` |
| rounds completed | 1.9 | ~1.5 |
| session-level observability | none | 12 `sessions/*/events.jsonl` |

Run 2 aggregates (`events.jsonl`, `timing-rollup.json`): **98.4 % of the run is
provider latency** — 11,127.6 s LLM vs **160.2 s tool time (1.4 %)** across 629
provider calls and 680 tool calls. Total cost **$161.80**.

## 2. Node table — `capsule.dot` (the measured graph)

Nine LLM nodes, thirteen deterministic. "needs tools?" is read off the prompt.

| node | shape/class | worker (effective) | needs tools? | provider/model | fidelity/thread | run-2 cost | recommendation |
|---|---|---|---|---|---|---|---|
| `orient` `:194` | box / maker | spawn → coding-agent | **yes** — "Survey the pinned tree" | anthropic default (sonnet) | compact / — | 171 s, 23 calls, $3.14 | keep worker; **add `reasoning_effort="low"`** — it summarizes a read, it does not decide |
| `rival` `:225` | box / maker | spawn | **yes** — reads tree, writes `rival.patch` | anthropic default | compact / `rival` | 691 s, 63 calls, $19.21 | keep. 11.6 %→6.1 % of run across the two runs; the RC-6 evidence it buys is load-bearing |
| `author` `:276` | box / maker | spawn | **yes** — writes + self-tests the capsule pair | anthropic default | **full** / `work` | **4,850 s, 256 calls, $101** | keep worker; **add `timeout=1500, allow_partial=true`**. 42.9 % of the run and rising — the only node that must be bounded |
| `mutate` `:374` | box / maker | spawn | **yes** — patch grounded in real file contents | anthropic default | compact / `mutate` | 1,779 s, 123 calls, $12.81 | keep. Note: round 2 burned 3 sessions on a `must_write` mtime miss and still failed |
| `mutate_b` `:381` | box / maker | spawn | **yes** | anthropic default | compact / `mutate_b` | 1,070 s, 56 calls, $8.24 | **add `reasoning_effort="medium"`**; consider merging into `mutate`'s thread (§5) |
| `void` `:388` | box / maker | spawn | **yes** | anthropic default | compact / `void` | 1,048 s, 35 calls, $5.28 | **add `reasoning_effort="medium"`**. Slowest per call (29.9 s) at the fewest calls — it is already thinking, unbudgeted |
| `critique` `:582` | box / **gate** | spawn | **yes** — reads capsule, three patches, rival, tree | **openai `gpt-[5-9]*`** | compact / — | 1,213 s, 51 calls, $8.24 | **add `reasoning_effort="high"`**. The one judgment node; highest quality-per-dollar lever in the graph |
| `diagnose` `:660` | box / gate | spawn | **yes** — reads engine logs *outside* `target_dir` | anthropic default | compact / — | not reached | **add `reasoning_effort="low"`** |
| `postmortem` `:717` | box / gate | spawn | **yes** — reads 6 artifact paths | anthropic default | compact / — | 477 s, 22 calls, $3.88 | **add `reasoning_effort="low"`**. 18 of its 35 tool calls are `read_file` |
| 13 deterministic nodes | parallelogram/hexagon | `tool_command=` | n/a | n/a | n/a | **5.8 s total (0.05 %)** | keep. The gate lattice costs nothing |

`feature-capsule.dot` differs only in scale — same nine LLM node ids, longer
prompts (`void` 6,899 chars vs 2,876), plus `criteria_gate` and four extra
refusal terminals, all deterministic. `task-runner.dot` adds `critique_b` (the
openai-pinned twin), `feedback`, `package`. **Every recommendation below applies
to all three**; §6 diffs `capsule.dot` and states the sibling equivalent.

**No LLM node in any graph is reasoning-only.** Every prompt opens by naming
files to read (`$issue_file`, `.ai/brief.md`, `.ai/gate.log`, the tree at
`$base_sha`); measured floor is `orient` at 32 tool calls. This decides §3.

## 3. Owner question 1 — worker per node

### 3.1 The question contains a false premise, and it matters

`coding-agent` → `direct` is not a per-node move. The engine's node-level
`worker=` vocabulary is **exactly two names** — `known_workers = frozenset({_SPAWN_WORKER_SENTINEL}) | self._registry.names()`
(`dot-runner .../backend.py:213`), where `_SPAWN_WORKER_SENTINEL = "spawn"`
(`:93`) and the registry holds one entry, `"llm-direct"` (`:212`). A node writing
`worker="amplifier-agent"` raises at `backend.py:339-343`. `coding-agent` /
`amplifier-agent` are **run-level** names (`default_worker.py:135-136`) selecting
which adapter the *spawned child* runs — never a node attribute.

This repo's `docs/DOT-AUTHORING-GUIDE.md:1121-1146` states this correctly.
dot-runner's `default_worker.py:130-134` docstring does not — it calls the two
adapter names "the whole user-facing vocabulary (`--worker <name>` / node
`worker=`)". **That comment is wrong; file it against dot-runner.** It is the
likely source of the premise.

### 3.2 Which nodes should move to `llm-direct`: **none, today**

Not because they reason over text — because of how they receive input. On the
CLI path the backend is built with `tools=config.get("tools", [])`
(`pipeline-runner/runner.py:576`), i.e. **empty**. `llm-direct` is a bare
in-process tool loop over whatever tools it was handed; in Actions that is zero.
Every LLM node here receives its inputs *only* through tool-mediated reads of
`$target_dir` and `.ai/`. No attribute inlines a file into a prompt — `$name`
substitution is by value (`substitution.py`, C14.1) and `fidelity` preambles
carry only prior-node summaries.

Moving a node to `direct` is therefore a **graph redesign** (pre-materialize its
inputs via a `tool_command=` glue node), not an attribute flip. `postmortem` is
the only plausible candidate — 22 calls over six known paths — and even it needs
a glue node first; not recommended, it trades 3.9 % of run cost for a new failure
class. Second constraint: `llm-direct` makes `llm_model` **mandatory**
(`workers/direct_worker.py` docstring row 12).

### 3.3 Does `loop-amplifier-agent` already get bounded context? **Partially — and it is not the path these runs used.**

Answered from code. Adapter
`dot-runner modules/loop-amplifier-agent/amplifier_module_loop_amplifier_agent/__init__.py`,
**`mount()` at `:114-118`**. It runs no tools itself — it boots a *separate*
`AmplifierSession` from amplifier-agent's own mount plan (`:450` →
`amplifier_agent_lib.bundle.cache.load_and_prepare_cached`; `:562-576` →
`amplifier_foundation/bundle/_prepared.py:551`), i.e. the bundle
`amplifier_agent_lib/bundle/bundle.md` (`amplifier-agent-anchors`). Plainly:

- **Compaction: YES.** `bundle.md:67-73` mounts `context-simple`,
  `max_tokens: 300000, compact_threshold: 0.8` — ephemeral per-request compaction
  at ~240 K tokens (`amplifier_module_context_simple/__init__.py:343-388`). Its
  `auto_compact: true` key is inert — zero readers in that module.
- **Per-tool-result truncation: NO.** `bundle.md:172-241` declares seven hooks,
  none truncating. dot-runner's `hooks-tool-truncation` (`default_worker.py:349-358`)
  registers on the **adapter's** coordinator (`hooks-tool-truncation/__init__.py:313-325`),
  not the hosted session's.
- **Retention window: NO.** `tool_result_retention_turns: int = 20` exists only in
  `loop-agent/config.py:65`; §45's own "implementation locations" names
  `modules/loop-agent/*` only.

**What this changes about the running loop-agent fix lane — the honest read:**
it does not make that lane worthless, it moves the target. All three workflows
run `--worker coding-agent` = `loop-agent`, the adapter that **already has both**
the retention window and `hooks-tool-truncation`; run 2's 12 `orchestrator:complete`
events all report `loop-agent`. So the 227,605-token author round happened **on
the most-bounded path available**, and §45's own measurement says why per-result
truncation could not have helped: not one of the 166 results exceeded its limit
(largest 17,112 chars vs bash's 30,000 default). The leak is *accumulation*.

Meanwhile the **default** worker absent `--worker` is `amplifier-agent`
(`default_worker.py:685-705`) — the *less*-bounded one. Two consequences: (a) the
fix lane's value is the retention window, not truncation; (b) a run without an
explicit `--worker` gets compaction-at-240 K only, and author round 2 peaked at
227,605 — **it would have finished without compaction ever firing**.

## 4. Owner question 2 — model / provider / reasoning per node

### 4.1 Current state

`critique` / `critique_b` pin `llm_provider="openai", llm_model="gpt-[5-9]*"`; the
glob is real (`backend.py:1238-1246` → `unified_llm/resolver.py:19,82-83`,
`fnmatch`). Every other LLM node sets neither, taking the implicit default
`"anthropic"` (`backend.py:1074-1092`) resolved via
`_PROVIDER_DEFAULT_MODEL_PATTERN["anthropic"] = ("sonnet", True)`
(`backend.py:1226-1230`) — **latest stable Sonnet**, an implicit pin the graph
never states.

### 4.2 The observability blocker, stated before any model recommendation

**`model` is `null` on 629 of 629 `provider:request` events in run 2.** Cost is
attributed (`cost_usd` on every response); the model is not. Every per-node model
recommendation is therefore **unfalsifiable today** — we can show what a node
cost, not what it ran on. First model-related change should be a dot-runner ask:
emit the resolved model on the provider-request event (the engine already
computes it, `MODEL_RESOLVED` at `backend.py:1325-1334`).

### 4.3 Recommendations

| node | current | recommended | why |
|---|---|---|---|
| `critique` / `critique_b` | openai `gpt-[5-9]*`, no effort | **+ `reasoning_effort="high"`** | The one judgment node. openai passes effort straight through (`adapters/openai.py:446-447`). $8.24 and 10.7 % of the run — the cheapest place to buy judgment |
| `void`, `mutate_b` | anthropic default | **+ `reasoning_effort="medium"`** | Both must find a *mechanically different* construction. Anthropic maps `medium`→8000 thinking tokens (`adapters/anthropic.py:46-58`). `void` already burns 29.9 s/call on 35 calls — it is thinking without a budget |
| `orient`, `diagnose`, `postmortem` | anthropic default | **+ `reasoning_effort="low"`** | Read-and-report over already-collected facts. `low`→1024 thinking tokens |
| `author` | anthropic default, `fidelity="full"` | **no effort attribute** (leave `None`) | 256 calls, 42.9 % of the run. Multiplying thinking tokens across the dominant node is the one change that could make run 2 look like run 1 |
| `rival`, `mutate` | anthropic default | unchanged | No evidence either way; do not tune what is not measured |

**Unknown-value trap:** an anthropic `reasoning_effort` outside `{low, medium,
high}` silently becomes 8000 (`adapters/anthropic.py:495-503`); the engine
validates nothing (C11, `engine-surface.v1.md:150-152`).

### 4.4 The runner constraint — stated because it constrains the answer

`terra` / `luna` exist only in the owner's host settings; GitHub-hosted Actions
has `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and nothing else
(`preflight.py:55-59`). **Every recommendation above uses only those two.**

Any future recommendation naming `terra`/`luna` must ship its infra change with
it: a self-hosted runner carrying those settings, or new repo secrets **plus** a
credential-name entry in `preflight.py`'s map. Shipping the pin alone is not a
degraded run — the startup preflight (`preflight.py:212-226`) **refuses to start
the pipeline**, naming every failing node, before node 1. That is why this pin
cannot be smuggled in "to see if it works".

## 5. Owner question 3 — budget honesty

### 5.1 The declared budget and the fuse disagree by 3×

Run 2, iteration 0, from `trace.jsonl` (seconds): preamble
(`start+setup+orient+rival`+resets) = **862**; one round
(`author+mutate+mutate_b+void+critique`+gates) = **5,594** (93.2 min);
iteration-0 total 6,456. The fuse is 19,800 s. Solving `862 + N × 5,594 ≤ 19,800` gives **N ≈ 3.4** — optimistic, because a
round is not constant: `author` went 1,242 s → 3,608 s between rounds as context
grew 24 K → 228 K input tokens. Measured: **run 1 got 1.9 rounds, run 2 ~1.5.**

The graph declares `max_iterations=6` and `setup` *raises* it to 8 for a test-less
subject, capped at 15 (`capsule.dot:180`). **The wall clock buys 2.** A 6-to-15
budget against a fuse permitting 2 is not a budget but a number the run can never
reach — and it is why both runs died at a wall rather than at a decision.

### 5.2 Where a round's time actually goes

Round 1, run 2: `author` 22.2 %, `critique` 21.7 %, `mutate_b` 19.1 %, `void`
18.7 %, `mutate` 18.1 %. **The three non-vacuity probes are 56 % of a round.**
Tool time is 1.4 % of the run — nothing targeting tools can matter.

### 5.3 The single largest number, which is not a graph problem

`cache_read_input_tokens` is a **flat 10,995 on all 629 calls**; `cache_write` is
non-null on 6. The prefix is cached, the growing conversation is not. Hit rate
decays from ~87 % on call 1 to **4.8 % on author's last call**; whole-run 12.4 %.
Author round 2 alone re-sent 24.7 M input tokens for $74.10 — 45.8 % of run cost.
**No `.dot` edit beats making incremental caching work in the engine.** Stated
first so §5.4 is not mistaken for the fix.

### 5.4 What the v2 graph should set

Given the 6h GitHub-hosted job cap that sizes the fuse:

1. **Bound the dominant node:** `author [timeout=1500, allow_partial=true]`. A
   bare integer `timeout=` is seconds (`dot_parser.py:52,646-650`); on expiry with
   `allow_partial` the outcome is `PARTIAL_SUCCESS` (`engine.py:992-997`), which
   `is_success` (`engine.py:1663`) and so traverses the plain
   `author -> author_reset` edge. A short capsule then meets `capsule_gate`, which
   routes a shape defect to `triage` — recovery the graph already has. The fuse
   still wins ties (C6.2, `engine-surface.v1.md:98-100`).
2. **Bound the probes and the judge:** `timeout=900` on `mutate`, `mutate_b`,
   `void`, `critique`. Round ceiling = 1,500 + 4×900 = **5,100 s**.
3. **Declare the budget the fuse buys:** `max_iterations=3`
   (862 + 3×5,100 = 16,162 s < 19,800 s ✓); cap the test-less bump at 4, not 15.

If §5.3 is fixed first the same fuse buys 6 rounds and `max_iterations=6` becomes
honest again. **Choose one; do not ship both a 6-round claim and a 2-round wall.**

## 6. Proposed v2 — unified diff (NOT applied)

Review-only. Apply after the owner rules on §5.4's either/or. Prompt bodies
elided as `...` — no prompt text changes.

```diff
--- a/.github/capsule-pipeline/capsule.dot
+++ b/.github/capsule-pipeline/capsule.dot
@@ -180 +180 @@ setup
-        tool_command="... B=${B:-6}; if [ \"$NT\" = true ]; then B=$((B+2)); [ \"$B\" -gt 15 ] && B=15; fi; ..."]
+        tool_command="... B=${B:-3}; if [ \"$NT\" = true ]; then B=$((B+1)); [ \"$B\" -gt 4 ] && B=4; fi; ..."]
@@ -194 +194 @@ orient
-    orient [shape=box, class="maker", must_write=".ai/brief.md",
+    orient [shape=box, class="maker", must_write=".ai/brief.md", reasoning_effort="low",
@@ -276,2 +276,4 @@ author
-    author [shape=box, class="maker", thread_id="work", fidelity="full",
-        must_write=".ai/capsule/DEFINITION.verify.sh",
+    // BUDGET (review 2026-09): 42.9% of run 2, 256 calls, 24.7M input tokens.
+    author [shape=box, class="maker", thread_id="work", fidelity="full",
+        timeout=1500, allow_partial=true,
+        must_write=".ai/capsule/DEFINITION.verify.sh",
@@ -374,10 +376,14 @@ mutate / mutate_b / void
-    mutate [shape=box, class="maker", thread_id="mutate",
+    // thread_id is INERT at compact fidelity (backend.py:470-474) -- intent,
+    // not behavior. See review 2026-09 section 7.
+    mutate [shape=box, class="maker", thread_id="mutate", timeout=900,
         must_write=".ai/hypothesis.patch",
-    mutate_b [shape=box, class="maker", thread_id="mutate_b",
+    mutate_b [shape=box, class="maker", thread_id="mutate_b",
+        timeout=900, reasoning_effort="medium",
         must_write=".ai/hypothesis_b.patch",
-    void [shape=box, class="maker", thread_id="void",
+    void [shape=box, class="maker", thread_id="void",
+        timeout=900, reasoning_effort="medium",
         must_write=".ai/hypothesis_v.patch",
@@ -582 +588,2 @@ critique
     critique [shape=box, class="gate", llm_provider="openai", llm_model="gpt-[5-9]*",
+        timeout=900, reasoning_effort="high",
         must_write=".ai/critique.md",
@@ -660 +667 @@ diagnose
-    diagnose [shape=box, class="gate", must_write=".ai/diagnose-verdict",
+    diagnose [shape=box, class="gate", must_write=".ai/diagnose-verdict", reasoning_effort="low",
@@ -717 +724 @@ postmortem
-    postmortem [shape=box, class="gate", must_write=".ai/postmortem/report.md",
+    postmortem [shape=box, class="gate", must_write=".ai/postmortem/report.md", reasoning_effort="low",
--- a/.github/workflows/capsule-specify.yml
+++ b/.github/workflows/capsule-specify.yml
@@ -517 +517 @@
-            --param max_iterations=6 \
+            --param max_iterations=3 \
```

Sibling edits are mechanically identical: `feature-capsule.dot` (same nine node
ids) with `feature-specify.yml:702` `8 → 3`; `task-runner.dot` (`attempt` takes
`author`'s treatment; `critique` **and** `critique_b` take `timeout=900` +
`reasoning_effort="high"`) with `capsule-implement.yml:575` `6 → 3`.

## 7. Doctrine check

- **`docs/VISION.md:48-70` (decision matrix).** `reasoning_effort`, `timeout`,
  `allow_partial` are spec-given properties the engine already implements; adding
  them is *more-aligned* movement — presumption of yes, nothing uncharted-tier.
- **Gates before work / one correction loop.** Both honored, and measurement
  shows the gate lattice is nearly free: 13 deterministic nodes, **5.8 s of
  11,305 s (0.05 %)**. `triage` is the single wall.
- **`fidelity` vocabulary.** The engine's set is exactly six — `full, truncate,
  compact, summary:low, summary:medium, summary:high` (`fidelity.py:44-53`),
  default `compact` (`:55`), precedence edge > node > graph > default (`:86-133`);
  an invalid value **warns and degrades to `compact`** rather than failing
  (`:92-101`). The graphs use `compact` and `full` (`author`/`attempt` only) — a
  correct, conservative subset. **But `thread_id` is read only when fidelity
  resolves to `full`** (`backend.py:470-474`), so the `rival` / `mutate` /
  `mutate_b` / `void` `thread_id=` attributes are **inert today**. They document
  intent, not behavior. v2 says so in a comment, so nobody "fixes" a non-bug.
- **Authoring layer — the answer to "do the skills teach it?"** Fidelity: yes.
  `docs/DOT-AUTHORING-GUIDE.md:1064-1146` teaches all six values and the correct
  two-name worker vocabulary; `skills/attractorify/SKILL.md:359` requires "one of
  the six real `fidelity=` values". Worker choice: yes as *vocabulary*, no as
  *decision*. **The gap is cost** — neither teaches worker/model/effort as a
  budget decision, and neither carries a per-node-cost worked example. §2's table
  is the missing artifact.
- **`specs/EXTENSIONS.md` here stops at §39** (2,664 lines, 41 sections). The
  shipped graphs rely on dot-runner's §40 (worker registry), §43 (`$name` at
  parse time — exactly `max_pipeline_duration="$max_duration"`), §45 (bounded
  per-call context). `specs/README-DISPOSITION.md` records why the copy stays but
  not that it is now **behind** the engine surface those graphs depend on.
  Recommend a banner naming dot-runner's ledger normative for §40+.
- **Contract clauses used** (dot-runner `contracts/engine-surface.v1.md`, still
  **DRAFT/unstamped**, `:3-8`): C2 `status.json` (`:47-58`), C4 `$name` (`:71-80`),
  C5/C7 pinning + preflight (`:82-114`), C6 fuse (`:95-103`), C10 thread/fidelity
  (`:137-145`), C11 `reasoning_effort` (`:147-154`). The graphs hand-roll nothing
  the contract covers.

## 8. Two defects found in passing

1. **`escalate` is dead in Actions.** Run 2 `iteration_1/escalate/status.json`:
   `HumanGateHandler requires an Interviewer but none was provided`. The
   `[A]/[C]/[K]` gate cannot fire in CI; the run fell straight to `abandon`.
   Every `-> escalate` edge in all three graphs is a trapdoor, not a gate.
2. **`mutate` round 2 burned 763 s over three sessions** on `must_write` —
   `mtime 1788737217.642 <= node_start 1788744193.583; planted before or at node
   start`. The artifact existed but predated the visit: either the maker writes
   identical content (no-op) or the reset does not clear `.ai/hypothesis.patch`
   between rounds.

Both are out of scope here; neither is fixed by the §6 diff.
