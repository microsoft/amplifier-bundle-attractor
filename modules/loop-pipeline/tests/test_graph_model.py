"""Tests for the graph data model."""

import logging

from amplifier_module_loop_pipeline.graph import Edge, Graph, Node, resolve_bool_attr


def test_node_defaults():
    """Node should have sensible defaults per spec Section 2.6."""
    node = Node(id="step1")
    assert node.id == "step1"
    assert node.label == "step1"  # label defaults to node ID
    assert node.shape == "box"
    assert node.type == ""
    assert node.prompt == ""
    assert node.attrs == {}
    assert node.handler_type == ""


def test_node_with_attributes():
    """Node should accept all spec attributes."""
    node = Node(
        id="implement",
        label="Implement Feature",
        shape="box",
        prompt="Build the feature for $goal",
        attrs={"max_retries": 3, "goal_gate": True, "timeout": 900000},
    )
    assert node.label == "Implement Feature"
    assert node.prompt == "Build the feature for $goal"
    assert node.attrs["max_retries"] == 3
    assert node.attrs["goal_gate"] is True


def test_edge_defaults():
    """Edge should have sensible defaults per spec Section 2.7."""
    edge = Edge(from_node="A", to_node="B")
    assert edge.from_node == "A"
    assert edge.to_node == "B"
    assert edge.label == ""
    assert edge.condition == ""
    assert edge.weight == 0
    assert edge.attrs == {}


def test_edge_with_attributes():
    """Edge should accept condition and weight."""
    edge = Edge(
        from_node="gate",
        to_node="exit",
        label="Yes",
        condition="outcome=success",
        weight=5,
    )
    assert edge.label == "Yes"
    assert edge.condition == "outcome=success"
    assert edge.weight == 5


def test_graph_construction():
    """Graph should hold nodes, edges, and graph-level attributes."""
    nodes = {
        "start": Node(id="start", shape="Mdiamond"),
        "work": Node(id="work", label="Do Work", prompt="Do the work"),
        "exit": Node(id="exit", shape="Msquare"),
    }
    edges = [
        Edge(from_node="start", to_node="work"),
        Edge(from_node="work", to_node="exit"),
    ]
    graph = Graph(name="test_pipeline", nodes=nodes, edges=edges)
    assert graph.name == "test_pipeline"
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2
    assert graph.goal == ""
    assert graph.default_max_retry == 0
    assert graph.graph_attrs == {}


def test_graph_with_goal():
    """Graph should store the goal attribute."""
    graph = Graph(
        name="pipeline",
        nodes={},
        edges=[],
        goal="Build the feature",
        default_max_retry=5,
    )
    assert graph.goal == "Build the feature"
    assert graph.default_max_retry == 5


def test_graph_outgoing_edges():
    """Graph.outgoing_edges should return edges from a given node."""
    edges = [
        Edge(from_node="A", to_node="B"),
        Edge(from_node="A", to_node="C"),
        Edge(from_node="B", to_node="C"),
    ]
    graph = Graph(name="test", nodes={}, edges=edges)
    outgoing = graph.outgoing_edges("A")
    assert len(outgoing) == 2
    assert {e.to_node for e in outgoing} == {"B", "C"}


def test_graph_incoming_edges():
    """Graph.incoming_edges should return edges targeting a given node."""
    edges = [
        Edge(from_node="A", to_node="C"),
        Edge(from_node="B", to_node="C"),
        Edge(from_node="C", to_node="D"),
    ]
    graph = Graph(name="test", nodes={}, edges=edges)
    incoming = graph.incoming_edges("C")
    assert len(incoming) == 2
    assert {e.from_node for e in incoming} == {"A", "B"}


def test_node_label_defaults_to_id():
    """When label is not provided, it should default to the node ID."""
    node = Node(id="my_step")
    assert node.label == "my_step"


def test_node_explicit_label_overrides_default():
    """An explicit label should be used instead of the ID."""
    node = Node(id="my_step", label="My Step")
    assert node.label == "My Step"


# --- M-10: Node first-class fields ---


