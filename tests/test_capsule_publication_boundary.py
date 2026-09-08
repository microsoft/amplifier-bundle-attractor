"""Executable publication-boundary checks for the specify workflows.

The tests execute each workflow's extracted ``Open capsule PR`` and issue-comment
run blocks in a disposable Git repository with a local bare remote.  Only the
``gh`` network boundary is stubbed; pair-integrity and shipped-gate checks are
the repository's real scripts.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = Path(
    os.environ.get(
        "CAPSULE_PUBLICATION_WORKFLOW_ROOT", REPO_ROOT / ".github" / "workflows"
    )
)
WORKFLOWS = ("capsule-specify.yml", "feature-specify.yml")
PUBLICATION_TOKEN_EXPRESSION = "${{ secrets.CAPSULE_PR_TOKEN || github.token }}"
CAPSULE_ID = "DEFINITION"
RUN_ID = "20260908T000000Z"
PR_URL = "https://example.invalid/owner/repository/pull/1"


def _steps(workflow_name: str) -> list[dict[str, object]]:
    workflow = yaml.safe_load(
        (WORKFLOW_ROOT / workflow_name).read_text(encoding="utf-8")
    )
    return workflow["jobs"]["specify"]["steps"]


def _step(workflow_name: str, name: str) -> dict[str, object]:
    for step in _steps(workflow_name):
        if step.get("name") == name:
            return step
    raise AssertionError(f"{workflow_name} has no {name!r} step")


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _render_run(run: str, base_sha: str, *, kind: str = "capsule") -> str:
    known_context = {
        "github.repository": "owner/repository",
        "github.run_id": "12345",
        "github.server_url": "https://example.invalid",
        "steps.base.outputs.run_id": RUN_ID,
        "steps.base.outputs.sha": base_sha,
        "steps.classify.outputs.id": CAPSULE_ID,
        "steps.classify.outputs.kind": kind,
        "steps.classify.outputs.hint": "fixture hint",
        "steps.classify.outputs.duration_hint": "fixture duration hint",
    }

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        assert expression in known_context, (
            f"unexpected GitHub expression: {expression}"
        )
        return known_context[expression]

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", replace, run)


def _publication_token(step: dict[str, object]) -> str:
    environment = step["env"]
    assert isinstance(environment, dict)
    # This is the workflow's real, declarative selection expression. The test
    # deliberately renders this known GitHub expression, rather than replacing
    # it with a test-only selector.
    assert environment["GH_TOKEN"] == PUBLICATION_TOKEN_EXPRESSION
    configured_secret = "configured-capsule-token"
    github_fallback = "fallback-github-token"
    return configured_secret or github_fallback


def _write_gh_stub(bin_dir: Path) -> Path:
    stub = bin_dir / "gh"
    stub.write_text(
        """#!/bin/sh
set -eu
token="$(printenv GH_TOKEN)"
[ "$token" = "$EXPECTED_GH_TOKEN" ] || exit 97
printf '%s\\n' "$1:$2" >> "$GH_LOG"
case "$1:$2" in
  api:graphql)
    if [ "${GH_MODE:-success}" = "preflight_401" ]; then exit 1; fi
    exit 0
    ;;
  pr:create)
    if [ "${GH_MODE:-success}" = "pr_create_fails" ]; then exit 1; fi
    printf '%s\\n' "$PR_STUB_URL"
    exit 0
    ;;
  issue:comment)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--body-file" ]; then
        cp "$2" "$GH_COMMENT"
        break
      fi
      shift
    done
    exit 0
    ;;
  *)
    exit 98
    ;;
