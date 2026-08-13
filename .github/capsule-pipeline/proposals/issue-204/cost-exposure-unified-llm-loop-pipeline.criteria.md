<!-- BINDING maintainer acceptance criteria -- extracted VERBATIM from the authenticated maintainer comment channel by criteria_gate. NO ONE in this run may edit this file: the pipeline holds no authority over maintainer text. -->
Source-comment-author: @bkrabach
Source-comment-id: 5274757428
Source-comment-updated-at: 2026-08-13T01:19:32Z

## Acceptance criteria (feature-capsule)

Owned-by: @bkrabach
Scope: V1 = per-call cost exposure on the unified_llm + loop-pipeline stack.
OUT (each with a follow-up home): MAP/REDUCE + per-model rollups (consumer/daemon
layer); spawn-path cost bridging via AmplifierBackend._run_with_spawn (follow-up —
any spawn-containing total must surface as None/flagged, never a confident
undercount); provider-tier _cost.py migration to a shared source (v2).
Delegated: the internal home of the rate data (unified_llm subpackage vs
standalone package) is the implementer's choice; these criteria bind only the
public surfaces named below.

AC-1: `unified_llm.compute_cost(model, input_tokens, output_tokens, *,
  cache_read_tokens=0, cache_write_tokens=0, speed=None)` is importable from
  `unified_llm` and returns `decimal.Decimal | None`. For
  `claude-sonnet-4-5-20250929` its result EXACTLY equals
  `amplifier_module_provider_anthropic._cost.compute_cost` for the same inputs
  across: input-only, input+output, cache_read-heavy, cache_write-heavy, and
  fast-mode cases (golden parity, computed against the provider table at test
  time — not hardcoded expected values).
AC-2: honest None — for a model id with no rate coverage, and for a case
  requiring a rate dimension the table lacks, compute_cost returns None: never 0,
  never a partial computation, never a float (Decimal type asserted).
  Selection rule (maintainer-adopted 2026-08-12): no production model is
  designated; 'a rate dimension the table lacks' means the ModelInfo field is
  None in catalog data (the spec types every cost field Float | None; the
  catalog is advisory data, not a fixed table); the gate exercises this by
  presenting, via the public catalog surface, a model entry whose required rate
  dimension is None and requiring compute_cost -> None for that case (Decimal
  otherwise; never 0, never float, never partial).
AC-3: a unified_llm Client.complete() response carries usage.cost_usd consistent
  with AC-1 (Decimal | None), including when assembled through the streaming
  accumulator.
AC-4: loop-pipeline's provider:response event payload carries a "cost_usd" key on
  the DirectProviderBackend path, equal to the response usage's cost_usd (None is
  a legal value; an absent key is not).
  Clarification (drafted by the maintainer's agent from pilot 5's standing judge
  finding; PENDING the maintainer's explicit ratification — carried for this
  machinery pilot under the run's agent-drafted-criteria provenance): 'carries a
  cost_usd key' means cost_usd is a key of the provider:response event payload
  itself (payload['cost_usd']), never only nested inside a sub-dict such as
  usage; a probe that accepts a nested-only value does not satisfy this
  criterion.
AC-5: summing Usage objects where any operand's cost_usd is None yields a total
  with cost_usd None (never TypeError, never a number).
AC-6 [guard]: existing token-field Usage arithmetic and provider:response token
  fields are unchanged for callers that ignore cost.

Verification licensing (drafted by the maintainer's agent from pilot 4's
machine-proven judge finding; PENDING the maintainer's explicit ratification —
carried for this machinery pilot under the run's agent-drafted-criteria
provenance): expected cost values in every AC probe MUST derive from the
provider rates oracle (the provider package's own cost table/computation),
imported independently of unified_llm, never from the implementation under
test; token counts for probe calls MUST be runtime-generated (never fixed
literals a stub could threshold against); the oracle-derived expected value
MUST be asserted Decimal before any comparison. A gate whose expected values
are SUT-derived does not satisfy AC-1/AC-3.

