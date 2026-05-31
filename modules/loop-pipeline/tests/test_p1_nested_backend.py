"""Tests for nested backend wiring (P1).

When a parent pipeline runs a child pipeline via a folder/pipeline node,
the child HandlerRegistry should receive the parent's backend so that
child codergen nodes call the backend correctly.

Tests:
- test_backend_not_propagated_to_child_currently: documents the bug was fixed (child IS called)
- test_parent_codergen_uses_backend: baseline confirmation
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext

# ---------------------------------------------------------------------------
# SpyBackend
# ---------------------------------------------------------------------------


class SpyBackend:
    """Records every (node_id, prompt) call made by the codergen handler."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        self.calls.append((node.id, prompt))
        return "done"

    def was_called_for(self, node_id: str) -> bool:
        """Return True if the backend was called for the given node_id."""
        return any(call[0] == node_id for call in self.calls)


# ---------------------------------------------------------------------------
# CHILD_DOT constant: simple child pipeline
# ---------------------------------------------------------------------------

CHILD_DOT = """\
digraph child {
    start [shape=Mdiamond]
    child_work [prompt="Do child work"]
    done [shape=Msquare]
    start -> child_work -> done
}
"""

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_dot(path: str, content: str) -> None:
    """Write DOT content to a file at the given path."""
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNestedBackendWiring:
    """Tests for nested backend wiring in pipeline execution."""

    @pytest.mark.asyncio
    async def test_backend_not_propagated_to_child_currently(self, tmp_path):
        """Child codergen nodes DO receive the parent's backend (bug is fixed).

        When a folder node launches a child pipeline, PipelineHandler forwards
        the backend to the child HandlerRegistry. As a result, the spy backend
        DOES record calls from the child's codergen nodes.
        This test documents the current (post-fix) behavior — the child
        HandlerRegistry has backend=spy.
        """
        # Write child.dot to tmp_path
        child_dot_path = str(tmp_path / "child.dot")
        _write_dot(child_dot_path, CHILD_DOT)

        # Build parent DOT: start -> folder_node (child.dot) -> done
        parent_dot = """\
digraph parent {
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="child.dot"]
    done [shape=Msquare]
    start -> sub -> done
}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        spy = SpyBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=spy))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        # Parent pipeline should still succeed overall
        assert outcome.status == StageStatus.SUCCESS

        # FIX CONFIRMED: child_work IS called via spy because
        # PipelineHandler creates HandlerRegistry(HandlerContext(backend=self._backend)) for the child.
        assert spy.was_called_for("child_work") is True, (
            "Expected child_work in spy calls — backend is propagated "
            "to child pipelines via PipelineHandler (post-fix behavior)"
        )

    @pytest.mark.asyncio
    async def test_parent_codergen_uses_backend(self, tmp_path):
        """Parent-level codergen nodes DO use the backend (baseline).

        Confirms that when a codergen node runs directly in the parent
        pipeline (not inside a nested pipeline), the backend is called
        correctly. This establishes the baseline that backend wiring works
        at the top level.
        """
        parent_dot = """\
digraph parent {
    start [shape=Mdiamond]
    parent_work [prompt="Do parent work"]
    done [shape=Msquare]
    start -> parent_work -> done
}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        spy = SpyBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=spy))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS

        # BASELINE: parent_work IS called via the spy backend
        called_node_ids = [call[0] for call in spy.calls]
        assert "parent_work" in called_node_ids, (
            "Expected parent_work in spy calls — baseline: parent backend works"
        )
