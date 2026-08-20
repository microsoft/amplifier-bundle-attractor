---
name: attractor-scout
version: "1.0.0"
description: >
  Mine YOUR OWN local context-intelligence session history to find the
  attractor-shaped opportunities hiding in your real recurring work — the
  units you do again and again, that cost real effort, and that would survive
  being handed to a loop. Surfaces them ranked, with honest-NOs as first-class
  output, and writes a self-contained HTML opportunity map. Own data only;
  nothing leaves the machine. Triggers: "/attractor-scout", "what should I
  automate?", "find my attractor opportunities", "scout my sessions", "what do
  I keep doing by hand?", "mine my own work for pipelines".
user-invocable: true
model_role: reasoning
allowed-tools:
  - bash
  - read_file
  - write_file
  - delegate
---

# /attractor-scout — mine your own work for the loops worth building

This skill runs **inline** in the current session so it can read the repo's
`AGENTS.md`, resolve an output path, and shell out to the bundled mining
scripts. It reads **your own** context-intelligence session history — the
record of what you actually did, session after session — and finds the
recurring units of work that are shaped like an attractor: a loop, gated on
evidence, that would survive a bad day. Then it hands you a ranked map, and it
is just as willing to tell you which of your habits are **not** worth
automating and why.

> **What "attractor-shaped" means — the three-question test, in your terms.**
> The same test `/attractorify` applies to one piece of work, this skill
> applies to every recurring unit in your history:
>
> - **Q1 — Is there a cycle?** Does the work actually iterate (try, check,
>   fix, repeat), or does it run once straight through? Once-through is a
>   *recipe*, not an attractor.
> - **Q2 — Is the exit gated on evidence?** Does it stop on a machine-checkable
>   condition — a test, a lint, a build, a readback — or does it stop when the
>   model feels done? An ungated loop is a *one-shot*: one gate away from
>   converting.
> - **Q3 — Would it survive one node having a bad day?** When a step errored,
>   did the work recover and still finish? If errors were never even observed,
>   that is *unproven* — a caveat, never a failure.
>
> A unit that clears all three, recurs at least twice, and cost you real toil
> is an **opportunity**. Everything else that recurs and cost toil is an
> **honest-NO**, reported *with the sub-test it failed and what would change
> the answer*. The classification is the value.

---

## Setup — do this first (defines every variable the commands use)

The pipeline shells out to the bundled scripts and writes intermediate JSON to
a scratch directory. Establish these three shell variables **before running any
command below** — every command references them, and none of them may be left
undefined:

```bash
# 1. The skill directory. When this skill was loaded via load_skill, it reports
#    a skill_directory; use that. Otherwise SKILL_DIR is the directory that
#    contains this SKILL.md (the folder holding scripts/). Set it explicitly to
#    an absolute path:
SKILL_DIR="/absolute/path/to/skills/attractor-scout"   # from load_skill's skill_directory
CLI="python $SKILL_DIR/scripts/attractor_scout_cli.py"

# 2. A scratch directory for intermediate JSON. Kept OUT of any repo — the
#    user's mined data must never land in version control.
WORK="$(mktemp -d -t attractor-scout.XXXXXX)"

# 3. Where the final HTML map is written. Default: the current working
#    directory. If this repo's AGENTS.md declares an artifacts/output path,
#    resolve it and set OUTPUT_PATH there instead.
OUTPUT_PATH="$PWD/attractor-scout-report.html"

# 4. Persist these for the whole run, because each shell command below may run
#    in a fresh shell that does not inherit them:
cat > "$WORK/env.sh" <<EOF
SKILL_DIR="$SKILL_DIR"; CLI="$CLI"; WORK="$WORK"; OUTPUT_PATH="$OUTPUT_PATH"
EOF
# ...then start every later command with:  source "$WORK/env.sh"
```

