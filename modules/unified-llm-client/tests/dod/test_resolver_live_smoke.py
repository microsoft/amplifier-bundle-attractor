"""Resolver live smoke tests — resolve-then-generate proof per provider.

For each provider whose API key is present in the environment:
  1. Call resolve_latest(adapter, "<family glob>") to get a live-resolved id.
  2. Call adapter.complete() with that id for 1 token.
  3. Assert output_tokens > 0 (proves the resolved id is generation-compatible).

Skip-LOUD when key absent: the skip message explicitly states that
"resolve-then-generate <provider> smoke SKIPPED: <KEY> not set — id-seam NOT
validated live".  No silent green passes.

Run with:
    pytest tests/dod/test_resolver_live_smoke.py -m integration

Requires: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY (or GOOGLE_API_KEY)
"""

from __future__ import annotations

import os

import pytest

from unified_llm.resolver import resolve_latest
from unified_llm.types import ContentKind, ContentPart, Message, Request, Role

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_resolve_then_generate_anthropic() -> None:
    """resolve-then-generate smoke — anthropic: *sonnet* family.

    Resolves the latest stable *sonnet* model from the live Anthropic API,
    then completes 1 token to prove the resolved id is generation-compatible.
    The same ``AnthropicAdapter`` instance is used for both list and generate,
    which is the structural id-seam guarantee.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip(
            "resolve-then-generate anthropic smoke SKIPPED: "
            "ANTHROPIC_API_KEY not set — id-seam NOT validated live"
        )

    from unified_llm.adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter()

    # Part 1: live resolution
    resolved = await resolve_latest(adapter, "*sonnet*", stable_only=True)
    assert resolved, "resolve_latest returned empty string"
    assert "preview" not in resolved.lower(), (
        f"resolve_latest(stable_only=True) returned non-stable id: {resolved!r}"
    )

    # Part 2: prove the resolved id is generation-compatible (same adapter instance)
    request = Request(
        model=resolved,
        messages=[
            Message(
                role=Role.USER,
                content=[
                    ContentPart(kind=ContentKind.TEXT, text="Reply with just 'ok'.")
                ],
            )
        ],
        max_tokens=16,
    )
    response = await adapter.complete(request)
    assert response.usage.output_tokens > 0, (
        f"Zero output tokens with resolved id {resolved!r} — id-seam failure: "
        "the resolved id may not be accepted by the same adapter's complete()"
    )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_resolve_then_generate_openai() -> None:
    """resolve-then-generate smoke — openai: gpt-4o* family.

    Resolves the latest stable gpt-4o* model from the live OpenAI API,
    then completes 1 token via the same adapter instance.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip(
            "resolve-then-generate openai smoke SKIPPED: "
            "OPENAI_API_KEY not set — id-seam NOT validated live"
        )

    from unified_llm.adapters.openai import OpenAIAdapter

    adapter = OpenAIAdapter()

    # Part 1: live resolution
    resolved = await resolve_latest(adapter, "gpt-4o*", stable_only=True)
    assert resolved, "resolve_latest returned empty string"
    assert "preview" not in resolved.lower(), (
        f"resolve_latest(stable_only=True) returned non-stable id: {resolved!r}"
    )

    # Part 2: prove generation-compatible (same adapter instance)
    request = Request(
        model=resolved,
        messages=[
            Message(
                role=Role.USER,
                content=[
                    ContentPart(kind=ContentKind.TEXT, text="Reply with just 'ok'.")
                ],
            )
        ],
        max_tokens=16,
    )
    response = await adapter.complete(request)
    assert response.usage.output_tokens > 0, (
        f"Zero output tokens with resolved id {resolved!r} — id-seam failure"
    )


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_resolve_then_generate_gemini() -> None:
    """resolve-then-generate smoke — gemini: *flash* family.

    Resolves the latest stable *flash* model from the live Gemini API,
    then completes 1 token via the same adapter instance.
    """
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        pytest.skip(
            "resolve-then-generate gemini smoke SKIPPED: "
            "GEMINI_API_KEY / GOOGLE_API_KEY not set — id-seam NOT validated live"
        )

    from unified_llm.adapters.gemini import GeminiAdapter

    adapter = GeminiAdapter()

    # Part 1: live resolution
    resolved = await resolve_latest(adapter, "*flash*", stable_only=True)
    assert resolved, "resolve_latest returned empty string"
    assert "preview" not in resolved.lower(), (
        f"resolve_latest(stable_only=True) returned non-stable id: {resolved!r}"
    )

    # Part 2: prove generation-compatible (same adapter instance)
    request = Request(
        model=resolved,
        messages=[
            Message(
                role=Role.USER,
                content=[
                    ContentPart(kind=ContentKind.TEXT, text="Reply with just 'ok'.")
                ],
            )
        ],
        max_tokens=16,
    )
    response = await adapter.complete(request)
    assert response.usage.output_tokens > 0, (
        f"Zero output tokens with resolved id {resolved!r} — id-seam failure"
    )
