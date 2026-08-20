# Attractor Engine Runtime Semantics (SHIPPED engine)

Runtime semantics of the **SHIPPED** engine (`AmplifierBackend`), above DOT syntax.
Where this diverges from the nlspec prose, the **SHIPPED behavior wins**. Each fact is
cited (`file:line` in `modules/loop-pipeline/amplifier_module_loop_pipeline/`, or nlspec
`§`). Re-validate any fact whose cite breaks — a broken cite means the engine moved and
this file is stale.

Cites are relative to `modules/loop-pipeline/amplifier_module_loop_pipeline/`.
nlspec = `attractor/attractor-spec.md`.

---

## 1. Node-type → handler capability table

Source: nlspec §2.8; `validation.py:24-34` (`SHAPE_TO_HANDLER`).

| shape | handler | LLM? | runs code? | set context / route? | tag |
|---|---|---|---|---|---|
| `Mdiamond` | `start` | no | no | no (no-op SUCCESS) `handlers/start.py` | [NLSPEC] |
| `Msquare` | `exit` | no | no | no (engine checks goal gates) `handlers/exit.py` | [NLSPEC] |
| `box` | `codergen` | **yes** | no | **YES via backend** (JSON / `report_outcome`) `backend.py:604-637` | [NLSPEC] |
| `diamond` | `conditional` | no | no | no-op SUCCESS; engine routes `handlers/conditional.py` | [NLSPEC] |
| `hexagon` | `wait.human` | no | no | yes — `suggested_next_ids` + `human.gate.*` `handlers/human.py` | [NLSPEC] |
| `component` | `parallel` | no (orchestrates) | no | emits `branch.{i}.outcome` `handlers/parallel.py` | [NLSPEC] |
| `tripleoctagon` | `parallel.fan_in` | **yes if `prompt` set** | no | yes `handlers/fan_in.py` (§4.9) | [NLSPEC] |
| `parallelogram` | `tool` | no | **yes (shell)** | yes — `tool.output` + `tool.last_line` `handlers/tool.py` | [NLSPEC] |
| `house` | `stack.manager_loop` | no | orchestrates child | experimental ("future form TBD" `validation.py:33`) | [EXTENSION] |
| `folder` | `pipeline` | no (runs child graph) | no | yes — merges child `outputs=` back `handlers/pipeline.py` | [EXTENSION] |

Handler resolution (dispatch): explicit `type` attr → `node_type` attr → shape mapping →
**hard-fail**. There is NO default-handler fallback: a shape outside the table above makes
`HandlerRegistry.get()` raise `ValueError` naming the shape, the node id, the full supported-shape
set, and the remedy (`shape=box` for an LLM node) — `handlers/__init__.py`. This is a **DECIDED
DIVERGENCE** from nlspec §4.2, whose resolution order ends "3. Default handler (the codergen/LLM
handler)": an unrecognized shape must never silently become an LLM session. Ledgered at
`specs/EXTENSIONS.md` §38 and `SPEC_CONFORMANCE.md` ATX-13; both halves asserted by matrix row
`ATX-M-F01`.

The spec-literal `SHAPE_TO_HANDLER.get(shape, "codergen")` fallback survives only in **non-dispatch**
helpers — lint classification (`validation.py`), output-table prediction
(`node_outputs.py:83-89`), and preflight's effective-handler estimate
(`preflight.py:80`). None of these selects an executing handler.

---

## 2. ★ THE DELTA LIST — engine does X, NOT Y (spec says Y) ★

**HIGHEST-VALUE SECTION.** The nlspec prose describes the *pure* handlers; the *shipped*
`AmplifierBackend` behaves differently. Reasoning from the spec here makes you confidently
wrong about the running engine.

1. **`box`/codergen nodes CAN route and set context.** Spec §4.5 shows `CodergenHandler`
   returning hard-coded SUCCESS. SHIPPED: the backend maps the LLM result to a full
   `Outcome` (status, `context_updates`, `preferred_label`, `suggested_next_ids`) via
   (a) a response that is entirely JSON → `_parse_outcome` (`backend.py:903`), or
   (b) the child calling the **`report_outcome` tool** → `_find_report_outcome_call`
   (`backend.py:621,827-890`). LLM nodes are NOT routing-inert.

