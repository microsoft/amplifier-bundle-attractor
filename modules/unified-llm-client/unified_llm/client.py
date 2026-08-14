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
        from unified_llm._cost import compute_cost

        adapter = self._resolve_adapter(request)

        async def handler(req: Request) -> Response:
            return await adapter.complete(req)

        response = await apply_middleware(self._middleware, handler, request)

        # Inject cost_usd if not already set by the adapter
        if response.usage.cost_usd is None:
            computed = compute_cost(
                response.model,
                response.usage.input_tokens,
                response.usage.output_tokens,
                cache_read_tokens=response.usage.cache_read_tokens or 0,
                cache_write_tokens=response.usage.cache_write_tokens or 0,
            )
            if computed is not None:
                from unified_llm.types import Usage

                response.usage = Usage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.total_tokens,
                    reasoning_tokens=response.usage.reasoning_tokens,
                    cache_read_tokens=response.usage.cache_read_tokens,
                    cache_write_tokens=response.usage.cache_write_tokens,
                    raw=response.usage.raw,
                    cost_usd=computed,
                )

        return response

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

        if not providers:
            raise ConfigurationError(
                "No API keys found in environment. Set at least one of: "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY"
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
