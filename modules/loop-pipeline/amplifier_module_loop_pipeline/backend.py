"""AmplifierBackend — CodergenBackend adapter using session spawning.

This is the "sessions all the way down" integration point. When the
pipeline engine hits a codergen node, the CodergenHandler calls this
backend, which spawns a coding agent sub-session via the Amplifier
``session.spawn`` capability.

When session.spawn is not available, falls back to a direct provider
mini tool loop that calls LLM → execute tool calls → repeat until the
model returns a text-only response.

Spec coverage: Section 4.5 (CodergenBackend Interface), Section 1.4,
               FID-001–010, Section 5.4.

fidelity=full continuity (see docs/designs/fidelity-full-session-continuity.md):
  ``_thread_transcripts`` maps a branch-local thread_key to a list of
  (node_id, instruction, output) triples — the accumulated node-exchange
  history for that thread.  After each ``full`` node, the exchange is
  appended (truncating any stale tail first, for goal-gate-retry
  idempotency).  On the next same-thread ``full`` node the history is
  converted to a ``parent_messages`` list (user/assistant roles) and
  passed to a FRESH spawn — never a session_id re-pass.  This removes
  the type confusion (id-where-a-conversation-belongs) that caused the
  continuity bug.

  Thread_id is branch-local: ``clone()`` resets ``_thread_transcripts``
  so parallel branches each start with a fresh transcript even when they
  share an explicit ``thread_id``.  See EXTENSIONS.md §12–13.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, overload

try:
    from amplifier_foundation import ProviderPreference as _ProviderPreference
except ImportError:

    class _ProviderPreference:  # type: ignore[no-redef]
        """Placeholder raised when amplifier_foundation is not installed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ImportError(
                "amplifier_foundation is required for ProviderPreference but is not installed. "
                "Install it with: pip install amplifier-foundation"
            )


from .context import PipelineContext
from .fidelity import build_preamble, resolve_fidelity, resolve_thread_key
from .graph import Edge, Graph, Node
from .outcome import Outcome, StageStatus
from .hook_bridge import _current_node_context, set_node_context
from .pipeline_events import (
    MODEL_RESOLVED,
    PROVIDER_ERROR,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)

logger = logging.getLogger(__name__)

# Map StageStatus value strings to enum members for parsing
_STATUS_MAP: dict[str, StageStatus] = {s.value: s for s in StageStatus}

# Maximum rounds for the direct tool loop fallback
_MAX_TOOL_LOOP_ROUNDS = 20


