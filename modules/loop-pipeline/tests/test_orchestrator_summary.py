"""Tests for pipeline summary building in PipelineOrchestrator.

Covers Bug 1 fix: ensures the orchestrator produces a meaningful summary
even when the final node's outcome has no notes.
"""

from amplifier_module_loop_pipeline import PipelineOrchestrator
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# --- Mock backend that returns outcomes with no notes ---


class _NullNotesBackend:
    """Backend that returns SUCCESS with notes=None for every node."""

    async def run(self, node, prompt, context, **kwargs):
        return Outcome(status=StageStatus.SUCCESS, notes=None)


class _RichNotesBackend:
    """Backend that returns SUCCESS with meaningful notes."""

    async def run(self, node, prompt, context, **kwargs):
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Completed node {node.id} with full results and details that are meaningful",
        )


# --- _build_pipeline_summary ---


def test_summary_with_null_notes():
    """Orchestrator builds a summary when outcome.notes is None."""
    orchestrator = PipelineOrchestrator(
        {"dot_source": "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"}
    )
    engine = _make_engine_with_completed_nodes(notes=None)
    outcome = Outcome(status=StageStatus.SUCCESS, notes=None)

    summary = orchestrator._build_pipeline_summary(engine, outcome)

    assert summary is not None
    assert len(summary) > 20
    assert "completed" in summary.lower() or "succeeded" in summary.lower()


def test_summary_with_short_notes():
    """Orchestrator synthesizes when outcome.notes is too short to be useful."""
    orchestrator = PipelineOrchestrator(
        {"dot_source": "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"}
    )
    engine = _make_engine_with_completed_nodes(notes="ok")
    outcome = Outcome(status=StageStatus.SUCCESS, notes="ok")

    summary = orchestrator._build_pipeline_summary(engine, outcome)

    assert len(summary) > 20


def test_summary_preserves_meaningful_notes():
    """Orchestrator uses outcome.notes when they are already meaningful."""
    orchestrator = PipelineOrchestrator(
        {"dot_source": "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"}
    )
    engine = _make_engine_with_completed_nodes(notes=None)
    long_notes = "This is a detailed description of what the pipeline accomplished across all stages."
    outcome = Outcome(status=StageStatus.SUCCESS, notes=long_notes)

    summary = orchestrator._build_pipeline_summary(engine, outcome)

    assert summary == long_notes


def test_summary_includes_node_count():
    """Summary includes how many nodes completed."""
    orchestrator = PipelineOrchestrator(
        {"dot_source": "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"}
    )
    engine = _make_engine_with_completed_nodes(notes=None, node_count=3)
    outcome = Outcome(status=StageStatus.SUCCESS, notes=None)

    summary = orchestrator._build_pipeline_summary(engine, outcome)

    assert "3" in summary


def test_summary_includes_failed_nodes():
    """Summary mentions failed nodes when present."""
    orchestrator = PipelineOrchestrator(
        {"dot_source": "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"}
    )
    engine = _make_engine_with_mixed_outcomes()
    outcome = Outcome(status=StageStatus.PARTIAL_SUCCESS, notes=None)

    summary = orchestrator._build_pipeline_summary(engine, outcome)

    assert "fail" in summary.lower() or "Failed" in summary


# --- Helpers ---


def _make_engine_with_completed_nodes(
    notes: str | None,
    node_count: int = 2,
) -> PipelineEngine:
    """Create a PipelineEngine with fake completed nodes."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    dot = 'digraph { start [shape=Mdiamond]; a [prompt="A"]; b [prompt="B"]; c [prompt="C"]; done [shape=Msquare]; start -> a -> b -> c -> done }'
    graph = parse_dot(dot)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=None))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root="/tmp/test-summary",
    )
    # Simulate completed nodes
    node_ids = [
        nid
        for nid in graph.nodes
        if graph.nodes[nid].shape not in ("Mdiamond", "Msquare")
    ]
    for nid in node_ids[:node_count]:
        engine.completed_nodes.append(nid)
        engine.node_outcomes[nid] = Outcome(status=StageStatus.SUCCESS, notes=notes)
    return engine


def _make_engine_with_mixed_outcomes() -> PipelineEngine:
    """Create a PipelineEngine with both successful and failed nodes."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    dot = 'digraph { start [shape=Mdiamond]; a [prompt="A"]; b [prompt="B"]; done [shape=Msquare]; start -> a -> b -> done }'
    graph = parse_dot(dot)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=None))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root="/tmp/test-summary",
    )
    node_ids = [
        nid
        for nid in graph.nodes
        if graph.nodes[nid].shape not in ("Mdiamond", "Msquare")
    ]
    engine.completed_nodes.append(node_ids[0])
    engine.node_outcomes[node_ids[0]] = Outcome(
        status=StageStatus.SUCCESS, notes="Done"
    )
    engine.completed_nodes.append(node_ids[1])
    engine.node_outcomes[node_ids[1]] = Outcome(
        status=StageStatus.FAIL, failure_reason="test error"
    )
    return engine
