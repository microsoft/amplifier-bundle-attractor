"""Tests for P9: parse_json node attribute on tool (parallelogram) nodes.

When a parallelogram node has parse_json="true", after successful execution the
handler json.loads() the stdout. If the result is a dict, each key-value pair is
injected into the pipeline context via context.set(key, value) and included in
the outcome's context_updates. If JSON parsing fails, a WARNING is logged and
the node still returns SUCCESS.

Tests:
- test_parse_json_injects_dict_keys_into_context: JSON dict keys injected into context
- test_without_parse_json_does_not_parse: JSON stdout NOT parsed when flag absent
- test_parse_json_malformed_json_logs_warning_and_succeeds: bad JSON → WARNING + SUCCESS
- test_parse_json_array_not_injected: JSON array not injected into context
- test_parse_json_primitive_not_injected: JSON primitive not injected into context
- test_parse_json_keys_in_context_updates: injected keys appear in outcome.context_updates
- test_parse_json_tool_output_still_set: tool.output set in context even with parse_json
- test_parse_json_not_invoked_on_failed_command: FAIL command → parse_json NOT invoked
"""

from __future__ import annotations

import json
import logging

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus


def _make_graph() -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


class TestParseJson:
    """Tests for the parse_json node attribute on tool nodes (P9)."""

    @pytest.mark.asyncio
    async def test_parse_json_injects_dict_keys_into_context(self, tmp_path):
        """parse_json='true' causes JSON dict keys to be injected into context.

        When a tool node outputs valid JSON with a dict, each key should be
        set in the pipeline context via context.set(key, value).
        """
        payload = json.dumps({"status": "ok", "count": 42, "message": "hello"})
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": f"echo '{payload}'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}"
        )
        assert ctx.get("status") == "ok", (
            f"Expected context['status'] == 'ok', got {ctx.get('status')!r}"
        )
        assert ctx.get("count") == 42, (
            f"Expected context['count'] == 42, got {ctx.get('count')!r}"
        )
        assert ctx.get("message") == "hello", (
            f"Expected context['message'] == 'hello', got {ctx.get('message')!r}"
        )

    @pytest.mark.asyncio
    async def test_without_parse_json_does_not_parse(self, tmp_path):
        """Without parse_json flag, JSON stdout is NOT parsed into context.

        Even if the tool outputs valid JSON, the keys should NOT appear
        in context when parse_json is not set.
        """
        payload = json.dumps({"status": "ok", "value": 99})
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": f"echo '{payload}'",
                # No parse_json attribute
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}"
        )
        # Keys from JSON should NOT be in context
        assert ctx.get("status") is None, (
            f"Expected 'status' NOT in context without parse_json, "
            f"got {ctx.get('status')!r}"
        )
        assert ctx.get("value") is None, (
            f"Expected 'value' NOT in context without parse_json, "
            f"got {ctx.get('value')!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_json_malformed_json_logs_warning_and_succeeds(
        self, tmp_path, caplog
    ):
        """Malformed JSON output logs a WARNING but the node still returns SUCCESS.

        When parse_json='true' but stdout is not valid JSON, the handler must:
        - log a WARNING-level message
        - still return SUCCESS (not FAIL)
        """
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": "echo 'not valid json {'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()

        with caplog.at_level(logging.WARNING):
            outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS even with malformed JSON, got {outcome.status!r}"
        )
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0, (
            "Expected at least one WARNING log for malformed JSON"
        )

    @pytest.mark.asyncio
    async def test_parse_json_array_not_injected(self, tmp_path):
        """JSON array output does not inject keys into context.

        If the parsed JSON result is a list, no keys should be injected
        into context (only dict results are injected).
        """
        array_payload = json.dumps(["item1", "item2", "item3"])
        node = Node(
            id="tool_array",
            attrs={
                "tool_command": f"echo '{array_payload}'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS for array JSON, got {outcome.status!r}"
        )
        # Should not have injected any indexed keys
        assert ctx.get("0") is None, (
            f"Expected no key '0' injected for array JSON, got {ctx.get('0')!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_json_primitive_not_injected(self, tmp_path):
        """JSON primitive output does not inject keys into context.

        If the parsed JSON result is a number, string, or bool, no keys
        should be injected into context (only dict results are injected).
        """
        node = Node(
            id="tool_prim",
            attrs={
                "tool_command": "echo '42'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS for primitive JSON, got {outcome.status!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_json_keys_in_context_updates(self, tmp_path):
        """Each injected key appears in outcome.context_updates.

        When parse_json='true' and stdout is a JSON dict, each key-value pair
        must appear in the returned outcome's context_updates dict.
        """
        payload = json.dumps({"result": "done", "score": 95})
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": f"echo '{payload}'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}"
        )
        assert outcome.context_updates is not None, (
            "Expected context_updates to be set in outcome"
        )
        assert "result" in outcome.context_updates, (
            f"Expected 'result' in context_updates, got {outcome.context_updates!r}"
        )
        assert outcome.context_updates["result"] == "done", (
            f"Expected context_updates['result'] == 'done', "
            f"got {outcome.context_updates['result']!r}"
        )
        assert "score" in outcome.context_updates, (
            f"Expected 'score' in context_updates, got {outcome.context_updates!r}"
        )
        assert outcome.context_updates["score"] == 95, (
            f"Expected context_updates['score'] == 95, "
            f"got {outcome.context_updates['score']!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_json_tool_output_still_set(self, tmp_path):
        """tool.output is still set in context even when parse_json is used.

        The parse_json feature must not replace the existing behavior of
        setting tool.output in context with the raw stdout.
        """
        payload = json.dumps({"key": "value"})
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": f"echo '{payload}'",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert tool_output, (
            "Expected tool.output to be set in context even with parse_json"
        )
        assert "tool.output" in (outcome.context_updates or {}), (
            "Expected tool.output in outcome.context_updates even with parse_json"
        )

    @pytest.mark.asyncio
    async def test_parse_json_not_invoked_on_failed_command(self, tmp_path):
        """parse_json is NOT invoked when the command exits non-zero (FAIL).

        A failing command returns FAIL and should not attempt JSON parsing.
        The node should return FAIL with no JSON keys injected.
        """
        # Use a command that outputs JSON but exits non-zero
        payload = json.dumps({"should_not": "be_injected"})
        # Command that prints JSON but exits with error
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": f"echo '{payload}'; exit 1",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.FAIL, (
            f"Expected FAIL for non-zero exit, got {outcome.status!r}"
        )
        # JSON keys must NOT be injected when command fails
        assert ctx.get("should_not") is None, (
            f"Expected 'should_not' NOT in context after failed command, "
            f"got {ctx.get('should_not')!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_json_unquoted_true_through_dot_parser(self, tmp_path):
        """parse_json=true (bare, unquoted) is honored via real parse_dot().

        Regression test for issue #389: the DOT parser coerces an unquoted
        `true` token to the Python bool ``True`` (not the string ``"true"``).
        This test drives that exact bare/unquoted form through the REAL
        `parse_dot()` parser and the REAL `ToolHandler`, proving JSON keys
        actually get injected into context -- a hand-built
        `Node(attrs={"parse_json": "true"})` (quoted, as used in the tests
        above) would never have caught this bug because it bypasses the
        parser's boolean coercion entirely.
        """
        payload = json.dumps({"status": "ok", "count": 7})
        # Escape embedded double-quotes for the DOT quoted-attribute-value
        # syntax (dot_parser.py's `\"` -> `"` unescape); the resulting
        # tool_command attribute value contains the real, unescaped JSON.
        escaped_payload = payload.replace('"', '\\"')
        dot_source = f"""\
digraph test {{
    start [shape=Mdiamond]
    tool_node [shape=parallelogram, tool_command="echo '{escaped_payload}'", parse_json=true]
    exit [shape=Msquare]
    start -> tool_node -> exit
}}
"""
        graph = parse_dot(dot_source)

        # Confirm the parser actually coerced the attribute to a real bool --
        # otherwise this test would not be exercising the reported bug at all.
        assert graph.nodes["tool_node"].attrs.get("parse_json") is True, (
            f"Expected parse_dot() to coerce unquoted parse_json=true to "
            f"Python bool True, got "
            f"{graph.nodes['tool_node'].attrs.get('parse_json')!r}"
        )

        handler = ToolHandler()
        ctx = _make_context()
        outcome = await handler.execute(
            graph.nodes["tool_node"], ctx, graph, str(tmp_path)
        )

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}"
        )
        assert ctx.get("status") == "ok", (
            f"Expected context['status'] == 'ok' after unquoted parse_json=true, "
            f"got {ctx.get('status')!r}"
        )
        assert ctx.get("count") == 7, (
            f"Expected context['count'] == 7 after unquoted parse_json=true, "
            f"got {ctx.get('count')!r}"
        )
