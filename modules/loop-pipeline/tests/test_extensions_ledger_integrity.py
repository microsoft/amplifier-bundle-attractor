"""Ledger-integrity guard for ``specs/EXTENSIONS.md``.

Guards against a class of damage discovered in practice: a rebase conflict on
``specs/EXTENSIONS.md`` resolved with ``git rebase -Xtheirs`` silently
discarded three already-merged ledger entries (`## 31.`, `## 32.`, `## 33.`)
while a fourth PR's own new entry (`## 34.`) survived, because `-Xtheirs`
takes one side's *whole file* rather than reconciling both sides' additions.
The numbering on either side of the gap stayed superficially plausible
(`## 30.` immediately followed by `## 34.`), and nothing asserted that the
sequence was supposed to be contiguous, so CI went green on a main branch
that had quietly lost three merged entries.

This test closes that gap: it asserts the numbered ``## N.`` section headings
in ``specs/EXTENSIONS.md`` form a contiguous ``1..max`` sequence with no gaps
and no duplicates. It does NOT check entry *content* (that a section's prose
is correct) -- only that the heading sequence itself is not missing entries
or double-counting one, which is exactly the invariant a silent multi-entry
clobber violates while leaving individually well-formed headings behind.

Honest limits:
  - This is a structural (heading-sequence) check, not a content check. A
    rebase resolution that clobbered *prose inside* a still-numbered section
    (rather than dropping whole headings) would not be caught here.
  - Non-numbered headings (``## Entry Format``, ``## Conformance Restoration
    Note (T0-4)``) are deliberately excluded from the sequence -- they are
    not ledger entries and carry no number to validate.
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUNDLE_ROOT = Path(__file__).parent.parent.parent.parent
LEDGER_PATH = BUNDLE_ROOT / "specs" / "EXTENSIONS.md"

# Matches "## 31. Title..." style numbered ledger-entry headings only.
# Non-numbered headings ("## Entry Format", "## Conformance Restoration
# Note (T0-4)") do not match and are correctly excluded from the sequence.
_SECTION_HEADING_RE = re.compile(r"^## (\d+)\.", re.MULTILINE)


def _section_numbers(text: str) -> list[int]:
    """Extract the numbered ``## N.`` section headings, in document order."""
    return [int(n) for n in _SECTION_HEADING_RE.findall(text)]


def _find_ledger_gaps_and_duplicates(text: str) -> tuple[list[int], list[int]]:
    """Return ``(gaps, duplicates)`` in the numbered section sequence.

    ``gaps``: section numbers in ``1..max(observed)`` that never appear.
    ``duplicates``: section numbers that appear more than once, in the order
    their *second* (repeat) occurrence is seen.
    """
    numbers = _section_numbers(text)
    if not numbers:
        return [], []

    seen: set[int] = set()
    duplicates: list[int] = []
    for n in numbers:
        if n in seen:
            duplicates.append(n)
        seen.add(n)

    expected = set(range(1, max(numbers) + 1))
    gaps = sorted(expected - seen)
    return gaps, duplicates


# ---------------------------------------------------------------------------
# Live-file check: the actual specs/EXTENSIONS.md must be contiguous today.
# ---------------------------------------------------------------------------


def test_extensions_ledger_section_numbers_contiguous_no_gaps_or_duplicates():
    """specs/EXTENSIONS.md's numbered ``## N.`` headings must be 1..max with
    no gaps and no duplicates.

    A gap here means a merged ledger entry has gone missing (e.g. a rebase
    conflict resolved with ``-Xtheirs`` silently dropped one side's
    additions) even though the numbering on either side of the hole still
    looks superficially fine. A duplicate means two entries claim the same
    section number, which is its own form of ledger corruption (e.g. a
    stacked-branch rebase that renumbered one entry to collide with another
    instead of the next free number).
    """
    text = LEDGER_PATH.read_text()
    gaps, duplicates = _find_ledger_gaps_and_duplicates(text)

    assert not gaps, (
        f"specs/EXTENSIONS.md is missing numbered ledger section(s): {gaps}. "
        "The heading sequence jumps over these numbers even though entries "
        "on both sides are present and well-formed -- this is exactly the "
        "shape of a rebase conflict resolved with 'git rebase -Xtheirs' (or "
        "a merge that silently took one side's whole file) discarding "
        "already-merged entries. Restore the missing section(s) verbatim "
        "from the commit that last had them (check git log/blame on this "
        "file), do not re-author them from memory."
    )
    assert not duplicates, (
        f"specs/EXTENSIONS.md has duplicate numbered ledger section(s): "
        f"{duplicates}. Two entries claim the same section number -- "
        "renumber the later entry to the next free number and fix any "
        "'depends-on: §NN' cross-references that pointed at the collided "
        "number."
    )


