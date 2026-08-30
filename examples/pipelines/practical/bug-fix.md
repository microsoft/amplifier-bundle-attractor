# Bug Fix Pipeline

Convergence exemplar: evidence-gated bug fix with feedback-accumulating outer
loop and root-cause wall. This pipeline is the flagship demonstration of the
attractor methodology -- a pipeline whose exit is structurally unreachable
until the bug is *demonstrably* fixed, whose failures route back with
accumulated critique, and whose shape teaches the doctrine so that copying
this file teaches it too.

## Usage

This example ships a target: `examples/pipelines/practical/sample/` contains a
`user_service.py` with a planted bug -- `get_display_name()` raises `TypeError`
when a user's `avatar` is `None`. Run the block below **from the attractor repo
root** and it works walk-up, no setup:

```bash
DOT="$PWD/examples/pipelines/practical/bug-fix.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-bugfix-demo
cd /tmp/attractor-bugfix-demo
dot-runner run "$DOT" \
    --worker coding-agent \
    --param goal="Fix the bug in user_service.py: get_display_name() raises TypeError when a user's avatar is None. Reproduce it first, apply the minimal fix, and add a regression test that covers the None-avatar case." \
    --cwd .
```

We copy the sample to a temp dir first so the committed fixture stays pristine
and every run starts clean. `$DOT` captures the pipeline's absolute path before
`cd`, because the `.dot` path is resolved from your current directory while
`--cwd` is where the pipeline reads and writes. Process cwd must equal `--cwd`
for box-node (agent) pipelines -- that's why we `cd` into the copy (see
`modules/pipeline-runner/KNOWN_ISSUES.md`).

**Point it at your own repo instead:** replace the `cp`/`cd` with
`cd /path/to/your/repo`, keep `$DOT` absolute, keep `--cwd .`, and swap in
your bug.

**Verify the result:** `cd /tmp/attractor-bugfix-demo && pytest -v` -- the
suite goes from 2 passing to 3 (the added None-avatar regression test), and
`get_display_name` now handles the missing avatar.

## The Shape (design-order)

The attractor methodology says: design the **sink first**, then the **gate**,
then the **loop**, then the **steps**. Read the pipeline in that order to
understand why each node is there.

### 1. Sink: the exit is unreachable until the work is done

The pipeline has two terminals:

- **`done`** (success): only reachable via `verdict_gate` on `ship`. `verdict_gate`
  only emits `ship` when `critique` writes `VERDICT: SHIP` to `.ai/critique.md`.
  `critique` only runs after `test_gate` passes. This means `done` is structurally
  unreachable until (a) `pytest` passes AND (b) a fresh reviewer judges the fix
  sound. The `pytest` condition is verified by code (not an LLM opinion).

- **`escalated`** (budget-exhaustion or hard-failure handoff): reachable via
  `postmortem`. `postmortem` is reached when the `test_gate` iteration counter
  is exhausted, or on a hard FAIL from `reproduce`, `diagnose`,
  `implement_fix`, `critique`, or `feedback`. A hard failure from `postmortem`
  itself routes directly to `escalated`. `escalated` is a deterministic tool
  node: it writes a minimal handoff even when the detailed LLM postmortem could
  not be produced. Thus one bad day at any LLM node is absorbed into a
  salvageable handoff rather than an unhandled/bare failure. `done` remains the
  graph's sole successful `Msquare` exit; an escalation is not success and can
  never have passed the test gate.

### 2. Gate: evidence over LLM verdicts

The `test_gate` node uses `shape=parallelogram` + `tool_command`, not
`shape=diamond`:

```dot
// RIGHT -- runs the real verifier, routes on observed evidence:
test_gate [shape=parallelogram, label="Tests Pass?",
           tool_command="mkdir -p .ai && pytest -q > .ai/test.log 2>&1; [ $? -eq 0 ] && printf pass || printf fail"]
test_gate -> critique [condition="context.tool.last_line=pass && outcome=success"]
test_gate -> triage   [condition="context.tool.last_line=fail"]
```

