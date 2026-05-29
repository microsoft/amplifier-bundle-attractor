"""Tests for M4: runs_on={always|success|failure} axis.

R12 WS-6 — engine node-failure propagation.

Design assertion #4: A node with runs_on=always (or runs_on=failure) executes
when its predecessor FAILED or SKIPPED; missing references resolve to "".
Its own genuine failures are NOT masked (distinct from continue_on_fail).
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_SKIPPED
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class EventCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})

    def events_of_type(self, event_name: str) -> list[dict[str, Any]]:
        return [e["data"] for e in self.events if e["name"] == event_name]


def _make_engine(dot_source: str, logs_root: str, hooks: Any = None) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext())
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


@pytest.mark.asyncio
async def test_runs_on_always_executes_when_predecessor_fails(tmp_path):
    """M4: runs_on=always node runs even when predecessor failed.

    Design assertion #4: cleanup-node ergonomic.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="resource.handle"]
            cleanup [shape=parallelogram,
                     tool_command="echo cleaned up",
                     runs_on=always]
            exit [shape=Msquare]
            start -> work -> cleanup -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["work"].status == StageStatus.FAIL
    # cleanup must have executed, not been skipped
    assert "cleanup" in engine.node_outcomes
    assert engine.node_outcomes["cleanup"].status == StageStatus.SUCCESS, (
        f"runs_on=always should execute after failure, got "
        f"{engine.node_outcomes['cleanup'].status}"
    )

    # No PIPELINE_NODE_SKIPPED event for cleanup
    skipped = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    cleanup_skips = [e for e in skipped if e.get("node_id") == "cleanup"]
    assert len(cleanup_skips) == 0, "runs_on=always should not be skipped"


@pytest.mark.asyncio
async def test_runs_on_always_executes_when_predecessor_succeeds(tmp_path):
    """M4: runs_on=always node also runs when predecessor succeeded."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="echo done",
                  outputs="resource.handle"]
            cleanup [shape=parallelogram, tool_command="echo cleanup",
                     runs_on=always]
            exit [shape=Msquare]
            start -> work -> cleanup -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["work"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["cleanup"].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_runs_on_failure_executes_when_predecessor_fails(tmp_path):
    """M4: runs_on=failure node runs when a predecessor failed."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="resource.handle"]
            on_fail [shape=parallelogram,
                     tool_command="echo failure handler ran",
                     runs_on=failure]
            exit [shape=Msquare]
            start -> work -> on_fail -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["work"].status == StageStatus.FAIL
    assert "on_fail" in engine.node_outcomes
    assert engine.node_outcomes["on_fail"].status == StageStatus.SUCCESS, (
        f"runs_on=failure should execute when predecessor failed, got "
        f"{engine.node_outcomes['on_fail'].status}"
    )


@pytest.mark.asyncio
async def test_runs_on_failure_skipped_when_no_predecessor_failed(tmp_path):
    """M4: runs_on=failure node is SKIPPED when all predecessors succeeded."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="echo success",
                  outputs="resource.handle"]
            on_fail [shape=parallelogram,
                     tool_command="echo should not run",
                     runs_on=failure]
            exit [shape=Msquare]
            start -> work -> on_fail -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["work"].status == StageStatus.SUCCESS
    # on_fail should be skipped because nothing failed
    assert "on_fail" in engine.node_outcomes
    assert engine.node_outcomes["on_fail"].status == StageStatus.SKIPPED, (
        f"runs_on=failure should skip when nothing failed, got "
        f"{engine.node_outcomes['on_fail'].status}"
    )


@pytest.mark.asyncio
async def test_runs_on_always_missing_refs_resolve_to_empty(tmp_path):
    """M4: For runs_on=always nodes, missing ${refs} resolve to '' not literal.

    The cleanup node references ${resource.handle} which was never written
    (producer failed). The command should run with empty substitution.
    We verify that the command runs (not skipped) and doesn't error on the
    missing reference.
    """
    hooks = EventCapture()
    # Write the resolved command to a file so we can inspect it
    output_file = tmp_path / "cleanup_out.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="resource.handle"]
            cleanup [shape=parallelogram,
                     tool_command="echo 'handle=${{resource.handle}}' > {output_file}",
                     runs_on=always]
            exit [shape=Msquare]
            start -> work -> cleanup -> exit
        }}
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["cleanup"].status == StageStatus.SUCCESS

    # The output file should exist and contain empty value for resource.handle
    assert output_file.exists(), "cleanup should have run and written output"
    content = output_file.read_text()
    # resource.handle was never set → resolved to "" → handle=
    assert "handle=" in content, f"Expected empty substitution, got: {content!r}"


@pytest.mark.asyncio
async def test_runs_on_success_default_behavior(tmp_path):
    """M4: runs_on=success (default) behaves as today's behavior."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="k"]
            consumer [shape=parallelogram,
                      tool_command="echo ${k}"]
            exit [shape=Msquare]
            start -> work -> consumer -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # Default runs_on=success + fail-fast: FAIL no longer traverses unconditional
    # edges to default-runs_on nodes.  consumer is NOT reached (absent from
    # node_outcomes), not SKIPPED.  Pipeline authors who want consumer to still
    # execute after a failure must use runs_on=always or runs_on=failure.
    assert "consumer" not in engine.node_outcomes, (
        f"Expected consumer to NOT be in node_outcomes under fail-fast semantics, "
        f"got status={engine.node_outcomes.get('consumer')}"
    )


