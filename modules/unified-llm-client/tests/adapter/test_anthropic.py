"""Tests for unified_llm.adapters.anthropic — Anthropic Messages API adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

import unified_llm.errors as E
from unified_llm.adapters.anthropic import AnthropicAdapter
from unified_llm.types import (
    ContentKind,
    ContentPart,
    ImageData,
    Message,
    Request,
    Role,
    ThinkingData,
    Tool,
    ToolCallData,
    ToolChoice,
)


def _make_adapter() -> AnthropicAdapter:
    """Create adapter with mocked AsyncAnthropic client."""
    with patch("unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"):
        return AnthropicAdapter(api_key="test-key")


# ---------------------------------------------------------------------------
# Task 22: Anthropic Request Translation
# ---------------------------------------------------------------------------


class TestRequestTranslation:
    """Task 22: Verify unified Request → Anthropic Messages API format."""

    def test_system_message_extracted(self) -> None:
        """System messages go to 'system' parameter, not in messages."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("You are helpful"),
                Message.user("Hello"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assert len(kwargs["system"]) == 1
        assert kwargs["system"][0]["type"] == "text"
        assert kwargs["system"][0]["text"] == "You are helpful"
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0]["role"] == "user"

    def test_developer_role_merged_with_system(self) -> None:
        """DEVELOPER role messages merge into system parameter."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("System instructions"),
                Message(
                    role=Role.DEVELOPER,
                    content=[
                        ContentPart(kind=ContentKind.TEXT, text="Dev instructions")
                    ],
                ),
                Message.user("Hello"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assert len(kwargs["system"]) == 2
        assert kwargs["system"][0]["text"] == "System instructions"
        assert kwargs["system"][1]["text"] == "Dev instructions"

    def test_max_tokens_defaults_to_4096(self) -> None:
        """max_tokens defaults to 4096 when not specified (Anthropic requires it)."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["max_tokens"] == 4096

    def test_max_tokens_from_request(self) -> None:
        """max_tokens uses request value when specified."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            max_tokens=8192,
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["max_tokens"] == 8192

    def test_user_message_translation(self) -> None:
        """User messages translate to Anthropic user role with text blocks."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hello world")],
        )
        kwargs = adapter._translate_request(request)
        msg = kwargs["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"] == [{"type": "text", "text": "Hello world"}]

    def test_assistant_message_translation(self) -> None:
        """Assistant messages translate to Anthropic assistant role."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.user("Hi"),
                Message.assistant("Hello!"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["messages"][1]["role"] == "assistant"
        assert kwargs["messages"][1]["content"] == [{"type": "text", "text": "Hello!"}]

    def test_tool_result_in_user_message(self) -> None:
        """TOOL role messages become user-role with tool_result blocks."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.user("What's the weather?"),
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.TOOL_CALL,
                            tool_call=ToolCallData(
                                id="call_1", name="weather", arguments={"city": "SF"}
                            ),
                        )
                    ],
                ),
                Message.tool_result(tool_call_id="call_1", content="72F sunny"),
            ],
        )
        kwargs = adapter._translate_request(request)
        tool_msg = kwargs["messages"][2]
        assert tool_msg["role"] == "user"
        assert tool_msg["content"][0]["type"] == "tool_result"
        assert tool_msg["content"][0]["tool_use_id"] == "call_1"
        assert tool_msg["content"][0]["content"] == "72F sunny"

    def test_consecutive_same_role_merged(self) -> None:
        """Consecutive same-role messages are merged (Anthropic requires alternation)."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.user("First question"),
                Message.user("Second question"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assert len(kwargs["messages"]) == 1
        assert len(kwargs["messages"][0]["content"]) == 2

    def test_tool_definitions_translated(self) -> None:
        """Tool definitions → Anthropic's {name, description, input_schema} format."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[
                Tool(
                    name="get_weather",
                    description="Get the weather",
                    parameters={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                ),
            ],
        )
        kwargs = adapter._translate_request(request)
        assert len(kwargs["tools"]) == 1
        tool = kwargs["tools"][0]
        assert tool["name"] == "get_weather"
        assert tool["description"] == "Get the weather"
        assert tool["input_schema"]["type"] == "object"

    def test_tool_choice_auto(self) -> None:
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[Tool(name="t", description="d", parameters={})],
            tool_choice=ToolChoice(mode="auto"),
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["tool_choice"] == {"type": "auto"}

    def test_tool_choice_none_omits_tools(self) -> None:
        """Anthropic: tool_choice 'none' means omit tools entirely."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[Tool(name="t", description="d", parameters={})],
            tool_choice=ToolChoice(mode="none"),
        )
        kwargs = adapter._translate_request(request)
        assert "tools" not in kwargs

    def test_tool_choice_required(self) -> None:
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[Tool(name="t", description="d", parameters={})],
            tool_choice=ToolChoice(mode="required"),
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["tool_choice"] == {"type": "any"}

    def test_tool_choice_named(self) -> None:
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[Tool(name="get_weather", description="d", parameters={})],
            tool_choice=ToolChoice(mode="named", tool_name="get_weather"),
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["tool_choice"] == {"type": "tool", "name": "get_weather"}

    def test_generation_params_passed(self) -> None:
        """temperature/top_p travel in extra_body; stop_sequences stays typed.

        anthropic 1.0.0 dropped temperature/top_p from the typed Messages
        surface, so the adapter relocates them into ``extra_body`` (the API
        still honours them there).  stop_sequences is still typed.
        """
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            temperature=0.7,
            top_p=0.9,
            stop_sequences=["END"],
        )
        kwargs = adapter._translate_request(request)
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs
        assert kwargs["extra_body"]["temperature"] == 0.7
        assert kwargs["extra_body"]["top_p"] == 0.9
        assert kwargs["stop_sequences"] == ["END"]

    def test_provider_options_passthrough(self) -> None:
        """provider_options['anthropic'] passes through extra parameters."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            provider_options={
                "anthropic": {
                    "extra_headers": {"anthropic-beta": "some-feature"},
                    "metadata": {"user_id": "user-123"},
                }
            },
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["extra_headers"] == {"anthropic-beta": "some-feature"}
        assert kwargs["metadata"] == {"user_id": "user-123"}

    # ------------------------------------------------------------------
    # Structured output (Spec §4.5 / capability matrix :989)
    # ------------------------------------------------------------------

    def test_json_schema_response_format_injects_extraction_tool(self) -> None:
        """json_schema response_format injects __structured_output__ tool.

        Asserts the outgoing request carries a synthetic extraction tool whose
        input_schema matches the caller's JSON schema, enabling tool-based
        structured output extraction (Anthropic has no native json_schema mode).
        """
        from unified_llm.types import ResponseFormat

        # Convention: the extraction tool name is "__structured_output__".
        # Must match STRUCTURED_OUTPUT_TOOL_NAME in adapters/anthropic.py
        # and _ANTHROPIC_STRUCTURED_OUTPUT_TOOL in generate.py.
        _TOOL_NAME = "__structured_output__"

        adapter = _make_adapter()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name", "age"],
        }
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Extract info")],
            response_format=ResponseFormat(
                type="json_schema",
                json_schema=schema,
                strict=True,
            ),
        )
        kwargs = adapter._translate_request(request)

        # A tools list must be present containing the extraction tool
        tools = kwargs.get("tools", [])
        extraction = next(
            (t for t in tools if t.get("name") == _TOOL_NAME),
            None,
        )
        assert extraction is not None, (
            f"Expected '{_TOOL_NAME}' tool to be injected, "
            f"got tools: {[t.get('name') for t in tools]}"
        )
        # The tool's input_schema must BE the caller's schema
        assert extraction["input_schema"] == schema

    def test_json_schema_response_format_forces_tool_choice(self) -> None:
        """json_schema response_format forces tool_choice to the extraction tool.

        Anthropic will only call the extraction tool if tool_choice is explicitly
        set to that tool name — otherwise it may choose to generate text.
        """
        from unified_llm.types import ResponseFormat

        _TOOL_NAME = "__structured_output__"

        adapter = _make_adapter()
        schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        }
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            response_format=ResponseFormat(
                type="json_schema",
                json_schema=schema,
            ),
        )
        kwargs = adapter._translate_request(request)

        tc = kwargs.get("tool_choice", {})
        assert tc.get("type") == "tool", (
            "tool_choice type must be 'tool' to force a named call"
        )
        assert tc.get("name") == _TOOL_NAME

    def test_json_response_format_without_schema_raises(self) -> None:
        """Plain json response_format (no schema) raises ConfigurationError on Anthropic.

        Anthropic cannot guarantee structured output without a schema, so we
        fail loud rather than silently degrade (Spec: no silent fallback).
        """
        import pytest

        from unified_llm.errors import ConfigurationError
        from unified_llm.types import ResponseFormat

        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            response_format=ResponseFormat(type="json"),
        )
        with pytest.raises(ConfigurationError):
            adapter._translate_request(request)

    def test_thinking_blocks_preserved_in_assistant(self) -> None:
        """Thinking blocks in assistant messages round-trip with signatures."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.user("Solve this"),
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.THINKING,
                            thinking=ThinkingData(
                                text="Let me think...", signature="sig_abc"
                            ),
                        ),
                        ContentPart(kind=ContentKind.TEXT, text="The answer is 42"),
                    ],
                ),
                Message.user("Why?"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assistant_msg = kwargs["messages"][1]
        assert assistant_msg["content"][0]["type"] == "thinking"
        assert assistant_msg["content"][0]["thinking"] == "Let me think..."
        assert assistant_msg["content"][0]["signature"] == "sig_abc"

    def test_redacted_thinking_blocks_passed_through(self) -> None:
        """Redacted thinking blocks pass through verbatim."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.user("Solve this"),
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.REDACTED_THINKING,
                            thinking=ThinkingData(text="<redacted>", redacted=True),
                        ),
                        ContentPart(kind=ContentKind.TEXT, text="Answer"),
                    ],
                ),
                Message.user("Why?"),
            ],
        )
        kwargs = adapter._translate_request(request)
        assistant_msg = kwargs["messages"][1]
        assert assistant_msg["content"][0]["type"] == "redacted_thinking"
        assert assistant_msg["content"][0]["data"] == "<redacted>"

    def test_image_url_translation(self) -> None:
        """IMAGE with URL translates to Anthropic image source."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(kind=ContentKind.TEXT, text="What's this?"),
                        ContentPart(
                            kind=ContentKind.IMAGE,
                            image=ImageData(url="https://example.com/img.png"),
                        ),
                    ],
                ),
            ],
        )
        kwargs = adapter._translate_request(request)
        content = kwargs["messages"][0]["content"]
        assert content[1]["type"] == "image"
        assert content[1]["source"]["type"] == "url"
        assert content[1]["source"]["url"] == "https://example.com/img.png"

    def test_image_base64_translation(self) -> None:
        """IMAGE with data translates to base64 source."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(
                            kind=ContentKind.IMAGE,
                            image=ImageData(data=b"\x89PNG", media_type="image/png"),
                        ),
                    ],
                ),
            ],
        )
        kwargs = adapter._translate_request(request)
        content = kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/png"

    def test_name_property(self) -> None:
        """Adapter name is 'anthropic'."""
        adapter = _make_adapter()
        assert adapter.name == "anthropic"


