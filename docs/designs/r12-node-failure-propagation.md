---
title: R12 — Node-Failure Propagation & Reference Resolution
status: design (Phase 3)
scope: amplifier-bundle-attractor / modules/loop-pipeline
awareness: engine-only — references amplifier-foundation; no pipeline-, resolver-, or DOT-specific knowledge
authors: zen-architect (Issue 3A, two defects)
---

# R12 — Node-Failure Propagation & Reference Resolution

## Executive Summary

Two engine defects, one root cause: **the engine has no contract for how a node's outputs flow to its successors.** Today, declared outputs are written only on the success branch, dotted references are actively excluded from substitution, and successors traverse unconditional edges on FAIL with no signal that their inputs are missing. The result is a pipeline that "works" by silently feeding literal `${...}` strings into shells.

This design adds three small mechanisms to the engine, each in the spirit of "do one thing well":

1. **`outputs=` node attribute** — authors declare which context keys a node produces. Engine-known handler outputs are inferred; `outputs=` overrides.
2. **Eager reference scan + skip propagation** — before executing a node, the engine scans its attribute strings for `${key}` / `$key` tokens. If any referenced key was declared by a failed/skipped predecessor, the node is marked `SKIPPED` and a new `PIPELINE_NODE_SKIPPED` event is emitted. The skipped node's own declared outputs flow into the same `failed_outputs` table.
3. **`runs_on={always|success|failure}` axis** — orthogonal to `continue_on_fail`. Lets cleanup nodes execute precisely when they should, without inverting status.

The engine learns these as **generic mechanisms**. Pipeline authors decide policy (which outputs, which references, which cleanup behavior). No knowledge of any specific pipeline, resolver, or domain object enters the engine.

---

## Problem Framing (Phase 2 Evidence)

| Symptom | File:Line | Root cause |
|---|---|---|
| FAIL traverses unconditional edges | `engine.py:467`, `edge_selection.py:25-68` | Edge selection does not consult outcome status unless an explicit `condition="outcome=fail"` exists |
| Declared outputs lost on FAIL | `engine.py:415-416`, `handlers/tool.py:139-184` | `context_updates` is built only on the success branch |
| Dotted references silently dropped | `handlers/tool.py:75-78` | `if "." not in str(key)` excludes `${dotted.key}` from substitution |
| Three substitution sites, three behaviors | `tool.py:75-78`, `transforms.py:31-46`, `handlers/human.py:74-86` | tool/transforms silently no-op on miss; human keeps the literal `${...}` |
| `SKIPPED` defined but unreachable from engine | `outcome.py:26`, `handlers/human.py:273-284` | Only the human handler ever produces SKIPPED |
| Sequential analogue of `error_policy` missing | `handlers/parallel.py:97-164` | `fail_fast/continue/ignore` honored only in parallel branches |

Existing affordances either point the wrong way (`auto_status=true` inverts FAIL→SUCCESS) or are scoped per-node (`continue_on_fail=true`). Goal-gate retry checks at exit, not at entry.

The contract gap: **a successor has no machine-readable way to ask "are my inputs valid?"** Authors compensate by writing `condition="outcome=fail"` edges everywhere — boilerplate that a mechanism layer should eliminate.

---

## Recommended Design (Mechanism vs Policy)

The engine provides four mechanisms. Pipelines provide policy.

### M1 — Declared Outputs (`outputs=`)

**Mechanism.** Each node may carry an `outputs="key1,key2,..."` attribute listing the context keys it is *contracted* to produce on success. Handlers that already write known keys (e.g., `tool.output`, `tool.last_line`) contribute an **inferred default**. The effective output set is `inferred ∪ explicit`, with `outputs=` taking precedence on conflict.

**Why both, not one.** Pure inference embeds handler knowledge into the engine and breaks for handlers that write context-driven keys (e.g., `parse_json=true`). Pure declaration burdens every author for the common case. The hybrid is the smallest contract that covers both: the engine knows what it knows; authors fill the gap.

**Authoring example (engine-generic):**
```
launch [tool_command="…", outputs="dtu.container_name,dtu.url"]
verify [tool_command="curl ${dtu.url}", outputs="health"]
```

### M2 — Eager Reference Scan

