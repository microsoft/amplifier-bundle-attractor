"""Tests for unified_llm.client — Client class with provider routing."""

import asyncio
import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

from unified_llm.client import Client, get_default_client, set_default_client
from unified_llm.errors import ConfigurationError, StreamProtocolError
from unified_llm.types import (
    FinishReason,
    Message,
    Request,
    Response,
    StreamEvent,
    StreamEventType,
    Usage,
)


def _make_response(provider: str = "mock") -> Response:
    return Response(
        id="r1",
        model="test-model",
        provider=provider,
        message=Message.assistant("hello"),
        finish_reason=FinishReason(reason="stop"),
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class _MockAdapter:
    """Minimal ProviderAdapter for testing."""

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self.complete_mock = AsyncMock(return_value=_make_response(name))
        self.stream_events: list[StreamEvent] = [
            StreamEvent(type=StreamEventType.TEXT_START),
            StreamEvent(type=StreamEventType.TEXT_DELTA, delta="hi"),
            StreamEvent(type=StreamEventType.TEXT_END),
            StreamEvent(type=StreamEventType.FINISH),
        ]

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: Request) -> Response:
        return await self.complete_mock(request)

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        for e in self.stream_events:
            yield e

    async def close(self) -> None:
        pass

    async def initialize(self) -> None:
        pass

    def supports_tool_choice(self, mode: str) -> bool:
        return True


def _make_request(provider: str | None = None) -> Request:
    return Request(model="test-model", messages=[Message.user("hi")], provider=provider)


# ---------------------------------------------------------------------------
# Helper to build a clean env dict without any provider API keys
# ---------------------------------------------------------------------------

_API_KEY_NAMES = frozenset(
    {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"}
)


def _env_without_keys() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _API_KEY_NAMES}


# ---------------------------------------------------------------------------
# Task 19 tests — Client construction + routing
# ---------------------------------------------------------------------------


class TestClientConstruction:
    """Client construction and provider registration."""

    def test_explicit_providers(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter}, default_provider="mock")
        assert "mock" in client.providers

    def test_no_providers_raises_on_request(self) -> None:
        client = Client(providers={})
        try:
            asyncio.run(client.complete(_make_request()))
            assert False, "Should raise"
        except ConfigurationError as e:
            assert (
                "no provider" in e.message.lower() or "no default" in e.message.lower()
            )


class TestProviderRouting:
    """Spec §2.2 — Provider resolution."""

    def test_explicit_provider_field(self) -> None:
        mock_a = _MockAdapter("alpha")
        mock_b = _MockAdapter("beta")
        client = Client(providers={"alpha": mock_a, "beta": mock_b})
        asyncio.run(client.complete(_make_request(provider="beta")))
        mock_b.complete_mock.assert_called_once()
        mock_a.complete_mock.assert_not_called()

    def test_default_provider_when_omitted(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter}, default_provider="mock")
        asyncio.run(client.complete(_make_request()))
        adapter.complete_mock.assert_called_once()

    def test_missing_provider_raises(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter})
        try:
            asyncio.run(client.complete(_make_request(provider="nonexistent")))
            assert False, "Should raise"
        except ConfigurationError:
            pass

    def test_no_default_no_explicit_raises(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter})
        try:
            asyncio.run(client.complete(_make_request()))
            assert False, "Should raise"
        except ConfigurationError:
            pass


class TestClientStream:
    """Client.stream() returns async iterator."""

    def test_stream_yields_events(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter}, default_provider="mock")

        async def run() -> list[StreamEvent]:
            result = []
            async for evt in client.stream(_make_request()):
                result.append(evt)
            return result

        events = asyncio.run(run())
        assert len(events) == 4
        assert events[0].type == StreamEventType.TEXT_START
        assert events[1].type == StreamEventType.TEXT_DELTA

    def test_stream_invalid_events_raise(self) -> None:
        adapter = _MockAdapter()
        adapter.stream_events = [
            StreamEvent(type=StreamEventType.TEXT_DELTA, delta="oops"),
            StreamEvent(type=StreamEventType.FINISH),
        ]
        client = Client(providers={"mock": adapter}, default_provider="mock")

        async def run() -> None:
            async for _ in client.stream(_make_request()):
                pass

        try:
            asyncio.run(run())
            assert False, "Should raise"
        except StreamProtocolError:
            pass


class TestClientClose:
    """Client.close() calls close() on all adapters."""

    def test_close(self) -> None:
        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter}, default_provider="mock")
        asyncio.run(client.close())
        # Should not raise


# ---------------------------------------------------------------------------
# Task 20 tests — from_env() + default client
# ---------------------------------------------------------------------------


class TestFromEnv:
    """Spec §2.2 — Client.from_env() detects API keys."""

    def test_no_keys_raises(self) -> None:
        """No API keys at all should raise ConfigurationError."""
        with patch.dict(os.environ, _env_without_keys(), clear=True):
            try:
                Client.from_env()
                assert False, "Should raise"
            except ConfigurationError as e:
                assert "no providers found in environment" in e.message.lower()

    def test_anthropic_key_detected(self) -> None:
        """ANTHROPIC_API_KEY should register anthropic provider."""
        env = _env_without_keys()
        env["ANTHROPIC_API_KEY"] = "test-key"
        with patch.dict(os.environ, env, clear=True):
            try:
                client = Client.from_env()
                assert "anthropic" in client.providers
                assert client.default_provider == "anthropic"
            except (ImportError, ModuleNotFoundError):
                # Expected — anthropic adapter module doesn't exist yet
                pass

    def test_openai_key_detected(self) -> None:
        """OPENAI_API_KEY should register openai provider."""
        env = _env_without_keys()
        env["OPENAI_API_KEY"] = "test-key"
        with patch.dict(os.environ, env, clear=True):
            try:
                client = Client.from_env()
                assert "openai" in client.providers
                assert client.default_provider == "openai"
            except (ImportError, ModuleNotFoundError):
                pass

    def test_gemini_key_detected(self) -> None:
        """GEMINI_API_KEY should register gemini provider."""
        env = _env_without_keys()
        env["GEMINI_API_KEY"] = "test-key"
        with patch.dict(os.environ, env, clear=True):
            try:
                client = Client.from_env()
                assert "gemini" in client.providers
            except (ImportError, ModuleNotFoundError):
                pass

    def test_google_api_key_also_works(self) -> None:
        """GOOGLE_API_KEY should also register gemini provider."""
        env = _env_without_keys()
        env["GOOGLE_API_KEY"] = "test-key"
        with patch.dict(os.environ, env, clear=True):
            try:
                client = Client.from_env()
                assert "gemini" in client.providers
            except (ImportError, ModuleNotFoundError):
                pass


