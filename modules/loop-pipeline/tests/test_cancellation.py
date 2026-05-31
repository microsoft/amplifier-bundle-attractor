"""Tests for engine-side cooperative cancellation.

Spec coverage: EXEC-019 (cooperative cancellation via threading.Event).
"""

import threading

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LINEAR_DOT = """
digraph {
    start [shape=Mdiamond]
    step1 [prompt="Do step 1"]
    step2 [prompt="Do step 2"]
    exit [shape=Msquare]
    start -> step1 -> step2 -> exit
}
"""


class RecordingBackend:
    """Records which nodes were executed and returns a fixed outcome."""

    def __init__(self, return_value: str = "done"):
        self._return_value = return_value
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        return self._return_value


class BlockingBackend:
    """Records calls; sets a threading.Event when the first node runs."""

    def __init__(self, signal_after: str, signal_event: threading.Event):
        self._signal_after = signal_after
        self._signal_event = signal_event
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if node.id == self._signal_after:
            self._signal_event.set()
        return "done"


def _make_engine(
    dot_source: str,
    backend=None,
    logs_root: str = "/tmp/test-pipeline-cancel",
    cancel_event: threading.Event | None = None,
) -> PipelineEngine:
    """Parse DOT, validate, and build an engine with optional cancel_event."""
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        cancel_event=cancel_event,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_engine_accepts_cancel_event_parameter(tmp_path):
    """PipelineEngine can be constructed with a threading.Event."""
    cancel_event = threading.Event()
    engine = _make_engine(
        dot_source=_LINEAR_DOT,
        backend=RecordingBackend(),
        logs_root=str(tmp_path),
        cancel_event=cancel_event,
    )
    assert engine is not None


@pytest.mark.asyncio
async def test_engine_stops_on_cancel_before_first_node(tmp_path):
    """Set cancel event before run(); engine returns cancelled outcome immediately."""
    cancel_event = threading.Event()
    cancel_event.set()  # Already cancelled before run()

    backend = RecordingBackend()
    engine = _make_engine(
        dot_source=_LINEAR_DOT,
        backend=backend,
        logs_root=str(tmp_path),
        cancel_event=cancel_event,
    )

    outcome = await engine.run()

    # Should have stopped without executing any work nodes
    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "cancelled"
    # step1 and step2 should NOT have been called
    assert "step1" not in backend.calls
    assert "step2" not in backend.calls


@pytest.mark.asyncio
async def test_engine_stops_on_cancel_between_nodes(tmp_path):
    """Set cancel event after first node completes; engine stops before second node."""
    cancel_event = threading.Event()

    class SetCancelAfterFirstBackend:
        """Sets cancel_event after step1 executes."""

        def __init__(self):
            self.calls: list[str] = []

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            self.calls.append(node.id)
            if node.id == "step1":
                cancel_event.set()
            return "done"

    backend = SetCancelAfterFirstBackend()
    engine = _make_engine(
        dot_source=_LINEAR_DOT,
        backend=backend,
        logs_root=str(tmp_path),
        cancel_event=cancel_event,
    )

    outcome = await engine.run()

    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "cancelled"
    # step1 ran, but step2 should NOT have been called
    assert "step1" in backend.calls
    assert "step2" not in backend.calls


@pytest.mark.asyncio
async def test_cancel_outcome_has_correct_status(tmp_path):
    """Cancelled outcome has status=FAIL and failure_reason='cancelled'."""
    cancel_event = threading.Event()
    cancel_event.set()

    engine = _make_engine(
        dot_source=_LINEAR_DOT,
        backend=RecordingBackend(),
        logs_root=str(tmp_path),
        cancel_event=cancel_event,
    )

    outcome = await engine.run()

    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "cancelled"
    assert "cancel" in (outcome.notes or "").lower()


