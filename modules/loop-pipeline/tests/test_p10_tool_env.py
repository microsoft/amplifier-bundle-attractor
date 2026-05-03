"""Tests for P10: tool_env node attribute on tool (parallelogram) nodes.

When a parallelogram node has tool_env="var_name1,var_name2", the handler
reads each comma-separated variable name from pipeline context, converts to
uppercase (snake_case -> UPPER_CASE), and passes as environment variables to
the subprocess. Variables not found in context are silently skipped. Leading
and trailing whitespace around names is trimmed.

Also covers tool.last_line routing: the last non-empty stdout line is stored in
context as tool.last_line, enabling condition="context.tool.last_line=<label>"
edge routing without touching outcome.preferred_label.

Tests:
- test_injects_single_var_as_env_var: single var from context injected as env var
- test_injects_multiple_vars: multiple comma-separated vars all injected
- test_uppercase_conversion: snake_case context key becomes UPPER_CASE env var
- test_missing_context_var_skipped: missing context var causes no crash (silently skipped)
- test_whitespace_trimmed_from_var_names: leading/trailing whitespace around names trimmed
- test_without_tool_env_does_not_inject: without tool_env, context vars NOT injected
- test_tool_env_combined_with_parse_json: tool_env and parse_json can be used together
- test_last_line_set_in_context: last stdout line → context["tool.last_line"]
- test_last_line_ignores_trailing_newlines: blank trailing lines are skipped
- test_last_line_absent_when_stdout_empty: empty stdout → tool.last_line="" (Fix 4: always emitted)
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
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


class TestToolEnv:
    """Tests for the tool_env node attribute on tool nodes (P10)."""

    @pytest.mark.asyncio
    async def test_injects_single_var_as_env_var(self, tmp_path):
        """tool_env injects a single context variable as an environment variable.

        When tool_env="state_file" and "state_file" is in context, the
        subprocess should receive STATE_FILE as an environment variable.
        """
        ctx = _make_context()
        ctx.set("state_file", "/tmp/state.json")

        node = Node(
            id="tool_node",
            attrs={
                "tool_command": "printenv STATE_FILE",
                "tool_env": "state_file",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert "/tmp/state.json" in tool_output, (
            f"Expected STATE_FILE='/tmp/state.json' in subprocess env, "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_injects_multiple_vars(self, tmp_path):
        """tool_env injects all comma-separated variable names as env vars.

        When tool_env="input_path,output_path", both INPUT_PATH and OUTPUT_PATH
        should be available as environment variables in the subprocess.
        """
        ctx = _make_context()
        ctx.set("input_path", "/data/in")
        ctx.set("output_path", "/data/out")

        node = Node(
            id="tool_node",
            attrs={
                "tool_command": "echo $INPUT_PATH $OUTPUT_PATH",
                "tool_env": "input_path,output_path",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert "/data/in" in tool_output, (
            f"Expected INPUT_PATH='/data/in' in subprocess env, "
            f"got tool.output={tool_output!r}"
        )
        assert "/data/out" in tool_output, (
            f"Expected OUTPUT_PATH='/data/out' in subprocess env, "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_uppercase_conversion(self, tmp_path):
        """Context key snake_case is converted to UPPER_CASE env var name.

        When tool_env="build_command", the context key "build_command" is
        looked up and the env var is named BUILD_COMMAND (uppercase).
        """
        ctx = _make_context()
        ctx.set("build_command", "make all")

        node = Node(
            id="tool_node",
            attrs={
                "tool_command": "printenv BUILD_COMMAND",
                "tool_env": "build_command",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert "make all" in tool_output, (
            f"Expected BUILD_COMMAND='make all' in subprocess env (uppercase), "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_missing_context_var_skipped(self, tmp_path):
        """Context variable missing from context is silently skipped (no crash).

        When tool_env="nonexistent_var" but "nonexistent_var" is not in context,
        the subprocess should still run successfully and the missing variable
        should simply not be present as an env var.
        """
        ctx = _make_context()
        # Do NOT set "nonexistent_var" in context

        node = Node(
            id="tool_node",
            attrs={
                # Print env var if set, else echo "absent" — confirms var was not injected
                "tool_command": "printenv NONEXISTENT_VAR || echo absent",
                "tool_env": "nonexistent_var",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        # Should succeed without error — missing var is silently skipped
        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS when context var is missing (silently skipped), "
            f"got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert "absent" in tool_output, (
            f"Expected NONEXISTENT_VAR to be absent from subprocess env "
            f"(printenv should fail, echo absent should run), "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_whitespace_trimmed_from_var_names(self, tmp_path):
        """Leading/trailing whitespace around variable names is trimmed.

        When tool_env=" state_file , build_command " (with spaces), the handler
        should trim whitespace and correctly inject STATE_FILE and BUILD_COMMAND.
        """
        ctx = _make_context()
        ctx.set("state_file", "/trimmed/path")
        ctx.set("build_command", "make test")

        node = Node(
            id="tool_node",
            attrs={
                # Note deliberate spaces around names
                "tool_command": "echo $STATE_FILE $BUILD_COMMAND",
                "tool_env": " state_file , build_command ",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS with whitespace-padded var names, "
            f"got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        assert "/trimmed/path" in tool_output, (
            f"Expected STATE_FILE='/trimmed/path' injected after whitespace trim, "
            f"got tool.output={tool_output!r}"
        )
        assert "make test" in tool_output, (
            f"Expected BUILD_COMMAND='make test' injected after whitespace trim, "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_without_tool_env_does_not_inject(self, tmp_path):
        """Without tool_env attribute, context vars are NOT injected as env vars.

        When a node has no tool_env, context variables should not appear as
        environment variables in the subprocess.
        """
        ctx = _make_context()
        # Use a unique name very unlikely to already exist in the host environment
        ctx.set("test_unique_context_var_p10", "should_not_appear_in_env")

        node = Node(
            id="tool_node",
            attrs={
                # No tool_env attribute
                # Print env var with fallback so the command still succeeds
                "tool_command": ("echo ${TEST_UNIQUE_CONTEXT_VAR_P10:-not_injected}"),
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        # Verify the env var was NOT injected (fallback "not_injected" should appear)
        assert "should_not_appear_in_env" not in tool_output, (
            f"Expected context var NOT injected without tool_env, "
            f"but found value in tool.output={tool_output!r}"
        )
        assert "not_injected" in tool_output, (
            f"Expected fallback 'not_injected' when env var absent, "
            f"got tool.output={tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_tool_env_combined_with_parse_json(self, tmp_path):
        """tool_env and parse_json can be used together on the same node.

        When both tool_env and parse_json are set, the handler should:
        1. Inject context vars as env vars into the subprocess
        2. Parse the JSON stdout and inject each key into context
        Both features must work correctly when combined.
        """
        ctx = _make_context()
        ctx.set("run_mode", "production")

        # Command uses the injected env var and outputs JSON
        node = Node(
            id="tool_node",
            attrs={
                "tool_command": (
                    'python3 -c "'
                    "import os, json; "
                    "print(json.dumps({'mode': os.environ.get('RUN_MODE', 'unknown'), 'status': 'ok'}))"
                    '"'
                ),
                "tool_env": "run_mode",
                "parse_json": "true",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS with tool_env + parse_json, "
            f"got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        # parse_json should have injected 'mode' and 'status' keys
        mode_value = ctx.get("mode")
        assert mode_value == "production", (
            f"Expected context['mode'] == 'production' (from RUN_MODE env var), "
            f"got {mode_value!r}"
        )
        status_value = ctx.get("status")
        assert status_value == "ok", (
            f"Expected context['status'] == 'ok', got {status_value!r}"
        )


class TestToolLastLine:
    """Tests for ToolHandler storing last stdout line as context["tool.last_line"].

    Tool commands that echo a routing label as their final line get that label
    stored in context["tool.last_line"], enabling condition="context.tool.last_line=<label>"
    routing on outgoing edges without touching outcome.preferred_label.

    This design preserves the existing condition="outcome=success" routing for tool
    nodes whose output is not a routing label (e.g. tool nodes that echo diagnostics).
    """

    @pytest.mark.asyncio
    async def test_last_line_set_in_context(self, tmp_path):
        """Last non-empty stdout line is stored in context as tool.last_line.

        Tool commands that echo a routing label (e.g. "tests_pass") as their
        last line should have that label stored in context["tool.last_line"]
        for condition="context.tool.last_line=tests_pass" edge routing.
        The outcome itself should have preferred_label=None so the standard
        condition="outcome=success" routing is not disrupted.
        """
        ctx = _make_context()
        node = Node(
            id="run_tests",
            attrs={"tool_command": "echo 'running tests'; echo tests_pass"},
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        # tool.last_line must be set to the routing label
        last_line = ctx.get("tool.last_line")
        assert last_line == "tests_pass", (
            f"Expected context['tool.last_line']='tests_pass' (last stdout line), "
            f"got {last_line!r}"
        )
        # preferred_label must remain None so outcome=success routing is unaffected
        assert outcome.preferred_label is None, (
            f"Expected preferred_label=None (not set by tool handler), "
            f"got {outcome.preferred_label!r}"
        )

    @pytest.mark.asyncio
    async def test_last_line_ignores_trailing_newlines(self, tmp_path):
        """Trailing blank lines in stdout do not become the tool.last_line value.

        When stdout ends with trailing newlines (as echo commands typically produce),
        the last *non-empty* stripped line is extracted as tool.last_line.
        """
        ctx = _make_context()
        node = Node(
            id="check",
            attrs={
                # printf to have fine-grained control over newlines
                "tool_command": "printf 'tests_fail\\n\\n'",
            },
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS
        last_line = ctx.get("tool.last_line")
        assert last_line == "tests_fail", (
            f"Expected context['tool.last_line']='tests_fail' (last non-empty line), "
            f"got {last_line!r}"
        )

    @pytest.mark.asyncio
    async def test_last_line_absent_when_stdout_empty(self, tmp_path):
        """Fix 4 (R12 R12.5): When stdout is empty, tool.last_line is set to "".

        Previously this test asserted tool.last_line was absent (None) for empty
        stdout. The R12 R12.5 fix changes tool.py to ALWAYS emit tool.last_line —
        using "" when stdout is empty — so the declared inferred contract in
        HANDLER_INFERRED_OUTPUTS["tool"] always holds, preventing false-positive
        PIPELINE_NODE_CONTRACT_VIOLATION events on empty-stdout tools.

        See: zen-architect review concern #1 (significant), R12 R12.5 fix list.
        """
        ctx = _make_context()
        node = Node(
            id="silent_tool",
            attrs={"tool_command": "true"},  # exits 0 with no output
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS
        # tool.last_line MUST be set (to "") even when stdout is empty —
        # the inferred contract requires it, and "" is semantically correct
        # (no routing label was produced).
        last_line = ctx.get("tool.last_line")
        assert last_line == "", (
            f"Expected context['tool.last_line'] to be '' for empty stdout, "
            f"got {last_line!r}"
        )
