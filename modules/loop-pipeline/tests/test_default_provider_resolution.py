"""Tests for the engine-resolved default llm_provider (FIX 1).

Prior behavior: the engine hardcoded ``"anthropic"`` as the default
``llm_provider`` for any node that declared none, at three literal call
sites (``backend.py:241``, ``backend.py:710``, ``__init__.py``). A
single-provider session mounting anything other than anthropic (e.g. only
github-copilot) hard-failed asking for an unmounted anthropic provider.

Fix: the default is now RESOLVED from the mounted provider set, computed in
``_build_backend()`` (the only place the full ``providers`` dict is visible)
and threaded into both backends as ``default_provider=``/``mounted_providers=``.
Per-node resolution (``_resolve_node_provider()`` in ``backend.py``, shared by
both backends) follows:

1. Explicit ``node.llm_provider`` / ``node.attrs["llm_provider"]`` wins.
2. Else the engine default -- the SOLE mounted provider.
3. Else (zero mounted) -> ``None`` -- simulation mode, unaffected here.
4. Else (>1 mounted, node names none) -> fail loud with a ``ValueError``
   naming the mounted providers, rather than silently picking one family
   (the issue #155 anti-pattern this repo explicitly forbids).

Covers:
- (a) sole-mounted non-anthropic provider routes a bare node to that provider
- (b) >1 mounted + bare node raises the ambiguous ValueError
- (c) single-anthropic-mount legacy behavior is unchanged
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

unified_llm = pytest.importorskip("unified_llm")

from amplifier_module_loop_pipeline import (  # noqa: E402
    DirectProviderBackend,
    _build_backend,
    _resolve_default_provider,
)
from amplifier_module_loop_pipeline.backend import (  # noqa: E402
    AmplifierBackend,
    _resolve_node_provider,
)
from amplifier_module_loop_pipeline.context import PipelineContext  # noqa: E402
from amplifier_module_loop_pipeline.graph import Node  # noqa: E402
from amplifier_module_loop_pipeline.outcome import StageStatus  # noqa: E402


def _make_node(**kwargs: Any) -> Node:
    defaults = {"id": "codegen", "prompt": "do the thing"}
    defaults.update(kwargs)
    return Node(**defaults)


def _make_generate_result(text: str) -> "unified_llm.GenerateResult":
    usage = unified_llm.Usage(input_tokens=1, output_tokens=1, total_tokens=2)
    response = unified_llm.Response(
        id="resp-mock",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
    )
    return unified_llm.GenerateResult(
        text=text,
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
        total_usage=usage,
        steps=[],
        response=response,
    )


# ---------------------------------------------------------------------------
# _resolve_default_provider() -- computed from the mounted provider set
# ---------------------------------------------------------------------------


def test_resolve_default_provider_zero_mounted_is_none():
    assert _resolve_default_provider({}) is None


def test_resolve_default_provider_sole_mounted_non_anthropic():
    """A single mounted github-copilot provider becomes the default -- not anthropic."""
    assert _resolve_default_provider({"github-copilot": object()}) == "github-copilot"


def test_resolve_default_provider_sole_mounted_anthropic_unchanged():
    assert _resolve_default_provider({"anthropic": object()}) == "anthropic"


def test_resolve_default_provider_multiple_mounted_is_ambiguous_none():
    providers = {"anthropic": object(), "openai": object()}
    assert _resolve_default_provider(providers) is None


# ---------------------------------------------------------------------------
# _resolve_node_provider() -- shared per-node resolution rule
# ---------------------------------------------------------------------------


def test_resolve_node_provider_explicit_field_wins_over_default():
    node = _make_node(llm_provider="openai")
    assert _resolve_node_provider(node, "anthropic", ("anthropic", "openai")) == "openai"


def test_resolve_node_provider_explicit_attrs_wins_over_default():
    node = _make_node(attrs={"llm_provider": "gemini"})
    assert _resolve_node_provider(node, "anthropic", ("anthropic",)) == "gemini"


def test_resolve_node_provider_bare_node_uses_sole_mounted_default():
    node = _make_node()
    assert (
        _resolve_node_provider(node, "github-copilot", ("github-copilot",))
        == "github-copilot"
    )


def test_resolve_node_provider_bare_node_ambiguous_raises_value_error():
    """>1 mounted, node names none -> fail loud, never silently pick one (issue #155)."""
    node = _make_node(id="critique_b")
    with pytest.raises(ValueError, match="critique_b") as excinfo:
        _resolve_node_provider(node, None, ("anthropic", "openai"))
    message = str(excinfo.value)
    assert "anthropic" in message
    assert "openai" in message
    assert "2 providers are mounted" in message


def test_resolve_node_provider_zero_mounted_and_no_default_raises():
    """Zero mounted with a non-None default never happens in practice, but a bare
    node with no default and zero mounted providers still fails loud rather than
    crashing obscurely -- simulation mode is handled upstream by never invoking
    this resolver (backend is None), not by this function returning a sentinel.
    """
    node = _make_node(id="lonely")
    with pytest.raises(ValueError, match="lonely"):
        _resolve_node_provider(node, None, ())


# ---------------------------------------------------------------------------
# _build_backend() wiring -- default_provider/mounted_providers threaded through
# ---------------------------------------------------------------------------


def _make_coordinator_no_spawn() -> Any:
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=None)
    coordinator.config = {}
    return coordinator


