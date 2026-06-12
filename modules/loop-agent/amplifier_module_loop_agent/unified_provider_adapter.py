"""UnifiedProviderAdapter: wraps unified-llm-client for loop-agent's duck-type contract.

See docs/designs/loop-agent-unified-adapter.md for full design.

The adapter satisfies AgentSession's provider duck-type:
  - adapter.complete(request: ChatRequest) -> ChatResponse
  - adapter.stream(request: ChatRequest) -> AsyncIterator[dict]

Internally it translates types, calls unified-llm-client, translates
results back, and maps errors from SDKError to LLMError.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from amplifier_core.llm_errors import (
    AuthenticationError as CoreAuthError,
    ContentFilterError as CoreContentFilterError,
    ContextLengthError as CoreContextLengthError,
    LLMError,
    LLMTimeoutError,
    ProviderUnavailableError,
    RateLimitError as CoreRateLimitError,
    StreamError as CoreStreamError,
)
from amplifier_core.message_models import (
    ChatRequest,
    ChatResponse,
    Message as CoreMessage,
    TextBlock,
    ThinkingBlock,
    ToolCall as CoreToolCall,
    Usage as CoreUsage,
)
from unified_llm import errors as ulm_errors
from unified_llm.client import Client
from unified_llm.errors import SDKError
from unified_llm.types import (
    ContentKind,
    ContentPart,
    Message as ULMMessage,
    Request as ULMRequest,
    Response as ULMResponse,
    Role,
    StreamEvent,
    StreamEventType,
    ThinkingData,
    ToolResultData as ULMToolResultData,
    Usage as ULMUsage,
)

logger = logging.getLogger(__name__)


class UnifiedProviderAdapter:
    """Wraps unified-llm-client to satisfy loop-agent's provider duck-type."""

    def __init__(
        self,
        *,
        provider_name: str,
        model: str,
        client: Any = None,
    ) -> None:
        self._provider_name = provider_name
        self._model = model

        if client is not None:
            self._client = client
        else:
            # Build client from environment-detected API keys
            self._client = Client.from_env()

    # ------------------------------------------------------------------
    # Request translation
    # ------------------------------------------------------------------

    def _translate_request(self, request: ChatRequest) -> ULMRequest:
        """Translate ChatRequest -> unified_llm.Request."""
        messages = [self._translate_message(m) for m in request.messages]
        return ULMRequest(
            model=self._model,
            messages=messages,
            provider=self._provider_name,
            reasoning_effort=request.reasoning_effort,
            # Tools are NOT passed: the agent owns the tool loop.
            # We only do single LLM calls via client.complete().
        )

    def _translate_message(self, msg: CoreMessage) -> ULMMessage:
        """Translate a single amplifier-core Message to unified-llm Message."""
        role = self._translate_role(msg.role)

        # Tool result messages: wrap string content as TOOL_RESULT part
        if msg.role == "tool" and msg.tool_call_id:
            content_str = (
                msg.content if isinstance(msg.content, str) else str(msg.content)
            )
            content = [
                ContentPart(
                    kind=ContentKind.TOOL_RESULT,
                    tool_result=ULMToolResultData(
                        tool_call_id=msg.tool_call_id,
                        content=content_str,
                    ),
                )
            ]
            return ULMMessage(role=role, content=content, tool_call_id=msg.tool_call_id)

        content = self._translate_content(msg.content)
        return ULMMessage(role=role, content=content, tool_call_id=msg.tool_call_id)

    @staticmethod
    def _translate_role(role: str) -> Role:
        """Map amplifier-core role string to unified-llm Role enum."""
        _ROLE_MAP = {
            "system": Role.SYSTEM,
            "user": Role.USER,
            "assistant": Role.ASSISTANT,
            "tool": Role.TOOL,
            "developer": Role.DEVELOPER,
            "function": Role.TOOL,  # Legacy function role
        }
        return _ROLE_MAP.get(role, Role.USER)

    @staticmethod
    def _translate_content(content: str | list) -> list[ContentPart]:
        """Translate message content to unified-llm ContentParts."""
        if isinstance(content, str):
            return [ContentPart(kind=ContentKind.TEXT, text=content)]

        parts: list[ContentPart] = []
        for block in content:
            if isinstance(block, TextBlock):
                parts.append(ContentPart(kind=ContentKind.TEXT, text=block.text))
            elif isinstance(block, ThinkingBlock):
                parts.append(
                    ContentPart(
                        kind=ContentKind.THINKING,
                        thinking=ThinkingData(
                            text=block.thinking,
                            signature=block.signature,
                        ),
                    )
                )
            # Other block types fall through gracefully.

        return parts or [ContentPart(kind=ContentKind.TEXT, text="")]

    # ------------------------------------------------------------------
    # Response translation (non-streaming)
    # ------------------------------------------------------------------

    def _translate_response(self, response: ULMResponse) -> ChatResponse:
        """Translate unified_llm.Response -> ChatResponse."""
        content_blocks = []

        for part in response.message.content:
            if part.kind == ContentKind.TEXT and part.text:
                content_blocks.append(TextBlock(text=part.text))
            elif part.kind == ContentKind.THINKING and part.thinking:
                content_blocks.append(
                    ThinkingBlock(
                        thinking=part.thinking.text,
                        signature=part.thinking.signature,
                    )
                )
            # TOOL_CALL parts handled separately below

        tool_calls = self._translate_tool_calls(response)
        usage = self._translate_usage(response.usage)

        return ChatResponse(
            content=content_blocks,
            tool_calls=tool_calls if tool_calls else None,
            usage=usage,
        )

    @staticmethod
    def _translate_tool_calls(response: ULMResponse) -> list[CoreToolCall]:
        """Extract and translate tool calls from unified-llm response."""
        result = []
        for tc_data in response.tool_calls:
            # ToolCallData.arguments can be dict or str (JSON)
            if isinstance(tc_data.arguments, dict):
                arguments = tc_data.arguments
            else:
                try:
                    arguments = json.loads(tc_data.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            result.append(
                CoreToolCall(id=tc_data.id, name=tc_data.name, arguments=arguments)
            )
        return result

    @staticmethod
    def _translate_usage(usage: ULMUsage | None) -> CoreUsage | None:
        """Translate unified-llm Usage to amplifier-core Usage."""
        if usage is None:
            return None
        return CoreUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    def _map_error(self, error: SDKError) -> LLMError:
        """Map unified-llm SDKError to amplifier-core LLMError."""
        msg = str(error)
        provider = self._provider_name
        status_code = getattr(error, "status_code", None)

        # --- Provider errors (have provider/status_code on the ULM side) ---

        if isinstance(error, ulm_errors.AuthenticationError):
            return CoreAuthError(msg, provider=provider, status_code=status_code)

        if isinstance(error, ulm_errors.AccessDeniedError):
            return CoreAuthError(msg, provider=provider, status_code=status_code)

        if isinstance(error, ulm_errors.RateLimitError):
            retry_after = getattr(error, "retry_after", None)
            return CoreRateLimitError(
                msg, provider=provider, status_code=status_code, retry_after=retry_after
            )

        if isinstance(error, ulm_errors.ServerError):
            return ProviderUnavailableError(
                msg, provider=provider, status_code=status_code
            )

        if isinstance(error, ulm_errors.ContentFilterError):
            return CoreContentFilterError(
                msg, provider=provider, status_code=status_code
            )

        if isinstance(error, ulm_errors.ContextLengthError):
            return CoreContextLengthError(
                msg, provider=provider, status_code=status_code
            )

        # --- Non-provider errors ---

        if isinstance(error, ulm_errors.RequestTimeoutError):
            return LLMTimeoutError(msg, provider=provider)

        if isinstance(error, ulm_errors.NetworkError):
            return ProviderUnavailableError(msg, provider=provider)

        if isinstance(error, ulm_errors.StreamError):
            return CoreStreamError(msg, provider=provider)

        # --- Fallback ---
        return LLMError(msg, provider=provider, retryable=error.retryable)

    # ------------------------------------------------------------------
    # Public API: complete()
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the wrapped unified LLM client (spec finalize contract).

        The wrapped ``unified_llm.Client`` owns an ``AsyncAnthropic``/httpx
        client bound to the running event loop.  When the owning (child)
        session is torn down it must close this WITHIN the loop; otherwise GC
        later runs ``aclose()`` on a closed loop, raising
        ``RuntimeError: Event loop is closed``.

        Safe and idempotent: a no-op when the wrapped client does not expose
        ``close()``.
        """
        close_fn = getattr(self._client, "close", None)
        if close_fn is not None:
            await close_fn()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Satisfy loop-agent's provider.complete() contract.

        Translates ChatRequest -> unified_llm.Request, calls client.complete(),
        translates unified_llm.Response -> ChatResponse.
        """
        ulm_request = self._translate_request(request)
        try:
            ulm_response = await self._client.complete(ulm_request)
        except SDKError as e:
            raise self._map_error(e) from e
        return self._translate_response(ulm_response)

    # ------------------------------------------------------------------
    # Public API: stream()
    # ------------------------------------------------------------------

    async def stream(self, request: ChatRequest):
        """Satisfy loop-agent's provider.stream() contract.

        MUST be an async generator function (uses yield) so that
        inspect.isasyncgenfunction(adapter.stream) returns True.

        Yields dict chunks with keys the agent session expects:
          content, thinking, reasoning_signature, tool_calls, usage
        """
        ulm_request = self._translate_request(request)

        try:
            async for event in self._client.stream(ulm_request):
                # --- Tool-call buffering ---
                if event.type == StreamEventType.TOOL_CALL_START:
                    continue  # Don't yield START

                if event.type == StreamEventType.TOOL_CALL_DELTA:
                    continue  # Don't yield DELTA (args accumulated by provider)

                if event.type == StreamEventType.TOOL_CALL_END:
                    tc = event.tool_call
                    if tc:
                        # Use complete tool_call from END event
                        arguments = tc.arguments
                        if not arguments and tc.raw_arguments:
                            try:
                                arguments = json.loads(tc.raw_arguments)
                            except (json.JSONDecodeError, TypeError):
                                arguments = {}
                        yield {
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "arguments": arguments,
                                }
                            ]
                        }
                    continue

                # --- Regular event translation ---
                chunk = self._translate_stream_event(event)
                if chunk is not None:
                    yield chunk

        except SDKError as e:
            raise self._map_error(e) from e

    def _translate_stream_event(self, event: StreamEvent) -> dict[str, Any] | None:
        """Translate a single StreamEvent to a dict chunk, or None to skip."""
        if event.type == StreamEventType.TEXT_DELTA and event.delta:
            return {"content": event.delta}

        if event.type == StreamEventType.REASONING_DELTA and event.reasoning_delta:
            return {"thinking": event.reasoning_delta}

        if event.type == StreamEventType.REASONING_END:
            # Extract signature for Anthropic multi-turn round-tripping
            sig = None
            if event.raw and isinstance(event.raw, dict):
                sig = event.raw.get("signature")
            if sig:
                return {"reasoning_signature": sig}
            return None

        if event.type == StreamEventType.FINISH and event.usage:
            usage_dict: dict[str, Any] = {
                "input_tokens": event.usage.input_tokens,
                "output_tokens": event.usage.output_tokens,
                "total_tokens": event.usage.total_tokens,
            }
            if event.usage.reasoning_tokens is not None:
                usage_dict["reasoning_tokens"] = event.usage.reasoning_tokens
            if event.usage.cache_read_tokens is not None:
                usage_dict["cache_read_tokens"] = event.usage.cache_read_tokens
            if event.usage.cache_write_tokens is not None:
                usage_dict["cache_write_tokens"] = event.usage.cache_write_tokens
            return {"usage": usage_dict}

        # STREAM_START, TEXT_START, TEXT_END, REASONING_START: skip
        return None
