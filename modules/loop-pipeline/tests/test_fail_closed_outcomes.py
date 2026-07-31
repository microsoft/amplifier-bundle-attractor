"""Fail-closed goal-gate outcome contract — EXTENSIONS.md §25.

Regression tests for T0-5: no-verdict completions must not satisfy goal gates.

Incident (2026-07-28): a convergence judge marked goal_gate=true wrote
"NOT CONVERGED — 2 of 7 criteria pass" and was recorded outcome=success by
the fail-open default in _parse_outcome. These tests lock down the fix.

Coverage:
  FC-001  goal_gate=true + plain prose → RETRY (fail-closed, not SUCCESS)
  FC-002  non-goal_gate + plain prose → SUCCESS (spec §4.5 preserved, plain-edge safe)
  FC-003  goal_gate=true + explicit JSON verdict → SUCCESS (ladder preserved)
  FC-004  goal_gate=true + fenced JSON verdict → SUCCESS (ladder preserved)
  FC-005  goal_gate=true + embedded verdict recovery → SUCCESS (ladder preserved)
  FC-006  goal_gate=true + report_outcome-style JSON → SUCCESS (ladder preserved)
  FC-007  is_explicit=True on explicit verdicts, False on plain-prose fallback
  FC-008  is_explicit=False on spawn-status-only result (no node-level verdict)
  FC-009  plain-edge safety: observer node (no goal_gate) with plain out-edge
          still gets SUCCESS and can traverse the edge (no silent hard-stop)
  FC-010  full-engine fixture: goal_gate + plain prose → pipeline does NOT exit success
  FC-011  full-engine fixture: goal_gate + explicit JSON success → pipeline exits success
  FC-012  goal_gate=true human gate is satisfiable (selection is an explicit verdict)
  FC-013  goal_gate=true fan-in node is satisfiable (aggregation is an explicit verdict)
  FC-014  is_explicit serialized into status.json (flat + iteration) and trace.jsonl
  FC-015  is_explicit serialized by the codergen early-writer (_write_status)

Structured-output (response_schema) verdict policy tests live in
test_response_schema.py (they need the unified_llm mock harness).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from amplifier_module_loop_pipeline.backend import _parse_outcome, _outcome_from_spawn_result
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise

# ---------------------------------------------------------------------------
# Incident prose specimen — the exact text from the 2026-07-28 incident
# ---------------------------------------------------------------------------
_INCIDENT_PROSE = (
    "I reviewed the seven convergence criteria in detail. "
    "Verdict: NOT CONVERGED - only 2 of 7 criteria pass. "
    "The networking implementation does not work and the harness was never created. "
    "Substantial work remains before this pipeline can be considered done."
)

# A benign observer node prose specimen
_OBSERVER_PROSE = "Observer: pipeline health looks good, no issues detected."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(goal_gate: bool = False, node_id: str = "judge") -> Node:
    """Build a minimal Node for _parse_outcome testing."""
    return Node(
        id=node_id,
        attrs={"goal_gate": "true" if goal_gate else None},
        goal_gate=goal_gate or None,
    )


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-fail-closed",
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


# ---------------------------------------------------------------------------
# FC-001: goal_gate=true + plain prose → RETRY
# ---------------------------------------------------------------------------


def test_fc001_goal_gate_plain_prose_returns_retry():
    """FC-001: goal_gate=true node with plain prose → RETRY (fail-closed).

    This is the exact incident failure mode: the judge wrote "NOT CONVERGED"
    in prose and was recorded SUCCESS. After the fix, it must return RETRY.
    """
    node = _make_node(goal_gate=True)
    result = _parse_outcome(_INCIDENT_PROSE, node=node)

    assert result.status == StageStatus.RETRY, (
        f"goal_gate=true plain prose must return RETRY (fail-closed), got {result.status!r}. "
        "EXTENSIONS.md §25 fix may not be applied."
    )
    assert not result.is_success, "RETRY must not be is_success"
    assert result.is_explicit is False, "Plain-prose fallback must have is_explicit=False"


# ---------------------------------------------------------------------------
# FC-002: non-goal_gate + plain prose → SUCCESS (spec §4.5 preserved)
# ---------------------------------------------------------------------------


def test_fc002_non_goal_gate_plain_prose_returns_success():
    """FC-002: Non-goal_gate node with plain prose → SUCCESS (spec §4.5 preserved).

    The fail-closed contract is scoped to goal_gate=true nodes only.
    Ordinary box nodes must still return SUCCESS for plain prose so that
    existing pipelines and plain-edge routing are unaffected.
    """
    # No node arg — simulates a non-goal_gate call
    result_no_node = _parse_outcome(_OBSERVER_PROSE)
    assert result_no_node.status == StageStatus.SUCCESS, (
        f"Non-goal_gate plain prose must return SUCCESS (spec §4.5), got {result_no_node.status!r}"
    )
    assert "Plain text response" in (result_no_node.notes or "")

    # Explicit node with goal_gate=False
    node = _make_node(goal_gate=False)
    result_explicit = _parse_outcome(_OBSERVER_PROSE, node=node)
    assert result_explicit.status == StageStatus.SUCCESS, (
        f"goal_gate=False plain prose must return SUCCESS, got {result_explicit.status!r}"
    )


# ---------------------------------------------------------------------------
# FC-003: goal_gate=true + explicit JSON verdict → SUCCESS preserved
# ---------------------------------------------------------------------------


def test_fc003_goal_gate_explicit_json_success_preserved():
    """FC-003: goal_gate=true + pure JSON success verdict → SUCCESS (ladder preserved).

    The fail-closed rule sits BELOW the verdict-recovery ladder.
    An explicit JSON success verdict must still satisfy the gate.
    """
    node = _make_node(goal_gate=True)
    payload = json.dumps({"status": "success", "notes": "All 7 criteria pass."})
    result = _parse_outcome(payload, node=node)

    assert result.status == StageStatus.SUCCESS, (
        f"Explicit JSON success on goal_gate node must return SUCCESS, got {result.status!r}"
    )
    assert result.is_explicit is True, "JSON verdict must have is_explicit=True"
    assert result.notes == "All 7 criteria pass."


# ---------------------------------------------------------------------------
# FC-004: goal_gate=true + fenced JSON verdict → SUCCESS preserved
# ---------------------------------------------------------------------------


def test_fc004_goal_gate_fenced_json_success_preserved():
    """FC-004: goal_gate=true + fenced JSON success verdict → SUCCESS (ladder preserved)."""
    node = _make_node(goal_gate=True)
    payload = "```json\n" + json.dumps({"status": "success", "notes": "Converged."}) + "\n```"
    result = _parse_outcome(payload, node=node)

    assert result.status == StageStatus.SUCCESS, (
        f"Fenced JSON success on goal_gate node must return SUCCESS, got {result.status!r}"
    )
    assert result.is_explicit is True


# ---------------------------------------------------------------------------
# FC-005: goal_gate=true + embedded verdict recovery → SUCCESS preserved
# ---------------------------------------------------------------------------


def test_fc005_goal_gate_embedded_verdict_recovery_preserved():
    """FC-005: goal_gate=true + prose-then-JSON verdict → embedded recovery preserved.

    Judges that emit prose + trailing JSON verdicts must keep working via
    the embedded verdict recovery path (step 4 of the ladder).
    """
    node = _make_node(goal_gate=True)
    payload = (
        "After reviewing all 7 criteria in detail, here is my verdict:\n"
        + json.dumps({"status": "success", "notes": "All criteria satisfied."})
    )
    result = _parse_outcome(payload, node=node)

    assert result.status == StageStatus.SUCCESS, (
        f"Embedded JSON success on goal_gate node must return SUCCESS, got {result.status!r}"
    )
    assert result.is_explicit is True


# ---------------------------------------------------------------------------
# FC-006: goal_gate=true + explicit FAIL verdict → FAIL preserved
# ---------------------------------------------------------------------------


def test_fc006_goal_gate_explicit_fail_verdict_preserved():
    """FC-006: goal_gate=true + explicit JSON FAIL verdict → FAIL (not RETRY).

    An explicit FAIL verdict must not be changed to RETRY by the fail-closed rule.
    The fail-closed rule only fires when there is NO parseable verdict at all.
    """
    node = _make_node(goal_gate=True)
    payload = json.dumps({"status": "fail", "failure_reason": "Tests did not pass."})
    result = _parse_outcome(payload, node=node)

    assert result.status == StageStatus.FAIL, (
        f"Explicit JSON FAIL on goal_gate node must return FAIL, got {result.status!r}"
    )
    assert result.is_explicit is True
    assert result.failure_reason == "Tests did not pass."


# ---------------------------------------------------------------------------
# FC-007: is_explicit field semantics
# ---------------------------------------------------------------------------


def test_fc007_is_explicit_field_semantics():
    """FC-007: is_explicit=True on explicit verdicts, False on plain-prose fallback.

    is_explicit is the durable observability field for distinguishing asserted
    verdicts from defaulted ones. Incident analysts should not need to
    reverse-engineer the 'Plain text response:' notes prefix.
    """
    node = _make_node(goal_gate=False)  # non-goal_gate so plain prose → SUCCESS

    # Plain prose → is_explicit=False
    plain = _parse_outcome("The work looks complete.", node=node)
    assert plain.status == StageStatus.SUCCESS
    assert plain.is_explicit is False, "Plain-prose fallback must have is_explicit=False"

    # Pure JSON → is_explicit=True
    explicit = _parse_outcome(json.dumps({"status": "success", "notes": "done"}), node=node)
    assert explicit.status == StageStatus.SUCCESS
    assert explicit.is_explicit is True, "JSON verdict must have is_explicit=True"

    # Fenced JSON → is_explicit=True
    fenced = _parse_outcome(
        "```json\n" + json.dumps({"status": "success"}) + "\n```", node=node
    )
    assert fenced.is_explicit is True

    # Embedded recovery → is_explicit=True
    embedded = _parse_outcome(
        "Some prose here.\n" + json.dumps({"status": "fail", "failure_reason": "x"}),
        node=node,
    )
    assert embedded.is_explicit is True

    # Empty → is_explicit=False (FAIL, no verdict)
    empty = _parse_outcome("", node=node)
    assert empty.status == StageStatus.FAIL
    assert empty.is_explicit is False


# ---------------------------------------------------------------------------
# FC-008: spawn-path consistency — status-only success is is_explicit=False
# ---------------------------------------------------------------------------


def test_fc008_spawn_status_only_is_not_explicit():
    """FC-008: _outcome_from_spawn_result() status-only success → is_explicit=False.

    A spawn result that has no report_outcome and no final text but has
    status=success in the orchestrator result should NOT be is_explicit=True.
    A goal_gate child cannot satisfy its gate via the spawn wrapper's status
    alone — it must provide a real verdict.

    EXTENSIONS.md §25: spawn-path consistency.
    """
    # Status-only spawn result (no metadata, no report_outcome)
    result = {"status": "success", "output": ""}
    outcome = _outcome_from_spawn_result(result)

    assert outcome is not None, "Status-only spawn result should return an Outcome"
    assert outcome.status == StageStatus.SUCCESS
    assert outcome.is_explicit is False, (
        "Spawn status-only success must have is_explicit=False — "
        "the orchestrator's completion status is not a node-level verdict. "
        "EXTENSIONS.md §25 fix may not be applied."
    )

    # With report_outcome in metadata → is_explicit=True (real verdict)
    result_with_ro = {
        "status": "success",
        "output": "",
        "metadata": {
            "report_outcome": {"status": "success", "notes": "All done."}
        },
    }
    outcome_with_ro = _outcome_from_spawn_result(result_with_ro)
    assert outcome_with_ro is not None
    assert outcome_with_ro.is_explicit is True, (
        "Spawn result with report_outcome must have is_explicit=True"
    )


# ---------------------------------------------------------------------------
# FC-009: plain-edge safety — observer node (no goal_gate) with plain out-edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc009_observer_node_plain_edge_safety(tmp_path):
    """FC-009: Observer node (no goal_gate) with only plain out-edges gets SUCCESS.

    The plain-edge silent-hard-stop hazard: FAIL/RETRY do not traverse plain
    unconditional edges. A naive global default flip would convert observer/
    reporter nodes into hard stops. The fail-closed contract is scoped to
    goal_gate=true nodes only, so observer nodes are unaffected.

    This test demonstrates the mitigation: a non-goal_gate node whose backend
    calls _parse_outcome with plain prose gets SUCCESS and can traverse its
    plain out-edge to exit. The backend must return an Outcome (not a string)
    so the codergen handler sees the _parse_outcome result directly.
    """

    class ParseOutcomeBackend:
        """Backend that calls _parse_outcome (mimics production spawn path)."""

        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> Outcome:
            return _parse_outcome(_OBSERVER_PROSE, node=node)

    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            observer [prompt="Observe the pipeline health"]
            exit [shape=Msquare]
            start -> observer -> exit
        }
        """,
        backend=ParseOutcomeBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    # Observer node (no goal_gate) with plain prose → SUCCESS (spec §4.5 preserved)
    # Plain out-edge start -> observer -> exit traversed normally (no silent hard-stop)
    assert outcome.is_success, (
        f"Observer node (no goal_gate) with plain prose must exit success via plain edge. "
        f"Got {outcome.status!r}. The fail-closed rule must be goal_gate-scoped only."
    )


