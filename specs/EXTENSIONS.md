# Attractor Extensions

Documented divergences and additions relative to the canonical attractor nlspec at
[github.com/strongdm/attractor](https://github.com/strongdm/attractor). The current
canonical snapshot lives at `specs/canonical/attractor-spec-canonical.md`.

**All extensions are backward-compatible with the canonical spec — community `.dot` files
written against the canonical spec should continue to work without modification.**

When in doubt about whether a behavior is spec-conformant, consult the canonical snapshot
before assuming it is a bug.

---

## 1. BareValue Grammar Production

**What:** The value grammar accepts unquoted bare identifiers in addition to quoted strings.
Examples: `shape=box`, `rankdir=LR`, `node_type=llm`. The grammar production is:

```
BareValue ::= [A-Za-z_][A-Za-z0-9_.:-]*
```

**Why:** Graphviz DOT source uses bare identifiers pervasively for built-in shape and
direction attributes. Requiring quotes everywhere would break existing community `.dot`
files. This is an additive clarification of what Graphviz already accepts; it is not a
departure from spec intent.

**Compatibility:** Fully backward-compatible. Quoted values continue to work unchanged.

---

## 2. `default_max_retries` (with Legacy Alias `default_max_retry`)

**What:** The graph-level retry ceiling attribute is `default_max_retries` (plural). The
singular `default_max_retry` is accepted as a legacy alias and maps to the same behavior.
Default value is `0` (no retries unless explicitly configured).

**Why:** The plural form is grammatically clearer ("the number of retries" rather than "the
maximum retry"). The legacy alias ensures any existing `.dot` files using the original
singular name continue to work without modification.

**Compatibility:** Both names are valid. Prefer `default_max_retries` in new pipelines.

---

## 3. `max_retries` Node Attribute Inherits Graph-Level Default

**What:** When a node omits the `max_retries` attribute, it inherits the graph's
`default_max_retries` value rather than defaulting to `0` independently. This allows a
single graph-level setting to establish a retry policy for all nodes simultaneously.

**Why:** Without inheritance, authors must repeat `max_retries=N` on every node that
should participate in a retry policy. The inheritance behavior is the natural complement to
`default_max_retries` existing at all: a graph-level default that nothing inherits would
serve no purpose.

**Compatibility:** Only observable in pipelines that set `default_max_retries` at the
graph level. Pipelines that do not set it see no change (effective retries remain 0).

---

## 4. `goal_gate` Accepts `PARTIAL_SUCCESS`

**What:** A node marked `goal_gate=true` is considered satisfied by either `SUCCESS` or
`PARTIAL_SUCCESS` outcome status. It is NOT satisfied by `FAIL`, `SKIP`, or other
statuses, and the pipeline exits with an unsatisfied-goal error if the node does not
reach at least `PARTIAL_SUCCESS`.

**Why:** Rigid `SUCCESS`-only gate semantics are too coarse for pipelines that implement
best-effort or iterative workflows — for example, a test-generation node that passes most
cases but flags a few as needing human review. Accepting `PARTIAL_SUCCESS` preserves the
gate intent (the node ran and made meaningful progress) while not blocking pipelines that
legitimately reach a partial outcome.

**Compatibility:** Existing `goal_gate=true` nodes that return `SUCCESS` are unaffected.
Nodes that return `PARTIAL_SUCCESS` now satisfy the gate where they previously would have
caused a pipeline failure.

---

## 5. Explicit TRANSFORM Phase in Execution Lifecycle

**What:** The execution lifecycle includes six phases rather than five:

```
PARSE -> TRANSFORM -> VALIDATE -> INITIALIZE -> EXECUTE -> FINALIZE
```

The TRANSFORM phase applies parse-time transforms (stylesheet resolution, variable
expansion, and custom AST transforms) before validation runs.

**Why:** Placing transforms before validation ensures that validation sees the final,
expanded graph — not the template form with unexpanded variables or unresolved stylesheets.
This prevents spurious validation failures on legal pipeline patterns that are valid only
after expansion.

**Compatibility:** Pipeline authors who consume the execution lifecycle events or hook into
the lifecycle will see a new `TRANSFORM` phase event before `VALIDATE`. Pipelines that do
not hook into lifecycle events are unaffected.

---

## 6. Error Semantics: `RETURN Outcome(status=FAIL)` vs `RAISE`

**What:** Handler error paths use `RETURN Outcome(status=FAIL, ...)` rather than raising
exceptions. Unhandled exceptions in handler code are caught and wrapped into a `FAIL`
outcome with the exception message in `notes`.

**Why:** Exception propagation from a handler would terminate the entire pipeline rather
than routing through the graph's conditional edges. Returning a `FAIL` outcome preserves
the pipeline's ability to dispatch to a failure branch (e.g., a `condition="outcome=fail"`
edge to a recovery node or human gate). This is the behavior authors expect: a failed node
should trigger failure-path routing, not crash the pipeline.

**Compatibility:** This is an implementation detail of the engine. Pipeline authors observe
`FAIL` outcomes on handler errors regardless of whether the internal mechanism uses
exceptions or return values. Existing pipelines are unaffected.

---

## 7. `type` vs `node_type` Internal Naming

**What:** The externally visible DOT attribute name for the node handler type is `type`.
The engine may use an internal field named `node_type` to avoid reserved-word conflicts in
Python (where `type` is a built-in). Both names refer to the same concept; the external
behavior is identical.

**Why:** Python's `type` built-in creates naming conflicts in dataclasses and attribute
access. Using `node_type` internally avoids shadowing the built-in. The DOT attribute name
`type` remains canonical and externally visible.

**Compatibility:** Pipeline authors use `type=llm`, `type=parallel`, etc. in DOT source.
The internal renaming is invisible at the DOT level.

---

## 8. Per-Branch Session Isolation for Full-Fidelity Threading

**What:** Our implementation realizes the spec's §5.4 `full`-fidelity "reused session / same
thread" behavior via an internal `_session_pool` on the backend \u2014 an implementation construct
below the spec's `CodergenBackend` `run(node, prompt, context)` interface (the spec models no
session object). As of this change, when a node executes inside a **parallel branch**, its
session pool and completion-tracking state are **isolated per branch**: each branch runs on a
branch-scoped engine with a cloned backend. Concurrent branches no longer share session state.

