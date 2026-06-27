# Design: Profile-Owned Layer-1 System Prompt

**Status:** Draft (for review — no code yet)
**Scope:** `modules/loop-agent`, attractor agent YAMLs + profiles, one `loop-pipeline` threading change. **Not** app-cli / foundation / the shared spawn contract.

## Problem

Pipeline node agents run with a **stub** Layer-1 system prompt (`"You are a coding agent."`), not their provider-aligned base prompt — causing hallucination + over-fragmentation in synthesis. Proven empirically: a real wiki-weaver ingest logs `Layer-1 base prompt is empty` ×18–31, and a raw-LLM-request capture of a spawned node confirms only the stub + env + tool catalog reaches the model.

Root cause (confirmed via code, not inference): the base prompt is sourced from a **`context.include` side-file** (`agents/attractor-agent-<prov>.yaml:50-51` → `context/system-<prov>.md`) and delivered through foundation's `_system_prompt_factory`. That factory is only wired when the child bundle's context is populated through the spawn — which the post-#74 inline overlays drop. So the base prompt survives only one spawn shape and silently degrades to a stub everywhere else.

## Spec grounding

The attractor nl-spec already answers "who owns the system prompt" — and it is **not** the orchestration layer:

- **Attractor-orchestration deliberately abstains** (mechanism, like the kernel): `attractor-spec.md:56` "Attractor defines the orchestration layer… does NOT require any specific LLM integration"; `:58` "What that backend does internally is entirely up to the implementor." Attractor knows only the per-node **task** prompt (`:151`).
- **The base system prompt is first-class one layer down, in the backend's `ProviderProfile`**: `coding-agent-loop-spec.md:469` "a 1:1 copy of the provider's reference agent — the exact same system prompt… byte for byte"; `:1004` "Each profile supplies its own base prompt." Project/context files are an **additive** layer (4), not the base (`:991-998`).
- **Known impl gap** (the repo's own reviews): `docs/reports/adversarial-spec-review.md:99-103` and `docs/reports/spec-gap-analysis-v2.md:48,68-70` — "No `ProviderProfile` class exists; system-prompt assembly is hardcoded."

So the base prompt's correct home is the **provider profile**, delivered as loop-agent's **Layer-1 `SessionConfig.system_prompt`** — a config value — **distinct from** the node task-prompt channel. (Note: the V_B eval — inline `system_prompt` in orchestrator config — accidentally hit this exact channel and drove `Layer-1 empty → 0`; it just authored the value as duplicated inline text instead of a profile-owned asset.)

## The contract (what "first-class" means here)

| # | Property | Spec anchor | Notes |
|---|----------|-------------|-------|
| 1 | Provider base prompt is a **profile-owned, versioned** asset | `coding-agent-loop:469,1004` (versioning is net-new) | One home, not a per-pipeline include |
| 2 | **OOTB, zero glue** for consumers (wiki-weaver, dot-graph) | implied at profile layer `:56,672` | composing a pipeline yields prompted agents |
| 3 | Delivered via the **canonical Layer-1 channel** (`SessionConfig.system_prompt`), spawn-path-agnostic | `:991,1004` (corrected: profile base, **not** the task/instruction channel) | works under app-cli static-inject **and** foundation factory |
| 4 | **Overridable via composition** without forking | `:56,999` (strongest match) | compose a different profile, or Layer-5 override |
| 5 | **Fail-loud** on empty/stub base | **contradicts** `attractor-spec.md:668-670,1408` (fail-soft) | deliberate strengthening — requires a spec note |

## Design

**A. Base prompt = profile-owned → loop-agent Layer-1.**
loop-agent loads a **profile-declared base-prompt** directly into `SessionConfig.system_prompt` (`config.py:39`). Insertion point: `__init__.py:113-137` (replace the factory-resolution block with a direct read of the profile's base-prompt path). Remove `context.include: ../context/system-<prov>.md` (the **base**) from the agent YAMLs + profiles. Consequence (confirmed): with no base `context.include`, foundation registers no base factory (`_prepared.py:656` guard), so `context._system_prompt_factory` is `None` and `config.system_prompt` flows straight through as Layer-1 — single owner, no precedence fight. `context.include` **remains** for genuine additive context (`@AGENTS.md` etc.), Layer 4 only.

**B. Runtime (per-invocation) override = Layer-5 `user_instructions` via the existing channel.**
`orchestrator_config` is already an end-to-end per-invocation passthrough (loop-pipeline → spawn_fn → wiki-weaver `prepared.spawn` / app-cli `spawn_sub_session` → `session.orchestrator.config` → loop-agent `SessionConfig`). loop-agent already reads `user_instructions` (`config.py:40`) as Layer-5 (highest precedence, `system_prompt.py:93-95`). One small change: `loop-pipeline/backend.py` `_run_with_spawn` adds `user_instructions` (and/or `system_prompt_file`) to the per-spawn `orchestrator_config` dict (`:421-428`), sourced from `node.attrs` (per-graph) and/or `PipelineContext` (per-run, caller-supplied). **No app-cli / foundation / shared-contract change.**

**C. Fail-loud.** An empty Layer-1 base for an LLM node is an error (or loud, non-silent failure), not a stub fallback. Deliberate departure from the spec's fail-soft posture (`agent_session.py:543-550`, `attractor-spec.md:668-670,1408`).

## Layering decision

**Option A (chosen): spec-faithful — fix lives in loop-agent/profile.** Stays inside the `coding-agent-loop`/backend layer that the spec says owns prompts; does **not** cross `attractor-spec.md §1.4` ("backend internals are up to the implementor").
**Option B (rejected): pull prompt ownership up into attractor-orchestration** — amends the spec's layering, larger blast radius, no benefit over A.

## Blast radius

- **Changes:** `loop-agent/__init__.py` (:113-137); `agents/attractor-agent-{anthropic,openai,gemini}.yaml` + `profiles/attractor-profile-*.yaml` (drop base `context.include`, add profile-owned base ref); `loop-pipeline/backend.py` (:421-428, runtime threading); optional `loop-agent/config.py` (`system_prompt_file` key).
- **Unchanged:** `system_prompt.py` 5-layer assembler (already reads Layer-1 from `config.system_prompt`); app-cli `spawn_sub_session`; foundation `PreparedBundle`; the shared spawn contract.
- **Consumers:** wiki-weaver, dot-graph benefit OOTB with **zero** changes. `pipeline-runner.yaml` agents share the same YAMLs — must change in lockstep.

## Verification (evals-driven — proof on raw requests, not unit-green)

1. **E1 OOTB delivery (DTU):** wiki-weaver ingest → `Layer-1 base prompt is empty` count **= 0**, AND the spawned node's **raw LLM request** system prompt contains the provider base sentinel.
2. **E2 runtime override (DTU):** caller supplies a per-run `user_instructions` with a unique sentinel → the spawned node's raw request shows it present **and** highest-precedence (base still present, override appended last).
3. **E3 fail-loud:** a node with no resolvable base → errors loudly; no silent stub.

(Two prior "unit-green" fixes passed tests but failed the DTU — so every claim here is gated on a raw-request capture, the method that finally told the truth.)

## Open questions / spec amendments

1. **Fail-loud** changes attractor's documented fail-soft behavior (`:668-670,1408`) → amend the spec, or scope fail-loud to LLM nodes only.
2. **`ProviderProfile` object:** keep the declarative (YAML profile) representation, or introduce the actual object the spec describes and the gap analyses flag as missing? (This design works either way; the base-prompt asset just needs an owner.)
3. **Prompt versioning** (contract #1) is net-new surface — neither spec blesses nor forbids it.
