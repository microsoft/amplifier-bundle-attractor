# Vision

What this repo is for, captured once so any change can be checked against it. Short by design: this
page is meant to be read before work, not archived after it.

It carries the *intent*. The mechanics live next door -- [`OPERATIONS.md`](OPERATIONS.md) holds what
each change owes, the decision matrix's per-tier tolls, and the observation convention;
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

**Objectives in, evidence-converged outcomes out.** *"The user might never pick an attractor
explicitly. The system infers it from the objective"* (maintainer, recorded design conversation).
The layering that implies: objective -> composition -> attractors -> execution
([`designs/2026-08-15-objective-layer.md`](designs/2026-08-15-objective-layer.md)).

---

## Our relationship to the nlspec

The governing rule for every change here is the **decision matrix** (maintainer ruling, 2026-08-15):

Every change here is weighed against the `strongdm/attractor` nlspec -- not code alone, but
behavior, philosophy, decision-making, design-thinking, process and documentation alike. Movement
that brings this project **more aligned** with the spec is the easy path: supported by default,
carrying the presumption of yes. Movement that would **drift** us away from the spec is made
genuinely hard and is readily pushed back on -- permitted only on measured evidence, and only as a
loud, ledgered divergence. Movement into territory the spec **does not address** meets real
resistance, though less of it: the silence has to be argued rather than assumed, and what ships
there stays additive and non-interfering. That gradient is the steering rule of this project.

Three postures, not two -- and resisted is not forbidden. The uncharted tier is a toll, not a wall:
every extension this repo carries passed through it, paying that toll on the way.

