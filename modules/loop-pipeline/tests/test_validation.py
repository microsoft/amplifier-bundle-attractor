"""Tests for graph validation (lint rules).

Covers spec Section 7 (Validation and Linting): diagnostic model,
built-in lint rules, and validate/validate_or_raise API.
"""

import pytest

from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.validation import (
    Diagnostic,
    ValidationError,
    validate,
    validate_or_raise,
)


# --- Test helpers ---


def _mdiamond(node_id: str = "start") -> Node:
    return Node(id=node_id, shape="Mdiamond", label="Start")


def _msquare(node_id: str = "exit") -> Node:
    return Node(id=node_id, shape="Msquare", label="Exit")


def _box(node_id: str = "work", **kwargs) -> Node:
    return Node(id=node_id, shape="box", **kwargs)


def _graph(
    nodes: dict[str, Node] | None = None,
    edges: list[Edge] | None = None,
    **kwargs,
) -> Graph:
    return Graph(
        name="test",
        nodes=nodes or {},
        edges=edges or [],
        **kwargs,
    )


# --- start_node rule ---


def test_missing_start_node():
    """ERROR: no start node (LINT-003 / start_node)."""
    g = _graph(
        nodes={"a": _box("a"), "exit": _msquare()},
        edges=[Edge(from_node="a", to_node="exit")],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "start_node" for d in diags)


def test_multiple_start_nodes():
    """ERROR: more than one start node."""
    g = _graph(
        nodes={
            "s1": _mdiamond("s1"),
            "s2": _mdiamond("s2"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="s1", to_node="exit"),
            Edge(from_node="s2", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "start_node" for d in diags)


# --- terminal_node rule ---


def test_missing_exit_node():
    """ERROR: no exit/terminal node (LINT-003 / terminal_node)."""
    g = _graph(
        nodes={"start": _mdiamond(), "a": _box("a")},
        edges=[Edge(from_node="start", to_node="a")],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "terminal_node" for d in diags)


def test_multiple_exit_nodes_error():
    """ERROR: spec says exactly one exit node; multiple exits are invalid (M-11)."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "a": _box("a", prompt="work"),
            "exit1": _msquare("exit1"),
            "exit2": _msquare("exit2"),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            Edge(from_node="a", to_node="exit1"),
            Edge(from_node="a", to_node="exit2"),
        ],
    )
    diags = validate(g)
    terminal_diags = [d for d in diags if d.rule == "terminal_node"]
    assert len(terminal_diags) == 1
    assert terminal_diags[0].severity == "ERROR"
    assert "exactly one" in terminal_diags[0].message.lower()


def test_single_exit_node_ok():
    """A single exit node should produce no terminal_node diagnostic (M-11)."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", prompt="do it"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    terminal_diags = [d for d in diags if d.rule == "terminal_node"]
    assert len(terminal_diags) == 0


# --- reachability rule ---


def test_unreachable_node():
    """ERROR: node not reachable from start (LINT-003 / reachability)."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "a": _box("a"),
            "orphan": _box("orphan"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            Edge(from_node="a", to_node="exit"),
            # orphan not reachable
        ],
    )
    diags = validate(g)
    assert any(d.rule == "reachability" and "orphan" in d.message for d in diags)


# --- edge_target_exists rule ---


def test_edge_target_exists():
    """ERROR: edge target references non-existent node."""
    g = _graph(
        nodes={"start": _mdiamond()},
        edges=[Edge(from_node="start", to_node="nonexistent")],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "edge_target_exists" for d in diags)


def test_edge_source_exists():
    """ERROR: edge source references non-existent node."""
    g = _graph(
        nodes={"exit": _msquare()},
        edges=[Edge(from_node="nonexistent", to_node="exit")],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "edge_target_exists" for d in diags)


# --- start_no_incoming rule ---


def test_start_no_incoming():
    """ERROR: start node must have no incoming edges."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "a": _box("a"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            Edge(from_node="a", to_node="start"),  # bad: incoming to start
            Edge(from_node="a", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "start_no_incoming" for d in diags)


# --- exit_no_outgoing rule ---


def test_exit_no_outgoing():
    """ERROR: exit node must have no outgoing edges."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "a": _box("a"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="a"),
            Edge(from_node="a", to_node="exit"),
            Edge(from_node="exit", to_node="a"),  # bad: outgoing from exit
        ],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "exit_no_outgoing" for d in diags)


# --- Warning-level rules ---


def test_goal_gate_without_retry_target():
    """WARNING: goal_gate=true but no retry_target."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", attrs={"goal_gate": True}),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert any(
        d.severity == "WARNING" and d.rule == "goal_gate_has_retry" for d in diags
    )


def test_prompt_on_llm_nodes():
    """WARNING: codergen nodes should have prompt or label."""
    # A box node with no prompt and default label (= id) triggers warning
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "step": Node(id="step", shape="box"),  # label defaults to id, no prompt
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="step"),
            Edge(from_node="step", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert any(
        d.severity == "WARNING" and d.rule == "prompt_on_llm_nodes" for d in diags
    )


def test_prompt_on_llm_nodes_ok_with_prompt():
    """No warning when codergen node has a prompt."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "step": Node(id="step", shape="box", prompt="Do the work"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="step"),
            Edge(from_node="step", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert not any(d.rule == "prompt_on_llm_nodes" for d in diags)


# --- validate_or_raise ---


def test_validate_or_raise_raises_on_errors():
    """validate_or_raise should raise ValidationError on ERROR diagnostics."""
    g = _graph(nodes={}, edges=[])  # Empty graph = missing start + exit
    with pytest.raises(ValidationError):
        validate_or_raise(g)


def test_validate_or_raise_returns_warnings():
    """validate_or_raise should return warnings (not raise)."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", attrs={"goal_gate": True}),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate_or_raise(g)
    warnings = [d for d in diags if d.severity == "WARNING"]
    assert len(warnings) >= 1