If you cannot resolve `SKILL_DIR` (no `skill_directory` from load_skill and you
are not inside the skill's own tree), STOP and say so — do not guess a path.
`$WORK` lives under `mktemp`, so its path is stable once created; re-`source`
`$WORK/env.sh` at the top of each step rather than re-running `mktemp`.

---

## The pipeline (fixed order; do not improvise the stages)

The logic lives once, in the bundled library (`scripts/attractor_scout/`); the
CLI (`scripts/attractor_scout_cli.py`, invoked here as `$CLI`) is the
workhorse. You orchestrate — you do not re-implement a detector.

**1 — Deterministic mining spine (NO LLM).** Discover → qualify → extract, all
in the bundled scripts:

```bash
source "$WORK/env.sh"
# Mines your own default tree (~/.amplifier/projects). To point at a different
# OWN-DATA location, pass --root as a TOP-LEVEL flag BEFORE the subcommand:
#   $CLI --root /path/to/your/projects extract --out "$WORK/extracts.jsonl"
$CLI extract --out "$WORK/extracts.jsonl"
```

This resolves the context-intelligence root
(`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` → `~/.amplifier/projects`),
version-checks every `metadata.json` and **fails loud on a schema mismatch**,
selects **prompt-carrying root sessions** (never top-N-by-workspace-size — the
biggest recurring unit lives smeared across dozens of tiny workspaces and is
invisible to size-ranked selection), reads errors from `tool:post.result.error`,
folds children into their root via `parent_id`, keys on **full** session ids,
and caps span at 7,200 s. It **fails loud on an empty root** with the exact
message `looked in <root>, found 0` — if you see that, STOP and report it; do
not fabricate a count from a shallower glob.

**2 — Semantic label + cluster (`fast` role).** The deterministic spine dedups
by tool-signature, but the large majority of real opportunities are found ONLY
by reading the work's *meaning* across differently-worded sessions. Delegate
this to `fast`-role sub-agents over **local text only**, in **batches of ~40
sessions**, run in **waves of 5 batches in series**. Those two numbers are the
calibrated shape: ~40 keeps a batch inside one workspace's coherence so a
cluster is not split across unrelated work, and 5-at-a-time in series stays
under provider rate limits on a corpus of any size. The `fast`
role does this reliably at scale — every batch measured in this build placed
every session with **zero invented ids**. Each batch returns cluster
assignments as JSON; collect them into an intermediate `$WORK/fast-clusters.json`
(the verdict-carrying `$WORK/clusters.json` is assembled in step 4).

**3 — Merge + fit verdicts (`reasoning` role).** Merge the per-batch clusters
(staged: batch → regional → global) and assign each cluster its **fit verdict**
with a `reasoning`-role sub-agent. This tier is not optional and not cosmetic:
the verdict-tier A/B (Gate 1, evidence in `evals/README.md`) measured a **~18%
verdict-flip rate** between the fast and reasoning tiers, **78%** of them the
reasoning tier upgrading a verdict the fast tier mechanically declined. Fast
labels; reasoning judges.

**4 — Author adjudication (`general` role).** The deterministic spine already
carries an author *prior* (harness / human / mixed) from fingerprints,
sentinels, and machine-launch metadata. That prior **over-calls human**,
because it cannot read intent from prompt text — a templated autonomous "lane"
mission looks human to it but is machine-launched. So adjudicate author at the
**cluster level, reading the prompt text**, with a `general`-role sub-agent
(the author-gate A/B, Gate 2 in `evals/README.md`, built this step: it admitted
**0 of 2** harness clusters in **10/10** trials). The adjudicated label
overrides the prior.

After steps 2–4, write `$WORK/clusters.json` in exactly this shape — one entry
per global cluster, carrying the fast-tier members and the reasoning/general
verdicts. `members` are session ids from the extract; `cycle` and
`evidence_gate` are the reasoning-tier fit booleans; `author` is the
general-tier adjudicated label (`human` | `mixed` | `harness`):

```json
{"clusters": [
  {"id": "c1", "name": "short label", "members": ["<sid>", "<sid>"],
   "cycle": true, "evidence_gate": true, "author": "human"}
]}
```

**5 — Deterministic re-verification (trust, then verify — a FATAL gate).**
Every count the LLM emitted — cluster membership above all — is handed BACK to
the deterministic layer and re-checked against the extract. This runs as part
of ranking, with `--strict`:

```bash
$CLI rank --strict \
    --extracts "$WORK/extracts.jsonl" \
    --clusters "$WORK/clusters.json" \
    --out "$WORK/ranked.json"
```

`--strict` makes a re-verification mismatch **FATAL**: if any member id the LLM
emitted does not resolve against the extract, the command prints the offending
ids and **exits non-zero**. If that happens, **STOP** — do not render, do not
report a ranking. A ranking that rests on counts you could not verify is worse
than no ranking. Re-run the label/cluster pass or fix the cluster JSON first.

**6 — Rank (inside the same `rank` call).** `score = n_sessions × leverage ×
fit / 100`, where `leverage = med_tool_calls + med_llm_cycles +
med_span_capped/60 + 2·errs/n` (median within cluster, never p75; `n_prompts`
carries zero signal and is dropped). Fit is binary {0,1}. **The author
admission gate runs BEFORE scoring: only human/mixed units are ranked**; harness
ceremony is routed to a separate waste-findings channel — reported (it is time
you could reclaim), not offered as an opportunity to act on.

**7 — Render (NO LLM).** The bundled deterministic renderer turns the ranked
JSON into ONE self-contained HTML file — a sampled simple→complex range across
the top, in-page modal deep-dives into the full list, honest-NOs shown with
verdict and remediation, waste-findings in their own channel:

```bash
$CLI render --ranked "$WORK/ranked.json" --out "$OUTPUT_PATH"
```

**8 — Demonstrate (teach with their top opportunity).** The map shows *what*
recurs; this step shows *the pipeline that would have converged it*. Run it for
`opportunities[0]` in `$WORK/ranked.json` — the ranking already made the pick, so
do not open with a menu. Skip this step entirely if the user asked for the map
only. If `opportunities` is empty there is no subject to demonstrate: write the
primer-only document and re-render, then say so plainly.

```bash
source "$WORK/env.sh"
# No opportunities? Primer only, and skip the rest of this step:
#   $CLI demo primer-only --out "$WORK/demos.json"
# Pin the unit ONCE, and pass it to every demo command in this step:
UNIT=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["opportunities"][0]["unit_id"])' \
        "$WORK/ranked.json")
SLUG=$($CLI demo brief --ranked "$WORK/ranked.json" --unit "$UNIT" \
        --extracts "$WORK/extracts.jsonl" --workdir "$WORK/demo")
```

`demo brief` writes `$WORK/demo/$SLUG/brief.md` — deterministically assembled
from their verified stats, their fit detail, the verify-class tools actually
seen in their own sessions' terminal windows, the A0–A10 authoring contract, and
the engine's attribute vocabulary. Delegate to a **fresh-context `reasoning`
sub-agent** whose instruction is exactly that file; it writes `pipeline.dot`,
`pipeline.md` and `narrative.json` into `$WORK/demo/$SLUG/`. **Cost: one
delegation; at most two if the gates reject the first draft; never more.**
Then gate, validate and publish:

```bash
$CLI demo assemble --ranked "$WORK/ranked.json" --unit "$UNIT" \
    --workdir "$WORK/demo/$SLUG" --output-dir "$(dirname "$OUTPUT_PATH")" \
    --out "$WORK/demos.json" --append
```

**`brief` and `assemble` must both point at the SAME unit.** `--unit` defaults
to `opportunities[0]` on *each* command independently, so an `assemble` that
omits it silently validates the draft against the top-ranked unit's numbers —
which is why `$UNIT` is pinned once above and passed to both. Mismatch it and
the count check fails on numbers the delegate never wrote.

`assemble` runs the verification ladder (`attractor lint` if it is on PATH; the
bundled doctrine checker always), validates every number in the **six teaching-
prose slots** against the re-verified ranking — **an invented count there is
FATAL, same as step 5** — and copies the `.dot` + companion beside the HTML
**only after the gates finish**. (Numbers written *inside* the generated `.dot`
— budgets, `max_iterations`, thresholds — are gate-checked by lint+doctrine, not
digit-whitelisted: a pipeline legitimately carries parameters, and the panel's
"what nothing checked" names that surface out loud.)
If `attractor` is missing it will say so in the artifact rather than imply a
pass. You may then ask the user ONCE whether to fetch the public linter via
`uvx` — **an inbound package fetch; none of their mined data leaves the machine;
never run it without their yes**. On yes, re-run assemble with
`--lint-cmd "uvx --from git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/pipeline-runner attractor"`.
If the gates reject the draft it exits non-zero and leaves the verbatim reports
at `$WORK/demo/$SLUG/gate-report.txt`: re-delegate **ONCE** with those reports
appended, and if it is still red, do not publish — say so and move on. Then
re-render the same file:

```bash
$CLI render --ranked "$WORK/ranked.json" --demos "$WORK/demos.json" --out "$OUTPUT_PATH"
```

**Generation is stochastic; verification, assembly and rendering are not.**
And: **what you authored — via your delegate — you cannot certify.** The
artifact carries the machine verdicts verbatim, says which checks did NOT run,
and offers the independent path (`examples/authoring/pipeline-author.dot` plus
the CLI install line). If the user asks you to vouch for the demo, answer in
those three parts — never "yes, I'm sure."

**9 — Offer more (their call).** List the top five not-yet-demonstrated
opportunities by name and ask exactly one question: *"Want another one
demonstrated? Name or number — or no."* Each yes repeats step 8 for that unit —
re-point `$UNIT` at the chosen `unit_id` so **both** `demo brief` and `demo
assemble` carry the same `--unit`, keep `--append`, and re-render. **Never
generate a second demonstration without a fresh explicit yes** — the first one
is the skill's second half; every one after it is marginal spend for marginal
personalization.

**10 — Deck mode (OPT-IN; ask once, and only after step 9).** Steps 7–9 produce
a deterministic report: a renderer places every number, so no model can invent
one. Deck mode produces something different — ONE authored, deck-grade,
personalized page over the same verified data — and pays for that freedom with
machine gates instead of with a deterministic renderer. Offer it in one
question, naming the cost out loud:

> *"I can also build a deck-grade version of this — one self-contained page
> that teaches the ideas and walks your own results, authored rather than
> templated. It costs one more reasoning-model delegation (two if the gates
> reject the first draft), and it only publishes if it passes them. Want it?"*

**Never build a deck without an explicit yes.** On yes:

```bash
source "$WORK/env.sh"
RUNDIR="$(dirname "$OUTPUT_PATH")"
BRIEF=$($CLI deck brief --ranked "$WORK/ranked.json" --demos "$WORK/demos.json" \
        --workdir "$WORK/deck")
```

`deck brief` writes `$WORK/deck/deck-brief.md` — deterministically assembled
from the same verified data the report rests on: the ranking, the honest-NOs,
the waste channel, every generated `.dot` verbatim with its gate verdicts, the
house style/technique contract, the hard self-containment constraints, and the
four MANDATES that make the gates passable. Delegate to a **fresh-context
`reasoning` sub-agent** whose instruction is exactly that file; it writes ONE
file, `deck.html`, into `$WORK/deck/`. **Cost: one delegation; at most two if
the gates reject the first draft; never more.**

**Drive that delegation across resumed turns, not one giant request.** A deck is
tens of thousands of tokens of markup, and a single request that tries to emit
the whole file in one response reliably exceeds the provider's ~600 s request
timeout and loses the work. Have the fresh-context author build the file up in
several turns — the document head/stylesheet/`<defs>` first, then one section
per turn, then the dialogs and the closing script — resuming the same session
each turn so it is still ONE fresh context. The brief tells the author this too;
it is stated here because the orchestrating session is what sequences the turns.

Then gate it:

```bash
$CLI deck verify --deck "$WORK/deck/deck.html" \
    --ranked "$WORK/ranked.json" --demos "$WORK/demos.json" \
    --report "$WORK/deck/deck-gate-report.txt"
```

`deck verify` is deterministic and it is the only thing that decides whether the
deck publishes. Five gates: **(a)** the HTML parses; **(b)** the page is
self-contained — no `<img>`/`<link>`/`<script src>`/`@import`/`<iframe>`/
`srcset`, `url(` only as `url(#…)`, exactly two https links, zero `file://`;
**(c)** every modal has a trigger and every trigger has a modal; **(d)** every
number displayed in visible text re-verifies against the run data or against a
derivation the deck itself declares, with provenance, in its
`<script type="application/json" id="derived-values">` block — **an undeclared
number is FATAL, same as step 5**; **(e)** every pipeline diagram matches its
real `.dot` node-for-node and edge-for-edge (an edge MULTISET comparison, so a
back-edge quietly not drawn is caught). Exit 0 means all five passed; **exit 3
means a gate came back red and the deck must NOT be published**.

If it exits 3, re-delegate **ONCE** with `$WORK/deck/deck-gate-report.txt`
appended verbatim, then re-verify. Still red? **Do not publish.** Say which
gates failed and move on — the report and the demonstrations are already
complete artifacts. On green, publish it beside the report:

```bash
cp "$WORK/deck/deck.html" "$RUNDIR/attractor-scout-deck.html"
```

**Optional vision rung — only AFTER `deck verify` passes.** If a
vision-analysis tool is available in this session, render the published deck to
images, stitch them, and inspect for layout defects (overlap, clipping,
unreadable contrast, a diagram running off its viewBox). At most **one** fix
round: hand the defects back to the same fresh-context author, re-run
`deck verify` (a visual fix must not break a gate), and republish. If no such
tool is available, do **not** imply it was checked — leave the honest label in
the deck's footer comment:

```
<!-- vision QA: NOT RUN -->
```

Same ladder philosophy as the demo lint rung: a rung that could not run is
*labelled*, never silently treated as a pass.

---

## Hard rules (non-negotiable — these are the trust contract)

- **Own data only. Nothing leaves the machine.** The label/cluster/verdict/
  author passes reason over **local text**; there is no network egress. Skip
  any write-only or read-blocked endpoint; sessions whose metadata explicitly
  labels them as originating from another source are out of scope. The
  top-level `--root` lever (it precedes the subcommand: `$CLI --root <path>
  extract ...`) exists only to point at *your own* context-intelligence tree —
  **never point it at another user's corpus or a shared mount**; this skill
  mines the caller's own history, nobody else's.
- **Tier C (local JSONL) is the floor, and it is fully sufficient.** Every
  signal expresses at Tier C — this is the proven path. A personal graph
  (Tier A/B) is an *optional sharpener* for counts and joins; it is **never a
  precondition**. If no graph answers, run at Tier C and say so with an honest
  one-line note. Never let the graph become required for a rung to appear.
- **Honest-NOs are first-class output**, never dropped, never padded into the
  opportunity list to lengthen it. Each carries its verdict, the sub-test it
  failed (`recipe` / `one-shot` / `fragile`), and its remediation.
- **Provisional flags surface in the artifact verbatim.** A unit seen in only
  2–3 sessions is admitted but flagged `provisional`. A `PASS-provisional`
  recovery is labelled as such. And **UNKNOWN never renders as FAIL** — a unit
  whose resilience was never stress-tested is `OPPORTUNITY(unproven)`, a
  caveat on an opportunity, not a decline. Most sessions never hit an error at
  all; "no bad day observed" is not "would not survive a bad day."
- **Fail loud on an empty root** — exact string `looked in <root>, found 0`,
  non-zero exit. Never invent a count.
- **What you authored, you cannot certify.** A demonstration your delegate
  wrote is not something you may vouch for. Asked whether it is right, answer
  in three parts and no fourth: what a machine checked and what it said
  (verbatim), what nothing checked (whether the prompts fit their workflow,
  whether the gate is the right definition-of-done, whether it solves the
  problem they actually have), and the independent path
  (`examples/authoring/pipeline-author.dot`, plus the CLI install line). Never
  "yes, I'm sure."
- **Never auto-fetch. Ask before any `uvx`.** The bundled doctrine checker is
  the floor and it always runs, so a "no" costs nothing but a label. An inbound
  public-package fetch is not data egress — say that plainly — but unrequested
  network activity inside a local-only promise is still a trust violation. And
  never run `uv tool install`: that line is text the user may choose to run.
- **Generated `.dot`/`.md` land beside the HTML, never in a repo** — under
  `attractor-scout-demos/` next to `$OUTPUT_PATH`, and only *after* the gates
  finish. A demo whose gates came back red is not published at all; an
  unverified-but-labelled demo is. **The deck lands there too**, as
  `attractor-scout-deck.html` — never in a repo, and never before
  `deck verify` exits 0.
- **Deck mode is opt-in, and a gate-failed deck never publishes.** Ask once,
  name the cost, and build nothing without a yes. The deck is *authored*, so
  it does not inherit the renderer's structural guarantee — it earns trust from
  `deck verify` instead, and a red gate after the one retry means the deck is
  discarded, not shipped with a caveat. The report and the demonstrations are
  already complete artifacts; a deck is a bonus, never a hostage.
- **Every number a deck displays is re-verified or declared.** The deck may do
  arithmetic the run data does not contain — a total, a percentage, a unit
  conversion — but it must declare each one, with its provenance, in its own
  `derived-values` block. An undeclared number is FATAL. And where the run has
  no value for something, the deck says so out loud; it never fills the hole.
- **The artifact is not the success test.** A pretty HTML file proves nothing;
  the ranking is only trustworthy because every count in it was re-verified
  against the raw records (step 5, `--strict`). Ship the map, but the map earns
  trust from that gate.

---

## Output

One **self-contained HTML** opportunity map (inlined CSS/JS, no network
references), written to `$OUTPUT_PATH`. That is the current working directory
by default, or an **`AGENTS.md`-guided output path** if the repo declares one.
Never write the user's mined data into a shared repo — their session history
belongs in their own artifact.

Plus, when demonstrated: `attractor-scout-demos/<slug>.dot` + `.md` beside it.
The HTML embeds the pipeline text as well as naming that path, so the artifact
stays self-contained if the folder ever moves. The one hyperlink it carries is
the published explainer — an anchor a reader may follow, not a resource the
page loads.

Plus, when deck mode was accepted AND every gate passed:
`attractor-scout-deck.html` beside them — one authored, self-contained,
deck-grade page over the same verified data, carrying exactly two outbound
anchors (the explainer and the bundle repository) and loading nothing. A deck
that failed `deck verify` is never written there at all.

---

## Notes

- The working name `attractor-scout` lives in **one place**,
  `scripts/attractor_scout/naming.py` (`SKILL_NAME`). Renaming the skill means
  changing that constant, this directory, and the `name:` above — nothing else
  hardcodes the string.
- Every quantitative claim above ("~18% flip", "78% upgrades", "0 of 2 in
  10/10") is pinned to the in-repo evidence file `evals/README.md`, and a
  doc-guard test (`tests/test_skill_doc_claims.py`) fails if a pinned number in
  this file drifts from that source. The deterministic acceptance gates are in
  `tests/` and run standalone with `pytest tests`.
