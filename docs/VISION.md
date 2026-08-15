# Vision

What this repo is for, captured once so any change can be checked against it. Short by design: this
page is meant to be read before work, not archived after it.

It carries the *intent*. The mechanics live next door -- [`QUALITY_PROTOCOL.md`](QUALITY_PROTOCOL.md)
holds what each change owes, the decision matrix's per-tier tolls, and the observation convention;
[`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) holds the ledger of every deliberate deviation.

---

## North star

The upstream nlspec states the aspiration this repo carries forward, in its own words:

> The graph is the workflow: nodes are tasks, edges are transitions, and attributes configure
> behavior.
>
> Pipeline authors do not write control flow; they declare graph structure.
>
> -- `specs/canonical/attractor-spec-canonical.md`, sections 1.1 and 1.3

Four commitments follow, and together they are what *attractor* adds to *workflow*:

**The graph is the program.** Not a picture of one. Nodes are computation, edges are dispatch,
clusters are subgraphs (`PRINCIPLES.md`). The `.dot` file is "a complete, self-contained workflow
definition that can be version-controlled, diffed, and reviewed in pull requests" (spec 1.2) -- which
is also why its rendering is a truthful diagram: it is generated from the thing that runs.

**Convergence on machine evidence, not step completion.** The exit is gated on evidence that is
machine-checkable and *external to the worker*, never on a stage reporting itself finished
(`PIPELINE_DESIGN_PRINCIPLES.md` section 0).

**Deterministic macro-control containing adaptive micro-control.** The graph owns the convergence
skeleton -- gates, budgets, walls, feedback channels. The model owns the domain decomposition,
because the model is the part that can adapt when the domain surprises it (section 0).
**Orchestrator RUNS, graph DEFINES, model ADAPTS.**

**The long horizon: objectives in, evidence-converged outcomes out.** *"The user might never pick an
attractor explicitly. The system infers it from the objective"* (maintainer, recorded design
conversation). The layering that implies: objective -> composition -> attractors -> execution
([`designs/2026-08-15-objective-layer.md`](designs/2026-08-15-objective-layer.md)).

---

## Our relationship to the nlspec

The governing rule for every change here is the **decision matrix** (maintainer ruling, 2026-08-15):

> any changes in behavior/philosophy/decision-making/design-thinking (all the things) should
> consider the strongdm/attractor nlspec -- it should be EASY/SUPPORTED to go the desired direction
> if it means bringing us more aligned to it; it should be REALLY HARD/readily pushed back on if it
> were to drift us from it; and RELATIVELY RESISTED if it takes us into uncharted
> (non-specified/absent from the nlspec) territory.

Three postures, not two. Toward the spec: the presumption is yes. Away from it: readily pushed back
on. Spec-silent territory: *relatively* resisted -- resisted, not forbidden, because the spec's
silence is not automatically a signal, and saying why it isn't is part of the price of going there.
The matrix governs **all the things** -- code, docs, examples, philosophy, design-thinking, process
-- not only conformance-bearing code.

It is the general form of the four-rule **Compatibility doctrine** at the top of
[`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) (maintainer ruling, 2026-08-14), which decides every
disposition in that ledger: honor the nlspec design where possible; 100% support for community
`.dot` files built against the nlspec; extensions additive and non-interfering; divergences only for
safety, backed by measured evidence, and always loud.

Its mechanical enforcement is the executable conformance matrix
(`specs/conformance/attractor-matrix.yaml` and its runner), which asserts decided *divergences* as
well as conformances -- because "drift is any movement not recorded in the ledger, in either
direction" (`QUALITY_PROTOCOL.md`, Layer 2). What each tier owes before it merges is
[`QUALITY_PROTOCOL.md` section 3](QUALITY_PROTOCOL.md).

---

## The layers we are building

**Shipped.**

- **The intent-and-evidence node doctrine.** A node publishes its objective, constraints, available
  tools, required evidence, and exit condition; an edge can mean *"objective not satisfied"* rather
  than *"next step"* -- realized as the per-node contract table behind
  `examples/objective/objective-runner.dot`.
- **The evidence-gated engine.** Fail-closed goal gates (`specs/EXTENSIONS.md` section 25), the
  `must_write=` artifact contract (27), the no-matching-edge hard fail (33), and `attractor lint` as
  a gate an author can put *inside* a graph (32).
- **The objective layer.** `examples/objective/` -- an objective in; verified satisfaction, an
  honest redirect, or a loud escalation out.

**Horizon -- deliberately parked.** Parked is a stated posture, not an omission.

- **The operator / portfolio surface** -- orchestrator-as-a-service, an event-driven resident
  system, schedulers, dashboards, a portfolio layer. Named in the vision conversation, ruled out of
  scope for now (objective-layer design, section 8).
- **Cross-platform execution-contract convergence** -- the spec's swappable `ExecutionEnvironment`
  seam (Local / Docker / K8s / WASM / SSH). `CAL-3` in `SPEC_CONFORMANCE.md`: deferred by the owner,
  context captured "so the future call is well-informed."

---

## Operating principles

- **Let the models breathe.** Graphs carry intent and evidence, never algorithms. *"When you find
  yourself adding `plan -> implement -> test` as graph nodes, stop."*
