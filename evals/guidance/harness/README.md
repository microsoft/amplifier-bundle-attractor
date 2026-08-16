# The harness

What actually runs a guidance-eval scenario, and how to operate it.

For what the instrument *is* and when the protocol requires it, read
[`../README.md`](../README.md) first. This page is the operator's manual.

---

## The one-liner

```bash
cd evals/guidance/harness
./run.sh --smoke                 # one scenario, whole plumbing, ~15 min
./run.sh                         # all eight
./run.sh --scenarios qa-02-never-converges work-02-twelve-step-pipeline
./run.sh --list                  # what would run, with the criteria each cites
```

`--list` short-circuits before any infrastructure check, so you can inspect the instrument
without Docker running.

## What a trial does

```
  launch DTU  (ubuntu 24.04 + uv, named guideval-*)
       |
  install     amplifier CLI, then `amplifier bundle add git+https://github.com/microsoft/
              amplifier-bundle-attractor@<ref>` -- the REAL user path, redirected to the local
              Gitea mirror -- then `amplifier bundle use attractor`, the attractor CLI, and a
              pinned checkout at /opt/attractor-src
       |
  readiness   amplifier on PATH; the ACTIVE bundle is attractor; `attractor lint --help`;
              the shipped objective runner lints clean; (exemplar scenarios) the fixture is RED
              -- all before a single model call is paid for
       |
  seed        the scenario's fixture into /workspace, if it has one
       |
  drive       session mode:  AIUser holds a real multi-turn conversation, in persona
              exemplar mode: `attractor run objective-runner.dot` against the fixture
       |
  extract     the session's own transcript.jsonl, rendered deterministically; plus the small
              decisive artifacts (.objective/*, any authored .dot)
       |
  mechanical  the scenario's machine_checks, re-run against the artifacts AFTER the fact
       |
  grade       a grader agent that never saw the session scores the cited rubric criteria,
              reading only the normalized /eval/graded/ folder
       |
  destroy     the DTU (kept on trial error, for post-mortem)
```

## Why the install goes through a Gitea mirror

`run.sh` clones the bundle into a detached checkout at a resolved SHA and pushes *that* to the
mirror. The DTU then installs from `github.com/microsoft/amplifier-bundle-attractor`, which the
profile's `url_rewrites` transparently redirect to the mirror.

Two things fall out of that, and both are the point:

1. **The eval walks the path a real user walks.** `amplifier bundle add git+https://...` is the
   documented install, so a guidance surface that only works from a local checkout — an unshipped
   file, a path that resolves in the repo and nowhere else — fails here rather than in a user's
   hands.
2. **It can never grade a working tree.** A push from a working tree would install whatever
   happens to be uncommitted on the machine running the eval, and the results would describe a
   state that exists nowhere else. `run.sh` prints a loud warning when your checkout HEAD differs
   from the pinned SHA, so the difference is never silent.

## The activation trap

`amplifier bundle add` only **registers** a bundle. Without `amplifier bundle use attractor` the
session composes the *default* bundle, none of the guidance surfaces under test are in play, and
the eval cheerfully grades stock Amplifier — producing a plausible number that means nothing.

This is the most dangerous silent no-op in the harness, so it is asserted twice:
`install.yaml` greps `amplifier bundle current` for `Active bundle: attractor` and fails the
install, and the readiness stage re-checks it before the trial proceeds.

## Where results go

**Always outside the repository.** Default:

```
<workspace>/.amplifier/evaluation/guidance-pilot/<UTC-timestamp>/
```

Override with `--results-root` or `$GUIDANCE_EVAL_RESULTS_ROOT`. The harness **refuses to start**
if the resolved path is inside the checkout: run directories carry full prompts, transcripts, and
provider-adjacent material, and none of that is source.

```
<UTC>/
├── run.json                 # bundle ref + SHA, scenarios, scoring thresholds, model pins
├── inputs/                  # rubric.md and the scenario files AS RUN
├── results.md / .json       # per-scenario verdicts, per-criterion scores, failed checks
└── <scenario>/
    ├── launch-profile.yaml  install.log  readiness.txt
    ├── ai_user.json         # the AI user's own conclude verdict (session mode)
    ├── runner.log           # the pipeline's log (exemplar mode)
    ├── transcript-source-paths.txt   workspace-listing.txt
    ├── graded/              # the normalized folder the grader is allowed to see
    │   ├── transcript.md  scenario.md  mechanical.json  artifacts/
    ├── grader.yaml          # generated from rubric.md's blocks
    ├── grader/              # initial_report.md, rubric.json, grader_result.json
    └── outcome.json
```

`inputs/` exists so a later reader is never guessing which version of the rubric produced a score.

