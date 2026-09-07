## Summary

`attractor-scout` and `attractorify` — this repo's two skills — are ~30 KB
authoring tools a human starts by hand, with zero all-time model loads in the
program usage table. Owner directive 2026-09-07: *"all of those authoring ones
are ones we run by hand, so they can be hidden."* Both now carry
`disable-model-invocation: true`, which moves them out of the model's
autonomous reach while leaving `/attractor-scout`, `/attractorify` and
`load_skill(skill_name=...)` working exactly as before. Ships with
`tests/test_skill_catalog_visibility.py` (S-300..S-304) so the decision is
guarded rather than remembered. Work item `model_performance-lswd`.

**Decision-matrix tier: territory the nlspec is silent about — and the cheapest
kind of it.** This touches no engine semantics, no `.dot` vocabulary, no
pipeline behaviour. It is a guidance-surface visibility flag on two skills in
`skills/`, which `AGENTS.md`'s verification gradient prices at *root guards*.
Nothing here moves toward or away from the nlspec.

**Read §5 of the DONE-NOTE before quoting this as a cost win.** The item is
filed under "skills catalog reduction" and **this change reduces zero catalog
bytes** — measured, not assumed. The lever is real; its units are risk, not
tokens.

## Verification checklist

- [x] nlspec evidence: **N/A — spec-silent territory.** Skill frontmatter
      visibility is not an attractor-spec concern; no section cited because
      none bears on it, and no extension is claimed.
- [x] Unit tests pass — the checklist's `pytest modules/loop-pipeline/` line is
      stale (loop-pipeline left in the P4 slim). Ran what CI actually runs; all
      three jobs green, output below.
- [x] Live pipeline run — **N/A.** Touches no `engine.py`, no handler, no
      exemplar graph, and nothing a pipeline executes. `AGENTS.md`'s
      verification gradient puts `skills/` changes at *root guards*.
- [x] AGENTS.md reviewed; repo-specific gates met.
      `tests/test_orchestrator_source_pin_guard.py` — the one AGENTS.md names
      as must-not-skip — ran and passed 8/8, no skips.
- [x] Backward-compat path unchanged. `user-invocable: true` retained on both,
      so the slash command survives; `SkillsTool._load_skill` gates fork
      execution on `context == "fork"` alone and neither skill declares it, so
      the load path is untouched. Verified live, not reasoned: both still
      return their complete body inline.
- [x] Observable contract → `specs/EXTENSIONS.md` entry: **not needed.** No
      dispatch semantics, event contract, or admission/validation behaviour
      changes. The observable change is which section of the skills-visibility
      reminder two skills appear in, which is `tool-skills` behaviour in the
      skills bundle, not an attractor engine contract.
- [x] Doc claim about code behavior ships a guard test: **yes.** The claim is
      "these two skills are hand-run only," and
      `tests/test_skill_catalog_visibility.py` pins it. Its docstring states
      plainly that S-300..S-303 are one-sided — the value under guard *is* an
      owner policy decision about these files, not a number derived from code,
      so there is no second source to cross-check against. The measured claims
      (0-byte delta, `hooks.py:412/417`, `discovery.py:297`) are sourced to
      `docs/lanes/lswd-attractor-catalog-reduction/evidence/`, with the
      measurement script committed.
- [x] Pre-publication leak review: **N/A — no new public content class.** The
      diff is two frontmatter blocks, one guard test, and a lane record under
      the existing `docs/lanes/` convention. No new top-level directory, no new
      artifact type reaching users, no fixture corpus. The evidence files carry
      command output and byte counts, no session data. The deterministic leak
      guards are green (see the pre-existing-red note below).
- [x] PR body includes verification evidence, not just "tests pass."
- [ ] **CI green before merge** — this is a draft PR opened by a lane that may
      not merge. Confirm with `gh pr checks` and require
      `CI Gate (all checks passed)` before the merge stage.

## Verification evidence

### Fail-before / pass-after

The guard was written first and run against untouched SKILL.md files:

```
$ python -m pytest tests/test_skill_catalog_visibility.py -q   # BEFORE
FAILED ...::test_s300_hand_run_skill_is_not_model_invocable[attractor-scout]
FAILED ...::test_s300_hand_run_skill_is_not_model_invocable[attractorify]
FAILED ...::test_s301_flag_is_a_real_boolean_not_a_string[attractor-scout]
FAILED ...::test_s301_flag_is_a_real_boolean_not_a_string[attractorify]
4 failed, 5 passed in 0.04s

$ python -m pytest tests/test_skill_catalog_visibility.py -q   # AFTER
9 passed in 0.03s
```

