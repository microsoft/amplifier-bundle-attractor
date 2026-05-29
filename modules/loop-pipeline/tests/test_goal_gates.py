"""Tests for goal gate enforcement at pipeline exit.

Spec coverage: GOAL-001–006, Section 3.4.

Goal gates are nodes with goal_gate=true that MUST reach SUCCESS or
PARTIAL_SUCCESS before the pipeline can exit. When traversal hits
the exit node, unsatisfied goal gates redirect to retry_target.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class MockBackend:
    """Backend that returns configurable outcomes per node."""

    def __init__(self, outcomes: dict[str, str | Outcome] | None = None):
        self._outcomes = outcomes or {}
        self.calls: list[str] = []

    async def run(self, node, prompt, context) -> str | Outcome:
        self.calls.append(node.id)
        return self._outcomes.get(node.id, "ok")


class CountingBackend:
    """Backend that tracks call counts per node and changes behavior."""

    def __init__(self, outcomes_by_call: dict[str, list[str | Outcome]]):
        """outcomes_by_call: node_id -> [outcome_for_call_1, outcome_for_call_2, ...]"""
        self._outcomes = outcomes_by_call
        self._counts: dict[str, int] = {}
        self.calls: list[str] = []

    async def run(self, node, prompt, context) -> str | Outcome:
        self.calls.append(node.id)
        count = self._counts.get(node.id, 0)
        self._counts[node.id] = count + 1
        outcomes = self._outcomes.get(node.id, ["ok"])
        idx = min(count, len(outcomes) - 1)
        return outcomes[idx]


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-goal-gates",
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


# --- GOAL-001: Satisfied goal gates allow exit ---


@pytest.mark.asyncio
async def test_satisfied_goal_gates_allow_exit(tmp_path):
    """When all goal gates succeed, pipeline exits successfully."""
    backend = MockBackend(
        outcomes={
            "critical": Outcome(status=StageStatus.SUCCESS, notes="All good"),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true]
            exit [shape=Msquare]
            start -> critical -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.is_success


@pytest.mark.asyncio
async def test_partial_success_satisfies_goal_gate(tmp_path):
    """PARTIAL_SUCCESS counts as satisfying a goal gate."""
    backend = MockBackend(
        outcomes={
            "critical": Outcome(
                status=StageStatus.PARTIAL_SUCCESS, notes="Mostly done"
            ),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true]
            exit [shape=Msquare]
            start -> critical -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.is_success


# --- GOAL-002: Unsatisfied goal gate without retry target fails ---


@pytest.mark.asyncio
async def test_unsatisfied_goal_gate_no_retry_target_fails(tmp_path):
    """Unsatisfied goal gate with no retry target → pipeline FAIL."""
    backend = MockBackend(
        outcomes={
            "critical": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true]
            exit [shape=Msquare]
            start -> critical
            critical -> exit [condition="outcome=fail"]
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.FAIL
    assert "goal gate" in (outcome.failure_reason or "").lower()


# --- GOAL-003: Unsatisfied goal gate jumps to node retry_target ---


@pytest.mark.asyncio
async def test_unsatisfied_goal_gate_jumps_to_node_retry_target(tmp_path):
    """Unsatisfied goal gate → jump to node-level retry_target."""
    # First call: critical fails. Engine hits exit, finds unsatisfied gate,
    # jumps to retry_target "critical". Second call: critical succeeds.
    backend = CountingBackend(
        outcomes_by_call={
            "critical": [
                Outcome(status=StageStatus.FAIL, failure_reason="broken"),
                Outcome(status=StageStatus.SUCCESS, notes="Fixed"),
            ],
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true, retry_target="critical"]
            exit [shape=Msquare]
            start -> critical
            critical -> exit [condition="outcome=fail"]
            critical -> exit [condition="outcome=success"]
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.is_success
    # critical should have been called twice
    assert backend.calls.count("critical") == 2


# --- GOAL-004: Unsatisfied goal gate uses graph-level retry_target ---


@pytest.mark.asyncio
async def test_unsatisfied_goal_gate_uses_graph_retry_target(tmp_path):
    """Unsatisfied goal gate falls back to graph-level retry_target."""
    backend = CountingBackend(
        outcomes_by_call={
            "critical": [
                Outcome(status=StageStatus.FAIL, failure_reason="broken"),
                Outcome(status=StageStatus.SUCCESS, notes="Fixed"),
            ],
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            retry_target = "critical"
            start [shape=Mdiamond]
            critical [prompt="Critical step", goal_gate=true]
            exit [shape=Msquare]
            start -> critical
            critical -> exit [condition="outcome=fail"]
            critical -> exit [condition="outcome=success"]
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.is_success
    assert backend.calls.count("critical") == 2


# --- GOAL-005: Multiple goal gates ---


@pytest.mark.asyncio
async def test_multiple_goal_gates_all_must_pass(tmp_path):
    """All goal gates must be satisfied, not just the first one."""
    backend = MockBackend(
        outcomes={
            "review": Outcome(status=StageStatus.SUCCESS),
            "test": Outcome(status=StageStatus.FAIL, failure_reason="tests fail"),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            review [prompt="Review", goal_gate=true]
            test [prompt="Test", goal_gate=true]
            exit [shape=Msquare]
            start -> review -> test
            test -> exit [condition="outcome=fail"]
            test -> exit [condition="outcome=success"]
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.FAIL
    assert "test" in (outcome.failure_reason or "").lower()


# --- GOAL-006: Goal gate check only at exit node ---


@pytest.mark.asyncio
async def test_goal_gate_check_only_at_exit(tmp_path):
    """Goal gates are checked only when reaching exit, not during traversal."""
    # critical fails, but pipeline continues to next step before hitting exit
    backend = MockBackend(
        outcomes={
            "critical": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            "cleanup": Outcome(status=StageStatus.SUCCESS),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical", goal_gate=true]
            cleanup [prompt="Cleanup"]
            exit [shape=Msquare]
            start -> critical
            critical -> cleanup [condition="outcome=fail"]
            cleanup -> exit
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    # Even though cleanup succeeded, the goal gate on critical is unsatisfied
    assert outcome.status == StageStatus.FAIL
    # Cleanup was still executed (gate not checked until exit)
    assert "cleanup" in backend.calls


# --- Edge case: non-goal-gate nodes don't affect gate check ---


@pytest.mark.asyncio
async def test_non_goal_gate_failure_doesnt_block_exit(tmp_path):
    """Non-goal-gate nodes with FAIL status don't block exit."""
    backend = MockBackend(
        outcomes={
            "optional": Outcome(
                status=StageStatus.FAIL, failure_reason="optional fail"
            ),
        }
    )
    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            optional [prompt="Optional step"]
            exit [shape=Msquare]
            start -> optional
            optional -> exit [condition="outcome=fail"]
        }
        """,
        backend=backend,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    # No goal_gate attribute, so failure doesn't block exit
    # The last node's outcome is used
    assert outcome.status == StageStatus.FAIL


# --- Goal gate retry with bounded retries to prevent infinite loops ---


@pytest.mark.asyncio
async def test_goal_gate_retry_bounded(tmp_path):
    """Goal gate retry is bounded to prevent infinite loops."""
    # critical always fails — should eventually give up
    backend = MockBackend(
        outcomes={
            "critical": Outcome(status=StageStatus.FAIL, failure_reason="stuck"),
        }
    )
    graph = parse_dot("""
        digraph {
            start [shape=Mdiamond]
            critical [prompt="Critical", goal_gate=true, retry_target="critical"]
            exit [shape=Msquare]
            start -> critical
            critical -> exit [condition="outcome=fail"]
        }
    """)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    # Should eventually fail (not loop forever)
    assert outcome.status == StageStatus.FAIL
    # Should have retried some number of times but not infinitely
    assert len(backend.calls) < 200
