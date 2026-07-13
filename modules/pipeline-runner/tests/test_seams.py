"""Unit tests for the run_pipeline consumer seams on ``make_spawn_fn`` and the
inline-only ``_resolve_agent_bundle`` contract.

These tests assert only the seam wiring -- that a caller-supplied
``child_constraint`` is applied to the resolved child bundle before spawn, that
``spawn_timeout`` wraps the spawn, that ``session_cwd`` is still threaded, and
that the removed legacy ``{"bundle": ...}`` agent shape now fails loud. They use
fakes and monkeypatching (no engine, no LLM, no DOT/graph/call-order asserts) so
they stay fast and non-brittle, per the module's testing philosophy.
"""

from __future__ import annotations

import asyncio

import pytest

from amplifier_module_pipeline_runner import runner as runner_mod
from amplifier_module_pipeline_runner.runner import _resolve_agent_bundle, make_spawn_fn


class FakePrepared:
    """Minimal stand-in for a PreparedBundle -- records the last spawn call."""

    def __init__(self, agents: dict | None = None, spawn_delay: float = 0.0) -> None:
        self.bundle = type("B", (), {"agents": agents or {}})()
        self.spawn_delay = spawn_delay
        self.last_spawn_kwargs: dict | None = None

    async def spawn(self, **kwargs):
        if self.spawn_delay:
            await asyncio.sleep(self.spawn_delay)
        self.last_spawn_kwargs = kwargs
        return {"status": "ok", "child_bundle": kwargs["child_bundle"]}


def test_legacy_bundle_shape_fails_loud():
    """The removed ``{"bundle": ...}`` reference shape must raise, not resolve."""
    with pytest.raises(ValueError, match="removed"):
        asyncio.run(
            _resolve_agent_bundle(
                "attractor-agent-anthropic",
                {"bundle": "attractor:agents/attractor-agent-anthropic"},
            )
        )


def test_child_constraint_applied_before_spawn(monkeypatch):
    """A caller ``child_constraint`` transforms the resolved bundle pre-spawn."""

    async def fake_resolve(agent_name, config):
        return f"unconstrained::{agent_name}"

    monkeypatch.setattr(runner_mod, "_resolve_agent_bundle", fake_resolve)

    prepared = FakePrepared()
    spawn = make_spawn_fn(
        prepared,
        cwd=None,
        child_constraint=lambda b: f"constrained::{b}",
    )

    result = asyncio.run(
        spawn(
            agent_name="agent-x",
            instruction="do the thing",
            parent_session=object(),
            agent_configs={"agent-x": {}},
        )
    )

    assert result["child_bundle"] == "constrained::unconstrained::agent-x"
    assert prepared.last_spawn_kwargs is not None
    assert (
        prepared.last_spawn_kwargs["child_bundle"]
        == "constrained::unconstrained::agent-x"
    )


def test_no_constraint_passes_bundle_through(monkeypatch):
    """Without a constraint, the resolved bundle reaches spawn unchanged."""

    async def fake_resolve(agent_name, config):
        return f"bundle::{agent_name}"

    monkeypatch.setattr(runner_mod, "_resolve_agent_bundle", fake_resolve)

    prepared = FakePrepared()
    spawn = make_spawn_fn(prepared, cwd=None)

    result = asyncio.run(
        spawn(
            agent_name="agent-y",
            instruction="i",
            parent_session=object(),
            agent_configs={"agent-y": {}},
        )
    )
    assert result["child_bundle"] == "bundle::agent-y"


def test_spawn_timeout_raises_on_slow_spawn(monkeypatch):
    """``spawn_timeout`` wraps the spawn and fails loud when it overruns."""

    async def fake_resolve(agent_name, config):
        return agent_name

    monkeypatch.setattr(runner_mod, "_resolve_agent_bundle", fake_resolve)

    prepared = FakePrepared(spawn_delay=0.2)
    spawn = make_spawn_fn(prepared, cwd=None, spawn_timeout=0.01)

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        asyncio.run(
            spawn(
                agent_name="slow",
                instruction="i",
                parent_session=object(),
                agent_configs={"slow": {}},
            )
        )


def test_session_cwd_still_threaded(monkeypatch, tmp_path):
    """The load-bearing ``session_cwd=cwd`` argument survives the new seams."""

    async def fake_resolve(agent_name, config):
        return agent_name

    monkeypatch.setattr(runner_mod, "_resolve_agent_bundle", fake_resolve)

    prepared = FakePrepared()
    spawn = make_spawn_fn(prepared, cwd=tmp_path)

    asyncio.run(
        spawn(
            agent_name="a",
            instruction="i",
            parent_session=object(),
            agent_configs={"a": {}},
        )
    )
    assert prepared.last_spawn_kwargs is not None
    assert prepared.last_spawn_kwargs["session_cwd"] == tmp_path