# --- Valid graph passes cleanly ---


def test_valid_graph_no_errors():
    """A well-formed graph should produce zero ERROR diagnostics."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", prompt="Do the work"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    errors = [d for d in diags if d.severity == "ERROR"]
    assert len(errors) == 0


# --- Diagnostic model ---


def test_diagnostic_has_fields():
    """Diagnostic should expose rule, severity, message."""
    d = Diagnostic(rule="start_node", severity="ERROR", message="No start node")
    assert d.rule == "start_node"
    assert d.severity == "ERROR"
    assert d.message == "No start node"


def test_diagnostic_optional_fields():
    """Diagnostic should support optional node_id, edge, fix."""
    d = Diagnostic(
        rule="reachability",
        severity="ERROR",
        message="Node orphan is unreachable",
        node_id="orphan",
        fix="Add an edge from start to orphan",
    )
    assert d.node_id == "orphan"
    assert d.fix == "Add an edge from start to orphan"


# --- Helper for new validation rules ---


def _make_graph(edges_extra=None, nodes_extra=None, graph_attrs=None):
    """Helper to build a minimal valid graph with optional extras."""
    nodes = {
        "start": Node(id="start", shape="Mdiamond"),
        "work": Node(id="work", shape="box", prompt="do work"),
        "done": Node(id="done", shape="Msquare"),
    }
    if nodes_extra:
        for n in nodes_extra:
            nodes[n.id] = n

    edges = [
        Edge(from_node="start", to_node="work"),
        Edge(from_node="work", to_node="done"),
    ]
    if edges_extra:
        edges.extend(edges_extra)

    return Graph(
        name="test",
        nodes=nodes,
        edges=edges,
        graph_attrs=graph_attrs or {},
    )


# --- condition_syntax rule ---


def test_condition_syntax_valid_conditions():
    """condition_syntax: valid conditions produce no diagnostics."""
    graph = _make_graph(
        edges_extra=[
            Edge(from_node="work", to_node="done", condition="outcome=success"),
        ]
    )
    diags = validate(graph)
    condition_diags = [d for d in diags if d.rule == "condition_syntax"]
    assert len(condition_diags) == 0


def test_condition_syntax_invalid_condition_is_error():
    """condition_syntax: malformed condition expression produces ERROR."""
    graph = _make_graph(
        edges_extra=[
            Edge(from_node="work", to_node="done", condition="===broken"),
        ]
    )
    diags = validate(graph)
    condition_diags = [d for d in diags if d.rule == "condition_syntax"]
    assert len(condition_diags) == 1
    assert condition_diags[0].severity == "ERROR"


def test_condition_syntax_empty_condition_ok():
    """condition_syntax: empty condition is always valid (means unconditional)."""
    graph = _make_graph(
        edges_extra=[
            Edge(from_node="work", to_node="done", condition=""),
        ]
    )
    diags = validate(graph)
    condition_diags = [d for d in diags if d.rule == "condition_syntax"]
    assert len(condition_diags) == 0


# --- stylesheet_syntax rule ---


def test_stylesheet_syntax_valid():
    """stylesheet_syntax: valid stylesheet produces no diagnostics."""
    graph = _make_graph(graph_attrs={"model_stylesheet": "* { llm_model: test; }"})
    graph.model_stylesheet = "* { llm_model: test; }"
    diags = validate(graph)
    style_diags = [d for d in diags if d.rule == "stylesheet_syntax"]
    assert len(style_diags) == 0


def test_stylesheet_syntax_empty_ok():
    """stylesheet_syntax: empty stylesheet is valid."""
    graph = _make_graph()
    graph.model_stylesheet = ""
    diags = validate(graph)
    style_diags = [d for d in diags if d.rule == "stylesheet_syntax"]
    assert len(style_diags) == 0


def test_stylesheet_syntax_invalid_is_error():
    """stylesheet_syntax: unparseable stylesheet produces ERROR."""
    graph = _make_graph()
    # Completely broken syntax -- no valid rules extractable
    graph.model_stylesheet = "{{{{not valid css at all"
    diags = validate(graph)
    style_diags = [d for d in diags if d.rule == "stylesheet_syntax"]
    assert len(style_diags) == 1
    assert style_diags[0].severity == "ERROR"


# --- type_known rule ---


def test_type_known_valid_type():
    """type_known: recognized type produces no warning."""
    graph = _make_graph(
        nodes_extra=[
            Node(id="gate", shape="box", type="tool", prompt="decide"),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="gate"),
            Edge(from_node="gate", to_node="done"),
        ],
    )
    diags = validate(graph)
    type_diags = [d for d in diags if d.rule == "type_known"]
    assert len(type_diags) == 0


def test_type_known_unknown_type_warns():
    """type_known: unrecognized type produces WARNING."""
    graph = _make_graph(
        nodes_extra=[
            Node(id="custom", shape="box", type="nonexistent_handler", prompt="x"),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="custom"),
            Edge(from_node="custom", to_node="done"),
        ],
    )
    diags = validate(graph)
    type_diags = [d for d in diags if d.rule == "type_known"]
    assert len(type_diags) == 1
    assert type_diags[0].severity == "WARNING"
    assert "nonexistent_handler" in type_diags[0].message


def test_type_known_empty_type_ok():
    """type_known: empty type (shape-based resolution) is always valid."""
    graph = _make_graph()  # work node has type="" (default)
    diags = validate(graph)
    type_diags = [d for d in diags if d.rule == "type_known"]
    assert len(type_diags) == 0


# --- fidelity_valid rule ---


def test_fidelity_valid_recognized_mode():
    """fidelity_valid: recognized fidelity mode produces no warning."""
    graph = _make_graph()
    graph.nodes["work"].attrs["fidelity"] = "full"
    diags = validate(graph)
    fid_diags = [d for d in diags if d.rule == "fidelity_valid"]
    assert len(fid_diags) == 0


def test_fidelity_valid_invalid_mode_warns():
    """fidelity_valid: unrecognized fidelity mode produces WARNING."""
    graph = _make_graph()
    graph.nodes["work"].attrs["fidelity"] = "typo_fidelity"
    diags = validate(graph)
    fid_diags = [d for d in diags if d.rule == "fidelity_valid"]
    assert len(fid_diags) == 1
    assert fid_diags[0].severity == "WARNING"
    assert "typo_fidelity" in fid_diags[0].message


def test_fidelity_valid_graph_default():
    """fidelity_valid: invalid graph default_fidelity produces WARNING."""
    graph = _make_graph(graph_attrs={"default_fidelity": "invalid_mode"})
    diags = validate(graph)
    fid_diags = [d for d in diags if d.rule == "fidelity_valid"]
    assert len(fid_diags) >= 1
    assert any("invalid_mode" in d.message for d in fid_diags)


def test_fidelity_valid_edge_fidelity():
    """fidelity_valid: invalid edge fidelity produces WARNING."""
    graph = _make_graph(
        edges_extra=[
            Edge(
                from_node="work",
                to_node="done",
                attrs={"fidelity": "bogus"},
            ),
        ]
    )
    diags = validate(graph)
    fid_diags = [d for d in diags if d.rule == "fidelity_valid"]
    assert len(fid_diags) >= 1


# --- retry_target_exists rule ---


def test_retry_target_exists_valid():
    """retry_target_exists: target pointing to real node is ok."""
    graph = _make_graph()
    graph.nodes["work"].attrs["retry_target"] = "work"  # points to itself
    diags = validate(graph)
    rt_diags = [d for d in diags if d.rule == "retry_target_exists"]
    assert len(rt_diags) == 0


def test_retry_target_exists_missing_target_warns():
    """retry_target_exists: target pointing to nonexistent node produces WARNING."""
    graph = _make_graph()
    graph.nodes["work"].attrs["retry_target"] = "nonexistent_node"
    diags = validate(graph)
    rt_diags = [d for d in diags if d.rule == "retry_target_exists"]
    assert len(rt_diags) == 1
    assert rt_diags[0].severity == "WARNING"
    assert "nonexistent_node" in rt_diags[0].message


def test_retry_target_exists_fallback_missing_warns():
    """retry_target_exists: fallback_retry_target with bad reference warns."""
    graph = _make_graph()
    graph.nodes["work"].attrs["fallback_retry_target"] = "ghost"
    diags = validate(graph)
    rt_diags = [d for d in diags if d.rule == "retry_target_exists"]
    assert len(rt_diags) == 1


def test_retry_target_exists_graph_level():
    """retry_target_exists: graph-level retry_target with bad reference warns."""
    graph = _make_graph(graph_attrs={"retry_target": "nonexistent"})
    diags = validate(graph)
    rt_diags = [d for d in diags if d.rule == "retry_target_exists"]
    assert len(rt_diags) >= 1


# --- reachability with retry/fallback targets ---


def test_fallback_retry_target_node_reachable():
    """Nodes referenced as fallback_retry_target should NOT be flagged unreachable."""
    fallback = Node(id="fallback", shape="box", prompt="retry from here")
    graph = _make_graph(
        nodes_extra=[fallback],
        # "fallback" has NO incoming edges — only reachable via retry attr
    )
    graph.nodes["work"].attrs["fallback_retry_target"] = "fallback"
    diags = validate(graph)
    reach_diags = [d for d in diags if d.rule == "reachability"]
    flagged_ids = {d.node_id for d in reach_diags}
    assert "fallback" not in flagged_ids, (
        "fallback_retry_target node should be considered reachable"
    )


def test_graph_level_retry_target_reachable():
    """Graph-level retry_target nodes should NOT be flagged unreachable."""
    retry_node = Node(id="retry_entry", shape="box", prompt="re-enter here")
    graph = _make_graph(
        nodes_extra=[retry_node],
        graph_attrs={"retry_target": "retry_entry"},
    )
    diags = validate(graph)
    reach_diags = [d for d in diags if d.rule == "reachability"]
    flagged_ids = {d.node_id for d in reach_diags}
    assert "retry_entry" not in flagged_ids, (
        "graph-level retry_target node should be considered reachable"
    )


# --- extra_rules parameter (L-19) ---


def test_extra_rules_are_invoked():
    """validate() runs user-supplied extra_rules alongside built-in rules (L-19)."""
    graph = _make_graph()

    def my_rule(g: Graph) -> list[Diagnostic]:
        return [
            Diagnostic(rule="custom_rule", severity="WARNING", message="custom check")
        ]

    diags = validate(graph, extra_rules=[my_rule])
    custom = [d for d in diags if d.rule == "custom_rule"]
    assert len(custom) == 1
    assert custom[0].message == "custom check"


def test_extra_rules_default_empty():
    """validate() with no extra_rules works as before (L-19)."""
    graph = _make_graph()
    diags_default = validate(graph)
    diags_explicit = validate(graph, extra_rules=[])
    # Same number of diagnostics either way
    assert len(diags_default) == len(diags_explicit)


def test_extra_rules_multiple():
    """validate() runs all provided extra rules (L-19)."""
    graph = _make_graph()

    def rule_a(g: Graph) -> list[Diagnostic]:
        return [Diagnostic(rule="rule_a", severity="INFO", message="a")]

    def rule_b(g: Graph) -> list[Diagnostic]:
        return [Diagnostic(rule="rule_b", severity="INFO", message="b")]

    diags = validate(graph, extra_rules=[rule_a, rule_b])
    custom_rules = {d.rule for d in diags if d.rule in ("rule_a", "rule_b")}
    assert custom_rules == {"rule_a", "rule_b"}


# --- SHAPE_TO_HANDLER / _LLM_SHAPES completeness ---


def test_ellipse_removed_from_shape_to_handler():
    """ellipse shape must NOT be in SHAPE_TO_HANDLER (removed — was never used)."""
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    assert "ellipse" not in SHAPE_TO_HANDLER, "ellipse should be removed from SHAPE_TO_HANDLER"


def test_diamond_maps_to_conditional_handler():
    """diamond shape must map to 'conditional' in SHAPE_TO_HANDLER (spec §2.8, §4.7).

    Previously diamond was absent from SHAPE_TO_HANDLER, causing it to silently
    fall through to the codergen LLM agent.  The fix adds diamond → conditional,
    implementing the ConditionalHandler spec (§4.7): a no-op that returns SUCCESS
    immediately, leaving routing to the engine's edge-selection algorithm (§3.3).
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    assert "diamond" in SHAPE_TO_HANDLER, (
        "diamond must be registered in SHAPE_TO_HANDLER (spec §2.8)"
    )
    assert SHAPE_TO_HANDLER["diamond"] == "conditional", (
        f"diamond must map to 'conditional', got '{SHAPE_TO_HANDLER['diamond']}' (spec §4.7)"
    )


