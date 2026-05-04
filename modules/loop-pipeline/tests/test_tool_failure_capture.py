"""Issue 10 TDD — Tool node failure must capture command + output.

When a pipeline tool node fails (non-zero exit code), the Outcome must carry
a ``failed_step`` dict with structured fields so the dashboard can display
the command and output instead of the "command lost on failure" placeholder.

Spec: Issue 10 / analog of WS-4 Sub-fix C for in-pipeline tool nodes.

Payload shape (``Outcome.failed_step``):
    {
        "command":    str,   # resolved shell command (capped at 500 chars)
        "exit_code":  int,
        "duration_s": float,
        "stdout_tail": str,  # last ≤2 KiB of stdout; empty string, NOT None
        "stderr_tail": str,  # last ≤2 KiB of stderr; empty string, NOT None
    }

When the total JSON-serialised size of ``failed_step`` exceeds 8 KiB the
payload is truncated in this order (mirrors WS-4 Sub-fix C):
    1. Drop ``stdout_tail`` (least useful for diagnosis)
    2. Truncate ``stderr_tail`` to 1 KiB
    3. Truncate ``command`` to 200 chars
When truncation fires ``failed_step["verification_gap"]["log_filtered"]``
is set to ``True`` so consumers know information was dropped.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus


def _make_graph() -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# ---------------------------------------------------------------------------
# Test 1: failed_step populated on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_captures_failed_step(tmp_path):
    """Issue 10 — Tool node fails; failed_step has command, stdout_tail, stderr_tail."""
    node = Node(
        id="bad",
        attrs={"tool_command": "echo some_stdout; echo some_stderr >&2; exit 1"},
    )
    handler = ToolHandler()
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.FAIL

    fs = outcome.failed_step
    assert fs is not None, "failed_step must be populated on failure"

    # command is captured
    assert "echo some_stdout" in fs["command"]

    # exit_code matches
    assert fs["exit_code"] == 1

    # duration captured and non-negative
    assert "duration_s" in fs
    assert fs["duration_s"] >= 0.0

    # stdout captured (non-None)
    assert fs["stdout_tail"] is not None
    assert "some_stdout" in fs["stdout_tail"]

    # stderr captured (non-None)
    assert fs["stderr_tail"] is not None
    assert "some_stderr" in fs["stderr_tail"]


# ---------------------------------------------------------------------------
# Test 2: empty stdout → stdout_tail is "" not None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_empty_stdout_tail_is_empty_string(tmp_path):
    """Issue 10 — Empty stdout produces empty string stdout_tail, not None.

    Mirrors WS-6 R12.5 Issue 4 fix for tool.last_line: emit '' on empty,
    never None.
    """
    node = Node(
        id="silent_fail",
        attrs={"tool_command": "exit 2"},
    )
    handler = ToolHandler()
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.FAIL

    fs = outcome.failed_step
    assert fs is not None

    assert fs["stdout_tail"] == "", (
        f"stdout_tail should be empty string, got {fs['stdout_tail']!r}"
    )
    assert fs["stderr_tail"] == "", (
        f"stderr_tail should be empty string, got {fs['stderr_tail']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: truncation fires → verification_gap.log_filtered = True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_truncation_sets_log_filtered(tmp_path):
    """Issue 10 — Truncation (8 KiB cap) sets verification_gap.log_filtered=True.

    Tests _build_failed_step directly with large inputs (5 KiB stdout + 5 KiB
    stderr) so the 8 KiB JSON cap fires and verification_gap.log_filtered is
    set, without relying on a subprocess to produce the exact byte counts.
    """
    from amplifier_module_loop_pipeline.handlers.tool import _build_failed_step

    big_stdout = "A" * 5000
    big_stderr = "B" * 5000

    fs = _build_failed_step(
        command="echo hello",
        exit_code=1,
        duration_s=0.1,
        stdout_text=big_stdout,
        stderr_text=big_stderr,
    )

    # Truncation must have fired — verify the flag is set
    vgap = fs.get("verification_gap", {})
    assert vgap.get("log_filtered") is True, (
        f"Expected verification_gap.log_filtered=True, got: {fs!r}"
    )

    # Serialised payload must be ≤ 8 KiB after truncation
    assert len(json.dumps(fs)) <= 8192, (
        "failed_step serialised payload exceeds 8 KiB after truncation"
    )


# ---------------------------------------------------------------------------
# Test 4: success path — failed_step is None (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_success_has_no_failed_step(tmp_path):
    """Issue 10 regression — Success path must NOT populate failed_step."""
    node = Node(id="ok", attrs={"tool_command": "echo hello"})
    handler = ToolHandler()
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))

    assert outcome.status == StageStatus.SUCCESS
    assert outcome.failed_step is None, (
        f"failed_step should be None on success, got {outcome.failed_step!r}"
    )
