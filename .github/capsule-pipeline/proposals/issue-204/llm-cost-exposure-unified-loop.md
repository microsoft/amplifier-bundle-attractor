---
id: llm-cost-exposure-unified-loop
title: "LLM cost exposure on the unified_llm + loop-pipeline stack"
red_signal: AC-1: UNMET
criteria_digest: b19fa073d57bec31af0a0a5707062dba9192a7bfb1dfa746703984d3693934f3
base_sha: b88964330b707473e308f569db374b30a2608247
later_commit: e3f57c05b41b63f2a1fa11bef7198e3185b01a6a
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

# Feature capsule: LLM cost exposure on the unified_llm + loop-pipeline stack

Oracle provenance:
  Source: https://github.com/microsoft/amplifier-module-provider-anthropic
  File: amplifier_module_provider_anthropic/_cost.py
  Commit: 68434cd3d0b3666d36902aea38bf8592f81c5104
  Vendored as: oracle.py (plain source, beside this file)

---

## Goal

Expose per-call LLM cost on the `unified_llm` + `loop-pipeline` stack so that
pipeline authors can observe dollar cost from `Client.complete()`, from the
streaming accumulator, and from the `provider:response` event emitted by
`DirectProviderBackend` — consistent with how cost is reported on other stacks.

The demanded end state, in the criteria's own terms:

- `unified_llm.compute_cost(model, input_tokens, output_tokens, *, cache_read_tokens=0, cache_write_tokens=0, speed=None)` is importable from `unified_llm` and returns `decimal.Decimal | None`.
- A `unified_llm.Client.complete()` response carries `usage.cost_usd` (`Decimal | None`), including through the streaming accumulator.
- `Usage` addition propagates `None` cost correctly: any `None` operand yields a `None` total.
- `DirectProviderBackend`'s `provider:response` event payload carries a top-level `"cost_usd"` key.
- Existing token-field arithmetic and existing `provider:response` payload structure are unchanged.

---

## Why this matters

Pipeline authors using `unified_llm.Client` + `DirectProviderBackend` have no
first-class way to obtain dollar cost today. They would have to reach into
private provider internals or maintain divergent per-app price tables. This
feature closes that gap with a public, oracle-consistent cost surface.

---

## Definition of done

Every AC below is listed with what the gate checks for it.

### AC-1 — `unified_llm.compute_cost` importable, multi-model golden parity

`unified_llm.compute_cost(model, input_tokens, output_tokens, *, cache_read_tokens=0, cache_write_tokens=0, speed=None)` is importable from `unified_llm` and returns `decimal.Decimal | None`.

Golden parity is MULTI-MODEL: at gate runtime the probe samples model ids from the pinned oracle's own rate table — at least 3 models, which MUST include `claude-sonnet-4-5-20250929` and at least one fast-eligible model per the oracle's own fast-eligibility data — and for EVERY sampled model the result EXACTLY equals the oracle's `compute_cost` for the same inputs across:
- input-only
- input+output
- cache_read-heavy
- cache_write-heavy
- (on the fast-eligible model, with `speed="fast"`) fast-mode

A probe that exercises exactly one model, or whose fast-mode case never runs on a fast-eligible model, does not satisfy this criterion.

**Gate check:** Import `unified_llm.compute_cost`. At runtime, read the oracle's `_RATES` keys; select `claude-sonnet-4-5-20250929`, one fast-eligible model from oracle's `_FAST_ELIGIBLE_MODELS`, and one additional model. Generate token counts at runtime (not fixed literals) using a seeded RNG. For each model and each case, assert `isinstance(result, Decimal)` and `result == oracle_expected` (oracle-derived, asserted Decimal before comparison). For the fast-eligible model with `speed="fast"`, assert the fast-mode result equals the oracle's fast-mode result.

**Observability at base SHA:** `from unified_llm import compute_cost` raises `ImportError`. UNMET.

---

### AC-2 — Honest None for unknown model and for model with None rate dimension

