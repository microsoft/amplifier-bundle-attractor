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
    """Design assertion #1: node with outputs= that fails causes SKIPPED successor.

    Fixture pipeline (placeholder names, no production names):
      start → producer_a [outputs="resource.handle"] → consumer_b [tool_command="use ${resource.handle}"] → exit

    producer_a fails (exit 1); consumer_b references ${resource.handle}.
    Expected: consumer_b outcome = SKIPPED; its handler is NOT invoked.
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

    # consumer_b must be SKIPPED
    assert "consumer_b" in engine.node_outcomes
    assert engine.node_outcomes["consumer_b"].status == StageStatus.SKIPPED, (
        f"Expected consumer_b SKIPPED, got {engine.node_outcomes['consumer_b'].status}"
    )

    # resource.handle must be in failed_outputs
    assert "resource.handle" in engine.failed_outputs
    assert engine.failed_outputs["resource.handle"] == "producer_a"


@pytest.mark.asyncio
async def test_skipped_node_emits_pipeline_node_skipped_event(tmp_path):
    """Design assertion #2: exactly one PIPELINE_NODE_SKIPPED per skipped node.

    Also verifies CR-4: failure_mode_taxonomy_version=1 in every event.
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

    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    # Exactly one SKIPPED event for consumer_b
    assert len(skipped_events) == 1
    evt = skipped_events[0]
    assert evt["node_id"] == "consumer_b"
    assert evt["cause"] == "predecessor_failed"
    assert "resource.handle" in evt["missing_keys"]
    # CR-4: taxonomy version must be present
    assert evt.get("failure_mode_taxonomy_version") == 1
    assert evt.get("failure_mode") == "predecessor_failed"


@pytest.mark.asyncio
async def test_skip_propagates_transitively(tmp_path):
    """Design assertion #1 (transitive): A→B→C where A fails; B is skipped;
    C references B's output and should ALSO be skipped.
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
    assert engine.node_outcomes["node_b"].status == StageStatus.SKIPPED
    assert engine.node_outcomes["node_c"].status == StageStatus.SKIPPED

    # All declared outputs propagated
    assert "a.result" in engine.failed_outputs
    assert "b.result" in engine.failed_outputs

    # Two skipped events
    skipped_events = hooks.events_of_type(PIPELINE_NODE_SKIPPED)
    assert len(skipped_events) == 2
    skipped_node_ids = {e["node_id"] for e in skipped_events}
    assert skipped_node_ids == {"node_b", "node_c"}


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
    """M2: When a node is SKIPPED, its handler must NOT be invoked.

    consumer_b references ${resource.handle} (which is in failed_outputs after
    producer_a fails).  If consumer_b's handler ran, it would echo "ran_marker"
    as the last line, setting tool.last_line = "ran_marker".  Since it is
    SKIPPED, tool.last_line should NOT be "ran_marker".
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

    assert engine.node_outcomes["consumer_b"].status == StageStatus.SKIPPED, (
        f"consumer_b should be SKIPPED, got {engine.node_outcomes['consumer_b'].status}"
    )
    # tool.last_line should NOT be "ran_marker" if handler was skipped
    assert engine.context.get("tool.last_line") != "ran_marker", (
        "consumer_b's handler should NOT have run (node was SKIPPED); "
        f"but tool.last_line = {engine.context.get('tool.last_line')!r}"
    )
