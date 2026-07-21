# Test Generation Pipeline

Generate tests, run them, and fix failures in a self-healing retry loop.

## Usage

```bash
attractor run examples/pipelines/practical/test-gen.dot \
    --param goal="<describe what to test, e.g. the authentication module>" \
    --cwd .
```

Point this at your own repo: `cd` into it, replace the goal with the module you want tested, and keep `--cwd .` (that's where the pipeline reads and writes). This example doesn't ship a target codebase. Running from the repo root also keeps box-node agents rooted correctly (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

## What It Does

1. **Analyze Module** -- Reads source files, identifies public API surface and edge cases
2. **Identify Gaps** -- Compares existing tests against the API surface
3. **Write Tests** -- Generates pytest tests covering identified gaps
4. **Run Tests** -- Executes the test suite and reports results
5. **Fix Failures** -- Diagnoses and fixes test failures (retry loop)

## Key Feature: Self-Healing Loop

The retry loop between `run_tests` and `fix_failures` means the pipeline doesn't just generate tests -- it validates them and fixes failures automatically. Up to 3 retry cycles.

## Models

Model-agnostic -- every node runs on your configured default provider/model. Add a `model_stylesheet` only if you want to route specific steps to specific models (see `examples/pipelines/06-model-stylesheet.dot`).
