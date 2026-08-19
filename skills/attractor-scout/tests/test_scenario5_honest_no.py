"""SCENARIO 5 — the honest-NO boundary, and the honest downgrade.

Pass thresholds:
  1. 4/4 per-class: every planted unit lands in its by-construction class,
     0 misclassifications, and every NO is EMITTED WITH ITS VERDICT
     (0 Fit-failing units silently dropped).
  2. Gate-flip A/B (N >= 20 trials): the same planted `one-shot`, with a gate
     added, flips one-shot -> OPPORTUNITY in 20/20; the verdict-set diff vs
     the no-gate variant is EXACTLY one label change.
  3. UNKNOWN-never-FAIL: 0 occurrences of a zero-error cluster rendered as
     FAIL, anywhere in the serialized output OR the HTML.
  4. Composition corroboration (17 recipe / 14 one-shot / 2 fragile +/-1) is a
     REAL-CORPUS arm -> `evals/scenario5_honest_no_real.py`.

The renderer-honesty check is here rather than in a render test on purpose:
manufacturing a FAIL from absence of evidence is a HONESTY failure, not a
formatting one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attractor_scout import discover, extract, fit_cycle, fit_gate, fit_recovery, honest_no, ranking, render

from fixtures.synthetic_corpus import build_honest_no_corpus

GATE_FLIP_TRIALS = 20


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("honestno") / "projects"
    truth = build_honest_no_corpus(root)
    disc = discover.enumerate_sessions(root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    by_id = {r["session_id"]: r for r in records}
    units = {name: [by_id[sid] for sid in sids if sid in by_id] for name, sids in truth.expected["units"].items()}
    return units, truth


def _verdict(members: list[dict]) -> honest_no.FitVerdict:
    return honest_no.classify(
        cycle=fit_cycle.cluster_cycle(members)["cycle"],
        gate=fit_gate.cluster_gate(members)["gate"],
        recovery=fit_recovery.detect(members).verdict,
    )


# ------------------------------------------------------------------ (1) 4/4
@pytest.mark.parametrize(
    ("unit", "expected_class", "expected_subtest"),
    [
        ("recipe", "recipe", "4a"),
        ("one_shot_no_gate", "one-shot", "4b"),
        ("fragile", "fragile", "4c"),
        ("unproven", None, None),
    ],
)
def test_each_planted_class_lands_where_it_was_planted(planted, unit, expected_class, expected_subtest):
    units, _ = planted
    verdict = _verdict(units[unit])
    assert verdict.no_class == expected_class, f"{unit}: got {verdict.no_class!r}"
    assert verdict.failed_subtest == expected_subtest


def test_every_honest_no_is_emitted_with_its_verdict_and_remediation(planted):
    units, _ = planted
    for name in ("recipe", "one_shot_no_gate", "fragile"):
        verdict = _verdict(units[name])
        assert verdict.verdict == honest_no.HONEST_NO
        assert verdict.failed_subtest
        assert verdict.remediation, f"{name} was emitted without remediation"


def test_no_fit_failing_unit_is_silently_dropped(planted):
    """Every planted unit must appear SOMEWHERE in the ranked output."""
    units, _ = planted
    inputs = [
        {"unit_id": name, "name": name, "members": members, "author_adjudicated": "human"}
        for name, members in units.items()
    ]
    result = ranking.rank(inputs)
    seen = {u["unit_id"] for u in result["opportunities"]} | {u["unit_id"] for u in result["honest_no"]}
    assert seen == set(units), f"missing: {set(units) - seen}"


# ------------------------------------------------------------- (2) gate flip
def test_adding_a_gate_flips_one_shot_to_opportunity_every_trial(planted):
    units, _ = planted
    flips = 0
    for _ in range(GATE_FLIP_TRIALS):
        before = _verdict(units["one_shot_no_gate"])
        after = _verdict(units["one_shot_with_gate"])
        if before.no_class == "one-shot" and after.verdict != honest_no.HONEST_NO:
            flips += 1
    assert flips == GATE_FLIP_TRIALS, f"{flips}/{GATE_FLIP_TRIALS}"


def test_the_gate_flip_moves_exactly_one_label_and_nothing_else(planted):
    """Isolation check: the two variants differ ONLY in the gate sub-test."""
    units, _ = planted
    before = _verdict(units["one_shot_no_gate"])
    after = _verdict(units["one_shot_with_gate"])
    assert before.cycle == after.cycle
    assert before.recovery == after.recovery
    assert before.gate is False and after.gate is True
    changed = {k for k in ("verdict", "no_class", "failed_subtest") if getattr(before, k) != getattr(after, k)}
    assert changed == {"verdict", "no_class", "failed_subtest"}


# --------------------------------------------------- (3) UNKNOWN never FAIL
def test_zero_error_cluster_is_unknown_not_fail(planted):
    units, _ = planted
    recovery = fit_recovery.detect(units["unproven"])
    assert fit_recovery.zero_error_cluster(units["unproven"])
    assert recovery.verdict == fit_recovery.UNKNOWN
    assert recovery.is_fail is False
    assert recovery.unobserved is True


def test_unobserved_recovery_downgrades_rather_than_fails(planted):
    units, _ = planted
    verdict = _verdict(units["unproven"])
    assert verdict.verdict == honest_no.OPPORTUNITY_UNPROVEN
    assert verdict.fit == 1, "an unproven unit still ranks; it is a caveat, not a NO"
    assert verdict.no_class is None


def test_no_code_path_maps_zero_errors_to_fail():
    """Exhaustive over the mapping table, not a spot check."""
    for cycle in (True, False):
        for gate in (True, False):
            verdict = honest_no.classify(cycle=cycle, gate=gate, recovery=fit_recovery.UNKNOWN)
            assert verdict.no_class != "fragile"
            assert verdict.failed_subtest != "4c"


def test_rendered_html_never_shows_a_zero_error_unit_as_fail(planted, tmp_path):
    units, _ = planted
    inputs = [
        {"unit_id": name, "name": name, "members": members, "author_adjudicated": "human"}
        for name, members in units.items()
    ]
    result = ranking.rank(inputs)
    html = render.render_html(result, generated_at="2020-01-01T00:00:00+00:00")

    unproven_rows = [u for u in result["opportunities"] if u["unit_id"] == "unproven"]
    assert unproven_rows and unproven_rows[0]["verdict"] == honest_no.OPPORTUNITY_UNPROVEN
    assert "OPPORTUNITY(unproven)" in html
    # The only permitted appearances of a failure word are on the genuinely
    # fragile unit; the unproven unit must never be described as failing.
    assert "unproven</div><div>FAIL" not in html
    for token in ("FAIL", "FAILED"):
        assert f">{token}<" not in html

    out = render.write_report(result, tmp_path / "r.html", generated_at="2020-01-01T00:00:00+00:00")
    assert out.is_file()


def test_renderer_is_deterministic(planted):
    units, _ = planted
    inputs = [{"unit_id": n, "name": n, "members": m, "author_adjudicated": "human"} for n, m in units.items()]
    result = ranking.rank(inputs)
    a = render.render_html(result, generated_at="2020-01-01T00:00:00+00:00")
    b = render.render_html(result, generated_at="2020-01-01T00:00:00+00:00")
    assert a == b


def test_rendered_html_is_self_contained(planted):
    units, _ = planted
    inputs = [{"unit_id": n, "name": n, "members": m, "author_adjudicated": "human"} for n, m in units.items()]
    html = render.render_html(ranking.rank(inputs), generated_at="2020-01-01T00:00:00+00:00")
    for forbidden in ("http://", "https://", "//cdn", "<link", "src="):
        assert forbidden not in html, f"artifact reaches outside itself: {forbidden!r}"


def test_a_unit_failing_frequency_is_out_of_scope_not_an_honest_no(planted):
    """Concept boundary: below the floor is not a decline, it is not-a-unit."""
    units, _ = planted
    singleton = [units["recipe"][0]]
    result = ranking.rank(
        [{"unit_id": "lonely", "name": "lonely", "members": singleton, "author_adjudicated": "human"}]
    )
    assert result["below_frequency_floor"][0]["unit_id"] == "lonely"
    assert not result["honest_no"]
    assert not result["opportunities"]


def test_fit_failing_units_score_zero_and_leave_the_ranking(planted):
    """Gap-5 seam, composed: high toil x fit=0 -> score 0, verdict still emitted."""
    units, _ = planted
    result = ranking.rank(
        [
            {
                "unit_id": "one-shot",
                "name": "one-shot",
                "members": units["one_shot_no_gate"],
                "author_adjudicated": "human",
            }
        ]
    )
    assert not result["opportunities"]
    (no_unit,) = result["honest_no"]
    assert no_unit["score"] == 0.0
    assert no_unit["leverage"] > 0.0, "the toil was real; only the fit multiplier zeroed it"
    assert no_unit["remediation"]
