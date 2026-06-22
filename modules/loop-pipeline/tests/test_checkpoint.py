"""Tests for checkpointing.

After every node execution, a JSON checkpoint is saved so the pipeline
can observe crash state. Tests cover serialization, deserialization,
and engine integration.

The engine always starts from the graph's start node — stale checkpoint
files are silently ignored. Graph-level idempotency is the handler's job.

Spec coverage: CHKP-001–006, Section 5.3.
"""

import json
import os

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    Checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# --- Checkpoint model ---


class TestCheckpointModel:
    """CHKP-001: Checkpoint captures execution state."""

    def test_create_checkpoint(self):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes=["start", "plan"],
            context_snapshot={"graph.goal": "build auth"},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert cp.current_node == "plan"
        assert len(cp.completed_nodes) == 2
        assert cp.context_snapshot["graph.goal"] == "build auth"

    def test_checkpoint_has_timestamp(self):
        cp = Checkpoint(
            current_node="step1",
            completed_nodes=[],
            context_snapshot={},
            timestamp="2025-06-15T12:00:00Z",
        )
        assert cp.timestamp == "2025-06-15T12:00:00Z"

    def test_checkpoint_node_retries(self):
        """Checkpoint preserves retry counters."""
        cp = Checkpoint(
            current_node="flaky",
            completed_nodes=["flaky"],
            context_snapshot={},
            timestamp="2025-01-01T00:00:00Z",
            node_retries={"flaky": 3},
        )
        assert cp.node_retries["flaky"] == 3

    def test_completed_nodes_is_list(self):
        """Spec §5.3: completed_nodes is List<String>."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes=["a", "b", "c"],
            context_snapshot={},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert isinstance(cp.completed_nodes, list)
        assert cp.completed_nodes == ["a", "b", "c"]


# --- Serialization ---


class TestCheckpointSerialization:
    """CHKP-002–003: Checkpoint saves/loads as valid JSON."""

    def test_save_creates_json_file(self, tmp_path):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes=["start"],
            context_snapshot={"graph.goal": "test"},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        assert os.path.exists(path)

    def test_saved_json_is_valid(self, tmp_path):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes=["start"],
            context_snapshot={"graph.goal": "test"},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        # Must be valid JSON
        with open(path) as f:
            data = json.load(f)
        assert data["current_node"] == "plan"
        # Spec §5.3: completed_nodes must be a list
        assert isinstance(data["completed_nodes"], list)

    def test_saved_json_is_human_readable(self, tmp_path):
        """JSON should be indented for debugging."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes=[],
            context_snapshot={},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        with open(path) as f:
            content = f.read()
        # Indented JSON has newlines and spaces
        assert "\n" in content

    def test_round_trip(self, tmp_path):
        """Save then load returns equivalent Checkpoint."""
        cp = Checkpoint(
            current_node="implement",
            completed_nodes=["start", "plan"],
            context_snapshot={"graph.goal": "build auth", "last_stage": "plan"},
            timestamp="2025-06-15T12:00:00Z",
            node_retries={"plan": 2},
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.current_node == "implement"
        assert loaded.completed_nodes == ["start", "plan"]
        assert loaded.context_snapshot["graph.goal"] == "build auth"
        assert loaded.timestamp == "2025-06-15T12:00:00Z"
        assert loaded.node_retries == {"plan": 2}

    def test_load_missing_file_raises(self, tmp_path):
        """Loading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_checkpoint(str(tmp_path / "nonexistent.json"))

    def test_save_with_empty_fields(self, tmp_path):
        """Empty checkpoint saves and loads correctly."""
        cp = Checkpoint(
            current_node="",
            completed_nodes=[],
            context_snapshot={},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.current_node == ""
        assert loaded.completed_nodes == []

    def test_node_retries_default_empty(self, tmp_path):
        """When no node_retries in JSON, defaults to empty dict."""
        cp = Checkpoint(
            current_node="x",
            completed_nodes=[],
            context_snapshot={},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.node_retries == {}

    def test_load_legacy_dict_completed_nodes(self, tmp_path):
        """load_checkpoint handles legacy dict completed_nodes gracefully."""
        path = str(tmp_path / "checkpoint.json")
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success", "plan": "success"},
            "context": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
        }
        with open(path, "w") as f:
            json.dump(raw, f)
        loaded = load_checkpoint(path)
        # Keys extracted in insertion order
        assert set(loaded.completed_nodes) == {"start", "plan"}
        assert isinstance(loaded.completed_nodes, list)


# --- Engine integration ---


class MockBackend:
    """Backend that returns a fixed string for every call."""

    def __init__(self, return_value: str = "done"):
        self._return_value = return_value
        self.calls: list[str] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        self.calls.append(node.id)
        return self._return_value


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-pipeline",
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


class TestCheckpointEngineIntegration:
    """CHKP-004: Engine saves checkpoint after each node."""

    @pytest.mark.asyncio
    async def test_checkpoint_saved_after_each_node(self, tmp_path):
        """Engine writes checkpoint.json after each node execution."""
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
            backend=MockBackend("done"),
            logs_root=str(tmp_path),
        )
        await engine.run()
        checkpoint_path = tmp_path / "checkpoint.json"
        assert checkpoint_path.exists()
        data = json.loads(checkpoint_path.read_text())
        # After full run, completed_nodes should include start, plan, implement
        assert "start" in data["completed_nodes"]
        assert "plan" in data["completed_nodes"]
        assert "implement" in data["completed_nodes"]

    @pytest.mark.asyncio
    async def test_checkpoint_has_context_snapshot(self, tmp_path):
        """Checkpoint includes context state."""
        engine = _make_engine(
            dot_source="""
            digraph {
                goal = "build auth"
                start [shape=Mdiamond]
                step [prompt="Work"]
                exit [shape=Msquare]
                start -> step -> exit
            }
            """,
            backend=MockBackend("done"),
            logs_root=str(tmp_path),
        )
        await engine.run()
        data = json.loads((tmp_path / "checkpoint.json").read_text())
        assert "graph.goal" in data["context"]


class TestResumeFromCheckpoint:
    """Engine always starts fresh; stale checkpoint is silently ignored."""

    @pytest.mark.asyncio
    async def test_no_checkpoint_runs_normally(self, tmp_path):
        """Engine without existing checkpoint runs from the beginning."""
        backend = MockBackend("done")
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                step [prompt="Work"]
                exit [shape=Msquare]
                start -> step -> exit
            }
            """,
            backend=backend,
            logs_root=str(tmp_path),
        )
        outcome = await engine.run()
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # Backend called for step (start is handled by StartHandler)
        assert "step" in backend.calls


