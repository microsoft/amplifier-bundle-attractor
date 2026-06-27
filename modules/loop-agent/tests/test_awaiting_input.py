"""Tests for AWAITING_INPUT state detection (1a4).

Verifies that:
1. Question-like text -> AWAITING_INPUT state
2. Non-question text -> IDLE state (existing behavior)
3. The heuristic correctly identifies questions
4. resume_with_input() continues the session
5. AGENT_AWAITING_INPUT event is emitted
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig
from amplifier_module_loop_agent.state import SessionState


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    return tool


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


# --- Heuristic unit tests ---


class TestLooksLikeQuestion:
    """Unit tests for the question detection heuristic."""

    def test_ends_with_question_mark(self):
        assert AgentSession._looks_like_question("What file should I edit?") is True

    def test_plain_statement(self):
        assert AgentSession._looks_like_question("I've completed the task.") is False

    def test_empty_string(self):
        assert AgentSession._looks_like_question("") is False

    def test_whitespace_only(self):
        assert AgentSession._looks_like_question("   \n  ") is False

    def test_question_with_trailing_whitespace(self):
        assert AgentSession._looks_like_question("What do you think?  \n") is True

    def test_question_before_code_fence(self):
        text = "Should I use this approach?\n```python\nx = 1\n```"
        assert AgentSession._looks_like_question(text) is False

    def test_question_after_code_fence(self):
        text = "```python\nx = 1\n```\nDoes this look correct?"
        assert AgentSession._looks_like_question(text) is True

    def test_question_mark_in_middle_not_at_end(self):
        assert AgentSession._looks_like_question(
            "What? I already did that."
        ) is False

    def test_multiline_question_at_end(self):
        text = "I found two options:\n1. Option A\n2. Option B\nWhich do you prefer?"
        assert AgentSession._looks_like_question(text) is True


# --- Integration tests ---


@pytest.mark.asyncio
async def test_question_response_enters_awaiting_input():
    """Model asking a question -> AWAITING_INPUT state."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_text_response("Which file should I edit?")
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
    )
    result = await session.process_input("fix the bug")

    assert result == "Which file should I edit?"
    assert session._state_machine.state == SessionState.AWAITING_INPUT


@pytest.mark.asyncio
async def test_statement_response_enters_idle():
    """Model making a statement -> IDLE state (existing behavior)."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_text_response("I've fixed the bug.")
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
    )
    result = await session.process_input("fix the bug")

    assert result == "I've fixed the bug."
    assert session._state_machine.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_awaiting_input_event_emitted():
    """AGENT_AWAITING_INPUT event is emitted for questions."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_text_response("What framework are you using?")
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
    )
    await session.process_input("add tests")

    event_names = [e for e, _ in hooks._emitted]
    assert "agent:awaiting_input" in event_names

    # session_end should NOT be emitted
    assert "agent:session_end" not in event_names


@pytest.mark.asyncio
async def test_session_end_not_emitted_for_question():
    """SESSION_END is not emitted when entering AWAITING_INPUT."""
    provider = AsyncMock()
    provider.complete = AsyncMock(
        return_value=_text_response("Do you want me to proceed?")
    )
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={},
        hooks=hooks,
    )
    await session.process_input("start")

    event_names = [e for e, _ in hooks._emitted]
    assert "agent:session_end" not in event_names


@pytest.mark.asyncio
async def test_resume_with_input_continues_session():
    """resume_with_input() transitions back and processes the answer."""
    call_count = 0

    async def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _text_response("What file?")
        else:
            return _text_response("Done editing src/main.py.")

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=side_effect)
    hooks = _make_hooks()

    session = AgentSession(
        config=SessionConfig(system_prompt="You are a test coding agent."),
        provider=provider,
        tools={"read_file": _make_mock_tool("read_file")},
        hooks=hooks,
    )

    # First input -> question
    result1 = await session.process_input("edit the main file")
    assert session._state_machine.state == SessionState.AWAITING_INPUT
    assert result1 == "What file?"

    # Resume with answer
    result2 = await session.resume_with_input("src/main.py")
    assert result2 == "Done editing src/main.py."
    assert session._state_machine.state == SessionState.IDLE
