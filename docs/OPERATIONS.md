# Operating practice

How work gets proven **in this repo specifically**: what each class of change owes before it merges,
and the machinery that keeps this repo's own claims honest.

**This page is not the protocol.** The protocol is the ratified **converge PROTOCOL v2** -- vision
first, contracts frozen before work is derived from them, the DRAFT -> FROZEN lifecycle and its
Freeze Bar, the CANDIDATE amendment protocol, the conformance ledger and its standing reconcile, and
the owner attention budget. This repo is governed by that as written, and does not restate it. What
lives here is only what converge does not decide: the local practice, the local guards, the local
ledgers.

Where the binding contracts live is stated once, in [`VISION.md`](VISION.md) under "Governing
contracts": the engine seam's contracts and ledger belong to `amplifier-bundle-dot-runner`
(`contracts/external/`, `ledger/rows.yaml`), because this repo is the opinionated layer and owns no
engine seam.

It sits above the two files you already read: [`AGENTS.md`](../AGENTS.md) carries the always-on
conventions and the merge discipline; [`PRINCIPLES.md`](../PRINCIPLES.md) carries the upstream
contract and the "walk upstream first" rule.

**Scope note.** Nothing here is enforced by the engine. Some of it is enforced by CI (named below, by
file); the rest is enforced by review. Where a piece of machinery does not exist yet, this document
says so in those words rather than describing it in the present tense.

> **Provenance.** This page is the surviving repo-specific half of the retired
> `docs/QUALITY_PROTOCOL.md`. What retired, and where each section went, is recorded in that file's
> tombstone; its full amendment history (entries 1-8, 2026-08-15 through 2026-08-19) is in git.

---

## 1. The arc

Every non-trivial change follows the same four moves, in order. Skipping one is a decision stated out
loud in the PR, not an oversight discovered later.

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

**Live proof.** *"The test passes"* is not *"it works."* A green unit suite proves the code does what
its author expected, in the author's own frame. Live proof means the real thing running in a real
environment: a real pipeline run through the real parser, engine and handler dispatch; a real
provider where the claim is about a provider; a really-killed process where the claim is about crash
recovery. This repo has the scar tissue -- bugs have shipped with green unit tests and failed on
first real-graph run (`AGENTS.md`, "Verification gradient"), which is why
`modules/loop-pipeline/tests/test_live_graph_gate.py` now runs in CI as the permanent hermetic floor.
A floor, not a ceiling: if the changed path is not one of the behaviors that file covers, do the live
run yourself and paste the evidence.

**Independent adversarial review.** A *fresh* session that did not produce the work, re-executes its
evidence, and tries to break it. Three properties are non-negotiable:

- It **re-runs** the evidence rather than reading the author's summary of it. A review that only
  reads is not a review. Findings cite `file:line`.
- It is **independent of the context that produced the artifact**. Verification inside the producing
  context is not verification -- a worker that knows what the gate reads can write what the gate
  reads. That is the same property `specs/EXTENSIONS.md` section 25 (fail-closed goal-gate outcomes)
  buys structurally, applied to human and agent review.
- It **classifies the change's decision-matrix tier** (section 3) and verifies that tier's toll was
  actually paid. A review that accepts an unclassified change has skipped the question the matrix
  exists to force.

**The fifth move is the owner's, and it is not this page's to define.** No merge without the
maintainer's explicit word. Ratification is one of the four things the owner is in the loop for under
converge PROTOCOL v2's attention budget; the local mechanics -- the required
`CI Gate (all checks passed)` status check, and the narrow, legitimate use of `--admin` -- are
specified in `AGENTS.md` under "Merge discipline: CI Gate is required, never bypass it". That section
governs; this one does not restate it.

---

## 2. What each class of change has to prove

The evidence a change owes is a function of what it can break. This table is the floor. A reviewer
may ask for more, never less. Every row is local: the classes are this repo's classes and the
instruments are this repo's instruments.

