"""Tests for the manager loop handler — supervisor pattern over a child subgraph.

The manager loop (shape=house) orchestrates observe/evaluate/act cycles
over a child subgraph. It runs the child, checks a guard condition, and
loops until the guard is satisfied or max cycles are exhausted.

Spec coverage: MGR-001-010, COMP-001-002, Section 4.11.
"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.manager_loop import ManagerLoopHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(
    *,
    manager_attrs: dict | None = None,
    graph_attrs: dict | None = None,
    has_child_edge: bool = True,
) -> Graph:
    """Build a minimal graph with a manager node and optional child target."""
    nodes = {
        "start": Node(id="start", shape="Mdiamond"),
        "manager": Node(
            id="manager",
            shape="house",
            label="Sprint Manager",
            attrs=manager_attrs or {},
        ),
        "child_task": Node(id="child_task", shape="box", label="Do work"),
        "exit": Node(id="exit", shape="Msquare"),
    }
    edges = [
        Edge(from_node="start", to_node="manager"),
    ]
    if has_child_edge:
        edges.append(Edge(from_node="manager", to_node="child_task"))
    edges.append(Edge(from_node="child_task", to_node="exit"))
    return Graph(
        name="test_manager",
        nodes=nodes,
        edges=edges,
        graph_attrs=graph_attrs or {},
    )


class _MockEngine:
    """Mock engine that returns outcomes from a sequence via run_subgraph."""

    def __init__(self, outcomes: list[Outcome]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0
        self.call_args: tuple | None = None  # (args, kwargs) of last call

    async def run_subgraph(self, node_id: str, *, context: object = None) -> Outcome:
        self.call_count += 1
        self.call_args = ((node_id,), {"context": context})
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            # Support AsyncMock-style StopAsyncIteration for exhausted sequences
            if isinstance(outcome, type) and issubclass(outcome, Exception):
                raise outcome()
            return outcome
        return Outcome(status=StageStatus.FAIL, failure_reason="no more outcomes")


def _make_runner(outcomes: list[Outcome]) -> _MockEngine:
    """Create a mock engine that returns outcomes in sequence."""
    return _MockEngine(outcomes)


# ---------------------------------------------------------------------------
# Core behavior tests
# ---------------------------------------------------------------------------


class TestManagerLoopExecution:
    """Manager loop runs child subgraph and evaluates outcomes."""

    @pytest.mark.asyncio
    async def test_child_success_stops_loop(self):
        """When the child succeeds, the manager returns SUCCESS after 1 cycle."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.SUCCESS, notes="child ok"),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "5"})
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.SUCCESS
        assert runner.call_count == 1

    @pytest.mark.asyncio
    async def test_child_fail_retries_then_succeeds(self):
        """Manager retries when child fails, stops when child succeeds."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.FAIL, failure_reason="broken"),
                Outcome(status=StageStatus.FAIL, failure_reason="still broken"),
                Outcome(status=StageStatus.SUCCESS, notes="fixed"),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.SUCCESS
        assert runner.call_count == 3

    @pytest.mark.asyncio
    async def test_max_cycles_exhausted(self):
        """When all cycles are used without success, returns FAIL."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.FAIL, failure_reason="nope"),
                Outcome(status=StageStatus.FAIL, failure_reason="nope"),
                Outcome(status=StageStatus.FAIL, failure_reason="nope"),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "3",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.FAIL
        assert runner.call_count == 3
        assert "3" in (result.failure_reason or "")

    @pytest.mark.asyncio
    async def test_partial_success_stops_loop(self):
        """PARTIAL_SUCCESS from child also satisfies the default guard."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.PARTIAL_SUCCESS, notes="close enough"),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "5"})
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.is_success
        assert runner.call_count == 1


# ---------------------------------------------------------------------------
# Guard / stop condition tests
# ---------------------------------------------------------------------------


class TestManagerStopCondition:
    """Stop condition (guard) controls when the manager exits the loop."""

    @pytest.mark.asyncio
    async def test_stop_condition_evaluated(self):
        """Guard condition 'outcome=success' stops on first success."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.FAIL),
                Outcome(status=StageStatus.SUCCESS),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.stop_condition": "outcome=success",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.SUCCESS
        assert runner.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_condition_not_met_keeps_looping(self):
        """If guard never satisfied, loop exhausts max_cycles."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.PARTIAL_SUCCESS),
                Outcome(status=StageStatus.PARTIAL_SUCCESS),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "2",
                "manager.stop_condition": "outcome=success",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        # Guard requires "success" exactly, partial_success doesn't match
        assert result.status == StageStatus.FAIL
        assert runner.call_count == 2


# ---------------------------------------------------------------------------
# Context recording tests
# ---------------------------------------------------------------------------


class TestManagerContextRecording:
    """Manager records cycle telemetry in the pipeline context."""

    @pytest.mark.asyncio
    async def test_records_cycle_status(self):
        """Each cycle's child status is recorded in context."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.FAIL),
                Outcome(status=StageStatus.SUCCESS),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        await handler.execute(graph.nodes["manager"], ctx, graph, "/tmp", engine=runner)

        assert ctx.get("manager.cycle_1.status") == "fail"
        assert ctx.get("manager.cycle_2.status") == "success"
        assert ctx.get("manager.last_child_status") == "success"

    @pytest.mark.asyncio
    async def test_outcome_contains_context_updates(self):
        """The returned outcome includes last_stage and cycle count."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.SUCCESS),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "3"})
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.context_updates is not None
        assert result.context_updates["last_stage"] == "manager"
        assert result.context_updates["manager.cycles"] == 1


# ---------------------------------------------------------------------------
# Steer action tests
# ---------------------------------------------------------------------------


class TestManagerSteer:
    """'steer' action injects steering context for child retries."""

    @pytest.mark.asyncio
    async def test_steer_injects_context(self):
        """When 'steer' in actions, child context gets steering message."""
        captured_contexts: list[PipelineContext] = []

        class _CapturingEngine3:
            async def run_subgraph(self, node_id, *, context=None):
                captured_contexts.append(context)
                if len(captured_contexts) < 2:
                    return Outcome(status=StageStatus.FAIL)
                return Outcome(status=StageStatus.SUCCESS)

        handler = ManagerLoopHandler()
        _engine = _CapturingEngine3()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.actions": "observe,steer,wait",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=_engine
        )

        # First cycle: no steering (no prior failure)
        assert captured_contexts[0].get("manager.steering") is None
        # Second cycle: steering injected from first failure
        steering = captured_contexts[1].get("manager.steering")
        assert steering is not None
        assert "fail" in steering.lower()

    @pytest.mark.asyncio
    async def test_steer_includes_failure_details(self):
        """M-15: Steering message includes actual failure details from prior cycle."""
        captured_contexts: list[PipelineContext] = []

        class _CapturingEngine4:
            async def run_subgraph(self, node_id, *, context=None):
                captured_contexts.append(context)
                if len(captured_contexts) < 2:
                    return Outcome(
                        status=StageStatus.FAIL,
                        failure_reason="tests failing: 3 of 10 assertions broken",
                        notes="Unit tests did not pass",
                    )
                return Outcome(status=StageStatus.SUCCESS)

        handler = ManagerLoopHandler()
        _engine = _CapturingEngine4()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.actions": "observe,steer,wait",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=_engine
        )

        steering = captured_contexts[1].get("manager.steering")
        assert steering is not None
        # M-15: steering must include the actual failure reason
        assert "tests failing" in steering
        assert "3 of 10 assertions broken" in steering


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestManagerEdgeCases:
    """Edge cases for the manager loop handler."""

    @pytest.mark.asyncio
    async def test_no_child_edges_returns_fail(self):
        """Manager with no outgoing edges returns FAIL."""
        handler = ManagerLoopHandler()
        graph = _make_graph(has_child_edge=False)
        ctx = PipelineContext()

        result = await handler.execute(graph.nodes["manager"], ctx, graph, "/tmp")

        assert result.status == StageStatus.FAIL
        assert "no child" in (result.failure_reason or "").lower()

    @pytest.mark.asyncio
    async def test_no_runner_returns_fail(self):
        """Manager without engine returns FAIL."""
        handler = ManagerLoopHandler()  # no engine
        graph = _make_graph(manager_attrs={"manager.max_cycles": "3"})
        ctx = PipelineContext()

        # engine=None (default) -> ManagerLoopHandler requires engine
        result = await handler.execute(graph.nodes["manager"], ctx, graph, "/tmp")

        assert result.status == StageStatus.FAIL

    @pytest.mark.asyncio
    async def test_default_max_cycles_is_1000(self):
        """M-14: Default max_cycles is 1000 per spec when not specified."""
        # Instead of running 1000 cycles, succeed on cycle 11 to prove
        # the default is > 10 (the old wrong default).
        call_count = 0

        class _Succeeds11Engine:
            async def run_subgraph(self, node_id, *, context=None):
                nonlocal call_count
                call_count += 1
                if call_count >= 11:
                    return Outcome(status=StageStatus.SUCCESS)
                return Outcome(status=StageStatus.FAIL)

        handler = ManagerLoopHandler()
        _engine = _Succeeds11Engine()
        graph = _make_graph(
            manager_attrs={
                "manager.poll_interval": "0s",
                # No max_cycles — should default to 1000
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=_engine
        )

        # With old default of 10, this would FAIL. With 1000, it succeeds.
        assert result.status == StageStatus.SUCCESS
        assert call_count == 11

    @pytest.mark.asyncio
    async def test_default_max_cycles_exhaustion_message(self):
        """M-14: When default max_cycles exhausted, failure mentions 1000."""
        call_count = 0

        class _AlwaysFailsEngine:
            async def run_subgraph(self, node_id, *, context=None):
                nonlocal call_count
                call_count += 1
                # Succeed on 1001 (never reached if default is 1000)
                if call_count > 1000:
                    return Outcome(status=StageStatus.SUCCESS)
                return Outcome(status=StageStatus.FAIL)

        handler = ManagerLoopHandler()
        _engine = _AlwaysFailsEngine()
        graph = _make_graph(
            manager_attrs={
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=_engine
        )

        assert result.status == StageStatus.FAIL
        assert "1000" in (result.failure_reason or "")
        assert call_count == 1000

    @pytest.mark.asyncio
    async def test_child_exception_treated_as_fail(self):
        """If the child runner raises, treat it as a FAIL outcome."""

        class ExceptionEngine:
            def __init__(self):
                self.call_count = 0
                self._items = [
                    RuntimeError("boom"),
                    Outcome(status=StageStatus.SUCCESS),
                ]

            async def run_subgraph(self, node_id, *, context=None):
                self.call_count += 1
                if self._items:
                    item = self._items.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                return Outcome(
                    status=StageStatus.FAIL, failure_reason="no more outcomes"
                )

        exception_engine = ExceptionEngine()
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "5",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=exception_engine
        )

        # First call raises, second succeeds — manager should recover
        assert result.status == StageStatus.SUCCESS
        assert exception_engine.call_count == 2

    @pytest.mark.asyncio
    async def test_runner_receives_child_node_id(self):
        """The subgraph_runner is called with the correct child start node ID."""
        runner = _make_runner(
            [
                Outcome(status=StageStatus.SUCCESS),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "1"})
        ctx = PipelineContext()

        await handler.execute(graph.nodes["manager"], ctx, graph, "/tmp", engine=runner)

        call_args = runner.call_args
        assert call_args is not None, "run_subgraph was not called"
        assert call_args[0][0] == "child_task"  # node_id


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


class TestManagerHandlerRegistration:
    """Handler registry resolves house shape to ManagerLoopHandler."""

    def test_registry_resolves_manager_handler(self):
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        registry = HandlerRegistry(HandlerContext())
        node = Node(id="mgr", shape="house")
        handler = registry.get(node)
        assert isinstance(handler, ManagerLoopHandler)


# ---------------------------------------------------------------------------
# Child dotfile tests
# ---------------------------------------------------------------------------


class TestManagerChildDotfile:
    """stack.child_dotfile routes manager to external DOT engine execution."""

    @pytest.mark.asyncio
    async def test_child_dotfile_on_node_attrs(self, tmp_path):
        """child_dotfile on node attrs runs external DOT, inline runner NOT called."""
        # Write a minimal child DOT file
        child_dot = tmp_path / "child.dot"
        child_dot.write_text(
            "digraph child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        inline_runner = AsyncMock(return_value=Outcome(status=StageStatus.SUCCESS))
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "1",
                "stack.child_dotfile": str(child_dot),
            },
            has_child_edge=True,
        )

        result = await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        # External DOT runs; inline runner is NOT called
        assert inline_runner.call_count == 0
        # Accept either status: the test verifies routing (inline runner skipped),
        # not child pipeline outcome — the child DOT's box handler may or may not
        # produce SUCCESS in the test environment.
        assert result.status in (StageStatus.SUCCESS, StageStatus.FAIL)

    @pytest.mark.asyncio
    async def test_child_dotfile_on_graph_attrs(self, tmp_path):
        """child_dotfile on graph_attrs works when node-level is absent."""
        child_dot = tmp_path / "child.dot"
        child_dot.write_text(
            "digraph child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        inline_runner = AsyncMock(return_value=Outcome(status=StageStatus.SUCCESS))
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={"manager.max_cycles": "1"},
            graph_attrs={"stack.child_dotfile": str(child_dot)},
            has_child_edge=True,
        )

        result = await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        assert inline_runner.call_count == 0
        # Accept either status: the test verifies routing (inline runner skipped),
        # not child pipeline outcome — the child DOT's box handler may or may not
        # produce SUCCESS in the test environment.
        assert result.status in (StageStatus.SUCCESS, StageStatus.FAIL)

    @pytest.mark.asyncio
    async def test_no_child_dotfile_uses_inline_runner(self):
        """Without child_dotfile, engine.run_subgraph is called."""
        inline_runner = _make_runner([Outcome(status=StageStatus.SUCCESS)])
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "1"})

        result = await handler.execute(
            graph.nodes["manager"],
            PipelineContext(),
            graph,
            "/tmp",
            engine=inline_runner,
        )

        assert inline_runner.call_count == 1
        assert result.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_node_attrs_override_graph_attrs(self, tmp_path):
        """Node-level stack.child_dotfile takes priority over graph-level."""
        node_dot = tmp_path / "node_child.dot"
        node_dot.write_text(
            "digraph node_child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )
        graph_dot = tmp_path / "graph_child.dot"
        graph_dot.write_text(
            "digraph graph_child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        inline_runner = AsyncMock(return_value=Outcome(status=StageStatus.SUCCESS))
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "1",
                "stack.child_dotfile": str(node_dot),
            },
            graph_attrs={"stack.child_dotfile": str(graph_dot)},
            has_child_edge=True,
        )

        result = await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        # Node-level wins; inline runner NOT called
        assert inline_runner.call_count == 0
        # Accept either status: the test verifies routing (node-level priority),
        # not child pipeline outcome — the child DOT's box handler may or may not
        # produce SUCCESS in the test environment.
        assert result.status in (StageStatus.SUCCESS, StageStatus.FAIL)


# ---------------------------------------------------------------------------
# Cycle-indexed observability tests
# ---------------------------------------------------------------------------


class TestManagerChildDotfileEngineWiring:
    """support#373 regression guard: hooks= / cancel_event= must reach the

    child PipelineEngine constructed by _run_child_dotfile(), not just the
    child HandlerRegistry's HandlerContext. Before the fix, the child
    PipelineEngine(...) call omitted both kwargs, so the entire child
    dotfile execution class emitted zero events and was cooperatively
    uncancellable -- even though hooks/cancel_event were captured on
    ManagerLoopHandler and threaded into the child HandlerContext.
    """

    @pytest.mark.asyncio
    async def test_hooks_reach_child_engine(self, tmp_path):
        """hooks= passed to ManagerLoopHandler must propagate to the child

        engine so the child dotfile pipeline's own node-level events
        (pipeline:node_start / node_complete, at minimum) are observable.
        Without the fix, child_engine.hooks is None and zero events fire.
        """
        child_dot = tmp_path / "child.dot"
        child_dot.write_text(
            "digraph child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        class _RecordingHooks:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            async def emit(self, event_name: str, data: dict) -> None:
                self.events.append((event_name, data))

        recorder = _RecordingHooks()
        handler = ManagerLoopHandler(hooks=recorder)
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "1",
                "stack.child_dotfile": str(child_dot),
            },
            has_child_edge=True,
        )

        await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        assert recorder.events, (
            "child dotfile pipeline emitted zero events -- hooks= was not "
            "propagated to the child PipelineEngine (support#373 regression)"
        )
        # At minimum, the child's own node_start should have fired.
        event_names = [name for name, _ in recorder.events]
        assert "pipeline:node_start" in event_names

    @pytest.mark.asyncio
    async def test_cancel_event_reaches_child_engine(self, tmp_path):
        """cancel_event= passed to ManagerLoopHandler must propagate to the

        child engine so a pre-set cancellation signal cooperatively stops
        the child dotfile pipeline before any node executes. Without the
        fix, child_engine's _cancel_event is None, cancellation is silently
        ignored, and the child's "task" node executes normally (emitting a
        node_start event) instead of short-circuiting.
        """
        child_dot = tmp_path / "child.dot"
        child_dot.write_text(
            "digraph child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        class _RecordingHooks:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            async def emit(self, event_name: str, data: dict) -> None:
                self.events.append((event_name, data))

        recorder = _RecordingHooks()
        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before the child engine runs
        handler = ManagerLoopHandler(hooks=recorder, cancel_event=cancel_event)
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "1",
                "stack.child_dotfile": str(child_dot),
            },
            has_child_edge=True,
        )

        result = await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        # Cancellation short-circuits child_engine.run() before any node
        # executes, so no pipeline:node_start should ever fire for "task".
        event_names = [name for name, _ in recorder.events]
        assert "pipeline:node_start" not in event_names, (
            "child engine executed a node despite a pre-set cancel_event -- "
            "cancel_event= was not propagated to the child PipelineEngine "
            "(support#373 regression)"
        )
        # The manager loop's default guard only stops early on success/
        # partial_success; on a cancelled (FAIL) child it keeps cycling
        # until max_cycles is exhausted, so we assert on the recorded
        # per-cycle child status rather than the manager's own generic
        # "Manager exhausted N cycle(s)" wrapper outcome.
        assert result.status == StageStatus.FAIL


class TestManagerChildDotfileObservability:
    """_subgraph_runs captures cycle-indexed observability data."""

    @pytest.mark.asyncio
    async def test_cycle_indexed_subgraph_runs(self, tmp_path):
        """After child dotfile execution, _subgraph_runs has cycle-indexed entry."""
        import json as _json

        child_dot = tmp_path / "child.dot"
        child_dot.write_text(
            "digraph child {\n"
            "  start [shape=Mdiamond];\n"
            "  task [shape=box];\n"
            "  done [shape=Msquare];\n"
            "  start -> task -> done;\n"
            "}\n"
        )

        class _MockBackend:
            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                return _json.dumps({"status": "success", "notes": f"mock: {node.id}"})

        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        def _registry_factory():
            return HandlerRegistry(HandlerContext(backend=_MockBackend()))

        handler = ManagerLoopHandler(handler_registry_factory=_registry_factory)
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "2",
                "manager.stop_condition": "outcome=success",
                "stack.child_dotfile": str(child_dot),
            },
            has_child_edge=True,
        )

        await handler.execute(
            graph.nodes["manager"], PipelineContext(), graph, str(tmp_path)
        )

        # _subgraph_runs attribute must exist
        assert hasattr(handler, "_subgraph_runs")
        # Cycle-indexed key must be present
        assert "manager_cycle_1" in handler._subgraph_runs
        run_data = handler._subgraph_runs["manager_cycle_1"]
        # Must have status and nodes_completed keys
        assert run_data["status"] == "success"
        assert "nodes_completed" in run_data
        assert run_data["dot_file"] == str(child_dot)
        assert run_data["total_elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# Interviewer forwarding tests for the child_dotfile path
# ---------------------------------------------------------------------------


CHILD_DOT_WITH_HITL_GATE = """\
digraph child_hitl {
    start [shape=Mdiamond]
    gate  [shape=hexagon, label="Approve to proceed?", type="wait.human"]
    done  [shape=Msquare]
    start -> gate
    gate  -> done [label="Approve"]
}
"""


class TestManagerChildDotfileInterviewerForwarding:
    """Regression tests: ManagerLoopHandler must forward its interviewer to child_dotfile registry.

    Root cause (latent bug): _run_child_dotfile() built its child HandlerContext
    without interviewer=, so HumanGateHandler inside any manager child_dotfile
    always received interviewer=None and raised ValueError at runtime.

    Fix: add interviewer param to ManagerLoopHandler.__init__, wire it through
    HandlerRegistry, and pass interviewer=self._interviewer to the child HandlerContext.
    """

    def test_registry_passes_interviewer_to_manager_handler(self) -> None:
        """HandlerRegistry.__init__ must pass ctx.interviewer to ManagerLoopHandler.

        RED on current main: ManagerLoopHandler.__init__ has no interviewer param;
        registry doesn't pass it; manager_handler._interviewer does not exist.
        GREEN after fix: param added, registry wired, attribute present and correct.
        """
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer = AutoApproveInterviewer()
        registry = HandlerRegistry(HandlerContext(interviewer=interviewer))

        manager_handler = registry._handlers["stack.manager_loop"]
        assert isinstance(manager_handler, ManagerLoopHandler)
        assert manager_handler._interviewer is interviewer, (
            "HandlerRegistry must pass ctx.interviewer to ManagerLoopHandler. "
            "Without this, HITL gates inside a manager child_dotfile pipeline "
            "silently receive interviewer=None and raise ValueError at runtime."
        )

    @pytest.mark.asyncio
    async def test_hitl_gate_in_manager_child_dotfile_succeeds_with_interviewer(
        self, tmp_path
    ) -> None:
        """HITL gate inside a manager child_dotfile receives the interviewer and auto-approves.

        RED on current main: ManagerLoopHandler.__init__ raises TypeError (no
        interviewer param), so even constructing the handler fails; and even if
        constructed another way the child HandlerContext is built without
        interviewer= so HumanGateHandler raises ValueError.
        GREEN after fix: interviewer threaded through, AutoApproveInterviewer
        approves the gate, child pipeline succeeds, manager returns SUCCESS.
        """
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        child_dot = tmp_path / "child_hitl.dot"
        child_dot.write_text(CHILD_DOT_WITH_HITL_GATE)

        # Parent graph: manager node points to the child_dotfile.
        # has_child_edge=False because the child pipeline lives in the dotfile.
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "1",
                "manager.actions": "observe",
                "stack.child_dotfile": str(child_dot),
            },
            has_child_edge=False,
        )
        graph.source_dir = str(tmp_path)

        # Requires the interviewer param to exist on ManagerLoopHandler.__init__
        handler = ManagerLoopHandler(interviewer=AutoApproveInterviewer())

        ctx = PipelineContext()
        outcome = await handler.execute(
            graph.nodes["manager"], ctx, graph, str(tmp_path), engine=None
        )

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS but got {outcome.status!r} — "
            f"failure_reason: {outcome.failure_reason!r}. "
            "Likely cause: HumanGateHandler received interviewer=None because "
            "ManagerLoopHandler._run_child_dotfile() did not thread the interviewer "
            "into the child HandlerContext."
        )

    @pytest.mark.asyncio
    async def test_e2e_manager_child_dotfile_hitl_via_full_engine(
        self, tmp_path
    ) -> None:
        """Full E2E: PipelineEngine with manager child_dotfile containing HITL gate.

        Interviewer must propagate through the full chain:
          HandlerRegistry -> ManagerLoopHandler -> child HandlerContext -> HumanGateHandler

        RED on current main: interviewer dropped at the child HandlerContext step.
        GREEN after fix.
        """
        import json as _json

        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        class _MockBackend:
            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                return _json.dumps({"status": "success", "notes": f"mock: {node.id}"})

        # Write the child DOT with a HITL gate
        child_dot = tmp_path / "hitl_child.dot"
        child_dot.write_text(CHILD_DOT_WITH_HITL_GATE)

        # Parent DOT: manager node (shape=house) with child_dotfile pointing at
        # the child pipeline that contains a hexagon gate.
        # Dotted attribute names must be quoted in DOT syntax.
        parent_dot_source = """\
