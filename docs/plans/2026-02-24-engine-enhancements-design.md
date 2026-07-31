# Attractor Engine Enhancements Design

> **Historical document (2026-02-24).** The multi-edge parallel fan-out
> described below (enhancement 6) shipped and was later RETIRED: T0-4
> restored spec §3.3 single-best-edge selection, and `shape=component` +
> `ParallelHandler` is now the only fan-out mechanism. Kept for history.

## Goal

Implement 6 enhancements to make the attractor pipeline engine production-ready for real-world DOT files like `semport.dot` (9 nodes, multi-provider, loop-restart) and `consensus_task.dot` (17 nodes, parallel fan-out, consensus).

## Background

The attractor pipeline engine can parse and execute DOT-defined pipelines, but several attributes defined in the NLSpec and used in real-world DOT files are not yet wired through. Additionally, the dashboard server lacks submission endpoints for programmatic pipeline execution. These enhancements are driven by the NLSpec and validated against real DOT files from `~/dev`.

Key context:
- The NLSpec defines `reasoning_effort`, `allow_partial`, `loop_restart`, parallel execution, and HTTP server mode
- The `type` attribute (not `node_type`) drives handler dispatch per the NLSpec; `node_type` is only for start/exit identification
- `max_agent_turns` is a new node-level attribute not in the NLSpec by that name (the spec uses `max_turns` at session scope)

## Items Overview

| # | Item | Spec Reference | Complexity |
|---|------|---------------|-----------|
| 1 | `node_type` handler dispatch clarification | Attractor 2.6, 2.8 | None needed (already works via `type` attr) |
| 2 | `reasoning_effort` passthrough | Attractor 2.6, Coding Agent Loop 153 | Small |
| 3 | `max_agent_turns` passthrough | New node-level attribute | Small |
| 4 | `allow_partial` in retry logic | Attractor 2.6, 508 | Small |
| 5 | `loop_restart` edge attribute | Attractor 2.7, 388-390 | Medium |
| 6 | Parallel execution via multi-edge fan-out | Attractor 4.8-4.9 | Medium |
| 7 | HTTP server mode | Attractor 9.5 | Large |

---

## Section 1: Node Attribute Passthrough (Items 1-4)

Items 1-4 share a common pattern: the DOT file specifies an attribute, and the engine needs to pass it through to the handler/backend or act on it in the retry logic.

### Current Data Flow

```
DOT node attrs -> parse_dot() -> Node object -> engine -> handler -> backend.run(node, prompt, context)
```

The `Node` object already carries all attributes in `node.attrs`. The `CodergenHandler` already passes the full `Node` to `backend.run()`. The attributes ARE available to the backend -- they just aren't being read or acted upon.

### Item 1: `node_type` for Handler Dispatch -- NO CHANGE NEEDED

The NLSpec uses the `type` attribute (not `node_type`) for handler dispatch. The engine's current dispatch resolution order:

1. `node.type` -- the DOT `type=` attribute (explicit override, highest priority)
2. `SHAPE_TO_HANDLER.get(node.shape, "codergen")` -- shape-based lookup, default codergen

`node_type` is a SEPARATE mechanism used ONLY for start/exit node identification (landed in commit b1ba750).

**Clarification for real-world DOT files:** Files that use `node_type="stack.observe"` or `node_type="stack.steer"` should map these to the `type` attribute during DOT parsing, OR the engine should check `node_type` as a fallback. The recommended approach is to add `node_type` as a third fallback in handler dispatch resolution:

1. `node.type` (highest priority)
2. `node.attrs.get("node_type")` (NEW -- fallback for real-world DOT files)
3. `SHAPE_TO_HANDLER.get(node.shape, "codergen")` (lowest priority)

### Item 2: `reasoning_effort` Passthrough

**NLSpec:** Node attribute, default `"high"`, options: `low | medium | high`. Maps to:
- OpenAI: reasoning token budget
- Anthropic: thinking budget
- Gemini: thinkingConfig

**Change needed:** `DirectProviderBackend` and `AmplifierBackend` need to read `node.attrs["reasoning_effort"]` and pass it to the LLM client's `generate()` call.

