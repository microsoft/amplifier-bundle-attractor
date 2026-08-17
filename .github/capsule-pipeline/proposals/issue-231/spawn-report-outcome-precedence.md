---
id: spawn-report-outcome-precedence
title: "Spawned agent's report_outcome verdict lost when child produces non-empty prose"
red_signal: DEFECT: report_outcome metadata ignored when output is non-empty
base_sha: 4729400d7017aeb3eb15ec1f763476b2c7ce5afb
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

When a spawned child agent calls `report_outcome` (setting `preferred_label` and `status`),
the parent backend must honor that structured verdict even when the child also produces
non-empty prose output. The returned `Outcome` must have `is_explicit=True` and
`preferred_label` equal to the value the child passed to `report_outcome`.

The spec (`specs/EXTENSIONS.md` §35 Precedence Policy) is explicit: structured
`report_outcome` status supersedes contradicting trailing prose. The current code
implements the opposite priority: `_run_with_spawn` in `backend.py` only consults
`metadata.report_outcome` when `output.strip()` is empty. When the child produces any
non-empty text — the normal LLM case — `_outcome_from_spawn_result` is never called,
`preferred_label` is silently discarded, and the returned `Outcome` has
`is_explicit=False, preferred_label=None`.

## Why this matters

Any pipeline routing a `box` node's decision through `preferred_label` on the standalone
spawn path is broken: every `condition="outcome=<label>"` edge out of that node fails to
match. If a catch-all edge is present the pipeline loops indefinitely; without one it
hard-fails with `no_matching_edge`. The child's `report_outcome` call succeeded and the
tool confirmed it — but the routing signal is silently discarded by the parent.

## Definition of done

1. When a spawned child produces non-empty prose output AND its spawn result carries
   `metadata.report_outcome` with a valid `status` and `preferred_label`, the backend
   must honor the structured verdict: the returned `Outcome` must have `is_explicit=True`
   and `preferred_label` equal to the value from the metadata.
2. When `metadata.report_outcome` is absent, the non-empty-output path continues to work
   as before (prose-based outcome parsing, `is_explicit=False`).
3. The per-node `status.json` written to `logs_root` must record `is_explicit: true` and
   `preferred_next_label` equal to the value from `metadata.report_outcome` — this is the
   observable artifact the issue reporter described.
4. The fix is accompanied by at least one regression test in the repository's applicable
   test conventions that exercises the non-empty-output + `metadata.report_outcome` path
   through the public backend surface, asserting `is_explicit=True` and the correct
   `preferred_label`.

The verify script runs two standalone Python probes and one existing-test regression guard:

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
described. `drive_engine` is the public API that `run_pipeline` and the CLI use — it
assembles `AmplifierBackend`, `HandlerRegistry`, and `PipelineEngine` directly, which is
the same code path that the defect lives on.

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

- Changes to `pipeline-runner`'s `make_spawn_fn` or `prepared.spawn` result assembly.
  The defect is entirely in `backend.py`'s outcome reconstruction; `make_spawn_fn`
  already forwards the spawn result dict unchanged.
- Changes to the mounted-orchestrator path (`_run_with_tool_loop`), which already
  correctly prioritizes `report_outcome` via `_find_report_outcome_call`.
- Changes to the empty-output path, which already works correctly.
- Any change to how `condition="outcome=<label>"` edges are evaluated by the engine.
- Routing the transport proof through `run_pipeline` (see prescription rebuttal at
  `.ai/findings/prescription-rebuttal.md`): `run_pipeline` requires
  `amplifier_foundation.Bundle` and a live `PreparedBundle.create_session()` which
  requires real infrastructure unavailable in a hermetic gate. `drive_engine` is the
  public API `run_pipeline` calls after building the session — it is the same code path
  for backend outcome reconstruction and is the correct hermetic seam.
