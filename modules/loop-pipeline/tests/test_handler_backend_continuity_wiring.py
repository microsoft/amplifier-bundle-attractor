"""Integration tests for the CodergenHandler → AmplifierBackend continuity wiring.

These tests exercise the REAL path that production uses: the engine /
run_subgraph invoke ``CodergenHandler.execute(node, context, graph, logs_root)``,
and the handler must forward ``graph`` into ``backend.run(...)`` so that the
fidelity=full transcript store/read gates (which require ``graph is not None``)
actually fire.

The earlier unit tests in test_backend_full_continuity.py passed ``graph``
straight into ``backend.run`` — they proved the mechanism but never the
handler→backend wiring.  A live DTU run revealed the gap: seeds wrote their
codewords but recall came back empty, because ``CodergenHandler.execute``
called ``backend.run(node, prompt, context)`` with only 3 args, leaving
``graph=None`` and silently skipping the transcript path.

Design: docs/designs/fidelity-full-session-continuity.md
Spec coverage: §5.4 (full fidelity), §4.5 (backend interface).
"""

import logging

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler


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
        self.calls: list[dict] = []
        self.session = _MockSession()
        # Include 'attractor-anthropic' with a non-pipeline session.orchestrator
        # so the identity recursion guard in _run_with_spawn does not fire.
        self.config: dict = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"output": self._output, "session_id": self._session_id}


def _make_full_node(node_id: str, prompt: str, thread_id: str = "main") -> Node:
    return Node(
        id=node_id,
        prompt=prompt,
        attrs={"llm_provider": "anthropic", "fidelity": "full", "thread_id": thread_id},
    )


def _make_full_graph(*node_ids: str, thread_id: str = "main") -> Graph:
    """Build a minimal linear graph where each listed node is fidelity=full."""
    nodes: dict[str, Node] = {"start": Node(id="start", shape="Mdiamond")}
    for nid in node_ids:
        nodes[nid] = _make_full_node(
            nid, prompt=f"prompt for {nid}", thread_id=thread_id
        )
    nodes["exit"] = Node(id="exit", shape="Msquare")

    edges: list[Edge] = [Edge(from_node="start", to_node=node_ids[0])]
    for i in range(len(node_ids) - 1):
        edges.append(Edge(from_node=node_ids[i], to_node=node_ids[i + 1]))
    edges.append(Edge(from_node=node_ids[-1], to_node="exit"))

    return Graph(name="test", nodes=nodes, edges=edges, graph_attrs={})


def _make_context() -> PipelineContext:
    ctx = PipelineContext()
    ctx.set("graph.goal", "Test handler continuity wiring")
    return ctx


# ---------------------------------------------------------------------------
# Test: REAL path — seed node then recall node via CodergenHandler.execute
#
# This is the test that would have caught the dead wiring. RED on the branch
# HEAD before the forwarding fix; GREEN after.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_execute_carries_full_continuity_across_nodes(tmp_path):
    """Seed → recall through CodergenHandler.execute carries history.

    Drives the handler (not backend.run directly).  The recall node's spawn
    must receive the seed node's (instruction, output) exchange via
    parent_messages — proving graph flows handler → backend so the transcript
    store/read gates fire on the production path.
    """
    coordinator = _SpawnCapture(output="The codeword is BANANA", session_id="seed-sess")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    handler = CodergenHandler(backend=backend)

    graph = _make_full_graph("seed", "recall", thread_id="codeword-thread")
    context = _make_context()
    logs_root = str(tmp_path / "logs")

    # Seed node — establishes context (the codeword)
    await handler.execute(graph.nodes["seed"], context, graph, logs_root)

    # Recall node — must see the seed's exchange
    coordinator._output = "Recalling: BANANA"
    coordinator._session_id = "recall-sess"
    await handler.execute(graph.nodes["recall"], context, graph, logs_root)

    assert len(coordinator.calls) == 2, "Expected exactly 2 spawn calls"

    seed_call = coordinator.calls[0]
    recall_call = coordinator.calls[1]

    # Seed: first node on the thread → no prior history
    assert not seed_call.get("parent_messages"), (
        "Seed node is first on its thread — must have no parent_messages"
    )
    assert "sub_session_id" not in seed_call

    # Recall: MUST carry the seed's exchange via parent_messages.
    # Before the wiring fix, graph=None silently skipped the store, so this
    # list would be empty/absent — the exact DTU failure (recall came back empty).
    pm = recall_call.get("parent_messages")
    assert pm, (
        "WIRING BUG: recall node received no parent_messages. "
        "CodergenHandler.execute must forward graph to backend.run so the "
        "fidelity=full transcript store/read gates fire. This is the dead-code "
        "bug a live DTU run exposed (seeds wrote codewords, recall came back empty)."
    )
    assert len(pm) == 2, (
        f"Expected one prior exchange (2 messages), got {len(pm)}: {pm}"
    )
    assert pm[0]["role"] == "user"
    assert pm[0]["content"] == "prompt for seed"
    assert pm[1]["role"] == "assistant"
    assert pm[1]["content"] == "The codeword is BANANA"

    # Recall: continuity via parent_messages, never sub_session_id
    assert "sub_session_id" not in recall_call


