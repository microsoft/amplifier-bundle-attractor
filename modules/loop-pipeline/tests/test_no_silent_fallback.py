"""Tests that unknown shapes raise clear errors instead of falling back to codergen.

Spec §2.8 defines a finite set of known shapes.  Any shape NOT in SHAPE_TO_HANDLER
is a programming error (typo, stale pipeline, unsupported shape) and must be
detected loudly at handler-dispatch time — not silently delegated to the codergen
LLM agent.

The pre-fix behavior was:
    HandlerRegistry.get(node) where node.shape="badshape"
    → SHAPE_TO_HANDLER.get("badshape", "codergen")  # silently returns "codergen"
    → CodergenHandler executes a full LLM session for a node that was never
      meant to be LLM-driven.

The post-fix behavior:
    HandlerRegistry.get(node) where node.shape="badshape"
    → raises ValueError with a message that names the bad shape
      AND lists the recognized shapes.

This is the "cranky-old-sam" principle: clear failure beats partial hack footgun.
"""

import pytest

from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Contract: Unknown shape raises ValueError with a clear message
# ---------------------------------------------------------------------------


def test_unknown_shape_raises_value_error():
    """HandlerRegistry.get() must raise ValueError for an unrecognized shape.

    Before the fix: silently returns CodergenHandler.
    After the fix: raises ValueError naming the bad shape.
    """
    registry = HandlerRegistry(HandlerContext())
    bad_node = Node(id="broken-gate", shape="trapezoid")

    with pytest.raises(ValueError, match="trapezoid"):
        registry.get(bad_node)


def test_unknown_shape_error_lists_supported_shapes():
    """ValueError message must include the list of supported shapes.

    The error message must help the author fix the problem, not just
    say 'unknown shape' — they need to know what IS valid.
    """
    registry = HandlerRegistry(HandlerContext())
    bad_node = Node(id="broken-gate", shape="octagon_mystery")

    with pytest.raises(ValueError) as exc_info:
        registry.get(bad_node)

    error_message = str(exc_info.value)
    # At least some of the known shapes must appear in the error
    known_shapes = list(SHAPE_TO_HANDLER.keys())
    shapes_in_message = [s for s in known_shapes if s in error_message]
    assert shapes_in_message, (
        f"Error message must list supported shapes. Message: {error_message!r}. "
        f"Expected at least one of: {known_shapes}"
    )


def test_unknown_shape_names_the_bad_shape():
    """The ValueError message must include the offending shape name."""
    registry = HandlerRegistry(HandlerContext())
    bad_node = Node(id="my-node", shape="cloud_undefined")

    with pytest.raises(ValueError) as exc_info:
        registry.get(bad_node)

    assert "cloud_undefined" in str(exc_info.value), (
        f"Error message should include the bad shape name 'cloud_undefined'. "
        f"Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# Regression: All known shapes still dispatch correctly after removing fallback
# ---------------------------------------------------------------------------


def test_known_shapes_still_dispatch_correctly():
    """After removing the fallback, all known shapes in SHAPE_TO_HANDLER must still work.

    This is a regression guard: removing .get(shape, "codergen") must not
    accidentally break dispatch for any already-registered shape.
    """
    registry = HandlerRegistry(HandlerContext())

    for shape, expected_handler_type in SHAPE_TO_HANDLER.items():
        node = Node(id=f"test-{shape}", shape=shape)
        # If the shape is registered AND the handler is registered, this must not raise.
        # The only exception: tripleoctagon requires kwargs, but we just care it doesn't
        # raise a ValueError about "unknown shape".
        try:
            handler = registry.get(node)
            assert handler is not None, (
                f"registry.get() returned None for shape='{shape}' "
                f"(expected_handler_type='{expected_handler_type}')"
            )
        except ValueError as exc:
            pytest.fail(
                f"registry.get() raised ValueError for known shape '{shape}': {exc}"
            )


def test_codergen_still_works_for_box_shape():
    """shape=box must still dispatch to CodergenHandler (the LLM-driven node type).

    Removing the fallback must not remove the legitimate codergen dispatch path.
    Authors who want LLM nodes must use shape=box explicitly.
    """
    from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler

    registry = HandlerRegistry(HandlerContext())
    llm_node = Node(id="my-llm-node", shape="box", prompt="Do something")
    handler = registry.get(llm_node)

    assert isinstance(handler, CodergenHandler), (
        f"shape=box must dispatch to CodergenHandler, got {type(handler).__name__}. "
        f"Removing the fallback must not break the legitimate codergen shape."
    )
