"""Drift guard for docs/OPERATIONS.md and docs/VISION.md -- Q-300..Q-312.

# --- Relocated from modules/loop-pipeline/tests/test_quality_protocol_guard.py as part of the repo
# split's Track A (root guard harness, DESIGN-repo-split.md §1.4/§5#2). This
# guard asserts on the OPINIONATED layer (repo-root docs/examples/skills/
# agents/context/bundles/behaviors), not on engine behavior, so it now runs
# from the repo-root `tests/` suite in CI instead of riding along inside the
# loop-pipeline module's own test tree. ---

# --- RE-AIMED 2026-09-02 (converge alignment).  `docs/QUALITY_PROTOCOL.md`
# was retired and redistributed: the protocol half was a local restatement of
# the ratified converge PROTOCOL v2, and the repo-specific operating practice
# moved to `docs/OPERATIONS.md`.  This module was NOT deleted -- every claim it
# held still needs holding, and the claims resolve against the repo rather than
# against any one page.  What changed: the page it reads is OPERATIONS.md;
# Q-303 retired with a reason (see below); Q-307 was re-aimed from "two copies
# agree" to "one home, matching a recorded constant"; Q-312 was re-aimed from a
# Changelog entry to the incidents that Changelog entry recorded.  The module
# filename is kept so the rename does not obscure the re-aim in `git log`. ---

Guards this repo's operating practice against its own external references
going stale.  The page is binding on contributors and on AI coding agents
working here, and its section 2 "Docs making factual claims" row demands a
guard for exactly this class of page.

**What makes these assertions non-tautological.**  Every check reads a claim
*from the doc* and resolves it against *the repository*.  A page-only
assertion ("the doc says five guards") would pass forever and fail only when
someone edited the doc, which is the one case that needs no guard.  These
fail when the **repo** moves underneath the doc -- a guard file renamed or
deleted, the matrix relocated, the canonical spec re-vendored to a new
upstream sha -- which is the case that actually produces a lying protocol.

Claims guarded, and what each is resolved against:

  Q-300  every ``test_*.py`` the doc names by name
         -> the file exists (bare names resolve under ``tests/``), EXCEPT the
            ones listed in ``ENGINE_RESIDENT_GUARDS``, which moved to
            ``amplifier-bundle-dot-runner`` in the P4 slim
  Q-300c the moved guards are still named on the page, alongside the repo they
         moved to -- so "it moved" never becomes indistinguishable from
         "it was quietly dropped"
  Q-300d none of the moved guards has quietly returned to this repo while
         still sitting inside Q-300's exemption list
  Q-301  the Layer-2 matrix document
         (``specs/conformance/attractor-matrix.yaml``), which the doc states as
         **shipped** -> the file exists on disk, the status names
         ``amplifier-bundle-dot-runner`` as where it now executes (Q-301), and
         the page names that runner by path (Q-301d).  RE-AIMED 2026-09-06 in
         the P4 slim: the runner left with the engine, so demanding it locally
         would be demanding a file this repo deliberately no longer has
  Q-302  the vendored canonical spec and its recorded upstream sha
         -> ``specs/canonical/attractor-spec-canonical.md`` exists, and the
            sha the doc records appears in ``SPEC_CONFORMANCE.md`` at the
            ``SYNC-1`` row the doc names as the pin's home
  Q-303  RETIRED 2026-09-02.  It asserted that this page carried a dated
         ``## Changelog``, enforcing the retired section 8's own amendment
         rule.  Amendment recording is converge PROTOCOL v2's rule now, not
         this page's, and the surviving page deliberately carries no second
         amendment history.  The claim is not silently dropped: the vision's
         Changelog -- the one this repo still keeps -- is pinned by Q-304b.
  Q-304  the captured vision the practice reads against
         -> ``docs/VISION.md`` exists and carries its own dated Changelog
  Q-305  VISION.md states the decision matrix, and the repo-relative files
         it cites resolve -> every relative markdown link lands on a real file
  Q-306  the page carries the decision-matrix TOLLS section and names the
         ``vision-observation`` label the observation convention depends on
         -> both are present in the page
  Q-307  the decision matrix's canonical articulation has exactly ONE home
         -> it appears in ``docs/VISION.md``, matches a constant recorded
            here, and appears in no other markdown file in the repo
  Q-308  the pre-publication leak-defense section and its three layers
         -> the section heading is present and each layer's run-in heading
            is on the page, named
  Q-309  the leak-lens reviewer's outsider brief, which the doc quotes
         **verbatim** and a reviewer is expected to be handed word-for-word
         -> the exact sentence pair appears on the page
  Q-310  the two reference implementations the section names as the shipped
         embodiment of its layers -> both files exist on disk
  Q-311  the PR checklist line that turns the leak-lens duty into a
         per-PR prompt -> ``.github/PULL_REQUEST_TEMPLATE.md`` carries it
  Q-312  the two measured incidents the leak-defense section exists on
         -> both dates are named on the page as its evidence

Honest limits:
  - Q-300 extracts filenames by regex over backticked prose.  A guard file
    referred to only in running text without backticks is invisible here.
    That is the same tradeoff the sibling doc guards make, and the failure
    direction is safe: an unquoted name is unguarded, never falsely red.
  - Q-302 checks that the sha *string* the doc records is the one the ledger
    records.  It does not re-fetch upstream or re-verify the vendored bytes;
    the matrix's SYNC row owns the byte-level sha256 pin.  This check owns
    the narrower claim that the doc and the ledger name the same commit.
  - Q-304b asserts a dated entry exists, not that the newest entry describes
    the newest amendment.  No mechanical check can know that; the converge
    amendment protocol's own review owns it.
  - Q-304..Q-307 deliberately do **not** guard the vision's prose.  A vision
    is judgment, not a set of fact claims about code; a guard over its
    wording would pin taste rather than truth, and would fail exactly when
    someone improved it.  What they guard is its *structure* (it exists, it
    has an amendment history, it states its governing rule, its citations
    resolve) and the one thing that can silently rot -- the decision
    matrix's articulation.  That risk changed shape on 2026-09-02 rather
    than disappearing: with the second copy retired, the failure mode is no
    longer "two copies disagree" but "the one copy was quietly edited" or
    "a second home crept back in".  Q-307 now covers both.
  - The ``vision-observation`` label itself is repo-external state (GitHub),
    which these suites cannot and should not reach.  Q-306 asserts the doc
    *states the label name*, which is the part that can drift in-tree.
  - Q-308/Q-309/Q-311 are *presence* checks over authored prose, and are
    deliberately anchored on the load-bearing strings rather than on whole
    paragraphs: the layer names (which are the model), the outsider brief
    (which a reviewer is handed word-for-word, so a paraphrase is a real
    change), and the checklist line's distinguishing phrases.  Rewording
    the surrounding argument is free; deleting or silently rewriting the
    parts a reader acts on is not.
  - Q-310 overlaps Q-300 for the one reference implementation that is a
    ``test_*.py`` file, and that is intentional.  Q-300 would catch it as
    one entry in an anonymous list; Q-310 fails naming *which* layer of
    section 7 lost its shipped embodiment, which is the message a reader
    of the failure actually needs.  ``scrub_secrets.py`` is not a test
    file and is reachable only through Q-310.
  - Q-312 resolves against the page itself rather than against the repo,
    which makes it a structural check rather than a code-movement one.  It
    is kept because the leak-defense section is argued entirely from two
    measured incidents: strip the dates and the section reads as policy
    someone preferred, which is the exact argument its own retirement
    condition forbids.  It previously pinned the Changelog entry that
    recorded those incidents; with the Changelog retired it pins the
    incidents themselves, which is the load-bearing half of what it held.
  - No identity value appears anywhere in this module, and none may be
    added.  The doctrine these checks guard is that a deny-list of
    identity terms publishes the terms it forbids; a guard over that
    doctrine that hardcoded one would be the same mistake, one level up.
  - This module skips wholesale when ``docs/OPERATIONS.md`` is absent, so a
    module-only/partial checkout still runs the rest of its suite.

Reference: ``docs/OPERATIONS.md`` section 5 (Layers 0-3), section 8
(machinery hygiene, which is where this guard is named), and sections 3-4
(the decision matrix's tolls and the observation duty).  The retired
``docs/QUALITY_PROTOCOL.md`` carries the tombstone mapping every former
section to its new home.
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
        if (candidate / "docs").is_dir() and (candidate / "bundle.md").is_file():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()
DOC_REL = "docs/OPERATIONS.md"
VISION_REL = "docs/VISION.md"
DOC_PATH = (BUNDLE_ROOT / DOC_REL) if BUNDLE_ROOT is not None else None
# RE-AIMED 2026-09-06 (the P4 slim, attractor-28x).  Bare `test_*.py` names in
# the doc used to resolve under `modules/loop-pipeline/tests/`, because that is
# where Layer 1 lived.  That module left this repo; Layer 1's surviving guards
# are the repo-root ones, so a bare name now resolves under `tests/`.
TESTS_DIR_REL = "tests"

#: Guard files the doc names that live in `amplifier-bundle-dot-runner`, not
#: here.  Two Layer-1 guards went with the engine in the P4 slim because they
#: could not be decoupled from the live parser/linter, and Layer 2's runner
#: went with it too.  Q-300 must not demand these exist locally; Q-300c holds
#: the other half of the claim -- that the doc keeps saying where they went,
#: and that none of them has quietly reappeared here without being taken off
#: this list deliberately.
ENGINE_RESIDENT_GUARDS: tuple[str, ...] = (
    "test_extensions_ledger_integrity.py",
    "test_examples_lint_clean.py",
    "ledger/checks/test_spec_conformance_matrix.py",
)
ENGINE_HOME = "amplifier-bundle-dot-runner"

pytestmark = pytest.mark.skipif(
    DOC_PATH is None or not DOC_PATH.is_file(),
    reason=(
        f"{DOC_REL} not present in this checkout -- the operating practice "
        "ships in the bundle repo, not in the loop-pipeline module "
        "distribution. Nothing to guard here; the rest of the module suite is "
        "unaffected."
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
    """A bare filename means the root tests dir; a path means itself."""
    return _root() / (name if "/" in name else f"{TESTS_DIR_REL}/{name}")


def _is_engine_resident(name: str) -> bool:
    """True for a guard the doc names as living in the engine repo."""
    return name in ENGINE_RESIDENT_GUARDS or name.rsplit("/", 1)[-1] in {
        n.rsplit("/", 1)[-1] for n in ENGINE_RESIDENT_GUARDS
    }


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
        if not _is_engine_resident(name)
        and not _resolve_named_test_file(name).is_file()
    ]
    assert not missing, (
        f"{DOC_REL} names guard-test files that do not exist:\n"
        + "".join(f"  - {m}\n" for m in missing)
        + "  The protocol's authority rests on the machinery it names being real.\n"
        "  A named-but-absent guard is the protocol claiming an enforcement it\n"
        "  does not have. Either the file was renamed/deleted -- in which case\n"
        "  update the doc in the same PR -- or the doc names it wrongly.\n"
        f"  (Bare filenames are resolved against {TESTS_DIR_REL}/. Guards that\n"
        f"  genuinely live in {ENGINE_HOME} belong in ENGINE_RESIDENT_GUARDS in\n"
        "  this file, and are then held by Q-300c instead.)"
    )


def test_q300b_the_five_layer1_guards_are_among_them():
    """The Layer-1 table's four resident guards specifically, named as a floor.

    Q-300 is generic over whatever the doc names; this pins the five the
    Layer-1 table is *about*, so silently dropping a row from that table
    fails here rather than shrinking the guarded set unnoticed.
    """
    # RE-AIMED 2026-09-06 (the P4 slim): the table is four rows now.
    # test_extensions_ledger_integrity.py and test_examples_lint_clean.py moved
    # to the engine repo and are pinned by Q-300c, not here.
    expected = {
        "test_doc_consistency.py",
        "test_engine_semantics_doc_guard.py",
        "test_explainer_doc_guard.py",
        "test_quality_protocol_guard.py",
    }
    named = set(_named_test_files())
    # Path-qualified mentions count as naming the file too.
    named |= {n.rsplit("/", 1)[-1] for n in named}
    missing = sorted(expected - named)
    assert not missing, (
        f"{DOC_REL}: the Layer-1 table no longer names {missing}.\n"
        "  Layer 1 IS its deterministic guards; dropping one from the\n"
        "  table without retiring the guard (section 5's retirement protocol)\n"
        "  leaves the doc describing a defense narrower than the one that runs.\n"
        "  If a guard was genuinely retired, update this expected set in the\n"
        "  same PR and record the retirement in the Changelog."
    )


# ---------------------------------------------------------------------------
# Q-300c: the guards that LEFT are still accounted for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ENGINE_RESIDENT_GUARDS)
def test_q300c_moved_guards_are_still_named_with_their_new_home(name: str):
    """A guard that moved must still be named here, next to where it went.

    Q-300 stops demanding these exist locally. That exemption is only honest
    if the page keeps saying they exist SOMEWHERE -- otherwise "it moved" is
    indistinguishable from "it was quietly dropped", which is exactly the
    silent-guard-loss failure the P4 slim was designed to avoid.
    """
    doc = _doc()
    bare = name.rsplit("/", 1)[-1]
    assert bare in doc, (
        f"{DOC_REL} no longer names `{bare}` at all. It is listed in this "
        f"guard's ENGINE_RESIDENT_GUARDS as having moved to {ENGINE_HOME}. "
        "Either the page dropped the row -- restore it, a moved guard is still "
        "a guard this repo's defense depends on -- or the guard was genuinely "
        "retired, in which case remove it from ENGINE_RESIDENT_GUARDS in the "
        "same PR and say so in the Changelog."
    )
    assert ENGINE_HOME in doc, (
        f"{DOC_REL} names `{bare}` but never names {ENGINE_HOME}. A reader is "
        "then told a guard exists with no way to find it. Name the repo the "
        "guard moved to."
    )


@pytest.mark.parametrize("name", ENGINE_RESIDENT_GUARDS)
def test_q300d_moved_guards_have_not_quietly_returned(name: str):
    """None of the moved guards exists locally under a stale exemption.

    If one comes back to this repo, it must come back into Q-300's real
    file-existence check -- not sit forever inside an exemption list that
    stopped describing reality.
    """
    local = _resolve_named_test_file(name)
    assert not local.is_file(), (
        f"{local.relative_to(_root())} exists in this repo, but "
        f"`{name}` is still listed in ENGINE_RESIDENT_GUARDS as living in "
        f"{ENGINE_HOME}. Remove it from that list so Q-300 guards it for real "
        "again, and update the Layer-1 table in the same PR."
    )


# ---------------------------------------------------------------------------
# Q-301: the Layer-2 files the doc declares shipped must exist
# ---------------------------------------------------------------------------

# RE-AIMED 2026-09-06 (the P4 slim, attractor-28x).  Layer 2 shipped as a
# matrix DOCUMENT plus a RUNNER.  The runner went with the engine; the document
# is frozen here and still cited by SPEC_CONFORMANCE.md and docs/VISION.md.  So
# this is the one file Layer 2 still claims to have on disk; the runner is held
# by Q-300c/Q-300d as an engine-resident guard, and Q-301d below pins that the
# page names where it executes.
LAYER2_FILES = ("specs/conformance/attractor-matrix.yaml",)
LAYER2_RUNNER_REL = "ledger/checks/test_spec_conformance_matrix.py"


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
        "'shipped'. The matrix document is on disk and its runner runs in "
        f"{ENGINE_HOME}'s CI, so a non-shipped status is the doc understating "
        "what runs. Update the status or retire the files."
    )
    assert ENGINE_HOME.lower() in status, (
        f"{DOC_REL}: Layer 2's status reads {match.group(1).strip()!r}, which "
        f"does not name {ENGINE_HOME}. Since the P4 slim, nothing in THIS repo "
        "executes the matrix -- a status that says 'shipped' without saying "
        "where it ships is the aspirational-contract failure this layer exists "
        "to prevent."
    )


def test_q301d_layer2_names_its_executing_runner():
    """Layer 2 names the runner and the repo that actually executes it."""
    doc = _doc()
    assert f"`{LAYER2_RUNNER_REL}`" in doc, (
        f"{DOC_REL}: Layer 2 no longer names `{LAYER2_RUNNER_REL}`, the runner "
        f"that executes the matrix in {ENGINE_HOME}. Layer 2's whole claim is "
        "that the matrix is EXECUTED; naming the document without naming the "
        "runner turns that into an unfalsifiable claim."
    )


@pytest.mark.parametrize("rel", LAYER2_FILES)
def test_q301b_layer2_files_exist(rel: str):
    """Every file Layer 2 names as present in THIS repo must be present."""
    assert (_root() / rel).is_file(), (
        f"{DOC_REL} declares Layer 2 shipped and names `{rel}`, but that file "
        "does not exist.\n"
        "  Layer 2 is a status claim: the doc asserts this matrix document is "
        "on disk here and\n"
        f"  is executed by {ENGINE_HOME}'s runner.\n"
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
        "  add the Changelog entry section 8 requires."
    )


# ---------------------------------------------------------------------------
# Q-303: RETIRED 2026-09-02 -- the page's own Changelog
# ---------------------------------------------------------------------------
#
# Q-303 asserted that this page carried a `## Changelog` with at least one
# dated entry.  It was enforcing the retired `docs/QUALITY_PROTOCOL.md`
# section 8's own rule on itself: "amendments are recorded in the Changelog,
# dated, with the evidence named".
#
# That rule is now converge PROTOCOL v2's, not this page's.  The surviving
# `docs/OPERATIONS.md` deliberately carries no Changelog: a second amendment
# history is precisely the "one claim, N homes" duplication this whole
# redistribution retired.  Re-aiming Q-303 at a section that is meant not to
# exist would be the guard asserting something the page does not say -- the
# failure mode this module's own docstring names as the thing to avoid.
#
# The claim is not silently dropped.  The amendment history this repo does
# keep is the vision's, and it is still pinned: see Q-304b below.  The
# retired page's own history (entries 1-8, 2026-08-15..2026-08-19) is in git.
#
# `_CHANGELOG_ENTRY_RE` is retained -- Q-304b uses it.

_CHANGELOG_ENTRY_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)


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
    """Section 3's heading, anchored on the title rather than its number.

    Re-anchored 2026-09-02 on "The decision matrix's tolls".  The rename is
    the point of the redistribution, not incidental to it: the *rule* now has
    one home (``docs/VISION.md``) and this page prices it.  A section here
    still titled "The decision matrix" would be the second home reasserting
    itself under the old name.
    """
    assert re.search(
        r"^##\s+\d+\.\s+The decision matrix's tolls\s*$", _doc(), re.MULTILINE
    ), (
        f"{DOC_REL}: no \"## <n>. The decision matrix's tolls\" section heading.\n"
        "  That section is where each matrix tier's toll is defined -- section 1's "
        "review duties and section 2's evidence table both defer to it, and "
        "`docs/VISION.md` states the rule it prices.\n"
        "  The heading is matched by title, not by number, so renumbering the page "
        "is fine; removing or renaming the section is not.\n"
        "  If it was renamed back to plain 'The decision matrix', check first "
        "that the rule's articulation has not come back with it -- Q-307 owns "
        "that, and one home is the invariant."
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
# Q-307: the decision matrix has exactly ONE home, and it is unchanged
# ---------------------------------------------------------------------------
#
# RE-AIMED 2026-09-02.  This check used to assert that the articulation read
# byte-identically on `docs/QUALITY_PROTOCOL.md` and `docs/VISION.md` -- a
# guard whose entire existence was owed to the text having two homes.  The
# protocol page retired and the second copy went with it; `docs/VISION.md` is
# now the single home.
#
# Deleting the guard with the duplication would have been wrong.  Removing a
# copy removes the "two copies disagree" failure but leaves two others, and
# they are the ones that actually rot a governing rule:
#
#   1. The one remaining copy is quietly edited.  Nothing else in the repo
#      states the rule, so nothing else can contradict the edit -- the exact
#      condition under which a silent change is invisible.  Pinning the text
#      against a constant recorded HERE (not read from the page) is what
#      keeps that loud.  This is deliberately the one place in this module
#      where an assertion is anchored on authored prose rather than resolved
#      against code: the rule IS prose, and it is a maintainer ruling, so
#      "someone edited it without the owner" is a real and detectable event.
#   2. A second home creeps back in.  A future page restates the paragraph
#      "for convenience" and the original duplication is recreated under a
#      new name.  The corpus scan below fails that on arrival rather than
#      years later when the two have drifted.
#
# Changing the rule is still entirely allowed -- it is an amendment: the
# owner's explicit word, the vision's Changelog, and this constant updated in
# the same PR.  What the guard forbids is doing it silently.

#: The canonical articulation of the maintainer's 2026-08-15 decision-matrix
#: ruling, recorded verbatim (whitespace-normalized).  Authored prose, not a
#: quotation -- the maintainer ruled the same day that his raw words be
#: replaced with an accurate representation of what he was communicating
#: (VISION.md Changelog entry 2).
_MATRIX_TEXT = (
    "Every change here is weighed against the `strongdm/attractor` nlspec -- "
    "not code alone, but behavior, philosophy, decision-making, "
    "design-thinking, process and documentation alike. Movement that brings "
    "this project **more aligned** with the spec is the easy path: supported "
    "by default, carrying the presumption of yes. Movement that would "
    "**drift** us away from the spec is made genuinely hard and is readily "
    "pushed back on -- permitted only on measured evidence, and only as a "
    "loud, ledgered divergence. Movement into territory the spec **does not "
    "address** meets real resistance, though less of it: the silence has to "
    "be argued rather than assumed, and what ships there stays additive and "
    "non-interfering. That gradient is the steering rule of this project."
)

_MATRIX_START = "Every change here is weighed against the `strongdm/attractor` nlspec"
_MATRIX_END = "That gradient is the steering rule of this project."

#: Where the single home is required to be.
MATRIX_HOME_REL = VISION_REL

#: Markdown trees excluded from the "exactly one home" scan, with reasons.
#: `.github/capsule-pipeline/vendor/` is vendored third-party fixture
#: material, not authored repo doctrine; `docs/QUALITY_PROTOCOL.md` is the
#: tombstone of the retired second home and is allowed to *describe* the
#: retirement without restating the rule (if it ever restates it, that IS a
#: second home and this scan should fail).
_MATRIX_SCAN_SKIP_PREFIXES = (".github/capsule-pipeline/vendor/",)


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
        "  This page is the rule's single home: it states the governing rule, "
        "and `docs/OPERATIONS.md` section 3 prices each tier of it without "
        "restating it. If the paragraph moved, move this guard's home with it "
        "in the same PR -- do not let the rule become homeless, and do not "
        "answer a move by adding a second copy."
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


def test_q307_decision_matrix_is_unchanged_at_its_single_home():
    """The one home states the rule, and states it as recorded."""
    found = _extract_articulation(_vision(), MATRIX_HOME_REL)
    assert found == _MATRIX_TEXT, (
        "DECISION-MATRIX DRIFT: the canonical articulation in "
        f"{MATRIX_HOME_REL} no longer matches the text recorded in this "
        "guard.\n"
        f"  on the page:\n    {found}\n"
        f"  recorded here:\n    {_MATRIX_TEXT}\n"
        "  This paragraph is a maintainer ruling (2026-08-15) and the governing\n"
        "  rule of the project. It has exactly one home, which means nothing\n"
        "  else in the repo can contradict an edit to it -- this constant is\n"
        "  what makes a silent edit loud.\n"
        "  Changing the rule is allowed, and is an amendment: the owner's\n"
        "  explicit word, a dated entry in the vision's Changelog, and this\n"
        "  constant updated in the same PR. Editing the page alone is not.\n"
        "  (Whitespace and blockquote markers are normalized before comparing,\n"
        "  so re-wrapping the paragraph is free; changing the words is not.)"
    )


def test_q307b_decision_matrix_has_no_second_home():
    """No other markdown file in the repo restates the rule.

    The retired duplication is not allowed to reappear under a new name.  A
    second copy is not redundancy -- it is a rule that can drift from itself
    while both copies keep the same title.
    """
    root = _root()
    others = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if rel == MATRIX_HOME_REL or rel.startswith(_MATRIX_SCAN_SKIP_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        if _MATRIX_START in _flatten_quote(text):
            others.append(rel)

    assert not others, (
        "DECISION-MATRIX SECOND HOME: the canonical articulation appears "
        f"outside {MATRIX_HOME_REL}:\n"
        + "".join(f"  - {o}\n" for o in others)
        + "  One rule, one home. This exact duplication is what the retired\n"
        "  `docs/QUALITY_PROTOCOL.md` section 3 cost: a guard existed whose\n"
        "  only job was detecting drift that the second copy created.\n"
        f"  Point the new page at {MATRIX_HOME_REL} rather than restating the\n"
        "  rule. If a second home is genuinely wanted, that is a decision to\n"
        "  make explicitly -- and this guard has to be redesigned with it,\n"
        "  not quietly widened."
    )


# ---------------------------------------------------------------------------
# Q-308..Q-312: the pre-publication leak defense
# ---------------------------------------------------------------------------
#
# Section 7 exists because two measured leaks got past the defenses that were
# in place: a live provider key through public run artifacts (2026-08-11), and
# maintainer host-name literals through a new skill's shipped files
# (2026-08-19).  The second is the one that shapes these checks -- BOTH a
# static deny-list guard and a grep-armed adversarial reviewer passed it, and
# only a stranger reading the diff caught it.
#
# ** No identity value is written anywhere below, and none may be added. **
# The doctrine under guard is that a deny-list of identity terms publishes the
# terms it forbids.  A guard over that doctrine that hardcoded one would be
# the same mistake, one level up.

LEAK_SECTION_TITLE = "Pre-publication leak defense"

#: The three layers, by their run-in headings.  These names ARE the model --
#: what each layer matches and, decisively, *where its data lives*.  Layer 2's
#: "DERIVED at runtime, never stored" is the incident-2 lesson in five words.
LEAK_LAYER_HEADINGS = (
    "**Layer 1 -- generic shapes, committed.**",
    "**Layer 2 -- environment identity, DERIVED at runtime, never stored.**",
    "**Layer 3 -- local deny-list, outside the repo.**",
)

#: Quoted verbatim in the doc because it is handed to a reviewer word-for-word.
#: A paraphrase is a different instrument: every clause names a distinct class
#: of identifier, and dropping one silently narrows what the reviewer looks for.
OUTSIDER_BRIEF = (
    "Read this diff as a stranger. List everything that identifies a person, "
    "a machine, an organization, an internal project, or a private process."
)

#: What section 7 points at as the shipped embodiment of its layers.  The
#: first runs all three layers over a skill's own files; the second is the
#: run-artifact-side sibling built after the 2026-08-11 key leak.
LEAK_REFERENCE_IMPLS = (
    "skills/attractor-scout/tests/test_no_real_data_leak.py",
    ".github/capsule-pipeline/scrub_secrets.py",
)

PR_TEMPLATE_REL = ".github/PULL_REQUEST_TEMPLATE.md"

#: The phrases that make the checklist line the leak-review line rather than
#: a generic "be careful" nudge: what it is, what triggers it, and the brief
#: it sends the reviewer to.
PR_TEMPLATE_MARKERS = (
    "Pre-publication leak review",
    "new public content class",
    "outsider brief",
)

#: The two measured incidents the leak defense is argued from.  RE-AIMED
#: 2026-09-02: this used to be a single Changelog date, pinning the entry that
#: recorded them.  With the page's Changelog retired (see Q-303), the entry is
#: gone but the incidents are not -- and the incidents were always the
#: load-bearing half.  Section 7's own retirement condition turns on them:
#: "the duty exists precisely because two defenses passed a real leak".
#: Strip the dates and the section reads as a preference someone held.
LEAK_INCIDENT_DATES = ("2026-08-11", "2026-08-19")


def _flat_doc() -> str:
    """The protocol, blockquote-stripped and whitespace-collapsed.

    Reuses `_flatten_quote` so a re-wrap at a different column, or a decision
    to set a quoted passage as a blockquote (or stop doing so), fails on
    meaning rather than on formatting.
    """
    return _flatten_quote(_doc())


def test_q308_leak_defense_section_exists():
    """The section heading, matched by title so renumbering stays free."""
    assert re.search(
        rf"^##\s+\d+\.\s+{re.escape(LEAK_SECTION_TITLE)}\s*$", _doc(), re.MULTILINE
    ), (
        f"{DOC_REL}: no '## <n>. {LEAK_SECTION_TITLE}' section heading.\n"
        "  That section is the repo's answer to two measured leaks, and section 2's\n"
        "  'New public content class' row and the PR checklist both defer to it for\n"
        "  what the leak-lens review actually is.\n"
        "  Matched by title, not by number, so renumbering the page is fine;\n"
        "  removing or renaming the section is an amendment and needs the\n"
        "  maintainer's word plus a Changelog entry (section 8)."
    )


@pytest.mark.parametrize("heading", LEAK_LAYER_HEADINGS)
def test_q308b_all_three_leak_layers_are_named(heading: str):
    """Each layer named, with the property that makes it non-redundant."""
    assert heading in _doc(), (
        f"{DOC_REL}: the pre-publication leak defense no longer carries\n"
        f"    {heading}\n"
        "  Each layer catches a class the others structurally cannot, and each is\n"
        "  defined as much by WHERE ITS DATA LIVES as by what it matches:\n"
        "    Layer 1  shapes, safe to commit because a shape reveals no value\n"
        "    Layer 2  identity DERIVED at runtime -- the answer to a deny-list\n"
        "             being self-defeating, since committing a term publishes it\n"
        "    Layer 3  the remaining terms, on local disk, never in the repo\n"
        "  Dropping a layer from the page leaves the model describing a defense\n"
        "  narrower than the one the reference implementation actually runs, which\n"
        "  is the exact gap the 2026-08-19 incident exploited.\n"
        "  If the wording changed deliberately, re-anchor this list in the same PR."
    )


def test_q309_outsider_brief_appears_verbatim():
    """The reviewer's brief is quoted word-for-word, not summarized."""
    assert OUTSIDER_BRIEF in _flat_doc(), (
        f"{DOC_REL}: the leak-lens reviewer's outsider brief is not on the page\n"
        "  verbatim. Expected, exactly:\n"
        f"    {OUTSIDER_BRIEF}\n"
        "  The doc quotes it verbatim on purpose -- it is handed to a fresh-context\n"
        "  reviewer word-for-word, and every clause names a distinct class of\n"
        "  identifier (person / machine / organization / internal project / private\n"
        "  process). A paraphrase silently narrows what the reviewer looks for, and\n"
        "  the 2026-08-19 incident is precisely a case where a capable reviewer\n"
        "  asked a narrower question ('do the greps pass') and shipped the leak.\n"
        "  (Whitespace and blockquote markers are normalized before comparing, so\n"
        "  re-wrapping the paragraph is safe; changing the words is not.)"
    )