# ---------------------------------------------------------------------------
# Task 23: Anthropic Response Translation
# ---------------------------------------------------------------------------


def _mock_anthropic_response(
    *,
    id: str = "msg_test123",
    model: str = "claude-sonnet-4-20250514",
    content: list[SimpleNamespace] | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 20,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
) -> SimpleNamespace:
    """Create a mock Anthropic Message response object."""
    usage_kwargs = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cache_read_input_tokens is not None:
        usage_kwargs["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens is not None:
        usage_kwargs["cache_creation_input_tokens"] = cache_creation_input_tokens

    return SimpleNamespace(
        id=id,
        model=model,
        type="message",
        role="assistant",
        content=content or [SimpleNamespace(type="text", text="Hello!")],
        stop_reason=stop_reason,
        usage=SimpleNamespace(**usage_kwargs),
    )


def _mock_raw_http(
    parsed: SimpleNamespace,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Mock the object ``messages.with_raw_response.create`` resolves to.

    On anthropic 1.0.0 that is an ``AsyncAPIResponse``, whose ``.parse()`` is a
    **coroutine function** — so the mock uses ``AsyncMock``.  Stubbing it with a
    plain sync ``MagicMock`` (as these tests used to) models a surface the SDK
    no longer has and hides a missing ``await`` in the adapter.
    """
    raw_http = MagicMock()
    raw_http.parse = AsyncMock(return_value=parsed)
    raw_http.headers = headers if headers is not None else {}
    return raw_http


class TestResponseTranslation:
    """Task 23: Verify Anthropic response → unified Response."""

    def test_text_content_block(self) -> None:
        """Text content blocks → TEXT ContentParts."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            content=[SimpleNamespace(type="text", text="Hello world")],
        )
        response = adapter._translate_response(raw)
        assert len(response.message.content) == 1
        part = response.message.content[0]
        assert part.kind == ContentKind.TEXT
        assert part.text == "Hello world"

    def test_tool_use_content_block(self) -> None:
        """tool_use blocks → TOOL_CALL ContentParts."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_123",
                    name="get_weather",
                    input={"city": "SF"},
                ),
            ],
            stop_reason="tool_use",
        )
        response = adapter._translate_response(raw)
        part = response.message.content[0]
        assert part.kind == ContentKind.TOOL_CALL
        assert part.tool_call is not None
        assert part.tool_call.id == "toolu_123"
        assert part.tool_call.name == "get_weather"
        assert part.tool_call.arguments == {"city": "SF"}

    def test_thinking_content_block(self) -> None:
        """thinking blocks → THINKING ContentParts with signature preserved."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            content=[
                SimpleNamespace(
                    type="thinking",
                    thinking="Let me reason step by step...",
                    signature="sig_abc123",
                ),
                SimpleNamespace(type="text", text="The answer is 42."),
            ],
        )
        response = adapter._translate_response(raw)
        thinking_part = response.message.content[0]
        assert thinking_part.kind == ContentKind.THINKING
        assert thinking_part.thinking is not None
        assert thinking_part.thinking.text == "Let me reason step by step..."
        assert thinking_part.thinking.signature == "sig_abc123"

    def test_redacted_thinking_content_block(self) -> None:
        """redacted_thinking blocks → REDACTED_THINKING ContentParts."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            content=[
                SimpleNamespace(type="redacted_thinking", data="<redacted_data>"),
                SimpleNamespace(type="text", text="Answer"),
            ],
        )
        response = adapter._translate_response(raw)
        redacted_part = response.message.content[0]
        assert redacted_part.kind == ContentKind.REDACTED_THINKING
        assert redacted_part.thinking is not None
        assert redacted_part.thinking.text == "<redacted_data>"
        assert redacted_part.thinking.redacted is True

    def test_finish_reason_end_turn(self) -> None:
        """end_turn → FinishReason(reason='stop')."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(stop_reason="end_turn")
        response = adapter._translate_response(raw)
        assert response.finish_reason.reason == "stop"
        assert response.finish_reason.raw == "end_turn"

    def test_finish_reason_stop_sequence(self) -> None:
        """stop_sequence → FinishReason(reason='stop')."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(stop_reason="stop_sequence")
        response = adapter._translate_response(raw)
        assert response.finish_reason.reason == "stop"

    def test_finish_reason_max_tokens(self) -> None:
        """max_tokens → FinishReason(reason='length')."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(stop_reason="max_tokens")
        response = adapter._translate_response(raw)
        assert response.finish_reason.reason == "length"

    def test_finish_reason_tool_use(self) -> None:
        """tool_use → FinishReason(reason='tool_calls')."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(stop_reason="tool_use")
        response = adapter._translate_response(raw)
        assert response.finish_reason.reason == "tool_calls"

    def test_usage_extraction(self) -> None:
        """Usage fields mapped correctly."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(input_tokens=100, output_tokens=50)
        response = adapter._translate_response(raw)
        assert response.usage.input_tokens == 100
        assert response.usage.output_tokens == 50
        assert response.usage.total_tokens == 150

    def test_usage_cache_tokens(self) -> None:
        """Cache read/write tokens extracted when present."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=20,
        )
        response = adapter._translate_response(raw)
        assert response.usage.cache_read_tokens == 80
        assert response.usage.cache_write_tokens == 20

    def test_response_metadata(self) -> None:
        """Response id, model, provider populated correctly."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(id="msg_abc", model="claude-sonnet-4-20250514")
        response = adapter._translate_response(raw)
        assert response.id == "msg_abc"
        assert response.model == "claude-sonnet-4-20250514"
        assert response.provider == "anthropic"

    def test_mixed_content_blocks(self) -> None:
        """Multiple content block types in a single response."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            content=[
                SimpleNamespace(type="thinking", thinking="Hmm...", signature="sig_1"),
                SimpleNamespace(type="text", text="Here's my answer"),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="search",
                    input={"q": "test"},
                ),
            ],
            stop_reason="tool_use",
        )
        response = adapter._translate_response(raw)
        assert len(response.message.content) == 3
        assert response.message.content[0].kind == ContentKind.THINKING
        assert response.message.content[1].kind == ContentKind.TEXT
        assert response.message.content[2].kind == ContentKind.TOOL_CALL


# ---------------------------------------------------------------------------
# Task 24: Anthropic Error Translation
# ---------------------------------------------------------------------------


def _make_api_status_error(
    status_code: int,
    message: str = "error",
    *,
    body: dict | None = None,
    retry_after: str | None = None,
) -> anthropic.APIStatusError:
    """Create a mock Anthropic APIStatusError."""
    import httpx

    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code=status_code,
        headers=headers,
        json=body
        or {"type": "error", "error": {"type": "api_error", "message": message}},
        request=request,
    )
    return anthropic.APIStatusError(
        message=message,
        response=response,
        body=body
        or {"type": "error", "error": {"type": "api_error", "message": message}},
    )


class TestErrorTranslation:
    """Task 24: Verify Anthropic SDK exceptions → unified error hierarchy."""

    def test_authentication_error(self) -> None:
        """anthropic.AuthenticationError → errors.AuthenticationError."""
        adapter = _make_adapter()
        exc = _make_api_status_error(401, "Invalid API key")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.AuthenticationError)
        assert result.retryable is False

    def test_permission_denied_error(self) -> None:
        """403 → errors.AccessDeniedError."""
        adapter = _make_adapter()
        exc = _make_api_status_error(403, "Forbidden")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.AccessDeniedError)
        assert result.retryable is False

    def test_not_found_error(self) -> None:
        """404 → errors.NotFoundError."""
        adapter = _make_adapter()
        exc = _make_api_status_error(404, "Model not found")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.NotFoundError)

    def test_bad_request_error(self) -> None:
        """400 → errors.InvalidRequestError."""
        adapter = _make_adapter()
        exc = _make_api_status_error(400, "Bad request")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.InvalidRequestError)
        assert result.retryable is False

    def test_rate_limit_error(self) -> None:
        """429 → errors.RateLimitError with retryable=True."""
        adapter = _make_adapter()
        exc = _make_api_status_error(429, "Rate limited")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.RateLimitError)
        assert result.retryable is True

    def test_rate_limit_with_retry_after(self) -> None:
        """429 with Retry-After header → retry_after parsed."""
        adapter = _make_adapter()
        exc = _make_api_status_error(429, "Rate limited", retry_after="30")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.RateLimitError)
        assert isinstance(result, E.ProviderError)
        assert result.retry_after == 30.0

    def test_server_error(self) -> None:
        """500 → errors.ServerError with retryable=True."""
        adapter = _make_adapter()
        exc = _make_api_status_error(500, "Internal server error")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.ServerError)
        assert result.retryable is True

    def test_overloaded_529(self) -> None:
        """529 (overloaded) → retryable ProviderError."""
        adapter = _make_adapter()
        exc = _make_api_status_error(529, "Overloaded")
        result = adapter._translate_error(exc)
        assert isinstance(result, E.ProviderError)
        assert result.retryable is True

    def test_connection_error(self) -> None:
        """anthropic.APIConnectionError → errors.NetworkError."""
        adapter = _make_adapter()
        exc = anthropic.APIConnectionError(request=SimpleNamespace(url="test"))
        result = adapter._translate_error(exc)
        assert isinstance(result, E.NetworkError)
        assert result.retryable is True

    def test_timeout_error(self) -> None:
        """anthropic.APITimeoutError → errors.RequestTimeoutError."""
        adapter = _make_adapter()
        exc = anthropic.APITimeoutError(request=SimpleNamespace(url="test"))
        result = adapter._translate_error(exc)
        assert isinstance(result, E.RequestTimeoutError)
        assert result.retryable is True

    def test_error_preserves_cause(self) -> None:
        """Translated errors preserve the original exception as cause."""
        adapter = _make_adapter()
        exc = _make_api_status_error(500, "Server error")
        result = adapter._translate_error(exc)
        assert result.cause is exc


# ---------------------------------------------------------------------------
# Task 25: Anthropic complete() Integration
# ---------------------------------------------------------------------------


class TestCompleteIntegration:
    """Task 25: Wire up request/response/error into complete()."""

    def test_complete_round_trip(self) -> None:
        """Full round-trip: unified Request → SDK call → unified Response."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = _mock_raw_http(
                _mock_anthropic_response(
                    content=[SimpleNamespace(type="text", text="Hello from Claude!")],
                )
            )
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("Hi")],
            )
            response = asyncio.run(adapter.complete(request))

            assert response.text == "Hello from Claude!"
            assert response.provider == "anthropic"
            assert response.finish_reason.reason == "stop"
            mock_client.messages.with_raw_response.create.assert_called_once()

    def test_complete_passes_translated_kwargs(self) -> None:
        """complete() passes correctly translated kwargs to SDK."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = _mock_raw_http(_mock_anthropic_response())
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[
                    Message.system("Be helpful"),
                    Message.user("Hello"),
                ],
                temperature=0.5,
                max_tokens=1024,
            )
            asyncio.run(adapter.complete(request))

            call_kwargs = mock_client.messages.with_raw_response.create.call_args[1]
            assert call_kwargs["model"] == "claude-sonnet-4-20250514"
            assert call_kwargs["max_tokens"] == 1024
            # anthropic 1.0.0 dropped temperature from the typed surface — it
            # rides in extra_body now, never as a top-level kwarg.
            assert "temperature" not in call_kwargs
            assert call_kwargs["extra_body"]["temperature"] == 0.5
            assert "system" in call_kwargs

    def test_complete_translates_api_errors(self) -> None:
        """complete() catches SDK exceptions and raises unified errors."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            mock_client.messages.with_raw_response.create = AsyncMock(
                side_effect=_make_api_status_error(429, "Rate limited")
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("Hi")],
            )
            with pytest.raises(E.RateLimitError):
                asyncio.run(adapter.complete(request))

    def test_complete_translates_connection_errors(self) -> None:
        """complete() catches connection errors and raises NetworkError."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            mock_client.messages.with_raw_response.create = AsyncMock(
                side_effect=anthropic.APIConnectionError(
                    request=SimpleNamespace(url="test")
                )
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("Hi")],
            )
            with pytest.raises(E.NetworkError):
                asyncio.run(adapter.complete(request))

    def test_complete_with_tool_response(self) -> None:
        """complete() handles tool_use responses correctly."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = _mock_raw_http(
                _mock_anthropic_response(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            id="toolu_1",
                            name="get_weather",
                            input={"city": "SF"},
                        ),
                    ],
                    stop_reason="tool_use",
                )
            )
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("What's the weather?")],
                tools=[
                    Tool(name="get_weather", description="Get weather", parameters={})
                ],
            )
            response = asyncio.run(adapter.complete(request))

            assert response.finish_reason.reason == "tool_calls"
            assert len(response.tool_calls) == 1
            assert response.tool_calls[0].name == "get_weather"


