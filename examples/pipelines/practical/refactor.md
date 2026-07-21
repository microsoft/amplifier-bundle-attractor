# Safe Refactoring Pipeline

Analyze code smells, plan refactoring, execute with snapshot test safety net.

## Usage

```bash
attractor run examples/pipelines/practical/refactor.dot \
    --param goal="Refactor src/auth/handler.py to reduce complexity and extract helper functions" \
    --cwd .
```

Run from the repo root so box-node agents root their writes at `--cwd .` (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

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
