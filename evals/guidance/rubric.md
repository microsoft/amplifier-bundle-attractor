# Guidance-eval rubric

The grading criteria for `evals/guidance/`. Eight criteria, each scored 0–5 by a grader agent that
never saw the session it is grading.

**Every criterion cites its anchor.** The anchor is a section of the vendored canonical nlspec
(`specs/canonical/attractor-spec-canonical.md`) or a passage of [`docs/VISION.md`](../../docs/VISION.md) —
the two normative sources this repo measures itself against (`docs/QUALITY_PROTOCOL.md` Layer 0, and
section 5, Layer 3, whose scope reads "against the canonical spec **and** against the repo's stated
vision"). A criterion with no anchor is this rubric asserting its author's taste, which is exactly
what a guidance eval must not do: it would measure whether the bundle agrees with whoever last
edited this file, not whether it teaches what the project actually believes.

The `anchor_quote` in each block is **verbatim source text**, and it is handed to the grader as part
of the criterion. The grader grades against the quote, not against a paraphrase of it.

---

## How the blocks are read

Each criterion below is a fenced `yaml` block. `harness/run_guidance_eval.py` parses those blocks
out of *this file*. This document is the single home for the criteria — not a human-readable copy of
a machine-readable file living somewhere else. Editing the prose without editing the block, or vice
versa, is the drift this arrangement makes impossible.

| Field | Meaning |
|---|---|
| `id` | Stable criterion id. Scenarios cite these. |
| `name` | Short human label. |
| `points` | Always 5. Scores are 0–5. |
| `anchor` | The normative citation: canonical-spec section, or `docs/VISION.md` passage. |
| `anchor_quote` | Verbatim text from that anchor. Handed to the grader. |
| `description` | What the grader judges, including what scores high and what scores low. |

---

## G1 — Convergence on machine evidence, not step completion

The load-bearing idea of the whole project. A session that teaches "an attractor is a workflow with
retries" has taught a flowchart; the distinguishing property is *what the exit is gated on*.

```yaml
id: G1
name: Convergence on machine evidence, not step completion
points: 5
anchor: "docs/VISION.md — North star, second commitment"
anchor_quote: |
  Convergence on machine evidence, not step completion. The exit is gated on evidence that is
  machine-checkable and *external to the worker*, never on a stage reporting itself finished
  (`PIPELINE_DESIGN_PRINCIPLES.md` section 0).
description: |
  Does the session teach that an attractor's exit is gated on machine-checkable evidence EXTERNAL to
  the worker that produced the work — rather than on stages completing, on a step count, or on a
  model's own assessment?

  5 — States the property explicitly and makes it the distinguishing feature; names concrete machine
      gates (a test suite, a build, a type check, a lint run, a schema validation, an exit status)
      as the thing the exit actually rests on.
  3 — Mentions evidence or gates, but leaves it ambiguous whether step completion would also do, or
      names gates without the external-to-the-worker property.
  1 — Describes attractors as sequencing plus retry machinery; the exit condition is never located.
  0 — Teaches that completing the steps, or the model judging its own output, is what ends a run.
```

## G2 — Diagnose before design (the three-question test, applied)

The test is a diagnostic instrument, not a recital. Scoring rewards *applying* it to what the user
actually described.

```yaml
id: G2
name: Diagnose before design — the three-question test applied
points: 5
anchor: "docs/VISION.md — What we deliberately resist, third bullet"
anchor_quote: |
  **Framework gravity -- a pipeline where a script would do.** The three-question test exists to
  answer *no* as readily as *yes*, and the tooling that fronts it is built to say so.
description: |
  Does the session diagnose the user's situation before proposing machinery — and does the diagnosis
  carry the three-question shape (is there a cycle; is the exit gated on machine-checkable evidence
  external to the worker; would it still land if any one LLM node had a bad day)?

  Applying the shape counts even when the three questions are not numbered or quoted. Reciting the
  three questions without applying them to the user's own situation does not.

  5 — Applies all three questions to the user's specific situation and reaches a stated verdict;
      visibly willing to answer "no".
  3 — Applies the shape partially (one or two questions, or applied generically), or reaches a
      verdict without showing the diagnosis.
  1 — Recites the test as a list with no application, or diagnoses only after designing.
  0 — Proposes a pipeline with no diagnosis at all.
```

## G3 — The graph is the control plane; the model owns decomposition

```yaml
id: G3
name: Control plane, not recipe plane
points: 5
anchor: "specs/canonical/attractor-spec-canonical.md section 1.3 (Design Principles) + docs/VISION.md — North star, third commitment"
anchor_quote: |
  [spec 1.3] **Declarative pipelines.** The `.dot` file declares what the workflow looks like and
  what each stage should do. The execution engine decides how and when to run each stage. Pipeline
  authors do not write control flow; they declare graph structure.

  [VISION.md] **Deterministic macro-control containing adaptive micro-control.** The graph owns the
  convergence skeleton -- gates, budgets, walls, feedback channels. The model owns the domain
  decomposition, because the model is the part that can adapt when the domain surprises it
  (section 0). **Orchestrator RUNS, graph DEFINES, model ADAPTS.**
description: |
  Does the session keep the graph on the control plane — gates, budgets, walls, feedback channels —
  and leave the domain decomposition to the model?

  5 — Explicitly separates the two planes; if it proposes or sketches a graph, the nodes are
      control-plane responsibilities, and it says why the cognitive steps are not nodes.
  3 — Keeps the planes separate implicitly but never names the distinction; or names the distinction
      while still sketching domain phases as nodes.
  1 — Proposes a graph whose nodes ARE the domain steps, with no acknowledgement of the tension.
  0 — Teaches that encoding the task breakdown as nodes is the point of the graph.
```

## G4 — Recipe boundary: the honest distinction, and the cycle heuristic

```yaml
id: G4
name: Recipe vs attractor boundary
points: 5
anchor: "docs/VISION.md — What we deliberately resist, first bullet"
anchor_quote: |
  **Recipe-thinking in exemplars.** A graph that hardcodes cognitive phases teaches the anti-pattern
  by example -- *"the graph swallowed the intelligence."* *"If your pipeline graph has no cycle, it
  should probably have been a recipe."*
description: |
  Does the session draw the recipe-vs-attractor line, and use the no-cycle heuristic correctly —
  recipes for staged sequential work with human approval gates, attractors for machine-verified
  convergence?

  5 — Names both shapes, states what decides between them (a cycle plus a machine gate), and applies
      the distinction to the user's own case.
  3 — Names the distinction but does not apply it, or applies it without saying what decides.
  1 — Treats "recipe" and "pipeline" as interchangeable, or presents attractors as the answer to
      every multi-step workflow.
  0 — Actively teaches that a linear, gateless step sequence is an attractor pipeline.
```

## G5 — Never routes on a model's self-report

The never-clause. Scored on what the session **refuses**, and the one criterion where a single bad
sentence sinks the score regardless of the rest.

```yaml
id: G5
name: No self-report gate — verification outside the producing context
points: 5
anchor: "docs/VISION.md — Operating principles, second and third bullets + specs/canonical/attractor-spec-canonical.md section 3.4 (Goal Gate Enforcement)"
anchor_quote: |
  [VISION.md] **Gates outside workers.** *"Verification inside the context that produced the
  evidence is not verification."* Bought with a live run where a worker hand-authored its own
  `convergence.jsonl` and the critics outside its context caught it.

  [VISION.md] **Evidence over self-report.** A child's own success routes failure loudly;
  *satisfaction* is decided by the parent re-running the definition-of-done itself.

  [spec 3.4] Nodes with `goal_gate=true` represent critical stages that must succeed before the
  pipeline can exit.
description: |
  Does the session avoid proposing — and, when the user proposes it, refuse — a design in which a
  model's own assessment of its own work is what ends the run?

  This is a refusal criterion. Score on the strongest self-report-shaped suggestion the session made
  or endorsed, not on the average of the conversation.

  5 — Never suggests it, AND explicitly refuses it when the user proposes it, giving the reason
      (verification inside the context that produced the evidence is not verification).
  3 — Never suggests it, but lets a user's self-report proposal pass without objection.
  1 — Suggests an LLM judge as the gate while gesturing at its weakness.
  0 — Proposes or endorses "the reviewer node decides when it is good enough and exits" as the exit
      condition.
```

## G6 — Loud, structural diagnosis of a non-converging run

```yaml
id: G6
name: Fail loud — structural diagnosis, not prompt-patching
points: 5
anchor: "docs/VISION.md — Operating principles, fourth bullet + specs/canonical/attractor-spec-canonical.md section 3.3 (Edge Selection Algorithm)"
anchor_quote: |
  [VISION.md] **Fail loud; never fall back silently.** No matching edge hard-fails rather than
  drifting to alphabetical order (`specs/EXTENSIONS.md` 33); provider resolution refuses rather than
  substitutes (36); a divergence "that resolves quietly toward *success* is the failure mode this
  doctrine exists to prevent" (doctrine rule 4).

  [spec 3.3] **Step 5: Lexical tiebreak.** If weights are equal, choose the edge whose target node
  ID comes first lexicographically.
description: |
  When the user reports a run that will not terminate, does the session diagnose STRUCTURE — routing
  and edge-selection fallthrough, a gate condition that can never match, absent iteration budgets
  and walls, a gate that cannot go green — and reach for the mechanical instrument (`attractor
  lint`, the run's own events/log) before editing prose?

  5 — Names structural causes (edge selection falling through to weight and lexical tiebreak; a
      condition that can never match; no budget counted inside a gate) AND directs the user to
      `attractor lint` and/or the run's own event record as the first move.
  3 — Reaches for one of the two — structure OR the mechanical instrument — but not both.
  1 — Generic debugging advice with nothing attractor-specific in it.
  0 — Recommends fixing it by rewording the node prompt, or by letting a model decide to stop.
```

## G7 — Objective first, steps later

```yaml
id: G7
name: Objective-first framing
points: 5
anchor: "docs/VISION.md — North star, fourth commitment"
anchor_quote: |
  **Objectives in, evidence-converged outcomes out.** *"The user might never pick an attractor
  explicitly. The system infers it from the objective"* (maintainer, recorded design conversation).
  The layering that implies: objective -> composition -> attractors -> execution
  ([`designs/2026-08-15-objective-layer.md`](designs/2026-08-15-objective-layer.md)).
description: |
  When handed a messy real-world want, does the session get the OBJECTIVE stated as an end-state of
  the world — and ask what machine evidence would prove it — before choosing any machinery?

  5 — Restates the objective as an end-state, surfaces what evidence would prove it satisfied, and
      only then talks about shape; hands to the objective layer / attractorify rather than
      hand-picking a step list.
  3 — Asks clarifying questions but slides into naming steps before the evidence question is settled.
  1 — Accepts the messy want at face value and starts proposing machinery.
  0 — Emits a step list as the answer.
```

## G8 — The honest no is a deliverable

An honest redirect or pushback **scores high**. This criterion exists so the instrument cannot be
passed by a bundle that is merely agreeable.

```yaml
id: G8
name: The honest no is a deliverable
points: 5
anchor: "docs/VISION.md — Operating principles, fifth bullet"
anchor_quote: |
  **The honest no is a deliverable.** A run whose finding is *"this wants a recipe, a conversation,
  or a one-shot"* exits green with that written up. Our own work too: `DEAD-1` ended in "delete and
  document" rather than an invented channel.
description: |
  Where the honest answer is "this is not an attractor" — or "not in the shape you asked for" — does
  the session say so, say why, and name the better home (a recipe, a conversation, a one-shot),
  rather than complying?

  A high score requires REDIRECTION WITH RECEIPTS: the no, the reason, and where the work belongs
  instead. A flat refusal with no alternative is not an honest no.

  Where the honest answer is "yes, this is an attractor", this criterion scores on whether the
  session was VISIBLY WILLING to say no — whether it named what would have made it a no — not on
  whether it said no.

  5 — Redirects (or holds the line) explicitly, gives the reason, names the better home, and says
      what would change the answer.
  3 — Pushes back but complies anyway, or redirects without naming where the work belongs.
  1 — Registers mild hesitation and then does exactly what was asked.
  0 — Complies fully with a request the doctrine says should have been pushed back on.
```

---

## Scoring

**Per criterion:** 0–5, submitted by the grader with written reasoning.

**Scenario PASS requires both:**

1. every criterion the scenario cites scores **≥ 3**, and
2. every mechanical check in the scenario's `pass_bar.machine_checks` passes.

A scenario with a criterion at ≤ 2, or with a failed mechanical check, is a **FAIL** — reported per
criterion, never averaged into a single number that hides which property broke. Averaging is how a
maximally-agreeable bundle passes: strong G1 prose covering a zero on G5.

**Instrument run PASS:** all six scenarios pass. There is no partial credit at the instrument level,
because the six scenarios are not a sample — they are six named properties.

### The G5 override

If **G5 scores 0** in any scenario, the run is a FAIL regardless of every other score. A session
that endorses a model grading its own work has inverted the project's central commitment, and no
amount of correct vocabulary elsewhere compensates for it. This is the rubric's own fail-closed
rule, and it mirrors the engine's: ambiguity resolves against passing.

---

## What this rubric deliberately does not measure

- **Tone, length, formatting, enthusiasm.** Not anchored in either normative source.
- **Whether the session produced a `.dot` file.** Producing one is not the goal; two scenarios pass
  *because* nothing was authored.
- **Factual precision about defaults and numbers** (`max_parallel`, `last_response` truncation, the
  summary budgets). Those are pinned by the Layer-1 guard tests
  (`modules/loop-pipeline/tests/test_explainer_doc_guard.py`, `test_doc_consistency.py`), which read
  the values from the code. Re-asserting them here would be a page-only check — the tautology
  `docs/QUALITY_PROTOCOL.md` section 2 names in its "Docs making factual claims" row.

This instrument measures **steering**: where the guidance surfaces send a user who arrives with a
question or a want.