@pytest.mark.asyncio
async def test_engine_without_cancel_event_runs_normally(tmp_path):
    """When cancel_event=None, engine runs to completion (regression test)."""
    backend = RecordingBackend("done")
    engine = _make_engine(
        dot_source=_LINEAR_DOT,
        backend=backend,
        logs_root=str(tmp_path),
        cancel_event=None,  # explicit None
    )

    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "step1" in backend.calls
    assert "step2" in backend.calls


@pytest.mark.asyncio
async def test_cancel_emits_pipeline_complete_event(tmp_path):
    """When cancelled, engine emits pipeline:complete with status 'cancelled'."""
    cancel_event = threading.Event()
    cancel_event.set()

    emitted_events: list[dict] = []

    class CapturingHooks:
        async def emit(self, event_name: str, data: dict) -> None:
            emitted_events.append({"event": event_name, "data": data})

    graph = parse_dot(_LINEAR_DOT)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=RecordingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
        hooks=CapturingHooks(),
        cancel_event=cancel_event,
    )

    await engine.run()

    # Find pipeline:complete event
    complete_events = [e for e in emitted_events if e["event"] == "pipeline:complete"]
    assert len(complete_events) >= 1
    complete_data = complete_events[-1]["data"]
    assert complete_data["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Cancellation propagation to nested child pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_propagates_to_nested_child_pipeline(tmp_path):
    """When parent is cancelled, the child pipeline (nested via PipelineHandler) also stops.

    Wiring: parent PipelineEngine -> HandlerRegistry(HandlerContext(cancel_event=...)) ->
            PipelineHandler(cancel_event=..., handler_registry_factory=...) ->
            child PipelineEngine(cancel_event=...)

    The handler_registry_factory ensures the child engine uses our test
    backend (so we can trigger cancellation from inside child execution).
    """
    from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler

    # -- Child DOT: 3 work nodes so cancellation can interrupt mid-execution --
    child_dot = """\
digraph child {
    start [shape=Mdiamond]
    child_step1 [prompt="Child step 1"]
    child_step2 [prompt="Child step 2"]
    child_step3 [prompt="Child step 3"]
    done [shape=Msquare]
    start -> child_step1 -> child_step2 -> child_step3 -> done
}
"""
    child_dot_path = tmp_path / "child.dot"
    child_dot_path.write_text(child_dot)

    # -- Parent DOT: folder node references the child DOT --
    parent_dot = f"""\
digraph parent {{
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="{child_dot_path}"]
    done [shape=Msquare]
    start -> sub -> done
}}
"""

    cancel_event = threading.Event()

    # Backend that sets cancel_event after child_step1 runs, so the child
    # engine sees cancellation before executing child_step2.
    class CancelAfterChildStep1:
        def __init__(self):
            self.calls: list[str] = []

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            self.calls.append(node.id)
            if node.id == "child_step1":
                cancel_event.set()
            return "done"

    backend = CancelAfterChildStep1()

    graph = parse_dot(parent_dot)
    validate_or_raise(graph)
    context = PipelineContext()

    # Build parent registry with cancel_event and our backend
    registry = HandlerRegistry(HandlerContext(backend=backend, cancel_event=cancel_event))

    # Override the pipeline handler with one that uses a factory so the
    # *child* engine also gets our test backend (the default child registry
    # would use a simulated backend that doesn't trigger cancellation).
    registry.register(
        "pipeline",
        PipelineHandler(
            handler_registry_factory=lambda: HandlerRegistry(HandlerContext(backend=backend)),
            cancel_event=cancel_event,
        ),
    )

    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path / "logs"),
        cancel_event=cancel_event,
    )

    outcome = await engine.run()

    # Parent outcome should be cancelled
    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "cancelled"

    # child_step1 ran (it triggered the cancel), but child_step2 and
    # child_step3 should NOT have run — proving the child was interrupted.
    assert "child_step1" in backend.calls
    assert "child_step2" not in backend.calls
    assert "child_step3" not in backend.calls
