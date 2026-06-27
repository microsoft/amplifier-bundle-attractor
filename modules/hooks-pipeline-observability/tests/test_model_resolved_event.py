"""model:resolved wiring — discovery contribution, aggregator subscription, and state capture.

The same ``_PIPELINE_EVENTS`` entry that the aggregator subscribes to is also
contributed to the ``observability.events`` channel, which is what Context
Intelligence / any logging recorder discovers. So this one event name closes
both the live-state gap (StateAggregator) and the durable-capture gap (recorders).
"""

import pytest

import amplifier_module_hooks_pipeline_observability as obs
from amplifier_module_hooks_pipeline_observability.aggregator import StateAggregator


def test_model_resolved_is_discoverable_and_subscribed():
    # observability.events discovery contribution (CI + any recorder read this)
    assert "model:resolved" in obs._PIPELINE_EVENTS
    # aggregator handler subscription
    assert obs._AGGREGATOR_HANDLER_MAP["model:resolved"] == "handle_model_resolved"


@pytest.mark.asyncio
async def test_aggregator_records_resolution():
    agg = StateAggregator()
    await agg.handle_pipeline_start(
        "pipeline:start", {"graph_name": "g", "node_count": 1}
    )
    await agg.handle_model_resolved(
        "model:resolved",
        {
            "raw": "claude-sonnet-4-*",
            "resolved": "claude-sonnet-4-6",
            "provider": "anthropic",
            "pattern": "claude-sonnet-4-*",
        },
    )
    state = agg.get_state()
    assert state is not None
    assert state.resolved_models == {"claude-sonnet-4-*": "claude-sonnet-4-6"}


@pytest.mark.asyncio
async def test_resolution_is_safe_noop_without_pipeline_state():
    agg = StateAggregator()
    # No pipeline:start yet -> no state -> must be a safe no-op, never raise.
    await agg.handle_model_resolved(
        "model:resolved", {"raw": "sonnet", "resolved": "claude-sonnet-4-6"}
    )
    assert agg.get_state() is None
