"""Tests for artifact store wiring into the pipeline engine (Task 1.5).

Verifies that PipelineEngine instantiates an ArtifactStore and makes
it accessible for handlers to use.

Spec coverage: ART-001-004, Section 5.5.
"""

import pytest

from amplifier_module_loop_pipeline.artifacts import ArtifactStore
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockBackend:
    """Backend that returns a fixed string for every call."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return "done"


def _make_engine(tmp_path) -> PipelineEngine:
    """Build a minimal engine with a simple start -> exit graph."""
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[Edge(from_node="start", to_node="exit")],
    )
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=MockBackend())),
        logs_root=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_has_artifact_store(tmp_path):
    """Engine must expose an artifact store after construction."""
    engine = _make_engine(tmp_path)
    assert hasattr(engine, "artifact_store")
    assert engine.artifact_store is not None


@pytest.mark.asyncio
async def test_engine_artifact_store_is_correct_type(tmp_path):
    """Engine's artifact store must be an ArtifactStore instance."""
    engine = _make_engine(tmp_path)
    assert isinstance(engine.artifact_store, ArtifactStore)


@pytest.mark.asyncio
async def test_engine_artifact_store_is_usable(tmp_path):
    """Artifact store can store and retrieve artifacts."""
    engine = _make_engine(tmp_path)
    artifact = engine.artifact_store.store("test_output", "hello world")
    assert artifact.name == "test_output"
    assert artifact.data == "hello world"

    # Retrieve it
    retrieved = engine.artifact_store.get("test_output")
    assert retrieved is not None
    assert retrieved.data == "hello world"


@pytest.mark.asyncio
async def test_engine_artifact_store_uses_logs_root(tmp_path):
    """Artifact store base dir matches the engine's logs_root."""
    engine = _make_engine(tmp_path)
    # The store's base dir should be the engine's logs_root
    assert engine.artifact_store._base_dir == str(tmp_path)


@pytest.mark.asyncio
async def test_engine_artifact_store_survives_run(tmp_path):
    """Artifact store is available before, during, and after engine.run()."""
    engine = _make_engine(tmp_path)

    # Store before run
    engine.artifact_store.store("pre_run", "before")

    await engine.run()

    # Store after run
    engine.artifact_store.store("post_run", "after")

    assert engine.artifact_store.get("pre_run") is not None
    assert engine.artifact_store.get("post_run") is not None
    assert set(engine.artifact_store.list()) == {"pre_run", "post_run"}