class AmplifierBackend:
    """CodergenBackend implementation using Amplifier session spawning.

    Resolves the provider profile from node attributes, spawns a child
    coding agent session, and parses the outcome from the response.

    Supports two execution paths:
    - **Path A (spawn)**: If ``session.spawn`` is available, delegates to
      a full child session with the complete tool loop.
    - **Path B (direct tool loop)**: If spawn is unavailable but a provider
      and tools are available, runs a mini agentic loop directly
      (LLM call → tool execution → repeat).

    Supports fidelity-based context control:
    - ``full``: Reuses sessions via a thread-keyed session pool.
    - ``compact``/``truncate``/``summary:*``: Fresh session with preamble.
    """

    def __init__(
        self,
        coordinator: Any,
        profiles: dict[str, str],
        provider: Any | None = None,
        tools: dict[str, Any] | None = None,
        unified_client: Any | None = None,
        hooks: Any | None = None,
    ) -> None:
        """Initialize the backend.

        Args:
            coordinator: Amplifier coordinator with session.spawn capability.
            profiles: Map of provider name to profile/bundle name.
                      e.g. {"anthropic": "attractor-anthropic", ...}
            provider: Optional LLM provider for direct tool loop fallback.
                      Used as a truthiness flag to enable Path B.
            tools: Optional tool dict for direct tool loop fallback.
            unified_client: Optional ``unified_llm.Client`` for LLM calls.
                            Created lazily via ``Client.from_env()`` if not provided.
            hooks: Optional HookRegistry for emitting provider-level events.
        """
        self._coordinator = coordinator
        self._profiles = profiles
        self._provider = provider
        self._tools = tools or {}
        self._unified_client = unified_client
        self._hooks = hooks
        self._spawn_fn: Any | None = None
        self._spawn_checked = False
        # _thread_transcripts: thread_key → list of (node_id, instruction, output) triples.
        # Replaces the former _session_pool (which stored a session_id — a type confusion:
        # an id where a conversation belongs).  Each triple represents one node-exchange
        # at user/assistant granularity.  Idempotent under goal-gate retries via
        # truncate-to-node-then-append (see _append_to_transcript).
        # Born branch-local: clone() resets to {} so parallel branches never share history.
        # See docs/designs/fidelity-full-session-continuity.md and EXTENSIONS.md §12–13.
        self._thread_transcripts: dict[str, list[tuple[str, str, str]]] = {}
        self._completed_nodes: dict[str, Outcome] = {}
        self._last_node_id: str | None = None

    def clone(self) -> AmplifierBackend:
        """Create a clone with shared immutable refs but fresh mutable state.

        Used for parallel branch isolation so concurrent branches don't
        corrupt each other's session pools or completion tracking.
        """
        new = AmplifierBackend.__new__(AmplifierBackend)
        # Shared immutable refs
        new._coordinator = self._coordinator
        new._profiles = self._profiles
        new._provider = self._provider
        new._unified_client = self._unified_client
        new._hooks = self._hooks

        # Copy tools: stateless tools are shared across clones (safe); stateful tools
        # (those exposing last_outcome) get an independent shallow copy with last_outcome
        # reset to None, so parallel branches start clean regardless of prior use.
        def _clone_tool(tool: Any) -> Any:
            # Detect stateful tools via explicit __dict__ inspection — not hasattr(),
            # which returns True for MagicMock and other proxy objects that fabricate
            # attributes dynamically.
            is_stateful = (
                # Instance attribute (e.g. ReportOutcomeTool sets self.last_outcome in __init__)
                "last_outcome" in getattr(tool, "__dict__", {})
                # Class attribute (e.g. _MockReportOutcomeTool defines last_outcome at class level)
                or any("last_outcome" in vars(cls) for cls in type(tool).__mro__)
            )
            if is_stateful:
                c = copy.copy(tool)
                c.last_outcome = None
                return c
            return tool

        new._tools = {k: _clone_tool(v) for k, v in self._tools.items()}
        # Inherit resolved spawn capability — the capability is a stateless
        # function from the shared _coordinator, so sharing the reference is as
        # safe as the clone already sharing _coordinator.  Inheriting prevents
        # concurrent first-resolution when N branch clones run under
        # asyncio.gather (each clone would otherwise race to call
        # _coordinator.get_capability("session.spawn") simultaneously, causing
        # some branches to receive None and fall to the tool-loop fallback).
        new._spawn_fn = self._spawn_fn
        new._spawn_checked = self._spawn_checked
        # Fresh mutable state — transcripts are born branch-local so that two
        # branches sharing the same thread_id maintain independent histories
        # (§3.8 isolation, EXTENSIONS.md §9 / §13).
        new._thread_transcripts = {}
        new._completed_nodes = {}
        new._last_node_id = None
        return new

    def ensure_spawn_resolved(self) -> None:
        """Resolve the session.spawn capability in place, once.

        Call this on the parent backend before creating branch clones via
        ``clone()``.  This guarantees that all clones inherit an already-
        resolved ``_spawn_fn`` (and ``_spawn_checked = True``) instead of
        performing a concurrent first-resolution when N branch engines each
        hit the lazy-check block in ``run()`` simultaneously under
        ``asyncio.gather``.

        Idempotent: safe to call multiple times; subsequent calls are no-ops.
        """
        if not self._spawn_checked:
            cap = self._coordinator.get_capability("session.spawn")
            if cap is not None:
                self._spawn_fn = cap
            self._spawn_checked = True

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge: Edge | None = None,
        graph: Graph | None = None,
    ) -> Outcome:
        """Execute a coding task by spawning a child session.

        Falls back to a direct provider tool loop when session.spawn is
        not available.

        Args:
            node: The pipeline node being executed.
            prompt: The expanded prompt string.
            context: The current pipeline context.
            incoming_edge: The edge leading to this node (for fidelity resolution).
            graph: The pipeline graph (for fidelity resolution).

        Returns:
            Outcome parsed from the child session's response.
        """
        # 1. Get spawn capability (lazy resolution, checked once)
        if not self._spawn_checked:
            cap = self._coordinator.get_capability("session.spawn")
            if cap is not None:
                self._spawn_fn = cap
            self._spawn_checked = True

        # 2. Resolve provider and profile from node attributes
        provider = node.attrs.get("llm_provider", "anthropic")
        model = node.attrs.get("llm_model")
        model = await _resolve_concrete_model(provider, model, emit=self._emit)
        reasoning_effort = node.attrs.get("reasoning_effort")
        max_agent_turns_raw = node.attrs.get("max_agent_turns")
        max_agent_turns = (
            int(max_agent_turns_raw) if max_agent_turns_raw is not None else None
        )
        profile_name = self._profiles.get(
            provider, next(iter(self._profiles.values()), "")
        )

        # 3. Resolve fidelity mode (spec FID-001–010)
        if graph is not None:
            fidelity = resolve_fidelity(node, incoming_edge, graph)
        else:
            # Fallback when graph not provided (backward compat)
            fidelity = node.attrs.get("fidelity", "compact")

        # CR-1 loud guard (silent-continuity-loss class): a fidelity=full node
        # needs `graph` to resolve its thread key and drive the transcript
        # store/read.  If `full` continuity is requested but `graph` is missing,
        # the store/read gates below would silently skip — exactly the dead-code
        # bug a live DTU run exposed (seeds wrote codewords, recall came back
        # empty because CodergenHandler.execute dropped `graph`).  Warn loudly so
        # a future caller that drops `graph` fails visibly instead of silently
        # losing continuity.  Scoped to the `full` path only: non-full nodes and
        # legitimately thread-less nodes never need a graph and must not warn.
        if fidelity == "full" and graph is None:
            logger.warning(
                "Node %s requested fidelity=full continuity but no graph was "
                "passed to backend.run() — the thread key cannot be resolved, so "
                "conversation continuity will NOT be honored for this node. The "
                "caller (handler/engine) must forward `graph`. See "
                "docs/designs/fidelity-full-session-continuity.md.",
                node.id,
            )

        # 4. Build the instruction with preamble for non-full modes
        if fidelity == "full":
            instruction = prompt
        else:
            preamble = build_preamble(fidelity, context, self._completed_nodes)
            instruction = f"{preamble}\n\n---\n\n{prompt}" if preamble else prompt

        # 5. Inject human gate response if present (consume-once)
        #
        # When a freeform hexagon gate precedes this node, the human's text
        # is stored in context as "human.gate.text".  We prepend it to the
        # instruction so it becomes part of the user message in the session's
        # conversation history.  With fidelity=full and session reuse, the
        # instruction IS a durable user turn in the persistent session record
        # — all future nodes on the same thread inherit it.
        gate_text = context.get("human.gate.text")
        if gate_text is not None:
            # Consume-once: always clear after the first LLM node following a
            # human gate, regardless of whether the text was empty.
            context.set("human.gate.text", None)
            if gate_text:  # Only inject if the human actually typed something
                gate_label = context.get("human.gate.label", "")
                gate_section = (
                    f'Human response at gate "{gate_label}":\n{gate_text}\n\n---\n\n'
                )
                instruction = gate_section + instruction

        # 6. Route to Path A (spawn) or Path B (direct tool loop)
        if self._spawn_fn is not None:
            outcome = await self._run_with_spawn(
                node,
                instruction,
                provider,
                model,
                reasoning_effort,
                max_agent_turns,
                profile_name,
                fidelity,
                incoming_edge,
                graph,
                context,
            )
            # When _run_with_spawn returns here, it either extracted an outcome
            # from the child's output / report_outcome / status, or it returned
            # Outcome(FAIL) so the engine can route via FAIL-edge → retry_target /
            # goal_gate.  No silent in-process fallback occurs inside that method.
        elif self._provider is not None:
            outcome = await self._run_with_tool_loop(
                node,
                instruction,
                reasoning_effort,
                max_agent_turns,
            )
        else:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=(
                    "Neither session.spawn nor a direct provider is "
                    "available — cannot execute node"
                ),
            )

        # Record completed node outcome for future preambles
        self._completed_nodes[node.id] = outcome
        self._last_node_id = node.id

        return outcome

    # ------------------------------------------------------------------
    # Path A: Full child session via session.spawn
    # ------------------------------------------------------------------

    async def _run_with_spawn(
        self,
        node: Node,
        instruction: str,
        provider: str,
        model: str | None,
        reasoning_effort: str | None,
        max_agent_turns: int | None,
        profile_name: str,
        fidelity: str,
        incoming_edge: Edge | None,
        graph: Graph | None,
        context: PipelineContext | None = None,
    ) -> Outcome:
        """Spawn a full child session via the CLI's session.spawn capability."""
        assert self._spawn_fn is not None  # guaranteed by caller

        # EXT-23: response_schema requires direct LLM generation; structured
        # output cannot be threaded through the spawned-agent protocol yet.
        if node.response_schema is not None:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=(
                    "response_schema is only supported on direct-LLM nodes "
                    "(not spawned-agent nodes) yet. "
                    "Either use a backend without session.spawn (e.g., "
                    "DirectProviderBackend) or remove response_schema from "
                    f"node '{node.id}'."
                ),
            )

        # Obtain parent_session from coordinator
        parent_session = getattr(self._coordinator, "session", None)

        # Obtain agent_configs from coordinator config
        coordinator_config = getattr(self._coordinator, "config", None) or {}
        agent_configs: dict[str, Any] = coordinator_config.get("agents", {})

        # FAIL-LOUD GUARD: detect agent config that would cause loop-pipeline to recurse.
        #
        # The spawn capability resolves the child's orchestrator by calling
        # merge_configs(parent.config, agent_configs[profile_name]).  It merges only
        # 'session:', 'providers:', 'tools:', and similar mount-plan keys — no external
        # references are resolved or loaded.
        #
        # Two conditions both cause the child to re-enter loop-pipeline:
        #
        #   (a) session.orchestrator.module is absent or None  → child inherits the
        #       parent's loop-pipeline orchestrator and re-executes the same DOT graph.
        #   (b) session.orchestrator.module is "loop-pipeline" → child IS loop-pipeline
        #       and re-executes the same DOT graph.
        #
        # Both were observed as 9,854-session infinite recursion (0 LLM calls, no
        # artifact produced).
        #
        # Fix: add an inline session.orchestrator with a non-pipeline module (e.g.
        # loop-agent) to the agent entry in your pipeline profile or bundle config.
        _agent_cfg_for_node: dict[str, Any] = agent_configs.get(profile_name) or {}
        _effective_orch_module: str | None = (
            (_agent_cfg_for_node.get("session") or {})
            .get("orchestrator", {})
            .get("module")
        )
        if _effective_orch_module is None or _effective_orch_module == "loop-pipeline":
            raise ValueError(
                f"loop-pipeline recursion guard: agent '{profile_name}' has "
                f"session.orchestrator.module={_effective_orch_module!r}. "
                f"The child would inherit or re-enter loop-pipeline, causing "
                f"infinite recursion. "
                f"Fix: add an inline session.orchestrator (non-pipeline, e.g. "
                f"loop-agent) to the '{profile_name}' agent definition in your "
                f"pipeline profile or bundle config."
            )

        # Build spawn kwargs matching the CLI spawn_capability signature
        spawn_kwargs: dict[str, Any] = {
            "agent_name": profile_name,
            "instruction": instruction,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
            # orchestrator_config: pass only non-None values so that loop-agent's
            # numeric comparisons (e.g. max_turns > 0) don't receive None and
            # throw TypeError.  Omitting a key lets the child orchestrator use its
            # own default, which is always safer than injecting None.
            "orchestrator_config": {
                k: v
                for k, v in {
                    "reasoning_effort": reasoning_effort,
                    "max_turns": max_agent_turns,
                }.items()
                if v is not None
            },
        }
        if model:
            spawn_kwargs["provider_preferences"] = [
                _ProviderPreference(provider=provider, model=model)
            ]

        # Inject shared execution environment attachment for child session
        if context is not None:
            container_id = context.get("internal.env_container_id")
            env_type = context.get("internal.env_type")
            if container_id:
                spawn_kwargs["tools"] = spawn_kwargs.get("tools", []) + [
                    {
                        "module": "tools-env-all",
                        "config": {
                            "auto_attach": {
                                "type": env_type,
                                "name": "pipeline-workspace",
                                "attach_to": container_id,
                            }
                        },
                    }
                ]

        # fidelity=full: resolve thread_key once for both pre-spawn history injection
        # and post-spawn transcript append.
        #
        # The former _session_pool stored a session_id and re-passed it as
        # sub_session_id — a type confusion (an id where a conversation belongs).
        # The fix: carry the actual node-exchange history in _thread_transcripts and
        # pass it as parent_messages to a FRESH spawn.  Foundation injects it via
        # set_messages before the child session runs.
        #
        # Mutual-exclusion invariant: for a full-fidelity carry, parent_messages and
        # sub_session_id are NEVER both present — parent_messages drives continuity;
        # sub_session_id is never set here.  The assert below enforces this so that
        # any future code path that accidentally re-introduces the old mechanism will
        # fail loudly rather than silently dropping history (the original symptom).
        thread_key: str | None = None
        if fidelity == "full" and graph is not None:
            thread_key = resolve_thread_key(
                node, incoming_edge, graph, self._last_node_id
            )
            prior_messages = self._get_parent_messages_for_thread(thread_key)
            if prior_messages:
                spawn_kwargs["parent_messages"] = prior_messages

        # CR-1 guard: parent_messages and sub_session_id must never coexist.
        # (sub_session_id is not set by this method for full-fidelity, so this
        # fires only if a caller or future patch accidentally introduces it.)
        assert not (
            spawn_kwargs.get("parent_messages") and spawn_kwargs.get("sub_session_id")
        ), (
            "BUG: parent_messages and sub_session_id cannot both be set for a "
            "full-fidelity spawn.  parent_messages drives continuity; "
            "sub_session_id re-passes a session identity and would cause "
            "foundation to silently drop the injected history."
        )

        # Spawn the child session
        try:
            result = await self._spawn_fn(**spawn_kwargs)
        except Exception as e:
            # Infrastructure failure: the spawn mechanism itself broke (e.g.
            # agent profile not found, session init error).  Return FAIL so the
            # engine can route via the spec's FAIL-edge (retry_target / goal_gate)
            # rather than silently re-running the node in a different harness.
            logger.warning("Spawn failed for node %s: %s", node.id, e)
            return Outcome(status=StageStatus.FAIL, failure_reason=str(e))

        # Parse outcome from result.
        # If the child produced output, _parse_outcome determines the outcome —
        # including intentional {"status":"fail"} verdicts from goal_gate nodes.
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        if not output.strip():
            # The child's FINAL assistant message was empty — but that does NOT
            # mean the child failed.  A child that did its work via tool calls
            # (writing pages, then a terminal report_outcome) and ended on a tool
            # call legitimately has no closing prose.  Before falling back, honor
            # the SAME outcome sources the direct tool loop already uses: the
            # child's report_outcome args and the orchestrator's completion
            # status (captured in the spawn result, see _prepared.py spawn()).
            spawn_outcome = _outcome_from_spawn_result(result)
            if spawn_outcome is not None:
                session_id = (
                    result.get("session_id") if isinstance(result, dict) else None
                )
                if session_id:
                    spawn_outcome.session_id = session_id
                return spawn_outcome

            # Genuinely empty: no text, no report_outcome, no success status.
            # Return FAIL so the engine can route via the spec's FAIL-edge
            # (retry_target / goal_gate) rather than silently re-running the
            # node in a materially different in-process harness.
            logger.warning(
                "Node %s: spawn returned empty output with no recoverable outcome.",
                node.id,
            )
            return Outcome(
                status=StageStatus.FAIL,
                notes="No output from child session",
                failure_reason="Empty spawn output",
            )

        outcome = _parse_outcome(output)

        # Capture session_id from spawn result for status.json observability.
        # session_id is kept on the Outcome for telemetry/debugging — it no longer
        # drives continuity (that role belongs to _thread_transcripts).
        session_id = result.get("session_id") if isinstance(result, dict) else None
        if session_id:
            outcome.session_id = session_id

        # Append this node's exchange to the thread transcript (full fidelity only).
        # Uses truncate-to-node-then-append for idempotency: if this node is being
        # re-run (e.g., after a goal-gate retry), its prior turn is replaced rather
        # than duplicated.  See _append_to_transcript for the algorithm.
        if fidelity == "full" and graph is not None and thread_key is not None:
            self._append_to_transcript(thread_key, node.id, instruction, output)

        return outcome

    # ------------------------------------------------------------------
    # Path B: Direct provider mini tool loop (spawn-unavailable path)
    # ------------------------------------------------------------------

    async def _run_with_tool_loop(
        self,
        node: Node,
        instruction: str,
        reasoning_effort: str | None,
        max_agent_turns: int | None = None,
    ) -> Outcome:
        """Execute via unified_llm.generate() (no child session).

        Delegates the full agentic tool loop to the unified-llm-client
        library, which handles LLM calls, tool execution, retry, and
        error mapping internally.

        EXT-23: When ``node.response_schema`` is set, passes a
        ``ResponseFormat(type="json_schema", ...)`` to ``generate()`` and
        returns the raw JSON text as the node output (SUCCESS outcome).
        """
        import unified_llm

        client = self._get_or_create_unified_client()
        provider_name = node.llm_provider or node.attrs.get("llm_provider", "anthropic")
        model = await _resolve_concrete_model(
            provider_name, _resolve_model(node), emit=self._emit
        )
        tools = _build_unified_tools(self._tools)

        # EXT-23: Build response_format when response_schema is set
        response_format: Any = None
        if node.response_schema is not None:
            response_format = unified_llm.ResponseFormat(
                type="json_schema",
                json_schema=node.response_schema,
                strict=True,
            )

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
                    "message_count": 1,  # prompt-only = 1 message
                },
            )

            # Check for deny action from hooks (e.g., approval gates)
            if (
                pre_result is not None
                and getattr(pre_result, "action", "continue") == "deny"
            ):
                reason = getattr(pre_result, "reason", None) or "Denied by hook"
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Denied by hook: {reason}",
                )

            result = await unified_llm.generate(
                model=model,
                prompt=instruction,
                tools=tools or None,
                max_tool_rounds=max_agent_turns
                if max_agent_turns is not None
                else _MAX_TOOL_LOOP_ROUNDS,
                reasoning_effort=reasoning_effort,
                provider=provider_name,
                client=client,
                response_format=response_format,
            )
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

        # EXT-23: Structured output mode — result.text is the JSON response, not an Outcome.
        # Skip Outcome-parsing logic entirely; stash the JSON as the node output.
        if node.response_schema is not None:
            raw_json = result.text or ""
            # Anthropic tool-extraction path: structured output lives in the
            # __structured_output__ tool call arguments, not in result.text.
            # Recover the JSON string when result.text is empty and a tool call is present.
            if not raw_json.strip() and result.tool_calls:
                _STRUCT_TOOL = "__structured_output__"
                for _tc in result.tool_calls:
                    if _tc.name == _STRUCT_TOOL:
                        _args = _tc.arguments
                        raw_json = (
                            json.dumps(_args)
                            if isinstance(_args, dict)
                            else (str(_args) if _args else "")
                        )
                        break
            parsed_obj: Any = None
            if raw_json.strip():
                try:
                    parsed_obj = json.loads(raw_json)
                except json.JSONDecodeError:
                    pass
            ctx_updates: dict[str, Any] = {
                "last_stage": node.id,
                "last_response": raw_json[:200],
            }
            if parsed_obj is not None:
                ctx_updates[node.id] = parsed_obj
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=raw_json,
                context_updates=ctx_updates,
            )

        # Map GenerateResult → Outcome
        #
        # Priority order (see issue #238):
        #   1. result.text contains JSON-like content → authoritative, use it
        #   2. result.text is plain prose or empty → fall back to report_outcome tool call args
        #      (extracted from result.steps — immutable, race-free; avoids the last_outcome
        #       shared-state bug when backend instances are cloned for parallel branches)
        #   3. No tool call either → plain prose → SUCCESS (spec 4.5), or empty → SUCCESS
        if result.text:
            stripped = result.text.strip()
            _fence_match = re.match(
                r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.DOTALL
            )
            if bool(_fence_match) or stripped.startswith("{"):
                return _parse_outcome(result.text)

        # Text is plain prose or empty — check if report_outcome was called
        lo = _find_report_outcome_call(result)
        if lo is not None:
            return Outcome(
                status=_STATUS_MAP.get(lo.get("status"), StageStatus.FAIL),
                context_updates=lo.get("context_updates"),
                failure_reason=lo.get("failure_reason"),
                preferred_label=lo.get("preferred_label"),
                suggested_next_ids=lo.get("suggested_next_ids"),
                notes=lo.get("notes"),
            )

        if result.text:
            return _parse_outcome(result.text)  # plain prose → SUCCESS per spec 4.5
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Stage completed: {node.id}",
        )

    # ------------------------------------------------------------------
    # _thread_transcripts helpers — fidelity=full continuity carrier
    # ------------------------------------------------------------------

    def _append_to_transcript(
        self,
        thread_key: str,
        node_id: str,
        instruction: str,
        output: str,
    ) -> None:
        """Append a node's (instruction, output) exchange to the thread transcript.

        Implements **truncate-to-node-then-append** for goal-gate-retry
        idempotency: if this node already has a turn in the transcript (from a
        prior attempt in the same run), all turns from that node onwards are
        removed before the new turn is appended.  This means a re-run node
        *replaces* its prior exchange rather than duplicating it.

        Algorithm:
            1. Scan the current transcript for the first tuple whose node_id
               matches the incoming node_id.
            2. If found at position i: truncate the list to the first i entries
               (discarding that node and all subsequent nodes' entries).
            3. Append the new triple (node_id, instruction, output).

        Called with role=user/assistant only (system/developer roles are
        stripped at this layer, matching app-cli behavior).

        Thread_id is branch-local (EXTENSIONS.md §13): ``clone()`` resets
        ``_thread_transcripts`` so sibling parallel branches each maintain
        independent transcripts even when they share an explicit thread_id.

        Note on sequentiality (§3.8 / design §2):
            Same-thread-key full nodes within a single branch always run
            sequentially (the engine while-loop is sequential; parallel
            branches each receive an isolated backend clone).  No asyncio
            lock is required.
        """
        turns = self._thread_transcripts.get(thread_key, [])
        # Truncate from the first occurrence of this node_id, replacing any
        # stale tail left by a prior attempt of the same node or later nodes.
        for i, (nid, _, _) in enumerate(turns):
            if nid == node_id:
                turns = turns[:i]
                break
        turns.append((node_id, instruction, output))
        self._thread_transcripts[thread_key] = turns

    def _get_parent_messages_for_thread(self, thread_key: str) -> list[dict[str, Any]]:
        """Return the accumulated conversation history for a thread as a flat
        ``parent_messages`` list (user/assistant dicts).

        Each stored triple ``(node_id, instruction, output)`` expands to two
        messages:
            {"role": "user",      "content": instruction}
            {"role": "assistant", "content": output}

        Returns an empty list if the thread has no prior exchanges (first node
        on the thread — no parent_messages will be set in that case).
        """
        messages: list[dict[str, Any]] = []
        for _, instr, out in self._thread_transcripts.get(thread_key, []):
            messages.append({"role": "user", "content": instr})
            messages.append({"role": "assistant", "content": out})
        return messages

    def _get_or_create_unified_client(self) -> Any:
        """Return the injected client or lazily create one from environment."""
        if self._unified_client is not None:
            return self._unified_client
        import unified_llm

        self._unified_client = unified_llm.Client.from_env()
        return self._unified_client

    async def close(self) -> None:
        """Release the cached unified LLM client (spec finalize contract).

        The fallback path lazily creates a ``unified_llm.Client`` wrapping an
        ``AsyncAnthropic``/httpx client bound to the running event loop.  Under
        the per-article ``asyncio.run()`` lifecycle, that client must be closed
        WITHIN its loop; otherwise GC later runs ``aclose()`` on a dead loop,
        raising ``RuntimeError: Event loop is closed``.  This method is called
        by the orchestrator's finalize path.

        Idempotent and safe: a no-op when no client was created, and tolerant of
        clients that do not expose ``close()``.
        """
        client = self._unified_client
        if client is None:
            return
        close_fn = getattr(client, "close", None)
        if close_fn is not None:
            await close_fn()
        self._unified_client = None

    async def _emit(self, event_name: str, data: dict[str, Any]) -> Any:
        """Emit an event via hooks, if provided.

        Returns the HookResult from hooks.emit(), or None if hooks is not set.
        Unlike the engine's fire-and-forget _emit, this returns the result
        so callers can inspect the action (deny, modify, etc.).
        """
        if self._hooks is not None:
            return await self._hooks.emit(event_name, data)
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _resolve_model(node: Node) -> str:
    """Resolve the LLM model identifier from a pipeline node.

    Requires an explicit ``llm_model`` attribute on the node.  No silent
    fallback to deprecated or hardcoded defaults — every pipeline that
    uses the direct tool loop must declare its model explicitly.

    Args:
        node: The pipeline node to resolve a model for.

    Returns:
        The explicit model identifier from ``node.llm_model``.

    Raises:
        ValueError: If neither ``node.llm_model`` nor ``attrs["llm_model"]``
            is set.  The pipeline author must supply a model explicitly.
    """
    if node.llm_model:
        return node.llm_model
    raise ValueError(
        f"Node '{node.id}' requires an explicit 'llm_model' attribute. "
        f'Set llm_model="<model-name>" in the node\'s DOT attributes or '
        f"via the pipeline's model_stylesheet. "
        f"No default model is provided — this prevents silently running "
        f"against a deprecated or unintended model."
    )


