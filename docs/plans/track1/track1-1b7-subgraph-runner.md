> **STATUS: Completed — PR #36.**

# Track 1-1B7: Wire subgraph_runner for Parallel/Manager Handlers (H-11)

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Create a `subgraph_runner` function that executes a portion of the graph starting from a given node, and wire it through `HandlerRegistry` to `ParallelHandler` and `ManagerLoopHandler` so they can actually run subgraphs instead of silently returning `None`-mode results.
**Architecture:** The runner is a closure created inside `PipelineOrchestrator.execute()` that captures the engine instance. It calls a new `PipelineEngine._run_from(node_id)` method which walks the graph from the specified node to the next exit-like terminal (exit node, or a node with no outgoing edges in the subgraph scope). The closure is passed as `subgraph_runner=` to `HandlerRegistry`, which passes it to `ParallelHandler` and `ManagerLoopHandler`.
**Tech Stack:** Python, pytest

**Finding:** H-11 from adversarial-spec-review.md
**Spec Reference:** Section 4.8 (Parallel), Section 4.11 (Manager Loop), Section 3.8 (Concurrency Model)

---

## Root Cause

**File:** `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py` lines 62-73

```python
        self._handlers: dict[str, NodeHandler] = {
            # ...
            "stack.manager_loop": ManagerLoopHandler(
                subgraph_runner=kwargs.get("subgraph_runner"),  # <-- always None
            ),
            "parallel": ParallelHandler(
                subgraph_runner=kwargs.get("subgraph_runner"),  # <-- always None
            ),
            # ...
        }
```

**File:** `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py` line 266

```python
        registry = HandlerRegistry(backend=backend)  # <-- no subgraph_runner passed
```

**Problem:** `HandlerRegistry` is constructed without a `subgraph_runner`. Both `ParallelHandler` and `ManagerLoopHandler` receive `None` and fall back to simulation mode:

- `ParallelHandler` (parallel.py:91-95): returns `Outcome(status=SUCCESS, notes="Simulated branch: ...")` 
- `ManagerLoopHandler` (manager_loop.py:110-114): returns `Outcome(status=FAIL, failure_reason="Manager loop requires a subgraph_runner")`

Neither handler can execute any actual subgraph work.

**What's needed:**
1. A `_run_from(start_node_id)` method on `PipelineEngine` that walks the graph from a given node to an exit/terminal, returning the final `Outcome`.
2. A closure/factory that creates the runner function with the right signature: `(node_id, context, graph, logs_root) -> Outcome`.
3. Pass the runner through `HandlerRegistry` constructor so `ParallelHandler` and `ManagerLoopHandler` receive it.

---

## The Fix

