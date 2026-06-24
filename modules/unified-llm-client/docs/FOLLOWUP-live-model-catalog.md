# Follow-up: Live Model Catalog via Provider Adapters

## What was deferred

The ideal implementation of `get_latest_model()` and `list_models()` would
source freshness directly from each provider's **live API** rather than from
the hand-maintained static `data/models.json`. Amplifier-core's
`Provider.list_models() -> list[ModelInfo]` protocol already requires every
adapter to implement live model discovery. The adapters in
`src/unified_llm/adapters/` (`anthropic.py`, `openai.py`, `gemini.py`,
`copilot.py`) would each need a `list_models()` method that calls
`client.models.list()` (or equivalent) and maps the live results to
`ModelInfo` objects with real `release_date` values.

## Why deferred

1. **Scope**: adding `list_models()` to all four adapters is a cross-adapter
   change, not a single-file fix, and touches every adapter's public surface.
2. **Zero live callers today**: all 23 `get_latest_model` call-sites are
   catalog internals, tests, or `__init__` exports — no production code
   consumes it yet.  The risk of a footgun hurting users is currently low.
3. **The recency fix + §8.10 smoke remove the footgun now**: `get_latest_model`
   now selects by `max(release_date, id)` deterministically, fails loudly for
   unknown providers, and the §8.10 staleness guard catches a dead id whenever
   keys are present.  This is a solid floor while the live-catalog work matures.

## How to implement when ready

1. Add `list_models() -> list[ModelInfo]` to each adapter, calling the
   provider's native model-list endpoint and mapping to `ModelInfo` with a
   typed `release_date` derived from the model id or the provider's metadata.
2. Update `catalog.py`'s `list_models(provider)` to call the active adapter's
   `list_models()` when available, falling back to `models.json` when the
   adapter is not loaded or the call fails.
3. Add an adapter-level test per provider that asserts the live-fetched list
   is non-empty and every entry has a valid `release_date`.

## References

- Amplifier-core `Provider.list_models()` contract: `amplifier-core` →
  `docs/contracts/PROVIDER_CONTRACT.md`
- Original unified-llm nlspec freshness intent: `strongdm-attractor` →
  `unified-llm-spec.md` §2.9 and App C