# ---------------------------------------------------------------------------
# Live model-token resolution (family token / glob -> concrete served id)
# ---------------------------------------------------------------------------
# Mirrors the proven wiki-weaver shim (wiki_weaver/model_resolver.py): an
# explicit id is returned unchanged with NO network call; a family token or a
# glob is resolved live against the provider's own served list via
# unified_llm.resolve_latest_for, which closes the id-seam (lister and
# generator share one adapter). Fail-loud: no match -> ValueError propagates.

# The ONE place to extend family-name support. Exact, case-insensitive token
# match only -- a concrete id that merely CONTAINS "sonnet"
# (e.g. "claude-sonnet-4-5") is NOT a family token and passes through unchanged.
_FAMILY_TOKENS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# Per-process cache: (provider, raw_token) -> concrete served id. A given
# pattern resolves at most once per run, so a run is deterministic and the
# resolved id is stable across every node/loop iteration that uses it.
_MODEL_RESOLUTION_CACHE: dict[tuple[str, str], str] = {}


def _is_model_pattern(model: str) -> bool:
    """True when *model* must be resolved (glob chars OR a known family token).

    A concrete id (no glob chars, not a family token) returns False and is
    passed straight through -- zero behavior change, no network call.
    """
    if any(ch in model for ch in "*?["):
        return True
    return model.strip().lower() in _FAMILY_TOKENS


