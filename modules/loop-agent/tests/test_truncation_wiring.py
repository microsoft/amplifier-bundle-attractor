"""Tests for tool output truncation wiring in the agent loop (1a1).

Verifies that when hooks-tool-truncation is active, the agent loop:
1. Emits tool:post after tool execution
2. Reads back HookResult(action="modify") data
3. Uses truncated output for the ToolResult sent to LLM
4. Preserves full output in agent:tool_call_end event
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import HookResult, ToolResult

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


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


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


@pytest.mark.asyncio
async def test_tool_post_emitted_after_execution():
    """tool:post event is emitted after each tool execution."""
    big_output = "x" * 100_000
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {"path": "big.txt"}),
        _text_response("done."),
    ])

    emitted_events: list[tuple[str, dict]] = []

    async def recording_emit(event: str, data: dict):
        emitted_events.append((event, data))
        if event == "tool:post":
            return HookResult(
                action="modify",
                data={
                    "result": "truncated_output",
                    "full_output": data.get("result"),
                },
            )
        return MagicMock(action="continue")

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recording_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # Verify tool:post was emitted
    post_events = [(e, d) for e, d in emitted_events if e == "tool:post"]
    assert len(post_events) == 1
    assert post_events[0][1]["tool_name"] == "read_file"
    assert post_events[0][1]["result"] == big_output


@pytest.mark.asyncio
async def test_truncated_output_sent_to_llm():
    """When tool:post returns modify, the LLM sees truncated output."""
    big_output = "x" * 100_000
    truncated = "truncated_version"
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {"path": "big.txt"}),
        _text_response("done."),
    ])

    async def truncating_emit(event: str, data: dict):
        if event == "tool:post":
            return HookResult(
                action="modify",
                data={"result": truncated, "full_output": data.get("result")},
            )
        return MagicMock(action="continue")

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=truncating_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # The second LLM call should contain the truncated tool result
    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    # The tool result content sent to LLM should be the truncated version
    assert tool_messages[0].content == truncated


@pytest.mark.asyncio
async def test_full_output_in_tool_call_end_event():
    """agent:tool_call_end event carries full untruncated output."""
    big_output = "x" * 100_000
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {"path": "big.txt"}),
        _text_response("done."),
    ])

    emitted_events: list[tuple[str, dict]] = []

    async def recording_emit(event: str, data: dict):
        emitted_events.append((event, data))
        if event == "tool:post":
            return HookResult(
                action="modify",
                data={"result": "short", "full_output": data.get("result")},
            )
        return MagicMock(action="continue")

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recording_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # agent:tool_call_end should have the FULL output
    end_events = [(e, d) for e, d in emitted_events
                  if e == "agent:tool_call_end"]
    assert len(end_events) == 1
    assert end_events[0][1]["output"] == big_output


@pytest.mark.asyncio
async def test_no_truncation_when_hook_continues():
    """When tool:post returns action=continue, output is unchanged."""
    tool = _make_mock_tool("read_file", output="small output")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {}),
        _text_response("done."),
    ])

    async def passthrough_emit(event: str, data: dict):
        return MagicMock(action="continue")

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=passthrough_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read")

    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert "small output" in tool_messages[0].content
