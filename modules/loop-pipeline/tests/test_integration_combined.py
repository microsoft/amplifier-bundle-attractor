"""Tests for Phase 4 Integration: combined conversational-gate + convergence-factory.

Validates both structure and end-to-end execution of demo-combined.dot,
which composes BOTH reusable patterns in sequence without requiring real API keys.

Test coverage:
- Structural: demo-combined.dot parses with correct node count and shapes
- Structural: exactly 2 folder nodes (one per pattern)
- Structural: first folder references conversational-gate.dot, second convergence-factory.dot
- Structural: both folder nodes have their respective context.* attributes
- Structural: sequential flow start -> gather_requirements -> generate_artifact -> done
- Execution: end-to-end combined run with QueueInterviewer + combined mock backend
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler
from amplifier_module_loop_pipeline.interviewer import Answer, Option, QueueInterviewer
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Repo root: modules/loop-pipeline/tests/ -> ../../..
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_PATTERNS_DIR = _REPO_ROOT / "examples" / "patterns"
_COMBINED_DOT = _PATTERNS_DIR / "demo-combined.dot"


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


class CombinedPatternBackend:
    """Mock backend for the combined pattern.

    Handles both child pipelines:
    - Gate pattern: eval node returns 'scored' immediately
    - Factory pattern: assess node returns 'converged' immediately
    - All other codergen nodes: return plain SUCCESS

    Records all node ids called for execution path verification.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext) -> Outcome:
        self.calls.append(node.id)
        if node.id == "eval":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="scored")
        if node.id == "assess":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


class MockToolHandler:
    """Returns SUCCESS for parallelogram/tool nodes (bypasses real shell execution).

    Registered as the 'tool' handler in the HandlerRegistry so that the
    validate node's ToolHandler is replaced during tests.
    """

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine=None,
    ) -> Outcome:
        return Outcome(status=StageStatus.SUCCESS, notes="Mock validation passed")


# ---------------------------------------------------------------------------
# Structural tests: Parse demo-combined.dot
# ---------------------------------------------------------------------------


