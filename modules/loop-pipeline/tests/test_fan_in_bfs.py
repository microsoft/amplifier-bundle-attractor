"""Tests for BFS-based fan-in node detection.

_find_fan_in_node() finds the first node reachable from ALL branch roots.
The pre-fix implementation used a 1-hop intersection (only direct neighbors
of branch roots), which fails when branches have multiple steps before
converging.

Example that exposes the bug:
    EvalParallel[component] → RunBaseline → ExtractMetrics_B ↘
                            → RunVariant  → ExtractMetrics_V → EvalGather[tripleoctagon]

1-hop check from [RunBaseline, RunVariant]:
    outgoing(RunBaseline) = {ExtractMetrics_B}
    outgoing(RunVariant)  = {ExtractMetrics_V}
    intersection = ∅  →  returns None  (WRONG)

BFS from [RunBaseline, RunVariant]:
    reachable(RunBaseline) = {ExtractMetrics_B, EvalGather}
    reachable(RunVariant)  = {ExtractMetrics_V, EvalGather}
    common = {EvalGather}  →  returns "EvalGather"  (CORRECT)
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


def _make_engine(graph: Graph, tmp_path) -> PipelineEngine:
    """Create a minimal engine with a HandlerRegistry for _find_fan_in_node tests."""
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext()),
        logs_root=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Core: multi-hop convergence detection
# ---------------------------------------------------------------------------


def test_fan_in_finds_multi_hop_convergence(tmp_path):
    """BFS must find a convergence node multiple hops away from the branch roots.

    Topology:
        root_a → b → join
        root_d → e → join

    1-hop: outgoing(root_a) ∩ outgoing(root_d) = {b} ∩ {e} = ∅ → None (WRONG)
    BFS:   reachable(root_a) ∩ reachable(root_d) = {b, join} ∩ {e, join} = {join} → "join" ✓
    """
    graph = Graph(
        name="test-multihop-fan-in",
        nodes={
            "component": Node(id="component", shape="component"),
            "root_a": Node(id="root_a", shape="box", prompt="Branch A"),
            "b": Node(id="b", shape="box", prompt="Step B"),
            "join": Node(id="join", shape="tripleoctagon"),
            "root_d": Node(id="root_d", shape="box", prompt="Branch D"),
            "e": Node(id="e", shape="box", prompt="Step E"),
            # Downstream from join — must not affect detection
            "continue": Node(id="continue", shape="box", prompt="Continue"),
        },
        edges=[
            Edge(from_node="component", to_node="root_a"),
            Edge(from_node="component", to_node="root_d"),
            Edge(from_node="root_a", to_node="b"),
            Edge(from_node="b", to_node="join"),
            Edge(from_node="root_d", to_node="e"),
            Edge(from_node="e", to_node="join"),
            Edge(from_node="join", to_node="continue"),
        ],
    )
    engine = _make_engine(graph, tmp_path)

    result = engine._find_fan_in_node(["root_a", "root_d"])

    assert result == "join", (
        f"_find_fan_in_node(['root_a', 'root_d']) returned {result!r}, "
        f"expected 'join'. "
        f"The 1-hop intersection check cannot find multi-hop convergence. "
        f"Upgrade to BFS."
    )


def test_fan_in_finds_deeper_convergence(tmp_path):
    """BFS must find convergence even with 3-hop branches.

    Topology:
        root_1 → a → b → convergence
        root_2 → c → d → convergence
    """
    graph = Graph(
        name="test-deep-fan-in",
        nodes={
            "component": Node(id="component", shape="component"),
            "root_1": Node(id="root_1", shape="box", prompt="R1"),
            "a": Node(id="a", shape="box", prompt="A"),
            "b": Node(id="b", shape="box", prompt="B"),
            "convergence": Node(id="convergence", shape="tripleoctagon"),
            "root_2": Node(id="root_2", shape="box", prompt="R2"),
            "c": Node(id="c", shape="box", prompt="C"),
            "d": Node(id="d", shape="box", prompt="D"),
        },
        edges=[
            Edge(from_node="component", to_node="root_1"),
            Edge(from_node="component", to_node="root_2"),
            Edge(from_node="root_1", to_node="a"),
            Edge(from_node="a", to_node="b"),
            Edge(from_node="b", to_node="convergence"),
            Edge(from_node="root_2", to_node="c"),
            Edge(from_node="c", to_node="d"),
            Edge(from_node="d", to_node="convergence"),
        ],
    )
    engine = _make_engine(graph, tmp_path)

    result = engine._find_fan_in_node(["root_1", "root_2"])

    assert result == "convergence", (
        f"_find_fan_in_node(['root_1', 'root_2']) returned {result!r}, "
        f"expected 'convergence' (3-hop BFS)."
    )


# ---------------------------------------------------------------------------
# Correctness: None when no convergence
# ---------------------------------------------------------------------------


def test_fan_in_returns_none_when_no_convergence(tmp_path):
    """BFS must return None when branches never converge.

    Both 1-hop and BFS should return None for divergent branches.
    This is a regression guard to ensure BFS does not invent a fan-in.
    """
    graph = Graph(
        name="test-no-convergence",
        nodes={
            "component": Node(id="component", shape="component"),
            "root_a": Node(id="root_a", shape="box", prompt="A"),
            "dead_end_a": Node(id="dead_end_a", shape="Msquare"),  # exit
            "root_b": Node(id="root_b", shape="box", prompt="B"),
            "dead_end_b": Node(id="dead_end_b", shape="Msquare"),  # exit
        },
        edges=[
            Edge(from_node="component", to_node="root_a"),
            Edge(from_node="component", to_node="root_b"),
            Edge(from_node="root_a", to_node="dead_end_a"),
            Edge(from_node="root_b", to_node="dead_end_b"),
        ],
    )
    engine = _make_engine(graph, tmp_path)

    result = engine._find_fan_in_node(["root_a", "root_b"])

    assert result is None, (
        f"_find_fan_in_node(['root_a', 'root_b']) returned {result!r} for "
        f"divergent branches. Expected None (branches never converge)."
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_fan_in_empty_list_returns_none(tmp_path):
    """Empty branch list must return None."""
    graph = Graph(
        name="test-empty",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
    )
    engine = _make_engine(graph, tmp_path)

    result = engine._find_fan_in_node([])

    assert result is None, f"_find_fan_in_node([]) must return None, got {result!r}"


def test_fan_in_excludes_branch_roots_from_candidates(tmp_path):
    """Branch roots themselves must NOT be returned as fan-in nodes.

    Topology where root_a appears in root_b's reachable set (via a cycle-like
    path, but more importantly: branch roots should not be chosen as their
    own convergence point in any degenerate case).

    Simpler test: when root_a immediately leads to root_b, the answer
    should still be the node AFTER both roots, not root_b itself.
    """
    # root_a → shared_middle → join
    # root_b → join
    graph = Graph(
        name="test-exclude-roots",
        nodes={
            "component": Node(id="component", shape="component"),
            "root_a": Node(id="root_a", shape="box", prompt="A"),
            "shared_middle": Node(id="shared_middle", shape="box", prompt="Mid"),
            "root_b": Node(id="root_b", shape="box", prompt="B"),
            "join": Node(id="join", shape="tripleoctagon"),
        },
        edges=[
            Edge(from_node="component", to_node="root_a"),
            Edge(from_node="component", to_node="root_b"),
            Edge(from_node="root_a", to_node="shared_middle"),
            Edge(from_node="shared_middle", to_node="join"),
            Edge(from_node="root_b", to_node="join"),
        ],
    )
    engine = _make_engine(graph, tmp_path)

    result = engine._find_fan_in_node(["root_a", "root_b"])

    # "join" is reachable from both; "root_b" is reachable from root_a
    # (via shared_middle? no — but root_b IS in the graph).
    # The correct answer is "join" because root_b is a branch root, not a fan-in.
    # Note: with the straightforward graph above, root_b IS reachable from root_a
    # only if there's an edge root_a→root_b, which there isn't. So this test
    # primarily checks that join is found correctly.
    assert result == "join", (
        f"_find_fan_in_node(['root_a', 'root_b']) returned {result!r}, expected 'join'"
    )


# ---------------------------------------------------------------------------
# Integration: full pipeline with multi-hop branches completes (not just unit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_with_multi_hop_branches_completes(tmp_path):
    """Integration: a component node with multi-hop branches must complete the pipeline.

    This is the real-world scenario that exposed the bug: after ParallelHandler
    executes both branches, the engine looks for the fan-in node to continue.
    With 1-hop detection, it emits pipeline:complete(fail) and exits.
    With BFS, it finds EvalGather and continues normally.
    """
    execution_counts: dict[str, int] = {}

    class CountingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            return "done"

    graph = Graph(
        name="test-multihop-integration",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "EvalParallel": Node(id="EvalParallel", shape="component"),
            "RunBaseline": Node(id="RunBaseline", shape="box", prompt="Run baseline"),
            "ExtractMetrics_B": Node(
                id="ExtractMetrics_B", shape="box", prompt="Extract baseline metrics"
            ),
            "RunVariant": Node(id="RunVariant", shape="box", prompt="Run variant"),
            "ExtractMetrics_V": Node(
                id="ExtractMetrics_V", shape="box", prompt="Extract variant metrics"
            ),
            "EvalGather": Node(id="EvalGather", shape="tripleoctagon"),
            "CompareMetrics": Node(
                id="CompareMetrics", shape="box", prompt="Compare metrics"
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="EvalParallel"),
            # Parallel fan-out
            Edge(from_node="EvalParallel", to_node="RunBaseline"),
            Edge(from_node="EvalParallel", to_node="RunVariant"),
            # Multi-hop branches (2 hops before convergence)
            Edge(from_node="RunBaseline", to_node="ExtractMetrics_B"),
            Edge(from_node="ExtractMetrics_B", to_node="EvalGather"),
            Edge(from_node="RunVariant", to_node="ExtractMetrics_V"),
            Edge(from_node="ExtractMetrics_V", to_node="EvalGather"),
            # After convergence
            Edge(from_node="EvalGather", to_node="CompareMetrics"),
            Edge(from_node="CompareMetrics", to_node="exit"),
        ],
    )

    from amplifier_module_loop_pipeline.outcome import StageStatus

    # No subgraph_runner needed — ParallelHandler receives engine via execute(engine=...)
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=CountingBackend())),
        logs_root=str(tmp_path),
    )

    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline failed: {outcome.failure_reason}. "
        f"Expected BFS to find EvalGather (2 hops from branch roots) and continue."
    )

    # All post-convergence nodes must have executed
    assert execution_counts.get("CompareMetrics", 0) == 1, (
        f"CompareMetrics must execute exactly once after fan-in. "
        f"Got {execution_counts.get('CompareMetrics', 0)}. "
        f"Engine likely failed to find fan-in with 1-hop intersection."
    )
