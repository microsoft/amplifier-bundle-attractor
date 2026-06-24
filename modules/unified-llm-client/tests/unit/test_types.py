"""Tests for unified_llm.types — enums and data model."""

from unified_llm.types import ContentKind, Role


class TestRole:
    """Role enum covers all five spec-defined roles."""

    def test_all_five_roles_exist(self) -> None:
        assert Role.SYSTEM is not None
        assert Role.USER is not None
        assert Role.ASSISTANT is not None
        assert Role.TOOL is not None
        assert Role.DEVELOPER is not None

    def test_role_values_are_strings(self) -> None:
        assert Role.SYSTEM.value == "system"
        assert Role.USER.value == "user"
        assert Role.ASSISTANT.value == "assistant"
        assert Role.TOOL.value == "tool"
        assert Role.DEVELOPER.value == "developer"


class TestContentKind:
    """ContentKind enum covers all eight spec-defined kinds."""

    def test_all_eight_kinds_exist(self) -> None:
        expected = {
            "TEXT", "IMAGE", "AUDIO", "DOCUMENT",
            "TOOL_CALL", "TOOL_RESULT", "THINKING", "REDACTED_THINKING",
        }
        actual = {k.name for k in ContentKind}
        assert actual == expected

    def test_kind_values_are_lowercase(self) -> None:
        assert ContentKind.TEXT.value == "text"
        assert ContentKind.IMAGE.value == "image"
        assert ContentKind.TOOL_CALL.value == "tool_call"
        assert ContentKind.REDACTED_THINKING.value == "redacted_thinking"


from unified_llm.types import (
    AudioData,
    DocumentData,
    ImageData,
    ThinkingData,
    ToolCallData,
    ToolResultData,
)


class TestImageData:
    """Spec §3.5 — ImageData record."""

    def test_url_image(self) -> None:
        img = ImageData(url="https://example.com/photo.jpg")
        assert img.url == "https://example.com/photo.jpg"
        assert img.data is None
        assert img.media_type is None
        assert img.detail is None

    def test_data_image(self) -> None:
        img = ImageData(data=b"\x89PNG", media_type="image/png")
        assert img.data == b"\x89PNG"
        assert img.media_type == "image/png"

    def test_detail_hint(self) -> None:
        img = ImageData(url="https://example.com/x.jpg", detail="high")
        assert img.detail == "high"


class TestToolCallData:
    """Spec §3.5 — ToolCallData record."""

    def test_construction(self) -> None:
        tc = ToolCallData(
            id="call_123", name="get_weather",
            arguments={"city": "SF"}, type="function",
        )
        assert tc.id == "call_123"
        assert tc.name == "get_weather"
        assert tc.arguments == {"city": "SF"}
        assert tc.type == "function"

    def test_default_type_is_function(self) -> None:
        tc = ToolCallData(id="c1", name="foo", arguments={})
        assert tc.type == "function"


class TestToolResultData:
    """Spec §3.5 — ToolResultData record."""

    def test_construction(self) -> None:
        tr = ToolResultData(
            tool_call_id="call_123", content="72F and sunny", is_error=False,
        )
        assert tr.tool_call_id == "call_123"
        assert tr.content == "72F and sunny"
        assert tr.is_error is False

    def test_error_result(self) -> None:
        tr = ToolResultData(
            tool_call_id="c1", content="connection timeout", is_error=True,
        )
        assert tr.is_error is True


class TestThinkingData:
    """Spec §3.5 — ThinkingData record."""

    def test_thinking_with_signature(self) -> None:
        td = ThinkingData(
            text="Let me think step by step...",
            signature="sig_abc123",
            redacted=False,
        )
        assert td.text == "Let me think step by step..."
        assert td.signature == "sig_abc123"
        assert td.redacted is False

    def test_redacted_thinking(self) -> None:
        td = ThinkingData(text="", redacted=True)
        assert td.redacted is True
        assert td.signature is None


class TestAudioData:
    """Spec §3.5 — AudioData record."""

    def test_url_audio(self) -> None:
        ad = AudioData(url="https://example.com/audio.wav")
        assert ad.url == "https://example.com/audio.wav"
        assert ad.data is None


