"""Tests for streaming support in the agent session (Sprint 2, Task 2a).

Spec coverage: EVT-006 (ASSISTANT_TEXT_START), EVT-007 (ASSISTANT_TEXT_DELTA),
EVT-008 (ASSISTANT_TEXT_END) — streaming path.

Verifies:
  1. New streaming event constants exist in events.py
  2. When provider has .stream(), the session uses it and emits delta events
  3. When provider lacks .stream(), the non-streaming fallback works as before
  4. Streaming path handles tool calls correctly (stream -> tools -> stream -> done)
  5. The final text returned by process_input() is correct in both paths
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import (
    AGENT_ASSISTANT_TEXT_END,
    PROVIDER_RESPONSE,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],  # type: ignore[arg-type]
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(
    *tool_calls_tuple: tuple[str, str, dict],
    text: str = "",
) -> ChatResponse:
    """ChatResponse with tool calls and optional text."""
    content = [{"type": "text", "text": text}] if text else []
    return ChatResponse(
        content=content,  # type: ignore[arg-type]
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


def _make_recording_hooks():
    """Create hooks mock that records all emitted events."""
    hooks = MagicMock()
    hooks._emitted = []

    async def _recording_emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


def _make_harness(config=None, responses=None, tool_names=None):
    """Build orchestrator + non-streaming mocks for testing.

    Returns (orchestrator, context, providers, tools, hooks).
    """
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses or [_text_response("done")])
    providers = {"test": provider}
    names = tool_names or ["read_file", "write_file"]
    tools = {n: _make_mock_tool(n) for n in names}
    hooks = _make_recording_hooks()
    context = MagicMock()
    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orchestrator, context, providers, tools, hooks


def _make_streaming_provider(chunks_sequences):
    """Create a provider mock with a real async generator .stream() method.

    chunks_sequences: list of lists-of-dicts. Each inner list is one call
    to .stream(). Each dict has optional "content", "tool_calls", "usage" keys.

    The .stream() method is a real async generator function, which means
    inspect.isasyncgenfunction(provider.stream) returns True. This is the
    detection mechanism used by the agent session to decide streaming vs
    non-streaming path.
    """
    provider = MagicMock()
    provider.complete = AsyncMock()  # fallback, should not be called

    call_idx = 0

    async def _fake_stream(request):
        nonlocal call_idx
        if call_idx < len(chunks_sequences):
            chunks = chunks_sequences[call_idx]
            call_idx += 1
        else:
            chunks = []
        for chunk in chunks:
            yield chunk

    provider.stream = _fake_stream
    return provider


# ---------------------------------------------------------------------------
# 1. Event constants exist
# ---------------------------------------------------------------------------


def test_streaming_event_constants_exist():
    """The three streaming event constants must be importable from events.py."""
    from amplifier_module_loop_agent.events import (
        AGENT_ASSISTANT_TEXT_START,
        AGENT_ASSISTANT_TEXT_DELTA,
    )

    assert AGENT_ASSISTANT_TEXT_START == "agent:assistant_text_start"
    assert AGENT_ASSISTANT_TEXT_DELTA == "agent:assistant_text_delta"
    # AGENT_ASSISTANT_TEXT_END already exists
    assert AGENT_ASSISTANT_TEXT_END == "agent:assistant_text_end"


# ---------------------------------------------------------------------------
# 2. Streaming path: provider has .stream()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_provider_emits_text_start_event():
    """When provider has .stream(), agent emits agent:assistant_text_start."""
    from amplifier_module_loop_agent.events import AGENT_ASSISTANT_TEXT_START

    provider = _make_streaming_provider(
        [
            [{"content": "Hello "}, {"content": "world"}],
        ]
    )
    providers = {"test": provider}
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute("hi", MagicMock(), providers, tools, hooks)

    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_ASSISTANT_TEXT_START in event_names


@pytest.mark.asyncio
async def test_streaming_provider_emits_text_delta_events():
    """When provider has .stream(), agent emits agent:assistant_text_delta per chunk."""
    from amplifier_module_loop_agent.events import AGENT_ASSISTANT_TEXT_DELTA

    provider = _make_streaming_provider(
        [
            [{"content": "Hello "}, {"content": "world"}],
        ]
    )
    providers = {"test": provider}
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute("hi", MagicMock(), providers, tools, hooks)

    delta_events = [e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_DELTA]
    assert len(delta_events) == 2
    assert delta_events[0][1]["delta"] == "Hello "
    assert delta_events[1][1]["delta"] == "world"


@pytest.mark.asyncio
async def test_streaming_provider_emits_text_end_with_full_text():
    """When streaming, agent:assistant_text_end carries the full assembled text."""
    provider = _make_streaming_provider(
        [
            [{"content": "Hello "}, {"content": "world"}],
        ]
    )
    providers = {"test": provider}
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute("hi", MagicMock(), providers, tools, hooks)

    end_events = [e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_END]
    assert len(end_events) == 1
    assert end_events[0][1]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_streaming_returns_assembled_text():
    """Streaming path returns the fully assembled text as the execute() result."""
    provider = _make_streaming_provider(
        [
            [{"content": "Hello "}, {"content": "world"}],
        ]
    )
    providers = {"test": provider}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute("hi", MagicMock(), providers, {}, hooks)
    assert result == "Hello world"


@pytest.mark.asyncio
async def test_streaming_does_not_call_provider_complete():
    """When provider has .stream(), provider.complete() must NOT be called."""
    provider = _make_streaming_provider(
        [
            [{"content": "streamed"}],
        ]
    )
    providers = {"test": provider}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute("hi", MagicMock(), providers, {}, hooks)

    provider.complete.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Non-streaming fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_streaming_provider_uses_complete():
    """When provider has no real .stream(), provider.complete() is used."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("non-streamed")]
    )
    result = await orch.execute("hi", ctx, provs, tools, hooks)
    assert result == "non-streamed"
    provs["test"].complete.assert_called_once()


