# Attractor Extensions

Documented divergences and additions relative to the canonical attractor nlspec at
[github.com/strongdm/attractor](https://github.com/strongdm/attractor). The current
canonical snapshot lives at `specs/canonical/attractor-spec-canonical.md`.

**All extensions are backward-compatible with the canonical spec — community `.dot` files
written against the canonical spec should continue to work without modification.**

When in doubt about whether a behavior is spec-conformant, consult the canonical snapshot
before assuming it is a bug.

---

## Entry Format

Every entry below carries (or, for older entries, has been backfilled with) two mandatory
fields declared in the entry's banner blockquote (or immediately under the heading, for an
entry with no banner):

- **`depends-on: §NN`** (or `depends-on: none`) — the section number of any other entry in
  this file that the current one builds on. This file is a flat chronological list with no
  built-in dependency tracking; `depends-on` makes a stacked extension traceable to its base
  so a reader (or a future author) can see at a glance that entry N assumes entry M's
  behavior. An extension that depends on another entry which is itself an undecided or
  deferred divergence must say so here — don't let a later extension quietly stack on top of
  an open question for months without anyone noticing.
- **`upstream action:`** — **required whenever the entry's banner states the behavior
  DIVERGES from canonical spec** (not required for pure additions to spec-silent areas).
  The value must be one of:
  - a real link to the upstream PR or issue proposing the change at
    `strongdm/attractor` (e.g. `https://github.com/strongdm/attractor/pull/NN` or
    `.../issues/NN`), or
  - `deferred, reason: <one-line reason>, review-by: <YYYY-MM-DD>` — a concrete calendar
    date the deferral will be revisited, not a placeholder.

  **A non-date value for `review-by` ("eventually", "TBD", "soon", "when we get to it") is
  not a permitted value.** Prose promising an upstream proposal with no date and no link is
  how a divergence sits unreviewed for months; a date makes it someone's job on a specific
  day. When a `review-by` date passes without the proposal being filed, the entry must be
  revisited: either file it, or replace the date with a fresh one and a fresh reason.

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

**`dot_file=` path resolution:** A relative `dot_file=` value is resolved by
`resolve_dot_path()` (`handlers/pipeline.py`) against a **precedence chain**, not a search
path -- the first non-empty candidate wins, with no existence check:

1. **Absolute path** -- used as-is.
2. **`graph.source_dir`** -- the directory of the `.dot` file that produced the *current*
   graph (root or child).
3. **`context.target_dir`** -- the pipeline's working directory (`--cwd` on the standalone
   CLI; the mounted orchestrator has no equivalent and skips this tier).
4. **`os.getcwd()`** -- the process's current working directory, as a last resort.

Every **child** graph reached through a `shape=folder` node already gets its `source_dir`
set to its own `.dot` file's directory (`PipelineHandler.execute()`, step 5), so a
grandchild's relative `dot_file=` resolves beside the child regardless of where the root
came from. A **root** graph gets its `source_dir` seeded from the directory of the `.dot`
file passed to the entry point that invoked it -- the standalone CLI (`attractor run
<file>`), the mounted `PipelineOrchestrator` (a local `dot_file` in its config), and the
`run_pipeline` tool (a `dot_file` input, forwarded to the mounted orchestrator's spawned
child session as an explicit `source_dir` alongside the already-resolved DOT text) all seed
it this way. Only an **inline** DOT source (`--dot-source`, a `dot_source` config value, or
a `dot_source` tool input) has no backing file and therefore no directory to seed --
`source_dir` stays empty for that root.