class TestDocumentData:
    """Spec §3.5 — DocumentData record."""

    def test_with_filename(self) -> None:
        dd = DocumentData(
            data=b"%PDF", media_type="application/pdf", file_name="report.pdf",
        )
        assert dd.file_name == "report.pdf"


from unified_llm.types import ContentPart


class TestContentPart:
    """Spec §3.3 — ContentPart tagged union."""

    def test_text_part(self) -> None:
        part = ContentPart(kind=ContentKind.TEXT, text="hello")
        assert part.kind == ContentKind.TEXT
        assert part.text == "hello"
        assert part.image is None

    def test_image_part(self) -> None:
        img = ImageData(url="https://example.com/img.png")
        part = ContentPart(kind=ContentKind.IMAGE, image=img)
        assert part.kind == ContentKind.IMAGE
        assert part.image is img

    def test_tool_call_part(self) -> None:
        tc = ToolCallData(id="c1", name="search", arguments={"q": "test"})
        part = ContentPart(kind=ContentKind.TOOL_CALL, tool_call=tc)
        assert part.kind == ContentKind.TOOL_CALL
        assert part.tool_call is tc

    def test_thinking_part(self) -> None:
        td = ThinkingData(text="Let me reason...", signature="sig1")
        part = ContentPart(kind=ContentKind.THINKING, thinking=td)
        assert part.kind == ContentKind.THINKING
        assert part.thinking is td

    def test_kind_accepts_arbitrary_string(self) -> None:
        """Spec says kind accepts String for extension."""
        part = ContentPart(kind="custom_provider_type", text="data")
        assert part.kind == "custom_provider_type"


from unified_llm.types import Message


class TestMessage:
    """Spec §3.1 — Message record with convenience constructors."""

    def test_basic_construction(self) -> None:
        msg = Message(
            role=Role.USER,
            content=[ContentPart(kind=ContentKind.TEXT, text="Hello")],
        )
        assert msg.role == Role.USER
        assert len(msg.content) == 1
        assert msg.name is None
        assert msg.tool_call_id is None

    def test_text_accessor_concatenates(self) -> None:
        """Spec: .text returns concatenation of all TEXT parts."""
        msg = Message(
            role=Role.ASSISTANT,
            content=[
                ContentPart(kind=ContentKind.TEXT, text="Hello "),
                ContentPart(kind=ContentKind.TOOL_CALL, tool_call=ToolCallData(
                    id="c1", name="foo", arguments={},
                )),
                ContentPart(kind=ContentKind.TEXT, text="world"),
            ],
        )
        assert msg.text == "Hello world"

    def test_text_accessor_empty_when_no_text_parts(self) -> None:
        msg = Message(role=Role.ASSISTANT, content=[])
        assert msg.text == ""

    def test_system_constructor(self) -> None:
        msg = Message.system("You are helpful.")
        assert msg.role == Role.SYSTEM
        assert msg.content[0].kind == ContentKind.TEXT
        assert msg.content[0].text == "You are helpful."

    def test_user_constructor(self) -> None:
        msg = Message.user("What is 2+2?")
        assert msg.role == Role.USER
        assert msg.text == "What is 2+2?"

    def test_assistant_constructor(self) -> None:
        msg = Message.assistant("The answer is 4.")
        assert msg.role == Role.ASSISTANT
        assert msg.text == "The answer is 4."

    def test_tool_result_constructor(self) -> None:
        msg = Message.tool_result(
            tool_call_id="call_123", content="72F and sunny", is_error=False,
        )
        assert msg.role == Role.TOOL
        assert msg.tool_call_id == "call_123"
        assert msg.content[0].kind == ContentKind.TOOL_RESULT
        assert msg.content[0].tool_result is not None
        assert msg.content[0].tool_result.tool_call_id == "call_123"
        assert msg.content[0].tool_result.content == "72F and sunny"


from unified_llm.types import Usage


