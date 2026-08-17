"""Tests for graph validation (lint rules).

Covers spec Section 7 (Validation and Linting): diagnostic model,
built-in lint rules, and validate/validate_or_raise API.
"""

import re

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import (
    Diagnostic,
    ValidationError,
    lint,
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


def test_goal_gate_with_loop_restart_edge():
    """No warning when goal_gate=true node has an outgoing loop_restart=true edge.

    This is the canonical convergence-loop pattern: the gate routes back to the
    worker via a loop_restart back-edge rather than a retry_target attribute.
    The rule must recognise this as a valid retry mechanism and not fire.
    """
    g = _graph(
        nodes={
            "start": _mdiamond(),
            "worker": _box("worker"),
            "gate": _box("gate", attrs={"goal_gate": True}),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="worker"),
            Edge(from_node="worker", to_node="gate"),
            Edge(from_node="gate", to_node="exit", attrs={"condition": "ok"}),
            Edge(from_node="gate", to_node="worker", loop_restart=True),
        ],
    )
    diags = validate(g)
    assert not any(d.rule == "goal_gate_has_retry" for d in diags)


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


# --- handler/tool-command and retry-budget structural errors ---


@pytest.mark.parametrize(
    ("node", "expected_rule"),
    [
        (
            Node(
                id="RCExhausted",
                shape="box",
                prompt="This is not a tool",
                attrs={"tool_command": "exit 1"},
            ),
            "tool_command_requires_tool_handler",
        ),
        (
            Node(
                id="explicit_non_tool",
                shape="parallelogram",
                type="codergen",
                prompt="This explicit type wins over the shape",
                attrs={"tool_command": "exit 1"},
            ),
            "tool_command_requires_tool_handler",
        ),
    ],
)
def test_tool_command_on_non_tool_handler_is_error(node: Node, expected_rule: str):
    """A command cannot silently be ignored by a non-tool handler."""
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    diagnostics = validate(graph)

    assert any(
        diagnostic.rule == expected_rule
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )
    with pytest.raises(ValidationError):
        validate_or_raise(graph)


@pytest.mark.parametrize(
    "node",
    [
        Node(
            id="tool_by_shape",
            shape="parallelogram",
            attrs={"tool_command": "printf ok"},
        ),
        Node(
            id="tool_by_explicit_type",
            shape="box",
            type="tool",
            prompt="Explicit tool handler",
            attrs={"tool_command": "printf ok"},
        ),
        Node(
            id="tool_by_node_type",
            shape="box",
            type="unknown.custom",
            prompt="Recognized node_type wins after unknown explicit type",
            attrs={"node_type": "tool", "tool_command": "printf ok"},
        ),
        Node(
            id="unknown_explicit_types_preserve_custom_escape",
            shape="box",
            type="unknown.custom",
            prompt="Both explicit custom names remain extensible",
            attrs={"node_type": "also.unknown", "tool_command": "custom command"},
        ),
    ],
)
def test_tool_command_handler_check_accepts_runtime_resolved_tool_nodes(node: Node):
    """Each runtime precedence path that resolves to tool accepts a command."""
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    diagnostics = validate(graph)

    assert not any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )


def test_explicit_tool_type_wins_over_codergen_node_type():
    """Runtime and validation both honor type before the node_type alias."""
    node = Node(
        id="tool_over_codergen",
        shape="box",
        type="tool",
        prompt="explicit tool owns execution",
        attrs={"node_type": "codergen", "tool_command": "printf executed"},
    )
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    assert type(HandlerRegistry(HandlerContext()).get(node)).__name__ == "ToolHandler"
    assert not any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.node_id == node.id
        for diagnostic in validate(graph)
    )


def test_unknown_type_known_codergen_node_type_rejects_ignored_tool_command():
    """Validation follows runtime to codergen after an unknown custom type."""
    node = Node(
        id="unknown_then_codergen",
        shape="parallelogram",
        type="unknown.custom",
        prompt="codergen owns execution",
        attrs={"node_type": "codergen", "tool_command": "printf ignored"},
    )
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    assert (
        type(HandlerRegistry(HandlerContext()).get(node)).__name__ == "CodergenHandler"
    )
    diagnostics = validate(graph)
    assert any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )


