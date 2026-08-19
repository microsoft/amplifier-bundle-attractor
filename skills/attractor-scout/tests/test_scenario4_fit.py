"""SCENARIO 4 — structural CYCLE recall + GATE precision. DETERMINISTIC HALVES.

N is PRE-REGISTERED at 200 planted sessions per arm, and every threshold
below was written before the detector was run against them.

  4a structural CYCLE (synthetic-planted)
    (1) treatment recall on implicit-loop-no-marker fixtures  >= 0.95
    (2) treatment false-positive on strictly-linear fixtures  == 0.00 (0/N)
    (3) CONTROL (explicit/lexical only) recall on the same    <= 0.05
        -- this is what proves structural detection is NECESSARY, not just
           nicer: the control has to actually fail.
  4b GATE (synthetic-planted)
    (6) recall on planted gated fixtures                      >= 0.95
    (7) false-positive on planted ungated/linear fixtures     == 0.00 (0/N)

The real-corpus known-answer arms (explicit 3.2% +/- 0.5pp; structural
>= 53.0% overall and >= 99.0% among >=6-tool; implicit:explicit >= 15:1;
terminal-check prevalence 16.7% +/- 2.0pp / 30.2% +/- 3.0pp) read the
maintainer's own corpus, which is never in this repo -- they run from
`evals/scenario4_fit_real.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attractor_scout import discover, extract, fit_cycle, fit_gate

from fixtures.synthetic_corpus import build_fit_corpus

N_PER_ARM = 200
CYCLE_RECALL_FLOOR = 0.95
CYCLE_FP_CEILING = 0.0
CONTROL_RECALL_CEILING = 0.05
GATE_RECALL_FLOOR = 0.95
GATE_FP_CEILING = 0.0


@pytest.fixture(scope="module")
def arms(tmp_path_factory) -> dict[str, list[dict]]:
    root: Path = tmp_path_factory.mktemp("fit") / "projects"
    truth = build_fit_corpus(root, n_per_arm=N_PER_ARM)
    disc = discover.enumerate_sessions(root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    by_id = {r["session_id"]: r for r in records}
    out = {arm: [by_id[sid] for sid in sids if sid in by_id] for arm, sids in truth.expected["arms"].items()}
    for arm, recs in out.items():
        assert len(recs) == N_PER_ARM, f"arm {arm}: {len(recs)} != pre-registered {N_PER_ARM}"
    return out


# ------------------------------------------------------------------- 4a (1)
def test_structural_detector_recalls_implicit_loops(arms):
    hits = sum(1 for r in arms["implicit_loop"] if fit_cycle.detect(r).cycle)
    recall = hits / N_PER_ARM
    assert recall >= CYCLE_RECALL_FLOOR, f"recall {recall:.3f} < {CYCLE_RECALL_FLOOR}"


def test_implicit_loop_fixtures_really_carry_no_explicit_marker(arms):
    """Guard: the fixture must not accidentally hand the control a free win."""
    for rec in arms["implicit_loop"]:
        assert rec.get("n_explicit_loop_events", 0) == 0
        assert rec.get("loop_markers", 0) == 0


# ------------------------------------------------------------------- 4a (2)
def test_structural_detector_never_fires_on_linear_sessions(arms):
    fps = sum(1 for r in arms["linear"] if fit_cycle.detect(r).cycle)
    assert fps / N_PER_ARM == CYCLE_FP_CEILING, f"{fps}/{N_PER_ARM} false positives"


# ------------------------------------------------------------------- 4a (3)
def test_control_lexical_detector_misses_almost_every_real_loop(arms):
    """The control MUST fail. If it passes, structural detection is unproven."""
    hits = sum(1 for r in arms["implicit_loop"] if fit_cycle.detect_explicit_only(r).cycle)
    recall = hits / N_PER_ARM
    assert recall <= CONTROL_RECALL_CEILING, f"control recall {recall:.3f} > {CONTROL_RECALL_CEILING}"


def test_treatment_beats_control_by_the_expected_order_of_magnitude(arms):
    treatment = sum(1 for r in arms["implicit_loop"] if fit_cycle.detect(r).cycle)
    control = sum(1 for r in arms["implicit_loop"] if fit_cycle.detect_explicit_only(r).cycle)
    assert treatment > control * 15 or control == 0


# ------------------------------------------------------------------- 4b (6)
def test_gate_recalls_planted_terminal_verification(arms):
    hits = sum(1 for r in arms["gated"] if fit_gate.detect(r).gate)
    recall = hits / N_PER_ARM
    assert recall >= GATE_RECALL_FLOOR, f"gate recall {recall:.3f} < {GATE_RECALL_FLOOR}"


# ------------------------------------------------------------------- 4b (7)
def test_gate_never_fires_on_ungated_sessions(arms):
    fps = sum(1 for r in arms["ungated"] if fit_gate.detect(r).gate)
    assert fps / N_PER_ARM == GATE_FP_CEILING, f"{fps}/{N_PER_ARM} false gates"


def test_gate_never_fires_on_linear_never_verified_sessions(arms):
    fps = sum(1 for r in arms["linear"] if fit_gate.detect(r).gate)
    assert fps == 0


# ---------------------------------------------------------------- mechanics
def test_windowed_repeat_requires_the_repeats_to_be_close_together():
    """Three uses across a hundred calls is not a loop; three in six is."""
    # Filler is all-distinct so the ONLY candidate repeat is "a".
    filler_a = [f"t{i}" for i in range(20)]
    filler_b = [f"u{i}" for i in range(20)]
    spread = ["a"] + filler_a + ["a"] + filler_b + ["a"]
    tight = ["a", "b", "a", "c", "a", "d"]
    assert fit_cycle.windowed_repeat(spread) is None
    assert fit_cycle.windowed_repeat(tight) == "a"


def test_error_retry_alone_is_sufficient_cycle_evidence():
    rec = {"tool_all": ["bash", "read_file"], "n_err_recover": 2}
    assert fit_cycle.detect(rec).cycle is True


def test_a_record_without_a_tool_stream_inherits_rather_than_denies():
    """An absent observation is not evidence of linearity."""
    legacy = {"implicit_loop": True, "n_tool_calls": 40}
    assert fit_cycle.detect(legacy).cycle is True
    assert "inherited" in fit_cycle.detect(legacy).evidence


def test_gate_config_is_swappable_for_gap1_finalization():
    cfg = fit_gate.GateConfig(verify_tools=frozenset({"syn_verifier"}), provisional=False)
    rec = {"tool_all": ["edit_file", "syn_verifier"], "status": "completed"}
    assert fit_gate.detect(rec, config=cfg).gate is True
    assert fit_gate.detect(rec).gate is False


def test_bash_verb_assist_is_bounded_to_the_terminal_window():
    rec = {
        "tool_all": ["bash", "bash"],
        "bash_cmds": ["pytest -q"],
        "status": "completed",
    }
    assert fit_gate.detect(rec).gate is True


def test_fit_is_an_and_across_all_three_subtests(arms):
    """A gated-but-linear unit still fails Fit — the AND actually collapses."""
    from attractor_scout import fit_recovery, honest_no

    linear_gated = dict(arms["linear"][0])
    linear_gated["tool_all"] = ["read_file", "grep", "python_check"]
    verdict = honest_no.classify(
        cycle=fit_cycle.detect(linear_gated).cycle,
        gate=fit_gate.detect(linear_gated).gate,
        recovery=fit_recovery.detect([linear_gated]).verdict,
    )
    assert verdict.fit == 0
    assert verdict.failed_subtest == "4a"
