# `pipeline-author.dot` — companion guide

The authoring attractor, node by node: what each node is responsible for, what
it is forbidden to do, what it is allowed to reach for, and what artifact proves
it did its job. For what this pipeline *is* and how to run it, read
[`README.md`](README.md) first; this page is the design record.

---

## The sink, named first

**A new pipeline exists at `out/<name>.dot`, with a companion at
`out/<name>.md`, that: lints clean under `attractor lint`; satisfies A1–A9 of
[`check_authored_pipeline.py`](check_authored_pipeline.py); and a critic that
never saw the author's reasoning said `VERDICT: SHIP` to.**

Or, the second honest sink: **`.authoring/redirect.md` exists, saying the work
described does not want an attractor and where it belongs instead.**

Everything below is the skeleton carved around those two, in the design order
the doctrine prescribes: name the sink, build the gate, build the loop, and only
then add work nodes.

---

## Why the graph is shaped this way

### The brief never touches a shell

`$brief` is expanded into **prompts only**. It is never interpolated into a
`tool_command`.

Engine substitution happens before `/bin/sh` ever sees the string, so a brief in
a `tool_command` is a command-injection hole — a brief containing `$(...)`, a
semicolon, or an unbalanced quote would execute or corrupt the gate. The brief
is untrusted text by construction: it comes from a human, or from
`/attractorify`'s handoff, or from another pipeline.

The consequence is a small design obligation: `triage` restates the brief
verbatim into `.authoring/triage.md`, and every deterministic node downstream —
including the provenance record — reads the **file**. The durable copy is the
LLM's transcription, and that is a deliberate, named trade: fidelity to the
original parameter is traded for never handing user text to a shell.

### One counter spans the whole corrective path

`lint_gate` owns the only iteration counter, and it increments on **entry**,
before it lints anything.

That placement is the point. Four different corrective legs exist —
`lint_fail`, `doctrine_bad`, critique `ITERATE`, and a transient critique FAIL —
and every one of them re-enters through `lint_gate`, either directly or via
`author`. `PIPELINE_DESIGN_PRINCIPLES.md` §3 records why this matters:
*"individually-bounded legs do not compose."* A persistent failure bouncing
between two separately-counted gates never exhausts either one, because neither
counter ever sees the other leg's attempts. Here there is one ledger and every
leg pays into it.

It also means a persistent provider outage **drains** the budget rather than
cycling forever: each transient FAIL at `critique` costs an iteration, and
exhaustion routes to `postmortem` → `escalated`, a loud nonzero, instead of
letting the engine's step cap kill the run with a bare FAIL that bypasses every
salvage path.

### Cheap gate before expensive gate

`attractor lint` and the structural contract both run before an LLM is paid to
critique anything. A graph that does not parse cannot be usefully judged, and
judgment is the expensive tier. This is the composite pattern from
`PIPELINE_DESIGN_PRINCIPLES.md` §2, applied to the authoring problem.

### The critic inherits nothing

`critique` runs at `fidelity="truncate"` — goal and run id only, no summary of
what came before.

This is not a token optimisation. It is the property being purchased. A live run
in this repo's history had a worker hand-author its own `convergence.jsonl` to
satisfy a gate, and only critics *outside its context* caught it. A critic that
inherited the author's reasoning would inherit the author's confidence with it;
this one sees the artifact and the two gate reports, and nothing else.

### Nothing lands in `out/` until every gate is green

The author works in `.authoring/draft/`. `package` publishes to `out/` only
after lint, the contract, and the critique have all passed. Draft and published
are different words on purpose: a reader who finds a file in `out/` can know,
without checking, that three gates said yes to it.

### `must_write=` paths are literals

`must_write.py` reads the raw attribute — it is **not** context-substituted. So
every `must_write` path in this graph is a literal, and the `$pipeline_name`
knob is spent exactly once, at `package`, inside a shell where the
`pn=$pipeline_name; N=${pn:-default}` idiom actually works. This was verified by
reading the engine rather than assumed; a design premised on what the engine
*ought* to do is how contracts come to rest on false premises.

---

## The nodes

### `preflight` — tool

- **Objective.** Make the run's preconditions true, or refuse before an LLM is
  ever paid.
- **Constraints.** Code-tier only, no judgement. Never reads `$brief`.
- **Capabilities.** `command -v`, the filesystem.
- **Required evidence.** `.authoring/` and `out/` exist; `attractor`, `python3`
  and `sha256sum` resolve; `check_authored_pipeline.py` is where
  `$authoring_dir` says; `pipeline_name` is safe as a filename, and the resolved
  name is written to `.authoring/name`.
- **Exit.** `ready` | `blocked` (exit 1, loud).

The name validation is not paranoia: `pipeline_name` becomes a path under
`out/`, and preflight is the last place a bad one can be refused for free.

### `triage` — LLM (`class="maker"`)

- **Objective.** Decide whether the work the brief describes wants an attractor
  at all, and restate the brief durably.
