---
id: cost-exposure-unified-llm-loop-pipeline
title: Per-call cost exposure on the unified_llm + loop-pipeline stack
red_signal: AC-1: UNMET
criteria_digest: 93dec259993c06c0ebcd88f8b8b991fa618b5fbedddbfef94e04804c6a320ee2
base_sha: 1c1eb1bcef82fbbf88914977687d39b374fadb60
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

Expose a public, consistent cost-computation surface on the `unified_llm` + loop-pipeline stack so that any caller using `unified_llm.Client` and `DirectProviderBackend` can obtain per-call USD cost without reaching into private provider internals or maintaining a divergent rate table.

Concretely:

- `unified_llm.compute_cost(model, input_tokens, output_tokens, *, cache_read_tokens=0, cache_write_tokens=0, speed=None)` is importable from `unified_llm` and returns `decimal.Decimal | None`.
- `unified_llm.Usage` gains a `cost_usd: Decimal | None` field, populated through both the non-streaming (`complete()`) and streaming (`StreamAccumulator`) response paths.
- The loop-pipeline's `provider:response` event payload carries a top-level `"cost_usd"` key on the `DirectProviderBackend` path.
- `Usage.__add__` propagates `cost_usd` with a None-absorbing rule (any None operand → None total).
- Existing token-field `Usage` arithmetic and `provider:response` token fields are unchanged.

## Why this matters

Apps built on `unified_llm.Client` + `DirectProviderBackend` currently have no first-class, consistent way to obtain dollar cost. The only options are to reach into private `_cost.py` internals (not public API) or maintain a divergent per-app price table. This feature closes the gap so every loop-pipeline app gets platform-consistent cost for free.

## Definition of done

| AC-ID | What the gate checks |
|---|---|
| AC-1 | `from unified_llm import compute_cost` succeeds. `compute_cost` is called with runtime-generated token counts (not fixed literals) for `claude-sonnet-4-5-20250929` across five case families: input-only, input+output, cache_read-heavy, cache_write-heavy, and fast-mode (speed='fast'). For each case, the result is compared to the value returned by the provider oracle (`amplifier_module_provider_anthropic._cost.compute_cost`) called independently with the same token counts. The oracle-derived expected value is asserted to be `Decimal` before comparison. The SUT result must be `Decimal`, must equal the oracle value exactly. |
| AC-2 | `compute_cost` returns `None` (not 0, not a float, not a partial Decimal) for (a) a runtime-generated unknown model ID with no rate coverage, and (b) a model whose required rate dimension is `None` — exercised by injecting a synthetic `ModelInfo` with `input_cost_per_million=None` into the catalog, confirming its presence via `get_model_info(synthetic_id)` (the public catalog surface), then calling `compute_cost(synthetic_id, inp, out)` and asserting `result is None` unconditionally. The synthetic model ID is generated at gate runtime from random alphanumerics. |
| AC-3 | A real `unified_llm.Client.complete()` call (using a fake `ProviderAdapter` that returns a `Response` with `Usage` containing no pre-populated `cost_usd`) produces a response whose `usage.cost_usd` equals the oracle value for the same model and runtime-generated token counts. Separately, a real `Client.stream()` call (using a fake adapter whose `stream()` yields a `FINISH` event with `Usage` containing no pre-populated `cost_usd`) fed through `StreamAccumulator` produces `response().usage.cost_usd` equal to the oracle value. For both paths, the oracle-derived expected value is asserted `Decimal` before comparison; the fake adapter must not pre-populate `cost_usd` — the production path must compute it. |
| AC-4 | A `DirectProviderBackend`-emitted `provider:response` event payload carries `"cost_usd"` as a **top-level key** (`payload["cost_usd"]` exists). A probe that finds `cost_usd` only inside `payload["usage"]` does not satisfy this criterion. The value equals `response.usage.cost_usd` (None is legal; absent key is not). |
| AC-5 | `Usage.__add__` is called with three combinations: (a) both `cost_usd` present → Decimal sum, (b) one `cost_usd` None → result None, (c) both None → result None. In each case the result type is asserted (Decimal or None, never float, never TypeError). |
| AC-6 [guard] | Existing token-field `Usage` arithmetic (input_tokens, output_tokens, total_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens) is unchanged: adding two `Usage` objects without `cost_usd` still produces correct token sums. A `provider:response` event payload still carries the `"usage"` sub-dict with all existing token keys. |

## Non-goals (Scope OUT, with follow-up homes)

- **MAP/REDUCE + per-model rollups** (consumer/daemon layer): deferred to a follow-up that adds aggregation across steps; not part of this AC set.
- **Spawn-path cost bridging via `AmplifierBackend._run_with_spawn`**: deferred; any spawn-containing total must surface as None/flagged, never a confident undercount (follow-up item).
- **Provider-tier `_cost.py` migration to a shared source** (v2): deferred; the internal home of the rate data (unified_llm subpackage vs standalone package) is the **implementer's choice** — these criteria bind only the public surfaces named in the ACs.
- **`AmplifierBackend._run_with_tool_loop()` `provider:response` cost_usd**: AC-4 names `DirectProviderBackend` specifically; whether the `AmplifierBackend` fallback path also carries `cost_usd` is not bound by these criteria.

## Delegated freedoms (implementer's choice)

- The internal home of the rate data (unified_llm subpackage, standalone package, or embedded in `_cost.py` per-provider) is not constrained by these criteria.
- Whether `compute_cost` looks up the catalog internally or uses a separate internal rate table is not constrained.
- Whether `ModelInfo` gains cache-tier rate fields is not constrained (the implementation may use any internal mechanism to resolve cache-tier rates for known models).
- Whether `usage.cost_usd` is computed by calling `compute_cost` internally or by a separate code path producing the same result is not constrained.