# --- Alternative start/exit node conventions ---


def test_start_node_by_node_type_attr():
    """shape=circle + node_type='start' should pass validation as a start node."""
    g = _graph(
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": _box("work", prompt="do it"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    errors = [d for d in diags if d.severity == "ERROR"]
    assert len(errors) == 0, f"Expected no errors, got: {errors}"


def test_exit_node_by_node_type_attr():
    """shape=doublecircle + node_type='exit' should pass validation as an exit node."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", prompt="do it"),
            "Exit": Node(
                id="Exit",
                shape="doublecircle",
                label="Exit",
                attrs={"node_type": "exit"},
            ),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="Exit"),
        ],
    )
    diags = validate(g)
    errors = [d for d in diags if d.severity == "ERROR"]
    assert len(errors) == 0, f"Expected no errors, got: {errors}"


def test_start_node_by_id_fallback():
    """Node with id='Start' (no Mdiamond, no node_type) should pass as start (NLSpec)."""
    g = _graph(
        nodes={
            "Start": Node(id="Start", shape="box", label="Start", prompt="begin"),
            "work": _box("work", prompt="do it"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    start_errors = [
        d for d in diags if d.severity == "ERROR" and d.rule == "start_node"
    ]
    assert len(start_errors) == 0, f"Expected no start_node errors, got: {start_errors}"


def test_exit_node_by_id_fallback():
    """Node with id='Exit' (no Msquare, no node_type) should pass as exit node."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", prompt="do it"),
            "Exit": Node(id="Exit", shape="box", label="Exit", prompt="end"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="Exit"),
        ],
    )
    diags = validate(g)
    terminal_errors = [
        d for d in diags if d.severity == "ERROR" and d.rule == "terminal_node"
    ]
    assert len(terminal_errors) == 0, (
        f"Expected no terminal_node errors, got: {terminal_errors}"
    )


