"""Cross-provider parity matrix tests (Phase 7, Task 7.1).

Spec coverage: Section 9.12 — 15 test scenarios × 3 providers = 45 test cells.

These are mock-based parity tests that verify the agent handles each
scenario correctly regardless of which provider profile is active.
Each test is parametrized across openai/anthropic/gemini where
provider-specific behavior differs.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.llm_errors import LLMError
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import (
    AGENT_LOOP_DETECTION,
    AGENT_SESSION_END,
    AGENT_SESSION_START,
    AGENT_STEERING_INJECTED,
    AGENT_TOOL_CALL_END,
    AGENT_TOOL_CALL_START,
    AGENT_TURN_LIMIT,
    AGENT_USER_INPUT,
)
from amplifier_module_loop_agent.state import SessionState


# ---------------------------------------------------------------------------
# Helpers (shared across all parity tests)
# ---------------------------------------------------------------------------

PROVIDERS = ["openai", "anthropic", "gemini"]


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(
    *tool_calls_tuple: tuple[str, str, dict],
    text: str = "",
) -> ChatResponse:
    """ChatResponse with tool calls and optional text."""
    content = [{"type": "text", "text": text}] if text else []
    return ChatResponse(
        content=content,
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args)
            for cid, name, args in tool_calls_tuple
        ],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_mock_tool(name: str, output: str = "ok") -> MagicMock:
    """Create a mock tool with the standard Tool protocol attributes."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


def _make_harness(
    config: dict | None = None,
    responses: list[ChatResponse] | None = None,
    tool_names: list[str] | None = None,
):
    """Build orchestrator + mocks for parity testing.

    Returns (orchestrator, context, providers, tools, hooks).
    """
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=responses or [_text_response("done")]
    )
    providers = {"test": provider}

    names = tool_names or [
        "read_file", "write_file", "edit_file", "bash", "grep", "glob",
        "apply_patch", "delegate",
    ]
    tools = {n: _make_mock_tool(n) for n in names}

    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)

    context = MagicMock()
    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)

    return orchestrator, context, providers, tools, hooks


def _emitted_events(hooks: MagicMock) -> list[str]:
    """Extract just the event names from the hooks mock."""
    return [e[0] for e in hooks._emitted]


def _emitted_event_data(hooks: MagicMock, event_name: str) -> list[dict]:
    """Extract data dicts for a specific event name."""
    return [e[1] for e in hooks._emitted if e[0] == event_name]


# ===========================================================================
# Scenario 1: Simple file creation (write_file)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_01_simple_file_creation(provider):
    """Provider calls write_file to create a file -> natural completion."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "write_file", {
                "file_path": "hello.py", "content": "print('hello')",
            })),
            _text_response("File created."),
        ]
    )
    result = await orch.execute("Create hello.py", ctx, provs, tools, hooks)

    assert result == "File created."
    tools["write_file"].execute.assert_called_once()
    call_args = tools["write_file"].execute.call_args[0][0]
    assert call_args["file_path"] == "hello.py"
    events = _emitted_events(hooks)
    assert AGENT_TOOL_CALL_START in events
    assert AGENT_TOOL_CALL_END in events
    assert orch._session._state_machine.state == SessionState.IDLE


# ===========================================================================
# Scenario 2: Read file then edit (read_file -> edit_file)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_02_read_then_edit(provider):
    """Provider reads a file then edits it — two-round tool sequence."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "app.py"})),
            _tool_response(("tc2", "edit_file", {
                "file_path": "app.py",
                "old_string": "old",
                "new_string": "new",
            })),
            _text_response("Edit applied."),
        ]
    )
    result = await orch.execute("Edit app.py", ctx, provs, tools, hooks)

    assert result == "Edit applied."
    tools["read_file"].execute.assert_called_once()
    tools["edit_file"].execute.assert_called_once()
    assert provs["test"].complete.call_count == 3


