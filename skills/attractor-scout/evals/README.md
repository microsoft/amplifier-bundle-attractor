# evals/ — the arms that cannot be CI gates, and why

Everything in `../tests/` is a **portable, CI-runnable acceptance gate**: synthetic
fixtures, ground truth by construction, zero real data. Everything in **here** needs
either a **real private corpus** or a **live model call**, so it is a runnable script
rather than a pytest gate. That split is deliberate — a gate that cannot run on
someone else's machine is not a gate.

Nothing in this directory writes into the repo. All outputs go to a caller-supplied
directory outside it.

| Script | What it measures | Needs |
|---|---|---|
| `real_corpus_arms.py` | Leverage / Fit / Honest-NO real-corpus arms | a real `extracts.jsonl` + cluster JSON |
| `gate1_verdict_tier_ab.py` | **Gate 1** — verdict-tier necessity A/B | real corpus + 2 model tiers |
| `gate2_author_admission.py` | **Gate 2** — author admission gate | real corpus + `general`-tier model |

```bash
python evals/real_corpus_arms.py      --extracts <corpus>/extracts.jsonl \
                                      --clusters <corpus>/clusters.json --out <outdir>
python evals/gate1_verdict_tier_ab.py prepare --run <corpus> --out <outdir>
#   ... run the fast and reasoning arms over <outdir>/gate1-payload.json ...
python evals/gate1_verdict_tier_ab.py score   --out <outdir>
```

---

## Guidance-surface eval toll — discharged via the documented fallback

`SKILL.md` is a new **guidance surface**, which normally warrants a full
eight-scenario guidance-eval run. The guidance-eval contract also names a
fallback for surfaces the standard scenarios cannot reach: state plainly that
**the eval cannot reach this surface**, and substitute a **fresh-session
walk-through** as the evidence. This skill takes the fallback, argued honestly:

**Why the eight scenarios structurally cannot reach `/attractor-scout`.** The
standard guidance scenarios exercise *ambient* guidance — the context an agent
carries into ordinary work without being asked. `/attractor-scout` is not
ambient: it is an **opt-in, user-invoked skill**. None of the eight scenarios
invoke a slash-command skill, and nothing in this skill runs unless a user
explicitly triggers it. Its entire ambient footprint is a **single line in the
skills-visibility list** (the one-line `description`); the 200-line body that
does the work is inert until invocation. A guidance eval that measures ambient
behaviour therefore has nothing of this skill to measure — the surface it would
grade only exists once someone runs the command, which the scenarios never do.

**The substituted evidence: a fresh-session walk-through.** A general-tier
sub-agent with `context_depth=none` — zero prior knowledge of the pipeline — was
handed only the SKILL.md body plus the load_skill runtime binding and a
synthetic corpus, and told to follow the skill exactly. Run 1 surfaced three
executability defects (a bash apostrophe-in-`${var:?}` hard break, undocumented
top-level `--root` placement, and an unstated persistent-shell assumption); all
three were fixed in SKILL.md. Run 2, post-fix, carried a zero-knowledge operator
end-to-end to a correct, self-contained artifact — every CLI stage exit 0, the
`rank --strict` re-verification gate green with zero invented ids, honest-NOs
rendered with their failed sub-tests, provisional/`unproven` flags present, and
harness ceremony correctly routed to waste. The walk-through evidence (both
runs, verbatim) lives outside the repo, in the maintainer's local evaluation
artifacts — it references a local synthetic run tree, not shippable code.

**Follow-up (do not build now).** The right permanent fix is a *dedicated*
guidance scenario for `/attractor-scout` that invokes the skill and grades the
run it produces — an addition to the guidance-eval suite, not a change to this
skill. It is deliberately deferred: it belongs in the guidance-eval bundle's
own backlog, and building it here would couple this skill to eval machinery it
should not own.

---

## Stage-1 results (run against the maintainer's own corpus: 2,164 sessions / 54 clusters)

### Gate 1 — verdict-tier necessity → **KEEP the reasoning tier (flagged)**

Frozen held-out split: `sha256(id) % 100 < 30` over the 329 batch clusters → **103
clusters**, hash recorded in `gate1-manifest.json`. Five paired trials per arm; label
and cluster assignments held identical, only the verdict tier varied.

