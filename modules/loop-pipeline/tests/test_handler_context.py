"""Tests for HandlerContext dataclass and updated HandlerRegistry constructor.

Spec coverage: T2.1 — HandlerContext replaces **kwargs: Any in HandlerRegistry.__init__.

These tests define the expected behavior BEFORE implementation (TDD RED phase).
"""

import dataclasses

import pytest

from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# HandlerContext construction
# ---------------------------------------------------------------------------


def test_handler_context_is_a_dataclass():
    """HandlerContext is a dataclass (has dataclass metadata)."""
    assert dataclasses.is_dataclass(HandlerContext)


def test_handler_context_is_frozen():
    """HandlerContext is frozen — mutation after construction raises."""
    ctx = HandlerContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.backend = "something"  # type: ignore[misc]


def test_handler_context_default_all_none():
    """HandlerContext() with no args produces all-None fields."""
    ctx = HandlerContext()
    assert ctx.backend is None
    assert ctx.hooks is None
    assert ctx.cancel_event is None
    assert ctx.interviewer is None


def test_handler_context_fields_accessible():
    """HandlerContext exposes backend, hooks, cancel_event, interviewer."""
    mock_backend = object()
    mock_hooks = object()
    mock_event = object()
    mock_interviewer = object()
    ctx = HandlerContext(
        backend=mock_backend,
        hooks=mock_hooks,
        cancel_event=mock_event,
        interviewer=mock_interviewer,
    )
    assert ctx.backend is mock_backend
    assert ctx.hooks is mock_hooks
    assert ctx.cancel_event is mock_event
    assert ctx.interviewer is mock_interviewer


def test_handler_context_partial_construction():
    """HandlerContext can be constructed with only some fields."""
    mock_backend = object()
    ctx = HandlerContext(backend=mock_backend)
    assert ctx.backend is mock_backend
    assert ctx.hooks is None
    assert ctx.cancel_event is None
    assert ctx.interviewer is None


# ---------------------------------------------------------------------------
# HandlerRegistry constructor: ctx is required
# ---------------------------------------------------------------------------


def test_registry_requires_handler_context_positional_arg():
    """HandlerRegistry() without a HandlerContext raises TypeError.

    After the T2.1 refactor, `HandlerRegistry.__init__` takes a required
    positional `ctx: HandlerContext` argument.  Calling without it must
    fail at runtime (and at pyright write-time).
    """
    with pytest.raises(TypeError):
        HandlerRegistry()  # type: ignore[call-arg]


def test_registry_kwargs_no_longer_accepted():
    """HandlerRegistry(backend=x) raises TypeError — the **kwargs API is gone.

    The old `HandlerRegistry(backend=x, hooks=y, ...)` signature is
    eliminated.  Callers must wrap in HandlerContext first.
    """
    with pytest.raises(TypeError):
        HandlerRegistry(backend="something")  # type: ignore[call-arg]


def test_registry_accepts_empty_handler_context():
    """HandlerRegistry(HandlerContext()) works — all fields default to None."""
    registry = HandlerRegistry(HandlerContext())
    assert registry is not None


# ---------------------------------------------------------------------------
# HandlerRegistry wires ctx fields to handlers
# ---------------------------------------------------------------------------


def test_registry_ctx_backend_wired_to_codergen():
    """HandlerRegistry wires ctx.backend to CodergenHandler."""
    from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler

    mock_backend = object()
    ctx = HandlerContext(backend=mock_backend)
    registry = HandlerRegistry(ctx)
    codergen = registry._handlers["codergen"]
    assert isinstance(codergen, CodergenHandler)
    assert codergen._backend is mock_backend


def test_registry_ctx_interviewer_wired_to_human_gate():
    """HandlerRegistry wires ctx.interviewer to HumanGateHandler."""
    from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler

    mock_interviewer = object()
    ctx = HandlerContext(interviewer=mock_interviewer)
    registry = HandlerRegistry(ctx)
    human_gate = registry._handlers["wait.human"]
    assert isinstance(human_gate, HumanGateHandler)
    assert human_gate._interviewer is mock_interviewer


def test_registry_ctx_hooks_wired_to_parallel_handler():
    """HandlerRegistry wires ctx.hooks to ParallelHandler."""
    from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler

    mock_hooks = object()
    ctx = HandlerContext(hooks=mock_hooks)
    registry = HandlerRegistry(ctx)
    parallel = registry._handlers["parallel"]
    assert isinstance(parallel, ParallelHandler)
    assert parallel._hooks is mock_hooks


def test_registry_ctx_stored_on_registry():
    """HandlerRegistry stores the HandlerContext as self._ctx (for clone_for_branch)."""
    ctx = HandlerContext()
    registry = HandlerRegistry(ctx)
    assert registry._ctx is ctx


# ---------------------------------------------------------------------------
# clone_for_branch works with HandlerContext
# ---------------------------------------------------------------------------


def test_clone_for_branch_preserves_ctx():
    """clone_for_branch() preserves _ctx on the cloned registry."""
    from unittest.mock import MagicMock

    mock_backend = MagicMock()
    cloned_backend = MagicMock()
    mock_backend.clone.return_value = cloned_backend

    ctx = HandlerContext(backend=mock_backend)
    registry = HandlerRegistry(ctx)
    cloned = registry.clone_for_branch()
    # The cloned registry should have _ctx pointing to the same frozen context
    assert cloned._ctx is ctx


# ---------------------------------------------------------------------------
# Design-intent documentation test
# ---------------------------------------------------------------------------


def test_handler_context_design_intent():
    """HandlerContext design intent: explicit named fields replace **kwargs: Any.

    This is a documentation test.  It verifies that HandlerContext:
    1. Has exactly the expected field names (no more, no fewer)
    2. No field named 'subgraph_runner' (eliminated in PR #36)
    3. No unknown kwargs silently accepted
    """
    field_names = {f.name for f in dataclasses.fields(HandlerContext)}
    assert field_names == {"backend", "hooks", "cancel_event", "interviewer"}
    assert "subgraph_runner" not in field_names
