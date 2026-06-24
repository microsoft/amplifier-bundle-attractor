"""Data model types for the unified LLM client.

Implements all 30+ types from the Unified LLM Client Specification (Sections 3.1-3.14).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Section 3.2 — Role
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Five roles covering the semantics of all major providers."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    DEVELOPER = "developer"


# ---------------------------------------------------------------------------
# Section 3.4 — ContentKind
# ---------------------------------------------------------------------------


class ContentKind(str, Enum):
    """Tagged union discriminator for ContentPart."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    REDACTED_THINKING = "redacted_thinking"


# ---------------------------------------------------------------------------
# Section 3.5 — Content Data Structures
# ---------------------------------------------------------------------------


@dataclass
class ImageData:
    """Image as URL, base64, or file reference. Exactly one of url/data must be set."""

    url: str | None = None
    data: bytes | None = None
    media_type: str | None = None
    detail: str | None = None


@dataclass
class AudioData:
    """Audio as URL or raw bytes with media type."""

    url: str | None = None
    data: bytes | None = None
    media_type: str | None = None


@dataclass
class DocumentData:
    """Document (PDF, etc.) as URL, base64, or file reference."""

    url: str | None = None
    data: bytes | None = None
    media_type: str | None = None
    file_name: str | None = None


@dataclass
class ToolCallData:
    """A model-initiated tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] | str
    type: str = "function"


@dataclass
class ToolResultData:
    """The result of executing a tool call."""

    tool_call_id: str
    content: str | dict[str, Any]
    is_error: bool = False
    image_data: bytes | None = None
    image_media_type: str | None = None


@dataclass
class ThinkingData:
    """Model reasoning/thinking content."""

    text: str
    signature: str | None = None
    redacted: bool = False


@dataclass
class ContentPart:
    """Tagged union for message content. The kind field determines which data field is populated.

    Spec §3.3: kind accepts both ContentKind enum values and arbitrary strings
    for provider-specific extension.
    """

    kind: ContentKind | str
    text: str | None = None
    image: ImageData | None = None
    audio: AudioData | None = None
    document: DocumentData | None = None
    tool_call: ToolCallData | None = None
    tool_result: ToolResultData | None = None
    thinking: ThinkingData | None = None


@dataclass
class Message:
    """The fundamental unit of conversation (Spec §3.1).

    A conversation is an ordered List[Message].
    """

    role: Role
    content: list[ContentPart]
    name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        """Auto-normalize string content to list[ContentPart]."""
        if isinstance(self.content, str):
            self.content = [ContentPart(kind=ContentKind.TEXT, text=self.content)]

    @property
    def text(self) -> str:
        """Concatenate text from all TEXT content parts. Returns '' if none."""
        return "".join(
            part.text
            for part in self.content
            if part.kind == ContentKind.TEXT and part.text is not None
        )

    @classmethod
    def system(cls, text: str) -> Message:
        """Convenience constructor for system messages."""
        return cls(
            role=Role.SYSTEM, content=[ContentPart(kind=ContentKind.TEXT, text=text)]
        )

    @classmethod
    def user(cls, text: str) -> Message:
        """Convenience constructor for user messages."""
        return cls(
            role=Role.USER, content=[ContentPart(kind=ContentKind.TEXT, text=text)]
        )

    @classmethod
    def assistant(cls, text: str) -> Message:
        """Convenience constructor for assistant messages."""
        return cls(
            role=Role.ASSISTANT, content=[ContentPart(kind=ContentKind.TEXT, text=text)]
        )

    @classmethod
    def tool_result(
        cls,
        *,
        tool_call_id: str,
        content: str | dict[str, Any],
        is_error: bool = False,
    ) -> Message:
        """Convenience constructor for tool result messages."""
        return cls(
            role=Role.TOOL,
            content=[
                ContentPart(
                    kind=ContentKind.TOOL_RESULT,
                    tool_result=ToolResultData(
                        tool_call_id=tool_call_id,
                        content=content,
                        is_error=is_error,
                    ),
                )
            ],
            tool_call_id=tool_call_id,
        )


def _add_optional(a: int | None, b: int | None) -> int | None:
    """Sum two optional ints: both None → None, else treat None as 0."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


