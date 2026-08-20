# Design: attractor-scout session provenance — deterministic human-vs-agent classification

**Status:** SHIPPED. Built and proved in the branch that lands this document.
**Date:** 2026-08-20
**Repo:** `amplifier-bundle-attractor`, `main` @ `7856305`
**Scope:** a provenance layer in the `/attractor-scout` deterministic mining spine that decides,
before anything downstream sees a session, whether that session was the user's own work or an
agent's work on their behalf. Plus the policy that decides what is done with each answer.

**Read against:** `skills/attractor-scout/` (SKILL.md, `scripts/attractor_scout/provenance.py`,
`discover.py`, `extract.py`, `author.py`, `ranking.py`, `render.py`, `pipeline.py`,
`fixtures/synthetic_corpus.py`, `tests/test_scenario11_session_provenance.py`),
`docs/QUALITY_PROTOCOL.md` (§2 guidance toll, §7 leak defense),
`docs/designs/2026-08-19-attractor-scout-demonstration-layer.md` (the additivity discipline this
follows).

---

## 0. The complaint, and the one-sentence fix

The maintainer's words about the personalized artifacts: they *"confuse things I did and things an
agent did on my behalf."*

They were right, and the cause was structural rather than incidental. Session selection admitted
any root session carrying a `prompt:submit`, and **that event is byte-identical whether a person
typed the prompt or a harness fired a single non-interactive run**. Everything downstream — the
semantic clustering, the fit verdicts, the ranking, the artifact — inherited a pool that had never
been filtered by authorship at all, and the one place that could still have caught it defaulted to
`human` on missing information.

What ships: a deterministic **R0–R5 provenance ladder** that classifies every mined session at
mining time, records which rung fired and on what evidence, and a settled policy that mines
opportunities from human-presumed sessions only, counting everything else out loud.

---

## 1. The investigation, generically

Two independent investigations converged on the same map. Reported here without session ids,
workspace names, paths, or any other identifying value — only shapes and measured rates.

### 1.1 What is recorded, and what is not

`session:start` carries a parent id, a redaction marker, a session id, and a timestamp. **That is
all.** There is no argv, no run mode, no `stdin.isatty()`, no launching component, nothing that
says a human was at a keyboard.

The consequence is not a detail, it is the shape of the whole problem: **no positive human marker
exists in the data.** Human authorship can only ever be *presumed* from behavioural shape. Every
design instinct that wants a "human" default is therefore wrong, and the layer must be built so
that an absence of evidence resolves to `UNKNOWN` and never to a person.

### 1.2 The four leaks (each verified, each fixed)

| # | Where | What was actually wrong |
| --- | --- | --- |
| 1 | qualification (`carries_prompt`) | Sole qualifier was "a `prompt:submit` exists". A harness-fired root emits the same event. **Zero discrimination.** |
| 2 | `SessionRef` construction | `working_dir` — the single most discriminating field the corpus records — was read from `metadata.json` and then dropped on the floor. Nothing downstream could see it. |
| 3 | `extract.machine_launched` | Measured **dead on the production path**: pipeline/recipe-start roots and prompt-carrying roots were disjoint sets, so the flag fired for **0** of the qualified roots it was scoring. It contributed a harness point that could never be earned. |
| 4 | `ranking.apply_admission_gate` | `adjudicated or prior or author or HUMAN` — a **fail-open to human**. A unit with no author verdict at all was silently ranked as the user's own work. |

A fifth, subtler one: the step-4 LLM author adjudication saw only ≤700 characters of
noise-stripped prompt text. It could not see the working directory, the id shape, the session
timing, or the prompt count — the facts that actually decide the question — and was being asked to
recover an over-call using strictly less information than the deterministic layer already had.

### 1.3 The signals, measured

**Definitive (agent):**

- a non-empty `parent_id`;
- a delegate session-id shape (zero-filled prefix) or a composite `_<agent>` suffix;
- a first event of `session:fork` — **2000/2000** sub-sessions vs **0/9,149** roots, a perfect
  separator, and the reason the layer records which event came *first* rather than merely which
  events occurred;