@pytest.mark.asyncio
async def test_non_streaming_still_emits_text_end():
    """Non-streaming path must still emit agent:assistant_text_end."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("fallback text")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)

    event_names = [e[0] for e in hooks._emitted]
    assert AGENT_ASSISTANT_TEXT_END in event_names

    text_event = next(e for e in hooks._emitted if e[0] == AGENT_ASSISTANT_TEXT_END)
    assert text_event[1]["text"] == "fallback text"


# ---------------------------------------------------------------------------
# 4. Streaming with tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_with_tool_calls_then_text():
    """Streaming handles tool calls in response, then text completion."""
    # First stream: tool calls, no text content
    # Second stream: text completion
    provider = _make_streaming_provider(
        [
            [{"tool_calls": [{"id": "tc1", "name": "read_file", "arguments": {}}]}],
            [{"content": "All done"}],
        ]
    )
    providers = {"test": provider}
    tools = {"read_file": _make_mock_tool("read_file")}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute("read it", MagicMock(), providers, tools, hooks)

    assert result == "All done"
    tools["read_file"].execute.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Event ordering in streaming path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_event_order():
    """Events flow: TEXT_START -> TEXT_DELTA(s) -> TEXT_END."""
    from amplifier_module_loop_agent.events import (
        AGENT_ASSISTANT_TEXT_START,
        AGENT_ASSISTANT_TEXT_DELTA,
    )

    provider = _make_streaming_provider(
        [
            [{"content": "A"}, {"content": "B"}],
        ]
    )
    providers = {"test": provider}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute("hi", MagicMock(), providers, {}, hooks)

    event_names = [e[0] for e in hooks._emitted]

    # TEXT_START before any TEXT_DELTA
    start_idx = event_names.index(AGENT_ASSISTANT_TEXT_START)
    first_delta_idx = event_names.index(AGENT_ASSISTANT_TEXT_DELTA)
    assert start_idx < first_delta_idx

    # All TEXT_DELTAs before TEXT_END
    last_delta_idx = (
        len(event_names) - 1 - event_names[::-1].index(AGENT_ASSISTANT_TEXT_DELTA)
    )
    end_idx = event_names.index(AGENT_ASSISTANT_TEXT_END)
    assert last_delta_idx < end_idx


# ---------------------------------------------------------------------------
# 6. Streaming collects usage from final chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_collects_usage():
    """Usage data from the stream is available (not lost)."""
    provider = _make_streaming_provider(
        [
            [
                {"content": "Hi"},
                {
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "total_tokens": 30,
                    }
                },
            ],
        ]
    )
    providers = {"test": provider}
    hooks = _make_recording_hooks()

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute("hi", MagicMock(), providers, {}, hooks)

    assert result == "Hi"
    # Usage should be emitted via provider:response
    resp_events = [e for e in hooks._emitted if e[0] == PROVIDER_RESPONSE]
    assert len(resp_events) >= 1
