"""Tests for fidelity wiring into the AmplifierBackend (Tasks 1.4, 1.7).

Verifies that resolve_fidelity(), build_preamble(), and the session pool
are actually called from backend.py's run() method.

Spec coverage: FID-001-010, Section 4.5, Section 5.4.
"""

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockSession:
    """Minimal stand-in for AmplifierSession."""

    config: dict = {}


class MockCoordinator:
    """Mock coordinator that tracks spawn calls."""

    def __init__(self, spawn_result: dict | None = None):
        self._spawn_result = spawn_result or {
            "output": "done",
            "session_id": "child-1",
        }
        self.spawn_called = False
        self.spawn_call_count = 0
        self.last_spawn_kwargs: dict = {}
        # Provide session and config like a real coordinator
        self.session = _MockSession()
        self.config: dict = {"agents": {}}

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.spawn_called = True
        self.spawn_call_count += 1
        self.last_spawn_kwargs = kwargs
        return self._spawn_result


def _make_node(**kwargs) -> Node:
    defaults: dict = {"id": "implement", "prompt": "Build it"}
    defaults.update(kwargs)
    return Node(**defaults)


def _make_context(goal: str = "Build feature X") -> PipelineContext:
    ctx = PipelineContext()
    ctx.set("graph.goal", goal)
    return ctx


def _make_graph_with_fidelity(
    node_fidelity: str | None = None,
    edge_fidelity: str | None = None,
    graph_default: str | None = None,
) -> tuple[Node, Edge | None, Graph]:
    """Build a minimal graph with configurable fidelity settings."""
    node_attrs: dict = {"llm_provider": "anthropic"}
    if node_fidelity:
        node_attrs["fidelity"] = node_fidelity
    node = Node(id="impl", prompt="Do the work", attrs=node_attrs)

    edge_attrs: dict = {}
    if edge_fidelity:
        edge_attrs["fidelity"] = edge_fidelity
    edge = Edge(from_node="start", to_node="impl", attrs=edge_attrs)

    graph_attrs: dict = {}
    if graph_default:
        graph_attrs["default_fidelity"] = graph_default

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "impl": node,
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="impl"),
            Edge(from_node="impl", to_node="exit"),
        ],
        graph_attrs=graph_attrs,
    )
    return node, edge, graph


