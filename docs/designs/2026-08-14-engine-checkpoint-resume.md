# Design: Engine-Level Checkpoint Resume (loop-pipeline)

**Status:** IMPLEMENTED — shipped by the PR that added this file (issue #224).
Deviations found in practice are recorded in that PR's body; the text below is
the design as ratified, kept as the record of intent.
**Issue:** #224 (maintainer acceptance criteria comment = definition of done)
**Spec authority:** `specs/canonical/attractor-spec-canonical.md` §5.3 "Resume behavior" (rules 1–6), §5.2, §5.6, DoD `:1857`
**Conformance ledger:** `SPEC_CONFORMANCE.md` ATX-2 — **DECIDED 2026-08-14: ALIGN (build it)**
**History it must not repeat:** PR #66 (commit `5ae3118`) removed a prior implementation whose two crash classes are structurally designed out below.
**Read against:** origin/main @ `3c0197a`. All line references are to that tree.

---

## 0. Charter and posture

The maintainer's ruling (issue #224, 2026-08-14): the missing engine-level resume is *a bug in our design*, not a defensible divergence — spec DoD `:1857` reads verbatim:

> Resume from checkpoint: load checkpoint -> restore state -> continue from current_node

Engine resume must be built per §5.3 and must **coexist** with the graph-owned file-guard idempotency pattern (`examples/pipelines/12-graph-resume.dot`, AC-5). Neither disables the other.

Two doctrine points shape everything below:

1. **Explicit opt-in only.** Resume happens ONLY through a dedicated entry point (`attractor resume` / `resume_pipeline()` / `PipelineEngine.resume()`). A fresh `run()` **never** reads a checkpoint. AC-4's inertness (stale/foreign `checkpoint.json` cannot affect a fresh run) is achieved **by construction** — there is no code path from `run()` to `load_checkpoint()` — not by a guard that can misfire (PR #66's `CheckpointMismatchError` poisoned fresh runs precisely because the identity guard ran implicitly inside `run()`; users deleted checkpoint files to escape it).
2. **Restore state, never re-execute.** No fast-forward replay of completed nodes (#66's "Step 1b" scanned the graph from Start re-running edge selection for *every* completed node — N chances to mis-route with reconstructed outcomes). The checkpoint carries enough state that the engine continues **without re-running anything already completed**; exactly **one** edge-selection decision is made at resume, from recorded inputs.

---

## 1. §5.3 rule-by-rule mapping

Spec §5.3 "Resume behavior", quoted verbatim per rule, mapped to mechanism and the AC that proves it:

| # | Spec text (verbatim) | Mechanism | Proven by |
|---|---|---|---|
| 1 | "Load the checkpoint from `{logs_root}/checkpoint.json`." | `attractor resume <run_dir>` / `resume_pipeline(run_dir)` reads exactly `<run_dir>/checkpoint.json` through the validation ladder (§4). No other location, no search. | AC-1, AC-6 |
| 2 | "Restore context state from `context_values`." | `PipelineEngine.resume()` seeds `self.context` from `checkpoint.context_snapshot` verbatim (spec key `context`), **instead of** calling `_initialize_context()`. The snapshot already contains `graph.*` mirrors, params, `iteration`/`loop_count`, `outcome`, `preferred_label` — everything Step 4 of the main loop had written before the save. | AC-2 |
| 3 | "Restore `completed_nodes` to skip already-finished work." | `self.completed_nodes` and `self.node_outcomes` restored from the checkpoint. "Skip" is achieved **positionally**: the traversal enters *after* the last completed node (§5). There is no per-node skip check and no replay scan — completed nodes are never visited, so they trivially cannot re-execute. | AC-2 |
| 4 | "Restore retry counters from `node_retries`." | `node_retries` (now actually populated at save time — today it is written as `{}`; see §2) plus `engine_state` counters (`node_execution_counts`, `goal_gate_retries`, `failure_routing_retries`, `iteration_count`, `steps`) restored onto the engine. Observable: `execution_index` in `pipeline:node_complete` events continues from pre-crash counts; the resumed run's own subsequent checkpoints carry the pre-crash `node_retries` forward rather than `{}`. | AC-2 |
| 5 | "Determine the next node to execute (the one after `current_node` in the traversal)." | Resume re-runs **edge selection once** from the recorded position: `select_edge(current_node, recorded_outcome, restored_context, graph)`. See §5 for why this and not "record the pre-selected next node". | AC-1 (routing equivalence), AC-6 (mismatch → loud) |
| 6 | "If the previous node used `full` fidelity, degrade to `summary:high` for the first resumed node, because in-memory LLM sessions cannot be serialized. After this one degraded hop, subsequent nodes may use `full` fidelity again." | One-shot, engine-armed fidelity cap on the **first resumed hop**, applied when that hop *resolves* to `full`; recorded in the run's own records (event + context log + the node's rendered prompt). See §6. | AC-3 |

