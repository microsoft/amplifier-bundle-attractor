"""Tests for tool output truncation wiring in the agent loop (1a1).

Verifies that when hooks-tool-truncation is active, the agent loop:
1. Emits tool:post after tool execution
2. Reads back the tool:post hook's (merged) output data
3. Uses truncated output for the ToolResult sent to LLM
4. Preserves full output in agent:tool_call_end event

IMPORTANT: these tests wrap a REAL ``amplifier_core.HookRegistry`` (the
Rust-backed engine), not a hand-rolled fake. A prior version of this file
used a bare ``MagicMock`` whose fake ``emit()`` directly fabricated
``HookResult(action="modify", ...)`` -- something the real registry's
``emit()`` never returns to a caller (see hooks.rs: ``Modify`` only chains
data between handlers; the terminal result returned to the caller always
carries ``action=Continue``, with the merged data preserved). That fake let
`test_truncated_output_sent_to_llm` pass while production discarded every
hook-truncated tool output. Routing every emit through the real registry
means these tests now exercise the actual merge-and-collapse contract.
"""

import pytest

from amplifier_core import HookRegistry, HookResult
from amplifier_core.events import TOOL_POST
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult
from unittest.mock import AsyncMock, MagicMock

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


class RecordingHookRegistry:
    """Thin recorder around a REAL ``amplifier_core.HookRegistry``.

    This is deliberately NOT a ``MagicMock``. ``.emit()`` below delegates to
    the actual Rust-backed ``HookRegistry.emit()``, so whatever that engine
    truly returns to a caller (merged ``data``, collapsed ``action``) is what
    ``AgentSession`` sees -- exactly like production. Handlers are attached
    via the real registry's own ``.register()``. ``.emitted`` additionally
    records every ``(event, data)`` pair passed through, purely so these
    tests can assert on emission the same way the old MagicMock-based tests
    did -- it plays no part in computing the returned HookResult.
    """

    def __init__(self) -> None:
        self._registry = HookRegistry()
        self.emitted: list[tuple[str, dict]] = []

    def register(self, event: str, handler) -> None:
        self._registry.register(event, handler)

    async def emit(self, event: str, data: dict):
        self.emitted.append((event, dict(data)))
        return await self._registry.emit(event, data)


def _make_truncating_hooks(truncated: str) -> RecordingHookRegistry:
    """Real HookRegistry with a tool:post handler that truncates `result`."""
    hooks = RecordingHookRegistry()

    async def truncate_handler(event: str, data: dict) -> HookResult:
        return HookResult(
            action="modify",
            data={"result": truncated, "full_output": data.get("result")},
        )

    hooks.register(TOOL_POST, truncate_handler)
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

    hooks = _make_truncating_hooks("truncated_output")

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # Verify tool:post was emitted
    post_events = [(e, d) for e, d in hooks.emitted if e == "tool:post"]
    assert len(post_events) == 1
    assert post_events[0][1]["tool_name"] == "read_file"
    assert post_events[0][1]["result"] == big_output


@pytest.mark.asyncio
async def test_truncated_output_sent_to_llm():
    """When tool:post's handler modifies `result`, the LLM sees the modified output.

    This is the test that a fabricated ``MagicMock`` HookResult let pass
    while production shipped with truncation silently disabled. It now runs
    against the real ``amplifier_core.HookRegistry``, so it fails on the old
    (buggy) `agent_session.py` gate of `post_result.action == "modify"` --
    that condition is never true from the real engine -- and passes once the
    caller instead reads the merged `data["result"]`.
    """
    big_output = "x" * 100_000
    truncated = "truncated_version"
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {"path": "big.txt"}),
        _text_response("done."),
    ])

    hooks = _make_truncating_hooks(truncated)

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

    hooks = _make_truncating_hooks("short")

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # agent:tool_call_end should have the FULL output
    end_events = [(e, d) for e, d in hooks.emitted
                  if e == "agent:tool_call_end"]
    assert len(end_events) == 1
    assert end_events[0][1]["output"] == big_output


@pytest.mark.asyncio
async def test_no_truncation_when_hook_continues():
    """When no tool:post handler modifies `result`, output is unchanged.

    No handler is registered on the real registry at all here: an
    unregistered event naturally collapses to `action=continue` with the
    input data unchanged, which is precisely the "nothing modified this"
    case the fix must leave as a no-op.
    """
    tool = _make_mock_tool("read_file", output="small output")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response("tc1", "read_file", {}),
        _text_response("done."),
    ])

    hooks = RecordingHookRegistry()

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
