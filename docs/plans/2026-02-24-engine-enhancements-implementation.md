# Attractor Engine Enhancements — Implementation Plan

> **Historical document (2026-02-24).** The multi-edge parallel fan-out built
> by Group B below shipped and was later RETIRED: T0-4 restored spec §3.3
> single-best-edge selection (`shape=component` + `ParallelHandler` is now the
> only fan-out mechanism). Kept for history.

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement 8 engine enhancements across attribute passthrough, edge traversal, and HTTP server mode to make the attractor pipeline engine production-ready for real-world DOT files (`semport.dot`, `consensus_task.dot`).

**Architecture:** Three independent groups of changes. Group A (Tasks 1-3) wires node attributes through to LLM backends. Group B (Tasks 4-6) enhances the engine's graph traversal for `node_type` dispatch, loop restarts, and multi-edge parallel fan-out. Group C (Tasks 7-8) adds pipeline submission endpoints to the dashboard FastAPI server with background asyncio execution.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio (strict mode), FastAPI, asyncio, unified-llm-client.

**Repos:**
- **Attractor bundle** (Groups A+B): `/home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline/`
- **Dashboard** (Group C): `/home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor/`

**Test commands:**
- Groups A+B: `cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline && uv run pytest tests/ -q --tb=short`
- Group C: `cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor && uv run pytest tests/ -q --tb=short`

---

## Group A — Attribute Passthrough

These three tasks wire existing DOT node attributes through to the LLM backends and retry logic. Each attribute already lives on the `Node` dataclass as a promoted field — the backends just don't read them yet.

---

### Task 1: `reasoning_effort` Passthrough

`reasoning_effort` is already a first-class field on `Node` (line 189 of `graph.py`) and the `AmplifierBackend` already reads it from `node.attrs.get("reasoning_effort")` and passes it to both `_run_with_spawn` and `_run_with_tool_loop`. Both code paths already use it.

This task verifies the existing wiring with explicit tests, then adds `max_agent_turns` reading to `_run_with_tool_loop` (which currently ignores it).

**Files:**
- Test: `modules/loop-pipeline/tests/test_backend.py`

**Step 1: Write the test**

Add the following test to the bottom of `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_reasoning_effort_passed_to_tool_loop(monkeypatch):
    """reasoning_effort from node attrs is forwarded to unified_llm.generate()."""
    captured_kwargs = {}

    async def mock_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result("done")

    monkeypatch.setattr("unified_llm.generate", mock_generate)

    node = Node(
        id="step",
        shape="box",
        prompt="Do work",
        attrs={"llm_provider": "test", "reasoning_effort": "low"},
    )
    coordinator = _MockCoordinator(spawn_fn=None)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"test": "test-profile"},
        provider=True,
        unified_client=_MockUnifiedClient([]),
    )
    await backend.run(node, "Do work", PipelineContext())

    assert captured_kwargs.get("reasoning_effort") == "low"


@pytest.mark.asyncio
async def test_reasoning_effort_defaults_to_none(monkeypatch):
    """When reasoning_effort is not set, None is forwarded (backend default)."""
    captured_kwargs = {}

    async def mock_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result("done")

    monkeypatch.setattr("unified_llm.generate", mock_generate)

    node = Node(
        id="step",
        shape="box",
        prompt="Do work",
        attrs={"llm_provider": "test"},
    )
    coordinator = _MockCoordinator(spawn_fn=None)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"test": "test-profile"},
        provider=True,
        unified_client=_MockUnifiedClient([]),
    )
    await backend.run(node, "Do work", PipelineContext())

    assert captured_kwargs.get("reasoning_effort") is None
```

Before writing this test, check if `_MockCoordinator` and `_make_generate_result` already exist in `test_backend.py`. If they don't, you'll need to create them. Here's what they look like:

```python
class _MockCoordinator:
    """Mock coordinator for testing."""

    def __init__(self, spawn_fn=None):
        self._spawn_fn = spawn_fn
        self.session = None
        self.config = {}

    def get_capability(self, name):
        if name == "session.spawn":
            return self._spawn_fn
        return None


def _make_generate_result(text):
    """Create a minimal unified_llm.GenerateResult for testing."""
    return unified_llm.GenerateResult(
        text=text,
        steps=[],
        total_usage=unified_llm.Usage(
            input_tokens=10, output_tokens=20, total_tokens=30
        ),
        finish_reason=unified_llm.FinishReason(reason="stop"),
    )
```

**Step 2: Run test to verify it passes (or fails if mock helpers are missing)**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_backend.py::test_reasoning_effort_passed_to_tool_loop -v --tb=short
uv run pytest tests/test_backend.py::test_reasoning_effort_defaults_to_none -v --tb=short
```

Expected: If mock helpers exist and the backend already passes `reasoning_effort`, both tests PASS. If not, fix the mock helpers first, then re-run.

The backend at `backend.py` line 337-344 already calls `unified_llm.generate(reasoning_effort=reasoning_effort, ...)`, so these tests should pass once the mock wiring is correct.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/tests/test_backend.py
git commit -m "test: verify reasoning_effort passthrough in AmplifierBackend"
```

---

### Task 2: `max_agent_turns` Passthrough

`max_agent_turns` is used in both real DOT files (`semport.dot`: `max_agent_turns="8"`, `consensus_task.dot`: `max_agent_turns="2"` to `"25"`). The backend needs to read it and pass it to `unified_llm.generate()` as `max_tool_rounds`.

Currently `_run_with_tool_loop` (line 341 of `backend.py`) hardcodes `max_tool_rounds=_MAX_TOOL_LOOP_ROUNDS` (which is 20). We need to read `node.attrs.get("max_agent_turns")` and use it if set.