DoD `:1856` ("Checkpoint is saved after each node completion (current_node, completed_nodes, context, retry counts)") is already satisfied on the write side except that retry counts are silently empty — §2 fixes that as part of the schema delta.

---

## 2. Checkpoint schema delta (v1 → v2)

### Today (implicit v1) — written by `checkpoint.py::save_checkpoint()`

```json
{
  "current_node": "<last completed node id>",
  "completed_nodes": ["a", "b"],
  "context": { "...": "PipelineContext.snapshot()" },
  "timestamp": "ISO-8601",
  "node_retries": {},          // field exists; engine NEVER populates it (checkpoint.py:53-59, engine.py:1297-1316)
  "logs": ["..."]
}
```

The module's own docstring: *"The engine always starts from the graph's start node — the checkpoint is an observability record, not a resume marker."* That sentence is retired by this design.

### v2 — superset, spec keys unchanged (maintainer constraint: "any extension must remain a superset keeping the §5.3 fields and the §5.6 `{logs_root}/checkpoint.json` location")

```json
{
  "schema_version": 2,
  "run_state": "in_flight",                    // "in_flight" | "completed"
  "current_node": "b",                          // unchanged: ID of the LAST COMPLETED node (spec §5.3)
  "completed_nodes": ["a", "b"],                // unchanged
  "context": { "...": "..." },                  // unchanged
  "timestamp": "...",                           // unchanged
  "node_retries": {"b": 1},                     // spec field, NOW POPULATED: retries consumed by each completed node (outcome.attempt_count - 1)
  "logs": ["..."],                              // unchanged
  "node_outcomes": {                            // NEW: routing/gating subset of each completed node's Outcome
    "b": {"status": "success", "preferred_label": "ship", "suggested_next_ids": null,
           "is_explicit": true, "failure_reason": null, "notes": "..."}
  },
  "engine_state": {                             // NEW: engine counters the loop carries across nodes
    "iteration_count": 0,
    "node_execution_counts": {"a": 1, "b": 2},
    "goal_gate_retries": 0,
    "failure_routing_retries": 0,
    "steps": 3
  },
  "graph": {                                    // NEW: identity + self-contained resume
    "fingerprint": "sha256:<hex of dot_source>",
    "dot_source": "digraph { ... }"
  }
}
```

**Why each addition is load-bearing (nothing speculative):**

