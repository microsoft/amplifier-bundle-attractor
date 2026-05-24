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


# ---------------------------------------------------------------------------
# Test 3: non-component multi-edge fan-out respects max_parallel (CONC-001–004)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_component_fanout_respects_max_parallel(tmp_path):
    """_execute_parallel_fan_out bounds concurrency with the source node's max_parallel.

    The engine-level fan-out path (for non-component nodes with multiple matching
    conditional edges) must respect max_parallel exactly the same way ParallelHandler
    does for shape=component nodes.  This test:

    - Sets max_parallel=2 on the fan-out source node
    - Fans out to 4 branches
    - Uses a slow backend (asyncio.sleep) to create measurable overlap
    - Tracks peak concurrent execution with a shared counter
    - Asserts peak ≤ 2 (bounded) and all 4 branches eventually executed

    Spec coverage: CONC-001–004, §3.8 (concurrency model).
    """
    import asyncio

    peak_concurrent = 0
    current_concurrent = 0
    concurrent_lock = asyncio.Lock()
    execution_counts: dict[str, int] = {}

    class BoundedConcurrencyBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            nonlocal peak_concurrent, current_concurrent
            async with concurrent_lock:
                current_concurrent += 1
                if current_concurrent > peak_concurrent:
                    peak_concurrent = current_concurrent
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            # Yield so other branches can start; makes overlapping detectable
            await asyncio.sleep(0.05)
            async with concurrent_lock:
                current_concurrent -= 1
            return "done"

    # Fan-out source: shape=box (NOT component), max_parallel=2.
    # All 4 branches have condition="outcome=success" so they all match at once.
    # After the fan-out _execute_parallel_fan_out returns, the engine uses BFS
    # (_find_fan_in_node) to locate the convergence node and routes there.
    # NOTE: use a plain box for convergence, NOT a tripleoctagon — the tripleoctagon
    # (FanInHandler) reads parallel.results which is only populated by ParallelHandler
    # (shape=component).  The non-component fan-out path stores branch results in
    # context_updates only, so a regular box is the correct convergence node here.
    graph = Graph(
        name="test-bounded-non-component-fanout",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "FanOut": Node(
                id="FanOut",
                shape="box",
                prompt="fan out work",
                attrs={"max_parallel": "2"},  # caps concurrent branches at 2
            ),
            "Branch1": Node(id="Branch1", shape="box", prompt="branch 1"),
            "Branch2": Node(id="Branch2", shape="box", prompt="branch 2"),
            "Branch3": Node(id="Branch3", shape="box", prompt="branch 3"),
            "Branch4": Node(id="Branch4", shape="box", prompt="branch 4"),
            "Converge": Node(id="Converge", shape="box", prompt="after branches"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="FanOut"),
            # 4 conditional edges all matching outcome=success → multi-edge fan-out
            Edge(from_node="FanOut", to_node="Branch1", condition="outcome=success"),
            Edge(from_node="FanOut", to_node="Branch2", condition="outcome=success"),
            Edge(from_node="FanOut", to_node="Branch3", condition="outcome=success"),
            Edge(from_node="FanOut", to_node="Branch4", condition="outcome=success"),
            # Convergence edges: BFS finds Converge as common reachable node from
            # all 4 branch roots → engine routes there after fan-out completes
            Edge(from_node="Branch1", to_node="Converge"),
            Edge(from_node="Branch2", to_node="Converge"),
            Edge(from_node="Branch3", to_node="Converge"),
            Edge(from_node="Branch4", to_node="Converge"),
            Edge(from_node="Converge", to_node="exit"),
        ],
    )

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(backend=BoundedConcurrencyBackend()),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline did not complete: {outcome.status}, {outcome.failure_reason}"
    )

    # All 4 branches must have run
    for branch in ("Branch1", "Branch2", "Branch3", "Branch4"):
        assert execution_counts.get(branch, 0) == 1, (
            f"{branch} should have run exactly once, got "
            f"{execution_counts.get(branch, 0)}"
        )

    # Peak concurrent execution must not exceed max_parallel=2
    assert peak_concurrent <= 2, (
        f"Peak concurrent branches was {peak_concurrent}, expected ≤ 2 "
        f"(max_parallel=2 on FanOut node). "
        f"_execute_parallel_fan_out must respect the source node's max_parallel."
    )