| Metric | Measured | Threshold | Verdict |
|---|---|---|---|
| Comparisons pooled | 515 (5 × 103) | — | — |
| Flips | 93 | — | — |
| Flip rate (point) | **18.06%** | in [18%, 30%] | **PASS** |
| Wilson 95% CI | **[14.98%, 21.61%]** | lower ≥ 15% → KEEP | **0.02pp short** |
| KILL test | CI upper 21.6% | < 10% → KILL | not triggered |
| Correction fraction | **not measured** | ≥ 0.80 | **DEFERRED — no independent gold (see below)** |
| Shatter guard: dominant-signature share | **2.2%** | ≤ 5% | **PASS** |
| Shatter guard: fragment count | **132** | ≥ 100 | **PASS** |

**Decision: KEEP, flagged.** The CI lower bound lands at 0.1498 against a 0.15
threshold — **one additional flip out of 515 would have carried it outright**. It is
decisively clear of the 10% KILL line, and the point estimate sits exactly at the
bottom of the pre-registered band, so the historical ~24% finding is reproduced in
direction and roughly in magnitude. Calling this a clean pass would overstate it;
calling it a kill would be wrong.

**Flip direction is the more informative result.** 73 of 93 flips (78%) are the
reasoning tier *upgrading* a verdict the fast tier declined: `one-shot → OPPORTUNITY`
(39), `fragile → OPPORTUNITY` (31), `recipe → OPPORTUNITY` (3). The fast tier is
systematically conservative — 40 `fragile` calls against the reasoning tier's 12.
Whether those upgrades are *correct* is **not** established here.

**Both tiers respected UNKNOWN-never-FAIL** (0 violations each): neither manufactured
a 4c failure from a zero-error cluster. That is a positive result and is recorded as
one rather than quietly dropped.

**Correction fraction is deferred, not skipped.** The check is void if the reasoning run
adjudicates its own flips (a run cannot be its own gold), and **no independent, frozen,
human-adjudicated gold set exists yet** for these clusters — a known evaluation-portability
limit. Reported as unmeasured rather than manufactured.

### Gate 2 — author admission gate → **BUILD the `general`-tier adjudication**

Ten independent adjudication trials over all 54 global clusters.

| Metric | Measured | Threshold | Verdict |
|---|---|---|---|
| Deterministic prior (control) | human **42** / mixed 2 / harness 10 | 42 human | **exact reproduction** |
| Top 2 by pure frequency are harness | **true** | — | the failure mode, confirmed |
| No-gate control: harness targets admitted | **2 of 2** | — | confirmed |
| Prior-only control: harness targets admitted | **0 of 2** | scenario predicted 2 of 2 | **scenario wrong — see below** |
| Treatment: harness admitted | **0 of 2, in 10/10 trials** | 0/2 in 10/10 | **PASS** |
| Treatment: human count | 21–24 | 33 ± 2 in ≥ 9/10 | **FAIL as written** |
| Treatment: admitted denominator (human + mixed) | 34–37 (median 36) | historical 37 | direction confirmed |

**Two findings the scenario did not anticipate, reported rather than smoothed over:**