For a model id with no rate coverage, `compute_cost` returns `None`: never 0, never a partial computation, never a float (`Decimal` type asserted for non-None results).

For a case requiring a rate dimension the table lacks (a `ModelInfo` field that is `None` in catalog data), `compute_cost` returns `None`.

Every AC-2 probe reaches the system under test through public, documented surfaces only — no underscore-prefixed imports from, and no monkey-patching of, catalog or `unified_llm` internals. If the public surface cannot present the required entry, the gate fails loudly as gate-infrastructure (never a license to reach private). Introspecting the gate's own vendored oracle is permitted.

**Gate check (unknown model):** Call `unified_llm.compute_cost` with a model id confirmed absent from oracle's `_RATES`. Assert result is `None`, not `0`, not a `float`.

**Gate check (None rate dimension):** Call `unified_llm.list_models()` to find a model whose `input_cost_per_million` or `output_cost_per_million` is `None`. Assert `compute_cost` returns `None` for that model. If no such model exists in the catalog, exit loudly as gate-infrastructure — this is never a pass.

**Observability at base SHA:** No `compute_cost` exists. No model in `models.json` has `None` cost fields. UNMET.

---

### AC-3 — `Client.complete()` response carries `usage.cost_usd` (Decimal | None), including via streaming accumulator

A `unified_llm.Client.complete()` response carries `usage.cost_usd` consistent with AC-1 (`Decimal | None`), including when assembled through the streaming accumulator.

The probe's mock adapter responses MUST carry only the model id and token counts — NEVER a pre-set `cost_usd` value. The assertion is that the returned response (and the stream-accumulated response) carries the oracle-consistent `Decimal` computed from those tokens. Which layer between adapter and caller computes the value remains the implementer's choice — the probe binds the public outcome, not the computing layer.

