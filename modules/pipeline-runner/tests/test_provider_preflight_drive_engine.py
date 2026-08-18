"""drive_engine startup provider preflight (issue #155).

The incident invoker was exactly this path: `attractor run` -> run_pipeline ->
drive_engine with DEFAULT_PROFILES mapping all three providers.  The 'openai'
PROFILE existed, but no OPENAI_API_KEY did -- so the critique_b node crashed
on every visit (`resolve_latest_for: no adapter found for provider 'openai'`)
and the graph's transient-recovery routing drained the entire iteration
budget against a defect that had nothing to do with the work.

These tests pin the fix: drive_engine now refuses AT STARTUP -- before any
node executes, before any LLM call -- naming the node, the provider, and the
missing credential.

Issue #283 adds the second half of that refusal on this path.  #195/#280
closed the "key set but the profile names an ABSENT agent" hole at
``PipelineOrchestrator.execute()``; drive_engine still passed no
``resolvable_profiles``, so the original #155 incident invoker stayed
fail-open for exactly that class: profile mapped + credential set + named
agent absent -> accepted at startup -> every visit fails at spawn -> the
budget drains to the engine's 200-step safety bound.  The tests below pin
BOTH directions: the refusal, and -- the crux, the #196 disease inverse --
that a run which WOULD work is still not refused.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from amplifier_module_pipeline_runner.runner import drive_engine

_DOT_DUAL_CRITIQUE = """\
digraph dual {
    graph [goal="dual-family critique fixture"]
    start [shape=Mdiamond]
    critique_b [shape=box, llm_provider="openai", prompt="independent review"]
    done [shape=Msquare]
    start -> critique_b -> done
}
"""


class _StubCoordinator:
    """Coordinator stub with a recording spawn capability."""

    def __init__(self) -> None:
        self.spawn_called = False
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-agent-openai": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
                "attractor-agent-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.spawn_called = True
        return {
            "output": json.dumps({"status": "success", "notes": "stub"}),
            "session_id": "child-1",
        }


def test_drive_engine_refuses_at_startup_missing_credential(
    tmp_path, monkeypatch
) -> None:
    """Incident configuration: openai profile mounted (DEFAULT_PROFILES),
    OPENAI_API_KEY absent -> refuse before ANY node executes."""
    from amplifier_module_loop_pipeline.preflight import ProviderPreflightError

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    coordinator = _StubCoordinator()

    with pytest.raises(ProviderPreflightError) as exc_info:
        asyncio.run(
            drive_engine(
                _DOT_DUAL_CRITIQUE,
                coordinator,
                cwd=tmp_path,
                logs_root=tmp_path / "logs",
                transform=True,
            )
        )

    msg = str(exc_info.value)
    assert "critique_b" in msg  # the failing node
    assert 'llm_provider="openai"' in msg  # its provider
    assert "OPENAI_API_KEY" in msg  # the missing credential
    assert not coordinator.spawn_called, (
        "refusal must happen before any node executes -- zero budget spent"
    )


def test_drive_engine_runs_when_declared_provider_is_serviceable(
    tmp_path, monkeypatch
) -> None:
    """Control: with the credential present, the same graph starts and runs
    to completion unaffected (presence is checked, never validity -- a
    hermetic harness sets the env var and mocks spawn)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-preflight")
    coordinator = _StubCoordinator()

    outcome = asyncio.run(
        drive_engine(
            _DOT_DUAL_CRITIQUE,
            coordinator,
            cwd=tmp_path,
            logs_root=tmp_path / "logs",
            transform=True,
        )
    )

    assert outcome.status.value == "success", outcome
    assert coordinator.spawn_called


# ---------------------------------------------------------------------------
# Adapter-resolvable profiles on the drive_engine path (issue #283)
#
# The residual of #195/#280.  A profile is a STRING naming an agent; knowing
# the string is MAPPED is not knowing the agent it names can be RESOLVED.
# ``AmplifierBackend._run_with_spawn`` resolves it in exactly one place --
# ``coordinator.config["agents"]`` -- so a profile naming an absent agent
# fails at EVERY spawn.  drive_engine must hand the preflight that same key
# set (the engine's shared ``_spawn_resolvable_agents``) and refuse at
# startup instead of draining the budget.
# ---------------------------------------------------------------------------

_DOT_DRAIN_LOOP = """\
digraph drain_loop {
    graph [goal="the #155 shape: a failing node on a recovery loop"]
    start [shape=Mdiamond]
    critique_b [shape=box, llm_provider="openai", prompt="dual-family critique"]
    recover [shape=box, llm_provider="anthropic", prompt="transient recovery"]
    done [shape=Msquare]
    start -> critique_b
    critique_b -> done    [condition="outcome=success"]
    critique_b -> recover [condition="outcome=fail"]
    recover -> critique_b [loop_restart="true"]
}
"""

_PROFILES_NAMING_AN_ABSENT_AGENT = {
    "anthropic": "attractor-anthropic",
    "openai": "attractor-openai",  # names an agent the coordinator does not have
}

_LOOP_AGENT: dict[str, Any] = {"session": {"orchestrator": {"module": "loop-agent"}}}


