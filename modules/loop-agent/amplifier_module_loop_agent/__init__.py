"""Attractor coding agent loop orchestrator.

A task-oriented agentic loop with session state machine, steering,
loop detection, and provider-aligned tool profiles.

Implements the coding-agent-loop-spec from the Attractor nlspec.
"""

from __future__ import annotations

# Amplifier module metadata
__amplifier_module_type__ = "orchestrator"

import inspect
import logging
from typing import Any

from .agent_session import AgentSession
from .config import SessionConfig
from .steering import FollowUpQueue, SteeringQueue
from .subagent_tools import SubagentManager

logger = logging.getLogger(__name__)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the loop-agent orchestrator."""
    cfg = config or {}
    orchestrator = AgentOrchestrator(coordinator, cfg)
    await coordinator.mount("orchestrator", orchestrator)
    logger.info("loop-agent orchestrator mounted")


class AgentOrchestrator:
    """Coding agent orchestrator implementing the Orchestrator protocol.

    Manages a session state machine, turn history, steering queue,
    and the core agentic loop for tool-augmented LLM interactions.
    """

    def __init__(self, coordinator: Any, config: dict[str, Any]) -> None:
        self._coordinator = coordinator
        self._config = config
        self._session: AgentSession | None = None
        self._steering_queue = SteeringQueue()
        self._follow_up_queue = FollowUpQueue()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def session(self) -> AgentSession | None:
        """The current session, or None before first execute()."""
        return self._session

    def steer(self, message: str) -> None:
        """Queue a steering message for injection between tool rounds.

        The message becomes a SteeringTurn in the history, converted
        to a user-role message for the LLM on the next call.
        """
        self._steering_queue.steer(message)

    def follow_up(self, message: str) -> None:
        """Queue a follow-up message for after the current input completes.

        Triggers a new processing cycle via recursive process_input().
        """
        self._follow_up_queue.follow_up(message)

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: Any,
        coordinator: Any = None,
    ) -> str:
        """Execute the agent loop with given prompt.

        Lazy-creates an AgentSession on first call. The session persists
        between calls so conversation history carries over.

        Args:
            prompt: User input prompt
            context: Context manager for conversation state
            providers: Available LLM providers
            tools: Available tools
            hooks: Hook registry for lifecycle events
            coordinator: Module coordinator for hook result processing
                and spawn capabilities (passed by kernel session)

        Returns:
            Final response string
        """
        # Update coordinator if a fresh one is passed by the kernel
        if coordinator is not None:
            self._coordinator = coordinator
        if self._session is None:
            # Deliver context.include as Layer-1 "provider base instructions" (nlspec §6.1).
            #
            # Foundation registers _system_prompt_factory on the context module when
            # context.include is declared in a bundle profile (e.g. attractor-agent-anthropic
            # includes context/system-anthropic.md).  loop-agent must resolve the factory
            # here because it builds its own message list via _convert_history_to_messages()
            # rather than delegating to context.get_messages_for_request() — so the factory
            # is never called on the normal context-simple path.
            #
            # Guard: inspect.iscoroutinefunction avoids calling MagicMock auto-attributes
            # that tests inject as a plain MagicMock context.
            config_dict = dict(self._config)
            # Try the context module's factory first — it is the authoritative source for
            # Layer-1 "Provider-specific base instructions" (nlspec §6.1).  The factory is
            # registered by foundation when context.include is declared in the bundle profile
            # (e.g. attractor-agent-anthropic includes context/system-anthropic.md).
            # system_prompt config is a fallback; it is used only when the factory produces
            # nothing (returns empty string) or is absent.
            factory = getattr(context, "_system_prompt_factory", None)
            if inspect.iscoroutinefunction(factory):
                try:
                    context_base = await factory()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "context._system_prompt_factory raised; Layer-1 base prompt "
                        "will fall back to system_prompt config or stub: %s",
                        exc,
                    )
                    context_base = ""
                if context_base:
                    # Factory wins: the bundle's context.include content is the canonical
                    # Layer-1 base prompt.  Do not double-inject by also appending
                    # system_prompt — the factory already incorporates the bundle instruction.
                    config_dict["system_prompt"] = context_base

            config = SessionConfig.from_dict(config_dict)
            # Use the first available provider
            provider_name = next(iter(providers.keys()))
            provider = providers[provider_name]

            # Merge subagent lifecycle tools into the tools dict
            all_tools = dict(tools)
            if config.current_depth < config.max_subagent_depth:
                subagent_mgr = SubagentManager(
                    coordinator=self._coordinator,
                    max_depth=config.max_subagent_depth,
                    current_depth=config.current_depth,
                )
                for tool in subagent_mgr.create_tools():
                    all_tools[tool.name] = tool

            # Wrap provider with unified-llm adapter if configured
            if self._config.get("use_unified_llm"):
                try:
                    from .unified_provider_adapter import UnifiedProviderAdapter

                    model = self._config.get("model", "")
                    provider = UnifiedProviderAdapter(
                        provider_name=provider_name,
                        model=model,
                    )
                    logger.info(
                        "Using UnifiedProviderAdapter for %s/%s",
                        provider_name,
                        model,
                    )
                except (ImportError, Exception) as e:
                    logger.warning(
                        "Failed to create UnifiedProviderAdapter, "
                        "using native provider: %s",
                        e,
                    )

            self._session = AgentSession(
                config=config,
                provider=provider,
                tools=all_tools,
                hooks=hooks,
                steering_queue=self._steering_queue,
                follow_up_queue=self._follow_up_queue,
                coordinator=self._coordinator,
                provider_name=provider_name,
            )
            # Register subagent depth on coordinator for tool-delegate
            self._coordinator.register_capability(
                "self_delegation_depth", config.current_depth
            )
        return await self._session.process_input(prompt)
