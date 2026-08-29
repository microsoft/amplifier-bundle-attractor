# 07 - Fidelity Modes Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism: fidelity
> attributes (`full`, `summary`, `none`) controlling how much prior context
> each node carries. For the canonical attractor shape used in real work,
> start with [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/07-fidelity-modes.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
dot-runner run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Fidelity attribute on nodes**: Each node explicitly sets how much prior context to carry
- **Fidelity attribute on edges**: The edge from `integration_test -> final_review` overrides the node's fidelity
- **`thread_id` for session reuse**: `implement_auth` and `implement_rate_limit` share `thread_id="api-impl"`, meaning they reuse the same LLM session under `full` fidelity
- **All fidelity modes**: truncate, full, compact (graph default), summary:medium, summary:high
- **Fidelity resolution precedence**: edge fidelity (highest) > node fidelity > graph `default_fidelity` > system default ("compact")

## Pipeline Structure

```
start -> architect -> implement_auth -> implement_rate_limit -> implement_logging -> integration_test -> final_review -> done
         (truncate)   (full, thread    (full, thread            (summary:medium)     (compact, from      (edge overrides
                       ="api-impl")     ="api-impl")                                  graph default)      to summary:high)
```

## Fidelity Resolution for Each Node

| Node | Node Fidelity | Edge Fidelity | Graph Default | **Resolved** | Session |
|------|--------------|---------------|---------------|-------------|---------|
| `architect` | `truncate` | -- | `compact` | **truncate** | Fresh (goal + run ID only) |
| `implement_auth` | `full` | -- | `compact` | **full** | Reused (thread "api-impl") |
| `implement_rate_limit` | `full` | -- | `compact` | **full** | Reused (thread "api-impl") |
| `implement_logging` | `summary:medium` | -- | `compact` | **summary:medium** | Fresh (~1500 token summary) |
| `integration_test` | (none) | -- | `compact` | **compact** | Fresh (bullet-point summary) |
| `final_review` | `compact` | `summary:high` | `compact` | **summary:high** | Fresh (~3000 token summary) |

Key: Edge fidelity on `integration_test -> final_review` overrides `final_review`'s own `fidelity="compact"`.

## Context Preamble Content by Mode

| Mode | Content |
|------|---------|
| `truncate` | "Goal: Build a REST API...\nRun ID: ..." |
| `full` | Full conversation history preserved in LLM session |
| `compact` | Bullet-point list: completed stages, outcomes, context.* values |
| `summary:medium` | Stage outcomes with notes, active context.* values (~1500 tokens) |
| `summary:high` | Detailed outcomes with failures, context updates (~3000 tokens) |

## Thread Reuse Behavior

`implement_auth` and `implement_rate_limit` both use `thread_id="api-impl"` with `fidelity="full"`:
- `implement_auth` starts a new LLM session with key "api-impl"
- `implement_rate_limit` **reuses** that same session -- it sees the full conversation from `implement_auth`
- This is powerful for multi-step implementations where each step builds on the previous one's context

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the fidelity modes pipeline"
    context:
      pipeline_path: "examples/pipelines/07-fidelity-modes.dot"
```

## What to Look For

- `architect/prompt.md` should have minimal preamble (truncate mode)
- `implement_auth` and `implement_rate_limit` should share a session thread
- `implement_logging/prompt.md` should have a medium-detail summary preamble
- `integration_test/prompt.md` should have a compact bullet-point preamble
- `final_review/prompt.md` should have a high-detail summary (edge override worked)
- Checkpoint resume: if the pipeline resumes from checkpoint, `full` fidelity degrades to `summary:high` for the first resumed node (session state can't be serialized)
