"""Tests for UnifiedProviderAdapter.

Validates the adapter that bridges unified-llm-client types to the
duck-type contract expected by AgentSession (ChatRequest/ChatResponse).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_loop_agent.unified_provider_adapter import UnifiedProviderAdapter


def test_adapter_is_importable():
    """Smoke test: module imports without error."""
    assert UnifiedProviderAdapter is not None


def test_constructor_stores_config():
    """Constructor stores provider_name, model, and accepts injected client."""
    mock_client = MagicMock()
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    assert adapter._provider_name == "anthropic"
    assert adapter._model == "claude-sonnet-4-20250514"
    assert adapter._client is mock_client


# ---------------------------------------------------------------------------
# Bug 1: UnifiedProviderAdapter.close() releases the wrapped client
#
# The adapter wraps a unified_llm.Client that owns an AsyncAnthropic/httpx
# client. Per the spec (unified-llm-spec.md:183), the client implements an
# async close(); the adapter must expose close() so its owner (the child
# session / spawn finalize) can release the connection within the event loop,
# preventing the "Event loop is closed" RuntimeError at corpus scale.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_close_closes_client():
    """UnifiedProviderAdapter.close() must await the wrapped client's close()."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    await adapter.close()
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_adapter_close_is_safe_when_client_has_no_close():
    """close() must not raise if the wrapped client lacks a close() method."""

    class _NoCloseClient:
        pass

    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=_NoCloseClient(),
    )
    # Must be a no-op, not an AttributeError.
    await adapter.close()


# ---------------------------------------------------------------------------
# Task 3: Simple text message translation
# ---------------------------------------------------------------------------
from amplifier_core.message_models import ChatRequest, Message as CoreMessage
from unified_llm.types import ContentKind, Role


def test_translate_text_messages():
    """String content messages translate to TEXT ContentParts."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    request = ChatRequest(
        messages=[
            CoreMessage(role="system", content="You are helpful."),
            CoreMessage(role="user", content="Hello"),
            CoreMessage(role="assistant", content="Hi there"),
        ],
        tools=None,
    )
    ulm_request = adapter._translate_request(request)

    assert len(ulm_request.messages) == 3
    assert ulm_request.messages[0].role == Role.SYSTEM
    assert ulm_request.messages[0].content[0].kind == ContentKind.TEXT
    assert ulm_request.messages[0].content[0].text == "You are helpful."
    assert ulm_request.messages[1].role == Role.USER
    assert ulm_request.messages[1].content[0].text == "Hello"
    assert ulm_request.messages[2].role == Role.ASSISTANT
    assert ulm_request.messages[2].content[0].text == "Hi there"


# ---------------------------------------------------------------------------
# Task 4: Complex content block translation
# ---------------------------------------------------------------------------
from amplifier_core.message_models import TextBlock, ThinkingBlock


def test_translate_content_blocks():
    """TextBlock and ThinkingBlock translate to correct ContentParts."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    request = ChatRequest(
        messages=[
            CoreMessage(
                role="assistant",
                content=[
                    ThinkingBlock(thinking="Let me think...", signature="sig_abc123"),
                    TextBlock(text="Here is my answer"),
                ],
            ),
        ],
        tools=None,
    )
    ulm_request = adapter._translate_request(request)

    parts = ulm_request.messages[0].content
    assert len(parts) == 2

    # ThinkingBlock -> THINKING ContentPart with signature
    assert parts[0].kind == ContentKind.THINKING
    assert parts[0].thinking is not None
    assert parts[0].thinking.text == "Let me think..."
    assert parts[0].thinking.signature == "sig_abc123"

    # TextBlock -> TEXT ContentPart
    assert parts[1].kind == ContentKind.TEXT
    assert parts[1].text == "Here is my answer"


