"""Drift guard for docs/QUALITY_PROTOCOL.md and docs/VISION.md -- Q-300..Q-307.

Guards the quality protocol itself against its own external references going
stale.  The doc is binding on contributors and on AI coding agents working
here, and section 2's "Docs making factual claims" row demands a guard for
exactly this class of page.  Until now it had none: section 5 named that gap
out loud and set an adoption condition -- *"the first time one of those
references is found stale, or the Layer-2 matrix lands (whichever comes
first)"*.  The matrix landing is this guard's trigger.

**What makes these assertions non-tautological.**  Every check reads a claim
*from the doc* and resolves it against *the repository*.  A page-only
assertion ("the doc says five guards") would pass forever and fail only when
someone edited the doc, which is the one case that needs no guard.  These
fail when the **repo** moves underneath the doc -- a guard file renamed or
deleted, the matrix relocated, the canonical spec re-vendored to a new
upstream sha -- which is the case that actually produces a lying protocol.

Claims guarded, and what each is resolved against:

  Q-300  every ``test_*.py`` the doc names by name
         -> the file exists under ``modules/loop-pipeline/tests/``
  Q-301  the Layer-2 files (``specs/conformance/attractor-matrix.yaml`` and
         the matrix runner module), which the doc states as **shipped**
         -> both files exist on disk
  Q-302  the vendored canonical spec and its recorded upstream sha
         -> ``specs/canonical/attractor-spec-canonical.md`` exists, and the
            sha the doc records appears in ``SPEC_CONFORMANCE.md`` at the
            ``SYNC-1`` row the doc names as the pin's home
  Q-303  the Changelog section the meta-protocol (section 7) requires
         -> the section exists and carries at least one dated entry
  Q-304  the captured vision the protocol now reads against
         -> ``docs/VISION.md`` exists and carries its own dated Changelog
  Q-305  VISION.md states the decision matrix, and the repo-relative files
         it cites resolve -> every relative markdown link lands on a real file
  Q-306  the protocol carries the decision-matrix section and names the
         ``vision-observation`` label the observation convention depends on
         -> both are present in the page
  Q-307  the decision matrix's canonical articulation lives on two pages
         -> the two copies are byte-identical once whitespace-normalized

Honest limits:
  - Q-300 extracts filenames by regex over backticked prose.  A guard file
    referred to only in running text without backticks is invisible here.
    That is the same tradeoff the sibling doc guards make, and the failure
    direction is safe: an unquoted name is unguarded, never falsely red.
  - Q-302 checks that the sha *string* the doc records is the one the ledger
    records.  It does not re-fetch upstream or re-verify the vendored bytes;
    the matrix's SYNC row owns the byte-level sha256 pin.  This check owns
    the narrower claim that the doc and the ledger name the same commit.
  - Q-303 asserts a dated entry exists, not that the newest entry describes
    the newest amendment.  No mechanical check can know that; section 7's
    review owns it.
  - Q-304..Q-307 deliberately do **not** guard the vision's prose.  A vision
    is judgment, not a set of fact claims about code; a guard over its
    wording would pin taste rather than truth, and would fail exactly when
    someone improved it.  What they guard is its *structure* (it exists, it
    has an amendment history, it states its governing rule, its citations
    resolve) and the one thing that can silently rot -- the decision
    matrix's articulation duplicated across two pages, where editing one
    copy leaves the other stating a different rule under the same name.
  - The ``vision-observation`` label itself is repo-external state (GitHub),
    which these suites cannot and should not reach.  Q-306 asserts the doc
    *states the label name*, which is the part that can drift in-tree.
  - This module skips wholesale when ``docs/QUALITY_PROTOCOL.md`` is absent,
    so the loop-pipeline suite still runs in a module-only/partial checkout.

Reference: ``docs/QUALITY_PROTOCOL.md`` section 5 (Layers 0-2), section 7
(the meta-protocol, which is where this guard's own adoption condition is
recorded), and sections 3-4 (the decision matrix and the observation duty).
"""

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locating the repo root and the doc
# ---------------------------------------------------------------------------


