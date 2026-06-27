"""Tests for subagent depth tracking (Task 4.3).

Spec coverage: SUB-013 (depth limiting via max_subagent_depth).

The actual spawning mechanism is tool-delegate. The loop-agent
orchestrator is responsible for:
1. Reading max_subagent_depth from config
2. Tracking current depth on the session
3. Registering depth as a capability on the coordinator
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from amplifier_core.message_models import ChatResponse, Usage

from amplifier_module_loop_agent import AgentOrchestrator


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_harness(
    config: dict | None = None,
    responses: list[ChatResponse] | None = None,
):
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    provider = AsyncMock()
    provider.complete = AsyncMock(
        side_effect=responses or [_text_response("done")]
    )
    providers = {"test": provider}
    tools: dict = {}
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    context = MagicMock()

    # Coordinator mock with register_capability tracking
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    orchestrator = AgentOrchestrator(coordinator=coordinator, config=cfg)
    return orchestrator, context, providers, tools, hooks, coordinator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_max_subagent_depth():
    """Default max_subagent_depth is 1."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    # Session config should have default max_subagent_depth=1
    assert orch._session._config.max_subagent_depth == 1


@pytest.mark.asyncio
async def test_custom_max_subagent_depth():
    """max_subagent_depth can be configured."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        config={"max_subagent_depth": 3},
        responses=[_text_response("ok")],
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._config.max_subagent_depth == 3


@pytest.mark.asyncio
async def test_depth_registered_on_coordinator():
    """self_delegation_depth is registered as a capability on the coordinator."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)

    # Verify register_capability was called with self_delegation_depth
    calls = coord.register_capability.call_args_list
    depth_calls = [c for c in calls if c[0][0] == "self_delegation_depth"]
    assert len(depth_calls) >= 1
    # Default starting depth is 0
    assert depth_calls[0] == call("self_delegation_depth", 0)


@pytest.mark.asyncio
async def test_depth_zero_disables_subagents():
    """max_subagent_depth=0 means no subagents allowed."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        config={"max_subagent_depth": 0},
        responses=[_text_response("ok")],
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._config.max_subagent_depth == 0


@pytest.mark.asyncio
async def test_session_tracks_current_depth():
    """AgentSession has a current_depth attribute."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        responses=[_text_response("ok")]
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    assert hasattr(orch._session, "_current_depth")
    assert orch._session._current_depth == 0


@pytest.mark.asyncio
async def test_session_depth_from_config():
    """Session depth can be initialized from config (for child sessions)."""
    orch, ctx, provs, tools, hooks, coord = _make_harness(
        config={"current_depth": 2},
        responses=[_text_response("ok")],
    )
    await orch.execute("hello", ctx, provs, tools, hooks)
    assert orch._session._current_depth == 2
    # Depth registered on coordinator should be 2
    calls = coord.register_capability.call_args_list
    depth_calls = [c for c in calls if c[0][0] == "self_delegation_depth"]
    assert depth_calls[0] == call("self_delegation_depth", 2)
