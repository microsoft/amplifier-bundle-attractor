"""Checkpointing for pipeline execution — write side and resume load side.

After every node execution, the engine saves a JSON checkpoint so the
pipeline can recover from crashes. The checkpoint captures the current
node, completed nodes (as a list), context snapshot, retry counters,
and execution logs.

**Is the checkpoint a resume marker?**  Yes — but only through the explicit
resume entry point (``attractor resume`` / ``resume_pipeline()`` /
``PipelineEngine.resume()``), which reads it via
:func:`load_checkpoint_for_resume`.  A fresh ``PipelineEngine.run()`` never
reads a checkpoint back: there is no call path from ``run()`` to any loader in
this module, so a stale or foreign ``checkpoint.json`` sitting where a fresh
run can see it is inert to that run *by construction*, not by a guard that
could misfire.  (An identity guard evaluated implicitly on every start is
exactly what made the pre-#66 implementation poison fresh runs.)

Graph-level idempotency (checking STATE.yaml, skipping completed work — see
``examples/pipelines/12-graph-resume.dot``) remains an independent, fully
supported pattern owned by individual node handlers.  Engine resume and
graph-owned skip-through coexist; neither disables the other.

Schema v2 (spec §5.3 superset):
    The six §5.3 fields keep their exact names and shapes
    (``current_node``, ``completed_nodes``, ``context``, ``timestamp``,
    ``node_retries``, ``logs``) at the §5.6 location
    ``{logs_root}/checkpoint.json``.  v2 adds ``schema_version``,
    ``run_state``, ``node_outcomes``, ``engine_state`` and ``graph``
    (fingerprint + embedded DOT source) so that a resume is self-contained and
    restores state rather than replaying completed work.

Spec coverage: CHKP-001–006, Section 5.3 (incl. "Resume behavior" rules 1–6)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .graph import Graph

#: Current checkpoint schema version.  Resume refuses anything else — a v1
#: checkpoint (no ``schema_version`` key) genuinely lacks the state a resume
#: needs, and a future version may mean something this engine cannot honor.
SCHEMA_VERSION: int = 2

#: ``run_state`` values.  A run that returned its final Outcome is flipped to
#: ``completed``; resuming it is refused (there is nothing left to continue).
RUN_STATE_IN_FLIGHT: str = "in_flight"
RUN_STATE_COMPLETED: str = "completed"

#: Statuses accepted in a serialized ``node_outcomes`` entry (StageStatus
#: values).  Kept as a literal set so the structural rung can validate a
#: checkpoint without depending on enum import order.
_VALID_STATUSES: frozenset[str] = frozenset(
    {"success", "partial_success", "retry", "fail", "skipped"}
)


class CheckpointFormatError(ValueError):
    """Raised when a checkpoint file cannot be parsed into a valid Checkpoint."""


# ---------------------------------------------------------------------------
# Resume validation error family (spec §5.3 rule 1 — the load ladder)
# ---------------------------------------------------------------------------


class CheckpointResumeError(Exception):
    """Base class for every refusal to resume from a checkpoint.

    Message discipline for every subclass: name *what* failed, quote the
    *offending value*, and state *what to do*.  A resume never falls back to a
    fresh start — a silent restart-from-scratch presented as a successful
    resume is the failure mode this family exists to prevent.
    """


class CheckpointMissingError(CheckpointResumeError):
    """Rung 1: no ``checkpoint.json`` at the given run directory."""


class CheckpointCorruptError(CheckpointResumeError):
    """Rung 2: the file exists but is not parseable as a checkpoint."""


class CheckpointSchemaVersionError(CheckpointResumeError):
    """Rung 3: ``schema_version`` is absent or not resumable by this engine."""


class CheckpointAlreadyCompletedError(CheckpointResumeError):
    """Rung 4: the run this checkpoint belongs to already finished."""


class CheckpointGraphMismatchError(CheckpointResumeError):
    """Rung 5: the graph supplied is not the graph that wrote the checkpoint."""


class CheckpointStructureError(CheckpointResumeError):
    """Rung 6: the checkpoint is structurally invalid against the graph."""


def fingerprint_dot_source(dot_source: str) -> str:
    """Return the stable identity of a DOT source: ``sha256:<hex>``.

    Covers the whole source text, so any structural drift between the run and
    the resume (renamed node, changed condition, added edge) changes the
    fingerprint.  This is the *only* identity check in the module, and it is
    reachable only from the explicit resume path (see the module docstring).
    """
    digest = hashlib.sha256((dot_source or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass
class Checkpoint:
    """Serializable snapshot of pipeline execution state.

    Saved after each node completes.  Enables crash recovery and — through the
    explicit resume entry point — resume.

    Spec Section 5.3: Checkpoint model.
    The spec's fields keep their exact names and shapes: ``current_node``,
    ``completed_nodes`` (List<String>), ``context_values`` (stored under the
    JSON key ``context``), ``node_retries``, ``logs``, ``timestamp``.
    Everything below ``logs`` is the v2 superset (see module docstring).
    """

    current_node: str
    completed_nodes: list[str]  # spec: List<String>
    context_snapshot: dict[str, Any]
    timestamp: str
    node_retries: dict[str, int] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)  # L-7: execution log entries

    # -- v2 superset --------------------------------------------------------

    #: Schema identity.  Written always; checked only on the resume path.
    schema_version: int = SCHEMA_VERSION

    #: ``in_flight`` while the run is walking the graph; ``completed`` once
    #: ``run()``/``resume()`` returned its final Outcome.
    run_state: str = RUN_STATE_IN_FLIGHT

    #: Routing/gating subset of each completed node's Outcome, keyed by node
    #: id: ``{status, preferred_label, suggested_next_ids, is_explicit,
    #: failure_reason, notes}``.  Required by goal-gate re-evaluation
    #: (``is_explicit`` — EXTENSIONS §25 fail-closed contract), by
    #: ``feedback_from=`` collection on a loop_restart edge, and by the single
    #: resume-hop edge selection (spec §5.3 rule 5), which must run against the
    #: last completed node's REAL outcome — never a reconstructed one.
    node_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Engine counters the main loop carries across nodes: ``iteration_count``,
    #: ``node_execution_counts``, ``goal_gate_retries``,
    #: ``failure_routing_retries``, ``steps``.  Restoring them is what keeps
    #: bounded-loop budgets from refreshing on every kill+resume.
    engine_state: dict[str, Any] = field(default_factory=dict)

    #: Graph identity + the DOT source itself, making a resume self-contained
    #: (``manifest.json`` does not carry the source):
    #: ``{"fingerprint": ..., "dot_source": ...}``.
    graph: dict[str, Any] = field(default_factory=dict)

    @property
    def graph_fingerprint(self) -> str:
        """The recorded graph fingerprint, or ``""`` when absent (v1)."""
        return str(self.graph.get("fingerprint", ""))

    @property
    def graph_dot_source(self) -> str:
        """The recorded DOT source, or ``""`` when absent (v1)."""
        return str(self.graph.get("dot_source", ""))


def save_checkpoint(checkpoint: Checkpoint, path: str) -> None:
    """Write checkpoint to a JSON file.

    The JSON is indented for human readability during debugging.

    Spec Section 5.3: Checkpoint.save(path).  The six spec keys are written
    first and unchanged; the v2 keys are additive (see module docstring).
    """
    data: dict[str, Any] = {
        # -- spec §5.3 fields, names and shapes unchanged -------------------
        "current_node": checkpoint.current_node,
        "completed_nodes": checkpoint.completed_nodes,
        "context": checkpoint.context_snapshot,
        "timestamp": checkpoint.timestamp,
        "node_retries": checkpoint.node_retries,
        "logs": checkpoint.logs,  # L-7
        # -- v2 superset ----------------------------------------------------
        "schema_version": checkpoint.schema_version,
        "run_state": checkpoint.run_state,
        "node_outcomes": checkpoint.node_outcomes,
        "engine_state": checkpoint.engine_state,
        "graph": checkpoint.graph,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_checkpoint(path: str) -> Checkpoint:
    """Read checkpoint from a JSON file.

    Raises FileNotFoundError if the file does not exist.

    Handles both the new list format and legacy dict format for
    completed_nodes (graceful forward migration — dict keys extracted as the
    node list).  v1 checkpoints (no ``schema_version``) load with
    ``schema_version=1`` and empty v2 fields; the resume ladder refuses them
    (see :func:`load_checkpoint_for_resume`).

    This is the raw loader — it performs NO resume validation.  Resume callers
    must use :func:`load_checkpoint_for_resume`.

    Spec Section 5.3: Checkpoint.load(path).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise CheckpointFormatError(
            f"checkpoint at {path} is not a JSON object (found {type(data).__name__})"
        )

    # Handle legacy dict format for completed_nodes gracefully
    raw_cn = data.get("completed_nodes", [])
    if isinstance(raw_cn, dict):
        completed_nodes: list[str] = list(raw_cn.keys())
    else:
        completed_nodes = list(raw_cn)

    graph_block = data.get("graph", {})
    if not isinstance(graph_block, dict):
        graph_block = {}

    return Checkpoint(
        current_node=data["current_node"],
        completed_nodes=completed_nodes,
        context_snapshot=data.get("context", {}),
        timestamp=data.get("timestamp", ""),
        node_retries=data.get("node_retries", {}),
        logs=data.get("logs", []),  # L-7
        # v2 superset — absent keys mean a pre-resume (v1) checkpoint.
        schema_version=int(data.get("schema_version", 1)),
        run_state=data.get("run_state", RUN_STATE_IN_FLIGHT),
        node_outcomes=data.get("node_outcomes") or {},
        engine_state=data.get("engine_state") or {},
        graph=graph_block,
    )


