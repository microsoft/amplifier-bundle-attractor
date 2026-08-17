# Capsule pipeline — the "specify" and "implement" stages

This directory wires two stages of an issue -> attractor -> PR system, with the
specify stage existing in two sibling flavors (defect and feature):

- **specify**: given a GitHub issue labeled `ready:spec`, an attractor
  pipeline reads the issue, investigates the pinned repository, and produces
  a **work capsule** — a definition-of-done markdown (`DEFINITION.md`)
  paired with an executable gate script (`DEFINITION.verify.sh`) that is
  provably red-for-the-right-reason at the issue's base commit and provably
  non-vacuous (up to three crude patches — two independently-shaped
  hypothesis hacks plus an adversarial void probe — are each applied, proven
  to green the gate, then the tree is hard-reset and the reset itself is
  proven, with a base control-run guarding against a broken harness), then
  judged behavior-bound by ONE LLM critic on a second model family
  (`critique`, `llm_provider="openai"`) whose anchored verdict a
  deterministic `verdict` gate classifies (SHIP / ITERATE / noverdict).
  The pipeline never implements a fix and never merges anything; it
  opens a **capsule PR** containing the proposed definition of done for a
  human to review. See `.github/workflows/capsule-specify.yml`.
- **specify (feature)**: the same stage for a FEATURE request, driven by the
  `ready:feature-spec` label and `feature-capsule.dot`. Everything above about
  RED-at-base stops holding when the capability is simply absent (every
  candidate gate is red at base -- correct, wrong, and vacuous alike), so the
  anchor becomes **maintainer-authored acceptance criteria** delivered over the
  authenticated issue-comment channel and pinned by digest before any budget is
  spent. The retained red check is demoted to a harness proof (the gate must
  articulate each absence as a per-criterion census row); non-vacuity inverts to
  the feature sense (a capability STUB must not green the gate). The output is
  the same shape of capsule PR, landing in the same `proposals/issue-<n>/`
  layout, so merging it fires the same implement stage. See
  `.github/workflows/feature-specify.yml`.
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
| `feature-capsule.dot` | The FEATURE specify-stage attractor pipeline (`digraph FeatureCapsulePipeline`) -- the sibling of `capsule.dot` for feature requests rather than defects. The defect pipeline's epistemic anchor (RED-at-base, informative *because* it could have been green) breaks structurally on a feature ask: an absent capability makes EVERY candidate gate red at base, so red stops discriminating. This graph replaces the anchor with BINDING maintainer-authored acceptance criteria arriving over the authenticated issue-comment channel (`author_association` in {OWNER, MEMBER, COLLABORATOR}, Bot-excluded -- server-side facts a filer cannot forge), ingested and digest-pinned by its `criteria_gate` before any budget is spent; the retained RED check is demoted to a HARNESS proof (the gate must ARTICULATE each absence into a per-criterion census row, not merely fail). Driven by `.github/workflows/feature-specify.yml` on the `ready:feature-spec` label. Its **in-run HERMETICITY probe** (a rider inside `nonvacuity_gate`) re-runs a greened gate from a pristine RELOCATED worktree at the pinned base to prove the gate's verdict tracks the tree it is invoked in — and, as of the 2026-08-13 re-sync, stages the capsule's plain-file gate FIXTURES (`.ai/capsule/oracle.py` and friends) into that worktree beside the pair, bounded exactly as its own `package` step is (regular files only, no directories, no recursion, pair skipped). That is the **in-run twin** of `verify_shipped_gate.sh`'s re-staging two rows below: run 31706411735 vendored its oracle correctly as a plain sibling and the probe, staging only the pair, still drew `GATE INFRASTRUCTURE FAILURE: oracle.py not found` and recorded `hermetic=diverged` on a gate that was in fact hermetic. Same class, two doors — keep them in step. As of the 2026-08-13 ADJUDICATION re-sync its no-honest-green branch no longer feeds the corrective/`cant_gate` lane when the adversarial STUB leg greened: that round exits SUCCESS on a `stub_adjudicate` token straight to the JUDGE (`critique`), whose duty is to classify the stub PATCH **from the artifact** (read the diff at `.ai/hypothesis_v.patch`) and never from its author's self-report — `VERDICT: FEATURE-EQUIVALENT-STUB` records the adjudicated finding and returns ONE re-verified honest round to the maker lane (`verdict -> mutate`, one grant per gate sha256), while a SABOTAGE-CLASS ruling keeps the existing corrective. Run 31738006101 is the measured case: that run's round-8 "stub" was a complete implementation (its author's own report still described the previous round's parasitic dodge) and both honest makers had re-emitted patches "verified" against round 7's logs, so the run escalated with 2 of 8 iterations unspent behind a finding whose canned header its own ledger row (`stub_greened=true`) contradicted. `budget_decision` now emits `cant_gate` only when that row shows NO leg of any kind greened, the finding's text is derived from the row, and the three makers carry the always-rewrite rule's missing half (never re-emit on a stale census — re-measure against the CURRENT gate). As of the 2026-08-13 **RC-11** re-sync it also guards the ENGINE's own environment: run 31760312250 proved non-vacuity, routed to the judge, and the judge node died at SPAWN in 11.97ms with `No module named 'unified_llm._cost'` (`session_id: null`, empty `response_tail`) because a maker had copied its patched build straight into a checked-out `.venv`'s site-packages — a place the reset cannot reach (`git clean -qfd -e .ai` carries no `-x`, and the interpreter-cache purge `-prune`s `.venv`, as that run's own `rival_reset` output states). `critique` is the one node pinning a glob model pattern, so it is the one node whose spawn makes the engine resolve a model catalog — importing the very library that run's subject ships. The single `critique -> escalate [outcome=fail]` edge read that as “critic unreachable” and auto-approve answered [A]bandon, killing a proven round one judge session short of a verdict. Three closures: the four maker prompts carry an explicit **ambient-install prohibition** beside their tree-hygiene rules (with the sanctioned `sys.path.insert(0, '<repo-relative dir>')` remedy); the three reset sites (`rival_reset`, `author_reset`, the `nonvacuity_gate` ENTRY reset) now **prove the ambient environment clean as well as the tree** — scanning every site-packages the interpreter reports plus every in-tree one the cache purge prunes for `.pth` / `__editable__` / `dist-info` `direct_url.json` entries pointing INTO the checkout, removing what they find, RE-PROBING, recording `"ambient": "clean"|"removed"|"unremovable"` in the ledger, and failing LOUD through the existing `reset_unproven -> reset_fail` route when a hit will not go (subject-agnostic by construction: it probes “anything pointing into `$target_dir`”, never a named package); and a new glue node `critique_fail_class` **splits the judge's fail edge** so a SPAWN-class failure (no `critique.md` this round AND too fast for any session to have run) reaches the RECOVER WALL under the budget discipline, while a critique that RAN and then failed its must_write/verdict discipline still reaches `escalate` — the unruled-fact protection is preserved, and every ambiguous case, including a missing `.ai/judge-entry` stamp, fails safe to the human gate. **The one vendored file that is NOT byte-identical below its header** -- three comment lines in the source's own header were rewritten for upstream-legality; the GRAPH BODY is byte-identical and its sha256 is pinned in the provenance box. See that box before re-syncing. |
| `task-runner.dot` | The implement-stage attractor pipeline (`digraph BacklogTaskRunner`) — a convergence loop (attempt → verify → dual critics → verdict → feedback → loop). |
| `attractor-pipeline-dual.yaml` | Multi-provider (Anthropic + OpenAI) base bundle. **Unconditionally wired into BOTH workflows**: `capsule-implement.yml` mounts it for `task-runner.dot`'s `critique_b` (issue #155), and `capsule-specify.yml` mounts it for `capsule.dot`'s `critique` node (the lean rebuild's one judgment node, which hard-declares `llm_provider="openai"`). Each workflow carries a loud preflight that refuses to start without `OPENAI_API_KEY`. |
| `scrub_secrets.py` + `test_scrub_secrets.py` | **NATIVE to this repo (not vendored).** Run-evidence secret scrubber + upload gate. Three verbs: `scrub` redacts the **evidence** roots (`.ai/`, the runner-temp `logs/` dir) in place right after the pipeline runs; `scan` is READ-ONLY and exits 1 on any finding (this is the verb the **capsule pair** gets — it cannot rewrite a byte); `gate` is what guards the artifact upload. Added after a 2026-08 incident where a worker's env dump (containing a live `OPENAI_API_KEY`) was persisted verbatim into `.ai/**/events.jsonl` and uploaded as a public artifact. Stdlib-only; see the script's docstring for the detection layers. **The capsule artifacts are deliberately NOT scrubbed** — see the two files below and the assignment-rule note in the docstring (2026-08-13 corruption incident, PR #205). **`gate` splits its verdict by finding class** (issue #206): known credential shapes hard-block the upload as before, while entropy-ONLY findings are quarantined — the spans are redacted in place as `[REDACTED:entropy]`, the roots are re-scanned, and the upload proceeds only if that second pass is clean. The entropy heuristic was blocking the evidence artifact on 4 of 4 real runs (legitimate digests/base64/request-ids in `logs/*/sessions/*/events.jsonl`), so no failed run could be diagnosed. `gate --never-redact <path>` fences any subtree that must keep the strict semantics; the specify workflows point it at `capsule-run/out` so PR #207's "the pair is scanned, never mutated" rule holds mechanically. |
| `capsule_pair_fence.sh` | **NATIVE to this repo (not vendored).** `record` / `verify` a sha256 manifest of every file in `capsule_out`: recorded immediately after the pipeline run, re-verified immediately before the bytes are staged for the branch push. Any change, addition, or removal is a loud failure that names the files. Exists because the scrubber silently rewrote a judge-approved `verify.sh` between those two points and the corrupted file was published (PR #205) with every CI check green. |
| `verify_shipped_gate.sh` | **NATIVE to this repo (not vendored).** Runs the **shipped** `<id>.verify.sh` — the exact bytes about to be pushed — in a pristine scratch worktree at the pinned base SHA, and asserts the lane's own gate contract before a capsule PR may be opened: `rc == 1` plus the declared `red_signal` in the output (defect lane, `capsule.dot`'s `redgate`), or `rc == 1` plus a complete, well-formed, non-duplicated `.ai/census` whose AC-ID set matches the recorded `<id>.census-red` and contains the declared `red_signal` as a whole row (feature lane, `feature-capsule.dot`'s `redgate`). Capped at 300s. Also re-stages the capsule's **vendored gate fixtures** (`<id>.*.py` / `<id>.*.json`) back beside the gate as `.ai/capsule/<original-name>`, the inverse of the `package` step's `<id>.` prefixing — a gate whose criteria demand an oracle *vendored beside the gate* loads it from that sibling path, and re-running the gate without it would fail the capsule for this script's omission rather than the gate's. Nothing else anywhere executes a shipped capsule gate — which is why PR #205's non-parseable gate passed every check. |
| `vendor/backlog/check-upstream-leaks.sh` | Publication-safety gate: scans the produced `DEFINITION.md` for internal working-vocabulary leaks before it ships in a PR. |
| `vendor/backlog/fixtures/leak-scan/*` | Self-test fixtures for the scanner above (RED/GREEN controls). |
| `vendor/runner/check-existing-tests.py` | **NO LONGER A BLOCKING GATE** (lean rebuild, council 2026-08-07): the `existing_test_gate` node this script backed was deleted from `capsule.dot`. It survives as a TOOL THE CRITIC MAY RUN — the `critique` node's prompt names it explicitly (`python3 $uplift_dir/runner/check-existing-tests.py --verify ... --repo .`) and treats its output as ADVISORY INPUT to a judgment, never as a verdict. (It was also a load-bearing `importlib` import for `check-witness-gate.py` until that checker was **deleted in the 2026-08-09 subtraction-sweep re-sync** — see the re-sync log; it now stands alone.) |

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

- `$uplift_dir/backlog/check-upstream-leaks.sh` (`setup` preflight existence check + `leak_gate` shell-out) -> `vendor/backlog/check-upstream-leaks.sh`
- `$uplift_dir/runner/check-existing-tests.py` (named in the `critique` node's **LLM prompt** as a tool the judge MAY run -- an advisory input, not a `tool_command=` shell-out) -> `vendor/runner/check-existing-tests.py`
- (`vendor/runner/check-witness-gate.py` is **gone** as of the 2026-08-09 subtraction-sweep re-sync: it had held zero graph-level references since the lean rebuild, the source repository then deleted it outright — zero callers, verified by grep — and the vendored copy was deleted to match. Its former `importlib` dependency on `check-existing-tests.py` vanished with it; that sibling stays vendored for the `critique` prompt reference above.)

The `vendor/` subtree therefore mirrors the source repository's own directory
layout (`backlog/`, `runner/`) one level down -- this is deliberate, not
incidental: any future checker `capsule.dot` grows will resolve correctly
under `uplift_dir` for free as long as it is vendored at the same relative
path it lives at in the source, with no path-mapping logic to maintain here.

**A class of reference exists that is *not* a `$uplift_dir/...` shell-out
and will not be found by grepping `capsule.dot` for `uplift_dir` at all**:
a checker loading a sibling script directly via `importlib`, resolved
relative to **its own file's location** (`Path(__file__).resolve().parent`)
-- not via `uplift_dir`, not via a `sys.path` entry, and not via the graph
at all. Such a sibling must be vendored in the exact same directory as its
importer, or the import silently resolves to nothing and the checker
crashes at runtime with a `FileNotFoundError` that no static check on
`capsule.dot` itself will ever surface. (The live instance of this class
was `check-witness-gate.py` loading `check-existing-tests.py` for
`resolve_subject_symbols`/`walk_repo`/`STOPWORDS`; that importer was
deleted in the 2026-08-09 subtraction-sweep re-sync, so as of that sync NO
cross-checker `importlib` import remains in the vendored tree -- but the
trap class stays named because the next vendored checker can reintroduce
it.) See "Re-syncing" step 2 below -- this is precisely the class of miss
that caused this file's own previous re-sync to ship gates whose checker
scripts weren't vendored.

**A distinct-but-related trap, closed in the void-probe sync (2026-08-07):**
a *new node* can add a **second, independent, directly-visible**
`$uplift_dir/...` shell-out to a file that is **already vendored and whose
content does not change**, with its own argument shape and its own place in
the control flow. "The file is already vendored and its self-test still
passes" is not the same claim as "this new call site's own invocation, with
its own arguments and its own place in the control flow, actually resolves
and behaves correctly." See step 5's amended wording below (the "prove
**each** call site" sentence) -- this is the reason it was added. The lean
rebuild's inverse also holds: a re-sync can DELETE every graph-level caller
of a still-vendored checker (as happened to `check-witness-gate.py` here) --
count call sites in both directions and say which files are now
advisory-only.

`task-runner.dot` is likewise **not modified** beyond its header. It was
authored for a slightly different invocation shape (a backlog task file with
its own `verify.sh` naming convention, drawn from a private task backlog
rather than a specify-stage capsule) — see the **INVOCATION
RECONCILIATION** note in its own header for exactly what was checked, what
was compatible with zero changes, and the one genuine gap (dual-family
critique needs a second model-provider key) that was closed at the
**workflow** level rather than by editing the engine:
`capsule-implement.yml` unconditionally sets `ATTRACTOR_PIPELINE_BUNDLE` to
the vendored `attractor-pipeline-dual.yaml` and carries a loud preflight
that refuses to start without `OPENAI_API_KEY` (issue #155 -- both
`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` now exist as proven repo secrets).
As of the lean-rebuild re-sync, `capsule-specify.yml` mirrors the exact same
pattern for `capsule.dot`'s `critique` node, which likewise hard-declares
`llm_provider="openai"` (a second model family, deliberately).

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

**Historical deviation, since resolved**: an earlier fixture set made
`vendor/backlog/check-upstream-leaks.sh --self-test` report 2 of its 6
self-checks failing from this vendored location (GREEN controls asserting
source-repository document text; they failed identically from the source
repo's own checkout — a fixture property, not a vendoring defect). The
current fixture set passes ALL self-checks from the vendored location
(`PASS (RED x4, GREEN x2)`, re-verified at the 2026-08-09 subtraction-sweep
re-sync). The scanner's runtime behavior (scanning an arbitrary file
against the DENY list) is exercised directly by `leak_gate` in
`capsule.dot`.

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
   runtime instead of at review time. As of the 2026-08-09
   subtraction-sweep re-sync that means: `backlog/check-upstream-leaks.sh`
   (the `setup` preflight + `leak_gate`) and
   `runner/check-existing-tests.py` (named in the `critique` node's LLM
   prompt as an advisory tool); `runner/check-witness-gate.py` was DELETED
   (source deleted it at zero callers; the vendored copy went with it).
   Also check the REVERSE direction: a re-sync can
   delete every caller of a checker (or, as with
   `runner/check-degenerate-hack.py` in this sync, delete the checker
   itself in the source) -- remove deleted files and re-count.
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

- **2026-08-08** -- `capsule.dot` re-synced
  `34fff9618e9c0cba3feee8ac8b82c94798720cec` ->
  `75ee4c6a7c99448a97f5fd7864fcbea43909eb77`. THE LEAN REBUILD -- not an
  incremental sync: the source file was rebuilt after a council review (6
  lenses, unanimous FAIL on the 34-node file) found the anti-gaming chain
  over-built -- all three passive screens PASSED the dodge they were built
  to catch (issue #146's live capsule) while the file had ZERO LLM judgment
  nodes. 1002 -> 323 lines, 35 -> 31 node declarations, 66 -> 52 edges.
  - DELETED (nodes): `mutate_gate`, `mutate_gate_b`, `diff_shape_gate`,
    `degenerate_gate`, `witness_gate`, `void_gate`, `existing_test_gate`;
    all five transient-recovery bypass edges (maker crashes now route to
    `triage` and hit the root-cause wall on repeat).
  - NEW: `nonvacuity_gate` (ONE loop over up to three patches -- A, B
    unless `# SINGLE-SHAPE:`, V unless `# NO-VOID:` -- apply -> run gate ->
    hard-reset -> PROVE the reset, with a base control-run guarding against
    a broken harness; records mechanical FACTS only); `critique` (the ONE
    judgment node, `llm_provider="openai"` / `llm_model="gpt-[5-9]*"` -- a
    second model family, deliberately); `verdict` (anchored 3-way
    classification of the judge's written verdict: SHIP / ITERATE /
    noverdict).
  - Fuse: `max_pipeline_duration` 3600s -> 14400s (4h), sized by the
    TIMEOUT ARITHMETIC in the source header. `capsule-specify.yml`'s job
    `timeout-minutes` was raised 90 -> 330 to clear it (same buffer ratio
    `capsule-implement.yml` uses over the identical 4h engine wall).
  - Checkers: `check-existing-tests.py` and `check-witness-gate.py`
    re-copied (docstrings now say NO LONGER A BLOCKING GATE / advisory
    input; the witness checker no longer imports the deleted degenerate
    checker -- `parse_hunks` is inlined). `check-degenerate-hack.py`
    **removed** -- deleted outright in the source (measured miss on its own
    target class); `grep -r` confirms no load-bearing reference (import,
    `_load(`, `$uplift_dir/...` shell-out) to it remains anywhere in the
    vendored tree (only historical prose in docstrings/this log).
  - Call-site count (step 2b, both directions):
    `backlog/check-upstream-leaks.sh` keeps two graph-level call sites
    (`setup`'s existence preflight, `leak_gate`'s shell-out);
    `runner/check-existing-tests.py` drops from one `tool_command=` caller
    to ONE LLM-prompt reference (`critique` names it as a tool the judge
    MAY run); `runner/check-witness-gate.py` drops from two `tool_command=`
    callers to ZERO (advisory-only, kept for the critic).
  - THE WIRING CHANGE this sync forces: `critique` pins
    `llm_provider="openai"`, so `capsule-specify.yml` now mirrors
    `capsule-implement.yml`'s issue-#155 pattern exactly -- unconditional
    `ATTRACTOR_PIPELINE_BUNDLE` pointing at the already-vendored
    `attractor-pipeline-dual.yaml` (byte-identical to source below its
    header; reused, not re-vendored) plus a loud `::error::` preflight that
    refuses to start without `OPENAI_API_KEY`. Under the default
    single-provider bundle that node either crashes per round or SILENTLY
    runs on Anthropic (the proven issue-#155 behavior) -- the exact failure
    the preflight exists to prevent.

  Runtime proof performed (see the PR that performed this sync for full
  transcripts), from a scratch git repo with `uplift_dir` set exactly as
  `capsule-specify.yml` sets it (`.github/capsule-pipeline/vendor`):
  (1) `nonvacuity_gate`'s own `tool_command=` text, extracted verbatim from
  the re-synced `.dot`, ran end-to-end over a real A/B/V patch triple (A
  greens, B applies-but-stays-red -- exercising the base control-run, which
  correctly re-proved rc=1 at the clean base -- V greens as a dodge):
  emitted `proven`, recorded the full facts ledger line
  (`"void_greened": true`), wrote `.ai/findings/void-greened.md`, and the
  reset proof held (clean porcelain, HEAD back at the pinned base).
  (2) `verdict`'s `tool_command=` text classified all three anchored cases:
  `VERDICT: SHIP` -> `ship` (rc=0), `VERDICT: ITERATE` -> `iterate` (rc=1,
  brief copied to `.ai/gate.log`), no anchored line -> `noverdict` (rc=0).
  (3) The `critique` prompt's advisory call site, verbatim
  (`python3 $uplift_dir/runner/check-existing-tests.py --verify ... --repo .`),
  resolved and ran through the vendored path (rc=0, a real determination).
  (4) `check-witness-gate.py --self-test` from the vendored directory: ALL
  PASSED -- proving both the surviving `importlib` sibling load and the
  inlined `parse_hunks`; a direct invocation against the scratch dodge
  patch also ran clean. Negative controls: a wrong `uplift_dir` reproduced
  the loud `can't open file` failure (rc=2), and vendoring
  `check-witness-gate.py` ALONE reproduced the exact
  `FileNotFoundError: .../check-existing-tests.py` the import trap predicts
  -- and did NOT ask for `check-degenerate-hack.py`, proving the inline
  really replaced that import. `attractor lint` on the re-synced `.dot`:
  OK, no findings; node/edge parity with source confirmed (31 node
  declarations / 52 edges on both). `task-runner.dot` was checked against
  the same source range and found **untouched** at its own separately-
  pinned commit (`f5322c24ed6fd8deaeddb519de7bfdfa861094d9`; zero
  non-comment deltas vs source) -- no re-sync needed.

- **2026-08-09** -- `capsule.dot` re-synced
  `75ee4c6a7c99448a97f5fd7864fcbea43909eb77` ->
  `7cb9ebc0f31061494c0db107c4cbd414976ba9f3`. Picks up the five-commit
  wave that just PASSED its earn-back evaluation as a set (4/6 vs the
  >=3/6 bar, zero guard-machinery episodes):
  - `25aa020` -- earn-back fix wave: `diagnose_gate` anchored sentinel;
    `--on-human-gate` invocation doc; `feedback_from="verdict"` on
    `author` (the judge's ITERATE became a SUCCESS routing token -- exit
    0, brief printed to stdout for the engine's bounded critique channel,
    routed by a `loop_restart` edge; `outcome=fail` now covers in-node
    crashes only); GREEN-binding language in the author prompt.
  - `e95f863` -- judge dodge-classification (SABOTAGE-CLASS is a recorded
    finding, REVIEWER-PLAUSIBLE blocks) + "THE GATE ASSERTS WHAT, NEVER
    HOW" (prompt-level only).
  - `9f43ba2` -- hermeticity rider on `nonvacuity_gate` (relocated-
    worktree probe after a hypothesis patch greens; `hermetic` ledger
    field; ambient-install scan recorded, never blocking) + "THE GATE
    RUNS WHERE IT STANDS" in the author prompt.
  - `f167cb3` -- RC-6 rival probe: new `rival` maker between orient and
    author (the `orient -> author` direct edge deleted), a 4th non-gating
    nonvacuity lane (`rival`/`rival_rc`/`rival_numstat`/`rival_paths`
    ledger facts, `rival-red-unadjudicated.md`/`no-rival.md` findings),
    the judge's RIVAL RULING and mandatory SURFACES stanza, and the fuse
    `max_pipeline_duration` 14400s -> 18000s (re-derived TIMEOUT
    ARITHMETIC in the source header).
  - `7cb9ebc` -- RC-7: new `rival_reset` glue node (hard-reset +
    reset-proof the moment the rival maker ends; unproven reset ->
    `reset_fail`), a pristineness PRECONDITION on `redgate` (refuses to
    measure a contaminated tree: `tree_dirty` token, `"pristine": false`
    ledger row), and per-round stale rival-finding cleanup inside
    `nonvacuity_gate`.
  Call-site count (step 2b, both directions) -- UNCHANGED by this sync:
  `backlog/check-upstream-leaks.sh` keeps its two graph-level call sites
  (`setup` preflight, `leak_gate`); `runner/check-existing-tests.py`
  keeps ONE LLM-prompt reference (`critique`); `runner/check-witness-gate.py`
  keeps ZERO (advisory-only). No checker was added or deleted in the
  source range (`git log 75ee4c6..7cb9ebc -- runner/check-*.py` is
  empty); both vendored checkers byte-compare identical to `7cb9ebc`
  (`cmp` clean) -- no re-copy needed. `grep -r` re-confirms no
  load-bearing reference to the deleted `check-degenerate-hack.py`
  anywhere in the vendored tree (historical prose only). The new
  `rival_reset` and the `redgate` precondition are pure inline shell --
  no `$uplift_dir` reference gained or lost.
  THE WIRING CHANGE this sync forces: the fuse grew to 18000s (300 min),
  so `capsule-specify.yml`'s `timeout-minutes` was raised 330 -> 360
  (the GitHub-hosted-runner hard cap; 330 no longer clears 300 +
  overhead). The reduced ~60-minute overhead budget was verified against
  real run timings, not asserted: runs 31259303929 and 31113280518 show
  all non-engine steps (checkout/setup-python/uv install before;
  classify/PR/comment/upload after) completing in SECONDS (~5s before,
  ~6-10s after) -- the 60-minute buffer exceeds observed overhead by
  more than two orders of magnitude.
  Runtime proof performed (see the PR that performed this sync for full
  transcripts), from a scratch git repo with `uplift_dir` set exactly as
  `capsule-specify.yml` sets it: (1) `redgate`'s re-synced
  `tool_command=` text, extracted verbatim: pristine tree + RED script ->
  `red_ok`; NEGATIVE (a dirty tracked file) -> `tree_dirty`,
  `"pristine": false` ledger row, refusal diagnostic in `.ai/gate.log`.
  (2) `nonvacuity_gate`'s full four-lane text over a real A/B/V/rival
  set (A greens; B applies-but-stays-red -- exercising the base
  control-run; V greens as a dodge; rival applies-but-stays-red --
  exercising the rival control-run): emitted `proven`, ledger row
  carried `"void_greened": true`, `"rival": "red", "rival_rc": "1"`,
  `"halt": "none", "hermetic": "proven"` (the relocated-worktree probe
  ran the greened gate hermetically); wrote `void-greened.md` and a
  fresh `rival-red-unadjudicated.md`, and REMOVED a deliberately-planted
  stale `no-rival.md` (the per-round cleanup). (3) `rival_reset`: a tree
  with the rival patch applied plus untracked junk -> `reset_proven`,
  tree restored, `.ai/` preserved; NEGATIVE (HEAD moved off base) ->
  `reset_unproven` with the loud RIVAL-RESET diagnostic in
  `.ai/gate.log`. (4) `verdict` classified all three anchored cases:
  SHIP -> `ship` (rc=0), ITERATE -> `iterate` at exit 0 (the new
  success-token contract) with the brief on stdout AND copied to
  `.ai/gate.log` + `last-stage-fail=critique`, no anchored line ->
  `noverdict` (rc=0). (5) The `critique` prompt's advisory call site,
  verbatim, resolved through the vendored path (rc=0, a real
  determination). Negative controls: a wrong `uplift_dir` reproduced the
  loud `can't open file` failure (rc=2); vendoring
  `check-witness-gate.py` ALONE reproduced the exact
  `FileNotFoundError: .../check-existing-tests.py` (rc=1) and did NOT
  ask for `check-degenerate-hack.py`; `check-witness-gate.py
  --self-test` from the vendored directory: ALL PASSED, and a direct
  invocation against the scratch dodge patch returned
  `VERDICT: witness_clean` (rc=0). `attractor lint` on the re-synced
  `.dot`: OK, no findings; node/edge parity with source confirmed (33
  node declarations / 58 edges on both). `task-runner.dot` was checked
  against the same source range and found **untouched** (identical blob
  at `7cb9ebc` and at its pinned `f5322c24`) -- no re-sync needed.

- **2026-08-09** -- `capsule.dot` re-synced
  `7cb9ebc0f31061494c0db107c4cbd414976ba9f3` ->
  `814754a09b63c12ccea6123d708f0e106dd124ef`. RC-8 synthetic-probe
  discipline -- **PROMPT-LEVEL ONLY**, one source commit, the named fix
  from issue #146's 5th CI attempt (an honest non-convergence: the
  authored gate probed synthetic graphs with FIXED node names and lost a
  6-round name-enumeration arms race -- every fixed name the author added
  simply joined the dodge's hardcoded skip-list, because the patch author
  can read the gate; a second recurring objection was a whole-graph
  early-return greening without the gate ever verifying per-node scope).
  The source diff is exactly two attribute lines
  (`git diff --numstat 7cb9ebc..814754a -- runner/capsule.dot` = `2 2`):
  - `author` prompt gains SYNTHETIC PROBES ARE BORN AT RUNTIME: synthetic
    witness names must be GENERATED AT GATE RUNTIME (a random suffix --
    `$RANDOM`, a `mktemp -u`-style unique name, a uuid), never fixed
    literals (a name that does not exist until the gate runs cannot be
    enumerated by any patch); plus MIXED-SCOPE PROBES -- when the reported
    rule has per-item scope (per-node, per-file, per-entry), at least one
    probe must place a triggering and a non-triggering item in the SAME
    input, so a whole-scope suppression cannot green.
  - `critique` prompt gains PROBE ENUMERABILITY: fixed enumerable probe
    IDs in the gate source are a blocking dodgeability finding ON THEIR
    OWN (a name skip-list is always reviewer-plausible; the race
    terminates only when enumeration is structurally impossible), and a
    missing mixed-scope probe on a per-item-scope subject is the same
    class at the same weight.
  Nodes/edges UNCHANGED: 33 node declarations / 58 edges, parity
  confirmed vendored vs source with the same count method on both files.
  No checker changes: `git log 7cb9ebc..dfbfe3d -- runner/check-*.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan` is EMPTY;
  both vendored `vendor/runner/` checkers byte-compare identical to the
  new source pin (`cmp` clean), and the vendored leak scanner differs
  from source only by its own provenance header (body identical).
  Call-site count (step 2b, both directions) -- UNCHANGED: the
  `$uplift_dir/...` census is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x
  LLM-prompt reference to `runner/check-existing-tests.py` in `critique`;
  `check-witness-gate.py` stays at ZERO graph-level callers,
  advisory-only). NO workflow change forced: the fuse
  (`max_pipeline_duration=18000`) and every timeout literal are
  untouched, so `capsule-specify.yml`'s 360-minute budget still clears it.
  ONE NEW TOKEN-SHAPED LITERAL enters the prompt text: `$RANDOM`. Proven
  (not assumed) to survive this repo's own shipped substitution layer as
  a literal: `modules/loop-pipeline`'s `substitution.py` contract is
  "Absent keys leave the token unchanged (literal pass-through)" (the
  brace form returns `m.group(0)` when the key is absent; the bare form
  iterates ONLY snapshot keys, so `$RANDOM` is never a substitution
  candidate), and the prompt path
  (`handlers/codergen.py::_expand_variables` ->
  `transforms.expand_params`, whose docstring reads "Unknown
  `$`-prefixed tokens are left unchanged") was exercised directly: the
  exact RC-8 phrase was run through both `substitute_context` and
  `expand_params` with a realistic context snapshot and came back
  byte-identical, `$RANDOM` still literal; the two shipped absent-key
  regression tests
  (`test_substitute_context_missing_key_leaves_literal`,
  `test_expand_params_preserves_undefined_prefixed_param`) both pass.
  The engine's eager M2 scan is inert here too: `_check_node_skip` only
  acts on refs present in `failed_outputs`, so an unresolvable `$RANDOM`
  ref never fails or skips a node. (The token sits in PROMPT text, not in
  a `tool_command` -- substitution.py's "unbound shell variable under
  `set -eu`" caveat applies only at the tool_command layer; `$RANDOM`
  becomes real shell only if the AUTHOR writes it into
  `DEFINITION.verify.sh`, where bash supplies `RANDOM` itself -- exactly
  the intent.)
  `attractor lint` on the re-synced `.dot`: OK, no findings.
  `vendor/backlog/check-upstream-leaks.sh --self-test`: PASS (RED x4,
  GREEN x2) -- note this is BETTER than the "2 of 6 failing"
  known-deviation documented above, which described an earlier fixture
  set; the current fixtures all pass from the vendored location.
  `task-runner.dot` was checked against the same source range and found
  **untouched** (identical blob `5d95826e` at `7cb9ebc`, `814754a`, and
  `dfbfe3d`) -- no re-sync needed.

- **2026-08-09 (subtraction sweep)** -- a NET-NEGATIVE sync (+121/-843 in the
  source commit): the source repository ran a measure-then-remove sweep and
  this re-sync mirrors it. All three vendored-from-source files re-pinned to
  the SAME source commit `b3bcedb5da8d60ce4490ad9ad9e2d547235891f5` (their
  pins had been allowed to drift apart; they now coincide):
  - `capsule.dot` re-synced `814754a09b63c12ccea6123d708f0e106dd124ef` ->
    `b3bcedb`. Source header slimmed 498 -> 486 lines (doctrine text
    replaced by source-side primer citations; the TIMEOUT ARITHMETIC block
    replaced by a pointer to the source-side rig test that recomputes it).
    Nodes/edges UNCHANGED. No `$uplift_dir/...` reference gained or lost.
    No workflow change forced: the fuse (`max_pipeline_duration=18000`) and
    every timeout literal are untouched, so `capsule-specify.yml`'s
    360-minute budget still clears it.
  - `task-runner.dot` re-synced `f5322c24ed6fd8deaeddb519de7bfdfa861094d9`
    -> `b3bcedb` -- the FIRST re-sync of this file since it was vendored.
    Source header slimmed 518 -> 419 lines (155 -> 56-line header) plus
    three stale in-body pointer fixes; node/edge declarations UNCHANGED.
    This sync also REMOVED the 15-line issue-#155 REVERTED comment block a
    prior pass had inserted inside the `critique_b` node (its content moved
    into this file's own provenance header, INVOCATION RECONCILIATION item
    2), so the vendored body is now genuinely byte-identical to source --
    `cmp` clean below the header, as the header has always claimed.
  - `vendor/backlog/check-upstream-leaks.sh` re-synced
    `67a53e531a133f44fa7bfc1afed3b6849a5a5610` -> `b3bcedb`. ONE deny-seed
    RETIRED: `\bcapsules?\b` -- the word became PUBLIC vocabulary when this
    very pipeline shipped upstream (301 occurrences of capsule/capsules in
    this repository's shipped tree), and its measured cost was burning
    iteration 1 in most runs (authors legitimately echo the word).
    Retirement is recorded in the script's own SEED NOTES. No fixture
    existed solely for that pattern, so none was removed; all 5 fixtures
    byte-compare identical to `b3bcedb` (`cmp` clean) -- no re-copy needed.
  - `vendor/runner/check-witness-gate.py` DELETED. The source deleted it
    outright at ZERO callers (both graph nodes that once invoked it,
    `witness_gate` and `void_gate`, died in the lean rebuild; verified by
    grep on both sides). The reverse-direction rule in step 2 is exactly
    what fired here. A full-repo dangling-reference sweep
    (`grep -r "check-witness-gate"` across workflows, docs, and this
    vendored tree) found NO load-bearing reference -- only prose in this
    README (current-state sections updated in this pass; historical log
    entries above left as history, they describe syncs that really
    happened). With the importer gone, NO cross-checker `importlib` import
    remains anywhere in `vendor/` (step 2a's grep of `vendor/runner/*.py`
    shows only stdlib imports plus string-literal test fixtures).
  - `vendor/runner/check-existing-tests.py` and
    `attractor-pipeline-dual.yaml` verified byte-identical to `b3bcedb`
    (`cmp` clean against their pins' bodies; both unchanged in the source
    range) -- NOT touched.
  - Call-site count (step 2b, both directions):
    `backlog/check-upstream-leaks.sh` keeps its two graph-level call sites
    (`setup` preflight, `leak_gate`); `runner/check-existing-tests.py`
    keeps ONE LLM-prompt reference (`critique`);
    `runner/check-witness-gate.py` goes from ZERO callers to NOT VENDORED.
  Runtime proof performed (see the PR that performed this sync for full
  transcripts): (1) `check-upstream-leaks.sh --self-test` from the vendored
  location: `PASS (RED x4, GREEN x2)`. (2) `leak_gate`'s `tool_command=`
  text, extracted verbatim from the re-synced `.dot` and run in a scratch
  git repo with `uplift_dir` set exactly as `capsule-specify.yml` sets it:
  a `DEFINITION.md` containing the word "capsule" now PASSES (`leak_clean`,
  rc=0, ledger row `"clean": true`) while one containing "primer" still
  TRIPS (rc=1, `.ai/gate.log` carrying
  `LEAK-HIT [...] pattern '\bprimer\b'` + the BLOCKED line,
  `last-stage-fail=round`) -- the retirement changed exactly the one seed
  it claimed. (3) `attractor lint`: `capsule.dot` OK, no findings;
  `task-runner.dot` 1 pre-existing CMD-001 warning (`ship_check` pipe to
  grep) -- present identically on the previous vendored copy, 0 errors,
  rc=0 on both files. (4) Node/edge parity, same comment-stripped count
  method on vendored and source: `capsule.dot` 34 declaration statements
  (33 nodes + the `graph [` attribute statement) / 58 edges on BOTH sides;
  `task-runner.dot` 24 declaration statements / 36 edges on BOTH sides.

- **2026-08-09 (RC-7 third leg + RC-8 boundary neutrality)** -- `capsule.dot`
  re-synced `b3bcedb5da8d60ce4490ad9ad9e2d547235891f5` ->
  `5c39ebf7ed3ae68997d66c888eeace300d561e26`. ONE source commit; the
  `runner/capsule.dot` diff is `42 5` (numstat), five hunks:
  - NEW `author_reset` glue node between `author` and `capsule_gate` (the
    `author -> capsule_gate` direct edge is DELETED) -- the RC-7 idiom
    applied to the author leg, symmetric with `rival_reset`: the author is
    an LLM maker that legitimately self-tests fix-shaped edits against the
    pinned tree; nothing restored the tree afterward, so `redgate`'s
    pristineness precondition (working as built) refused to measure and the
    run died at `reset_fail` with budget remaining. The node hard-resets
    (`git checkout -- .` + `git clean -qfd -e .ai`), PROVES the reset
    (clean porcelain excluding `.ai/`, HEAD back at the pinned base), and
    records the dirt EITHER WAY as a ledger fact
    (`{"gate": "author_reset", "dirty": true|false}` in
    `.ai/convergence.jsonl`) plus, when dirty, a shipped finding
    (`.ai/findings/author-tree-dirt.md`). Unproven reset ->
    `reset_unproven` -> `reset_fail` LOUD halt; `reset_fail`'s message now
    names the post-author leg alongside the others. Nodes 33 -> 34, edges
    58 -> 60.
  - `package` gains ONE new finding-ship line
    (`$id.author-tree-dirt.md`).
  - RC-8 boundary-neutrality prompt doctrine (prompt-level only): `author`
    gains RUNTIME-BORN IS NOT ENOUGH -- generated probe identifiers must be
    SEMANTICALLY NEUTRAL (no subject-domain tokens; a domain-word-plus-
    random-suffix probe smuggles an unlicensed boundary extension into the
    gate, so a correct fix of exactly the reported cases stays RED);
    `critique` gains BOUNDARY LICENSING -- embedded domain vocabulary in a
    probe is an explicit ruling the judge must make (QUOTE the issue text
    that licenses the wider boundary, or block as over-specification).
  No checker/deny-list/task-runner/dual-yaml changes:
  `git log b3bcedb..5c39ebf -- runner/check-existing-tests.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan
  runner/task-runner.dot runner/attractor-pipeline-dual.yaml` is EMPTY.
  Verified by `cmp` at `5c39ebf`: `task-runner.dot` (below its 90-line
  header), `attractor-pipeline-dual.yaml` (below 22), the leak scanner
  (below its 10 header lines), `check-existing-tests.py` (byte-identical,
  no header), and all 5 leak-scan fixtures (byte-identical) -- none
  touched. Call-site count (step 2b, both directions) -- UNCHANGED: the
  `$uplift_dir/...` census is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x LLM-prompt
  reference to `runner/check-existing-tests.py` in `critique`); the new
  `author_reset` is pure inline shell -- no `$uplift_dir` reference gained
  or lost. NO workflow change forced: the fuse
  (`max_pipeline_duration="18000s"`) and every timeout literal are
  untouched, so `capsule-specify.yml`'s 360-minute budget still clears it.
  Runtime proof performed (see the PR that performed this sync for full
  transcripts), from a scratch git repo shaped as the workflow shapes
  `target_dir`: (1) `author_reset`'s `tool_command=` text, extracted
  verbatim from the re-synced `.dot` -- DIRTY case (a modified tracked
  file + untracked junk, a prior-round ledger present): printed
  `reset_proven` (rc=0), tree restored (porcelain empty excluding `.ai/`,
  HEAD == pinned base), ledger row
  `{"iteration": 2, "gate": "author_reset", "dirty": true}` appended, and
  `.ai/findings/author-tree-dirt.md` written naming the exact dirty
  entries; CLEAN case (pristine tree): `reset_proven` (rc=0),
  `{"iteration": 1, "gate": "author_reset", "dirty": false}`, and NO
  finding file created. (2) `package`'s `tool_command=` text with a
  capsule pair + that finding present: printed `packaged` (rc=0) and the
  new ship line landed `demo-fix.author-tree-dirt.md` in `$capsule_out`
  alongside the pair. `attractor lint` on the re-synced `.dot`: OK, no
  findings (rc=0). Node/edge parity, same comment-stripped count method on
  vendored and source: 34 node declarations / 60 edges on BOTH sides
  (33/58 at the prior pin -- exactly the one-node, two-edge delta the
  source commit claims).

- **2026-08-10 (RC-9 surface binding)** -- `capsule.dot` re-synced
  `5c39ebf7ed3ae68997d66c888eeace300d561e26` ->
  `94dfc060b14a3f07e1c703811eda1f7a66817864`. ONE source commit,
  **PROMPT-ONLY**: the `runner/capsule.dot` diff is `3 3` (numstat), three
  hunks, each replacing one `prompt=` attribute line -- the surface-binding
  doctrine from the v3 transfer evaluation's named false-SHIP class (both
  false SHIPs were shipped gates red or crashing at the real merged fix
  because they bound to internal plumbing):
  - `author` gains THE GATE BINDS TO BEHAVIOR, NOT PLUMBING (assert the
    reported symptom at the outermost public surface that exhibits it;
    reach internals only THROUGH public entries; never hand-assemble
    internal state or invoke underscore-private functions with synthetic
    intermediates) and EXIT BY ASSERTION, NEVER BY CRASH (probe-setup
    exceptions -> exit >=2; code-under-test exceptions -> caught, printed,
    exit 1 -- a crash exits with the assertion code while asserting
    nothing).
  - `rival` gains SURFACE DIVERSITY IS YOUR DUTY: when the issue admits
    more than one plausible repair surface, the rival MUST repair on a
    surface DIFFERENT from the obvious first one (it samples the space of
    correct fixes); the honest single-surface degradation is a leading
    `# SINGLE-SURFACE:` comment line above the diff (a leading comment
    line is `git apply`-safe), recorded as a CLAIM the critic verifies.
    This REPLACES the old selection rule ("use the surface the reported
    symptom lives at as a user experiences it") -- the source rig's
    negative controls assert the retirement; grep confirms the old wording
    is GONE from the vendored copy.
  - `critique` gains PRIVATE-SURFACE BINDING, the sibling licensing duty
    (a probe invoking private underscore-prefixed functions or
    hand-assembling internal payloads/state binds the verdict to PLUMBING:
    either QUOTE the issue text placing the defect at that internal seam
    with no public surface exhibiting it, or it is blocking
    over-specification), plus unhandled-exception probes as a blocking
    exit-discipline finding ON THEIR OWN; and the SURFACES stanza is
    SHARPENED to named file-level coverage from the ledger's touched-path
    facts -- "no surface foreclosed" may only be claimed of surfaces some
    measured patch touched, with the mandatory shape "all measured patches
    touched only <path> -- foreclosure of other surfaces is unmeasured"
    when every measured patch landed on one surface. This REPLACES the old
    blanket wording ("which of them this run MEASURED the gate greening
    under" without named paths) -- retirement likewise rig-asserted and
    grep-confirmed gone from the vendored copy.
  Nodes/edges UNCHANGED: 34 node declarations / 60 edges, parity confirmed
  vendored vs source with the same comment-stripped count method on both
  files. Body byte-identity: sha256 of the vendored copy below its 13-line
  header == sha256 of `runner/capsule.dot` at `94dfc06`
  (`b8f7b7c7f67f796925205e9123ff47a8ea3b0820844fb3e08782e0b0c55029a6`).
  No checker/deny-list/task-runner/dual-yaml changes:
  `git log 5c39ebf..94dfc06 -- runner/check-existing-tests.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan
  runner/task-runner.dot runner/attractor-pipeline-dual.yaml` is EMPTY
  (the source commit's other files are its own rig tests and a
  retirement-audit note, none vendored). Verified by `cmp` at `94dfc06`:
  `task-runner.dot` (below its 90-line header),
  `attractor-pipeline-dual.yaml` (below 22), the leak scanner (below its
  10 inserted header lines), `check-existing-tests.py` (byte-identical,
  no header), and all 5 leak-scan fixtures (byte-identical) -- none
  touched. Call-site count (step 2b, both directions) -- UNCHANGED: the
  `$uplift_dir/...` census is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x
  LLM-prompt reference to `runner/check-existing-tests.py` in
  `critique`). NO workflow change forced: the fuse
  (`max_pipeline_duration="18000s"`) and every timeout literal are
  untouched, so `capsule-specify.yml`'s 360-minute budget still clears it.
  No new token-shaped literal enters the prompt text (the RC-8 `$RANDOM`
  analysis above remains the only such case). `attractor lint` on the
  re-synced `.dot`: OK, no findings (rc=0).

- **2026-08-10 (RC-10 interpreter-bytecode purge + RC-11 prescription
  compliance)** -- `capsule.dot` re-synced
  `94dfc060b14a3f07e1c703811eda1f7a66817864` ->
  `d6f8d3a300eb9bb78087f78c596ad9d6d040be03`. ONE source commit; the
  `runner/capsule.dot` diff is `60 12` (numstat), 13 hunks, nodes/edges
  UNCHANGED at 34/60:
  - **RC-10 (interpreter-bytecode purge; tool_command + comment changes,
    5 nodes)** -- from a mechanically-proven guard-machinery false fact in
    the source repo's heldout-v4 evaluation (E-1): stale `__pycache__`
    bytecode survived a git-proven reset because a size-preserving edit
    round-tripped within one integer mtime-second, so CPython's
    timestamp-based pyc validation (int-second mtime + size) kept serving
    fix-shaped bytecode at a truly git-pristine base -- a false
    `green_on_main`. A targeted purge idiom (`find` deleting `__pycache__/`
    dirs + `*.py[co]` files, with `.git`/`.venv`/`venv` pruned -- NEVER
    `git clean -x`, which would nuke module venvs) now extends the
    `tool_command=` of `rival_reset`, `author_reset`, `redgate`
    (pre-measure), `nonvacuity_gate` (entry + every round-trip reset,
    including the rival leg), and `discrimination_check` (after checking
    out `$later_commit`). **Additive ledger field for
    `.ai/convergence.jsonl` consumers**: the `author_reset` and `redgate`
    ledger rows gain `"pyc_purged": "<count>"` (existing fields
    unchanged); `rival_reset` echoes its count into the reset-proof output
    (`PYC-PURGE (RC-10): removed N interpreter-cache entries ...`).
  - **RC-11 (prescription compliance; PROMPT-ONLY, 2 nodes)** -- `author`
    gains PRESCRIPTION COMPLIANCE: when the latest critique names a
    concrete, specific change, the next draft MUST either APPLY it or
    carry an explicit written rebuttal at
    `.ai/findings/prescription-rebuttal.md` (**a new in-loop artifact**:
    quote the prescription, argue concretely, overwrite any stale rebuttal
    -- the file speaks for THIS draft only). `critique` gains PRESCRIPTION
    FOLLOW-THROUGH: compliance is checked FIRST, and an un-applied,
    un-rebutted prescription produces a blocking finding in exactly the
    ledger shape 'prescription from the prior round neither applied nor
    rebutted'; a written rebuttal is an argument, never auto-compliance --
    a rebuttal the judge rejects leaves the prescription standing.
  Body byte-identity: sha256 of the vendored copy below its (now 16-line)
  header == sha256 of `runner/capsule.dot` at `d6f8d3a`
  (`19b9b8ad4af85285bc8266a995e12be5f1048eabd71f65067fdc164e4ec5e707`).
  No checker/deny-list/task-runner/dual-yaml changes:
  `git log 94dfc06..d6f8d3a -- runner/check-existing-tests.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan
  runner/task-runner.dot runner/attractor-pipeline-dual.yaml` is EMPTY
  (the source commit's other files are its own rig tests --
  `runner/tests/test_capsule_stale_bytecode.sh`, new -- and a
  retirement-audit note, none vendored). Verified by `cmp` at `d6f8d3a`:
  `task-runner.dot` (below its 90-line header),
  `attractor-pipeline-dual.yaml` (below 22), the leak scanner (below its
  10 inserted header lines), `check-existing-tests.py` (byte-identical,
  no header), and all 5 leak-scan fixtures (byte-identical) -- none
  touched. Call-site count (step 2b, both directions) -- UNCHANGED: the
  `$uplift_dir/...` census is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x
  LLM-prompt reference to `runner/check-existing-tests.py` in
  `critique`); the purge idiom is pure inline shell -- no `$uplift_dir`
  reference gained or lost. NO workflow change forced: the fuse
  (`max_pipeline_duration="18000s"`) and every timeout literal (4x
  `timeout 900`) are untouched on both sides, so `capsule-specify.yml`'s
  360-minute budget still clears it. Runtime proof performed (see the PR
  that performed this sync for full transcripts), from a scratch git repo
  shaped as the workflow shapes `target_dir`: (1) `author_reset`'s
  `tool_command=` text, extracted verbatim from the re-synced `.dot`, with
  a planted `__pycache__/x.pyc` in the tree AND a decoy
  `.venv/lib/keep.pyc`: printed `reset_proven` (rc=0), the planted cache
  REMOVED, the venv decoy PRESERVED (venvs pruned, proving the purge is
  targeted, not `git clean -x`-shaped), ledger row carrying
  `"pyc_purged"` with a non-zero count; (2) the same command on a clean
  tree: `reset_proven` (rc=0), `"pyc_purged": "0"`, behavior otherwise
  unchanged from the pre-sync node; (3) `redgate`'s `tool_command=` text,
  same two cases: purge fires pre-measure, `"pyc_purged"` rides the
  redgate ledger row, gate verdict logic unchanged. `attractor lint` on
  the re-synced `.dot`: OK, no findings (rc=0).

- **2026-08-10 (heldout-v5 structural fixes: launch contract, typed diagnose
  verdict, rebut-only move, engine-record visibility)** -- `capsule.dot`
  re-synced `d6f8d3a300eb9bb78087f78c596ad9d6d040be03` ->
  `5c1d79d2d93c0fbe2fe7923bfde728d8aaed4dec`. ONE source commit; the
  `runner/capsule.dot` diff is `95 24` (numstat), nodes/edges 34/60 ->
  **35/62** (one new node, two new edges):
  - **LAUNCH CONTRACT (behavior change worth a sentence for workflow
    operators: the pipeline now writes into the target repo's
    `.git/info/exclude`)** -- `setup` idempotently appends `.ai/` to
    `.git/info/exclude` BEFORE the porcelain pristineness check.
    Repo-local and uncommitted (correct for someone else's repo), NEVER
    `.gitignore` (a `.gitignore` edit would itself dirty the tree the
    check is about to measure). This closes the class where the pipeline
    self-tripped on its own `?? .ai/` scratch and refused at `dirty_tree`
    on any target repo that does not ship an `.ai/` ignore rule (3/3
    fresh sibling-repo clones in the source repo's heldout-v5
    evaluation). The action is echoed as a recorded fact
    (`AI-EXCLUDE (launch contract): ...`). A genuinely dirty tree (any
    tracked-file modification, any non-`.ai` untracked file) still
    refuses loud.
  - **STRUCTURAL SENTINEL CLOSE (typed diagnose verdict; ONE new node,
    TWO new edges, ONE new in-loop artifact)** -- the third recurrence of
    the typed-sentinel class (a BLOCKED the diagnose LLM emitted inside a
    conditional construction matched the anchored prose grep, abandoning
    a run against its own diagnosis) is closed structurally: the verdict
    no longer lives in prose. `diagnose` gains
    `must_write=".ai/diagnose-verdict"` -- **a new in-loop artifact**, a
    single-line machine-fact file reading exactly CONTINUE or BLOCKED,
    presence + per-visit freshness enforced by the engine's `must_write=`
    contract -- plus a rewritten prompt (prose declared routing-inert; an
    engine-record reading duty: locate the runner's logs dir and QUOTE
    the failed node's `status.json` `failure_reason` verbatim before
    theorizing). `diagnose_gate` now exact-matches the FILE only (the
    prose grep on `diagnosis.md` is retired; BLOCKED anywhere in prose is
    inert); anything other than exactly one line reading CONTINUE or
    BLOCKED routes to **`diagnose_fail`** (new node), the loud-halt idiom
    -- never a silent abandon, never a silent continue. A new
    `diagnose -> escalate [outcome=fail]` edge covers `must_write`
    exhaustion/in-node crash (the retry ladder handles no-writes first;
    the edge is what remains after it), so the lane cannot dead-end.
  - **REBUT-ONLY MOVE (prompt-only, 2 nodes)** -- `author`: a rebut-only
    round re-emits the gate file as-is (satisfying the engine's freshness
    floor deliberately) and writes the rebuttal; `critique`: an
    unchanged-but-re-emitted gate accompanied by a written rebuttal is a
    LEGITIMATE compliance shape -- rule on the rebuttal's merits, never
    on the unchanged bytes.
  - **ENGINE-RECORD VISIBILITY (prompt + comment only)** -- the diagnose
    lane previously read only `.ai/` state, so engine-level `must_write`
    violations were structurally invisible; the prompt now points the LLM
    at the engine's own per-node records first. A glue capture (tee
    `failure_reason` into `.ai/`) was weighed and REJECTED in the node's
    own comment: edges cannot execute commands and the engine does not
    substitute `outcome.failure_reason` into any `tool_command` context
    key -- the prompt is the smallest mechanism that puts the fact in
    front of the model.
  Body byte-identity: sha256 of the vendored copy below its (now 19-line)
  header == sha256 of `runner/capsule.dot` at `5c1d79d`
  (`91d8cceff5c0d6461dc72c9f98d14a0c5472c5294e8ac015b67a46bdbbb4614e`).
  No checker/deny-list/task-runner/dual-yaml changes:
  `git log d6f8d3a..5c1d79d -- runner/check-existing-tests.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan
  runner/task-runner.dot runner/attractor-pipeline-dual.yaml` is EMPTY
  (the source commit's other files are its own rig tests -- including
  the new `runner/tests/test_capsule_launch_contract.sh` -- and a
  retirement-audit note, none vendored). Verified by `cmp` at `5c1d79d`:
  `task-runner.dot` (below its 90-line header),
  `attractor-pipeline-dual.yaml` (below 22), the leak scanner (below its
  10 inserted header lines), `check-existing-tests.py` (byte-identical,
  no header), and all 5 leak-scan fixtures (byte-identical) -- none
  touched. Call-site count (step 2b, both directions) -- UNCHANGED: the
  `$uplift_dir/...` census is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x
  LLM-prompt reference to `runner/check-existing-tests.py` in
  `critique`); the new `diagnose_fail` and the exclude-append are pure
  inline shell -- no `$uplift_dir` reference gained or lost. NO workflow
  change forced: the fuse (`max_pipeline_duration="18000s"`) and every
  timeout literal are untouched on both sides, so `capsule-specify.yml`'s
  360-minute budget still clears it. No new token-shaped literal enters
  the prompt text (the RC-8 `$RANDOM` analysis above remains the only
  such case). Runtime proof performed (see the PR that performed this
  sync for full transcripts), from a scratch git repo shaped as the
  workflow shapes `target_dir`: (1) `setup`'s `tool_command=` text,
  extracted verbatim from the re-synced `.dot`, on a repo WITHOUT an
  `.ai/` ignore rule (the command creates `.ai/` itself): echoed the
  AI-EXCLUDE appended fact and printed `ok`; rerun: echoed the idempotent
  fact, printed `ok`, exactly ONE `.ai/` line in `.git/info/exclude`; a
  modified tracked file and a non-`.ai` untracked file each still
  printed `dirty`. (2) `diagnose_gate`'s `tool_command=` text: verdict
  file `CONTINUE` -> `continue`; `BLOCKED` -> `blocked`; the exact
  conditional-BLOCKED prose shape in `diagnosis.md` with verdict file
  `CONTINUE` -> `continue` (prose ignored); two-line and missing verdict
  files -> `malformed` with the malformation named in `.ai/gate.log`;
  whitespace-padded `CONTINUE` -> `continue`. `attractor lint` on the
  re-synced `.dot`: OK, no findings (rc=0);
  `check-upstream-leaks.sh --self-test`: PASS (RED x4, GREEN x2).
  Node/edge parity, same comment-stripped count method on vendored and
  source: 35 node declarations / 62 edges on BOTH sides (34/60 at the
  prior pin -- exactly the one-node, two-edge delta the source commit
  claims).

- **2026-08-10 (heldout-v6 fixes: gate self-containment, no-shipped-tests
  package)** -- `capsule.dot` re-synced
  `5c1d79d2d93c0fbe2fe7923bfde728d8aaed4dec` ->
  `957293fc5687997b68244b4991a49faf2f293b29`. ONE source commit; the
  `runner/capsule.dot` diff is `69 9` (numstat), nodes/edges **UNCHANGED at
  35/62** (5 hunks: `setup` comment + `tool_command`, `author` prompt,
  `nonvacuity_gate` comment + `tool_command`, `critique` prompt):
  - **GATE SELF-CONTAINMENT (behavior change worth a sentence for
    workflow operators: a capsule whose gate is not self-contained now
    BLOCKS instead of shipping)** -- `nonvacuity_gate`'s hermeticity
    classification splits: the greened gate exiting rc>=2 in the pristine
    relocated worktree (`hermetic=unprobed_rc<N>`) is a machine-proven
    NOT-SELF-CONTAINED fact (the gate cannot produce a verdict on a fresh
    clone -- the exact invocation official scoring and every downstream
    consumer runs) and is now a BLOCKING corrective: gate FAIL -> triage
    -> author, with `.ai/gate.log` naming the failed invocation and the
    remedy class (self-provision via `uv run --with` / a script-created
    venv, or bind to repo-native fresh-clone invocations). Only
    probe-machinery trouble (`unprobed_worktree` / `unprobed_apply`)
    keeps the old undecidable-proceed posture, finding recorded in
    `.ai/findings/hermeticity.md`. The `author` prompt gains "THE CLONE
    IS PRISTINE -- SELF-PROVISION OR BIND TO REPO CONVENTIONS".
  - **NO-SHIPPED-TESTS PACKAGE (behavior change worth a sentence:
    test-less target repos now run at +2 iterations)** -- `setup` records
    a TEST-SCAN fact after the base-SHA checkout (flag file
    `.ai/no-shipped-tests` + **new ledger fields**: a `'gate': 'setup'`
    row carrying `no_shipped_tests`, `shipped_test_files`, and `budget`)
    and mints the budget in its ok-branch: a test-less subject gets
    `max_iterations`+2 (default 6 -> 8), bounded, capped at 15
    (`bump_budget`'s own cap). The `author` prompt requires a real
    regression test in the repo's own conventions on test-less subjects
    (+ SEAM FAKES PIN THE REAL PATH); the `critique` prompt gains the
    TEST-LESS SUBJECTS gate (zero real tests on a test-less subject =
    blocking unless the DoD records concretely why none is possible).
  Body byte-identity: sha256 of the vendored copy below its (now 21-line)
  header == sha256 of `runner/capsule.dot` at `957293f`
  (`6d6243b557e9f6330f8aceac444d7f312f644c65af1ad17b63b7817624c33b87`).
  No checker/deny-list/task-runner/dual-yaml changes:
  `git log 5c1d79d..957293f -- runner/check-existing-tests.py
  backlog/check-upstream-leaks.sh backlog/fixtures/leak-scan
  runner/task-runner.dot runner/attractor-pipeline-dual.yaml` is EMPTY
  (the source commit's other files are its own rig tests --
  `test_capsule_hermeticity.sh`, `test_capsule_testless_budget.sh`,
  `test_capsule_prompt_doctrine.sh` -- and a retirement-audit note, none
  vendored). Verified by `cmp` at `957293f`: `task-runner.dot` (below its
  90-line header), `attractor-pipeline-dual.yaml` (below 22), the leak
  scanner (below its 10 inserted header lines),
  `check-existing-tests.py` (byte-identical, no header), and all 5
  leak-scan fixtures (byte-identical) -- none touched. Call-site count
  (step 2b, both directions) -- UNCHANGED: the `$uplift_dir/...` census
  is identical on both sides of the sync (2x
  `backlog/check-upstream-leaks.sh` from `setup`/`leak_gate`, 1x
  LLM-prompt reference to `runner/check-existing-tests.py` in
  `critique`). NO workflow change forced: the fuse
  (`max_pipeline_duration="18000s"`) and every `timeout` literal are
  identical on both sides of the sync (census diffed), so
  `capsule-specify.yml`'s 360-minute budget still clears it; the +2
  budget bump changes round COUNT only, and the source comment's
  recomputed fuse arithmetic (an 8-round worst case projecting to ~56%
  of the fuse) rides in the body. Runtime proof performed (see the PR
  that performed this sync for full transcripts), from a scratch git
  repo shaped as the workflow shapes `target_dir`: (1) `setup`'s
  `tool_command=` text, extracted verbatim from the re-synced `.dot`, on
  a repo with NO test files: echoed `TEST-SCAN (recorded fact):
  shipped_test_files=0 no_shipped_tests=true budget=8`, wrote the ledger
  row `{"iteration": 0, "gate": "setup", "no_shipped_tests": true,
  "shipped_test_files": 0, "budget": 8}` and flag file `true`, printed
  `ok`; after committing a `tests/test_x.py`: `no_shipped_tests=false
  budget=6`, ledger row `false`/`1`/`6`, printed `ok`. (2)
  `nonvacuity_gate`'s blocking arm, proven statically from the re-synced
  `tool_command=` text (full hermeticity execution not re-run here -- the
  source rig `tests/test_capsule_hermeticity.sh` covers behavior): the
  classification arm reads `case "$h_st" in unprobed_rc*)
  UNP=selfcontain;; unprobed*) UNP=yes;; *) UNP=no;; esac` and the ONLY
  blocking arm reads `case "$h_st" in unprobed_rc*) { echo "HERMETICITY
  FAIL: THE GATE IS NOT SELF-CONTAINED. ..." ... } > .ai/gate.log; echo
  nonvacuity > .ai/last-stage-fail; exit 1;; esac` -- `unprobed_rc*`
  only; `unprobed_worktree`/`unprobed_apply` fall through to `printf
  proven` with the finding recorded. `attractor lint` on the re-synced
  `.dot`: OK, no findings (rc=0); `check-upstream-leaks.sh --self-test`:
  PASS (RED x4, GREEN x2). Node/edge parity, same comment-stripped count
  method on vendored and source: 35 node declarations / 62 edges on BOTH
  sides (35/62 at the prior pin -- exactly the zero-topology delta the
  source commit claims).

- **2026-08-17** -- `task-runner.dot` re-synced
  `b3bcedb5da8d60ce4490ad9ad9e2d547235891f5` ->
  `fae27d0e1969fd48f9b890b9c9660b10f2489471` (WORK DURABILITY, issue #220).
  One new deterministic node, `salvage`, and three rewired edges. The defect
  it closes: `package` was the ONLY node in the graph that composed a commit,
  reachable by exactly one edge (`verdict=ship`), so every non-shipping exit
  (stall, budget exhaustion, blocked diagnosis) ran `escalate -> abandon` --
  `echo ... >&2; exit 1` -- with the run's entire product still uncommitted in
  the runner's working tree. Incident run 31789137305 destroyed a complete,
  gate-GREEN implementation that way, and THIS repository's
  `capsule-implement.yml` was the actor that named the eight files and
  refused. `salvage` commits the tree AS IT STANDS to the CURRENT branch on
  the three non-shipping doors into the human gate (`diagnose_gate` blocked,
  `postmortem`, `pm_gate`), then routes to `escalate` on one unconditional
  edge. `ship_check`'s dirty leg is deliberately NOT routed through it (that
  door owes a human the forensic picture the gate just refused).
  - **Step 2 (`uplift_dir` references):** unchanged in BOTH directions --
    `grep -n uplift_dir` on the re-synced source and on the prior pin returns
    the identical two hits (`setup`'s `check-upstream-leaks.sh` preflight is
    `capsule.dot`'s, not this graph's; `task-runner.dot` itself references no
    `$uplift_dir` path at all, before or after). No checker added, removed,
    renamed, or newly called; nothing to vendor and nothing to delete.
  - **Steps 2a/2b:** no new or newly-vendored Python checker, and no new
    graph-level call site of any checker -- the new node shells out to `git`
    and nothing else. Both steps are vacuous for this sync, recorded rather
    than skipped silently.
  - **Byte parity:** `tail -n +97 .github/capsule-pipeline/task-runner.dot`
    is byte-identical to the source's `runner/task-runner.dot` at the pinned
    commit; that body's sha256
    (`7c93fe011563c9c13521c178ede6e530ed91bfde13a536b6b745f053d9c49f47`) is
    now pinned in this file's provenance box, so parity is checkable here
    without access to the source repository. The prior pin's body sha256 was
    `62b50b53b87e8fdd0b457145be646f5cc54ab414081b1f321c721b7c45f0d53e`.
  - **Topology, same comment-stripped count method on both sides:** 23 -> 24
    node declarations, 36 -> 37 edges. `attractor lint` on the re-synced
    `.dot`: 0 ERRORs, 1 warning -- the pre-existing `CMD-001` on `ship_check`,
    byte-identical to the warning at the prior pin.
  - **Source-side rig:** 30/30 green, including a new control
    (`runner/tests/test_task_runner_salvage.sh`, 34 assertions) whose engine
    battery runs the REAL engine over the REAL terminal path in both
    directions -- a reconstructed PRE-FIX wiring that must lose the work, and
    the shipped wiring on the identical fixture that must keep it.

- **2026-08-17 (THE VOID RATCHET: void-robustness on engine-class
  subjects)** -- `capsule.dot` re-synced
  `957293fc5687997b68244b4991a49faf2f293b29` ->
  `91f0d84de3fdabe27757eb8d0e8055f9d35a5797`. TWO source commits (the
  ratchet + its fuse sizing); the `runner/capsule.dot` diff is `65 11`
  (numstat), nodes/edges **UNCHANGED at 35/62** (6 hunks: graph fuse,
  `setup` comment, `nonvacuity_gate` comment + `tool_command`, `author`
  prompt, `critique` prompt, `void` prompt):
  - **THE VOID RATCHET (mechanical, inside `nonvacuity_gate`)** -- the
    measured residual (heldout-v6 finding 2; issue #231 run 31991897829;
    issues #165/#166): the critic caught the void dodge EVERY round with
    machine proof while six-plus rounds never produced the
    counter-assertion -- and nothing ever RE-TESTED a previously-greening
    dodge against the revised gate (#231's postmortem hands exactly that
    re-test to a human; #165's it5 re-green proves holes silently
    reopen). Now: every greening void patch is archived durably at
    `.ai/void-archive/it<N>.patch` (content-deduped, capped at 8 with the
    overflow recorded); every visit re-runs each archived dodge against
    the CURRENT gate (apply -> `timeout "$VATO"` (900s) -> hard-reset ->
    PROVE) under a cumulative wall (`VAWALL=1800`s; exhaustion records
    `void_ratchet: overbudget`, never a silent skip). rc==0 ALIVE /
    rc==1 CLOSED / rc>=2 loud infra. **New ledger fields** on the
    nonvacuity row: `void_archive`, `void_alive`, `void_ratchet`. An
    ALIVE dodge is republished VERBATIM in
    `.ai/findings/void-greened.md` under a counter-patch obligation;
    all-closed is stated explicitly. FACTS ONLY: an alive dodge never
    auto-blocks -- the ONE judge converts alive facts into ITERATE
    (sabotage-class dodges stay ship-legal).
  - **Doctrine (prose whose obligation the ratchet re-checks)** --
    `author` gains THE VOID RATCHET / COUNTER-PATCH OBLIGATION
    (void-greened.md is mandatory input; every alive diff must run red,
    never by special-casing bytes) and THE POSITIVE ROUND-TRIP RULE
    (value-loss defects demand a runtime-random expected value recovered
    EQUAL through the reported path; absence-only green is dodgeable by
    construction); `critique` gains THE RATCHET RULING (never
    non-blocking on the prediction a redesign would reject the dodge --
    the re-run IS that test; ITERATE prescriptions must name the
    round-trip assertion concretely; `overbudget` means unmeasured,
    never closed); the `void` maker hunts holes the archive does not
    cover (absence-only assertions first).
  - **Fuse** -- `max_pipeline_duration` `18000s` -> `19800s` (330min):
    tool_worst 11700s (9900s legs + the 1800s ratchet wall) + 4200s LLM
    allowance, x1.2 = 19080s <= 19800s, recomputed by the source rigs
    from the live text (`VAWALL` extraction is a loud failure if the
    ratchet ever escapes the model). Sized strictly INSIDE this repo's
    `capsule-specify.yml` 360-minute job ceiling (~30min overhead; the
    outer wall must outlive the fuse or the job kill pre-empts the
    fuse's own loud path). The workflow's stale sizing comment (18000s /
    300min) was updated in the same PR -- comment only, zero behavior;
    `timeout-minutes: 360` unchanged.
  - Body byte-identity BEFORE the sync: vendored copy below its header
    sha256-matched `runner/capsule.dot@957293f` (`6d6243b5...`). AFTER:
    below its (now 26-line) header sha256-matches
    `runner/capsule.dot@91f0d84`:
    `9c45d15336426c8ee0ed32ecbccac2310cfe429ece807161e7e0ec1d46bafd2c`
  - Checker/deny-list/dual-yaml parity: `git log 957293f..91f0d84 --
    runner/check-existing-tests.py backlog/check-upstream-leaks.sh
    backlog/fixtures/leak-scan runner/attractor-pipeline-dual.yaml` is
    EMPTY (0 commits per file). `$uplift_dir/...` call-site census
    IDENTICAL on both sides (2x leak scanner, 1x advisory
    check-existing-tests.py in the critique prompt); no new checker, no
    new call site, `.ai/void-archive/` lives inside the run scratch. The
    source range DOES touch `runner/task-runner.dot` and
    `runner/feature-capsule.dot` (other lanes' work, this same wave);
    their vendored copies are deliberately NOT re-synced here -- one
    residual, one re-sync -- and stay at their own pins for their own
    lanes' PRs.
  - Source rig at `91f0d84`: 30/30 test files green including the new
    `tests/test_capsule_void_robustness.sh` (24 checks; RED half runs
    the pre-ratchet `nonvacuity_gate` verbatim from `957293f` and proves
    the blind spot -- a prior round's dodge alive against the revised
    gate with no archive, no re-run, no fact; GREEN half proves archive,
    alive-fact + verbatim republication, closure under a positive
    round-trip gate, dedupe, reset-proof). `attractor lint`: OK on both
    source and the re-synced vendored copy.

- **2026-08-17 (post-review: HONEST SCOPE for the ratchet)** -- `capsule.dot`
  re-synced `91f0d84de3fdabe27757eb8d0e8055f9d35a5797` ->
  `b7b29f9bb42a307dd0abcc29689a72c31d1d6f2b`. ONE source commit, the
  adversarial review's MERGE-AFTER-FIX close on the entry immediately
  above (same PR, second sync). The `runner/capsule.dot` diff is `33 4`
  (numstat), 3 hunks (`nonvacuity_gate` comment + `tool_command`,
  `critique` prompt), nodes/edges **UNCHANGED at 35/62**:
  - **HONEST SCOPE (required fix)** -- the reviewer built the attack:
    `rm .ai/void-archive/*.patch` mid-loop, and the next visit reported
    `void_archive: 0` / `void_ratchet: none`, byte-indistinguishable from
    a run that was never dodged; worse, the `critique` prompt asserted to
    the ONE judge that *"every dodge that EVER greened ... lives verbatim
    in `.ai/void-archive/` and was re-run against THIS draft this
    round"* -- one `rm` made the judge's trusted context FALSE. The graph
    comment now carries the house HONEST SCOPE stanza (precedent:
    `task-runner.dot`'s `stamp_a` FORGE-STAMP GUARD): the ratchet is
    **ANTI-FORGETTING, not anti-adversary**; `.ai/void-archive/` and
    `.ai/convergence.jsonl` are worker-writable (the residual, named);
    the REOPEN TRIGGER is a ledger whose `void_archive` count REGRESSES
    across rounds; the RETIREMENT CONDITION is engine session attribution
    (EXTENSIONS section 29's sibling residual closer -- the same trigger
    family `stamp_a` carries). The `critique` prompt is reworded to stay
    TRUE under tampering: the archive is a **FLOOR** on what was
    re-tested, never proof that every dodge which ever greened is still
    in it, and a regressing count is itself a red flag to weigh.
  - **Regression as a FACT (recommended 1)** -- the live archive count at
    entry is compared against the PRIOR nonvacuity ledger row's
    `void_archive`; `live < prior` records `void_ratchet: regressed` plus
    a new `void_archive_prior` ledger field (the comparison, shown) and
    writes an ARCHIVE REGRESSION stanza at the head of
    `.ai/findings/void-greened.md` -- even when the archive is now empty
    and there is nothing left to re-run, the case that previously
    produced no findings file at all. Never a block: facts only, the ONE
    judge rules.
  - **`no_apply` is UNMEASURED, not CLOSED (recommended 2)** -- an
    archived dodge that no longer applies (`git apply --check` fails) was
    folded into `clean` AND printed *"All archived dodges are
    machine-proven CLOSED ... each re-run exited 1"* for a dodge that
    never ran. Now its own `void_ratchet: no_apply` value plus a
    `void_noapply` ledger tally; the findings file states NOT-APPLICABLE
    IS UNMEASURED, NOT CLOSED and the all-closed sentence only fires when
    `no_apply` and `overbudget` are both zero -- matching how
    `overbudget` was already honestly unmeasured. THE RATCHET RULING in
    `critique` gains the reading rules for both new values.
  - **Fuse UNCHANGED** -- a count comparison adds no timeout leg:
    `max_pipeline_duration` stays `19800s`, tool_worst 11700s, need
    19080s <= 19800s; both source arithmetic rigs recompute from the live
    text and stay green. No workflow change forced.
  - Body byte-identity: vendored copy below its (now 37-line) header
    sha256-matches `runner/capsule.dot@b7b29f9`:
    `a9348ed56f7fec864d6634fdc2314e90ba3402320d690202cd222595c0272c8e`
    (`9c45d153...` at the prior pin `91f0d84`).
  - Checker/deny-list/dual-yaml parity: `git log 91f0d84..b7b29f9 --
    runner/check-existing-tests.py backlog/check-upstream-leaks.sh
    backlog/fixtures/leak-scan runner/attractor-pipeline-dual.yaml
    runner/task-runner.dot runner/feature-capsule.dot` is EMPTY (0
    commits per file -- this source commit touches only
    `runner/capsule.dot`, its rig, and the design note).
    `$uplift_dir/...` call-site census IDENTICAL on both sides (2x leak
    scanner, 1x advisory `check-existing-tests.py`); no new checker, no
    new call site.
  - Source rig at `b7b29f9`: **31/31 test files green**;
    `tests/test_capsule_void_robustness.sh` grew 24 -> **37 checks**, its
    two new RED halves extracting the ratchet **as shipped at the
    reviewed pin `91f0d84`** by the same `git show` idiom as the
    `957293f` control -- THE RM ATTACK (RED: `void_archive: 0` /
    `void_ratchet: none` / no comparison / a stale round-1
    `void-greened.md`; GREEN: `void_ratchet: regressed`,
    `void_archive_prior: 1` beside `void_archive: 0`, findings rewritten)
    and `no_apply`-vs-`clean` (RED: `void_ratchet: clean` + the false
    closure claim; GREEN: `void_ratchet: no_apply`, `void_noapply: 1`,
    closure suppressed). `attractor lint`: OK on both source and the
    re-synced vendored copy.