def test_translate_tool_result_message():
    """Tool result message translates to TOOL_RESULT ContentPart."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    request = ChatRequest(
        messages=[
            CoreMessage(role="tool", content="file contents here", tool_call_id="tc_1"),
        ],
        tools=None,
    )
    ulm_request = adapter._translate_request(request)

    msg = ulm_request.messages[0]
    assert msg.role == Role.TOOL
    assert msg.tool_call_id == "tc_1"
    assert msg.content[0].kind == ContentKind.TOOL_RESULT
    assert msg.content[0].tool_result is not None
    assert msg.content[0].tool_result.tool_call_id == "tc_1"
    assert msg.content[0].tool_result.content == "file contents here"


# ---------------------------------------------------------------------------
# Task 5: Generation parameters
# ---------------------------------------------------------------------------


def test_translate_request_params():
    """reasoning_effort and model are passed through to unified-llm Request."""
    adapter = UnifiedProviderAdapter(
        provider_name="openai",
        model="o3-mini",
        client=MagicMock(),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Think hard")],
        reasoning_effort="high",
    )
    ulm_request = adapter._translate_request(request)

    assert ulm_request.model == "o3-mini"
    assert ulm_request.provider == "openai"
    assert ulm_request.reasoning_effort == "high"
    # Tools are NOT passed — agent owns the tool loop
    assert ulm_request.tools is None


# ---------------------------------------------------------------------------
# Task 6: Text response translation
# ---------------------------------------------------------------------------
from unified_llm.types import (
    ContentPart,
    FinishReason,
    Message as ULMMessage,
    Response as ULMResponse,
    Usage as ULMUsage,
)


def test_translate_text_response():
    """Response with TEXT content -> ChatResponse with TextBlock."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    ulm_response = ULMResponse(
        id="resp_1",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[ContentPart(kind=ContentKind.TEXT, text="Hello world")],
        ),
        finish_reason=FinishReason(reason="stop"),
        usage=ULMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    chat_response = adapter._translate_response(ulm_response)

    assert len(chat_response.content) == 1
    assert chat_response.content[0].type == "text"
    assert chat_response.content[0].text == "Hello world"


# ---------------------------------------------------------------------------
# Task 7: Thinking response with signature preservation
# ---------------------------------------------------------------------------
from unified_llm.types import ThinkingData


def test_translate_thinking_response_with_signature():
    """THINKING ContentPart -> ThinkingBlock with signature preserved."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    ulm_response = ULMResponse(
        id="resp_2",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[
                ContentPart(
                    kind=ContentKind.THINKING,
                    thinking=ThinkingData(
                        text="I should analyze this carefully...",
                        signature="sig_roundtrip_abc",
                    ),
                ),
                ContentPart(kind=ContentKind.TEXT, text="Here is my analysis"),
            ],
        ),
        finish_reason=FinishReason(reason="stop"),
        usage=ULMUsage(input_tokens=20, output_tokens=50, total_tokens=70),
    )
    chat_response = adapter._translate_response(ulm_response)

    assert len(chat_response.content) == 2
    # ThinkingBlock with signature
    thinking = chat_response.content[0]
    assert thinking.type == "thinking"
    assert thinking.thinking == "I should analyze this carefully..."
    assert thinking.signature == "sig_roundtrip_abc"
    # TextBlock
    assert chat_response.content[1].type == "text"
    assert chat_response.content[1].text == "Here is my analysis"


# ---------------------------------------------------------------------------
# Task 8: Tool call translation
# ---------------------------------------------------------------------------
from unified_llm.types import ToolCallData as ULMToolCallData


def test_translate_tool_calls():
    """ToolCallData from response -> amplifier-core ToolCall objects."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    ulm_response = ULMResponse(
        id="resp_3",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[
                ContentPart(
                    kind=ContentKind.TOOL_CALL,
                    tool_call=ULMToolCallData(
                        id="tc_1",
                        name="read_file",
                        arguments={"path": "/tmp/test.py"},
                    ),
                ),
                ContentPart(
                    kind=ContentKind.TOOL_CALL,
                    tool_call=ULMToolCallData(
                        id="tc_2",
                        name="write_file",
                        arguments={"path": "/tmp/out.py", "content": "hello"},
                    ),
                ),
            ],
        ),
        finish_reason=FinishReason(reason="tool_calls"),
        usage=ULMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
    )
    chat_response = adapter._translate_response(ulm_response)

    assert chat_response.tool_calls is not None
    assert len(chat_response.tool_calls) == 2
    assert chat_response.tool_calls[0].id == "tc_1"
    assert chat_response.tool_calls[0].name == "read_file"
    assert chat_response.tool_calls[0].arguments == {"path": "/tmp/test.py"}
    assert chat_response.tool_calls[1].id == "tc_2"
    assert chat_response.tool_calls[1].name == "write_file"


