"""Tests for PipelineEngine.terminate_pipeline() helper.

Spec coverage: T2.3 — terminate_pipeline() centralizes routing-termination
Outcome construction so the invariant (thread upstream failure_reason,
put routing message in notes) is enforced in one place rather than
duplicated at three call sites.

These tests define the expected behavior BEFORE implementation (TDD RED phase).
"""

import ast
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine():
    """Build a minimal PipelineEngine instance for unit-testing terminate_pipeline."""
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.graph import Graph, Node
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext

    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[],
    )
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext()),
        logs_root="/tmp/test_terminate",
    )
    return engine


# ---------------------------------------------------------------------------
# Core behavior: failure_reason threading
# ---------------------------------------------------------------------------


def test_terminate_pipeline_preserves_upstream_failure_reason():
    """terminate_pipeline threads upstream failure_reason into the result Outcome.

    When an upstream outcome has a failure_reason, the routing-termination
    Outcome should carry that reason as its failure_reason (not the routing
    message).  The routing message goes into notes.
    """
    engine = _make_engine()
    upstream = Outcome(
        status=StageStatus.FAIL,
        failure_reason="actual_handler_error",
    )
    result = engine.terminate_pipeline(
        node_id="some_node",
        upstream_outcome=upstream,
        termination_reason="No matching edge from node 'some_node'",
    )
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "actual_handler_error"
    assert result.notes == "No matching edge from node 'some_node'"


def test_terminate_pipeline_no_upstream_reason_uses_termination_reason():
    """When upstream has no failure_reason, termination_reason becomes failure_reason.

    This is the behavior preserved from before PR #34 for cases where no
    handler error exists — the routing message IS the failure reason.
    """
    engine = _make_engine()
    upstream = Outcome(status=StageStatus.FAIL)  # no failure_reason
    result = engine.terminate_pipeline(
        node_id="some_node",
        upstream_outcome=upstream,
        termination_reason="No matching edge from node 'some_node'",
    )
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "No matching edge from node 'some_node'"
    assert result.notes is None


def test_terminate_pipeline_no_upstream_outcome():
    """terminate_pipeline works with upstream_outcome=None (resume-path site).

    The resume-path routing-termination site doesn't have an upstream
    handler outcome, only a routing message.
    """
    engine = _make_engine()
    result = engine.terminate_pipeline(
        node_id="some_node",
        upstream_outcome=None,
        termination_reason="No matching edge from resumed node 'some_node'",
    )
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "No matching edge from resumed node 'some_node'"
    assert result.notes is None


def test_terminate_pipeline_always_returns_fail_status():
    """terminate_pipeline always returns FAIL status regardless of upstream."""
    engine = _make_engine()
    # Even if upstream succeeded, routing-termination means the pipeline fails
    result = engine.terminate_pipeline(
        node_id="node",
        upstream_outcome=Outcome(status=StageStatus.SUCCESS),
        termination_reason="Some routing message",
    )
    assert result.status == StageStatus.FAIL


# ---------------------------------------------------------------------------
# Totality: terminate_pipeline never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "upstream_outcome,termination_reason",
    [
        # None upstream
        (None, "reason"),
        # Upstream with no failure_reason
        (Outcome(status=StageStatus.FAIL), "reason"),
        # Upstream with failure_reason
        (Outcome(status=StageStatus.FAIL, failure_reason="handler_err"), "reason"),
        # Empty termination_reason
        (None, ""),
        # Unicode termination_reason
        (None, "réseau de terminaison\u2019s"),
        # Very long reason
        (None, "x" * 10_000),
        # Upstream with notes
        (
            Outcome(
                status=StageStatus.FAIL,
                failure_reason="err",
                notes="some notes",
            ),
            "routing message",
        ),
        # Skipped upstream
        (Outcome(status=StageStatus.SKIPPED, failure_reason="skip_reason"), "msg"),
        # Success upstream
        (Outcome(status=StageStatus.SUCCESS), "routing msg"),
    ],
)
def test_terminate_pipeline_totality(upstream_outcome, termination_reason):
    """terminate_pipeline never raises across the full input space.

    This is the totality test required by the spec: the helper must be
    safe to call from any routing-termination site without try/except.
    """
    engine = _make_engine()
    # Must not raise
    result = engine.terminate_pipeline(
        node_id="n",
        upstream_outcome=upstream_outcome,
        termination_reason=termination_reason,
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL


# ---------------------------------------------------------------------------
# Sole-caller AST guard: no inline routing-termination Outcome in engine.py
# ---------------------------------------------------------------------------


def test_no_inline_routing_termination_outcome_in_engine_py():
    """AST guard: engine.py has no inline routing-termination Outcome constructions.

    After the T2.3 refactor, the ONLY place that constructs
    ``Outcome(status=StageStatus.FAIL, ...)`` with a routing-termination
    reason string like "No matching edge from" is inside
    ``terminate_pipeline()``.

    This test walks the AST of engine.py and asserts that no top-level
    call site (outside of terminate_pipeline's body) constructs an Outcome
    whose failure_reason literal matches the routing-termination pattern.
    """
    engine_path = (
        Path(__file__).parent.parent
        / "amplifier_module_loop_pipeline"
        / "engine.py"
    )
    source = engine_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    _ROUTING_PATTERNS = (
        "No matching edge from",
        "No matching edge from resumed node",
        "No matching edge from skipped node",
    )

    # Find terminate_pipeline's body range so we can exclude it
    # (the helper is allowed to construct the Outcome — that's the point)
    terminate_pipeline_linenos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "terminate_pipeline":
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    terminate_pipeline_linenos.add(child.lineno)

    # Find all Outcome(...) calls with routing-termination patterns
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Is this an Outcome(...) call?
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "Outcome"):
            continue
        # Is the call site OUTSIDE of terminate_pipeline?
        if hasattr(node, "lineno") and node.lineno in terminate_pipeline_linenos:
            continue
        # Does it have a failure_reason kwarg that matches a routing-termination pattern?
        for kw in node.keywords:
            if kw.arg != "failure_reason":
                continue
            if not isinstance(kw.value, (ast.Constant, ast.JoinedStr)):
                continue
            # Check string constant
            if isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str
            ):
                for pat in _ROUTING_PATTERNS:
                    if pat in kw.value.value:
                        violations.append(node.lineno)
            # Check f-string (JoinedStr) - check if any constant part matches
            if isinstance(kw.value, ast.JoinedStr):
                for part in kw.value.values:
                    if isinstance(part, ast.Constant) and isinstance(
                        part.value, str
                    ):
                        for pat in _ROUTING_PATTERNS:
                            if pat in part.value:
                                violations.append(node.lineno)

    assert violations == [], (
        f"engine.py has inline routing-termination Outcome constructions at "
        f"lines {violations}. These must be replaced with terminate_pipeline() calls. "
        f"Only the body of terminate_pipeline() may construct these Outcomes."
    )