# ---------------------------------------------------------------------------
# Task 26: Anthropic Streaming Translation
# ---------------------------------------------------------------------------


def _make_stream_events_text() -> list[SimpleNamespace]:
    """Mock Anthropic raw stream events for a simple text response."""
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="msg_stream1",
                model="claude-sonnet-4-20250514",
                usage=SimpleNamespace(input_tokens=10),
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hello"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text=" world"),
        ),
        SimpleNamespace(
            type="content_block_stop",
            index=0,
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=5),
        ),
        SimpleNamespace(type="message_stop"),
    ]


def _make_stream_events_tool_use() -> list[SimpleNamespace]:
    """Mock stream events for a tool_use response."""
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="msg_tool1",
                model="claude-sonnet-4-20250514",
                usage=SimpleNamespace(input_tokens=15),
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use", id="toolu_1", name="get_weather"
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"city"'),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json=': "SF"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=10),
        ),
        SimpleNamespace(type="message_stop"),
    ]


def _make_stream_events_thinking() -> list[SimpleNamespace]:
    """Mock stream events with thinking blocks."""
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="msg_think1",
                model="claude-sonnet-4-20250514",
                usage=SimpleNamespace(input_tokens=20),
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="thinking", thinking=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="Let me think..."),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="text_delta", text="Answer is 42"),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=15),
        ),
        SimpleNamespace(type="message_stop"),
    ]