@pytest.mark.parametrize("rel", LEAK_REFERENCE_IMPLS)
def test_q310_named_reference_implementations_exist(rel: str):
    """Section 7 names shipped code; the code has to be shipped."""
    assert f"`{rel}`" in _doc(), (
        f"{DOC_REL}: the pre-publication leak defense no longer names `{rel}`.\n"
        "  This guard resolves the doc's claims against the repo; if the doc stops\n"
        "  making the claim, re-anchor the guard (or retire it) rather than letting\n"
        "  it assert something the page does not say."
    )
    assert (_root() / rel).is_file(), (
        f"{DOC_REL} names `{rel}` as a shipped reference implementation of the\n"
        "  leak defense, but that file does not exist.\n"
        "  A named-but-absent reference is the protocol claiming a defense it does\n"
        "  not have -- and unlike a stale doc claim, a reader who goes looking for\n"
        "  the pattern to copy finds nothing and writes their own.\n"
        "  Either the file moved -- update the doc in the same PR -- or the guard\n"
        "  was deleted, in which case section 7 has to be rewritten with it."
    )


@pytest.mark.parametrize("marker", PR_TEMPLATE_MARKERS)
def test_q311_pr_template_carries_the_leak_review_line(marker: str):
    """The duty is only real if every PR is asked the question."""
    template = _root() / PR_TEMPLATE_REL
    assert template.is_file(), (
        f"{DOC_REL} section 7 and section 2 both cite `{PR_TEMPLATE_REL}` as where "
        "the leak-lens duty becomes a per-PR prompt, but the template does not "
        "exist in this checkout."
    )
    text = template.read_text(encoding="utf-8")
    assert marker in text, (
        f"{PR_TEMPLATE_REL}: the pre-publication leak review line no longer carries\n"
        f"    {marker!r}\n"
        "  Section 2's 'New public content class' row and section 7's review duty\n"
        "  both rely on the checklist to ask the question on every PR. A duty that\n"
        "  lives only in a document nobody re-reads mid-PR is a duty that silently\n"
        "  stops happening -- which is what the 2026-08-11 and 2026-08-19 incidents\n"
        "  cost, and why the line is honest-N/A capable rather than optional.\n"
        "  If the line was reworded deliberately, re-anchor these markers in the\n"
        "  same PR."
    )