@overload
async def _resolve_concrete_model(
    provider: str, model: str, *, emit: Any = None
) -> str: ...
@overload
async def _resolve_concrete_model(
    provider: str, model: str | None, *, emit: Any = None
) -> str | None: ...
async def _resolve_concrete_model(
    provider: str, model: str | None, *, emit: Any = None
) -> str | None:
    """Resolve a node's ``llm_model`` token to a concrete served model id.

    - ``None``/empty  -> returned unchanged (spawn path tolerates a missing
      model; the direct paths have already fail-loud'd via ``_resolve_model``).
    - concrete id     -> returned unchanged, NO network call (full back-compat).
    - glob / family   -> resolved live via ``unified_llm.resolve_latest_for``
      and cached per ``(provider, token)`` so the run resolves once.

    Fail-loud: a no-match / unresolvable / missing-adapter condition raises
    ``ValueError`` from the resolver -- never a silent default.
    """
    if not model or not _is_model_pattern(model):
        return model

    cache_key = (provider, model)
    cached = _MODEL_RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # lazy import, matching the existing import idiom in this module
    from unified_llm import resolve_latest_for

    token = model.strip().lower()
    pattern = f"*{token}*" if token in _FAMILY_TOKENS else model
    concrete = await resolve_latest_for(provider, pattern, stable_only=True)

    _MODEL_RESOLUTION_CACHE[cache_key] = concrete
    logger.info(
        "loop-pipeline resolved llm_model %r -> %r (provider=%s, pattern=%s)",
        model,
        concrete,
        provider,
        pattern,
    )
    # Emit the resolution as a pipeline event so the run's event stream records
    # exactly which concrete model a pattern/family token resolved to (audit /
    # eval reproducibility). Fires once per distinct resolution (cache miss).
    if emit is not None:
        await emit(
            MODEL_RESOLVED,
            {
                "raw": model,
                "resolved": concrete,
                "provider": provider,
                "pattern": pattern,
            },
        )
    return concrete


