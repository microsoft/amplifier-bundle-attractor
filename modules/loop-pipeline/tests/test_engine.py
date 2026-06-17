"""Tests for the pipeline execution engine.

Spec coverage: EXEC-001–018, Section 3.2.
"""

import asyncio
import time

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class MockBackend:
    """Backend that returns a fixed string for every call."""

    def __init__(self, return_value: str = "done"):
        self._return_value = return_value
        self.calls: list[str] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        self.calls.append(node.id)
        return self._return_value


class SequenceBackend:
    """Backend that returns different outcomes per node id."""

    def __init__(self, outcomes: dict[str, str | Outcome]):
        self._outcomes = outcomes
        self.calls: list[str] = []

    async def run(
        self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
    ) -> str | Outcome:
        self.calls.append(node.id)
        return self._outcomes.get(node.id, "ok")


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-pipeline",
) -> PipelineEngine:
    """Parse DOT, validate, and build an engine."""
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


@pytest.mark.asyncio
async def test_simple_linear_pipeline(tmp_path):
    """start -> plan -> implement -> exit completes successfully."""
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            plan [prompt="Plan the work"]
            implement [prompt="Build it"]
            exit [shape=Msquare]
            start -> plan -> implement -> exit
        }
        """,
        backend=MockBackend("done"),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)


@pytest.mark.asyncio
async def test_engine_visits_all_nodes(tmp_path):
    """Engine visits start, plan, implement, exit in order."""
    backend = MockBackend("done")
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            plan [prompt="Plan"]
            implement [prompt="Build"]
            exit [shape=Msquare]
            start -> plan -> implement -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    await engine.run()
    # Backend is only called for codergen nodes (plan, implement)
    assert backend.calls == ["plan", "implement"]


@pytest.mark.asyncio
async def test_conditional_branching(tmp_path):
    """Condition-based routing follows matching edges."""
    backend = SequenceBackend(
        outcomes={
            "check": Outcome(status=StageStatus.SUCCESS),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            check [shape=parallelogram, tool_command="echo routing"]
            pass_path [prompt="Tests pass"]
            fail_path [prompt="Tests fail"]
            exit [shape=Msquare]
            start -> check
            check -> pass_path [condition="outcome=success"]
            check -> fail_path [condition="outcome=fail"]
            pass_path -> exit
            fail_path -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.SUCCESS
    # Should have taken the pass_path since check returned SUCCESS
    assert "pass_path" in backend.calls
    assert "fail_path" not in backend.calls


@pytest.mark.asyncio
async def test_context_updates_propagate(tmp_path):
    """Context updates from outcomes are visible to subsequent nodes."""

    class ContextCheckBackend:
        def __init__(self):
            self.seen_values: dict[str, str | None] = {}

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id == "step1":
                return Outcome(
                    status=StageStatus.SUCCESS,
                    context_updates={"my_key": "my_value"},
                )
            if node.id == "step2":
                self.seen_values["my_key"] = context.get("my_key")
                return "done"
            return "ok"

    backend = ContextCheckBackend()
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            step1 [prompt="Step 1"]
            step2 [prompt="Step 2"]
            exit [shape=Msquare]
            start -> step1 -> step2 -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    await engine.run()
    assert backend.seen_values.get("my_key") == "my_value"


@pytest.mark.asyncio
async def test_goal_set_in_context(tmp_path):
    """Graph goal is mirrored into context."""
    engine = _make_engine(
        dot_source="""
        digraph {
            goal = "build auth"
            start [shape=Mdiamond]
            exit [shape=Msquare]
            start -> exit
        }
        """,
        backend=MockBackend("ok"),
        logs_root=str(tmp_path),
    )
    await engine.run()
    assert engine.context.get("graph.goal") == "build auth"


@pytest.mark.asyncio
async def test_no_matching_edge_returns_fail(tmp_path):
    """No outgoing edges from a non-terminal node returns fail."""
    # Build a graph manually where a codergen node has no outgoing edges.
    # (Can't use the parser helper because validation would reject it,
    # so we build the engine directly.)
    from amplifier_module_loop_pipeline.graph import Edge

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "dead_end": Node(id="dead_end", prompt="work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="dead_end"),
            # dead_end has NO outgoing edges
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("ok")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.FAIL
    assert "No matching edge" in (outcome.failure_reason or "")


@pytest.mark.asyncio
async def test_goal_gate_unsatisfied_returns_fail(tmp_path):
    """Goal gate with non-success outcome fails the pipeline at exit."""

    class FailingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id == "critical":
                return Outcome(status=StageStatus.FAIL, failure_reason="broken")
            return "ok"

    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true]
            exit [shape=Msquare]
            start -> critical
            critical -> exit [condition="outcome=fail"]
        }
        """,
        backend=FailingBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_deterministic_execution(tmp_path):
    """Same graph + same context = same path."""
    backend1 = MockBackend("done")
    backend2 = MockBackend("done")

    dot_source = """
    digraph {
        start [shape=Mdiamond]
        a [prompt="A"]
        b [prompt="B"]
        exit [shape=Msquare]
        start -> a -> b -> exit
    }
    """
    engine1 = _make_engine(dot_source, backend=backend1, logs_root=str(tmp_path / "r1"))
    engine2 = _make_engine(dot_source, backend=backend2, logs_root=str(tmp_path / "r2"))

    await engine1.run()
    await engine2.run()
    assert backend1.calls == backend2.calls


