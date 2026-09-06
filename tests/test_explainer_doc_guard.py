"""Drift guard for docs/attractor-explained.html -- D-210..D-216.

# --- Relocated from modules/loop-pipeline/tests/test_explainer_doc_guard.py as part of the repo
# split's Track A (root guard harness, DESIGN-repo-split.md §1.4/§5#2). This
# guard asserts on the OPINIONATED layer (repo-root docs/examples/skills/
# agents/context/bundles/behaviors), not on engine behavior, so it now runs
# from the repo-root `tests/` suite in CI instead of riding along inside the
# loop-pipeline module's own test tree. ---

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
        if (candidate / "docs").is_dir() and (candidate / "bundle.md").is_file():
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


# --- RE-AIMED 2026-09-06 (the P4 slim, attractor-28x). --------------------
#
# D-211, D-212, D-213 and D-215b used to read engine source directly out of
# `modules/loop-pipeline/amplifier_module_loop_pipeline/` and compare it with
# the page. That package is GONE from this repo -- the compat window closed
# and `amplifier-bundle-dot-runner` is the engine's sole home -- while
# `docs/attractor-explained.html` still ships here. A guard cannot read a
# file in another repository from CI, so the two-sided form is no longer
# available to this repo at all.
#
# The re-aim follows the precedent this repo already set for Q-307 in
# tests/test_quality_protocol_guard.py: where a second copy stopped being
# readable, the claim was pinned to a RECORDED CONSTANT that names its
# provenance. Each engine number below was READ OUT OF the engine at a named
# dot-runner commit, and is recorded here with the file and line it came from.
#
# What this still catches: the page drifting on its own -- someone editing a
# number in the prose, or in the closing "Where a number is quoted" ledger,
# without the other, or without touching the engine at all. That is the
# in-repo failure mode, and it is the only one this repo can still see.
#
# What this NO LONGER catches, stated plainly: the ENGINE moving underneath
# the page. That half now belongs in dot-runner, next to the code. It is
# filed as a dot-runner follow-up, not silently dropped -- see the PR body.
#
# RETIREMENT / RE-AIM CONDITION: when the engine-number claims on
# `docs/attractor-explained.html` move to dot-runner (or the page does),
# delete these constants and the checks that read them, in the same PR.
#
# Anti-rot: test_d210_recorded_engine_constants_name_their_provenance below
# fails if any row here stops naming a dot-runner path, a line, and a sha --
# so the table cannot decay into unsourced magic numbers.

#: The dot-runner commit every constant below was read from.
ENGINE_TRUTH_SHA = "1dfc78bc10d79e4e0ee26e9ac278d95aceaf54b4"
ENGINE_TRUTH_REPO = "microsoft/amplifier-bundle-dot-runner"
_ENGINE_PKG = "modules/loop-pipeline/amplifier_module_loop_pipeline"

#: check id -> (value, dot-runner path, line, the source expression observed)
ENGINE_TRUTH: dict[str, tuple[int, str, int, str]] = {
    "max_parallel_default": (
        4,
        f"{_ENGINE_PKG}/handlers/parallel.py",
        96,
        'int(node.attrs.get("max_parallel", 4))',
    ),
    "last_response_truncation": (
        200,
        f"{_ENGINE_PKG}/handlers/codergen.py",
        224,
        '"last_response": response_text[:200]',
    ),
    "summary_budget_low": (
        600,
        f"{_ENGINE_PKG}/fidelity.py",
        273,
        "# ~600 tokens: brief summary with minimal event counts",
    ),
    "summary_budget_medium": (
        1500,
        f"{_ENGINE_PKG}/fidelity.py",
        285,
        "# ~1500 tokens: recent stage outcomes and active context",
    ),
    "summary_budget_high": (
        3000,
        f"{_ENGINE_PKG}/fidelity.py",
        312,
        "# ~3000 tokens: comprehensive detail including failures",
    ),
}


def _engine_truth(key: str) -> int:
    value, _path, _line, _expr = ENGINE_TRUTH[key]
    return value


def _provenance(key: str) -> str:
    _value, path, line, expr = ENGINE_TRUTH[key]
    return f"{ENGINE_TRUTH_REPO}@{ENGINE_TRUTH_SHA[:7]} {path}:{line} -- `{expr}`"


def test_d210_recorded_engine_constants_name_their_provenance():
    """Every recorded engine constant names where it was read from.

    Without this, the table above rots into unsourced magic numbers the
    moment someone "fixes" a failing check by editing the constant. A
    constant that cannot say which dot-runner file and line it came from is
    not a recorded observation, it is a guess.
    """
    assert ENGINE_TRUTH, (
        "ENGINE_TRUTH is empty -- D-211..D-213 would pass vacuously. If the "
        "page's engine numbers genuinely moved to dot-runner, delete those "
        "checks deliberately rather than emptying their source of truth."
    )
    assert re.fullmatch(r"[0-9a-f]{40}", ENGINE_TRUTH_SHA), (
        f"ENGINE_TRUTH_SHA is {ENGINE_TRUTH_SHA!r}, not a full 40-char commit "
        "sha. The whole point of the recorded form is that a reader can go and "
        "check the value at an exact commit."
    )
    bad = [
        key
        for key, (value, path, line, expr) in ENGINE_TRUTH.items()
        if not (
            isinstance(value, int)
            and path.startswith(_ENGINE_PKG)
            and isinstance(line, int)
            and line > 0
            and expr.strip()
        )
    ]
    assert not bad, (
        f"ENGINE_TRUTH rows {bad} do not name a dot-runner path, a line, and "
        "the source expression they were read from. Re-read the value at a "
        f"named {ENGINE_TRUTH_REPO} commit and record all four fields."
    )


# ---------------------------------------------------------------------------
# D-210: feedback_from critique caps
# ---------------------------------------------------------------------------


def test_d211_max_parallel_default_matches_parallel_handler():
    """D-211: the page's ``max_parallel`` default must equal the handler's.

    Page claim: "``max_parallel`` defaulting to 4."  Source of truth: the
    fallback in ``handlers/parallel.py``'s ``node.attrs.get("max_parallel", N)``.
    """
    code_default = _engine_truth("max_parallel_default")
    match = _claim(
        r"max_parallel\s*defaulting to\s*(\d+)",
        "max_parallel default",
    )
    page_default = _int(match.group(1))

    assert page_default == code_default, (
        f"{PAGE_REL} says max_parallel defaults to {page_default}, but the "
        f"engine defaults it to {code_default}: "
        f"{_provenance('max_parallel_default')}. Update the 'Variables and "
        f"parallelism' paragraph in {PAGE_REL} (and its closing 'Where a number "
        f"is quoted' ledger) to match -- or, if the ENGINE moved, re-read the "
        f"value at a current dot-runner commit and update ENGINE_TRUTH and the "
        f"page together in the same PR. (D-211)"
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
    code_limit = _engine_truth("last_response_truncation")
    match = _claim(
        r"last_response\s*is truncated to\s*([\d,]+) characters",
        "last_response truncation",
    )
    page_limit = _int(match.group(1))

    assert page_limit == code_limit, (
        f"{PAGE_REL} says last_response is truncated to {page_limit} characters, "
        f"but the engine truncates it to {code_limit}: "
        f"{_provenance('last_response_truncation')}. Update the 'Gotcha' note in "
        f"{PAGE_REL} (and its closing 'Where a number is quoted' ledger) to "
        f"match -- or, if the ENGINE moved, re-read the value at a current "
        f"dot-runner commit and update ENGINE_TRUTH and the page together in "
        f"the same PR. (D-212)"
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
    an enforced constant, so the comment IS the anchor.  Since the P4 slim those
    comments live in dot-runner and are RECORDED in ``ENGINE_TRUTH`` above,
    with the file, line, and sha they were read from.
    """
    for level in ("low", "medium", "high"):
        code_budget = _engine_truth(f"summary_budget_{level}")

        page_match = _claim(
            rf"summary:{level}\s*Summary at roughly\s*([\d,]+)\s*tokens",
            f"summary:{level} budget",
        )
        page_budget = _int(page_match.group(1))

        assert page_budget == code_budget, (
            f"{PAGE_REL} says summary:{level} is roughly {page_budget:,} tokens, "
            f"but the engine budgets it at ~{code_budget:,}: "
            f"{_provenance(f'summary_budget_{level}')}. Update the 'context "
            f"fidelity modes' table in {PAGE_REL} (and its closing 'Where a number "
            f"is quoted' ledger) to match -- or, if the ENGINE moved, re-read the "
            f"budgets at a current dot-runner commit and update ENGINE_TRUTH and "
            f"the page together in the same PR. (D-213)"
        )


