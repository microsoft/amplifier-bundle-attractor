"""Tests for P6: Convergence Factory Pattern.

Validates both structure and execution of the reusable convergence-factory.dot
pattern and its demo parent pipeline without requiring real API keys.

Test coverage:
- Structural parse tests for convergence-factory.dot (7 nodes, correct shapes, edges)
- Structural parse tests for demo-convergence-factory.dot (folder node, context attrs)
- Structural: edge conditions correct (converged vs refine routing)
- Structural: loop_restart edge from feedback -> generate exists
- Structural: prompt templates reference expected context variables
- Execution: single-pass convergence (assess returns converged on first pass)
- Execution: one-refinement convergence (assess returns refine then converged)
- Execution: execution path verification via backend call tracking
- Execution: context variables expand in prompts ($artifact_goal, etc.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Repo root: modules/loop-pipeline/tests/ -> ../../..
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_PATTERNS_DIR = _REPO_ROOT / "examples" / "patterns"
_FACTORY_DOT = _PATTERNS_DIR / "convergence-factory.dot"
_DEMO_DOT = _PATTERNS_DIR / "demo-convergence-factory.dot"


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


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


class ConvergedBackend:
    """Always returns preferred_label='converged' — simulates immediate convergence."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> Outcome:
        self.calls.append(node.id)
        if node.id == "assess":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