# ---------------------------------------------------------------------------
# Regression proof: synthetic content mirroring the exact incident shape.
#
# These do not touch the live file at all -- they prove the *checker logic*
# itself flags gaps/duplicates, independent of whatever specs/EXTENSIONS.md
# currently contains. See the module docstring for the real-world incident
# these mirror (PR #135's §31/32/33 silently discarded by PR #136's
# `-Xtheirs` rebase, leaving §30 followed directly by §34).
# ---------------------------------------------------------------------------


def _synthetic_ledger(numbers: list[int]) -> str:
    """Build a minimal synthetic EXTENSIONS.md body with the given section
    numbers, in order, each a well-formed heading + body + separator."""
    parts = [
        "# Attractor Extensions\n\n## Entry Format\n\nnot a numbered entry\n\n---\n"
    ]
    for n in numbers:
        parts.append(f"## {n}. Synthetic Entry {n}\n\nsome body text.\n\n---\n")
    parts.append("## Conformance Restoration Note (T0-4)\n\nnot a numbered entry\n")
    return "\n".join(parts)


def test_checker_flags_the_incident_shape_30_then_34():
    """Regression proof (RED case): a synthetic doc with 1..30 present and
    then jumping straight to 34 -- exactly the shape this repo's main branch
    was silently reduced to -- must be flagged as missing 31, 32, and 33.
    """
    doc = _synthetic_ledger([*range(1, 31), 34])
    gaps, duplicates = _find_ledger_gaps_and_duplicates(doc)
    assert gaps == [31, 32, 33], (
        f"expected the checker to flag exactly [31, 32, 33] as missing, got {gaps}"
    )
    assert duplicates == []


def test_checker_flags_duplicate_section_numbers():
    """Regression proof (RED case): a synthetic doc with two entries both
    numbered 34 (e.g. a stacked-branch rebase collision) must be flagged."""
    doc = _synthetic_ledger([*range(1, 34), 34, 34])
    gaps, duplicates = _find_ledger_gaps_and_duplicates(doc)
    assert gaps == []
    assert duplicates == [34], (
        f"expected duplicate [34] to be flagged, got {duplicates}"
    )


def test_checker_passes_a_contiguous_sequence():
    """Regression proof (GREEN case): a synthetic doc with an unbroken
    1..34 sequence -- the restored shape -- must report no gaps and no
    duplicates."""
    doc = _synthetic_ledger(list(range(1, 35)))
    gaps, duplicates = _find_ledger_gaps_and_duplicates(doc)
    assert gaps == []
    assert duplicates == []


# ---------------------------------------------------------------------------
# `upstream action:` value-format guard.
#
# Discovered in practice alongside the heading-sequence damage above: a
# ledger entry can carry a well-formed, contiguous heading and still commit
# to something dishonest in its *value* -- e.g. `deferred, ...,
# review-by: <date>` written against an upstream repo already known to be
# dormant, with issues disabled, where filing would not land. The Entry
# Format section (above, in this same file) defines exactly which values are
# legal; this check is the mechanical half of that promise: every
# `upstream action:` value in the live ledger must start with one of them.
#
# This does NOT validate that a `deferred` reason or `declining` reason is
# *true* -- that is a judgment call no regex can make. It only catches the
# cheap, structural mistake of a value that isn't in one of the legal forms
# at all (e.g. a bare vague promise with no link, no date, and no
# `declining` framing), which is the shape the incident behind this task
# took: a `review-by` date can be well-formed as a *string* while still
# encoding a plan that the evidence already rules out -- that half is a
# human judgment this test cannot make, and does not try to.
# ---------------------------------------------------------------------------

# The first line of an `upstream action:` value establishes which of the
# three legal forms (link / deferred-with-date / declining-with-reason) —
# plus the pre-existing `not applicable` disposition — the entry is using.
# Longer reasons wrap onto further blockquote lines, which this check does
# not need to read: the category is fixed by how the value opens.
_UPSTREAM_ACTION_VALUE_RE = re.compile(
    r"^>\s*\*\*upstream action:\*\*\s*(.+)$", re.MULTILINE
)

_LEGAL_UPSTREAM_ACTION_PREFIXES = (
    "https://github.com/",  # a real upstream PR/issue link
    "deferred, reason:",  # deferred, with a review-by date (checked below)
    "declining, reason:",  # honest non-filing, no date required
    "not applicable",  # no upstream action applies (spec-silent / additive)
)

