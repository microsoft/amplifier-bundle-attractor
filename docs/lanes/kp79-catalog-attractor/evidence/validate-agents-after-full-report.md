# Agent Validation Report

> **REDACTED:** absolute host paths replaced with `<repo>` / `<amplifier-cache>` per
> `docs/OPERATIONS.md` section 7 (leak-defense layers 1 and 2). Content otherwise verbatim.

## Executive Summary

- **Overall Verdict**: ⚠️ **PASS WITH WARNINGS**
- **Repository**: `<repo>`
- **Agents Found**: 1 total across 1 location
- **Quality Breakdown**: 0 good, 0 polish, **1 needs_work**, 0 critical
- **Issues**: **0 errors, 1 warning, 1 suggestion**

The single warning is a `tools:`-declaration finding, **not** a description-quality finding. The description itself meets the standard as written and requires zero bytes of change.

### Coverage

```
Scanned: <repo>/**/agents/*.md, <repo>/agents/*.md, excluding .git, .venv, docs, node_modules, test-fixtures, tests
Candidates: 1 files matched the scan
Classified as agents: 1 across 1 locations
Classified as NON-agents: 0 ({})
Classifier: frontmatter declares a top-level `meta:` key (docs/AGENT_AUTHORING.md)
```

| Location | Agents |
|----------|--------|
| agents/  | 1      |

No NON-AGENTS table: `non_agent_count` is 0 — every file matching the scan patterns was classified as an agent.

> **Scope note for the reader:** the seven `agents/*.yaml` files in this repo are agent-*profile bundles* (composition units), not agent definitions. They do not match the `agents/*.md` scan pattern and are correctly outside this run. They are not under-counted agents.

## Quality Classification Summary

| Agent | Quality | Triggers | No Examples | Tools | Model Role | Description |
|-------|---------|----------|-------------|-------|------------|-------------|
| attractor-expert | needs_work | ✅ (`DO NOT`) | ✅ (0 examples, 0 commentary — required shape) | ⚠️ implicit | ⚠️ absent | 597 chars / 149 tok |

## Model Role Coverage

- **Model Role Coverage**: **0/1** agents have `model_role` declared

**Model Role Distribution:**

| Role | Count |
|------|-------|
| _(none declared)_ | 0 |

**Agents Without model_role:**

- `attractor-expert` — will use the session default for model routing.

**Invalid/Deprecated Role Warnings:**

None. No invalid or deprecated `model_role` values were found.

## Detailed Findings

### Errors (Must Fix) — HIGH Priority

**None.** Structural validation reports `errors: 0`. Frontmatter parses, required fields are present, and the description carries **zero** `<example>`/`<commentary>` blocks — the required shape since v1.4.0.

### Warnings (Should Fix) — MEDIUM Priority

```
[MEDIUM] Agent: attractor-expert - NO_TOOLS_SECTION (composition-dependent, genuine)
Problem: The agent declares no `tools:` at any level and inherits from the
         spawning composition. Its own body makes tool use a DELIVERY
         OBLIGATION -- "The graph is not delivered until `dot-runner lint
         <file>` has been RUN on it and its verdict is in your reply"
         (agents/attractor-expert.md:313-318), plus `dot-runner trace
         <run_dir>` for debugging (:577-581). Both require bash; authoring
         and reading .dot graphs require filesystem and search.

         Inheritance holds for three of four compositions and fails for the
         fourth:
           bundle.md root ........................ fs/bash/search .... OK
           bundles/attractor-interactive.yaml:53-61 fs/bash/search .... OK
           agents/attractor-agent-*.yaml:43-51 .... fs/bash/search .... OK
           behaviors/attractor-core.yaml ALONE .... report-outcome +
                                                    skills ONLY ....... FAILS

         behaviors/attractor-core.yaml:55-63 registers this agent
         UNCONDITIONALLY, and that same file documents behavior-only installs
         as a supported path (:20-25). In that install the expert mounts with
         no bash, no filesystem, no search -- and degrades to prose about a
         file it cannot read, which is the exact failure it exists to prevent.
         The body's escape hatch ("If you cannot run the linter in your
         context, say that in the handback", :322-326) is the symptom, not
         the fix.

Before: agents/attractor-expert.md frontmatter declares `meta:` and
        `session.orchestrator` only (:1-45). No `tools:` key.

After:  Add at the top level of the frontmatter, after the `session:` block,
        before the closing `---`:

        tools:
          # attractor-expert's contract REQUIRES tool access: it must run
          # `dot-runner lint <file>` before delivering a graph and
          # `dot-runner trace <run_dir>` when debugging.  Declared here
          # rather than inherited because behaviors/attractor-core.yaml
          # registers this agent into behavior-only installs that mount
          # neither bash nor filesystem.  These are EXTERNAL modules, so
          # `git+...@main` is correct -- the no-self-pin rule in
          # behaviors/attractor-core.yaml governs same-repo modules only.
          - module: tool-filesystem
            source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
          - module: tool-bash
            source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
            config:
              timeout: 120
          - module: tool-search
            source: git+https://github.com/microsoft/amplifier-module-tool-search@main

        Sources and `timeout: 120` mirror agents/attractor-agent-anthropic.yaml:43-51
        so the expert's shell budget matches the coding agents'.
```

**⚠️ Two honest gaps in that remediation — the caller decides, this report does not assert them as verified:**

