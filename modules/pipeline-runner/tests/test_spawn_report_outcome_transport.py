"""The child->parent `report_outcome` verdict transport, end to end (issue #285).

This is the ONE test file in the repo where all three parties to the transport
are the REAL implementations at the same time:

  * PRODUCER  -- `amplifier_module_loop_agent.AgentOrchestrator`, which emits the
    `orchestrator:complete` envelope (EXTENSIONS.md §35);
  * VERDICT TOOL -- `amplifier_module_tool_report_outcome.ReportOutcomeTool`, the
    thing the child actually calls;
  * CONSUMER  -- `amplifier_module_loop_pipeline.backend.AmplifierBackend`, whose
    `_outcome_from_spawn_result` is the only place a spawn-path
    `is_explicit=True` outcome can come from.

Only two things are doubles, and both are deliberate:

  * the LLM provider (scripted responses -- no network in CI);
  * the SPAWN PLUMBING, which reproduces foundation's
    `PreparedBundle.spawn` result assembly verbatim (see `_spawn_result_from`
    below).  Standing up a real `AmplifierSession` child would test
    foundation, not this repo's seam.

Why it lives in `pipeline-runner`: this module already sits above both halves
(it owns `make_spawn_fn`), and issue #285 asked specifically for "a
pipeline-runner-level regression asserting is_explicit == true and a non-null
preferred_label after a child agent *actually calls* report_outcome -- i.e.
proving the envelope **travelled**, not merely that the parent would honor one
if it had."

RED against origin/main: at `701edc7` loop-agent emitted no
`orchestrator:complete` at all, so `metadata` was always `{}` and the parent
recorded `notes="Child session completed with empty final message",
is_explicit=False`.
"""

from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.events import ORCHESTRATOR_COMPLETE
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_tool_report_outcome import ReportOutcomeTool

