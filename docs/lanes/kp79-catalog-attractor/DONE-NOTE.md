# Lane kp79-catalog-attractor — catalog hygiene, `amplifier-bundle-attractor`

**Work item: `model_performance-ycxo`** (filed and claimed by this lane; child of
`model_performance-slee`) · **Outcome branch: A (RESOLVED)** · **Spend: see §9 — not $0.00, and
why**

Stage 1 of the catalog-hygiene sweep applied to this repo. The one agent's `meta.description`
is now trigger-first, ≤600 chars, carries an explicit USE WHEN / DO NOT USE WHEN, and contains
**zero** `<example>`/`<commentary>` blocks. Both registered skills' descriptions are
trigger-first, single-paragraph, ≤400 chars. Frontmatter only — every body is md5-identical.

---

## 0. THE GOAL NAMED A RESOLVED ITEM — read this before §1

`GOAL.md` Procedure 1 instructs `work_claim(project="model_performance",
item_id="model_performance-kp79")`. **`model_performance-kp79` is RESOLVED** (held by another
agent session; resolution text on the record). The claim is therefore refused *by
construction*, and for a non-holder **none** of the goal template's three "exhaustive" outcome
branches is executable: A and B require `work_resolve`, C requires `work_release`, and all
three require custody this lane can never obtain.

This is not a new discovery — it is the exact defect **kp79's own resolution** and
**`model_performance-slee`'s `STRUCTURAL FIX REQUIRED BEFORE LAUNCH` section** both diagnose,
verbatim: *"file ONE CHILD ITEM PER REPO under this item, so each lane claims its own id."*

So the lane executed the prescribed fix rather than absorbing the defect or stopping at
`BLOCKED.md`:

| Step | Result |
|---|---|
| `work_list item_id=model_performance-kp79` | `status: resolved` — claim impossible |
| `work_list item_id=model_performance-slee` | `status: open`, `holder: None`; names **`microsoft/amplifier-bundle-attractor (attractor-expert)`** under **STILL UNSWEPT** |
| `work_add` a per-repo child, `related: follow-up-of slee` | **`model_performance-ycxo`** created |
| `work_claim item_id=model_performance-ycxo` | claimed, custody established |

Precedent in the same batch: `model_performance-sovs` — *"STAGE 1 (A) child: catalog hygiene for
microsoft/amplifier-bundle-ios-tester"* — is a sibling lane doing exactly this.

`slee` is also the **authoritative spec** for this work (its acceptance criteria, its five
cross-cutting findings, and its mandatory publication ordering), and it is stricter than
`GOAL.md` in two places this lane honoured: the git-stash A/B capture protocol (finding 5) and
the repo-wide set guard (finding 1).

**Reported, not absorbed:** `GOAL.md` should name a per-repo child id, not `kp79`.

---

## 1. The measurement that makes this real — both always-on surfaces, before vs after

Rendered from a **scratch `AmplifierSession`** built from this repo's `bundle.md`, reading the
live `delegate` tool's `description` and the `hooks-skills-visibility` injection straight off
the mounted tool / emitted hook. **No prompt executed → no LLM call → $0.**

Captured **back-to-back with `git stash push` / render / `git stash pop`** — `slee` finding 5:
a whole-catalog delta taken minutes apart on this shared multi-lane host is not attributable,
and it exits 0 either way. Both renders came from one uninterrupted sequence.

Reproduce:

```bash
python3 docs/lanes/kp79-catalog-attractor/render_catalog.py "file://$PWD/bundle.md" <out-dir>
python3 docs/lanes/kp79-catalog-attractor/measure_catalog.py <before>/delegate-catalog.txt \
                                                             <after>/delegate-catalog.txt attractor
```

### 1a. `delegate` agent catalog

```
WHOLE delegate tool description: 39133 B -> 37632 B  (saved 1501 B, 3.8%)
agent rows: before 45, after 45

agent                                         before B   after B   saved B
attractor-pipeline-runner                           85        85         0
attractor-profile-anthropic                         80        80         0
attractor-profile-gemini                            74        74         0
attractor-profile-openai                            74        74         0
attractor:attractor-expert                        2133       632      1501
TOTAL (selected rows)                             2446       945      1501

rows NOT matching 'attractor': 35730 B -> 35730 B (delta 0 B; expect 0)
hunks=1  (expect 1)
```

