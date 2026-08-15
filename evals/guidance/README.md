# The guidance eval

A standing, re-runnable instrument that answers one question:

> **When a real user arrives at this bundle with a question or a want, where does the guidance
> send them?**

Six scenarios drive real sessions against the bundle's guidance surfaces — installed in a fresh
container the way a user installs it — and a grader that never saw those sessions scores them
against criteria anchored in the canonical nlspec and [`docs/VISION.md`](../../docs/VISION.md).

This is the instrument `docs/QUALITY_PROTOCOL.md` section 2 names in its **Guidance surfaces** row.

---

## Why it exists

The quality protocol requires evidence for every class of change. For `agents/`, `skills/`,
`context/`, and the teaching content in `README.md` and `docs/`, the required evidence is
"guidance-eval evidence **once the eval instrument ships**" — and until it does, the floor is a
fresh-session walk-through pasted into the PR.

A walk-through is a real check and it caught real problems, but it has three properties that make
it unsuitable as the standing bar:

- **It is authored by the person whose change it validates.** A walk-through written by the
  author, in the author's frame, is verification inside the context that produced the artifact —
  the property this repo refuses everywhere else (`docs/VISION.md`, "Gates outside workers").
- **It runs against the working tree**, so it proves the guidance steers *for someone who already
  has the repo checked out*, which is not who the guidance is for.
- **It is not comparable.** Two walk-throughs of two changes measure different things, so there is
  no way to tell whether a guidance change made anything better or worse.

This instrument fixes all three: the sessions are driven by a user simulator with a persona and no
knowledge of the doctrine, the bundle is installed over the real `bundle add` path from a pinned
ref, and every run scores the same criteria against the same six scenarios.

## What it measures — and what it does not

**It measures steering.** Where the guidance sends a user. Whether the exit condition it teaches
is machine evidence or step completion. Whether it diagnoses before designing. Whether it says no
when no is the honest answer.

**It does not measure** tone, length, whether a `.dot` file was produced (in two scenarios the
correct answer is that nothing gets authored), or factual precision about defaults and numbers.
Those numbers are pinned by the Layer-1 guard tests, which read them from the code —
re-asserting them here would be the page-only tautology the protocol's "Docs making factual
claims" row warns about.

## The six scenarios

Three classes, two each. Six, not a matrix: each one is a **named property**, and adding a
seventh should require naming a property the other six do not cover.

| # | Scenario | The property under test |
|---|---|---|
| **a** | [`qa-01-what-is-an-attractor`](scenarios/qa-01-what-is-an-attractor.yaml) | A newcomer asks what an attractor is and when to use one instead of a recipe. **Passes if** the session teaches convergence on machine evidence external to the worker — not step completion, not "a flowchart with retries" — applies the three-question shape, points at a shipped teaching surface, and holds the line when the user offers the retry-loop analogy. |
| **b** | [`qa-02-never-converges`](scenarios/qa-02-never-converges.yaml) | "My pipeline runs forever / never converges — help." **Passes if** the diagnosis is structural (budgets counted inside gates, a condition that can never match, edge selection falling through to the lexical tiebreak), it reaches for `attractor lint` and the run's own event record, and it **refuses** the user's proposal to let the review node decide it is done. |
| **c** | [`work-01-stale-release-notes`](scenarios/work-01-stale-release-notes.yaml) | A messy real objective: "our release notes are always stale… build me something." **Passes if** the session works the objective layer — restates the objective as an end-state, presses for what machine evidence would prove it, routes to `/attractorify` or the objective runner — rather than hand-picking a step list. |
| **d** | [`work-02-twelve-step-pipeline`](scenarios/work-02-twelve-step-pipeline.yaml) | "Just write me a 12-step pipeline that does X, A to Z" — a recipe-shaped ask, fully specified. **Passes if** the session pushes back per doctrine: names the ask as recipe-shaped, offers the recipe-vs-attractor distinction with the reason, and does **not** dutifully author a gateless twelve-node chain. The honest no scores high. |
| **e** | [`exemplar-01-sloppy-routable`](scenarios/exemplar-01-sloppy-routable.yaml) | A sloppily-phrased but genuinely machine-checkable objective, run through the shipped `examples/objective/objective-runner.dot`. **Passes if** the intake routes it to a lane anyway: `triage_gate` admits the record, the shape is not `redirect`, `evidence_command` is not `NONE`, and the disposition is not `redirected`. |
| **f** | [`exemplar-02-honest-redirect`](scenarios/exemplar-02-honest-redirect.yaml) | An objective with no machine evidence available at all, through the same runner. **Passes if** it produces the honest no with receipts: `disposition == redirected`, `shape == redirect`, `evidence_command == NONE`, and a `redirect.md` naming the better home. A `satisfied` here is the most interesting possible failure — it means a hollow definition of done was invented and then passed. |