| Class | Examples | Required evidence before merge |
|---|---|---|
| **Engine / handler code** | `engine.py`, `handlers/*`, dispatch, routing, retry, checkpoint | Full module suites green **and** a live pipeline run exercising the changed path (paste the `events.jsonl` slice) **and** independent adversarial review **and** a ledger entry if the change is spec-relevant (last row) |
| **Exemplar / example graphs** | `examples/pipelines/*.dot`, `examples/patterns/*.dot`, `examples/objective/*` | `dot-runner lint` with **zero ERROR** diagnostics -- warnings are informational, which is exactly the line `modules/loop-pipeline/tests/test_examples_lint_clean.py` enforces -- **and** at least one live convergence run **and** the graph's own gates demonstrated **RED and GREEN**: a negative control proving the gate can fail, a positive control proving it can pass. A gate only ever seen green is an unproven gate |
| **Guidance surfaces** | `agents/`, `skills/`, `context/`, teaching content in `README.md` and `docs/` | **Guidance-eval evidence** from [`evals/guidance/`](../evals/guidance/README.md) -- the instrument shipped, and its 2026-08-15 baseline is the run every later run is read against. Run the scenarios whose `surfaces_under_test:` name the file you touched and paste the results table plus the decisive transcript quotes; a broad change -- a bundle recomposition, a doctrine amendment, a new guidance surface -- warrants the full six. Where the eval genuinely cannot reach the changed surface, say so in the PR in those words and fall back to a **fresh-session walk-through**: a session with no prior context follows only the changed text and arrives at the intended behavior |
| **Docs making factual claims** | any doc asserting a number, default, vocabulary, or behavior | A guard test pinning each load-bearing claim to **its source of truth in code**, following the existing guards (section 5, Layer 1). A page-only assertion ("the page says 500") is tautological: it passes forever and fails only when someone edits the page, which is the one case needing no guard. The assertion must read the value from the code and fail when the **code** moves |
| **New public content class** | a new top-level directory; a new artifact type that reaches users (run artifacts, published pages, generated reports); docs carrying real-run evidence; a new fixture corpus | The deterministic leak guards green **and** a **leak-lens review** (section 7): a fresh-context reviewer reads the diff under the outsider brief and reports what it identifies. Both, not either -- a passing grep is not the semantic read, which is precisely how the 2026-08-19 incident got through |
| **Spec-relevant behavior** | anything that conforms to, diverges from, or extends the nlspec | A `specs/EXTENSIONS.md` entry and/or a `SPEC_CONFORMANCE.md` row, **in the same PR**, per the Compatibility doctrine. Entries obey the Entry Format: `depends-on:`, plus `upstream action:` in one of its legal forms whenever the banner states a divergence. Its **matrix tier** (section 3) sets the rest of the toll |

Three of these already have machinery behind them: the `EXTENSIONS.md` requirement is on the PR
checklist (`.github/PULL_REQUEST_TEMPLATE.md`), the leak-lens review is on that same checklist as an
honest-N/A line, and the ledger's structural integrity is guarded in CI. The rest are enforced by
review today.

**The new-public-content row names a reviewer obligation, not just an artifact.** Its evidence is a
*reading*, performed by someone who did not write the content -- the same independence property
section 1 requires of adversarial review, for the same reason. Section 7 defines the brief and why a
grep cannot substitute for it.

**Every change also carries a decision-matrix tier**, and the two compose rather than substitute for
each other. The row above says what the change owes for *what it can break*; section 3 says what it
owes for *which direction it moves relative to the nlspec*. A drifting engine change owes its live
run **and** its ledger entry **and** its conformance-matrix row.