class TestUsage:
    """Spec §3.9 — Usage with addition operator."""

    def test_basic_construction(self) -> None:
        u = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
        assert u.input_tokens == 100
        assert u.output_tokens == 50
        assert u.total_tokens == 150

    def test_optional_fields_default_none(self) -> None:
        u = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
        assert u.reasoning_tokens is None
        assert u.cache_read_tokens is None
        assert u.cache_write_tokens is None
        assert u.raw is None

    def test_addition_sums_integer_fields(self) -> None:
        a = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
        b = Usage(input_tokens=200, output_tokens=80, total_tokens=280)
        result = a + b
        assert result.input_tokens == 300
        assert result.output_tokens == 130
        assert result.total_tokens == 430

    def test_addition_handles_optional_fields(self) -> None:
        """If either side is non-None, sum them (treating None as 0)."""
        a = Usage(input_tokens=10, output_tokens=5, total_tokens=15, reasoning_tokens=100)
        b = Usage(input_tokens=20, output_tokens=10, total_tokens=30, reasoning_tokens=None)
        result = a + b
        assert result.reasoning_tokens == 100

    def test_addition_both_none_stays_none(self) -> None:
        a = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
        b = Usage(input_tokens=20, output_tokens=10, total_tokens=30)
        result = a + b
        assert result.reasoning_tokens is None
        assert result.cache_read_tokens is None

    def test_addition_both_present(self) -> None:
        a = Usage(
            input_tokens=10, output_tokens=5, total_tokens=15,
            cache_read_tokens=50, cache_write_tokens=10,
        )
        b = Usage(
            input_tokens=20, output_tokens=10, total_tokens=30,
            cache_read_tokens=30, cache_write_tokens=5,
        )
        result = a + b
        assert result.cache_read_tokens == 80
        assert result.cache_write_tokens == 15


from unified_llm.types import FinishReason, RateLimitInfo, Warning


class TestFinishReason:
    """Spec §3.8 — Dual-representation finish reason."""

    def test_unified_reason(self) -> None:
        fr = FinishReason(reason="stop", raw="end_turn")
        assert fr.reason == "stop"
        assert fr.raw == "end_turn"

    def test_raw_defaults_none(self) -> None:
        fr = FinishReason(reason="tool_calls")
        assert fr.raw is None


class TestWarning:
    """Spec §3.11 — Warning record."""

    def test_basic(self) -> None:
        w = Warning(message="Token limit approaching", code="token_limit")
        assert w.message == "Token limit approaching"
        assert w.code == "token_limit"


class TestRateLimitInfo:
    """Spec §3.12 — RateLimitInfo from response headers."""

    def test_all_fields(self) -> None:
        rli = RateLimitInfo(
            requests_remaining=99, requests_limit=100,
            tokens_remaining=9000, tokens_limit=10000,
        )
        assert rli.requests_remaining == 99

    def test_all_optional(self) -> None:
        rli = RateLimitInfo()
        assert rli.requests_remaining is None
        assert rli.reset_at is None


from unified_llm.types import Request, ResponseFormat


class TestResponseFormat:
    """Spec §3.10 — ResponseFormat."""

    def test_text_format(self) -> None:
        rf = ResponseFormat(type="text")
        assert rf.type == "text"
        assert rf.json_schema is None
        assert rf.strict is False

    def test_json_schema_format(self) -> None:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        rf = ResponseFormat(type="json_schema", json_schema=schema, strict=True)
        assert rf.json_schema == schema
        assert rf.strict is True


class TestRequest:
    """Spec §3.6 — Request record."""

    def test_minimal_request(self) -> None:
        req = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hello")],
        )
        assert req.model == "claude-sonnet-4-20250514"
        assert len(req.messages) == 1
        assert req.provider is None
        assert req.tools is None
        assert req.temperature is None

    def test_full_request(self) -> None:
        req = Request(
            model="gpt-5.2",
            messages=[Message.user("test")],
            provider="openai",
            temperature=0.7,
            max_tokens=500,
            reasoning_effort="high",
            provider_options={"openai": {"reasoning": {"effort": "high"}}},
        )
        assert req.provider == "openai"
        assert req.temperature == 0.7
        assert req.max_tokens == 500
        assert req.reasoning_effort == "high"


from unified_llm.types import Response