- `pipeline:start` / `pipeline:node_start` / `recipe:start` / `recipe:loop_start`.

**Strong structural heuristics:**

- `working_dir` under a tmp root — **22/22** agent;
- the wider ephemeral pattern set (scratch dirs, worktrees, lanes, compact-UTC-timestamped
  basenames, mktemp-shaped basenames) — **39/40**;
- a single templated long brief (≥1,200 chars, opening with a markdown title, a role preamble, or
  an injected context file), never returned to — agent-leaning;
- ≥2 prompts with at least one inter-prompt gap ≥45 s — **3/3** human, **0/9** agent.

**False friends, measured and rejected** (named here so a future reader does not re-adopt one):

- a `deprecation:warning` first event — a bundle/time artifact, and it hits sessions in the home
  workspace band too;
- `application_host` — constant across the corpus, so it separates nothing;
- a UUID-shaped session id — thousands of UUID sessions carry parents, so the shape says nothing
  about lineage.

### 1.4 The honest residual

The single-prompt band in a stable home workspace is **21–29% agent-contaminated** and genuinely
undecidable from what is recorded. There is no clever reading of the available fields that
resolves it. It gets `UNKNOWN`.

---

## 2. The ladder

`scripts/attractor_scout/provenance.py`. Standard library only, in the deterministic layer, run
during extraction — before clustering, before ranking, before any model sees a record. First rung
that fires wins, so a definitive signal can never be overridden by a weaker one below it.

| Rung | Verdict | Fires on |
| --- | --- | --- |
| **R0** | `agent` | definitive lineage: `parent_id`, delegate/composite id shape, `session:fork` opener |
| **R1** | `agent` | definitive orchestration: a pipeline or recipe start event in the scan window |
| **R2** | `agent` | structural path: an ephemeral or lane-shaped `working_dir` |
| **R3** | `likely-agent` | exactly one prompt in the whole file **and** a templated long head |
| **R4** | `human-presumed` | ≥2 prompts **and** ≥1 inter-prompt gap ≥ `HUMAN_GAP_MIN_S` |
| **R5** | `unknown` | nothing decisive was recorded |

Every threshold is a named module constant carrying its measured justification in a comment
(`HUMAN_GAP_MIN_S = 45.0`, `TEMPLATED_PROMPT_MIN_CHARS = 1_200`, `ONE_SHOT_PROMPTS = 1`,
`DELEGATE_SID_PREFIX`, `FORK_FIRST_EVENT`, `ORCHESTRATION_START_EVENTS`, and the R2 pattern set as
one documented block). No threshold is inlined at a call site.

**Every verdict is auditable.** `Provenance` records the verdict, the rung, the *named signal*
that fired, and an evidence dict: `prompt_count`, `span_s`, `workspace_class`,
`workspace_pattern`, `first_prompt_shape`, `first_prompt_chars`, `max_prompt_gap_s`,
`first_event`. The evidence carries **classes and shapes, never values** — a workspace *class*,
not a path; a prompt *shape*, not prompt text — which is what makes it safe to render in an
artifact and safe to hand to an LLM tier.

---

## 3. The policy

Settled exactly this way, and stated in SKILL.md, in `provenance.py`, and in the artifact:

1. **Opportunities are mined from R4 (human-presumed) only.**
2. **R5 UNKNOWN is excluded from ranked opportunities** — counted and sampled in the artifact's
   provenance panel instead. The complaint being fixed *is* contamination, and a 21–29% agent
   band is too wide to hand back to someone as "your recurring work". The panel says so, and names
   the upstream change that would recover those sessions rather than pretending they are lost.
3. **R0–R3 agent sessions are excluded from opportunities and counted**, surfaced as one
   *already-automated footprint* section: what agents already do on your behalf. Context, not an
   opportunity to act on.
