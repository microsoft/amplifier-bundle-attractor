# Attractor Expert — Design-Time Authoring Defenses

Four defense layers that static lint cannot see. The agent self-checks every
pipeline it designs or reviews at the command/contract layer. Each defense
maps to a real failure mode from production incident history.

---

## Defense 1 — Pipe-masked exit codes (CMD-001)

**The hazard:** `tool_command="cmd 2>&1 | tail -N"` — in `/bin/sh`, the
pipeline's exit code is `tail`'s, which is always 0 when it can read input.
The gate records SUCCESS even when `cmd` printed `No such file or directory`.

**Self-check question:** Does any tool node pipe its primary command into a
filter (`tail`, `head`, `grep`, `sed`, `awk`, etc.) without `set -o pipefail`?

**Honest alternatives:**
```sh
# Redirect — exit code is cmd's; output in out.log for diagnostics
cmd > out.log 2>&1

# Token gate (no pipe) — both branches fire; routing on the token
cmd && printf ok || printf fail
```

**Note:** `pipefail` is not POSIX sh — use `bash -c 'set -o pipefail; ...'`
or prefer the redirect idiom. The lint rule CMD-001 catches this statically;
the agent catches it at design time, before lint runs.

---

## Defense 2 — Always-true sentinels (CMD-002)

**The hazard:** `tool_command="cmd 2>&1 | tail -N && echo SENTINEL"` — `tail`
exits 0 unconditionally (it can always read its input), so `&& echo SENTINEL`
fires regardless of whether `cmd` succeeded. `tool.last_line` becomes the
sentinel string even when the command failed. The gate always says yes.

**Self-check question:** Does any tool node end with `&& echo TOKEN` or
`&& printf TOKEN` after a pipe to a filter? If so, the sentinel is always-true.

**Honest token gate (no pipe):**
```sh
# Both branches fire — the || distinguishes success from failure
cmd && printf green || printf red

# Exit-code gate — failure is preserved
cmd && printf green || { printf red; exit 1; }
```

**Key discriminator:** does the command's success or failure still influence
either the exit code or the emitted token? Sentinel hazard shapes destroy that
influence. The lint rule CMD-002 catches this statically.

---

## Defense 3 — Judge verdict contracts

**The hazard:** A `goal_gate=true` LLM node whose prompt does not mandate a
machine-readable verdict. Under the fail-closed contract (engine-semantics.md
§5), a goal gate reached via plain prose returns RETRY, not SUCCESS. But a
judge that writes "NOT CONVERGED — 2 of 7 criteria pass" as prose and never
calls `report_outcome` or emits structured JSON will exhaust retries and
degrade to FAIL — the replan loop never fires as intended.

**Self-check question:** Does every `goal_gate=true` node have an explicit
outcome instruction in its prompt?

**Compliant patterns, in escalation order (RETCON, 2026-08-29 -- `report_outcome`
is no longer the taught mechanism):**

1. **Verdict file + deterministic gate** (route on evidence, not typed
   sentinels -- preferred for complex judges, and the default choice):
   ```dot
   judge [shape=box, prompt="... Write verdict to .ai/verdict.txt: first
       line must be exactly CONVERGED or NOT_CONVERGED."]
   verdict_gate [shape=parallelogram, max_retries=0,
       tool_command="grep -q '^CONVERGED$' .ai/verdict.txt && printf ok || printf fail"]
   judge -> verdict_gate
   verdict_gate -> done  [condition="tool.last_line=ok"]
   verdict_gate -> fix   [condition="tool.last_line=fail"]
   ```

2. **Node-written `status.json`** (canonical spec §4.5 / Appendix C), for
   out-of-band or spawned-worker outcomes -- the engine reads it back and now
   auto-injects the path + envelope into every spawned worker's instruction:
   > "Write your verdict to the status.json contract path given in your
   > instructions, with `outcome: success` or `outcome: fail`."

3. **Pure JSON response**:
   > "Respond with ONLY a JSON object: `{\"status\": \"success\"}` or
   > `{\"status\": \"fail\", \"reason\": \"...\"}`. No surrounding prose."

4. ~~`report_outcome` tool call~~ -- **removed in the engine's 0.2.0 repair
   release; no longer callable at all.** It was a legacy compatibility
   window, never the taught mechanism, and even while it existed a tool call
   was still a self-report from inside the same context that produced the
   work: not an exemption from "verification inside the context that
   produced the evidence is not verification" (`context/dot-reference.md`).
   Only patterns 1-3 above remain. See `docs/PIPELINE_PATTERNS.md` §6's
   anti-pattern catalog ("`report_outcome`-as-primary", alongside
   "LLM-Emitted Routing Sentinel").

**Cross-reference:** engine-semantics.md §5 (verdict-recovery ladder, fail-closed
goal-gate contract, `is_explicit` field).

---

## Defense 4 — Delta-assertion gates (base-SHA anchor)

**The hazard:** A work-completion gate that runs tests on an unmodified tree.
`cargo test` (or `pytest`, `go test`, etc.) green on the baseline proves
nothing about the work the pipeline was supposed to do. If the implementation
node silently failed to write anything, the gate passes anyway.

**Self-check question:** Does the pipeline's completion gate assert that the
expected work delta actually exists — not just that tests pass?

**The base-SHA pattern:**
```sh
# Record the baseline before any work begins (in a setup/orient node):
git rev-parse HEAD > .ai/base-sha

# In the completion gate — assert commits exist beyond the baseline:
base=$(cat .ai/base-sha)
new_commits=$(git log --oneline "$base"..HEAD | wc -l)
[ "$new_commits" -gt 0 ] || { echo "No commits since baseline"; exit 1; }
```

