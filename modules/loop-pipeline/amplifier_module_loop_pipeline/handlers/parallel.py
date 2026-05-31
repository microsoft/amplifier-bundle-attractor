"""Parallel handler — fans out execution to concurrent branches.

Each parallel branch receives an isolated clone of the parent context
and runs independently. The handler waits for all branches to complete
(or applies a configurable join policy) before returning.

Spec coverage: PAR-001–013, CONC-001–004, Section 4.8.

Node attributes:
    max_parallel   – Maximum concurrent branches (default 4).
    join_policy    – wait_all | first_success | k_of_n | quorum (default wait_all).
    error_policy   – fail_fast | continue | ignore (default continue).
    min_success    – Required successes for k_of_n (default 1).
    quorum_fraction – Required fraction for quorum (default 0.5).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import TYPE_CHECKING, Any, Callable

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus

if TYPE_CHECKING:
    from ..engine import PipelineEngine

logger = logging.getLogger(__name__)


class ParallelHandler:
    """Handler for parallel fan-out nodes (shape=component).

    Spawns concurrent branches with isolated contexts, respects
    bounded parallelism, and evaluates a join policy on results.
    """

    def __init__(
        self,
        hooks: Any = None,
    ) -> None:
        """Initialize the parallel handler.

        Args:
            hooks: Optional hooks object for event emission.
        """
        self._hooks = hooks

    async def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit an event via hooks, if provided."""
        if self._hooks is not None:
            await self._hooks.emit(event_name, data)

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Execute a parallel node by fanning out to all outgoing edges.

        1. Identify fan-out edges (all outgoing edges from this node).
        2. Clone context per branch for isolation.
        3. Execute branches concurrently with bounded parallelism.
        4. Store results in parent context for downstream fan-in.
        5. Evaluate join policy and return aggregate outcome.
        """
        from ..pipeline_events import (
            PIPELINE_NODE_COMPLETE,
            PIPELINE_NODE_START,
            PIPELINE_PARALLEL_BRANCH_COMPLETED,
            PIPELINE_PARALLEL_BRANCH_STARTED,
            PIPELINE_PARALLEL_COMPLETED,
            PIPELINE_PARALLEL_STARTED,
        )

        branches = graph.outgoing_edges(node.id)
        if not branches:
            return Outcome(
                status=StageStatus.SUCCESS,
                notes="Parallel node with no branches",
            )

        max_parallel = int(node.attrs.get("max_parallel", 4))
        join_policy = str(node.attrs.get("join_policy", "wait_all"))
        error_policy = str(node.attrs.get("error_policy", "continue"))
        semaphore = asyncio.Semaphore(max_parallel)

        await self._emit(
            PIPELINE_PARALLEL_STARTED,
            {"node_id": node.id, "branch_count": len(branches)},
        )

        async def run_branch(target_node_id: str) -> dict[str, Any]:
            """Execute a single branch with bounded concurrency."""
            async with semaphore:
                await self._emit(
                    PIPELINE_PARALLEL_BRANCH_STARTED,
                    {"node_id": node.id, "branch_node_id": target_node_id},
                )

                # Resolve handler type for the branch target node so consumers
                # that read events don't have to look up the graph separately.
                target_node = graph.nodes.get(target_node_id)
                branch_handler_type = (
                    (target_node.type or target_node.shape)
                    if target_node is not None
                    else "unknown"
                )

                # Emit the standard per-node start event so the parent loop's
                # event stream includes all branch nodes (Fix #1).  The
                # via_parallel=True marker lets consumers distinguish these
                # from main-loop node events without special-casing the shape.
                await self._emit(
                    PIPELINE_NODE_START,
                    {
                        "node_id": target_node_id,
                        "handler_type": branch_handler_type,
                        "attempt": 1,
                        "execution_index": 1,
                        "via_parallel": True,
                    },
                )

                branch_start = time.monotonic()
                branch_context = context.clone()
                if engine is None:
                    outcome = Outcome(
                        status=StageStatus.FAIL,
                        notes=f"ParallelHandler requires engine to be passed via execute(engine=...). Branch '{target_node_id}' cannot execute.",
                        failure_reason="No engine configured",
                    )
                else:
                    try:
                        # Move 1: give each branch its own engine so run_subgraph
                        # uses an isolated handler_registry (and therefore isolated
                        # backend _thread_transcripts / _completed_nodes per branch).
                        # Fall back to the parent engine for mock/stub engines used
                        # in unit tests that predate clone_for_branch.
                        clone_fn = getattr(engine, "clone_for_branch", None)
                        if clone_fn is not None:
                            branch_engine = clone_fn(context=branch_context)
                            outcome = await branch_engine.run_subgraph(
                                target_node_id, context=branch_context
                            )
                        else:
                            outcome = await engine.run_subgraph(
                                target_node_id, context=branch_context
                            )
                    except Exception as e:
                        logger.warning(
                            "Branch %s raised exception: %s", target_node_id, e
                        )
                        outcome = Outcome(
                            status=StageStatus.FAIL,
                            failure_reason=str(e),
                        )

                branch_duration_ms = (time.monotonic() - branch_start) * 1000

                # Emit the standard per-node complete event (Fix #1).
                await self._emit(
                    PIPELINE_NODE_COMPLETE,
                    {
                        "node_id": target_node_id,
                        "status": outcome.status.value,
                        "duration_ms": branch_duration_ms,
                        "notes": outcome.notes,
                        "failure_reason": outcome.failure_reason,
                        "via_parallel": True,
                    },
                )

                await self._emit(
                    PIPELINE_PARALLEL_BRANCH_COMPLETED,
                    {
                        "node_id": node.id,
                        "branch_node_id": target_node_id,
                        "status": outcome.status.value,
                    },
                )

                return {
                    "node_id": target_node_id,
                    "status": outcome.status.value,
                    "notes": outcome.notes,
                    "failure_reason": outcome.failure_reason,
                    "context_updates": outcome.context_updates,
                }

        # Dispatch based on error policy and join policy
        if error_policy == "fail_fast":
            results = await _run_fail_fast(branches, run_branch, semaphore)
        elif join_policy == "first_success":
            results = await _run_first_success(branches, run_branch)
        elif join_policy == "k_of_n":
            k = int(node.attrs.get("min_success", 1))
            results = await _run_k_of_n(branches, run_branch, k)
        else:
            # Default (continue) and ignore both run all branches
            tasks = [run_branch(edge.to_node) for edge in branches]
            results = list(await asyncio.gather(*tasks))

        # Apply ignore error policy: filter out failures before storing
        if error_policy == "ignore":
            results = [
                r for r in results if r["status"] in ("success", "partial_success")
            ]

        # Store results in parent context for fan-in
        context.set("parallel.results", results)
        context.set("parallel.count", len(results))

        await self._emit(
            PIPELINE_PARALLEL_COMPLETED,
            {
                "node_id": node.id,
                "branch_count": len(branches),
                "result_count": len(results),
            },
        )

        # Evaluate join policy
        return _apply_join_policy(results, join_policy, node_attrs=node.attrs)


async def _run_fail_fast(
    branches: list,
    run_branch: Callable,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Execute branches with fail_fast: cancel remaining on first failure.

    Uses asyncio tasks with a shared cancellation event. When any branch
    completes with a failure status, remaining branches are cancelled.
    """
    results: list[dict[str, Any]] = []
    failure_event = asyncio.Event()

    async def guarded_branch(edge) -> dict[str, Any] | None:
        """Run a branch but bail early if failure_event is set."""
        if failure_event.is_set():
            return None
        result = await run_branch(edge.to_node)
        if result["status"] == "fail":
            failure_event.set()
        return result

    tasks = [asyncio.create_task(guarded_branch(edge)) for edge in branches]

    # Wait with FIRST_EXCEPTION so we can cancel promptly
    done: set[asyncio.Task] = set()
    pending: set[asyncio.Task] = set(tasks)

    while pending:
        newly_done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )
        done.update(newly_done)

        # Check if any completed task indicates failure
        for task in newly_done:
            result = task.result()
            if result is not None:
                results.append(result)
                if result["status"] == "fail":
                    # Cancel remaining pending tasks
                    for p in pending:
                        p.cancel()
                    # Collect any already-done results from pending
                    if pending:
                        cancelled_done, _ = await asyncio.wait(pending)
                        for ct in cancelled_done:
                            try:
                                cr = ct.result()
                                if cr is not None:
                                    results.append(cr)
                            except asyncio.CancelledError:
                                pass
                    return results

    return results