# ---------------------------------------------------------------------------
# FC-010: full-engine fixture — goal_gate + plain prose → pipeline does NOT exit success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc010_goal_gate_plain_prose_does_not_exit_success(tmp_path):
    """FC-010: Full-engine: goal_gate=true + plain prose → pipeline does NOT exit success.

    This is the mechanical DoD fixture from T0-5.verify.sh run in-process.
    The incident's exact prose is used. The backend calls _parse_outcome
    directly (mimicking the production spawn path) so the fail-closed rule
    in _parse_outcome is exercised. The pipeline must NOT exit success.
    """

    class PlainProseBackend:
        """Mimics production spawn path: calls _parse_outcome over plain prose.

        Returns an Outcome so the codergen handler sees the _parse_outcome
        result directly (isinstance(result, Outcome) → return result).
        """

        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> Outcome:
            return _parse_outcome(_INCIDENT_PROSE, node=node)

    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            judge [prompt="Evaluate convergence", goal_gate=true, max_retries=0]
            exit [shape=Msquare]
            start -> judge
            judge -> exit [condition="outcome=fail"]
            judge -> exit [condition="outcome=success"]
        }
        """,
        backend=PlainProseBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert not outcome.is_success, (
        f"goal_gate=true plain prose must NOT exit success. Got {outcome.status!r}. "
        "EXTENSIONS.md §25 fail-closed fix may not be applied."
    )


# ---------------------------------------------------------------------------
# FC-011: full-engine fixture — goal_gate + explicit JSON success → pipeline exits success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc011_goal_gate_explicit_json_success_exits_success(tmp_path):
    """FC-011: Full-engine: goal_gate=true + explicit JSON success → pipeline exits success.

    Explicit verdicts must keep working. A goal_gate node whose backend
    calls _parse_outcome over a JSON success verdict must allow the pipeline
    to exit success.
    """

    class ExplicitVerdictBackend:
        """Backend that calls _parse_outcome over an explicit JSON success verdict."""

        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> Outcome:
            payload = json.dumps({
                "status": "success",
                "notes": "All 7 convergence criteria satisfied.",
            })
            return _parse_outcome(payload, node=node)

    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            judge [prompt="Evaluate convergence", goal_gate=true]
            exit [shape=Msquare]
            start -> judge -> exit
        }
        """,
        backend=ExplicitVerdictBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.is_success, (
        f"goal_gate=true explicit JSON success must exit success. Got {outcome.status!r}. "
        "The verdict-recovery ladder must not be weakened."
    )


