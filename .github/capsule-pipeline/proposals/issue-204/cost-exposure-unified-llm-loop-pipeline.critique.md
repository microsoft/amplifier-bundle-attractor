The AC-3 production-path probe and AC-2 missing-rate probe now exercise the required behavior, so this capsule is ready to ship.

## Prior prescription follow-through

The prior brief required a real `Client.complete()` call and a genuine presented catalog entry with a required `None` rate dimension.  Both prescriptions are applied:

- AC-3 constructs fake adapters with runtime-generated usage lacking a supplied `cost_usd`, calls `await client.complete(request)`, and independently drives `client.stream(request)` into `StreamAccumulator.response()`.  Each result is compared with a `Decimal` produced by the independently imported provider oracle.
- AC-2 injects a synthetic `ModelInfo` whose `input_cost_per_million=None`, verifies that exact object through public `get_model_info(synthetic_id)`, calls the public `compute_cost` with nonzero runtime-generated input tokens, and requires `result is None` unconditionally.  It restores catalog state afterward.

## Executed current-tree evidence

I ran the current gate directly:

```text
cd /home/runner/work/amplifier-bundle-attractor/amplifier-bundle-attractor && bash .ai/capsule/DEFINITION.verify.sh; rc=$?; printf '\nGATE_RC=%s\n' "$rc"; cat .ai/census
```

It exited `GATE_RC=1` at the pinned base and wrote the complete six-row census:

```text
AC-1: UNMET
AC-2: UNMET
AC-3: UNMET
AC-4: UNMET
AC-5: UNMET
AC-6: MET
```

That matches the declared whole-line `red_signal: AC-1: UNMET`.  The latest nonvacuity ledger record records both mechanically different honest implementations green, with `AC-1` through `AC-5` flipping, no inert ACs, and a proven hermetic reset.

## Criteria fidelity

Each feature criterion has an observable public-surface assertion.  AC-1 derives five expected results at runtime from the independently imported provider computation and asserts both oracle and SUT values are `Decimal`; AC-2 covers both unknown-model and explicitly missing-dimension negative space; AC-3 covers real non-streaming and streaming Client routes; AC-4 observes the emitted DirectProviderBackend event and rejects a nested-only cost; AC-5 covers present/present and both None-absorbing cases; and AC-6 preserves both Usage token arithmetic and the emitted token fields.  The assertions do not impose an internal rate-data location or a particular cost-attachment implementation, consistent with the explicitly delegated freedoms.

## Stub classification

The ledger and `stub-greened.md` establish that the V patch greened AC-1 through AC-5.  Reading `hypothesis_v.patch` shows that it is sabotage-class rather than reviewer-plausible: its new cost implementation explicitly calls itself a stub, dynamically searches the machine-local `/home/runner/.amplifier/cache`, and imports the oracle from that runner-only location.  That is an overt environment-dependent fake a reviewer would reject on sight, not a credible feature implementation that exposes an untested criterion gap.  The recorded finding remains useful evidence, but does not block this criteria-faithful gate.  No gate-blind rival was produced, so there is no rival evidence either way.

VERDICT: SHIP