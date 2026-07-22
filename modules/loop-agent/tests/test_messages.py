"""Tests for history-to-messages conversion (Task 1.8).

Spec coverage: LOOP-010, STEER-003, STEER-010.
Verifies that typed Turn history is correctly converted to Message
objects for ChatRequest, with system-first ordering, content blocks,
ThinkingBlock preservation, and edge case handling.
"""

from amplifier_core.message_models import TextBlock, ThinkingBlock
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent.messages import convert_history_to_messages
from amplifier_module_loop_agent.turns import (
    AssistantTurn,
    SteeringTurn,
    SystemTurn,
    ToolResultsTurn,
    UserTurn,
)


# ---------------------------------------------------------------------------
# Basic turn type conversions
# ---------------------------------------------------------------------------


def test_user_turn_becomes_user_message():
    turns = [UserTurn(content="hello")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"


def test_system_turn_becomes_system_message():
    turns = [SystemTurn(content="You are a coding agent.")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are a coding agent."


def test_steering_turn_becomes_user_message():
    """SteeringTurn is converted to user-role message (STEER-003, STEER-010)."""
    turns = [SteeringTurn(content="try differently")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert "try differently" in msgs[0].content


def test_assistant_turn_text_only():
    """AssistantTurn with text only → assistant message."""
    turns = [AssistantTurn(content="Hello!")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    # Content should contain the text
    content = msgs[0].content
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, TextBlock)]
        assert len(text_blocks) == 1
        assert text_blocks[0].text == "Hello!"
    else:
        assert content == "Hello!"


def test_assistant_turn_with_tool_calls():
    """AssistantTurn with tool calls → tool_calls on message."""
    turns = [
        AssistantTurn(
            content="Let me read that file.",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {"path": "x.py"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    # tool_calls should be present (via extra fields)
    assert hasattr(msgs[0], "tool_calls") or "tool_calls" in (msgs[0].model_extra or {})


# ---------------------------------------------------------------------------
# System-first ordering
# ---------------------------------------------------------------------------


def test_system_messages_placed_first():
    """System messages appear before all other messages."""
    turns = [
        UserTurn(content="hello"),
        SystemTurn(content="system prompt"),
        AssistantTurn(content="hi"),
    ]
    msgs = convert_history_to_messages(turns)
    assert msgs[0].role == "system"
    assert msgs[0].content == "system prompt"
    assert msgs[1].role == "user"
    assert msgs[2].role == "assistant"


def test_multiple_system_messages_all_first():
    """Multiple system messages all placed at the beginning."""
    turns = [
        UserTurn(content="hello"),
        SystemTurn(content="system 1"),
        AssistantTurn(content="hi"),
        SystemTurn(content="system 2"),
    ]
    msgs = convert_history_to_messages(turns)
    assert msgs[0].role == "system"
    assert msgs[1].role == "system"
    assert msgs[2].role == "user"
    assert msgs[3].role == "assistant"


# ---------------------------------------------------------------------------
# ToolResultsTurn conversion
# ---------------------------------------------------------------------------


def test_tool_results_turn_basic():
    """ToolResultsTurn → tool messages with correct call_id."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {}},
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=True, output="file contents")]),
    ]
    msgs = convert_history_to_messages(turns)
    # assistant message + 1 tool message
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"
    assert "file contents" in tool_msgs[0].content


def test_tool_results_multiple_tools():
    """Multiple tool calls → multiple tool result messages with matching IDs."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {}},
                {"id": "tc2", "name": "write_file", "arguments": {}},
            ],
        ),
        ToolResultsTurn(
            results=[
                ToolResult(success=True, output="read output"),
                ToolResult(success=True, output="write output"),
            ]
        ),
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0].tool_call_id == "tc1"
    assert tool_msgs[1].tool_call_id == "tc2"


def test_tool_results_with_errors():
    """Tool results with errors are properly serialized."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "bad_tool", "arguments": {}},
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=False, output="Tool error: oops")]),
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "error" in tool_msgs[0].content.lower() or "oops" in tool_msgs[0].content


# ---------------------------------------------------------------------------
# ThinkingBlock preservation
# ---------------------------------------------------------------------------


def test_assistant_turn_with_reasoning():
    """AssistantTurn with reasoning → content includes ThinkingBlock."""
    turns = [AssistantTurn(content="Answer", reasoning="Let me think...")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    content = msgs[0].content
    assert isinstance(content, list), "Content should be a list of blocks"
    thinking_blocks = [b for b in content if isinstance(b, ThinkingBlock)]
    text_blocks = [b for b in content if isinstance(b, TextBlock)]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0].thinking == "Let me think..."
    assert len(text_blocks) == 1
    assert text_blocks[0].text == "Answer"


def test_thinking_block_preserves_signature():
    """ThinkingBlock signature is preserved for multi-turn."""
    turns = [
        AssistantTurn(
            content="Answer",
            reasoning="Let me think...",
            reasoning_signature="sig123abc",
        )
    ]
    msgs = convert_history_to_messages(turns)
    content = msgs[0].content
    assert isinstance(content, list)
    thinking_blocks = [b for b in content if isinstance(b, ThinkingBlock)]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0].signature == "sig123abc"


def test_thinking_block_before_text_block():
    """ThinkingBlock appears before TextBlock in content (provider convention)."""
    turns = [AssistantTurn(content="Answer", reasoning="Thinking...")]
    msgs = convert_history_to_messages(turns)
    content = msgs[0].content
    assert isinstance(content, list)
    # Find positions
    thinking_idx = next(
        i for i, b in enumerate(content) if isinstance(b, ThinkingBlock)
    )
    text_idx = next(i for i, b in enumerate(content) if isinstance(b, TextBlock))
    assert thinking_idx < text_idx


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_content_assistant():
    """AssistantTurn with empty content → still produces a message."""
    turns = [AssistantTurn(content="")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"


def test_none_content_assistant():
    """AssistantTurn with None-ish content → message with empty string."""
    turns = [AssistantTurn(content="", tool_calls=[])]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"


def test_empty_history():
    """Empty history → empty messages list."""
    msgs = convert_history_to_messages([])
    assert msgs == []


def test_tool_results_without_preceding_assistant():
    """ToolResultsTurn without preceding AssistantTurn → tool_call_id is None."""
    turns = [
        ToolResultsTurn(results=[ToolResult(success=True, output="orphan result")])
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id is None


def test_full_conversation_round_trip():
    """Full conversation with system, user, assistant, tools, steering."""
    turns = [
        SystemTurn(content="You are helpful."),
        UserTurn(content="Read x.py"),
        AssistantTurn(
            content="I'll read it.",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {"path": "x.py"}}
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=True, output="print('hello')")]),
        SteeringTurn(content="Also check y.py"),
        AssistantTurn(content="Sure, let me check y.py too."),
    ]
    msgs = convert_history_to_messages(turns)

    # System first
    assert msgs[0].role == "system"
    # Then user, assistant, tool, steering(=user), assistant
    roles = [m.role for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "user", "assistant"]


# ---------------------------------------------------------------------------
# OpenAI multi-turn tool-use regression: assistant tool-call name must be
# emitted under BOTH "name" (OpenAI provider reads this) and "tool" (Anthropic
# provider reads this).  See A/B differential
# .amplifier/evaluation/openai-ab-differential/20260630/VERDICT.md:
# loop-agent emitted only "tool" -> OpenAI provider's tc.get("name") was empty
# -> it dropped the function_call item -> orphaned function_call_output ->
# Responses-API "No tool call found for function call output" hard error.
# ---------------------------------------------------------------------------


def test_assistant_tool_call_emits_name_and_tool_keys():
    """Single tool call: serialized message carries name AND tool (both == fn name).

    REQUIRED shape test (RED before the fix, GREEN after): the assistant
    tool-call turn must serialize the function name under both keys, and the
    assistant(tool_calls) message must sit immediately before the tool message
    carrying the matching tool_call_id.
    """
    turns = [
        UserTurn(content="create probe.txt"),
        AssistantTurn(
            content="",
            tool_calls=[
                {
                    "id": "call_x",
                    "name": "write_file",
                    "arguments": '{"file_path":"./probe.txt","content":"hello world"}',
                }
            ],
        ),
        ToolResultsTurn(
            results=[ToolResult(success=True, output='{"file_path":"./probe.txt"}')]
        ),
    ]
    msgs = convert_history_to_messages(turns)

    # Find the assistant message and its serialized tool_calls.
    assistant_idx = next(i for i, m in enumerate(msgs) if m.role == "assistant")
    assistant = msgs[assistant_idx]
    dumped = assistant.model_dump()
    tool_calls = dumped["tool_calls"]
    assert len(tool_calls) == 1
    # Both keys present, both equal to the function name (the regression assertion).
    assert tool_calls[0]["name"] == "write_file"
    assert tool_calls[0]["tool"] == "write_file"
    assert tool_calls[0]["id"] == "call_x"

    # The assistant(tool_calls) message is immediately before the tool message
    # carrying the matching tool_call_id.
    following = msgs[assistant_idx + 1]
    assert following.role == "tool"
    assert following.tool_call_id == "call_x"


def test_assistant_multiple_tool_calls_emit_name_and_tool_keys():
    """Multiple tool calls in one assistant turn: every call carries name AND tool."""
    turns = [
        UserTurn(content="read both files"),
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "call_a", "name": "read_file", "arguments": '{"path":"a.py"}'},
                {"id": "call_b", "name": "write_file", "arguments": '{"path":"b.py"}'},
            ],
        ),
        ToolResultsTurn(
            results=[
                ToolResult(success=True, output="a contents"),
                ToolResult(success=True, output="b contents"),
            ]
        ),
    ]
    msgs = convert_history_to_messages(turns)

    assistant = next(m for m in msgs if m.role == "assistant")
    tool_calls = assistant.model_dump()["tool_calls"]
    assert len(tool_calls) == 2
    assert tool_calls[0]["name"] == tool_calls[0]["tool"] == "read_file"
    assert tool_calls[1]["name"] == tool_calls[1]["tool"] == "write_file"
    # Tool results pair to the calls by index/id (unaffected by the name key).
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["call_a", "call_b"]


# NOTE: The provider-contract tests proposed in the fix plan
# (provider-openai._convert_messages emits function_call before its
# function_call_output; provider-anthropic still reads "tool") are intentionally
# OMITTED here: neither amplifier_module_provider_openai nor
# amplifier_module_provider_anthropic is importable in loop-agent's test
# environment (verified), and adding them as test dependencies would be a heavy,
# out-of-module dependency.  The shape tests above pin the loop-agent side of the
# contract (the only side this module owns); the provider-side guarantees are
# documented in the inline comment in messages.py and proven by the A/B
# differential artifacts referenced above.


# ---------------------------------------------------------------------------
# Regression: assistant turn with ONLY tool calls (no prose) must never
# serialize with an empty text content block.  Anthropic's Messages API
# rejects any text content block whose text is empty ("messages: text
# content blocks must be non-empty").  This surfaced when the model's
# response was tool-call-only (e.g. its very first action is a bash
# tool_use): the assistant turn's empty content was previously carried as
# a bare "" string (or an empty TextBlock alongside a ThinkingBlock), and
# BOTH shapes get unconditionally wrapped into a TEXT content part
# downstream (unified_provider_adapter.py::_translate_content), producing
# the exact rejected shape observed in the field:
#   [{"type": "text", "text": ""}, {"type": "tool_use", ...}]
# See messages.py::_build_assistant_message for the fix at the cause.
# ---------------------------------------------------------------------------


def test_assistant_tool_call_only_no_reasoning_has_no_empty_text_block():
    """AssistantTurn with tool calls, empty text, no reasoning -> content
    carries NO text block at all (not even an empty-string block)."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "bash", "arguments": {"command": "ls"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assistant = next(m for m in msgs if m.role == "assistant")
    content = assistant.content
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, TextBlock)]
        assert text_blocks == []
    else:
        # If content is a bare string, it must not be an empty string --
        # a bare "" gets auto-wrapped into an empty TEXT content part
        # downstream, reproducing the bug one layer up.
        assert content != ""


