---
id: topo006-conditional-plain-edge-false-negative
title: "TOPO-006 false negative: conditional-plus-plain-edge receiver silently passes failure to exit"
red_signal: fail_routed_to_exit diagnostic expected but got []
base_sha: da8ffd1faa87128573bd9872e12aa4f4f7747f0b
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint()` must emit at least one `fail_routed_to_exit` diagnostic when a
failure-conditioned edge routes to a node that has **both** a
condition-bearing outgoing edge and a plain (unconditional) outgoing edge
directly to the terminal success node (exit). Currently `lint()` emits
zero diagnostics for this graph shape — a false negative that lets a
pipeline silently exit green after a failed verification gate.

The end state: calling `lint()` on the hazard graph described below returns
a non-empty list of `fail_routed_to_exit` diagnostics. Nodes that truly
re-gate the flow (all outgoing edges are conditional, with no plain escape
route to the exit) continue to produce no diagnostic.

## Why this matters

The `_check_fail_routed_to_exit` rule (TOPO-006) exists to warn authors
when a failure outcome can reach the terminal success node through an
unmarked pass-through path. The current `_node_regates` helper suppresses
the diagnostic for any node that has *any* condition-bearing outgoing edge,
even when that node simultaneously has a plain unconditional edge straight
to the exit. At runtime, if the conditional edge's condition does not match,
the engine takes the plain edge — routing the failure out through the
success door. The rule is silent on exactly the shape it was designed to
catch.

**Concrete hazard graph (from issue #197):**

```
start -> work -> verify
verify -> done  [condition="outcome=success"]
verify -> triage [condition="outcome=fail"]
triage -> work  [condition="context.tool.last_line=retry"]
triage -> done              ← plain edge, the silent escape
```

When `triage` runs after a failure from `verify`:
- If `context.tool.last_line=retry` matches → back to `work` (corrective).
- If it does not match → the plain edge to `done` fires → pipeline exits
  green with a failed verification gate. Silent success.

`lint()` currently returns `[]` for `fail_routed_to_exit` on this graph.

## Definition of done

The gate script checks three behavioral conditions, all of which must hold
for the defect to be considered fixed:

1. **Hazard graph is flagged.** `lint()` on the graph above returns at
   least one `fail_routed_to_exit` diagnostic.

2. **True re-gate shape is not flagged.** A node whose every outgoing edge
   is conditional (no plain escape route to exit) produces no
   `fail_routed_to_exit` diagnostic. This is the mixed-case probe: both
   shapes appear in the same test run, so a whole-scope suppression cannot
   green the gate.

3. **Existing test suite passes.** `uv run --project modules/loop-pipeline
   pytest tests/test_topological_lint.py::TestFailRoutedToExit` must pass
   in full. The existing test `test_indirect_regating_intermediary_not_flagged`
   currently asserts `not _diag(lint(g), "fail_routed_to_exit")` for the
   hazard graph — that assertion encodes the wrong expectation and must be
   corrected (or split into two tests) as part of the fix. The gate enforces
   this by running the full class: if the wrong assertion remains, the class
   will fail after the behavioral fix, and the gate will exit 1.

The fix may refine `_node_regates`, change how
`_unmarked_passthrough_path_to_exit` handles plain edges out of re-gating
nodes, or use any other approach — the gate asserts only the observable
behavior, not the implementation shape.

The `runs_on=always`/`runs_on=failure` exemption must be preserved: a plain
edge whose target is marked `runs_on=always` or `runs_on=failure` is a
deliberately declared handled-failure termination and should not be flagged.
The gate does not probe this boundary (it is already covered by existing
passing tests); the fix must not regress it.

## Non-goals

- Changing the engine's runtime routing behavior. This is a lint-only rule.
- Flagging nodes with plain edges to non-exit nodes (those are followed by
  the BFS and evaluated on their own merits).
- Changing any other TOPO rule.
- Prescribing which internal function or code path the fix uses.