It is the general form of the four-rule **Compatibility doctrine** at the top of
[`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md) (maintainer ruling, 2026-08-14), which decides every
disposition in that ledger: honor the nlspec design where possible; 100% support for community
`.dot` files built against the nlspec; extensions additive and non-interfering; divergences only for
safety, backed by measured evidence, and always loud.

Its mechanical enforcement is the executable conformance matrix
(`specs/conformance/attractor-matrix.yaml` and its runner), which asserts decided *divergences* as
well as conformances -- because "drift is any movement not recorded in the ledger, in either
direction" (`OPERATIONS.md`, Layer 2). What each tier owes before it merges is
[`OPERATIONS.md` section 3](OPERATIONS.md).

---

## Governing contracts

This page states the intent. What *binds* it -- the contracts a change is measured against, and the
ledger that records where reality stands against them -- lives in two places, and neither of them is
a second copy of the other.

**The engine seam's contracts and ledger live in the engine repo.** `amplifier-bundle-dot-runner`
owns the engine, its specs machinery, and its conformance ledger: the frozen per-seam contracts under
its `contracts/external/`, and its clause-granular `ledger/rows.yaml`. This repo is the *opinionated
layer* on top of that seam -- graphs, examples, guidance surfaces, skills, agents. It owns no engine
seam, so it authors no engine contract. Where a claim here depends on engine behavior, the engine
repo's contract is the normative source; this repo cites it rather than restating it.

**This repo's quality machinery is the converge protocol itself, referenced and not duplicated.**
Vision-first, contract-driven change; the DRAFT -> FROZEN lifecycle and its Freeze Bar; the CANDIDATE
amendment protocol; the conformance ledger and its standing reconcile; the owner attention budget --
those are the ratified converge PROTOCOL v2, and this repo is governed by it as written. What stays
local is only what converge does not decide: this repo's own operating practice, which lives in
[`OPERATIONS.md`](OPERATIONS.md).

The rule that keeps this honest is the one that shaped this section: **one claim, one home.** A rule
restated locally is a rule that can drift from the protocol it claims to be, silently, under the same
name.

---

## The layers we converge on

A ladder, in the order the rungs rest on each other. Each is stated as the outcome it produces, not
as a step in a build order.

- **Node contracts carry intent and evidence.** A node publishes its objective, constraints,
  available tools, required evidence, and exit condition; an edge can mean *"objective not
  satisfied"* rather than *"next step"* -- the per-node contract shape behind
  `examples/objective/objective-runner.dot`.
- **The engine is evidence-gated end to end.** Goal gates fail closed (`specs/EXTENSIONS.md`
  section 25); a `must_write=` artifact contract cannot be satisfied by declaring success (27); no
  matching edge is a hard failure rather than an alphabetical guess (33); and `dot-runner lint` is a
  gate an author can put *inside* a graph (32).
- **An objective goes in; a verified outcome comes out.** Verified satisfaction, an honest
  redirect, or a loud escalation -- never a plausible report of success. The user need never pick an
  attractor; the system infers it from the objective (`examples/objective/`,
  [`designs/2026-08-15-objective-layer.md`](designs/2026-08-15-objective-layer.md)).
- **The operator steers a portfolio, not a run.** Orchestrator-as-a-service, an event-driven
  resident system, schedulers, dashboards, a portfolio layer -- the surface on which a person steers
  many convergences at once instead of babysitting one. From the vision conversation, recorded in
  the objective-layer design (section 8).

The ladder tops out there, honestly. Past the operator surface the sources record open *questions*
rather than a destination -- the spec's swappable `ExecutionEnvironment` seam (Local / Docker / K8s
/ WASM / SSH) is `CAL-3` in `SPEC_CONFORMANCE.md`, a call nobody has made yet. An undecided call is
a ledger row, not a vision. This page does not forge decisions the maintainer has not made.

---

## Operating principles

- **Let the models breathe.** Graphs carry intent and evidence, never algorithms. *"When you find
  yourself adding `plan -> implement -> test` as graph nodes, stop."*
- **Gates outside workers.** *"Verification inside the context that produced the evidence is not
  verification."* Bought with a live run where a worker hand-authored its own `convergence.jsonl`
  and the critics outside its context caught it. The rule binds the sessions and agents this repo
  composes, not only the graphs they design: what a session authored, it cannot certify -- it
  relays the machine's verdict as fact and offers the independent path.
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
- **One command; opinion arrives by composition.** `dot-runner` is the only entry point. A
  bundle's opinion -- which worker runs a node, which model backs it -- reaches the engine through
  composition (`--worker`, config), never through a second command name; bundles themselves are
  internal-only as of the engine's 0.2.0 repair release (`--bundle`/`DOT_RUNNER_BUNDLE` are removed
  from the CLI surface -- worker NAMES are the whole user-facing concept). A second binary is not a
  feature; it is the `.dot` file losing its claim to be the complete, self-contained workflow
  definition (spec 1.2).
- **The spec's own channels carry the outcome; extensions are retconned, not defended.** Artifact
  files, tool exit codes, and a node-written `status.json` (spec §4.5 / Appendix C) are the taught
  and implemented way a worker -- spawned or not -- delivers its verdict; a pure-JSON verdict is
  the sharpest reading of that same channel, and a legacy report tool was removed outright once its
  compatibility window closed (engine 0.2.0 repair release), never kept on as a parallel truth. When a deeper read of the nlspec shows a shipped
  extension was never the right shape, the correct move is to undo it -- demote, back out,
  deprecate -- ledgered exactly as loudly as the extension itself was ledgered in. Shipping first
  earns no immunity from being retconned later.
- **Community `.dot` files are never surprised.** A node property with an obvious spec-given
  meaning (`llm_provider` and its kind) behaves exactly that way out of the box; this repo's own
  ecosystem conventions get a vote only after the spec's own meaning is honored. Spec-first, then
  mapping -- never the other order.
- **Peer-system internals are off-limits.** What another runtime keeps private stays private; this
  repo reaches it only through the seam that runtime publishes, or by asking upstream for one. A
  reach-in that happens to work is still a reach-in, and "it worked" is not the bar.
- **The amplifier-agent bet.** For a surface this repo did not already have a worker for,
  amplifier-agent is the default; the fallback when it is unavailable is direct and loud, never a
  silent substitution.

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

- **This page states the desired state, never the current one.** It is the basin this project
  converges toward, written as though already true. What exists today, what is in flight, and what
  comes next are status and sequencing: they live in the ledgers
  ([`SPEC_CONFORMANCE.md`](../SPEC_CONFORMANCE.md), [`specs/EXTENSIONS.md`](../specs/EXTENSIONS.md)),
  in [`OPERATIONS.md`](OPERATIONS.md), and in the issue queue. A page that has to be
  edited when a layer ships is a status report wearing a vision's name.
- **Amendments require the maintainer's explicit word.** This is his vision, captured -- not a
  consensus document, and not an agent's inference.
- **Amendments carry evidence** -- a failure this framing would have caught, or a cost it retires.
  Restating a preference more forcefully is not evidence.
- **Amendments land in the Changelog below**, dated, with the evidence named. The Changelog is the
  amendment history; the sections above are only ever the current state.
- **Observations against this vision are captured, not swallowed.** Anyone -- human or agent --
  who notices the repo drifting from this page, or this page drifting from what we actually believe,
  files it per the *"if you see something, do something"* convention in
  [`OPERATIONS.md` section 4](OPERATIONS.md), without derailing the work that surfaced
  it.

---

## Changelog

Amendments to this vision, newest first. Each entry names the evidence that justified it.

### 2026-09-02 -- governing contracts named, and this page becomes the matrix's single home (entry 5)

- **Added.** A **"Governing contracts"** section: the engine seam's contracts and ledger live in
  `amplifier-bundle-dot-runner` (`contracts/external/`, `ledger/rows.yaml`), because this repo is the
  opinionated layer and owns no engine seam; and this repo's quality machinery *is* the ratified
  converge PROTOCOL v2, referenced rather than restated, with only local operating practice staying
  local (`docs/OPERATIONS.md`).
- **Changed.** This page is now the **single home** of the decision matrix's canonical articulation.
  It previously lived here *and* in `docs/QUALITY_PROTOCOL.md` section 3, pinned byte-identical
  across the two by `test_quality_protocol_guard.py`'s Q-307. The protocol page retired; the
  articulation did not move and is not edited -- what changed is that there is no second copy for it
  to drift from. Q-307 is re-aimed accordingly: it now pins this page's copy against a recorded
  constant **and** asserts the text exists exactly once across the docs corpus, so a silent edit here
  and a re-introduced second home both still fail loud.
- **Evidence that justified it: a measured cost this repo was paying.** The two-home articulation is
  the "one claim, N homes" failure the converge protocol names, and it had already cost a guard
  (Q-307) whose entire job was to detect the drift the duplication made possible -- machinery that
  exists only because the duplication exists. The governing-contracts gap is the same class,
  measured differently: contributors read this page before work, and it did not say where the binding
  contracts live, so a change touching engine behavior had no stated normative source to check
  against and the repo's own quality rules read as locally-invented rather than as the ratified
  protocol they are.
- **Scope: documentation only.** No engine, handler, example or ledger *behavior* changed. The
  decision matrix's articulation is byte-unchanged; the north star, the layers, the operating
  principles, the human's role and what we resist are untouched. Pointers into the retired protocol
  page were re-aimed at its surviving homes in the same PR.
- **Retirement condition.** The governing-contracts section retires if this repo ever owns an engine
  seam of its own -- at which point it would author contracts rather than cite them, and the section
  would be describing a division that no longer holds.

### 2026-08-29 -- the ruling-batch postures captured as operating principles (entry 4)

- **Changed.** "Operating principles" gains five statements, each a durable posture rather than a
  status report: one command with opinion arriving only through composition, never a second
  command name; the spec's own outcome channels (artifact files, exit codes, node-written
  `status.json`) as the taught and implemented way a worker reports its verdict, with a pure-JSON
  verdict as the sharpest reading and a legacy report tool surviving only as a dated compatibility
  window -- and a shipped extension retconned, not defended, once a deeper spec reading shows it
  was never the right shape; community `.dot` files never surprised by a node property the spec
  already gives obvious meaning, spec-first before this repo's own ecosystem mapping; peer-system
  internals off-limits, reached only through a published seam or an upstream ask; and the
  amplifier-agent bet for a surface this repo did not already have a worker for, with a loud direct
  fallback.
- **Evidence that justified it: the maintainer ruled a batch of decisions on 2026-08-29 and
  directed, in these words, that they be captured "in vision + contracts in the repo to maintain
  this guidance/focus going forward."** Every posture named here is already shipped reality, not a
  proposal: the single-command CLI (this repo's own PR #330, alongside `amplifier-bundle-dot-runner`
  PR #15); the status.json escalation ladder and the RETCON of `report_outcome` to a legacy window
  (this repo's own commit `552272d`, alongside `amplifier-bundle-dot-runner`'s PRs #16/#18/#332);
  the undo-audit RETCON posture for extensions generally (`amplifier-bundle-dot-runner` PR #19);
  `llm_provider` spec-first mapping (`amplifier-bundle-dot-runner` PR #17); the coordinator-mount
  reach-in's deletion (`amplifier-bundle-dot-runner` PR #18); and the amplifier-agent-default ruling
  (`amplifier-bundle-dot-runner` PR #20). The gap this closes is exactly the one section 4 exists to
  prevent: rulings that already shipped as code and ledger rows, with nowhere a contributor reads
  *before* work states them as the posture going forward.
- **Scope: documentation only.** No engine, handler, example or ledger *behavior* changed. The
  decision matrix's articulation -- pinned across both pages by `test_quality_protocol_guard.py`'s
  Q-307 -- is untouched.
- **Retirement condition.** Unchanged: none.

### 2026-08-16 -- the gates bind the assistant too (entry 3)

- **Changed.** "Gates outside workers" now says the rule binds the sessions and agents this repo
  composes, not only the graphs they design -- what a session authored, it cannot certify. One
  sentence, one home: the principle's other statement, "Evidence over self-report," is untouched.
- **Evidence that justified it: the maintainer read the open question and ruled** (2026-08-16,
  resolving issue #266). The failure this framing would have caught is on record -- a graded
  session taught the never-clause correctly, then, asked to vouch for a graph it had just written,
  certified the absence of self-report gates by self-report. And the cost it retires: the clause
  was carried only on derived guidance surfaces, which are byte-capped, so a later budget cut there
  could drop it without becoming drift the review that reads guidance against this page can find.
  Stated here, that instrument guards it.
- **Scope: documentation only.** No engine, handler, example or ledger *behavior* changed, and the
  decision matrix's articulation -- pinned across both pages by `test_quality_protocol_guard.py`'s
  Q-307 -- is untouched.
- **Retirement condition.** Unchanged: none.

### 2026-08-15 -- desired state only, and the matrix in authored prose (entry 2)

- **Changed, first.** This page states the **desired state** and never the current one. "The layers
  we are building," with its *shipped* / *deliberately parked* split, is now "The layers we converge
  on" -- the same ladder stated as the outcomes it produces, with no marker anywhere of what exists
  today. *"The long horizon"* came off the fourth north-star commitment for the same reason.
  "Maintaining this document" now says outright where status and sequencing live instead: the
  ledgers, the quality protocol, and the issue queue.
- **Changed, second.** The maintainer's raw ruling, previously blockquoted verbatim in "Our
  relationship to the nlspec," is replaced by one authored articulation of the same rule. The
  identical paragraph is the canonical statement in [`QUALITY_PROTOCOL.md`](QUALITY_PROTOCOL.md)
  section 3, and `test_quality_protocol_guard.py`'s Q-307 still pins the two copies to each other,
  re-anchored on the new text.
- **Evidence that justified both: the maintainer read the shipped page and ruled** (2026-08-15).
  On the first -- a vision that records what shipped has to be edited every time something ships,
  which makes it a status report competing with the ledgers rather than the thing they are measured
  against; the goal is the attractor basin this project converges toward, an eventual consistency,
  stated as the achieved outcome. On the second -- a quote reproduces the phrasing of a
  conversation, including its shorthand, and this page is read by people who were not in it.
- **What the honesty rule cost, and why that is the right price.** Cross-platform execution-contract
  convergence (`CAL-3`) left the ladder entirely: it is an *open* call, not a chosen destination,
  and promoting an undecided ledger row into the vision would have been this page forging a decision
  the maintainer has not made. The ladder now says where it tops out, and says why.
- **Scope: documentation only.** No engine, handler, example or ledger *behavior* changed. Every
  Q-300..Q-307 assertion was re-proved red by mutation and restored byte-identically.
- **Retirement condition.** Unchanged: none.

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
  parked layers are recorded as parked -- a stated posture, not a silent omission. *(Superseded by
  entry 2: this page no longer marks layers as shipped or parked at all.)*
- **Retirement condition.** None. This is the document other documents are measured against; it
  narrows or grows by amendment, and retires only if the project does.
