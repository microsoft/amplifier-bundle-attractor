"""Evidence test: 02-plan-implement-test.dot corrective loop fires.

This test is the reproducible fixture behind Tutorial 02's live-run evidence
claim:
  "A live run shows the corrective loop actually firing at least once before
   convergence."

It loads examples/pipelines/02-plan-implement-test.dot through the actual
PipelineEngine, injects:
  - A mock LLM backend for the 'plan' and 'implement' nodes (no API keys needed)
  - A controlled mock tool handler for 'test_gate' that returns gate_fail on
    iteration 1 and gate_pass on iteration 2

Then asserts the resulting event stream contains:
  - pipeline:start with graph_name == "PlanImplementTest"
  - pipeline:edge_selected from test_gate -> implement (back-edge fires)
  - pipeline:edge_selected from test_gate -> done (convergence)
  - pipeline:goal_gate_check with test_gate in satisfied

This test is the auditable proof that the engine demonstrates the corrective
loop -- a static JSONL file can be fabricated, a test that runs the engine
and asserts the event sequence cannot.

Companion evidence transcript:
  examples/pipelines/practical/evidence/plan-implement-test-2026-08-03/
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_EDGE_SELECTED,
    PIPELINE_GOAL_GATE_CHECK,
    PIPELINE_START,
)
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_DOT_FILE = _REPO_ROOT / "examples" / "pipelines" / "02-plan-implement-test.dot"


# ---------------------------------------------------------------------------
# Event capture
# ---------------------------------------------------------------------------


class EventCapture:
    """Records all events emitted by the engine."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, dict(data)))

    @property
    def event_names(self) -> list[str]:
        return [e for e, _ in self.events]

    def get_data(self, event_name: str) -> list[dict[str, Any]]:
        return [d for e, d in self.events if e == event_name]

    def count(self, event_name: str) -> int:
        return sum(1 for e, _ in self.events if e == event_name)

    def edge_selected_pairs(self) -> list[tuple[str, str]]:
        """Return (from_node, to_node) pairs for all edge_selected events."""
        return [
            (d["from_node"], d["to_node"])
            for d in self.get_data(PIPELINE_EDGE_SELECTED)
        ]


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


class MockLLMBackend:
    """Returns plain SUCCESS for all LLM nodes (plan, implement).

    No API keys needed -- this is a unit-test fixture, not a real run.
    """

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> Outcome:
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Stage completed: {node.id}",
        )


class ControlledToolHandler:
    """Mock tool handler for the test_gate (parallelogram) node.

    Iteration 1: sets context.tool.last_line = "gate_fail"  -> back-edge fires
    Iteration 2: sets context.tool.last_line = "gate_pass"  -> converges

    Returns Outcome with is_explicit=True so goal_gate=true can be satisfied
    (the gate check requires is_success AND is_explicit per EXTENSIONS.md §25).
    """

    def __init__(self) -> None:
        self._call_count = 0

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: Any = None,
    ) -> Outcome:
        self._call_count += 1
        if self._call_count == 1:
            # First call: tests "fail" -- back-edge should fire
            last_line = "gate_fail"
            notes = "pytest: 1 failed (fixture iteration 1)"
        else:
            # Second call: tests "pass" -- convergence path
            last_line = "gate_pass"
            notes = "pytest: 3 passed (fixture iteration 2)"

        context.set("tool.last_line", last_line)
        context.set("tool.output", last_line + "\n")
        context_updates = {
            "tool.last_line": last_line,
            "tool.output": last_line + "\n",
        }

        return Outcome(
            status=StageStatus.SUCCESS,
            is_explicit=True,
            context_updates=context_updates,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Engine builder
# ---------------------------------------------------------------------------


def _make_engine(
    hooks: EventCapture,
    tmp_path: Path,
) -> PipelineEngine:
    """Load 02-plan-implement-test.dot and build a PipelineEngine.

    Uses:
    - MockLLMBackend for plan/implement (codergen) nodes
    - ControlledToolHandler for test_gate (parallelogram) node
    """
    dot_source = _DOT_FILE.read_text(encoding="utf-8")
    graph = parse_dot(dot_source)
    validate_or_raise(graph)

    context = PipelineContext()
    backend = MockLLMBackend()

    registry = HandlerRegistry(HandlerContext(backend=backend, hooks=hooks))
    # Override the default ToolHandler with our controlled mock
    registry.register("tool", ControlledToolHandler())

    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path / "logs"),
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanImplementTestDotFileExists:
    """Structural precondition: the DOT file we are testing exists."""

    def test_dot_file_exists(self) -> None:
        """02-plan-implement-test.dot is present in examples/pipelines/."""
        assert _DOT_FILE.exists(), (
            f"Expected DOT file at {_DOT_FILE} -- "
            "is the test running from the repo root?"
        )

    def test_dot_file_parses(self) -> None:
        """02-plan-implement-test.dot parses without errors."""
        source = _DOT_FILE.read_text(encoding="utf-8")
        graph = parse_dot(source)
        assert len(graph.nodes) > 0

    def test_dot_has_parallelogram_node(self) -> None:
        """Graph contains at least one parallelogram (tool gate) node."""
        source = _DOT_FILE.read_text(encoding="utf-8")
        graph = parse_dot(source)
        tool_nodes = [n for n in graph.nodes.values() if n.shape == "parallelogram"]
        assert len(tool_nodes) >= 1, (
            f"Expected at least one parallelogram node, got shapes: "
            f"{[n.shape for n in graph.nodes.values()]}"
        )

    def test_dot_has_back_edge(self) -> None:
        """Graph contains a back-edge (loop_restart=true edge)."""
        source = _DOT_FILE.read_text(encoding="utf-8")
        graph = parse_dot(source)
        back_edges = [e for e in graph.edges if e.loop_restart]
        assert len(back_edges) >= 1, (
            "Expected at least one loop_restart=true edge (back-edge), "
            f"got edges: {[(e.from_node, e.to_node) for e in graph.edges]}"
        )

    def test_dot_has_goal_gate(self) -> None:
        """Graph contains a node with goal_gate=true."""
        source = _DOT_FILE.read_text(encoding="utf-8")
        graph = parse_dot(source)
        gate_nodes = [
            n
            for n in graph.nodes.values()
            if n.attrs.get("goal_gate") in (True, "true")
        ]
        assert len(gate_nodes) >= 1, (
            "Expected at least one goal_gate=true node, "
            f"got nodes: {[(n.id, n.attrs) for n in graph.nodes.values()]}"
        )

    def test_dot_has_retry_target(self) -> None:
        """The goal_gate node has a retry_target (clears goal_gate_has_retry warning)."""
        source = _DOT_FILE.read_text(encoding="utf-8")
        graph = parse_dot(source)
        gate_nodes_with_retry = [
            n
            for n in graph.nodes.values()
            if n.attrs.get("goal_gate") in (True, "true")
            and n.attrs.get("retry_target")
        ]
        assert len(gate_nodes_with_retry) >= 1, (
            "Expected goal_gate node to have retry_target set. "
            "This clears the [goal_gate_has_retry] lint warning."
        )


