---
id: preflight-auto-discovered-profiles
title: "Provider preflight false refusal on auto-discovered profiles"
red_signal: no provider module or profile is mounted for it
base_sha: da8ffd1faa87128573bd9872e12aa4f4f7747f0b
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`PipelineOrchestrator.execute()` must not raise `ProviderPreflightError` when
the only profiles available are auto-discovered from
`coordinator.config["agents"]` (i.e., no explicit `"profiles"` key in
orchestrator config). The preflight must see the same profiles that
`_build_backend()` would compute, so a pipeline that would have run
successfully does not fail at startup with a factually wrong "no provider
module or profile is mounted" message.

## Why this matters

`_build_backend()` has two profile sources: (1) explicit `config["profiles"]`
and (2) auto-discovery from `coordinator.config["agents"]` when no explicit
profiles are present. The preflight check runs before `_build_backend()` and
only sees source (1). When source (2) is the only source -- an agent named
like the declared provider (e.g., agent `"openai"` serving `llm_provider="openai"`)
-- the preflight fires `ProviderPreflightError` even though `_build_backend()`
would have found a valid profile. The pipeline is refused for a reason that
is factually wrong: the run CAN serve the declared provider.

## Definition of done

1. `PipelineOrchestrator.execute()` does not raise `ProviderPreflightError`
   when the coordinator has an agent whose name matches the declared
   `llm_provider`, no explicit `"profiles"` key is in orchestrator config,
   and at least one provider is mounted (so simulation mode is not triggered).
   This must hold for any agent-named profile, not only for known provider
   names like "openai" -- the auto-discovery rule is general.

2. The preflight still correctly refuses when an agent-named profile exists
   for provider A but the node declares provider B, and provider B has no
   matching agent or mounted provider -- per-item selectivity is preserved.
   The refusal must name only the unmatched provider (B); the matched provider
   (A) must not appear in the refusal message, proving the matching item was
   accepted rather than suppressed along with the unmatched item.

3. Profile binding is preserved: when a node declares a provider that has no
   matching agent in the coordinator (and is not mounted), the preflight must
   still refuse. A correct fix makes the preflight aware of auto-discovered
   profiles, not credential-check-blind or profile-check-blind.

4. The full loop-pipeline test suite passes under `uv run --project
   modules/loop-pipeline pytest tests/` (excluding `test_remote_dot.py`,
   which requires an optional module not in this module's declared
   dependencies). A regression test for the auto-discovery path may be placed
   in any file in the test suite.

The verify script checks items 1-4 mechanically. Every provider name it uses --
in the must-ACCEPT scenarios and the must-REFUSE scenarios alike -- comes from
ONE runtime generator producing identically shaped, semantically neutral names
(nine uniformly random lowercase letters, the first letter included, plus the
run's shared numeric suffix). There is no fixed prefix and no shape difference
between an accept-case name and a refuse-case name, and each generated name is
additionally required to be ACCEPTED in one scenario and REFUSED in another,
with only the presence of a matching agent changed between them. The name
therefore carries no information about the expected verdict: a fix that
special-cases a known provider name (e.g. "openai"), a fixed probe name, or any
name pattern whatsoever is forced to get one of the two scenarios wrong. The fix
must implement the general auto-discovery rule. Part 2 additionally checks that
the refusal message from the mixed-graph run does not name the matched provider,
proving per-item selectivity rather than whole-scope suppression.

The declared red_signal is the substring the preflight's refusal message
produces ORGANICALLY for an unknown provider -- a provider outside
`PROVIDER_KEY_ENV` gets no credential parenthetical, so the organic text ends at
"...mounted for it". The gate never prints that substring itself: it reaches the
RED log only because Part 1 echoes the caught `ProviderPreflightError` verbatim.
A red produced by anything else -- an over-broad patch, a failing regression
suite -- therefore cannot carry the signal, which is what makes checking for it
evidence rather than ceremony.

## Non-goals

- Changing the credential-check logic in `check_provider_preflight()` itself.
- Changing the profile resolution priority (explicit profiles still override
  auto-discovery).
- Covering nested/child pipeline graphs (the preflight's documented scope
  is the root graph only).
- Policing nodes with no declared `llm_provider` (the implicit default is
  deliberately not checked by the preflight).
