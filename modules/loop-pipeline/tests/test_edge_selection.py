"""Tests for the 5-step edge selection algorithm.

Spec coverage: ESEL-001–010, Section 3.3.
"""

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.edge_selection import (
    select_all_matching_edges,
    select_edge,
)
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


def test_condition_matching_takes_priority():
    """Step 1: condition-matching edges first (ESEL-002)."""
    edges = [
        Edge("A", "B", condition="outcome=fail"),
        Edge("A", "C", label="success"),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.FAIL)
    ctx = PipelineContext()
    selected = select_edge("A", outcome, ctx, graph)
    assert selected is not None
    assert selected.to_node == "B"


def test_condition_no_match_falls_through():
    """Condition edges that don't match are skipped."""
    edges = [
        Edge("A", "B", condition="outcome=fail"),
        Edge("A", "C"),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    selected = select_edge("A", outcome, ctx, graph)
    assert selected is not None
    assert selected.to_node == "C"


def test_preferred_label_match():
    """Step 2: preferred_label match (ESEL-003)."""
    edges = [Edge("A", "B", label="tests_pass"), Edge("A", "C", label="tests_fail")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="tests_pass")
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


def test_label_normalization():
    """Labels normalized: lowercase, strip accelerators (ESEL-004)."""
    edges = [Edge("A", "B", label="[Y] Tests Pass")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="tests pass")
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


def test_suggested_next_ids():
    """Step 3: suggested_next_ids match (ESEL-005)."""
    edges = [Edge("A", "B"), Edge("A", "C")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, suggested_next_ids=["C"])
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "C"


def test_weight_tiebreak():
    """Step 4: higher weight wins (ESEL-006)."""
    edges = [Edge("A", "B", weight=1), Edge("A", "C", weight=5)]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "C"


def test_lexical_tiebreak():
    """Step 5: equal weight -> lexical order (ESEL-007)."""
    edges = [Edge("A", "zebra"), Edge("A", "alpha")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "alpha"


def test_no_edges_returns_none():
    """No outgoing edges returns None."""
    graph = Graph(name="test", nodes={"A": Node(id="A")}, edges=[])
    selected = select_edge(
        "A", Outcome(status=StageStatus.SUCCESS), PipelineContext(), graph
    )
    assert selected is None


def test_condition_edges_sorted_by_weight():
    """Multiple matching conditions use weight tiebreak."""
    edges = [
        Edge("A", "B", condition="outcome=success", weight=1),
        Edge("A", "C", condition="outcome=success", weight=10),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "C"


# --- Additional accelerator normalization patterns ---


def test_label_normalization_strip_accelerator_paren():
    """Strip accelerator prefix like 'A) ' from labels (ESEL-004)."""
    edges = [Edge("A", "B", label="A) Fix Code")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="fix code")
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


def test_label_normalization_strip_accelerator_dash():
    """Strip accelerator prefix like 'Y - ' from labels (ESEL-004)."""
    edges = [Edge("A", "B", label="Y - Accept")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="accept")
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


# --- Suggested next IDs edge cases ---


def test_suggested_next_ids_first_match_wins():
    """First match in suggested_next_ids is selected."""
    edges = [Edge("A", "B"), Edge("A", "C")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, suggested_next_ids=["C", "B"])
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "C"  # C listed first in suggested_next_ids


def test_suggested_next_ids_no_match_falls_through():
    """If suggested IDs don't match any edge target, fall through to weight."""
    edges = [Edge("A", "B")]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, suggested_next_ids=["X", "Y"])
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


# --- Determinism ---


def test_deterministic_with_same_inputs():
    """Same inputs always produce same output (ESEL-001)."""
    edges = [
        Edge("A", "B", weight=3),
        Edge("A", "C", weight=3),
        Edge("A", "D", weight=1),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    results = [select_edge("A", outcome, ctx, graph) for _ in range(20)]
    first = results[0]
    assert first is not None
    assert all(r is not None and r.to_node == first.to_node for r in results)


# --- No-match returns None (spec §3.3 RETURN NONE) ---


def test_all_conditional_edges_none_match_returns_none():
    """If all edges have conditions and none match, select_edge returns None (spec §3.3).

    Previously the implementation had a silent fallback that returned an arbitrary
    edge via _best_by_weight_then_lexical(edges).  That violated spec §3.3 whose
    final step is RETURN NONE.  The engine already handles None correctly (halts
    with FAIL outcome).  This test was previously named
    test_all_conditions_false_fallback_to_weight and asserted selected is not None;
    the assertion has been corrected to encode spec-compliant behavior.
    """
    edges = [
        Edge("A", "B", condition="outcome=fail", weight=1),
        Edge("A", "C", condition="outcome=fail", weight=5),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS)
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is None


def test_fail_node_all_conditional_edges_no_match_returns_none():
    """Spec §3.3: select_edge returns None when no edge matches a FAIL outcome.

    A node whose only outgoing edges carry conditions that don't match the
    actual outcome must produce select_edge → None, which allows the engine to
    correctly halt the pipeline with the FAIL outcome instead of silently
    routing into downstream nodes that then also fail (cascading failures).

    Pipeline authors who want downstream execution to continue after a failure
    should use continue_on_fail="true" on the node — the engine handles that
    attribute before calling select_edge.
    """
    # Graph: N1 → N2 [condition="outcome=success"]
    #        N1 → N3 [condition="outcome=other"]
    # Outcome: FAIL — neither condition matches.
    edges = [
        Edge("N1", "N2", condition="outcome=success"),
        Edge("N1", "N3", condition="outcome=other"),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.FAIL)
    result = select_edge("N1", outcome, PipelineContext(), graph)
    assert result is None


def test_fail_outcome_does_not_traverse_unconditional_edges():
    """Spec §3.7 fail-fast intent: FAIL outcomes do NOT traverse unconditional edges.

    Previously this test was named test_fail_node_with_unconditional_edge_routes
    and asserted that unconditional edges were followed on FAIL.  That encoded
    §3.3's pseudocode literally, but violated §3.7's stated fail-fast intent.

    The corrected behavior: FAIL + unconditional edge → None.  The engine then
    checks retry_target / fallback_retry_target and terminates if neither is set.

    Pipeline authors who want fail-forward have three opt-in mechanisms:
    - continue_on_fail="true"          → engine flips FAIL→SUCCESS before select_edge
    - runs_on=always / runs_on=failure → downstream skip-gate override
    - condition="outcome=fail" edge    → matched in Step 1 (unaffected by this change)
    """
    # Graph: N1 → N2 [condition="outcome=success"]  (won't match FAIL)
    #        N1 → N3                                  (unconditional — MUST NOT be followed on FAIL)
    edges = [
        Edge("N1", "N2", condition="outcome=success"),
        Edge("N1", "N3"),  # unconditional
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.FAIL)
    result = select_edge("N1", outcome, PipelineContext(), graph)
    assert result is None, (
        "FAIL outcome must NOT traverse unconditional edges (spec §3.7 fail-fast). "
        f"Expected None, got edge to '{result.to_node if result else None}'"
    )


# --- Priority order tests ---


def test_condition_beats_preferred_label():
    """Condition match (step 1) beats preferred label (step 2)."""
    # With preferred_label set, outcome resolves to preferred_label value.
    # Edge B's condition matches via outcome=go_here (condition step).
    # Edge C's label would match via preferred_label step, but condition wins.
    edges = [
        Edge("A", "B", condition="outcome=go_here"),
        Edge("A", "C", label="go_here"),
    ]
    graph = _make_graph(edges)
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="go_here")
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


def test_preferred_label_beats_suggested_ids():
    """Preferred label (step 2) beats suggested IDs (step 3)."""
    edges = [Edge("A", "B", label="go_here"), Edge("A", "C")]
    graph = _make_graph(edges)
    outcome = Outcome(
        status=StageStatus.SUCCESS,
        preferred_label="go_here",
        suggested_next_ids=["C"],
    )
    selected = select_edge("A", outcome, PipelineContext(), graph)
    assert selected is not None
    assert selected.to_node == "B"


# --- Multi-edge fan-out detection (select_all_matching_edges) ---


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


def test_select_all_matching_edges_no_outgoing():
    """Node with no outgoing edges returns empty list."""
    graph = Graph(
        name="test",
        nodes={"a": Node(id="a")},
        edges=[],
    )
    outcome = Outcome(status=StageStatus.SUCCESS)
    context = PipelineContext()
    edges = select_all_matching_edges("a", outcome, context, graph)
    assert len(edges) == 0
