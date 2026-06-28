"""Tests for system prompt wiring into the agent session (Tasks 1.1, 1.2, 1.3).

Verifies that build_system_prompt(), build_environment_context(), and
discover_project_docs() are actually called from agent_session.py and
their output appears in the ChatRequest sent to the provider.

Spec coverage: PROV-002, SYS-001, SYS-005-008, ENVCTX-001-002.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, Usage

from amplifier_module_loop_agent import AgentOrchestrator, _resolve_system_prompt_file
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
    config = SessionConfig.from_dict(
        {
            "system_prompt": "You are a coding agent.",
            "max_tool_rounds_per_input": 1,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
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
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base prompt.",
            "max_tool_rounds_per_input": 5,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _text_response("first"),
            _text_response("second"),
        ]
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
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
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base prompt.",
            "max_tool_rounds_per_input": 1,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
        provider_name="anthropic",
        model="claude-sonnet-4-5",
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "<environment>" in system_content
    assert "Working directory:" in system_content


@pytest.mark.asyncio
async def test_environment_context_includes_provider_and_model():
    """Environment block includes provider and model when supplied."""
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base.",
            "max_tool_rounds_per_input": 1,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
        provider_name="openai",
        model="gpt-5",
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

    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base.",
            "max_tool_rounds_per_input": 1,
            "working_dir": str(tmp_path),
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
        provider_name="anthropic",
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "Always use TDD." in system_content


@pytest.mark.asyncio
async def test_no_project_docs_when_none_exist(tmp_path):
    """System prompt still works when no project doc files exist."""
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base.",
            "max_tool_rounds_per_input": 1,
            "working_dir": str(tmp_path),
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
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
        # No system_prompt: the anthropic provider DEFAULT supplies Layer-1, so
        # this test no longer needs a guard-satisfying dummy (ripple shrink).
        config={"max_tool_rounds_per_input": 1},
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
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base prompt.",
            "max_tool_rounds_per_input": 1,
            "user_instructions": "Always respond in French",
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
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
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base prompt.",
            "max_tool_rounds_per_input": 1,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    session = AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "User Instructions" not in system_content


@pytest.mark.asyncio
async def test_tool_descriptions_in_system_prompt():
    """Mounted tools appear in the system prompt's tool descriptions layer."""
    config = SessionConfig.from_dict(
        {
            "system_prompt": "Base.",
            "max_tool_rounds_per_input": 1,
        }
    )
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()

    # Create mock tools
    tool = MagicMock()
    tool.name = "read_file"
    tool.description = "Reads a file from disk"
    tool.input_schema = {"type": "object", "properties": {}}

    session = AgentSession(
        config=config,
        provider=provider,
        tools={"read_file": tool},
        hooks=hooks,
    )
    await session.process_input("hello")

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert "read_file" in system_content
    assert "Reads a file from disk" in system_content


