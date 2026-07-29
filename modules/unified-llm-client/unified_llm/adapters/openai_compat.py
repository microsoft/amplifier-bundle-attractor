"""OpenAI-Compatible Chat Completions adapter (Spec §7.10).

For third-party services (vLLM, Ollama, Together AI, Groq) that expose
an OpenAI-compatible Chat Completions API (/v1/chat/completions).

CRITICAL: This adapter uses the Chat Completions API, NOT the Responses API.
It does NOT support reasoning tokens or built-in tools (Responses API features).
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import openai

from collections.abc import AsyncIterator

from unified_llm import errors
from unified_llm.adapters._openai_strict_schema import (
    make_openai_strict_schema as _make_strict_schema,
)
from unified_llm.types import (
    ContentKind,
    ContentPart,
    FinishReason,
    Message,
    RateLimitInfo,
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


def _serialize_raw(obj: Any) -> dict[str, Any] | None:
    """Defensively serialize a provider SDK response to a JSON-serializable dict.

    Tries, in order:
    1. Already a dict — return as-is.
    2. Pydantic model_dump() — OpenAI SDK objects are pydantic v2 BaseModel.
    3. to_dict() — fallback for other SDK styles.
    4. vars() — for SimpleNamespace and plain objects.
    5. Fallback sentinel {"_unserializable": repr(obj)}.

    Returns None only if *obj* is None.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            result = model_dump()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    try:
        d = vars(obj)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"_unserializable": repr(obj)}


def _parse_ratelimit_headers(headers: Any) -> RateLimitInfo | None:
    """Parse x-ratelimit-* HTTP headers into a RateLimitInfo.

    Third-party OpenAI-compatible providers (Groq, Together AI, etc.) often
    mirror the same header names as OpenAI.  Returns None when no recognised
    header is present, so callers can skip setting Response.rate_limit.
    """

    def _int(key: str) -> int | None:
        v = headers.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def _reset_dt(key: str) -> datetime | None:
        v = headers.get(key)
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
        m = re.fullmatch(
            r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?",
            v.strip(),
        )
        if m and any(g is not None for g in m.groups()):
            hours = int(m.group(1) or 0)
            minutes = int(m.group(2) or 0)
            seconds = float(m.group(3) or 0)
            millis = int(m.group(4) or 0)
            delta = timedelta(
                hours=hours, minutes=minutes, seconds=seconds, milliseconds=millis
            )
            return datetime.now(timezone.utc) + delta
        return None

    limit_req = _int("x-ratelimit-limit-requests")
    remaining_req = _int("x-ratelimit-remaining-requests")
    limit_tok = _int("x-ratelimit-limit-tokens")
    remaining_tok = _int("x-ratelimit-remaining-tokens")
    reset_at = _reset_dt("x-ratelimit-reset-requests")

    if all(
        v is None
        for v in [limit_req, remaining_req, limit_tok, remaining_tok, reset_at]
    ):
        return None

    return RateLimitInfo(
        requests_limit=limit_req,
        requests_remaining=remaining_req,
        tokens_limit=limit_tok,
        tokens_remaining=remaining_tok,
        reset_at=reset_at,
    )


