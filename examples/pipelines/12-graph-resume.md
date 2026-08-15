# 12 - Graph-Level Resume Pattern

> **Engine-feature demo** — this guide teaches a single mechanism: graph-level
> resume via file-state guard nodes. The implement/test loop at its core is
> Tutorial 00's convergence shape; the guards decorate it with durability
> (skip-if-done). For the canonical attractor shape itself, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## What This Exercises

- **Graph-level resume**: Making a pipeline resumable from the graph itself, with no engine
  resume involved. The engine always runs from Start; resume happens at the graph level via
  file-state self-skip. (Engine-level `attractor resume` is a separate, complementary
  mechanism -- see the closing section for when to reach for which.)
- **`shape=parallelogram` guard nodes**: Each check_* node runs a shell command that tests
  for an artifact file and prints a routing token (`done` or `todo`).
- **`context.tool.last_line` routing**: Guard nodes route via
  `condition="context.tool.last_line=done"` (skip) or `condition="context.tool.last_line=todo"`
  (do the work). The last non-empty stdout line of the tool_command is stored in
  `context["tool.last_line"]` automatically by the tool handler.
- **File-state artifacts**: Each stage writes a durable artifact (`.ai/smells.md`,
  `.ai/refactor-plan.md`, `.ai/snapshot.txt`, `.ai/STATE.json`). On the next run,
  the guard checks for the artifact and skips if it exists.
- **Developer rewind control**: Delete an artifact file to rewind to that stage. The engine
  re-evaluates from Start and naturally re-executes only the rewound stage and everything
  downstream. No engine support needed; no special flags; no "resume mode" in the engine.
- **Iterative-flag variant** (`STATE.json`): The implement+test loop uses a JSON file with
  a `tests_passed` boolean rather than a simple sentinel file, demonstrating the richer
  variant for stages with internal iteration.
- **Model stylesheet with `.reasoning` class**: Expensive reasoning-model nodes
  (`plan_refactor`, `diff_review`) keep their class even in the resume flow -- deleting
  the plan artifact forces a fresh plan at the full reasoning cost, which is the correct
  behavior.

## Pipeline Structure

```
start
  │
  ▼
check_smells ─[todo]─► analyze_smells ─┐
  │[skip]                               │
  └──────────────────────────────────── ▼
                                    check_plan ─[todo]─► plan_refactor ─┐
                                       │[skip]                           │
                                       └─────────────────────────────── ▼
                                                                   check_snapshot ─[todo]─► snapshot_tests ─┐
                                                                       │[skip]                               │
                                                                       └──────────────────────────────────── ▼
                                                                                                       check_tests_done ─[todo]─► implement_refactor ─► run_tests ─► test_gate
                                                                                                           │[skip]                                                      │[pass]
                                                                                                           │                                                            │
                                                                                                           └──────────────────────────────────────────────────────────► diff_review ─► done
                                                                                                                                                          [fix]◄─────────────────────┘
                                                                                                                                                       implement_refactor ◄────────────┘
```

Compact form:

```
start → check_smells ──[todo]──► analyze_smells ──► check_plan
              └──[skip]──────────────────────────────────►┘
                         check_plan ──[todo]──► plan_refactor ──► check_snapshot
                               └──[skip]──────────────────────────────────────►┘
                                        check_snapshot ──[todo]──► snapshot_tests ──► check_tests_done
                                               └──[skip]──────────────────────────────────────────────►┘
                                                          check_tests_done ──[todo]──► implement_refactor ──► run_tests ──► test_gate ──[pass]──► diff_review ──► done
                                                                └──[skip]────────────────────────────────────────────────────────────────────────────►┘
                                                                                                                                    [fix] ◄────────────────┘
```

## Stage Artifacts

