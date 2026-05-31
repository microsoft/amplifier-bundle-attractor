"""Tests for fidelity=full intra-run session continuity.

Verifies the Candidate A design: _thread_transcripts carries conversation
across same-thread full-fidelity nodes via parent_messages (not session_id
reuse).

Design spec: docs/designs/fidelity-full-session-continuity.md
Spec coverage: §5.4 (full fidelity), §3.8 (sequential same-thread traversal),
               EXTENSIONS.md §12-13.
"""

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _MockSession:
    config: dict = {}


class _SpawnCapture:
    """Coordinator mock that captures every spawn call's kwargs."""

    def __init__(self, output: str = "done", session_id: str = "child-1"):
        self._output = output
        self._session_id = session_id
        self.calls: list[dict] = []  # all spawn kwarg dicts in call order
        self.session = _MockSession()
        self.config: dict = {"agents": {}}

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"output": self._output, "session_id": self._session_id}


def _make_full_node(node_id: str, thread_id: str = "main") -> Node:
    return Node(
        id=node_id,
        prompt="Do work",
        attrs={"llm_provider": "anthropic", "fidelity": "full", "thread_id": thread_id},
    )


def _make_graph_with_full_nodes(
    *node_ids: str, thread_id: str = "main"
) -> tuple[Graph, Edge]:
    """Build a minimal linear graph with all nodes set to fidelity=full."""
    nodes: dict[str, Node] = {"start": Node(id="start", shape="Mdiamond")}
    for nid in node_ids:
        nodes[nid] = _make_full_node(nid, thread_id=thread_id)
    nodes["exit"] = Node(id="exit", shape="Msquare")

    edges: list[Edge] = [Edge(from_node="start", to_node=node_ids[0])]
    for i in range(len(node_ids) - 1):
        edges.append(Edge(from_node=node_ids[i], to_node=node_ids[i + 1]))
    edges.append(Edge(from_node=node_ids[-1], to_node="exit"))

    incoming_edge = Edge(from_node="start", to_node=node_ids[0])
    graph = Graph(name="test", nodes=nodes, edges=edges, graph_attrs={})
    return graph, incoming_edge


def _make_context() -> PipelineContext:
    ctx = PipelineContext()
    ctx.set("graph.goal", "Test continuity")
    return ctx


# ---------------------------------------------------------------------------
# Test 1: Intra-run continuity — the core proof (RED on main, GREEN after fix)
#
# Node A runs on thread T → Node B runs on thread T →
# B's spawn must receive parent_messages=[{user: A.instr}, {asst: A.output}]
# B's spawn must NOT receive sub_session_id or session_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_second_node_gets_parent_messages():
    """Node B on the same thread sees Node A's exchange via parent_messages.

    The FIRST node on a thread gets no parent_messages (no prior history).
    The SECOND node sees the first node's (instruction, output) pair as
    parent_messages in user/assistant roles.

    Verifies the core of the Candidate A design:
      - No sub_session_id for full-fidelity continuity (type confusion removed)
      - parent_messages carries the prior exchange
    """
    coordinator = _SpawnCapture(output="First node output", session_id="sess-A")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    graph, _ = _make_graph_with_full_nodes("node_a", "node_b", thread_id="main-thread")
    context = _make_context()

    # Edges with the correct fidelity context
    edge_to_a = Edge(from_node="start", to_node="node_a")
    edge_to_b = Edge(from_node="node_a", to_node="node_b")

    # Run Node A
    node_a = graph.nodes["node_a"]
    await backend.run(
        node_a, "First instruction", context, incoming_edge=edge_to_a, graph=graph
    )

    # Run Node B (same thread)
    coordinator._output = "Second node output"
    coordinator._session_id = "sess-B"
    node_b = graph.nodes["node_b"]
    await backend.run(
        node_b, "Second instruction", context, incoming_edge=edge_to_b, graph=graph
    )

    # --- Assertions ---
    assert len(coordinator.calls) == 2, "Expected exactly 2 spawn calls"

    first_call = coordinator.calls[0]
    second_call = coordinator.calls[1]

    # First node: no prior history → no parent_messages
    assert "parent_messages" not in first_call or not first_call.get(
        "parent_messages"
    ), (
        "First full-fidelity node on a thread must NOT receive parent_messages "
        "(no prior history exists)"
    )
    # First node: never uses sub_session_id for continuity
    assert "sub_session_id" not in first_call, (
        "sub_session_id must never appear on a full-fidelity spawn "
        "(it was the type confusion that caused the continuity bug)"
    )

    # Second node: gets parent_messages from Node A's exchange
    assert "parent_messages" in second_call, (
        "Second full-fidelity node on same thread must receive parent_messages"
    )
    pm = second_call["parent_messages"]
    assert isinstance(pm, list), "parent_messages must be a list"
    assert len(pm) == 2, (
        f"One prior exchange = 2 messages (user + assistant). Got {len(pm)}: {pm}"
    )
    assert pm[0]["role"] == "user", "First message in exchange must be role=user"
    assert pm[0]["content"] == "First instruction", (
        "user message content must be the node's instruction"
    )
    assert pm[1]["role"] == "assistant", (
        "Second message in exchange must be role=assistant"
    )
    assert pm[1]["content"] == "First node output", (
        "assistant message content must be the node's final output"
    )

    # Second node: no sub_session_id (continuity is via parent_messages now)
    assert "sub_session_id" not in second_call, (
        "sub_session_id must never appear on a full-fidelity spawn; "
        "continuity is via parent_messages"
    )


