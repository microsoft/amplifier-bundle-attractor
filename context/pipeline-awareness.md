# Pipeline Capabilities

You have access to the `run_pipeline` tool which can execute DOT graph pipelines.

## Critical: run_pipeline is SYNCHRONOUS

`run_pipeline` is a **synchronous** tool. When it returns, the pipeline is **fully
complete**. Do NOT call any of these after a pipeline run:
- `wait` — the pipeline is already done
- `close_agent` — the pipeline session is already closed
- `send_input` — there is no pending pipeline to send input to
- Any polling or status-check tool

When `run_pipeline` returns its result, simply read the result and respond to the
user with a summary of what the pipeline accomplished.

## When to Use Pipelines

Use `run_pipeline` when the user asks you to:
- Run a pipeline or workflow defined in a `.dot` file
- Execute a multi-step coding pipeline
- Run an Attractor pipeline

For simple tasks (1-2 straightforward steps), just do the work directly — no
pipeline needed.

## Pipeline Decision Heuristic

When the user asks you to do a complex task, decide:

1. **Simple task (1-2 steps, no branching)** — Just do it directly. No pipeline.
   Example: "Add a docstring to this function" or "Fix the typo in README.md"

2. **Medium task (2-4 ordered steps)** — Generate an inline pipeline with `dot_source`.
   Example: "Refactor the auth module" becomes a plan -> implement -> test pipeline.

3. **Complex task (branches, review loops, parallel work, quality gates)** — Generate
   a full pipeline with conditional routing, retries, or parallel fan-out.
   Example: "Build a comprehensive test suite for 3 modules" uses parallel fan-out.

When generating a pipeline, refer to the DOT Reference Card (loaded in your context)
for the available node shapes, attributes, and patterns.

## How to Use

Call `run_pipeline` with:
- **`goal`** (required): The task description. This replaces `$goal` in node prompts.
- **`dot_file`** (optional): Path to a `.dot` file. Supports `@attractor:` mentions.
- **`dot_source`** (optional): Inline DOT digraph string.
- **`params`** (optional): Key-value pairs for `$param` expansion in node prompts.

You must provide either `dot_file` or `dot_source`.

## Examples

Run a pipeline from a file:
```json
{
  "goal": "Refactor the authentication module to use async patterns",
  "dot_file": "@attractor:examples/pipelines/02-plan-implement-test.dot"
}
```

Run a simple inline pipeline:
```json
{
  "goal": "Add input validation to the user registration endpoint",
  "dot_source": "digraph { start [shape=Mdiamond]; implement [prompt=\"$goal\"]; test [prompt=\"Write tests for the changes\"]; done [shape=Msquare]; start -> implement -> test -> done }"
}
```

## Available Example Pipelines

- `@attractor:examples/pipelines/01-simple-linear.dot` — Minimal start -> implement -> done
- `@attractor:examples/pipelines/02-plan-implement-test.dot` — Plan, implement, test cycle
- `@attractor:examples/pipelines/03-conditional-routing.dot` — Conditional branching based on outcomes
- `@attractor:examples/pipelines/04-retry-with-fallback.dot` — Retry logic with fallback paths
- `@attractor:examples/pipelines/05-parallel-fan-out.dot` — Parallel execution with fan-in
- `@attractor:examples/pipelines/06-model-stylesheet.dot` — Multi-provider model selection

### Practical Pipelines

- `@attractor:examples/pipelines/practical/pr-review.dot` — Parallel multi-dimension PR review
- `@attractor:examples/pipelines/practical/test-gen.dot` — Test generation with validation loop
- `@attractor:examples/pipelines/practical/bug-fix.dot` — Systematic reproduce -> diagnose -> fix -> verify
- `@attractor:examples/pipelines/practical/feature-build.dot` — Parallel implementation with human review gate
- `@attractor:examples/pipelines/practical/refactor.dot` — Safe refactoring with snapshot tests

## After a Pipeline Completes

When `run_pipeline` returns, the result contains:
- `status` — "success", "partial_success", or "fail"
- `notes` — Summary of what was accomplished
- `duration_seconds` — How long it took
- `nodes_completed` — How many pipeline stages ran
- `message` — Confirmation that the pipeline is complete

Read the result and tell the user what happened. Do NOT call any follow-up tools
related to the pipeline — it is already complete.

## Authoring or editing a pipeline? Consult attractor-expert FIRST

Before handing any `.dot` authoring/editing — or any "build an LLM workflow" task —
to a generic builder (modular-builder, a self-spawn, or inline Python), delegate to
`attractor:attractor-expert` for BOTH the design and the authoring. Generic builders
carry **no** attractor engine runtime semantics and will re-discover the foot-guns the
hard way (routing on `tool.output` vs `last_line`, missing FAIL edges, prose-vs-JSON
verdicts, `tool_command` CWD, folder checkpoint reuse). The engine's actual runtime
behavior — including where it diverges from the spec prose — is in
`@attractor:context/engine-semantics.md`.

## Deep Questions

For deep pipeline design questions, DOT syntax details, debugging pipeline
failures, or programmatic integration, delegate to `attractor:attractor-expert`.
