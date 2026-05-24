"""Tests for M2 + M3: eager reference scan, PIPELINE_NODE_SKIPPED event.

R12 WS-6 — engine node-failure propagation.

Design assertion #1: Failed predecessor → skipped successor.
Design assertion #2: Every skip emits exactly one PIPELINE_NODE_SKIPPED event.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_NODE_SKIPPED,
)
from amplifier_module_loop_pipeline.substitution import extract_refs
from amplifier_module_loop_pipeline.validation import validate_or_raise


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class EventCapture:
    """Minimal hooks object that captures emitted events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})

    def events_of_type(self, event_name: str) -> list[dict[str, Any]]:
        return [e["data"] for e in self.events if e["name"] == event_name]


def _make_engine(
    dot_source: str,
    logs_root: str,
    hooks: Any = None,
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry()
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Tests for extract_refs (substitution module)
# ---------------------------------------------------------------------------


def test_extract_refs_brace_form():
    """extract_refs captures ${key} tokens."""
    refs = extract_refs("curl ${server.url}/path")
    assert "server.url" in refs


def test_extract_refs_bare_form():
    """extract_refs captures $key tokens."""
    refs = extract_refs("$api.key is needed")
    assert "api.key" in refs


def test_extract_refs_mixed():
    """extract_refs handles both forms in one string."""
    refs = extract_refs("${tool.output} and $plain_key")
    assert "tool.output" in refs
    assert "plain_key" in refs


def test_extract_refs_empty():
    """extract_refs returns empty set for text without $."""
    assert extract_refs("no refs here") == set()
    assert extract_refs("") == set()


def test_extract_refs_double_dollar_ignored():
    """extract_refs does not include $$ escape as a ref."""
    refs = extract_refs("literal $$ sign")
    assert not refs  # $$ should not create a ref


# ---------------------------------------------------------------------------
# Tests for M2/M3: skip propagation via engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_predecessor_causes_skipped_successor(tmp_path):
    """Design assertion #1: when a predecessor fails, its successors are not reached.

    Fixture pipeline (placeholder names, no production names):
      start → producer_a [outputs="resource.handle"] → consumer_b [tool_command="use ${resource.handle}"] → exit

    producer_a fails (exit 1).  Under fail-fast semantics (spec §3.7), the engine
    does not traverse the unconditional edge to consumer_b — consumer_b is absent
    from node_outcomes, not merely SKIPPED.

    Pipeline authors who want consumer_b to run on failure should use one of the
    explicit opt-in mechanisms: runs_on=always / runs_on=failure on consumer_b,
    or a condition="outcome=fail" edge from producer_a.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram,
                        tool_command="exit 1",
                        outputs="resource.handle"]
            consumer_b [shape=parallelogram,
                        tool_command="echo using ${resource.handle}"]
            exit [shape=Msquare]
            start -> producer_a
            producer_a -> consumer_b
            consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # producer_a must have failed
    assert engine.node_outcomes["producer_a"].status == StageStatus.FAIL

    # consumer_b must NOT be in node_outcomes — fail-fast halts at producer_a
    assert "consumer_b" not in engine.node_outcomes, (
        "Under fail-fast semantics, consumer_b must not be reached "
        f"(got status={engine.node_outcomes.get('consumer_b')})"
    )

    # resource.handle must be in failed_outputs (populated when producer_a fails)
    assert "resource.handle" in engine.failed_outputs
    assert engine.failed_outputs["resource.handle"] == "producer_a"


@pytest.mark.asyncio
async def test_skipped_node_emits_pipeline_node_skipped_event(tmp_path):
    """Fail-fast behavior: FAIL halts the pipeline; downstream nodes are NOT reached.

    Under fail-fast semantics (spec §3.7), after producer_a fails the engine does
    not traverse the unconditional edge to consumer_b.  No PIPELINE_NODE_SKIPPED
    events are emitted — consumer_b is absent from node_outcomes entirely.

    Pipeline authors who need cleanup to run after failure should use
    runs_on=always or runs_on=failure on the cleanup node, or add an explicit
    condition="outcome=fail" edge from producer_a.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram,
                        tool_command="exit 1",
                        outputs="resource.handle"]
            consumer_b [shape=parallelogram,
                        tool_command="echo ${resource.handle}",
                        outputs="consumer.result"]
            exit [shape=Msquare]
            start -> producer_a -> consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # producer_a failed
    assert engine.node_outcomes["producer_a"].status == StageStatus.FAIL

    # consumer_b is NOT in node_outcomes — the engine halted at producer_a
    assert "consumer_b" not in engine.node_outcomes, (
        "Under fail-fast semantics consumer_b must not be reached; "
        f"got {engine.node_outcomes.get('consumer_b')}"
    )

    # No PIPELINE_NODE_SKIPPED events — consumer_b was never visited
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 0, (
        f"Expected 0 PIPELINE_NODE_SKIPPED events under fail-fast, "
        f"got {len(skipped_events)}: {skipped_events}"
    )


