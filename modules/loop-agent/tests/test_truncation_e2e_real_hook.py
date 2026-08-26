"""End-to-end wiring test with the REAL hooks-tool-truncation module mounted
on a REAL amplifier_core.HookRegistry (not a fake hook double).

This proves the fix at the integration boundary the regression test in
test_truncation_wiring.py cannot: that the actual kernel (amplifier-core's
Rust HookRegistry, via its Python binding) really does collapse a Modify
result to action="continue" on return, and that AgentSession's tool:post
consumer really does read the truncated output back out of it.

Only the LLM provider and the tool are mocked; hooks-tool-truncation and
the HookRegistry it mounts on are the real, shipped modules.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core import HookRegistry
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult
from amplifier_module_hooks_tool_truncation import mount as mount_truncation_hook
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


def _make_mock_tool(name: str, output: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


@pytest.mark.asyncio
async def test_real_truncation_hook_output_reaches_llm():
    """Oversized tool output, real hooks-tool-truncation mounted on a real
    HookRegistry: the LLM-bound content must be truncated to the configured
    char_limit (not the raw, oversized output).
    """
    char_limit = 500
    real_hooks = HookRegistry()

    # coordinator duck-type: hooks-tool-truncation's mount() only calls
    # coordinator.get("hooks").
    coordinator = SimpleNamespace(
        get=lambda key: real_hooks if key == "hooks" else None
    )
    cleanup = await mount_truncation_hook(
        coordinator,
        config={"char_limits": {"bash": char_limit}, "line_limits": {}, "modes": {}},
    )
    assert cleanup is not None

    big_output = "L" * 50_000
    tool = _make_mock_tool("bash", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "bash", {"command": "cat huge.log"}),
            _text_response("done."),
        ]
    )

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"bash": tool},
        hooks=real_hooks,
    )
    await session.process_input("cat huge.log")

    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    llm_content = tool_messages[0].content

    # The money proof: LLM-bound content is NOT the raw 50,000-char output,
    # IS bounded near the configured 500-char limit (plus marker overhead),
    # and carries the truncation warning the spec (§5.1) requires.
    assert llm_content != big_output
    assert len(llm_content) < len(big_output)
    assert len(llm_content) <= char_limit + 500  # allow marker overhead
    assert "[WARNING: Tool output was truncated" in llm_content

    cleanup()


@pytest.mark.asyncio
async def test_real_truncation_hook_leaves_small_output_untouched():
    """Under the configured limit: real hooks-tool-truncation is a no-op,
    and the exact original string reaches the LLM.
    """
    real_hooks = HookRegistry()
    coordinator = SimpleNamespace(
        get=lambda key: real_hooks if key == "hooks" else None
    )
    cleanup = await mount_truncation_hook(
        coordinator,
        config={"char_limits": {"bash": 30_000}, "line_limits": {}, "modes": {}},
    )

    small_output = "all good, 3 files changed"
    tool = _make_mock_tool("bash", output=small_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "bash", {"command": "git status"}),
            _text_response("done."),
        ]
    )

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"bash": tool},
        hooks=real_hooks,
    )
    await session.process_input("git status")

    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert tool_messages[0].content == small_output

    cleanup()