def test_node_first_class_fields_defaults():
    """All 12 promoted node attributes should have sensible defaults (M-10)."""
    node = Node(id="step")
    assert node.max_retries is None
    assert node.goal_gate is None
    assert node.retry_target is None
    assert node.fallback_retry_target is None
    assert node.fidelity is None
    assert node.thread_id is None
    assert node.timeout is None
    assert node.llm_model is None
    assert node.llm_provider is None
    assert node.reasoning_effort is None
    assert node.auto_status is None
    assert node.allow_partial is None


def test_node_first_class_fields_set():
    """Promoted fields should be settable via constructor (M-10)."""
    node = Node(
        id="step",
        max_retries=3,
        goal_gate=True,
        retry_target="plan",
        fallback_retry_target="start",
        fidelity="full",
        thread_id="t1",
        timeout=30000,
        llm_model="gpt-4",
        llm_provider="openai",
        reasoning_effort="high",
        auto_status=True,
        allow_partial=False,
    )
    assert node.max_retries == 3
    assert node.goal_gate is True
    assert node.retry_target == "plan"
    assert node.fallback_retry_target == "start"
    assert node.fidelity == "full"
    assert node.thread_id == "t1"
    assert node.timeout == 30000
    assert node.llm_model == "gpt-4"
    assert node.llm_provider == "openai"
    assert node.reasoning_effort == "high"
    assert node.auto_status is True
    assert node.allow_partial is False


def test_node_attrs_dict_backward_compat():
    """Accessing promoted fields via attrs dict should still work (M-10)."""
    node = Node(id="step", goal_gate=True, max_retries=3, fidelity="full")
    # Backward compat: node.attrs.get("goal_gate") should return the value
    assert node.attrs.get("goal_gate") is True
    assert node.attrs.get("max_retries") == 3
    assert node.attrs.get("fidelity") == "full"
    # Non-existent keys still return None
    assert node.attrs.get("nonexistent") is None


def test_node_attrs_dict_set_promotes_to_field():
    """Setting a promoted key on attrs should be reflected in the field (M-10)."""
    node = Node(id="step")
    node.attrs["goal_gate"] = True
    # The field should reflect the value
    assert node.goal_gate is True


# --- L-5: Edge first-class fields ---


def test_edge_first_class_fields_defaults():
    """All promoted edge attributes should have sensible defaults (L-5)."""
    edge = Edge(from_node="A", to_node="B")
    assert edge.fidelity is None
    assert edge.thread_id is None
    assert edge.loop_restart is None


def test_edge_first_class_fields_set():
    """Promoted edge fields should be settable via constructor (L-5)."""
    edge = Edge(
        from_node="A",
        to_node="B",
        fidelity="full",
        thread_id="t1",
        loop_restart=True,
    )
    assert edge.fidelity == "full"
    assert edge.thread_id == "t1"
    assert edge.loop_restart is True


def test_edge_attrs_dict_backward_compat():
    """Accessing promoted fields via attrs dict should still work (L-5)."""
    edge = Edge(from_node="A", to_node="B", fidelity="full", thread_id="t1")
    assert edge.attrs.get("fidelity") == "full"
    assert edge.attrs.get("thread_id") == "t1"
    assert edge.attrs.get("nonexistent") is None


def test_edge_attrs_from_dict_promotes():
    """Promoted keys passed in attrs dict should be promoted to fields (L-5)."""
    edge = Edge(from_node="A", to_node="B", attrs={"fidelity": "compact", "custom": 42})
    assert edge.fidelity == "compact"
    assert edge.attrs.get("custom") == 42


# --- L-6: Graph first-class fields ---


def test_graph_first_class_fields_defaults():
    """All promoted graph attributes should have sensible defaults (L-6)."""
    graph = Graph(name="test", nodes={}, edges=[])
    assert graph.retry_target is None
    assert graph.fallback_retry_target is None
    assert graph.default_fidelity is None
    assert graph.label is None


def test_graph_first_class_fields_set():
    """Promoted graph fields should be settable via constructor (L-6)."""
    graph = Graph(
        name="test",
        nodes={},
        edges=[],
        retry_target="plan",
        fallback_retry_target="start",
        default_fidelity="full",
        label="My Pipeline",
    )
    assert graph.retry_target == "plan"
    assert graph.fallback_retry_target == "start"
    assert graph.default_fidelity == "full"
    assert graph.label == "My Pipeline"


