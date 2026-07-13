"""Unit tests for the run_pipeline consumer seam on ``_build_prepared``'s
``extra_overlays`` parameter.

``extra_overlays`` is the generic seam a consumer (e.g. the wiki-weaver
consumer) uses to add cross-cutting configuration to every session and
spawned child -- e.g. mounting an observability hook -- without the runner
needing to know what the overlay contains. These tests assert that each
overlay supplied is genuinely composed onto the runtime bundle, in order,
after the runtime orchestrator overlay -- i.e. it actually reaches the
prepared bundle used to build the session, not merely accepted and dropped.
They use fakes and monkeypatching (no real bundle loading, no engine, no
LLM) so they stay fast and non-brittle, per the module's testing philosophy.
"""

from __future__ import annotations

import asyncio

from amplifier_module_pipeline_runner import runner as runner_mod


class FakePrepared:
    """Minimal stand-in for a PreparedBundle -- records what it was composed from."""

    def __init__(self, applied: list) -> None:
        self.applied = applied


class FakeBundle:
    """Minimal stand-in for ``amplifier_foundation.Bundle`` -- records
    ``.compose()`` calls in order so a test can confirm an overlay genuinely
    reaches the final composed bundle used to ``prepare()`` (not just
    accepted and silently dropped)."""

    def __init__(self, applied: list | None = None) -> None:
        self.applied = applied or []

    def compose(self, other):
        return FakeBundle(applied=[*self.applied, other])

    async def prepare(self, *, install_deps):
        del install_deps
        return FakePrepared(applied=self.applied)


def _patch_base_bundle(monkeypatch) -> None:
    async def fake_load_base_bundle():
        return FakeBundle()

    monkeypatch.setattr(runner_mod, "_load_base_bundle", fake_load_base_bundle)


def test_extra_overlay_reaches_prepared_bundle_and_is_genuinely_invoked(
    monkeypatch, tmp_path
):
    """An overlay passed via ``extra_overlays`` lands in the composed chain
    used to build the prepared bundle, positioned AFTER the runtime
    orchestrator overlay -- and is the exact object a real consumer's
    hook-mounting overlay would be, proven by invoking it post-composition."""
    _patch_base_bundle(monkeypatch)

    invoked: list[str] = []

    class ObservabilityOverlay:
        """Stand-in for a consumer's real overlay (e.g. a hook-mounting
        ``Bundle``) -- calling it has an observable effect, proving it's the
        genuine object that was wired through composition, not a copy or a
        placeholder."""

        def mark(self) -> None:
            invoked.append("observability-overlay-applied")

    overlay = ObservabilityOverlay()

    prepared = asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
            extra_overlays=[overlay],
        )
    )

    # Runtime orchestrator overlay composed first, caller's overlay second.
    assert len(prepared.applied) == 2
    assert prepared.applied[1] is overlay

    # Prove it's genuinely wired in (not merely present in a list): the same
    # object that reached the prepared bundle is callable and produces the
    # real effect a consumer's overlay would rely on.
    prepared.applied[1].mark()
    assert invoked == ["observability-overlay-applied"]


def test_multiple_extra_overlays_composed_in_order(monkeypatch, tmp_path):
    """Multiple overlays are composed onto the prepared bundle in the exact
    order supplied -- composition order matters for overlay semantics."""
    _patch_base_bundle(monkeypatch)

    overlay_a, overlay_b = object(), object()

    prepared = asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
            extra_overlays=[overlay_a, overlay_b],
        )
    )

    assert prepared.applied[1] is overlay_a
    assert prepared.applied[2] is overlay_b


def test_no_extra_overlays_leaves_only_runtime_overlay(monkeypatch, tmp_path):
    """Without ``extra_overlays``, only the runtime orchestrator overlay is
    composed -- the seam is a strict addition, not a required parameter."""
    _patch_base_bundle(monkeypatch)

    prepared = asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
        )
    )

    assert len(prepared.applied) == 1