esac
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _fixture(tmp_path: Path, workflow_name: str) -> dict[str, Path | str]:
    workspace = tmp_path / "workspace"
    remote = tmp_path / "remote.git"
    runner_temp = tmp_path / "runner-temp"
    bin_dir = tmp_path / "bin"
    workspace.mkdir(parents=True)
    runner_temp.mkdir()
    bin_dir.mkdir()

    pipeline_dir = workspace / ".github" / "capsule-pipeline"
    pipeline_dir.mkdir(parents=True)
    for filename in ("capsule_pair_fence.sh", "verify_shipped_gate.sh"):
        copied = pipeline_dir / filename
        shutil.copy2(REPO_ROOT / ".github" / "capsule-pipeline" / filename, copied)
        copied.chmod(0o755)
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")

    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "fixture@example.invalid")
    _git(workspace, "config", "user.name", "Publication fixture")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "base")
    base_sha = _git(workspace, "rev-parse", "HEAD")
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(workspace, "remote", "add", "origin", str(remote))
    _git(workspace, "push", "-q", "-u", "origin", "main")

    out = runner_temp / "capsule-run" / "out"
    out.mkdir(parents=True)
    is_feature = workflow_name == "feature-specify.yml"
    red_signal = "AC-1: UNMET" if is_feature else "EXPECTED RED"
    (out / f"{CAPSULE_ID}.md").write_text(
        f"---\nred_signal: {red_signal}\n---\nfixture capsule\n", encoding="utf-8"
    )
    if is_feature:
        gate = "mkdir -p .ai\nprintf 'AC-1: UNMET\\n' > .ai/census\necho feature red\nexit 1\n"
        (out / f"{CAPSULE_ID}.census-red").write_text("AC-1: UNMET\n", encoding="utf-8")
    else:
        gate = "echo EXPECTED RED\nexit 1\n"
    gate_path = out / f"{CAPSULE_ID}.verify.sh"
    gate_path.write_text(f"#!/usr/bin/env bash\n{gate}", encoding="utf-8")
    gate_path.chmod(0o755)
    manifest = runner_temp / "capsule-pair.sha256"
    recorded = subprocess.run(
        [
            str(pipeline_dir / "capsule_pair_fence.sh"),
            "record",
            str(out),
            str(manifest),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert recorded.returncode == 0, recorded.stderr

    _write_gh_stub(bin_dir)
    branch_prefix = "feature-capsule" if is_feature else "capsule"
    return {
        "base_sha": base_sha,
        "branch": f"{branch_prefix}/issue-42-{RUN_ID}",
        "comment": tmp_path / "comment.md",
        "gh_log": tmp_path / "gh.log",
        "home": tmp_path / "home",
        "runner_temp": runner_temp,
        "workspace": workspace,
        "bin_dir": bin_dir,
    }


def _environment(
    fixture: dict[str, Path | str], step: dict[str, object], mode: str
) -> dict[str, str]:
    home = fixture["home"]
    assert isinstance(home, Path)
    home.mkdir(exist_ok=True)
    workspace = fixture["workspace"]
    runner_temp = fixture["runner_temp"]
    bin_dir = fixture["bin_dir"]
    comment = fixture["comment"]
    gh_log = fixture["gh_log"]
    assert all(
        isinstance(value, Path)
        for value in (workspace, runner_temp, bin_dir, comment, gh_log)
    )
    return {
        **os.environ,
        "EXPECTED_GH_TOKEN": _publication_token(step),
        "GH_TOKEN": _publication_token(step),
        "GH_COMMENT": str(comment),
        "GH_LOG": str(gh_log),
        "GH_MODE": mode,
        "GITHUB_OUTPUT": str(runner_temp / "github-output"),
        "GITHUB_WORKSPACE": str(workspace),
        "HOME": str(home),
        "ISSUE_NUMBER": "42",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PR_STUB_URL": PR_URL,
        "RUNNER_TEMP": str(runner_temp),
    }


def _run_open_pr(
    fixture: dict[str, Path | str], workflow_name: str, mode: str = "success"
) -> subprocess.CompletedProcess[str]:
    step = _step(workflow_name, "Open capsule PR")
    run = step["run"]
    base_sha = fixture["base_sha"]
    workspace = fixture["workspace"]
    assert (
        isinstance(run, str)
        and isinstance(base_sha, str)
        and isinstance(workspace, Path)
    )
    return subprocess.run(
        ["bash", "-c", _render_run(run, base_sha)],
        cwd=workspace,
        check=False,
        text=True,
        capture_output=True,
        env=_environment(fixture, step, mode),
    )


def _run_comment(
    fixture: dict[str, Path | str],
    workflow_name: str,
    *,
    kind: str,
    pr_step: str,
    pr_url: str,
) -> subprocess.CompletedProcess[str]:
    step = _step(workflow_name, "Comment on the issue with the outcome")
    run = step["run"]
    base_sha = fixture["base_sha"]
    workspace = fixture["workspace"]
    assert (
        isinstance(run, str)
        and isinstance(base_sha, str)
        and isinstance(workspace, Path)
    )
    environment = _environment(
        fixture, _step(workflow_name, "Open capsule PR"), "success"
    )
    environment.update(
        {
            "CAPSULE_SECRET_GATE": "true",
            "GATE_EXEC": "true",
            "KIND": kind,
            "PR_STEP": pr_step,
            "PR_URL": pr_url,
        }
    )
    return subprocess.run(
        ["bash", "-c", _render_run(run, base_sha, kind=kind)],
        cwd=workspace,
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )


def _gh_calls(fixture: dict[str, Path | str]) -> list[str]:
    log = fixture["gh_log"]
    assert isinstance(log, Path)
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_publication_credential_preflight_stops_before_branch_or_push(
    tmp_path: Path, workflow_name: str
) -> None:
    fixture = _fixture(tmp_path, workflow_name)

    result = _run_open_pr(fixture, workflow_name, "preflight_401")

    workspace = fixture["workspace"]
    branch = fixture["branch"]
    assert isinstance(workspace, Path) and isinstance(branch, str)
    assert result.returncode != 0
    assert "credential check failed" in result.stderr.lower()
    assert _gh_calls(fixture) == ["api:graphql"]
    assert _git(workspace, "branch", "--list", branch) == ""
    remote_branch = _git(
        workspace, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
    )
    assert remote_branch == ""


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_successful_publication_uses_selected_credential_and_opens_pr(
    tmp_path: Path, workflow_name: str
) -> None:
    fixture = _fixture(tmp_path, workflow_name)

    result = _run_open_pr(fixture, workflow_name)

    workspace = fixture["workspace"]
    runner_temp = fixture["runner_temp"]
    branch = fixture["branch"]
    assert (
        isinstance(workspace, Path)
        and isinstance(runner_temp, Path)
        and isinstance(branch, str)
    )
    assert result.returncode == 0, result.stderr
    assert _gh_calls(fixture) == ["api:graphql", "pr:create"]
    assert _git(workspace, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    assert f"url={PR_URL}" in (runner_temp / "github-output").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_pr_create_failure_after_push_reports_publication_blocked(
    tmp_path: Path, workflow_name: str
) -> None:
    fixture = _fixture(tmp_path, workflow_name)

    publication = _run_open_pr(fixture, workflow_name, "pr_create_fails")
    comment = _run_comment(
        fixture, workflow_name, kind="capsule", pr_step="failure", pr_url=""
    )

    workspace = fixture["workspace"]
    branch = fixture["branch"]
    comment_path = fixture["comment"]
    assert (
        isinstance(workspace, Path)
        and isinstance(branch, str)
        and isinstance(comment_path, Path)
    )
    assert publication.returncode != 0
    assert _gh_calls(fixture)[:2] == ["api:graphql", "pr:create"]
    assert _git(workspace, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    assert comment.returncode == 0, comment.stderr
    body = comment_path.read_text(encoding="utf-8")
    assert (
        "work capsule produced; publication did not complete; no capsule PR URL available"
        in body
    )
    assert "nothing was pushed" not in body
    assert "pre-publication check failed" not in body
    assert "capsule artifacts free of secret-shaped material" in body
    assert "shipped gate executes RED" in body
    assert "Workflow run:" in body


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_capsule_comment_opens_only_for_success_with_a_url(
    tmp_path: Path, workflow_name: str
) -> None:
    opened_fixture = _fixture(tmp_path / "opened", workflow_name)
    opened = _run_comment(
        opened_fixture, workflow_name, kind="capsule", pr_step="success", pr_url=PR_URL
    )
    opened_path = opened_fixture["comment"]
    assert isinstance(opened_path, Path)
    assert opened.returncode == 0, opened.stderr
    opened_body = opened_path.read_text(encoding="utf-8")
    assert "work capsule opened" in opened_body
    assert PR_URL in opened_body

    missing_url_fixture = _fixture(tmp_path / "missing-url", workflow_name)
    blocked = _run_comment(
        missing_url_fixture, workflow_name, kind="capsule", pr_step="success", pr_url=""
    )
    blocked_path = missing_url_fixture["comment"]
    assert isinstance(blocked_path, Path)
    assert blocked.returncode == 0, blocked.stderr
    assert (
        "work capsule produced; publication did not complete; no capsule PR URL available"
        in blocked_path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_non_capsule_comment_path_remains_unchanged(
    tmp_path: Path, workflow_name: str
) -> None:
    fixture = _fixture(tmp_path, workflow_name)

    result = _run_comment(
        fixture, workflow_name, kind="green_on_main", pr_step="failure", pr_url=""
    )

    comment_path = fixture["comment"]
    assert isinstance(comment_path, Path)
    assert result.returncode == 0, result.stderr
    body = comment_path.read_text(encoding="utf-8")
    assert "executable check" in body
    assert "work capsule produced; publication did not complete" not in body
