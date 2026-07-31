"""Start node handler.

Returns SUCCESS immediately. The start node (shape=Mdiamond) is the
entry point of the pipeline and performs no work.

Spec coverage: HSTART-001–002, Section 4.3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus

if TYPE_CHECKING:
    from ..engine import PipelineEngine


class StartHandler:
    """Handler for start nodes (shape=Mdiamond)."""

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Return SUCCESS immediately."""
        # EXTENSIONS.md §25: structural no-op — deterministic SUCCESS that
        # cannot be an LLM default, so it is an explicit verdict.
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"Start node: {node.id}",
            is_explicit=True,
        )
