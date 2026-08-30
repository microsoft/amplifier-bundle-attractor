# Test Generation Pipeline

Generate tests, run them, and fix failures in a self-healing retry loop.

## Usage

This example ships a target: `examples/pipelines/practical/sample/` contains a
`user_service.py` with only happy-path tests -- the None-avatar path, the
missing-user path, and all of `validate_user()` are uncovered. Run the block
below **from the attractor repo root** and it works walk-up, no setup:

```bash
DOT="$PWD/examples/pipelines/practical/test-gen.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-testgen-demo
cd /tmp/attractor-testgen-demo
dot-runner run "$DOT" \
    --worker coding-agent \
    --param goal="Expand test coverage for user_service.py. The existing suite only covers the happy path -- add tests for the untested paths: get_display_name() with a None avatar, get_user() for an unknown username, and the validate_user() rules (short/empty/non-alphanumeric username, missing/malformed email)." \
    --cwd .
```

We copy the sample to a temp dir first so the committed fixture stays pristine
and every run starts clean. `$DOT` captures the pipeline's absolute path before
`cd`, because the `.dot` path is resolved from your current directory while
`--cwd` is where the pipeline reads and writes. Process cwd must equal `--cwd`
for box-node (agent) pipelines -- that's why we `cd` into the copy (see
`modules/pipeline-runner/KNOWN_ISSUES.md`).

**Point it at your own repo instead:** replace the `cp`/`cd` with `cd /path/to/your/repo`, keep `$DOT` absolute, keep `--cwd .`, and swap in the module you want tested.

**Verify the result:** `cd /tmp/attractor-testgen-demo && pytest -v` -- the suite grows well past the original 2 happy-path tests and stays green.

## What It Does

1. **Analyze Module** -- Reads source files, identifies public API surface and edge cases
2. **Identify Gaps** -- Compares existing tests against the API surface
3. **Write Tests** -- Generates pytest tests covering identified gaps
4. **Run Tests** -- Executes the test suite and reports results
5. **Fix Failures** -- Diagnoses and fixes test failures (retry loop)

## Key Feature: Self-Healing Loop

The retry loop between `run_tests` and `fix_failures` means the pipeline doesn't just generate tests -- it validates them and fixes failures automatically. Up to 3 retry cycles.

## Routing Pattern: Evidence Gate

The `test_gate` node uses `shape=parallelogram` + `tool_command`, not `shape=diamond`:

```dot
// RIGHT -- runs the real verifier, routes on observed evidence:
test_gate [shape=parallelogram, label="Tests Pass?",
           tool_command="pytest -q > /dev/null 2>&1 && printf pass || printf fail"]
test_gate -> done         [condition="context.tool.last_line=pass"]
test_gate -> fix_failures [condition="context.tool.last_line=fail"]
```

**Why not diamond**: `ConditionalHandler` (the diamond handler) unconditionally returns SUCCESS.
A `condition="outcome!=success"` edge from a diamond is always false -- the self-healing loop
would never fire, and the pipeline would report success with failing tests. The parallelogram
gate runs `pytest` directly and routes on the actual exit code.

**FAIL routing**: `write_tests` has `retry_target="fix_failures"`. If any LLM node crashes
(hard FAIL), the engine's `default_max_retries=3` provides retry before fail-fast termination.

## Models

Model-agnostic -- every node runs on your configured default provider/model. Add a `model_stylesheet` only if you want to route specific steps to specific models (see `examples/pipelines/06-model-stylesheet.dot`).
