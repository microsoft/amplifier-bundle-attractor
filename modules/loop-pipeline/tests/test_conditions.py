"""Tests for condition expression language.

Spec coverage: CEXPR-001–011 (Section 10).
"""

from amplifier_module_loop_pipeline.conditions import evaluate_condition
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


def test_outcome_equals():
    """outcome=success matches success status."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("outcome=success", outcome, ctx) is True
    assert evaluate_condition("outcome=fail", outcome, ctx) is False


def test_not_equals():
    """outcome!=fail when outcome is success."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("outcome!=fail", outcome, ctx) is True
    assert evaluate_condition("outcome!=success", outcome, ctx) is False


def test_context_lookup():
    """context.key=value matches context store."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("last_stage", "plan")
    assert evaluate_condition("context.last_stage=plan", outcome, ctx) is True
    assert evaluate_condition("context.last_stage=implement", outcome, ctx) is False


def test_and_clauses():
    """Multiple clauses joined by && must all be true."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("tests_pass", "true")
    assert (
        evaluate_condition("outcome=success && context.tests_pass=true", outcome, ctx)
        is True
    )
    ctx.set("tests_pass", "false")
    assert (
        evaluate_condition("outcome=success && context.tests_pass=true", outcome, ctx)
        is False
    )


def test_empty_condition_is_true():
    """Empty condition always evaluates to true."""
    outcome = Outcome(status=StageStatus.FAIL)
    ctx = PipelineContext()
    assert evaluate_condition("", outcome, ctx) is True


def test_missing_context_key_is_empty_string():
    """Missing context key resolves to empty string."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("context.missing=value", outcome, ctx) is False
    assert evaluate_condition("context.missing=", outcome, ctx) is True


def test_whitespace_tolerance():
    """Whitespace around operators and clauses is tolerated."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("x", "1")
    assert evaluate_condition(" outcome = success ", outcome, ctx) is True
    assert (
        evaluate_condition("outcome = success && context.x = 1", outcome, ctx) is True
    )


# --- preferred_label key resolution ---


def test_preferred_label_equals():
    """preferred_label key resolves to outcome.preferred_label (CEXPR-005)."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="Fix")
    ctx = PipelineContext()
    assert evaluate_condition("preferred_label=Fix", outcome, ctx) is True
    assert evaluate_condition("preferred_label=Ship", outcome, ctx) is False


def test_preferred_label_not_equals():
    """preferred_label != comparison."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="Fix")
    ctx = PipelineContext()
    assert evaluate_condition("preferred_label!=Ship", outcome, ctx) is True
    assert evaluate_condition("preferred_label!=Fix", outcome, ctx) is False


def test_preferred_label_none_resolves_to_empty():
    """None preferred_label resolves to empty string."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label=None)
    ctx = PipelineContext()
    assert evaluate_condition("preferred_label=", outcome, ctx) is True
    assert evaluate_condition("preferred_label=Fix", outcome, ctx) is False


# --- context key resolution variants ---


def test_context_key_with_full_prefix():
    """context.X tries both 'context.X' and 'X' in context store (CEXPR-006)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    # Set with bare key — context.last_stage should still find it
    ctx.set("last_stage", "plan")
    assert evaluate_condition("context.last_stage=plan", outcome, ctx) is True


def test_context_key_with_full_qualified_name():
    """If context stores 'context.X', that takes priority."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("context.stage", "qualified")
    ctx.set("stage", "bare")
    assert evaluate_condition("context.stage=qualified", outcome, ctx) is True


def test_context_numeric_value_coerced_to_string():
    """Non-string context values are coerced to strings for comparison."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("count", 42)
    assert evaluate_condition("context.count=42", outcome, ctx) is True


# --- outcome status values ---


def test_outcome_status_values():
    """All StageStatus values are accessible via outcome key."""
    ctx = PipelineContext()
    for status in StageStatus:
        outcome = Outcome(status=status)
        assert evaluate_condition(f"outcome={status.value}", outcome, ctx) is True


# --- multiple && clauses ---


def test_three_clauses_all_true():
    """Three AND-joined clauses, all true."""
    # With preferred_label set, outcome resolves to preferred_label value
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="go")
    ctx = PipelineContext()
    ctx.set("ready", "true")
    assert (
        evaluate_condition(
            "outcome=go && preferred_label=go && context.ready=true",
            outcome,
            ctx,
        )
        is True
    )


