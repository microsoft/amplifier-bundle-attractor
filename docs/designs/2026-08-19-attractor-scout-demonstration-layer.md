# Design: the attractor-scout demonstration/teaching layer

**Status:** SHIPPED. Decision-complete when written (2026-08-19); built and live-proved in the
PR that also lands this document. The design below is preserved as written except for a
pre-publication leak sweep (`docs/QUALITY_PROTOCOL.md` section 7) and section 6, which records
what actually changed on contact with the build.
**Date:** 2026-08-19
**Repo:** `amplifier-bundle-attractor`, `main` @ `4aaedcf`
**Scope:** the "second half" of the `/attractor-scout` onboarding skill — a demonstration and
teaching layer on top of the shipped opportunity-mining half. Design only; no implementation here.

**Read against:** `skills/attractor-scout/` (SKILL.md, `scripts/attractor_scout/`, `tests/`,
`fixtures/synthetic_corpus.py`, `evals/README.md`), `skills/attractorify/SKILL.md`
(eval-frozen — ZERO edits planned), `examples/authoring/pipeline-author.dot` +
`check_authored_pipeline.py` (A0–A10), `examples/patterns/convergence-factory.dot`,
`context/attractor-awareness.md` (never-clause + self-certification output contract),
`context/dot-reference.md` (the vocabulary), `docs/VISION.md`, `docs/QUALITY_PROTOCOL.md`
(§2 guidance toll, §3 decision matrix, §7 leak defense).

---

## 0. The gap, and what ships

The shipped skill delivers the mining half: deterministic spine → LLM label/cluster/verdict/author
tiers → `rank --strict` re-verification (FATAL on mismatch) → deterministic self-contained HTML
map. What it does not do — the maintainer's onboarding intent:

1. **Teach what an attractor pipeline IS** (learn-about).
2. **Demonstrate how attractor could have helped THEM** — for a real top opportunity, the actual
   pipeline that would have converged their recurring scenario, with the why.
3. Adjudicate **skillify's** role.

What ships (one sentence): after the map renders, the skill authors — via one bounded
reasoning-role delegation — a small doctrine-shaped `.dot` + companion for the user's **#1 ranked
opportunity**, machine-gates it (`attractor lint` when available, always the vendored stdlib
doctrine checker), assembles a **demonstration bundle** whose every number is deterministically
re-verified, and re-renders the same self-contained HTML with a one-screen "what is an attractor
pipeline" primer plus per-demo teaching sections; further demos are user-selected, one explicit
yes at a time.

Two invariants carry over untouched and bind everything below:

- **The renderer stays LLM-free.** All generated narrative arrives as *data*; the renderer is
  deterministic and byte-reproducible given the same inputs and a pinned timestamp.
- **Own data only; nothing leaves the machine.** The one network-adjacent option (fetching the
  public linter via `uvx`) is inbound, consent-gated, and never automatic (D3).

---

## 1. The eight decisions at a glance

| # | Question | Decision |
|---|---|---|
| D1 | Where in the flow / how many | New SKILL.md steps **8–9 after render**: auto-demo exactly **K=1** (the top-ranked opportunity), then user-selected extras, one explicit yes each; render is re-run per demo (cheap, deterministic). Zero opportunities ⇒ primer-only. |
| D2 | Generation mechanism | **(a) bounded fresh-context `reasoning` delegation**, fed a deterministically assembled brief (`demo brief` subcommand: evidence digest + doctrine contract + vocabulary excerpt). Budget: **2 attempts** (initial + one gate-informed retry). (b) `pipeline-author.dot` is the *offered* independent path, never the default; (c) inline authoring rejected. |
| D3 | Machine gates | Ladder: **`attractor lint` (PATH) → consent-gated `uvx` lint (ask first, flagged as inbound fetch) → vendored stdlib doctrine checker (always runs) → honest `UNVERIFIED` label**. Verification levels `lint+doctrine` / `doctrine-only` / `none`, rendered verbatim; red verdicts after the retry ⇒ demo not published. Self-certification panel (machine-checked / not-checked / independent path) in every demo. |
| D4 | Teaching content contract | Numbers **only** from re-verified `ranked.json` via deterministic templates; LLM narrative fills six named prose slots, validated by a **digit-whitelist** and a **node-name check** (fail loud). Primer = fixed deterministic template, once per artifact, **linking** the published explainer. Convergence math = deterministic arithmetic with labeled illustrative constants. |
| D5 | HTML integration | Extend `render.py` with an optional `demos` input (new `--demos` flag). Primer + demo sections insert between the sampled range and the ranked table. **No demos ⇒ byte-identical output to today.** Generated `.dot`/`.md` land beside the HTML in `<outdir>/attractor-scout-demos/`; the HTML embeds the `.dot` text (self-contained) *and* states the on-disk relative path. |
| D6 | skillify | **No runtime role** (argued); generation-time lineage acknowledged in this design doc + PR body only; possible future role named, not built. |
| D7 | Tests/evals | CI-deterministic: assembly validation, ladder labeling, vendored-checker pin + red-proof, renderer additivity/honesty, leak guard auto-covers new files. Guidance toll discharged via the documented **fresh-session walk-through fallback** (scout precedent). LIVE proof on the maintainer's real corpus, artifacts outside the repo, leak-lens on all PR evidence. |
| D8 | Out of scope | Auto-running generated pipelines; any edit to `skills/attractorify/SKILL.md`, `examples/authoring/*`, or the explainer; wayfinder; team-shared tier; auto-installing the CLI; demos for honest-NOs/waste; a new guidance-eval scenario (deferred, precedented). |

