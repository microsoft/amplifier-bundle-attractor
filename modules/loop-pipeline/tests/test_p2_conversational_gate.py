"""Tests for P2: Conversational Gate Pattern.

Validates both structure and execution of the reusable conversational-gate.dot
pattern and its demo parent pipeline without requiring real API keys.

Test coverage:
- Structural parse tests for conversational-gate.dot (5 nodes, correct shapes, edges)
- Structural parse tests for demo-conversational-gates.dot (3 folder nodes)
- Execution test: single-pass path (ask -> eval(scored) -> check -> done)
- Execution test: loop path (eval(need_more) first, then eval(scored) on second pass)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.interviewer import Answer, Option, QueueInterviewer
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Repo root: modules/loop-pipeline/tests/ -> ../../..
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_PATTERNS_DIR = _REPO_ROOT / "examples" / "patterns"
_GATE_DOT = _PATTERNS_DIR / "conversational-gate.dot"
_DEMO_DOT = _PATTERNS_DIR / "demo-conversational-gates.dot"


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


class ScoredBackend:
    """Always returns preferred_label='scored' — simulates a passing evaluation."""

    async def run(self, node: Node, prompt: str, context: PipelineContext) -> Outcome:
        return Outcome(status=StageStatus.SUCCESS, preferred_label="scored")


class SequentialOutcomeBackend:
    """Returns Outcome objects from a list in sequence, cycling on the last one."""

    def __init__(self, outcomes: list[Outcome]) -> None:
        self._outcomes = outcomes
        self._idx = 0
        self.call_count = 0

    async def run(self, node: Node, prompt: str, context: PipelineContext) -> Outcome:
        result = self._outcomes[min(self._idx, len(self._outcomes) - 1)]
        self._idx += 1
        self.call_count += 1
        return result


# ---------------------------------------------------------------------------
# Helper to build a context with gate variables set
# ---------------------------------------------------------------------------


def _make_gate_context() -> PipelineContext:
    """Return a PipelineContext with gate_topic/criteria/output_path pre-set."""
    ctx = PipelineContext()
    ctx.set("gate_topic", "Test gate topic: rate this system's quality 0-100")
    ctx.set(
        "gate_criteria", "Look for: test coverage, clear architecture, CI pipeline."
    )
    ctx.set("gate_output_path", "/tmp/test_gate_output.md")
    return ctx


# ---------------------------------------------------------------------------
# Structural tests: Parse conversational-gate.dot
# ---------------------------------------------------------------------------


class TestConversationalGateParse:
    """P2 structural tests: conversational-gate.dot parses to expected graph."""

    def test_file_exists(self):
        """Pattern file exists at examples/patterns/conversational-gate.dot."""
        assert _GATE_DOT.exists(), f"Pattern file not found: {_GATE_DOT}"

    def test_parses_with_five_nodes(self):
        """conversational-gate.dot parses into exactly 5 nodes."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        assert len(g.nodes) == 5, (
            f"Expected 5 nodes, got {len(g.nodes)}: {list(g.nodes.keys())}"
        )

    def test_node_shapes(self):
        """Pattern has start(Mdiamond), ask(hexagon), eval(box), check(parallelogram), done(Msquare)."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)

        start_nodes = [n for n in g.nodes.values() if n.shape == "Mdiamond"]
        ask_nodes = [n for n in g.nodes.values() if n.shape == "hexagon"]
        check_nodes = [n for n in g.nodes.values() if n.shape == "parallelogram"]
        done_nodes = [n for n in g.nodes.values() if n.shape == "Msquare"]

        assert len(start_nodes) == 1, (
            f"Expected 1 Mdiamond start node, got {len(start_nodes)}"
        )
        assert len(ask_nodes) == 1, (
            f"Expected 1 hexagon (ask) node, got {len(ask_nodes)}"
        )
        assert len(check_nodes) == 1, (
            f"Expected 1 parallelogram (check) node, got {len(check_nodes)}"
        )
        assert len(done_nodes) == 1, (
            f"Expected 1 Msquare done node, got {len(done_nodes)}"
        )
        # eval_nodes: all non-special shapes (box or empty default)
        codergen_like = [
            n
            for n in g.nodes.values()
            if n.shape not in ("Mdiamond", "hexagon", "parallelogram", "Msquare")
        ]
        assert len(codergen_like) == 1, (
            f"Expected 1 codergen (eval) node, got {len(codergen_like)}: "
            f"{[(n.id, n.shape) for n in codergen_like]}"
        )

    def test_node_ids_include_ask_eval_check(self):
        """The pattern includes nodes named 'ask', 'eval', and 'check'."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        assert "ask" in g.nodes, f"Node 'ask' missing. Nodes: {list(g.nodes.keys())}"
        assert "eval" in g.nodes, f"Node 'eval' missing. Nodes: {list(g.nodes.keys())}"
        assert "check" in g.nodes, (
            f"Node 'check' missing. Nodes: {list(g.nodes.keys())}"
        )

    def test_has_five_edges(self):
        """Pattern has exactly 5 edges."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        assert len(g.edges) == 5, (
            f"Expected 5 edges, got {len(g.edges)}: "
            f"{[(e.from_node, e.to_node, e.label) for e in g.edges]}"
        )

    def test_ask_to_eval_edge_labeled_continue(self):
        """The ask->eval edge is labeled 'continue'."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        ask_to_eval = [
            e for e in g.edges if e.from_node == "ask" and e.to_node == "eval"
        ]
        assert len(ask_to_eval) == 1, "Expected exactly one ask->eval edge"
        assert ask_to_eval[0].label == "continue", (
            f"Expected ask->eval label='continue', got {ask_to_eval[0].label!r}"
        )

    def test_check_has_condition_edges(self):
        """check node has two conditional edges: 'scored' -> done, 'need_more' -> ask."""
        source = _GATE_DOT.read_text()
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
        assert "scored" in labels, f"Expected 'scored' edge from check, got {labels}"
        assert "need_more" in labels, (
            f"Expected 'need_more' edge from check, got {labels}"
        )

    def test_loop_back_edge_goes_to_ask(self):
        """The 'need_more' conditional edge from check routes back to ask."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        need_more_edges = [
            e for e in g.edges if e.from_node == "check" and e.label == "need_more"
        ]
        assert len(need_more_edges) == 1, (
            "Expected exactly one 'need_more' edge from check"
        )
        assert need_more_edges[0].to_node == "ask", (
            f"Expected need_more edge to go to 'ask', got {need_more_edges[0].to_node!r}"
        )

    def test_eval_prompt_references_gate_variables(self):
        """eval node's prompt contains $gate_topic and $gate_criteria template variables."""
        source = _GATE_DOT.read_text()
        g = parse_dot(source)
        eval_node = g.nodes.get("eval")
        assert eval_node is not None, "Node 'eval' not found"
        prompt = eval_node.prompt or ""
        assert "$gate_topic" in prompt, (
            f"Expected '$gate_topic' in eval prompt, got: {prompt!r}"
        )
        assert "$gate_criteria" in prompt, (
            f"Expected '$gate_criteria' in eval prompt, got: {prompt!r}"
        )