# ---------------------------------------------------------------------------
# Task 9: Usage translation
# ---------------------------------------------------------------------------
from amplifier_core.message_models import Usage as CoreUsage


def test_translate_usage_all_fields():
    """All usage fields map correctly including optional ones."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    ulm_response = ULMResponse(
        id="resp_4",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[ContentPart(kind=ContentKind.TEXT, text="ok")],
        ),
        finish_reason=FinishReason(reason="stop"),
        usage=ULMUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            reasoning_tokens=20,
            cache_read_tokens=30,
            cache_write_tokens=10,
        ),
    )
    chat_response = adapter._translate_response(ulm_response)

    assert chat_response.usage is not None
    assert chat_response.usage.input_tokens == 100
    assert chat_response.usage.output_tokens == 50
    assert chat_response.usage.total_tokens == 150
    assert chat_response.usage.reasoning_tokens == 20
    assert chat_response.usage.cache_read_tokens == 30
    assert chat_response.usage.cache_write_tokens == 10


def test_translate_usage_minimal():
    """Usage with only required fields maps correctly, optional fields are None."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    ulm_response = ULMResponse(
        id="resp_5",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[ContentPart(kind=ContentKind.TEXT, text="ok")],
        ),
        finish_reason=FinishReason(reason="stop"),
        usage=ULMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    chat_response = adapter._translate_response(ulm_response)

    assert chat_response.usage is not None
    assert chat_response.usage.input_tokens == 10
    assert chat_response.usage.output_tokens == 5
    assert chat_response.usage.total_tokens == 15


# ---------------------------------------------------------------------------
# Task 10: complete() end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_end_to_end():
    """complete() translates request, calls client, translates response."""
    # Build a mock unified-llm response
    ulm_response = ULMResponse(
        id="resp_e2e",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=ULMMessage(
            role=Role.ASSISTANT,
            content=[ContentPart(kind=ContentKind.TEXT, text="Test response")],
        ),
        finish_reason=FinishReason(reason="stop"),
        usage=ULMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=ulm_response)

    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hello")],
    )

    result = await adapter.complete(request)

    # Verify client was called with translated request
    mock_client.complete.assert_called_once()
    call_arg = mock_client.complete.call_args[0][0]
    assert call_arg.model == "claude-sonnet-4-20250514"
    assert call_arg.provider == "anthropic"
    assert call_arg.messages[0].role == Role.USER

    # Verify response was translated back
    assert len(result.content) == 1
    assert result.content[0].text == "Test response"
    assert result.usage.input_tokens == 10


# ---------------------------------------------------------------------------
# Task 11: Error mapping (SDKError -> LLMError)
# ---------------------------------------------------------------------------
from amplifier_core.llm_errors import (
    AuthenticationError as CoreAuthError,
    RateLimitError as CoreRateLimitError,
    ProviderUnavailableError,
    LLMTimeoutError,
    ContentFilterError as CoreContentFilterError,
    ContextLengthError as CoreContextLengthError,
    StreamError as CoreStreamError,
    LLMError,
)
from unified_llm import Client, StreamProtocolError, errors as ulm_errors


