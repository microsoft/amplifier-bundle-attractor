# PR Review Pipeline

Multi-dimensional pull request review with parallel analysis streams.

## Usage

```bash
attractor run examples/pipelines/practical/pr-review.dot \
    --param goal="Review the changes on this branch for quality and security" \
    --cwd .
```

Run from the root of your repo, checked out on the feature branch you want reviewed: the pipeline reviews `git diff main...HEAD`, so it needs a `main` branch to diff against and a non-empty diff. If your default branch isn't `main` (or you're sitting on `main`), edit the `git diff` command in `pr-review.dot`. `--cwd .` is where box-node agents read and write (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

Or via the interactive agent:
> "Run the PR review pipeline on the current branch"

## What It Does

1. **Analyze Diff** -- Reads `git diff main...HEAD` and summarizes changes
2. **Parallel Review** -- Simultaneously checks for bugs, style issues, security vulnerabilities, and performance problems
3. **Prioritize** -- Ranks all findings by severity (must-fix -> should-fix -> consider)
4. **Generate Comments** -- Creates actionable PR review comments with file paths and suggested fixes

## Models

Model-agnostic -- every node runs on your configured default provider/model. To route the reasoning-heavy `prioritize` step to a stronger model, add a `model_stylesheet` and tag the node with a class (see `examples/pipelines/06-model-stylesheet.dot`).

## Expected Behavior

- Wall-clock time: roughly the same as a single review (4 reviews run in parallel)
- Output: Markdown checklist of prioritized findings with file:line references
- The `goal_gate` on `generate_comments` ensures the pipeline won't exit without producing output