Scenarios (e) and (f) are the same fixture workspace with opposite correct answers. That pairing
is deliberate: an intake that always routes and an intake that always redirects each pass exactly
one of them, and the instrument reports which.

## When the protocol requires running it

Per `docs/QUALITY_PROTOCOL.md` section 2, **Guidance surfaces** row — any change to:

- `agents/` — including `agents/attractor-expert.md` and its context files
- `skills/` — including `skills/attractorify/SKILL.md`
- `context/` — `pipeline-awareness.md`, `dot-reference.md`, `engine-semantics.md`, and siblings
- teaching content in `README.md` and `docs/` — including `docs/attractor-explained.html`,
  `docs/PIPELINE_DESIGN_PRINCIPLES.md`, and `docs/GETTING-STARTED.md`

Run the scenarios whose `surfaces_under_test:` name the file you touched, and paste the results
table plus the decisive transcript quotes into the PR. A full six-scenario run is warranted when
the change is broad — a bundle recomposition, a doctrine amendment, a new guidance surface — and
whenever the Layer-3 holistic review fires (section 6).

Two changes also warrant a run even though they are not guidance edits: **any change to
`examples/objective/`**, which scenarios (e) and (f) exercise directly, and **any amendment to
`docs/VISION.md`**, because the rubric's anchors live there and an amendment may have moved what
the instrument is measuring.

## Running it

```bash
cd evals/guidance/harness
./run.sh --list                  # what would run, and against which criteria
./run.sh --smoke                 # one scenario, whole plumbing, ~15 min
./run.sh --scenarios qa-02-never-converges
./run.sh                         # all six
```

Prerequisites, mechanics, DTU details, and failure handling: [`harness/README.md`](harness/README.md).

## Reading a result

Results land **outside the repo**, under
`<workspace>/.amplifier/evaluation/guidance-pilot/<UTC>/`. The harness refuses to start if the
results path resolves inside the checkout — transcripts and prompts are not source.

`results.md` gives the table. Then read, in this order:

1. **The failed checks and low criteria, individually.** Nothing is averaged. A scenario fails if
   *any* cited criterion scores below 3 or *any* mechanical check fails, and the report names
   which — because "4.1 out of 5" hides the one property that broke.
2. **The grader's reasoning per criterion.** Each score comes with the sentence it was scored on.
   A score whose reasoning does not quote the transcript is a score to distrust.
3. **The transcript itself**, at `<scenario>/graded/transcript.md`. This is the artifact. The
   scores are a reading of it, and a reading can be wrong.
4. **`ai_user.json`** — the simulated user's own verdict, and its verbatim quotes of the decisive
   moments. When the grader and the user disagree, that disagreement is the finding.

**A suspicious pass is a bug in the eval until proven otherwise.** The failure this instrument is
most exposed to is grading an environment where the bundle was never active — which is why
activation is asserted twice and the readiness log is kept per trial.

### The G5 override

If **G5** — "never routes on a model's self-report" — scores 0 in any scenario, the run fails
regardless of every other score. A session that endorses a model grading its own work has
inverted the project's central commitment. The rubric's own rule is fail-closed, for the same
reason the engine's is.

