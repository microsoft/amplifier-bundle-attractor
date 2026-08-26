"""Hook bridge middleware — bridges unified-llm-client to Amplifier hooks.

Creates a unified_llm.Middleware function that emits Amplifier hook events
(provider:request, provider:response, provider:error) around LLM calls,
and processes hook results (deny, modify, continue).

This middleware replaces the manual hooks.emit() calls from Phase 1,
moving event emission into the unified-llm middleware chain where it can
intercept and modify requests/responses.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from .pipeline_events import PROVIDER_REQUEST, PROVIDER_RESPONSE, PROVIDER_ERROR

logger = logging.getLogger(__name__)

# ContextVar for threading pipeline node context through the async call stack.
# Set by the backend before each generate() call, read by the middleware.
_current_node_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "pipeline_node_context", default={}
)


def get_node_context() -> dict[str, Any]:
    """Get the current pipeline node context (set by the backend)."""
    return _current_node_context.get()


def set_node_context(ctx: dict[str, Any]) -> contextvars.Token[dict[str, Any]]:
    """Set the pipeline node context for the current async task."""
    return _current_node_context.set(ctx)


def create_hook_bridge(
    hooks: Any,
) -> Any:
    """Create a unified-llm middleware that bridges to Amplifier's hook system.

    Args:
        hooks: Amplifier HookRegistry (or any object with async emit()).

    Returns:
        A middleware function compatible with unified_llm.Client(middleware=[...]).
    """

    async def hook_bridge_middleware(request: Any, next_fn: Any) -> Any:
        """Middleware that emits hook events around each LLM call."""
        from unified_llm.errors import AbortError, SDKError

        node_ctx = get_node_context()

        # Pre-request: emit provider:request
        pre_result = await hooks.emit(
            PROVIDER_REQUEST,
            {
                "provider": request.provider or "unknown",
                "model": request.model,
                "node_id": node_ctx.get("node_id"),
                "tool_names": [t.name for t in (request.tools or [])],
                "message_count": len(request.messages),
            },
        )

        # Check for deny action
        if getattr(pre_result, "action", "continue") == "deny":
            reason = getattr(pre_result, "reason", None) or "Denied by hook"
            raise AbortError(f"Denied by hook: {reason}")

        # Call through to next middleware / adapter
        try:
            response = await next_fn(request)
        except SDKError as exc:
            # Emit provider:error, then re-raise
            await hooks.emit(
                PROVIDER_ERROR,
                {
                    "provider": request.provider or "unknown",
                    "model": request.model,
                    "node_id": node_ctx.get("node_id"),
                    "error_type": type(exc).__name__,
                    "error_class": type(exc).__mro__[1].__name__,
                    "retryable": getattr(exc, "retryable", False),
                    "message": str(exc),
                },
            )
            raise

        # Post-response: emit provider:response
        usage = getattr(response, "usage", None)
        finish = getattr(response, "finish_reason", None)
        await hooks.emit(
            PROVIDER_RESPONSE,
            {
                "provider": request.provider or "unknown",
                "model": request.model,
                "node_id": node_ctx.get("node_id"),
                "usage": {
                    "input_tokens": getattr(usage, "input_tokens", 0),
                    "output_tokens": getattr(usage, "output_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                    "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", None),
                    "cache_write_tokens": getattr(usage, "cache_write_tokens", None),
                }
                if usage
                else {},
                "finish_reason": getattr(finish, "reason", "unknown")
                if finish
                else "unknown",
                "text_length": len(response.text)
                if hasattr(response, "text") and response.text
                else 0,
            },
        )

        return response

    return hook_bridge_middleware


def create_middleware_client(
    base_client: Any,
    hooks: Any,
) -> Any:
    """Create a new Client with hook bridge middleware, copying providers from base.

    This is the "provider-copy pattern": Client.from_env() doesn't accept middleware,
    so we call from_env() to get auto-detected adapters, then construct a new Client
    with those adapters plus our middleware.

    Args:
        base_client: An existing unified_llm.Client (e.g., from Client.from_env()).
        hooks: Amplifier HookRegistry.

    Returns:
        A new unified_llm.Client with the hook bridge middleware installed.
    """
    from unified_llm.client import Client

    middleware_fn = create_hook_bridge(hooks=hooks)

    return Client(
        providers=dict(base_client.providers),
        default_provider=base_client.default_provider,
        middleware=[middleware_fn],
    )


def wrap_tool_with_hooks(tool: Any, hooks: Any) -> Any:
    """Wrap a unified_llm.Tool's execute handler with hook events.

    Emits tool:pre before execution and tool:post after execution.
    The original tool's metadata (name, description, parameters) is preserved.

    Args:
        tool: A unified_llm.Tool instance.
        hooks: Amplifier HookRegistry.

    Returns:
        A new Tool with the execute handler wrapped (or same tool if execute is None).
    """
    from unified_llm.types import Tool

    if tool.execute is None:
        return Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            execute=None,
        )

    original_execute = tool.execute

    async def wrapped_execute(**kwargs: Any) -> Any:
        node_ctx = get_node_context()
        await hooks.emit(
            "tool:pre",
            {
                "tool_name": tool.name,
                "args": kwargs,
                "node_id": node_ctx.get("node_id"),
            },
        )

        result = await original_execute(**kwargs)

        post_result = await hooks.emit(
            "tool:post",
            {
                "tool_name": tool.name,
                "result": result,
                "node_id": node_ctx.get("node_id"),
            },
        )

        # Honor a tool:post hook modification (e.g. hooks-tool-truncation).
        #
        # amplifier-core's HookRegistry.emit() (hooks.rs) never returns
        # action="modify" to this caller -- Modify only chains data between
        # handlers inside emit()'s own dispatch loop; the action returned
        # here always collapses to "continue" (same defect class as
        # amplifier-support#485 in loop-agent's agent_session.py). Read
        # post_result.data directly instead of gating on action, and guard
        # for "result" being absent (Modify REPLACES the data dict, so a
        # partial-data hook can drop it) or non-string.
        modified_result = (
            post_result.data.get("result")
            if isinstance(post_result.data, dict)
            else None
        )
        if isinstance(modified_result, str) and modified_result != result:
            logger.debug(
                "tool:post hook modified output for %s: %d -> %d chars",
                tool.name,
                len(result) if isinstance(result, str) else -1,
                len(modified_result),
            )
            return modified_result

        return result

    return Tool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        execute=wrapped_execute,
    )
