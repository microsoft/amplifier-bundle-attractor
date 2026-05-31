"""Pipeline end-to-end tests (Phase 7, Task 7.3).

Spec coverage: Section 11.3, 11.4, 11.12, 11.13.

End-to-end tests that exercise the full pipeline stack:
    parse_dot → validate → PipelineEngine.run() → handler execution
    → edge selection → context propagation → checkpoint → events

Uses 3 DOT fixture files:
- simple_linear.dot: Start → Implement → Validate → Exit
- conditional_branch.dot: Start → Implement → Test → Gate (success/fail) → Exit
- goal_gate.dot: Start → Implement (goal_gate=true) → Review → Exit
"""

import json
import os

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_CHECKPOINT,
    PIPELINE_COMPLETE,
    PIPELINE_EDGE_SELECTED,
    PIPELINE_GOAL_GATE_CHECK,
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_START,
    PIPELINE_START,
)
from amplifier_module_loop_pipeline.validation import validate, validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    """Load a DOT fixture file by name."""
    path = os.path.join(FIXTURES_DIR, name)
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


class SuccessBackend:
    """Backend that returns SUCCESS for every node."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        self.calls.append(node.id)
        self.prompts[node.id] = prompt
        return f"Completed: {node.id}"


class OutcomeBackend:
    """Backend that returns pre-configured outcomes per node ID.

    If an outcome is not configured for a node, returns SUCCESS.
    Supports a call counter for nodes that change behavior on retry.
    """

    def __init__(self, outcomes: dict[str, Outcome | str]) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []
        self.call_counts: dict[str, int] = {}

    async def run(
        self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
    ) -> str | Outcome:
        self.calls.append(node.id)
        self.call_counts[node.id] = self.call_counts.get(node.id, 0) + 1
        result = self._outcomes.get(node.id, "ok")
        return result


class RetryThenSucceedBackend:
    """Backend that fails N times for a node then succeeds.

    Used to test goal gate retry behavior.
    """

    def __init__(self, fail_node: str, fail_count: int = 1) -> None:
        self._fail_node = fail_node
        self._fail_count = fail_count
        self.calls: list[str] = []
        self._node_attempts: dict[str, int] = {}

    async def run(
        self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
    ) -> str | Outcome:
        self.calls.append(node.id)
        self._node_attempts[node.id] = self._node_attempts.get(node.id, 0) + 1

        if node.id == self._fail_node:
            if self._node_attempts[node.id] <= self._fail_count:
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Attempt {self._node_attempts[node.id]} failed",
                )
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Completed: {node.id}",
        )


# ---------------------------------------------------------------------------
# Event recorder
# ---------------------------------------------------------------------------


class EventRecorder:
    """Records pipeline events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    @property
    def event_names(self) -> list[str]:
        return [e[0] for e in self.events]

    def get_data(self, event_name: str) -> list[dict]:
        return [e[1] for e in self.events if e[0] == event_name]


# ---------------------------------------------------------------------------
# Helper to build engines from fixtures
# ---------------------------------------------------------------------------


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-pipeline-e2e",
    hooks: object | None = None,
) -> PipelineEngine:
    """Parse DOT, validate, build engine."""
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ===========================================================================
# Test Suite 1: simple_linear.dot
# Start → Implement → Validate → Exit
# ===========================================================================


