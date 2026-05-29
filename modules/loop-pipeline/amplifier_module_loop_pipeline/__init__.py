"""Attractor pipeline orchestrator module.

A DOT graph-driven multi-stage AI workflow engine. Parses directed graphs
(defined in Graphviz DOT syntax) to orchestrate multi-stage AI pipelines
where each node is an AI task and edges define the flow between them.

Implements the Attractor specification (attractor-spec.md).
"""

from __future__ import annotations

# Amplifier module metadata
__amplifier_module_type__ = "orchestrator"

import json
import logging
import os
import re
import tempfile
from typing import Any

from .context import PipelineContext
from .dot_parser import parse_dot
from .engine import PipelineEngine
from .handlers import HandlerRegistry
from .handlers.context import HandlerContext
from .outcome import Outcome, StageStatus
from .hook_bridge import _current_node_context, set_node_context
from .pipeline_events import PROVIDER_ERROR, PROVIDER_REQUEST, PROVIDER_RESPONSE
from .transforms import apply_transforms
from .validation import validate_or_raise

logger = logging.getLogger(__name__)


class DirectProviderBackend:
    """Backend that calls a provider directly via unified_llm.generate().

    This is the default backend when no session.spawn capability is
    available.  It delegates the agentic tool loop to the unified-llm-client
    library, which handles LLM calls, tool execution, retry, and error
    mapping internally.
    """

    def __init__(
        self,
        provider: Any,
        tools: dict[str, Any] | None = None,
        hooks: Any = None,
        coordinator: Any = None,
        unified_client: Any | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools or {}
        self._hooks = hooks
        self._coordinator = coordinator
        self._unified_client = unified_client
        # Fidelity state (H-9): track completed nodes and message history
        self._completed_nodes: dict[str, Any] = {}
        self._message_pools: dict[str, list] = {}  # thread_key -> unified_llm Messages
        self._last_node_id: str | None = None

    async def run(
        self,
        node: Any,
        prompt: str,
        context: PipelineContext,
        *,
        incoming_edge: Any | None = None,
        graph: Any | None = None,
        **kwargs: Any,
    ) -> Outcome:
        """Run an LLM call for *node* via unified_llm.generate().

        Supports fidelity-aware context carryover (spec Section 5.4):
        - full: reuse message history from previous calls with same thread key
        - compact/truncate/summary: prepend preamble from completed node history
        """
        import unified_llm

        from .backend import (
            _build_unified_tools,
            _find_report_outcome_call,
            _parse_outcome,
            _resolve_model,
            _STATUS_MAP,
            _MAX_TOOL_LOOP_ROUNDS,
        )

        # Resolve fidelity mode (spec FID-001)
        from .fidelity import build_preamble, resolve_fidelity, resolve_thread_key

        fidelity = "compact"  # default
        thread_key = node.id
        if graph is not None:
            fidelity = resolve_fidelity(node, incoming_edge, graph)
            thread_key = resolve_thread_key(
                node, incoming_edge, graph, self._last_node_id
            )

        # Resolve model, provider, tools, reasoning, max_agent_turns
        model = _resolve_model(node)
        provider_name = (
            node.llm_provider
            if hasattr(node, "llm_provider") and node.llm_provider
            else node.attrs.get("llm_provider", "anthropic")
        )
        reasoning_effort = node.attrs.get("reasoning_effort")
        max_agent_turns_raw = node.attrs.get("max_agent_turns")
        max_agent_turns = (
            int(max_agent_turns_raw) if max_agent_turns_raw is not None else None
        )
        tools = _build_unified_tools(self._tools)
        client = self._get_or_create_unified_client()

        # Build generate() kwargs based on fidelity mode
        generate_kwargs: dict[str, Any] = dict(
            model=model,
            tools=tools or None,
            max_tool_rounds=max_agent_turns
            if max_agent_turns is not None
            else _MAX_TOOL_LOOP_ROUNDS,
            reasoning_effort=reasoning_effort,
            provider=provider_name,
            client=client,
        )

        if fidelity == "full":
            # Reuse accumulated message history for this thread key
            ulm_messages: list[unified_llm.Message] = list(
                self._message_pools.get(thread_key, [])
            )
            ulm_messages.append(unified_llm.Message.user(prompt))
            generate_kwargs["messages"] = ulm_messages
        else:
            # Fresh session with preamble
            if graph is not None and self._completed_nodes:
                preamble = build_preamble(fidelity, context, self._completed_nodes)
                effective_prompt = (
                    f"{preamble}\n\n---\n\n{prompt}" if preamble else prompt
                )
            else:
                effective_prompt = prompt
            generate_kwargs["prompt"] = effective_prompt

        # Set node context for the hook bridge middleware
        token = set_node_context({"node_id": node.id})

        try:
            # Emit provider:request before the LLM call
            pre_result = await self._emit(
                PROVIDER_REQUEST,
                {
                    "provider": provider_name,
                    "model": model,
                    "node_id": node.id,
                    "tool_names": [t.name for t in tools] if tools else [],
                    "message_count": len(generate_kwargs.get("messages", [])) or 1,
                },
            )

            # Check for deny action from hooks
            if (
                pre_result is not None
                and getattr(pre_result, "action", "continue") == "deny"
            ):
                reason = getattr(pre_result, "reason", None) or "Denied by hook"
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Denied by hook: {reason}",
                )

            # Call unified_llm.generate() — handles tool loop, retry, errors
            result = await unified_llm.generate(**generate_kwargs)
        except unified_llm.SDKError as exc:
            logger.warning("unified_llm.generate failed for node %s: %s", node.id, exc)
            await self._emit(
                PROVIDER_ERROR,
                {
                    "provider": provider_name,
                    "model": model,
                    "node_id": node.id,
                    "error_type": type(exc).__name__,
                    "error_class": type(exc).__mro__[1].__name__,
                    "retryable": getattr(exc, "retryable", False),
                    "message": str(exc),
                },
            )
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=str(exc),
            )
        except Exception as exc:
            logger.warning("Unexpected error in generate for node %s: %s", node.id, exc)
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=str(exc),
            )
        finally:
            _current_node_context.reset(token)

        # Emit provider:response after successful LLM call
        await self._emit(
            PROVIDER_RESPONSE,
            {
                "provider": provider_name,
                "model": model,
                "node_id": node.id,
                "usage": {
                    "input_tokens": result.total_usage.input_tokens,
                    "output_tokens": result.total_usage.output_tokens,
                    "total_tokens": result.total_usage.total_tokens,
                    "reasoning_tokens": result.total_usage.reasoning_tokens,
                    "cache_read_tokens": result.total_usage.cache_read_tokens,
                    "cache_write_tokens": result.total_usage.cache_write_tokens,
                },
                "finish_reason": result.finish_reason.reason,
                "text_length": len(result.text) if result.text else 0,
                "step_count": len(result.steps),
            },
        )

        # Map GenerateResult → Outcome (same priority order as _run_with_tool_loop)
        text = result.text

        if text:
            stripped = text.strip()
            _fence_match = re.match(
                r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.DOTALL
            )
            if bool(_fence_match) or stripped.startswith("{"):
                outcome = _parse_outcome(text)
                outcome.context_updates = {
                    "last_stage": node.id,
                    "last_response": text[:200],
                }
                self._completed_nodes[node.id] = outcome
                self._last_node_id = node.id
                return outcome

        # Text is plain prose or empty — check if report_outcome was called
        lo = _find_report_outcome_call(result)
        if lo is not None:
            outcome = Outcome(
                status=_STATUS_MAP.get(lo.get("status"), StageStatus.FAIL),
                context_updates=lo.get("context_updates"),
                failure_reason=lo.get("failure_reason"),
                preferred_label=lo.get("preferred_label"),
                suggested_next_ids=lo.get("suggested_next_ids"),
                notes=lo.get("notes"),
            )
            self._completed_nodes[node.id] = outcome
            self._last_node_id = node.id
            return outcome

        if text:
            outcome = _parse_outcome(text)
        else:
            outcome = Outcome(
                status=StageStatus.SUCCESS,
                notes=f"Stage completed: {node.id}",
            )

        outcome.context_updates = {
            "last_stage": node.id,
            "last_response": text[:200] if text else "",
        }

        # Record fidelity state for future calls
        self._completed_nodes[node.id] = outcome
        self._last_node_id = node.id

        # For full fidelity: save conversation history including tool steps
        if fidelity == "full":
            conversation: list[unified_llm.Message] = list(ulm_messages)
            for i, step in enumerate(result.steps):
                conversation.append(step.response.message)
                # Add tool results for intermediate steps (loop continued)
                if i < len(result.steps) - 1:
                    for tr in step.tool_results:
                        conversation.append(
                            unified_llm.Message.tool_result(
                                tool_call_id=tr.tool_call_id,
                                content=tr.content
                                if isinstance(tr.content, str)
                                else str(tr.content),
                                is_error=tr.is_error,
                            )
                        )
            self._message_pools[thread_key] = conversation

        return outcome

    def _get_or_create_unified_client(self) -> Any:
        """Return the injected client or lazily create one from environment."""
        if self._unified_client is not None:
            return self._unified_client
        import unified_llm

        self._unified_client = unified_llm.Client.from_env()
        return self._unified_client

    async def _emit(self, event_name: str, data: dict[str, Any]) -> Any:
        """Emit an event via hooks, if provided.

        Returns the HookResult from hooks.emit(), or None if hooks is not set.
        """
        if self._hooks is not None:
            return await self._hooks.emit(event_name, data)
        return None