2. **FAIL is fail-fast — it does NOT traverse plain edges.** Spec §3.2 pseudocode advances
   on any selected edge. SHIPPED (`edge_selection.py:79-101`): on `status==FAIL`, plain
   unconditional edges are skipped. FAIL routes ONLY via `condition="outcome=fail"`, a
   downstream node with `runs_on=always|failure`, or `retry_target`/`fallback_retry_target`
   (§3.7); else the branch halts FAIL. (This is the §3.7 fix merged this session.)

3. **Dotted context keys DO expand** in `tool_command` / `tool_env` / `description`
   (`substitution.py:90-103`, M5) — `${tool.output}`, `$tool.output` both resolve. The
   old "dotted keys not expanded" belief is stale. **CAVEAT:** they do NOT expand inside a
   codergen `prompt` — prompts only expand `$goal`, `$context`, and *plain* (non-dotted)
   keys (`codergen.py:144-173`).

4. **Tool CWD = `context.target_dir` → `graph.source_dir` → process default** — NOT the
   engine dir (`tool.py:116-123`). Set `context.target_dir` for the job dir; no `${JOB_DIR}`
   injection needed.

5. **Verdict fences are tolerated.** Spec implies strict bare JSON. SHIPPED strips
   ` ```json … ``` ` fences before parsing (`backend.py:614-618,925-927`). (The real
   foot-gun is prose-before-JSON — see §6.)

6. **No backend / no `llm_model` now RAISE (fail-loud), not silently degrade.**
   `CodergenHandler` with no backend raises (`codergen.py:88-92`); `_resolve_model` raises
   with no `llm_model` (`backend.py:772`). The old "silent DirectProviderBackend / silent
   default model" modes are closed.

7. **Invalid `fidelity=` warns, not silently defaults.** `fidelity.py:78,94,109,192` (M-22)
   logs a warning then falls back to `compact`.

---

## 3. Routing contract

Source: `edge_selection.py`; `handlers/tool.py`; nlspec §3.3, §3.7, §10.

- **Token channel:** route a tool node via `condition="context.tool.last_line=<token>"`.
  `tool.last_line` = last non-empty stripped stdout line (`tool.py:212-220`) — set **only on
  success**. A **failing tool node does NOT refresh `tool.last_line`** (`tool.py:158-176`
  early FAIL return precedes the `context.set` at `tool.py:220`); the key retains the value
  from the last *successful* execution.
  **Stale-label rule (T0-4 — historical note + determinism note):** on the second+ visit to a
  gate, a stale `tool.last_line` value from a prior successful run can match a
  `context.tool.last_line=X` edge even when the current run failed. Prior to T0-4, this caused
  an *unintended parallel fan-out* (both the stale-label edge and the `outcome=fail` edge
  executed simultaneously). **After T0-4**, the engine conforms to spec §3.3: when multiple
  conditional edges simultaneously match, `select_edge()` deterministically picks **exactly
  one** — the highest-weight edge, with lexical target-id tiebreak. The fan-out consequence is
  gone; the staleness is not. The deterministic pick can still be the wrong edge (e.g. the
  stale-label edge wins over the `outcome=fail` edge if it has higher weight or comes first
  lexically). **Discipline:** add `&& outcome=success` to `context.tool.last_line=X` edges
  that share a source node with an `outcome=fail` edge — this ensures the label edge only
  fires when the tool actually succeeded and the label is fresh. The conjunction is good
  explicitness discipline; it is no longer a safety requirement against fan-out.
  `tool.output` = **full stdout** (`tool.py:179`) — conditioning on it silently never matches.
- **Bare-token condition** = truthy lookup: `condition="context.flag"` is true iff the value
  is non-empty (nlspec §10.5; `conditions.py`).
- **5-step selection** (§3.3; `edge_selection.py:39-101`): condition-match → `preferred_label`
  (unconditional edges only) → `suggested_next_ids` (unconditional only) → highest `weight`
  → **lexical tiebreak on target id**. The lexical tiebreak is silent but specified —
  >1 unconditional edge from one node picks lexically-first.
