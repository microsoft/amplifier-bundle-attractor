# Code Review Checklist — The Five Questions

> **Purpose**: A Socratic checklist for catching the recurring bug classes
> documented in `RECURRING-BUG-CLASSES.md`. Apply during PR review.
> Each species gets 2–3 questions that would have caught the historical
> incident at review time.

## How to use this checklist

- Apply each section to the PR diff. The questions are direction-of-travel
  prompts, not pass/fail gates.
- A "no" or evasive answer to a direction-of-travel question warrants a
  discussion in the PR thread, not silent acceptance.
- The checklist is opinionated and named on purpose — reviewers (human
  or AI) raising a concern should cite the species number (e.g., "S1:
  this looks like incomplete assembly") so the author can map the
  feedback to the underlying class without re-deriving it.
- The full diagnosis and the "true noun vs located noun" distinction
  live in `RECURRING-BUG-CLASSES.md`. Read that doc once; come back to
  this one every PR.

## S1 — Incomplete assembly

- Does this PR add a constructor parameter?
  → If yes, **why is it not a required field on the context / dataclass**?
    A new optional kwarg is the exact shape that has bitten
    `HandlerRegistry` five times.
  → Would have caught #249 / #250 (`subgraph_runner` kwarg).
- Can this object be constructed in a state where a later method fails
  for lack of a dependency? If yes, push the dependency into the
  constructor signature so the type system rejects the half-wired state.

## S2 — Lossy reconstruction

- Does this code build a NEW `Outcome` / result / event payload at a
  boundary? Where did the original's `failure_reason` / identity /
  metadata go? Trace each field of the new object back to its source.
  → Would have caught #251.
- If I trace a failure from origin to surface, is the message still the
  origin's? If the surfaced message is generic ("No matching edge from
  node 'X'"), the boundary is dropping the upstream cause.

## S3 — Unscoped shared state

- Does this read a file / directory / dict keyed only by location
  (`logs_root`, archive path, in-memory pool)? What proves the data at
  that location belongs to **this** run?
  → Would have caught #252.
- Two runs sharing this resource — what leaks? If the answer is "we
  rely on cleanup," that is a verb-invariant. The noun-fix is an
  identity key required to read.

## S4 — Partial-coverage symmetry

- This normalizes / translates / parses input X. What are the sibling
  inputs (keys vs values, host vs DTU, Docker vs Incus, quoted vs
  unquoted)? Are they all covered at the SAME point?
  → Would have caught #253 and the dev-mode `/workspace` translation gap.
- Am I adding a SECOND strip / translate / normalize site? → **Stop.
  Move it upstream to one site.** A second site is the recurrence
  signature, not the fix.

## S5 — Aspirational contract

- Does this call or branch assume a path, mount, or interface exists?
  Show me where it is created. If the creator does not exist in this
  PR or in a documented upstream contract, the branch is aspirational.
  → Would have caught the dot-graph manifest dead if-branch.
- Is there an `if [ -d ... ]` / `if hasattr(...)` / fallback guarding
  something that should always be true? Either the assumption is real
  (and the guard is dead code) or the assumption is false (and the
  "primary" path is the lie). Both warrant a fix.

## Direction-of-travel question (catches future cousins)

- **Does this PR remove a verb** (a "remember to") OR **add a noun** (a
  type, field, or required parameter)?
  → If the PR adds a new "remember to call X at every site"
    obligation, challenge the author: why won't a noun work?
  → This question is what prevents the next band-aid that re-opens a
    hotspot. The hotspot data in `RESOLVE-MIGRATION-GUIDE.md` shows
    `HandlerRegistry` was bitten five times by exactly this pattern
    before the noun-fix landed in Wave 1.

## Tests

- Do the tests assert **invariant-as-data** — construct-time failures,
  type errors, unconstructable invalid state? These survive future
  refactors because they describe the shape of the data.
- Or do they assert **invariant-as-procedure** — "when X happens, Y is
  called"? Procedure assertions cannot catch the procedure being
  skipped at a sibling site (the actual recurrence pattern). If the
  test suite is only the second kind, the bug class is not closed.