def test_three_clauses_one_false():
    """Three AND-joined clauses, one false → overall false."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="go")
    ctx = PipelineContext()
    ctx.set("ready", "false")
    assert (
        evaluate_condition(
            "outcome=go && preferred_label=go && context.ready=true",
            outcome,
            ctx,
        )
        is False
    )


# --- edge cases ---


def test_none_condition_is_true():
    """None condition (not just empty string) is true."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    # Type-wise this would be str, but if someone passes whitespace-only
    assert evaluate_condition("   ", outcome, ctx) is True


def test_value_with_equals_sign():
    """Value containing = is handled (split on first = only)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("expr", "a=b")
    assert evaluate_condition("context.expr=a=b", outcome, ctx) is True


def test_not_equals_with_missing_key():
    """Missing key != non-empty value → True (empty != 'something')."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("context.missing!=value", outcome, ctx) is True


def test_not_equals_with_missing_key_empty_value():
    """Missing key != '' → False (empty != empty)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("context.missing!=", outcome, ctx) is False


# --- Bare key truthiness (L-20, Spec Section 10.5) ---


def test_bare_key_truthy_context_value():
    """Bare key evaluates to truthy when context value is non-empty (L-20)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("approved", "true")
    assert evaluate_condition("approved", outcome, ctx) is True


def test_bare_key_falsy_missing():
    """Bare key evaluates to falsy when context key is missing (L-20)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("nonexistent_key", outcome, ctx) is False


def test_bare_key_falsy_empty_string():
    """Bare key evaluates to falsy when context value is empty string (L-20)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("flag", "")
    assert evaluate_condition("flag", outcome, ctx) is False


def test_bare_key_in_conjunction():
    """Bare key works alongside = clauses in && expressions (L-20)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("ready", "yes")
    assert evaluate_condition("outcome=success && ready", outcome, ctx) is True
    ctx.set("ready", "")
    assert evaluate_condition("outcome=success && ready", outcome, ctx) is False


def test_bare_key_outcome():
    """Bare 'outcome' key is truthy (resolves to status string) (L-20)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    assert evaluate_condition("outcome", outcome, ctx) is True


# --- outcome key resolves preferred_label for custom routing ---


def test_outcome_resolves_preferred_label_custom_value():
    """outcome=yes matches when preferred_label='yes' (custom routing)."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label="yes")
    ctx = PipelineContext()
    assert evaluate_condition("outcome=yes", outcome, ctx) is True


def test_outcome_resolves_status_when_no_preferred_label():
    """outcome=success still works when preferred_label is None."""
    outcome = Outcome(status=StageStatus.SUCCESS, preferred_label=None)
    ctx = PipelineContext()
    assert evaluate_condition("outcome=success", outcome, ctx) is True


def test_outcome_resolves_status_fail_when_no_preferred_label():
    """outcome=fail matches standard status when preferred_label is None."""
    outcome = Outcome(status=StageStatus.FAIL, preferred_label=None)
    ctx = PipelineContext()
    assert evaluate_condition("outcome=fail", outcome, ctx) is True


# --- spec §10.7 regression: && is the only AND separator ---


def test_ampersand_and_both_clauses_must_pass():
    """Spec §10.7: '&&' is the ONLY AND separator — both clauses must pass (B10)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("state", "ready")
    # Both clauses pass → True
    assert evaluate_condition("outcome=success && context.state=ready", outcome, ctx) is True
    # Second clause fails → False
    ctx.set("state", "busy")
    assert evaluate_condition("outcome=success && context.state=ready", outcome, ctx) is False


def test_comma_in_value_is_literal_not_and_separator():
    """Spec §10.7: comma is NOT an AND separator — a value containing a comma
    is treated as ONE literal clause, not split into two clauses (B10)."""
    outcome = Outcome(status=StageStatus.SUCCESS)
    ctx = PipelineContext()
    ctx.set("tag", "a,b")
    # Single clause: key=context.tag, value=a,b — literal match
    assert evaluate_condition("context.tag=a,b", outcome, ctx) is True
    # Different value → no match
    ctx.set("tag", "a,c")
    assert evaluate_condition("context.tag=a,b", outcome, ctx) is False
