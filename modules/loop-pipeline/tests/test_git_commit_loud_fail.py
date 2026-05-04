"""Regression tests: git commit without user identity MUST fail loudly.

Root cause of Issue 13 ("CommitChanges status=success but git panel shows no commits"):

    The smoke_test pipeline's CommitChanges tool_command lacked two things:
      1. ``git config user.email`` / ``git config user.name`` — relying on
         Docker-image-level git config meant the commit either silently
         succeeded (if the image had identity configured) or silently failed
         in environments where it was not set.
      2. ``git push origin HEAD`` — commits landed in the local workspace
         clone but were never pushed to the Gitea remote.  The git panel
         reads from Gitea; without the push it always showed empty.

Design contract (tool.py):
    A tool node whose shell command exits non-zero MUST produce
    StageStatus.FAIL.  Git identity errors exit 128.  ``set -e`` propagates
    all non-zero exits, so a missing identity will surface as a hard FAIL
    rather than a silent success.

Principle being guarded
-----------------------
Any pipeline tool_command that runs ``git commit`` MUST either:
  a) Set user.email and user.name inline (``git config user.email …``
     before the commit), OR
  b) Know that the runtime environment has already configured them.

When (a) is the chosen path — as in the canonical commit-and-push pattern —
the identity lines MUST appear before ``git commit``.  This test suite
documents the boundary and gives a live executable spec for it.

Tests
-----
- test_git_commit_without_identity_fails_loudly
    git commit in a fresh repo with no identity ⟹ StageStatus.FAIL
- test_git_commit_with_inline_identity_succeeds
    git commit with inline ``git config`` ⟹ StageStatus.SUCCESS
- test_git_commit_no_push_does_not_update_remote
    commit without push leaves remote at prior state (simulating the
    silent-success / empty-git-panel failure mode)
"""

from __future__ import annotations

import subprocess

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


def _make_context(target_dir: str = "") -> PipelineContext:
    ctx = PipelineContext()
    if target_dir:
        ctx.set("context.target_dir", target_dir)
    return ctx


def _init_repo(path: str) -> None:
    """Initialise a bare git repo at *path* (no user config, no commits)."""
    subprocess.run(["git", "init", path], check=True, capture_output=True)