@pytest.mark.parametrize("date", LEAK_INCIDENT_DATES)
def test_q312_leak_defense_names_the_incidents_it_is_argued_from(date: str):
    """The evidence stays on the page, or the rule becomes a preference."""
    doc = _doc()
    assert re.search(
        rf"^##\s+\d+\.\s+{re.escape(LEAK_SECTION_TITLE)}\s*$", doc, re.MULTILINE
    ), (
        f"{DOC_REL}: the '{LEAK_SECTION_TITLE}' section is missing entirely -- "
        "see Q-308, which owns that claim."
    )
    assert date in doc, (
        f"{DOC_REL}: the '{LEAK_SECTION_TITLE}' section no longer names the\n"
        f"  {date} incident.\n"
        "  This section is argued ENTIRELY from two measured leaks: a live\n"
        "  provider key through public run artifacts (2026-08-11), and\n"
        "  maintainer host-name literals through a new skill's shipped files\n"
        "  (2026-08-19). The second is the one that shapes the design -- both a\n"
        "  static deny-list guard and a grep-armed adversarial reviewer passed\n"
        "  it, which is why Layer 2 derives identity instead of listing it, and\n"
        "  why the leak-lens duty exists at all.\n"
        "  The section's own retirement condition turns on those incidents in\n"
        "  those words. Strip the dates and every rule below reads as a\n"
        "  preference someone held rather than a cost someone paid -- and a\n"
        "  preference is exactly what the retirement review is supposed to\n"
        "  delete.\n"
        "  This check resolves against the page rather than the repo. It is\n"
        "  kept because here the evidence IS the argument.\n"
        "  (Re-aimed 2026-09-02 from the Changelog entry that recorded these\n"
        "  incidents, which retired with the page's Changelog -- see Q-303.)"
    )
