"""Regression tests for spec-conformance batch fixes.

Covers four zero-usage fixes that carry no pipeline blast radius:

  A1 — auto_status must NOT mask an explicit FAIL/RETRY (fail-loud)
  A2 — goal_gate="true" (quoted string) must be recognized
  B6 — retry preset values must match the spec §3.5 table
  B7 — fan_in must publish the spec key parallel.fan_in.best_outcome
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.fan_in import FanInHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.retry import BackoffConfig, RetryPolicy
from amplifier_module_loop_pipeline.validation import validate

# ---------------------------------------------------------------------------
# A1 — auto_status fail-loud (spec §2.6 / Appendix C)
# ---------------------------------------------------------------------------


class _SkippedBackend:
    """Backend that returns SKIPPED — the 'no status written' sentinel."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(status=StageStatus.SKIPPED)


class _FailingBackend:
    """Backend that always returns an explicit FAIL."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(status=StageStatus.FAIL, failure_reason="explicit failure")


class _RetryBackend:
    """Backend that always returns RETRY (exhausts retries → FAIL at engine level)."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(status=StageStatus.RETRY, failure_reason="retrying")


@pytest.mark.asyncio
async def test_auto_status_preserves_explicit_fail_a1(tmp_path):
    """A1: auto_status=true must NOT override an explicit FAIL — fail-loud.

    Spec §2.6 (auto_status) + Appendix C (attractor-spec.md:2078):
    auto_status synthesizes SUCCESS ONLY when the handler writes no status.
    """
    graph = Graph(
        name="test_a1_fail",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(
                id="gate",
                shape="box",
                prompt="do work",
                auto_status=True,
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="exit", condition="outcome=fail"),
        ],
    )
    ctx = PipelineContext()
    engine = PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=HandlerRegistry(HandlerContext(backend=_FailingBackend())),
        logs_root=str(tmp_path),
    )
    await engine.run()
    # FAIL must remain FAIL — auto_status must not mask it
    assert engine.node_outcomes["gate"].status == StageStatus.FAIL, (
        "auto_status=true silently promoted an explicit FAIL to SUCCESS — "
        "this violates spec §2.6 fail-loud contract"
    )


@pytest.mark.asyncio
async def test_auto_status_preserves_explicit_retry_a1(tmp_path):
    """A1: auto_status=true must NOT override an explicit RETRY — fail-loud."""
    graph = Graph(
        name="test_a1_retry",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(
                id="gate",
                shape="box",
                prompt="do work",
                auto_status=True,
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            # After retries exhaust the engine converts RETRY→FAIL, so route on fail
            Edge(from_node="gate", to_node="exit", condition="outcome=fail"),
        ],
    )
    ctx = PipelineContext()
    engine = PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=HandlerRegistry(HandlerContext(backend=_RetryBackend())),
        logs_root=str(tmp_path),
    )
    await engine.run()
    # After exhausting retries the final outcome must not be SUCCESS
    assert engine.node_outcomes["gate"].status != StageStatus.SUCCESS, (
        "auto_status=true promoted an explicit RETRY/FAIL to SUCCESS — "
        "this violates spec §2.6 fail-loud contract"
    )


@pytest.mark.asyncio
async def test_auto_status_promotes_skipped_to_success_a1(tmp_path):
    """A1: auto_status=true synthesizes SUCCESS when handler writes no status (SKIPPED).

    SKIPPED is the engine's 'no-status-written' sentinel for the auto_status contract.
    """
    graph = Graph(
        name="test_a1_skipped",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(
                id="gate",
                shape="box",
                prompt="do work",
                auto_status=True,
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="exit"),
        ],
    )
    ctx = PipelineContext()
    engine = PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=HandlerRegistry(HandlerContext(backend=_SkippedBackend())),
        logs_root=str(tmp_path),
    )
    await engine.run()
    # SKIPPED (no-status) must be promoted to SUCCESS when auto_status=true
    assert engine.node_outcomes["gate"].status == StageStatus.SUCCESS, (
        "auto_status=true did not promote SKIPPED to SUCCESS — "
        "spec §2.6 / Appendix C requires SUCCESS synthesis for no-status case"
    )


