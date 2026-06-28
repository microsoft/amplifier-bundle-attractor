"""Attractor coding agent loop orchestrator.

A task-oriented agentic loop with session state machine, steering,
loop detection, and provider-aligned tool profiles.

Implements the coding-agent-loop-spec from the Attractor nlspec.
"""

from __future__ import annotations

# Amplifier module metadata
__amplifier_module_type__ = "orchestrator"

import logging
from pathlib import Path
from typing import Any

from .agent_session import AgentSession
from .config import SessionConfig
from .steering import FollowUpQueue, SteeringQueue
from .subagent_tools import SubagentManager

logger = logging.getLogger(__name__)


def _resolve_system_prompt_file(spf: str) -> Path:
    """Resolve a configured ``system_prompt_file`` to an absolute, existing path.

    Resolution is **CWD-INDEPENDENT**. It anchors on this module's installed
    location (``__file__``), never on the process working directory — so the
    same configured value resolves identically no matter where the process was
    launched from (a different CWD, a container, a recipe that changes dirs) or
    which consumer spawned the loop-agent node.

    Rules:
      (a) ABSOLUTE ``spf`` -> used as-is (must exist).
      (b) RELATIVE ``spf`` -> resolved against the loop-agent module's owning
          bundle root. The module installs (editable) at::

              <bundle-root>/modules/loop-agent/amplifier_module_loop_agent/__init__.py

          so the bundle root is ``parents[3]`` of this file. That is the
          documented, primary anchor and is tried FIRST (deterministic in the
          normal layout). As resilience against a future layout/nesting change,
          if the primary anchor does not contain the file we then walk UP the
          remaining ancestors and accept the first one under which the relative
          path actually exists. Every candidate is anchored on ``__file__`` —
          the current working directory is never consulted.
      (c) If no candidate resolves to an existing file, raise a CLEAR, ACTIONABLE
          ``FileNotFoundError`` naming the configured value AND the absolute path
          tried — never a silent empty Layer-1.

    Returns:
        Absolute ``Path`` to an existing file.

    Raises:
        FileNotFoundError: when no existing file can be resolved.

    See docs/designs/layer-1-profile-owned-system-prompt.md.
    """
    p = Path(spf)
    if p.is_absolute():
        if p.is_file():
            return p
        raise FileNotFoundError(
            f"system_prompt_file {spf!r} (absolute path) does not exist at {p}. "
            f"Point session.orchestrator.config.system_prompt_file at an existing "
            f"file (e.g. context/system-<provider>.md). "
            f"See docs/designs/layer-1-profile-owned-system-prompt.md."
        )

    pkg_file = Path(__file__).resolve()
    ancestors = pkg_file.parents  # CWD-independent: anchored on __file__

    # Build the candidate list: the documented bundle-root anchor (parents[3])
    # FIRST for determinism, then every remaining ancestor as a resilience
    # fallback. Preserve order, drop duplicates.
    candidates: list[Path] = []
    if len(ancestors) > 3:
        candidates.append(ancestors[3] / p)
    for ancestor in ancestors:
        cand = ancestor / p
        if cand not in candidates:
            candidates.append(cand)

    for cand in candidates:
        if cand.is_file():
            return cand

    primary = candidates[0] if candidates else (pkg_file.parent / p)
    raise FileNotFoundError(
        f"system_prompt_file {spf!r} (relative) could not be resolved to an "
        f"existing file. Expected it at the bundle root: {primary}. "
        f"Resolution is anchored on the loop-agent module location "
        f"({pkg_file}) and is independent of the current working directory. "
        f"Add the file, or fix session.orchestrator.config.system_prompt_file. "
        f"See docs/designs/layer-1-profile-owned-system-prompt.md."
    )


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
            # Load Layer-1 base prompt from profile-declared file (nlspec §6.1:
            # "Provider-specific base instructions from ProviderProfile").
            #
            # Design: docs/designs/layer-1-profile-owned-system-prompt.md
            #
            # The profile declares `system_prompt_file: context/system-<prov>.md`
            # in session.orchestrator.config.  loop-agent resolves it here — before
            # the session is created — so the text lands in SessionConfig.system_prompt
            # (Layer-1) regardless of which spawn path was used.
            #
            # Path resolution is delegated to _resolve_system_prompt_file, which is
            # CWD-INDEPENDENT (anchored on this module's __file__, never the process
            # working directory) and fail-loud: a configured-but-missing file raises
            # a clear, actionable FileNotFoundError naming the value and the absolute
            # path tried — rather than a silent empty Layer-1 that only trips the
            # generic guard later. See docs/designs/layer-1-profile-owned-system-prompt.md.
            #
            # If system_prompt is already set in config (e.g. injected by loop-pipeline's
            # backend.py before spawn), the file is skipped — single owner, no conflict.
            config_dict = dict(self._config)
            _spf = config_dict.get("system_prompt_file", "")
            if _spf and not config_dict.get("system_prompt"):
                _spf_path = _resolve_system_prompt_file(_spf)
                config_dict["system_prompt"] = _spf_path.read_text(encoding="utf-8")

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
