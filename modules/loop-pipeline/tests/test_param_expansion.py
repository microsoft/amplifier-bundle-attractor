"""Tests for $param template expansion in transforms.

Covers Task 3.1: expand_params() function and its integration with
expand_variables() for replacing $param_name tokens in node prompts.
"""

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.transforms import expand_params, expand_variables

# --- Unit tests for expand_params ---


def test_expand_goal_still_works():
    """Existing $goal expansion still works after adding param support."""
    context = PipelineContext()
    context.set("graph.goal", "build auth system")
    dot = 'digraph { start [shape=Mdiamond]; a [prompt="Do $goal"]; done [shape=Msquare]; start -> a -> done }'
    graph = parse_dot(dot)
    expand_variables(graph, context)
    assert graph.nodes["a"].prompt == "Do build auth system"


def test_expand_custom_param():
    """$language in prompt replaced with 'Python' from params."""
    result = expand_params(
        "Write code in $language",
        {"language": "Python"},
    )
    assert result == "Write code in Python"


def test_expand_multiple_params():
    """Multiple params all expanded."""
    result = expand_params(
        "Create a $framework app in $language",
        {"framework": "FastAPI", "language": "Python"},
    )
    assert result == "Create a FastAPI app in Python"


def test_unknown_param_left_alone():
    """$unknown not in params stays as $unknown."""
    result = expand_params(
        "Build $unknown with $language",
        {"language": "Python"},
    )
    assert result == "Build $unknown with Python"


def test_expand_params_preserves_undefined_prefixed_param():
    """A defined param must not replace the prefix of an absent param token."""
    result = expand_params(
        "echo NAME=$name SUFFIXED=$name_suffix ID=$id ID2=$id2",
        {"name": "Alice", "id": "42"},
    )
    assert result == "echo NAME=Alice SUFFIXED=$name_suffix ID=42 ID2=$id2"


def test_expand_params_dot_after_key_with_no_dotted_sibling_substitutes():
    """A literal '.' after $key must not block substitution when no longer
    dotted key sharing that prefix exists in params (regression: this
    previously required '$name.txt' to stay literal)."""
    result = expand_params("cat $name.txt", {"name": "report"})
    assert result == "cat report.txt"


def test_expand_params_trailing_period_substitutes():
    """A sentence-ending period after $key must not block substitution."""
    result = expand_params("Run $tool. Then go.", {"tool": "X"})
    assert result == "Run X. Then go."


def test_expand_params_dot_still_blocks_when_dotted_sibling_exists():
    """When a longer dotted key sharing the prefix IS present in params, the
    '.' must still block the shorter key's substitution."""
    result = expand_params(
        "$tool.last_line",
        {"tool": "X", "tool.last_line": "Y"},
    )
    assert result == "Y"


def test_expand_in_graph_goal():
    """Params expand in graph-level goal attribute context too."""
    context = PipelineContext()
    context.set("graph.goal", "user authentication")
    context.set("graph.params_values", {"framework": "FastAPI"})
    dot = 'digraph { start [shape=Mdiamond]; a [prompt="Build a $framework app for: $goal"]; done [shape=Msquare]; start -> a -> done }'
    graph = parse_dot(dot)
    expand_variables(graph, context)
    assert graph.nodes["a"].prompt == "Build a FastAPI app for: user authentication"


def test_empty_params_no_crash():
    """Empty params dict doesn't break anything."""
    result = expand_params("Build a $framework app", {})
    assert result == "Build a $framework app"


# --- Integration: expand_variables with params in context ---


def test_expand_variables_with_params_in_context():
    """expand_variables replaces both $goal and $param tokens."""
    dot = 'digraph { start [shape=Mdiamond]; a [prompt="Build a $framework app for: $goal"]; done [shape=Msquare]; start -> a -> done }'
    graph = parse_dot(dot)
    context = PipelineContext()
    context.set("graph.goal", "user authentication")
    context.set("graph.params_values", {"framework": "FastAPI"})

    expand_variables(graph, context)

    assert graph.nodes["a"].prompt == "Build a FastAPI app for: user authentication"


def test_expand_variables_without_params():
    """expand_variables still works when no params are in context."""
    dot = 'digraph { start [shape=Mdiamond]; a [prompt="Do $goal"]; done [shape=Msquare]; start -> a -> done }'
    graph = parse_dot(dot)
    context = PipelineContext()
    context.set("graph.goal", "the thing")

    expand_variables(graph, context)

    assert graph.nodes["a"].prompt == "Do the thing"


def test_expand_params_coexists_with_goal():
    """$goal and $param expansion work together without interference."""
    # $goal should NOT be expanded by expand_params (it's handled separately)
    result = expand_params(
        "Do $goal in $language",
        {"language": "Python"},
    )
    assert result == "Do $goal in Python"


def test_expand_params_backslash_value_literal():
    """Param values containing backslashes are inserted verbatim (no re.sub escape interpretation)."""
    # Windows path: backslash followed by 'U' would raise re.error if not handled correctly
    result = expand_params("output=$path", {"path": r"C:\Users\Alice"})
    assert result == r"output=C:\Users\Alice"


def test_expand_params_backslash_newline_value_literal():
    """Param values with \\n are inserted as the two-char literal, not as a newline."""
    result = expand_params("msg=$text", {"text": r"hello\nworld"})
    assert result == r"msg=hello\nworld"
