"""Tests for report_outcome tool."""

import hashlib
import os
import subprocess
import sys

import pytest
from amplifier_module_tool_report_outcome import (
    _STATUSES_SORTED as _MODULE_STATUSES_SORTED,
)
from amplifier_module_tool_report_outcome import ReportOutcomeTool

VALID_STATUSES = ["success", "fail", "partial_success", "retry"]


@pytest.mark.asyncio(loop_scope="session")
async def test_report_success():
    """Report a successful outcome with preferred label."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute(
        {
            "status": "success",
            "preferred_label": "tests_pass",
            "notes": "All 42 tests passing",
        }
    )
    assert result.success
    assert result.output["status"] == "success"
    assert result.output["preferred_label"] == "tests_pass"
    assert result.output["notes"] == "All 42 tests passing"


@pytest.mark.asyncio(loop_scope="session")
async def test_report_fail():
    """Report a failed outcome with failure reason."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute(
        {
            "status": "fail",
            "failure_reason": "3 tests still failing",
        }
    )
    assert result.success  # Tool itself succeeds
    assert result.output["status"] == "fail"
    assert result.output["failure_reason"] == "3 tests still failing"


@pytest.mark.asyncio(loop_scope="session")
async def test_report_partial_success():
    """Report partial success with context updates."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute(
        {
            "status": "partial_success",
            "context_updates": {"tests_passing": 39, "tests_failing": 3},
            "notes": "Most tests pass but 3 remaining failures",
        }
    )
    assert result.success
    assert result.output["status"] == "partial_success"
    assert result.output["context_updates"]["tests_passing"] == 39


@pytest.mark.asyncio(loop_scope="session")
async def test_report_retry():
    """Report retry status with suggested next IDs."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute(
        {
            "status": "retry",
            "suggested_next_ids": ["fix_tests", "review_changes"],
            "failure_reason": "Flaky test detected, retry needed",
        }
    )
    assert result.success
    assert result.output["status"] == "retry"
    assert result.output["suggested_next_ids"] == ["fix_tests", "review_changes"]


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_status_rejected():
    """Invalid status value returns error."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"status": "invalid_value"})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_status_rejected():
    """Missing status parameter returns error."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"notes": "no status provided"})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_input_rejected():
    """Empty input returns error."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_minimal_report():
    """Minimal report with only status is valid."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"status": "success"})
    assert result.success
    assert result.output["status"] == "success"


@pytest.mark.asyncio(loop_scope="session")
async def test_all_valid_statuses():
    """All defined status values are accepted."""
    tool = ReportOutcomeTool(config={})
    for status in VALID_STATUSES:
        result = await tool.execute({"status": status})
        assert result.success, f"Status {status!r} should be valid"
        assert result.output["status"] == status


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_stored_on_tool():
    """Tool stores the last reported outcome for retrieval."""
    tool = ReportOutcomeTool(config={})
    await tool.execute({"status": "success", "notes": "first"})
    await tool.execute({"status": "fail", "failure_reason": "second"})
    assert tool.last_outcome is not None
    assert tool.last_outcome["status"] == "fail"
    assert tool.last_outcome["failure_reason"] == "second"


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_not_stored_on_error():
    """Invalid reports don't overwrite the stored outcome."""
    tool = ReportOutcomeTool(config={})
    await tool.execute({"status": "success", "notes": "valid"})
    await tool.execute({"status": "bogus"})
    assert tool.last_outcome["status"] == "success"


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_name_and_description():
    """Tool has correct name and description."""
    tool = ReportOutcomeTool(config={})
    assert tool.name == "report_outcome"
    assert "outcome" in tool.description.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_input_schema():
    """Tool exposes correct input schema."""
    tool = ReportOutcomeTool(config={})
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "status" in schema["properties"]
    assert "status" in schema["required"]
    # Optional fields present
    assert "preferred_label" in schema["properties"]
    assert "suggested_next_ids" in schema["properties"]
    assert "context_updates" in schema["properties"]
    assert "notes" in schema["properties"]
    assert "failure_reason" in schema["properties"]


@pytest.mark.asyncio(loop_scope="session")
async def test_output_includes_confirmation():
    """Successful output includes a human-readable confirmation message."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"status": "success"})
    assert result.success
    assert "message" in result.output
    assert isinstance(result.output["message"], str)


@pytest.mark.asyncio(loop_scope="session")
async def test_event_emitted_via_coordinator():
    """When a coordinator with hooks is present, an outcome:reported event is emitted."""
    from unittest.mock import AsyncMock, MagicMock

    hooks = MagicMock()
    hooks.emit = AsyncMock()
    coordinator = MagicMock()
    coordinator.hooks = hooks

    tool = ReportOutcomeTool(config={}, coordinator=coordinator)
    result = await tool.execute(
        {"status": "success", "preferred_label": "done", "notes": "all good"}
    )
    assert result.success
    hooks.emit.assert_called_once()
    event_name = hooks.emit.call_args[0][0]
    event_data = hooks.emit.call_args[0][1]
    assert event_name == "outcome:reported"
    assert event_data["status"] == "success"
    assert event_data["preferred_label"] == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_no_event_without_coordinator():
    """Tool works without a coordinator (no event emission, no crash)."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"status": "success"})
    assert result.success


@pytest.mark.asyncio(loop_scope="session")
async def test_context_updates_type_validation():
    """context_updates must be a dict if provided."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute({"status": "success", "context_updates": "not_a_dict"})
    assert not result.success
    assert "message" in result.error


@pytest.mark.asyncio(loop_scope="session")
async def test_suggested_next_ids_type_validation():
    """suggested_next_ids must be a list if provided."""
    tool = ReportOutcomeTool(config={})
    result = await tool.execute(
        {"status": "success", "suggested_next_ids": "not_a_list"}
    )
    assert not result.success
    assert "message" in result.error


# -- Schema determinism (prompt-cache stability) ----------------------------
#
# Regression guard for the S4 structural pattern (two independent `enum`
# call sites that must never drift apart). Full story:
# docs/designs/RECURRING-BUG-CLASSES.md (S4). See
# test_schema_serialization_is_deterministic_across_processes below for the
# actual cross-process proof.


def test_schema_enum_matches_canonical_order():
    """The enum must be exactly the module's canonical sorted order."""
    tool = ReportOutcomeTool(config={})
    assert tool.input_schema["properties"]["status"]["enum"] == list(
        _MODULE_STATUSES_SORTED
    )


def test_schema_enum_contains_expected_members():
    """Regression guard: sorting must never silently drop or add a value."""
    tool = ReportOutcomeTool(config={})
    enum = tool.input_schema["properties"]["status"]["enum"]
    assert set(enum) == {"success", "fail", "partial_success", "retry"}
    assert len(enum) == 4


_SCHEMA_PROBE = (
    "import json\n"
    "from amplifier_module_tool_report_outcome import ReportOutcomeTool\n"
    "tool = ReportOutcomeTool(config={})\n"
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
