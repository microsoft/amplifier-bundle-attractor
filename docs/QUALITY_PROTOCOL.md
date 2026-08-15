# The Quality Protocol

How work gets proven in this repo, what each kind of change owes before it merges, and how the
machinery that enforces all of it is kept honest -- including how this document itself is amended
and how its own scaffolding gets retired.

Binding on contributors and on AI coding agents working here. It sits above the two files you
already read: [`AGENTS.md`](../AGENTS.md) carries the always-on conventions and the merge
discipline; [`PRINCIPLES.md`](../PRINCIPLES.md) carries the upstream contract and the "walk
upstream first" rule. This page is the protocol those two serve.

**Scope note.** Nothing here is enforced by the engine. Some of it is enforced by CI (named below,
by file); the rest is enforced by review. Where a piece of machinery does not exist yet, this
document says so in those words rather than describing it in the present tense.

---

## 1. The arc

Every non-trivial change follows the same five moves, in order. Skipping one is a decision stated
out loud in the PR, not an oversight discovered later.

**Design.** Understand the behavior before changing it. Where the engine's own behavior is
load-bearing to the design -- routing, retry, fidelity, checkpointing -- read the code, and when
reading is not enough run an **empirical probe**: a throwaway graph or script that makes the engine
answer the question directly. A design premised on what the engine *ought* to do is how `ATX-2`'s
original disposition came to rest on a false premise and had to be withdrawn (`SPEC_CONFORMANCE.md`,
ATX-2). Substantial designs get a dated record in [`docs/designs/`](designs/) --
`YYYY-MM-DD-<topic>.md`, the naming already in use there.

**Build.** The smallest change that carries the design. The bug species this ecosystem actually
produces are catalogued in
[`docs/designs/RECURRING-BUG-CLASSES.md`](designs/RECURRING-BUG-CLASSES.md); read it while
designing, not after review.

**Live proof.** *"The test passes"* is not *"it works."* A green unit suite proves the code does
what its author expected, in the author's own frame. Live proof means the real thing running in a
real environment: a real pipeline run through the real parser, engine and handler dispatch; a real
provider where the claim is about a provider; a really-killed process where the claim is about
crash recovery. This repo has the scar tissue -- bugs have shipped with green unit tests and failed
on first real-graph run (`AGENTS.md`, "Verification gradient"), which is why
`modules/loop-pipeline/tests/test_live_graph_gate.py` now runs in CI as the permanent hermetic
floor. A floor, not a ceiling: if the changed path is not one of the behaviors that file covers, do
the live run yourself and paste the evidence.

**Independent adversarial review.** A *fresh* session that did not produce the work, re-executes its
evidence, and tries to break it. Two properties are non-negotiable:

- It **re-runs** the evidence rather than reading the author's summary of it. A review that only
  reads is not a review. Findings cite `file:line`.
- It is **independent of the context that produced the artifact**. Verification inside the producing
  context is not verification -- a worker that knows what the gate reads can write what the gate
  reads. That is the same property `specs/EXTENSIONS.md` section 25 (fail-closed goal-gate outcomes)
  buys structurally, applied to human and agent review.

**The maintainer's explicit word.** No merge without it. The mechanics -- the required
`CI Gate (all checks passed)` status check, and the narrow, legitimate use of `--admin` -- are
already specified in `AGENTS.md` under "Merge discipline: CI Gate is required, never bypass it".
That section governs; this one does not restate it.

---

## 2. What each class of change has to prove

The evidence a change owes is a function of what it can break. This table is the floor. A reviewer
may ask for more, never less.

