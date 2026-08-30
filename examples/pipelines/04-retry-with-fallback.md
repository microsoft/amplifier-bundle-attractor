# 04 - Retry with Fallback Pipeline

> **Engine-feature demo + renegotiation exemplar** — this guide teaches two
> things in one file: the engine's retry ladder (`max_retries`, `retry_target`,
> `fallback_retry_target`, fail-edges) and **explicit criteria renegotiation**:
> what to do when the graph cannot meet its original goal within budget.
>
> The retry loop is evidence-gated: a parallelogram tool node (`validate_gate`)
> runs a fixed RFC 5322 case set mechanically, tracks how many times it has
> been entered, and routes on `context.tool.last_line` — the same idiom as
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md). When the gate
> has run its budget without the hard criteria passing, it emits
> `budget_exhausted` and the drawn edge `validate_gate -> renegotiate` fires —
> routing through an explicit `renegotiate` decision point that **records the
> downgrade durably**. A second parallelogram tool node (`check_renegotiation`)
> then **enforces** that the disclosure artifact (`renegotiation.md`) exists and
> contains all required sections with non-empty content before the relaxed path can proceed. The
> relaxed path is structurally blocked until the record is verified — making
> the disclosure a pipeline-enforced requirement, not an LLM promise. A
> `SUCCESS` that is indistinguishable from full-criteria success is a failure
> of the renegotiation pattern.
>
> Two drawn trigger edges lead to `renegotiate`: the budget-exhaustion path from
> `validate_gate` (the primary teaching — cannot meet criteria A within budget)
> and the implementation-failure path from `implement` (handler failure, API
> error). Both are visible in the topology; a reader of the `.dot` alone can
> see WHERE renegotiation happens and WHY.

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/04-retry-with-fallback.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
dot-runner run "$DOT" --worker coding-agent --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

### Demo: force the renegotiation path

A capable model may pass the RFC 5322 case set before the budget runs out, so
the relaxed path won't fire on every natural run. To see the renegotiation
choreography deterministically, pre-seed the budget counter in the run
directory before launching — the first `validate_gate` entry then lands past
budget (count 3 → 4 > budget 3) and the `budget_exhausted` edge fires:

```bash
DOT="$PWD/examples/pipelines/04-retry-with-fallback.dot"
mkdir -p /tmp/attractor-demo-renegotiate && cd /tmp/attractor-demo-renegotiate
echo 3 > validate_count.txt
dot-runner run "$DOT" --worker coding-agent --cwd .
```

Watch for: `validate_gate -> renegotiate` in the routing, `renegotiation.md`
written and verified (`check_renegotiation_output.txt`), then
`simple_implement -> validate_relaxed -> done` on the relaxed case set. The
run directory ends up holding the full renegotiation record — a SUCCESS you
can tell apart from full-criteria success by inspection.

Captured evidence from one live run of each path (hard-path full success and
forced relaxed-path renegotiation, verbatim run artifacts plus trace) is
committed at
[`practical/evidence/retry-fallback-renegotiation-2026-08-03/`](practical/evidence/retry-fallback-renegotiation-2026-08-03/manifest.json).

## What This Exercises

### Retry-ladder attributes (the engine mechanism)
- **`max_retries` attribute**: `implement` has `max_retries=2` meaning up to 3 total executions (1 initial + 2 retries)
- **`retry_target`** (node-level): When `implement` exhausts retries, jump back to `plan` for a fresh approach (spec §3.7 step 2)
- **`fallback_retry_target`** (node-level on `implement`): If no drawn edge matches a FAIL outcome, the engine would consult `retry_target` then `fallback_retry_target` as a belt-and-suspenders chain (spec §3.7 steps 2-3). The drawn edge `implement -> renegotiate` **is** the engine mechanism in this graph — it is selected at spec §3.3 Step 1 on any FAIL outcome, before the engine reaches `_resolve_failure_retry_target`. The attributes are present to demonstrate the retry-ladder API; they are not the active routing mechanism here.
- **Fail-edge**: `validate_gate -> implement [condition="context.tool.last_line=gate_fail"]` — explicit per-node failure routing
- **Graph-level `default_max_retries`**: Sets the global retry ceiling to 3
- **`goal_gate` + retry interaction**: Both `implement` and `simple_implement` are goal gates — the pipeline cannot exit until at least one succeeds
- **`allow_partial`**: `simple_implement` accepts PARTIAL_SUCCESS when retries exhaust, treating it as good enough for the relaxed criteria

