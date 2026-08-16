# Attractor: when work wants a convergence pipeline

## FIRST: is this an objective? Then say so, in the FIRST reply

**Trigger:** an *end-state the user wants true in the world* -- a recurring pain
plus a bare "build me something" (*"our release notes are always stale"*) --
that a machine could plausibly check: a command, a test, a diff, an exit status.

**In the first reply, all three:**

1. **Restate it as an end-state**, in their terms, and get agreement.
2. **Ask the definition-of-done question out loud** -- *"how will we know it's
   actually working -- what check could prove it?"* Press past the first vague
   answer to something a machine can run.
3. **Name the objective path, by name:**
   - **`/attractorify`** -- applies the three-question test and designs the shape
     (as willing to answer "this wants a recipe" as "yes").
   - **`attractor:attractor-expert`** -- delegate for design and authoring detail.
   - **`@attractor:examples/objective/objective-runner.dot`** -- the shipped
     objective layer: hand it the objective as `goal`; it triages, then picks a
     lane, composes a child, or redirects with a diagnosis.

**Say the names**, even when another mode (brainstorm, design, planning, a
generic builder) is the right host: end-state and definition-of-done first,
before any step list. It may also end in **"no"** -- if no machine check could
prove the end-state, say so and name where the work belongs.

## The three-question test -- run it on the REQUEST

1. **Is there a cycle?** No path backwards, nothing to converge to.
2. **Is the exit gated on machine-checkable evidence external to the worker?**
3. **Would it still land if any one LLM node had a bad day?**

Short of three yeses -- especially a linear, gateless chain -- it is **recipe
territory, and that verdict OPENS your visible reply.** A verdict that stays in
your reasoning is not a diagnosis; what the user gets is silent compliance.
Name it, give the reason (no cycle; no machine-checked gate between the run and
"done"; the steps are the domain decomposition copied into the control plane),
offer the honest alternative. Only if they still want it: author it, then relay
`attractor lint`'s verdict. Delegating does not discharge it -- the verdict is
yours to say. **Only when the test genuinely comes back recipe-shaped**:
deliberate one-pass work is legitimate; this is a diagnosis, not a disclaimer.

## The never-clause

**The self-report gate is this project's named anti-pattern.** A worker's -- or a
judge's -- claim about its own output is NEVER the exit condition; exits gate on
machine evidence produced outside the context that did the work. Putting that
claim on an edge condition changes the mechanism, not the authority.

## Before authoring or editing ANY `.dot`

- **Delegate to `attractor:attractor-expert` first.** Generic builders carry no
  engine semantics.
- **`@attractor:context/dot-reference.md` is THE attribute vocabulary.** An
  attribute not on that card is inert -- it does nothing, silently.
- **Always `attractor lint <file>`** on what you author or edit.

## Depth, on demand -- pointers, not preloads

- Patterns, fidelity, stylesheets: `@attractor:context/pipeline-awareness.md`
- Runtime semantics, routing, debugging: `attractor:attractor-expert`
- Running one: `attractor run x.dot`, or `attractor-pipeline-runner`.
