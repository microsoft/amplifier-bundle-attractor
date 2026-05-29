"""Checkpointing and resume for pipeline execution.

After every node execution, the engine saves a JSON checkpoint so the
pipeline can resume after crashes. The checkpoint captures the current
node, completed nodes with outcomes, context snapshot, retry counters,
and execution logs.

A RunIdentity (T2.4) is embedded in every checkpoint so the engine can
detect when a checkpoint was written by a structurally different graph.
On mismatch the engine raises CheckpointMismatchError — a hard-fail that
prevents side-effecting nodes from being re-applied to the wrong pipeline.

Spec coverage: CHKP-001-006, Section 5.3, T2.4
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .run_identity import RunIdentity


class CheckpointMismatchError(RuntimeError):
    """Raised when a loaded checkpoint's identity does not match the current graph.

    The engine refuses to resume rather than silently restarting from scratch,
    which would re-execute side-effecting nodes (git pushes, branch creates,
    file writes) on a pipeline that produced a different graph.

    The error message includes:
    - The checkpoint file path
    - The first 12 hex digits of both fingerprints
    - Remediation instructions (delete the file to start fresh)
    """


class CheckpointFormatError(ValueError):
    """Raised when a checkpoint file cannot be parsed into a valid Checkpoint."""


@dataclass
class Checkpoint:
    """Serializable snapshot of pipeline execution state.

    Saved after each node completes. Enables crash recovery and resume.

    Spec Section 5.3: Checkpoint model.
    """

    current_node: str
    completed_nodes: dict[str, str]  # node_id -> outcome status
    context_snapshot: dict[str, Any]
    node_outcomes: dict[str, dict[str, Any]]  # node_id -> serialized Outcome
    timestamp: str
    node_retries: dict[str, int] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)  # L-7: execution log entries
    identity: RunIdentity | None = (
        None  # T2.4: graph identity for stale-checkpoint guard
    )

    @property
    def completed_node_list(self) -> list[str]:
        """Return completed node IDs as a list (L-8: spec compliance).

        The spec defines ``completed_nodes`` as ``List<String>``.
        This property provides that view while keeping the internal
        dict representation for backward compatibility.
        """
        return list(self.completed_nodes.keys())


def save_checkpoint(checkpoint: Checkpoint, path: str) -> None:
    """Write checkpoint to a JSON file.

    The JSON is indented for human readability during debugging.
    If the checkpoint carries a RunIdentity, it is serialized as a
    nested ``{"graph_fingerprint": "..."}`` dict under the ``"identity"`` key.

    Spec Section 5.3: Checkpoint.save(path).
    """
    data: dict[str, Any] = {
        "current_node": checkpoint.current_node,
        "completed_nodes": checkpoint.completed_nodes,
        "context": checkpoint.context_snapshot,
        "node_outcomes": checkpoint.node_outcomes,
        "timestamp": checkpoint.timestamp,
        "node_retries": checkpoint.node_retries,
        "logs": checkpoint.logs,  # L-7
    }
    if checkpoint.identity is not None:
        data["identity"] = {"graph_fingerprint": checkpoint.identity.graph_fingerprint}

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_checkpoint(path: str) -> Checkpoint:
    """Read checkpoint from a JSON file.

    Raises FileNotFoundError if the file does not exist.

    Identity is reconstructed from three possible formats (in priority order):

    1. **T2.4 format** (``"identity": {"graph_fingerprint": "..."}``):
       RunIdentity reconstructed directly.

    2. **Wave-0 #252 format** (``"graph_fingerprint": "..."`` at top level):
       Legacy format from the Wave-0 graph-fingerprint fix.  The fingerprint
       is promoted into a RunIdentity so it participates in the same mismatch
       guard as new checkpoints.

    3. **Pre-#252 format** (neither key present):
       Old checkpoints without any identity.  ``identity`` is set to ``None``;
       the engine discards these as part of the one-time migration (no hard-fail,
       just a fresh run).

    Spec Section 5.3: Checkpoint.load(path).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Reconstruct RunIdentity from whichever format is present.
    identity: RunIdentity | None = None

    if "identity" in data and isinstance(data["identity"], dict):
        # T2.4 format: {"identity": {"graph_fingerprint": "..."}}
        fp = data["identity"].get("graph_fingerprint", "")
        if fp:
            identity = RunIdentity(graph_fingerprint=fp)
    elif "graph_fingerprint" in data and data["graph_fingerprint"]:
        # Wave-0 #252 format: top-level "graph_fingerprint" string
        identity = RunIdentity(graph_fingerprint=str(data["graph_fingerprint"]))
    # else: pre-#252 legacy → identity stays None

    return Checkpoint(
        current_node=data["current_node"],
        completed_nodes=data.get("completed_nodes", {}),
        context_snapshot=data.get("context", {}),
        node_outcomes=data.get("node_outcomes", {}),
        timestamp=data.get("timestamp", ""),
        node_retries=data.get("node_retries", {}),
        logs=data.get("logs", []),  # L-7
        identity=identity,
    )