# --- New guard tests ---

_SIMPLE_DOT = """
digraph {
    start [shape=Mdiamond]
    step  [prompt="Work"]
    exit  [shape=Msquare]
    start -> step -> exit
}
"""


class TestStaleCheckpointIgnored:
    """Stale checkpoint.json is silently ignored; engine always starts from Start."""

    @pytest.mark.asyncio
    async def test_stale_checkpoint_does_not_crash(self, tmp_path):
        """A stale checkpoint.json (any content, any identity) is ignored; engine runs fresh."""
        # Write a stale checkpoint — could be from a different graph, different run, anything
        cp_path = tmp_path / "checkpoint.json"
        stale = {
            "current_node": "some_old_node",
            "completed_nodes": {"start": "success", "step": "success"},
            "context": {"graph.goal": "old goal"},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "identity": {"graph_fingerprint": "0" * 32},
        }
        with open(str(cp_path), "w") as f:
            json.dump(stale, f)

        # Engine should run from Start without crashing
        backend = MockBackend("done")
        engine = _make_engine(_SIMPLE_DOT, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # step should have run because engine always starts from Start
        assert "step" in backend.calls

    @pytest.mark.asyncio
    async def test_stale_checkpoint_overwritten_with_fresh_run(self, tmp_path):
        """After a fresh run, the checkpoint reflects the just-completed run, not the stale one."""
        cp_path = tmp_path / "checkpoint.json"
        stale = {
            "current_node": "completely_different_node",
            "completed_nodes": [],
            "context": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
        }
        with open(str(cp_path), "w") as f:
            json.dump(stale, f)

        engine = _make_engine(_SIMPLE_DOT, backend=MockBackend("done"), logs_root=str(tmp_path))
        await engine.run()

        data = json.loads(cp_path.read_text())
        # Checkpoint should now reflect actual completed nodes from the fresh run
        assert "start" in data["completed_nodes"]
        assert "step" in data["completed_nodes"]


class TestCheckpointKeyShape:
    """Spec §5.3: checkpoint.json has the correct field shape."""

    @pytest.mark.asyncio
    async def test_checkpoint_json_has_spec_keys(self, tmp_path):
        """Written checkpoint has the spec-mandated keys and correct types."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                step [prompt="Work"]
                exit [shape=Msquare]
                start -> step -> exit
            }
            """,
            backend=MockBackend("done"),
            logs_root=str(tmp_path),
        )
        await engine.run()
        cp_path = tmp_path / "checkpoint.json"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text())

        # Spec §5.3: required fields
        assert "current_node" in data
        assert "completed_nodes" in data
        assert "context" in data
        assert "timestamp" in data
        assert "node_retries" in data
        assert "logs" in data

        # Spec §5.3: completed_nodes is List<String>
        assert isinstance(data["completed_nodes"], list)
        # No beyond-spec fields
        assert "node_outcomes" not in data
        assert "identity" not in data
