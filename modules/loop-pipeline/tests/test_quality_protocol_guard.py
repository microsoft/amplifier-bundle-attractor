"""Drift guard for docs/QUALITY_PROTOCOL.md -- Q-300..Q-303.

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
  Q-303  the Changelog section the meta-protocol (section 5) requires
         -> the section exists and carries at least one dated entry

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
    the newest amendment.  No mechanical check can know that; section 5's
    review owns it.
  - This module skips wholesale when ``docs/QUALITY_PROTOCOL.md`` is absent,
    so the loop-pipeline suite still runs in a module-only/partial checkout.

Reference: ``docs/QUALITY_PROTOCOL.md`` section 3 (Layers 0-2) and section 5
(the meta-protocol, which is where this guard's own adoption condition is
recorded).
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