| Class | Examples | Required evidence before merge |
|---|---|---|
| **Engine / handler code** | `engine.py`, `handlers/*`, dispatch, routing, retry, checkpoint | Full module suites green **and** a live pipeline run exercising the changed path (paste the `events.jsonl` slice) **and** independent adversarial review **and** a ledger entry if the change is spec-relevant (last row) |
| **Exemplar / example graphs** | `examples/pipelines/*.dot`, `examples/patterns/*.dot`, `examples/objective/*` | `attractor lint` with **zero ERROR** diagnostics -- warnings are informational, which is exactly the line `modules/loop-pipeline/tests/test_examples_lint_clean.py` enforces -- **and** at least one live convergence run **and** the graph's own gates demonstrated **RED and GREEN**: a negative control proving the gate can fail, a positive control proving it can pass. A gate only ever seen green is an unproven gate |
| **Guidance surfaces** | `agents/`, `skills/`, `context/`, teaching content in `README.md` and `docs/` | Guidance-eval evidence **once the eval instrument ships**. *That instrument does not exist in this repo today: it is in flight, and a pilot is planned.* Until it lands, the floor is a **fresh-session walk-through** proving the guidance steers as written -- a session with no prior context follows only the changed text and arrives at the intended behavior. Paste the walk-through |
| **Docs making factual claims** | any doc asserting a number, default, vocabulary, or behavior | A guard test pinning each load-bearing claim to **its source of truth in code**, following the five existing guards (section 3, Layer 1). A page-only assertion ("the page says 500") is tautological: it passes forever and fails only when someone edits the page, which is the one case needing no guard. The assertion must read the value from the code and fail when the **code** moves |
| **Spec-relevant behavior** | anything that conforms to, diverges from, or extends the nlspec | A `specs/EXTENSIONS.md` entry and/or a `SPEC_CONFORMANCE.md` row, **in the same PR**, per the Compatibility doctrine. Entries obey the Entry Format: `depends-on:`, plus `upstream action:` in one of its legal forms whenever the banner states a divergence |

Two of these already have machinery behind them: the `EXTENSIONS.md` requirement is on the PR
checklist (`.github/PULL_REQUEST_TEMPLATE.md`), and the ledger's structural integrity is guarded in
CI. The rest are enforced by review today.