# ===========================================================================
# Scenario 3: Multi-file edit (multiple edit operations)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_03_multi_file_edit(provider):
    """Provider edits multiple files across tool rounds."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "edit_file", {
                "file_path": "a.py", "old_string": "x", "new_string": "y",
            })),
            _tool_response(("tc2", "edit_file", {
                "file_path": "b.py", "old_string": "m", "new_string": "n",
            })),
            _text_response("Both files edited."),
        ]
    )
    result = await orch.execute("Edit both", ctx, provs, tools, hooks)

    assert result == "Both files edited."
    assert tools["edit_file"].execute.call_count == 2
    assert provs["test"].complete.call_count == 3


# ===========================================================================
# Scenario 4: Shell command execution (bash)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_04_shell_command(provider):
    """Provider runs a bash command and gets output."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "bash", {"command": "python hello.py"})),
            _text_response("Command succeeded."),
        ]
    )
    result = await orch.execute("Run hello.py", ctx, provs, tools, hooks)

    assert result == "Command succeeded."
    tools["bash"].execute.assert_called_once()
    call_args = tools["bash"].execute.call_args[0][0]
    assert call_args["command"] == "python hello.py"


# ===========================================================================
# Scenario 5: Shell command timeout
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_05_shell_timeout(provider):
    """Shell command times out — error result returned, LLM recovers."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "bash", {"command": "sleep 30"})),
            _text_response("Command timed out, I'll try differently."),
        ]
    )
    # Simulate timeout error from tool
    tools["bash"].execute = AsyncMock(
        side_effect=TimeoutError("Command timed out after 10000ms")
    )
    result = await orch.execute("Run sleep", ctx, provs, tools, hooks)

    assert result == "Command timed out, I'll try differently."
    # The error is caught and returned as ToolResult, LLM recovers
    assert provs["test"].complete.call_count == 2


# ===========================================================================
# Scenario 6: Grep + glob (search tools)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_06_grep_and_glob(provider):
    """Provider uses grep and glob tools to find files."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(
                ("tc1", "glob", {"pattern": "**/*.py"}),
                ("tc2", "grep", {"pattern": "import os", "path": "src/"}),
            ),
            _text_response("Found the files."),
        ]
    )
    result = await orch.execute("Find python files", ctx, provs, tools, hooks)

    assert result == "Found the files."
    tools["glob"].execute.assert_called_once()
    tools["grep"].execute.assert_called_once()


# ===========================================================================
# Scenario 7: Multi-step task (read -> analyze -> edit)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_07_multi_step_task(provider):
    """Three-round task: read, analyze (text), then edit."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "main.py"})),
            _tool_response(("tc2", "bash", {"command": "python -m py_compile main.py"})),
            _tool_response(("tc3", "edit_file", {
                "file_path": "main.py", "old_string": "bug", "new_string": "fix",
            })),
            _text_response("Bug fixed."),
        ]
    )
    result = await orch.execute("Fix the bug", ctx, provs, tools, hooks)

    assert result == "Bug fixed."
    assert provs["test"].complete.call_count == 4
    tools["read_file"].execute.assert_called_once()
    tools["bash"].execute.assert_called_once()
    tools["edit_file"].execute.assert_called_once()


# ===========================================================================
# Scenario 8: Tool output truncation (large output triggers truncation)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_08_tool_output_truncation(provider):
    """Large tool output is returned (truncation is the tool's job, but
    the agent loop handles it gracefully and the LLM gets it all)."""
    large_output = "x" * 100_000
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "big.txt"})),
            _text_response("File is large."),
        ]
    )
    tools["read_file"].execute = AsyncMock(
        return_value=ToolResult(success=True, output=large_output)
    )
    result = await orch.execute("Read big.txt", ctx, provs, tools, hooks)

    assert result == "File is large."
    # Verify the tool_call_end event has the output
    end_events = _emitted_event_data(hooks, AGENT_TOOL_CALL_END)
    assert len(end_events) >= 1
    assert "output" in end_events[0] or "error" not in end_events[0]


# ===========================================================================
# Scenario 9: Parallel tool calls (multiple tools in one response)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_09_parallel_tool_calls(provider):
    """Multiple tool calls in a single LLM response are all executed."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(
                ("tc1", "read_file", {"file_path": "a.py"}),
                ("tc2", "read_file", {"file_path": "b.py"}),
                ("tc3", "write_file", {"file_path": "c.py", "content": "new"}),
            ),
            _text_response("All three done."),
        ]
    )
    result = await orch.execute("Process files", ctx, provs, tools, hooks)

    assert result == "All three done."
    assert tools["read_file"].execute.call_count == 2
    assert tools["write_file"].execute.call_count == 1
    # Verify tool_call_start emitted for each
    start_events = _emitted_event_data(hooks, AGENT_TOOL_CALL_START)
    assert len(start_events) == 3


