# DESIGN: The Composition Fix — serving the bundle's own guidance

**Repo:** microsoft/amplifier-bundle-attractor
**Baseline:** `origin/main` @ `0f84309` (all reads below via `git show origin/main:<path>`; the local checkout at `4f8e22d` lags and was not relied on)
**Status:** SHIPPED. Design + empirical probes below; the build's deviations from them are recorded verbatim in [§8 — As built](#8-as-built-what-shipped-and-where-it-deviates).
**Date:** 2026-08-15

---

## 1. The defect, restated

The bundle does not serve its own guidance. Three verified causes:

**Cause 1 — the standard install composes no always-on guidance.**
`bundle.md:17-19` includes only `amplifier-foundation@main` + `attractor:behaviors/attractor-core`. There is **no `context:` key anywhere in bundle.md** (re-verified against `0f84309`; the file is 167 lines: frontmatter has `includes:`, `tools:` (tool-skills), `agents:` (dict-form profile agents) — nothing else). The two always-on guidance files, `context/pipeline-awareness.md` and `context/dot-reference.md`, are composed **only** by `bundles/attractor-interactive.yaml:63-67`:

```yaml
context:
  # '../context/' climbs from this YAML's dir to the bundle-root context/; idiomatic target is 'attractor:context/...' pending foundation namespaced-include support.
  include:
    - ../context/pipeline-awareness.md
    - ../context/dot-reference.md
```

…and nothing on the standard install path (`amplifier bundle add git+…@main` → root `bundle.md`) includes `attractor-interactive`. Measured consequence: the guidance-eval baseline's work-01/work-02 failures — the objective-first steering and the DOT attribute vocabulary were never in-session.

**Cause 2 — `agents/attractor-expert.md` is registered by nothing.**
Zero `agents:` YAML references to the file (grep of all YAML at `0f84309`; only `skills/attractorify/SKILL.md:344` reads it, as a delegation *fallback*: "If delegation is unavailable, fall back to reading `agents/attractor-expert.md` directly"). The expert real sessions reach is the **inline dict** at `behaviors/attractor-core.yaml:23-35`: a loop-agent orchestrator whose Layer-1 is `system_prompt_file: context/system-attractor-expert.md`. The 20 KB expert knowledge body (plus its `@attractor:context/attractor-expert-defenses.md` transclusion at line 298) is a dead file on every real path.

**Cause 3 — the @main self-pin makes branch guidance untestable.**
`behaviors/attractor-core.yaml:28` pins `source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent`. loop-agent resolves a relative `system_prompt_file` against **its own installed module location** — verified at `modules/loop-agent/amplifier_module_loop_agent/__init__.py`, `_resolve_system_prompt_file`:

> "Resolution is **CWD-INDEPENDENT**. It anchors on this module's installed location (`__file__`) … the bundle root is `parents[3]` of this file."

So a branch install fetches loop-agent from **main's** snapshot, and `context/system-attractor-expert.md` resolves inside **main's** snapshot. Branch regression-testing of guidance is structurally impossible without the mirror-main workaround. The same self-pin class recurs across `bundle.md` (4 agent orchestrator pins + the skills registration pin `git+…attractor@main#subdirectory=skills`), `bundles/*.yaml`, `agents/*.yaml`, `profiles/*.yaml`.

---

## 2. Probe results

### P1 — sizes of the candidate context files (origin/main @ 0f84309; tokens ≈ bytes/4)

| File | Bytes | Lines | Words | Est. tokens |
|---|---:|---:|---:|---:|
| `context/pipeline-awareness.md` | 12,598 | 222 | 1,777 | ~3,150 |
| `context/dot-reference.md` | 10,780 | 218 | 1,485 | ~2,695 |
| **Interactive's always-on pair (today)** | **23,378** | 440 | 3,262 | **~5,845** |
| `context/system-attractor-expert.md` (expert Layer-1) | 5,972 | 106 | 907 | ~1,493 |
| `context/attractor-expert-defenses.md` (transcluded by expert .md) | 10,615 | 259 | 1,469 | ~2,653 |
| `agents/attractor-expert.md` (body = knowledge) | 20,444 | 393 | 2,933 | ~5,111 |
| `context/engine-semantics.md` (on-demand deep doc) | 20,047 | 311 | 2,547 | ~5,011 |