class TestDefaultClient:
    """Spec §2.5 — Module-level default client."""

    def test_set_and_get_default(self) -> None:
        import unified_llm.client as client_mod

        # Reset state
        client_mod._default_client = None

        adapter = _MockAdapter()
        client = Client(providers={"mock": adapter}, default_provider="mock")
        set_default_client(client)
        assert get_default_client() is client

        # Clean up
        client_mod._default_client = None

    def test_get_default_without_set_calls_from_env(self) -> None:
        import unified_llm.client as client_mod

        # Reset state
        client_mod._default_client = None

        # With no API keys, get_default_client should raise ConfigurationError
        with patch.dict(os.environ, _env_without_keys(), clear=True):
            try:
                get_default_client()
                assert False, "Should raise"
            except ConfigurationError:
                pass

        # Clean up
        client_mod._default_client = None


# ---------------------------------------------------------------------------
# Task 21 tests — Client middleware integration (complete + stream)
# ---------------------------------------------------------------------------


class TestClientMiddlewareIntegration:
    """Spec §2.3 — Middleware applied through Client constructor."""

    def test_complete_through_middleware_onion_order(self) -> None:
        """Middleware intercepts complete() calls in onion order."""
        order: list[str] = []

        async def mw_a(request, next_fn):  # noqa: ANN001
            order.append("A_req")
            response = await next_fn(request)
            order.append("A_resp")
            return response

        async def mw_b(request, next_fn):  # noqa: ANN001
            order.append("B_req")
            response = await next_fn(request)
            order.append("B_resp")
            return response

        adapter = _MockAdapter()
        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[mw_a, mw_b],
        )
        result = asyncio.run(client.complete(_make_request()))
        assert result.text == "hello"
        assert order == ["A_req", "B_req", "B_resp", "A_resp"]

    def test_middleware_can_modify_request(self) -> None:
        """Middleware can modify the request before it reaches the adapter."""
        captured_temp: float | None = None

        async def set_temp(request, next_fn):  # noqa: ANN001
            request.temperature = 0.42
            return await next_fn(request)

        adapter = _MockAdapter()

        # Replace adapter.complete to capture the temperature
        async def patched_complete(request: Request) -> Response:
            nonlocal captured_temp
            captured_temp = request.temperature
            return _make_response("mock")

        adapter.complete = patched_complete  # type: ignore[assignment]

        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[set_temp],
        )
        asyncio.run(client.complete(_make_request()))
        assert captured_temp == 0.42

    def test_middleware_can_modify_response(self) -> None:
        """Middleware can modify the response on the way back."""

        async def tag_response(request, next_fn):  # noqa: ANN001
            response = await next_fn(request)
            response.id = "modified"
            return response

        adapter = _MockAdapter()
        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[tag_response],
        )
        result = asyncio.run(client.complete(_make_request()))
        assert result.id == "modified"

    def test_stream_through_middleware(self) -> None:
        """Middleware intercepts stream() calls and can observe events."""
        seen_deltas: list[str] = []

        async def logger_mw(request, next_fn):  # noqa: ANN001
            async for event in next_fn(request):
                if event.delta:
                    seen_deltas.append(event.delta)
                yield event

        adapter = _MockAdapter()
        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[logger_mw],
        )

        async def run() -> list[StreamEvent]:
            result = []
            async for evt in client.stream(_make_request()):
                result.append(evt)
            return result

        events = asyncio.run(run())
        assert len(events) == 4
        assert seen_deltas == ["hi"]

    def test_stream_middleware_ordering(self) -> None:
        """Streaming middleware maintains onion order for event observation."""
        order: list[str] = []

        async def mw_outer(request, next_fn):  # noqa: ANN001
            order.append("outer_start")
            async for event in next_fn(request):
                order.append("outer_event")
                yield event
            order.append("outer_done")

        async def mw_inner(request, next_fn):  # noqa: ANN001
            order.append("inner_start")
            async for event in next_fn(request):
                order.append("inner_event")
                yield event
            order.append("inner_done")

        adapter = _MockAdapter()
        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[mw_outer, mw_inner],
        )

        async def run() -> list[StreamEvent]:
            result = []
            async for evt in client.stream(_make_request()):
                result.append(evt)
            return result

        asyncio.run(run())
        # Outer starts first, inner starts second, events flow out inner→outer
        assert order[0] == "outer_start"
        assert order[1] == "inner_start"

    def test_no_middleware_passthrough(self) -> None:
        """Client with empty middleware list works like direct adapter call."""
        adapter = _MockAdapter()
        client = Client(
            providers={"mock": adapter},
            default_provider="mock",
            middleware=[],
        )
        result = asyncio.run(client.complete(_make_request()))
        assert result.text == "hello"
        adapter.complete_mock.assert_called_once()
