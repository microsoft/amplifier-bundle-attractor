"""Tests for context window awareness (Task 4.4).

Spec coverage: Section 5.5 (Context Window Awareness).

After each LLM call, estimate total context usage using the heuristic
1 token ~ 4 chars. If usage exceeds 80% of context_window_size, emit
an agent:context_warning event. Informational only — no automatic
compaction.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator


# ---------------------------------------------------------------------------
# Test helpers
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


def _make_harness(
    config: dict | None = None,
    responses: list[ChatResponse] | None = None,
    tool_names: list[str] | None = None,
):
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
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    orchestrator = AgentOrchestrator(coordinator=coordinator, config=cfg)
    return orchestrator, context, providers, tools, hooks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_warning_when_context_window_unknown():
    """No warning when context_window_size is 0 (unknown/unset)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 0},
        responses=[_text_response("ok")],
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:context_warning" not in events


@pytest.mark.asyncio
async def test_no_warning_when_usage_below_threshold():
    """No warning when usage is well below 80% of context window."""
    # context_window_size=100000 tokens = 400000 chars.
    # A short prompt "hello" + "ok" is far below 80%.
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 100000},
        responses=[_text_response("ok")],
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:context_warning" not in events


@pytest.mark.asyncio
async def test_warning_emitted_at_80_percent():
    """Emit agent:context_warning when usage exceeds 80% of context window.

    context_window_size=100 tokens = 400 chars at 80% threshold.
    80% of 100 tokens = 80 tokens = 320 chars.
    If we send >320 chars of content, we should get a warning.
    """
    # Use a prompt that's large relative to the window
    big_prompt = "x" * 400  # 400 chars = 100 tokens, well over 80 of 100
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 100},
        responses=[_text_response("ok")],
    )
    await orch.execute(big_prompt, ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:context_warning" in events


@pytest.mark.asyncio
async def test_warning_contains_usage_info():
    """Warning event data includes usage and limit information."""
    big_prompt = "x" * 400
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 100},
        responses=[_text_response("ok")],
    )
    await orch.execute(big_prompt, ctx, provs, tools, hooks)
    warning_events = [
        (name, data)
        for name, data in hooks._emitted
        if name == "agent:context_warning"
    ]
    assert len(warning_events) >= 1
    data = warning_events[0][1]
    assert "approx_tokens" in data
    assert "context_window_size" in data
    assert "usage_percent" in data


@pytest.mark.asyncio
async def test_warning_during_tool_loop():
    """Warning emitted when context grows large during tool loop.

    Use a tool that returns a large result so that by the second LLM call,
    total history exceeds the threshold.
    context_window_size=25 tokens = 100 chars. 80% = 80 chars.
    Prompt (50 chars) + tool result (50 chars) > 80 chars.
    """
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 25},
        responses=[
            _tool_response(("tc1", "read_file", {"path": "a.py"})),
            _text_response("done reading"),
        ],
    )
    # Make the tool return a large-ish result
    tools["read_file"].execute = AsyncMock(
        return_value=ToolResult(success=True, output="x" * 100)
    )
    await orch.execute("x" * 50, ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:context_warning" in events


@pytest.mark.asyncio
async def test_no_warning_with_adequate_window():
    """No warning when window is large enough for the conversation."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"context_window_size": 1000000},  # 1M tokens
        responses=[
            _tool_response(("tc1", "read_file", {"path": "a.py"})),
            _text_response("done"),
        ],
    )
    await orch.execute("read the file", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:context_warning" not in events
