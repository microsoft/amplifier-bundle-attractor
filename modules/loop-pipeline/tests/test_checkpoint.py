"""Tests for checkpointing and resume.

After every node execution, a JSON checkpoint is saved so the pipeline
can resume after crashes. Tests cover serialization, deserialization,
engine integration, and resume-from-checkpoint behavior.

Spec coverage: CHKP-001–006, Section 5.3, T2.4 (RunIdentity hard-fail).
"""

import json
import logging
import os

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    Checkpoint,
    CheckpointMismatchError,
    load_checkpoint,
    save_checkpoint,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.run_identity import RunIdentity
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# --- Checkpoint model ---


class TestCheckpointModel:
    """CHKP-001: Checkpoint captures execution state."""

    def test_create_checkpoint(self):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={"graph.goal": "build auth"},
            node_outcomes={
                "start": {"status": "success", "notes": "Start node"},
                "plan": {"status": "success", "notes": "Planned"},
            },
            timestamp="2025-01-01T00:00:00Z",
        )
        assert cp.current_node == "plan"
        assert len(cp.completed_nodes) == 2
        assert cp.context_snapshot["graph.goal"] == "build auth"

    def test_checkpoint_has_timestamp(self):
        cp = Checkpoint(
            current_node="step1",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-06-15T12:00:00Z",
        )
        assert cp.timestamp == "2025-06-15T12:00:00Z"

    def test_checkpoint_node_retries(self):
        """Checkpoint preserves retry counters."""
        cp = Checkpoint(
            current_node="flaky",
            completed_nodes={"flaky": "success"},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
            node_retries={"flaky": 3},
        )
        assert cp.node_retries["flaky"] == 3


# --- Serialization ---


