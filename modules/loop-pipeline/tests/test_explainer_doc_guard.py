"""Engine-dependent residual of the docs/attractor-explained.html drift guard -- D-210, D-214, D-216, D-216b.

The majority of this guard (D-211..D-213, D-215, D-215b -- text-anchored
and source-inspection checks that don't need the live engine) moved to
the repo-root opinionated-layer harness at tests/test_explainer_doc_guard.py
(Track A of the repo split, DESIGN-repo-split.md §1.4/§5#2). These four
checks stayed here because they assert the page's claim against LIVE
engine modules (amplifier_module_loop_pipeline.feedback / .fidelity /
.validation), not just against other doc/spec text.
"""

import html as _html
import re
from pathlib import Path


def _find_bundle_root() -> Path | None:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docs").is_dir() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()
PAGE_REL = "docs/attractor-explained.html"
PAGE_PATH = (BUNDLE_ROOT / PAGE_REL) if BUNDLE_ROOT is not None else None

_BLOCK_RE = re.compile(r"<(script|style|svg)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _page_html() -> str:
    assert PAGE_PATH is not None
    return PAGE_PATH.read_text(encoding="utf-8")


def _page_text() -> str:
    """Prose-only view of the page: markup and vector art removed."""
    stripped = _BLOCK_RE.sub(" ", _page_html())
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", stripped)))


def _claim(pattern: str, label: str):
    """Find a claim in the page prose, or fail naming what went missing."""
    match = re.search(pattern, _page_text())
    assert match is not None, (
        f"{PAGE_REL}: could not find the {label} claim on the page "
        f"(pattern: {pattern!r})."
    )
    return match


def _int(raw: str) -> int:
    """'1,500' -> 1500 (the page writes thousands separators, code does not)."""
    return int(raw.replace(",", "").replace("\u202f", "").replace("\xa0", ""))


def _table_segment(caption: str) -> str:
    """Raw HTML of the table whose <caption> contains *caption*."""
    page = _page_html()
    start = page.find(caption)
    assert start != -1, (
        f"{PAGE_REL}: table captioned {caption!r} not found. This guard reads "
        f"that table to compare the page against the engine; re-anchor it if the "
        f"caption changed."
    )
    end = page.find("</table>", start)
    assert end != -1, f"{PAGE_REL}: table captioned {caption!r} is not closed."
    return page[start:end]


def test_d210_feedback_critique_caps_match_feedback_module():
    """D-210: the page's ``feedback_from`` caps must equal feedback.py's.

    Page claim: "Each critique is truncated to 500 characters, at most 5 are
    carried."  Source of truth: ``feedback.py`` ``MAX_CRITIQUE_CHARS`` and
    ``MAX_CRITIQUES``.  Changing either constant must fail here, not ship a
    published page that quotes the old cap.
    """
    from amplifier_module_loop_pipeline.feedback import (
        MAX_CRITIQUE_CHARS,
        MAX_CRITIQUES,
    )

    match = _claim(
        r"critique is truncated to\s*([\d,]+)\s*characters\s*,\s*"
        r"at most\s*(\d+)\s*are carried",
        "feedback_from critique cap",
    )
    page_chars, page_count = _int(match.group(1)), _int(match.group(2))

    assert page_chars == MAX_CRITIQUE_CHARS, (
        f"{PAGE_REL} says each critique is truncated to {page_chars} characters, "
        f"but feedback.py MAX_CRITIQUE_CHARS is {MAX_CRITIQUE_CHARS}. Update the "
        f"'feedback_from' paragraph in {PAGE_REL} (and its closing 'Where a number "
        f"is quoted' ledger) to match feedback.py. (D-210)"
    )
    assert page_count == MAX_CRITIQUES, (
        f"{PAGE_REL} says at most {page_count} critiques are carried, but "
        f"feedback.py MAX_CRITIQUES is {MAX_CRITIQUES}. Update the 'feedback_from' "
        f"paragraph in {PAGE_REL} (and its closing 'Where a number is quoted' "
        f"ledger) to match feedback.py. (D-210)"
    )


# ---------------------------------------------------------------------------
# D-211: max_parallel default
# ---------------------------------------------------------------------------


def test_d214_fidelity_mode_vocabulary_matches_fidelity_module():
    """D-214: the page's fidelity table must list exactly the shipped modes.

    Source of truth: ``fidelity.py`` ``VALID_FIDELITY_MODES`` (membership) and
    ``_DEFAULT_FIDELITY`` (which row is marked "(default)").  Adding or renaming
    a mode without touching the page must fail here.
    """
    from amplifier_module_loop_pipeline.fidelity import (
        _DEFAULT_FIDELITY,
        VALID_FIDELITY_MODES,
    )

    segment = _table_segment("context fidelity modes")
    listed = set(re.findall(r"<td><code>([a-z:]+)</code>", segment))

    assert listed == set(VALID_FIDELITY_MODES), (
        f"{PAGE_REL}'s 'context fidelity modes' table lists {sorted(listed)}, but "
        f"fidelity.py VALID_FIDELITY_MODES is "
        f"{sorted(VALID_FIDELITY_MODES)}. "
        f"Missing from the page: {sorted(set(VALID_FIDELITY_MODES) - listed)}; "
        f"stale on the page: {sorted(listed - set(VALID_FIDELITY_MODES))}. Update "
        f"the table in {PAGE_REL} to match fidelity.py. (D-214)"
    )

    default_row = re.search(
        rf"<td><code>{re.escape(_DEFAULT_FIDELITY)}</code>.{{0,120}}?\(default\)",
        segment,
        re.DOTALL,
    )
    assert default_row is not None, (
        f"{PAGE_REL}'s 'context fidelity modes' table does not mark "
        f"'{_DEFAULT_FIDELITY}' as the default, but fidelity.py _DEFAULT_FIDELITY "
        f"is '{_DEFAULT_FIDELITY}'. Move the '(default)' marker in {PAGE_REL} to "
        f"the row fidelity.py actually defaults to. (D-214)"
    )


# ---------------------------------------------------------------------------
# D-215: six-phase lifecycle including TRANSFORM
# ---------------------------------------------------------------------------


def _page_shape_rows() -> dict[str, str]:
    segment = _table_segment("node shapes and what they execute")
    return {
        m.group(1): m.group(3)
        for m in re.finditer(
            r"<td><code>(\w+)</code>(.*?)</td>\s*<td>(.*?)</td>", segment, re.DOTALL
        )
    }


def test_d216_shape_vocabulary_matches_validation_module():
    """D-216: the page's shape table must be exactly the shipped vocabulary.

    Source of truth: ``validation.py`` ``SHAPE_TO_HANDLER``.  The page sells
    "a rendered Attractor graph is readable at a glance" on the strength of this
    table being complete, so both directions are checked: a shape the engine
    gained but the page never listed, and a shape the page still lists after the
    engine dropped it, are both drift.
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    listed = set(_page_shape_rows())
    shipped = set(SHAPE_TO_HANDLER)

    assert listed == shipped, (
        f"{PAGE_REL}'s node-shape table and validation.py SHAPE_TO_HANDLER "
        f"disagree. Missing from the page: {sorted(shipped - listed)}; stale on "
        f"the page: {sorted(listed - shipped)}. Update the 'node shapes and what "
        f"they execute' table in {PAGE_REL} to match validation.py. (D-216)"
    )


def test_d216b_load_bearing_tier_claims_match_validation_module():
    """D-216b: the two tier claims the page's argument rests on must hold.

    The page's "models judge, shells execute" section depends on ``box`` being
    the LLM/codergen tier and ``parallelogram`` being the shell/tool tier -- that
    is the split the whole evidence-gate thesis is built on.  Checked against
    ``validation.py`` ``SHAPE_TO_HANDLER`` rather than restated.
    """
    from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER

    rows = _page_shape_rows()
    for shape, expected_handler in (("box", "codergen"), ("parallelogram", "tool")):
        assert SHAPE_TO_HANDLER.get(shape) == expected_handler, (
            f"validation.py now maps shape '{shape}' to "
            f"'{SHAPE_TO_HANDLER.get(shape)}', not '{expected_handler}'. "
            f"{PAGE_REL}'s 'Tier discipline: models judge, shells execute' section "
            f"and its node-shape table both assume the old mapping -- update the "
            f"page, then re-anchor this guard. (D-216b)"
        )
        assert expected_handler in rows.get(shape, "").lower(), (
            f"{PAGE_REL}'s node-shape row for '{shape}' does not describe it as the "
            f"'{expected_handler}' tier (row text: {rows.get(shape, '<missing>')!r}), "
            f"but validation.py maps it to '{expected_handler}'. Update the row in "
            f"{PAGE_REL} to match validation.py. (D-216b)"
        )