(Abridged for the point being made -- the shipped `test_gate` also implements
the iteration budget counter, see section 5. The routing shape is identical.)

**Why not diamond**: `ConditionalHandler` (the diamond handler) unconditionally
returns SUCCESS. A `condition="outcome!=success"` edge from a diamond is always
false -- the fix loop would never fire, and the pipeline would report success
even when tests are failing. The parallelogram gate runs `pytest` directly and
routes on the actual exit code.

**Why `&& outcome=success` on the pass edge** (STALE-LABEL RULE): on this
specific graph, `test_gate`'s tool command always exits 0 (both branches end
with `printf pass` or `printf fail`, so ToolHandler always stores a fresh
`tool.last_line`). The conjunction is therefore harmless here -- not strictly
required. It is included as general-case discipline: in the broader engine,
a tool node that exits nonzero does NOT refresh `tool.last_line`, and on a
second visit a stale `"pass"` label + FAIL simultaneously match two edges.
Historical note (T0-4): prior to spec-conformance restoration, the engine
fanned out to both edges in this case; after T0-4 it conforms to spec §3.3
and picks ONE edge deterministically (weight desc, then lexical target-id
tiebreak). The deterministic pick can still be the wrong edge -- so the
`&& outcome=success` conjunction remains good explicitness discipline. Apply
it to any `last_line` edge that shares a source node with an `outcome=fail`
edge, as future-proofing when the tool command changes.

**Why save output to `.ai/test.log`**: the triage node (see below) reads the
test failure output to compute a failure signature. Without saving the output,
triage cannot detect repeated identical failures.

### 3. Loop: inner fix cycle + root-cause wall + outer quality loop

The pipeline has three corrective mechanisms:

**Inner loop** (mechanical): `implement_fix → test_gate → [fail] → triage → [novel] → implement_fix`

Tight fix cycle. Session continuity (`default_thread_id` + `default_fidelity=full`)
so the fixer sees what it just tried.

**Root-cause wall**: `triage → [repeat] → diagnose → implement_fix`

If the test fails with the *same failure signature twice*, `triage` routes to
`diagnose` instead of back to `implement_fix`. Stop patching -- find the actual
root cause. An attractor absorbs model drift, not deterministic bugs. The
`triage` node computes an md5 hash of the last 20 lines of `.ai/test.log`
(stripping timing tokens to avoid false "novel" on identical failures with
different runtimes) and compares to the stored previous signature.

```dot
triage [shape=parallelogram, label="Novel Failure?",
        tool_command="sig=$(tail -20 .ai/test.log ... | md5sum ...); prev=$(cat .ai/last-fail-sig ...); ..."]
triage -> implement_fix [label="novel",  condition="context.tool.last_line=novel"]
triage -> diagnose      [label="repeat", condition="context.tool.last_line=repeat"]
```

**Outer loop** (quality): `test_gate → [pass] → critique → verdict_gate →
[iterate] → feedback → [loop_restart] → implement_fix`

Fresh eyes on green work. `critique` judges fix quality and test integrity
(was the test weakened to get to green?). `verdict_gate` greps `.ai/critique.md`
for the verdict -- no LLM opinion in the routing. `feedback` distills the
highest-leverage change and writes it to `.ai/feedback/`. `loop_restart` on the
back-edge resets iteration state so the next `implement_fix` starts fresh while
retaining the accumulated critique.

### 4. Feedback accumulation: descent, not re-flip

Without feedback, a retry is a coin re-flip -- the same model with the same
context will likely make the same mistake. With feedback:

1. `critique` writes specific findings to `.ai/critique.md`
2. `feedback` distills the highest-leverage change to `.ai/feedback/N-next.md`
3. `implement_fix` reads `.ai/feedback/` at the top of its prompt on every
   entry -- accumulated critique from prior iterations makes each attempt
   informed descent toward the goal