**The `attractor:attractor-expert` row fell 2,133 → 632 B (−70.4%).** The slice delta (1,501 B)
and the whole-catalog delta (1,501 B) are **identical to the byte**, the 40 non-attractor rows
moved **0 B**, and `diff -u` of the two renders contains **exactly one hunk**. Nothing else
moved.

### 1b. `hooks-skills-visibility` block

```
WHOLE hooks-skills-visibility block: 20900 B -> 20708 B (saved 192 B, 0.9%)
skill rows: before 64, after 64
skill                         before B   after B   saved B
attractor-scout                    601       418       183
attractorify                       320       311         9
TOTAL (attractor skills)           921       729       192
rows NOT matching 'attractor': 19888 B -> 19888 B (delta 0 B; expect 0)
hunks=1
```

Same three controls hold: slice == whole, other rows delta 0, exactly one hunk.

### 1c. Combined per-turn head

| Surface | before | after | saved |
|---|---:|---:|---:|
| `delegate` agent catalog | 39,133 B | 37,632 B | **−1,501 B** |
| `hooks-skills-visibility` | 20,900 B | 20,708 B | **−192 B** |
| **combined always-on head** | **60,033 B** | **58,340 B** | **−1,693 B (−2.8 %)** |

**1,693 bytes removed from the head of every session, on every turn**, whether or not
`attractor-expert` is ever delegated to or either skill is ever loaded.

Artifacts: `evidence/before/`, `evidence/after/`, `evidence/catalog-measurement.txt`,
`evidence/delegate-catalog.diff`, `evidence/skills-visibility.diff`.

---

## 2. Before/after char counts

| File | stock | lean | delta | budget |
|---|---:|---:|---:|---|
| `agents/attractor-expert.md` (`meta.description`) | 2,094 | 597 | **−1,497 (−71.5 %)** | ✅ ≤600 |
| `skills/attractor-scout/SKILL.md` | 575 | 394 | **−181 (−31.5 %)** | ✅ ≤400 |
| `skills/attractorify/SKILL.md` | 297 | 290 | **−7 (−2.4 %)** | ✅ ≤400 |
| **repo total** | **2,966** | **1,281** | **−1,685 (−56.8 %)** | |

Counts are of the **parsed** YAML value (each is a `>` folded scalar, so each includes the one
trailing newline the folder appends — measured identically on both sides).

`<example>` blocks: **3 → 0**. `<commentary>` blocks: **3 → 0**.

### Nothing was edited that did not need it

`attractorify` was already within budget (297 ≤ 400) and was **not** rewritten for a diff — it
was **re-ordered** so its first clause is the trigger rather than the action, at a cost of
**−7 chars**. All six trigger strings are carried verbatim; the only word dropped is "current".
Had it been trigger-first as well as within budget it would have been left byte-identical.

**Four other attractor rows in the delegate catalog were deliberately left byte-identical**
(`attractor-pipeline-runner`, `attractor-profile-{anthropic,openai,gemini}`) — see §7.

---

## 3. FIDELITY TABLE — every stock fact, checked against lean

Method: enumerate every WHAT / trigger / constraint / USE WHEN fact in each stock description,
**including facts that lived only inside `<example>`/`<commentary>` blocks**, and locate each
in the lean description (or, where relevant, in the agent body, which is unchanged).

**Result: zero triggers, constraints or USE WHEN / DO NOT USE WHEN facts lost. Nothing owed.**
Three non-protected items are absent and are listed separately in §3d with their exact
restoration cost, rather than netted away.

### 3a. `attractor-expert` (2,094 → 597)

