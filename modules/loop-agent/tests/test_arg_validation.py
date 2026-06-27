"""Tests for tool argument validation in the agent loop (1a3).

Verifies that:
1. Missing required fields produce a clean error result
2. Type mismatches produce a clean error result
3. Valid arguments pass through to execution
4. Tools with no schema are not validated
5. The LLM receives the error and can self-correct
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(*tool_calls_tuple) -> ChatResponse:
    return ChatResponse(
        content=[],
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args)
            for cid, name, args in tool_calls_tuple
        ],
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


def _make_tool_with_schema(name: str, schema: dict, output: str = "ok"):
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = schema
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


# --- Validation method unit tests ---


class TestValidateToolArguments:
    """Unit tests for the static _validate_tool_arguments method."""

    def test_missing_required_field(self):
        tool = _make_tool_with_schema("read_file", {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "offset": {"type": "integer"},
            },
        })
        result = AgentSession._validate_tool_arguments(tool, {})
        assert result is not None
        assert "file_path" in result
        assert "Missing required field" in result

    def test_all_required_fields_present(self):
        tool = _make_tool_with_schema("read_file", {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
            },
        })
        result = AgentSession._validate_tool_arguments(
            tool, {"file_path": "/tmp/test.py"}
        )
        assert result is None

    def test_type_mismatch(self):
        tool = _make_tool_with_schema("read_file", {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer"},
            },
        })
        result = AgentSession._validate_tool_arguments(
            tool, {"file_path": "/tmp/test.py", "offset": "not_a_number"}
        )
        assert result is not None
        assert "offset" in result
        assert "integer" in result

    def test_no_schema_passes(self):
        tool = MagicMock()
        tool.name = "custom_tool"
        tool.input_schema = None
        result = AgentSession._validate_tool_arguments(tool, {"anything": "goes"})
        assert result is None

    def test_empty_schema_passes(self):
        tool = _make_tool_with_schema("custom", {})
        result = AgentSession._validate_tool_arguments(tool, {"anything": "goes"})
        assert result is None

    def test_none_arguments_fails_required(self):
        tool = _make_tool_with_schema("write_file", {
            "type": "object",
            "required": ["file_path", "content"],
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
        })
        result = AgentSession._validate_tool_arguments(tool, None)
        assert result is not None
        assert "file_path" in result
        assert "content" in result

    def test_multiple_missing_fields(self):
        tool = _make_tool_with_schema("write_file", {
            "type": "object",
            "required": ["file_path", "content"],
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
        })
        result = AgentSession._validate_tool_arguments(tool, {})
        assert result is not None
        assert "file_path" in result
        assert "content" in result


# --- Integration tests ---


@pytest.mark.asyncio
async def test_validation_error_sent_to_llm():
    """Missing required field -> error result fed back to LLM for self-correction."""
    tool = _make_tool_with_schema("read_file", {
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file"},
        },
    })

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        # First call: LLM calls read_file without file_path
        _tool_response(("tc1", "read_file", {})),
        # Second call: LLM corrects and provides file_path
        _tool_response(("tc2", "read_file", {"file_path": "/tmp/test.py"})),
        # Third call: LLM responds with text
        _text_response("Read complete."),
    ])
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    result = await session.process_input("read test.py")

    assert result == "Read complete."
    # First tool call should NOT have executed (validation failed)
    # Second tool call should have executed (validation passed)
    assert tool.execute.call_count == 1
    tool.execute.assert_called_once_with({"file_path": "/tmp/test.py"})


@pytest.mark.asyncio
async def test_validation_error_emits_tool_call_end():
    """Validation failure emits agent:tool_call_end with error."""
    tool = _make_tool_with_schema("write_file", {
        "type": "object",
        "required": ["file_path", "content"],
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
    })

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _tool_response(("tc1", "write_file", {"content": "hello"})),  # missing file_path
        _text_response("ok."),
    ])
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"write_file": tool},
        hooks=hooks,
    )
    await session.process_input("write hello")

    # Find the tool_call_end event
    end_events = [
        (e, d) for e, d in hooks._emitted if e == "agent:tool_call_end"
    ]
    assert len(end_events) == 1
    assert "error" in end_events[0][1]
    assert "file_path" in end_events[0][1]["error"]
