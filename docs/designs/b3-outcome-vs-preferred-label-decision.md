# B3 decision: `outcome` condition key resolves to `preferred_label || status`

**Status:** OPEN — decision required (do NOT blind-fix)
**Found by:** full-nlspec conformance audit + .dot impact analysis (resolver + bundle corpora)
**Why this is a doc, not a fix:** conforming the engine to the spec here would BREAK real pipelines. This is load-bearing drift tangled with an active pipeline bug. It needs a coordinated decision, not a one-line change.

## The discrepancy

Upstream nlspec (`strongdm/attractor@fb57a55`): the `outcome` condition key resolves to the node's **status only**.
- §3.3 Step 2 / variable resolution: [attractor-spec.md#L412](https://github.com/strongdm/attractor/blob/fb57a55ed97372a27ac90102f436947e29f48426/attractor-spec.md#L412), §10.3–10.4 [~L1684](https://github.com/strongdm/attractor/blob/fb57a55ed97372a27ac90102f436947e29f48426/attractor-spec.md#L1684)
- The spec has a **separate** `preferred_label` key for label-based routing.

Implementation — `modules/loop-pipeline/amplifier_module_loop_pipeline/conditions.py:80`:
```python
if key == "outcome":
    return outcome.preferred_label or outcome.status.value
```
So `condition="outcome=success"` evaluates **False** whenever a handler set any `preferred_label`, and `condition="outcome=<custom-label>"` evaluates True. This is an undocumented divergence (no `specs/EXTENSIONS.md` entry).

## Why it is LOAD-BEARING (conforming to spec would break pipelines)

Pipelines route on **custom outcome labels** via this exact behavior:
- `modules/loop-pipeline/tests/fixtures/integration/semport.dot` — routes on custom values `yes/retry/process/done/port/skip`.
- `modules/loop-pipeline/tests/fixtures/integration/consensus_task.dot` — routes on `needs_dod/has_dod/yes/retry`.
- Resolver pipelines use the sibling `context.preferred_label=` form heavily (`foundry/admissions.dot`, `patterns/*`), which is unaffected — but the `outcome=<label>` form is real and in use.

If `outcome` is made status-only (spec-literal), every `condition="outcome=<custom>"` route silently stops matching. That is a worse failure mode than the current drift.

## The ACTIVE bug it is tangled with

LLM `box` goal_gate nodes that return plain text complete with `status=SUCCESS`. With the current resolution, `condition="outcome=fail"` on such a node is **inert** — it checks `preferred_label` (unset) then status (`success ≠ fail`). The pipeline authors already know:

`amplifier-resolver-dot-graph/src/.../pipelines/experiments/feature_cycle.dot:163-171` (verbatim):
```
// INERT: box-node outcome=fail parses as success, so this edge never fires
SelectWork -> ExitSuccess [condition="outcome=fail", ...]
// INERT: ... so review failures slip to Commit
Review -> FixGate [condition="outcome=fail", ...]
```
The same latent pattern (without warning comments) appears across `dotpowers.dot` (26 goal_gate nodes), `develop.dot`, and `resolve_validated.dot` — their plan-review / quality-review / fix loops likely never route to the fix path. This is a **pipeline-design** problem (an LLM must *signal* failure via `report_outcome`, a tool `last_line`, or an explicit label — it cannot be inferred from plain text), entangled with the engine's `outcome` semantics.

## Options

1. **Conform to spec (status-only `outcome`).** Cleanest vs upstream. ❌ Breaks all `outcome=<custom-label>` routing (semport, consensus_task, and any resolver pipeline using the form). Rejected unless paired with a migration.
2. **Document the current behavior as an intentional extension** in `specs/EXTENSIONS.md` (the `&&`/Rust-reference lineage suggests it was deliberate), AND add a distinct **status-only** key (e.g. `status=` or `outcome.status=`) so authors who want true status routing — including reliable `outcome=fail`/`status=fail` — have an unambiguous mechanism. ✅ Non-breaking; gives authors both tools; lets the resolver fix loops route on real failure.
3. **Hybrid:** keep `outcome` as-is, add the status-only key (option 2), then migrate the genuinely-broken `outcome=fail`-from-box-node edges in the resolver to the status key (or to a `report_outcome`-driven label). Coordinated with resolver owners.

## Recommendation

**Option 3.** Document `outcome = preferred_label || status` as an extension, add an explicit status-only routing key, then coordinate a resolver-side migration of the inert `outcome=fail` edges. This is non-breaking for the engine and actually fixes the dead failure-routing in the resolver's core loops.

**Requires coordination with `amplifier-resolver-dot-graph` owners** before any engine change — the affected pipelines (`dotpowers`, `develop`, `resolve_validated`, `feature_cycle`) are theirs.

## Out of scope for the safe-batch PR
This decision ships nothing on its own. The safe-batch PR (A1 auto_status, A2 goal_gate-quoted, B6 retry presets, B7 fan_in best_outcome) is independent and carries zero pipeline impact. B3 is recorded here for the decision and the coordinated follow-up.