async def _run_first_success(
    branches: list,
    run_branch: Callable,
) -> list[dict[str, Any]]:
    """Execute branches with first_success: cancel remaining on first success.

    Uses asyncio tasks with incremental completion. When any branch
    completes with a success status, remaining branches are cancelled
    and the collected results are returned immediately.
    """
    results: list[dict[str, Any]] = []
    tasks = [asyncio.create_task(run_branch(edge.to_node)) for edge in branches]
    pending: set[asyncio.Task] = set(tasks)

    while pending:
        newly_done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )

        for task in newly_done:
            result = task.result()
            results.append(result)
            if result["status"] in ("success", "partial_success"):
                # Cancel remaining pending tasks
                for p in pending:
                    p.cancel()
                # Collect any already-completed results from pending
                if pending:
                    cancelled_done, _ = await asyncio.wait(pending)
                    for ct in cancelled_done:
                        try:
                            cr = ct.result()
                            if cr is not None:
                                results.append(cr)
                        except asyncio.CancelledError:
                            pass
                return results

    return results


async def _run_k_of_n(
    branches: list,
    run_branch: Callable,
    k: int,
) -> list[dict[str, Any]]:
    """Execute branches with k_of_n: cancel remaining when k successes reached.

    Tracks completed branches incrementally. Returns early when k branches
    have succeeded. Also returns early when the threshold becomes impossible
    (too many failures for remaining branches to reach k).
    """
    results: list[dict[str, Any]] = []
    tasks = [asyncio.create_task(run_branch(edge.to_node)) for edge in branches]
    pending: set[asyncio.Task] = set(tasks)
    success_count = 0

    while pending:
        newly_done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )

        for task in newly_done:
            result = task.result()
            results.append(result)
            if result["status"] in ("success", "partial_success"):
                success_count += 1

        # Check if threshold is met
        if success_count >= k:
            for p in pending:
                p.cancel()
            if pending:
                cancelled_done, _ = await asyncio.wait(pending)
                for ct in cancelled_done:
                    try:
                        cr = ct.result()
                        if cr is not None:
                            results.append(cr)
                    except asyncio.CancelledError:
                        pass
            return results

        # Check if threshold is impossible: too many failures already
        remaining = len(pending)
        if success_count + remaining < k:
            # Even if all remaining succeed, can't reach k
            for p in pending:
                p.cancel()
            if pending:
                cancelled_done, _ = await asyncio.wait(pending)
                for ct in cancelled_done:
                    try:
                        cr = ct.result()
                        if cr is not None:
                            results.append(cr)
                    except asyncio.CancelledError:
                        pass
            return results

    return results


