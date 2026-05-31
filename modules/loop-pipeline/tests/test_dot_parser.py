"""Tests for the DOT parser.

Covers spec Section 2 (DOT DSL Schema): grammar, value types,
node/edge declarations, chained edges, defaults, subgraphs, comments,
and graph-level attributes.
"""

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot


# --- Basic parsing ---


def test_simple_graph():
    """Parse a minimal digraph with nodes and edges."""
    graph = parse_dot("""
    digraph pipeline {
        start [shape=Mdiamond]
        plan [label="Plan the work"]
        implement [label="Implement"]
        done [shape=Msquare]
        start -> plan -> implement -> done
    }
    """)
    assert graph.name == "pipeline"
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    assert graph.nodes["start"].shape == "Mdiamond"
    assert graph.nodes["plan"].label == "Plan the work"
    assert graph.nodes["done"].shape == "Msquare"


def test_rejects_undirected_graph():
    """Only digraph is allowed (DOT-001)."""
    with pytest.raises(ValueError, match="digraph"):
        parse_dot("graph { A -- B }")


def test_rejects_strict_modifier():
    """strict modifier is rejected (Section 2.3)."""
    with pytest.raises(ValueError, match="strict"):
        parse_dot("strict digraph { A -> B }")


def test_rejects_undirected_edge():
    """Undirected edges (--) are rejected (Section 2.3)."""
    with pytest.raises(ValueError, match="--"):
        parse_dot("digraph { A -- B }")


def test_allows_double_dash_in_plain_quoted_attribute():
    """-- inside a plain quoted attribute value must not raise ValueError."""
    graph = parse_dot('digraph test { A [label="git log --oneline"]; A -> B }')
    assert "A" in graph.nodes
    assert len(graph.edges) == 1


def test_allows_double_dash_after_escaped_quote_in_string():
    """-- after a single escaped quote in a quoted attribute must not raise ValueError.

    The bug: _has_undirected_edges toggled in_string on every '"' char
    (including backslash-escaped quotes like \"), without skipping the escape
    sequence. A single \" before the -- causes in_string to flip False, then
    the subsequent -- is misidentified as an undirected edge declaration.

    From dotpowers.dot: tool_command="...\"$(git ...) rev-parse --show-toplevel..."
    """
    # DOT source: A [cmd="test \"data --flag"]
    # The \" toggles in_string=False; then -- is seen outside-string → ValueError.
    graph = parse_dot('digraph test { A [cmd="test \\"data --flag"]; A -> B }')
    assert "A" in graph.nodes
    assert len(graph.edges) == 1


# --- Chained edges ---


def test_chained_edges_expanded():
    """A -> B -> C expands to A->B and B->C (Section 2.9)."""
    graph = parse_dot("digraph test { A -> B -> C }")
    assert len(graph.edges) == 2
    assert graph.edges[0].from_node == "A"
    assert graph.edges[0].to_node == "B"
    assert graph.edges[1].from_node == "B"
    assert graph.edges[1].to_node == "C"


def test_chained_edges_with_attributes():
    """Chained edge attributes apply to all edges in the chain (Section 2.9)."""
    graph = parse_dot('digraph test { A -> B -> C [label="next"] }')
    assert len(graph.edges) == 2
    assert graph.edges[0].label == "next"
    assert graph.edges[1].label == "next"


def test_four_node_chain():
    """A -> B -> C -> D expands to 3 edges."""
    graph = parse_dot("digraph test { A -> B -> C -> D }")
    assert len(graph.edges) == 3


# --- Node defaults ---


def test_node_defaults():
    """node [...] sets baseline attributes (Section 2.11)."""
    graph = parse_dot("""
    digraph test {
        node [shape=box, max_retries=3]
        A
        B [shape=hexagon]
    }
    """)
    assert graph.nodes["A"].shape == "box"
    assert graph.nodes["A"].attrs.get("max_retries") == 3
    assert graph.nodes["B"].shape == "hexagon"  # Override


def test_edge_defaults():
    """edge [...] sets baseline edge attributes (Section 2.11)."""
    graph = parse_dot("""
    digraph test {
        edge [weight=5]
        A -> B
        C -> D [weight=10]
    }
    """)
    assert graph.edges[0].weight == 5
    assert graph.edges[1].weight == 10  # Override


# --- Subgraph support ---


def test_subgraph_support():
    """Nodes inside subgraphs are added to the top-level graph."""
    graph = parse_dot("""
    digraph test {
        subgraph cluster_impl {
            label="Implementation"
            code
            test
        }
        code -> test
    }
    """)
    assert "code" in graph.nodes
    assert "test" in graph.nodes


