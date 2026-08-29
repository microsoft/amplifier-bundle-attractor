# Task Runner — Battle-Hardened Convergence Attractor

A goal+DoD-driven convergence attractor that takes a task description file plus
an executable definition-of-done script and converges on completion. The exit is
structurally unreachable until the DoD script passes **and** the work survives a
strict critique.

This is the battle-hardened big sibling of
[`convergence-factory.dot`](convergence-factory.dot). Where the factory is a
clean skeleton, the task runner is a survivor: every corrective structure earned
its place — four of the five lessons below come from live-run failures, the
fifth from an empirical engine probe that caught a trap during review, before
it could bite a run. Its provenance is this repo — it was
hardened by running real improvement tasks against `amplifier-bundle-attractor`
itself, and PRs #98, #99, and #101 are its by-products.

---

## Quick start

Copy the fixture to a scratch directory so the committed sample stays pristine,
then run from the **repo root**:

```bash
DOT="$PWD/examples/patterns/task-runner.dot"
cp -r examples/patterns/task-runner-fixture /tmp/task-runner-demo
cd /tmp/task-runner-demo
git init -q && git add -A && git commit -qm "fixture baseline"
dot-runner run "$DOT" \
    --param task_file="$PWD/sample-task.md" \
    --param target_dir="$PWD" \
    --param max_iterations=6 \
    --cwd .
```

The `git init` matters: the ship path (`package` → `ship_check`) commits the
finished work to a `task/<id>` branch and verifies a clean tree — the target
must be a git repository, or the run ends at the `escalate` human gate instead
of `done`.

The fixture uses a **planted-red design**: the DoD script fails on the first
gate visit by design (it keys on `.ai/iter`, which only the verify gate
increments). This guarantees the corrective path fires on every first run —
so you see the basin absorb a failure before trusting it with real work. The
fixture README explains this honestly.

**Point it at your own task instead:** replace `TASK` with your task `.md` file
and `target_dir` with your repo root. Keep `$DOT` absolute before `cd`, keep
`--cwd .` matching the process cwd (see below).

---

## Invocation contract

```bash
cd <target_repo>
DOT=/abs/path/to/task-runner.dot
dot-runner run "$DOT" \
    --param task_file=/abs/path/to/task.md \
    --param target_dir=$PWD \
    --param max_iterations=6 \
    --cwd .
```

**Parameters:**

| Param | What it is |
|---|---|
| `task_file` | Absolute path to the task description `.md` file |
| `target_dir` | Absolute path to the target repo — pass `$PWD` after `cd` |
| `max_iterations` | Budget: total verify attempts before the budget wall fires |

**The process-cwd == `--cwd` gotcha:** tool-node cwd is seeded from `--cwd`,
not from `--param target_dir`. The `$target_dir` param is prompt text for box
nodes only. The invocation above keeps them identical on purpose. If they
diverge, box nodes write to one place and tool nodes read from another — a
silent split that surfaces only when a tool node can't find a file a box node
just wrote. See `modules/pipeline-runner/KNOWN_ISSUES.md`.

**The target must be a git repository:** the ship path (`package` →
`ship_check`) commits the finished work to a `task/<id>` branch and verifies a
clean tree. Point the runner only at git-tracked targets (or `git init` a
scratch directory, as the quick start does).

**DoD script discovery:** the runner looks for a sibling of `task_file` named
`<task-basename>.verify.sh` (same name as the task file, `.md` replaced by
`.verify.sh`). Keep the task file and its DoD script as siblings with matching
basenames.

**Where models come from (the DOT declares none):** the `dot-runner` CLI path
never requires a per-node `llm_model`. `dot-runner run` builds the
`bundles/attractor-pipeline.yaml` bundle and registers a `session.spawn`
capability (`run_pipeline` in
`modules/pipeline-runner/amplifier_module_pipeline_runner/runner.py`), so
every box node takes the engine's *spawned-agent* path
(`AmplifierBackend._run_with_spawn`), which explicitly tolerates a missing
model. Each node's `llm_provider` (default `anthropic`) is routed through the
profiles map (`anthropic → attractor-agent-anthropic`, `openai →
attractor-agent-openai`, `gemini → attractor-agent-gemini`) to a
per-provider child agent, and the model then comes from the bundle's provider
config — `provider-anthropic` declares `default_model: claude-sonnet-4-6` in
`bundles/attractor-pipeline.yaml`.

