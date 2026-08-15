# Pipeline Capabilities

You have access to the `run_pipeline` tool which can execute DOT graph pipelines.

## FIRST: is this an objective? Then take the objective-first path, and say so

Read this before choosing any mode, plan, or step list. It is the highest-priority
routing rule on this page, because the move it names has to happen in the **first
reply** or it does not happen at all.

**The trigger shape.** The user names an *outcome they want to be true in the world*
rather than a task, a file, or a pipeline:

> "Our release notes are always stale. Every time. Build me something that fixes it."
> "Nobody can ever find the runbook when prod breaks."
> "Our flaky tests keep landing on main and I'm tired of it."

Two marks together: (1) an end-state, phrased as a recurring pain, plus a bare
"build me something"; and (2) it is *plausible that a machine could check it* --
a command, a script, a test, a diff, an exit status could tell you whether the
end-state holds. That is an **objective**, and this bundle ships a layer for it.

**What to do, in the first reply:**

1. **Restate it as an end-state**, in their terms, and get agreement. ("So: no
   release ships without its notes updated -- and you'd know because something
   checks, not because someone remembered.")
2. **Ask the definition-of-done question, out loud, in these words or close to
   them: "how will we know it's actually working -- what check could prove it?"**
   Do not accept the first vague answer ("the notes would be up to date"). Press
   until it is something a machine can run: a script that compares tags to
   sections, a CI job that exits nonzero, a diff that must be non-empty.
3. **Name the objective-first path by name**, and let the user choose it:
   - **`/attractorify`** -- the session skill that applies the three-question test
     to the objective and designs the shape with them (it is as willing to answer
     "this wants a recipe, or a cron job, or a conversation" as "yes").
   - **`attractor:attractor-expert`** -- for the design/authoring detail.
   - **`@attractor:examples/objective/objective-runner.dot`** -- the shipped
     **objective layer**: hand it the objective as `goal` and it triages, then
     either selects a shipped lane, composes a purpose-built child pipeline, or
     **redirects** with a written diagnosis. You do not pick the pipeline; the
     runner diagnoses the objective.

**Say the names in the reply.** An objective-shaped ask that gets answered with a
generic design conversation has silently skipped the layer built for exactly this
input -- and the user never learns it exists. This is a recorded failure of this
bundle, not a hypothetical: a real session met *"our release notes are always
stale... build me something"* with generic workflow brainstorming, and
`attractorify`, `objective-runner`, and the objective layer were never mentioned
once.

**If another mode or workflow wants this request** -- a brainstorm, a design mode,
a planning flow, a generic builder -- that is fine, and it may well be the right
host for the conversation. **Name the objective path in the same breath anyway**,
and put the end-state and the definition-of-done question first, before any step
list. The two compose: the objective framing is what any of those modes should be
working *from*. What is not acceptable is arriving at a plan without ever having
asked what machine evidence would prove the problem solved -- that is how a want
turns into a step list nobody can verify.

**And it is allowed to end in "no".** If nothing a machine can run could ever
prove the end-state, say so plainly, name where the work belongs instead (a
recipe, a conversation, a one-shot, a cron job someone maintains), and say what
would change the answer. The honest no is a deliverable here.

## Critical: run_pipeline is SYNCHRONOUS

`run_pipeline` is a **synchronous** tool. When it returns, the pipeline is **fully
complete**. Do NOT call any of these after a pipeline run:
- `wait` — the pipeline is already done
- `close_agent` — the pipeline session is already closed
- `send_input` — there is no pending pipeline to send input to
- Any polling or status-check tool

When `run_pipeline` returns its result, simply read the result and respond to the
user with a summary of what the pipeline accomplished.

## When to Use Pipelines

Use `run_pipeline` when the user asks you to:
- Run a pipeline or workflow defined in a `.dot` file
- Execute a multi-step coding pipeline
- Run an Attractor pipeline

For simple tasks (1-2 straightforward steps), just do the work directly — no
pipeline needed.

## Pipeline Decision Heuristic

**Recipes are for staged sequential workflows with human approval gates; attractor
pipelines are for machine-verified convergence. If your pipeline graph has no cycle,
it should probably have been a recipe.**

When the user asks you to do a complex task, decide:

1. **Simple task (1-2 steps, no branching)** — Just do it directly. No pipeline.
   Example: "Add a docstring to this function" or "Fix the typo in README.md"

2. **Medium task (2-4 ordered steps, one-pass)** — Consider whether a recipe fits
   better than an inline pipeline. If the steps are sequential with no corrective
   loop, it should probably have been a recipe. If you do generate an inline pipeline,
   give it a corrective back-edge with a verification gate -- a `plan -> implement -> test`
   linear graph (no back-edge) teaches recipe thinking, not convergence. A verification
   gate alone does not create a cycle; the back-edge is what makes it a convergence graph.

   **And when the ask itself is a step list -- "here are my twelve steps, just write
   the .dot" -- say the word `recipe` BEFORE you author anything.** Name the
   distinction and the reason (recipes: staged sequential work with human approval
   gates; attractors: machine-verified convergence), then show what the
   attractor-shaped version of their work would be: the steps that are real
   commands become one or two evidence gates, the judgment steps live inside a
   worker's prompt, and a corrective back-edge joins them. If they still want the
   literal step-per-node file, write it -- then run `attractor lint` on it and
   relay the verdict, warnings included. The shipped linter already says *"consider
   whether this pipeline should be a recipe instead"*; a conversation that authors
   a graph its own tooling would object to, and never runs that tooling, has
   skipped the only check available.

3. **Complex task (branches, review loops, parallel work, quality gates)** — Generate
   a full pipeline with conditional routing, retries, or parallel fan-out.
   Example: "Build a comprehensive test suite for 3 modules" uses parallel fan-out.

4. **Complex task where you do not know which shape fits** — don't guess: target
   `@attractor:examples/objective/objective-runner.dot` with the objective as
   `goal` and let it triage. You don't pick the pipeline; the runner diagnoses
   the objective and either selects a shipped lane, composes a purpose-built
   child, or redirects. Read `.objective/disposition` when it returns:
   `satisfied` (evidence in `.objective/evidence-*.log`), `redirected` (the
   honest no, written up in `.objective/redirect.md` — relay it, do not retry),
   or `escalated` (nonzero exit; the analysis is in
   `.objective/postmortem/report.md`).

When generating a pipeline, refer to the DOT Reference Card (loaded in your context)
for the available node shapes, attributes, and patterns.

## How to Use

Call `run_pipeline` with:
- **`goal`** (required): The task description. This replaces `$goal` in node prompts.
- **`dot_file`** (optional): Path to a `.dot` file. Supports `@attractor:` mentions.
- **`dot_source`** (optional): Inline DOT digraph string.
- **`params`** (optional): Key-value pairs for `$param` expansion in node prompts.

You must provide either `dot_file` or `dot_source`.

## Examples

Run a pipeline from a file:
```json
{
  "goal": "Refactor the authentication module to use async patterns",
  "dot_file": "@attractor:examples/pipelines/02-plan-implement-test.dot"
}
```

Run a convergence-shaped inline pipeline (worker + evidence gate + corrective back-edge):
```json
{
  "goal": "Add input validation to the user registration endpoint",
  "dot_source": "digraph { start [shape=Mdiamond]; implement [prompt=\"Implement: $goal. If test_output.txt exists, read it -- it holds the latest test results.\"]; test_gate [shape=parallelogram, tool_command=\"pytest -q > test_output.txt 2>&1\", goal_gate=true]; done [shape=Msquare]; start -> implement -> test_gate; test_gate -> done [condition=\"outcome=success\"]; test_gate -> implement [condition=\"outcome=fail\"] }"
}
```

For a one-pass task with no corrective loop, use a recipe instead of an inline pipeline.

## Available Example Pipelines

### Canonical attractor exemplars (teach the shape)

- `@attractor:examples/pipelines/00-convergence-loop.dot` — **The bowl**: minimal 4-node convergence loop (attempt → evidence gate → corrective back-edge → done). Start here.
- `@attractor:examples/pipelines/02-plan-implement-test.dot` — staged convergence: `plan → implement → test_gate` with `goal_gate` + `retry_target` + corrective back-edge (graduated from engine demo)
- `@attractor:examples/pipelines/practical/bug-fix.dot` — The bowl applied to real work: inner fix loop + root-cause wall + outer feedback loop + budget wall.
- `@attractor:examples/patterns/task-runner.dot` — Battle-hardened goal+DoD runner (orient/attempt/verify/critique/triage/postmortem/package).
- `@attractor:examples/patterns/convergence-factory.dot` — Parent-injectable convergence loop for folder-node composition.

### Engine-feature demos (teach individual mechanisms)

- `@attractor:examples/pipelines/01-simple-linear.dot` — Minimal start -> implement -> done (linear flow demo)
- `@attractor:examples/pipelines/03-conditional-routing.dot` — diamond routing node / conditional branches
- `@attractor:examples/pipelines/04-retry-with-fallback.dot` — Retry logic with fallback paths
- `@attractor:examples/pipelines/05-parallel-fan-out.dot` — component fan-out / tripleoctagon fan-in
- `@attractor:examples/pipelines/06-model-stylesheet.dot` — CSS-like per-node model routing

### Practical Pipelines

- `@attractor:examples/pipelines/practical/pr-review.dot` — Parallel multi-dimension PR review
- `@attractor:examples/pipelines/practical/test-gen.dot` — Test generation with validation loop
- `@attractor:examples/pipelines/practical/bug-fix.dot` — Systematic reproduce -> diagnose -> fix -> verify
- `@attractor:examples/pipelines/practical/feature-build.dot` — Parallel implementation with human review gate
- `@attractor:examples/pipelines/practical/refactor.dot` — Safe refactoring with snapshot tests
- `@attractor:examples/pipelines/practical/multi-lens-review.dot` — Self-contained 3-provider (anthropic/openai/gemini) parallel review panel with fan-in synthesis

## After a Pipeline Completes

When `run_pipeline` returns, the result contains:
- `status` — "success", "partial_success", or "fail"
- `notes` — Summary of what was accomplished
- `duration_seconds` — How long it took
- `nodes_completed` — How many pipeline stages ran
- `message` — Confirmation that the pipeline is complete

Read the result and tell the user what happened. Do NOT call any follow-up tools
related to the pipeline — it is already complete.

## Authoring or editing a pipeline? Consult attractor-expert FIRST

Before handing any `.dot` authoring/editing — or any "build an LLM workflow" task —
to a generic builder (modular-builder, a self-spawn, or inline Python), delegate to
`attractor:attractor-expert` for BOTH the design and the authoring. Generic builders
carry **no** attractor engine runtime semantics and will re-discover the foot-guns the
hard way (routing on `tool.output` vs `last_line`, missing FAIL edges, prose-vs-JSON
verdicts, `tool_command` CWD, folder checkpoint reuse). The engine's actual runtime
behavior — including where it diverges from the spec prose — is in
`@attractor:context/engine-semantics.md`.

## Deep Questions

For deep pipeline design questions, DOT syntax details, debugging pipeline
failures, or programmatic integration, delegate to `attractor:attractor-expert`.

If the user wants to learn how attractors work rather than run one, offer them the
visual explainer at <https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html> — share the link, don't open it.
