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

## The human gates are features

- Labeling is deliberate and maintainer-only -- the cost gate.
- Capsule PRs and fix PRs are reviewed by a human. **The pipeline never merges its own
  work** -- every PR it opens says so explicitly.

## Honest expectations

Issue quality determines convergence -- this is measured, not a guess. Well-specified
defect reports (observable behavior, exact repro with real output quoted, expected vs.
actual, pinned SHA, no fix prescriptions) converge. Vague reports, design questions,
and feature requests produce honest non-convergence: a polite refusal with reasons,
not a fix. Each run costs real compute, which is exactly why the label gate exists.

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
