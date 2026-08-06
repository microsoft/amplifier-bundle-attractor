## 27. `must_write=` Node Attribute — Fail-Closed Artifact Contract

> **This extension adds an artifact-contract enforcement check to the
> engine** (per retry attempt, plus a final post-override backstop).
> It does not conflict with the canonical spec; the spec is silent on
> per-node artifact contracts.  Nodes without `must_write=` are completely
> untouched (opt-in).

### Motivation

The same engine gap has been patched at the graph level repeatedly — every
guard hand-rolled after a live failure:

1. **pm_gate** (`examples/patterns/task-runner.dot`) — the postmortem node
   was observed returning SUCCESS without writing its report; a deterministic
   stub-guard gate now guarantees the file exists.
2. **Verdict gates counting absence as refusal** — in live runs of this
   pattern, a critique node silently ended on plain-text narration without
   writing its critique file; the deterministic verdict gate downstream
   (a grep against the missing file) counted the absence as a refusal, and
   a stall counter killed a run whose tree was ship-quality by direct
   re-verification.
3. **Historical postmortem stubs** — the same "completed without writing"
   shape observed before pm_gate existed.

A box node's contract is often "this file now exists with real content."  The
engine had no way to be told that.  `must_write=` puts the cheapest evidence
check (the artifact exists AND is fresh AND is non-trivial) where every graph
gets it for free, instead of every author rediscovering the trap live.

### What this extension does

A node may declare `must_write=<path>` as a node attribute.  After the handler
returns a non-FAIL outcome, the engine runs a three-axis post-execution check:

1. **Existence:** the file at `<path>` must exist.
2. **Freshness floor (REQUIRED):** `artifact.mtime > node_start_wall`
   (strictly greater than; `time.time()` snapshot taken immediately before the
   handler runs).  A pre-planted file whose mtime predates OR equals the node
   start time FAILS even if it has content — presence alone is exactly the hole
   this contract closes.  The equality case is rejected explicitly: an
   adversary (or a coarse-resolution filesystem) can set an artifact's mtime
   via `os.utime` to match the recorded start time, bypassing a `>=` check.
3. **Non-trivial:** the artifact must contain at least one non-whitespace byte.
   An empty file or a whitespace-only file does not satisfy the contract.

The check runs in two places:

1. **Per-attempt, inside the retry ladder** (`execute_with_retry`): a
   completed attempt (SUCCESS / PARTIAL_SUCCESS) that violates the contract
   consumes a retry attempt exactly like a RETRY outcome — the same shape as
   the fail-closed goal-gate verdict retries (§25).  When attempts are
   exhausted, the violation becomes a loud FAIL with a clear
   `failure_reason` naming the violated axis, and the node routes through
   its normal failure edges (`retry_target`, `condition="outcome=fail"`
   edges, etc.).
2. **As the engine's final backstop, after all outcome overrides**: the same
   check runs again AFTER the `auto_status` promotion and the
   `continue_on_fail` override, so no override can convert an
   artifact-contract violation into a silent success.

If the handler already returned FAIL, the check does not run (no
double-wrapping of failure reasons).

### Path resolution (DESIGN DECISION)

`must_write=` paths follow the same resolution rule as `requires=`:

- **Absolute paths** are used as-is.
- **Relative paths** are resolved against `context.target_dir` if set,
  falling back to `os.getcwd()`.

The task-runner invocation sets `--cwd <target_repo>` and `--param
target_dir=<target_repo>`, so `.ai/postmortem/report.md` in a postmortem node
resolves to `<target_repo>/.ai/postmortem/report.md` — which is the right
place.  Pipeline authors must document which cwd is the anchor in their graph's
invocation comments to avoid the environment-lies class at the contract layer.

### Non-trivial semantics (DESIGN DECISION)

"Non-trivial" means: `content.strip()` is non-empty (at least one
non-whitespace byte).  This is the floor.  Quality (schema, verdict
structure, minimum size) is NOT validated — that remains graph policy.

### Interaction with retries, goal_gate, and continue_on_fail

- **Retries:** a `must_write=` violation **respects `max_retries`** — and the
  mechanism is worth stating precisely, because a plain FAIL outcome is
  *never* re-attempted by `max_retries` in this engine (the retry ladder
  retries only RETRY outcomes and retryable exceptions; see spec §3.5).  The
  contract is therefore checked **per-attempt inside `execute_with_retry()`**:
  a completed attempt (SUCCESS / PARTIAL_SUCCESS) that violates the contract
  consumes a retry attempt exactly like a RETRY outcome, mirroring the
  fail-closed goal-gate verdict retries (§25).  A no-write completion is
  precisely the flaky-failure class where an in-place retry helps —
  re-invoking the handler gives it another chance to produce the artifact.
  With `max_retries=N`, a never-writes node invokes its handler exactly
  `1 + N` times before failing.  When attempts are exhausted, the violation
  becomes a loud FAIL that routes through the node's normal failure edges —
  `retry_target` and `condition="outcome=fail"` graph-routing retries work
  as usual.  `allow_partial=true` does **not** soften the exhausted FAIL to
  PARTIAL_SUCCESS (fail-closed).  This holds on **both** exhaustion paths:
  the completed-attempt path (SUCCESS/PARTIAL_SUCCESS attempts that never
  produced the artifact) AND the RETRY-exhaustion path, where the ladder
  would otherwise manufacture a `PARTIAL_SUCCESS("Retries exhausted,
  partial accepted")` verdict — that manufactured verdict is checked
  against the artifact contract before it is returned.  Retries exhausted
  + `allow_partial` + no artifact is a loud FAIL: no artifact means there
  is nothing to accept partially.
