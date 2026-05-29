"""RunIdentity — the value object that scopes a pipeline run's persistent state.

A RunIdentity is derived from the graph's structural content. Two runs of
structurally identical graphs share an identity; runs of different graphs do not.

This is the key used by the checkpoint system to detect when a checkpoint was
written by a different graph than the one currently running. On mismatch, the
engine hard-fails rather than silently restarting — preventing side-effecting
nodes (git pushes, branch creates, file writes) from being re-applied to a
pipeline they weren't designed for.

Spec coverage: T2.4 (RunIdentity noun, S3-closure)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph import Graph


@dataclass(frozen=True)
class RunIdentity:
    """The identity that scopes a pipeline run's persistent state.

    Two runs of the same graph share an identity. Two runs of structurally
    different graphs do not. The graph_fingerprint is the only field today;
    the dataclass is open for extension (run_id, started_at) without breaking
    consumers — RunIdentity is a frozen value object, compared by value.

    Immutable and hashable: safe to use as a dict key or set member.
    """

    graph_fingerprint: str

    @classmethod
    def from_graph(cls, graph: Graph) -> RunIdentity:
        """Derive a RunIdentity from a parsed pipeline graph.

        The fingerprint is an MD5 hex digest of the graph's DOT source. When
        dot_source is absent (empty or None), the fingerprint falls back to a
        sorted comma-joined list of node IDs — deterministic across insertion
        order.

        MD5 is used as a non-cryptographic structural fingerprint only.
        Collision resistance at this scale (pipeline graph content) is ample.
        """
        source: str = graph.dot_source or ",".join(sorted(graph.nodes.keys()))
        # noqa: S324 — MD5 used as a non-crypto structural fingerprint
        return cls(
            graph_fingerprint=hashlib.md5(source.encode()).hexdigest()  # noqa: S324
        )
