"""Contract tests for the `orchestrator:complete` completion envelope.

EXTENSIONS.md §35 ("Spawned-Agent Outcome Transport and `report_outcome`
Ordering Barrier").

WHY THIS FILE EXISTS (issue #285): a spawned child's semantic verdict reaches
its parent through exactly one channel — the `orchestrator:complete` event's
`metadata.report_outcome`.  Foundation's `PreparedBundle.spawn` registers a
temporary hook on that event and copies `metadata` verbatim into the spawn
result dict; loop-pipeline's `_outcome_from_spawn_result` then reads
`metadata["report_outcome"]` and is the ONLY place an `is_explicit=True`
outcome can come from on the spawn path.

Before #285, loop-agent never emitted this event at all, so every spawned
child arrived at its parent as a verdict-less status-only completion and the
parent recorded `notes="Child session completed with empty final message",
is_explicit=false` — no matter what the child had actually reported.

The two layers of the envelope are deliberately separate and are asserted
separately below:

  * top-level `status` — LIFECYCLE ONLY (how the invocation ended)
  * `metadata.report_outcome` — the SEMANTIC verdict (what the node decided)
"""

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.events import ORCHESTRATOR_COMPLETE
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult
from amplifier_module_loop_agent import AgentOrchestrator

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(*calls, text: str = "") -> ChatResponse:
    """A response carrying tool calls, optionally alongside assistant text."""
    return ChatResponse(
        content=[{"type": "text", "text": text}] if text else [],
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls
        ],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class _ReportOutcomeDouble:
    """Faithful stand-in for `tool-report-outcome`'s `ReportOutcomeTool`.

    loop-agent does not depend on the tool package, so the two behaviors the
    envelope actually relies on are reproduced here verbatim:

      * a SUCCESSFUL call overwrites `last_outcome` (last-write-wins);
      * a call that fails validation returns an unsuccessful ToolResult and
        leaves `last_outcome` untouched.

    The real tool is exercised end-to-end against this same contract in
    ``modules/pipeline-runner/tests/test_spawn_report_outcome_transport.py``.
    """

    name = "report_outcome"
    description = "Report the outcome of your work."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }

    def __init__(self) -> None:
        self.last_outcome: dict | None = None

    async def execute(self, input: dict) -> ToolResult:
        status = input.get("status")
        if status not in {"success", "fail", "partial_success", "retry"}:
            return ToolResult(
                success=False,
                output=f"Invalid status: {status!r}",
                error={"message": "invalid status"},
            )
        self.last_outcome = {k: v for k, v in input.items() if v is not None}
        return ToolResult(success=True, output={"message": f"Outcome: {status}"})


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


def _make_orchestrator(responses, tools=None, config=None):
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses)
    providers = {"test": provider}
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    orch = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orch, MagicMock(), providers, dict(tools or {}), _make_hooks()


def _completions(hooks) -> list[dict]:
    return [d for name, d in hooks._emitted if name == ORCHESTRATOR_COMPLETE]


# ---------------------------------------------------------------------------
# The transport itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_outcome_verdict_rides_the_completion_envelope():
    """A child's report_outcome lands in `metadata.report_outcome`.

    This is the #285 defect in one assertion: at origin/main no
    `orchestrator:complete` was emitted at all, so this list was empty and the
    verdict never crossed the spawn boundary.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                ("tc1", "report_outcome", {"status": "fail", "preferred_label": "escalate"})
            )
        ],
        tools={"report_outcome": report},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    completions = _completions(hooks)
    assert len(completions) == 1, "exactly one completion envelope per invocation"
    envelope = completions[0]
    assert envelope["orchestrator"] == "loop-agent"
    assert envelope["status"] == "success"
    assert envelope["metadata"]["report_outcome"] == {
        "status": "fail",
        "preferred_label": "escalate",
    }


@pytest.mark.asyncio
async def test_lifecycle_status_is_not_the_semantic_verdict():
    """A FAIL verdict does NOT make the lifecycle status 'fail'.

    The two layers stay separate: the invocation completed cleanly (lifecycle
    `success`) while the node's verdict is `fail`.  Collapsing them would make
    every reporting child look like a broken session.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_tool_response(("tc1", "report_outcome", {"status": "fail"}))],
        tools={"report_outcome": report},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    envelope = _completions(hooks)[0]
    assert envelope["status"] == "success"
    assert envelope["metadata"]["report_outcome"]["status"] == "fail"


