# Capsule pipeline — the "specify" stage

This directory wires the **specify** stage of an issue -> attractor -> PR system:
given a GitHub issue labeled `ready:spec`, an attractor pipeline reads the
issue, investigates the pinned repository, and produces a **work capsule** —
a definition-of-done markdown (`DEFINITION.md`) paired with an executable gate
script (`DEFINITION.verify.sh`) that is provably red-for-the-right-reason at
the issue's base commit and provably non-vacuous (a crude hypothesis patch
turns it green, then the tree is hard-reset and the reset is proven — twice,
via two independently-shaped hacks). The pipeline never implements a fix and
never merges anything; it opens a **capsule PR** containing the proposed
definition of done for a human to review. See
`.github/workflows/capsule-specify.yml` for the workflow that drives it.

## Files

| Path | What it is |
|---|---|
| `capsule.dot` | The attractor pipeline itself (`digraph CapsulePipeline`). |
| `vendor/backlog/check-upstream-leaks.sh` | Publication-safety gate: scans the produced `DEFINITION.md` for internal working-vocabulary leaks before it ships in a PR. |
| `vendor/backlog/fixtures/leak-scan/*` | Self-test fixtures for the scanner above (RED/GREEN controls). |

## Provenance — these are VENDORED copies

`capsule.dot` and `vendor/backlog/check-upstream-leaks.sh` (+ its fixtures)
were authored and evaluated in a private working repository that is **not
publicly reachable** — GitHub Actions runners cannot clone it, so the
pipeline cannot reference it live the way it does when run by hand from that
repository. Both files are copied here **verbatim below their provenance
header** (see the header comment at the top of each file for the exact
source commit). Do not hand-edit the body of either file — if a fix or
improvement is needed, make it in the source repository, re-copy the file
here, and re-apply the provenance header comment.

`capsule.dot` itself is **not modified** beyond the header: it references its
leak-scanning dependency via a `uplift_dir` **parameter**, not a hardcoded
path, so vendoring the scanner under `vendor/backlog/check-upstream-leaks.sh`
and pointing `--param uplift_dir=.github/capsule-pipeline/vendor` at it
satisfies the graph's existing `$uplift_dir/backlog/check-upstream-leaks.sh`
reference with zero changes to the pipeline's logic.

## Why the leak-scan gate still applies here

`check-upstream-leaks.sh` exists to keep a private project's internal
working vocabulary (task IDs, internal mechanism names, private-repo
terminology) out of text that ships publicly. Its home repository is
private; this repository (`amplifier-bundle-attractor`) is the **public**
target such text ships *into* — that is the boundary the scanner was built
to guard, and running the pipeline directly inside the public repo does not
remove the concern: `DEFINITION.md` is a work-order artifact that lands in a
PR any contributor to this repo may read, and it must be written in this
repo's own vocabulary (referencing this repo's docs, conventions, and
exemplars), not in unexplained jargon from an unrelated private project.
Nothing about the gate's DENY list needed to change for that reason; it is
vendored as-is.

**Known, harmless deviation**: `vendor/backlog/check-upstream-leaks.sh
--self-test` reports 2 of its 6 self-checks failing here (the GREEN controls
that assert specific historical document text from the *source* repository
scans clean, and that a specific historical task-ID census matches). Those
two checks are about the source repository's own document set, not about
this repository, and the same two checks already fail identically when run
from the source repo's own checkout — this is a pre-existing property of the
fixtures, not something vendoring introduced, and it does not affect the
scanner's actual runtime behavior (scanning an arbitrary file against the
DENY list), which is exercised directly by `leak_gate` in `capsule.dot` and
was independently re-verified working (the 4 RED fixtures all still trip the
scanner correctly) before this was wired up.

## Re-syncing

1. In the source repository, confirm the current commit of `runner/capsule.dot`
   and `backlog/check-upstream-leaks.sh` (+ `backlog/fixtures/leak-scan/`).
2. Copy the files here verbatim (`cp`, not a manual retype).
3. Re-apply (or update) the provenance header comment at the top of each file
   with the new source commit.
4. Run `attractor lint .github/capsule-pipeline/capsule.dot` and
   `vendor/backlog/check-upstream-leaks.sh --self-test` and note any new
   deviations here.
