"""Tests for event emission (Task 1.6).

Spec coverage: EVENT-001 through EVENT-009.
Verifies that the agent session emits the full set of spec events
through the hooks parameter at the correct times with correct data.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import (
    AGENT_ASSISTANT_TEXT_END,
    AGENT_SESSION_END,
    AGENT_SESSION_START,
    AGENT_TOOL_CALL_END,
    AGENT_TOOL_CALL_START,
    AGENT_TURN_LIMIT,
    AGENT_USER_INPUT,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)


# ---------------------------------------------------------------------------
# Test helpers (same pattern as test_agent_session.py)
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(
    *tool_calls_tuple: tuple[str, str, dict],
    text: str = "",
) -> ChatResponse:
    """ChatResponse with tool calls and optional text."""
    content = [{"type": "text", "text": text}] if text else []
    return ChatResponse(
        content=content,
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args)
            for cid, name, args in tool_calls_tuple
        ],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_mock_tool(name: str, output: str = "ok") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


def _make_harness(config=None, responses=None, tool_names=None):
    """Build orchestrator + mocks for testing events."""
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=responses or [_text_response("done")]
    )
    providers = {"test": provider}
    names = tool_names or ["read_file", "write_file"]
    tools = {n: _make_mock_tool(n) for n in names}
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    context = MagicMock()
    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orchestrator, context, providers, tools, hooks


# ---------------------------------------------------------------------------
# Session lifecycle events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_event_emitted():
    """agent:session_start is emitted with session_id."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_SESSION_START in event_names
    start_event = next(e for e in hooks._emitted if e[0] == AGENT_SESSION_START)
    assert "session_id" in start_event[1]


@pytest.mark.asyncio
async def test_session_start_emitted_once():
    """agent:session_start is emitted only on first process_input."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("first"), _text_response("second")]
    )
    await orch.execute("msg1", ctx, provs, tools, hooks)
    await orch.execute("msg2", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert event_names.count(AGENT_SESSION_START) == 1


@pytest.mark.asyncio
async def test_session_end_event_emitted():
    """agent:session_end is emitted when processing completes."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("done")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_SESSION_END in event_names


@pytest.mark.asyncio
async def test_session_end_has_final_state():
    """agent:session_end carries state information."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("done")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    end_event = next(e for e in hooks._emitted if e[0] == AGENT_SESSION_END)
    assert "state" in end_event[1]


# ---------------------------------------------------------------------------
# Assistant text events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_text_end_event():
    """agent:assistant_text_end is emitted with full text."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("Hello world")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_ASSISTANT_TEXT_END in event_names
    text_event = next(
        e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_END
    )
    assert text_event[1]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_assistant_text_end_with_reasoning():
    """agent:assistant_text_end includes reasoning when available."""
    # Create a response with reasoning content
    response = ChatResponse(
        content=[
            {"type": "thinking", "thinking": "Let me think..."},
            {"type": "text", "text": "Answer"},
        ],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    orch, ctx, provs, tools, hooks = _make_harness(responses=[response])
    await orch.execute("think about it", ctx, provs, tools, hooks)
    text_event = next(
        e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_END
    )
    assert text_event[1]["text"] == "Answer"
    assert text_event[1].get("reasoning") == "Let me think..."


@pytest.mark.asyncio
async def test_assistant_text_end_per_llm_call():
    """agent:assistant_text_end emitted for each LLM response."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {}), text="Working..."),
            _text_response("Done"),
        ]
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    text_events = [e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_END]
    assert len(text_events) == 2


# ---------------------------------------------------------------------------
# Provider events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_request_event():
    """provider:request is emitted before each LLM call."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert PROVIDER_REQUEST in event_names


@pytest.mark.asyncio
async def test_provider_response_event_with_usage():
    """provider:response is emitted after LLM call with usage data."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    resp_event = next(e for e in hooks._emitted if e[0] == PROVIDER_RESPONSE)
    assert "usage" in resp_event[1]
    assert resp_event[1]["usage"]["input_tokens"] == 10
    assert resp_event[1]["usage"]["output_tokens"] == 5


@pytest.mark.asyncio
async def test_provider_events_per_llm_call():
    """provider:request + provider:response emitted for each LLM call."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    names = [e[0] for e in hooks._emitted]
    assert names.count(PROVIDER_REQUEST) == 2
    assert names.count(PROVIDER_RESPONSE) == 2


# ---------------------------------------------------------------------------
# Tool call events (enhancement: duration_ms)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_end_has_duration():
    """agent:tool_call_end carries duration_ms."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    await orch.execute("do it", ctx, provs, tools, hooks)
    end_event = next(e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_END)
    assert "duration_ms" in end_event[1]
    assert isinstance(end_event[1]["duration_ms"], (int, float))
    assert end_event[1]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Event ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_ordering_simple():
    """Events are emitted in correct order for a simple text response."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    names = [e[0] for e in hooks._emitted]

    # session_start should come first
    assert names[0] == AGENT_SESSION_START

    # user_input before provider:request
    ui_idx = names.index(AGENT_USER_INPUT)
    pr_idx = names.index(PROVIDER_REQUEST)
    assert ui_idx < pr_idx

    # provider:request before provider:response
    resp_idx = names.index(PROVIDER_RESPONSE)
    assert pr_idx < resp_idx

    # assistant_text_end after provider:response
    ate_idx = names.index(AGENT_ASSISTANT_TEXT_END)
    assert resp_idx < ate_idx

    # session_end should come last
    assert names[-1] == AGENT_SESSION_END


@pytest.mark.asyncio
async def test_event_ordering_with_tools():
    """Events follow correct order with tool calls."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    names = [e[0] for e in hooks._emitted]

    # session_start first, session_end last
    assert names[0] == AGENT_SESSION_START
    assert names[-1] == AGENT_SESSION_END

    # First provider round: request -> response -> assistant_text_end
    first_req = names.index(PROVIDER_REQUEST)
    first_resp = names.index(PROVIDER_RESPONSE)
    first_text = names.index(AGENT_ASSISTANT_TEXT_END)
    assert first_req < first_resp < first_text

    # Tool events after first assistant response
    tool_start = names.index(AGENT_TOOL_CALL_START)
    tool_end = names.index(AGENT_TOOL_CALL_END)
    assert first_text < tool_start < tool_end

    # Second provider round after tool completion
    second_req_idx = names.index(PROVIDER_REQUEST, first_req + 1)
    assert tool_end < second_req_idx


@pytest.mark.asyncio
async def test_turn_limit_event_before_session_end():
    """agent:turn_limit is emitted before agent:session_end."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 1},
        responses=[_tool_response(("tc1", "read_file", {}))],
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    names = [e[0] for e in hooks._emitted]
    assert AGENT_TURN_LIMIT in names
    limit_idx = names.index(AGENT_TURN_LIMIT)
    end_idx = names.index(AGENT_SESSION_END)
    assert limit_idx < end_idx
