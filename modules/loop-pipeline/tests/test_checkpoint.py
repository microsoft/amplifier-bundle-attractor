"""Tests for checkpointing.

After every node execution, a JSON checkpoint is saved so the pipeline
can observe crash state. Tests cover serialization, deserialization,
and engine integration.

A fresh ``run()`` always starts from the graph's start node — stale
checkpoint files are inert to it (there is no call path from ``run()`` to
any checkpoint loader). Resume is a separate, explicit entry point; see
``test_resume_validation.py`` and the runner's resume e2e tests.

Spec coverage: CHKP-001–006, Section 5.3.
"""

import json
import os

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    Checkpoint,
    fingerprint_dot_source,
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

        # Schema v2 is a strict SUPERSET of the six §5.3 fields above: the
        # spec keys keep their exact names and shapes; the v2 keys are
        # additive and exist so the explicit resume entry point can restore
        # state instead of replaying completed work (issue #224).
        assert data["schema_version"] == 2
        assert data["run_state"] in ("in_flight", "completed")
        assert isinstance(data["node_outcomes"], dict)
        assert isinstance(data["engine_state"], dict)
        assert isinstance(data["graph"], dict)
        # The retired pre-#66 "identity" block is NOT what v2 reintroduces:
        # graph identity lives under graph.fingerprint and is evaluated ONLY
        # on the explicit resume path, never inside a fresh run().
        assert "identity" not in data


# --- Schema v2 write side (issue #224) ---


class TestCheckpointV2Schema:
    """Schema v2 is a strict superset of the six spec §5.3 fields."""

    def test_defaults_are_v2_in_flight(self):
        cp = Checkpoint(
            current_node="a",
            completed_nodes=["a"],
            context_snapshot={},
            timestamp="2026-08-14T00:00:00Z",
        )
        assert cp.schema_version == 2
        assert cp.run_state == "in_flight"
        assert cp.node_outcomes == {}
        assert cp.engine_state == {}
        assert cp.graph == {}

    def test_v2_round_trip(self, tmp_path):
        """All v2 fields survive save -> load."""
        cp = Checkpoint(
            current_node="b",
            completed_nodes=["a", "b"],
            context_snapshot={"k": "v"},
            timestamp="2026-08-14T00:00:00Z",
            node_retries={"b": 1},
            logs=["l1"],
            run_state="in_flight",
            node_outcomes={
                "b": {
                    "status": "success",
                    "preferred_label": "ship",
                    "suggested_next_ids": None,
                    "is_explicit": True,
                    "failure_reason": None,
                    "notes": "ok",
                }
            },
            engine_state={
                "iteration_count": 1,
                "node_execution_counts": {"a": 1, "b": 2},
                "goal_gate_retries": 0,
                "failure_routing_retries": 0,
                "steps": 3,
            },
            graph={"fingerprint": "sha256:abc", "dot_source": "digraph {}"},
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)

        assert loaded.schema_version == 2
        assert loaded.run_state == "in_flight"
        assert loaded.node_retries == {"b": 1}
        assert loaded.node_outcomes["b"]["is_explicit"] is True
        assert loaded.node_outcomes["b"]["preferred_label"] == "ship"
        assert loaded.engine_state["node_execution_counts"] == {"a": 1, "b": 2}
        assert loaded.graph_fingerprint == "sha256:abc"
        assert loaded.graph_dot_source == "digraph {}"

    def test_v1_checkpoint_loads_as_version_1(self, tmp_path):
        """A pre-resume checkpoint (no schema_version) reports v1, not v2."""
        path = str(tmp_path / "checkpoint.json")
        raw = {
            "current_node": "step",
            "completed_nodes": ["start", "step"],
            "context": {},
            "timestamp": "2025-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
        }
        with open(path, "w") as f:
            json.dump(raw, f)
        loaded = load_checkpoint(path)
        assert loaded.schema_version == 1
        assert loaded.graph_fingerprint == ""
        assert loaded.node_outcomes == {}

    def test_fingerprint_is_stable_and_source_sensitive(self):
        a = "digraph { start -> exit }"
        b = "digraph { start -> other }"
        assert fingerprint_dot_source(a) == fingerprint_dot_source(a)
        assert fingerprint_dot_source(a) != fingerprint_dot_source(b)
        assert fingerprint_dot_source(a).startswith("sha256:")
class TestEngineWritesV2:
    """Spec §5.3 rule 4 / DoD :1856 — node_retries is actually populated now."""

    @pytest.mark.asyncio
    async def test_engine_checkpoint_carries_v2_block(self, tmp_path):
        engine = _make_engine(_SIMPLE_DOT, backend=MockBackend("done"), logs_root=str(tmp_path))
        await engine.run()
        data = json.loads((tmp_path / "checkpoint.json").read_text())

        assert data["schema_version"] == 2
        # A finished run flips run_state so a resume of it is refused loudly.
        assert data["run_state"] == "completed"
        assert data["graph"]["fingerprint"].startswith("sha256:")
        assert "digraph" in data["graph"]["dot_source"]
        assert data["node_outcomes"]["step"]["status"] == "success"
        assert data["engine_state"]["node_execution_counts"]["step"] == 1
        # node_retries used to be written as {} unconditionally; a node that
        # consumed no retries now records 0 rather than being absent.
        assert data["node_retries"]["step"] == 0
