"""Tests for loop detection (Task 4.2).

Spec coverage: DETECT-001 through DETECT-009 (Section 2.10).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.loop_detection import LoopDetector
from amplifier_module_loop_agent.turns import SteeringTurn


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
    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orchestrator, context, providers, tools, hooks


# ---------------------------------------------------------------------------
# LoopDetector unit tests
# ---------------------------------------------------------------------------


class TestLoopDetector:
    def test_no_loop_with_varied_calls(self):
        """Varied tool calls should not trigger detection."""
        d = LoopDetector(window_size=10)
        d.record("read_file", {"path": "a.py"})
        d.record("write_file", {"path": "b.py"})
        d.record("grep", {"pattern": "foo"})
        assert d.check() is None

    def test_message_matches_spec_verbatim(self):
        """Loop detection warning must match spec Section 2.10 exactly (M-2)."""
        window = 10
        d = LoopDetector(window_size=window)
        for _ in range(window):
            d.record("read_file", {"path": "a.py"})
        result = d.check()
        expected = (
            f"Loop detected: the last {window} tool calls follow a "
            "repeating pattern. Try a different approach."
        )
        assert result == expected

    def test_detects_pattern_length_1(self):
        """Same call repeated N times triggers detection (DETECT-006)."""
        d = LoopDetector(window_size=10)
        for _ in range(10):
            d.record("read_file", {"path": "a.py"})
        result = d.check()
        assert result is not None
        assert "loop" in result.lower() or "pattern" in result.lower()

    def test_detects_pattern_length_2(self):
        """Alternating pattern of 2 repeated triggers detection."""
        d = LoopDetector(window_size=10)
        for _ in range(5):
            d.record("read_file", {"path": "a.py"})
            d.record("write_file", {"path": "b.py"})
        result = d.check()
        assert result is not None

    def test_detects_pattern_length_3(self):
        """Pattern of 3 repeated triggers detection."""
        d = LoopDetector(window_size=12)
        for _ in range(4):
            d.record("read_file", {"path": "a.py"})
            d.record("grep", {"pattern": "foo"})
            d.record("write_file", {"path": "b.py"})
        result = d.check()
        assert result is not None

    def test_window_too_small_returns_none(self):
        """Fewer than window_size calls -> no detection (DETECT-005)."""
        d = LoopDetector(window_size=10)
        for _ in range(5):
            d.record("read_file", {"path": "a.py"})
        assert d.check() is None

    def test_different_args_not_detected(self):
        """Same tool name but different arguments are not a loop."""
        d = LoopDetector(window_size=10)
        for i in range(10):
            d.record("read_file", {"path": f"file_{i}.py"})
        assert d.check() is None

    def test_reset_clears_history(self):
        """reset() clears all recorded signatures."""
        d = LoopDetector(window_size=10)
        for _ in range(10):
            d.record("read_file", {"path": "a.py"})
        d.reset()
        assert d.check() is None

    def test_window_slides(self):
        """Only the last N signatures are considered."""
        d = LoopDetector(window_size=10)
        # Fill with varied calls
        for i in range(8):
            d.record("read_file", {"path": f"file_{i}.py"})
        # Then add 10 identical calls
        for _ in range(10):
            d.record("write_file", {"path": "same.py"})
        result = d.check()
        assert result is not None

    def test_signature_uses_sorted_json(self):
        """Arguments are hashed with sorted keys for consistency."""
        d = LoopDetector(window_size=10)
        for _ in range(5):
            d.record("read_file", {"path": "a.py", "encoding": "utf-8"})
            d.record("read_file", {"encoding": "utf-8", "path": "a.py"})
        # Both orderings should produce the same signature -> pattern of 1
        result = d.check()
        assert result is not None


# ---------------------------------------------------------------------------
# Integration: loop detection wired into agent_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_detection_emits_event():
    """agent:loop_detection event is emitted when loop is detected."""
    # Create 10 identical tool calls + 1 final text response
    responses = []
    for i in range(10):
        responses.append(
            _tool_response((f"tc{i}", "read_file", {"path": "same.py"}))
        )
    responses.append(_text_response("finally done"))

    orch, ctx, provs, tools, hooks = _make_harness(
        config={"enable_loop_detection": True, "loop_detection_window": 10},
        responses=responses,
    )
    await orch.execute("go", ctx, provs, tools, hooks)

    events = [e[0] for e in hooks._emitted]
    assert "agent:loop_detection" in events


@pytest.mark.asyncio
async def test_loop_detection_injects_steering_turn():
    """Loop detection injects a warning as a SteeringTurn."""
    responses = []
    for i in range(10):
        responses.append(
            _tool_response((f"tc{i}", "read_file", {"path": "same.py"}))
        )
    responses.append(_text_response("done"))

    orch, ctx, provs, tools, hooks = _make_harness(
        config={"enable_loop_detection": True, "loop_detection_window": 10},
        responses=responses,
    )
    await orch.execute("go", ctx, provs, tools, hooks)

    session = orch._session
    steering_turns = [t for t in session._history if isinstance(t, SteeringTurn)]
    assert len(steering_turns) >= 1
    # The warning should mention loop or pattern
    assert any(
        "loop" in t.content.lower() or "pattern" in t.content.lower()
        for t in steering_turns
    )


@pytest.mark.asyncio
async def test_loop_detection_disabled():
    """No detection when enable_loop_detection is False."""
    responses = []
    for i in range(10):
        responses.append(
            _tool_response((f"tc{i}", "read_file", {"path": "same.py"}))
        )
    responses.append(_text_response("done"))

    orch, ctx, provs, tools, hooks = _make_harness(
        config={"enable_loop_detection": False, "loop_detection_window": 10},
        responses=responses,
    )
    await orch.execute("go", ctx, provs, tools, hooks)

    events = [e[0] for e in hooks._emitted]
    assert "agent:loop_detection" not in events


@pytest.mark.asyncio
async def test_no_false_positive_with_varied_tools():
    """Varied tool calls should not trigger loop detection."""
    responses = [
        _tool_response(("tc1", "read_file", {"path": "a.py"})),
        _tool_response(("tc2", "write_file", {"path": "b.py"})),
        _tool_response(("tc3", "read_file", {"path": "c.py"})),
        _text_response("done"),
    ]
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"enable_loop_detection": True, "loop_detection_window": 10},
        responses=responses,
    )
    await orch.execute("go", ctx, provs, tools, hooks)

    events = [e[0] for e in hooks._emitted]
    assert "agent:loop_detection" not in events
