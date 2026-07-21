# Bug Fix Pipeline

Systematic debugging: reproduce, diagnose, fix, write regression test, verify.

## Usage

```bash
attractor run examples/pipelines/practical/bug-fix.dot \
    --param goal="<describe the bug to fix, e.g. a crash when a user has no avatar>" \
    --cwd .
```

Point this at your own repo: `cd` into it, replace the goal with your bug, and keep `--cwd .` (that's where the pipeline reads and writes). This example doesn't ship a target codebase. Running from the repo root also keeps box-node agents rooted correctly (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

## What It Does

1. **Reproduce** -- Writes and runs a minimal reproduction script
2. **Diagnose** -- Analyzes the root cause (reasoning-heavy step)
3. **Implement Fix** -- Makes the minimal code change to resolve the issue
4. **Regression Test** -- Writes a test that proves the fix works
5. **Run Tests** -- Verifies all tests pass (retries fix if not)

## Models

Model-agnostic -- every node runs on your configured default provider/model. To route the reasoning-heavy `diagnose` step to a stronger model, add a `model_stylesheet` and tag the node with a class (see `examples/pipelines/06-model-stylesheet.dot`).

## Key Feature: Disciplined Workflow

Forces the reproduce-first pattern. The regression test ensures the bug stays fixed. The retry loop catches cases where the fix breaks other tests.