def test_unknown_type_known_tool_node_type_allows_tool_command():
    """Validation follows runtime to tool after an unknown custom type."""
    node = Node(
        id="unknown_then_tool",
        shape="box",
        type="unknown.custom",
        prompt="tool owns execution",
        attrs={"node_type": "tool", "tool_command": "printf executed"},
    )
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    assert type(HandlerRegistry(HandlerContext()).get(node)).__name__ == "ToolHandler"
    diagnostics = validate(graph)
    assert not any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize(
    "node",
    [
        Node(
            id="recognized_type_wins",
            shape="parallelogram",
            type="codergen",
            prompt="Recognized explicit type wins",
            attrs={"node_type": "tool", "tool_command": "printf ignored"},
        ),
        Node(
            id="recognized_node_type_wins",
            shape="parallelogram",
            type="unknown.custom",
            prompt="Recognized node_type wins",
            attrs={"node_type": "codergen", "tool_command": "printf ignored"},
        ),
        Node(
            id="known_conditional",
            shape="parallelogram",
            type="conditional",
            prompt="Known conditional cannot execute a command",
            attrs={"tool_command": "printf ignored"},
        ),
        Node(
            id="unknown_type_known_conditional",
            shape="parallelogram",
            type="unknown.custom",
            prompt="Known node_type follows unknown custom type",
            attrs={"node_type": "conditional", "tool_command": "printf ignored"},
        ),
    ],
)
def test_tool_command_handler_check_matches_runtime_non_tool_precedence(node: Node):
    """Definitive recognized built-in non-tool handlers reject commands."""
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    diagnostics = validate(graph)

    assert any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )


@pytest.mark.asyncio
async def test_registered_custom_handler_with_tool_command_is_not_blocked(tmp_path):
    """Unknown explicit types remain available to runtime-registered handlers."""

    class CustomHandler:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            return Outcome(status=StageStatus.SUCCESS, notes="custom executed")

    node = Node(
        id="custom",
        shape="box",
        type="custom.handler",
        prompt="custom work",
        attrs={"tool_command": "custom handler owns this attribute"},
    )
    graph = _make_graph(
        nodes_extra=[node],
        edges_extra=[
            Edge(from_node="work", to_node=node.id),
            Edge(from_node=node.id, to_node="done"),
        ],
    )

    diagnostics = validate(graph)
    assert not any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.node_id == node.id
        for diagnostic in diagnostics
    )

    registry = HandlerRegistry(HandlerContext())
    handler = CustomHandler()
    registry.register("custom.handler", handler)

    assert registry.get(node) is handler
    outcome = await registry.get(node).execute(
        node,
        PipelineContext(),
        graph,
        str(tmp_path),
    )
    assert outcome.status == StageStatus.SUCCESS


@pytest.mark.parametrize(
    ("node_retries", "graph_default"),
    [
        (-1, ""),
        (
            None,
            "graph [default_max_retry=-1]",
        ),
        (
            None,
            "graph [default_max_retries=-1]",
        ),
    ],
)
def test_negative_effective_retry_budget_is_error(
    node_retries: int | None,
    graph_default: str,
):
    """Node and both parsed graph-default spellings reject negative retry budgets."""
    graph = parse_dot(
        f"""
        digraph RetryBudget {{
            {graph_default}
            start [shape=Mdiamond]
            work [shape=box, prompt="work"]
            exit [shape=Msquare]
            start -> work -> exit
        }}
        """
    )
    if node_retries is not None:
        graph.nodes["work"].max_retries = -1

    diagnostics = validate(graph)

    assert any(
        diagnostic.rule == "retry_budget_non_negative"
        and diagnostic.severity == "ERROR"
        for diagnostic in diagnostics
    )
    with pytest.raises(ValidationError):
        validate_or_raise(graph)


def test_zero_and_node_override_retry_budgets_are_valid():
    """Zero is valid and a non-negative node override wins over a graph default."""
    graph = _make_graph(graph_attrs={"default_max_retry": 3})
    graph.nodes["work"].max_retries = 0

    diagnostics = validate(graph)

    assert not any(
        diagnostic.rule == "retry_budget_non_negative" for diagnostic in diagnostics
    )


