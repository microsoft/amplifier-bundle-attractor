"""Unit tests for node timeout unit conversion (Bug 1).

The DOT parser stores all durations as milliseconds (dot_parser._DURATION_UNITS).
Engine and tool handler must divide node.timeout by 1000 to obtain seconds.

Spec: attractor-spec.md uses timeout_seconds.
Invariant: timeout="900s" in DOT -> 900-second effective asyncio.timeout.
"""

from amplifier_module_loop_pipeline.dot_parser import parse_dot


# ---------------------------------------------------------------------------
# Parser output: node.timeout stores milliseconds
# ---------------------------------------------------------------------------


def test_timeout_900s_stored_as_milliseconds():
    """Parser converts '900s' to 900000 ms stored on node.timeout."""
    graph = parse_dot('digraph { A [timeout="900s"] }')
    node = graph.nodes["A"]
    assert node.timeout == 900_000  # 900 * 1_000


def test_timeout_2m_stored_as_milliseconds():
    """Parser converts '2m' to 120000 ms stored on node.timeout."""
    graph = parse_dot('digraph { A [timeout="2m"] }')
    node = graph.nodes["A"]
    assert node.timeout == 120_000  # 2 * 60 * 1_000


def test_timeout_1h_stored_as_milliseconds():
    """Parser converts '1h' to 3600000 ms stored on node.timeout."""
    graph = parse_dot('digraph { A [timeout="1h"] }')
    node = graph.nodes["A"]
    assert node.timeout == 3_600_000  # 1 * 3600 * 1_000


# ---------------------------------------------------------------------------
# Consumer conversion: divide by 1000 to get effective seconds
# ---------------------------------------------------------------------------


def test_timeout_900s_yields_effective_900_seconds():
    """timeout='900s' in DOT must yield 900 effective seconds after /1000."""
    graph = parse_dot('digraph { A [timeout="900s"] }')
    node = graph.nodes["A"]
    assert node.timeout is not None, "node.timeout must be set"
    # This is the conversion the engine and tool handler now apply:
    timeout_s = float(node.timeout) / 1000.0
    assert timeout_s == 900.0


def test_timeout_2m_yields_effective_120_seconds():
    """timeout='2m' in DOT must yield 120 effective seconds after /1000."""
    graph = parse_dot('digraph { A [timeout="2m"] }')
    node = graph.nodes["A"]
    assert node.timeout is not None, "node.timeout must be set"
    timeout_s = float(node.timeout) / 1000.0
    assert timeout_s == 120.0


def test_timeout_30s_yields_effective_30_seconds():
    """timeout='30s' in DOT must yield 30 effective seconds after /1000."""
    graph = parse_dot('digraph { A [timeout="30s"] }')
    node = graph.nodes["A"]
    assert node.timeout is not None, "node.timeout must be set"
    timeout_s = float(node.timeout) / 1000.0
    assert timeout_s == 30.0


# ---------------------------------------------------------------------------
# Regression: max_pipeline_duration is in ms on BOTH sides — unchanged
# ---------------------------------------------------------------------------


def test_max_pipeline_duration_stored_as_milliseconds():
    """Graph-level max_pipeline_duration is stored in ms and compared in ms.

    This is intentionally ms on both sides (engine.py:277) and must NOT be
    divided — only the per-node timeout needs the /1000 conversion.
    """
    graph = parse_dot('digraph { max_pipeline_duration="5m" }')
    assert graph.max_pipeline_duration == 300_000  # 5 * 60 * 1_000 ms


# ---------------------------------------------------------------------------
# Regression: BARE-INTEGER timeout means SECONDS (Fix B)
#
# A bare integer (no unit suffix) is the form every shipped pipeline actually
# uses (e.g. dot-graph pr_feedback.dot timeout="300"). Before this fix the
# parser stored it as the raw int 300 and the engine's /1000 enforcement made
# it 0.3s, killing every LLM/tool node on arrival. A bare integer must be
# interpreted as SECONDS and stored as milliseconds, exactly like "300s".
#
# This is the test gap that let the original regression ship: the suite only
# ever exercised suffixed values.
# ---------------------------------------------------------------------------


def test_bare_integer_timeout_stored_as_milliseconds():
    """Parser treats bare '300' as 300 seconds -> 300000 ms (like '300s')."""
    graph = parse_dot('digraph { A [timeout="300"] }')
    node = graph.nodes["A"]
    assert node.timeout == 300_000  # 300 seconds * 1_000


def test_bare_integer_timeout_yields_effective_300_seconds():
    """timeout='300' (bare) must yield 300 effective seconds after /1000."""
    graph = parse_dot('digraph { A [timeout="300"] }')
    node = graph.nodes["A"]
    assert node.timeout is not None
    timeout_s = float(node.timeout) / 1000.0
    assert timeout_s == 300.0  # NOT 0.3


def test_bare_integer_600_matches_documented_600s_intent():
    """pr_feedback.dot's Decide node 'timeout=\"600\"' must mean 600s, not 0.6s."""
    graph = parse_dot('digraph { Decide [timeout="600"] }')
    node = graph.nodes["Decide"]
    assert node.timeout is not None
    assert float(node.timeout) / 1000.0 == 600.0


def test_bare_integer_unquoted_timeout_is_seconds():
    """An unquoted bare integer timeout is also seconds."""
    graph = parse_dot("digraph { A [timeout=300] }")
    node = graph.nodes["A"]
    assert node.timeout is not None
    assert float(node.timeout) / 1000.0 == 300.0


def test_suffixed_and_bare_agree_for_same_number():
    """'300' and '300s' must produce identical effective timeouts."""
    bare = parse_dot('digraph { A [timeout="300"] }').nodes["A"].timeout
    suffixed = parse_dot('digraph { A [timeout="300s"] }').nodes["A"].timeout
    assert bare == suffixed == 300_000


def test_subsecond_suffixed_timeout_preserved():
    """Sub-second suffixed values (e.g. '250ms') are NOT rescaled to seconds."""
    graph = parse_dot('digraph { A [timeout="250ms"] }')
    node = graph.nodes["A"]
    assert node.timeout == 250  # 0.25s — a real, intended sub-second value


def test_node_default_bare_integer_timeout_is_seconds():
    """A bare integer node-default timeout is normalized to seconds too."""
    graph = parse_dot('digraph { node [timeout="120"]; A; B [timeout="900s"] }')
    a_timeout = graph.nodes["A"].timeout
    b_timeout = graph.nodes["B"].timeout
    assert a_timeout is not None and b_timeout is not None
    assert float(a_timeout) / 1000.0 == 120.0  # inherited default
    assert float(b_timeout) / 1000.0 == 900.0  # explicit suffix


# ---------------------------------------------------------------------------
# Guard: the bare-integer rule is TIMEOUT-SPECIFIC and must not leak to other
# integer-valued attributes (e.g. max_agent_turns), which would be corrupted
# if _try_parse_duration started treating bare ints as durations globally.
# ---------------------------------------------------------------------------


def test_non_timeout_integer_attr_not_rescaled():
    """max_agent_turns='22' must stay 22, not become 22000."""
    graph = parse_dot('digraph { A [timeout="300", max_agent_turns="22"] }')
    node = graph.nodes["A"]
    assert str(node.attrs.get("max_agent_turns")) == "22"
    assert node.timeout == 300_000
