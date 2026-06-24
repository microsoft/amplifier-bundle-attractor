"""Gemini API adapter (Spec §7.3-7.8).

Wraps the google-genai SDK's Client to implement the
ProviderAdapter interface with request/response/error/stream translation.

Uses the NEW google-genai package (google.genai.Client), NOT the old
google-generativeai package.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from google import genai

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
    Tool,
    ToolCall,
    ToolCallData,
    ToolChoice,
    Usage,
)


class GeminiAdapter:
    """Gemini API adapter."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client = genai.Client(**kwargs)
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "gemini"

    # ------------------------------------------------------------------
    # Task 37: complete() Integration
    # ------------------------------------------------------------------

    async def complete(self, request: Request) -> Response:
        """Send a request, block until done, return full Response."""
        try:
            kwargs = self._translate_request(request)
            raw = await self._client.aio.models.generate_content(
                model=request.model, **kwargs
            )
            return self._translate_response(raw, model=request.model)
        except Exception as e:
            raise self._translate_error(e) from e

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Send a request, return async iterator of StreamEvent."""
        kwargs = self._translate_request(request)

        try:
            raw_stream = await self._client.aio.models.generate_content_stream(
                model=request.model, **kwargs
            )
        except Exception as e:
            raise self._translate_error(e) from e

        text_started = False
        has_tool_calls = False
        last_usage: Usage | None = None
        last_finish_reason: str | None = None

        try:
            async for chunk in raw_stream:
                candidates = getattr(chunk, "candidates", None)
                if not candidates:
                    continue
                candidate = candidates[0]
                if candidate is None:
                    continue

                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) if content else None
                if not parts:
                    continue

                for part in parts:
                    # Text part
                    text_val = getattr(part, "text", None)
                    if text_val:
                        if not text_started:
                            yield StreamEvent(type=StreamEventType.TEXT_START)
                            text_started = True
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA, delta=text_val
                        )

                    # Function call part (complete in one chunk per spec)
                    fc = getattr(part, "function_call", None)
                    if fc is not None:
                        has_tool_calls = True
                        synthetic_id = f"call_{uuid.uuid4().hex[:12]}"
                        args = dict(fc.args) if fc.args else {}
                        tc = ToolCall(
                            id=synthetic_id,
                            name=fc.name,
                            arguments=args,
                        )
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call=ToolCall(
                                id=synthetic_id,
                                name=fc.name,
                                arguments={},
                            ),
                        )
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_END,
                            tool_call=tc,
                        )

                # Track finish reason and usage from each chunk
                chunk_finish = getattr(candidate, "finish_reason", None)
                if chunk_finish:
                    last_finish_reason = str(chunk_finish)

                usage_meta = getattr(chunk, "usage_metadata", None)
                if usage_meta:
                    last_usage = self._map_usage(usage_meta)

            # Emit TEXT_END if we had text
            if text_started:
                yield StreamEvent(type=StreamEventType.TEXT_END)

            # Determine finish reason
            if has_tool_calls:
                finish_reason = FinishReason(
                    reason="tool_calls", raw=last_finish_reason
                )
            elif last_finish_reason:
                finish_reason = self._map_finish_reason(last_finish_reason)
            else:
                finish_reason = FinishReason(reason="other")

            usage = last_usage or Usage(input_tokens=0, output_tokens=0, total_tokens=0)

            yield StreamEvent(
                type=StreamEventType.FINISH,
                finish_reason=finish_reason,
                usage=usage,
                response=Response(
                    id="",
                    model=request.model,
                    provider="gemini",
                    message=Message(role=Role.ASSISTANT, content=[]),
                    finish_reason=finish_reason,
                    usage=usage,
                ),
            )
        except Exception as e:
            raise self._translate_error(e) from e

    async def close(self) -> None:
        """Release resources."""

    async def initialize(self) -> None:
        """Validate configuration on startup."""

    async def list_models(self) -> list[str]:
        """Return the live list of model ids served by this Gemini client.

        Uses the same ``self._client`` (``genai.Client``) instance used for
        ``complete()`` and ``stream()``, so the returned ids are in the same
        namespace as what the adapter passes to
        ``aio.models.generate_content(model=...)``.  This is the foundation
        of the id-seam guarantee: the lister IS the generator.

        The google-genai SDK returns model names as ``"models/<id>"``
        (e.g. ``"models/gemini-2.0-flash"``).  We strip the ``"models/"``
        prefix to match the short form accepted by ``generate_content``.
        """
        ids: list[str] = []
        pager = await self._client.aio.models.list()
        async for model in pager:
            name: str = getattr(model, "name", "") or ""
            if name.startswith("models/"):
                name = name[len("models/") :]
            if name:
                ids.append(name)
        return ids

    def supports_tool_choice(self, mode: str) -> bool:
        """Check if a particular tool choice mode is supported."""
        return mode in ("auto", "none", "required")

    # ------------------------------------------------------------------
    # Task 34: Request Translation
    # ------------------------------------------------------------------

    def _translate_request(self, request: Request) -> dict[str, Any]:
        """Convert unified Request → Gemini API kwargs."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        # Build mapping from tool_call_id → function name for tool results
        call_id_to_name: dict[str, str] = {}
        for msg in request.messages:
            if msg.role == Role.ASSISTANT:
                for part in msg.content:
                    if part.kind == ContentKind.TOOL_CALL and part.tool_call:
                        call_id_to_name[part.tool_call.id] = part.tool_call.name

        for msg in request.messages:
            if msg.role in (Role.SYSTEM, Role.DEVELOPER):
                # Extract to systemInstruction
                for part in msg.content:
                    if part.kind == ContentKind.TEXT and part.text:
                        system_parts.append(part.text)
            elif msg.role == Role.USER:
                parts = self._translate_content_parts(msg.content)
                contents.append({"role": "user", "parts": parts})
            elif msg.role == Role.ASSISTANT:
                parts = self._translate_model_parts(msg.content)
                contents.append({"role": "model", "parts": parts})
            elif msg.role == Role.TOOL:
                parts = self._translate_tool_result_parts(msg.content, call_id_to_name)
                contents.append({"role": "user", "parts": parts})

        kwargs: dict[str, Any] = {
            "contents": contents,
        }

        # Tools
        if request.tools:
            kwargs["tools"] = self._translate_tools(request.tools)

        # Generation config
        config: dict[str, Any] = {}

        if system_parts:
            config["system_instruction"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.top_p is not None:
            config["top_p"] = request.top_p
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        if request.stop_sequences:
            config["stop_sequences"] = request.stop_sequences

        # Tool choice
        if request.tool_choice:
            tc_config = self._translate_tool_choice(request.tool_choice)
            if tc_config:
                config["tool_config"] = tc_config

        if config:
            kwargs["config"] = config

        # Provider options escape hatch
        if request.provider_options and "gemini" in request.provider_options:
            opts = request.provider_options["gemini"]
            for k, v in opts.items():
                kwargs[k] = v

        return kwargs

    def _translate_content_parts(
        self, parts: list[ContentPart]
    ) -> list[dict[str, Any]]:
        """Translate user content parts to Gemini format."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                result.append({"text": part.text})
            elif part.kind == ContentKind.IMAGE and part.image:
                if part.image.url:
                    result.append(
                        {
                            "file_data": {
                                "mime_type": part.image.media_type or "image/png",
                                "file_uri": part.image.url,
                            }
                        }
                    )
                elif part.image.data:
                    result.append(
                        {
                            "inline_data": {
                                "mime_type": part.image.media_type or "image/png",
                                "data": base64.b64encode(part.image.data).decode(),
                            }
                        }
                    )
        return result

    def _translate_model_parts(self, parts: list[ContentPart]) -> list[dict[str, Any]]:
        """Translate assistant/model content parts."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                result.append({"text": part.text})
            elif part.kind == ContentKind.TOOL_CALL and part.tool_call:
                tc = part.tool_call
                args = (
                    tc.arguments
                    if isinstance(tc.arguments, dict)
                    else json.loads(tc.arguments)
                )
                result.append(
                    {
                        "function_call": {
                            "name": tc.name,
                            "args": args,
                        }
                    }
                )
        return result

    def _translate_tool_result_parts(
        self,
        parts: list[ContentPart],
        call_id_to_name: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Translate tool result content to Gemini's functionResponse format.

        Uses function NAME (not call ID) per spec §7.3.
        Wraps string results in {"result": "..."}.
        """
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TOOL_RESULT and part.tool_result:
                tr = part.tool_result
                func_name = call_id_to_name.get(tr.tool_call_id, tr.tool_call_id)

                # Wrap string content in dict
                if isinstance(tr.content, str):
                    response_data: dict[str, Any] = {"result": tr.content}
                else:
                    response_data = (
                        tr.content
                        if isinstance(tr.content, dict)
                        else {"result": str(tr.content)}
                    )

                result.append(
                    {
                        "function_response": {
                            "name": func_name,
                            "response": response_data,
                        }
                    }
                )
        return result

    def _translate_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Translate Tool definitions to Gemini functionDeclarations format."""
        declarations = []
        for tool in tools:
            if isinstance(tool, Tool):
                declarations.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                )
        return [{"function_declarations": declarations}]

    # ------------------------------------------------------------------
    # Task 35: Response Translation
    # ------------------------------------------------------------------

    def _translate_response(self, raw: Any, *, model: str) -> Response:
        """Convert Gemini generateContent response → unified Response."""
        content_parts: list[ContentPart] = []
        has_tool_calls = False

        candidate = raw.candidates[0] if raw.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                # Text part
                text_val = getattr(part, "text", None)
                if text_val:
                    content_parts.append(
                        ContentPart(kind=ContentKind.TEXT, text=text_val)
                    )

                # Function call part
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    has_tool_calls = True
                    synthetic_id = f"call_{uuid.uuid4().hex[:12]}"
                    content_parts.append(
                        ContentPart(
                            kind=ContentKind.TOOL_CALL,
                            tool_call=ToolCallData(
                                id=synthetic_id,
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {},
                                type="function",
                            ),
                        )
                    )

        # Finish reason
        raw_finish = (
            getattr(candidate, "finish_reason", "STOP") if candidate else "STOP"
        )
        if has_tool_calls:
            finish_reason = FinishReason(reason="tool_calls", raw=str(raw_finish))
        else:
            finish_reason = self._map_finish_reason(str(raw_finish))

        # Usage
        usage = self._map_usage(getattr(raw, "usage_metadata", None))

        # Response ID — Gemini doesn't provide one, use empty string
        model_version = getattr(raw, "model_version", model)

        return Response(
            id="",
            model=model_version or model,
            provider="gemini",
            message=Message(role=Role.ASSISTANT, content=content_parts),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _map_finish_reason(self, finish_reason: str) -> FinishReason:
        """Map Gemini finish reason to unified FinishReason.

        The google-genai SDK returns enum values whose str() is e.g.
        ``"FinishReason.STOP"`` while ``.value`` is ``"STOP"``.
        We normalise by stripping any ``"FinishReason."`` prefix so the
        lookup works regardless of SDK representation.
        """
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        # Normalise: "FinishReason.STOP" -> "STOP"
        key = finish_reason
        if "." in key:
            key = key.rsplit(".", 1)[-1]
        reason = mapping.get(key, "other")
        return FinishReason(reason=reason, raw=finish_reason)

    def _map_usage(self, usage_metadata: Any) -> Usage:
        """Map Gemini usageMetadata to unified Usage."""
        if usage_metadata is None:
            return Usage(input_tokens=0, output_tokens=0, total_tokens=0)

        input_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
        total_tokens = getattr(usage_metadata, "total_token_count", None)
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens

        # Reasoning tokens from thoughtsTokenCount
        reasoning_tokens: int | None = getattr(
            usage_metadata, "thoughts_token_count", None
        )

        # Cache tokens from cachedContentTokenCount
        cache_read_tokens: int | None = getattr(
            usage_metadata, "cached_content_token_count", None
        )

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
        )

    # ------------------------------------------------------------------
    # Task 36: Error Translation (including gRPC)
    # ------------------------------------------------------------------

    # gRPC status → unified error type (Spec §6.4)
    _GRPC_STATUS_MAP: dict[str, type[errors.ProviderError]] = {
        "NOT_FOUND": errors.NotFoundError,
        "INVALID_ARGUMENT": errors.InvalidRequestError,
        "UNAUTHENTICATED": errors.AuthenticationError,
        "PERMISSION_DENIED": errors.AccessDeniedError,
        "RESOURCE_EXHAUSTED": errors.RateLimitError,
        "UNAVAILABLE": errors.ServerError,
        "INTERNAL": errors.ServerError,
    }

    def _translate_error(self, error: Exception) -> errors.SDKError:
        """Map google-genai SDK exception → unified error hierarchy."""
        from google.genai import errors as genai_errors

        if isinstance(error, genai_errors.APIError):
            status_code = getattr(error, "code", 0) or 0
            message = str(error)
            grpc_status = getattr(error, "status", None)

            # Handle timeout separately (different constructor signature)
            if grpc_status == "DEADLINE_EXCEEDED":
                return errors.RequestTimeoutError(message, cause=error)

            # Try gRPC status mapping
            if grpc_status and grpc_status in self._GRPC_STATUS_MAP:
                cls = self._GRPC_STATUS_MAP[grpc_status]
                return cls(
                    message=message,
                    provider="gemini",
                    status_code=status_code,
                    error_code=grpc_status,
                    cause=error,
                )

            # Fall back to HTTP status code mapping
            return errors.error_from_status_code(
                status_code=status_code,
                message=message,
                provider="gemini",
                error_code=grpc_status,
                cause=error,
            )

        # Generic fallback
        return errors.ProviderError(
            message=str(error),
            provider="gemini",
            retryable=True,
            cause=error,
        )

    def _translate_tool_choice(self, tool_choice: ToolChoice) -> dict[str, Any] | None:
        """Map unified ToolChoice to Gemini's tool_config format."""
        mode_map = {
            "auto": "AUTO",
            "none": "NONE",
            "required": "ANY",
        }
        mode = mode_map.get(tool_choice.mode)
        if mode is None:
            return None
        return {
            "function_calling_config": {
                "mode": mode,
            }
        }