**Mechanism.** Before invoking a handler, the engine extracts the set of `${key}` and `$key` tokens from a defined set of substitutable attributes (`tool_command`, `prompt`, `description`, `tool_env`). It compares this set against `failed_outputs`, an engine-owned mapping `{output_key → producing_node_id}` populated when a node ends in FAIL or SKIPPED.

If the intersection is non-empty, the engine produces an `Outcome(status=SKIPPED, …)` *for this node, without invoking its handler*, populates the new event payload, and adds the node's own declared outputs to `failed_outputs`.

**Why eager, not lazy.** Lazy detection (handlers asking the context "is this key missing-because-failed?") forces every handler — current and future — to participate in the contract. That violates "do one thing well": handlers do their job; the engine arbitrates flow. The eager scan lives at the one place that already knows about node attributes, edges, and outcomes. New handlers inherit skip propagation for free.

**Cost paid.** The engine teaches itself which attributes can carry references. This is a small, finite list documented in one place. Adding a new substitutable attribute is a one-line registration.

### M3 — Skip Semantics

When the eager scan triggers a skip:

- `Outcome.status = StageStatus.SKIPPED` (already in `outcome.py:26`)
- New event: **`PIPELINE_NODE_SKIPPED`** with payload
  `{node_id, cause: "predecessor_failed", references: [{key, producer_node_id}], missing_keys: [...]}`
- Skipped node's `outputs=` set is added to `failed_outputs`, keyed to *this* node — propagation is transitive.
- Edge selection treats SKIPPED like FAIL for routing: matches `condition="outcome=skipped"` first, then falls back to `condition="outcome=fail"`, then unconditional. **A SKIPPED node never traverses unconditional edges silently** — if no skip-/fail-aware edge matches, the engine emits `PIPELINE_STAGE_FAILED` and halts the linear path (existing failure-retry routing applies).
- Parallel handler (`handlers/parallel.py`) reports each branch's terminal status; on join, FAIL **and** SKIPPED branches feed the same `failed_outputs` table that downstream sequential nodes consult. This is the missing sequential analogue of `error_policy`.

### M4 — `runs_on` Axis

**Mechanism.** A new node attribute `runs_on={always|success|failure}` (default `success`).

- `success` — current behavior; engine applies M2/M3.
- `failure` — node executes only if any of its declared-input producers ended in FAIL/SKIPPED. The engine resolves missing references to the empty string and clears the skip propagation for *this node* (its own outputs still flow normally).
- `always` — node executes regardless of upstream state, with the same empty-string resolution.

**Why a separate axis.** `continue_on_fail=true` already exists and means "the *node itself* failed, but route as success." It is the wrong knob for "this node *runs because* upstream failed." Conflating them would make cleanup nodes fragile (they would silence their own genuine failures). Separation keeps each attribute on its single, clear responsibility.

**Pattern enabled (engine-generic):**
```
work    [outputs="resource.handle"]
cleanup [tool_command="release ${resource.handle}", runs_on=always]
work -> cleanup
```
Cleanup runs whether `work` succeeded, failed, or was skipped. If `resource.handle` was never written, it resolves to `""` and the author's command decides what that means.

### M5 — Unified Substitution Policy

The three substitution sites (`tool.py:75-78`, `transforms.py:31-46`, `handlers/human.py:74-86`) are unified onto **one** policy:

| Token | Resolution |
|---|---|
| `$key` (no dot) | Replace with context value if present; otherwise leave literal **and** register a miss |
| `${key}` (any chars, including dots) | Same |
| `$$` | Escape — produces literal `$` |

The dotted-key guard (`tool.py:75-78`) is removed. The engine's eager scan (M2) runs first, so by the time substitution executes, every key is either present (predecessor succeeded) or the node is already SKIPPED (M3) or running with empty-string resolution (M4). Substitution itself never sees the failure case — it just substitutes what's there.

**Why one policy.** The current divergence (silent drop in tool/transforms vs. literal-keep in human) is accidental, not intentional, and the human variant exists because it had no other signal. With M2/M3 carrying the failure signal explicitly, all three sites can share one transform.

---

## Alternatives Considered & Rejected