# ---------------------------------------------------------------------------
# FC-012: goal_gate=true human gate is satisfiable (review round 2, Finding 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc012_goal_gate_human_gate_satisfiable(tmp_path):
    """FC-012: A goal_gate=true human gate must be satisfiable.

    Review round 2, Finding 2 (MAJOR): the gate check requires is_explicit
    for EVERY goal_gate node, but the human handler returned SUCCESS without
    it — so a goal_gate=true human gate retry-stormed despite carrying the
    most explicit verdict possible (a human's selection). The fix classifies
    the human selection as an explicit verdict (is_explicit=True) rather
    than scoping the gate check down: one uniform gate rule is preserved.
    """
    from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

    graph = parse_dot(
        """
        digraph {
            start [shape=Mdiamond]
            gate [shape=hexagon, goal_gate=true, prompt="Approve the release?"]
            exit [shape=Msquare]
            start -> gate
            gate -> exit [label="approve"]
        }
        """
    )
    validate_or_raise(graph)
    registry = HandlerRegistry(
        HandlerContext(backend=None, interviewer=AutoApproveInterviewer())
    )
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    gate_outcome = engine.node_outcomes["gate"]
    assert gate_outcome.is_explicit is True, (
        "Human gate selection must be an explicit verdict (deterministic "
        "human action, cannot be an LLM default). EXTENSIONS.md §25."
    )
    assert outcome.is_success, (
        f"goal_gate=true human gate must be satisfiable by a selection. "
        f"Got {outcome.status!r} — the gate check is over-broad (Finding 2)."
    )


