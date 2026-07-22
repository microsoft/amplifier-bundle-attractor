# 03 - Conditional Routing Pipeline

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/03-conditional-routing.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Diamond handler**: `shape=diamond` resolves to the `conditional` handler -- a no-op that returns SUCCESS, letting the engine handle routing via edge conditions
- **Edge conditions**: `condition="outcome=success"` and `condition="outcome!=success"` are evaluated against the context after the `test` node completes
- **Condition expression language**: The `=` (equals) and `!=` (not equals) operators, with `outcome` as a built-in variable
- **Edge weights**: When multiple condition-matched edges are eligible, higher `weight` wins
- **Retry loop**: The `fix -> test` edge creates a cycle for iterative fixing

## Pipeline Structure

```
start --> implement --> test --> gate --[success]--> done
                        ^       |
                        |       +--[fail]--> fix
                        |                     |
                        +---------------------+
```

## Expected Behavior

### Happy Path (tests pass)
1. `implement` writes the URL shortener code -> SUCCESS
2. `test` runs tests -> SUCCESS (sets `outcome=success` in context)
3. `gate` handler returns SUCCESS (no-op)
4. Edge selection evaluates conditions on `gate`'s outgoing edges:
   - `gate -> done`: condition `outcome=success` -- the context has `outcome=success` from the `test` node -> **TRUE**
   - `gate -> fix`: condition `outcome!=success` -> **FALSE**
   - Engine picks `gate -> done` (condition matched, weight=10)
5. Pipeline exits successfully

### Fix Path (tests fail)
1. `implement` writes code -> SUCCESS
2. `test` runs tests -> SUCCESS (but the *test* node's handler returned success even though the tests it ran may have found issues)
   - Note: In practice, the codergen handler always returns SUCCESS unless the backend fails. The *outcome* reflects handler execution, not the semantic meaning of the tests. For real conditional routing, you'd need a backend that returns FAIL when tests fail, or use context values like `context.tests_passed=false`.
3. If `test` returns with `outcome!=success`, the gate routes to `fix`
4. `fix` attempts repairs, then loops back to `test`

### Key Insight: How Conditions Work
The `gate` (diamond) node itself always succeeds. But the condition expressions on its outgoing edges look at the **context**, which still holds the `outcome` value set by the *previous* node (`test`). The conditional handler is a pass-through that preserves the routing context from the preceding node.

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the conditional routing pipeline"
    context:
      pipeline_path: "examples/pipelines/03-conditional-routing.dot"
```

## What to Look For

- `gate/status.json` shows `"outcome": "success"` (the conditional handler itself always succeeds)
- Edge selection logs show which condition matched and which edge was taken
- If the happy path is taken: `checkpoint.json` shows `gate` followed by `done`
- If the fix path is taken: `fix/` directory appears in logs, then another `test/` execution
- Weights on edges: if both conditions somehow matched, weight=10 beats weight=5
