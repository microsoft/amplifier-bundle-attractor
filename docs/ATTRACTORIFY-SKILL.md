# /attractorify Skill

The `/attractorify` skill is the session-facing entry point for attractor pipeline
design. It lives at `skills/attractorify/SKILL.md` and registers as a slash command
in any Amplifier session that loads this bundle.

## What it does

When invoked, the skill:

1. **Diagnoses first** — applies the three-question test
   (`docs/PIPELINE_DESIGN_PRINCIPLES.md` §0) to decide whether an attractor
   pipeline is warranted at all. When the honest answer is "one-shot it," "this is
   a recipe," or "use the existing attractor," it says so and cites the rule.

2. **Designs conversationally** when a pipeline IS warranted — extracts goal (as
   end-state), machine-checkable DoD, budgets, and evidence gates from the session
   context; consults the `attractor:attractor-expert` agent by delegation at design
   start and final review; produces a `.dot` file; lints it with `dot-runner lint`;
   and hands back the runnable artifact plus the exact invocation.

3. **Asks before designing** when context is thin — if the session under-determines
   any of DoD, budget, target repo, or acceptance criteria, it asks targeted
   clarifying questions derived from what is actually missing. Not a boilerplate
   questionnaire.

## Placement rationale

This skill lives in `amplifier-bundle-attractor` (the domain home) rather than
`amplifier-bundle-skills` (the sibling collection) because:

- The skill teaches from and links to `agents/attractor-expert.md`,
  `docs/DOT-AUTHORING-GUIDE.md`, `docs/PIPELINE_DESIGN_PRINCIPLES.md`, and the
  `dot-runner lint` CLI — all of which live here. Co-location keeps the decision
  logic beside the doctrine it applies.
- The skill is domain-specific (attractor pipeline design), not a general-purpose
  session utility. The skills collection is the right home for cross-domain skills;
  this one is inseparable from the attractor doctrine.
- Maintainability: when the doctrine docs evolve, the skill's links update in the
  same PR. A cross-repo dependency would create a lag.

The tradeoff: users who load only `amplifier-bundle-skills` will not see
`/attractorify`. If adoption evidence shows this is a friction point, the skill
can be mirrored or the placement reconsidered.

## Execution mode

The skill runs **inline** (not as a forked sub-session). A forked skill with
default `context_depth=none` sees nothing of the current session — which is
precisely what the skill needs to read. The council/council-here pair in the
skills family is the precedent: council (fork, external target) vs council-here
(inline, current session). This skill is the inline variant.

## Reference surfaces

- `skills/attractorify/SKILL.md` — the skill itself
- `agents/attractor-expert.md` — source prompt of the `attractor:attractor-expert`
  agent the skill delegates to for design and review
- `docs/PIPELINE_DESIGN_PRINCIPLES.md` §0 — the three-question test and one-sentence rule
- `docs/DOT-AUTHORING-GUIDE.md` — node contract, lint rules, `dot-runner lint` CLI
- `skills/attractorify/evidence/` — **design simulations** (authored illustrations
  of the three acceptance scenarios: clarifying-question exchange, lint-clean
  design run, diagnosis-honesty case — NOT session records; see the README in
  that directory). Live-session evidence is being collected separately and will
  replace or supplement these files before the skill is promoted beyond
  experimental status.
