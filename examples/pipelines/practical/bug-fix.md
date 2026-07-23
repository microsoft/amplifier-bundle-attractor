# Bug Fix Pipeline

Systematic debugging: reproduce, diagnose, fix, write regression test, verify.

## Usage

This example ships a target: `examples/pipelines/practical/sample/` contains a
`user_service.py` with a planted bug -- `get_display_name()` raises `TypeError`
when a user's `avatar` is `None`. Run the block below **from the attractor repo
root** and it works walk-up, no setup:

```bash
DOT="$PWD/examples/pipelines/practical/bug-fix.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-bugfix-demo
cd /tmp/attractor-bugfix-demo
attractor run "$DOT" \
    --param goal="Fix the bug in user_service.py: get_display_name() raises TypeError when a user's avatar is None. Reproduce it first, apply the minimal fix, and add a regression test that covers the None-avatar case." \
    --cwd .
```

We copy the sample to a temp dir first so the committed fixture stays pristine
and every run starts clean. `$DOT` captures the pipeline's absolute path before
`cd`, because the `.dot` path is resolved from your current directory while
`--cwd` is where the pipeline reads and writes. Process cwd must equal `--cwd`
for box-node (agent) pipelines -- that's why we `cd` into the copy (see
`modules/pipeline-runner/KNOWN_ISSUES.md`).

**Point it at your own repo instead:** replace the `cp`/`cd` with `cd /path/to/your/repo`, keep `$DOT` absolute, keep `--cwd .`, and swap in your bug.

**Verify the result:** `cd /tmp/attractor-bugfix-demo && pytest -v` -- the suite goes from 2 passing to 3 (the added None-avatar regression test), and `get_display_name` now handles the missing avatar.

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
