"""Scenario 7 — the degradation ladder, and the two fates it distinguishes.

A rung being UNAVAILABLE and a rung coming back RED are different things, and
conflating them is how an artifact ends up implying a pass nobody earned:

* unavailable  -> labelled honestly, the demo still publishes at the level
                  that actually ran;
* red          -> the demo is NOT published, at all, ever.

Every rung is exercised for real: a fake `dot-runner` shim on PATH for rung 1,
an emptied PATH for rung 3-only, a deliberately broken checker for rung 4, and
a fixture with its evidence gate deleted for the red path. Gates proven red,
per repo doctrine — a gate that has only ever been seen passing is not a gate.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from attractor_scout import demo
from attractor_scout import demo_templates as T

from fixtures import demo_fixture as F

SKILL_DIR = Path(__file__).resolve().parent.parent


def _shim(tmp_path: Path, payload: str, *, exit_code: int = 0) -> Path:
    """A fake `dot-runner` on PATH that emits a canned lint verdict."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "dot-runner"
    shim.write_text(
        "#!/bin/sh\n"
        + "".join(f'printf "%s\\n" {json.dumps(line)}\n' for line in payload.splitlines())
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _draft(tmp_path: Path, **kwargs) -> Path:
    return F.write_draft(tmp_path / "wk", **kwargs)


RELPATH = "attractor-scout-demos/synthetic-demo.dot"


# ------------------------------------------------------------------ rung 1
def test_rung1_cli_on_path_gives_lint_plus_doctrine_and_relays_verbatim(tmp_path, monkeypatch):
    verdict_text = "dot-runner lint: pipeline.dot: OK (no findings)"
    bindir = _shim(tmp_path, verdict_text)
    monkeypatch.setenv("PATH", str(bindir))
    wk = _draft(tmp_path)

    result = demo.run_ladder(
        dot_path=wk / "pipeline.dot",
        companion_path=wk / "pipeline.md",
        relpath=RELPATH,
    )
    assert result.level == T.LEVEL_LINT_DOCTRINE
    assert result.lint_verdict == verdict_text, "the lint verdict is relayed VERBATIM, never paraphrased"
    assert result.lint_not_run_reason is None
    assert result.doctrine_verdict == "doctrine_ok"
    assert not result.red


def test_rung1_warnings_pass_but_are_quoted(tmp_path, monkeypatch):
    warning = "WARNING: [TOPO-006] [give_up] (give_up -> done) routes a failure outcome into the exit node."
    monkeypatch.setenv("PATH", str(_shim(tmp_path, warning)))
    wk = _draft(tmp_path)

    result = demo.run_ladder(dot_path=wk / "pipeline.dot", companion_path=wk / "pipeline.md", relpath=RELPATH)
    assert result.level == T.LEVEL_LINT_DOCTRINE
    assert not result.red, "a WARNING is not a red verdict"
    assert "TOPO-006" in (result.lint_verdict or ""), "the warning must still be quoted"


def test_rung1_lint_errors_are_a_red_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(_shim(tmp_path, "ERROR: [shape_resolvable] [check] unknown shape", exit_code=1)))
    wk = _draft(tmp_path)

    result = demo.run_ladder(dot_path=wk / "pipeline.dot", companion_path=wk / "pipeline.md", relpath=RELPATH)
    assert result.red
    assert any("ERROR" in reason for reason in result.red_reasons)


def test_rung2_lint_cmd_override_is_used_when_supplied(tmp_path, monkeypatch):
    """Rung 2 arrives ONLY as an explicit override — never automatically."""
    monkeypatch.setenv("PATH", "")
    bindir = _shim(tmp_path, "dot-runner lint: pipeline.dot: OK (no findings)")
    wk = _draft(tmp_path)

    result = demo.run_ladder(
        dot_path=wk / "pipeline.dot",
        companion_path=wk / "pipeline.md",
        relpath=RELPATH,
        lint_cmd=str(bindir / "dot-runner"),
    )
    assert result.level == T.LEVEL_LINT_DOCTRINE
    assert "OK (no findings)" in (result.lint_verdict or "")


def test_the_uvx_rung_is_asked_out_loud_and_named_as_an_inbound_fetch():
    question = T.uvx_consent_question(RELPATH)
    assert "Yes/no?" in question
    assert "inbound fetch" in question
    assert "none of your mined data leaves this machine" in question
    assert "uvx --from" in question


