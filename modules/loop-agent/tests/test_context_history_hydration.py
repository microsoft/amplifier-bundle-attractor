"""Tests for seeded cross-node history hydration (support#497).

Independent re-review found the REAL support#497 fix belongs here, not in
the loop-amplifier-agent adapter: loop-agent's AgentSession kept its own
SessionHistory and never read the mounted ``context`` the Orchestrator
protocol hands ``execute()`` -- so a fidelity="full" spawn's seeded
``parent_messages`` (delivered via foundation's
``child_context.set_messages(parent_messages)`` before ``execute()`` runs)
never reached ``_convert_history_to_messages()``, and thus never reached the
provider request. Ticket's live evidence: "Restored 2 messages to context"
followed by "Final message count for API: 1".

These tests exercise the fix end-to-end through the public
``AgentOrchestrator.execute()`` seam (mirroring test_agent_session.py's own
harness style) so a RED run against the pre-fix code path fails for the
right reason: the seeded messages never reaching ``provider.complete``'s
request, not merely "some attribute wasn't set".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.message_models import ChatResponse, Usage
from amplifier_module_loop_agent import AgentOrchestrator

# ---------------------------------------------------------------------------
# Test helpers (mirrors tests/test_agent_session.py's _make_harness style)
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    """ChatResponse with text only (natural completion)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class FakeContext:
    """Minimal double for the mounted ContextManager (support#497 seam).

    Mirrors the ONLY two behaviors the fix depends on: an async
    ``get_messages()`` returning ``list[dict]`` (or raising, for the
    honest-failure negative).
    """

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._messages = list(messages) if messages is not None else []
        self._raises = raises
        self.call_count = 0

    async def get_messages(self) -> list[dict[str, Any]]:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return list(self._messages)


def _make_harness(
    config: dict | None = None,
    responses: list[ChatResponse] | None = None,
):
    """Build an orchestrator + mocks, mirroring test_agent_session.py.

    Returns (orchestrator, providers, tools, hooks).
    """
    defaults = {"system_prompt": "You are a test coding agent."}
    cfg = {**defaults, **(config or {})}

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses or [_text_response("done")])
    providers = {"test": provider}

    tools: dict[str, Any] = {}

    hooks = MagicMock()

    async def _recording_emit(event: str, data: dict):
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)

    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orchestrator, providers, tools, hooks


def _sent_messages(provider: AsyncMock, call_index: int = 0) -> list[Any]:
    """The `messages` list of the ChatRequest built for provider.complete's
    call_index'th invocation."""
    request = provider.complete.call_args_list[call_index].args[0]
    return request.messages


# ---------------------------------------------------------------------------
# 1. RED-proof: seeded history must reach the built provider request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeded_context_history_reaches_provider_request():
    """RED-proof (support#497): against the pre-fix loop-agent, `context` was
    accepted by execute() and silently dropped -- the built ChatRequest carried
    only the system message + the current prompt (ticket's "Final message
    count for API: 1"), no matter what the mounted context held. Post-fix,
    the seeded prior-turn messages are replayed into session history BEFORE
    the loop runs, so they appear in the request sent to provider.complete().
    """
    orch, provs, tools, hooks = _make_harness(
        responses=[_text_response("The secret code is ZEBRA-42.")]
    )
    seeded = [
        {"role": "user", "content": "The secret code is ZEBRA-42."},
        {"role": "assistant", "content": "Understood, I will remember it."},
    ]
    context = FakeContext(seeded)

    result = await orch.execute(
        "What was the secret code?", context, provs, tools, hooks
    )
    assert result == "The secret code is ZEBRA-42."

    sent = _sent_messages(provs["test"])
    contents = [getattr(m, "content", None) for m in sent]
    assert "The secret code is ZEBRA-42." in contents
    assert "Understood, I will remember it." in contents
    # The seeded turns precede the current prompt (order preserved).
    idx_seed_user = contents.index("The secret code is ZEBRA-42.")
    idx_seed_assistant = contents.index("Understood, I will remember it.")
    idx_prompt = contents.index("What was the secret code?")
    assert idx_seed_user < idx_seed_assistant < idx_prompt


@pytest.mark.asyncio
async def test_message_count_signature_inverted():
    """The ticket's own failure signature, inverted: 2 seeded messages + 1
    prompt must yield MORE than one non-system message in the built request
    (pre-fix: exactly the fresh system message + the bare prompt, i.e. the
    conversation carried only 1 non-system message)."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    seeded = [
        {"role": "user", "content": "turn one"},
        {"role": "assistant", "content": "turn one reply"},
    ]
    context = FakeContext(seeded)

    await orch.execute("turn two prompt", context, provs, tools, hooks)

    sent = _sent_messages(provs["test"])
    non_system = [m for m in sent if m.role != "system"]
    assert len(non_system) > 1, (
        f"expected >1 non-system message (2 seeded + 1 prompt), got "
        f"{len(non_system)}: {non_system!r}"
    )
    assert len(non_system) == 3


# ---------------------------------------------------------------------------
# 2. Negatives: context=None, empty context, get_messages() raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_none_is_a_noop():
    """context=None must behave exactly like today: no seeded history, no crash."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])

    result = await orch.execute("hello", None, provs, tools, hooks)
    assert result == "ok"

    sent = _sent_messages(provs["test"])
    non_system = [m for m in sent if m.role != "system"]
    assert len(non_system) == 1
    assert non_system[0].content == "hello"


