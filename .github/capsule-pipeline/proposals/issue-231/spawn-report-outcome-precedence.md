---
id: spawn-report-outcome-precedence
title: "Spawned agent's report_outcome verdict lost when child produces non-empty prose"
red_signal: DEFECT: report_outcome metadata ignored when output is non-empty
base_sha: 4729400d7017aeb3eb15ec1f763476b2c7ce5afb
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

When a spawned child's spawn result carries a `report_outcome` verdict (setting
`preferred_label` and `status`), the parent backend must honor that structured verdict
even when the child also produces non-empty prose output. The returned `Outcome` must
have `is_explicit=True` and `preferred_label` equal to the value the child passed to
`report_outcome`.

This capsule targets the **parent-side §35 Precedence-Policy violation** — the half of
issue #231 where the verdict envelope *does* reach the parent and the parent discards it
anyway.

`specs/EXTENSIONS.md` §35 "Precedence Policy" (EXTENSIONS.md:2031-2041) is explicit:
*"structured `report_outcome` status supersedes contradicting trailing prose."* The
current code implements the opposite priority. Verified at the pinned base SHA
`4729400d7017aeb3eb15ec1f763476b2c7ce5afb`, in
`modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`
(`_run_with_spawn` is defined at line 399):

- **line 589** — the child's prose is read:
  `output = result.get("output", "") if isinstance(result, dict) else str(result)`
- **line 590** — the short-circuit: `if not output.strip():`
- **line 598** — `spawn_outcome = _outcome_from_spawn_result(result)`, the *only* call
  site that consults `metadata.report_outcome`, sits **inside that empty-output branch**
- **line 621** — the non-empty path falls straight through to
  `outcome = _parse_outcome(output, node=node)`, which parses prose/JSON and never
  inspects `result["metadata"]` at all

So when the child produces any non-empty text — the normal LLM case —
`_outcome_from_spawn_result` is never called, `preferred_label` is silently discarded,
and the returned `Outcome` has `is_explicit=False, preferred_label=None`. The verdict
arrived and the parent threw it away. That is precisely the precedence inversion §35
forbids.

## Scope: the parent-side half of #231 — and only that half

Issue #231 has **two independent defects wearing one symptom**
(`preferred_label=None`, `is_explicit=false` on the parent node). This capsule and its
gate cover exactly one of them.

**In scope (this capsule):** the *precedence* defect above. The spawn result carries
`metadata.report_outcome`; `backend.py` ignores it because the output was non-empty.

**Out of scope — tracked as a sibling issue:** the *transport* defect. In that half the
child's verdict envelope **never reaches the parent at all**, so there is nothing for
precedence to arbitrate. Its signature is the other branch of
`_outcome_from_spawn_result` (defined at line 1272): the fall-through at
**backend.py:1318-1322**, which mints
`notes="Child session completed with empty final message"` with `is_explicit=False`
(the notes string is line 1320). That branch is reachable **only when
`metadata.report_outcome` is absent or carries an unmappable `status`** — had the
envelope arrived, the check at line 1298 would have returned first with
`is_explicit=True`.

The `status.json` quoted in issue #231 carries exactly that `notes` string, so the
reporter's **live repro is on the transport half, not this one**. Its child's final
assistant message was empty, which means the line-590 short-circuit had already routed
to `_outcome_from_spawn_result`; it simply had no envelope to honor.

**Tracked separately as issue #285** ("report_outcome verdict envelope is not
transported from spawned child to parent — the empty-final-message half of #231").

**Issue #231 is resolved only when BOTH land.** A fix that greens this capsule's gate is
necessary but not sufficient: the reporter's live repro will still fail until #285 is
fixed too. This capsule must not be read, merged, or credited as closing #231 on its own.

## §35 ledger-truth note (documentation-accuracy observation, not fixed here)

§35's **"Implementation locations"** (EXTENSIONS.md:2054-2067) names loop-agent transport
code that **does not exist on `main`**: `modules/loop-agent/.../__init__.py` and
`agent_session.py` exist but contain zero `report_outcome` references,
`modules/loop-agent/tests/test_orchestrator_completion.py` does not exist at all, and
`git log origin/main -S report_outcome -- modules/loop-agent` returns zero commits (the
would-be transport commit `9251a6a` is not an ancestor of `main`). §35's spec text landed;
its transport implementation did not. Flagged here so a reader is not misled into assuming
the envelope is produced today — **this capsule does not fix that**; it is raised for the
maintainer in #285.

## Why this matters

Any pipeline routing a `box` node's decision through `preferred_label` on the standalone
spawn path is broken: every `condition="outcome=<label>"` edge out of that node fails to
match. If a catch-all edge is present the pipeline loops indefinitely; without one it
hard-fails with `no_matching_edge`. The child's `report_outcome` call succeeded and the
tool confirmed it — but the routing signal is silently discarded by the parent.

## Definition of done

Every criterion below is **parent-side**: each one presupposes that the spawn result
already carries `metadata.report_outcome`, and asserts what the parent does with it. None
of them assert that the envelope *travelled* — that is #285's contract, not this one's.

1. When a spawned child produces non-empty prose output AND its spawn result carries
   `metadata.report_outcome` with a valid `status` and `preferred_label`, the backend
   must honor the structured verdict: the returned `Outcome` must have `is_explicit=True`
   and `preferred_label` equal to the value from the metadata.
