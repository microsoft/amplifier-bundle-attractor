"""support#381 TDD — Codergen (LLM) node failure must capture prompt + response.

support#381 generalizes the ``failed_step`` structured-detail pattern
(previously ToolHandler-only, see test_tool_failure_capture.py) to
CodergenHandler — the highest-value gap since it covers every LLM node in
every pipeline. Before this fix, ``Outcome.failed_step`` was ``None`` on
every codergen failure path (backend exception, goal_gate parse failure),
leaving consumers with only notes/failure_reason and no prompt/response
context to diagnose the failure with.

Payload shape (``Outcome.failed_step``), mirroring ToolHandler's
command/exit_code/stdout_tail/stderr_tail shape with LLM-appropriate
analogs:
    {
        "prompt":        str,        # truncated to 500 chars
        "response_tail": str,        # last <=2000 chars; empty string, NOT None
        "error":         str | None,
    }

When the total JSON-serialised size of ``failed_step`` exceeds 8 KiB,
``response_tail`` is dropped first and
``failed_step["verification_gap"]["log_filtered"]`` is set to ``True``
(mirrors ToolHandler's truncation discipline).
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler
from amplifier_module_loop_pipeline.outcome import StageStatus


def _make_graph() -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# ---------------------------------------------------------------------------
# Test 1: failed_step populated on backend exception
# ---------------------------------------------------------------------------


class _RaisingBackend:
    """Backend that always raises, simulating an infrastructure failure."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        raise RuntimeError("backend exploded")


@pytest.mark.asyncio
async def test_codergen_exception_captures_failed_step(tmp_path):
    """support#381 — Backend exception; failed_step has prompt, error, empty response_tail."""
    node = Node(id="bad", prompt="Write the thing")
    handler = CodergenHandler(backend=_RaisingBackend())
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.FAIL

    fs = outcome.failed_step
    assert fs is not None, "failed_step must be populated on exception failure"

    assert "Write the thing" in fs["prompt"]
    assert fs["error"] is not None
    assert "backend exploded" in fs["error"]
    # No response was ever produced on the exception path.
    assert fs["response_tail"] == ""


# ---------------------------------------------------------------------------
# Test 2: failed_step populated on goal_gate parse-failure (plain-prose RETRY
# does not count as failed_step-worthy; only an actual FAIL verdict does)
# ---------------------------------------------------------------------------


class _EchoBackend:
    """Backend that returns a fixed string response (not an Outcome)."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return self._response


@pytest.mark.asyncio
async def test_codergen_goal_gate_fail_captures_failed_step(tmp_path):
    """support#381 — A goal_gate=true node whose verdict-recovery ladder

    returns FAIL must carry failed_step with prompt + response context,
    not just notes/failure_reason.
    """
    # An explicit JSON verdict of status=fail routes through _parse_outcome
    # to a FAIL Outcome (is_explicit=True), which previously left
    # failed_step as None.
    response = json.dumps({"status": "fail", "notes": "the gate rejected this"})
    node = Node(id="gated", prompt="Do the gated thing", attrs={"goal_gate": "true"})
    handler = CodergenHandler(backend=_EchoBackend(response))
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.FAIL

    fs = outcome.failed_step
    assert fs is not None, "failed_step must be populated on goal_gate FAIL"
    assert "Do the gated thing" in fs["prompt"]
    assert "the gate rejected this" in fs["response_tail"]


# ---------------------------------------------------------------------------
# Test 3: truncation fires -> verification_gap.log_filtered = True
# ---------------------------------------------------------------------------


def test_codergen_failed_step_truncation_sets_log_filtered():
    """support#381 — Truncation (8 KiB cap) sets verification_gap.log_filtered=True.

    Note: response_text is already capped to the last 2000 chars inside
    _build_failed_step before the 8 KiB total-size check runs (unlike
    ToolHandler, which caps the *total* first and drops stdout_tail only
    if still over budget) — so a large response_text alone can't trigger
    the cap. Use a large `error` string instead, which is not pre-truncated.
    """
    from amplifier_module_loop_pipeline.handlers.codergen import _build_failed_step

    big_error = "E" * 9000

    fs = _build_failed_step(
        prompt="a short prompt",
        response_text="a short response",
        error=big_error,
    )

    vgap = fs.get("verification_gap", {})
    assert vgap.get("log_filtered") is True, (
        f"Expected verification_gap.log_filtered=True, got: {fs!r}"
    )
    assert "response_tail" not in fs


# ---------------------------------------------------------------------------
# Test 5: success path — failed_step is None (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codergen_success_has_no_failed_step(tmp_path):
    """support#381 regression — Success path must NOT populate failed_step."""
    node = Node(id="ok", prompt="Do the thing")
    handler = CodergenHandler(backend=_EchoBackend("all good"))
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.SUCCESS
    assert outcome.failed_step is None, (
        f"failed_step should be None on success, got {outcome.failed_step!r}"
    )
