# Design: fidelity=full Session Continuity

Status: validated through a full /systems-design pass (Phases 1-7, user-approved at each gate). Intra-run scope recommended for implementation; cross-run resume defined and deferred to a separate design.

Spec authority: strongdm/attractor nlspec @ `fb57a55`.

---

## 1. Problem Framing

`fidelity=full` cross-node session continuity is broken in the loop-pipeline backend
(`modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`).

Root cause: `_session_pool` stores a `session_id` and re-passes it to `session.spawn`
(fresh-spawn by design in every implementation) expecting the prior conversation to
resume — but `session_id` is an identity/trace token, not a history pointer, so history
is never restored. A `full` node that should "remember" an earlier same-thread node
starts fresh ("first message in this session").

**It is a type confusion: an *id* stored where a *conversation* belongs.**

Goal: restore intra-run, same-thread, in-process `full` continuity (spec-mandated by
§5.4). Discovered via a real DTU run plus multi-agent investigation; not a regression —
it never worked through the loop-pipeline backend.

---

## 2. Explicit Assumptions

- **Granularity:** `full` continuity = node-exchange `(instruction, final_output)` pairs.
  The spawn result exposes only `output` + `session_id`, NOT the child's inner tool-loop
  turns — so the carrier captures the conversation *between* nodes, not the child agent's
  internal reasoning. (User-accepted as the meaning of `full` at the backend layer.)
- **Sequentiality:** same-thread-key `full` nodes do not run concurrently *within one
  branch* (§3.8 sequential same-thread traversal). **MUST-VERIFY-DURING-IMPLEMENTATION** —
  if intra-branch same-key concurrency is real, add a per-thread lock around the
  truncate-append.
- **Context contract:** the child context implements `set_messages` — verified a validated
  `amplifier_core` contract method (`interfaces.py:202`, `validation/context.py:333`), so
  foundation's `parent_messages` injection reliably fires for our path.

---

## 3. System Boundaries

- **In scope (this design):** the loop-pipeline backend's continuity carrier. Reuses
  foundation's existing `parent_messages` injection (`_prepared.py:861`), which fires only
  when `session_id` is absent.
- **Out of scope (Phase 2):** cross-run resume after container restart (see the Phase 2
  section).
- **Layering:** the fix sits BELOW the spec's §4.5 backend contract
  (`run(node, prompt, context) → String|Outcome` — no session primitive); per §4.5
  silence, the realization mechanism is app-layer policy (mechanism-not-policy).

Spec authority: strongdm/attractor nlspec — §5.4 (`full` = reuse same thread, full history
preserved), §5.3 (sessions in-memory, non-serializable), unified-llm §2.6 (stateless
client; continuity = caller-passed message list).

---

## 4. Components and Responsibilities

- **`_thread_transcripts: dict[str, list[Message]]`** — renamed from `_session_pool`
  (which will no longer hold sessions). thread_key → accumulated conversation. Born
  branch-local: `clone_for_branch` already resets it to `{}` (`backend.py:150`), so the
  per-branch isolation fix (already shipped) composes for free — transcripts never cross
  branches.
- **Append (single writer):** after each `full` node completes, append `user`
  (= instruction) + `assistant` (= final `output`) — user/assistant roles only (strip
  system/developer, matching app-cli). Idempotent under intra-run replay (see §5).
- **Carry:** on the next same-thread `full` node, pass the list as `parent_messages` to a
  FRESH spawn — never a `session_id`. The backend guarantees this mutual exclusion by
  construction (closes the CR-1 silent-drop) and asserts it.
- **Outcome `session_id`** is still captured for `status.json` observability — it just no
  longer drives continuity.

---

## 5. Data and Control Flows

- Node N (`full`, thread T) runs → backend appends `(instr_N, out_N)` to
  `_thread_transcripts[T]`.
- Node N+1 (`full`, thread T) → backend passes `parent_messages=_thread_transcripts[T]`
  (no `session_id`) → foundation `set_messages` → child sees prior history. Realizes the
  spec's "reuse the same session" as the message list the stateless client already
  requires.
- **Idempotency (intra-run):** goal-gate retries clear `completed_nodes` and RE-RUN nodes
  (`engine.py:245-248` region) — a blind append would double a turn within one run.
  Resolved by **truncate-to-node-then-append**: a re-run node truncates its prior turn
  before re-appending, so replay rebuilds rather than duplicates.

---

## 6. Risks and Failure Modes

- **CR-1 silent injection no-op (RESOLVED, was must-fix):** foundation injects only under
  `if parent_messages and not session_id` + `if hasattr(context, "set_messages")` with no
  else. Closed backend-side: `set_messages` is a verified contract method (always present
  for our path), and the backend never passes `session_id` with `parent_messages` (mutual
  exclusion by construction + assert). Foundation warn-else = optional defense-in-depth,
  not required.
- **Intra-run double-append (RESOLVED):** truncate-to-node-then-append (above).
- **SC-3 thread_id is branch-local:** a shared `thread_id` across parallel branches
  silently forks into N independent transcripts (the isolation reset). Mitigation:
  documented note (+ optional parse-time warning).
- **SC-4 naming/type confusion:** keep the field renamed (`_thread_transcripts`), exactly
  one writer, and no stale id-into-pool path — leaving the old name/path is how the
  original bug happened.
- **Open assumption:** intra-branch same-key concurrency → per-thread lock if real
  (must-verify; see §2).

---

## 7. Tradeoffs

8-dimension frame, Candidate A (chosen) vs B vs C.

