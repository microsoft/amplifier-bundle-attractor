"""Scenario 9 — the deck-mode gates, proven GREEN and proven RED.

Deck mode gives up the deterministic renderer's structural guarantee (a
language model authors the whole page) and buys the trust back with gates. A
gate that has never been shown failing is not a gate, so every gate class here
is exercised twice: once over the clean synthetic deck, and once over a
mutation that breaks exactly one property.

No LLM anywhere. The candidate deck is a fixture; the run data is a fixture;
everything in between is stdlib and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from attractor_scout import deck as deck_mod
from attractor_scout import deck_templates as DT

from fixtures import deck_fixture as F


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A completed run's data on disk: ranked.json + demos.json."""
    (tmp_path / "ranked.json").write_text(json.dumps(F.deck_ranked_fixture()), encoding="utf-8")
    (tmp_path / "demos.json").write_text(json.dumps(F.deck_demos_fixture()), encoding="utf-8")
    return tmp_path


def _verify(run_dir: Path, html: str, *, name: str = "deck.html") -> deck_mod.DeckReport:
    candidate = run_dir / name
    candidate.write_text(html, encoding="utf-8")
    return deck_mod.verify_deck(
        deck_path=candidate,
        ranked_path=run_dir / "ranked.json",
        demos_path=run_dir / "demos.json",
    )


def _gate(report: deck_mod.DeckReport, letter: str) -> deck_mod.GateResult:
    for gate in report.gates:
        if gate.letter == letter:
            return gate
    raise AssertionError(f"no gate {letter!r} in the report")


# --------------------------------------------------------------------- GREEN
def test_clean_deck_passes_every_gate(run_dir: Path):
    """★ The green baseline: a minimal clean deck clears all six gates."""
    report = _verify(run_dir, F.clean_deck())
    failing = [f"{g.letter}: {g.findings}" for g in report.gates if not g.passed]
    assert report.ok, f"the clean fixture deck must pass every gate; failures: {failing}"
    assert [g.letter for g in report.gates] == ["a", "b", "c", "d", "e", "f"]


def test_clean_deck_report_reports_real_numbers(run_dir: Path):
    report = _verify(run_dir, F.clean_deck())
    assert "2 https href(s)" in _gate(report, "b").detail
    assert "1 dialog(s), 1 trigger(s), 1 paired" in _gate(report, "c").detail
    assert "declared derivation" in _gate(report, "d").detail
    assert "1 of 1 demonstration diagram(s) matched" in _gate(report, "e").detail


def test_verdict_line_says_pass(run_dir: Path):
    report = _verify(run_dir, F.clean_deck())
    assert "verdict: PASS" in report.render()


# ----------------------------------------------------------- (a) it parses
def test_red_unclosed_container_fails_gate_a(run_dir: Path):
    report = _verify(run_dir, F.broken_nesting())
    assert not report.ok
    gate = _gate(report, "a")
    assert not gate.passed
    assert any("section" in f for f in gate.findings), gate.findings


def test_red_missing_doctype_fails_gate_a(run_dir: Path):
    report = _verify(run_dir, F.clean_deck().replace("<!doctype html>\n", "", 1))
    assert not _gate(report, "a").passed


# ------------------------------------------------- (b) it is self-contained
def test_red_external_image_fails_gate_b(run_dir: Path):
    report = _verify(run_dir, F.with_external_resource())
    assert not report.ok
    gate = _gate(report, "b")
    assert not gate.passed
    assert any("<img>" in f for f in gate.findings), gate.findings


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        ('<link rel="stylesheet" href="deck.css">', "<link>"),
        ('<script src="deck.js"></script>', "src attribute"),
        ("<style>@import url(other.css);</style>", "@import"),
        ('<iframe src="x.html"></iframe>', "<iframe>"),
        ('<source srcset="a.png 1x">', "srcset"),
    ],
)
def test_red_each_external_resource_class_fails_gate_b(run_dir: Path, mutation: str, needle: str):
    html = F.clean_deck().replace("</head>", f"{mutation}\n</head>", 1)
    gate = _gate(_verify(run_dir, html), "b")
    assert not gate.passed
    assert any(needle in f for f in gate.findings), (needle, gate.findings)


