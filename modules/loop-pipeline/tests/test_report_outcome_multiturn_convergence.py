"""Multi-turn convergence test: real DOT engine + real AmplifierBackend.

Closes the gap admitted by PR #88 ("prioritize report_outcome tool calls;
implement last-call-wins semantics"): that fix had no live multi-cycle
convergence run against a real engine at merge time.

This test drives ``report_outcome_convergence.dot`` through the actual
``PipelineEngine`` + ``HandlerRegistry`` + ``AmplifierBackend`` (Path B,
direct tool loop -- no session.spawn), with a mock ``unified_llm`` client
that simulates 3 assess-node visits: refine -> refine -> converged.

The critical assertion is on turn 3: the model calls
``report_outcome(preferred_label="converged")`` as a tool call AND THEN
emits a JSON-shaped CONTRADICTORY verdict as trailing text in its
follow-up turn ({"status": "success", "preferred_label": "refine"}). Per
``backend.py::_find_report_outcome_call`` / ``_run_with_tool_loop``, the
report_outcome tool call must win the routing decision regardless of that
trailing text -- this is the exact priority-ordering behavior PR #88 fixed
(issue #238: report_outcome priority inversion). Under the pre-#88
text-first semantics the trailing JSON would parse to "refine" and win,
looping the engine back to assess -- so this test FAILS on pre-#88 code
and genuinely pins the regression.

Spec coverage: companion to PR #38 (wiki-weaver) / PR #88 (this repo,
commit 74a743a).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core / amplifier_foundation stub so the
# backend's lazy imports work in the test environment, exactly as
# test_backend.py does for the same reason (AmplifierBackend imports these
# lazily at call time, and they may not be installed in the test env).
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

if "amplifier_foundation" not in sys.modules:
    from dataclasses import dataclass as _dc

    @_dc
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise

# ---------------------------------------------------------------------------
# Mock helpers -- mirrors test_backend.py's _MockUnifiedClient / response
# builders / _MockReportOutcomeTool / NoSpawnCoordinator / _make_node /
# _make_context.  Kept local per this repo's self-contained test file
# convention (no test file in this repo imports from another test file).
# ---------------------------------------------------------------------------


class _MockUnifiedClient:
    """Mock unified_llm.Client that plays back a queued list of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0
        self.requests: list[Any] = []

    async def complete(self, request):
        self.call_count += 1
        self.requests.append(request)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            if isinstance(resp, Exception):
                raise resp
            return resp
        return _make_text_response("fallback")


def _make_text_response(text):
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def _make_tool_call_response(calls):
    """calls = [{"id": "tc-1", "name": "report_outcome", "args": {...}}]"""
    content = []
    for c in calls:
        content.append(
            unified_llm.ContentPart(
                kind=unified_llm.ContentKind.TOOL_CALL,
                tool_call=unified_llm.ToolCallData(
                    id=c["id"],
                    name=c["name"],
                    arguments=c.get("args", {}),
                ),
            )
        )
    return unified_llm.Response(
        id="resp-tool",
        model="test-model",
        provider="test",
        message=unified_llm.Message(role=unified_llm.Role.ASSISTANT, content=content),
        finish_reason=unified_llm.FinishReason(reason="tool_calls"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class _MockSession:
    """Minimal stand-in for AmplifierSession."""

    config: dict[str, Any] = {}


class NoSpawnCoordinator:
    """Coordinator with no session.spawn capability.

    Forces AmplifierBackend.run() down the direct _run_with_tool_loop
    (Path B) code path instead of attempting to spawn a sub-session.
    """

    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str):
        return None


@dataclass
class _MockToolResult:
    output: str = "tool output"
    success: bool = True


class _MockReportOutcomeTool:
    """Minimal stand-in for ReportOutcomeTool.

    The backend extracts the outcome from result.steps[i].tool_calls
    (immutable, race-free), not from any state on the tool object itself --
    execute() only needs to return a truthy result so unified_llm.generate()
    can complete the tool loop.
    """

    last_outcome: dict | None = None
    name = "report_outcome"
    description = "Report structured outcome for pipeline routing."
    parameters = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "preferred_label": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["status"],
    }

    async def execute(self, input: dict) -> _MockToolResult:
        return _MockToolResult(output=f"recorded: {input.get('status', '?')}")


# ---------------------------------------------------------------------------
# Engine wiring helper -- mirrors test_goal_gates.py's _make_engine, but
# wires a real AmplifierBackend (Path B) instead of a MockBackend.
# ---------------------------------------------------------------------------

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "report_outcome_convergence.dot"
).read_text()