**Why:** §3.8 mandates that "each parallel branch receives an isolated clone of the context."
Our `_session_pool` sits below the spec's abstraction, so the spec does not explicitly govern
it \u2014 but sharing it across concurrent branches violated the spec's isolation *intent* and our
own §4.12 handler-statelessness rule, producing silent non-deterministic cross-branch
contamination under `fidelity=full`. Per-branch isolation extends the spec's isolation intent
down to our session-pool layer.

**Compatibility:** Fully backward-compatible. Sequential pipelines and parallel pipelines
without nested stateful codegen see no change. No spec-conformant `.dot` file can depend on
cross-branch session sharing, because the spec never defines that behavior \u2014 it defines the
opposite (§3.8 isolation). This change moves observable behavior toward what a conforming
pipeline already assumes.

---

## 9. Same `thread_id` Across Concurrent Branches Resolves to Isolation

**What:** The spec contains an unresolved interaction: §5.4 thread-resolution says nodes
sharing a `thread_id` "reuse the same LLM session," while §3.8 says parallel branches must be
isolated. When the **same explicit `thread_id` appears on nodes in two different concurrent
parallel branches**, these two rules conflict. Our implementation resolves this by giving
**§3.8 (branch isolation) precedence**: each branch's nodes get an isolated session even if
they carry an identical `thread_id` to a sibling branch's nodes. Thread-id-based session reuse
continues to work normally for the **sequential** case (nodes in the same linear path).

**Why:** §3.8's isolation mandate is the stronger, more consistent guarantee; a shared LLM
session across concurrent branches is precisely the contamination this change eliminates.
"Isolate by default" is the safe, deterministic resolution of a spec self-contradiction.

**Compatibility:** Backward-compatible for all spec-conformant pipelines except the narrow,
spec-self-contradictory case of an author deliberately placing the same `thread_id` on nodes
in different concurrent branches expecting them to share one session \u2014 a behavior the spec
never coherently defines. Such a pipeline relies on undefined/contradictory behavior; we make
the resolution explicit and deterministic here.

---

## 10. `shape=folder` / `dot_file=` Sub-Pipeline Nodes

**What:** We support a sub-pipeline node declared via `shape=folder` with a `dot_file=`
attribute, which runs an entire child `.dot` graph as a single node's execution. The spec
describes sub-pipeline composition as a *pattern* (§9.4 \u2014 "a node whose handler runs an entire
sub-graph as its execution," with the manager loop named as the example) but does not define a
dedicated `shape=folder` shape or `dot_file=` attribute for it.

**Why:** A first-class folder/sub-pipeline node is ergonomic for composing pipelines from
reusable `.dot` fragments without the manager-loop supervisor machinery. It implements the
spec's §9.4 sub-pipeline pattern with a dedicated, declarative shape.

**Compatibility:** Additive and non-shadowing. `folder` is not a spec-assigned shape in the
§2.8 shape\u2192handler table, and `dot_file` does not collide with any spec-defined attribute
name, so the mechanism cannot change the behavior of any spec-conformant `.dot` file.
(Documenting a pre-existing extension that was previously undocumented.)

---

## 11. Sub-Pipeline and Manager-Child Execution Is a Fresh Session Boundary

**What:** Same-`thread_id` LLM session continuity (§5.4 thread resolution) applies WITHIN a
single graph traversal. It does NOT cross a sub-pipeline boundary: a node inside a
`shape=folder` / `dot_file=` sub-pipeline (§9.4) or a manager-loop child dotfile (§4.11) runs
as a separate child graph/engine and starts a fresh LLM session, even if it carries the same
`thread_id` as a node in the parent graph. Session continuity for a shared `thread_id` holds
for inline nodes and flattened DOT `subgraph cluster_*` blocks (which §11.1 flattens into the
same graph), but not across a child-graph execution boundary.

**Why:** The spec frames sessions as run-local and non-serializable (§5.3: "in-memory LLM
sessions cannot be serialized"; §3.1 finalize closes sessions), the thread-resolution ladder
is graph-scoped (§5.4, tier 3 is "graph-level default thread"), and §9.4 defines a
sub-pipeline as "a node whose handler runs an entire sub-graph as its execution" — a separate
execution unit. This matches the subagent model (coding-agent-loop §7.1: a child session "runs
its own agentic loop with its own conversation history but shares the parent's execution
environment"). Our implementation makes this concrete: a sub-pipeline / manager child runs on
a child engine with its own session pool. The spec does not explicitly state cross-sub-pipeline
continuity either way; we adopt "fresh boundary" as the deterministic, spec-intent-aligned
choice, consistent with the per-branch isolation decisions in sections 8 and 9.

**Compatibility:** Backward-compatible. No spec-conformant `.dot` can depend on
cross-sub-pipeline session continuity, because the spec never promises it and the surrounding
normative clauses (§5.3, §5.4, §9.4) indicate the opposite. Authors who need a node to continue
a shared-`thread_id` session must keep it inline in the same graph (or in a flattened cluster),
not behind a sub-pipeline / folder / manager-child boundary.
