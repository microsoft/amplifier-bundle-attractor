# Engine Contracts

These are the contracts the attractor engine enforces with its consumers — pipeline
authors, resolver implementers, and direct CLI users. The strongdm/attractor nlspec is
the canonical specification; this doc captures the engine's behavior at decision points
the spec leaves to implementations.

**Layering:** The attractor engine is policy-light infrastructure. Resolvers add policy
on top. Pipeline authors compose pipelines using engine and resolver primitives. This doc
covers the engine layer only; resolver-level contracts belong in resolver documentation.

---

## 1. M5 Substitution Contract

**Behavior:** Context keys referenced as `$key` or `${key}` in node attribute strings
(prompts, `tool_command`, labels) are replaced with their values before handlers run.
Absent keys are left as literal text — no exception is raised.

**Implication for consumers:** A `tool_command` running under `set -eu` bash will fail
with "parameter not set" (exit 2) if it references a key absent from context. The engine
does not insert defaults; defaults are a resolver or shell concern. The universally
portable defense is shell-default syntax:

```bash
${optional_var:-fallback_value}
```

This applies identically across all invocation paths. No upstream abstraction layer needs
to be relied upon.

**Three-layer default pattern (for reference):**

| Layer | Owner | Mechanism |
|---|---|---|
| 1 — Universal floor | Pipeline author | Shell defaults in bash: `${VAR:-value}` — works for all consumers |
| 2 — Bridge enforcement | Resolver | Resolver seeds context with schema-declared defaults at dispatch (protects direct API callers that bypass the UI) |
| 3 — UI affordance | Resolver | Resolver emits `defaultValue` on schema components so users see pre-filled fields |

The engine participates only at layer 1. Layers 2 and 3 are resolver concerns.

**Spec basis:** §4.5 defines `$goal` for codergen handler prompts. M5 generalizes this
to all context keys in `tool_command` strings — a documented extension beyond the written
spec.

**Reference:** `modules/loop-pipeline/amplifier_module_loop_pipeline/substitution.py`
(module docstring).

---

## 2. Fail-Fast Policy

**Behavior:** When a node produces `outcome.status=FAIL`, unconditional outgoing edges
are NOT followed. The engine checks for explicit failure routing: a
`condition="outcome=fail"` edge, a declared `retry_target` / `fallback_retry_target`, or
a downstream node with `runs_on=always` or `runs_on=failure`. If none apply, the pipeline
terminates with the FAIL outcome.

**Implication for consumers:** Failures are loud by default. Downstream nodes that
depend on missing inputs don't silently receive nothing — they don't run at all. Pipeline
authors must explicitly opt in to any failure-routing behavior they want.

**Reference:** `modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py`
(lines 62–98; outcome-status guard at line 76).

### Explicit fail-forward opt-ins

Three mechanisms let pipeline authors override fail-fast for specific scenarios:

| Attribute / pattern | Applied to | Effect |
|---|---|---|
| `continue_on_fail="true"` | Source node | Engine converts FAIL→SUCCESS before edge selection. Downstream nodes see a success outcome and follow unconditional edges normally. Use for optional/informational nodes whose failure must not block the main flow. |
| `runs_on=always` | Downstream node | Node executes even if predecessors failed. Use for cleanup nodes (teardown, notifications) that must run regardless of upstream outcome. |
| `runs_on=failure` | Downstream node | Node executes ONLY if predecessors failed. Use for error-handling or recovery nodes. |
| `condition="outcome=fail"` edge | Outgoing edge | Matched in Step 1 of edge selection; explicit failure-routing edge. Most semantically clear when paired with a `condition="outcome=success"` edge on the same source node. |

**`continue_on_fail` and `runs_on` are not orthogonal.** A node with
`continue_on_fail=true` that fails has its outcome flipped FAIL→SUCCESS before edge
selection; a downstream `runs_on=failure` node will NOT see that predecessor as failed
(the failure was swallowed). Use `runs_on=always` on cleanup nodes that must fire after
a `continue_on_fail` predecessor. See engine.py lines 487–498 for the canonical
explanation.

**Reference:** `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`
(line 501 for `continue_on_fail` check; lines 1301–1412 for `_get_runs_on` and skip-gate
logic). Also: `edge_selection.py` lines 68–91 for the edge-level routing comment.

---

## 3. Structural Concurrency Policy

**Behavior:** The `max_parallel` node attribute caps the number of concurrent branches
during fan-out execution. The default is 4 when not specified. The attribute applies to
both `shape=component` nodes (handled by `ParallelHandler`) and engine-level multi-edge
fan-outs (handled by `_execute_parallel_fan_out`).

**Implication for consumers:** `max_parallel` controls engine-level branch concurrency
only — it does not cap LLM API calls per provider. Provider-level rate limiting (for
example, per-provider process-wide semaphores) is a provider module concern, not an
engine concern. This separation is intentional: the engine has no visibility into which
LLM backend a branch will use. Setting `max_parallel` on a parallel node bounds how many
branches the engine dispatches simultaneously; any additional rate control is the
provider module's responsibility.

**Reference:**
`modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py` (line 98,
`ParallelHandler`); `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`
(lines 1193–1199, `_execute_parallel_fan_out`).

---

## 4. Cross-Consumer Guidance

| Consumer | Relevant contracts |
|---|---|
| Attractor-compatible resolver (DOT-graph style) | M5 substitution (engine enforces layer 1 only), fail-fast, structural concurrency. The resolver adds layers 2 and 3 of the default pattern on top. |
| Attractor loaded as a tool inside an agent session | M5 substitution, fail-fast, structural concurrency. Pipeline author must use shell defaults (`${VAR:-value}`) for any context key that may be absent at execution time. |
| Custom resolver | All contracts. The resolver implements its own param/default mechanism on top of the engine's absent-key pass-through. |
| `attractor run` CLI | All contracts. Pipeline author owns defaults via shell-default syntax. |

---

## 5. Code Reference Index

| Contract | File | Location |
|---|---|---|
| M5 substitution | `modules/loop-pipeline/amplifier_module_loop_pipeline/substitution.py` | Module docstring |
| Fail-fast / edge selection | `modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py` | Lines 62–98 |
| `continue_on_fail` override | `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` | Line 501 and comment block at 487–498 |
| `runs_on` logic | `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` | Lines 1301–1412 |
| Structural concurrency (component) | `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py` | Line 98 |
| Structural concurrency (fan-out) | `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` | Lines 1193–1199 |