def _build_backend(
    providers: dict[str, Any],
    tools: dict[str, Any],
    hooks: Any,
    coordinator: Any | None,
    orchestrator_config: dict[str, Any] | None = None,
) -> Any | None:
    """Auto-construct a backend from the available providers.

    Resolution order:
    1. If coordinator exposes ``session.spawn`` \u2192 use AmplifierBackend
       (full "sessions all the way down").  Profiles are resolved from
       ``orchestrator_config["profiles"]`` or auto-discovered from
       ``coordinator.config["agents"]``.
    2. Else if at least one provider is available \u2192 use
       DirectProviderBackend (mini agentic tool loop per node).
    3. Otherwise \u2192 return None (codergen handler falls through to
       simulation mode).
    """
    first_provider = next(iter(providers.values()), None) if providers else None

    # Try the full spawn-based backend first
    if coordinator is not None:
        spawn_fn = None
        if hasattr(coordinator, "get_capability"):
            try:
                spawn_fn = coordinator.get_capability("session.spawn")
            except Exception:
                pass
        if spawn_fn is not None:
            from .backend import AmplifierBackend

            # Resolve profiles: explicit config > auto-discovery from agents
            cfg = orchestrator_config or {}
            profiles: dict[str, str] = {}

            # Source 1: Explicit profiles mapping in orchestrator config
            # e.g. config.profiles = {"anthropic": "attractor-anthropic"}
            explicit_profiles = cfg.get("profiles")
            if isinstance(explicit_profiles, dict):
                profiles.update(explicit_profiles)

            # Source 2: Auto-discover from coordinator.config["agents"]
            # Each agent entry is mapped as agent_name -> agent_name.
            if not profiles:
                coordinator_config = getattr(coordinator, "config", None) or {}
                agents = coordinator_config.get("agents", {})
                for agent_name, agent_cfg in agents.items():
                    if isinstance(agent_cfg, dict):
                        profiles[agent_name] = agent_name

            if profiles:
                logger.info(
                    "Using AmplifierBackend (session.spawn available, profiles=%s)",
                    list(profiles.keys()),
                )
            else:
                logger.warning(
                    "Using AmplifierBackend but profiles dict is empty. "
                    "Pipeline nodes may fail to resolve agent profiles. "
                    "Add 'profiles' to orchestrator config or 'agents' "
                    "to the bundle."
                )

            return AmplifierBackend(
                coordinator,
                profiles=profiles,
                provider=first_provider,
                tools=tools,
                hooks=hooks,
            )

    # Fall back to direct provider tool loop
    if first_provider is not None:
        logger.info("Using DirectProviderBackend (direct provider tool loop)")
        return DirectProviderBackend(first_provider, tools, hooks, coordinator)

    logger.warning(
        "No providers available \u2014 codergen nodes will run in simulation mode"
    )
    return None


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the loop-pipeline orchestrator.

    Config options:
        dot_source: Inline DOT digraph string.
        dot_file: Path to a .dot file.
    """
    cfg = config or {}
    orchestrator = PipelineOrchestrator(cfg)
    await coordinator.mount("orchestrator", orchestrator)
    logger.info("loop-pipeline orchestrator mounted")


class PipelineOrchestrator:
    """DOT graph-driven pipeline orchestrator.

    Parses a DOT digraph and walks it node-by-node, executing handlers
    for each node type and selecting edges based on outcomes.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: Any,
        **kwargs: Any,
    ) -> str:
        """Execute the pipeline.

        Parses the DOT graph, validates it, and walks from start to exit.

        Returns a JSON string with the pipeline outcome.
        """
        # 1. Get DOT source
        dot_source = self._resolve_dot_source()

        # 2. Parse the DOT graph
        graph = parse_dot(dot_source)

        # 3. Create pipeline context with goal from the prompt
        pipeline_context = PipelineContext()
        if prompt:
            pipeline_context.set("graph.goal", prompt)

        # Set params for $param expansion in transforms
        params = self.config.get("params")
        if params:
            pipeline_context.set("graph.params_values", params)

        # 4. Apply transforms (variable expansion, stylesheet) before validation
        apply_transforms(graph, pipeline_context)

        # 5. Validate the (transformed) graph
        validate_or_raise(graph)

        # 6. Set up logs directory
        logs_root = self.config.get(
            "logs_root", os.path.join(tempfile.gettempdir(), "attractor-pipeline")
        )
        os.makedirs(logs_root, exist_ok=True)

        # 6b. Write the DOT source for dashboard visualization
        dot_path = os.path.join(logs_root, "graph.dot")
        with open(dot_path, "w") as f:
            f.write(dot_source)

        # 7. Resolve backend: explicit kwarg \u2192 auto-construct from providers
        coordinator = kwargs.get("coordinator")
        backend = kwargs.get("backend")
        if backend is None:
            backend = _build_backend(providers, tools, hooks, coordinator, self.config)

        # 7b. Environment setup (if configured)
        env_config: dict[str, Any] | None = self.config.get("execution_environment")
        container_id = None
        env_instance_name = "pipeline-workspace"  # default for teardown
        if env_config:
            env_instance_name = env_config.get("name", "pipeline-workspace")
            if "env_create" in tools:
                env_create_args = dict(env_config)  # copy to avoid mutating config
                env_create_args.setdefault("type", "docker")
                env_create_args.setdefault("name", "pipeline-workspace")
                result = await tools["env_create"].execute(env_create_args)
                try:
                    parsed = json.loads(result.output)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "env_create returned unparseable output: %s", result.output
                    )
                    parsed = {}
                container_id = parsed.get("container_id")
                if container_id:
                    pipeline_context.set("internal.env_container_id", container_id)
                    pipeline_context.set(
                        "internal.env_type", env_config.get("type", "docker")
                    )
                    logger.info(
                        "Execution environment created: %s (container_id=%s)",
                        env_instance_name,
                        container_id,
                    )
                else:
                    logger.warning(
                        "env_create succeeded but returned no container_id "
                        "— falling back to local execution"
                    )
            else:
                logger.warning(
                    "execution_environment configured but env_create tool not "
                    "available (env-all bundle not composed?) — falling back "
                    "to local execution"
                )

        # 8. Create registry (no closures, no rewire — engine passes self at call time)
        registry = HandlerRegistry(
            HandlerContext(
                backend=backend,
                hooks=hooks,
            )
        )

        # 9. Create engine (carries itself to handlers via execute(engine=...))
        engine = PipelineEngine(
            graph=graph,
            context=pipeline_context,
            handler_registry=registry,
            logs_root=logs_root,
            hooks=hooks,
        )

        # 10. Run the engine (with environment teardown in finally)
        try:
            outcome = await engine.run(goal=prompt or None)
        finally:
            # Environment teardown
            if container_id and "env_destroy" in tools:
                try:
                    await tools["env_destroy"].execute({"instance": env_instance_name})
                    logger.info(
                        "Execution environment destroyed: %s",
                        env_instance_name,
                    )
                except Exception:
                    logger.exception(
                        "Failed to destroy execution environment %s "
                        "— container may need manual cleanup",
                        env_instance_name,
                    )

        # 12. Build a meaningful summary from all completed nodes
        summary = self._build_pipeline_summary(engine, outcome)

        # 13. Return the final outcome as JSON
        result = {
            "status": outcome.status.value,
            "notes": summary,
            "failure_reason": outcome.failure_reason,
            "nodes_completed": len(engine.completed_nodes),
            "node_statuses": {
                nid: engine.node_outcomes[nid].status.value
                for nid in engine.completed_nodes
                if nid in engine.node_outcomes
            },
        }
        return json.dumps(result)

    def _build_pipeline_summary(self, engine: PipelineEngine, outcome: Outcome) -> str:
        """Build a human-readable pipeline summary.

        If the final outcome has meaningful notes, use them.
        Otherwise, synthesize a summary from all completed nodes.
        """
        # Use the outcome's notes if they exist and are meaningful
        if outcome.notes and len(outcome.notes) > 20:
            return outcome.notes

        # Synthesize from all node outcomes
        parts: list[str] = []
        total = len(engine.completed_nodes)
        succeeded = sum(
            1
            for nid in engine.completed_nodes
            if nid in engine.node_outcomes and engine.node_outcomes[nid].is_success
        )
        failed = total - succeeded

        parts.append(f"Pipeline completed: {succeeded}/{total} nodes succeeded.")

        if failed:
            failed_nodes = [
                nid
                for nid in engine.completed_nodes
                if nid in engine.node_outcomes
                and not engine.node_outcomes[nid].is_success
            ]
            parts.append(f"Failed nodes: {', '.join(failed_nodes)}.")

        # Include the last node's notes if available
        if engine.completed_nodes:
            last_id = engine.completed_nodes[-1]
            last_out = engine.node_outcomes.get(last_id)
            if last_out and last_out.notes:
                # Truncate to avoid bloating the summary
                snippet = last_out.notes[:300]
                parts.append(f"Last node ({last_id}): {snippet}")

        return " ".join(parts)

    def _resolve_dot_source(self) -> str:
        """Resolve DOT source from config (inline or file)."""
        dot_source = self.config.get("dot_source")
        if dot_source:
            return dot_source

        dot_file = self.config.get("dot_file")
        if dot_file:
            with open(dot_file) as f:
                return f.read()

        raise ValueError(
            "No DOT source configured. Set 'dot_source' or 'dot_file' in config."
        )