For `_run_with_spawn`, `max_agent_turns` should be passed in the `orchestrator_config`.

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`
- Test: `modules/loop-pipeline/tests/test_backend.py`

**Step 1: Write the failing test**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_max_agent_turns_limits_tool_loop(monkeypatch):
    """max_agent_turns from node attrs is forwarded as max_tool_rounds."""
    captured_kwargs = {}

    async def mock_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result("done")

    monkeypatch.setattr("unified_llm.generate", mock_generate)

    node = Node(
        id="step",
        shape="box",
        prompt="Do work",
        attrs={"llm_provider": "test", "max_agent_turns": "8"},
    )
    coordinator = _MockCoordinator(spawn_fn=None)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"test": "test-profile"},
        provider=True,
        unified_client=_MockUnifiedClient([]),
    )
    await backend.run(node, "Do work", PipelineContext())

    assert captured_kwargs.get("max_tool_rounds") == 8


@pytest.mark.asyncio
async def test_max_agent_turns_defaults_to_constant(monkeypatch):
    """When max_agent_turns is not set, _MAX_TOOL_LOOP_ROUNDS (20) is used."""
    captured_kwargs = {}

    async def mock_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result("done")

    monkeypatch.setattr("unified_llm.generate", mock_generate)

    node = Node(
        id="step",
        shape="box",
        prompt="Do work",
        attrs={"llm_provider": "test"},
    )
    coordinator = _MockCoordinator(spawn_fn=None)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"test": "test-profile"},
        provider=True,
        unified_client=_MockUnifiedClient([]),
    )
    await backend.run(node, "Do work", PipelineContext())

    assert captured_kwargs.get("max_tool_rounds") == 20
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_backend.py::test_max_agent_turns_limits_tool_loop -v --tb=short
```

Expected: FAIL — the backend currently hardcodes `max_tool_rounds=_MAX_TOOL_LOOP_ROUNDS`.

**Step 3: Implement in `backend.py`**

In `_run_with_tool_loop` (around line 291), add `max_agent_turns` as a parameter:

Change the method signature from:

```python
    async def _run_with_tool_loop(
        self,
        node: Node,
        instruction: str,
        reasoning_effort: str | None,
    ) -> Outcome:
```

to:

```python
    async def _run_with_tool_loop(
        self,
        node: Node,
        instruction: str,
        reasoning_effort: str | None,
        max_agent_turns: int | None = None,
    ) -> Outcome:
```

Then change the `unified_llm.generate()` call (around line 337) from:

```python
            result = await unified_llm.generate(
                model=model,
                prompt=instruction,
                tools=tools or None,
                max_tool_rounds=_MAX_TOOL_LOOP_ROUNDS,
                reasoning_effort=reasoning_effort,
                provider=provider_name,
                client=client,
            )
```

to:

```python
            result = await unified_llm.generate(
                model=model,
                prompt=instruction,
                tools=tools or None,
                max_tool_rounds=max_agent_turns if max_agent_turns is not None else _MAX_TOOL_LOOP_ROUNDS,
                reasoning_effort=reasoning_effort,
                provider=provider_name,
                client=client,
            )
```

Now update every call site of `_run_with_tool_loop` in the `run()` method. There are two call sites (around lines 167 and 173). Read `max_agent_turns` from the node before the routing block:

After `reasoning_effort = node.attrs.get("reasoning_effort")` (line 128), add:

```python
        max_agent_turns_raw = node.attrs.get("max_agent_turns")
        max_agent_turns = int(max_agent_turns_raw) if max_agent_turns_raw is not None else None
```

Then update every call from `self._run_with_tool_loop(node, instruction, reasoning_effort)` to `self._run_with_tool_loop(node, instruction, reasoning_effort, max_agent_turns)`.

For `_run_with_spawn`, add `max_agent_turns` to the `orchestrator_config` dict (around line 226):

```python
        spawn_kwargs: dict[str, Any] = {
            "agent_name": profile_name,
            "instruction": instruction,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
            "orchestrator_config": {
                "reasoning_effort": reasoning_effort,
                "max_turns": max_agent_turns,
            },
        }
```

This means the `run()` method also needs to pass `max_agent_turns` to `_run_with_spawn`. Update the `_run_with_spawn` signature to accept it:

```python
    async def _run_with_spawn(
        self,
        node: Node,
        instruction: str,
        provider: str,
        model: str | None,
        reasoning_effort: str | None,
        max_agent_turns: int | None,
        profile_name: str,
        ...
    ) -> Outcome:
```

And update the call site in `run()` (around line 149) to include `max_agent_turns`.

**Step 4: Run test to verify it passes**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_backend.py::test_max_agent_turns_limits_tool_loop tests/test_backend.py::test_max_agent_turns_defaults_to_constant -v --tb=short
```

Expected: PASS

**Step 5: Run all existing tests to check for regressions**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_backend.py -q --tb=short
```

Expected: All tests PASS.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py modules/loop-pipeline/tests/test_backend.py
git commit -m "feat: wire max_agent_turns through to LLM backends"
```

---

### Task 3: `allow_partial` in Retry Logic

`allow_partial` is already implemented! Look at `retry.py` lines 264-269:

```python
    if node.attrs.get("allow_partial") is True:
        return Outcome(
            status=StageStatus.PARTIAL_SUCCESS,
            notes="Retries exhausted, partial accepted",
            failure_reason=last_outcome.failure_reason if last_outcome else None,
        )
```

And there's already a test for it in `test_retry.py` line 157-166:

```python
async def test_allow_partial_on_exhaustion():
    """allow_partial=true -> PARTIAL_SUCCESS after retries exhausted (RETRY-005)."""
```

This task is just to confirm the existing test covers the behavior and verify the design document's Item 4 is already handled. No code changes needed.

**Step 1: Run the existing test to confirm it passes**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_retry.py::test_allow_partial_on_exhaustion -v --tb=short
```

Expected: PASS — `allow_partial` is already fully implemented and tested.

**Step 2: No commit needed — already implemented.**

---

## Group B — Edge Traversal Enhancements

These tasks modify the engine's graph walking behavior. They are sequential: Task 4 (handler dispatch) enables real DOT files, Task 5 (loop_restart) enables `semport.dot` loops, Task 6 (multi-edge parallel) enables `consensus_task.dot` fan-out.

---

### Task 4: `node_type` as Fallback for Handler Dispatch

