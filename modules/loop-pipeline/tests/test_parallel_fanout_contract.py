"""Lock the parallel fan-out contract for shape=component and shape=parallelogram.

Spec coverage: §2.8 (shape-to-handler mapping), §3.3 (select_edge algorithm),
§3.8 (concurrency model), §4.8 (ParallelHandler).

Two contract assertions:

1. shape=component + unconditional outgoing edges → fan-out (ParallelHandler
   dispatches ALL outgoing edges concurrently).  The spec (§3.8) states:
   "Parallelism exists within specific node handlers (parallel, parallel.fan_in)
   that manage concurrent execution internally."

2. shape=parallelogram + unconditional outgoing edges → single-edge selection
   (ToolHandler executes the tool_command; then the engine's 5-step select_edge
   algorithm (§3.3) runs and returns exactly ONE edge: best_by_weight_then_lexical,
   which is the lexically-first target node ID when weights are equal).

These two behaviors are adjacent and easily confused.  Test 2 fills a coverage
gap: without it, authoring a parallelogram fork expecting fan-out will silently
discard the second branch and complete on half the data.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_wired_engine(
    graph: Graph,
    backend: object,
    logs_root: str,
) -> PipelineEngine:
    """Create a PipelineEngine with subgraph_runner wired to engine._run_from.

    Required when testing shape=component nodes (ParallelHandler needs this
    closure to execute branches as proper subgraphs).
    """
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(backend=backend),
        logs_root=logs_root,
    )

    async def subgraph_runner(
        node_id: str,
        branch_context: PipelineContext,
        _graph: Graph,
        _logs_root: str,
    ) -> object:
        return await engine._run_from(node_id, context=branch_context)

    registry = HandlerRegistry(backend=backend, subgraph_runner=subgraph_runner)
    engine.handler_registry = registry
    return engine


# ---------------------------------------------------------------------------
# Test 1: shape=component fans out ALL unconditional outgoing edges (§3.8, §4.8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_shape_fans_out_unconditional_edges(tmp_path):
    """shape=component with unconditional outgoing edges MUST fan out to all children.

    Per spec §3.8: "Parallelism exists within specific node handlers (parallel,
    parallel.fan_in) that manage concurrent execution internally."  The parallel
    handler (shape=component) dispatches ALL outgoing edges concurrently — not via
    the 5-step select_edge algorithm, but directly through graph.outgoing_edges().

    This test: start → EvalParallel[component] → RunBaseline, RunVariant (both
    unconditional) → EvalGather[tripleoctagon] → Continue → exit.
    Both RunBaseline and RunVariant must execute.
    """
    execution_counts: dict[str, int] = {}

    class CountingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            return "done"

    graph = Graph(
        name="test-component-2branch-fanout",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "EvalParallel": Node(id="EvalParallel", shape="component"),
            "RunBaseline": Node(id="RunBaseline", shape="box", prompt="Run baseline"),
            "RunVariant": Node(id="RunVariant", shape="box", prompt="Run variant"),
            "EvalGather": Node(id="EvalGather", shape="tripleoctagon"),
            "Continue": Node(id="Continue", shape="box", prompt="Continue"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="EvalParallel"),
            # Fan-out: 2 unconditional branches
            Edge(from_node="EvalParallel", to_node="RunBaseline"),
            Edge(from_node="EvalParallel", to_node="RunVariant"),
            # Fan-in
            Edge(from_node="RunBaseline", to_node="EvalGather"),
            Edge(from_node="RunVariant", to_node="EvalGather"),
            Edge(from_node="EvalGather", to_node="Continue"),
            Edge(from_node="Continue", to_node="exit"),
        ],
    )

    engine = await _make_wired_engine(graph, CountingBackend(), str(tmp_path))
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline did not complete successfully: {outcome.status}, "
        f"{outcome.failure_reason}"
    )

    # Both branches MUST have executed (the whole point of shape=component)
    assert execution_counts.get("RunBaseline", 0) == 1, (
        f"RunBaseline should have executed exactly once via ParallelHandler, "
        f"got {execution_counts.get('RunBaseline', 0)}"
    )
    assert execution_counts.get("RunVariant", 0) == 1, (
        f"RunVariant should have executed exactly once via ParallelHandler, "
        f"got {execution_counts.get('RunVariant', 0)}"
    )


# ---------------------------------------------------------------------------
# Test 2: shape=parallelogram picks exactly ONE edge (§3.3 select_edge — NOT fan-out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallelogram_shape_does_not_fan_out_unconditional_edges(tmp_path):
    """shape=parallelogram + unconditional outgoing edges selects EXACTLY ONE target.

    Per spec §3.3, select_edge() returns one edge.  The 5-step algorithm's final
    step for unconditional edges is best_by_weight_then_lexical(edges) → edges[0].
    When two edges have equal weight, the lexically-first target node ID wins.

    "LeafA" < "LeafB" alphabetically → LeafA runs, LeafB is silently ignored.

    This is spec-compliant behavior, not a bug.  Authors who want fan-out from
    a gate-style fork node MUST use shape=component (§2.8, §3.8).

    Coverage gap: without this test, authoring a parallelogram expecting parallel
    dispatch produces a pipeline that silently completes on half the data — no
    error, no warning, wrong behavior.
    """
    execution_counts: dict[str, int] = {}

    class CountingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            return "done"

    # shape=parallelogram → ToolHandler; runs tool_command then selects ONE edge.
    graph = Graph(
        name="test-parallelogram-single-edge-selection",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(
                id="gate",
                shape="parallelogram",
                attrs={"tool_command": "printf 'gate_done'"},
            ),
            "LeafA": Node(id="LeafA", shape="box", prompt="Leaf A"),
            "LeafB": Node(id="LeafB", shape="box", prompt="Leaf B"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="LeafA"),  # unconditional
            Edge(from_node="gate", to_node="LeafB"),  # unconditional
            Edge(from_node="LeafA", to_node="exit"),
            Edge(from_node="LeafB", to_node="exit"),
        ],
    )

    # No subgraph_runner needed — ToolHandler and LLM nodes don't use it.
    context = PipelineContext()
    registry = HandlerRegistry(backend=CountingBackend())
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline did not complete: {outcome.status}, {outcome.failure_reason}"
    )

    # Per spec §3.3 step 5: select_edge returns the lexically-first unconditional edge.
    # "LeafA" < "LeafB" → LeafA runs.
    assert execution_counts.get("LeafA", 0) == 1, (
        f"LeafA (lexically first, 'LeafA' < 'LeafB') must run exactly once, "
        f"got {execution_counts.get('LeafA', 0)}"
    )
    # LeafB must NOT run — there is no fan-out for parallelogram + unconditional edges.
    assert execution_counts.get("LeafB", 0) == 0, (
        f"LeafB must NOT run (spec §3.3: select_edge returns ONE edge; parallelogram "
        f"is NOT a fan-out node), got {execution_counts.get('LeafB', 0)}"
    )