- **SKIPPED (DESIGN DECISION):** SKIPPED means the node did not execute,
  and the artifact contract applies only to **completed executions** — a
  SKIPPED outcome passes through the check unconverted, in both the retry
  ladder and the engine's final backstop.  A legitimately-skipped
  `must_write=` node (runs_on mismatch, failed dependencies, handler-side
  skip) is NOT converted to FAIL for lacking an artifact it was never asked
  to produce.  The one deliberate asymmetry: `auto_status=true` promotion
  (SKIPPED → SUCCESS) runs BEFORE the final backstop, so a promoted node
  counts as a completed execution and the contract applies to it — a node
  that ran, wrote no status, and wrote no artifact is exactly the
  narration-without-artifact class this contract exists to catch.
- **goal_gate:** the FAIL outcome returned by the must_write check has
  `is_explicit=False` (the node never asserted a verdict; the engine forced
  the FAIL).  A `goal_gate=true` node whose must_write check fires cannot
  satisfy its own gate — correct, since it produced no artifact.
- **continue_on_fail:** a `must_write=` FAIL is **non-overridable**.
  `continue_on_fail=true` does NOT suppress it.  The guarantee is by
  **ordering**, not a flag: the engine runs the must_write check as the
  FINAL backstop, after the `auto_status` promotion and the
  `continue_on_fail` override, so any non-FAIL outcome that reaches the end
  of node processing without a fresh, non-trivial artifact is failed there.
  This also covers the adjacent side door: a must_write node whose handler
  FAILED for its own reasons and whose artifact was never written cannot be
  resurrected to SUCCESS by `continue_on_fail=true` — the backstop re-checks
  the artifact contract after the override and fails the node.  A pipeline
  author cannot accidentally (or intentionally) void the artifact contract
  by adding `continue_on_fail=true` to a must_write node.

### Residual: delayed-replant window