@pytest.mark.parametrize(
    "ulm_error, expected_type, expected_retryable",
    [
        (
            ulm_errors.AuthenticationError(
                message="bad key", provider="anthropic", status_code=401
            ),
            CoreAuthError,
            False,
        ),
        (
            ulm_errors.RateLimitError(
                message="slow down", provider="anthropic", status_code=429
            ),
            CoreRateLimitError,
            True,
        ),
        (
            ulm_errors.ServerError(
                message="internal error", provider="anthropic", status_code=500
            ),
            ProviderUnavailableError,
            True,
        ),
        (
            ulm_errors.ContentFilterError(message="blocked", provider="anthropic"),
            CoreContentFilterError,
            False,
        ),
        (
            ulm_errors.ContextLengthError(
                message="too long", provider="anthropic", status_code=413
            ),
            CoreContextLengthError,
            False,
        ),
        (
            ulm_errors.RequestTimeoutError("timed out"),
            LLMTimeoutError,
            True,
        ),
        (
            ulm_errors.NetworkError("connection refused"),
            ProviderUnavailableError,
            True,
        ),
        (
            ulm_errors.StreamError("stream broke"),
            CoreStreamError,
            True,
        ),
        (
            ulm_errors.ConfigurationError("bad config"),
            LLMError,
            False,
        ),
    ],
    ids=[
        "auth",
        "rate_limit",
        "server",
        "content_filter",
        "context_length",
        "timeout",
        "network",
        "stream",
        "config",
    ],
)
def test_map_error(ulm_error, expected_type, expected_retryable):
    """Each SDKError maps to the correct LLMError with right retryability."""
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    mapped = adapter._map_error(ulm_error)

    assert isinstance(mapped, expected_type), (
        f"Expected {expected_type.__name__}, got {type(mapped).__name__}"
    )
    assert mapped.retryable == expected_retryable
    assert mapped.provider == "anthropic"


# ---------------------------------------------------------------------------
# Task 12: Wire error mapping into complete() with exception chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_maps_sdk_error_to_llm_error():
    """SDKError from client.complete() is caught and re-raised as LLMError."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        side_effect=ulm_errors.RateLimitError(
            message="429 rate limited", provider="anthropic", status_code=429
        )
    )
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hello")],
    )

    with pytest.raises(CoreRateLimitError) as exc_info:
        await adapter.complete(request)

    assert exc_info.value.retryable is True
    assert exc_info.value.provider == "anthropic"
    # Original error is chained
    assert exc_info.value.__cause__ is not None


@pytest.mark.asyncio
async def test_complete_maps_auth_error():
    """AuthenticationError from client -> CoreAuthError (non-retryable)."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        side_effect=ulm_errors.AuthenticationError(
            message="invalid key", provider="anthropic", status_code=401
        )
    )
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hello")],
    )

    with pytest.raises(CoreAuthError) as exc_info:
        await adapter.complete(request)

    assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# Task 13: Basic streaming — TEXT_DELTA
# ---------------------------------------------------------------------------
from unified_llm.types import StreamEvent, StreamEventType


async def _collect_stream(adapter, request):
    """Helper: collect all chunks from adapter.stream()."""
    chunks = []
    async for chunk in adapter.stream(request):
        chunks.append(chunk)
    return chunks


def _make_streaming_adapter(*events):
    """Create adapter with a mock client that streams given events."""

    async def fake_stream(request):
        for event in events:
            yield event

    mock_client = MagicMock()
    mock_client.stream = fake_stream
    return UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )


@pytest.mark.asyncio
async def test_stream_text_deltas():
    """TEXT_DELTA events yield {content: delta} chunks."""
    adapter = _make_streaming_adapter(
        StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Hello"),
        StreamEvent(type=StreamEventType.TEXT_DELTA, delta=" world"),
        StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hi")],
    )
    chunks = await _collect_stream(adapter, request)

    text_chunks = [c for c in chunks if "content" in c]
    assert len(text_chunks) == 2
    assert text_chunks[0] == {"content": "Hello"}
    assert text_chunks[1] == {"content": " world"}


