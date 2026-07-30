# Task Runner Sample Fixture

A self-contained planted-red demo for the task-runner exemplar. Run it in
minutes to see the corrective path fire live.

## What it contains

| File | Role |
|---|---|
| `sample-task.md` | Task description (goal + DoD + non-goals) |
| `sample-task.verify.sh` | Executable DoD script (the cheap gate) |

## The planted-red design

The DoD script fails at the runner's **first gate visit by design**. It keys on
`.ai/iter` — the runner's gate-visit counter, incremented only by the verify
gate. No matter what the worker does during the work phase, the first gate visit
goes red.

The second visit goes green if `.ai-demo/answer.txt` contains the sha256 digest
of `.ai-demo/nonce` (which the script creates on first call and prints in the
error output).

This guarantees the corrective path (`verify red → triage → attempt → verify
green`) executes at least once on every first run. A corrective basin that has
never absorbed a failure is decoration.

## How to run

From the **attractor repo root**:

```bash
DOT="$PWD/examples/patterns/task-runner.dot"
cp -r examples/patterns/task-runner-fixture /tmp/task-runner-demo
cd /tmp/task-runner-demo
git init -q && git add -A && git commit -qm "fixture baseline"
attractor run "$DOT" \
    --param task_file="$PWD/sample-task.md" \
    --param target_dir="$PWD" \
    --param max_iterations=6 \
    --cwd .
```

We copy to `/tmp` so the committed fixture stays pristine and every run starts
clean. `$DOT` is captured as an absolute path before `cd`. The `git init`
matters: the runner's ship path (`package` → `ship_check`) commits the finished
work to a `task/<id>` branch and verifies a clean tree — the target must be a
git repository, or the run ends at the `escalate` human gate instead of `done`.

## Expected convergence record

After the run, `.ai/convergence.jsonl` should contain two verify entries:

```jsonl
{"iteration": 1, "gate": "verify", "pass": false}
{"iteration": 2, "gate": "verify", "pass": true}
{"iteration": 2, "gate": "critique", "ship": true}
```

The first `pass: false` is the planted red. The second `pass: true` is the
corrective path succeeding. This is the descent curve the guide describes.
