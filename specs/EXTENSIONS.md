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

> **Implementation note:** `_session_pool` was superseded by `_thread_transcripts` (see §12–13); the per-branch isolation semantics described here remain in effect.

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

---

## 12. `fidelity=full` Continuity Is Realized via `parent_messages` at Node-Exchange Granularity

**What:** The spec's §5.4 `full`-fidelity "reuse the same session / full history preserved"
requirement is realized in our implementation by a backend-held message-list carrier injected
into each subsequent same-thread spawn via the `parent_messages` mechanism (foundation
`_prepared.py` §4.5 leave-open). The carrier holds **node-exchange granularity**: one
`(role=user, content=instruction)` + `(role=assistant, content=final_output)` pair per `full`
node. The child agent's inner tool-loop turns are **not** included — only the conversation
*between* nodes is preserved, not the child's internal reasoning.

**Why:** The spec's §5.4 language ("reuse the same LLM session", "full history preserved") is
written as a *behavior specification*, not a mechanism mandate. The spec separately notes
(§5.3) that sessions are in-memory and non-serializable, and unified-llm §2.6 models the LLM
client as stateless (continuity = caller-passed message list). Our realization of §5.4 using
`parent_messages` is mechanism-not-policy: the spec's §4.5 CodergenBackend interface is
silent on how continuity is achieved, leaving this to the app layer. Node-exchange granularity
(instruction + final output) was accepted as the meaning of `full` at the backend layer — the
spawn result exposes only `output` + `session_id`, not inner tool-loop turns, so inner-turn
fidelity across nodes is architecturally inaccessible at this layer.

**Compatibility:** Additive and non-breaking. Prior behavior (sub_session_id re-pass) was
silently broken — it never preserved history because session_id is an identity/trace token,
not a history pointer. This change restores the spec-mandated behavior. No spec-conformant
`.dot` file can depend on the broken non-continuity.

---

## 13. `thread_id` Is Branch-Local — Same `thread_id` in Sibling Branches Does Not Join Conversations