### Task 1: Add `_run_from()` method to PipelineEngine

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`

**Step 1: Write the failing test**

Create `modules/loop-pipeline/tests/test_subgraph_runner.py`:

```python
"""Tests for subgraph runner (H-11).

Validates that PipelineEngine._run_from() can execute a subgraph
starting from a specified node and return the final outcome.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


class CountingHandler:
    """Handler that counts calls and always succeeds."""

    def __init__(self):
        self.call_counts: dict[str, int] = {}

    async def execute(self, node, context, graph, logs_root):
        self.call_counts[node.id] = self.call_counts.get(node.id, 0) + 1
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Executed {node.id}",
        )


def _make_subgraph():
    """Build a graph: start -> a -> b -> done.

    _run_from("a") should execute a, then b, then hit done (exit).
    """
    return Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "a": Node(id="a", shape="box", prompt="Step A"),
            "b": Node(id="b", shape="box", prompt="Step B"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            Edge(from_node="a", to_node="b"),
            Edge(from_node="b", to_node="done"),
        ],
    )


@pytest.mark.asyncio
async def test_run_from_executes_subgraph(tmp_path):
    """_run_from('a') should execute a, b, then stop at exit."""
    graph = _make_subgraph()
    counting = CountingHandler()
    registry = HandlerRegistry()
    registry.register("codergen", counting)

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    # Initialize context (normally done in run())
    engine._initialize_context(goal="test")

    outcome = await engine._run_from("a")

    assert outcome.status == StageStatus.SUCCESS
    # Both a and b should have been executed
    assert counting.call_counts.get("a") == 1
    assert counting.call_counts.get("b") == 1


@pytest.mark.asyncio
async def test_run_from_with_isolated_context(tmp_path):
    """_run_from with a separate context should not pollute the engine context."""
    graph = _make_subgraph()
    counting = CountingHandler()
    registry = HandlerRegistry()
    registry.register("codergen", counting)

    main_context = PipelineContext()
    main_context.set("main_key", "main_value")

    engine = PipelineEngine(
        graph=graph,
        context=main_context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    engine._initialize_context(goal="test")

    branch_context = main_context.clone()
    outcome = await engine._run_from("a", context=branch_context)

    assert outcome.status == StageStatus.SUCCESS
    # Branch context should have node outcomes; main should not
    assert "main_key" in main_context.snapshot()


@pytest.mark.asyncio
async def test_run_from_stops_at_dead_end(tmp_path):
    """_run_from should return last outcome when no more edges exist."""
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "a": Node(id="a", shape="box", prompt="Only node"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            # a has no outgoing edges -- dead end
        ],
    )
    counting = CountingHandler()
    registry = HandlerRegistry()
    registry.register("codergen", counting)

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    engine._initialize_context(goal="test")

    outcome = await engine._run_from("a")

    assert outcome.status == StageStatus.SUCCESS
    assert counting.call_counts.get("a") == 1
```

**Step 2: Run to verify tests fail**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/test_subgraph_runner.py -xvs`

Expected: FAIL -- `AttributeError: 'PipelineEngine' object has no attribute '_run_from'`

**Step 3: Commit failing tests**

```bash
cd /path/to/amplifier-bundle-attractor
git add modules/loop-pipeline/tests/test_subgraph_runner.py
git commit -m "test: add subgraph runner tests (H-11)"
```

---

### Task 2: Implement `_run_from()` on PipelineEngine

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`

**Step 1: Add the `_run_from()` method**

Add this method to `PipelineEngine`, after the `run()` method (before `_initialize_context`):

```python
    async def _run_from(
        self,
        start_node_id: str,
        *,
        context: PipelineContext | None = None,
    ) -> Outcome:
        """Execute a subgraph starting from the given node.

        Walks from *start_node_id* until an exit node is reached, no
        outgoing edges exist, or the node is not in the graph.

        This is the subgraph runner used by ParallelHandler and
        ManagerLoopHandler to execute branches and child subgraphs.

        Args:
            start_node_id: Node ID to begin execution from.
            context: Optional isolated context for this subgraph run.
                     If None, uses the engine's main context.

        Returns:
            The final Outcome of the subgraph execution.
        """
        ctx = context if context is not None else self.context

        if start_node_id not in self.graph.nodes:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Subgraph start node '{start_node_id}' not found in graph",
            )

        current_node = self.graph.nodes[start_node_id]
        last_outcome: Outcome | None = None

        # Safety bound to prevent infinite loops
        max_steps = len(self.graph.nodes) * self._MAX_GOAL_GATE_RETRIES

        for _step in range(max_steps):
            # Check for terminal node (exit)
            if current_node.shape == "Msquare":
                return last_outcome or Outcome(
                    status=StageStatus.SUCCESS,
                    notes="Subgraph reached exit node",
                )

            # Execute node handler (no retry policy in subgraph -- parent manages retries)
            handler = self.handler_registry.get(current_node)

            # Skip start nodes (no-op)
            if current_node.shape == "Mdiamond":
                outcome = Outcome(status=StageStatus.SUCCESS)
            else:
                try:
                    outcome = await handler.execute(
                        current_node, ctx, self.graph, self.logs_root
                    )
                except Exception as exc:
                    return Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=f"Subgraph node '{current_node.id}' raised: {exc}",
                    )

            last_outcome = outcome

            # Apply context updates
            if outcome.context_updates:
                ctx.update(outcome.context_updates)
            ctx.set("outcome", outcome.status.value)
            if outcome.preferred_label:
                ctx.set("preferred_label", outcome.preferred_label)

            # Select next edge
            edge = select_edge(current_node.id, outcome, ctx, self.graph)
            if edge is None:
                # No outgoing edge -- subgraph is complete
                return outcome

            current_node = self.graph.nodes[edge.to_node]

        # Safety bound exceeded
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=f"Subgraph exceeded {max_steps} steps (safety bound)",
        )
```

**Step 2: Run the tests**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/test_subgraph_runner.py -xvs`

Expected: All PASS

**Step 3: Run full suite**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/ -x --tb=short -q`

Expected: All PASS

**Step 4: Commit**

```bash
cd /path/to/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py
git commit -m "feat: add PipelineEngine._run_from() for subgraph execution (H-11)"
```

---

### Task 3: Create runner closure and wire through HandlerRegistry

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py` lines 265-276

**Step 1: Write the failing integration test**

Add to `test_subgraph_runner.py`:

```python
@pytest.mark.asyncio
async def test_parallel_handler_uses_wired_subgraph_runner(tmp_path):
    """Integration: ParallelHandler receives a real subgraph_runner and uses it."""
    import json
    from amplifier_module_loop_pipeline import PipelineOrchestrator

    dot = '''
    digraph test {
        graph [goal="test parallel"]
        start [shape=Mdiamond]
        par [shape=component]
        b1 [shape=box, prompt="Branch 1"]
        b2 [shape=box, prompt="Branch 2"]
        fan_in [shape=tripleoctagon]
        done [shape=Msquare]

        start -> par
        par -> b1
        par -> b2
        b1 -> fan_in
        b2 -> fan_in
        fan_in -> done
    }
    '''
    orchestrator = PipelineOrchestrator({"dot_source": dot})
    result_json = await orchestrator.execute(
        prompt="test parallel",
        context=None,
        providers={},
        tools={},
        hooks=None,
    )
    result = json.loads(result_json)
    # Pipeline should complete -- parallel branches should have run
    assert result["status"] in ("success", "partial_success")
```

**Step 2: Update `PipelineOrchestrator.execute()` to create and pass subgraph_runner**

In `__init__.py`, find the section where the engine is created (around lines 265-276):

```python
        # 7. Register handlers
        registry = HandlerRegistry(backend=backend)

        # 8. Run the engine
        engine = PipelineEngine(
            graph=graph,
            context=pipeline_context,
            handler_registry=registry,
            logs_root=logs_root,
            hooks=hooks,
        )
        outcome = await engine.run(goal=prompt or None)
```

Replace with:

```python
        # 7. Create engine first (handlers need its _run_from method)
        # Use a placeholder registry, then replace after wiring
        engine = PipelineEngine(
            graph=graph,
            context=pipeline_context,
            handler_registry=HandlerRegistry(backend=backend),  # temp
            logs_root=logs_root,
            hooks=hooks,
        )

        # 8. Create subgraph runner closure that delegates to engine._run_from
        async def subgraph_runner(
            node_id: str,
            branch_context: PipelineContext,
            _graph: Any,
            _logs_root: str,
        ) -> Outcome:
            """Execute a subgraph branch via the engine."""
            return await engine._run_from(node_id, context=branch_context)

        # 9. Register handlers with the subgraph runner wired in
        registry = HandlerRegistry(
            backend=backend,
            subgraph_runner=subgraph_runner,
            hooks=hooks,
        )
        engine.handler_registry = registry

        # 10. Run the engine
        outcome = await engine.run(goal=prompt or None)
```

**Step 3: Run the integration test**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/test_subgraph_runner.py::test_parallel_handler_uses_wired_subgraph_runner -xvs`

Expected: PASS

**Step 4: Run the full test suite**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/ -x --tb=short -q`

Expected: All PASS

**Step 5: Commit**

```bash
cd /path/to/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py
git add modules/loop-pipeline/tests/test_subgraph_runner.py
git commit -m "feat: wire subgraph_runner to ParallelHandler and ManagerLoopHandler (H-11)

Creates a subgraph_runner closure in PipelineOrchestrator.execute()
that delegates to PipelineEngine._run_from(). Passes it through
HandlerRegistry to ParallelHandler and ManagerLoopHandler.

Parallel branches and manager loop child subgraphs now execute
real graph traversal instead of returning simulation results."
```

---

### Task 4: Write test for ManagerLoopHandler with wired runner

**Files:**
- Modify: `modules/loop-pipeline/tests/test_subgraph_runner.py`

**Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_manager_loop_uses_wired_subgraph_runner(tmp_path):
    """ManagerLoopHandler receives and uses a real subgraph_runner."""
    from amplifier_module_loop_pipeline.handlers.manager_loop import ManagerLoopHandler

    calls = []

    async def mock_runner(node_id, ctx, graph, logs_root):
        calls.append(node_id)
        return Outcome(status=StageStatus.SUCCESS, notes="child done")

    handler = ManagerLoopHandler(subgraph_runner=mock_runner)

    graph = Graph(
        name="test",
        nodes={
            "mgr": Node(
                id="mgr",
                shape="house",
                attrs={"manager.max_cycles": 1},
            ),
            "child": Node(id="child", shape="box", prompt="child work"),
        },
        edges=[
            Edge(from_node="mgr", to_node="child"),
        ],
    )

    ctx = PipelineContext()
    outcome = await handler.execute(graph.nodes["mgr"], ctx, graph, str(tmp_path))

    assert outcome.status == StageStatus.SUCCESS
    assert "child" in calls
```

**Step 2: Run test**

Run: `cd /path/to/amplifier-bundle-attractor && python -m pytest modules/loop-pipeline/tests/test_subgraph_runner.py::test_manager_loop_uses_wired_subgraph_runner -xvs`

Expected: PASS (ManagerLoopHandler already handles `subgraph_runner` correctly when it's not None)

**Step 3: Commit**

```bash
cd /path/to/amplifier-bundle-attractor
git add modules/loop-pipeline/tests/test_subgraph_runner.py
git commit -m "test: add ManagerLoopHandler subgraph runner integration test (H-11)"
```

---

## Design Notes

### Why a closure instead of passing the engine directly?

The `SubgraphRunner` type alias (defined in both `parallel.py` and `manager_loop.py`) is:

```python
SubgraphRunner = Callable[
    [str, PipelineContext, Graph, str],
    Coroutine[Any, Any, Outcome],
]
```

A closure that captures the engine instance matches this signature naturally:

```python
async def subgraph_runner(node_id, branch_context, _graph, _logs_root):
    return await engine._run_from(node_id, context=branch_context)
```

The `_graph` and `_logs_root` params are accepted but ignored since the engine already has the graph and logs_root. This keeps the handler interface unchanged.

### Why create the engine before the registry?

The engine needs the registry to execute handlers, and the registry needs the subgraph_runner closure which captures the engine. This creates a circular dependency. The solution:
1. Create engine with a temporary registry
2. Create the runner closure (captures engine)
3. Create the real registry with the runner
4. Replace `engine.handler_registry` with the real registry

This is safe because the engine doesn't use the registry until `run()` is called.

### Subgraph scope

`_run_from()` walks the full graph from the given start node. It stops at:
- Exit nodes (shape=Msquare)
- Dead ends (no outgoing edges after edge selection)
- Safety bound exceeded

For parallel branches, this means each branch walks its own path through the graph until it reaches a fan-in node or exit. The fan-in node itself is NOT executed by the branch -- the parent parallel handler returns and the engine's main loop advances to the fan-in.

---

## Backward Compatibility

- **Low risk.** `ParallelHandler` previously returned simulation results when `subgraph_runner` was `None`. Now it gets a real runner and executes real subgraphs. This is a behavioral change but is the correct spec behavior.
- `ManagerLoopHandler` previously returned `FAIL` when `subgraph_runner` was `None`. Now it gets a real runner. Same argument.
- Existing tests that construct `HandlerRegistry()` without `subgraph_runner` continue to work -- handlers fall back to simulation/fail mode as before.
- Tests that construct `PipelineEngine` directly won't have a subgraph_runner wired. They continue to work for non-parallel/non-manager graphs.

## Dependencies

- Depends on `edge_selection.py` (already exists) for `select_edge()` used in `_run_from()`.
- No circular import issues -- `_run_from()` is a method on `PipelineEngine` which already imports `select_edge`.
- If 1B6 (pipeline events) is also being implemented, ensure `hooks` is passed through `HandlerRegistry` as specified there.

## PR Details

- **Branch:** `track1/1b7-subgraph-runner`
- **Title:** `feat: wire subgraph_runner for parallel/manager handlers (H-11)`
- **Labels:** `track1`, `pipeline`, `spec-compliance`