- `schema_version` — future changes fail loud, not silent. Resume refuses anything ≠ 2 with an actionable message ("this checkpoint predates resume support / was written by a newer engine"). Absent version ⇒ v1 ⇒ refusal (v1 checkpoints genuinely lack resume state).
- `run_state` — a final write flips it to `"completed"` when `run()` returns its final Outcome. Resuming a finished run is refused loudly ("nothing to resume; run finished with status X") instead of re-running the terminal/gate logic ambiguously.
- `node_retries` populated — rule 4 / DoD `:1856` demand it; the value is available at save time as `outcome.attempt_count - 1` (`retry.py` sets `attempt_count` on every outcome that passes through `execute_with_retry`).
- `node_outcomes` — required by three consumers the old design starved: (a) `_check_goal_gates()` iterates `self.node_outcomes` checking `is_success and is_explicit` (engine.py:1220-1249) — without restoration, a resumed run of a gated graph would find every gate unsatisfied and re-execute via `retry_target`, violating AC-2; (b) `collect_and_inject_feedback()` on a `loop_restart` edge reads `node_outcomes`; (c) rule 5's single edge-selection needs the last completed node's real outcome, not a reconstructed `SUCCESS` (a #66 failure ingredient). Only the routing/gating subset is stored; `context_updates` are NOT re-stored per node (already merged into `context`), `response_text`/`failed_step`/`session_id` are not stored (not routing-relevant; sessions are dead by definition).
- `engine_state` — the counters `run()` holds in locals or fields across loop iterations: `iteration_count` (drives `$iteration` re-seed and `iteration_N/` dirs on the next `loop_restart`), `node_execution_counts` (drives `execution_index` continuity — the machine-checkable face of "counters restore rather than reset"), `goal_gate_retries`/`failure_routing_retries`/`steps` (bounded-loop budgets continue instead of refreshing on every kill+resume — prevents an unbounded loop achievable by repeatedly killing a gate-retrying run).
- `graph` — makes resume self-contained (`manifest.json` does not store the DOT source) and makes graph-identity mismatch **checkable at the entry point only** (never inside fresh `run()` — the #66 inversion). DOT sources are KB-scale; embedding is cheap.

**`failed_outputs` is deliberately NOT persisted.** It is derivable: at restore time, for every completed node whose recorded status is `fail`/`skipped`, call the existing `_populate_failed_outputs(node_id)` (which reads the graph-derived `_output_table`). One source of truth, smaller schema.

**Write-side change on fresh runs (the one unavoidable fresh-path touch):** `_save_checkpoint()` grows the new fields. This is additive JSON content; no fresh-run *behavior* changes (nothing is read back), existing suites stay green, and the §5.3 six keys keep their exact names and shapes. Everything else in this design lives behind the explicit entry point.

---

## 3. Entry-point surface

### CLI (pipeline-runner)

```
attractor resume <run_dir> [--dot-file PATH] [--provider P] [--cwd DIR]
                 [--on-human-gate {fail,auto-approve,console}] [--param k=v ...]
```

- `<run_dir>` — the `logs_root` the interrupted run left behind (same positional convention as `attractor trace <run_dir>`).
- Graph source: by default the checkpoint's embedded `graph.dot_source`. `--dot-file` is allowed for provenance/auditing but MUST fingerprint-match the checkpoint or the ladder refuses (AC-6-loud, naming both fingerprints). There is **no override flag** — cross-graph checkpoint reuse is explicitly OUT per the maintainer's scope note ("a checkpoint binds to the run that wrote it").
- `--provider`, `--cwd`, `--on-human-gate` behave exactly as on `run` (process-level wiring cannot be serialized). `--param` is accepted for parity but restored context wins on key collisions — params were already in the snapshot; a resume-time param may only *add* keys, never silently shadow restored state (collision ⇒ loud error).
- Docs must state plainly: resume from the same working directory the run used; file-state produced by tool/agent nodes lives there and the engine cannot verify it.

### Library (pipeline-runner)

```python
async def resume_pipeline(
    run_dir: Path | str, *,
    dot_source: str | None = None,   # optional; must fingerprint-match if given
    provider: str = "anthropic",
    cwd: Path | str | None = None,
    hooks: Any = None, interviewer: Any = None,
    # same backend-injection seam run_pipeline exposes (AC-3's stub path)
) -> PipelineResult: ...
```

Mirrors `run_pipeline()`'s wiring (parse → transform → validate → build backend/registry/engine) but with `logs_root = run_dir` and `engine.resume(checkpoint)` instead of `engine.run()`. Transforms are re-applied to the DOT source exactly as a fresh run would (the fingerprint binds the *source*, which is what transforms are a pure function of).

### Engine (loop-pipeline)

```python
class PipelineEngine:
    async def run(self, goal=None) -> Outcome: ...        # UNCHANGED public surface & semantics
    async def resume(self, checkpoint: Checkpoint) -> Outcome:  # NEW
```

`run()` keeps its exact signature, docstring intent ("always starts from the graph's start node") and behavior. `resume()` is a sibling, not a mode flag on `run()` — a flagged `run(resume_from=...)` would put resume logic back inside the fresh path, the #66 shape.

### Validation ladder (order is the contract; every rung fails loud, exit non-zero, never falls back to fresh start)

```
1. exists      — <run_dir>/checkpoint.json present?            else: "nothing to resume at <path>" (AC-6 "missing")
2. parses      — valid JSON, loadable Checkpoint?               else: "corrupted checkpoint: <json error>" (AC-6 "corrupted")
3. version     — schema_version == 2?                           else: "checkpoint schema v<X> is not resumable; v2 required.
                                                                       v1 checkpoints are pre-resume observability records."
4. liveness    — run_state == "in_flight"?                      else: "run already completed with status <s>; nothing to resume."
5. identity    — fingerprint(graph to run) == checkpoint.graph.fingerprint?
                                                                else: "checkpoint was written by a different graph:
                                                                       checkpoint <fp8>… vs supplied <fp8>…; resume refused
                                                                       (side-effecting nodes must not be re-applied to a
                                                                       graph they weren't run against)." (AC-6)
6. structure   — current_node ∈ graph.nodes; completed_nodes ⊆ graph.nodes;
                 node_outcomes statuses ∈ StageStatus            else: name the offending id/value (AC-6 "structurally invalid,
                                                                       including current_node not a node of the graph")
7. THEN restore — only after 1–6 pass does any engine state mutate.
```

Rungs 1–6 live in `checkpoint.py` as `load_checkpoint_for_resume(path, graph) -> Checkpoint`, raising a single exception family `CheckpointResumeError` (subclassed per rung for tests; one message discipline: what failed, the offending value, what to do). The engine never validates — by the time `engine.resume()` runs, the checkpoint is proven. Note rung 5 is the #66 `RunIdentity` idea **relocated to the only place it can't hurt anyone**: an explicit resume request. Fresh runs never evaluate it.

---

## 4. Restore set (precisely what `engine.resume()` sets before entering the loop)

| Engine state | Source | Note |
|---|---|---|
| `context` (all keys) | `checkpoint.context` | verbatim; `_initialize_context()` NOT called (would re-seed `iteration` to 0 and re-mirror graph attrs the snapshot already holds) |
| `completed_nodes` | `checkpoint.completed_nodes` | order preserved |
| `node_outcomes` | `checkpoint.node_outcomes` → `Outcome(...)` per node | routing/gating subset; `attempt_count` rehydrated from `node_retries` |
| `iteration_count` | `engine_state.iteration_count` | `$iteration`/`$loop_count` strings already in context |
| `_node_execution_counts` | `engine_state.node_execution_counts` | `execution_index` continuity |
| `goal_gate_retries`, `failure_routing_retries`, `steps` | `engine_state.*` | passed into the loop as initial values |
| `failed_outputs` | **derived**: `_populate_failed_outputs(n)` for each completed n with fail/skipped status | see §2 |
| manifest | additive read-modify-write: append `{resumed_at, from_node}` to a `resumes` list | never rewrites `start_time`; `_write_manifest()` NOT called |
| `PIPELINE_RESUME` event (new constant in `pipeline_events.py`) | emitted with `{checkpoint_node, completed_count, iteration_count, fidelity_degrade_armed}` | the run's own record that a resume happened (feeds AC-1/AC-2/AC-3 gates) |

Then: `return await self._run_loop(entry_node=graph.nodes[checkpoint.current_node], resume_outcome=node_outcomes[current_node], ...counters)`.

---

## 5. The resume-position / routing decision (the crux)

**Decision: the checkpoint records the last COMPLETED node + its outcome; resume re-runs edge selection ONCE from that recorded outcome. The already-selected next node is deliberately NOT recorded.**

### Why Option A (record completed node + outcome, re-select at resume)

1. **It is what the spec's own save placement means.** §3.2 Step 5 saves the checkpoint *after* context updates and *before* Step 6 edge selection — the spec's checkpoint semantically cannot contain a selected next node, because selection hasn't happened yet at save time. Our engine has the same order (context updates ~:778-783 → save `:790` → `select_edge` `:852`). Rule 5's wording — "**Determine** the next node to execute (the one after `current_node` in the traversal)" — is an action performed *at resume time*, relative to the last completed node. Option A is the literal reading.
2. **The mid-node-crash case falls out for free.** Kill during node N+1 (after N's checkpoint): the checkpoint knows N completed and knows N's outcome. Resume re-selects from N → arrives at N+1 → N+1 executes. The interrupted node re-executes **because it never completed** — the one legitimate "re-run" — and it happens with zero special-case code. Its partial side effects from the crashed attempt are exactly the class the *coexisting* graph-owned file-guard pattern (AC-5) already handles for authors who care; the engine makes no idempotency promise for a node it never recorded as done (same contract as today).
3. **No stale-selected-edge state can exist.** Under Option B (record the selected next node), a crash between selection and the next node's completion pins a routing decision that (a) duplicates state derivable from `(outcome, context, graph)`, (b) can dangle if the graph changed between run and resume with no way to distinguish "routing changed" from "target renamed", and (c) would require moving or doubling the save site, touching the fresh path. Determinism makes Option A safe: `select_edge(node_id, outcome, context, graph)` is a pure function of its inputs (conditions over context, weights, lexical tiebreak — no I/O, no randomness), and every input is bit-identical at resume (recorded outcome subset; restored context including the `outcome`/`preferred_label` keys Step 4 wrote *before* the save; fingerprint-proven graph). The resumed process re-derives exactly the decision the crashed process made — AC-1's "routes off the resume point exactly as the uninterrupted one does".
4. **Terminal-node checkpoints resume correctly.** The terminal save site (`:355`) writes `current_node = <terminal>` *before* the goal-gate check. If the process dies during gate evaluation/finalization, resume enters the loop at the terminal node; the loop's Step-1 terminal branch re-runs the gate check over restored `node_outcomes` (pure) and finishes — or routes to `retry_target` exactly as the live run would have. No special code. (A run that got past finalization has `run_state="completed"` and is refused at ladder rung 4.)

### How the loop is entered (the only engine refactor)

Extract `run()`'s `while True:` body into `_run_loop(entry_node, *, resume_outcome: Outcome | None = None, counters...)`:

- `run()` = exact current preamble (`_initialize_context`, `_write_manifest`, `PIPELINE_START`, `_find_start_node`) + `_run_loop(start_node)` with `resume_outcome=None`. Behaviorally byte-identical; the fresh path never sees a live branch.
- `resume()` = restore (§4) + `_run_loop(checkpoint_node, resume_outcome=recorded)`.
- Inside the loop, exactly one guarded branch: on the **first** iteration when `resume_outcome is not None` — skip handler execution, skip re-recording completion (already in `completed_nodes`/`node_outcomes`), skip context-update application (already merged pre-crash, present in the restored snapshot), skip the checkpoint save (nothing changed), set `outcome = resume_outcome`, clear the flag, and fall through to **Step 5 edge selection and everything after it** (loop_restart handling, advance) — all shared, unduplicated code. This is not replay: no scan, no reconstruction, one decision, once.

**Failure mode when the graph changed between run and resume:** structural drift that survives the fingerprint check is impossible (the fingerprint covers the whole DOT source), so a changed graph is caught at ladder rung 5 with both fingerprints named. The residual case — same graph, but `select_edge` returns `None` at the resume hop (e.g. the recorded outcome was FAIL and the graph legitimately dead-ends) — terminates through the loop's existing no-matching-edge hard-fail (`terminate_pipeline` + `PIPELINE_ERROR error_type=no_matching_edge`, ATX-11), with the resume-hop message extended to name the checkpoint file, the recorded node and outcome status — loud, actionable, never a silent restart. This is #66's crash class #2 reduced from "N replayed chances to mis-route with fabricated outcomes" to "the same single decision the live run faced, with its real inputs."

---

## 6. Fidelity degrade (rule 6) — mechanics and observability

**Reality being honored:** `fidelity=full` continuity lives in `AmplifierBackend._thread_transcripts` — an in-memory dict of node exchanges replayed as `parent_messages` into fresh spawns (backend.py:15-26, 516-535). A killed process loses it unrecoverably. Without rule 6, the first resumed `full` hop would *silently* spawn with empty history — the exact "silently proceeds as if nothing degraded" outcome AC-3 forbids.

**Trigger interpretation (stated openly):** rule 6's condition is "the previous node used `full`". We implement: **degrade when the first resumed hop itself *resolves* to `full`.** Rationale: if the first resumed node resolves to any non-`full` mode, it gets a fresh session with a preamble *by definition* — there is nothing to degrade and `summary:high` substitution would be a semantic no-op or a wrong override of an explicit author choice; if it resolves `full`, its thread transcript is lost regardless of what the previous node used. The two readings differ only where the spec's reading does nothing useful; ours also covers the lost-transcript case the spec's literal trigger misses (prev non-full → next full). One hop only, exactly as written: after the first degraded hop completes, transcripts accumulate normally and later nodes "may use `full` fidelity again" untouched.

**Mechanism (minimal, engine-owned, stub-proof):**

1. `engine.resume()` arms a one-shot: on the first *executed* node (the resume hop), the engine — which holds the node, the selected incoming edge, and the graph — calls the existing pure `resolve_fidelity(node, edge, graph)` itself.
2. If it resolves `full`: the engine (a) emits `PIPELINE_RESUME_FIDELITY_DEGRADE {node_id, from: "full", to: "summary:high"}` (new event constant), (b) `context.append_log("resume: fidelity degraded full→summary:high for node <id> (spec §5.3 rule 6)")` — this lands durably in the run dir via the next checkpoint's `logs` field, and (c) sets the reserved one-hop context key `resume.fidelity_cap = "summary:high"`, cleared unconditionally right after the handler returns (so it can never leak into later hops or checkpoints).
3. `AmplifierBackend.run()` honors the cap in two lines after its `resolve_fidelity` call: `if context.get("resume.fidelity_cap") and fidelity == "full": fidelity = context.get("resume.fidelity_cap")`. `build_preamble("summary:high", context, completed_nodes)` then does what it already does — the ~3000-token summary is built from *restored* context and completed-node history, which is precisely the serializable state the spec says survives.

**Why the record is engine-side:** AC-3 permits a stub backend via the public injection seam, and "binds the observable record and the non-crash, not any internal rehydration mechanism." A stub replacing `AmplifierBackend` bypasses any backend-resident logging — so the durable record (event + context-log line) is produced by the engine, which runs identically under stubs. The stub merely has to not-crash; the real backend additionally applies the substitution, corroborated by the degraded node's `prompt.md` containing a summary preamble instead of nothing.

**Observability inventory for AC-3:** `PIPELINE_RESUME` event (`fidelity_degrade_armed`), `PIPELINE_RESUME_FIDELITY_DEGRADE` event, the context-log line inside subsequent `checkpoint.json` `logs`, and the node's `prompt.md`. Any one satisfies "its own records show the degraded summary:high treatment was applied for exactly that first resumed hop"; we provide four for the price of ~15 lines.

---

## 7. Run-directory continuation story (§5.6)

**Decision: resume continues IN PLACE in the same `{logs_root}`. No new run directory.**

- Rule 1 compels it: the checkpoint *is* `{logs_root}/checkpoint.json`; the state being resumed belongs to that run. A resumed run is the **same execution**, continued — §5.6's "each pipeline execution produces a directory tree" reads naturally as one tree per logical execution, not per OS process.
- `trace.jsonl` is append-only (engine.py:1416-1429): the resumed process's records append after the interrupted ones, so AC-1's union-of-executed-nodes assertion is a straight read of one file (interrupted records + resumed records = control records).
- Completed nodes' `{node_id}/status.json`, `prompt.md`, `response.md` remain untouched (their handlers never run — AC-2 evidence by mtime/content). The re-executed interrupted node overwrites its own dir, same as any live re-visit today.
- `manifest.json` gains an additive `resumes` list (§4); `start_time` and provenance survive.
- The "fresh log directory" phrase in spec §2.7 belongs to `loop_restart`, which this bundle already, deliberately, diverges from (ATX-12, ledgered: in-process reset, retained run dir, `iteration_N/` subtrees). Resume follows the shipped ATX-12 semantics — `iteration_count` restores from `engine_state` and the next `loop_restart` creates `iteration_{K+1}/` in the same tree. Inventing a new-directory story for resume would contradict both rule 1 and the ratified ATX-12 posture.

---

## 8. Interaction analysis

- **`loop_restart` / `$iteration` mid-iteration crash.** Checkpoints are written per node *inside* an iteration; `engine_state.iteration_count` records the current iteration K, and the context snapshot carries `iteration="K"`. Crash anywhere inside iteration K resumes inside iteration K with correct `$iteration`. Crash *during the node after a `loop_restart` edge* is the elegant case: the last checkpoint predates the restart transition (save precedes edge selection), so resume replays the transition through the shared Step-6 code — re-increments to K+1, re-creates `iteration_{K+1}/` (mkdir exist_ok), re-runs `collect_and_inject_feedback` (pure function of `node_outcomes`+context ⇒ same content to the same paths), re-clears `completed_nodes`/`preferred_label`, re-seeds `$iteration` — bit-for-bit what the crashed process did. Zero special code, *because* we checkpoint pre-selection (§5.1).
- **`must_write` / retry ladder.** Both live inside `execute_with_retry` — intra-node. A crash mid-attempt loses the attempt counter for the in-flight node, which is correct: that node never completed and re-executes from attempt 1 with its full budget. Completed nodes' consumed retries persist via `node_retries` (rule 4). Note honestly: restoring `node_retries` has no additional routing effect in this engine — and none in the spec's own pseudocode either, whose retry loop is equally per-invocation. It restores as observable state (post-resume checkpoints, `attempt_count` rehydration), which is what rule 4 and AC-2's "restore rather than reset" can mean here.
- **Goal gates.** `_check_goal_gates()` re-evaluates over restored `node_outcomes` including `is_explicit` (EXTENSIONS §25 fail-closed contract survives the round-trip because `is_explicit` is serialized). `goal_gate_retries` budget continues from `engine_state`. A pre-crash satisfied gate stays satisfied; a resumed terminal checkpoint re-runs the check identically.
- **Parallel nodes.** Branch clones set `_checkpoint_path = None` (S5, engine.py:219-220) — branches never checkpoint, so no checkpoint can ever exist "inside" a parallel region. A crash mid-parallel resumes from the last top-level checkpoint before the parallel node; the parallel node re-executes whole (it never completed). **Resume is top-level-only in v1, honestly stated** — matching the maintainer's OUT list verbatim ("branch clones deliberately never checkpoint... a resume re-executes the interrupted top-level node from its start; follow-up issue if community graphs surface the need"). The ladder needs no "inside a branch" rung because the state is unrepresentable.
- **Human gates.** A run killed while parked at a `wait.human` node resumes by re-asking the question (node never completed) through whatever interviewer the resume invocation wires — the natural behavior; documented, not special-cased.
- **Events/hooks.** Consumers see a second `PIPELINE_RESUME`-opened event stream in the same run dir. `execution_index` continuity (restored `_node_execution_counts`) keeps per-node ordinals monotonic across the boundary, so timelines don't double-count.

---

## 9. Explicit NON-goals

1. **Auto-resume.** No implicit checkpoint read-back ever; `attractor run` on a dir containing a checkpoint overwrites it at the first node completion, exactly as today. (AC-4 by construction.)
2. **Fast-forward replay.** No re-walking from Start, no per-completed-node edge re-selection, no reconstructed outcomes. (#66 Step 1b, designed out.)
3. **Branch-interior resume.** Out of scope v1 (see §8); named follow-up per the maintainer's OUT list.
4. **Cross-run / cross-graph checkpoint reuse.** Fingerprint mismatch refuses, no override flag; follow-up issue per maintainer.
5. **Checkpoint-every-K / mid-node checkpoints / time-based checkpoints.** Save sites are unchanged (the three existing ones).
6. **Serializing live LLM sessions.** Excluded by the spec itself (rule 6 exists because it's impossible).
7. **Distributed anything** (locks, remote state, concurrent resumers). Single-process, local filesystem, same trust model as today.
8. **Retry-budget semantics changes.** `node_retries` restores as data; no new cross-invocation retry enforcement is invented.

---

## 10. Risk register — the two #66 crash classes, structurally prevented

| # | #66 crash class | Old mechanism that caused it | Structural prevention (not a guard) |
|---|---|---|---|
| 1 | `CheckpointMismatchError` poisoning **fresh** runs (users deleted `checkpoint.json` between runs to escape; wiki repo did exactly this) | `run()` implicitly called `_try_resume_from_checkpoint()` on every start; the identity guard therefore executed on runs that never asked to resume | Fresh `run()` contains **no call** to `load_checkpoint`/ladder/fingerprint code — the identity check exists only inside `load_checkpoint_for_resume()`, reachable only from the explicit `resume` entry points. A fresh run cannot hit a mismatch error because it never evaluates identity. Inertness is a property of the call graph, not a conditional. |
| 2 | `"No matching edge from resumed node"` (amplifier-resolver-dot-graph) | Step 1b replayed edge selection for *every* completed node while walking from Start, using reconstructed outcomes (`completed_nodes` as a status dict; default `Outcome(SUCCESS)` when missing; `preferred_label` persisted separately from context) — N decisions × degraded inputs | Exactly **one** edge selection at resume, from the recorded full routing subset of the real outcome plus the verbatim restored context (`outcome`/`preferred_label` keys included, having been written before the save). `select_edge` is deterministic over those inputs and the fingerprint-proven graph ⇒ the resumed decision *is* the crashed process's decision. If it still dead-ends (graph legitimately dead-ends there), the failure is the loop's existing loud ATX-11 hard-fail, enriched with checkpoint provenance — a correct report, not a crash class. |

Residual risks, named: (a) **non-serializable context values** — `save_checkpoint` uses `default=str`, so an exotic object round-trips as a string; mitigation: none new (the spec's own artifact-store guidance §5.5 already says context holds "only small scalar values for routing and checkpoint serialization"); documented. (b) **external world drift** (files/branches changed between crash and resume) — out of the engine's epistemic reach; the graph-owned guard pattern (AC-5) is the sanctioned tool, and the docs cross-link the two patterns as complements. (c) **`--cwd` mismatch on resume** — documented instruction, not enforceable.

---

## 11. Test strategy per AC

**Shared kill-fixture harness** (new `modules/loop-pipeline/tests/` + runner-level e2e): deterministic, tool-only graph `start → a → b → c → d → exit`, tool nodes only (`shape=parallelogram`, `tool_command=...`), no LLM, no network.

- Interruption is **real**: the test launches `attractor run fixture.dot --logs-root <dir>` via `subprocess.Popen` (new session/process group), and node `c`'s command is `if [ -f "$BLOCK" ]; then touch c_started; sleep 300; fi; echo c-done` with `BLOCK` present. The parent polls for `c_started` (bounded wait), then `os.killpg(SIGKILL)` — an actual hard termination after node `b`'s checkpoint write, per the criteria's "killed, or an equivalent hard stop of the process — not a simulated interrupt inside a single process". Then it removes `BLOCK` and invokes `attractor resume <dir>` as a genuinely separate process.
- **Control run at gate runtime**: same graph bytes, `BLOCK` absent, fresh dir — never a committed golden.

Per AC:

- **AC-1 (equivalence):** compare resumed-final vs control-final: CLI exit status + final outcome status; final `checkpoint.json` `context` and `completed_nodes` (normalizing inherently-run-varying fields: `timestamp`, `logs`, duration-bearing values); per-node artifact files written by tool commands. Union assertion: interrupted `trace.jsonl` prefix (a,b) ⊎ resumed appended records (c,d) == control's (a,b,c,d), and routing off `b` identical.
- **AC-2 (rules 2–5):** nodes `a`/`b` append to `runs.log` files — line counts stay 1 after resume (handlers demonstrably did not re-run); resumed process's trace/events contain no `node_start` for a/b. Context restoration: `b` emits a context value (via `tool.last_line`/`outputs=`), `d`'s command substitutes it into a file — content proves visibility *and* behavioral effect. Retry counters: `b` succeeds on attempt 2 (`[ -f b_try ] || (touch b_try; exit 1)`, `max_retry=2`) ⇒ pre-crash checkpoint has `node_retries.b == 1`; assert the resumed run's final checkpoint still carries `1` (restored, not reset) and `execution_index` continuity in events.
- **AC-3 (rule 6):** LLM-shaped fixture `l1(full) → l2(full, same thread) → l3(full)` driven by a stub backend through `resume_pipeline`'s public backend-injection seam; stub blocks on a signal file during `l2`; SIGKILL; resume with a fresh stub. Assert: process completes (non-crash); `PIPELINE_RESUME_FIDELITY_DEGRADE` for exactly `l2` appears in the resumed run's records, and the context-log line lands in the next checkpoint's `logs`; no degrade record for `l3` (later nodes free to resolve `full`). A real-backend unit test separately asserts the cap substitution (`resolve→full` becomes `summary:high` preamble in the rendered prompt).
- **AC-4 (guard):** full existing loop-pipeline + pipeline-runner suites unmodified and green. New tests: plant a foreign/corrupt/v1 `checkpoint.json` in `--logs-root`, run `attractor run` fresh — identical behavior to a clean dir (assert vs a clean-dir control), file simply overwritten; assert-by-grep/CI check that `engine.run()`'s call graph contains no `load_checkpoint` reference (construction-level inertness, enforced).
- **AC-5 (guard):** byte-equality check on `examples/pipelines/12-graph-resume.dot` in the PR diff (untouched), plus its existing lint/parse/execution coverage re-run as-is; one integration test running the example's guard-skip semantics on a fresh run from Start.
- **AC-6 (loud failures):** table-driven ladder tests — missing file, truncated JSON, `schema_version` absent/1/99, `run_state=completed`, fingerprint mismatch (edited DOT), `current_node="ghost"`, `completed_nodes` containing an unknown id, invalid status string. Each: non-zero exit, message names the specific problem and the remedy, **and** the run dir is untouched (no node executed, no checkpoint rewritten) — proving "never silently falls back to restarting from start" in both exit code and side-effect absence.

---

## 12. File-touch inventory and build order

| File | Change | Rough size |
|---|---|---|
| `modules/loop-pipeline/amplifier_module_loop_pipeline/checkpoint.py` | v2 fields on `Checkpoint`; serialize/deserialize `node_outcomes`/`engine_state`/`graph`/`run_state`/`schema_version`; `load_checkpoint_for_resume()` ladder; `CheckpointResumeError` family; retire the "not a resume marker" docstring | ~+180 lines |
| `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` | extract `run()` body → `_run_loop(entry, resume_outcome=None, counters)` (mechanical); new `resume()` (~restore + enter loop); `_save_checkpoint()` enrichment (populate `node_retries` from `attempt_count`, outcomes subset, engine_state, graph identity, `run_state`); final-outcome `run_state="completed"` write; resume-hop fidelity arm + events; resume-hop no-edge message enrichment | ~+170 / ~40 moved |
| `modules/loop-pipeline/amplifier_module_loop_pipeline/pipeline_events.py` | `PIPELINE_RESUME`, `PIPELINE_RESUME_FIDELITY_DEGRADE` | +2 constants |
| `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py` | honor one-hop `resume.fidelity_cap` after `resolve_fidelity` | ~+8 |
| `modules/pipeline-runner/amplifier_module_pipeline_runner/runner.py` | `resume_pipeline(run_dir, ...)` reusing `run_pipeline`'s wiring | ~+70 |
| `modules/pipeline-runner/amplifier_module_pipeline_runner/cli.py` | `resume` subcommand + dispatch | ~+70 |
| `modules/loop-pipeline/tests/test_checkpoint.py` | v2 write-side coverage | ~+60 |
| `modules/loop-pipeline/tests/test_resume_validation.py` (new) | ladder table tests (AC-6) | ~+160 |
| `modules/pipeline-runner/tests/test_resume_e2e.py` (new) | kill-fixture harness, AC-1/2/3/4 scenarios | ~+350 |
| `docs/designs/DESIGN-engine-resume.md` | this document | — |
| `SPEC_CONFORMANCE.md` | ATX-2 → DONE on merge (in the implementation PR) | ~5 |
| **Untouched, verified so** | `examples/pipelines/12-graph-resume.{dot,md}` (byte-identical); `fidelity.py`; `retry.py`; `outcome.py`; `edge_selection.py`; all handlers | 0 |

**Build order for the implementer:**

1. `checkpoint.py` v2 write-side + dataclass + unit tests (fresh suites must stay green here — proves superset).
2. `checkpoint.py` validation ladder + error family + AC-6 table tests (no engine involvement yet).
3. `engine.py` `_run_loop` extraction as a **pure refactor commit** — full existing suites green before any resume code exists.
4. `engine.py` `_save_checkpoint` enrichment + `run_state` final write.
5. `engine.py` `resume()` + restore set + resume-hop branch + events.
6. Fidelity arm + `backend.py` cap + AC-3 unit coverage.
7. `runner.py` `resume_pipeline` + `cli.py` `resume` verb.
8. Kill-fixture e2e harness; AC-1/AC-2/AC-3 gates.
9. AC-4/AC-5 guard tests; SPEC_CONFORMANCE flip; docs cross-link (12-graph-resume.md gains a "see also: engine resume" pointer — additive prose only, `.dot` untouched).

---

## 13. Spec ambiguities encountered and resolutions taken

1. **"continue from current_node" (DoD `:1857`) vs "the one after current_node" (rule 5).** "From" could read as re-executing `current_node`. Resolution: rule 5's parenthetical and §5.3's field definition ("`current_node`: ID of the **last completed** node") disambiguate — continue from the position *after* it. Re-executing a completed node would also violate AC-2 outright.
2. **Rule 6's trigger** ("previous node used full") vs the lossy case it misses (previous non-full, next full). Resolution in §6: degrade when the first resumed hop *resolves* full — a behavioral superset that is a no-op precisely where the literal trigger demands a no-op.
3. **Rule 4's `node_retries` restoration has no routing consequence** in either this engine or the spec's own pseudocode (both scope retry loops per invocation). Resolution: restore as observable state; AC-2's oracle is the recorded/carried-forward values and `execution_index` continuity, not a behavioral retry change. Flagged rather than inventing cross-invocation retry semantics the spec never defines.
4. **§2.7 "fresh log directory" vs §5.6 single run tree** for continuation. Resolution in §7: continue in place; the fresh-directory language belongs to `loop_restart`, already a ratified divergence (ATX-12).
