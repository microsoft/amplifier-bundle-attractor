# The guidance eval

A standing, re-runnable instrument that answers one question:

> **When a real user arrives at this bundle with a question or a want, where does the guidance
> send them?**

Eight scenarios drive real sessions against the bundle's guidance surfaces — installed in a fresh
container the way a user installs it — and a grader that never saw those sessions scores them
against criteria anchored in the canonical nlspec and [`docs/VISION.md`](../../docs/VISION.md).

This is the instrument `docs/QUALITY_PROTOCOL.md` section 2 names in its **Guidance surfaces** row.

---

## Why it exists

The quality protocol requires evidence for every class of change. For `agents/`, `skills/`,
`context/`, and the teaching content in `README.md` and `docs/`, the required evidence was, until
this instrument shipped, a **fresh-session walk-through** pasted into the PR — an interim floor
held open in section 2's own words until guidance-eval evidence could replace it.

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
ref, and every run scores the same criteria against the same eight scenarios.

## What it measures — and what it does not

**It measures steering.** Where the guidance sends a user. Whether the exit condition it teaches
is machine evidence or step completion. Whether it diagnoses before designing. Whether it says no
when no is the honest answer.

**It does not measure** tone, length, whether a `.dot` file was produced (in two scenarios the
correct answer is that nothing gets authored), or factual precision about defaults and numbers.
Those numbers are pinned by the Layer-1 guard tests, which read them from the code —
re-asserting them here would be the page-only tautology the protocol's "Docs making factual
claims" row warns about.

## The eight scenarios

Three classes. Eight, not a matrix: each one is a **named property**, and adding a ninth should
require naming a property the other eight do not cover.

| # | Scenario | The property under test |
|---|---|---|
| **a** | [`qa-01-what-is-an-attractor`](scenarios/qa-01-what-is-an-attractor.yaml) | A newcomer asks what an attractor is and when to use one instead of a recipe. **Passes if** the session teaches convergence on machine evidence external to the worker — not step completion, not "a flowchart with retries" — applies the three-question shape, points at a shipped teaching surface, and holds the line when the user offers the retry-loop analogy. |
| **b** | [`qa-02-never-converges`](scenarios/qa-02-never-converges.yaml) | "My pipeline runs forever / never converges — help." **Passes if** the diagnosis is structural (budgets counted inside gates, a condition that can never match, edge selection falling through to the lexical tiebreak), it reaches for `attractor lint` and the run's own event record, and it **refuses** the user's proposal to let the review node decide it is done. |
| **c** | [`work-01-stale-release-notes`](scenarios/work-01-stale-release-notes.yaml) | A messy real objective: "our release notes are always stale… build me something." **Passes if** the session works the objective layer — restates the objective as an end-state, presses for what machine evidence would prove it, routes to `/attractorify` or the objective runner — rather than hand-picking a step list. |
| **d** | [`work-02-twelve-step-pipeline`](scenarios/work-02-twelve-step-pipeline.yaml) | "Just write me a 12-step pipeline that does X, A to Z" — a recipe-shaped ask, fully specified. **Passes if** the session pushes back per doctrine: names the ask as recipe-shaped, offers the recipe-vs-attractor distinction with the reason, and does **not** dutifully author a gateless twelve-node chain. The honest no scores high. |
| **e** | [`exemplar-01-sloppy-routable`](scenarios/exemplar-01-sloppy-routable.yaml) | A sloppily-phrased but genuinely machine-checkable objective, run through the shipped `examples/objective/objective-runner.dot`. **Passes if** the intake routes it to a lane anyway: `triage_gate` admits the record, the shape is not `redirect`, `evidence_command` is not `NONE`, and the disposition is not `redirected`. |
| **f** | [`exemplar-02-honest-redirect`](scenarios/exemplar-02-honest-redirect.yaml) | An objective with no machine evidence available at all, through the same runner. **Passes if** it produces the honest no with receipts: `disposition == redirected`, `shape == redirect`, `evidence_command == NONE`, and a `redirect.md` naming the better home. A `satisfied` here is the most interesting possible failure — it means a hollow definition of done was invented and then passed. |
| **g** | [`work-03-docs-drifted-from-spec`](scenarios/work-03-docs-drifted-from-spec.yaml) | "Our docs quietly stopped being true — I want to find the rest of them." The user is describing, in their own words, the job [`examples/drift-review/`](../../examples/drift-review/README.md) was built to do. **Passes if** the session reaches that shipped executor rather than improvising a bespoke sweep, locates the exit on the gate that re-opens every cited `file:line` outside the reviewers' context, and **refuses** the user's request to file the findings unread — naming the human verification step as where that judgment lives. |
| **h** | [`work-04-reusable-pipeline-authoring`](scenarios/work-04-reusable-pipeline-authoring.yaml) | "I've hand-written three of these and they're all subtly broken — write me a real one." **Passes if** the session routes the ask to the shipped authoring path — `/attractorify` to diagnose and design with the user present, [`examples/authoring/`](../../examples/authoring/README.md) to converge the artifact under executed gates — instead of typing a fourth graph into the chat, and **refuses** to vouch for its own draft when asked to, naming what to run instead. |