**What:** `fidelity=full` session continuity (§5.4 thread resolution) is *branch-local*: the
backend's `_thread_transcripts` carrier is reset to `{}` when a backend is cloned for a
parallel branch (`clone()`). Two sibling branches that both carry an explicit `thread_id`
**do not share conversation history** — each branch accumulates its own independent
transcript. Thread-id-based history continuity operates only within a single linear path
(i.e., a single branch's sequential execution).

**Why:** This resolves the same §5.4 vs §3.8 spec conflict addressed in §9 (per-branch
session-pool isolation): §3.8 isolation (each parallel branch receives an independent clone)
takes precedence over §5.4 thread-id-based reuse when the two rules conflict. Isolation is
the deterministic, safe resolution — a shared conversation across concurrent branches is
precisely the cross-contamination the per-branch isolation design eliminates. The transcript
isolation is a natural consequence of the backend clone resetting mutable state.

**Compatibility:** Backward-compatible. The prior implementation was broken for cross-node
continuity regardless of branching, so no existing pipeline could have been relying on
cross-branch conversation sharing. Authors who intend a shared thread to carry history across
nodes must place those nodes in the same sequential path (not in sibling parallel branches).

---

## 14. `allow_partial` Applies on Node Timeout, Not Only Retry Exhaustion

**What:** The canonical spec scopes `allow_partial` (§2.6) to a single trigger: "Accept
PARTIAL_SUCCESS when retries are exhausted instead of failing" (§5.2 retry pseudocode). We
extend it to a second trigger: when a node with `allow_partial` set exceeds its `timeout`
(§2.6), the engine yields `PARTIAL_SUCCESS` instead of `FAIL`. Because `PARTIAL_SUCCESS` is
success-class for routing (§5.2), the graph continues along the timed-out node's unconditional
edge rather than terminating the run. Without `allow_partial`, a timeout still produces `FAIL`
and flows through normal failure routing (§3.7) — unchanged.

**Why:** For iterative loops (a node meant to make incremental progress across many
executions, with progress recorded in context/files), a single slow iteration hitting its
timeout would otherwise tear down the entire run via §3.7 termination. `allow_partial` is the
author's explicit opt-in that an incomplete-but-progressing node is "good enough to proceed" —
the same intent the spec already honors for retry exhaustion and that §4 honors for goal gates.
Applying it on the timeout path extends that intent to the one other place a node can fail to
fully complete. The behavior is gated entirely behind the opt-in attribute; nodes without it
see no change.

**Note on attribute spelling:** This extension also corrects a string-vs-bool defect at the
`allow_partial` call sites. The DOT parser coerces *unquoted* `allow_partial=true` to bool
`True` but leaves *quoted* `allow_partial="true"` as the string `"true"`; the call sites
previously tested `attrs.get("allow_partial") is True`, which never matched the quoted form —
so `allow_partial` was inert for the common quoted spelling on both the retry-exhaustion and
timeout paths. Both call sites now accept bool `True` or the string `"true"`, so both DOT
spellings behave identically (consistent with extension §1, BareValue, where quoted and
unquoted values are equivalent).

**Compatibility:** Fully backward-compatible. Nodes without `allow_partial` are unaffected
(timeout still routes via §3.7). Nodes that set it now continue past a timeout where they
previously terminated the run — moving observable behavior toward the author's stated intent.
No spec-conformant `.dot` file can depend on the prior "single timeout kills the graph despite
`allow_partial`" behavior, since that was the defect this corrects.

---

## 15. `max_pipeline_duration` Graph-Level Wall-Clock Timeout

**What:** A graph-level attribute `max_pipeline_duration` (integer, milliseconds) that is NOT
defined in the upstream attractor nlspec. When set, the engine checks elapsed wall-clock time
before each step; if the elapsed time exceeds `max_pipeline_duration`, the pipeline terminates
immediately with `status=FAIL` and `failure_reason="max_pipeline_duration_exceeded"`.

**Why:** The upstream spec's step-count ceiling (`max_steps`) guards against infinite loops but
does not bound wall-clock time. Long-running nodes (network calls, LLM invocations) can stall
a pipeline for an unbounded duration even within the step ceiling. `max_pipeline_duration`
provides an independent wall-clock safety bound that is orthogonal to step count and useful
for production deployments with SLA requirements.

**Behavior:**
- Checked before each step in the main execution loop (Step 0 in the engine's step dispatch).
- Measured via `time.monotonic()` (elapsed milliseconds since pipeline start).
- Terminates the pipeline without executing the current step if the limit is exceeded.
- The termination outcome carries `failure_reason="max_pipeline_duration_exceeded"` and a
  human-readable `notes` message showing the configured limit.

**Implementation locations:**
- `engine.py:280–291` — enforcement logic (Step 0 duration check)
- `graph.py:301` — `max_pipeline_duration: int | None` field on the `Graph` model (milliseconds)
- `dot_parser.py:444` — promotes the DOT graph-block attribute to `graph_fields`, coercing to `int`

**Compatibility:** Additive. Pipelines that do not set `max_pipeline_duration` are unaffected
(the attribute defaults to `None` and the check is skipped). The attribute name does not collide
with any upstream spec-defined graph attribute.

---

## 16. Fail-Fast Edge Routing with `runs_on` / `continue_on_fail`

**What:** On a node `FAIL` outcome, unconditional out-edges are followed only if the target
node declares `runs_on` ∈ {`always`, `failure`}; otherwise routing stops (fail-fast). The
`continue_on_fail` attribute opts a node out of fail-fast propagation. Canonical §3.3 step 4
selects the highest-weight unconditional edge regardless of outcome status.

**Why:** Fail-fast prevents a failed stage from silently feeding garbage into downstream work.
Cleanup/notification nodes can still run via `runs_on=always|failure`. This is the engine's
"fail loud, don't proceed in a lesser state" stance.

**Compatibility:** Pipelines with no failures behave identically to canonical. Pipelines that
relied on canonical "continue past FAIL on the best unconditional edge" must add
`runs_on=always` (or `continue_on_fail`) to the intended successor.

## 17. Node I/O Contracts: `requires=` / `outputs=` with Skip Propagation

**What:** Nodes may declare `requires=<keys>` and `outputs=<keys>`. A node whose required
inputs are absent (e.g. produced by a skipped/failed upstream node) is itself skipped, emitting
`PIPELINE_NODE_SKIPPED`; a node that completes without producing its declared `outputs` emits
`PIPELINE_NODE_CONTRACT_VIOLATION`. Skips propagate along the dependency chain.

**Why:** Makes data dependencies explicit and turns "ran but produced nothing useful" into a
loud, observable event rather than a silent downstream failure.

**Compatibility:** Additive — nodes that declare neither attribute are unaffected.

## 18. Parallel Join Policies Beyond Canonical: `k_of_n` / `quorum` / `error_policy`

**What:** `shape=parallel` fan-out supports join policies beyond canonical `wait_all` /
`first_success`: `k_of_n` (proceed when k branches succeed), `quorum`, and a configurable
`error_policy` governing how branch errors affect the join.

**Why:** Real fan-out workloads need partial-completion semantics (e.g. "best 3 of 5 drafts")
without hand-rolling them in conditions.

**Compatibility:** Additive — default join behavior matches canonical `wait_all`.

## 19. `wait.human` `freeform` Mode and Attachments

**What:** The human-gate node supports a `freeform` response mode (open text, not only
accelerator-key choices) and file attachments alongside the human's response.

**Why:** Review gates often need a paragraph of guidance or a file, not just an approve/reject
keypress.

**Compatibility:** Additive — accelerator-key gates behave as in canonical.

## 20. Tool Node: `parse_json`, `tool_env`, `tool.last_line`

**What:** `shape=tool` nodes support `parse_json` (parse stdout as JSON into context),
`tool_env` (inject env vars for the command), and expose `tool.last_line` as a routing key in
addition to `tool.output`.

**Why:** Tools that emit JSON or a terminal status line are common; routing on `last_line`
avoids brittle full-stdout matching (the canonical "prose-vs-JSON" hazard).

**Compatibility:** Additive — `tool.output` and existing tool routing are unchanged.

## 21. Variable Expansion Beyond `$goal`: `$param` and `${key}`

**What:** Prompt/attribute substitution supports `$param` and `${key}` forms in addition to the
canonical `$goal`, resolving against pipeline context. Substitution remains simple string
replacement, not a templating engine (consistent with canonical §4.5).

**Why:** Pipelines need to thread context values (not just the goal) into prompts without a
full template language.

**Compatibility:** Additive — `$goal` behaves as in canonical; literals without `$`/`${` are
untouched.

## 22. `outcome=` Condition Resolves to `preferred_label` First

**What:** In edge conditions, the `outcome` key resolves to `outcome.preferred_label` when set
(via `report_outcome`), falling back to `outcome.status`. Canonical §10.4 defines `outcome` as
`outcome.status` only, with `preferred_label` as a separate key.

**Why:** Lets a node steer its own routing by emitting a `preferred_label` through
`report_outcome`, which is load-bearing for outcome-driven pipelines.

**Compatibility:** **Not behavior-neutral.** A canonical pipeline matching `outcome=<status>`
still works *unless* a node also sets a `preferred_label`, in which case `outcome=` matches the
label. Pipelines needing strict status matching should branch on the explicit status value.
Tracked as gap `ATX-5` in `SPEC_CONFORMANCE.md`.

---

## 23. `response_schema` Node Attribute (Structured Output)

> **This extension is NOT in the canonical attractor spec.** Canonical §4.5 explicitly keeps
> output format at the backend layer, outside the DOT pipeline language. This attribute is an
> additive, backward-compatible extension that is safe to ignore by spec-conformant backends
> that do not support it.

**What:** A node may carry a `response_schema` DOT attribute that declares a JSON Schema object
for its LLM response. When set, the pipeline engine passes a `ResponseFormat(type="json_schema",
json_schema=<schema>, strict=True)` to the `unified-llm-client`'s `generate()` call, requesting
provider-native structured output. The raw JSON text returned by the LLM is stored as the node's
output (`outcome.notes`) and the parsed object is also stashed in pipeline context under the node
ID for downstream use.

**Value forms — either of:**

- **Inline JSON object** (trimmed value starts with `{`): the attribute value is parsed directly
  as a JSON object. Example:
  ```dot
  extract [response_schema="{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}}}"]
  ```
- **File path** (any other value): resolved relative to the `.dot` file's directory (or the
  current working directory if the graph was loaded from an inline string). The file must contain
  a valid JSON object. Example:
  ```dot
  extract [response_schema="schemas/person.json"]
  ```

**Fail-loud:** If the value is neither valid inline JSON nor a readable file containing a valid
JSON object, `apply_transforms()` raises `ValueError` immediately with a clear message — no
silent skip, no proceeding without a resolved schema.

**Provider threading:** The resolved schema is passed as `ResponseFormat(type="json_schema",
json_schema=schema, strict=True)` to `unified_llm.generate()`. Provider mapping is handled by
the `unified-llm-client` library:
- OpenAI: native `response_format` JSON Schema mode.
- Gemini: `response_mime_type="application/json"` + `response_schema`.
- Anthropic: tool-extraction technique (the library synthesizes a `__structured_output__` tool).

**Spawn path limitation:** `response_schema` is **only** supported when the node executes via the
direct-LLM path (`AmplifierBackend` Path B or `DirectProviderBackend`). If a node with
`response_schema` routes through `AmplifierBackend._run_with_spawn` (Path A — full child
session), the engine returns `Outcome(FAIL)` with a clear message:
_"response\_schema is only supported on direct-LLM nodes (not spawned-agent nodes) yet."_

**Downstream:** The structured JSON string is set as `outcome.notes`. The parsed object (when
JSON is valid) is stored in pipeline context as `context[node.id]` for downstream nodes to
reference via `${node_id}` substitution or direct context lookup.

**Compatibility:** Fully additive and backward-compatible. Nodes without `response_schema` are
unaffected. Existing `.dot` files work without modification. Canonical spec-conformant backends
that do not read `response_schema` will silently treat it as an unknown attribute (per the
existing unknown-attr passthrough behaviour of `dot_parser.py::_apply_node`).

---

## 24. Convergence Observability: Per-Iteration Records, `$iteration` Substitution, and `trace.jsonl`

> **This extension is NOT in the canonical attractor spec.** The canonical spec defines the run
> directory layout (Appendix C / Section 5.6) but does not specify per-iteration sub-directories
> or a `trace.jsonl` descent curve. This extension is additive and backward-compatible.

**What:** Three coordinated additions that make the attractor convergence claim measurable:

1. **Per-iteration node records** — on each `loop_restart` edge traversal the engine creates
   `logs_root/iteration_N/<node_id>/status.json` alongside the existing flat
   `logs_root/<node_id>/status.json` (backward compat). Each iteration's records are preserved;
   a 10-iteration run yields 10 complete per-iteration snapshots, none overwritten.

2. **`$iteration` / `$loop_count` context keys** — the engine seeds `iteration` and `loop_count`
   into `PipelineContext` at pipeline start (value `"0"`) and increments both on every
   `loop_restart`. Because the substitution machinery (`substitution.py`) already expands `$key`
   from context, `$iteration` and `$loop_count` are immediately usable in `prompt` attributes
   and `tool_command` strings without any additional wiring.

3. **`trace.jsonl` descent curve** — the engine appends one JSONL record to
   `logs_root/trace.jsonl` after every node completion (including skipped nodes). Record shape:
   ```json
   {"iteration": 0, "node_id": "work", "status": "success", "preferred_label": "go",
    "duration_ms": 42.1, "ts": "2024-01-01T00:00:00+00:00"}
   ```
   The file is append-only and engine-written (not hook-derived), so it survives without any
   hook configured. Reading it across all iterations gives the descent curve: gate signals and
   durations per node per iteration.

4. **`attractor trace <run-dir>` CLI subcommand** — reads `trace.jsonl` and prints a
   human-readable summary of iterations, nodes, statuses, and durations. Exits 0 even if no
   `trace.jsonl` exists (run directories that predate this extension).

**Why:** The attractor claim is *convergence*: work descends toward a verified sink. Without
per-iteration records, "converged" and "got lucky once" are indistinguishable. `trace.jsonl` is
the empirical form of the convergence claim — a descent curve that can be plotted, compared
across runs, and used as evidence in evals. `$iteration` lets pipeline authors write prompts
that reference the current iteration number (e.g. "This is attempt $iteration — previous
attempts failed because…").

**Canonical spec note:** The canonical spec should gain matching vocabulary for per-iteration
run directories and a trace artifact in the Appendix C run-directory layout section. Until then,
this extension documents the behavior here.

**Backward compatibility:** Fully additive.
- Existing pipelines that do not use `loop_restart` see no change (iteration stays `"0"` and
  `trace.jsonl` records only that single pass).
- The flat `logs_root/<node_id>/status.json` path is preserved alongside the new
  `iteration_N/<node_id>/status.json` path — existing consumers that read the flat path are
  unaffected.
- `$iteration` and `$loop_count` in context are new keys; existing pipelines that happen to
  use those names as their own context keys will see them overwritten at pipeline start and on
  each `loop_restart`. Pipeline authors should treat `iteration` and `loop_count` as reserved
  context key names going forward.

**Implementation locations:**
- `engine.py: _initialize_context()` — seeds `iteration` and `loop_count` to `"0"` at start
- `engine.py: run() Step 6 (loop_restart)` — increments and re-seeds both keys on restart
- `engine.py: _write_node_status()` — writes iteration-scoped path and appends to `trace.jsonl`
- `modules/pipeline-runner/amplifier_module_pipeline_runner/cli.py: cmd_trace()` — trace subcommand

---

## 25. Fail-Closed Goal-Gate Outcomes

> **This extension DIVERGES from canonical spec §4.5.** The canonical spec pseudocode (§4.5,
> `CodergenHandler`) returns `Outcome(status=SUCCESS, notes="Stage completed: …")` unconditionally
> for any non-empty string response. This extension changes that behavior for `goal_gate=true`
> nodes. See walk-upstream note in `PRINCIPLES.md`.

### Incident motivation

2026-07-28: a 20-node pipeline ran 2.4 hours via the standalone attractor CLI and exited
`status=success` with zero work product. The convergence judge node (marked `goal_gate=true`)
wrote "NOT CONVERGED — 2 of 7 criteria pass. The networking implementation does not work and
the harness was never created." — and was recorded `outcome=success` because `_parse_outcome`'s
final fallback converted the plain-prose response to SUCCESS. The designed replan loop
(`max_retries=4, retry_target=analyze_plan`) never fired. This extension closes that class of
false success.

### What the canonical spec says

Canonical spec §4.5 `CodergenHandler` pseudocode:

```
any string response → write response.md → Outcome(status=SUCCESS, notes="Stage completed: …")
```

This is unconditional: even a response that literally says "NOT CONVERGED" is recorded as
SUCCESS. The spec is fail-open by design (it assumes the node's prose is advisory and routing
is the caller's responsibility).

### What this extension does instead

**Scope decision: goal_gate=true nodes only.** A global default flip to RETRY/FAIL for all
plain-text responses would break nearly every existing pipeline (most `box` nodes in tutorials
and examples end in prose — see backward-compat inventory below). The fail-closed contract
applies only when the node carries `goal_gate=true`, which already signals that the node's
outcome is load-bearing for pipeline exit.

**The verdict-recovery ladder is preserved.** `_parse_outcome` already tries (in order):
1. Fenced JSON (` ```json … ``` `) → strip fence, parse as JSON
2. Pure JSON (`stripped.startswith("{")`) → parse, honor `status` field
3. Embedded verdict recovery → find last balanced `{…}` in prose, parse if it carries `status`

The fail-closed rule sits **below** this ladder. Only when every recovery attempt has failed
(the output is genuinely plain prose with no parseable verdict) does the fail-closed rule fire.
Judges that emit prose + trailing JSON verdicts keep working via path 3.

**Status choice: RETRY, not FAIL.** FAIL is fail-fast — it does not traverse plain edges
(EXTENSIONS.md §16; `edge_selection.py:79-101`). A naive FAIL default would convert observer/
reporter nodes with only plain out-edges into hard stops. RETRY respects `max_retries` (then
degrades to FAIL) and is the appropriate signal for "try again with an explicit verdict." When
`max_retries=0`, RETRY degrades immediately to FAIL at the goal-gate check.

**`is_explicit` field on `Outcome`.** A new `is_explicit: bool` field (default `False`)
distinguishes an asserted verdict from a defaulted one. `is_explicit=True` is set by every
producer with an unambiguous verdict mechanism:

- `report_outcome` tool call (tool-loop, direct-provider, and spawn paths)
- pure JSON / fenced JSON / recovered embedded JSON verdicts (`_parse_outcome`)
- a tool (parallelogram) node's exit code — 0 is an explicit success, nonzero an explicit
  fail (`handlers/tool.py`); the exit code IS the verdict
- **verdict-shaped** `response_schema` structured output — a captured `report_outcome`
  call or a `status` field with a recognized value (see policy decision below)
- deterministic handler verdicts that cannot be LLM-defaulted: human-gate selections and
  freeform input (`handlers/human.py`), structural no-op SUCCESS (`start`/`exit`/
  `conditional` handlers), fan-in ranking (`handlers/fan_in.py`), and parallel join-policy
  aggregation (`handlers/parallel.py`)

`is_explicit=False` marks defaulted statuses: the plain-prose fallback, empty-response
defaults, a spawn wrapper's status-only completion, **non-verdict** structured output
(parseable or not — format is not a verdict), and config/environment failures (timeout,
missing `tool_command`, handler exception) where no verdict was produced.

**Enforcement is two-layer (belt and suspenders).**

1. *Parser layer:* `_parse_outcome` returns RETRY (not SUCCESS) for plain-prose goal_gate
   responses, so retry machinery fires at the node.
2. *Gate layer (centralized):* `_check_goal_gates()` treats a gate as satisfied only when
   `outcome.is_success AND outcome.is_explicit` (`engine.py`). This closes bypass paths that
   never reach the parser's plain-prose rung — notably the spawn path's status-only SUCCESS
   and unparseable structured output.

`is_explicit` is therefore load-bearing at the gate check, not just observability metadata.
Any new Outcome producer must classify itself: set `is_explicit=True` iff the status comes
from an unambiguous verdict mechanism.

**`response_schema` policy decision (corrected in independent review round 2).**
**Format ≠ verdict.** Parseable schema output proves the model followed the requested
FORMAT; it does not prove the node asserted a VERDICT. Schema-parsed output is explicit
ONLY when it carries a recognized verdict, routed through the same verdict ladder as every
other path: a captured `report_outcome` tool call (authoritative), or a `status` field
whose value is a recognized StageStatus. Generic structured output — `{"name": "Alice"}`,
`{"assessment": "NOT CONVERGED"}` — stays DERIVED (`is_explicit=False`): the node still
returns SUCCESS (ordinary schema nodes are unchanged), but a `goal_gate=true` schema node
cannot satisfy its gate with it. The original round-1 policy ("parseable schema output IS
explicit") was a false-success side door: a goal_gate structured-output judge returning
`{"assessment": "NOT CONVERGED"}` — or a name-extraction payload — would have shipped
success. Both structured-output paths (`backend.py` tool-loop and
`DirectProviderBackend.run()`) share one classifier
(`backend._outcome_from_structured_output`); empty or unparseable schema output also
stays `is_explicit=False`, so a goal_gate schema node fails closed in every non-verdict
case.

**CodergenHandler string path.** When a backend returns a raw string (the spec §4.5
`CodergenHandler` path — exercised by simple/custom backends and test doubles), a
`goal_gate=true` node's string is routed through the verdict-recovery ladder
(`_parse_outcome`): JSON verdicts are honored, plain prose returns RETRY. This implements in
our own handler the exact goal_gate check the walk-upstream note recommends for the spec.
Non-goal_gate string responses keep the unconditional-SUCCESS wrap (spec §4.5 preserved).

**Spawn-path consistency.** `_outcome_from_spawn_result()` returns `is_explicit=False` when
recovering from the orchestrator's completion status alone (no `report_outcome`, no JSON). A
goal_gate child that produces no final text and no report_outcome cannot satisfy its gate via
the spawn wrapper's status field alone — the gate layer rejects it.

### Backward-compat inventory

**Producer classification (every Outcome-producing path that can reach a goal-gate check):**

| Producer | Verdict mechanism | `is_explicit` |
|---|---|---|
| `report_outcome` tool call (tool-loop / direct-provider / spawn metadata) | asserted by node | `True` |
| Pure / fenced / embedded JSON verdict (`_parse_outcome`) | asserted by node | `True` |
| Tool node exit code (`handlers/tool.py`) — 0 and nonzero | process exit code | `True` |
| `response_schema` output carrying a verdict — captured `report_outcome`, or `status` field with a recognized value (`backend._outcome_from_structured_output`) | verdict via the standard ladder | `True` |
| `response_schema` output WITHOUT a verdict — generic data such as `{"name": "Alice"}`; also empty/unparseable | format only, no verdict | `False` (gate fails closed) |
| Plain-prose fallback (`_parse_outcome`) | none — defaulted | `False` (+ RETRY for goal_gate) |
| Codergen string-wrap for non-goal_gate nodes (spec §4.5) | none — defaulted | `False` (not gate-relevant) |
| Spawn status-only completion (`_outcome_from_spawn_result`) | wrapper status, not node verdict | `False` (gate fails closed) |
| Empty response (any path) | none | `False` (FAIL) |
| Tool timeout / missing `tool_command` / handler exception | environment failure | `False` (FAIL — not gate-relevant) |
| Human gate selection / freeform input (`handlers/human.py`) | deterministic human action — cannot be LLM-defaulted | `True` |
| Human gate SKIPPED (`handlers/human.py`) | deterministic interviewer decision | `True` (FAIL) |
| Start / Exit / Conditional structural no-ops | deterministic structural SUCCESS — no LLM in the loop | `True` |
| Fan-in ranking verdict (`handlers/fan_in.py`) | deterministic aggregation over branch statuses | `True` |
| Fan-in with no `parallel.results` (`handlers/fan_in.py`) | environment/wiring failure | `False` (FAIL) |
| Parallel join-policy verdict, incl. no-branch SUCCESS (`handlers/parallel.py`) | deterministic counting rule over branch statuses | `True` |
| Parallel branch exception / missing engine (`handlers/parallel.py`) | environment failure | `False` (FAIL) |
| Manager-loop stop/guard completion (`handlers/manager_loop.py`) | the child's verdict | propagates child's `is_explicit` |
| Manager-loop cycle exhaustion / config failure (`handlers/manager_loop.py`) | environment failure | `False` (FAIL) |
| Folder / pipeline (subgraph) node (`handlers/pipeline.py`) | child pipeline's terminal outcome — CAN carry a defaulted LLM completion | propagates child's `is_explicit` (outcome returned verbatim) |

**Shipped examples with `goal_gate=true` nodes (complete sweep of `examples/`):**

| File | Gate node(s) | Behavior delta |
|---|---|---|
| `examples/patterns/task-runner.dot` | `verify`, `verdict` (parallelogram tool gates) | **None** — tool exit codes are explicit verdicts; gates satisfied on exit 0 exactly as before |
| `examples/pipelines/practical/feature-build.dot` | `integration_test` (LLM, retry_target=self) | Plain-prose completion now RETRYs instead of silently satisfying the gate |
| `examples/pipelines/02-plan-implement-test.dot` | `implement` (LLM) | Same — explicit verdict (report_outcome / JSON) now required |
| `examples/pipelines/04-retry-with-fallback.dot` | `implement`, `simple_implement` (LLM) | Same |
| `examples/pipelines/10-full-attractor.dot` | `implement_backend`, `implement_frontend` (LLM) | Same |
| `examples/pipelines/practical/pr-review.dot` | `generate_comments` (LLM, no retry_target) | Same; with no retry_target an unsatisfied gate ends the pipeline FAIL (see hazard note) |
| `examples/pipelines/practical/multi-lens-review.dot` | `synthesize` (LLM, no retry_target) | Same |
| `examples/pipelines/practical/refactor.dot` | `snapshot_tests` (LLM, retry_target=self) | Same |
| `examples/pipelines/practical/test-gen.dot` | `write_tests` (LLM, retry_target) | Same |

For the LLM gate nodes above this is the intended breaking change: in the default Amplifier
backend the child session has the `report_outcome` tool available and is prompted to use it;
completions that end in bare prose now RETRY (then degrade to FAIL) instead of silently
recording success — which is the incident class this extension closes.

**Tests affected and updated in this change:**

| Test | Why affected | Resolution |
|---|---|---|
| `test_goal_gate_retry_clears_failures.py` (3 tests, tool-node gates) | tool exits lacked `is_explicit` | fixed by `handlers/tool.py` (exit codes are explicit) |
| `test_pipeline_e2e.py::TestGoalGate::test_success_with_satisfied_gate` | `SuccessBackend` returned plain prose | `SuccessBackend` now returns a pure-JSON verdict |
| `test_pipeline_e2e.py::TestGoalGate::test_retry_on_unsatisfied_gate` | `RetryThenSucceedBackend` Outcome lacked `is_explicit` | double now sets `is_explicit=True` |
| `test_pipeline_e2e.py::TestSpecSmokeTest::test_step3_execute` | `SuccessBackend` plain prose on `goal_gate` node | same `SuccessBackend` fix |
| `tests/test_backend.py:576` `test_backend_plain_text_returns_success` | tests a **non**-goal_gate node | unaffected (plain prose → SUCCESS preserved) |
| `tests/test_backend.py:1096` `test_parse_outcome_plain_text_returns_success` | `_parse_outcome` with no `node` arg | unaffected |
| `tests/test_goal_gates.py` | MockBackend returns explicit Outcomes | updated with `is_explicit=True` on mock verdicts |

**Plain-edge silent-hard-stop hazard:** Observer and reporter nodes that have only plain
out-edges and no `goal_gate=true` are **unaffected** — they still get SUCCESS for plain prose
(spec §4.5 preserved). For `goal_gate=true` nodes with only plain out-edges, the RETRY status
will not traverse those edges (RETRY routes like FAIL for edge selection). Authors should
ensure goal_gate nodes have explicit `condition="outcome=fail"` or `retry_target` edges, or
use `report_outcome` / JSON verdicts to produce the expected routing signal. The lint sweep
(`test_examples_lint_clean.py`) and `dot_graph validate` catch isolated nodes and missing
fallback edges.

### Walk-upstream note

The canonical spec §4.5 default should change to: when a node carries `goal_gate=true`, a
plain-prose response (no JSON, no `report_outcome`) must NOT be recorded as SUCCESS. The
recommended upstream change is to add a `goal_gate` check in `CodergenHandler` before the
final SUCCESS fallback, returning RETRY (or FAIL with a clear message) instead. Until the
upstream spec adopts this, this extension documents the divergence.

### Implementation locations

- `backend.py: _parse_outcome()` — fail-closed rule at the final rung; `node` parameter added
- `backend.py: _outcome_from_spawn_result()` — status-only success is `is_explicit=False`
- `backend.py: _run_with_spawn()` — passes `node=node` to `_parse_outcome`
- `backend.py: _run_with_tool_loop()` — passes `node=node` to `_parse_outcome`; no-text path
  now returns FAIL for goal_gate nodes (consistent with spawn path's empty→FAIL); non-goal_gate
  no-text keeps the spec §4.5 SUCCESS default; structured-output path delegates to
  `_outcome_from_structured_output` (verdict-shaped → explicit; generic data → derived)
- `backend.py: _outcome_from_structured_output()` — the single structured-output classifier
  shared by both backends (format ≠ verdict; see policy decision above)
- `__init__.py: DirectProviderBackend.run()` — passes `node=node` to `_parse_outcome`; same
  no-text scoping; structured-output path delegates to the shared classifier
- `outcome.py: Outcome` — `is_explicit: bool = False` field added
- `handlers/tool.py: ToolHandler.execute()` — exit-code outcomes are explicit verdicts
  (`is_explicit=True` for both exit 0 → SUCCESS and nonzero → FAIL); timeout, missing
  `tool_command`, and handler exceptions remain non-explicit (no verdict was produced)
- `handlers/codergen.py: CodergenHandler.execute()` — goal_gate string responses are routed
  through `_parse_outcome` (verdict ladder + fail-closed); non-goal_gate string responses keep
  the spec §4.5 unconditional-SUCCESS wrap
- `handlers/human.py` — selections, freeform input, and SKIP are explicit (deterministic
  human/interviewer actions); a `goal_gate=true` human gate is satisfiable
- `handlers/start.py`, `handlers/exit.py`, `handlers/conditional.py` — structural no-op
  SUCCESS is explicit (deterministic, no LLM in the loop)
- `handlers/fan_in.py`, `handlers/parallel.py` — aggregation/join-policy verdicts are
  explicit (deterministic rules over branch statuses); wiring/environment failures stay
  non-explicit
- `handlers/manager_loop.py` — stop/guard completions propagate the child's `is_explicit`;
  exhaustion and config failures stay non-explicit
- `handlers/pipeline.py` — returns the child outcome verbatim, so the child's `is_explicit`
  propagates (a folder node's outcome CAN carry a defaulted LLM completion)
- `engine.py: _check_goal_gates()` — centralized gate enforcement:
  `gate_satisfied = outcome.is_success and outcome.is_explicit`. The gate DOES consult
  `is_explicit` directly; this is what closes the spawn status-only bypass and any future
  producer that forgets to classify itself
- `engine.py: _write_node_status()` and `handlers/codergen.py: _write_status()` —
  `is_explicit` is serialized into every `status.json` (flat + iteration-scoped) and every
  `trace.jsonl` record, making it durable audit data rather than an in-memory-only flag

---

## 26. Worker-Session Observability: Durable `response.md` + Real Session-Event Persistence

> **This extension is additive** — it implements the canonical spec's own run-dir layout
> contract (§5.6 requires per-node `prompt.md`/`response.md`) and adds worker-session event
> persistence the spec does not specify. It also FIXES a spec self-contradiction; see the
> walk-upstream note below.

### Incident motivation

Worker sessions inside a pipeline run were write-only compute: they thought, acted, and
vanished. Three separate post-mortems (one on the 2026-07-28 external incident that also
motivated §25, two on internal runs) all dead-ended on the same missing evidence:

- The node's full final response survived only as a ~200-char scrap
  (`notes="Plain text response: {output[:200]}"` / `last_response[:200]`), because the
  codergen handler wrote `response.md` only when the backend returned a *string* — and the
  production `AmplifierBackend` spawn path always returns an `Outcome`, early-returning past
  the write. Diagnostic analyses produced by pipeline nodes were cut off mid-sentence.
- The `session_id` recorded in `status.json` was a dangling pointer: no `events.jsonl` or
  `transcript.jsonl` existed anywhere on disk for spawned worker sessions (foundation's spawn
  path never persists; see walk-upstream note). "Which tools did the worker call?" — the
  first question of every wrong-but-plausible audit — was unanswerable.

### What this extension does

**1. Full-response durability (`Outcome.response_text` → `response.md`).**
`_parse_outcome()` (backend.py) now carries the verbatim child output on
`Outcome.response_text` — set on every return path, *before* any truncation. The codergen
handler writes it to `<stage_dir>/response.md` on the Outcome path, closing the early-return
gap. The field is a file-write concern only: it is NOT serialized into `status.json`,
`trace.jsonl`, or `context_updates`, and the ≤200-char `last_response` context truncation is
unchanged (context economy working as designed).

**2. `session_id` in the codergen early-writer.** The engine's status writers already
serialize `session_id`; the codergen handler's own `_write_status()` now does too, so the
Outcome path never leaves a status record without its join key.

**3. Real worker-session event persistence.** The worker's actual event stream is captured
and persisted per session:

- `amplifier_module_loop_pipeline.worker_observability` exposes a `ContextVar`
  (`current_worker_sessions_dir`); the codergen handler sets it to `<stage_dir>/sessions`
  for the duration of each backend call (try/finally-reset, task-local so parallel branches
  cannot cross-talk).
- `hooks-pipeline-observability` — already mounted into the parent session by the
  attractor-core behavior, and composed into **every spawned worker session** by
  `PreparedBundle.spawn`'s parent+child bundle composition — registers a
  `SessionEventPersister` that appends each received event to
  `<stage_dir>/sessions/<session_id>/events.jsonl`. The `session_id` comes from the event
  payload itself: the amplifier-core kernel merges it into every event via
  `hooks.set_default_fields` at session construction.
- Persisted events (curated for forensic value; streaming deltas excluded):
  `session:start`, `session:resume`, `session:end`, `prompt:submit`, `prompt:complete`,
  `tool:pre`, `tool:post`, `orchestrator:complete`. Record shape is the standard session
  observer shape — `{"event": <name>, "timestamp": <utc-iso>, "data": {...}}` — one JSON
  object per line, append-only.

These are the events the worker's own orchestrator/kernel emit as they happen (e.g.
loop-agent's `tool:pre`/`tool:post` at tool-execution time) — captured, not reconstructed.
An earlier design that fabricated a 3-event ledger after the child completed was rejected in
review: a synthetic record cannot answer "which tools did the worker call?" and amounts to a
second, invented session store.

### Forensic navigation contract

Starting from ONLY the run dir:

```
<logs_root>/<node_id>/status.json        → read "session_id"
<logs_root>/<node_id>/sessions/<session_id>/events.jsonl
                                         → the worker's real event stream
<logs_root>/<node_id>/response.md        → the worker's full final response
```

### Walk-upstream note: where persistence belongs

Session persistence is a *session* concern owned by amplifier-foundation: ordinary sessions
persist `events.jsonl`/`transcript.jsonl` under `~/.amplifier/projects/<project>/sessions/<id>/`
(`amplifier_foundation/session/store.py`, `finder.py`). On this stack that idiom never fires
for pipeline workers: `PreparedBundle.spawn()` has **zero persist call sites** —
`DEFAULT_SESSIONS_ROOT` is never written for spawned children; they are ephemeral by
construction. The right long-term home for worker persistence is therefore foundation's spawn
path (an upstream change to a different repo). Until that exists, this bundle captures the
real event stream at the seam it owns — the hooks module it already mounts into every worker
session — and persists it in the run dir, which is (a) the canonical pipeline-scoped forensic
record (`prompt.md`, `response.md`, `status.json` already live there) and (b) durable in
CI/sandbox environments where `$HOME` is ephemeral. Standard file name, standard record
shape, real events — pointers stay resolvable without inventing a parallel session store.

**Spec self-contradiction (flagged upstream):** canonical spec §5.6 *requires* per-node
`prompt.md` and `response.md` in the run-dir layout, and its conformance checklist asserts
`artifacts_exist(logs_root, <node>, ["prompt.md", "response.md", "status.json"])` — yet the
spec's own CodergenHandler pseudocode contains an
`IF result is an Outcome: write_status(stage_dir, result); RETURN result` early-return that
skips the `response.md` write for Outcome-returning backends. The shipped handler had
faithfully transcribed that self-contradiction. This extension implements the layout
contract; the spec's pseudocode should be corrected to match its own §5.6.

### Files touched

- `modules/loop-pipeline/amplifier_module_loop_pipeline/outcome.py` — `response_text` field
- `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py` — `_parse_outcome()`
  sets `response_text` on all return paths
- `modules/loop-pipeline/amplifier_module_loop_pipeline/worker_observability.py` — the
  ContextVar seam (new)
- `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/codergen.py` — Outcome-path
  `response.md` write, ContextVar set/reset, `session_id` in `_write_status()`
- `modules/hooks-pipeline-observability/amplifier_module_hooks_pipeline_observability/session_events.py`
  — `SessionEventPersister` (new), registered in the module's `mount()`

### Compatibility

Fully backward-compatible and fail-safe:

- Existing pipelines run unchanged; `last_response` truncation and spawn/continuity semantics
  (thread transcripts, CR-1 invariant) are untouched.
- Persistence degrades to a silent no-op at every missing seam: hooks module not mounted →
  no subscriber; loop-pipeline not importable in the mounting session → resolver returns
  `None`; ContextVar unset (session not spawned by a codergen node) → no write; event without
  `session_id` → skipped. Persister handlers never raise into the session.
- `response.md` is written only when `response_text` is present; infrastructure-failure
  Outcomes (no child output) skip it.

---

## Conformance Restoration Note (T0-4)

**What was retired:** An unledgered dialect where non-`shape=parallel`, non-component nodes
with two or more simultaneously-matching conditional outgoing edges fanned out to ALL matching
targets in parallel (via `select_all_matching_edges` → `_execute_parallel_fan_out`), then
required a fan-in node.  This behavior was never documented in this ledger.

**What was restored:** §3.3 single-edge selection — `best_by_weight_then_lexical(condition_matched)` —
is now the sole edge-selection path for non-`shape=parallel`, non-component nodes.  When
multiple conditional edges simultaneously match, the engine deterministically picks exactly one:
the highest-weight match, with lexical target-id tiebreak.

**What is unchanged:** `shape=parallel` fan-out (extension #18) and component-node parallelism
(ParallelHandler) are untouched.  These are spec-sanctioned explicit parallelism constructs.

**Walk-upstream note (PRINCIPLES.md):** This is a conformance restoration, not a new extension.
No spec change is needed.  The canonical spec at §3.3 already prescribes single-edge selection;
this implementation now fulfills it.  See `SPEC_CONFORMANCE.md` ATX-10 for the ledger entry.

**Compatibility-banner note:** The banner at the top of this ledger promises that community
`.dot` files written against the canonical spec continue to work without modification.  While
the multi-match dialect was live, that promise was compromised for any spec-conformant graph
in which two conditional edges could simultaneously match (the spec prescribes one deterministic
successor; the engine ran both).  With this restoration the engine's edge selection matches the
spec letter, and the banner is true again for edge selection.  Graphs that deliberately relied
on the retired dialect must express parallelism explicitly (`shape=component` or `shape=parallel`,
extension #18).