def _find_bundle_root() -> Path | None:
    """Walk up from this file looking for the bundle repo root.

    Walks rather than hardcoding a parent count, so the guard survives the
    module being vendored or re-nested, and returns None (-> module skip)
    rather than pointing at a plausible-but-wrong directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docs").is_dir() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()
DOC_REL = "docs/QUALITY_PROTOCOL.md"
VISION_REL = "docs/VISION.md"
DOC_PATH = (BUNDLE_ROOT / DOC_REL) if BUNDLE_ROOT is not None else None
TESTS_DIR_REL = "modules/loop-pipeline/tests"

pytestmark = pytest.mark.skipif(
    DOC_PATH is None or not DOC_PATH.is_file(),
    reason=(
        f"{DOC_REL} not present in this checkout -- the quality protocol ships "
        "in the bundle repo, not in the loop-pipeline module distribution. "
        "Nothing to guard here; the rest of the module suite is unaffected."
    ),
)


def _doc() -> str:
    assert DOC_PATH is not None  # guaranteed by pytestmark
    return DOC_PATH.read_text(encoding="utf-8")


def _root() -> Path:
    assert BUNDLE_ROOT is not None  # guaranteed by pytestmark
    return BUNDLE_ROOT


# ---------------------------------------------------------------------------
# Q-300: every guard-test file the doc names by name must exist
# ---------------------------------------------------------------------------

# Backticked `test_*.py`, either bare (`test_doc_consistency.py`, as the
# Layer-1 table writes them) or path-qualified
# (`modules/loop-pipeline/tests/test_live_graph_gate.py`, as the prose does).
_TEST_FILE_RE = re.compile(r"`([A-Za-z0-9_./-]*test_[A-Za-z0-9_]+\.py)`")


def _named_test_files() -> list[str]:
    return sorted(set(_TEST_FILE_RE.findall(_doc())))


def _resolve_named_test_file(name: str) -> Path:
    """A bare filename means the loop-pipeline tests dir; a path means itself."""
    return _root() / (name if "/" in name else f"{TESTS_DIR_REL}/{name}")


def test_q300_named_guard_test_files_all_exist():
    """Every `test_*.py` the protocol names by name resolves to a real file."""
    named = _named_test_files()
    assert named, (
        f"{DOC_REL}: the doc names no test files at all. Layer 1 and Layer 2 "
        f"are both defined by the files they name; if that prose was rewritten "
        f"to stop naming them, re-anchor this guard on the new wording rather "
        f"than deleting it."
    )
    missing = [
        f"{name} (looked for {_resolve_named_test_file(name).relative_to(_root())})"
        for name in named
        if not _resolve_named_test_file(name).is_file()
    ]
    assert not missing, (
        f"{DOC_REL} names guard-test files that do not exist:\n"
        + "".join(f"  - {m}\n" for m in missing)
        + "  The protocol's authority rests on the machinery it names being real.\n"
        "  A named-but-absent guard is the protocol claiming an enforcement it\n"
        "  does not have. Either the file was renamed/deleted -- in which case\n"
        "  update the doc in the same PR -- or the doc names it wrongly.\n"
        f"  (Bare filenames are resolved against {TESTS_DIR_REL}/.)"
    )


def test_q300b_the_five_layer1_guards_are_among_them():
    """The Layer-1 table's five guards specifically, named as a floor.

    Q-300 is generic over whatever the doc names; this pins the five the
    Layer-1 table is *about*, so silently dropping a row from that table
    fails here rather than shrinking the guarded set unnoticed.
    """
    expected = {
        "test_extensions_ledger_integrity.py",
        "test_doc_consistency.py",
        "test_engine_semantics_doc_guard.py",
        "test_explainer_doc_guard.py",
        "test_examples_lint_clean.py",
    }
    named = set(_named_test_files())
    # Path-qualified mentions count as naming the file too.
    named |= {n.rsplit("/", 1)[-1] for n in named}
    missing = sorted(expected - named)
    assert not missing, (
        f"{DOC_REL}: the Layer-1 table no longer names {missing}.\n"
        "  Layer 1 IS its five deterministic guards; dropping one from the\n"
        "  table without retiring the guard (section 5's retirement protocol)\n"
        "  leaves the doc describing a defense narrower than the one that runs.\n"
        "  If a guard was genuinely retired, update this expected set in the\n"
        "  same PR and record the retirement in the Changelog."
    )


# ---------------------------------------------------------------------------
# Q-301: the Layer-2 files the doc declares shipped must exist
# ---------------------------------------------------------------------------

LAYER2_FILES = (
    "specs/conformance/attractor-matrix.yaml",
    f"{TESTS_DIR_REL}/test_spec_conformance_matrix.py",
)


def test_q301_layer2_is_declared_shipped():
    """Layer 2's status line must still read as shipped, not 'in flight'."""
    doc = _doc()
    match = re.search(
        r"###\s*Layer 2[^\n]*\n+\*\*Status:\s*([^*]+)\*\*", doc, re.IGNORECASE
    )
    assert match is not None, (
        f"{DOC_REL}: could not find Layer 2's '**Status: ...**' line. Either the "
        "section was reworded -- re-anchor this pattern -- or the status claim "
        "was dropped, which is exactly the claim this guard exists to hold."
    )
    status = match.group(1).strip().lower()
    assert "shipped" in status, (
        f"{DOC_REL}: Layer 2's status reads {match.group(1).strip()!r}, not "
        "'shipped'. The files below are on disk, so a non-shipped status is the "
        "doc understating what runs in CI. Update the status or retire the files."
    )


