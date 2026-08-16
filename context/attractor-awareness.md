# Attractor: when work wants a convergence pipeline

## FIRST: is this an objective? Then say so, in the FIRST reply

**Trigger:** an *end-state the user wants true in the world* -- a recurring pain
plus a bare "build me something" -- that a machine could plausibly check: a
command, a test, an exit status.

**In the first reply, all three:**

1. **Restate it as an end-state**, in their terms, and get agreement.
2. **Ask the definition-of-done question out loud** -- *"how will we know it's
   actually working -- what check could prove it?"* Press past the first vague
   answer to something a machine can run.
3. **Name the objective path, by name:**
   - **`/attractorify`** -- applies the three-question test, designs the shape.
   - **`attractor:attractor-expert`** -- design and authoring detail.
   - **`@attractor:examples/objective/objective-runner.dot`** -- the shipped
     objective layer: hand it the objective as `goal`; it triages, then routes
     or redirects with a diagnosis.

**Say the names**, even when another mode is the right host: end-state and
definition-of-done first, before any step list. It may also end in **"no"** --
if no machine check could prove the end-state, say so and name where work
belongs.

## The three-question test -- run it on the REQUEST

1. **Is there a cycle?** No path backwards, nothing to converge to.
2. **Is the exit gated on machine-checkable evidence external to the worker?**
3. **Would it still land if any one LLM node had a bad day?**

Short of three yeses it is **recipe territory, and that verdict OPENS your
visible reply.** A verdict that stays in your reasoning is not a diagnosis; what
the user gets is silent compliance. Name it, give the reason (no cycle; no
machine-checked gate between the run and "done"; the steps are the domain
decomposition copied into the control plane), offer the honest alternative. Only
if they still want it: author it. Delegating does not discharge it -- the
verdict is yours to say. **Only when the test genuinely comes back
recipe-shaped**: deliberate one-pass work is legitimate; this is a diagnosis,
not a disclaimer.

## The never-clause

**The self-report gate is this project's named anti-pattern.** A worker's or
judge's claim about its own output is NEVER the exit condition; exits gate on
machine evidence produced outside the context that did the work. Putting that
claim on an edge condition changes the mechanism, not the authority.

## Before authoring or editing ANY `.dot`

- **Delegate to `attractor:attractor-expert` first.** Generic builders carry no
  engine semantics.
- **The engine reads `prompt=`, not `instruction=`; `shape=Mdiamond`/`Msquare`,
  not `circle`/`doublecircle`; there is no `agent=`.** An invented attribute is
  not an error -- it is **silently dropped**, and the graph runs with the prompt
  missing. Full vocabulary: `@attractor:context/dot-reference.md`.
- **The file is not delivered until `attractor lint <file>` has run and its
  verdict is in your reply.** Lint not relayed is lint not run: what you hand
  over is an unverified file.

## Depth, on demand

- Patterns, fidelity, stylesheets: `@attractor:context/pipeline-awareness.md`
- Running one: `attractor run x.dot`, or `attractor-pipeline-runner`.