# ------------------------------------------------------------------ rung 3
def test_rung3_no_cli_gives_doctrine_only_and_the_exact_not_run_label(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    wk = _draft(tmp_path)

    result = demo.run_ladder(dot_path=wk / "pipeline.dot", companion_path=wk / "pipeline.md", relpath=RELPATH)
    assert result.level == T.LEVEL_DOCTRINE_ONLY
    assert result.lint_verdict is None
    assert result.lint_not_run_reason == (
        f"dot-runner lint: NOT RUN — the CLI is not installed here. Run it yourself: dot-runner lint {RELPATH}"
    )
    assert result.doctrine_verdict == "doctrine_ok"
    assert "[PASS] A4" in (result.doctrine_report or ""), "the per-check summary must be carried verbatim"


def test_rung3_runs_even_when_the_cli_is_present(tmp_path, monkeypatch):
    """The floor is a second opinion, not a fallback: it always runs."""
    monkeypatch.setenv("PATH", str(_shim(tmp_path, "dot-runner lint: pipeline.dot: OK (no findings)")))
    wk = _draft(tmp_path)
    result = demo.run_ladder(dot_path=wk / "pipeline.dot", companion_path=wk / "pipeline.md", relpath=RELPATH)
    assert result.doctrine_report is not None


# ------------------------------------------------------------------ rung 4
def test_rung4_a_checker_that_cannot_execute_yields_none_and_the_unverified_banner(tmp_path, monkeypatch):
    wk = _draft(tmp_path)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated environmental failure of the doctrine checker")

    monkeypatch.setattr(demo, "run_doctrine_checker", _boom)
    result = demo.run_ladder(dot_path=wk / "pipeline.dot", companion_path=wk / "pipeline.md", relpath=RELPATH)

    assert result.level == T.LEVEL_NONE
    assert result.doctrine_verdict is None
    assert result.doctrine_report is None
    assert "nothing machine-checked this pipeline" in (result.lint_not_run_reason or "")
    assert T.LABEL_UNVERIFIED == "UNVERIFIED — no machine check ran on this pipeline"


# ------------------------------------------------------------- the red fate
def test_a_doctrine_red_demo_is_never_published(tmp_path, monkeypatch):
    """Delete the evidence gate: A4 goes red, and NOTHING lands on disk."""
    monkeypatch.setenv("PATH", "")
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps(F.ranked_fixture()), encoding="utf-8")
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, dot_text=F.without_gate(), narrative=F.narrative_without("check"))

    verdict, report = demo.run_doctrine_checker(
        tmp_path / "demo" / slug / "pipeline.dot",
        tmp_path / "demo" / slug / "pipeline.md",
    )
    assert verdict == "doctrine_bad", "deleting the gate must be caught"
    assert "[FAIL] A4" in report, "A4 is the load-bearing check"

    with pytest.raises(demo.DemoGateRed) as exc:
        demo.assemble_demo(
            ranked_path=ranked_path,
            unit_id=None,
            workdir=tmp_path / "demo" / slug,
            output_dir=tmp_path / "out",
            generated_at="2020-01-01T00:00:00+00:00",
        )
    assert "NOT" in str(exc.value) and "published" in str(exc.value)
    assert not (tmp_path / "out").exists(), "no files may be copied when the gates came back red"
    assert not (tmp_path / "demos.json").exists(), "no demos.json entry may be written"
    assert (tmp_path / "demo" / slug / "gate-report.txt").is_file(), (
        "the verbatim gate reports must be left on disk for the ONE corrective retry"
    )


def test_the_red_fate_exits_two_through_the_cli(tmp_path, monkeypatch):
    import subprocess

    env = dict(os.environ, PATH="")
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps(F.ranked_fixture()), encoding="utf-8")
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, dot_text=F.without_gate(), narrative=F.narrative_without("check"))

    proc = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "attractor_scout_cli.py"),
            "demo",
            "assemble",
            "--ranked",
            str(ranked_path),
            "--workdir",
            str(tmp_path / "demo" / slug),
            "--output-dir",
            str(tmp_path / "out"),
            "--out",
            str(tmp_path / "demos.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 2
    assert "DemoGateRed" in proc.stderr


def test_publish_happens_only_after_the_gates(tmp_path, monkeypatch):
    """The publish-after-gates rule, stated as an ordering invariant."""
    monkeypatch.setenv("PATH", "")
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps(F.ranked_fixture()), encoding="utf-8")
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug)

    seen: list[str] = []
    real_ladder = demo.run_ladder
    real_publish = demo._publish

    def spy_ladder(**kwargs):
        seen.append("ladder")
        return real_ladder(**kwargs)

    def spy_publish(*args, **kwargs):
        seen.append("publish")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(demo, "run_ladder", spy_ladder)
    monkeypatch.setattr(demo, "_publish", spy_publish)
    demo.assemble_demo(
        ranked_path=ranked_path,
        unit_id=None,
        workdir=tmp_path / "demo" / slug,
        output_dir=tmp_path / "out",
        generated_at="2020-01-01T00:00:00+00:00",
    )
    assert seen == ["ladder", "publish"]


def test_the_three_level_labels_are_the_exact_strings_the_design_pins():
    assert T.LEVEL_LINT_DOCTRINE == "lint+doctrine"
    assert T.LEVEL_DOCTRINE_ONLY == "doctrine-only"
    assert T.LEVEL_NONE == "none"
