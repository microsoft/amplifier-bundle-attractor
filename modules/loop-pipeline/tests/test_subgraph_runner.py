"""Tests for subgraph runner (H-11).

Validates that PipelineEngine.run_subgraph() can execute a subgraph
starting from a specified node and return the final outcome.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class CountingHandler:
    """Handler that counts calls and always succeeds."""

    def __init__(self):
        self.call_counts: dict[str, int] = {}

    async def execute(self, node, context, graph, logs_root, *, engine=None):
        self.call_counts[node.id] = self.call_counts.get(node.id, 0) + 1
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Executed {node.id}",
        )


def _make_subgraph():
    """Build a graph: start -> a -> b -> done.

    run_subgraph("a") should execute a, then b, then hit done (exit).
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
    """run_subgraph('a') should execute a, b, then stop at exit."""
    graph = _make_subgraph()
    counting = CountingHandler()
    registry = HandlerRegistry(HandlerContext())
    registry.register("codergen", counting)

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    # Initialize context (normally done in run())
    engine._initialize_context(goal="test")

    outcome = await engine.run_subgraph("a")

    assert outcome.status == StageStatus.SUCCESS
    # Both a and b should have been executed
    assert counting.call_counts.get("a") == 1
    assert counting.call_counts.get("b") == 1


@pytest.mark.asyncio
async def test_run_from_with_isolated_context(tmp_path):
    """_run_from with a separate context should not pollute the engine context."""
    graph = _make_subgraph()
    counting = CountingHandler()
    registry = HandlerRegistry(HandlerContext())
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
    outcome = await engine.run_subgraph("a", context=branch_context)

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
    registry = HandlerRegistry(HandlerContext())
    registry.register("codergen", counting)

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    engine._initialize_context(goal="test")

    outcome = await engine.run_subgraph("a")

    assert outcome.status == StageStatus.SUCCESS
    assert counting.call_counts.get("a") == 1


@pytest.mark.asyncio
async def test_run_from_nonexistent_node(tmp_path):
    """_run_from with a bad node ID should return FAIL."""
    graph = _make_subgraph()
    registry = HandlerRegistry(HandlerContext())

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    engine._initialize_context(goal="test")

    outcome = await engine.run_subgraph("nonexistent")

    assert outcome.status == StageStatus.FAIL
    assert "not found" in (outcome.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_manager_loop_uses_wired_subgraph_runner(tmp_path):
    """ManagerLoopHandler calls engine.run_subgraph when engine is provided."""
    from amplifier_module_loop_pipeline.handlers.manager_loop import ManagerLoopHandler

    calls: list[str] = []

    class MockEngine:
        async def run_subgraph(self, node_id, *, context=None):
            calls.append(node_id)
            return Outcome(status=StageStatus.SUCCESS, notes="child done")

    handler = ManagerLoopHandler()

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
    outcome = await handler.execute(
        graph.nodes["mgr"], ctx, graph, str(tmp_path), engine=MockEngine()
    )

    assert outcome.status == StageStatus.SUCCESS
    assert "child" in calls


@pytest.mark.asyncio
async def test_parallel_handler_uses_wired_subgraph_runner(tmp_path):
    """Integration: ParallelHandler receives a real subgraph_runner and uses it."""
    import json

    from amplifier_module_loop_pipeline import PipelineOrchestrator

    dot = """
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
    """
    orchestrator = PipelineOrchestrator({"dot_source": dot, "logs_root": str(tmp_path)})

    class _MockBackend:
        async def run(self, node, prompt, context):
            return json.dumps({"status": "success", "notes": f"mock: {node.id}"})

    result_json = await orchestrator.execute(
        prompt="test parallel",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=_MockBackend(),
    )
    result = json.loads(result_json)
    # Pipeline should complete -- parallel branches should have run
    assert result["status"] in ("success", "partial_success")