def _make_tool_handler(pipeline_tool: Any) -> Any:
    """Create a unified_llm-compatible execute handler from a pipeline tool.

    Pipeline tools expect ``execute(input: dict)``.
    unified_llm calls ``tool.execute(**kwargs)``.
    This wrapper bridges the two conventions.
    """

    async def handler(**kwargs: Any) -> str:
        result = await pipeline_tool.execute(kwargs)
        if hasattr(result, "output"):
            return result.output
        return str(result)

    return handler


def _build_unified_tools(pipeline_tools: dict[str, Any]) -> list[Any]:
    """Convert pipeline tools to unified_llm.Tool objects."""
    import unified_llm

    tools: list[Any] = []
    for tool in pipeline_tools.values():
        schema = (
            getattr(tool, "parameters", None)
            or getattr(tool, "schema", None)
            or getattr(tool, "input_schema", None)  # ReportOutcomeTool exposes this
        )
        if schema is None:
            schema = {"type": "object", "properties": {}}

        execute_fn = None
        if hasattr(tool, "execute"):
            execute_fn = _make_tool_handler(tool)

        tools.append(
            unified_llm.Tool(
                name=getattr(tool, "name", str(tool)),
                description=getattr(tool, "description", ""),
                parameters=schema if isinstance(schema, dict) else {},
                execute=execute_fn,
            )
        )
    return tools


