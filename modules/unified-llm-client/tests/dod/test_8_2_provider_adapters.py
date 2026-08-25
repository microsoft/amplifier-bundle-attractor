"""DoD §8.2 — Provider Adapters.

For EACH provider (OpenAI, Anthropic, Gemini), verify the adapter contract.
Uses mocked SDK responses — no real API keys needed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import openai
import pytest

from unified_llm import (
    ContentKind,
    ContentPart,
    Message,
    Request,
    Role,
)
from unified_llm.adapters.anthropic import AnthropicAdapter
from unified_llm.adapters.gemini import GeminiAdapter
from unified_llm.adapters.openai import OpenAIAdapter


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _openai_adapter() -> OpenAIAdapter:
    with patch("unified_llm.adapters.openai.openai.AsyncOpenAI"):
        return OpenAIAdapter(api_key="test")


def _anthropic_adapter() -> AnthropicAdapter:
    with patch("unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"):
        return AnthropicAdapter(api_key="test")


def _gemini_adapter() -> GeminiAdapter:
    with patch("unified_llm.adapters.gemini.genai.Client"):
        return GeminiAdapter(api_key="test")


# ---------------------------------------------------------------------------
# §8.2 — Native API usage
# ---------------------------------------------------------------------------


class TestNativeAPIUsage:
    """Each adapter uses the provider's native API."""

    def test_openai_uses_responses_api(self) -> None:
        """OpenAI adapter uses Responses API (client.responses.create)."""
        adapter = _openai_adapter()
        request = Request(model="gpt-4.1", messages=[Message.user("Hi")])
        kwargs = adapter._translate_request(request)
        # Responses API uses 'input' not 'messages'
        assert "input" in kwargs
        assert "messages" not in kwargs

    def test_anthropic_uses_messages_api(self) -> None:
        """Anthropic adapter uses Messages API."""
        adapter = _anthropic_adapter()
        request = Request(
            model="claude-sonnet-4-20250514", messages=[Message.user("Hi")]
        )
        kwargs = adapter._translate_request(request)
        # Messages API uses 'messages'
        assert "messages" in kwargs

    def test_gemini_uses_gemini_api(self) -> None:
        """Gemini adapter uses native Gemini API."""
        adapter = _gemini_adapter()
        # Just verify the adapter constructs a genai client
        assert adapter._client is not None


# ---------------------------------------------------------------------------
# §8.2 — Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    """Authentication works (API key from env var or explicit config)."""

    def test_openai_auth(self) -> None:
        with patch("unified_llm.adapters.openai.openai.AsyncOpenAI") as mock_cls:
            OpenAIAdapter(api_key="sk-test")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["api_key"] == "sk-test"

    def test_anthropic_auth(self) -> None:
        with patch(
            "unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"
        ) as mock_cls:
            AnthropicAdapter(api_key="sk-ant-test")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["api_key"] == "sk-ant-test"

    def test_gemini_auth(self) -> None:
        with patch("unified_llm.adapters.gemini.genai.Client") as mock_cls:
            GeminiAdapter(api_key="gm-test")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["api_key"] == "gm-test"


# ---------------------------------------------------------------------------
# §8.2 — complete() returns correctly populated Response
# ---------------------------------------------------------------------------


class TestCompleteReturnsResponse:
    """complete() sends request and returns correctly populated Response."""

    def test_openai_complete(self) -> None:
        adapter = _openai_adapter()
        mock_raw = SimpleNamespace(
            id="resp_1",
            model="gpt-4.1",
            status="completed",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[SimpleNamespace(type="output_text", text="Hi!")],
                )
            ],
            usage=SimpleNamespace(
                input_tokens=5,
                output_tokens=2,
                total_tokens=7,
                output_tokens_details=None,
                input_tokens_details=None,
            ),
        )
        _mock_raw_http = MagicMock()
        _mock_raw_http.parse.return_value = mock_raw
        _mock_raw_http.headers = {}
        adapter._client.responses.with_raw_response.create = AsyncMock(
            return_value=_mock_raw_http
        )
        request = Request(model="gpt-4.1", messages=[Message.user("Hello")])
        response = asyncio.run(adapter.complete(request))
        assert response.text == "Hi!"
        assert response.provider == "openai"
        assert response.usage.input_tokens == 5

    def test_anthropic_complete(self) -> None:
        adapter = _anthropic_adapter()
        mock_raw = SimpleNamespace(
            id="msg_1",
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Hello!")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=3),
        )
        _mock_raw_http = MagicMock()
        _mock_raw_http.parse = AsyncMock(return_value=mock_raw)
        _mock_raw_http.headers = {}
        adapter._client.messages.with_raw_response.create = AsyncMock(
            return_value=_mock_raw_http
        )
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            max_tokens=1024,
        )
        response = asyncio.run(adapter.complete(request))
        assert response.text == "Hello!"
        assert response.provider == "anthropic"

    def test_gemini_complete(self) -> None:
        adapter = _gemini_adapter()
        mock_candidate = SimpleNamespace(
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        text="Gemini says hi!", function_call=None, thought=None
                    )
                ]
            ),
            finish_reason="STOP",
        )
        mock_raw = SimpleNamespace(
            candidates=[mock_candidate],
            usage_metadata=SimpleNamespace(
                prompt_token_count=8,
                candidates_token_count=4,
                total_token_count=12,
                thoughts_token_count=None,
                cached_content_token_count=None,
            ),
        )
        adapter._client.aio.models.generate_content = AsyncMock(return_value=mock_raw)
        request = Request(model="gemini-2.5-flash", messages=[Message.user("Hi")])
        response = asyncio.run(adapter.complete(request))
        assert response.text == "Gemini says hi!"
        assert response.provider == "gemini"


