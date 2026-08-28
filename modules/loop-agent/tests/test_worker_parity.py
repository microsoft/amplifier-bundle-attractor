"""worker-parity-kit wiring for loop-agent.

See modules/worker-parity-kit/README.md ("Why this exists") in
amplifier-bundle-dot-runner for the three incidents this kit's suite guards
against -- support#497 (this module's own incident) is the second one.

This file adds ONE thing: a ``WorkerHarness`` (``worker_parity_kit.protocol``)
that drives THIS module's REAL ``AgentOrchestrator.execute()`` hermetically,
reusing the same style of hand-rolled fakes ``test_context_history_hydration.py``
and ``test_parity_matrix.py`` already depend on (an ``AsyncMock`` provider, a
``MagicMock`` hooks double, a minimal context double with ``get_messages()``)
-- no network, no credentials.

``from worker_parity_kit.suite import *`` below is what actually collects the
3 MUST tests + the TARGET-tier parametrized test against the
``worker_harness`` fixture; nothing in this file re-implements any of them.

Message-shape note: loop-agent's ``ChatRequest.messages`` are
``amplifier_core.message_models.Message`` objects, not dicts. This module's
``_normalize_messages`` converts them into the plain
``list[dict[str, Any]]`` shape ``TurnResult.messages_sent_to_provider``
declares (``worker_parity_kit.protocol``) -- the kit's own
``_message_contents`` helper is tolerant of either shape, but normalizing
here keeps this harness's output honest about what the protocol promises.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.message_models import ChatResponse, Usage
from worker_parity_kit.doubles import CapturingHooks, FakeContextManager
from worker_parity_kit.protocol import TurnResult
from worker_parity_kit.suite import *

from amplifier_module_loop_agent import AgentOrchestrator

#: Honest accounting of what this suite actually proves (review finding:
#: the previous wording here claimed loop-agent "honors every TARGET
#: capability ... confirmed empirically" -- true only for a subset).
#:
#: Genuinely exercised, source-grounded: context replay (M2 -- seeded
#: messages demonstrably reach the provider boundary), `max_turns` /
#: `reasoning_effort` / `user_instructions` (each read from `self._config`
#: and threaded into the turn loop / provider request in agent_session.py
#: and config.py), `llm_provider` (the explicit-provider resolution path in
#: __init__.py), and `tools_passthrough` (AgentOrchestrator._execute_session
#: uses the passed-in `tools` dict DIRECTLY as the session's own tool
#: registry: `all_tools = dict(tools)` -- unlike loop-amplifier-agent, which
#: boots its OWN bundle and deliberately never forwards the parent's
#: mounted tools; see that module's declared_absences).
#:
#: `approvals_posture`, `provider_preferences_precedence`, and
#: `telemetry_session_id` also pass green, but VACUOUSLY: they clear the
#: TARGET tier's shallow no-crash / not-silently-dropped smoke bar, not
#: genuine handling -- grepping this module's source for approval /
#: provider_preferences / telemetry / session-id stamping turns up zero
#: hits, and the kit's own probe configs for these three are either empty
#: (`telemetry_session_id`) or a duplicate of the `llm_provider` probe
#: (`provider_preferences_precedence`), so there is nothing distinguishing
#: for the smoke check to exercise. That absence is deliberate, not a gap:
#: loop-agent has no approval subsystem (an approval-free posture per the
#: coding-agent-loop spec Sec8) and no telemetry/preferences-precedence
#: layer of its own (those concerns, where they exist, ride other layers,
#: not this orchestrator). `declared_absences` stays empty below -- the
#: kit's TARGET bar doesn't distinguish "handled" from "nothing here to
#: mishandle", so there is no real absence to declare -- but this comment
#: must not claim more than the suite proves; see this PR's report for the
#: full genuine-vs-vacuous breakdown.
DECLARED_ABSENCES: frozenset[str] = frozenset()


def _text_response(text: str = "ok") -> ChatResponse:
    """ChatResponse with text only (natural completion, no tool calls)."""
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class _ListWarningHandler(logging.Handler):
    """Captures this module's own WARNING-level log records for a turn."""

    def __init__(self, records: list[str]) -> None:
        super().__init__(level=logging.WARNING)
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record.getMessage())


def _normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Normalize a ChatRequest's ``Message`` objects (or plain dicts) into
    the ``list[dict[str, Any]]`` shape ``TurnResult.messages_sent_to_provider``
    declares -- each dict carries at least ``role``/``content``."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
            continue
        out.append(
            {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}
        )
    return out


class LoopAgentWorkerHarness:
    """WorkerHarness wiring the REAL ``AgentOrchestrator.execute()`` to
    worker-parity-kit's shared suite, hermetically (no network/keys).

    Mounts a single provider keyed as ``"anthropic"`` (rather than an
    arbitrary ``"test"`` key) so the TARGET tier's ``llm_provider`` /
    ``provider_preferences_precedence`` probes -- which set
    ``orchestrator_config={"llm_provider": "anthropic"}`` -- resolve to a
    mounted provider instead of hitting loop-agent's real "provider requested
    but not mounted" fail-loud path (AgentOrchestrator._execute_session).
    """

    declared_absences: frozenset[str] = DECLARED_ABSENCES

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=_text_response("ok"))
        providers = {"anthropic": provider}
        tools: dict[str, Any] = {}
        hooks = CapturingHooks()
        context = FakeContextManager(seeded_context_messages)

        warnings: list[str] = []
        handler = _ListWarningHandler(warnings)
        logger = logging.getLogger("amplifier_module_loop_agent")
        logger.addHandler(handler)

        cfg: dict[str, Any] = {"system_prompt": "You are a test coding agent."}
        cfg.update(orchestrator_config or {})

        orchestrator = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
        try:
            reply = await orchestrator.execute(prompt, context, providers, tools, hooks)
        finally:
            logger.removeHandler(handler)

        sent: list[dict[str, Any]] | None = None
        if provider.complete.call_args_list:
            # `[-1]` (last call) assumes exactly one provider call per turn --
            # true today only because `_text_response` always returns
            # `tool_calls=None`, and agent_session only loops back to the
            # provider on a truthy `tool_calls`. If this harness's mock ever
            # grows a multi-call (tool-loop) turn, sampling `[-1]` would
            # silently pick the wrong call instead of the one the assertions
            # actually mean to inspect.
            request = provider.complete.call_args_list[-1].args[0]
            sent = _normalize_messages(request.messages)

        return TurnResult(
            reply=reply,
            messages_sent_to_provider=sent,
            completion_envelope=hooks.completion,
            warnings=warnings,
        )


@pytest.fixture
def worker_harness() -> LoopAgentWorkerHarness:
    return LoopAgentWorkerHarness()