class _MockAsyncStream:
    """Mock async iterator for Anthropic streaming."""

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


from unified_llm.types import StreamEventType  # noqa: E402


class TestStreamingTranslation:
    """Task 26: Verify Anthropic SSE events → unified StreamEvent sequence."""

    def _collect_stream(
        self, adapter: AnthropicAdapter, request: Request, events: list[SimpleNamespace]
    ) -> list:
        """Run streaming and collect all events."""
        from unified_llm.types import StreamEvent as SE

        mock_create = AsyncMock(return_value=_MockAsyncStream(events))
        with patch.object(adapter._client.messages, "create", mock_create):
            result: list[SE] = []

            async def run():
                async for evt in adapter.stream(request):
                    result.append(evt)

            asyncio.run(run())
        return result

    def test_text_stream_event_sequence(self) -> None:
        """Text stream: STREAM_START → TEXT_START → TEXT_DELTA*2 → TEXT_END → FINISH."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_text())

        types = [e.type for e in events]
        assert StreamEventType.STREAM_START in types
        assert StreamEventType.TEXT_START in types
        assert types.count(StreamEventType.TEXT_DELTA) == 2
        assert StreamEventType.TEXT_END in types
        assert StreamEventType.FINISH in types

    def test_text_deltas_contain_text(self) -> None:
        """TEXT_DELTA events carry the delta text."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_text())

        deltas = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert deltas[0].delta == "Hello"
        assert deltas[1].delta == " world"

    def test_finish_event_has_usage(self) -> None:
        """FINISH event carries usage and finish_reason."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_text())

        finish = [e for e in events if e.type == StreamEventType.FINISH][0]
        assert finish.finish_reason is not None
        assert finish.finish_reason.reason == "stop"
        assert finish.usage is not None
        assert finish.usage.input_tokens == 10
        assert finish.usage.output_tokens == 5

    def test_tool_call_stream_events(self) -> None:
        """Tool use stream: TOOL_CALL_START → TOOL_CALL_DELTA → TOOL_CALL_END."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_tool_use())

        types = [e.type for e in events]
        assert StreamEventType.TOOL_CALL_START in types
        assert StreamEventType.TOOL_CALL_DELTA in types
        assert StreamEventType.TOOL_CALL_END in types

    def test_tool_call_end_has_parsed_args(self) -> None:
        """TOOL_CALL_END carries the complete parsed tool call."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_tool_use())

        end_evt = [e for e in events if e.type == StreamEventType.TOOL_CALL_END][0]
        assert end_evt.tool_call is not None
        assert end_evt.tool_call.id == "toolu_1"
        assert end_evt.tool_call.name == "get_weather"
        assert end_evt.tool_call.arguments == {"city": "SF"}

    def test_tool_use_finish_reason(self) -> None:
        """Tool use stream: FINISH has reason='tool_calls'."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_tool_use())

        finish = [e for e in events if e.type == StreamEventType.FINISH][0]
        assert finish.finish_reason is not None
        assert finish.finish_reason.reason == "tool_calls"

    def test_thinking_stream_events(self) -> None:
        """Thinking stream: REASONING_START → REASONING_DELTA → REASONING_END."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_thinking())

        types = [e.type for e in events]
        assert StreamEventType.REASONING_START in types
        assert StreamEventType.REASONING_DELTA in types
        assert StreamEventType.REASONING_END in types

    def test_thinking_delta_carries_text(self) -> None:
        """REASONING_DELTA events carry the thinking text."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        events = self._collect_stream(adapter, request, _make_stream_events_thinking())

        reasoning = [e for e in events if e.type == StreamEventType.REASONING_DELTA]
        assert len(reasoning) == 1
        assert reasoning[0].reasoning_delta == "Let me think..."

    def test_stream_error_translated(self) -> None:
        """Stream errors are caught and translated to unified errors."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )

        exc = _make_api_status_error(500, "Server error")
        with patch.object(adapter._client.messages, "create", side_effect=exc):
            with pytest.raises(E.ServerError):

                async def run():
                    async for _ in adapter.stream(request):
                        pass

                asyncio.run(run())


# ---------------------------------------------------------------------------
# Task 27: Anthropic Prompt Caching
# ---------------------------------------------------------------------------


class TestPromptCaching:
    """Task 27: Verify cache_control injection and beta header."""

    def test_cache_control_on_last_system_block(self) -> None:
        """cache_control breakpoint injected on last system content block."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("You are a helpful assistant"),
                Message.user("Hello"),
            ],
        )
        kwargs = adapter._translate_request(request)
        # Last system block should have cache_control
        last_system = kwargs["system"][-1]
        assert last_system.get("cache_control") == {"type": "ephemeral"}

    def test_cache_control_on_tool_definitions(self) -> None:
        """cache_control injected on last tool definition."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            tools=[
                Tool(name="tool_a", description="First tool", parameters={}),
                Tool(name="tool_b", description="Second tool", parameters={}),
            ],
        )
        kwargs = adapter._translate_request(request)
        # Last tool should have cache_control
        last_tool = kwargs["tools"][-1]
        assert last_tool.get("cache_control") == {"type": "ephemeral"}
        # First tool should NOT have cache_control
        first_tool = kwargs["tools"][0]
        assert "cache_control" not in first_tool

    def test_beta_header_included(self) -> None:
        """prompt-caching-2024-07-31 beta header included when caching active."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("System prompt"),
                Message.user("Hello"),
            ],
        )
        kwargs = adapter._translate_request(request)
        extra_headers = kwargs.get("extra_headers", {})
        assert "prompt-caching-2024-07-31" in extra_headers.get("anthropic-beta", "")

    def test_auto_cache_disabled(self) -> None:
        """auto_cache=false disables cache_control injection."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("System prompt"),
                Message.user("Hello"),
            ],
            provider_options={"anthropic": {"auto_cache": False}},
        )
        kwargs = adapter._translate_request(request)
        # No cache_control on system blocks
        for block in kwargs.get("system", []):
            assert "cache_control" not in block
        # No beta header
        extra_headers = kwargs.get("extra_headers", {})
        assert "prompt-caching" not in extra_headers.get("anthropic-beta", "")

    def test_cache_control_not_on_empty_system(self) -> None:
        """No cache_control injection when there are no system messages."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hello")],
        )
        kwargs = adapter._translate_request(request)
        assert "system" not in kwargs
        # No beta header either since there's nothing to cache
        assert "extra_headers" not in kwargs

    def test_cache_tokens_in_usage(self) -> None:
        """cache_read_tokens and cache_write_tokens populated in Response."""
        adapter = _make_adapter()
        raw = _mock_anthropic_response(
            cache_read_input_tokens=500,
            cache_creation_input_tokens=100,
        )
        response = adapter._translate_response(raw)
        assert response.usage.cache_read_tokens == 500
        assert response.usage.cache_write_tokens == 100

    def test_beta_header_merged_with_existing(self) -> None:
        """Beta header merges with existing anthropic-beta from provider_options."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("System prompt"),
                Message.user("Hello"),
            ],
            provider_options={
                "anthropic": {
                    "extra_headers": {
                        "anthropic-beta": "max-tokens-3-5-sonnet-2024-07-15"
                    },
                }
            },
        )
        kwargs = adapter._translate_request(request)
        beta = kwargs["extra_headers"]["anthropic-beta"]
        assert "prompt-caching-2024-07-31" in beta
        assert "max-tokens-3-5-sonnet-2024-07-15" in beta


# ---------------------------------------------------------------------------
# anthropic 1.0.0: wire-only param relocation (regression guard)
# ---------------------------------------------------------------------------


class TestWireOnlyParamRelocation:
    """anthropic 1.0.0 removed temperature/top_p/top_k from the typed surface.

    Passing them as top-level kwargs to ``messages.create``/``.stream`` raises
    ``TypeError: got an unexpected keyword argument``.  The adapter must
    relocate them into ``extra_body``, which the SDK forwards verbatim onto
    the wire.  These tests fail RED against the pre-fix adapter.
    """

    def test_installed_sdk_messages_create_rejects_wire_only_params(self) -> None:
        """Real-signature guard: document WHY the relocation is needed.

        Introspects the *installed* SDK rather than trusting a changelog.  If
        a future SDK restores these as typed params this test goes red and the
        relocation can be revisited.
        """
        import inspect

        from anthropic.resources.messages import AsyncMessages

        typed_params = set(inspect.signature(AsyncMessages.create).parameters)
        for removed in ("temperature", "top_p", "top_k"):
            assert removed not in typed_params, (
                f"{removed!r} is a typed param of AsyncMessages.create in "
                f"anthropic {anthropic.__version__} — the extra_body relocation "
                "may no longer be necessary."
            )
        # extra_body IS accepted — that is where the relocated params land.
        assert "extra_body" in typed_params

    def test_complete_never_passes_temperature_or_top_p_as_kwargs(self) -> None:
        """complete(): temperature/top_p reach the SDK via extra_body only."""
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = _mock_raw_http(_mock_anthropic_response())
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("Hi")],
                temperature=0.3,
                top_p=0.8,
            )
            asyncio.run(adapter.complete(request))

            call_kwargs = mock_client.messages.with_raw_response.create.call_args[1]
            assert "temperature" not in call_kwargs, (
                "temperature must not be a top-level kwarg — anthropic 1.0.0 "
                "raises TypeError for it"
            )
            assert "top_p" not in call_kwargs, (
                "top_p must not be a top-level kwarg — anthropic 1.0.0 raises "
                "TypeError for it"
            )
            # ...but the values must still travel on the wire.
            assert call_kwargs["extra_body"]["temperature"] == 0.3
            assert call_kwargs["extra_body"]["top_p"] == 0.8

    def test_stream_never_passes_temperature_or_top_p_as_kwargs(self) -> None:
        """stream(): temperature/top_p reach the SDK via extra_body only."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            temperature=0.3,
            top_p=0.8,
        )
        mock_create = AsyncMock(
            return_value=_MockAsyncStream(_make_stream_events_text())
        )
        with patch.object(adapter._client.messages, "create", mock_create):

            async def run() -> None:
                async for _ in adapter.stream(request):
                    pass

            asyncio.run(run())

        call_kwargs = mock_create.call_args[1]
        assert "temperature" not in call_kwargs
        assert "top_p" not in call_kwargs
        assert call_kwargs["extra_body"]["temperature"] == 0.3
        assert call_kwargs["extra_body"]["top_p"] == 0.8
        assert call_kwargs["stream"] is True

    def test_thinking_temperature_override_relocated(self) -> None:
        """The extended-thinking temperature=1.0 constraint rides extra_body."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            temperature=0.2,
            reasoning_effort="high",
            max_tokens=20000,
        )
        kwargs = adapter._translate_request(request)
        assert "temperature" not in kwargs
        assert kwargs["extra_body"]["temperature"] == 1.0

    def test_top_k_from_provider_options_relocated(self) -> None:
        """top_k (also removed in 1.0.0) is relocated from provider_options."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            provider_options={"anthropic": {"top_k": 40}},
        )
        kwargs = adapter._translate_request(request)
        assert "top_k" not in kwargs
        assert kwargs["extra_body"]["top_k"] == 40

    def test_caller_supplied_extra_body_wins(self) -> None:
        """A key already in extra_body is never clobbered by the relocation."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            temperature=0.7,
            provider_options={"anthropic": {"extra_body": {"temperature": 0.1}}},
        )
        kwargs = adapter._translate_request(request)
        assert "temperature" not in kwargs
        assert kwargs["extra_body"]["temperature"] == 0.1

    def test_no_generation_params_leaves_extra_body_absent(self) -> None:
        """No wire-only params → no spurious empty extra_body is added."""
        adapter = _make_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
        )
        kwargs = adapter._translate_request(request)
        assert "extra_body" not in kwargs


# ---------------------------------------------------------------------------
# anthropic 1.0.0: AsyncAPIResponse.parse is a coroutine (regression guard)
# ---------------------------------------------------------------------------


class TestRawResponseParseIsAwaited:
    """anthropic 1.0.0 removed the legacy *sync* raw-response surface.

    ``with_raw_response.create`` on an async client now resolves to
    ``AsyncAPIResponse``, whose ``.parse()`` is a **coroutine function**.
    Calling it without ``await`` yields a coroutine object, and the very next
    attribute access (``raw.content``, inside ``_translate_response``) raises
    ``AttributeError: 'coroutine' object has no attribute 'content'``.

    The pre-existing mocks were blind to exactly this: they stubbed ``.parse``
    with a *sync* ``MagicMock`` returning a plain object — a surface the 1.0.0
    SDK no longer has — so the un-awaited call looked fine under test and blew
    up against the real API.  The mocks below are ``AsyncMock``, matching the
    installed SDK, and fail RED against the pre-fix adapter.
    """

    def test_installed_sdk_async_raw_response_parse_is_coroutine(self) -> None:
        """Real-signature guard: document WHY the ``await`` is needed.

        Introspects the *installed* SDK rather than trusting a changelog.  If a
        future SDK makes ``parse`` sync again — or restores the legacy sync
        response class that ``with_raw_response`` used to return — this goes
        red and the ``await`` must be revisited.
        """
        import inspect

        from anthropic._response import AsyncAPIResponse

        assert inspect.iscoroutinefunction(AsyncAPIResponse.parse), (
            "AsyncAPIResponse.parse is not a coroutine function in anthropic "
            f"{anthropic.__version__} — the `await` in complete() may no "
            "longer be correct."
        )
        # The legacy sync raw-response surface is gone in 1.0.0.  While it is
        # absent, with_raw_response can only resolve to AsyncAPIResponse.
        assert not hasattr(anthropic._response, "LegacyAPIResponse"), (
            "LegacyAPIResponse is back in anthropic "
            f"{anthropic.__version__} — re-check which response class "
            "with_raw_response.create returns before trusting the `await`."
        )

    def test_complete_awaits_raw_response_parse(self) -> None:
        """★ complete() must ``await`` the coroutine returned by ``.parse()``.

        RED against the pre-fix adapter with:
            AttributeError: 'coroutine' object has no attribute 'content'
        """
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = MagicMock()
            # Matches anthropic 1.0.0: AsyncAPIResponse.parse is a coroutine fn.
            _mock_raw.parse = AsyncMock(
                return_value=_mock_anthropic_response(
                    content=[SimpleNamespace(type="text", text="Awaited!")],
                )
            )
            _mock_raw.headers = {}
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            request = Request(
                model="claude-sonnet-4-20250514",
                messages=[Message.user("Hi")],
            )
            response = asyncio.run(adapter.complete(request))

            # The parsed Message — not a coroutine — reached translation.
            assert response.text == "Awaited!"
            _mock_raw.parse.assert_awaited_once()

    def test_complete_serializes_the_awaited_message_not_a_coroutine(self) -> None:
        """The awaited object must also reach ``_serialize_raw``/``response.raw``.

        ``complete()`` consumes ``raw`` twice — ``_translate_response(raw)`` and
        ``_serialize_raw(raw)``.  Both must see the resolved Message.
        """
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            adapter = AnthropicAdapter(api_key="test-key")
            mock_client = mock_cls.return_value
            _mock_raw = MagicMock()
            _mock_raw.parse = AsyncMock(return_value=_mock_anthropic_response())
            _mock_raw.headers = {}
            mock_client.messages.with_raw_response.create = AsyncMock(
                return_value=_mock_raw
            )

            response = asyncio.run(
                adapter.complete(
                    Request(
                        model="claude-sonnet-4-20250514",
                        messages=[Message.user("Hi")],
                    )
                )
            )

            assert isinstance(response.raw, dict)
            assert response.raw["id"] == "msg_test123"

    def test_adapter_never_calls_parse_without_await(self) -> None:
        """Structural guard: every ``.parse()`` in the adapter is awaited.

        The DTU reported ``stream()`` as fine, but a second un-awaited site
        anywhere in the module would fail the same way and be equally
        invisible to mocked tests.  This walks the module's AST instead of
        trusting a grep, so any future ``.parse()`` added without ``await``
        goes red here.
        """
        import ast
        import inspect

        import unified_llm.adapters.anthropic as anthropic_adapter

        tree = ast.parse(inspect.getsource(anthropic_adapter))
        awaited = {
            id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)
        }
        unawaited = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parse"
            and id(node) not in awaited
        ]
        assert not unawaited, (
            f"un-awaited .parse() call(s) at line(s) {unawaited} of "
            "unified_llm/adapters/anthropic.py — AsyncAPIResponse.parse is a "
            "coroutine function in anthropic 1.0.0"
        )