So the root install today serves **0** guidance tokens; the interactive entry point serves **~5.8k** tokens always-on.

### P2 — how the ecosystem composes (cached bundles under `~/.amplifier/cache/`, 6 surveyed)

| Bundle | `context:` always-on | Size | `agents:` registration | Same-repo module `source:` |
|---|---|---:|---|---|
| work-tracker | `work-tracker:context/awareness.md` | 2,287 B (~570 tok) | `include: [work-tracker:work-executor]` (.md agent) | `../modules/tool-work-tracker` (relative) |
| browser-tester | `browser-tester:context/browser-awareness.md` | 3,183 B (~800 tok) | `include:` 3 .md agents | — |
| terminal-tester | `terminal-tester:context/terminal-awareness.md` | 2,566 B (~640 tok) | `include:` 3 .md agents | `../modules/tool-terminal-inspector` (relative) |
| android-tester | `android-tester:context/android-awareness.md` | 3,215 B (~800 tok) | `include:` 3 .md agents | `../modules/tool-android-inspector` (relative) |
| ios-tester | `ios-awareness.md` | 3,890 B (~970 tok) | (same pattern) | — |
| recipes | `recipe-awareness.md` 1,851 B; heavy `recipe-instructions.md` (8 KB) transcluded into bundle.md body | 1,851 B (~460 tok) | — | — |

The doctrine is real and uniform: **thin always-on awareness ≈ 1.9–3.9 KB (~460–975 tokens), heavy knowledge in registered `.md` agents, same-repo modules by relative source.** work-tracker's behavior YAML says it in its own description: *"a thin always-on awareness context covering the silent-failure hazards"* — and carries the decisive comment: *"Relative sources resolve against THIS FILE's directory (behaviors/), not the repo root. '../modules/...' is correct; './modules/...' silently resolves to behaviors/modules/ and the module never loads."* These bundles are installed via `git+` in this very environment and their tools are mounted in the current session — relative same-repo sources are production-proven under the real install path.

### P3 — loop-agent resolution + source forms

- `_resolve_system_prompt_file` (quoted above): anchor = module `__file__`, bundle root = `parents[3]`. Whoever serves the loop-agent **module bytes** serves the **context files** next to them. Pin @main → main's files, always.
- Foundation module `source:` accepts: git URL (`git+…@ref#subdirectory=…`) and **filesystem-relative path resolved against the declaring YAML's directory** (P2 evidence). A relative source inside an installed snapshot stays inside that snapshot **at the installed ref** — branch install ⇒ branch loop-agent ⇒ `parents[3]` = branch snapshot ⇒ branch guidance. No third "same-repo namespaced module" form was found in foundation; not designed against.
- Skills self-pin equivalent exists too: work-tracker registers its skills as `"@work-tracker:skills"` (namespaced, ref-free) where attractor pins `git+…attractor@main#subdirectory=skills`.

### P4 — what `context:` and `agents:` actually do (foundation source, cache @ installed ref)

