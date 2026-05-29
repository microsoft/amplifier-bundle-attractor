"""Tests for pipeline handler — DOT file path resolution and PipelineHandler.execute().

Spec coverage: resolve_dot_path, _expand_path_variables, PipelineHandler.execute().
"""

import json
import os
from unittest.mock import AsyncMock

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.pipeline import (
    PipelineHandler,
    resolve_dot_path,
)
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class _MockBackend:
    """Minimal mock backend — returns JSON success outcome for any node."""

    async def run(self, node, prompt, context):
        return json.dumps({"status": "success", "notes": f"mock: {node.id}"})


def _make_registry_factory():
    """Return a HandlerRegistry factory that wires in a mock backend."""
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry

    def factory():
        return HandlerRegistry(HandlerContext(backend=_MockBackend()))

    return factory


class TestResolveDotPath:
    """Tests for resolve_dot_path() and _expand_path_variables()."""

    def test_absolute_path_unchanged(self) -> None:
        """Absolute paths are returned as-is."""
        ctx = PipelineContext()
        result = resolve_dot_path("/abs/child.dot", source_dir="/parent", context=ctx)
        assert result == "/abs/child.dot"

    def test_relative_to_source_dir(self) -> None:
        """Relative paths resolve against source_dir."""
        ctx = PipelineContext()
        result = resolve_dot_path(
            "child.dot", source_dir="/parent/pipelines", context=ctx
        )
        assert result == "/parent/pipelines/child.dot"

    def test_relative_subdirectory(self) -> None:
        """Subdirectory paths resolve correctly."""
        ctx = PipelineContext()
        result = resolve_dot_path("sub/child.dot", source_dir="/parent", context=ctx)
        assert result == "/parent/sub/child.dot"

    def test_variable_expansion(self) -> None:
        """$language token is expanded from context."""
        ctx = PipelineContext()
        ctx.set("language", "python")
        result = resolve_dot_path(
            "$language/tasks.dot", source_dir="/pipelines", context=ctx
        )
        assert result == "/pipelines/python/tasks.dot"

    def test_variable_expansion_then_absolute(self) -> None:
        """If expansion produces an absolute path, use it."""
        ctx = PipelineContext()
        ctx.set("base", "/absolute/root")
        result = resolve_dot_path("$base/child.dot", source_dir="/parent", context=ctx)
        assert result == "/absolute/root/child.dot"

    def test_empty_source_dir_uses_cwd(self) -> None:
        """Empty source_dir falls back to os.getcwd()."""
        ctx = PipelineContext()
        result = resolve_dot_path("child.dot", source_dir="", context=ctx)
        assert result == os.path.join(os.getcwd(), "child.dot")

    def test_no_variable_in_path(self) -> None:
        """Paths without $ are not modified by context."""
        ctx = PipelineContext()
        ctx.set("language", "python")
        result = resolve_dot_path("plain/child.dot", source_dir="/parent", context=ctx)
        assert result == "/parent/plain/child.dot"

    def test_unknown_variable_left_unchanged(self) -> None:
        """Unknown $tokens survive expansion unchanged."""
        ctx = PipelineContext()
        ctx.set("language", "python")
        result = resolve_dot_path(
            "$unknown/$language/child.dot", source_dir="/parent", context=ctx
        )
        assert result == "/parent/$unknown/python/child.dot"

    def test_variable_adjacent_to_hyphen(self) -> None:
        """$language-review.dot expands $language, hyphen is not part of variable."""
        ctx = PipelineContext()
        ctx.set("language", "python")
        result = resolve_dot_path(
            "pipelines/$language-review.dot", source_dir="/root", context=ctx
        )
        assert result == "/root/pipelines/python-review.dot"

    def test_multiple_known_variables(self) -> None:
        """Multiple $tokens in a single path are all expanded."""
        ctx = PipelineContext()
        ctx.set("org", "acme")
        ctx.set("project", "widget")
        result = resolve_dot_path(
            "$org/$project-pipeline.dot", source_dir="/pipelines", context=ctx
        )
        assert result == "/pipelines/acme/widget-pipeline.dot"


# ---------------------------------------------------------------------------
# PipelineHandler.execute() tests
# ---------------------------------------------------------------------------

