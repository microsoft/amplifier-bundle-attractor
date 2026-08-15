# Drift Review -- the Layer-3 executor

The periodic holistic semantic review from
[`docs/QUALITY_PROTOCOL.md` section 5](../../docs/QUALITY_PROTOCOL.md), built as
an attractor. The repo reviews itself with its own engine.

| | |
|---|---|
| **Graph** | [`drift-review.dot`](drift-review.dot) |
| **Gate** | [`check_findings.py`](check_findings.py) -- the finding-shape validator |
| **Contract** | [`finding-contract.md`](finding-contract.md) -- what a finding must be |
| **Guard** | `modules/loop-pipeline/tests/test_drift_review_gate.py` |

---

## What Layer 3 is

`QUALITY_PROTOCOL.md` names five drift layers. Four of them are already
mechanical, and all four are **local** -- each checks one claim, one file, one
section:

| Layer | What it is | What it cannot see |
|---|---|---|
| 0 | the vendored upstream nlspec, pinned byte-for-byte | anything about *this* repo |
| 1 | six guard tests pinning documented claims to the code they came from | a claim nobody wrote a guard for |
| 2 | the executable conformance matrix asserting every decided divergence | anything outside the conformance surface |
| 3 | **this** | -- |
| 4 | the meta-protocol governing layers 0-3 | -- |

Layer 3's own words for the gap it fills:

> "None of them can see that the README teaches one mental model while an
> exemplar demonstrates another, or that a ledger entry is individually
> well-formed and collectively obsolete. That is a semantic reading of the
> whole, and it has to be done as one."

So Layer 3 catches what only judgment sees: a doc paragraph teaching retired
behavior, an example contradicting doctrine, vocabulary drifting from the
spec's, a ledger row that parses cleanly and stopped describing reality.

Scope, per the protocol: **docs, examples, guidance surfaces, and both ledgers**,
read against the canonical spec *and* against
[`docs/VISION.md`](../../docs/VISION.md). Open `vision-observation` issues are a
named input (section 4) -- read as a set, because one observation is often noise
and five of them are a pattern.

---

## When it fires

Section 6's triggers, verbatim in summary. Whichever fires first, fires:

- **Every ~15 merged PRs touching `modules/` or `docs/`** -- the cadence knob.
- **Any PR that adds or edits an `EXTENSIONS.md` section or a
  `SPEC_CONFORMANCE.md` ledger row** -- the conformance surface moved, so the
  claims around it may no longer be true.
- **Any upstream spec movement** -- a SYNC event (Layer 0).
- **Any incident or postmortem naming a doc-vs-code contradiction** --
  immediate, and scoped to that surface class.
- **A quarterly floor**, regardless. A quiet quarter is not evidence of a
  correct repo.

---

## How to run it

The repository under review is the **process working directory**. There is
deliberately no `--param` for it: a second path to configure is a second way to
review the wrong tree while believing you reviewed the right one. `preflight`
asserts the normative sources are present in the cwd and refuses otherwise.

```bash
RD="$PWD/examples/drift-review"      # absolute; the gates run from here
cd <the repository under review>     # usually the same checkout

attractor run "$RD/drift-review.dot" \
    --param goal="Layer-3 holistic drift review of this repository" \
    --param runner_dir="$RD" \
    --cwd .
```

Everything the run writes lands under `.drift-review/` in the working
directory. It is scratch state, not a deliverable to commit -- copy
`report.md` and `findings.json` wherever the evidence for that review belongs.

| Parameter | Default | What it is |
|---|---|---|
| `goal` | *(required)* | the review objective, carried into every worker's prompt |
| `runner_dir` | *(required)* | absolute path to this directory |
| `max_revisions` | 2 | findings-repair budget -- at most 3 gate passes |
| `max_reports` | 2 | report-repair budget -- at most 3 gate passes |

### Outputs

| Path | What it is |
|---|---|
| `.drift-review/report.md` | **the deliverable** -- what was swept, every finding with both sides quoted |
| `.drift-review/findings.json` | the admitted corpus, machine-readable, sorted by severity |
| `.drift-review/findings-report.txt` | the gate's admission record: what it accepted and what it rejected, with reasons, plus the COVERAGE reconciliation |
| `.drift-review/coverage.txt` | one measured `<class>: swept/inventory (pct)` line per class -- the numbers `report.md` is required to carry verbatim |
| `.drift-review/disposition` | `findings` \| `clean` \| `escalated` -- how an unattended caller tells the outcomes apart |
| `.drift-review/inventory/` | the four surface lists the reviewers were scoped to, and their counts |

