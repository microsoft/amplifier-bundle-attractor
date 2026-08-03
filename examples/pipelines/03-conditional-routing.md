# 03 - Conditional Routing Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism:
> evidence-based conditional routing (`context.tool.last_line`) from a
> parallelogram gate, plus edge weights — the same routing idiom Tutorial 00's
> gate uses. For the canonical attractor shape used in real work, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

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

- **Parallelogram (tool) gate**: `shape=parallelogram` + `tool_command` runs the real verifier (pytest) and prints a routing token (`pass`/`fail`) as its last stdout line
- **Evidence-based routing**: Edges condition on `context.tool.last_line=pass` / `context.tool.last_line=fail` -- the routing token written by the gate's tool command
- **Why not `shape=diamond` + `outcome=`**: `ConditionalHandler` (the diamond handler) unconditionally returns SUCCESS, overwriting whatever the upstream node produced. A `condition="outcome=success"` edge from a diamond is always true; `condition="outcome!=success"` is always false. Those edges are dead. The correct pattern is a parallelogram that runs the real verifier.
- **Edge weights**: When multiple condition-matched edges are eligible, higher `weight` wins
- **Retry loop**: The `fix -> test` edge creates a cycle for iterative fixing

## The Routing Pattern Explained

```dot
// WRONG (dead edges -- ConditionalHandler always returns SUCCESS):
gate [shape=diamond, label="Tests Pass?"]
gate -> done [condition="outcome=success"]   // always fires
gate -> fix  [condition="outcome!=success"]  // never fires

// RIGHT (evidence-based -- routes on what actually happened):
gate [shape=parallelogram, label="Tests Pass?",
      tool_command="pytest -q ... && printf pass || printf fail"]
gate -> done [condition="context.tool.last_line=pass"]
gate -> fix  [condition="context.tool.last_line=fail"]
```

Key facts:
- `shape=parallelogram` maps to the `tool` handler, which runs `tool_command` as a shell command
- The last non-empty stdout line is stored in `context["tool.last_line"]` automatically
- Route on `context.tool.last_line`, never on `tool.output` (full stdout never matches a condition exactly)
- A diamond is only correct when routing on `context.*` keys set by *earlier* nodes (e.g., a `report_outcome` `preferred_label`) -- never on `outcome=` of the node before it

## Pipeline Structure

```
start --> implement --> test --> gate --[pass]--> done
                        ^        |
                        |        +--[fail]--> fix
                        |                     |
                        +---------------------+
```

## Expected Behavior

### Happy Path (tests pass)
1. `implement` writes the URL shortener code and tests -> SUCCESS
2. `test` (LLM) runs tests and reports results -> SUCCESS
3. `gate` (parallelogram) runs `pytest -q test_url_shortener.py` directly:
   - All tests pass -> exits 0 -> prints "pass" -> `context.tool.last_line = "pass"`
4. Edge selection: `gate -> done` condition `context.tool.last_line=pass` matches -> **done**

### Fix Path (tests fail)
1. `implement` writes code -> SUCCESS
2. `test` (LLM) runs tests -> SUCCESS (LLM nodes return SUCCESS unless the backend crashes)
3. `gate` (parallelogram) runs pytest directly:
   - Tests fail -> exits non-0 -> prints "fail" -> `context.tool.last_line = "fail"`
4. Edge selection: `gate -> fix` condition `context.tool.last_line=fail` matches -> **fix loop**
5. `fix` repairs the implementation, then loops back to `test`

### Key Insight: Route on Evidence, Not Opinion
The gate runs the real verifier. The routing token (`pass`/`fail`) is the literal exit code of pytest, not an LLM's assessment of whether tests passed. This is why the loop actually fires when tests fail -- the gate observes what happened and routes accordingly.

## What to Look For

- `gate/output.txt` shows `pass` or `fail` on the last line (the routing token)
- Edge selection logs show `condition="context.tool.last_line=pass"` matched (or `=fail` matched)
- If the happy path is taken: execution goes `gate -> done`
- If the fix path is taken: `fix/` directory appears in logs, then another `test/` execution
- The cycle is real: `fix -> test -> gate -> fix` will iterate until tests pass
