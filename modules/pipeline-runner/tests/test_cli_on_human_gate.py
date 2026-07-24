"""Tests for ``--on-human-gate console`` -- argparse wiring, interviewer
selection, and the fail-loud stdin guard.

Spec basis (specs/canonical/attractor-spec-canonical.md, identical to the
fresh upstream clone at attractor/attractor-spec.md):
    Section 6.1  -- Interviewer interface.
    Section 6.4  -- "ConsoleInterviewer (CLI): Reads from standard input.
                     Displays formatted prompts with option keys."
    Conformance checklist (~line 1865): "ConsoleInterviewer prompts in
                     terminal and reads user input."
    Section 9.5  -- human gates must be operable via CLI (web controls are
                     additive on top of that baseline).

These tests assert only the CLI-level wiring seam -- that 'console' is an
accepted choice, that it resolves to a real ConsoleInterviewer via the same
seam auto-approve already uses (``runner.run_pipeline(..., interviewer=...)``),
and that an unusable stdin fails loud at startup rather than hanging at the
first gate. ``runner.run_pipeline`` itself is monkeypatched in these tests
(the engine run is exercised separately, with NO interviewer mock, in
test_console_gate_integration.py).
"""

from __future__ import annotations

import io
import sys

import pytest

from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner import runner as runner_mod
from amplifier_module_pipeline_runner.runner import PipelineResult


# --- argparse: choice accepted + help text ---------------------------------


def test_console_choice_accepted_by_argparse():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "dummy.dot", "--on-human-gate", "console"])
    assert args.on_human_gate == "console"


def test_unknown_on_human_gate_choice_still_rejected():
    """'console' is additive -- an unrelated bogus value must still fail loud."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "dummy.dot", "--on-human-gate", "bogus"])


def test_help_text_documents_console_mode(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--help"])
    out = capsys.readouterr().out
    assert "console" in out
    assert "ConsoleInterviewer" in out
    assert "stdin" in out


# --- _stdin_is_usable() unit tests ------------------------------------------


def test_stdin_usable_none_is_unusable(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    assert cli._stdin_is_usable() is False


def test_stdin_usable_closed_file_is_unusable(monkeypatch, tmp_path):
    f = open(tmp_path / "x.txt", "w", encoding="utf-8")
    f.close()
    monkeypatch.setattr(sys, "stdin", f)
    assert cli._stdin_is_usable() is False


def test_stdin_usable_piped_stringio_is_usable(monkeypatch):
    """Piped (non-tty) stdin MUST be allowed -- scripted answers are legitimate."""
    piped = io.StringIO("A\n")
    monkeypatch.setattr(sys, "stdin", piped)
    assert piped.isatty() is False  # sanity: this really is a non-tty stream
    assert cli._stdin_is_usable() is True


# --- cmd_run: interviewer selection per --on-human-gate choice --------------


def _make_dot_file(tmp_path) -> str:
    dot_file = tmp_path / "pipeline.dot"
    dot_file.write_text(
        "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
        encoding="utf-8",
    )
    return str(dot_file)


def _run_cmd_run(monkeypatch, tmp_path, on_human_gate: str, *, patch_run_pipeline=True):
    """Parse real argv through build_parser() and invoke cmd_run, capturing
    the ``interviewer`` kwarg handed to ``runner.run_pipeline`` (never actually
    running the engine -- that's covered by test_console_gate_integration.py).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["interviewer"] = kwargs.get("interviewer")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    if patch_run_pipeline:
        monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", dot_path, "--on-human-gate", on_human_gate, "--cwd", str(tmp_path)]
    )
    rc = cli.cmd_run(args)
    return rc, captured


def test_fail_mode_passes_no_interviewer(monkeypatch, tmp_path):
    rc, captured = _run_cmd_run(monkeypatch, tmp_path, "fail")
    assert rc == 0
    assert captured["interviewer"] is None


def test_auto_approve_mode_passes_auto_approve_interviewer(monkeypatch, tmp_path):
    from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

    rc, captured = _run_cmd_run(monkeypatch, tmp_path, "auto-approve")
    assert rc == 0
    assert isinstance(captured["interviewer"], AutoApproveInterviewer)


def test_console_mode_with_usable_stdin_passes_console_interviewer(
    monkeypatch, tmp_path
):
    from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

    monkeypatch.setattr(sys, "stdin", io.StringIO("A\n"))
    rc, captured = _run_cmd_run(monkeypatch, tmp_path, "console")
    assert rc == 0
    assert isinstance(captured["interviewer"], ConsoleInterviewer)


# --- fail-loud stdin guard ---------------------------------------------------


def test_console_mode_fails_loud_at_startup_when_stdin_unusable(
    monkeypatch, tmp_path, capsys
):
    """Closed/absent stdin must error BEFORE any pipeline run is attempted --
    never hang waiting on the first gate.
    """
    monkeypatch.setattr(sys, "stdin", None)

    async def must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "run_pipeline must NOT be called when stdin is unusable -- "
            "the guard must fire at startup, before any gate is reached"
        )

    monkeypatch.setattr(runner_mod, "run_pipeline", must_not_be_called)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(
        ["run", dot_path, "--on-human-gate", "console", "--cwd", str(tmp_path)]
    )

    rc = cli.cmd_run(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "stdin" in err