class TestResponse:
    """Spec §3.7 — Response with convenience accessors."""

    def _make_response(self) -> Response:
        return Response(
            id="resp_123",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            message=Message(
                role=Role.ASSISTANT,
                content=[
                    ContentPart(kind=ContentKind.THINKING, thinking=ThinkingData(
                        text="Reasoning here", signature="sig1",
                    )),
                    ContentPart(kind=ContentKind.TEXT, text="The answer is 42."),
                    ContentPart(kind=ContentKind.TOOL_CALL, tool_call=ToolCallData(
                        id="c1", name="calc", arguments={"expr": "6*7"},
                    )),
                ],
            ),
            finish_reason=FinishReason(reason="tool_calls", raw="tool_use"),
            usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    def test_text_accessor(self) -> None:
        resp = self._make_response()
        assert resp.text == "The answer is 42."

    def test_tool_calls_accessor(self) -> None:
        resp = self._make_response()
        calls = resp.tool_calls
        assert len(calls) == 1
        assert calls[0].name == "calc"

    def test_reasoning_accessor(self) -> None:
        resp = self._make_response()
        assert resp.reasoning == "Reasoning here"

    def test_reasoning_none_when_absent(self) -> None:
        resp = Response(
            id="r1", model="m", provider="p",
            message=Message.assistant("Hello"),
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        assert resp.reasoning is None

    def test_raw_and_warnings_default(self) -> None:
        resp = Response(
            id="r1", model="m", provider="p",
            message=Message.assistant("Hi"),
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        assert resp.raw is None
        assert resp.warnings == []
        assert resp.rate_limit is None


from unified_llm.types import Tool, ToolCall, ToolChoice, ToolResult


class TestTool:
    """Spec §5.1 — Tool definition."""

    def test_passive_tool(self) -> None:
        t = Tool(
            name="get_weather",
            description="Get weather for a location",
            parameters={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        )
        assert t.name == "get_weather"
        assert t.execute is None

    def test_active_tool(self) -> None:
        def handler(**kwargs: object) -> str:
            return "72F"

        t = Tool(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {}},
            execute=handler,
        )
        assert t.execute is not None


class TestToolChoice:
    """Spec §5.3 — ToolChoice modes."""

    def test_auto_mode(self) -> None:
        tc = ToolChoice(mode="auto")
        assert tc.mode == "auto"
        assert tc.tool_name is None

    def test_named_mode(self) -> None:
        tc = ToolChoice(mode="named", tool_name="get_weather")
        assert tc.mode == "named"
        assert tc.tool_name == "get_weather"


class TestToolCall:
    """Spec §5.4 — ToolCall extracted from responses."""

    def test_construction(self) -> None:
        tc = ToolCall(id="call_1", name="search", arguments={"q": "test"})
        assert tc.raw_arguments is None

    def test_with_raw_arguments(self) -> None:
        tc = ToolCall(
            id="call_1", name="search",
            arguments={"q": "test"}, raw_arguments='{"q": "test"}',
        )
        assert tc.raw_arguments == '{"q": "test"}'


class TestToolResult:
    """Spec §5.4 — ToolResult from execute handlers."""

    def test_success_result(self) -> None:
        tr = ToolResult(tool_call_id="c1", content="72F", is_error=False)
        assert tr.is_error is False

    def test_error_result(self) -> None:
        tr = ToolResult(tool_call_id="c1", content="timeout", is_error=True)
        assert tr.is_error is True


from unified_llm.types import StreamAccumulator, StreamEvent, StreamEventType


class TestStreamEventType:
    """Spec §3.14 — StreamEventType enum with 13 types."""

    def test_all_thirteen_types_exist(self) -> None:
        expected = {
            "STREAM_START", "TEXT_START", "TEXT_DELTA", "TEXT_END",
            "REASONING_START", "REASONING_DELTA", "REASONING_END",
            "TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END",
            "FINISH", "ERROR", "PROVIDER_EVENT",
        }
        actual = {t.name for t in StreamEventType}
        assert actual == expected


class TestStreamEvent:
    """Spec §3.13 — StreamEvent record."""

    def test_text_delta_event(self) -> None:
        evt = StreamEvent(
            type=StreamEventType.TEXT_DELTA, delta="Hello", text_id="t1",
        )
        assert evt.type == StreamEventType.TEXT_DELTA
        assert evt.delta == "Hello"
        assert evt.text_id == "t1"

    def test_finish_event(self) -> None:
        evt = StreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        assert evt.finish_reason is not None
        assert evt.usage is not None

    def test_error_event(self) -> None:
        evt = StreamEvent(type=StreamEventType.ERROR)
        assert evt.error is None  # error field exists but defaults to None

    def test_type_accepts_string(self) -> None:
        """Spec: type accepts String for provider-specific events."""
        evt = StreamEvent(type="custom_event")
        assert evt.type == "custom_event"


class TestStreamAccumulator:
    """Spec §4.4 — StreamAccumulator assembles events into Response."""

    def test_accumulate_text_deltas(self) -> None:
        acc = StreamAccumulator()
        acc.process(StreamEvent(type=StreamEventType.TEXT_START, text_id="t1"))
        acc.process(StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Hello ", text_id="t1"))
        acc.process(StreamEvent(type=StreamEventType.TEXT_DELTA, delta="world", text_id="t1"))
        acc.process(StreamEvent(type=StreamEventType.TEXT_END, text_id="t1"))
        acc.process(StreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        ))
        resp = acc.response()
        assert resp.text == "Hello world"
        assert resp.finish_reason.reason == "stop"
        assert resp.usage.total_tokens == 15

    def test_accumulate_tool_call(self) -> None:
        acc = StreamAccumulator()
        acc.process(StreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call=ToolCall(id="c1", name="search", arguments={}),
        ))
        acc.process(StreamEvent(
            type=StreamEventType.TOOL_CALL_DELTA,
            tool_call=ToolCall(id="c1", name="search", arguments={}, raw_arguments='{"q":'),
        ))
        acc.process(StreamEvent(
            type=StreamEventType.TOOL_CALL_END,
            tool_call=ToolCall(id="c1", name="search", arguments={"q": "test"}),
        ))
        acc.process(StreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=FinishReason(reason="tool_calls"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        ))
        resp = acc.response()
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"


from unified_llm.types import GenerateResult, StepResult


class TestStepResult:
    """Spec §4.3 — StepResult for each tool loop iteration."""

    def test_construction(self) -> None:
        resp = Response(
            id="r1", model="m", provider="p",
            message=Message.assistant("Hi"),
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        step = StepResult(
            text="Hi", tool_calls=[], tool_results=[],
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            response=resp, warnings=[],
        )
        assert step.text == "Hi"
        assert step.reasoning is None


class TestGenerateResult:
    """Spec §4.3 — GenerateResult aggregating steps."""

    def test_construction(self) -> None:
        resp = Response(
            id="r1", model="m", provider="p",
            message=Message.assistant("Final answer"),
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        result = GenerateResult(
            text="Final answer",
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            total_usage=Usage(input_tokens=30, output_tokens=15, total_tokens=45),
            steps=[],
            response=resp,
        )
        assert result.text == "Final answer"
        assert result.total_usage.total_tokens == 45
        assert result.output is None


from datetime import date

from unified_llm.types import AdapterTimeout, ModelInfo, TimeoutConfig


class TestTimeoutConfig:
    """Spec §4.7 — Timeout configuration."""

    def test_defaults(self) -> None:
        tc = TimeoutConfig()
        assert tc.total is None
        assert tc.per_step is None


class TestAdapterTimeout:
    """Spec §4.7 — Adapter-level timeout scopes."""

    def test_defaults(self) -> None:
        at = AdapterTimeout()
        assert at.connect == 10.0
        assert at.request == 120.0
        assert at.stream_read == 30.0


class TestModelInfo:
    """Spec §2.9 — Model catalog entry."""

    def test_construction(self) -> None:
        mi = ModelInfo(
            id="claude-sonnet-4-20250514",
            provider="anthropic",
            display_name="Claude Sonnet 4",
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            supports_reasoning=True,
            release_date=date(2025, 5, 14),
        )
        assert mi.id == "claude-sonnet-4-20250514"
        assert mi.release_date == date(2025, 5, 14)
        assert mi.max_output is None
        assert mi.input_cost_per_million is None
        assert mi.aliases == []
