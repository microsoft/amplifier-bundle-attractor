# 10 - Full Attractor (Kitchen Sink)

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/10-full-attractor.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd . --on-human-gate auto-approve
```

`--on-human-gate auto-approve` always takes the gate's first option so the run completes non-interactively; drop it and run interactively if you want the gate to actually branch.

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

This is a realistic "build a feature" pipeline that exercises **every Attractor feature** together.

### Features Covered

| Feature | Where Used |
|---------|-----------|
| **Linear traversal** | start -> plan, integrate -> test, final_review -> review_gate |
| **Evidence-based routing** | test_gate (parallelogram) with `context.tool.last_line=pass` / `context.tool.last_line=fail` |
| **Parallel fan-out** | parallel_impl (component) -> backend + frontend branches |
| **Parallel fan-in** | collect (tripleoctagon) consolidates branch results |
| **Human gate** | review_gate (hexagon) with [S]/[P]/[R] accelerator keys |
| **Goal gates** | implement_backend and implement_frontend both have `goal_gate=true` |
| **Retry logic** | `max_retries=2` on both implementation nodes |
| **Retry targets** | Node-level `retry_target="plan"`, graph-level `fallback_retry_target="plan"` |
| **Model stylesheet** | 5 rules: `*`, `.planning`, `.code`, `.fast`, `#final_review` |
| **Fidelity modes** | truncate (plan), full (impl branches with thread_id), compact (default), summary:high (fix/review), summary:medium (polish) |
| **Thread IDs** | `thread_id="backend-impl"` and `thread_id="frontend-impl"` for session reuse |
| **$goal expansion** | Used in plan, implement_backend, implement_frontend prompts |
| **Edge weights** | Pass edge (weight=10) preferred over Fail edge (weight=5) |
| **Edge conditions** | `context.tool.last_line=pass` and `context.tool.last_line=fail` on test_gate edges |
| **Accelerator keys** | `[S] Ship it!`, `[P] Polish first`, `[R] Rework needed` |
| **Class attribute** | `.planning`, `.code`, `.fast` on various nodes |
| **Join policy** | `wait_all` on parallel_impl |
| **Error policy** | `continue` on parallel_impl |
| **Graph-level defaults** | `default_fidelity`, `default_max_retry`, `retry_target`, `fallback_retry_target` |

## Pipeline Structure

```
start
  |
  v
plan (.planning, truncate fidelity, gpt-[5-9]* model)
  |
  v
parallel_impl (component, wait_all, max_parallel=2)
  |              |
  v              v
implement_     implement_
backend        frontend
(full fidelity, (full fidelity,
 thread:backend  thread:frontend
 goal_gate)      goal_gate)
  |              |
  v              v
collect (tripleoctagon fan-in)
  |
  v
integrate (.code, compact fidelity)
  |
  v
test (.fast, gemini-flash)
  |
  v
test_gate (parallelogram -- runs pytest, routes on context.tool.last_line)
  |                  |
  | [pass]           | [fail]
  v                  v
final_review    fix_tests (summary:high)
(#id -> opus)       |
  |                  +-> test (loop)
  v
review_gate (hexagon)
  |          |            |
  | [S]      | [P]        | [R]
  v          v            v
done       polish      fix_tests
           (summary:    (loop)
            medium)
              |
              +-> final_review (loop)
```

## Model Assignment (After Stylesheet)

| Node | Class | Stylesheet Match | Resolved Model |
|------|-------|-----------------|----------------|
| `plan` | planning | `.planning` (specificity=2) | gpt-[5-9]* (openai, high) |
| `implement_backend` | code | `.code` (specificity=2) | claude-sonnet-* (anthropic) |
| `implement_frontend` | code | `.code` (specificity=2) | claude-sonnet-* (anthropic) |
| `integrate` | code | `.code` (specificity=2) | claude-sonnet-* (anthropic) |
| `test` | fast | `.fast` (specificity=2) | gemini-*-flash (gemini, low) |
| `fix_tests` | code | `.code` (specificity=2) | claude-sonnet-* (anthropic) |
| `final_review` | code | `#final_review` (specificity=3) | claude-opus-* (anthropic, high) |
| `polish` | code | `.code` (specificity=2) | claude-sonnet-* (anthropic) |

> These are evergreen glob ids resolved against each provider's live model list at
> run time. See [06-model-stylesheet.md](06-model-stylesheet.md) for how the forms
> stay current across generations (and why OpenAI uses the `gpt-[5-9]*` range).

## Expected Behavior

### Happy Path
1. `plan` creates the implementation plan (gpt-[5-9]* with high reasoning, truncate fidelity)
2. `parallel_impl` fans out to 2 branches:
   - `implement_backend` runs with full fidelity on thread "backend-impl"
   - `implement_frontend` runs with full fidelity on thread "frontend-impl"
3. `collect` (fan-in) consolidates results, selects best candidate
4. `integrate` connects the pieces (compact fidelity from graph default)
5. `test` runs the test suite (gemini-flash for speed)
6. `test_gate` (parallelogram) runs `pytest -q` directly and routes on the result:
   - Tests pass -> prints "pass" -> `context.tool.last_line=pass` -> `final_review` (weight=10)
7. `final_review` performs comprehensive review (claude-opus, summary:high)
8. `review_gate` presents choices to human:
   - `[S] Ship it!` -> done
9. Pipeline completes with all goal gates satisfied

### Test Failure Loop
At `test_gate` (parallelogram), if pytest exits non-0:
- Prints "fail" -> `context.tool.last_line=fail` -> routes to `fix_tests`
- `fix_tests` (summary:high fidelity for detailed failure context) loops back to `test`
- `test` runs again -> `test_gate` re-runs pytest -> cycle repeats until tests pass

**Why parallelogram, not diamond**: `ConditionalHandler` (the diamond handler) unconditionally
returns SUCCESS, so `condition="outcome!=success"` from a diamond is always false -- the loop
never fires. The parallelogram gate runs the real verifier and routes on observed evidence.

### Human Rejection Loops
At `review_gate`:
- `[P] Polish first` -> polish -> final_review -> review_gate (polish loop)
- `[R] Rework needed` -> fix_tests -> test -> test_gate -> ... (full rework)

### Goal Gate Enforcement
When reaching `done`:
- Engine checks `implement_backend` (goal_gate=true) -- must be SUCCESS
- Engine checks `implement_frontend` (goal_gate=true) -- must be SUCCESS
- If either failed: engine jumps to their `retry_target="plan"` for a fresh attempt
- Graph-level `fallback_retry_target="plan"` provides a last resort

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Build the user notifications feature"
    context:
      pipeline_path: "examples/pipelines/10-full-attractor.dot"
      # Use "console" for interactive human gates, "auto" for CI
      interviewer: "auto"
```

## What to Look For

1. **Stylesheet application**: Check that node attrs contain the correct model after initialization
2. **Parallel execution**: Two branch directories in logs, `parallel.results` in context
3. **Fidelity preambles**: Compare prompt.md content across nodes with different fidelity modes
4. **Evidence-based routing**: test_gate (parallelogram) runs pytest; `context.tool.last_line` holds `pass` or `fail`; correct branch taken
5. **Human gate**: review_gate presents 3 options with accelerator keys
6. **Goal gates**: At exit, both implementation nodes checked for success
7. **Variable expansion**: All `$goal` references replaced with the graph goal
8. **Thread reuse**: backend-impl and frontend-impl threads maintain session continuity
9. **Checkpoint**: Full state serialized after each node, including parallel results
10. **Event stream**: Full lifecycle of events from pipeline:start through pipeline:complete