**The Compatibility doctrine governs the last row.** Its four rules -- honor the nlspec design where
possible; 100% support for community `.dot` files written against the nlspec; extensions additive and
non-interfering; divergences only for safety, backed by measured evidence, and always loud -- are
stated in full at the top of [`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) (maintainer ruling,
2026-08-14). Every disposition in that ledger is decided by them.

---

## 3. The decision matrix's tolls

**The rule itself has one home, and it is not this page.** The decision matrix -- the maintainer's
2026-08-15 ruling, three postures toward the `strongdm/attractor` nlspec -- is stated in
[`VISION.md`](VISION.md) under "Our relationship to the nlspec". Read it there. It was previously
restated here word-for-word, and the cost of that duplication was a guard whose whole job was to
detect the drift the second copy made possible; the copy retired, the guard was re-aimed to pin the
single home.

The gradient reaches past the conformance-bearing code the ledger tracks: examples, guidance surfaces
and process changes are classified by it too. This section prices it: what each tier owes before it
merges.

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

Retirement condition: none. These are the local tolls on the project's steering rule.

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

1. **A `docs/VISION.md` amendment**, through the converge amendment protocol -- the owner's explicit
   word, evidence, a dated Changelog entry on that page.
2. **A filed work item** -- the vision is right and the repo is not; the fix is ordinary work.
3. **A recorded decline, with the reason** -- closed, saying why it changes nothing. A declined
   observation that says why is a smaller version of the same value; a silently-closed one is a lost
   one.

Retirement condition: the duty has none. The label and the PR heading retire if the observation
stream proves empty across several Layer-3 cycles -- which would itself be evidence the vision has
stabilized, and the review that observed it would say so.

---

## 5. Drift defense in depth

Four layers of local machinery. Each catches a class the layer below cannot see.

### Layer 0 -- vendored truth

`specs/canonical/*-canonical.md` is the upstream nlspec, vendored and pinned byte-for-byte to
`strongdm/attractor` @ **`fb57a55`** (`SPEC_CONFORMANCE.md`, `SYNC-1`). It is the normative text: when
the shipped engine and a doc disagree about what the spec says, this is what settles it.

Any upstream movement is a **SYNC event** -- re-vendor, then re-read every ledger entry whose
disposition depended on the old text. The precedent exists: at `fb57a55`, upstream had absorbed
`specs/EXTENSIONS.md` sections 1-7 item-for-item, and each now carries a
`status: ABSORBED UPSTREAM @ <sha>` banner naming the canonical section that supersedes it.

Upstream is currently dormant -- four ledger entries carry `upstream action: declining` citing exactly
that. The check stays anyway: it costs one comparison, and dormancy is a fact about today, not a
property of the repo.

### Layer 1 -- deterministic guards

Six test files, run in CI on every PR. Each pins a documented claim to something that fails when the
*code* moves:

| Guard file | What it pins |
|---|---|
| `test_extensions_ledger_integrity.py` | `specs/EXTENSIONS.md` numbered headings form a contiguous `1..max` sequence -- no gaps, no duplicates -- and every `upstream action:` value is one of the legal forms, with `deferred` carrying a real `review-by` date. Written after a `git rebase -Xtheirs` silently discarded three already-merged ledger entries and left plausible-looking numbering behind |
| `tests/test_doc_consistency.py` | The retry-ceiling default, read from the canonical spec snapshot and cross-checked against the authoring guide; and the `house` shape's LLM classification agreeing across `DOT-AUTHORING-GUIDE.md` and `DOT-SYNTAX.md` |
| `tests/test_engine_semantics_doc_guard.py` | `context/engine-semantics.md`, the bundle's declared source of truth for shipped-engine behavior -- the text-anchored claims (the no-matching-edge and stale-label rules); the behavior-anchored half (a real engine run asserting the main loop hard-fails on no matching edge) rides with the engine module's own tests |
| `tests/test_explainer_doc_guard.py` | The published explainer page, `docs/attractor-explained.html`: feedback-critique caps, the parallel-branch default, `last_response` truncation, the summary budgets, the fidelity vocabulary and its default, the lifecycle phases, and the shape-to-execution-tier vocabulary -- each read from its source module, never from the page |
| `test_examples_lint_clean.py` | Every `.dot` under `examples/` lints with zero ERROR diagnostics. Written because the dead-corrective-edge class shipped in eight examples for months, because nothing could see topology |
| `tests/test_quality_protocol_guard.py` | This page's own external references: every guard-test filename it names exists; the two Layer-2 files exist and Layer 2 still reads *shipped*; the vendored canonical spec exists and the upstream SHA recorded here is the one `SPEC_CONFORMANCE.md`'s `SYNC-1` row records. Also the vision wiring (Q-304..Q-307): `docs/VISION.md` exists with its own dated Changelog and names the decision matrix; this page carries the decision-matrix tolls section and the literal `vision-observation` label; and the decision matrix's canonical articulation exists **exactly once** across the docs corpus, in `docs/VISION.md`, matching a recorded constant. Also the leak-defense wiring (Q-308..Q-312): the pre-publication section exists naming all three layers, the outsider brief appears verbatim, the two reference implementations it names exist on disk, `.github/PULL_REQUEST_TEMPLATE.md` carries the leak-review line, and the page names both incidents that justify the duty |

Bare filenames above resolve under `modules/loop-pipeline/tests/`; path-qualified ones resolve as
written.

**The rule this layer imposes: a new claim-bearing doc ships with its guard.** The explainer guard
states the reason plainly -- a page nobody re-reads rots silently and keeps being shared, which is
strictly worse than an internal doc going stale.

### Layer 2 -- executable conformance matrix

**Status: shipped (tranche 1).** Two files, run in CI on every PR inside the existing loop-pipeline
job:

| File | What it is |
|---|---|
| `specs/conformance/attractor-matrix.yaml` | The matrix itself -- a reviewed *document*, one row per normative statement cluster, carrying the verbatim spec quote, the disposition, the ledger cite, and the assertion |
| `modules/loop-pipeline/tests/test_spec_conformance_matrix.py` | The runner -- per-row structural integrity, in-process behavioral probes, the upstream-sync sha pin, and the coverage tripwire |

Tranche 1 covers every decided divergence, every OPEN ledger item, the load-bearing conformances, and
the SYNC row. Later tranches extend it section by section; ULM/CAL matrices are named as tranche 3.
The design record is
[`docs/designs/2026-08-15-conformance-matrix.md`](designs/2026-08-15-conformance-matrix.md).

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
must change**. The failure is a prompt to update `SPEC_CONFORMANCE.md` or `specs/EXTENSIONS.md`, or to
revert -- never to edit the assertion.

Two mechanisms keep that honest. A **coverage tripwire** requires every DIVERGES-bannered
`EXTENSIONS.md` entry and every DIVERGE-disposition `ATX-*` row to be cited by at least one matrix
row, so a future divergence cannot be ledgered without also being asserted. And the **SYNC row** pins
the canonical file's sha256, turning a re-vendor from a quiet commit into a demanded full-matrix
re-review -- which is exactly the work the `fb57a55` sync required by hand.

Retirement condition: none for the mechanism. Individual rows retire when upstream absorbs the
divergence (the `ABSORBED UPSTREAM` banner protocol) or when a decision closes an `OPEN-PINNED` row --
in both cases the row changes disposition rather than disappearing.

### Layer 3 -- periodic holistic semantic review

Layers 0-2 are local: each checks one claim, one file, one section. None of them can see that the
README teaches one mental model while an exemplar demonstrates another, or that a ledger entry is
individually well-formed and collectively obsolete. That is a semantic reading of the whole, and it
has to be done as one.

Scope: docs, examples, guidance surfaces, and both ledgers, read against the canonical spec **and**
against the repo's stated vision -- which is [`docs/VISION.md`](VISION.md), not an inference. **Open
`vision-observation` issues are a named input** (section 4): they are the observations the repo
collected between reviews, read as a set. Output: findings with `file:line` evidence, filed as issues
-- not a report that gets read once.

**The executor is [`examples/drift-review/`](../examples/drift-review/)** -- a self-review attractor
pipeline, whose findings gate re-opens every cited `file:line` on *both* sides outside every
reviewer's context, and whose shape is guarded by `tests/test_drift_review_gate.py`. It reports; a
human triages and files.

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

## 7. Pre-publication leak defense

Section 5 asks whether what this repo *says* is true. This section asks a different question about
the same artifacts: whether what this repo *publishes* is ours to publish. The asymmetry is what makes
it a separate defense -- a wrong claim is corrected by the next commit, while a leaked value is in the
world's clones, forks and caches the moment it lands, and no later commit takes it back. The defense
therefore runs **before** the push, and it is the one place in this document where a false positive is
cheap and a false negative is unrecoverable.

**Two measured incidents shape everything below**, and they are the evidence this section exists on: a
live provider key reached public run artifacts on **2026-08-11**, and maintainer host-name literals
reached a new skill's shipped files on **2026-08-19**. The second is the one that shapes these
checks -- BOTH a static deny-list guard and a grep-armed adversarial reviewer passed it, and only a
stranger reading the diff caught it.

**The layers here are numbered locally.** They are *not* section 5's drift layers -- different threat,
different machinery, different retirement conditions. Where both are meant, they are named apart.

### The three layers

Each layer catches a class the others structurally cannot, and each is defined as much by **where its
data lives** as by what it matches. The shipped reference implementation is
`skills/attractor-scout/tests/test_no_real_data_leak.py`, which runs all three over the skill's
shipped source and docs -- a defined set of text suffixes, with narrow documented exemptions (caches,
and the scanner's own pattern definitions) -- on every suite invocation.

**Layer 1 -- generic shapes, committed.** Patterns matching the *shape* of private data without
naming any instance of it: v4-UUID session ids, `-home-<user>`-shaped workspace slugs, personal path
prefixes, database and graph endpoints, e-mail shape, common secret-key prefixes. These literals are
safe to commit precisely because a shape reveals no value -- the pattern for "an API key looks like
this" is not an API key. The canonical sibling on the run-artifact side is
`.github/capsule-pipeline/scrub_secrets.py`: the deterministic scrubber built after the 2026-08-11 key
leak, together with its residual gates. It is also the canonical, drift-tripwire-pinned pattern source
for the write-seam redaction the observability persister applies as artifacts are written -- its
patterns ported from that scrubber -- so a value is scrubbed where it is written rather than only
where it is uploaded.

**Layer 2 -- environment identity, DERIVED at runtime, never stored.** The guard asks the executing
machine who it is -- hostname (including its short form), login user, home directory, git `user.name`
and `user.email` -- and asserts that none of those strings appear in any shipped file. What is
committed is the *derivation*, never a value. This catches **any** contributor's identity by
construction, on their own machine, in the file they just wrote; on a CI runner it harmlessly checks
the runner's own identity. A static deny-list cannot do this, and the reason is not merely that such a
list is always incomplete: **committing an identity term to a deny-list publishes that term.** A
deny-list of secrets is a list of secrets. That is the self-defeat the 2026-08-19 incident
demonstrated in production, and Layer 2 is the structural answer to it. Values below a minimum length
are skipped, so a short username cannot flag every file in the repo.

**Layer 3 -- local deny-list, outside the repo.** The identity terms Layer 2 cannot derive -- other
machines, project codenames, collaborators' handles -- live in a file on the contributor's own disk: a
path named by an environment variable, or `~/.amplifier/leak-denylist.txt` by default. When the file
is absent the layer skips silently, which is the expected and correct state on CI. The rule that makes
this layer work is the one it exists to enforce: **identity values never land in the repo** -- not in a
fixture, not in a comment, and not in the list of things being forbidden. A failure message from this
layer names how many terms matched and withholds which, for the same reason.

### The leak-lens review duty

Deterministic layers match what someone already thought to describe. The 2026-08-19 incident is the
counter-example that earns this duty: maintainer host-name literals were hardcoded across four shipped
files of a new skill, and **both** existing defenses passed them. The skill's own static deny-list
guard missed them because the literals were not in the list -- and adding them would have published
them. The independent adversarial review's leak-attack missed them because it executed a list of greps
instead of reading the diff as a stranger. What caught them was a maintainer-prompted manual read by a
reader asked to look at the change with no context and say what it revealed.

So: **any PR that introduces a NEW public content class gets a semantic pre-publication review by a
fresh-context reviewer**, briefed verbatim:

> *"Read this diff as a stranger. List everything that identifies a person, a machine, an
> organization, an internal project, or a private process."*

A **new public content class** is a new top-level directory; a new artifact type that reaches users;
docs carrying real-run evidence; or a new fixture corpus. Section 2's evidence table carries the same
row, and `.github/PULL_REQUEST_TEMPLATE.md` carries the checklist line.

**This duty is explicitly distinct from confirming that the greps pass.** A grep-armed reviewer is
answering *does anything match the patterns we already wrote down* -- which is the question the
deterministic layers answer faster, cheaper and more reliably than any human will. The outsider brief
asks the question no pattern encodes: *what does this text tell a stranger about us.* Incident 2 is
the proof that these are different questions, because a capable reviewer answered the first one
correctly and shipped the leak anyway.

The reviewer must be **fresh-context** for the same reason section 1's adversarial review must be
independent of the context that produced the artifact: a reader who watched the content being written
cannot un-know what the strings mean. To their author, an identity literal reads as ordinary
configuration; that is exactly why it survives every review that is not performed by a stranger.

**Honest N/A is a valid answer, and the common one** -- most PRs introduce no new public content class
at all. The checklist line takes the reason in one line rather than inviting a checkbox to be ticked
past.

### Retirement conditions

- **Layer 1** retires per-pattern, not wholesale: an individual shape retires when the data class it
  matches stops being produced -- an endpoint form no longer used, a key prefix no longer issued. The
  layer itself retires only with the artifact classes it scans.
- **Layers 2 and 3** retire together when the ecosystem ships a centralized, identity-aware scrubber
  that these guards can delegate to -- one place deriving environment identity and one place holding
  the local terms, with the per-skill copies deleted rather than left in place. Until that exists,
  duplicated derivation is the cost of the guarantee.
- **The leak-lens duty** retires only on **measured** evidence that the deterministic layers catch the
  semantic class: an eval in which content carrying an outsider-visible identifier that no committed
  pattern names is caught by the guards alone. The burden sits on the evidence and not on the absence
  of incidents -- the duty exists precisely because two defenses passed a real leak, and "nothing has
  leaked since" is what a working duty and an unnecessary one look like from outside.

---

## 8. Machinery hygiene

**How this machinery is amended is not this page's rule.** Amendment evidence, ratification, dated
records and the freeze/amend lifecycle are converge PROTOCOL v2's, and this repo follows it as
written. Two things stay local, because they are about *this* repo's machinery rather than about the
protocol.

**The retirement review** runs on the same triggers as Layer 3 (section 6) and asks two questions of
every piece of machinery in Layers 0-3:

1. **What has it caught since the last review?** Nothing is a finding, not a pass. A guard that has
   never fired is either protecting a genuinely closed hole or asserting something that cannot break,
   and those have different answers.
2. **Does it still earn its keep?** The cost is real: runtime, review attention, and the friction it
   adds to every unrelated change that has to walk past it.

Machinery whose retirement condition has fired is **removed in the same wave** -- not deprecated, not
left in place "just in case." This is the self-ablation discipline, and it is the half of process
design that usually goes undone: yesterday's scaffolding is today's tax. Every guard, gate and rule
added names its retirement condition where one exists; some have none, and saying so explicitly is a
valid answer while silence is not. `specs/EXTENSIONS.md` section 27 already carries a "Guard
retirement inventory" -- that is the shape.

**This page's own guard.** It has one: `tests/test_quality_protocol_guard.py`, inherited from the
retired protocol page and re-aimed here. Each claim it holds is read from this page and resolved
against the repository rather than against the page itself -- which is what makes the assertions
non-tautological. What it pins is listed in Layer 1's table above.

**What is deliberately not guarded: the vision prose itself.** `docs/VISION.md` is judgment, not a set
of fact claims about code, and a guard over its wording would pin taste rather than truth. The guards
hold its *structure* (it exists, it has an amendment history, it states the governing rule) and the
one thing that could silently rot -- the decision matrix's articulation, now pinned to a single home.

**One reference remains unpinned, deliberately: the issue numbers.** The issue and PR numbers cited on
this page resolve only over the network, and these suites do not reach it. That is a real remaining
gap, named here in those words rather than left for a reader to find; it belongs to the Layer-3 pass,
not to CI.

Retirement condition: none while this page names external references. The guard narrows as the page
stops naming things, and retires only with the page.

---

## 9. Dogfooding

Improvements to this repo that fit the pipelines' weight class go through the repo's own lanes.
[`docs/ISSUE_PIPELINE.md`](ISSUE_PIPELINE.md) documents both -- the defect lane (`ready:spec`) and the
feature lane (`ready:feature-spec`), each maintainer-triggered, each ending at a human review gate.
The pipeline never merges its own work.

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
specify. Choosing to hand-build is a normal call, and it changes nothing about section 1: the same arc
applies, with the same live proof and the same independent review.

**Either way, the acceptance criteria live on the issue first.** In the feature lane that is already
structural -- criteria must be posted as a maintainer comment before the label, because a comment's
author role is computed server-side and cannot be forged, while an issue body can be edited by anyone
after the fact (`docs/ISSUE_PIPELINE.md`). Hand-built work inherits the discipline for the same reason
it exists: criteria written after the work is done describe the work rather than test it.