The mtime-floor alone leaves a narrow window where an external process writes
a content-bearing file after node start but before the check runs, and the
node's own session never wrote.  **Session attribution** — correlating the
write to this node's `session_id` — is the preferred closing mechanism: it
retires the sibling-plant class entirely (a sibling node pre-writing another
node's declared artifact inside the window).  The mtime floor
is the minimum shipped here; session attribution is deferred.  The test
suite (`test_case4_delayed_replant_informational`) documents this residual
honestly: a delayed replant passes under the mtime-only implementation, by
design and on the record.

### Exemplar adoption

`examples/patterns/task-runner.dot` postmortem node declares
`must_write=".ai/postmortem/report.md"` as the first consumer.  The
`pm_gate` guard remains in place until the contract is live-proven; it is
not removed in this change (per the task's non-goal).

### Guard retirement inventory

What this contract retires, and when — honest on both halves:

- **Already retired by the freshness floor (shipped here):** guard glue that
  exists only to wipe STALE prior-round artifacts before a node re-executes.
  When a node is visited again on a graph cycle, a fresh `node_start_wall`
  is recorded for that execution — a file left over from a previous round
  has an older mtime and cannot satisfy this round's contract.
- **Retires when session attribution lands (deferred):** guard glue against
  SIBLING PLANTS — one node pre-writing another node's declared artifact
  during the delayed-replant window.  The mtime floor cannot distinguish
  that write from the node's own.
- **Retires only after the contract is live-proven:** the **pm_gate stub**
  in `examples/patterns/task-runner.dot` — subsumed by the postmortem
  node's own fail-closed artifact contract (`must_write=` is declared on
  that node in this change, but the deterministic guard is deliberately
  kept; see Exemplar adoption).

**What does NOT retire:**

- **Verdict parsing stays graph policy.**  A write-first skeleton ending
  `VERDICT: PENDING` passes every `must_write=` axis (fresh, authored,
  non-trivial) yet carries no shippable verdict — the task-runner's anchored
  `^VERDICT:` grep still refuses it.  Presence and quality are separate
  contracts by design: `must_write=` moves the presence half into the
  engine; the quality half (anchored verdict parsing, consensus, stall
  counting) remains graph policy forever.

### Backward-compatibility inventory

All existing pipelines are unaffected: the check is opt-in.  No existing node
in the shipped examples declares `must_write=`; the DOT parser already passes
unknown attributes through to `node.attrs` unchanged.  The only new behavior
is for nodes that explicitly add the attribute.

### Files touched

- `modules/loop-pipeline/amplifier_module_loop_pipeline/must_write.py` —
  `check_must_write(node, outcome, node_start_wall, context)`: the shared
  contract check (new module, so `engine` and `retry` can both use it
  without a circular import).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/retry.py` —
  per-attempt check inside `execute_with_retry()`: a completed attempt that
  violates the contract consumes a retry attempt like a RETRY outcome;
  exhaustion returns the loud FAIL (`allow_partial` does not soften it).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` —
  `node_start_wall = time.time()` recorded before handler execution;
  `_check_must_write` delegates to the shared check and runs as the FINAL
  backstop (Step 2.7, after the auto_status and continue_on_fail overrides).
- `specs/EXTENSIONS.md` — this entry.
- `examples/patterns/task-runner.dot` — postmortem node gains
  `must_write=".ai/postmortem/report.md"` (exemplar adoption).
- `modules/loop-pipeline/tests/test_engine_must_write.py` — unit tests for
  the adversarial battery cases, relative-path resolution, non-trivial
  semantics, retry semantics (`1 + max_retries` handler invocations,
  retry-then-write success, allow_partial and continue_on_fail
  interactions), backward compat, and the council-amendment battery
  (RETRY-exhaustion manufactured-verdict veto both directions, SKIPPED
  pass-through both levels, auto_status-promotion asymmetry).
- `modules/loop-pipeline/tests/test_retry.py` — exhaustion telemetry truth:
  the `pipeline:stage_failed` event's `final_status` always matches the
  returned outcome (string `allow_partial="false"`, partial acceptance,
  and must_write-vetoed partial).
- `docs/CONTRACTS.md`, `docs/DOT-SYNTAX.md`, `docs/DOT-AUTHORING-GUIDE.md`,
  `context/engine-semantics.md` — retry-ladder truth stated where
  `max_retries` is glossed (the ladder retries RETRY outcomes, retryable
  exceptions, and must_write violations; a plain FAIL is never retried in
  place), plus the continue_on_fail behavior-change sentence.
- `docs/reports/2026-02-20-nlspec-dod-gap-analysis.md` — dated errata note
  for the §11.5 "retried on RETRY or FAIL outcomes | PASS" row.

---

## 28. Run Provenance Stamping in `manifest.json`

**What:** `manifest.json` (written by the engine at run-directory creation, Spec §5.6) now
includes two additional provenance fields:

```json
{
  "graph_name": "...",
  "goal": "...",
  "start_time": "2026-08-03T00:00:00+00:00",
  "node_count": 3,
  "edge_count": 2,
  "engine_version": "0.1.0",
  "engine_commit": "abc1234..."
}
```

- `engine_version` — the `amplifier-module-loop-pipeline` package version string from
  `importlib.metadata`.  Today this is the static `pyproject.toml` value (`"0.1.0"`);
  it becomes discriminating when the package adopts release tags.
- `engine_commit` — the resolved git commit hash from PEP 610 `direct_url.json`, written
  by uv for git installs.  For editable/dev installs where `direct_url.json` is absent or
  carries no commit, the value is `"unknown"` — stamped honestly rather than guessed.

The standalone runner augments the manifest after each engine run, including a
failed run, with `runner_version`, `runner_commit`, and `provider` fields. Runner
version and commit use the same install-time metadata / PEP 610 mechanism and use
`"unknown"` when that identity is unavailable. `provider` is the runner API/CLI
selection (DOT node-level provider attributes remain the routing authority). One
writer per field — no races.

**Why:** Incident 2026-07-28: the run directory could not self-describe what code produced
it.  The incident analysis had to reconstruct engine identity from install history.  In a
fast-moving repo, "which engine produced this run?" is the first triage question; this
extension makes the run directory answer it durably.  Any cross-run comparison tooling
likewise needs per-run code provenance to be meaningful.

**Honesty contract:** `"unknown"` is the correct value when identity cannot be determined
from install-time metadata without fabricating.  A fabricated provenance field is worse
than an honest gap — stamp `"unknown"` over guessing.

**Compatibility:** Fully backward-compatible.  The five legacy fields (`graph_name`, `goal`,
`start_time`, `node_count`, `edge_count`) are unchanged.  The new fields are additive.
Existing manifest consumers (dashboards, tests reading `manifest.json`) continue to work.

**Runner-engine compatibility assertion:** The `pipeline-runner` package now includes a
startup compatibility assertion (`compat.py`) that checks for required engine symbols before
any node runs.  The chosen shape is a compat-assert (not a pinned dep or single-package
collapse) — see `compat.py` for the tradeoff rationale and the `amplifier-foundation @main`
deferral note.

---