# ---------------------------------------------------------------------------
# FC-013: goal_gate=true fan-in node is satisfiable (review round 2, Finding 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc013_goal_gate_fan_in_satisfiable(tmp_path):
    """FC-013: A goal_gate=true fan-in node must be satisfiable.

    The fan-in handler's verdict is a deterministic ranking rule over branch
    statuses — a legitimate non-LLM verdict mechanism (analogous to a tool
    exit code). It must carry is_explicit=True or a goal_gate fan-in is
    unsatisfiable.
    """
    graph = parse_dot(
        """
        digraph {
            start [shape=Mdiamond]
            consolidate [shape=tripleoctagon, goal_gate=true]
            exit [shape=Msquare]
            start -> consolidate -> exit
        }
        """
    )
    validate_or_raise(graph)
    context = PipelineContext()
    # Simulate an upstream parallel node's results (the fan-in contract input).
    context.set(
        "parallel.results",
        [
            {"node_id": "branch_a", "status": "success", "notes": "A done"},
            {"node_id": "branch_b", "status": "fail", "failure_reason": "B broke"},
        ],
    )
    registry = HandlerRegistry(HandlerContext(backend=None))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    fan_in_outcome = engine.node_outcomes["consolidate"]
    assert fan_in_outcome.is_explicit is True, (
        "Fan-in aggregation verdict must be explicit (deterministic ranking "
        "over branch statuses). EXTENSIONS.md §25."
    )
    assert outcome.is_success, (
        f"goal_gate=true fan-in with a successful best candidate must be "
        f"satisfiable. Got {outcome.status!r} — gate check over-broad (Finding 2)."
    )


