# Feature Build Pipeline

Parse a spec, break into subtasks, implement in parallel, integration test, human review.

## Usage

```bash
attractor run examples/pipelines/practical/feature-build.dot \
    --param goal="<describe the feature to build, e.g. avatar upload with thumbnails>" \
    --cwd . \
    --on-human-gate auto-approve
```

`--on-human-gate auto-approve` is required to run non-interactively: this pipeline has a human-review gate (hexagon) that otherwise waits for a person. Point it at your own repo: `cd` in, replace the goal, and keep `--cwd .` (that's where the pipeline reads and writes). This example doesn't ship a target codebase. Running from the repo root also keeps box-node agents rooted correctly (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

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

## Models

Model-agnostic -- every node runs on your configured default provider/model. To route the planning step (`parse_spec`) to a stronger model, add a `model_stylesheet` (see `examples/pipelines/06-model-stylesheet.dot`).