def test_red_non_local_url_reference_fails_gate_b(run_dir: Path):
    html = F.clean_deck().replace("--bg:#0b0e13;", "--bg:url(https://example.invalid/bg.png);", 1)
    gate = _gate(_verify(run_dir, html), "b")
    assert not gate.passed
    assert any("url(" in f for f in gate.findings)


def test_red_third_https_link_fails_gate_b(run_dir: Path):
    html = F.clean_deck().replace(
        "</footer>",
        '<p><a href="https://example.invalid/more">more</a></p></footer>',
        1,
    )
    gate = _gate(_verify(run_dir, html), "b")
    assert not gate.passed
    assert f"exactly {DT.ALLOWED_HTTPS_HREFS} are permitted" in " ".join(gate.findings)


def test_red_file_url_fails_gate_b(run_dir: Path):
    html = F.clean_deck().replace("The bundle:", 'The bundle: <a href="file:///tmp/x">local</a>', 1)
    gate = _gate(_verify(run_dir, html), "b")
    assert not gate.passed
    assert any("file://" in f for f in gate.findings)


# ------------------------------------------- (c) dialogs and their triggers
def test_red_orphan_dialog_fails_gate_c(run_dir: Path):
    report = _verify(run_dir, F.with_orphan_dialog())
    assert not report.ok
    gate = _gate(report, "c")
    assert not gate.passed
    assert any("m-orphan" in f for f in gate.findings), gate.findings


def test_red_trigger_pointing_at_nothing_fails_gate_c(run_dir: Path):
    html = F.clean_deck().replace('data-modal="m-unit"', 'data-modal="m-missing"', 1)
    gate = _gate(_verify(run_dir, html), "c")
    assert not gate.passed
    assert any("m-missing" in f for f in gate.findings)


def test_two_triggers_for_one_dialog_is_legal(run_dir: Path):
    """Not a strict bijection: a dialog may be opened from more than one place.

    The recovered exemplar does exactly this (two entry points into one
    deep-dive). What is forbidden is an ORPHAN in either direction.
    """
    html = F.clean_deck().replace(
        '</section>\n\n<section id="demos">',
        '<p class="hot" role="button" tabindex="0" data-modal="m-unit">Same detail, second door.</p>'
        '</section>\n\n<section id="demos">',
        1,
    )
    gate = _gate(_verify(run_dir, html), "c")
    assert gate.passed, gate.findings
    assert "2 trigger(s)" in gate.detail


# -------------------------------------------------- (d) every number resolves
def test_red_fabricated_number_fails_gate_d(run_dir: Path):
    """★ The load-bearing red-proof: a figure the run never produced."""
    report = _verify(run_dir, F.with_fabricated_number())
    assert not report.ok
    gate = _gate(report, "d")
    assert not gate.passed
    assert any("4812" in f or "4,812" in f for f in gate.findings), gate.findings


def test_red_undeclared_derivation_fails_gate_d(run_dir: Path):
    """Real arithmetic, correct answer, never declared — still red."""
    report = _verify(run_dir, F.without_derived_block())
    gate = _gate(report, "d")
    assert not gate.passed
    assert any("'18'" in f for f in gate.findings), gate.findings


def test_red_derivation_without_provenance_fails_gate_d(run_dir: Path):
    """Declared, but with no arithmetic provenance — indistinguishable from invention."""
    report = _verify(run_dir, F.with_unprovenanced_derivation())
    gate = _gate(report, "d")
    assert not gate.passed
    assert any("provenance" in f for f in gate.findings), gate.findings


def test_red_derivation_citing_an_unsupplied_input_fails_gate_d(run_dir: Path):
    html = F.clean_deck().replace('"inputs": ["7", "11"]', '"inputs": ["7", "9999"]', 1)
    gate = _gate(_verify(run_dir, html), "d")
    assert not gate.passed
    assert any("9999" in f for f in gate.findings), gate.findings


