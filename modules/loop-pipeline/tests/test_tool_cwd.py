"""Regression tests: ToolHandler subprocess working-directory contract.

The ToolHandler must set the subprocess cwd to ``context.target_dir``
when that key is present.  This contract exists so that tool_command
scripts can operate on workspace files directly — without needing an
extra ``cd`` step inside the script.

Root cause of Issue 11 ("cd: can't cd to workspace"):
    smoke_test.dot tool nodes contained ``cd workspace`` at the top of
    each tool_command.  The intent was "change into the workspace
    subdirectory", but ``context.target_dir`` is already set to the
    workspace directory (e.g. ``/project/workspace``), so ``cd workspace``
    tried to descend into a non-existent ``/project/workspace/workspace/``
    subdirectory — causing an immediate exit-code-2 failure on the
    third line of every tool script.

    Fix: remove ``cd workspace`` from smoke_test.dot (already in the
    workspace directory); use absolute paths like ``cd /project/workspace``
    in dotpowers.dot (where SetupGitWorkspace always creates the workspace
    at that absolute location regardless of context.target_dir).

Design contract (tool.py, line 109):
    cwd: str | None = context.get("context.target_dir") or graph.source_dir or None

Tests
-----
- test_cwd_is_context_target_dir          — subprocess runs from context.target_dir
- test_files_written_without_cd           — mkdir + write lands in context.target_dir
- test_fallback_to_source_dir             — graph.source_dir used when target_dir absent
- test_cd_workspace_fails_when_in_workspace — documents the anti-pattern root cause
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus


def _make_graph(source_dir: str = "") -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
        source_dir=source_dir,
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


class TestToolCwd:
    """ToolHandler working-directory contract (Issue 11 regression guard)."""

    @pytest.mark.asyncio
    async def test_cwd_is_context_target_dir(self, tmp_path):
        """Subprocess working directory equals context.target_dir.

        When context.target_dir is set, ``create_subprocess_shell`` must
        receive that path as its ``cwd`` argument.  We verify indirectly
        by running ``pwd`` and asserting the output matches the directory.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctx = _make_context()
        ctx.set("context.target_dir", str(workspace))

        node = Node(
            id="pwd_node",
            attrs={"tool_command": "pwd"},
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        tool_output = ctx.get("tool.output", "")
        # pwd resolves symlinks; compare realpath
        import os

        assert (
            os.path.realpath(str(workspace)) in tool_output
            or str(workspace) in tool_output
        ), (
            f"Expected subprocess cwd to be {workspace!s}, but pwd output was {tool_output!r}"
        )

    @pytest.mark.asyncio
    async def test_files_written_without_cd(self, tmp_path):
        """Files created in tool_command land in context.target_dir without 'cd'.

        The correct pattern for pipeline tool scripts: just write files
        directly.  The subprocess already starts inside context.target_dir.
        No 'cd workspace' or similar navigation step is needed.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctx = _make_context()
        ctx.set("context.target_dir", str(workspace))

        node = Node(
            id="write_node",
            attrs={"tool_command": "mkdir -p src && printf hello > src/canary.txt"},
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        canary = workspace / "src" / "canary.txt"
        assert canary.exists(), (
            f"Expected {canary} to exist after tool_command wrote it, "
            "but it was not found.  Subprocess cwd may not be context.target_dir."
        )
        assert canary.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_fallback_to_source_dir(self, tmp_path):
        """graph.source_dir is used as cwd when context.target_dir is absent.

        When context.target_dir is not set, the handler falls back to
        graph.source_dir so that pipelines loaded from a DOT file can
        still run commands relative to the DOT file's location.
        """
        source_dir = tmp_path / "pipeline_src"
        source_dir.mkdir()

        ctx = _make_context()  # no context.target_dir

        node = Node(
            id="pwd_node",
            attrs={"tool_command": "printf canary > fallback_test.txt"},
        )
        handler = ToolHandler()
        outcome = await handler.execute(
            node, ctx, _make_graph(source_dir=str(source_dir)), str(tmp_path)
        )

        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status!r}: {outcome.failure_reason!r}"
        )
        fallback_file = source_dir / "fallback_test.txt"
        assert fallback_file.exists(), (
            f"Expected {fallback_file} to exist — fallback to graph.source_dir is broken."
        )

    @pytest.mark.asyncio
    async def test_cd_workspace_fails_when_cwd_is_workspace(self, tmp_path):
        """The 'cd workspace' anti-pattern fails when cwd is already the workspace.

        This test is the regression guard for Issue 11:
        - context.target_dir = /project/workspace  (tool handler sets cwd here)
        - tool_command starts with 'cd workspace'
        - shell tries to cd to /project/workspace/workspace  (doesn't exist)
        - shell exits with code 2

        Pipeline DOT files must NOT use 'cd workspace' as a navigation
        step.  They should either write directly (cwd is already the
        workspace) or use an absolute path like 'cd /project/workspace'.

        If this test starts PASSING (cd workspace succeeds), the cwd
        contract has changed and the DOT files may need re-evaluation.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctx = _make_context()
        ctx.set("context.target_dir", str(workspace))

        # Anti-pattern: 'cd workspace' when cwd IS already the workspace
        node = Node(
            id="antipattern_node",
            attrs={"tool_command": "cd workspace"},
        )
        handler = ToolHandler()
        outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))

        # This MUST fail — workspace/workspace/ does not exist
        assert outcome.status == StageStatus.FAIL, (
            "Expected 'cd workspace' to FAIL when cwd is already the workspace "
            "directory (no workspace/workspace/ subdirectory exists).  "
            "If this is now SUCCESS, the cwd contract has changed; review "
            "DOT pipeline tool_command scripts for 'cd workspace' anti-patterns."
        )
