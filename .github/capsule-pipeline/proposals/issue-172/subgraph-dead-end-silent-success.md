---
id: subgraph-dead-end-silent-success
title: "Dead end inside a composed child subgraph is silently reported as SUCCESS to the parent"
red_signal: run_subgraph dead-end returned status='success' but expected 'fail'
base_sha: 64de299651b1d326ee5451a690cb1c51ff6bbca8
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`PipelineEngine.run_subgraph()` must treat a conditional-mismatch dead end — a node whose outgoing edges exist but none can match the current outcome — as a failure with a traceable `failure_reason`, consistent with the main loop's documented hard-fail posture. After a correct fix, a subgraph that dead-ends because its only outgoing edge has a condition that cannot match (e.g. `condition="outcome=fail"` when the node succeeded) must return `status=fail` with a non-empty `failure_reason`, not `status=success`.

## Why this matters

The engine's documented contract (`specs/EXTENSIONS.md §33`, "Main-Loop No-Matching-Edge Hard-Fail") states: "Never a silent fallback; always a traceable failure reason." That contract is enforced in the main loop (`engine.py`, `run()`) but is not enforced in `run_subgraph()`, the shared executor for:

- **Parallel fan-out branch bodies** (`handlers/parallel.py`): each branch body runs via `run_subgraph`. A dead-ended branch returns `status=success` to the fan-in, which can select it as the winning candidate — a branch that never completed its designed path wins the selection.
- **Manager-loop in-graph child chains** (`handlers/manager_loop.py`): each supervised cycle runs via `run_subgraph`. A dead-ended cycle satisfies an `outcome=success` stop condition, so the manager exits after one partial cycle with `status=success`.

In both cases the overall pipeline exits `status=success` / exit code 0 with no failure signal anywhere — not in the final outcome, not in node outcomes, not in events. The identical graph shape (a node with outgoing edges that cannot match) is a hard failure at top level and a silent success inside a composed subgraph. Authors cannot reason about which discipline applies where.

## Definition of done

The verify script (`DEFINITION.verify.sh`) checks three behavioral observables:

1. **Conditional-mismatch dead-end probe**: `run_subgraph()` is called starting from a node whose only outgoing edge has `condition="outcome=fail"` (so the edge cannot match when the node succeeds). The gate asserts that the returned outcome has `status=fail` AND a non-empty `failure_reason` AND that the dead-end node executed exactly once (not 250+ times via a safety-bound loop). At the base SHA this assertion fails — that is the red signal. A fix that returns `status=fail` with an empty `failure_reason` also fails this probe, because a traceable reason is part of the required behavior. A fix that converts no-selection to a self-loop (triggering the 250-step safety bound) also fails this probe, because the dead-end must be detected after the first execution, not after an arbitrary loop.

2. **Mixed-scope / whole-scope suppression guard**: `run_subgraph()` is also called starting from a different node in the same graph — one whose outgoing edge is unconditional and leads to an exit node. The gate asserts that this call returns `status=success`. This detects a whole-scope suppression (a fix that returns FAIL for all `run_subgraph` calls regardless of whether a dead end actually occurred).

3. **Composed parent outcome (parallel branch surface)**: A full parallel pipeline is run via `engine.run()`, with one branch that dead-ends (conditional mismatch) and one that completes normally. Both branches have a graph-level path to a shared fan-in node (valid topology), so the engine can locate the fan-in and exercise the actual composition behavior rather than failing on a topology error. The gate asserts that the overall pipeline result is NOT `status=success`. This checks that the false-GREEN propagates all the way to the pipeline outcome — not just to `run_subgraph()`'s return value — and covers the parallel branch composition surface the report identifies.

All probes use runtime-generated node names and graph names to prevent name-enumeration workarounds.

The report explicitly leaves open whether a mid-graph no-edge stop (a node with no outgoing edges at all) is a designed terminus or an error; that distinction is a resolution choice, not a gate requirement. The gate does not assert any particular behavior for that case.

## Known coupled surfaces

A fix must also address the following (facts, not a prescribed fix shape):

- `engine.py`: the `edge is None` branch in `run_subgraph()` (currently around lines 1104–1107), commented "No outgoing edge -- subgraph is complete", returns the last outcome unchanged. A correct fix makes conditional-mismatch dead ends return `status=fail` with a `failure_reason`.
- `context/engine-semantics.md §3`: currently documents `run_subgraph` as returning "the last outcome on a dead-end — no hard-fail" (flagged `[MEDIUM]`). This wording is drift-guarded by `tests/test_engine_semantics_doc_guard.py::test_d200_subgraph_path_distinguished`, which asserts the doc mentions `subgraph|run_subgraph|parallel branch`. If the behavior changes, the doc must be updated and that guard test must be updated to match the new wording.
- `specs/EXTENSIONS.md §33`: the compatibility note there explicitly reserved any change to `run_subgraph`'s dead-end behavior as a separate decision. That note should be updated to record the resolution.
- `tests/test_subgraph_runner.py::test_run_from_stops_at_dead_end`: currently asserts `outcome.status == StageStatus.SUCCESS` for a case where a node has no outgoing edges. Whether this test needs updating depends on how the fix treats that case — the gate does not prescribe the answer.

## Non-goals

- The `shape=folder` / `dot_file=` composition path is **not** affected. That path runs the child via a full child-engine `run()` call, which already hard-fails on dead ends. No change is needed there.
- The verify script does not check doc wording, spec text, or test names. It only checks the behavioral observables described above.
- The verify script does not prescribe how `run_subgraph` detects or signals the dead end (e.g. whether it calls `terminate_pipeline`, emits a `PIPELINE_ERROR` event, or uses a different mechanism). Any correct fix that returns `status=fail` with a non-empty `failure_reason` for a conditional-mismatch dead end satisfies the gate.
- The verify script does not prescribe the behavior of a node with no outgoing edges at all. That is a separate design question the report explicitly left open.