def test_malformed_derived_block_fails_gate_d(run_dir: Path):
    html = F.clean_deck().replace('[\n  {"value": "18",', "[ not json {", 1)
    gate = _gate(_verify(run_dir, html), "d")
    assert not gate.passed


def test_supplied_stats_are_stateable_without_declaration(run_dir: Path):
    """A verified stat may be printed in digits with no ceremony at all."""
    html = F.clean_deck().replace(
        "<h1>Your work, mapped.</h1>",
        "<h1>Your work, mapped.</h1><p>7 sessions, 12 median tool calls, 930 seconds.</p>",
        1,
    )
    gate = _gate(_verify(run_dir, html), "d")
    assert gate.passed, gate.findings


def test_pipeline_parameters_are_stateable(run_dir: Path):
    """Numbers written inside the generated .dot are supplied data, not invention."""
    html = F.clean_deck().replace(
        "<h1>Your work, mapped.</h1>",
        "<h1>Your work, mapped.</h1><p>The wall stops after 3 attempts.</p>",
        1,
    )
    assert _gate(_verify(run_dir, html), "d").passed


def test_named_limit_spelled_out_numbers_are_not_caught(run_dir: Path):
    """NAMED LIMIT — documented in `deck.py`, not closed.

    A written numeral is not a numeric token and passes unchecked. Detecting
    written numerals is NLP guesswork; a fail-loud guard that guessed wrong
    would block honest prose. If this test ever starts failing because someone
    closed the hole, the docstring in `deck.py` must be updated to match.
    """
    html = F.clean_deck().replace(
        "<h1>Your work, mapped.</h1>",
        "<h1>Your work, mapped.</h1><p>Four thousand eight hundred and twelve units were examined.</p>",
        1,
    )
    assert _gate(_verify(run_dir, html), "d").passed


def test_named_limit_decomposition_rides_along(run_dir: Path):
    """NAMED LIMIT — a supplied decimal's own sub-runs pass with it.

    `0.33` whitelists the bare run `33`, so "33 percent" passes. A neighbouring
    value the data does not contain is still rejected, so the leak is bounded
    to runs a supplied number literally contains.
    """
    clean = F.clean_deck()
    ok = clean.replace("<h1>Your work, mapped.</h1>", "<h1>Your work, mapped.</h1><p>About 33 percent.</p>", 1)
    assert _gate(_verify(run_dir, ok), "d").passed
    bad = clean.replace("<h1>Your work, mapped.</h1>", "<h1>Your work, mapped.</h1><p>About 37 percent.</p>", 1)
    assert not _gate(_verify(run_dir, bad), "d").passed


def test_scope_boundary_attribute_numbers_are_not_scanned(run_dir: Path):
    """SCOPE BOUNDARY — SVG geometry and id references are not visible claims.

    Stated so it cannot quietly widen: gate (d) reads text nodes plus the meta
    description. Coordinates are numbers by the thousand and whitelisting them
    would gut the gate.
    """
    html = F.clean_deck().replace('viewBox="0 0 900 200"', 'viewBox="0 0 1234 5678"', 1)
    assert _gate(_verify(run_dir, html), "d").passed


def test_meta_description_is_scanned(run_dir: Path):
    """The page's own summary sentence is a visible claim, and is checked."""
    html = F.clean_deck().replace(
        'content="A synthetic deck-mode fixture:',
        'content="A synthetic deck-mode fixture over 4812 sessions:',
        1,
    )
    assert not _gate(_verify(run_dir, html), "d").passed


# ---------------------------------------------------- (e) diagram fidelity
def test_red_edge_multiset_off_by_one_fails_gate_e(run_dir: Path):
    """★ The corrective back-edge quietly not drawn — the exact failure that matters."""
    report = _verify(run_dir, F.with_edge_dropped())
    assert not report.ok
    gate = _gate(report, "e")
    assert not gate.passed
    joined = " ".join(gate.findings)
    assert "edge multiset differs" in joined
    assert "budget_wall->worker" in joined, gate.findings


