"""Pipeline execution engine — the graph-walking core.

Traverses a parsed DOT graph from the start node to an exit node,
executing handlers for each node and selecting edges based on outcomes.
This is the heart of the Attractor pipeline orchestrator.

Spec coverage: EXEC-001–018, CHKP-004–006, EVT-001–008, DIR-001, STAT-001–004,
               Sections 3.2, 5.6, 9.6.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .checkpoint import (
    RUN_STATE_COMPLETED,
    RUN_STATE_IN_FLIGHT,
    Checkpoint,
    fingerprint_dot_source,
    save_checkpoint,
)
from .context import PipelineContext
from .edge_selection import select_edge
from .feedback import collect_and_inject_feedback
from .fidelity import RESUME_FIDELITY_CAP_KEY, resolve_fidelity
from .graph import Graph, Node, resolve_bool_attr
from .handlers import HandlerRegistry
from .handlers.pipeline import ChildDotResolutionError
from .must_write import check_must_write
from .node_outputs import SUBSTITUTABLE_ATTRS, build_output_table
from .outcome import Outcome, StageStatus
from .pipeline_events import (
    PIPELINE_CHECKPOINT,
    PIPELINE_COMPLETE,
    PIPELINE_EDGE_SELECTED,
    PIPELINE_ERROR,
    PIPELINE_GOAL_GATE_CHECK,
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_CONTRACT_VIOLATION,
    PIPELINE_NODE_SKIPPED,
    PIPELINE_NODE_START,
    PIPELINE_RESUME,
    PIPELINE_RESUME_FIDELITY_DEGRADE,
    PIPELINE_START,
)
from .retry import RetryPolicy, execute_with_retry
from .substitution import extract_refs

logger = logging.getLogger(__name__)


def _get_engine_provenance() -> dict:
    """Return engine version/commit provenance for manifest stamping.

    Reads install-time metadata only — no shell-out to git at runtime.
    Strategy (in order of discriminating power):

    1. PEP 610 ``direct_url.json``: for git installs uv records the resolved
       commit hash here.  Editable installs (``uv sync`` in-tree) may omit the
       ``requested_revision`` / ``commit_id`` fields — we stamp ``"unknown"``
       rather than guessing.
    2. ``importlib.metadata.version()``: the static ``pyproject.toml`` version
       string (``"0.1.0"`` today).  Low-information but honest.

    Returns a dict with keys ``engine_version`` and ``engine_commit``.
    Values are ``"unknown"`` when identity cannot be determined without
    fabricating — a fabricated provenance field is worse than an honest gap.
    """
    import importlib.metadata as _meta

    engine_version: str = "unknown"
    engine_commit: str = "unknown"

    try:
        engine_version = _meta.version("amplifier-module-loop-pipeline")
    except _meta.PackageNotFoundError:
        pass

    try:
        # PEP 610: uv writes direct_url.json for git/url installs.
        # Path: <site-packages>/amplifier_module_loop_pipeline-*.dist-info/direct_url.json
        dist = _meta.distribution("amplifier-module-loop-pipeline")
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            direct_url = json.loads(direct_url_text)
            vcs_info = direct_url.get("vcs_info", {})
            commit_id = vcs_info.get("commit_id", "")
            if commit_id:
                engine_commit = commit_id
    except Exception as exc:  # noqa: BLE001 — provenance is best-effort; never crash
        logger.debug("engine commit provenance unavailable: %s", exc)

    return {"engine_version": engine_version, "engine_commit": engine_commit}


def _outcome_from_checkpoint_record(record: dict[str, Any], retries: int) -> Outcome:
    """Rehydrate a completed node's Outcome from its checkpoint record.

    Only the routing/gating subset round-trips (see
    ``PipelineEngine._serialize_node_outcomes``).  ``attempt_count`` is
    reconstructed from the sibling ``node_retries`` entry (spec §5.3 rule 4),
    which is why the two are written together.
    """
    return Outcome(
        status=StageStatus(record["status"]),
        preferred_label=record.get("preferred_label"),
        suggested_next_ids=record.get("suggested_next_ids"),
        notes=record.get("notes"),
        failure_reason=record.get("failure_reason"),
        is_explicit=bool(record.get("is_explicit", False)),
        attempt_count=int(retries) + 1,
    )


class PipelineEngine:
    """Graph-walking execution engine.

    Walks the graph from start to exit, executing node handlers and
    selecting edges deterministically based on outcomes and context.

    Saves a checkpoint after each node execution for crash observability.
    The engine always starts from the graph's start node — graph-level
    idempotency (checking state files, skipping completed work) is the
    responsibility of individual node handlers, not the engine.
    """

    # Maximum number of goal-gate-driven retries before giving up.
    # Prevents infinite loops when a gate's retry_target never satisfies.
    _MAX_GOAL_GATE_RETRIES: int = 50

    def __init__(
        self,
        graph: Graph,
        context: PipelineContext,
        handler_registry: HandlerRegistry,
        logs_root: str,
        hooks: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.graph = graph
        self.context = context
        self.handler_registry = handler_registry
        self.logs_root = logs_root
        self.hooks = hooks
        self._cancel_event = cancel_event
        self.node_outcomes: dict[str, Outcome] = {}
        self.completed_nodes: list[str] = []
        self.iteration_count: int = 0
        self._node_execution_counts: dict[
            str, int
        ] = {}  # per-node execution count (graph-level visits)
        self._checkpoint_path: str | None = os.path.join(logs_root, "checkpoint.json")
        # Last checkpoint payload written, retained so the terminal run_state
        # flip can rewrite it without reading the file back (see
        # _mark_run_completed).  Never read on any fresh-run path.
        self._last_checkpoint: Checkpoint | None = None
        self._graph_identity_cache: dict[str, Any] | None = None
        # Spec §5.3 rule 6 one-shot: armed by resume(), consumed by the
        # first node actually executed afterwards.  Always False on a
        # fresh run.
        self._resume_fidelity_armed: bool = False
        self.artifact_store = ArtifactStore(base_dir=logs_root)

        # M1/M2 (R12): Output table + failed-outputs propagation table.
        # _output_table maps node_id → set of context keys the node is contracted
        # to produce; built once at graph-load from outputs= attrs + inference.
        # failed_outputs maps context-key → producing-node-id for all keys that
        # came from a failed or skipped predecessor.  Cleared on retry (CR-3).
        self._output_table: dict[str, frozenset[str]] = build_output_table(graph)
        self.failed_outputs: dict[str, str] = {}

        # S5: Branch-clone marker and discriminator.
        # Set by clone_for_branch() to prevent run() from being called on a
        # branch engine.  _branch_id is threaded into events emitted from this
        # engine so concurrent-branch logs are sortable (S4).
        self._is_branch_clone: bool = False
        self._branch_id: str | None = None

    def clone_for_branch(self, *, context: PipelineContext) -> "PipelineEngine":
        """Create a branch-isolated clone of this engine for parallel execution.

        Each concurrent parallel branch must have its own engine so that
        ``run_subgraph`` uses an isolated ``handler_registry`` (and therefore
        an isolated backend ``_thread_transcripts`` / ``_completed_nodes``).

        Split table (critic-corrected, implement exactly):
          ISOLATED per branch (cloned):
            context          — caller must pass ``context.clone()``
            handler_registry — ``clone_for_branch()`` gives fresh backend state
            node_outcomes    — auto-fresh by __init__
            completed_nodes  — auto-fresh by __init__
            iteration_count  — auto-fresh by __init__
            _node_execution_counts — auto-fresh by __init__
            failed_outputs   — auto-fresh by __init__

          SHARED by reference (immutable or shared semantics):
            graph            — immutable post-load
            logs_root        — shared str, no mutable state
            hooks            — events surface on one stream
            _cancel_event    — cancel propagates across all branches
            artifact_store   — CRITICAL (C1): share the L-12 lock and
                               cross-branch artifact visibility
            _output_table    — pure function of graph, avoid re-derivation

          DISABLED on clones:
            _checkpoint_path — None; S5 guard prevents run() on branch clones

        Args:
            context: Branch-isolated context (caller must pass ``context.clone()``).

        Returns:
            A new ``PipelineEngine`` marked as a branch clone.

        Raises:
            RuntimeError: If called on an engine that is itself a branch clone
                (nested cloning is not permitted; only the top-level engine clones).
        """
        # Resolve spawn capability on the parent backend BEFORE cloning so that
        # branch clones inherit an already-resolved _spawn_fn instead of
        # performing a concurrent first-resolution under asyncio.gather.
        # Without this, N parallel branches each receive a fresh clone with
        # _spawn_fn=None and all race to call get_capability simultaneously,
        # causing some branches to fall back to the tool loop (session_id: None)
        # and silently break fidelity=full.
        # Use getattr so that backends without the method (e.g. test stubs) are
        # skipped safely — the hasattr + call pattern isn't type-safe on
        # get_backend()'s "object | None" return type.
        parent_backend = self.handler_registry.get_backend()
        ensure_fn = getattr(parent_backend, "ensure_spawn_resolved", None)
        if ensure_fn is not None:
            ensure_fn()

        clone = PipelineEngine(
            graph=self.graph,
            context=context,
            handler_registry=self.handler_registry.clone_for_branch(),
            logs_root=self.logs_root,
            hooks=self.hooks,
            cancel_event=self._cancel_event,
        )
        # C1: share the parent's ArtifactStore (preserves L-12 lock and visibility)
        clone.artifact_store = self.artifact_store
        # Avoid wasteful re-derivation (output table is a pure function of graph)
        clone._output_table = self._output_table
        # S5: disable checkpointing on branch clones
        clone._checkpoint_path = None
        # S4 + S5: mark as branch clone and assign a discriminator for log sorting
        clone._is_branch_clone = True
        clone._branch_id = f"branch@{id(clone):#x}"
        return clone

    def _check_cancelled(self) -> bool:
        """Check if cancellation has been requested via the cancel event."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    async def run(self, goal: str | None = None) -> Outcome:
        """Execute the pipeline from start to exit.

        Always starts from the graph's start node. Saves a checkpoint after
        each node execution. Graph-level idempotency (skipping already-done
        work) is the responsibility of individual node handlers, not the engine.

        Args:
            goal: Optional goal string to set in context. If not provided,
                uses the graph-level goal attribute.

        Returns:
            The final Outcome of the pipeline run.

        Raises:
            RuntimeError: If called on a branch-clone engine (S5 guard).
                Branch engines are driven by ``run_subgraph`` only; calling
                ``run()`` on them would silently attempt checkpoint
                save against the shared ``logs_root``, corrupting
                the parent engine's checkpoint state.
        """
        # S5: Branch-clone guard — run() must never be called on a branch engine.
        # Branch engines are driven exclusively via run_subgraph().
        # Silent checkpoint resume/save on a branch would corrupt the parent's
        # checkpoint, producing wrong output non-deterministically.
        if self._is_branch_clone:
            raise RuntimeError(
                "run() must not be called on a branch-clone engine; "
                "branch engines are driven by run_subgraph() only. "
                "Create a top-level PipelineEngine for full pipeline execution."
            )

        pipeline_start_time = time.monotonic()

        # Initialize context with graph attributes
        self._initialize_context(goal)

        # Note: transforms (variable expansion, stylesheet) are applied by
        # PipelineOrchestrator.execute() between parse and validate, before
        # the engine is constructed.  Do NOT re-apply here.

        # Create run directory structure (manifest, artifacts/)
        self._write_manifest(goal)

        # Emit pipeline:start
        await self._emit(
            PIPELINE_START,
            {
                "graph_name": self.graph.name,
                "node_count": len(self.graph.nodes),
                "edge_count": len(self.graph.edges),
                "goal": self.graph.goal or goal or "",
                "dot_source": self.graph.dot_source,
            },
        )

        # Find the start node — engine always starts from Start
        current_node = self._find_start_node()

        # Spec §5.3 rule 5 ("determine the next node to execute") is the only
        # thing resume needs that a fresh start does not; everything else in
        # the walk is identical.  The walk therefore lives in _run_loop(), and
        # run()/resume() are siblings that differ only in what they hand it.
        # run() is a flag-free fresh path by construction: nothing here reads a
        # checkpoint, so a stale or foreign checkpoint.json is inert to it.
        outcome = await self._run_loop(
            current_node,
            pipeline_start_time=pipeline_start_time,
        )
        # Terminal state write: a finished run is not resumable (ladder rung 4).
        self._mark_run_completed()
        return outcome

    async def resume(
        self,
        checkpoint: Checkpoint,
        *,
        context_overrides: dict[str, Any] | None = None,
    ) -> Outcome:
        """Continue an interrupted run from its checkpoint — spec §5.3.

        The sibling of :meth:`run`, not a mode of it.  ``run()`` has no call
        path to any checkpoint loader, so a stale or foreign ``checkpoint.json``
        cannot affect a fresh run *by construction* — that inertness is a
        property of the call graph, not a conditional that could misfire.

        The checkpoint MUST already have passed the whole validation ladder
        (``checkpoint.load_checkpoint_for_resume`` rungs 1–5 plus
        ``verify_checkpoint_structure`` rung 6).  By the time this runs the
        checkpoint is proven; the engine never validates, and nothing here
        mutates state that the ladder has not already vouched for.

        Spec §5.3 "Resume behavior" mapping:
          rule 2 — context restored verbatim from the snapshot (NOT
                   ``_initialize_context()``, which would re-seed ``iteration``
                   to 0 and re-mirror graph attrs the snapshot already holds).
          rule 3 — ``completed_nodes`` restored.  "Skip already-finished work"
                   is achieved POSITIONALLY: the walk re-enters after the last
                   completed node, so completed nodes are never visited and
                   trivially cannot re-execute.  No per-node skip check, no
                   replay scan.
          rule 4 — ``node_retries`` and the engine's own counters restored
                   rather than reset.
          rule 5 — the next node is DETERMINED at resume time by re-running
                   edge selection exactly once from the recorded outcome (see
                   the resume-hop branch in ``_run_loop``).
          rule 6 — the one-shot ``full`` -> ``summary:high`` degrade is armed
                   here and applied to the first node actually executed.

        Args:
            checkpoint: A ladder-validated v2 checkpoint.
            context_overrides: Process-level wiring that cannot be serialized
                and therefore belongs to the resuming invocation rather than
                the crashed one (e.g. ``context.target_dir`` from ``--cwd``).
                Applied AFTER the snapshot restore, so it wins.

        Returns:
            The final Outcome of the resumed pipeline run.

        Raises:
            RuntimeError: If called on a branch-clone engine (S5 guard) or if
                the checkpoint's ``current_node`` is not in the graph (which
                the ladder's structural rung must already have refused).
        """
        # S5: same guard as run() — branch engines never own a checkpoint.
        if self._is_branch_clone:
            raise RuntimeError(
                "resume() must not be called on a branch-clone engine; "
                "branch engines are driven by run_subgraph() only and never "
                "checkpoint (see clone_for_branch)."
            )

        if checkpoint.current_node not in self.graph.nodes:
            # Defence in depth: the ladder's rung 6 owns this refusal and
            # produces the actionable message.  Reaching here means a caller
            # skipped the ladder.
            raise RuntimeError(
                f"resume() called with an unvalidated checkpoint: current_node "
                f"{checkpoint.current_node!r} is not in the graph. Callers must "
                "use checkpoint.load_checkpoint_for_resume() + "
                "verify_checkpoint_structure() first."
            )

        pipeline_start_time = time.monotonic()

        # -- rule 2: restore context verbatim -------------------------------
        self.context.update(dict(checkpoint.context_snapshot))
        if context_overrides:
            self.context.update(dict(context_overrides))
        # Restore the run log so it accumulates across the process boundary
        # instead of restarting (the next checkpoint's `logs` stays complete).
        for entry in checkpoint.logs:
            self.context.append_log(entry)

        # -- rule 3: restore completed work ---------------------------------
        self.completed_nodes = list(checkpoint.completed_nodes)
        self.node_outcomes = {
            node_id: _outcome_from_checkpoint_record(
                record, checkpoint.node_retries.get(node_id, 0)
            )
            for node_id, record in checkpoint.node_outcomes.items()
        }

        # -- rule 4: restore retry counters and engine budgets --------------
        engine_state = checkpoint.engine_state or {}
        self.iteration_count = int(engine_state.get("iteration_count", 0) or 0)
        self._node_execution_counts = {
            str(k): int(v)
            for k, v in (engine_state.get("node_execution_counts") or {}).items()
        }
        goal_gate_retries = int(engine_state.get("goal_gate_retries", 0) or 0)
        failure_routing_retries = int(
            engine_state.get("failure_routing_retries", 0) or 0
        )
        steps = int(engine_state.get("steps", 0) or 0)

        # failed_outputs is DERIVED, not persisted: it is a pure function of
        # the graph's output table and which completed nodes failed/skipped.
        # One source of truth, smaller schema.
        for node_id, outcome in self.node_outcomes.items():
            if outcome.status in (StageStatus.FAIL, StageStatus.SKIPPED):
                self._populate_failed_outputs(node_id)

        # -- rule 6: arm the one-shot fidelity degrade ----------------------
        # Armed unconditionally; whether it fires depends on what the first
        # executed node RESOLVES to (see _apply_resume_fidelity_degrade).
        self._resume_fidelity_armed = True

        self._record_resume_in_manifest(checkpoint)

        self.context.append_log(
            f"resume: continuing from checkpoint after node "
            f"'{checkpoint.current_node}' ({len(self.completed_nodes)} node(s) "
            f"already complete, iteration {self.iteration_count})"
        )

        await self._emit(
            PIPELINE_RESUME,
            {
                "checkpoint_node": checkpoint.current_node,
                "completed_count": len(self.completed_nodes),
                "iteration_count": self.iteration_count,
                "fidelity_degrade_armed": True,
            },
        )

        # -- rule 5: hand the walk the recorded outcome, once ---------------
        # A terminal-node checkpoint has no recorded outcome for current_node
        # (the terminal save happens before the goal-gate check, and exit nodes
        # never execute a handler).  resume_outcome=None then, and the loop's
        # terminal branch re-runs the gate check over the restored outcomes —
        # no special case.
        entry_node = self.graph.nodes[checkpoint.current_node]
        resume_outcome = self.node_outcomes.get(checkpoint.current_node)

        outcome = await self._run_loop(
            entry_node,
            pipeline_start_time=pipeline_start_time,
            goal_gate_retries=goal_gate_retries,
            failure_routing_retries=failure_routing_retries,
            steps=steps,
            resume_outcome=resume_outcome,
        )
        self._mark_run_completed()
        return outcome

    async def _apply_resume_fidelity_degrade(self, node: Node) -> None:
        """Spec §5.3 rule 6 — cap the FIRST node executed after a resume.

        ``fidelity=full`` continuity is an in-memory transcript replayed into a
        fresh spawn (``AmplifierBackend._thread_transcripts``).  A killed
        process loses it unrecoverably, so without this the first resumed
        ``full`` hop would silently spawn with empty history — "proceeds as if
        nothing degraded", which is precisely what must not happen.

        Trigger, stated openly: we degrade when the first resumed hop itself
        RESOLVES to ``full``.  The spec's literal trigger is "if the PREVIOUS
        node used full"; the two readings differ only where the literal one
        does nothing useful — if this hop resolves to any non-``full`` mode it
        already gets a fresh session with a preamble, so substituting
        ``summary:high`` would be a no-op or an override of an explicit author
        choice.  Ours additionally covers the lost-transcript case the literal
        trigger misses (previous non-full -> next full).

        One hop only, exactly as written: the arm is consumed by the first
        executed node whatever it resolves to, so later nodes "may use full
        fidelity again" untouched.

        The durable record is produced HERE, engine-side, not in the backend:
        a stub backend supplied through the public injection seam must still
        leave the run's own records showing the degrade.
        """
        if not self._resume_fidelity_armed:
            return
        self._resume_fidelity_armed = False  # one-shot, consumed either way

        # incoming_edge is not threaded through this stack (CodergenHandler
        # passes incoming_edge=None to the backend), so resolve exactly what
        # the backend will resolve.
        fidelity = resolve_fidelity(node, None, self.graph)
        if fidelity != "full":
            return

        self.context.set(RESUME_FIDELITY_CAP_KEY, "summary:high")
        self.context.append_log(
            f"resume: fidelity degraded full->summary:high for node "
            f"'{node.id}' (spec §5.3 rule 6 — in-memory LLM sessions cannot "
            f"be serialized)"
        )
        await self._emit(
            PIPELINE_RESUME_FIDELITY_DEGRADE,
            {"node_id": node.id, "from": "full", "to": "summary:high"},
        )

    def _record_resume_in_manifest(self, checkpoint: Checkpoint) -> None:
        """Append this resume to ``manifest.json``'s ``resumes`` list.

        Additive read-modify-write: ``start_time`` and provenance survive, and
        ``_write_manifest()`` is deliberately NOT called (it would stamp a new
        ``start_time`` over the interrupted run's).  Spec §5.6: a resumed run
        is the SAME execution continued, in the same run directory.
        """
        manifest_path = os.path.join(self.logs_root, "manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            if not isinstance(manifest, dict):
                return
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("resume: manifest not updatable (%s)", exc)
            return

        resumes = manifest.get("resumes")
        if not isinstance(resumes, list):
            resumes = []
        resumes.append(
            {
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "from_node": checkpoint.current_node,
                "completed_count": len(checkpoint.completed_nodes),
            }
        )
        manifest["resumes"] = resumes
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except OSError as exc:  # pragma: no cover - best effort provenance
            logger.debug("resume: manifest not writable (%s)", exc)

    async def _run_loop(
        self,
        current_node: Node,
        *,
        pipeline_start_time: float,
        goal_gate_retries: int = 0,
        failure_routing_retries: int = 0,
        steps: int = 0,
        resume_outcome: Outcome | None = None,
    ) -> Outcome:
        """Walk the graph from ``current_node`` until an exit or a failure.

        The shared body of ``run()`` (fresh, entered at the start node with
        zeroed counters) and ``resume()`` (entered at the checkpoint's last
        completed node, with its recorded outcome and restored counters).
        Extracted verbatim so that no traversal, routing, checkpoint, or
        loop_restart behavior can ever diverge between entry points — there is
        exactly one implementation of the walk.

        Args:
            current_node: Node to execute next.
            pipeline_start_time: ``time.monotonic()`` at run start; drives the
                ``max_pipeline_duration`` budget and completion timing.
            goal_gate_retries: Goal-gate retry budget already consumed.
            failure_routing_retries: Failure-routing retry budget already consumed.
            steps: Safety step counter already consumed.
            resume_outcome: Spec §5.3 rule 5 — the recorded outcome of
                ``current_node``, present ONLY on a resume.  When set, the first
                iteration skips execution of that already-completed node and
                goes straight to edge selection (see the resume-hop branch
                below).  ``None`` on every fresh run.

        Returns:
            The final Outcome of the pipeline run.
        """
        # Bound total pipeline steps to prevent infinite loops caused by
        # condition-routing bugs or missing edge guards. Matches the safety
        # bound used in the subgraph runner (run_subgraph).
        max_steps = len(self.graph.nodes) * self._MAX_GOAL_GATE_RETRIES
        while True:
            # Safety step counter — checked first so every loop iteration
            # (including resume-path continues) is counted.
            steps += 1
            if steps > max_steps:
                exceeded_outcome = Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=(
                        f"Pipeline exceeded {max_steps} steps (safety bound): "
                        f"{len(self.graph.nodes)} nodes × {self._MAX_GOAL_GATE_RETRIES}"
                    ),
                )
                logger.error(
                    "Pipeline safety bound exceeded: %d steps (max=%d), terminating",
                    steps,
                    max_steps,
                )
                await self._emit_complete(exceeded_outcome, pipeline_start_time)
                return exceeded_outcome

            # Step 0: Enforce max_pipeline_duration if set on the graph.
            # The DOT parser stores durations as milliseconds.
            if self.graph.max_pipeline_duration:
                elapsed_ms = (time.monotonic() - pipeline_start_time) * 1000
                if elapsed_ms > self.graph.max_pipeline_duration:
                    duration_outcome = Outcome(
                        status=StageStatus.FAIL,
                        notes=(
                            f"Pipeline exceeded max duration of "
                            f"{self.graph.max_pipeline_duration}ms"
                        ),
                        failure_reason="max_pipeline_duration_exceeded",
                    )
                    await self._emit_complete(duration_outcome, pipeline_start_time)
                    return duration_outcome

            # Step 0.5: Check for cancellation (cooperative cross-thread signal)
            if self._check_cancelled():
                cancelled_outcome = Outcome(
                    status=StageStatus.FAIL,
                    notes="Pipeline cancelled by user request",
                    failure_reason="cancelled",
                )
                await self._emit(
                    PIPELINE_COMPLETE,
                    {
                        "status": "cancelled",
                        "total_nodes_executed": len(self.completed_nodes),
                        "duration_ms": (time.monotonic() - pipeline_start_time) * 1000,
                    },
                )
                return cancelled_outcome

            # Step 1: Check for terminal node (exit)
            if current_node.is_exit_node():
                self._save_checkpoint(
                current_node.id,
                goal_gate_retries=goal_gate_retries,
                failure_routing_retries=failure_routing_retries,
                steps=steps,
            )
                await self._emit(
                    PIPELINE_CHECKPOINT,
                    {
                        "node_id": current_node.id,
                        "checkpoint_path": self._checkpoint_path,
                    },
                )
                gate_result = await self._check_goal_gates()

                # All gates satisfied — return final outcome
                if gate_result.status != StageStatus.FAIL:
                    await self._emit_complete(gate_result, pipeline_start_time)
                    return gate_result

                # Unsatisfied gate with retry target — jump there
                if (
                    gate_result.suggested_next_ids
                    and goal_gate_retries < self._MAX_GOAL_GATE_RETRIES
                ):
                    # _check_goal_gates() is currently the sole producer of a
                    # FAIL outcome carrying suggested_next_ids here, and it
                    # only ever sets a single retry_target already verified
                    # to be `in self.graph.nodes` (see below). Defended here
                    # too (coerce + membership check instead of a bare `[0]`
                    # index into a dict) so a type-mismatched or otherwise
                    # unresolvable ID degrades to a diagnosed failure instead
                    # of an uncaught KeyError, matching the same coercion
                    # policy edge_selection.select_edge applies (see its
                    # _coerce_suggested_id) rather than a second, divergent
                    # rule for the same "suggested next ID" concept.
                    from .edge_selection import _coerce_suggested_id

                    raw_retry_id = gate_result.suggested_next_ids[0]
                    retry_node_id = _coerce_suggested_id(raw_retry_id)
                    if retry_node_id is not None and retry_node_id in self.graph.nodes:
                        goal_gate_retries += 1
                        logger.info(
                            "Goal gate unsatisfied, retrying from '%s' (attempt %d)",
                            retry_node_id,
                            goal_gate_retries,
                        )
                        # CR-3 (R12): Reset per-run state so skip-propagation
                        # from attempt N does not block the retried nodes in
                        # attempt N+1.
                        self.completed_nodes.clear()
                        self.node_outcomes.clear()
                        self.failed_outputs.clear()
                        current_node = self.graph.nodes[retry_node_id]
                        continue

                    logger.warning(
                        "Goal gate unsatisfied but the suggested retry ID %r "
                        "did not resolve to a graph node (available: %r); "
                        "failing instead of retrying.",
                        raw_retry_id,
                        list(self.graph.nodes),
                    )

                # No retry target or retries exhausted — fail
                await self._emit_complete(gate_result, pipeline_start_time)
                return gate_result

            # ---- Resume hop: spec §5.3 rule 5, taken exactly once --------
            #
            # The checkpoint records the LAST COMPLETED node and its real
            # outcome; the save happens BEFORE edge selection (Step 5 below),
            # so a resumed run's very first act is to make that one selection
            # itself, from recorded inputs.  Everything the crashed process
            # had already done for this node — handler execution, completion
            # recording, context updates, its checkpoint — is restored state,
            # not work to redo.  So this hop skips straight to Step 5 and then
            # shares every line after it (loop_restart handling, advance).
            #
            # This is NOT replay: no walk from Start, no per-node
            # re-selection, no reconstructed outcomes — one decision, once,
            # from the same inputs the live run had.  (Those three were the
            # ingredients of the pre-#66 "no matching edge from resumed node"
            # crash class.)  The node the crash interrupted re-executes
            # naturally on the NEXT iteration, because it never completed.
            if resume_outcome is not None:
                outcome = resume_outcome
                resume_outcome = None
            else:
                # M2/M3/M4: Eager reference scan — skip if a predecessor failed.
                # Must run BEFORE the handler is invoked so handlers never see
                # missing-because-failed inputs.
                skip_outcome = await self._check_node_skip(current_node)
                if skip_outcome is not None:
                    # Record the skip, populate failed_outputs, and emit events.
                    self.completed_nodes.append(current_node.id)
                    self.node_outcomes[current_node.id] = skip_outcome
                    self._populate_failed_outputs(current_node.id)

                    node_duration_ms = 0.0
                    self._write_node_status(current_node.id, skip_outcome, node_duration_ms)
                    await self._emit(
                        PIPELINE_NODE_COMPLETE,
                        {
                            "node_id": current_node.id,
                            "status": skip_outcome.status.value,
                            "duration_ms": node_duration_ms,
                            "notes": skip_outcome.notes,
                            "failure_reason": skip_outcome.failure_reason,
                            "session_id": None,
                            "execution_index": self._node_execution_counts.get(
                                current_node.id, 0
                            ),
                        },
                    )
                    self._save_checkpoint(
                    current_node.id,
                    goal_gate_retries=goal_gate_retries,
                    failure_routing_retries=failure_routing_retries,
                    steps=steps,
                )
                    await self._emit(
                        PIPELINE_CHECKPOINT,
                        {
                            "node_id": current_node.id,
                            "checkpoint_path": self._checkpoint_path,
                        },
                    )

                    # Route the skipped node: treat SKIPPED like FAIL for edge
                    # selection (conditions matching "outcome=skipped" or
                    # "outcome=fail" win; unconditional edges may still apply).
                    # M4: For runs_on=failure nodes that were skipped because
                    # nothing failed, use a synthetic FAIL-shaped outcome to
                    # keep routing predictable.
                    routing_outcome = Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=skip_outcome.failure_reason,
                        notes=skip_outcome.notes,
                    )
                    edge = select_edge(
                        current_node.id, routing_outcome, self.context, self.graph
                    )
                    if edge is None:
                        # Skip propagation: after select_edge, also try unconditional
                        # edges for skip propagation.  Downstream nodes will be checked
                        # by _check_node_skip and SKIPPED if their dependencies failed.
                        # This preserves skip-chain observability even under the fail-fast
                        # guard (which blocks unconditional edges for FAIL outcomes when
                        # the target has runs_on=success, the default).
                        skip_candidates = [
                            e
                            for e in self.graph.outgoing_edges(current_node.id)
                            if not e.condition
                        ]
                        if skip_candidates:
                            from .edge_selection import _best_by_weight_then_lexical

                            edge = _best_by_weight_then_lexical(skip_candidates)
                    if edge is None:
                        retry_node = self._resolve_failure_retry_target(current_node)
                        if (
                            retry_node is not None
                            and failure_routing_retries < self._MAX_GOAL_GATE_RETRIES
                        ):
                            failure_routing_retries += 1
                            current_node = retry_node
                            continue
                        fail_outcome = self.terminate_pipeline(
                            node_id=current_node.id,
                            upstream_outcome=routing_outcome,
                            termination_reason=(
                                f"No matching edge from skipped node '{current_node.id}'"
                            ),
                        )
                        await self._emit_complete(fail_outcome, pipeline_start_time)
                        return fail_outcome
                    current_node = self.graph.nodes[edge.to_node]
                    continue

                # M4: For runs_on=always or runs_on=failure nodes, resolve
                # missing context references to empty string before the handler.
                runs_on = self._get_runs_on(current_node)
                if runs_on in ("always", "failure"):
                    self._resolve_missing_as_empty(current_node)

                # Step 1.9 (Bug H): Pre-execution requires= file validation.
                # If a node declares ``requires=`` (comma-separated relative paths),
                # every path must exist under context.target_dir (or os.getcwd() as
                # fallback) before the handler runs.  Missing files cause an
                # immediate FAIL with a clear error — the handler is never invoked.
                # This prevents LLM agents from fabricating missing inputs when
                # upstream branches didn't produce their expected artifacts.
                _requires_fail = self._check_requires(current_node)

                # Step 2: Execute node handler with retry policy
                handler = self.handler_registry.get(current_node)
                handler_type = current_node.type or current_node.shape

                # Increment per-node execution count (monotonic across all loop iterations)
                self._node_execution_counts[current_node.id] = (
                    self._node_execution_counts.get(current_node.id, 0) + 1
                )
                execution_index = self._node_execution_counts[current_node.id]

                await self._emit(
                    PIPELINE_NODE_START,
                    {
                        "node_id": current_node.id,
                        "handler_type": handler_type,
                        "attempt": 1,  # within-handler retry counter (backward compat)
                        "execution_index": execution_index,  # NEW — graph-level visit count
                    },
                )

                # Spec §5.3 rule 6: one-shot fidelity cap on the FIRST node
                # executed after a resume.  Inert on every fresh run (the arm
                # is only ever set by resume()).
                await self._apply_resume_fidelity_degrade(current_node)

                node_start_time = time.monotonic()
                node_start_wall = (
                    time.time()
                )  # wall-clock epoch for must_write= mtime comparison
                retry_policy = RetryPolicy.from_node(current_node, self.graph)

                if _requires_fail is not None:
                    # requires= validation failed — short-circuit without calling handler
                    outcome = _requires_fail
                else:
                    # Per-node timeout enforcement: wrap handler execution with
                    # asyncio.timeout when the node declares a timeout attribute.
                    # The DOT parser stores all node-timeout durations as
                    # milliseconds (see dot_parser._DURATION_UNITS): suffixed values
                    # like "300s" via _try_parse_duration, and BARE integers like
                    # "300" via dot_parser._SECONDS_IF_BARE_DURATION_ATTRS (seconds
                    # -> ms at parse time). Divide by 1000 to get seconds for
                    # asyncio.timeout. (Graph-level max_pipeline_duration is also in
                    # ms and is compared against elapsed_ms — do not change that
                    # path.)
                    node_timeout_raw = current_node.timeout
                    if node_timeout_raw:
                        timeout_s = float(node_timeout_raw) / 1000.0
                        try:
                            async with asyncio.timeout(timeout_s):
                                outcome = await execute_with_retry(
                                    handler,
                                    current_node,
                                    self.context,
                                    self.graph,
                                    self.logs_root,
                                    retry_policy,
                                    hooks=self.hooks,
                                    engine=self,
                                )
                        except ChildDotResolutionError as _child_dot_exc:
                            # Issue #200 -- see the sibling handler below.
                            return await self._terminate_child_dot_resolution(
                                node_id=current_node.id,
                                exc=_child_dot_exc,
                                node_start_time=node_start_time,
                                pipeline_start_time=pipeline_start_time,
                                execution_index=execution_index,
                            )
                        except asyncio.TimeoutError:
                            node_duration_ms = (time.monotonic() - node_start_time) * 1000
                            _ap = current_node.attrs.get("allow_partial")
                            _timeout_status = (
                                StageStatus.PARTIAL_SUCCESS
                                if resolve_bool_attr(_ap, "allow_partial")
                                else StageStatus.FAIL
                            )
                            outcome = Outcome(
                                status=_timeout_status,
                                notes=f"Node '{current_node.id}' timed out after {timeout_s}s",
                                failure_reason="timeout",
                            )
                            await self._emit(
                                PIPELINE_NODE_COMPLETE,
                                {
                                    "node_id": current_node.id,
                                    "status": "timeout",
                                    "duration_ms": node_duration_ms,
                                    "notes": outcome.notes,
                                    "failure_reason": outcome.failure_reason,
                                    "session_id": outcome.session_id,
                                    "execution_index": execution_index,  # NEW
                                },
                            )
                    else:
                        try:
                            outcome = await execute_with_retry(
                                handler,
                                current_node,
                                self.context,
                                self.graph,
                                self.logs_root,
                                retry_policy,
                                hooks=self.hooks,
                                engine=self,
                            )
                        except ChildDotResolutionError as _child_dot_exc:
                            # Issue #200: a shape=folder node's dot_file= named
                            # no existing child graph.  That is a child-graph
                            # RESOLUTION fault, and it must never be allowed to
                            # reach Step 5 below: FAIL is fail-fast there, so a
                            # graph with no failure edge would terminate as
                            # `no_matching_edge` / "No matching edge from node
                            # 'X'" -- a routing framing for a missing FILE.
                            # Terminate here instead, in the fault's own class.
                            return await self._terminate_child_dot_resolution(
                                node_id=current_node.id,
                                exc=_child_dot_exc,
                                node_start_time=node_start_time,
                                pipeline_start_time=pipeline_start_time,
                                execution_index=execution_index,
                            )
                node_duration_ms = (time.monotonic() - node_start_time) * 1000

                # Spec §5.3 rule 6 is ONE hop: clear the cap the moment the
                # handler returns, so it can never reach a later node or a
                # checkpoint's context snapshot (Step 4b below).
                #
                # POP, not set(None).  This runs on EVERY node of EVERY run,
                # deliberately — an unconditional clear cannot misfire the way
                # a "was it armed?" guard can, which is what keeps the one-shot
                # airtight.  But a null WRITE would create the key on every
                # fresh run too, putting `resume.fidelity_cap: null` into every
                # fresh checkpoint's context and changing the fresh-run record
                # vs. main (AC-4).  Removing the key keeps the unconditional
                # clear AND leaves no trace: design §6's "can never leak into
                # later hops or checkpoints" holds for fresh and resumed runs
                # alike.  Pinned by test_no_resume_keys_in_a_fresh_checkpoint.
                self.context.pop(RESUME_FIDELITY_CAP_KEY)

                # Step 2.5: Check for cancellation after node execution
                if self._check_cancelled():
                    cancelled_outcome = Outcome(
                        status=StageStatus.FAIL,
                        notes=f"Pipeline cancelled after node '{current_node.id}' completed",
                        failure_reason="cancelled",
                    )
                    await self._emit(
                        PIPELINE_COMPLETE,
                        {
                            "status": "cancelled",
                            "total_nodes_executed": len(self.completed_nodes),
                            "duration_ms": (time.monotonic() - pipeline_start_time) * 1000,
                        },
                    )
                    return cancelled_outcome

                # L-9: auto_status — synthesize SUCCESS only when the handler writes
                # no status (SKIPPED).  Spec §2.6 / Appendix C: "auto-generates a
                # SUCCESS outcome" only applies when *no* status was written.
                # An explicit FAIL or RETRY must pass through unchanged (fail-loud).
                # Accept both bare true and the quoted string "true" (DOT parser
                # returns "true" for quoted attribute values).
                if (
                    resolve_bool_attr(current_node.auto_status, "auto_status")
                    and outcome.status == StageStatus.SKIPPED
                ):
                    logger.debug(
                        "Node '%s' has auto_status=true; promoting SKIPPED to SUCCESS",
                        current_node.id,
                    )
                    # S2 field audit (Outcome has 11 fields; see outcome.py) --
                    # auto_status override:
                    #   status, notes  -> OVERRIDDEN (this IS the promotion)
                    #   context_updates, preferred_label, suggested_next_ids,
                    #   failure_reason, session_id, response_text, failed_step,
                    #   attempt_count  -> CARRIED forward; nothing upstream lost
                    #   is_explicit    -> RESET to default False: this SUCCESS
                    #     was synthesized by policy, not asserted by the node,
                    #     so it must not silently satisfy a goal_gate (which
                    #     requires is_success AND is_explicit; see
                    #     _check_goal_gates()).
                    outcome = Outcome(
                        status=StageStatus.SUCCESS,
                        notes="auto_status override (was skipped)",
                        context_updates=outcome.context_updates,
                        preferred_label=outcome.preferred_label,
                        suggested_next_ids=outcome.suggested_next_ids,
                        failure_reason=outcome.failure_reason,
                        session_id=outcome.session_id,
                        response_text=outcome.response_text,
                        failed_step=outcome.failed_step,
                        attempt_count=outcome.attempt_count,
                    )

                # continue_on_fail: override FAIL to SUCCESS for routing, log the failure
                #
                # NOTE — continue_on_fail and runs_on are NOT orthogonal:
                # - continue_on_fail (per-predecessor-node override) flips FAIL→SUCCESS
                #   BEFORE _populate_failed_outputs runs (see the M2/R12 FAIL check
                #   below, which tests the already-overridden outcome).
                # - runs_on=failure (per-cleanup-node gate) checks the failed_outputs
                #   table populated by _populate_failed_outputs.
                # - A predecessor with continue_on_fail=true that "fails" will appear
                #   SUCCESSFUL to a runs_on=failure cleanup node — the cleanup will NOT
                #   trigger.
                # - This is intentional: continue_on_fail says "treat this as success;
                #   do not surface a failure to the rest of the graph." A cleanup that
                #   wants to fire on the original failure should use runs_on=always
                #   instead of runs_on=failure.
                if (
                    resolve_bool_attr(
                        current_node.attrs.get("continue_on_fail"), "continue_on_fail"
                    )
                    and outcome.status == StageStatus.FAIL
                ):
                    logger.warning(
                        "Node '%s' failed but continue_on_fail=true; overriding to SUCCESS "
                        "(failure: %s)",
                        current_node.id,
                        outcome.failure_reason or outcome.notes or "no reason given",
                    )
                    # S2 field audit (Outcome has 11 fields; see outcome.py) --
                    # continue_on_fail override:
                    #   status         -> OVERRIDDEN (this IS the override)
                    #   notes          -> OVERRIDDEN; failure_reason is folded
                    #     into the new notes text above, so the standalone
                    #     failure_reason field is intentionally RESET to None
                    #     below (not carried) rather than duplicated.
                    #   context_updates, preferred_label, suggested_next_ids,
                    #   session_id, response_text, failed_step, attempt_count
                    #                  -> CARRIED forward; nothing upstream lost
                    #     (failed_step in particular: continue_on_fail
                    #     suppresses the failure for ROUTING only -- it must
                    #     not erase the diagnostic record of what failed).
                    #   is_explicit    -> RESET to default False: same
                    #     reasoning as the auto_status override above -- this
                    #     SUCCESS was synthesized by policy, not asserted by
                    #     the node, so it must not silently satisfy a
                    #     goal_gate (see _check_goal_gates()).
                    outcome = Outcome(
                        status=StageStatus.SUCCESS,
                        notes=(
                            f"continue_on_fail override (was FAIL: "
                            f"{outcome.failure_reason or outcome.notes})"
                        ),
                        context_updates=outcome.context_updates,
                        preferred_label=outcome.preferred_label,
                        suggested_next_ids=outcome.suggested_next_ids,
                        session_id=outcome.session_id,
                        response_text=outcome.response_text,
                        failed_step=outcome.failed_step,
                        attempt_count=outcome.attempt_count,
                    )

                # Step 2.7: must_write= final backstop (EXTENSIONS.md §27).
                # The per-attempt check inside execute_with_retry() already consumed
                # max_retries attempts for violations on completed attempts; this
                # backstop runs AFTER the auto_status and continue_on_fail overrides
                # so that NO override can convert an artifact-contract violation into
                # a silent success.  Running last — not a flag on the override blocks —
                # is what makes the must_write FAIL non-overridable by construction.
                must_write_fail = self._check_must_write(
                    current_node, outcome, node_start_wall
                )
                if must_write_fail is not None:
                    outcome = must_write_fail

                # Step 3: Record completion
                self.completed_nodes.append(current_node.id)
                self.node_outcomes[current_node.id] = outcome
                logger.debug("Node %s completed: %s", current_node.id, outcome.status.value)

                # Step 3b: Write per-node status.json BEFORE emitting so hook bridge can copy it
                self._write_node_status(current_node.id, outcome, node_duration_ms)

                await self._emit(
                    PIPELINE_NODE_COMPLETE,
                    {
                        "node_id": current_node.id,
                        "status": outcome.status.value,
                        "duration_ms": node_duration_ms,
                        "notes": outcome.notes,
                        "failure_reason": outcome.failure_reason,
                        "session_id": outcome.session_id,
                        "execution_index": execution_index,  # NEW — graph-level visit count
                        # Issue 10: structured tool-invocation failure payload.
                        # Populated by ToolHandler on failure; None on success or for
                        # non-tool nodes.  Consumers check for None before reading.
                        "failed_step": outcome.failed_step,
                        # support#379: real attempt count consumed by the retry
                        # ladder (execute_with_retry sets Outcome.attempt_count).
                        # Falls back to 1 for outcomes that never entered the
                        # retry ladder (e.g. the requires= backstop above).
                        "attempt": outcome.attempt_count or 1,
                    },
                )

                # M2 (R12): If the node failed or was skipped, add its declared
                # outputs to failed_outputs so downstream nodes can be skipped.
                # (SKIPPED outcomes from the engine skip-check are handled inline
                # above; this path covers genuine handler-side FAILures.)
                if outcome.status == StageStatus.FAIL:
                    self._populate_failed_outputs(current_node.id)

                # Step 4: Apply context updates from outcome
                if outcome.context_updates:
                    self.context.update(outcome.context_updates)
                self.context.set("outcome", outcome.status.value)
                if outcome.preferred_label:
                    self.context.set("preferred_label", outcome.preferred_label)

                # M3 (R12): Post-success contract violation audit — verify that
                # all declared outputs= keys were actually written to context.
                if outcome.is_success:
                    await self._check_contract_violation(current_node.id, outcome)

                # Step 4b: Save checkpoint after each node
                self._save_checkpoint(
                    current_node.id,
                    goal_gate_retries=goal_gate_retries,
                    failure_routing_retries=failure_routing_retries,
                    steps=steps,
                )
                await self._emit(
                    PIPELINE_CHECKPOINT,
                    {
                        "node_id": current_node.id,
                        "checkpoint_path": self._checkpoint_path,
                    },
                )

            # Step 5: Select next edge — spec §3.3 single-edge selection.
            #
            # T0-4 (spec-conformance restoration): the engine now conforms to
            # attractor-spec.md §3.3 step 1, which prescribes
            # best_by_weight_then_lexical(condition_matched) — ONE edge,
            # deterministic.  The previous multi-match → parallel fan-out path
            # (select_all_matching_edges → _execute_parallel_fan_out) for
            # non-component nodes is retired.  Explicit parallelism remains
            # intact via two spec-sanctioned paths:
            #   - shape=component nodes (handled below, via ParallelHandler)
            #   - shape=parallel fan-out (EXTENSIONS.md #18, handled above)
            #
            # BUG G FIX: Component nodes (shape=component) are handled by
            # ParallelHandler, which fans out ALL outgoing branches internally
            # via run_subgraph and populates parallel.results in context.
            # The engine must NOT re-fan-out after the handler returns — that
            # would execute each branch a second time.  Instead, read ALL
            # outgoing edges directly from the graph, find the shared fan-in
            # node, and route to it.  The FanInHandler reads parallel.results.
            if current_node.shape == "component":
                all_branches = self.graph.outgoing_edges(current_node.id)
                if len(all_branches) > 1:
                    fan_in_node_id = self._find_fan_in_node(
                        [e.to_node for e in all_branches]
                    )
                    if fan_in_node_id is None:
                        fail_outcome = Outcome(
                            status=StageStatus.FAIL,
                            failure_reason=(
                                f"Parallel fan-out from component node "
                                f"'{current_node.id}' has no convergence "
                                f"(fan-in) node — add a shape=tripleoctagon "
                                f"node that all branches lead to"
                            ),
                        )
                        await self._emit_complete(fail_outcome, pipeline_start_time)
                        return fail_outcome
                    logger.info(
                        "Component node '%s' parallel fan-out complete; "
                        "routing to fan-in node '%s'",
                        current_node.id,
                        fan_in_node_id,
                    )
                    current_node = self.graph.nodes[fan_in_node_id]
                    continue
                # Single outgoing edge from component node — fall through to
                # normal single-edge selection below.

            # Single-edge selection: spec §3.3 five-step ladder ending in
            # best_by_weight_then_lexical.  When multiple conditional edges
            # simultaneously match, select_edge() deterministically returns
            # the one with the highest weight (lexical target-id tiebreak) —
            # never a fan-out.
            edge = select_edge(current_node.id, outcome, self.context, self.graph)
            if edge is None:
                # Try failure routing: node/graph retry targets
                retry_node = self._resolve_failure_retry_target(current_node)
                if (
                    retry_node is not None
                    and failure_routing_retries < self._MAX_GOAL_GATE_RETRIES
                ):
                    failure_routing_retries += 1
                    logger.info(
                        "No matching edge from '%s', failure-routing to '%s' "
                        "(attempt %d)",
                        current_node.id,
                        retry_node.id,
                        failure_routing_retries,
                    )
                    # CR-3 (R12): Reset per-run state so skip-propagation from
                    # attempt N does not block retried nodes in attempt N+1.
                    # Mirrors the state clear in the goal-gate retry path above.
                    self.completed_nodes.clear()
                    self.node_outcomes.clear()
                    self.failed_outputs.clear()
                    current_node = retry_node
                    continue

                fail_outcome = self.terminate_pipeline(
                    node_id=current_node.id,
                    upstream_outcome=outcome,
                    termination_reason=self._no_matching_edge_reason(
                        current_node.id, outcome
                    ),
                )
                await self._emit(
                    PIPELINE_ERROR,
                    {
                        "node_id": current_node.id,
                        "error_type": "no_matching_edge",
                        "message": fail_outcome.failure_reason or "",
                    },
                )
                await self._emit_complete(fail_outcome, pipeline_start_time)
                return fail_outcome

            await self._emit(
                PIPELINE_EDGE_SELECTED,
                {
                    "from_node": edge.from_node,
                    "to_node": edge.to_node,
                    "edge_label": edge.label,
                },
            )

            # Step 6: Handle loop_restart edge attribute (NLSpec Section 174)
            if resolve_bool_attr(edge.loop_restart, "loop_restart"):
                self.iteration_count += 1
                iteration_dir = os.path.join(
                    self.logs_root, f"iteration_{self.iteration_count}"
                )
                os.makedirs(iteration_dir, exist_ok=True)
                logger.info(
                    "loop_restart: iteration %d, fresh log dir '%s', "
                    "continuing from '%s'",
                    self.iteration_count,
                    iteration_dir,
                    edge.to_node,
                )
                # Extension #29: collect feedback_from= critique output BEFORE
                # clearing node_outcomes so the critic's output is still
                # available.  The accumulated channel survives the restart
                # because context_updates are intentionally left untouched.
                collect_and_inject_feedback(
                    graph=self.graph,
                    node_outcomes=self.node_outcomes,
                    context=self.context,
                    iteration_count=self.iteration_count,
                    logs_root=self.logs_root,
                )
                # Reset engine state for clean re-execution
                self.completed_nodes.clear()
                self.node_outcomes.clear()
                self.failed_outputs.clear()  # M2 R12: clear skip-propagation table
                goal_gate_retries = 0
                failure_routing_retries = 0
                # Bug fix: clear stale per-cycle routing signal so a prior
                # cycle's preferred_label (e.g. "converged") cannot leak into
                # this restarted cycle's edge-selection when the new cycle's
                # outcome doesn't set its own preferred_label (see
                # docs/designs/... loop_restart context leak). This is the
                # only outcome-derived key resolved by condition evaluation
                # (conditions.py _resolve_key: "outcome" and "preferred_label")
                # that is written conditionally on truthiness elsewhere in
                # this file (Step 4 above, and the run_subgraph mirror), so
                # it is the only one that can go stale here. context_updates
                # are pipeline-declared outputs=, not per-cycle routing
                # signals, and are intentionally left untouched.
                self.context.set("preferred_label", None)
                # Extension #24: update $iteration / $loop_count in context so
                # prompts and tool commands in the new iteration see the current
                # iteration number (not the stale value from the prior cycle).
                self.context.set("iteration", str(self.iteration_count))
                self.context.set("loop_count", str(self.iteration_count))

            # Step 7: Advance to next node
            current_node = self.graph.nodes[edge.to_node]

    async def run_subgraph(
        self,
        start_node_id: str,
        *,
        context: PipelineContext | None = None,
        emit_node_events: bool = True,
    ) -> Outcome:
        """Execute a subgraph starting from the given node.

        Walks from *start_node_id* until an exit node is reached, no
        outgoing edges exist, or the node is not in the graph.

        This is the subgraph runner used by ParallelHandler and
        ManagerLoopHandler to execute branches and child subgraphs.

        support#379 (fix 1): this method emits pipeline:node_start /
        pipeline:node_complete for each node it executes — previously it
        emitted nothing at all, leaving ManagerLoopHandler's in-graph
        subgraph path (and any other direct caller) entirely dark.

        Args:
            start_node_id: Node ID to begin execution from.
            context: Optional isolated context for this subgraph run.
                     If None, uses the engine's main context.
            emit_node_events: Whether to emit pipeline:node_start /
                pipeline:node_complete for each node executed in this
                subgraph. Defaults to True. ParallelHandler passes False
                when calling this from a branch engine, because it already
                emits the equivalent events itself, tagged
                via_parallel=True (see handlers/parallel.py's per-branch
                event contract, documented in this repo's AGENTS.md) — a
                second emission here would double-count every branch node
                in the timeline. Keyword-only so existing callers (and
                test doubles implementing a narrower
                ``run_subgraph(start_node_id, *, context=...)`` override)
                are unaffected by this parameter's addition.

        Returns:
            The final Outcome of the subgraph execution.
        """
        ctx = context if context is not None else self.context

        if start_node_id not in self.graph.nodes:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Subgraph start node '{start_node_id}' not found in graph",
            )

        current_node = self.graph.nodes[start_node_id]
        last_outcome: Outcome | None = None

        # Safety bound to prevent infinite loops
        max_steps = len(self.graph.nodes) * self._MAX_GOAL_GATE_RETRIES

        for _step in range(max_steps):
            # Check cancellation in subgraph runner too
            if self._check_cancelled():
                return Outcome(
                    status=StageStatus.FAIL,
                    notes="Pipeline cancelled during subgraph execution",
                    failure_reason="cancelled",
                )

            # Check for terminal node (exit or fan_in)
            if current_node.is_exit_node() or current_node.shape == "tripleoctagon":
                return last_outcome or Outcome(
                    status=StageStatus.SUCCESS,
                    notes="Subgraph reached terminal node",
                )

            # Execute node handler (no retry policy in subgraph -- parent manages retries)
            handler = self.handler_registry.get(current_node)
            handler_type = current_node.type or current_node.shape

            # Skip start nodes (no-op) -- mirrors run()'s handling; no events
            # emitted for the synthetic start-node no-op, consistent with the
            # top-level run() loop which also does not emit node_start/
            # node_complete for the pipeline's own start node.
            if current_node.is_start_node():
                outcome = Outcome(status=StageStatus.SUCCESS)
            else:
                # support#379 (fix 1): run_subgraph() previously emitted NO
                # events at all -- it is the shared subgraph runner used by
                # ManagerLoopHandler's in-graph path and ParallelHandler's
                # branch bodies, so both execution classes were entirely
                # dark. Mirror the top-level run() loop's node_start /
                # node_complete emission (minus retry-ladder fields, since
                # run_subgraph has no retry policy of its own).
                node_start_time = time.monotonic()
                if emit_node_events:
                    await self._emit(
                        PIPELINE_NODE_START,
                        {
                            "node_id": current_node.id,
                            "handler_type": handler_type,
                            "attempt": 1,
                        },
                    )
                try:
                    outcome = await handler.execute(
                        current_node, ctx, self.graph, self.logs_root, engine=self
                    )
                except ChildDotResolutionError as exc:
                    # Issue #200: keep the child-resolution diagnostic verbatim
                    # here too.  run_subgraph is the shared runner for parallel
                    # branch bodies and the manager-loop in-graph path, and its
                    # generic wrapper below would bury the candidate-path chain
                    # behind "Subgraph node 'X' raised: ...".
                    fail_outcome = Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=str(exc),
                        notes=str(exc),
                    )
                    if emit_node_events:
                        await self._emit(
                            PIPELINE_NODE_COMPLETE,
                            {
                                "node_id": current_node.id,
                                "status": fail_outcome.status.value,
                                "duration_ms": (time.monotonic() - node_start_time)
                                * 1000,
                                "notes": fail_outcome.notes,
                                "failure_reason": fail_outcome.failure_reason,
                                "session_id": None,
                                "attempt": 1,
                            },
                        )
                    return fail_outcome
                except Exception as exc:
                    fail_outcome = Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=f"Subgraph node '{current_node.id}' raised: {exc}",
                    )
                    if emit_node_events:
                        await self._emit(
                            PIPELINE_NODE_COMPLETE,
                            {
                                "node_id": current_node.id,
                                "status": fail_outcome.status.value,
                                "duration_ms": (time.monotonic() - node_start_time)
                                * 1000,
                                "notes": fail_outcome.notes,
                                "failure_reason": fail_outcome.failure_reason,
                                "session_id": None,
                                "attempt": 1,
                            },
                        )
                    return fail_outcome
                if emit_node_events:
                    await self._emit(
                        PIPELINE_NODE_COMPLETE,
                        {
                            "node_id": current_node.id,
                            "status": outcome.status.value,
                            "duration_ms": (time.monotonic() - node_start_time) * 1000,
                            "notes": outcome.notes,
                            "failure_reason": outcome.failure_reason,
                            "session_id": outcome.session_id,
                            "failed_step": outcome.failed_step,
                            "attempt": outcome.attempt_count or 1,
                        },
                    )

            last_outcome = outcome

            # Apply context updates
            if outcome.context_updates:
                ctx.update(outcome.context_updates)
            ctx.set("outcome", outcome.status.value)
            if outcome.preferred_label:
                ctx.set("preferred_label", outcome.preferred_label)

            # Select next edge
            edge = select_edge(current_node.id, outcome, ctx, self.graph)
            if edge is None:
                # Distinguish two cases:
                # (a) Conditional-mismatch dead end: outgoing edges exist but
                #     none matched the current outcome. This is a hard failure
                #     consistent with the main loop's no-matching-edge posture
                #     (EXTENSIONS.md §33). The engine forced this FAIL; no node
                #     produced a verdict, so is_explicit=False.
                # (b) No outgoing edges at all: a designed terminus. Return the
                #     last outcome unchanged (graceful subgraph completion).
                outgoing = self.graph.outgoing_edges(current_node.id)
                if outgoing:
                    return Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=(
                            f"Subgraph dead end at node '{current_node.id}': "
                            f"{len(outgoing)} outgoing edge(s) exist but none "
                            f"match the current outcome "
                            f"(status={outcome.status.value}). "
                            f"This is a conditional-mismatch dead end \u2014 "
                            f"add a matching edge or a fallback route."
                        ),
                        is_explicit=False,
                    )
                # No outgoing edges at all -- subgraph reached a designed terminus.
                return outcome

            current_node = self.graph.nodes[edge.to_node]

        # Safety bound exceeded
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=f"Subgraph exceeded {max_steps} steps (safety bound)",
        )

    # Backward compat: _run_from was the pre-refactor private method name.
    # Will be removed in a future release.
    _run_from = run_subgraph

    def _initialize_context(self, goal: str | None) -> None:
        """Mirror graph attributes into context.

        Spec Section 3.1: Initialize phase.

        This seeds context with graph-level attributes only.  Additional keys
        arrive through external channels:

        - The resolver/dispatcher injects user-provided params and any
          schema-declared defaults (e.g. ``default:`` fields in
          resolver.yaml) into context before ``run()`` is called.  That is a
          resolver-layer responsibility, not an engine responsibility.
        - Subsequent nodes write outputs into context via the M5
          substitution mechanism and ``outputs=`` declarations.

        The engine itself has no concept of "param defaults".  Keys that exist
        in context at execution time are available for ``$variable``
        substitution in tool_command strings; keys that are absent leave the
        literal token unchanged (see ``substitution.py`` M5 contract).
        Pipeline authors should use shell ``${VAR:-default}`` syntax for any
        context key that may be absent at execution time.
        """
        # Set goal from argument or graph attribute
        effective_goal = goal or self.graph.goal
        if effective_goal:
            self.context.set("graph.goal", effective_goal)

        # Mirror graph-level attributes
        for key, value in self.graph.graph_attrs.items():
            self.context.set(f"graph.{key}", value)

        # Extension #24: Seed iteration state so $iteration / $loop_count
        # are available for substitution in prompts and tool commands from
        # the very first node.  iteration_count starts at 0 (the initial
        # pass before any loop_restart); it is incremented and re-seeded on
        # each loop_restart edge (see Step 6 in run()).
        self.context.set("iteration", str(self.iteration_count))
        self.context.set("loop_count", str(self.iteration_count))

    def _find_start_node(self) -> Node:
        """Find the start node.

        Resolution order (L-21, Spec Section 3.2, NLSpec line 344):
          1. shape=Mdiamond
          2. node_type="start" attribute
          3. id="start" (case-insensitive)

        Raises ValueError if no start node can be resolved.
        """
        # Priority 1: shape=Mdiamond
        for node in self.graph.nodes.values():
            if node.shape == "Mdiamond":
                return node

        # Priority 2: type="start" attribute
        for node in self.graph.nodes.values():
            if node.type == "start":
                logger.debug(
                    "No Mdiamond node found; using type='start' node '%s'",
                    node.id,
                )
                return node

        # Priority 3: id="start" (case-insensitive, L-21)
        for node in self.graph.nodes.values():
            if node.id.lower() == "start":
                logger.debug(
                    "No Mdiamond/type node found; using id='%s' as start node",
                    node.id,
                )
                return node

        raise ValueError(
            "No start node found (no shape=Mdiamond, no type='start', "
            "and no id='start'/'Start')"
        )

    async def _check_goal_gates(self) -> Outcome:
        """Check goal gate satisfaction at exit.

        Spec Section 3.4: Goal Gate Enforcement.

        Returns:
            SUCCESS/PARTIAL_SUCCESS if all goal gates passed.
            FAIL with suggested_next_ids=[retry_target] if a gate is
            unsatisfied and a retry target exists.
            FAIL without suggested_next_ids if no retry target.
        """
        unsatisfied: list[tuple[str, Outcome]] = []
        satisfied: list[str] = []
        for node_id, outcome in self.node_outcomes.items():
            node = self.graph.nodes.get(node_id)
            if node is None:
                continue
            if resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate"):
                # EXTENSIONS.md §25 — fail-closed gate enforcement.
                # A goal_gate node satisfies its gate ONLY when:
                #   1. outcome.is_success is True (SUCCESS or PARTIAL_SUCCESS), AND
                #   2. outcome.is_explicit is True (an asserted verdict: report_outcome,
                #      JSON, fenced JSON, or recovered embedded verdict).
                # This closes the spawn-path bypass: _outcome_from_spawn_result()
                # returns is_explicit=False when recovering from the orchestrator's
                # completion status alone (no report_outcome, no JSON). That outcome
                # may be SUCCESS but it is not an explicit verdict from the node.
                gate_satisfied = outcome.is_success and outcome.is_explicit
                if gate_satisfied:
                    satisfied.append(node_id)
                else:
                    unsatisfied.append((node_id, outcome))

        unsatisfied_ids = [nid for nid, _ in unsatisfied]
        await self._emit(
            PIPELINE_GOAL_GATE_CHECK,
            {
                "satisfied": satisfied,
                "unsatisfied": unsatisfied_ids,
            },
        )

        if not unsatisfied:
            # All goal gates satisfied (or none exist)
            if self.completed_nodes:
                last_id = self.completed_nodes[-1]
                last_outcome = self.node_outcomes.get(last_id)
                if last_outcome:
                    return last_outcome
            return Outcome(status=StageStatus.SUCCESS, notes="Pipeline completed")

        # Find the first unsatisfied gate and its retry target
        gate_node_id, gate_outcome = unsatisfied[0]
        gate_node = self.graph.nodes[gate_node_id]

        # Retry target resolution: node > node fallback > graph > graph fallback
        retry_target = (
            gate_node.attrs.get("retry_target")
            or gate_node.attrs.get("fallback_retry_target")
            or self.graph.graph_attrs.get("retry_target")
            or self.graph.graph_attrs.get("fallback_retry_target")
        )

        failure_reason = f"Unsatisfied goal gates: {unsatisfied_ids}"

        if retry_target and retry_target in self.graph.nodes:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=failure_reason,
                suggested_next_ids=[retry_target],
            )

        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=failure_reason,
        )

    def _graph_identity(self) -> dict[str, Any]:
        """Return the checkpoint's ``graph`` block: fingerprint + DOT source.

        Computed once per engine (the DOT source is immutable post-load) and
        embedded in every checkpoint so a resume is self-contained —
        ``manifest.json`` does not carry the source.  The fingerprint is what
        ladder rung 5 compares; it is evaluated ONLY on the explicit resume
        path (see checkpoint.py's module docstring), never here.
        """
        if self._graph_identity_cache is None:
            dot_source = self.graph.dot_source or ""
            self._graph_identity_cache = {
                "fingerprint": fingerprint_dot_source(dot_source),
                "dot_source": dot_source,
            }
        return self._graph_identity_cache

    def _serialize_node_outcomes(self) -> dict[str, dict[str, Any]]:
        """Routing/gating subset of every completed node's Outcome.

        Spec §5.3 rule 5 needs the last completed node's REAL outcome to make
        the single resume-hop edge-selection decision (a reconstructed
        ``SUCCESS`` was an ingredient of the pre-#66 "no matching edge from
        resumed node" crash class).  ``is_explicit`` rides along because
        EXTENSIONS §25's fail-closed goal-gate contract must survive the
        round trip: without it a resumed run of a gated graph would find every
        gate unsatisfied and re-execute via ``retry_target``.

        Deliberately NOT stored: ``context_updates`` (already merged into the
        context snapshot — one source of truth), ``response_text`` /
        ``failed_step`` / ``session_id`` (not routing-relevant; sessions are
        dead by definition once the process is gone).
        """
        return {
            node_id: {
                "status": outcome.status.value,
                "preferred_label": outcome.preferred_label,
                "suggested_next_ids": outcome.suggested_next_ids,
                "is_explicit": outcome.is_explicit,
                "failure_reason": outcome.failure_reason,
                "notes": outcome.notes,
            }
            for node_id, outcome in self.node_outcomes.items()
        }

    def _serialize_node_retries(self) -> dict[str, int]:
        """Retries consumed per completed node — spec §5.3 rule 4 / DoD :1856.

        ``execute_with_retry`` records the 1-indexed ``attempt_count`` on every
        outcome that passes through the retry ladder, so retries consumed is
        ``attempt_count - 1``.  This field existed in the schema before but was
        written as ``{}`` unconditionally; the spec asks for it, so it is now
        actually populated.
        """
        return {
            node_id: max(0, (outcome.attempt_count or 1) - 1)
            for node_id, outcome in self.node_outcomes.items()
        }

    def _save_checkpoint(
        self,
        current_node_id: str,
        *,
        goal_gate_retries: int = 0,
        failure_routing_retries: int = 0,
        steps: int = 0,
    ) -> None:
        """Save a checkpoint after a node execution.

        Spec Section 5.3: Checkpoint.save.  ``current_node`` is the LAST
        COMPLETED node (spec's own definition) and the save happens BEFORE
        edge selection — which is why a resume re-runs that one selection
        itself rather than restoring a pre-picked next node.

        The checkpoint IS a resume marker, but only through the explicit
        resume entry point; a fresh ``run()`` never reads one back.
        Graph-level idempotency (``examples/pipelines/12-graph-resume.dot``)
        remains an independent, fully supported pattern.
        """
        if self._checkpoint_path is None:
            return  # S5: branch clones never checkpoint

        os.makedirs(self.logs_root, exist_ok=True)

        cp = Checkpoint(
            current_node=current_node_id,
            completed_nodes=list(self.completed_nodes),
            context_snapshot=self.context.snapshot(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            node_retries=self._serialize_node_retries(),
            logs=self.context.get_logs(),  # L-7: include logs in checkpoint
            run_state=RUN_STATE_IN_FLIGHT,
            node_outcomes=self._serialize_node_outcomes(),
            engine_state={
                "iteration_count": self.iteration_count,
                "node_execution_counts": dict(self._node_execution_counts),
                "goal_gate_retries": goal_gate_retries,
                "failure_routing_retries": failure_routing_retries,
                "steps": steps,
            },
            graph=self._graph_identity(),
        )
        save_checkpoint(cp, self._checkpoint_path)
        # Retained so the terminal run_state flip can rewrite the SAME payload
        # without ever reading a checkpoint back (AC-4: no checkpoint read on
        # any non-resume path, enforced by construction).
        self._last_checkpoint = cp

    def _mark_run_completed(self) -> None:
        """Flip the final checkpoint's ``run_state`` to ``completed``.

        Called once, when an entry point is about to return its final Outcome.
        Resuming a finished run is refused at ladder rung 4 rather than
        re-running terminal/gate logic against ambiguous state.

        Rewrites the last checkpoint payload held in memory — it does NOT read
        the file back, so no entry point (fresh or resumed) ever loads a
        checkpoint outside the explicit resume path.  A run that dies before
        any checkpoint exists leaves nothing to mark, which is correct: there
        is nothing to resume either.
        """
        if self._checkpoint_path is None or self._last_checkpoint is None:
            return
        self._last_checkpoint.run_state = RUN_STATE_COMPLETED
        save_checkpoint(self._last_checkpoint, self._checkpoint_path)

    # -- Run directory helpers -----------------------------------------------

    def _write_manifest(self, goal: str | None) -> None:
        """Write manifest.json and create the artifacts/ directory.

        Spec Section 5.6: Run Directory Structure.

        Extension #28: Run provenance stamping.  The manifest includes
        ``engine_version`` and ``engine_commit`` so an incident analyst can
        tell from the run directory alone what code produced the run.  Values
        are ``"unknown"`` for editable/dev installs where commit identity is
        not available from install-time metadata (PEP 610 direct_url.json).
        The runner layer may augment the manifest with ``runner_version``,
        ``runner_commit``, and ``provider`` fields after this call — one
        writer per field, no races.  Existing fields are additive-only;
        ``graph_name``, ``goal``, ``start_time``, ``node_count``,
        ``edge_count`` are unchanged for backward compatibility.
        """
        os.makedirs(self.logs_root, exist_ok=True)
        os.makedirs(os.path.join(self.logs_root, "artifacts"), exist_ok=True)

        provenance = _get_engine_provenance()
        manifest = {
            "graph_name": self.graph.name,
            "goal": self.graph.goal or goal or "",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "node_count": len(self.graph.nodes),
            "edge_count": len(self.graph.edges),
            # Provenance fields (Extension #28): additive, backward-compatible.
            # Values are "unknown" when identity cannot be determined without
            # fabricating — stamp honestly, never guess.
            "engine_version": provenance["engine_version"],
            "engine_commit": provenance["engine_commit"],
        }
        manifest_path = os.path.join(self.logs_root, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        # Write graph.dot for dashboard visualization
        if self.graph.dot_source:
            dot_path = os.path.join(self.logs_root, "graph.dot")
            with open(dot_path, "w") as f:
                f.write(self.graph.dot_source)

    def _write_node_status(
        self, node_id: str, outcome: Outcome, duration_ms: float
    ) -> None:
        """Write status.json for a node after execution.

        Spec Section 5.6: Per-node status.json.

        Extension #24: Per-iteration records.
        Writes to two locations:
          1. logs_root/<node_id>/status.json  — flat path (backward compat)
          2. logs_root/iteration_<N>/<node_id>/status.json  — iteration-scoped
             (survives loop_restart; each iteration's record is preserved)
        Also appends one record to logs_root/trace.jsonl (append-only descent
        curve — the canonical convergence observability artifact).
        """
        status = {
            "node_id": node_id,
            "iteration": self.iteration_count,
            "outcome": outcome.status.value,
            "status": outcome.status.value,  # backward compat (M-19)
            "preferred_next_label": outcome.preferred_label,
            "suggested_next_ids": outcome.suggested_next_ids,
            "context_updates": outcome.context_updates,
            "duration_ms": duration_ms,
            "notes": outcome.notes,
            "failure_reason": outcome.failure_reason,
            "session_id": outcome.session_id,
            # EXTENSIONS.md §25: is_explicit is durable audit data — analysts
            # must be able to distinguish asserted verdicts from defaulted
            # ones without reverse-engineering the notes prefix.
            "is_explicit": outcome.is_explicit,
            # Issue 10: structured tool-invocation failure payload.
            # Populated by ToolHandler on failure; None/absent on success.
            "failed_step": outcome.failed_step,
        }

        # 1. Flat path (backward compat — existing consumers read this)
        node_dir = os.path.join(self.logs_root, node_id)
        os.makedirs(node_dir, exist_ok=True)
        status_path = os.path.join(node_dir, "status.json")
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)

        # 2. Iteration-scoped path (Extension #24 — survives loop_restart)
        iteration_node_dir = os.path.join(
            self.logs_root,
            f"iteration_{self.iteration_count}",
            node_id,
        )
        os.makedirs(iteration_node_dir, exist_ok=True)
        iteration_status_path = os.path.join(iteration_node_dir, "status.json")
        with open(iteration_status_path, "w") as f:
            json.dump(status, f, indent=2)

        # 3. Append to trace.jsonl (append-only descent curve)
        trace_record = {
            "iteration": self.iteration_count,
            "node_id": node_id,
            "status": outcome.status.value,
            "preferred_label": outcome.preferred_label,
            # EXTENSIONS.md §25: durable audit field (see status.json above).
            "is_explicit": outcome.is_explicit,
            "duration_ms": duration_ms,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        trace_path = os.path.join(self.logs_root, "trace.jsonl")
        with open(trace_path, "a") as f:
            f.write(json.dumps(trace_record) + "\n")

    # -- Parallel fan-in helper (component-node explicit parallelism) -----------
    #
    # T0-4: the engine-level _execute_parallel_fan_out helper that backed the
    # retired multi-match fan-out dialect has been deleted along with its only
    # call site.  Explicit parallelism runs through ParallelHandler (which
    # executes each branch via run_subgraph); the engine keeps only the fan-in
    # discovery below.

    def _find_fan_in_node(self, parallel_target_ids: list[str]) -> str | None:
        """Find the first node reachable from ALL parallel branch roots via BFS.

        Replaces the 1-hop intersection approach, which failed when branches
        had multiple steps before converging (multi-hop fan-in).

        Example that was broken under 1-hop:
            component → RunBaseline → ExtractMetrics_B → EvalGather
                      → RunVariant  → ExtractMetrics_V → EvalGather

        1-hop: outgoing(RunBaseline) ∩ outgoing(RunVariant)
               = {ExtractMetrics_B} ∩ {ExtractMetrics_V} = ∅  → None (WRONG)

        BFS: reachable(RunBaseline) = {ExtractMetrics_B, EvalGather}
             reachable(RunVariant)  = {ExtractMetrics_V, EvalGather}
             common - roots         = {EvalGather}  → "EvalGather" (CORRECT)

        Args:
            parallel_target_ids: First node in each parallel branch (direct
                children of the component/fan-out node).

        Returns:
            The earliest common descendant of all branches (minimum max-depth
            across branches), or None if branches never converge.
        """
        if not parallel_target_ids:
            return None

        # BFS from each branch root, collecting all reachable nodes with depth
        reachable_per_branch: list[dict[str, int]] = []
        for root in parallel_target_ids:
            visited: dict[str, int] = {}
            queue: list[tuple[str, int]] = [(root, 0)]
            while queue:
                node_id, depth = queue.pop(0)
                if node_id in visited:
                    continue
                visited[node_id] = depth
                for edge in self.graph.outgoing_edges(node_id):
                    if edge.to_node not in visited:
                        queue.append((edge.to_node, depth + 1))
            reachable_per_branch.append(visited)

        # Common nodes = intersection of all reachable sets
        common: set[str] = set(reachable_per_branch[0].keys())
        for other in reachable_per_branch[1:]:
            common = common.intersection(other.keys())

        # Exclude branch roots (they cannot be their own fan-in node)
        branch_root_set = set(parallel_target_ids)
        common = common - branch_root_set

        if not common:
            return None

        # Pick the node with the smallest maximum depth across all branches
        # (the earliest / shallowest shared descendant)
        best = min(common, key=lambda n: max(r[n] for r in reachable_per_branch))
        return best

    # -- Failure routing helpers ----------------------------------------------

    async def _terminate_child_dot_resolution(
        self,
        *,
        node_id: str,
        exc: ChildDotResolutionError,
        node_start_time: float,
        pipeline_start_time: float,
        execution_index: int,
    ) -> Outcome:
        """Terminate the run on a child-graph RESOLUTION fault (issue #200).

        A ``shape=folder`` node whose ``dot_file=`` names no existing child
        graph is not a node whose work failed and not a graph whose routing is
        incomplete -- it is a composition fault, and it gets its own terminal
        class here rather than being laundered through edge selection into
        ``no_matching_edge`` (EXTENSIONS.md §33), which named the wrong
        subsystem and printed only the single chosen path.

        Deliberately terminal: there is no child graph to run and no honest
        way to route around one that does not exist.  ``validate_or_raise``
        stays untouched -- admission remains LAZY (EXTENSIONS.md §10), so a
        child DOT written earlier in the same run still resolves normally and
        write-then-run composition is unaffected.
        """
        fail_outcome = Outcome(
            status=StageStatus.FAIL,
            notes=str(exc),
            failure_reason=str(exc),
        )
        node_duration_ms = (time.monotonic() - node_start_time) * 1000
        self.node_outcomes[node_id] = fail_outcome
        self._write_node_status(node_id, fail_outcome, node_duration_ms)
        await self._emit(
            PIPELINE_NODE_COMPLETE,
            {
                "node_id": node_id,
                "status": fail_outcome.status.value,
                "duration_ms": node_duration_ms,
                "notes": fail_outcome.notes,
                "failure_reason": fail_outcome.failure_reason,
                "session_id": None,
                "execution_index": execution_index,
            },
        )
        await self._emit(
            PIPELINE_ERROR,
            {
                "node_id": node_id,
                "error_type": "child_dot_resolution",
                "message": str(exc),
            },
        )
        await self._emit_complete(fail_outcome, pipeline_start_time)
        return fail_outcome

    def _no_matching_edge_reason(self, node_id: str, outcome: Outcome) -> str:
        """Build a diagnostic 'no matching edge' message that traces back.

        The bare "No matching edge from node 'X'" message (unchanged as the
        prefix, for backward compatibility with existing substring checks)
        gives no clue why routing failed. When the outcome carried
        suggested_next_ids that didn't resolve to any edge -- e.g. a
        genuinely wrong/hallucinated ID, or one edge_selection's coercion
        policy legitimately rejected (see edge_selection._coerce_suggested_id)
        -- name both the suggestion and the edges that actually existed, so
        the real cause is traceable instead of a dead end.
        """
        reason = f"No matching edge from node '{node_id}'"
        if outcome.suggested_next_ids:
            available = [e.to_node for e in self.graph.outgoing_edges(node_id)]
            reason += (
                f" (suggested_next_ids={outcome.suggested_next_ids!r} matched "
                f"none of the outgoing edges; available targets={available!r})"
            )
        return reason

    def _resolve_failure_retry_target(self, node: Node) -> Node | None:
        """Resolve a retry target when no edge matches after node execution.

        Fallback chain (first match wins):
        1. node.retry_target
        2. node.fallback_retry_target

        Graph-level retry_target is goal-gate-exit only (spec §3.4); it is
        intentionally NOT consulted on per-node failure (spec §3.7).

        Returns the target Node or None if no valid target exists.
        """
        target_id = node.attrs.get("retry_target") or node.attrs.get(
            "fallback_retry_target"
        )
        if target_id and target_id in self.graph.nodes:
            return self.graph.nodes[target_id]
        return None

    def terminate_pipeline(
        self,
        *,
        node_id: str,
        upstream_outcome: Outcome | None,
        termination_reason: str,
    ) -> Outcome:
        """The ONLY API for routing-termination Outcome construction.

        Threads ``upstream_outcome.failure_reason`` automatically.  If no
        upstream reason exists (or upstream_outcome is None), the routing
        message becomes the failure_reason — today's behavior preserved for
        outcome-less terminations.

        Invariants (enforced by test_terminate_pipeline.py):
        - Never raises.  (Totality test asserts this across full input space.)
        - Preserves ``upstream_outcome.failure_reason`` as failure_reason
          when present; routing message lives in notes.
        - If upstream had no reason: failure_reason = routing message, notes = None.

        Args:
            node_id: ID of the node where routing terminated.  Not used in
                result construction but available for caller context / logging.
            upstream_outcome: The handler's outcome (or routing_outcome from
                skip-path), or None for resume-path where no handler ran.
            termination_reason: Human-readable routing message
                (e.g. "No matching edge from node 'X'").

        Returns:
            An Outcome with status=FAIL and the threaded failure_reason / notes.

        Sole-caller guard: the AST test in test_terminate_pipeline.py asserts
        that no top-level Outcome construction with a "No matching edge from"
        failure_reason pattern exists outside this method body.
        """
        upstream_reason = upstream_outcome.failure_reason if upstream_outcome else None
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=upstream_reason or termination_reason,
            notes=termination_reason if upstream_reason else None,
        )

    # -- R12 M1-M4 helpers ---------------------------------------------------

    def _get_runs_on(self, node: Node) -> str:
        """Return the node's ``runs_on`` axis value.

        M4: Determines whether the node executes based on upstream state.

        Returns:
            One of ``"success"`` (default), ``"always"``, or ``"failure"``.

        Interaction with ``continue_on_fail``:
            ``continue_on_fail`` and ``runs_on`` are NOT orthogonal. A
            predecessor node with ``continue_on_fail=true`` that fails at
            runtime has its outcome flipped FAIL→SUCCESS *before*
            ``_populate_failed_outputs`` runs. This means the failure signal
            is swallowed: a downstream ``runs_on=failure`` cleanup node will
            NOT trigger, because the failed-outputs table is never populated
            for that predecessor. Use ``runs_on=always`` on the cleanup node
            if you want it to fire regardless of whether the predecessor used
            ``continue_on_fail``. See also the comment block at the
            ``continue_on_fail`` override site in ``run()``.
        """
        raw = node.attrs.get("runs_on", "success") or "success"
        val = str(raw).strip().lower()
        if val in ("always", "failure"):
            return val
        return "success"

    def _extract_node_refs(self, node: Node) -> set[str]:
        """Extract all context key references from a node's substitutable attrs.

        M2: Scans ``tool_command``, ``prompt``, ``description``, and
        ``tool_env`` for ``${key}`` and ``$key`` tokens.  The list of
        scanned attributes is declared in :data:`SUBSTITUTABLE_ATTRS` and
        is the single authoritative registry — adding a new substitutable
        attribute is a one-line addition there.

        Args:
            node: The node whose attributes are scanned.

        Returns:
            Set of context key names referenced by the node.
        """
        refs: set[str] = set()
        for attr_name in SUBSTITUTABLE_ATTRS:
            if attr_name == "prompt":
                val = node.prompt or node.attrs.get("prompt", "") or ""
            else:
                val = node.attrs.get(attr_name, "") or ""
            if val:
                refs.update(extract_refs(str(val)))
        return refs

    async def _check_node_skip(self, node: Node) -> Outcome | None:
        """Pre-execution skip check (M2/M3/M4).

        Before invoking a handler, scans the node's substitutable attributes
        for context key references.  If any referenced key is in
        :attr:`failed_outputs`, the node is SKIPPED and a
        ``PIPELINE_NODE_SKIPPED`` event is emitted.

        For nodes with ``runs_on=always`` or ``runs_on=failure``, the skip
        logic is bypassed: missing references resolve to empty string rather
        than causing a skip (M4).

        For ``runs_on=failure`` nodes: execute only if ``failed_outputs`` is
        non-empty (i.e. at least one predecessor failed somewhere in the
        pipeline); skip otherwise.

        Args:
            node: The node about to execute.

        Returns:
            A SKIPPED ``Outcome`` if the node should be skipped, else ``None``.
        """
        runs_on = self._get_runs_on(node)

        if runs_on == "always":
            # Always execute; missing references resolve to empty string.
            return None

        if runs_on == "failure":
            # Execute only when something upstream has failed.
            if not self.failed_outputs:
                # Nothing has failed — skip this failure-cleanup node.
                skip_outcome = Outcome(
                    status=StageStatus.SKIPPED,
                    notes=(
                        f"Node '{node.id}' runs_on=failure but no predecessors failed"
                    ),
                    failure_reason="no_predecessor_failure",
                )
                await self._emit(
                    PIPELINE_NODE_SKIPPED,
                    {
                        "node_id": node.id,
                        "cause": "no_predecessor_failure",
                        "references": [],
                        "missing_keys": [],
                        # failure_mode is intentionally None here: this skip
                        # is the *absence* of a failure (the happy path ran
                        # clean). Emitting "predecessor_failed" when no
                        # predecessor failed produces false-positive hits for
                        # downstream observability filters and queries on
                        # failure_mode=predecessor_failed.
                        "failure_mode": None,
                        "failure_mode_taxonomy_version": 1,
                    },
                )
                return skip_outcome
            # Something failed — run this node, resolving missing refs to "".
            return None

        # runs_on == "success" (default): skip if any referenced key is failed.
        refs = self._extract_node_refs(node)
        failed_refs: list[dict[str, str]] = []
        for key in refs:
            if key in self.failed_outputs:
                failed_refs.append(
                    {"key": key, "producer_node_id": self.failed_outputs[key]}
                )

        if not failed_refs:
            return None  # No failed references — proceed normally.

        missing_keys = [r["key"] for r in failed_refs]
        skip_outcome = Outcome(
            status=StageStatus.SKIPPED,
            notes=(
                f"Node '{node.id}' skipped: predecessor(s) failed for keys "
                f"{missing_keys}"
            ),
            failure_reason="predecessor_failed",
        )
        await self._emit(
            PIPELINE_NODE_SKIPPED,
            {
                "node_id": node.id,
                "cause": "predecessor_failed",
                "references": failed_refs,
                "missing_keys": missing_keys,
                "failure_mode": "predecessor_failed",
                "failure_mode_taxonomy_version": 1,
            },
        )
        logger.info(
            "Node '%s' SKIPPED — predecessor failed for keys: %s",
            node.id,
            missing_keys,
        )
        return skip_outcome

    def _populate_failed_outputs(self, node_id: str) -> None:
        """Add a failed/skipped node's declared outputs to :attr:`failed_outputs`.

        M2: When a node ends in FAIL or SKIPPED, all context keys it was
        contracted to produce are marked as failed.  Downstream nodes that
        reference those keys will be caught by the eager scan and skipped
        (transitive skip propagation).

        Args:
            node_id: ID of the failed/skipped node.
        """
        outputs = self._output_table.get(node_id, frozenset())
        for key in outputs:
            if key not in self.failed_outputs:
                self.failed_outputs[key] = node_id

    async def _check_contract_violation(self, node_id: str, outcome: Outcome) -> None:
        """Post-success contract violation audit (M3).

        After a node succeeds, compare its declared ``outputs=`` set against
        the keys it actually wrote to context (via ``outcome.context_updates``).
        If any declared output is missing, emit a
        ``PIPELINE_NODE_CONTRACT_VIOLATION`` event.

        This is a diagnostic signal, not a hard error: the node's outcome is
        not changed.  The information is available in ``events.jsonl`` for
        author debugging.

        Args:
            node_id: The producer node's ID.
            outcome: The node's SUCCESS outcome.
        """
        # Fix #2: Component nodes (shape=component) emit parallel results via
        # parallel.results in context, not via declared per-node outputs.
        # build_output_table() infers dynamic branch.{idx}.outcome keys for
        # every component node, but ParallelHandler never writes those keys to
        # outcome.context_updates.  Checking the contract here would always
        # fire a false-positive violation.  Skip entirely for component nodes.
        node = self.graph.nodes.get(node_id)
        if node is not None and node.shape == "component":
            return

        declared = self._output_table.get(node_id, frozenset())
        if not declared:
            return  # No declared outputs → nothing to check.

        emitted = (
            set(outcome.context_updates.keys()) if outcome.context_updates else set()
        )
        missing = declared - emitted

        if not missing:
            return  # All declared outputs were emitted ✓

        await self._emit(
            PIPELINE_NODE_CONTRACT_VIOLATION,
            {
                "node_id": node_id,
                "declared": sorted(declared),
                "emitted": sorted(emitted),
                "missing": sorted(missing),
                "failure_mode": "software",
                "failure_mode_taxonomy_version": 1,
            },
        )
        logger.warning(
            "Node '%s' succeeded but declared outputs %s were not emitted "
            "(emitted: %s)",
            node_id,
            sorted(missing),
            sorted(emitted),
        )

    def _resolve_missing_as_empty(self, node: Node) -> None:
        """Resolve missing ``${key}`` references to empty string (M4).

        For nodes with ``runs_on=always`` or ``runs_on=failure``, missing
        context keys are pre-populated with empty string so that
        substitution in the handler produces ``""`` rather than a literal
        ``${key}`` token.

        This is done by injecting empty-string values for any referenced key
        that is not currently in context.  The injection is temporary: context
        values set here will be overwritten if a successor produces the key.

        Args:
            node: The node whose missing refs should resolve to empty string.
        """
        refs = self._extract_node_refs(node)
        for key in refs:
            if self.context.get(key) is None:
                self.context.set(key, "")

    def _check_requires(self, node: Node) -> Outcome | None:
        """Pre-execution file existence check for the ``requires=`` attribute (Bug H).

        Reads the node's ``requires`` attribute (comma-separated relative file
        paths) and verifies that every declared path exists on disk before the
        handler runs.  Paths are resolved relative to ``context.target_dir``
        if set, falling back to ``os.getcwd()``.

        This prevents LLM agents from fabricating missing inputs when upstream
        parallel branches didn't produce their expected artifacts.  Failing
        fast here surfaces the real error (missing file) rather than letting
        the agent hallucinate a plausible-looking result.

        Returns:
            A FAIL ``Outcome`` naming all missing files if any are absent,
            ``None`` if all required files exist (or no ``requires=`` is set).
        """
        raw_requires = node.attrs.get("requires") if node.attrs else None
        if not raw_requires:
            return None

        # Resolve base directory: context.target_dir takes precedence
        base_dir_raw = self.context.get("context.target_dir")
        base_dir = Path(str(base_dir_raw)) if base_dir_raw else Path(os.getcwd())

        # Parse comma-separated paths, strip whitespace
        paths = [p.strip() for p in str(raw_requires).split(",") if p.strip()]
        if not paths:
            return None

        missing = [p for p in paths if not (base_dir / p).exists()]
        if not missing:
            return None  # All required files are present — proceed normally

        logger.warning(
            "Node '%s' requires= validation failed: missing files %s (base_dir=%s)",
            node.id,
            missing,
            base_dir,
        )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Node '{node.id}' requires inputs that don't exist: {missing} "
                f"(resolved under {base_dir})"
            ),
            notes=(
                f"Missing required files: {', '.join(missing)}. "
                f"Ensure upstream nodes produced these artifacts before this "
                f"node runs. Set requires= to declare file preconditions."
            ),
        )

    def _check_must_write(
        self, node: Node, outcome: Outcome, node_start_wall: float
    ) -> Outcome | None:
        """Artifact contract for the ``must_write=`` attribute (EXTENSIONS.md §27).

        Thin delegate to :func:`must_write.check_must_write` (shared with the
        retry ladder, which runs the same check per-attempt so that violations
        consume ``max_retries`` attempts — see ``retry.execute_with_retry``).
        The engine calls this as the FINAL backstop, after the ``auto_status``
        and ``continue_on_fail`` overrides, so no override can convert an
        artifact-contract violation into a silent success.

        Returns:
            A FAIL ``Outcome`` if the contract is violated, ``None`` if the
            contract is satisfied or the node has no ``must_write=`` attribute.
        """
        return check_must_write(node, outcome, node_start_wall, self.context)

    # -- Event helpers -------------------------------------------------------

    async def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit an event via hooks, if provided.

        S4: If this engine is a branch clone, injects ``branch_id`` into the
        event payload so concurrent-branch logs can be disambiguated.  The
        discriminator is ``None`` for the top-level engine (no overhead).
        """
        if self.hooks is not None:
            if self._branch_id is not None:
                data = {**data, "branch_id": self._branch_id}
            await self.hooks.emit(event_name, data)

    async def _emit_complete(self, outcome: Outcome, start_time: float) -> None:
        """Emit the pipeline:complete event."""
        duration_ms = (time.monotonic() - start_time) * 1000
        await self._emit(
            PIPELINE_COMPLETE,
            {
                "status": outcome.status.value,
                "total_nodes_executed": len(self.completed_nodes),
                "duration_ms": duration_ms,
            },
        )