class RefineThenConvergeBackend:
    """Returns 'refine' on first assess call, 'converged' on subsequent calls.

    All generate and feedback nodes return plain SUCCESS.
    Tracks ALL node executions across loop_restart iterations.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._assess_count = 0

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> Outcome:
        self.calls.append(node.id)
        if node.id == "assess":
            self._assess_count += 1
            if self._assess_count == 1:
                return Outcome(status=StageStatus.SUCCESS, preferred_label="refine")
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


class CapturingBackend:
    """Records node_id → latest prompt text. Converges immediately on assess."""

    def __init__(self) -> None:
        self.prompts: dict[str, str] = {}

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> Outcome:
        self.prompts[node.id] = prompt
        if node.id == "assess":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


# ---------------------------------------------------------------------------
# Helper to build a PipelineEngine for the factory pattern
# ---------------------------------------------------------------------------


def _make_factory_engine(
    backend: object,
    tmp_path: Path,
    extra_ctx: dict[str, str] | None = None,
) -> PipelineEngine:
    """Return a PipelineEngine wired with mock backends for the factory pattern.

    Registers MockToolHandler for the 'tool' handler type so the validate
    (parallelogram) node doesn't attempt real shell execution.
    """
    source = _FACTORY_DOT.read_text()
    graph = parse_dot(source)

    ctx = PipelineContext()
    # Inject required context variables
    ctx.set("artifact_goal", "Test artifact goal")
    ctx.set("artifact_path", "test_artifact.py")
    ctx.set("validation_criteria", "File exists and contains expected content")
    ctx.set("validation_command", "echo validation passed")
    if extra_ctx:
        for key, value in extra_ctx.items():
            ctx.set(key, value)

    registry = HandlerRegistry(HandlerContext(backend=backend))
    registry.register("tool", MockToolHandler())

    return PipelineEngine(
        graph=graph,
        context=ctx,
        handler_registry=registry,
        logs_root=str(tmp_path / "logs"),
    )


# ---------------------------------------------------------------------------
# Structural tests: Parse convergence-factory.dot
# ---------------------------------------------------------------------------


class TestConvergenceFactoryParse:
    """P6 structural tests: convergence-factory.dot parses to expected graph."""

    def test_file_exists(self):
        """Pattern file exists at examples/patterns/convergence-factory.dot."""
        assert _FACTORY_DOT.exists(), f"Pattern file not found: {_FACTORY_DOT}"

    def test_parses_with_seven_nodes(self):
        """convergence-factory.dot parses into exactly 7 nodes."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        assert len(g.nodes) == 7, (
            f"Expected 7 nodes, got {len(g.nodes)}: {list(g.nodes.keys())}"
        )

    def test_node_ids_include_core_names(self):
        """Pattern includes nodes: start, generate, validate, assess, check, feedback, done."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        expected = {
            "start",
            "generate",
            "validate",
            "assess",
            "check",
            "feedback",
            "done",
        }
        actual = set(g.nodes.keys())
        assert expected == actual, f"Expected node IDs {expected}, got {actual}"

    def test_node_shapes(self):
        """Pattern has correct shapes: Mdiamond/Msquare/parallelogram/box."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)

        assert g.nodes["start"].shape == "Mdiamond", (
            f"Expected start shape=Mdiamond, got {g.nodes['start'].shape!r}"
        )
        assert g.nodes["done"].shape == "Msquare", (
            f"Expected done shape=Msquare, got {g.nodes['done'].shape!r}"
        )
        assert g.nodes["check"].shape == "parallelogram", (
            f"Expected check shape=parallelogram, got {g.nodes['check'].shape!r}"
        )
        assert g.nodes["validate"].shape == "parallelogram", (
            f"Expected validate shape=parallelogram, got {g.nodes['validate'].shape!r}"
        )
        # generate, assess, feedback should be box (default codergen)
        for node_id in ("generate", "assess", "feedback"):
            shape = g.nodes[node_id].shape
            assert shape in ("box", ""), (
                f"Expected {node_id} shape=box or default, got {shape!r}"
            )

    def test_has_seven_edges(self):
        """Pattern has exactly 7 edges."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        assert len(g.edges) == 7, (
            f"Expected 7 edges, got {len(g.edges)}: "
            f"{[(e.from_node, e.to_node, e.label) for e in g.edges]}"
        )

    def test_check_has_converged_and_refine_edges(self):
        """check node has two conditional edges: 'converged' -> done, 'refine' -> feedback."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        check_edges = [e for e in g.edges if e.from_node == "check"]
        assert len(check_edges) == 2, (
            f"Expected 2 edges from 'check', got {len(check_edges)}"
        )
        # Both edges should have conditions
        for e in check_edges:
            assert e.condition, (
                f"Edge check->{e.to_node} should have a condition, got {e.condition!r}"
            )
        labels = {e.label for e in check_edges}
        assert "converged" in labels, (
            f"Expected 'converged' edge from check, got labels={labels}"
        )
        assert "refine" in labels, (
            f"Expected 'refine' edge from check, got labels={labels}"
        )

    def test_converged_edge_goes_to_done(self):
        """The 'converged' conditional edge from check routes to done."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        converged_edges = [
            e for e in g.edges if e.from_node == "check" and e.label == "converged"
        ]
        assert len(converged_edges) == 1, (
            "Expected exactly one 'converged' edge from check"
        )
        assert converged_edges[0].to_node == "done", (
            f"Expected 'converged' edge to go to 'done', got {converged_edges[0].to_node!r}"
        )

    def test_refine_edge_goes_to_feedback(self):
        """The 'refine' conditional edge from check routes to feedback."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        refine_edges = [
            e for e in g.edges if e.from_node == "check" and e.label == "refine"
        ]
        assert len(refine_edges) == 1, "Expected exactly one 'refine' edge from check"
        assert refine_edges[0].to_node == "feedback", (
            f"Expected 'refine' edge to go to 'feedback', got {refine_edges[0].to_node!r}"
        )

    def test_feedback_to_generate_has_loop_restart(self):
        """The feedback -> generate edge has loop_restart=true."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        fb_to_gen = [
            e for e in g.edges if e.from_node == "feedback" and e.to_node == "generate"
        ]
        assert len(fb_to_gen) == 1, "Expected exactly one feedback->generate edge"
        edge = fb_to_gen[0]
        # The parser may store as True (bool) or "true" (string) depending on parsing
        loop_val = edge.loop_restart
        assert loop_val is True or loop_val == "true", (
            f"Expected feedback->generate loop_restart=true, got {loop_val!r}"
        )

    def test_generate_prompt_references_artifact_variables(self):
        """generate node's prompt contains $artifact_goal and $artifact_path."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        prompt = g.nodes["generate"].prompt
        assert "$artifact_goal" in prompt, (
            f"Expected '$artifact_goal' in generate prompt, got: {prompt!r}"
        )
        assert "$artifact_path" in prompt, (
            f"Expected '$artifact_path' in generate prompt, got: {prompt!r}"
        )

    def test_assess_prompt_references_criteria_variable(self):
        """assess node's prompt contains $validation_criteria."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        prompt = g.nodes["assess"].prompt
        assert "$validation_criteria" in prompt, (
            f"Expected '$validation_criteria' in assess prompt, got: {prompt!r}"
        )
        assert "$artifact_goal" in prompt, (
            f"Expected '$artifact_goal' in assess prompt, got: {prompt!r}"
        )

    def test_feedback_prompt_references_artifact_and_criteria(self):
        """feedback node's prompt contains $artifact_goal and $validation_criteria."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        prompt = g.nodes["feedback"].prompt
        assert "$artifact_goal" in prompt, (
            f"Expected '$artifact_goal' in feedback prompt, got: {prompt!r}"
        )
        assert "$validation_criteria" in prompt, (
            f"Expected '$validation_criteria' in feedback prompt, got: {prompt!r}"
        )

    def test_validate_node_has_tool_command_attribute(self):
        """validate node has tool_command attribute referencing $validation_command."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        validate_node = g.nodes["validate"]
        tool_cmd = validate_node.attrs.get("tool_command", "")
        assert tool_cmd, (
            "Expected validate node to have tool_command attribute, got empty"
        )
        assert "$validation_command" in tool_cmd, (
            f"Expected tool_command to reference $validation_command, got {tool_cmd!r}"
        )

    def test_linear_flow_start_to_check(self):
        """Verify the linear chain: start->generate->validate->assess->check."""
        source = _FACTORY_DOT.read_text()
        g = parse_dot(source)
        edge_map = {e.from_node: e.to_node for e in g.edges if not e.condition}
        expected_chain = ["start", "generate", "validate", "assess", "check"]
        current = "start"
        visited = [current]
        for _ in range(4):
            nxt = edge_map.get(current)
            if nxt is None:
                break
            visited.append(nxt)
            current = nxt
        assert visited == expected_chain, (
            f"Expected linear chain {expected_chain}, got {visited}"
        )


# ---------------------------------------------------------------------------
# Structural tests: Parse demo-convergence-factory.dot
# ---------------------------------------------------------------------------


class TestDemoConvergenceFactoryParse:
    """P6 structural tests: demo-convergence-factory.dot parses to expected graph."""

    def test_demo_file_exists(self):
        """Demo pipeline file exists at examples/patterns/demo-convergence-factory.dot."""
        assert _DEMO_DOT.exists(), f"Demo file not found: {_DEMO_DOT}"

    def test_demo_parses_ok(self):
        """demo-convergence-factory.dot parses without errors."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        assert len(g.nodes) > 0

    def test_demo_has_folder_node(self):
        """Demo pipeline has a folder node pointing to the factory pattern."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        assert len(folder_nodes) >= 1, (
            f"Expected at least 1 folder node, got {len(folder_nodes)}"
        )

    def test_folder_node_references_convergence_factory(self):
        """The folder node references convergence-factory.dot."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        for node in folder_nodes:
            dot_file = node.attrs.get("dot_file", "")
            assert "convergence-factory.dot" in dot_file, (
                f"Node {node.id!r} dot_file should reference convergence-factory.dot, "
                f"got {dot_file!r}"
            )

    def test_folder_node_has_all_required_context_attrs(self):
        """The folder node has all 4 required context.* attributes."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        for node in folder_nodes:
            assert "context.artifact_goal" in node.attrs, (
                f"Node {node.id!r} missing context.artifact_goal attr"
            )
            assert "context.artifact_path" in node.attrs, (
                f"Node {node.id!r} missing context.artifact_path attr"
            )
            assert "context.validation_criteria" in node.attrs, (
                f"Node {node.id!r} missing context.validation_criteria attr"
            )
            assert "context.validation_command" in node.attrs, (
                f"Node {node.id!r} missing context.validation_command attr"
            )

    def test_demo_has_start_and_done_nodes(self):
        """Demo has a start (Mdiamond) and done (Msquare) node."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        start_nodes = [n for n in g.nodes.values() if n.shape == "Mdiamond"]
        done_nodes = [n for n in g.nodes.values() if n.shape == "Msquare"]
        assert len(start_nodes) == 1, f"Expected 1 start node, got {len(start_nodes)}"
        assert len(done_nodes) == 1, f"Expected 1 done node, got {len(done_nodes)}"

    def test_demo_linear_flow(self):
        """start -> generate_utils -> done linear chain exists."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        edge_map = {e.from_node: e.to_node for e in g.edges}
        start_node = next(
            (n.id for n in g.nodes.values() if n.shape == "Mdiamond"), None
        )
        assert start_node is not None, "No start (Mdiamond) node found"

        # Follow the chain: start -> folder -> done (3 nodes)
        current = start_node
        visited = [current]
        for _ in range(2):
            nxt = edge_map.get(current)
            if nxt is None:
                break
            visited.append(nxt)
            current = nxt

        assert len(visited) == 3, (
            f"Expected 3-node chain start->folder->done, got chain: {visited}"
        )

    def test_demo_artifact_goal_references_greet_function(self):
        """The demo artifact_goal context attr references the greet function."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        assert folder_nodes, "No folder nodes found"
        goal = folder_nodes[0].attrs.get("context.artifact_goal", "")
        assert "greet" in goal.lower(), (
            f"Expected 'greet' in artifact_goal, got: {goal!r}"
        )