- **Tool non-zero exit → FAIL** (`tool.py:158-176`); needs an explicit FAIL route per #2 above.
- **No edge selected — behavior depends on execution context:**
  - **Main loop** (`engine.py:773-788`): hard-fails with `terminate_pipeline`, emits
    `PIPELINE_ERROR` with `error_type: no_matching_edge`. This is NOT a silent SUCCESS.
    (Shipped behavior since the initial engine commit `6c8bf5a`; the earlier claim here
    transcribed nlspec §3.2 step 6, which the shipped main loop has never followed — an
    unreconciled spec/engine divergence.)
  - **Subgraph branches** (`run_subgraph`): behavior depends on *why* no edge was selected:
    - **Conditional-mismatch dead end** (outgoing edges exist but none matched the current
      outcome): returns `Outcome(status=FAIL, is_explicit=False)` with a non-empty
      `failure_reason` naming the node and the unmatched outcome. Consistent with the
      main loop's hard-fail posture (EXTENSIONS.md §33). A dead-ended parallel branch
      surfaces this failure in `parallel.results` (the entry carries `status=fail` and a
      non-empty `failure_reason`), where join policies and the fan-in can aggregate it.
      (Resolved in issue-172; see EXTENSIONS.md §33 compatibility note update.)
    - **No outgoing edges at all** (designed terminus): returns the last outcome unchanged —
      graceful subgraph completion. This is the intended exit for a branch that reaches
      the end of its designed path with no further routing required.

---

## 4. Substitution + CWD

Source: `substitution.py`; `node_outputs.py:68-75`; `handlers/tool.py:116-123`.

- Both `$key` and `${key}` resolve, including dotted keys. `$$` → literal `$`.
- **Substitutable attrs only:** `tool_command`, `prompt`, `description`, `tool_env`
  (`SUBSTITUTABLE_ATTRS`, `node_outputs.py:68`). Other attrs are not scanned.
