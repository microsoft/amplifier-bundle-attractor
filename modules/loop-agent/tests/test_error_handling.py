"""Tests for error handling and graceful shutdown (Task 1.7).

Spec coverage: ERR-001 through ERR-013, SHUT-001 through SHUT-009,
STOP-004, STOP-005.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.llm_errors import (
    AuthenticationError,
    ContextLengthError,
    LLMTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import (
    AGENT_CONTEXT_WARNING,
    AGENT_ERROR,
    AGENT_SESSION_END,
    PROVIDER_ERROR,
)
from amplifier_module_loop_agent.state import SessionState


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


def _make_harness(config=None, responses=None, tool_names=None, provider_error=None):
    """Build orchestrator + mocks. If provider_error is set, provider.complete raises it."""
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    provider = AsyncMock()
    if provider_error is not None:
        provider.complete = AsyncMock(side_effect=provider_error)
    else:
        provider.complete = AsyncMock(side_effect=responses or [_text_response("done")])
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
# Non-retryable LLM errors → session CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_error_closes_session():
    """AuthenticationError → session CLOSED, error re-raised."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=AuthenticationError("invalid key", provider="openai")
    )
    with pytest.raises(AuthenticationError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.CLOSED


@pytest.mark.asyncio
async def test_context_length_error_session_continues():
    """ContextLengthError → session stays IDLE (NOT closed), error NOT re-raised.

    Spec Appendix B / STOP-005: ContextLengthError is handled separately.
    Session must remain usable after a context-window overflow.
    """
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=ContextLengthError(
            "context too long", provider="openai", status_code=413
        )
    )
    # Must NOT raise — the session absorbs the overflow and stays alive
    await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_context_length_error_emits_context_warning():
    """ContextLengthError → agent:context_warning event emitted (not session_end).

    Spec Appendix B: ContextLengthError | Emit warning event, session continues.
    """
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=ContextLengthError(
            "context too long", provider="openai", status_code=413
        )
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_CONTEXT_WARNING in event_names
    # Verify the warning carries the context_length_exceeded flag
    warning_events = [e for e in hooks._emitted if e[0] == AGENT_CONTEXT_WARNING]
    assert any(e[1].get("context_length_exceeded") is True for e in warning_events)


@pytest.mark.asyncio
async def test_auth_error_emits_error_event():
    """AuthenticationError → agent:error event emitted."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=AuthenticationError("bad key", provider="openai")
    )
    with pytest.raises(AuthenticationError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_ERROR in event_names


@pytest.mark.asyncio
async def test_auth_error_emits_provider_error_event():
    """AuthenticationError → provider:error event with enriched data."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=AuthenticationError(
            "bad key", provider="openai", status_code=401
        )
    )
    with pytest.raises(AuthenticationError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    provider_err = next(e for e in hooks._emitted if e[0] == PROVIDER_ERROR)
    assert provider_err[1]["retryable"] is False
    assert provider_err[1]["status_code"] == 401
    assert provider_err[1]["provider"] == "openai"


@pytest.mark.asyncio
async def test_auth_error_emits_session_end():
    """AuthenticationError → agent:session_end emitted with CLOSED state."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=AuthenticationError("bad key", provider="openai")
    )
    with pytest.raises(AuthenticationError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    end_events = [e for e in hooks._emitted if e[0] == AGENT_SESSION_END]
    assert len(end_events) == 1
    assert end_events[0][1]["state"] == "closed"


# ---------------------------------------------------------------------------
# Retryable LLM errors → re-raise (provider handles retry internally)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_error_propagates():
    """RateLimitError → re-raised after provider exhausts retries."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=RateLimitError(
            "rate limited", provider="openai", retry_after=1.0
        )
    )
    with pytest.raises(RateLimitError):
        await orch.execute("hello", ctx, provs, tools, hooks)


@pytest.mark.asyncio
async def test_retryable_error_emits_provider_error():
    """RateLimitError → provider:error event with retryable=True."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=RateLimitError("rate limited", provider="openai")
    )
    with pytest.raises(RateLimitError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    provider_err = next(e for e in hooks._emitted if e[0] == PROVIDER_ERROR)
    assert provider_err[1]["retryable"] is True


@pytest.mark.asyncio
async def test_timeout_error_propagates():
    """LLMTimeoutError → re-raised."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=LLMTimeoutError("timeout", provider="openai")
    )
    with pytest.raises(LLMTimeoutError):
        await orch.execute("hello", ctx, provs, tools, hooks)