---

## The shape

```
start
  -> preflight        (code)  admission: normative sources present, tools on PATH
  -> inventory        (code)  git ls-files -> four claim-bearing surface classes
  -> review_fanout    (fan-out, wait_all, error_policy=continue, max_parallel=4)
       |- review_core_docs  (LLM)  README, root conventions, docs/
       |- review_examples   (LLM)  examples/ -- graphs, guides, gate primitives
       |- review_guidance   (LLM)  agents/, skills/, context/, behaviors/, bundles/
       `- review_ledgers    (LLM)  SPEC_CONFORMANCE.md, specs/
  -> reviews_join     (fan-in)
  -> findings_gate    (code)  check_findings.py -- SHAPE, re-reading every citation
       |- findings_ok       -> consolidate
       |- findings_bad      -> revise -> back to findings_gate   [budget: max_revisions]
       `- revise_exhausted  -> postmortem
  -> consolidate      (LLM)   writes .drift-review/report.md
  -> report_gate      (code)  goal gate: every admitted finding appears in the report
       |- report_ok         -> done
       |- report_bad        -> consolidate                       [budget: max_reports]
       `- report_exhausted  -> postmortem
  -> done             (the single exit)

postmortem (LLM) -> escalated (code, exit 1) -- the only red path
```

**Four classes, because they fail differently.** A doc lies in prose. An
exemplar lies by demonstration -- worse, because a reader copies it. A guidance
surface lies to an agent nobody is watching. A ledger lies by staying true
sentence-by-sentence while its disposition stops describing reality.

**Each reviewer runs in its own context** and never sees the others' work. That
independence is the point: four correlated reviewers are one reviewer with a
larger bill.

### The gates

| Gate | Contract |
|---|---|
| `preflight` | **Refuse before spending an LLM.** The five normative sources exist in the cwd; `check_findings.py` and `finding-contract.md` exist under `runner_dir`; `git`, `python3`, `grep`, `sort`, `wc` are on PATH. `ready`, or `blocked` and exit 1. |
| `inventory` | **Scope is a file, not a recollection.** Four lists from `git ls-files` -- only tracked files, because an untracked scratch file makes no claim on anyone. A class matching nothing is a machinery failure (`no_surfaces`, exit 1), never an empty result: a review that swept an empty list would report "clean" about surfaces it never opened. |
| `findings_gate` | **Every finding cites `file:line` on both sides, and the gate re-opens both files.** Shape, not truth. Also **reconciles each reviewer's `swept` array against the inventory on disk** and publishes the coverage it measured. `findings_ok` \| `findings_bad` \| `revise_exhausted`; nonzero exit means the gate itself could not run, which routes to the loud terminal rather than into the repair loop. |
| `report_gate` | **The exit is unreachable if the report dropped a finding, or overstated the sweep.** Re-derives the ids from `findings.json` -- it never believes the report's own table -- requires all four class names to appear, and requires every line of `coverage.txt` verbatim, so neither a quietly-skipped class nor a quietly-skipped half of a class can read as a clean one. Also writes `disposition`. |

### Honest exits

| Outcome | Exit | Disposition | Meaning |
|---|---|---|---|
| findings present | `done`, green | `findings` | **The review succeeded.** Finding drift is the job. |
| zero findings | `done`, green | `clean` | The review succeeded and says what it swept. |
| machinery failure | `escalated`, **exit 1** | `escalated` | A gate could not run, a budget was spent without converging, or no corpus was produced. |

A review that finds drift has not failed, and this graph will not let that
confusion into the exit code. What is red is *the instrument breaking* --
because an instrument that fails quietly is worse than no instrument, and a
clean report from a broken sweep is the exact failure Layer 3 exists to prevent.

---

## How findings flow

```
pipeline  ->  report.md + findings.json
                 |
                 v
human     ->  VERIFY each finding: open both cited sides. Is the drift real?
                 |
      +----------+----------+
      |                     |
      v                     v
   real                  not real
      |                     |
      v                     v
 file a GitHub issue    record the decline, with the reason
 (or a `vision-observation`
  issue when it bears on
  docs/VISION.md)