@pytest.mark.asyncio
async def test_auto_status_quoted_true_promotes_skipped_a1(tmp_path):
    """A1: auto_status set as the quoted string 'true' also recognizes SKIPPED.

    DOT parser returns the string "true" for quoted attribute values.
    The engine must accept both True (bool) and "true" (string).
    """
    # Construct node with string "true" stored in the auto_status field directly
    node = Node(id="gate", shape="box", prompt="do work")
    # Simulate the DOT-quoted form: the parser returns "true" as a string,
    # which gets promoted to the auto_status field as a string.
    object.__setattr__(node, "auto_status", "true")

    graph = Graph(
        name="test_a1_quoted",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": node,
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="exit"),
        ],
    )
    ctx = PipelineContext()
    engine = PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=HandlerRegistry(HandlerContext(backend=_SkippedBackend())),
        logs_root=str(tmp_path),
    )
    await engine.run()
    assert engine.node_outcomes["gate"].status == StageStatus.SUCCESS, (
        "auto_status='true' (quoted string form) did not promote SKIPPED to SUCCESS"
    )


# ---------------------------------------------------------------------------
# A2 — goal_gate="true" (quoted) recognized by both lint and engine
# ---------------------------------------------------------------------------


def test_goal_gate_quoted_string_recognized_by_lint_a2():
    """A2: validation lint fires for goal_gate='true' (quoted string form).

    DOT quoted `goal_gate="true"` yields the string "true"; the lint rule
    must treat it the same as the unquoted boolean True.
    Spec §2.6 (goal_gate Boolean attribute).
    """
    # Build a node where goal_gate is the string "true" (quoted DOT form)
    node = Node(id="quality_gate", shape="box", prompt="check")
    object.__setattr__(node, "goal_gate", "true")  # simulate quoted DOT form

    graph = Graph(
        name="test_a2_lint",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "quality_gate": node,
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="quality_gate"),
            Edge(from_node="quality_gate", to_node="exit"),
        ],
    )
    diags = validate(graph)
    rule_names = {d.rule for d in diags}
    assert "goal_gate_has_retry" in rule_names, (
        "Lint rule 'goal_gate_has_retry' did not fire for goal_gate='true' (quoted string). "
        "Spec §2.6: goal_gate is Boolean; quoted 'true' must be recognized."
    )


@pytest.mark.asyncio
async def test_goal_gate_quoted_string_enforced_by_engine_a2(tmp_path):
    """A2: engine enforces goal_gate='true' (quoted string) at pipeline exit.

    When a goal_gate node fails and goal_gate is the string "true", the engine
    must mark the gate as unsatisfied and return FAIL — parity with bare True.
    """

    class AlwaysFailBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return Outcome(status=StageStatus.FAIL, failure_reason="gate fails")

    # goal_gate as string "true" (simulates DOT quoted form)
    gate_node = Node(id="quality_gate", shape="box", prompt="check")
    object.__setattr__(gate_node, "goal_gate", "true")

    graph = Graph(
        name="test_a2_engine",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "quality_gate": gate_node,
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="quality_gate"),
            Edge(from_node="quality_gate", to_node="exit", condition="outcome=fail"),
        ],
    )
    ctx = PipelineContext()
    engine = PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=HandlerRegistry(HandlerContext(backend=AlwaysFailBackend())),
        logs_root=str(tmp_path),
    )
    final = await engine.run()
    # The pipeline must fail because the goal gate is unsatisfied
    assert final.status == StageStatus.FAIL, (
        "Engine did not enforce goal_gate='true' (quoted string) — "
        "pipeline returned SUCCESS despite a failing gate node"
    )


# ---------------------------------------------------------------------------
# B6 — retry preset values match spec §3.5 table
# ---------------------------------------------------------------------------


def test_retry_preset_none_b6():
    """B6: 'none' preset — 1 attempt, no retries."""
    policy = RetryPolicy.from_preset("none")
    assert policy.max_attempts == 1


def test_retry_preset_standard_b6():
    """B6: 'standard' preset — 5 attempts, 200ms initial, factor 2.0.

    Spec §3.5: delays 200, 400, 800, 1600, 3200 ms.
    """
    policy = RetryPolicy.from_preset("standard")
    assert policy.max_attempts == 5, (
        f"standard preset: expected max_attempts=5, got {policy.max_attempts}"
    )
    # Default backoff: 200ms initial, factor 2.0
    assert policy.backoff.initial_delay_ms == 200.0
    assert policy.backoff.backoff_factor == 2.0
    # Spot-check first-attempt delay (no jitter)
    cfg_no_jitter = BackoffConfig(
        initial_delay_ms=policy.backoff.initial_delay_ms,
        backoff_factor=policy.backoff.backoff_factor,
        jitter=False,
    )
    assert cfg_no_jitter.delay_for_attempt(1) == 200.0