The error `Node 'orient' requires an explicit 'llm_model' attribute` comes
from the engine's *direct-LLM* path (`_resolve_model` in
`modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`), which
fires only when the engine is driven **without** a `session.spawn` capability
— e.g. embedding `PipelineEngine`/`AmplifierBackend` directly in your own
harness. Both behaviors are real; they belong to different entry points.

To take explicit control, set a graph-level `model_stylesheet` (per-node
`llm_model` also works — on the spawn path it is delivered to the child agent
via provider preferences, and glob/family tokens are resolved live to the
newest served model):

```dot
graph [
    // Uncomment to route by node class instead of the bundle default:
    // model_stylesheet=".gate { llm_model: claude-opus-* } .maker { llm_model: claude-sonnet-* }"
]
```

**`.ai/convergence.jsonl`:** exactly two gates append records — not every
gate visit. `verify` appends `{"iteration": N, "gate": "verify", "pass":
bool}` per DoD run, plus `{"iteration": N, "gate": "verify", "budget":
"exhausted"}` when its budget wall fires; `verdict` appends `{"iteration": N,
"gate": "critique", "ship": bool}` per critique round. Together they are the
descent curve — the file-based workaround for the engine not yet recording
gate outcomes natively (engine-native convergence records are proposed in
PR #99) — and the primary evidence artifact for a live run.

---

## Shape

```
start -> setup -> orient -> attempt -> verify
                                          |green
                                       critique -> verdict
                                          |ship        |iterate
                                       package      feedback -> attempt (loop_restart)
                                          |             |fail (transient)
                                       ship_check -> verify
                                          |dirty
                                       escalate
                                          |
                                  [A] abandon  [C] bump_budget -> attempt

verify |fail
triage -> attempt (novel)
       -> diagnose -> diagnose_gate -> attempt (continue)
                                    -> escalate (blocked)
       -> postmortem -> pm_gate -> escalate (exhausted)

verify |exhausted  (budget wall: fires BEFORE the DoD script runs)
postmortem -> pm_gate -> escalate
```

**Inner loop** (tight mechanical fix): `attempt -> verify -> triage -> attempt`.
Session continuity via `thread_id=work` — the maker remembers its work across
inner iterations.

**Outer loop** (quality convergence): `verify -> critique -> verdict -> feedback
-> attempt`. Fresh eyes via edge-level `fidelity=compact` on `feedback->attempt`
— `loop_restart` alone does not clear backend thread transcripts; the edge
attribute does.

**Root-cause wall:** same failure signature twice → `diagnose` (find the cause,
never recommend a blind retry) → `diagnose_gate` (clears the signature for one
clean shot).

**Budget as a decision point:** the budget is checked in `verify` itself —
every entry (first attempt, inner-loop retry, or transient re-entry)
increments `.ai/iter`, and the wall fires *before* the DoD script runs.
Exhaustion → `postmortem` → `pm_gate` → `escalate` → human gate. Never a
bare FAIL. Abandon is listed first so unattended auto-approve fails safe.
(`triage` keeps its own exhausted check as a harmless second wall.)

**Stall detection:** 3 consecutive critique refusals on mechanically-green work
→ `postmortem` → human gate. The outer loop gets a budget too.

---

## Five live-fire lessons

These are not design principles — they are scars. Four come from live runs that
failed without them; one (the stale-label rule) from an empirical engine probe
during review.

### 1. Stale-label conjunction

**Doctrine:** Route on evidence — and know how the evidence channel decays.

**Mechanics:** A failing tool node does NOT refresh `tool.last_line`
(ToolHandler returns early on nonzero exit). Therefore on a second visit after
a failure, a stale label + FAIL can simultaneously match a
`context.tool.last_line=X` edge AND an `outcome=fail` edge. In this graph:
`verify->critique` carries `last_line=green && outcome=success`;
`verdict->package` carries `last_line=ship && outcome=success`. The
`&& outcome=success` conjunction makes the routing intent unambiguous —
good explicitness discipline regardless of engine semantics.

**Historical note (T0-4):** Prior to spec-conformance restoration, stale
"green" + FAIL matched two edges and the engine fanned out to both, critiquing
red work. The bug fired only on the **second+** gate visit — exactly when the
corrective loop first works — so it survived topology lints and was caught only
by probing the running engine during review. After T0-4, the engine conforms to
spec §3.3 and picks ONE edge deterministically (weight desc, then lexical
target-id tiebreak). The deterministic pick can still be the wrong edge — so
the conjunction remains good practice, though it is no longer a safety
requirement against fan-out.

### 2. Transient-recovery routes

**Doctrine:** Survive one node's bad day — including the provider's.

**Mechanics:** Ship-path box nodes (`critique`, `feedback`, `package`) carry
`outcome=fail` edges back to `verify`. A transient failure re-enters via the
cheap gate: verify re-greens in seconds and the outer loop re-converges. Each
re-entry costs an iteration — `verify` increments `.ai/iter` and checks the
budget *before* running the DoD script — so a **persistent** outage drains
the budget through `verify` and escalates honestly via
`verify->postmortem->human` instead of looping forever. `orient` stays
fail-fast (fails early, cheap to relaunch); `diagnose` and `postmortem` stay
fail-fast (already inside failure handling — a failure there should surface,
not loop).

**War story:** An overloaded-error at the `critique` node fail-fasted a live run
after the work was already done. One edge addition fixed it permanently. A
cross-provider review then caught the recovery loop's own blind spot: the
budget check originally lived only in `triage`, which runs only on verify
FAIL — so a *persistent* critique-stage outage would cycle
critique(FAIL)→verify(PASS)→critique forever, until the engine's step cap
killed the run with a bare FAIL, bypassing the postmortem path this graph
promises. Moving the budget wall into `verify` (checked before the DoD script
runs) closed it: now every re-entry spends budget on both the red and green
paths.

### 3. Root-cause wall + signature normalization

**Doctrine:** An attractor absorbs model drift, not deterministic bugs.

**Mechanics:** `triage` hashes the verify-log tail with volatile tokens (tmp
paths, float timings) stripped via `sed` before `md5sum`. Same signature twice
→ `diagnose` (find the cause); `diagnose_gate` clears the signature for one
clean shot. Without normalization, every identical failure hashes "novel" and
the wall never fires.

**War story:** A run burned its whole budget because a random `/tmp/attractor-run-*/`
path in the hash made every identical failure look novel. The root-cause wall
never fired; the budget wall did.

### 4. Stall detection — budget as a decision point on both loops

**Doctrine:** The bound is a decision, not a fuse — and the green path needs
one too.

**Mechanics:** Every verify entry is bounded by `.ai/iter` vs budget — the
wall lives in `verify` itself and fires before the DoD script runs
(`verify->postmortem`; `triage` keeps a second check on the red path). The
quality loop is additionally bounded by `.ai/stall-count` — 3 critique
refusals → `stall` → `postmortem` → human gate. Abandon is listed **first**
so unattended auto-approve fails safe.

**War story:** A mechanically-green run looped 6 consecutive critique refusals
(a fresh maximally-strict critic always finds something new) until the operator
killed it at the wall-clock fuse. The stall counter is that fuse, moved inside
the graph.

### 5. pm_gate — deterministic must-write guard

**Doctrine:** Never let an exit path depend on a box node having actually written
its artifact.

**Mechanics:** `pm_gate` checks `.ai/postmortem/report.md` is non-empty and
writes a labeled stub if not, so `abandon` never references a missing file. The
pattern generalizes: any node whose artifact is required by a downstream path
should have a deterministic gate after it.

**War story:** The `postmortem` node returned SUCCESS without writing its report
— twice, in consecutive runs. The gate was added after the second occurrence.

---

## Supporting mechanics

**`loop_restart` does not clear thread transcripts.** Fresh eyes come from
edge-level `fidelity=compact` on `feedback->attempt` (highest precedence). The
`loop_restart` attribute resets engine iteration state; the `fidelity` attribute
compresses the thread context. Both are needed.

**`goal_gate` on both gates.** `verify` and `verdict` both carry `goal_gate=true`.
This makes the exit unearnable without evidence: the engine requires the goal to
be met at both the mechanical gate and the judgment gate before `done` is
reachable.

**Cheap gate before expensive gate.** `verify` (a shell script, seconds) runs
before `critique` (an LLM call, expensive). Work that fails mechanically never
reaches the expensive gate.

**`${k:-default}` is shell, not engine.** The engine does NOT expand shell-style
defaults. `${B:-6}` in a `tool_command` works because `B` is a shell variable in
the same command — not because the engine expands it. Never name a shell
variable the same as a param.

**Composing a stronger critique.** When the stakes justify the spend, swap
the single `critique` stage for two **independent** reviewers from different
model families — a second box node with `llm_provider="openai"` and an
explicit `llm_model` — and make `verdict` require consensus: both final
`VERDICT:` lines must say SHIP. The second reviewer must not read the first's
critique file; independence is what makes agreement meaningful, and
cross-family disagreement is signal (this repo's own multi-lens doctrine).
Two deployment cautions, both verified empirically: (1) the second family's
provider must be **mounted** in the runner's base bundle — the shipped
`bundles/attractor-pipeline.yaml` mounts `provider-anthropic` only, and an
`llm_provider="openai"` node then silently runs on Anthropic (point
`ATTRACTOR_PIPELINE_BUNDLE` at a bundle that mounts both, and pin
`llm_provider` in the openai agent's own orchestrator config — verify with an
identity probe). (2) Run the two reviewers in **sequence, not in parallel**:
parallel component branches route through `run_subgraph`'s permissive fail
path (see `context/engine-semantics.md`), which would let a crashed reviewer
branch pass silently.

**Model doctrine.** The expensive model belongs in the gates (`critique`,
`diagnose`) — gate quality determines basin depth; maker quality only affects
iteration count. Use `model_stylesheet` to route nodes by class:

```dot
model_stylesheet=".gate { llm_model: <strong-reasoning-model> } .maker { llm_model: <standard-coding-model> }"
```

---

## Live-run evidence

The exemplar has been run end-to-end against its own sample fixture. The
corrective path fired on the first run as designed.

**Convergence record** (`.ai/convergence.jsonl` from the run):

```jsonl
{"iteration": 1, "gate": "verify", "pass": false}
{"iteration": 2, "gate": "verify", "pass": true}
{"iteration": 2, "gate": "critique", "ship": true}
```

**Trace** (nodes visited in order):
`start → setup → orient → attempt → verify(FAIL) → triage → attempt → verify(PASS) → critique → verdict(SHIP) → package → ship_check → done`

The first `verify` failure is the planted red. `triage` routed to `attempt`
(novel failure, budget not exhausted). The second `attempt` computed
`sha256sum .ai-demo/nonce` and wrote the hex digest to `.ai-demo/answer.txt`.
The second `verify` passed. `critique` and `verdict` both cleared. The run
reached `done` in 2 gate visits.

**Note on DoD script discovery:** during the live run, a bug was found and fixed
in the `setup` and `verify` nodes. The original used `VF="${task_file%.md}.verify.sh"` —
the engine substitutes `$task_file` but does not expand shell parameter
substitutions like `${task_file%.md}` (only `$name` and `${name}` exactly).
The fix: `VF="$task_file"; VF="${VF%.md}.verify.sh"` — assign first, then strip
the extension from the shell variable; the engine only rewrites `$name` /
`${name}` and leaves everything else to the shell.

---

## Fixture design notes

The sample fixture (`task-runner-fixture/`) uses a **planted-red design** keyed
on `.ai/iter` (the runner's gate-visit counter, incremented only by the verify
gate). The worker cannot absorb the red during the work phase because it has no
access to `.ai/iter` before the gate fires — an earlier version keyed on a nonce
file, and a diligent worker pre-ran the DoD script during the work phase and
absorbed the red before the gate ever saw it. Good discipline, wrong drill.

The fixture README is honest about this: the first red is planted to demonstrate
the corrective path, not because anything is broken.

---

## Placement and provenance

This exemplar lives in `examples/patterns/` as the battle-hardened big sibling
of `convergence-factory.dot`. The `patterns/` directory holds reusable DOT
structures; this is the first paired `.md` guide here (setting the convention).

**Provenance:** this graph was built and hardened by working a real improvement
backlog against this repository. It is not a designed diagram — it is a
survivor. PRs #98, #99, and #101 are its by-products, and each taught it
something: the run behind #98 shipped only after the critique gate refused a
mechanically-green first attempt; the run behind #99 stalled at the critique
gate — the salvaged work became the PR, and the stall became lesson 4; the run
behind #101 shipped a subtle false historical claim that only human review
caught (the critique gate bounds review — it does not replace it).

For design principles and routing reference, see
[`docs/PIPELINE_DESIGN_PRINCIPLES.md`](../../docs/PIPELINE_DESIGN_PRINCIPLES.md)
and [`docs/ROUTING-REFERENCE.md`](../../docs/ROUTING-REFERENCE.md).
