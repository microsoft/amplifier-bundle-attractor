"""Tests for clone-spawn concurrency regression fix.

Background
----------
After the parallel-branch isolation fix (Move 1+2), each branch gets a fresh
``AmplifierBackend`` clone via ``backend.clone()``.  The previous implementation
reset ``_spawn_fn = None`` and ``_spawn_checked = False`` on each clone, so N
concurrent branches all attempted to resolve ``session.spawn`` independently
under ``asyncio.gather``.

When those concurrent first-resolution calls collide on the shared coordinator,
some branches receive ``None`` back (or raise) and fall to the tool-loop
fallback path, setting ``session_id = None`` and never writing to
``_session_pool``.  The result: ``fidelity=full`` is silently broken for every
parallel branch — the DTU e2e run showed both seed nodes with ``session_id:
None`` when branches ran concurrently but a valid ``session_id`` on sequential
execution.

Fix: "resolve once, then share"
--------------------------------
1. ``backend.clone()`` inherits ``_spawn_fn`` and ``_spawn_checked`` from the
   parent instead of resetting them.  The capability is a stateless function
   from the shared ``_coordinator`` — sharing the resolved reference is as safe
   as the clone already sharing ``_coordinator`` itself.

2. A new ``ensure_spawn_resolved()`` method resolves spawn in-place on the
   parent (idempotent, synchronous — mirrors the lazy check in ``run()``).

3. ``PipelineEngine.clone_for_branch()`` calls ``ensure_spawn_resolved()`` on
   the parent backend before ``handler_registry.clone_for_branch()`` creates
   any clones, so all clones inherit an already-resolved ``_spawn_fn`` and
   ``_spawn_checked = True``.

TDD status
----------
RED on the isolation branch before this commit:
  - ``test_clone_inherits_resolved_spawn_fn``:  clone() resets to None/False
  - ``test_clone_inherits_unresolved_spawn_state``:  passes trivially (no change needed)
  - ``test_ensure_spawn_resolved_exists_and_resolves``:  AttributeError (method absent)
  - ``test_ensure_spawn_resolved_is_idempotent``:  AttributeError (method absent)
  - ``test_get_capability_called_exactly_once_across_n_clones``:  N+1 calls, not 1

GREEN after this commit (all five).

Spec coverage: FID-001 (fidelity=full session reuse), PAR-001 (parallel isolation).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from amplifier_module_loop_pipeline.backend import AmplifierBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(
    *, resolved: bool = False
) -> tuple[AmplifierBackend, MagicMock, MagicMock]:
    """Return (backend, coordinator, spawn_fn).

    If *resolved* is True, the backend's spawn capability is pre-resolved so
    ``_spawn_fn`` is set and ``_spawn_checked`` is True.
    """
    coordinator = MagicMock()
    spawn_fn = MagicMock()
    coordinator.get_capability.return_value = spawn_fn
    backend = AmplifierBackend(coordinator=coordinator, profiles={"anthropic": "p"})
    if resolved:
        # Simulate resolution that would happen during the first run() call.
        backend._spawn_fn = spawn_fn
        backend._spawn_checked = True
    return backend, coordinator, spawn_fn


# ---------------------------------------------------------------------------
# S1 — clone() inherits parent's resolved spawn state
# ---------------------------------------------------------------------------


class TestCloneInheritsSpawnResolution:
    """After fix: clone() inherits _spawn_fn and _spawn_checked from parent."""

    def test_clone_inherits_resolved_spawn_fn(self):
        """Clone must inherit _spawn_fn when parent has already resolved.

        RED on current branch: clone() resets _spawn_fn=None/_spawn_checked=False.
        GREEN after fix: clone inherits the parent's non-None _spawn_fn and True flag.
        """
        parent, _, spawn_fn = _make_backend(resolved=True)

        clone = parent.clone()

        assert clone._spawn_checked is True, (
            "Clone must inherit _spawn_checked=True from a resolved parent — "
            "otherwise the clone performs a redundant (and concurrent) first-resolution"
        )
        assert clone._spawn_fn is spawn_fn, (
            "Clone must inherit parent's _spawn_fn (same stateless capability reference)"
        )

    def test_clone_mirrors_unresolved_state_when_parent_unchecked(self):
        """Clone gets _spawn_checked=False when parent has not resolved yet.

        This test is GREEN on the current branch (both reset to False) and must
        STAY GREEN after the fix (both are False, mirrored from unchecked parent).
        """
        parent, _, _ = _make_backend(resolved=False)
        assert parent._spawn_checked is False  # sanity

        clone = parent.clone()

        assert clone._spawn_checked is False
        assert clone._spawn_fn is None

    def test_clone_inherits_none_spawn_fn_when_coordinator_returns_none(self):
        """If coordinator returned None (no spawn capability), clone inherits that too.

        Parent resolves but finds no spawn capability → _spawn_fn stays None,
        _spawn_checked is True.  Clone must inherit that exact state so it knows
        NOT to retry the coordinator.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None  # no spawn available
        parent = AmplifierBackend(coordinator=coordinator, profiles={"a": "b"})
        parent._spawn_checked = True  # resolved, but found nothing
        parent._spawn_fn = None

        clone = parent.clone()

        assert clone._spawn_checked is True, (
            "Clone must inherit _spawn_checked=True even when parent found no spawn fn"
        )
        assert clone._spawn_fn is None, "Clone must inherit None _spawn_fn"