def test_subgraph_class_derivation():
    """Subgraph label should derive a CSS-like class for nodes within (L-3)."""
    graph = parse_dot("""
    digraph test {
        subgraph cluster_impl {
            label="Implementation Phase"
            code
            test
        }
        code -> test
    }
    """)
    # Class derived from label: lowercase, spaces->hyphens, strip non-alphanum
    assert graph.nodes["code"].attrs.get("class") == "implementation-phase"
    assert graph.nodes["test"].attrs.get("class") == "implementation-phase"


def test_subgraph_class_not_overridden():
    """Explicit class attr on a node should not be overridden by subgraph (L-3)."""
    graph = parse_dot("""
    digraph test {
        subgraph cluster_x {
            label="My Group"
            A [class="custom"]
        }
    }
    """)
    # Explicit class should be preserved
    assert graph.nodes["A"].attrs.get("class") == "custom"


def test_subgraph_no_label_no_class():
    """Subgraph without label should not set class (L-3)."""
    graph = parse_dot("""
    digraph test {
        subgraph cluster_x {
            A
        }
    }
    """)
    assert graph.nodes["A"].attrs.get("class") is None


def test_subgraph_node_defaults():
    """Subgraph node defaults apply to nodes within (Section 2.10)."""
    graph = parse_dot("""
    digraph test {
        subgraph cluster_loop {
            node [timeout="900s"]
            Plan
            Implement [timeout="1800s"]
        }
    }
    """)
    assert graph.nodes["Plan"].attrs.get("timeout") == 900000  # 900s -> ms
    assert graph.nodes["Implement"].attrs.get("timeout") == 1800000  # override


# --- Attribute value types (Section 2.4) ---


def test_string_values():
    """Quoted strings with escape sequences."""
    graph = parse_dot(r"""
    digraph test {
        A [label="Hello \"world\"", prompt="line1\nline2"]
    }
    """)
    assert graph.nodes["A"].label == 'Hello "world"'
    assert graph.nodes["A"].prompt == "line1\nline2"


def test_integer_values():
    """Integer attributes."""
    graph = parse_dot("""
    digraph test {
        A [max_retries=3, weight=-1]
    }
    """)
    assert graph.nodes["A"].attrs.get("max_retries") == 3


def test_leading_dot_float_values():
    """Leading-dot floats like .5 should be parsed as 0.5 (L-1)."""
    graph = parse_dot("""
    digraph test {
        A [weight=.5, threshold=.75]
    }
    """)
    assert graph.nodes["A"].attrs.get("weight") == 0.5
    assert graph.nodes["A"].attrs.get("threshold") == 0.75


def test_boolean_values():
    """Boolean true/false keywords."""
    graph = parse_dot("""
    digraph test {
        A [goal_gate=true, auto_status=false]
    }
    """)
    assert graph.nodes["A"].attrs.get("goal_gate") is True
    assert graph.nodes["A"].attrs.get("auto_status") is False


def test_duration_values():
    """Duration values converted to milliseconds (Section 2.4)."""
    graph = parse_dot("""
    digraph test {
        A [timeout="30s"]
        B [timeout="15m"]
        C [timeout="2h"]
        D [timeout="250ms"]
    }
    """)
    assert graph.nodes["A"].attrs.get("timeout") == 30_000
    assert graph.nodes["B"].attrs.get("timeout") == 900_000
    assert graph.nodes["C"].attrs.get("timeout") == 7_200_000
    assert graph.nodes["D"].attrs.get("timeout") == 250


# --- Comments (Section 2.3) ---


def test_line_comments_stripped():
    """// comments are removed before parsing."""
    graph = parse_dot("""
    digraph test {
        // This is a comment
        A -> B
    }
    """)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_block_comments_stripped():
    """/* */ comments are removed before parsing."""
    graph = parse_dot("""
    digraph test {
        A -> B /* inline comment */
        /* multi
           line
           comment */
        C -> D
    }
    """)
    assert len(graph.edges) == 2


# --- Graph-level attributes (Section 2.5) ---


def test_graph_level_attributes_bare():
    """Top-level key=value declarations."""
    graph = parse_dot("""
    digraph test {
        goal="Build the feature"
        default_max_retry=5
        A [shape=Mdiamond]
        B [shape=Msquare]
        A -> B
    }
    """)
    assert graph.goal == "Build the feature"
    assert graph.default_max_retry == 5