# ---------------------------------------------------------------------------
# Execution tests: run the convergence factory pipeline with mock backends
# ---------------------------------------------------------------------------


class TestConvergenceFactoryExecution:
    """P6 execution tests: pipeline runs end-to-end using mock backends."""

    @pytest.mark.asyncio
    async def test_single_pass_convergence(self, tmp_path):
        """Single-pass: generate -> validate -> assess(converged) -> check -> done.

        When assess returns 'converged' immediately, the pipeline should
        complete in one iteration without visiting feedback at all.
        """
        backend = ConvergedBackend()
        engine = _make_factory_engine(backend, tmp_path)

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status}. "
            f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
        )
        # feedback should NOT have been called
        assert "feedback" not in backend.calls, (
            f"Expected feedback NOT visited in single-pass convergence, "
            f"but backend calls included feedback: {backend.calls}"
        )
        # generate and assess should have been called exactly once
        assert backend.calls.count("generate") == 1, (
            f"Expected generate called once, got {backend.calls.count('generate')}. "
            f"All calls: {backend.calls}"
        )
        assert backend.calls.count("assess") == 1, (
            f"Expected assess called once, got {backend.calls.count('assess')}. "
            f"All calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_single_pass_execution_path_order(self, tmp_path):
        """Single-pass path visits nodes in order: generate, assess, (no feedback)."""
        backend = ConvergedBackend()
        engine = _make_factory_engine(backend, tmp_path)

        await engine.run()

        # Verify generate comes before assess in backend call order
        calls = backend.calls
        assert "generate" in calls, f"Expected 'generate' in calls: {calls}"
        assert "assess" in calls, f"Expected 'assess' in calls: {calls}"
        gen_idx = calls.index("generate")
        assess_idx = calls.index("assess")
        assert gen_idx < assess_idx, (
            f"Expected generate before assess, got calls: {calls}"
        )

    @pytest.mark.asyncio
    async def test_one_refinement_convergence(self, tmp_path):
        """One-refinement: assess returns 'refine' first, then 'converged'.

        Expected path:
          Iteration 1: generate -> validate -> assess(refine) -> check -> feedback
          (loop_restart) -> generate -> validate -> assess(converged) -> check -> done

        Pipeline should succeed after exactly one refinement loop.
        """
        backend = RefineThenConvergeBackend()
        engine = _make_factory_engine(backend, tmp_path)

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS after one refinement, got {outcome.status}. "
            f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
        )

    @pytest.mark.asyncio
    async def test_one_refinement_generate_called_twice(self, tmp_path):
        """In one-refinement path: generate is visited twice (once per iteration)."""
        backend = RefineThenConvergeBackend()
        engine = _make_factory_engine(backend, tmp_path)

        await engine.run()

        gen_count = backend.calls.count("generate")
        assert gen_count == 2, (
            f"Expected generate called twice (once per iteration), got {gen_count}. "
            f"All calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_one_refinement_assess_called_twice(self, tmp_path):
        """In one-refinement path: assess is called twice (refine then converged)."""
        backend = RefineThenConvergeBackend()
        engine = _make_factory_engine(backend, tmp_path)

        await engine.run()

        assess_count = backend.calls.count("assess")
        assert assess_count == 2, (
            f"Expected assess called twice (refine + converge), got {assess_count}. "
            f"All calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_one_refinement_feedback_called_once(self, tmp_path):
        """In one-refinement path: feedback is called exactly once."""
        backend = RefineThenConvergeBackend()
        engine = _make_factory_engine(backend, tmp_path)

        await engine.run()

        feedback_count = backend.calls.count("feedback")
        assert feedback_count == 1, (
            f"Expected feedback called once, got {feedback_count}. "
            f"All calls: {backend.calls}"
        )

    @pytest.mark.asyncio
    async def test_one_refinement_execution_order(self, tmp_path):
        """Verify full execution order: generate, assess, feedback, generate, assess."""
        backend = RefineThenConvergeBackend()
        engine = _make_factory_engine(backend, tmp_path)

        await engine.run()

        calls = backend.calls
        # Filter to just the codergen nodes we care about
        relevant = [c for c in calls if c in ("generate", "assess", "feedback")]
        expected = ["generate", "assess", "feedback", "generate", "assess"]
        assert relevant == expected, (
            f"Expected execution order {expected}, got {relevant}. "
            f"Full backend calls: {calls}"
        )

    @pytest.mark.asyncio
    async def test_context_variables_expand_in_generate_prompt(self, tmp_path):
        """$artifact_goal is expanded in generate's prompt when set in context.

        The parent sets context.artifact_goal='test goal for greet function'.
        The generate node's prompt template has $artifact_goal.
        After running, the captured prompt should contain the expanded value,
        not the raw $artifact_goal literal.
        """
        backend = CapturingBackend()
        engine = _make_factory_engine(
            backend,
            tmp_path,
            extra_ctx={"artifact_goal": "test goal for greet function"},
        )

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS

        gen_prompt = backend.prompts.get("generate", "")
        assert "test goal for greet function" in gen_prompt, (
            f"Expected artifact_goal expansion in generate prompt, got: {gen_prompt!r}"
        )
        assert "$artifact_goal" not in gen_prompt, (
            f"Expected $artifact_goal to be expanded (not raw), got: {gen_prompt!r}"
        )

    @pytest.mark.asyncio
    async def test_context_variables_expand_in_assess_prompt(self, tmp_path):
        """$validation_criteria and $artifact_goal expand in assess prompt."""
        backend = CapturingBackend()
        engine = _make_factory_engine(
            backend,
            tmp_path,
            extra_ctx={
                "artifact_goal": "create a greet utility",
                "validation_criteria": "function returns correct greeting string",
            },
        )

        await engine.run()

        assess_prompt = backend.prompts.get("assess", "")
        assert "create a greet utility" in assess_prompt, (
            f"Expected artifact_goal expansion in assess prompt, got: {assess_prompt!r}"
        )
        assert "function returns correct greeting string" in assess_prompt, (
            f"Expected validation_criteria expansion in assess prompt, "
            f"got: {assess_prompt!r}"
        )
        assert "$validation_criteria" not in assess_prompt, (
            f"Expected $validation_criteria to be expanded (not raw), "
            f"got: {assess_prompt!r}"
        )
