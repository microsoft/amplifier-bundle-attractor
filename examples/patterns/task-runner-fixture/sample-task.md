---
id: sample-task
title: Planted-red demo — write a sha256 answer file
tier: demo
effort: minutes
target_dir: (wherever you run the task runner)
touches: [.ai-demo/]
verify: sample-task.verify.sh
status: ready
---

# Planted-red demo — write a sha256 answer file

## Goal

A file `.ai-demo/answer.txt` exists in the working directory containing the
sha256 hex digest of `.ai-demo/nonce`.

## Why this fixture exists

This is a **DRILL-style planted-red fixture**: the DoD script fails at the
runner's first gate visit by design. It keys on `.ai/iter` — the runner's
gate-visit counter, incremented only by the verify gate. Pre-running the script
during the work phase cannot avoid the red, because `.ai/iter` does not exist
until the gate fires.

The first gate visit always goes red. The second goes green if `answer.txt` is
correct. This guarantees the corrective path (`verify red -> triage -> attempt
-> verify green`) executes at least once, live, so you can see the basin absorb
a failure before trusting it with real work.

The fixture README is honest about this: the first red is planted on purpose.

## Definition of done

**Mechanical** (`sample-task.verify.sh`): `.ai-demo/answer.txt` contains
`sha256sum(.ai-demo/nonce)`. First invocation always fails (creates the nonce
and requires a second gate visit).

**Judgment** (critique gate): trivial — confirm the answer was computed from the
actual nonce (not guessed or hardcoded) and nothing outside `.ai-demo/` was
touched.

## What the worker should do

When verification fails, read `.ai/verify.log`: it tells you the nonce now
exists and what the answer must contain. Compute `sha256sum .ai-demo/nonce`,
write the hex digest into `.ai-demo/answer.txt`, and the next gate visit will
pass.

## Non-goals

- Touch nothing outside `.ai-demo/`.
- Do not modify the DoD script (its first-run failure is intentional).
- Do not read `.ai/iter` directly — the planted red is a property of the gate,
  not something to work around.
