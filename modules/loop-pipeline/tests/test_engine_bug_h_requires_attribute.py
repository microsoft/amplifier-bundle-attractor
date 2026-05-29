"""Regression tests for Bug H — box handler doesn't validate declared inputs.

Spec coverage: Sections 2.6 (Node Attributes), 4.5 (CodergenBackend).

Root cause (confirmed pre-fix):
  When a node's execution context expected input files are absent (e.g. because
  upstream parallel branches didn't all run due to Bug G), the LLM agent runs
  anyway and fabricates the missing inputs. The engine has no mechanism to
  enforce pre-execution file existence checks.

Fix (engine.py, before execute_with_retry):
  Support a ``requires=`` node attribute (comma-separated relative file paths).
  Before the handler runs, the engine validates all declared paths exist under
  ``context.target_dir`` (or ``os.getcwd()`` as fallback). If any are missing,
  the node fails with a clear error *before* the handler runs.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_with_graph(
    graph: Graph, backend: object, tmp_path: object
) -> PipelineEngine:
    """Build a plain engine (no subgraph_runner needed — all box nodes)."""
    context = PipelineContext()
    # Set context.target_dir so file checks are relative to tmp_path
    context.set("context.target_dir", str(tmp_path))
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Bug H regression: requires= validation blocks handler when files missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_missing_file_fails_node_before_handler_runs(tmp_path):
    """REGRESSION: node with requires= and missing file must FAIL before handler.

    Before the fix: handler runs regardless → LLM fabricates missing inputs.
    After the fix: engine detects missing file, fails node with clear error,
                   handler is never invoked.
    """
    handler_called = False

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            nonlocal handler_called
            handler_called = True
            return "done"

    graph = Graph(
        name="test-requires",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(
                id="work",
                shape="box",
                prompt="Do work",
                attrs={"requires": ".ai/variant_opus.md"},
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )

    # DO NOT create .ai/variant_opus.md — it must be missing
    engine = _make_engine_with_graph(graph, TrackingBackend(), tmp_path)
    await engine.run()

    # BUG H fix: handler must NOT have been called
    assert not handler_called, (
        "Handler was called despite missing required file — "
        "LLM fabrication risk (Bug H not fixed)"
    )

    # The pipeline should fail (or work node should be recorded as failed)
    work_outcome = engine.node_outcomes.get("work")
    assert work_outcome is not None, "work node was never recorded in node_outcomes"
    assert work_outcome.status == StageStatus.FAIL, (
        f"work node should have FAIL status, got {work_outcome.status}"
    )

    # The failure reason must name the missing file
    assert work_outcome.failure_reason is not None
    assert "variant_opus.md" in work_outcome.failure_reason or "variant_opus.md" in (
        work_outcome.notes or ""
    ), (
        f"Failure reason must name the missing file. Got: {work_outcome.failure_reason!r}, "
        f"notes: {work_outcome.notes!r}"
    )


@pytest.mark.asyncio
async def test_requires_all_present_handler_runs_normally(tmp_path):
    """Node with requires= and all files present: handler executes normally."""
    handler_called = False

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            nonlocal handler_called
            handler_called = True
            return "done"

    # Create the required files
    ai_dir = tmp_path / ".ai"
    ai_dir.mkdir()
    (ai_dir / "variant_opus.md").write_text("opus proposal")
    (ai_dir / "variant_sonnet.md").write_text("sonnet proposal")

    graph = Graph(
        name="test-requires-present",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "consolidate": Node(
                id="consolidate",
                shape="box",
                prompt="Consolidate variants",
                attrs={"requires": ".ai/variant_opus.md, .ai/variant_sonnet.md"},
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="consolidate"),
            Edge(from_node="consolidate", to_node="exit"),
        ],
    )

    engine = _make_engine_with_graph(graph, TrackingBackend(), tmp_path)
    outcome = await engine.run()

    # All files present — handler must run
    assert handler_called, (
        "Handler was NOT called despite all required files being present"
    )
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline should succeed when required files are present, got {outcome.status}"
    )


@pytest.mark.asyncio
async def test_no_requires_attr_behavior_unchanged(tmp_path):
    """Nodes without requires= continue to execute regardless of filesystem state."""
    handler_called = False

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            nonlocal handler_called
            handler_called = True
            return "done"

    # Node with NO requires= attribute
    graph = Graph(
        name="test-no-requires",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(
                id="work",
                shape="box",
                prompt="Do work without file requirements",
                # no attrs["requires"]
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )

    engine = _make_engine_with_graph(graph, TrackingBackend(), tmp_path)
    outcome = await engine.run()

    assert handler_called, "Handler must run when no requires= attribute is present"
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)


@pytest.mark.asyncio
async def test_requires_partial_missing_reports_all_missing_files(tmp_path):
    """When some required files are present and some are not, all missing ones are reported."""
    ai_dir = tmp_path / ".ai"
    ai_dir.mkdir()
    (ai_dir / "variant_haiku.md").write_text("haiku proposal")
    # variant_opus.md and variant_sonnet.md are NOT created

    class NoopBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            return "done"

    graph = Graph(
        name="test-partial-missing",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "consolidate": Node(
                id="consolidate",
                shape="box",
                prompt="Consolidate all three variants",
                attrs={
                    "requires": ".ai/variant_haiku.md, .ai/variant_opus.md, .ai/variant_sonnet.md"
                },
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="consolidate"),
            Edge(from_node="consolidate", to_node="exit"),
        ],
    )

    engine = _make_engine_with_graph(graph, NoopBackend(), tmp_path)
    await engine.run()

    work_outcome = engine.node_outcomes.get("consolidate")
    assert work_outcome is not None
    assert work_outcome.status == StageStatus.FAIL

    # Both missing files must be named in the failure
    failure_text = (work_outcome.failure_reason or "") + (work_outcome.notes or "")
    assert "variant_opus.md" in failure_text, (
        f"variant_opus.md should appear in failure: {failure_text!r}"
    )
    assert "variant_sonnet.md" in failure_text, (
        f"variant_sonnet.md should appear in failure: {failure_text!r}"
    )


@pytest.mark.asyncio
async def test_requires_uses_context_target_dir_for_path_resolution(tmp_path):
    """requires= paths are resolved relative to context.target_dir.

    The engine must use context.target_dir (not logs_root or cwd) when
    context.target_dir is set.
    """
    # Create a separate target_dir that is different from tmp_path
    target_dir = tmp_path / "workspace"
    target_dir.mkdir()
    ai_dir = target_dir / ".ai"
    ai_dir.mkdir()
    (ai_dir / "input.md").write_text("input content")

    handler_called = False

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            nonlocal handler_called
            handler_called = True
            return "done"

    graph = Graph(
        name="test-target-dir",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(
                id="work",
                shape="box",
                prompt="Use the input",
                attrs={"requires": ".ai/input.md"},
            ),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="exit"),
        ],
    )

    # Point context.target_dir at the workspace subdirectory (not tmp_path)
    context = PipelineContext()
    context.set("context.target_dir", str(target_dir))
    registry = HandlerRegistry(HandlerContext(backend=TrackingBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),  # different from target_dir
    )
    outcome = await engine.run()

    # File exists under target_dir — handler must run
    assert handler_called, (
        "Handler was not called — requires= path not resolved relative to context.target_dir"
    )
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