## The layout here

| Path | What it is |
|---|---|
| `run.sh` | preflight, gitea, pinned mirror push, dispatch |
| `run_guidance_eval.py` | the trial loop: launch → install → readiness → drive → extract → check → grade |
| `config.yaml` | scenario list, smoke scenario, scoring thresholds (mirrors `rubric.md`) |
| `profiles/guidance-dtu.yaml` | the DTU: ubuntu + uv + pytest, url_rewrites to the mirror |
| `agents/attractor-user-install/` | the real-user install path, extraction hints, and the AI user's invocation guide |
| `fixtures/notesvc/` | the workspace the exemplar scenarios run against — deliberately RED |
| `tests/` | offline guards for the pure-python parts of the driver (no Docker, no models, no spend) |

## Building blocks reused

From [`microsoft/amplifier-bundle-evaluation`](https://github.com/microsoft/amplifier-bundle-evaluation):

| Block | Use |
|---|---|
| `ai_user.AIUser` | drives the session in persona, one continuous conversation, `conclude` verdict |
| `extractor.Extractor` | prepared per run; the transcript itself is recovered deterministically (below) |
| `grader.Grader` + `grader.schema` | blind scoring against the generated `grader.yaml` |
| `harness.dtu.DTU` | launch / exec / file_push / destroy |
| `harness.install.compose_launch_profile`, `install_agent`, `verify_env` | merged profile, in-DTU setup, host env preflight |
| `harness.loaders.load_agent` | the standard `agents/<id>/` directory contract |

`harness.trial.run_trial` is **not** called directly. Its stage sequence is replayed here with two
deviations it does not support: a per-scenario persona passed to `AIUser.run(...)`, and a
mechanical-checks stage between extract and grade. This follows the custom-harness precedent in
the evaluation bundle's own example 01.

**The transcript is recovered deterministically, not summarized.** The session's own
`transcript.jsonl` is rendered to markdown by a small in-DTU Python script. The artifact under
grading is the conversation; asking a model to summarize it first would destroy the evidence and
then grade the destruction. If recovery fails, the harness records that loudly in the trial's
notes and marks the grade provisional rather than quietly substituting the AI user's own account.

## Prerequisites

- `amplifier-digital-twin`, `amplifier-gitea`, `docker` (running), `git`, `python3`
- `amplifier_evaluation` importable — clone `microsoft/amplifier-bundle-evaluation`, `uv sync`,
  and activate its `.venv` (or set `AMPLIFIER_EVALUATION_ROOT`). `run.sh` will find and activate
  a sibling checkout's venv automatically.
- `ANTHROPIC_API_KEY` in the environment or `~/.amplifier/keys.env`

## Cost and time

Session scenarios are two to three conversational turns plus grading: roughly 10–20 minutes and a
few dollars each. Exemplar scenarios run a real pipeline with a child pipeline underneath and are
the expensive ones — budget up to an hour and a few tens of dollars. A full eight-scenario run is a
couple of hours of wall time.

Start with `--smoke`. It walks the entire plumbing on the cheapest scenario.

## The offline tests

The parts of the driver that decide **what a check is allowed to read** are pure functions over
text, and they are worth pinning without paying for a trial:

```bash
python3 -m pytest evals/guidance/harness/tests -q     # needs pyyaml; nothing else
```

`tests/test_assistant_answer_text.py` guards `assistant_answer_text()` — the extractor behind the
`assistant_answer_lacks_all` check kind. Its failure modes are asymmetric: reading too much makes
a check fail loudly, reading too little makes it pass silently. The tests are written around that
asymmetry, and the fixture transcript they read carries an assistant answer with its own `## `
markdown headings, which is what used to truncate it (#262).

These tests are not part of the CI matrix — that matrix builds and tests `modules/`. Run them when
you touch the driver.

## When a trial breaks

- **The DTU is kept on trial error** (`--keep-dtus-on-failure`, on by default). Inspect it with
  `amplifier-digital-twin exec <id> -- bash`, then destroy it yourself.
- Stranded instances are always named `guideval-*`. `amplifier-digital-twin list` finds them.
- A readiness failure aborts *before* model spend and writes `readiness.txt` naming the gate.
- A failed `machine_check` is reported per check with its `why:` — it is a finding about the
  bundle or about the harness, and results.md never averages it away.

## Reading a result honestly

The smoke run's *scores* are not data. Read the trial end-to-end by hand: the transcript, the
grader's `initial_report.md`, and the mechanical checks. A suspicious pass is a bug in the eval
until proven otherwise — the failure mode this instrument is most exposed to is grading an
environment where the bundle was never active at all.