The five green in both states are the ones guarding what must *not* change
(S-302 user-invocable, S-303 inline, S-304 the declared set) — that is what
makes the pair honest rather than tautological.

Verbatim: `docs/lanes/lswd-attractor-catalog-reduction/evidence/fail-before.txt`,
`pass-after.txt`.

### Every CI job

Reproduce with `bash docs/lanes/lswd-attractor-catalog-reduction/evidence/run_suites.sh`.

| job | before | after |
|---|---|---|
| `opinionated-guards` — `pytest tests/ -q --ignore=tests/e2e` | 204 passed, 2 skipped | **213 passed, 2 skipped** |
| `unit-tests` — `modules/tool-report-outcome` | 21 passed | **21 passed** |
| `dot-render-gate` — 43 tracked `.dot` | 0 failed | **0 failed** |

The +9 are all this PR's. The 2 skips are pre-existing and unrelated
(`test_context_include_paths.py`, namespaced framework-resolved refs).

### One pre-existing red, proven not ours

`skills/attractor-scout/tests/` is not a CI suite (root `pyproject.toml` sets
`testpaths = ["tests"]`; the skill carries its own `pytest.ini`). It was run
anyway because this PR edits that skill's SKILL.md.
`test_no_real_data_leak.py::test_layer2_no_current_environment_identity` fails
on 7 files — **and fails on the same 7 at `c12885d` with this branch's changes
stashed.** The matched term is `'amplifier'`, derived at runtime from the
machine's git identity (`user.name = Amplifier`) and colliding with the product
name in paths like `~/.amplifier/projects`. Stash command and both outputs:
`evidence/pre-existing-failures.txt`. Not fixed here — out of scope, and it is
an environment collision in the guard's identity derivation, not a leak.

### Real-session load — the acceptance criterion

The work item requires that both still load by name after the change. Checked
in a live agent session at $0, using `tool-skills`' own `source` parameter to
merge this worktree's `skills/` into the live catalog in memory:

| call | result |
|---|---|
| `load_skill(skill_name="attractorify")` | `success: true`, complete body returned inline |
| `load_skill(skill_name="attractor-scout")` | `success: true`, complete body returned inline |

A fresh `amplifier` run against this bundle was deliberately **rejected**: the
bundle mounts `modules/tool-report-outcome` by relative source, and a first run
editable-installs local module sources into the shared uv tool venv, which
`AMPLIFIER_HOME` does not isolate — pointing that at a lane worktree writes
`.pth` files at a path that vanishes on teardown. A container run was rejected
on the lane's $0 spend authority. Both reasons recorded in
`evidence/real-session-load.md`.

### The measurement that changes how this should be described

`evidence/measure_catalog_delta.py` renders the live
`SkillsVisibilityHook._format_skills_list` — the function producing the
`hooks-skills-visibility` reminder in every session — with the flag forced off
and on:

| catalog | flag absent | flag true | delta |
|---|---:|---:|---:|
| this repo's 2 skills alone | 457 B | 463 B | **+6 B** |
| stock skills bundle + this repo (40 skills) | 6,623 B | 6,623 B | **0 B** |

Both sections emit one line per skill under the same 180-char cap
(`DEFAULT_LINE_CHAR_CAP`), so moving from `Available skills` to `User-invoked
skills` is free; the marginal case is +6 B because the second header appears.
The 2,500-token budget bounding only the regular section was not binding at
either size — in a catalog large enough for it to bind the sign could differ,
and this lane did not measure that.

What the flag buys is the **tail**: the model can no longer decide on its own to
load a ~30 KB body (7.5–7.6 k tokens) inline mid-task. Zero all-time loads means
the realised cost to date is zero — this closes the path, it does not recover a
measured loss.

## Observations

None arose. The `docs/VISION.md` passages were not engaged by this change —
it touches no engine semantics, no pipeline behaviour, and no doctrine claim.

## Notes for reviewers

- **The honest framing matters more than the diff.** Three lines of frontmatter
  are trivial; the finding that "skills catalog reduction" saves 0 bytes is the
  part worth carrying forward, and it is written into the SKILL.md comments, the
  guard's docstring, and §5 of the DONE-NOTE so it cannot be quoted loose.
- **S-304 is decision-forcing and will fail on the next new skill.** That is
  intended: a third `skills/*/SKILL.md` must be added to `HAND_RUN` or
  `MODEL_INVOCABLE` — a one-line edit — rather than silently defaulting to
  model-reachable. Push back if that is unwelcome; it is the one assertion here
  that constrains future work rather than recording this decision.
- **Draft, and this lane may not merge.** Fail-before/pass-after is demonstrated
  above; the merge is the manager's next stage.
