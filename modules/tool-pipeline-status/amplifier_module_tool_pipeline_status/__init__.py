"""Pipeline Status Query Tool Module for Amplifier.

Provides a tool that reads from the ``pipeline.state`` contribution channel
and returns the current PipelineRunState as structured JSON.  Supports
optional filtering to return only metrics or current-node info.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import logging
from typing import Any

__all__ = ["PipelineStatusTool", "mount"]

logger = logging.getLogger(__name__)

VALID_FILTERS = frozenset({"full", "metrics", "current"})

# Canonical, stable ordering of VALID_FILTERS, read by BOTH the schema
# `enum` and the error-message join below so they can never drift apart
# (S4 -- partial-coverage symmetry). A tuple, not a list, so the shared
# source of truth can't be mutated through a consumer. Do NOT reorder and
# do NOT inline `sorted(VALID_FILTERS)` at either consumer again -- that
# reopens the prompt-cache bug. Full story:
# docs/designs/RECURRING-BUG-CLASSES.md (S4). Regression guard:
# test_schema_serialization_is_deterministic_across_processes.
_FILTERS_SORTED: tuple[str, ...] = tuple(sorted(VALID_FILTERS))

# Keys returned for the "metrics" filter
_METRICS_KEYS = frozenset(
    {
        "pipeline_id",
        "status",
        "total_elapsed_ms",
        "total_llm_calls",
        "total_tokens_in",
        "total_tokens_out",
        "total_tokens_cached",
        "total_tokens_reasoning",
        "nodes_completed",
        "nodes_total",
    }
)

# Keys returned for the "current" filter
_CURRENT_KEYS = frozenset(
    {
        "pipeline_id",
        "goal",
        "status",
        "current_node",
        "nodes_completed",
        "nodes_total",
        "total_elapsed_ms",
        "execution_path",
    }
)


class PipelineStatusTool:
    """Query the current pipeline execution state.

    Reads state from the ``pipeline.state`` contribution channel via the
    coordinator and returns it as structured JSON.
    """

    name = "pipeline_status"
    description = (
        "Query the current pipeline execution status. Returns pipeline state "
        "including progress, current node, token metrics, and errors. "
        "Use filter='metrics' for just token/timing aggregates, or "
        "filter='current' for just the current node and progress."
    )

    def __init__(self, config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = config
        self.coordinator = coordinator

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": list(_FILTERS_SORTED),
                    "description": (
                        "Detail level: 'full' (default) returns everything, "
                        "'metrics' returns only aggregate metrics, "
                        "'current' returns current node and progress info."
                    ),
                },
            },
        }

    async def execute(self, input: dict[str, Any]) -> Any:
        """Execute the pipeline_status tool.

        Args:
            input: Tool input with optional 'filter' parameter.

        Returns:
            ToolResult with the pipeline state (filtered as requested).
        """
        from amplifier_core import ToolResult

        filter_mode = input.get("filter", "full")

        # Validate filter
        if filter_mode not in VALID_FILTERS:
            error_msg = (
                f"Invalid filter: {filter_mode!r}. "
                f"Must be one of: {', '.join(_FILTERS_SORTED)}"
            )
            return ToolResult(
                success=False,
                output=error_msg,
                error={"message": error_msg},
            )

        # Collect state from contribution channel
        state_dict = await self._get_state()
        if state_dict is None:
            return ToolResult(
                success=True,
                output={"message": "No pipeline is currently running."},
            )

        # Apply filter
        if filter_mode == "full":
            output = state_dict
        elif filter_mode == "metrics":
            output = {k: v for k, v in state_dict.items() if k in _METRICS_KEYS}
        elif filter_mode == "current":
            output = {k: v for k, v in state_dict.items() if k in _CURRENT_KEYS}
        else:
            output = state_dict

        return ToolResult(success=True, output=output)

    async def _get_state(self) -> dict[str, Any] | None:
        """Retrieve state from the pipeline.state contribution channel."""
        if self.coordinator is None:
            return None

        contributions = await self.coordinator.collect_contributions("pipeline.state")

        # collect_contributions returns a list; we want the first non-None item
        for contrib in contributions:
            if contrib is not None:
                # If it's a dataclass with to_dict(), serialize it
                if hasattr(contrib, "to_dict"):
                    return contrib.to_dict()
                # If it's already a dict, return it directly
                if isinstance(contrib, dict):
                    return contrib
        return None


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the pipeline_status tool.

    Args:
        coordinator: Module coordinator for registering tools.
        config: Module configuration.
    """
    config = config or {}
    tool = PipelineStatusTool(config, coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted pipeline_status tool")
