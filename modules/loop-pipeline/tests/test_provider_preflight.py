"""Startup provider preflight + no-fallback profile resolution (issue #155).

Covers the maintainer ruling's acceptance spec:

- R1: STARTUP PREFLIGHT -- before the walk begins, the engine cross-checks
  every node's declared ``llm_provider`` against the mounted providers and
  REFUSES TO START, naming each failing node, its provider, and the missing
  credential.  Static check only -- no live API call, zero nodes executed,
  zero budget consumed.
- R3: the silent profile fallback in ``backend.py``
  (``self._profiles.get(provider, next(iter(self._profiles.values()), ""))``)
  must not survive -- a declared provider with no profile fails loud naming
  the provider, never falls back to another provider's profile.
- R5: the provider-not-in-profiles regression class is a shipped test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from amplifier_module_loop_pipeline import PipelineOrchestrator
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.preflight import (
    ProviderPreflightError,
    check_provider_preflight,
    collect_declared_llm_providers,
)
from amplifier_module_loop_pipeline.transforms import apply_transforms

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DOT_DECLARED_OPENAI = """\
digraph declared_openai {
    graph [goal="preflight fixture"]
    start [shape=Mdiamond]
    critique_b [shape=box, llm_provider="openai", prompt="review"]
    done [shape=Msquare]
    start -> critique_b -> done
}
"""

_DOT_MULTI_UNSERVICEABLE = """\
digraph multi {
    start [shape=Mdiamond]
    a [shape=box, llm_provider="openai", prompt="x"]
    b [shape=box, llm_provider="openai", prompt="y"]
    c [shape=box, llm_provider="gemini", prompt="z"]
    done [shape=Msquare]
    start -> a -> b -> c -> done
}
"""


class _SpyBackend:
    """Injected backend spy -- proves the walk did/did not start."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        return json.dumps({"status": "success", "notes": f"mock: {node.id}"})


class _SpySpawnCoordinator:
    """Minimal coordinator with a session.spawn capability that records calls."""

    def __init__(self) -> None:
        self.spawn_called = False
        self.session = None
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-anthropic": {
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
        return {"output": json.dumps({"status": "success"}), "session_id": "c-1"}


# ---------------------------------------------------------------------------
# check_provider_preflight unit behavior (R1)
# ---------------------------------------------------------------------------


def test_refusal_names_node_provider_and_credential():
    graph = parse_dot(_DOT_DECLARED_OPENAI)
    with pytest.raises(ProviderPreflightError) as exc_info:
        check_provider_preflight(
            graph, profiles={"anthropic": "attractor-agent-anthropic"}, env={}
        )
    msg = str(exc_info.value)
    assert "critique_b" in msg  # the failing node
    assert 'llm_provider="openai"' in msg  # its provider
    assert "OPENAI_API_KEY" in msg  # the missing credential
    assert "refusing to start" in msg.lower()


def test_refusal_lists_every_failing_node_in_one_error():
    graph = parse_dot(_DOT_MULTI_UNSERVICEABLE)
    with pytest.raises(ProviderPreflightError) as exc_info:
        check_provider_preflight(
            graph, profiles={"anthropic": "attractor-agent-anthropic"}, env={}
        )
    msg = str(exc_info.value)
    for node_id in ("'a'", "'b'", "'c'"):
        assert node_id in msg, f"expected {node_id} in refusal:\n{msg}"
    assert "OPENAI_API_KEY" in msg
    assert "GEMINI_API_KEY" in msg


def test_profile_with_missing_credential_is_unserviceable():
    """The incident config: an 'openai' PROFILE is mounted (DEFAULT_PROFILES
    always maps it) but OPENAI_API_KEY is absent -- exactly the crash-loop
    configuration.  Must refuse, naming the profile AND the credential."""
    graph = parse_dot(_DOT_DECLARED_OPENAI)
    with pytest.raises(ProviderPreflightError) as exc_info:
        check_provider_preflight(
            graph,
            profiles={
                "anthropic": "attractor-agent-anthropic",
                "openai": "attractor-agent-openai",
            },
            env={"ANTHROPIC_API_KEY": "sk-x"},
        )
    msg = str(exc_info.value)
    assert "attractor-agent-openai" in msg
    assert "OPENAI_API_KEY is not set" in msg


