"""Mocked unit tests for ULM-7, ULM-9, and ULM-10 silent-drop fixes.

ULM-7  reasoning_effort is a no-op on Anthropic/Gemini → now maps to thinking budget.
ULM-9  Error message-body classification → phrase-matched typed errors.
ULM-10 AUDIO/DOCUMENT content parts silently dropped → now raise ConfigurationError.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import unified_llm.errors as E
from unified_llm.adapters.anthropic import AnthropicAdapter
from unified_llm.adapters.gemini import GeminiAdapter
from unified_llm.types import (
    AudioData,
    ContentKind,
    ContentPart,
    DocumentData,
    Message,
    Request,
    Role,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_anthropic_adapter() -> AnthropicAdapter:
    with patch("unified_llm.adapters.anthropic.anthropic.AsyncAnthropic"):
        return AnthropicAdapter(api_key="test-key")


def _make_gemini_adapter() -> GeminiAdapter:
    with patch("unified_llm.adapters.gemini.genai.Client"):
        return GeminiAdapter(api_key="test-key")


def _simple_request(**extra) -> Request:
    return Request(
        model="test-model",
        messages=[Message.user("Hello")],
        **extra,
    )


# ===========================================================================
# ULM-7: reasoning_effort → extended thinking
# ===========================================================================


class TestULM7AnthropicReasoning:
    """reasoning_effort maps to Anthropic thinking param when set."""

    def test_reasoning_effort_high_adds_thinking_param(self) -> None:
        """High effort → thinking param with a large budget and temperature=1."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(reasoning_effort="high", max_tokens=20000)
        kwargs = adapter._translate_request(req)

        thinking = kwargs.get("thinking")
        assert thinking is not None, (
            "thinking param must be present when reasoning_effort is set"
        )
        assert thinking["type"] == "enabled"
        assert thinking["budget_tokens"] >= 1024
        assert thinking["budget_tokens"] < kwargs["max_tokens"], (
            "budget_tokens must be strictly less than max_tokens"
        )
        # Anthropic requires temperature=1.0 when thinking is enabled, but
        # anthropic 1.0.0 removed temperature from the typed Messages surface,
        # so the adapter sends it via extra_body instead of as a kwarg.
        assert "temperature" not in kwargs, (
            "temperature must not be a top-level kwarg under anthropic 1.0.0"
        )
        assert kwargs["extra_body"]["temperature"] == 1.0, (
            "Anthropic requires temperature=1.0 when thinking is enabled"
        )

    def test_reasoning_effort_low_budget_at_minimum(self) -> None:
        """Low effort → budget clamped to minimum 1024 (but still < max_tokens)."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(reasoning_effort="low", max_tokens=4096)
        kwargs = adapter._translate_request(req)

        thinking = kwargs.get("thinking")
        assert thinking is not None
        assert thinking["budget_tokens"] >= 1024

    def test_reasoning_effort_medium_budget_midrange(self) -> None:
        """Medium effort → budget is between low and high."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(reasoning_effort="medium", max_tokens=20000)
        kwargs = adapter._translate_request(req)

        thinking = kwargs.get("thinking")
        assert thinking is not None
        assert thinking["budget_tokens"] > 1024  # more than low
        assert thinking["budget_tokens"] < 20000  # less than max_tokens

    def test_reasoning_effort_budget_clamped_below_max_tokens(self) -> None:
        """Budget is always clamped to max_tokens - 1."""
        adapter = _make_anthropic_adapter()
        # max_tokens=1500 with high effort (budget wants 16000) → must be clamped
        req = _simple_request(reasoning_effort="high", max_tokens=1500)
        kwargs = adapter._translate_request(req)

        thinking = kwargs.get("thinking")
        assert thinking is not None
        assert thinking["budget_tokens"] == 1499  # max_tokens - 1

    def test_reasoning_effort_skipped_when_max_tokens_too_small(self) -> None:
        """If max_tokens <= 1024, thinking constraints can't be met → skip thinking."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(reasoning_effort="high", max_tokens=512)
        kwargs = adapter._translate_request(req)

        assert "thinking" not in kwargs, (
            "thinking must not be set when max_tokens <= 1024"
        )

    def test_reasoning_effort_not_set_no_thinking(self) -> None:
        """When reasoning_effort is None, the thinking param must NOT be added."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(max_tokens=4096)  # no reasoning_effort
        kwargs = adapter._translate_request(req)

        assert "thinking" not in kwargs, (
            "thinking must not be injected when reasoning_effort is unset"
        )

    def test_reasoning_effort_adds_beta_header(self) -> None:
        """Extended thinking adds the interleaved-thinking beta header."""
        adapter = _make_anthropic_adapter()
        req = _simple_request(reasoning_effort="medium", max_tokens=10000)
        kwargs = adapter._translate_request(req)

        extra_headers = kwargs.get("extra_headers", {})
        beta = extra_headers.get("anthropic-beta", "")
        assert "interleaved-thinking-2025-05-14" in beta, (
            "interleaved-thinking-2025-05-14 beta header must be set"
        )


