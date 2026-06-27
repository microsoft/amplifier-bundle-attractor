"""Tests for cancellation checkpoints in the agent loop (1a2).

Verifies 3 cancellation checkpoints:
1. Top of loop -- graceful cancel before LLM call
2. After provider call -- immediate cancel before processing response
3. Around tool execution -- cancel during gather, results still added
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig
from amplifier_module_loop_agent.state import SessionState


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(call_id: str, tool_name: str, args: dict) -> ChatResponse:
    return ChatResponse(
        content=[],
        tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=args)],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_mock_tool(name: str, output: str = "ok") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


def _make_cancellation_mock(is_cancelled: bool = False, is_immediate: bool = False):
    """Create a mock cancellation token."""
    cancel = MagicMock()
    type(cancel).is_cancelled = PropertyMock(return_value=is_cancelled)
    type(cancel).is_immediate = PropertyMock(return_value=is_immediate)
    cancel.register_tool_start = MagicMock()
    cancel.register_tool_complete = MagicMock()
    return cancel


def _make_coordinator(is_cancelled=False, is_immediate=False):
    coord = MagicMock()
    coord.cancellation = _make_cancellation_mock(is_cancelled, is_immediate)
    return coord


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted = []

    async def _recording_emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


@pytest.mark.asyncio
async def test_checkpoint1_graceful_cancel_at_loop_top():
    """Graceful cancel at top of loop exits without calling provider."""
    coordinator = _make_coordinator(is_cancelled=True)
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("should not reach"))
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools=tools,
        hooks=hooks,
        coordinator=coordinator,
    )
    result = await session.process_input("do stuff")

    # Provider should NOT be called -- we cancelled before the LLM call
    provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint2_immediate_cancel_after_provider():
    """Immediate cancel after provider call exits before tool execution."""
    # Cancel flag starts False, becomes True after first provider call
    coordinator = _make_coordinator(is_cancelled=False, is_immediate=False)
    call_count = 0
    original_response = _tool_response("tc1", "read_file", {"path": "x.py"})

    async def provider_side_effect(request):
        nonlocal call_count
        call_count += 1
        # After first call, set immediate cancel
        type(coordinator.cancellation).is_immediate = PropertyMock(return_value=True)
        return original_response

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=provider_side_effect)
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools=tools,
        hooks=hooks,
        coordinator=coordinator,
    )
    result = await session.process_input("read x.py")

    # Provider called once, then immediate cancel kicks in
    assert provider.complete.call_count == 1
    # Tool should NOT be executed
    tools["read_file"].execute.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint3_tool_results_added_on_cancel():
    """When cancelled after tool execution, tool results are still in history."""
    coordinator = _make_coordinator(is_cancelled=False, is_immediate=False)

    async def tool_side_effect(args):
        # Set cancel during tool execution
        type(coordinator.cancellation).is_cancelled = PropertyMock(return_value=True)
        return ToolResult(success=True, output="file contents")

    tool = _make_mock_tool("read_file")
    tool.execute = AsyncMock(side_effect=tool_side_effect)

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {"path": "x.py"}),
        _text_response("should not reach"),
    ])
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
        coordinator=coordinator,
    )
    await session.process_input("read x.py")

    # Tool was executed
    tool.execute.assert_called_once()
    # Provider called only once (cancelled before second call)
    assert provider.complete.call_count == 1
    # Tool results were added to history (not orphaned)
    from amplifier_module_loop_agent.turns import ToolResultsTurn
    tool_result_turns = [
        t for t in session._history if isinstance(t, ToolResultsTurn)
    ]
    assert len(tool_result_turns) == 1


@pytest.mark.asyncio
async def test_tool_register_start_complete_called():
    """Tool execution registers with cancellation token for visibility."""
    coordinator = _make_coordinator()
    tool = _make_mock_tool("read_file")
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {}),
        _text_response("done."),
    ])
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
        coordinator=coordinator,
    )
    await session.process_input("read")

    coordinator.cancellation.register_tool_start.assert_called_once_with(
        "tc1", "read_file"
    )
    coordinator.cancellation.register_tool_complete.assert_called_once_with("tc1")


@pytest.mark.asyncio
async def test_no_coordinator_no_crash():
    """Without a coordinator, cancellation checks are no-ops."""
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("ok."))
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
        coordinator=None,
    )
    result = await session.process_input("hi")
    assert result == "ok."
