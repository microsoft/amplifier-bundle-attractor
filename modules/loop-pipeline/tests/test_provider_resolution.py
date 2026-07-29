"""Provider and profile resolution -- fail-loud, no hidden defaults (C2).

Locks two defects that both produced a WRONG MODEL while reporting SUCCESS:

A1  ``profiles.get(provider, next(iter(profiles.values())))`` silently ran an
    UNMAPPED provider's node on the FIRST agent profile.  Every other layer in
    the backend is fail-loud; this was the one gap.

A2  ``node.attrs.get("llm_provider", "anthropic")`` baked a POLICY into engine
    code.  It is wrong for any bundle that does not mount anthropic -- exactly
    the local-model case.

Also locks the RUNG-3 HARDENING: when a provider is resolved from anything
other than the node's own declaration, a ``pipeline:provider_defaulted`` event
MUST be emitted.  A log line is not sufficient -- an implicit default must be
machine-checkable, or a config accident silently becomes graph-wide policy.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from amplifier_module_loop_pipeline.backend import (
    AmplifierBackend,
    _resolve_profile,
    _resolve_provider,
)
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_PROVIDER_DEFAULTED

THREE_PROFILES = {
    "anthropic": "attractor-agent-anthropic",
    "openai": "attractor-agent-openai",
    "gemini": "attractor-agent-gemini",
}


class _Recorder:
    """Captures emitted pipeline events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, name: str, data: dict) -> None:
        self.events.append((name, data))

    def named(self, name: str) -> list[dict]:
        return [d for n, d in self.events if n == name]


def _resolve(node, *, candidates, default_provider=None, emit=None):
    return asyncio.run(
        _resolve_provider(
            node,
            candidates=candidates,
            default_provider=default_provider,
            emit=emit,
        )
    )


# ---------------------------------------------------------------------------
# Rung 1 -- the author declared it
# ---------------------------------------------------------------------------


def test_declared_provider_wins_and_maps_to_its_profile():
    """Baseline: an explicitly declared provider routes to its own profile."""
    node = Node(id="implement", attrs={"llm_provider": "openai"})

    provider = _resolve(node, candidates=tuple(THREE_PROFILES))

    assert provider == "openai"
    assert _resolve_profile(node.id, provider, THREE_PROFILES) == (
        "attractor-agent-openai"
    )


def test_declared_provider_emits_no_defaulted_event():
    """Rung 1 is not a default -- it must stay silent."""
    node = Node(id="implement", attrs={"llm_provider": "openai"})
    rec = _Recorder()

    _resolve(node, candidates=tuple(THREE_PROFILES), emit=rec)

    assert rec.named(PIPELINE_PROVIDER_DEFAULTED) == []


# ---------------------------------------------------------------------------
# A1 REGRESSION LOCK -- the silent misroute
# ---------------------------------------------------------------------------


def test_unmapped_provider_raises_and_never_falls_back():
    """THE A1 LOCK.

    Before C2 this returned ``attractor-agent-anthropic`` -- a different model,
    reporting SUCCESS.  It must now raise, and must NOT return the first
    profile.
    """
    with pytest.raises(ValueError) as exc:
        _resolve_profile("implement", "ollama", THREE_PROFILES)

    msg = str(exc.value)
    assert "ollama" in msg
    assert "attractor-agent-anthropic" not in msg.split("Mapped providers:")[0]
    # The fix must be stated, not just the failure.
    assert "profiles" in msg
    # And the mapped set must be listed so the author can act.
    for known in THREE_PROFILES:
        assert known in msg


def test_unmapped_provider_error_names_the_node():
    with pytest.raises(ValueError, match="my_node"):
        _resolve_profile("my_node", "vllm", THREE_PROFILES)


# ---------------------------------------------------------------------------
# Rung 2 -- the bundle declared it
# ---------------------------------------------------------------------------


