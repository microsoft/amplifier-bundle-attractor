"""Tests for Fix #1: ParallelHandler emits pipeline:node_start / pipeline:node_complete
for each parallel branch's target node.

Spec gap surfaced during PR #12 verification (instance a7777afaab76): branch nodes
executed successfully but their PIPELINE_NODE_START / PIPELINE_NODE_COMPLETE events
were missing from the main event stream.  External observers (CLI, UI, monitoring)
reading events.jsonl could not tell that 3 branches ran in parallel.

Fix: run_branch() now emits PIPELINE_NODE_START before invoking the subgraph runner
and PIPELINE_NODE_COMPLETE after it returns, with via_parallel=True so consumers can
distinguish these from main-loop node events without breaking consumers that don't
know about parallel fan-out.

Ordering contract:
  PIPELINE_PARALLEL_STARTED
    PIPELINE_NODE_START  (branch target, via_parallel=True)
    PIPELINE_NODE_COMPLETE (branch target, via_parallel=True)
  PIPELINE_PARALLEL_COMPLETED
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_START,
    PIPELINE_PARALLEL_COMPLETED,
    PIPELINE_PARALLEL_STARTED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeHooks:
    """Captures all emitted events for inspection."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})

    def events_of_type(self, name: str) -> list[dict[str, Any]]:
        """Return payloads for all events with the given name."""
        return [e["data"] for e in self.events if e["name"] == name]

    def event_names(self) -> list[str]:
        return [e["name"] for e in self.events]


def _make_fanout_graph(branch_ids: list[str]) -> tuple[Node, Graph]:
    """Build a minimal component → N-branch graph."""
    par_node = Node(id="fork", shape="component")
    nodes: dict[str, Node] = {"fork": par_node}
    for b in branch_ids:
        nodes[b] = Node(id=b, shape="parallelogram", prompt="...")
    graph = Graph(
        name="test",
        nodes=nodes,
        edges=[Edge(from_node="fork", to_node=b) for b in branch_ids],
    )
    return par_node, graph


class _SuccessEngine:
    """Mock engine that always returns SUCCESS for any subgraph execution."""

    async def run_subgraph(
        self, node_id: str, *, context: PipelineContext | None = None
    ) -> Outcome:
        return Outcome(status=StageStatus.SUCCESS, notes=f"{node_id} ran")


# ---------------------------------------------------------------------------
# Fix #1 regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_nodes_emit_pipeline_node_start_events() -> None:
    """Each parallel branch target emits a pipeline:node_start event (Fix #1)."""
    hooks = FakeHooks()
    branch_ids = ["variant_opus", "variant_sonnet", "variant_haiku"]
    par_node, graph = _make_fanout_graph(branch_ids)
    handler = ParallelHandler(hooks=hooks)

    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )

    start_events = hooks.events_of_type(PIPELINE_NODE_START)
    started_node_ids = {e["node_id"] for e in start_events}
    assert set(branch_ids) == started_node_ids, (
        f"Expected pipeline:node_start for each branch, "
        f"got node_ids: {started_node_ids!r}.  "
        f"Full start events: {start_events}"
    )


@pytest.mark.asyncio
async def test_branch_nodes_emit_pipeline_node_complete_events() -> None:
    """Each parallel branch target emits a pipeline:node_complete event (Fix #1)."""
    hooks = FakeHooks()
    branch_ids = ["variant_opus", "variant_sonnet", "variant_haiku"]
    par_node, graph = _make_fanout_graph(branch_ids)
    handler = ParallelHandler(hooks=hooks)

    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )

    complete_events = hooks.events_of_type(PIPELINE_NODE_COMPLETE)
    completed_node_ids = {e["node_id"] for e in complete_events}
    assert set(branch_ids) == completed_node_ids, (
        f"Expected pipeline:node_complete for each branch, "
        f"got node_ids: {completed_node_ids!r}.  "
        f"Full complete events: {complete_events}"
    )


@pytest.mark.asyncio
async def test_branch_node_complete_carries_correct_status() -> None:
    """pipeline:node_complete for a branch carries the branch's actual outcome status."""
    hooks = FakeHooks()
    par_node, graph = _make_fanout_graph(["good_branch", "bad_branch"])

    class MixedEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            if node_id == "bad_branch":
                return Outcome(status=StageStatus.FAIL, failure_reason="bad")
            return Outcome(status=StageStatus.SUCCESS, notes="ok")

    handler = ParallelHandler(hooks=hooks)
    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=MixedEngine()
    )

    complete_events = hooks.events_of_type(PIPELINE_NODE_COMPLETE)
    by_node = {e["node_id"]: e for e in complete_events}
    assert by_node["good_branch"]["status"] == "success"
    assert by_node["bad_branch"]["status"] == "fail"


@pytest.mark.asyncio
async def test_branch_node_events_carry_via_parallel_marker() -> None:
    """Branch node_start and node_complete events include via_parallel=True (Fix #1)."""
    hooks = FakeHooks()
    par_node, graph = _make_fanout_graph(["b0"])
    handler = ParallelHandler(hooks=hooks)

    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )

    start_events = hooks.events_of_type(PIPELINE_NODE_START)
    assert start_events, "Expected at least one pipeline:node_start event"
    bad_start = [e for e in start_events if e.get("via_parallel") is not True]
    assert not bad_start, (
        f"All branch node_start events must have via_parallel=True; "
        f"offending events: {bad_start}"
    )

    complete_events = hooks.events_of_type(PIPELINE_NODE_COMPLETE)
    assert complete_events, "Expected at least one pipeline:node_complete event"
    bad_complete = [e for e in complete_events if e.get("via_parallel") is not True]
    assert not bad_complete, (
        f"All branch node_complete events must have via_parallel=True; "
        f"offending events: {bad_complete}"
    )


