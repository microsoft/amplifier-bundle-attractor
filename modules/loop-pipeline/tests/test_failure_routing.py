"""Tests for per-node failure routing (1b2).

When no edge matches after node execution, the engine should check
retry_target and fallback_retry_target on the node and graph before
returning FAIL.

Fallback chain: node.retry_target → node.fallback_retry_target →
                graph.retry_target → graph.fallback_retry_target → terminate.

Spec coverage: EXEC-015–018, Section 3.3.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class CountingBackend:
    """Backend that tracks call count per node and returns configurable outcomes."""

    def __init__(self, outcomes: dict[str, list[Outcome | str]] | None = None):
        self._outcomes = outcomes or {}
        self._call_counts: dict[str, int] = {}

    async def run(self, node, prompt, context, incoming_edge=None, graph=None) -> str | Outcome:
        count = self._call_counts.get(node.id, 0)
        self._call_counts[node.id] = count + 1
        seq = self._outcomes.get(node.id, ["done"])
        if count < len(seq):
            return seq[count]
        return seq[-1]  # repeat last

    def call_count(self, node_id: str) -> int:
        return self._call_counts.get(node_id, 0)


def _make_engine(
    graph: Graph,
    backend: object | None = None,
    logs_root: str = "/tmp/test-failure-routing",
    hooks: object | None = None,
) -> PipelineEngine:
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


class TestNodeRetryTarget:
    """When no edge matches, engine checks node.retry_target first."""

    @pytest.mark.asyncio
    async def test_node_retry_target_redirects_on_no_edge(self, tmp_path):
        """Node's retry_target is used when no edge matches."""
        # dead_end has retry_target pointing to recovery node
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(
                    id="dead_end",
                    prompt="work",
                    attrs={"retry_target": "recovery"},
                ),
                "recovery": Node(id="recovery", prompt="recover"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
                # dead_end has NO outgoing edges — triggers failure routing
                Edge(from_node="recovery", to_node="exit"),
            ],
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()
        # Should have routed to recovery and then to exit
        assert outcome.status != StageStatus.FAIL or "No matching edge" not in (
            outcome.failure_reason or ""
        )
        assert backend.call_count("recovery") >= 1

    @pytest.mark.asyncio
    async def test_node_fallback_retry_target_used_when_primary_missing(self, tmp_path):
        """Node's fallback_retry_target is used when retry_target is absent."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(
                    id="dead_end",
                    prompt="work",
                    attrs={"fallback_retry_target": "fallback"},
                ),
                "fallback": Node(id="fallback", prompt="fallback work"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
                Edge(from_node="fallback", to_node="exit"),
            ],
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        await engine.run()
        assert backend.call_count("fallback") >= 1


class TestGraphRetryTarget:
    """Graph-level retry targets are checked after node-level."""

    @pytest.mark.asyncio
    async def test_graph_retry_target_used_when_node_has_none(self, tmp_path):
        """Graph's retry_target is used when node has no retry targets."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(id="dead_end", prompt="work"),
                "graph_recovery": Node(id="graph_recovery", prompt="recover"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
                Edge(from_node="graph_recovery", to_node="exit"),
            ],
            graph_attrs={"retry_target": "graph_recovery"},
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        await engine.run()
        assert backend.call_count("graph_recovery") >= 1

    @pytest.mark.asyncio
    async def test_graph_fallback_retry_target_last_resort(self, tmp_path):
        """Graph's fallback_retry_target is the last resort before terminate."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(id="dead_end", prompt="work"),
                "last_resort": Node(id="last_resort", prompt="last chance"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
                Edge(from_node="last_resort", to_node="exit"),
            ],
            graph_attrs={"fallback_retry_target": "last_resort"},
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        await engine.run()
        assert backend.call_count("last_resort") >= 1


class TestFailureRoutingTerminate:
    """When no retry target exists anywhere, engine terminates with FAIL."""

    @pytest.mark.asyncio
    async def test_no_retry_target_anywhere_returns_fail(self, tmp_path):
        """No retry targets at any level → FAIL with 'No matching edge'."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "dead_end": Node(id="dead_end", prompt="work"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="dead_end"),
            ],
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()
        assert outcome.status == StageStatus.FAIL
        assert "No matching edge" in (outcome.failure_reason or "")


class TestFailureRoutingBounded:
    """Failure routing retries must be bounded to prevent infinite loops."""

    @pytest.mark.asyncio
    async def test_failure_routing_bounded_by_max_retries(self, tmp_path):
        """Failure routing retries are bounded (no infinite loop).

        If the retry target also has no matching edges and loops back,
        the engine must eventually terminate.
        """
        # Both nodes have no outgoing edges, retry_target points to each other
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "a": Node(id="a", prompt="work", attrs={"retry_target": "b"}),
                "b": Node(id="b", prompt="work", attrs={"retry_target": "a"}),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="a"),
                # Neither a nor b have outgoing edges → infinite retry loop
            ],
        )
        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()
        # Must terminate (not hang) and return FAIL
        assert outcome.status == StageStatus.FAIL
        # Total calls should be bounded by _MAX_GOAL_GATE_RETRIES (shared limit)
        total_calls = backend.call_count("a") + backend.call_count("b")
        assert total_calls <= PipelineEngine._MAX_GOAL_GATE_RETRIES + 5
