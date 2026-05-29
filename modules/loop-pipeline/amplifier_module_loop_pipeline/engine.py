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
    Checkpoint,
    CheckpointMismatchError,
    load_checkpoint,
    save_checkpoint,
)
from .context import PipelineContext
from .run_identity import RunIdentity
from .edge_selection import select_all_matching_edges, select_edge
from .graph import Graph, Node
from .handlers import HandlerRegistry
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
    PIPELINE_START,
)
from .retry import RetryPolicy, execute_with_retry
from .substitution import extract_refs

logger = logging.getLogger(__name__)


class PipelineEngine:
    """Graph-walking execution engine.

    Walks the graph from start to exit, executing node handlers and
    selecting edges deterministically based on outcomes and context.

    Saves a checkpoint after each node execution so the pipeline can
    resume after crashes.
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
        self._fidelity_degraded_hop: bool = False
        self._checkpoint_path = os.path.join(logs_root, "checkpoint.json")
        self.artifact_store = ArtifactStore(base_dir=logs_root)

        # M1/M2 (R12): Output table + failed-outputs propagation table.
        # _output_table maps node_id → set of context keys the node is contracted
        # to produce; built once at graph-load from outputs= attrs + inference.
        # failed_outputs maps context-key → producing-node-id for all keys that
        # came from a failed or skipped predecessor.  Cleared on retry (CR-3).
        self._output_table: dict[str, frozenset[str]] = build_output_table(graph)
        self.failed_outputs: dict[str, str] = {}

    def _check_cancelled(self) -> bool:
        """Check if cancellation has been requested via the cancel event."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    async def run(self, goal: str | None = None) -> Outcome:
        """Execute the pipeline from start to exit.

        If a checkpoint exists at ``{logs_root}/checkpoint.json``, the
        engine resumes from it, skipping already-completed nodes and
        restoring context state.

        Args:
            goal: Optional goal string to set in context. If not provided,
                uses the graph-level goal attribute.

        Returns:
            The final Outcome of the pipeline run.
        """
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

        # Check for existing checkpoint and restore state
        resumed = self._try_resume_from_checkpoint()

        # Find the start node
        current_node = self._find_start_node()

        # Bound goal-gate-driven retries to prevent infinite loops
        goal_gate_retries = 0
        # Bound failure-routing retries (no-matching-edge fallback chain)
        failure_routing_retries = 0

        # Bound total pipeline steps to prevent infinite loops caused by
        # condition-routing bugs or missing edge guards. Matches the safety
        # bound used in the subgraph runner (run_subgraph).
        max_steps = len(self.graph.nodes) * self._MAX_GOAL_GATE_RETRIES
        steps = 0

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
                self._save_checkpoint(current_node.id)
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
                    retry_node_id = gate_result.suggested_next_ids[0]
                    goal_gate_retries += 1
                    logger.info(
                        "Goal gate unsatisfied, retrying from '%s' (attempt %d)",
                        retry_node_id,
                        goal_gate_retries,
                    )
                    # CR-3 (R12): Reset per-run state so skip-propagation from
                    # attempt N does not block the retried nodes in attempt N+1.
                    self.completed_nodes.clear()
                    self.node_outcomes.clear()
                    self.failed_outputs.clear()
                    current_node = self.graph.nodes[retry_node_id]
                    continue

                # No retry target or retries exhausted — fail
                await self._emit_complete(gate_result, pipeline_start_time)
                return gate_result

            # Step 1b: Skip already-completed nodes (resume path)
            if resumed and current_node.id in {nid for nid in self.completed_nodes}:
                # Re-select the edge this node used last time
                edge = select_edge(
                    current_node.id,
                    self.node_outcomes.get(
                        current_node.id, Outcome(status=StageStatus.SUCCESS)
                    ),
                    self.context,
                    self.graph,
                )
                if edge is None:
                    fail_outcome = self.terminate_pipeline(
                        node_id=current_node.id,
                        upstream_outcome=None,
                        termination_reason=(
                            f"No matching edge from resumed node '{current_node.id}'"
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
                current_node = self.graph.nodes[edge.to_node]
                continue

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
                self._save_checkpoint(current_node.id)
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

            node_start_time = time.monotonic()
            retry_policy = RetryPolicy.from_node(current_node, self.graph)

            if _requires_fail is not None:
                # requires= validation failed — short-circuit without calling handler
                outcome = _requires_fail
            else:
                # Per-node timeout enforcement: wrap handler execution with
                # asyncio.timeout when the node declares a timeout attribute.
                # DOT timeout values are in seconds (per NLSpec timeout_seconds).
                node_timeout_raw = current_node.timeout
                if node_timeout_raw:
                    timeout_s = float(node_timeout_raw)
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
                    except asyncio.TimeoutError:
                        node_duration_ms = (time.monotonic() - node_start_time) * 1000
                        outcome = Outcome(
                            status=StageStatus.FAIL,
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
            node_duration_ms = (time.monotonic() - node_start_time) * 1000

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

            # L-9: auto_status — override non-success to SUCCESS when enabled
            if current_node.auto_status is True and not outcome.is_success:
                logger.debug(
                    "Node '%s' has auto_status=true; overriding %s to SUCCESS",
                    current_node.id,
                    outcome.status.value,
                )
                outcome = Outcome(
                    status=StageStatus.SUCCESS,
                    notes=f"auto_status override (was {outcome.status.value})",
                    context_updates=outcome.context_updates,
                    preferred_label=outcome.preferred_label,
                    suggested_next_ids=outcome.suggested_next_ids,
                )

            # continue_on_fail: override FAIL to SUCCESS for routing, log the failure
            #
            # NOTE — continue_on_fail and runs_on are NOT orthogonal:
            # - continue_on_fail (per-predecessor-node override) flips FAIL→SUCCESS
            #   BEFORE _populate_failed_outputs runs (see the FAIL check at
            #   engine.py:512 which tests the already-overridden outcome).
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
                current_node.attrs.get("continue_on_fail") == "true"
                and outcome.status == StageStatus.FAIL
            ):
                logger.warning(
                    "Node '%s' failed but continue_on_fail=true; overriding to SUCCESS "
                    "(failure: %s)",
                    current_node.id,
                    outcome.failure_reason or outcome.notes or "no reason given",
                )
                outcome = Outcome(
                    status=StageStatus.SUCCESS,
                    notes=(
                        f"continue_on_fail override (was FAIL: "
                        f"{outcome.failure_reason or outcome.notes})"
                    ),
                    context_updates=outcome.context_updates,
                    preferred_label=outcome.preferred_label,
                    suggested_next_ids=outcome.suggested_next_ids,
                )

            # Step 3: Record completion
            self.completed_nodes.append(current_node.id)
            self.node_outcomes[current_node.id] = outcome
            logger.debug("Node %s completed: %s", current_node.id, outcome.status.value)

            # M-23: One-hop fidelity restoration
            if self._fidelity_degraded_hop:
                self.context.set("graph.default_fidelity", "full")
                self._fidelity_degraded_hop = False
                logger.info(
                    "Checkpoint resume: restored fidelity to 'full' "
                    "after one-hop degradation (node '%s')",
                    current_node.id,
                )

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
            self._save_checkpoint(current_node.id)
            await self._emit(
                PIPELINE_CHECKPOINT,
                {
                    "node_id": current_node.id,
                    "checkpoint_path": self._checkpoint_path,
                },
            )

            # Step 5: Select next edge(s) — detect multi-edge fan-out
            #
            # BUG G FIX: Component nodes (shape=component) are handled by
            # ParallelHandler, which fans out ALL outgoing branches internally
            # via run_subgraph and populates parallel.results
            # in context.  The engine must NOT re-fan-out via
            # _execute_parallel_fan_out after the handler returns — that would
            # execute each branch a second time.
            #
            # Key subtlety: component nodes typically use UNCONDITIONAL outgoing
            # edges (branches run regardless of conditions), so
            # select_all_matching_edges() — which only returns condition-matched
            # edges — must NOT be used here.  Instead, read ALL outgoing edges
            # directly from the graph, find the shared fan-in node, and route
            # to it.  The FanInHandler will then read parallel.results.
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

            all_matching = select_all_matching_edges(
                current_node.id, outcome, self.context, self.graph
            )

            if len(all_matching) > 1:
                # Multi-edge fan-out from a non-component node: execute all
                # targets in parallel via the engine-level fan-out path.
                logger.info(
                    "Multi-edge fan-out from '%s': %d parallel targets",
                    current_node.id,
                    len(all_matching),
                )

                await self._execute_parallel_fan_out(all_matching, pipeline_start_time)

                # Find convergence node: the first node that all parallel
                # targets share as a common outgoing edge target
                fan_in_node_id = self._find_fan_in_node(
                    [e.to_node for e in all_matching]
                )
                if fan_in_node_id is None:
                    fail_outcome = Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=(
                            f"Multi-edge fan-out from '{current_node.id}' "
                            f"has no convergence (fan-in) node"
                        ),
                    )
                    await self._emit_complete(fail_outcome, pipeline_start_time)
                    return fail_outcome

                # Store parallel results in context for the fan-in node
                current_node = self.graph.nodes[fan_in_node_id]
                continue

            # Single-edge selection (normal path)
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
                    termination_reason=(
                        f"No matching edge from node '{current_node.id}'"
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
            if edge.loop_restart:
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
                # Reset engine state for clean re-execution
                self.completed_nodes.clear()
                self.node_outcomes.clear()
                self.failed_outputs.clear()  # M2 R12: clear skip-propagation table
                goal_gate_retries = 0
                failure_routing_retries = 0

            # Step 7: Advance to next node
            current_node = self.graph.nodes[edge.to_node]

    async def run_subgraph(
        self,
        start_node_id: str,
        *,
        context: PipelineContext | None = None,
    ) -> Outcome:
        """Execute a subgraph starting from the given node.

        Walks from *start_node_id* until an exit node is reached, no
        outgoing edges exist, or the node is not in the graph.

        This is the subgraph runner used by ParallelHandler and
        ManagerLoopHandler to execute branches and child subgraphs.

        Args:
            start_node_id: Node ID to begin execution from.
            context: Optional isolated context for this subgraph run.
                     If None, uses the engine's main context.

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

            # Skip start nodes (no-op)
            if current_node.is_start_node():
                outcome = Outcome(status=StageStatus.SUCCESS)
            else:
                try:
                    outcome = await handler.execute(
                        current_node, ctx, self.graph, self.logs_root, engine=self
                    )
                except Exception as exc:
                    return Outcome(
                        status=StageStatus.FAIL,
                        failure_reason=f"Subgraph node '{current_node.id}' raised: {exc}",
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
                # No outgoing edge -- subgraph is complete
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
            if node.attrs.get("goal_gate") is True:
                if outcome.is_success:
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

    def _try_resume_from_checkpoint(self) -> bool:
        """Try to load and restore state from an existing checkpoint.

        Returns True if a checkpoint was loaded (resume mode), False otherwise.

        Raises CheckpointMismatchError if the checkpoint belongs to a different
        graph than the one currently running.  This is an intentional hard-fail:
        silently restarting would re-execute side-effecting nodes (git pushes,
        branch creates, file writes) that have already been applied.

        Three checkpoint formats are handled:

        1. Pre-identity (no ``identity`` field, no ``graph_fingerprint``):
           One-time migration — discard with an info-level log message; treat as
           "no checkpoint exists."  NOT a hard-fail: these checkpoints predate
           the identity guard entirely.

        2. Wave-0 #252 format (top-level ``graph_fingerprint`` string):
           Promoted into a RunIdentity and checked normally.  Mismatch → hard-fail.

        3. T2.4 format (``identity`` dict):
           Standard path.  Mismatch → hard-fail.

        Resume re-runs are scoped to nodes after the last completed_node.
        Idempotency of side-effecting handlers is the handler's responsibility,
        NOT the engine's.  See ``docs/designs/RECURRING-BUG-CLASSES.md``
        Species S3 for context.

        Spec Section 5.3: Resume behavior, T2.4 (RunIdentity hard-fail).
        """
        if not os.path.exists(self._checkpoint_path):
            return False

        try:
            cp = load_checkpoint(self._checkpoint_path)
        except (FileNotFoundError, KeyError, ValueError):
            logger.warning("Failed to load checkpoint, starting fresh")
            return False

        # T2.4: Identity guard — must run BEFORE restoring context.
        current_identity = RunIdentity.from_graph(self.graph)

        if cp.identity is None:
            # Pre-identity format (no identity field, no graph_fingerprint).
            # This is a one-time migration: discard silently, start fresh.
            # NOT a hard-fail — these checkpoints predate the identity guard.
            logger.info(
                "Pre-identity checkpoint format detected at %s; discarding "
                "(one-time migration — new checkpoints will embed RunIdentity).",
                self._checkpoint_path,
            )
            return False

        if cp.identity != current_identity:
            # Hard-fail: refuse to resume a checkpoint from a different graph.
            # Silently restarting would re-apply side-effecting nodes.
            raise CheckpointMismatchError(
                f"Checkpoint identity mismatch at {self._checkpoint_path}.\n"
                f"  Checkpoint identity : {cp.identity.graph_fingerprint[:12]}...\n"
                f"  Current graph       : {current_identity.graph_fingerprint[:12]}...\n"
                f"\n"
                f"The pipeline graph has changed since this checkpoint was written.\n"
                f"To avoid double-applying side-effecting nodes (git pushes,\n"
                f"branch creation, file writes), resume is REFUSED.\n"
                f"\n"
                f"To start fresh: delete {self._checkpoint_path} and re-run."
            )

        # Identity matches — proceed with context restoration.

        # Restore context from checkpoint
        for key, value in cp.context_snapshot.items():
            self.context.set(key, value)

        # M-23: Degrade fidelity from "full" to "summary:high" on resume.
        # The full session context is lost after a crash, so full fidelity
        # cannot be honoured.  Other modes pass through unchanged.
        restored_fidelity = self.context.get("graph.default_fidelity")
        if restored_fidelity == "full":
            self.context.set("graph.default_fidelity", "summary:high")
            self._fidelity_degraded_hop = True
            logger.info(
                "Checkpoint resume: degraded fidelity from 'full' to "
                "'summary:high' (full session context unavailable); "
                "will restore after first node"
            )

        # Restore completed nodes and outcomes
        for node_id, status_str in cp.completed_nodes.items():
            if node_id not in self.completed_nodes:
                self.completed_nodes.append(node_id)
            # Reconstruct a minimal Outcome from saved status
            self.node_outcomes[node_id] = Outcome(
                status=StageStatus(status_str),
                notes=cp.node_outcomes.get(node_id, {}).get("notes"),
                failure_reason=cp.node_outcomes.get(node_id, {}).get("failure_reason"),
                preferred_label=cp.node_outcomes.get(node_id, {}).get(
                    "preferred_label"
                ),
            )

        logger.info(
            "Resumed from checkpoint: %d nodes completed, current=%s",
            len(self.completed_nodes),
            cp.current_node,
        )
        return True

    def _save_checkpoint(self, current_node_id: str) -> None:
        """Save a checkpoint after a node execution.

        Spec Section 5.3: Checkpoint.save.
        """
        os.makedirs(self.logs_root, exist_ok=True)

        # Serialize node outcomes
        serialized_outcomes: dict[str, dict[str, str | None]] = {}
        for node_id, outcome in self.node_outcomes.items():
            serialized_outcomes[node_id] = {
                "status": outcome.status.value,
                "notes": outcome.notes,
                "failure_reason": outcome.failure_reason,
                "preferred_label": outcome.preferred_label,
            }

        cp = Checkpoint(
            current_node=current_node_id,
            completed_nodes={
                nid: self.node_outcomes[nid].status.value
                for nid in self.completed_nodes
                if nid in self.node_outcomes
            },
            context_snapshot=self.context.snapshot(),
            node_outcomes=serialized_outcomes,
            timestamp=datetime.now(timezone.utc).isoformat(),
            logs=self.context.get_logs(),  # L-7: include logs in checkpoint
            identity=RunIdentity.from_graph(self.graph),  # T2.4: scope to this graph
        )
        save_checkpoint(cp, self._checkpoint_path)

    # -- Run directory helpers -----------------------------------------------

    def _write_manifest(self, goal: str | None) -> None:
        """Write manifest.json and create the artifacts/ directory.

        Spec Section 5.6: Run Directory Structure.
        """
        os.makedirs(self.logs_root, exist_ok=True)
        os.makedirs(os.path.join(self.logs_root, "artifacts"), exist_ok=True)

        manifest = {
            "graph_name": self.graph.name,
            "goal": self.graph.goal or goal or "",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "node_count": len(self.graph.nodes),
            "edge_count": len(self.graph.edges),
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
        """
        node_dir = os.path.join(self.logs_root, node_id)
        os.makedirs(node_dir, exist_ok=True)
        status = {
            "node_id": node_id,
            "outcome": outcome.status.value,
            "status": outcome.status.value,  # backward compat (M-19)
            "preferred_next_label": outcome.preferred_label,
            "suggested_next_ids": outcome.suggested_next_ids,
            "context_updates": outcome.context_updates,
            "duration_ms": duration_ms,
            "notes": outcome.notes,
            "failure_reason": outcome.failure_reason,
            "session_id": outcome.session_id,
            # Issue 10: structured tool-invocation failure payload.
            # Populated by ToolHandler on failure; None/absent on success.
            "failed_step": outcome.failed_step,
        }
        status_path = os.path.join(node_dir, "status.json")
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)

    # -- Multi-edge parallel fan-out helpers -----------------------------------

    async def _execute_parallel_fan_out(
        self,
        edges: list,
        pipeline_start_time: float,
    ) -> list[dict[str, Any]]:
        """Execute multiple branch targets in parallel with isolated contexts.

        Each branch gets a clone of the current context for isolation
        (NLSpec Section 798). Results are collected and context_updates
        from all branches are merged back into the main context.
        """

        async def run_branch(target_node_id: str) -> dict[str, Any]:
            branch_context = self.context.clone()
            branch_registry = self.handler_registry.clone_for_branch()
            node = self.graph.nodes[target_node_id]
            handler = branch_registry.get(node)
            handler_type = node.type or node.shape

            # Increment per-node execution count for branch nodes
            self._node_execution_counts[node.id] = (
                self._node_execution_counts.get(node.id, 0) + 1
            )
            branch_execution_index = self._node_execution_counts[node.id]

            await self._emit(
                PIPELINE_NODE_START,
                {
                    "node_id": node.id,
                    "handler_type": handler_type,
                    "attempt": 1,  # within-handler retry counter (backward compat)
                    "execution_index": branch_execution_index,  # NEW — graph-level visit count
                },
            )

            node_start = time.monotonic()
            retry_policy = RetryPolicy.from_node(node, self.graph)

            try:
                outcome = await execute_with_retry(
                    handler,
                    node,
                    branch_context,
                    self.graph,
                    self.logs_root,
                    retry_policy,
                    hooks=self.hooks,
                    engine=self,
                )
            except Exception as exc:
                outcome = Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Parallel branch '{target_node_id}' raised: {exc}",
                )

            node_duration = (time.monotonic() - node_start) * 1000

            # Record completion in the main engine state
            self.completed_nodes.append(target_node_id)
            self.node_outcomes[target_node_id] = outcome

            # Write per-node status.json BEFORE emitting so hook bridge can copy it
            self._write_node_status(target_node_id, outcome, node_duration)

            await self._emit(
                PIPELINE_NODE_COMPLETE,
                {
                    "node_id": target_node_id,
                    "status": outcome.status.value,
                    "duration_ms": node_duration,
                    "notes": outcome.notes,
                    "failure_reason": outcome.failure_reason,
                    "session_id": outcome.session_id,
                    "execution_index": branch_execution_index,  # NEW — graph-level visit count
                },
            )

            return {
                "node_id": target_node_id,
                "status": outcome.status.value,
                "notes": outcome.notes,
                "failure_reason": outcome.failure_reason,
                "context_updates": outcome.context_updates,
            }

        # Execute all branches concurrently with bounded parallelism.
        # Read max_parallel from the source node's attrs (matching ParallelHandler's
        # convention for shape=component nodes).  Default 4 mirrors ParallelHandler.
        source_node = self.graph.nodes.get(edges[0].from_node) if edges else None
        max_parallel = int(
            source_node.attrs.get("max_parallel", 4) if source_node else 4
        )
        semaphore = asyncio.Semaphore(max_parallel)

        async def bounded_run_branch(target_node_id: str) -> dict[str, Any]:
            async with semaphore:
                return await run_branch(target_node_id)

        tasks = [bounded_run_branch(edge.to_node) for edge in edges]
        results = list(await asyncio.gather(*tasks))

        # Apply context_updates from all branches
        for result in results:
            updates = result.get("context_updates")
            if updates:
                self.context.update(updates)

        return results

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

    def _resolve_failure_retry_target(self, node: Node) -> Node | None:
        """Resolve a retry target when no edge matches after node execution.

        Fallback chain (first match wins):
        1. node.retry_target
        2. node.fallback_retry_target
        3. graph.retry_target
        4. graph.fallback_retry_target

        Returns the target Node or None if no valid target exists.
        """
        target_id = (
            node.attrs.get("retry_target")
            or node.attrs.get("fallback_retry_target")
            or self.graph.graph_attrs.get("retry_target")
            or self.graph.graph_attrs.get("fallback_retry_target")
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

    # -- Event helpers -------------------------------------------------------

    async def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit an event via hooks, if provided."""
        if self.hooks is not None:
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