@pytest.mark.parametrize("value", [-1, "-1", True, 1.5, "1.5", "invalid"])
def test_invalid_programmatic_node_retry_values_are_deterministic_errors(value):
    """Validation rejects unsafe node retry values without raising Python errors."""
    graph = _make_graph()
    graph.nodes["work"].max_retries = value

    diagnostics = validate(graph)

    assert any(
        diagnostic.rule == "retry_budget_non_negative"
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == "work"
        for diagnostic in diagnostics
    )


@pytest.mark.parametrize("value", [-1, "-1", True, 1.5, "1.5", "invalid"])
def test_invalid_programmatic_graph_retry_values_are_deterministic_errors(value):
    """Validation rejects unsafe graph defaults without TypeError or truncation."""
    graph = _make_graph()
    graph.default_max_retry = value

    diagnostics = validate(graph)

    assert any(
        diagnostic.rule == "retry_budget_non_negative"
        and diagnostic.severity == "ERROR"
        and not diagnostic.node_id
        for diagnostic in diagnostics
    )


def test_quoted_integer_node_retry_is_valid():
    """Programmatic quoted integer node retries remain accepted."""
    graph = _make_graph()
    graph.nodes["work"].max_retries = "2"

    diagnostics = validate(graph)

    assert not any(
        diagnostic.rule == "retry_budget_non_negative" for diagnostic in diagnostics
    )


def test_lint_includes_tool_command_handler_structural_error():
    """The public lint surface includes structural handler/command errors."""
    graph = parse_dot(
        """
        digraph HandlerMismatch {
            start [shape=Mdiamond]
            RCExhausted [shape=box, prompt="not a tool", tool_command="exit 1"]
            exit [shape=Msquare]
            start -> RCExhausted -> exit
        }
        """
    )

    assert any(
        diagnostic.rule == "tool_command_requires_tool_handler"
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == "RCExhausted"
        for diagnostic in lint(graph)
    )


def test_lint_includes_retry_budget_structural_error():
    """The public lint surface includes negative retry-budget errors."""
    graph = parse_dot(
        """
        digraph RetryBudget {
            start [shape=Mdiamond]
            work [shape=box, prompt="work", max_retries=-1]
            exit [shape=Msquare]
            start -> work -> exit
        }
        """
    )

    assert any(
        diagnostic.rule == "retry_budget_non_negative"
        and diagnostic.severity == "ERROR"
        and diagnostic.node_id == "work"
        for diagnostic in lint(graph)
    )


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


# Shapes this implementation adds beyond the upstream nlspec §2.8 table.
# Every entry here MUST have a corresponding record in specs/EXTENSIONS.md.
_INTENTIONAL_SHAPE_EXTENSIONS = {"folder": "pipeline"}