| Alternative | Why rejected |
|---|---|
| **Per-handler "is this key valid?" callback (lazy)** | Forces every handler — including future ones — to participate. Violates "do one thing well." Engine already owns flow arbitration. |
| **`auto_status=true` for upstream propagation** | Wrong direction (inverts status). Reuse would conflate two distinct concerns. |
| **Implicit edge: `condition="upstream_fail"` everywhere** | Pushes mechanism into edge syntax. Boilerplate scales linearly with graph size. The whole point of M2/M3 is to make this implicit. |
| **Hard-fail the pipeline on missing reference** | Reasonable default in some contexts; wrong default for graphs with cleanup/retry branches. `runs_on` + skip propagation gives authors graceful policy. |
| **Strict `outputs=` only, no inference** | Forces every existing pipeline to retrofit declarations. Migration cost too high for too little gain. |
| **Strict inference only** | Engine grows handler-specific knowledge. Fails for `parse_json` and any future dynamic-key handler. |

---

## Tradeoffs (8-Dimension Frame)

| Dimension | Assessment |
|---|---|
| **Simplicity** | +4 mechanisms, all single-purpose. Removes the dotted-key guard. Net: engine gains contract clarity. |
| **Composability** | Skip propagates through parallel join automatically. New handlers inherit M2/M3. |
| **Author burden** | Common case unchanged (inference covers it). Explicit `outputs=` is opt-in. |
| **Boundary cleanliness** | All four mechanisms live in the engine. Handlers gain nothing new to remember (they already produce `Outcome` and write context). |
| **Backward compatibility** | M5 changes substitution behavior on the dotted-key path — see Migration. |
| **Observability** | New `PIPELINE_NODE_SKIPPED` event with structured cause/reference. Skip is loud, not silent — aligns with R9 loud-fail discipline (see below). |
| **Testability** | Each mechanism has crisp pre/post conditions. Skip is a state, not a side effect. |
| **Awareness compliance** | Zero pipeline/resolver knowledge. The engine learns "outputs and references" — concepts as generic as "edges and nodes." |

---

## Loud-Fail Discipline (R9 alignment)

R9's `containers/client.py:565-583` raised `ContainerError` on critical failures rather than swallowing them. R12's analog is structurally different but philosophically identical: **failures and their consequences must produce a structured signal a downstream observer can act on.** R9 raises; R12 emits a typed event and propagates a typed status. Neither relies on string matching or silent context drift. R12 references R9's pattern; it does not depend on R9's code.

---

## Risks

1. **False-positive skips.** Author writes `${not.really.a.context.key}` as a literal string. Mitigation: M2 only marks SKIPPED if the missing key is in `failed_outputs`. A genuinely undeclared key (not produced by any predecessor) falls through to substitution, which leaves it literal — same as today's `human.py` behavior. *(Open question deferred to plan: should this also warn?)*
2. **`outputs=` drift.** Author declares `outputs="x"` but handler writes `y`. Engine cannot detect this without runtime introspection of context writes. *Plan-phase decision: optional post-hoc audit warning, not a hard error.*
3. **Skip storm.** A widely-referenced root failure cascades through a large graph. This is the *correct* behavior, but logs may be noisy. Mitigation: `PIPELINE_NODE_SKIPPED` events carry the originating producer ID, so observers can collapse cascades. Same risk as today's silent-cascade — but now visible.
4. **Parallel join semantics.** With heterogeneous branch outcomes (some SUCCESS, some SKIPPED, some FAIL), the union written to `failed_outputs` could surprise authors. Mitigation: documented; existing `error_policy=ignore` already filters parallel results.

---

## Acceptance Test (Pipeline-Author Assertions)

These are author-facing contracts the design must satisfy. Implementer translates to test code in plan/execute phase.

