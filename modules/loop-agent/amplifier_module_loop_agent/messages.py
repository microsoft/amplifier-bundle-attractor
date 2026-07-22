"""History-to-messages conversion for LLM requests.

Spec coverage: LOOP-010, STEER-003, STEER-010.

Converts typed Turn history to Message objects suitable for ChatRequest.
Key behaviors:
- System messages are placed first regardless of history order.
- AssistantTurn reasoning is preserved as ThinkingBlock (with signature
  for multi-turn Anthropic conversations).
- SteeringTurns become user-role messages.
- ToolResultsTurn entries are mapped to individual tool-role messages
  with matching tool_call_id from the preceding AssistantTurn.
"""

from __future__ import annotations

from typing import Any, Iterable

from amplifier_core.message_models import (
    ContentBlockUnion,
    Message,
    TextBlock,
    ThinkingBlock,
)

from .turns import (
    AssistantTurn,
    SteeringTurn,
    SystemTurn,
    ToolResultsTurn,
    Turn,
    UserTurn,
)


def convert_history_to_messages(
    turns: Iterable[Turn],
) -> list[Message]:
    """Convert typed turn history to Message objects for ChatRequest.

    System messages are collected and placed first. All other messages
    preserve their relative order.
    """
    system_messages: list[Message] = []
    other_messages: list[Message] = []
    pending_tool_calls: list[dict[str, Any]] = []

    for turn in turns:
        if isinstance(turn, SystemTurn):
            system_messages.append(Message(role="system", content=turn.content))

        elif isinstance(turn, UserTurn):
            other_messages.append(Message(role="user", content=turn.content))

        elif isinstance(turn, SteeringTurn):
            # Steering turns become user messages (spec STEER-003)
            other_messages.append(Message(role="user", content=turn.content))

        elif isinstance(turn, AssistantTurn):
            msg = _build_assistant_message(turn)
            if turn.tool_calls:
                pending_tool_calls = turn.tool_calls
            other_messages.append(msg)

        elif isinstance(turn, ToolResultsTurn):
            for i, result in enumerate(turn.results):
                call_id = (
                    pending_tool_calls[i]["id"] if i < len(pending_tool_calls) else None
                )
                other_messages.append(
                    Message(
                        role="tool",
                        content=result.get_serialized_output(),
                        tool_call_id=call_id,
                    )
                )
            pending_tool_calls = []

    return system_messages + other_messages


def _build_assistant_message(turn: AssistantTurn) -> Message:
    """Build a Message from an AssistantTurn with proper content blocks.

    If the turn has reasoning, content is a list of blocks:
        [ThinkingBlock(...), TextBlock(...)] -- the TextBlock is OMITTED
        when there is no non-whitespace text (see below).
    Otherwise content is the text string directly, or an empty list when
    there is no text but the turn issued tool calls.

    Anthropic's Messages API rejects ANY text content block whose text is
    empty ("messages: text content blocks must be non-empty"). This bites
    exactly when the model's response is tool-call-only -- no prose at all
    (e.g. its very first action is a bash tool_use). Previously this
    produced content="" (bare string) or
    content=[ThinkingBlock(...), TextBlock(text="")], and BOTH shapes get
    unconditionally wrapped/serialized into an empty text content part
    downstream, which providers reject on the request that follows (once
    the tool result turn is appended). Omitting the empty/whitespace-only
    text block here is the fix at the cause: history assembly never
    produces the invalid shape in the first place.

    Tool calls are passed as extra kwargs (Message uses extra="allow").
    """
    kwargs: dict[str, Any] = {"role": "assistant"}
    has_text = bool(turn.content and turn.content.strip())

    # Build content: use blocks when reasoning is present
    if turn.reasoning:
        blocks: list[ContentBlockUnion] = []
        # ThinkingBlock first (provider convention)
        thinking_kwargs: dict[str, Any] = {"thinking": turn.reasoning}
        if turn.reasoning_signature:
            thinking_kwargs["signature"] = turn.reasoning_signature
        blocks.append(ThinkingBlock(**thinking_kwargs))
        # Then text -- only when there is actual (non-whitespace) text.
        # An empty TextBlock alongside a tool-call-only response is
        # exactly the invalid shape described above.
        if has_text:
            blocks.append(TextBlock(text=turn.content))
        kwargs["content"] = blocks
    elif has_text:
        kwargs["content"] = turn.content
    elif turn.tool_calls:
        # No text, no reasoning, but this turn issued tool calls: use an
        # empty content list rather than a bare "" string. A bare ""
        # string is auto-wrapped downstream into a TEXT content part with
        # empty text -- the same bug, one layer up (see
        # unified_provider_adapter.py::_translate_content).
        kwargs["content"] = []
    else:
        # Genuinely empty turn (no text, no reasoning, no tool calls):
        # preserve existing behavior of an empty string.
        kwargs["content"] = ""

    # Tool calls (passed as extra field via extra="allow")
    if turn.tool_calls:
        # Emit the function name under BOTH "name" and "tool". Provider request
        # builders disagree on the key: the OpenAI provider reads tc.get("name")
        # (an empty name silently drops the function_call item from the Responses
        # API input, orphaning its function_call_output), while the Anthropic
        # provider reads tc.get("tool"). Carrying both keys is additive and keeps
        # us compatible with both until the ecosystem canonicalizes on one key.
        kwargs["tool_calls"] = [
            {
                "id": tc["id"],
                "name": tc["name"],
                "tool": tc["name"],
                "arguments": tc["arguments"],
            }
            for tc in turn.tool_calls
        ]

    return Message(**kwargs)