@dataclass
class Usage:
    """Token usage statistics (Spec §3.9). Supports addition for multi-step aggregation."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    raw: dict[str, Any] | None = None

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            reasoning_tokens=_add_optional(
                self.reasoning_tokens, other.reasoning_tokens
            ),
            cache_read_tokens=_add_optional(
                self.cache_read_tokens, other.cache_read_tokens
            ),
            cache_write_tokens=_add_optional(
                self.cache_write_tokens, other.cache_write_tokens
            ),
        )


# ---------------------------------------------------------------------------
# Section 3.8 — FinishReason
# ---------------------------------------------------------------------------


@dataclass
class FinishReason:
    """Dual representation: unified reason + provider-specific raw value."""

    reason: str  # "stop", "length", "tool_calls", "content_filter", "error", "other"
    raw: str | None = None


# ---------------------------------------------------------------------------
# Section 3.11 — Warning
# ---------------------------------------------------------------------------


@dataclass
class Warning:
    """Non-fatal issue from the provider."""

    message: str
    code: str | None = None


# ---------------------------------------------------------------------------
# Section 3.12 — RateLimitInfo
# ---------------------------------------------------------------------------


@dataclass
class RateLimitInfo:
    """Rate limit metadata from provider response headers."""

    requests_remaining: int | None = None
    requests_limit: int | None = None
    tokens_remaining: int | None = None
    tokens_limit: int | None = None
    reset_at: datetime | None = None


# ---------------------------------------------------------------------------
# Section 3.10 — ResponseFormat
# ---------------------------------------------------------------------------


@dataclass
class ResponseFormat:
    """Structured output format specification."""

    type: str  # "text", "json", "json_schema"
    json_schema: dict[str, Any] | None = None
    strict: bool = False


# ---------------------------------------------------------------------------
# Section 3.6 — Request
# ---------------------------------------------------------------------------


@dataclass
class Request:
    """The single input type for both complete() and stream() (Spec §3.6)."""

    model: str
    messages: list[Message]
    provider: str | None = None
    tools: list[Any] | None = None  # list[Tool] — forward ref resolved in Task 10
    tool_choice: Any | None = None  # ToolChoice — forward ref resolved in Task 10
    response_format: ResponseFormat | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop_sequences: list[str] | None = None
    reasoning_effort: str | None = None
    metadata: dict[str, str] | None = None
    provider_options: dict[str, Any] | None = None
    stream_validation_mode: str | None = None


# ---------------------------------------------------------------------------
# Section 3.7 — Response
# ---------------------------------------------------------------------------


@dataclass
class Response:
    """Unified response from any provider (Spec §3.7)."""

    id: str
    model: str
    provider: str
    message: Message
    finish_reason: FinishReason
    usage: Usage
    raw: dict[str, Any] | None = None
    warnings: list[Warning] = field(default_factory=list)
    rate_limit: RateLimitInfo | None = None

    @property
    def text(self) -> str:
        """Concatenated text from all text parts."""
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCallData]:
        """Extracted tool calls from the message."""
        return [
            part.tool_call
            for part in self.message.content
            if part.kind == ContentKind.TOOL_CALL and part.tool_call is not None
        ]

    @property
    def reasoning(self) -> str | None:
        """Concatenated reasoning/thinking text, or None if absent."""
        parts = [
            part.thinking.text
            for part in self.message.content
            if part.kind in (ContentKind.THINKING, ContentKind.REDACTED_THINKING)
            and part.thinking is not None
            and part.thinking.text
        ]
        return "".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Section 5.1 — Tool
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    """Tool definition with optional execute handler (Spec §5.1)."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Any] | None = None


# ---------------------------------------------------------------------------
# Section 5.3 — ToolChoice
# ---------------------------------------------------------------------------


@dataclass
class ToolChoice:
    """Controls how the model uses tools (Spec §5.3)."""

    mode: str  # "auto", "none", "required", "named"
    tool_name: str | None = None