# ---------------------------------------------------------------------------
# Structural tests: Parse demo-conversational-gates.dot
# ---------------------------------------------------------------------------


class TestDemoConversationalGatesParse:
    """P2 structural tests: demo-conversational-gates.dot parses to expected graph."""

    def test_demo_file_exists(self):
        """Demo pipeline file exists at examples/patterns/demo-conversational-gates.dot."""
        assert _DEMO_DOT.exists(), f"Demo file not found: {_DEMO_DOT}"

    def test_demo_parses_ok(self):
        """demo-conversational-gates.dot parses without errors."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        assert len(g.nodes) > 0

    def test_demo_has_three_folder_nodes(self):
        """Demo pipeline has exactly 3 folder nodes pointing to the gate pattern."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        assert len(folder_nodes) == 3, (
            f"Expected 3 folder nodes (gate1, gate2, gate3), "
            f"got {len(folder_nodes)}: {[n.id for n in folder_nodes]}"
        )

    def test_folder_nodes_reference_conversational_gate(self):
        """All folder nodes in the demo reference conversational-gate.dot."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        for node in folder_nodes:
            dot_file = node.attrs.get("dot_file", "")
            assert "conversational-gate.dot" in dot_file, (
                f"Node {node.id!r} dot_file should reference conversational-gate.dot, "
                f"got {dot_file!r}"
            )

    def test_folder_nodes_have_context_attrs(self):
        """Each folder node has context.gate_topic, context.gate_criteria, context.gate_output_path."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        folder_nodes = [n for n in g.nodes.values() if n.shape == "folder"]
        for node in folder_nodes:
            assert "context.gate_topic" in node.attrs, (
                f"Node {node.id!r} missing context.gate_topic attr"
            )
            assert "context.gate_criteria" in node.attrs, (
                f"Node {node.id!r} missing context.gate_criteria attr"
            )
            assert "context.gate_output_path" in node.attrs, (
                f"Node {node.id!r} missing context.gate_output_path attr"
            )

    def test_demo_has_sequential_flow(self):
        """start -> gate1 -> gate2 -> gate3 -> done linear chain exists."""
        source = _DEMO_DOT.read_text()
        g = parse_dot(source)
        # Check the chain: start->gate1->gate2->gate3->done
        edge_map: dict[str, str] = {e.from_node: e.to_node for e in g.edges}
        start_node = next(
            (n.id for n in g.nodes.values() if n.shape == "Mdiamond"), None
        )
        assert start_node is not None, "No start (Mdiamond) node found"

        current = start_node
        visited = [current]
        for _ in range(4):  # 4 hops: start->gate1->gate2->gate3->done
            nxt = edge_map.get(current)
            if nxt is None:
                break
            visited.append(nxt)
            current = nxt

        assert len(visited) == 5, (
            f"Expected 5-node chain start->gate1->gate2->gate3->done, "
            f"got chain: {visited}"
        )


