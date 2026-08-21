"""Doc-guard: every quantitative claim in SKILL.md is pinned to in-repo evidence.

Repo doctrine: a doc that makes a factual claim gets a guard test. SKILL.md is
a guidance surface that cites measured numbers; each RETAINED number must trace
to `evals/README.md` (the committed evidence file), and a claim that drifts
from its source must break this test rather than mislead a reader.

Numbers that could only be sourced from the maintainer's private calibration
data were softened to qualitative language in SKILL.md instead of pinned — this
test also guards that those private figures did NOT sneak back in.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"
EVAL_README = SKILL_DIR / "evals" / "README.md"

#: Each retained quantitative claim in SKILL.md, and the substring that must
#: exist in evals/README.md to source it. Matching is done after stripping
#: markdown emphasis (`*`), so bolding either side does not break the pin. If a
#: claim's wording changes, update BOTH sides here so the pin stays honest.
PINNED_CLAIMS = [
    # (phrase that must appear in SKILL.md, substring that must source it in evals/README.md)
    ("~18% verdict-flip rate", "18.06%"),
    ("78% of them the reasoning tier", "73 of 93 flips (78%)"),
    ("0 of 2 harness clusters", "0 of 2"),
    ("10/10 trials", "10/10 trials"),
]


def _strip_emphasis(text: str) -> str:
    """Drop markdown emphasis and collapse whitespace, so bolding or a line
    wrap between words never breaks a substring pin."""
    return re.sub(r"\s+", " ", text.replace("*", ""))


#: Private calibration figures that must NOT reappear as bare claims in SKILL.md
#: (they were deliberately softened to qualitative language). These strings live
#: only in the maintainer's uncommitted calibration data.
FORBIDDEN_PRIVATE_FIGURES = [
    "2,164/2,164",
    "2164/2164",
    "89% of real opportunities",
    "Two thirds of sessions",
    "65.2%",
]


def test_skill_md_exists():
    assert SKILL_MD.is_file()
    assert EVAL_README.is_file()


def test_every_pinned_claim_is_present_and_sourced():
    skill = _strip_emphasis(SKILL_MD.read_text(encoding="utf-8"))
    readme = _strip_emphasis(EVAL_README.read_text(encoding="utf-8"))
    for claim, source in PINNED_CLAIMS:
        assert _strip_emphasis(claim) in skill, f"SKILL.md no longer contains the pinned claim {claim!r}"
        assert _strip_emphasis(source) in readme, (
            f"SKILL.md claims {claim!r} but its source {source!r} is not in evals/README.md — "
            f"the pin is broken; re-check the number or update the guard."
        )


def test_no_private_calibration_figures_leaked_into_skill():
    skill = SKILL_MD.read_text(encoding="utf-8")
    leaked = [fig for fig in FORBIDDEN_PRIVATE_FIGURES if fig in skill]
    assert not leaked, (
        f"SKILL.md carries private-calibration figure(s) {leaked} that cannot be sourced from an "
        f"in-repo file. Soften to qualitative language or pin to evals/README.md."
    )


def test_skill_percentages_are_all_accounted_for():
    """Any bare percentage in SKILL.md prose must be one we deliberately pinned.

    Guards against a future edit dropping in a new, unsourced percentage. The
    only percentages allowed are the ones in PINNED_CLAIMS.
    """
    skill = SKILL_MD.read_text(encoding="utf-8")
    # Ignore the YAML frontmatter (descriptions/triggers carry no stats).
    body = skill.split("---", 2)[-1]
    pinned_pcts = {"18", "78"}
    found = set(re.findall(r"(\d{1,3})%", body))
    unexpected = found - pinned_pcts
    assert not unexpected, (
        f"SKILL.md contains unpinned percentage(s) {sorted(unexpected)}. Pin each to "
        f"evals/README.md (add to PINNED_CLAIMS) or remove it."
    )


# --------------------------------------------------------------------------
# Step 10 (deck mode) claims, pinned to the CODE that makes them true.
# Same doctrine as above, but the source of truth is the implementation, not
# evals/README.md: step 10 describes gate behaviour, so a claim that drifts
# from the code must break this test rather than mislead a reader.
# --------------------------------------------------------------------------

#: (phrase that must appear in SKILL.md, substring that must source it, source file)
STEP10_PINNED_CLAIMS = [
    ("attractor-scout-deck.html", 'DECK_FILENAME = f"{SKILL_NAME}-deck.html"', "scripts/attractor_scout/naming.py"),
    ("exit 3", "return 3", "scripts/attractor_scout_cli.py"),
    ("deck verify", "def cmd_deck", "scripts/attractor_scout_cli.py"),
    ("deck brief", 'action == "brief"', "scripts/attractor_scout_cli.py"),
    # The 600 s provider-timeout rationale for staged delegation (FOLD 6).
    ("600 s", "DECK_MAX_ATTEMPTS", "scripts/attractor_scout/deck.py"),
    # The structural depth gate (f) and the class tokens it counts.
    ("structural depth gate", "def gate_modal_depth", "scripts/attractor_scout/deck.py"),
    ("`evidence` inset", 'MODAL_EVIDENCE_CLASS = "evidence"', "scripts/attractor_scout/deck_templates.py"),
]


def test_step10_claims_are_sourced_in_code():
    skill = _strip_emphasis(SKILL_MD.read_text(encoding="utf-8"))
    for phrase, source, relpath in STEP10_PINNED_CLAIMS:
        assert _strip_emphasis(phrase) in skill, f"SKILL.md no longer contains the step-10 claim {phrase!r}"
        code = (SKILL_DIR / relpath).read_text(encoding="utf-8")
        assert source in code, (
            f"SKILL.md's step-10 claim {phrase!r} is not sourced by {source!r} in {relpath} \u2014 "
            f"the code moved; re-check the claim or update the pin."
        )


#: Step 1/4/6 provenance claims, pinned to the CODE that makes them true — same
#: doctrine as the step-10 block above. A rung, a verdict name or a policy that
#: drifts from `provenance.py` must break this test rather than mislead a reader.
PROVENANCE_PINNED_CLAIMS = [
    (
        "scripts/attractor_scout/provenance.py",
        'HUMAN_PRESUMED = "human-presumed"',
        "scripts/attractor_scout/provenance.py",
    ),
    ("human-presumed", 'HUMAN_PRESUMED = "human-presumed"', "scripts/attractor_scout/provenance.py"),
    ("likely-agent", 'LIKELY_AGENT = "likely-agent"', "scripts/attractor_scout/provenance.py"),
    ("`session:fork` opener", 'FORK_FIRST_EVENT = "session:fork"', "scripts/attractor_scout/provenance.py"),
    ("a pipeline or recipe start event", "ORCHESTRATION_START_EVENTS", "scripts/attractor_scout/provenance.py"),
    ("`working_dir`", "def classify_workspace", "scripts/attractor_scout/provenance.py"),
    (
        "opportunities are mined\nfrom `R4` only",
        "OPPORTUNITY_VERDICT = HUMAN_PRESUMED",
        "scripts/attractor_scout/provenance.py",
    ),
    ("unattributed", 'unit["author"] = provenance.UNKNOWN', "scripts/attractor_scout/ranking.py"),
    ("provenance panel", "def _provenance_block", "scripts/attractor_scout/render.py"),
    # Step 4's never-promote-agent-to-human adjudication rule. Prose-only in
    # SKILL.md, so pin it to the invariant comment at the seam that consumes
    # the adjudicated label — a SKILL.md edit that drops the rule goes RED.
    (
        "may NEVER move a cluster",
        "may NEVER move a\n        # cluster toward human",
        "scripts/attractor_scout/clustering.py",
    ),
]


def test_provenance_claims_are_sourced_in_code():
    skill = _strip_emphasis(SKILL_MD.read_text(encoding="utf-8"))
    for phrase, source, relpath in PROVENANCE_PINNED_CLAIMS:
        assert _strip_emphasis(phrase) in skill, f"SKILL.md no longer contains the provenance claim {phrase!r}"
        code = (SKILL_DIR / relpath).read_text(encoding="utf-8")
        assert source in code, (
            f"SKILL.md's provenance claim {phrase!r} is not sourced by {source!r} in {relpath} \u2014 "
            f"the code moved; re-check the claim or update the pin."
        )


#: Step 2 (fast-tier labelling) claims, pinned to the CODE that makes them
#: true. The pin here USED to read "zero invented ids" - a live 64-batch run
#: disproved it, so the claim was replaced with the containment that is
#: actually enforced. This guard's contract is doc-follows-reality: if the
#: batch boundary is ever removed, the SKILL.md sentence describing it must go
#: RED rather than keep promising a check that no longer runs.
LABELLING_PINNED_CLAIMS = [
    (
        "checked against the ids THAT batch was handed",
        "def validate_batch_labels",
        "scripts/attractor_scout/clustering.py",
    ),
    (
        "`invented_ids_rejected` in the run summary",
        '"invented_ids_rejected": self.invented_ids_rejected',
        "scripts/attractor_scout/pipeline.py",
    ),
    ("label-merge", "def cmd_label_merge", "scripts/attractor_scout_cli.py"),
]


def test_labelling_claims_are_sourced_in_code():
    skill = _strip_emphasis(SKILL_MD.read_text(encoding="utf-8"))
    for phrase, source, relpath in LABELLING_PINNED_CLAIMS:
        assert _strip_emphasis(phrase) in skill, f"SKILL.md no longer contains the labelling claim {phrase!r}"
        code = (SKILL_DIR / relpath).read_text(encoding="utf-8")
        assert source in code, (
            f"SKILL.md's labelling claim {phrase!r} is not sourced by {source!r} in {relpath} \u2014 "
            f"the code moved; re-check the claim or update the pin."
        )


def test_the_retired_zero_invented_ids_pin_never_comes_back():
    """The old step-2 pin claimed a property the machine did not have.

    A full-corpus run measured the fast tier returning session ids that were
    in no supplied batch. "Zero" was therefore never a discipline this skill
    could keep - only a containment it could enforce. Re-asserting the old
    wording would be a doc claiming a guarantee the code does not give.
    """
    skill = _strip_emphasis(SKILL_MD.read_text(encoding="utf-8"))
    assert "zero invented ids" not in skill.lower(), (
        "SKILL.md re-asserted 'zero invented ids'. The enforced truth is that invented ids are "
        "REJECTED at the batch boundary and COUNTED - say that instead."
    )


def test_skill_names_every_rung_of_the_ladder():
    """All six rungs must be described in the guidance while all six run in code."""
    skill = SKILL_MD.read_text(encoding="utf-8")
    from attractor_scout import provenance

    for rung in provenance.RUNGS:
        assert f"`{rung}`" in skill, f"SKILL.md no longer names provenance rung {rung}"


def test_step10_names_all_six_gates():
    """Step 10 must describe every deterministic deck gate (a)-(f) by letter.

    Guards against a future edit silently dropping a gate description from the
    guidance while the gate still runs in code.
    """
    skill = SKILL_MD.read_text(encoding="utf-8")
    for letter in "abcdef":
        assert f"**({letter})**" in skill, f"SKILL.md step 10 no longer names deck gate ({letter})"