class TestDemoCombinedParse:
    """Integration structural tests: demo-combined.dot parses to expected graph."""

    def test_file_exists(self):
        """Combined demo file exists at examples/patterns/demo-combined.dot."""
        assert _COMBINED_DOT.exists(), f"Demo file not found: {_COMBINED_DOT}"

    def test_parses_with_four_nodes(self):
        """demo-combined.dot parses into exactly 4 nodes: start, 2 folders, done."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        assert len(g.nodes) == 4, (
            f"Expected 4 nodes, got {len(g.nodes)}: {list(g.nodes.keys())}"
        )

    def test_has_two_folder_nodes(self):
        """Combined demo has exactly 2 folder nodes (gate + factory)."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        assert len(folder_nodes) == 2, (
            f"Expected 2 folder nodes, got {len(folder_nodes)}: "
            f"{[n.id for n in folder_nodes]}"
        )

    def test_first_folder_references_conversational_gate(self):
        """The first folder node (gather_requirements) references conversational-gate.dot."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        # The first folder in the sequence is gather_requirements
        gather_node = next(
            (n for n in folder_nodes if n.id == "gather_requirements"), None
        )
        assert gather_node is not None, (
            f"Expected 'gather_requirements' folder node, got: "
            f"{[n.id for n in folder_nodes]}"
        )
        dot_file = gather_node.attrs.get("dot_file", "")
        assert "conversational-gate.dot" in dot_file, (
            f"gather_requirements dot_file should reference conversational-gate.dot, "
            f"got {dot_file!r}"
        )

    def test_second_folder_references_convergence_factory(self):
        """The second folder node (generate_artifact) references convergence-factory.dot."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        generate_node = next(
            (n for n in folder_nodes if n.id == "generate_artifact"), None
        )
        assert generate_node is not None, (
            f"Expected 'generate_artifact' folder node, got: "
            f"{[n.id for n in folder_nodes]}"
        )
        dot_file = generate_node.attrs.get("dot_file", "")
        assert "convergence-factory.dot" in dot_file, (
            f"generate_artifact dot_file should reference convergence-factory.dot, "
            f"got {dot_file!r}"
        )

    def test_gather_requirements_has_gate_context_attrs(self):
        """gather_requirements folder node has all required gate context.* attributes."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        node = g.nodes.get("gather_requirements")
        assert node is not None, "Expected 'gather_requirements' node"
        assert "context.gate_topic" in node.attrs, (
            "gather_requirements missing context.gate_topic attr"
        )
        assert "context.gate_criteria" in node.attrs, (
            "gather_requirements missing context.gate_criteria attr"
        )
        assert "context.gate_output_path" in node.attrs, (
            "gather_requirements missing context.gate_output_path attr"
        )

    def test_generate_artifact_has_factory_context_attrs(self):
        """generate_artifact folder node has all required factory context.* attributes."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        node = g.nodes.get("generate_artifact")
        assert node is not None, "Expected 'generate_artifact' node"
        assert "context.artifact_goal" in node.attrs, (
            "generate_artifact missing context.artifact_goal attr"
        )
        assert "context.artifact_path" in node.attrs, (
            "generate_artifact missing context.artifact_path attr"
        )
        assert "context.validation_criteria" in node.attrs, (
            "generate_artifact missing context.validation_criteria attr"
        )
        assert "context.validation_command" in node.attrs, (
            "generate_artifact missing context.validation_command attr"
        )

    def test_sequential_flow(self):
        """start -> gather_requirements -> generate_artifact -> done linear chain exists."""
        source = _COMBINED_DOT.read_text()
        g = parse_dot(source)
        edge_map: dict[str, str] = {e.from_node: e.to_node for e in g.edges}
        start_node = next(
            (n.id for n in g.nodes.values() if n.shape == "Mdiamond"), None
        )
        assert start_node is not None, "No start (Mdiamond) node found"

        # Follow chain: start -> gather_requirements -> generate_artifact -> done (4 nodes)
        current = start_node
        visited = [current]
        for _ in range(3):  # 3 hops to reach all 4 nodes
            nxt = edge_map.get(current)
            if nxt is None:
                break
            visited.append(nxt)
            current = nxt

        assert len(visited) == 4, (
            f"Expected 4-node chain start->gather->generate->done, got chain: {visited}"
        )
        # Verify the node ids
        assert "gather_requirements" in visited, (
            f"Expected 'gather_requirements' in chain: {visited}"
        )
        assert "generate_artifact" in visited, (
            f"Expected 'generate_artifact' in chain: {visited}"
        )


# ---------------------------------------------------------------------------
# Execution test: end-to-end combined pipeline run
# ---------------------------------------------------------------------------