def _init_repo_with_initial_commit(path: str) -> None:
    """Initialise a git repo with one initial commit (like Gitea auto_init=True)."""
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", path, "config", "user.email", "setup@test.local"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", path, "config", "user.name", "Setup"],
        check=True,
        capture_output=True,
    )
    # Create an initial commit so the repo is non-empty (mirrors Gitea auto_init)
    readme = f"{path}/README.md"
    with open(readme, "w") as f:
        f.write("# workspace\n")
    subprocess.run(
        ["git", "-C", path, "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", path, "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )
    # Remove the per-repo identity so subsequent operations start unconfigured
    subprocess.run(
        ["git", "-C", path, "config", "--unset", "user.email"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", path, "config", "--unset", "user.name"],
        check=True,
        capture_output=True,
    )


class TestGitCommitLoudFail:
    """git commit tool_command contract: identity required, push required."""

    @pytest.mark.asyncio
    async def test_git_commit_without_identity_fails_loudly(self, tmp_path):
        """git commit without user.email/user.name MUST produce StageStatus.FAIL.

        When a pipeline tool_command does ``git commit`` without first
        setting user.email and user.name, modern git exits 128.  With
        ``set -e`` in the script, that non-zero exit propagates and the
        ToolHandler MUST surface a FAIL — never a silent success.

        This is the root-cause guard for Issue 13: CommitChanges reported
        status=success despite commits never landing in the git panel.
        """
        repo_dir = tmp_path / "workspace"
        repo_dir.mkdir()
        _init_repo_with_initial_commit(str(repo_dir))

        # Stage a new file — same pattern as WriteTestFile → CommitChanges
        (repo_dir / "canary.txt").write_text("smoke test canary\n")

        # Tool command mirrors the pre-fix CommitChanges: no git config identity,
        # no push.  The GIT_CONFIG_NOSYSTEM + HOME override isolates from host
        # git config so the test is hermetic even on developer machines.
        tool_command = (
            "#!/bin/sh\n"
            "set -e\n"
            "export GIT_CONFIG_NOSYSTEM=1\n"
            "export GIT_CONFIG_GLOBAL=/dev/null\n"
            "git add -A\n"
            "git commit -m 'test commit without identity'\n"
            "printf 'committed'\n"
        )
        node = Node(id="commit_node", attrs={"tool_command": tool_command})
        ctx = _make_context(target_dir=str(repo_dir))

        outcome = await ToolHandler().execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.FAIL, (
            "Expected git commit without user identity to FAIL loudly. "
            f"Got {outcome.status!r} with reason: {outcome.failure_reason!r}. "
            "If this is now SUCCESS, git may have found identity from an "
            "unexpected source — check GIT_AUTHOR_EMAIL, ~/.gitconfig, or "
            "the system git config."
        )

    @pytest.mark.asyncio
    async def test_git_commit_with_inline_identity_succeeds(self, tmp_path):
        """git commit with inline git config identity MUST produce StageStatus.SUCCESS.

        This validates the fix pattern: set user.email and user.name
        immediately before the commit.  This is the canonical form that
        pipeline tool_commands MUST use to be self-contained.
        """
        repo_dir = tmp_path / "workspace"
        repo_dir.mkdir()
        _init_repo_with_initial_commit(str(repo_dir))

        (repo_dir / "canary.txt").write_text("smoke test canary\n")

        # The fixed pattern: inline identity before commit (no push needed in
        # this unit test — push is exercised separately)
        tool_command = (
            "#!/bin/sh\n"
            "set -e\n"
            "export GIT_CONFIG_NOSYSTEM=1\n"
            "export GIT_CONFIG_GLOBAL=/dev/null\n"
            "git config user.email pipeline-test@amplifier.dev\n"
            "git config user.name pipeline-test\n"
            "git add -A\n"
            "git commit -m 'test commit with inline identity'\n"
            "printf 'committed'\n"
        )
        node = Node(id="commit_node", attrs={"tool_command": tool_command})
        ctx = _make_context(target_dir=str(repo_dir))

        outcome = await ToolHandler().execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            "Expected git commit with inline identity to SUCCEED. "
            f"Got {outcome.status!r} with reason: {outcome.failure_reason!r}."
        )
        last_line = ctx.get("tool.last_line", "")
        assert last_line == "committed", (
            f"Expected tool.last_line='committed', got {last_line!r}. "
            "The commit completed but the final printf may have failed."
        )

    @pytest.mark.asyncio
    async def test_git_commit_no_push_does_not_update_remote(self, tmp_path):
        """Commit without push leaves the remote unaware of new commits.

        This test documents the silent-success / empty-git-panel failure
        mode from Issue 13:
          - Worker container has a local clone of the Gitea workspace repo.
          - ``git commit`` succeeds locally.
          - Without ``git push``, the Gitea remote sees no new commits.
          - The git panel (which reads from Gitea) therefore shows nothing.

        The test uses a local bare repo as a stand-in for the Gitea remote
        to verify that commits do NOT appear on the remote unless pushed.
        """
        bare_dir = tmp_path / "remote.git"
        clone_dir = tmp_path / "workspace"
        bare_dir.mkdir()
        clone_dir.mkdir()

        # Create a bare "remote" repo with an initial commit (mimics Gitea
        # auto_init=True which the platform uses for empty workspaces).
        subprocess.run(
            ["git", "init", "--bare", str(bare_dir)], check=True, capture_output=True
        )
        # Clone the bare remote into the workspace (mirrors platform setup)
        subprocess.run(
            ["git", "clone", str(bare_dir), str(clone_dir)],
            check=True,
            capture_output=True,
        )
        # Set up identity in the clone and create the initial commit
        subprocess.run(
            ["git", "-C", str(clone_dir), "config", "user.email", "setup@test.local"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "config", "user.name", "Setup"],
            check=True,
            capture_output=True,
        )
        (clone_dir / "README.md").write_text("# workspace\n")
        subprocess.run(
            ["git", "-C", str(clone_dir), "add", "README.md"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "commit", "-m", "initial commit"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "push", "origin", "HEAD"],
            check=True,
            capture_output=True,
        )

        # Record the current tip of the remote BEFORE the pipeline commit
        result = subprocess.run(
            ["git", "-C", str(bare_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        remote_sha_before = result.stdout.strip()

        # Stage a new file (mimics WriteTestFile)
        (clone_dir / "pipeline_file.txt").write_text("written by pipeline\n")

        # Tool command: commit locally but NO push (the pre-fix pattern)
        tool_command = (
            "#!/bin/sh\n"
            "set -e\n"
            "git config user.email pipeline-test@amplifier.dev\n"
            "git config user.name pipeline-test\n"
            "git add -A\n"
            "git commit -m 'pipeline commit without push'\n"
            "printf 'committed'\n"
        )
        node = Node(id="commit_node", attrs={"tool_command": tool_command})
        ctx = _make_context(target_dir=str(clone_dir))

        outcome = await ToolHandler().execute(node, ctx, _make_graph(), str(tmp_path))

        assert outcome.status == StageStatus.SUCCESS, (
            f"Commit should succeed locally; got {outcome.status!r}: "
            f"{outcome.failure_reason!r}"
        )

        # Verify remote is still at the pre-commit SHA (no push happened)
        result = subprocess.run(
            ["git", "-C", str(bare_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        remote_sha_after = result.stdout.strip()

        assert remote_sha_before == remote_sha_after, (
            "Expected remote to be UNCHANGED after commit-without-push, "
            f"but SHA moved from {remote_sha_before} to {remote_sha_after}. "
            "This is the Issue 13 failure mode: commit-without-push means "
            "the git panel (reading from remote) never sees the new commits."
        )