@pytest.mark.asyncio
async def test_branch_node_complete_carries_duration_ms() -> None:
    """pipeline:node_complete for a branch includes a numeric duration_ms field."""
    hooks = FakeHooks()
    branch_ids = ["b0", "b1"]
    par_node, graph = _make_fanout_graph(branch_ids)
    handler = ParallelHandler(hooks=hooks)

    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )

    complete_events = hooks.events_of_type(PIPELINE_NODE_COMPLETE)
    # Guard: we must have one event per branch (not vacuously passing on empty list)
    assert len(complete_events) == len(branch_ids), (
        f"Expected {len(branch_ids)} node_complete events, got {len(complete_events)}"
    )
    for evt in complete_events:
        assert "duration_ms" in evt, f"Missing duration_ms in {evt}"
        assert isinstance(evt["duration_ms"], (int, float)), (
            f"duration_ms must be numeric; got {type(evt['duration_ms'])}"
        )


@pytest.mark.asyncio
async def test_branch_node_events_ordered_within_parallel_envelope() -> None:
    """Branch node_start/complete appear after parallel_started and before parallel_completed."""
    hooks = FakeHooks()
    par_node, graph = _make_fanout_graph(["b1", "b2"])
    handler = ParallelHandler(hooks=hooks)

    await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )

    names = hooks.event_names()

    first_parallel_started = next(
        i for i, n in enumerate(names) if n == PIPELINE_PARALLEL_STARTED
    )
    last_parallel_completed = max(
        i for i, n in enumerate(names) if n == PIPELINE_PARALLEL_COMPLETED
    )

    branch_start_indices = [i for i, n in enumerate(names) if n == PIPELINE_NODE_START]
    branch_complete_indices = [
        i for i, n in enumerate(names) if n == PIPELINE_NODE_COMPLETE
    ]

    for idx in branch_start_indices:
        assert idx > first_parallel_started, (
            f"branch node_start at index {idx} should come AFTER "
            f"parallel_started at {first_parallel_started}"
        )
    for idx in branch_complete_indices:
        assert idx < last_parallel_completed, (
            f"branch node_complete at index {idx} should come BEFORE "
            f"parallel_completed at {last_parallel_completed}"
        )


@pytest.mark.asyncio
async def test_no_branch_events_when_no_hooks() -> None:
    """ParallelHandler without hooks must not raise — fix is a no-op when hooks=None."""
    par_node, graph = _make_fanout_graph(["b0", "b1"])
    handler = ParallelHandler(hooks=None)

    # Should run without raising
    outcome = await handler.execute(
        par_node, PipelineContext(), graph, "/tmp/logs", engine=_SuccessEngine()
    )
    assert outcome.is_success


@pytest.mark.asyncio
async def test_branch_nodes_emit_exactly_one_node_start_via_real_engine(
    tmp_path,
) -> None:
    """support#379 regression guard: with a REAL PipelineEngine (not the

    _SuccessEngine stub used above), each branch node must emit exactly
    ONE pipeline:node_start / pipeline:node_complete pair — not two.

    Background: engine.run_subgraph() was given its own node_start/
    node_complete emission (support#379 fix 1) to un-darken
    ManagerLoopHandler's in-graph subgraph path. ParallelHandler already
    emits equivalent events for branch nodes (this file's Fix #1, tagged
    via_parallel=True). During this PR's own development, a first-pass
    implementation let BOTH emit, silently double-counting every branch
    node in the timeline (caught by the full test suite, not by any single
    targeted test — this test exists so it can never regress silently
    again). The tests above all use `_SuccessEngine`, a stub whose
    run_subgraph() never emits anything itself, so they could not catch
    this class of bug; this test uses the real engine + clone_for_branch()
    path exactly as production does.
    """
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext

    class _AlwaysSucceedHandler:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            return Outcome(status=StageStatus.SUCCESS, notes=f"{node.id} ran")

    hooks = FakeHooks()
    branch_ids = ["b0", "b1", "b2"]
    par_node, graph = _make_fanout_graph(branch_ids)

    registry = HandlerRegistry(HandlerContext())
    registry.register("parallelogram", _AlwaysSucceedHandler())

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
        hooks=hooks,
    )

    handler = ParallelHandler(hooks=hooks)
    outcome = await handler.execute(
        par_node, PipelineContext(), graph, str(tmp_path), engine=engine
    )

    assert outcome.is_success

    start_events = hooks.events_of_type(PIPELINE_NODE_START)
    complete_events = hooks.events_of_type(PIPELINE_NODE_COMPLETE)

    for branch_id in branch_ids:
        starts_for_branch = [e for e in start_events if e["node_id"] == branch_id]
        completes_for_branch = [e for e in complete_events if e["node_id"] == branch_id]
        assert len(starts_for_branch) == 1, (
            f"Expected exactly 1 pipeline:node_start for branch '{branch_id}', "
            f"got {len(starts_for_branch)} — run_subgraph()'s own emission "
            f"was not suppressed for ParallelHandler branches "
            f"(support#379 double-emission regression)."
        )
        assert len(completes_for_branch) == 1, (
            f"Expected exactly 1 pipeline:node_complete for branch '{branch_id}', "
            f"got {len(completes_for_branch)} — run_subgraph()'s own emission "
            f"was not suppressed for ParallelHandler branches "
            f"(support#379 double-emission regression)."
        )