class TestCombinedExecution:
    """Integration execution tests: combined pipeline runs end-to-end with mocks."""

    @pytest.mark.asyncio
    async def test_end_to_end_combined_pipeline(self, tmp_path):
        """End-to-end: gate gathers requirements, factory generates artifact.

        Both child pipelines execute to completion using:
        - QueueInterviewer with one pre-scripted answer for the gate's ask node
        - CombinedPatternBackend that routes eval->scored and assess->converged
        - MockToolHandler bypasses real shell execution for the factory's validate node

        The pipeline handler must be wired with a handler_registry_factory that
        provides both the backend and the interviewer to each nested child pipeline.

        Expected nodes visited across the entire run (in child pipelines):
        - Gate child:    ask, eval, check  (conversational-gate.dot)
        - Factory child: generate, assess, check  (convergence-factory.dot)
        """
        source = _COMBINED_DOT.read_text()
        graph = parse_dot(source)
        # Set source_dir so PipelineHandler can resolve relative dot_file paths
        graph.source_dir = str(_PATTERNS_DIR)

        backend = CombinedPatternBackend()

        # One answer for the gate's ask (hexagon) node
        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )

        # Factory to build child registries with both backend + interviewer + tool mock
        def child_registry_factory() -> HandlerRegistry:
            registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
            registry.register("tool", MockToolHandler())
            return registry

        # Build parent registry; override pipeline handler to use the factory
        parent_registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        parent_registry.register("tool", MockToolHandler())
        parent_registry.register(
            "pipeline",
            PipelineHandler(
                handler_registry_factory=child_registry_factory,
                backend=backend,
            ),
        )

        context = PipelineContext()

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=parent_registry,
            logs_root=str(tmp_path / "logs"),
        )

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status}. "
            f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
        )

    @pytest.mark.asyncio
    async def test_combined_visits_both_child_patterns(self, tmp_path):
        """Execution visits nodes from BOTH child pipelines.

        The backend's call list must include nodes from both:
        - conversational-gate.dot child: eval (and ask via wait.human handler)
        - convergence-factory.dot child: generate and assess
        """
        source = _COMBINED_DOT.read_text()
        graph = parse_dot(source)
        graph.source_dir = str(_PATTERNS_DIR)

        backend = CombinedPatternBackend()

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )

        def child_registry_factory() -> HandlerRegistry:
            registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
            registry.register("tool", MockToolHandler())
            return registry

        parent_registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        parent_registry.register("tool", MockToolHandler())
        parent_registry.register(
            "pipeline",
            PipelineHandler(
                handler_registry_factory=child_registry_factory,
                backend=backend,
            ),
        )

        context = PipelineContext()
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=parent_registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        # Gate pattern nodes (conversational-gate.dot)
        assert "eval" in backend.calls, (
            f"Expected 'eval' (gate pattern) in backend calls: {backend.calls}"
        )

        # Factory pattern nodes (convergence-factory.dot)
        assert "generate" in backend.calls, (
            f"Expected 'generate' (factory pattern) in backend calls: {backend.calls}"
        )
        assert "assess" in backend.calls, (
            f"Expected 'assess' (factory pattern) in backend calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_gate_eval_returns_scored(self, tmp_path):
        """The gate pattern's eval node is called and returns 'scored' (single pass)."""
        source = _COMBINED_DOT.read_text()
        graph = parse_dot(source)
        graph.source_dir = str(_PATTERNS_DIR)

        backend = CombinedPatternBackend()

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )

        def child_registry_factory() -> HandlerRegistry:
            registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
            registry.register("tool", MockToolHandler())
            return registry

        parent_registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        parent_registry.register("tool", MockToolHandler())
        parent_registry.register(
            "pipeline",
            PipelineHandler(
                handler_registry_factory=child_registry_factory,
                backend=backend,
            ),
        )

        context = PipelineContext()
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=parent_registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        # eval called exactly once (scored immediately, no loop)
        eval_count = backend.calls.count("eval")
        assert eval_count == 1, (
            f"Expected eval called once (immediate score), got {eval_count}. "
            f"All backend calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_factory_assess_returns_converged(self, tmp_path):
        """The factory pattern's assess node is called and returns 'converged' (single pass)."""
        source = _COMBINED_DOT.read_text()
        graph = parse_dot(source)
        graph.source_dir = str(_PATTERNS_DIR)

        backend = CombinedPatternBackend()

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )

        def child_registry_factory() -> HandlerRegistry:
            registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
            registry.register("tool", MockToolHandler())
            return registry

        parent_registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        parent_registry.register("tool", MockToolHandler())
        parent_registry.register(
            "pipeline",
            PipelineHandler(
                handler_registry_factory=child_registry_factory,
                backend=backend,
            ),
        )

        context = PipelineContext()
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=parent_registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        # assess called exactly once (converged immediately, no refinement loop)
        assess_count = backend.calls.count("assess")
        assert assess_count == 1, (
            f"Expected assess called once (immediate convergence), got {assess_count}. "
            f"All backend calls: {backend.calls}"
        )