class TestSimpleLinear:
    """Tests using the simple_linear.dot fixture."""

    def _load(self) -> str:
        return _load_fixture("simple_linear.dot")

    @pytest.mark.asyncio
    async def test_parse_produces_correct_graph(self):
        """Parse the fixture and verify node/edge counts."""
        dot = self._load()
        graph = parse_dot(dot)

        assert graph.name == "SimpleLinear"
        assert len(graph.nodes) == 4  # start, implement, validate, exit
        assert (
            len(graph.edges) == 3
        )  # start->implement, implement->validate, validate->exit
        assert graph.goal == "Implement a simple feature"

    @pytest.mark.asyncio
    async def test_validate_no_errors(self):
        """Validation should produce no errors for this valid graph."""
        dot = self._load()
        graph = parse_dot(dot)
        diags = validate(graph)
        errors = [d for d in diags if d.severity == "ERROR"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_full_execution(self, tmp_path):
        """Full pipeline execution: start → implement → validate → exit."""
        dot = self._load()
        backend = SuccessBackend()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        outcome = await engine.run()

        # Pipeline should succeed
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        # Backend called for codergen nodes only (not start/exit)
        assert "implement" in backend.calls
        assert "validate" in backend.calls
        assert "start" not in backend.calls
        assert "exit" not in backend.calls

    @pytest.mark.asyncio
    async def test_correct_execution_order(self, tmp_path):
        """Nodes execute in correct order: start, implement, validate."""
        dot = self._load()
        backend = SuccessBackend()

        engine = _make_engine(dot, backend=backend, logs_root=str(tmp_path))
        await engine.run()

        # Completed nodes should be in order
        assert engine.completed_nodes == ["start", "implement", "validate"]
        # Backend only called for codergen nodes
        assert backend.calls == ["implement", "validate"]

    @pytest.mark.asyncio
    async def test_context_propagation(self, tmp_path):
        """Context updates from one node are visible to the next."""
        dot = self._load()
        backend = SuccessBackend()

        engine = _make_engine(dot, backend=backend, logs_root=str(tmp_path))
        await engine.run()

        # Goal should be in context
        assert engine.context.get("graph.goal") == "Implement a simple feature"
        # Last outcome should be set
        assert engine.context.get("outcome") is not None

    @pytest.mark.asyncio
    async def test_events_emitted(self, tmp_path):
        """All expected events are emitted during execution."""
        dot = self._load()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        await engine.run()

        names = recorder.event_names
        assert PIPELINE_START in names
        assert PIPELINE_COMPLETE in names
        assert PIPELINE_NODE_START in names
        assert PIPELINE_NODE_COMPLETE in names
        assert PIPELINE_EDGE_SELECTED in names
        assert PIPELINE_CHECKPOINT in names

    @pytest.mark.asyncio
    async def test_checkpoint_created(self, tmp_path):
        """Checkpoint file is created after execution."""
        dot = self._load()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        checkpoint_path = os.path.join(str(tmp_path), "checkpoint.json")
        assert os.path.exists(checkpoint_path)

        with open(checkpoint_path) as f:
            cp = json.load(f)
        assert "start" in cp["completed_nodes"]
        assert "implement" in cp["completed_nodes"]
        assert "validate" in cp["completed_nodes"]

    @pytest.mark.asyncio
    async def test_goal_expansion_in_prompt(self, tmp_path):
        """$goal in prompts is expanded to the graph goal."""
        dot = self._load()
        backend = SuccessBackend()

        engine = _make_engine(dot, backend=backend, logs_root=str(tmp_path))
        await engine.run()

        # The implement node has prompt="Implement the feature based on: $goal"
        # After expansion, $goal should be replaced
        implement_prompt = backend.prompts.get("implement", "")
        assert "Implement a simple feature" in implement_prompt
        assert "$goal" not in implement_prompt

    @pytest.mark.asyncio
    async def test_artifacts_written(self, tmp_path):
        """Per-node artifacts (prompt.md, response.md, status.json) are written."""
        dot = self._load()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        # Check implement node artifacts
        impl_dir = os.path.join(str(tmp_path), "implement")
        assert os.path.exists(os.path.join(impl_dir, "prompt.md"))
        assert os.path.exists(os.path.join(impl_dir, "response.md"))
        assert os.path.exists(os.path.join(impl_dir, "status.json"))


# ===========================================================================
# Test Suite 2: conditional_branch.dot
# Start → Implement → Test → Gate (success/fail edges) → Exit
# ===========================================================================


class TestConditionalBranch:
    """Tests using the conditional_branch.dot fixture."""

    def _load(self) -> str:
        return _load_fixture("conditional_branch.dot")

    @pytest.mark.asyncio
    async def test_parse_produces_correct_graph(self):
        """Parse the fixture and verify structure."""
        dot = self._load()
        graph = parse_dot(dot)

        assert graph.name == "ConditionalBranch"
        assert len(graph.nodes) == 4  # start, implement, test, exit
        assert len(graph.edges) == 4  # start->impl, impl->test, test->exit, test->impl
        assert graph.goal == "Implement and validate a feature"

    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path):
        """Success path: test routes to exit when outcome=success."""
        dot = self._load()
        backend = SuccessBackend()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # Should take the success path: start -> implement -> test -> exit
        assert "implement" in backend.calls
        assert "test" in backend.calls

    @pytest.mark.asyncio
    async def test_failure_loops_back(self, tmp_path):
        """Failure path: test routes back to implement when outcome=fail.

        When the test node returns FAIL, the engine sets context['outcome']='fail'.
        Edge selection evaluates 'outcome=fail' and routes back to implement.
        On the second pass, test returns SUCCESS and routes to exit.
        """
        call_count = {"test": 0}

        class FailThenSucceedBackend:
            def __init__(self):
                self.calls: list[str] = []

            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                self.calls.append(node.id)
                if node.id == "test":
                    call_count["test"] += 1
                    if call_count["test"] <= 1:
                        return Outcome(
                            status=StageStatus.FAIL,
                            failure_reason="Tests failed",
                        )
                return Outcome(status=StageStatus.SUCCESS)

        dot = self._load()
        backend = FailThenSucceedBackend()

        engine = _make_engine(dot, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()

        # Should eventually succeed after retrying
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # implement should have been called at least twice (initial + retry)
        impl_count = sum(1 for c in backend.calls if c == "implement")
        assert impl_count >= 2

    @pytest.mark.asyncio
    async def test_edge_selection_events(self, tmp_path):
        """Edge selection events are emitted with correct from/to nodes."""
        dot = self._load()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        await engine.run()

        edge_events = recorder.get_data(PIPELINE_EDGE_SELECTED)
        assert len(edge_events) >= 3  # start->impl, impl->test, test->exit
        # First edge should be start -> implement
        assert edge_events[0]["from_node"] == "start"
        assert edge_events[0]["to_node"] == "implement"

    @pytest.mark.asyncio
    async def test_node_complete_events(self, tmp_path):
        """Node complete events include status and duration."""
        dot = self._load()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        await engine.run()

        complete_events = recorder.get_data(PIPELINE_NODE_COMPLETE)
        assert len(complete_events) >= 3  # start, implement, test
        for evt in complete_events:
            assert "node_id" in evt
            assert "status" in evt
            assert "duration_ms" in evt


# ===========================================================================
# Test Suite 3: goal_gate.dot
# Start → Implement (goal_gate=true) → Review → Exit
# Tests retry on unsatisfied gate
# ===========================================================================


class TestGoalGate:
    """Tests using the goal_gate.dot fixture."""

    def _load(self) -> str:
        return _load_fixture("goal_gate.dot")

    @pytest.mark.asyncio
    async def test_parse_produces_correct_graph(self):
        """Parse the fixture and verify structure."""
        dot = self._load()
        graph = parse_dot(dot)

        assert graph.name == "GoalGate"
        assert len(graph.nodes) == 4  # start, implement, review, exit
        assert graph.goal == "Create a hello world Python script"

        # Verify goal_gate attribute on implement node
        impl_node = graph.nodes["implement"]
        assert impl_node.attrs.get("goal_gate") is True

    @pytest.mark.asyncio
    async def test_validate_with_goal_gate(self):
        """Validation should not error — goal gate has retry_target."""
        dot = self._load()
        graph = parse_dot(dot)
        diags = validate(graph)
        errors = [d for d in diags if d.severity == "ERROR"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_success_with_satisfied_gate(self, tmp_path):
        """Pipeline succeeds when goal gate node succeeds."""
        dot = self._load()
        backend = SuccessBackend()
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert "implement" in backend.calls
        assert "review" in backend.calls

        # Goal gate check should have been emitted
        gate_events = recorder.get_data(PIPELINE_GOAL_GATE_CHECK)
        assert len(gate_events) >= 1

    @pytest.mark.asyncio
    async def test_retry_on_unsatisfied_gate(self, tmp_path):
        """Goal gate unsatisfied → pipeline retries from retry_target.

        When implement fails the first time, the pipeline should:
        1. Reach exit with unsatisfied gate
        2. Route back to implement (retry_target)
        3. Succeed on second attempt
        """
        dot = self._load()
        backend = RetryThenSucceedBackend(fail_node="implement", fail_count=1)
        recorder = EventRecorder()

        engine = _make_engine(
            dot,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        outcome = await engine.run()

        # Should eventually succeed after retry
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        # implement should have been called at least twice
        impl_count = sum(1 for c in backend.calls if c == "implement")
        assert impl_count >= 2

        # Goal gate check events should show the gate was checked
        gate_events = recorder.get_data(PIPELINE_GOAL_GATE_CHECK)
        assert len(gate_events) >= 1

    @pytest.mark.asyncio
    async def test_checkpoint_includes_goal_gate_node(self, tmp_path):
        """Checkpoint should track the goal gate node's outcome."""
        dot = self._load()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        checkpoint_path = os.path.join(str(tmp_path), "checkpoint.json")
        with open(checkpoint_path) as f:
            cp = json.load(f)

        assert "implement" in cp["completed_nodes"]
        assert cp["completed_nodes"]["implement"] == "success"

    @pytest.mark.asyncio
    async def test_context_has_goal(self, tmp_path):
        """Goal is propagated into context."""
        dot = self._load()

        engine = _make_engine(
            dot,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        assert engine.context.get("graph.goal") == "Create a hello world Python script"


# ===========================================================================
# Test Suite 4: Spec Section 11.13 Integration Smoke Test
# Plan → Implement → Review → Done (from the spec's pseudocode)
# ===========================================================================


class TestSpecSmokeTest:
    """Implements the exact test from Section 11.13 of the attractor spec."""

    SPEC_DOT = """\
digraph test_pipeline {
    graph [goal="Create a hello world Python script"]

    start       [shape=Mdiamond]
    plan        [shape=box, prompt="Plan how to create a hello world script for: $goal"]
    implement   [shape=box, prompt="Write the code based on the plan", goal_gate=true]
    review      [shape=box, prompt="Review the code for correctness"]
    done        [shape=Msquare]

    start -> plan
    plan -> implement
    implement -> review [condition="outcome=success"]
    implement -> plan   [condition="outcome=fail", label="Retry"]
    review -> done      [condition="outcome=success"]
    review -> implement [condition="outcome=fail", label="Fix"]
}
"""

    @pytest.mark.asyncio
    async def test_step1_parse(self):
        """Step 1: Parse and verify graph structure."""
        graph = parse_dot(self.SPEC_DOT)

        assert graph.goal == "Create a hello world Python script"
        assert len(graph.nodes) == 5
        assert len(graph.edges) == 6

    @pytest.mark.asyncio
    async def test_step2_validate(self):
        """Step 2: Validate produces no error-severity results."""
        graph = parse_dot(self.SPEC_DOT)
        diags = validate(graph)
        errors = [d for d in diags if d.severity == "ERROR"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_step3_execute(self, tmp_path):
        """Step 3: Execute with mock backend and verify success."""
        backend = SuccessBackend()
        recorder = EventRecorder()

        engine = _make_engine(
            self.SPEC_DOT,
            backend=backend,
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        outcome = await engine.run()

        # Step 4: Verify outcome
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert "implement" in engine.completed_nodes

    @pytest.mark.asyncio
    async def test_step4_verify_artifacts(self, tmp_path):
        """Step 4: Verify per-node artifacts exist."""
        engine = _make_engine(
            self.SPEC_DOT,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        for node_id in ["plan", "implement", "review"]:
            node_dir = os.path.join(str(tmp_path), node_id)
            assert os.path.exists(os.path.join(node_dir, "prompt.md")), (
                f"Missing prompt.md for {node_id}"
            )
            assert os.path.exists(os.path.join(node_dir, "response.md")), (
                f"Missing response.md for {node_id}"
            )
            assert os.path.exists(os.path.join(node_dir, "status.json")), (
                f"Missing status.json for {node_id}"
            )

    @pytest.mark.asyncio
    async def test_step5_goal_gate_satisfied(self, tmp_path):
        """Step 5: Verify goal gate on implement is satisfied."""
        engine = _make_engine(
            self.SPEC_DOT,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        # implement is a goal gate — it should be in outcomes with SUCCESS
        impl_outcome = engine.node_outcomes.get("implement")
        assert impl_outcome is not None
        assert impl_outcome.is_success

    @pytest.mark.asyncio
    async def test_step6_checkpoint(self, tmp_path):
        """Step 6: Verify checkpoint records completed nodes."""
        engine = _make_engine(
            self.SPEC_DOT,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        checkpoint_path = os.path.join(str(tmp_path), "checkpoint.json")
        with open(checkpoint_path) as f:
            cp = json.load(f)

        assert "plan" in cp["completed_nodes"]
        assert "implement" in cp["completed_nodes"]
        assert "review" in cp["completed_nodes"]

    @pytest.mark.asyncio
    async def test_full_event_sequence(self, tmp_path):
        """Verify the complete event sequence for a successful run."""
        recorder = EventRecorder()

        engine = _make_engine(
            self.SPEC_DOT,
            backend=SuccessBackend(),
            logs_root=str(tmp_path),
            hooks=recorder,
        )
        await engine.run()

        names = recorder.event_names

        # First event should be pipeline:start
        assert names[0] == PIPELINE_START

        # Last event should be pipeline:complete
        assert names[-1] == PIPELINE_COMPLETE

        # Should have node_start/complete pairs for each executed node
        start_nodes = [d["node_id"] for d in recorder.get_data(PIPELINE_NODE_START)]
        complete_nodes = [
            d["node_id"] for d in recorder.get_data(PIPELINE_NODE_COMPLETE)
        ]
        for node_id in ["start", "plan", "implement", "review"]:
            assert node_id in start_nodes, f"{node_id} missing from node_start events"
            assert node_id in complete_nodes, (
                f"{node_id} missing from node_complete events"
            )

        # Should have edge_selected events
        edge_events = recorder.get_data(PIPELINE_EDGE_SELECTED)
        assert (
            len(edge_events) >= 4
        )  # start->plan, plan->impl, impl->review, review->done