### Explicit criteria renegotiation (the pattern)
- **`renegotiate` node**: the visible decision point where criteria are relaxed. A reader of the drawn graph can see WHERE renegotiation happens. It has two drawn trigger edges — one for budget exhaustion (from `validate_gate`), one for implementation failure (from `implement`) — plus the `record_missing` retry edge back from `check_renegotiation`. Its prompt instructs it to write `renegotiation.md` with all five required disclosure sections.
- **`check_renegotiation` node** (`shape=parallelogram`): the **enforcement gate** immediately after `renegotiate`. Its `tool_command` parses `renegotiation.md` structurally: the five required headings (ORIGINAL GOAL, RELAXED CRITERIA, REASON, WHAT THIS RUN WILL ACHIEVE, WHAT THIS RUN WILL NOT ACHIEVE) must each be a **distinct heading line** (plain, `##`, numbered, or bold forms all accepted), appearing **exactly once, in that order, outside code fences**, and every section must have **non-empty content beneath its heading**. Substring matching is deliberately not used — a single line containing all five heading strings, or five bare headings with no content, is rejected: heading presence is not section presence. If the file is missing or malformed, it emits `record_missing` (with the specific defects written to `check_renegotiation_output.txt`) and the drawn edge routes back to `renegotiate` for a retry. Only when it emits `record_ok` does the edge to `simple_implement` fire. This makes the disclosure artifact a **pipeline-enforced requirement**, not an LLM promise — the relaxed path is structurally blocked until a real disclosure is verified.
- **Recorded downgrade**: `renegotiation.md` is written by `renegotiate` and verified by `check_renegotiation` — naming what was relaxed (RFC 5322 → common formats), why (budget exhaustion or implementation failure), and what the run will achieve against the original goal. The downgrade is **durable and inspectable**: any run reader can open `renegotiation.md` and see THAT it happened.
- **Different gate case sets**: `validate_gate` tests RFC 5322 edge cases (quoted strings, domain literals); `validate_relaxed` tests common formats only. The visible difference between the two gate commands IS the renegotiation — a reader of the `.dot` can see exactly what was relaxed.
- **Named result**: the final output names what was achieved versus what was originally asked, so a SUCCESS on the relaxed path is distinguishable from a full-criteria success.

### Evidence-gate craft (TOPO-004/005 cleared)
- **`validate_gate`** (`shape=parallelogram`): runs a fixed RFC 5322 case set mechanically, tracks iteration count in `validate_count.txt`, emits `gate_pass`, `gate_fail`, or `budget_exhausted`, routes on `context.tool.last_line` — not on LLM judgment.
- **`validate_relaxed`** (`shape=parallelogram`): same idiom, relaxed case set, emits `relaxed_pass` or `relaxed_fail`.
- **Gate truthfulness**: both gate commands exit 1 from the python3 check when any case fails, so the shell's `|| printf gate_fail` (or `|| printf relaxed_fail`) fires correctly. The gate commands always exit 0 overall (printf does not set exit code), so `tool.last_line` is always fresh — no stale-label hazard.
- **Stale-label safety**: the `pass` and `budget_exhausted` edges carry `&& outcome=success` (see [DOT-AUTHORING-GUIDE.md](../../docs/DOT-AUTHORING-GUIDE.md) CMD-001/002). General-case discipline: if a tool ever exits nonzero, a stale `tool.last_line` could be the deterministic pick on a FAIL visit.

## Pipeline Structure

```
start --> plan --> implement --> validate_gate
                    ^   |           |          |            \
                    |   | (retry)   | gate_fail | budget_    \ gate_pass
                    +---+           v           | exhausted   v
                                implement       |             done
                                   |            |
           (implement FAIL)        |            | (cannot meet criteria within budget)
                    \              |            |
                     v            v            v
                     +-----> renegotiate <-----+
                                   |            ^
                                   |            | (record_missing -- retry)
                                   v            |
                         check_renegotiation ----+
                          (tool: enforces
                           renegotiation.md)
                                   |
                                   | (record_ok)
                                   v
                           simple_implement --> validate_relaxed
                                                    |           \
                                                    | relax_fail \  relaxed_pass
                                                    v             v
                                            simple_implement     done
```

Two paths lead to `renegotiate`:
1. **Budget exhaustion** (primary teaching): `validate_gate` has been entered N times without the RFC 5322 case set passing → emits `budget_exhausted` → drawn edge routes to `renegotiate`.
2. **Implementation failure** (secondary): `implement` returns a FAIL outcome → drawn `outcome=fail` edge routes to `renegotiate`.

`check_renegotiation` (parallelogram) enforces that `renegotiation.md` exists and is a real disclosure: five distinct heading lines, exactly once each, in prescribed order, outside code fences, each with non-empty content beneath it. The relaxed path is structurally blocked until this gate passes.