def test_profile_with_credential_present_is_serviceable():
    graph = parse_dot(_DOT_DECLARED_OPENAI)
    check_provider_preflight(
        graph,
        profiles={"openai": "attractor-agent-openai"},
        env={"OPENAI_API_KEY": "sk-x"},
    )  # must not raise


def test_mounted_provider_module_is_serviceable():
    graph = parse_dot(_DOT_DECLARED_OPENAI)
    check_provider_preflight(graph, mounted_providers=["openai"], env={})


def test_unknown_provider_with_profile_gets_benefit_of_the_doubt():
    """A provider outside PROVIDER_KEY_ENV has no statically checkable
    credential -- a mounted profile is accepted as serviceable."""
    dot = _DOT_DECLARED_OPENAI.replace('"openai"', '"local-llama"')
    graph = parse_dot(dot)
    check_provider_preflight(graph, profiles={"local-llama": "agent-local"}, env={})


def test_unknown_provider_without_profile_is_refused():
    dot = _DOT_DECLARED_OPENAI.replace('"openai"', '"local-llama"')
    graph = parse_dot(dot)
    with pytest.raises(ProviderPreflightError) as exc_info:
        check_provider_preflight(graph, profiles={"anthropic": "a"}, env={})
    assert "local-llama" in str(exc_info.value)


def test_simulation_mode_is_skipped():
    """Nothing mounted at all -> simulation mode -- preflight must not make
    it unreachable."""
    graph = parse_dot(_DOT_DECLARED_OPENAI)
    check_provider_preflight(graph, mounted_providers=(), profiles=None, env={})


def test_undeclared_default_provider_is_not_policed():
    """A node with NO declared llm_provider uses the engine default; the
    preflight deliberately does not police the implicit default (documented
    scope decision -- see preflight.py)."""
    dot = """\
digraph undeclared {
    start [shape=Mdiamond]
    work [shape=box, prompt="x"]
    done [shape=Msquare]
    start -> work -> done
}
"""
    graph = parse_dot(dot)
    check_provider_preflight(graph, profiles={"openai": "agent-o"}, env={})


def test_non_llm_nodes_are_ignored():
    """llm_provider on a tool node is inert -- never triggers a refusal."""
    dot = """\
digraph toolonly {
    start [shape=Mdiamond]
    t [shape=parallelogram, tool_command="true", llm_provider="openai"]
    done [shape=Msquare]
    start -> t -> done
}
"""
    graph = parse_dot(dot)
    check_provider_preflight(graph, profiles={"anthropic": "a"}, env={})


def test_stylesheet_assigned_provider_is_visible_to_preflight():
    """apply_transforms materializes stylesheet properties before the
    preflight runs -- a stylesheet-routed provider is checked too."""
    dot = """\
digraph styled {
    graph [model_stylesheet="#work { llm_provider: openai }"]
    start [shape=Mdiamond]
    work [shape=box, prompt="x"]
    done [shape=Msquare]
    start -> work -> done
}
"""
    graph = parse_dot(dot)
    apply_transforms(graph, PipelineContext())
    declared = collect_declared_llm_providers(graph)
    assert declared == {"openai": ["work"]}
    with pytest.raises(ProviderPreflightError):
        check_provider_preflight(graph, profiles={"anthropic": "a"}, env={})


# ---------------------------------------------------------------------------
# Orchestrator startup refusal (R1: refuses to start, zero nodes executed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_execute_refuses_at_startup_before_any_node():
    """The mounted-orchestrator entry point refuses BEFORE the walk begins:
    the error is raised out of execute() and no engine/backend was built."""
    orchestrator = PipelineOrchestrator(
        config={
            "dot_source": _DOT_DECLARED_OPENAI,
            # The shipped bundle's shape: profiles for all three providers.
            # 'openai' has a profile but (in this test env) no credential.
            "profiles": {
                "anthropic": "attractor-agent-anthropic",
                "openai": "attractor-agent-openai",
            },
        }
    )
    import os

    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with pytest.raises(ProviderPreflightError) as exc_info:
            await orchestrator.execute(
                prompt="goal",
                context=None,
                providers={"anthropic": object()},
                tools={},
                hooks=None,
            )
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved
    msg = str(exc_info.value)
    assert "critique_b" in msg
    assert "OPENAI_API_KEY" in msg