---

## 2. Decisions in full

### D1 — Where demonstration happens; K; the interactive shape; cost

**Decision.** Two new SKILL.md steps *after* the existing step 7 (render), leaving steps 1–7
byte-untouched:

- **Step 8 — Demonstrate (auto, K=1).** Immediately after the map is written, generate one
  demonstration for `opportunities[0]` of `ranked.json` (the list is already score-ordered with
  the escalating-trajectory boost; `provisional`/`unproven` flags carry into the demo header
  verbatim — they are caveats, not disqualifiers). Then re-render the same `$OUTPUT_PATH` with
  `--demos`. If `opportunities` is empty, skip generation and render **primer-only**
  (`demos: []`, `primer: true`) with the honest note that a demonstration needs a subject.
- **Step 9 — Offer more (user-selected).** Show the top 5 not-yet-demonstrated opportunity names
  and ask exactly one question: *"Want another one demonstrated? Name or number — or no."* Each
  yes runs the step-8 mechanics for that unit with `--append`, then re-renders. Never loop
  without a fresh explicit yes. If the user said "map only" at any point, honor it and skip
  step 8 entirely.

**Cost story (stated in SKILL.md in one line).** Each demo costs one `reasoning`-role delegation,
at most two when the machine gates reject the first draft; no fast-tier involvement; re-rendering
is deterministic and free. The auto-demo is not consent-gated because demonstration *is* the
skill's second half — the user invoked an onboarding skill whose description says it demonstrates;
every demo beyond the first is consent-gated because that is marginal spend for marginal
personalization.

**Rationale.** The map renders first so the mining payoff is never held hostage to demo latency,
and a demo failure leaves a complete v1 artifact (the layer is strictly additive even in failure).
K=1 because the primer + one worked example is what teaches the concept; demo #2 onward
personalizes further but teaches nothing new, so it should cost an explicit yes. Top-1 by rank —
not user-picked-first — because an onboarding user cannot yet judge which unit is most
attractor-shaped; the ranking exists precisely to make that call, and the "show me" moment dies if
it opens with a menu.

**Rejected alternatives.** *Automatic top-K for K≥2*: multiplies LLM spend with diminishing
teaching return, and lengthens the artifact past onboarding attention. *Fully user-selected (K=0
auto)*: breaks the maintainer's "demonstrate how attractor could have helped them" — a
demonstration you must configure before seeing is a form, not a demonstration. *Demo before first
render*: couples the mining payoff to generation latency and makes demo failure block the map.

### D2 — Worked-pipeline generation mechanism

**Decision: option (a)** — one bounded, fresh-context **`reasoning`-role delegation** per demo,
driven by a deterministically assembled brief. Mechanics:

1. `$CLI demo brief --ranked "$WORK/ranked.json" --unit <unit_id> --workdir "$WORK/demo"`
   derives the slug (printed on stdout), creates `$WORK/demo/<slug>/`, and writes `brief.md`
   there — assembling the instruction pack **deterministically** (no improvised prompts): the unit's
   verified stats and gist; its fit detail (cycle/gate/recovery booleans); the most common
   verify-class tools observed in the cluster members' terminal windows (computed from the
   extract — this is the evidence the gate command is derived from); the A0–A10 authoring
   contract summary; a compact vendored excerpt of the engine vocabulary (the `prompt=` /
   `shape=` / `tool_command=` / `condition=` / `fidelity=` / `max_retries=` / `goal_gate=`
   essentials from `context/dot-reference.md`, because a fresh-context delegate sees only what
   the instruction carries); a **node budget of max 9 nodes** (start, exit, 1–2 workers, gate(s),
   budget wall, loud terminal — the convergence-factory shape in miniature); and the output
   contract: write `pipeline.dot`, `pipeline.md` (companion naming every worker — A9), and
   `narrative.json` (six prose slots, D4) into `$WORK/demo/<slug>/`.
2. The orchestrating session delegates with that brief (fresh context, `reasoning` role).
3. Machine gates run (D3). On a red verdict, **one** corrective retry: re-delegate with the gate
   reports appended verbatim (this enacts the corrective loop the demo is teaching). Still red ⇒
   the demo is not published; say so in chat; the map keeps its primer.

Demos are generated **only for `verdict ∈ {OPPORTUNITY, OPPORTUNITY(unproven)}`** units — which
guarantees, by the fit test's own construction, that cycle and gate evidence exist for the brief
to cite. The brief is untrusted-text-safe by the same rule as `pipeline-author.dot`: unit names
and gists are expanded into *prompts and prose slots only*, never into a `tool_command` the
orchestrator executes.

**Rationale.** (a) is the only option that satisfies all four constraints at once: it works with
**bundle-only installs** (no `attractor` CLI required — the doctrine checker is stdlib, D3); it
keeps **cost bounded and known** (≤2 delegations); it preserves **nothing-leaves-the-machine**
(delegation reasons over local text); and it keeps the **separation the never-clause needs** — a
fresh-context author whose output the orchestrator machine-checks means the session relays machine
verdicts about work it did not itself write into its own context.

