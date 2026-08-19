"""SCENARIO 1 (Gate 2) — the AUTHOR admission gate precedes ranking.

This file is the PORTABLE TWIN half of the scenario: planted-human admission
= 100% and planted-harness admission = 0%, each holding across 10/10 trials.
The real-corpus arms (deterministic prior admits BOTH harness clusters into
the ranked top-2; treatment recovers human 42 -> 33 +/- 2; anchor re-find) run
from `evals/gate2_author_admission.py` against the maintainer's own data,
which is never in this repo.

The deterministic prior is expected to be WRONG on one shape here, and that
is asserted rather than hidden: a templated autonomous "lane" mission is
harness-launched but contains real engineering work, and the prior cannot
read intent from prompt text. Recovering that over-call is exactly what the
`general`-tier adjudication is for, and the gate accepts an adjudicated label
over its own prior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attractor_scout import author, discover, extract, ranking

from fixtures.synthetic_corpus import build_author_corpus

TRIALS = 10


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("author") / "projects"
    truth = build_author_corpus(root)
    disc = discover.enumerate_sessions(root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    by_id = {r["session_id"]: r for r in records}
    human = {n: [by_id[s] for s in ids if s in by_id] for n, ids in truth.expected["human"].items()}
    harness = {n: [by_id[s] for s in ids if s in by_id] for n, ids in truth.expected["harness"].items()}
    return human, harness


def _units(human: dict, harness: dict) -> list[dict]:
    units = []
    for name, members in {**human, **harness}.items():
        prior = author.cluster_author_prior(members)
        units.append(
            {
                "unit_id": name,
                "name": name,
                "members": members,
                "author_prior": prior["author_prior"],
                "author_mix": prior["author_mix"],
            }
        )
    return units


def test_planted_harness_admission_is_zero_across_ten_trials(planted):
    human, harness = planted
    failures = 0
    for _ in range(TRIALS):
        result = ranking.rank(_units(human, harness))
        ranked_ids = {u["unit_id"] for u in result["opportunities"]} | {u["unit_id"] for u in result["honest_no"]}
        if ranked_ids & set(harness):
            failures += 1
    assert failures == 0, f"harness admitted in {failures}/{TRIALS} trials"


def test_planted_human_admission_is_one_hundred_percent_across_ten_trials(planted):
    human, harness = planted
    passes = 0
    for _ in range(TRIALS):
        result = ranking.rank(_units(human, harness))
        ranked_ids = {u["unit_id"] for u in result["opportunities"]} | {u["unit_id"] for u in result["honest_no"]}
        if set(human) <= ranked_ids:
            passes += 1
    assert passes == TRIALS, f"human admitted in only {passes}/{TRIALS} trials"


def test_harness_units_are_routed_to_waste_findings_not_dropped(planted):
    human, harness = planted
    result = ranking.rank(_units(human, harness))
    waste_ids = {w["unit_id"] for w in result["waste_findings"]}
    assert waste_ids == set(harness), "harness ceremony must still be REPORTED, as waste"
    for finding in result["waste_findings"]:
        assert finding["reclaimable_hours"] >= 0


def test_the_gate_runs_before_scoring_not_after(planted):
    """A harness unit must never receive a score at all."""
    human, harness = planted
    result = ranking.rank(_units(human, harness))
    for finding in result["waste_findings"]:
        assert "score" not in finding


def test_frequency_alone_would_have_ranked_the_machine_first(planted):
    """The failure this gate exists to prevent, demonstrated on the fixture."""
    human, harness = planted
    biggest = max({**human, **harness}.items(), key=lambda kv: len(kv[1]))
    assert biggest[0] in harness, "fixture must plant harness as the most frequent thing"


def test_sentinel_probes_are_classified_harness_by_the_prior(planted):
    _, harness = planted
    for rec in harness["liveness-sentinel"]:
        assert rec["author"] == author.HARNESS
        assert "sentinel" in rec["author_signals"]


def test_eval_phrasing_is_classified_harness_by_the_prior(planted):
    _, harness = planted
    for rec in harness["self-classifier"]:
        assert rec["author"] == author.HARNESS


def test_conversational_multi_turn_work_is_classified_human_by_the_prior(planted):
    human, _ = planted
    for members in human.values():
        for rec in members:
            assert rec["author"] == author.HUMAN


def test_adjudicated_label_overrides_the_prior(planted):
    """The recovery path for the prior's measured over-call (42 -> 33)."""
    human, _ = planted
    name, members = next(iter(human.items()))
    unit = {
        "unit_id": name,
        "name": name,
        "members": members,
        "author_prior": author.HUMAN,
        "author_adjudicated": author.HARNESS,
    }
    result = ranking.rank([unit])
    assert not result["opportunities"]
    assert result["waste_findings"][0]["unit_id"] == name


def test_mixed_is_admitted_alongside_human(planted):
    human, _ = planted
    name, members = next(iter(human.items()))
    unit = {"unit_id": name, "name": name, "members": members, "author_adjudicated": author.MIXED}
    result = ranking.rank([unit])
    ranked = {u["unit_id"] for u in result["opportunities"]} | {u["unit_id"] for u in result["honest_no"]}
    assert name in ranked


def test_prior_over_calls_human_on_a_templated_lane_mission(corpus_root: Path):
    """The KNOWN weakness, asserted rather than papered over.

    A harness-launched lane mission that carries real multi-turn engineering
    work reads as human to the deterministic prior. This test documents the
    gap that Gate 2's adjudication tier exists to close; if the prior ever
    starts getting this right on its own, this test fails loudly and the
    adjudication tier should be re-sized.
    """
    from fixtures.synthetic_corpus import synth_id, write_session

    mission = (
        "Lane mission: bring the retry policy in the queue worker in line with the "
        "documented contract, add the regression test, and drive it to green."
    )
    for i in range(6):
        write_session(
            corpus_root,
            "syn-lane",
            synth_id("synlane", i),
            prompts=[mission, "still failing, keep going", "ok verify the tests pass"],
            tools=["read_file", "edit_file", "bash", "bash", "python_check"],
            errors_at=[2],
            recover=True,
            span_s=800,
            started_offset_s=i * 3600,
        )
    disc = discover.enumerate_sessions(corpus_root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    prior = author.cluster_author_prior(records)
    assert prior["author_prior"] == author.HUMAN, (
        "the deterministic prior is documented to over-call human here; if this now "
        "resolves correctly without the adjudication tier, re-run Gate 2"
    )
