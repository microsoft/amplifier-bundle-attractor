"""Checkpointing for pipeline execution.

After every node execution, the engine saves a JSON checkpoint so the
pipeline can recover from crashes. The checkpoint captures the current
node, completed nodes (as a list), context snapshot, retry counters,
and execution logs.

The engine always starts from the graph's start node — the checkpoint
is an observability record, not a resume marker. Graph-level idempotency
(checking STATE.yaml, skipping completed work) is the responsibility of
individual node handlers.

Spec coverage: CHKP-001–006, Section 5.3
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class CheckpointFormatError(ValueError):
    """Raised when a checkpoint file cannot be parsed into a valid Checkpoint."""


@dataclass
class Checkpoint:
    """Serializable snapshot of pipeline execution state.

    Saved after each node completes. Enables crash recovery observability.

    Spec Section 5.3: Checkpoint model.
    Fields match the spec exactly: current_node, completed_nodes (List<String>),
    context_values (stored as context_snapshot), node_retries, logs.
    """

    current_node: str
    completed_nodes: list[str]  # spec: List<String>
    context_snapshot: dict[str, Any]
    timestamp: str
    node_retries: dict[str, int] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)  # L-7: execution log entries


def save_checkpoint(checkpoint: Checkpoint, path: str) -> None:
    """Write checkpoint to a JSON file.

    The JSON is indented for human readability during debugging.

    Spec Section 5.3: Checkpoint.save(path).
    """
    data: dict[str, Any] = {
        "current_node": checkpoint.current_node,
        "completed_nodes": checkpoint.completed_nodes,
        "context": checkpoint.context_snapshot,
        "timestamp": checkpoint.timestamp,
        "node_retries": checkpoint.node_retries,
        "logs": checkpoint.logs,  # L-7
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_checkpoint(path: str) -> Checkpoint:
    """Read checkpoint from a JSON file.

    Raises FileNotFoundError if the file does not exist.

    Handles both the new list format and legacy dict format for
    completed_nodes (graceful forward migration — dict keys extracted
    as the node list).

    Spec Section 5.3: Checkpoint.load(path).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Handle legacy dict format for completed_nodes gracefully
    raw_cn = data.get("completed_nodes", [])
    if isinstance(raw_cn, dict):
        completed_nodes: list[str] = list(raw_cn.keys())
    else:
        completed_nodes = list(raw_cn)

    return Checkpoint(
        current_node=data["current_node"],
        completed_nodes=completed_nodes,
        context_snapshot=data.get("context", {}),
        timestamp=data.get("timestamp", ""),
        node_retries=data.get("node_retries", {}),
        logs=data.get("logs", []),  # L-7
    )