# ---------------------------------------------------------------------------
# FC-014: is_explicit is serialized (status.json flat + iteration, trace.jsonl)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fc014_is_explicit_serialized_in_status_and_trace(tmp_path):
    """FC-014: is_explicit is durable audit data — it must be serialized.

    Review round 2, Finding 3 (MINOR): engine-semantics.md calls is_explicit
    the durable field, but the status.json and trace.jsonl writers omitted
    it. Regression: assert presence (and correct value) in the flat
    status.json, the iteration-scoped status.json, and every trace.jsonl
    record.
    """

    class ExplicitVerdictBackend:
        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> Outcome:
            return _parse_outcome(
                json.dumps({"status": "success", "notes": "done"}), node=node
            )

    engine = _make_engine(
        dot_source="""
        digraph {
            start [shape=Mdiamond]
            judge [prompt="Evaluate", goal_gate=true]
            exit [shape=Msquare]
            start -> judge -> exit
        }
        """,
        backend=ExplicitVerdictBackend(),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.is_success

    from pathlib import Path

    root = Path(str(tmp_path))

    # 1. Flat status.json
    flat = json.loads((root / "judge" / "status.json").read_text())
    assert "is_explicit" in flat, "status.json must serialize is_explicit"
    assert flat["is_explicit"] is True

    # 2. Iteration-scoped status.json (Extension #24)
    iter_files = list(root.glob("iteration_*/judge/status.json"))
    assert iter_files, "iteration-scoped status.json missing"
    iter_status = json.loads(iter_files[0].read_text())
    assert iter_status["is_explicit"] is True

    # 3. trace.jsonl — every record carries the field
    trace_lines = (root / "trace.jsonl").read_text().strip().splitlines()
    assert trace_lines, "trace.jsonl missing or empty"
    records = [json.loads(line) for line in trace_lines]
    assert all("is_explicit" in r for r in records), (
        "every trace.jsonl record must carry is_explicit"
    )
    judge_records = [r for r in records if r["node_id"] == "judge"]
    assert judge_records and judge_records[-1]["is_explicit"] is True


# ---------------------------------------------------------------------------
# FC-015: codergen early-writer serializes is_explicit
# ---------------------------------------------------------------------------


def test_fc015_codergen_early_writer_serializes_is_explicit(tmp_path):
    """FC-015: the codergen handler's own status writer carries is_explicit."""
    from amplifier_module_loop_pipeline.handlers.codergen import _write_status

    _write_status(
        str(tmp_path),
        Outcome(status=StageStatus.SUCCESS, notes="ok", is_explicit=True),
    )
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["is_explicit"] is True

    _write_status(
        str(tmp_path),
        Outcome(status=StageStatus.SUCCESS, notes="defaulted"),
    )
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["is_explicit"] is False
