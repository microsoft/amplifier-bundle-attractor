"""Scenario 8 — the renderer stays deterministic, additive, and honest.

Three properties, each machine-checked here:

**Additive.** With no demos, the artifact is BYTE-IDENTICAL to what the
mining-only renderer produced. Every demonstration byte — CSS included — is
inside a conditional, so the frozen half cannot be disturbed by the new half.

**Deterministic.** Same inputs plus a pinned timestamp produce the same bytes.
Generation is stochastic; verification, assembly and rendering are not.

**Honest.** The new invariant, sibling of "UNKNOWN never renders as FAIL":
an UNVERIFIED demo is NEVER rendered as verified. If the linter did not run,
the artifact says so in those words rather than leaving a silence a reader
would read as a pass.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from attractor_scout import demo, render
from attractor_scout import demo_templates as T

from fixtures import demo_fixture as F

PINNED = "2020-01-01T00:00:00+00:00"

#: Substrings that exist ONLY because a demo was rendered.
DEMO_MARKERS = (
    "primer-section",
    "demo-heading",
    "attractor-explained.html",
    T.SEC_PANEL,
    T.MATH_LABEL,
    "demonstrated",
)


def _build(tmp_path: Path, **ranked_kwargs) -> tuple[dict, dict, Path]:
    """Ranked + a fully assembled single-demo demos.json, published on disk."""
    ranked = F.ranked_fixture(**ranked_kwargs)
    ranked_path = tmp_path / "ranked.json"
    ranked_path.write_text(json.dumps(ranked), encoding="utf-8")
    slug, _ = demo.build_brief(ranked_path=ranked_path, unit_id=None, workdir=tmp_path / "demo")
    F.write_draft(tmp_path / "demo" / slug)
    out_dir = tmp_path / "out"
    entry = demo.assemble_demo(
        ranked_path=ranked_path,
        unit_id=None,
        workdir=tmp_path / "demo" / slug,
        output_dir=out_dir,
        generated_at=PINNED,
    )
    demos = demo.write_demos(entry, tmp_path / "demos.json", append=False)
    return ranked, demos, out_dir


# ------------------------------------------------------------- additivity
def test_no_demos_means_zero_demo_markers(tmp_path: Path):
    ranked = F.ranked_fixture()
    html = render.render_html(ranked, generated_at=PINNED)
    for marker in DEMO_MARKERS:
        assert marker not in html, f"a demo marker leaked into the no-demos artifact: {marker!r}"
    assert "demo-css" not in html


def test_no_demos_is_byte_identical_to_the_pre_demo_layer_output(tmp_path: Path):
    """Additivity as a hard test: demos=None must change nothing at all."""
    ranked = F.ranked_fixture()
    baseline = render.render_html(ranked, generated_at=PINNED)
    with_none = render.render_html(ranked, generated_at=PINNED, demos=None)
    with_empty_doc = render.render_html(ranked, generated_at=PINNED, demos={})
    assert baseline == with_none == with_empty_doc


def test_rendering_is_deterministic(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    first = render.render_html(ranked, generated_at=PINNED, demos=demos)
    second = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert first == second, "same inputs + pinned timestamp must give byte-identical output"


def test_existing_sections_survive_the_insertion(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    for heading in (
        "A range of what you already do",
        "Ranked opportunities",
        "Honest NOs",
        "Waste findings",
    ):
        assert heading in html
    assert html.index("A range of what you already do") < html.index("primer-section")
    assert html.index("primer-section") < html.index("Ranked opportunities")


# ------------------------------------------------------------------ primer
def test_primer_renders_exactly_once_and_links_the_explainer_exactly_once(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert html.count('id="primer-section"') == 1
    assert html.count(f'href="{T.EXPLAINER_URL}"') == 1, "the explainer is LINKED once, never inlined"
    assert T.PRIMER_TITLE in html
    for heading, _body in T.PRIMER_PARTS:
        assert heading in html


def test_primer_only_artifact_when_there_is_nothing_to_demonstrate(tmp_path: Path):
    ranked = F.ranked_fixture()
    ranked["opportunities"] = []
    doc = demo.empty_demos_doc()
    html = render.render_html(ranked, generated_at=PINNED, demos=doc)
    assert html.count('id="primer-section"') == 1
    assert "demo-heading" not in html
    assert "demonstrated" not in html, "the sub-line must not claim demos that do not exist"


def test_the_header_subline_counts_demonstrations(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert "1 demonstrated" in html


# -------------------------------------------------------------- the demo
def test_demo_section_carries_the_dot_escaped_and_the_walk(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert html.count('class="demo-heading"') == 1
    assert "digraph SyntheticDemo" in html, "the .dot text is embedded, so the artifact survives a file move"
    assert "<digraph" not in html
    assert "&quot;" in html, "the embedded .dot must be HTML-escaped through _esc"
    for step in F.DEMO_NARRATIVE["pipeline_walk"]:
        assert f"<code>{step['node']}</code>" in html


def test_relpaths_in_the_html_match_files_actually_written(tmp_path: Path):
    ranked, demos, out_dir = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    hrefs = set(re.findall(r'href="([^"]+)"', html))
    local = {h for h in hrefs if not h.startswith("http")}
    assert local, "the artifact must print the on-disk paths it wrote"
    for rel in local:
        assert (out_dir / rel).is_file(), f"the HTML claims {rel}, which does not exist on disk"


def test_the_artifact_fetches_nothing(tmp_path: Path):
    """Self-contained: an anchor is a reference, not a fetched resource."""
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert "<link " not in html
    assert "src=" not in html
    assert "@import" not in html
    external = [u for u in re.findall(r'href="(https?://[^"]+)"', html)]
    assert external == [T.EXPLAINER_URL], "the only external reference is the explainer anchor"


def test_convergence_math_is_rendered_with_its_illustrative_label(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert T.MATH_LABEL in html
    assert "0.9" in html
    math = demos["demos"][0]["convergence_math"]
    assert str(math["once_through"]) in html
    assert str(math["gated_loop"]) in html


def test_the_self_certification_panel_has_all_three_parts(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert T.PANEL_PART1_TITLE in html
    assert T.PANEL_PART2_TITLE in html
    assert T.PANEL_PART3_TITLE in html
    assert "Structure lints; judgment does not." in html
    # The commands are escaped through _esc like every other string.
    assert escape(T.CLI_INSTALL_CMD, quote=True) in html
    assert T.AUTHOR_PIPELINE_PATH in html


def test_panel_part2_names_the_dot_prose_surface_the_whitelist_does_not_cover(tmp_path: Path):
    """Finding 1 honesty pin, so the 'every number' claim cannot re-widen.

    The digit whitelist governs the six teaching-prose slots only; numbers
    written inside the generated .dot (budgets, max_iterations, thresholds) are
    gate-checked by lint+doctrine, not whitelisted. The panel that exists to
    say 'what nothing checked' must NAME that surface out loud — in the
    constant AND in the rendered artifact.
    """
    body = T.PANEL_PART2_BODY.lower()
    assert "number" in body, "part 2 must own the un-whitelisted number surface, not just prompts"
    assert "inside the pipeline" in body or ".dot" in body or "pipeline itself" in body, (
        "part 2 must name the .dot as the surface, not leave it implicit"
    )
    assert "not" in body and ("verified stats" in body or "cross-check" in body), (
        "part 2 must say those numbers are NOT cross-checked against the verified stats"
    )
    # And it must actually reach the reader.
    ranked, demos, _ = _build(tmp_path)
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert escape(T.PANEL_PART2_BODY, quote=True) in html


# ------------------------------------------------------------- the labels
def _relabel(demos: dict, verification: dict) -> dict:
    doc = json.loads(json.dumps(demos))
    doc["demos"][0]["verification"] = verification
    return doc


def test_doctrine_only_renders_the_exact_not_run_label(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    relpath = demos["demos"][0]["dot_relpath"]
    doc = _relabel(
        demos,
        {
            "level": T.LEVEL_DOCTRINE_ONLY,
            "lint_verdict": None,
            "lint_not_run_reason": T.lint_not_run_label(relpath),
            "doctrine_verdict": "doctrine_ok",
            "doctrine_report": "AUTHORED-PIPELINE DOCTRINE REPORT\nverdict:   doctrine_ok\n",
        },
    )
    html = render.render_html(ranked, generated_at=PINNED, demos=doc)
    assert f"dot-runner lint: NOT RUN — the CLI is not installed here. Run it yourself: dot-runner lint {relpath}" in html
    assert T.LABEL_UNVERIFIED not in html, "doctrine-only is verified, just not by lint"
    assert "doctrine-only" in html


def test_level_none_renders_the_unverified_banner_and_never_reads_as_verified(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    doc = _relabel(
        demos,
        {
            "level": T.LEVEL_NONE,
            "lint_verdict": None,
            "lint_not_run_reason": "the bundled doctrine checker could not execute in this environment",
            "doctrine_verdict": None,
            "doctrine_report": None,
        },
    )
    html = render.render_html(ranked, generated_at=PINNED, demos=doc)
    assert T.LABEL_UNVERIFIED in html
    assert "doctrine_ok" not in html, "an unverified demo must never display a passing verdict"
    assert "unverified" in html, "the banner carries its own visual treatment"
    assert "dot-runner lint" in html, "both commands to run yourself are offered"


def test_lint_plus_doctrine_quotes_both_verdicts_verbatim(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path)
    verbatim = "dot-runner lint: pipeline.dot: OK (no findings)"
    doc = _relabel(
        demos,
        {
            "level": T.LEVEL_LINT_DOCTRINE,
            "lint_verdict": verbatim,
            "lint_not_run_reason": None,
            "doctrine_verdict": "doctrine_ok",
            "doctrine_report": "[PASS] A4 the exit is structurally unreachable without passing an evidence gate",
        },
    )
    html = render.render_html(ranked, generated_at=PINNED, demos=doc)
    assert verbatim in html
    assert "[PASS] A4" in html
    assert "lint+doctrine" in html


def test_unknown_recovery_renders_as_a_caveat_never_as_fail(tmp_path: Path):
    ranked, demos, _ = _build(tmp_path, verdict="OPPORTUNITY(unproven)", recovery="UNKNOWN")
    html = render.render_html(ranked, generated_at=PINNED, demos=demos)
    assert "unproven — no bad day was ever observed, which is a caveat, never a failure" in html
    assert "FAIL" not in html.split('class="demo"')[1].split("</div>")[0]


def test_write_report_threads_demos_through(tmp_path: Path):
    ranked, demos, out_dir = _build(tmp_path)
    path = render.write_report(ranked, out_dir / "report.html", generated_at=PINNED, demos=demos)
    assert path.is_file()
    assert "primer-section" in path.read_text(encoding="utf-8")