_REVIEW_BY_DATE_RE = re.compile(r"review-by:\s*\d{4}-\d{2}-\d{2}\b")


def _upstream_action_first_lines(text: str) -> list[str]:
    """Extract the first line of every `upstream action:` value, in document
    order (multi-line reasons continue past this point but the legal-form
    prefix always appears on this first line)."""
    return [m.strip() for m in _UPSTREAM_ACTION_VALUE_RE.findall(text)]


def _illegal_upstream_action_values(first_lines: list[str]) -> list[str]:
    """Return the subset of `upstream action:` first-lines that do not open
    with one of the legal forms."""
    return [
        line
        for line in first_lines
        if not line.startswith(_LEGAL_UPSTREAM_ACTION_PREFIXES)
    ]


def test_extensions_ledger_upstream_action_values_are_legal_forms():
    """Every `upstream action:` value in the live ledger must open with one
    of the forms the Entry Format section declares legal: a real upstream
    link, `deferred, reason: ..., review-by: <date>`, `declining, reason:
    ...`, or `not applicable ...`.

    This is a structural check only -- it cannot judge whether a `deferred`
    or `declining` reason is factually true (that takes verifying the
    upstream repo's actual state, as this task did for §25/§29/§33). It
    exists to catch the cheaper mistake: a value that isn't shaped like any
    of the legal forms at all.
    """
    text = LEDGER_PATH.read_text()
    first_lines = _upstream_action_first_lines(text)
    assert first_lines, (
        "expected at least one `upstream action:` entry in the live ledger"
    )

    illegal = _illegal_upstream_action_values(first_lines)
    assert not illegal, (
        f"specs/EXTENSIONS.md has `upstream action:` value(s) not shaped like any "
        f"legal form (a real link, `deferred, reason: ..., review-by: <date>`, "
        f"`declining, reason: ...`, or `not applicable ...`): {illegal!r}. See the "
        "Entry Format section at the top of specs/EXTENSIONS.md."
    )


def test_extensions_ledger_deferred_upstream_actions_carry_a_review_by_date():
    """Every `deferred, reason: ...` value must carry a `review-by:
    YYYY-MM-DD` date somewhere in its (possibly multi-line) reason -- a
    non-date placeholder ("eventually", "TBD", "soon") is exactly the
    failure mode the Entry Format section forbids.

    Checked against the full multi-line banner block per entry (not just
    the first line), since the date may trail the reason across a wrapped
    blockquote line -- unlike the legal-form check above, which only needs
    the opening word.
    """
    text = LEDGER_PATH.read_text()
    # Each blockquote banner is a contiguous run of `>`-prefixed lines; grab
    # every such run and inspect the ones that open an `upstream action:`
    # value with `deferred, reason:`.
    banners = re.findall(r"(?:^>.*\n)+", text, re.MULTILINE)
    deferred_banners = [
        b for b in banners if "**upstream action:** deferred, reason:" in b
    ]
    for banner in deferred_banners:
        assert _REVIEW_BY_DATE_RE.search(banner), (
            f"a `deferred` upstream action is missing a `review-by: YYYY-MM-DD` date: "
            f"{banner!r}"
        )


def test_checker_flags_an_illegal_upstream_action_value():
    """Regression proof (RED case): a vague, undated promise with no link
    and no `declining` framing must be flagged -- this is the shape the
    §25/§29/§33 incident would have taken if written as free prose instead
    of one of the ledger's legal forms."""
    doc = "> **upstream action:** we should probably raise this with upstream at some point\n"
    first_lines = _upstream_action_first_lines(doc)
    assert first_lines == ["we should probably raise this with upstream at some point"]
    illegal = _illegal_upstream_action_values(first_lines)
    assert illegal == first_lines


def test_checker_accepts_all_four_legal_upstream_action_forms():
    """Regression proof (GREEN case): one example of each legal form must
    pass, including the `declining` form this task added."""
    doc = (
        "> **upstream action:** https://github.com/strongdm/attractor/pull/42\n"
        "> **upstream action:** deferred, reason: needs one more release, review-by: 2026-12-01\n"
        "> **upstream action:** declining, reason: upstream repo is dormant\n"
        "> **upstream action:** not applicable -- spec-silent area\n"
    )
    first_lines = _upstream_action_first_lines(doc)
    assert len(first_lines) == 4
    assert _illegal_upstream_action_values(first_lines) == []