class OpenAICompatAdapter:
    """OpenAI-compatible Chat Completions adapter."""

    def __init__(
        self,
        *,
        name: str = "openai_compat",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Create an adapter for one OpenAI-compatible endpoint.

        Args:
            name: The registry name this adapter is registered under. It is
                NOT cosmetic: it is echoed in ``Response.provider`` and is the
                key consulted in ``Request.provider_options``. Registering the
                adapter as "ollama" while it reports "openai_compat" would make
                ``Response.provider`` lie in every audit record, and would
                cause ``provider_options={"ollama": ...}`` to be silently
                dropped. One endpoint, one name, everywhere.
            api_key: Endpoint credential. Local servers ignore it, but the
                OpenAI SDK expects a string.
            base_url: The endpoint's OpenAI-compatible base URL.
            timeout: Optional request timeout in seconds.
        """
        self._name = name
        kwargs: dict[str, Any] = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = openai.AsyncOpenAI(**kwargs)

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------------
    # complete()
    # ------------------------------------------------------------------

    async def complete(self, request: Request) -> Response:
        """Send a request, block until done, return full Response."""
        try:
            kwargs = self._translate_request(request)
            # ULM-5/ULM-6: use with_raw_response to access HTTP headers for
            # rate-limit info while still getting the parsed SDK object.
            raw_http = await self._client.chat.completions.with_raw_response.create(
                **kwargs
            )
            raw = raw_http.parse()
            headers = raw_http.headers
            response = self._translate_response(raw)
            response.raw = _serialize_raw(raw)
            rate_limit = _parse_ratelimit_headers(headers)
            if rate_limit is not None:
                response.rate_limit = rate_limit
            return response
        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

    # ------------------------------------------------------------------
    # stream()
    # ------------------------------------------------------------------

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Send a request, return async iterator of StreamEvent."""
        kwargs = self._translate_request(request)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        try:
            raw_stream = await self._client.chat.completions.create(**kwargs)
        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

        model = request.model
        response_id = ""
        text_started = False

        # Per-tool-call state: index -> (id, name, args_parts)
        tool_calls_state: dict[int, tuple[str, str, list[str]]] = {}
        tool_calls_started: set[int] = set()

        try:
            first_chunk = True
            async for chunk in raw_stream:
                chunk_id = getattr(chunk, "id", "") or ""
                if chunk_id:
                    response_id = chunk_id

                if first_chunk:
                    yield StreamEvent(type=StreamEventType.STREAM_START)
                    first_chunk = False

                choices = getattr(chunk, "choices", []) or []
                if not choices:
                    # Final chunk with usage only
                    usage_obj = getattr(chunk, "usage", None)
                    if usage_obj is not None:
                        if text_started:
                            yield StreamEvent(type=StreamEventType.TEXT_END)
                            text_started = False
                        # Emit any pending tool call ends
                        for idx in list(tool_calls_started):
                            tc_id, tc_name, tc_parts = tool_calls_state[idx]
                            args_str = "".join(tc_parts)
                            try:
                                args = json.loads(args_str) if args_str else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_END,
                                tool_call=ToolCall(
                                    id=tc_id,
                                    name=tc_name,
                                    arguments=args,
                                    raw_arguments=args_str or None,
                                ),
                            )
                            tool_calls_started.discard(idx)

                        usage = self._extract_usage_from_obj(usage_obj)
                        finish_reason = FinishReason(reason="stop")
                        # Use last known finish reason if we have tool calls
                        if tool_calls_state:
                            finish_reason = FinishReason(reason="tool_calls")
                        yield StreamEvent(
                            type=StreamEventType.FINISH,
                            finish_reason=finish_reason,
                            usage=usage,
                            response=Response(
                                id=response_id,
                                model=model,
                                provider=self._name,
                                message=Message(role=Role.ASSISTANT, content=[]),
                                finish_reason=finish_reason,
                                usage=usage,
                            ),
                        )
                    continue

                choice = choices[0]
                delta = getattr(choice, "delta", None)
                chunk_finish = getattr(choice, "finish_reason", None)

                if delta:
                    # Text content
                    content = getattr(delta, "content", None)
                    if content:
                        if not text_started:
                            yield StreamEvent(type=StreamEventType.TEXT_START)
                            text_started = True
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA, delta=content
                        )

                    # Tool calls
                    delta_tool_calls = getattr(delta, "tool_calls", None)
                    if delta_tool_calls:
                        for tc_delta in delta_tool_calls:
                            idx = getattr(tc_delta, "index", 0)
                            tc_id = getattr(tc_delta, "id", None)
                            tc_func = getattr(tc_delta, "function", None)
                            tc_name = (
                                getattr(tc_func, "name", None) if tc_func else None
                            )
                            tc_args = (
                                getattr(tc_func, "arguments", None) if tc_func else None
                            )

                            if idx not in tool_calls_state:
                                tool_calls_state[idx] = (
                                    tc_id or "",
                                    tc_name or "",
                                    [],
                                )

                            if idx not in tool_calls_started:
                                tool_calls_started.add(idx)
                                stored_id, stored_name, _ = tool_calls_state[idx]
                                yield StreamEvent(
                                    type=StreamEventType.TOOL_CALL_START,
                                    tool_call=ToolCall(
                                        id=tc_id or stored_id,
                                        name=tc_name or stored_name,
                                        arguments={},
                                    ),
                                )

                            # Update state
                            stored_id, stored_name, parts = tool_calls_state[idx]
                            if tc_id:
                                stored_id = tc_id
                            if tc_name:
                                stored_name = tc_name
                            if tc_args:
                                parts.append(tc_args)
                                yield StreamEvent(
                                    type=StreamEventType.TOOL_CALL_DELTA,
                                    delta=tc_args,
                                )
                            tool_calls_state[idx] = (stored_id, stored_name, parts)

                if chunk_finish:
                    if text_started:
                        yield StreamEvent(type=StreamEventType.TEXT_END)
                        text_started = False

                    # Emit tool call ends
                    for idx in list(tool_calls_started):
                        tc_id, tc_name, tc_parts = tool_calls_state[idx]
                        args_str = "".join(tc_parts)
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_END,
                            tool_call=ToolCall(
                                id=tc_id,
                                name=tc_name,
                                arguments=args,
                                raw_arguments=args_str or None,
                            ),
                        )
                        tool_calls_started.discard(idx)

                    # Extract usage from chunk if available
                    chunk_usage_obj = getattr(chunk, "usage", None)
                    if chunk_usage_obj:
                        usage = self._extract_usage_from_obj(chunk_usage_obj)
                    else:
                        usage = Usage(input_tokens=0, output_tokens=0, total_tokens=0)

                    finish_reason = self._map_finish_reason(chunk_finish)
                    yield StreamEvent(
                        type=StreamEventType.FINISH,
                        finish_reason=finish_reason,
                        usage=usage,
                        response=Response(
                            id=response_id,
                            model=model,
                            provider=self._name,
                            message=Message(role=Role.ASSISTANT, content=[]),
                            finish_reason=finish_reason,
                            usage=usage,
                        ),
                    )

        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

    async def close(self) -> None:
        """Release resources."""
        await self._client.close()

    async def initialize(self) -> None:
        """Validate configuration on startup."""

    def supports_tool_choice(self, mode: str) -> bool:
        """Check if a particular tool choice mode is supported."""
        return mode in ("auto", "none", "required", "named")

    # ------------------------------------------------------------------
    # Request Translation (Chat Completions format)
    # ------------------------------------------------------------------

    def _translate_request(self, request: Request) -> dict[str, Any]:
        """Convert unified Request → Chat Completions API kwargs."""
        messages: list[dict[str, Any]] = []

        for msg in request.messages:
            messages.append(self._translate_message(msg))

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
        }

        # Tools
        if request.tools:
            kwargs["tools"] = self._translate_tools(request.tools)

        # Tool choice
        if request.tool_choice:
            kwargs["tool_choice"] = self._translate_tool_choice(request.tool_choice)

        # Generation params
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        # Response format
        if request.response_format:
            kwargs["response_format"] = self._translate_response_format(
                request.response_format
            )

        # NOTE: reasoning_effort is NOT passed through — Chat Completions limitation

        # Provider options escape hatch
        if request.provider_options and self._name in request.provider_options:
            opts = request.provider_options[self._name]
            for k, v in opts.items():
                kwargs[k] = v

        return kwargs

    def _translate_message(self, msg: Message) -> dict[str, Any]:
        """Translate a single Message to Chat Completions format."""
        role = self._map_role(msg.role)

        # Check for tool call content (assistant with tool calls)
        tool_call_parts = [
            p for p in msg.content if p.kind == ContentKind.TOOL_CALL and p.tool_call
        ]
        # Check for tool result content
        tool_result_parts = [
            p
            for p in msg.content
            if p.kind == ContentKind.TOOL_RESULT and p.tool_result
        ]

        if tool_result_parts:
            # Tool result message
            tr = tool_result_parts[0].tool_result
            assert tr is not None
            content = (
                tr.content if isinstance(tr.content, str) else json.dumps(tr.content)
            )
            return {
                "role": "tool",
                "tool_call_id": tr.tool_call_id,
                "content": content,
            }

        if tool_call_parts and role == "assistant":
            # Assistant message with tool calls
            result: dict[str, Any] = {"role": "assistant"}
            # Text content (may be None)
            text_parts = [
                p.text for p in msg.content if p.kind == ContentKind.TEXT and p.text
            ]
            result["content"] = "\n".join(text_parts) if text_parts else None
            result["tool_calls"] = []
            for p in tool_call_parts:
                tc = p.tool_call
                assert tc is not None
                args = (
                    json.dumps(tc.arguments)
                    if isinstance(tc.arguments, dict)
                    else tc.arguments
                )
                result["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": args},
                    }
                )
            return result

        # Check if we need multipart content (images, etc.)
        has_non_text = any(p.kind != ContentKind.TEXT for p in msg.content)

        if has_non_text:
            content_parts: list[dict[str, Any]] = []
            for part in msg.content:
                if part.kind == ContentKind.TEXT and part.text is not None:
                    content_parts.append({"type": "text", "text": part.text})
                elif part.kind == ContentKind.IMAGE and part.image:
                    if part.image.url:
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": part.image.url},
                            }
                        )
                    elif part.image.data:
                        media_type = part.image.media_type or "image/png"
                        b64 = base64.b64encode(part.image.data).decode()
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{b64}",
                                },
                            }
                        )
                else:
                    # ULM-10: No silent drops — fail loud for unsupported content kinds.
                    raise errors.ConfigurationError(
                        f"OpenAI-compat adapter: unsupported content kind "
                        f"{part.kind!r} in message. Only TEXT and IMAGE parts are "
                        "supported. AUDIO and DOCUMENT parts are not yet implemented."
                    )
            return {"role": role, "content": content_parts}

        # Simple text message
        text = msg.text
        return {"role": role, "content": text}

    def _map_role(self, role: Role) -> str:
        """Map unified Role to Chat Completions role string."""
        mapping = {
            Role.SYSTEM: "system",
            Role.USER: "user",
            Role.ASSISTANT: "assistant",
            Role.TOOL: "tool",
            Role.DEVELOPER: "system",  # Developer → system in Chat Completions
        }
        return mapping.get(role, "user")

    def _translate_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Translate Tool definitions to Chat Completions format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
            if isinstance(tool, Tool)
        ]

    def _translate_tool_choice(self, tool_choice: ToolChoice) -> Any:
        """Map unified ToolChoice to Chat Completions format."""
        if tool_choice.mode == "auto":
            return "auto"
        elif tool_choice.mode == "none":
            return "none"
        elif tool_choice.mode == "required":
            return "required"
        elif tool_choice.mode == "named" and tool_choice.tool_name:
            return {"type": "function", "function": {"name": tool_choice.tool_name}}
        return "auto"

    def _translate_response_format(self, fmt: Any) -> dict[str, Any]:
        """Translate ResponseFormat to Chat Completions format."""
        if fmt.type == "json_schema" and fmt.json_schema:
            if fmt.strict:
                # ULM-16: OpenAI-compatible endpoints that enforce strict mode
                # have the same schema requirements as the native OpenAI API:
                # every object must have additionalProperties:false and
                # required=[all property keys].  Transform a deep copy of the
                # user's schema; originally-optional fields become nullable so
                # the model can return null instead of omitting the key.
                schema = _make_strict_schema(fmt.json_schema)
            else:
                schema = fmt.json_schema
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": schema,
                    "strict": fmt.strict,
                },
            }
        elif fmt.type == "json":
            return {"type": "json_object"}
        return {"type": "text"}

    # ------------------------------------------------------------------
    # Response Translation
    # ------------------------------------------------------------------

    def _translate_response(self, raw: Any) -> Response:
        """Convert Chat Completions response → unified Response."""
        choice = raw.choices[0] if raw.choices else None
        content_parts: list[ContentPart] = []

        if choice:
            msg = choice.message
            # Text content
            text = getattr(msg, "content", None)
            if text:
                content_parts.append(ContentPart(kind=ContentKind.TEXT, text=text))
            # Tool calls
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    fc = tc.function
                    args_str = getattr(fc, "arguments", "")
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {}
                    content_parts.append(
                        ContentPart(
                            kind=ContentKind.TOOL_CALL,
                            tool_call=ToolCallData(
                                id=tc.id,
                                name=fc.name,
                                arguments=args,
                                type="function",
                            ),
                        )
                    )

        finish_reason = self._map_finish_reason(
            getattr(choice, "finish_reason", None) if choice else None
        )
        usage = self._extract_usage(raw)

        return Response(
            id=getattr(raw, "id", ""),
            model=getattr(raw, "model", ""),
            provider=self._name,
            message=Message(role=Role.ASSISTANT, content=content_parts),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _map_finish_reason(self, reason: str | None) -> FinishReason:
        """Map Chat Completions finish_reason to unified FinishReason."""
        mapping = {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_calls",
            "content_filter": "content_filter",
        }
        unified = mapping.get(reason or "", "other")
        return FinishReason(reason=unified, raw=reason)

    def _extract_usage(self, raw: Any) -> Usage:
        """Extract usage from Chat Completions response."""
        usage_obj = getattr(raw, "usage", None)
        if usage_obj is None:
            return Usage(input_tokens=0, output_tokens=0, total_tokens=0)
        return self._extract_usage_from_obj(usage_obj)

    def _extract_usage_from_obj(self, usage_obj: Any) -> Usage:
        """Extract usage from a usage object."""
        input_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage_obj, "completion_tokens", 0) or 0
        total_tokens = getattr(usage_obj, "total_tokens", None)
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    # ------------------------------------------------------------------
    # Error Translation
    # ------------------------------------------------------------------

    def _translate_error(self, error: Exception) -> errors.SDKError:
        """Map OpenAI SDK exception → unified error hierarchy."""
        if isinstance(error, openai.APITimeoutError):
            return errors.RequestTimeoutError(str(error), cause=error)

        if isinstance(error, openai.APIConnectionError):
            return errors.NetworkError(str(error), cause=error)

        if isinstance(error, openai.APIStatusError):
            status_code = error.status_code
            message = str(error)

            body = getattr(error, "body", None)
            raw = body if isinstance(body, dict) else None
            error_code = None
            if isinstance(body, dict) and "error" in body:
                err_obj = body["error"]
                if isinstance(err_obj, dict):
                    error_code = err_obj.get("code") or err_obj.get("type")

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
                provider=self._name,
                error_code=error_code,
                raw=raw,
                retry_after=retry_after,
                cause=error,
            )

        return errors.ProviderError(
            message=str(error),
            provider=self._name,
            retryable=True,
            cause=error,
        )