CHILD_DOT = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Do child work"]
    done [shape=Msquare]
    start -> work -> done
}
"""


def _write_child_dot(tmp_path):
    """Write CHILD_DOT to a file and return the path."""
    dot_file = tmp_path / "child.dot"
    dot_file.write_text(CHILD_DOT)
    return str(dot_file)


def _make_parent_graph(tmp_path):
    """Build a minimal parent graph with a pipeline node pointing to child.dot."""
    dot_path = _write_child_dot(tmp_path)
    return Graph(
        name="parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "sub": Node(
                id="sub",
                shape="component",
                type="pipeline",
                attrs={"dot_file": str(dot_path)},
            ),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="sub"),
            Edge(from_node="sub", to_node="done"),
        ],
        source_dir=str(tmp_path),
    )


class TestPipelineHandlerExecute:
    """Tests for PipelineHandler.execute()."""

    @pytest.mark.asyncio
    async def test_executes_child_pipeline_and_returns_success(self, tmp_path):
        """Valid child DOT executes and returns SUCCESS."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        outcome = await handler.execute(node, context, graph, logs_root)

        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_missing_dot_file_returns_fail(self, tmp_path):
        """Missing DOT file on disk returns FAIL with 'not found'."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        # Point to a non-existent file
        node.attrs["dot_file"] = str(tmp_path / "nonexistent.dot")
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        outcome = await handler.execute(node, context, graph, logs_root)

        assert outcome.status == StageStatus.FAIL
        assert outcome.failure_reason is not None
        assert "not found" in outcome.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_invalid_dot_source_returns_fail(self, tmp_path):
        """Invalid DOT syntax returns FAIL with 'parse' in failure_reason."""
        bad_dot = tmp_path / "bad.dot"
        bad_dot.write_text("this is not valid DOT syntax at all!!!")
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        node.attrs["dot_file"] = str(bad_dot)
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        outcome = await handler.execute(node, context, graph, logs_root)

        assert outcome.status == StageStatus.FAIL
        assert outcome.failure_reason is not None
        assert "parse" in outcome.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_missing_dot_file_attr_returns_fail(self, tmp_path):
        """Missing dot_file attribute returns FAIL with 'dot_file'."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        # Remove the dot_file attribute
        if "dot_file" in node.attrs:
            del node.attrs["dot_file"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        outcome = await handler.execute(node, context, graph, logs_root)

        assert outcome.status == StageStatus.FAIL
        assert outcome.failure_reason is not None
        assert "dot_file" in outcome.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_child_logs_written_to_subdirectory(self, tmp_path):
        """Child logs are written to {logs_root}/subgraph_{node_id}/."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        await handler.execute(node, context, graph, logs_root)

        manifest_path = os.path.join(logs_root, "subgraph_sub", "manifest.json")
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "graph_name" in manifest

    @pytest.mark.asyncio
    async def test_child_context_is_cloned(self, tmp_path):
        """Parent context is not polluted by child execution."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        context.set("parent_key", "parent_value")
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        await handler.execute(node, context, graph, logs_root)

        # Parent context should still have parent_key
        assert context.get("parent_key") == "parent_value"
        # Parent context should NOT have child engine's internal keys
        # (like graph.goal which is set by engine._initialize_context)
        assert context.get("outcome") is None

    @pytest.mark.asyncio
    async def test_execute_expands_context_variable_in_dot_file(self, tmp_path):
        """dot_file with $variable is expanded from context before resolution."""
        # Write child DOT into a subdirectory named "python"
        subdir = tmp_path / "python"
        subdir.mkdir()
        child_dot = subdir / "review.dot"
        child_dot.write_text(CHILD_DOT)

        # Build parent graph with $lang/review.dot as dot_file
        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="component",
                    type="pipeline",
                    attrs={"dot_file": "$lang/review.dot"},
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        context.set("lang", "python")
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        outcome = await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert outcome.status == StageStatus.SUCCESS


# ---------------------------------------------------------------------------
# PipelineHandler observability tests
# ---------------------------------------------------------------------------


class TestPipelineHandlerObservability:
    """Tests for subgraph observability — _subgraph_runs capture and event emission."""

    @pytest.mark.asyncio
    async def test_populates_subgraph_runs(self, tmp_path):
        """After execution, handler._subgraph_runs['sub'] contains all expected keys."""
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        await handler.execute(node, context, graph, logs_root)

        assert "sub" in handler._subgraph_runs
        run = handler._subgraph_runs["sub"]
        expected_keys = {
            "dot_file",
            "dot_source",
            "pipeline_id",
            "goal",
            "status",
            "execution_path",
            "node_outcomes",
            "total_elapsed_ms",
            "nodes_completed",
            "nodes_total",
        }
        assert expected_keys.issubset(run.keys())
        assert run["status"] == "success"
        assert isinstance(run["total_elapsed_ms"], float)
        assert isinstance(run["nodes_completed"], int)
        assert isinstance(run["nodes_total"], int)
        assert run["nodes_completed"] > 0

    @pytest.mark.asyncio
    async def test_emits_subgraph_start_event(self, tmp_path):
        """hooks.emit is called with 'pipeline:subgraph_start' including node_id."""
        hooks = AsyncMock()
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(hooks=hooks)
        await handler.execute(node, context, graph, logs_root)

        # Find the pipeline:subgraph_start call
        start_calls = [
            c for c in hooks.emit.call_args_list if c[0][0] == "pipeline:subgraph_start"
        ]
        assert len(start_calls) == 1
        data = start_calls[0][0][1]
        assert data["node_id"] == "sub"
        assert "dot_file" in data
        assert "pipeline_id" in data
        assert "goal" in data

    @pytest.mark.asyncio
    async def test_emits_subgraph_complete_event(self, tmp_path):
        """hooks.emit is called with 'pipeline:subgraph_complete' including node_id, status, duration_ms."""
        hooks = AsyncMock()
        graph = _make_parent_graph(tmp_path)
        node = graph.nodes["sub"]
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(
            hooks=hooks, handler_registry_factory=_make_registry_factory()
        )
        await handler.execute(node, context, graph, logs_root)

        # Find the pipeline:subgraph_complete call
        complete_calls = [
            c
            for c in hooks.emit.call_args_list
            if c[0][0] == "pipeline:subgraph_complete"
        ]
        assert len(complete_calls) == 1
        data = complete_calls[0][0][1]
        assert data["node_id"] == "sub"
        assert data["status"] == "success"
        assert "duration_ms" in data
        assert isinstance(data["duration_ms"], float)
        assert "pipeline_id" in data
        assert "nodes_completed" in data
        assert "nodes_total" in data


# ---------------------------------------------------------------------------
# PipelineHandler registration in HandlerRegistry
# ---------------------------------------------------------------------------


class TestPipelineHandlerRegistration:
    """Tests for PipelineHandler registration in HandlerRegistry."""

    def test_registry_resolves_pipeline_handler(self) -> None:
        """Node with shape=folder resolves to PipelineHandler instance."""
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        registry = HandlerRegistry(HandlerContext())
        node = Node(id="sub", shape="folder")
        handler = registry.get(node)
        assert isinstance(handler, PipelineHandler)

    def test_registry_resolves_explicit_type(self) -> None:
        """Node with type='pipeline' resolves to PipelineHandler instance."""
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        registry = HandlerRegistry(HandlerContext())
        node = Node(id="sub", shape="box", type="pipeline")
        handler = registry.get(node)
        assert isinstance(handler, PipelineHandler)


# ---------------------------------------------------------------------------
# End-to-end tests: parent DOT referencing child DOT via fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestPipelineHandlerE2E:
    """End-to-end tests using fixture DOT files for parent-child pipelines."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_child_dot(self, tmp_path):
        """Full engine run of parent_with_child.dot returns SUCCESS."""
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        parent_dot_path = os.path.join(FIXTURES_DIR, "parent_with_child.dot")
        with open(parent_dot_path) as f:
            dot_source = f.read()

        graph = parse_dot(dot_source)
        graph.source_dir = FIXTURES_DIR

        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=_MockBackend()))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_child_creates_log_subdirectory(self, tmp_path):
        """Child logs directory subgraph_review/ is created with manifest.json."""
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        parent_dot_path = os.path.join(FIXTURES_DIR, "parent_with_child.dot")
        with open(parent_dot_path) as f:
            dot_source = f.read()

        graph = parse_dot(dot_source)
        graph.source_dir = FIXTURES_DIR

        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext())
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        await engine.run()

        subgraph_dir = os.path.join(logs_root, "subgraph_review")
        assert os.path.isdir(subgraph_dir)
        manifest_path = os.path.join(subgraph_dir, "manifest.json")
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "graph_name" in manifest


# ---------------------------------------------------------------------------
# Interviewer forwarding tests
# ---------------------------------------------------------------------------

CHILD_DOT_WITH_HUMAN_GATE = """\
digraph child_human {
    start [shape=Mdiamond]
    gate [shape=hexagon, prompt="Approve to continue?"]
    done [shape=Msquare]
    start -> gate
    gate -> done [label="Approve"]
}
"""


class TestInterviewerForwarding:
    """Tests that PipelineHandler forwards its interviewer to child HandlerRegistry."""

    def test_registry_passes_interviewer_to_pipeline_handler(self) -> None:
        """HandlerRegistry.__init__ passes interviewer kwarg to the PipelineHandler.

        When a HandlerRegistry is created with ``interviewer=x``, the PipelineHandler
        stored in ``_handlers["pipeline"]`` must have ``_interviewer`` set to ``x``.
        Without this, nested pipelines routed through the registry won't forward the
        interviewer to their child registries, causing human gate nodes to fail.

        Expected failure: HandlerRegistry.__init__ creates PipelineHandler without
        passing ``interviewer``, so ``_handlers["pipeline"]._interviewer`` is ``None``
        instead of the supplied AutoApproveInterviewer.
        """
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry
        from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer = AutoApproveInterviewer()
        registry = HandlerRegistry(HandlerContext(interviewer=interviewer))

        pipeline_handler = registry._handlers["pipeline"]
        assert isinstance(pipeline_handler, PipelineHandler)
        assert pipeline_handler._interviewer is interviewer

    def test_clone_for_branch_preserves_pipeline_interviewer(self) -> None:
        """clone_for_branch preserves interviewer in the new PipelineHandler.

        When ``clone_for_branch`` creates a fresh PipelineHandler to replace the
        mutable original, it must forward ``original_pipeline._interviewer`` so that
        cloned branches retain the interviewer for their nested pipelines.

        Expected failure: clone_for_branch creates PipelineHandler without
        ``interviewer=original_pipeline._interviewer``, so the cloned handler's
        ``_interviewer`` is ``None`` instead of the supplied AutoApproveInterviewer.
        """
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry
        from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer = AutoApproveInterviewer()
        registry = HandlerRegistry(HandlerContext(interviewer=interviewer))

        branch_registry = registry.clone_for_branch()

        branch_pipeline_handler = branch_registry._handlers["pipeline"]
        assert isinstance(branch_pipeline_handler, PipelineHandler)
        assert branch_pipeline_handler._interviewer is interviewer

    @pytest.mark.asyncio
    async def test_child_registry_receives_interviewer(self, tmp_path):
        """PipelineHandler constructed with an interviewer forwards it to child HandlerRegistry.

        The child pipeline contains a hexagon (wait.human) node that requires an
        Interviewer to run. When PipelineHandler is constructed with an
        AutoApproveInterviewer, that interviewer must be forwarded to the child
        HandlerRegistry so the child's wait.human handler can use it.

        Expected failure: PipelineHandler does not accept an ``interviewer`` kwarg
        (or accepts it via **kwargs but does not store or forward it), so the child
        HandlerRegistry is created without the interviewer and the hexagon handler
        raises ``ValueError: HumanGateHandler requires an Interviewer``, causing
        the outcome to be FAIL rather than SUCCESS.
        """
        from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler  # noqa: F401
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        # Write child DOT with a hexagon (human gate) node to a temp file
        child_dot_path = tmp_path / "child_with_gate.dot"
        child_dot_path.write_text(CHILD_DOT_WITH_HUMAN_GATE)

        # Build parent graph with a folder node pointing to the child DOT
        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={"dot_file": str(child_dot_path)},
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")
        interviewer = AutoApproveInterviewer()

        # Create PipelineHandler WITH an AutoApproveInterviewer so the child
        # pipeline's human gate can be automatically approved.
        handler = PipelineHandler(interviewer=interviewer)
        outcome = await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        # The child pipeline should complete successfully because the interviewer
        # auto-approves the human gate node.
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_e2e_human_gate_in_child_pipeline(self, tmp_path):
        """Full engine run with human gate in child pipeline succeeds via AutoApproveInterviewer.

        Uses the full PipelineEngine (not just the handler). Writes a child DOT
        with a hexagon gate, writes a parent DOT referencing the child via a
        folder node, parses the parent DOT, creates a HandlerRegistry with
        AutoApproveInterviewer, and runs the engine end-to-end.

        The interviewer must be forwarded from the top-level HandlerRegistry
        through the PipelineHandler down to the child HandlerRegistry so the
        hexagon gate node can be auto-approved.
        """
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        # Write child DOT with hexagon gate to temp file
        child_dot_path = tmp_path / "child_with_gate.dot"
        child_dot_path.write_text(CHILD_DOT_WITH_HUMAN_GATE)

        # Write parent DOT referencing child via folder node
        parent_dot_source = """\
digraph parent_e2e {
    graph [goal="E2E test with human gate in child"]
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="child_with_gate.dot"]
    done [shape=Msquare]
    start -> sub -> done
}
"""
        parent_dot_path = tmp_path / "parent_e2e.dot"
        parent_dot_path.write_text(parent_dot_source)

        # Parse parent DOT and set source_dir so relative child path resolves
        graph = parse_dot(parent_dot_path.read_text())
        graph.source_dir = str(tmp_path)

        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(interviewer=AutoApproveInterviewer()))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS


# ---------------------------------------------------------------------------
# Outputs merge-back tests: declared outputs should merge back to parent
# ---------------------------------------------------------------------------

CHILD_DOT_SETS_CONTEXT = """\
digraph child_sets_context {
    start [shape=Mdiamond]
    work [prompt="Do child work"]
    done [shape=Msquare]
    start -> work -> done
}
"""


class TestOutputsMergeBack:
    """Tests that declared outputs merge back from child context to parent context."""

    @pytest.mark.asyncio
    async def test_declared_outputs_merge_back_to_parent(self, tmp_path):
        """Declared outputs='result,detail' merge back to parent context after child run.

        The folder node declares outputs='result,detail' and pre-seeds the child
        context with context.result='pass' and context.detail='All tests passed'.
        After execution, both keys should be present in the PARENT context.

        Expected failure: outputs attribute is currently ignored; parent context
        remains empty after child execution.
        """
        dot_file = tmp_path / "child.dot"
        dot_file.write_text(CHILD_DOT_SETS_CONTEXT)

        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={
                        "dot_file": str(dot_file),
                        "outputs": "result,detail",
                        "context.result": "pass",
                        "context.detail": "All tests passed",
                    },
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert context.get("result") == "pass"
        assert context.get("detail") == "All tests passed"

    @pytest.mark.asyncio
    async def test_undeclared_keys_do_not_merge_back(self, tmp_path):
        """Only declared outputs merge back; undeclared keys stay isolated.

        The folder node declares outputs='result' (only result) but also pre-seeds
        context.secret='should_not_leak' into the child context. After execution,
        result should be present in parent context (declared) but secret should NOT
        (not declared in outputs).

        Expected failure: outputs attribute is currently ignored; parent context
        remains empty after child execution, so result assertion fails first.
        """
        dot_file = tmp_path / "child.dot"
        dot_file.write_text(CHILD_DOT_SETS_CONTEXT)

        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={
                        "dot_file": str(dot_file),
                        "outputs": "result",
                        "context.result": "pass",
                        "context.secret": "should_not_leak",
                    },
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert context.get("result") == "pass"  # declared, should merge back
        assert context.get("secret") is None  # not declared, should NOT merge back

    @pytest.mark.asyncio
    async def test_outputs_not_merged_on_child_failure(self, tmp_path):
        """Outputs do not merge back to parent context when child pipeline fails.

        The folder node declares outputs='result' and pre-seeds
        context.result='should_not_leak'. The child DOT references a nonexistent
        file, so the handler returns FAIL before the merge-back code runs.

        After execution, outcome.status should be FAIL and context.get('result')
        should be None, verifying that the merge-back code only runs on
        outcome.is_success.

        Uses default PipelineHandler() (no factory).
        """
        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={
                        "dot_file": "nonexistent.dot",
                        "outputs": "result",
                        "context.result": "should_not_leak",
                    },
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler()
        outcome = await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert outcome.status == StageStatus.FAIL
        assert context.get("result") is None

    @pytest.mark.asyncio
    async def test_empty_outputs_attribute_is_noop(self, tmp_path):
        """Whitespace-and-comma-only outputs='  , , ' is treated as no outputs — nothing merges.

        The folder node has outputs='  , , ' (no real keys after stripping) and
        context.some_key='some_value' pre-seeded into the child context. After
        execution, context.get('some_key') must be None because the outputs list
        parses to empty and the merge-back loop never runs.
        """
        dot_file = tmp_path / "child.dot"
        dot_file.write_text(CHILD_DOT)

        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={
                        "dot_file": str(dot_file),
                        "outputs": "  , , ",
                        "context.some_key": "some_value",
                    },
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert context.get("some_key") is None

    @pytest.mark.asyncio
    async def test_no_outputs_attribute_preserves_isolation(self, tmp_path):
        """Folder node without any outputs attribute keeps full child context isolation.

        The folder node has no 'outputs' attribute at all but pre-seeds
        context.injected='value' into the child context. After execution,
        context.get('injected') must be None because, with no outputs declared,
        nothing is ever merged back to the parent context.
        """
        dot_file = tmp_path / "child.dot"
        dot_file.write_text(CHILD_DOT)

        graph = Graph(
            name="parent",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "sub": Node(
                    id="sub",
                    shape="folder",
                    type="pipeline",
                    attrs={
                        "dot_file": str(dot_file),
                        "context.injected": "value",
                    },
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="sub"),
                Edge(from_node="sub", to_node="done"),
            ],
            source_dir=str(tmp_path),
        )

        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        handler = PipelineHandler(handler_registry_factory=_make_registry_factory())
        await handler.execute(graph.nodes["sub"], context, graph, logs_root)

        assert context.get("injected") is None


# ---------------------------------------------------------------------------
# Regression tests: issue #249 — child HandlerRegistry subgraph_runner wiring
# ---------------------------------------------------------------------------

# Child DOT with a manager (shape=house) node.  Uses correct manager.* attribute
# names (not stack.* aliases) so max_cycles and actions are actually read by
# ManagerLoopHandler — keeping the test deterministic and fast.
_CHILD_DOT_WITH_MANAGER = """\
digraph child_manager {
    start [shape=Mdiamond]
    mgr [shape=house, "manager.max_cycles"="1", "manager.actions"="observe"]
    work [shape=box, prompt="child work"]
    done [shape=Msquare]
    start -> mgr
    mgr -> work
    work -> done
}
"""


def _make_parent_graph_with_manager_child(tmp_path):
    """Write _CHILD_DOT_WITH_MANAGER to tmp_path and return a parent Graph."""
    child_dot_path = tmp_path / "child_mgr.dot"
    child_dot_path.write_text(_CHILD_DOT_WITH_MANAGER)
    return Graph(
        name="parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "sub": Node(
                id="sub",
                shape="folder",
                type="pipeline",
                attrs={"dot_file": str(child_dot_path)},
            ),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="sub"),
            Edge(from_node="sub", to_node="done"),
        ],
        source_dir=str(tmp_path),
    )


class TestChildRegistrySubgraphRunnerWiring:
    """Regression tests for issue #249.

    PipelineHandler.execute() was building the child HandlerRegistry without
    any mechanism for child manager/parallel handlers to execute subgraphs.
    Any child node with shape=house or shape=component inside a shape=folder
    sub-pipeline therefore failed immediately with an error.

    After the refactor (issue #250), the engine passes itself to each handler
    via execute(engine=...).  ManagerLoopHandler and ParallelHandler use
    engine.run_subgraph() directly — no subgraph_runner kwarg or _runner field
    remains.  The original implementation check (._runner is not None) is
    replaced by a behavioral check: the manager node in a child pipeline must
    succeed end-to-end.
    """

    @pytest.mark.asyncio
    async def test_child_registry_has_subgraph_runners_wired(self, tmp_path):
        """Child manager/parallel nodes inside a folder pipeline must be able to run subgraphs.

        After the #250 refactor, this is verified behaviorally: the pipeline
        completes successfully, proving that ManagerLoopHandler received the
        engine and called engine.run_subgraph() without error.

        Preserving tracking reference: issue #249 root cause was missing wiring.
        The #250 refactor eliminates the wiring step entirely — engine carries
        itself, so there is nothing to forget to wire.
        """
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        graph = _make_parent_graph_with_manager_child(tmp_path)
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        # Canonical construction — no dance, no closure, no rewire.
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=HandlerRegistry(HandlerContext(backend=_MockBackend())),
            logs_root=logs_root,
        )
        outcome = await engine.run(goal="test wiring")

        # Behavioral assertion: the pipeline must succeed end-to-end.
        # If ManagerLoopHandler failed with "requires engine", outcome would be FAIL.
        pipeline_handler = engine.handler_registry._handlers["pipeline"]
        subgraph_run = pipeline_handler._subgraph_runs.get("sub")
        assert subgraph_run is not None, (
            "PipelineHandler did not record a subgraph run for node 'sub' — "
            "the parent engine may not have reached the folder node"
        )

        mgr_outcome_data = subgraph_run["node_outcomes"].get("mgr")
        assert mgr_outcome_data is not None, (
            "Manager node 'mgr' not found in child subgraph node_outcomes"
        )

        # Key regression assertion (issue #249): manager must NOT fail with missing-engine error.
        assert (
            mgr_outcome_data["failure_reason"]
            != "ManagerLoopHandler requires engine to be passed via execute(engine=...)"
        ), (
            "Manager node failed with engine-not-wired error — "
            "the #250 refactor should have eliminated this failure mode"
        )

        assert mgr_outcome_data["status"] == "success", (
            f"Manager node did not succeed — status={mgr_outcome_data['status']!r}, "
            f"failure_reason={mgr_outcome_data.get('failure_reason')!r}"
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_child_manager_node_runner_is_invoked(self, tmp_path):
        """E2E: parent folder -> child house manager must invoke its subgraph runner.

        Before the fix (issue #249):
          child manager:  FAIL "Manager loop requires a subgraph_runner"
          parent outcome: FAIL (edge selection re-surfaces child failure)

        After the fix (issue #249) and refactor (issue #250):
          child manager receives engine via execute(engine=...), calls
          engine.run_subgraph(), the mock backend handles the branch work
          node, manager completes in 1 cycle, child pipeline succeeds,
          and the parent pipeline succeeds end-to-end.

        Spec: issue #249 regression test.
        """
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        graph = _make_parent_graph_with_manager_child(tmp_path)
        context = PipelineContext()
        logs_root = str(tmp_path / "logs")

        # Canonical construction — no dance, no closure, no rewire.
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=HandlerRegistry(HandlerContext(backend=_MockBackend())),
            logs_root=logs_root,
        )

        outcome = await engine.run(goal="test child manager runner")

        # Retrieve the subgraph run data recorded by PipelineHandler.
        pipeline_handler = engine.handler_registry._handlers["pipeline"]
        subgraph_run = pipeline_handler._subgraph_runs.get("sub")
        assert subgraph_run is not None, (
            "PipelineHandler did not record a subgraph run for node 'sub' — "
            "check that the parent engine reached the folder node"
        )

        mgr_outcome_data = subgraph_run["node_outcomes"].get("mgr")
        assert mgr_outcome_data is not None, (
            "Manager node 'mgr' not found in child subgraph node_outcomes — "
            "the child pipeline may not have reached the manager node"
        )

        # Key regression assertion: manager must NOT fail with the missing-runner error.
        assert (
            mgr_outcome_data["failure_reason"]
            != "Manager loop requires a subgraph_runner"
        ), (
            "Manager node failed with 'Manager loop requires a subgraph_runner' — "
            "subgraph_runner not wired in child HandlerRegistry (issue #249)"
        )

        # With the fix the manager's subgraph_runner is invoked.  The mock backend
        # handles "work" successfully, manager completes in 1 cycle, child pipeline
        # succeeds, parent pipeline succeeds end-to-end.
        assert mgr_outcome_data["status"] == "success", (
            f"Manager node did not succeed — status={mgr_outcome_data['status']!r}, "
            f"failure_reason={mgr_outcome_data.get('failure_reason')!r}"
        )
        assert outcome.status == StageStatus.SUCCESS
