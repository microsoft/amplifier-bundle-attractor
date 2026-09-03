# The finding contract

What a Layer-3 drift-review worker must write, and what `check_findings.py` will
mechanically reject. Read this in full before you write anything.

The gate that enforces this runs **outside your context**. It cannot be argued
with, it did not read your reasoning, and it will send the file back with the
exact reason. That is deliberate: *verification inside the context that produced
the evidence is not verification* (`docs/PIPELINE_DESIGN_PRINCIPLES.md` section
0). Your job is to produce findings a stranger can check.

---

## What you are looking for

`docs/OPERATIONS.md` section 5 defines five drift layers. Layers 0-2 are
**local**: a vendored spec pinned byte-for-byte, six guard tests pinning
documented numbers to the code they came from, and an executable conformance
matrix asserting every decided divergence. They are already running in CI, and
they already catch everything they can see.

Layer 3 is what they structurally cannot see:

> "None of them can see that the README teaches one mental model while an
> exemplar demonstrates another, or that a ledger entry is individually
> well-formed and collectively obsolete."

Concretely, the finding species worth reporting:

- **A paragraph teaching retired behavior.** The text was true when written; the
  code or the doctrine moved, and the page did not.
- **An example contradicting doctrine.** A shipped exemplar demonstrates the
  shape the vision explicitly resists, or its guide describes behavior its graph
  no longer has.
- **Vocabulary drift.** A surface names a concept differently from the canonical
  spec, so a reader who learns here mis-reads there.
- **A ledger entry individually well-formed and collectively obsolete.** The row
  parses, the banner is legal, and the disposition no longer describes reality.
- **A guidance surface steering an agent against a stated principle.** What
  `agents/`, `skills/`, `context/` tell a model to do, versus what the vision
  says the project is.

What is **not** a Layer-3 finding, because something else already owns it:

- A pinned number disagreeing with its source (Layer 1's six guard files).
- A ledgered divergence that flipped (Layer 2's conformance matrix).
- A typo, a broken relative link, a stale issue number. Real, but this is not
  the instrument for it — and the quality protocol already names the issue
  numbers as a known, deliberate gap.

---

## The shape

Write exactly one JSON file for your class, at
`.drift-review/raw/<class>.json`:

```json
{
  "class": "core-docs",
  "swept": [
    "README.md",
    "docs/GETTING-STARTED.md"
  ],
  "findings": [
    {
      "id": "DR-CORE-001",
      "class": "core-docs",
      "severity": "high",
      "title": "One line naming the contradiction, not the topic",
      "drift": {
        "file": "docs/SOME-GUIDE.md",
        "line": 214,
        "quote": "verbatim text copied off that line"
      },
      "contradicts": {
        "file": "specs/canonical/attractor-spec-canonical.md",
        "line": 981,
        "quote": "verbatim text copied off that line"
      },
      "why": "What the contradiction IS, and what a reader would get wrong."
    }
  ]
}
```

`swept` is **required and non-empty even when you find nothing.** A zero-finding
class still has to say what it read — otherwise "no drift found" and "nothing
was looked at" are the same record, and the first is a result while the second
is a failure wearing its clothes.

**`swept` is reconciled, not taken on trust.** The gate reads
`.drift-review/inventory/<class>.txt` — the list written before you ran — and
measures your array against it. Three things follow, and none of them is a trap:

- **Report what you actually opened, and nothing else.** A partial sweep is a
  legitimate outcome and it will be published as one (`examples: 62/114 (54%)`
  reaches `report.md` under a gate). Padding the array to look complete is the
  one move that turns an honest partial result into a false claim, and it is
  the move the reconciliation exists to make visible.
- **The normative sources you read for context belong in `swept` too.** They are
  reported separately, as reads rather than as surfaces of your class, so they
  no longer inflate the count. You do not have to decide what is in-class; the
  gate already knows.
- **Do not list the same path twice.** Duplicates are counted once and named in
  the report. Harmless, but it is bookkeeping the gate should not have to do
  for you.

The only coverage rule that *rejects* is the floor: if **none** of what you
listed is in your class's inventory, there was no review of that class to
report, and the round comes back.

---

## What the gate checks, rule by rule

**Both sides carry `file`, `line`, `quote`.** All three, on `drift` and on
`contradicts`. A finding with one side is an assertion.

**Both files resolve against the actual tree.** Repo-relative, existing, a
regular file, inside the repository. Not remembered, not reconstructed.

**Both line numbers are in range** for the file as it exists right now.

**Both quotes appear where you said they do.** The quote must occur, verbatim,
within a small window around the cited line (2 lines before, 6 after —
whitespace differences are tolerated so a reflowed markdown paragraph still
resolves; invented text is not). **Open the file and copy the line.** A quote
reconstructed from memory is exactly the failure this rule exists to catch.

**Each quote is at least 16 characters** after whitespace normalization. A
three-word quote anchors nothing.

**`contradicts.file` is a normative source.** One of:

| Source | What it is |
|---|---|
| `specs/canonical/` | the vendored upstream nlspec, pinned byte-for-byte (Layer 0) |
| `docs/VISION.md` | the repo's stated vision |
| `SPEC_CONFORMANCE.md` | the conformance ledger |
| `specs/EXTENSIONS.md` | the extensions ledger |
| `specs/conformance/` | the executable conformance matrix |

This is the definition of drift, not a formatting preference. Two non-normative
surfaces disagreeing is a proofreading note; a surface disagreeing with one of
these is drift. If you cannot name the normative passage a thing contradicts,
you have found something else — and Layer 3 is not the place to file it.

**`drift.file` and `contradicts.file` are different files.** A finding names a
drifting surface *and* the separate passage it moved away from.

**Both rules above judge the path your citation *resolves to*.** Write plain
repo-relative paths. `specs/canonical/../../docs/SOME-DOC.md` is not a citation
of `specs/canonical/`, and `specs/canonical/../../README.md` is not a different
file from `README.md` — the gate resolves both before it decides, so a traversal
buys exactly nothing except a rejection that names it.

**`severity` is one of `critical`, `high`, `medium`, `low`.** The human triaging
this sorts by that field, so an invented value costs them the sort.

**`id` is unique across all four classes** and matches `[A-Za-z0-9][A-Za-z0-9._-]{2,63}`.
Use your class prefix so ids cannot collide: `DR-CORE-`, `DR-EX-`, `DR-GUIDE-`,
`DR-LEDGER-`.

**`title` is at least 12 characters, `why` at least 40.** `why` carries the
argument. A citation pair with no argument is a coincidence of two files
mentioning the same word.

---

## Severity, calibrated

| Severity | Use when |
|---|---|
| `critical` | A reader following this surface would ship something wrong, or the surface asserts the opposite of a normative source |
| `high` | Teaches a retired behavior or a rejected mental model; a reader learns something they will have to unlearn |
| `medium` | Vocabulary or framing has drifted; the reader is not misled about behavior but is misled about the model |
| `low` | Stale in a way a careful reader recovers from unaided |

---

## What you must not do

**Do not fix anything.** This review reports; a human triages and files. The
separation is the design: a reviewer that edits what it reviews has re-entered
the context it was supposed to stay outside of. Write nothing outside
`.drift-review/`.

**Do not pad.** A finding whose citations do not resolve is worse than no
finding: it costs a human the read and it teaches them to distrust the
instrument. Ten checkable findings beat forty claims.

**Do not report zero findings as a failure.** A clean class is a result. Say
what you swept, and say it found nothing.
