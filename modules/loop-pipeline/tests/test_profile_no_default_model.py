"""Tests that _resolve_model() requires explicit model specification.

Pre-fix behavior:
    _resolve_model(node)
    → if node.llm_model is None, return _DEFAULT_MODELS.get(provider, "claude-sonnet-4-20250514")
    → i.e., silently falls back to a hardcoded model (which may be deprecated)

Post-fix behavior:
    _resolve_model(node)
    → if node.llm_model is None, raise ValueError with a clear message
    → forces every pipeline to explicitly declare which model to use

This is the direct-tool-loop (Path B) code path.  Path A (spawn) relies
on the agent profile bundle's default_model config, which is addressed
separately by removing the default_model field from all agent YAML files.

Cranky-old-sam principle: no silent defaults.  "claude-sonnet-4-20250514"
appearing in a fallback dict is a deprecated model masquerading as a
safe choice.  Surface the omission immediately.
"""

import pytest

from amplifier_module_loop_pipeline.graph import Node


# ---------------------------------------------------------------------------
# Core: _resolve_model() must raise when no model is set
# ---------------------------------------------------------------------------


def test_resolve_model_raises_without_explicit_model():
    """_resolve_model() must raise ValueError when node.llm_model is not set.

    Before the fix: returns a hardcoded default from _DEFAULT_MODELS.
    After the fix: raises ValueError with a clear message.
    """
    from amplifier_module_loop_pipeline.backend import _resolve_model

    # Node with no explicit model
    node = Node(id="my-node", shape="box", prompt="Do something")

    with pytest.raises(ValueError) as exc_info:
        _resolve_model(node)

    error_message = str(exc_info.value)
    assert "model" in error_message.lower(), (
        f"ValueError message should mention 'model'. Got: {error_message!r}"
    )
    assert "my-node" in error_message or "llm_model" in error_message.lower(), (
        f"Error should identify what's missing (node id or attribute). "
        f"Got: {error_message!r}"
    )


def test_resolve_model_raises_for_anthropic_provider_without_model():
    """_resolve_model() must not fall back to 'claude-sonnet-4-20250514' for anthropic."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(id="anthro-node", shape="box", prompt="Anthropic task")
    # Explicitly set provider, but no model
    node.attrs["llm_provider"] = "anthropic"

    with pytest.raises(ValueError):
        _resolve_model(node)


def test_resolve_model_raises_for_openai_provider_without_model():
    """_resolve_model() must not fall back to 'gpt-4o' for openai."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(id="oai-node", shape="box", prompt="OpenAI task")
    node.attrs["llm_provider"] = "openai"

    with pytest.raises(ValueError):
        _resolve_model(node)


def test_resolve_model_raises_for_gemini_provider_without_model():
    """_resolve_model() must not fall back to 'gemini-2.0-flash' for gemini."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(id="gem-node", shape="box", prompt="Gemini task")
    node.attrs["llm_provider"] = "gemini"

    with pytest.raises(ValueError):
        _resolve_model(node)


# ---------------------------------------------------------------------------
# Positive: explicit model still works
# ---------------------------------------------------------------------------


def test_resolve_model_returns_explicit_model():
    """_resolve_model() must return the explicitly set llm_model."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(
        id="explicit-node",
        shape="box",
        prompt="With explicit model",
        attrs={"llm_model": "claude-3-7-sonnet-20250219"},
    )

    result = _resolve_model(node)

    assert result == "claude-3-7-sonnet-20250219", (
        f"_resolve_model() should return the explicit llm_model, got {result!r}"
    )


def test_resolve_model_returns_explicit_model_via_llm_model_attr():
    """_resolve_model() must use node.llm_model when set."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    # node.llm_model is a direct attribute (not via node.attrs)
    node = Node(id="direct-attr-node", shape="box", prompt="Direct attr test")
    # llm_model is a dataclass field on Node
    node.llm_model = "gpt-4.1-mini"

    result = _resolve_model(node)

    assert result == "gpt-4.1-mini", (
        f"_resolve_model() should return node.llm_model='gpt-4.1-mini', got {result!r}"
    )


# ---------------------------------------------------------------------------
# Structural check: _DEFAULT_MODELS is removed (or at minimum, not consulted)
# ---------------------------------------------------------------------------


def test_default_models_dict_not_used_as_fallback():
    """The _DEFAULT_MODELS dict (if it still exists) must NOT be consulted as a fallback.

    This test verifies the behavior, not the existence of the dict.  Even if
    _DEFAULT_MODELS remains for other reasons, _resolve_model() must not use
    it when llm_model is unset — it must raise instead.
    """
    from amplifier_module_loop_pipeline.backend import _resolve_model
    import amplifier_module_loop_pipeline.backend as backend_module

    # If the dict exists, its values must not be returned by _resolve_model
    default_models = getattr(backend_module, "_DEFAULT_MODELS", {})

    node = Node(id="probe-node", shape="box", prompt="Probe")

    # Regardless of whether _DEFAULT_MODELS exists, _resolve_model must raise
    with pytest.raises(ValueError):
        result = _resolve_model(node)
        # If we somehow reach here, fail the test explicitly
        if default_models:
            assert result not in default_models.values(), (
                f"_resolve_model() returned a value from _DEFAULT_MODELS: {result!r}. "
                f"It must raise instead of returning a default."
            )
