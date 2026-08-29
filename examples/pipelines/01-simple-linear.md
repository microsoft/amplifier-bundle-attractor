# 01 - Simple Linear Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism: linear
> traversal with start/exit node detection and basic DOT parsing. For the
> canonical attractor shape used in real work, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/01-simple-linear.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
dot-runner run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Basic DOT parsing**: digraph with graph-level attributes, node declarations, edge chains
- **Start/exit node detection**: `shape=Mdiamond` for start, `shape=Msquare` for exit
- **Linear traversal**: Single path from start to exit with no branching
- **Codergen handler**: The default `box` shape resolves to the codergen (LLM task) handler
- **Variable expansion**: `$goal` in the prompt is replaced with the graph-level `goal` attribute
- **Logging**: prompt.md and response.md are written to the node's log directory

## Pipeline Structure

```
start --> implement --> done
```

## Expected Behavior

1. Engine finds the start node (`shape=Mdiamond`) and begins there
2. Start handler returns SUCCESS immediately (no-op)
3. Edge selection picks the only outgoing edge: `start -> implement`
4. Codergen handler:
   - Expands `$goal` in the prompt to "Create a simple Python script that prints hello world"
   - Writes `implement/prompt.md` to the logs directory
   - Calls the backend (or produces a simulated response)
   - Writes `implement/response.md` and `implement/status.json`
   - Returns SUCCESS with `context_updates: {last_stage: "implement", last_response: "..."}`
5. Edge selection picks the only outgoing edge: `implement -> done`
6. Exit handler returns SUCCESS
7. No goal gates exist, so pipeline completes successfully

## Or run from a bundle / recipe

```yaml
# In your bundle YAML or recipe step:
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the simple linear pipeline"
    context:
      pipeline_path: "examples/pipelines/01-simple-linear.dot"
```

Or programmatically:

```python
from amplifier_module_loop_pipeline import parse_dot, PipelineEngine

dot_source = open("examples/pipelines/01-simple-linear.dot").read()
graph = parse_dot(dot_source)
# ... create context, registry, engine and run
```

## What to Look For

- `manifest.json` in the logs root with graph name and goal
- `implement/prompt.md` contains the expanded prompt (no literal `$goal`)
- `implement/response.md` contains the LLM response (or `[Simulated]` text)
- `implement/status.json` shows `"outcome": "success"`
- `checkpoint.json` shows `"current_node": "done"` and `"completed_nodes"` includes both `start` and `implement`
- Pipeline outcome is SUCCESS