Every work node has at least one incoming graph edge — the renegotiation path is visible to topology readers without opening node attributes.

## Expected Behavior

### Happy Path
1. `plan` succeeds
2. `implement` writes `email_regex.txt`
3. `validate_gate` runs the RFC 5322 case set — all pass → `gate_pass`
4. Pipeline exits SUCCESS via `validate_gate -> done`

### Retry Path
1. `implement` writes a regex that fails some RFC 5322 edge cases
2. `validate_gate` emits `gate_fail` → loops back to `implement` with `loop_restart=true`
3. `implement` reads `validate_output.txt` (the gate's output) and fixes the regex
4. Up to budget (3 entries of `validate_gate`); if budget exhausted → renegotiation path

### Renegotiation Path — Budget Exhaustion (primary teaching)
1. `validate_gate` has been entered 3 times without the hard case set passing
2. On the 4th entry, `validate_gate` reads `validate_count.txt` (count = 4 > budget 3) and emits `budget_exhausted`
3. The drawn edge `validate_gate -> renegotiate [condition="context.tool.last_line=budget_exhausted && outcome=success"]` is selected
4. `renegotiate` writes `renegotiation.md` — what was relaxed, why (budget exhaustion), and what this run will achieve against the original RFC 5322 goal
5. `check_renegotiation` (tool gate) structurally parses `renegotiation.md`: five distinct heading lines, once each, in order, outside code fences, each section with content beneath it. Bare headings, a one-line heading salad, out-of-order or duplicated headings, and fenced headings are all rejected; the specific defects are written to `check_renegotiation_output.txt` and the drawn edge routes back to `renegotiate` for a retry. Only on `record_ok` does the edge to `simple_implement` fire
6. `simple_implement` produces a common-format regex (not RFC 5322 compliant)
7. `validate_relaxed` checks the relaxed case set — common formats only, no RFC 5322 edge cases
8. Pipeline exits with `allow_partial=true` satisfying the goal gate — but `renegotiation.md` makes clear this is NOT the original goal, and `check_renegotiation_output.txt` confirms the record was verified

### Renegotiation Path — Implementation Failure (secondary)
1. `implement` returns a FAIL outcome (handler failure, API error)
2. The drawn edge `implement -> renegotiate [condition="outcome=fail"]` is selected at spec §3.3 Step 1 — before the engine reaches `_resolve_failure_retry_target`. The `retry_target` and `fallback_retry_target` attributes are present as belt-and-suspenders for topologies without a drawn edge; they are not the active routing mechanism in this graph.
3. `renegotiate` writes `renegotiation.md` — what was relaxed, why (implementation failure), and what this run will achieve against the original RFC 5322 goal
4. Continues as in the budget-exhaustion path above (through `check_renegotiation`)

### What makes the renegotiation honest
- **Visible**: the `renegotiate` node appears in the drawn topology with explicit incoming trigger edges — a topology reader can see WHERE renegotiation happens and which conditions trigger it
- **Enforced**: `check_renegotiation` (parallelogram tool gate) deterministically blocks the relaxed path until `renegotiation.md` is a real disclosure — distinct heading lines, in order, each with content. Heading presence alone is not enough, and neither is any forgeable shape (heading salad, empty template, fenced headings); the disclosure is a pipeline requirement, not an LLM promise
- **Recorded**: `renegotiation.md` is written by `renegotiate` and verified by `check_renegotiation` before `simple_implement` runs, naming the trigger (budget exhaustion or implementation failure)
- **Named**: the final result names what was achieved (common-format validation) versus what was asked (RFC 5322)
- **Distinguishable**: the two gate case sets differ visibly in the `.dot` — a reader can see exactly what was relaxed
- **Truthful gates**: the gate commands exit 1 on failure so routing labels reflect actual check results — the wrong-but-plausible failure this file exists to prevent cannot hide inside the gate itself

## What to Look For

- `validate_output.txt` — RFC 5322 gate results (PASS/FAIL per case)
- `validate_count.txt` — how many times `validate_gate` has been entered (budget tracking)
- `renegotiation.md` — the recorded downgrade (present only when the relaxed path was taken; names the trigger and what was relaxed; **required** — the relaxed path cannot exit SUCCESS without it)
- `check_renegotiation_output.txt` — the enforcement gate's output confirming `renegotiation.md` was verified (present only on the relaxed path)
- `validate_relaxed_output.txt` — relaxed gate results (present only on the fallback path)
- `email_regex.txt` — the regex produced (may be RFC 5322 or common-format depending on which path ran)
- `checkpoint.json` — shows which nodes were completed and their outcomes
- Goal gate check: look for `pipeline:goal_gate_check` events showing satisfied/unsatisfied lists
- `allow_partial` behavior: if `simple_implement` returns PARTIAL_SUCCESS, it still satisfies the gate — but `renegotiation.md` records that this is a relaxed result, and `check_renegotiation_output.txt` confirms the record was verified before the relaxed path proceeded

## The Wrong-but-Plausible Failure This File Exists to Prevent

Silent criteria drift is the wrong-but-plausible failure at the pipeline-design
level. The pre-upgrade version of this file committed that sin: when the hard
path exhausted retries, the engine quietly rerouted to `simple_implement` (via
a node attribute invisible in the topology), which used `allow_partial=true` to
satisfy the goal gate — while the graph-level goal still claimed RFC 5322.
The run exited SUCCESS having answered a *different question* than it was asked,
and nothing in the topology, the outputs, or the final verdict said so.

That is not converging on the attractor; it is **quietly moving the attractor**
so that wherever the ball already sits counts as the bottom of the bowl.

Renegotiation itself is legitimate — real engineering relaxes scope under budget
pressure. What makes it honest is that the decision is visible (an explicit
routing point a reader can see in the graph), the downgrade is recorded
(`renegotiation.md`), **enforced** (a tool gate blocks the relaxed path until
the record is verified), and the result names the relaxation. This file is the
canonical exemplar of that pattern.

Four pitfalls shaped this design — each was a real defect in an earlier
draft of this file, caught by review, and each is a general lesson for
anyone building a renegotiation path:

1. **Budget exhaustion must route to renegotiate.** An earlier draft only
   routed `implement -> renegotiate` on implementation failure. The
   budget-exhaustion scenario (validate_gate cycling gate_fail → implement →
   gate_fail) never reached `renegotiate` via a drawn edge — the
   `fallback_retry_target` was an inactive attribute. This version adds budget
   tracking to `validate_gate` (following the `bug-fix.dot` pattern) and a drawn
   `validate_gate -> renegotiate [condition="context.tool.last_line=budget_exhausted"]`
   edge. Now the primary teaching — "cannot meet criteria A within budget →
   explicit renegotiation" — is truthfully demonstrated.

2. **Gate labels must be truthful on negative evidence.** An earlier draft's gate
   commands used `python3 -c "...print('gate_pass' if all_pass else 'gate_fail')" > output.txt 2>&1 && echo gate_pass || echo gate_fail`. Since the python3 script always exited 0, the `echo gate_pass` always fired — the gate was always passing, even when the regex failed RFC 5322 cases. This version fixes both gate commands: the python3 scripts exit 1 on failure (`sys.exit(0 if all_pass else 1)`), so `|| printf gate_fail` fires correctly. The gate commands use `printf` (not `echo`) and always exit 0 overall, keeping `tool.last_line` fresh.

3. **The renegotiation record must be enforced, not promised.** An earlier
   draft's `renegotiate` node (an LLM box) only *asked* the LLM to write
   `renegotiation.md`. A backend that omits the file write produces a SUCCESS
   on the relaxed path with no disclosure — indistinguishable from full-criteria
   success. This is the "aspirational contract" recurring bug class: the contract
   is stated but not enforced. This version adds `check_renegotiation`
   (parallelogram tool gate) immediately after `renegotiate`; only when
   `record_ok` fires does the edge to `simple_implement` open. The relaxed path
   is structurally blocked until the disclosure is verified.

4. **The enforcement gate must parse structure, not search substrings.** Two
   successively weaker versions of the gate were each defeated by a forgeable
   shape. A presence check (all five heading strings appear somewhere in the
   file) accepted a file of five bare headings with no content. A
   presence-plus-section-slicing check that still located headings by substring
   accepted a one-line "heading salad" — all five heading strings on a single
   line plus one unrelated sentence, which every alleged section then claimed
   as its content. The lesson: **a lenient parser inside an enforcement gate is
   itself the wrong-but-plausible bug.** This version parses actual heading
   LINES (exact match after stripping markdown/numbering/bold markers and a
   trailing colon), requires each exactly once, in prescribed order, outside
   code fences, and requires non-empty content between each heading line and
   the next. Both forgeries — and their neighbors (out-of-order, duplicated,
   fenced headings, empty sections) — are rejected with named diagnostics in
   `check_renegotiation_output.txt`. The gate enforces that the downgrade is
   RECORDED (substantive, correctly structured content), not merely LABELED.

Every one of these enforcement claims is regression-tested against the
engine-parsed gate commands (never hand-copied strings) in
[`modules/loop-pipeline/tests/test_retry_with_fallback_evidence.py`](../../modules/loop-pipeline/tests/test_retry_with_fallback_evidence.py).