@pytest.mark.asyncio
async def test_orchestrator_execute_with_injected_backend_skips_preflight():
    """An explicitly injected backend is the injector's responsibility --
    the preflight must not second-guess it (mock backends make no provider
    claims)."""
    spy = _SpyBackend()
    orchestrator = PipelineOrchestrator(config={"dot_source": _DOT_DECLARED_OPENAI})
    result_json = await orchestrator.execute(
        prompt="goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=spy,
    )
    result = json.loads(result_json)
    assert result["status"] == StageStatus.SUCCESS.value, result
    assert spy.calls == ["critique_b"]


@pytest.mark.asyncio
async def test_orchestrator_execute_serviceable_graph_runs_unaffected(monkeypatch):
    """Control: a graph whose declared provider IS serviceable (profile +
    credential present) passes the preflight and runs to completion via the
    auto-constructed spawn backend -- NOT the injected-backend skip path."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    coordinator = _SpySpawnCoordinator()
    coordinator.config["agents"]["attractor-agent-openai"] = {
        "session": {"orchestrator": {"module": "loop-agent"}},
    }
    orchestrator = PipelineOrchestrator(
        config={
            "dot_source": _DOT_DECLARED_OPENAI,
            "profiles": {"openai": "attractor-agent-openai"},
        }
    )
    result_json = await orchestrator.execute(
        prompt="goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        coordinator=coordinator,
    )
    result = json.loads(result_json)
    assert result["status"] == StageStatus.SUCCESS.value, result
    assert coordinator.spawn_called


# ---------------------------------------------------------------------------
# Auto-discovered profiles via coordinator.config["agents"] (issue #196)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_accepts_auto_discovered_agent_profile():
    """Regression (issue #196): execute() must NOT raise ProviderPreflightError
    when the only profiles come from auto-discovery (coordinator.config["agents"]).

    Scenario: no explicit "profiles" key in orchestrator config, coordinator has
    an agent whose name matches the declared llm_provider, at least one provider
    is mounted (so simulation mode is NOT triggered).  The preflight must see the
    auto-discovered profile and accept the run.
    """

    class _CoordWithAgent:
        """Coordinator with session.spawn and one agent named 'local-llama'."""

        config: dict = {
            "agents": {
                "local-llama": {"session": {"orchestrator": {"module": "loop-agent"}}},
            }
        }

        def get_capability(self, name: str):
            if name == "session.spawn":
                return self._spawn_fn
            return None

        async def _spawn_fn(self, **kwargs):
            return {
                "output": json.dumps({"status": "success", "notes": "auto-disc probe"}),
                "session_id": "s-auto",
            }

    dot = """\
digraph auto_disc {
    graph [goal="auto-discovery preflight regression"]
    start [shape=Mdiamond]
    work [shape=box, llm_provider="local-llama", prompt="do the thing"]
    done [shape=Msquare]
    start -> work -> done
}
"""
    orchestrator = PipelineOrchestrator(
        config={"dot_source": dot}
        # Deliberately NO "profiles" key -- auto-discovery path only
    )
    coordinator = _CoordWithAgent()

    # providers={"anthropic": object()} ensures simulation mode is NOT triggered
    # (mounted_providers is non-empty), so the preflight runs.
    # A ProviderPreflightError here is the bug -- "local-llama" has a matching
    # agent so _build_backend() would find it; the preflight must find it too.
    try:
        await orchestrator.execute(
            prompt="regression probe",
            context=None,
            providers={"anthropic": object()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
        # Reached here: preflight passed, run proceeded (and may have failed for
        # another reason -- that's fine, the defect is the false refusal).
    except ProviderPreflightError as exc:
        pytest.fail(
            f"execute() raised ProviderPreflightError for a provider whose agent "
            f"exists in coordinator.config['agents'] -- auto-discovery is broken "
            f"in the preflight.  Error: {exc}"
        )
    except Exception:
        # Any other exception means the preflight passed (the bug is absent) and
        # the pipeline failed for an unrelated reason (e.g. fake provider).
        pass


@pytest.mark.asyncio
async def test_preflight_still_refuses_provider_with_no_matching_agent():
    """Negative case (issue #196): a provider with no matching agent and no
    mounted module must still be refused.  The fix is additive (extends profile
    sources) -- it must not disable profile binding entirely.
    """

    class _CoordWithOtherAgent:
        """Coordinator with session.spawn but an agent for 'anthropic', not 'openai'."""

        config: dict = {
            "agents": {
                "anthropic": {"session": {"orchestrator": {"module": "loop-agent"}}},
            }
        }

        def get_capability(self, name: str):
            if name == "session.spawn":
                return self._spawn_fn
            return None

        async def _spawn_fn(self, **kwargs):
            return {"output": json.dumps({"status": "success"}), "session_id": "s-neg"}

    dot = """\
digraph neg_probe {
    graph [goal="negative preflight regression"]
    start [shape=Mdiamond]
    work [shape=box, llm_provider="openai", prompt="do the thing"]
    done [shape=Msquare]
    start -> work -> done
}
"""
    orchestrator = PipelineOrchestrator(
        config={"dot_source": dot}
        # No "profiles" -- auto-discovery path only
    )
    coordinator = _CoordWithOtherAgent()

    import os

    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with pytest.raises(ProviderPreflightError):
            await orchestrator.execute(
                prompt="negative probe",
                context=None,
                providers={"anthropic": object()},
                tools={},
                hooks=None,
                coordinator=coordinator,
            )
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


@pytest.mark.asyncio
async def test_preflight_explicit_profiles_still_override_auto_discovery():
    """Priority preservation (issue #196): explicit config["profiles"] must
    take precedence over auto-discovery.  When both are present, the explicit
    profiles win and auto-discovery is not consulted.
    """

    class _CoordWithAgent:
        config: dict = {
            "agents": {
                # Agent for 'local-llama' exists -- but we have explicit profiles
                # that do NOT include 'local-llama', so it should still be refused.
                "local-llama": {"session": {"orchestrator": {"module": "loop-agent"}}},
            }
        }

        def get_capability(self, name: str):
            if name == "session.spawn":
                return self._spawn_fn
            return None

        async def _spawn_fn(self, **kwargs):
            return {"output": json.dumps({"status": "success"}), "session_id": "s-prio"}

    dot = """\
digraph priority_probe {
    graph [goal="priority preflight regression"]
    start [shape=Mdiamond]
    work [shape=box, llm_provider="local-llama", prompt="do the thing"]
    done [shape=Msquare]
    start -> work -> done
}
"""
    orchestrator = PipelineOrchestrator(
        config={
            "dot_source": dot,
            # Explicit profiles present but do NOT include 'local-llama'.
            # Auto-discovery must NOT override this.
            "profiles": {"anthropic": "attractor-anthropic"},
        }
    )
    coordinator = _CoordWithAgent()

    # The preflight must refuse because explicit profiles are used (and they
    # don't include 'local-llama'), not auto-discovered ones.
    with pytest.raises(ProviderPreflightError) as exc_info:
        await orchestrator.execute(
            prompt="priority probe",
            context=None,
            providers={"anthropic": object()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
    assert "local-llama" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Backend no-fallback profile resolution (R3 + R5 regression class)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_path_provider_not_in_profiles_fails_loud_never_falls_back():
    """R5 regression: declared provider absent from profiles must raise,
    naming the provider -- NOT silently spawn another provider's profile."""
    coordinator = _SpySpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    from amplifier_module_loop_pipeline.graph import Node

    node = Node(id="critique_b", prompt="review", attrs={"llm_provider": "openai"})
    with pytest.raises(ValueError, match="openai") as exc_info:
        await backend.run(node, "review the work", PipelineContext())
    assert not coordinator.spawn_called, (
        "spawn must never be reached with another provider's profile"
    )
    msg = str(exc_info.value)
    assert "critique_b" in msg
    assert "OPENAI_API_KEY" in msg  # names the missing credential


@pytest.mark.asyncio
async def test_spawn_path_empty_profiles_fails_loud():
    """Empty profiles + spawn available: the old code silently resolved
    profile_name='' -- now it fails loud naming the provider."""
    coordinator = _SpySpawnCoordinator()
    backend = AmplifierBackend(coordinator=coordinator, profiles={})
    from amplifier_module_loop_pipeline.graph import Node

    node = Node(id="work", prompt="x", attrs={"llm_provider": "anthropic"})
    with pytest.raises(ValueError, match="anthropic"):
        await backend.run(node, "task", PipelineContext())
    assert not coordinator.spawn_called


@pytest.mark.asyncio
async def test_spawn_path_matching_profile_still_routes_correctly():
    """Control: exact profile match keeps working exactly as before."""
    coordinator = _SpySpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    from amplifier_module_loop_pipeline.graph import Node

    node = Node(id="work", prompt="x", attrs={"llm_provider": "anthropic"})
    outcome = await backend.run(node, "task", PipelineContext())
    assert coordinator.spawn_called
    assert outcome.status == StageStatus.SUCCESS