The feedback channel is a file convention (`.ai/feedback/`), not an engine
mechanism. The critique node must write there; the attempt node must be
instructed to read it. If the attempt prompt doesn't reference `.ai/feedback/`,
the feedback is written but never read -- a coin re-flip with extra steps.

### 5. Budget as a decision point

The pipeline has three budget mechanisms with different termination semantics:

**Iteration counter** (the main convergence budget): `test_gate` increments
`.ai/iter` on EVERY entry -- both inner-loop visits (red tests → triage →
implement_fix → test_gate) and outer-loop visits (green tests → critique →
iterate → feedback → implement_fix → test_gate). When the count exceeds
`.ai/budget` (default 5), `test_gate` emits `"exhausted"` and routes to
`postmortem` → `escalated` without running `pytest`. This is the designed
decision point for ordinary non-convergence: the postmortem node writes
`.ai/postmortem/report.md` with what was attempted, whether the loop was
descending or oscillating, and options for a human to decide (change approach /
split the task / escalate). `escalated` then writes an actionable handoff.
`escalated` fails loudly by its own exit code (`exit 1`, after the artifacts
are written, with `max_retries=0` so the deliberate failure is not retried),
so the run reports failure once these artifacts exist -- a salvageable
failure, not a bare FAIL, and portable: it does not depend on any particular
engine dead-end semantics.

To override the default budget, write a number to `.ai/budget` before running:
```bash
mkdir -p /tmp/attractor-bugfix-demo/.ai && echo 8 > /tmp/attractor-bugfix-demo/.ai/budget
```

**`default_max_retries=3`** bounds `implement_fix` *hard failures* (LLM node
FAIL -- e.g., provider error, context overflow). It does NOT bound normal
convergence cycles: when `test_gate` returns `"fail"`, the tool node itself
succeeds (exit 0 from `printf fail`), so no retry budget is consumed. When
`implement_fix` exhausts its retry budget (hard FAIL), the `outcome=fail` edge
also routes to `postmortem` → `escalated`.

**`max_pipeline_duration="3600s"`** is a safety ceiling, not a postmortem
trigger. The engine checks this limit at the top of every iteration and, if
exceeded, returns `Outcome(FAIL, failure_reason="max_pipeline_duration_exceeded")`
immediately -- it does NOT traverse any graph edge. This means a duration
timeout produces a bare FAIL with no postmortem. Treat it as an emergency stop,
not as a normal budget decision point. The iteration counter (above) is the
designed decision point; the duration limit is the fire exit.

## Convergence evidence

To see the pipeline converge, run it against the shipped sample:

```bash
DOT="$PWD/examples/pipelines/practical/bug-fix.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-bugfix-demo
cd /tmp/attractor-bugfix-demo
dot-runner run "$DOT" \
    --worker coding-agent \
    --param goal="Fix the bug in user_service.py: get_display_name() raises TypeError when a user's avatar is None. Reproduce it first, apply the minimal fix, and add a regression test that covers the None-avatar case." \
    --cwd .
```

The engine writes a run directory (shown as `logs=...` by `dot-runner run`)
containing `manifest.json`, `checkpoint.json`, and per-node output
directories. A real induced two-pass run against the current graph (20 edges,
12 nodes) is committed at
[`evidence/bugfix-convergence-2026-07-30/`](evidence/bugfix-convergence-2026-07-30/).
Its `manifest.json`, `goal.txt`, `feedback-2-next.md`, `critique-iter1.md`,
`critique-final.md`, and `pytest-result.txt` are run artifacts. Two files in
that set come from observability beyond what the engine on `main` produces:
`trace.jsonl` was written by an engine build carrying the
convergence-observability changes proposed in PR #99 (an engine without those
changes does not write `trace.jsonl`), and `events.jsonl` is the same run's
`pipeline:*` event stream captured by a small custom hooks observer driving
`run_pipeline()` -- not an artifact the standalone CLI creates by itself. The
sample was copied to `/tmp`, so the committed fixture remained untouched.