def test_alternative_start_exit_full_pipeline():
    """Real-world pattern: circle+node_type start, doublecircle+node_type exit."""
    g = _graph(
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": _box("work", prompt="do it"),
            "Exit": Node(
                id="Exit",
                shape="doublecircle",
                label="Exit",
                attrs={"node_type": "exit"},
            ),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="Exit"),
        ],
    )
    diags = validate(g)
    errors = [d for d in diags if d.severity == "ERROR"]
    assert len(errors) == 0, f"Expected no errors, got: {errors}"


def test_no_prompt_warning_on_alternative_start_exit():
    """Start/exit nodes using circle/doublecircle should not trigger prompt warnings."""
    g = _graph(
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": _box("work", prompt="do it"),
            "Exit": Node(
                id="Exit",
                shape="doublecircle",
                label="Exit",
                attrs={"node_type": "exit"},
            ),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="Exit"),
        ],
    )
    diags = validate(g)
    prompt_diags = [d for d in diags if d.rule == "prompt_on_llm_nodes"]
    flagged_ids = {d.node_id for d in prompt_diags}
    assert "Start" not in flagged_ids, "Start node should not get prompt warning"
    assert "Exit" not in flagged_ids, "Exit node should not get prompt warning"


def test_start_no_incoming_alternative_convention():
    """start_no_incoming applies to alternative start nodes too."""
    g = _graph(
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": _box("work", prompt="do it"),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="Start"),  # bad: incoming to start
            Edge(from_node="work", to_node="exit"),
        ],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "start_no_incoming" for d in diags)