def test_red_duplicate_edge_drawn_once_fails_gate_e(run_dir: Path):
    """A multiset, not a set: an edge declared twice must be drawn twice."""
    doubled_dot = F.DECK_DOT.replace(
        "    start -> worker\n",
        "    start -> worker\n    start -> worker\n",
        1,
    )
    demos = F.deck_demos_fixture()
    demos["demos"][0]["dot_text"] = doubled_dot
    (run_dir / "demos.json").write_text(json.dumps(demos), encoding="utf-8")
    gate = _gate(_verify(run_dir, F.clean_deck()), "e")
    assert not gate.passed
    assert "start->worker" in " ".join(gate.findings)


def test_red_invented_node_fails_gate_e(run_dir: Path):
    gate = _gate(_verify(run_dir, F.with_node_added()), "e")
    assert not gate.passed
    assert "tidy_up" in " ".join(gate.findings)


def test_red_no_matching_diagram_fails_gate_e(run_dir: Path):
    html = F.clean_deck().replace(f'data-diagram="{F.DECK_SLUG}"', "", 1).replace('data-node="start"', "", 1)
    gate = _gate(_verify(run_dir, html), "e")
    assert not gate.passed
    assert "could be matched" in " ".join(gate.findings)


def test_node_set_fallback_matches_when_data_diagram_is_absent(run_dir: Path):
    """The recovered exemplar carries no `data-diagram`; the gate still works."""
    html = F.clean_deck().replace(f' data-diagram="{F.DECK_SLUG}"', "", 1)
    gate = _gate(_verify(run_dir, html), "e")
    assert gate.passed, gate.findings


def test_diagram_with_no_demonstration_to_check_against_is_red(tmp_path: Path):
    (tmp_path / "ranked.json").write_text(json.dumps(F.deck_ranked_fixture()), encoding="utf-8")
    candidate = tmp_path / "deck.html"
    candidate.write_text(F.clean_deck(), encoding="utf-8")
    report = deck_mod.verify_deck(deck_path=candidate, ranked_path=tmp_path / "ranked.json", demos_path=None)
    gate = _gate(report, "e")
    assert not gate.passed
    assert "no demonstration bundle was supplied" in " ".join(gate.findings)


# ------------------------------------------- (f) every modal has the depth
def test_clean_deck_modal_conforms_to_the_structure_contract(run_dir: Path):
    """★ GREEN: the fixture's one modal carries all five mandated parts."""
    gate = _gate(_verify(run_dir, F.clean_deck()), "f")
    assert gate.passed, gate.findings
    assert "1/1 dialog(s) conforming" in gate.detail
    assert "sub-section(s)" in gate.detail and "evidence block(s)" in gate.detail


def test_red_hollow_modal_fails_gate_f(run_dir: Path):
    """★ RED: a modal that is two flat paragraphs and nothing else.

    Everything else about the deck is untouched, so this proves the gate is
    reading modal STRUCTURE and not riding some other gate's failure.
    """
    report = _verify(run_dir, F.with_hollow_modal())
    assert not report.ok
    gate = _gate(report, "f")
    assert not gate.passed
    joined = " ".join(gate.findings)
    assert "m-unit" in joined, gate.findings
    for expected in ("sub-section", "evidence", "why", "entry"):
        assert expected in joined, f"the finding must name the missing {expected!r}: {gate.findings}"
    for other in ("a", "b", "c", "d", "e"):
        assert _gate(report, other).passed, f"gate {other} must be unaffected: {_gate(report, other).findings}"


def test_red_modal_without_evidence_fails_gate_f(run_dir: Path):
    """RED: heading, sub-sections, why and entry --- but never the reader's own data."""
    report = _verify(run_dir, F.with_modal_missing_evidence())
    gate = _gate(report, "f")
    assert not gate.passed
    joined = " ".join(gate.findings)
    assert DT.MODAL_EVIDENCE_CLASS in joined, gate.findings
    assert "sub-section" not in joined, f"only the evidence gap should be named: {gate.findings}"


