# Tutorial 00: The Convergence Loop

**Stop here and you know the point.** Every subsequent tutorial is a variation on
this shape.

## Why the loop comes first

A six-step linear chain of nodes that each succeed 90 % of the time has roughly
**0.53 end-to-end reliability** (0.9^6 ≈ 0.53). Add one corrective loop with the
same nodes and the same per-step probability, and reliability rises to roughly
**0.94**. The loop is not a style choice — it is the mechanism that turns a
flowchart into an attractor.

The shape check: does your pipeline have (1) a worker that produces
something, (2) a gate that checks it with a machine-readable signal, and (3) a
back-edge that routes the worker back when the gate fails? If yes, it is a
convergence graph. If no, it is a script with extra syntax.

(This is a structural check on the graph itself -- distinct from the
[three-question test](../../docs/PIPELINE_DESIGN_PRINCIPLES.md#the-three-question-test)
in `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0, which diagnoses whether work
warrants an attractor *at all*.)

## The shape

```
start → implement → test_gate ──(gate_pass)──→ done
                       ↑                          
                       └──────(gate_fail)──────────
```

Four nodes. The exit (`done`) is structurally unreachable until `test_gate`
echoes `gate_pass`. The gate is a **parallelogram** (tool node): it runs
`pytest` mechanically and echoes a routing label as its last output line. The
engine stores that label in `context.tool.last_line`; the outgoing edges
condition on it.

This is what makes the exit structurally unreachable: no LLM self-report can
fake a green test run. The gate is deterministic.

## Walk-up invocation

Run from the repo root:

```bash
DOT="$PWD/examples/pipelines/00-convergence-loop.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

Two things about the command shape:

- **Capture `$DOT` absolute before `cd`** — the path resolves from your
  *current* directory, so a relative path breaks after `cd`.
- **Process cwd must equal `--cwd`** for box-node pipelines — the agent
  orchestrator roots its writes at the process cwd, so `cd` into the run dir
  and pass `--cwd .` (see `modules/pipeline-runner/KNOWN_ISSUES.md`).

No `--param` needed — the goal is baked into the `.dot`:

```
graph [goal="Write a Python function count_words(text) ..."]
```

## What happens

1. **`start`** — entry point (Mdiamond shape, no-op).
2. **`implement`** — LLM agent writes `word_counter.py` and
   `test_word_counter.py`. On retry iterations it reads `test_output.txt`
   (written by the gate on the previous pass) to see what failed.
3. **`test_gate`** — tool node (parallelogram) runs:
   ```
   pytest -q test_word_counter.py > test_output.txt 2>&1 && echo gate_pass || echo gate_fail
   ```
   The last line of stdout is `gate_pass` or `gate_fail`. The engine stores it
   in `context.tool.last_line`.
4. **Routing** — `gate_pass` → `done`; `gate_fail` → back to `implement`
   (with `loop_restart=true` to reset iteration state).
5. **`done`** — exit point (Msquare shape).

The loop fires at least once before convergence whenever the first
implementation attempt has a bug. `test_output.txt` carries the failure
evidence forward so `implement` can read it on the next pass.

## Key mechanics to copy

| Mechanic | Why |
|----------|-----|
| `shape=parallelogram` | Marks the node as a tool node (runs `tool_command`, not an LLM) |
| `goal_gate=true` | Pipeline cannot exit until this node reports success |
| `echo gate_pass \|\| echo gate_fail` | Produces a routing label as the last stdout line |
| `condition="context.tool.last_line=gate_pass"` | Routes on the label, not on a self-reported outcome |
| `loop_restart="true"` on the back-edge | Resets iteration state so `implement` starts fresh |
| Redirect to file (`> test_output.txt`) | Carries failure evidence to the next iteration |

## The routing idiom: `context.tool.last_line`

The gate command echoes a label as its last line. The engine stores that label
in `context.tool.last_line` (see `docs/DOT-AUTHORING-GUIDE.md` §Routing via
tool.last_line). Outgoing edges condition on it:

```dot
test_gate -> done      [condition="context.tool.last_line=gate_pass"]
test_gate -> implement [condition="context.tool.last_line=gate_fail", loop_restart="true"]
```

This is the evidence-routing idiom. The gate is deterministic; the routing is
on a machine-produced signal, not an LLM verdict.

## Grow it

This is the minimal form (4 nodes). The full convergence shape adds:

- An **assess** node (LLM) between the gate and the back-edge — for semantic
  quality checks that tools cannot express.
- An **outer feedback loop** — a second gate that checks higher-level
  criteria (e.g. "does the function handle edge cases?").
- A **budget wall** — a counter that halts after N iterations to prevent
  infinite loops.

See `examples/patterns/convergence-factory.dot` for the 7-node reference
shape with an inner + outer loop, and `examples/pipelines/practical/bug-fix.dot`
for a production-grade convergence pipeline.

## Live-run evidence

A controlled fixture run of this pipeline — mock LLM backend, tool handler
returning `gate_fail` on iteration 1 then `gate_pass` on iteration 2 — is
stored at:

```
examples/pipelines/practical/evidence/convergence-loop-2026-08-03/events.jsonl
```

Key events in that stream:

```
pipeline:edge_selected  test_gate -> implement  "fix and retry"   (loop fires)
pipeline:edge_selected  test_gate -> done        "tests pass"      (convergence)
pipeline:goal_gate_check  satisfied: ["test_gate"]
pipeline:complete  status: success
```

The corrective back-edge fires once before the success path — exactly the
pattern the tutorial teaches.

## What comes next

Every tutorial after this one is a variation on the bowl:

| Tutorial | What it adds |
|----------|--------------|
| `01-simple-linear.md` | Engine demo: the simplest possible pipeline (no gate, no loop) |
| `02-plan-implement-test.md` | Convergence tutorial: staged `plan → implement → test_gate` with `goal_gate` + `retry_target` + corrective back-edge (graduated from engine demo) |
| `03-conditional-routing.md` | Engine demo: `diamond` routing node |
| `04-retry-with-fallback.md` | Retry ladder demo + renegotiation exemplar: what to do when the basin is out of reach — explicit, recorded criteria relaxation (graduated from engine demo) |
| `05-parallel-fan-out.md` | Engine demo: `component` fan-out / `tripleoctagon` fan-in |

The canonical convergence exemplars (this file, `02-plan-implement-test`, `bug-fix`, `task-runner`) teach
the philosophy. The numbered engine-feature demos (01, 03-09, 12) teach individual
mechanisms; `10-full-attractor` is the extended application that layers every
feature onto this shape. See `examples/pipelines/README.md` for the full ramp.