def test_shape_to_handler_conforms_to_upstream_nlspec():
    """SHAPE_TO_HANDLER must equal the upstream §2.8 table plus recorded extensions.

    This is the guard that was missing.  In 2026-04 `diamond`/`conditional` was
    deleted from SHAPE_TO_HANDLER on the reasoning that a no-op handler is
    redundant -- but upstream §2.8 lists it and §4.7 specifies it, and being a
    no-op is the design.  Nothing caught the divergence.  The consequence
    (found six weeks later, in the commit that restored it): `shape=diamond`
    silently fell through to the codergen LLM handler, so every routing node
    became a paid model call.

    Deriving from the spec makes that class of drift impossible to land
    quietly: remove a spec-mandated shape and this test names it.  Add a new
    one and you must record it in _INTENTIONAL_SHAPE_EXTENSIONS *and*
    specs/EXTENSIONS.md.  It also subsumes every "shape X must not exist"
    assertion -- phantom vocabulary fails set equality without being named.
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    spec = (_repo_root() / "specs" / "canonical" / "attractor-spec-canonical.md").read_text()
    rows = re.findall(r"^\|\s*`([A-Za-z]+)`\s*\|\s*`([a-z_.]+)`", spec, re.M)
    upstream = {shape: handler for shape, handler in rows}
    assert upstream, "could not parse the §2.8 shape table from the canonical spec"

    expected = {**upstream, **_INTENTIONAL_SHAPE_EXTENSIONS}
    assert SHAPE_TO_HANDLER == expected, (
        f"SHAPE_TO_HANDLER diverges from upstream nlspec §2.8.\n"
        f"  missing (spec requires): {sorted(set(expected) - set(SHAPE_TO_HANDLER))}\n"
        f"  unrecorded extras:       {sorted(set(SHAPE_TO_HANDLER) - set(expected))}\n"
        f"  handler mismatches:      "
        f"{ {k: (expected[k], SHAPE_TO_HANDLER[k]) for k in set(expected) & set(SHAPE_TO_HANDLER) if expected[k] != SHAPE_TO_HANDLER[k]} }\n"
        f"An intentional addition must be recorded in _INTENTIONAL_SHAPE_EXTENSIONS "
        f"and specs/EXTENSIONS.md."
    )


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


def test_doc_shape_tables_match_shape_to_handler():
    """Agent-visible shape tables must match SHAPE_TO_HANDLER exactly.

    Derived from ground truth rather than a hardcoded snapshot, because the
    hardcoded form went stale and became a guard protecting a false claim:

      2026-04-11  diamond/conditional removed from SHAPE_TO_HANDLER
      2026-04-17  test added asserting "diamond must NOT appear in docs"
      2026-05-23  ConditionalHandler implemented -- diamond RE-ADDED

    For two months the test enforced the April state against May's code, so
    the reference card silently under-documented a supported shape and any
    attempt to correct it hit a red bar.  A derived assertion cannot drift:
    add or remove a shape in SHAPE_TO_HANDLER and this test tells you which
    doc to update.
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    doc = (_repo_root() / "context" / "dot-reference.md").read_text()
    documented = set(re.findall(r"^\| `([A-Za-z]+)` \|", doc, re.M))
    assert documented == set(SHAPE_TO_HANDLER), (
        f"context/dot-reference.md shape table diverges from SHAPE_TO_HANDLER.\n"
        f"  omitted (a capability agents will never use): "
        f"{sorted(set(SHAPE_TO_HANDLER) - documented)}\n"
        f"  phantom (vocabulary the engine rejects):      "
        f"{sorted(documented - set(SHAPE_TO_HANDLER))}"
    )


