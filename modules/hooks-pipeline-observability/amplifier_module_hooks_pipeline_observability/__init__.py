"""Pipeline observability hooks — state aggregator, status bar, and event persistence."""

from __future__ import annotations

import logging
from typing import Any

from .aggregator import StateAggregator
from .status_bar import StatusBarContributor

logger = logging.getLogger(__name__)

# Amplifier module metadata
__amplifier_module_type__ = "hooks"

# All pipeline events this module subscribes to
_PIPELINE_EVENTS = [
    "pipeline:start",
    "pipeline:complete",
    "pipeline:node_start",
    "pipeline:node_complete",
    "pipeline:edge_selected",
    "pipeline:checkpoint",
    "pipeline:goal_gate_check",
    "pipeline:error",
    "pipeline:parallel_started",
    "pipeline:parallel_branch_started",
    "pipeline:parallel_branch_completed",
    "pipeline:parallel_completed",
    "pipeline:interview_started",
    "pipeline:interview_completed",
    "pipeline:interview_timeout",
    "pipeline:stage_retrying",
    "pipeline:stage_failed",
    "provider:response",
    "model:resolved",
]

# Map event names to StateAggregator handler method names
_AGGREGATOR_HANDLER_MAP: dict[str, str] = {
    "pipeline:start": "handle_pipeline_start",
    "pipeline:complete": "handle_pipeline_complete",
    "pipeline:node_start": "handle_node_start",
    "pipeline:node_complete": "handle_node_complete",
    "pipeline:edge_selected": "handle_edge_selected",
    "pipeline:checkpoint": "handle_checkpoint",
    "pipeline:goal_gate_check": "handle_goal_gate_check",
    "pipeline:error": "handle_error",
    "pipeline:parallel_started": "handle_parallel_started",
    "pipeline:parallel_branch_started": "handle_parallel_branch_started",
    "pipeline:parallel_branch_completed": "handle_parallel_branch_completed",
    "pipeline:parallel_completed": "handle_parallel_completed",
    "pipeline:interview_started": "handle_interview_started",
    "pipeline:interview_completed": "handle_interview_completed",
    "pipeline:interview_timeout": "handle_interview_timeout",
    "pipeline:stage_retrying": "handle_stage_retrying",
    "pipeline:stage_failed": "handle_stage_failed",
    "provider:response": "handle_provider_response",
    "model:resolved": "handle_model_resolved",
}


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount pipeline observability hooks into the Amplifier coordinator.

    Registers:
    1. StateAggregator — subscribes to all 18 observability events, maintains PipelineRunState
    2. StatusBarContributor — compact system-reminder for context injection
    3. pipeline.state contribution channel — makes state queryable
    4. observability.events contribution — ensures pipeline events are discoverable
    """
    hooks = coordinator.get("hooks")
    aggregator = StateAggregator()

    # Register aggregator handlers for all pipeline events
    for event_name, handler_name in _AGGREGATOR_HANDLER_MAP.items():
        handler = getattr(aggregator, handler_name)
        hooks.register(event_name, handler, name="pipeline-observability")

    # Status bar contributor for context injection
    status_bar = StatusBarContributor(aggregator)
    coordinator.register_contributor(
        "system-reminders",
        "pipeline-status",
        status_bar.contribute,
    )

    # Register pipeline.state contribution channel
    coordinator.register_contributor(
        "pipeline.state",
        "hooks-pipeline-observability",
        aggregator.get_state,
    )

    # Register pipeline events for observability.events discovery (Layer 3)
    # hooks-logging reads via get_capability(), so we must use register_capability()
    # Append to any existing events already registered by other modules
    existing_events = coordinator.get_capability("observability.events") or []
    existing_events.extend(_PIPELINE_EVENTS)
    coordinator.register_capability("observability.events", existing_events)

    logger.info("Mounted hooks-pipeline-observability (aggregator + status bar)")