1. **Unverified:** that the loader honors a top-level `tools:` in an agent-definition `.md` and merges it onto the spawned session. Sibling `agents/*.yaml` files declare `tools:` at that level, and the structural validator's own remediation text says "in frontmatter or bundle.yaml" — but no spawn was executed to confirm it for the `.md` form. **Verification that would settle it:** DTU install of a behavior-only composition (`behaviors/attractor-core` alone), spawn `attractor-expert`, ask it to run `dot-runner lint` on a fixture `.dot`, confirm bash is mounted. If the `.md` form is not honored, the fallback is declaring the three tools in `behaviors/attractor-core.yaml`'s `tools:` block — which fixes the same composition but widens the tool surface of every pipeline LLM node, a trade that file's own comments (`:20-25`) argue against.
2. **Scope:** this is a **mount-behavior change**, not a frontmatter-description change. `tests/test_orchestrator_source_pin_guard.py` scans orchestrator-class sources only, so new `tools:` pins will not trip it — but this repo's verification gradient (AGENTS.md) puts agent-profile changes behind root guards, and a change to what an agent can *do* warrants the live check above before it ships. **It does not belong in a frontmatter-description-hygiene changeset; it wants its own PR.**

### Suggestions (Consider) — LOW Priority

```
[LOW] Agent: attractor-expert - no model_role declared
Current: (absent) -- model routing falls back to the session default
Improved: model_role: reasoning

attractor-expert is a consultant that reasons about routing semantics,
substitution, and verdict contracts. `reasoning` is defensible. This is a
routing/cost change, not a description change, and like the tools finding it
is out of scope for a catalog-hygiene pass.
```

**No suggestion is offered on the description text.** Assessed against both criteria this phase owns, it passes:

1. **WHEN clause — PASS.** Not a bare imperative. It states a decision rule and enumerates the qualifying contexts: *"USE WHEN work touches an Attractor pipeline: authoring/editing .dot graphs; debugging failures or unexpected routing (edge selection, conditions, outcomes); picking a pattern (linear, parallel fan-out/fan-in, conditional); fidelity modes, model stylesheets, handlers, bundle config; Python integration (llm-direct vs spawn workers)."* It carries an explicit negative boundary — *"DO NOT USE WHEN no pipeline or .dot graph is involved"* — and a timing rule (*"consult BEFORE delegating .dot work, mid-build, and review"*). A router gets a deciding factor, an exclusion, and a cadence.
2. **WHAT guidance — PASS.** Stated with a differentiator rather than a label: *"Owns the SHIPPED engine's runtime semantics (routing, substitution, verdict contract, fail-loud), not just DOT syntax — which generic builders lack."* That answers the question a router actually has: why this agent instead of a generic builder.

**Explicit instruction to any downstream editor: leave `meta.description` byte-identical. 597 → 597, delta 0.**

⚠️ **Hazard if it is edited anyway:** at 597 chars it sits **3 bytes** under the ≤~600 budget. A "tightening" pass will be tempted to drop an enumerated trigger context to buy headroom. Each item in that list — routing/edge-selection debugging, pattern selection, fidelity modes, model stylesheets, handlers, bundle config, llm-direct vs spawn workers — is a **distinct routing fact**. Dropping one is a mis-route that surfaces later as *"it didn't use the right thing"* with nobody tracing it back here. Do not trade a routing fact for byte headroom that buys nothing. An edit that exists to produce a diff is worse than no edit.

## Remediation Priority

| Priority | Item | Action | Ships where |
|---|---|---|---|
| **HIGH** | — | None. Zero structural errors. | — |
| **MEDIUM** | `attractor-expert` NO_TOOLS_SECTION | Add explicit `tools:` block (above), **after** the DTU verification of the `.md`-form merge | **Separate PR** — mount-behavior change |
| **LOW** | `attractor-expert` no `model_role` | Consider `model_role: reasoning` | Separate PR — routing/cost change |
| **NONE** | `attractor-expert` description | **Make no edit.** Byte-identical, 597 → 597, delta 0, no facts dropped. | — |

**Next step for a catalog-hygiene changeset on this branch: zero description bytes changed for `attractor-expert`.** The fidelity-table row is `stock == lean, 597 → 597, delta 0, no facts dropped`. Both open warnings are real but are capability/routing changes that a description edit cannot resolve; each deserves its own PR with its own evidence.

## Metadata

- **Validated**: 2026-09-07
- **Recipe**: validate-agents v1.7.0
- **Discovery scope**: repo-wide `agents/*.md`. `scan_patterns`: `<repo>/**/agents/*.md`, `<repo>/agents/*.md`. `excluded_parts`: `.git`, `.venv`, `docs`, `node_modules`, `test-fixtures`, `tests`.
- **Agents discovered**: **1** total across the locations in `location_counts` — `agents/`: 1.
- **Classification**: `candidates_scanned`: **1**. `classifier`: *frontmatter declares a top-level `meta:` key (docs/AGENT_AUTHORING.md)*, mode `yaml`. `non_agent_count`: **0**; `non_agents_found` is empty, so there are no excluded-file rows to repeat. 1 file matched the scan, 1 was an agent, 0 were not.
- **Quality Thresholds**: see `foundation:context/shared/description-authoring-principles.md` (canonical — not restated here). Gates: no structural errors (including any `<example>`/`<commentary>` block in the description — rejected entirely per V3, not merely capped), explicit tools section, description ≥ 100 chars, description token budget (provisional WARN >300 tok / ERROR >600 tok). MUST/ALWAYS/REQUIRED/PROACTIVELY keyword presence is reported as a metric, not a gate.
  - Measured for `attractor-expert`: 597 chars / **149 tok** — well inside the token budget; 0 examples; 0 commentary; 1 absolute keyword (`DO NOT`); `has_strong_trigger: true`.
- **Severity Guide**:
  - **ERROR (critical)**: Invalid YAML, missing required fields, or any `<example>`/`<commentary>` block in the description (will break, or is rejected per V3)
  - **WARNING (needs_work)**: No explicit tools, relying on inheritance, or description over budget (may break or bloat every session)
  - **SUGGESTION (polish)**: Missing WHEN deciding factor (quality improvement)