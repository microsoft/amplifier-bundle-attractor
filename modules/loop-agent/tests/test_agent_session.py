"""Tests for the core agentic loop (Task 1.5).

Spec coverage: LOOP-001 through LOOP-021, STOP-001 through STOP-005, ARCH-007-008.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.state import SessionState


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
    """ChatResponse with tool calls and optional accompanying text.

    Each positional arg is (call_id, tool_name, arguments).
    """
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
    """Create a mock tool with the standard Tool protocol attributes."""
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
    """Build orchestrator + mocks for testing the agentic loop.

    Returns (orchestrator, context, providers, tools, hooks).
    """
    # Provide a non-empty system_prompt by default so tests don't trip the
    # fail-loud guard introduced in docs/designs/layer-1-profile-owned-system-prompt.md §C.
    defaults = {"system_prompt": "You are a test coding agent."}
    cfg = {**defaults, **(config or {})}

    # Provider mock
    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=responses or [_text_response("done")]
    )
    providers = {"test": provider}

    # Tool mocks
    names = tool_names or ["read_file", "write_file"]
    tools = {n: _make_mock_tool(n) for n in names}

    # Hooks mock — records all emitted events
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)

    # Context (passed through but not used by AgentSession directly)
    context = MagicMock()

    # Orchestrator
    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)

    return orchestrator, context, providers, tools, hooks


# ---------------------------------------------------------------------------
# Core loop tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_natural_completion_no_tools():
    """Text-only response -> loop exits immediately with that text."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("Hello!")]
    )
    result = await orch.execute("hi", ctx, provs, tools, hooks)
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_tool_call_then_text_completion():
    """Tool call -> execute -> LLM responds with text -> done."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"path": "x.py"})),
            _text_response("Read complete"),
        ]
    )
    result = await orch.execute("read x.py", ctx, provs, tools, hooks)
    assert result == "Read complete"
    tools["read_file"].execute.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_tool_rounds():
    """Multiple rounds of tool calls before text completion."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _tool_response(("tc2", "write_file", {})),
            _text_response("All done"),
        ]
    )
    result = await orch.execute("do stuff", ctx, provs, tools, hooks)
    assert result == "All done"
    assert provs["test"].complete.call_count == 3


@pytest.mark.asyncio
async def test_parallel_tool_execution():
    """Multiple tool calls in a single response are all executed."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(
                ("tc1", "read_file", {}),
                ("tc2", "write_file", {}),
            ),
            _text_response("Both done"),
        ]
    )
    result = await orch.execute("do both", ctx, provs, tools, hooks)
    assert result == "Both done"
    tools["read_file"].execute.assert_called_once()
    tools["write_file"].execute.assert_called_once()


# ---------------------------------------------------------------------------
# Round-limit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tool_rounds_stops_loop():
    """Loop exits after max_tool_rounds_per_input tool rounds."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 2},
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _tool_response(("tc2", "read_file", {})),
            # Third LLM call should NOT happen
            _text_response("should not reach"),
        ],
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    # round 0<2: LLM(1)->tools->round=1, round 1<2: LLM(2)->tools->round=2, 2<2 false: exit
    assert provs["test"].complete.call_count == 2


@pytest.mark.asyncio
async def test_max_tool_rounds_returns_last_text():
    """When round limit hit, return the last text the LLM produced."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 1},
        responses=[
            _tool_response(("tc1", "read_file", {}), text="Working on it"),
        ],
    )
    result = await orch.execute("go", ctx, provs, tools, hooks)
    assert result == "Working on it"


@pytest.mark.asyncio
async def test_max_tool_rounds_zero_is_unlimited():
    """max_tool_rounds_per_input=0 means unlimited — loop does NOT cap at zero.

    Spec coding-agent-loop-spec.md:150 — 0 = unlimited.
    With 0 the session must run until the model stops requesting tools.
    """
    # 5 tool rounds, then a text-only response (natural completion)
    tool_responses = [
        _tool_response((f"tc{i}", "read_file", {})) for i in range(5)
    ]
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 0},
        responses=tool_responses + [_text_response("done after 5 rounds")],
    )
    result = await orch.execute("go", ctx, provs, tools, hooks)
    assert result == "done after 5 rounds"
    # All 6 provider calls happened (5 tool rounds + 1 final text response)
    assert provs["test"].complete.call_count == 6


@pytest.mark.asyncio
async def test_max_tool_rounds_three_caps_at_three():
    """max_tool_rounds_per_input=3 caps the loop at exactly 3 tool rounds.

    Spec coding-agent-loop-spec.md:231 — IF max > 0 AND round >= max: BREAK.
    """
    # Provide more responses than needed — only 3 should be consumed
    responses = [
        _tool_response((f"tc{i}", "read_file", {})) for i in range(10)
    ]
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 3},
        responses=responses,
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    # 3 tool rounds = 3 provider calls; the loop must exit before call #4
    assert provs["test"].complete.call_count == 3


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_returns_error_result():
    """Tool exception -> ToolResult(success=False), loop continues."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("Recovered"),
        ]
    )
    tools["read_file"].execute = AsyncMock(side_effect=RuntimeError("oops"))
    result = await orch.execute("read", ctx, provs, tools, hooks)
    assert result == "Recovered"  # LLM recovered from the error


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_result():
    """Unknown tool name -> error result fed back to LLM, not exception."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "nonexistent", {})),
            _text_response("Handled"),
        ]
    )
    result = await orch.execute("try it", ctx, provs, tools, hooks)
    assert result == "Handled"


# ---------------------------------------------------------------------------
# Session persistence tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_persists_across_calls():
    """History carries over between execute() calls."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _text_response("First"),
            _text_response("Second"),
        ]
    )
    r1 = await orch.execute("msg1", ctx, provs, tools, hooks)
    r2 = await orch.execute("msg2", ctx, provs, tools, hooks)
    assert r1 == "First"
    assert r2 == "Second"
    assert provs["test"].complete.call_count == 2
    # Second call's request should include history from first call
    second_request = provs["test"].complete.call_args_list[1][0][0]
    # system + user1 + assistant1 + user2 = 4 messages
    assert len(second_request.messages) == 4
    assert second_request.messages[0].role == "system"


