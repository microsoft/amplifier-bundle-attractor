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
- It **classifies the change's decision-matrix tier** (section 3) and verifies that tier's toll was
  actually paid. A review that accepts an unclassified change has skipped the question the matrix
  exists to force.

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
| **Guidance surfaces** | `agents/`, `skills/`, `context/`, teaching content in `README.md` and `docs/` | **Guidance-eval evidence** from [`evals/guidance/`](../evals/guidance/README.md) -- the instrument shipped, and its 2026-08-15 baseline is the run every later run is read against. Run the scenarios whose `surfaces_under_test:` name the file you touched and paste the results table plus the decisive transcript quotes; a broad change -- a bundle recomposition, a doctrine amendment, a new guidance surface -- warrants the full six. Where the eval genuinely cannot reach the changed surface, say so in the PR in those words and fall back to a **fresh-session walk-through**: a session with no prior context follows only the changed text and arrives at the intended behavior |
| **Docs making factual claims** | any doc asserting a number, default, vocabulary, or behavior | A guard test pinning each load-bearing claim to **its source of truth in code**, following the existing guards (section 5, Layer 1). A page-only assertion ("the page says 500") is tautological: it passes forever and fails only when someone edits the page, which is the one case needing no guard. The assertion must read the value from the code and fail when the **code** moves |
| **Spec-relevant behavior** | anything that conforms to, diverges from, or extends the nlspec | A `specs/EXTENSIONS.md` entry and/or a `SPEC_CONFORMANCE.md` row, **in the same PR**, per the Compatibility doctrine. Entries obey the Entry Format: `depends-on:`, plus `upstream action:` in one of its legal forms whenever the banner states a divergence. Its **matrix tier** (section 3) sets the rest of the toll |

Two of these already have machinery behind them: the `EXTENSIONS.md` requirement is on the PR
checklist (`.github/PULL_REQUEST_TEMPLATE.md`), and the ledger's structural integrity is guarded in
CI. The rest are enforced by review today.

**Every change also carries a decision-matrix tier**, and the two compose rather than substitute for
each other. The row above says what the change owes for *what it can break*; section 3 says what it
owes for *which direction it moves relative to the nlspec*. A drifting engine change owes its live
run **and** its ledger entry **and** its conformance-matrix row.

