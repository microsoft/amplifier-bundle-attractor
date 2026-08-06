"""Tests for pipeline_status query tool."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_pipeline_status import (
    _FILTERS_SORTED as _MODULE_FILTERS_SORTED,
)
from amplifier_module_tool_pipeline_status import (
    PipelineStatusTool,
    mount,
)

# -- Helpers ---------------------------------------------------------------


def _make_state_dict(
    *,
    pipeline_id: str = "test-pipeline",
    goal: str = "Build a widget",
    status: str = "running",
    current_node: str | None = "plan",
    nodes_completed: int = 1,
    nodes_total: int = 3,
    total_tokens_in: int = 500,
    total_tokens_out: int = 200,
    total_llm_calls: int = 2,
) -> dict:
    """Create a minimal PipelineRunState-like dict for testing."""
    return {
        "pipeline_id": pipeline_id,
        "dot_source": "",
        "goal": goal,
        "nodes": {},
        "edges": [],
        "status": status,
        "current_node": current_node,
        "execution_path": ["plan"],
        "branches_taken": [],
        "node_runs": {},
        "edge_decisions": [],
        "loop_iterations": {},
        "goal_gate_checks": [],
        "parallel_branches": {},
        "subgraph_runs": {},
        "human_interactions": [],
        "supervisor_cycles": {},
        "total_elapsed_ms": 5000,
        "total_llm_calls": total_llm_calls,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_tokens_cached": 0,
        "total_tokens_reasoning": 0,
        "nodes_completed": nodes_completed,
        "nodes_total": nodes_total,
        "timing": {},
        "errors": [],
    }


def _make_coordinator_with_state(state_dict: dict | None = None):
    """Create a mock coordinator that returns state from collect_contributions."""
    coordinator = MagicMock()
    if state_dict is not None:
        coordinator.collect_contributions = AsyncMock(return_value=[state_dict])
    else:
        coordinator.collect_contributions = AsyncMock(return_value=[])
    return coordinator


# -- Tool metadata tests ---------------------------------------------------


def test_tool_name():
    """Tool has correct name."""
    tool = PipelineStatusTool(config={})
    assert tool.name == "pipeline_status"


def test_tool_description():
    """Tool has a meaningful description."""
    tool = PipelineStatusTool(config={})
    assert "pipeline" in tool.description.lower()
    assert "status" in tool.description.lower()


def test_tool_input_schema():
    """Tool exposes correct input schema with filter parameter."""
    tool = PipelineStatusTool(config={})
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "filter" in schema["properties"]
    assert "full" in schema["properties"]["filter"]["enum"]
    assert "metrics" in schema["properties"]["filter"]["enum"]
    assert "current" in schema["properties"]["filter"]["enum"]


# -- Mount tests -----------------------------------------------------------


def test_mount_is_importable():
    """mount() should be importable from the package."""
    assert callable(mount)


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_tool():
    """mount() should register the tool via coordinator.mount('tools', ...)."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    await mount(coordinator)

    coordinator.mount.assert_called_once()
    args = coordinator.mount.call_args
    assert args.args[0] == "tools"
    assert args.kwargs.get("name") == "pipeline_status"


