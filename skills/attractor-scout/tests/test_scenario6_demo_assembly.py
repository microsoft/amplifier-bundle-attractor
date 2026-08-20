"""Scenario 6 — the demonstration brief and the count-integrity guard.

The mining half's trust contract is "every count re-verified before render".
The demonstration half inherits it BY CONSTRUCTION: the LLM never states a
number, it only narrates around numbers the deterministic layer placed. This
file is the enforcement of that claim.

Layer 1 — the brief is DETERMINISTIC and carries the evidence.
Layer 2 — RED PROOFS. A guard that has never been shown failing is not a
guard, so every validation path is exercised against a fixture mutated to
break exactly one property: an invented count, a walked node that is not in
the graph, a missing slot, a missing file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from attractor_scout import demo
from attractor_scout.errors import AttractorScoutError

from fixtures import demo_fixture as F

SKILL_DIR = Path(__file__).resolve().parent.parent
CLI = [sys.executable, str(SKILL_DIR / "scripts" / "attractor_scout_cli.py")]


def _write_ranked(tmp_path: Path, **kwargs) -> Path:
    path = tmp_path / "ranked.json"
    path.write_text(json.dumps(F.ranked_fixture(**kwargs)), encoding="utf-8")
    return path


def _write_extracts(tmp_path: Path, ranked: dict) -> Path:
    """An extract whose members' terminal windows carry verify-class tools."""
    unit = ranked["opportunities"][0]
    path = tmp_path / "extracts.jsonl"
    lines = []
    for sid in unit["members"]:
        lines.append(
            json.dumps(
                {
                    "session_id": sid,
                    "tool_tail": ["read_file", "edit_file", "python_check"],
                    "tool_seq": ["read_file", "edit_file", "python_check"],
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- layer 1
def test_brief_carries_the_verified_stats_and_is_deterministic(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, brief_path = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    text = brief_path.read_text(encoding="utf-8")

    assert slug.endswith(f"-{F.DEMO_UNIT_ID}"), "the slug must carry the unit id for uniqueness"
    for rendered in ("7", "12", "4", "930", "0.33"):
        assert rendered in text, f"the brief must quote the verified stat {rendered}"
    assert F.DEMO_UNIT_NAME in text
    assert "at most 9 nodes" in text, "the node budget must be stated to the author"
    assert "A10" in text and "A4" in text, "the A0-A10 contract summary must be carried"
    assert "prompt=" in text and "tool_command=" in text, "the vocabulary excerpt must be carried"

    # Determinism: same ranking, same brief, byte for byte.
    _, again = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo2")
    assert again.read_text(encoding="utf-8") == text


def test_brief_carries_the_gate_tool_evidence_from_their_own_sessions(tmp_path: Path):
    ranked = F.ranked_fixture()
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps(ranked), encoding="utf-8")
    extracts = _write_extracts(tmp_path, ranked)

    _, brief_path = demo.build_brief(
        ranked_path=ranked_path,
        unit_id=None,
        workdir=tmp_path / "demo",
        extracts_path=extracts,
    )
    text = brief_path.read_text(encoding="utf-8")
    assert "python_check" in text, "the census must name the verify-class tool actually observed"
    assert "read_file" not in text.split("## What to write")[0], "non-verify tools are not gate evidence"


def test_brief_is_honest_when_no_gate_evidence_was_observed(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    _, brief_path = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    text = brief_path.read_text(encoding="utf-8")
    assert "No verify-class tool was observed" in text
    assert "never invent evidence that was not there" in text


def test_assembly_succeeds_on_the_canned_fixture(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug)

    entry = demo.assemble_demo(
        ranked_path=ranked_path,
        unit_id=None,
        workdir=tmp_path / "demo" / slug,
        output_dir=tmp_path / "out",
        generated_at="2020-01-01T00:00:00+00:00",
    )
    assert entry["slug"] == slug
    assert entry["stats"]["n_sessions"] == 7
    assert entry["convergence_math"]["p_step"] == demo.P_STEP
    assert entry["convergence_math"]["chain_len"] == 4
    assert entry["convergence_math"]["gated_loop"] > entry["convergence_math"]["once_through"]
    assert (tmp_path / "out" / entry["dot_relpath"]).is_file()
    assert (tmp_path / "out" / entry["companion_relpath"]).is_file()


def test_only_opportunity_verdicts_may_be_demonstrated(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path, verdict="HONEST-NO")
    with pytest.raises(AttractorScoutError) as exc:
        demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    assert "anti-pattern" in str(exc.value)


def test_unproven_is_a_caveat_not_a_disqualifier(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path, verdict="OPPORTUNITY(unproven)", recovery="UNKNOWN")
    slug, brief_path = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    assert "unproven" in brief_path.read_text(encoding="utf-8")
    F.write_draft(tmp_path / "demo" / slug)
    entry = demo.assemble_demo(
        ranked_path=ranked_path,
        unit_id=None,
        workdir=tmp_path / "demo" / slug,
        output_dir=tmp_path / "out",
        generated_at="2020-01-01T00:00:00+00:00",
    )
    assert entry["fit"]["recovery"] == "UNKNOWN"
    assert entry["fit"]["verdict"] == "OPPORTUNITY(unproven)"


# ---------------------------------------------------------------- layer 2
def _assemble_cli(tmp_path: Path, ranked_path: Path, slug: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            *CLI,
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
            "--generated-at",
            "2020-01-01T00:00:00+00:00",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_red_proof_an_invented_count_is_fatal_and_names_the_token(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, narrative=F.with_invented_count(token="97"))

    proc = _assemble_cli(tmp_path, ranked_path, slug)
    assert proc.returncode == 2, "an invented count must be FATAL, same posture as rank --strict"
    assert "'97'" in proc.stderr, "the offending token must be named"
    assert not (tmp_path / "out").exists(), "nothing may be published when validation failed"


def test_red_proof_a_walked_node_not_in_the_dot_is_fatal(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, narrative=F.with_unknown_node("ghost_node"))

    proc = _assemble_cli(tmp_path, ranked_path, slug)
    assert proc.returncode == 2
    assert "ghost_node" in proc.stderr
    assert not (tmp_path / "demos.json").exists()


def test_red_proof_a_missing_slot_is_fatal(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, narrative=F.without_slot("payoff_note"))

    proc = _assemble_cli(tmp_path, ranked_path, slug)
    assert proc.returncode == 2
    assert "payoff_note" in proc.stderr


def test_red_proof_a_missing_companion_is_fatal(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug, omit=("pipeline.md",))

    proc = _assemble_cli(tmp_path, ranked_path, slug)
    assert proc.returncode == 2
    assert "pipeline.md" in proc.stderr


def test_red_proof_an_over_long_slot_is_fatal(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    long_narrative = json.loads(json.dumps(F.DEMO_NARRATIVE))
    long_narrative["scenario_gist"] = "x" * (demo.MAX_SLOT_CHARS + 1)
    F.write_draft(tmp_path / "demo" / slug, narrative=long_narrative)

    proc = _assemble_cli(tmp_path, ranked_path, slug)
    assert proc.returncode == 2
    assert "scenario_gist" in proc.stderr


def test_prose_numbers_that_ARE_their_stats_are_legal(tmp_path: Path):
    ranked_path = _write_ranked(tmp_path)
    ranked = json.loads(ranked_path.read_text(encoding="utf-8"))
    stats = demo.unit_stats(ranked["opportunities"][0])
    ok = json.loads(json.dumps(F.DEMO_NARRATIVE))
    ok["payoff_note"] = "Across 7 sessions this shape repeated; about a dozen tool calls each time."
    cleaned = demo.validate_narrative(ok, stats, F.DEMO_DOT)
    assert "about a dozen" in cleaned["payoff_note"]


# -------------------------------------------------------- named-limit pins
# Finding 2: the digit whitelist has two documented, deliberately-unclosed
# bypasses. These tests pin the CURRENT (passing) behavior on purpose, so a
# future claim to have closed either one turns them RED and forces an honest
# update to demo.validate_narrative's docstring and the design's D4 section,
# rather than a silent behavior change. See that docstring for the rationale.
def test_named_limit_decomposition_bypass_is_expected_to_pass(tmp_path: Path):
    """`0.33` rendered as `0.33` also whitelists the bare run `33` — documented.

    Closing it would ban the legitimate `0.33`->`33%` reference; the boundary
    is the tokenizer's. If this ever starts RAISING, the bypass was closed and
    the docs must be updated to match.
    """
    stats = {
        "n_sessions": 7,
        "med_tool_calls": 12.0,
        "med_llm_cycles": 4.0,
        "med_span_s": 930.0,
        "err_rate": 0.33,
        "provisional": False,
    }
    assert "33" in demo.allowed_digit_runs(stats), "the sub-run 33 leaks from the rendered 0.33"
    narrative = json.loads(json.dumps(F.DEMO_NARRATIVE))
    narrative["payoff_note"] = "It shaved 33 minutes off the loop."  # 33 is not a stat, but decomposes in
    cleaned = demo.validate_narrative(narrative, stats, F.DEMO_DOT)
    assert "33 minutes" in cleaned["payoff_note"]


def test_named_limit_spelled_out_numbers_are_expected_to_pass(tmp_path: Path):
    """ "forty-seven hours" is not a digit-run and passes — documented.

    Detecting written numerals is NLP guesswork; a fail-loud guard that guessed
    wrong would block honest prose. If this ever starts RAISING, spelled-out
    detection was added and the docs must be updated to match.
    """
    ranked_path = _write_ranked(tmp_path)
    stats = demo.unit_stats(json.loads(ranked_path.read_text(encoding="utf-8"))["opportunities"][0])
    narrative = json.loads(json.dumps(F.DEMO_NARRATIVE))
    narrative["payoff_note"] = "It reclaimed forty-seven hours of hand-running the same check."
    cleaned = demo.validate_narrative(narrative, stats, F.DEMO_DOT)
    assert "forty-seven hours" in cleaned["payoff_note"]


def test_the_still_closed_cases_stay_closed(tmp_path: Path):
    """The bypasses are narrow: `93` from `930` and node-smuggling STAY rejected."""
    ranked_path = _write_ranked(tmp_path)
    stats = demo.unit_stats(json.loads(ranked_path.read_text(encoding="utf-8"))["opportunities"][0])
    # 930 is a stat (med_span_s); 93 is NOT one of its whitelisted sub-runs.
    assert "93" not in demo.allowed_digit_runs(stats)
    bad = json.loads(json.dumps(F.DEMO_NARRATIVE))
    bad["payoff_note"] = "It ran 93 times."
    with pytest.raises(demo.DemoNarrativeInvalid) as exc:
        demo.validate_narrative(bad, stats, F.DEMO_DOT)
    assert "'93'" in str(exc.value)


def test_empty_opportunity_list_refuses_to_invent_a_subject(tmp_path: Path):
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps({"opportunities": [], "summary": {}}), encoding="utf-8")
    with pytest.raises(AttractorScoutError) as exc:
        demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    assert "primer-only" in str(exc.value)


def test_demos_json_append_replaces_rather_than_duplicates(tmp_path: Path):
    out = tmp_path / "demos.json"
    entry = {"unit_id": "d1", "name": "first"}
    demo.write_demos(entry, out, append=False)
    demo.write_demos({"unit_id": "d2", "name": "second"}, out, append=True)
    doc = demo.write_demos({"unit_id": "d1", "name": "first again"}, out, append=True)
    assert [d["unit_id"] for d in doc["demos"]] == ["d2", "d1"]
    assert doc["primer"] is True
    assert doc["explainer_url"].endswith("attractor-explained.html")


def test_step9_menu_hides_already_demonstrated_units(tmp_path: Path):
    extra = [dict(F.ranked_fixture(unit_id="d2", name="SYNTHETIC-UNIT-E")["opportunities"][0])]
    ranked = F.ranked_fixture(extra_opportunities=extra)
    remaining = demo.not_yet_demonstrated(ranked, {"demos": [{"unit_id": "d1"}]})
    assert [u["unit_id"] for u in remaining] == ["d2"]
