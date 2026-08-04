"""Tests for P8: continue_on_fail node attribute.

When a node has continue_on_fail="true" in its attrs and the handler returns
a FAIL outcome, the engine overrides the outcome to SUCCESS for edge-selection
purposes while logging a WARNING with the failure reason.

Tests:
- test_continue_on_fail_overrides_fail_to_success: FAIL → SUCCESS when flag set
- test_continue_on_fail_pipeline_continues_to_next_node: pipeline runs past failing node
- test_without_continue_on_fail_preserves_fail: no flag → FAIL preserved
- test_continue_on_fail_logs_warning: WARNING emitted with node id and reason
- test_continue_on_fail_does_not_affect_success: SUCCESS unchanged with flag set
- test_continue_on_fail_on_tool_node: works on parallelogram (tool) nodes via DOT parsing
"""

from __future__ import annotations

import logging

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# FailingBackend — returns FAIL for nodes whose IDs start with 'fail_'
# ---------------------------------------------------------------------------


class FailingBackend:
    """Returns FAIL for node IDs starting with 'fail_', SUCCESS otherwise."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> Outcome:
        self.calls.append(node.id)
        if node.id.startswith("fail_"):
            return Outcome(status=StageStatus.FAIL, failure_reason="simulated failure")
        return Outcome(status=StageStatus.SUCCESS)


# ---------------------------------------------------------------------------
# FailingToolHandler — mock tool handler that always returns FAIL
# ---------------------------------------------------------------------------


class FailingToolHandler:
    """Mock tool handler that returns FAIL for parallelogram nodes."""

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine=None,
    ) -> Outcome:
        return Outcome(status=StageStatus.FAIL, failure_reason="tool simulated failure")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContinueOnFail:
    """Tests for the continue_on_fail node attribute (P8)."""

    @pytest.mark.asyncio
    async def test_continue_on_fail_overrides_fail_to_success(self, tmp_path):
        """FAIL outcome is overridden to SUCCESS when continue_on_fail='true'.

        A node with continue_on_fail='true' in attrs that returns FAIL should
        have its outcome recorded as SUCCESS in the engine's node_outcomes dict.
        """
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true"},
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        await engine.run()

        # The node outcome should be SUCCESS (overridden from FAIL)
        assert engine.node_outcomes["fail_node"].status == StageStatus.SUCCESS, (
            f"Expected fail_node outcome to be SUCCESS after continue_on_fail override, "
            f"got {engine.node_outcomes['fail_node'].status!r}"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_pipeline_continues_to_next_node(self, tmp_path):
        """Pipeline executes subsequent nodes after a continue_on_fail override.

        When a failing node has continue_on_fail='true', the pipeline should
        select the default (success) edge and continue executing the next node.
        """
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true"},
                ),
                "next_node": Node(id="next_node", shape="box", prompt="next work"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="next_node"),
                Edge(from_node="next_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        backend = FailingBackend()
        registry = HandlerRegistry(HandlerContext(backend=backend))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        outcome = await engine.run()

        # next_node should have been executed (pipeline continued past the failing node)
        assert "next_node" in engine.completed_nodes, (
            f"Expected next_node to be executed after continue_on_fail override, "
            f"completed_nodes={engine.completed_nodes!r}"
        )
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
            f"Expected overall pipeline SUCCESS, got {outcome.status!r}"
        )

    @pytest.mark.asyncio
    async def test_without_continue_on_fail_preserves_fail(self, tmp_path):
        """Without continue_on_fail, a FAIL outcome is preserved as FAIL.

        A node that returns FAIL and does NOT have continue_on_fail='true'
        should have its FAIL outcome preserved in node_outcomes.
        """
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    # No continue_on_fail attr
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="exit", condition="outcome=fail"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        await engine.run()

        # Without the flag, FAIL should be preserved
        assert engine.node_outcomes["fail_node"].status == StageStatus.FAIL, (
            f"Expected fail_node outcome to remain FAIL without continue_on_fail, "
            f"got {engine.node_outcomes['fail_node'].status!r}"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_logs_warning(self, tmp_path, caplog):
        """A WARNING log is emitted containing 'continue_on_fail' and the node id.

        When the continue_on_fail override is applied, the engine must log a
        WARNING-level message that contains the node id and mentions the failure.
        """
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true"},
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )

        with caplog.at_level(logging.WARNING):
            await engine.run()

        # There should be a WARNING log mentioning continue_on_fail and the node id
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "continue_on_fail" in r.message and "fail_node" in r.message
            for r in warning_records
        ), (
            f"Expected WARNING log with 'continue_on_fail' and 'fail_node', "
            f"got warnings: {[r.message for r in warning_records]!r}"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_does_not_affect_success(self, tmp_path):
        """continue_on_fail='true' does not change SUCCESS outcomes.

        A node with continue_on_fail='true' that returns SUCCESS should still
        have SUCCESS in node_outcomes (the flag must not interfere with SUCCESS).
        """

        class SuccessBackend:
            async def run(
                self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
            ) -> Outcome:
                return Outcome(status=StageStatus.SUCCESS, notes="all good")

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "ok_node": Node(
                    id="ok_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true"},
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="ok_node"),
                Edge(from_node="ok_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=SuccessBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        await engine.run()

        # SUCCESS should be preserved, not altered
        assert engine.node_outcomes["ok_node"].status == StageStatus.SUCCESS, (
            f"Expected ok_node outcome to remain SUCCESS with continue_on_fail, "
            f"got {engine.node_outcomes['ok_node'].status!r}"
        )
        assert engine.node_outcomes["ok_node"].notes == "all good", (
            f"Expected notes to be preserved as 'all good', "
            f"got {engine.node_outcomes['ok_node'].notes!r}"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_preserves_attempt_count(self, tmp_path):
        """attempt_count from the retry ladder must survive continue_on_fail.

        (S2 lossy-reconstruction regression guard, docs/designs/RECURRING-BUG-CLASSES.md.)

        A node with max_retries='2' (max_attempts=3) whose handler returns
        RETRY on the first two attempts and FAIL on the third has
        attempt_count=3 set by execute_with_retry's FAIL path. When
        continue_on_fail='true' then overrides that FAIL outcome to SUCCESS,
        the emitted pipeline:node_complete event must still report
        attempt=3, not the attempt_count=None fallback of 1.
        """

        class _RecordingHooks:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            async def emit(self, event_name: str, data: dict) -> None:
                self.events.append((event_name, data))

        class RetryThenFailHandler:
            """Returns RETRY on the first two calls, FAIL on the third."""

            def __init__(self) -> None:
                self.calls = 0

            async def execute(self, node, context, graph, logs_root, *, engine=None):
                self.calls += 1
                if self.calls < 3:
                    return Outcome(status=StageStatus.RETRY)
                return Outcome(status=StageStatus.FAIL, failure_reason="boom")

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true", "max_retries": "2"},
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext())
        handler = RetryThenFailHandler()
        registry.register("codergen", handler)
        hooks = _RecordingHooks()
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()

        assert handler.calls == 3, f"Expected 3 handler calls, got {handler.calls}"
        assert engine.node_outcomes["fail_node"].status == StageStatus.SUCCESS
        assert engine.node_outcomes["fail_node"].attempt_count == 3, (
            f"Expected attempt_count=3 to survive continue_on_fail override, "
            f"got {engine.node_outcomes['fail_node'].attempt_count!r} "
            f"(S2 lossy reconstruction regression)"
        )

        complete_events = [
            data for name, data in hooks.events if name == "pipeline:node_complete"
        ]
        fail_node_events = [e for e in complete_events if e["node_id"] == "fail_node"]
        assert len(fail_node_events) == 1, (
            f"Expected exactly one pipeline:node_complete for fail_node, "
            f"got {len(fail_node_events)}"
        )
        assert fail_node_events[0]["attempt"] == 3, (
            f"Expected pipeline:node_complete attempt=3 after continue_on_fail "
            f"override, got {fail_node_events[0]['attempt']!r} "
            f"(S2 lossy reconstruction regression)"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_preserves_failed_step(self, tmp_path):
        """failed_step (structured failure detail) must survive continue_on_fail.

        (S2 lossy-reconstruction regression guard, docs/designs/RECURRING-BUG-CLASSES.md.)

        continue_on_fail suppresses a FAIL outcome for ROUTING purposes only
        -- it must not erase the diagnostic record of what actually failed.
        A tool-style handler that fails with a structured failed_step
        payload must still expose that payload after the override, both on
        the stored Outcome and on the emitted pipeline:node_complete event.
        """

        class _RecordingHooks:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            async def emit(self, event_name: str, data: dict) -> None:
                self.events.append((event_name, data))

        expected_failed_step = {
            "command": "false",
            "exit_code": 1,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "simulated tool failure",
        }

        class FailingStepHandler:
            """Always returns FAIL with a structured failed_step payload."""

            async def execute(self, node, context, graph, logs_root, *, engine=None):
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason="tool exited non-zero",
                    failed_step=dict(expected_failed_step),
                )

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "fail_node": Node(
                    id="fail_node",
                    shape="box",
                    prompt="work",
                    attrs={"continue_on_fail": "true"},
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="fail_node"),
                Edge(from_node="fail_node", to_node="exit"),
            ],
        )
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext())
        registry.register("codergen", FailingStepHandler())
        hooks = _RecordingHooks()
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
            hooks=hooks,
        )
        await engine.run()

        assert engine.node_outcomes["fail_node"].status == StageStatus.SUCCESS
        assert engine.node_outcomes["fail_node"].failed_step == expected_failed_step, (
            f"Expected failed_step to survive continue_on_fail override, "
            f"got {engine.node_outcomes['fail_node'].failed_step!r} "
            f"(S2 lossy reconstruction regression)"
        )

        complete_events = [
            data for name, data in hooks.events if name == "pipeline:node_complete"
        ]
        fail_node_events = [e for e in complete_events if e["node_id"] == "fail_node"]
        assert len(fail_node_events) == 1
        assert fail_node_events[0]["failed_step"] == expected_failed_step, (
            f"Expected pipeline:node_complete failed_step to survive "
            f"continue_on_fail override, got "
            f"{fail_node_events[0]['failed_step']!r} "
            f"(S2 lossy reconstruction regression)"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_on_tool_node(self, tmp_path):
        """continue_on_fail works on tool nodes (parallelogram shape) via DOT parsing.

        A parallelogram-shaped node with continue_on_fail='true' whose tool
        handler returns FAIL should have its outcome overridden to SUCCESS.
        """
        dot_source = """\