def _find_report_outcome_call(result: Any) -> dict[str, Any] | None:
    """Return report_outcome call arguments from generate() result steps, or None.

    Walks result.steps[i].tool_calls (each StepResult carries the tool calls
    for that LLM exchange).  Using the immutable step record avoids the
    ReportOutcomeTool.last_outcome shared-state bug: backend.clone() shallow-
    copies self._tools, so parallel branches share the same tool object and
    would race on last_outcome.  result.steps is created fresh per generate()
    call and is never shared between branches.
    """
    for step in getattr(result, "steps", []) or []:
        for tc in getattr(step, "tool_calls", []) or []:
            if getattr(tc, "name", None) == "report_outcome":
                return getattr(tc, "arguments", {}) or {}
    return None


# Spawn-result status strings that count as a real, non-failing completion.
# These map 1:1 to non-FAIL StageStatus members; any other string (e.g.
# "error", "", or a missing status) is treated as "no success signal".
_SPAWN_SUCCESS_STATUSES = frozenset(
    {
        StageStatus.SUCCESS.value,
        StageStatus.PARTIAL_SUCCESS.value,
    }
)


def _outcome_from_spawn_result(result: Any) -> Outcome | None:
    """Recover an Outcome from a spawn result whose final text was empty.

    A child that completed its work via tool calls (and ended on a terminal
    report_outcome) legitimately returns empty final text.  The spawn result
    still carries the authoritative outcome in the SAME sources the direct
    tool loop honors:

      1. ``metadata["report_outcome"]`` — the child's report_outcome arguments,
         captured from its orchestrator:complete metadata.  Mirrors how
         ``_run_with_tool_loop`` consults ``_find_report_outcome_call``.
      2. ``status`` — the orchestrator's completion status.  A recognized
         success status means the child finished cleanly; empty closing prose
         is acceptable (spec Section 4.5 treats prose/empty success as SUCCESS).

    Returns the recovered Outcome, or ``None`` when there is genuinely no
    outcome signal (no report_outcome AND no success status), in which case the
    caller falls back / fails loud as before.
    """
    if not isinstance(result, dict):
        return None

    metadata = result.get("metadata")
    report_outcome = (
        metadata.get("report_outcome") if isinstance(metadata, dict) else None
    )
    if isinstance(report_outcome, dict):
        status = _STATUS_MAP.get(report_outcome.get("status", ""))
        if status is not None:
            return Outcome(
                status=status,
                failure_reason=report_outcome.get("failure_reason"),
                notes=report_outcome.get("notes"),
                preferred_label=report_outcome.get("preferred_label"),
                suggested_next_ids=report_outcome.get("suggested_next_ids"),
                context_updates=report_outcome.get("context_updates"),
            )

    if result.get("status") in _SPAWN_SUCCESS_STATUSES:
        status = _STATUS_MAP[result["status"]]
        return Outcome(
            status=status,
            notes="Child session completed with empty final message",
        )

    return None


