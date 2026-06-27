"""Tests for parallel tool call gating (1a7).

Verifies that:
1. supports_parallel_tool_calls=True (default) uses parallel execution
2. supports_parallel_tool_calls=False uses sequential execution
3. Single tool call always executes sequentially (even when parallel=True)
4. Sequential execution preserves call order
5. Config flag can be set via from_dict()
"""

import asyncio
import time

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


def _multi_tool_response(*tool_calls_tuple) -> ChatResponse:
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


def _make_ordering_tool(name: str, order_tracker: list):
    """Tool that records execution order."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}

    async def tracking_execute(args):
        order_tracker.append(name)
        return ToolResult(success=True, output=f"{name} done")

    tool.execute = AsyncMock(side_effect=tracking_execute)
    return tool


def _make_slow_tool(name: str, delay: float = 0.1, output: str = "ok"):
    """Tool that takes `delay` seconds to execute."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}

    async def slow_execute(args):
        await asyncio.sleep(delay)
        return ToolResult(success=True, output=output)

    tool.execute = AsyncMock(side_effect=slow_execute)
    return tool


# --- Config tests ---


class TestConfigFlag:
    """Tests for the supports_parallel_tool_calls config field."""

    def test_default_is_true(self):
        config = SessionConfig(system_prompt="You are a test coding agent.")
        assert config.supports_parallel_tool_calls is True

    def test_can_set_false(self):
        config = SessionConfig(supports_parallel_tool_calls=False)
        assert config.supports_parallel_tool_calls is False

    def test_from_dict_sets_flag(self):
        config = SessionConfig.from_dict({"supports_parallel_tool_calls": False})
        assert config.supports_parallel_tool_calls is False

    def test_from_dict_default(self):
        config = SessionConfig.from_dict({})
        assert config.supports_parallel_tool_calls is True


# --- Sequential execution tests ---


@pytest.mark.asyncio
async def test_sequential_when_parallel_disabled():
    """With supports_parallel_tool_calls=False, tools execute sequentially."""
    order: list[str] = []
    tool_a = _make_ordering_tool("tool_a", order)
    tool_b = _make_ordering_tool("tool_b", order)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(
                ("tc1", "tool_a", {}),
                ("tc2", "tool_b", {}),
            ),
            _text_response("done."),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent.", supports_parallel_tool_calls=False),
        provider=provider,
        tools={"tool_a": tool_a, "tool_b": tool_b},
        hooks=hooks,
    )
    await session.process_input("do both")

    # Both tools executed
    assert tool_a.execute.call_count == 1
    assert tool_b.execute.call_count == 1

    # Sequential means order is deterministic: a then b
    assert order == ["tool_a", "tool_b"]


@pytest.mark.asyncio
async def test_sequential_preserves_order():
    """Sequential execution preserves the order tools were returned by the LLM."""
    order: list[str] = []
    tools = {}
    for name in ["first", "second", "third"]:
        tools[name] = _make_ordering_tool(name, order)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(
                ("tc1", "first", {}),
                ("tc2", "second", {}),
                ("tc3", "third", {}),
            ),
            _text_response("done."),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent.", supports_parallel_tool_calls=False),
        provider=provider,
        tools=tools,
        hooks=hooks,
    )
    await session.process_input("do all three")

    assert order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_sequential_timing():
    """Sequential execution takes longer than parallel (proves no gather)."""
    delay = 0.05  # 50ms per tool
    tool_a = _make_slow_tool("tool_a", delay=delay)
    tool_b = _make_slow_tool("tool_b", delay=delay)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(
                ("tc1", "tool_a", {}),
                ("tc2", "tool_b", {}),
            ),
            _text_response("done."),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent.", supports_parallel_tool_calls=False),
        provider=provider,
        tools={"tool_a": tool_a, "tool_b": tool_b},
        hooks=hooks,
    )

    start = time.monotonic()
    await session.process_input("do both")
    elapsed = time.monotonic() - start

    # Sequential: should take at least 2x the delay (100ms)
    # With parallel it would take ~50ms
    assert elapsed >= delay * 1.5, (
        f"Expected sequential timing (>={delay * 1.5:.3f}s), got {elapsed:.3f}s"
    )


# --- Parallel execution tests ---


@pytest.mark.asyncio
async def test_parallel_when_enabled_and_multiple():
    """With supports_parallel_tool_calls=True and multiple calls, runs in parallel."""
    delay = 0.1
    tool_a = _make_slow_tool("tool_a", delay=delay)
    tool_b = _make_slow_tool("tool_b", delay=delay)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(
                ("tc1", "tool_a", {}),
                ("tc2", "tool_b", {}),
            ),
            _text_response("done."),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent.", supports_parallel_tool_calls=True),
        provider=provider,
        tools={"tool_a": tool_a, "tool_b": tool_b},
        hooks=hooks,
    )

    start = time.monotonic()
    await session.process_input("do both")
    elapsed = time.monotonic() - start

    # Parallel: should complete in roughly delay time, not 2x
    # Use generous bound to avoid flaky CI
    assert elapsed < delay * 1.8, (
        f"Expected parallel timing (<{delay * 1.8:.3f}s), got {elapsed:.3f}s"
    )


@pytest.mark.asyncio
async def test_single_tool_always_sequential():
    """A single tool call is sequential even with parallel=True."""
    order: list[str] = []
    tool_a = _make_ordering_tool("tool_a", order)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(("tc1", "tool_a", {})),
            _text_response("done."),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent.", supports_parallel_tool_calls=True),
        provider=provider,
        tools={"tool_a": tool_a},
        hooks=hooks,
    )
    await session.process_input("do one")

    assert tool_a.execute.call_count == 1
    assert order == ["tool_a"]