**`context.target_dir` (`--cwd`) is an independent knob and does not shadow `source_dir`.**
It answers a different question -- where box/tool nodes write files and read relative
inputs at *runtime* -- while `source_dir` answers where the pipeline's own `.dot` tree lives
on disk. The precedence chain above means an explicitly-set `graph.source_dir` always wins
over `context.target_dir` for `dot_file=` resolution: pointing `--cwd` at a separate
workspace does not require flattening a multi-file pipeline into that workspace.

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
>
> **depends-on:** none
>
> **upstream action:** deferred, reason: the recommended upstream change (a `goal_gate` check
> in §4.5's `CodergenHandler` before the unconditional SUCCESS fallback) is straightforward to
> state, but we want the backward-compat inventory below to stay settled across at least one
> more consuming release before asking upstream to adopt a fail-closed default that every
> nlspec implementation would then inherit, review-by: 2026-09-05

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

## 27. `must_write=` Node Attribute — Fail-Closed Artifact Contract

> **This extension adds an artifact-contract enforcement check to the
> engine** (per retry attempt, plus a final post-override backstop).
> It does not conflict with the canonical spec; the spec is silent on
> per-node artifact contracts.  Nodes without `must_write=` are completely
> untouched (opt-in).

### Motivation

The same engine gap has been patched at the graph level repeatedly — every
guard hand-rolled after a live failure:

1. **pm_gate** (`examples/patterns/task-runner.dot`) — the postmortem node
   was observed returning SUCCESS without writing its report; a deterministic
   stub-guard gate now guarantees the file exists.
2. **Verdict gates counting absence as refusal** — in live runs of this
   pattern, a critique node silently ended on plain-text narration without
   writing its critique file; the deterministic verdict gate downstream
   (a grep against the missing file) counted the absence as a refusal, and
   a stall counter killed a run whose tree was ship-quality by direct
   re-verification.
3. **Historical postmortem stubs** — the same "completed without writing"
   shape observed before pm_gate existed.

A box node's contract is often "this file now exists with real content."  The
engine had no way to be told that.  `must_write=` puts the cheapest evidence
check (the artifact exists AND is fresh AND is non-trivial) where every graph
gets it for free, instead of every author rediscovering the trap live.

### What this extension does

A node may declare `must_write=<path>` as a node attribute.  After the handler
returns a non-FAIL outcome, the engine runs a three-axis post-execution check:

1. **Existence:** the file at `<path>` must exist.
2. **Freshness floor (REQUIRED):** `artifact.mtime > node_start_wall`
   (strictly greater than; `time.time()` snapshot taken immediately before the
   handler runs).  A pre-planted file whose mtime predates OR equals the node
   start time FAILS even if it has content — presence alone is exactly the hole
   this contract closes.  The equality case is rejected explicitly: an
   adversary (or a coarse-resolution filesystem) can set an artifact's mtime
   via `os.utime` to match the recorded start time, bypassing a `>=` check.
3. **Non-trivial:** the artifact must contain at least one non-whitespace byte.
   An empty file or a whitespace-only file does not satisfy the contract.

The check runs in two places:

1. **Per-attempt, inside the retry ladder** (`execute_with_retry`): a
   completed attempt (SUCCESS / PARTIAL_SUCCESS) that violates the contract
   consumes a retry attempt exactly like a RETRY outcome — the same shape as
   the fail-closed goal-gate verdict retries (§25).  When attempts are
   exhausted, the violation becomes a loud FAIL with a clear
   `failure_reason` naming the violated axis, and the node routes through
   its normal failure edges (`retry_target`, `condition="outcome=fail"`
   edges, etc.).
2. **As the engine's final backstop, after all outcome overrides**: the same
   check runs again AFTER the `auto_status` promotion and the
   `continue_on_fail` override, so no override can convert an
   artifact-contract violation into a silent success.

If the handler already returned FAIL, the check does not run (no
double-wrapping of failure reasons).

### Path resolution (DESIGN DECISION)

`must_write=` paths follow the same resolution rule as `requires=`:

- **Absolute paths** are used as-is.
- **Relative paths** are resolved against `context.target_dir` if set,
  falling back to `os.getcwd()`.

The task-runner invocation sets `--cwd <target_repo>` and `--param
target_dir=<target_repo>`, so `.ai/postmortem/report.md` in a postmortem node
resolves to `<target_repo>/.ai/postmortem/report.md` — which is the right
place.  Pipeline authors must document which cwd is the anchor in their graph's
invocation comments to avoid the environment-lies class at the contract layer.

### Non-trivial semantics (DESIGN DECISION)

"Non-trivial" means: `content.strip()` is non-empty (at least one
non-whitespace byte).  This is the floor.  Quality (schema, verdict
structure, minimum size) is NOT validated — that remains graph policy.

### Interaction with retries, goal_gate, and continue_on_fail

- **Retries:** a `must_write=` violation **respects `max_retries`** — and the
  mechanism is worth stating precisely, because a plain FAIL outcome is
  *never* re-attempted by `max_retries` in this engine (the retry ladder
  retries only RETRY outcomes and retryable exceptions; see spec §3.5).  The
  contract is therefore checked **per-attempt inside `execute_with_retry()`**:
  a completed attempt (SUCCESS / PARTIAL_SUCCESS) that violates the contract
  consumes a retry attempt exactly like a RETRY outcome, mirroring the
  fail-closed goal-gate verdict retries (§25).  A no-write completion is
  precisely the flaky-failure class where an in-place retry helps —
  re-invoking the handler gives it another chance to produce the artifact.
  With `max_retries=N`, a never-writes node invokes its handler exactly
  `1 + N` times before failing.  When attempts are exhausted, the violation
  becomes a loud FAIL that routes through the node's normal failure edges —
  `retry_target` and `condition="outcome=fail"` graph-routing retries work
  as usual.  `allow_partial=true` does **not** soften the exhausted FAIL to
  PARTIAL_SUCCESS (fail-closed).  This holds on **both** exhaustion paths:
  the completed-attempt path (SUCCESS/PARTIAL_SUCCESS attempts that never
  produced the artifact) AND the RETRY-exhaustion path, where the ladder
  would otherwise manufacture a `PARTIAL_SUCCESS("Retries exhausted,
  partial accepted")` verdict — that manufactured verdict is checked
  against the artifact contract before it is returned.  Retries exhausted
  + `allow_partial` + no artifact is a loud FAIL: no artifact means there
  is nothing to accept partially.
- **SKIPPED (DESIGN DECISION):** SKIPPED means the node did not execute,
  and the artifact contract applies only to **completed executions** — a
  SKIPPED outcome passes through the check unconverted, in both the retry
  ladder and the engine's final backstop.  A legitimately-skipped
  `must_write=` node (runs_on mismatch, failed dependencies, handler-side
  skip) is NOT converted to FAIL for lacking an artifact it was never asked
  to produce.  The one deliberate asymmetry: `auto_status=true` promotion
  (SKIPPED → SUCCESS) runs BEFORE the final backstop, so a promoted node
  counts as a completed execution and the contract applies to it — a node
  that ran, wrote no status, and wrote no artifact is exactly the
  narration-without-artifact class this contract exists to catch.
- **goal_gate:** the FAIL outcome returned by the must_write check has
  `is_explicit=False` (the node never asserted a verdict; the engine forced
  the FAIL).  A `goal_gate=true` node whose must_write check fires cannot
  satisfy its own gate — correct, since it produced no artifact.
- **continue_on_fail:** a `must_write=` FAIL is **non-overridable**.
  `continue_on_fail=true` does NOT suppress it.  The guarantee is by
  **ordering**, not a flag: the engine runs the must_write check as the
  FINAL backstop, after the `auto_status` promotion and the
  `continue_on_fail` override, so any non-FAIL outcome that reaches the end
  of node processing without a fresh, non-trivial artifact is failed there.
  This also covers the adjacent side door: a must_write node whose handler
  FAILED for its own reasons and whose artifact was never written cannot be
  resurrected to SUCCESS by `continue_on_fail=true` — the backstop re-checks
  the artifact contract after the override and fails the node.  A pipeline
  author cannot accidentally (or intentionally) void the artifact contract
  by adding `continue_on_fail=true` to a must_write node.

### Residual: delayed-replant window

The mtime-floor alone leaves a narrow window where an external process writes
a content-bearing file after node start but before the check runs, and the
node's own session never wrote.  **Session attribution** — correlating the
write to this node's `session_id` — is the preferred closing mechanism: it
retires the sibling-plant class entirely (a sibling node pre-writing another
node's declared artifact inside the window).  The mtime floor
is the minimum shipped here; session attribution is deferred.  The test
suite (`test_case4_delayed_replant_informational`) documents this residual
honestly: a delayed replant passes under the mtime-only implementation, by
design and on the record.

### Exemplar adoption

`examples/patterns/task-runner.dot` postmortem node declares
`must_write=".ai/postmortem/report.md"` as the first consumer.  The
`pm_gate` guard remains in place until the contract is live-proven; it is
not removed in this change (per the task's non-goal).

### Guard retirement inventory

What this contract retires, and when — honest on both halves:

- **Already retired by the freshness floor (shipped here):** guard glue that
  exists only to wipe STALE prior-round artifacts before a node re-executes.
  When a node is visited again on a graph cycle, a fresh `node_start_wall`
  is recorded for that execution — a file left over from a previous round
  has an older mtime and cannot satisfy this round's contract.
- **Retires when session attribution lands (deferred):** guard glue against
  SIBLING PLANTS — one node pre-writing another node's declared artifact
  during the delayed-replant window.  The mtime floor cannot distinguish
  that write from the node's own.
- **Retires only after the contract is live-proven:** the **pm_gate stub**
  in `examples/patterns/task-runner.dot` — subsumed by the postmortem
  node's own fail-closed artifact contract (`must_write=` is declared on
  that node in this change, but the deterministic guard is deliberately
  kept; see Exemplar adoption).

**What does NOT retire:**

- **Verdict parsing stays graph policy.**  A write-first skeleton ending
  `VERDICT: PENDING` passes every `must_write=` axis (fresh, authored,
  non-trivial) yet carries no shippable verdict — the task-runner's anchored
  `^VERDICT:` grep still refuses it.  Presence and quality are separate
  contracts by design: `must_write=` moves the presence half into the
  engine; the quality half (anchored verdict parsing, consensus, stall
  counting) remains graph policy forever.

### Backward-compatibility inventory

All existing pipelines are unaffected: the check is opt-in.  No existing node
in the shipped examples declares `must_write=`; the DOT parser already passes
unknown attributes through to `node.attrs` unchanged.  The only new behavior
is for nodes that explicitly add the attribute.

### Files touched

- `modules/loop-pipeline/amplifier_module_loop_pipeline/must_write.py` —
  `check_must_write(node, outcome, node_start_wall, context)`: the shared
  contract check (new module, so `engine` and `retry` can both use it
  without a circular import).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/retry.py` —
  per-attempt check inside `execute_with_retry()`: a completed attempt that
  violates the contract consumes a retry attempt like a RETRY outcome;
  exhaustion returns the loud FAIL (`allow_partial` does not soften it).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` —
  `node_start_wall = time.time()` recorded before handler execution;
  `_check_must_write` delegates to the shared check and runs as the FINAL
  backstop (Step 2.7, after the auto_status and continue_on_fail overrides).
- `specs/EXTENSIONS.md` — this entry.
- `examples/patterns/task-runner.dot` — postmortem node gains
  `must_write=".ai/postmortem/report.md"` (exemplar adoption).
- `modules/loop-pipeline/tests/test_engine_must_write.py` — unit tests for
  the adversarial battery cases, relative-path resolution, non-trivial
  semantics, retry semantics (`1 + max_retries` handler invocations,
  retry-then-write success, allow_partial and continue_on_fail
  interactions), backward compat, and the council-amendment battery
  (RETRY-exhaustion manufactured-verdict veto both directions, SKIPPED
  pass-through both levels, auto_status-promotion asymmetry).
- `modules/loop-pipeline/tests/test_retry.py` — exhaustion telemetry truth:
  the `pipeline:stage_failed` event's `final_status` always matches the
  returned outcome (string `allow_partial="false"`, partial acceptance,
  and must_write-vetoed partial).
- `docs/CONTRACTS.md`, `docs/DOT-SYNTAX.md`, `docs/DOT-AUTHORING-GUIDE.md`,
  `context/engine-semantics.md` — retry-ladder truth stated where
  `max_retries` is glossed (the ladder retries RETRY outcomes, retryable
  exceptions, and must_write violations; a plain FAIL is never retried in
  place), plus the continue_on_fail behavior-change sentence.
- `docs/reports/2026-02-20-nlspec-dod-gap-analysis.md` — dated errata note
  for the §11.5 "retried on RETRY or FAIL outcomes | PASS" row.

---

## 28. Run Provenance Stamping in `manifest.json`

**What:** `manifest.json` (written by the engine at run-directory creation, Spec §5.6) now
includes two additional provenance fields:

```json
{
  "graph_name": "...",
  "goal": "...",
  "start_time": "2026-08-03T00:00:00+00:00",
  "node_count": 3,
  "edge_count": 2,
  "engine_version": "0.1.0",
  "engine_commit": "abc1234..."
}
```

- `engine_version` — the `amplifier-module-loop-pipeline` package version string from
  `importlib.metadata`.  Today this is the static `pyproject.toml` value (`"0.1.0"`);
  it becomes discriminating when the package adopts release tags.
- `engine_commit` — the resolved git commit hash from PEP 610 `direct_url.json`, written
  by uv for git installs.  For editable/dev installs where `direct_url.json` is absent or
  carries no commit, the value is `"unknown"` — stamped honestly rather than guessed.

The standalone runner augments the manifest after each engine run, including a
failed run, with `runner_version`, `runner_commit`, and `provider` fields. Runner
version and commit use the same install-time metadata / PEP 610 mechanism and use
`"unknown"` when that identity is unavailable. `provider` is the runner API/CLI
selection (DOT node-level provider attributes remain the routing authority). One
writer per field — no races.

**Why:** Incident 2026-07-28: the run directory could not self-describe what code produced
it.  The incident analysis had to reconstruct engine identity from install history.  In a
fast-moving repo, "which engine produced this run?" is the first triage question; this
extension makes the run directory answer it durably.  Any cross-run comparison tooling
likewise needs per-run code provenance to be meaningful.

**Honesty contract:** `"unknown"` is the correct value when identity cannot be determined
from install-time metadata without fabricating.  A fabricated provenance field is worse
than an honest gap — stamp `"unknown"` over guessing.

**Compatibility:** Fully backward-compatible.  The five legacy fields (`graph_name`, `goal`,
`start_time`, `node_count`, `edge_count`) are unchanged.  The new fields are additive.
Existing manifest consumers (dashboards, tests reading `manifest.json`) continue to work.

**Runner-engine compatibility assertion:** The `pipeline-runner` package now includes a
startup compatibility assertion (`compat.py`) that checks for required engine symbols before
any node runs.  The chosen shape is a compat-assert (not a pinned dep or single-package
collapse) — see `compat.py` for the tradeoff rationale and the `amplifier-foundation @main`
deferral note.

---

## 29. `feedback_from=` Node Attribute — Feedback Accumulation Contract

> **This extension is NOT in the canonical attractor spec.** The canonical spec has no
> feedback-accumulation vocabulary. This extension should be proposed upstream: the mathematical
> heart of the attractor (retry-with-accumulated-critique is descent, not re-flip) is a spec-level
> claim that deserves a spec-level mechanism. Until then, this extension documents the behavior here.
>
> **depends-on:** none
>
> **upstream action:** deferred, reason: this is additive to a spec-silent area rather than a
> divergence, but it is exactly the kind of shipped-ahead-of-upstream mechanism this repo commits
> to proposing back; we want at least one more consuming pipeline generation beyond
> `convergence-factory.dot` before writing spec language, so the proposal reflects a proven shape
> rather than a speculative one, review-by: 2026-09-05

**What:** A node may declare `feedback_from="<critic_node_id>"` to establish an engine-enforced
feedback accumulation contract. On every `loop_restart` edge traversal, the engine:

1. Reads the named critic node's output from the just-completed iteration's `node_outcomes` (BEFORE
   clearing them).
2. Prepends an iteration label: `"Iteration N critique: <text>"`.
3. Appends the labeled entry to an accumulated channel stored in context under the internal key
   `feedback.channel.<target_node_id>` (e.g. `feedback.channel.generate` for a node named
   `generate`). Each target node gets its own channel key, preventing feedback leakage when multiple
   generator nodes each declare a different critic in the same pipeline.
4. Trims the channel to at most `MAX_CRITIQUES = 5` entries (oldest-first drop — the curation bound).
5. Composes the channel into a newline-joined string and writes it to the **plain** context key
   `prior_critiques_<target_node_id>` (e.g. `prior_critiques_generate`), making it immediately
   available for `$prior_critiques_<target_node_id>` substitution (e.g. `$prior_critiques_generate`)
   in `prompt` attributes on the next iteration. **Delivery is guaranteed:** if the target's prompt
   does not reference the placeholder, the codergen handler appends a labeled critique-history block
   automatically before variable expansion (`feedback.py:ensure_feedback_placeholder()`). The
   placeholder controls WHERE the history appears, never WHETHER it appears — forgetting it cannot
   silently sever the feedback loop.
6. Writes the accumulated channel to a durable artifact at
   `<logs_root>/feedback/<target_node_id>.md`, overwriting it each restart so it always reflects the
   current window.

The critic node's output is resolved in this order: `context_updates["tool.output"]` (full stdout of
a tool node) → `context_updates["tool.last_line"]` → `outcome.notes` (codergen summary) →
`outcome.failure_reason` (if the critic itself failed — still informative feedback).

**Why:** The mathematical heart of the attractor is descent: a retry without critique of the prior
attempt is a coin re-flip (same distribution, new sample); a retry with accumulated critique is
descent. Before this extension, that load-bearing behavior hung on prose: the generator node's prompt
said "check `.ai/feedback/` for prior guidance" — invisible to the engine, unverifiable at run time,
silently lost when a prompt was edited, and dependent on the model choosing to comply every iteration.
One bad day — the exact perturbation the basin exists to absorb — and the loop degraded into an
infinite re-flip with a nicer name, indistinguishable from convergence until the budget died.

`feedback_from=` converts every retry loop from hoping into descending. Whether feedback reaches the
next iteration is now a property of the graph structure, not of model obedience on a given day.

**Curation / token discipline:** The channel is bounded to `MAX_CRITIQUES = 5` entries; each entry
is truncated to `MAX_CRITIQUE_CHARS = 500` characters with a `[…truncated]` suffix. Token cost per
iteration: at most `5 × 500 = 2 500` characters of injected critique — well within typical prompt
budgets. The critique node itself is the primary curator: pipeline authors write the critique node's
prompt to emit a single highest-leverage observation per iteration (the "Pyramid Summary" pattern in
`convergence-factory.dot`). The window bound is a safety net, not the primary curation mechanism.
An unbounded append channel becomes a stagnation attractor — early wrong ideas crowd out corrections;
accumulated critique becomes context poisoning. The bound prevents this.

**Injection carrier:** `prior_critiques_<target_node_id>` (e.g. `prior_critiques_generate`) is a
**plain** (non-dotted) context key. The substitution machinery
(`handlers/codergen.py:_expand_variables`, P7 block) expands only plain keys from context in `prompt`
attributes. Dotted keys (e.g. `feedback.channel.<node_id>`) work in `tool_command` but NOT in prompts
— `context/engine-semantics.md §4`. The internal accumulation channel uses the dotted key
`feedback.channel.<target_node_id>` precisely to avoid prompt expansion; the injected key
`prior_critiques_<target_node_id>` is plain precisely to enable it. Pipeline authors MAY reference
`$prior_critiques_<target_node_id>` in their `prompt` attribute — e.g. `$prior_critiques_generate`
for a node whose `id` is `generate` — to control placement. When the placeholder is absent, the
codergen handler appends a labeled block carrying it before expansion, so the same substitution
path delivers the history either way (declaring `feedback_from=` is sufficient on its own).

**Timing contract:** `collect_and_inject_feedback()` (`feedback.py`) is called at `loop_restart`
time, AFTER the critic node has completed (its output is in `node_outcomes`) and BEFORE
`node_outcomes.clear()` erases it. The injected `prior_critiques_<target_node_id>` key survives the
restart because `context_updates` are intentionally left untouched by the loop_restart block
(`engine.py` Step 6 comment). This is the natural carrier: feedback is another context write that the
restart intentionally preserves.

**Attribute placement:** `feedback_from=` is declared on the **target node** (the generator), not
on the loop_restart edge. This makes the dependency explicit in the graph: the generator node
declares which critic it listens to. Multiple target nodes can each declare different critics.

**Backward compatibility:** Fully opt-in. Nodes without `feedback_from=` are completely untouched —
zero change in behavior. The file-based `.ai/feedback/` convention used by existing pipelines
continues to work. The engine channel is additive: pipelines can use both simultaneously.

**Walk-upstream note:** The canonical spec has no feedback-accumulation vocabulary. This extension
should be proposed upstream: "feedback must accumulate across iterations" is a spec-level claim about
what makes iteration a descent rather than a re-flip. The `attractor lint` tool can grow a
topological rule: "outer loop without a `feedback_from=` channel on any generator node" — a
statically checkable warning that a loop may be re-flipping rather than descending.

**Implementation locations:**
- `amplifier_module_loop_pipeline/feedback.py` — `collect_and_inject_feedback()`, the collection
  and injection logic, and `ensure_feedback_placeholder()`, the prompt-side delivery guarantee
  (analogous to `must_write.py`)
- `handlers/codergen.py: execute() step 1` — calls `ensure_feedback_placeholder()` on the raw
  prompt before variable expansion
- `engine.py: run() Step 6 (loop_restart)` — calls `collect_and_inject_feedback()` BEFORE
  `node_outcomes.clear()`, then continues with the existing restart sequence
- `modules/loop-pipeline/tests/test_feedback_mechanism.py` — unit + integration tests
- `examples/patterns/convergence-factory.dot` — canonical exemplar declaring the contract

**Constants (tunables in `feedback.py`):**
- `MAX_CRITIQUES = 5` — maximum channel depth (oldest-first drop when exceeded)
- `MAX_CRITIQUE_CHARS = 500` — per-entry character cap (truncated with `[…truncated]`)
- `PRIOR_CRITIQUES_KEY_PREFIX = "prior_critiques_"` — prefix for the per-target plain injection key
  (canonical; full key = `PRIOR_CRITIQUES_KEY_PREFIX + node_id`, e.g. `"prior_critiques_generate"`)
- `_CHANNEL_KEY_PREFIX = "feedback.channel."` — prefix for the per-target internal dotted key
  (canonical; full key = `_CHANNEL_KEY_PREFIX + node_id`, e.g. `"feedback.channel.generate"`)
- `PRIOR_CRITIQUES_KEY = "prior_critiques"` — the unscoped key name from the initial design.
  Never written by the engine; retained so tests can assert it is never written (regression
  guard for per-target scoping)
- `_CHANNEL_KEY = "feedback.channel"` — the unscoped channel name; same never-written guard

---

## 30. Ledger Entry for PR #120's Observability Trio: `attempt_count`, Generalized `failed_step`, `cycle_index`, `emit_node_events`, Exception-Driven `stage_retrying`, and `_branch_id` Scoping

> **This is a ledger entry, not new work.** PR #120 (commit `fb9fbe5`, "epic #371 observability
> trio") shipped the contract additions described below without a corresponding entry in this
> file, in violation of `PRINCIPLES.md`'s requirement that "new event contracts \u2026 [require you to]
> add or update a spec extension document in the same PR that lands the implementation.
> Implementation without a corresponding spec note is debt." The gap was found in an independent
> post-merge review; none of the behavior below is new, and nothing is broken \u2014 this entry pays
> down the documentation debt for work that already shipped. Credit for the implementation
> belongs to PR #120 (Ken Chau); this entry is written after the fact, by a reviewer, to close
> the gap the original PR left open.

### What shipped

**1. `Outcome.attempt_count: int | None`** \u2014 the real, 1-indexed attempt count consumed by the
retry ladder. `None` when the outcome never entered the ladder (e.g. the engine's `must_write=`
final backstop, or subgraph/branch execution, which has no retry policy of its own).

- `outcome.py:97` \u2014 field declaration; docstring at `outcome.py:89-96` states the `None` case
  precisely and notes SKIPPED outcomes ARE included (they pass through the ladder without
  looping within it).
- `retry.py` \u2014 populated on every return path of `execute_with_retry()`: the exception-FAIL
  paths (`retry.py:238`, `:263`), the must_write-clean success path (`:277`), the
  must_write-exhaustion FAIL (`:307`), the plain-FAIL return (`:312`), the SKIPPED return
  (`:329`), the manufactured PARTIAL_SUCCESS on retries-exhausted (`:369`), and the manufactured
  FAIL on retries-exhausted (`:387`).
- `engine.py:742` \u2014 surfaced on the `pipeline:node_complete` event as `"attempt": outcome.attempt_count or 1`
  (falls back to `1` for outcomes that never entered the ladder, e.g. the `requires=` skip
  backstop). This is distinct from the pre-existing `"attempt": 1` at `engine.py:510`
  emitted on `pipeline:node_start`, which is a within-handler retry counter kept for backward
  compatibility \u2014 the two fields are not the same signal and consumers should not conflate them.
- `engine.py:615-700` \u2014 the two `Outcome` reconstruction sites (`auto_status` promotion and
  `continue_on_fail` override) carry `attempt_count` (and `failed_step`) forward field-by-field
  instead of dropping them; the reconstructed `Outcome` otherwise resets `is_explicit` to its
  default so a masked/overridden result cannot silently satisfy a `goal_gate=true` node's gate
  (see \u00a725).

**2. `failed_step` generalized from `ToolHandler`-only to `CodergenHandler`.** Previously the
structured `failed_step` payload (\u00a725's backward-compat inventory footnote; originally "Issue 10
/ analog of WS-4 Sub-fix C") was populated only by `handlers/tool.py`. `handlers/codergen.py` now
populates it too, on both its failure paths, with an LLM-appropriate shape:

```
{"prompt": <first 500 chars>, "response_tail": <last 2000 chars, "" not None>, "error": <str>}
```

capped at 8192 total bytes (`_TOTAL_CAP_BYTES`, `codergen.py:268`); when the encoded payload
exceeds the cap, `response_tail` is dropped first and replaced with
`"verification_gap": {"log_filtered": True}` (`codergen.py:299-302`), mirroring `ToolHandler`'s
truncation-marker convention. `response_tail` is always a string, never `None`, matching
`ToolHandler`'s `stdout_tail`/`stderr_tail` convention (`outcome.py:82-83`).

- `handlers/codergen.py:163-172` \u2014 exception path: `_build_failed_step(prompt=prompt,
  response_text=None, error=str(e))`.
- `handlers/codergen.py:206-215` \u2014 goal-gate verdict-recovery path: when `_parse_outcome`
  returns FAIL and no `failed_step` is already set, attaches the same shape with the actual
  `response_text` captured.
- `handlers/codergen.py:271-304` \u2014 `_build_failed_step()`, the shared builder and truncation
  logic for both call sites.

**3. `cycle_index` (0-based)** on manager-loop and pipeline subgraph-completion records, giving
both handlers a common field name for "which repetition" without requiring a consumer to know
each handler's own on-disk numbering convention:

- `handlers/manager_loop.py:417-427` \u2014 `_subgraph_runs` entries gain `"cycle_index": cycle - 1`
  (the handler's own `cycle` counter is 1-based; the on-disk `{manager_node_id}_cycle_{cycle}`
  naming is unchanged).
- `handlers/pipeline.py:315-323` \u2014 the analogous subgraph-completion record gains
  `"cycle_index": _inv`, already 0-based on that path; on-disk `subgraph_{node.id}` /
  `subgraph_{node.id}__iter{N}` naming is unchanged.

**4. `run_subgraph(..., emit_node_events: bool = True)`** \u2014 a new public keyword-only parameter
on `PipelineEngine.run_subgraph()` (`engine.py:933-938`). Previously `run_subgraph()` emitted no
`pipeline:node_start` / `pipeline:node_complete` events at all, leaving `ManagerLoopHandler`'s
in-graph subgraph path (and any other direct caller) entirely dark. `run_subgraph()` now emits
both events for every node it executes, by default. `ParallelHandler` passes
`emit_node_events=False` for its branch engines (`handlers/parallel.py:169,175`) because it
already emits the equivalent events itself, tagged `via_parallel=True`; without the opt-out,
branch nodes would double-count in the timeline.

This parameter replaces a private `_suppress_subgraph_node_events` setattr flag from an earlier
iteration of the same change \u2014 the setattr approach required external code to mutate engine
state and save/restore it around a shared instance. The keyword-only parameter is the one
behavior-affecting piece of this ledger entry: **the default changed from "emits nothing" to
"emits by default,"** which is new signal for any consumer already listening to
`pipeline:node_start`/`pipeline:node_complete` on an engine whose graph contains subgraph or
manager-loop nodes. Existing callers passing only `(start_node_id, context=...)` are unaffected
by the parameter's addition, and the wire shape of the emitted events matches the top-level
`run()` loop's node events (retry-ladder-only fields such as `attempt` fall back to `1`, since
`run_subgraph()` has no retry policy of its own).

**5. `pipeline:stage_retrying` on exception-driven retries.** Before this change, `retry.py` only
emitted `PIPELINE_STAGE_RETRYING` for a RETRY-status outcome or a `must_write=` violation
(`retry.py:290-297`, `:342-349`); an exception raised by the handler itself retried silently.
`retry.py:246-256` adds the same emission on the exception path, with `"reason":
f"exception:{type(e).__name__}"` so a consumer can distinguish an exception-driven retry from a
status-driven one. The event only fires when `attempt < policy.max_attempts` (i.e. another
attempt will actually happen) \u2014 an exhausted exception path returns FAIL directly, as before.

**6. `_branch_id` scoping conventions** for child-engine event disambiguation, used consistently
by both nested-execution handlers:

- `handlers/manager_loop.py:379-382` \u2014 `cycle:{manager_node_id}:{cycle}`, prefixed with the
  parent's own `_branch_id` (if any) via `>` so nesting under a parallel branch stays
  disambiguated.
- `handlers/pipeline.py:258-262` \u2014 `subgraph:{node.id}`, same parent-prefixing convention.

Both sites set `child_engine._branch_id` directly (an attribute read by `_emit`, not a new public
API) rather than threading a new constructor parameter through; this is consistent with how the
existing `ParallelHandler` branch tagging already worked and does not change any wire shape by
itself \u2014 it only prevents concurrent child-engine events (folder subgraphs under parallel
fan-out, nested manager-loop cycles) from being ambiguous about their source.

### Compatibility

**Additive on the wire.** No existing `status.json` / `pipeline:*` event field was removed or
renamed. `Outcome.attempt_count` is a new dataclass field with a `None` default; existing
`Outcome(...)` call sites that do not pass it are unaffected. `"attempt"` on
`pipeline:node_complete`, `"cycle_index"` on the two subgraph-completion records, and the
generalized `failed_step` on `CodergenHandler` failures are all new keys in existing dict
payloads \u2014 a consumer that does not read them sees no change. `pipeline:stage_retrying` on
exception-driven retries is a new *occasion* to emit an existing event with its existing shape,
not a new field.

**One behavior-affecting change: `run_subgraph()`'s default.** Everything else in this entry is
purely additive (new fields on outcomes/events a consumer must opt into reading). The
`emit_node_events` default is different in kind: it changes what a *silent* method now does by
default \u2014 emitting `pipeline:node_start`/`pipeline:node_complete` for every subgraph node where
it previously emitted nothing. A consumer that hooks pipeline events on an engine driving a graph
with subgraph or manager-loop nodes will now see node events for that nested execution that it
did not see before. Any direct caller of `run_subgraph()` that needs the old silent behavior
should pass `emit_node_events=False` explicitly, as `ParallelHandler` does for its branch
engines.

### Implementation locations

- `modules/loop-pipeline/amplifier_module_loop_pipeline/outcome.py` \u2014 `attempt_count` field
  (line 97).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/retry.py` \u2014 `attempt_count` set on every
  return path; exception-driven `pipeline:stage_retrying` emission.
- `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` \u2014 `"attempt"` on
  `pipeline:node_complete` (main loop and `run_subgraph()`); `attempt_count`/`failed_step`
  carried through the `auto_status` and `continue_on_fail` `Outcome` reconstructions;
  `run_subgraph(..., emit_node_events: bool = True)` and its node-event emission.
- `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/codergen.py` \u2014 generalized
  `failed_step` (`_build_failed_step()` and its two call sites).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/manager_loop.py` \u2014
  `hooks=`/`cancel_event=` wiring onto the child `PipelineEngine`; `cycle_index` on
  `_subgraph_runs` entries; `_branch_id` scoping (`cycle:{manager_node_id}:{cycle}`).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/pipeline.py` \u2014 `cycle_index` on
  the subgraph-completion record; `_branch_id` scoping (`subgraph:{node.id}`).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py` \u2014
  `emit_node_events=False` on branch-engine `run_subgraph()` calls (avoids double-counting
  branch node events already emitted with `via_parallel=True`).
- `modules/loop-pipeline/amplifier_module_loop_pipeline/pipeline_events.py` \u2014
  `PIPELINE_STAGE_RETRYING` (pre-existing constant; new emission occasion only).
- Tests exercising this surface (added/extended in PR #120, unchanged by this entry):
  `modules/loop-pipeline/tests/test_retry.py`, `test_subgraph_runner.py`,
  `test_manager_loop.py`, `test_parallel_branch_observability.py`,
  `test_p8_continue_on_fail.py`.

---

## 31. Ledger Entry for PR #134: Retry-Budget Validation (Conformance Restoration) and
Tool-Command/Handler-Mismatch Rejection (Stricter-Than-Spec Admission)

> **This entry is a ledger entry for already-merged work, not new work.** PR #134
> (commit `d792807`, "fix: validate DOT retry budgets", @robotdad) shipped two `validate()`-time
> structural checks without a corresponding entry in this file. Credit for the implementation
> belongs to the PR's author; this entry is written after the fact to close the ledger gap and
> to classify each half of the change honestly — they are not the same kind of change.
>
> **depends-on:** §2, §3 (this entry validates the exact attributes those extensions define:
> `default_max_retries`/`default_max_retry` and node-level `max_retries` inheritance)
>
> **upstream action:** not applicable to the retry-parsing half (conformance restoration, see
> below — canonical spec already requires this). Not applicable to the handler-mismatch half
> either: it is a strictly local admission-time narrowing that refuses a subset of graphs the
> spec would silently admit; it does not ask the spec to change, so there is nothing to propose
> upstream.

**What shipped:**

1. **`retry_budget_non_negative` — a conformance restoration.** The canonical spec declares
   both the graph-level default and the node-level override as typed `Integer` attributes:
   `default_max_retries` (`attractor-spec.md:139`, also `:1993`) and `max_retries`
   (`attractor-spec.md:152`, also `:2010`). Before this PR, the DOT parser silently coerced
   malformed values (`int(val)` truncated fractions like `1.5`→`1`, accepted `True` as `1` since
   Python's `int(True) == 1`, and raised an unhandled `ValueError`/`TypeError` on non-numeric
   strings instead of producing a diagnostic). `_parse_non_negative_retry_count()`
   (`retry.py`) and the new `_check_retry_budgets()` validation rule (`validation.py`) now reject
   negative values, booleans, fractions, and unparseable strings at `validate()` time with a
   named `ERROR` diagnostic, for both the node attribute and both graph-level aliases
   (`default_max_retry` / `default_max_retries`). **This is a restoration, not an extension:**
   the spec already declares these attributes as `Integer`; the implementation previously
   accepted values the spec's own type never permitted, and silently mis-executed rather than
   diagnosing them. No spec change is needed and no `upstream action` applies.

2. **`tool_command_requires_tool_handler` — a stricter-than-spec admission rule.** Canonical
   spec §4.5 (`CodergenHandler`, `attractor-spec.md:656-705`) never reads or references the
   `tool_command` attribute at all; the spec is silent on it for a codergen-resolved node, which
   means a spec-conformant `CodergenHandler` simply ignores a `tool_command` attribute sitting on
   a node it handles — the spec permits (by omission) a graph where `tool_command` is present but
   inert. This PR's `_check_tool_command_handler()` (`validation.py`) makes that shape a
   validation `ERROR`: a non-empty `tool_command` on a node whose *effective* handler resolves to
   a recognized non-tool built-in (codergen, conditional, start, exit, …) is now rejected outright,
   not silently ignored. **This is a real narrowing, plainly stated:** we now refuse to execute a
   graph the canonical spec would admit and run (just with the attribute quietly doing nothing).
   Unrecognized/custom `type=`/`node_type=` values are deliberately exempted (`_effective_handler_type()`
   returns `None` for any unknown explicit type), preserving the custom-handler extension point —
   the rule only fires when the *resolved* handler is a recognized non-tool built-in, never for an
   unregistered extension type the runtime hasn't seen yet.

**Why this framing matters:** conflating the two would either overstate the retry-parsing fix
as a behavior change requiring upstream sign-off (it doesn't — the spec's own `Integer` type
already prohibited the values now rejected) or understate the handler-mismatch rule as "just
tightening validation" without naming that it refuses spec-legal graphs. Recording both
correctly is the point of this entry.

**Compatibility:** The retry-parsing restoration is backward-compatible for every graph that
was already supplying spec-conformant integer retry values; only malformed values that were
previously mis-executed (truncated, coerced, or silently defaulted via an uncaught exception
path) now produce a clear diagnostic instead. The handler-mismatch rule is a **breaking**
narrowing for the specific, narrow case of a `tool_command` attribute present on a node
resolving to a recognized non-tool handler — such a graph now fails `validate()` where it
previously ran with the attribute silently inert.

**Implementation locations:**
- `modules/loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py: _set_graph_attr()` —
  preserves the raw parsed graph-level retry default instead of coercing it at parse time
- `modules/loop-pipeline/amplifier_module_loop_pipeline/retry.py: _parse_non_negative_retry_count()` —
  the shared non-negative-integer parser (used by both the runtime `RetryPolicy` and validation)
- `modules/loop-pipeline/amplifier_module_loop_pipeline/validation.py: _check_retry_budgets()`,
  `_check_tool_command_handler()`, `_effective_handler_type()`
- `docs/DOT-AUTHORING-GUIDE.md` — documents both structural errors under "Static Lint Rules"
- Tests: `modules/loop-pipeline/tests/test_dot_parser.py`, `test_retry.py`, `test_validation.py`

---

## 32. Ledger Entry for PR #106: `attractor lint` as a Separate Entry Point (§7.4)

> **This entry is a ledger entry for already-merged work, not new work.** PR #106 ("attractor
> lint — five topological basin-lint rules + CLI") shipped claiming no `specs/EXTENSIONS.md`
> entry was needed. An independent post-merge audit judged that call "arguable, and I'd add
> one" — the discriminator for whether an entry is owed is the **entry point** a change lands
> on (advisory `lint()` vs. admission-gating `validate()`/`validate_or_raise()`), not the
> severity of the findings it produces, and a separate advisory entry point is itself a fact
> about the implementation worth recording even though it widens nothing. This entry pays that
> down. Credit for the implementation belongs to PR #106; this entry is written after the fact.
>
> **depends-on:** none
>
> **upstream action:** not applicable — canonical spec §7.4 explicitly permits custom/additional
> lint rules as an extension point (`extra_rules` parameter on `validate()`; see §7.3-7.4 of the
> canonical spec), and `lint()` composes exactly that permitted mechanism. `validate_or_raise()`
> (the admission-gating entry point) is untouched by this change. Nothing here diverges from or
> narrows the spec, so there is no upstream ask.

**What shipped:** A new `lint()` public entry point (`validation.py`) distinct from
`validate()`/`validate_or_raise()`, plus a CLI subcommand (`attractor lint <file.dot>`) that runs
it. `lint()` runs everything `validate()` runs (LINT-001–018, the structural rules, including the
two admission-gating rules from §31 above) **plus** five additional topological ("basin-lint")
rules that reason about cycle structure and handler semantics rather than per-attribute syntax:

- **TOPO-001** (`ERROR`) — dead conditional edge out of a `diamond` (`ConditionalHandler`) node:
  `outcome!=success` / `outcome=fail` conditions on an edge out of a diamond can never fire,
  because `ConditionalHandler` always returns `SUCCESS` unconditionally and `FAIL` is fail-fast
  (never reaches a diamond via a plain edge). This was the root cause of 8 shipped examples
  carrying dead corrective edges for months before this rule existed.
- **TOPO-002** (`WARNING`) — ambiguous multi-match on a tool node (stale `tool.last_line` +
  `outcome=fail` both matching on a retry visit).
- **TOPO-003** (`WARNING`) — acyclic graph (no corrective cycle at all); flags a candidate for
  "this should have been a recipe, not an attractor," while explicitly allowing deliberate
  one-pass pipelines.
- **TOPO-004** (`WARNING`) — a cycle (SCC) with no explicitly-gated exit edge.
- **TOPO-005** (`WARNING`) — a cycle whose continuation/exit rests solely on LLM say-so, with no
  deterministic (tool or human-gate) evidence gate on the cycle.

**Why a separate entry point, not folded into `validate()`:** the five TOPO rules are
judgment calls about pipeline *design quality* (is this graph shaped like a converging
attractor?), not about whether the graph is *executable*. `validate_or_raise()` — the
admission-gating entry point that decides whether a pipeline runs at all — is untouched;
every one of the five rules is reachable only through `lint()`, and only TOPO-001 defaults to
`ERROR` severity within `lint()`'s own exit-code contract (errors → exit 1, warnings → exit 0
unless `--strict`). This is exactly the kind of extension canonical §7.4 anticipates: additional
rules layered on top of, not instead of, the spec's own validation surface.

**Compatibility:** Fully additive. No existing `validate()` or `validate_or_raise()` caller is
affected; `lint()` is a new, separately-invoked surface. A pipeline that was runnable before
this PR remains equally runnable after it — `attractor lint` is an author-time advisory tool,
never consulted by the engine at run time.

**Implementation locations:**
- `modules/loop-pipeline/amplifier_module_loop_pipeline/validation.py` — `lint()` entry point;
  `_check_dead_conditional_edge()` (TOPO-001) and the four sibling TOPO-002–005 checks
- `modules/pipeline-runner/amplifier_module_pipeline_runner/cli.py` — `attractor lint` subcommand
- `docs/DOT-AUTHORING-GUIDE.md` — "Static Lint Rules (`attractor lint`)" section documents all
  five rules with fix examples
- Tests: `modules/loop-pipeline/tests/test_topological_lint.py`,
  `modules/loop-pipeline/tests/test_examples_lint_clean.py`

---

## 33. Main-Loop No-Matching-Edge Hard-Fail

> **This extension DIVERGES from canonical spec §3.2.** Canonical spec §3.2 step 6
> (`attractor-spec.md:388-393`) specifies: when no next edge is selected, return the last
> outcome unchanged if it is `FAIL`; otherwise return `Outcome(status=SUCCESS, notes="Pipeline
> completed")` — a dead end is treated as a normal, successful pipeline completion regardless of
> whether the graph's author intended that node to be a true exit. Our engine instead hard-fails
> in every case: a dead end always terminates the pipeline with `status=FAIL` and a
> `PIPELINE_ERROR` event carrying `error_type=no_matching_edge`, whether or not the last outcome
> was `FAIL`. See `SPEC_CONFORMANCE.md` ATX-11 for the ledger entry and `PRINCIPLES.md` for the
> walk-upstream note.
>
> **depends-on:** none
>
> **upstream action:** deferred, reason: this repo's upstream-contribution effort is currently
> concentrated elsewhere; proposing a change to canonical §3.2's dead-end semantics is a
> considered spec-language edit we want to bring with worked examples (including the
> `run_subgraph` counter-case noted below) rather than a quick issue, review-by: 2026-09-05

### The decision

This was an unledgered divergence: the engine has hard-failed on no-matching-edge since its
initial commit (verified against `git log` — the behavior predates and is unrelated to PR #66,
which only removed a duplicate resume-path check). A session audit found the gap and initially
recorded it with a pending `DECIDE` disposition (ALIGN vs. DIVERGE); that disposition was never
committed to `SPEC_CONFORMANCE.md`, so the decision has been open, undocumented, and — because
`examples/pipelines/practical/bug-fix.dot`'s `escalated` node relies on exactly this hard-fail
behavior to report failure after writing its handoff artifacts (§8 backward-compat note in the
T0-4 restoration above notwithstanding) — **load-bearing** for a shipped exemplar.

**The decision: keep the hard-fail. Never a silent fallback; always a traceable failure
reason.** Rationale: a silent `SUCCESS` on an unrouted, dead-ended graph is the exact incident
class this engine exists to prevent. A real 2.4-hour pipeline run once exited `status=success`
with zero work product because a downstream signal was silently treated as acceptable
completion (see §25's incident motivation for the sibling case at the goal-gate layer). Applying
the spec's dead-end→SUCCESS rule at the main-loop level would reintroduce that same failure
mode one layer up: any graph with a genuinely unreachable or missing edge — an authoring
mistake, not a designed exit — would silently report success instead of surfacing the gap. A
loud, traceable failure (`PIPELINE_ERROR error_type=no_matching_edge`, plus
`terminate_pipeline()`'s `failure_reason`) costs an author a debugging session; a silent false
success costs an operator hours before anyone notices nothing happened.

**No behavior change in this entry.** The engine already behaves this way and has since its
first commit; this entry and the corresponding `SPEC_CONFORMANCE.md` update record the decision
that was made, not a code change.

**Compatibility note — `run_subgraph` is intentionally NOT changed by this decision.**
`run_subgraph()` (`engine.py:917-925` at time of writing) returns the last outcome unchanged on
a dead end, matching the spec's permissive shape — this is deliberate: subgraph dead-ends are
the *compositional* path (a folder/sub-pipeline node's internal routing choices are its own
business), while the top-level main loop is where an unrouted graph is a run-ending authoring
defect. Any future change to `run_subgraph`'s dead-end behavior is a separate decision, not
implied by this entry.

**Implementation locations:**
- `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` — main loop's no-matching-edge
  hard-fail (`terminate_pipeline()` call + `PIPELINE_ERROR` emission with
  `error_type=no_matching_edge`, around the retry-target fallback check)
- `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py: terminate_pipeline()` — the
  sole construction path for a routing-termination outcome (see `AGENTS.md` common-pitfalls: never
  construct a fresh `Outcome(FAIL, ...)` inline at this boundary — it drops `failure_reason`)
- `context/engine-semantics.md` §3 — documents both halves (main-loop hard-fail vs.
  `run_subgraph`'s permissive dead-end) and is guarded against drift by
  `modules/loop-pipeline/tests/test_engine_semantics_doc_guard.py` (D-200a/b/c)
- `examples/pipelines/practical/bug-fix.dot` (`escalated` node) — the shipped exemplar that
  depends on this hard-fail to report failure after writing handoff artifacts

---
## 34. `suggested_next_ids` Type Coercion at Edge Selection (Bug Fix)

> **This is a bug fix restoring intended behavior, not a new extension.** The spec (§3.3 Step 3)
> and this codebase's own `Node.id: str` / `Edge.to_node: str` contract (`graph.py`) have always
> treated node IDs as strings; nothing here changes that contract or adds a new capability.
>
> **depends-on:** none (this closes a gap between the canonical string-ID contract and the code
> that was supposed to enforce it; it does not build on or narrow any other ledger entry)
>
> **upstream action:** not applicable — no spec change is needed and no compatibility-banner
> impact applies. This restores behavior the canonical spec's own string node-ID contract
> already required; the implementation previously accepted a type the contract never permitted
> and silently mis-routed or hard-failed instead of matching correctly.

**Found by:** a 6-lens council review convened while reviewing PR #133 ("preserve spawned agent
outcomes"). The bug is pre-existing and independent of #133 — present on `main` before and
after that PR — but #133's whole premise (making an explicit child `report_outcome` verdict
survive the `session.spawn` boundary reliably) increases how much pipelines lean on
`suggested_next_ids` surviving that boundary correctly, so the same latent bug becomes more
consequential once spawn-path explicit routing is the norm rather than the exception. PR #133
should merge after this lands; nothing in #133 introduces or worsens the bug below.

**What was broken:** `edge_selection.select_edge()` Step 3 compared
`e.to_node == suggested_id` with no type coercion. `Outcome.suggested_next_ids` travels through
several JSON-parsing paths (`backend.py`: `_find_report_outcome_call`, `_outcome_from_structured_output`,
`_outcome_from_spawn_result`, `_parse_outcome`'s pure-JSON and embedded-verdict-recovery
branches) with no per-element type validation before construction. A spawned child (or any
`report_outcome` caller) that emits a bare-number ID in JSON — `{"suggested_next_ids": [3]}`
instead of `{"suggested_next_ids": ["3"]}`, an easy LLM slip — produced a Python `int`, and
`"3" == 3` is always `False`. Depending on graph shape this manifested two ways:

- **With a competing unconditional edge present:** Step 3 silently failed to match, routing
  fell through to Step 4's weight/lexical tiebreak, and the pipeline silently ran the WRONG
  node. No error, no trace.
- **Without one:** Step 4 also found nothing (fail-fast / no eligible unconditional edge), and
  the engine hard-failed with the generic `"No matching edge from node 'X'"` message, which
  named neither the rejected suggestion nor the edges that existed — untraceable.

**The fix:**

- `edge_selection._coerce_suggested_id()` — normalizes one `suggested_next_ids` entry to its
  canonical node-ID string before comparison. Policy: `str` passes through unchanged; `int`
  (excluding `bool`, a `int` subclass but never a sane ID) is coerced via `str(value)` (`3 ->
  "3"`); anything else (`bool`, `float`, `dict`, `list`, `None`, ...) is a genuinely malformed
  shape, not a type slip, and is rejected — logged as a warning naming the value and its type,
  and skipped so one bad entry doesn't prevent the rest of the list from being tried. Floats are
  deliberately NOT coerced: `3.0` is ambiguous against node `"3"` vs a literal node `"3.0"`, and
  silently picking one would be exactly the "coerce into something plausible" failure mode this
  fix is designed to avoid for compound/ambiguous shapes.
- `engine.PipelineEngine._no_matching_edge_reason()` — the `no_matching_edge` failure message
  (still prefixed `"No matching edge from node 'X'"` for backward compatibility with existing
  substring checks) now appends, when the outcome carried `suggested_next_ids`, the suggested
  IDs and the outgoing edge targets that actually existed, so a genuinely unresolvable
  suggestion (wrong ID, or a shape `_coerce_suggested_id` correctly rejected) produces a
  traceable diagnostic instead of a dead end.
- The goal-gate-retry lookup at `engine.py` (`gate_result.suggested_next_ids[0]` ->
  `self.graph.nodes[retry_node_id]`) applied the same unguarded-comparison *class* of risk (an
  uncaught `KeyError` on a type-mismatched or unresolvable ID) even though it is currently
  protected by `_check_goal_gates()`'s own membership check on the sole path that constructs
  such an outcome today. Hardened to use the same `_coerce_suggested_id` + membership check
  rather than a second, divergent rule for the same "suggested next ID" concept, degrading to a
  diagnosed failure instead of a crash if a future producer ever violates that invariant.

**Grep audit (repo-wide):** every other `self.graph.nodes[...]` dict index in `engine.py`
(`edge.to_node`, `fan_in_node_id`, `start_node_id`, `gate_node_id`) is keyed by an ID the engine
itself derived from the graph's own structure, or already validated via an `in` check
(`_resolve_failure_retry_target`) — none of them consume raw LLM/tool-reported IDs directly. No
other instance of the string/int boundary risk was found in the module.

**What is unchanged:** the JSON-parsing call sites in `backend.py` are untouched — the fix is
applied once, at the actual point of comparison, so it covers every current and future producer
of `Outcome.suggested_next_ids` uniformly rather than duplicating validation at each parse site.

**Tests:** `modules/loop-pipeline/tests/test_spawn_suggested_next_ids_coercion.py` — end-to-end,
through the real `session.spawn` path (`AmplifierBackend._run_via_spawn` -> `_parse_outcome`)
and the real `PipelineEngine`, not synthetic `Outcome` objects. Covers both graph shapes (with
and without a competing fallback edge) with an adversarial, JSON-round-tripped int payload.

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

---

## 35. Spawned-Agent Outcome Transport and `report_outcome` Ordering Barrier

> **depends-on:** §25
>
> **upstream action:** not applicable — spawned agent outcome transport is purely implementer-level semantics within the canonical spawn()/execute(...) -> str contract. This extension adds metadata transport to an already-implemented spawn boundary without changing the documented return contract or diverging from the canonical spec. No spec change is needed.

**What:** The `loop-agent` orchestrator transports a spawned child's semantic
`report_outcome` verdict through the canonical `orchestrator:complete` event without changing the
orchestrator's `execute(...) -> str` return contract.

### Completion envelope

Every `AgentOrchestrator.execute()` invocation emits exactly one `orchestrator:complete` event,
including initialization failures and raised exceptions. Its payload has two deliberately
separate layers:

```json
{
  "orchestrator": "loop-agent",
  "status": "success | incomplete | cancelled",
  "turn_count": 2,
  "metadata": {
    "report_outcome": {
      "status": "success | partial_success | retry | fail",
      "preferred_label": "optional",
      "suggested_next_ids": ["optional"],
      "context_updates": {"optional": "value"},
      "notes": "optional",
      "failure_reason": "optional"
    }
  }
}
```

Top-level `status` is **only lifecycle state**:

- `success` — natural completion
- `incomplete` — max-turn, tool-round, context, or awaiting-input limit; initialization/provider/
  tool-loop exception (event is emitted before the original exception is re-raised)
- `cancelled` — cooperative or task cancellation

The semantic node verdict lives only in `metadata.report_outcome`; it does not redefine lifecycle
status. `metadata` is `{}` when no successful report belongs to that invocation, and interrupted
invocations do not promote a partial report. The mounted report tool's `last_outcome` is reset
before each invocation so state cannot leak between calls. `turn_count` is the per-invocation
number of attempted provider calls, computed from the cumulative provider-call counter.

### Ordering barrier

Ordinary assistant tool-call batches retain configured parallel execution. A batch containing
**at least one** `report_outcome` call is the exception: every call in that batch executes
sequentially in the provider-declared order. This barrier is required because `last_outcome` is a
single semantic completion register. For multiple valid reports, the last successful declared
report wins. A later report that fails argument validation or execution does not erase the prior
valid report. After the complete declared batch finishes, any successful `report_outcome` call
terminates the current outer `execute()` invocation without another provider call or automatic
follow-up processing. Already-queued follow-ups remain queued; they are not cleared or consumed by
the terminal report path and may be processed by a later explicit `execute()` invocation.

### Precedence Policy

A child process may emit both an explicit structured verdict (via `report_outcome`) and trailing
prose in its response. **The precedence rule is explicit: structured `report_outcome` status
supersedes contradicting trailing prose.** A spawned agent that returns `status: fail` in its
report-outcome metadata but then writes "all done, mission accomplished" as closing text is
recorded as FAIL; the documented verdict takes precedence over cheerful prose. This mirrors the
behavior already implemented in the direct tool-loop path where tool-command `report_outcome`
verdicts were always the canonical judgment. The spawn path now offers explicit verdict transport
to upstream callers who elect to consume it, placing both paths on equal footing for verdict
reliability.

### Compatibility

This is additive at the spawn boundary:

- `execute()` still returns the original final string unchanged.
- Consumers that ignore `orchestrator:complete.metadata` continue to see the documented lifecycle
  envelope.
- Spawn consumers may opt into explicit verdict transport through
  `metadata.report_outcome`; status-only spawn results remain non-explicit.
- Parallel execution is unchanged for batches without `report_outcome`.

### Implementation locations

- `modules/loop-agent/amplifier_module_loop_agent/__init__.py` —
  per-invocation reset, exactly-one completion emission, lifecycle classification, provider-call
  `turn_count`, and `metadata.report_outcome` transport
- `modules/loop-agent/amplifier_module_loop_agent/agent_session.py` —
  provider-call counting, invocation termination reason, and the `report_outcome` batch ordering
  barrier
- `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py` —
  spawn-result precedence, semantic `Outcome` reconstruction, response/session preservation, and
  full-fidelity transcript continuity
- `modules/loop-agent/tests/test_orchestrator_completion.py`,
  `modules/loop-agent/tests/test_parallel_gating.py`, and
  `modules/loop-pipeline/tests/test_backend_fidelity.py` — contract tests

