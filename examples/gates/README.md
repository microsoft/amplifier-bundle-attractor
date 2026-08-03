# Gate Primitive Library

Copy-paste `.dot` snippets for pipeline authors. Each primitive is a self-contained,
lint-clean digraph: a `parallelogram` node with its `tool_command`, the routing edges
that consume its token, and comments stating exactly what it observes and what each
token means.

This directory is the seed of a growing gate-primitive library. The first three
primitives cover the delta-assertion family: the unanchored `diff-nonempty` form
answers "is the tree dirty?"; the anchored form shipped here answers the
completion-grade question "did durable work land since we started?". More
primitives join this directory as they are extracted from real pipelines.

---

## Gate contract (read before using any primitive)

### Signal channel

The engine exposes the last non-empty stdout line as `context.tool.last_line`. Route
on that; never on `tool.output`.

### Exit code = outcome

Nonzero exit → FAIL. FAIL does not traverse plain edges — it routes only via
`condition="outcome=fail"` edges, `retry_target`, or `runs_on` nodes. A red path
with no fail-route runs off the rim.

### Idiom A — token gate (always exit 0)

```
cmd && printf green || printf red
```

Outcome is SUCCESS both ways; routing is purely on the token. Simple, no stale-label
risk — but `goal_gate=true` on it is vacuous (the node never fails).

### Idiom B — exit-code gate

```
cmd && printf green || { printf red; exit 1; }
```

With routing edges:

```dot
gate -> fix  [condition="outcome=fail"]
gate -> next [condition="context.tool.last_line=green && outcome=success"]
```

Makes `goal_gate` meaningful (exit unearnable without evidence) — but **requires the
`&& outcome=success` conjunction** on the green edge. A failing tool node does not
refresh `tool.last_line` (ToolHandler returns early on nonzero exit). On a second
visit after a failure, a stale `green` + FAIL can match both edges; the engine picks
one deterministically (spec §3.3: weight desc, then lexical tiebreak), but the
conjunction makes intent unambiguous and prevents the stale edge from being the
deterministic pick. This is the stale-label discipline (see the
`examples/patterns/task-runner.dot` header for the same rule in a shipped exemplar).

### Tokens are single lowercase words

`green`/`red`, `present`/`missing`, `changed`/`unchanged`, `ok`/`fail`. Use
`printf`, not `echo` with trailing content. The token must be the last non-empty
line even when the wrapped command prints output — redirect noisy output to a log
file, e.g. `> .ai/test.log 2>&1`.

---

## Primitives in this library

### `base-sha-anchor.dot` — Record base SHA at pipeline start

**What it observes:** The current HEAD SHA before any work begins.

**Tokens:** `ok` (anchor written) | `fail` (not a git repo or .ai/ unwritable)

**Idiom:** B (exit-code gate). Fails hard when git is unavailable — a vacuous ok
would silently disable all downstream delta gates.

**Use when:** Any pipeline that needs to assert durable commits exist after work
(pair with `delta-assertion-gate.dot`). Run as the first preflight node.

---

### `delta-assertion-gate.dot` — Assert durable commits exist since base

**What it observes:** Whether commits exist in `$expected_paths` since the recorded
base SHA (written by `base-sha-anchor.dot`).

**Tokens:** `changed` (commits exist; gate green) | `unchanged` (no commits; gate
red) | `no_anchor` (.ai/base-sha missing; gate red)

**Idiom:** B (exit-code gate). Fails hard when no delta exists — a vacuous pass
repeats the incident's undetected no-op run.

**Use when:** Any pipeline where work nodes must commit their changes. Scope
`$expected_paths` to the paths the work was expected to touch — unrelated concurrent
commits do not fake the delta.

**The doctrine:** Working-tree claims are not evidence in a shared checkout —
durable commits are. See `docs/PIPELINE_DESIGN_PRINCIPLES.md §7` for the full
discipline and the incident analysis.

**Anchored vs unanchored diff:**

| Form | Question answered | When to use |
|---|---|---|
| `! git diff --quiet` | Is the tree dirty? | Cheap smoke check in a private checkout |
| `git log BASE..HEAD -- <paths>` | Did durable work land since we started? | Completion gate (this primitive) |

The unanchored form goes permanently green after any unrelated dirtying. The anchored
form is the completion-grade question.

---

### `preflight-evidence-file.dot` — Preflight writes durable evidence; later gate reads it

**What it observes:** Environment/vantage state at pipeline start (tools available,
repo state, network reachability, etc.).

**Tokens:** `ready` (all required checks passed) | `blocked` (required environment
unavailable)

**Idiom:** B (exit-code gate). Binary contract: `ready` (exit 0) or `blocked`
(exit 1). The evidence file records full check results (including non-fatal states
like `REPO_CLEAN=dirty`) for later inspection without re-running checks.

**Use when:** Any pipeline that depends on external environment state. Especially
important for long-running pipelines where environment changes mid-run are possible.

**The positive lesson (incident 2026-07-28):** The preflight vantage gates that DID
work wrote truthful results to a durable env file (`.verify-vantages.env`,
`TIER_B=ok/TIER_C=ok`) before work began. Durable evidence files, written by
deterministic nodes, read by later gates — the same discipline as the base-SHA
anchor.

**Environment-identity lesson:** Verify running-code identity before entering a loop
(see `docs/PIPELINE_DESIGN_PRINCIPLES.md §3`). The preflight evidence file records
that identity durably. If the environment changes mid-run, the durable file preserves
what was true at pipeline start.

---

## Both-walls discipline

Every gate primitive in this library must have its red path exercised, not just its
green path. A gate whose red path was never exercised repeats the T0-1 mistake
(8 of 24 shipped examples had dead corrective edges because the red path was never
tested).

For the delta-assertion gate specifically: run it once where commits land (gate
green) and once where work is only uncommitted or absent (gate red). The gate's
value is precisely that it catches the second case — which is the case that matters.

---

## Cross-references

| Topic | Where to look |
|---|---|
| Delta-assertion discipline, doctrine sentence, honest limits | `docs/PIPELINE_DESIGN_PRINCIPLES.md §7` |
| Gate idiom selection (Idiom A vs B), stale-label rule | `docs/PIPELINE_DESIGN_PRINCIPLES.md §1`, `examples/patterns/task-runner.dot` header |
| Anti-pattern catalog (AP-1/2/3), routing discipline | `docs/PIPELINE_PATTERNS.md §6`, `§7` |
