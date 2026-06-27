# 10 - Full Attractor (Kitchen Sink)

## What This Exercises

This is a realistic "build a feature" pipeline that exercises **every Attractor feature** together.

### Features Covered

| Feature | Where Used |
|---------|-----------|
| **Linear traversal** | start -> plan, integrate -> test, final_review -> review_gate |
| **Conditional routing** | test_gate (diamond) with `outcome=success` / `outcome!=success` |
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
| **Edge conditions** | `outcome=success` and `outcome!=success` on test_gate edges |
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
plan (.planning, truncate fidelity, o3 model)
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
test_gate (diamond)
  |                  |
  | [Pass]           | [Fail]
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
| `plan` | planning | `.planning` (specificity=2) | o3 (openai, high) |
| `implement_backend` | code | `.code` (specificity=2) | claude-sonnet-4-6 (anthropic) |
| `implement_frontend` | code | `.code` (specificity=2) | claude-sonnet-4-6 (anthropic) |
| `integrate` | code | `.code` (specificity=2) | claude-sonnet-4-6 (anthropic) |
| `test` | fast | `.fast` (specificity=2) | gemini-2.5-flash-preview-05-20 (gemini, low) |
| `fix_tests` | code | `.code` (specificity=2) | claude-sonnet-4-6 (anthropic) |
| `final_review` | code | `#final_review` (specificity=3) | claude-opus-4-20250514 (anthropic, high) |
| `polish` | code | `.code` (specificity=2) | claude-sonnet-4-6 (anthropic) |

## Expected Behavior

### Happy Path
1. `plan` creates the implementation plan (o3 with high reasoning, truncate fidelity)
2. `parallel_impl` fans out to 2 branches:
   - `implement_backend` runs with full fidelity on thread "backend-impl"
   - `implement_frontend` runs with full fidelity on thread "frontend-impl"
3. `collect` (fan-in) consolidates results, selects best candidate
4. `integrate` connects the pieces (compact fidelity from graph default)
5. `test` runs the test suite (gemini-flash for speed)
6. `test_gate` routes based on outcome:
   - SUCCESS -> `final_review` (condition match, weight=10)
7. `final_review` performs comprehensive review (claude-opus, summary:high)
8. `review_gate` presents choices to human:
   - `[S] Ship it!` -> done
9. Pipeline completes with all goal gates satisfied

### Test Failure Loop
At `test_gate`, if outcome != success:
- Routes to `fix_tests` (summary:high fidelity for detailed failure context)
- `fix_tests` loops back to `test`
- Cycle repeats until tests pass

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

## How to Run

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
4. **Conditional routing**: test_gate edges evaluated, correct branch taken
5. **Human gate**: review_gate presents 3 options with accelerator keys
6. **Goal gates**: At exit, both implementation nodes checked for success
7. **Variable expansion**: All `$goal` references replaced with the graph goal
8. **Thread reuse**: backend-impl and frontend-impl threads maintain session continuity
9. **Checkpoint**: Full state serialized after each node, including parallel results
10. **Event stream**: Full lifecycle of events from pipeline:start through pipeline:complete
