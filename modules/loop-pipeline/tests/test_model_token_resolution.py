"""Tests for live model-token resolution in the loop-pipeline node-model path.

Covers ``_resolve_concrete_model``: concrete ids pass through with NO network
call (full back-compat), family tokens and globs resolve via
``unified_llm.resolve_latest_for``, the resolution is cached once per run, and
unresolvable patterns fail loud. The resolver itself is monkeypatched so these
tests are fully offline and deterministic.
"""

import pytest

import amplifier_module_loop_pipeline.backend as backend


@pytest.fixture(autouse=True)
def _clear_cache():
    backend._MODEL_RESOLUTION_CACHE.clear()
    yield
    backend._MODEL_RESOLUTION_CACHE.clear()


@pytest.mark.asyncio
async def test_concrete_id_passes_through_without_network(monkeypatch):
    """A concrete id (even one containing a family substring) is returned
    unchanged and must NOT trigger a resolver/network call."""

    async def _boom(*a, **k):
        raise AssertionError("resolver must NOT be called for a concrete id")

    monkeypatch.setattr("unified_llm.resolve_latest_for", _boom)
    out = await backend._resolve_concrete_model("anthropic", "claude-sonnet-4-5")
    assert out == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_glob_resolves_via_live_resolver(monkeypatch):
    async def _stub(provider, pattern, *, stable_only):
        assert pattern == "claude-sonnet-4-*"
        assert stable_only is True
        return "claude-sonnet-4-5"

    monkeypatch.setattr("unified_llm.resolve_latest_for", _stub)
    out = await backend._resolve_concrete_model("anthropic", "claude-sonnet-4-*")
    assert out == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_family_token_expands_to_glob(monkeypatch):
    seen = {}

    async def _stub(provider, pattern, *, stable_only):
        seen["pattern"] = pattern
        return "claude-sonnet-4-5"

    monkeypatch.setattr("unified_llm.resolve_latest_for", _stub)
    out = await backend._resolve_concrete_model("anthropic", "sonnet")
    assert seen["pattern"] == "*sonnet*"
    assert out == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_resolves_once_per_run(monkeypatch):
    calls = {"n": 0}

    async def _stub(provider, pattern, *, stable_only):
        calls["n"] += 1
        return "claude-sonnet-4-5"

    monkeypatch.setattr("unified_llm.resolve_latest_for", _stub)
    await backend._resolve_concrete_model("anthropic", "sonnet")
    await backend._resolve_concrete_model("anthropic", "sonnet")
    assert calls["n"] == 1  # second call served from cache


@pytest.mark.asyncio
async def test_no_match_fails_loud(monkeypatch):
    async def _no_match(provider, pattern, *, stable_only):
        raise ValueError("no model ids match")

    monkeypatch.setattr("unified_llm.resolve_latest_for", _no_match)
    with pytest.raises(ValueError):
        await backend._resolve_concrete_model("anthropic", "claude-nope-*")


@pytest.mark.asyncio
async def test_none_passes_through_for_spawn(monkeypatch):
    """The spawn path tolerates a model-less node: None in, None out, no call."""

    async def _boom(*a, **k):
        raise AssertionError("resolver must NOT be called for a None model")

    monkeypatch.setattr("unified_llm.resolve_latest_for", _boom)
    assert await backend._resolve_concrete_model("anthropic", None) is None


def test_is_model_pattern_classification():
    # globs and exact family tokens are patterns
    assert backend._is_model_pattern("claude-sonnet-4-*")
    assert backend._is_model_pattern("sonnet")
    assert backend._is_model_pattern("HAIKU")  # case-insensitive token
    # concrete ids are NOT patterns (even when they contain a family substring)
    assert not backend._is_model_pattern("claude-sonnet-4-5")
    assert not backend._is_model_pattern("claude-haiku-4-5-20251001")
    assert not backend._is_model_pattern("gpt-5.4")


@pytest.mark.asyncio
async def test_emit_fires_once_on_resolution(monkeypatch):
    """A resolution emits a model:resolved event with the raw + concrete id."""
    from amplifier_module_loop_pipeline.pipeline_events import MODEL_RESOLVED

    async def _stub(provider, pattern, *, stable_only):
        return "claude-sonnet-4-6"

    monkeypatch.setattr("unified_llm.resolve_latest_for", _stub)

    events = []

    async def _emit(event_name, data):
        events.append((event_name, data))

    out = await backend._resolve_concrete_model(
        "anthropic", "claude-sonnet-4-*", emit=_emit
    )
    assert out == "claude-sonnet-4-6"
    assert len(events) == 1
    name, data = events[0]
    assert name == MODEL_RESOLVED
    assert data == {
        "raw": "claude-sonnet-4-*",
        "resolved": "claude-sonnet-4-6",
        "provider": "anthropic",
        "pattern": "claude-sonnet-4-*",
    }


@pytest.mark.asyncio
async def test_emit_not_called_for_concrete_id(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("resolver must NOT be called for a concrete id")

    monkeypatch.setattr("unified_llm.resolve_latest_for", _boom)

    called = []

    async def _emit(event_name, data):
        called.append(event_name)

    out = await backend._resolve_concrete_model(
        "anthropic", "claude-sonnet-4-5", emit=_emit
    )
    assert out == "claude-sonnet-4-5"
    assert called == []  # no resolution -> no event


@pytest.mark.asyncio
async def test_emit_not_called_on_cache_hit(monkeypatch):
    async def _stub(provider, pattern, *, stable_only):
        return "claude-sonnet-4-6"

    monkeypatch.setattr("unified_llm.resolve_latest_for", _stub)

    events = []

    async def _emit(event_name, data):
        events.append(event_name)

    await backend._resolve_concrete_model("anthropic", "sonnet", emit=_emit)
    await backend._resolve_concrete_model("anthropic", "sonnet", emit=_emit)
    assert len(events) == 1  # second call served from cache, no re-emit