**Rejected alternatives.** **(b) drive `pipeline-author.dot` via the CLI as the default**: its own
preflight hard-fails without `attractor`, `python3`, `sha256sum` on PATH — exactly the tools an
onboarding user may lack; it budgets up to 4 author iterations and 14400 s wall clock — an
unbounded-feeling cost for a teaching artifact; and it produces a *hardened reusable pipeline*,
which is more than a demonstration needs. It is instead the **offered independent path** in every
demo (D3's panel and D4's run-forward section carry its exact invocation with the brief
pre-filled) — the same shape as attractorify Step 8. **(c) inline authoring by the driving
session**: burns main-context tokens on a context already carrying the whole mined corpus;
produces the worst self-certification posture (the certifying session is the literal author, so
even relaying machine verdicts reads as self-vouching); and loses the clean retry seam that
feeding gate reports to a fresh delegate provides.

### D3 — Machine gates, the degradation ladder, and the self-certification contract

**Decision — the ladder, exactly:**

| Rung | Condition | What runs | `verification.level` |
|---|---|---|---|
| 1 | `command -v attractor` succeeds | `attractor lint <dot>` — findings captured **verbatim**; the only clean verdict is `OK (no findings)`; WARNINGs (e.g. the one deliberate TOPO-006 on the loud-terminal idiom) pass but are quoted; ERRORs are a red verdict | `lint+doctrine` (with rung 3) |
| 2 | CLI absent | Ask the user **once**: *"The attractor CLI isn't installed. I can fetch and run the public linter via `uvx --from git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner attractor lint <file>` — that downloads a public package (an inbound fetch; none of your mined data leaves this machine). Yes/no?"* On yes, run it via the `--lint-cmd` override. On no, skip. **Never silent, never automatic.** | `lint+doctrine` on yes |
| 3 | always | The **vendored** stdlib doctrine checker (A0–A10) — ships inside the skill at `scripts/attractor_scout/authoring_contract.py`, a **byte-identical copy** of `examples/authoring/check_authored_pipeline.py`, pinned by a drift test (D7). Runs whether or not lint ran (second opinion / floor). `doctrine_bad` is a red verdict. | `doctrine-only` when rung 1/2 didn't run |
| 4 | the checker itself cannot execute (environmental crash) | nothing verified | `none` |

**Red verdict vs. unavailability — different fates.** A red verdict (lint ERROR or `doctrine_bad`)
after the one retry means the demo is **not published** — the artifact never carries a broken
demo. Unavailability of a rung is honestly *labeled*, and the demo publishes at the level that did
run. The three level labels are exact strings the renderer must emit and tests must pin:

