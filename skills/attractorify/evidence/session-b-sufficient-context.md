# Evidence Session B — Sufficient Context: Lint-Clean .dot Design

> **SIMULATION — an authored illustration, not a session record.**
> This file demonstrates intended skill behavior through a constructed scenario.
> It has no session ID, timestamps, or external verifiability.
> The generated `.dot` artifact (`session-b-generated.dot`) is real and passes
> `dot-runner lint` clean (independently verified). The transcript is constructed.
> Live-session evidence is being collected separately and will replace or
> supplement this file; the skill stays experimental until real transcripts land.

**Scenario:** User invokes `/attractorify` with a well-specified task. The session
context answers all three questions of the three-question test and provides a
machine-checkable DoD.

**Session context at invocation time:**
> User is working in a repo with a PR diff at `/tmp/pr-123.diff`. They want an
> automated PR review pipeline that produces a structured review with a Summary,
> Risk assessment, and per-file notes. The review must be checked for completeness
> by a script before the pipeline exits. Budget: 30 minutes max.

---

## Transcript

**User:** `/attractorify automated PR review — reads /tmp/pr-123.diff, produces
Summary + Risk + Per-file notes (a note for every changed file), exits only when
a completeness check passes. Budget: 30 minutes max.`

**Skill (Step 1 — Diagnose):**

Applying the three-question test from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0:

- **Q1. Is there a cycle?** YES — review -> completeness check -> fix_review -> check
  is a corrective loop.
- **Q2. Is the exit gated on evidence?** YES — a completeness script (required
  sections present + a per-file note for every file in the diff) is
  machine-checkable and external to the LLM reviewer.
- **Q3. Would it survive a bad day?** YES — a review that omits "## Risk" or
  skips a changed file fails the script regardless of how confident the LLM
  sounds; the gate is deterministic.

All three questions: YES. An attractor pipeline is warranted.

**Skill (Step 2 — No clarifying questions needed):**

Session provides: goal (structured PR review), DoD (completeness script: three
sections + per-file coverage), budget (30 minutes, encoded as
`max_pipeline_duration="1800s"`), target (current working directory). Nothing is
missing.

**Skill (Step 3 — Design):**

Delegating to `attractor:attractor-expert` for design review of the shape and
routing (gate placement, retry target, edge conditions) before handback.

Following design order from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0:
1. Sink first: `done` node — exit only when `check_gate` passes.
2. Gate: `check_gate` (parallelogram, deterministic completeness script,
   `goal_gate=true`).
3. Loop: `check_gate -> fix_review -> check_gate` corrective cycle.
4. Work node: `review` (box, LLM, reads diff, writes `.ai/review.md`).

Node contract (from `docs/DOT-AUTHORING-GUIDE.md`):
- `review`: Objective = produce structured review; Constraints = write to
  `.ai/review.md`; Available capabilities = read_file, bash; Required evidence
  = `.ai/review.md` contains all three sections and a note per changed file.
- `check_gate`: Objective = verify completeness (sections + per-file coverage);
  evidence = the script's exit code.

Writing artifact to `skills/attractorify/evidence/session-b-generated.dot`...

```
dot-runner lint skills/attractorify/evidence/session-b-generated.dot
```

**Lint output:**
```
dot-runner lint: skills/attractorify/evidence/session-b-generated.dot: OK (no findings)
```

**Skill (handback):**

Artifact: `skills/attractorify/evidence/session-b-generated.dot`

Invocation:
```bash
dot-runner run skills/attractorify/evidence/session-b-generated.dot \
    --param pr_diff=/tmp/pr-123.diff \
    --param target_dir=$PWD \
    --cwd .
```

Structural choices:
- `check_gate` is `parallelogram` (not `box`) — completeness (sections present,
  every changed file covered) is deterministic scripting, not a judgment call.
  Whether each note is genuinely actionable IS judgment — the gate checks
  structure and coverage; an independent critique node could gate adequacy.
- `goal_gate=true` on `check_gate`, not on `review` — the LLM finishing is not
  a goal state; the external check passing is.
- `fix_review` is separate from `review` — fresh-eyes re-entry with context of
  what was missing, not a blind retry.

---

## Lint output (captured)

```
dot-runner lint: skills/attractorify/evidence/session-b-generated.dot: OK (no findings)
```

The generated `.dot` is at `skills/attractorify/evidence/session-b-generated.dot`.
