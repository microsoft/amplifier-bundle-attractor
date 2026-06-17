"""FIX 3A — verdict node prose-then-JSON no longer coerced to SUCCESS.

After the fix (backend.py `_parse_outcome`): when the model emits prose
followed by a JSON verdict object, the LAST balanced `{...}` block is
extracted and, if it contains a recognised status, that status is honoured
rather than silently coerced to SUCCESS.

Invariant: an explicit FAIL/RETRY verdict MUST NOT be silently dropped.
Pure-JSON and fenced-JSON paths are unchanged; genuine plain prose with no
status-JSON still returns SUCCESS; prose containing a JSON object without a
recognised status also returns SUCCESS.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

from amplifier_module_loop_pipeline.backend import AmplifierBackend, _parse_outcome  # noqa: E402
from amplifier_module_loop_pipeline.context import PipelineContext  # noqa: E402
from amplifier_module_loop_pipeline.graph import Node  # noqa: E402
from amplifier_module_loop_pipeline.outcome import StageStatus  # noqa: E402

# ---------------------------------------------------------------------------
# Specimens
# ---------------------------------------------------------------------------
_PROSE_THEN_JSON_FAIL = "Here's my verdict:\n" + json.dumps(
    {
        "status": "fail",
        "failure_reason": "Tests did not pass",
        "notes": "3 of 5 assertions failed",
    }
)

_PROSE_THEN_JSON_RETRY = "Let me explain my decision:\n" + json.dumps(
    {
        "status": "retry",
        "failure_reason": "Incomplete implementation",
        "preferred_label": "needs_more_work",
    }
)

_PLAIN_PROSE = "The work looks good overall, no JSON here at all."

_PROSE_WITH_NON_STATUS_JSON = "Here is some context:\n" + json.dumps(
    {"key": "value", "count": 42}
)


# ---------------------------------------------------------------------------
# Helper: minimal GenerateResult with no tool-call steps
# ---------------------------------------------------------------------------


def _make_generate_result(text: str) -> Any:
    """Build a minimal unified_llm.GenerateResult for mocking generate()."""
    usage = unified_llm.Usage(
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    )
    response = unified_llm.Response(
        id="resp-mock",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
    )
    return unified_llm.GenerateResult(
        text=text,
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
        total_usage=usage,
        steps=[],  # ← no report_outcome tool calls in steps
        response=response,
    )


# ---------------------------------------------------------------------------
# Stub coordinator — no session.spawn capability (forces Path B tool loop)
# ---------------------------------------------------------------------------


class _NoSpawnCoordinator:
    session: Any = type("S", (), {"config": {}})()
    config: dict = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


# ---------------------------------------------------------------------------
# Test 1: Direct unit tests on _parse_outcome (FIXED behavior)
# ---------------------------------------------------------------------------


def test_parse_outcome_prose_then_json_fail_recovered():
    """_parse_outcome recovers FAIL verdict from prose-then-JSON response.

    FIXED BEHAVIOR (FIX 3A):
        The LAST balanced {...} in the stripped string is extracted.
        It contains "status": "fail" which maps to StageStatus.FAIL via
        _STATUS_MAP.  _parse_outcome now returns Outcome(status=FAIL) and
        emits a logger.warning that the verdict was recovered.
    """
    result = _parse_outcome(_PROSE_THEN_JSON_FAIL)

    assert result.status == StageStatus.FAIL, (
        f"Expected FAIL after fix, got {result.status!r}. FIX 3A may not be applied."
    )
    assert result.failure_reason == "Tests did not pass", (
        f"Expected failure_reason from embedded JSON, got {result.failure_reason!r}"
    )
    assert result.notes == "3 of 5 assertions failed", (
        f"Expected notes from embedded JSON, got {result.notes!r}"
    )


def test_parse_outcome_prose_then_json_retry_recovered():
    """_parse_outcome recovers RETRY verdict from prose-then-JSON response."""
    result = _parse_outcome(_PROSE_THEN_JSON_RETRY)

    assert result.status == StageStatus.RETRY, (
        f"Expected RETRY after fix, got {result.status!r}. FIX 3A may not be applied."
    )
    assert result.failure_reason == "Incomplete implementation", (
        f"Expected failure_reason from embedded JSON, got {result.failure_reason!r}"
    )
    assert result.preferred_label == "needs_more_work", (
        f"Expected preferred_label from embedded JSON, got {result.preferred_label!r}"
    )


def test_parse_outcome_clean_json_still_works():
    """Sanity check: bare JSON (no prose prefix) is still parsed correctly."""
    payload = json.dumps({"status": "fail", "failure_reason": "deliberate"})
    result = _parse_outcome(payload)
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "deliberate"


def test_parse_outcome_fenced_json_still_works():
    """Sanity check: ```json ... ``` fenced JSON is still parsed correctly."""
    payload = (
        "```json\n"
        + json.dumps({"status": "fail", "failure_reason": "fenced"})
        + "\n```"
    )
    result = _parse_outcome(payload)
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "fenced"


def test_parse_outcome_plain_prose_no_json_is_success():
    """Genuine plain prose with no JSON object returns SUCCESS (spec §4.5)."""
    result = _parse_outcome(_PLAIN_PROSE)
    assert result.status == StageStatus.SUCCESS, (
        f"Plain prose should still return SUCCESS, got {result.status!r}"
    )


def test_parse_outcome_prose_with_non_status_json_is_success():
    """Prose containing a JSON object without a recognised status → SUCCESS.

    The embedded JSON has keys "key" and "count" but no "status" key.
    The recovery logic should find the JSON, fail the status check, and
    fall through to the plain-text SUCCESS default.
    """
    result = _parse_outcome(_PROSE_WITH_NON_STATUS_JSON)
    assert result.status == StageStatus.SUCCESS, (
        f"Prose with non-status JSON should return SUCCESS, got {result.status!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: Full Path-B backend run (monkeypatched unified_llm.generate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_tool_loop_prose_then_json_fail_recovered(monkeypatch):
    """Path-B backend correctly propagates FAIL from prose-then-JSON response.

    FIXED BEHAVIOR (FIX 3A):
        The decision logic at backend.py:612-637 passes prose-then-JSON to
        _parse_outcome() (line 633).  _parse_outcome now recovers the
        embedded FAIL verdict and returns Outcome(status=FAIL).
    """

    async def _fake_generate(**kwargs: Any) -> Any:
        return _make_generate_result(_PROSE_THEN_JSON_FAIL)

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = _NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel enables Path B (tool loop)
    )
    node = Node(
        id="verdict_node",
        llm_model="test-model",
        attrs={"llm_provider": "test"},
    )
    context = PipelineContext()

    outcome = await backend.run(node, "Evaluate the implementation", context)

    assert outcome.status == StageStatus.FAIL, (
        f"Expected FAIL after fix, got {outcome.status!r}. "
        "FIX 3A may not be applied or Path B routing is broken."
    )
    assert outcome.failure_reason == "Tests did not pass", (
        f"Expected failure_reason from embedded JSON, got {outcome.failure_reason!r}"
    )


@pytest.mark.asyncio
async def test_backend_tool_loop_clean_json_fail_correctly_parsed(monkeypatch):
    """Sanity: bare JSON FAIL (no prose) is still parsed correctly on Path B."""

    async def _fake_generate(**kwargs: Any) -> Any:
        return _make_generate_result(
            json.dumps({"status": "fail", "failure_reason": "clean json fail"})
        )

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = _NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
    )
    node = Node(id="judge", llm_model="test-model", attrs={"llm_provider": "test"})
    outcome = await backend.run(node, "Judge", PipelineContext())

    # Clean JSON should still work correctly (this verifies the parser is fine)
    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "clean json fail"