def test_graph_attrs_dict_backward_compat():
    """Accessing promoted fields via graph_attrs dict should still work (L-6)."""
    graph = Graph(
        name="test",
        nodes={},
        edges=[],
        retry_target="plan",
        default_fidelity="full",
    )
    assert graph.graph_attrs.get("retry_target") == "plan"
    assert graph.graph_attrs.get("default_fidelity") == "full"
    assert graph.graph_attrs.get("nonexistent") is None


def test_graph_attrs_from_dict_promotes():
    """Promoted keys passed in graph_attrs dict should promote to fields (L-6)."""
    graph = Graph(
        name="test",
        nodes={},
        edges=[],
        graph_attrs={"retry_target": "plan", "rankdir": "LR"},
    )
    assert graph.retry_target == "plan"
    assert graph.graph_attrs.get("rankdir") == "LR"


# --- source_dir field ---


def test_graph_source_dir_defaults_to_empty():
    """Graph.source_dir should default to empty string."""
    graph = Graph(name="test", nodes={}, edges=[])
    assert graph.source_dir == ""


def test_graph_source_dir_set():
    """Graph.source_dir should be settable via constructor."""
    graph = Graph(name="test", nodes={}, edges=[], source_dir="/some/path")
    assert graph.source_dir == "/some/path"


# --- resolve_bool_attr: shared boolean-attribute accessor (issue #389) ---


def test_resolve_bool_attr_real_bool():
    """A real Python bool passes through unchanged."""
    assert resolve_bool_attr(True, "goal_gate") is True
    assert resolve_bool_attr(False, "goal_gate") is False


def test_resolve_bool_attr_quoted_string_true_false():
    """Quoted 'true'/'false' strings (as produced by the DOT parser for
    quoted attribute values) are interpreted as their boolean equivalents.
    """
    assert resolve_bool_attr("true", "continue_on_fail") is True
    assert resolve_bool_attr("false", "continue_on_fail") is False


def test_resolve_bool_attr_case_insensitive():
    """String matching is case-insensitive and tolerates surrounding whitespace."""
    assert resolve_bool_attr("True", "auto_status") is True
    assert resolve_bool_attr("TRUE", "auto_status") is True
    assert resolve_bool_attr(" true ", "auto_status") is True
    assert resolve_bool_attr("False", "auto_status") is False
    assert resolve_bool_attr("FALSE", "auto_status") is False


def test_resolve_bool_attr_none_is_false():
    """A missing attribute (None) resolves to False without a warning."""
    assert resolve_bool_attr(None, "allow_partial") is False


def test_resolve_bool_attr_none_does_not_warn(caplog):
    """None is a normal 'attribute not set' case -- it must not warn."""
    with caplog.at_level(logging.WARNING):
        resolve_bool_attr(None, "allow_partial")
    assert len(caplog.records) == 0, (
        f"Expected no warning for value=None, got {[r.message for r in caplog.records]!r}"
    )


def test_resolve_bool_attr_uninterpretable_value_warns_and_is_false(caplog):
    """An uninterpretable value (e.g. a typo) resolves to False AND logs a
    WARNING naming the attribute and the offending value.

    This is the "make the uninterpretable case loud" requirement from
    issue #389: an unrecognized value must not fail silently the same way
    the original bug did.
    """
    with caplog.at_level(logging.WARNING):
        result = resolve_bool_attr("maybe", "continue_on_fail")

    assert result is False
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1, (
        f"Expected exactly one WARNING, got {[r.message for r in caplog.records]!r}"
    )
    message = warning_records[0].message
    assert "continue_on_fail" in message, (
        f"Expected the attribute name in the warning, got {message!r}"
    )
    assert "maybe" in message, (
        f"Expected the offending value in the warning, got {message!r}"
    )


def test_resolve_bool_attr_uninterpretable_numeric_string_warns():
    """A numeric-looking string like '1' is uninterpretable and warns."""
    import logging as _logging

    logger = _logging.getLogger("amplifier_module_loop_pipeline.graph")
    with_records: list[logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            with_records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        assert resolve_bool_attr("1", "parse_json") is False
    finally:
        logger.removeHandler(handler)
    assert any("parse_json" in r.getMessage() for r in with_records), (
        "Expected a WARNING naming 'parse_json' for the uninterpretable value '1'"
    )