**The Compatibility doctrine governs the last row.** Its four rules -- honor the nlspec design where
possible; 100% support for community `.dot` files written against the nlspec; extensions additive
and non-interfering; divergences only for safety, backed by measured evidence, and always loud --
are stated in full at the top of [`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) (maintainer ruling,
2026-08-14). Every disposition in that ledger is decided by them.

---

## 3. Drift defense in depth

Five layers. Each catches a class the layer below cannot see. They are named so another repo can
lift the model without lifting this repo's specifics.

### Layer 0 -- vendored truth

`specs/canonical/*-canonical.md` is the upstream nlspec, vendored and pinned byte-for-byte to
`strongdm/attractor` @ **`fb57a55`** (`SPEC_CONFORMANCE.md`, `SYNC-1`). It is the normative text:
when the shipped engine and a doc disagree about what the spec says, this is what settles it.

Any upstream movement is a **SYNC event** -- re-vendor, then re-read every ledger entry whose
disposition depended on the old text. The precedent exists: at `fb57a55`, upstream had absorbed
`specs/EXTENSIONS.md` sections 1-7 item-for-item, and each now carries a
`status: ABSORBED UPSTREAM @ <sha>` banner naming the canonical section that supersedes it.

Upstream is currently dormant -- four ledger entries carry `upstream action: declining` citing
exactly that. The check stays anyway: it costs one comparison, and dormancy is a fact about today,
not a property of the repo.

### Layer 1 -- deterministic guards

Five test files in `modules/loop-pipeline/tests/`, run in CI on every PR. Each pins a documented
claim to something that fails when the *code* moves:

| Guard file | What it pins |
|---|---|
| `test_extensions_ledger_integrity.py` | `specs/EXTENSIONS.md` numbered headings form a contiguous `1..max` sequence -- no gaps, no duplicates -- and every `upstream action:` value is one of the legal forms, with `deferred` carrying a real `review-by` date. Written after a `git rebase -Xtheirs` silently discarded three already-merged ledger entries and left plausible-looking numbering behind |
| `test_doc_consistency.py` | The retry-ceiling default, read from the canonical spec snapshot and cross-checked against the authoring guide; and the `house` shape's LLM classification agreeing across `DOT-AUTHORING-GUIDE.md` and `DOT-SYNTAX.md` |
| `test_engine_semantics_doc_guard.py` | `context/engine-semantics.md`, the bundle's declared source of truth for shipped-engine behavior -- both text-anchored claims (the no-matching-edge and stale-label rules) and behavior-anchored ones (a real engine run asserting the main loop hard-fails on no matching edge) |
| `test_explainer_doc_guard.py` | The published explainer page, `docs/attractor-explained.html`: feedback-critique caps, the parallel-branch default, `last_response` truncation, the summary budgets, the fidelity vocabulary and its default, the lifecycle phases, and the shape-to-execution-tier vocabulary -- each read from its source module, never from the page |
| `test_examples_lint_clean.py` | Every `.dot` under `examples/` lints with zero ERROR diagnostics. Written because the dead-corrective-edge class shipped in eight examples for months, because nothing could see topology |

**The rule this layer imposes: a new claim-bearing doc ships with its guard.** The explainer guard
states the reason plainly -- a page nobody re-reads rots silently and keeps being shared, which is
strictly worse than an internal doc going stale.

### Layer 2 -- executable conformance matrix

**Status: shipped (tranche 1).** Two files, run in CI on every PR inside the existing
loop-pipeline job:

| File | What it is |
|---|---|
| `specs/conformance/attractor-matrix.yaml` | The matrix itself -- a reviewed *document*, one row per normative statement cluster, carrying the verbatim spec quote, the disposition, the ledger cite, and the assertion |
| `modules/loop-pipeline/tests/test_spec_conformance_matrix.py` | The runner -- per-row structural integrity, in-process behavioral probes, the upstream-sync sha pin, and the coverage tripwire |

Tranche 1 covers every decided divergence, every OPEN ledger item, the load-bearing conformances,
and the SYNC row. Later tranches extend it section by section; ULM/CAL matrices are named as tranche
3. The design record is [`docs/designs/2026-08-15-conformance-matrix.md`](designs/2026-08-15-conformance-matrix.md).

One row per spec section: **spec section -> executable assertion -> disposition**, where disposition
is one of `CONFORM`, `DIVERGE-DECIDED`, `EXTENSION`, `NOT-IMPLEMENTED-DECIDED`, plus two the build
earned: `OPEN-PINNED` (pin an undecided behavior without forging a decision the maintainer has not
made) and `NOT-ASSERTABLE` (aspirational prose, argued per row).

The load-bearing idea is that **decided divergences are asserted too.** A matrix that only asserts
conformance detects drift in one direction and is blind in the other -- silently drifting *back*
toward spec, at a point where the ledger says we deliberately do not conform, is equally a
contradiction between the repo and its own record. Drift is any movement not recorded in the ledger,
in either direction.

So a flipped assertion is never merely a red test: its failure message names **the ledger entry that
must change**. The failure is a prompt to update `SPEC_CONFORMANCE.md` or `specs/EXTENSIONS.md`, or
to revert -- never to edit the assertion.

Two mechanisms keep that honest. A **coverage tripwire** requires every DIVERGES-bannered
`EXTENSIONS.md` entry and every DIVERGE-disposition `ATX-*` row to be cited by at least one matrix
row, so a future divergence cannot be ledgered without also being asserted. And the **SYNC row**
pins the canonical file's sha256, turning a re-vendor from a quiet commit into a demanded
full-matrix re-review -- which is exactly the work the `fb57a55` sync required by hand.

Retirement condition: none for the mechanism. Individual rows retire when upstream absorbs the
divergence (the `ABSORBED UPSTREAM` banner protocol) or when a decision closes an `OPEN-PINNED`
row -- in both cases the row changes disposition rather than disappearing.

### Layer 3 -- periodic holistic semantic review

Layers 0-2 are local: each checks one claim, one file, one section. None of them can see that the
README teaches one mental model while an exemplar demonstrates another, or that a ledger entry is
individually well-formed and collectively obsolete. That is a semantic reading of the whole, and it
has to be done as one.

Scope: docs, examples, guidance surfaces, and both ledgers, read against the canonical spec **and**
against the repo's stated vision. Output: findings with `file:line` evidence, filed as issues -- not
a report that gets read once.

Executed as an agent wave today. The intent is for it to become a **self-review attractor pipeline**
-- the repo reviewing itself with its own machinery (section 6). That pipeline does not exist yet.

### Layer 4 -- the meta-protocol

Layers 0-3 are machinery, and machinery accretes. Section 5 governs how they are amended, and how
they are retired.

---

## 4. When the Layer-3 review fires

A release-less repo cannot measure in versions, so the primary trigger counts merges.

- **Every ~15 merged PRs touching `modules/` or `docs/`.** The cadence knob, and the one most likely
  to need retuning once it has fired a few times.
- **Any PR that adds or edits an `EXTENSIONS.md` section, or a `SPEC_CONFORMANCE.md` ledger row.**
  The conformance surface moved; the claims around it may no longer be true.
- **Any upstream spec movement** -- a SYNC event (Layer 0).
- **Any incident or postmortem naming a doc-vs-code contradiction.** Immediate, and scoped to that
  surface class: if one page lied, ask what else of that kind lies.
- **A quarterly floor**, regardless of all of the above. A quiet quarter is not evidence of a correct
  repo.

Triggers are inclusive -- whichever fires first, fires.

---

## 5. The meta-protocol -- improving the protocol

This document is subject to itself. It is a doc making factual claims, it is a guidance surface, and
it is machinery. All three of those rows in section 2 apply to it.

**Amendments require measured evidence.** Not an opinion about what would be better: either **a
failure this change would have caught**, or **a cost this change retires**. "It seems more rigorous"
is not evidence -- it is the argument that grows checklists nobody reads. This mirrors the doctrine's
own rule for divergences: name the property, cite the incident or the measurement.

**Amendments are recorded in the Changelog** at the bottom of this file, dated, with the evidence
named. The Changelog is the amendment history; the sections above are only ever the current state.

**Every guard, gate and rule added must name its retirement condition where one exists.** The
condition is the observable event that makes the machinery unnecessary -- the bug class becoming
structurally impossible, upstream absorbing the divergence, the surface disappearing. Some have
none, and saying so explicitly is a valid answer; silence is not. `specs/EXTENSIONS.md` section 27
already carries a "Guard retirement inventory" -- that is the shape.

**The retirement review** runs on the same triggers as Layer 3 and asks two questions of every piece
of machinery in Layers 0-3:

1. **What has it caught since the last review?** Nothing is a finding, not a pass. A guard that has
   never fired is either protecting a genuinely closed hole or asserting something that cannot
   break, and those have different answers.
2. **Does it still earn its keep?** The cost is real: runtime, review attention, and the friction it
   adds to every unrelated change that has to walk past it.

Machinery whose retirement condition has fired is **removed in the same wave** -- not deprecated,
not left in place "just in case." This is the self-ablation discipline, and it is the half of process
design that usually goes undone: yesterday's scaffolding is today's tax.

**This document's own guard.** It ships without one. Its load-bearing external references -- the five
guard filenames, the canonical SHA, the four issue numbers -- are not machine-pinned today, and by
section 2's own rule that is a gap, named here rather than left for a reader to find. Adoption
condition: the first time one of those references is found stale, or the Layer-2 matrix lands
(whichever comes first), this file gets the guard it asks of everything else. **That condition has
now fired** -- the matrix landed 2026-08-15 -- so this file's own guard is owed next, and is
recorded as such in the Changelog below rather than left for a reader to discover.

---

## 6. Dogfooding

Improvements to this repo that fit the pipelines' weight class go through the repo's own lanes.
[`docs/ISSUE_PIPELINE.md`](ISSUE_PIPELINE.md) documents both -- the defect lane (`ready:spec`) and
the feature lane (`ready:feature-spec`), each maintainer-triggered, each ending at a human review
gate. The pipeline never merges its own work.

This is measured, not aspirational. Four issues have gone through end-to-end and shipped to `main`
after human review:

| Issue | What it was | Fix PR |
|---|---|---|
| [#144](https://github.com/microsoft/amplifier-bundle-attractor/issues/144) | `$key` substitution mangling unrelated longer variable names that share a prefix | #156 |
| [#146](https://github.com/microsoft/amplifier-bundle-attractor/issues/146) | the tutorial's first example tripping its own linter | #175 |
| [#172](https://github.com/microsoft/amplifier-bundle-attractor/issues/172) | a dead end inside a composed child subgraph reported as SUCCESS to the parent | #182 |
| [#204](https://github.com/microsoft/amplifier-bundle-attractor/issues/204) | LLM cost calculation not exposed for the `unified_llm` + loop-pipeline stack | #223 |

**Hand-building is the sanctioned path for work above the lanes' measured weight class.** The lanes
are not a purity test -- some work is larger, more exploratory, or more entangled than a capsule can
specify. Choosing to hand-build is a normal call, and it changes nothing about section 1: the same
arc applies, with the same live proof and the same independent review.

**Either way, the acceptance criteria live on the issue first.** In the feature lane that is already
structural -- criteria must be posted as a maintainer comment before the label, because a comment's
author role is computed server-side and cannot be forged, while an issue body can be edited by
anyone after the fact (`docs/ISSUE_PIPELINE.md`). Hand-built work inherits the discipline for the
same reason it exists: criteria written after the work is done describe the work rather than test it.

---

## 7. Lifting this model

Other repos in the ecosystem are welcome to take this. What transfers, and what does not:

**Portable.** The five-layer drift model (vendored truth -> deterministic guards -> executable
conformance matrix -> holistic semantic review -> meta-protocol); the arc (design -> build -> live
proof -> independent adversarial review -> maintainer's word); the *shape* of the change-class table,
one row per thing-that-can-break with the evidence it owes; and the meta-protocol itself --
evidence-gated amendments, named retirement conditions, a periodic retirement review. Those are about
the structure of a quality system, not about DOT graphs.

**This-repo-specific.** `attractor lint` and its rule IDs; the particular ledgers
(`SPEC_CONFORMANCE.md`, `specs/EXTENSIONS.md`) and their entry formats; the five named guard files;
the pinned upstream SHA; the issue-pipeline lanes. Every one of those is the local answer to a
general question -- *what is your normative source, what pins your claims to it, what is the record
of deliberate deviation* -- and each repo's answer will look different.

The adaptation that matters is the honest one: name your own sources of truth, then ask which of the
five layers you actually lack. A repo with no upstream spec has no Layer 0 and should not invent one.

This repo's maintainers will help seed a customized version on request -- open an issue.

---

## Changelog

Amendments to this protocol, newest first. Each entry names the evidence that justified it.

### 2026-08-15 -- Layer 2 shipped (entry 2)

- **Changed.** Section 3, Layer 2 flipped from *"in flight -- designed here, not built"* to shipped,
  naming the two real files: `specs/conformance/attractor-matrix.yaml` (38 tranche-1 rows) and
  `modules/loop-pipeline/tests/test_spec_conformance_matrix.py` (the runner). The disposition
  vocabulary gained `OPEN-PINNED` and `NOT-ASSERTABLE`; the coverage tripwire and the SYNC sha pin
  are described because they now exist.
- **Evidence that the layer earns its cost, at adoption.** Building tranche 1 was itself the
  measurement. It surfaced **two unledgered contradictions with the canonical spec** that four
  existing defenses could not see -- the unknown-shape hard error against section 4.2's
  default-handler fallback, and `reasoning_effort`'s absent `"high"` default against Appendix A --
  both filed as [#234](https://github.com/microsoft/amplifier-bundle-attractor/issues/234) and
  pinned as `OPEN-PINNED` rows rather than silently encoded. It also pinned three points where the
  canonical spec **contradicts itself** (retry-on-FAIL, reachability severity, and the goal-gate
  ladder's routing signal), each recorded on the row that chooses a side. Layer 1 could not have
  found any of these: those guards pin *our docs* to *our code*, and say nothing about the spec.
- **The mechanism was proven by mutation before it merged.** Per the design's definition of done,
  one real engine behavior was flipped in a scratch copy (dead-end-with-non-FAIL made to return
  SUCCESS, the exact un-divergence `ATX-11` forbids) and the matrix produced the specified failure
  naming spec section 3.2, `ATX-11`, `EXTENSIONS.md` section 33, and the two legal exits; a ledger
  citation was deleted in a second scratch copy and the structural check failed naming it. A guard
  never seen red is an unproven guard.
- **Scope of this change: additive.** No engine, handler, or example code changed. The matrix
  *indexes* existing coverage rather than duplicating it: 22 of 38 rows cite tests that already
  exist, verified by AST parse rather than import (indexed cites cross per-module venv boundaries).
- **Retirement conditions.** The matrix mechanism has none -- it is the executable form of a ledger
  that is itself permanent. Individual rows retire by changing disposition: when upstream absorbs a
  divergence, or when a decision closes an `OPEN-PINNED` row. **This document's own missing guard
  (section 5) is now due**: its stated adoption condition -- "or the Layer-2 matrix lands" -- fired
  with this entry.

### 2026-08-15 -- protocol captured (entry 1)

- **Established.** Maintainer ruling: capture the repo's quality and standards protocols in per-repo
  convention files -- including a protocol for improving the protocol, explicit anti-drift machinery
  against the upstream nlspec, and dogfooding.
- **Evidence that each layer earns its cost, at adoption.** Layer 0: the `fb57a55` sync, which found
  upstream had absorbed `specs/EXTENSIONS.md` sections 1-7 item-for-item. Layer 1: the `-Xtheirs`
  rebase that silently discarded three merged ledger entries (now guarded by
  `test_extensions_ledger_integrity.py`), and the dead-corrective-edge class that shipped in eight
  examples because nothing could see topology (now guarded by `test_examples_lint_clean.py`).
  Layer 3: ledger entries and docs that were individually well-formed and collectively
  contradictory -- a class only a whole-repo read surfaces.
- **Scope of this change: documentation only.** No engine, handler, example or test code changed.
  Layer 2 and the guidance-eval instrument are marked in flight and are explicitly *not* claimed to
  exist.
- **Retirement conditions.** Section 5's retirement review has none: it is the mechanism that retires
  other machinery and cannot retire itself. Layer 3's ~15-merge cadence retires when a measured rate
  exists to replace the estimate. This document's missing self-guard (section 5) retires when that
  guard lands.