@pytest.mark.parametrize("rel", LAYER2_FILES)
def test_q301b_layer2_files_exist(rel: str):
    """Both files Layer 2 names as the shipped matrix must be present."""
    assert (_root() / rel).is_file(), (
        f"{DOC_REL} declares Layer 2 shipped and names `{rel}`, but that file "
        "does not exist.\n"
        "  Layer 2 is a status claim about CI: the doc asserts these two files "
        "run on every PR.\n"
        "  A missing one means the protocol advertises a conformance defense "
        "that is not there."
    )


def test_q301c_layer2_files_are_named_in_the_doc():
    """The two Layer-2 paths are named in the doc, not just known to this guard."""
    doc = _doc()
    for rel in LAYER2_FILES:
        assert f"`{rel}`" in doc, (
            f"{DOC_REL}: Layer 2 no longer names `{rel}`. This guard resolves the "
            "doc's claims against the repo; if the doc stops making the claim, "
            "re-anchor the guard (or retire it) rather than letting it assert "
            "something the page does not say."
        )


# ---------------------------------------------------------------------------
# Q-302: the vendored canonical spec and its recorded upstream sha
# ---------------------------------------------------------------------------

CANONICAL_REL = "specs/canonical/attractor-spec-canonical.md"
LEDGER_REL = "SPEC_CONFORMANCE.md"


def _recorded_upstream_sha() -> str:
    """The sha Layer 0 records as the canonical pin."""
    doc = _doc()
    match = re.search(
        r"pinned byte-for-byte to\s*\n?\s*`strongdm/attractor`\s*@\s*\*\*`([0-9a-f]{7,40})`\*\*",
        doc,
    )
    assert match is not None, (
        f"{DOC_REL}: could not find Layer 0's canonical sha pin (the "
        "'pinned byte-for-byte to `strongdm/attractor` @ **`<sha>`**' sentence). "
        "Either the sentence was reworded -- re-anchor this pattern -- or the "
        "pin was dropped, which would leave Layer 0's normative claim unsourced."
    )
    return match.group(1)


def test_q302_canonical_spec_exists():
    """Layer 0's normative text must actually be vendored in this checkout."""
    assert (_root() / CANONICAL_REL).is_file(), (
        f"{DOC_REL} Layer 0 names `{CANONICAL_REL}` as the vendored normative "
        "text that settles doc-vs-engine disputes about the spec, but the file "
        "is absent. Layer 0 is the base of the drift model; without the file "
        "there is nothing for Layers 1-2 to be measured against."
    )