| Stage | Guard node | Work node | Artifact | Rewind by |
|-------|-----------|-----------|----------|-----------|
| 1 | `check_smells` | `analyze_smells` | `.ai/smells.md` | `rm .ai/smells.md` |
| 2 | `check_plan` | `plan_refactor` | `.ai/refactor-plan.md` | `rm .ai/refactor-plan.md` |
| 3 | `check_snapshot` | `snapshot_tests` | `.ai/snapshot.txt` | `rm .ai/snapshot.txt` |
| 4/5 | `check_tests_done` | `implement_refactor` + `run_tests` loop | `.ai/STATE.json` | `rm .ai/STATE.json` |
| 6 | (none) | `diff_review` | — | `rm .ai/STATE.json` then rerun |

## First Run vs. Resume Run

### First Run (no artifacts)

```
start
  → check_smells           [tool_command exits 0, last_line="todo"]
  → analyze_smells         [writes .ai/smells.md]
  → check_plan             [last_line="todo"]
  → plan_refactor          [writes .ai/refactor-plan.md]
  → check_snapshot         [last_line="todo"]
  → snapshot_tests         [writes .ai/snapshot.txt]
  → check_tests_done       [STATE.json missing → last_line="todo"]
  → implement_refactor     [writes .ai/STATE.json: {"tests_passed": false}]
  → run_tests
  → test_gate              [runs pytest -q; prints "pass" or "fail"]
    → [pass] diff_review   [run_tests also updated STATE.json: {"tests_passed": true}]
    → done
```

### Resume Run (crash after snapshot_tests, before implement_refactor)

All three artifact files exist (`.ai/smells.md`, `.ai/refactor-plan.md`, `.ai/snapshot.txt`),
but `.ai/STATE.json` does not:

```
start
  → check_smells       [.ai/smells.md exists    → last_line="done"] → SKIP
  → check_plan         [.ai/refactor-plan.md exists → last_line="done"] → SKIP
  → check_snapshot     [.ai/snapshot.txt exists  → last_line="done"] → SKIP
  → check_tests_done   [STATE.json missing        → last_line="todo"]
  → implement_refactor [resumes from here -- expensive LLM stages 1-3 are skipped]
  → run_tests → test_gate → diff_review → done
```

The engine ran from Start every time. No goto, no jump, no engine resume API.

## Expected Behavior

### First run
All guard nodes print `todo`; all work nodes execute in sequence. The implement+test loop
may iterate if tests fail: `test_gate` (parallelogram) runs `pytest -q` directly and prints
`fail` -> routes back to `implement_refactor`. The loop writes `{"tests_passed": false}` each
time until all tests pass, then `{"tests_passed": true}`, and `test_gate` prints `pass` -> routes
to `diff_review`.

**Why `test_gate` is a parallelogram, not a diamond**: All guard nodes in this pipeline
(`check_smells`, `check_plan`, `check_snapshot`, `check_tests_done`, and now `test_gate`) use
`shape=parallelogram` + `tool_command` + `context.tool.last_line` routing. This is consistent
and correct. The diamond handler (`ConditionalHandler`) unconditionally returns SUCCESS, so
`condition="outcome!=success"` from a diamond is always false -- the implement+test loop would
never fire. The parallelogram gate runs the real verifier and routes on observed evidence.

### Resume after crash (any stage)
Guard nodes for completed stages print `done` (their artifact exists); their work nodes are
bypassed. The first guard that prints `todo` (its artifact is missing) is where execution
resumes. All downstream nodes execute normally.

### Rewinding a stage
Delete one or more artifact files and re-run. The guard for the deleted artifact prints `todo`,
causing the work node to re-execute. All downstream guards inherit fresh artifacts and
re-execute their work nodes as well.

```bash
# ($DOT and the goal are as set in "How to Run" below; run these from your repo
#  so the .ai/ artifacts and --cwd . line up.)

# Re-run only the plan stage and everything after it:
rm .ai/refactor-plan.md && attractor run "$DOT" --param goal="..." --cwd .

# Re-run the implement+test loop and diff review only:
rm .ai/STATE.json && attractor run "$DOT" --param goal="..." --cwd .

# Start completely fresh:
rm -rf .ai/ && attractor run "$DOT" --param goal="..." --cwd .
```

