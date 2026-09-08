"""Regression coverage for the capsule author-reset scratch-state contract.

The reset command is extracted from the shipped graph and executed in a disposable
Git repository.  It is intentionally never copied into this test: the graph is
the executable source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOT = REPO_ROOT / ".github" / "capsule-pipeline" / "capsule.dot"


def _dot_path() -> Path:
    """Allow the red-control invocation to supply pre-change graph bytes."""
    return Path(os.environ.get("CAPSULE_AUTHOR_RESET_DOT", DEFAULT_DOT))


def _author_reset_command() -> str:
    dot = _dot_path().read_text(encoding="utf-8")
    match = re.search(
        r'author_reset\s*\[[^\]]*?tool_command="((?:[^"\\]|\\.)*)"', dot, re.DOTALL
    )
    assert match, f"could not extract author_reset tool_command from {_dot_path()}"
    return re.sub(r"\\(.)", lambda escaped: escaped.group(1), match.group(1))


def _fixture_env(cwd: Path) -> dict[str, str]:
    """Confine git and the extracted shell command to the disposable fixture."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    home = cwd / ".ai" / ".test-home"
    home.mkdir(parents=True, exist_ok=True)
    xdg_config_home = home / ".config"
    xdg_config_home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _run(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", command],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env=_fixture_env(cwd),
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env=_fixture_env(cwd),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, str, bytes]:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Capsule reset test")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    feedback = b"feedback\x00bytes\r\n\xffmust survive exactly\n"
    (repo / ".ai" / "feedback").mkdir(parents=True)
    (repo / ".ai" / "base-sha").write_text(f"{base}\n", encoding="utf-8")
    (repo / ".ai" / "convergence.jsonl").write_text(
        '{"iteration": 1, "gate": "round", "shape": "ok"}\n', encoding="utf-8"
    )
    (repo / ".ai" / "feedback" / "critique-001.md").write_bytes(feedback)

    # Simulate author self-test residue in the tracked tree; reset must recover it.
    (repo / "tracked.txt").write_text("author self-test edit\n", encoding="utf-8")
    (repo / "untracked.tmp").write_text("remove me\n", encoding="utf-8")
    return repo, base, feedback


def _assert_pinned_clean_tree(repo: Path, base: str) -> None:
    status = _git(repo, "status", "--porcelain")
    non_ai_entries = [
        line
        for line in status.splitlines()
        if not re.match(r"^.{3}\.ai(?:/|$)", line)
    ]
    assert not non_ai_entries, status
    assert _git(repo, "diff", "--quiet", "--") == ""
    assert _git(repo, "rev-parse", "HEAD") == base


def test_author_reset_removes_stale_hypothesis_patch_and_preserves_feedback(
    tmp_path: Path,
) -> None:
    repo, base, feedback = _fixture(tmp_path)
    patch = repo / ".ai" / "hypothesis.patch"
    patch.write_text("diff --git a/tracked.txt b/tracked.txt\nstale patch\n", encoding="utf-8")

    result = _run(_author_reset_command(), repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("reset_proven")
    assert not patch.exists()
    assert not patch.is_symlink()
    _assert_pinned_clean_tree(repo, base)
    assert (repo / ".ai" / "feedback" / "critique-001.md").read_bytes() == feedback


def test_author_reset_removes_dangling_hypothesis_patch_symlink(tmp_path: Path) -> None:
    repo, base, feedback = _fixture(tmp_path)
    patch = repo / ".ai" / "hypothesis.patch"
    patch.symlink_to("missing-hypothesis.patch")
    assert patch.is_symlink() and not patch.exists()

    result = _run(_author_reset_command(), repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("reset_proven")
    assert not patch.exists()
    assert not patch.is_symlink()
    _assert_pinned_clean_tree(repo, base)
    assert (repo / ".ai" / "feedback" / "critique-001.md").read_bytes() == feedback


def test_author_reset_reports_unproven_when_patch_cleanup_cannot_remove_directory(
    tmp_path: Path,
) -> None:
    repo, base, feedback = _fixture(tmp_path)
    patch = repo / ".ai" / "hypothesis.patch"
    patch.mkdir()
    (patch / "cannot-be-unlinked-by-rm-f").write_text("stale\n", encoding="utf-8")

    result = _run(_author_reset_command(), repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("reset_unproven")
    assert patch.is_dir()
    assert "AUTHOR-RESET: RESET NOT PROVEN" in (repo / ".ai" / "gate.log").read_text(
        encoding="utf-8"
    )
    _assert_pinned_clean_tree(repo, base)
    assert (repo / ".ai" / "feedback" / "critique-001.md").read_bytes() == feedback
