---
id: goal-gate-has-retry-loop-restart
title: "goal_gate_has_retry lint rule fires false positive on 00-convergence-loop.dot"
red_signal: goal_gate_has_retry false positive on 00-convergence-loop.dot
base_sha: 64de299651b1d326ee5451a690cb1c51ff6bbca8
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

Running `lint()` on `examples/pipelines/00-convergence-loop.dot` must produce
zero diagnostics with `rule == "goal_gate_has_retry"`.

Currently it produces one WARNING:

```
[WARNING] [goal_gate_has_retry] Node 'test_gate' has goal_gate=true but no retry_target
  fix: Add retry_target or fallback_retry_target attribute
```

This is a false positive: the graph does implement retry via the
`test_gate -> implement [loop_restart="true"]` back-edge, which is the
canonical retry mechanism for tool-gated convergence loops.

The rule must continue to fire for goal-gate nodes that genuinely lack any
retry mechanism — the fix must not remove or broadly suppress the rule.

## Why this matters

`examples/pipelines/00-convergence-loop.dot` is the first tutorial example and
the canonical reference for the convergence-loop pattern. Running
`attractor lint --strict` over the shipped examples fails on it, which is
surprising and undermines trust in the linter for new users.

The fix is either:
- **Repair the example**: add `retry_target` (or `fallback_retry_target`) to
  `test_gate` in `00-convergence-loop.dot`, or
- **Repair the rule**: teach `_check_goal_gate_has_retry` in `validation.py`
  to recognise an outgoing `loop_restart=true` edge as satisfying the retry
  requirement.

Both are valid resolutions. The gate accepts either.

## Definition of done

`DEFINITION.verify.sh` checks two behavioral conditions:

1. **Primary assertion**: `lint()` on `examples/pipelines/00-convergence-loop.dot`
   returns zero diagnostics with `rule == "goal_gate_has_retry"`. This is the
   reported defect; exit 1 with the red signal if it is still present.

2. **Positive-case preservation**: a synthetic goal-gate node with no
   `retry_target`/`fallback_retry_target` attribute and no outgoing
   `loop_restart` edge must still produce a `goal_gate_has_retry` warning.
   Node names are generated at gate runtime to prevent name-enumeration dodges.
   This confirms the rule was not simply removed or blanket-suppressed.

The gate also runs the existing shipped test
`test_validation.py::test_goal_gate_without_retry_target` as an infrastructure
check (exit 2 if it fails unexpectedly), to confirm the repo's own positive
case is intact before proceeding.

## Non-goals

- No specific implementation approach is required. The gate does not inspect
  whether the fix touches `validation.py`, `00-convergence-loop.dot`, or both.
- Whether `examples/pipelines/practical/multi-lens-review.dot` or
  `examples/pipelines/practical/pr-review.dot` should be updated is out of
  scope; those examples have no `loop_restart` back-edges and their warnings
  are correct.
- The `--strict` CLI flag behavior is out of scope; the fix targets the
  library-level `lint()` function only.
- The gate does not require the rule to recognize `loop_restart` edges in
  arbitrary synthetic graphs; it only requires the false positive on the
  shipped example to be eliminated.