## How to Run

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Refactor the legacy module"
    context:
      pipeline_path: "examples/pipelines/12-graph-resume.dot"
      goal: "Refactor src/legacy.py to eliminate god-class anti-patterns"
```

Or with the `attractor run` CLI directly. This example refactors a real module
(`src/legacy.py`) in **your own repo**, so point it there -- capture the pipeline's
absolute path, `cd` into your repo, and keep `--cwd .`:

```bash
DOT="/path/to/attractor/examples/pipelines/12-graph-resume.dot"
cd /path/to/your/repo        # must contain src/legacy.py and a test suite
attractor run "$DOT" \
    --param goal="Refactor src/legacy.py to eliminate god-class anti-patterns" \
    --cwd .
```

The `.dot` path resolves from your current directory, but box-node (agent) pipelines
need process cwd to equal `--cwd` -- so `cd` into your repo, give `$DOT` an absolute
path, and keep `--cwd .` (see [`../../modules/pipeline-runner/KNOWN_ISSUES.md`](../../modules/pipeline-runner/KNOWN_ISSUES.md)).
The resume artifacts (`.ai/`) are written under `--cwd`.

## What to Look For

- **Guard node logs** (`check_smells/output.txt`, etc.): show `done` or `todo` on the last
  line, confirming the routing token the engine will use.
- **Edge selection logs**: show `condition="context.tool.last_line=todo"` matched (or `=done`
  matched) for each guard, and which outgoing edge was chosen.
- **Skipped work nodes**: on a resume run, `analyze_smells/`, `plan_refactor/`, etc. will
  have no log directory (or only from a prior run) -- confirming the skip.
- **`context["tool.last_line"]`** in event logs: the routing token from the most recent guard.
- **`checkpoint.json`**: on a resume run, completed stage IDs appear in `completed_nodes`
  but the stages are not re-executed -- the guards self-skip before the engine would
  need to track them.
- **`STATE.json` evolution**: starts as `{"tests_passed": false}` after `implement_refactor`,
  updates to `{"tests_passed": true}` after a passing `run_tests`.

## Key Insight: This and Engine-Level Resume Answer Different Questions

Graph-level resume is not a substitute for `attractor resume`, and `attractor resume` did
not retire this pattern. They coexist by design -- that is the ratified ruling, not a
compromise (`docs/designs/2026-08-14-engine-checkpoint-resume.md` §0: *"Engine resume must
be built per §5.3 and must **coexist** with the graph-owned file-guard idempotency
pattern. Neither disables the other."*). They answer different questions:

| | Answers | Reach for it when |
|---|---|---|
| **Graph-owned file guards** (this example) | "is this work already done *on disk*?" | Stages are expensive and idempotent, the graph may be edited between runs, or you want per-artifact rewind |
| **Engine `resume`** (§5.3) | "did this *process* die mid-graph?" | A crash, kill or lost machine left in-flight engine state -- retry counters, `$iteration`, accumulated context -- that no filesystem artifact can reconstruct |

What this pattern buys you, and the engine cannot:

- Guards re-evaluate the real filesystem on every run, so the state they read is the truth
  on disk rather than a record of what a previous run believed.
- Editing the graph and re-running works: the engine evaluates edges fresh from Start, and
  guards adapt to whatever graph is in front of them.
- Deleting one artifact file is a surgical rewind of exactly one stage, with no engine
  state to reset and no run directory to find.
- It needs no engine support at all, so it works identically on any conformant runtime.

What engine resume buys you, and this pattern cannot: the accumulated in-memory state of a
run that died. A file guard can tell you `implement_refactor` finished; it cannot tell you
how many retries `run_tests` had already spent, or restore the context the earlier nodes
accumulated. Resume is explicit opt-in -- a plain `attractor run` never reads a checkpoint
-- so adding it changes nothing about how this graph behaves.

For the mechanism and its guarantees see the design record above and
[`docs/DOT-AUTHORING-GUIDE.md`](../../docs/DOT-AUTHORING-GUIDE.md); the conformance
position is `SPEC_CONFORMANCE.md` ATX-2.