@pytest.mark.asyncio
async def test_full_fidelity_transcript_grows_across_nodes():
    """Three nodes on the same thread: each subsequent node gets growing history.

    Node A: no parent_messages (first)
    Node B: 2 messages (A's exchange)
    Node C: 4 messages (A + B exchanges)
    """
    coordinator = _SpawnCapture(output="output-A", session_id="sess-1")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    graph, _ = _make_graph_with_full_nodes("a", "b", "c", thread_id="T")
    context = _make_context()

    edge_ab = Edge(from_node="start", to_node="a")
    edge_bc = Edge(from_node="a", to_node="b")
    edge_cd = Edge(from_node="b", to_node="c")

    coordinator._output = "out-A"
    await backend.run(
        graph.nodes["a"], "instr-A", context, incoming_edge=edge_ab, graph=graph
    )

    coordinator._output = "out-B"
    await backend.run(
        graph.nodes["b"], "instr-B", context, incoming_edge=edge_bc, graph=graph
    )

    coordinator._output = "out-C"
    await backend.run(
        graph.nodes["c"], "instr-C", context, incoming_edge=edge_cd, graph=graph
    )

    assert len(coordinator.calls) == 3
    # Node A: no prior messages
    assert not coordinator.calls[0].get("parent_messages")
    # Node B: 2 messages (A's exchange)
    pm_b = coordinator.calls[1].get("parent_messages", [])
    assert len(pm_b) == 2
    # Node C: 4 messages (A + B exchanges)
    pm_c = coordinator.calls[2].get("parent_messages", [])
    assert len(pm_c) == 4
    assert pm_c[0] == {"role": "user", "content": "instr-A"}
    assert pm_c[1] == {"role": "assistant", "content": "out-A"}
    assert pm_c[2] == {"role": "user", "content": "instr-B"}
    assert pm_c[3] == {"role": "assistant", "content": "out-B"}


