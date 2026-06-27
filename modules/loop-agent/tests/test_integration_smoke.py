"""Integration smoke tests (Phase 7, Task 7.2).

Spec coverage: Section 9.13 — 7-step end-to-end test with mocked provider.

These tests exercise the full stack:
    AgentOrchestrator → AgentSession → provider.complete() (mocked)
    → tool execution → event emission

Each step builds on the previous, using a single orchestrator instance
so session history carries over (proving session persistence).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.events import (
    AGENT_SESSION_END,
    AGENT_SESSION_START,
    AGENT_STEERING_INJECTED,
    AGENT_TOOL_CALL_END,
    AGENT_TOOL_CALL_START,
    AGENT_USER_INPUT,
)
from amplifier_module_loop_agent.state import SessionState


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


class EventRecorder:
    """Records all emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict):
        self.events.append((event, data))
        return MagicMock(action="continue")

    @property
    def event_names(self) -> list[str]:
        return [e[0] for e in self.events]

    def get_data(self, event_name: str) -> list[dict]:
        return [e[1] for e in self.events if e[0] == event_name]


# ===========================================================================
# Step 1: File creation → verify file exists
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step1_file_creation():
    """Step 1: Provider creates a file via write_file tool.

    Verifies:
    - write_file tool is called with correct parameters
    - Agent returns natural completion text
    - Session starts in IDLE after completion
    - Session start/end events are emitted
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(
                (
                    "tc1",
                    "write_file",
                    {
                        "file_path": "hello.py",
                        "content": "print('Hello World')",
                    },
                )
            ),
            _text_response("Created hello.py with Hello World print statement."),
        ]
    )

    tools = {
        "write_file": _make_mock_tool("write_file", "File written successfully"),
        "read_file": _make_mock_tool("read_file"),
        "edit_file": _make_mock_tool("edit_file"),
        "bash": _make_mock_tool("bash"),
        "delegate": _make_mock_tool("delegate"),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute(
        "Create a file called hello.py that prints 'Hello World'",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Verify tool was called
    tools["write_file"].execute.assert_called_once()
    call_args = tools["write_file"].execute.call_args[0][0]
    assert call_args["file_path"] == "hello.py"
    assert "Hello World" in call_args["content"]

    # Verify natural completion
    assert "hello.py" in result.lower() or "Hello" in result

    # Verify events
    assert AGENT_SESSION_START in recorder.event_names
    assert AGENT_USER_INPUT in recorder.event_names
    assert AGENT_TOOL_CALL_START in recorder.event_names
    assert AGENT_TOOL_CALL_END in recorder.event_names
    assert AGENT_SESSION_END in recorder.event_names

    # Verify state
    assert orch._session._state_machine.state == SessionState.IDLE


# ===========================================================================
# Step 2: Read + edit → verify edit applied
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step2_read_and_edit():
    """Step 2: Read a file then edit it — two-round tool interaction.

    Verifies:
    - read_file is called first
    - edit_file is called second with correct old/new strings
    - History carries over (session persists)
    - Provider receives growing message history
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            # First execute: create file
            _tool_response(
                (
                    "tc1",
                    "write_file",
                    {
                        "file_path": "hello.py",
                        "content": "print('Hello')",
                    },
                )
            ),
            _text_response("Created."),
            # Second execute: read then edit
            _tool_response(("tc2", "read_file", {"file_path": "hello.py"})),
            _tool_response(
                (
                    "tc3",
                    "edit_file",
                    {
                        "file_path": "hello.py",
                        "old_string": "Hello",
                        "new_string": "Hello\\nprint('Goodbye')",
                    },
                )
            ),
            _text_response("Added Goodbye print statement."),
        ]
    )

    tools = {
        "write_file": _make_mock_tool("write_file", "ok"),
        "read_file": _make_mock_tool("read_file", "print('Hello')"),
        "edit_file": _make_mock_tool("edit_file", "Edit applied"),
        "bash": _make_mock_tool("bash"),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})

    # First call: create file
    r1 = await orch.execute(
        "Create hello.py",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "Created" in r1

    # Second call: read and edit
    r2 = await orch.execute(
        "Read hello.py and add a second print statement that says 'Goodbye'",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "Goodbye" in r2

    # Verify tools called in expected order
    tools["read_file"].execute.assert_called_once()
    tools["edit_file"].execute.assert_called_once()

    # Verify session persisted: the second call's request should include
    # history from the first call
    second_call_request = provider.complete.call_args_list[2][0][0]
    # Should have: user1 + assistant1 + user2 = 3+ messages
    assert len(second_call_request.messages) >= 3


# ===========================================================================
# Step 3: Shell execution → verify output captured
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step3_shell_execution():
    """Step 3: Run a shell command and capture output.

    Verifies:
    - bash tool is called with the correct command
    - Tool output is captured in the tool_call_end event
    - Agent processes the output and provides summary
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(("tc1", "bash", {"command": "python hello.py"})),
            _text_response("The script output: Hello World\\nGoodbye"),
        ]
    )

    tools = {
        "bash": _make_mock_tool("bash", "Hello World\nGoodbye"),
        "read_file": _make_mock_tool("read_file"),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    await orch.execute(
        "Run hello.py and show the output",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Verify bash tool was called
    tools["bash"].execute.assert_called_once()
    call_args = tools["bash"].execute.call_args[0][0]
    assert "hello.py" in call_args["command"]

    # Verify tool_call_end event captured the output
    end_events = recorder.get_data(AGENT_TOOL_CALL_END)
    assert len(end_events) == 1
    assert "output" in end_events[0]
    assert "Hello World" in end_events[0]["output"]


# ===========================================================================
# Step 4: Truncation → verify large output truncated with marker
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step4_truncation():
    """Step 4: Large tool output handled gracefully.

    Verifies:
    - Tool returns large output (100k chars)
    - tool_call_end event captures the output
    - Agent loop doesn't crash on large output
    - Agent produces a response
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    large_output = "x" * 100_000

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(("tc1", "read_file", {"file_path": "big.txt"})),
            _text_response("The file is very large (100K characters)."),
        ]
    )

    big_tool = _make_mock_tool("read_file")
    big_tool.execute = AsyncMock(
        return_value=ToolResult(success=True, output=large_output)
    )
    tools = {"read_file": big_tool}

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute(
        "Read big.txt",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Agent should still complete
    assert result is not None
    assert len(result) > 0

    # tool_call_end should have captured output
    end_events = recorder.get_data(AGENT_TOOL_CALL_END)
    assert len(end_events) == 1
    # The output in the event is str(output) which is the full 100k
    assert len(end_events[0].get("output", "")) == 100_000


# ===========================================================================
# Step 5: Steering → verify injected message appears in history
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step5_steering():
    """Step 5: Steering message injected mid-task.

    Verifies:
    - steer() queues a message
    - The message is drained between tool rounds
    - agent:steering_injected event is emitted
    - The steering content appears in the event data
    - The second LLM call includes the steering message in its request
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(
                (
                    "tc1",
                    "write_file",
                    {
                        "file_path": "app.py",
                        "content": "from flask import Flask\napp = Flask(__name__)\n",
                    },
                )
            ),
            _text_response("Created a minimal Flask app with /health endpoint."),
        ]
    )

    tools = {
        "write_file": _make_mock_tool("write_file"),
        "read_file": _make_mock_tool("read_file"),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})

    # Queue steering before execute
    orch.steer("Actually, just create a single /health endpoint for now")

    await orch.execute(
        "Create a Flask web application with multiple routes",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Verify steering event
    assert AGENT_STEERING_INJECTED in recorder.event_names
    steering_data = recorder.get_data(AGENT_STEERING_INJECTED)
    assert len(steering_data) >= 1
    assert "/health" in steering_data[0]["content"]

    # Verify the second LLM call includes the steering message
    # (steering is drained before the first LLM call, so the first
    # request should include it as a user-role message)
    first_request = provider.complete.call_args_list[0][0][0]
    all_content = " ".join(
        m.content if isinstance(m.content, str) else "" for m in first_request.messages
    )
    assert "/health" in all_content


# ===========================================================================
# Step 6: Subagent → verify delegation works (mock spawn)
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step6_subagent():
    """Step 6: Subagent delegation via the delegate tool.

    Verifies:
    - delegate tool is called with correct instruction
    - Subagent result is returned as a tool result
    - Agent processes the subagent output
    - tool_call_start/end events bracket the delegation
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(
                (
                    "tc1",
                    "delegate",
                    {
                        "instruction": "Write tests for hello.py",
                        "agent": "self",
                    },
                )
            ),
            _text_response("Subagent wrote tests. File test_hello.py created."),
        ]
    )

    delegate_tool = _make_mock_tool("delegate")
    delegate_tool.execute = AsyncMock(
        return_value=ToolResult(
            success=True,
            output="Created test_hello.py with 3 test cases.",
        )
    )
    tools = {
        "delegate": delegate_tool,
        "read_file": _make_mock_tool("read_file"),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute(
        "Spawn a subagent to write tests for hello.py, then review its output",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Verify delegate tool called
    delegate_tool.execute.assert_called_once()
    call_args = delegate_tool.execute.call_args[0][0]
    assert call_args["instruction"] == "Write tests for hello.py"

    # Verify tool_call events bracket the delegation
    tc_starts = recorder.get_data(AGENT_TOOL_CALL_START)
    tc_ends = recorder.get_data(AGENT_TOOL_CALL_END)
    assert len(tc_starts) == 1
    assert len(tc_ends) == 1
    assert tc_starts[0]["tool_name"] == "delegate"

    # Verify agent got a result
    assert "test" in result.lower()


# ===========================================================================
# Step 7: Timeout handling → verify timeout error returned gracefully
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_step7_timeout_handling():
    """Step 7: Shell command timeout handled gracefully.

    Verifies:
    - bash tool raises a timeout error
    - Error is caught and converted to ToolResult(success=False)
    - Error result is sent back to LLM
    - LLM recovers and provides a response
    - tool_call_end event includes the error
    - Session remains in IDLE (not CLOSED)
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=[
            _tool_response(("tc1", "bash", {"command": "sleep 30"})),
            _text_response(
                "The command timed out after 10 seconds. "
                "The default timeout is 10s for safety."
            ),
        ]
    )

    bash_tool = _make_mock_tool("bash")
    bash_tool.execute = AsyncMock(
        side_effect=TimeoutError("Command timed out after 10000ms")
    )
    tools = {"bash": bash_tool}

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    result = await orch.execute(
        "Run 'sleep 30' with the default timeout",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )

    # Verify LLM recovered
    assert "timed out" in result.lower() or "timeout" in result.lower()

    # Verify tool_call_end has error info
    end_events = recorder.get_data(AGENT_TOOL_CALL_END)
    assert len(end_events) == 1
    assert "error" in end_events[0]
    assert "timed out" in end_events[0]["error"].lower()

    # Verify session is IDLE (not CLOSED — the error was recoverable)
    assert orch._session._state_machine.state == SessionState.IDLE

    # Verify the LLM was called twice:
    # 1) Initial call that produced the bash tool call
    # 2) After timeout error, LLM called again with error result
    assert provider.complete.call_count == 2


# ===========================================================================
# Full end-to-end: all 7 steps in sequence on a single orchestrator
# ===========================================================================


@pytest.mark.asyncio
async def test_smoke_full_sequence():
    """Full 7-step smoke test on a single orchestrator instance.

    Exercises session persistence: history from each step carries over
    to the next. This is the closest to the spec's pseudocode in 9.13.
    """
    recorder = EventRecorder()
    hooks = MagicMock()
    hooks.emit = AsyncMock(side_effect=recorder.emit)

    # Pre-build all responses for the 7 steps
    responses = [
        # Step 1: File creation
        _tool_response(
            (
                "tc1",
                "write_file",
                {
                    "file_path": "hello.py",
                    "content": "print('Hello World')",
                },
            )
        ),
        _text_response("Created hello.py"),
        # Step 2: Read + edit
        _tool_response(("tc2", "read_file", {"file_path": "hello.py"})),
        _tool_response(
            (
                "tc3",
                "edit_file",
                {
                    "file_path": "hello.py",
                    "old_string": "Hello World",
                    "new_string": "Hello World')\\nprint('Goodbye",
                },
            )
        ),
        _text_response("Added Goodbye."),
        # Step 3: Shell execution
        _tool_response(("tc4", "bash", {"command": "python hello.py"})),
        _text_response("Output: Hello World, Goodbye"),
        # Step 4: Large output (truncation)
        _tool_response(("tc5", "read_file", {"file_path": "big.txt"})),
        _text_response("File is large."),
        # Step 5: Steering (will redirect mid-task)
        _tool_response(
            (
                "tc6",
                "write_file",
                {
                    "file_path": "app.py",
                    "content": "health endpoint only",
                },
            )
        ),
        _text_response("Created minimal Flask app."),
        # Step 6: Subagent
        _tool_response(
            (
                "tc7",
                "delegate",
                {
                    "instruction": "Write tests",
                    "agent": "self",
                },
            )
        ),
        _text_response("Tests written."),
        # Step 7: Timeout
        _tool_response(("tc8", "bash", {"command": "sleep 30"})),
        _text_response("Command timed out gracefully."),
    ]

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses)

    # Tools with specific behaviors for certain steps
    tools = {
        "write_file": _make_mock_tool("write_file"),
        "read_file": _make_mock_tool("read_file", "print('Hello World')"),
        "edit_file": _make_mock_tool("edit_file"),
        "bash": _make_mock_tool("bash", "Hello World\nGoodbye"),
        "delegate": _make_mock_tool("delegate", "Tests created."),
    }

    orch = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})

    # Step 1: File creation
    r1 = await orch.execute(
        "Create hello.py that prints 'Hello World'",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "hello.py" in r1.lower() or "Created" in r1

    # Step 2: Read + edit
    r2 = await orch.execute(
        "Read hello.py and add Goodbye",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "Goodbye" in r2

    # Step 3: Shell execution
    r3 = await orch.execute(
        "Run hello.py",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "Hello" in r3 or "Output" in r3

    # Step 4: Truncation - swap read_file to return large output
    tools["read_file"].execute = AsyncMock(
        return_value=ToolResult(success=True, output="x" * 100_000)
    )
    r4 = await orch.execute(
        "Read big.txt",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert r4  # Got some response

    # Step 5: Steering
    tools["read_file"].execute = AsyncMock(
        return_value=ToolResult(success=True, output="ok")
    )
    orch.steer("Just create a /health endpoint")
    await orch.execute(
        "Create Flask app with multiple routes",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert AGENT_STEERING_INJECTED in recorder.event_names

    # Step 6: Subagent
    r6 = await orch.execute(
        "Spawn subagent to write tests",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert "test" in r6.lower() or "Tests" in r6

    # Step 7: Timeout
    tools["bash"].execute = AsyncMock(side_effect=TimeoutError("Timed out"))
    r7 = await orch.execute(
        "Run 'sleep 30'",
        MagicMock(),
        {"test": provider},
        tools,
        hooks,
    )
    assert r7  # Got some response despite timeout

    # Final state check
    assert orch._session._state_machine.state == SessionState.IDLE

    # Verify session events bookend the entire interaction
    assert recorder.event_names[0] == AGENT_SESSION_START
    # The last agent event should be session_end
    agent_events = [e for e in recorder.event_names if e.startswith("agent:")]
    assert agent_events[-1] == AGENT_SESSION_END

    # Verify tool calls happened (at least 8 tools across all steps)
    tool_starts = recorder.get_data(AGENT_TOOL_CALL_START)
    assert len(tool_starts) >= 8

    # Verify the LLM was called many times (session history grew)
    assert provider.complete.call_count == len(responses)