# ---------------------------------------------------------------------------
# Task 14: Reasoning streaming with signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_reasoning_with_signature():
    """REASONING_DELTA -> {thinking: delta}, REASONING_END -> {reasoning_signature: sig}."""
    adapter = _make_streaming_adapter(
        StreamEvent(type=StreamEventType.REASONING_START),
        StreamEvent(type=StreamEventType.REASONING_DELTA, reasoning_delta="Step 1: "),
        StreamEvent(
            type=StreamEventType.REASONING_DELTA, reasoning_delta="analyze input"
        ),
        StreamEvent(
            type=StreamEventType.REASONING_END,
            raw={"signature": "sig_roundtrip_xyz"},
        ),
        StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Result"),
        StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(
                input_tokens=20,
                output_tokens=10,
                total_tokens=30,
            ),
        ),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Think")],
    )
    chunks = await _collect_stream(adapter, request)

    thinking_chunks = [c for c in chunks if "thinking" in c]
    assert len(thinking_chunks) == 2
    assert thinking_chunks[0] == {"thinking": "Step 1: "}
    assert thinking_chunks[1] == {"thinking": "analyze input"}

    sig_chunks = [c for c in chunks if "reasoning_signature" in c]
    assert len(sig_chunks) == 1
    assert sig_chunks[0] == {"reasoning_signature": "sig_roundtrip_xyz"}


# ---------------------------------------------------------------------------
# Task 15: Streaming tool-call buffering
# ---------------------------------------------------------------------------
from unified_llm.types import ToolCall as ULMToolCall


@pytest.mark.asyncio
async def test_stream_tool_call_buffering():
    """TOOL_CALL_START/DELTA/END -> single {tool_calls: [...]} chunk."""
    adapter = _make_streaming_adapter(
        StreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call=ULMToolCall(id="tc_1", name="read_file", arguments={}),
        ),
        StreamEvent(
            type=StreamEventType.TOOL_CALL_DELTA,
            delta='{"path": "/tmp/test.py"}',
        ),
        StreamEvent(
            type=StreamEventType.TOOL_CALL_END,
            tool_call=ULMToolCall(
                id="tc_1",
                name="read_file",
                arguments={"path": "/tmp/test.py"},
            ),
        ),
        StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Read file")],
    )
    chunks = await _collect_stream(adapter, request)

    tc_chunks = [c for c in chunks if "tool_calls" in c]
    assert len(tc_chunks) == 1
    assert tc_chunks[0]["tool_calls"] == [
        {"id": "tc_1", "name": "read_file", "arguments": {"path": "/tmp/test.py"}}
    ]


@pytest.mark.asyncio
async def test_stream_multiple_tool_calls():
    """Multiple sequential tool calls each yield a separate chunk."""
    adapter = _make_streaming_adapter(
        # First tool call
        StreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call=ULMToolCall(id="tc_1", name="read_file", arguments={}),
        ),
        StreamEvent(
            type=StreamEventType.TOOL_CALL_END,
            tool_call=ULMToolCall(
                id="tc_1",
                name="read_file",
                arguments={"path": "a.py"},
            ),
        ),
        # Second tool call
        StreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call=ULMToolCall(id="tc_2", name="write_file", arguments={}),
        ),
        StreamEvent(
            type=StreamEventType.TOOL_CALL_END,
            tool_call=ULMToolCall(
                id="tc_2",
                name="write_file",
                arguments={"path": "b.py", "content": "hello"},
            ),
        ),
        StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Do both")],
    )
    chunks = await _collect_stream(adapter, request)

    tc_chunks = [c for c in chunks if "tool_calls" in c]
    assert len(tc_chunks) == 2
    assert tc_chunks[0]["tool_calls"][0]["name"] == "read_file"
    assert tc_chunks[1]["tool_calls"][0]["name"] == "write_file"


# ---------------------------------------------------------------------------
# Task 16: Streaming usage and finish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_finish_usage():
    """FINISH event yields {usage: {...}} chunk with all token fields."""
    adapter = _make_streaming_adapter(
        StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Hi"),
        StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                reasoning_tokens=20,
            ),
        ),
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hello")],
    )
    chunks = await _collect_stream(adapter, request)

    usage_chunks = [c for c in chunks if "usage" in c]
    assert len(usage_chunks) == 1
    usage = usage_chunks[0]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 150
    assert usage["reasoning_tokens"] == 20