def _parse_outcome(output: str) -> Outcome:
    """Parse an outcome from child session output.

    Tries JSON first (from tool-report-outcome). Plain text responses return
    SUCCESS per spec Section 4.5 — the backend is only responsible for
    producing Outcome objects when it wants non-SUCCESS status. Empty output
    returns FAIL (no work was done).
    """
    # Empty/whitespace-only output means no work was done
    stripped = output.strip()
    if not stripped:
        return Outcome(
            status=StageStatus.FAIL,
            notes="No output from LLM",
            failure_reason="Empty LLM response",
        )

    # Strip markdown code fences (```json...``` or ```...```) that LLMs sometimes
    # emit despite explicit "no fences" instructions.  This is a common failure mode
    # when the eval node prompt asks for a JSON object: the LLM wraps it in a fence,
    # making stripped.startswith("{") false and causing context_updates to be lost.
    # Example: "```json\n{...}\n```" -> "{...}"
    _fence_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.DOTALL)
    if _fence_match:
        stripped = _fence_match.group(1).strip()

    # Try to parse JSON outcome
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "status" in data:
                status = _STATUS_MAP.get(data["status"])
                if status is not None:
                    return Outcome(
                        status=status,
                        failure_reason=data.get("failure_reason"),
                        notes=data.get("notes"),
                        preferred_label=data.get("preferred_label"),
                        suggested_next_ids=data.get("suggested_next_ids"),
                        context_updates=data.get("context_updates"),
                    )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Before falling through to the SUCCESS default, attempt to RECOVER an
    # embedded verdict from prose-wrapped responses.  Models sometimes emit prose
    # followed by a JSON verdict object (e.g. "Here's my verdict:\n{...}") rather
    # than pure JSON.  We find the LAST balanced {...} in the string and, if it
    # contains a recognised status, honour it rather than silently coercing to
    # SUCCESS.  Pure-JSON and fenced-JSON paths above are unchanged; this only
    # fires when both prior branches were skipped (stripped does NOT start with
    # "{" or a code fence).
    #
    # Spec invariant: an explicit FAIL/RETRY verdict MUST NOT be silently coerced
    # to SUCCESS.  Verdict nodes that want reliable parsing should emit pure JSON
    # or call the report_outcome tool.
    last_open = stripped.rfind("{")
    if last_open != -1:
        depth = 0
        end_pos = -1
        for _i in range(last_open, len(stripped)):
            _ch = stripped[_i]
            if _ch == "{":
                depth += 1
            elif _ch == "}":
                depth -= 1
                if depth == 0:
                    end_pos = _i
                    break
        if end_pos != -1:
            candidate = stripped[last_open : end_pos + 1]
            try:
                _embedded = json.loads(candidate)
                if isinstance(_embedded, dict) and "status" in _embedded:
                    _recovered_status = _STATUS_MAP.get(_embedded["status"])
                    if _recovered_status is not None:
                        logger.warning(
                            "Verdict recovered from prose-wrapped response "
                            "(embedded status=%r).  Verdict nodes should emit "
                            "pure JSON or call the report_outcome tool.",
                            _embedded["status"],
                        )
                        return Outcome(
                            status=_recovered_status,
                            failure_reason=_embedded.get("failure_reason"),
                            notes=_embedded.get("notes"),
                            preferred_label=_embedded.get("preferred_label"),
                            suggested_next_ids=_embedded.get("suggested_next_ids"),
                            context_updates=_embedded.get("context_updates"),
                        )
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # Plain text response — per spec Section 4.5, treat as SUCCESS
    return Outcome(
        status=StageStatus.SUCCESS,
        notes=f"Plain text response: {output[:200]}",
    )