# -- Execute tests ---------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_full_returns_state():
    """execute with filter='full' returns the complete state."""
    state = _make_state_dict()
    coordinator = _make_coordinator_with_state(state)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({"filter": "full"})

    assert result.success
    assert result.output["pipeline_id"] == "test-pipeline"
    assert result.output["status"] == "running"
    assert result.output["goal"] == "Build a widget"


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_default_is_full():
    """execute with no filter defaults to 'full'."""
    state = _make_state_dict()
    coordinator = _make_coordinator_with_state(state)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({})

    assert result.success
    assert result.output["pipeline_id"] == "test-pipeline"


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_metrics_filter():
    """execute with filter='metrics' returns only aggregate metrics."""
    state = _make_state_dict(total_tokens_in=1000, total_tokens_out=500)
    coordinator = _make_coordinator_with_state(state)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({"filter": "metrics"})

    assert result.success
    assert result.output["total_tokens_in"] == 1000
    assert result.output["total_tokens_out"] == 500
    assert result.output["total_llm_calls"] == 2
    # Should NOT include full state fields like execution_path
    assert "execution_path" not in result.output


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_current_filter():
    """execute with filter='current' returns current node and progress info."""
    state = _make_state_dict(
        current_node="impl", nodes_completed=1, nodes_total=3, status="running"
    )
    coordinator = _make_coordinator_with_state(state)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({"filter": "current"})

    assert result.success
    assert result.output["current_node"] == "impl"
    assert result.output["status"] == "running"
    assert result.output["nodes_completed"] == 1
    assert result.output["nodes_total"] == 3
    # Should NOT include full state fields like node_runs
    assert "node_runs" not in result.output


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_no_pipeline_running():
    """execute returns informative message when no pipeline is running."""
    coordinator = _make_coordinator_with_state(None)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({})

    assert result.success
    assert "no pipeline" in result.output["message"].lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_invalid_filter():
    """execute with invalid filter returns error."""
    state = _make_state_dict()
    coordinator = _make_coordinator_with_state(state)
    tool = PipelineStatusTool(config={}, coordinator=coordinator)

    result = await tool.execute({"filter": "bogus"})

    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_without_coordinator():
    """Tool works without a coordinator (returns no-pipeline message)."""
    tool = PipelineStatusTool(config={})

    result = await tool.execute({})

    assert result.success
    assert "no pipeline" in result.output["message"].lower()


# -- Schema determinism (prompt-cache stability) ----------------------------
#
# Regression guard for the S4 structural pattern (two independent `enum`
# call sites that must never drift apart). Full story:
# docs/designs/RECURRING-BUG-CLASSES.md (S4). See
# test_schema_serialization_is_deterministic_across_processes below for the
# actual cross-process proof.


def test_schema_enum_matches_canonical_order():
    """The enum must be exactly the module's canonical sorted order."""
    tool = PipelineStatusTool(config={})
    assert tool.input_schema["properties"]["filter"]["enum"] == list(
        _MODULE_FILTERS_SORTED
    )


def test_schema_enum_contains_expected_members():
    """Regression guard: sorting must never silently drop or add a value."""
    tool = PipelineStatusTool(config={})
    enum = tool.input_schema["properties"]["filter"]["enum"]
    assert set(enum) == {"full", "metrics", "current"}
    assert len(enum) == 3


_SCHEMA_PROBE = (
    "import json\n"
    "from amplifier_module_tool_pipeline_status import PipelineStatusTool\n"
    "tool = PipelineStatusTool(config={})\n"
    "print(json.dumps(tool.input_schema, sort_keys=False, separators=(',', ':')))\n"
)


def test_schema_serialization_is_deterministic_across_processes():
    """The REAL eval: the serialized input_schema must be byte-identical across
    independent interpreter starts.

    A same-process test cannot catch this bug class because PYTHONHASHSEED is
    fixed for the lifetime of one interpreter -- frozenset iteration order
    only varies *between* process starts. This spawns N independent
    subprocesses (matching how the real production incident manifested: a
    fresh schema hash at every process restart) and asserts they all produce
    the identical serialized schema.
    """
    n_procs = 8
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)  # let each subprocess pick its own random seed

    canon_outputs: list[str] = []
    for _ in range(n_procs):
        result = subprocess.run(
            [sys.executable, "-c", _SCHEMA_PROBE],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            check=False,
        )
        assert result.returncode == 0, (
            f"Probe subprocess failed (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        canon_outputs.append(result.stdout.strip())

    hashes = {hashlib.sha256(c.encode()).hexdigest()[:16] for c in canon_outputs}
    distinct_orders = sorted(set(canon_outputs))
    assert len(hashes) == 1, (
        f"Expected 1 distinct input_schema serialization across {n_procs} "
        f"independent processes, got {len(hashes)}. The schema's byte "
        "representation is not stable across interpreter restarts (a "
        "PYTHONHASHSEED-dependent frozenset iteration order), which "
        "invalidates the ENTIRE Anthropic prompt cache on every process "
        f"restart.\nDistinct serializations observed:\n" + "\n".join(distinct_orders)
    )