def test_red_modal_with_one_subsection_fails_gate_f(run_dir: Path):
    """RED: the sub-section minimum is a real threshold, not a presence check."""
    gate = _gate(_verify(run_dir, F.with_modal_one_subsection()), "f")
    assert not gate.passed
    joined = " ".join(gate.findings)
    assert f"1 <{DT.MODAL_SUBSECTION_TAG}> sub-section(s)" in joined, gate.findings
    assert str(DT.MODAL_MIN_SUBSECTIONS) in joined


def test_gate_f_is_structural_not_a_length_check(run_dir: Path):
    """A SHORT modal that has every part passes; a LONG one missing parts fails.

    The whole design intent of gate (f), asserted directly: padding buys
    nothing and brevity costs nothing. Structure is the only currency.
    """
    filler = "<p>The same point, restated at length, again and again.</p>\n" * 30
    padded_hollow = F.with_hollow_modal().replace(
        "<p>It is worth automating.</p>", "<p>It is worth automating.</p>\n" + filler, 1
    )
    assert not _gate(_verify(run_dir, padded_hollow), "f").passed

    trimmed = F.clean_deck().replace(
        "A generated report comes back wrong, you repair it, and you run the check again until the\n"
        "      check stops complaining.",
        "It repeats.",
        1,
    )
    assert len(trimmed) < len(F.clean_deck())
    assert _gate(_verify(run_dir, trimmed), "f").passed


def test_gate_f_class_token_is_matched_exactly(run_dir: Path):
    """`class="whyever"` is not a why-block --- the gate splits, never substrings."""
    html = F.clean_deck().replace('<p class="why">', '<p class="whyever">', 1)
    gate = _gate(_verify(run_dir, html), "f")
    assert not gate.passed
    assert DT.MODAL_WHY_CLASS in " ".join(gate.findings)


def test_gate_f_accepts_a_class_token_beside_others(run_dir: Path):
    """A mandated class may sit alongside styling classes --- the token is what counts."""
    html = F.clean_deck().replace('<div class="evidence">', '<div class="inset evidence wide">', 1)
    assert _gate(_verify(run_dir, html), "f").passed


def test_gate_f_names_an_unnamed_dialog_rather_than_crashing(run_dir: Path):
    """A <dialog> with no id still gets counted and still gets named in the finding."""
    html = F.clean_deck().replace(
        '<script type="application/json" id="derived-values">',
        '<dialog><div class="mbox"><h3>No id at all</h3></div></dialog>\n\n'
        '<script type="application/json" id="derived-values">',
        1,
    )
    gate = _gate(_verify(run_dir, html), "f")
    assert not gate.passed
    assert "(unnamed dialog)" in " ".join(gate.findings), gate.findings


# ------------------------------------------------------------ report shape
def test_failures_name_the_file_and_the_reason(run_dir: Path):
    report = _verify(run_dir, F.with_fabricated_number(), name="candidate-deck.html")
    text = report.render()
    assert "candidate-deck.html" in text
    assert "verdict: FAIL" in text
    assert "[FAIL] d" in text
    assert "[PASS] a" in text


def test_report_round_trips_as_json(run_dir: Path):
    report = _verify(run_dir, F.clean_deck())
    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["ok"] is True
    assert [g["letter"] for g in payload["gates"]] == ["a", "b", "c", "d", "e", "f"]


# ===================================================================
# HARDENING (adversarial review MERGE-AFTER-FIX) --- RED + PASS proofs
# ===================================================================


# ---- FIX 1: derived-values self-dealing (inputs now mandatory) ------------
def test_red_inputless_derivation_fails_gate_d(run_dir: Path):
    """The reviewer's exact probe: {"value":"8731","from":"qqq"} with no inputs.

    This used to PASS (inputs were optional, only a non-empty `from` was
    required). It must now be RED --- a value cannot be conjured from nothing.
    """
    report = _verify(run_dir, F.with_inputless_derivation())
    assert not report.ok
    gate = _gate(report, "d")
    assert not gate.passed
    assert any("no 'inputs'" in f for f in gate.findings), gate.findings


def test_red_empty_inputs_derivation_fails_gate_d(run_dir: Path):
    gate = _gate(_verify(run_dir, F.with_empty_inputs_derivation()), "d")
    assert not gate.passed
    assert any("inputs" in f for f in gate.findings), gate.findings


