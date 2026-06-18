"""Tests for system prompt wiring into the agent session (Tasks 1.1, 1.2, 1.3).

Verifies that build_system_prompt(), build_environment_context(), and
discover_project_docs() are actually called from agent_session.py and
their output appears in the ChatRequest sent to the provider.

Spec coverage: PROV-002, SYS-001, SYS-005-008, ENVCTX-001-002.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_core.message_models import ChatResponse, Usage

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


# ---------------------------------------------------------------------------
# Task 1.1: System prompt is included in ChatRequest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_included_in_chat_request():
    """The first message in every ChatRequest must be a system message
    containing the base prompt from config."""
    config = SessionConfig.from_dict({
        "system_prompt": "You are a coding agent.",
        "max_tool_rounds_per_input": 1,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
    )
    await session.process_input("hello")

    # Verify provider.complete was called with a system message first
    call_args = provider.complete.call_args
    request = call_args[0][0]
    assert request.messages[0].role == "system"
    assert "You are a coding agent." in request.messages[0].content


@pytest.mark.asyncio
async def test_system_prompt_rebuilt_every_iteration():
    """System prompt must be rebuilt every LLM call (spec PROV-002)."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base prompt.",
        "max_tool_rounds_per_input": 5,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=[
        _text_response("first"),
        _text_response("second"),
    ])
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
    )
    # Two separate calls = two ChatRequests, each should have system prompt
    await session.process_input("hello")
    await session.process_input("again")

    assert provider.complete.call_count == 2
    for call in provider.complete.call_args_list:
        request = call[0][0]
        assert request.messages[0].role == "system"
        assert "Base prompt." in request.messages[0].content


# ---------------------------------------------------------------------------
# Task 1.2: Environment context appears in system prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_environment_context_in_system_prompt():
    """System prompt must contain <environment> block with working dir."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base prompt.",
        "max_tool_rounds_per_input": 1,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
        provider_name="anthropic", model="claude-sonnet-4-5",
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "<environment>" in system_content
    assert "Working directory:" in system_content


@pytest.mark.asyncio
async def test_environment_context_includes_provider_and_model():
    """Environment block includes provider and model when supplied."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base.",
        "max_tool_rounds_per_input": 1,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
        provider_name="openai", model="gpt-5",
    )
    await session.process_input("hi")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Provider: openai" in system_content
    assert "Model: gpt-5" in system_content


