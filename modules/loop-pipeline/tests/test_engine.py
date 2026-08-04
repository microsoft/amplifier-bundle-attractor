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

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        self.calls.append(node.id)
        return self._return_value


class SequenceBackend:
    """Backend that returns different outcomes per node id."""

    def __init__(self, outcomes: dict[str, str | Outcome]):
        self._outcomes = outcomes
        self.calls: list[str] = []

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
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
async def test_auto_status_promotion_preserves_attempt_count(tmp_path):
    """attempt_count from the retry ladder must survive auto_status promotion.

    (S2 lossy-reconstruction regression guard, docs/designs/RECURRING-BUG-CLASSES.md.)

    A node with max_retries='2' (max_attempts=3) whose handler returns RETRY
    on the first two attempts and SKIPPED on the third has attempt_count=3
    set by execute_with_retry's SKIPPED path. When auto_status='true' then
    promotes that SKIPPED outcome to SUCCESS, the promoted outcome -- and the
    emitted pipeline:node_complete event -- must still report attempt=3, not
    the attempt_count=None fallback of 1.
    """

    class _RecordingHooks:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        async def emit(self, event_name: str, data: dict) -> None:
            self.events.append((event_name, data))

    class RetryThenSkipHandler:
        """Returns RETRY on the first two calls, SKIPPED on the third."""

        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, node, context, graph, logs_root, *, engine=None):
            self.calls += 1
            if self.calls < 3:
                return Outcome(status=StageStatus.RETRY)
            return Outcome(status=StageStatus.SKIPPED)

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "auto_node": Node(
                id="auto_node",
                shape="box",
                prompt="work",
                attrs={"auto_status": "true", "max_retries": "2"},
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="auto_node"),
            Edge(from_node="auto_node", to_node="exit"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext())
    handler = RetryThenSkipHandler()
    registry.register("codergen", handler)
    hooks = _RecordingHooks()
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert handler.calls == 3, f"Expected 3 handler calls, got {handler.calls}"
    assert engine.node_outcomes["auto_node"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["auto_node"].attempt_count == 3, (
        f"Expected attempt_count=3 to survive auto_status promotion, "
        f"got {engine.node_outcomes['auto_node'].attempt_count!r} "
        f"(S2 lossy reconstruction regression)"
    )

    complete_events = [
        data for name, data in hooks.events if name == "pipeline:node_complete"
    ]
    auto_node_events = [e for e in complete_events if e["node_id"] == "auto_node"]
    assert len(auto_node_events) == 1, (
        f"Expected exactly one pipeline:node_complete for auto_node, "
        f"got {len(auto_node_events)}"
    )
    assert auto_node_events[0]["attempt"] == 3, (
        f"Expected pipeline:node_complete attempt=3 after auto_status "
        f"promotion, got {auto_node_events[0]['attempt']!r} "
        f"(S2 lossy reconstruction regression)"
    )


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


class StaleLabelBackend:
    """Cycle 1 sets preferred_label='refine' (triggers loop_restart); every
    subsequent call to 'work' returns an outcome with NO preferred_label at
    all -- simulating the known, separate model-reliability bug where an
    LLM's report_outcome call omits the label. Used to prove the engine
    does not let cycle 1's label leak into cycle 2's routing decision.
    """

    def __init__(self):
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if node.id == "work" and self.calls.count("work") == 1:
            return Outcome(status=StageStatus.SUCCESS, preferred_label="refine")
        return Outcome(status=StageStatus.SUCCESS)


def _make_stale_label_graph() -> Graph:
    """Mirrors synthesize.dot's assess/feedback shape: a converged edge and
    a refine (loop_restart) edge both gated on context.preferred_label,
    plus an unconditional 'else' fallback (the fail-safe direction).

    ``mark_converged``/``mark_pending`` are deterministic no-op tool nodes
    (not exit nodes) so the taken path is visible in ``completed_nodes`` --
    exit nodes (shape=Msquare) terminate the run loop at Step 1, before
    ``completed_nodes`` is ever appended to, so they cannot be used to
    observe which branch was actually taken.
    """
    return Graph(
        name="test-stale-label",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "mark_converged": Node(
                id="mark_converged",
                shape="parallelogram",
                attrs={"tool_command": "true"},
            ),
            "mark_pending": Node(
                id="mark_pending", shape="parallelogram", attrs={"tool_command": "true"}
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(
                from_node="work",
                to_node="work",
                label="refine",
                condition="context.preferred_label=refine",
                loop_restart=True,
            ),
            Edge(
                from_node="work",
                to_node="mark_converged",
                label="converged",
                condition="context.preferred_label=converged",
            ),
            # Unconditional fallback -- fail-safe direction (more work,
            # never premature "done"), matching synthesize.dot's else edge.
            Edge(from_node="work", to_node="mark_pending", label="else"),
            Edge(from_node="mark_converged", to_node="exit"),
            Edge(from_node="mark_pending", to_node="exit"),
        ],
    )


@pytest.mark.asyncio
async def test_loop_restart_clears_stale_preferred_label_within_source(tmp_path):
    """loop_restart clears context.preferred_label so a prior cycle's label
    cannot leak into a later cycle whose own outcome omits the label.

    Cycle 1: work returns preferred_label="refine" -> context.preferred_label
    is set to "refine" -> the work->work[condition="context.preferred_label=
    refine"] edge matches -> loop_restart fires.

    Cycle 2: work returns an outcome with NO preferred_label (the known
    model-reliability omission bug). Without the fix, context.preferred_label
    would still read "refine" from cycle 1, so the SAME loop_restart edge
    would incorrectly match again using stale state (an infinite stale-label
    loop) -- or, in the sibling scenario where cycle 1 set "converged"
    instead, a stale "converged" would falsely route straight to "done".
    With the fix, the context key is cleared at the loop_restart point, so
    cycle 2's unlabeled outcome falls through to the safe, unconditional
    "else" fallback instead.
    """
    backend = StaleLabelBackend()
    graph = _make_stale_label_graph()
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
    # "work" executed exactly twice: cycle 1 (refine) + cycle 2 (no label).
    # If the stale label leaked, cycle 2 would re-match "refine" and loop
    # forever instead of terminating after two calls.
    assert backend.calls.count("work") == 2
    # THE FIX: cycle 1's "refine" must not survive the loop_restart.
    assert engine.context.get("preferred_label") is None
    # Cycle 2 must fall through to the safe unconditional fallback, not the
    # stale-matched "refine" loop or a falsely-matched "converged" path.
    # mark_pending/mark_converged are tool nodes (not exit nodes), so they
    # ARE recorded in completed_nodes -- unlike the shared "exit" node,
    # which (being shape=Msquare) short-circuits the run loop at Step 1
    # before completed_nodes is ever appended to.
    assert "mark_pending" in engine.completed_nodes
    assert "mark_converged" not in engine.completed_nodes


class SourceAssessBackend:
    """Shared backend across two folder-node (sub-pipeline) invocations.

    Mirrors synthesize.dot's assess node: source A converges cleanly on its
    first assess call (preferred_label="converged"); source B's assess call
    reports NO preferred_label at all -- the known model-reliability
    omission bug. Used to prove a fresh child context for source B does not
    inherit source A's stale "converged" left in the parent context.
    """

    def __init__(self):
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if node.id != "assess":
            return Outcome(status=StageStatus.SUCCESS)
        if self.calls.count("assess") == 1:
            # Source A: converges cleanly.
            return Outcome(
                status=StageStatus.SUCCESS,
                preferred_label="converged",
                context_updates={"more_sources": "true"},
            )
        # Source B: assess omits preferred_label entirely this cycle.
        return Outcome(
            status=StageStatus.SUCCESS,
            context_updates={"more_sources": "false"},
        )


CHILD_SOURCE_DOT = """
digraph childsrc {
    start [shape=Mdiamond]
    assess [shape=box, prompt="Assess this source"]
    done [shape=Msquare]
    pending [shape=Msquare]
    start -> assess
    assess -> done    [condition="context.preferred_label=converged"]
    assess -> pending
}
"""


@pytest.mark.asyncio
async def test_folder_loop_restart_does_not_leak_preferred_label_across_sources(
    tmp_path,
):
    """Cross-source leak: a folder sub-pipeline's converged outcome must not
    survive the parent's loop_restart and pollute the NEXT sub-pipeline's
    cloned context.

    Mirrors ingest.dot + synthesize.dot: a parent drain loop runs a child
    DOT (synthesize.dot) per source via a shape=folder node, looping back
    to the SAME folder node via a loop_restart edge for the next source.

    Source A's child pipeline converges (preferred_label="converged"),
    which the parent engine writes into ITS OWN context (engine.py:647-648)
    when the folder node's outcome propagates up. Source B's child
    pipeline is then entered via PipelineHandler.execute()'s
    ``context.clone()`` (handlers/pipeline.py:163) of that SAME parent
    context.

    Without the fix, the parent's context.preferred_label would still read
    "converged" from source A at the moment of the clone, so source B's
    freshly-cloned child context would start already "converged" --
    meaning if source B's own assess call also omits preferred_label (the
    known model-reliability bug), its very first assess routes straight to
    "done" without ever having been judged -- a silent false success.

    With the fix, the parent's loop_restart (executed by the SAME engine.py
    code path, once for the parent's own restart edge) clears
    preferred_label before source B's folder-node clone happens, so source
    B's child context starts clean and correctly falls through to the safe
    "pending" fallback instead of a false "done".
    """
    child_dot_path = tmp_path / "child.dot"
    child_dot_path.write_text(CHILD_SOURCE_DOT)

    parent_dot = f"""
    digraph parentsrc {{
        start [shape=Mdiamond]
        run_synthesize [shape=folder, dot_file="{child_dot_path}", outputs="more_sources"]
        done [shape=Msquare]
        start -> run_synthesize
        run_synthesize -> run_synthesize [loop_restart="true", condition="context.more_sources=true"]
        run_synthesize -> done [condition="context.more_sources=false"]
    }}
    """
    graph = parse_dot(parent_dot)
    validate_or_raise(graph)
    graph.source_dir = str(tmp_path)

    backend = SourceAssessBackend()
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
    # assess was invoked exactly twice: source A, then source B.
    assert backend.calls.count("assess") == 2
    # THE FIX: source A's "converged" must not survive the parent's
    # loop_restart, so it cannot be present at source B's context.clone().
    assert engine.context.get("preferred_label") is None
    # The pipeline correctly reflects that source B never actually
    # converged (its own assess omitted the label) -- it drained via the
    # "false" branch, not a leaked-true one.
    assert engine.context.get("more_sources") == "false"


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


# --- Multi-edge selection: spec §3.3 single-edge selection (T0-4) ---
#
# After T0-4 spec-conformance restoration, the engine selects exactly ONE
# edge when multiple conditional edges match simultaneously.  The retired
# multi-match → parallel fan-out path (select_all_matching_edges →
# _execute_parallel_fan_out) is gone for non-component nodes.
#
# Tests below verify:
#   - Multi-match resolves to ONE edge (highest weight, lexical tiebreak)
#   - Single-match still works (unchanged)
#   - Explicit parallelism via shape=component is unaffected
# The old "executes_all_targets" and "detects_fan_in" tests that asserted
# fan-out behavior for non-component nodes are updated to assert single-edge
# selection instead.


@pytest.mark.asyncio
async def test_multi_edge_single_match_selects_highest_weight(tmp_path):
    """Spec §3.3: when multiple conditional edges match, select the highest-weight one.

    Three edges from 'check' all carry condition=outcome=success.  The engine
    must select exactly ONE — the edge with weight=3 (to branch_c).
    """
    executed_nodes: list[str] = []

    class TrackingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            executed_nodes.append(node.id)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-single-best-edge",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "branch_a": Node(id="branch_a", shape="box", prompt="A"),
            "branch_b": Node(id="branch_b", shape="box", prompt="B"),
            "branch_c": Node(id="branch_c", shape="box", prompt="C"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            # All three conditions match; weight=3 makes branch_c the winner.
            Edge(from_node="check", to_node="branch_a", condition="outcome=success", weight=1),
            Edge(from_node="check", to_node="branch_b", condition="outcome=success", weight=2),
            Edge(from_node="check", to_node="branch_c", condition="outcome=success", weight=3),
            Edge(from_node="branch_a", to_node="exit"),
            Edge(from_node="branch_b", to_node="exit"),
            Edge(from_node="branch_c", to_node="exit"),
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
    # Exactly ONE branch must have been executed — the highest-weight one.
    assert "branch_c" in executed_nodes, "branch_c (weight=3) must be selected"
    assert "branch_a" not in executed_nodes, "branch_a must NOT execute (weight=1)"
    assert "branch_b" not in executed_nodes, "branch_b must NOT execute (weight=2)"


@pytest.mark.asyncio
async def test_multi_edge_lexical_tiebreak(tmp_path):
    """Spec §3.3: when weights are equal, lexical target-id tiebreak applies.

    Two edges from 'check' carry the same condition and equal weight.
    The lexically-first target id ('aaa') wins.
    """
    executed_nodes: list[str] = []

    class TrackingBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            executed_nodes.append(node.id)
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="test-lexical-tiebreak",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "check": Node(id="check", shape="box", prompt="Check"),
            "aaa": Node(id="aaa", shape="box", prompt="AAA"),
            "zzz": Node(id="zzz", shape="box", prompt="ZZZ"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="check"),
            # Equal weight; 'aaa' < 'zzz' lexically — 'aaa' wins.
            Edge(from_node="check", to_node="aaa", condition="outcome=success"),
            Edge(from_node="check", to_node="zzz", condition="outcome=success"),
            Edge(from_node="aaa", to_node="exit"),
            Edge(from_node="zzz", to_node="exit"),
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
    assert "aaa" in executed_nodes, "aaa (lexically first) must be selected"
    assert "zzz" not in executed_nodes, "zzz must NOT execute (lexically later)"


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


# --- Parallel fan-out: shape=component (spec-sanctioned explicit parallelism) ---
#
# shape=component nodes fan out ALL outgoing edges via ParallelHandler.
# This is the spec-sanctioned path for explicit parallelism (§3.8, §4.8,
# EXTENSIONS.md #18).  It is structurally separate from the retired
# multi-match fan-out path and is untouched by T0-4.


@pytest.mark.asyncio
async def test_parallel_fan_out_branches_run_concurrently(tmp_path):
    """shape=component: three parallel branches each sleeping 0.2s finish in < 0.5s wall-clock."""

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
            "src": Node(id="src", shape="component"),
            "b1": Node(id="b1", shape="box", prompt="B1"),
            "b2": Node(id="b2", shape="box", prompt="B2"),
            "b3": Node(id="b3", shape="box", prompt="B3"),
            "converge": Node(id="converge", shape="tripleoctagon"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="src"),
            # shape=component fans out ALL unconditional outgoing edges.
            Edge(from_node="src", to_node="b1"),
            Edge(from_node="src", to_node="b2"),
            Edge(from_node="src", to_node="b3"),
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
    """shape=component: each parallel branch gets its own cloned handler registry."""
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
            "src": Node(id="src", shape="component"),
            "b1": Node(id="b1", shape="box", prompt="B1"),
            "b2": Node(id="b2", shape="box", prompt="B2"),
            "b3": Node(id="b3", shape="box", prompt="B3"),
            "converge": Node(id="converge", shape="tripleoctagon"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="src"),
            Edge(from_node="src", to_node="b1"),
            Edge(from_node="src", to_node="b2"),
            Edge(from_node="src", to_node="b3"),
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