**Gate check (complete path):** Build a mock `ProviderAdapter` that returns a `Response` with a known model id (in oracle's `_RATES`) and runtime-generated token counts; no `cost_usd` on the adapter response. Construct a `Client` with this adapter. Call `await client.complete(request)`. Assert `response.usage.cost_usd` is a `Decimal` and equals the oracle's `compute_cost` for those tokens.

**Gate check (streaming accumulator path):** Build a mock adapter that yields `StreamEvent` objects including a `FINISH` event with a `Usage` carrying the same model id and token counts (no `cost_usd`). Consume via `StreamAccumulator`. Call `accumulator.response()`. Assert `response.usage.cost_usd` equals the oracle's expected value.

**Observability at base SHA:** `Usage` has no `cost_usd` field. `Response` has no `cost_usd`. UNMET.

---

### AC-4 — `DirectProviderBackend` path: `provider:response` event carries top-level `cost_usd` key

Loop-pipeline's `provider:response` event payload carries a `"cost_usd"` key on the `DirectProviderBackend` path, equal to the response usage's `cost_usd` (`None` is a legal value; an absent key is not).

`"cost_usd"` means `cost_usd` is a key of the `provider:response` event payload itself (`payload['cost_usd']`), never only nested inside a sub-dict such as `usage`. A probe that accepts a nested-only value does not satisfy this criterion.

**Gate check:** Create a `DirectProviderBackend` with a mock unified_llm client. Run two sub-cases: (A) the mock client returns a response whose `usage.cost_usd` is a runtime-generated `Decimal`; assert `payload['cost_usd']` equals that same `Decimal` (propagation, not recomputation). (B) the mock client returns a response whose `usage.cost_usd` is `None`; assert `'cost_usd' in payload` and `payload['cost_usd'] is None` (key must be present even when value is `None`). Assert in both cases that `cost_usd` is at the top level of the payload, never only nested inside `payload['usage']`.

**Observability at base SHA:** `provider:response` payload in `DirectProviderBackend.run()` contains `usage` dict with token fields but no top-level `cost_usd` key. UNMET.

---

### AC-5 — `Usage` addition: any `None` operand yields `None` total `cost_usd`

Summing `Usage` objects where any operand's `cost_usd` is `None` yields a total with `cost_usd` `None` (never `TypeError`, never a number).

**Gate check:** Probe all four combinations:
- `Decimal + None` → `None`
- `None + Decimal` → `None`
- `None + None` → `None`
- `Decimal + Decimal` → `Decimal` sum

Assert no `TypeError` is raised in any case.

**Observability at base SHA:** `Usage` has no `cost_usd` field; `__add__` does not handle it. UNMET.

---

### AC-6 [guard] — Existing token-field Usage arithmetic and provider:response token fields unchanged

Existing token-field `Usage` arithmetic and `provider:response` token fields are unchanged for callers that ignore cost.

**Gate check:** Assert `Usage.__add__` for `input_tokens`, `output_tokens`, `total_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_write_tokens` sums as before, including non-None operands for both cache fields (e.g. `cache_read_tokens=7 + cache_read_tokens=9 → 16`; `cache_write_tokens=8 + cache_write_tokens=10 → 18`). Assert `provider:response` payload from `DirectProviderBackend` still carries `usage.input_tokens == 10`, `usage.output_tokens == 20`, `usage.total_tokens == 30`, `usage.cache_read_tokens == 13`, `usage.cache_write_tokens == 17`, `finish_reason`, `step_count` — the mock Usage is constructed with these specific non-None values so that a feature build that silently drops or corrupts any of those fields in the emitted payload is caught. Adding `cost_usd` must not remove, rename, or alter any existing key.

**Observability at base SHA:** MET (the feature is unbuilt; existing behavior is intact). The gate must confirm these pass after the feature is built.

---

## Non-goals

Per criteria (Scope OUT, each with a follow-up home):

- **MAP/REDUCE + per-model rollups** — consumer/daemon layer; deferred, not dropped.
- **Spawn-path cost bridging via `AmplifierBackend._run_with_spawn`** — any spawn-containing total must surface as `None`/flagged, never a confident undercount; deferred to a follow-up.
- **Provider-tier `_cost.py` migration to a shared source** — v2 work; deferred, not dropped.

## Delegated (implementer's choice)

Per criteria (Delegated line):

- **The internal home of the rate data** (unified_llm subpackage vs standalone package) is the implementer's choice. The criteria bind only the public surfaces named above.

---

## Gate structure

The gate is `DEFINITION.verify.sh`. It:

1. Resolves the repo root from cwd (no arguments; invoked as `bash .ai/capsule/DEFINITION.verify.sh` from the repo root).
2. Loads the oracle from `oracle.py` (sibling path, plain source). If the oracle fails to import, exits >=2 as infrastructure failure — never a pass.
3. Installs the system under test from the repository using `sys.path.insert` (never `pip install`).
4. Runs each AC probe as a numbered Python subprocess, capturing pass/fail per criterion.
5. Writes `.ai/census` with exactly one `AC-<n>: MET` or `AC-<n>: UNMET` row per ingested AC-ID.
6. Exits 0 only if every census row is MET; exits 1 when any row is UNMET; exits >=2 only on genuine infrastructure problems.

The gate does NOT install any package from ambient caches or runner-specific paths. It uses `sys.path.insert` to make the subject importable from the repo tree only.

---

## Open questions for the maintainer

See `.ai/brief.md` RISK-2 and RISK-4 for two unresolved design forks:

1. **AC-2 "None rate dimension" public surface** (RISK-2): How should the gate exercise the "rate dimension is None" case via public surfaces only? Will the implementation add a model with `null` cost fields to `models.json`, expose a public `ModelInfo`-accepting overload of `compute_cost`, or use another mechanism?

2. **AC-4 scope** (RISK-4): Does `cost_usd` in `provider:response` also apply to `AmplifierBackend._run_with_tool_loop()` (Path B), or only to `DirectProviderBackend`?