In that run, iteration 0 fixes the code and test but intentionally leaves a
stale comment in the newly created reproduction script. The green test gate
then reaches critique, which returns ITERATE; `feedback-2-next.md` identifies
that exact stale comment. The captured `trace.jsonl` and `events.jsonl`
record `feedback` at iteration 0, then a second `implement_fix`,
`test_gate`, `critique`, and `verdict_gate` at iteration 1. The second attempt
reads the written feedback, corrects only that comment, and the final critique
records `VERDICT: SHIP`; `pytest-result.txt` records all three tests passing.

A supplemental run demonstrating the budget-exhaustion path (iteration counter
→ `postmortem` → `escalated`) is committed at
[`evidence/bugfix-budget-exhaustion/`](evidence/bugfix-budget-exhaustion/). That run
used `.ai/budget=0` to force immediate exhaustion. It predates the current
20-edge graph but still demonstrates the counter's ordinary-nonconvergence
route with real `postmortem-report.md` and `escalation.md` artifacts; the
`bugfix-convergence-2026-07-30` run is the evidence for the final graph.

When inspecting your own run, inspect the feedback files (`.ai/feedback/`) and
`.ai/critique.md` in the target repo, and the per-node output directories in
the run logs. If your engine build writes `trace.jsonl` (the
convergence-observability changes proposed in PR #99), also look for two
`implement_fix` completions with different `iteration` values there. Together
they show that the later attempt was informed by durable written feedback --
descent, not a re-flip.

## What It Does

1. **Reproduce** -- Writes and runs a minimal reproduction script
2. **Diagnose** -- Analyzes the root cause (reasoning-heavy step); also fires
   as the root-cause wall when `triage` detects the same failure twice
3. **Implement Fix** -- Makes the minimal code change; writes regression test;
   reads `.ai/feedback/` for prior critique on re-entry
4. **Test Gate** -- Increments iteration counter; checks budget (routes
   `exhausted` to postmortem if exceeded); otherwise runs `pytest`
   deterministically, saves output to `.ai/test.log`, routes on exit code
5. **Triage** -- Computes failure signature; routes "novel" to implement_fix,
   "repeat" to diagnose (root-cause wall)
6. **Critique** -- Fresh-eyes quality judge; writes verdict to `.ai/critique.md`
7. **Verdict Gate** -- Greps the verdict; routes to ship or iterate
8. **Feedback** -- Distills highest-leverage change to `.ai/feedback/`;
   `loop_restart` back to implement_fix
9. **Postmortem** -- Budget-exhaustion decision point (reached via test_gate
   iteration counter OR a hard LLM failure); writes a detailed salvage report.
   If it hard-fails, its FAIL edge bypasses it to the deterministic `escalated`
   handoff, so a single LLM outage remains salvageable and distinct from `done`.

## Models

Model-agnostic -- every node runs on your configured default provider/model. To
route the reasoning-heavy `diagnose` step to a stronger model, add a
`model_stylesheet` and tag the node with a class (see
`examples/pipelines/06-model-stylesheet.dot`).

## Authoring the Next Pipeline

This pipeline teaches a design discipline. When you copy it, ask:

1. **What is the sink?** Design the exit condition first. What evidence makes
   the exit unreachable until the work is done?
2. **What is the gate?** Is it deterministic (parallelogram + tool exit code)?
   Or are you trusting an LLM's opinion (diamond + outcome=)?
3. **What is the loop?** Is there a corrective back-edge? Does it carry
   feedback, or is it a coin re-flip?
4. **Is there a root-cause wall?** If the same failure repeats, does the
   pipeline stop patching and diagnose, or does it retry blindly?
5. **Is the budget bounded?** A loop without a rim is an infinite loop. What
   bounds the loop -- a duration limit, a budget counter, or both?

If you can answer all five, you have an attractor. If you can't, you have a
flowchart.