# ---------------------------------------------------------------------------
# Execution tests: run the conversational gate pipeline with mocks
# ---------------------------------------------------------------------------


class TestConversationalGateExecution:
    """P2 execution tests: pipeline runs end-to-end using mock backend and QueueInterviewer."""

    @pytest.mark.asyncio
    async def test_single_pass_scored_immediately(self, tmp_path):
        """Single-pass: ask -> eval(scored) -> check -> done.

        Uses QueueInterviewer with one pre-scripted answer and ScoredBackend.
        The pipeline should complete in one loop iteration.
        """
        gate_source = _GATE_DOT.read_text()
        graph = parse_dot(gate_source)

        # Queue one answer for the ask hexagon (the "continue" option)
        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )
        backend = ScoredBackend()

        context = _make_gate_context()
        registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path / "logs"),
        )

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status}. "
            f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
        )
        # Verify the execution path included ask, eval, check, done
        assert "ask" in engine.completed_nodes, "Expected 'ask' in completed nodes"
        assert "eval" in engine.completed_nodes, "Expected 'eval' in completed nodes"
        assert "check" in engine.completed_nodes, "Expected 'check' in completed nodes"

    @pytest.mark.asyncio
    async def test_single_pass_execution_path(self, tmp_path):
        """Single-pass path is exactly: start, ask, eval, check (then done/exit)."""
        gate_source = _GATE_DOT.read_text()
        graph = parse_dot(gate_source)

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )
        backend = ScoredBackend()
        context = _make_gate_context()
        registry = HandlerRegistry(HandlerContext(backend=backend, interviewer=interviewer))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        # ask should appear exactly once (no loop)
        ask_count = engine.completed_nodes.count("ask")
        eval_count = engine.completed_nodes.count("eval")
        assert ask_count == 1, f"Expected ask visited once, got {ask_count}"
        assert eval_count == 1, f"Expected eval visited once, got {eval_count}"

    @pytest.mark.asyncio
    async def test_loop_behavior_need_more_then_scored(self, tmp_path):
        """Loop path: eval returns need_more first, then scored on second pass.

        Execution path: ask -> eval(need_more) -> check -> ask -> eval(scored) -> check -> done
        The pipeline should loop once and then converge.
        """
        gate_source = _GATE_DOT.read_text()
        graph = parse_dot(gate_source)

        # Queue two answers: one for each time ask is reached
        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )

        # Backend: need_more on first eval call, scored on second
        sequential_backend = SequentialOutcomeBackend(
            [
                Outcome(status=StageStatus.SUCCESS, preferred_label="need_more"),
                Outcome(status=StageStatus.SUCCESS, preferred_label="scored"),
            ]
        )

        context = _make_gate_context()
        registry = HandlerRegistry(HandlerContext(backend=sequential_backend, interviewer=interviewer))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path / "logs"),
        )

        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS after looping, got {outcome.status}. "
            f"Notes: {outcome.notes!r}, failure_reason: {outcome.failure_reason!r}"
        )

    @pytest.mark.asyncio
    async def test_loop_behavior_ask_called_twice(self, tmp_path):
        """In loop path: ask is visited twice, eval is called twice."""
        gate_source = _GATE_DOT.read_text()
        graph = parse_dot(gate_source)

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )
        sequential_backend = SequentialOutcomeBackend(
            [
                Outcome(status=StageStatus.SUCCESS, preferred_label="need_more"),
                Outcome(status=StageStatus.SUCCESS, preferred_label="scored"),
            ]
        )

        context = _make_gate_context()
        registry = HandlerRegistry(HandlerContext(backend=sequential_backend, interviewer=interviewer))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        # ask should be visited twice (initial + after loop-back)
        ask_count = engine.completed_nodes.count("ask")
        eval_count = engine.completed_nodes.count("eval")
        assert ask_count == 2, (
            f"Expected ask visited twice (once initial + once after loop), got {ask_count}. "
            f"Full execution path: {engine.completed_nodes}"
        )
        assert eval_count == 2, (
            f"Expected eval called twice (once need_more + once scored), got {eval_count}. "
            f"Full execution path: {engine.completed_nodes}"
        )

    @pytest.mark.asyncio
    async def test_context_variables_injected_into_eval_prompt(self, tmp_path):
        """gate_topic and gate_criteria from context are expanded in eval's prompt."""
        gate_source = _GATE_DOT.read_text()
        graph = parse_dot(gate_source)

        captured_prompts: dict[str, str] = {}

        class CapturingBackend:
            async def run(
                self, node: Node, prompt: str, context: PipelineContext
            ) -> Outcome:
                captured_prompts[node.id] = prompt
                return Outcome(status=StageStatus.SUCCESS, preferred_label="scored")

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                ),
            ]
        )
        context = _make_gate_context()
        registry = HandlerRegistry(HandlerContext(backend=CapturingBackend(), interviewer=interviewer))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path / "logs"),
        )

        await engine.run()

        eval_prompt = captured_prompts.get("eval", "")
        assert "Test gate topic" in eval_prompt, (
            f"Expected gate_topic expansion in eval prompt, got: {eval_prompt!r}"
        )
        assert "test coverage" in eval_prompt, (
            f"Expected gate_criteria expansion in eval prompt, got: {eval_prompt!r}"
        )
        assert "$gate_topic" not in eval_prompt, (
            f"Expected $gate_topic to be expanded (not raw), got: {eval_prompt!r}"
        )
        assert "$gate_criteria" not in eval_prompt, (
            f"Expected $gate_criteria to be expanded (not raw), got: {eval_prompt!r}"
        )