def _apply_join_policy(
    results: list[dict[str, Any]],
    policy: str,
    node_attrs: dict[str, Any] | None = None,
) -> Outcome:
    """Evaluate a join policy against branch results.

    Supports: wait_all, first_success, k_of_n, quorum.
    Unknown policies fall back to wait_all behaviour.
    """
    if not results:
        return Outcome(status=StageStatus.SUCCESS, notes="No branches")

    attrs = node_attrs or {}

    success_count = sum(
        1 for r in results if r["status"] in ("success", "partial_success")
    )
    fail_count = sum(1 for r in results if r["status"] == "fail")
    total = len(results)

    # -- wait_all --------------------------------------------------------
    if policy == "wait_all":
        if fail_count == 0:
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=f"All {total} branches succeeded",
            )
        return Outcome(
            status=StageStatus.PARTIAL_SUCCESS,
            notes=f"{success_count}/{total} branches succeeded, {fail_count} failed",
        )

    # -- first_success ---------------------------------------------------
    if policy == "first_success":
        if success_count > 0:
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=f"At least one branch succeeded ({success_count}/{total})",
            )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=f"No branches succeeded out of {total}",
        )

    # -- k_of_n ----------------------------------------------------------
    if policy == "k_of_n":
        k = int(attrs.get("min_success", 1))
        if success_count >= k:
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=f"{success_count}/{total} branches succeeded (needed {k})",
            )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Only {success_count}/{k} required branches succeeded "
                f"(out of {total} total)"
            ),
        )

    # -- quorum ----------------------------------------------------------
    if policy == "quorum":
        fraction = float(attrs.get("quorum_fraction", 0.5))
        needed = math.ceil(total * fraction)
        if success_count >= needed:
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=(
                    f"{success_count}/{total} branches succeeded "
                    f"(needed {needed}, fraction={fraction})"
                ),
            )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Only {success_count}/{needed} required branches succeeded "
                f"(fraction={fraction}, total={total})"
            ),
        )

    # -- Unknown policy: fall back to wait_all ---------------------------
    if fail_count == 0:
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"All {total} branches succeeded",
        )
    return Outcome(
        status=StageStatus.PARTIAL_SUCCESS,
        notes=f"{success_count}/{total} branches succeeded",
    )