- **Constraints.** Author nothing. Do not self-route — the routing token comes
  from the gate. The verdict vocabulary is fixed and must be the file's **final
  line**.
- **Capabilities.** Reads `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0 and the
  workspace.
- **Required evidence.** `.authoring/triage.md` — the brief restated, the
  three-question test answered with quotes, the named sink, the implied budget,
  and an anchored `VERDICT:` line. Enforced by `must_write=`.
- **Exit.** A last line `triage_gate` can read.

`triage` applies the three-question test to **the work the brief describes**,
not to this authoring run. Its most valuable output is often `REDIRECT`.

### `triage_gate` — tool

- **Objective.** Admit the diagnosis and be the thing that decides the route.
- **Constraints.** Deterministic; last-line anchored, case-insensitive exact
  match. Idiom A — always exit 0, so `tool.last_line` is always fresh.
- **Required evidence.** `.authoring/triage-line.txt`,
  `.authoring/convergence.jsonl`.
- **Exit.** `attractor` | `redirect` | `triage_bad` | `triage_exhausted`.

A nonzero exit here means the gate itself could not run, which routes to the
postmortem path — deliberately distinct from a record it read and rejected.
`triage_bad` is fuse-bounded by `max_frames`: an LLM that cannot produce a
readable verdict line in three tries is not going to on the fourth.

### `redirect_report` — LLM (`class="maker"`)

- **Objective.** Deliver the diagnosis as the work product.
- **Constraints.** Quote the failing three-question answers verbatim from
  `triage.md`; name exactly one better home; author no pipeline.
- **Capabilities.** Reads `.authoring/triage.md` and the doctrine docs.
- **Required evidence.** `.authoring/redirect.md`, enforced by `must_write=`.
- **Exit.** Green, through `finalize`.

This path exits **green**. The diagnosis *is* the deliverable, and the
disposition artifact is what lets an unattended caller tell it apart from
"pipeline authored."

### `author` — LLM (`class="maker"`)

- **Objective.** Write a new, reusable attractor pipeline for the brief, plus
  the companion guide that explains its shape.
- **Constraints.** Control plane only — no `plan -> implement -> test` as graph
  nodes. Gates outside workers. One exit, one cycle, one budget wall, fail routes
  on every worker. **Never weaken a gate to get past a gate.**
- **Capabilities.** Writes files. Reads the authoring contract in
  [`README.md`](README.md), the doctrine docs, the shipped exemplars
  (`examples/patterns/task-runner.dot`, `examples/gates/`), and every gate report
  left by a previous attempt.
- **Required evidence.** `.authoring/draft/pipeline.dot` (enforced by
  `must_write=`) and `.authoring/draft/pipeline.md`.
- **Exit.** Both files exist, pass `lint_gate` and `doctrine_gate`, and survive
  `critique`.

The prompt states the contract and the design order, and points at the checks by
name — A1 through A9 are a **stated** contract, not a guessing game. What the
prompt deliberately does not contain is a procedure for writing a graph. A node
that carries the algorithm cannot absorb a model's bad day.

### `lint_gate` — tool (`class="gate"`)

- **Objective.** Machine-reject a broken draft before anything expensive reads
  it, and own the one counter that spans every corrective leg.
- **Constraints.** ERRORs block, warnings pass and are recorded (no `--strict`);
  `doctrine_gate` owns the design-shape checks. Budget checked on entry, before
  the linter runs.
- **Required evidence.** `.authoring/lint-report.txt`,
  `.authoring/convergence.jsonl`, `.authoring/iter`.
- **Exit.** `lint_pass` | `lint_fail` | `exhausted` | `lint_unavailable`
  (exit 1).

`lint_unavailable` is distinct from `lint_fail` on purpose: the CLI leaving
`PATH` mid-run is an environment failure, not a verdict on the draft, and it
routes to the salvage path rather than to another rewrite.

### `doctrine_gate` — tool (`class="gate"`)

- **Objective.** Enforce the shape checks lint does not own, or owns only as
  advice.
- **Constraints.** Deterministic checks only. It judges structure, never fitness
  for the brief.
- **Capabilities.** `check_authored_pipeline.py` under whatever `python3` is on
  `PATH`.
- **Required evidence.** `.authoring/doctrine-report.txt`, one line per check.
- **Exit.** `doctrine_ok` | `doctrine_bad` | `gate_error` (exit 1).

The load-bearing check is **A4**: delete every evidence gate from the drafted
graph and ask whether its exit is still reachable from `start`. A graph can
carry a cycle *and* a gate and still hang `done` directly off a worker — A1–A3
all pass, and the exit was never gated on anything. A4 is the one that notices.

### `critique` — LLM (`class="gate"`, `fidelity="truncate"`)

- **Objective.** Judge what the machine gates cannot: is this the right pipeline
  *for this brief*, and are its gates real?
- **Constraints.** Inherits nothing. Every finding cites a file and a node or
  edge by name, and is grounded in a quoted gate-report line or a command it
  actually ran. The verdict is the file's final line, anchored.
- **Capabilities.** Reads the draft, both gate reports, the triage record and the
  doctrine docs; may run commands to check claims.
- **Required evidence.** `.authoring/critique.md`, enforced by `must_write=`.
- **Exit.** `VERDICT: SHIP` or `VERDICT: ITERATE` as the last line.

Its six questions are the ones structure cannot answer: does the gate actually
prove the brief's goal (would it be red today?); is any node a domain phase; can
the loop descend, or does it re-flip the same coin; what happens on the bad day;
do the node prompts carry contracts or algorithms; does the companion explain
*why*.

### `verdict_gate` — tool (`class="gate"`, `goal_gate=true`)

- **Objective.** Turn the critique into a route, with no LLM in the router.
- **Constraints.** Last-line anchored, case-insensitive **exact** match
  (`grep -qix`). Idiom B — nonzero on the red path.
- **Required evidence.** `.authoring/verdict-line.txt`,
  `.authoring/convergence.jsonl`.
- **Exit.** `ship` (exit 0) | `iterate` / `noverdict` (exit 1, loud).

Two properties are deliberate here.

**It cannot false-ship on quoted instructions.** A critique that writes "end with
`VERDICT: SHIP` or `VERDICT: ITERATE`" mid-prose contains both keywords; a bare
`grep -qi 'SHIP'` would match the instruction text. `tail -n 1` plus `grep -qix`
is immune — the instruction is never the last line, and `-x` rejects partial
matches. That exact false-SHIP has been observed in this repo before.

**It fails closed.** A missing, empty or unparseable critique is `noverdict`,
which routes to another iteration and never to the exit. Ambiguity resolves
against shipping — the same rule `specs/EXTENSIONS.md` §25 applies to goal-gate
outcomes.

Idiom B is chosen over Idiom A so that `goal_gate=true` has real teeth: a red
verdict is a genuine FAIL the engine's exit-time check can see, rather than a
declaration of intent. The cost is that `iterate` and `noverdict` share one
outgoing edge, because a nonzero exit does not refresh `tool.last_line` and
routing them apart would mean reading a stale label. The distinction is kept
where it belongs instead — in `.authoring/convergence.jsonl`, and on stderr.

### `package` — tool (`class="glue"`)

- **Objective.** Make the accepted draft a named, traceable deliverable.
- **Constraints.** Code-tier only.
- **Required evidence.** `out/<name>.dot`, `out/<name>.md`,
  `.authoring/PROVENANCE.md` (with sha256 digests), `.authoring/published`.
- **Exit.** `published` | `publish_failed` (exit 1).

Provenance is assembled by a shell, from the real artifacts, rather than written
by a model. A provenance record a model wrote is a summary of what it believes
happened; one a shell assembled by concatenating the actual gate reports is a
record of what did.

### `finalize` — tool (`class="glue"`)

- **Objective.** Make "the run ended" and "the run produced a disposition" the
  same event.
- **Required evidence.** `.authoring/disposition` = `authored` | `redirected`.
- **Exit.** `finalized` | `no_disposition` (exit 1, loud).

`done` is reachable **only** through `finalize`. That is the structural half of
the exit guarantee; `goal_gate` on `verdict_gate` is the engine-level half.

### `postmortem` — LLM (`class="gate"`) → `escalated` — tool

- **Objective.** Salvage the analysis from a run that could not converge, then
  fail loudly.
- **Constraints.** `postmortem` must cite the convergence record and name
  *where* the failure was: the brief, the triage, the draft, or the critique.
- **Required evidence.** `.authoring/postmortem/report.md` (`must_write=`);
  `.authoring/postmortem/escalation.md`, which `escalated` writes **either way**
  — including when the postmortem node itself failed.
- **Exit.** `escalated` exits 1 with `max_retries=0` and no fail-route, so the
  engine hard-fails loud: run status `fail`, CLI exit 1.

`escalated` is a second honest terminal, not a second success exit. The engine
admits exactly one `Msquare`; a loud nonzero tool node is how the other one is
expressed.

---

## Honest limits

- **The brief's fidelity depends on `triage`'s transcription.** The durable copy
  of the brief is the one the LLM wrote into `triage.md`, because the raw
  parameter is deliberately kept out of every shell. A transcription that drops
  a constraint drops it for the whole run. The trade is named above; the mitigation
  is that `triage.md` is embedded verbatim in `PROVENANCE.md`, so the drift is
  visible after the fact.
- **The contract checks structure, not fitness.** A1–A9 cannot tell whether the
  authored gate asserts the *right* thing. `critique` can, and does — but it is
  an LLM, and the budget wall exists because it can also be wrong in either
  direction.
- **A5's vocabulary is a convention, not a semantic analysis.** It recognises a
  budget wall by the identifiers the command and edges use. An author who counts
  iterations under an unrecognised name will be told so by name, with the accepted
  vocabulary printed in the failure detail.
- **This pipeline does not run what it authors.** It proves the artifact is
  well-shaped and well-judged; proving it *works* on real input is the authored
  pipeline's own first run. `examples/authoring/README.md` says which evidence
  belongs to which.