# ---------------------------------------------------------------------------
# Task 1.3: Project doc discovery wired into prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_docs_discovered_for_provider(tmp_path):
    """System prompt includes AGENTS.md content when present."""
    # Create a fake AGENTS.md in a temp dir
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project Rules\nAlways use TDD.")

    config = SessionConfig.from_dict({
        "system_prompt": "Base.",
        "max_tool_rounds_per_input": 1,
        "working_dir": str(tmp_path),
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
        provider_name="anthropic",
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Always use TDD." in system_content


@pytest.mark.asyncio
async def test_no_project_docs_when_none_exist(tmp_path):
    """System prompt still works when no project doc files exist."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base.",
        "max_tool_rounds_per_input": 1,
        "working_dir": str(tmp_path),
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    # Should still have system message, just without project docs section
    assert request.messages[0].role == "system"
    assert "Base." in request.messages[0].content


# ---------------------------------------------------------------------------
# Task 1.1 orchestrator integration: provider name passed through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_passes_provider_name():
    """AgentOrchestrator extracts and passes provider_name to session."""
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"system_prompt": "Agent prompt.", "max_tool_rounds_per_input": 1},
    )
    await orch.execute("hi", MagicMock(), {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    # The provider name should appear in environment context
    assert "Provider: anthropic" in system_content


# ---------------------------------------------------------------------------
# Fix 2.3: User instructions override (Layer 5, spec Section 6.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_instructions_appended_as_layer5():
    """Config with user_instructions → system prompt ends with that instruction."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base prompt.",
        "max_tool_rounds_per_input": 1,
        "user_instructions": "Always respond in French",
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Always respond in French" in system_content
    # User instructions should be last (highest priority)
    assert system_content.strip().endswith("Always respond in French")


@pytest.mark.asyncio
async def test_no_user_instructions_omits_layer5():
    """No user_instructions config → no User Instructions section in prompt."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base prompt.",
        "max_tool_rounds_per_input": 1,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config, provider=provider, tools={}, hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "User Instructions" not in system_content


@pytest.mark.asyncio
async def test_tool_descriptions_in_system_prompt():
    """Mounted tools appear in the system prompt's tool descriptions layer."""
    config = SessionConfig.from_dict({
        "system_prompt": "Base.",
        "max_tool_rounds_per_input": 1,
    })
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    # Create mock tools
    tool = MagicMock()
    tool.name = "read_file"
    tool.description = "Reads a file from disk"
    tool.input_schema = {"type": "object", "properties": {}}

    session = AgentSession(
        config=config, provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "read_file" in system_content
    assert "Reads a file from disk" in system_content


# ---------------------------------------------------------------------------
# Layer-1 fix: context._system_prompt_factory delivers provider base prompt
# (nlspec §6.1 — "Provider-specific base instructions (from ProviderProfile)")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_factory_provides_layer1_base_prompt():
    """When context has _system_prompt_factory, its result is used as Layer-1.

    Spec §6.1: Layer 1 = "Provider-specific base instructions (from ProviderProfile)".
    The factory is registered by foundation when context.include is declared in the
    bundle profile. loop-agent must resolve it in execute() since it builds its own
    message list and never calls context.get_messages_for_request().
    """
    FACTORY_SENTINEL = "BASE-PROMPT-FROM-FACTORY-X7K9Q2"

    async def mock_factory():
        return f"# Agent Base\n\n{FACTORY_SENTINEL}\n\nYou are a coding agent."

    context = MagicMock()
    context._system_prompt_factory = mock_factory  # real async function

    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"max_tool_rounds_per_input": 1},  # no system_prompt — factory must provide it
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert FACTORY_SENTINEL in system_content, (
        f"Factory sentinel not found in system prompt. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_context_factory_wins_over_config_system_prompt():
    """context._system_prompt_factory is primary; system_prompt config is fallback.

    Spec §6.1: the context module delivers "Provider-specific base instructions
    (from ProviderProfile)". This takes precedence over an explicit system_prompt
    in orchestrator config. No double-injection: the factory already incorporates
    the bundle instruction.
    """
    FACTORY_SENTINEL = "FACTORY-WINS-SENTINEL-M3P7"
    CONFIG_SENTINEL = "CONFIG-SYSTEM-PROMPT-SHOULD-NOT-WIN"

    async def mock_factory():
        return f"# From Factory\n\n{FACTORY_SENTINEL}"

    context = MagicMock()
    context._system_prompt_factory = mock_factory

    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={
            "system_prompt": CONFIG_SENTINEL,  # explicit config present
            "max_tool_rounds_per_input": 1,
        },
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    # Factory result is the canonical Layer-1; config system_prompt is a fallback
    assert FACTORY_SENTINEL in system_content, (
        f"Factory sentinel not found. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_context_factory_error_falls_back_to_config():
    """When factory raises, Layer-1 falls back to system_prompt config."""

    async def failing_factory():
        raise RuntimeError("factory failure")

    context = MagicMock()
    context._system_prompt_factory = failing_factory

    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"system_prompt": "Fallback prompt.", "max_tool_rounds_per_input": 1},
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Fallback prompt." in system_content


@pytest.mark.asyncio
async def test_non_coroutine_factory_not_called():
    """A non-async _system_prompt_factory attribute is ignored (no call).

    Guards against MagicMock auto-attributes in tests — MagicMock creates a regular
    (non-coroutine) callable on any attribute access, and calling it as an async
    function would raise. inspect.iscoroutinefunction ensures we only call real factories.
    """
    factory_called = []

    def sync_factory():  # NOT async — should be ignored
        factory_called.append(True)
        return "should not be used"

    context = MagicMock()
    context._system_prompt_factory = sync_factory

    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"system_prompt": "Config prompt.", "max_tool_rounds_per_input": 1},
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    # Sync factory must NOT have been called
    assert not factory_called, "sync factory should not be called"

    # Config system_prompt should be used instead
    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Config prompt." in system_content
