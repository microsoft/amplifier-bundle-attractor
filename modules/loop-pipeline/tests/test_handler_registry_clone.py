"""Tests for HandlerRegistry.clone_for_branch() — parallel branch isolation.

Verifies that clone_for_branch() produces a registry where:
- The codergen handler has a cloned (different) backend
- Non-codergen handlers are shared (same identity)
- The handlers dict is a separate container
"""

from unittest.mock import MagicMock

from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


def _make_registry_with_mock_backend():
    """Create a HandlerRegistry with a mock backend that supports clone()."""
    mock_backend = MagicMock()
    cloned_backend = MagicMock()
    mock_backend.clone.return_value = cloned_backend
    registry = HandlerRegistry(HandlerContext(backend=mock_backend))
    return registry, mock_backend, cloned_backend


class TestHandlerRegistryCloneForBranch:
    """Tests for HandlerRegistry.clone_for_branch()."""

    def test_codergen_handler_has_cloned_backend(self):
        """Cloned registry's codergen handler uses a different backend object."""
        registry, mock_backend, cloned_backend = _make_registry_with_mock_backend()

        branch_registry = registry.clone_for_branch()

        original_codergen = registry._handlers["codergen"]
        cloned_codergen = branch_registry._handlers["codergen"]

        # The codergen handler itself should be a different object
        assert cloned_codergen is not original_codergen
        # The cloned handler's backend should be the clone, not the original
        assert cloned_codergen._backend is cloned_backend
        assert cloned_codergen._backend is not mock_backend
        # clone() was called on the original backend
        mock_backend.clone.assert_called_once()

    def test_non_codergen_handlers_are_shared(self):
        """Non-codergen/pipeline handlers are the same objects (shared identity)."""
        registry, _, _ = _make_registry_with_mock_backend()

        branch_registry = registry.clone_for_branch()

        # codergen and pipeline are replaced with fresh instances in clone_for_branch
        cloned_types = {"codergen", "pipeline"}
        for key in registry._handlers:
            if key in cloned_types:
                continue
            assert branch_registry._handlers[key] is registry._handlers[key], (
                f"Handler '{key}' should be shared (same identity)"
            )

    def test_handlers_dict_is_independent(self):
        """Cloned registry's _handlers dict is a different container."""
        registry, _, _ = _make_registry_with_mock_backend()

        branch_registry = registry.clone_for_branch()

        assert branch_registry._handlers is not registry._handlers

    def test_clone_returns_handler_registry_instance(self):
        """clone_for_branch() returns a HandlerRegistry."""
        registry, _, _ = _make_registry_with_mock_backend()

        branch_registry = registry.clone_for_branch()

        assert isinstance(branch_registry, HandlerRegistry)
