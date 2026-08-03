# Example Pipelines

Every pipeline is a Graphviz DOT graph the attractor engine walks node-by-node.
This folder has three kinds:

| Folder / file | What it is |
|---------------|-----------|
| **`00` + `01`–`12` (numbered)** | **Tutorials** — `00` and `02` are convergence tutorials (the bowl, then staged convergence); the rest of `01`–`12` are engine-feature demos, each isolating one mechanism. Self-contained: the goal is embedded in the `.dot`. |
| [**`practical/`**](practical/) | **Task pipelines** for real work — bug fix, refactor, test-gen, PR review, feature build, multi-lens review. Three ship a runnable sample. See [practical/README.md](practical/README.md). |
| [**`patterns/`**](../patterns/) | **Canonical attractor exemplars** (convergence factory, task-runner) and reusable DOT patterns to drop into your own graphs. These teach the philosophy; the numbered tutorials teach the mechanics. |
| [**`gates/`**](../gates/) | **Gate primitives** — copy-paste gate snippets (base-SHA anchor, delta-assertion, preflight evidence file) with the full gate contract in [gates/README.md](../gates/README.md). |

Each pipeline is a pair: a `.dot` (the graph) and a `.md` (the guide — start there).

## Start here: the convergence loop

**`00-convergence-loop`** is the first tutorial and the canonical shape. Read it
before anything else — every other tutorial is a variation on it or a demo of one
of its mechanisms.

> **The point in one sentence:** chains multiply variance; loops divide it. A
> linear chain of 0.90-probability nodes has ~0.53 end-to-end reliability; one
> corrective loop around the same nodes raises it to ~0.94.

The shape: `implement → test_gate → done` with a back-edge from `test_gate` to
`implement` when the gate fails. The exit is structurally unreachable until a
machine-checked signal fires.

## Canonical attractor exemplars

These files teach the philosophy — the convergence shape and why it matters:

| File | What it teaches |
|------|----------------|
| [00-convergence-loop.md](00-convergence-loop.md) | **The bowl** — minimal 4-node convergence loop. Start here. |
| [practical/bug-fix.md](practical/bug-fix.md) | The bowl applied to real work: inner fix loop + root-cause wall + outer feedback loop + budget wall. |
| [../patterns/task-runner.md](../patterns/task-runner.md) | Battle-hardened goal+DoD runner: orient/attempt/verify/critique/triage/postmortem/package. |
| [../patterns/convergence-factory.dot](../patterns/convergence-factory.dot) | Parent-injectable form of the bowl — for folder-node composition. |

## Convergence tutorials

These files teach the convergence shape with increasing complexity:

| # | Guide | What it teaches |
|---|-------|----------------|
| 00 | [00-convergence-loop.md](00-convergence-loop.md) | **The bowl** — minimal 4-node convergence loop. Start here. |
| 02 | [02-plan-implement-test.md](02-plan-implement-test.md) | Staged convergence: `plan → implement → test_gate` with `goal_gate` + `retry_target` + corrective back-edge |

Tutorial 02 graduated from an engine demo to a convergence tutorial: it now has
a corrective loop (tool gate + back-edge + budget), teaches `goal_gate` +
`retry_target` together, and explicitly owns the recipe-plane tension (staged
nodes as a teaching device, not a real-work pattern).

## Engine-feature demos

These files teach individual mechanisms. Each isolates one concept. Read them
after `00` when you need that specific feature:

| # | Guide | Mechanism it demos |
|---|-------|--------------------|
| 01 | [01-simple-linear.md](01-simple-linear.md) | Linear `A → B → C` — the simplest possible pipeline |
| 03 | [03-conditional-routing.md](03-conditional-routing.md) | `diamond` routing node / conditional branches |
| 04 | [04-retry-with-fallback.md](04-retry-with-fallback.md) | Retry ladder + explicit criteria renegotiation (recorded, gate-enforced downgrade) |
| 05 | [05-parallel-fan-out.md](05-parallel-fan-out.md) | `component` fan-out / `tripleoctagon` fan-in |
| 06 | [06-model-stylesheet.md](06-model-stylesheet.md) | CSS-like per-node model routing |
| 07 | [07-fidelity-modes.md](07-fidelity-modes.md) | Fidelity + `thread_id` context control |
| 08 | [08-human-gate.md](08-human-gate.md) | `hexagon` human-approval gate |
| 09 | [09-manager-supervisor.md](09-manager-supervisor.md) | `house` manager/supervisor loop |
| 10 | [10-full-attractor.md](10-full-attractor.md) | Kitchen-sink — every feature together |
| 11 | [11-manager-child-dotfile-hitl/](11-manager-child-dotfile-hitl/) | Multi-file: parent pipeline spawns a child pipeline with its own gate. **Regression fixture — known to fail via standalone `attractor run`; see its README.** |
| 12 | [12-graph-resume.md](12-graph-resume.md) | Graph-level resume via file-state guard nodes |

## Run any example from the CLI

The `attractor run` CLI executes a `.dot` directly. Because these pipelines have
**box (agent) nodes**, the run command has a specific shape — run it **from the
attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/00-convergence-loop.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd .
```

The tutorials are self-contained (the goal is baked into the `.dot`), so no
`--param goal=...` is needed. Two things about the command shape:

- **`$DOT` is captured absolute before `cd`** — the `.dot` path resolves from your
  *current* directory, so once you `cd` into the run dir a relative path won't find it.
- **Process cwd must equal `--cwd`** for box-node pipelines — the agent orchestrator
  roots writes at the process cwd, so we `cd` into the run dir and pass `--cwd .`
  (see [../../modules/pipeline-runner/KNOWN_ISSUES.md](../../modules/pipeline-runner/KNOWN_ISSUES.md)).

We run in a scratch dir (`/tmp/attractor-demo`) so any files a pipeline generates
don't land in your checkout. A couple of exceptions:

- **Human-gate pipelines** (`08`, `10`) block on a hexagon node. Add
  `--on-human-gate auto-approve` to run non-interactively — it always takes the
  gate's first option, so run it interactively (drop the flag) if you want the
  gate to actually branch.
- **`12-graph-resume`** refactors a real module in **your own repo** — point
  `--cwd` at your repo instead of a scratch dir. Its guide has the details.

## Or run from a bundle / recipe

Each tutorial guide also shows the **config** form — pointing the `loop-pipeline`
orchestrator at the `.dot` from a bundle, or invoking `attractor:pipeline-runner`
from a recipe step. Use that when you're embedding a pipeline in an app or
composing it into a larger workflow rather than running it ad-hoc from the CLI.

## Models

Every pipeline here is **model-agnostic** except the practical `multi-lens-review`
(which pins one provider per lens on purpose). To route specific nodes to specific
models, add a `model_stylesheet` — see [06-model-stylesheet.dot](06-model-stylesheet.dot).