def test_graph_attr_block():
    """graph [...] attribute block."""
    graph = parse_dot("""
    digraph test {
        graph [goal="Run tests and report", label="Test Pipeline"]
        A [shape=Mdiamond]
        B [shape=Msquare]
        A -> B
    }
    """)
    assert graph.goal == "Run tests and report"
    assert graph.graph_attrs.get("label") == "Test Pipeline"


def test_rankdir_as_graph_attr():
    """rankdir is a common graph attribute."""
    graph = parse_dot("""
    digraph test {
        rankdir=LR
        A -> B
    }
    """)
    assert graph.graph_attrs.get("rankdir") == "LR"


# --- Spec example graphs (Section 2.13) ---


def test_spec_simple_linear_workflow():
    """Example from spec Section 2.13: Simple linear workflow."""
    graph = parse_dot("""
    digraph Simple {
        graph [goal="Run tests and report"]
        rankdir=LR

        start [shape=Mdiamond, label="Start"]
        exit  [shape=Msquare, label="Exit"]

        run_tests [label="Run Tests", prompt="Run the test suite and report results"]
        report    [label="Report", prompt="Summarize the test results"]

        start -> run_tests -> report -> exit
    }
    """)
    assert graph.name == "Simple"
    assert graph.goal == "Run tests and report"
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    assert graph.nodes["start"].shape == "Mdiamond"
    assert graph.nodes["exit"].shape == "Msquare"
    assert graph.nodes["run_tests"].label == "Run Tests"
    assert graph.nodes["run_tests"].prompt == "Run the test suite and report results"


def test_spec_branching_workflow():
    """Example from spec Section 2.13: Branching workflow with conditions."""
    graph = parse_dot("""
    digraph Branch {
        graph [goal="Implement and validate a feature"]
        rankdir=LR
        node [shape=box, timeout="900s"]

        start     [shape=Mdiamond, label="Start"]
        exit      [shape=Msquare, label="Exit"]
        plan      [label="Plan", prompt="Plan the implementation"]
        implement [label="Implement", prompt="Implement the plan"]
        validate  [label="Validate", prompt="Run tests"]
        start -> plan -> implement -> validate
        validate -> exit      [label="Yes", condition="outcome=success"]
        validate -> implement [label="No", condition="outcome!=success"]
    }
    """)
    assert graph.name == "Branch"
    assert len(graph.nodes) == 5
    # Chained: start -> plan -> implement -> validate = 3 edges
    # Plus 2 from validate = 5 total
    assert len(graph.edges) == 5
    # Check condition edges
    gate_edges = graph.outgoing_edges("validate")
    assert len(gate_edges) == 2
    yes_edge = [e for e in gate_edges if e.label == "Yes"][0]
    assert yes_edge.condition == "outcome=success"
    assert yes_edge.to_node == "exit"


def test_implicit_node_from_edge():
    """Nodes referenced only in edges should be auto-created."""
    graph = parse_dot("""
    digraph test {
        A -> B -> C
    }
    """)
    assert "A" in graph.nodes
    assert "B" in graph.nodes
    assert "C" in graph.nodes


def test_semicolons_optional():
    """Semicolons are accepted but not required (Section 2.3)."""
    graph = parse_dot("""
    digraph test {
        A [shape=box];
        B [shape=hexagon];
        A -> B;
    }
    """)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_edge_with_multiple_attributes():
    """Edges can have multiple attributes."""
    graph = parse_dot("""
    digraph test {
        A -> B [label="retry", condition="outcome=fail", weight=10]
    }
    """)
    edge = graph.edges[0]
    assert edge.label == "retry"
    assert edge.condition == "outcome=fail"
    assert edge.weight == 10


def test_node_with_prompt_containing_dollar_goal():
    """$goal in prompts is preserved (expansion happens in handler)."""
    graph = parse_dot("""
    digraph test {
        A [prompt="Build the feature for $goal"]
    }
    """)
    assert "$goal" in graph.nodes["A"].prompt


# --- M-9: Comma enforcement in attribute blocks ---


