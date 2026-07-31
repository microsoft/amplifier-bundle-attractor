# Evidence Session C — Diagnosis Honesty: Not an Attractor

> **SIMULATION — an authored illustration, not a session record.**
> This file demonstrates intended skill behavior through a constructed scenario.
> It has no session ID, timestamps, or external verifiability.
> Live-session evidence is being collected separately and will replace or
> supplement this file; the skill stays experimental until real transcripts land.

**Scenario:** User invokes `/attractorify` on work that does not warrant a
pipeline. The skill diagnoses honestly and recommends against building one.

**Session context at invocation time:**
> User wants to generate a CHANGELOG entry for the current sprint. They have a
> list of merged PRs and want the skill to format them into a Markdown changelog
> section. One-shot transformation: input = PR list, output = formatted Markdown.

---

## Transcript

**User:** `/attractorify generate a CHANGELOG entry from this sprint's merged PRs
— here's the list: [PR-101: Fix auth bug, PR-102: Add dark mode, PR-103: Upgrade
deps]. Output: Markdown section with date and bullet list.`

**Skill (Step 1 — Diagnose):**

Applying the three-question test from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0:

- **Q1. Is there a cycle?** NO — this is a one-shot transformation: PR list →
  Markdown. There is no iteration, no corrective loop, no retry needed. The
  output is fully determined by the input.
- **Q2. Is the exit gated on evidence?** The output is readable Markdown — there
  is no machine-checkable DoD that would distinguish "correct" from "incorrect"
  formatting. Human review is the gate, which makes this a recipe, not an
  attractor.
- **Q3. Would it survive a bad day?** Irrelevant — Q1 is already NO. A linear
  one-shot transformation cannot be an attractor.

**Diagnosis: This is NOT an attractor use case.**

The one-sentence rule from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0 applies:
*"Recipes are for staged sequential workflows with human approval gates; attractor
pipelines are for machine-verified convergence. If your pipeline graph has no
cycle, it should probably have been a recipe."*

This has no cycle. It should be a one-shot prompt, not a pipeline.

**Recommendation:** Ask me directly — I can generate the CHANGELOG entry right
now in this session. If you want it in a repeatable format, a recipe (a simple
script that calls an LLM once) is the right tool. Building an attractor pipeline
for a one-shot formatting task would be engineering overhead with no convergence
benefit.

**User:** That makes sense. Can you just generate it?

**Skill:** Yes.

```markdown
## Sprint 2026-07-31

- Fix authentication bug (#101)
- Add dark mode support (#102)
- Upgrade dependencies (#103)
```

---

## What this demonstrates

- The skill applied the three-question test honestly. Q1 (is there a cycle?)
  returned NO, which is sufficient to decline.
- It cited the one-sentence rule from `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0
  explicitly — not a vague "this doesn't need a pipeline."
- It offered the correct alternative (one-shot in-session) rather than leaving
  the user without a path forward.
- It did NOT build a pipeline. The skill is an adoption surface for the doctrine,
  not an attractor-pushing machine.