# ---------------------------------------------------------------------------
# Profile-owned Layer-1 system prompt (nlspec §6.1 + design §A, §B, §C)
# ---------------------------------------------------------------------------
# These tests verify the new profile-owned system prompt mechanism:
#   A. system_prompt in config → used directly as Layer-1
#   B. system_prompt_file (absolute path) → loaded and used as Layer-1
#   C. system_prompt set + system_prompt_file set → system_prompt takes precedence
#   D. Missing system_prompt (no file) → fail-loud RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_in_config_is_layer1():
    """system_prompt in orchestrator config is used directly as Layer-1 (nlspec §6.1).

    Design §A: config.system_prompt is the canonical Layer-1 channel.  There is no
    factory override — the profile-owned value flows straight through.
    """
    SENTINEL = "CONFIG-LAYER1-SENTINEL-X7K9Q2"
    context = MagicMock()

    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={
            "system_prompt": f"# Agent Base\n\n{SENTINEL}\n\nYou are a coding agent.",
            "max_tool_rounds_per_input": 1,
        },
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert SENTINEL in system_content, (
        f"system_prompt sentinel not found in Layer-1. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_system_prompt_file_loaded_as_layer1(tmp_path):
    """system_prompt_file (absolute path) is loaded and its content becomes Layer-1.

    Design §A: loop-agent resolves system_prompt_file at session init and puts the
    content into config.system_prompt.  Absolute paths bypass bundle-root resolution,
    making this testable without a real bundle layout.
    """
    SENTINEL = "SYSTEM-FILE-SENTINEL-X9K2M"
    prompt_file = tmp_path / "base-prompt.md"
    prompt_file.write_text(f"# Provider Base\n\n{SENTINEL}", encoding="utf-8")

    context = MagicMock()
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={
            "system_prompt_file": str(prompt_file),  # absolute path
            "max_tool_rounds_per_input": 1,
        },
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert SENTINEL in system_content, (
        f"system_prompt_file content not found in Layer-1. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_system_prompt_takes_precedence_over_system_prompt_file(tmp_path):
    """When both system_prompt and system_prompt_file are set, system_prompt wins.

    Design §A: If system_prompt is already present in config (e.g. injected by
    loop-pipeline backend.py before spawn), system_prompt_file is skipped — single
    owner, no conflict.
    """
    FILE_SENTINEL = "FILE-SHOULD-NOT-WIN"
    CONFIG_SENTINEL = "CONFIG-PROMPT-WINS-SENTINEL"
    prompt_file = tmp_path / "base.md"
    prompt_file.write_text(f"# From File\n\n{FILE_SENTINEL}", encoding="utf-8")

    context = MagicMock()
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={
            "system_prompt": CONFIG_SENTINEL,
            "system_prompt_file": str(prompt_file),
            "max_tool_rounds_per_input": 1,
        },
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert CONFIG_SENTINEL in system_content, (
        f"system_prompt not used. Got: {system_content[:200]}"
    )
    assert FILE_SENTINEL not in system_content, (
        f"system_prompt_file content leaked through. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_unknown_provider_with_no_base_raises_loud_error():
    """Precedence (4): unknown provider + no explicit base raises a clear RuntimeError.

    With the provider-default in place, a KNOWN provider (anthropic/openai/gemini)
    always resolves a default base. The fail-loud path is now an UNKNOWN provider
    with no system_prompt / system_prompt_file: there is no default to apply and
    we must NOT silently pick a wrong one.
    """
    context = MagicMock()
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"max_tool_rounds_per_input": 1},  # no system_prompt, no file
    )
    with pytest.raises(RuntimeError, match="not one of the known providers"):
        # "test" is not a known provider -> no default -> fail loud.
        await orch.execute("hello", context, {"test": provider}, {}, hooks)


@pytest.mark.asyncio
async def test_provider_default_base_prompt_loaded_for_known_provider():
    """Precedence (3): with no explicit base, a known provider loads its default.

    An anthropic agent with NO system_prompt / system_prompt_file resolves the
    bundle's context/system-anthropic.md provider default into Layer-1 — this is
    what lets the 30 per-YAML system_prompt_file lines be removed.
    """
    context = MagicMock()
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"max_tool_rounds_per_input": 1},  # no base configured at all
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    # The real Anthropic provider base ships this sentinel heading.
    assert "Anthropic Profile" in system_content or "Claude Code" in system_content, (
        f"provider-default base not loaded into Layer-1. Got: {system_content[:200]}"
    )


@pytest.mark.asyncio
async def test_explicit_config_overrides_provider_default():
    """Precedence (1) beats (3): explicit system_prompt wins over the provider default."""
    context = MagicMock()
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    hooks = _make_hooks()
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    SENTINEL = "EXPLICIT-OVERRIDE-WINS-Q7"
    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"system_prompt": SENTINEL, "max_tool_rounds_per_input": 1},
    )
    await orch.execute("hello", context, {"anthropic": provider}, {}, hooks)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content
    assert SENTINEL in system_content
    # The provider default must NOT have been loaded on top of the explicit base.
    assert "Anthropic Profile" not in system_content