def test_q302b_recorded_sha_matches_the_ledger_pin():
    """The sha the doc records is the sha the ledger's SYNC-1 row records."""
    sha = _recorded_upstream_sha()
    ledger_path = _root() / LEDGER_REL
    assert ledger_path.is_file(), (
        f"{DOC_REL} cites `{LEDGER_REL}` (`SYNC-1`) as the home of the canonical "
        f"pin, but {LEDGER_REL} does not exist in this checkout."
    )
    ledger = ledger_path.read_text(encoding="utf-8")

    sync_rows = [ln for ln in ledger.splitlines() if re.match(r"^\|\s*SYNC-1\s*\|", ln)]
    assert sync_rows, (
        f"{LEDGER_REL}: no `SYNC-1` row found, but {DOC_REL} Layer 0 cites it as "
        "where the canonical pin is recorded. Either the ledger row was renamed "
        "-- update the doc's citation in the same PR -- or the pin's record is gone."
    )
    assert any(sha in row for row in sync_rows), (
        f"CANONICAL PIN DRIFT: {DOC_REL} records the vendored spec as pinned to "
        f"`{sha}`, but {LEDGER_REL}'s SYNC-1 row does not name that sha:\n"
        + "".join(f"    {row}\n" for row in sync_rows)
        + "  Upstream movement is a SYNC event (Layer 0): re-vendor, then re-read\n"
        "  every ledger entry whose disposition depended on the old text. If the\n"
        "  re-vendor happened, this doc's sha was left behind -- update it and\n"
        "  add the Changelog entry section 5 requires."
    )


# ---------------------------------------------------------------------------
# Q-303: the Changelog the meta-protocol requires
# ---------------------------------------------------------------------------

_CHANGELOG_ENTRY_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)


def test_q303_changelog_exists_with_at_least_one_dated_entry():
    """Section 5 requires amendments be recorded, dated, in a Changelog."""
    doc = _doc()
    assert re.search(r"^##\s+Changelog\s*$", doc, re.MULTILINE), (
        f"{DOC_REL}: the `## Changelog` section is gone. Section 5 makes it "
        "load-bearing -- 'Amendments are recorded in the Changelog at the bottom "
        "of this file, dated, with the evidence named. The Changelog is the "
        "amendment history; the sections above are only ever the current state.' "
        "Without it the doc has no amendment history and the meta-protocol "
        "cannot be satisfied."
    )
    changelog = doc.split("## Changelog", 1)[1]
    entries = _CHANGELOG_ENTRY_RE.findall(changelog)
    assert entries, (
        f"{DOC_REL}: the Changelog section carries no dated `### YYYY-MM-DD` "
        "entry. Section 5 requires every amendment to be recorded and dated; an "
        "empty changelog means either the history was dropped or entries stopped "
        "using the dated heading form this guard (and every reader) relies on."
    )


# ---------------------------------------------------------------------------
# Q-304: the captured vision the protocol reads against
# ---------------------------------------------------------------------------


def _vision_path() -> Path:
    return _root() / VISION_REL


def _vision() -> str:
    return _vision_path().read_text(encoding="utf-8")


def test_q304_vision_doc_exists():
    """Layer 3 and section 4 both read against a *file*, not an inference."""
    assert _vision_path().is_file(), (
        f"{DOC_REL} names `{VISION_REL}` as the repo's captured vision -- Layer 3 "
        "reads against it, and section 4's observation duty is defined as "
        "observations *against it*.\n"
        f"  `{VISION_REL}` does not exist in this checkout, which leaves both "
        "pointing at nothing.\n"
        "  Either the file moved -- update every citation in the same PR -- or "
        "the vision capture was reverted, in which case sections 3-4 and Layer 3 "
        "have to be rewritten with it."
    )