## Hold-out and refresh discipline

**This instrument can be overfit, and if it is, it will keep saying yes while the guidance gets
worse.** The failure mode is specific: someone reads a low score, edits the guidance surface to
contain the phrases the scenario checks for, and the number goes up while a real user's experience
does not change at all. That is not a hypothetical — it is the natural gradient of having a score.

Four rules hold the line.

**1. Fix the guidance, never the scenario.** A failing scenario is a finding about the bundle
until an independent reading says otherwise. If you conclude the scenario is wrong, say so in the
PR, in those words, with the reason — and expect a reviewer to disagree. Editing a scenario in the
same change that made it fail is the single most suspicious diff this directory can contain.

**2. The rubric is frozen within an iteration.** Criteria may be added or amended between runs,
never mid-cycle to rescue a result. Any rubric change re-baselines: prior results are no longer
comparable, and `results.json` records the criteria and thresholds the run was actually judged
against so that stays visible.

**3. Never publish the scenario text into the guidance surfaces.** Do not quote a persona, an
opening ask, or a `machine_checks` phrase into `agents/`, `skills/`, `context/`, or the docs.
Doing so trains the surfaces on the test. The mechanical checks are deliberately narrow and
literal for the same reason: they catch blatant regressions, and the rubric criteria — which are
judged, not matched — carry the real weight.

**4. Refresh on a schedule, and hold one back.** When the instrument has run several times
without finding anything, that is a finding about the instrument, not a clean bill of health
(`docs/QUALITY_PROTOCOL.md` section 7's retirement review: *"What has it caught since the last
review? Nothing is a finding, not a pass."*). At that point **rewrite one scenario's prose from
scratch** — same property, same criteria, entirely new persona, phrasing, and surface details —
and re-run. A bundle that passes the old wording and fails the new one was fitted to the wording.

Rotate which scenario gets rewritten, and record the rewrite in the PR that makes it. The property
list is the stable thing here; the words are not, and must not be.

## Status — what has been proven, and what has not

Stated plainly rather than implied, per `docs/QUALITY_PROTOCOL.md`'s scope note: where a piece of
machinery has not been exercised yet, say so in those words.

**Proven end-to-end.** Scenario (a) has run the whole path for real — pinned mirror push, DTU
launch, `bundle add` install from the mirror, readiness gates, an AI-user-driven multi-turn
session against the installed bundle, deterministic transcript recovery, mechanical checks, blind
grading against the anchored criteria, and results written outside the repo. It passed
(`G1=3 G2=3 G4=5 G5=3 G8=5`, both mechanical checks green).

**Shares that proven path.** Scenarios (b), (c) and (d) are the same session mode with different
personas and pass bars: every stage they use is the stage (a) exercised. They have not themselves
been run.

**Not yet exercised: the exemplar path.** Scenarios (e) and (f) drive
`examples/objective/objective-runner.dot` inside the DTU, which stage (a) does not touch. What
*is* proven of it: the `objective-runner-lints` readiness gate runs `attractor lint` against the
shipped runner in the DTU on every trial, and passes — so the CLI, the checkout, and the graph's
parse are real. The run itself, the disposition artifacts, and the file/JSON mechanical-check
kinds have not been exercised against a live pipeline. Treat the first (e)/(f) run as a smoke
run, and read it by hand.

## Layout

```
evals/guidance/
├── README.md              # this page — what the instrument is and when to run it
├── rubric.md              # the 8 criteria, each citing a canonical-spec § or a VISION passage
├── scenarios/             # 6 scenarios: persona, opening ask, follow-up behavior, pass bar
└── harness/               # the runnable instrument (see harness/README.md)
```

`rubric.md` is both the human-readable argument and the machine-readable source: the harness parses
its fenced `yaml` blocks directly, so the prose defending a criterion and the text the grader
receives cannot drift apart.
