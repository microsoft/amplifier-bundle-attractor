# Capsule pipeline — the "specify" and "implement" stages

This directory wires two stages of an issue -> attractor -> PR system:

- **specify**: given a GitHub issue labeled `ready:spec`, an attractor
  pipeline reads the issue, investigates the pinned repository, and produces
  a **work capsule** — a definition-of-done markdown (`DEFINITION.md`)
  paired with an executable gate script (`DEFINITION.verify.sh`) that is
  provably red-for-the-right-reason at the issue's base commit and provably
  non-vacuous (a crude hypothesis patch turns it green, then the tree is
  hard-reset and the reset is proven — twice, via two independently-shaped
  hacks). The pipeline never implements a fix and never merges anything; it
  opens a **capsule PR** containing the proposed definition of done for a
  human to review. See `.github/workflows/capsule-specify.yml`.
- **implement**: given a merged capsule PR (or a manual dispatch naming a
  capsule path), a hardened convergence-loop attractor (`task-runner.dot`)
  reads the capsule's definition of done and its gate script, makes real
  code changes, verifies them against the gate, subjects green work to
  LLM critique, and — only once both a deterministic gate and a critique
  quorum agree — packages the fix on a branch and opens a **fix PR**. See
  `.github/workflows/capsule-implement.yml`.

## Files

| Path | What it is |
|---|---|
| `capsule.dot` | The specify-stage attractor pipeline (`digraph CapsulePipeline`). |
| `task-runner.dot` | The implement-stage attractor pipeline (`digraph BacklogTaskRunner`) — a convergence loop (attempt → verify → dual critics → verdict → feedback → loop). |
| `attractor-pipeline-dual.yaml` | Multi-provider (Anthropic + OpenAI) base bundle for `task-runner.dot`'s dual-family critique. **Vendored but not currently wired in** — see its own provenance header and `capsule-implement.yml` for why. |
| `vendor/backlog/check-upstream-leaks.sh` | Publication-safety gate: scans the produced `DEFINITION.md` for internal working-vocabulary leaks before it ships in a PR. |
| `vendor/backlog/fixtures/leak-scan/*` | Self-test fixtures for the scanner above (RED/GREEN controls). |
| `vendor/runner/check-degenerate-hack.py` | `degenerate_gate`'s checker: suspects a hypothesis patch (A **and** B, checked per-patch since the A/B symmetry fix) that greens the gate by deleting/stubbing behavior rather than implementing a real alternate fix (THE INVERSION RULE). |
| `vendor/runner/check-existing-tests.py` | `existing_test_gate`'s checker: derives a capsule's subject from the symbols/paths its own `DEFINITION.verify.sh` names, and blocks if the repo already ships an on-topic test the gate doesn't run (FOLD IN THE EXISTING TESTS). **Also a load-bearing import for `check-witness-gate.py`** (see below) — it is no longer only `existing_test_gate`'s dependency. |
| `vendor/runner/check-witness-gate.py` | Checker shared by **two gates**, `witness_gate` and `void_gate`: catches "vacuous by no-occasion" — a proven-greening (or, for `void_gate`, actively-constructed) hypothesis patch that dodges the reported defect by deleting the *occasion* to observe it (e.g. deleting a single `goal_gate=true` token) rather than fixing it. Imports `resolve_subject_symbols`/`walk_repo`/`STOPWORDS` from `check-existing-tests.py` and diff-hunk parsing from `check-degenerate-hack.py` **at runtime via `importlib`**, resolved relative to its own file location (`Path(__file__).resolve().parent`) — both sibling files must be vendored in the *same directory*, not merely somewhere under `uplift_dir`. Content is unchanged as of the void-probe sync (2026-08-07, this pass), but it gained a **second, independent graph-level caller** (`void_gate`'s own `tool_command=` shells out to it directly, exactly like `witness_gate`'s does) — see the Re-sync log entry for that sync for why an unchanged file can still need re-proving. |

## Provenance — these are VENDORED copies

`capsule.dot`, `task-runner.dot`, `attractor-pipeline-dual.yaml`, and
`vendor/backlog/check-upstream-leaks.sh` (+ its fixtures) were authored and
evaluated in a private working repository that is **not publicly
reachable** — GitHub Actions runners cannot clone it, so the pipelines
cannot reference it live the way they do when run by hand from that
repository. Every file is copied here **verbatim below its provenance
header** (see the header comment at the top of each file for the exact
source commit). Do not hand-edit the body of any of these files — if a fix
or improvement is needed, make it in the source repository, re-copy the
file here, and re-apply the provenance header comment.