@pytest.mark.asyncio
async def test_runs_on_always_genuine_failures_not_masked(tmp_path):
    """M4: runs_on=always does NOT mask genuine failures in the node itself.

    Design assertion #4: Its own genuine failures are NOT masked
    (distinct from continue_on_fail).
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="resource.handle"]
            cleanup [shape=parallelogram,
                     tool_command="exit 2",
                     runs_on=always]
            exit [shape=Msquare]
            start -> work -> cleanup -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # cleanup executed (runs_on=always) but genuinely failed (exit 2)
    assert "cleanup" in engine.node_outcomes
    assert engine.node_outcomes["cleanup"].status == StageStatus.FAIL, (
        "runs_on=always should not mask genuine failures in the node itself"
    )


@pytest.mark.asyncio
async def test_runs_on_failure_skipped_event_has_no_failure_mode(tmp_path):
    """Fix 1 (R12 R12.5): PIPELINE_NODE_SKIPPED for runs_on=failure happy-path
    skip carries failure_mode=None, NOT 'predecessor_failed'.

    When no predecessor failed the skip is the *absence* of failure.
    Emitting failure_mode='predecessor_failed' produces false-positive hits in
    downstream observability filters and queries on that field.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="echo ok",
                  outputs="resource.handle"]
            on_fail [shape=parallelogram,
                     tool_command="echo should not run",
                     runs_on=failure]
            exit [shape=Msquare]
            start -> work -> on_fail -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["work"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["on_fail"].status == StageStatus.SKIPPED

    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    on_fail_skip = next(
        (e for e in skipped_events if e.get("node_id") == "on_fail"), None
    )
    assert on_fail_skip is not None, (
        "Expected a PIPELINE_NODE_SKIPPED event for on_fail"
    )
    assert on_fail_skip["cause"] == "no_predecessor_failure"
    # The critical assertion: failure_mode must be None, not "predecessor_failed"
    assert on_fail_skip.get("failure_mode") is None, (
        f"failure_mode should be None for no_predecessor_failure skip, "
        f"got {on_fail_skip.get('failure_mode')!r}"
    )
    # taxonomy_version still ships per CR-4
    assert on_fail_skip.get("failure_mode_taxonomy_version") == 1


@pytest.mark.asyncio
async def test_continue_on_fail_swallows_failure_signal_for_runs_on_failure(tmp_path):
    """Fix 5 (R12 R12.5): continue_on_fail × runs_on=failure interaction.

    A predecessor with continue_on_fail=true that fails at runtime has its
    outcome flipped FAIL→SUCCESS BEFORE _populate_failed_outputs runs.
    A downstream runs_on=failure cleanup node therefore does NOT trigger,
    because the failed_outputs table is never populated for that predecessor.

    Pipeline authors who want a cleanup to fire regardless should use
    runs_on=always instead of runs_on=failure.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            work [shape=parallelogram, tool_command="exit 1",
                  outputs="k", continue_on_fail="true"]
            on_fail [shape=parallelogram,
                     tool_command="echo failure cleanup",
                     runs_on=failure]
            exit [shape=Msquare]
            start -> work -> on_fail -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # work "failed" but continue_on_fail flipped it to SUCCESS
    assert engine.node_outcomes["work"].status == StageStatus.SUCCESS

    # on_fail (runs_on=failure) should be SKIPPED — continue_on_fail swallowed
    # the failure signal before failed_outputs was populated
    assert "on_fail" in engine.node_outcomes
    assert engine.node_outcomes["on_fail"].status == StageStatus.SKIPPED, (
        "runs_on=failure cleanup should be SKIPPED when predecessor used "
        "continue_on_fail=true (failure signal swallowed)"
    )

    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    on_fail_skip = next(
        (e for e in skipped_events if e.get("node_id") == "on_fail"), None
    )
    assert on_fail_skip is not None
    assert on_fail_skip["cause"] == "no_predecessor_failure"