def test_exit_no_outgoing_alternative_convention():
    """exit_no_outgoing applies to alternative exit nodes too."""
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "work": _box("work", prompt="do it"),
            "Exit": Node(
                id="Exit",
                shape="doublecircle",
                label="Exit",
                attrs={"node_type": "exit"},
            ),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="Exit"),
            Edge(from_node="Exit", to_node="work"),  # bad: outgoing from exit
        ],
    )
    diags = validate(g)
    assert any(d.severity == "ERROR" and d.rule == "exit_no_outgoing" for d in diags)


# --- folder shape -> pipeline mapping ---


def test_folder_shape_maps_to_pipeline():
    """SHAPE_TO_HANDLER must map 'folder' to 'pipeline'."""
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    assert SHAPE_TO_HANDLER["folder"] == "pipeline"


def test_folder_node_type_known():
    """A node with shape=folder should not trigger a type_known warning."""
    graph = _make_graph(
        nodes_extra=[
            Node(id="sub", shape="folder", label="Sub-pipeline"),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="sub"),
            Edge(from_node="sub", to_node="done"),
        ],
    )
    diags = validate(graph)
    type_diags = [d for d in diags if d.rule == "type_known"]
    assert len(type_diags) == 0, (
        f"folder node should not trigger type_known warning, got: {type_diags}"
    )