# ---------------------------------------------------------------------------
# Task 1.4: Fidelity preamble is prepended to prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_prepends_compact_preamble():
    """Backend builds a preamble from compact fidelity and prepends to prompt."""
    coordinator = MockCoordinator(spawn_result={"output": "done", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    node = _make_node(attrs={"llm_provider": "anthropic", "fidelity": "compact"})
    _, edge, graph = _make_graph_with_fidelity(node_fidelity="compact")
    context = _make_context("Build feature X")

    await backend.run(
        node,
        "Implement the plan",
        context,
        incoming_edge=edge,
        graph=graph,
    )

    # Verify spawn was called with a prompt that includes the preamble
    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "Goal:" in instruction  # compact preamble starts with Goal:
    assert "Implement the plan" in instruction


@pytest.mark.asyncio
async def test_backend_truncate_preamble_is_minimal():
    """Truncate fidelity produces a minimal preamble with just goal and run ID."""
    coordinator = MockCoordinator(spawn_result={"output": "done", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    node = _make_node(attrs={"llm_provider": "anthropic", "fidelity": "truncate"})
    _, edge, graph = _make_graph_with_fidelity(node_fidelity="truncate")
    context = _make_context("Build feature X")

    await backend.run(node, "Do the work", context, incoming_edge=edge, graph=graph)

    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "Goal:" in instruction
    assert "Do the work" in instruction


@pytest.mark.asyncio
async def test_backend_no_preamble_for_full_fidelity():
    """Full fidelity mode does not prepend a preamble (session is reused)."""
    coordinator = MockCoordinator(spawn_result={"output": "done", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    node = _make_node(attrs={"llm_provider": "anthropic", "fidelity": "full"})
    _, edge, graph = _make_graph_with_fidelity(node_fidelity="full")
    context = _make_context()

    await backend.run(node, "Do the work", context, incoming_edge=edge, graph=graph)

    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    # For full fidelity, the instruction should be the raw prompt without preamble
    assert instruction == "Do the work"


@pytest.mark.asyncio
async def test_backend_default_fidelity_is_compact():
    """Without explicit fidelity, default is compact (prepends preamble)."""
    coordinator = MockCoordinator(spawn_result={"output": "done", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    node = _make_node(attrs={"llm_provider": "anthropic"})  # No fidelity attr
    _, edge, graph = _make_graph_with_fidelity()  # No graph default either
    context = _make_context("Build feature X")

    await backend.run(node, "Do it", context, incoming_edge=edge, graph=graph)

    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "Goal:" in instruction  # compact is the default


# ---------------------------------------------------------------------------
# Task 1.7: Session pool for full fidelity thread reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_carries_history_via_parent_messages_for_full_fidelity():
    """Full fidelity with same thread_id carries history via parent_messages.

    The second node on the same thread must receive the first node's
    (instruction, output) exchange as parent_messages — NOT sub_session_id.
    sub_session_id was the type confusion (id-where-a-conversation-belongs)
    that caused the continuity bug.
    """
    coordinator = MockCoordinator(
        spawn_result={"output": "first task output", "session_id": "sess-abc"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    # First call: no prior history, spawns fresh
    node1 = _make_node(
        id="step1",
        attrs={
            "llm_provider": "anthropic",
            "fidelity": "full",
            "thread_id": "main-thread",
        },
    )
    _, edge, graph = _make_graph_with_fidelity(node_fidelity="full")
    context = _make_context()

    await backend.run(node1, "First task", context, incoming_edge=edge, graph=graph)
    first_call_kwargs = coordinator.last_spawn_kwargs

    # First call: no prior history → no parent_messages, no sub_session_id
    assert "sub_session_id" not in first_call_kwargs, (
        "sub_session_id must never appear on a full-fidelity spawn"
    )
    assert not first_call_kwargs.get("parent_messages"), (
        "First node on a thread has no prior history — no parent_messages"
    )

    # Second call: same thread_id should carry prior history as parent_messages
    node2 = _make_node(
        id="step2",
        attrs={
            "llm_provider": "anthropic",
            "fidelity": "full",
            "thread_id": "main-thread",
        },
    )
    edge2 = Edge(from_node="step1", to_node="step2")
    await backend.run(node2, "Second task", context, incoming_edge=edge2, graph=graph)
    second_call_kwargs = coordinator.last_spawn_kwargs

    # Second call: parent_messages carries the first node's exchange
    assert "parent_messages" in second_call_kwargs, (
        "Second full-fidelity node on same thread must receive parent_messages"
    )
    pm = second_call_kwargs["parent_messages"]
    assert len(pm) == 2, f"Expected 2 messages (user + assistant), got {len(pm)}: {pm}"
    assert pm[0]["role"] == "user"
    assert pm[0]["content"] == "First task"
    assert pm[1]["role"] == "assistant"
    assert pm[1]["content"] == "first task output"

    # Second call: never uses sub_session_id
    assert "sub_session_id" not in second_call_kwargs, (
        "sub_session_id must never appear on a full-fidelity spawn; "
        "continuity is via parent_messages"
    )


@pytest.mark.asyncio
async def test_backend_separate_sessions_for_different_threads():
    """Different thread_ids get separate sessions, not reused."""
    coordinator = MockCoordinator(
        spawn_result={"output": "done", "session_id": "sess-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    _, edge, graph = _make_graph_with_fidelity(node_fidelity="full")
    context = _make_context()

    node1 = _make_node(
        id="a",
        attrs={
            "llm_provider": "anthropic",
            "fidelity": "full",
            "thread_id": "thread-A",
        },
    )
    await backend.run(node1, "Task A", context, incoming_edge=edge, graph=graph)

    # Second call with different thread
    coordinator._spawn_result = {"output": "done", "session_id": "sess-2"}
    node2 = _make_node(
        id="b",
        attrs={
            "llm_provider": "anthropic",
            "fidelity": "full",
            "thread_id": "thread-B",
        },
    )
    await backend.run(node2, "Task B", context, incoming_edge=edge, graph=graph)

    # Should NOT have sub_session_id since thread-B is new
    assert "sub_session_id" not in coordinator.last_spawn_kwargs


@pytest.mark.asyncio
async def test_backend_records_completed_node_outcomes():
    """Backend tracks completed node outcomes for preamble building."""
    coordinator = MockCoordinator(spawn_result={"output": "done", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    _, edge, graph = _make_graph_with_fidelity(node_fidelity="compact")
    context = _make_context("Build it")

    node1 = _make_node(
        id="plan", attrs={"llm_provider": "anthropic", "fidelity": "compact"}
    )
    await backend.run(node1, "Plan the work", context, incoming_edge=edge, graph=graph)

    # After first run, the backend should have recorded the outcome
    node2 = _make_node(
        id="impl", attrs={"llm_provider": "anthropic", "fidelity": "compact"}
    )
    await backend.run(node2, "Implement", context, incoming_edge=edge, graph=graph)

    # Second call's preamble should mention the completed stage
    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "plan" in instruction.lower()  # completed stage appears in preamble


# ---------------------------------------------------------------------------
# session_id capture: backend sets outcome.session_id from spawn result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_captures_session_id_on_outcome():
    """Backend sets outcome.session_id from spawn result (all fidelity modes)."""
    coordinator = MockCoordinator(
        spawn_result={"output": "done", "session_id": "child-sess-xyz"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    outcome = await backend.run(node, "Do the work", _make_context())

    assert outcome.session_id == "child-sess-xyz"


@pytest.mark.asyncio
async def test_backend_captures_session_id_for_compact_fidelity():
    """Backend captures session_id onto outcome for compact fidelity (not just full)."""
    coordinator = MockCoordinator(
        spawn_result={"output": "done", "session_id": "compact-sess-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node, edge, graph = _make_graph_with_fidelity(node_fidelity="compact")
    outcome = await backend.run(
        node, "Do the work", _make_context(), incoming_edge=edge, graph=graph
    )

    assert outcome.session_id == "compact-sess-1"


@pytest.mark.asyncio
async def test_backend_session_id_none_when_not_in_result():
    """outcome.session_id is None when spawn result has no session_id key."""
    coordinator = MockCoordinator(spawn_result={"output": "done"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    outcome = await backend.run(node, "Do the work", _make_context())

    assert outcome.session_id is None