class _AgentsCoordinator:
    """Coordinator whose ``config['agents']`` is set by the test.

    This is the mapping ``AmplifierBackend._run_with_spawn`` indexes the
    profile into, so it is also exactly what the preflight must judge.
    """

    def __init__(self, *agent_names: str) -> None:
        self.spawn_calls: list[str] = []
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {
            "agents": {name: dict(_LOOP_AGENT) for name in agent_names}
        }

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs):
        self.spawn_calls.append(str(kwargs.get("agent_name")))
        return {
            "output": json.dumps({"status": "success", "notes": "stub"}),
            "session_id": "child-1",
        }


class _NoSpawnCoordinator:
    """A bare coordinator: no ``session.spawn`` capability at all."""

    hooks = None
    session = None
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str):
        return None


def test_drive_engine_refuses_when_profile_names_an_absent_agent(
    tmp_path, monkeypatch
) -> None:
    """#283: credential SET, profile MAPPED, named agent ABSENT -> refuse.

    Against the pre-fix runner this configuration was ACCEPTED and the run
    drained to the engine's 200-step safety bound (critique_b executed 101
    times, 99 real spawns issued for the recovery node).  It must now refuse
    at startup with ZERO spawns, naming the profile, the missing agent, and
    what IS resolvable -- the same message shape as the execute() path.
    """
    from amplifier_module_loop_pipeline.preflight import ProviderPreflightError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")  # credential IS set
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")

    coordinator = _AgentsCoordinator("attractor-anthropic")  # NOT attractor-openai

    with pytest.raises(ProviderPreflightError) as exc_info:
        asyncio.run(
            drive_engine(
                _DOT_DRAIN_LOOP,
                coordinator,
                cwd=tmp_path,
                logs_root=tmp_path / "logs",
                profiles=_PROFILES_NAMING_AN_ABSENT_AGENT,
                transform=True,
            )
        )

    msg = str(exc_info.value)
    assert "critique_b" in msg  # the failing node
    assert "attractor-openai" in msg  # the profile that cannot resolve
    assert "attractor-anthropic" in msg  # what CAN be resolved
    assert not coordinator.spawn_calls, "zero spawns -- zero budget spent"


def test_drive_engine_runs_when_the_named_agent_is_present(tmp_path, monkeypatch) -> None:
    """The no-false-refusal crux (#196's disease, inverted).

    SAME graph, SAME credentials, SAME profiles map as the test above -- the
    single difference is that the agent the profile names EXISTS.  The run
    must still start and succeed.  The fix discriminates on adapter
    resolution, never on the graph shape or the profile string.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")

    coordinator = _AgentsCoordinator("attractor-anthropic", "attractor-openai")

    outcome = asyncio.run(
        drive_engine(
            _DOT_DRAIN_LOOP,
            coordinator,
            cwd=tmp_path,
            logs_root=tmp_path / "logs",
            profiles=_PROFILES_NAMING_AN_ABSENT_AGENT,  # identical map
            transform=True,
        )
    )

    assert outcome.status.value == "success", outcome
    assert "attractor-openai" in coordinator.spawn_calls


def test_drive_engine_preflight_sees_exactly_the_keys_the_spawn_resolves_against() -> None:
    """Same dict, same moment.

    ``_spawn_resolvable_agents`` returns the keys of ``coordinator.config
    ["agents"]``; ``AmplifierBackend._run_with_spawn`` looks the profile up in
    that same mapping on the same coordinator object drive_engine hands the
    backend.  Pinning the identity here is what keeps the preflight from ever
    judging a DIFFERENT set than the spawn will -- the #196 failure mode.
    """
    from amplifier_module_loop_pipeline import _spawn_resolvable_agents

    coordinator = _AgentsCoordinator("attractor-anthropic", "attractor-openai")
    backend_indexes_into = (getattr(coordinator, "config", None) or {}).get("agents", {})

    assert backend_indexes_into is coordinator.config["agents"]
    assert set(_spawn_resolvable_agents(coordinator)) == set(backend_indexes_into)


def test_drive_engine_does_not_police_resolution_without_a_spawn_capability(
    tmp_path, monkeypatch
) -> None:
    """The bare path keeps its current behavior: ``None``, not a refusal.

    With no ``session.spawn`` capability the profiles map is never consumed by
    a spawn at all, so adapter resolution is genuinely not knowable here.
    ``None`` means "do not police it" -- it never means "everything
    resolves" -- and this path must not acquire a new refusal.
    """
    from amplifier_module_loop_pipeline import _spawn_resolvable_agents
    from amplifier_module_loop_pipeline.preflight import ProviderPreflightError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")

    bare = _NoSpawnCoordinator()
    assert _spawn_resolvable_agents(bare) is None

    try:
        asyncio.run(
            drive_engine(
                _DOT_DRAIN_LOOP,
                bare,
                cwd=tmp_path,
                logs_root=tmp_path / "logs",
                profiles=_PROFILES_NAMING_AN_ABSENT_AGENT,
                transform=True,
            )
        )
    except ProviderPreflightError as exc:  # pragma: no cover - the defect
        pytest.fail(f"bare no-spawn path must not be refused at startup: {exc}")
    except Exception:
        pass  # any NON-preflight failure means the preflight let it through