**The Compatibility doctrine governs the last row.** Its four rules -- honor the nlspec design where
possible; 100% support for community `.dot` files written against the nlspec; extensions additive
and non-interfering; divergences only for safety, backed by measured evidence, and always loud --
are stated in full at the top of [`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) (maintainer ruling,
2026-08-14). Every disposition in that ledger is decided by them. Section 3 is the general form of
the same rule, applied past conformance to everything else.

---

## 3. The decision matrix

Maintainer ruling, 2026-08-15. The rule that decides which direction a change is allowed to move,
and what it owes for moving there:

Every change here is weighed against the `strongdm/attractor` nlspec -- not code alone, but
behavior, philosophy, decision-making, design-thinking, process and documentation alike. Movement
that brings this project **more aligned** with the spec is the easy path: supported by default,
carrying the presumption of yes. Movement that would **drift** us away from the spec is made
genuinely hard and is readily pushed back on -- permitted only on measured evidence, and only as a
loud, ledgered divergence. Movement into territory the spec **does not address** meets real
resistance, though less of it: the silence has to be argued rather than assumed, and what ships
there stays additive and non-interfering. That gradient is the steering rule of this project.

The gradient reaches past the conformance-bearing code the ledger tracks: examples, guidance
surfaces and process changes are classified by it too. [`docs/VISION.md`](VISION.md) states it as
the governing rule of the project; this section states what each tier costs before it merges.

| Tier | Direction relative to the nlspec | Posture | Toll |
|---|---|---|---|
| **Toward-spec** | closes a gap with the canonical spec | EASY / SUPPORTED -- presumption of yes | The normal evidence for its change class (section 2). **No ledger entry needed**: conforming is the default state, not a deviation |
| **Uncharted / extension** | the spec is silent here | RELATIVELY RESISTED | A stated justification for **why the spec's silence is not itself a signal**; proof the change is **additive and non-interfering** (a spec-conformant graph behaves identically); and a `specs/EXTENSIONS.md` entry in the same PR |
| **Drift** | moves away from what the spec specifies | REALLY HARD / readily pushed back on | **Measured** safety evidence -- the named safety property, plus the incident or measurement showing the spec-literal behavior actually failed; **loud** behavior, never a quiet resolution toward success; and a `SPEC_CONFORMANCE.md` ledger entry **plus a conformance-matrix row, in the same PR** |

**The drift row is the one with mechanical teeth.** Layer 2's coverage tripwire requires every
DIVERGE-disposition `ATX-*` row and every DIVERGES-bannered `specs/EXTENSIONS.md` entry to be cited
by at least one matrix row (section 5, Layer 2), so a drift cannot be ledgered without also being
asserted -- and a later silent movement *back* toward spec fails the assertion just as loudly.

**Uncharted is resisted, not forbidden.** Every shipped extension passed through that tier. What the
resistance buys is that the silence gets *argued* rather than assumed -- and the argument is
checkable later: at the `fb57a55` sync, upstream had absorbed `specs/EXTENSIONS.md` sections 1-7
item-for-item, which is what "the spec has not said this yet" looks like when the extension was
right.

**Classify explicitly, in the PR.** The tier is a claim a reviewer can disagree with, which is only
possible if it was stated. An unstated tier defaults, in practice, to "toward-spec" -- the cheapest
one -- which is exactly the failure this section exists to prevent.

Retirement condition: none. This is the project's steering rule, not scaffolding around a bug class.

---

## 4. "If you see something, do something"

Maintainer ruling, 2026-08-15. The vision refines over time, so it cannot only be examined when
someone sits down to examine it. During **any** work here -- including work with nothing to do with
the vision -- everyone, human or agent, watches for observations against the currently captured
vision in [`docs/VISION.md`](VISION.md), and captures them **without derailing the work at hand**.

**What counts as an observation.** Anything bearing on the captured vision: the repo drifting from
what the page says; the page drifting from what we actually believe; a shipped surface contradicting
a stated principle; a parked layer that reality has un-parked; a spec passage the vision should be
reading differently.

**How it is captured.**

- **A GitHub issue labeled `vision-observation`**, citing the `docs/VISION.md` passage -- or the spec
  passage -- it bears on, and what was seen. One observation per issue.
- **Plus an `## Observations` heading in the PR body** when one arises mid-PR, so the reviewer of
  that PR sees it without going looking. The heading is honest when empty: *"none arose"* is a
  finding, not a blank to fill.

**Observations are non-blocking.** They never gate the work that surfaced them, and never need
resolving in the PR that filed them. That is the property that makes the duty affordable: an
observation costs one issue, not a detour.

**Triage.** Open `vision-observation` issues are a named input to the Layer-3 holistic reviews
(section 5) and to wave checkpoints, read *as a set* -- one observation is often noise, and five of
them are a pattern.

**Resolution paths**, each recorded:

1. **A `docs/VISION.md` amendment**, through that page's own meta-protocol -- the maintainer's
   explicit word, evidence, a dated Changelog entry.
2. **A filed work item** -- the vision is right and the repo is not; the fix is ordinary work.
3. **A recorded decline, with the reason** -- closed, saying why it changes nothing. A declined
   observation that says why is a smaller version of the same value; a silently-closed one is a
   lost one.

Retirement condition: the duty has none. The label and the PR heading retire if the observation
stream proves empty across several Layer-3 cycles -- which would itself be evidence the vision has
stabilized, and the review that observed it would say so.

---

## 5. Drift defense in depth

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

Six test files in `modules/loop-pipeline/tests/`, run in CI on every PR. Each pins a documented
claim to something that fails when the *code* moves:

| Guard file | What it pins |
|---|---|
| `test_extensions_ledger_integrity.py` | `specs/EXTENSIONS.md` numbered headings form a contiguous `1..max` sequence -- no gaps, no duplicates -- and every `upstream action:` value is one of the legal forms, with `deferred` carrying a real `review-by` date. Written after a `git rebase -Xtheirs` silently discarded three already-merged ledger entries and left plausible-looking numbering behind |
| `test_doc_consistency.py` | The retry-ceiling default, read from the canonical spec snapshot and cross-checked against the authoring guide; and the `house` shape's LLM classification agreeing across `DOT-AUTHORING-GUIDE.md` and `DOT-SYNTAX.md` |
| `test_engine_semantics_doc_guard.py` | `context/engine-semantics.md`, the bundle's declared source of truth for shipped-engine behavior -- both text-anchored claims (the no-matching-edge and stale-label rules) and behavior-anchored ones (a real engine run asserting the main loop hard-fails on no matching edge) |
| `test_explainer_doc_guard.py` | The published explainer page, `docs/attractor-explained.html`: feedback-critique caps, the parallel-branch default, `last_response` truncation, the summary budgets, the fidelity vocabulary and its default, the lifecycle phases, and the shape-to-execution-tier vocabulary -- each read from its source module, never from the page |
| `test_examples_lint_clean.py` | Every `.dot` under `examples/` lints with zero ERROR diagnostics. Written because the dead-corrective-edge class shipped in eight examples for months, because nothing could see topology |
| `test_quality_protocol_guard.py` | This document's own external references: every guard-test filename it names exists; the two Layer-2 files exist and Layer 2 still reads *shipped*; the vendored canonical spec exists and the upstream SHA recorded here is the one `SPEC_CONFORMANCE.md`'s `SYNC-1` row records; the Changelog exists with a dated entry. Also the vision wiring (Q-304..Q-307): `docs/VISION.md` exists with its own dated Changelog and names the decision matrix; this page carries the decision-matrix section and the literal `vision-observation` label; and the decision matrix's canonical articulation reads identically in both pages. Written because section 7 set its own adoption condition and this file has to keep it (section 7) |

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
against the repo's stated vision -- which is [`docs/VISION.md`](VISION.md), not an inference.
**Open `vision-observation` issues are a named input** (section 4): they are the observations the
repo collected between reviews, read as a set. Output: findings with `file:line` evidence, filed as
issues -- not a report that gets read once.

Executed as an agent wave until now. **The executor is
[`examples/drift-review/`](../examples/drift-review/)** -- a self-review attractor pipeline (section
8), whose findings gate re-opens every cited `file:line` on *both* sides outside every reviewer's
context, and whose shape is guarded by
`modules/loop-pipeline/tests/test_drift_review_gate.py`. It reports; a human triages and files.

### Layer 4 -- the meta-protocol

Layers 0-3 are machinery, and machinery accretes. Section 7 governs how they are amended, and how
they are retired.

---

## 6. When the Layer-3 review fires

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

## 7. The meta-protocol -- improving the protocol

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

**This document's own guard.** It has one:
`modules/loop-pipeline/tests/test_quality_protocol_guard.py`. The stated adoption condition -- *the
first time one of those references is found stale, or the Layer-2 matrix lands, whichever comes
first* -- **fired with the matrix**, and the guard shipped in the same PR rather than in the next
one. It was extended with the vision wave (Q-304..Q-307). A protocol that defers its own rule while enforcing it on others is not a protocol; that is the
whole reason the condition was written with a trigger instead of an intention.

What it pins, each claim read from this page and resolved against the repository rather than against
the page itself: every guard-test filename named here resolves to a real file; the two Layer-2 files
exist and Layer 2's status still reads *shipped*; `specs/canonical/attractor-spec-canonical.md`
exists and the upstream SHA recorded in Layer 0 is the SHA `SPEC_CONFORMANCE.md`'s `SYNC-1` row
records; the Changelog this section mandates exists carrying at least one dated entry; and, since
the vision wave, that [`docs/VISION.md`](VISION.md) exists with its own dated Changelog and names
the decision matrix, that this page names the `vision-observation` label section 4 depends on, and
that the decision matrix's canonical articulation reads identically on both pages. It skips
wholesale in a checkout without this file, and fails loud otherwise.

**What is deliberately not guarded: the vision prose itself.** `docs/VISION.md` is judgment, not a
set of fact claims about code, and a guard over its wording would pin taste rather than truth. The
guards hold its *structure* (it exists, it has an amendment history, it states the governing rule)
and the one thing that can silently rot -- the decision matrix's articulation living on two pages.

**One reference remains unpinned, deliberately: the issue numbers.** The nine issue and PR numbers
cited on this page (#144, #146, #156, #172, #175, #182, #204, #223, #234) resolve only over the
network, and these suites do not reach it. That is a real remaining gap, named here in those words
rather than left for a reader to find; it belongs to the Layer-3 pass, not to CI.

Retirement condition: none while this page names external references. The guard narrows as the page
stops naming things, and retires only with the page.

---

## 8. Dogfooding

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

## 9. Lifting this model

Other repos in the ecosystem are welcome to take this. What transfers, and what does not:

**Portable.** The five-layer drift model (vendored truth -> deterministic guards -> executable
conformance matrix -> holistic semantic review -> meta-protocol); the arc (design -> build -> live
proof -> independent adversarial review -> maintainer's word); the *shape* of the change-class table,
one row per thing-that-can-break with the evidence it owes; and the meta-protocol itself --
evidence-gated amendments, named retirement conditions, a periodic retirement review. Those are about
the structure of a quality system, not about DOT graphs.

Two more, added with the vision wave and portable for the same reason: a **captured vision document**
governed by its own amendment meta-protocol (`docs/VISION.md`), so "the repo's stated vision" is a
file rather than an inference; and the pair that keeps it alive -- a **decision matrix** stating a
different posture and a different toll per direction relative to whatever your normative source is
(section 3), plus the **standing observation duty** with a label, a PR heading, non-blocking capture,
and named resolution paths (section 4). A repo with no upstream spec still has a direction it is
being steered in, and can still ask what each change costs relative to it.

**Intended follow-up, named not done:** promoting the vision-document + observation-convention
pattern into `amplifier-foundation`'s per-repo-conventions guidance, so other repos inherit the shape
instead of re-deriving it. That is a separate change in a separate repo; this PR does not make it.

**This-repo-specific.** `attractor lint` and its rule IDs; the particular ledgers
(`SPEC_CONFORMANCE.md`, `specs/EXTENSIONS.md`) and their entry formats; the six named guard files;
the pinned upstream SHA; the issue-pipeline lanes. Every one of those is the local answer to a
general question -- *what is your normative source, what pins your claims to it, what is the record
of deliberate deviation* -- and each repo's answer will look different.

The adaptation that matters is the honest one: name your own sources of truth, then ask which of the
five layers you actually lack. A repo with no upstream spec has no Layer 0 and should not invent one.

This repo's maintainers will help seed a customized version on request -- open an issue.

---

## Changelog

Amendments to this protocol, newest first. Each entry names the evidence that justified it.

### 2026-08-15 -- the guidance eval shipped; section 2's interim floor retired (entry 7)

- **Amended.** Section 2's **Guidance surfaces** row required "guidance-eval evidence *once the eval
  instrument ships*" and named a fresh-session walk-through as the floor until it did. The
  instrument shipped: [`evals/guidance/`](../evals/guidance/README.md) -- six scenarios, a rubric
  whose every criterion cites a canonical-spec section or a `docs/VISION.md` passage, and a harness
  that installs the bundle over the real `amplifier bundle add` path into a fresh container and
  grades the resulting sessions blind. The row now names it. The walk-through survives only as a
  stated-in-the-PR fallback for a surface the eval cannot reach.
- **Evidence that it earns its cost, at adoption.** The full six ran on 2026-08-15 against `main`
  @ `ed5bdef`: **three of six passed**, and the failures are findings about this bundle, not about
  the instrument. `attractor-expert`, asked whether a review node could decide it was done,
  answered "yes, the review node decides" and authored a self-report exit -- the project's central
  commitment, inverted, on the surface that exists to teach it. The objective layer proved
  unreachable by conversation: `/attractorify`, the objective runner and `attractor-expert` were
  never named. A gateless twelve-node chain was authored on request with no pushback, on a file
  whose own `attractor lint` run says "consider whether this pipeline should be a recipe instead".
  A fourth finding was visible only *across* scenarios, which is precisely what a per-PR
  walk-through cannot see: both authoring surfaces emit a DOT dialect the shipped engine does not
  use. Each is tracked in the issues filed from that baseline.
- **The cost it retires.** The interim floor was verification inside the context that produced the
  artifact -- a walk-through authored by the person whose change it validates, run against a working
  tree rather than an installed bundle, and not comparable to any other walk-through. That is the
  property section 1's independent-review rule refuses everywhere else in this document.
- **Scope of this change: documentation and `evals/` only.** No engine, handler, example or test
  code changed. Section 2's row is the only normative edit. The baseline table lives in
  `evals/guidance/README.md`, not here, and no run artifact is committed: the harness refuses to
  write results inside the checkout, so transcripts and prompts stay out of source by construction.
- **Retirement conditions.** The instrument's own are stated in `evals/guidance/README.md` and are
  governed by section 7's retirement review -- *what has it caught since the last review?* -- plus a
  hold-out discipline that requires one scenario rewritten from scratch, same property and new
  words, whenever several runs pass without finding anything. The walk-through fallback in the
  section 2 row retires when no change class remains that the eval cannot reach.

### 2026-08-15 -- Layer 3 gets an executor (entry 6)

- **Changed.** Layer 3's closing line flipped from *"that pipeline does not exist yet"* to naming
  [`examples/drift-review/`](../examples/drift-review/): the holistic review is now run by an
  attractor, with `check_findings.py` as the gate that decides which proposed findings are shaped
  and `modules/loop-pipeline/tests/test_drift_review_gate.py` guarding both. Nothing about Layer
  3's *scope* changed -- docs, examples, guidance surfaces and both ledgers, read against the
  canonical spec and `docs/VISION.md`.
- **Evidence that justified it: the triggers in section 6 fired, and the layer had no executor to
  answer them with.** More than fifteen PRs touching `modules/` and `docs/` have merged since the
  protocol was captured, and the conformance surface moved repeatedly (the matrix landed, the
  vision wave amended two pages). Layer 3 was the only layer whose defense was "someone will read
  everything carefully" -- which is the defense that silently does not happen. The first real pass
  was run with this executor before it merged; its report is the adoption evidence.
- **What the executor buys that a careful read does not.** Findings are *shaped* or they do not
  ship: each cites `file:line` on both sides -- the drifting surface and the normative passage it
  contradicts -- and the gate re-opens both files rather than believing either citation. That
  closes the gap an unstructured review leaves, where a plausible-sounding finding costs a human
  the read and proves nothing.
- **Deliberately not automated: filing.** The pipeline reports; a human verifies each finding and
  decides what becomes an issue or a `vision-observation` (section 4). Shape is not truth -- the
  gate proves a citation resolves, never that two passages actually contradict each other -- and a
  reviewer that acts on its own findings has re-entered the context it was built to stay outside
  of.
- **Scope of this change: additive.** No engine, handler or ledger *behavior* changed. One new
  example directory, one new guard test, one README gallery row, and this page's Layer-3 line.
- **Retirement condition.** The layer has none. The *executor* retires if the review it runs stops
  earning its cost under section 7's retirement review -- if it finds nothing across several
  cycles, that is itself the finding, and the review that observed it would say so.

### 2026-08-15 -- the decision matrix stated in authored prose (entry 5)

- **Changed.** Section 3's blockquote -- the maintainer's raw 2026-08-15 ruling, reproduced
  verbatim -- is replaced by a single authored articulation of the same rule. The attribution line,
  the three-tier toll table and every piece of wiring around it are untouched; only the quotation
  is. The identical paragraph is now the canonical statement in [`docs/VISION.md`](VISION.md), and
  `test_quality_protocol_guard.py`'s Q-307 pins the two copies to each other as before,
  re-anchored on the new text.
- **Evidence that justified it: the maintainer read the shipped page and ruled** (2026-08-15) that
  his verbatim words be replaced with an accurate representation of what he was communicating. A
  quote reproduces the phrasing of a conversation, including its shorthand; this page is read by
  contributors and agents who were not in that conversation, and the rule has to survive without
  it. The articulation is what the ruling *says*, stated once, in prose that stands alone.
- **Scope of this change: documentation only.** No engine, handler, example or ledger *behavior*
  changed. Q-307's anchors moved with the text; every Q-300..Q-307 assertion was re-proved red by
  mutation and restored byte-identically.
- **Retirement condition.** Unchanged: none. Section 3 is the project's steering rule, not
  scaffolding around a bug class.

### 2026-08-15 -- the decision matrix and the observation duty (entry 4)

- **Changed.** Two new sections, both maintainer rulings of 2026-08-15. **Section 3, "The decision
  matrix"** states the three postures toward the `strongdm/attractor` nlspec verbatim *(superseded
  by entry 5: the verbatim quote was replaced by an authored articulation of the same ruling)* and
  gives each tier its toll (toward-spec: presumption of yes, no ledger entry; uncharted: justify
  the silence, prove additive and non-interfering, `specs/EXTENSIONS.md` entry; drift: measured
  safety evidence, loud behavior, ledger entry **plus** a conformance-matrix row in the same PR).
  **Section 4, "If you see something, do something"** establishes the standing observation duty
  against [`docs/VISION.md`](VISION.md) -- a `vision-observation` issue plus an `## Observations`
  heading in the PR body, non-blocking, triaged into the Layer-3 reviews, with three recorded
  resolution paths.
  Wired in: section 1's adversarial-review duties now include classifying the tier and verifying its
  toll; section 2's table names the tier as a second, composing obligation; Layer 3 names
  `docs/VISION.md` as the vision it reads against and `vision-observation` issues as an input;
  section 9 adds both to the portable set.
- **Renumbering, stated rather than silent.** Old sections 3-7 are now 5-9. Cross-references
  throughout the page -- including in Changelog entries 1-3 -- were repointed at the new numbers, so
  every citation still resolves. No historical *claim* was altered; only the pointers. The one
  external citation (`specs/conformance/attractor-matrix.yaml`'s header comment) was updated in the
  same PR.
- **Evidence that these earn their cost.** The decision matrix is not new behavior -- it is the rule
  the maintainer has been steering by, and the Compatibility doctrine (`SPEC_CONFORMANCE.md`,
  2026-08-14) is it applied to conformance alone. What was missing was the general form and the
  price list: nothing said what a *philosophy* or *exemplar* change owed for moving away from the
  spec, and an unstated tier defaults in practice to the cheapest one. For the observation duty, the
  measured gap is Layer 3's own scope line: it already read "against the repo's stated vision" while
  no document stated it, and its findings only ever arrived in batches at review time -- so an
  observation noticed during unrelated work had nowhere to go but a reviewer's memory.
- **Scope of this change: documentation only.** No engine, handler, example or ledger *behavior*
  changed. `docs/VISION.md` is new; `test_quality_protocol_guard.py` gains Q-304..Q-307.
- **Proven red before green.** Each new assertion was mutated in a scratch copy and observed to fail
  naming the specific stale reference, then restored byte-identically. A guard never seen red is an
  unproven guard.
- **Deliberately unguarded: the vision prose.** `docs/VISION.md` is judgment, not fact claims about
  code. The guards hold its structure and the one thing that can silently rot -- the decision
  matrix's articulation living on two pages -- and nothing about its wording.
- **Retirement conditions.** The decision matrix has none: it is the project's steering rule, not
  scaffolding around a bug class. The observation duty's label and PR heading retire if the
  observation stream proves empty across several Layer-3 cycles, which would itself be a finding.
- **Intended follow-up, named not done.** Promoting the vision-document + observation-convention
  pattern into `amplifier-foundation`'s per-repo-conventions guidance (section 9). Separate repo,
  separate change.

### 2026-08-15 -- this document's own guard shipped (entry 3)

- **Changed.** Section 7's "**This document's own guard**" passage flipped from *owed next* to
  shipped, naming `modules/loop-pipeline/tests/test_quality_protocol_guard.py`. Layer 1 goes from
  five guard files to six and the new guard gets its table row. Section 2's and section 9's
  pointers at "the five existing guards" / "the five named guard files" were updated with it.
- **Evidence that justified it: the protocol's own rule fired, and the deferral was the finding.**
  Entry 2 recorded the adoption condition as met ("the matrix landed") and then deferred the guard
  to a later PR. Independent adversarial review of that same PR read section 2's "Docs making
  factual claims" row strictly and called the deferral what it was -- this page shipping a claim
  with no guard, in the PR whose whole subject is making records load-bearing. The condition was
  written with a trigger precisely so it could not be deferred by intention; honoring it in the
  triggering PR is the only reading that leaves the rule meaning anything.
- **What the guard asserts, and why none of it is tautological.** Four claim classes, each read
  from this page and resolved against the *repository*: the guard-test filenames it names exist;
  the two Layer-2 files exist and Layer 2 still reads *shipped*; the vendored canonical spec exists
  and the SHA recorded in Layer 0 matches `SPEC_CONFORMANCE.md`'s `SYNC-1` row; the Changelog
  exists with a dated entry. A page-only check ("the page says six") would pass forever and fail
  only on an edit to the page -- the one case needing no guard. These fail when the **repo** moves:
  a guard renamed, the matrix relocated, the canonical spec re-vendored to a new upstream SHA.
- **Proven red before green.** Each assertion was mutated in a scratch copy and observed to fail
  naming the specific stale reference, then restored. A guard never seen red is an unproven guard
  -- the same bar entry 2 held the matrix to.
- **Scope of this change: additive.** One new test module and this page. No engine, handler,
  example or ledger *behavior* changed.
- **Honest gap, named not hidden.** The nine issue/PR numbers on this page stay unpinned: they
  resolve only over the network and these suites do not reach it. Recorded in section 7 in those
  words, and assigned to the Layer-3 pass rather than to CI.
- **Retirement condition.** None while this page names external references. The guard narrows as
  the page stops naming things, and retires only with the page.

### 2026-08-15 -- Layer 2 shipped (entry 2)

- **Changed.** Section 5, Layer 2 flipped from *"in flight -- designed here, not built"* to shipped,
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
  (section 7) is now due**: its stated adoption condition -- "or the Layer-2 matrix lands" -- fired
  with this entry. *(Discharged by entry 3, same PR: the guard shipped rather than being deferred.)*

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
- **Retirement conditions.** Section 7's retirement review has none: it is the mechanism that retires
  other machinery and cannot retire itself. Layer 3's ~15-merge cadence retires when a measured rate
  exists to replace the estimate. This document's missing self-guard (section 7) retires when that
  guard lands.