def test_space_separated_attrs_warns(recwarn):
    """Space-separated attributes without commas should emit a warning (M-9)."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        graph = parse_dot("""
        digraph test {
            A [shape=box label="Hello" max_retries=3]
        }
        """)
    # Should still parse (backward compatible)
    assert graph.nodes["A"].shape == "box"
    assert graph.nodes["A"].label == "Hello"
    # But should have emitted a warning
    comma_warnings = [x for x in w if "comma" in str(x.message).lower()]
    assert len(comma_warnings) >= 1, "Expected warning about missing commas"


def test_comma_separated_attrs_no_warning(recwarn):
    """Properly comma-separated attributes should NOT emit a warning (M-9)."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        graph = parse_dot("""
        digraph test {
            A [shape=box, label="Hello", max_retries=3]
        }
        """)
    assert graph.nodes["A"].shape == "box"
    assert graph.nodes["A"].label == "Hello"
    comma_warnings = [x for x in w if "comma" in str(x.message).lower()]
    assert len(comma_warnings) == 0, "No warning expected for comma-separated attrs"


def test_multiple_digraphs_rejected():
    """Multiple digraphs in one file should produce a clear error (L-2)."""
    with pytest.raises(ValueError, match="[Mm]ultiple.*digraph"):
        parse_dot("""
        digraph first { A -> B }
        digraph second { C -> D }
        """)


def test_single_attr_no_warning():
    """A single attribute needs no comma and should not warn (M-9)."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parse_dot("""
        digraph test {
            A [shape=box]
        }
        """)
    comma_warnings = [x for x in w if "comma" in str(x.message).lower()]
    assert len(comma_warnings) == 0


# --- Escape sequence regression tests (added for multi-line shell heredoc crash) ---
# Background: chained str.replace() in _parse_value was order-dependent. The bug
# was that ``\n`` -> LF ran before ``\\`` -> ``\``, so an input of ``\\n`` (two
# literal backslashes followed by n — a common escape pattern in multi-line shell
# heredoc command attributes — used as a logical line separator) was mis-processed
# into ``\<LF>`` — dash interprets that as line-continuation, joining script lines.
# The resulting joined line broke heredoc structure in any tool_command node that
# used Python heredoc syntax, causing pipeline failure with
# ``/bin/sh: Syntax error: "(" unexpected``.
#
# Fix: reorder the chain so ``\\`` -> ``\`` runs FIRST, before any
# ``\<char>`` -> <char> conversion. With that order, ``\\n`` correctly
# yields LF — which is what existing pipelines expect.


def test_escape_double_backslash_n_yields_newline():
    """REGRESSION: ``\\\\n`` in DOT source must yield a real newline (LF).

    Pipelines that use ``\\\\n`` as a logical line separator in multi-line
    shell heredoc tool_command attributes depend on this behavior.
    """
    graph = parse_dot('digraph t { n [label="line1\\\\nline2"] }')
    assert graph.nodes["n"].label == "line1\nline2"


def test_escape_single_backslash_n_yields_newline():
    """``\\n`` in DOT source yields a real newline (the simple case)."""
    graph = parse_dot('digraph t { n [label="line1\\nline2"] }')
    assert graph.nodes["n"].label == "line1\nline2"


def test_escape_double_backslash_t_yields_tab():
    """``\\\\t`` -> TAB (consistency with ``\\\\n``)."""
    graph = parse_dot('digraph t { n [label="a\\\\tb"] }')
    assert graph.nodes["n"].label == "a\tb"


def test_escape_quote():
    r"""``\"`` yields a literal quote inside a DOT string."""
    graph = parse_dot('digraph t { n [label="he said \\"hi\\""] }')
    assert graph.nodes["n"].label == 'he said "hi"'


def test_escape_quadruple_backslash_yields_pair():
    """``\\\\\\\\`` (4 backslashes) -> ``\\\\`` (2 backslashes).

    Pairwise reduction: each adjacent ``\\\\`` collapses to ``\\``.
    """
    graph = parse_dot('digraph t { n [label="\\\\\\\\"] }')
    assert graph.nodes["n"].label == "\\\\"


def test_escape_multiline_shell_heredoc_tool_command_preserves_structure():
    """Multi-line shell heredoc tool_command must produce valid multi-line shell.

    Before the fix: ``\\\\n`` between script lines became ``\<LF>`` (line
    continuation), joining lines into one big logical line and breaking any
    `python3 - << 'PYEOF'` heredoc. Result: dash parsed Python source as shell
    code and crashed on the first ``(``.
    """
    cmd_attr = "#!/bin/sh\\\\nset -e\\\\nprintf hello\\\\nprintf world"
    graph = parse_dot(f'digraph t {{ n [label="{cmd_attr}"] }}')
    assert graph.nodes["n"].label == ("#!/bin/sh\nset -e\nprintf hello\nprintf world")


# --- Fail-loud: malformed backslash-quote delimiters (Layer A) ---


