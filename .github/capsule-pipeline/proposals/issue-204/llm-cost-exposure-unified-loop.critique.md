The prior AC-6 prescription is applied: the gate now rejects a changed `provider:response` `total_tokens` value while preserving the complete criteria census.

## Criteria-fidelity review

- **AC-1:** The probe imports the required public API, selects three distinct oracle-table models at runtime including the mandated Sonnet ID and an oracle-declared fast-eligible model, generates token counts at runtime, and compares every required token-shape (including fast mode) to a sibling vendored, pinned oracle as `Decimal` values. This tests behavior rather than file placement or implementation internals.
- **AC-2:** It tests both stated failure semantics through public `unified_llm` surfaces: an oracle-confirmed unknown ID must yield `None`, and a catalog-visible entry with a missing rate dimension must yield `None`. It treats inability to present the latter public fixture as loud gate infrastructure failure, as the criterion requires; it does not import or patch private catalog state.
- **AC-3:** The adapter supplies model and runtime-generated tokens but no cost. Both `Client.complete()` and a real stream consumed by `StreamAccumulator` must return an oracle-consistent `Decimal`, so forwarding a fixture-provided value cannot green it.
- **AC-4:** Two DirectProviderBackend executions require the event payload's *top-level* `cost_usd` to equal a response `Decimal` and, independently, require the key to be present with `None`. This is the specified propagation behavior, not an invented calculation layer.
- **AC-5:** All three `None` operand positions and the positive Decimal sum are exercised, including no-TypeError behavior.
- **AC-6:** Existing token arithmetic and the existing event token fields are checked for exact values, including non-None cache fields and `total_tokens`, as well as `finish_reason` and `step_count` presence. These are directly licensed by the unchanged-fields guard.

## Prior prescription follow-through and executed counterexample

The previous brief required `payload['usage']['total_tokens'] == 30`. The current AC-6 probe contains that assertion. I independently executed the prior counterexample against a fresh pinned-base worktree after applying the honest hypothesis and changing only the emitted event's `total_tokens` to `0`:

```sh
wt=$(mktemp -d /tmp/ac6-total-verified-XXXXXX)
git worktree add --detach "$wt" b88964330b707473e308f569db374b30a2608247
cp -a .ai "$wt/.ai"
(cd "$wt" && git apply .ai/hypothesis.patch && \
 python3 - <<'PY'
p='modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py'
s=open(p).read()
needle='"total_tokens": result.total_usage.total_tokens,'
assert needle in s
open(p, 'w').write(s.replace(needle, '"total_tokens": 0,'))
PY
 bash .ai/capsule/DEFINITION.verify.sh)
```

It exited `1` with `FAIL: AC-6 guard: total_tokens=0, expected 30` and census row `AC-6: UNMET`. The concrete prior prescription is therefore applied, not merely rebutted.

## Mechanical evidence and non-vacuity

`.ai/census-red` is a complete six-row census and records AC-1 through AC-5 UNMET with AC-6 MET, consistent with frontmatter `red_signal: AC-1: UNMET`. The latest nonvacuity record reports both independent honest implementations green and flip AC-1 through AC-5, no inert criteria, and hermetic execution. The rival's RED is not over-specification evidence: its own executed log identifies missing AC-2 public None-rate coverage and missing AC-3 streaming accumulation, both expressly required by the criteria.

The recorded greened V patch is **sabotage-class**, not reviewer-plausible: its new SUT `_cost.py` walks upward specifically for `.ai/capsule/oracle.py` and delegates calculation to the gate fixture (`.ai/hypothesis_v.patch`, lines 52-105). That fixture parasitism is overtly unacceptable in review and is a recorded finding rather than a block. No void-adjudication artifact exists for this round.

There are shipped tests in this repository (`no_shipped_tests: false` in the setup record), and the capsule runs real public-surface behavior rather than a testless existence check.
VERDICT: SHIP