Scenarios (e) and (f) are the same fixture workspace with opposite correct answers. That pairing
is deliberate: an intake that always routes and an intake that always redirects each pass exactly
one of them, and the instrument reports which.

Scenarios (g) and (h) are the newest pair, and they close a hole in the instrument rather than
adding a variation to it: this repo shipped two guidance surfaces — the drift-review executor and
the pipeline-authoring exemplar with its `/attractorify` handoff — that **no scenario exercised**.
Both are things a user is meant to be *steered to*, and until (g) and (h) nothing measured whether
that steering happens. Their property is the one the other six do not carry: *does the guidance
reach a surface the bundle already ships, and use it for what it is?* Both are deliberately built
so that reciting the surface's name is not enough — each one ends on a request the surface's own
doctrine says must be refused.

## When the protocol requires running it

Per `docs/QUALITY_PROTOCOL.md` section 2, **Guidance surfaces** row — any change to:

- `agents/` — including `agents/attractor-expert.md` and its context files
- `skills/` — including `skills/attractorify/SKILL.md`
- `context/` — `pipeline-awareness.md`, `dot-reference.md`, `engine-semantics.md`, and siblings
- teaching content in `README.md` and `docs/` — including `docs/attractor-explained.html`,
  `docs/PIPELINE_DESIGN_PRINCIPLES.md`, and `docs/GETTING-STARTED.md`

Run the scenarios whose `surfaces_under_test:` name the file you touched, and paste the results
table plus the decisive transcript quotes into the PR. A full eight-scenario run is warranted when
the change is broad — a bundle recomposition, a doctrine amendment, a new guidance surface — and
whenever the Layer-3 holistic review fires (section 6).

Three changes also warrant a run even though they are not guidance edits: **any change to
`examples/objective/`**, which scenarios (e) and (f) exercise directly; **any change to
`examples/drift-review/` or `examples/authoring/`**, which (g) and (h) steer toward; and **any
amendment to `docs/VISION.md`**, because the rubric's anchors live there and an amendment may have
moved what the instrument is measuring.

## Running it