- **Prompt caveat (repeat of delta #3):** dotted keys do NOT expand in `prompt`; only
  `$goal`, `$context`, plain keys do (`codergen.py:144-173`).
- **Absent key → literal token survives** (`substitution.py:11`, intentional pass-through).
  Under `set -eu` bash this dies "unbound variable". **Defense:** shell default
  `${var:-fallback}` in the `tool_command`.
- **CWD:** `context.target_dir` → `graph.source_dir` (the `.dot`'s dir) → process default.

---

## 5. Verdict contract

Source: `backend.py:_parse_outcome, _outcome_from_spawn_result`; `outcome.py`.

**The verdict-recovery ladder** (tried in order for every LLM response):

1. **`report_outcome` tool call** — authoritative; checked before any text parsing (tool-loop
   path: `backend.py:_find_report_outcome_call`; spawn path: `metadata["report_outcome"]`).
2. **Fenced JSON** — ` ```json … ``` ` stripped, then parsed as JSON.
3. **Pure JSON** — entire stripped response starts with `{`, parsed, `status` field honored.
4. **Embedded verdict recovery** — last balanced `{…}` in prose extracted; if it carries a
   recognized `status`, honored with a warning. This catches "prose + trailing JSON" patterns.
5. **Plain-prose fallback** — no parseable verdict found (see fail-closed rule below).

Empty response → FAIL (no work was done).

**Fail-closed goal-gate contract (EXTENSIONS.md §25 — diverges from canonical spec §4.5):**
A `goal_gate=true` node MUST provide an explicit verdict (paths 1–4 above). If the response
reaches the plain-prose fallback (path 5), the outcome is **RETRY** (not SUCCESS). RETRY
respects `max_retries` and then degrades to FAIL; it is the correct signal for "try again
with an explicit verdict." A goal gate cannot be satisfied by silence or by prose that says
the work is not done.

For **non-goal_gate nodes**, path 5 still returns SUCCESS per canonical spec §4.5 — backward
compatible default for ordinary `box` nodes that end in prose.

**What `max_retries` actually retries (the retry ladder):** the ladder retries RETRY
outcomes, retryable-classified exceptions (classified by exception type and message
content — timeouts, connection errors, HTTP 429/5xx), and `must_write=` artifact-contract
violations (EXTENSIONS.md §27); a plain FAIL is returned immediately, never retried.
Graph-level retries of a FAIL are a separate mechanism: `retry_target` and
`condition="outcome=fail"` edges.

**`is_explicit` field on `Outcome`:** `outcome.is_explicit=True` means the status was asserted
by an unambiguous mechanism: paths 1–4 above, a tool (parallelogram) node's exit code
(0 = explicit success, nonzero = explicit fail; `handlers/tool.py`), **verdict-shaped**
`response_schema` structured output (a captured `report_outcome` call or a `status` field
with a recognized value — `backend._outcome_from_structured_output`), or a deterministic
handler verdict that cannot be LLM-defaulted (human-gate selections/freeform input,
start/exit/conditional structural SUCCESS, fan-in ranking, parallel join-policy results).
`is_explicit=False` means the status was defaulted: plain-prose fallback, empty-response
default, a status-only spawn result with no node-level verdict, or **non-verdict**
structured output (format ≠ verdict — `{"name": "Alice"}` parses but asserts nothing, and
does not satisfy a gate). Compositional handlers whose outcome IS a child's outcome
(folder/pipeline nodes, manager-loop stop) propagate the child's `is_explicit`. The
`"Plain text response:"` notes prefix is the legacy signal; `is_explicit` is the durable
field, serialized into every node `status.json` (flat and iteration-scoped) and every
`trace.jsonl` record (`engine.py:_write_node_status`, `codergen.py:_write_status`).

**The gate check enforces both flags:** `_check_goal_gates()` (`engine.py`) treats a goal gate
as satisfied only when `outcome.is_success AND outcome.is_explicit`. A SUCCESS with
`is_explicit=False` (e.g. a spawn wrapper's clean exit with no `report_outcome`, or a schema
node whose output carried no recognized verdict) does NOT satisfy the gate. This centralized enforcement
closes bypass paths that never go through `_parse_outcome`. The RETRY-from-`_parse_outcome`
rule above is the belt; the gate's `is_explicit` requirement is the suspenders.

**Author guidance:** For `goal_gate=true` nodes, always call the **`report_outcome` tool** or
emit a pure JSON response (or use a parallelogram tool node — its exit code is the verdict).
Do not rely on prose output — even prose that says "CONVERGED" will return RETRY under the
fail-closed contract. The recovery ladder (paths 2–4) is a safety net, not a contract.

**Plain-edge hazard:** RETRY (like FAIL) does not traverse plain unconditional edges — it
routes only via `condition="outcome=fail"`, `retry_target`, or `runs_on=always|failure` nodes.
Goal_gate nodes should have explicit `condition="outcome=fail"` or `retry_target` edges. For
non-goal_gate nodes, the plain-prose SUCCESS default means plain edges still work as before.

---

## 6. Remaining real foot-guns

- **`last_response` inter-node carry is ~200 chars** under every fidelity mode except `full`
  — the truncation is in the handler writing the key (`codergen.py:137`, `response_text[:200]`),
  not in `compact` specifically. `compact`/`truncate` preambles surface that short key;
  **`full` bypasses it** via stored transcripts (`backend.py:643-704`). Need the full prior
  text downstream? Use `fidelity=full`.
- **`folder`/subgraph checkpoint reuse across loop iterations (CONFIRMED + FIXED):** child logs
  were keyed on node id alone (`subgraph_<node.id>`), so a folder re-entered in a loop restored the
  prior iteration's completed checkpoint and silently skipped work (the "skips all but the 1st
  source" symptom). Now namespaced per invocation — `subgraph_<node.id>` then `__iter1`, `__iter2`…
  (`pipeline.py:168-200`) — so each iteration runs fresh while a single child run can still resume.
  Regression test: `tests/test_folder_checkpoint_reuse_repro.py`.

---

## 7. Feedback Accumulation (`feedback_from=`) — Extension #29

Source: `feedback.py`; `engine.py Step 6 (loop_restart)`; `specs/EXTENSIONS.md §29`.

**Attribute:** `feedback_from="<critic_node_id>"` declared on the **target (generator) node**.

**What the engine does on every `loop_restart`:**

1. Calls `collect_and_inject_feedback()` (in `feedback.py`) BEFORE `node_outcomes.clear()`.
2. Reads the named critic node's output from `node_outcomes` (resolution order:
   `context_updates["tool.output"]` → `context_updates["tool.last_line"]` →
   `outcome.notes` → `outcome.failure_reason`).
3. Truncates to `MAX_CRITIQUE_CHARS = 500` chars with `[…truncated]` suffix.
4. Labels the entry: `"Iteration N critique: <text>"`.
5. Appends to the accumulated channel stored under the **dotted** key
   `feedback.channel.<target_node_id>` (e.g. `feedback.channel.generate` for a node named
   `generate`; dotted = survives restart, NOT expanded in prompts; per-target scoping prevents
   leakage between multiple generators in the same pipeline).
6. Trims channel to `MAX_CRITIQUES = 5` entries (oldest-first drop).
7. Writes the channel as a newline-joined string to the **plain** key
   `prior_critiques_<target_node_id>` (e.g. `prior_critiques_generate`; plain = expands as
   `$prior_critiques_<target_node_id>` in `prompt` attributes on the next iteration, e.g.
   `$prior_critiques_generate`).
8. Writes the channel to `<logs_root>/feedback/<target_node_id>.md` (overwritten each
   restart — always reflects the current window).

**Timing:** Called after critic node completes, before `node_outcomes.clear()` (line 862).
The injected `prior_critiques_<target_node_id>` key survives the restart because
`context_updates` are intentionally left untouched (engine.py Step 6 comment). Order in Step 6:
`collect_and_inject_feedback()` → `completed_nodes.clear()` → `node_outcomes.clear()`.

**Injection carrier:** `prior_critiques_<target_node_id>` (e.g. `prior_critiques_generate`) is a
plain (non-dotted) key. The P7 block in `codergen.py:_expand_variables` expands plain context keys
in `prompt` attributes. Dotted keys (including `feedback.channel.<node_id>`) do NOT expand in
prompts — only in `tool_command`/`tool_env`/`description` (delta #3 above). The design uses dotted
for persistence and plain for injection deliberately. Pipeline authors MAY reference
`$prior_critiques_<target_node_id>` in their `prompt` attribute to control placement.
**Delivery is guaranteed either way:** when the placeholder is absent, the codergen
handler appends a labeled critique-history block carrying it before expansion
(`feedback.py:ensure_feedback_placeholder()`, called from `codergen.py` step 1) — the
same P7 expansion path then substitutes it. Declaring `feedback_from=` is sufficient
on its own.

**Disk layout:**
- `<logs_root>/feedback/<target_node_id>.md` — accumulated channel (co-location artifact).
  Holds critiques from all retained iterations in one file. Distinct from Extension #24's
  per-iteration records (`iteration_N/<node_id>/status.json`), which scatter one critique
  per file. The feedback/ artifact is what only accumulate-and-inject produces.

**Interplay with `loop_restart`:** The feedback channel is another context write that
`loop_restart` intentionally preserves (same mechanism as `outputs=` values). It does NOT
reset on `loop_restart`; it accumulates. The channel window (max 5) is the only bound.

**Interplay with fidelity:** `feedback_from=` and fidelity are complementary, not
redundant. Fidelity (`full`/`compact`/`truncate`) controls what the *same* actor
remembers of its own prior attempt via backend transcripts (inner loop). `feedback_from=`
gives the *next, fresh* actor the distilled lesson from the prior iteration (outer loop).
Note: `loop_restart` does NOT clear backend thread transcripts — fresh-eyes re-entry is
expressed via edge-level fidelity (e.g., `fidelity=truncate` on the back-edge's target).
Feedback injection is the complement: memory of the *lesson* without memory of the *failed
attempt*. Combining `fidelity=full` + `feedback_from=` on the same node is valid but
creates overlap (full history + injected critiques); prefer one or the other.

**Backward compatibility:** Fully opt-in. Nodes without `feedback_from=` are untouched.
The file-based `.ai/feedback/` convention used by existing pipelines continues to work.

**Token cost:** At most `MAX_CRITIQUES × MAX_CRITIQUE_CHARS = 5 × 500 = 2 500` characters
of injected critique per iteration — bounded regardless of iteration count.

---

## 8. Golden Rules

1. **Every inference is an `llm`/`box` node.** Never call `unified_llm` directly, never
   drop to Python for model calls.
2. **Code nodes (`parallelogram`/tool) are glue only** — shell/IO/orchestration, not inference.
3. **Copy the nearest proven pipeline before inventing.** Simplicity applies to the proven
   pattern, not to a minimal node count built on a wrong engine model.
4. **Route verdicts via the `report_outcome` tool, not free-text JSON** (§5 bug).
5. **Run `dot_graph validate` after every edit** — catches isolated nodes, stray quotes,
   missing fallback edges before an expensive rebuild.
6. **Author for fail-loud:** explicit FAIL edges (§2 #2), explicit `llm_model`, explicit
   `${var:-default}` in shell.