@pytest.mark.asyncio
async def test_execute_still_returns_the_final_string():
    """§35 Compatibility: the `execute(...) -> str` contract is unchanged."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("the answer")]
    )
    assert await orch.execute("go", ctx, provs, tools, hooks) == "the answer"


# ---------------------------------------------------------------------------
# Fail-closed: no verdict must never look like a verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_report_outcome_leaves_metadata_empty():
    """A child that never reports carries NO verdict — `metadata == {}`.

    This is what keeps a downstream goal gate fail-closed (EXTENSIONS.md §25):
    the parent sees a status-only completion and records `is_explicit=False`.
    """
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("I did the thing, all good!")]
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    envelope = _completions(hooks)[0]
    assert envelope["status"] == "success"
    assert envelope["metadata"] == {}


@pytest.mark.asyncio
async def test_a_rejected_report_is_not_promoted():
    """A report that fails validation leaves no verdict behind."""
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(("tc1", "report_outcome", {"status": "nonsense"})),
            _text_response("oh well"),
        ],
        tools={"report_outcome": report},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    assert report.last_outcome is None
    assert _completions(hooks)[0]["metadata"] == {}


# ---------------------------------------------------------------------------
# Per-invocation isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verdict_does_not_leak_into_the_next_invocation():
    """`last_outcome` is reset before each invocation.

    The tool instance is mounted once per session and the session is reused
    across `execute()` calls, so without the reset invocation #2 would report
    invocation #1's verdict as if the child had just asserted it.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(("tc1", "report_outcome", {"status": "fail"})),
            _text_response("nothing to report this time"),
        ],
        tools={"report_outcome": report},
    )

    await orch.execute("first", ctx, provs, tools, hooks)
    await orch.execute("second", ctx, provs, tools, hooks)

    first, second = _completions(hooks)
    assert first["metadata"]["report_outcome"]["status"] == "fail"
    assert second["metadata"] == {}, "invocation 2 inherited invocation 1's verdict"


@pytest.mark.asyncio
async def test_turn_count_is_per_invocation():
    """`turn_count` counts THIS invocation's provider calls, not the session's."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(("tc1", "noop", {})),
            _text_response("done"),
            _text_response("second invocation"),
        ],
        tools={"noop": _make_noop_tool()},
    )

    await orch.execute("first", ctx, provs, tools, hooks)
    await orch.execute("second", ctx, provs, tools, hooks)

    first, second = _completions(hooks)
    assert first["turn_count"] == 2
    assert second["turn_count"] == 1


def _make_noop_tool():
    tool = MagicMock()
    tool.name = "noop"
    tool.description = "no-op"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    return tool


# ---------------------------------------------------------------------------
# Last-declared-report-wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_successful_report_in_a_batch_wins():
    """Two valid reports in one batch: the LAST declared one is transported."""
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                ("tc1", "report_outcome", {"status": "success", "preferred_label": "first"}),
                ("tc2", "report_outcome", {"status": "fail", "preferred_label": "second"}),
            )
        ],
        tools={"report_outcome": report},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    verdict = _completions(hooks)[0]["metadata"]["report_outcome"]
    assert verdict["status"] == "fail"
    assert verdict["preferred_label"] == "second"


@pytest.mark.asyncio
async def test_a_later_invalid_report_does_not_erase_a_valid_one():
    """§35: 'A later report that fails ... does not erase the prior valid report.'"""
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                ("tc1", "report_outcome", {"status": "fail", "preferred_label": "escalate"}),
                ("tc2", "report_outcome", {"status": "not-a-status"}),
            )
        ],
        tools={"report_outcome": report},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    verdict = _completions(hooks)[0]["metadata"]["report_outcome"]
    assert verdict == {"status": "fail", "preferred_label": "escalate"}


# ---------------------------------------------------------------------------
# Terminal report path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_report_terminates_the_invocation():
    """A successful report ends the invocation — no further provider call.

    The provider below would raise if a second call were made, so this asserts
    the barrier rather than merely observing a convenient response list.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                ("tc1", "report_outcome", {"status": "success"}),
                text="closing prose emitted alongside the verdict",
            ),
            AssertionError("a second provider call was made after report_outcome"),
        ],
        tools={"report_outcome": report},
    )

    result = await orch.execute("go", ctx, provs, tools, hooks)

    assert result == "closing prose emitted alongside the verdict"
    assert provs["test"].complete.await_count == 1
    assert _completions(hooks)[0]["turn_count"] == 1


# ---------------------------------------------------------------------------
# Interrupted invocations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_raising_invocation_still_emits_exactly_one_envelope():
    """An exception must not swallow the envelope — the spawn boundary needs it."""
    boom = RuntimeError("provider exploded")
    orch, ctx, provs, tools, hooks = _make_orchestrator(responses=[boom])

    with pytest.raises(RuntimeError, match="provider exploded"):
        await orch.execute("go", ctx, provs, tools, hooks)

    completions = _completions(hooks)
    assert len(completions) == 1
    assert completions[0]["status"] == "incomplete"
    assert completions[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_a_cancelled_invocation_reports_cancelled_and_no_verdict():
    """Cancellation is a lifecycle state, and never promotes a partial report."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[asyncio.CancelledError()]
    )

    with pytest.raises(asyncio.CancelledError):
        await orch.execute("go", ctx, provs, tools, hooks)

    completions = _completions(hooks)
    assert len(completions) == 1
    assert completions[0]["status"] == "cancelled"
    assert completions[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_a_turn_limited_invocation_is_incomplete():
    """max_turns is a wall, not a completion — lifecycle status says so."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("never reached")],
        config={"max_turns": 1},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    envelope = _completions(hooks)[0]
    assert envelope["status"] == "incomplete"
    assert envelope["metadata"] == {}