@pytest.mark.asyncio
async def test_start_node_fallback_to_id_start(tmp_path):
    """Engine falls back to id='start' when no Mdiamond node exists (L-21)."""
    # Build graph manually — no Mdiamond, but a node with id="start"
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="box", prompt="Begin"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("ok")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    # Should succeed — engine found the start node via id fallback
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "start" in engine.completed_nodes


@pytest.mark.asyncio
async def test_start_node_fallback_to_id_Start(tmp_path):
    """Engine falls back to id='Start' (capitalized) when no Mdiamond (L-21)."""
    graph = Graph(
        name="test",
        nodes={
            "Start": Node(id="Start", shape="box", prompt="Begin"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="Start", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("ok")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)


@pytest.mark.asyncio
async def test_start_node_shape_takes_priority(tmp_path):
    """Mdiamond shape is preferred over id='start' fallback (L-21)."""
    graph = Graph(
        name="test",
        nodes={
            "begin": Node(id="begin", shape="Mdiamond"),
            "start": Node(id="start", shape="box", prompt="Not the start"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="begin", to_node="exit"),
            Edge(from_node="start", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("ok")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # "start" (the box node) should NOT have been visited as the entry point
    assert "start" not in engine.completed_nodes


@pytest.mark.asyncio
async def test_auto_status_preserves_explicit_fail(tmp_path):
    """auto_status=true must NOT mask an explicit FAIL — fail-loud (spec §2.6/Appendix C).

    The handler explicitly returns FAIL; auto_status may only synthesize SUCCESS
    when the handler writes *no* status (SKIPPED sentinel), not when it returns a
    real failure.
    """

    class FailingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id == "auto_node":
                return Outcome(status=StageStatus.FAIL, failure_reason="oops")
            return "ok"

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "auto_node": Node(
                id="auto_node",
                shape="box",
                prompt="work",
                auto_status=True,
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="auto_node"),
            Edge(from_node="auto_node", to_node="exit", condition="outcome=fail"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    await engine.run()
    # auto_status must NOT override an explicit FAIL — the failure must be preserved
    assert engine.node_outcomes["auto_node"].status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_auto_status_false_preserves_fail(tmp_path):
    """Without auto_status, FAIL outcome is preserved (L-9)."""

    class FailingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id == "fail_node":
                return Outcome(status=StageStatus.FAIL, failure_reason="oops")
            return "ok"

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "fail_node": Node(id="fail_node", shape="box", prompt="work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="fail_node"),
            Edge(from_node="fail_node", to_node="exit", condition="outcome=fail"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    await engine.run()
    # Without auto_status, FAIL is preserved
    assert engine.node_outcomes["fail_node"].status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_engine_records_node_outcomes(tmp_path):
    """Engine tracks outcomes for every visited node."""
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            step [prompt="Do work"]
            exit [shape=Msquare]
            start -> step -> exit
        }
        """,
        backend=MockBackend("done"),
        logs_root=str(tmp_path),
    )
    await engine.run()
    assert "start" in engine.node_outcomes
    assert "step" in engine.node_outcomes
    assert engine.node_outcomes["step"].status == StageStatus.SUCCESS


# --- Alternative start/exit node conventions ---


@pytest.mark.asyncio
async def test_engine_finds_start_by_node_type_attr(tmp_path):
    """Engine finds start node via node_type='start' attribute."""
    graph = Graph(
        name="test",
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare", label="Exit"),
        },
        edges=[
            Edge(from_node="Start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("done")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    start = engine._find_start_node()
    assert start.id == "Start"


@pytest.mark.asyncio
async def test_engine_runs_alternative_start_exit(tmp_path):
    """Engine executes pipeline with circle/doublecircle + node_type conventions."""
    graph = Graph(
        name="test",
        nodes={
            "Start": Node(
                id="Start",
                shape="circle",
                label="Start",
                attrs={"node_type": "start"},
            ),
            "work": Node(id="work", shape="box", prompt="Do work"),
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
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("done")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)


@pytest.mark.asyncio
async def test_engine_mdiamond_takes_priority_over_node_type(tmp_path):
    """shape=Mdiamond has higher priority than node_type='start'."""
    graph = Graph(
        name="test",
        nodes={
            "real_start": Node(id="real_start", shape="Mdiamond", label="RealStart"),
            "alt_start": Node(
                id="alt_start",
                shape="circle",
                attrs={"node_type": "start"},
            ),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare", label="Exit"),
        },
        edges=[
            Edge(from_node="real_start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=MockBackend("done")))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    start = engine._find_start_node()
    assert start.id == "real_start", "Mdiamond should take priority over node_type"


# --- loop_restart edge handling ---


class LoopOnceBackend:
    """Backend that triggers loop_restart on first call to 'work', then succeeds."""

    def __init__(self):
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if node.id == "work" and self.calls.count("work") == 1:
            return Outcome(status=StageStatus.SUCCESS, preferred_label="loop")
        return Outcome(status=StageStatus.SUCCESS)


def _make_loop_restart_graph() -> Graph:
    """Build a graph with a loop_restart edge: start -> work -[loop]-> work -> exit."""
    return Graph(
        name="test-loop",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(
                from_node="work",
                to_node="work",
                condition="preferred_label=loop",
                loop_restart=True,
            ),
            Edge(from_node="work", to_node="exit"),
        ],
    )


@pytest.mark.asyncio
async def test_loop_restart_re_executes_target_node(tmp_path):
    """loop_restart=true on an edge causes the engine to re-execute the target node."""
    backend = LoopOnceBackend()
    graph = _make_loop_restart_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # "work" was executed twice: once before loop_restart, once after
    assert backend.calls.count("work") == 2
    # completed_nodes was cleared by loop_restart; "start" from the
    # first iteration should no longer be present
    assert "start" not in engine.completed_nodes


@pytest.mark.asyncio
async def test_loop_restart_resets_retry_counters(tmp_path):
    """loop_restart clears node_outcomes (retry tracking) for clean re-execution."""
    backend = LoopOnceBackend()
    graph = _make_loop_restart_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    await engine.run()

    # node_outcomes was cleared by loop_restart; only last-iteration outcomes remain.
    # "start" from the first iteration should not be in node_outcomes.
    assert "start" not in engine.node_outcomes
    # "work" from the second iteration should still be present
    assert "work" in engine.node_outcomes


@pytest.mark.asyncio
async def test_loop_restart_increments_iteration_counter(tmp_path):
    """loop_restart increments iteration_count and creates a fresh log directory."""
    backend = LoopOnceBackend()
    graph = _make_loop_restart_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    await engine.run()

    # Iteration counter should have been incremented once
    assert engine.iteration_count == 1
    # A fresh log subdirectory should have been created
    assert (tmp_path / "iteration_1").is_dir()


@pytest.mark.asyncio
async def test_normal_edge_without_loop_restart(tmp_path):
    """Normal edges (without loop_restart) don't reset state or increment counter."""
    backend = MockBackend("done")
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # No loop restart occurred
    assert engine.iteration_count == 0
    # "work" was only executed once
    assert backend.calls.count("work") == 1
    # All nodes are still in completed_nodes (not cleared)
    assert "start" in engine.completed_nodes
    assert "work" in engine.completed_nodes


# --- Multi-edge parallel fan-out ---


@pytest.mark.asyncio
async def test_multi_edge_fan_out_executes_all_targets(tmp_path):
    """Graph with node having 3 edges with same condition executes all three."""
    executed_nodes: list[str] = []

    class TrackingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            executed_nodes.append(node.id)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-fanout",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "branch_a": Node(id="branch_a", shape="box", prompt="A"),
            "branch_b": Node(id="branch_b", shape="box", prompt="B"),
            "branch_c": Node(id="branch_c", shape="box", prompt="C"),
            "consolidate": Node(id="consolidate", shape="box", prompt="Merge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            # Multi-edge fan-out: same condition, three targets
            Edge(from_node="check", to_node="branch_a", condition="outcome=success"),
            Edge(from_node="check", to_node="branch_b", condition="outcome=success"),
            Edge(from_node="check", to_node="branch_c", condition="outcome=success"),
            # All branches converge on consolidate (fan-in)
            Edge(from_node="branch_a", to_node="consolidate"),
            Edge(from_node="branch_b", to_node="consolidate"),
            Edge(from_node="branch_c", to_node="consolidate"),
            Edge(from_node="consolidate", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=TrackingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # All three branches should have been executed
    assert "branch_a" in executed_nodes
    assert "branch_b" in executed_nodes
    assert "branch_c" in executed_nodes


@pytest.mark.asyncio
async def test_multi_edge_fan_out_detects_fan_in(tmp_path):
    """Fan-in node E executes after parallel branches B, C, D complete."""
    executed_nodes: list[str] = []

    class TrackingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            executed_nodes.append(node.id)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-fanin",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "A": Node(id="A", shape="box", prompt="A"),
            "B": Node(id="B", shape="box", prompt="B"),
            "C": Node(id="C", shape="box", prompt="C"),
            "D": Node(id="D", shape="box", prompt="D"),
            "E": Node(id="E", shape="box", prompt="E"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="A"),
            # A fans out to B, C, D (same condition)
            Edge(from_node="A", to_node="B", condition="outcome=success"),
            Edge(from_node="A", to_node="C", condition="outcome=success"),
            Edge(from_node="A", to_node="D", condition="outcome=success"),
            # B, C, D all converge on E (fan-in)
            Edge(from_node="B", to_node="E"),
            Edge(from_node="C", to_node="E"),
            Edge(from_node="D", to_node="E"),
            Edge(from_node="E", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=TrackingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # E (fan-in) should execute after B, C, D
    assert "E" in executed_nodes
    # B, C, D should all appear before E
    e_index = executed_nodes.index("E")
    assert "B" in executed_nodes[:e_index]
    assert "C" in executed_nodes[:e_index]
    assert "D" in executed_nodes[:e_index]


@pytest.mark.asyncio
async def test_multi_edge_single_match_still_works(tmp_path):
    """When only one edge matches a condition, single-edge path is used."""
    backend = SequenceBackend(
        outcomes={
            "check": Outcome(status=StageStatus.SUCCESS),
        }
    )

    graph = Graph(
        name="test-single",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "yes_path": Node(id="yes_path", shape="box", prompt="Yes"),
            "no_path": Node(id="no_path", shape="box", prompt="No"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            Edge(from_node="check", to_node="yes_path", condition="outcome=success"),
            Edge(from_node="check", to_node="no_path", condition="outcome=fail"),
            Edge(from_node="yes_path", to_node="exit"),
            Edge(from_node="no_path", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # Only yes_path should have been executed (single match)
    assert "yes_path" in backend.calls
    assert "no_path" not in backend.calls


@pytest.mark.asyncio
async def test_multi_edge_parallel_context_isolation(tmp_path):
    """Each parallel branch gets its own context copy — mutations don't leak."""
    seen_values: dict[str, str | None] = {}

    class ContextMutatingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id == "branch_a":
                context.set("branch_key", "from_a")
                return Outcome(status=StageStatus.SUCCESS)
            if node.id == "branch_b":
                # branch_b should NOT see branch_a's mutation
                seen_values["branch_b_saw"] = context.get("branch_key")
                return Outcome(status=StageStatus.SUCCESS)
            if node.id == "merge":
                seen_values["merge_saw"] = context.get("branch_key")
                return Outcome(status=StageStatus.SUCCESS)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-isolation",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "branch_a": Node(id="branch_a", shape="box", prompt="A"),
            "branch_b": Node(id="branch_b", shape="box", prompt="B"),
            "merge": Node(id="merge", shape="box", prompt="Merge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            Edge(from_node="check", to_node="branch_a", condition="outcome=success"),
            Edge(from_node="check", to_node="branch_b", condition="outcome=success"),
            Edge(from_node="branch_a", to_node="merge"),
            Edge(from_node="branch_b", to_node="merge"),
            Edge(from_node="merge", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=ContextMutatingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # branch_b should NOT have seen branch_a's context mutation
    assert seen_values.get("branch_b_saw") is None


# --- Parallel fan-out: concurrency timing and clone-per-branch isolation ---


@pytest.mark.asyncio
async def test_parallel_fan_out_branches_run_concurrently(tmp_path):
    """Three parallel branches each sleeping 0.2s finish in < 0.5s wall-clock."""

    class SlowCloningBackend:
        def clone(self):
            return SlowCloningBackend()

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            if node.id.startswith("b"):
                await asyncio.sleep(0.2)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-timing",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "src": Node(id="src", shape="box", prompt="Source"),
            "b1": Node(id="b1", shape="box", prompt="B1"),
            "b2": Node(id="b2", shape="box", prompt="B2"),
            "b3": Node(id="b3", shape="box", prompt="B3"),
            "converge": Node(id="converge", shape="box", prompt="Converge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="src"),
            Edge(from_node="src", to_node="b1", condition="outcome=success"),
            Edge(from_node="src", to_node="b2", condition="outcome=success"),
            Edge(from_node="src", to_node="b3", condition="outcome=success"),
            Edge(from_node="b1", to_node="converge"),
            Edge(from_node="b2", to_node="converge"),
            Edge(from_node="b3", to_node="converge"),
            Edge(from_node="converge", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=SlowCloningBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    t0 = time.monotonic()
    outcome = await engine.run()
    elapsed = time.monotonic() - t0

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # 3 × 0.2s concurrent should be ~0.2s, NOT 0.6s sequential
    assert elapsed < 0.5, f"Parallel branches took {elapsed:.2f}s (expected < 0.5s)"


@pytest.mark.asyncio
async def test_parallel_fan_out_clones_registry_per_branch(tmp_path):
    """Each parallel branch gets its own cloned handler registry."""
    from unittest.mock import patch

    class CloningBackend:
        def clone(self):
            return CloningBackend()

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-clone-isolation",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "src": Node(id="src", shape="box", prompt="Source"),
            "b1": Node(id="b1", shape="box", prompt="B1"),
            "b2": Node(id="b2", shape="box", prompt="B2"),
            "b3": Node(id="b3", shape="box", prompt="B3"),
            "converge": Node(id="converge", shape="box", prompt="Converge"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="src"),
            Edge(from_node="src", to_node="b1", condition="outcome=success"),
            Edge(from_node="src", to_node="b2", condition="outcome=success"),
            Edge(from_node="src", to_node="b3", condition="outcome=success"),
            Edge(from_node="b1", to_node="converge"),
            Edge(from_node="b2", to_node="converge"),
            Edge(from_node="b3", to_node="converge"),
            Edge(from_node="converge", to_node="exit"),
        ],
    )

    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=CloningBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    with patch.object(
        registry, "clone_for_branch", wraps=registry.clone_for_branch
    ) as mock_clone:
        outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # clone_for_branch should be called once per parallel branch (3 branches)
    assert mock_clone.call_count == 3, (
        f"Expected 3 clone_for_branch calls, got {mock_clone.call_count}"
    )


# --- main loop max_steps safety bound ---


@pytest.mark.asyncio
async def test_main_loop_safety_bound_terminates_infinite_cycle(tmp_path):
    """Main run() loop terminates with FAIL when the step limit is exceeded.

    Regression test: before the fix, a condition-routing bug (always-false
    conditions) could cause the engine to cycle indefinitely.  The safety
    bound must catch this and return a FAIL outcome rather than hang.

    We patch _MAX_GOAL_GATE_RETRIES to 2 so max_steps = nodes × 2 = 6,
    making the test complete quickly while still exercising the bound.
    """
    from unittest.mock import patch

    # A graph where the only exit edge has a condition that is never satisfied.
    # The unconditional edge back to 'work' is always preferred, so the engine
    # cycles: start → work → work → work → ... forever without the bound.
    dot_source = """
    digraph {
        start  [shape=Mdiamond]
        work   [shape=parallelogram, tool_command="echo always_loops"]
        exit   [shape=Msquare]
        start -> work
        work  -> exit [condition="outcome=never_true"]
        work  -> work
    }
    """
    engine = _make_engine(
        dot_source=dot_source, backend=MockBackend(), logs_root=str(tmp_path)
    )

    # Patch the class constant so max_steps = 3 nodes × 2 = 6 steps
    with patch.object(type(engine), "_MAX_GOAL_GATE_RETRIES", new=2):
        outcome = await engine.run()

    assert outcome.status == StageStatus.FAIL, (
        f"Expected FAIL when step bound exceeded, got {outcome.status!r}"
    )
    assert "safety bound" in (outcome.failure_reason or ""), (
        f"Expected 'safety bound' in failure_reason, got {outcome.failure_reason!r}"
    )
