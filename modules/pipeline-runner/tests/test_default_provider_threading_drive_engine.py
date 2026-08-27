"""FIX A: drive_engine must thread the MOUNTED provider set into AmplifierBackend.

FIX 1 (see ``modules/loop-pipeline/tests/test_default_provider_resolution.py``)
made the loop-pipeline engine resolve a bare node's (no explicit
``llm_provider``) default from the MOUNTED provider set via
``_resolve_default_provider`` / ``_resolve_node_provider``, threaded into
``AmplifierBackend`` as ``default_provider=``/``mounted_providers=``.

But the standalone-CLI path (``drive_engine`` in runner.py) constructed
``AmplifierBackend(coordinator=..., profiles=...)`` WITHOUT passing those two
kwargs. They are keyword-only with defaults ``None``/``()``, so the CLI path
always saw "0 mounted" and EVERY bare node hit the ambiguous-resolution
``ValueError`` -- for any provider, regardless of what was actually mounted.
Confirmed live in a DTU.

These tests pin the fix at the ``drive_engine`` seam (mirroring the style of
``test_provider_preflight_drive_engine.py``):

(a) sole-mounted provider -> ``default_provider``/``mounted_providers`` are
    threaded into ``AmplifierBackend`` exactly as the mounted-orchestrator
    path would, and the bare node reaches execution (a real spawn) instead
    of raising.
(b) >1 mounted provider + a bare node -> still fails loud, per-node, at
    resolution time (the #155 anti-pattern this repo forbids: never silently
    pick one family).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_pipeline_runner.runner import drive_engine

# A BARE node: no llm_provider attribute at all. Must inherit the engine
# default rather than raise (sole-mounted case) or must raise loud (ambiguous
# multi-mounted case) -- never silently pick a provider.
_DOT_BARE_NODE = """\
digraph bare {
    graph [goal="bare-node provider inheritance fixture"]
    start [shape=Mdiamond]
    codegen [shape=box, prompt="do the work"]
    done [shape=Msquare]
    start -> codegen -> done
}
"""

_LOOP_AGENT: dict[str, Any] = {"session": {"orchestrator": {"module": "loop-agent"}}}


class _ProvidersCoordinator:
    """Coordinator stub exposing a ``.get("providers")`` mount point --
    the SAME mount point amplifier_core's own module-execute dispatch reads
    (see e.g. ``coordinator.get("providers") or {}`` in amplifier_core's
    ``_session_exec.py``/``session.py``) -- plus a recording spawn
    capability, mirroring ``_AgentsCoordinator`` in
    ``test_provider_preflight_drive_engine.py``.
    """

    def __init__(
        self, providers: dict[str, Any], agent_names: tuple[str, ...]
    ) -> None:
        self._providers = providers
        self.spawn_calls: list[str] = []
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {
            "agents": {name: dict(_LOOP_AGENT) for name in agent_names}
        }

    def get(self, key: str, default: Any = None) -> Any:
        if key == "providers":
            return self._providers
        return default

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs):
        self.spawn_calls.append(str(kwargs.get("agent_name")))
        return {
            "output": json.dumps({"status": "success", "notes": "stub"}),
            "session_id": "child-1",
        }


def test_drive_engine_threads_sole_mounted_provider_into_backend(
    tmp_path, monkeypatch
) -> None:
    """(a) Sole-mounted provider (e.g. "github-copilot") -> default_provider
    threaded == that provider, mounted_providers == ("github-copilot",), and
    the bare node reaches execution rather than raising.

    Against the pre-fix runner this configuration raised the ambiguous
    ``ValueError`` for the "codegen" node on EVERY run, regardless of the
    fact that exactly one provider was mounted.
    """
    captured_kwargs: dict[str, Any] = {}
    original_init = AmplifierBackend.__init__

    def _spy_init(self, *args, **kwargs):
        captured_kwargs.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(AmplifierBackend, "__init__", _spy_init)

    coordinator = _ProvidersCoordinator(
        providers={"github-copilot": object()},
        agent_names=("attractor-github-copilot",),
    )

    outcome = asyncio.run(
        drive_engine(
            _DOT_BARE_NODE,
            coordinator,
            cwd=tmp_path,
            logs_root=tmp_path / "logs",
            profiles={"github-copilot": "attractor-github-copilot"},
            transform=True,
        )
    )

    assert captured_kwargs.get("default_provider") == "github-copilot"
    assert captured_kwargs.get("mounted_providers") == ("github-copilot",)
    assert outcome.status.value == "success", outcome
    assert coordinator.spawn_calls == ["attractor-github-copilot"], (
        "the bare node must actually reach spawn/execution, not raise"
    )


def test_drive_engine_ambiguous_multi_mounted_bare_node_still_fails_loud(
    tmp_path,
) -> None:
    """(b) >1 mounted provider + a bare node -> still fails loud, per-node.

    The fix must not paper over genuine ambiguity: with two providers
    mounted and the node naming none, resolution must still fail, naming
    the failing node and the mounted providers -- never silently pick one
    family (issue #155's forbidden anti-pattern).

    ``_resolve_node_provider``'s ``ValueError`` is raised inside node
    execution (``AmplifierBackend.run``), which the engine's own node
    handler catches and converts into a failed ``Outcome`` (never a bare
    node-loss of the exception all the way out of ``drive_engine``) -- so
    the pinned assertion is on the returned ``Outcome``, not a raised
    exception. ``test_resolve_node_provider_bare_node_ambiguous_raises_value_error``
    in ``modules/loop-pipeline/tests/test_default_provider_resolution.py``
    already pins the raw resolver's raise; this test pins that the SAME
    failure reaches the CLI/``drive_engine`` seam undegraded.
    """
    coordinator = _ProvidersCoordinator(
        providers={"anthropic": object(), "openai": object()},
        agent_names=("attractor-anthropic", "attractor-openai"),
    )

    outcome = asyncio.run(
        drive_engine(
            _DOT_BARE_NODE,
            coordinator,
            cwd=tmp_path,
            logs_root=tmp_path / "logs",
            profiles={
                "anthropic": "attractor-anthropic",
                "openai": "attractor-openai",
            },
            transform=True,
        )
    )

    assert outcome.status.value == "fail", outcome
    msg = outcome.failure_reason or ""
    assert "codegen" in msg  # the failing (bare) node
    assert "anthropic" in msg
    assert "openai" in msg
    assert not coordinator.spawn_calls, (
        "ambiguous resolution must fail before any spawn is issued"
    )