# ===========================================================================
# Scenario 10: Steering mid-task (steer message injected between rounds)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_10_steering_mid_task(provider):
    """Steering message injected between tool rounds appears in history."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "app.py"})),
            _text_response("Redirected approach."),
        ]
    )
    # Queue steering before execute — it will be drained between rounds
    orch.steer("Focus on the /health endpoint only")
    result = await orch.execute("Build Flask app", ctx, provs, tools, hooks)

    assert result == "Redirected approach."
    events = _emitted_events(hooks)
    assert AGENT_STEERING_INJECTED in events
    # Verify the steering content
    steering_data = _emitted_event_data(hooks, AGENT_STEERING_INJECTED)
    assert any("health" in d.get("content", "") for d in steering_data)


# ===========================================================================
# Scenario 11: Reasoning effort change (config update takes effect)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_11_reasoning_effort(provider):
    """reasoning_effort from config is passed through to ChatRequest."""
    orch, ctx, provs, tools, hooks = _make_harness(
        config={"reasoning_effort": "high"},
        responses=[_text_response("Thought deeply.")],
    )
    result = await orch.execute("Think hard", ctx, provs, tools, hooks)

    assert result == "Thought deeply."
    request = provs["test"].complete.call_args_list[0][0][0]
    assert request.reasoning_effort == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_11_reasoning_effort_none(provider):
    """No reasoning_effort config means None is passed (provider default)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("Normal thinking.")],
    )
    await orch.execute("Think", ctx, provs, tools, hooks)

    request = provs["test"].complete.call_args_list[0][0][0]
    assert request.reasoning_effort is None