- `lint+doctrine`: quote both verdicts verbatim.
- `doctrine-only`: the panel's lint line reads exactly
  `attractor lint: NOT RUN — the CLI is not installed here. Run it yourself: attractor lint <relpath>`
  (never an implied pass — the #270 doctrine: if the linter can't run, SAY SO in the artifact).
- `none`: a prominent `UNVERIFIED — no machine check ran on this pipeline` banner plus both
  commands to run.

**The self-certification panel** (deterministic template, injected verbatim machine output; the
`context/attractor-awareness.md` output contract made structural) appears in **every** demo:

1. **What a machine checked, and what it said** — the lint verdict verbatim (warnings included)
   and the doctrine report's verdict line + per-check summary.
2. **What nothing checked** — whether the prompts fit your workflow, whether the gate command is
   the right definition-of-done for your case, whether this pipeline solves the problem you
   actually have. Structure lints; judgment does not.
3. **The independent path** — the exact `pipeline-author.dot` invocation with this demo's brief as
   `--param brief=`, and the CLI install line
   (`uv tool install "git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner"`,
   held as a single constant `CLI_INSTALL_CMD` in `demo_templates.py`).

SKILL.md additionally instructs the session: when asked to vouch for a demo, answer in those three
parts — never "yes, I'm sure" (the graded-session failure attractorify Step 6 documents).

**Rationale.** Lint is the gold-standard machine verdict and must be first when reachable; the
vendored checker is the floor that makes bundle-only installs verifiable at all; and vendoring —
rather than referencing `examples/authoring/` by relative path — is what lets the skill's
**standalone** test suite (its conftest forbids cross-repo imports) exercise the gate hermetically
in CI, and keeps the skill working if it is ever installed without the full repo tree. The repo
already sanctions exactly this pattern twice: `check_authored_pipeline.py` is itself an adapted
copy of `check_child_contract.py` carrying its own agreement test, and `test_quality_protocol_guard.py`
Q-307 pins two copies of the decision matrix to each other. The `uvx` rung is consent-gated
because a skill whose headline is "nothing leaves the machine" must not initiate *any* network
activity silently — an inbound public-package fetch is not data egress (and the artifact/SKILL.md
say so in those words), but unrequested network I/O is still a trust-surface violation in this
skill's frame.

**Rejected alternatives.** *Require the CLI (hard dependency)*: kills bundle-only onboarding — the
audience this layer exists for. *Auto-`uvx` without asking*: silent network activity inside a
local-only promise. *Reference `examples/authoring/check_authored_pipeline.py` by path instead of
vendoring*: breaks the standalone-tests principle and standalone installs; the byte-pin test makes
the vendored copy cost one `cp` per upstream change instead of a drift risk. *Suppress unverified
demos entirely*: the maintainer's ladder explicitly ends in an honest "unverified" label, and an
unverified-but-labeled demo still teaches — the label itself teaches the verification doctrine.

### D4 — The teaching-content contract

**Decision — per demonstrated opportunity, in this order, with this deterministic/LLM split:**

| Section | Source |
|---|---|
| 1. Header: unit name, verdict badge, THEIR verified numbers — n distinct sessions, median tool calls, median LLM cycles, median span, error rate, `provisional`/`unproven` flags verbatim | **Deterministic** (from `ranked.json`, which `rank --strict` already re-verified against the extract) |
| 2. "What this keeps costing you by hand" — the toil restated from `leverage_detail` | **Deterministic** template |
| 3. "In your own terms" — scenario gist | **LLM slot** `scenario_gist` |
| 4. The three-question fit test applied to THEIR scenario — Q1/Q2/Q3 verdict lines from `fit_detail`/`recovery` (UNKNOWN renders as *unproven — a caveat, never a failure*, same invariant as the map) | Verdicts **deterministic**; one **LLM slot** per question (`q1_cycle_note`, `q2_gate_note`, `q3_recovery_note`) explaining the verdict in their scenario's terms |
| 5. The pipeline, walked — embedded `.dot` text + per-node notes | `.dot` text **deterministic** (escaped verbatim); **LLM slot** `pipeline_walk` = array of `{node, note}` |
| 6. Convergence math — why the loop beats the once-through run | **Deterministic** arithmetic in the template: with their median LLM-cycle count *n* (verified) as chain length and a **fixed illustrative per-step reliability of 0.9** (labeled: *"illustrative arithmetic — not a measurement of your sessions"*), render `0.9^n` once-through vs. the gated-loop retry probability with the demo's own budget wall. Computed in Python at assembly, never by the LLM |
| 7. "Why this would have helped" — one line | **LLM slot** `payoff_note` |
| 8. Entry points — `/attractorify` (design it conversationally), `examples/authoring/pipeline-author.dot` (converge it under executed gates), `examples/patterns/task-runner.dot` (the canonical skeleton), the objective layer (`examples/objective/objective-runner.dot`), and the repo-hosted capsule lane (`docs/ISSUE_PIPELINE.md`) | **Deterministic** template |
| 9. "Run it going forward" — the on-disk `.dot` path, the exact `attractor run <relpath> --cwd .` invocation with its budget params, `CLI_INSTALL_CMD`, and the `pipeline-author.dot` alternative with the brief pre-filled | **Deterministic** template |
| 10. Verification panel (D3) | **Deterministic** template + verbatim machine output |

**Once per artifact — the learn-about primer:** a fixed deterministic template section, *"What an
attractor pipeline is — in one screen"*: loop + machine-evidence gate + budget wall; the
three-question test; the honest-no doctrine; gates-outside-workers in one sentence — followed by a
plain link to <https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html>.
The repo convention is to **link the explainer, never inline it**; and a hyperlink does not breach
the self-contained rule, which forbids *fetched resources* (CSS/JS/fonts/data), not references a
reader may choose to follow. State that distinction in a comment in `render.py`.

**The count-integrity guard (the existing doctrine, extended to narrative — SCOPED to the six
teaching-prose slots):** every number in the six teaching-prose slots comes from a deterministic
slot fed by re-verified `ranked.json`. The LLM slots are validated at assembly, **fail-loud**
(exit 2, same posture as `rank --strict`):

- **Digit whitelist:** every digit-run in every narrative string must be a member of the allowed
  set = string forms of the unit's verified stats (as rendered) ∪ `{0,1,2,3,4}` (for Q1/Q2/Q3 and
  4a/4b/4c references). An invented count anywhere in the narrative kills the assembly with the
  offending token named. Prose like "about a dozen" is always legal.