# ---------------------------------------------------------------------------
# Test 2: Idempotency — goal-gate retry doesn't double-append
#
# A node that is re-run (simulating a goal-gate retry that re-executes the node)
# should replace its prior turn, not duplicate it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_idempotent_on_node_rerun():
    """Re-running a full-fidelity node replaces its transcript turn (not duplicates).

    Simulates a goal-gate retry that re-executes node_a. The next node (node_b)
    should see exactly ONE exchange from node_a in its parent_messages, not two.
    """
    coordinator = _SpawnCapture(output="initial-output", session_id="s1")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    graph, _ = _make_graph_with_full_nodes("node_a", "node_b", thread_id="T")
    context = _make_context()
    edge_to_a = Edge(from_node="start", to_node="node_a")
    edge_to_b = Edge(from_node="node_a", to_node="node_b")

    # First run of node_a
    coordinator._output = "output-attempt-1"
    await backend.run(
        graph.nodes["node_a"],
        "instr-attempt-1",
        context,
        incoming_edge=edge_to_a,
        graph=graph,
    )

    # Simulate goal-gate retry: node_a is re-run (same node_id, same thread)
    coordinator._output = "output-attempt-2"
    await backend.run(
        graph.nodes["node_a"],
        "instr-attempt-2",
        context,
        incoming_edge=edge_to_a,
        graph=graph,
    )

    # Now node_b runs — it should see ONLY the second attempt's exchange
    coordinator._output = "out-B"
    await backend.run(
        graph.nodes["node_b"], "instr-B", context, incoming_edge=edge_to_b, graph=graph
    )

    b_call = coordinator.calls[-1]
    pm = b_call.get("parent_messages", [])
    assert len(pm) == 2, (
        f"Node B must see exactly ONE exchange from the re-run node_a. "
        f"Got {len(pm)} messages: {pm}"
    )
    assert pm[0]["content"] == "instr-attempt-2", (
        "The SECOND attempt's instruction must replace the first"
    )
    assert pm[1]["content"] == "output-attempt-2", (
        "The SECOND attempt's output must replace the first"
    )


# ---------------------------------------------------------------------------
# Test 3: Isolation — clone resets transcript; same thread_id in sibling
# branches does NOT join conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_clone_has_independent_transcript():
    """clone() resets _thread_transcripts; branch clones are transcript-isolated.

    If two branch clones share the same thread_id, they each get their own
    independent transcript — they do NOT share history (§3.8 isolation).
    """
    coordinator = _SpawnCapture(output="parent-output", session_id="parent-sess")
    parent_backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    graph, _ = _make_graph_with_full_nodes(
        "node_a", "node_b", thread_id="shared-thread"
    )
    context = _make_context()
    edge_to_a = Edge(from_node="start", to_node="node_a")
    edge_to_b = Edge(from_node="node_a", to_node="node_b")

    # Parent backend runs node_a — builds a transcript entry
    coordinator._output = "parent-node-a-output"
    await parent_backend.run(
        graph.nodes["node_a"],
        "parent-instr-A",
        context,
        incoming_edge=edge_to_a,
        graph=graph,
    )

    # Clone the backend (simulates branch isolation)
    branch_backend = parent_backend.clone()

    # Branch backend runs node_b — its transcript is FRESH (clone resets it)
    # So node_b in the branch must NOT see parent backend's node_a exchange
    coord_b = _SpawnCapture(output="branch-output", session_id="branch-sess")
    branch_backend._coordinator = coord_b
    branch_backend._spawn_fn = None
    branch_backend._spawn_checked = False

    await branch_backend.run(
        graph.nodes["node_b"],
        "branch-instr-B",
        context,
        incoming_edge=edge_to_b,
        graph=graph,
    )

    # The branch's node_b spawn must NOT have parent_messages from the parent backend
    assert len(coord_b.calls) == 1
    branch_call = coord_b.calls[0]
    pm = branch_call.get("parent_messages") or []
    assert len(pm) == 0, (
        f"Branch backend must have a fresh transcript (clone resets it). "
        f"Got unexpected parent_messages: {pm}"
    )