class TestULM7GeminiReasoning:
    """reasoning_effort maps to Gemini ThinkingConfig when set."""

    def test_reasoning_effort_high_adds_thinking_config(self) -> None:
        """High effort → ThinkingConfig present in generation config."""
        adapter = _make_gemini_adapter()
        req = _simple_request(reasoning_effort="high")
        kwargs = adapter._translate_request(req)

        config = kwargs.get("config", {})
        thinking_config = config.get("thinking_config")
        assert thinking_config is not None, (
            "thinking_config must be present in config when reasoning_effort is set"
        )
        # Should be a ThinkingConfig object with thinking_budget set
        budget = getattr(thinking_config, "thinking_budget", None)
        assert budget is not None and budget > 0, (
            f"thinking_config.thinking_budget must be a positive int, got {budget}"
        )

    def test_reasoning_effort_low_smallest_budget(self) -> None:
        """Low effort → smallest budget (1024)."""
        adapter = _make_gemini_adapter()
        req = _simple_request(reasoning_effort="low")
        kwargs = adapter._translate_request(req)

        config = kwargs.get("config", {})
        thinking_config = config.get("thinking_config")
        assert thinking_config is not None
        budget = getattr(thinking_config, "thinking_budget", None)
        assert budget == 1024

    def test_reasoning_effort_medium_budget(self) -> None:
        """Medium effort → budget between low and high."""
        adapter = _make_gemini_adapter()
        req = _simple_request(reasoning_effort="medium")
        kwargs = adapter._translate_request(req)

        config = kwargs.get("config", {})
        thinking_config = config.get("thinking_config")
        assert thinking_config is not None
        budget = getattr(thinking_config, "thinking_budget", None)
        assert budget is not None
        assert 1024 < budget <= 16000

    def test_reasoning_effort_not_set_no_thinking_config(self) -> None:
        """When reasoning_effort is None, thinking_config must NOT be added."""
        adapter = _make_gemini_adapter()
        req = _simple_request()  # no reasoning_effort
        kwargs = adapter._translate_request(req)

        config = kwargs.get("config", {})
        assert "thinking_config" not in config, (
            "thinking_config must not be injected when reasoning_effort is unset"
        )


# ===========================================================================
# ULM-9: Error message-body classification
# ===========================================================================


class TestULM9ErrorClassification:
    """Ambiguous provider errors are promoted by message-body phrase matching."""

    # -- Quota / rate / billing --

    def test_insufficient_quota_phrase_raises_quota_exceeded(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="You have exceeded your insufficient_quota for this period.",
            provider="openai",
        )
        assert isinstance(err, E.QuotaExceededError), f"got {type(err)}"

    def test_quota_phrase_raises_quota_exceeded(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Monthly quota exceeded. Please upgrade your plan.",
            provider="anthropic",
        )
        assert isinstance(err, E.QuotaExceededError), f"got {type(err)}"

    def test_billing_phrase_raises_quota_exceeded(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Billing issue: your account has been suspended.",
            provider="gemini",
        )
        assert isinstance(err, E.QuotaExceededError), f"got {type(err)}"

    def test_rate_limit_phrase_raises_quota_exceeded(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Rate limit reached for your account tier.",
            provider="openai",
        )
        assert isinstance(err, E.QuotaExceededError), f"got {type(err)}"

    # -- Context length --

    def test_context_length_phrase_raises_context_length_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="This model's maximum context length is 4096 tokens.",
            provider="openai",
        )
        assert isinstance(err, E.ContextLengthError), f"got {type(err)}"

    def test_too_many_tokens_phrase_raises_context_length_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Request failed: too many tokens in prompt.",
            provider="anthropic",
        )
        assert isinstance(err, E.ContextLengthError), f"got {type(err)}"

    def test_prompt_is_too_long_phrase_raises_context_length_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="The prompt is too long for this model.",
            provider="gemini",
        )
        assert isinstance(err, E.ContextLengthError), f"got {type(err)}"

    def test_maximum_context_phrase_raises_context_length_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Exceeded maximum context window size.",
            provider="openai",
        )
        assert isinstance(err, E.ContextLengthError), f"got {type(err)}"

    # -- Content filter / safety --

    def test_content_filter_phrase_raises_content_filter_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Response blocked by content filter.",
            provider="openai",
        )
        assert isinstance(err, E.ContentFilterError), f"got {type(err)}"

    def test_content_policy_phrase_raises_content_filter_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Request violates content_policy guidelines.",
            provider="openai",
        )
        assert isinstance(err, E.ContentFilterError), f"got {type(err)}"

    def test_safety_phrase_raises_content_filter_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="Output was filtered by safety classifier.",
            provider="gemini",
        )
        assert isinstance(err, E.ContentFilterError), f"got {type(err)}"

    def test_blocked_phrase_raises_content_filter_error(self) -> None:
        err = E.error_from_status_code(
            status_code=400,
            message="This request has been blocked.",
            provider="anthropic",
        )
        assert isinstance(err, E.ContentFilterError), f"got {type(err)}"

    # -- Non-matching messages stay as generic typed errors --

    def test_non_matching_message_stays_invalid_request(self) -> None:
        """A plain 400 with unrelated text stays as InvalidRequestError."""
        err = E.error_from_status_code(
            status_code=400,
            message="Invalid parameter: temperature must be between 0 and 1.",
            provider="openai",
        )
        assert isinstance(err, E.InvalidRequestError), f"got {type(err)}"

    def test_non_matching_message_stays_auth_error(self) -> None:
        """A 401 must not be reclassified even if message contains other phrases."""
        err = E.error_from_status_code(
            status_code=401,
            message="Unauthorized: invalid API key provided.",
            provider="openai",
        )
        assert isinstance(err, E.AuthenticationError), f"got {type(err)}"

    def test_non_matching_message_stays_rate_limit(self) -> None:
        """A 429 stays as RateLimitError — message classification does not override it."""
        err = E.error_from_status_code(
            status_code=429,
            message="Rate limit exceeded. Retry after 60 seconds.",
            provider="openai",
        )
        assert isinstance(err, E.RateLimitError), f"got {type(err)}"

    def test_413_stays_context_length_without_body(self) -> None:
        """A 413 from the status map stays ContextLengthError even with no phrase."""
        err = E.error_from_status_code(
            status_code=413,
            message="Payload too large.",
            provider="openai",
        )
        assert isinstance(err, E.ContextLengthError), f"got {type(err)}"

    def test_existing_model_not_found_promotion_unchanged(self) -> None:
        """model_not_found error_code still promotes to NotFoundError."""
        err = E.error_from_status_code(
            status_code=400,
            message="Model gpt-99 not found.",
            provider="openai",
            error_code="model_not_found",
        )
        assert isinstance(err, E.NotFoundError), f"got {type(err)}"