- **Node-name check:** every `pipeline_walk[].node` must be a node id in the parsed `.dot`
  (parsed with the vendored checker's own `parse_dot_min` — one parser, one truth).
- **Shape checks:** all six slots present, strings, each ≤ 600 chars; `pipeline_walk` non-empty.

**Scope boundary (the claim, narrowed to what the check actually enforces).** The digit whitelist
governs the six teaching-prose slots and *only* those. Numbers written **inside the generated
`.dot`** — a budget, a `max_iterations`, a threshold, a figure in a node's `prompt=` or `label=` —
are LLM-authored and rendered verbatim, and are **not** digit-whitelisted. That is deliberate, not
an oversight: a `.dot` legitimately carries pipeline parameters, and a whitelist there would
false-positive on real ones (a `max_iterations=6` is not a fabricated count). The `.dot` gets its
own appropriate machine gate — `attractor lint` plus the authoring contract (D3) — which checks it
for *structure*, not for agreement with the verified stats. The self-certification panel's part 2
("what nothing checked") **names this surface out loud**, and a test pins that it keeps doing so,
so the claim cannot silently re-widen.

**Two named limits of the whitelist itself (documented, not closed).** The digit-run scan matches
`\d+`, which leaves two honest gaps, each a deliberate trade rather than a defect:

- **Decomposition:** a stat like `0.33` rendered as `0.33` also whitelists the bare run `33`, so a
  narrative saying "33 minutes" would pass. Closing it would mean whitelisting only the exact
  rendered string, which bans the legitimate `0.33`→`33%` kind of reference; the boundary here is
  the tokenizer's, and the check fails loud on the *shape* it can define cleanly.
- **Spelled-out numbers:** "forty-seven hours" is not a digit-run and passes. Detecting written
  numerals is NLP guesswork, and a fail-loud guard that guessed wrong would block honest prose —
  the worse failure for a trust surface whose whole point is not to cry wolf.

Both are covered by **expected-pass regression tests**, so a future claim to have closed either one
forces an honest update to this section rather than a silent behavior change.

**Rationale.** The mining half earned trust with "every count re-verified before render"; the
demo half inherits it by construction — the LLM never gets to *state* a number, only to narrate
around numbers the deterministic layer placed, and the whitelist makes that machine-checked
rather than hoped. The fixed 0.9 in the convergence math is deliberately *not* derived from their
data: no per-step success probability is measured by the extract, and deriving one would be a
fabricated statistic wearing their data's clothes — an illustrative constant, labeled as such, is
the honest version.

**Rejected alternatives.** *Let the LLM write the whole demo section*: unverifiable numbers in a
skill whose trust contract is verified numbers. *Forbid digits in narrative entirely*: forces
stilted prose and bans legitimate references like "Q2". *Derive per-step reliability from their
error rates*: measures something (tool errors) and presents it as something else (LLM step
success) — a fabricated measurement. *Put the primer text in SKILL.md*: guidance-surface toll for
text users never see at invocation time; it belongs in the template module, rendered into the
artifact.

### D5 — HTML integration and on-disk layout

**Decision.**

- `render.py` gains an optional `demos: dict | None` parameter on `render_html`/`write_report`,
  and the CLI `render` subcommand gains `--demos demos.json`. **When absent, output is
  byte-identical to today** (machine-checked: no demo marker appears; determinism test holds).
- Section placement, when demos are supplied: after the sampled simple→complex grid and before
  "Ranked opportunities", insert (i) the primer section, then (ii) one demonstration section per
  demo in `demos.json` order. The header sub-line appends `· N demonstrated` when N > 0. Existing
  sections and their order are untouched (additive-in-the-middle; existing tests assert
  substrings, not offsets).
- The renderer consumes `demos.json` as **pure data**; every string is HTML-escaped through the
  existing `_esc`; the `.dot` text is embedded in a `<pre>` block. Same-inputs ⇒ byte-identical
  output with a pinned `--generated-at`. (Generation is stochastic; **verification, assembly, and
  rendering are deterministic** — state this split in SKILL.md.)
- **On disk:** published demo files land beside the HTML at
  `<output_dir>/attractor-scout-demos/<slug>.dot` and `<slug>.md`, where `<slug>` is the unit
  name sanitized to `[A-Za-z0-9._-]+` (the `pipeline_name` charset) + `-<unit_id>` for
  uniqueness, and the directory stem is a `naming.py` constant (`DEMO_DIR_STEM = f"{SKILL_NAME}-demos"`).
  Files are copied there **only after** the ladder finishes (publish-after-gates, the
  `pipeline-author.dot` rule in miniature). Never inside any repo — same rule as the map.
- **Honest reference:** the HTML both embeds the `.dot` text (survives file moves; self-contained)
  and prints the relative path it wrote (`attractor-scout-demos/<slug>.dot`) with a relative
  `<a href>`. Assembly fails loud if the copy failed — the HTML never claims a file that does not
  exist.

**`demos.json` schema (the assembly output; the renderer's whole input for the new sections):**

```json
{
  "primer": true,
  "explainer_url": "https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html",
  "demos": [
    {
      "unit_id": "c1", "name": "...", "slug": "...",
      "dot_relpath": "attractor-scout-demos/<slug>.dot",
      "companion_relpath": "attractor-scout-demos/<slug>.md",
      "dot_text": "...verbatim...",
      "stats": {"n_sessions": 0, "med_tool_calls": 0, "med_llm_cycles": 0,
                 "med_span_s": 0, "err_rate": 0.0, "provisional": false},
      "fit": {"cycle": true, "gate": true, "recovery": "PASS|PASS-provisional|UNKNOWN",
               "verdict": "OPPORTUNITY|OPPORTUNITY(unproven)"},
      "narrative": {"scenario_gist": "...", "q1_cycle_note": "...", "q2_gate_note": "...",
                     "q3_recovery_note": "...", "pipeline_walk": [{"node": "...", "note": "..."}],
                     "payoff_note": "..."},
      "convergence_math": {"chain_len": 0, "p_step": 0.9, "once_through": 0.0,
                            "gated_loop": 0.0, "budget": 2},
      "verification": {"level": "lint+doctrine|doctrine-only|none",
                        "lint_verdict": "...verbatim or null...",
                        "lint_not_run_reason": "...or null...",
                        "doctrine_verdict": "doctrine_ok",
                        "doctrine_report": "...verbatim..."},
      "invocation": {"run_cmd": "...", "author_cmd": "...", "install_cmd": "..."},
      "generated_at": "ISO-8601"
    }
  ]
}
```

**Rationale.** One renderer, one artifact, one trust story: the map's credibility already rests on
"deterministic renderer over re-verified data", and the demo layer buys into it instead of
shipping a second artifact with weaker guarantees. Byte-identity without `--demos` makes the
change provably additive to the frozen mining half.

**Rejected alternatives.** *A second, separate demo HTML file*: two artifacts to keep
self-contained, two to leak-guard, and the teaching detaches from the data that motivates it.
*Modal-only demos*: long-form teaching in a modal fights the medium; modals stay for unit
deep-dives. *Inline the `.dot` only (no files on disk)*: kills "run it going forward" — the user
needs a file path to hand to `attractor run`. *Link files only (no embed)*: the artifact stops
being self-contained the day the folder is moved.

### D6 — skillify adjudication

**Runtime role: none.** skillify is an ecosystem skill for *authoring skills*; it does not ship in
this bundle, so a runtime dependency would break exactly the bundle-only audience this layer
serves — and nothing in the demo flow is skill-authoring-shaped (it authors a `.dot` demonstration,
not a SKILL.md). **Generation lineage: real, and acknowledged honestly** — the maintainer's intent
("using the skillify skill ... for creating educational material") describes how this teaching
layer's *own packaging* is produced: skillify-style discipline shaped the mining half's SKILL.md
and shapes this layer's step text, and that lineage is recorded here and in the PR body — not in
SKILL.md, where it would spend guidance-surface toll on a fact no user needs at invocation time.
**Future role, named not built:** if the demo-brief/authoring guidance ever proves reusable beyond
this skill, extracting it *as a skill* would be a skillify job then — deliberately deferred, per
the repo's "no primitives ahead of the evidence" resistance.

### D7 — Test/eval plan

**CI-deterministic (all stdlib, all under `skills/attractor-scout/tests/`, standalone per the
existing conftest):**

| Test | Asserts |
|---|---|
| `test_scenario6_demo_assembly.py` | Planted synthetic opportunity → `demo brief` output carries the verified stats + gate-tool evidence; assembly with the canned fixture narrative/`.dot` succeeds; **red-proofs:** an invented count in a narrative slot ⇒ exit 2 naming the token; a `pipeline_walk` node not in the `.dot` ⇒ exit 2; missing companion ⇒ exit 2; missing slot ⇒ exit 2. |
| `test_scenario7_verification_ladder.py` | Fake `attractor` shim on PATH emitting a canned verdict ⇒ `level=lint+doctrine`, verdict relayed **verbatim**; empty PATH ⇒ `level=doctrine-only` + the exact `NOT RUN` label; checker forced to crash ⇒ `level=none` + the exact `UNVERIFIED` banner; a doctrine-red fixture (gate deleted ⇒ A4 fails) ⇒ demo refuses to publish (no files copied, no demos.json entry). Gates proven red, per repo doctrine. |
| `test_scenario8_demo_render.py` | `--demos` ⇒ primer exactly once; explainer link exactly once; per-demo sections present; `.dot` embedded escaped; relpaths in HTML == files actually written; verification labels rendered exactly per level; **no-demos ⇒ zero demo markers and determinism (two renders byte-equal)**; UNVERIFIED never rendered as verified (the new honesty invariant, sibling of UNKNOWN-never-FAIL). |
| `test_vendored_doctrine_checker_pin.py` | Vendored `authoring_contract.py` is **byte-identical** to `examples/authoring/check_authored_pipeline.py` when that path exists relative to the bundle root (skip when absent — standalone install); plus every attribute name in the vendored vocabulary excerpt appears in `context/dot-reference.md` when present. |
| existing `test_no_real_data_leak.py` | Auto-covers every new shipped file (it rglobs all `.py/.md/...`) — new template text must carry no personal home paths, no emails, no identity terms; the demo fixture uses the `SYNTHETIC-` marker discipline. No changes needed; verify green. |
| existing `test_skill_doc_claims.py` | Unchanged — by design the new SKILL.md text contains **no new percentages or pinned numerics** (the arithmetic lives in `demo_templates.py`), so the `{18, 78}` percentage set and pinned claims stay valid. |

**What CI cannot test, and its substitute.** LLM generation cannot run in CI; what CI proves is
everything around it — brief assembly, validation, gates, labels, rendering — against a **canned,
doctrine-clean fixture** (`fixtures/demo_fixture.py`: a deterministic small `.dot` in the
convergence-factory shape + companion + narrative + mutation helpers).

**Guidance-surface toll (§2).** SKILL.md changes are a guidance surface. The standard scenarios
structurally cannot reach an opt-in slash-command skill — the argument already accepted for this
skill in `evals/README.md` — so the toll is discharged by the documented fallback, **stated in the
PR in those words**: a fresh-session walk-through (PR #297-era precedent, and this skill's own
Run-1/Run-2 precedent): a `context_depth=none` general-tier sub-agent follows only the amended
SKILL.md over a synthetic corpus, twice — once with a fake `attractor` shim on PATH, once without —
and must land correct artifacts with correct ladder labels both times. Fix what Run 1 surfaces;
record both runs in `evals/README.md` (evidence itself stays outside the repo, as before). A
dedicated guidance scenario remains deferred to the guidance-eval bundle's backlog (precedented).

**LIVE proof spec (on the maintainer's real mined data; artifacts land OUTSIDE the repo, in the maintainer's local evaluation artifacts).** (1) Full run end-to-end including the auto-demo; maintainer has the real CLI
⇒ `lint+doctrine`, quote the lint verdict verbatim. (2) Negative control: re-run with `attractor`
masked from PATH ⇒ `doctrine-only` labels proven on real data. (3) Red control: mutate the
generated `.dot` (delete the gate) ⇒ A4 red ⇒ publish refused. PR evidence carries **verdict lines
and structure only** — never unit names, gists, or any session-derived text (they derive from
private sessions); leak-lens review (§7) reads the whole diff plus the PR body under the outsider
brief, and the PR checklist's leak line is answered non-N/A because this ships a **new
artifact-type content class** (§2's new-public-content row: deterministic guards green **and** the
semantic read).

**Decision-matrix tier (state in the PR):** uncharted-adjacent, same argument as the scout skill
and the leak-defense amendment — onboarding/teaching content extends no observable pipeline
contract, so no `specs/EXTENSIONS.md` entry is owed; the silence is a scope boundary, argued in
those words.

### D8 — Scope guard (explicitly OUT)

- **Auto-running the generated pipeline.** The demo hands back the file and the exact invocation;
  launching is the human's explicit call (attractorify's "not an auto-runner", verbatim doctrine).
- **Any edit to `skills/attractorify/SKILL.md`** (eval-frozen baseline; its doctrine is reused by
  reference only), to `examples/authoring/*`, to `context/attractor-awareness.md`, or to the
  published explainer.
- **Auto-installing or auto-fetching the CLI.** The `uvx` rung is ask-first only; `uv tool
  install` appears only as text the user may run.
- **Demos for honest-NOs or waste findings.** Authoring a pipeline for work that failed the fit
  test would demonstrate the anti-pattern; the map's verdict + remediation is their teaching.
- **Wayfinder** — separate lane, untouched. **Team-shared tier** — stays scoped out; own-data-only
  is load-bearing.
- **No graph (Tier A/B) requirement anywhere in the demo path** — Tier C stays the floor.
- **No new guidance-eval scenario in this PR** (deferred, precedented) and **no renderer network
  resources** (the explainer link is an anchor, not a fetch).
- **No rename** — `naming.py` stays the single naming source; new constants live there.

---

## 3. SKILL.md step text sketch (builder adapts wording, keeps every bolded rule)

> **8 — Demonstrate (teach with their top opportunity).** The map shows *what* recurs; this step
> shows *the pipeline that would have converged it*. For `opportunities[0]` in
> `$WORK/ranked.json` (skip to the primer-only render if the list is empty, and skip entirely if
> the user asked for the map only):
>
> ```bash
> source "$WORK/env.sh"
> SLUG=$($CLI demo brief --ranked "$WORK/ranked.json" --unit <unit_id> --workdir "$WORK/demo")
> ```
>
> Delegate to a **fresh-context `reasoning` sub-agent** whose instruction is exactly the brief
> file — it writes `pipeline.dot`, `pipeline.md`, and `narrative.json` into `$WORK/demo/$SLUG/`.
> **Cost: one delegation; at most two if the gates reject the first draft; never more.** Then
> gate and assemble:
>
> ```bash
> $CLI demo assemble --ranked "$WORK/ranked.json" --unit <unit_id> \
>     --workdir "$WORK/demo/$SLUG" --output-dir "$(dirname "$OUTPUT_PATH")" \
>     --out "$WORK/demos.json" --append
> ```
>
> `assemble` runs the verification ladder (`attractor lint` if on PATH; the bundled doctrine
> checker always), validates every narrative number against the re-verified ranking (**an
> invented count is FATAL, same as step 5**), and publishes the `.dot` + companion beside the
> HTML **only after the gates finish**. If `attractor` is missing it will tell you; you may then
> ask the user ONCE whether to fetch the public linter via `uvx` — **an inbound package fetch;
> none of their mined data leaves the machine; never run it without their yes** (on yes, re-run
> assemble with `--lint-cmd "uvx --from git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner attractor"`).
> If the gates reject the draft, re-delegate ONCE with the gate reports appended verbatim; if it
> is still red, do not publish — say so and move on. Then re-render:
>
> ```bash
> $CLI render --ranked "$WORK/ranked.json" --demos "$WORK/demos.json" --out "$OUTPUT_PATH"
> ```
>
> **What you authored (via your delegate), you cannot certify.** The artifact carries the machine
> verdicts verbatim, says which checks did NOT run, and offers the independent path
> (`examples/authoring/pipeline-author.dot` + the CLI install line). If the user asks you to
> vouch for the demo, answer in those three parts — never "yes, I'm sure."
>
> **9 — Offer more (their call).** List the top 5 not-yet-demonstrated opportunities by name and
> ask one question: demonstrate another? Each yes repeats step 8 for that unit (`--append`) and
> re-renders. Never generate a second demo without a fresh explicit yes.

Hard-rules section gains three bullets: the self-certification rule (above, one line), *never
auto-fetch — ask before any `uvx`, a "no" is fine and the doctrine checker is the floor*, and
*generated `.dot`/`.md` land beside the HTML, never in a repo*. The Output section adds: "plus, when
demonstrated, `attractor-scout-demos/<slug>.dot` + `.md` beside it." No new numeric claims anywhere
in SKILL.md.

---

## 4. File-by-file build plan

**New files (all under `skills/attractor-scout/` unless noted):**

| File | Contents |
|---|---|
| `scripts/attractor_scout/demo.py` | Brief assembly (evidence digest incl. cluster gate-tool census from members; slug derivation; node budget constant `DEMO_MAX_NODES = 9`; attempt budget `DEMO_MAX_ATTEMPTS = 2`); narrative validation (digit whitelist, node-name check via the vendored parser, shape/length checks); verification-ladder driver (`lint` discovery, `--lint-cmd` override, vendored checker invocation, level resolution, verbatim capture); convergence-math computation (`p_step = 0.9` constant, labeled); publish-after-gates copy; `demos.json` read/write/append. Fail-loud via `AttractorScoutError` (exit 2 path). |
| `scripts/attractor_scout/demo_templates.py` | All deterministic template text: primer (+ `EXPLAINER_URL`), section skeletons, verification-panel strings (the three exact level labels as constants: `LABEL_LINT_NOT_RUN`, `LABEL_UNVERIFIED`), entry-points, run-forward, `CLI_INSTALL_CMD`, the vocabulary excerpt used in briefs. No LLM anywhere. |
| `scripts/attractor_scout/authoring_contract.py` | **Byte-identical vendored copy** of `examples/authoring/check_authored_pipeline.py` (stdlib, self-contained; importable for `parse_dot_min` and `run_checks`, runnable as a script). |
| `fixtures/demo_fixture.py` | Deterministic doctrine-clean demo `.dot` (convergence-factory miniature, synthetic names only) + companion + narrative builders + mutation helpers (`without_gate()`, `with_invented_count()`, `with_unknown_node()`). |
| `tests/test_scenario6_demo_assembly.py` | Per D7. |
| `tests/test_scenario7_verification_ladder.py` | Per D7 (shim-on-PATH via tmp dir + `PATH` env). |
| `tests/test_scenario8_demo_render.py` | Per D7. |
| `tests/test_vendored_doctrine_checker_pin.py` | Byte-pin + vocab-excerpt pin (skip when repo files absent). |

**Changed files:**

| File | Change |
|---|---|
| `scripts/attractor_scout_cli.py` | New `demo` subcommand with `brief` and `assemble` actions (flags per §3; `assemble` accepts `--lint-cmd`, `--append`, `--output-dir`); `render` gains `--demos`. Exit-code contract unchanged (0 ok / 2 fail-loud). |
| `scripts/attractor_scout/render.py` | `render_html(result, *, tier_note="", generated_at=None, demos=None)` + `write_report(..., demos=None)`; primer + demo section emitters (import templates from `demo_templates.py`); sub-line `· N demonstrated`; comment stating the link-vs-fetch self-contained distinction. **Guarantee: `demos=None` ⇒ byte-identical output.** |
| `scripts/attractor_scout/naming.py` | Add `DEMO_DIR_STEM = f"{SKILL_NAME}-demos"` (single naming source preserved). |
| `SKILL.md` | Steps 8–9, three hard-rule bullets, Output line, description sentence for the demo half. **No new percentages/numeric claims.** |
| `evals/README.md` | New section recording the demonstration-layer walk-through fallback (both runs, summarized; evidence outside the repo) and the deferred dedicated scenario. |

**No changes** to: `skills/attractorify/SKILL.md`, `examples/**`, `context/**`, `docs/**`,
`fixtures/synthetic_corpus.py`, existing tests, `ranking.py`, extract/discover/cluster modules.

---

## 5. Settled — no open questions

Every question raised in the ask is settled above with rationale and a rejected alternative
(D1–D8). Points where this design deliberately exercised judgment beyond the framing: the auto-demo
runs *after* the first render (not as a gate to it); demo #1 is not consent-gated but every
subsequent one is; the `uvx` fetch is included but strictly ask-first; unverified demos publish
with the honest label while gate-*failed* demos never publish; and skillify's lineage is recorded
outside SKILL.md to keep the guidance surface lean.

---

## 6. Build notes — what changed on contact with the build

The design above is preserved as written. These are the deviations and tooling facts the build
produced, recorded honestly rather than folded back into the text as if they had been designed.

**Deviations from the file-by-file plan (each with its one-line justification):**

- **`SKILL.md` frontmatter left untouched.** §4 wanted a demo-half `description:` sentence; the
  build kept the frontmatter exactly as PR #300 had just fixed it (`model_role: reasoning`,
  `version: "1.0.0"`). Demonstration is announced in the body, which is what D1's consent argument
  actually needs — the ambient one-liner does not.