# ---------------------------------------------------------------------------
# Task 16.5: Stream validation smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_invalid_events_raise_stream_error():
    """Invalid stream events raise a protocol error via client validation."""

    class _InvalidAdapter:
        async def stream(self, request):
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="oops")
            yield StreamEvent(type=StreamEventType.FINISH)

    client = Client(providers={"mock": _InvalidAdapter()}, default_provider="mock")
    adapter = UnifiedProviderAdapter(
        provider_name="mock",
        model="mock-model",
        client=client,
    )
    request = ChatRequest(messages=[CoreMessage(role="user", content="Hi")])

    with pytest.raises(CoreStreamError) as exc_info:
        async for _chunk in adapter.stream(request):
            pass

    assert isinstance(exc_info.value.__cause__, StreamProtocolError)


# ---------------------------------------------------------------------------
# Task 17: Verify stream() is async generator + streaming error mapping
# ---------------------------------------------------------------------------
import inspect


def test_stream_is_async_generator_function():
    """adapter.stream must pass inspect.isasyncgenfunction() check.

    AgentSession._detect_streaming_support() uses this at construction
    time to decide whether to use streaming.
    """
    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=MagicMock(),
    )
    assert inspect.isasyncgenfunction(adapter.stream), (
        "adapter.stream must be an async generator function "
        "(defined with 'async def' + 'yield') for AgentSession streaming detection"
    )


@pytest.mark.asyncio
async def test_stream_maps_sdk_error_to_llm_error():
    """SDKError during streaming is caught and re-raised as LLMError."""

    async def failing_stream(request):
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="partial")
        raise ulm_errors.ServerError(
            message="stream failed", provider="anthropic", status_code=500
        )

    mock_client = MagicMock()
    mock_client.stream = failing_stream

    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )
    request = ChatRequest(
        messages=[CoreMessage(role="user", content="Hello")],
    )

    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for _chunk in adapter.stream(request):
            pass  # Consume until error

    assert exc_info.value.retryable is True
    assert exc_info.value.__cause__ is not None


# ---------------------------------------------------------------------------
# Task 18: Client construction inside adapter
# ---------------------------------------------------------------------------
from unittest.mock import patch


@pytest.mark.asyncio
async def test_adapter_builds_client_from_env():
    """When no client is injected, adapter builds one via Client.from_env()."""
    mock_client_instance = MagicMock()
    mock_client_instance.complete = AsyncMock(
        return_value=ULMResponse(
            id="resp_env",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            message=ULMMessage(
                role=Role.ASSISTANT,
                content=[ContentPart(kind=ContentKind.TEXT, text="from env")],
            ),
            finish_reason=FinishReason(reason="stop"),
            usage=ULMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
        ),
    )

    mock_constructed_client = MagicMock()
    mock_constructed_client.providers = {"anthropic": MagicMock()}
    mock_constructed_client.complete = mock_client_instance.complete

    with patch(
        "amplifier_module_loop_agent.unified_provider_adapter.Client"
    ) as MockClient:
        MockClient.from_env.return_value = mock_constructed_client

        # No client= passed — should build from env
        adapter = UnifiedProviderAdapter(
            provider_name="anthropic",
            model="claude-sonnet-4-20250514",
        )

        MockClient.from_env.assert_called_once()
        assert adapter._client is not None


# ---------------------------------------------------------------------------
# Task 19: Injection in AgentOrchestrator
# ---------------------------------------------------------------------------
from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_core.message_models import ChatResponse
from amplifier_core.models import ToolResult