Or assert specific files changed:
```sh
base=$(cat .ai/base-sha)
git diff --name-only "$base"..HEAD | grep -q 'src/' || {
    echo "No source changes since baseline"; exit 1; }
```

**When this matters most:** pipelines that call external build/test commands
where a silent no-op (missing script, wrong cwd, wrong target) still exits 0.
The delta assertion is the second check — it catches "tests passed but nothing
changed."

---

## Defense 5 — Deferral/observer routing power

**The hazard:** An observer or deferral node that detects a problem but has no
conditional out-edges and no durable evidence file. Its discovery defaults to
SUCCESS and goes nowhere. An observation that cannot route is decoration.

**The archetype:** A node that checks for missing implementation work and
writes a prose report — but all its out-edges are unconditional, so the
pipeline continues to success regardless of what it found.

**Self-check question:** For every node whose job is to NOTICE a problem
(audit, health-check, preflight, deferral), does it either:
- (a) have conditional out-edges keyed to what it observes (requires a
  machine-readable observation — evidence file + tool gate), OR
- (b) is it explicitly documented as advisory-only and kept OFF the success
  path's certification chain?

**Compliant pattern — evidence file + gate:**
```dot
check_impl [shape=box,
    prompt="Check whether the implementation exists. Write .ai/impl-status.txt:
        first line must be PRESENT or MISSING."]

impl_gate [shape=parallelogram, max_retries=0,
    tool_command="grep -q '^PRESENT$' .ai/impl-status.txt && printf ok || printf missing"]

check_impl -> impl_gate
impl_gate -> next_step  [condition="tool.last_line=ok"]
impl_gate -> fix_impl   [condition="tool.last_line=missing"]
```

**The deferral label:** a node explicitly named or documented as a "deferral"
(e.g., `windows_defer`) should either have routing power (pattern above) or
carry a comment explaining why it is advisory-only and confirming it is not on
the certification chain. Undocumented deferrals that default to success are the
failure mode.

---

## Retry sophistication — patterns to teach

The following patterns appear in production pipelines and exceed the
sophistication of current exemplars. They are doctrine, not novelty.

### Causal per-gate retry targets

Route retry to the node that can change the cause — not always back to
`attempt`. Different failure classes have different root causes:

```dot
build_harness [shape=parallelogram, goal_gate=true,
    retry_target="fix_harness"]   // harness fails? fix the harness

run_tests [shape=parallelogram, goal_gate=true,
    retry_target="fix_tests"]     // tests fail? fix the tests, not the harness

security_scan [shape=parallelogram, goal_gate=true,
    retry_target="fix_security"]  // security fails? fix security issues
```

Compare to the simpler (but less causal) pattern where all retry_targets
point to the same `attempt` node — that works for homogeneous failure classes
but loses precision when failures have different causes.

### Per-failure-class fix nodes

Differentiated failure edges with dedicated fix nodes per failure class:

```dot
// Instead of one generic fix node:
verify -> attempt [condition="outcome=fail"]

// Use per-class fix nodes:
verify -> fix_build   [condition="tool.last_line=build_failed"]
verify -> fix_tests   [condition="tool.last_line=test_failed"]
verify -> fix_security [condition="tool.last_line=security_failed"]
```

This is the mechanized form of differentiated failure edges (see
DOT-AUTHORING-GUIDE.md §"Causal Retry Patterns"). The task-runner exemplar
(`examples/patterns/task-runner.md`) uses a single triage path — per-class fix
nodes are appropriate when failure classes are distinct and have different
remediation strategies.

### Graph-level `fallback_retry_target`

Graph-level `retry_target` and `fallback_retry_target` participate in
**unsatisfied goal-gate exit resolution** (spec §3.4) — they are the final
steps in the order: node retry → node fallback → graph retry → graph fallback.
They are NOT consulted on per-node failure (spec §3.7). Per-node failure with
no matching edge and no node-level retry target terminates FAIL — it does not
fall through to graph-level recovery. For per-node recovery, use a node-level
`retry_target` attribute or a conditional corrective edge.

In convergence pipelines, set graph-level targets as the last resort in
goal-gate-exit resolution:

```dot
digraph ConvergencePipeline {
    graph [
        goal="...",
        // These fire on unsatisfied goal-gate exit (spec §3.4),
        // NOT on per-node failure (spec §3.7).
        retry_target="attempt",
        fallback_retry_target="analyze_plan"   // goal-gate exit last resort: replan
    ]
    // ...
}
```

This is convergence doctrine — not just a tutorial feature. The graph-level
fallback is the final safety net when all goal gates are unsatisfied and the
primary retry cannot address the failure. Teach it alongside `retry_target`
in convergence pipelines, with the understanding that it applies to goal-gate
exit, not to ordinary per-node failure.

**Individually-bounded legs do not compose.** Growing the number of legs (the
patterns above) increases exposure to this defect if no rule bounds the
whole path: two separately-budgeted gates can bounce a persistent failure
between each other forever, because neither gate's own counter sees the
other's attempts (a live instance: `critique(FAIL) -> verify(PASS) ->
critique`, each node individually well-behaved). When corrective work spans
more than one gate, at least one counter must span the whole path — see
`docs/PIPELINE_DESIGN_PRINCIPLES.md` §3, "Individually-bounded legs do not
compose."

**Cross-reference:** DOT-AUTHORING-GUIDE.md §"Retry with Fallback" and the
Causal Retry Patterns section; examples/pipelines/04-retry-with-fallback.dot.