Real-world DOT files use `node_type="stack.observe"` and `node_type="stack.steer"` instead of the `type` attribute. The handler registry's `get()` method (in `handlers/__init__.py` line 87) currently checks only `node.type` then `SHAPE_TO_HANDLER`. We need to add `node.attrs.get("node_type")` as a second fallback.

However, per the design document, the `node_type` values in real DOT files are `stack.observe` and `stack.steer`, which are NOT registered handler types. They should map to `codergen` (the default). The important `node_type` values that DO matter are `start` and `exit` (already handled by `is_start_node()` and `is_exit_node()`).

So the actual change is: if `node_type` contains a recognized handler type, use it. Otherwise fall through to shape-based lookup.

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py`
- Test: `modules/loop-pipeline/tests/test_handlers.py`

**Step 1: Write the failing test**

Add to `tests/test_handlers.py`:

```python
def test_registry_node_type_fallback():
    """node_type attribute is used as fallback when type is empty."""
    registry = HandlerRegistry()
    # node_type="conditional" should resolve to ConditionalHandler
    node = Node(id="x", shape="box", type="", attrs={"node_type": "conditional"})
    handler = registry.get(node)
    assert isinstance(handler, ConditionalHandler)


def test_registry_node_type_unknown_falls_to_shape():
    """Unknown node_type (e.g. stack.observe) falls through to shape-based lookup."""
    registry = HandlerRegistry()
    # node_type="stack.observe" is NOT a registered handler type
    # shape=box -> codergen
    node = Node(id="x", shape="box", type="", attrs={"node_type": "stack.observe"})
    handler = registry.get(node)
    assert isinstance(handler, CodergenHandler)


def test_registry_type_takes_priority_over_node_type():
    """Explicit type= attribute takes priority over node_type."""
    registry = HandlerRegistry()
    node = Node(id="x", shape="box", type="conditional", attrs={"node_type": "codergen"})
    handler = registry.get(node)
    assert isinstance(handler, ConditionalHandler)
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_handlers.py::test_registry_node_type_fallback -v --tb=short
```

Expected: FAIL — `node_type="conditional"` is not checked, so it falls through to shape=box → codergen.

**Step 3: Implement in `handlers/__init__.py`**

Change `HandlerRegistry.get()` (line 81-88) from:

```python
    def get(self, node: Node) -> NodeHandler:
        """Resolve the handler for a node.

        Uses the node's explicit type first, then shape mapping,
        falling back to codergen.
        """
        handler_type = node.type or SHAPE_TO_HANDLER.get(node.shape, "codergen")
        return self._handlers.get(handler_type, self._handlers["codergen"])
```

to:

```python
    def get(self, node: Node) -> NodeHandler:
        """Resolve the handler for a node.

        Resolution order:
        1. Node's explicit ``type`` attribute (highest priority)
        2. ``node_type`` attribute if it matches a registered handler
        3. Shape-to-handler-type mapping (lowest priority, default codergen)
        """
        if node.type:
            handler_type = node.type
        else:
            # Fallback: check node_type attr for recognized handler types
            node_type_attr = node.attrs.get("node_type")
            if node_type_attr and node_type_attr in self._handlers:
                handler_type = node_type_attr
            else:
                handler_type = SHAPE_TO_HANDLER.get(node.shape, "codergen")
        return self._handlers.get(handler_type, self._handlers["codergen"])
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_handlers.py::test_registry_node_type_fallback tests/test_handlers.py::test_registry_node_type_unknown_falls_to_shape tests/test_handlers.py::test_registry_type_takes_priority_over_node_type -v --tb=short
```

Expected: All 3 PASS.

**Step 5: Run all handler tests for regressions**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_handlers.py -q --tb=short
```

Expected: All PASS.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py modules/loop-pipeline/tests/test_handlers.py
git commit -m "feat: add node_type as fallback in handler dispatch resolution"
```

---

### Task 5: `loop_restart` Edge Attribute Handling

When an edge has `loop_restart=true`, traversing it should:
1. Create a fresh log subdirectory (timestamp-suffixed)
2. Reset retry counters
3. Continue execution from the edge's target node (not from start)

`loop_restart` is already a promoted field on the `Edge` dataclass (line 261 of `graph.py`). The engine just doesn't act on it.

Used in `semport.dot`:
- `FinalizeAndUpdateLedger -> FetchUpstreamSonnet [loop_restart="true"]`
- `AnalyzePlanSonnet -> FetchUpstreamSonnet [condition="outcome=skip", label="skip", loop_restart="true"]`

And in `consensus_task.dot`:
- `Postmortem -> PlanGemini [loop_restart="true"]`
- `Postmortem -> PlanGPT [loop_restart="true"]`
- `Postmortem -> PlanOpus [loop_restart="true"]`

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`
- Test: `modules/loop-pipeline/tests/test_engine.py`

**Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_loop_restart_creates_fresh_logs_and_continues(tmp_path):
    """loop_restart=true on an edge creates a new log dir and continues from target."""
    import os

    # Build a simple graph: start -> work -> exit, with work -> work [loop_restart=true]
    # We'll use a custom backend that returns "retry" once then "success"
    call_count = {"n": 0}

    class LoopBackend:
        async def run(self, node, prompt, context):
            call_count["n"] += 1
            if node.id == "work" and call_count["n"] == 1:
                return Outcome(
                    status=StageStatus.SUCCESS,
                    context_updates={"outcome": "loop"},
                )
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-loop-restart",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(
                from_node="work",
                to_node="work",
                condition="outcome=loop",
                attrs={"loop_restart": True},
            ),
            Edge(from_node="work", to_node="exit", condition="outcome=success"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(backend=LoopBackend())
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # work was executed twice (once before loop_restart, once after)
    assert call_count["n"] >= 2
    # A fresh log subdirectory was created (loop iteration)
    subdirs = [d for d in os.listdir(tmp_path) if os.path.isdir(os.path.join(tmp_path, d))]
    # Should have at least the original node dirs plus a loop iteration subdir
    assert len(subdirs) >= 1
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py::test_loop_restart_creates_fresh_logs_and_continues -v --tb=short
```

Expected: FAIL — the engine doesn't check `loop_restart` on edges and won't create fresh log dirs.

**Step 3: Implement in `engine.py`**

In the main `run()` loop, after edge selection (around line 314) and the `PIPELINE_EDGE_SELECTED` event emission (line 348-355), add `loop_restart` handling before advancing to the next node.

Find this block in `engine.py` (around line 348-358):

```python
            await self._emit(
                PIPELINE_EDGE_SELECTED,
                {
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "edge_label": edge.label,
                },
            )

            # Step 6: Advance to next node
            current_node = self.graph.nodes[edge.to_node]
```

Replace it with:

```python
            await self._emit(
                PIPELINE_EDGE_SELECTED,
                {
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "edge_label": edge.label,
                },
            )

            # Step 6: Handle loop_restart edge attribute
            if edge.loop_restart is True:
                # Create a fresh log subdirectory for the new iteration
                iteration_dir = os.path.join(
                    self.logs_root,
                    f"loop-{len(self.completed_nodes)}-{int(time.monotonic() * 1000)}",
                )
                os.makedirs(iteration_dir, exist_ok=True)
                logger.info(
                    "loop_restart: fresh log dir '%s', continuing from '%s'",
                    iteration_dir,
                    edge.to_node,
                )
                # Reset retry counters by clearing completed_nodes
                # (allows nodes to be re-executed)
                self.completed_nodes.clear()
                self.node_outcomes.clear()
                # Reset the failure routing counter
                failure_routing_retries = 0

            # Step 7: Advance to next node
            current_node = self.graph.nodes[edge.to_node]
```

**Step 4: Run test to verify it passes**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py::test_loop_restart_creates_fresh_logs_and_continues -v --tb=short
```

Expected: PASS

**Step 5: Run all engine tests for regressions**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py -q --tb=short
```

Expected: All PASS.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py modules/loop-pipeline/tests/test_engine.py
git commit -m "feat: implement loop_restart edge attribute for iteration loops"
```

---

### Task 6: Multi-Edge Parallel Fan-Out Detection and Execution

This is the most complex task. In `consensus_task.dot`, a single node like `CheckDoD` has multiple outgoing edges with the SAME condition going to different targets:

```dot
CheckDoD -> DefineDoD_Gemini [condition="outcome=needs_dod"];
CheckDoD -> DefineDoD_GPT [condition="outcome=needs_dod"];
CheckDoD -> DefineDoD_Opus [condition="outcome=needs_dod"];
```

The current `select_edge()` function returns only ONE edge (the first match). The engine needs to detect when multiple edges match the same condition and execute all targets in parallel.

**Approach:**
1. Add a new function `select_all_matching_edges()` to `edge_selection.py`
2. Modify the engine's main loop to call it, detect multi-edge fan-out, execute in parallel, then find the convergence (fan-in) node

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py`
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`
- Test: `modules/loop-pipeline/tests/test_edge_selection.py`
- Test: `modules/loop-pipeline/tests/test_engine.py`

This task is split into two sub-tasks for manageability.

#### Task 6a: Add `select_all_matching_edges()` to edge selection

**Step 1: Write the failing test**

Add to `tests/test_edge_selection.py`:

```python
from amplifier_module_loop_pipeline.edge_selection import select_all_matching_edges


def test_select_all_matching_edges_single_match():
    """Single matching edge returns a list with one edge."""
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "a": Node(id="a", shape="box", prompt="A"),
            "b": Node(id="b", shape="box", prompt="B"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="a", condition="outcome=success"),
            Edge(from_node="start", to_node="b", condition="outcome=fail"),
        ],
    )
    outcome = Outcome(status=StageStatus.SUCCESS)
    context = PipelineContext()
    edges = select_all_matching_edges("start", outcome, context, graph)
    assert len(edges) == 1
    assert edges[0].to_node == "a"


def test_select_all_matching_edges_multi_match():
    """Multiple edges with same condition returns all of them."""
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "a": Node(id="a", shape="box", prompt="A"),
            "b": Node(id="b", shape="box", prompt="B"),
            "c": Node(id="c", shape="box", prompt="C"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="a", condition="outcome=success"),
            Edge(from_node="start", to_node="b", condition="outcome=success"),
            Edge(from_node="start", to_node="c", condition="outcome=success"),
        ],
    )
    outcome = Outcome(status=StageStatus.SUCCESS)
    context = PipelineContext()
    edges = select_all_matching_edges("start", outcome, context, graph)
    assert len(edges) == 3
    target_nodes = {e.to_node for e in edges}
    assert target_nodes == {"a", "b", "c"}


def test_select_all_matching_edges_no_match():
    """No matching edges returns empty list."""
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "a": Node(id="a", shape="box", prompt="A"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="a", condition="outcome=fail"),
        ],
    )
    outcome = Outcome(status=StageStatus.SUCCESS)
    context = PipelineContext()
    edges = select_all_matching_edges("start", outcome, context, graph)
    assert len(edges) == 0
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_edge_selection.py::test_select_all_matching_edges_multi_match -v --tb=short
```

Expected: FAIL — `select_all_matching_edges` doesn't exist yet.

**Step 3: Implement in `edge_selection.py`**

Add this function to the end of `edge_selection.py` (after the existing `select_edge` function):

```python
def select_all_matching_edges(
    node_id: str,
    outcome: Outcome,
    context: PipelineContext,
    graph: Graph,
) -> list[Edge]:
    """Return ALL condition-matching edges from a node's outgoing edges.

    Unlike select_edge() which returns the single best edge, this returns
    every edge whose condition evaluates to True. Used by the engine to
    detect multi-edge fan-out patterns (parallel execution).

    Returns an empty list if no edges have matching conditions.
    Falls back to the same five-step logic as select_edge for non-condition
    edges, but only when no condition-matched edges exist.
    """
    edges = graph.outgoing_edges(node_id)
    if not edges:
        return []

    # Step 1: ALL condition-matching edges
    condition_matched = [
        e
        for e in edges
        if e.condition and evaluate_condition(e.condition, outcome, context)
    ]
    return condition_matched
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_edge_selection.py::test_select_all_matching_edges_single_match tests/test_edge_selection.py::test_select_all_matching_edges_multi_match tests/test_edge_selection.py::test_select_all_matching_edges_no_match -v --tb=short
```

Expected: All 3 PASS.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py modules/loop-pipeline/tests/test_edge_selection.py
git commit -m "feat: add select_all_matching_edges for multi-edge fan-out detection"
```

#### Task 6b: Engine Multi-Edge Parallel Execution

Now modify the engine's main loop to use `select_all_matching_edges` and execute in parallel when multiple edges match.

**Step 1: Write the failing test**

Add to `tests/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_multi_edge_parallel_fan_out(tmp_path):
    """Multiple edges with the same condition from one node execute in parallel."""
    executed_nodes = []

    class TrackingBackend:
        async def run(self, node, prompt, context):
            executed_nodes.append(node.id)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-parallel",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "branch_a": Node(id="branch_a", shape="box", prompt="A"),
            "branch_b": Node(id="branch_b", shape="box", prompt="B"),
            "branch_c": Node(id="branch_c", shape="box", prompt="C"),
            "consolidate": Node(id="consolidate", shape="box", prompt="Merge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            # Multi-edge fan-out: same condition, three targets
            Edge(from_node="check", to_node="branch_a", condition="outcome=success"),
            Edge(from_node="check", to_node="branch_b", condition="outcome=success"),
            Edge(from_node="check", to_node="branch_c", condition="outcome=success"),
            # All branches converge on consolidate (fan-in)
            Edge(from_node="branch_a", to_node="consolidate"),
            Edge(from_node="branch_b", to_node="consolidate"),
            Edge(from_node="branch_c", to_node="consolidate"),
            Edge(from_node="consolidate", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(backend=TrackingBackend())
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # All three branches AND consolidate should have been executed
    assert "branch_a" in executed_nodes
    assert "branch_b" in executed_nodes
    assert "branch_c" in executed_nodes
    assert "consolidate" in executed_nodes
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py::test_multi_edge_parallel_fan_out -v --tb=short
```

Expected: FAIL — the engine currently picks only one edge when multiple match.

**Step 3: Implement in `engine.py`**

This requires changes in the main `run()` loop. The key insight: after edge selection (Step 5 in the loop), check if multiple edges match. If so, execute all targets in parallel, then advance to the convergence node.

First, add the import at the top of `engine.py`:

```python
from .edge_selection import select_all_matching_edges, select_edge
```

(Replace the existing `from .edge_selection import select_edge` on line 24.)

Then, in the main `run()` loop, replace the edge selection block (around lines 313-358). Find this section:

```python
            # Step 5: Select next edge
            edge = select_edge(current_node.id, outcome, self.context, self.graph)
            if edge is None:
```

Replace the entire Step 5 through Step 6 (lines 313-358) with:

```python
            # Step 5: Select next edge(s) — detect multi-edge fan-out
            all_matching = select_all_matching_edges(
                current_node.id, outcome, self.context, self.graph
            )

            if len(all_matching) > 1:
                # Multi-edge fan-out: execute all targets in parallel
                logger.info(
                    "Multi-edge fan-out from '%s': %d parallel targets",
                    current_node.id,
                    len(all_matching),
                )

                parallel_outcomes = await self._execute_parallel_fan_out(
                    all_matching, pipeline_start_time
                )

                # Find convergence node: the first node that all parallel
                # targets share as a common outgoing edge target
                fan_in_node_id = self._find_fan_in_node(
                    [e.to_node for e in all_matching]
                )
                if fan_in_node_id is None:
                    fail_outcome = Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=(
                            f"Multi-edge fan-out from '{current_node.id}' "
                            f"has no convergence (fan-in) node"
                        ),
                    )
                    await self._emit_complete(fail_outcome, pipeline_start_time)
                    return fail_outcome

                # Store parallel results in context for the fan-in node
                self.context.set("parallel.results", parallel_outcomes)
                self.context.set("parallel.count", len(parallel_outcomes))

                current_node = self.graph.nodes[fan_in_node_id]
                continue

            # Single-edge selection (normal path)
            edge = select_edge(current_node.id, outcome, self.context, self.graph)
            if edge is None:
                # Try failure routing: node/graph retry targets
                retry_node = self._resolve_failure_retry_target(current_node)
                if (
                    retry_node is not None
                    and failure_routing_retries < self._MAX_GOAL_GATE_RETRIES
                ):
                    failure_routing_retries += 1
                    logger.info(
                        "No matching edge from '%s', failure-routing to '%s' "
                        "(attempt %d)",
                        current_node.id,
                        retry_node.id,
                        failure_routing_retries,
                    )
                    current_node = retry_node
                    continue

                fail_outcome = Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"No matching edge from node '{current_node.id}'",
                )
                await self._emit(
                    PIPELINE_ERROR,
                    {
                        "node_id": current_node.id,
                        "error_type": "no_matching_edge",
                        "message": fail_outcome.failure_reason or "",
                    },
                )
                await self._emit_complete(fail_outcome, pipeline_start_time)
                return fail_outcome

            await self._emit(
                PIPELINE_EDGE_SELECTED,
                {
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "edge_label": edge.label,
                },
            )

            # Step 6: Handle loop_restart edge attribute
            if edge.loop_restart is True:
                iteration_dir = os.path.join(
                    self.logs_root,
                    f"loop-{len(self.completed_nodes)}-{int(time.monotonic() * 1000)}",
                )
                os.makedirs(iteration_dir, exist_ok=True)
                logger.info(
                    "loop_restart: fresh log dir '%s', continuing from '%s'",
                    iteration_dir,
                    edge.to_node,
                )
                self.completed_nodes.clear()
                self.node_outcomes.clear()
                failure_routing_retries = 0

            # Step 7: Advance to next node
            current_node = self.graph.nodes[edge.to_node]
```

Now add the two new helper methods to `PipelineEngine`:

```python
    async def _execute_parallel_fan_out(
        self,
        edges: list,
        pipeline_start_time: float,
    ) -> list[dict[str, Any]]:
        """Execute multiple branch targets in parallel with isolated contexts.

        Each branch gets a deep copy of the current context. Only
        context_updates from outcomes are collected.
        """
        import copy

        results: list[dict[str, Any]] = []

        async def run_branch(target_node_id: str) -> dict[str, Any]:
            branch_context = self.context.clone()
            node = self.graph.nodes[target_node_id]
            handler = self.handler_registry.get(node)
            handler_type = node.type or node.shape

            await self._emit(
                PIPELINE_NODE_START,
                {"node_id": node.id, "handler_type": handler_type, "attempt": 1},
            )

            node_start = time.monotonic()
            retry_policy = RetryPolicy.from_node(node, self.graph)

            try:
                outcome = await execute_with_retry(
                    handler,
                    node,
                    branch_context,
                    self.graph,
                    self.logs_root,
                    retry_policy,
                    hooks=self.hooks,
                )
            except Exception as exc:
                outcome = Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Parallel branch '{target_node_id}' raised: {exc}",
                )

            node_duration = (time.monotonic() - node_start) * 1000

            # Record completion in the main engine state
            self.completed_nodes.append(target_node_id)
            self.node_outcomes[target_node_id] = outcome

            await self._emit(
                PIPELINE_NODE_COMPLETE,
                {
                    "node_id": target_node_id,
                    "status": outcome.status.value,
                    "duration_ms": node_duration,
                },
            )
            self._write_node_status(target_node_id, outcome, node_duration)

            return {
                "node_id": target_node_id,
                "status": outcome.status.value,
                "notes": outcome.notes,
                "failure_reason": outcome.failure_reason,
                "context_updates": outcome.context_updates,
            }

        # Execute all branches concurrently
        tasks = [run_branch(edge.to_node) for edge in edges]
        results = list(await asyncio.gather(*tasks))

        # Apply context_updates from all branches
        for result in results:
            updates = result.get("context_updates")
            if updates:
                self.context.update(updates)

        return results

    def _find_fan_in_node(self, parallel_target_ids: list[str]) -> str | None:
        """Find the convergence node where all parallel branches meet.

        Looks for a node that has incoming unconditional edges from ALL
        parallel target nodes.
        """
        if not parallel_target_ids:
            return None

        # Collect all outgoing edge targets from each parallel node
        target_sets: list[set[str]] = []
        for node_id in parallel_target_ids:
            targets = {e.to_node for e in self.graph.outgoing_edges(node_id)}
            target_sets.append(targets)

        # Fan-in node = intersection of all target sets
        if not target_sets:
            return None

        common = target_sets[0]
        for ts in target_sets[1:]:
            common = common & ts

        if not common:
            return None

        # If multiple common targets, pick the first alphabetically
        return sorted(common)[0]