def test_readme_shape_table_matches_shape_to_handler():
    """README.md's shape table must not omit a supported shape.

    Superseded D-127, whose premise ("diamond has no registered handler") was
    invalidated when ConditionalHandler landed -- see
    test_doc_shape_tables_match_shape_to_handler for the full timeline.
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    doc = (_repo_root() / "README.md").read_text()
    table = doc.split("| Shape |")[1].split("\n\n")[0] if "| Shape |" in doc else doc
    documented = set(re.findall(r"^\| `([A-Za-z]+)` \|", table, re.M))
    assert documented == set(SHAPE_TO_HANDLER), (
        f"README.md shape table diverges from SHAPE_TO_HANDLER.\n"
        f"  omitted: {sorted(set(SHAPE_TO_HANDLER) - documented)}\n"
        f"  phantom: {sorted(documented - set(SHAPE_TO_HANDLER))}"
    )


# --- shape_resolvable rule (issue #268) ---
# lint() must produce an ERROR for any node whose type is empty and whose
# shape is not in SHAPE_TO_HANDLER.  The rule fires per-node and must not
# flag nodes that have an explicit type= attribute or a known shape.


def test_unknown_shape_no_type_produces_error_via_lint():
    """Regression: lint() must emit ERROR for a node with a typo'd shape and no type.

    Issue #268: a node with shape=parallelgram (typo) and no explicit type
    silently fell through to the codergen handler class at lint time, then
    raised ValueError at runtime dispatch.  The rule must surface the error
    before any node executes.
    """
    graph = _make_graph(
        nodes_extra=[
            Node(id="bad", shape="parallelgram"),  # typo: missing 'o'
        ],
        edges_extra=[
            Edge(from_node="work", to_node="bad"),
            Edge(from_node="bad", to_node="done"),
        ],
    )
    diags = lint(graph)
    errors = [d for d in diags if d.severity == "ERROR" and d.rule == "shape_resolvable"]
    assert errors, (
        f"Expected at least one ERROR with rule='shape_resolvable' for node 'bad' "
        f"(shape='parallelgram', no explicit type), got: {diags}"
    )
    assert any(d.node_id == "bad" or "bad" in d.message for d in errors), (
        f"ERROR must be associated with node 'bad', got: {errors}"
    )


def test_unknown_shape_with_tool_command_produces_error():
    """lint() must emit ERROR for unknown-shape node that carries tool_command.

    A node with an unrecognized shape and a tool_command attribute must still
    produce an ERROR -- the rule is unconditional on the presence of tool_command.
    """
    graph = _make_graph(
        nodes_extra=[
            Node(id="bad_tc", shape="parallelgram", attrs={"tool_command": "./run.sh"}),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="bad_tc"),
            Edge(from_node="bad_tc", to_node="done"),
        ],
    )
    diags = lint(graph)
    errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "bad_tc" or "bad_tc" in d.message)
    ]
    assert errors, (
        f"Expected ERROR for unknown-shape+tool_command node 'bad_tc', got: {diags}"
    )


def test_unknown_shape_with_prompt_produces_error():
    """lint() must emit ERROR for unknown-shape node that has a non-empty prompt.

    The rule is unconditional: unknown shape + no explicit type => ERROR,
    regardless of whether the node has a prompt or label.
    """
    graph = _make_graph(
        nodes_extra=[
            Node(id="bad_prompt", shape="parallelgram", prompt="do something"),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="bad_prompt"),
            Edge(from_node="bad_prompt", to_node="done"),
        ],
    )
    diags = lint(graph)
    errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "bad_prompt" or "bad_prompt" in d.message)
    ]
    assert errors, (
        f"Expected ERROR for unknown-shape+prompt node 'bad_prompt', got: {diags}"
    )


def test_known_shape_node_not_flagged_by_shape_resolvable():
    """Known-shape nodes in the same graph as unknown-shape nodes must not be flagged.

    The rule fires per-node: a node with shape=parallelogram (known) must not
    receive a shape_resolvable ERROR even when sibling nodes have unknown shapes.
    """
    graph = _make_graph(
        nodes_extra=[
            Node(id="bad", shape="parallelgram"),  # unknown shape
            Node(id="good", shape="parallelogram", attrs={"tool_command": "./ok.sh"}),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="bad"),
            Edge(from_node="bad", to_node="good"),
            Edge(from_node="good", to_node="done"),
        ],
    )
    diags = lint(graph)
    good_shape_errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "good" or "good" in d.message)
    ]
    assert not good_shape_errors, (
        f"Known-shape node 'good' (parallelogram) must not be flagged by "
        f"shape_resolvable, got: {good_shape_errors}"
    )
    # The bad node must still be flagged
    bad_shape_errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "bad" or "bad" in d.message)
    ]
    assert bad_shape_errors, (
        f"Unknown-shape node 'bad' (parallelgram) must be flagged, got: {diags}"
    )


def test_explicit_type_suppresses_shape_resolvable():
    """Nodes with an explicit type= attribute must not be flagged by shape_resolvable.

    The existing type_known rule handles unknown type values.  The shape_resolvable
    rule targets only the shape-based resolution path: node.type is empty AND
    node.shape is not in SHAPE_TO_HANDLER.
    """
    graph = _make_graph(
        nodes_extra=[
            # Unknown shape but explicit type -- shape_resolvable must not fire
            Node(id="typed", shape="parallelgram", type="tool",
                 attrs={"tool_command": "./run.sh"}),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="typed"),
            Edge(from_node="typed", to_node="done"),
        ],
    )
    diags = lint(graph)
    shape_errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "typed" or "typed" in d.message)
    ]
    assert not shape_errors, (
        f"Node 'typed' has explicit type='tool' and must not be flagged by "
        f"shape_resolvable (that path belongs to type_known), got: {shape_errors}"
    )


def test_shape_resolvable_also_fires_via_validate():
    """validate() must also emit the shape_resolvable ERROR (not just lint()).

    The rule lives in validate(), so validate_or_raise() also catches
    unknown-shape nodes -- consistent with the existing validation architecture.
    """
    graph = _make_graph(
        nodes_extra=[
            Node(id="bad", shape="parallelgram"),
        ],
        edges_extra=[
            Edge(from_node="work", to_node="bad"),
            Edge(from_node="bad", to_node="done"),
        ],
    )
    diags = validate(graph)
    errors = [
        d for d in diags
        if d.severity == "ERROR" and d.rule == "shape_resolvable"
        and (d.node_id == "bad" or "bad" in d.message)
    ]
    assert errors, (
        f"validate() must also emit shape_resolvable ERROR for node 'bad', got: {diags}"
    )