# ---------------------------------------------------------------------------
# Resume validation ladder (spec §5.3 rule 1)
#
# Order IS the contract.  Every rung fails loud with a named cause and an
# actionable remedy; NO rung ever falls back to a fresh start.  Nothing in the
# engine mutates until every rung has passed.
#
#   1. exists   2. parses   3. version   4. liveness
#   5. identity 6. structure  ->  then restore
#
# Rungs 1–5 are :func:`load_checkpoint_for_resume` (they need only the file and
# the DOT source).  Rung 6 is :func:`verify_checkpoint_structure`, which needs
# the parsed+transformed Graph the resume will actually execute — and that
# graph can only be built AFTER rung 5 has proven the source's identity.  Both
# live here; the engine never validates.
# ---------------------------------------------------------------------------


def load_checkpoint_for_resume(
    path: str,
    *,
    dot_source: str | None = None,
) -> Checkpoint:
    """Load a checkpoint for an explicit resume, running ladder rungs 1–5.

    Args:
        path: Path to ``{logs_root}/checkpoint.json`` (spec §5.3 rule 1 —
            exactly this location, no search).
        dot_source: Optional DOT source supplied by the caller (``--dot-file``).
            When given it MUST fingerprint-match the checkpoint's embedded
            source; when omitted the checkpoint's own embedded source is
            authoritative and the identity rung is satisfied by construction.

    Returns:
        The validated Checkpoint.  ``checkpoint.graph_dot_source`` is the DOT
        source to execute.

    Raises:
        CheckpointMissingError: rung 1 — no checkpoint file.
        CheckpointCorruptError: rung 2 — unparseable / not a checkpoint.
        CheckpointSchemaVersionError: rung 3 — not schema v2.
        CheckpointAlreadyCompletedError: rung 4 — the run already finished.
        CheckpointStructureError: rung 5 — v2 but no embedded graph identity.
        CheckpointGraphMismatchError: rung 5 — supplied graph is a different graph.
    """
    # -- Rung 1: exists -----------------------------------------------------
    if not os.path.isfile(path):
        raise CheckpointMissingError(
            f"nothing to resume: no checkpoint at {path}. "
            "A resumable run writes checkpoint.json into its logs_root after "
            "every node completion — pass the run directory of an interrupted "
            "run (the one printed as 'logs=' when it started)."
        )

    # -- Rung 2: parses -----------------------------------------------------
    try:
        checkpoint = load_checkpoint(path)
    except json.JSONDecodeError as exc:
        raise CheckpointCorruptError(
            f"corrupted checkpoint at {path}: not valid JSON ({exc}). "
            "The file was most likely truncated by the interruption. There is "
            "no partial-resume path — re-run the pipeline from the start."
        ) from exc
    except (CheckpointFormatError, KeyError, TypeError, ValueError) as exc:
        raise CheckpointCorruptError(
            f"corrupted checkpoint at {path}: {type(exc).__name__}: {exc}. "
            "The file parsed as JSON but is not a checkpoint (a required field "
            "such as 'current_node' is missing or malformed). Re-run the "
            "pipeline from the start."
        ) from exc

    # -- Rung 3: version ----------------------------------------------------
    if checkpoint.schema_version != SCHEMA_VERSION:
        if checkpoint.schema_version < SCHEMA_VERSION:
            detail = (
                "v1 checkpoints are pre-resume observability records: they do "
                "not carry the node outcomes, engine counters, or graph "
                "identity a resume requires. Re-run the pipeline from the start."
            )
        else:
            detail = (
                "this checkpoint was written by a newer engine than the one "
                "resuming it; upgrade the engine or re-run from the start."
            )
        raise CheckpointSchemaVersionError(
            f"checkpoint schema v{checkpoint.schema_version} is not resumable; "
            f"v{SCHEMA_VERSION} required ({path}). {detail}"
        )

    # -- Rung 4: liveness ---------------------------------------------------
    if checkpoint.run_state != RUN_STATE_IN_FLIGHT:
        final_status = checkpoint.context_snapshot.get("outcome", "unknown")
        raise CheckpointAlreadyCompletedError(
            f"run already completed (run_state={checkpoint.run_state!r}, final "
            f"outcome={final_status!r}); nothing to resume at {path}. "
            "Start a new run with 'attractor run' if you want to execute the "
            "graph again."
        )

    # -- Rung 5: identity ---------------------------------------------------
    recorded_fp = checkpoint.graph_fingerprint
    if not recorded_fp or not checkpoint.graph_dot_source:
        raise CheckpointStructureError(
            f"checkpoint at {path} declares schema v{SCHEMA_VERSION} but "
            "carries no graph identity (graph.fingerprint / graph.dot_source). "
            "It was not written by this engine's checkpoint writer; resume "
            "refused. Re-run the pipeline from the start."
        )
    if dot_source is not None:
        supplied_fp = fingerprint_dot_source(dot_source)
        if supplied_fp != recorded_fp:
            raise CheckpointGraphMismatchError(
                "checkpoint was written by a different graph: checkpoint "
                f"{recorded_fp[:15]}… vs supplied {supplied_fp[:15]}… "
                f"({path}); resume refused (side-effecting nodes must not be "
                "re-applied to a graph they weren't run against). Drop "
                "--dot-file to resume against the graph embedded in the "
                "checkpoint, or start a new run against the changed graph."
            )

    return checkpoint


