"""SCENARIO 3 — leverage calibration. DETERMINISTIC HALVES (Arm ii + invariants).

Arms 0, i and iii are stated against the REAL corpus (own data, never in this
repo), so they live in `evals/scenario3_leverage_real.py` and are run at build
time against the calibration extract. What lives HERE is everything that can
be proven portably, by construction:

  * Arm ii  — the span cap. `capped span-term == 120 exactly` is a HARD
    INVARIANT, and cap-OFF on the SAME fixture must blow the ratio past 100x.
  * The `n_prompts` DROP as a structural property of the shipped formula.
  * median-not-p75 as a structural property.

Arm ii's fixture is the 90-day abandoned open session — the one that, with
the cap off, becomes the highest-toil unit in the corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attractor_scout import discover, extract, leverage

from fixtures.synthetic_corpus import build_span_cap_corpus

SPAN_CAP_S = 7_200.0
CAPPED_SPAN_TERM = 120.0


@pytest.fixture
def span_records(corpus_root: Path) -> list[dict]:
    build_span_cap_corpus(corpus_root)
    disc = discover.enumerate_sessions(corpus_root)
    refs = discover.qualify(disc)
    return extract.extract_corpus(disc, refs)


# ------------------------------------------------------------------- Arm ii
def test_capped_span_term_is_exactly_120(span_records):
    """7200/60 == 120. The hard invariant."""
    assert leverage.span_term(span_records, cap_span=True) == pytest.approx(CAPPED_SPAN_TERM)


def test_capped_span_term_can_never_exceed_120(span_records):
    assert leverage.span_term(span_records, cap_span=True) <= CAPPED_SPAN_TERM


def test_uncapped_span_makes_an_abandoned_session_dominate(span_records):
    """Cap OFF on the same fixture: >=1,000,000 s median and >=100x leverage."""
    uncapped_median = leverage.median([float(r.get("span_s") or 0.0) for r in span_records])
    assert uncapped_median >= 1_000_000, uncapped_median

    capped = leverage.compute_leverage(span_records, cap_span=True).leverage
    uncapped = leverage.compute_leverage(span_records, cap_span=False).leverage
    assert uncapped / capped >= 100, f"uncapped/capped = {uncapped / capped:.1f}"


def test_span_cap_barely_moves_a_normal_cluster(corpus_root: Path):
    """<=5% shift vs a no-plant baseline: the cap targets abandonment only."""
    from fixtures.synthetic_corpus import synth_id, write_session

    for i in range(5):
        write_session(
            corpus_root,
            "syn-normal",
            synth_id("synnrm", i),
            prompts=["ordinary work"],
            tools=["bash", "bash", "bash"],
            span_s=300 + i * 100,
            started_offset_s=i * 3600,
        )
    disc = discover.enumerate_sessions(corpus_root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    capped = leverage.compute_leverage(records, cap_span=True).leverage
    uncapped = leverage.compute_leverage(records, cap_span=False).leverage
    assert abs(uncapped - capped) / capped <= 0.05


# --------------------------------------------------------------- structural
def test_n_prompts_is_not_in_the_shipped_leverage_formula():
    """Arm i, structurally: the score cannot move when only n_prompts moves."""
    cheap = [{"n_tool_calls": 10, "n_llm_cycles": 5, "span_capped_s": 60, "n_tool_errors": 0, "n_prompts": 1}] * 4
    same_but_chatty = [
        {"n_tool_calls": 10, "n_llm_cycles": 5, "span_capped_s": 60, "n_tool_errors": 0, "n_prompts": 40}
    ] * 4
    assert leverage.compute_leverage(cheap).leverage == leverage.compute_leverage(same_but_chatty).leverage


def test_including_n_prompts_would_change_the_answer(span_records):
    """The ablation arm is real: with n_prompts in, the number moves."""
    base = leverage.compute_leverage(span_records).leverage
    ablated = leverage.compute_leverage(span_records, include_n_prompts=True).leverage
    assert ablated >= base


def test_leverage_formula_matches_the_calibrated_combination():
    members = [
        {"n_tool_calls": 72, "n_llm_cycles": 64, "span_capped_s": 1889.0, "n_tool_errors": 3},
        {"n_tool_calls": 72, "n_llm_cycles": 64, "span_capped_s": 1889.0, "n_tool_errors": 3},
    ]
    prof = leverage.compute_leverage(members)
    expected = 72 + 64 + (1889.0 / 60.0) + 2 * (6 / 2)
    assert prof.leverage == pytest.approx(expected)


def test_median_absorbs_an_outlier_that_p75_amplifies():
    """Arm iii, structurally: one outlier session must not speak for a cluster."""
    members = [
        {"n_tool_calls": 5, "n_llm_cycles": 5, "span_capped_s": 60, "n_tool_errors": 0},
        {"n_tool_calls": 5, "n_llm_cycles": 5, "span_capped_s": 60, "n_tool_errors": 0},
        {"n_tool_calls": 5, "n_llm_cycles": 5, "span_capped_s": 60, "n_tool_errors": 0},
        {"n_tool_calls": 500, "n_llm_cycles": 500, "span_capped_s": 7200, "n_tool_errors": 0},
    ]
    med = leverage.compute_leverage(members, aggregate="median").leverage
    p75 = leverage.compute_leverage(members, aggregate="p75").leverage
    assert med < p75
    assert med == pytest.approx(5 + 5 + 1.0)


def test_errors_are_read_per_session_and_doubled():
    quiet = [{"n_tool_calls": 4, "n_llm_cycles": 4, "span_capped_s": 0, "n_tool_errors": 0}] * 2
    erroring = [{"n_tool_calls": 4, "n_llm_cycles": 4, "span_capped_s": 0, "n_tool_errors": 3}] * 2
    delta = leverage.compute_leverage(erroring).leverage - leverage.compute_leverage(quiet).leverage
    assert delta == pytest.approx(2 * 3.0)


def test_separation_reports_infinity_instead_of_dividing_by_zero():
    high = [{"n_tool_calls": 50}] * 3
    low = [{"n_tool_calls": 0}] * 3
    assert leverage.separation(high, low, "tool_calls") == float("inf")
    assert leverage.separation(low, low, "tool_calls") == 0.0
