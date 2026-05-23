"""Conditional node handler.

A no-op that returns SUCCESS immediately.  Diamond nodes (shape=diamond)
are routing nodes; the engine's edge-selection algorithm (§3.3) handles
actual routing from the diamond node based on outgoing edge conditions.
The handler itself performs no work.

Spec coverage: Section 4.7 (ConditionalHandler).

Node attributes:
    None — this handler is intentionally attribute-free.
"""

from __future__ import annotations

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus


class ConditionalHandler:
    """Handler for conditional (diamond) nodes (shape=diamond).

    Returns SUCCESS immediately.  Routing is handled by the execution
    engine's edge selection algorithm (spec §3.3), not by this handler.

    This is the correct implementation for decision-point / routing nodes
    that use edge conditions to direct flow.  Any LLM-driven gate that
    needs to *reason* about which branch to take should use shape=box
    (CodergenHandler) instead, with conditional edges to its successors.
    """

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
    ) -> Outcome:
        """Return SUCCESS immediately.  Routing is handled by the engine."""
        return Outcome(status=StageStatus.SUCCESS, notes=f"Conditional node: {node.id}")