def test_q304b_vision_changelog_exists_with_a_dated_entry():
    """VISION.md's own meta-protocol mandates a dated amendment history."""
    doc = _vision()
    assert re.search(r"^##\s+Changelog\s*$", doc, re.MULTILINE), (
        f"{VISION_REL}: the `## Changelog` section is gone. That page's "
        "'Maintaining this document' section makes it load-bearing -- amendments "
        "land there, dated, with the evidence named, and the sections above are "
        "only ever the current state. Without it the vision has no amendment "
        "history and cannot be amended per its own rule."
    )
    changelog = doc.split("## Changelog", 1)[1]
    entries = _CHANGELOG_ENTRY_RE.findall(changelog)
    assert entries, (
        f"{VISION_REL}: the Changelog carries no dated `### YYYY-MM-DD` entry. "
        "Every amendment to the vision requires the maintainer's explicit word "
        "and a dated record of it; an empty changelog means either the history "
        "was dropped or entries stopped using the dated heading form."
    )


# ---------------------------------------------------------------------------
# Q-305: VISION.md states the governing rule, and its citations resolve
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_q305_vision_names_the_decision_matrix():
    """The vision's governing rule has to be *in* the vision."""
    doc = _vision()
    assert "decision matrix" in doc, (
        f"{VISION_REL}: the page no longer says 'decision matrix'.\n"
        "  The maintainer's 2026-08-15 ruling makes it THE governing rule of "
        "this project -- the three postures toward the strongdm/attractor "
        "nlspec -- and `docs/QUALITY_PROTOCOL.md` section 3 defers to this page "
        "as where it is stated as such.\n"
        "  If the rule was renamed, re-anchor this guard and section 3's "
        "cross-reference in the same PR; if it was removed, that is a vision "
        "amendment and needs the maintainer's explicit word plus a Changelog "
        "entry."
    )


def test_q305b_vision_relative_links_resolve():
    """Every repo-relative file the vision cites is a file that exists."""
    broken = []
    for target in _MD_LINK_RE.findall(_vision()):
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        resolved = (_vision_path().parent / target).resolve()
        if not resolved.exists():
            broken.append(f"{target} (looked for {resolved})")
    assert not broken, (
        f"{VISION_REL} links to files that do not exist:\n"
        + "".join(f"  - {b}\n" for b in broken)
        + "  The vision is a short page whose authority rests on tracing every\n"
        "  claim to a real source. A dead citation is the page asserting a\n"
        "  provenance it does not have. Update the link in the PR that moved\n"
        "  the file."
    )


# ---------------------------------------------------------------------------
# Q-306: the protocol carries the decision matrix and names the label
# ---------------------------------------------------------------------------

OBSERVATION_LABEL = "vision-observation"


def test_q306_protocol_carries_the_decision_matrix_section():
    """Section 3's heading, anchored on the title rather than its number."""
    assert re.search(r"^##\s+\d+\.\s+The decision matrix\s*$", _doc(), re.MULTILINE), (
        f"{DOC_REL}: no '## <n>. The decision matrix' section heading.\n"
        "  That section is where each matrix tier's toll is defined -- section 1's "
        "review duties, section 2's evidence table and `docs/VISION.md` all defer "
        "to it.\n"
        "  The heading is matched by title, not by number, so renumbering the page "
        "is fine; removing or renaming the section is not, and needs the "
        "maintainer's word plus a Changelog entry."
    )


def test_q306b_protocol_names_the_observation_label():
    """The observation convention is defined by a label it must name."""
    assert f"`{OBSERVATION_LABEL}`" in _doc(), (
        f"{DOC_REL}: the literal label string `{OBSERVATION_LABEL}` is not in the "
        "page.\n"
        "  Section 4's convention is mechanically 'file an issue carrying this "
        "label'; if the page stops naming it, nobody can file one correctly and "
        "the Layer-3 triage input silently empties.\n"
        "  The label's existence on GitHub is repo-external state this suite "
        "cannot see -- what it guards is that the doc still states the name."
    )


