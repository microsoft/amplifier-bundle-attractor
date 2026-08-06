---
id: prefix-sharing-substitution-corruption
title: "$key substitution corrupts undefined variables whose names share a prefix with a defined variable"
red_signal: SUFFIXED=Alice_suffix
base_sha: 9d3dd1c2feb30b8264058c6febf9d828ea27a5c4
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`substitute_context()` in `substitution.py` must leave undefined `$key` tokens
unchanged (literal pass-through) even when the undefined key's name begins with
the same characters as a defined key.

Concretely: given context `{"name": "Alice"}` and input text
`echo NAME=$name SUFFIXED=$name_suffix`, the function must return
`echo NAME=Alice SUFFIXED=$name_suffix` — the undefined `$name_suffix` token
survives as a literal, identical to any other absent-key token.

The same boundary-aware behaviour is required in `expand_params()` in
`transforms.py`, which contains the identical bare `str.replace()` call.

## Why this matters

The documented contract for both functions is that absent keys pass through
unchanged — no exception is raised, no substitution occurs. The current
implementation violates that contract silently: `str.replace("$name", "Alice")`
is not token-boundary-aware, so it replaces the `$name` prefix inside
`$name_suffix`, producing `Alice_suffix` — a value that was never defined and
never intended.

This is not a contrived edge case. Any pipeline that uses two context keys
where one name is a prefix of the other (`id`/`id2`, `model`/`model_stylesheet`,
`tool`/`tool_name`, etc.) will hit silent corruption whenever only the shorter
key is present. The corruption is invisible at runtime: no error is raised, the
wrong string is silently injected into `tool_command` or `prompt` text, and the
resulting behaviour is undefined.

The existing "longest keys first" sort in `substitute_context()` only prevents
the collision when **both** the shorter and longer key are in the context
snapshot. It does nothing when the longer key is absent — which is exactly the
failing case.

## Definition of done

The verify script checks the following observable behaviours:

1. **Primary defect — `substitute_context`**: calling
   `substitute_context("echo NAME=$name SUFFIXED=$name_suffix", {"name": "Alice"})`
   returns `"echo NAME=Alice SUFFIXED=$name_suffix"`.  The corrupted form
   `Alice_suffix` must not appear in the output.

2. **Variant — numeric suffix**: calling
   `substitute_context("echo ID=$id ID2=$id2", {"id": "42"})`
   returns `"echo ID=42 ID2=$id2"`.  The corrupted form `422` must not appear.

3. **Secondary site — `expand_params`**: calling
   `expand_params("echo NAME=$name SUFFIXED=$name_suffix", {"name": "Alice"})`
   returns `"echo NAME=Alice SUFFIXED=$name_suffix"`.  Same boundary requirement.

4. **Regression — defined-both case still works**: calling
   `substitute_context("$tool.output and $tool", {"tool": "base", "tool.output": "dotted_value"})`
   still resolves both tokens correctly (the existing longest-key-wins behaviour
   must not regress).

5. **Existing unit tests pass**: the full `test_unified_substitution.py` suite
   continues to pass.

Human reviewer should additionally confirm that no existing `$goal` or
`${key}` (brace-form) substitution behaviour is altered, and that the fix
does not introduce a new dependency on any library not already present in
`modules/loop-pipeline/pyproject.toml`.

## Non-goals

- Fixing or changing the `extract_refs()` function — it already uses a
  regex that is word-boundary-aware and is not affected by this bug.
- Changing the `${key}` brace-form path in Phase 1 of `substitute_context()` —
  braces delimit the key exactly and are not affected.
- Changing the `expand_goal_variable()` function — it expands a single fixed
  token (`$goal`) and the prefix-collision scenario does not apply.
- Adding CLI-level or end-to-end pipeline integration tests — the unit-level
  `substitute_context()` surface is the canonical test boundary for this module.
- Changing the documented absent-key pass-through contract itself.