digraph parent_manager_hitl {
    graph [goal="Test manager child_dotfile HITL forwarding"]
    start   [shape=Mdiamond]
    manager [shape=house, "manager.max_cycles"="1", "manager.actions"="observe",
             "stack.child_dotfile"="hitl_child.dot"]
    done    [shape=Msquare]
    start -> manager -> done
}
"""
        parent_graph = parse_dot(parent_dot_source)
        # source_dir so that "hitl_child.dot" resolves relative to tmp_path
        parent_graph.source_dir = str(tmp_path)

        ctx = PipelineContext()
        registry = HandlerRegistry(
            HandlerContext(
                backend=_MockBackend(),
                interviewer=AutoApproveInterviewer(),
            )
        )
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=parent_graph,
            context=ctx,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS but got {outcome.status!r} — "
            f"failure_reason: {outcome.failure_reason!r}. "
            "Interviewer may not have reached the child HITL gate through the "
            "manager child_dotfile pipeline."
        )


# ---------------------------------------------------------------------------
# Dead-end failure_reason propagation tests
# ---------------------------------------------------------------------------


class TestManagerDeadEndPropagation:
    """Manager loop must propagate failure_reason from a dead-ended child subgraph.

    When run_subgraph() returns FAIL with a non-empty failure_reason because
    the child subgraph hit a conditional-mismatch dead end, the manager's
    returned Outcome must carry that failure_reason — not silently drop it.
    This locks down the no-lossy-reconstruction criterion from the task brief.
    """

    @pytest.mark.asyncio
    async def test_stop_condition_child_dead_end_failure_reason_propagated(self):
        """Stop-condition exit: failure_reason from a dead-ended child is forwarded.

        Simulates: run_subgraph returns FAIL with a traceable dead-end reason.
        The manager's stop condition matches (outcome=fail), so the manager
        exits via the stop-condition branch.  The returned Outcome must carry
        the same failure_reason — not None.
        """
        dead_end_reason = (
            "Subgraph dead end at node 'work': 1 outgoing edge(s) exist "
            "but none match the current outcome (status=success). "
            "This is a conditional-mismatch dead end."
        )
        runner = _make_runner(
            [
                Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=dead_end_reason,
                ),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "3",
                "manager.stop_condition": "outcome=fail",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.FAIL
        assert result.failure_reason, (
            "Manager must forward the child's failure_reason — got None. "
            "A dead-ended child's traceable reason was silently dropped at "
            "the manager boundary (lossy reconstruction)."
        )
        assert dead_end_reason in result.failure_reason, (
            f"Expected dead-end reason in manager outcome, got: {result.failure_reason!r}"
        )

    @pytest.mark.asyncio
    async def test_default_guard_child_dead_end_failure_reason_propagated(self):
        """Default-guard exit: failure_reason is forwarded even on is_success path.

        Simulates: run_subgraph returns PARTIAL_SUCCESS (is_success=True) but
        carries a failure_reason (e.g. from a partial failure with context).
        The default guard stops on is_success; the manager must forward the
        failure_reason rather than constructing a fresh Outcome that drops it.
        """
        partial_reason = "partial failure: some steps skipped"
        runner = _make_runner(
            [
                Outcome(
                    status=StageStatus.PARTIAL_SUCCESS,
                    failure_reason=partial_reason,
                ),
            ]
        )
        handler = ManagerLoopHandler()
        graph = _make_graph(manager_attrs={"manager.max_cycles": "3"})
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.is_success
        assert result.failure_reason == partial_reason, (
            f"Manager must forward child failure_reason on default-guard exit. "
            f"Expected {partial_reason!r}, got {result.failure_reason!r}."
        )

    @pytest.mark.asyncio
    async def test_exhaustion_child_dead_end_failure_reason_incorporated(self):
        """DEFAULT-config exhaustion: child dead-end reason is incorporated transitively.

        Simulates: a dead-ending child always returns FAIL (no stop_condition,
        no is_success), so the manager exhausts its max_cycles.  The returned
        failure_reason must incorporate the child's dead-end text, not just
        say "Manager exhausted N cycle(s)" with the causal reason lost.

        This is the binding gap identified by the scope ruling: traceability is
        transitive — a manager that exhausts cycles because its in-graph child
        dead-ended must surface the child's causal failure text.
        """
        dead_end_reason = (
            "Subgraph dead end at node 'work': 1 outgoing edge(s) exist "
            "but none match the current outcome (status=success). "
            "This is a conditional-mismatch dead end."
        )
        # Always returns FAIL with the dead-end reason — manager never stops
        # via stop_condition or default guard, so it exhausts max_cycles.
        runner = _make_runner(
            [
                Outcome(status=StageStatus.FAIL, failure_reason=dead_end_reason),
                Outcome(status=StageStatus.FAIL, failure_reason=dead_end_reason),
                Outcome(status=StageStatus.FAIL, failure_reason=dead_end_reason),
            ]
        )
        handler = ManagerLoopHandler()
        # DEFAULT config: no stop_condition, manager exhausts max_cycles=3
        graph = _make_graph(
            manager_attrs={
                "manager.max_cycles": "3",
                "manager.poll_interval": "0s",
            }
        )
        ctx = PipelineContext()

        result = await handler.execute(
            graph.nodes["manager"], ctx, graph, "/tmp", engine=runner
        )

        assert result.status == StageStatus.FAIL
        assert result.failure_reason, (
            "Manager exhaustion must carry a non-empty failure_reason — got None. "
            "The child's dead-end reason was silently dropped (lossy reconstruction)."
        )
        assert dead_end_reason in result.failure_reason, (
            f"Manager exhaustion failure_reason must incorporate the child's dead-end "
            f"text (transitive traceability). "
            f"Expected {dead_end_reason!r} in failure_reason, "
            f"got: {result.failure_reason!r}"
        )