```

**Step 4: Run test to verify it passes**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py::test_multi_edge_parallel_fan_out -v --tb=short
```

Expected: PASS

**Step 5: Run all engine tests for regressions**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/test_engine.py -q --tb=short
```

Expected: All PASS. If any tests break, it's likely because the edge selection refactoring changed the code path. Debug by comparing the old single-edge path with the new one.

**Step 6: Run the full test suite**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/ -q --tb=short
```

Expected: All PASS.

**Step 7: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py modules/loop-pipeline/tests/test_engine.py
git commit -m "feat: implement multi-edge parallel fan-out detection and execution"
```

---

## Group C — HTTP Server Mode

These tasks add pipeline submission and background execution to the dashboard FastAPI server. They are self-contained in the dashboard repo.

---

### Task 7: Pipeline Submission Endpoint (`POST /api/pipelines`)

Add a `POST /api/pipelines` endpoint that accepts DOT source + goal, validates the DOT, creates a log directory, and returns a pipeline ID immediately.

**Files:**
- Create: `amplifier_dashboard_attractor/routes/submissions.py`
- Modify: `amplifier_dashboard_attractor/server.py`
- Test: `tests/test_submissions.py`

**Step 1: Write the failing test**

Create `tests/test_submissions.py`:

```python
"""Tests for pipeline submission endpoint (POST /api/pipelines)."""

