# The Objective Layer

One layer up from an attractor. You do not pick a pipeline — you state an
**objective**, and `objective-runner.dot` diagnoses it, routes it, converges on
it against machine evidence, and tells you honestly when there is no machine
evidence to be had.

```
objective  ->  composition  ->  attractors  ->  execution
(this graph)   (compose path)   (the lanes)     (their own gates)
```

The basin: **the stated objective is satisfied with machine evidence, or it is
honestly redirected.** Both are green exits, and they are told apart by a
disposition artifact. A run that can do neither escalates loudly with a nonzero
exit — never through the success door.

---

## Run it

```bash
OBJ="$PWD/examples/objective"          # capture the absolute path BEFORE cd
cd /path/to/your/workspace
attractor run "$OBJ/objective-runner.dot" \
    --param goal="get_display_name() raises TypeError when a user has no avatar. Fix it and add a regression test." \
    --param runner_dir="$OBJ" \
    --param target_dir="$PWD" \
    --cwd .
```

Process cwd must equal `--cwd` for box-node (agent) pipelines — see
[`../../modules/pipeline-runner/KNOWN_ISSUES.md`](../../modules/pipeline-runner/KNOWN_ISSUES.md).

| Param | Required | What it is |
|---|---|---|
| `goal` | yes | **The objective.** It travels as the canonical `goal` param, so every shipped lane consumes it natively — no adapter layer, no new vocabulary. |
| `runner_dir` | yes | Absolute path to *this* directory. The gates run `validate_triage.py` and `check_child_contract.py` from here. |
| `target_dir` | yes | Absolute path to the workspace. The generated child's `dot_file` must be absolute, because a relative `dot_file` resolves against the runner's own directory first. |
| `max_iterations` | no (3) | Evidence-loop budget. |
| `max_compose` | no (2) | Compose-fix budget — at most 3 lint/contract attempts. |
| `max_frames` | no (2) | How many malformed triage records to tolerate. |

**Add `--on-human-gate auto-approve`** for an unattended run. The `approve`
hexagon sits *after* the machine evidence gate; the CLI default
(`--on-human-gate fail`) deliberately refuses to guess on your behalf.

**Environment.** Whatever your objective's definition of done needs must be on
`PATH` for the tool nodes — `pytest`, your build, your linter. A missing tool
shows up as a red gate, which is honest but slower to read than checking first.

### Reading the result

```bash
cat .objective/disposition        # satisfied | redirected | escalated
```

| Disposition | Exit | What you got | Where to look |
|---|---|---|---|
| `satisfied` | 0 | The objective's definition of done passed **in the parent**, and the workspace actually changed | `.objective/evidence-*.log`, `.objective/convergence.jsonl` |
| `redirected` | 0 | The honest no: this is not an attractor, and here is why and where it belongs | `.objective/redirect.md` |
| `escalated` | **1** | Could not converge within budget; the value salvaged is the analysis | `.objective/postmortem/report.md`, `.objective/postmortem/escalation.md` |

---

## What it does, in order

1. **`preflight`** (tool) — makes the run's preconditions true or refuses before
   an LLM is ever paid, and records `.objective/anchor`: a digest of the
   workspace *before* any work.
2. **`frame`** (LLM) — diagnoses the objective with this repo's own
   [three-question test](../../docs/PIPELINE_DESIGN_PRINCIPLES.md) and writes
   `.objective/triage.json` + `.objective/objective.md`. It **proposes**; it
   does not route.
3. **`triage_gate`** (tool) — validates the record against
   [`triage-schema.json`](triage-schema.json) and prints the routing token. This
   is the node that decides.
4. One of three paths:
   - **select** — a shipped `examples/pipelines/practical/*.dot` runs as a
     `folder` node, unmodified, carrying its own gates and budgets.
   - **compose** — an LLM writes a purpose-built child `.dot` + `dod.sh`; two
     gates outside its context check the result before it is allowed to run.
     One of them (**C9**) *executes* the `dod.sh` and rejects it unless it is
     genuinely red — a definition of done that cannot fail is not one.
   - **redirect** — the honest no, written up as `.objective/redirect.md`.
5. **`evidence_gate`** (tool, the load-bearing one) — **re-runs the definition of
   done itself**, in the parent, and asserts the workspace changed since the
   anchor. On failure, `feedback` writes the descent signal and the run
   re-enters through `triage_gate` — same shape, fresh context.
6. **`approve`** (human) → **`finalize`** (tool) → `done`. `finalize` refuses to
   open without a disposition artifact.

---

## The three ideas worth copying

### 1. The first routing decision runs on a machine artifact, not a self-report

The obvious design has `frame` call `report_outcome(preferred_label="bugfix")`
and routes on that. This graph deliberately does not. `frame` writes a JSON
record; `triage_gate` — code, outside the worker's context — validates it and
prints the token.

That is the same rule every gate in this repo obeys, applied to the one decision
authors usually exempt: *verification inside the context that produced the
evidence is not verification.* The schema's cross-field rules make it bite —
`CF-1` rejects any non-redirect shape whose `evidence_command` is `NONE`, so
"no attractor without machine evidence" is a check rather than a slogan.

### 2. The parent never trusts the child's self-report

A child pipeline's terminal outcome is used for **loud fail-routing only**.
Whether the objective is *satisfied* is decided by `evidence_gate`, which re-runs
the definition-of-done command itself.

This is defense in depth on purpose. A child's green is necessary, never
sufficient — in this repo's own history, a worker fabricated its convergence
evidence and the gate reading that file passed it. The gates that caught it were
outside the worker's context.