@pytest.mark.asyncio
async def test_empty_context_history_is_a_noop():
    """A present context whose get_messages() returns [] seeds nothing."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    context = FakeContext([])

    result = await orch.execute("hello", context, provs, tools, hooks)
    assert result == "ok"
    assert context.call_count == 1

    sent = _sent_messages(provs["test"])
    non_system = [m for m in sent if m.role != "system"]
    assert len(non_system) == 1
    assert non_system[0].content == "hello"


@pytest.mark.asyncio
async def test_context_without_get_messages_is_a_noop():
    """A context object that doesn't even expose get_messages() must not crash."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    context = object()  # no get_messages attribute at all

    result = await orch.execute("hello", context, provs, tools, hooks)
    assert result == "ok"


@pytest.mark.asyncio
async def test_context_get_messages_raises_warns_and_proceeds(
    caplog: pytest.LogCaptureFixture,
):
    """A context whose get_messages() raises must NOT crash the node -- log a
    loud warning and proceed as if there were nothing to replay (degraded
    continuity, never a dead node)."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    context = FakeContext(raises=RuntimeError("context store unavailable"))

    import logging

    with caplog.at_level(logging.WARNING):
        result = await orch.execute("hello", context, provs, tools, hooks)

    assert result == "ok"
    assert any(
        "context.get_messages() raised" in rec.getMessage() for rec in caplog.records
    )

    sent = _sent_messages(provs["test"])
    non_system = [m for m in sent if m.role != "system"]
    assert len(non_system) == 1
    assert non_system[0].content == "hello"


# ---------------------------------------------------------------------------
# 3. System-prompt survival.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_own_system_framing_survives_hydration_exactly_once():
    """loop-agent's own Layer-1..5 system prompt must appear EXACTLY ONCE in
    the built request after hydration -- neither displaced nor duplicated --
    even when the seeded context itself carries a role="system" message."""
    orch, provs, tools, hooks = _make_harness(
        config={"system_prompt": "OWN SYSTEM FRAMING MARKER"},
        responses=[_text_response("ok")],
    )
    seeded = [
        {"role": "system", "content": "a DIFFERENT node's stale system prompt"},
        {"role": "user", "content": "prior turn"},
        {"role": "assistant", "content": "prior reply"},
    ]
    context = FakeContext(seeded)

    await orch.execute("current prompt", context, provs, tools, hooks)

    sent = _sent_messages(provs["test"])
    system_messages = [m for m in sent if m.role == "system"]
    assert len(system_messages) == 1, (
        f"expected exactly one system message, got {len(system_messages)}: "
        f"{system_messages!r}"
    )
    assert "OWN SYSTEM FRAMING MARKER" in system_messages[0].content
    assert "a DIFFERENT node's stale system prompt" not in system_messages[0].content
    # The system message always leads (messages.py: system-first ordering).
    assert sent[0] is system_messages[0]
    # The seeded user/assistant turns still made it through.
    contents = [getattr(m, "content", None) for m in sent]
    assert "prior turn" in contents
    assert "prior reply" in contents


# ---------------------------------------------------------------------------
# 4. Hydrate once, before turn 1 -- not re-seeded on a later process_input().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydration_runs_once_not_on_every_invocation():
    """A session persists across multiple execute() calls (follow-ups,
    steering). Seeded history belongs only at turn 1 -- a second execute()
    call on the SAME session/context must not replay the seeded messages a
    second time."""
    orch, provs, tools, hooks = _make_harness(
        responses=[_text_response("first"), _text_response("second")]
    )
    seeded = [{"role": "user", "content": "seed-once"}]
    context = FakeContext(seeded)

    await orch.execute("prompt one", context, provs, tools, hooks)
    await orch.execute("prompt two", context, provs, tools, hooks)

    # get_messages() is only even consulted once (hydration guard trips
    # before the second call ever reads the context again).
    assert context.call_count == 1

    sent_second = _sent_messages(provs["test"], call_index=1)
    seed_occurrences = [
        m for m in sent_second if getattr(m, "content", None) == "seed-once"
    ]
    assert len(seed_occurrences) == 1, (
        "seeded message replayed more than once across invocations -- "
        f"found {len(seed_occurrences)} occurrences"
    )


# ---------------------------------------------------------------------------
# 5. Unsupported roles in seeded history are skipped, not crashed on.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_role_in_seeded_history_is_skipped_not_fatal(
    caplog: pytest.LogCaptureFixture,
):
    """A seeded 'tool'-role (or otherwise-shaped) entry cannot be safely
    replayed without a correlated tool_call_id -- it must be skipped with a
    warning, never fabricated, and never crash the node."""
    orch, provs, tools, hooks = _make_harness(responses=[_text_response("ok")])
    seeded = [
        {"role": "user", "content": "kept turn"},
        {"role": "tool", "content": "orphaned tool result"},
        "not-even-a-dict",
    ]
    context = FakeContext(seeded)

    import logging

    with caplog.at_level(logging.WARNING):
        result = await orch.execute("hello", context, provs, tools, hooks)

    assert result == "ok"
    assert any("could not be replayed" in rec.getMessage() for rec in caplog.records)

    sent = _sent_messages(provs["test"])
    contents = [getattr(m, "content", None) for m in sent]
    assert "kept turn" in contents
    assert "orphaned tool result" not in contents