- `amplifier_foundation/bundle/_dataclass.py::_parse_context`: `include:` entries with a namespace prefix (`ns:path`) are stored pending and resolved via `source_base_paths` after compose (`resolve_pending_context`); plain relative entries resolve immediately against the declaring file's dir. **The `attractor-interactive.yaml` comment "pending foundation namespaced-include support" is stale — namespaced context includes are implemented and used in production (browser-tester).**
- `amplifier_foundation/bundle/_prepared.py`: `context:` entries become ContextFile objects injected into every prepared session's payload (provenance kind `bundle_context_decl`), re-read at each session preparation. A root-bundle `context:` key = always-on system-context block for sessions composed from that bundle.
- Compose semantics (`Bundle.compose` docstring): *"agents: later overrides earlier (by agent name); context: accumulates with namespace prefix; instruction: later replaces earlier."* → two definitions under one agent name cannot coexist; last one wins.
- Agent registration: `agents: include: ["ns:name"]` → `source_base_paths[ns]/agents/name.md`. `_load_agent_file_metadata` extracts `meta:` **and top-level `tools/providers/hooks/session`** from .md frontmatter, plus `instruction` from the body — so a .md agent can carry its own `session.orchestrator` (needed because attractor-core is composed under pipeline parents, where an orchestrator-less child would inherit `loop-pipeline` and recurse — bundle.md's own warning).
- Include graph (verified): `behaviors/attractor-core` is included by root `bundle.md`, all three `bundles/*.yaml`, `agents/*.yaml` (all six provider agents + pipeline-runner), and all `profiles/*.yaml`. **Anything added to attractor-core lands in every pipeline LLM node.** Root `bundle.md` is included by nothing else. This asymmetry drives the whole design.

---

## 3. The composition design

### 3.1 Q1 — what a standard install composes: a new thin awareness, root-only

**New file: `context/attractor-awareness.md` — budget ≤ 3.2 KB (~800 tokens), target ~2.4 KB (~600 tokens).** Content plan (five blocks, sized):

1. **Objective-first trigger** (~700 B): the two marks (recurring end-state pain + "build me something"; machine-checkable plausibility), the first-reply moves (restate as end-state; ask the definition-of-done question out loud), and the say-the-names rule: `/attractorify`, `attractor:attractor-expert`, `@attractor:examples/objective/objective-runner.dot`. Condensed from `pipeline-awareness.md`'s "FIRST: is this an objective?" section, keeping its "name the objective path in the same breath even if another mode wants the request" clause — that is the exact work-01 failure.
2. **Three-question test** (~250 B): cycle? machine-checkable exit external to the worker? survives one bad LLM day? Plus: "a linear gateless chain is recipe territory — say so before authoring."
3. **The never-clause** (~200 B): "The self-report gate is this project's named anti-pattern. A worker's — or judge's — claim about its own output is never the exit; exits gate on machine evidence external to the worker."
4. **Authoring tripwire** (~350 B): "Before authoring or editing ANY `.dot`: delegate to `attractor:attractor-expert`. The only attribute vocabulary the engine reads is `@attractor:context/dot-reference.md` — attributes not on that card are silently inert. Lint everything: `attractor lint <file>`." (This is the work-02 failure: invented attributes because the vocabulary card wasn't in-session.)
5. **Pointer block** (~400 B): on-demand depth = `@attractor:context/dot-reference.md`, `@attractor:context/pipeline-awareness.md`, `docs/attractor-explained.html`; pipeline *execution* from this composition goes through the `attractor-pipeline-runner` agent or the `attractor` CLI. **Must not claim `run_pipeline` exists** — the root bundle does not mount `tool-pipeline-run` (interactive does), and `pipeline-awareness.md` opens with "You have access to the `run_pipeline` tool," which is precisely why that file cannot be composed into root as-is.

**Placement:** `bundle.md` frontmatter gains:

```yaml
context:
  include:
    - context/attractor-awareness.md      # plain relative: resolves against bundle.md's dir
```

**Explicitly NOT in `behaviors/attractor-core.yaml`** — attractor-core flows into every pipeline LLM node (P4 include graph); putting interactive steering there would inject "delegate to the expert / say /attractorify" into non-interactive pipeline workers and charge ~600 tokens per node per iteration. Root-only placement leaves every pipeline composition byte-identical.

**What stays where:**

| Content | Home | Served |
|---|---|---|
| Objective trigger, 3-question test, never-clause, authoring tripwire, pointers | NEW `attractor-awareness.md` | always-on, root sessions (~600 tok) |
| Full `pipeline-awareness.md` + `dot-reference.md` | unchanged | always-on in interactive (unchanged ~5.8k); on-demand @mention from root |
| Expert knowledge (20.4 KB) + defenses (10.6 KB) + Layer-1 persona (6 KB) | attractor-expert agent (the context sink) | on delegation only (~9.3k tok in the child) |
| `engine-semantics.md` (20 KB) | on-demand, expert reads it | never always-on |

**Before/after per-session token cost:**

| Surface | Before | After |
|---|---:|---:|
| Standard root install, always-on | 0 (defect) | ~600 (budget cap ~800) |
| Interactive entry point, always-on | ~5,845 | ~5,845 (unchanged) |
| Pipeline LLM node sessions | 0 | 0 (unchanged — design invariant) |
| Expert delegation (on demand) | ~1.5k (Layer-1 only; body dead) | ~9.3k (Layer-1 + body + defenses) |

One honest caveat: dict-form agents spawned *from* a root session (e.g. `attractor-pipeline-runner`) may inherit the parent's composed context per the spawn-merge comment ("merges this agent's session: key onto the parent config"); worst case ≈ +600 tokens per spawned child. Build-order probe B2 verifies; if it inherits, that is the same cost every P2 bundle already accepts.

### 3.2 Q2 — agent registration: one expert, .md-defined, explicitly registered

Foundation gives exactly the mechanism needed (P4): a `.md` agent whose frontmatter carries the mount plan. **Merge the two experts into one definition owned by `agents/attractor-expert.md`:**

1. Add to its frontmatter (sibling of `meta:`):

```yaml
session:
  orchestrator:
    module: loop-agent
    source: ../modules/loop-agent        # anchor verified by probe B1; see below
    config:
      system_prompt_file: context/system-attractor-expert.md
```

2. Replace `behaviors/attractor-core.yaml:23-35`'s inline dict with:

```yaml
agents:
  include:
    - attractor:attractor-expert
```

Result: one name, one definition; Layer-1 = the persona file (**preserves the qa-02 fix path exactly** — same loop-agent, same `system_prompt_file`, now un-pinned), instruction = the 20 KB body + defenses transclusion; the explicit `session.orchestrator` keeps the anti-recursion rule intact for pipeline-parent spawns. `SKILL.md:344`'s delegation target and fallback both keep working; README:434's claim ("Sessions that compose attractor-core have access to attractor-expert") becomes fully true.

**Probe B1 (build order step 0):** verify the relative-source anchor for frontmatter `session:` blocks (candidates: `../modules/loop-agent` from `agents/`, or bundle-root-relative `modules/loop-agent`) by spawning the expert in a scratch local install and confirming (a) loop-agent mounts, (b) a canary line from the .md body appears in the child's instruction, (c) the Layer-1 file is served. **Fallback if frontmatter-relative sources prove unreliable:** keep the inline dict in attractor-core.yaml (its `../modules/loop-agent` anchor is the work-tracker-proven form) and fold the .md body + defenses into `context/system-attractor-expert.md` (Layer-1 grows to ~9.3k, expert-child-only); `agents/attractor-expert.md` then shrinks to frontmatter + pointer so the two can never drift.

**Rejected: registering the .md as a second agent name** (e.g. `attractor-expert-docs`) — two experts under near-identical names is the confusion the defect report warns about, and compose semantics ("later overrides earlier by name") make the single-name merge clean.

### 3.3 Q3 — the @main self-pin: relative sources, and the mirror becomes optional

| Option | (a) normal installs | (b) branch installs | (c) compat doctrine |
|---|---|---|---|
| **Keep `git+…@main#subdirectory=…`** | works | serves **main's** modules + guidance (the defect) | status quo; branch testing needs mirror-main |
| **Relative `../modules/X` / `modules/X`** ✅ | identical bytes — the cache snapshot at the installed ref contains `modules/`; resolution never leaves the snapshot | self-consistent: branch modules, branch `parents[3]` guidance | production-proven (work-tracker, terminal-tester, android-tester install via `git+` and run in this environment today); additive — no engine change |
| Namespaced module source (`attractor:modules/X`) | — | — | **rejected: no evidence the foundation loader supports this form for modules** (found for context/agents/skills only) |

**Design: flip every same-repo pin to a relative source.** Tranche 1 (the defect): `behaviors/attractor-core.yaml:28` → `source: ../modules/loop-agent`. Tranche 2 (same class, same PR, mechanical): `bundle.md`'s four agent orchestrator pins → `modules/loop-agent` / `modules/loop-pipeline`; `bundle.md`'s skills registration → `"@attractor:skills"` (work-tracker's exact form — otherwise a branch install serves **main's** attractorify SKILL.md, the same defect for skills); `behaviors/attractor-core.yaml`'s tool/hook pins; `bundles/*.yaml`, `agents/*.yaml`, `profiles/*.yaml` orchestrator pins. External pins (foundation, tool-skills module, providers) stay `@main` — they are different repos; that is what @main pins are for.

**If relative sources had failed:** the sanctioned alternative is the eval harness's existing dedicated-Gitea procedure (`run.sh` pushes the detached SHA to a mirror; the DTU's `url_rewrites` redirect `github.com/microsoft/amplifier-bundle-attractor` there, so @main pins resolve to the pushed content). Honestly weighed: it works, it is already documented, and it is the wrong permanent answer — it tests a *hybrid* only reachable through a bespoke harness, leaves every non-eval branch install silently wrong, and hides the defect it works around. It remains the harness's transport either way; it stops being *load-bearing for correctness*.

---

## 4. The proof plan

The fix's proof is the guidance-eval itself — and cause 3's fix is what lets the eval test the branch **directly**: `run.sh` pushes the branch SHA to the mirror, `bundle add git+…@<sha>` installs it, and with relative sources every module and context file comes from that snapshot. No mirror-main-as-branch hybrid; the fix enables its own proof.

**Run: all six scenarios.** The eval README names this exact case: *"A full six-scenario run is warranted when the change is broad — a bundle recomposition…"*

| Scenario | Baseline | Pass bar for this fix |
|---|---|---|
| `work-01-stale-release-notes` | FAIL (MC-C1: `/attractorify`/objective-runner/expert never named; foundation `/brainstorm` captured the ask) | **Must flip to PASS**: objective path named in-session, definition-of-done pressed |
| `work-02-twelve-step-pipeline` | FAIL (authored gateless chain; invented attributes) | **Must flip to PASS**: recipe-shaped ask named as such; no invented-attribute DOT authored |
| `qa-01-what-is-an-attractor` | PASS | must not regress |
| `qa-02-never-converges` | PASS post-defenses (reaches the loop-agent expert) | must not regress — the merged expert serves the same Layer-1 |
| `exemplar-01-sloppy-routable` / `exemplar-02-honest-redirect` | PASS | must not regress — **these double as the non-interference proof**: they run the objective-runner through real pipeline compositions the change must leave byte-identical |

Scoring is the instrument's own: a scenario fails if *any* cited criterion scores below 3 or *any* mechanical check fails; nothing averaged. Paste the results table + decisive transcript quotes into the PR (the §2 guidance-surfaces toll).

**An honest partial outcome, pre-named:** work-01's first reply may still be captured by foundation's mode-routing even with the awareness composed — the awareness competes with foundation's own always-on prompts. That outcome is measurable and citable: MC-C1 (the path is *named*) passes while the first-reply-timing criterion scores 2–3. Verdict in that case: the composition defect is fixed (steering is in-session and cited by the grader), and the residual is a foundation-interaction issue — filed as its own work item with the transcript quote, not spun as a full pass. Escalation lever if work-02 does not flip on the tripwire pointer alone: compose `dot-reference.md` into root too (+~2.7k tokens always-on) — a measured trade the maintainer makes on eval evidence, not taste.

---

## 5. Tier classification + toll (docs/QUALITY_PROTOCOL.md §3)

**Tier: Uncharted / extension.** The nlspec governs the engine — the coding-agent loop and pipeline semantics. Amplifier bundle composition is territory the spec does not address, and §3 explicitly reaches this far: "examples, guidance surfaces and process changes are classified by it too."

**Toll owed (per the §3 table):**
1. *Why the spec's silence is not a signal:* the spec defines what the agent and pipeline do, not how a host platform mounts guidance into interactive sessions; the guidance surfaces themselves already shipped and paid their tolls — this change makes the shipped bundle actually serve them on the documented install path.
2. *Additive and non-interfering — "a spec-conformant graph behaves identically":* zero engine-module code changes; the conformance matrix and its runner are untouched; every pipeline composition's *served content* is unchanged (root-only context placement; relative sources serve identical bytes at the installed ref); exemplar-01/02 assert it behaviorally.
3. *`specs/EXTENSIONS.md` entry in the same PR:* strictly read, §3 requires it; nothing here is a runtime graph-semantics extension, so the entry would be a short composition note. Proportionality flagged as open question #4 rather than silently skipped.

Plus the §2 **Guidance surfaces** row evidence: the full six-scenario eval results table + transcript quotes in the PR (section 4).

---

## 6. File-touch inventory + build order

**Step 0 — probes (before any edit):**
- **B1**: frontmatter `session:` + relative module source anchor — scratch local `bundle add` + spawn the expert; assert loop-agent mounts, canary line from .md body present, Layer-1 served. Decides §3.2 primary vs fallback.
- **B2**: does a dict-form agent spawned from a root session inherit the parent's `context:` files? (Token-cost accounting only; does not gate the design.)
- **B3**: local-path `amplifier bundle add` of the edited checkout; dump a session and assert the awareness block is present with `bundle_context_decl` provenance, and absent from an `attractor-pipeline` composition.

**Build order (each step leaves the tree shippable):**

| # | File | Change |
|---|---|---|
| 1 | `context/attractor-awareness.md` | NEW — five blocks per §3.1, hard cap 3.2 KB; lint check in PR quotes final byte/token count |
| 2 | `bundle.md` | add `context: include: [context/attractor-awareness.md]` |
| 3 | `behaviors/attractor-core.yaml` | loop-agent source → `../modules/loop-agent`; replace inline expert dict with `agents: include: [attractor:attractor-expert]` (per B1) |
| 4 | `agents/attractor-expert.md` | add frontmatter `session.orchestrator` block (per B1) |
| 5 | `bundle.md`, `behaviors/attractor-core.yaml`, `bundles/*.yaml`, `agents/*.yaml`, `profiles/*.yaml` | self-pin sweep: same-repo `git+…@main#subdirectory=modules/*` → relative; skills registration → `"@attractor:skills"` |
| 6 | `bundles/attractor-interactive.yaml` | fix the stale "pending namespaced-include support" comment; optionally move to `attractor:context/…` form (cosmetic; behavior identical) |
| 7 | `specs/EXTENSIONS.md` (+ README composition note) | the §5 toll entry; README:434 claim now fully true |
| 8 | Eval run | full six scenarios against the branch SHA, direct; results table into the PR |

Constraint check: `bundles/attractor-interactive.yaml` keeps working (its own includes untouched; step 6 cosmetic); **zero engine changes** (no `modules/**` code touched — step 5 edits YAML references only); the 5 practical lanes / objective-runner run as pipelines composed from `attractor-pipeline`/profiles, which include attractor-core, not root — no composition they see changes, and exemplar-01/02 assert it.

---

## 7. Open taste questions for the maintainer

1. **Always-on budget:** is ~600–800 tokens in every root session acceptable? (Ecosystem norm is 460–975.) And if work-02 doesn't flip on the pointer alone, is +2.7k for an always-on `dot-reference.md` an acceptable escalation, or should the tripwire get sharper instead?
2. **Expert merge shape:** .md-owns-everything (frontmatter `session:`, primary) vs YAML-dict + fold-body-into-Layer-1 (fallback) — any preference beyond what probe B1 decides?
3. **Self-pin sweep scope:** minimal (attractor-core.yaml only — the verified defect) vs the full same-repo sweep in one PR (recommended: same defect class, mechanical, one review)?
4. **`specs/EXTENSIONS.md` for a packaging change:** strict §3 reading says yes; proportional reading says the entry documents nothing a graph author can observe. Which reading governs?
5. **Root vs interactive convergence:** should the root bundle eventually mount `run_pipeline` (making root ≈ interactive)? The awareness wording (§3.1 block 5) is written for today's answer ("no") and would need one line changed if that flips.
6. **Interactive's own diet:** the interactive entry point still composes ~5.8k tokens always-on; out of scope here, but the awareness file is a ready replacement candidate if a diet is ever wanted.


---

## 8. As built: what shipped, and where it deviates

Sections 1-7 are the design as written *before* the build. This section records what the build
actually did and every place it departed from that design. Where they disagree, this section
describes the shipped bundle.

### 8.1 The deviation that matters: **two resolution classes**, and one pin that cannot be removed

§3.3 recommended flipping **every** same-repo `git+…@main` pin to a relative source, on the
strength of the P2 ecosystem survey (work-tracker et al.). That survey generalised one case too
far. Foundation resolves a module `source:` in **two different ways**, and only one of them can
be made relative:

| Class | Where | When resolved | Anchored on | Relative safe? |
|---|---|---|---|---|
| **A — parse-time** | `tools:` / `providers:` / `hooks:` list entries | at **parse** time, in `Bundle.from_dict` → `_validate_module_list` (foundation `bundle/_dataclass.py`: *"Resolve relative source paths to absolute (before composition can change base_path) — this fixes issue #190"*) | **the declaring file's own directory** | **YES** — under any composition |
| **B — late** | `session.orchestrator.source` (top-level, agent dicts, and agent-`.md` frontmatter) | at **prepare** time, via `FileSourceHandler(base_path=…)` | **the COMPOSED ROOT's `base_path`** — and `Bundle.compose` ends with `result.base_path = other.base_path`, so the *outermost* declaring bundle wins | **NO** — never, in a real session |

Class B is not merely fragile; in a real `amplifier` session it is always wrong, because the
composed root is the **app's own bundle**, not this one. That was measured, not reasoned: a first
build of this PR made bundle.md's four orchestrators and the expert's orchestrator relative, was
pushed, and the guidance eval installed it into a clean DTU the documented way
(`amplifier bundle add git+…@<branch>` + `amplifier bundle use attractor`). The session refused
to start:

```
5 of 117 modules failed to activate (strict mode):
  - loop-agent: File not found:
    /root/.local/share/uv/tools/amplifier/lib/python3.12/site-packages/
      amplifier_app_cli/_bundle/behaviors/modules/loop-agent        (x4)
  - loop-pipeline: File not found:
    .../amplifier_app_cli/_bundle/behaviors/modules/loop-pipeline
The session was not started because it would have been missing the capabilities above.
```

`.../amplifier_app_cli/_bundle/behaviors/` is the composed root's `base_path`. No relative path
written in this repo can reach this repo's snapshot from there, and foundation has no namespaced
module-source form (`attractor:modules/X`) to write instead — confirmed by reading
`sources/resolver.py`'s handler list (file / git / zip / http; no namespace handler).

Class A was verified the other way, in the same probe pass: with a synthetic foreign root
composing `behaviors/attractor-core.yaml`, `../modules/tool-report-outcome` resolved to the
absolute in-snapshot path, because parse-time resolution had already baked the declaring file's
directory in.

**Therefore the shipped sweep is split by class:**

- **Class A — flipped, everywhere (9):** `behaviors/attractor-core.yaml` (4), plus the
  `tools:`/`hooks:` self-pins in `bundles/attractor-interactive.yaml` (1),
  `profiles/attractor-e2e-anthropic.yaml` (2), `profiles/attractor-e2e-gemini.yaml` (2),
  `profiles/attractor-profile-openai.yaml` (1). *(9 module pins across 5 files.)*
- **Skills registration (1):** `"@attractor:skills"` — namespaced and ref-free, work-tracker's
  production form. Neither class: skills resolve through `source_base_paths`, always in-snapshot.
- **Class B — kept as `@main`, everywhere (34), each with a comment naming the measurement.**
  These are not oversights and a later contributor must not "finish the sweep": doing so is what
  produced the DTU failure quoted above.

**What this means for cause 3 (§1), stated plainly.** A branch install is now self-consistent
for every surface that resolves through the bundle namespace or through parse-time anchoring:

| Surface | Serves the branch on a branch install? | Mechanism |
|---|---|---|
| `context/attractor-awareness.md` (new, always-on) | **YES** | `context: include: attractor:context/…` → `source_base_paths` |
| `agents/attractor-expert.md` — the 18 KB knowledge body | **YES** | `agents: include: attractor:attractor-expert` → `source_base_paths` |
| `skills/attractorify/SKILL.md` | **YES** | `"@attractor:skills"` |
| `context/pipeline-awareness.md`, `context/dot-reference.md` | **YES** | `attractor:context/…` include + `@attractor:` mentions |
| `tool-report-outcome`, the three hooks, `tool-pipeline-run`, `tool-apply-patch` | **YES** | Class A relative sources |
| **loop-agent / loop-pipeline module code** | **NO — still `@main`** | Class B; no expressible alternative |
| **`context/system-attractor-expert.md` (the expert's Layer-1 persona)** | **NO — still `@main`** | rides with loop-agent: the module resolves a relative `system_prompt_file` against its own installed location (`parents[3]`) |

The last two rows are the honest residual. They are also the two this PR does not modify. The
remedy is not in this repo: it needs a foundation-level ref-free same-repo module source (a
`attractor:modules/X` form, or parse-time resolution extended to `session.orchestrator`). That is
filed as follow-up work, with the DTU output above as its evidence.

### 8.2 Verified composition behavior of the shipped tree

Three compositions, probed against the built worktree with the real foundation (no LLM calls):

| Composition | expert registered (body served) | `loop-agent` resolves |
|---|---|---|
| **Root install** — `bundle add git+…@<ref>` + `bundle use attractor` | YES, 18,101-char body | YES |
| **README Quick Start** — user bundle `includes:` a profile | YES, 18,101-char body | YES |
| **`attractor-core` alone under a foreign root** | YES, 18,101-char body | YES |

All three hold because every orchestrator source stayed a resolvable `git+` URL. The expert
merge changes *which file defines the agent*, not whether its orchestrator can be found.

### 8.3 Other deviations from §6's build order

- **Step 1 (awareness file):** shipped at **3,246 bytes / 796 tokens** (cl100k) — inside the
  ≤ 3.2 KB cap, above the ~2.4 KB stretch target. Five blocks as specified; it does **not**
  mention `run_pipeline` (the root bundle does not mount `tool-pipeline-run`), pointing at the
  `attractor` CLI and the `attractor-pipeline-runner` agent instead.
- **Step 2 (`bundle.md`):** the `context:` entry uses the **namespaced** `attractor:context/…`
  form rather than §3.1's plain relative one — P4 showed namespaced includes are implemented and
  in production, and the namespaced form is the one that survives being composed from anywhere
  (the same property Class B lacks). Root `bundle.md` *also* registers the expert, so the
  standard install gets it even if attractor-core is ever recomposed.
- **Step 3/4 (the expert merge):** shipped as §3.2's **primary** shape (the `.md` owns
  everything) — probe B1 confirmed foundation loads `session:` from agent-`.md` frontmatter and
  the 18 KB body as `instruction`. The fallback shape was not needed. Two nuances: the registered
  agent name is **`attractor:attractor-expert`** (namespaced, as `agents: include:` always
  produces) — exactly the name `skills/attractorify/SKILL.md` and the awareness file already tell
  callers to delegate to; and the orchestrator source in that frontmatter is a Class B pin, so it
  stayed `@main` per §8.1.
- **Step 6 (interactive):** its two heavy context includes moved to the namespaced
  `attractor:context/…` form, and the stale "pending foundation namespaced-include support"
  comment was replaced with what P4 found.
- **Step 8 (eval):** run as specified — all six scenarios, branch installed directly, no
  mirror-main hybrid. Results in the PR body.
