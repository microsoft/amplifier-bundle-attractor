# §3.7 Failure-Routing Conformance: graph-level `retry_target` is goal-gate-exit only

**Status:** proposed
**Branch:** `fix/folder-failure-routing-conformance`
**Motivated by:** PR #54 (robotdad) — surfaced while debugging a pipeline where a failed
`shape=folder` node silently fell through to the graph-level `retry_target` and restarted a loop.

## TL;DR

The engine's per-node failure path pulls in a **graph-level** `retry_target`/`fallback_retry_target`
catch-all that the upstream nlspec **does not** put there. Per upstream §3.7, a node failure with no
fail-edge and no **node-level** retry target must **halt loud** with the failure reason. We drifted.
The fix is a two-line **subtraction** that restores spec conformance and, as a side effect, delivers
exactly the failure semantics we want: failures fail loud; recovery is the author's explicit choice.

This is **conformance, not extension.** Our local spec already says the right thing — only the engine
(and some docs/one example) diverged.

## The upstream contract (verified)

`strongdm/attractor` `attractor-spec.md` §3.7 (commit `fb57a55`, 2026-03-17), verbatim:

> When a stage returns FAIL (or retries are exhausted), the engine attempts failure routing in this order:
> 1. **Fail edge:** an outgoing edge with `condition="outcome=fail"`. If found, follow it.
> 2. **Retry target:** Node attribute `retry_target`. Jump to that node.
> 3. **Fallback retry target:** Node attribute `fallback_retry_target`. Jump to that node.
> 4. **Pipeline termination:** No failure route found. The pipeline fails with the stage's failure reason.

Steps 2–3 are **node attributes only**. Step 4 is a **loud halt** carrying the stage's failure reason.
There is **no graph-level tier** in the per-node failure path.

Graph-level `retry_target`/`fallback_retry_target` *is* a real spec concept — but scoped to a
**different** mechanism: the goal-gate-unsatisfied-at-exit path (§3.4 / §2.5: *"Node ID to jump to if
exit is reached with unsatisfied goal gates."*). Not arbitrary node failures.

Our local copies (`specs/attractor-spec.md` §3.7, `specs/canonical/attractor-spec-canonical.md` §3.7)
mirror upstream exactly. `specs/EXTENSIONS.md` documents 14 extensions; **none** declare graph-level
retry on per-node failure. So the engine behavior is **undocumented drift**, not an intentional extension.

## Subgraph / `shape=folder` semantics (why this is the same bug)

Upstream §4.11 / §9.4: a child sub-pipeline failure → the handler returns `FAIL` → the parent applies
the **standard §3.7 routing**. Our `PipelineHandler` already does this (returns the child `Outcome`
verbatim). The caller "leverages errors within for their own routing" by putting a
`condition="outcome=fail"` edge on the folder node — spec example §10.6: `plan -> fix [condition="outcome=fail"]`.

So folder nodes need **no special handling**. Once §3.7 is conformant, an unhandled folder failure
halts loud with the child's reason, and a folder failure the caller wired with a fail-edge routes
exactly where the caller said. Both are already-correct once the graph-level catch-all is gone.

## Root cause (verified)

`modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` — `_resolve_failure_retry_target`
(used by the per-node failure path at `engine.py:452` and `:785`):

```python
target_id = (
    node.attrs.get("retry_target")              # §3.7 step 2  ✓
    or node.attrs.get("fallback_retry_target")  # §3.7 step 3  ✓
    or self.graph.graph_attrs.get("retry_target")           # NOT in §3.7  ✗
    or self.graph.graph_attrs.get("fallback_retry_target")  # NOT in §3.7  ✗
)
```

The `:785` path additionally clears all completed-node state (`completed_nodes`, `node_outcomes`,
`failed_outputs`) and restarts — i.e. the silent loop-restart that bit the PR #54 author.

The goal-gate path is **independent and untouched by this fix**: `_check_goal_gates()`
(`engine.py:1068-1074`) resolves its own node→graph cascade and returns the target via
`Outcome(suggested_next_ids=[...])`; it never calls `_resolve_failure_retry_target`. So stripping the
graph-level tiers from the failure helper does **not** regress §3.4 goal-gate conformance.

## The change

1. **Engine (the fix):** remove the two graph-level tiers from `_resolve_failure_retry_target`.
   Per-node failure routing becomes node-level-then-halt, per §3.7. Goal-gate path unchanged.

2. **Tests:** the existing `tests/test_failure_routing.py` cases
   `test_graph_retry_target_used_when_node_has_none` and `test_graph_fallback_retry_target_last_resort`
   currently lock in the **drift** — replace them with assertions that an unhandled failure **halts loud**
   with the failure reason (§3.7 step 4), and that goal-gate graph-level retry still works (§3.4).
   Add the folder-node coverage from PR #54: keep its **fail-edge-fires** case (§3.7 step 1, the
   cornerstone), reframe/replace its graph-retry baseline case to assert loud halt.

3. **Docs:** `docs/ROUTING-REFERENCE.md` (teaches graph-level `fallback_retry_target` as a node-failure
   catch-all — re-scope to goal-gate, point authors at fail-edges/node-level retry for failure recovery);
   `docs/DOT-AUTHORING-GUIDE.md:535` (add the goal-gate scope qualifier to `fallback_retry_target`) and
   the "Retry with Fallback" pattern (drop the graph-level `fallback_retry_target`; node-level on
   `implement` is sufficient and spec-correct).

4. **Example:** `examples/pipelines/04-retry-with-fallback.dot` — remove the graph-level
   `fallback_retry_target` line (node-level `retry_target`/`fallback_retry_target` on `implement` plus the
   `validate -[outcome=fail]-> implement` fail-edge are already spec-conformant); update its `.md` writeup.

## Blast radius

Behavior change: any pipeline relying on a **graph-level** `retry_target`/`fallback_retry_target` to
silently recover from an arbitrary **node** failure will now **halt loud** instead. That reliance was
off-spec and undocumented as an extension. Authors who want recovery express it explicitly (fail-edge or
node-level retry target) — which the spec has always intended. Goal-gate-driven graph-level retry is
unaffected.

## Proof plan (the gate)

Not "tests pass." Run the real engine on a pipeline where a `shape=folder` child fails (via a
`parallelogram` tool node `tool_command="exit 1"`, no LLM needed) and demonstrate, with the run's
`events.jsonl` / returned `Outcome`:

1. **Caught:** parent has `reality_check -[outcome=fail]-> handler` → routes to `handler`, child's
   failure reason preserved.
2. **Loud halt:** parent has no fail-edge, no node-level retry, but a graph-level `retry_target` present
   → pipeline **terminates FAIL** with the child's reason (does **not** jump to the graph target, does
   **not** restart the loop).

Both behaviors are what §3.7 mandates and what we want.