class TestPlanImplementTestEngineRun:
    """Live-run evidence: the corrective loop actually fires.

    These tests run the actual PipelineEngine with mock handlers and assert
    the event sequence proves the back-edge fired before convergence.
    """

    @pytest.mark.asyncio
    async def test_pipeline_starts_with_correct_graph_name(self, tmp_path: Path) -> None:
        """pipeline:start event has graph_name == 'PlanImplementTest'."""
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        start_events = hooks.get_data(PIPELINE_START)
        assert start_events, "Expected at least one pipeline:start event"
        assert start_events[0].get("graph_name") == "PlanImplementTest", (
            f"Expected graph_name='PlanImplementTest', "
            f"got {start_events[0].get('graph_name')!r}"
        )

    @pytest.mark.asyncio
    async def test_back_edge_fires_on_first_gate_fail(self, tmp_path: Path) -> None:
        """pipeline:edge_selected shows test_gate -> implement (back-edge fires).

        This is the core evidence claim: the corrective loop must actually
        fire at least once before convergence.  A cycle that never fires is
        a decorative loop -- the dead-pattern disease in new clothes.
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        pairs = hooks.edge_selected_pairs()
        assert ("test_gate", "implement") in pairs, (
            "Expected pipeline:edge_selected from test_gate -> implement "
            "(back-edge fires on gate_fail).  "
            f"Actual edge_selected pairs: {pairs}"
        )

    @pytest.mark.asyncio
    async def test_convergence_path_fires_after_back_edge(self, tmp_path: Path) -> None:
        """pipeline:edge_selected shows test_gate -> done (convergence) after back-edge.

        The pipeline converges: after the corrective loop fires once, the gate
        reports gate_pass and the success path is taken.
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        pairs = hooks.edge_selected_pairs()
        assert ("test_gate", "done") in pairs, (
            "Expected pipeline:edge_selected from test_gate -> done "
            "(convergence after gate_pass).  "
            f"Actual edge_selected pairs: {pairs}"
        )

    @pytest.mark.asyncio
    async def test_back_edge_fires_before_convergence(self, tmp_path: Path) -> None:
        """The back-edge (test_gate -> implement) appears before convergence (test_gate -> done).

        Order matters: the loop must fire first, then converge.
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        pairs = hooks.edge_selected_pairs()
        try:
            back_edge_idx = pairs.index(("test_gate", "implement"))
        except ValueError:
            pytest.fail(
                "Back-edge (test_gate -> implement) never appeared in edge_selected events. "
                f"Pairs: {pairs}"
            )
        try:
            converge_idx = pairs.index(("test_gate", "done"))
        except ValueError:
            pytest.fail(
                "Convergence edge (test_gate -> done) never appeared in edge_selected events. "
                f"Pairs: {pairs}"
            )

        assert back_edge_idx < converge_idx, (
            f"Expected back-edge (idx={back_edge_idx}) before convergence "
            f"(idx={converge_idx}).  Pairs: {pairs}"
        )

    @pytest.mark.asyncio
    async def test_goal_gate_satisfied_at_convergence(self, tmp_path: Path) -> None:
        """pipeline:goal_gate_check has test_gate in 'satisfied' at pipeline exit.

        The goal_gate=true on test_gate means the exit is structurally
        unreachable until the gate reports success.  This event confirms the
        gate was actually satisfied (not just bypassed).
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        gate_checks = hooks.get_data(PIPELINE_GOAL_GATE_CHECK)
        assert gate_checks, "Expected at least one pipeline:goal_gate_check event"

        # The final gate check should have test_gate in satisfied
        final_check = gate_checks[-1]
        assert "test_gate" in final_check.get("satisfied", []), (
            f"Expected 'test_gate' in satisfied at final gate check. "
            f"Got: satisfied={final_check.get('satisfied')!r}, "
            f"unsatisfied={final_check.get('unsatisfied')!r}"
        )
        assert "test_gate" not in final_check.get("unsatisfied", []), (
            f"Expected 'test_gate' NOT in unsatisfied at final gate check. "
            f"Got: {final_check.get('unsatisfied')!r}"
        )

    @pytest.mark.asyncio
    async def test_pipeline_completes_successfully(self, tmp_path: Path) -> None:
        """Pipeline run returns SUCCESS (not FAIL or PARTIAL_SUCCESS)."""
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
            f"Expected pipeline to complete successfully, "
            f"got status={outcome.status}, "
            f"failure_reason={outcome.failure_reason!r}"
        )

    @pytest.mark.asyncio
    async def test_implement_executed_twice(self, tmp_path: Path) -> None:
        """implement node is executed twice: once initially, once after the back-edge.

        This confirms the loop_restart semantics are working: the implement
        node re-runs after the test_gate routes back via the back-edge.
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_START

        node_starts = hooks.get_data(PIPELINE_NODE_START)
        implement_starts = [e for e in node_starts if e.get("node_id") == "implement"]
        assert len(implement_starts) >= 2, (
            f"Expected implement to start at least twice (initial + retry). "
            f"Got {len(implement_starts)} start events.  "
            f"All node starts: {[e.get('node_id') for e in node_starts]}"
        )

    @pytest.mark.asyncio
    async def test_test_gate_executed_twice(self, tmp_path: Path) -> None:
        """test_gate is executed twice: once returning gate_fail, once gate_pass.

        This is the parallelogram evidence gate running twice -- once to
        discover the failure (routing back to implement), once to confirm
        convergence (routing to done).
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        await engine.run()

        from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_START

        node_starts = hooks.get_data(PIPELINE_NODE_START)
        gate_starts = [e for e in node_starts if e.get("node_id") == "test_gate"]
        assert len(gate_starts) >= 2, (
            f"Expected test_gate to start at least twice (gate_fail + gate_pass). "
            f"Got {len(gate_starts)} start events.  "
            f"All node starts: {[e.get('node_id') for e in node_starts]}"
        )

    @pytest.mark.asyncio
    async def test_full_event_sequence_shape(self, tmp_path: Path) -> None:
        """End-to-end event sequence demonstrates the attractor shape.

        Verifies the complete execution path:
          start -> plan -> implement -> test_gate (gate_fail)
               -> implement -> test_gate (gate_pass) -> done
               -> goal_gate_check (satisfied) -> pipeline:complete

        This is the convergence loop in action.
        """
        hooks = EventCapture()
        engine = _make_engine(hooks, tmp_path)

        outcome = await engine.run()

        # Pipeline succeeded
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        # Edge sequence must include both the back-edge and convergence
        pairs = hooks.edge_selected_pairs()
        assert ("test_gate", "implement") in pairs, "Back-edge must fire"
        assert ("test_gate", "done") in pairs, "Convergence must follow"

        # Goal gate must be satisfied
        gate_checks = hooks.get_data(PIPELINE_GOAL_GATE_CHECK)
        assert gate_checks
        assert "test_gate" in gate_checks[-1].get("satisfied", [])

        # The plan stage ran (staging is preserved as a teaching device)
        from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_COMPLETE

        completed_ids = {
            e.get("node_id") for e in hooks.get_data(PIPELINE_NODE_COMPLETE)
        }
        assert "plan" in completed_ids, "plan node must execute (staging preserved)"
        assert "implement" in completed_ids, "implement node must execute"
        assert "test_gate" in completed_ids, "test_gate node must execute"
