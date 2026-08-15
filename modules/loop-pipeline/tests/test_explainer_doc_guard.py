"""Drift guard for docs/attractor-explained.html -- D-210..D-216.

Guards the human-facing explainer page (``docs/attractor-explained.html``,
published at
https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html)
against silent drift away from the engine it describes.

**Why this page gets its own guard.**  The other doc guards protect internal
references that a maintainer re-reads while working in the tree; a stale line
there is caught by the next person to use it.  This page is different: it is
written for people *outside* the working set -- newcomers orienting, colleagues
receiving the link second-hand -- and it is deliberately NEVER loaded into agent
context (it is ~87 KB of markup; agents get the same facts far more cheaply from
``docs/`` and ``context/``).  Nobody in the loop re-reads it.  So a number that
rots here rots silently and keeps being shared, which is strictly worse than an
internal doc going stale.

**The assertions are two-sided on purpose.**  Every check reads the value from
its *source of truth in the code* and compares it against what the page claims.
A page-only assertion ("the page says 500") would be tautological -- it would
pass forever and fail only when someone edited the page, which is the one case
that does not need guarding.  These fail when the CODE changes, which is the
case that actually produces a stale page.

Claims guarded, and the source of truth each is checked against:

  D-210  ``feedback_from`` critique caps (500 chars, 5 carried)
         <- ``feedback.py`` ``MAX_CRITIQUE_CHARS`` / ``MAX_CRITIQUES``
  D-211  ``max_parallel`` default (4)
         <- ``handlers/parallel.py`` ``node.attrs.get("max_parallel", N)``
  D-212  ``last_response`` truncation (200 chars, every fidelity mode)
         <- ``handlers/codergen.py`` ``response_text[:N]``
  D-213  summary fidelity budgets (~600 / ~1,500 / ~3,000 tokens)
         <- ``fidelity.py`` ``_build_summary_preamble`` level comments
  D-214  the fidelity mode vocabulary and its default
         <- ``fidelity.py`` ``VALID_FIDELITY_MODES`` / ``_DEFAULT_FIDELITY``
  D-215  the six-phase lifecycle incl. TRANSFORM
         <- ``specs/canonical/attractor-spec-canonical.md`` section 3.1, plus the
            transform-before-validate call order in ``__init__.py``
  D-216  the shape -> execution-tier vocabulary
         <- ``validation.py`` ``SHAPE_TO_HANDLER``

Honest limits:
  - D-211/D-212/D-213 read *source text* rather than an importable symbol,
    because those values are inline literals (and, for D-213, budget comments)
    with no constant to import.  A refactor that moves the literal without
    changing it will fail this guard loudly rather than silently -- the failure
    message says which file to re-anchor on.
  - Extracting the page's claims is regex-over-prose.  Rewording the sentence a
    claim lives in will fail the guard with "claim not found on the page"; that
    is the intended failure, not a false alarm -- a reworded claim needs a
    re-anchored guard.
  - This module skips wholesale when ``docs/attractor-explained.html`` is absent,
    so the loop-pipeline suite still runs in a module-only/partial checkout.

Companion pointers that must keep resolving to the published page:
``README.md``, ``agents/attractor-expert.md``, ``skills/attractorify/SKILL.md``,
``context/pipeline-awareness.md``.
"""

import html as _html
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locating the repo root and the page
# ---------------------------------------------------------------------------


