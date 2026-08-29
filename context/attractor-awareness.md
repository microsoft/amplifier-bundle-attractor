# Attractor: when work wants a convergence pipeline

## FIRST: is this an objective? Say so in the FIRST reply

**Trigger:** an *end-state the user wants true in the world* -- a recurring pain
plus a bare "build me something" -- that a machine could check.

**In the first reply, all three:**

1. **Restate it as an end-state** in their terms; get agreement.
2. **Ask the definition-of-done question out loud** -- *"how will we know it's
   working -- what check could prove it?"* Press past vague answers to what a
   machine can run.
3. **Name the shipped path, by name:**
   - **`/attractorify`** -- applies the three-question test, designs the shape.
   - **`@attractor:examples/objective/objective-runner.dot`** -- the objective
     layer: hand it the objective as `goal`; it triages and routes.
   - **Docs/examples/guidance drifted from a spec?**
     **`@attractor:examples/drift-review/`** -- `check_findings.py` re-opens
     every `file:line` citation. Shape is not truth: a finding is a claim a
     **human** verifies, never filed unread.

**Say the names**, even when another mode hosts it: end-state and
definition-of-done before any step list. It may end in **"no"**: if no machine
check could prove it, name where the work belongs.

## The three-question test -- run it on the REQUEST

1. **Is there a cycle?**
2. **Is the exit gated on machine-checkable evidence external to the worker?**
3. **Would it still land if any one LLM node had a bad day?**

Short of three yeses it is **recipe territory, and that verdict OPENS your
visible reply.** A verdict left in your reasoning lands as compliance.
Name it, give the reason (no cycle; no machine-checked gate; the steps are the
domain decomposition copied into the control plane), and the honest
alternative. If they still want it, author it. Delegating does not discharge
it. **Only when it genuinely comes back recipe-shaped**: a diagnosis, not a
disclaimer.

## The never-clause

**The self-report gate is this project's named anti-pattern.** A worker's or
judge's claim about its own output is NEVER the exit condition; exits gate on
machine evidence from outside the context that did the work. Putting that claim
on an edge changes the mechanism, not the authority. **And it binds YOUR OWN
work in this conversation, not just pipelines you design:** what you authored,
you cannot certify. Relay MACHINE verdicts as facts; never your own judgment.
Asked to vouch, say what was machine-checked, what was not, and offer
the independent path: `@attractor:examples/authoring/`, or a fresh reviewer.

## Before authoring or editing ANY `.dot`

- **Delegate to `attractor:attractor-expert` first.** Generic builders carry no
  engine semantics.
- **The engine reads `prompt=`, not `instruction=`; `shape=Mdiamond`/`Msquare`,
  not `circle`/`doublecircle`; there is no `agent=`.** An invented attribute is
  not an error -- it is **silently dropped**, and the graph runs with no prompt.
  Vocabulary: `@attractor:context/dot-reference.md`.
- **The file is not delivered until `dot-runner lint <file>` has run and its
  verdict is in your reply.** Lint not relayed is lint not run: you hand over
  an unverified file. Relay the **findings**, not the exit code -- lint exits 0
  on warnings, and `VOCAB-001` ("will run with no prompt") is a warning. The
  only clean verdict is `OK (no findings)`.

## Depth, on demand

- Runtime semantics, routing, debugging: `attractor:attractor-expert`
