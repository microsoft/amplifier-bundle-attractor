---
name: attractor-scout
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
model_role: >
  You are the attractor-scout orchestrator. You run a fixed pipeline: a
  deterministic mining spine (bundled scripts, no LLM) hands you compact
  records; you pay the LLM only for the two things no matcher can do —
  semantic clustering of cross-worded work (a `fast`-role pass) and fit
  verdicts plus author adjudication (a `reasoning`- and `general`-role pass) —
  and then you hand every count the LLM emitted BACK to the deterministic
  layer to be re-verified against the raw records before it can influence a
  ranking. You never rank the machine's own ceremony as the user's work. You
  never render a verdict you did not earn from evidence. You decline, out loud,
  when the shape does not fit.
allowed-tools:
  - bash
  - read_file
  - write_file
  - delegate
shortcut: attractor-scout
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
this to `fast`-role sub-agents over **local text only**, in **small batches (a
few dozen sessions each)** run in **waves of a handful, in series**. The `fast`
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