- In `DirectProviderBackend`: when calling `unified_llm.Client.generate()`, pass `reasoning_effort=node.attrs.get("reasoning_effort", "high")` as a parameter.
- In `AmplifierBackend`: when spawning a child session, include `reasoning_effort` in the spawn config so the child session's LLM calls use it.

### Item 3: `max_agent_turns` Passthrough

Not in the NLSpec by this name -- the spec uses `max_turns` at session scope. `max_agent_turns` is a node-level attribute used in real-world DOT files (`semport.dot`, `consensus_task.dot`) to limit the agent loop iterations per node.

**Change needed:** The backend reads `node.attrs.get("max_agent_turns")` and passes it to the agent session config as `max_turns`.

- In `DirectProviderBackend`: limits the internal LLM call loop.
- In `AmplifierBackend`: passed to the spawn config.

### Item 4: `allow_partial` in Retry Logic

**NLSpec Section 508:** When `allow_partial=true` on a node, if retries are exhausted and the last outcome was `PARTIAL_SUCCESS`, the engine accepts it instead of returning `FAIL`.

**Change needed:** In the engine's retry loop (`engine.py`, the `execute_with_retry` function or equivalent), after retries are exhausted, check `node.attrs.get("allow_partial")`. If true and the last outcome status is `"partial"` or `"partial_success"`, accept it as success instead of failing.

---

## Section 2: Edge Traversal Enhancements (Items 5-6)

### Item 5: `loop_restart` Edge Attribute

**NLSpec Section 174, 388-390:** Edge attribute `loop_restart: Boolean, default false`. When true, traversing this edge terminates the current run, creates a fresh log directory, and re-launches the pipeline from the edge's target node.

**Change needed:** In the engine's edge traversal logic, after selecting the next edge, check `edge.attrs.get("loop_restart")`. If true:

1. Save any accumulated state/results
2. Create a new `logs_root` subdirectory (or timestamp-suffixed directory)
3. Reset retry counters
4. Continue execution from the edge's target node (not from start)

**Real-world usage:** Used in `semport.dot` for:
- The "process next commit" loop: `FinalizeAndUpdateLedger -> FetchUpstreamSonnet` with `loop_restart=true`
- The "skip" path: `AnalyzePlanSonnet -> FetchUpstreamSonnet` with `loop_restart=true`

### Item 6: Parallel Execution via Multi-Edge Fan-Out

The existing parallel handler uses `component`/`tripleoctagon` shapes for explicit fan-out/fan-in. But `consensus_task.dot` uses a different pattern: a single node (e.g., `CheckDoD`) has multiple outgoing edges with the SAME condition (`outcome=needs_dod`) going to different targets (`DefineDoD_Gemini`, `DefineDoD_GPT`, `DefineDoD_Opus`).

The engine's current edge selection picks ONE edge when a condition matches. For the multi-edge pattern, it needs to detect when multiple edges share the same condition and execute all matching targets in parallel.

**Change needed:** In the engine's edge selection logic:

1. After evaluating conditions, collect ALL edges that match (not just the first)
2. If multiple edges match the same condition, fan out to all targets in parallel
3. After all parallel targets complete, continue from the first node that all targets converge on (detected by having multiple incoming edges from the parallel group)
4. Each parallel branch gets an isolated deep-copy of context (per NLSpec Section 798)
5. Only `context_updates` from outcomes merge back

**Fan-in detection:** When the engine tries to execute a node that has incoming edges from nodes that haven't completed yet, it waits. Once all incoming parallel branches have completed, it proceeds. This is automatic -- no explicit fan-in markers required.

**Key benefit:** `consensus_task.dot` works WITHOUT changing the DOT file. The engine infers parallelism from the graph topology.

---

## Section 3: HTTP Server Mode (Item 7)

Extend the existing dashboard FastAPI server (`amplifier-dashboard-attractor`) with pipeline submission and control endpoints per NLSpec Section 9.5. This makes the dashboard the single API surface for pipeline management.

### New Endpoints

Added to the existing dashboard server:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/pipelines` | Submit DOT source + goal, start async execution, return pipeline ID |
| `POST` | `/api/pipelines/{id}/cancel` | Cancel a running pipeline |
| `GET` | `/api/pipelines/{id}/events` | SSE stream of real-time pipeline events |
| `GET` | `/api/pipelines/{id}/questions` | Pending human gate questions |
| `POST` | `/api/pipelines/{id}/questions/{qid}/answer` | Submit human gate answer |

Existing endpoints (already implemented):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/pipelines` | Fleet view |
| `GET` | `/api/pipelines/{id}` | Pipeline state |
| `GET` | `/api/pipelines/{id}/nodes/{nodeId}` | Node detail |