# JSON-shaped CONTRADICTORY verdict the model emits as trailing text AFTER
# its turn-3 report_outcome(preferred_label="converged") tool call. This is
# deliberately contradictory JSON (not prose) so the test pins #88's
# priority order: under pre-#88 text-first semantics, JSON-shaped trailing
# text was parsed and returned BEFORE the report_outcome tool call was ever
# checked -- so "refine" would win and the engine would loop back to assess
# (test fails). Under post-#88 tool-call-first semantics the tool call's
# "converged" wins (test passes). Plain prose would NOT discriminate: prose
# isn't JSON-shaped, so it falls through to the tool call under BOTH
# orderings.
_TURN_3_TRAILING_TEXT = '{"status": "success", "preferred_label": "refine"}'


def _make_engine(backend: AmplifierBackend, logs_root: str) -> PipelineEngine:
    graph = parse_dot(_FIXTURE)
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
# The test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_outcome_wins_over_trailing_prose_across_three_turns(tmp_path):
    """3 assess visits (refine, refine, converged) route correctly end-to-end.

    Turn 3 specifically exercises the priority-ordering fix: the model
    calls report_outcome(preferred_label="converged") as a tool call, then
    its follow-up turn emits a JSON-shaped CONTRADICTORY verdict
    ({"status": "success", "preferred_label": "refine"}) as trailing text.
    The trailing JSON must NOT override the tool call's verdict. Under
    pre-#88 text-first semantics it WOULD (JSON-shaped text was parsed
    before the tool call was checked), so this test fails on pre-#88 code.
    """
    report_tool = _MockReportOutcomeTool()

    mock_client = _MockUnifiedClient(
        [
            # Turn 1: assess calls report_outcome(refine), empty closing text.
            _make_tool_call_response(
                [
                    {
                        "id": "tc-1",
                        "name": "report_outcome",
                        "args": {
                            "status": "success",
                            "preferred_label": "refine",
                            "notes": "gap 1",
                        },
                    }
                ]
            ),
            _make_text_response(""),
            # Turn 2: assess calls report_outcome(refine) again, empty closing text.
            _make_tool_call_response(
                [
                    {
                        "id": "tc-2",
                        "name": "report_outcome",
                        "args": {
                            "status": "success",
                            "preferred_label": "refine",
                            "notes": "gap 2",
                        },
                    }
                ]
            ),
            _make_text_response(""),
            # Turn 3: assess calls report_outcome(converged), THEN emits a
            # JSON-shaped CONTRADICTORY verdict ("refine") as trailing text.
            # The tool call must still win (#88 tool-call-first priority).
            _make_tool_call_response(
                [
                    {
                        "id": "tc-3",
                        "name": "report_outcome",
                        "args": {
                            "status": "success",
                            "preferred_label": "converged",
                            "notes": "all good",
                        },
                    }
                ]
            ),
            _make_text_response(_TURN_3_TRAILING_TEXT),
        ]
    )

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=mock_client,
    )

    engine = _make_engine(backend, logs_root=str(tmp_path / "logs"))
    outcome = await engine.run()

    # 1. Pipeline completed via the converged path, not a drain/exhaustion path.
    assert outcome.status == StageStatus.SUCCESS, (
        f"Expected SUCCESS via converged routing, got {outcome.status}. "
        f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
    )

    # 2. Exactly 3 node-visits happened (2 mock-client responses each): no
    # premature convergence (would be < 6) and no extra/infinite looping
    # (would be > 6).
    assert mock_client.call_count == 6, (
        f"Expected exactly 6 unified_llm.complete() calls (3 assess visits "
        f"x 2 rounds each: tool-call + follow-up), got {mock_client.call_count}"
    )

    # 3. Prove the priority-ordering fix was actually exercised, not just
    # trivially satisfied. Turn 3's follow-up round was queued with a
    # JSON-shaped trailing verdict (_TURN_3_TRAILING_TEXT) that CONTRADICTS
    # the tool call: tool call says "converged", trailing text says
    # "refine". Confirm the setup really is discriminating: the text must
    # be JSON-shaped (so pre-#88 text-first extraction would parse it and
    # return early) and must carry the contradictory label (so if it wins,
    # routing observably diverges). Combined with assertions 1-2 above --
    # SUCCESS reached in exactly 6 calls -- this proves the report_outcome
    # tool call's "converged" verdict won the routing decision despite the
    # trailing JSON. Under the pre-#88 priority inversion (#238) the
    # trailing JSON's "refine" would win, the engine would loop back to
    # "assess", and assertions 1-2 would fail -- so this test genuinely
    # pins #88's tool-call-first priority order.
    _trailing = _TURN_3_TRAILING_TEXT.strip()
    assert _trailing.startswith("{"), (
        "Test setup error: turn 3's trailing text must be JSON-shaped so "
        "that pre-#88 text-first extraction would have parsed it -- "
        "otherwise this test does not pin the #88 priority order."
    )
    assert '"preferred_label": "refine"' in _trailing, (
        "Test setup error: turn 3's trailing JSON must carry a verdict "
        "that CONTRADICTS the tool call's 'converged' label -- otherwise "
        "both priority orders yield the same routing and the test cannot "
        "discriminate pre-#88 from post-#88 behavior."
    )
