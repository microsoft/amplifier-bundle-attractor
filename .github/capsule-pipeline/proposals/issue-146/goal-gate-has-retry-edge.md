---
id: goal-gate-has-retry-edge
title: "goal_gate_has_retry lint rule fires a false WARNING for 00-convergence-loop.dot"
red_signal: goal_gate_has_retry
base_sha: 71e6c4ca2dc98f4f876a20e707e60cb309fe3da1
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint()` applied to `examples/pipelines/00-convergence-loop.dot` must not produce any diagnostic with `rule="goal_gate_has_retry"` for the `test_gate` node.

The `test_gate` node carries `goal_gate=true` and has an outgoing edge to `implement` with `loop_restart="true"`, which is the graph-structural expression of a retry path. The `goal_gate_has_retry` lint rule currently checks only for `retry_target` / `fallback_retry_target` node attributes and a graph-level `retry_target` attribute; it does not recognise an outgoing `loop_restart` edge as an equivalent retry mechanism, so it fires a spurious WARNING even though the retry path exists and is structurally correct.

The end state is that `lint()` on this example returns zero diagnostics with `rule="goal_gate_has_retry"` for `test_gate`. Either fix is acceptable:

- **Fix A (rule side):** update `_check_goal_gate_has_retry` in `validation.py` to treat any outgoing edge from the `goal_gate` node whose `loop_restart` attribute resolves to `true` as satisfying the retry requirement.
- **Fix B (example side):** add `retry_target="implement"` to the `test_gate` node in `00-convergence-loop.dot` so the attribute-based check is satisfied.

## Why this matters

`00-convergence-loop.dot` is the first tutorial example — the canonical illustration of the convergence-loop pattern. A lint WARNING on it is misleading: contributors and users running `lint()` over the examples corpus see a WARNING on the very graph that is supposed to show the correct shape. The example is structurally sound (the retry edge exists), so the WARNING is a false positive that erodes trust in the lint tool and in the example itself.

## Definition of done

**What the verify script checks (automated):**

1. The `amplifier_module_loop_pipeline` package is importable and `lint()` is callable (infrastructure guard).
2. `lint()` applied to the parsed `00-convergence-loop.dot` produces **zero** diagnostics whose `rule` field equals `"goal_gate_has_retry"` and whose `node_id` field equals `"test_gate"`. This is the direct, observable assertion: the false-positive WARNING no longer fires for this node.

**Human reviewer criteria (not automated):**

- If Fix A was applied: confirm that the rule still fires for nodes that have `goal_gate=true` but genuinely no retry mechanism at all (no `retry_target` attribute and no outgoing `loop_restart` edge). The other examples with `goal_gate_has_retry` warnings (`multi-lens-review.dot`, `pr-review.dot`) have no `loop_restart` edges, so their warnings should be unaffected.
- If Fix B was applied: confirm that the added `retry_target` attribute is consistent with the graph topology (i.e., it names the node the existing `loop_restart` edge already points to) and that the example's explanatory comments remain accurate.
- In either case: the existing `test_examples_lint_clean.py` suite (ERROR-only gate) must still pass.

## Non-goals

- Changing the severity policy in `test_examples_lint_clean.py` (that file deliberately allows WARNINGs; whether to tighten it for the flagship example is a separate question).
- Fixing the `goal_gate_has_retry` WARNING for `multi-lens-review.dot` or `pr-review.dot` — those nodes do not have `loop_restart` back-edges, so their warnings may be legitimate regardless of which fix is chosen here.
- Any change to runtime engine dispatch or pipeline execution semantics — this is a lint-only issue with no runtime impact.
