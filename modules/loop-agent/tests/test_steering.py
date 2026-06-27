"""Tests for steering and follow-up queues (Task 4.1).

Spec coverage: STEER-001 through STEER-010.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.steering import FollowUpQueue, SteeringQueue
from amplifier_module_loop_agent.turns import SteeringTurn


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(
    *tool_calls_tuple: tuple[str, str, dict],
    text: str = "",
) -> ChatResponse:
    """ChatResponse with tool calls and optional accompanying text."""
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
# SteeringQueue unit tests
# ---------------------------------------------------------------------------


class TestSteeringQueue:
    @pytest.mark.asyncio
    async def test_steer_and_drain(self):
        """steer() enqueues, drain() returns all pending messages."""
        q = SteeringQueue()
        q.steer("msg1")
        q.steer("msg2")
        result = q.drain()
        assert result == ["msg1", "msg2"]

    @pytest.mark.asyncio
    async def test_drain_empty(self):
        """drain() returns empty list when no messages queued."""
        q = SteeringQueue()
        assert q.drain() == []

    @pytest.mark.asyncio
    async def test_drain_clears_queue(self):
        """drain() removes messages from the queue."""
        q = SteeringQueue()
        q.steer("msg")
        q.drain()
        assert q.drain() == []


# ---------------------------------------------------------------------------
# FollowUpQueue unit tests
# ---------------------------------------------------------------------------


class TestFollowUpQueue:
    @pytest.mark.asyncio
    async def test_follow_up_and_drain(self):
        """follow_up() enqueues, drain() returns one at a time."""
        q = FollowUpQueue()
        q.follow_up("a")
        q.follow_up("b")
        assert q.drain() == "a"
        assert q.drain() == "b"
        assert q.drain() is None

    @pytest.mark.asyncio
    async def test_drain_empty(self):
        """drain() returns None when empty."""
        q = FollowUpQueue()
        assert q.drain() is None

    @pytest.mark.asyncio
    async def test_is_empty(self):
        """is_empty property reflects queue state."""
        q = FollowUpQueue()
        assert q.is_empty
        q.follow_up("x")
        assert not q.is_empty
        q.drain()
        assert q.is_empty


# ---------------------------------------------------------------------------
# Integration: steering injected between tool rounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_injects_message_between_rounds():
    """Steering message appears after tool round, before next LLM call (STEER-001)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "test.py"})),
            _text_response("I see the steering message, adjusting approach"),
        ]
    )
    # Queue a steering message before execution
    orch.steer("Focus on the login module instead")
    result = await orch.execute("analyze the code", ctx, provs, tools, hooks)

    # Verify SteeringTurn was added to history
    session = orch._session
    steering_turns = [t for t in session._history if isinstance(t, SteeringTurn)]
    assert len(steering_turns) >= 1
    assert steering_turns[0].content == "Focus on the login module instead"


@pytest.mark.asyncio
async def test_steer_emits_steering_injected_event():
    """agent:steering_injected event is emitted when steering is drained."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    orch.steer("redirect")
    await orch.execute("go", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:steering_injected" in events


@pytest.mark.asyncio
async def test_steering_drained_before_first_llm_call():
    """Steering is drained before the first LLM call in process_input."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    orch.steer("early message")
    await orch.execute("go", ctx, provs, tools, hooks)

    # The steering turn should appear before the first LLM call
    session = orch._session
    steering_turns = [t for t in session._history if isinstance(t, SteeringTurn)]
    assert len(steering_turns) == 1


@pytest.mark.asyncio
async def test_steering_drained_after_tool_round():
    """Steering drained after each tool execution round (STEER-002)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    # We need to steer DURING tool execution. We do this by having the tool
    # execution side-effect queue a steering message.
    original_execute = tools["read_file"].execute

    async def steer_during_tool(args):
        orch.steer("mid-task redirect")
        return ToolResult(success=True, output="file content")

    tools["read_file"].execute = AsyncMock(side_effect=steer_during_tool)
    await orch.execute("read file", ctx, provs, tools, hooks)

    session = orch._session
    steering_turns = [t for t in session._history if isinstance(t, SteeringTurn)]
    assert len(steering_turns) >= 1
    assert steering_turns[0].content == "mid-task redirect"


# ---------------------------------------------------------------------------
# Integration: follow-up queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_processed_after_loop_completes():
    """Follow-up messages are processed after current input completes (STEER-005)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _text_response("done with first"),
            _text_response("done with followup"),
        ]
    )
    orch.follow_up("Now also update the tests")
    result = await orch.execute("update main.py", ctx, provs, tools, hooks)

    # Both the original and the follow-up should have been processed
    assert provs["test"].complete.call_count == 2


@pytest.mark.asyncio
async def test_followup_multiple_messages():
    """Multiple follow-up messages are processed in order."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _text_response("first done"),
            _text_response("followup1 done"),
            _text_response("followup2 done"),
        ]
    )
    orch.follow_up("followup1")
    orch.follow_up("followup2")
    await orch.execute("main task", ctx, provs, tools, hooks)
    assert provs["test"].complete.call_count == 3


@pytest.mark.asyncio
async def test_session_idle_only_when_followup_empty():
    """Session only goes IDLE when both loop exits AND follow_up queue is empty."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _text_response("first"),
            _text_response("second"),
        ]
    )
    orch.follow_up("more work")
    await orch.execute("start", ctx, provs, tools, hooks)

    # After everything completes, session should be in IDLE
    from amplifier_module_loop_agent.state import SessionState

    assert orch._session._state_machine.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Orchestrator API surface tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_exposes_steer_method():
    """AgentOrchestrator has a steer() method."""
    orch, ctx, provs, tools, hooks = _make_harness()
    assert hasattr(orch, "steer")
    orch.steer("test")  # Should not raise


@pytest.mark.asyncio
async def test_orchestrator_exposes_follow_up_method():
    """AgentOrchestrator has a follow_up() method."""
    orch, ctx, provs, tools, hooks = _make_harness()
    assert hasattr(orch, "follow_up")
    orch.follow_up("test")  # Should not raise


@pytest.mark.asyncio
async def test_orchestrator_exposes_session_property():
    """AgentOrchestrator has a session property."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    # Before first execute, session is None
    assert orch.session is None
    await orch.execute("hi", ctx, provs, tools, hooks)
    # After execute, session is available
    assert orch.session is not None
