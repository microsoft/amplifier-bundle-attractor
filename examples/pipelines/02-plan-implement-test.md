# 02 - Plan-Implement-Test Pipeline

> **Convergence tutorial** — this guide teaches multi-stage staging (`plan →
> implement → test`) combined with a `goal_gate` and a real corrective loop.
> The exit is structurally unreachable until a tool gate reports the tests pass.
> For the minimal convergence shape (no staging), start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## What this teaches (and what 00 doesn't)

Tutorial 00 shows the minimal convergence skeleton: one worker, one gate,
one back-edge. Tutorial 02 adds **explicit staging** — `plan`, `implement`, and
`test_gate` as separate nodes — and shows how `goal_gate` + `retry_target` work
together to make the gate meaningful at the exit.

**The recipe-plane note (own it explicitly):** Doctrine
(`docs/PIPELINE_DESIGN_PRINCIPLES.md §0`) says that `plan → implement → test`
as graph nodes encodes the *model's* job (domain decomposition) into the
*author's* job (control plane). In real work, those phases belong in the
worker's prompt, not the graph — see `practical/bug-fix.dot` and
`examples/patterns/task-runner.dot` as the real-work shapes. Here the staged
nodes are a **teaching device**: they let the reader see multi-stage traversal,
context flow between stages, and the `goal_gate`+`retry_target` pairing in a
setting where the shape is easy to reason about. The convergence skeleton
(evidence gate + back-edge + budget) is the load-bearing structure.

## Run it

Self-contained — the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/02-plan-implement-test.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the
`$DOT` capture + `cd` + `--cwd .` are needed.

## Pipeline Structure

```
start --> plan --> implement --> test_gate --> done
                      ^           (goal_gate)
                      |
                      +-- (gate_fail, loop_restart) --+
```

The shape: `plan` produces an outline; `implement` writes `calculator.py` and
`test_calculator.py`; `test_gate` (a parallelogram tool node) runs `pytest`
mechanically and echoes a routing label as its last output line. The engine
stores that label in `context.tool.last_line`; the outgoing edges condition on
it:

- `gate_pass` → proceed to `done` (exit is now reachable)
- `gate_fail` → loop back to `implement` with `loop_restart=true` for a fresh
  retry iteration

**Why a tool gate, not an LLM verdict?** The gate is deterministic: the test
suite either passes or it does not. No LLM self-report can fake a green test
run. This is what makes the exit structurally unreachable until the work is
actually done — the same principle Tutorial 00 introduces.

**The `goal_gate` + `retry_target` pairing:** `test_gate` carries both
`goal_gate=true` and `retry_target=implement`. If the pipeline reaches the exit
node with an unsatisfied gate, the engine uses `retry_target` to route back
rather than failing hard. The corrective back-edge handles the in-loop case;
`retry_target` handles the exit-time case. Together they make the gate
meaningful in both contexts.

## What This Exercises

- **Staged traversal**: Three nodes (`plan`, `implement`, `test_gate`) executed
  in sequence with context flowing between them
- **Corrective back-edge**: `test_gate → implement` with `loop_restart=true`
  resets the iteration for a fresh attempt
- **Evidence-gated exit**: the exit is structurally unreachable until
  `context.tool.last_line=gate_pass`
- **`goal_gate` + `retry_target`**: the gate survives to the exit node check;
  `retry_target` names where to resume
- **Iteration budget**: `max_pipeline_duration=300000` (5 minutes) is the
  duration cap the engine actually enforces on this graph. Note: `default_max_retries`
  bounds handler retries on individual nodes, not graph traversal through
  `loop_restart` edges — a loop without a real bound is a different foot-gun,
  not a fix. Use `max_pipeline_duration` for a duration cap the engine enforces.
- **Context updates**: each stage's output is visible to subsequent stages via
  `context`

## Expected Behavior

1. `plan` executes — lists 3 steps, returns SUCCESS
2. `implement` executes — writes `calculator.py` and `test_calculator.py`,
   returns SUCCESS
3. `test_gate` runs `pytest -q test_calculator.py` mechanically:
   - If tests pass → echoes `gate_pass` → engine routes to `done`
   - If tests fail → echoes `gate_fail` → engine routes back to `implement`
     with `loop_restart=true` for a fresh attempt; `implement` reads
     `test_output.txt` to see what failed
4. Loop repeats until `gate_pass` or the 5-minute duration cap
   (`max_pipeline_duration=300000`) is exceeded
5. Pipeline completes with SUCCESS once the gate reports `gate_pass`

**Files produced on disk:**
- `calculator.py` — the implementation (created/revised by `implement`)
- `test_calculator.py` — the pytest test file (created/revised by `implement`)
- `test_output.txt` — pytest output captured by `test_gate` (read by
  `implement` on retry iterations)

## What to Look For

- Stage directories in logs: `plan/`, `implement/`, `test_gate/`
- On a retry: a second `implement/` iteration directory; `test_output.txt`
  present on disk before it runs
- `checkpoint.json` shows `test_gate` in `completed_nodes` with
  `"outcome": "success"` (goal gate satisfied)
- `context.tool.last_line` = `"gate_pass"` in the final context
- Pipeline final outcome is SUCCESS

## Live-run evidence

A controlled fixture run of this pipeline — mock LLM backend, tool handler
returning `gate_fail` on iteration 1 then `gate_pass` on iteration 2 — is
stored at:

```
examples/pipelines/practical/evidence/plan-implement-test-2026-08-03/events.jsonl
```

Key events in that stream:

```
pipeline:edge_selected  test_gate -> implement  "fix and retry"   (back-edge fires)
pipeline:edge_selected  test_gate -> done        "tests pass"      (convergence)
pipeline:goal_gate_check  satisfied: ["test_gate"]
pipeline:complete  status: success
```

The corrective back-edge fires once before the success path — the loop is
real, not decorative. The `goal_gate_check` confirms the gate was satisfied
(not just bypassed).

The transcript was captured from the reproducible fixture test, which runs
this exact `.dot` through the real `PipelineEngine` (mock LLM backend, no
credentials needed) and asserts the event sequence. Reproduce it yourself:

```bash
cd modules/loop-pipeline
uv run pytest -q tests/test_plan_implement_test_evidence.py
```

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the plan-implement-test pipeline"
    context:
      pipeline_path: "examples/pipelines/02-plan-implement-test.dot"
```

## Where to go next

- **Tutorial 00** (`00-convergence-loop.md`) — the minimal shape: one worker,
  one gate, one back-edge. No staging. Read this first if you haven't.
- **`practical/bug-fix.dot`** — the convergence skeleton applied to real work,
  with an inner fix loop, a root-cause wall, and a budget wall. This is what
  the staged phases look like when they belong in the worker's prompt instead
  of the graph.
- **`examples/patterns/task-runner.dot`** — the battle-hardened goal+DoD
  runner: orient / attempt / verify / critique / triage / postmortem / package.
  Zero domain phases; all control-plane responsibilities.
