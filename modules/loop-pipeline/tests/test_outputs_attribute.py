"""Tests for M1: outputs= attribute parsing and handler-side inference table.

R12 WS-6 — engine node-failure propagation.
"""

from __future__ import annotations


from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.node_outputs import (
    HANDLER_INFERRED_OUTPUTS,
    SUBSTITUTABLE_ATTRS,
    build_output_table,
)


# ---------------------------------------------------------------------------
# Tests for HANDLER_INFERRED_OUTPUTS (SC-2 closed table)
# ---------------------------------------------------------------------------


def test_tool_handler_infers_tool_output_and_last_line():
    """SC-2: tool handler contributes tool.output and tool.last_line."""
    inferred = HANDLER_INFERRED_OUTPUTS["tool"]
    assert "tool.output" in inferred
    assert "tool.last_line" in inferred


def test_human_handler_infers_human_input_and_choice():
    """SC-2: human handler contributes human.input and human.choice."""
    inferred = HANDLER_INFERRED_OUTPUTS["wait.human"]
    assert "human.input" in inferred
    assert "human.choice" in inferred


def test_other_handlers_not_in_inference_table():
    """SC-2: other handlers have no inferred outputs (use explicit outputs=)."""
    # codergen, pipeline, exit, start are not in the table
    for handler_type in ("codergen", "pipeline", "exit", "start", "stack.manager_loop"):
        assert handler_type not in HANDLER_INFERRED_OUTPUTS, (
            f"Handler '{handler_type}' should not be in inference table"
        )


# ---------------------------------------------------------------------------
# Tests for SUBSTITUTABLE_ATTRS registry (M2)
# ---------------------------------------------------------------------------


def test_substitutable_attrs_contains_required_attrs():
    """M2: the substitutable attribute set covers the required attrs."""
    for attr in ("tool_command", "prompt", "description", "tool_env"):
        assert attr in SUBSTITUTABLE_ATTRS, f"'{attr}' not in SUBSTITUTABLE_ATTRS"


# ---------------------------------------------------------------------------
# Tests for build_output_table (M1 output table construction)
# ---------------------------------------------------------------------------


def test_explicit_outputs_attr_parsed():
    """M1: outputs='x,y' on a DOT node is parsed into the output table."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        producer_a [shape=parallelogram, tool_command="echo hi",
                    outputs="validated.path,validated.schema"]
        exit [shape=Msquare]
        start -> producer_a -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    assert "validated.path" in table["producer_a"]
    assert "validated.schema" in table["producer_a"]


def test_tool_handler_inferred_outputs_in_table():
    """M1: tool handler infers tool.output and tool.last_line automatically."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        worker [shape=parallelogram, tool_command="echo done"]
        exit [shape=Msquare]
        start -> worker -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    assert "tool.output" in table["worker"]
    assert "tool.last_line" in table["worker"]


def test_explicit_outputs_merged_with_inferred():
    """M1: outputs= declaration is unioned with inferred outputs."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        worker [shape=parallelogram, tool_command="echo hi",
                parse_json="true", outputs="parsed.result"]
        exit [shape=Msquare]
        start -> worker -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    # Both explicit and inferred should be present
    assert "parsed.result" in table["worker"]
    assert "tool.output" in table["worker"]
    assert "tool.last_line" in table["worker"]


def test_human_handler_inferred_outputs_in_table():
    """M1: human handler infers human.input and human.choice."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        gate [shape=hexagon, prompt="Approve?"]
        exit [shape=Msquare]
        start -> gate -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    assert "human.input" in table["gate"]
    assert "human.choice" in table["gate"]


def test_parallel_handler_infers_branch_outcomes():
    """M1: parallel handler infers branch.{idx}.outcome per outgoing edge."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        fan_out [shape=component]
        branch_a [shape=parallelogram, tool_command="echo a"]
        branch_b [shape=parallelogram, tool_command="echo b"]
        join [shape=tripleoctagon]
        exit [shape=Msquare]
        start -> fan_out
        fan_out -> branch_a
        fan_out -> branch_b
        branch_a -> join
        branch_b -> join
        join -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    # fan_out has 2 outgoing edges → branch.0.outcome and branch.1.outcome
    assert "branch.0.outcome" in table["fan_out"]
    assert "branch.1.outcome" in table["fan_out"]


def test_node_with_no_outputs_has_empty_set():
    """M1: a node with no inferred or explicit outputs has empty frozenset."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        codergen_node [prompt="Do something"]
        exit [shape=Msquare]
        start -> codergen_node -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    # codergen node has no declared or inferred outputs
    assert table["codergen_node"] == frozenset()


def test_outputs_overrides_not_needed_since_union():
    """M1: outputs= on a tool node unions with inferred (no conflict needed)."""
    dot = """
    digraph {
        start [shape=Mdiamond]
        worker [shape=parallelogram, tool_command="echo hi",
                outputs="tool.output,extra.key"]
        exit [shape=Msquare]
        start -> worker -> exit
    }
    """
    graph = parse_dot(dot)
    table = build_output_table(graph)
    # explicit tool.output is a no-op union (already inferred)
    assert "tool.output" in table["worker"]
    assert "extra.key" in table["worker"]
    assert "tool.last_line" in table["worker"]
