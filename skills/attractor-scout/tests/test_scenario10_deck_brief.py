"""Scenario 10 — the deck brief, and the CLI contract around deck mode.

The brief is the ONLY thing a fresh-context deck author sees. If it does not
carry the mandates, the author cannot pass the gates; if it does not carry the
real `.dot` text, the author cannot draw the real pipeline. Both are asserted
here rather than promised.

The CLI half pins the exit-code contract: 0 when every gate passed, 3 when one
did not. **A gate-red deck is never published** — the same posture the demo
layer takes on a red verification ladder.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from attractor_scout import deck as deck_mod
from attractor_scout import deck_templates as DT
from attractor_scout.naming import DECK_FILENAME, SKILL_NAME

from fixtures import deck_fixture as F

SKILL_DIR = Path(__file__).resolve().parent.parent
CLI = SKILL_DIR / "scripts" / "attractor_scout_cli.py"


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    (tmp_path / "ranked.json").write_text(json.dumps(F.deck_ranked_fixture()), encoding="utf-8")
    (tmp_path / "demos.json").write_text(json.dumps(F.deck_demos_fixture()), encoding="utf-8")
    return tmp_path


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# ------------------------------------------------------------------- brief
def test_brief_is_written_and_deterministic(run_dir: Path):
    first = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    )
    assert first.name == "deck-brief.md"
    text_a = first.read_text(encoding="utf-8")
    second = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck2"
    )
    assert second.read_text(encoding="utf-8") == text_a, "the same run must always produce the same brief"


def test_brief_carries_all_four_mandates(run_dir: Path):
    """★ Without these, the gates are unpassable by an author who cannot see them."""
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    for mandate in (
        "MANDATE 1 --- EVERY DISPLAYED NUMBER RESOLVES",
        "MANDATE 2 --- DECLARE EVERY DERIVED VALUE",
        "MANDATE 3 --- DIAGRAM FIDELITY IS CHECKED",
        "MANDATE 4 --- ABSENT DATA GETS AN HONEST NOTE",
    ):
        assert mandate in brief, f"the brief must state {mandate!r}"
    assert DT.DERIVED_BLOCK_ID in brief
    assert DT.NODE_ATTR in brief and DT.EDGE_ATTR in brief
    assert "TESTIMONY" in brief


def test_brief_carries_the_real_dot_text_verbatim(run_dir: Path):
    """★ The diagram must be OF the pipeline, so the pipeline must be IN the brief."""
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert F.DECK_DOT.rstrip() in brief
    for node in F.DECK_NODES:
        assert node in brief
    for edge in F.DECK_EDGES:
        assert edge in brief, f"the brief must spell edge {edge!r} the way the gate expects it"


def test_brief_carries_the_style_contract_and_hard_constraints(run_dir: Path):
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    for marker in (
        "THE SECTION ARC",
        "HERO",
        "TEACH THE BASIN",
        "RANKED OPPORTUNITIES",
        "DEMONSTRATIONS",
        "HONEST NOES AND WASTE",
        "GOING FORWARD",
        "THE DIAGRAM GRAMMAR",
        "HARD CONSTRAINTS",
        "ZERO EXTERNAL REQUESTS",
        "WORKS FROM file://",
        "SYSTEM FONTS ONLY",
        "prefers-reduced-motion",
    ):
        assert marker in brief, f"the brief must carry {marker!r}"


def test_brief_carries_the_gate_verdicts_verbatim(run_dir: Path):
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert "doctrine_ok" in brief
    assert "doctrine-only" in brief
    assert "NOT RUN" in brief


def test_brief_names_what_the_run_does_not_have(run_dir: Path):
    """Mandate 4 needs a subject: the absences are computed, not guessed."""
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert "WHAT THIS RUN DOES NOT HAVE" in brief
    assert "workspace / project attribution" in brief
    assert "per-step LLM success rate" in brief


def test_brief_lists_the_numbers_that_are_already_legal(run_dir: Path):
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert "NUMBERS THAT ARE ALREADY LEGAL" in brief
    whitelist = deck_mod.build_number_whitelist(
        json.loads((run_dir / "ranked.json").read_text()),
        json.loads((run_dir / "demos.json").read_text()),
    )
    assert "930" in whitelist and "12" in whitelist and "7" in whitelist


def test_brief_works_with_no_demonstrations(run_dir: Path):
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=None, workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert "none were produced in this run" in brief
    assert "skip the demonstrations section" in brief


def test_brief_names_the_published_filename(run_dir: Path):
    brief = deck_mod.build_deck_brief(
        ranked_path=run_dir / "ranked.json", demos_path=run_dir / "demos.json", workdir=run_dir / "deck"
    ).read_text(encoding="utf-8")
    assert DECK_FILENAME in brief
    assert DECK_FILENAME == f"{SKILL_NAME}-deck.html"


# --------------------------------------------------------------------- CLI
def test_cli_brief_prints_only_the_path(run_dir: Path):
    proc = _run_cli(
        "deck",
        "brief",
        "--ranked",
        str(run_dir / "ranked.json"),
        "--demos",
        str(run_dir / "demos.json"),
        "--workdir",
        str(run_dir / "deck"),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(run_dir / "deck" / "deck-brief.md")


def test_cli_verify_exits_zero_on_a_clean_deck(run_dir: Path):
    (run_dir / "deck.html").write_text(F.clean_deck(), encoding="utf-8")
    proc = _run_cli(
        "deck",
        "verify",
        "--deck",
        str(run_dir / "deck.html"),
        "--ranked",
        str(run_dir / "ranked.json"),
        "--demos",
        str(run_dir / "demos.json"),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "verdict: PASS" in proc.stdout


def test_cli_verify_exits_three_on_a_red_gate(run_dir: Path):
    """★ The publish-or-refuse seam: a red gate is a distinct, loud exit code."""
    (run_dir / "deck.html").write_text(F.with_fabricated_number(), encoding="utf-8")
    proc = _run_cli(
        "deck",
        "verify",
        "--deck",
        str(run_dir / "deck.html"),
        "--ranked",
        str(run_dir / "ranked.json"),
        "--demos",
        str(run_dir / "demos.json"),
        "--report",
        str(run_dir / "deck-gate-report.txt"),
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "verdict: FAIL" in proc.stdout
    assert "DECK GATE RED [d]" in proc.stderr
    assert "was NOT published" in proc.stderr
    report = (run_dir / "deck-gate-report.txt").read_text(encoding="utf-8")
    assert "[FAIL] d" in report


def test_cli_verify_writes_a_machine_readable_report(run_dir: Path):
    (run_dir / "deck.html").write_text(F.clean_deck(), encoding="utf-8")
    proc = _run_cli(
        "deck",
        "verify",
        "--deck",
        str(run_dir / "deck.html"),
        "--ranked",
        str(run_dir / "ranked.json"),
        "--demos",
        str(run_dir / "demos.json"),
        "--json-out",
        str(run_dir / "gates.json"),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads((run_dir / "gates.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert len(payload["gates"]) == 5


def test_cli_fails_loud_on_a_missing_deck(run_dir: Path):
    proc = _run_cli(
        "deck",
        "verify",
        "--deck",
        str(run_dir / "nope.html"),
        "--ranked",
        str(run_dir / "ranked.json"),
    )
    assert proc.returncode == 2
    assert "FAIL-LOUD" in proc.stderr


# ------------------------------------------------------------------ publish
def test_publish_lands_beside_the_report(run_dir: Path, tmp_path: Path):
    candidate = run_dir / "deck.html"
    candidate.write_text(F.clean_deck(), encoding="utf-8")
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    published = deck_mod.publish_deck(candidate, out_dir)
    assert published == out_dir / DECK_FILENAME
    assert published.read_text(encoding="utf-8") == F.clean_deck()


def test_deck_mode_is_bounded_to_two_attempts():
    """One authoring delegation, plus at most one gate-informed retry. Never more."""
    assert deck_mod.DECK_MAX_ATTEMPTS == 2