def test_malformed_backslash_delimiter_rejected():
    r"""Backslash-quote (\"...\") as attribute delimiter must raise ValueError.

    This is the specific failure mode from ralph-loop pipelines: edge conditions
    used \"value\" instead of "value", causing the tokenizer to silently
    mis-parse the condition as a bare key (context.tool.last_line) and evaluate
    it truthy for all 4 outgoing edges, producing a fan-out instead of
    single-edge routing.  The fix (Layer A) raises an error immediately so the
    author sees the problem at parse time instead of debugging wrong runtime
    behaviour.

    See RECURRING-BUG-CLASSES.md S6.
    """
    # Python repr: 'condition=\\"context.tool.last_line=continue\\"'
    # On-disk DOT: condition=\"context.tool.last_line=continue\"
    with pytest.raises(ValueError, match="backslash-quote"):
        parse_dot(
            "digraph test {"
            "  start [shape=Mdiamond]; done [shape=Msquare]"
            '  start -> done [condition=\\"context.tool.last_line=continue\\"]'
            "}"
        )


def test_malformed_backslash_delimiter_error_is_actionable():
    r"""Error message must name the offending construct and the correct form."""
    with pytest.raises(ValueError) as exc_info:
        parse_dot(
            "digraph t {"
            "  s [shape=Mdiamond]; d [shape=Msquare]"
            '  s -> d [condition=\\"outcome=success\\"]'
            "}"
        )
    msg = str(exc_info.value)
    # Points at the problem
    assert "backslash-quote" in msg or '\\"' in msg
    # Names the correct form
    assert "plain" in msg or 'condition="' in msg


def test_plain_quote_condition_parses_and_routes_correctly():
    r"""Plain-quoted condition must parse and select exactly one matching edge.

    Regression guard: with the old silent-mis-parse behaviour, all 4 conditional
    edges from Verify matched because the malformed \"context.tool.last_line\"
    was a truthy bare key.  This test confirms that with plain quotes exactly
    one edge matches when context.tool.last_line=continue.
    """
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.edge_selection import select_all_matching_edges
    from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

    graph = parse_dot("""
    digraph test {
        start  [shape=Mdiamond]
        verify [prompt="Verify"]
        execute     [prompt="Execute again"]
        report_done [shape=Msquare]
        report_stalled [shape=Msquare]
        report_max    [shape=Msquare]

        start -> verify
        verify -> execute       [condition="context.tool.last_line=continue", loop_restart=true]
        verify -> report_done   [condition="context.tool.last_line=done"]
        verify -> report_stalled[condition="context.tool.last_line=stalled"]
        verify -> report_max    [condition="context.tool.last_line=max_iter"]
    }
    """)

    ctx = PipelineContext()
    ctx.set("tool.last_line", "continue")
    outcome = Outcome(status=StageStatus.SUCCESS)

    matched = select_all_matching_edges("verify", outcome, ctx, graph)
    assert len(matched) == 1, (
        f"Expected exactly 1 matching edge, got {len(matched)}: "
        f"{[e.to_node for e in matched]}"
    )
    assert matched[0].to_node == "execute"
    assert matched[0].attrs.get("loop_restart") is True


def test_backslash_quote_inside_string_value_still_valid():
    r"""Backslash-quote INSIDE a quoted string (interior escape) must still work.

    The fix must not break the legitimate use: tool_command="echo \"hello\""
    where \"  is an interior escape for a literal double-quote character,
    not a delimiter.  The outer delimiters remain plain double-quotes.
    """
    graph = parse_dot(
        "digraph t {"
        "  s [shape=Mdiamond]; d [shape=Msquare]"
        '  s [tool_command="echo \\"hello world\\""]'
        "  s -> d"
        "}"
    )
    assert graph.nodes["s"].attrs.get("tool_command") == 'echo "hello world"'


def test_bare_key_truthy_condition_still_supported():
    """Bare-key truthy conditions (L-20) remain valid after the Layer A fix.

    The Layer A fix only rejects stray backslashes before a double-quote.
    A bare-key condition like condition="approved" (no operator) is
    intentional (CEXPR-011, Spec Section 10.5) and must continue to work.
    """
    from amplifier_module_loop_pipeline.conditions import evaluate_condition
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

    # Bare-key condition parses without error
    graph = parse_dot("""
    digraph test {
        start [shape=Mdiamond]; done [shape=Msquare]
        start -> done [condition="approved"]
    }
    """)
    edge = graph.edges[0]
    assert edge.condition == "approved"

    # Evaluates truthy when context key has a non-empty value
    ctx = PipelineContext()
    ctx.set("approved", "yes")
    outcome = Outcome(status=StageStatus.SUCCESS)
    assert evaluate_condition("approved", outcome, ctx) is True

    # Evaluates falsy when context key is absent
    ctx2 = PipelineContext()
    assert evaluate_condition("approved", outcome, ctx2) is False