def verify_checkpoint_structure(checkpoint: Checkpoint, graph: "Graph") -> None:
    """Ladder rung 6: structural validity of a checkpoint against a graph.

    Runs AFTER :func:`load_checkpoint_for_resume` (rungs 1–5) against the
    parsed, transformed graph the resume will actually execute.  Nothing in the
    engine mutates until this returns.

    Raises:
        CheckpointStructureError: naming the offending id or value.
    """
    if checkpoint.current_node not in graph.nodes:
        raise CheckpointStructureError(
            f"checkpoint's current_node {checkpoint.current_node!r} is not a "
            f"node of the graph being resumed (nodes: {sorted(graph.nodes)!r}); "
            "resume refused. The checkpoint does not belong to this graph — "
            "resume the run directory that wrote it."
        )

    unknown = [n for n in checkpoint.completed_nodes if n not in graph.nodes]
    if unknown:
        raise CheckpointStructureError(
            f"checkpoint's completed_nodes contain ids that are not nodes of "
            f"the graph being resumed: {unknown!r} (nodes: "
            f"{sorted(graph.nodes)!r}); resume refused. The checkpoint does not "
            "belong to this graph — resume the run directory that wrote it."
        )

    for node_id, record in checkpoint.node_outcomes.items():
        if node_id not in graph.nodes:
            raise CheckpointStructureError(
                f"checkpoint's node_outcomes reference {node_id!r}, which is "
                f"not a node of the graph being resumed (nodes: "
                f"{sorted(graph.nodes)!r}); resume refused."
            )
        if not isinstance(record, dict):
            raise CheckpointStructureError(
                f"checkpoint's node_outcomes[{node_id!r}] is not an object "
                f"(found {type(record).__name__}); resume refused — the "
                "checkpoint is structurally invalid."
            )
        status = record.get("status")
        if status not in _VALID_STATUSES:
            raise CheckpointStructureError(
                f"checkpoint's node_outcomes[{node_id!r}].status is {status!r}, "
                f"which is not a valid StageStatus "
                f"({sorted(_VALID_STATUSES)!r}); resume refused — the "
                "checkpoint is structurally invalid."
            )
