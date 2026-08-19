"""SCENARIO 2 (Gate 0 family) — cross-workspace recovery vs the size-ranked trap.

The E3 selector is discovery-spine correctness: it feeds every downstream
signal, so it is greened with Gate 0 rather than after it.

Pass thresholds, verbatim from the scenario:
  * A/B held identical except selection strategy.
    control (size-ranked top-N):     freq(U) == 0   -> U absent from output
    treatment (prompt-carrying):     freq(U) == 135 exactly
  * Jointly asserted on the treatment count:
    freq(U) == 135 AND != 300 (raw occurrence lines)
                    AND != 175 (unfolded child sessions)
                    AND != 134 (8-char prefix collision collapse)
  * Statistical-N: 5 independent re-seeds, require 5/5.

Why the scale is real here: the synthetic plants the FULL 135/66/40/300
structure, not a token version of it, because the four failure numbers only
separate at that scale.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attractor_scout import discover, extract, frequency_signature

from fixtures.synthetic_corpus import UNIT_MARKER, build_frequency_corpus

N_RESEEDS = 5


def _mine(root: Path, *, selector: str, top_n: int | None = None) -> list[dict]:
    disc = discover.enumerate_sessions(root)
    refs = discover.qualify(disc, selector=selector, top_n_workspaces=top_n)
    return extract.extract_corpus(disc, refs)


def _unit_members(records: list[dict]) -> list[dict]:
    """Records belonging to planted unit U (stands in for the semantic pass)."""
    return [r for r in records if any(UNIT_MARKER in str(p) for p in (r.get("prompts") or []))]


def _raw_grep_occurrences(root: Path) -> int:
    """The dedup-failure baseline: count occurrence LINES, not sessions."""
    total = 0
    for path in root.rglob("context-intelligence/events.jsonl"):
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            total += sum(1 for line in fh if UNIT_MARKER in line)
    return total


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("freq") / "projects"
    truth = build_frequency_corpus(root, seed=0)
    return root, truth


def test_treatment_recovers_the_true_distinct_reach(built):
    root, truth = built
    members = _unit_members(_mine(root, selector="prompt-carrying"))
    assert frequency_signature.count_distinct_sessions(members) == truth.expected["distinct_roots"] == 135


def test_control_size_ranked_selection_drops_the_unit_below_the_floor(built):
    """The A/B: identical corpus, only the selection strategy varies."""
    root, _ = built
    members = _unit_members(_mine(root, selector="size-ranked", top_n=2))
    n = frequency_signature.count_distinct_sessions(members)
    assert n == 0
    assert not frequency_signature.passes_floor(n), "U must be absent from the control's output entirely"


def test_count_is_not_the_raw_occurrence_line_count(built):
    root, truth = built
    members = _unit_members(_mine(root, selector="prompt-carrying"))
    n = frequency_signature.count_distinct_sessions(members)
    assert _raw_grep_occurrences(root) == truth.expected["occurrence_lines"] == 300
    assert n != 300


def test_count_is_not_the_unfolded_child_session_count(built):
    """135 + 40 children = 175. Children FOLD into their root, never add."""
    root, truth = built
    disc = discover.enumerate_sessions(root)
    # Roots AND children, so the fold has something to fold.
    all_refs = list(disc.sessions)

    unfolded = extract.extract_all(all_refs, fold_children=False)
    folded = extract.extract_all(all_refs, fold_children=True)

    n_unfolded = frequency_signature.count_distinct_sessions(_unit_members(unfolded))
    n_folded = frequency_signature.count_distinct_sessions(_unit_members(folded))

    assert n_unfolded == truth.expected["unfolded_sessions"] == 175, "control: unfolded count"
    assert n_folded == 135
    assert n_folded != 175


def test_count_is_not_the_eight_char_prefix_collapse(built):
    """One planted pair shares 8 chars; keying on the prefix loses a session."""
    root, truth = built
    members = _unit_members(_mine(root, selector="prompt-carrying"))
    full_ids = {str(m["session_id"]) for m in members}
    prefixes = {sid[:8] for sid in full_ids}
    assert len(prefixes) == truth.expected["distinct_8char_prefixes"] == 134
    assert len(full_ids) == 135
    assert len(full_ids) != len(prefixes)


def test_statistical_n_five_independent_reseeds(tmp_path_factory):
    """5/5 pass rate, reported — not a single green run."""
    passes = 0
    for seed in range(N_RESEEDS):
        root = tmp_path_factory.mktemp(f"freq-seed{seed}") / "projects"
        build_frequency_corpus(root, seed=seed, decoy_size=120)
        treatment = frequency_signature.count_distinct_sessions(_unit_members(_mine(root, selector="prompt-carrying")))
        control = frequency_signature.count_distinct_sessions(
            _unit_members(_mine(root, selector="size-ranked", top_n=2))
        )
        if treatment == 135 and control == 0:
            passes += 1
    assert passes == N_RESEEDS, f"pass rate {passes}/{N_RESEEDS}"


def test_e1_prompts_are_found_despite_event_key_coming_last(built):
    """Regression guard for the 72% undercount (head-matching)."""
    root, _ = built
    disc = discover.enumerate_sessions(root)
    qualified = discover.qualify(disc, selector="prompt-carrying")
    # Every planted session carries exactly one FORMAT-B prompt line.
    assert len(qualified) == len(disc.roots)


def test_e2_a_one_megabyte_config_line_does_not_hide_the_prompt(built):
    """Regression guard for the byte-budgeted read (28x undercount)."""
    root, truth = built
    disc = discover.enumerate_sessions(root)
    # Every 5th planted unit session carries a ~1 MB session:config BEFORE
    # its prompt. All of them must still qualify.
    big = [
        s
        for s in disc.roots
        if s.session_id in truth.expected["unit_session_ids"] and s.events_path.stat().st_size > 1_000_000
    ]
    assert big, "fixture did not plant any oversized-config sessions"
    assert all(discover.carries_prompt(s.events_path) for s in big)