def test_red_from_not_referencing_inputs_fails_gate_d(run_dir: Path):
    """A `from` that lists inputs but references none of them is junk provenance."""
    gate = _gate(_verify(run_dir, F.with_from_not_referencing_inputs()), "d")
    assert not gate.passed
    assert any("does not reference" in f for f in gate.findings), gate.findings


def test_named_limit_wrong_total_with_real_inputs_passes_gate_d(run_dir: Path):
    """NAMED LIMIT (expected PASS) --- P5/P6 kept: the gate does not recompute.

    A declaration with real, supplied inputs and a `from` that references them
    passes even when the stated total is wrong (7 + 11 != 900). Recomputing is
    the residual; declaring real inputs is what the hardening forces. If this
    ever fails because someone added arithmetic evaluation, deck.py's named
    limit and design doc must be updated to match.
    """
    gate = _gate(_verify(run_dir, F.with_wrong_total_but_real_inputs()), "d")
    assert gate.passed, gate.findings


# ---- FIX 2: gate (b) blocklist holes --------------------------------------
@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (F.with_object_data, "<object>"),
        (F.with_xlink_href, "xlink:href"),
        (F.with_media_source, "<source>"),
        (F.with_embed, "<embed>"),
        (F.with_track, "<track>"),
        (F.with_use_href, "xlink:href / <use> href"),
    ],
)
def test_red_each_gate_b_hole_now_fails(run_dir: Path, mutation, needle):
    """Every external-resource construct the review found publishing clean is RED."""
    report = _verify(run_dir, mutation())
    assert not report.ok, f"{mutation.__name__} should fail gate b"
    gate = _gate(report, "b")
    assert not gate.passed
    assert any(needle in f for f in gate.findings), (needle, gate.findings)


def test_gate_b_detail_line_is_truthful_when_violated(run_dir: Path):
    """The detail line must report the real external-resource count, not a hardcoded 0."""
    gate = _gate(_verify(run_dir, F.with_object_data()), "b")
    assert "0 external-resource" not in gate.detail
    assert "external-resource element(s)/attribute(s)" in gate.detail


def test_local_use_href_is_legal(run_dir: Path):
    """A same-document <use href="#local"> reference is not external --- PASS."""
    gate = _gate(_verify(run_dir, F.with_local_use_href()), "b")
    assert gate.passed, gate.findings


# ---- FIX 3: gate (d) blind to script-injected text ------------------------
def test_red_script_injected_number_fails_gate_d(run_dir: Path):
    """document.title = "8731 units examined" --- injected via JS, must be caught."""
    report = _verify(run_dir, F.with_script_injected_number())
    assert not report.ok
    gate = _gate(report, "d")
    assert not gate.passed
    assert any("8731" in f and "script" in f for f in gate.findings), gate.findings


def test_named_limit_script_geometry_string_passes_gate_d(run_dir: Path):
    """NAMED LIMIT (expected PASS) --- a CSS/geometry string literal with digits but
    no prose word (the exemplar's own IntersectionObserver rootMargin) is not a
    displayed claim and is not scanned. Scanning every literal would false-positive
    here; if this ever fails, the named limit in deck.py must be updated."""
    gate = _gate(_verify(run_dir, F.with_script_geometry_string()), "d")
    assert gate.passed, gate.findings


def test_named_limit_script_concatenated_number_passes_gate_d(run_dir: Path):
    """NAMED LIMIT (expected PASS) --- a number built from a bare numeric LITERAL by
    concatenation ("" + 8731 + " units") is not inside a string literal and is
    not seen. The computed/concatenated residual, kept on purpose."""
    gate = _gate(_verify(run_dir, F.with_script_concatenated_number()), "d")
    assert gate.passed, gate.findings


def test_clean_deck_detail_counts_the_script_body(run_dir: Path):
    """The gate-d detail line names how many inline script bodies were scanned."""
    gate = _gate(_verify(run_dir, F.clean_deck()), "d")
    assert "inline script body(ies)" in gate.detail