# ---------------------------------------------------------------------------
# Test: handler forwards graph (focused unit test on the wiring contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_execute_forwards_graph_to_backend(tmp_path):
    """CodergenHandler.execute passes the graph object through to backend.run.

    A focused contract test: the backend records the graph it received, and we
    assert it is the SAME graph object the handler was given (not None).
    """
    received: dict = {}

    class _RecordingBackend:
        async def run(
            self,
            node,
            prompt,
            context,
            incoming_edge=None,
            graph=None,
        ):
            received["graph"] = graph
            received["incoming_edge"] = incoming_edge
            return "ok"

    handler = CodergenHandler(backend=_RecordingBackend())
    graph = _make_full_graph("only", thread_id="t")
    context = _make_context()
    logs_root = str(tmp_path / "logs")

    await handler.execute(graph.nodes["only"], context, graph, logs_root)

    assert received.get("graph") is graph, (
        "CodergenHandler.execute must forward the graph object it received "
        "into backend.run(...). Got: " + repr(received.get("graph"))
    )


# ---------------------------------------------------------------------------
# Test: loud guard — full node reaching backend.run without a graph WARNs
# (it does NOT silently skip continuity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_without_graph_warns_not_silent(caplog):
    """A fidelity=full node with no graph emits a loud WARNING.

    This closes the silent-continuity-loss class (CR-1) at the exact spot the
    existing assert did not cover: a caller that drops ``graph`` for a full
    node can no longer silently lose continuity — it logs a visible warning.
    """
    coordinator = _SpawnCapture(output="done", session_id="s1")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    # fidelity=full node attr, but call backend.run with NO graph (graph=None)
    node = _make_full_node("orphan", prompt="do work", thread_id="t")
    context = _make_context()

    with caplog.at_level(logging.WARNING):
        await backend.run(node, "do work", context)  # no graph, no incoming_edge

    warned = any(
        "full" in rec.message.lower() and "graph" in rec.message.lower()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    )
    assert warned, (
        "A fidelity=full node reaching backend.run without a graph must emit a "
        "loud WARNING (continuity requested but cannot be honored), not silently "
        "skip the transcript path. Warnings seen: "
        + repr([r.message for r in caplog.records])
    )


@pytest.mark.asyncio
async def test_non_full_without_graph_does_not_warn(caplog):
    """A non-full node with no graph must NOT trigger the continuity warning.

    The guard must be targeted: only fire when full continuity is requested
    but cannot be honored — never on compact/truncate/summary or thread-less
    nodes that legitimately run without a graph.
    """
    coordinator = _SpawnCapture(output="done", session_id="s1")
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    # No fidelity attr → defaults to compact when graph is absent
    node = Node(id="plain", prompt="do work", attrs={"llm_provider": "anthropic"})
    context = _make_context()

    with caplog.at_level(logging.WARNING):
        await backend.run(node, "do work", context)

    spurious = any(
        "continuity" in rec.message.lower() or "thread" in rec.message.lower()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    )
    assert not spurious, (
        "Non-full nodes must not trigger the full-continuity warning. "
        "Warnings seen: " + repr([r.message for r in caplog.records])
    )
