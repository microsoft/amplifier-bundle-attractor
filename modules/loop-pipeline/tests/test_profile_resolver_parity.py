"""Parity: preflight and _build_backend share ONE profile resolver (issue #279).

Before the extraction, ``PipelineOrchestrator.execute()``'s provider-preflight
step 5b and ``_build_backend()`` each carried an independent copy of the same
provider->agent-profile discovery rule (explicit ``config["profiles"]`` first,
else auto-discovery from ``coordinator.config["agents"]``, gated on
``session.spawn``, dict-valued entries only).  Fidelity was exact but pinned to
nothing: the preflight copy cited "``_build_backend()`` lines 438-443 exactly"
in a comment, and no test related the two.  A solo edit to either could drift
silently -- and a preflight that sees FEWER sources than the backend raises
false refusals, which is precisely how issue #196 happened.

These tests make the single home ENFORCED rather than promised: a resolver
monkeypatched ONCE must be observed by BOTH call sites, and the module must
contain exactly one implementation of the discovery rule.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

import amplifier_module_loop_pipeline as mod
from amplifier_module_loop_pipeline import (
    PipelineOrchestrator,
    _build_backend,
    _resolve_profiles,
)
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.preflight import ProviderPreflightError

# A graph whose node declares provider "openai".  Deliberately NOT a name the
# real resolver would ever produce for the coordinator below (real
# auto-discovery yields {"attractor-openai": "attractor-openai"}), so ONLY a
# call site that actually consumed the patched resolver can serve this node.
_DOT = """\
digraph parity {
    graph [goal="resolver parity"]
    start [shape=Mdiamond]
    work [shape=box, llm_provider="openai", prompt="do the thing"]
    done [shape=Msquare]
    start -> work -> done
}
"""


class _SpawnCoordinator:
    """session.spawn coordinator with exactly one agent: attractor-openai."""

    def __init__(self) -> None:
        self.spawned_agents: list[str] = []
        self.session = None
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-openai": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs):
        self.spawned_agents.append(kwargs.get("agent_name", "?"))
        return {"output": json.dumps({"status": "success"}), "session_id": "s-parity"}


# ---------------------------------------------------------------------------
# The headline: ONE patched resolver, ONE run, BOTH call sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_patched_resolver_is_consumed_by_both_call_sites(monkeypatch):
    """A single monkeypatched ``_resolve_profiles`` must be seen by the
    preflight AND by the backend build, in ONE ``execute()`` run.

    The scenario is discriminating in both directions:

    - if the PREFLIGHT still had its own copy, it would resolve
      ``{"attractor-openai": "attractor-openai"}`` (real auto-discovery), find
      no profile for the declared provider ``openai``, and raise
      ``ProviderPreflightError``;
    - if ``_build_backend`` still had its own copy, ``AmplifierBackend`` would
      get that same mapping, ``profiles.get("openai")`` would be ``None``, and
      the #155 no-fallback guard would fail the node without ever spawning.

    Only both call sites consuming the patched resolver produces a SUCCESS with
    a spawn of ``attractor-openai``.
    """
    seen: list[tuple[Any, Any]] = []

    def _fake_resolver(config, coordinator):
        seen.append((config, coordinator))
        return {"openai": "attractor-openai"}

    monkeypatch.setattr(mod, "_resolve_profiles", _fake_resolver)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")

    coordinator = _SpawnCoordinator()
    orchestrator = PipelineOrchestrator(config={"dot_source": _DOT})
    result = json.loads(
        await orchestrator.execute(
            prompt="goal",
            context=None,
            providers={},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
    )

    assert result["status"] == StageStatus.SUCCESS.value, result
    assert coordinator.spawned_agents == ["attractor-openai"], (
        "the backend did not consume the shared resolver"
    )
    assert len(seen) == 2, (
        f"expected exactly 2 resolver calls (preflight + _build_backend), got {len(seen)}"
    )
    # Both call sites must resolve from the SAME inputs -- that is the parity
    # property the two copies used to promise by comment alone.
    assert seen[0][0] is seen[1][0], "call sites passed different configs"
    assert seen[0][1] is seen[1][1], "call sites passed different coordinators"


@pytest.mark.asyncio
async def test_build_backend_consumes_the_shared_resolver(monkeypatch):
    """Direct pin on the second home: whatever the shared resolver returns is
    exactly what ``AmplifierBackend`` is constructed with."""
    marker = {"anthropic": "sentinel-agent"}
    monkeypatch.setattr(mod, "_resolve_profiles", lambda config, coordinator: marker)

    backend = _build_backend({}, {}, None, _SpawnCoordinator(), {"profiles": {}})

    assert backend is not None
    assert backend._profiles == marker


# ---------------------------------------------------------------------------
# Structural: exactly ONE implementation, and no rot-prone line citation
# ---------------------------------------------------------------------------


def test_module_contains_exactly_one_auto_discovery_implementation():
    """The discovery rule's distinguishing line -- the dict-valued agent filter
    -- must appear exactly once in the module: inside ``_resolve_profiles``.
    A second occurrence means a copy has been reintroduced."""
    source = inspect.getsource(mod)
    assert source.count("isinstance(agent_cfg, dict)") == 1, (
        "the agent auto-discovery filter appears more than once -- the single "
        "home has been duplicated again (issue #279)"
    )
    assert "isinstance(agent_cfg, dict)" in inspect.getsource(_resolve_profiles)


def test_both_call_sites_reference_the_shared_resolver_by_name():
    """Source-level companion to the behavioral pin above: neither call site
    may hand-roll discovery, and the rot-prone line-number citation that used
    to stand in for this test must not come back."""
    backend_src = inspect.getsource(_build_backend)
    execute_src = inspect.getsource(PipelineOrchestrator.execute)
    assert "_resolve_profiles(" in backend_src
    assert "_resolve_profiles(" in execute_src
    module_src = inspect.getsource(mod)
    assert "lines 438-443" not in module_src, (
        "a line-number citation of _build_backend() has returned; cite the "
        "function, not its line numbers (issue #279)"
    )


# ---------------------------------------------------------------------------
# The fail-closed posture must survive the extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_crash_refuses_it_never_falsely_accepts(monkeypatch):
    """#279: the preflight's outer exception handling is a fail-closed posture,
    not incidental defensiveness.  A discovery crash must yield FEWER profiles
    -- hence a refusal -- never a silent accept of an unserviceable provider."""

    def _boom(config, coordinator):
        raise RuntimeError("malformed coordinator config")

    monkeypatch.setattr(mod, "_resolve_profiles", _boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")

    coordinator = _SpawnCoordinator()
    orchestrator = PipelineOrchestrator(config={"dot_source": _DOT})
    with pytest.raises(ProviderPreflightError) as exc_info:
        await orchestrator.execute(
            prompt="goal",
            context=None,
            providers={"anthropic": object()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
    assert "work" in str(exc_info.value)
    assert not coordinator.spawned_agents, "zero spawns -- the run never started"


# ---------------------------------------------------------------------------
# Behavior of the shared resolver itself (the semantics both homes inherit)
# ---------------------------------------------------------------------------


def test_explicit_profiles_win_over_auto_discovery():
    coordinator = _SpawnCoordinator()
    assert _resolve_profiles({"profiles": {"anthropic": "mine"}}, coordinator) == {
        "anthropic": "mine"
    }


def test_empty_explicit_profiles_fall_through_to_auto_discovery():
    """Truthiness fall-through, preserved verbatim from BOTH former copies: an
    explicit but EMPTY mapping auto-discovers rather than meaning "no profiles"."""
    coordinator = _SpawnCoordinator()
    assert _resolve_profiles({"profiles": {}}, coordinator) == {
        "attractor-openai": "attractor-openai"
    }


def test_auto_discovery_is_gated_on_the_spawn_capability():
    class _NoSpawn(_SpawnCoordinator):
        def get_capability(self, name: str):
            return None

    assert _resolve_profiles({}, _NoSpawn()) == {}
    assert _resolve_profiles({}, None) == {}


def test_non_dict_agent_entries_are_filtered_out():
    coordinator = _SpawnCoordinator()
    coordinator.config["agents"]["not-a-dict"] = "just-a-string"
    assert _resolve_profiles({}, coordinator) == {
        "attractor-openai": "attractor-openai"
    }
