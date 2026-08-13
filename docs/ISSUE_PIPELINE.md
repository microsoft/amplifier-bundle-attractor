# The Issue Pipeline: What Happens After You Label

This repo runs an autonomous issue -> fix pipeline. A maintainer hands it a
well-specified defect report; it hands back a machine-verified definition of done and,
later, a proposed fix -- with a human review gate at every step. This page is what to
expect after filing a [defect report](../.github/ISSUE_TEMPLATE/defect-report.yml).

## The flow

1. **A maintainer applies the `ready:spec` label.** The label is the deliberate human
   trigger -- it is never applied automatically, because each run spends real compute.

2. **The specify stage runs** (`.github/workflows/capsule-specify.yml`; measured runs
   take roughly 20-40 minutes, budgeted up to several hours). It reads the issue,
   investigates the pinned repository, and tries to produce a **work capsule**: a
   definition-of-done document paired with an executable verification gate that is
   proven RED-for-the-right-reason at the pinned base commit and proven non-vacuous
   (crude hypothesis patches are shown to turn it green). The outcome, either way, is
   posted as a comment on the issue:
   - **A capsule PR** opens for human review. It contains *no implementation* --
     merging it approves the definition of done, nothing more.
   - **Or an honest refusal**: the gate is already green at the base commit, or no
     non-vacuous gate could be proven within budget, or the run genuinely did not
     converge -- each posted with its reasons (including a postmortem for
     non-convergence), never silently.

3. **A human reviews and merges the capsule PR.** The review focus, by design: read the
   hypothesis patches -- if a deliberately crude patch would look like an acceptable
   pass, the gate is too weak and the capsule should be tightened, not merged.

4. **Merging the capsule PR auto-fires the implement stage**
   (`.github/workflows/capsule-implement.yml`; typically 30-90+ minutes, budgeted up to
   4 hours). A convergence-loop pipeline makes real code changes, verifies them against
   the capsule's own gate, and subjects green work to independent LLM critique across
   two model families. It ends in one of:
   - **A fix PR**, opened non-draft for human review.
   - **Or an honestly-titled work-in-progress PR** ("did not converge") salvaging the
     committed work, with the judge's objections and the postmortem in the workflow
     run's uploaded artifacts.

## Feature requests

Defects and features are different problems, and they run on different pipelines.

The defect lane's proof rests on a gate being **RED at the base commit for the right
reason** -- informative precisely because it *could* have come out green. That anchor
breaks for a feature ask: when a capability is simply absent, *every* candidate gate is
red at base -- the correct one, the wrong one, and the vacuous one alike -- so red stops
telling you anything. The feature lane (`.github/workflows/feature-specify.yml`, driving
`.github/capsule-pipeline/feature-capsule.dot`) replaces that anchor with something a
machine must not invent: **acceptance criteria a maintainer wrote down**.

**How a maintainer starts a feature run.** Two things, in this order:

1. **Post the acceptance criteria as a comment on the issue** -- not in the issue body;
   see below. The block must look like this:

   ```markdown
   ## Acceptance criteria (feature-capsule)

   Owned-by: @your-github-login
   Scope: IN -- what this feature covers. OUT -- what it does not, and where that is tracked instead.

   AC-1: <one testable criterion, stated as an observable behavior through a public surface>
   AC-2: <another>
   AC-3 [guard]: <a criterion that ALREADY holds at the base commit and must keep holding>
   ```

   - `Owned-by:` must be **your own** login. Adoption is an explicit act, so pasting
     someone else's proposed criteria does not bind until you own them.
   - `Scope:` is required. "Deferred" needs a named home, and silence about what is OUT
     is itself an unmade decision.
   - Each `AC-<n>` becomes exactly one row of a machine-checked census, so IDs must be
     unique and each criterion must be independently testable. `[guard]` marks a
     criterion that must already hold today -- a regression guard.
   - A criteria block inside a quote (`>`) or a fenced code block is deliberately
     **ignored**: quoting someone else's proposal is not a ruling.

2. **Apply the `ready:feature-spec` label.** Same deliberate, maintainer-only cost gate
   as `ready:spec`, and -- as with the defect lane -- the label is the only trigger.

**Why a comment, and not the issue body.** Anyone can write anything in an issue body,
including a section claiming to be a maintainer ruling, and the body can be edited after
the fact. GitHub reports each *comment's* author role (OWNER / MEMBER / COLLABORATOR)
server-side, computed from that account's real relationship to this repository, and a
filer cannot forge it. So the comment channel is the only one here that can carry
authority. The issue body is still read, as background; it just cannot bind.

**What you get back**, posted as a comment on the issue either way:

- **A capsule PR**, the same shape as the defect lane's, with the maintainer's criteria
  shipped alongside the capsule and pinned by digest. Merging it approves the definition
  of done and fires the same implement stage; it contains no implementation.
- **A refusal, before any real compute is spent**, if no usable criteria block was found
  -- posted together with the criteria template, so the next action is copy-edit-post
  rather than archaeology. The pipeline also refuses (rather than guessing) when two
  different maintainers have posted competing criteria: settling that by
  last-poster-wins would be the pipeline quietly rewriting your spec.
- **Blocking questions for the maintainer**, if the run got as far as a working draft
  gate and then hit a genuine fork in the criteria. Each question is written to be
  closable in about a sentence. Answer it, post corrected criteria, re-label.

**If you filed the request and are not a maintainer:** the refusal and
blocked-on-criteria outcomes are not judgments of your request -- they mean a maintainer
still has to say what "done" means. Both templates say so explicitly.

## The human gates are features

- Labeling is deliberate and maintainer-only -- the cost gate.
- Capsule PRs and fix PRs are reviewed by a human. **The pipeline never merges its own
  work** -- every PR it opens says so explicitly.

## Honest expectations

Issue quality determines convergence -- this is measured, not a guess. Well-specified
defect reports (observable behavior, exact repro with real output quoted, expected vs.
actual, pinned SHA, no fix prescriptions) converge. Vague reports and design questions produce
honest non-convergence: a polite refusal with reasons, not a fix. Feature requests are
not a defect-lane input at all -- they have their own lane and their own entry
requirement (see [Feature requests](#feature-requests)); handing one to `ready:spec`
still produces a refusal. Each run costs real compute, which is exactly why the label
gate exists.

## What makes a good report

Use the [defect report form](../.github/ISSUE_TEMPLATE/defect-report.yml) -- it walks
you through it. The measured essentials:

- **Describe what the software DOES, not what the fix should be.** The pipeline
  independently explores repair surfaces and verifies against behavior; a prescribed
  fix biases and narrows its verification gate.
- **Exact repro commands with the actual output quoted** -- the gate is built from
  observable behavior, so a runnable reproduction is the strongest input.
- **Expected vs. actual, stated plainly**, and the commit SHA you observed it on.
- **Self-contained plain English** -- the pipeline reads the issue text, not your
  browser tabs.

## Provenance

This system is measured, not aspirational: the pipeline's fixes for
[#146](https://github.com/microsoft/amplifier-bundle-attractor/issues/146) (capsule PR
#171 -> fix PR #175) and
[#172](https://github.com/microsoft/amplifier-bundle-attractor/issues/172) (capsule PR
#174 -> fix PR #182) shipped to `main` after human review.
