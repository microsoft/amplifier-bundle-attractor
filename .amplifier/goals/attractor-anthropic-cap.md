# Goal: cap anthropic SDK below 1.0.0 in unified-llm-client

## Outcome

The `unified-llm-client` module's declared `anthropic` dependency carries an
upper-bound cap below `1.0.0`, so dependency resolution can never pull in an
anthropic SDK version that removed the `temperature` parameter from
`Messages.create()`/`.stream()` — the exact parameter this module's
`unified_llm/adapters/anthropic.py` adapter passes into that call today.

## Complete when

Complete when **either** every item below reaches a terminal state, **or**
it is conclusively demonstrated the remainder cannot, naming the blocker for
each. Items ending FAIL or BLOCKED are residuals, not failures of the goal.

### Items (each resolves independently to PASS / FAIL-named / BLOCKED-named)

1. **Cap the dependency.** In `modules/unified-llm-client/pyproject.toml`,
   change the `anthropic>=0.40.0` constraint to `anthropic>=0.40.0,<1.0.0`.
   PASS when the constraint is updated and dependency resolution
   (`uv lock`/`uv sync` at whatever scope this module resolves at — check
   existing scripts/CI config for the actual invocation) succeeds against
   it, resolving to an anthropic version below `1.0.0`.

2. **Verify no regressions.** Run this module's test suite (tests live under
   `modules/unified-llm-client/tests`, run via `uv run pytest` from the
   module directory or repo root per existing convention) and confirm no new
   failures relative to the pre-existing baseline (record the baseline
   pass/fail count before your changes, and the count after). PASS when
   after-count has no new failures vs. the baseline.

## SCOPE-OUTS

- Fixing `unified_llm/adapters/anthropic.py` to actually support the
  anthropic 1.0.0 API (whose `Messages.create()`/`.stream()` no longer
  accepts `temperature` and has no `**kwargs` catch-all) is explicitly NOT
  required in this lane. This lane's job is only to cap the version so the
  existing adapter code keeps working against pre-1.0.0 behavior — migrating
  the adapter to the new API is separate future work.
- Changes to any other module in this repo are NOT required — scope is
  `modules/unified-llm-client` only.
- A live DTU/integration test is NOT part of this lane — that happens
  separately, after all lanes of this batch land, using the branches
  together.

## KNOWN

- Working directory: `/home/ken/workspace/amplifier-bundle-attractor/worktrees/attractor-cap`
  — work ONLY here. Do not touch the main checkout or sibling worktrees.
- Branch: `fix/pin-anthropic-below-1.0`
- Base SHA: `1fb33d69e21d7b6fdc59b55668b530603e13dc25`
- File ownership: `modules/unified-llm-client/pyproject.toml` and any
  lockfile it produces. If you find you need to touch a file outside this
  scope (including `unified_llm/adapters/anthropic.py`), that is a residual
  — record the needed edit and stop; do not cross into it.
- Test command: `uv run pytest modules/unified-llm-client/tests` (or the
  module-local equivalent if resolution requires running from within
  `modules/unified-llm-client`).
- Commit early, push always — push your branch as you commit, don't batch
  everything into one commit at the end.
- Never merge to `main`. The orchestrator handles landing (this repo requires
  a PR, never a direct push to `main`).
- Time bound: 15 minutes wall-clock / 20 turns. Exceeding either is a
  terminal `BUDGET` state — report it, do not rush or skip the commit.
- Add `DONE.json` to this repo's `.gitignore` before writing it — it is not
  currently ignored here.
- Write `DONE.json` in the worktree root as your final act, with fields:
  `lane, session_id, verdict, branch, head, pushed, items[], residuals[],
  pending_human[], suite`. `verdict` is exactly one of `COMPLETE` / `BLOCKED`
  / `PARTIAL`. `session_id` is this lane's own session id.