| Dimension | A — transcript in the pool (chosen) | B — `session.resume` + store (app-cli pattern) | C — keep the live session alive |
|---|---|---|---|
| Latency | good — in-memory pass, no per-node disk I/O intra-run | poor — `store.save`/`load` disk I/O every node on the hot path | good intra-run, but holds resources for the run duration |
| Complexity | good — one dict value-type change, 0 new author concepts | poor — new capability + store + routing, 2 repos | poor — session pinning + teardown override |
| Reliability | good — in-memory, no external store; contained blast radius | adequate — adds store availability/staleness/collision modes | poor — held sessions accumulate; breaks spawn contract |
| Cost | good — no new storage | adequate — store storage + ops | poor — N live sessions held for run duration |
| Security | good — stays in-process + existing checkpoint (same trust boundary as #39) | adequate — new transcripts-at-rest surface | neutral |
| Scalability | adequate — transcript grows per same-thread `full` node (inherent to `full`) | similar growth + store scaling | poor — N held sessions |
| Reversibility | good — value-type change + additive field, no new API/contract | poor — a registered capability + store is a hard-to-walk-back, 2-repo contract | poor — cross-cutting lifecycle change, least reversible |
| Org fit | good — single repo (attractor owns it) | poor — 2 repos, imports cross-process machinery | poor — cross-cutting |
| **Optimizes for** | minimal concepts + reversibility | cross-process persistence | object-identity reuse |
| **Sacrifices** | inner-agent-turn fidelity (node-exchange granularity only) | simplicity; adds new failure modes | the spawn-and-cleanup lifecycle; serializability |

- **Candidate B** imports a CROSS-PROCESS disk machine for an INTRA-RUN need, then must
  disable its own cross-run-restore half to stay §5.3-conformant, and re-impose
  branch-keying on a global store (re-deriving the isolation we already have). It solves
  the wrong scope and then fights itself.
- **Candidate C** fights the spawn-and-cleanup lifecycle and the stateless client (there
  is no durable live session; the conversation is already a message list), so it
  "collapses into A wearing a costume," can't serialize for cross-run (§5.3 impossible),
  and changes a cross-cutting lifecycle contract. Highest blast radius.
- **Dominant tradeoff:** Complexity × Reversibility — A wins decisively. Catalytic
  question: A is wrong only if inner-agent-turn fidelity across nodes is required (it
  isn't, per §5.4 + user).

---

## 8. Recommended Design

Candidate A, intra-run scope. The fix is *smaller than the bug* — it removes a type
confusion (id-where-a-conversation-belongs) rather than adding machinery.

Change the value type of one dict from `dict[str, session_id]` to
`dict[str, list[Message]]`; append the node exchange after each `full` node; carry the
list as `parent_messages` to a fresh spawn on the next same-thread `full` node. Reuses
foundation's `parent_messages` injection, the already-shipped per-branch isolation reset,
and the existing summary-fidelity machinery — no new components, no new author concepts.

---

## 9. Simplest Credible Alternative

Candidate A IS the simplest credible design (change the value type in one dict; reuse
three existing mechanisms). B and C are strictly more complex and solve the wrong scope.
No simpler adequate alternative exists.

---

## 10. Migration and Rollout Plan

Correct-by-default, NO feature flag (a flag would only preserve the broken path; this
restores spec-mandated behavior that is currently silently broken). Single-repo
(amplifier-bundle-attractor). Composes with the already-shipped per-branch isolation fix.
Implementation follows TDD; validate with the seed→recall codeword scenario.

---

## 11. Success Metrics

- Seed→recall continuity test passes intra-run (a `full` node sees the prior same-thread
  node's exchange).
- No double-append after a goal-gate retry re-runs a node.
- Per-branch isolation preserved (no cross-branch transcript leak).
- Injection is confirmed (no silent continuity drop) — backend assert holds.

---

## Phase 2 — Cross-run resume: evaluated, intentionally NOT built

Decision (2026-05): cross-run resume — persisting per-thread transcripts so a `full`
thread survives a container restart — was evaluated and deliberately NOT built.

Rationale:
- §5.3 caps cross-run `full` at `summary:high` degradation, never full restore. The
  achievable ceiling is a degraded summary, not continuity.
- That degraded behavior already exists: the M-23 path degrades `full -> summary:high`
  on resume from `completed_nodes` outcomes, so a resumed node is not blank today.
- Building it (engine->backend restore wiring, a transcript-aware summarizer,
  cross-run idempotency, transcript boundedness to avoid O(N^2) checkpoint rewrite,
  and redaction of conversation content at rest) is roughly 4-8 engineering-days across
  the riskiest engine paths (checkpoint / resume / persistence-at-rest), plus permanent
  maintenance and a new security surface — for marginal, already-mostly-covered value.
- Revisit only if a concrete pipeline relies on `full` cross-node memory, runs long
  enough to span restarts, AND the outcome-summary baseline is demonstrably insufficient.

### Behavior authors can rely on
- Within a run: `full` threads have full conversation continuity.
- Across a container restart: a `full` thread degrades to `summary:high` (built from
  node outcomes, per §5.3). Raw conversation memory does NOT survive a restart, by spec.
  Put state that must survive restarts in node outcomes/context (which are checkpointed),
  not in conversation threads.

---

## Proposed EXTENSIONS.md entries (for implementation — note them, do not write here)

1. `full` continuity is realized as a backend-held message-list carrier injected via
   `parent_messages` (the mechanism §4.5 leaves open), at **node-exchange granularity**
   `(instruction, final_output)` — not the child agent's inner tool-loop turns.
2. `thread_id` is **branch-local**: it does not join conversations across parallel branches
   (the per-branch isolation reset).