- **`demo brief` gained an optional `--extracts` flag** (not in §3's sketch). D2 requires the
  gate-tool census to be "computed from the extract", but §3's command omitted the flag that would
  carry it. The optional flag satisfies both; SKILL.md's step 8 passes it. Absent the flag, the
  brief says plainly that no gate evidence was available rather than inventing one.

**Tooling change — `skills/attractor-scout/ruff.toml` (new file).** The build added a ruff config to
the skill with two settings, and both are disclosed here so the diff carries no silent surprise:

- `line-length = 120`. This matches the style the whole skill was already written in. Without it,
  ruff's default (88) reports the *pre-existing* tree as dirty: it turns **9 lint errors into 6**
  (quieting three `I001` unsorted-import findings that only trip at the shorter width) and would
  reformat **27 files** that were never at 88 columns. Pinning 120 is therefore not a behavior
  change to this layer's code — it is making the linter agree with the code that already shipped,
  so `ruff check`/`format --check` are a real signal on the new files instead of drowning in
  reformat noise on old ones. The remaining 6 errors are all in design-frozen or out-of-scope files
  (`evals/`, `fixtures/synthetic_corpus.py`, `__init__.py`) and are left untouched.
- `extend-exclude = ["scripts/attractor_scout/authoring_contract.py"]`. The vendored doctrine
  checker is a byte-identical copy pinned by a sha256 test; ruff formatting it here would fork it
  from its upstream home and break the pin. It is linted/formatted only upstream, which is the only
  place a change to it belongs.

**Scope narrowing after adversarial review (Finding 1).** The "every number is deterministic,
never LLM-emitted" claim was over-broad: the digit whitelist governs the six teaching-prose slots
only, and numbers *inside* the generated `.dot` (budgets, `max_iterations`, thresholds) are
LLM-authored and gate-checked by lint+doctrine, not whitelisted — because a `.dot` legitimately
carries pipeline parameters and a whitelist there would false-positive on real ones. The claim was
narrowed to match everywhere it appears (D4, SKILL.md, `demo_templates`, this doc), the
self-certification panel's part 2 now names that surface out loud, and a test pins that it keeps
doing so. The whitelist's two named limits (decomposition, spelled-out numerals) are documented at
the check and in D4, with expected-pass regression tests so a future "closed it" is forced to
update the docs honestly.