1. **Failed predecessor → skipped successor.** A node with `outputs="k"` that ends FAIL must cause any successor referencing `${k}` (or `$k`) to be marked `SKIPPED` *without* its handler being invoked. The successor's `outputs=` must propagate into `failed_outputs`.
2. **Loud signal.** Every skip emits exactly one `PIPELINE_NODE_SKIPPED` event with `{node_id, cause, references[*].producer_node_id, missing_keys}`. No skip is silent.
3. **Dotted references work on success.** `${a.b.c}` resolves to `context["a.b.c"]` in `tool_command`, `prompt`, `description`, and `tool_env`. (Fixes the `tool.py:75-78` defect.)
4. **Cleanup runs after failure.** A node with `runs_on=always` (or `runs_on=failure`) executes when its predecessor FAILED or SKIPPED; missing references resolve to `""`. Its own genuine failures are *not* masked (distinct from `continue_on_fail`).
5. **Parallel branches feed sequential skip.** A parallel node whose branch fails (under `error_policy=continue` or `ignore`) populates `failed_outputs` such that a downstream sequential node referencing the failed branch's declared outputs is SKIPPED.
6. **Routing precedence.** A node ending SKIPPED prefers an outgoing edge with `condition="outcome=skipped"`, then `condition="outcome=fail"`, then halts the linear path (no silent unconditional traversal).
7. **Unified substitution policy.** All three substitution sites (tool, transforms, human) treat present/missing keys identically: present → substitute; missing → leave literal token AND (engine pre-pass) trigger skip if producer failed.

---

## Migration Plan (Zero Downtime)

**Behavioral change surfaces.** M5 fixes the silent-drop bug. Two regression classes are possible:

1. **Pipelines that intentionally rely on the literal `${dotted.key}` reaching the shell.** None expected; the literal is a "bad substitution" error in bash today, so any pipeline that "works" with it is asymptomatic-broken.
2. **Pipelines whose tool nodes reference keys never written on success.** Today these silently run with literal tokens (broken but uncaught). After R12, they SKIP with a loud event. This is a *correctness improvement*, but downstream nodes that previously executed against garbage now don't execute at all.

**Compatibility levers (existing, unchanged):**
- `continue_on_fail=true` — node-level opt-in to ignore self-failure (existing).
- `runs_on=always` (new) — node-level opt-in to ignore upstream-failure.
- Inference covers nodes without `outputs=` declarations — no retrofit required for the common case.

**Migration steps:**
1. Land M1, M2, M3, M4 with inference enabled. No author action required.
2. Land M5 (drop dotted guard, unify substitution). Run the existing pipeline acceptance battery (whichever pipelines currently exercise the engine in CI) as the regression gate. Any new SKIPs surface real bugs the eager scan now catches.
3. Document `outputs=` and `runs_on=` as the recommended explicit-contract path for new pipelines.

There is no flag-flip, no version pin, no two-phase rollout required: failures that R12 surfaces were already broken; cleanup paths that R12 enables were already absent. The mechanism is additive at the boundary that matters.

---

## Open Questions Deferred to Plan/Execute

The design fixes the *contract*. The implementer decides:

- **Substitutable-attribute registry shape.** A frozen list in the engine module vs. a per-handler declaration. Either works; pick the simpler one.
- **`failed_outputs` storage.** A field on the engine instance vs. a reserved namespace inside `PipelineContext`. Naming is policy-adjacent; pick the one that round-trips through checkpoints cleanly.
- **Inference table.** Which handlers contribute which inferred outputs, and whether the table lives in `outcome.py` (alongside `StageStatus`) or in each handler module. Either is fine; consistency matters more than location.
- **Skip-vs-warn for unreferenced missing keys.** When `${k}` is referenced and `k` is in *neither* context nor `failed_outputs`, current proposal: leave literal (matches `human.py` today). Plan-phase may upgrade to `WARN` log without changing the contract.
- **Parallel branch attribution.** When multiple branches declare overlapping `outputs=`, the last-writer-wins rule for `failed_outputs` is acceptable; document it. Stricter conflict detection is a future concern.

These are implementation choices, not contract changes. The seven acceptance assertions above bound the implementer's freedom precisely.

---

## Awareness Compliance Check

- ✅ No mention of DTU, LaunchDTU, TeardownDTU, resolve_validated, dotpowers, exercise, reality_check, orchestrator, semport, smoke_test, or any other specific pipeline.
- ✅ No imports from `amplifier-resolve`, `amplifier-resolver-*`, `amplifier-bundle-resolve`, or any non-foundation bundle.
- ✅ The vocabulary used — *outputs*, *references*, *skip*, *runs_on* — is generic to any pipeline graph. Examples are placeholder names (`work`, `cleanup`, `launch`, `verify`).
- ✅ Mechanisms are taught to *all* pipeline authors equally; no pipeline gets privileged status.
- ✅ The engine remains pure mechanism; pipelines remain pure policy.
