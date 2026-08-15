# The Objective Layer — an objective-runner exemplar for amplifier-bundle-attractor

**Status:** SHIPPED as an exemplar. Designed and probed 2026-08-14; built, live-proved,
and merged into `examples/objective/` in the PR that also lands this document. The
design below is preserved as written; §11 records what actually changed on contact
with the build, and §12 records the maintainer rulings that closed §10's open
questions.
**Destination:** `docs/designs/` in `microsoft/amplifier-bundle-attractor`.
**Charter:** Maintainer ruling 2026-08-14 — build "attractor + one layer up" **in-bundle** as a
**shipped exemplar** plus guidance-wave improvements, explicitly NOT a platform service and NOT
Resolve/TeamPulse integration. Vision anchor (maintainer's recorded design conversation):
*"The user might never pick an attractor explicitly. The system infers it from the objective."*
Node semantics: nodes publish *objective, constraints, available tools, required evidence, exit
conditions* — "edges might represent 'objective not satisfied' instead of 'next step.'"
Hierarchy: **orchestrator RUNS, graph DEFINES, model ADAPTS**; layering **objective →
composition → attractors → execution**.

All engine facts below were probed against a `/tmp` worktree of `origin/main` @ `02fcaa8`
("docs: ship the human-facing explainer page + its drift guard") using the standalone
`attractor` CLI from `modules/pipeline-runner` (uv-synced, real engine, real runs). Probe
artifacts live under `/tmp/objprobe/{p1..p5}` (graphs, scripts, run logs, checkpoints).

---

## 1. The objective-runner graph

### 1.1 What the exemplar is

One shipped `.dot` — `examples/objective/objective-runner.dot` — whose **basin is "the stated
objective is satisfied with machine evidence, or honestly redirected."** It is invoked with an
*objective*, not a pipeline choice:

```bash
cd <workspace>
attractor run $BUNDLE/examples/objective/objective-runner.dot \
    --param goal="users report a crash when saving a file whose name contains unicode" \
    --param target_dir=$PWD \
    --cwd .
```

The objective travels as the canonical `goal` param (probe P2, §2: flat `--param` keys reach
every child pipeline via the cloned context, and the bundle's practical pipelines already
consume `$goal` — so *the objective IS the goal*, no adapter layer). `target_dir` follows the
`task-runner.dot` convention (it is prompt/path text; tool-node cwd comes from `--cwd`).

### 1.2 Node/edge sketch

```
start(Mdiamond)
  → frame(box)                       # diagnose: write .objective/triage.json + objective.md
  → triage_gate(parallelogram)       # machine-validate triage; print routing token
      —bugfix→   lane_bugfix(folder → ../pipelines/practical/bug-fix.dot)
      —feature→  lane_feature(folder → ../pipelines/practical/feature-build.dot)
      —refactor→ lane_refactor(folder → ../pipelines/practical/refactor.dot)
      —testgen→  lane_testgen(folder → ../pipelines/practical/test-gen.dot)
      —review→   lane_review(folder → ../pipelines/practical/pr-review.dot)
      —compose→  compose(box)        # WRITE a purpose-built child .dot + dod.sh
                   → lint_gate(parallelogram: attractor lint <generated>)
                   → contract_gate(parallelogram: check_child_contract.py)
                   → run_child(folder → $target_dir/.objective/generated/child.dot)
      —redirect→ redirect_report(box) # the honest no: recipe/conversation, with quotes
      —triage_bad→ frame              # corrective: malformed triage (bounded fuse)
  lanes/run_child → evidence_gate(parallelogram, goal_gate=true)
      —evidence_ok→  approve(hexagon human gate)
      —evidence_bad→ feedback(box) → back to the lane/compose entry (corrective, spends budget)
      —exhausted→    postmortem(box) → escalated(parallelogram, exit 1)   # LOUD red terminal
  approve —[R] Reject→ feedback ; —[A] Approve→ finalize
  redirect_report → finalize(parallelogram)  # requires a disposition artifact
  finalize → done(Msquare)                   # the ONE exit (engine rule, §2 P-extra)
  any box —outcome=fail→ postmortem → escalated
```

Two honest terminals, expressed in the engine's own verified idioms (probe P-extra in §2:
`validate_or_raise` **requires exactly one exit node**, so "two terminals" cannot be two
`Msquare`s):

- **Honest no / redirect** — a *successful* completion whose deliverable is
  `.objective/redirect.md` ("this wants a recipe/conversation, not an attractor"), reaching the
  single green exit through `finalize`, which mechanically requires a disposition artifact.
- **Budget-exhausted escalate** — the `bug-fix.dot` idiom: `escalated` writes
  `.objective/postmortem/escalation.md` then `printf escalated; exit 1` with `max_retries=0`
  and no fail-route — the engine hard-fails LOUD (run `status=fail`, CLI exit 1; probed).

### 1.3 Node contracts (Objective / Constraints / Capabilities / Required evidence / Exit)

Per the vision's node semantics, every node's header comment in the shipped `.dot` carries this
contract block. The load-bearing ones:

| Node | Objective | Constraints | Capabilities | Required evidence | Exit condition |
|---|---|---|---|---|---|
| `frame` (box) | Classify `$goal` with the repo's three-question test (PIPELINE_DESIGN_PRINCIPLES §0) and choose a shape | Do NOT do the work; do NOT self-route; vocabulary fixed; must name a machine `evidence_command` or `NONE` | read repo, read `$goal` | `.objective/triage.json` (schema-valid) + `.objective/objective.md` (restated objective, constraints, required evidence) | triage.json exists and parses |
| `triage_gate` (tool) | Admit only well-formed triage | code-tier only; vocabulary equality is exact lowercase | `python` + `triage-schema.json` | validated triage.json; prints ONE token from the vocabulary | token printed && exit 0; malformed → `triage_bad` (fuse-bounded) |
| `lane_*` (folder) | Satisfy the objective via the shipped practical pipeline | child carries its OWN gates (already true of every `practical/*.dot`); fresh session boundary (EXTENSIONS §11) | child graph's own | the child's own evidence files (e.g. `.ai/test.log`, artifacts in workspace) | child terminal outcome returns to parent (P3) |
| `compose` (box) | WRITE `child.dot` + `dod.sh` satisfying the composed-child contract (§4) | gates OUTSIDE workers; single exit; cycle + budget mandatory; build ONLY from bundle-shipped patterns (§4.3) | write files; read `patterns/`, `gates/`, contract doc | `$target_dir/.objective/generated/child.dot`, `dod.sh` | files exist (checked by next gates) |
| `lint_gate` (tool) | Machine-reject broken generated graphs BEFORE execution | ERRORs block; warnings pass (contract_gate owns shape checks) | `attractor lint` (exit 1 on errors — probed P5) | `lint-report.txt` | `lint_pass` token && exit 0; `lint_fail` → compose_fix loop |
| `contract_gate` (tool) | Enforce the composed-child contract structurally | deterministic checks only | `check_child_contract.py` | `contract-report.txt` | `contract_ok` / `contract_bad` |
| `run_child` (folder) | Execute the generated child | dot_file resolves lazily at node execution (P1) | child graph | child's evidence files under workspace | child outcome |
| `evidence_gate` (tool, `goal_gate=true`) | Verify the objective is satisfied by RE-RUNNING the DoD — never by the child's self-report (P3) | file-based only; owns the iteration fuse (`.objective/iter` vs `$max_iterations`) | `bash dod.sh` / `evidence_command`, anchored-delta gate (`examples/gates/base-sha-anchor.dot` idiom) | `evidence-<n>.log`, appended `.objective/convergence.jsonl` | `evidence_ok` / `evidence_bad` / `exhausted` |
| `approve` (hexagon) | Consequential human approval | choices from edge labels; safe choice FIRST (unattended auto-approve picks first — task-runner doctrine); CLI default `--on-human-gate fail` stays honest | human | recorded gate answer in run log | Approve / Reject |
| `redirect_report` (box) | The honest no, with receipts | must quote the failing three-question answers; must name the better home (recipe / conversation / one-shot) | write file | `.objective/redirect.md` | file exists (finalize checks) |
| `postmortem` (box) → `escalated` (tool) | Salvage value from a non-converging run, then fail LOUD | escalated: `max_retries=0`, `exit 1`, no fail-route | write files | `.objective/postmortem/report.md`, `escalation.md` | pipeline `status=fail`, CLI exit 1 |
| `finalize` (tool) | Refuse a green exit without a disposition | code-tier | file test | `.objective/disposition` = satisfied\|redirected + the artifact it points to | exit 0 only if disposition artifact present |

