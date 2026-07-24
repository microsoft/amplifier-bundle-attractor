"""Integration test: console-mode human gate driven end-to-end through the
runner's real engine machinery, with a REAL ``ConsoleInterviewer`` (never
mocked) and only ``sys.stdin`` faked (piped, scripted input).

This proves the ``--on-human-gate console`` wiring does real stdin-driven
edge selection -- not a silent auto-approve/first-choice fallback -- by
running a minimal two-choice hexagon gate and asserting the SECOND choice's
edge is the one actually taken when "B\\n" is piped in.

Spec basis (specs/canonical/attractor-spec-canonical.md, identical to the
fresh upstream clone at attractor/attractor-spec.md):
    Section 6.1  -- Interviewer interface (``ask``/``ask_multiple``/``inform``).
    Section 6.4  -- "ConsoleInterviewer (CLI): Reads from standard input.
                     Displays formatted prompts with option keys."
    Conformance checklist (~line 1865): "ConsoleInterviewer prompts in
                     terminal and reads user input."
    Section 9.5  -- human gates must be operable via CLI.

Uses ``drive_engine`` (the low-level seam ``run_pipeline``/the CLI itself
build on -- see runner.py's module docstring) with a bare ``coordinator``
stand-in: this graph has no box/agent nodes, so the coordinator is never
touched (only the human-gate handler and exit handler fire).
"""

from __future__ import annotations

import io
import sys

import pytest

from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_NODE_COMPLETE
from amplifier_module_pipeline_runner.runner import drive_engine

_GATE_DOT = """
digraph ConsoleGateIntegration {
    graph [goal="prove console-mode edge selection"]
    start  [shape=Mdiamond]
    gate   [shape=hexagon, label="Pick one"]
    path_a [shape=parallelogram, tool_command="true"]
    path_b [shape=parallelogram, tool_command="true"]
    done   [shape=Msquare]

    start -> gate
    gate -> path_a [label="[A] First choice"]
    gate -> path_b [label="[B] Second choice"]
    path_a -> done
    path_b -> done
}
"""
# Exactly one exit (Msquare) node is a hard validation requirement (see
# ValidationError "exactly one is required"), so the two choices are
# distinguished by which intermediate tool node (path_a/path_b) executes on
# the way to the single shared exit -- not by distinct exit nodes.


class _RecordingHooks:
    """Minimal hooks stand-in -- records every emitted pipeline event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_name: str, data: dict) -> None:
        self.events.append((event_name, dict(data)))


def _completed_node_ids(hooks: "_RecordingHooks") -> list[str]:
    return [
        data["node_id"] for name, data in hooks.events if name == PIPELINE_NODE_COMPLETE
    ]


@pytest.mark.asyncio
async def test_console_mode_scripted_stdin_takes_second_choice(monkeypatch, tmp_path):
    """Piped stdin "B\\n" through a REAL ConsoleInterviewer routes to path_b.

    If console mode silently behaved like auto-approve (which always picks
    the FIRST edge), this would incorrectly route through path_a instead --
    so this assertion proves real stdin-driven selection, not a fallback.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO("B\n"))
    hooks = _RecordingHooks()

    outcome = await drive_engine(
        _GATE_DOT,
        coordinator=object(),  # never touched -- no box/agent nodes here
        interviewer=ConsoleInterviewer(),
        hooks=hooks,
        logs_root=str(tmp_path),
        transform=True,
        validate=True,
    )

    assert outcome.status.value == "success"
    completed = _completed_node_ids(hooks)
    # The exit node itself doesn't emit its own node_complete event (the
    # engine's main loop returns as soon as it reaches an exit node), so the
    # last *handler* to complete is the branch-specific tool node -- which is
    # exactly what proves which edge was taken.
    assert completed[-1] == "path_b"
    assert "path_a" not in completed


@pytest.mark.asyncio
async def test_console_mode_scripted_stdin_takes_first_choice(monkeypatch, tmp_path):
    """Complementary case: piped stdin "A\\n" routes through path_a."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("A\n"))
    hooks = _RecordingHooks()

    outcome = await drive_engine(
        _GATE_DOT,
        coordinator=object(),
        interviewer=ConsoleInterviewer(),
        hooks=hooks,
        logs_root=str(tmp_path),
        transform=True,
        validate=True,
    )

    assert outcome.status.value == "success"
    completed = _completed_node_ids(hooks)
    assert completed[-1] == "path_a"
    assert "path_b" not in completed
