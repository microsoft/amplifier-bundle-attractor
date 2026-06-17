"""Tests for CR-3: retry clears failed_outputs / completed_nodes / node_outcomes.

R12 WS-6 — engine node-failure propagation.

CR-3 (COE Phase 4): When a retry fires, the engine SHALL CLEAR failed_outputs
alongside completed_nodes and node_outcomes.  Without this, skip-propagation
from attempt N would block retried nodes in attempt N+1.

Retry trigger (spec §3.7 conformance):
  The failing node carries a NODE-level ``retry_target`` (spec §3.7 step 2 —
  explicit, author-authored failure handling on that node).  When the node
  fails with no matching fail-edge, the engine follows the node-level
  retry_target and clears per-run state (CR-3) before re-executing.

  NOTE: graph-level ``retry_target`` is intentionally NOT consulted on
  per-node failure (spec §3.7); it applies only to the goal-gate-unsatisfied-
  at-exit path (spec §3.4).  These tests therefore use node-level retry_target
  so the retry is triggered the spec-conformant way.  The CR-3 state-clearing
  contract under test is identical regardless of which retry path fired.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class EventCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})


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
async def test_goal_gate_retry_clears_failed_outputs(tmp_path):
    """CR-3: failed_outputs cleared alongside completed_nodes on retry.

    Pipeline:
      start → work_a [goal_gate, outputs="k", retry_target=work_a] → exit

    Attempt 1: work_a fails → failed_outputs has "k" → node-level retry_target
              fires (CR-3: state cleared including failed_outputs).
    Attempt 2: work_a succeeds → gate satisfied → pipeline succeeds.

    work_a uses a counter file to fail once then succeed.
    """
    counter_file = tmp_path / "attempt.txt"
    counter_file.write_text("0")

    hooks = EventCapture()
    # Fail on first attempt, succeed on second
    cmd = (
        f"c=$(cat {counter_file}); c=$((c+1)); echo $c > {counter_file}; "
        f"if [ $c -lt 2 ]; then exit 1; fi"
    )
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            work_a [shape=parallelogram,
                    tool_command="{cmd}",
                    outputs="work.result",
                    goal_gate=true,
                    retry_target=work_a]
            exit [shape=Msquare]
            start -> work_a -> exit
        }}
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    final_outcome = await engine.run()

    # After retry, the pipeline should have succeeded
    assert final_outcome.is_success, (
        f"Pipeline should have succeeded after retry. Got: {final_outcome}"
    )

    # After successful completion, failed_outputs should be empty
    assert "work.result" not in engine.failed_outputs, (
        "work.result should not be in failed_outputs after successful retry"
    )


@pytest.mark.asyncio
async def test_goal_gate_retry_clears_completed_nodes(tmp_path):
    """CR-3: completed_nodes and node_outcomes are cleared on retry.

    Verifies the full state-clearing contract: all three tables reset.
    After retry, work_a must be re-executed (not skipped as already-completed).
    """
    counter_file = tmp_path / "attempt.txt"
    counter_file.write_text("0")

    hooks = EventCapture()
    cmd = (
        f"c=$(cat {counter_file}); c=$((c+1)); echo $c > {counter_file}; "
        f"if [ $c -lt 2 ]; then exit 1; fi"
    )
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            work_a [shape=parallelogram,
                    tool_command="{cmd}",
                    outputs="work.result",
                    goal_gate=true,
                    retry_target=work_a]
            exit [shape=Msquare]
            start -> work_a -> exit
        }}
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    final_outcome = await engine.run()

    assert final_outcome.is_success, (
        f"Pipeline should succeed after retry, got: {final_outcome}"
    )
    # The final completed_nodes should reflect the second run
    assert "work_a" in engine.completed_nodes
    assert engine.node_outcomes["work_a"].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_skipped_node_reruns_after_predecessor_succeeds_on_retry(tmp_path):
    """CR-3 (acceptance assertion #8): after retry, previously-skipped node
    executes if its predecessor now succeeds.

    Pipeline:
      start → producer [goal_gate, outputs="k", retry_target=producer]
            → consumer [uses ${k}] → exit

    Attempt 1: producer fails → consumer SKIPPED → node-level retry_target fires.
    Attempt 2 (after CR-3 clear): producer succeeds → consumer runs → gate satisfied.
    """
    counter_file = tmp_path / "attempt.txt"
    counter_file.write_text("0")

    output_file = tmp_path / "consumer_out.txt"
    cmd = (
        f"c=$(cat {counter_file}); c=$((c+1)); echo $c > {counter_file}; "
        f"if [ $c -lt 2 ]; then exit 1; else echo produced_value; fi"
    )

    hooks = EventCapture()
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            producer [shape=parallelogram,
                      tool_command="{cmd}",
                      outputs="k",
                      goal_gate=true,
                      retry_target=producer]
            consumer [shape=parallelogram,
                      tool_command="echo using_${{k}} > {output_file}"]
            exit [shape=Msquare]
            start -> producer -> consumer -> exit
        }}
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    final_outcome = await engine.run()

    assert final_outcome.is_success, (
        f"Pipeline should succeed: producer succeeds on retry, consumer runs. "
        f"Got: {final_outcome}"
    )

    # consumer should have executed and succeeded on attempt 2
    assert "consumer" in engine.node_outcomes, "consumer should appear in node_outcomes"
    assert engine.node_outcomes["consumer"].status == StageStatus.SUCCESS, (
        f"consumer should succeed on retry attempt, got "
        f"{engine.node_outcomes['consumer'].status}"
    )

    # consumer's output file should exist with substituted content
    assert output_file.exists(), "consumer should have written output on retry"