# ---------------------------------------------------------------------------
# S2 — ensure_spawn_resolved() method
# ---------------------------------------------------------------------------


class TestEnsureSpawnResolved:
    """ensure_spawn_resolved() must exist, resolve once, and be idempotent."""

    def test_ensure_spawn_resolved_exists_and_resolves(self):
        """ensure_spawn_resolved() must exist and resolve _spawn_fn on the backend.

        RED on current branch: method does not exist → AttributeError.
        GREEN after fix: method exists, resolves spawn, sets _spawn_checked=True.
        """
        parent, coordinator, spawn_fn = _make_backend(resolved=False)
        assert not parent._spawn_checked  # pre-condition

        parent.ensure_spawn_resolved()

        coordinator.get_capability.assert_called_once_with("session.spawn")
        assert parent._spawn_fn is spawn_fn
        assert parent._spawn_checked is True

    def test_ensure_spawn_resolved_is_idempotent(self):
        """Calling ensure_spawn_resolved() twice must call get_capability only once.

        RED on current branch: method does not exist → AttributeError.
        GREEN after fix: second call is a no-op (already checked).
        """
        parent, coordinator, _ = _make_backend(resolved=False)

        parent.ensure_spawn_resolved()
        parent.ensure_spawn_resolved()  # second call — must be no-op

        assert coordinator.get_capability.call_count == 1, (
            "ensure_spawn_resolved() must be idempotent: "
            "second call must not hit get_capability again"
        )

    def test_ensure_spawn_resolved_handles_no_spawn_capability(self):
        """ensure_spawn_resolved() with no spawn capability sets _spawn_checked=True/_spawn_fn=None.

        Even when coordinator returns None, the method must mark the backend as
        checked so future calls (and inherited clones) skip the coordinator.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        parent = AmplifierBackend(coordinator=coordinator, profiles={"a": "b"})

        parent.ensure_spawn_resolved()

        assert parent._spawn_checked is True
        assert parent._spawn_fn is None


# ---------------------------------------------------------------------------
# S3 — get_capability called exactly once across parent + N clones (fan-out)
# ---------------------------------------------------------------------------


class TestGetCapabilityCalledOnceAcrossFanOut:
    """Simulated parallel fan-out: resolve once on parent, zero re-resolves on clones."""

    def test_get_capability_called_exactly_once_across_n_clones(self):
        """Simulated fan-out: get_capability must be called EXACTLY ONCE total.

        This is the core correctness property of the fix:
          - parent.ensure_spawn_resolved() resolves once
          - N clones inherit _spawn_fn/_spawn_checked=True from the parent
          - no clone executes the lazy-check block (``if not _spawn_checked``)
          - total get_capability calls: 1 (parent only)

        RED on current branch:
          - ensure_spawn_resolved() doesn't exist → AttributeError, OR
          - clone() resets _spawn_checked=False → each of 4 clones re-resolves
            → 5 calls instead of 1.

        GREEN after fix: exactly 1 call.
        """
        coordinator = MagicMock()
        spawn_fn = MagicMock()
        coordinator.get_capability.return_value = spawn_fn

        parent = AmplifierBackend(coordinator=coordinator, profiles={"a": "b"})

        # Step 1: resolve on parent (simulating clone_for_branch pre-resolve)
        parent.ensure_spawn_resolved()
        assert coordinator.get_capability.call_count == 1

        # Step 2: create N clones (simulating N parallel branches)
        n = 4
        clones = [parent.clone() for _ in range(n)]

        # Step 3: simulate each clone's lazy-check on first run() entry
        # (the ``if not self._spawn_checked`` guard in AmplifierBackend.run)
        for c in clones:
            if not c._spawn_checked:
                # This branch must NEVER execute after the fix
                c._coordinator.get_capability("session.spawn")
                c._spawn_checked = True

        # Verify: total calls across parent + all clones must be exactly 1
        total_calls = coordinator.get_capability.call_count
        assert total_calls == 1, (
            f"get_capability must be called exactly once (on parent only). "
            f"Got {total_calls} calls. "
            f"Branch clones must NOT perform a concurrent first-resolution — "
            f"they must inherit the parent's already-resolved _spawn_fn."
        )

    def test_all_clones_share_same_spawn_fn_reference(self):
        """Every clone in the fan-out must point to the same spawn_fn object.

        The spawn fn is stateless (sourced from shared coordinator); sharing
        the reference is safe.  This also verifies that clones don't each
        acquire a different instance via separate get_capability calls.

        RED on current branch: clones reset _spawn_fn=None (not the parent fn).
        GREEN after fix: all clones inherit the parent's resolved _spawn_fn.
        """
        parent, _, spawn_fn = _make_backend(resolved=True)

        clones = [parent.clone() for _ in range(5)]

        for i, c in enumerate(clones):
            assert c._spawn_fn is spawn_fn, (
                f"Clone {i}: _spawn_fn must be the same object as parent's spawn_fn. "
                f"Got {c._spawn_fn!r} (expected {spawn_fn!r})"
            )