@pytest.mark.asyncio
async def test_round_count_resets_per_input():
    """round_count resets for each execute() call (per-input, not per-session).

    With max_tool_rounds=2 each call can do 1 tool round + 1 text call.
    The key assertion: the second call also gets a full tool round,
    proving round_count was reset (not carried over from first call).
    """
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 2},
        responses=[
            # First execute: 1 tool round, then text
            _tool_response(("tc1", "read_file", {})),
            _text_response("First done"),
            # Second execute: 1 tool round, then text
            _tool_response(("tc2", "write_file", {})),
            _text_response("Second done"),
        ],
    )
    r1 = await orch.execute("first", ctx, provs, tools, hooks)
    r2 = await orch.execute("second", ctx, provs, tools, hooks)
    assert r1 == "First done"
    assert r2 == "Second done"


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_idle_after_completion():
    """State returns to IDLE after natural completion."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("done")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_state_idle_after_turn_limit():
    """State returns to IDLE after turn limit is reached."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 1},
        responses=[
            _tool_response(("tc1", "read_file", {})),
        ],
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Event emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_input_event_emitted():
    """agent:user_input event is emitted at the start of processing."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:user_input" in events


@pytest.mark.asyncio
async def test_tool_call_events_emitted():
    """agent:tool_call_start and agent:tool_call_end bracket tool execution."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    await orch.execute("do it", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:tool_call_start" in events
    assert "agent:tool_call_end" in events


@pytest.mark.asyncio
async def test_turn_limit_event_emitted():
    """agent:turn_limit event emitted when round limit is reached."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"max_tool_rounds_per_input": 1},
        responses=[
            _tool_response(("tc1", "read_file", {})),
        ],
    )
    await orch.execute("go", ctx, provs, tools, hooks)
    events = [e[0] for e in hooks._emitted]
    assert "agent:turn_limit" in events


@pytest.mark.asyncio
async def test_tool_choice_is_auto():
    """ChatRequest always uses tool_choice='auto'."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)
    request = provs["test"].complete.call_args_list[0][0][0]
    assert request.tool_choice == "auto"


# ---------------------------------------------------------------------------
# M-1: response_id capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_id_captured_on_assistant_turn():
    """response_id from provider response is stored on AssistantTurn (M-1)."""
    response = ChatResponse(
        content=[{"type": "text", "text": "hello"}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        response_id="resp_abc123",
    )
    orch, ctx, provs, tools, hooks = _make_harness(responses=[response])
    await orch.execute("hi", ctx, provs, tools, hooks)

    turn = orch._session._history.last_assistant_turn
    assert turn is not None
    assert turn.response_id == "resp_abc123"


@pytest.mark.asyncio
async def test_response_id_none_when_not_present():
    """response_id stays None when provider doesn't include one (M-1)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)

    turn = orch._session._history.last_assistant_turn
    assert turn is not None
    assert turn.response_id is None


# ---------------------------------------------------------------------------
# M-5: ToolRegistry integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_uses_tool_registry():
    """AgentSession stores tools as a ToolRegistry, not a plain dict (M-5)."""
    from amplifier_module_loop_agent.tool_registry import ToolRegistry

    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)

    # The internal _tools attribute should be a ToolRegistry instance
    assert isinstance(orch._session._tools, ToolRegistry)


@pytest.mark.asyncio
async def test_tool_registry_get_used_for_lookup():
    """AgentSession uses ToolRegistry.get() for tool lookup (M-5)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("done"),
        ]
    )
    await orch.execute("read", ctx, provs, tools, hooks)

    # Tool was found and executed via the registry
    tools["read_file"].execute.assert_called_once()
