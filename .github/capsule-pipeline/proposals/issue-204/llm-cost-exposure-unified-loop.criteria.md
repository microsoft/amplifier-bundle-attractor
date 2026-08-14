<!-- BINDING maintainer acceptance criteria -- extracted VERBATIM from the authenticated maintainer comment channel by criteria_gate. NO ONE in this run may edit this file: the pipeline holds no authority over maintainer text. -->
Source-comment-author: @bkrabach
Source-comment-id: 5285660908
Source-comment-updated-at: 2026-08-13T19:52:07Z

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
  `unified_llm` and returns `decimal.Decimal | None`. Golden parity is
  MULTI-MODEL (Option A, maintainer-decided 2026-08-12): at gate runtime the probe samples model ids from the
  pinned oracle's own rate table — at least 3 models, which MUST include
  `claude-sonnet-4-5-20250929` and at least one fast-eligible model per the
  oracle's own fast-eligibility data — and for EVERY sampled model the result
  EXACTLY equals the oracle's compute_cost for the same inputs across:
  input-only, input+output, cache_read-heavy, cache_write-heavy, and (on the
  fast-eligible model, with speed="fast") fast-mode cases (golden parity,
  computed against the pinned oracle at test time — not hardcoded expected
  values). A probe that exercises exactly one model, or whose fast-mode case
  never runs on a fast-eligible model, does not satisfy this criterion.
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
  Public-surface binding (per the 2026-08-13 council review): every AC-2 probe
  reaches the system under test through public, documented surfaces only — no
  underscore-prefixed imports from, and no monkey-patching of, catalog or
  unified_llm internals. If the public surface cannot present the required
  entry, the gate fails loudly as gate-infrastructure; that is never a license
  to reach private. (Introspecting the gate's own vendored oracle is permitted —
  it is a fixture, not the system under test.)
AC-3: a unified_llm Client.complete() response carries usage.cost_usd consistent
  with AC-1 (Decimal | None), including when assembled through the streaming
  accumulator.
  Probe-shape clarification (2026-08-13, applying this criterion's own
  'consistent with AC-1' requirement; answers fire 6's AC-3 fork): the AC-3
  probe's mock adapter responses MUST carry only the model id and token counts —
  NEVER a pre-set cost_usd value. The assertion is that the returned response
  (and the stream-accumulated response) carries the oracle-consistent Decimal
  computed from those tokens; a probe whose expected value was placed on the
  adapter response by the gate itself tests only forwarding and does not
  satisfy this criterion. WHICH layer between adapter and caller computes the
  value remains the implementer's choice (the Delegated clause stands) — the
  probe binds the public outcome, not the computing layer.
AC-4: loop-pipeline's provider:response event payload carries a "cost_usd" key on
  the DirectProviderBackend path, equal to the response usage's cost_usd (None is
  a legal value; an absent key is not).
  Clarification: 'carries a cost_usd key' means cost_usd is a key of the
  provider:response event payload itself (payload['cost_usd']), never only
  nested inside a sub-dict such as usage; a probe that accepts a nested-only
  value does not satisfy this criterion.
AC-5: summing Usage objects where any operand's cost_usd is None yields a total
  with cost_usd None (never TypeError, never a number).
AC-6 [guard]: existing token-field Usage arithmetic and provider:response token
  fields are unchanged for callers that ignore cost.

Verification licensing: expected cost values in every AC probe MUST derive from
the provider rates oracle (the provider package's own cost table/computation)
via a PINNED copy VENDORED beside the gate inside the capsule, its exact
version/commit recorded in capsule provenance. The oracle is imported from that
vendored copy only — never from the implementation under test, and never
resolved from ambient caches, installed packages, or runner-specific paths (a
gate that loads its oracle from a hardcoded path such as
/home/runner/.amplifier/cache is runner-verified, not machine-verified, and
does not satisfy this licensing). Golden parity MUST execute in a clean
environment containing only the capsule and the system under test. If the
vendored oracle fails to load, the gate MUST exit loudly as an infrastructure
failure: type-only or None-accepting fallback assertions are prohibited —
oracle absence is never a pass. Token counts for probe calls MUST be
runtime-generated (never fixed literals a stub could threshold against); the
oracle-derived expected value MUST be asserted Decimal before any comparison.
A gate whose expected values are SUT-derived does not satisfy AC-1/AC-3. Oracle provenance (clarification 2026-08-12, applying this ratified licensing to the
repo topology; answers the pipeline's oracle-provenance question): 'the provider
package's own cost table/computation' means amplifier_module_provider_anthropic/_cost.py
from the public microsoft/amplifier-module-provider-anthropic repository — it exists,
is public, and carries input/output, cache_read, cache_write, and fast-eligibility
data. The gate author vendors that file at a pinned commit (fetched at authoring
time; source URL + commit SHA recorded in capsule provenance). Rate data invented
by the gate author or sourced from published pricing pages does NOT satisfy this
licensing (the gate must never define its own truth); models.json is likewise not
the oracle. Vendoring form (clarification 2026-08-13, applying this licensing's
reviewability intent; answers fire 4's publication block): the vendored oracle
ships as its own plain, readable source file beside the gate — a separate
capsule artifact (e.g. oracle.py) loaded at gate runtime by sibling path —
NEVER embedded or encoded (base64 or any similar blob) inside the gate script
or any capsule document; an encoded blob is secret-shaped and unreviewable and
does not satisfy this licensing.

Criteria drafted with agent assistance; revised per the 2026-08-13 council
review; ratified by @bkrabach on 2026-08-12; oracle-provenance clarification applied 2026-08-12; vendoring-form clarification applied 2026-08-13; AC-3 probe-shape clarification applied 2026-08-13.

