# 05 - Parallel Fan-Out Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism: parallel
> fan-out via `shape=component` and fan-in via `shape=tripleoctagon`. For the
> canonical attractor shape used in real work, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/05-parallel-fan-out.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Parallel handler** (`shape=component`): Fans out to multiple branches concurrently
- **Fan-in handler** (`shape=tripleoctagon`): Consolidates parallel results and selects the best candidate
- **`join_policy="wait_all"`**: All branches must complete before fan-in proceeds
- **`error_policy="continue"`**: If one branch fails, other branches still run to completion
- **`max_parallel=3`**: Bounds concurrent execution to 3 branches (matches our branch count)
- **Isolated branch contexts**: Each branch gets a `context.clone()` -- changes in one branch don't affect others
- **`parallel.results` in context**: The parallel handler stores branch results for the fan-in handler to consume

## Pipeline Structure

```
                          +--> test_arithmetic --+
                          |                      |
start -> plan -> parallel +--> test_trig --------+--> collect_results -> summarize -> done
                          |                      |
                          +--> test_stats -------+
```

## Expected Behavior

1. `plan` creates the test plan -> SUCCESS
2. `parallel_tests` handler activates:
   - Identifies 3 outgoing edges (fan-out branches)
   - Clones context for each branch (isolation)
   - Creates asyncio semaphore with `max_parallel=3`
   - Emits `pipeline:parallel:started` event with `branch_count=3`
   - Executes all 3 branches concurrently
   - Each branch emits `pipeline:parallel:branch:started` and `pipeline:parallel:branch:completed`
   - Stores results in `context["parallel.results"]` as a list of dicts
   - Evaluates `wait_all` policy: all 3 must complete (SUCCESS if none failed, PARTIAL_SUCCESS if any failed)
3. `collect_results` (fan-in) handler:
   - Reads `parallel.results` from context
   - Ranks candidates by status (SUCCESS > PARTIAL_SUCCESS > RETRY > FAIL)
   - Records winner in `parallel.fan_in.best_id` and `parallel.fan_in.best_status`
4. `summarize` creates a unified report
5. Pipeline completes

### Join Policy Variations

The canonical spec defines exactly two join policies (§4.8,
[`specs/canonical/attractor-spec-canonical.md`](../../specs/canonical/attractor-spec-canonical.md)):

| Policy | Behavior |
|--------|----------|
| `wait_all` | All branches complete. SUCCESS if none failed, PARTIAL_SUCCESS otherwise |
| `first_success` | Returns as soon as one branch succeeds. Others may be cancelled |

This engine also accepts two **non-canonical extensions**. Upstream removed both from
the spec at `fb57a55`, and no shipped graph in this repo uses either -- they are recorded
as subtraction candidates in [`specs/EXTENSIONS.md`](../../specs/EXTENSIONS.md) §18. They
still work; reach for them only when the two canonical policies genuinely cannot express
the join, and expect a `.dot` that uses them to be non-portable to a spec-only runtime:

| Policy (extension) | Behavior |
|--------|----------|
| `k_of_n` | At least `min_success` branches must succeed (set via node attribute) |
| `quorum` | At least `quorum_fraction` (e.g., 0.5) of branches must succeed |

### Error Policy Variations

`error_policy` is likewise a non-canonical extension (removed upstream at `fb57a55`;
`specs/EXTENSIONS.md` §18) -- but unlike `k_of_n`/`quorum` it is in live use across this
repo's own graphs, including the one above:

| Policy | Behavior |
|--------|----------|
| `continue` | All branches run to completion regardless of failures |
| `fail_fast` | Cancel remaining branches on first failure |
| `ignore` | Filter out failed branches from results entirely |

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the parallel fan-out pipeline"
    context:
      pipeline_path: "examples/pipelines/05-parallel-fan-out.dot"
```

## What to Look For

- `parallel:started` event with `branch_count=3`
- Three `parallel:branch:completed` events (one per branch)
- `parallel:completed` event with `success_count` and `failure_count`
- Context contains `parallel.results` (list of 3 result dicts)
- Context contains `parallel.fan_in.best_id` after fan-in
- Each branch's log directory has its own `prompt.md`, `response.md`, `status.json`
- Branch contexts are isolated -- changes in `test_arithmetic` don't appear in `test_trig`