### Pipeline Execution Model

When `POST /api/pipelines` receives a DOT source + goal + optional config:

1. Parse and validate the DOT
2. Create a unique `logs_root` directory for this run
3. Write `graph.dot` to the directory
4. Start the pipeline engine in a background asyncio task
5. Return `{"pipeline_id": "...", "status": "running"}` immediately
6. Engine writes status files as it runs (checkpoint, per-node status)
7. The dashboard's `pipeline_logs_reader` picks up results in real-time
8. SSE endpoint streams events from the engine via a queue

The server manages pipeline execution directly using `DirectProviderBackend` (for analysis pipelines) or a local `AmplifierSession` (for coding pipelines with tools). No dependency on the Amplifier CLI.

### Cancellation

`POST /api/pipelines/{id}/cancel` sets a cancellation flag that the engine checks between node executions. The engine completes the current node, then exits with status `"cancelled"`.

### Human Gate Handling

When the engine hits a `wait.human` handler:

1. Register a pending question on the server (stored in memory, keyed by `pipeline_id` + `question_id`)
2. The handler blocks (`asyncio.Event.wait()`)
3. `GET /questions` returns the pending question
4. `POST /questions/{qid}/answer` sets the answer and signals the `asyncio.Event`
5. The handler resumes with the user's answer

### SSE Event Stream

`GET /api/pipelines/{id}/events` returns a Server-Sent Events stream. Each pipeline event (`pipeline:start`, `pipeline:node_start`, etc.) is pushed as an SSE event with the event data as JSON. The server maintains a per-pipeline event queue that the engine writes to via a hook, and the SSE endpoint reads from.

### Provider Configuration

The `POST /api/pipelines` request body includes optional provider config:

```json
{
  "dot_source": "digraph { ... }",
  "goal": "Build a feature",
  "providers": {
    "anthropic": {"api_key": "...", "default_model": "claude-sonnet-4-20250514"},
    "openai": {"api_key": "...", "default_model": "gpt-5.1"},
    "gemini": {"api_key": "...", "default_model": "gemini-3-flash-preview"}
  }
}
```

If providers are not specified, the server reads from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

---

## Scope Boundaries

- **Items 1-4** (attribute passthrough): Changes to the attractor bundle only (`loop-pipeline` module)
- **Items 5-6** (parallel + loop_restart): Changes to the engine's edge traversal logic
- **Item 7** (HTTP server): Changes to the dashboard server only
- No changes to `amplifier-core`, `amplifier-foundation`, or `execution-environments`

---

## Testing Strategy

| Item | Test Approach |
|------|-------------|
| `reasoning_effort` passthrough | Unit test: verify backend passes `reasoning_effort` to LLM client |
| `max_agent_turns` passthrough | Unit test: verify backend passes `max_turns` to spawn config |
| `allow_partial` | Unit test: verify engine accepts partial success when `allow_partial=true` |
| `loop_restart` | Unit test: verify engine creates fresh logs and restarts from target node |
| Multi-edge parallel | Integration test: run a DOT with multi-edge fan-out, verify parallel execution |
| HTTP server | Integration test: POST a DOT, poll status, verify completion |
| E2E | Run `semport.dot` and `consensus_task.dot` through the engine and verify they complete |

---

## Open Questions

1. **Multi-edge parallel fan-in detection:** Should it be automatic (based on graph topology) or require explicit fan-in markers? Recommended: automatic, with the engine detecting convergence points from the graph structure.

2. **HTTP server execution model:** Should pipeline execution happen in the same process as the server, or in separate worker processes? Recommended: same process (asyncio tasks) for v1, worker processes for v2 if needed for isolation/scaling.

3. **`loop_restart` context handling:** Should the restart create a completely fresh context, or carry over accumulated context from the previous iteration? The NLSpec says "restart_run" which implies fresh, but carrying context may be useful for convergence loops. Recommended: fresh context per NLSpec, with an option to carry over specific keys.