@pytest.mark.asyncio
async def test_orchestrator_injects_adapter_when_use_unified_llm():
    """AgentOrchestrator wraps provider with UnifiedProviderAdapter when use_unified_llm=True."""

    # The adapter's stream() is an async generator, so AgentSession will
    # detect streaming support and use the streaming path.  We must mock
    # the client's stream method as an async generator.
    async def fake_stream(request):
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Adapter response")
        yield StreamEvent(
            type=StreamEventType.FINISH,
            usage=ULMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    mock_client = MagicMock()
    mock_client.stream = fake_stream

    with patch(
        "amplifier_module_loop_agent.unified_provider_adapter.Client"
    ) as MockClient:
        MockClient.from_env.return_value = mock_client

        config = {"use_unified_llm": True, "model": "claude-sonnet-4-20250514"}
        coordinator = MagicMock()
        orchestrator = AgentOrchestrator(coordinator=coordinator, config=config)

        # Native provider — should NOT be called when adapter is active
        native_provider = AsyncMock()
        providers = {"anthropic": native_provider}

        hooks = MagicMock()

        async def _emit(event, data):
            return MagicMock(action="continue")

        hooks.emit = AsyncMock(side_effect=_emit)

        tools = {}
        context = MagicMock()

        result = await orchestrator.execute(
            "Hello", context, providers, tools, hooks, coordinator
        )

        assert result == "Adapter response"
        # Native provider should NOT have been called
        native_provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_orchestrator_skips_adapter_when_not_configured():
    """Without use_unified_llm config, orchestrator uses native provider directly."""
    text_response = ChatResponse(
        content=[TextBlock(text="Native response")],
        tool_calls=None,
        usage=CoreUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    native_provider = MagicMock()
    native_provider.complete = AsyncMock(return_value=text_response)

    config = {"model": "claude-sonnet-4-20250514"}  # No use_unified_llm
    coordinator = MagicMock()
    orchestrator = AgentOrchestrator(coordinator=coordinator, config=config)

    providers = {"anthropic": native_provider}

    hooks = MagicMock()

    async def _emit(event, data):
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)

    tools = {}
    context = MagicMock()

    result = await orchestrator.execute(
        "Hello", context, providers, tools, hooks, coordinator
    )

    assert result == "Native response"
    native_provider.complete.assert_called_once()


# ---------------------------------------------------------------------------
# Task 20: End-to-end integration test with adapter + AgentSession
# ---------------------------------------------------------------------------
from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


@pytest.mark.asyncio
async def test_end_to_end_adapter_with_agent_session():
    """Full integration: adapter + AgentSession completes a multi-turn conversation.

    Flow: user prompt -> adapter.stream() -> tool call -> tool execute ->
    adapter.stream() again -> text response.

    Since the adapter's stream() is an async generator function,
    AgentSession._detect_streaming_support() returns True and the session
    uses the streaming path throughout.
    """
    # Track stream call count so we can return different sequences
    call_count = 0

    async def multi_turn_stream(request):
        """Fake stream that returns tool call on first call, text on second."""
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Response 1: text + tool call
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA, delta="Let me read that file"
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_START,
                tool_call=ULMToolCall(id="tc_1", name="read_file", arguments={}),
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_END,
                tool_call=ULMToolCall(
                    id="tc_1",
                    name="read_file",
                    arguments={"path": "/tmp/test.py"},
                ),
            )
            yield StreamEvent(
                type=StreamEventType.FINISH,
                usage=ULMUsage(input_tokens=20, output_tokens=15, total_tokens=35),
            )
        else:
            # Response 2: text completion
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                delta="The file contains hello world.",
            )
            yield StreamEvent(
                type=StreamEventType.FINISH,
                usage=ULMUsage(input_tokens=30, output_tokens=10, total_tokens=40),
            )

    mock_client = MagicMock()
    mock_client.stream = multi_turn_stream

    adapter = UnifiedProviderAdapter(
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
        client=mock_client,
    )

    # Build a mock tool
    mock_tool = MagicMock()
    mock_tool.name = "read_file"
    mock_tool.description = "Read a file"
    mock_tool.input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    mock_tool.execute = AsyncMock(
        return_value=ToolResult(success=True, output="hello world")
    )

    # Build hooks
    hooks = MagicMock()

    async def _emit(event, data):
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)

    # Create AgentSession with the ADAPTER as provider
    config = SessionConfig()
    session = AgentSession(
        config=config,
        provider=adapter,  # <-- The adapter!
        tools={"read_file": mock_tool},
        hooks=hooks,
        provider_name="anthropic",
        model="claude-sonnet-4-20250514",
    )

    result = await session.process_input("Read /tmp/test.py for me")

    # Verify full flow worked
    assert result == "The file contains hello world."
    assert call_count == 2
    mock_tool.execute.assert_called_once_with({"path": "/tmp/test.py"})