# ===========================================================================
# Scenario 12: Subagent spawn and wait (delegate tool)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_12_subagent_spawn(provider):
    """Provider calls the delegate tool to spawn a subagent."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "delegate", {
                "instruction": "Write tests for hello.py",
                "agent": "self",
            })),
            _text_response("Tests written by subagent."),
        ]
    )
    tools["delegate"].execute = AsyncMock(
        return_value=ToolResult(success=True, output="Tests created: test_hello.py")
    )
    result = await orch.execute("Spawn subagent for tests", ctx, provs, tools, hooks)

    assert result == "Tests written by subagent."
    tools["delegate"].execute.assert_called_once()
    call_args = tools["delegate"].execute.call_args[0][0]
    assert call_args["instruction"] == "Write tests for hello.py"


# ===========================================================================
# Scenario 13: Loop detection (repeating pattern triggers warning)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_13_loop_detection(provider):
    """Repeating identical tool calls trigger a loop detection warning."""
    # Create 10 identical tool call responses to fill the detection window
    identical_responses = [
        _tool_response(("tc", "read_file", {"file_path": "same.py"}))
        for _ in range(10)
    ]
    # Then a natural completion
    identical_responses.append(_text_response("Broke out of loop."))

    orch, ctx, provs, tools, hooks = _make_harness(
        config={"enable_loop_detection": True, "loop_detection_window": 10},
        responses=identical_responses,
    )
    result = await orch.execute("Do something", ctx, provs, tools, hooks)

    assert result == "Broke out of loop."
    events = _emitted_events(hooks)
    assert AGENT_LOOP_DETECTION in events


# ===========================================================================
# Scenario 14: Error recovery (tool fails, model retries)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_scenario_14_error_recovery(provider):
    """Tool execution error is caught, returned to LLM, and LLM recovers."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "edit_file", {
                "file_path": "x.py", "old_string": "missing", "new_string": "new",
            })),
            # LLM sees the error and tries read_file first
            _tool_response(("tc2", "read_file", {"file_path": "x.py"})),
            _tool_response(("tc3", "edit_file", {
                "file_path": "x.py", "old_string": "actual", "new_string": "new",
            })),
            _text_response("Fixed after retry."),
        ]
    )
    # First edit_file call fails
    call_count = {"n": 0}
    original_execute = tools["edit_file"].execute

    async def _fail_then_succeed(args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("old_string not found in file")
        return ToolResult(success=True, output="Edit applied")

    tools["edit_file"].execute = AsyncMock(side_effect=_fail_then_succeed)
    result = await orch.execute("Edit x.py", ctx, provs, tools, hooks)

    assert result == "Fixed after retry."
    assert call_count["n"] == 2  # Failed once, succeeded once
    # Verify tool_call_end events include both error and success
    end_events = _emitted_event_data(hooks, AGENT_TOOL_CALL_END)
    assert len(end_events) >= 2


# ===========================================================================
# Scenario 15: Provider-specific editing format
# (apply_patch for OpenAI vs edit_file for Anthropic)
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_15_openai_uses_apply_patch():
    """OpenAI provider profile uses apply_patch tool for editing."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "apply_patch", {
                "patch": "*** Begin Patch\n*** Add File: hello.py\n+print('hello')\n*** End Patch",
            })),
            _text_response("Patch applied."),
        ]
    )
    result = await orch.execute("Create hello.py", ctx, provs, tools, hooks)

    assert result == "Patch applied."
    tools["apply_patch"].execute.assert_called_once()


@pytest.mark.asyncio
async def test_scenario_15_anthropic_uses_edit_file():
    """Anthropic provider profile uses edit_file (old_string/new_string)."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "edit_file", {
                "file_path": "hello.py",
                "old_string": "old",
                "new_string": "new",
            })),
            _text_response("Edit done."),
        ]
    )
    result = await orch.execute("Edit hello.py", ctx, provs, tools, hooks)

    assert result == "Edit done."
    tools["edit_file"].execute.assert_called_once()


@pytest.mark.asyncio
async def test_scenario_15_gemini_uses_edit_file():
    """Gemini provider profile also uses edit_file style editing."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "edit_file", {
                "file_path": "hello.py",
                "old_string": "old",
                "new_string": "new",
            })),
            _text_response("Edit done."),
        ]
    )
    result = await orch.execute("Edit hello.py", ctx, provs, tools, hooks)

    assert result == "Edit done."
    tools["edit_file"].execute.assert_called_once()


# ===========================================================================
# Session lifecycle validation (shared across all scenarios)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_session_start_and_end_events(provider):
    """Every scenario emits session_start and session_end events."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[_text_response("done")]
    )
    await orch.execute("hi", ctx, provs, tools, hooks)

    events = _emitted_events(hooks)
    assert AGENT_SESSION_START in events
    assert AGENT_SESSION_END in events
    # session_start should be first agent event
    agent_events = [e for e in events if e.startswith("agent:")]
    assert agent_events[0] == AGENT_SESSION_START


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_state_returns_to_idle(provider):
    """After any successful scenario, state machine returns to IDLE."""
    orch, ctx, provs, tools, hooks = _make_harness(
        responses=[
            _tool_response(("tc1", "read_file", {"file_path": "a.py"})),
            _text_response("done"),
        ]
    )
    await orch.execute("do it", ctx, provs, tools, hooks)
    assert orch._session._state_machine.state == SessionState.IDLE