def test_q306c_vision_points_at_the_observation_convention():
    """VISION.md delegates observation capture; the target must still be there."""
    vision = _vision()
    assert "if you see something, do something" in vision.lower(), (
        f"{VISION_REL}: the page no longer points at the "
        f'"if you see something, do something" convention.\n'
        f"  Its 'Maintaining this document' section delegates observation capture "
        f"to {DOC_REL} rather than restating it; dropping the pointer leaves "
        "readers with a duty and no procedure."
    )
    assert re.search(
        r"^##\s+\d+\.\s+\"If you see something, do something\"\s*$",
        _doc(),
        re.MULTILINE,
    ), (
        f"{DOC_REL}: no '## <n>. \"If you see something, do something\"' section "
        f"heading, but {VISION_REL} points readers at it for how observations are "
        "captured, triaged and resolved. Re-anchor both in the same PR."
    )


# ---------------------------------------------------------------------------
# Q-307: the decision matrix reads identically on both pages
# ---------------------------------------------------------------------------

# The canonical articulation of the maintainer's 2026-08-15 decision-matrix
# ruling.  It is authored prose, not a quotation -- the maintainer ruled the
# same day that his raw words be replaced with an accurate representation of
# what he was communicating (QUALITY_PROTOCOL.md Changelog entry 5,
# VISION.md Changelog entry 2).  One paragraph, two homes; these anchors are
# its first and last sentences.
_MATRIX_START = "Every change here is weighed against the `strongdm/attractor` nlspec"
_MATRIX_END = "That gradient is the steering rule of this project."


def _flatten_quote(text: str) -> str:
    """Strip blockquote markers and collapse whitespace.

    Kept blockquote-tolerant so the check survives either page choosing to
    set the paragraph as a quote block again, and so a re-wrap at a different
    column fails on meaning rather than on formatting.
    """
    unquoted = [re.sub(r"^\s*>\s?", "", line) for line in text.splitlines()]
    return re.sub(r"\s+", " ", " ".join(unquoted)).strip()


def _extract_articulation(text: str, rel: str) -> str:
    flat = _flatten_quote(text)
    start = flat.find(_MATRIX_START)
    assert start != -1, (
        f"{rel}: the decision matrix's canonical articulation (maintainer "
        f"ruling, 2026-08-15) is not stated here -- could not find "
        f"{_MATRIX_START!r}.\n"
        "  Both pages carry the identical paragraph on purpose: the vision "
        "states it as the governing rule, the protocol prices each tier of it. "
        "A second, differently-worded statement of the same rule is exactly "
        "the drift this check exists to catch."
    )
    end = flat.find(_MATRIX_END, start)
    assert end != -1, (
        f"{rel}: the articulation starts but does not end with "
        f"{_MATRIX_END!r}. That closing sentence is what makes the paragraph a "
        "steering rule rather than a description; the third posture -- real "
        "but lesser resistance in spec-silent territory -- sits just above it "
        "and is the one most easily dropped, and it is the one that "
        "distinguishes this matrix from a two-way conform/diverge rule."
    )
    return flat[start : end + len(_MATRIX_END)]


def test_q307_decision_matrix_reads_identically_on_both_pages():
    """One rule, two homes -- they must not drift apart."""
    from_protocol = _extract_articulation(_doc(), DOC_REL)
    from_vision = _extract_articulation(_vision(), VISION_REL)
    assert from_protocol == from_vision, (
        "DECISION-MATRIX DRIFT: the decision matrix reads differently on the "
        "two pages that state it.\n"
        f"  {DOC_REL}:\n    {from_protocol}\n"
        f"  {VISION_REL}:\n    {from_vision}\n"
        "  This is the cost of stating one rule in two places, and the reason\n"
        "  this check exists: editing one copy leaves the other stating a\n"
        "  different rule under the same name.\n"
        "  Fix the copy that drifted -- do not 'meet in the middle'. If the\n"
        "  rule itself changed, that is an amendment: the maintainer's\n"
        "  explicit word, both pages updated, both Changelogs entered."
    )