def test_retry_preset_aggressive_b6():
    """B6: 'aggressive' preset — 5 attempts, 500ms initial, factor 2.0.

    Spec §3.5: delays 500, 1000, 2000, 4000, 8000 ms.
    """
    policy = RetryPolicy.from_preset("aggressive")
    assert policy.max_attempts == 5, (
        f"aggressive preset: expected max_attempts=5, got {policy.max_attempts}"
    )
    assert policy.backoff.initial_delay_ms == 500.0, (
        f"aggressive preset: expected initial_delay_ms=500, got {policy.backoff.initial_delay_ms}"
    )
    assert policy.backoff.backoff_factor == 2.0
    cfg_no_jitter = BackoffConfig(
        initial_delay_ms=policy.backoff.initial_delay_ms,
        backoff_factor=policy.backoff.backoff_factor,
        jitter=False,
    )
    assert cfg_no_jitter.delay_for_attempt(1) == 500.0


def test_retry_preset_linear_b6():
    """B6: 'linear' preset — 3 attempts, 500ms initial, factor 1.0 (fixed delay).

    Spec §3.5: delays 500, 500, 500 ms.
    """
    policy = RetryPolicy.from_preset("linear")
    assert policy.max_attempts == 3, (
        f"linear preset: expected max_attempts=3, got {policy.max_attempts}"
    )
    assert policy.backoff.initial_delay_ms == 500.0, (
        f"linear preset: expected initial_delay_ms=500, got {policy.backoff.initial_delay_ms}"
    )
    assert policy.backoff.backoff_factor == 1.0
    cfg_no_jitter = BackoffConfig(
        initial_delay_ms=policy.backoff.initial_delay_ms,
        backoff_factor=policy.backoff.backoff_factor,
        jitter=False,
    )
    assert cfg_no_jitter.delay_for_attempt(1) == 500.0
    assert cfg_no_jitter.delay_for_attempt(2) == 500.0  # fixed


def test_retry_preset_patient_b6():
    """B6: 'patient' preset — 3 attempts, 2000ms initial, factor 3.0.

    Spec §3.5: delays 2000, 6000, 18000 ms.
    """
    policy = RetryPolicy.from_preset("patient")
    assert policy.max_attempts == 3, (
        f"patient preset: expected max_attempts=3, got {policy.max_attempts}"
    )
    assert policy.backoff.initial_delay_ms == 2000.0, (
        f"patient preset: expected initial_delay_ms=2000, got {policy.backoff.initial_delay_ms}"
    )
    assert policy.backoff.backoff_factor == 3.0, (
        f"patient preset: expected backoff_factor=3.0, got {policy.backoff.backoff_factor}"
    )
    cfg_no_jitter = BackoffConfig(
        initial_delay_ms=policy.backoff.initial_delay_ms,
        backoff_factor=policy.backoff.backoff_factor,
        jitter=False,
    )
    assert cfg_no_jitter.delay_for_attempt(1) == 2000.0
    assert cfg_no_jitter.delay_for_attempt(2) == 6000.0
    assert cfg_no_jitter.delay_for_attempt(3) == 18000.0


# ---------------------------------------------------------------------------
# B7 — fan_in publishes spec key parallel.fan_in.best_outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_in_publishes_best_outcome_key_b7(tmp_path):
    """B7: fan_in writes parallel.fan_in.best_outcome to context.

    Spec §4.8 (attractor-spec.md:876): the canonical key is
    ``parallel.fan_in.best_outcome``.  The implementation also keeps the
    legacy ``parallel.fan_in.best_status`` for compatibility; both must be
    present after fan_in executes.
    """
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    graph = Graph(name="test_b7", nodes={"fan_in": node}, edges=[])
    ctx = PipelineContext()
    ctx.set(
        "parallel.results",
        [{"node_id": "branch_a", "status": "success", "notes": ""}],
    )

    outcome = await handler.execute(node, ctx, graph, str(tmp_path))

    assert outcome.status == StageStatus.SUCCESS, (
        f"fan_in handler failed unexpectedly: {outcome}"
    )

    # Spec key must be present in context
    best_outcome_ctx = ctx.get("parallel.fan_in.best_outcome")
    assert best_outcome_ctx is not None, (
        "fan_in did not set 'parallel.fan_in.best_outcome' in context — "
        "spec §4.8 requires this key"
    )
    assert best_outcome_ctx == "success", (
        f"parallel.fan_in.best_outcome expected 'success', got {best_outcome_ctx!r}"
    )

    # Spec key must also appear in context_updates
    assert outcome.context_updates is not None
    assert "parallel.fan_in.best_outcome" in outcome.context_updates, (
        "fan_in did not include 'parallel.fan_in.best_outcome' in Outcome.context_updates"
    )
    assert outcome.context_updates["parallel.fan_in.best_outcome"] == "success"

    # Legacy key still present (backward compat)
    assert ctx.get("parallel.fan_in.best_status") == "success", (
        "fan_in removed legacy 'parallel.fan_in.best_status' — it must be kept for compat"
    )