| # | Stock fact | Lived in | In lean? |
|---|---|---|---|
| 1 | pipeline **design AND authoring** expert | lead | ✅ "authoring/editing .dot graphs" + the design/mid-build/review cadence |
| 2 | authority on the **SHIPPED** engine's **runtime semantics** | lead | ✅ "Owns the SHIPPED engine's runtime semantics" |
| 3 | routing | lead | ✅ verbatim |
| 4 | substitution | lead | ✅ verbatim |
| 5 | verdict contract | lead | ✅ verbatim |
| 6 | fail-loud behavior | lead | ✅ "fail-loud" |
| 7 | "not just DOT syntax" | lead | ✅ verbatim |
| 8 | use when working with Attractor pipelines | lead | ✅ "USE WHEN work touches an Attractor pipeline" |
| 9 | DOT graph syntax | lead | ✅ "authoring/editing .dot graphs" |
| 10 | pipeline debugging | lead | ✅ "debugging failures" |
| 11 | programmatic integration | lead | ✅ "Python integration" |
| 12 | designing OR authoring/editing **any** .dot pipeline graph | MUST-list | ✅ |
| 13 | do this **BEFORE** handing pipeline implementation to a generic builder | MUST-list | ✅ "consult BEFORE delegating .dot work" |
| 14 | generic builders **carry no attractor engine semantics** | MUST-list | ✅ "which generic builders lack" |
| 15 | debugging pipeline failures or **unexpected routing** | MUST-list | ✅ verbatim |
| 16 | integrating Attractor pipelines into Python applications | MUST-list | ✅ "Python integration" |
| 17 | choosing between pipeline patterns (linear, parallel, conditional) | MUST-list | ✅ "picking a pattern (linear, parallel fan-out/fan-in, conditional)" |
| 18 | understanding fidelity modes, model stylesheets, handler types | MUST-list | ✅ "fidelity modes, model stylesheets, handlers" |
| 19 | working with the attractor bundle configuration | MUST-list | ✅ "bundle config" |
| 20 | consult at design START, mid-build, and final review — **not once** | tail | ✅ "consult BEFORE delegating .dot work, mid-build, and review" |
| 21 | *(example-only)* parallel **fan-out/fan-in** patterns | `<example>` 1 | ✅ **RESCUED** into the pattern list — stock's own MUST-list said only "parallel" |
| 22 | *(commentary-only)* the expert's knowledge of **handlers** and **patterns** | `<commentary>` 1 | ✅ both in the lean USE-WHEN list |
| 23 | *(example-only)* a conditional gate taking the wrong path | `<example>` 2 | ✅ "unexpected routing … conditions" |
| 24 | *(commentary-only)* **edge selection, condition syntax, outcome values** | `<commentary>` 2 | ✅ **RESCUED** as "(edge selection, conditions, outcomes)" |
| 25 | *(commentary-only)* **`llm-direct` worker vs `spawn` worker paths** | `<commentary>` 3 | ✅ **RESCUED** as "(llm-direct vs spawn workers)" |
| 26 | DO NOT USE WHEN | — | ➕ **ADDED** — stock had none |

