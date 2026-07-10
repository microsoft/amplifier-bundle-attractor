"""Unit tests for drive_engine's observability-hook defaulting.

The engine and handler stack emit ``pipeline:*`` / ``provider:*`` / ``tool:*``
events to whatever ``hooks`` object they're constructed with. A mounted
observability hook lives on ``coordinator.hooks``; the mounted-orchestrator
path reaches it because the session hands the orchestrator ``coordinator.hooks``
and it forwards that same object into the engine. When we drive the engine
directly we must do the same, or those events are emitted into nothing.

These tests patch the loop-pipeline construction points (no real engine run,
no LLM) and assert ONLY the defaulting decision: when the caller passes no
hooks, drive_engine feeds ``coordinator.hooks`` to BOTH the HandlerContext and
the PipelineEngine; an explicit ``hooks=`` wins; and a coordinator lacking
``.hooks`` is handled safely.
"""

from __future__ import annotations

import asyncio

from amplifier_module_pipeline_runner.runner import drive_engine


class _FakeOutcome:
    class _Status:
        value = "success"

    status = _Status()
    notes = ""
    failure_reason = None


class _Captor:
    """Captures the ``hooks`` value handed to the patched constructors."""

    def __init__(self) -> None:
        self.handler_hooks: object = "UNSET"
        self.engine_hooks: object = "UNSET"


def _install_patches(monkeypatch, captor: _Captor) -> None:
    import amplifier_module_loop_pipeline.backend as backend_mod
    import amplifier_module_loop_pipeline.context as context_mod
    import amplifier_module_loop_pipeline.engine as engine_mod
    import amplifier_module_loop_pipeline.handlers as handlers_mod

    class FakeContext:
        def set(self, *_args, **_kwargs) -> None:
            pass

    class FakeBackend:
        def __init__(self, **_kwargs) -> None:
            pass

    class FakeHandlerContext:
        def __init__(self, *, backend=None, hooks=None, interviewer=None) -> None:
            captor.handler_hooks = hooks

    class FakeHandlerRegistry:
        def __init__(self, _ctx) -> None:
            pass

    class FakeEngine:
        def __init__(self, *, graph=None, context=None, handler_registry=None, logs_root=None, hooks=None) -> None:
            captor.engine_hooks = hooks

        async def run(self):
            return _FakeOutcome()

    monkeypatch.setattr(context_mod, "PipelineContext", FakeContext)
    monkeypatch.setattr(backend_mod, "AmplifierBackend", FakeBackend)
    monkeypatch.setattr(handlers_mod, "HandlerContext", FakeHandlerContext)
    monkeypatch.setattr(handlers_mod, "HandlerRegistry", FakeHandlerRegistry)
    monkeypatch.setattr(engine_mod, "PipelineEngine", FakeEngine)


class _CoordinatorWithHooks:
    def __init__(self, hooks_obj) -> None:
        self.hooks = hooks_obj


class _CoordinatorNoHooks:
    """A bare stub coordinator that does not expose ``.hooks``."""


def _run(monkeypatch, coordinator, hooks):
    captor = _Captor()
    _install_patches(monkeypatch, captor)
    # Pass a non-str graph so parse_dot is skipped; transform/validate off so
    # apply_transforms/validate_or_raise are never called.
    asyncio.run(
        drive_engine(
            object(),  # graph sentinel (not a str)
            coordinator,
            logs_root="/tmp/does-not-matter",
            hooks=hooks,
            transform=False,
            validate=False,
        )
    )
    return captor


def test_defaults_to_coordinator_hooks_when_none(monkeypatch):
    sentinel = object()
    coord = _CoordinatorWithHooks(sentinel)
    captor = _run(monkeypatch, coord, hooks=None)
    assert captor.engine_hooks is sentinel
    assert captor.handler_hooks is sentinel


def test_explicit_hooks_override_coordinator(monkeypatch):
    explicit = object()
    coord = _CoordinatorWithHooks(object())  # different object
    captor = _run(monkeypatch, coord, hooks=explicit)
    assert captor.engine_hooks is explicit
    assert captor.handler_hooks is explicit


def test_coordinator_without_hooks_is_safe(monkeypatch):
    coord = _CoordinatorNoHooks()
    captor = _run(monkeypatch, coord, hooks=None)
    # Both sinks received a real decision (None), not the "UNSET" sentinel --
    # i.e. the defaulting path ran and getattr(..., None) kept it safe.
    assert captor.engine_hooks is None
    assert captor.handler_hooks is None