# ---------------------------------------------------------------------------
# D-214: fidelity mode vocabulary and default
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
    than in spec prose alone.  Since the P4 slim that orchestrator lives in
    dot-runner, so the call order is a RECORDED observation (see ENGINE_TRUTH)
    and the page half -- the diagram's own ordering -- is checked live.
    """
    # The orchestrator's call order, READ from the engine and recorded here
    # with its provenance (see the ENGINE_TRUTH note above for why this is a
    # recorded observation rather than a live read).
    call_order = (
        ("apply_transforms(", 388),
        ("validate_or_raise(", 391),
    )
    src_rel = f"{_ENGINE_PKG}/__init__.py"
    transform_call, transform_line = call_order[0]
    validate_call, validate_line = call_order[1]

    assert transform_line < validate_line, (
        f"the recorded orchestrator call order has {validate_call} at line "
        f"{validate_line} BEFORE {transform_call} at line {transform_line} "
        f"({ENGINE_TRUTH_REPO}@{ENGINE_TRUTH_SHA[:7]} {src_rel}). "
        f"{PAGE_REL} tells readers TRANSFORM runs before VALIDATE 'so lint reads "
        f"the expanded graph'. Either the ordering regressed or the page is now "
        f"wrong -- fix whichever is actually stale. (D-215b)"
    )

    # The page half: both phase names must actually be in the diagram, in that
    # order. This is the half this repo owns and can still check for real.
    html = _page_html()
    page_transform = html.find("TRANSFORM")
    page_validate = html.find("VALIDATE")
    assert page_transform != -1 and page_validate != -1, (
        f"{PAGE_REL} no longer names both TRANSFORM and VALIDATE in its "
        f"lifecycle diagram (TRANSFORM at {page_transform}, VALIDATE at "
        f"{page_validate}). (D-215b)"
    )
    assert page_transform < page_validate, (
        f"{PAGE_REL} shows VALIDATE before TRANSFORM in its lifecycle diagram, "
        f"but the orchestrator calls {transform_call} (line {transform_line}) "
        f"before {validate_call} (line {validate_line}) at "
        f"{ENGINE_TRUTH_REPO}@{ENGINE_TRUTH_SHA[:7]} {src_rel}. (D-215b)"
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
