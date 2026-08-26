"""Tests for tool output truncation wiring in the agent loop (1a1).

Verifies that when hooks-tool-truncation is active, the agent loop:
1. Emits tool:post after tool execution
2. Reads back the hook-modified event data
3. Uses truncated output for the ToolResult sent to LLM
4. Preserves full output in agent:tool_call_end event

Regression coverage for amplifier-support#485 / PR #318: amplifier-core's
HookRegistry.emit() (hooks.rs) never returns action="modify" to its caller
-- Modify only chains data between handlers *inside* emit()'s own dispatch
loop; the action returned to the caller always collapses to "continue"
(or deny/ask_user/inject_context, if some handler in the chain requested
one of those). A consumer that gates on `post_result.action == "modify"`
is dead code that can never fire.

The test doubles below model that REAL kernel contract: a "modifying" hook
double returns `HookResult(action="continue", data=<replaced dict>)`, never
`action="modify"`. A double that fabricates `action="modify"` back to the
caller (as this file's fakes used to) is unfaithful to the kernel and can
mask a broken consumer -- which is exactly how this bug went undetected in
production.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
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
    """A hook double with no registered handlers: always action=continue."""
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        return HookResult(action="continue", data=dict(data))

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


def _make_modifying_hooks(replacement_result):
    """A hook double faithful to the REAL kernel contract for a modifying hook.

    amplifier-core's HookRegistry.emit() (hooks.rs) folds a handler's
    Modify result into `current_data` and returns it to the caller under
    action="continue" -- never "modify". This is what hooks-tool-truncation
    (and any other tool:post modifier) actually looks like from the
    consumer's point of view.
    """
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        if event == "tool:post":
            modified = {
                **data,
                "result": replacement_result,
                "full_output": data.get("result"),
            }
            return HookResult(action="continue", data=modified)
        return HookResult(action="continue", data=dict(data))

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


@pytest.mark.asyncio
async def test_tool_post_emitted_after_execution():
    """tool:post event is emitted after each tool execution."""
    big_output = "x" * 100_000
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {"path": "big.txt"}),
            _text_response("done."),
        ]
    )

    hooks = _make_modifying_hooks("truncated_output")

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # Verify tool:post was emitted with the raw (pre-hook) event data
    post_events = [(e, d) for e, d in hooks._emitted if e == "tool:post"]
    assert len(post_events) == 1
    assert post_events[0][1]["tool_name"] == "read_file"
    assert post_events[0][1]["result"] == big_output


@pytest.mark.asyncio
async def test_tool_post_modification_reaches_llm():
    """REGRESSION (amplifier-support#485): a tool:post hook's modified
    `result` must reach the LLM payload.

    This is the dead-branch proof. amplifier-core's real dispatch semantics
    (hooks.rs) return `action="continue"` to the caller even when a handler
    requested Modify -- so a consumer gated on
    `post_result.action == "modify"` NEVER applies the modification. At
    origin/main (agent_session.py:918, pre-fix) this test fails: the fake
    hook below returns exactly what the kernel really returns
    (action="continue", data with a replaced "result"), and the dead branch
    ignores it, so the LLM sees the raw, untruncated output. After the fix
    (reading post_result.data directly, guarded by isinstance/!=), the
    modified output reaches the LLM and this test passes.
    """
    big_output = "x" * 100_000
    truncated = "truncated_version"
    tool = _make_mock_tool("read_file", output=big_output)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {"path": "big.txt"}),
            _text_response("done."),
        ]
    )

    hooks = _make_modifying_hooks(truncated)

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
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {"path": "big.txt"}),
            _text_response("done."),
        ]
    )

    hooks = _make_modifying_hooks("short")

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("read big.txt")

    # agent:tool_call_end should have the FULL output
    end_events = [(e, d) for e, d in hooks._emitted if e == "agent:tool_call_end"]
    assert len(end_events) == 1
    assert end_events[0][1]["output"] == big_output


@pytest.mark.asyncio
async def test_no_truncation_when_hook_continues():
    """When no hook modifies the result, output is unchanged."""
    tool = _make_mock_tool("read_file", output="small output")

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {}),
            _text_response("done."),
        ]
    )

    hooks = _make_hooks()

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


# ---------------------------------------------------------------------------
# Guard cases (3c): the consumer must not crash or spuriously log when the
# hook-modified data doesn't look like a real modification.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_result_key_no_crash_raw_preserved():
    """post_result.data without a "result" key: no crash, raw output used.

    Modify REPLACES current_data rather than merging it, so a hook that
    returns a partial data dict can legitimately drop "result" entirely.
    """
    tool = _make_mock_tool("read_file", output="raw output")

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {}),
            _text_response("done."),
        ]
    )

    async def _emit(event, data):
        if event == "tool:post":
            # A hook that replaced current_data with something that has no
            # "result" key at all (e.g. it only cared about a side-channel
            # field). This must not be treated as a crash or as raw==None.
            return HookResult(action="continue", data={"call_id": data.get("call_id")})
        return HookResult(action="continue", data=dict(data))

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    # Must not raise
    await session.process_input("read")

    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "raw output"


@pytest.mark.asyncio
async def test_non_string_result_ignored():
    """A non-string "result" in post_result.data must be ignored, not sent to the LLM."""
    tool = _make_mock_tool("read_file", output="raw output")

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {}),
            _text_response("done."),
        ]
    )

    async def _emit(event, data):
        if event == "tool:post":
            # Misbehaving/foreign hook returns a non-string "result".
            return HookResult(action="continue", data={**data, "result": 12345})
        return HookResult(action="continue", data=dict(data))

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=_emit)

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
    assert tool_messages[0].content == "raw output"


@pytest.mark.asyncio
async def test_identical_result_no_spurious_modification_log(caplog):
    """A hook that echoes back an identical "result" must not log a modification."""
    tool = _make_mock_tool("read_file", output="unchanged output")

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response("tc1", "read_file", {}),
            _text_response("done."),
        ]
    )

    async def _emit(event, data):
        if event == "tool:post":
            # Hook ran, decided nothing needed truncating, echoed "result"
            # back unchanged (as hooks-tool-truncation's action="continue"
            # short path effectively does).
            return HookResult(
                action="continue", data={**data, "result": data.get("result")}
            )
        return HookResult(action="continue", data=dict(data))

    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=_emit)

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )

    with caplog.at_level(
        logging.DEBUG, logger="amplifier_module_loop_agent.agent_session"
    ):
        await session.process_input("read")

    second_request = provider.complete.call_args_list[1][0][0]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert tool_messages[0].content == "unchanged output"
    assert not any("hook modified output" in rec.message for rec in caplog.records)
