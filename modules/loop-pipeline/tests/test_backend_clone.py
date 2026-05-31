"""Tests for AmplifierBackend.clone() — parallel branch isolation.

Verifies that clone() produces a new backend with:
- Fresh mutable state (session_pool, completed_nodes, etc.)
- Shared immutable refs (coordinator, profiles, provider, etc.)
- Independent tool dict (shallow copy)
"""

from unittest.mock import MagicMock

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


def _make_backend(**overrides):
    """Create an AmplifierBackend with mock dependencies."""
    defaults = {
        "coordinator": MagicMock(),
        "profiles": {"anthropic": "test-profile"},
        "provider": MagicMock(),
        "tools": {"tool_a": MagicMock(), "tool_b": MagicMock()},
        "unified_client": MagicMock(),
        "hooks": MagicMock(),
    }
    defaults.update(overrides)
    return AmplifierBackend(**defaults)


class TestAmplifierBackendClone:
    """Tests for AmplifierBackend.clone()."""

    def test_clone_has_empty_mutable_state(self):
        """Cloned backend starts with fresh mutable state.

        _session_pool, _completed_nodes, and _last_node_id are always fresh.
        _spawn_fn and _spawn_checked are INHERITED from the parent (not reset)
        so branch clones never perform a concurrent first-resolution of
        session.spawn under asyncio.gather.
        """
        original = _make_backend()
        # Dirty the original's mutable state
        original._session_pool["thread-1"] = "session-abc"
        original._completed_nodes["node-1"] = Outcome(status=StageStatus.SUCCESS)
        original._last_node_id = "node-1"
        original._spawn_checked = True

        clone = original.clone()

        assert clone._session_pool == {}
        assert clone._completed_nodes == {}
        assert clone._last_node_id is None
        # _spawn_checked is inherited (not reset) to prevent concurrent
        # first-resolution; see test_backend_clone_spawn_inheritance.py
        assert clone._spawn_checked is True

    def test_clone_shares_immutable_refs(self):
        """Cloned backend shares the same immutable reference objects."""
        original = _make_backend()

        clone = original.clone()

        assert clone._coordinator is original._coordinator
        assert clone._profiles is original._profiles
        assert clone._provider is original._provider
        assert clone._unified_client is original._unified_client
        assert clone._hooks is original._hooks

    def test_clone_has_independent_tools_dict(self):
        """Cloned backend's tools dict is a shallow copy — independent container."""
        original = _make_backend()

        clone = original.clone()

        # Dict is a different object
        assert clone._tools is not original._tools
        # But tool objects inside are shared
        assert clone._tools["tool_a"] is original._tools["tool_a"]
        assert clone._tools["tool_b"] is original._tools["tool_b"]

    def test_mutations_on_clone_dont_affect_original(self):
        """Mutating clone's mutable state doesn't change the original."""
        original = _make_backend()

        clone = original.clone()
        clone._session_pool["thread-x"] = "session-999"
        clone._completed_nodes["node-x"] = Outcome(status=StageStatus.FAIL)
        clone._last_node_id = "node-x"
        clone._spawn_checked = True
        clone._tools["tool_new"] = MagicMock()

        assert original._session_pool == {}
        assert original._completed_nodes == {}
        assert original._last_node_id is None
        assert original._spawn_checked is False
        assert "tool_new" not in original._tools
