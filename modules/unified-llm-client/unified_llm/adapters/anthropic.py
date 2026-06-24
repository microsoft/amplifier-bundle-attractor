"""Anthropic Messages API adapter (Spec §7.3-7.8).

Wraps the anthropic SDK's AsyncAnthropic client to implement the
ProviderAdapter interface with request/response/error/stream translation.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import anthropic

from collections.abc import AsyncIterator

from unified_llm import errors
from unified_llm.types import (
    ContentKind,
    ContentPart,
    FinishReason,
    Message,
    Request,
    Response,
    Role,
    StreamEvent,
    StreamEventType,
    ThinkingData,
    Tool,
    ToolCall,
    ToolCallData,
    ToolChoice,
    Usage,
)


class AnthropicAdapter:
    """Anthropic Messages API adapter."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        if default_headers is not None:
            kwargs["default_headers"] = default_headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = anthropic.AsyncAnthropic(**kwargs)

    @property
    def name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Task 25: complete() Integration
    # ------------------------------------------------------------------

    async def complete(self, request: Request) -> Response:
        """Send a request, block until done, return full Response."""
        try:
            kwargs = self._translate_request(request)
            raw = await self._client.messages.create(**kwargs)
            return self._translate_response(raw)
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            raise self._translate_error(e) from e

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Send a request, return async iterator of StreamEvent."""
        kwargs = self._translate_request(request)
        kwargs["stream"] = True

        try:
            raw_stream = await self._client.messages.create(**kwargs)
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            raise self._translate_error(e) from e

        message_id = ""
        model = request.model
        input_tokens = 0
        output_tokens = 0
        finish_reason: FinishReason = FinishReason(reason="other")

        # Per-block state
        current_block_type: str | None = None
        current_tool_id: str = ""
        current_tool_name: str = ""
        tool_args_parts: list[str] = []

        try:
            async for event in raw_stream:
                if event.type == "message_start":
                    message_id = event.message.id
                    model = event.message.model
                    input_tokens = event.message.usage.input_tokens
                    yield StreamEvent(type=StreamEventType.STREAM_START)

                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "text":
                        current_block_type = "text"
                        yield StreamEvent(type=StreamEventType.TEXT_START)
                    elif block.type == "tool_use":
                        current_block_type = "tool_use"
                        current_tool_id = block.id
                        current_tool_name = block.name
                        tool_args_parts = []
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call=ToolCall(
                                id=block.id, name=block.name, arguments={}
                            ),
                        )
                    elif block.type == "thinking":
                        current_block_type = "thinking"
                        yield StreamEvent(type=StreamEventType.REASONING_START)

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA, delta=delta.text
                        )
                    elif delta.type == "input_json_delta":
                        tool_args_parts.append(delta.partial_json)
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_DELTA,
                            delta=delta.partial_json,
                        )
                    elif delta.type == "thinking_delta":
                        yield StreamEvent(
                            type=StreamEventType.REASONING_DELTA,
                            reasoning_delta=delta.thinking,
                        )

                elif event.type == "content_block_stop":
                    if current_block_type == "text":
                        yield StreamEvent(type=StreamEventType.TEXT_END)
                    elif current_block_type == "tool_use":
                        args_str = "".join(tool_args_parts)
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_END,
                            tool_call=ToolCall(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments=args,
                                raw_arguments=args_str or None,
                            ),
                        )
                    elif current_block_type == "thinking":
                        yield StreamEvent(type=StreamEventType.REASONING_END)
                    current_block_type = None

                elif event.type == "message_delta":
                    output_tokens = event.usage.output_tokens
                    finish_reason = self._map_finish_reason(event.delta.stop_reason)

                elif event.type == "message_stop":
                    usage = Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                    )
                    yield StreamEvent(
                        type=StreamEventType.FINISH,
                        finish_reason=finish_reason,
                        usage=usage,
                        response=Response(
                            id=message_id,
                            model=model,
                            provider="anthropic",
                            message=Message(role=Role.ASSISTANT, content=[]),
                            finish_reason=finish_reason,
                            usage=usage,
                        ),
                    )
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            raise self._translate_error(e) from e

    async def close(self) -> None:
        """Release resources."""
        await self._client.close()

    async def initialize(self) -> None:
        """Validate configuration on startup."""

    async def list_models(self) -> list[str]:
        """Return the live list of model ids served by this Anthropic client.

        Uses the same ``self._client`` instance used for ``complete()`` and
        ``stream()``, so the returned ids are in the same namespace as what
        the adapter passes to ``messages.create(model=...)``.  This is the
        foundation of the id-seam guarantee: the lister IS the generator.

        Returns first page only (Anthropic serves all current models on
        the first page — no autopagination needed in practice).
        """
        page = await self._client.models.list()
        return [m.id for m in page.data]

    def supports_tool_choice(self, mode: str) -> bool:
        """Check if a particular tool choice mode is supported."""
        return mode in ("auto", "none", "required", "named")

    # ------------------------------------------------------------------
    # Task 22: Request Translation
    # ------------------------------------------------------------------

    def _translate_request(self, request: Request) -> dict[str, Any]:
        """Convert unified Request → Anthropic Messages API kwargs."""
        system_parts: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []

        for msg in request.messages:
            if msg.role in (Role.SYSTEM, Role.DEVELOPER):
                # Extract to system parameter
                for part in msg.content:
                    if part.kind == ContentKind.TEXT and part.text:
                        system_parts.append({"type": "text", "text": part.text})
            elif msg.role == Role.USER:
                content = self._translate_user_content(msg.content)
                messages.append({"role": "user", "content": content})
            elif msg.role == Role.ASSISTANT:
                content = self._translate_assistant_content(msg.content)
                messages.append({"role": "assistant", "content": content})
            elif msg.role == Role.TOOL:
                # Tool results go in user messages for Anthropic
                content = self._translate_tool_result_content(msg.content)
                messages.append({"role": "user", "content": content})

        # Merge consecutive same-role messages (Anthropic requires alternation)
        messages = self._merge_consecutive_roles(messages)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }

        if system_parts:
            kwargs["system"] = system_parts

        # Tools
        if request.tools:
            kwargs["tools"] = self._translate_tools(request.tools)

        # Tool choice
        if request.tool_choice:
            self._apply_tool_choice(kwargs, request.tool_choice)

        # Generation params
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop_sequences:
            kwargs["stop_sequences"] = request.stop_sequences

        # Provider options escape hatch
        if request.provider_options and "anthropic" in request.provider_options:
            opts = request.provider_options["anthropic"]
            if "extra_headers" in opts:
                kwargs["extra_headers"] = opts["extra_headers"]
            for k, v in opts.items():
                if k not in ("extra_headers", "auto_cache"):
                    kwargs[k] = v

        # Task 27: Prompt caching — inject cache_control breakpoints
        auto_cache = True
        if request.provider_options and "anthropic" in request.provider_options:
            auto_cache = request.provider_options["anthropic"].get("auto_cache", True)

        if auto_cache:
            self._inject_cache_control(kwargs)

        return kwargs

    def _translate_user_content(self, parts: list[ContentPart]) -> list[dict[str, Any]]:
        """Translate user content parts to Anthropic format."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                result.append({"type": "text", "text": part.text})
            elif part.kind == ContentKind.IMAGE and part.image:
                if part.image.url:
                    result.append(
                        {
                            "type": "image",
                            "source": {"type": "url", "url": part.image.url},
                        }
                    )
                elif part.image.data:
                    result.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": part.image.media_type or "image/png",
                                "data": base64.b64encode(part.image.data).decode(),
                            },
                        }
                    )
        return result

    def _translate_assistant_content(
        self, parts: list[ContentPart]
    ) -> list[dict[str, Any]]:
        """Translate assistant content parts (text, tool_use, thinking)."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                result.append({"type": "text", "text": part.text})
            elif part.kind == ContentKind.TOOL_CALL and part.tool_call:
                tc = part.tool_call
                input_val = (
                    tc.arguments
                    if isinstance(tc.arguments, dict)
                    else json.loads(tc.arguments)
                )
                result.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": input_val,
                    }
                )
            elif part.kind == ContentKind.THINKING and part.thinking:
                result.append(
                    {
                        "type": "thinking",
                        "thinking": part.thinking.text,
                        "signature": part.thinking.signature or "",
                    }
                )
            elif part.kind == ContentKind.REDACTED_THINKING and part.thinking:
                result.append(
                    {
                        "type": "redacted_thinking",
                        "data": part.thinking.text,
                    }
                )
        return result

    def _translate_tool_result_content(
        self, parts: list[ContentPart]
    ) -> list[dict[str, Any]]:
        """Translate tool result content to Anthropic's tool_result format."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TOOL_RESULT and part.tool_result:
                tr = part.tool_result
                content = (
                    tr.content
                    if isinstance(tr.content, str)
                    else json.dumps(tr.content)
                )
                result.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": content,
                        "is_error": tr.is_error,
                    }
                )
        return result

    def _translate_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Translate Tool definitions to Anthropic format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
            if isinstance(tool, Tool)
        ]

    def _apply_tool_choice(
        self, kwargs: dict[str, Any], tool_choice: ToolChoice
    ) -> None:
        """Map unified ToolChoice to Anthropic's tool_choice format."""
        if tool_choice.mode == "auto":
            kwargs["tool_choice"] = {"type": "auto"}
        elif tool_choice.mode == "none":
            # Anthropic: omit tools from request entirely for "none"
            kwargs.pop("tools", None)
        elif tool_choice.mode == "required":
            kwargs["tool_choice"] = {"type": "any"}
        elif tool_choice.mode == "named" and tool_choice.tool_name:
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice.tool_name}

    def _merge_consecutive_roles(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge consecutive same-role messages (Anthropic requires alternation)."""
        if not messages:
            return messages
        merged: list[dict[str, Any]] = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"]:
                merged[-1]["content"].extend(msg["content"])
            else:
                merged.append(msg)
        return merged

    # ------------------------------------------------------------------
    # Task 27: Prompt Caching
    # ------------------------------------------------------------------

    def _inject_cache_control(self, kwargs: dict[str, Any]) -> None:
        """Auto-inject cache_control breakpoints and beta header.

        Adds cache_control: {"type": "ephemeral"} to:
        - The last system message content block
        - The last tool definition (when present)

        Also adds the prompt-caching-2024-07-31 beta header.
        """
        has_cacheable = False

        # Inject on last system content block
        system_parts = kwargs.get("system")
        if system_parts and isinstance(system_parts, list) and len(system_parts) > 0:
            system_parts[-1]["cache_control"] = {"type": "ephemeral"}
            has_cacheable = True

        # Inject on last tool definition
        tools = kwargs.get("tools")
        if tools and isinstance(tools, list) and len(tools) > 0:
            tools[-1]["cache_control"] = {"type": "ephemeral"}
            has_cacheable = True

        # Add beta header if we injected any cache_control
        if has_cacheable:
            extra_headers = kwargs.get("extra_headers", {})
            existing_beta = extra_headers.get("anthropic-beta", "")
            cache_beta = "prompt-caching-2024-07-31"
            if cache_beta not in existing_beta:
                if existing_beta:
                    extra_headers["anthropic-beta"] = f"{existing_beta},{cache_beta}"
                else:
                    extra_headers["anthropic-beta"] = cache_beta
            kwargs["extra_headers"] = extra_headers

    # ------------------------------------------------------------------
    # Task 23: Response Translation
    # ------------------------------------------------------------------

    def _translate_response(self, raw: Any) -> Response:
        """Convert Anthropic Message → unified Response."""
        content_parts: list[ContentPart] = []

        for block in raw.content:
            if block.type == "text":
                content_parts.append(
                    ContentPart(kind=ContentKind.TEXT, text=block.text)
                )
            elif block.type == "tool_use":
                content_parts.append(
                    ContentPart(
                        kind=ContentKind.TOOL_CALL,
                        tool_call=ToolCallData(
                            id=block.id,
                            name=block.name,
                            arguments=block.input,
                            type="function",
                        ),
                    )
                )
            elif block.type == "thinking":
                content_parts.append(
                    ContentPart(
                        kind=ContentKind.THINKING,
                        thinking=ThinkingData(
                            text=block.thinking,
                            signature=getattr(block, "signature", None),
                        ),
                    )
                )
            elif block.type == "redacted_thinking":
                content_parts.append(
                    ContentPart(
                        kind=ContentKind.REDACTED_THINKING,
                        thinking=ThinkingData(
                            text=getattr(block, "data", ""),
                            redacted=True,
                        ),
                    )
                )

        return Response(
            id=raw.id,
            model=raw.model,
            provider="anthropic",
            message=Message(role=Role.ASSISTANT, content=content_parts),
            finish_reason=self._map_finish_reason(raw.stop_reason),
            usage=self._map_usage(raw.usage),
        )

    def _map_finish_reason(self, stop_reason: str | None) -> FinishReason:
        """Map Anthropic stop_reason to unified FinishReason."""
        mapping = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        reason = mapping.get(stop_reason or "", "other")
        return FinishReason(reason=reason, raw=stop_reason)

    def _map_usage(self, usage: Any) -> Usage:
        """Map Anthropic usage to unified Usage."""
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        cache_read = getattr(usage, "cache_read_input_tokens", None)
        cache_write = getattr(usage, "cache_creation_input_tokens", None)

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    # ------------------------------------------------------------------
    # Task 24: Error Translation
    # ------------------------------------------------------------------

    def _translate_error(self, error: Exception) -> errors.SDKError:
        """Map Anthropic SDK exception → unified error hierarchy."""
        if isinstance(error, anthropic.APITimeoutError):
            return errors.RequestTimeoutError(str(error), cause=error)

        if isinstance(error, anthropic.APIConnectionError):
            return errors.NetworkError(str(error), cause=error)

        if isinstance(error, anthropic.APIStatusError):
            status_code = error.status_code
            message = str(error)

            # Extract body / error code
            body = getattr(error, "body", None)
            raw = body if isinstance(body, dict) else None
            error_code = None
            if isinstance(body, dict) and "error" in body:
                error_code = body["error"].get("type")

            # Extract Retry-After header
            retry_after: float | None = None
            response = getattr(error, "response", None)
            if response is not None and hasattr(response, "headers"):
                ra = response.headers.get("retry-after")
                if ra:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        pass

            return errors.error_from_status_code(
                status_code=status_code,
                message=message,
                provider="anthropic",
                error_code=error_code,
                raw=raw,
                retry_after=retry_after,
                cause=error,
            )

        # Generic fallback
        return errors.ProviderError(
            message=str(error),
            provider="anthropic",
            retryable=True,
            cause=error,
        )