- **Gates outside workers.** *"Verification inside the context that produced the evidence is not
  verification."* Bought with a live run where a worker hand-authored its own `convergence.jsonl`
  and the critics outside its context caught it.
- **Evidence over self-report.** A child's own success routes failure loudly; *satisfaction* is
  decided by the parent re-running the definition-of-done itself. On one live proof a worker had
  genuinely fixed the bug and written "Status: COMPLETE" -- and the run still refused to report
  success, because the machine evidence was absent.
- **Fail loud; never fall back silently.** No matching edge hard-fails rather than drifting to
  alphabetical order (`specs/EXTENSIONS.md` 33); provider resolution refuses rather than substitutes
  (36); a divergence "that resolves quietly toward *success* is the failure mode this doctrine
  exists to prevent" (doctrine rule 4).
- **The honest no is a deliverable.** A run whose finding is *"this wants a recipe, a conversation,
  or a one-shot"* exits green with that written up. Our own work too: `DEAD-1` ended in "delete and
  document" rather than an invented channel.
- **Additive and non-interfering toward community `.dot` files.** A spec-conformant graph must run
  here unmodified; "an extension that breaks a conforming graph is a bug, not an extension."

---

## The human's role

Operator and coach, not step-executor. The engine "can pause at designated nodes, present choices to
a human operator, and route based on the human's decision ... critical for AI workflows where
automated judgment may not be sufficient" (spec 1.3).

**Approvals are consequential, and gates are earned.** A human gate sits *after* machine evidence has
been established, so the person decides something real instead of rubber-stamping a step -- and the
first choice offered is always "the one that does not fabricate an outcome" (objective-layer design,
11.1). Repo-level, the same shape: no merge without the maintainer's explicit word, on top of a green
required check.

**Attention is the budgeted resource.** Every gate, guard and rule costs "runtime, review attention,
and the friction it adds to every unrelated change that has to walk past it" -- which is why
machinery whose retirement condition has fired is removed, not deprecated. Spending a person's
attention on what a machine could decide is the same error as spending a model on what a shell
command could decide.

---

## What we deliberately resist

- **Recipe-thinking in exemplars.** A graph that hardcodes cognitive phases teaches the anti-pattern
  by example -- *"the graph swallowed the intelligence."* *"If your pipeline graph has no cycle, it
  should probably have been a recipe."*
- **The uber-attractor.** No adaptive mega-node with a self-assessed exit; a runner routes to
  *separate* children carrying their own gates. "One adaptive mega-node with self-assessed exit
  would have shipped the fabrication."
- **Framework gravity -- a pipeline where a script would do.** The three-question test exists to
  answer *no* as readily as *yes*, and the tooling that fronts it is built to say so.
- **Primitives invented ahead of the evidence.** No learned template selection, no auto-tuning, no
  channel invented to justify a config field. New machinery needs "a failure this change would have
  caught, or a cost this change retires" -- *"it seems more rigorous" is not evidence.*

---

## Maintaining this document

The vision refines over time, so this page is held to the bar of the protocol that enforces it:

- **Amendments require the maintainer's explicit word.** This is his vision, captured -- not a
  consensus document, and not an agent's inference.
- **Amendments carry evidence** -- a failure this framing would have caught, or a cost it retires.
  Restating a preference more forcefully is not evidence.
- **Amendments land in the Changelog below**, dated, with the evidence named. The Changelog is the
  amendment history; the sections above are only ever the current state.
- **Observations against this vision are captured, not swallowed.** Anyone -- human or agent --
  who notices the repo drifting from this page, or this page drifting from what we actually believe,
  files it per the *"if you see something, do something"* convention in
  [`QUALITY_PROTOCOL.md` section 4](QUALITY_PROTOCOL.md), without derailing the work that surfaced
  it.

---

## Changelog

Amendments to this vision, newest first. Each entry names the evidence that justified it.

### 2026-08-15 -- vision captured (entry 1)

- **Established.** Maintainer ruling: capture the vision this project is being steered toward in one
  canonical document, with the **decision matrix** as its governing rule -- three postures toward the
  `strongdm/attractor` nlspec, applying to "all the things," not only to conformance-bearing code.
- **Evidence that the capture earns its cost.** The vision already existed, scattered across six
  surfaces that each carried a piece and none of which claimed the whole: the Compatibility
  doctrine (`SPEC_CONFORMANCE.md`), the control-plane/recipe-plane line and three-question test
  (`PIPELINE_DESIGN_PRINCIPLES.md` section 0), the objective-layer charter and its non-goals, the
  quality protocol's drift model, the README's "objective layer" paragraph, and the upstream spec's
  section 1. `QUALITY_PROTOCOL.md`'s Layer 3 already named "the repo's stated vision" as something
  the holistic review reads against -- while no single document stated it. That gap is what this
  page closes; nothing here is invented, and every claim traces to one of those sources or to the
  ruling.
- **Scope: documentation only.** No engine, handler, example or ledger *behavior* changed. The
  parked layers are recorded as parked -- a stated posture, not a silent omission.
- **Retirement condition.** None. This is the document other documents are measured against; it
  narrows or grows by amendment, and retires only if the project does.