# ---------------------------------------------------------------------------
# Section 5.4 — ToolCall and ToolResult
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A tool invocation extracted from a response."""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None


@dataclass
class ToolResult:
    """The output of executing a tool call."""

    tool_call_id: str
    content: str | dict[str, Any] | list[Any]
    is_error: bool = False


# ---------------------------------------------------------------------------
# Section 3.14 — StreamEventType
# ---------------------------------------------------------------------------


class StreamEventType(str, Enum):
    """Thirteen stream event types following the start/delta/end pattern."""

    STREAM_START = "stream_start"
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_END = "text_end"
    REASONING_START = "reasoning_start"
    REASONING_DELTA = "reasoning_delta"
    REASONING_END = "reasoning_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    FINISH = "finish"
    ERROR = "error"
    PROVIDER_EVENT = "provider_event"


# ---------------------------------------------------------------------------
# Section 3.13 — StreamEvent
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    """Unified stream event (Spec §3.13)."""

    type: StreamEventType | str

    # Text events
    delta: str | None = None
    text_id: str | None = None

    # Reasoning events
    reasoning_delta: str | None = None

    # Tool call events
    tool_call: ToolCall | None = None

    # Finish event
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    response: Response | None = None

    # Error event
    error: Any | None = None  # SDKError — typed in errors.py

    # Passthrough
    raw: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Section 4.4 — StreamAccumulator
# ---------------------------------------------------------------------------


class StreamAccumulator:
    """Collects stream events into a complete Response (Spec §4.4)."""

    def __init__(self) -> None:
        self._text_parts: dict[str | None, list[str]] = {}
        self._reasoning_parts: list[str] = []
        self._tool_calls: list[ToolCall] = []
        self._finish_reason: FinishReason | None = None
        self._usage: Usage | None = None
        self._response_id: str = ""
        self._model: str = ""
        self._provider: str = ""

    def process(self, event: StreamEvent) -> None:
        """Process a single stream event."""
        if event.type == StreamEventType.TEXT_DELTA and event.delta:
            key = event.text_id
            self._text_parts.setdefault(key, []).append(event.delta)
        elif event.type == StreamEventType.REASONING_DELTA and event.reasoning_delta:
            self._reasoning_parts.append(event.reasoning_delta)
        elif event.type == StreamEventType.TOOL_CALL_END and event.tool_call:
            self._tool_calls.append(event.tool_call)
        elif event.type == StreamEventType.FINISH:
            self._finish_reason = event.finish_reason
            self._usage = event.usage
            if event.response:
                self._response_id = event.response.id
                self._model = event.response.model
                self._provider = event.response.provider

    def response(self) -> Response:
        """Build the accumulated Response. Call after stream ends."""
        content: list[ContentPart] = []

        # Assemble text
        full_text = "".join("".join(parts) for parts in self._text_parts.values())
        if full_text:
            content.append(ContentPart(kind=ContentKind.TEXT, text=full_text))

        # Assemble reasoning
        if self._reasoning_parts:
            reasoning_text = "".join(self._reasoning_parts)
            content.append(
                ContentPart(
                    kind=ContentKind.THINKING,
                    thinking=ThinkingData(text=reasoning_text),
                )
            )

        # Assemble tool calls
        for tc in self._tool_calls:
            content.append(
                ContentPart(
                    kind=ContentKind.TOOL_CALL,
                    tool_call=ToolCallData(
                        id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                    ),
                )
            )

        return Response(
            id=self._response_id,
            model=self._model,
            provider=self._provider,
            message=Message(role=Role.ASSISTANT, content=content),
            finish_reason=self._finish_reason or FinishReason(reason="other"),
            usage=self._usage or Usage(input_tokens=0, output_tokens=0, total_tokens=0),
        )


# ---------------------------------------------------------------------------
# Section 4.3 — StepResult and GenerateResult
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Result of a single step in the tool loop."""

    text: str
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    finish_reason: FinishReason
    usage: Usage
    response: Response
    warnings: list[Warning]
    reasoning: str | None = None


@dataclass
class GenerateResult:
    """Aggregated result from generate() across all steps."""

    text: str
    finish_reason: FinishReason
    usage: Usage
    total_usage: Usage
    steps: list[StepResult]
    response: Response
    reasoning: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    output: Any | None = None  # Parsed structured output (generate_object)


# ---------------------------------------------------------------------------
# Section 4.7 — Timeout Configuration
# ---------------------------------------------------------------------------


@dataclass
class TimeoutConfig:
    """Multi-step timeout configuration."""

    total: float | None = None
    per_step: float | None = None


@dataclass
class AdapterTimeout:
    """Adapter-level timeout scopes with sensible defaults."""

    connect: float = 10.0
    request: float = 120.0
    stream_read: float = 30.0


# ---------------------------------------------------------------------------
# Section 2.9 — ModelInfo (catalog entry)
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """A model catalog entry.

    ``release_date`` is the authoritative recency field used by
    ``get_latest_model()`` to select the newest model without depending on
    JSON file order.  It is typed, required (no default), and must be an ISO
    8601 date string in the source ``models.json``.
    """

    id: str
    provider: str
    display_name: str
    context_window: int
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    release_date: date  # required; used for recency selection — no default
    max_output: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    aliases: list[str] = field(default_factory=list)
