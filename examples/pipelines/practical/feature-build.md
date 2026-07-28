# Feature Build Pipeline

Parse a spec, break into subtasks, implement in parallel, integration test, human review.

## Usage

```bash
attractor run examples/pipelines/practical/feature-build.dot \
    --param goal="<describe the feature to build, e.g. avatar upload with thumbnails>" \
    --cwd . \
    --on-human-gate auto-approve
```

**About `--on-human-gate auto-approve`:** this pipeline has a human-review gate (hexagon) that blocks a non-interactive run. `auto-approve` unblocks it by always taking the gate's **first** option — which here is **Ship** — so it *never* exercises the Rework path. It lets the demo run to completion, but the review checkpoint becomes a no-op that ships every time. Drop the flag and run interactively if you actually want the gate to mean something.

**Pointing at your own repo:** the `.dot` path is resolved relative to your *current* directory, while `--cwd` is where the pipeline reads and writes code. Give the pipeline file an absolute (or attractor-repo-relative) path and point `--cwd` at your repo — e.g. `attractor run /path/to/attractor/examples/pipelines/practical/feature-build.dot --cwd /path/to/your/repo`. This example doesn't ship a target codebase. See `modules/pipeline-runner/KNOWN_ISSUES.md` for the box-node cwd caveat.

## What It Does

1. **Parse Spec** -- Breaks the feature into data model, business logic, API, and test components
2. **Plan Subtasks** -- Creates 2-3 independent, non-conflicting implementation tasks
3. **Parallel Implement** -- Simultaneously builds core logic, API layer, and unit tests
4. **Integration Test** -- Runs all tests together, fixes integration issues (retry loop)
5. **Human Review** -- Pauses for human approval before finalizing (Ship or Rework)

## Key Features

- **Parallel implementation** of independent subtasks for faster builds
- **plan_subtasks** explicitly ensures no file conflicts between parallel branches
- **Human gate** before finalization gives the developer a review checkpoint
- **Integration test retry** catches cross-branch issues automatically

## Routing Pattern: Evidence Gate

The `test_gate` node uses `shape=parallelogram` + `tool_command`, not `shape=diamond`:

```dot
// RIGHT -- runs the real verifier, routes on observed evidence:
test_gate [shape=parallelogram, label="Tests Pass?",
           tool_command="pytest -q > /dev/null 2>&1 && printf pass || printf fail"]
test_gate -> review_gate      [condition="context.tool.last_line=pass"]
test_gate -> integration_test [condition="context.tool.last_line=fail"]
```

**Why not diamond**: `ConditionalHandler` (the diamond handler) unconditionally returns SUCCESS.
A `condition="outcome!=success"` edge from a diamond is always false -- the integration test
retry loop would never fire, and the pipeline would proceed to human review even when tests are
failing. The parallelogram gate runs `pytest` directly and routes on the actual exit code.

**FAIL routing**: `integration_test` has `retry_target="integration_test"` and `max_retries=3`.
If the LLM node crashes (hard FAIL), the engine retries up to 3 times before fail-fast
termination.

## Models

Model-agnostic -- every node runs on your configured default provider/model. To route the planning step (`parse_spec`) to a stronger model, add a `model_stylesheet` (see `examples/pipelines/06-model-stylesheet.dot`).
