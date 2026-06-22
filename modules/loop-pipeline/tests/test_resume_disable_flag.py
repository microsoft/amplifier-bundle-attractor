"""Tests for the resume_from_checkpoint="false" graph attribute.

When a pipeline sets ``resume_from_checkpoint="false"`` in its graph block,
the engine must NOT load or honour any existing checkpoint; it runs from
Start every time and lets the graph's own resume node (e.g. ResumeGate)
drive continuation.

Test coverage:
  T1  -- engine ignores checkpoint and re-executes completed nodes
  T2  -- parse_dot preserves the graph attr so the engine can read it
  T3  -- _checkpoint_resume_enabled() semantics table
"""

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    Checkpoint,
    save_checkpoint,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.run_identity import RunIdentity
from amplifier_module_loop_pipeline.validation import validate_or_raise


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DOT_DISABLE_FLAG = """\
digraph {
    graph [
        resume_from_checkpoint = "false",
    ]
    start  [shape=Mdiamond]
    work   [shape=box, prompt="Do work"]
    finish [shape=Msquare]
    start -> work -> finish
}
"""

_DOT_NO_FLAG = """\
digraph {
    start  [shape=Mdiamond]
    work   [shape=box, prompt="Do work"]
    finish [shape=Msquare]
    start -> work -> finish
}
"""


class _TrackingBackend:
    """Backend that records every node it was asked to run."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        self.calls.append(node.id)
        return "done"


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-resume-disable",
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


# ---------------------------------------------------------------------------
# T1 — engine ignores checkpoint and re-executes completed nodes
# ---------------------------------------------------------------------------


class TestCheckpointDisabledFlag:
    """T1: resume_from_checkpoint=false causes engine to run from Start.

    A checkpoint that marks 'work' as already completed must be IGNORED when
    the flag is set — the node must be executed (not fast-forwarded past).
    """

    @pytest.mark.asyncio
    async def test_completed_node_is_reexecuted_when_flag_set(self, tmp_path):
        """Engine re-executes a node recorded in the checkpoint when
        resume_from_checkpoint="false" is present in the graph block."""
        graph = parse_dot(_DOT_DISABLE_FLAG)
        identity = RunIdentity.from_graph(graph)

        # Write a checkpoint that says 'work' is done.
        cp = Checkpoint(
            current_node="work",
            completed_nodes={"start": "success", "work": "success"},
            context_snapshot={},
            node_outcomes={
                "start": {
                    "status": "success",
                    "notes": None,
                    "failure_reason": None,
                    "preferred_label": None,
                },
                "work": {
                    "status": "success",
                    "notes": None,
                    "failure_reason": None,
                    "preferred_label": None,
                },
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        backend = _TrackingBackend()
        engine = _make_engine(
            _DOT_DISABLE_FLAG, backend=backend, logs_root=str(tmp_path)
        )
        outcome = await engine.run()

        # Pipeline must succeed (ran to completion from Start).
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # 'work' must have been called — the checkpoint was NOT honoured.
        assert "work" in backend.calls, (
            f"Expected 'work' to be executed (checkpoint disabled), "
            f"but backend.calls={backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_checkpoint_not_ignored_without_flag(self, tmp_path):
        """Sanity: without the flag, the engine DOES honour the checkpoint
        and skips nodes already recorded as completed."""
        graph = parse_dot(_DOT_NO_FLAG)
        identity = RunIdentity.from_graph(graph)

        cp = Checkpoint(
            current_node="work",
            completed_nodes={"start": "success", "work": "success"},
            context_snapshot={},
            node_outcomes={
                "start": {
                    "status": "success",
                    "notes": None,
                    "failure_reason": None,
                    "preferred_label": None,
                },
                "work": {
                    "status": "success",
                    "notes": None,
                    "failure_reason": None,
                    "preferred_label": None,
                },
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        backend = _TrackingBackend()
        engine = _make_engine(_DOT_NO_FLAG, backend=backend, logs_root=str(tmp_path))
        await engine.run()

        # Without the flag the checkpoint IS honoured — 'work' is skipped.
        assert "work" not in backend.calls, (
            f"Expected 'work' to be SKIPPED (checkpoint active), "
            f"but backend.calls={backend.calls}"
        )


# ---------------------------------------------------------------------------
# T2 — parse_dot preserves the graph attribute
# ---------------------------------------------------------------------------


class TestParseDotCarriesFlag:
    """T2: parse_dot carries resume_from_checkpoint into graph_attrs."""

    def test_flag_present_in_graph_attrs(self):
        graph = parse_dot(_DOT_DISABLE_FLAG)
        assert "resume_from_checkpoint" in graph.graph_attrs, (
            "parse_dot must preserve resume_from_checkpoint in graph_attrs"
        )
        assert graph.graph_attrs["resume_from_checkpoint"] == "false"

    def test_flag_absent_when_not_set(self):
        graph = parse_dot(_DOT_NO_FLAG)
        assert "resume_from_checkpoint" not in graph.graph_attrs


# ---------------------------------------------------------------------------
# T3 — _checkpoint_resume_enabled() semantics table
# ---------------------------------------------------------------------------


class TestCheckpointResumeEnabledSemantics:
    """T3: _checkpoint_resume_enabled() returns the right bool for every value.

    Falsy  : "false", "False", "0", "no", "off"  → returns False
    Truthy : absent, "true", "yes", "1"           → returns True
    """

    @pytest.mark.parametrize(
        "attr_value, expected",
        [
            (None, True),  # absent — default is enabled
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_semantics(self, attr_value: str | None, expected: bool, tmp_path):
        if attr_value is None:
            dot = _DOT_NO_FLAG
        else:
            dot = f"""\
digraph {{
    graph [ resume_from_checkpoint = "{attr_value}", ]
    start  [shape=Mdiamond]
    finish [shape=Msquare]
    start -> finish
}}
"""
        engine = _make_engine(dot, backend=_TrackingBackend(), logs_root=str(tmp_path))
        assert engine._checkpoint_resume_enabled() is expected, (
            f"attr_value={attr_value!r}: expected {expected}, "
            f"got {engine._checkpoint_resume_enabled()}"
        )