`capsule.dot` itself is **not modified** beyond the header: it references all
of its checker dependencies via the same `uplift_dir` **parameter**,
never a hardcoded path, so vendoring each checker at the matching relative
location under `vendor/` and pointing
`--param uplift_dir=.github/capsule-pipeline/vendor` at it satisfies every
one of the graph's existing references with zero changes to the pipeline's
logic:

- `$uplift_dir/backlog/check-upstream-leaks.sh` (`leak_gate`) -> `vendor/backlog/check-upstream-leaks.sh`
- `$uplift_dir/runner/check-degenerate-hack.py` (`degenerate_gate`) -> `vendor/runner/check-degenerate-hack.py`
- `$uplift_dir/runner/check-existing-tests.py` (`existing_test_gate`) -> `vendor/runner/check-existing-tests.py`
- `$uplift_dir/runner/check-witness-gate.py` (`witness_gate`) -> `vendor/runner/check-witness-gate.py`

The `vendor/` subtree therefore mirrors the source repository's own directory
layout (`backlog/`, `runner/`) one level down -- this is deliberate, not
incidental: any future checker `capsule.dot` grows will resolve correctly
under `uplift_dir` for free as long as it is vendored at the same relative
path it lives at in the source, with no path-mapping logic to maintain here.

**A fourth reference exists that is *not* a `$uplift_dir/...` shell-out and
will not be found by grepping `capsule.dot` for `uplift_dir` at all**:
`check-witness-gate.py` loads `check-existing-tests.py` (for
`resolve_subject_symbols`/`walk_repo`/`STOPWORDS`) and `check-degenerate-hack.py`
(for its diff-hunk parser) directly via `importlib`, resolved relative to
**its own file's location** (`Path(__file__).resolve().parent`) -- not via
`uplift_dir`, not via a `sys.path` entry, and not via the graph at all. This
means both sibling files must be vendored in the exact same directory as
`check-witness-gate.py` (`vendor/runner/`), or the import silently resolves
to nothing and `witness_gate` crashes at runtime with a `FileNotFoundError`
that no static check on `capsule.dot` itself will ever surface. See
"Re-syncing" step 2 below -- this is precisely the class of miss that caused
this file's own previous re-sync to ship gates whose checker scripts weren't
vendored.

**A distinct-but-related trap, closed in the void-probe sync (2026-08-07, this
pass):** a *new node* can add a **second, independent, directly-visible**
`$uplift_dir/...` shell-out to a file that is **already vendored and whose
content does not change** -- `void_gate` shells out to
`$uplift_dir/runner/check-witness-gate.py` exactly like `witness_gate`
already did, with a different (single-`--patch`) argument shape and from a
different point in the pipeline (after `void_gate`'s own hard-reset, not
after `mutate_gate`/`mutate_gate_b`'s). Unlike the importlib case above,
this one *is* grep-visible via step 2's `uplift_dir` search -- but "the file
is already vendored and its self-test still passes" is not the same claim as
"this new call site's own invocation, with its own arguments and its own
place in the control flow, actually resolves and behaves correctly." See
step 5's amended wording below (the "prove **each** call site" sentence) --
this is the reason it was added.

`task-runner.dot` is likewise **not modified** beyond its header. It was
authored for a slightly different invocation shape (a backlog task file with
its own `verify.sh` naming convention, drawn from a private task backlog
rather than a specify-stage capsule) — see the **INVOCATION
RECONCILIATION** note in its own header for exactly what was checked, what
was compatible with zero changes, and the one genuine gap (dual-family
critique needs a second model-provider key that does not exist as a proven
secret in this repo yet) that was closed at the **workflow** level
(`capsule-implement.yml` conditionally sets `ATTRACTOR_PIPELINE_BUNDLE` only
when `secrets.OPENAI_API_KEY` is present) rather than by editing the engine.

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
   and diff it against the vendored copy's pinned commit (its header names the
   commit) to see exactly what's missing -- don't assume; quote the diff.
   Do the same for `runner/task-runner.dot` against its own separately-pinned
   commit (the two files are not necessarily in sync with each other).
2. Check whether the diff added, removed, or renamed any `$uplift_dir/...`
   references (grep the new source for `uplift_dir`) -- every checker a
   `tool_command=` node shells out to must exist under `vendor/` at the exact
   relative path the graph resolves, or the corresponding gate breaks at
   runtime instead of at review time. As of the 2026-08-07 re-sync that means:
   `backlog/check-upstream-leaks.sh`, `runner/check-degenerate-hack.py`,
   `runner/check-existing-tests.py`, and `runner/check-witness-gate.py`.
   **This grep is NOT sufficient by itself** -- see step 2a. (This gap is
   exactly what let the previous re-sync ship `degenerate_gate` and
   `existing_test_gate` referencing checkers that were never vendored: the
   step existed, but grepping `capsule.dot` alone doesn't surface every
   load-bearing dependency a *newly-added* checker script itself introduces.)