# ---------------------------------------------------------------------------
# Test 4: Loud-not-silent (CR-1) — sub_session_id never appears in full spawns
#
# The backend guarantees mutual exclusion of parent_messages and sub_session_id
# by construction. Verify this for all full-fidelity calls.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_never_uses_sub_session_id():
    """Full-fidelity spawns must NEVER include sub_session_id in any call.

    The old broken implementation stored a session_id and re-passed it as
    sub_session_id. The fix removes this path entirely. Continuity is always
    via parent_messages, never via session resumption.
    """
    coordinator = _SpawnCapture(output="done", session_id="sess-xyz")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    graph, _ = _make_graph_with_full_nodes("a", "b", "c", thread_id="T")
    context = _make_context()

    edges = [
        Edge(from_node="start", to_node="a"),
        Edge(from_node="a", to_node="b"),
        Edge(from_node="b", to_node="c"),
    ]
    nodes = ["a", "b", "c"]
    instructions = ["instr-A", "instr-B", "instr-C"]

    for node_id, edge, instr in zip(nodes, edges, instructions):
        await backend.run(
            graph.nodes[node_id], instr, context, incoming_edge=edge, graph=graph
        )

    for i, call in enumerate(coordinator.calls):
        assert "sub_session_id" not in call, (
            f"Call {i} for full-fidelity spawn must not include sub_session_id. "
            f"sub_session_id is the broken mechanism that caused the continuity bug. "
            f"Full kwargs: {call}"
        )


# ---------------------------------------------------------------------------
# Test 5: Different threads remain independent (non-regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_different_threads_dont_share_transcript():
    """Nodes on different thread_ids keep independent transcripts.

    node_a (thread=T1) and node_c (thread=T2) must not share history.
    node_b follows node_a on T1; it sees node_a's exchange.
    node_d follows node_c on T2; it must NOT see T1 history.
    """
    coordinator = _SpawnCapture(output="done", session_id="s1")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    # Build a graph with 4 nodes across two threads
    nodes_map = {
        "start": Node(id="start", shape="Mdiamond"),
        "node_a": _make_full_node("node_a", thread_id="T1"),
        "node_b": _make_full_node("node_b", thread_id="T1"),
        "node_c": _make_full_node("node_c", thread_id="T2"),
        "node_d": _make_full_node("node_d", thread_id="T2"),
        "exit": Node(id="exit", shape="Msquare"),
    }
    edges = [
        Edge(from_node="start", to_node="node_a"),
        Edge(from_node="node_a", to_node="node_b"),
        Edge(from_node="node_b", to_node="node_c"),
        Edge(from_node="node_c", to_node="node_d"),
        Edge(from_node="node_d", to_node="exit"),
    ]
    graph = Graph(name="test", nodes=nodes_map, edges=edges, graph_attrs={})
    context = _make_context()

    # Thread T1
    coordinator._output = "out-A"
    await backend.run(
        graph.nodes["node_a"],
        "instr-A",
        context,
        incoming_edge=edges[0],
        graph=graph,
    )
    coordinator._output = "out-B"
    await backend.run(
        graph.nodes["node_b"],
        "instr-B",
        context,
        incoming_edge=edges[1],
        graph=graph,
    )

    # Thread T2 — separate thread, should start fresh
    coordinator._output = "out-C"
    await backend.run(
        graph.nodes["node_c"],
        "instr-C",
        context,
        incoming_edge=edges[2],
        graph=graph,
    )
    coordinator._output = "out-D"
    await backend.run(
        graph.nodes["node_d"],
        "instr-D",
        context,
        incoming_edge=edges[3],
        graph=graph,
    )

    assert len(coordinator.calls) == 4

    # node_a (first on T1): no parent_messages
    assert not coordinator.calls[0].get("parent_messages")
    # node_b: sees T1's history only (A's exchange)
    pm_b = coordinator.calls[1].get("parent_messages", [])
    assert len(pm_b) == 2
    assert pm_b[0]["content"] == "instr-A"

    # node_c (first on T2): no parent_messages (fresh thread)
    assert not coordinator.calls[2].get("parent_messages"), (
        "node_c is the first node on T2 — must start with no parent_messages"
    )
    # node_d (second on T2): sees only T2 history
    pm_d = coordinator.calls[3].get("parent_messages", [])
    assert len(pm_d) == 2, (
        f"node_d on T2 must see only T2 history (node_c's exchange). "
        f"Got {len(pm_d)} messages: {pm_d}"
    )
    assert pm_d[0]["content"] == "instr-C", "node_d must see T2 history, not T1"
