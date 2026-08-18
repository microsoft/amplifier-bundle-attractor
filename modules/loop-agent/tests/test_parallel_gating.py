"""Tests for parallel tool call gating (1a7).

Verifies that:
1. supports_parallel_tool_calls=True (default) uses parallel execution
2. supports_parallel_tool_calls=False uses sequential execution
3. Single tool call always executes sequentially (even when parallel=True)
4. Sequential execution preserves call order
5. Config flag can be set via from_dict()
"""

import asyncio

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


class _ConcurrencyTracker:
    """Records the high-water mark of simultaneously in-flight calls.

    Deterministic substitute for wall-clock timing assertions: a serial
    implementation can never observe more than 1 call in flight at once;
    a concurrent (gather-based) implementation of N overlapping calls must
    observe N in flight at some point. Immune to machine speed/contention.
    """

    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = asyncio.Lock()

    async def enter(self):
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

    async def exit(self):
        async with self._lock:
            self.in_flight -= 1


def _make_tracked_tool(name: str, tracker: "_ConcurrencyTracker", delay: float = 0.05):
    """Tool that records its in-flight window on a shared concurrency tracker.

    A brief `delay` widens the window in which overlapping calls would be
    observed together, but no assertion ever depends on its magnitude or on
    wall-clock elapsed time -- only on whether calls actually overlapped.
    """
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}

    async def tracked_execute(args):
        await tracker.enter()
        try:
            await asyncio.sleep(delay)
            return ToolResult(success=True, output="ok")
        finally:
            await tracker.exit()

    tool.execute = AsyncMock(side_effect=tracked_execute)
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
    """Sequential execution never overlaps calls (proves no gather).

    Deterministic (no wall-clock): with supports_parallel_tool_calls=False,
    a shared concurrency tracker's high-water mark must stay at 1 -- each
    tool call must fully complete (enter -> sleep -> exit) before the next
    one starts. Immune to slow/contended CI runners, unlike a timing bound.
    """
    tracker = _ConcurrencyTracker()
    tool_a = _make_tracked_tool("tool_a", tracker)
    tool_b = _make_tracked_tool("tool_b", tracker)

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

    assert tracker.max_in_flight == 1, (
        f"Expected sequential execution to never overlap (max 1 in flight), "
        f"but observed {tracker.max_in_flight} simultaneously in flight"
    )


# --- Parallel execution tests ---


@pytest.mark.asyncio
async def test_parallel_when_enabled_and_multiple():
    """With supports_parallel_tool_calls=True and multiple calls, they run concurrently.

    Deterministic (no wall-clock): a shared concurrency tracker records the
    high-water mark of simultaneously in-flight tool executions. A serial
    implementation can never exceed 1 in flight; a concurrent (gather-based)
    implementation of two overlapping calls must reach 2. This proves actual
    concurrency regardless of machine speed or scheduler contention -- unlike
    a wall-clock ratio, which a busy shared CI runner can fail spuriously.
    """
    tracker = _ConcurrencyTracker()
    tool_a = _make_tracked_tool("tool_a", tracker)
    tool_b = _make_tracked_tool("tool_b", tracker)

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

    await session.process_input("do both")

    assert tracker.max_in_flight >= 2, (
        f"Expected both tool calls to be in flight simultaneously (concurrent "
        f"execution), but max observed concurrency was {tracker.max_in_flight}"
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


# ---------------------------------------------------------------------------
# report_outcome ordering barrier (EXTENSIONS.md §35)
# ---------------------------------------------------------------------------
#
# `report_outcome` writes a SINGLE semantic completion register (the tool's
# `last_outcome`), which the orchestrator then transports to the parent as
# `metadata.report_outcome`.  Under asyncio.gather() the winner of two reports
# in one batch would be decided by scheduling order rather than by the order
# the model declared them — so "the last declared report wins" would not be a
# statement anyone could rely on.  A batch carrying at least one
# `report_outcome` therefore runs sequentially; every other batch keeps the
# configured parallel behavior.


def _make_report_outcome_tool(order_tracker: list):
    """Stand-in for tool-report-outcome that records declared-order writes."""
    tool = MagicMock()
    tool.name = "report_outcome"
    tool.description = "Report the outcome of your work."
    tool.input_schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    tool.last_outcome = None

    async def _execute(args):
        # Yield control so a gather()-based execution would interleave here.
        await asyncio.sleep(0)
        order_tracker.append(args.get("preferred_label"))
        tool.last_outcome = dict(args)
        return ToolResult(success=True, output="reported")

    tool.execute = AsyncMock(side_effect=_execute)
    return tool


@pytest.mark.asyncio
async def test_batch_with_report_outcome_runs_sequentially():
    """A batch containing report_outcome executes in provider-declared order."""
    order: list[str] = []
    report = _make_report_outcome_tool(order)
    slow = _make_slow_tool("slow_tool", delay=0.05)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(
                ("tc1", "slow_tool", {}),
                ("tc2", "report_outcome", {"status": "fail", "preferred_label": "a"}),
                ("tc3", "report_outcome", {"status": "success", "preferred_label": "b"}),
            ),
        ]
    )

    session = AgentSession(
        config=SessionConfig(
            system_prompt="You are a test coding agent.",
            supports_parallel_tool_calls=True,
        ),
        provider=provider,
        tools={"slow_tool": slow, "report_outcome": report},
        hooks=_make_hooks(),
    )
    await session.process_input("go")

    # Declared order preserved, so the LAST declared report is the one left in
    # the register — deterministically, not by scheduling luck.
    assert order == ["a", "b"]
    assert report.last_outcome["preferred_label"] == "b"


@pytest.mark.asyncio
async def test_batch_without_report_outcome_still_runs_in_parallel():
    """The barrier is scoped to report_outcome — ordinary batches are unchanged."""
    tracker = _ConcurrencyTracker()
    tool_a = _make_tracked_tool("tool_a", tracker, delay=0.05)
    tool_b = _make_tracked_tool("tool_b", tracker, delay=0.05)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _multi_tool_response(("tc1", "tool_a", {}), ("tc2", "tool_b", {})),
            _text_response("done."),
        ]
    )

    session = AgentSession(
        config=SessionConfig(
            system_prompt="You are a test coding agent.",
            supports_parallel_tool_calls=True,
        ),
        provider=provider,
        tools={"tool_a": tool_a, "tool_b": tool_b},
        hooks=_make_hooks(),
    )
    await session.process_input("go")

    assert tracker.max_in_flight == 2