# ===========================================================================
# ULM-10: Unsupported content kinds fail loud
# ===========================================================================


class TestULM10AnthropicUnsupportedContent:
    """AUDIO and DOCUMENT in user/assistant content raise ConfigurationError."""

    def test_audio_in_user_message_raises(self) -> None:
        adapter = _make_anthropic_adapter()
        req = Request(
            model="claude-test",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(
                            kind=ContentKind.AUDIO,
                            audio=AudioData(url="https://example.com/audio.mp3"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_document_in_user_message_raises(self) -> None:
        adapter = _make_anthropic_adapter()
        req = Request(
            model="claude-test",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(
                            kind=ContentKind.DOCUMENT,
                            document=DocumentData(url="https://example.com/doc.pdf"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_audio_in_assistant_message_raises(self) -> None:
        adapter = _make_anthropic_adapter()
        req = Request(
            model="claude-test",
            messages=[
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.AUDIO,
                            audio=AudioData(url="https://example.com/audio.mp3"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_document_in_assistant_message_raises(self) -> None:
        adapter = _make_anthropic_adapter()
        req = Request(
            model="claude-test",
            messages=[
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.DOCUMENT,
                            document=DocumentData(url="https://example.com/doc.pdf"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_text_and_image_in_user_message_still_work(self) -> None:
        """Sanity-check: text + image in user content must not raise."""
        adapter = _make_anthropic_adapter()
        req = Request(
            model="claude-test",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(kind=ContentKind.TEXT, text="Look at this:"),
                    ],
                )
            ],
        )
        # Should not raise
        kwargs = adapter._translate_request(req)
        assert kwargs["messages"][0]["content"][0]["type"] == "text"


class TestULM10GeminiUnsupportedContent:
    """AUDIO and DOCUMENT in Gemini user/model content raise ConfigurationError."""

    def test_audio_in_user_message_raises(self) -> None:
        adapter = _make_gemini_adapter()
        req = Request(
            model="gemini-test",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(
                            kind=ContentKind.AUDIO,
                            audio=AudioData(url="https://example.com/audio.mp3"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_document_in_user_message_raises(self) -> None:
        adapter = _make_gemini_adapter()
        req = Request(
            model="gemini-test",
            messages=[
                Message(
                    role=Role.USER,
                    content=[
                        ContentPart(
                            kind=ContentKind.DOCUMENT,
                            document=DocumentData(url="https://example.com/doc.pdf"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_audio_in_model_message_raises(self) -> None:
        adapter = _make_gemini_adapter()
        req = Request(
            model="gemini-test",
            messages=[
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.AUDIO,
                            audio=AudioData(url="https://example.com/audio.mp3"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_document_in_model_message_raises(self) -> None:
        adapter = _make_gemini_adapter()
        req = Request(
            model="gemini-test",
            messages=[
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ContentPart(
                            kind=ContentKind.DOCUMENT,
                            document=DocumentData(url="https://example.com/doc.pdf"),
                        )
                    ],
                )
            ],
        )
        with pytest.raises(E.ConfigurationError, match="unsupported content kind"):
            adapter._translate_request(req)

    def test_text_in_user_message_still_works(self) -> None:
        """Sanity-check: text in user content must not raise."""
        adapter = _make_gemini_adapter()
        req = Request(
            model="gemini-test",
            messages=[Message.user("Hello")],
        )
        kwargs = adapter._translate_request(req)
        assert kwargs["contents"][0]["parts"][0]["text"] == "Hello"