# --- Doc-consistency: stale shape entries must not appear in agent-visible docs ---


def _repo_root():
    from pathlib import Path

    # test file lives at modules/loop-pipeline/tests/test_validation.py
    # repo root is 4 parents up
    return Path(__file__).parent.parent.parent.parent


def test_doc_diamond_not_in_dot_reference_shape_table():
    """context/dot-reference.md must NOT list diamond as a supported shape (D-128).

    diamond was removed from SHAPE_TO_HANDLER; listing it in the agent-visible
    reference card causes agents to generate invalid pipelines.
    """
    doc = (_repo_root() / "context" / "dot-reference.md").read_text()
    # Match a markdown table row whose first cell is exactly `diamond`
    assert "| `diamond` |" not in doc, (
        "context/dot-reference.md still lists 'diamond' as a shape in the "
        "handler table — remove it (D-128)"
    )


def test_doc_ellipse_not_in_dot_reference_shape_table():
    """context/dot-reference.md must NOT list ellipse as a supported shape (D-129).

    ellipse was removed from SHAPE_TO_HANDLER; documenting it as an alias
    is misleading even though it falls through to the default codergen handler.
    """
    doc = (_repo_root() / "context" / "dot-reference.md").read_text()
    assert "| `ellipse` |" not in doc, (
        "context/dot-reference.md still lists 'ellipse' as a shape in the "
        "handler table — remove it (D-129)"
    )


def test_doc_diamond_not_in_readme_shape_table():
    """README.md must NOT list diamond as a supported shape (D-127).

    Same root cause as D-128 — diamond has no registered handler.
    """
    doc = (_repo_root() / "README.md").read_text()
    assert "| `diamond` |" not in doc, (
        "README.md still lists 'diamond' as a shape in the handler table "
        "— remove it (D-127)"
    )
