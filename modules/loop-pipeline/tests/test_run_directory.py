"""Tests for the run directory structure.

Spec coverage: DIR-001, STAT-001–004, Section 5.6.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockBackend:
    """Backend that returns a fixed string."""

    def __init__(self, return_value: str = "done") -> None:
        self._return_value = return_value

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        return self._return_value


class SequenceBackend:
    """Backend that returns different outcomes per node id."""

    def __init__(self, outcomes: dict[str, str | Outcome]) -> None:
        self._outcomes = outcomes

    async def run(
        self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
    ) -> str | Outcome:
        return self._outcomes.get(node.id, "ok")


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-run-dir",
) -> PipelineEngine:
    """Parse DOT, validate, and build an engine."""
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
# manifest.json tests
# ---------------------------------------------------------------------------


class TestManifest:
    """Engine creates manifest.json at pipeline start."""

    @pytest.mark.asyncio
    async def test_manifest_created(self, tmp_path):
        """manifest.json exists after pipeline execution."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()

    @pytest.mark.asyncio
    async def test_manifest_is_valid_json(self, tmp_path):
        """manifest.json contains valid JSON."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "manifest.json") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_manifest_has_graph_name(self, tmp_path):
        """manifest.json includes graph name."""
        engine = _make_engine(
            dot_source="""
            digraph my_pipeline {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "manifest.json") as f:
            data = json.load(f)
        assert data["graph_name"] == "my_pipeline"

    @pytest.mark.asyncio
    async def test_manifest_has_start_time(self, tmp_path):
        """manifest.json includes a start_time."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "manifest.json") as f:
            data = json.load(f)
        assert "start_time" in data
        assert len(data["start_time"]) > 0

    @pytest.mark.asyncio
    async def test_manifest_has_goal(self, tmp_path):
        """manifest.json includes the goal when set."""
        engine = _make_engine(
            dot_source="""
            digraph {
                goal = "build auth"
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "manifest.json") as f:
            data = json.load(f)
        assert data["goal"] == "build auth"

    @pytest.mark.asyncio
    async def test_manifest_has_node_and_edge_counts(self, tmp_path):
        """manifest.json includes node_count and edge_count."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                a [prompt="A"]
                b [prompt="B"]
                exit [shape=Msquare]
                start -> a -> b -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "manifest.json") as f:
            data = json.load(f)
        assert data["node_count"] == 4
        assert data["edge_count"] == 3


# ---------------------------------------------------------------------------
# Per-node status.json tests
# ---------------------------------------------------------------------------


class TestNodeStatusFiles:
    """Engine creates per-node directories with status.json."""

    @pytest.mark.asyncio
    async def test_codergen_node_has_status_json(self, tmp_path):
        """Codergen nodes get status.json in their directory."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        status_path = tmp_path / "work" / "status.json"
        assert status_path.exists()

    @pytest.mark.asyncio
    async def test_start_node_has_status_json(self, tmp_path):
        """Start node gets status.json in its directory."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        status_path = tmp_path / "start" / "status.json"
        assert status_path.exists()

    @pytest.mark.asyncio
    async def test_status_json_is_valid(self, tmp_path):
        """status.json is valid JSON with expected fields."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "status" in data
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_status_json_has_timing(self, tmp_path):
        """status.json includes duration_ms."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "duration_ms" in data
        assert isinstance(data["duration_ms"], (int, float))

    @pytest.mark.asyncio
    async def test_status_json_has_node_id(self, tmp_path):
        """status.json includes node_id."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert data["node_id"] == "work"

    @pytest.mark.asyncio
    async def test_all_executed_nodes_have_status(self, tmp_path):
        """Every executed node gets a status.json."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                exit [shape=Msquare]
                start -> plan -> implement -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        for node_id in ["start", "plan", "implement"]:
            status_path = tmp_path / node_id / "status.json"
            assert status_path.exists(), f"Missing status.json for {node_id}"

    @pytest.mark.asyncio
    async def test_failed_node_status(self, tmp_path):
        """Failed nodes get status.json with fail status."""
        backend = SequenceBackend(
            outcomes={
                "bad": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            }
        )
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                bad [prompt="Will fail"]
                exit [shape=Msquare]
                start -> bad
                bad -> exit [condition="outcome=fail"]
            }
            """,
            backend=backend,
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "bad" / "status.json") as f:
            data = json.load(f)
        assert data["status"] == "fail"
        assert data["failure_reason"] == "broken"


# ---------------------------------------------------------------------------
# Codergen-specific files (prompt.md, response.md)
# ---------------------------------------------------------------------------


class TestCodergenFiles:
    """Codergen nodes produce prompt.md and response.md."""

    @pytest.mark.asyncio
    async def test_codergen_has_prompt_md(self, tmp_path):
        """Codergen nodes write prompt.md."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Build a thing"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        prompt_path = tmp_path / "work" / "prompt.md"
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "Build a thing" in content

    @pytest.mark.asyncio
    async def test_codergen_has_response_md(self, tmp_path):
        """Codergen nodes write response.md."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Build a thing"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend("built it!"),
            logs_root=str(tmp_path),
        )
        await engine.run()
        response_path = tmp_path / "work" / "response.md"
        assert response_path.exists()
        content = response_path.read_text()
        assert "built it!" in content

    @pytest.mark.asyncio
    async def test_non_codergen_no_prompt_md(self, tmp_path):
        """Non-codergen nodes (start) do NOT write prompt.md."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        prompt_path = tmp_path / "start" / "prompt.md"
        assert not prompt_path.exists()


# ---------------------------------------------------------------------------
# Artifacts directory
# ---------------------------------------------------------------------------


class TestArtifactsDirectory:
    """artifacts/ directory is created when needed."""

    @pytest.mark.asyncio
    async def test_artifacts_dir_exists_after_run(self, tmp_path):
        """artifacts/ directory is created in the run directory."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                exit [shape=Msquare]
                start -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        artifacts_dir = tmp_path / "artifacts"
        assert artifacts_dir.exists()
        assert artifacts_dir.is_dir()


# ---------------------------------------------------------------------------
# checkpoint.json co-existence
# ---------------------------------------------------------------------------


class TestCheckpointCoexistence:
    """checkpoint.json is in the run directory alongside manifest.json."""

    @pytest.mark.asyncio
    async def test_both_manifest_and_checkpoint(self, tmp_path):
        """Both manifest.json and checkpoint.json exist after a run."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        assert (tmp_path / "manifest.json").exists()
        assert (tmp_path / "checkpoint.json").exists()
