---
id: goal-gate-loop-restart-lint
title: "goal_gate_has_retry lint rule fires on loop_restart back-edge retry"
red_signal: Node 'test_gate' has goal_gate=true but no retry_target
base_sha: 44428ab67a530f23aa5579104f4ff68e4e809c37
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint()` must not emit a `goal_gate_has_retry` WARNING for a `goal_gate=true` node that already has a `loop_restart=true` outgoing edge. After the fix, running `lint()` against `examples/pipelines/00-convergence-loop.dot` produces zero diagnostics of any severity.

## Why this matters

`00-convergence-loop.dot` is the canonical tutorial example — the first graph new contributors read and reuse as a starting point. It implements retry via an edge-based idiom (`loop_restart=true` on the back-edge), which is the primary attractor pattern the engine recognises at runtime (`engine.py:904-912`). The lint rule `_check_goal_gate_has_retry` is blind to this idiom: it only inspects `retry_target` and `fallback_retry_target` node attributes, so it fires a spurious WARNING on a graph that is correctly structured. The result is that `attractor lint --strict` on the flagship example produces a warning that tells the reader to add an attribute the example deliberately does not need — actively misleading for anyone learning the edge-retry idiom.

## Definition of done

The verification script (`DEFINITION.verify.sh`) checks all of the following automatically:

1. **Defect is gone**: `lint()` run against `examples/pipelines/00-convergence-loop.dot` produces **no** diagnostic with the message `Node 'test_gate' has goal_gate=true but no retry_target`.
2. **No regressions in the validation suite**: `tests/test_validation.py` passes in full (this file directly exercises `lint`, `validate`, and the `goal_gate_has_retry` rule, including the case where a `goal_gate` node genuinely has no retry path and must still warn).
3. **Examples corpus still clean**: `tests/test_examples_lint_clean.py` passes in full (no ERROR-severity findings introduced across any shipped example).

## Non-goals

- No particular implementation approach is required. Both widening the lint rule to recognise `loop_restart=true` outgoing edges and adding a `retry_target` attribute to the example DOT file are valid fixes; the verification script is green for either.
- Engine runtime behaviour is not changed. The `loop_restart=true` edge already works correctly at runtime; this is a lint hygiene issue only.
- The `test_examples_lint_clean.py` suite is not required to assert warning-freedom for every example — only that the specific spurious warning on `test_gate` is gone from the output.
