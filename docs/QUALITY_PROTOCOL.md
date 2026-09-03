# The Quality Protocol -- RETIRED

**This page has been retired and redistributed** (2026-09-02). Nothing normative lives here any
more; the file remains only so existing citations land somewhere that tells you where to go.

## The protocol itself: converge PROTOCOL v2

The parts of this page that were a *protocol* -- how change is governed, how a spec is amended, how
machinery is priced and ratified -- were a local restatement of what is now the ratified **converge
PROTOCOL v2**: vision first, contracts frozen before work is derived from them, the DRAFT -> FROZEN
lifecycle and its Freeze Bar, the CANDIDATE amendment protocol, the conformance ledger and its
standing reconcile, and the owner attention budget. This repo is governed by that protocol as
written, and does not restate it.

One claim, one home. A rule restated locally is a rule that can drift from the protocol it claims to
be, silently, under the same name -- which is exactly what this page had already started costing: its
decision-matrix section was pinned byte-identical to `docs/VISION.md` by a guard whose entire job was
to detect drift that only existed because the text had two homes.

## Where each section went

| Retired section | Disposition | New home |
|---|---|---|
| **1. The arc** | Redistributed | [`OPERATIONS.md` §1](OPERATIONS.md). The fifth move -- the maintainer's explicit word -- retired here: it is converge PROTOCOL v2's owner attention budget, with the local merge mechanics already specified in [`AGENTS.md`](../AGENTS.md) |
| **2. What each class of change has to prove** | Redistributed unchanged | [`OPERATIONS.md` §2](OPERATIONS.md) -- repo-specific: every row names this repo's own classes and instruments |
| **3. The decision matrix** | **Retired as a second home**; tolls redistributed | The matrix's canonical articulation now lives **once**, in [`VISION.md`](VISION.md) under "Our relationship to the nlspec" -- byte-unchanged, not edited. The per-tier tolls it priced are [`OPERATIONS.md` §3](OPERATIONS.md) |
| **4. "If you see something, do something"** | Redistributed | [`OPERATIONS.md` §4](OPERATIONS.md) -- the `vision-observation` label convention is local, and the vision page still points at it |
| **5. Drift defense in depth** | Redistributed, Layer 4 retired | [`OPERATIONS.md` §5](OPERATIONS.md). Layers 0-3 name real local files and stay. Layer 4 was a pointer to §8's meta-protocol and retires with it |
| **6. When the Layer-3 review fires** | Redistributed unchanged | [`OPERATIONS.md` §6](OPERATIONS.md) -- the trigger thresholds are a local cadence knob |
| **7. Pre-publication leak defense** | Redistributed | [`OPERATIONS.md` §7](OPERATIONS.md) -- earned by two measured local incidents (2026-08-11, 2026-08-19), not by any protocol |
| **8. The meta-protocol** | **Mostly retired**; hygiene redistributed | The amendment rules (measured evidence, dated record, ratification) are converge PROTOCOL v2's. What survives is local machinery hygiene -- the retirement review's two questions and this repo's own guard -- at [`OPERATIONS.md` §8](OPERATIONS.md) |
| **9. Dogfooding** | Redistributed unchanged | [`OPERATIONS.md` §9](OPERATIONS.md) -- the measured evidence log of four issues that went through the repo's own lanes |
| **10. Lifting this model** | **Retired** | An export guide for a quality model that converge PROTOCOL v2 now supersedes as the ratified protocol. A repo that wants the model should adopt converge, not lift this page |
| **Changelog (entries 1-8)** | **Retired** | The amendment history, 2026-08-15 through 2026-08-19, is in git. Amendment recording is converge's rule now, and the surviving page is not a second amendment history |

## The guard

`tests/test_quality_protocol_guard.py` (Q-300..Q-312) was this page's guard. It was **re-aimed**,
not deleted: its claims now resolve against [`OPERATIONS.md`](OPERATIONS.md) and
[`VISION.md`](VISION.md). Two checks were retired with a reason rather than re-aimed, and Q-307 was
re-aimed from "these two copies are byte-identical" to "this text exists exactly once, and matches a
recorded constant" -- so a silent edit and a re-introduced second home both still fail loud. The
dispositions are recorded in the PR that retired this page.
