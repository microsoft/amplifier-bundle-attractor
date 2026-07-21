# Feature Build Pipeline

Parse a spec, break into subtasks, implement in parallel, integration test, human review.

## Usage

```bash
attractor run examples/pipelines/practical/feature-build.dot \
    --param goal="Add user avatar upload with S3 storage and thumbnail generation" \
    --cwd .
```

Run from the repo root so box-node agents root their writes at `--cwd .` (see `modules/pipeline-runner/KNOWN_ISSUES.md`). This pipeline has a human-gate (hexagon) node; add `--on-human-gate auto-approve` to run it non-interactively.

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