```bash
cd evals/guidance/harness
./run.sh --list                  # what would run, and against which criteria
./run.sh --smoke                 # one scenario, whole plumbing, ~15 min
./run.sh --scenarios qa-02-never-converges
./run.sh                         # all eight
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

## Status — the baselines

### The 2026-08-15 baseline (a)–(f)

Stated plainly rather than implied, per `docs/QUALITY_PROTOCOL.md`'s scope note.

**All six scenarios have been exercised live.** On 2026-08-15, against `main` @ `ed5bdef`, every
scenario ran the whole path for real — pinned mirror push, DTU launch, `bundle add` install from
the mirror, readiness gates, AI-user-driven multi-turn sessions and live
`examples/objective/objective-runner.dot` runs against the installed bundle, deterministic
transcript recovery, mechanical checks, blind grading against the anchored criteria, and results
written outside the repo. No stage of the harness remains unexercised.

**Three of six passed.** The instrument's first product is a finding, not a clean bill of health.

**Disclosed: two mechanical-check commands were repaired mid-baseline.** Rule 1 above calls an
undisclosed scenario edit "the single most suspicious diff this directory can contain", so this one
is stated here rather than left to be found. `MC-E2` and `MC-F6` — the two exemplar scenarios' re-run
of the shipped admission gate — invoked `examples/objective/validate_triage.py` with a bare
positional path, but that script requires `--triage` and `--schema`; the check died in argparse with
exit 2 and could not have exited 0 for **any** bundle, good or bad. The repair changed how the gate
is called, not what it demands — harness plumbing, not tuning — and the bar moved up rather than
down, since the corrected check also fails on a `triage_bad` or `triage_exhausted` token where the
original could only ever error out. The rubric, the personas, the opening asks and every other
scenario input were byte-identical either side of the repair, and the YAMLs committed here are the
fixed version. **No verdict in the table turns on it:** on the failing first pass both exemplars'
grader scores were already at the passing values recorded below — the broken invocation was the only
thing failing, and it was failing on its own usage error.

#### The table

| # | Scenario | Verdict | Finding |
|---|---|---|---|
| **a** | `qa-01-what-is-an-attractor` | **PASS** | Taught machine gates throughout and delivered the honest "you don't need an attractor" (G8=5); softened to "AI adapts vs dumb retry" under the flowchart-with-retries push rather than holding the external-to-the-worker line (G1=3). |
| **b** | `qa-02-never-converges` | **FAIL** | Asked whether the review node could decide it was done, `attractor-expert` answered "**yes, the review node decides**" and authored a self-report exit — the central commitment inverted (G5=0, which is the override; G1=0, G8=0). Never reached `attractor lint` or the event record. |
| **c** | `work-01-stale-release-notes` | **FAIL** | The objective layer is unreachable by conversation: foundation's `/brainstorm` mode-routing captured the ask, and `attractorify`, the objective runner and `attractor-expert` were never named (MC-C1). Nobody asked what would prove it done (MC-C2). |
| **d** | `work-02-twelve-step-pipeline` | **FAIL** | Authored the gateless twelve-node chain on request with zero pushback; the word "recipe" never appears (MC-D1). `attractor lint` on the file it just authored says *"consider whether this pipeline should be a recipe instead"* — the doctrine the session skipped is already in the linter. |
| **e** | `exemplar-01-sloppy-routable` | **PASS** *(flagged)* | The sloppy-but-checkable objective was restated as an end-state and routed to a lane with `pytest` as the gate (G1=G2=G7=5, every mechanical check green). Flagged: the runner exited 1 with `disposition=escalated` — a known objective-runner defect, not a guidance failure. |
| **f** | `exemplar-02-honest-redirect` | **PASS** | The strongest artifact in the baseline: a complete honest redirect with receipts — the no, the reason, the better home, and what would change the answer (G1=G2=G4=G8=5, all six mechanical checks green). |

**One cross-cutting finding, visible only across scenarios.** Both authoring surfaces emitted a DOT
dialect the shipped engine does not use — `agent=`, `instruction=`, `attractor_handler=` — which no
single scenario's pass bar was looking for. That is the kind of finding a standing instrument buys
and a per-PR walk-through does not.

Every finding above is tracked in the issues filed from the 2026-08-15 baseline. Per the hold-out
discipline above, none of them is a reason to edit a scenario: they are findings about the bundle
until an independent reading says otherwise.

**Where the evidence lives.** Outside the repo, by design — under
`<workspace>/.amplifier/evaluation/guidance-pilot/<UTC>/`, one directory per run, each carrying
`results.md`, `results.json`, per-scenario transcripts, `ai_user.json`, and the readiness logs. The
harness refuses to start if that path resolves inside the checkout. Nothing from a run is committed
here, and this table is the repo's record of the baseline rather than a copy of its artifacts.

**Read this table as a reference point, not a scoreboard.** It is the run every later run is
compared against, and it was taken before any of the findings were acted on. A future run that
scores better because the guidance improved is the intended outcome; one that scores better because
a scenario was reworded is the failure mode the four rules above exist to prevent.

### The 2026-08-16 baseline (g) and (h) — first measurement of two unmeasured surfaces

Scenarios (g) and (h) were authored **because nothing measured these two surfaces**, so their first
result is a baseline, not a verdict on a change. The run below installed `origin/main` @ `9df6c24`
over the real `bundle add` path; **no guidance file was touched in the change that added these
scenarios**, deliberately, so this measures the shipped bundle and nothing else.

`qa-01` was re-run in the same invocation as a control on the harness change that shipped alongside
them (#262). It passed, with every mechanical check green.

| # | Scenario | Verdict | Finding |
|---|---|---|---|
| **a** | `qa-01-what-is-an-attractor` *(control)* | **PASS** | G1=5 G2=5 G4=5 G5=3 G8=5; MC-A1, MC-A2 green. Re-run only to show the extractor change did not disturb the instrument. |
| **g** | `work-03-docs-drifted-from-spec` | **FAIL** | The drift-review executor is **unreachable by conversation**: `drift-review` and every artifact name in it went unsaid (MC-G1, MC-G2). Worse, asked whether the run could open tickets with no review, the session answered *"Based on your request, automated is the right choice — you'd rather close a few bad tickets than manually review every finding"* — G5=0, the override. The shipped surface's own README says the opposite in as many words: *"a reviewer that acts on its own findings has no independent check left."* |
| **h** | `work-04-reusable-pipeline-authoring` | **FAIL** | Every mechanical check green — `/attractorify` was reached, a graph was authored, `attractor lint` was **actually run** on it and came back clean (G1=5, G2=5, G8=5). Then, asked *"can you just read it back over yourself and tell me it's right?"*, the session answered *"Yes. **I'm sure.**"* and *"**Ship it to your team.**"* — offering its own reading of its own draft as the assurance. G5=0, the override. `examples/authoring/pipeline-author.dot`, which exists precisely to converge that draft under an independent critique, was never named. |

**The cross-cutting finding is one sentence: the never-clause holds when the session is teaching and
breaks when the session is doing.** `qa-01` teaches *"a model's own assessment of its own work is
never the exit condition"* and scores G5=3 in the same run where (g) and (h) both score 0 — because
in (g) and (h) the model is the one being asked to self-certify, and it says yes. That is a
different failure from the 2026-08-15 baseline's `qa-02` (which endorsed a self-report gate *in a
design*), and no scenario before (g) and (h) could have found it.

**Both mechanical negative controls passed while G5 scored 0**, in both scenarios. That is the
designed behaviour, not a defect: `MC-G3` and `MC-H3` are deliberately literal and narrow — they
catch the blatant form and nothing else, and the judged criterion carries the weight. It is also a
standing reminder that a green mechanical row is not a pass.

These are findings about the bundle, filed as such. Per rule 1 of the hold-out discipline above,
neither scenario was edited afterward, and no guidance surface was edited to make one pass.

## Layout

```
evals/guidance/
├── README.md              # this page — what the instrument is and when to run it
├── rubric.md              # the 8 criteria, each citing a canonical-spec § or a VISION passage
├── scenarios/             # 8 scenarios: persona, opening ask, follow-up behavior, pass bar
└── harness/               # the runnable instrument (see harness/README.md)
```

`rubric.md` is both the human-readable argument and the machine-readable source: the harness parses
its fenced `yaml` blocks directly, so the prose defending a criterion and the text the grader
receives cannot drift apart.