4. **The ranking fail-open is now fail-honest.** An absent author verdict routes a unit to a new
   `unattributed` channel: excluded from the ranking, reported, and *not* relabelled harness
   either — claiming it for the machine is the same unearned claim in the other direction.
5. **The step-4 LLM adjudication now receives the deterministic verdict and its evidence**, bound
   by one rule stated verbatim in the prompt: it may move a cluster *away* from human when the
   prompt text shows machine authorship the ladder could not see, and it may **never** move a
   cluster toward human. The adjudicator can tighten; it cannot launder an agent session into
   human work.

The enforcement point is a single named seam, `provenance.gate_units`, called by
`pipeline.run` and by the CLI's `rank` command **after** cluster membership has been re-verified
against the extract (so an invented member id is still caught by `--strict`) and **before**
scoring (so no non-R4 session can reach a score). A unit that loses every member is not deleted:
it is reported with its rung mix as already-automated or as unattributed.

One consequence worth stating plainly, because it will be visible on first run: **the ranked list
gets shorter.** That is the fix working. A smaller honest map beats a fuller one that mixes an
agent's work in with the user's.

---

## 4. What changed, file by file

| File | Change |
| --- | --- |
| `provenance.py` *(new)* | The ladder, the named constants, the evidence contract, `gate_units` (the mining boundary), `summarize` (the panel payload), and the fail-honest readers (`verdict_of`, `is_opportunity_eligible`). |
| `discover.py` | `working_dir` plumbed onto `SessionRef` (leak 2). `carries_prompt` split into `scan_events` — one read that returns the first event and any orchestration starts alongside the prompt answer — with `carries_prompt` kept as a thin, documented "necessary, never sufficient" wrapper (leak 1). `qualify` stamps each qualified ref with that prescan. |
| `extract.py` | Collects the first event, orchestration starts, and per-prompt timestamps; computes inter-prompt gaps; stamps `provenance` on every record. `machine_launched` **deleted** (leak 3). |
| `author.py` | The dead `machine_launched` harness point replaced by the provenance rung (R0–R2 → +3, R3 → +2). Deliberately **no human-side counterpart**: there is no positive human marker to score. |
| `ranking.py` | `apply_admission_gate` returns three channels and no longer defaults to human (leak 4); `rank` emits `unattributed` and `summary.n_unattributed`. |
| `render.py` | Additive provenance panel: per-rung counts, sample evidence, the already-automated footprint, the honest UNKNOWN story, the upstream-fix note. Zero bytes — CSS included — when the ranked result carries no provenance data. |
| `pipeline.py`, `attractor_scout_cli.py` | Wire the mining boundary and attach the panel payload; re-stamp records read back from an existing `extracts.jsonl`. |
| `SKILL.md` | Step 1 names the provenance pass and the policy with the rung table; step 4 passes the verdict + evidence into the adjudication and states the never-promote-to-human rule verbatim; step 6 names both gates in order; step 7 and the Output section name the panel; one new hard rule. |

---

## 5. Tests

`tests/test_scenario11_session_provenance.py`, over a new
`fixtures/synthetic_corpus.build_provenance_corpus`. Every planted class runs the *same* shape of
work at the *same* cost — a real loop, a terminal verification, one error it recovers from —
differing only in its leading tool (so A-rung clustering separates them) and in its **provenance**.
That isolation is the point: any difference in what reaches the ranking is caused by the ladder and
by nothing else.

The pinned verdict table: `human_multi_turn` → R4 · `delegate_sub_session` → R0 · `pipeline_root` →
R1 · `harness_oneshot_tmp` → R2 · `goal_lane_worktree` → R2 · `templated_brief_root` → R3 ·
`stable_single_prompt` → R5.

Three RED proofs, because a gate that has never been shown failing is not a gate:

1. **The leak, reproduced then closed.** The harness one-shot class is a prompt-carrying root in a
   tmp workspace. The test asserts the old sole qualifier still admits it, strips the provenance
   stamp to reproduce the pre-provenance world exactly (the author prior reads it as human and the
   unit lands in the ranked opportunities), then shows the shipped path excluding it — *and*
   counting it in the panel.