**Budget fuse arithmetic (shown, not implied).** Engine safety net: `max_steps = nodes × 50`
(`engine.py:137` `_MAX_GOAL_GATE_RETRIES: int = 50`, `engine.py:611`) — for this ~16-node graph,
800 steps; the exemplar's own fuses fire far earlier. Runner fuses: `triage_bad` loop ≤ 2
re-frames (`.objective/frame-iter`); compose-fix loop ≤ `$max_compose` (default 2 → at most 3
lint/contract attempts); evidence loop ≤ `$max_iterations` (default 3) counted in
`.objective/iter` by `evidence_gate` itself, `bug-fix.dot`-style, so *every* re-entry spends
budget. Worst-case LLM-node executions ≈ frame(3) + compose(3) + per-evidence-iteration child
runs(3 × child's own internal budget, default 6) + feedback(3) + postmortem(1) — bounded and
inspectable. Each composed child additionally carries its own `$max_iterations` wall (§4).

---

## 2. Probe results — verified engine facts

Method: real runs of the standalone CLI (`attractor run` / `attractor lint`) from
`/tmp/objprobe-wt` (git worktree of `origin/main` @ `02fcaa8`), tool-node graphs for
determinism plus two live LLM runs for the intake idiom. Quotes are verbatim from runs, source,
or `git show origin/main:<path>`.

### P1 — Write-then-run composition: **WORKS TODAY (lazy resolution), and validation/lint do NOT check existence**

- Run-proven: a 3-node graph — tool node writes `gen/child.dot`, `shape=folder,
  dot_file="gen/child.dot"` executes it. Result: `attractor: status=success`; the child's own
  artifact `child-proof.txt` appeared (`child-evidence-1786761403`). The file did not exist at
  parse/validate time.
- Source-of-record: EXTENSIONS §10 — resolution is a precedence chain, *"the first non-empty
  candidate wins, **with no existence check**"*; the handler opens the file only at node
  execution (`handlers/pipeline.py` step 3) and returns
  `Outcome(FAIL, "Child DOT file not found: …")` if absent.
- Validate/lint phase: with the target absent, `attractor lint parent.dot` produced only
  `WARNING: [acyclic_graph] …` and **rc=0** — no missing-file diagnostic. A never-written
  target fails **late and loud** at run time:
  `[PIPELINE] ✗ Error at child (no_matching_edge): Child DOT file not found:
  /tmp/objprobe/p1/never/exists.dot` → `attractor: status=fail`, CLI exit **1**.
- Open issue **#200** confirms and complains about exactly this (*"resolved … with no check
  that the resulting file actually exists"*). **Compat watch (finding F2, §2.6):** #200's fix
  must not become a parse-time admission gate, or write-then-run composition dies.

### P2 — Params/context INTO a child: **context clone + `context.*` attrs; pass the objective as `goal`**

Run-proven with parent → `child.dot` (folder) → child tool node:

```
objective=[prove-param-flow-p2]        # --param objective=…  → flat context key → cloned into child (handler step 6) → $objective substituted in child tool_command
mission=[injected-by-folder-attr]      # folder-node attr context.mission=…  → injected into child context (step 6b)
goal=[]                                # $goal is NOT a substitution key in tool_command …
graph.goal=[parent-goal-sentinel]      # … but ${graph.goal} IS, inherited from the parent via the clone
```

EXTENSIONS §21 covers `$param`/`${key}` substitution; `PipelineHandler.execute()` step 6
(`context.clone()`) and step 6b (`context.*` attr injection) are the mechanisms. Note the
folder handler also **clears `preferred_label`** at the boundary (step 6a — stale-label
hygiene) and child goal falls back to the parent's (`child_graph.goal or
context.get("graph.goal")`, step 10). Design consequence: invoke the runner with
`--param goal="<objective>"` so lanes and composed children consume it natively; `context.*`
attrs are the per-lane knob channel (probed again in P4: `context.lane` reached the child).

### P3 — What comes BACK from a child: **verbatim terminal outcome + declared `outputs=` merge + (leaky) `context_updates` — so the gate reads FILES**

Run-proven (`outputs="verdict,artifact_path"` on the folder node; child emits JSON via
`parse_json=true`):

- Parent checkpoint recorded the folder node's outcome as the child's terminal outcome
  **verbatim** (handler step 12): `{"status": "success", "preferred_label": null, …,
  "is_explicit": true, "notes": "Tool completed: bash emit_verdict.sh"}`.
- Declared keys merged on success (step 11b2) and **drove parent routing**: path
  `['start', 'child', 'route_pass']` via `condition="context.verdict=pass"`.
- **Leak (fact, not bug):** the child's *undeclared* `undeclared_key: 'should-not-cross'` ALSO
  appeared in parent context — the child's terminal outcome carries `context_updates`, and the
  parent engine applies them generically (`engine.py:1142` — *"Step 4: Apply context updates
  from outcome"*). The folder boundary is therefore **leakier than `outputs=` suggests**.
- Fail path: #182/#172 fixed `failure_reason` propagation and subgraph-dead-end silent success;
  the missing-file FAIL in P1 surfaced loudly in the parent.

**Design ruling:** the parent `evidence_gate` trusts only (a) files on disk and (b) its own
re-execution of the DoD command. Child `status` is used solely for loud fail-routing; merged
context keys are routing hints, never evidence. This is the doc'd doctrine made mechanical:
*"Verification inside the context that produced the evidence is not verification"*
(PIPELINE_DESIGN_PRINCIPLES §0, the fabricated `convergence.jsonl` incident).

### P4 — Conditional routing among folder nodes: **evidence-token routing proven; live LLM run exposed a verdict-transport gap (F1)**

- Deterministic idiom (run-proven twice): one intake tool node prints a token; edges
  `condition="context.tool.last_line=<token> && outcome=success"` (ROUTING-REFERENCE's
  stale-label discipline: *"conjoin `&& outcome=success` onto every last_line edge that shares
  a source node with a failure edge"*). Objective "users report a crash on save" → path
  `['start', 'intake', 'lane_bugfix']`; "please add a feature for csv export" →
  `['start', 'intake', 'lane_feature']`. The doctrine's other idiom — `outcome=<label>`
  resolving `preferred_label` first — is ledgered as EXTENSIONS §22 / ATX-5 and covered by
  repo tests.
- Live LLM run 1 (box intake, plain instruction to use `report_outcome`): the spawned agent
  answered in prose; engine recorded `is_explicit=false, preferred_label=None,
  notes="Plain text response: **Classification: `bugfix`**…"`; **no edge matched** and the
  engine failed LOUD exactly as EXTENSIONS §33 promises:
  `[PIPELINE] ✗ Error at intake (no_matching_edge): No matching edge from node 'intake'`.
- Live LLM run 2 (strong MUST-call prompt + catch-all
  `condition="outcome!=bugfix && outcome!=feature && outcome!=novel && outcome!=fail"` →
  bounded reprompt loop): the catch-all corrective edge **fired and looped** (9 cycles observed
  before I killed the run) — and inside one completed intake session the agent **did** call the
  tool: events.jsonl shows `tool:pre {"tool_input": {"preferred_label": "bugfix", "status":
  "success"}, "tool_name": "report_outcome"}` and `tool:post … "result": "{\"message\":
  \"Outcome reported: success (preferred_label=bugfix)\""` — yet the engine still recorded
  `preferred_label=None, is_explicit=False` for that node. **The verdict was lost between the
  spawned agent and the parent engine on the standalone-CLI spawn path.** → Finding **F1**
  (§2.6).

**Design ruling:** the intake routes on a **machine artifact + code-tier token gate**
(`frame` writes `triage.json`; `triage_gate` validates and prints the token). That is (a) immune
to F1, and (b) independently better doctrine — routing evidence lives outside the worker's
self-report, exactly like every shipped gate. `preferred_label` remains the documented idiom
for nodes that legitimately steer themselves; the exemplar simply refuses to hang the *first*
routing decision on an unverified LLM verdict.

### P5 — `attractor lint` as an in-graph gate on a GENERATED graph: **works; exit 1 on ERRORs only; `--strict` promotes warnings**

Run-proven end to end, generated-then-linted inside one pipeline:

- Deliberately broken generated child (TOPO-001 dead conditional edge):
  in-graph gate `…/attractor lint gen/child-candidate.dot && echo lint_pass || echo lint_fail`
  routed to `blocked` — path `['start', 'gen', 'lint_gate', 'blocked']`. Execution of the broken
  child was **prevented by machine evidence**. Report: `ERROR: [dead_conditional_edge] …` /
  `attractor lint: gen/child-candidate.dot: 1 error(s), 2 warning(s)`; direct exit code **1**.
- Clean-but-warning child (acyclic → `WARNING: [acyclic_graph]`): exit **0**, gate printed
  `lint_pass`, path `['start', 'gen', 'lint_gate', 'run_child']`, child artifact created.
  `--strict` on the same file: exit **1**. Matches EXTENSIONS §32's contract: *"errors → exit
  1, warnings → exit 0 unless `--strict`"*.
- Bonus: lint on a *missing* path exits 1 (`attractor lint: DOT file not found: …`) — the gate
  also catches "the composer never wrote the file." And origin/main lint already includes
  TOPO-006 `fail_routed_to_exit` (the silent-success class) — the contract checker doesn't need
  to re-implement it.

### P-extra — facts discovered by probing that shape the design

- **Exactly one exit node** is admission-gating: `ValidationError: Validation failed: Pipeline
  has 2 exit nodes (pass_exit, fail_exit); exactly one is required` (CLI exit 1, run refused).
  Honest terminals must be one green exit + a fail-loud escalation node (§1.2).
- Engine step cap: `max_steps = len(graph.nodes) × 50` (`engine.py:137,611`).
- A FAIL outcome is fail-fast (no plain-edge drift; EXTENSIONS §16), and no-matching-edge is a
  LOUD hard fail (§33) — both observed live in P4.

### 2.6 Gap findings (named, not designed around silently)

- **F1 — spawned-agent `report_outcome` verdict lost on the standalone CLI path.** Evidence in
  P4: tool call succeeded in the child session (`tool:post` result *"Outcome reported: success
  (preferred_label=bugfix)"*) but the node outcome came back `is_explicit=false,
  preferred_label=None`. EXTENSIONS §35 documents the intended transport
  (`orchestrator:complete` → `metadata.report_outcome` → `backend.py` outcome reconstruction,
  *"report_outcome tool call was made → authoritative"*, backend.py:814). The mounted-
  orchestrator path has live e2e coverage (`profiles/attractor-e2e-*.yaml` mount
  `tool-report-outcome`); the loss reproduced only via `pipeline-runner`'s
  `make_spawn_fn` → `prepared.spawn` path. **Smallest fix shape:** ensure the spawn-result dict
  returned through `make_spawn_fn` carries the child's `metadata.report_outcome` envelope to
  `AmplifierBackend._run_with_spawn`'s reconstruction (one seam, additive; plus a
  pipeline-runner e2e asserting `is_explicit=true` after a child `report_outcome`). The
  exemplar does not depend on the fix (artifact-token intake), but the fix unblocks
  `preferred_label` intake as a documented alternative.
- **F2 — issue #200 vs write-then-run (compat watch, not a bug).** The requested "upfront
  existence check" must stay out of `validate_or_raise`, or runtime-generated `dot_file`
  targets (this exemplar's compose path, and any future composition layer) become impossible.
  Proposed shape: keep admission lazy; improve the *node-entry* error to name the candidate
  chain (kills the misleading `no_matching_edge` framing #200 quotes); optionally a lint
  WARNING when a *static* relative target is absent — advisory, per §32's entry-point
  discriminator.
- **F3 — `$goal` is not a tool-command substitution key** (P2: `goal=[]` vs
  `graph.goal=[parent-goal-sentinel]`). Not a bug — but guidance-worthy: children reference
  `${graph.goal}` or a passed `goal` param. One paragraph in the authoring guide (§6).

---

## 3. Select vs compose vs redirect — the intake decision logic

`frame` applies the repo's own three-question test (PIPELINE_DESIGN_PRINCIPLES §0: cycle? /
evidence-gated exit? / survives one LLM bad day?) *prospectively* to the stated objective, and
writes `triage.json`:

```json
{
  "shape": "bugfix | feature | refactor | testgen | review | compose | redirect",
  "three_question": {"cycle": "...", "evidence_gate": "...", "bad_day": "..."},
  "evidence_command": "<shell command that exits 0 iff the objective is satisfied, or NONE>",
  "rationale": "..."
}
```

**Routing vocabulary** = exactly the eight tokens above plus `triage_bad` (emitted by the gate,
not the model). Tokens are single lowercase words because condition matching is exact string
equality (ROUTING-REFERENCE §3).

Decision logic, enforced mechanically by `triage_gate` rather than trusted:

1. **redirect** iff `evidence_command == NONE` **or** the test says no cycle is wanted — *no
   attractor without machine evidence* is a schema rule, not a vibe: the gate rejects any
   non-redirect shape whose `evidence_command` is `NONE`. (The honest no is part of the vision;
   `attractorify`'s "the honest no is reserved for asks with no machine gate" becomes an
   executable check.)
2. **select** (`bugfix|feature|refactor|testgen|review`) iff the objective maps onto ONE
   shipped practical pipeline's contract — its walk-up params are satisfiable from `$goal` +
   workspace, and its gate form fits the evidence_command (e.g. pytest-shaped DoD → bug-fix /
   test-gen). The five lanes are the bundle's existing `examples/pipelines/practical/*.dot`,
   unmodified — they already carry their own internal gates, budgets, and escalation.
3. **compose** otherwise — machine-checkable but not lane-shaped (multi-phase, novel artifact,
   bespoke DoD). The composer writes a child graph built from shipped patterns (§4).

The intake's authority is deliberately thin: `frame` proposes, `triage_gate` admits, the
evidence gate decides. Edges out of `triage_gate` mean "this sub-objective is not yet
satisfied"; the corrective edges (`triage_bad`, `evidence_bad`, lint/contract failures) are the
vision's "objective not satisfied" edges in the flesh.

---

## 4. The composed-child contract

### 4.1 What every generated child MUST contain

1. **Single `Msquare` exit** (engine admission rule) reached only through a deterministic gate.
2. **≥1 evidence gate OUTSIDE the worker nodes**: a `parallelogram` whose `tool_command` runs
   the *provided* `dod.sh` / `evidence_command` — never an `echo pass`, never an LLM verdict as
   the only gate (the measured reason: external gates caught the fabricated-evidence incident;
   one adaptive mega-node would have shipped it).
3. **≥1 corrective cycle** — gate-fail routes back to the worker with feedback
   (`feedback_from=` or `.ai/feedback/` file convention, both shipped in
   `patterns/convergence-factory.dot`).
4. **A budget wall inside the child** — iteration fuse (`$max_iterations`, default 6) checked
   *in the gate node* so green and red paths both spend budget (task-runner doctrine), with an
   `exhausted` route to a fail-loud escalation node (`exit 1`, `max_retries=0`).
5. **Honest failure routing** — every gate has an `outcome=fail` route; no TOPO-006
   silent-success shape; stale-label conjunctions on shared-source token edges.
6. **Parameter hygiene** — consumes `$goal` (cloned from the parent, P2); writes all evidence
   under the workspace (`.objective/…` or `.ai/…`), because files are the only trusted
   return channel (P3).

### 4.2 How the contract is enforced BEFORE execution (machine evidence, gates outside the composer)

- `lint_gate`: `attractor lint <generated>` — parse errors, dead conditional edges (TOPO-001),
  fail-routed-to-exit (TOPO-006), goal-gate/retry integrity; exit 1 blocks (P5-proved).
- `contract_gate`: `check_child_contract.py <generated> dod.sh` — the structural checks lint
  deliberately doesn't own (design-quality vs executability, §32): has-cycle, gate-node
  present and its `tool_command` invokes `dod.sh`/`evidence_command` (string-level check),
  budget fuse present, single exit, `outcome=fail` routes from every gate, no gate colocated
  in a `box` node. ~100 lines of stdlib Python parsing via the engine's own
  `parse_dot` (import from the installed module; no new engine surface).
- Both gates loop back to `compose` with the report as feedback, fused at `$max_compose`.
- The parent's `evidence_gate` then re-verifies AFTER execution regardless (defense in depth —
  the composed child's green is necessary, not sufficient).

### 4.3 Primitive source — stated choice

The T3-6 verify-and-validate / recover subgraphs live in a **private** repo and are NOT
available to this bundle. **Choice: build from what the bundle ships** —
`examples/patterns/task-runner.dot` (convergence skeleton), `patterns/convergence-factory.dot`
(context.*-injected factory loop), `examples/gates/*` (token/exit-code gate idioms,
base-SHA-anchored delta assertion) — plus one fresh, minimal exemplar-owned asset:
`compose-contract.md` (the contract above, written as instructions the composer embeds) and
`check_child_contract.py`. No new primitive *library* is proposed; if the private primitives
are ever published, the composer's prompt references them additively.

---

## 5. Evidence-flow design (how the parent verifies without trusting self-report)

Channels across the folder boundary, per probes:

| Channel | Probed behavior | Trust policy in this design |
|---|---|---|
| Files on disk (shared `--cwd`) | child artifacts land in the parent's workspace (P1/P3) | **The evidence channel.** Gate re-runs `dod.sh`; reads `.objective/…` artifacts |
| Child terminal `Outcome` (status/notes/failure_reason) | returned verbatim (P3); FAIL propagates loudly (#172/#182) | Fail-routing only — `outcome=fail` edges to postmortem |
| Declared `outputs=` context merge | merged on success, drove parent routing (P3) | Routing hints only, never gate evidence |
| Undeclared `context_updates` | **leak across the boundary** (P3, engine.py:1142) | Explicitly distrusted; documented in the exemplar guide |
| `preferred_label` | cleared entering the child (step 6a); null on return in probes | Not used across the boundary |

`evidence_gate` mechanics: (1) increment `.objective/iter`, wall against `$max_iterations`
FIRST (every visit spends budget); (2) assert required artifacts exist; (3) re-run
`evidence_command`/`dod.sh` capturing `evidence-<n>.log`; (4) anchored delta assertion —
"did durable work land since `base-sha`" (`examples/gates/base-sha-anchor.dot` idiom) so a
no-op child cannot pass on a stale green; (5) append `{"iteration", "gate", "pass"}` to
`.objective/convergence.jsonl` (descent visible, task-runner convention); (6) print
`evidence_ok|evidence_bad|exhausted` — token edges carry the stale-label conjunction.

---

## 6. Guidance wave — objective-first usage (exact files)

1. **`agents/attractor-expert.md`** — add an "Objective layer" section: when a user brings an
   *objective* rather than a pipeline choice, the expert's first move is the objective-runner
   (or its triage discipline manually): three-question test → select/compose/redirect; teach
   the select-vs-compose criteria (§3), the composed-child contract (§4), and F1/F3 as current
   sharp edges. The expert stops recommending "pick a .dot" as the entry move.
2. **`skills/attractorify/SKILL.md`** — close the loop after diagnosis: verdict `attractor` now
   ends with "run it through `examples/objective/objective-runner.dot`" (and when a shipped
   lane fits, name it via the same vocabulary the runner uses); verdict `recipe`/`one-shot`
   maps to the runner's `redirect` disposition so conversational and pipeline triage agree —
   one triage doctrine, two front doors.
3. **`context/pipeline-awareness.md`** — extend the decision heuristic: for a complex objective,
   `run_pipeline` may target the objective-runner itself with `goal=<objective>` — "you don't
   pick the pipeline; the runner triages" — plus the two-line honest-outcome contract
   (redirect.md / escalation.md) so agents relay honest exits instead of retrying blindly.
4. **`README.md`** — Pipeline Gallery gains an "Objective-first" row and the Quick Start gains
   the §1.1 invocation as the *first* example: state an objective, get either verified
   satisfaction, an honest redirect, or a loud escalation.
5. **`docs/PIPELINE_DESIGN_PRINCIPLES.md`** (§0 cross-ref) + **`docs/DOT-AUTHORING-GUIDE.md`**
   — one subsection each: "The objective layer" naming the layering (objective → composition →
   attractors → execution), the composed-child contract, and the F3 note (`${graph.goal}` vs
   `$goal` in tool commands); authoring guide points at `compose-contract.md` as the checklist
   for ANY parent-writes-child graph.
6. **`examples/pipelines/practical/README.md`** — one paragraph: these five pipelines are the
   objective-runner's *lanes*; their walk-up contracts (params, DoD shape) are what makes them
   selectable — keep them stable.

---

## 7. Live-proof plan (three real objectives, machine-checkable passes)

Shipped as `examples/objective/evidence/<run>/` (repo convention), each with a `verify.sh` the
CI or a reviewer can re-run:

1. **SELECT** — objective: *"Fix the TypeError in get_display_name when a user's avatar is
   None"* against the shipped `practical/sample/`. PASS iff: run `status=success` ∧
   `triage.json .shape=="bugfix"` ∧ checkpoint `completed_nodes` contains `lane_bugfix` and
   no compose/redirect node ∧ independent re-run of `pytest -q` in the sample exits 0 ∧
   `.objective/convergence.jsonl` non-empty.
2. **COMPOSE** — objective: *"Create CONTRIBUTING.md whose sections satisfy the provided
   `check_contrib.py`"* (checker shipped in the evidence fixture; no lane fits, DoD is
   machine-checkable). PASS iff: `generated/child.dot` exists ∧ `lint-report.txt` shows 0
   errors ∧ `contract-report.txt` = contract_ok ∧ a `subgraph_run_child*` dir exists in the
   run logs ∧ independent `python check_contrib.py` exits 0 ∧ run `status=success`.
3. **REDIRECT (the honest no)** — objective: *"Summarize this repo's architecture for a
   newcomer"* (no machine gate exists). PASS iff: run `status=success` ∧
   `.objective/redirect.md` exists, names `recipe|conversation`, and quotes Q2=no ∧
   `completed_nodes` contains **no** lane/compose folder node ∧ disposition=`redirected`.

Negative proof (already probed, re-shipped as fixture): a compose run whose generated child is
broken routes `lint_gate → compose` and, at `$max_compose`, exits via `escalated` with CLI
exit 1 — never executes the broken child.

---

## 8. Non-goals

- **No platform service / scheduler / portfolio orchestrator** — the "orchestrator as a
  service", event-driven resident system, and portfolio layer from the vision conversation are
  explicitly out (maintainer: in-bundle exemplar only).
- **No Resolve/TeamPulse integration**, no dashboards, no telemetry beyond existing run logs.
- **No repo split / rename** (the "graph runtime" naming thread is the maintainer's to pursue).
- **No uber-attractor / adaptive mega-node** — gates stay outside workers; the runner routes to
  *separate* children with their own gates.
- **No engine changes** in the exemplar PR. F1/F2 are filed as issues with smallest-fix shapes,
  not patched here; nothing in §1 depends on them.
- **No learned template selection / auto-tuning** — selection is a declared, inspectable gate.
- **No new top-level directory or module** — `examples/` weight only (§10 Q1).

Compat statement (SPEC_CONFORMANCE doctrine): everything here is additive author-level content
— new `.dot` + docs + one stdlib checker script. No new attributes, shapes, or semantics; a
spec-conformant community `.dot` is unaffected. The exemplar consumes only ledgered extensions
(§10 folder/dot_file, §20 tool.last_line, §21 params, §24 $iteration, §27 must_write where
useful, §32 lint CLI).

---

## 9. File-touch inventory + build order

New (all under `examples/objective/` unless noted):

| # | File | Role |
|---|---|---|
| 1 | `objective-runner.dot` | the exemplar graph (§1) |
| 2 | `objective-runner.md` | walk-up guide (pair convention) |
| 3 | `triage-schema.json` | intake vocabulary + required-fields contract |
| 4 | `compose-contract.md` | composed-child contract, embedded by the composer prompt |
| 5 | `check_child_contract.py` | structural gate (§4.2) |
| 6 | `evidence/…` (3 runs + negative fixture) | live proofs (§7) |
| 7 | `docs/designs/DESIGN-objective-layer.md` | this doc |

Modified (guidance wave, §6): `agents/attractor-expert.md`, `skills/attractorify/SKILL.md`,
`context/pipeline-awareness.md`, `README.md`, `docs/PIPELINE_DESIGN_PRINCIPLES.md`,
`docs/DOT-AUTHORING-GUIDE.md`, `examples/pipelines/practical/README.md`.

Filed, not built: issue F1 (verdict transport, with the P4 evidence bundle), comment on #200
(F2 compat shape), doc-nit F3.

**Build order:** (1) `triage-schema.json` + `check_child_contract.py` + unit fixtures →
(2) `objective-runner.dot` with lanes only (select + redirect paths), lint-clean, live-proof
runs 1 and 3 → (3) compose path + `compose-contract.md`, negative fixture, live-proof run 2 →
(4) walk-up guide + evidence dirs → (5) guidance wave → (6) file F1/F2/F3. Each step ends
runnable; steps 2–3 each gated by their own live proof before the next begins.

---

## 10. Open taste questions for the maintainer

1. **Placement/naming:** `examples/objective/objective-runner.dot` (own dir, like
   `patterns/task-runner`'s ecosystem) vs a flat `examples/patterns/objective-runner.dot`?
   And is "objective-runner" the word, or does the vision's vocabulary ("composition",
   "playbook") belong in the name?
2. **Param name:** ship as `goal` (zero-friction with existing lanes, P2-proven) with
   "objective" living in the docs/artifacts — or introduce `objective` as a first-class alias
   and thread it? (Alias costs a mapping mechanism the engine doesn't have today.)
3. **Intake vocabulary breadth:** the five lanes + compose + redirect, or start narrower
   (bugfix/testgen/compose/redirect) and grow lanes as their walk-up contracts prove stable?
4. **`preferred_label` intake:** once F1 is fixed, should the exemplar *switch* its intake to
   `report_outcome`/`preferred_label` (doctrine's self-steering idiom) or keep the
   artifact+token gate as the taught pattern for *first-hop* routing? (I lean artifact+token —
   evidence outside the worker — but it's a doctrine statement worth your ruling.)
5. **Lint strictness on generated children:** plain `lint` (ERRORs block; probed) + contract
   checker, or `--strict` with a curated warning allowlist?
6. **#200 disposition:** do you want the exemplar PR to carry the improved node-entry error
   message proposal (F2) as its one engine-adjacent suggestion, or keep the PR purely
   additive-content and file everything?
7. **Redirect disposition:** is "honest no" a green exit with `redirect.md` (my design — the
   diagnosis IS the deliverable) or should redirect exit nonzero so unattended callers can't
   mistake it for "objective satisfied"? (A `--param strict_disposition=true` could flip it.)

---

### Appendix — probe inventory

| Probe | Graph/run | Key artifact |
|---|---|---|
| P1 | `/tmp/objprobe/p1/parent.dot`, `missing.dot` | `child-proof.txt`; lint rc=0 with absent target; late-fail quote |
| P2 | `/tmp/objprobe/p2/parent.dot` + `child.dot` | `p2-evidence.txt` (4-line substitution matrix) |
| P3 | `/tmp/objprobe/p3/parent.dot` + `child.dot` | checkpoint child outcome; merged + leaked keys; routed path |
| P4 | `/tmp/objprobe/p4/route.dot`, `route-llm.dot`, `route-llm2.dot` | two lane routes; no_matching_edge quote; `tool:pre/post report_outcome` events vs `is_explicit=false` |
| P5 | `/tmp/objprobe/p5/gate.dot` + generators | blocked vs executed paths; lint rc matrix (1 / 0 / `--strict` 1) |

Engine anchors: `handlers/pipeline.py` (folder handler steps 1–12), `engine.py:137,611`
(step cap), `engine.py:1142` (context_updates application), `validation.py` (`lint()` vs
`validate_or_raise()`, single-exit rule), `specs/EXTENSIONS.md` §10/§11/§16/§20/§21/§22/§24/
§32/§33/§35, `docs/ROUTING-REFERENCE.md` §3–4, `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0.


---

## 11. As shipped — where the build diverged from this design

Recorded honestly, because a design doc that quietly matches the code it produced
is a design doc nobody re-reads. Everything below was found by building the thing
and running it for real; nothing here required an engine change.

### 11.1 The human gate lists **Approve** first, not Reject

§1.2/§1.3 put `[R] Reject` first, reasoning from the task-runner doctrine that the
"safe" choice goes first because an unattended `--on-human-gate auto-approve` run
selects edge one.

That doctrine holds where task-runner uses it — its human gate is on the *failure*
path, so "Abandon" first fails safe. The objective runner's `approve` gate sits on
the *success* path, immediately after a gate that already passed on machine
evidence. Reject-first there means every unattended run rejects a verified result,
spends an evidence iteration, rejects again, and finally escalates: a **false red**
after burning the whole budget. Approve-first accepts what the machine evidence
already established, and the CLI default (`--on-human-gate fail`) still refuses to
guess — an unattended caller has to opt in explicitly.

The generalizable rule, which §1.3 stated too narrowly: **the first choice should
be the one that does not fabricate an outcome.** On a failure-path gate that is
"abandon"; on a success-path gate downstream of real evidence it is "approve".

### 11.2 The delta anchor is a workspace digest, not a base-SHA/commit delta

§5 specified the `examples/gates/base-sha-anchor.dot` idiom — "did durable commits
land since `base-sha`". Shipping that would have made the exemplar fail on its own
first live proof: **none of the five shipped practical lanes commit anything.** A
commit-delta assertion goes red after a perfectly successful `bug-fix.dot` run.

Shipped instead: `preflight` records a digest of the workspace file tree (pruning
`.git`, `.objective`, `.ai`, `__pycache__`, `.pytest_cache`, `.ruff_cache`,
`node_modules`, `.venv`), and `evidence_gate` recomputes it and requires it to have
moved. That buys the property the design actually wanted — a no-op child cannot
pass on a stale green — without requiring the children to commit. Pruning the
runner's own state directories is load-bearing: without it, `.ai/test.log` alone
would satisfy the delta and the gate would be decorative.

### 11.3 `check_child_contract.py` ships its own DOT reader

§4.2 said "parsing via the engine's own `parse_dot` (import from the installed
module)". In practice the gate runs under whatever `python3` is on `PATH` in the
*target workspace*, which is not the `attractor` CLI's virtualenv — importing the
engine would have made the gate environment-dependent in exactly the situation
where it must be reliable.

Shipped: ~200 lines of stdlib tokenizer + parser for the DOT subset the shipped
graphs use, fail-closed on anything it cannot read (a graph with no
`digraph`/`graph` header, or an unparseable one, is `contract_bad`, never
`contract_ok`). The risk this introduces — two parsers disagreeing — is guarded by
a test that asserts both produce identical node sets and edge counts for
`bug-fix.dot`, `test-gen.dot`, `task-runner.dot` and `objective-runner.dot`. It also
runs *after* `attractor lint` has already accepted the file, so the engine's parser
is always the first opinion.

### 11.4 A third param: `runner_dir`

§1.1 showed `goal` + `target_dir`. The gates need to invoke `validate_triage.py`
and `check_child_contract.py`, which live beside the `.dot` — and a `tool_command`
runs with cwd = the *workspace*, with no context key naming the graph's own
directory. `runner_dir` is the honest fix, validated at `preflight` (a missing or
wrong `runner_dir` prints `blocked` and exits 1 before an LLM is ever paid) rather
than discovered as a confusing failure three nodes later.

### 11.5 `validate_triage.py` — a mechanism the file inventory omitted

§9 listed `triage-schema.json` but no validator. The schema is data; something has
to enforce it, and §1.3's "code-tier only" rules out doing it in the prompt. Shipped
as a stdlib script implementing a deliberately small JSON-Schema subset (`type`,
`required`, `properties`, `enum`, `min_length`) plus the three cross-field rules,
and owning the re-frame fuse. `jsonschema` is deliberately not a dependency: an
exemplar gate that needs `pip install` to run is not an exemplar.

### 11.6 Evidence lives outside the repo; the walk-up guide is `README.md`

§7/§9 proposed shipping `examples/objective/evidence/<run>/`. Maintainer ruling:
run artifacts are never committed. The three live proofs and the negative control
were archived outside the repository, and §7's pass criteria were executed against
them rather than published as fixtures. The `objective-runner.md` walk-up pair
became `examples/objective/README.md` (maintainer ruling on placement).

### 11.7 Guidance wave, as scoped

Shipped: `agents/attractor-expert.md`, `skills/attractorify/SKILL.md`,
`context/pipeline-awareness.md`, `README.md`,
`examples/pipelines/practical/README.md`, one pattern entry in
`docs/PIPELINE_PATTERNS.md` (§7, "schema-validated artifact as the routing
signal" — it fit the existing structure better than a renumbered new section), and
the F3 note in `docs/DOT-AUTHORING-GUIDE.md`. `docs/PIPELINE_DESIGN_PRINCIPLES.md`
was left alone: §0 already states the doctrine the exemplar implements, and adding
a cross-reference there would have been decoration.

### 11.8 What the live proofs found that no probe could

- **A lane's tooling must actually exec.** LP1's first attempt failed because the
  host's `pytest` shim had a dangling shebang (`#!/opt/az/bin/python3.13`), so
  `sh -c pytest` reported "not found" while `command -v pytest` resolved happily.
  The exemplar behaved correctly — child escalated, parent routed
  `outcome=fail` → postmortem → escalated, CLI exit 1 — and, notably, the child's
  worker had *actually fixed the bug* and written "Status: COMPLETE" in its own
  scratch file. The pipeline still refused to report success. That is the doctrine
  paying for itself: a worker's self-report bought exactly nothing.
- **The two generated-child gates catch different things, and both are needed.**
  A deliberately-broken child with a dead conditional edge is blocked by
  `lint_gate` while `contract_gate` says `contract_ok`; a child that is acyclic with
  `goal_gate=true` on its worker passes `attractor lint` with warnings only and is
  blocked by `contract_gate`. Neither gate subsumes the other.
- **`frame` writes serviceable but imperfect DoDs.** LP1's evidence command piped
  through `tee /tmp/pytest-out.txt` — non-vacuous (it greps for a passing test whose
  name mentions the None-avatar case) but writing scratch outside the workspace.
  Worth a future tightening of the `frame` prompt; recorded rather than hidden.

---

## 12. Maintainer rulings on §10's open questions

| # | Question | Ruling |
|---|---|---|
| 1 | Placement/naming | `examples/objective/` as its own directory; the graph is `objective-runner.dot` |
| 2 | Param name | Ship as `goal`. The objective IS the goal; no new param vocabulary |
| 3 | Intake vocabulary breadth | All five practical lanes + `compose` + `redirect` |
| 4 | `preferred_label` intake | Keep the artifact+token gate. It is the taught doctrine — evidence outside the worker — and it happens to also dodge F1 |
| 5 | Lint strictness on generated children | Plain `lint` (ERRORs block, warnings do not) + the contract checker. Warnings are recorded in the run record |
| 6 | #200 disposition | Keep the PR purely additive content. F2 is filed as a comment on #200, not patched here |
| 7 | Redirect disposition | Green exit. The honest diagnosis is a successful deliverable; `.objective/disposition` is what lets an unattended caller tell the outcomes apart |
