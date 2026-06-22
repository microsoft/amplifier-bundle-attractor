# 12 - Graph-Level Resume Pattern

## What This Exercises

- **Graph-level resume**: The correct way to make a pipeline resumable. The engine always
  runs from Start; resume happens at the graph level via file-state self-skip.
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
  → test_gate
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
may iterate if tests fail (`test_gate → implement_refactor → run_tests`), writing
`{"tests_passed": false}` each time until all tests pass, then `{"tests_passed": true}`.

### Resume after crash (any stage)
Guard nodes for completed stages print `done` (their artifact exists); their work nodes are
bypassed. The first guard that prints `todo` (its artifact is missing) is where execution
resumes. All downstream nodes execute normally.

### Rewinding a stage
Delete one or more artifact files and re-run. The guard for the deleted artifact prints `todo`,
causing the work node to re-execute. All downstream guards inherit fresh artifacts and
re-execute their work nodes as well.

```bash
# Re-run only the plan stage and everything after it:
rm .ai/refactor-plan.md && amplifier run ...

# Re-run the implement+test loop and diff review only:
rm .ai/STATE.json && amplifier run ...

# Start completely fresh:
rm -rf .ai/ && amplifier run ...
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

Or with the CLI directly:

```bash
amplifier run \
  --dot-file examples/pipelines/12-graph-resume.dot \
  --goal "Refactor src/legacy.py to eliminate god-class anti-patterns"
```

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

## Key Insight: Why This Is Better Than Engine-Level Resume

Engine-level resume (fast-forward replay from a checkpoint) has two failure modes:

1. **Edge-structure mismatch**: If the graph's edge conditions do not match the saved routing
   path (e.g. after editing the graph, or when resuming a different graph at the same
   checkpoint path), the engine fails with "No matching edge from resumed node."

2. **Graph drift**: Engine resume bakes in routing decisions from the previous run. If the
   graph changed between runs, the engine blindly replays stale decisions.

Graph-level resume has neither problem:
- The engine always evaluates edges fresh from Start.
- Guard nodes re-evaluate the real filesystem state on every run.
- Editing the graph and re-running works correctly -- guards adapt to the current graph.
- Deleting an artifact file is a surgical rewind; no engine state to reset.