def _make_coordinator_with_spawn(agents: dict | None = None) -> Any:
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=MagicMock())
    coordinator.config = {"agents": agents or {}}
    return coordinator


def test_build_backend_threads_sole_provider_default_into_direct_backend():
    coordinator = _make_coordinator_no_spawn()
    providers = {"github-copilot": object()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert isinstance(backend, DirectProviderBackend)
    assert backend._default_provider == "github-copilot"
    assert backend._mounted_providers == ("github-copilot",)


def test_build_backend_threads_none_default_when_multiple_providers_mounted():
    coordinator = _make_coordinator_no_spawn()
    providers = {"anthropic": object(), "openai": object()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert isinstance(backend, DirectProviderBackend)
    assert backend._default_provider is None
    assert set(backend._mounted_providers) == {"anthropic", "openai"}


def test_build_backend_threads_default_into_amplifier_backend():
    coordinator = _make_coordinator_with_spawn(
        agents={"attractor-anthropic": {"session": {"orchestrator": {}}}}
    )
    providers = {"anthropic": object()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert isinstance(backend, AmplifierBackend)
    assert backend._default_provider == "anthropic"
    assert backend._mounted_providers == ("anthropic",)


def test_build_backend_no_providers_returns_none_backend():
    """Zero mounted providers still falls through to simulation mode (unchanged)."""
    coordinator = _make_coordinator_no_spawn()
    backend = _build_backend({}, {}, None, coordinator, {})
    assert backend is None


# ---------------------------------------------------------------------------
# AmplifierBackend.clone() copies the new fields (else test_backend_clone.py
# style AttributeError on a cloned branch)
# ---------------------------------------------------------------------------


def test_amplifier_backend_clone_copies_default_provider_fields():
    coordinator = _make_coordinator_with_spawn()
    backend = AmplifierBackend(
        coordinator,
        profiles={},
        provider=object(),
        default_provider="github-copilot",
        mounted_providers=("github-copilot",),
    )
    clone = backend.clone()
    assert clone._default_provider == "github-copilot"
    assert clone._mounted_providers == ("github-copilot",)


# ---------------------------------------------------------------------------
# End-to-end-ish: DirectProviderBackend.run() actually routes a bare node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_backend_bare_node_routes_to_sole_mounted_non_anthropic(
    monkeypatch,
):
    """(a) Sole-mounted non-anthropic provider routes a bare node to that provider."""
    captured_kwargs: dict[str, Any] = {}

    async def _fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result(json.dumps({"status": "success"}))

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(
        provider=object(),  # truthy sentinel
        unified_client=object(),
        default_provider="github-copilot",
        mounted_providers=("github-copilot",),
    )
    # Bare node: no llm_provider attribute or attrs key at all.
    node = _make_node(attrs={"llm_model": "some-model"})

    result = await backend.run(node, "task", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert captured_kwargs.get("provider") == "github-copilot"


@pytest.mark.asyncio
async def test_direct_backend_bare_node_ambiguous_multi_provider_raises(monkeypatch):
    """(b) >1 mounted + bare node raises the ambiguous ValueError, never picks one."""

    async def _fake_generate(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("generate() must not be called when resolution fails")

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=object(),
        default_provider=None,  # >1 mounted -> ambiguous, per _resolve_default_provider
        mounted_providers=("anthropic", "openai"),
    )
    node = _make_node(id="critique_b", attrs={"llm_model": "some-model"})

    with pytest.raises(ValueError, match="critique_b"):
        await backend.run(node, "task", PipelineContext())


@pytest.mark.asyncio
async def test_direct_backend_bare_node_single_anthropic_mount_unchanged(monkeypatch):
    """(c) Single anthropic mount: bare node still routes to anthropic (legacy behavior)."""
    captured_kwargs: dict[str, Any] = {}

    async def _fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result(json.dumps({"status": "success"}))

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=object(),
        default_provider="anthropic",
        mounted_providers=("anthropic",),
    )
    node = _make_node(attrs={"llm_model": "some-model"})

    result = await backend.run(node, "task", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert captured_kwargs.get("provider") == "anthropic"


@pytest.mark.asyncio
async def test_direct_backend_defaults_are_backward_compatible_when_omitted(monkeypatch):
    """Constructing DirectProviderBackend without the new kwargs at all (positional-only
    legacy call sites) must still work -- and a bare node then hits the ambiguous/no-default
    path (None, ()) rather than silently defaulting to anthropic.
    """

    async def _fake_generate(**kwargs):  # pragma: no cover
        raise AssertionError("generate() must not be called when resolution fails")

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(object(), {}, None, None, unified_client=object())
    node = _make_node(attrs={"llm_model": "some-model"})

    with pytest.raises(ValueError):
        await backend.run(node, "task", PipelineContext())
