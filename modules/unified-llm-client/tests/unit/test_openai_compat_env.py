"""OpenAI-compatible endpoint registration from the environment (Spec §7.10).

The load-bearing property: the registered NAME is operator-chosen, because it
is what a caller puts in ``Request.provider`` -- and, for attractor, what a DOT
node puts in ``llm_provider``.  A scheme that dictated the name (by sniffing
``OLLAMA_HOST`` and friends) would force graph authors to spell their endpoints
the way we happened to name a runtime.
"""

from __future__ import annotations

import pytest

from unified_llm.client import Client
from unified_llm.errors import ConfigurationError

_CLOUD_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
_COMPAT_KEYS = (
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_PROVIDER_NAME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a known-empty environment."""
    for key in (*_CLOUD_KEYS, *_COMPAT_KEYS):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Single-endpoint shorthand
# ---------------------------------------------------------------------------


def test_shorthand_registers_as_local_by_default(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")

    client = Client.from_env()

    assert set(client.providers) == {"local"}
    assert client.default_provider == "local"


def test_shorthand_name_is_overridable(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://localhost:12434/engines/v1")
    monkeypatch.setenv("OPENAI_COMPAT_PROVIDER_NAME", "docker")

    client = Client.from_env()

    assert set(client.providers) == {"docker"}
    assert client.providers["docker"].name == "docker"


# ---------------------------------------------------------------------------
# Precedence -- must not disturb the existing cloud contract (§2.2)
# ---------------------------------------------------------------------------


def test_compat_never_steals_default_from_a_cloud_provider(monkeypatch):
    """
    §2.2: "the first registered provider becomes the default".

    Compat endpoints register LAST, so adding one to an environment that
    already has a cloud key must not change which provider is default.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")

    client = Client.from_env()

    assert client.default_provider == "anthropic"
    assert set(client.providers) == {"anthropic", "local"}


def test_compat_alone_satisfies_from_env(monkeypatch):
    """A local-only environment is a valid environment.

    Before this, from_env() raised unless a cloud API key was present -- which
    made local-only operation impossible by construction.
    """
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")

    assert Client.from_env().providers  # does not raise


def test_no_providers_at_all_names_the_compat_vars(monkeypatch):
    with pytest.raises(ConfigurationError) as exc:
        Client.from_env()

    msg = str(exc.value)
    assert "OPENAI_COMPAT_BASE_URL" in msg
