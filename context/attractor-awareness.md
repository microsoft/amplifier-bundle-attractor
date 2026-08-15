# Attractor: when work wants a convergence pipeline

## FIRST: is this an objective? Then say so, in the FIRST reply

**Trigger:** the user names an *end-state they want true in the world* -- a
recurring pain plus a bare "build me something" (*"our release notes are always
stale -- build me something"*) -- and a machine could plausibly check it: a
command, a test, a diff, an exit status.

**In the first reply, all three:**

1. **Restate it as an end-state**, in their terms, and get agreement.
2. **Ask the definition-of-done question out loud** -- *"how will we know it's
   actually working -- what check could prove it?"* Press past the first vague
   answer to something a machine can run.
3. **Name the objective path, by name:**
   - **`/attractorify`** -- applies the three-question test with them and designs
     the shape (as willing to answer "this wants a recipe" as "yes").
   - **`attractor:attractor-expert`** -- delegate for design and authoring detail.
   - **`@attractor:examples/objective/objective-runner.dot`** -- the shipped
     objective layer: hand it the objective as `goal`; it triages, then picks a
     lane, composes a child pipeline, or redirects with a written diagnosis.

**Say the names.** If another mode wants the request -- brainstorm, design,
planning, a generic builder -- that is fine, and may be the right host. **Name
the objective path in the same breath anyway**, end-state and
definition-of-done first, before any step list. An objective answered with a generic design
conversation silently skips the layer built for it: a recorded failure here, not
a hypothetical. It may also end in **"no"** -- if no machine check could prove
the end-state, say so and name where the work belongs.

## The three-question test

1. **Is there a cycle?** No path backwards, nothing to converge to.
2. **Is the exit gated on machine-checkable evidence external to the worker?**
3. **Would it still land if any one LLM node had a bad day?**

Short of three yeses -- especially a linear, gateless chain -- it is **recipe
territory: say so before authoring a graph.**

## The never-clause

**The self-report gate is this project's named anti-pattern.** A worker's -- or a
judge's -- claim about its own output is NEVER the exit condition; exits gate on
machine evidence produced outside the context that did the work. Putting that
claim on an edge condition changes the mechanism, not the authority.

## Before authoring or editing ANY `.dot`

- **Delegate to `attractor:attractor-expert` first.** Generic builders carry no
  engine semantics and re-discover the foot-guns the hard way.
- **`@attractor:context/dot-reference.md` is THE attribute vocabulary.** An
  attribute not on that card is silently inert -- invented attributes do not
  error, they do nothing.
- **Always `attractor lint <file>`** on what you author or edit.

## Depth, on demand -- pointers, not preloads

- Attributes: `@attractor:context/dot-reference.md`
- Patterns, fidelity, stylesheets, objective layer:
  `@attractor:context/pipeline-awareness.md`
- Runtime semantics, routing, debugging: `attractor:attractor-expert`
- Running a pipeline from here: the `attractor` CLI (`attractor run x.dot`) or
  the `attractor-pipeline-runner` agent.