def test_assistant_tool_call_only_whitespace_text_has_no_empty_text_block():
    """Whitespace-only text alongside tool calls is treated as empty too."""
    turns = [
        AssistantTurn(
            content="   \n\t  ",
            tool_calls=[
                {"id": "tc1", "name": "bash", "arguments": {"command": "ls"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assistant = next(m for m in msgs if m.role == "assistant")
    content = assistant.content
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, TextBlock)]
        assert text_blocks == []
    else:
        assert content.strip() == "" and content != "   \n\t  "


def test_assistant_tool_call_only_content_is_not_bare_empty_string():
    """The content field itself must not be the bare "" string when tool
    calls are present: a bare "" gets auto-wrapped downstream into a TEXT
    content part with empty text (unified_provider_adapter.py
    ::_translate_content), reproducing the exact same bug one layer up.
    """
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "bash", "arguments": {"command": "ls"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assistant = next(m for m in msgs if m.role == "assistant")
    assert assistant.content != ""


def test_assistant_reasoning_plus_tool_calls_no_text_omits_empty_text_block():
    """Reasoning present + no text + tool calls -> content has the
    ThinkingBlock only; no empty TextBlock is appended alongside it."""
    turns = [
        AssistantTurn(
            content="",
            reasoning="Let me check the files first.",
            tool_calls=[
                {"id": "tc1", "name": "bash", "arguments": {"command": "ls"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assistant = next(m for m in msgs if m.role == "assistant")
    content = assistant.content
    assert isinstance(content, list)
    thinking_blocks = [b for b in content if isinstance(b, ThinkingBlock)]
    text_blocks = [b for b in content if isinstance(b, TextBlock)]
    assert len(thinking_blocks) == 1
    assert text_blocks == []


def test_assistant_text_plus_tool_calls_keeps_text_block():
    """Positive/pinned case: non-empty text + tool_calls keeps BOTH the
    text content and the tool call -- the fix must not over-omit."""
    turns = [
        AssistantTurn(
            content="Let me check that.",
            tool_calls=[
                {"id": "tc1", "name": "bash", "arguments": {"command": "ls"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assistant = next(m for m in msgs if m.role == "assistant")
    content = assistant.content
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, TextBlock)]
        assert len(text_blocks) == 1
        assert text_blocks[0].text == "Let me check that."
    else:
        assert content == "Let me check that."
    tool_calls = assistant.model_dump()["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "bash"


def test_assistant_reasoning_with_text_still_keeps_text_block():
    """Reasoning + non-empty text (no tool calls) -> both blocks kept.

    Regression guard against over-aggressive omission: the fix must only
    omit the TextBlock when there is truly no non-whitespace text.
    """
    turns = [AssistantTurn(content="Answer", reasoning="Thinking...")]
    msgs = convert_history_to_messages(turns)
    content = msgs[0].content
    assert isinstance(content, list)
    text_blocks = [b for b in content if isinstance(b, TextBlock)]
    assert len(text_blocks) == 1
    assert text_blocks[0].text == "Answer"