Three facts that existed **only** inside example/commentary blocks (#21, #24, #25) are now in
the rendered description proper. The lean row carries **more** routing information than stock
while costing 70 % fewer bytes.

### 3b. `attractor-scout` (575 → 394)

| Stock fact | In lean? |
|---|---|
| mines **YOUR OWN** context-intelligence **session history** | ✅ "scout their own sessions" + "Mines YOUR OWN context-intelligence history" |
| finds attractor-shaped opportunities in real **recurring** work | ✅ "attractor opportunities" + "recurring" |
| units you do **again and again** | ✅ "recurring" |
| that **cost real effort** | ✅ "costly" |
| that **would survive being handed to a loop** | ✅ "would survive a loop" |
| surfaces them **ranked** | ✅ "ranks it" |
| **honest-NOs as first-class output** | ✅ "honest-NOs first-class" |
| writes a **self-contained HTML** opportunity map | ✅ "writes a self-contained HTML map" |
| **Own data only** | ✅ verbatim |
| **nothing leaves the machine** | ✅ verbatim |
| trigger `/attractor-scout` | ✅ verbatim |
| trigger "what should I automate?" | ✅ "asks what to automate" |
| trigger "find my attractor opportunities" | ✅ "for attractor opportunities" |
| trigger "scout my sessions" | ✅ "scout their own sessions" |
| trigger "what do I keep doing by hand?" | ✅ "what they keep doing by hand" |
| trigger "mine my own work for pipelines" | ✅ verbatim |

All six trigger phrases survive. Two adjectives are absorbed ("local" — implied by "nothing
leaves the machine"; "opportunity map" → "map", same referent one clause after "attractor
opportunities"). No trigger, constraint or USE-WHEN fact dropped.

### 3c. `attractorify` (297 → 290)

| Stock fact | In lean? |
|---|---|
| analyze the **current session** | ✅ "Use when the session might warrant…" (the word "current" is the only loss, −8 chars) |
| decide whether an attractor pipeline is **warranted** | ✅ "might warrant … decide whether one is" |
| then **design one conversationally** if it is | ✅ "then design it conversationally if so" |
| all **six** trigger phrases | ✅ all six **verbatim, byte-for-byte** |

### 3d. Absent in lean — assessed as NOT in the protected set, with restoration cost quoted

`GOAL.md`'s fidelity gate protects *"any **trigger, constraint, or USE WHEN / DO NOT USE WHEN
fact**"*. Three stock items are absent from lean and are **not** members of that set. They are
listed here with the exact byte cost of restoring them, so a reviewer can overrule with a
number in hand rather than a re-read.

| Stock item | Why it is not a protected fact | Restoration cost |
|---|---|---|
| `(e.g. modular-builder)` | An **illustrative instance** of the class "generic builder". The constraint itself (#13) and its rationale (#14) are both present in class form, so **the routing decision is identical with or without the name**. Additionally, this repo's own `AGENTS.md` *Dependency awareness rule* says not to name another repository's components in shipped surfaces; `modular-builder` belongs to `amplifier-foundation`. **Checked, not assumed:** it also appears **0 times** in the agent body, so it is genuinely gone from the repo. | **+17 chars → 614**, which breaks the ≤~600 budget and the new guard's `AGENT_MAX_CHARS` |
| "will re-discover the foot-guns the hard way" | Rhetorical **consequence** of #14, not a trigger or constraint. Also 0 body hits. | **+45 chars → 642** |
| "shapes" (from `<commentary>` 1: "shapes, handlers, and patterns") | Node-shape vocabulary. `handlers` and `patterns` are both in lean; **"shape" appears 18× in the unchanged agent body**, i.e. the fact is preserved on the pay-per-use surface, which is the sweep's whole thesis. | **+8 chars → 605** |

At 597/600 there is no headroom to restore any of the three without exceeding the stated
budget. The lane's judgment: fidelity of *routing* facts is intact (§3a is complete), and none
of these three changes a routing decision. **Named rather than netted away.**

---

## 4. `validate-agents` v1.7.0 — FAIL → PASS WITH WARNINGS

**Do not read this as "PASS was held".** Stock was **failing**: `<example>` and `<commentary>`
are structural **ERRORs** under v1.4.0+, so stock classified `critical`. This is `slee` finding
3 confirmed on a fifth repo.

Deterministic phases 0–3, run on both trees back-to-back at $0
(`evidence/validate-agents-structural-phases.txt`):

```
===== BEFORE (stock, merge-base c12885d) =====
agents discovered: 1
structural summary: {'total': 1, 'passed': 0, 'errors': 2, 'warnings': 1}
quality_level: critical
  attractor-expert   chars= 2094 examples=3 commentary=3
                     errors=['COMMENTARY_TAG_PRESENT', 'EXAMPLE_BLOCK_PRESENT']
                     warnings=['NO_TOOLS_SECTION']

===== AFTER (branch lane/kp79-catalog-attractor) =====
agents discovered: 1
structural summary: {'total': 1, 'passed': 1, 'errors': 0, 'warnings': 1}
quality_level: needs_work
  attractor-expert   chars=  597 examples=0 commentary=0
                     errors=[] warnings=['NO_TOOLS_SECTION']
```

**The full recipe** (all 12 steps, including the three LLM phases) was then run on the branch —
`evidence/validate-agents-after-full-report.md`, session
`2620a7e20e624213-20260907-105125_recipe`. Verdict, quoted:

> - **Overall Verdict**: ⚠️ **PASS WITH WARNINGS**
> - **Agents Found**: 1 total across 1 location
> - **Quality Breakdown**: 0 good, 0 polish, **1 needs_work**, 0 critical
> - **Issues**: **0 errors, 1 warning, 1 suggestion**

The report's own description-quality phase returned **PASS on both criteria it owns** (WHEN
clause and WHAT guidance) and concluded *"leave `meta.description` byte-identical … 597 → 597,
delta 0"* — i.e. it graded the lean text and found nothing to change.

`needs_work` is carried **entirely** by `NO_TOOLS_SECTION`; the classifier's branch is
`elif not has_explicit_tools: quality = "needs_work"`, evaluated *before* any
description check. A description edit cannot reach it. See §5.

---

## 5. The one warning is REAL here — and it is the opposite of `slee` finding 2

`slee` finding 2 warns that `NO_TOOLS_SECTION` is often a **false negative**: the checker reads
agent frontmatter only, so a valid behavior-level `tools:` declaration is flagged as missing,
and *"do not 'fix' it by duplicating the declaration into frontmatter."*

**Checked rather than assumed, and here it does not apply.** `behaviors/attractor-core.yaml`
declares exactly two tools — `tool-report-outcome` and `tool-skills` — and **no** filesystem,
bash or search. That same file registers `attractor-expert` **unconditionally** (`agents:
include: [attractor:attractor-expert]`) and documents behavior-only installs as a supported
path. Meanwhile the agent's own body makes a shell command a *delivery obligation*: *"The graph
is not delivered until `dot-runner lint <file>` has been RUN on it"*.

So in a `behaviors/attractor-core`-only composition the expert mounts with no bash, no
filesystem and no search — and degrades to prose about a file it cannot read.

**Deliberately NOT fixed on this branch.** It is a **mount-behaviour** change, not a
frontmatter-description change; it alters what the agent can do at runtime, and it needs its own
live verification (the report itself flags as *unverified* whether the loader honours a
top-level `tools:` in an agent-definition `.md`). Filed as an observation for a separate PR
rather than smuggled into a hygiene changeset. The `model_role` suggestion is out of scope for
the same reason.

---

## 6. Bodies byte-identical — verified, not asserted

md5 of everything after the second `---`, stock vs branch:

| File | md5 (stock) | md5 (branch) | |
|---|---|---|---|
| `agents/attractor-expert.md` | `24eaab7b8e47613ac2df09911730e502` | `24eaab7b8e47613ac2df09911730e502` | ✅ 31,236 B unchanged |
| `skills/attractor-scout/SKILL.md` | `a44b7462815acc58951e7bbf7ef07d6d` | `a44b7462815acc58951e7bbf7ef07d6d` | ✅ 28,969 B unchanged |
| `skills/attractorify/SKILL.md` | `2d31402c82e91784b6a9f2e3f751bfb0` | `2d31402c82e91784b6a9f2e3f751bfb0` | ✅ 28,802 B unchanged |

Every edit was applied through a script that re-parses the YAML afterwards and **asserts** the
parsed description equals the intended string and the body md5 is unchanged; it would have
raised rather than written a divergent fold.

---

## 7. Scope calls, stated rather than silent

**The four inline `agents:` rows in `bundle.md` were left byte-identical.** They render into
the delegate catalog (`attractor-pipeline-runner`, `attractor-profile-{anthropic,openai,gemini}`
— 313 B combined) and they are *not* trigger-first, so the question is real. They were not
touched because:

1. They are **bundle-config entries, not agent frontmatter**. `GOAL.md`'s standard is scoped to
   *"Agent `description` (frontmatter — note it is nested under `meta:` in these bundles)"*.
   `validate-agents` v1.7.0 classifies an agent by *"frontmatter declares a top-level `meta:`
   key"* and independently discovers **1** agent in this repo.
2. `slee` scopes this repo to *"amplifier-bundle-attractor (**attractor-expert**)"*.
3. They are **pipeline-internal machinery** — spawned by the engine / the `run_pipeline` tool
   via the `profiles:` map, never delegated to by hand. Bringing them to the full standard means
   *adding* USE WHEN / DO NOT USE WHEN prose, i.e. **growing** the always-on catalog by roughly
   800–1,200 B to better route agents nothing routes to. That is a byte regression for no
   routing gain.
4. The same four descriptions are duplicated in `bundles/attractor-interactive.yaml`,
   `bundles/attractor-pipeline.yaml` and `agents/pipeline-runner.yaml`; editing them is a
   four-file change with a de-duplication question attached, which is its own PR.

Recorded as a **candidate follow-up**, not as done: *"add a one-clause DO-NOT-DELEGATE-DIRECTLY
marker to the four pipeline-internal agent rows, in one place, after de-duplicating them."*

**`docs/ATTRACTORIFY-SKILL.md` is out of scope.** It is a `SKILL.md`-shaped file under `docs/`;
`tool-skills` registers only `@attractor:skills`, so it renders into no catalog and costs 0
B/turn. Reported rather than edited to make a count match (`slee` finding 4, corollary).

### The pre-launch count, verified rather than re-derived

`GOAL.md` states *"2 agents, 2 skills, 1 file containing `<example>` outside `docs/`"*.
Measured against the merge-base with `git grep` (not a working-tree grep):

| Claim | Measured | |
|---|---|---|
| 2 agents | **1** — `validate-agents` discovers `agents/attractor-expert.md` only | ⚠️ over-count |
| 2 skills | **2** — `skills/attractorify`, `skills/attractor-scout` | ✅ |
| 1 file with `<example>` outside `docs/` | **1** — `agents/attractor-expert.md` | ✅ |

The seven `agents/*.yaml` files are agent-*profile bundles* (composition units), not agent
definitions; `docs/plans/2026-03-12-…md` carries a `meta:` block for a proposed script and is
excluded by the recipe's own `docs/` exclusion. Neither is an under-counted agent.

---

## 8. Tests and CI

**This repo HAS CI** — `.github/workflows/ci.yml`: `unit-tests` (matrix), `dot-render-gate`,
`opinionated-guards`, and the aggregate `CI Gate (all checks passed)`, which is the one required
status check on `main`. Stated because four repos earlier in this sweep had none.

| | result |
|---|---|
| `python -m pytest tests/ -q --ignore=tests/e2e` on stock | 204 passed, 2 skipped |
| same, on the branch | **214 passed, 2 skipped** |
| delta | **+10 — exactly the new guard's 10 cases, zero regressions** |

### New repo-wide guard: `tests/test_agent_description_policy.py` (`slee` finding 1)

Walks `agents/*.md` **and** `skills/*/SKILL.md` **as a set**, so an agent or skill added later
is covered — a per-file test can only guard what existed when it was written, which is how the
policy drifted in the first place. Carries an **empty-glob tripwire**
(`test_the_sweep_is_not_vacuous`) so a rename or directory move breaks the guard loudly instead
of silently disarming it.

Asserts: description length within `[100, 600]` for agents and `[100, 400]` for skills (the
100-char floor mirrors `validate-agents`' own `MIN_DESCRIPTION_LENGTH`, so a description cannot
be "shortened" into uselessness to satisfy the ceiling); explicit USE WHEN **and** DO NOT USE
WHEN in every agent description; single paragraph for every skill description; and zero
`<example>`/`<commentary>` in either.

**Proven red before green** (`evidence/guard-fail-before.txt`) — the guard run against the
stock descriptions:

```
FAILED test_agent_description_is_within_budget[agents/attractor-expert.md]
FAILED test_agent_description_carries_both_routing_clauses[agents/attractor-expert.md]
FAILED test_skill_description_is_within_budget[skills/attractor-scout/SKILL.md]
FAILED test_description_carries_no_example_markup[agents/attractor-expert.md]
4 failed, 6 passed
```

and against the branch: `10 passed`.

**No test in this repo asserted that `<example>` blocks must be PRESENT** — `slee` finding 1's
dot-graph failure mode was checked for and does not occur here. `grep` over `tests/` found three
files mentioning "description"; all three are unrelated (a DOT node prompt, a `bundle.description`
assertion on `agents/attractor-agent-openai.yaml`, and a prose string).

---

## 9. Spend — NOT $0.00, and the goal text is inconsistent about it

`GOAL.md` sets the authority at **$0.00** (`0 runs x 0 arms x $0 / 1.00 = $0.00`) and in the
same sentence enumerates what it buys: *"Text edits, **a recipe run**, a catalog render."*
A `validate-agents` run is also a **required deliverable** (*"`validate-agents` run ON THE
BRANCH with its verdict quoted"*), and three of its twelve steps are LLM steps.

| Activity | API spend |
|---|---|
| All description edits | $0.00 |
| Catalog renders (4 sessions, both surfaces, both trees) | $0.00 — no prompt executed |
| `validate-agents` deterministic phases 0–3 (×2 trees) | $0.00 — pure bash/python |
| `validate-agents` **full recipe** on the branch, 3 LLM steps | **not $0.00 — see below** |
| API measurement runs | **none** — not authorised, not performed |
| Infrastructure (DTU/containers) | **none created**, so no ledger row and nothing to tear down |

**The recipe runner records no token usage or cost.** `steps.jsonl` for the run carries zero
`usage` objects across all 12 steps, so there is no measured figure to quote and this lane will
not invent one. What can be stated exactly: **3 LLM steps** (`description-quality-check`,
`tool-access-analysis`, `synthesize-report`) over **1 agent**, producing 11,849 chars of report.
Order of magnitude, on a sonnet-class model, is **well under $1**.

Two things follow, both reported rather than absorbed:

1. **Goal-text inconsistency (small, one clause):** "$0" and "a recipe run" cannot both be
   literally true while a required deliverable runs LLM steps. Either the cap should read
   "$0 API measurement; recipe runs and catalog renders excluded", or the deliverable should
   specify the deterministic phases only.
2. **Instrument gap, worth its own item:** a program that gates every lane on spend runs recipes
   through a runner that **records no usage**. Every recipe-running lane is reporting an
   estimate. `evidence/` carries the session id so the figure can be recovered if the runner
   ever gains usage capture.

The cap did **not** bind. Nothing was recorded NOT-POSSIBLE. No deliverable was dropped.

---

## 10. Deliverable status

| Deliverable | Status |
|---|---|
| Every description meeting the standard | **DONE** — 1 agent ≤600 with USE WHEN / DO NOT USE WHEN and 0 examples; 2 skills ≤400, single paragraph, trigger-first |
| FIDELITY TABLE, incl. commentary-only facts | **DONE** — §3. Zero protected facts lost; 3 rescued out of `<commentary>`; 3 non-protected absences named with restoration cost |
| Before/after char counts per item + repo total | **DONE** — §2 |
| Delegate catalog rendered BEFORE and AFTER, bytes quoted, with the control | **DONE** — §1a/§1b. Slice == whole to the byte, other rows delta 0, exactly one hunk on each surface |
| `validate-agents` on the branch, verdict quoted | **DONE** — §4. **FAIL (critical, 2 errors) → PASS WITH WARNINGS (0 errors)**, stated as a fail-before/pass-after, not as a held PASS |
| CI green where the repo has CI | **DONE** — repo has CI; see §8 and the PR |
| Bodies byte-identical | **DONE** — §6, md5 on all three |
| DRAFT PR, not merged | **DONE** — see the PR link in `DONE.json` |
| DONE-NOTE at the lane artifact root | **DONE** — this file; repo-root `DONE-NOTE.md` untouched |

**Outcome branch A (RESOLVED).** No deliverable was NOT-POSSIBLE.

---

## 11. Instrument fix worth carrying to the remaining lanes

`measure_catalog.py` was forked from the android-tester lane and **one line had to be fixed**.
It keyed a catalog row with:

```python
line[len("  - "):].split(":", 2)[0:2]   ->  ":".join(...)
```

which is correct only for a **namespaced** agent (`bundle:agent: desc`), where the first two
colon-separated fields are exactly the name. This repo's catalog also carries **un-namespaced**
rows (`attractor-profile-openai: desc`), for which that expression folds the *description* into
the key. An un-namespaced agent whose description changed would then appear as **two different
rows** — one only in `before`, one only in `after` — and its shrink would be reported as a
removal plus an addition, with the "expect 0" control silently wrong.

Fixed by keying on everything before the first `": "` (an agent name can never contain a space),
which is correct for both shapes. **Any remaining lane in this sweep whose repo registers
agents inline in `bundle.md` (i.e. un-namespaced) needs this fix**; the four repos already
merged all used namespaced agents and were unaffected.

---

## 12. Pre-publication leak review — a NOT-SAFE verdict, and what resolved it

This PR introduces a **new public content class** on two counts (`docs/lanes/` is a new
directory here, and it carries real-run evidence), so `docs/OPERATIONS.md` §7 requires **both**
the deterministic layers **and** the leak-lens review by a fresh-context reader. Both were run.
The reviewer's first verdict was **NOT SAFE TO PUBLISH AS-IS**; that is recorded here rather
than quietly superseded.

### Deterministic layers — `evidence/leak-scan.txt`

Layer-1 patterns and the Layer-2 derivation were taken **verbatim** from this repo's own
reference guard (`skills/attractor-scout/tests/test_no_real_data_leak.py`) and run over the 18
files this change adds or modifies.

**Layer 1: 11/11 PASS**, 0 matches each — v4-UUID session id, `gc-NN` cluster id, `-home-<user>`
slug, e-mail shape, secret-key prefixes, `/home/`, `/Users/`, `~/.amplifier/projects/-`,
`localhost:7687`, `bolt://`, `.internal`.
**Layer 2: hostname, hostname short form, login user, home directory and git `user.email` all
match 0 times.** Layer 3 (local deny-list) absent — the expected state.

Two real hits were **caught by these layers and fixed before the commit stood**, not missed:

1. Absolute host paths (`/home/<user>/…`) in three captured evidence files. Redacted to
   `<repo>` / `<amplifier-cache>`; each file carries a banner saying so, and the content is
   otherwise verbatim.
2. **A work-item holder id of the form `agent-<hostname>-<pid>` embedded the machine's
   hostname** — a Layer-2 leak hiding inside an identifier that looks like an opaque token.
   Replaced with "held by another agent session". This is the finding worth carrying to the
   other lanes in this sweep: **holder ids are not opaque, they contain the host name.**

The one residual Layer-2 FLAG is git `user.name`, which in this batch is the bot identity
"Amplifier" — the product word, matching 175× legitimately. **Reported, not absorbed:** Layer 2
has a false-positive class when `user.name` is a product word rather than a personal name, and
the repo's own reference guard would behave identically here. One clause would close it.

### Leak-lens review — fresh-context reader, verbatim brief

A reader with **no context on this change** was given only the brief `docs/OPERATIONS.md` §7
specifies: *"Read this diff as a stranger. List everything that identifies a person, a machine,
an organization, an internal project, or a private process."*

It confirmed zero usernames, hostnames, IPs, absolute paths or credentials survive, and —
asked specifically — confirmed the two large catalog captures expose **no private or unreleased
bundle, no personal skill, and no machine fingerprint beyond the public bundle dependency graph
any consumer of this repo would render for themselves.**

It then returned **NOT SAFE AS-IS** on one substantive ground: `DONE-NOTE.md` documents an
internal multi-repo work-tracking process — project name, live item ids, `work_claim` /
`work_resolve` / `work_release` semantics, sibling-lane names, spend mechanics — which a
stranger reading a hygiene PR does not need. It named the two checks that would flip the
verdict. **Both were performed, and both cleared:**

| Reviewer's condition | Checked how | Result |
|---|---|---|
| Is a narrative `docs/lanes/` DONE-NOTE an established, accepted public convention here? | `gh api repos/microsoft/amplifier-bundle-android-tester/contents/docs/lanes` | **Yes** — `kp79-catalog-android-tester/` is live on that repo's public `main`, and its DONE-NOTE names `model_performance-kp79`, its outcome branch and a `## Spend` section. This lane's own `GOAL.md` mandates the location, and `tools/check_lane_artifact_paths.py` enforces it. Precedent, not a leak. |
| Are the sibling repo names public, or internal codenames? | `gh api repos/microsoft/<name>` on each | **All public** — `amplifier-work-tracker`, `amplifier-bundle-{stories,converge,ios-tester,digital-twin-universe,amplifier-tester}`. |

One further reviewer flag was a **transcript artifact, not repo content**: a line rendered to it
as `key = [REDACTED:SECRET]` is `key = m.group(1)…` (32 chars, no high-entropy token) and is
**byte-identical to the already-public android-tester copy** of the same helper. Verified rather
than assumed, and recorded so nobody chases the phantom.

**Adjudicated verdict: SAFE TO PUBLISH.** The single-source reason: the only ground for the
NOT-SAFE verdict was uncertainty about an established convention and about repo visibility, and
both were resolved against public evidence rather than against this lane's own say-so.

---

## 13. What remains open

1. **The merge.** The PR is draft by design — the manager merges. (§0's item `ycxo` is resolved
   at the draft PR, per the LANDING STAGE clause.)
2. **`GOAL.md` names a resolved work item.** Fix before the next batch: name a per-repo child id.
   `slee` already carries the general instruction.
3. **`NO_TOOLS_SECTION` on `attractor-expert` is a real defect** (§5), not the false negative
   `slee` warns about. Wants its own PR plus a behavior-only-install live check.
4. **`model_role` absent on `attractor-expert`** — `reasoning` is defensible; routing/cost
   change, separate PR.
5. **The four pipeline-internal catalog rows** (§7) — de-duplicate across four files, then add a
   one-clause do-not-delegate-directly marker in the single remaining place.
6. **The recipe runner records no token usage** (§9) — every spend-gated lane that runs a recipe
   is reporting an estimate.
7. **`slee` still has 5+ repos unswept** (ios-tester is held by a sibling; work-tracker, stories,
   converge, digital-twin-universe, amplifier-tester, plus the third-party set). Each needs its
   own child item, as this one did.

8. **Leak-defense Layer 2 false-positives on a product-word `git user.name`** (§12) — one
   clause in `skills/attractor-scout/tests/test_no_real_data_leak.py`, or one line in
   `docs/OPERATIONS.md` §7, would close it.
9. **Work-item holder ids embed the machine hostname** (§12) — every lane in this sweep that
   quotes a holder id in a public artifact is leaking a hostname. Worth a note on `slee`.

**Observations against `docs/VISION.md`** (per `AGENTS.md`, *"If you see something, do
something"*): none arose. This change touches guidance surfaces only and moves toward, not away
from, the nlspec — it changes no engine behaviour and no spec-adjacent claim. **Decision-matrix
tier: guidance surfaces / doctrine-neutral**, whose required verification per the repo's own
gradient is *"Root guards"* — run, green, and extended by a new guard.
