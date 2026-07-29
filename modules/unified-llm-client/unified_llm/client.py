"""Core Client class with provider routing (Spec §2.2, §3, §4.1-4.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from unified_llm.adapters import ProviderAdapter
from unified_llm.errors import ConfigurationError
from unified_llm.middleware import (
    Middleware,
    apply_middleware,
    apply_streaming_middleware,
)
from unified_llm.stream_validation import validate_stream
from unified_llm.types import Request, Response, StreamEvent

# Module-level default client (Spec §2.5)
_default_client: Client | None = None

# Env-var prefix for OpenAI-compatible endpoints (Spec §7.10).
_COMPAT_PREFIX = "OPENAI_COMPAT"

# Local endpoints typically ignore the key, but the OpenAI SDK wants a string.
_COMPAT_DEFAULT_KEY = "not-needed"





def _openai_compat_from_env() -> dict[str, ProviderAdapter]:
    """Build an OpenAI-compatible adapter from the 3-variable shorthand (Spec §7.10).

    The registered NAME is what a caller puts in ``Request.provider`` -- and, for
    attractor, what a DOT node puts in ``llm_provider``. So the name is chosen by
    the operator, not by us::

        OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
        OPENAI_COMPAT_PROVIDER_NAME=ollama           # optional, default "local"
        OPENAI_COMPAT_API_KEY=...                    # optional, default "not-needed"

    Keyed on ``BASE_URL``, never on an API key: local endpoints have no meaningful
    key, so requiring one would make them unreachable.

    Returns:
        Mapping of provider name to adapter, or empty when ``BASE_URL`` is not set.
    """
    import os

    base_url = os.environ.get(f"{_COMPAT_PREFIX}_BASE_URL")
    if not base_url:
        return {}

    from unified_llm.adapters.openai_compat import OpenAICompatAdapter

    name = os.environ.get(f"{_COMPAT_PREFIX}_PROVIDER_NAME", "local").strip() or "local"
    api_key = (
        os.environ.get(f"{_COMPAT_PREFIX}_API_KEY")
        or _COMPAT_DEFAULT_KEY
    )
    return {name: OpenAICompatAdapter(name=name, api_key=api_key, base_url=base_url)}


class Client:
    """Provider-agnostic LLM client (Spec §3).

    Routes requests to registered provider adapters. Applies middleware.
    Does NOT retry — that's Layer 4's responsibility.
    """

    def __init__(
        self,
        providers: dict[str, ProviderAdapter],
        default_provider: str | None = None,
        middleware: list[Middleware] | None = None,
    ) -> None:
        self.providers = dict(providers)
        self.default_provider = default_provider
        self._middleware = middleware or []

    def _resolve_adapter(self, request: Request) -> ProviderAdapter:
        """Resolve which adapter handles this request."""
        provider_name = request.provider or self.default_provider
        if provider_name is None:
            raise ConfigurationError(
                "No provider specified and no default provider configured. "
                "Set provider on the request or configure a default_provider."
            )
        adapter = self.providers.get(provider_name)
        if adapter is None:
            raise ConfigurationError(
                f"Provider '{provider_name}' not found. "
                f"Available providers: {list(self.providers.keys())}"
            )
        return adapter

    async def complete(self, request: Request) -> Response:
        """Low-level blocking call. No retry. (Spec §4.1)."""
        adapter = self._resolve_adapter(request)

        async def handler(req: Request) -> Response:
            return await adapter.complete(req)

        return await apply_middleware(self._middleware, handler, request)

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Low-level streaming call. No retry. (Spec §4.2)."""
        adapter = self._resolve_adapter(request)

        async def handler(req: Request) -> AsyncIterator[StreamEvent]:
            if req.stream_validation_mode is None:
                async for event in validate_stream(adapter.stream(req)):
                    yield event
            else:
                async for event in validate_stream(
                    adapter.stream(req), mode=req.stream_validation_mode
                ):
                    yield event

        async for event in apply_streaming_middleware(
            self._middleware, handler, request
        ):
            yield event

    async def close(self) -> None:
        """Release resources on all adapters (Spec §2.4)."""
        for adapter in self.providers.values():
            if hasattr(adapter, "close"):
                await adapter.close()

    @classmethod
    def from_env(cls) -> Client:
        """Create a Client by detecting API keys from environment (Spec §2.2).

        Registers adapters for providers whose keys are present.
        First registered becomes default.
        """
        import os

        providers: dict[str, ProviderAdapter] = {}
        default: str | None = None

        # Anthropic
        if os.environ.get("ANTHROPIC_API_KEY"):
            from unified_llm.adapters.anthropic import AnthropicAdapter

            providers["anthropic"] = AnthropicAdapter()
            if default is None:
                default = "anthropic"

        # OpenAI
        if os.environ.get("OPENAI_API_KEY"):
            from unified_llm.adapters.openai import OpenAIAdapter

            providers["openai"] = OpenAIAdapter()
            if default is None:
                default = "openai"

        # Gemini
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            from unified_llm.adapters.gemini import GeminiAdapter

            providers["gemini"] = GeminiAdapter()
            if default is None:
                default = "gemini"

        # OpenAI-compatible endpoints (Spec §7.10): vLLM, Ollama, llama.cpp,
        # Docker Model Runner, LM Studio, Together, Groq...
        #
        # Registered LAST so an OpenAI-compatible endpoint never steals
        # `default_provider` from a cloud key that is already present, per §2.2
        # ("the first registered provider becomes the default").
        for name, adapter in _openai_compat_from_env().items():
            providers[name] = adapter
            if default is None:
                default = name

        if not providers:
            raise ConfigurationError(
                "No providers found in environment. Set at least one of: "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, or an "
                "OpenAI-compatible endpoint via OPENAI_COMPAT_BASE_URL "
                "(optionally with OPENAI_COMPAT_PROVIDER_NAME and OPENAI_COMPAT_API_KEY)."
            )

        return cls(providers=providers, default_provider=default)


def set_default_client(client: Client) -> None:
    """Set the module-level default client (Spec §2.5)."""
    global _default_client
    _default_client = client


def get_default_client() -> Client:
    """Get or lazily initialize the default client."""
    global _default_client
    if _default_client is None:
        _default_client = Client.from_env()
    return _default_client