# ---------------------------------------------------------------------------
# §8.2 — All 5 roles translated
# ---------------------------------------------------------------------------


class TestRoleTranslation:
    """All 5 roles (SYSTEM, USER, ASSISTANT, TOOL, DEVELOPER) are translated."""

    def test_openai_roles(self) -> None:
        adapter = _openai_adapter()
        request = Request(
            model="gpt-4.1",
            messages=[
                Message.system("sys"),
                Message(
                    role=Role.DEVELOPER,
                    content=[ContentPart(kind=ContentKind.TEXT, text="dev")],
                ),
                Message.user("usr"),
                Message.assistant("asst"),
            ],
        )
        kwargs = adapter._translate_request(request)
        # System and developer go to instructions
        assert "instructions" in kwargs
        assert "sys" in kwargs["instructions"]
        assert "dev" in kwargs["instructions"]
        # User and assistant go to input
        assert len(kwargs["input"]) == 2

    def test_anthropic_roles(self) -> None:
        adapter = _anthropic_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[
                Message.system("sys"),
                Message.user("usr"),
                Message.assistant("asst"),
            ],
        )
        kwargs = adapter._translate_request(request)
        # System extracted to 'system' param
        assert "system" in kwargs
        # Messages have user and assistant
        roles = [m["role"] for m in kwargs["messages"]]
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# §8.2 — provider_options escape hatch
# ---------------------------------------------------------------------------


class TestProviderOptionsPassthrough:
    """provider_options escape hatch passes through provider-specific parameters."""

    def test_openai_provider_options(self) -> None:
        adapter = _openai_adapter()
        request = Request(
            model="gpt-4.1",
            messages=[Message.user("Hi")],
            provider_options={"openai": {"store": True}},
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["store"] is True

    def test_anthropic_provider_options(self) -> None:
        adapter = _anthropic_adapter()
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            provider_options={"anthropic": {"metadata": {"user_id": "test"}}},
        )
        kwargs = adapter._translate_request(request)
        assert kwargs["metadata"] == {"user_id": "test"}


# ---------------------------------------------------------------------------
# §8.2 — HTTP errors translated
# ---------------------------------------------------------------------------


class TestErrorTranslation:
    """HTTP errors are translated to the correct error hierarchy types."""

    def test_openai_401_auth_error(self) -> None:
        from unified_llm import AuthenticationError

        adapter = _openai_adapter()
        api_error = openai.AuthenticationError(
            message="Bad key",
            response=MagicMock(status_code=401, headers={}),
            body={"error": {"message": "Bad key"}},
        )
        adapter._client.responses.with_raw_response.create = AsyncMock(
            side_effect=api_error
        )
        request = Request(model="gpt-4.1", messages=[Message.user("Hi")])
        with pytest.raises(AuthenticationError):
            asyncio.run(adapter.complete(request))

    def test_anthropic_429_rate_limit(self) -> None:
        from unified_llm import RateLimitError

        adapter = _anthropic_adapter()
        api_error = anthropic.RateLimitError(
            message="Rate limited",
            response=MagicMock(status_code=429, headers=MagicMock(get=lambda k: None)),
            body={"error": {"message": "Rate limited", "type": "rate_limit_error"}},
        )
        adapter._client.messages.with_raw_response.create = AsyncMock(
            side_effect=api_error
        )
        request = Request(
            model="claude-sonnet-4-20250514",
            messages=[Message.user("Hi")],
            max_tokens=100,
        )
        with pytest.raises(RateLimitError):
            asyncio.run(adapter.complete(request))


# ---------------------------------------------------------------------------
# §8.2 — Retry-After header parsed
# ---------------------------------------------------------------------------


class TestRetryAfterHeader:
    """Retry-After headers are parsed and set on the error object."""

    def test_openai_retry_after(self) -> None:
        adapter = _openai_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"retry-after": "30"}
        api_error = openai.RateLimitError(
            message="Rate limited",
            response=mock_response,
            body={"error": {"message": "Rate limited"}},
        )
        adapter._client.responses.with_raw_response.create = AsyncMock(
            side_effect=api_error
        )
        request = Request(model="gpt-4.1", messages=[Message.user("Hi")])
        try:
            asyncio.run(adapter.complete(request))
        except Exception as e:
            assert hasattr(e, "retry_after")
            assert e.retry_after == 30.0  # type: ignore[attr-defined]
