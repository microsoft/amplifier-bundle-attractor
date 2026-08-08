---
id: goal-gate-lint-spurious-warning
title: "lint() emits spurious goal_gate_has_retry WARNING on 00-convergence-loop.dot"
red_signal: Node 'test_gate' has goal_gate=true but no retry_target
base_sha: 7a2991a88e1407cd250a840fefe9718f4d1253df
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint()` on `examples/pipelines/00-convergence-loop.dot` produces zero diagnostics
with rule `goal_gate_has_retry`. The canonical "00" tutorial example is lint-clean
under all rules, not just ERROR-severity ones.

## Why this matters

`00-convergence-loop.dot` is the first example a new contributor reaches for. Running
`lint --strict` on it today emits:

```
WARNING: [goal_gate_has_retry] [test_gate] Node 'test_gate' has goal_gate=true but no retry_target
```

The graph does implement retry — the edge `test_gate -> implement` carries
`loop_restart="true"` on the `condition="context.tool.last_line=gate_fail"` branch —
but the lint rule `_check_goal_gate_has_retry` (validation.py) only inspects node
attributes (`retry_target`, `fallback_retry_target`) and graph-level attributes. It
does not recognize edge-based retry, so it fires on a pattern that is topologically
correct.

The existing test `test_example_lints_without_errors` (test_examples_lint_clean.py)
only asserts the absence of ERROR-severity diagnostics, so this WARNING passes
silently and the misfiring goes undetected by the suite.

## Definition of done

- `lint()` on `examples/pipelines/00-convergence-loop.dot` returns no diagnostic
  with `rule == "goal_gate_has_retry"` and `node_id == "test_gate"`.
- The existing test suite for lint rules (`test_validation.py`) and the examples
  corpus sweep (`test_examples_lint_clean.py`) both continue to pass.
- `DEFINITION.verify.sh` exits 0.

Acceptable fix approaches (either resolves the defect):

**Fix A — update the example:** Remove `goal_gate=true` from the `test_gate` node in
`00-convergence-loop.dot`. The attribute is effectively a no-op on this node because
the tool command always exits 0 (the `|| echo gate_fail` branch ensures this), so the
goal_gate mechanism never triggers at runtime. The loop already converges via edge
routing.

**Fix B — extend the lint rule:** Update `_check_goal_gate_has_retry` in
`validation.py` to also recognize edge-based retry: a `goal_gate=true` node that has
at least one outgoing edge with `loop_restart="true"` counts as having a retry
mechanism, and the WARNING is suppressed.

## Non-goals

- Changing the severity threshold in `test_example_lints_without_errors` (that test's
  ERROR-only policy is intentional and documented).
- Removing the `goal_gate_has_retry` lint rule itself.
- Fixing any other example pipeline or any other lint rule.