# ---------------------------------------------------------------------------
# Child-side harness: a real loop-agent invocation with a scripted provider
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(*calls, text: str = "") -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}] if text else [],
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls
        ],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class _CapturingHooks:
    """Stands in for the child session's hook registry.

    Mirrors what foundation's `PreparedBundle.spawn` does: it registers a
    temporary `orchestrator:complete` subscriber and keeps the last payload.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.completion: dict[str, Any] = {}

    async def emit(self, event: str, data: dict) -> Any:
        self.events.append((event, data))
        if event == ORCHESTRATOR_COMPLETE:
            self.completion.update(data)
        return MagicMock(action="continue")


async def _run_child(responses) -> dict[str, Any]:
    """Run one REAL loop-agent invocation and return a foundation-shaped result.

    The returned dict's four keys and their defaults are copied from
    `amplifier_foundation/bundle/_prepared.py::PreparedBundle.spawn` -- the
    `status`/`turn_count`/`metadata` fallbacks are foundation's own, and the
    `"success"` default for `status` is precisely why a child that emitted NO
    completion envelope still looked like a clean completion to the parent.
    """
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses)
    hooks = _CapturingHooks()
    report_tool = ReportOutcomeTool(config={}, coordinator=None)

    orch = AgentOrchestrator(
        coordinator=MagicMock(),
        config={"system_prompt": "You are a test coding agent."},
    )
    output = await orch.execute(
        "do the work",
        MagicMock(),
        {"test": provider},
        {"report_outcome": report_tool},
        hooks,
    )

    return {
        "output": output,
        "session_id": "child-session-1",
        "status": hooks.completion.get("status", "success"),
        "turn_count": hooks.completion.get("turn_count", 1),
        "metadata": hooks.completion.get("metadata", {}),
    }


# ---------------------------------------------------------------------------
# Parent-side harness: the real AmplifierBackend over a canned spawn result
# ---------------------------------------------------------------------------


class _Session:
    config: ClassVar[dict[str, Any]] = {}


class _Coordinator:
    """Minimal coordinator exposing the `session.spawn` capability."""

    def __init__(self, spawn_result: dict[str, Any]) -> None:
        self._spawn_result = spawn_result
        self.session = _Session()
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}}
                }
            }
        }

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs: Any) -> dict[str, Any]:
        return self._spawn_result


async def _parent_outcome(spawn_result: dict[str, Any], **node_attrs: Any):
    backend = AmplifierBackend(
        coordinator=_Coordinator(spawn_result),
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = Node(
        id="intake",
        prompt="Do the work",
        attrs={"llm_provider": "anthropic", **node_attrs},
    )
    return await backend.run(node, "Do the work", PipelineContext())


# ---------------------------------------------------------------------------
# 1. The transport (the #285 defect)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_verdict_survives_the_spawn_boundary():
    """A child's report_outcome reaches the parent as an EXPLICIT outcome.

    This is the exact incident from #231/#285: the child calls report_outcome
    with a real verdict and emits no closing prose.  Before the transport
    shipped, the parent recorded
    `notes="Child session completed with empty final message"`,
    `is_explicit=False`, `preferred_label=None`.
    """
    result = await _run_child(
        [
            _tool_response(
                (
                    "tc1",
                    "report_outcome",
                    {
                        "status": "fail",
                        "preferred_label": "escalate",
                        "failure_reason": "probe verdict",
                        "notes": "transport probe verdict",
                    },
                )
            )
        ]
    )

    # The envelope travelled.
    assert result["metadata"]["report_outcome"]["preferred_label"] == "escalate"

    outcome = await _parent_outcome(result)

    assert outcome.is_explicit is True
    assert outcome.status is StageStatus.FAIL
    assert outcome.preferred_label == "escalate"
    assert outcome.failure_reason == "probe verdict"
    assert outcome.notes == "transport probe verdict"
    assert outcome.notes != "Child session completed with empty final message"


@pytest.mark.asyncio
async def test_context_updates_ride_along():
    """The envelope carries the whole verdict, not just status + label."""
    result = await _run_child(
        [
            _tool_response(
                (
                    "tc1",
                    "report_outcome",
                    {
                        "status": "success",
                        "context_updates": {"artifact": "report.md"},
                        "suggested_next_ids": ["publish"],
                    },
                )
            )
        ]
    )

    outcome = await _parent_outcome(result)

    assert outcome.is_explicit is True
    assert outcome.context_updates == {"artifact": "report.md"}
    assert outcome.suggested_next_ids == ["publish"]


# ---------------------------------------------------------------------------
# 2. Fail-closed (EXTENSIONS.md §25 / §35 Compatibility)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_without_a_verdict_stays_non_explicit():
    """A status-only completion must NOT be promoted to an explicit verdict.

    The whole point of `is_explicit` is that a goal gate can only be satisfied
    by an asserted verdict (EXTENSIONS.md §25).  Adding the transport must not
    hand every clean child an explicit-looking outcome for free.
    """
    result = await _run_child([_text_response("")])

    assert result["status"] == "success"
    assert result["metadata"] == {}

    outcome = await _parent_outcome(result)

    assert outcome.is_explicit is False
    assert outcome.status is StageStatus.SUCCESS
    assert outcome.notes == "Child session completed with empty final message"


@pytest.mark.asyncio
async def test_goal_gate_still_fails_closed_without_a_verdict():
    """A goal_gate node whose child never reported does not pass the gate.

    Plain prose + `goal_gate=true` degrades to RETRY with `is_explicit=False`
    (the §25 parser rung), so the gate cannot be satisfied by a child that
    merely finished.
    """
    result = await _run_child([_text_response("All done, everything looks great!")])

    assert result["metadata"] == {}

    outcome = await _parent_outcome(result, goal_gate="true")

    assert outcome.is_explicit is False
    assert outcome.status is StageStatus.RETRY


# ---------------------------------------------------------------------------
# 3. Precedence (EXTENSIONS.md §35 Precedence Policy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_verdict_beats_cheerful_prose():
    """Evidence over self-report: `status=fail` + "mission accomplished" => FAIL.

    Without the transport the parent has nothing but the prose to go on, and
    `_parse_outcome` reads cheerful prose as SUCCESS.
    """
    result = await _run_child(
        [
            _tool_response(
                (
                    "tc1",
                    "report_outcome",
                    {
                        "status": "fail",
                        "failure_reason": "the work did not converge",
                    },
                ),
                text="All done, mission accomplished!",
            )
        ]
    )

    # The prose really is there -- this is not an empty-output shortcut.
    assert result["output"] == "All done, mission accomplished!"

    outcome = await _parent_outcome(result)

    assert outcome.status is StageStatus.FAIL
    assert outcome.is_explicit is True
    assert outcome.failure_reason == "the work did not converge"


@pytest.mark.asyncio
async def test_last_declared_verdict_is_the_one_transported():
    """Two verdicts in one batch: the LAST declared one reaches the parent.

    The §35 ordering barrier makes this deterministic -- the batch runs in
    provider-declared order rather than under `asyncio.gather`.
    """
    result = await _run_child(
        [
            _tool_response(
                ("tc1", "report_outcome", {"status": "success", "preferred_label": "ship"}),
                ("tc2", "report_outcome", {"status": "fail", "preferred_label": "escalate"}),
            )
        ]
    )

    outcome = await _parent_outcome(result)

    assert outcome.status is StageStatus.FAIL
    assert outcome.preferred_label == "escalate"