def _find_bundle_root() -> Path | None:
    """Walk up from this file looking for the bundle repo root.

    The sibling guards hardcode ``Path(__file__).parent.parent.parent.parent``;
    this walks instead so the guard survives the module being vendored or
    re-nested, and returns None (-> module skip) rather than pointing at a
    plausible-but-wrong directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docs").is_dir() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()
PAGE_REL = "docs/attractor-explained.html"
PAGE_PATH = (BUNDLE_ROOT / PAGE_REL) if BUNDLE_ROOT is not None else None
PAGE_URL = (
    "https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html"
)

pytestmark = pytest.mark.skipif(
    PAGE_PATH is None or not PAGE_PATH.is_file(),
    reason=(
        f"{PAGE_REL} not present in this checkout -- the explainer page ships in "
        "the bundle repo, not in the loop-pipeline module distribution. Nothing "
        "to guard here; the rest of the module suite is unaffected."
    ),
)


# ---------------------------------------------------------------------------
# Page text extraction
# ---------------------------------------------------------------------------

# <style>/<script>/<svg> bodies are full of numbers (coordinates, font sizes)
# that would pollute the prose regexes below. Strip those blocks whole before
# stripping the remaining tags.
_BLOCK_RE = re.compile(r"<(script|style|svg)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _page_html() -> str:
    assert PAGE_PATH is not None  # guaranteed by pytestmark
    return PAGE_PATH.read_text(encoding="utf-8")


def _page_text() -> str:
    """Prose-only view of the page: markup and vector art removed."""
    stripped = _BLOCK_RE.sub(" ", _page_html())
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", stripped)))


def _claim(pattern: str, label: str) -> re.Match[str]:
    """Find a claim in the page prose, or fail naming what went missing."""
    match = re.search(pattern, _page_text())
    assert match is not None, (
        f"{PAGE_REL}: could not find the {label} claim on the page "
        f"(pattern: {pattern!r}). Either the claim was removed -- in which case "
        f"drop the matching assertion here -- or the sentence was reworded, in "
        f"which case re-anchor this pattern on the new wording. The page is "
        f"published at {PAGE_URL} and is never loaded into agent context, so an "
        f"unguarded claim on it rots silently."
    )
    return match


#: Spelled-out counts the page uses in prose (it writes "Six phases", not "6").
_NUM_WORDS: dict[int, str] = {
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
}


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


# ---------------------------------------------------------------------------
# Source-of-truth readers
# ---------------------------------------------------------------------------


def _pkg_dir() -> Path:
    """Directory of the installed/importable loop-pipeline package.

    Derived from the imported module rather than assembled from BUNDLE_ROOT, so
    the source read below is guaranteed to be the same code the rest of the
    suite exercises.
    """
    import amplifier_module_loop_pipeline as pkg

    assert pkg.__file__ is not None
    return Path(pkg.__file__).parent


def _pkg_src(rel: str) -> str:
    return (_pkg_dir() / rel).read_text(encoding="utf-8")


def _source_literal(rel: str, pattern: str, what: str) -> int:
    """Extract a single integer literal from package source, or fail loudly."""
    match = re.search(pattern, _pkg_src(rel))
    assert match is not None, (
        f"{rel}: could not read the {what} literal (pattern: {pattern!r}). "
        f"It is the source of truth for a claim on {PAGE_REL}; if the value moved "
        f"to a named constant or a different call site, re-anchor this guard on "
        f"the new location so the page keeps being checked against real code."
    )
    return int(match.group(1))


# ---------------------------------------------------------------------------
# D-210: feedback_from critique caps
# ---------------------------------------------------------------------------


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


def test_d211_max_parallel_default_matches_parallel_handler():
    """D-211: the page's ``max_parallel`` default must equal the handler's.

    Page claim: "``max_parallel`` defaulting to 4."  Source of truth: the
    fallback in ``handlers/parallel.py``'s ``node.attrs.get("max_parallel", N)``.
    """
    code_default = _source_literal(
        "handlers/parallel.py",
        r"max_parallel[\"']\s*,\s*(\d+)\s*\)",
        "max_parallel default",
    )
    match = _claim(
        r"max_parallel\s*defaulting to\s*(\d+)",
        "max_parallel default",
    )
    page_default = _int(match.group(1))

    assert page_default == code_default, (
        f"{PAGE_REL} says max_parallel defaults to {page_default}, but "
        f"handlers/parallel.py defaults it to {code_default}. Update the "
        f"'Variables and parallelism' paragraph in {PAGE_REL} (and its closing "
        f"'Where a number is quoted' ledger) to match handlers/parallel.py. (D-211)"
    )


# ---------------------------------------------------------------------------
# D-212: last_response truncation
# ---------------------------------------------------------------------------


def test_d212_last_response_truncation_matches_codergen_handler():
    """D-212: the page's ``last_response`` truncation must equal codergen's.

    Page claim (the 'Gotcha' note): "``last_response`` is truncated to 200
    characters in *every* mode -- ``full`` included."  Source of truth: the
    ``response_text[:N]`` slice in ``handlers/codergen.py``, which is where the
    key is written into ``context_updates``.
    """
    code_limit = _source_literal(
        "handlers/codergen.py",
        r"response_text\[:(\d+)\]",
        "last_response truncation",
    )
    match = _claim(
        r"last_response\s*is truncated to\s*([\d,]+) characters",
        "last_response truncation",
    )
    page_limit = _int(match.group(1))

    assert page_limit == code_limit, (
        f"{PAGE_REL} says last_response is truncated to {page_limit} characters, "
        f"but handlers/codergen.py truncates it to {code_limit} "
        f"(response_text[:{code_limit}]). Update the 'Gotcha' note in {PAGE_REL} "
        f"(and its closing 'Where a number is quoted' ledger) to match "
        f"handlers/codergen.py. (D-212)"
    )


# ---------------------------------------------------------------------------
# D-213: summary fidelity budgets
# ---------------------------------------------------------------------------


def test_d213_summary_budgets_match_fidelity_module():
    """D-213: the page's summary token budgets must equal fidelity.py's.

    Page claim (the context-fidelity table): summary:low/medium/high are
    "Summary at roughly 600 / 1,500 / 3,000 tokens".  Source of truth: the
    per-level budget comments inside ``fidelity.py`` ``_build_summary_preamble``
    (``# ~600 tokens:`` and friends) -- these budgets are documented intent, not
    an enforced constant, so the comment IS the anchor.
    """
    src = _pkg_src("fidelity.py")
    for level in ("low", "medium", "high"):
        code_match = re.search(
            rf"level\s*==\s*[\"']{level}[\"'].*?#\s*~([\d,]+)\s*tokens",
            src,
            re.DOTALL,
        )
        assert code_match is not None, (
            f"fidelity.py: could not read the '~N tokens' budget comment for "
            f"summary:{level} inside _build_summary_preamble. It is the source of "
            f"truth for the context-fidelity table in {PAGE_REL}; re-anchor this "
            f"guard if the budgets moved to named constants. (D-213)"
        )
        code_budget = _int(code_match.group(1))

        page_match = _claim(
            rf"summary:{level}\s*Summary at roughly\s*([\d,]+)\s*tokens",
            f"summary:{level} budget",
        )
        page_budget = _int(page_match.group(1))

        assert page_budget == code_budget, (
            f"{PAGE_REL} says summary:{level} is roughly {page_budget:,} tokens, "
            f"but fidelity.py budgets it at ~{code_budget:,} tokens. Update the "
            f"'context fidelity modes' table in {PAGE_REL} (and its closing 'Where "
            f"a number is quoted' ledger) to match fidelity.py. (D-213)"
        )


# ---------------------------------------------------------------------------
# D-214: fidelity mode vocabulary and default
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


def test_d215_lifecycle_phases_match_canonical_spec():
    """D-215: the page's run lifecycle must be the canonical six phases.

    Page claim: "Six phases" + a PARSE / TRANSFORM / VALIDATE / INITIALIZE /
    EXECUTE / FINALIZE diagram.  Source of truth: the lifecycle line in
    ``specs/canonical/attractor-spec-canonical.md`` section 3.1 (absorbed
    upstream; ``specs/EXTENSIONS.md`` section 5 retains the history).  The
    five-phase form the page used to carry omitted TRANSFORM, which is exactly
    the drift this catches.
    """
    assert BUNDLE_ROOT is not None
    spec_rel = "specs/canonical/attractor-spec-canonical.md"
    spec = (BUNDLE_ROOT / spec_rel).read_text(encoding="utf-8")

    spec_match = re.search(r"^\s*(PARSE(?:\s*->\s*[A-Z]+)+)\s*$", spec, re.MULTILINE)
    assert spec_match is not None, (
        f"{spec_rel}: could not find the 'PARSE -> ... -> FINALIZE' lifecycle "
        f"line. It is the source of truth for the run-lifecycle diagram in "
        f"{PAGE_REL}; re-anchor this guard if the spec reformatted it. (D-215)"
    )
    phases = [p.strip() for p in spec_match.group(1).split("->")]

    text = _page_text()
    # The page spells the count out in prose ("Six phases"); accept either form.
    count_alt = "|".join(
        re.escape(f) for f in (str(len(phases)), _NUM_WORDS.get(len(phases), "")) if f
    )
    assert re.search(rf"\b(?:{count_alt})[ -]phases?\b", text, re.IGNORECASE), (
        f"{PAGE_REL} does not describe the lifecycle as {len(phases)} phases, but "
        f"{spec_rel} specifies {len(phases)}: {' -> '.join(phases)}. Update the "
        f"'Under the hood' standfirst and the lifecycle diagram in {PAGE_REL}. "
        f"(D-215)"
    )
    missing = [p for p in phases if p not in _page_html()]
    assert not missing, (
        f"{PAGE_REL} is missing lifecycle phase(s) {missing} from its run-lifecycle "
        f"diagram. {spec_rel} specifies {' -> '.join(phases)}. Update the diagram "
        f"in {PAGE_REL} to match the spec. (D-215)"
    )


def test_d215b_transform_runs_before_validate_in_source():
    """D-215b (source-inspection): TRANSFORM really does precede VALIDATE.

    The page's caption claims TRANSFORM "resolves the stylesheet and expands
    variables before VALIDATE sees the graph".  That ordering is the whole point
    of the phase, so ground the claim in the orchestrator's call order rather
    than in spec prose alone.
    """
    src = _pkg_src("__init__.py")
    transform_pos = src.find("apply_transforms(")
    validate_pos = src.find("validate_or_raise(")

    assert transform_pos != -1, (
        "__init__.py does not call apply_transforms(). The TRANSFORM phase in "
        f"{PAGE_REL}'s lifecycle diagram is now unsupported by the shipped "
        "orchestrator. (D-215b)"
    )
    assert validate_pos != -1, (
        "__init__.py does not call validate_or_raise(). The VALIDATE phase in "
        f"{PAGE_REL}'s lifecycle diagram is now unsupported by the shipped "
        "orchestrator. (D-215b)"
    )
    assert transform_pos < validate_pos, (
        f"__init__.py calls validate_or_raise() BEFORE apply_transforms(). "
        f"{PAGE_REL} tells readers TRANSFORM runs before VALIDATE 'so lint reads "
        f"the expanded graph'. Either the ordering regressed or the page is now "
        f"wrong -- fix whichever is actually stale. (D-215b)"
    )


# ---------------------------------------------------------------------------
# D-216: shape -> execution-tier vocabulary
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
