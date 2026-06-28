"""Core agentic loop for the coding agent.

Spec coverage: LOOP-001 through LOOP-023, STOP-001 through STOP-005,
ARCH-007, ARCH-008, EVENT-001 through EVENT-009, ERR-001 through ERR-013,
SHUT-001 through SHUT-009.

The AgentSession is the heart of the orchestrator. It holds conversation
state, dispatches tool calls, manages events, and enforces limits.
The core loop follows the spec's exact cadence:
    build request -> call LLM -> check tools -> execute -> repeat
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from typing import Any

from amplifier_core.llm_errors import ContextLengthError, LLMError
from amplifier_core.message_models import (
    ChatRequest,
    ChatResponse,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolSpec,
)
from amplifier_core.events import (
    CONTENT_BLOCK_END,
    CONTENT_BLOCK_START,
    TOOL_POST,
    TOOL_PRE,
)
from amplifier_core.models import ToolResult

from .config import SessionConfig
from .environment import build_environment_context
from .system_prompt import build_system_prompt, discover_project_docs
from .events import (
    AGENT_ASSISTANT_TEXT_DELTA,
    AGENT_ASSISTANT_TEXT_END,
    AGENT_ASSISTANT_TEXT_START,
    AGENT_AWAITING_INPUT,
    AGENT_CONTEXT_WARNING,
    AGENT_ERROR,
    AGENT_LOOP_DETECTION,
    AGENT_SESSION_END,
    AGENT_SESSION_START,
    AGENT_STEERING_INJECTED,
    AGENT_TOOL_CALL_END,
    AGENT_TOOL_CALL_OUTPUT_DELTA,
    AGENT_TOOL_CALL_START,
    AGENT_TURN_LIMIT,
    AGENT_USER_INPUT,
    PROVIDER_ERROR,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)
from .loop_detection import LoopDetector
from .messages import convert_history_to_messages
from .state import SessionState, SessionStateMachine
from .steering import FollowUpQueue, SteeringQueue
from .tool_registry import ToolRegistry
from .turns import (
    AssistantTurn,
    SessionHistory,
    SteeringTurn,
    ToolResultsTurn,
    UserTurn,
)

logger = logging.getLogger(__name__)


# Canonical provider IDs, shared by AgentSession (project-doc / env-context
# filtering) and AgentOrchestrator (provider-default base-prompt selection).
KNOWN_PROVIDERS = ("anthropic", "openai", "gemini")


def canonical_provider(raw: str | None) -> str | None:
    """Normalise a raw provider name to a canonical ID (anthropic/openai/gemini).

    Bundle composition may yield provider names like "provider-anthropic" or
    "Provider-OpenAI"; this returns the canonical ID via case-insensitive
    substring match, or None when the provider cannot be identified.
    """
    if not raw:
        return None
    lower = raw.lower()
    for canonical in KNOWN_PROVIDERS:
        if canonical in lower:
            return canonical
    return None


class AgentSession:
    """Manages a single coding agent session with the core agentic loop.

    Holds conversation history, state machine, and configuration.
    Persists across multiple process_input() calls so history carries over.
    """

    def __init__(
        self,
        config: SessionConfig,
        provider: Any,
        tools: dict[str, Any] | ToolRegistry,
        hooks: Any,
        steering_queue: SteeringQueue | None = None,
        follow_up_queue: FollowUpQueue | None = None,
        coordinator: Any = None,
        provider_name: str = "",
        model: str = "",
    ) -> None:
        self._config = config
        self._provider = provider
        self._tools: ToolRegistry = (
            tools if isinstance(tools, ToolRegistry) else ToolRegistry.from_dict(tools)
        )
        self._hooks = hooks
        self._coordinator = coordinator
        self._state_machine = SessionStateMachine()
        self._history = SessionHistory()
        self._session_id = str(uuid.uuid4())
        self._session_started = False
        self._steering_queue = steering_queue or SteeringQueue()
        self._follow_up_queue = follow_up_queue or FollowUpQueue()
        self._loop_detector = LoopDetector(window_size=config.loop_detection_window)
        self._current_depth = config.current_depth
        self._provider_name = provider_name
        self._model = model
        self._use_streaming = self._detect_streaming_support()
        self._follow_up_depth = 0  # Tracks recursion depth for SESSION_END timing
        self._tracked_processes: set[Any] = set()  # M-7: running tool subprocesses

    # ------------------------------------------------------------------
    # Streaming detection
    # ------------------------------------------------------------------

    def _detect_streaming_support(self) -> bool:
        """Check if the provider supports streaming via an async generator.

        Uses inspect.isasyncgenfunction to distinguish real async generator
        .stream() methods from auto-generated mock attributes.  This is
        safe with both production providers and test mocks.
        """
        stream_fn = getattr(self._provider, "stream", None)
        return stream_fn is not None and inspect.isasyncgenfunction(stream_fn)

    # ------------------------------------------------------------------
    # Provider call dispatch (streaming vs non-streaming)
    # ------------------------------------------------------------------

    async def _call_provider(self, request: ChatRequest) -> dict[str, Any]:
        """Call the provider, choosing streaming or non-streaming path.

        Returns a normalised dict with keys:
          text, reasoning, reasoning_signature, tool_calls (list[dict]),
          raw_tool_calls (original ToolCall objects for execution),
          usage (Usage object or None), usage_data (dict for events).
        """
        if self._use_streaming:
            return await self._call_provider_streaming(request)
        return await self._call_provider_complete(request)

    @staticmethod
    def _extract_response_id(response: ChatResponse) -> str | None:
        """Extract response_id from a ChatResponse.

        Checks for a top-level ``response_id`` extra field first, then
        falls back to ``metadata.response_id``.  Returns None when the
        provider does not supply one.
        """
        # Pydantic extra fields (provider passes response_id= directly)
        rid = getattr(response, "response_id", None)
        if rid is not None:
            return str(rid)
        # Fallback: metadata dict
        meta = getattr(response, "metadata", None)
        if meta and isinstance(meta, dict):
            rid = meta.get("response_id")
            if rid is not None:
                return str(rid)
        return None

    async def _call_provider_complete(self, request: ChatRequest) -> dict[str, Any]:
        """Non-streaming path: call provider.complete() and extract fields."""
        response: ChatResponse = await self._provider.complete(request)

        text = self._extract_text(response)
        reasoning = self._extract_reasoning(response)
        reasoning_sig = self._extract_reasoning_signature(response)
        usage = response.usage
        usage_data = usage.model_dump() if usage else {}
        response_id = self._extract_response_id(response)

        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls: list[Any] = []
        if response.tool_calls:
            raw_tool_calls = list(response.tool_calls)
            tool_calls = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]

        return {
            "text": text,
            "reasoning": reasoning,
            "reasoning_signature": reasoning_sig,
            "tool_calls": tool_calls,
            "raw_tool_calls": raw_tool_calls,
            "usage": usage,
            "usage_data": usage_data,
            "response_id": response_id,
        }

    async def _call_provider_streaming(self, request: ChatRequest) -> dict[str, Any]:
        """Streaming path: consume provider.stream(), emitting delta events.

        Emits ASSISTANT_TEXT_START before any deltas, ASSISTANT_TEXT_DELTA
        for each content chunk, and ASSISTANT_TEXT_END with the full text
        and reasoning when the stream completes.

        Captures both text content and reasoning/thinking content from
        stream chunks to preserve reasoning for thinking-enabled models
        (Claude extended thinking, OpenAI o-series).
        """
        await self._hooks.emit(AGENT_ASSISTANT_TEXT_START, {})

        full_text = ""
        full_reasoning = ""
        reasoning_signature: str | None = None
        tool_calls: list[dict[str, Any]] = []
        usage_data: dict[str, Any] = {}

        async for chunk in self._provider.stream(request):
            # Accumulate text content
            content = chunk.get("content")
            if content:
                full_text += content
                await self._hooks.emit(AGENT_ASSISTANT_TEXT_DELTA, {"delta": content})

            # Accumulate reasoning/thinking content
            thinking = chunk.get("thinking") or chunk.get("reasoning")
            if thinking:
                full_reasoning += thinking

            # Capture reasoning signature (for multi-turn Anthropic thinking)
            chunk_sig = chunk.get("reasoning_signature") or chunk.get("signature")
            if chunk_sig:
                reasoning_signature = chunk_sig

            # Accumulate tool calls
            chunk_tool_calls = chunk.get("tool_calls")
            if chunk_tool_calls:
                tool_calls.extend(chunk_tool_calls)

            # Capture usage data
            chunk_usage = chunk.get("usage")
            if chunk_usage:
                usage_data = chunk_usage

        # Emit text end with full assembled text and reasoning
        text_end_data: dict[str, Any] = {"text": full_text}
        if full_reasoning:
            text_end_data["reasoning"] = full_reasoning
        await self._hooks.emit(AGENT_ASSISTANT_TEXT_END, text_end_data)

        # Build ToolCall-like objects for the execution path
        raw_tool_calls: list[Any] = []
        if tool_calls:
            from types import SimpleNamespace

            raw_tool_calls = [
                SimpleNamespace(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in tool_calls
            ]

        return {
            "text": full_text,
            "reasoning": full_reasoning if full_reasoning else None,
            "reasoning_signature": reasoning_signature,
            "tool_calls": tool_calls,
            "raw_tool_calls": raw_tool_calls,
            "usage": None,
            "usage_data": usage_data,
            "response_id": None,  # Streaming path: no ChatResponse object
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_input(self, prompt: str) -> str:
        """Process a user input through the agentic loop.

        Appends user turn, calls LLM in a loop interleaved with tool
        execution, and returns the final text response.  The loop exits
        on natural completion (no tool calls), round limit, or turn limit.
        """
        # Emit session_start once (spec: SESSION_START on session creation)
        if not self._session_started:
            self._session_started = True
            await self._hooks.emit(
                AGENT_SESSION_START, {"session_id": self._session_id}
            )

        self._state_machine.submit()  # IDLE -> PROCESSING

        # Record user turn
        self._history.append(UserTurn(content=prompt))
        await self._hooks.emit(AGENT_USER_INPUT, {"content": prompt})

        # Drain steering before first LLM call (spec STEER-001)
        await self._drain_steering()

        round_count = 0
        last_text = ""

        # Per-provider max_tool_rounds_per_provider is accessible via
        # config.max_tool_rounds_per_provider but the loop uses the global
        # max_tool_rounds_per_input as the spec primitive; per-provider
        # limits are an optional extension (M-4) for future wiring.
        _max_rounds = self._config.max_tool_rounds_per_input

        # Spec STOP-002: <=0 means unlimited. Use the same guard the spec
        # pseudocode describes: "IF max > 0 AND round_count >= max: BREAK".
        while _max_rounds <= 0 or round_count < _max_rounds:
            # Checkpoint 1: Graceful cancellation at top of loop
            if self._is_cancelled():
                self._state_machine.complete()
                await self._emit_session_end()
                return await self._process_follow_ups(last_text)

            # Check session-wide turn limit
            if (
                self._config.max_turns > 0
                and self._history.turn_count >= self._config.max_turns
            ):
                await self._hooks.emit(
                    AGENT_TURN_LIMIT,
                    {"total_turns": self._history.turn_count},
                )
                break

            # Build LLM request
            messages = self._convert_history_to_messages()
            tool_specs = self._get_tool_definitions()
            request = ChatRequest(
                messages=messages,
                tools=tool_specs,
                tool_choice="auto",
                reasoning_effort=self._config.reasoning_effort,
            )

            # Emit provider:request before LLM call
            await self._hooks.emit(PROVIDER_REQUEST, {})

            # Call LLM — streaming or non-streaming based on provider capability
            try:
                call_result = await self._call_provider(request)
            except ContextLengthError as e:
                # Spec Appendix B / STOP-005: ContextLengthError is handled
                # separately from other non-retryable errors.  Emit a context
                # warning (reusing AGENT_CONTEXT_WARNING) and return to IDLE
                # so the session stays usable — do NOT close it.
                await self._emit_provider_error(e)
                await self._emit_error(str(e))
                await self._hooks.emit(
                    AGENT_CONTEXT_WARNING,
                    {
                        "message": str(e),
                        "context_length_exceeded": True,
                    },
                )
                self._state_machine.complete()  # PROCESSING -> IDLE
                return await self._process_follow_ups(last_text)
            except LLMError as e:
                await self._emit_provider_error(e)
                await self._emit_error(str(e))
                if not e.retryable:
                    # Other non-retryable errors (e.g. auth) → CLOSED
                    self._state_machine.fatal_error()
                    await self._emit_session_end()
                    raise
                raise
            except Exception as e:
                # Generic unexpected error → CLOSED
                await self._emit_error(str(e))
                self._state_machine.fatal_error()
                await self._emit_session_end()
                raise

            # Checkpoint 2: Immediate cancellation after provider call
            if self._is_immediate_cancel():
                self._state_machine.complete()
                await self._emit_session_end()
                return await self._process_follow_ups(last_text)

            text = call_result["text"]
            reasoning = call_result["reasoning"]
            reasoning_sig = call_result["reasoning_signature"]
            tool_calls = call_result["tool_calls"]
            usage = call_result["usage"]
            usage_data = call_result["usage_data"]
            response_id = call_result.get("response_id")

            # Emit provider:response after LLM call with usage data
            await self._hooks.emit(PROVIDER_RESPONSE, {"usage": usage_data})

            # Emit content_block events for thinking blocks (core events
            # consumed by hooks-streaming-ui for real-time display)
            if reasoning:
                await self._hooks.emit(
                    CONTENT_BLOCK_START,
                    {"block_type": "thinking", "block_index": 0},
                )
                await self._hooks.emit(
                    CONTENT_BLOCK_END,
                    {
                        "block_index": 0,
                        "total_blocks": 1,
                        "block": {"type": "thinking", "thinking": reasoning},
                        "usage": usage_data,
                    },
                )

            # Check context window usage (spec Section 5.5)
            await self._check_context_usage()

            if text:
                last_text = text

            # Record assistant turn
            tool_calls_data = []
            if tool_calls:
                tool_calls_data = [
                    {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    }
                    for tc in tool_calls
                ]
            self._history.append(
                AssistantTurn(
                    content=text,
                    tool_calls=tool_calls_data,
                    reasoning=reasoning,
                    reasoning_signature=reasoning_sig,
                    usage=usage,
                    response_id=response_id,
                )
            )

            # Emit assistant_text_end (spec EVENT-003)
            # (Streaming path already emitted START/DELTA/END; non-streaming
            # emits only END here.)
            if not self._use_streaming:
                text_end_data: dict[str, Any] = {"text": text}
                if reasoning:
                    text_end_data["reasoning"] = reasoning
                await self._hooks.emit(AGENT_ASSISTANT_TEXT_END, text_end_data)

            # Natural completion: no tool calls
            if not tool_calls:
                # Detect if the model is asking the user a question
                if self._looks_like_question(text):
                    self._state_machine.await_input()  # PROCESSING -> AWAITING_INPUT
                    await self._hooks.emit(
                        AGENT_AWAITING_INPUT,
                        {"text": text, "session_id": self._session_id},
                    )
                    # Do NOT emit session_end or process follow-ups.
                    # Host decides: answer via resume_with_input() or close.
                    return text
                else:
                    self._state_machine.complete()  # PROCESSING -> IDLE
                    # SESSION_END emitted after follow-ups are fully drained
                    return await self._process_follow_ups(text)

            # Execute tools in parallel
            raw_tool_calls = call_result["raw_tool_calls"]
            results = await self._execute_tool_calls(raw_tool_calls)
            self._history.append(ToolResultsTurn(results=results))
            round_count += 1

            # Record tool calls for loop detection
            if self._config.enable_loop_detection:
                for tc in raw_tool_calls:
                    self._loop_detector.record(tc.name, tc.arguments)

            # Drain steering after each tool round (spec STEER-002)
            await self._drain_steering()

            # Check for loop detection (spec Section 2.10)
            await self._check_loop_detection()

        # Round limit reached
        await self._hooks.emit(AGENT_TURN_LIMIT, {"round_count": round_count})
        self._state_machine.complete()  # PROCESSING -> IDLE
        # SESSION_END emitted after follow-ups are fully drained
        return await self._process_follow_ups(last_text)

    # ------------------------------------------------------------------
    # Provider name resolution (spec Section 3: Provider-Aligned Toolsets)
    # ------------------------------------------------------------------

    # Canonical provider IDs for flexible matching
    _KNOWN_PROVIDERS = KNOWN_PROVIDERS

    def _resolve_provider_id(self) -> str | None:
        """Resolve raw provider name to a canonical provider ID.

        Bundle composition may yield provider names like
        "provider-anthropic" or "Provider-OpenAI".  This method
        normalises them to a canonical ID ("anthropic", "openai",
        "gemini") so that project doc discovery and environment
        context use the correct provider-specific behaviour.

        Returns None when the provider cannot be identified.
        """
        return canonical_provider(self._provider_name)

    # ------------------------------------------------------------------
    # System prompt assembly (spec PROV-002: rebuilt every LLM call)
    # ------------------------------------------------------------------

    def _build_system_prompt_text(self) -> str:
        """Assemble the 5-layer system prompt.

        Layers:
          1. Base prompt from config
          2. Environment context (working dir, platform, git, model)
          3. Tool descriptions (from mounted tools)
          4. Project docs (AGENTS.md, provider-specific files)
          5. User instructions override (highest priority, spec §6.2)
        """
        import os

        # Resolve canonical provider ID for doc filtering
        provider_id = self._resolve_provider_id()

        # Layer 1: Base prompt — comes ONLY from config.system_prompt (set directly,
        # or loaded by AgentOrchestrator.execute() from system_prompt_file before the
        # session is created). loop-agent does NOT consume the bundle's context.include
        # files for the base — that channel is for ADDITIVE context, not Layer-1
        # (proven empirically in core 1.6.0; the prior assumption was wrong). The
        # provider base prompt (nlspec §6.1 ProviderProfile) MUST therefore be supplied
        # via system_prompt / system_prompt_file in session.orchestrator.config.
        # See docs/designs/layer-1-profile-owned-system-prompt.md.
        base_prompt = self._config.system_prompt
        if not base_prompt:
            # Fail-loud: an empty Layer-1 is a configuration error, not a
            # recoverable runtime condition.  The silent stub ("You are a
            # coding agent.") was masking misconfiguration and causing
            # hallucination + over-fragmentation in synthesis.
            # Fix: add system_prompt_file: context/system-<provider>.md to
            # session.orchestrator.config in the agent YAML or profile.
            # See docs/designs/layer-1-profile-owned-system-prompt.md §C.
            raise RuntimeError(
                "Layer-1 base prompt is empty: session was created with no "
                "system_prompt in orchestrator config and no system_prompt_file "
                "that resolved to content. "
                "Add system_prompt_file: context/system-<provider>.md to the "
                "agent's session.orchestrator.config. "
                "See docs/designs/layer-1-profile-owned-system-prompt.md."
            )

        # Layer 2: Environment context
        working_dir = self._config.working_dir or os.getcwd()
        environment = build_environment_context(
            working_dir=working_dir,
            provider_name=self._provider_name or None,
            model=self._model or None,
        )

        # Layer 3: Tool descriptions
        tool_lines: list[str] = []
        for tool in self._tools.values():
            desc = getattr(tool, "description", "") or ""
            tool_lines.append(f"- **{tool.name}**: {desc}")
        tool_descriptions = "\n".join(tool_lines)

        # Layer 4: Project docs (uses resolved canonical provider ID)
        project_docs = discover_project_docs(
            working_dir=working_dir,
            provider_id=provider_id,
        )

        # Layer 5: User instructions override
        user_override = self._config.user_instructions or None

        return build_system_prompt(
            base_prompt=base_prompt,
            environment=environment,
            tool_descriptions=tool_descriptions,
            project_docs=project_docs,
            user_override=user_override,
        )

    # ------------------------------------------------------------------
    # History -> Messages conversion
    # ------------------------------------------------------------------

    def _convert_history_to_messages(self) -> list[Message]:
        """Convert typed turn history to Message objects for ChatRequest.

        Delegates to the messages module which handles system-first
        ordering, content blocks, and ThinkingBlock preservation.
        Then prepends the system prompt as the first message.
        """
        messages = convert_history_to_messages(self._history)

        # Rebuild system prompt every iteration (spec PROV-002)
        system_text = self._build_system_prompt_text()
        system_msg = Message(role="system", content=system_text)

        # Prepend system message, removing any existing system messages
        # (they'll be rebuilt fresh each time)
        non_system = [m for m in messages if m.role != "system"]
        return [system_msg] + non_system

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def _get_tool_definitions(self) -> list[ToolSpec] | None:
        """Convert mounted tools to ToolSpec list for ChatRequest."""
        if not self._tools:
            return None
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
            )
            for tool in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        """Execute tool calls, parallel or sequential based on config.

        Uses asyncio.gather() when supports_parallel_tool_calls is True
        AND there are multiple tool calls. Otherwise executes sequentially
        to preserve ordering guarantees (spec Section 3.2).
        """
        use_parallel = self._config.supports_parallel_tool_calls and len(tool_calls) > 1

        if use_parallel:
            try:
                results = await asyncio.gather(
                    *[self._execute_single_tool(tc) for tc in tool_calls]
                )
                return list(results)
            except asyncio.CancelledError:
                # Immediate cancel during tool execution:
                # Synthesize cancelled results for ALL tool calls to maintain
                # tool_use/tool_result pairing (provider API contract)
                logger.info("Tool execution cancelled - synthesizing cancelled results")
                return [
                    ToolResult(
                        success=False,
                        output="Tool execution was cancelled by user",
                    )
                    for _ in tool_calls
                ]
        else:
            results: list[ToolResult] = []
            for tc in tool_calls:
                result = await self._execute_single_tool(tc)
                results.append(result)
            return results

    async def _execute_single_tool(self, tool_call: Any) -> ToolResult:
        """Execute a single tool call. Never raises — errors become results."""
        # Register tool with cancellation token for visibility
        if self._coordinator:
            cancellation = getattr(self._coordinator, "cancellation", None)
            if cancellation and hasattr(cancellation, "register_tool_start"):
                cancellation.register_tool_start(tool_call.id, tool_call.name)

        try:
            return await self._execute_single_tool_inner(tool_call)
        finally:
            # Unregister tool from cancellation token
            if self._coordinator:
                cancellation = getattr(self._coordinator, "cancellation", None)
                if cancellation and hasattr(cancellation, "register_tool_complete"):
                    cancellation.register_tool_complete(tool_call.id)

    @staticmethod
    def _validate_tool_arguments(
        tool: Any, arguments: dict[str, Any] | None
    ) -> str | None:
        """Validate tool arguments against the tool's input_schema.

        Checks required field presence and basic type conformance.
        Returns None on success or an error message string describing
        all validation failures.

        This is a lightweight validator (no jsonschema dependency).
        """
        schema = getattr(tool, "input_schema", None)
        if not schema or not isinstance(schema, dict):
            return None  # No schema to validate against

        args = arguments or {}

        errors: list[str] = []

        # Check required fields
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field_name in required:
            if field_name not in args:
                field_schema = properties.get(field_name, {})
                field_type = field_schema.get("type", "any")
                field_desc = field_schema.get("description", "")
                hint = f" ({field_desc})" if field_desc else ""
                errors.append(
                    f"Missing required field '{field_name}' (type: {field_type}){hint}"
                )

        # Basic type checking for present fields
        _JSON_SCHEMA_TYPE_MAP = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for field_name, value in args.items():
            if field_name in properties:
                expected_type_str = properties[field_name].get("type")
                if expected_type_str and expected_type_str in _JSON_SCHEMA_TYPE_MAP:
                    expected_type = _JSON_SCHEMA_TYPE_MAP[expected_type_str]
                    if not isinstance(value, expected_type):
                        errors.append(
                            f"Field '{field_name}' expected type "
                            f"'{expected_type_str}', got '{type(value).__name__}'"
                        )

        if errors:
            error_list = "; ".join(errors)
            return (
                f"Validation error for tool '{tool.name}': {error_list}. "
                f"Please fix the arguments and try again."
            )
        return None

    async def _execute_single_tool_inner(self, tool_call: Any) -> ToolResult:
        """Inner tool execution logic. Never raises — errors become results."""
        # Core tool:pre event (consumed by hooks-streaming-ui for display)
        await self._hooks.emit(
            TOOL_PRE,
            {"tool_name": tool_call.name, "tool_input": tool_call.arguments},
        )
        # Agent-specific event (for orchestrator-level tracking)
        await self._hooks.emit(
            AGENT_TOOL_CALL_START,
            {"tool_name": tool_call.name, "call_id": tool_call.id},
        )

        start_time = time.monotonic()

        tool = self._tools.get(tool_call.name)
        if tool is None:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = f"Unknown tool: {tool_call.name}"
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                },
            )
            return ToolResult(success=False, output=error_msg)

        # Validate arguments against tool's JSON Schema (spec Section 3.8 step 2)
        validation_error = self._validate_tool_arguments(tool, tool_call.arguments)
        if validation_error is not None:
            duration_ms = (time.monotonic() - start_time) * 1000
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "error": validation_error,
                    "duration_ms": duration_ms,
                },
            )
            return ToolResult(success=False, output=validation_error)

        try:
            result = await tool.execute(tool_call.arguments)
            duration_ms = (time.monotonic() - start_time) * 1000

            # Serialize result for the tool:post hook
            raw_output = result.get_serialized_output()

            # Emit tool:post for hooks (truncation, logging, etc.)
            post_result = await self._hooks.emit(
                TOOL_POST,
                {
                    "tool_name": tool_call.name,
                    "tool_input": tool_call.arguments,
                    "result": raw_output,
                    "call_id": tool_call.id,
                },
            )

            # If a hook modified the result (e.g. truncation), use the
            # modified output for the LLM while preserving full output
            # in the event stream.
            llm_output = raw_output
            if (
                hasattr(post_result, "action")
                and post_result.action == "modify"
                and hasattr(post_result, "data")
                and post_result.data
                and "result" in post_result.data
            ):
                llm_output = post_result.data["result"]

            # Emit tool output delta for UI streaming (spec TOOL_CALL_OUTPUT_DELTA)
            await self._hooks.emit(
                AGENT_TOOL_CALL_OUTPUT_DELTA,
                {
                    "tool_name": tool_call.name,
                    "tool_call_id": tool_call.id,
                    "delta": raw_output,
                    "is_final": True,
                },
            )

            # Emit agent:tool_call_end with FULL untruncated output
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "output": raw_output,
                    "duration_ms": duration_ms,
                },
            )

            # Return result with potentially truncated output for LLM
            return ToolResult(success=result.success, output=llm_output)
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = f"Tool error ({tool_call.name}): {e}"
            logger.error(error_msg)
            # Emit tool output delta even on errors
            await self._hooks.emit(
                AGENT_TOOL_CALL_OUTPUT_DELTA,
                {
                    "tool_name": tool_call.name,
                    "tool_call_id": tool_call.id,
                    "delta": error_msg,
                    "is_final": True,
                },
            )
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                },
            )
            return ToolResult(success=False, output=error_msg)

    # ------------------------------------------------------------------
    # Question detection (spec Section 2.3: AWAITING_INPUT)
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        """Detect if model text looks like a question directed at the user.

        Heuristic: strip trailing whitespace, markdown formatting, and
        code fences. If the cleaned text ends with '?', treat it as a
        question. This intentionally errs on the side of caution --
        only text that clearly ends as a question triggers AWAITING_INPUT.
        """
        if not text or not text.strip():
            return False

        cleaned = text.rstrip()
        lines = cleaned.split("\n")

        # Walk backwards past empty lines and code fences
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("```"):
                continue
            return stripped.endswith("?")

        return False

    async def resume_with_input(self, answer: str) -> str:
        """Resume from AWAITING_INPUT state with the user's answer.

        Transitions AWAITING_INPUT -> PROCESSING -> IDLE and then calls
        process_input() with the user's answer as the new prompt.

        Args:
            answer: The user's response to the agent's question.

        Returns:
            The agent's response after processing the answer.

        Raises:
            InvalidTransitionError: If not in AWAITING_INPUT state.
        """
        self._state_machine.resume_input()  # AWAITING_INPUT -> PROCESSING
        self._state_machine.complete()  # PROCESSING -> IDLE
        return await self.process_input(answer)

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def _extract_text(self, response: ChatResponse) -> str:
        """Extract text content from a ChatResponse's content blocks."""
        if not response.content:
            return ""
        parts = []
        for block in response.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "\n\n".join(parts) if parts else ""

    def _extract_reasoning(self, response: ChatResponse) -> str | None:
        """Extract reasoning/thinking content from a ChatResponse."""
        if not response.content:
            return None
        parts = []
        for block in response.content:
            if isinstance(block, ThinkingBlock):
                parts.append(block.thinking)
        return "\n\n".join(parts) if parts else None

    def _extract_reasoning_signature(self, response: ChatResponse) -> str | None:
        """Extract ThinkingBlock signature for multi-turn preservation."""
        if not response.content:
            return None
        for block in response.content:
            if isinstance(block, ThinkingBlock) and block.signature:
                return block.signature
        return None

    # ------------------------------------------------------------------
    # Graceful shutdown (spec SHUT-001 through SHUT-009)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Process tracking (M-7)
    # ------------------------------------------------------------------

    def register_process(self, process: Any) -> None:
        """Track a running tool subprocess for shutdown cleanup."""
        self._tracked_processes.add(process)

    def unregister_process(self, process: Any) -> None:
        """Remove a completed subprocess from tracking."""
        self._tracked_processes.discard(process)

    _DEFAULT_PROCESS_TIMEOUT = 2.0  # seconds before SIGKILL

    async def shutdown(self, *, process_timeout: float | None = None) -> None:
        """Gracefully shut down the session.

        Spec ERR-015 / M-7: Cancel in-flight work, terminate tracked
        tool subprocesses (SIGTERM then SIGKILL), emit session_end,
        transition to CLOSED. Idempotent — safe to call multiple times.
        """
        if self._state_machine.state == SessionState.CLOSED:
            return

        # Terminate tracked tool subprocesses (M-7)
        timeout = (
            process_timeout
            if process_timeout is not None
            else self._DEFAULT_PROCESS_TIMEOUT
        )
        await self._terminate_tracked_processes(timeout)

        # Transition to CLOSED from any non-CLOSED state
        if self._state_machine.state == SessionState.PROCESSING:
            self._state_machine.fatal_error()
        elif self._state_machine.state == SessionState.AWAITING_INPUT:
            self._state_machine.abort()
        else:
            # IDLE → CLOSED
            self._state_machine.close()
        await self._emit_session_end()

    async def _terminate_tracked_processes(self, timeout: float) -> None:
        """Send SIGTERM to tracked processes, then SIGKILL after timeout."""
        processes = list(self._tracked_processes)
        self._tracked_processes.clear()

        for proc in processes:
            # Skip already-exited processes
            if getattr(proc, "returncode", None) is not None:
                continue
            try:
                proc.terminate()
                logger.info(
                    "Sent SIGTERM to tool process %s",
                    getattr(proc, "pid", "?"),
                )
            except (OSError, ProcessLookupError):
                continue

            # Wait for graceful exit, then force-kill
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except (asyncio.TimeoutError, Exception):
                try:
                    proc.kill()
                    logger.warning(
                        "Sent SIGKILL to tool process %s after %.1fs timeout",
                        getattr(proc, "pid", "?"),
                        timeout,
                    )
                except (OSError, ProcessLookupError):
                    pass

    # ------------------------------------------------------------------
    # Cancellation checks (spec Section 2.4, C-5)
    # ------------------------------------------------------------------

    def _is_cancelled(self) -> bool:
        """Check if graceful cancellation has been requested."""
        if self._coordinator is None:
            return False
        cancellation = getattr(self._coordinator, "cancellation", None)
        if cancellation is None:
            return False
        # Use 'is True' to avoid MagicMock truthiness in tests
        return getattr(cancellation, "is_cancelled", False) is True

    def _is_immediate_cancel(self) -> bool:
        """Check if immediate (force) cancellation has been requested."""
        if self._coordinator is None:
            return False
        cancellation = getattr(self._coordinator, "cancellation", None)
        if cancellation is None:
            return False
        # Use 'is True' to avoid MagicMock truthiness in tests
        return getattr(cancellation, "is_immediate", False) is True

    # ------------------------------------------------------------------
    # Error event helpers
    # ------------------------------------------------------------------

    async def _emit_error(self, message: str) -> None:
        """Emit agent:error event."""
        await self._hooks.emit(AGENT_ERROR, {"error": message})

    async def _emit_provider_error(self, error: LLMError) -> None:
        """Emit provider:error event with enriched LLMError data."""
        await self._hooks.emit(
            PROVIDER_ERROR,
            {
                "error": str(error),
                "retryable": error.retryable,
                "status_code": error.status_code,
                "provider": error.provider,
            },
        )

    async def _emit_session_end(self) -> None:
        """Emit agent:session_end with current state."""
        await self._hooks.emit(
            AGENT_SESSION_END,
            {"state": self._state_machine.state.value},
        )

    # ------------------------------------------------------------------
    # Steering (spec STEER-001 through STEER-010)
    # ------------------------------------------------------------------

    async def _drain_steering(self) -> None:
        """Drain pending steering messages into history.

        Each drained message becomes a SteeringTurn appended to history
        and an agent:steering_injected event is emitted.
        """
        messages = self._steering_queue.drain()
        for msg in messages:
            self._history.append(SteeringTurn(content=msg))
            await self._hooks.emit(AGENT_STEERING_INJECTED, {"content": msg})

    async def _process_follow_ups(self, last_result: str) -> str:
        """Process queued follow-up messages after the loop completes.

        Calls process_input() for each follow-up message. Emits
        SESSION_END only from the outermost call, after the entire
        follow-up queue is fully drained (spec Section 2.5).
        """
        result = last_result
        self._follow_up_depth += 1
        try:
            next_msg = self._follow_up_queue.drain()
            while next_msg is not None:
                result = await self.process_input(next_msg)
                next_msg = self._follow_up_queue.drain()
        finally:
            self._follow_up_depth -= 1

        # Emit SESSION_END only from the outermost follow-up call
        if self._follow_up_depth == 0:
            await self._emit_session_end()

        return result

    # ------------------------------------------------------------------
    # Loop detection (spec Section 2.10)
    # ------------------------------------------------------------------

    async def _check_loop_detection(self) -> None:
        """Check for repeating tool call patterns and inject warning.

        If loop detection is enabled and a pattern is detected, injects
        a warning as a SteeringTurn and emits an agent:loop_detection event.
        """
        if not self._config.enable_loop_detection:
            return
        warning = self._loop_detector.check()
        if warning is not None:
            self._history.append(SteeringTurn(content=warning))
            await self._hooks.emit(AGENT_LOOP_DETECTION, {"warning": warning})
            # Reset detector after firing to avoid repeated warnings
            self._loop_detector.reset()

    # ------------------------------------------------------------------
    # Context window awareness (spec Section 5.5)
    # ------------------------------------------------------------------

    async def _check_context_usage(self) -> None:
        """Estimate context usage and emit warning if over 80%.

        Uses the heuristic: 1 token ~ 4 characters.
        Informational only — no automatic compaction.
        """
        window_size = self._config.context_window_size
        if window_size <= 0:
            return  # Unknown or unlimited — skip check

        # Estimate total characters across all messages
        messages = self._convert_history_to_messages()
        total_chars = 0
        for msg in messages:
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        total_chars += len(block.text)
                    elif isinstance(block, ThinkingBlock):
                        total_chars += len(block.thinking)

        approx_tokens = total_chars / 4
        threshold = window_size * 0.8

        if approx_tokens > threshold:
            usage_percent = round(approx_tokens / window_size * 100)
            await self._hooks.emit(
                AGENT_CONTEXT_WARNING,
                {
                    "approx_tokens": int(approx_tokens),
                    "context_window_size": window_size,
                    "usage_percent": usage_percent,
                    "message": (
                        f"Context usage at ~{usage_percent}% "
                        f"of context window ({int(approx_tokens)}/{window_size} tokens)"
                    ),
                },
            )