# ---------------------------------------------------------------------------
# _resolve_system_prompt_file: CWD-independent, fail-loud path resolution
# (must-fix 1 — council BLOCKER)
# ---------------------------------------------------------------------------


def test_resolve_relative_system_prompt_file_is_cwd_independent(monkeypatch):
    """A RELATIVE system_prompt_file resolves to the bundle-root file regardless of CWD.

    Council BLOCKER: resolution must anchor on the module's __file__, never the
    process working directory. We prove this by chdir'ing to an unrelated dir
    (/tmp) and confirming the attractor bundle's own context/system-anthropic.md
    still resolves and is readable. This is exactly what lets a different consumer
    (e.g. the dot-graph resolver), launched from anywhere, reuse attractor's prompts.
    """
    import os
    import tempfile

    # The loop-agent module installs editable inside the attractor bundle, so the
    # bundle ships these provider base prompts at <bundle-root>/context/.
    rel = "context/system-anthropic.md"

    with tempfile.TemporaryDirectory() as other_cwd:
        monkeypatch.chdir(other_cwd)
        assert os.getcwd() == os.path.realpath(other_cwd) or os.getcwd() == other_cwd

        resolved = _resolve_system_prompt_file(rel)

        assert resolved.is_absolute()
        assert resolved.is_file(), f"expected an existing file, got {resolved}"
        assert resolved.name == "system-anthropic.md"
        # Resolved against the module's bundle root, NOT the (temp) CWD.
        assert str(other_cwd) not in str(resolved)
        # And it is genuinely readable (the content becomes Layer-1).
        assert resolved.read_text(encoding="utf-8").strip()


def test_resolve_relative_system_prompt_file_same_from_any_cwd(monkeypatch):
    """Resolution is deterministic: the same absolute path from two different CWDs."""
    import tempfile

    rel = "context/system-gemini.md"

    with tempfile.TemporaryDirectory() as cwd_a:
        monkeypatch.chdir(cwd_a)
        resolved_a = _resolve_system_prompt_file(rel)
    with tempfile.TemporaryDirectory() as cwd_b:
        monkeypatch.chdir(cwd_b)
        resolved_b = _resolve_system_prompt_file(rel)

    assert resolved_a == resolved_b


def test_resolve_missing_relative_file_raises_clear_actionable_error(monkeypatch):
    """A missing RELATIVE file raises a clear error naming the value AND a tried path."""
    import tempfile

    bogus = "context/system-does-not-exist-zzz.md"

    with tempfile.TemporaryDirectory() as other_cwd:
        monkeypatch.chdir(other_cwd)
        with pytest.raises(FileNotFoundError) as exc:
            _resolve_system_prompt_file(bogus)

    msg = str(exc.value)
    # Names the configured value...
    assert bogus in msg
    # ...names an absolute path it tried (not a bare "not found")...
    assert "system-does-not-exist-zzz.md" in msg
    # ...and states it is CWD-independent (so the user doesn't chase a CWD red herring).
    assert "working directory" in msg.lower()
    # ...and points at the fix / design doc.
    assert "system_prompt_file" in msg


def test_resolve_missing_absolute_file_raises_clear_error(tmp_path):
    """A missing ABSOLUTE file raises a clear error naming the path."""
    missing = tmp_path / "nope" / "base.md"
    with pytest.raises(FileNotFoundError) as exc:
        _resolve_system_prompt_file(str(missing))
    msg = str(exc.value)
    assert str(missing) in msg
    assert "system_prompt_file" in msg


def test_resolve_existing_absolute_file_used_as_is(tmp_path):
    """An existing ABSOLUTE file is returned unchanged."""
    f = tmp_path / "base.md"
    f.write_text("ABS-BASE", encoding="utf-8")
    resolved = _resolve_system_prompt_file(str(f))
    assert resolved == f
    assert resolved.read_text(encoding="utf-8") == "ABS-BASE"
