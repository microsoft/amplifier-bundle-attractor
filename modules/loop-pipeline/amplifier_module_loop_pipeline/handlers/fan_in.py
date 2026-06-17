"""Fan-in handler — consolidates results from a parallel node.

Reads ``parallel.results`` from context, ranks candidates by outcome
status, selects the best one, and records the winner in context for
downstream nodes.

Spec coverage: FANIN-001–005, Section 4.9.

Heuristic ranking (best first):
    SUCCESS > PARTIAL_SUCCESS > RETRY > FAIL

Ties are broken by node ID (lexicographic ascending).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..engine import PipelineEngine

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)

# Ranking for heuristic selection: lower number = better
_STATUS_RANK: dict[str, int] = {
    "success": 0,
    "partial_success": 1,
    "retry": 2,
    "skipped": 3,
    "fail": 4,
}


@runtime_checkable
class FanInBackend(Protocol):
    """Protocol for LLM-based fan-in evaluation (M-17).

    Implementations receive the node prompt, parallel results, and node,
    and return the node_id of the best candidate.
    """

    async def evaluate(
        self, prompt: str, results: list[dict[str, Any]], node: Node
    ) -> str: ...


class FanInHandler:
    """Handler for fan-in nodes (shape=tripleoctagon).

    Evaluates parallel results and selects the best candidate.
    When a backend is provided and the node has a prompt, uses LLM-based
    evaluation (M-17). Otherwise falls back to heuristic ranking.
    """

    def __init__(self, backend: FanInBackend | None = None) -> None:
        self._backend = backend

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Evaluate parallel results and select the best candidate.

        1. Read parallel.results from context.
        2. If node has prompt and backend, use LLM evaluation (M-17).
        3. Otherwise rank candidates by status (heuristic).
        4. Record winner in context.
        5. Return SUCCESS if at least one candidate succeeded.
        """
        results: list[dict[str, Any]] | None = context.get("parallel.results")

        if not results:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason="No parallel results to evaluate",
            )

        # M-17: Use LLM-based evaluation when prompt and backend available
        best: dict[str, Any] | None = None
        if node.prompt and self._backend is not None:
            try:
                best_id = await self._backend.evaluate(node.prompt, results, node)
                # Find the result dict matching the returned ID
                for r in results:
                    if r.get("node_id") == best_id:
                        best = r
                        break
                if best is None:
                    logger.warning(
                        "Backend returned unknown node_id '%s', "
                        "falling back to heuristic",
                        best_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Fan-in backend evaluation failed: %s, falling back to heuristic",
                    exc,
                )

        # Fallback: heuristic selection
        if best is None:
            best = _heuristic_select(results)

        if best is None:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason="No parallel results to evaluate",
            )

        best_status = best.get("status", "fail")
        best_id = best.get("node_id", "unknown")

        # Record winner in context
        context.set("parallel.fan_in.best_id", best_id)
        context.set("parallel.fan_in.best_status", best_status)
        # Spec §4.8: canonical key is best_outcome; keep best_status for compat
        context.set("parallel.fan_in.best_outcome", best_status)

        # If best candidate failed, fan-in fails
        if best_status == "fail":
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"All candidates failed. Best: {best_id}",
                notes=best.get("notes"),
            )

        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Selected best candidate: {best_id} ({best_status})",
            context_updates={
                "parallel.fan_in.best_id": best_id,
                "parallel.fan_in.best_status": best_status,
                # Spec §4.8: canonical key is best_outcome; keep best_status for compat
                "parallel.fan_in.best_outcome": best_status,
            },
        )


def _heuristic_select(
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the best candidate by status ranking, then node ID.

    Spec Section 4.9: heuristic_select algorithm.
    """
    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda c: (
            _STATUS_RANK.get(c.get("status", "fail"), 99),
            c.get("node_id", ""),
        ),
    )[0]