2a. **Grep every newly-added or newly-vendored Python checker for its OWN
   sibling-file dependencies** -- `importlib`-based loading of another script
   in the same directory (e.g. `check-witness-gate.py`'s `_load(...,
   "check-existing-tests.py")`), `sys.path` manipulation, or a plain
   `import` of a local module. These dependencies are invisible to a grep of
   `capsule.dot` for `uplift_dir` (the referencing script isn't shelled out
   from the graph with a path -- it's imported from *within* another
   checker), and an already-vendored, content-unchanged file can become
   load-bearing for a brand-new reason: `check-existing-tests.py` didn't
   change in this sync, but it silently became a runtime dependency of
   `check-witness-gate.py` too. Run `grep -n "^import\|^from\|_load(\|importlib"
   vendor/runner/*.py` and confirm every named sibling file is present in the
   same `vendor/` subdirectory.
2b. **Count the graph-level call sites of every checker named by step 2's
   grep, not just whether the checker is present** -- an already-vendored,
   content-unchanged file can gain a **second (or third) independent
   `tool_command=` caller** elsewhere in the same diff (e.g. the void-probe
   sync: `void_gate` shells out to `$uplift_dir/runner/check-witness-gate.py`
   with its own argument shape, a single `--patch`, from a different point in
   the control flow than `witness_gate`'s multi-`--patch` call). Step 2's
   grep already surfaces that a second reference exists (it is not the
   invisible import case step 2a covers) -- the trap here is stopping at
   "the file is already vendored, and it already has a passing self-test"
   without re-proving *that specific new call site's own invocation*. A
   checker resolving in isolation does not prove every caller's arguments,
   working assumptions, and place in the control flow also resolve. Note
   every distinct node name that shells out to the same checker path.
3. Copy the files here verbatim (`cp`, not a manual retype), preserving the
   executable bit on any `.sh`/`.py` checker.
4. Re-apply (or update) the provenance header comment at the top of each file
   with the new source commit and date.
5. Run `attractor lint .github/capsule-pipeline/capsule.dot` and
   `vendor/backlog/check-upstream-leaks.sh --self-test`, and **prove the
   vendored checkers actually resolve** at the path the workflow sets
   `uplift_dir` to (`$GITHUB_WORKSPACE/.github/capsule-pipeline/vendor`) --
   a `workflow_dispatch` run or a local invocation with `uplift_dir` set the
   same way, not just a file-existence check. **This must include proving any
   cross-checker `importlib` import found in step 2a actually resolves** --
   invoke the importing checker (not just `python3 -c "import ..."` in
   isolation) from a scratch repo with `uplift_dir` pointed at the vendored
   location, and run a negative control (wrong `uplift_dir`, or one sibling
   file deliberately absent) so a pass isn't accidental. **It must also
   separately exercise EACH graph-level call site named by step 2b** --
   extract that node's own `tool_command=` text verbatim from the re-synced
   `.dot` and run it (or the checker invocation inside it) with its own exact
   arguments, not only the checker's generic `--self-test` -- a self-test
   proves the file loads; it does not prove a *different* caller's specific
   arguments and calling context also work. Note any new deviations here.

## Re-sync log

- **2026-08-07** -- `capsule.dot` re-synced `67a53e531a1` (content-identical
  to `73e75e7`, the commit actually last touching `runner/capsule.dot`) ->
  `d93d1e60066abdc61f082e48619569eef0816a09`. Picked up two gates that did
  not exist in the vendored copy before this sync, and were therefore never
  exercised by any specify-stage run this repository's Actions had produced
  up to this point:
  - `degenerate_gate` (source commit `0afb874`, THE INVERSION RULE) --
    routes a hypothesis-B patch that greens the gate by deleting/stubbing
    behavior to `triage` instead of treating it as proof the gate is
    behavior-bound.
  - `existing_test_gate` (source commit `d93d1e6`, FOLD IN THE EXISTING
    TESTS) -- blocks a capsule whose gate declares a subject the target
    repo already ships an on-topic test for, and doesn't run it.
  Vendored their checkers (`vendor/runner/check-degenerate-hack.py`,
  `vendor/runner/check-existing-tests.py`) for the first time -- this
  `vendor/runner/` subdirectory did not exist before this sync.
  `task-runner.dot` was checked against the same source range and found
  **byte-identical** at its own pinned commit
  (`f5322c24ed6fd8deaeddb519de7bfdfa861094d9`) -- no re-sync needed; see the
  PR that performed this sync for the full proof.

- **2026-08-07 (later same day)** -- `capsule.dot` re-synced
  `d93d1e60066abdc61f082e48619569eef0816a09` ->
  `3a53cf13cbf426bcdea7e4382fae9feb9efe977d`. Picked up two more additions:
  - `witness_gate` (new gate, catches "vacuous by no-occasion" -- a
    hypothesis patch that greens the gate by *deleting the observed case*
    rather than fixing the reported cause; motivating live case: GitHub
    issue #146, where deleting the single token `goal_gate=true` from a
    `.dot` example greens the gate while the reported lint blind spot
    remains untouched). Vendored its checker,
    `vendor/runner/check-witness-gate.py`, for the first time.
  - `degenerate_gate` A/B symmetry fix -- it previously inspected only
    `.ai/hypothesis_b.patch`; it now runs `check-degenerate-hack.py` over
    **both** hypothesis slots (`hypothesis.patch` and `hypothesis_b.patch`)
    and reports which fired. No new file to vendor for this half (it reuses
    the already-vendored `check-degenerate-hack.py` unchanged), but the
    `.dot` body calling it changed (now invoked twice per round).
  This sync exercised the exact trap this README's checklist step 2a now
  names explicitly: `check-witness-gate.py` imports
  `resolve_subject_symbols`/`walk_repo`/`STOPWORDS` from
  `check-existing-tests.py` **and** diff-hunk parsing from
  `check-degenerate-hack.py`, both via `importlib`, resolved relative to its
  own file's directory -- not via `uplift_dir`, and invisible to a grep of
  `capsule.dot` for `uplift_dir` references (that grep only finds
  `check-witness-gate.py` itself as a new `$uplift_dir/...` shell-out; it
  would not have flagged that the *already-vendored, content-unchanged*
  `check-existing-tests.py` had become load-bearing for a second, different
  consumer). `check-existing-tests.py` and `check-degenerate-hack.py` were
  diffed against this same source range and found byte-identical to the
  copies already vendored from the prior sync -- no re-copy was needed for
  either, only the new `check-witness-gate.py`. Runtime resolution of all
  three checkers, including the cross-file `importlib` import, was proven
  from a scratch repo with `uplift_dir` set exactly as
  `capsule-specify.yml` sets it, plus a negative control (wrong
  `uplift_dir`) -- see the PR that performed this sync for the full
  transcripts. `task-runner.dot` was checked against the same source range
  and found **byte-identical** at its own separately-pinned commit
  (`f5322c24ed6fd8deaeddb519de7bfdfa861094d9`, unchanged from the previous
  sync) -- no re-sync needed.
  The prior version of this README's checklist (step 2 alone: grep
  `capsule.dot` for `uplift_dir`) would **not** have caught this sync's
  cross-file import trap on its own -- it names only references the graph
  itself makes. Step 2a above was added in this pass specifically to close
  that gap: newly-vendored Python checkers must themselves be grepped for
  sibling-file loading, and any already-vendored file they reference must be
  re-confirmed present, even when that file's own content didn't change.
  `task-runner.dot` was checked against the same source range and found
  **byte-identical** at its own pinned commit (`f5322c24ed6fd8deaeddb519de7bfdfa861094d9`)
  -- no re-sync needed; see the PR that performed this sync for the full
  proof.

- **2026-08-07 (later still)** -- `capsule.dot` re-synced
  `3a53cf13cbf426bcdea7e4382fae9feb9efe977d` ->
  `34fff9618e9c0cba3feee8ac8b82c94798720cec`. Picked up one addition, THE
  VOID PROBE (motivating live case: GitHub issue #146's own capsule PR #161,
  `goal-gate-loop-restart-lint.verify.sh`, base `44428ab67a530f23aa5579104f4ff68e4e809c37`
  -- both its hypothesis patches are legitimate, so `witness_gate` correctly
  reported `witness_clean` on its own patches; the capsule was *still*
  dodgeable by hand, because `witness_gate` is passive -- it only inspects
  patches written for a different purpose, so a dodge nobody happened to
  write into slot A or B goes undetected):
  - `void` (new node, `class="maker"`) -- writes `.ai/hypothesis_v.patch`
    under an INVERTED objective from `mutate`/`mutate_b`: actively search
    for the cheapest patch that greens the gate *without* fixing the
    reported defect, or write a `# NO-VOID:` explanation if none is
    honestly constructible.
  - `void_gate` (new node) -- applies that patch, proves GREEN, hard-resets,
    proves the reset, then **re-invokes `check-witness-gate.py` a SECOND
    time** (same four-condition analysis, reused verbatim) against only
    `.ai/hypothesis_v.patch` as the guard: a greening patch is only treated
    as a CONFIRMED dodge if that analysis also says so; otherwise it is
    PASS-with-a-finding (`.ai/findings/void-inconclusive.md`), biased to
    PASS like `witness_gate`/`existing_test_gate`.
  - `witness_gate`'s own success edges were rewired to feed `void` (not
    `discrimination_check`) first; `void_gate`'s three success tokens (no
    dodge possible, dodge resisted, inconclusive) all proceed to
    `discrimination_check`; an unproven reset is a loud halt (shared with
    `mutate_gate`/`mutate_gate_b`); a confirmed dodge or an infra failure
    routes to `triage` through the same root-cause wall every other gate
    defect in this file uses.
  **No new file to vendor**: `void_gate` reuses `check-witness-gate.py`
  verbatim, exactly as `witness_gate` already does -- but this is precisely
  the "recurring trap" this checklist exists to name: `check-witness-gate.py`
  is unchanged (byte-identical diff, confirmed against the source range) yet
  became newly load-bearing for a **second, independent graph-level caller**
  with its own argument shape (`void_gate` passes a single `--patch`; unlike
  `witness_gate` it also calls the checker from AFTER its own git-apply /
  hard-reset cycle, not before one). `check-existing-tests.py` and
  `check-degenerate-hack.py` were diffed against this same source range and
  found byte-identical to the copies already vendored -- no re-copy needed
  for either. `task-runner.dot` was checked against the same source range
  and found **byte-identical** at its own separately-pinned commit
  (`f5322c24ed6fd8deaeddb519de7bfdfa861094d9`, unchanged since the first
  sync) -- no re-sync needed.

  Runtime proof performed (see the PR that performed this sync for full
  transcripts): (1) `check-witness-gate.py --self-test` and a direct
  invocation with `void_gate`'s own single-`--patch` argument shape, from a
  scratch repo with `uplift_dir` set to this repo's own
  `.github/capsule-pipeline/vendor` (the same path `capsule-specify.yml`
  computes); (2) a negative control vendoring `check-witness-gate.py` ALONE
  (its two sibling files absent) reproduced the exact
  `FileNotFoundError: check-existing-tests.py` the import trap predicts,
  proving the positive result is not accidental; (3) a second discriminating
  negative control -- the SAME checker invocation given a legitimate patch
  (issue #146's own `hypothesis.patch`) instead of a dodge -- returned
  `VERDICT: witness_clean` (rc=0), proving the mechanism discriminates
  rather than always firing; (4) THE LIVE CASE: `void_gate`'s own
  `tool_command=` text was extracted verbatim from the re-synced `.dot` and
  executed against a `git worktree` of this repo pinned at base
  `44428ab67a530f23aa5579104f4ff68e4e809c37`, seeded with issue #146's real
  `DEFINITION.md`/`DEFINITION.verify.sh` and a reconstruction of PR #161's
  actual dodge (`sed -i '/^ *goal_gate=true *$/d' examples/pipelines/00-convergence-loop.dot`,
  `git diff --numstat` confirming `0	1` as the historical record states) as
  `.ai/hypothesis_v.patch` -- the full node body ran end-to-end (git apply,
  the real gate script via a synced `modules/loop-pipeline` venv, hard
  reset, reset proof, and the guard) through the **vendored** checker path
  and produced exit code 1, `.ai/gate.log` containing
  `VOID-DODGE CONFIRMED: ...`, and `.ai/convergence.jsonl` recording
  `{"gate": "void", "greened": true, "reset_proven": true}` followed by
  `{"gate": "void", "verdict": "dodge_confirmed"}` -- the exact shape
  `void_gate -> triage [condition="outcome=fail", ...]` routes on.

  **Checklist hardening in this pass**: neither step 2 nor step 2a, as
  worded before this sync, made explicit that an already-vendored,
  content-unchanged checker can gain a **second independent graph-level
  call site** whose own argument shape and calling context still need
  runtime proof -- step 2's grep *does* surface the new reference here (it
  is not the invisible-`importlib` case step 2a covers), but "the file is
  present and its self-test passes" is not the same claim as "this specific
  new caller's own invocation resolves." Step 2b was added to name this
  explicitly (count call sites, not just presence), and step 5 was amended
  to require exercising each named call site's own extracted
  `tool_command=` text, not only the checker's generic `--self-test`.