```

**The pipeline never files anything, and never fixes anything.** That separation
is the design, not an omission:

- **A reviewer that acts on its own findings has no independent check left.**
  The whole reason `findings_gate` sits outside every worker's context is that
  verification inside the context that produced the evidence is not
  verification. An auto-filing reviewer re-enters that context by the back door.
- **Shape is not truth.** `check_findings.py` proves a citation *resolves*. It
  cannot prove the two passages actually contradict each other -- that is
  judgment, and judgment is what a human is for. A finding that survives the
  gate is a *checkable claim*, not an established fact.
- **Filing is consequential.** An issue costs someone's attention, and
  `docs/VISION.md` records attention as the budgeted resource. A machine that
  can spend it without a person in the loop will.

Per `QUALITY_PROTOCOL.md` section 4, a verified finding that bears on the vision
becomes a **`vision-observation` issue** citing the passage it bears on -- one
observation per issue. A declined finding gets a **recorded decline with the
reason**: "a declined observation that says why is a smaller version of the same
value; a silently-closed one is a lost one."

---

## The finding contract, in one paragraph

Every finding cites `file:line` on **both** sides -- the drifting surface and
the normative passage it contradicts -- with a quote that resolves, verbatim,
against the tree. The `contradicts` side must be one of `specs/canonical/`,
`docs/VISION.md`, `SPEC_CONFORMANCE.md`, `specs/EXTENSIONS.md`, or
`specs/conformance/`; that closed set *is* the definition of drift, and it is
what keeps a Layer-3 finding meaning the same thing Layers 0-2 mean, one level
up. Severity comes from a fixed vocabulary. Full rules, with the reasoning, in
[`finding-contract.md`](finding-contract.md).

A zero-finding class must still list what it **swept**. Otherwise "no drift
found" and "nothing was looked at" are the same record -- and the first is a
result while the second is a failure wearing its clothes.

---

## What this instrument does not cover

Named rather than left for a reader to find:

- **Shape, not truth.** See above. The gate proves citations resolve; a human
  decides whether the contradiction is real.
- **Recall is not measured.** Nothing here proves the reviewers *found*
  everything. `swept` records what was opened, which bounds the claim honestly:
  the review covers the surfaces it names, at the judgment of the model that
  read them.
- **Coverage is measured; reading is not.** `findings_gate` reconciles each
  reviewer's `swept` array against the inventory the pipeline itself wrote, and
  the fraction it computes rides into `report.md` under a gate — so a class
  swept 62-of-114 can no longer publish as a clean sweep. What the gate compares
  is the *array* against the inventory, not the *reading* against the file. It
  is deliberately not a pass/fail bar for that reason: the cheapest way to
  satisfy such a bar would be to paste the inventory into `swept`, which this
  gate cannot tell from a real sweep, and the repair worker a rejection routes
  to is forbidden from reviewing anyway. So the number is published rather than
  enforced. An honest partial sweep is a fine outcome; an unmarked one is not.
- **Untracked files are out of scope** by construction -- `git ls-files`.
- **`vision-observation` issues are a protocol input, not a pipeline input.**
  The graph reads the tree, not the network. Bring the open observation set to
  the human triage step, where section 4 says it is read *as a set*.
- **One pass is one opinion.** The four reviewers are independent of each other,
  not of themselves. A finding a reviewer missed is invisible to this run.

---

## Cross-references

| Topic | Where to look |
|---|---|
| The five drift layers, and what each owns | [`docs/QUALITY_PROTOCOL.md` section 5](../../docs/QUALITY_PROTOCOL.md) |
| When Layer 3 fires | [`docs/QUALITY_PROTOCOL.md` section 6](../../docs/QUALITY_PROTOCOL.md) |
| The observation duty and its resolution paths | [`docs/QUALITY_PROTOCOL.md` section 4](../../docs/QUALITY_PROTOCOL.md) |
| Gates outside workers, the three-question test | [`docs/PIPELINE_DESIGN_PRINCIPLES.md` section 0](../../docs/PIPELINE_DESIGN_PRINCIPLES.md) |
| Gate idioms, the stale-label rule | [`examples/gates/README.md`](../gates/README.md) |
| The same shape one layer up (objective -> lane -> evidence) | [`examples/objective/README.md`](../objective/README.md) |
