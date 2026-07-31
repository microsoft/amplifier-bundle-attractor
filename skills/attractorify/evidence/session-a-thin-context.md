# Evidence Session A — Thin Context: Clarifying Questions

> **SIMULATION — an authored illustration, not a session record.**
> This file demonstrates intended skill behavior through a constructed scenario.
> It has no session ID, timestamps, or external verifiability.
> Live-session evidence is being collected separately and will replace or
> supplement this file; the skill stays experimental until real transcripts land.

**Scenario:** User invokes `/attractorify` with a vague description. The session
context does not specify a machine-checkable DoD, a budget, or a target repo.

**Session context at invocation time:**
> User has been working on a Python service. The last few messages discuss wanting
> to "improve the code quality" and "maybe automate the review process somehow."
> No task file, no DoD script, no repository name, no iteration cap mentioned.

---

## Transcript

**User:** `/attractorify improve my Python service's code quality`

**Skill (Step 1 — Diagnose):**

Applying the three-question test from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0:

- **Q1. Is there a cycle?** Potentially — code quality improvement with a
  verification loop is a classic attractor shape. Depends on what "done" means.
- **Q2. Is the exit gated on evidence?** Unknown — "improve code quality" is not
  machine-checkable as stated. No DoD script, no lint threshold, no test suite
  target mentioned.
- **Q3. Would it survive a bad day?** Unknown — depends on what the gate is.

Q2 cannot be answered from the session context. The session under-determines the
design. Proceeding to Step 2.

**Skill (Step 2 — Ask):**

The session answers Q1 (there's a loop-shaped problem here) but leaves Q2 and Q3
open. Three things are genuinely missing:

1. **What does "improved code quality" mean in a machine-checkable way?**
   For example: all `ruff` checks pass? Test coverage above 80%? Zero `mypy`
   errors? The DoD must be something a script can verify — not "the code looks
   better." Without this, the pipeline has no convergence criterion and will
   loop indefinitely or exit on LLM self-report.

2. **What is the target repository and its working directory?** The pipeline
   needs `--param target_dir=...` and `--cwd`. Is this the service you have
   open now, or a different repo?

3. **What is the budget?** How many verification attempts before the run
   escalates to you? (Typical: 4–8 iterations for a quality-improvement task.)

I'm not asking about your goals or your timeline — those are clear enough. These
three questions are what the pipeline's structure depends on, and I cannot design
the gate or the loop without them.

**User:** The target is `~/projects/myservice`. DoD is: `ruff check . && mypy .`
both exit 0. Budget: 5 iterations.

**Skill:** Context is now sufficient. Proceeding to Step 3 — design.

*(Design continued in a follow-up exchange; the produced artifact is not the
focus of this session. Session B demonstrates the full design + lint path.)*

---

## What this demonstrates

- The skill did NOT ask a boilerplate questionnaire. It asked exactly three
  questions derived from what was genuinely missing: the machine-checkable DoD
  (Q2 of the three-question test), the target repo (parameterization gap), and
  the budget (loop structure gap).
- It did NOT ask about the user's goals, timeline, team size, or other
  context that the session had already answered or that doesn't affect pipeline
  structure.
- The clarifying questions are derived from the three-question test's Q2 gap,
  not from a fixed intake form.
