"""Lock the spec §3.7 fail-fast guard in select_edge.

Spec §3.7 (fail-fast intent): when a node returns FAIL, the engine looks for
an explicit failure path (condition="outcome=fail" edges, retry_target,
fallback_retry_target) and terminates if none exists.

These tests ensure that select_edge() enforces §3.7 by returning None for
FAIL outcomes when only unconditional edges are available — while keeping
SUCCESS routing and explicit failure-path routing unchanged.

Spec coverage: §3.7 (fail-fast intent), §3.3 (RETURN NONE), ESEL-002.
"""

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.edge_selection import select_edge
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


def _make_graph(edges: list[Edge]) -> Graph:
    """Build a minimal graph with the given edges."""
    nodes: dict[str, Node] = {}
    for e in edges:
        if e.from_node not in nodes:
            nodes[e.from_node] = Node(id=e.from_node)
        if e.to_node not in nodes:
            nodes[e.to_node] = Node(id=e.to_node)
    return Graph(name="test", nodes=nodes, edges=edges)


def test_fail_outcome_does_not_traverse_unconditional_edges():
    """Per spec §3.7 (fail-fast intent), a FAIL outcome must NOT traverse
    unconditional outgoing edges.

    Pipeline authors who want fail-forward use one of three explicit opt-in
    mechanisms (all preserved and tested below):
    - continue_on_fail="true" on the node (engine converts FAIL→SUCCESS before
      calling select_edge, so unconditional edges work normally)
    - runs_on=always or runs_on=failure (node-level skip gate override)
    - condition="outcome=fail" edges (explicit error routing — Step 1 above)

    The engine will then check retry_target / fallback_retry_target and
    terminate if neither is configured.
    """
    # Graph: N1 → N2 (unconditional, no condition)
    # Call select_edge(N1, FAIL_outcome, context, graph)
    # Assert: returns None (engine will then halt or use retry_target)
    edges = [Edge("N1", "N2")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.FAIL)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is None, (
        "FAIL outcome must NOT traverse unconditional outgoing edges "
        f"(spec §3.7 fail-fast). Expected None, got edge to "
        f"'{result.to_node if result else None}'"
    )


def test_success_outcome_traverses_unconditional_edges_as_before():
    """Belt-and-suspenders: confirm unconditional edges still work for SUCCESS.

    The fail-fast guard ONLY applies to FAIL outcomes.  SUCCESS must continue
    to traverse unconditional edges normally (steps 4 & 5 of spec §3.3).
    """
    # Same setup as above, SUCCESS outcome instead
    # Assert: returns N1->N2 edge
    edges = [Edge("N1", "N2")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is not None, (
        "SUCCESS outcome must traverse unconditional edges (spec §3.3 steps 4 & 5). "
        "Got None."
    )
    assert result.to_node == "N2", f"Expected edge to 'N2', got '{result.to_node}'"


def test_fail_outcome_traverses_explicit_fail_edge():
    """FAIL + condition="outcome=fail" edge: should follow it (spec §3.7 step 1).

    Explicit failure routing via a condition="outcome=fail" edge is the primary
    mechanism for pipeline authors to route to error-handling nodes.  The fail-
    fast guard must NOT block this — Step 1 (condition matching) runs before
    the unconditional-edge guard.
    """
    # N1 → N_error  [condition="outcome=fail"]
    # N1 → N_next   [condition="outcome=success"]
    # Call with FAIL outcome → should follow N_error edge
    edges = [
        Edge("N1", "N_error", condition="outcome=fail"),
        Edge("N1", "N_next", condition="outcome=success"),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.FAIL)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is not None, (
        "FAIL outcome with a matching condition='outcome=fail' edge must follow "
        "that edge (spec §3.7, Step 1). Got None."
    )
    assert result.to_node == "N_error", (
        f"Expected edge to 'N_error', got '{result.to_node}'"
    )


def test_partial_success_outcome_traverses_unconditional_edges():
    """PARTIAL_SUCCESS is NOT treated as FAIL — unconditional edges are followed.

    The fail-fast guard applies only to StageStatus.FAIL.  Other non-SUCCESS
    statuses (PARTIAL_SUCCESS, RETRY, SKIPPED) are NOT blocked by the guard.
    """
    edges = [Edge("N1", "N2")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.PARTIAL_SUCCESS)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is not None, (
        "PARTIAL_SUCCESS must traverse unconditional edges (fail-fast guard "
        "applies to FAIL only). Got None."
    )
    assert result.to_node == "N2"


def test_fail_outcome_routes_to_runs_on_always_node():
    """FAIL + unconditional edge to runs_on=always node: should follow it.

    runs_on=always is an explicit opt-in for failure routing.  A downstream
    cleanup node with runs_on=always must be reachable via unconditional edge
    even when the predecessor failed.  The engine's skip gate then decides
    whether to execute the cleanup node; select_edge's job is to route there.
    """
    from amplifier_module_loop_pipeline.graph import Node

    # N1 → Cleanup (unconditional, cleanup has runs_on=always)
    # N1 → Normal (unconditional, Normal has default runs_on=success)
    # FAIL outcome → should route to Cleanup (opt-in), NOT to Normal
    cleanup_node = Node(id="Cleanup", attrs={"runs_on": "always"})
    normal_node = Node(id="Normal")
    n1_node = Node(id="N1")

    edges = [
        Edge("N1", "Cleanup"),  # unconditional → target has runs_on=always
        Edge("N1", "Normal"),  # unconditional → target has default runs_on=success
    ]
    from amplifier_module_loop_pipeline.graph import Graph

    graph = Graph(
        name="test",
        nodes={"N1": n1_node, "Cleanup": cleanup_node, "Normal": normal_node},
        edges=edges,
    )
    outcome = Outcome(status=StageStatus.FAIL)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is not None, (
        "FAIL must route to runs_on=always node via unconditional edge. Got None."
    )
    assert result.to_node == "Cleanup", (
        f"Expected edge to 'Cleanup' (runs_on=always), got '{result.to_node}'"
    )


def test_fail_outcome_routes_to_runs_on_failure_node():
    """FAIL + unconditional edge to runs_on=failure node: should follow it.

    runs_on=failure is an explicit opt-in for failure routing (dedicated error
    handlers).  These must be reachable even when the predecessor failed.
    """
    from amplifier_module_loop_pipeline.graph import Graph, Node

    on_fail_node = Node(id="OnFail", attrs={"runs_on": "failure"})
    n1_node = Node(id="N1")
    edges = [Edge("N1", "OnFail")]
    graph = Graph(
        name="test",
        nodes={"N1": n1_node, "OnFail": on_fail_node},
        edges=edges,
    )
    outcome = Outcome(status=StageStatus.FAIL)

    result = select_edge("N1", outcome, PipelineContext(), graph)

    assert result is not None, (
        "FAIL must route to runs_on=failure node via unconditional edge. Got None."
    )
    assert result.to_node == "OnFail"
