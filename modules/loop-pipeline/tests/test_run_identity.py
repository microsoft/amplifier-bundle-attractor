"""Tests for RunIdentity value object.

RunIdentity scopes a pipeline run to a specific graph structure.
Two runs of the same graph share an identity; structurally different
graphs do not. Identity is a frozen (immutable, hashable) value object.

Spec coverage: T2.4 (RunIdentity noun)
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.run_identity import RunIdentity


class TestRunIdentityEquality:
    """Identical graphs produce the same identity; different graphs differ."""

    def test_identical_dot_source_produces_identical_identity(self):
        """Same graph.dot_source → same graph_fingerprint."""
        from unittest.mock import MagicMock

        graph = MagicMock()
        graph.dot_source = "digraph { start -> end }"
        graph.nodes = {}

        id1 = RunIdentity.from_graph(graph)
        id2 = RunIdentity.from_graph(graph)

        assert id1 == id2

    def test_different_dot_source_produces_different_identity(self):
        """Structurally different graphs produce different fingerprints."""
        from unittest.mock import MagicMock

        g1 = MagicMock()
        g1.dot_source = "digraph { start -> end }"
        g1.nodes = {}

        g2 = MagicMock()
        g2.dot_source = "digraph { start -> middle -> end }"
        g2.nodes = {}

        id1 = RunIdentity.from_graph(g1)
        id2 = RunIdentity.from_graph(g2)

        assert id1 != id2

    def test_empty_dot_source_falls_back_to_sorted_node_names(self):
        """When dot_source is empty, identity is derived from sorted node keys."""
        from unittest.mock import MagicMock

        g1 = MagicMock()
        g1.dot_source = ""
        g1.nodes = {"b": MagicMock(), "a": MagicMock()}

        g2 = MagicMock()
        g2.dot_source = ""
        g2.nodes = {"a": MagicMock(), "b": MagicMock()}

        # Order of dict construction doesn't matter — both sort to "a,b"
        id1 = RunIdentity.from_graph(g1)
        id2 = RunIdentity.from_graph(g2)

        assert id1 == id2

    def test_none_dot_source_falls_back_to_node_names(self):
        """dot_source=None is treated as empty (falsy) and falls back to nodes."""
        from unittest.mock import MagicMock

        g = MagicMock()
        g.dot_source = None
        g.nodes = {"a": MagicMock(), "b": MagicMock()}

        # Should not raise; should derive identity from node names
        identity = RunIdentity.from_graph(g)
        assert identity.graph_fingerprint  # non-empty hex digest


class TestRunIdentityImmutability:
    """RunIdentity is a frozen dataclass — hashable and immutable."""

    def test_identity_is_hashable(self):
        """Frozen dataclass: identity can be used as dict key or in sets."""
        identity = RunIdentity(graph_fingerprint="abc123")
        # Should not raise
        d = {identity: "value"}
        assert d[identity] == "value"
        assert identity in {identity}

    def test_identity_is_immutable(self):
        """Frozen dataclass: mutation raises FrozenInstanceError."""
        identity = RunIdentity(graph_fingerprint="abc123")
        with pytest.raises(
            Exception
        ):  # FrozenInstanceError is a subclass of AttributeError
            identity.graph_fingerprint = "changed"  # type: ignore[misc]

    def test_identity_equality_is_value_based(self):
        """Two RunIdentity instances with the same fingerprint are equal."""
        id1 = RunIdentity(graph_fingerprint="deadbeef")
        id2 = RunIdentity(graph_fingerprint="deadbeef")
        assert id1 == id2
        assert hash(id1) == hash(id2)

    def test_identity_inequality_on_different_fingerprint(self):
        """Two RunIdentity instances with different fingerprints are unequal."""
        id1 = RunIdentity(graph_fingerprint="abc")
        id2 = RunIdentity(graph_fingerprint="xyz")
        assert id1 != id2


class TestRunIdentityFingerprint:
    """graph_fingerprint is a hex digest — consistent and deterministic."""

    def test_fingerprint_is_hex_string(self):
        """Fingerprint should be a non-empty hex string (MD5 = 32 chars)."""
        from unittest.mock import MagicMock

        graph = MagicMock()
        graph.dot_source = "digraph { a -> b }"
        graph.nodes = {}

        identity = RunIdentity.from_graph(graph)
        assert isinstance(identity.graph_fingerprint, str)
        assert len(identity.graph_fingerprint) == 32  # MD5 hex digest
        # All characters are hex
        assert all(c in "0123456789abcdef" for c in identity.graph_fingerprint)

    def test_fingerprint_is_deterministic(self):
        """Multiple calls with the same source produce the same fingerprint."""
        from unittest.mock import MagicMock

        graph = MagicMock()
        graph.dot_source = "digraph { a -> b -> c }"
        graph.nodes = {}

        ids = [RunIdentity.from_graph(graph) for _ in range(5)]
        assert len(set(id_.graph_fingerprint for id_ in ids)) == 1
