"""Tests for SESSION_END emission timing (1a6).

Verifies that:
1. SESSION_END is emitted exactly once per process_input() call
2. SESSION_END is emitted AFTER follow-ups are fully drained
3. No premature SESSION_END before follow-up processing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, Usage

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig
from amplifier_module_loop_agent.steering import FollowUpQueue


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


def _make_mock_tool(name: str) -> MagicMock:
    from amplifier_core.models import ToolResult

    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    return tool


@pytest.mark.asyncio
async def test_session_end_emitted_exactly_once_no_followups():
    """Without follow-ups, SESSION_END is emitted exactly once."""
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done."))
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
    )
    await session.process_input("do it")

    session_end_events = [e for e, _ in hooks._emitted if e == "agent:session_end"]
    assert len(session_end_events) == 1


@pytest.mark.asyncio
async def test_session_end_after_followups_drained():
    """SESSION_END comes AFTER all follow-ups are processed."""
    follow_up_queue = FollowUpQueue()
    follow_up_queue.follow_up("follow-up message")

    call_count = 0

    async def provider_side_effect(request):
        nonlocal call_count
        call_count += 1
        return _text_response(f"Response {call_count}.")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=provider_side_effect)
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
        follow_up_queue=follow_up_queue,
    )
    await session.process_input("start")

    # Both the original and follow-up were processed
    assert provider.complete.call_count == 2

    # SESSION_END should be emitted exactly ONCE
    session_end_events = [e for e, _ in hooks._emitted if e == "agent:session_end"]
    assert len(session_end_events) == 1


@pytest.mark.asyncio
async def test_session_end_is_last_lifecycle_event():
    """SESSION_END is the last lifecycle event emitted."""
    follow_up_queue = FollowUpQueue()
    follow_up_queue.follow_up("followup 1")

    call_count = 0

    async def provider_side_effect(request):
        nonlocal call_count
        call_count += 1
        return _text_response(f"Response {call_count}.")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=provider_side_effect)
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={},
        hooks=hooks,
        follow_up_queue=follow_up_queue,
    )
    await session.process_input("go")

    # Find the index of SESSION_END
    events = [e for e, _ in hooks._emitted]
    session_end_indices = [i for i, e in enumerate(events) if e == "agent:session_end"]
    assert len(session_end_indices) == 1
    session_end_idx = session_end_indices[0]

    # No agent:user_input or agent:assistant_text_end events AFTER session_end
    for i in range(session_end_idx + 1, len(events)):
        assert events[i] not in (
            "agent:user_input",
            "agent:assistant_text_end",
        ), f"Event {events[i]} emitted after SESSION_END at index {i}"


@pytest.mark.asyncio
async def test_multiple_followups_single_session_end():
    """Multiple follow-ups in queue -> still exactly one SESSION_END."""
    follow_up_queue = FollowUpQueue()
    follow_up_queue.follow_up("followup 1")
    follow_up_queue.follow_up("followup 2")
    follow_up_queue.follow_up("followup 3")

    call_count = 0

    async def provider_side_effect(request):
        nonlocal call_count
        call_count += 1
        return _text_response(f"Response {call_count}.")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=provider_side_effect)
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={},
        hooks=hooks,
        follow_up_queue=follow_up_queue,
    )
    await session.process_input("go")

    # 1 original + 3 follow-ups = 4 provider calls
    assert provider.complete.call_count == 4

    # Exactly one SESSION_END
    session_end_events = [e for e, _ in hooks._emitted if e == "agent:session_end"]
    assert len(session_end_events) == 1