import pytest
from httpx import ASGITransport, AsyncClient

from amplifier_dashboard_attractor.server import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(pipeline_logs_dir=str(tmp_path))


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


SIMPLE_DOT = """
digraph {
    start [shape=Mdiamond]
    work [prompt="Do something"]
    exit [shape=Msquare]
    start -> work -> exit
}
"""


@pytest.mark.asyncio
async def test_submit_pipeline_returns_pipeline_id(client):
    """POST /api/pipelines returns 201 with pipeline_id and status."""
    resp = await client.post(
        "/api/pipelines",
        json={"dot_source": SIMPLE_DOT, "goal": "Test goal"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "pipeline_id" in body
    assert body["status"] == "running"


@pytest.mark.asyncio
async def test_submit_pipeline_invalid_dot(client):
    """POST /api/pipelines with invalid DOT returns 422."""
    resp = await client.post(
        "/api/pipelines",
        json={"dot_source": "not valid dot", "goal": "Test"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_pipeline_missing_dot_source(client):
    """POST /api/pipelines without dot_source returns 422."""
    resp = await client.post(
        "/api/pipelines",
        json={"goal": "Test"},
    )
    assert resp.status_code == 422
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/test_submissions.py -v --tb=short
```

Expected: FAIL — the endpoint doesn't exist.

**Step 3: Create `amplifier_dashboard_attractor/routes/submissions.py`**

```python
"""Pipeline submission endpoints.

POST /api/pipelines  — Submit DOT source + goal, start async execution.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/pipelines", tags=["submissions"])


class PipelineSubmission(BaseModel):
    """Request body for pipeline submission."""

    dot_source: str = Field(..., description="DOT digraph source")
    goal: str = Field("", description="Pipeline goal")
    providers: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider configs: {provider_name: {api_key, default_model}}",
    )


@router.post("", status_code=201)
async def submit_pipeline(request: Request, submission: PipelineSubmission):
    """Submit a pipeline for execution.

    1. Parse and validate the DOT source
    2. Create a logs_root directory
    3. Write graph.dot to the directory
    4. Start background execution (if executor available)
    5. Return pipeline_id + status immediately
    """
    # Lazy import to avoid hard dependency on the pipeline module
    try:
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.validation import (
            ValidationError,
            validate_or_raise,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Pipeline engine module (amplifier-module-loop-pipeline) not installed",
        )

    # 1. Parse DOT
    try:
        graph = parse_dot(submission.dot_source)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DOT source: {exc}")

    # 2. Validate
    try:
        validate_or_raise(graph)
    except ValidationError as exc:
        messages = [d.message for d in exc.diagnostics if d.severity == "ERROR"]
        raise HTTPException(
            status_code=422,
            detail=f"DOT validation failed: {'; '.join(messages)}",
        )

    # 3. Create logs directory
    pipeline_id = f"{graph.name}-{uuid.uuid4().hex[:8]}"
    logs_base = _get_logs_base(request)
    logs_root = os.path.join(logs_base, pipeline_id)
    os.makedirs(logs_root, exist_ok=True)

    # Write graph.dot
    dot_path = os.path.join(logs_root, "graph.dot")
    with open(dot_path, "w") as f:
        f.write(submission.dot_source)

    # Write manifest.json
    import json

    manifest = {
        "graph_name": graph.name,
        "goal": submission.goal,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
    }
    with open(os.path.join(logs_root, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # 4. Start background execution (Task 8 adds this)
    executor = getattr(request.app.state, "pipeline_executor", None)
    if executor is not None:
        await executor.start(
            pipeline_id=pipeline_id,
            graph=graph,
            goal=submission.goal,
            logs_root=logs_root,
            providers=submission.providers,
        )

    # 5. Return immediately
    return {
        "pipeline_id": pipeline_id,
        "status": "running",
        "logs_root": logs_root,
    }


def _get_logs_base(request: Request) -> str:
    """Resolve the base directory for pipeline logs."""
    reader = getattr(request.app.state, "pipeline_logs_reader", None)
    if reader and reader.logs_dirs:
        return str(reader.logs_dirs[0])
    return "/tmp/attractor-pipelines"
```

**Step 4: Register the router in `server.py`**

In `amplifier_dashboard_attractor/server.py`, add the import and registration. After the existing router imports (around line 23-24):

```python
from amplifier_dashboard_attractor.routes.pipelines import router as pipelines_router
from amplifier_dashboard_attractor.routes.submissions import router as submissions_router
from amplifier_dashboard_attractor.routes.ws import router as ws_router
```

Then after `app.include_router(pipelines_router)` (around line 70), add:

```python
    app.include_router(submissions_router)
```

**Step 5: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/test_submissions.py -v --tb=short
```

Expected: PASS for `test_submit_pipeline_returns_pipeline_id` and `test_submit_pipeline_missing_dot_source`. The `test_submit_pipeline_invalid_dot` test should also PASS because `parse_dot()` will raise on invalid input.

**Note:** This test requires `amplifier-module-loop-pipeline` to be importable. If it's not installed in the dashboard's venv, either install it (`uv pip install -e ../amplifier-bundle-attractor/modules/loop-pipeline`) or mock the import in the test.

**Step 6: Run all dashboard tests**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/ -q --tb=short
```

Expected: All PASS.

**Step 7: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
git add amplifier_dashboard_attractor/routes/submissions.py amplifier_dashboard_attractor/server.py tests/test_submissions.py
git commit -m "feat: add POST /api/pipelines endpoint for pipeline submission"
```

---

### Task 8: Background Execution with Asyncio Tasks

Add a `PipelineExecutor` class that manages background asyncio tasks for pipeline execution. When a pipeline is submitted (Task 7), the executor starts the engine in a background task. The existing `pipeline_logs_reader` picks up results as they're written to disk.

**Files:**
- Create: `amplifier_dashboard_attractor/pipeline_executor.py`
- Modify: `amplifier_dashboard_attractor/server.py`
- Test: `tests/test_pipeline_executor.py`

**Step 1: Write the failing test**

Create `tests/test_pipeline_executor.py`:

```python
"""Tests for the pipeline background executor."""

import asyncio

import pytest

from amplifier_dashboard_attractor.pipeline_executor import PipelineExecutor


@pytest.mark.asyncio
async def test_executor_starts_and_tracks_pipeline(tmp_path):
    """Executor starts a pipeline and tracks it by ID."""
    executor = PipelineExecutor()

    # Create a minimal mock graph and engine
    # We'll test with a DOT string that the executor parses internally
    dot_source = """
    digraph {
        start [shape=Mdiamond]
        work [prompt="Do something"]
        exit [shape=Msquare]
        start -> work -> exit
    }
    """
    logs_root = str(tmp_path / "test-pipeline")

    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot(dot_source)

    await executor.start(
        pipeline_id="test-001",
        graph=graph,
        goal="Test goal",
        logs_root=logs_root,
        providers={},
    )

    assert "test-001" in executor.active_pipelines
    # Give the background task a moment to run
    await asyncio.sleep(0.5)

    status = executor.get_status("test-001")
    assert status in ("running", "completed", "failed")


@pytest.mark.asyncio
async def test_executor_unknown_pipeline():
    """Getting status of unknown pipeline returns None."""
    executor = PipelineExecutor()
    assert executor.get_status("nonexistent") is None


@pytest.mark.asyncio
async def test_executor_cleanup(tmp_path):
    """Completed pipelines can be cleaned up."""
    executor = PipelineExecutor()

    dot_source = """
    digraph {
        start [shape=Mdiamond]
        exit [shape=Msquare]
        start -> exit
    }
    """
    logs_root = str(tmp_path / "test-cleanup")

    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot(dot_source)

    await executor.start(
        pipeline_id="cleanup-001",
        graph=graph,
        goal="Cleanup test",
        logs_root=logs_root,
        providers={},
    )

    # Wait for completion
    await asyncio.sleep(1.0)

    executor.cleanup_completed()
    # Completed pipeline should be removed from active tracking
    # (or still present but marked as completed)
    status = executor.get_status("cleanup-001")
    assert status in ("completed", "failed", None)
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/test_pipeline_executor.py -v --tb=short
```

Expected: FAIL — `PipelineExecutor` doesn't exist.

**Step 3: Create `amplifier_dashboard_attractor/pipeline_executor.py`**

```python
"""Background pipeline execution manager.

Manages asyncio tasks for running pipelines submitted via the HTTP API.
Each pipeline runs in its own background task, writing results to disk
where the pipeline_logs_reader picks them up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """Manages background pipeline execution tasks.

    Each submitted pipeline gets its own asyncio task. The executor
    tracks active tasks by pipeline_id and provides status queries.
    """

    def __init__(self) -> None:
        self.active_pipelines: dict[str, dict[str, Any]] = {}

    async def start(
        self,
        *,
        pipeline_id: str,
        graph: Any,
        goal: str,
        logs_root: str,
        providers: dict[str, Any],
    ) -> None:
        """Start a pipeline in a background asyncio task."""
        task = asyncio.create_task(
            self._run_pipeline(pipeline_id, graph, goal, logs_root, providers),
            name=f"pipeline-{pipeline_id}",
        )
        self.active_pipelines[pipeline_id] = {
            "task": task,
            "status": "running",
            "logs_root": logs_root,
        }

    async def _run_pipeline(
        self,
        pipeline_id: str,
        graph: Any,
        goal: str,
        logs_root: str,
        providers: dict[str, Any],
    ) -> None:
        """Execute a pipeline engine in the background."""
        try:
            from amplifier_module_loop_pipeline.context import PipelineContext
            from amplifier_module_loop_pipeline.engine import PipelineEngine
            from amplifier_module_loop_pipeline.handlers import HandlerRegistry

            context = PipelineContext()

            # Build backend from providers config (if available)
            backend = self._build_backend(providers)

            registry = HandlerRegistry(backend=backend)

            # Wire up the subgraph runner for parallel execution
            engine = PipelineEngine(
                graph=graph,
                context=context,
                handler_registry=registry,
                logs_root=logs_root,
            )

            outcome = await engine.run(goal=goal)

            status = "completed" if outcome.is_success else "failed"
            if pipeline_id in self.active_pipelines:
                self.active_pipelines[pipeline_id]["status"] = status

            logger.info(
                "Pipeline %s finished: %s",
                pipeline_id,
                outcome.status.value,
            )

        except Exception as exc:
            logger.error("Pipeline %s failed with exception: %s", pipeline_id, exc)
            if pipeline_id in self.active_pipelines:
                self.active_pipelines[pipeline_id]["status"] = "failed"
                self.active_pipelines[pipeline_id]["error"] = str(exc)

    def _build_backend(self, providers: dict[str, Any]) -> Any | None:
        """Build an AmplifierBackend from provider configuration.

        Returns None if no providers are configured (simulation mode).
        """
        if not providers:
            return None

        try:
            from amplifier_module_loop_pipeline.backend import AmplifierBackend

            # Create a minimal coordinator stub for direct backend use
            class _StubCoordinator:
                session = None
                config: dict[str, Any] = {}

                def get_capability(self, name: str) -> None:
                    return None

            # Build profiles from provider config
            profiles = {name: name for name in providers}

            return AmplifierBackend(
                coordinator=_StubCoordinator(),
                profiles=profiles,
                provider=True,  # Enable direct tool loop
            )
        except ImportError:
            logger.warning("Pipeline module not available, running in simulation mode")
            return None

    def get_status(self, pipeline_id: str) -> str | None:
        """Get the current status of a pipeline."""
        info = self.active_pipelines.get(pipeline_id)
        if info is None:
            return None
        return info["status"]

    def cleanup_completed(self) -> int:
        """Remove completed/failed pipelines from tracking.

        Returns the number of pipelines cleaned up.
        """
        to_remove = [
            pid
            for pid, info in self.active_pipelines.items()
            if info["status"] in ("completed", "failed")
        ]
        for pid in to_remove:
            del self.active_pipelines[pid]
        return len(to_remove)
```

**Step 4: Wire executor into `server.py`**

In `server.py`, after the data source initialization block (around line 73), add executor initialization. After the `pipeline_logs_dir` block:

```python
    if pipeline_logs_dir:
        from amplifier_dashboard_attractor.pipeline_logs_reader import (
            PipelineLogsReader,
        )

        dirs = [d.strip() for d in pipeline_logs_dir.split(",") if d.strip()]
        app.state.pipeline_logs_reader = PipelineLogsReader(logs_dirs=dirs)

        # Initialize pipeline executor for background execution
        from amplifier_dashboard_attractor.pipeline_executor import PipelineExecutor

        app.state.pipeline_executor = PipelineExecutor()
```

**Step 5: Run tests to verify they pass**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/test_pipeline_executor.py -v --tb=short
```

Expected: PASS (the pipeline runs in simulation mode without a backend, writing logs to disk).

**Step 6: Run all dashboard tests**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/ -q --tb=short
```

Expected: All PASS.

**Step 7: Commit**

```bash
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
git add amplifier_dashboard_attractor/pipeline_executor.py amplifier_dashboard_attractor/server.py tests/test_pipeline_executor.py
git commit -m "feat: add background pipeline executor with asyncio tasks"
```

---

## Final Validation

After all 8 tasks are complete, run the full test suites for both repos:

```bash
# Attractor bundle (Groups A + B)
cd /home/bkrabach/dev/attractor-next/amplifier-bundle-attractor/modules/loop-pipeline
uv run pytest tests/ -q --tb=short

# Dashboard (Group C)
cd /home/bkrabach/dev/attractor-next/amplifier-dashboard-attractor
uv run pytest tests/ -q --tb=short
```

Both should report all tests passing with zero failures.

---

## Deferred to v1.1

The following endpoints are NOT included in this plan:

- `POST /api/pipelines/{id}/cancel` — cancellation flag + engine check
- `GET /api/pipelines/{id}/events` — SSE event stream
- `GET /api/pipelines/{id}/questions` — pending human gate questions
- `POST /api/pipelines/{id}/questions/{qid}/answer` — human gate answers

These build on the executor infrastructure from Task 8 and can be added incrementally.
