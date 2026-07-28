# 09 - Manager Supervisor Pipeline

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/09-manager-supervisor.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Manager loop handler** (`shape=house`): Orchestrates an observe/evaluate/act cycle over a child subgraph
- **`manager.max_cycles=5`**: The manager will attempt at most 5 cycles before giving up
- **`manager.stop_condition`**: Custom condition expression evaluated each cycle (`outcome=success`)
- **`manager.poll_interval`**: Delay between cycles (`"0s"` for immediate re-run, use `"45s"` for real polling)
- **`manager.actions`**: Comma-separated action list -- `observe` (run child), `steer` (inject feedback), `wait` (delay between cycles)
- **Subgraph execution**: The manager uses `subgraph_runner` to execute a sub-pipeline starting from its first outgoing edge target
- **Child context cloning**: Each cycle gets an isolated context clone
- **Steering injection**: When `steer` is in actions and a previous cycle failed, the manager injects `manager.steering` into the child context with failure details
- **Cycle telemetry**: Context is updated with `manager.cycle_N.status`, `manager.last_child_status`, `manager.cycles_completed`
- **Evidence-based gate**: The child's internal `gate` uses `shape=parallelogram` + `tool_command` to run pytest and route on `context.tool.last_line` -- not a diamond routing on `outcome=`. See routing pattern explanation below.

## Pipeline Structure

```
start -> plan -> manager -> report -> done
                   |
                   v (child subgraph, run each cycle)
                 implement -> test -> gate --[pass]--> done (child exits)
                   ^                  |
                   +---[fail]---------+
```

## Routing Pattern: Evidence Gate vs. Diamond

The child's `gate` node uses `shape=parallelogram` (tool gate), not `shape=diamond` (conditional gate):

```dot
// WRONG (dead edges -- ConditionalHandler always returns SUCCESS):
gate [shape=diamond, label="Tests Pass?"]
gate -> done      [condition="outcome=success"]   // always fires
gate -> implement [condition="outcome!=success"]  // never fires

// RIGHT (evidence-based -- routes on what actually happened):
gate [shape=parallelogram, label="Tests Pass?",
      tool_command="pytest -q > /dev/null 2>&1 && printf pass || printf fail"]
gate -> done      [condition="context.tool.last_line=pass"]
gate -> implement [condition="context.tool.last_line=fail"]
```

`ConditionalHandler` (the diamond handler) unconditionally returns SUCCESS, overwriting the upstream node's outcome. A `condition="outcome!=success"` edge from a diamond is always false -- the loop never fires. The parallelogram gate runs the real verifier and routes on observed evidence.

## Two-Level Retry Structure

This pipeline has two levels of retry:

1. **Child-level loop**: `gate` routes `implement -> test -> gate` on failure. The child can loop multiple times within a single manager cycle.
2. **Manager-level retry**: If the child subgraph fails overall, the manager starts a new cycle (up to `max_cycles=5`) with steering context injected.

The child's evidence gate (level 1) handles within-cycle test failures. The manager's outer loop (level 2) handles cross-cycle retry with LLM steering guidance.

## Expected Behavior

### Cycle 1: First Attempt
1. `plan` creates the implementation plan -> SUCCESS
2. `manager` handler starts:
   - Reads config: max_cycles=5, poll_interval=0s, actions=[observe, steer, wait]
   - Identifies child start node: `implement` (first outgoing edge)
3. **OBSERVE**: Clones context, runs child subgraph from `implement`
   - Child executes: implement -> test -> gate
   - If gate routes to `done` (tests pass): child returns SUCCESS
   - If gate routes back to `implement` (tests fail): child continues looping within the subgraph
4. **EVALUATE**: Checks `outcome=success` against child outcome
   - If child succeeded: manager returns SUCCESS, pipeline continues to `report`
   - If child failed: proceed to next cycle

### Cycle 2+: Retry with Steering
1. **OBSERVE**: Clone context, inject steering:
   - `manager.steering = "Cycle 1 of 5 resulted in fail. Failure reason: ... Adjust your approach."`
2. Run child subgraph again with steering context available
3. **EVALUATE**: Check stop condition again
4. **ACT**: Wait `poll_interval` before next cycle (0s = immediate)

### Max Cycles Exhausted
If all 5 cycles fail, the manager returns FAIL with:
- `failure_reason: "Manager exhausted 5 cycle(s)"`
- `notes: "Last child status: fail"`

## Manager Actions

| Action | Behavior |
|--------|----------|
| `observe` | Run the child subgraph and collect its outcome |
| `steer` | Inject `manager.steering` context with failure details from previous cycle |
| `wait` | Sleep for `poll_interval` between cycles |

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the manager supervisor pipeline"
    context:
      pipeline_path: "examples/pipelines/09-manager-supervisor.dot"
```

## What to Look For

- Context keys after manager completes:
  - `manager.cycles_completed`: Number of cycles executed (1-5)
  - `manager.last_child_status`: Last child outcome ("success" or "fail")
  - `manager.cycle_1.status`, `manager.cycle_2.status`, etc.: Per-cycle outcomes
- In cycle 2+, child context contains `manager.steering` with previous failure details
- The manager's own status.json shows the aggregate outcome
- If max_cycles exhausted: `failure_reason` mentions the cycle count
- `report` node runs after manager completes (regardless of success/fail path)
