"""Tests for TOOL_CALL_OUTPUT_DELTA event emission (Fix 2.4).

Spec coverage: EventKind TOOL_CALL_OUTPUT_DELTA — streaming tool output
to UIs as it's produced, especially for long-running shell commands.

For non-streaming tools (the common case), one delta with the full output
and is_final=True is emitted after tool execution completes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import AGENT_TOOL_CALL_OUTPUT_DELTA


# ---------------------------------------------------------------------------
# Test helpers (same pattern as test_events.py)
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(
    *tool_calls_tuple: tuple[str, str, dict],
    text: str = "",
) -> ChatResponse:
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
# Tests
# ---------------------------------------------------------------------------


class TestToolCallOutputDeltaEvent:
    """AGENT_TOOL_CALL_OUTPUT_DELTA event tests."""

    def test_event_constant_exists(self):
        """The event constant is defined in events module."""
        assert AGENT_TOOL_CALL_OUTPUT_DELTA == "agent:tool_call_output_delta"

    @pytest.mark.asyncio
    async def test_delta_emitted_after_tool_execution(self):
        """agent:tool_call_output_delta is emitted after tool produces output."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        event_names = [e[0] for e in hooks._emitted]
        assert AGENT_TOOL_CALL_OUTPUT_DELTA in event_names

    @pytest.mark.asyncio
    async def test_delta_contains_tool_name(self):
        """Delta event carries the tool_name."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_event = next(
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        )
        assert delta_event[1]["tool_name"] == "read_file"

    @pytest.mark.asyncio
    async def test_delta_contains_tool_call_id(self):
        """Delta event carries the tool_call_id."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_event = next(
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        )
        assert delta_event[1]["tool_call_id"] == "tc1"

    @pytest.mark.asyncio
    async def test_delta_contains_output(self):
        """Delta event carries the tool output in the 'delta' field."""
        tools_custom = {"read_file": _make_mock_tool("read_file", output="file contents here")}
        orch, ctx, provs, _, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        # Override tools with custom output
        tools = {"read_file": _make_mock_tool("read_file", output="file contents here"),
                 "write_file": _make_mock_tool("write_file")}
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_event = next(
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        )
        assert delta_event[1]["delta"] == "file contents here"

    @pytest.mark.asyncio
    async def test_delta_is_final_true_for_non_streaming(self):
        """For non-streaming tools, is_final=True (single delta with full output)."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_event = next(
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        )
        assert delta_event[1]["is_final"] is True

    @pytest.mark.asyncio
    async def test_delta_emitted_per_tool_call(self):
        """One delta emitted per tool call in a parallel batch."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(
                    ("tc1", "read_file", {}),
                    ("tc2", "write_file", {}),
                ),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_events = [
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        ]
        assert len(delta_events) == 2

    @pytest.mark.asyncio
    async def test_delta_between_tool_start_and_end(self):
        """Delta event is emitted between tool_call_start and tool_call_end."""
        from amplifier_module_loop_agent.events import (
            AGENT_TOOL_CALL_START,
            AGENT_TOOL_CALL_END,
        )

        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("done"),
            ]
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        names = [e[0] for e in hooks._emitted]

        start_idx = names.index(AGENT_TOOL_CALL_START)
        delta_idx = names.index(AGENT_TOOL_CALL_OUTPUT_DELTA)
        end_idx = names.index(AGENT_TOOL_CALL_END)
        assert start_idx < delta_idx < end_idx

    @pytest.mark.asyncio
    async def test_delta_emitted_even_on_tool_error(self):
        """Delta is emitted even when the tool returns an error result."""
        orch, ctx, provs, tools, hooks = _make_harness(
            responses=[
                _tool_response(("tc1", "read_file", {})),
                _text_response("recovered"),
            ]
        )
        tools["read_file"].execute = AsyncMock(
            return_value=ToolResult(success=False, output="permission denied")
        )
        await orch.execute("go", ctx, provs, tools, hooks)
        delta_events = [
            e for e in hooks._emitted if e[0] == AGENT_TOOL_CALL_OUTPUT_DELTA
        ]
        assert len(delta_events) == 1
        assert delta_events[0][1]["delta"] == "permission denied"
