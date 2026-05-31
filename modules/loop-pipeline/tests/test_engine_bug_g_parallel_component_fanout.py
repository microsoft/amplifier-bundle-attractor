"""Regression tests for Bug G — dual parallel-fan-out paths in the engine.

Spec coverage: PAR-001–013, EXEC-001–018, Sections 4.8, 2.8.

Root cause (confirmed pre-fix):
  When the engine encounters a shape=component node, it dispatches to
  ParallelHandler which correctly fans out all branches via _run_from().
  Then at Step 5 of the main loop, select_all_matching_edges() returns all
  outgoing edges again, triggering _execute_parallel_fan_out() a SECOND time.
  This means each branch executes TWICE — once inside ParallelHandler's
  subgraph runner, once directly in _execute_parallel_fan_out().

Fix (engine.py Step 5):
  Gate the engine-level multi-edge fan-out on shape != "component".  Component
  nodes have already been handled by ParallelHandler; the engine should find
  the fan-in node and route to it rather than re-executing the branches.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_3branch_component_graph() -> Graph:
    """Build a graph: start → ProposeFork[component] → 3 branches → ProposeJoin[tripleoctagon] → exit."""
    return Graph(
        name="test-component-fanout",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "ProposeFork": Node(id="ProposeFork", shape="component"),
            "branch_haiku": Node(id="branch_haiku", shape="box", prompt="Haiku branch"),
            "branch_opus": Node(id="branch_opus", shape="box", prompt="Opus branch"),
            "branch_sonnet": Node(
                id="branch_sonnet", shape="box", prompt="Sonnet branch"
            ),
            "ProposeJoin": Node(id="ProposeJoin", shape="tripleoctagon"),
            "Summarize": Node(id="Summarize", shape="box", prompt="Summarize"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="ProposeFork"),
            # Fan-out: 3 branches
            Edge(from_node="ProposeFork", to_node="branch_haiku"),
            Edge(from_node="ProposeFork", to_node="branch_opus"),
            Edge(from_node="ProposeFork", to_node="branch_sonnet"),
            # All converge at the fan-in
            Edge(from_node="branch_haiku", to_node="ProposeJoin"),
            Edge(from_node="branch_opus", to_node="ProposeJoin"),
            Edge(from_node="branch_sonnet", to_node="ProposeJoin"),
            # Continue after fan-in
            Edge(from_node="ProposeJoin", to_node="Summarize"),
            Edge(from_node="Summarize", to_node="exit"),
        ],
    )


async def _make_wired_engine(
    graph: Graph,
    backend: object,
    logs_root: str,
) -> PipelineEngine:
    """Create a PipelineEngine. Engine passes itself to handlers via execute(engine=self).

    No subgraph_runner closure needed — ParallelHandler receives the engine
    directly via execute(engine=...) and calls engine.run_subgraph().
    """
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs_root,
    )


# ---------------------------------------------------------------------------
# Bug G regression: each branch must execute EXACTLY ONCE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_fanout_each_branch_executes_exactly_once(tmp_path):
    """REGRESSION: component node must NOT trigger a second engine-level fan-out.

    With the Bug G fix: each branch executes exactly once (via ParallelHandler).
    Without the fix: each branch executes twice (ParallelHandler + _execute_parallel_fan_out).
    """
    execution_counts: dict[str, int] = {}

    class CountingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            return "done"

    graph = _make_3branch_component_graph()
    engine = await _make_wired_engine(graph, CountingBackend(), str(tmp_path))
    outcome = await engine.run()

    # Pipeline must complete
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline did not complete successfully: {outcome.status}, {outcome.failure_reason}"
    )

    # BUG G: each branch must execute EXACTLY ONCE, not twice
    assert execution_counts.get("branch_haiku", 0) == 1, (
        f"branch_haiku executed {execution_counts.get('branch_haiku', 0)} times "
        f"(expected 1 — dual-fan-out bug causes 2)"
    )
    assert execution_counts.get("branch_opus", 0) == 1, (
        f"branch_opus executed {execution_counts.get('branch_opus', 0)} times "
        f"(expected 1 — dual-fan-out bug causes 2)"
    )
    assert execution_counts.get("branch_sonnet", 0) == 1, (
        f"branch_sonnet executed {execution_counts.get('branch_sonnet', 0)} times "
        f"(expected 1 — dual-fan-out bug causes 2)"
    )


@pytest.mark.asyncio
async def test_component_fanout_all_three_branches_execute(tmp_path):
    """All three parallel branches must execute (AND-join, not OR-join).

    With the Bug G fix: all 3 branches run and parallel.results has 3 entries.
    Without the fix: still 3 execute (just doubled), so this test is GREEN before fix too.
    Kept as a companion assertion for the record.
    """
    executed: list[str] = []

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            executed.append(node.id)
            return "done"

    graph = _make_3branch_component_graph()
    engine = await _make_wired_engine(graph, TrackingBackend(), str(tmp_path))
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "branch_haiku" in executed
    assert "branch_opus" in executed
    assert "branch_sonnet" in executed


@pytest.mark.asyncio
async def test_component_fanout_parallel_results_has_three_entries(tmp_path):
    """parallel.results must have exactly 3 entries after a 3-branch component node.

    Without the fix: ParallelHandler writes 3 entries to parallel.results, then
    _execute_parallel_fan_out runs the branches again (but doesn't overwrite parallel.results).
    So this test passes both before and after the fix — confirming schema integrity.
    Documented here for completeness.
    """

    class SimpleBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            return "done"

    graph = _make_3branch_component_graph()
    engine = await _make_wired_engine(graph, SimpleBackend(), str(tmp_path))
    await engine.run()

    results = engine.context.get("parallel.results")
    assert results is not None, "parallel.results was not set in context"
    assert len(results) == 3, (
        f"Expected 3 parallel results, got {len(results)}: {results}"
    )
    node_ids = {r["node_id"] for r in results}
    assert "branch_haiku" in node_ids
    assert "branch_opus" in node_ids
    assert "branch_sonnet" in node_ids


@pytest.mark.asyncio
async def test_component_fanout_fan_in_node_executes_after_all_branches(tmp_path):
    """ProposeJoin (tripleoctagon / fan_in) must execute after all branches complete.

    And the Summarize node after the fan-in must also execute — verifying the
    engine correctly advances past the component/tripleoctagon pair.
    """
    executed: list[str] = []

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            executed.append(node.id)
            return "done"

    graph = _make_3branch_component_graph()
    engine = await _make_wired_engine(graph, TrackingBackend(), str(tmp_path))
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

    # Fan-in must execute
    assert "ProposeJoin" not in executed, (
        "FanInHandler is not a codergen node — its handler doesn't call backend.run(). "
        "Check via completed_nodes instead."
    )
    assert "ProposeJoin" in engine.completed_nodes, (
        "ProposeJoin (tripleoctagon fan-in) was never executed"
    )

    # Node after fan-in must also execute
    assert "Summarize" in executed, (
        "Summarize node after fan-in was never executed — engine didn't advance past fan-in"
    )


@pytest.mark.asyncio
async def test_non_component_multi_edge_fanout_still_works(tmp_path):
    """Engine-level multi-edge fan-out (shape=box, not component) must be unaffected.

    The existing _execute_parallel_fan_out path is used for non-component nodes
    with multiple matching outgoing edges. The fix must NOT break this path.
    """
    executed: list[str] = []

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            executed.append(node.id)
            return "done"

    # Non-component node (box) with multiple same-condition outgoing edges
    graph = Graph(
        name="test-box-fanout",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "b1": Node(id="b1", shape="box", prompt="B1"),
            "b2": Node(id="b2", shape="box", prompt="B2"),
            "merge": Node(id="merge", shape="box", prompt="Merge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            Edge(from_node="check", to_node="b1", condition="outcome=success"),
            Edge(from_node="check", to_node="b2", condition="outcome=success"),
            Edge(from_node="b1", to_node="merge"),
            Edge(from_node="b2", to_node="merge"),
            Edge(from_node="merge", to_node="exit"),
        ],
    )

    # No subgraph_runner needed for box nodes (uses engine-level fan-out)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=TrackingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "b1" in executed
    assert "b2" in executed
    assert "merge" in executed
