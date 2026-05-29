"""Tests for M3: PIPELINE_NODE_CONTRACT_VIOLATION event.

R12 WS-6 — engine node-failure propagation.

Design assertion: When a producer node succeeds but declared outputs= keys
were not emitted via context_updates, a PIPELINE_NODE_CONTRACT_VIOLATION
event is emitted.
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
    PIPELINE_NODE_CONTRACT_VIOLATION,
)
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
async def test_contract_violation_when_declared_output_not_emitted(tmp_path):
    """M3: When producer succeeds but declared output not in context_updates,
    PIPELINE_NODE_CONTRACT_VIOLATION is emitted.

    We simulate this by declaring outputs="special.key" on a tool node that
    runs "echo hello" — the node succeeds but never writes "special.key".
    (tool.output and tool.last_line ARE emitted, but special.key is not.)
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer [shape=parallelogram,
                      tool_command="echo hello",
                      outputs="tool.output,tool.last_line,special.key"]
            exit [shape=Msquare]
            start -> producer -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # Producer should have succeeded
    assert engine.node_outcomes["producer"].status == StageStatus.SUCCESS

    # Contract violation event should have been emitted
    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    assert len(violations) == 1, (
        f"Expected 1 contract violation event, got {len(violations)}: {violations}"
    )

    evt = violations[0]
    assert evt["node_id"] == "producer"
    assert "special.key" in evt["missing"]
    # CR-4: taxonomy version
    assert evt.get("failure_mode_taxonomy_version") == 1
    assert evt.get("failure_mode") == "software"

    # tool.output and tool.last_line should be in emitted (they are written)
    assert "tool.output" in evt["emitted"]


@pytest.mark.asyncio
async def test_no_contract_violation_when_all_outputs_emitted(tmp_path):
    """M3: No contract violation when all declared outputs are emitted.

    A tool node that declares only tool.output and tool.last_line (which are
    always emitted by the ToolHandler) should NOT trigger a violation.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer [shape=parallelogram,
                      tool_command="echo hello",
                      outputs="tool.output,tool.last_line"]
            exit [shape=Msquare]
            start -> producer -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    assert len(violations) == 0, f"Expected 0 contract violations but got: {violations}"


@pytest.mark.asyncio
async def test_no_contract_violation_when_no_outputs_declared(tmp_path):
    """M3: Nodes without outputs= have no contract to violate."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            worker [shape=parallelogram, tool_command="echo done"]
            exit [shape=Msquare]
            start -> worker -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    assert len(violations) == 0


@pytest.mark.asyncio
async def test_contract_violation_payload_structure(tmp_path):
    """M3: Contract violation event payload has declared, emitted, missing fields."""
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            producer [shape=parallelogram,
                      tool_command="echo hi",
                      outputs="tool.output,missing_key_1,missing_key_2"]
            exit [shape=Msquare]
            start -> producer -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    assert len(violations) == 1
    evt = violations[0]

    # Check required fields
    assert "node_id" in evt
    assert "declared" in evt
    assert "emitted" in evt
    assert "missing" in evt
    assert "failure_mode" in evt
    assert "failure_mode_taxonomy_version" in evt

    # Check values
    assert set(evt["missing"]) == {"missing_key_1", "missing_key_2"}
    assert "tool.output" in evt["emitted"]


@pytest.mark.asyncio
async def test_no_contract_violation_for_component_node(tmp_path):
    """Fix #2: component-shape nodes must NOT trigger PIPELINE_NODE_CONTRACT_VIOLATION.

    Component nodes (shape=component) emit parallel results via parallel.results in
    context, not via per-node declared outputs.  build_output_table() assigns dynamic
    branch.{idx}.outcome keys to every component node with N outgoing edges.  Before
    the fix, _check_contract_violation fired a false-positive violation event for
    every component node on every successful run because it never writes
    branch.N.outcome keys directly to outcome.context_updates.

    After the fix, the contract check is skipped entirely for nodes with shape=component.
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            fork  [shape=component]
            b0    [shape=parallelogram, tool_command="echo 0"]
            b1    [shape=parallelogram, tool_command="echo 1"]
            b2    [shape=parallelogram, tool_command="echo 2"]
            join  [shape=tripleoctagon]
            exit  [shape=Msquare]
            start -> fork
            fork  -> b0
            fork  -> b1
            fork  -> b2
            b0    -> join
            b1    -> join
            b2    -> join
            join  -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    # The component node 'fork' must NOT emit a contract violation.
    # build_output_table() infers branch.0.outcome, branch.1.outcome, branch.2.outcome
    # for the fork node, but ParallelHandler stores results via parallel.results, not
    # via outcome.context_updates.  This was a spurious false-positive before the fix.
    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    fork_violations = [v for v in violations if v["node_id"] == "fork"]
    assert fork_violations == [], (
        f"component node 'fork' must NOT produce CONTRACT_VIOLATION events; "
        f"got: {fork_violations}"
    )


@pytest.mark.asyncio
async def test_no_contract_violation_for_tool_with_empty_stdout(tmp_path):
    """Fix 4 (R12 R12.5): A tool node with empty stdout must NOT trigger a
    false-positive PIPELINE_NODE_CONTRACT_VIOLATION.

    HANDLER_INFERRED_OUTPUTS["tool"] declares both tool.output and
    tool.last_line. Before the fix, tool.last_line was only emitted when
    stdout was non-empty — silent stdout produced a declared-but-not-emitted
    gap, which _audit_contract_violation flagged as a violation.

    After the fix, tool.last_line is always emitted (as "" on empty stdout),
    so the contract holds and no violation event should fire.

    Mirrors the wait.human discipline fix (R12 commit 7473521).
    """
    hooks = EventCapture()
    engine = _make_engine(
        """
        digraph {
            start [shape=Mdiamond]
            silent [shape=parallelogram,
                    tool_command="true"]
            exit [shape=Msquare]
            start -> silent -> exit
        }
        """,
        logs_root=str(tmp_path),
        hooks=hooks,
    )
    await engine.run()

    assert engine.node_outcomes["silent"].status == StageStatus.SUCCESS

    # The critical assertion: no false-positive contract violation
    violations = hooks.events_of_type(PIPELINE_NODE_CONTRACT_VIOLATION)
    assert len(violations) == 0, (
        f"Empty-stdout tool should NOT produce a CONTRACT_VIOLATION; got: {violations}"
    )