def test_bundle_default_used_when_node_is_silent():
    node = Node(id="implement")
    rec = _Recorder()

    provider = _resolve(
        node,
        candidates=tuple(THREE_PROFILES),
        default_provider="anthropic",
        emit=rec,
    )

    assert provider == "anthropic"
    emitted = rec.named(PIPELINE_PROVIDER_DEFAULTED)
    assert len(emitted) == 1
    assert emitted[0]["node_id"] == "implement"
    assert emitted[0]["provider"] == "anthropic"
    assert emitted[0]["reason"] == "bundle_default"
    assert emitted[0]["candidates"] == sorted(THREE_PROFILES)


# ---------------------------------------------------------------------------
# Rung 3 -- HARDENED.  Unambiguous, but never silent.
# ---------------------------------------------------------------------------


def test_sole_candidate_resolves_and_emits_machine_checkable_signal():
    """THE RUNG-3 HARDENING LOCK.

    A single mounted provider is unambiguous, so we resolve it -- but a config
    accident (e.g. a second provider removed last month) must not become
    graph-wide policy invisibly.  The event is the signal; a log line is not
    assertable and therefore not enough.
    """
    node = Node(id="implement")
    rec = _Recorder()

    provider = _resolve(node, candidates=("local",), emit=rec)

    assert provider == "local"
    emitted = rec.named(PIPELINE_PROVIDER_DEFAULTED)
    assert len(emitted) == 1
    assert emitted[0]["reason"] == "sole_mapped_provider"
    assert emitted[0]["provider"] == "local"
    assert emitted[0]["node_id"] == "implement"


def test_sole_candidate_still_resolves_without_an_emitter():
    """emit is optional -- resolution must not depend on observability."""
    node = Node(id="implement")

    assert _resolve(node, candidates=("local",), emit=None) == "local"


# ---------------------------------------------------------------------------
# Rung 4 -- fail loud rather than guess
# ---------------------------------------------------------------------------


def test_ambiguous_with_no_default_fails_loud():
    """Multiple candidates and no declared default is unresolvable.

    This is the sibling case of rung 3 and the one most likely to be reached
    by a multi-provider bundle that forgets to declare its default.
    """
    node = Node(id="implement")

    with pytest.raises(ValueError) as exc:
        _resolve(node, candidates=tuple(THREE_PROFILES))

    msg = str(exc.value)
    assert "implement" in msg
    for known in THREE_PROFILES:
        assert known in msg
    # Both remedies must be offered.
    assert "llm_provider" in msg
    assert "default_provider" in msg


# ---------------------------------------------------------------------------
# Lossy-reconstruction lock (recurring bug class)
# ---------------------------------------------------------------------------


def test_clone_preserves_default_provider():
    """Parallel branches must not silently lose the bundle default.

    ``clone()`` is used for parallel branch isolation.  A field added to
    ``__init__`` but forgotten in ``clone()`` is the documented
    lossy-reconstruction bug class -- branches would fall through to rung 3 or
    rung 4 while the main path resolved fine.
    """
    backend = AmplifierBackend(
        coordinator=None,
        profiles=dict(THREE_PROFILES),
        default_provider="anthropic",
    )

    assert backend.clone()._default_provider == "anthropic"


# ---------------------------------------------------------------------------
# A2 lock -- the literal must be gone, and stay gone
# ---------------------------------------------------------------------------


def test_no_hardcoded_anthropic_default_remains_in_loop_pipeline():
    """THE A2 LOCK.

    Guards against a well-meaning re-introduction of the convenience default.
    Policy belongs in the bundle config, not in engine code.
    """
    pkg = pathlib.Path(__file__).parent.parent / "amplifier_module_loop_pipeline"
    pattern = re.compile(r"""\.get\(\s*["']llm_provider["']\s*,\s*["']anthropic["']""")

    offenders = [
        f"{path.relative_to(pkg)}:{i}"
        for path in pkg.rglob("*.py")
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]

    assert offenders == [], (
        f"hardcoded 'anthropic' provider default reintroduced at: {offenders}"
    )