The second half of that gate is the **delta assertion**: a green check on an
unchanged workspace means nothing happened and the check was already passing.
Both halves must hold, which is why `preflight` records an anchor first.

Two later additions close the ways a green could still be manufactured, both
found by an adversarial review of this exemplar rather than in theory:

- **C9** — `contract_gate` *runs* the generated `dod.sh` once, at admission,
  and requires exit 1. "The DoD must be red before the work exists" used to be
  a line in the composer's prompt, and a prompt instruction is a suggestion: a
  composer writing `exit 0` satisfied every structural check, its child
  converged instantly, and this gate re-ran the same vacuous script and agreed.
- **The sha-pin** — `triage_gate` and `contract_gate` record `sha256` of what
  they admitted (`.objective/evidence-command.sha256`, `.objective/dod.sha256`);
  `evidence_gate` re-hashes *before* re-running and refuses loudly on a
  mismatch, so a check rewritten after it was approved cannot be the check that
  gets run. It is **anti-accident, not anti-adversary** — the workspace is
  child-writable, so a determined child could update the pin too. Closing that
  structurally needs custody outside the workspace, which is deliberately out
  of scope for an exemplar; see
  [`compose-contract.md`](compose-contract.md#the-pin-and-what-it-is-not).

### 3. The honest no is a deliverable, not a failure

`redirect` exits **green** with `.objective/redirect.md` — the objective
restated, the three-question answers quoted verbatim, the better home named
(recipe / conversation / one-shot), and *what would make it an attractor*: the
specific machine check that does not exist yet.

An accurate "no, and here is why" is worth more than a run. The disposition
artifact is what lets an unattended caller tell that outcome apart from
"satisfied" without reading prose.

---

## Files here

| File | Role |
|---|---|
| [`objective-runner.dot`](objective-runner.dot) | the graph — every node carries its objective / constraints / capabilities / required evidence / exit condition as a header comment |
| [`triage-schema.json`](triage-schema.json) | the intake contract: routing vocabulary, required fields, and the cross-field rules |
| [`validate_triage.py`](validate_triage.py) | `triage_gate`'s mechanism — stdlib only, no `jsonschema` dependency |
| [`compose-contract.md`](compose-contract.md) | what every generated child must satisfy, written as the composer reads it |
| [`check_child_contract.py`](check_child_contract.py) | `contract_gate`'s mechanism — eight structural checks plus **C9**, which *runs* the generated `dod.sh` at admission and rejects it unless it is red |

---

## Budgets and how a run ends

| Fuse | Default | Counted by | Where it goes when spent |
|---|---|---|---|
| re-frame on a malformed triage record | `max_frames` = 2 | `validate_triage.py` (`.objective/frame-iter`) | `triage_exhausted` → postmortem |
| compose rewrite after a blocked child | `max_compose` = 2 | `lint_gate` (`.objective/compose-iter`) | `compose_exhausted` → postmortem |
| evidence iterations | `max_iterations` = 3 | `evidence_gate` (`.objective/iter`) | `exhausted` → postmortem |

Every fuse is counted **inside a gate**, so green and red paths both spend
budget, and a persistent provider outage drains the budget and escalates
honestly instead of looping. The engine's own step cap (`nodes × 50` = 1050 here)
is a backstop that should never be what stops a run — if it is, the graph has a
bug.

`postmortem` writes the analysis; `escalated` writes the handoff and then
`exit 1` with `max_retries=0` and no fail-route, so the engine hard-fails loud.

---

## What this deliberately is NOT

- **Not a service.** No daemon, no scheduler, no event loop, no resident
  process. It is a `.dot` file you run.
- **Not a portfolio orchestrator.** One objective per invocation. There is no
  queue, no prioritization, no cross-run state.
- **Not a Resolve / Team Pulse integration.** No dashboards, no telemetry beyond
  the run logs the engine already writes.
- **Not an uber-attractor.** It does not absorb the lanes into one adaptive
  mega-node. It routes to *separate* children that keep their own gates —
  because one adaptive node with a self-assessed exit is exactly the shape that
  ships fabricated evidence.
- **Not a replacement for picking a pipeline.** If you already know you want
  `bug-fix.dot`, run `bug-fix.dot`. This layer earns its keep when you know the
  objective and not the shape.
- **Not learned or adaptive selection.** Routing is a declared, inspectable
  gate over a schema-validated record. You can read why it chose what it chose.
- **Not engine surface.** Everything here is author-level content: one `.dot`,
  two stdlib scripts, and docs. No new attributes, shapes, or semantics.

---

## Related

| Topic | Where |
|---|---|
| Designing a NEW pipeline from a conversation | [`/attractorify`](../../skills/attractorify/SKILL.md) — this runner *selects and composes* from what exists; attractorify *designs* what doesn't |
| The convergence skeleton this borrows from | [`../patterns/task-runner.dot`](../patterns/task-runner.dot) |
| The lanes | [`../pipelines/practical/`](../pipelines/practical/) |
| Gate idioms and the stale-label rule | [`../gates/README.md`](../gates/README.md) |
| Why gates live outside workers | [`../../docs/PIPELINE_DESIGN_PRINCIPLES.md`](../../docs/PIPELINE_DESIGN_PRINCIPLES.md) |
| The design and its engine probes | [`../../docs/designs/2026-08-15-objective-layer.md`](../../docs/designs/2026-08-15-objective-layer.md) |