2. **The fail-open regression.** A unit with no author verdict must not rank as human, must not be
   relabelled harness, and must land in `unattributed` with `author == unknown`.
3. **Renderer additivity, as a byte test.** No provenance data ⇒ byte-identical output, with a
   marker sweep proving not one panel byte leaked into the frozen half.

Plus: the false friends stay rejected; the R2 pattern set is parametrized including the
timestamped and mktemp shapes; `gate_units` never mutates its input; gated-out units are reported
with their rung mix; the two R4 thresholds (`HUMAN_GAP_MIN_S`, `HUMAN_MIN_PROMPTS`) are pinned by
a scripted-cadence fixture that goes red if either is weakened; and the CLI `rank` path is
guarded by its own end-to-end RED proof, independent of `pipeline.run`.

**The leak scan runs over the SHIPPED artifact, not a vacuum.** The panel is summarized with the
gate run over the *real* fixture corpus (a populated already-automated and unattributed set), then
the full HTML is rendered and asserted to carry no session id, no workspace path, and no prompt
text. One member-derived string does legitimately reach the HTML: a unit's **name**, printed in
the unattributed table. That is intentional and safe — a unit name is an LLM-authored cluster
*label*, never a path, id, or prompt body — and a dedicated test pins that channel so a future
change that starts piping a path or an id into a name breaks loudly.

Suite: **406 passed / 52 skipped → 454 passed / 54 skipped** (508 collected).

---

## 6. Named limits

- **No positive human marker exists.** `R4` is `human-presumed`, and the name is the claim. If the
  event schema ever records one, R4 should be re-derived from it rather than kept as a behavioural
  proxy.
- **The home-workspace single-prompt band is undecidable** at 21–29% agent contamination. Excluded
  as `UNKNOWN`, never as human, never as agent.
- **R2 is conservative by construction.** A workspace that merely looks unusual is left to R5. The
  pattern set only contains shapes that cleared the measured bar.
- **R4 needs timestamps.** A corpus whose prompt events carry no usable per-event timestamp cannot
  express an inter-prompt gap, so it resolves to R5 across the board. That is the honest failure
  mode: an empty ranking with a full panel, not a fabricated one.
- **R4 has one residual bypass, and it is the ladder's honest ceiling.** A harness that paces its
  prompts like a person — multiple turns with staged gaps ≥ `HUMAN_GAP_MIN_S` — running in a
  stable workspace is indistinguishable from human work in today's data, so a few such runs may sit
  inside the human-presumed pool. Nothing behavioural closes this: pacing and workspace are exactly
  the signals a determined harness can mimic. **The upstream invocation-provenance marker (§7) is
  what closes it** — a recorded "who launched this" needs no timing heuristic. Named here, and named
  out loud in the artifact panel (`R4_RESIDUAL_NOTE`), rather than left as a silent hole in the one
  pool the ranking actually trusts.
- **`fold_children` remains a dead path.** `qualify` returns roots only, so a child session never
  reaches `extract_all` and `_fold_into` never runs on the production path. Left in place and
  noted rather than "fixed": making it live would change distinct-session reach for every existing
  unit, which is a ranking-semantics decision, not a provenance one. Flagged for the orchestrator.

---

## 7. ★ The upstream fix this motivates

**Record invocation provenance at `session:start`.** Four fields — `argv`, the run mode, whether
`stdin` was a tty, and the launching component — would collapse rungs **R2 through R5 into one
recorded boolean**. Every heuristic in this document exists solely because that boolean is not
written down, and every one of them is a proxy that can only ever be *presumed*.

This is not an attractor-scout change; it is an event-schema change in the context-intelligence
writer, and it would pay off for every consumer of that corpus, not just this skill. The layer
shipped here is deliberately built so that the day such a field appears, R2–R5 can be replaced by
reading it, and R0/R1 remain useful as they are.

Named here as an observation for the ecosystem rather than filed as a work item, because the
decision about whether to record it belongs with whoever owns the writer.