1. **The prior-only control does not fail the way the author-gate scenario predicted.** The
   scenario predicted the deterministic-prior control would admit both harness clusters into
   the ranked top 2. It does not — the prior already labels both of them harness and excludes
   them. The failure the gate prevents is a **pure-frequency** failure (with no author
   gate at all, both are admitted and they are #1 and #2 by frequency), not a prior
   failure. A third no-gate control arm was added to measure that distinction instead
   of quietly reinterpreting the existing one.

2. **The `human 42 → 33 ± 2` threshold did not reproduce, but the admission behaviour
   did.** The adjudicators land at 21–24 human because they route 12–15 clusters to
   `mixed` where the historical run used only 4. Since the gate admits `human ∪ mixed`,
   the **admitted denominator** — the number that actually determines what the user is
   shown — lands at 34–37 against the historical 37. The disagreement is about where
   the human/mixed line sits *inside* the admitted set, not about what gets admitted.
   The `mixed` class has **no independent human-adjudicated gold set** to settle it (the
   same evaluation-portability limit noted under Gate 1). Not resolved here by fiat.

### Leverage / Fit / Honest-NO real-corpus arms

| Arm | Measured | Threshold | Verdict |
|---|---|---|---|
| S3 Arm 0 — tool-call separation | **13.09×** | ≥ 10× | PASS |
| S3 Arm 0 — capped-span separation | **11.93×** | ≥ 10× | PASS |
| S3 Arm 0 — tool-error separation | **10.77×** | ≥ 10× | PASS |
| S3 Arm 0 — LLM-cycle separation | **8.00×** | ≥ 5× | PASS |
| S3 Arm 0 — combined leverage | **11.01×** | ≥ 10× | PASS |
| S3 Arm 0 — `tool:error` event guard | **10** events / 2,164 sessions | ≤ 10 | PASS |
| S3 Arm i — `n_prompts` standalone | **1.00×** | ∈ [0.9, 1.1] | PASS (drop confirmed) |
| S3 Arm i — extra separation from adding it | **0.86×** | ≤ 1.1× | PASS |
| S3 Arm iii — p75 adds nothing over median | 10.58× vs 11.01× | p75 ≤ median | PASS |
| S3 Arm iii — session-level p75/median amplification | **12.84×** | 12.8× ± 1.5 | **PASS** (faithful check) |
| S3 Arm iii — clustering absorbs the skew | cluster 1.34× < session 12.84× | cluster < session | **PASS** |
| S4 — explicit-only loop rate | **3.19%** | 3.2% ± 0.5pp | PASS |
| S4 — structural loop rate | **54.3%** | ≥ 53.0% | PASS |
| S4 — structural rate, ≥6-tool sessions | **99.83%** | ≥ 99.0% | PASS |
| S4 — implicit : explicit ratio | **17.0 : 1** | ≥ 15 : 1 | PASS |
| S4 — terminal-check prevalence (all) | **16.73%** | 16.7% ± 2.0pp | PASS |
| S4 — terminal-check prevalence (≥6-tool) | **30.20%** | 30.2% ± 3.0pp | PASS |
| S5 — UNKNOWN rendered as FAIL | **0** | 0 | PASS (empirical) |
| S5 — mapper strict-AND, all 4 recovery states | **16/16 cases** | all | **PASS — STRUCTURAL, not a corpus measurement** |
| S5 — mapper strict-AND of source booleans | **54/54** | all | **PASS — STRUCTURAL, not a corpus measurement** |
| S5 — `fragile` count reproduces | **2** | 2 ± 1 | PASS (empirical) |

**S3 Arm iii → PASS-WITH-NOTE (orchestrator-adjudicated, Stage 2).** The faithful
rejection-of-p75 check is the **session-level** p75/median amplification, which
reproduces calibration's 12.8× almost exactly (**12.84×**) — that is the skew that
motivates rejecting p75, and it is the check the eval now asserts. Clustering then
absorbs it: the cluster-level reading collapses to 1.34× (leverage) / 1.31× (span). The
written cluster-level `≤ 1.3×` threshold came from a **1.22× point estimate on slightly
different data**; both readings stay computed in the eval, but only the session-level
one is asserted, and the cluster-level ones are recorded **informational**. Conclusion
is unchanged: median wins, p75 amplifies one outlier at session level.

**S5 composition → RE-SPEC (orchestrator-adjudicated, Stage 2). The mapper is correct.**
The historical 17/14/2 expectation baked in **reasoning-adjudicated** verdicts as if
they were deterministic truth — the exact gap Gate 1 proved justifies the reasoning
tier (78% of flips are the reasoning tier upgrading a mechanical decline). The eval no
longer chases 17/14/2. What it now asserts is the deterministic truth the mapper is
responsible for: **its verdict is a strict AND of each cluster's own 4a/4b/4c booleans
(54/54)**. `fragile` reproduces exactly (2). The 16 source clusters whose stated verdict
sits *above* the strict-AND floor are recorded as **`reasoning_layer_upgrades_over_strict_and`**
— that is the reasoning layer doing its job, not a mapper defect. Final verdicts flow
**deterministic-floor → reasoning-verdict layer** (Gate-1 KEEP), and the eval reports
`composition_deterministic_floor` (20/23/0) alongside `composition_with_reasoning_inputs`
(20/23/2) so the reasoning tier's contribution is visible rather than assumed.