class TestCheckpointSerialization:
    """CHKP-002–003: Checkpoint saves/loads as valid JSON."""

    def test_save_creates_json_file(self, tmp_path):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success"},
            context_snapshot={"graph.goal": "test"},
            node_outcomes={"start": {"status": "success"}},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        assert os.path.exists(path)

    def test_saved_json_is_valid(self, tmp_path):
        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success"},
            context_snapshot={"graph.goal": "test"},
            node_outcomes={"start": {"status": "success"}},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        # Must be valid JSON
        with open(path) as f:
            data = json.load(f)
        assert data["current_node"] == "plan"

    def test_saved_json_is_human_readable(self, tmp_path):
        """JSON should be indented for debugging."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
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
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={"graph.goal": "build auth", "last_stage": "plan"},
            node_outcomes={
                "start": {"status": "success", "notes": "ok"},
                "plan": {"status": "success", "notes": "planned"},
            },
            timestamp="2025-06-15T12:00:00Z",
            node_retries={"plan": 2},
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.current_node == "implement"
        assert loaded.completed_nodes == {"start": "success", "plan": "success"}
        assert loaded.context_snapshot["graph.goal"] == "build auth"
        assert loaded.node_outcomes["plan"]["notes"] == "planned"
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
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.current_node == ""
        assert loaded.completed_nodes == {}

    def test_node_retries_default_empty(self, tmp_path):
        """When no node_retries in JSON, defaults to empty dict."""
        cp = Checkpoint(
            current_node="x",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.node_retries == {}


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
    """CHKP-005–006: Resume from checkpoint skips completed nodes."""

    @pytest.mark.asyncio
    async def test_resume_skips_completed_nodes(self, tmp_path):
        """Resumed engine skips nodes that are already completed."""
        dot_source = """
            digraph {
                goal = "build auth"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                exit [shape=Msquare]
                start -> plan -> implement -> exit
            }
            """
        # Compute identity so the engine accepts the checkpoint (T2.4)
        graph = parse_dot(dot_source)
        identity = RunIdentity.from_graph(graph)

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={"graph.goal": "build auth", "outcome": "success"},
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        # Now create an engine and resume from checkpoint
        backend = MockBackend("done")
        engine = _make_engine(
            dot_source=dot_source, backend=backend, logs_root=str(tmp_path)
        )
        outcome = await engine.run()
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # Backend should have been called for implement but NOT plan
        # (plan was completed in the checkpoint)
        assert "implement" in backend.calls
        assert "plan" not in backend.calls

    @pytest.mark.asyncio
    async def test_resume_restores_context(self, tmp_path):
        """Resumed engine has context values from checkpoint."""
        dot_source = """
            digraph {
                goal = "build auth"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                exit [shape=Msquare]
                start -> plan -> implement -> exit
            }
            """
        graph = parse_dot(dot_source)
        identity = RunIdentity.from_graph(graph)

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={
                "graph.goal": "build auth",
                "outcome": "success",
                "custom_key": "custom_value",
            },
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        backend = MockBackend("done")
        engine = _make_engine(
            dot_source=dot_source, backend=backend, logs_root=str(tmp_path)
        )
        await engine.run()
        # Context should include the restored values
        assert engine.context.get("custom_key") == "custom_value"

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


# --- RunIdentity hard-fail (T2.4) ---

_SIMPLE_DOT = """
digraph {
    start [shape=Mdiamond]
    step  [prompt="Work"]
    exit  [shape=Msquare]
    start -> step -> exit
}
"""

_DIFFERENT_DOT = """
digraph {
    start [shape=Mdiamond]
    step1 [prompt="Work A"]
    step2 [prompt="Work B"]
    exit  [shape=Msquare]
    start -> step1 -> step2 -> exit
}
"""


class TestRunIdentityHardFail:
    """T2.4: RunIdentity replaces graph_fingerprint; mismatch is a hard-fail.

    Five cases are specified:
    1. Pre-identity format (no identity, no graph_fingerprint) → discard silently, log info.
    2. Wave-0 #252 format (has graph_fingerprint, matches) → resume.
    3. Wave-0 #252 format (has graph_fingerprint, mismatches) → hard-fail.
    4. New T2.4 format (has identity, matches) → resume.
    5. New T2.4 format (has identity, mismatches) → hard-fail.
    """

    # -- load_checkpoint unit-level tests (no engine) --

    def test_load_legacy_checkpoint_returns_none_identity(self, tmp_path):
        """Pre-#252 checkpoint (no identity, no graph_fingerprint) loads with identity=None."""
        path = str(tmp_path / "checkpoint.json")
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            # Note: no "identity" and no "graph_fingerprint" keys
        }
        with open(path, "w") as f:
            json.dump(raw, f)

        cp = load_checkpoint(path)
        assert cp.identity is None

    def test_load_252_format_checkpoint_builds_identity_from_graph_fingerprint(
        self, tmp_path
    ):
        """Wave-0 #252 format (graph_fingerprint str) → RunIdentity reconstructed."""
        path = str(tmp_path / "checkpoint.json")
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "graph_fingerprint": "abcdef1234567890abcdef1234567890",
        }
        with open(path, "w") as f:
            json.dump(raw, f)

        cp = load_checkpoint(path)
        assert cp.identity is not None
        assert isinstance(cp.identity, RunIdentity)
        assert cp.identity.graph_fingerprint == "abcdef1234567890abcdef1234567890"

    def test_load_t24_format_checkpoint_builds_identity(self, tmp_path):
        """New T2.4 format (identity dict) → RunIdentity reconstructed correctly."""
        path = str(tmp_path / "checkpoint.json")
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "identity": {"graph_fingerprint": "deadbeef1234567890abcdef12345678"},
        }
        with open(path, "w") as f:
            json.dump(raw, f)

        cp = load_checkpoint(path)
        assert cp.identity is not None
        assert cp.identity.graph_fingerprint == "deadbeef1234567890abcdef12345678"

    def test_save_checkpoint_with_identity_serializes_identity(self, tmp_path):
        """Checkpoint with RunIdentity saves identity dict to JSON."""
        identity = RunIdentity(graph_fingerprint="cafebabe1234567890abcdef12345678")
        cp = Checkpoint(
            current_node="step",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)

        with open(path) as f:
            data = json.load(f)

        assert "identity" in data
        assert (
            data["identity"]["graph_fingerprint"] == "cafebabe1234567890abcdef12345678"
        )

    # -- engine integration tests --

    @pytest.mark.asyncio
    async def test_legacy_checkpoint_is_discarded_with_info_log(self, tmp_path, caplog):
        """Pre-identity checkpoint (no identity) is discarded; info log emitted; runs fresh."""
        # Write a checkpoint with no identity field
        cp_path = tmp_path / "checkpoint.json"
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success", "step": "success"},
            "context": {"graph.goal": "test"},
            "node_outcomes": {
                "start": {"status": "success"},
                "step": {"status": "success"},
            },
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
        }
        with open(str(cp_path), "w") as f:
            json.dump(raw, f)

        backend = MockBackend("done")
        engine = _make_engine(_SIMPLE_DOT, backend=backend, logs_root=str(tmp_path))

        with caplog.at_level(logging.INFO):
            outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # step should have run because checkpoint was discarded (fresh run)
        assert "step" in backend.calls
        # Info log should mention the legacy discard
        assert any(
            "pre-identity" in record.message.lower()
            or "legacy" in record.message.lower()
            or "migration" in record.message.lower()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_matching_identity_resumes_normally(self, tmp_path):
        """Checkpoint with matching identity resumes; completed nodes skipped."""
        # Build the identity for the graph we'll use
        graph = parse_dot(_SIMPLE_DOT)
        from amplifier_module_loop_pipeline.run_identity import RunIdentity as RI

        identity = RI.from_graph(graph)

        # Write a checkpoint with the correct identity and step already done
        cp_path = tmp_path / "checkpoint.json"
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success", "step": "success"},
            "context": {"graph.goal": ""},
            "node_outcomes": {
                "start": {
                    "status": "success",
                    "notes": None,
                    "failure_reason": None,
                    "preferred_label": None,
                },
                "step": {
                    "status": "success",
                    "notes": "done",
                    "failure_reason": None,
                    "preferred_label": None,
                },
            },
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "identity": {"graph_fingerprint": identity.graph_fingerprint},
        }
        with open(str(cp_path), "w") as f:
            json.dump(raw, f)

        backend = MockBackend("done")
        engine = _make_engine(_SIMPLE_DOT, backend=backend, logs_root=str(tmp_path))
        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        # step was completed in checkpoint → backend should NOT have been called for step
        assert "step" not in backend.calls

    @pytest.mark.asyncio
    async def test_identity_mismatch_raises_checkpoint_mismatch_error(self, tmp_path):
        """Identity mismatch → CheckpointMismatchError raised (hard-fail, not silent restart)."""
        # Write a checkpoint with a DIFFERENT graph's identity
        different_graph = parse_dot(_DIFFERENT_DOT)
        from amplifier_module_loop_pipeline.run_identity import RunIdentity as RI

        different_identity = RI.from_graph(different_graph)

        cp_path = tmp_path / "checkpoint.json"
        raw = {
            "current_node": "step1",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {"start": {"status": "success"}},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "identity": {"graph_fingerprint": different_identity.graph_fingerprint},
        }
        with open(str(cp_path), "w") as f:
            json.dump(raw, f)

        # Run the SIMPLE graph against a checkpoint from the DIFFERENT graph
        engine = _make_engine(
            _SIMPLE_DOT, backend=MockBackend("done"), logs_root=str(tmp_path)
        )

        with pytest.raises(CheckpointMismatchError) as exc_info:
            await engine.run()

        # Error message must contain remediation: tell user to delete the file
        error_msg = str(exc_info.value)
        assert "delete" in error_msg.lower() or "remove" in error_msg.lower()
        assert str(cp_path) in error_msg

    @pytest.mark.asyncio
    async def test_252_format_mismatch_raises_checkpoint_mismatch_error(self, tmp_path):
        """Wave-0 #252 format with mismatched graph_fingerprint → hard-fail."""
        cp_path = tmp_path / "checkpoint.json"
        raw = {
            "current_node": "step",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {"start": {"status": "success"}},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "graph_fingerprint": "0" * 32,  # clearly wrong fingerprint
        }
        with open(str(cp_path), "w") as f:
            json.dump(raw, f)

        engine = _make_engine(
            _SIMPLE_DOT, backend=MockBackend("done"), logs_root=str(tmp_path)
        )

        with pytest.raises(CheckpointMismatchError):
            await engine.run()

    @pytest.mark.asyncio
    async def test_error_message_contains_checkpoint_path(self, tmp_path):
        """CheckpointMismatchError includes the path to the stale checkpoint file."""
        different_graph = parse_dot(_DIFFERENT_DOT)
        from amplifier_module_loop_pipeline.run_identity import RunIdentity as RI

        different_identity = RI.from_graph(different_graph)

        cp_path = tmp_path / "checkpoint.json"
        raw = {
            "current_node": "step1",
            "completed_nodes": {"start": "success"},
            "context": {},
            "node_outcomes": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
            "identity": {"graph_fingerprint": different_identity.graph_fingerprint},
        }
        with open(str(cp_path), "w") as f:
            json.dump(raw, f)

        engine = _make_engine(
            _SIMPLE_DOT, backend=MockBackend("done"), logs_root=str(tmp_path)
        )

        with pytest.raises(CheckpointMismatchError) as exc_info:
            await engine.run()

        assert str(cp_path) in str(exc_info.value)
