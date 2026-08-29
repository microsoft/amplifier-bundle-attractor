# Safe Refactoring Pipeline

Analyze code smells, plan refactoring, execute with snapshot test safety net.

## Usage

This example ships a target: `examples/pipelines/practical/sample/` contains a
`user_service.py` whose `validate_user()` is a long, deeply-nested method with
duplicated validation blocks -- and a green test suite to prove the refactor
preserves behavior. Run the block below **from the attractor repo root** and it
works walk-up, no setup:

```bash
DOT="$PWD/examples/pipelines/practical/refactor.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-refactor-demo
cd /tmp/attractor-refactor-demo
dot-runner run "$DOT" \
    --param goal="Refactor validate_user() in user_service.py: remove the deep nesting and the duplicated username/email validation blocks by extracting a helper. Preserve behavior exactly -- the existing tests must still pass." \
    --cwd .
```

We copy the sample to a temp dir first so the committed fixture stays pristine
and every run starts clean. `$DOT` captures the pipeline's absolute path before
`cd`, because the `.dot` path is resolved from your current directory while
`--cwd` is where the pipeline reads and writes. Process cwd must equal `--cwd`
for box-node (agent) pipelines -- that's why we `cd` into the copy (see
`modules/pipeline-runner/KNOWN_ISSUES.md`).

**Point it at your own repo instead:** replace the `cp`/`cd` with `cd /path/to/your/repo`, keep `$DOT` absolute, keep `--cwd .`, and swap in your refactor goal.

**Verify the result:** `cd /tmp/attractor-refactor-demo && pytest -v` -- the suite stays green (behavior preserved), and `validate_user()` is flatter with the duplication extracted.

## What It Does

1. **Analyze Smells** -- Identifies code smells ranked by impact
2. **Plan Refactoring** -- Creates a risk-ordered plan (reasoning-heavy step)
3. **Snapshot Tests** -- Captures baseline test results (or writes characterization tests)
4. **Implement** -- Executes the plan with small, atomic edits
5. **Run Tests** -- Verifies no regressions against baseline (retries if failures, max 2 attempts)
6. **Diff Review** -- Verifies behavior preservation

## Models

Model-agnostic -- every node runs on your configured default provider/model. To route the reasoning-heavy steps (`plan_refactor`, `diff_review`) to a stronger model, add a `model_stylesheet` and tag those nodes with a class (see `examples/pipelines/06-model-stylesheet.dot`).

## Key Feature: Snapshot Safety Net

The snapshot-first approach gives a safety net. If the refactoring breaks tests, the retry loop between `run_tests` and `implement_refactor` catches regressions immediately. The diff review confirms behavior preservation.

## Routing Pattern: Evidence Gate

The `test_gate` node uses `shape=parallelogram` + `tool_command`, not `shape=diamond`:

```dot
// RIGHT -- runs the real verifier, routes on observed evidence:
test_gate [shape=parallelogram, label="Tests Pass?",
           tool_command="pytest -q > /dev/null 2>&1 && printf pass || printf fail"]
test_gate -> diff_review        [condition="context.tool.last_line=pass"]
test_gate -> implement_refactor [condition="context.tool.last_line=fail"]
```

**Why not diamond**: `ConditionalHandler` (the diamond handler) unconditionally returns SUCCESS.
A `condition="outcome!=success"` edge from a diamond is always false -- the fix loop would never
fire, and the pipeline would proceed to diff review even when tests are failing. The parallelogram
gate runs `pytest` directly and routes on the actual exit code.

**FAIL routing**: `snapshot_tests` has `retry_target="snapshot_tests"`. If any LLM node crashes
(hard FAIL), the engine's `default_max_retries=3` provides retry before fail-fast termination.