@pytest.mark.asyncio
async def test_skip_propagates_transitively(tmp_path):
    """Fail-fast: A→B→C where A fails; the engine halts at A (not B or C).

    Under fail-fast semantics (spec §3.7), FAIL does not traverse unconditional
    edges to default runs_on=success nodes.  The pipeline halts at node_a.
    Neither node_b nor node_c is visited (both absent from node_outcomes).

    To propagate execution past node_a failure, use condition="outcome=fail"
    edges or runs_on=always / runs_on=failure on node_b.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            node_a [shape=parallelogram, tool_command="exit 1",
                    outputs="a.result"]
            node_b [shape=parallelogram, tool_command="echo ${a.result}",
                    outputs="b.result"]
            node_c [shape=parallelogram, tool_command="echo ${b.result}"]
            exit [shape=Msquare]
            start -> node_a -> node_b -> node_c -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["node_a"].status == StageStatus.FAIL

    # Pipeline halted at node_a — node_b and node_c are NOT in node_outcomes
    assert "node_b" not in engine.node_outcomes, (
        f"node_b should not be reached under fail-fast, "
        f"got {engine.node_outcomes.get('node_b')}"
    )
    assert "node_c" not in engine.node_outcomes, (
        f"node_c should not be reached under fail-fast, "
        f"got {engine.node_outcomes.get('node_c')}"
    )

    # a.result is still in failed_outputs (populated when node_a fails)
    assert "a.result" in engine.failed_outputs

    # No PIPELINE_NODE_SKIPPED events — no nodes were visited after node_a
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 0, (
        f"Expected 0 PIPELINE_NODE_SKIPPED events, got {len(skipped_events)}"
    )


@pytest.mark.asyncio
async def test_skip_not_triggered_for_unrelated_references(tmp_path):
    """M2: A node whose references are NOT in failed_outputs executes normally.

    pipeline: A (succeeds) → B (references a.result); B should execute.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            node_a [shape=parallelogram, tool_command="echo success",
                    outputs="a.result"]
            node_b [shape=parallelogram, tool_command="echo hello"]
            exit [shape=Msquare]
            start -> node_a -> node_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # Nothing should be skipped
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 0

    assert engine.node_outcomes["node_a"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["node_b"].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_handler_not_invoked_on_skip(tmp_path):
    """Fail-fast: when a predecessor fails, its successor's handler is NOT invoked.

    consumer_b references ${resource.handle} (from producer_a which fails).
    Under fail-fast semantics (spec §3.7), the engine halts at producer_a and
    never visits consumer_b.  consumer_b's handler is therefore NOT invoked —
    tool.last_line will NOT be "ran_marker".

    This verifies the behavioral guarantee: handlers of unreachable successors
    are never invoked, regardless of whether the engine halts (fail-fast) or
    skips (legacy behavior).
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer_a [shape=parallelogram, tool_command="exit 1",
                        outputs="resource.handle"]
            consumer_b [shape=parallelogram,
                        tool_command="echo using ${resource.handle}; echo ran_marker"]
            exit [shape=Msquare]
            start -> producer_a -> consumer_b -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # Under fail-fast, consumer_b is never reached — not in node_outcomes
    assert "consumer_b" not in engine.node_outcomes, (
        f"consumer_b should not be in node_outcomes under fail-fast, "
        f"got {engine.node_outcomes.get('consumer_b')}"
    )
    # Core guarantee: handler was NOT invoked — "ran_marker" was not written
    assert engine.context.get("tool.last_line") != "ran_marker", (
        "consumer_b's handler should NOT have run; "
        f"but tool.last_line = {engine.context.get('tool.last_line')!r}"
    )