2. When `metadata.report_outcome` is absent, the non-empty-output path continues to work
   as before (prose-based outcome parsing, `is_explicit=False`).
3. The per-node `status.json` written to `logs_root` must record `is_explicit: true` and
   `preferred_next_label` equal to the value from `metadata.report_outcome` — this is the
   observable artifact the issue reporter described, reproduced here with the envelope
   supplied.
4. The fix is accompanied by at least one regression test in the repository's applicable
   test conventions that exercises the non-empty-output + `metadata.report_outcome` path
   through the public backend surface, asserting `is_explicit=True` and the correct
   `preferred_label`.

The verify script runs two standalone Python probes and one existing-test regression guard.
Both probes construct the spawn result themselves, with `metadata.report_outcome` already
populated — that is what makes them a test of *precedence* and not of transport:

**Probe 1 (loop-pipeline, standalone)**: A Python script written to a tmpdir *outside*
both test trees and executed directly via `python3` (not pytest). Generates a
runtime-random `preferred_label`, drives it through `AmplifierBackend.run()` with asyncio,
and exits 1 with the red_signal substring unless the recovered value round-trips equal to
the expected one. Three assertions: (a) non-empty prose + `metadata.report_outcome` with
the runtime-random label — asserts the round-trip value is recovered exactly
(`is_explicit=True`, `preferred_label` matches); (b) a mixed probe — one call with
metadata (label must round-trip), one without (prose fallback, `is_explicit=False`) — to
confirm per-call behavior rather than whole-scope suppression; (c) a non-empty JSON fail
output without metadata — asserts `_parse_outcome` is still used (kills the `if True:` void
dodge, which always calls `_outcome_from_spawn_result` and returns SUCCESS from the spawn
envelope's status field instead of FAIL from the JSON output).

**Probe 2 (pipeline-runner, standalone)**: A Python script written to a tmpdir *outside*
both test trees and executed directly via `python3` (not pytest). Calls `drive_engine()`
with a fake coordinator that has `session.spawn` registered returning the controlled spawn
result (with `metadata.report_outcome` carrying the runtime-random label). Asserts the
persisted per-node `status.json` records `is_explicit: true` and `preferred_next_label`
equal to the runtime-random label. This is the exact observable symptom the issue reporter
described, reproduced from the parent side with the envelope supplied. `drive_engine` is
the public API that `run_pipeline` and the CLI use — it assembles `AmplifierBackend`,
`HandlerRegistry`, and `PipelineEngine` directly, which is the same code path that the
defect lives on.

**Void dodge defense**: Both probes are standalone Python scripts run via `python3`, NOT
pytest test files. They are written to a tmpdir outside both module test trees, so no
`conftest.py` hook (`pytest_runtest_makereport` or otherwise) can intercept or forge their
results. The shell script observes the exit code and a required completion message directly.

**Hermeticity**: Both probes insert the invoking source tree's `loop-pipeline` directory at
`sys.path[0]` and the gate also sets `PYTHONPATH` to the same directory, ensuring the
pinned backend is resolved regardless of what is installed in either module's venv.

**Existing-test regression guard**: also folds in the existing
`test_spawn_empty_output_with_report_outcome_does_not_fall_back` test from `test_backend.py`
via pytest to confirm the fix does not regress the already-working empty-output path.

## Non-goals

- **The child-side transport half of #231 — issue #285.** Making the child's
  `report_outcome` envelope actually arrive in the spawn result (§35's
  `orchestrator:complete` → `metadata.report_outcome` transport, whose named
  implementation is unshipped on `main`, see the ledger-truth note above) is explicitly
  not this capsule's job, and this capsule's gate cannot observe it: both probes inject
  the envelope directly. #231 closes only when both halves land.
- Changes to `pipeline-runner`'s `make_spawn_fn` or `prepared.spawn` result assembly.
  `make_spawn_fn` (`modules/pipeline-runner/.../runner.py:404-491`) returns
  `await prepared.spawn(...)` unchanged, so it forwards the spawn result dict as handed
  to it — nothing here needs to change for the *precedence* defect. (Whether something
  upstream of it must change for the *transport* defect is #285's question.)
- Changes to the mounted-orchestrator path (`_run_with_tool_loop`), which already
  correctly prioritizes `report_outcome` via `_find_report_outcome_call`.
- Changes to the empty-output path, which already works correctly *when an envelope is
  present* (the `metadata.report_outcome` branch at `backend.py:1298`).
- Any change to how `condition="outcome=<label>"` edges are evaluated by the engine.
- Any correction to §35's "Implementation locations" text. The inaccuracy is recorded
  above and in #285 for the maintainer; this capsule edits no spec.
- Routing the transport proof through `run_pipeline` (see prescription rebuttal at
  `.ai/findings/prescription-rebuttal.md`): `run_pipeline` requires
  `amplifier_foundation.Bundle` and a live `PreparedBundle.create_session()` which
  requires real infrastructure unavailable in a hermetic gate. `drive_engine` is the
  public API `run_pipeline` calls after building the session — it is the same code path
  for backend outcome reconstruction and is the correct hermetic seam.
