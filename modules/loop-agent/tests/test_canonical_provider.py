"""Tests for canonical_provider() substring-match false positives.

Bug (confirmed): canonical_provider() does a case-insensitive SUBSTRING
containment check against KNOWN_PROVIDERS = ("anthropic", "openai", "gemini").
A compound provider name like "azure-openai" (a real, distinct provider
module in the ecosystem -- different auth, different endpoint from plain
OpenAI) contains the substring "openai", so it was incorrectly canonicalized
to "openai" instead of being recognised as an unknown/distinct provider.

This silently misroutes explicit llm_provider requests: if a pipeline node
asks for "azure-openai" and only a plain "openai" provider is mounted, the
exact-match check fails, falls through to the canonical-match scan, and
WRONGLY resolves to the mounted "openai" instance with no error -- a
wrong-provider misroute (different API, different auth, different endpoint).

These tests pin down both the unit-level behavior of canonical_provider()
and the end-to-end fail-loud contract it must preserve.
"""

from __future__ import annotations

from amplifier_module_loop_agent.agent_session import canonical_provider


# ---------------------------------------------------------------------------
# The bug: compound names must NOT be absorbed into an unrelated family
# ---------------------------------------------------------------------------


def test_azure_openai_does_not_canonicalize_to_openai():
    """'azure-openai' is a distinct, differently-configured provider.

    It must NOT canonicalize to "openai" -- doing so is exactly the false
    positive that causes the silent misroute described above.
    """
    assert canonical_provider("azure-openai") != "openai"


def test_azure_openai_variants_do_not_canonicalize_to_openai():
    """Case and separator variants of the compound name must not leak through."""
    for raw in (
        "azure-openai",
        "Azure-OpenAI",
        "AZURE-OPENAI",
        "azure_openai",
        "azureopenai",
    ):
        assert canonical_provider(raw) != "openai", (
            f"canonical_provider({raw!r}) incorrectly resolved to 'openai'"
        )


# ---------------------------------------------------------------------------
# Regression: the 3 legitimate canonical families must still match correctly
# ---------------------------------------------------------------------------


def test_anthropic_prefixed_name_canonicalizes():
    assert canonical_provider("provider-anthropic") == "anthropic"


def test_anthropic_suffixed_model_name_canonicalizes():
    assert canonical_provider("anthropic-sonnet") == "anthropic"


def test_openai_case_insensitive_canonicalizes():
    assert canonical_provider("Provider-OpenAI") == "openai"


def test_plain_openai_canonicalizes():
    assert canonical_provider("openai") == "openai"


def test_gemini_prefixed_name_canonicalizes():
    assert canonical_provider("provider-gemini") == "gemini"


def test_unknown_provider_returns_none():
    assert canonical_provider("ollama") is None


def test_none_and_empty_return_none():
    assert canonical_provider(None) is None
    assert canonical_provider("") is None