@pytest.mark.asyncio
async def test_provider_unavailable_propagates():
    """ProviderUnavailableError → re-raised."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=ProviderUnavailableError("down", provider="openai")
    )
    with pytest.raises(ProviderUnavailableError):
        await orch.execute("hello", ctx, provs, tools, hooks)


# ---------------------------------------------------------------------------
# Generic (non-LLM) exceptions → session CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_exception_closes_session():
    """Unexpected exception from provider → session CLOSED."""
    orch, ctx, provs, tools, hooks = _make_harness(
        provider_error=RuntimeError("unexpected failure")
    )
    with pytest.raises(RuntimeError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.CLOSED


@pytest.mark.asyncio
async def test_generic_exception_emits_error_event():
    """Unexpected exception → agent:error event emitted."""
    orch, ctx, provs, tools, hooks = _make_harness(provider_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_ERROR in event_names


@pytest.mark.asyncio
async def test_generic_exception_emits_session_end():
    """Unexpected exception → agent:session_end with CLOSED state."""
    orch, ctx, provs, tools, hooks = _make_harness(provider_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await orch.execute("hello", ctx, provs, tools, hooks)
    end_events = [e for e in hooks._emitted if e[0] == AGENT_SESSION_END]
    assert len(end_events) == 1
    assert end_events[0][1]["state"] == "closed"


# ---------------------------------------------------------------------------
# LLM error mid-loop (after successful tool round)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_error_after_tool_round_closes_session():
    """LLM error on second call (after a tool round) → CLOSED."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            AuthenticationError("expired", provider="openai"),
        ]
    )
    # Override provider to return first response then error
    provider = provs["test"]
    first_response = _tool_response(("tc1", "read_file", {}))
    provider.complete = AsyncMock(
        side_effect=[first_response, AuthenticationError("expired", provider="openai")]
    )
    with pytest.raises(AuthenticationError):
        await orch.execute("go", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.CLOSED


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_transitions_to_closed():
    """shutdown() transitions session to CLOSED."""
    orch, ctx, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    await orch.execute("hi", ctx, provs, tools, hooks)
    # Session is now IDLE
    assert orch._session._state_machine.state == SessionState.IDLE
    await orch._session.shutdown()
    assert orch._session._state_machine.state == SessionState.CLOSED


@pytest.mark.asyncio
async def test_shutdown_emits_session_end():
    """shutdown() emits agent:session_end with CLOSED state."""
    orch, ctx, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    await orch.execute("hi", ctx, provs, tools, hooks)
    # Clear recorded events from execute()
    hooks._emitted.clear()
    await orch._session.shutdown()
    end_events = [e for e in hooks._emitted if e[0] == AGENT_SESSION_END]
    assert len(end_events) == 1
    assert end_events[0][1]["state"] == "closed"


@pytest.mark.asyncio
async def test_shutdown_idempotent():
    """Calling shutdown() twice does not raise."""
    orch, ctx, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    await orch.execute("hi", ctx, provs, tools, hooks)
    await orch._session.shutdown()
    # Second shutdown should not raise
    await orch._session.shutdown()
    assert orch._session._state_machine.state == SessionState.CLOSED


# ---------------------------------------------------------------------------
# Tool-level errors (verify existing behavior from Task 1.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_returned_to_llm():
    """Tool execution error → error result sent to LLM, not exception."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {})),
            _text_response("Recovered"),
        ]
    )
    tools["read_file"].execute = AsyncMock(side_effect=RuntimeError("oops"))
    result = await orch.execute("read", ctx, provs, tools, hooks)
    assert result == "Recovered"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_result():
    """Unknown tool name → error result fed back to LLM, not exception."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "nonexistent", {})),
            _text_response("Handled"),
        ]
    )
    result = await orch.execute("try it", ctx, provs, tools, hooks)
    assert result == "Handled"


@pytest.mark.asyncio
async def test_unknown_tool_session_stays_open():
    """Unknown tool does NOT close the session — it's a tool-level error."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "nonexistent", {})),
            _text_response("ok"),
        ]
    )
    await orch.execute("try it", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.IDLE
