"""Tests for provider-aligned tool presentation (Sprint 2, Task 2.2 / GAP-AL-03).

Verifies that:
  1. Provider name is flexibly resolved to a canonical provider ID
     (e.g. "provider-anthropic" -> "anthropic")
  2. The system prompt base comes from orchestrator config
  3. Only the tools mounted by the bundle appear in the ChatRequest
  4. Provider-specific project docs are loaded based on resolved ID
  5. Fallback to generic when provider can't be identified

Spec coverage: Section 3 (Provider-Aligned Toolsets), Section 6.5 (Project
Document Discovery).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

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
        content=[{"type": "text", "text": text}],  # type: ignore[arg-type]
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_hooks() -> MagicMock:
    hooks = MagicMock()
    hooks._emitted = []  # list of (event, data) tuples

    async def _recording_emit(event: str, data: dict) -> MagicMock:
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


def _make_tool(name: str, description: str = "") -> MagicMock:
    """Create a mock tool with the standard Tool protocol attributes."""
    tool = MagicMock()
    tool.name = name
    tool.description = description or f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    return tool


# ---------------------------------------------------------------------------
# 1. Flexible provider name resolution
# ---------------------------------------------------------------------------


class TestProviderNameResolution:
    """The orchestrator must flexibly resolve provider names to canonical IDs."""

    @pytest.mark.asyncio
    async def test_resolve_anthropic_from_plain_name(self):
        """'anthropic' resolves to canonical 'anthropic' for project docs."""
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
            provider_name="anthropic",
        )

        resolved = session._resolve_provider_id()
        assert resolved == "anthropic"

    @pytest.mark.asyncio
    async def test_resolve_anthropic_from_prefixed_name(self):
        """'provider-anthropic' resolves to canonical 'anthropic'."""
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
            provider_name="provider-anthropic",
        )

        resolved = session._resolve_provider_id()
        assert resolved == "anthropic"

    @pytest.mark.asyncio
    async def test_resolve_openai_from_prefixed_name(self):
        """'provider-openai' resolves to canonical 'openai'."""
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
            provider_name="provider-openai",
        )

        resolved = session._resolve_provider_id()
        assert resolved == "openai"

    @pytest.mark.asyncio
    async def test_resolve_gemini_from_prefixed_name(self):
        """'provider-gemini' resolves to canonical 'gemini'."""
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
            provider_name="provider-gemini",
        )

        resolved = session._resolve_provider_id()
        assert resolved == "gemini"

    @pytest.mark.asyncio
    async def test_resolve_unknown_provider_returns_none(self):
        """Unknown provider name resolves to None (generic)."""
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
            provider_name="some-custom-provider",
        )

        resolved = session._resolve_provider_id()
        assert resolved is None

    @pytest.mark.asyncio
    async def test_resolve_empty_provider_returns_none(self):
        """Empty provider name resolves to None."""
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
            provider_name="",
        )

        resolved = session._resolve_provider_id()
        assert resolved is None

    @pytest.mark.asyncio
    async def test_resolve_case_insensitive(self):
        """Provider resolution is case-insensitive."""
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
            provider_name="Provider-Anthropic",
        )

        resolved = session._resolve_provider_id()
        assert resolved == "anthropic"


# ---------------------------------------------------------------------------
# 2. Resolved provider ID used in system prompt assembly
# ---------------------------------------------------------------------------


class TestResolvedProviderInPrompt:
    """The resolved provider ID flows into project doc discovery and env context."""

    @pytest.mark.asyncio
    async def test_prefixed_provider_name_loads_correct_project_docs(self, tmp_path):
        """Even with 'provider-anthropic', CLAUDE.md should be loaded."""
        # Create provider-specific doc
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude-specific rules\nUse edit_file for edits.")

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
            provider_name="provider-anthropic",
        )
        await session.process_input("hello")

        request = provider.complete.call_args[0][0]
        system_content = request.messages[0].content
        assert "Use edit_file for edits." in system_content

    @pytest.mark.asyncio
    async def test_prefixed_provider_name_excludes_wrong_docs(self, tmp_path):
        """With 'provider-openai', CLAUDE.md should NOT be loaded."""
        # Create CLAUDE.md which should NOT be loaded for OpenAI
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude-specific rules\nUse edit_file for edits.")

        # Create OpenAI-specific doc which SHOULD be loaded
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        instructions = codex_dir / "instructions.md"
        instructions.write_text("# Codex rules\nUse apply_patch for edits.")

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
            provider_name="provider-openai",
        )
        await session.process_input("hello")

        request = provider.complete.call_args[0][0]
        system_content = request.messages[0].content
        assert "Use apply_patch for edits." in system_content
        assert "Use edit_file for edits." not in system_content


# ---------------------------------------------------------------------------
# 3. System prompt from orchestrator config
# ---------------------------------------------------------------------------


class TestSystemPromptFromConfig:
    """Agent reads base system prompt from orchestrator config."""

    @pytest.mark.asyncio
    async def test_system_prompt_from_config(self):
        """Agent reads base system prompt from orchestrator config."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={
                "system_prompt": "You are a Claude Code agent. Use edit_file for edits.",
                "max_tool_rounds_per_input": 1,
            },
        )
        await orch.execute("hello", MagicMock(), {"anthropic": provider}, {}, hooks)

        request = provider.complete.call_args[0][0]
        system_msg = request.messages[0]
        assert system_msg.role == "system"
        assert "Claude Code agent" in system_msg.content

    @pytest.mark.asyncio
    async def test_default_system_prompt_when_not_configured(self):
        """Falls back to generic system prompt when not configured."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1},
        )
        await orch.execute("hello", MagicMock(), {"test": provider}, {}, hooks)

        request = provider.complete.call_args[0][0]
        system_msg = request.messages[0]
        assert system_msg.role == "system"
        # Default fallback prompt
        assert "coding agent" in system_msg.content.lower()


# ---------------------------------------------------------------------------
# 4. Only mounted tools appear in ChatRequest
# ---------------------------------------------------------------------------


class TestOnlyMountedToolsInRequest:
    """Only tools mounted by the bundle appear in the ChatRequest."""

    @pytest.mark.asyncio
    async def test_only_mounted_tools_in_request_openai_profile(self):
        """OpenAI profile: only apply_patch and bash-like tools appear."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        # Simulate an OpenAI profile: only apply_patch, read_file, bash mounted
        tools = {
            "apply_patch": _make_tool("apply_patch", "Apply a code patch"),
            "read_file": _make_tool("read_file", "Read a file"),
            "bash": _make_tool("bash", "Execute shell commands"),
        }

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1},
        )
        await orch.execute("hello", MagicMock(), {"openai": provider}, tools, hooks)

        request = provider.complete.call_args[0][0]
        tool_names = [t.name for t in request.tools]
        # Mounted tools must be present
        assert "apply_patch" in tool_names
        assert "bash" in tool_names
        assert "read_file" in tool_names
        # edit_file should NOT be here (not mounted in OpenAI profile)
        assert "edit_file" not in tool_names
        # Subagent lifecycle tools are also registered by the orchestrator
        subagent_tools = {"spawn_agent", "send_input", "wait", "close_agent"}
        non_mounted_non_subagent = (
            set(tool_names) - {"apply_patch", "bash", "read_file"} - subagent_tools
        )
        assert non_mounted_non_subagent == set(), (
            f"Unexpected tools: {non_mounted_non_subagent}"
        )

    @pytest.mark.asyncio
    async def test_only_mounted_tools_in_request_anthropic_profile(self):
        """Anthropic profile: edit_file present, apply_patch absent."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        # Simulate an Anthropic profile: edit_file, read_file, write_file, bash
        tools = {
            "edit_file": _make_tool("edit_file", "Edit file with old/new string"),
            "read_file": _make_tool("read_file", "Read a file"),
            "write_file": _make_tool("write_file", "Write a file"),
            "bash": _make_tool("bash", "Execute shell commands"),
        }

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1},
        )
        await orch.execute("hello", MagicMock(), {"anthropic": provider}, tools, hooks)

        request = provider.complete.call_args[0][0]
        tool_names = [t.name for t in request.tools]
        assert "edit_file" in tool_names
        assert "apply_patch" not in tool_names

    @pytest.mark.asyncio
    async def test_no_tools_when_none_mounted_and_subagents_disabled(self):
        """When no tools are mounted and subagents disabled, request.tools is None."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        # Disable subagent tools by setting max_subagent_depth=0
        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1, "max_subagent_depth": 0},
        )
        await orch.execute("hello", MagicMock(), {"test": provider}, {}, hooks)

        request = provider.complete.call_args[0][0]
        assert request.tools is None

    @pytest.mark.asyncio
    async def test_only_subagent_tools_when_none_mounted(self):
        """When no tools are mounted but subagents allowed, only subagent tools appear."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1},
        )
        await orch.execute("hello", MagicMock(), {"test": provider}, {}, hooks)

        request = provider.complete.call_args[0][0]
        tool_names = sorted(t.name for t in request.tools)
        assert tool_names == ["close_agent", "send_input", "spawn_agent", "wait"]

    @pytest.mark.asyncio
    async def test_tool_count_matches_mounted_plus_subagent(self):
        """ChatRequest has mounted tools plus subagent lifecycle tools."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        tools = {
            "apply_patch": _make_tool("apply_patch"),
            "bash": _make_tool("bash"),
        }

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1},
        )
        await orch.execute("hello", MagicMock(), {"openai": provider}, tools, hooks)

        request = provider.complete.call_args[0][0]
        # 2 mounted + 4 subagent tools = 6
        assert len(request.tools) == 6

    @pytest.mark.asyncio
    async def test_tool_count_exact_when_subagents_disabled(self):
        """ChatRequest has exactly mounted tools when subagents disabled."""
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        hooks = _make_hooks()
        coordinator = MagicMock()

        tools = {
            "apply_patch": _make_tool("apply_patch"),
            "bash": _make_tool("bash"),
        }

        orch = AgentOrchestrator(
            coordinator=coordinator,
            config={"system_prompt": "You are a test coding agent.", "max_tool_rounds_per_input": 1, "max_subagent_depth": 0},
        )
        await orch.execute("hello", MagicMock(), {"openai": provider}, tools, hooks)

        request = provider.complete.call_args[0][0]
        assert len(request.tools) == 2


# ---------------------------------------------------------------------------
# 5. Tool descriptions in system prompt match mounted tools
# ---------------------------------------------------------------------------


class TestToolDescriptionsMatchMounted:
    """System prompt tool descriptions reflect only mounted tools."""

    @pytest.mark.asyncio
    async def test_tool_descriptions_only_contain_mounted_tools(self):
        """System prompt's tool description section only lists mounted tools."""
        config = SessionConfig.from_dict(
            {
                "system_prompt": "Base.",
                "max_tool_rounds_per_input": 1,
            }
        )
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("done"))
        hooks = _make_hooks()

        tools = {
            "apply_patch": _make_tool("apply_patch", "Apply a patch to code"),
            "bash": _make_tool("bash", "Run shell commands"),
        }

        session = AgentSession(
            config=config,
            provider=provider,
            tools=tools,
            hooks=hooks,
            provider_name="openai",
        )
        await session.process_input("hello")

        request = provider.complete.call_args[0][0]
        system_content = request.messages[0].content
        assert "apply_patch" in system_content
        assert "bash" in system_content
        # edit_file not mounted, should not appear
        assert "edit_file" not in system_content