digraph test {
    start [shape=Mdiamond]
    fail_tool [shape=parallelogram, continue_on_fail="true", command="false"]
    exit [shape=Msquare]
    start -> fail_tool -> exit
}
"""
        graph = parse_dot(dot_source)

        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
        # Replace the built-in tool handler with our failing mock
        registry.register("tool", FailingToolHandler())
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        outcome = await engine.run()

        # Despite the tool node failing, continue_on_fail should override to SUCCESS
        assert engine.node_outcomes["fail_tool"].status == StageStatus.SUCCESS, (
            f"Expected fail_tool outcome to be SUCCESS after continue_on_fail override, "
            f"got {engine.node_outcomes['fail_tool'].status!r}"
        )
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
            f"Expected overall pipeline SUCCESS, got {outcome.status!r}"
        )

    @pytest.mark.asyncio
    async def test_continue_on_fail_unquoted_true_through_dot_parser(self, tmp_path):
        """continue_on_fail=true (bare, unquoted) overrides FAIL via real parse_dot().

        Regression test for issue #389: the DOT parser coerces an unquoted
        `true` token to the Python bool ``True`` (not the string ``"true"``).
        This test drives that exact bare/unquoted form through the REAL
        `parse_dot()` parser and the REAL `PipelineEngine`, proving the
        override actually fires -- a hand-built `Node(attrs={"continue_on_fail":
        "true"})` (quoted, as used in the tests above) would never have caught
        this bug because it bypasses the parser's boolean coercion entirely.
        """
        dot_source = """\
digraph test {
    start [shape=Mdiamond]
    fail_node [shape=box, prompt="work", continue_on_fail=true]
    exit [shape=Msquare]
    start -> fail_node -> exit
}
"""
        graph = parse_dot(dot_source)

        # Confirm the parser actually coerced the attribute to a real bool --
        # otherwise this test would not be exercising the reported bug at all.
        assert graph.nodes["fail_node"].attrs.get("continue_on_fail") is True, (
            f"Expected parse_dot() to coerce unquoted continue_on_fail=true to "
            f"Python bool True, got "
            f"{graph.nodes['fail_node'].attrs.get('continue_on_fail')!r}"
        )

        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=FailingBackend()))
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(tmp_path),
        )
        outcome = await engine.run()

        assert engine.node_outcomes["fail_node"].status == StageStatus.SUCCESS, (
            f"Expected fail_node outcome to be SUCCESS after unquoted "
            f"continue_on_fail=true override, got "
            f"{engine.node_outcomes['fail_node'].status!r}"
        )
        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
            f"Expected overall pipeline SUCCESS, got {outcome.status!r}"
        )
