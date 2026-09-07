# lswd — skills catalog reduction: hide `attractor-scout` + `attractorify`

Work item: `model_performance-lswd` (project `model_performance`). Owner approved
2026-09-07: *"all of those authoring ones are ones we run by hand, so they can be
hidden."*

Branch `lane/lswd-attractor-catalog-reduction`, cut from `origin/main` @ `c12885d`.
Ships as a **DRAFT PR**. This lane may not merge — the merge is the manager's next
stage.

Spend: **$0**. No LLM eval, no container, no API call beyond this lane's own
session.

---

## 1. What changed

Three files, one behaviour.

| file | change |
|---|---|
| `skills/attractor-scout/SKILL.md` | `+ disable-model-invocation: true` (frontmatter) |
| `skills/attractorify/SKILL.md` | `+ disable-model-invocation: true` (frontmatter) |
| `tests/test_skill_catalog_visibility.py` | **new** — S-300..S-304, the guard that keeps it |

Both skills keep `user-invocable: true`, keep `model_role: reasoning`, keep their
`allowed-tools`, and keep running **inline** (neither declares `context: fork`).

---

## 2. Fail-before / pass-after

Evidence files are verbatim command output in `evidence/`.

| | command | result |
|---|---|---|
| **before** | `pytest tests/test_skill_catalog_visibility.py -q` at `c12885d` + the new test, SKILL.md untouched | **4 failed, 5 passed** — `evidence/fail-before.txt` |
| **after** | same command, SKILL.md edited | **9 passed** — `evidence/pass-after.txt` |

The four that flip are S-300 and S-301, once per skill. The five that pass in both
states are the ones asserting what must *not* change (S-302 user-invocable, S-303
inline, S-304 the declared skill set) — they were green before because they are
guarding the status quo, and that is what makes the red/green pair honest rather
than tautological.

## 3. Full suite, green

`evidence/full-suite-after.txt`, reproducible via `evidence/run_suites.sh`. Every
job in `.github/workflows/ci.yml`:

| CI job | before | after |
|---|---|---|
| `opinionated-guards` (`pytest tests/ --ignore=tests/e2e`) | 204 passed, 2 skipped | **213 passed, 2 skipped** (+9, all mine) |
| `unit-tests` (`modules/tool-report-outcome`) | 21 passed | **21 passed** |
| `dot-render-gate` (43 tracked `.dot`) | 0 failed | **0 failed** |

`tests/e2e` is excluded by CI's own design (live/Docker, needs keys —
`tests/e2e/MANUAL_E2E.md`), not by this lane.

**And green on the real CI, not just locally.** Run
[34167331579](https://github.com/microsoft/amplifier-bundle-attractor/actions/runs/34167331579)
on PR #352 (`gh pr checks 352`):

    CI Gate (all checks passed)                    pass   4s
    DOT Render Gate (every tracked .dot renders)   pass  17s
    Opinionated Guards (repo root)                 pass  14s
    Unit Tests (tool-report-outcome, py3.11)       pass  11s
    Unit Tests (tool-report-outcome, py3.13)       pass  10s
    license/cla                                    pass

`CI Gate` is the one stable context branch protection requires, and it is
fail-closed by construction (`if: always()` plus an explicit `needs.*.result`
check), so its `pass` means every upstream job genuinely succeeded rather than
was skipped.

**One pre-existing red, proven not ours.** `skills/attractor-scout/tests/` is not
in CI (root `pyproject.toml` sets `testpaths = ["tests"]`; the skill has its own
`pytest.ini`). It was run anyway because this change edits that skill's SKILL.md.
`test_no_real_data_leak.py::test_layer2_no_current_environment_identity` fails on
7 files — **and fails on the same 7 files at `c12885d` with this lane's changes
stashed**. The matched term is `'amplifier'`, derived at runtime from this
machine's git identity (`user.name = Amplifier`) and colliding with the product
name in paths like `~/.amplifier/projects`. Proof, with the stash command and
both outputs: `evidence/pre-existing-failures.txt`.

## 4. Real-session check — the acceptance criterion

*"Given a real session, when `load_skill(skill_name=...)` is called directly on
each, then both still load successfully."*

Done, in this lane's own live agent session, at $0, using `tool-skills`' own
`source` parameter to merge the worktree's `skills/` into the live catalog
(in-memory only — no config write). Both returned `success: true` with the
**complete SKILL.md body inline**. Detail, and why a fresh `amplifier` run and a
DTU were both deliberately rejected: `evidence/real-session-load.md`.

## 5. The number that is NOT what the item implies — read this before quoting it

The item is filed under "skills catalog reduction". **This change reduces zero
catalog bytes, and that was measured rather than assumed.**

`evidence/measure_catalog_delta.py` renders the live
`SkillsVisibilityHook._format_skills_list` — the function that produces the
`hooks-skills-visibility` reminder in every session — over a real catalog with
the flag forced off and on:

| catalog | flag absent | flag true | delta |
|---|---:|---:|---:|
| this repo's 2 skills alone | 457 B | 463 B | **+6 B** |
| stock skills bundle + this repo (40 skills) | 6,623 B | 6,623 B | **0 B** |

Both sections emit one line per skill under the same 180-char cap
(`DEFAULT_LINE_CHAR_CAP`), so moving a skill from `Available skills` to
`User-invoked skills` is free; the marginal case is +6 B because the second
section's header appears. The 2,500-token budget that bounds only the regular
section (`DEFAULT_VISIBILITY_TOKEN_BUDGET`) was not binding at either catalog
size — in a catalog large enough for it to bind, the sign could differ, and this
lane did not measure that.

**What the change actually buys is the tail, not the line.** With the flag set,
the model can no longer decide on its own to load a ~30 KB body (7.5–7.6 k
tokens at the hook's own 4 chars/token estimate) inline in the middle of an
unrelated task. The usage table records zero all-time
loads for both skills, so the realised cost to date is zero — this closes the
path, it does not recover a measured loss. That is the honest framing, and it is
the owner's stated reason too: *they are run by hand.*

Anyone re-using "catalog reduction" as a cost lever should read this section
first. The lever is real; its units are risk, not tokens.

## 6. The guard, and what each check would catch

`tests/test_skill_catalog_visibility.py`, in the repo's guard idiom (file-content
assertions, pytest + pyyaml only, no engine import — so it runs in the
`opinionated-guards` job which installs nothing else).

| id | assertion | the regression it catches |
|---|---|---|
| S-300 | both carry `disable-model-invocation: true` | the flag dropped in a later edit |
| S-301 | the value is a real YAML boolean | `discovery.py:297` coerces a non-bool with `bool(...)`, under which the **string** `"false"` is truthy — a quoted value works today and inverts silently the day someone tries to turn it off |
| S-302 | both keep `user-invocable: true` | hiding from the model is only acceptable while the slash command survives; dropping both strands the skill with no invocation path |
| S-303 | neither declares `context: fork` | invisible *and* blind — a forked attractorify sees nothing of the session it exists to analyse |
| S-304 | `skills/` on disk == the declared set | decision-forcing: a third SKILL.md must state whether the model may reach for it, rather than defaulting to yes |

S-301 reports a missing key and points at S-300 rather than raising `KeyError`,
following `1b7bac5` (PRN-003/004).

## 7. Honest limits

- The CI guard reads frontmatter, not the running `tool-skills` module. This repo
  does not depend on the skills bundle and the `opinionated-guards` job installs
  only pytest + pyyaml, so a live-module assertion could not run there. The
  behavioural half is verified in §4 and reproducible via
  `evidence/measure_catalog_delta.py`, which does import the live module.
- S-300..S-303 are one-sided — they assert on this repo's own files with no second
  source to cross-check against — because the value under guard *is* an owner
  policy decision about these files, not a number derived from code. Sibling
  guards in this repo are two-sided for the opposite reason; the difference is
  deliberate and stated in the test's docstring.
- The catalog measurement (§5) uses the stock skills bundle plus this repo as its
  ambient catalog because the real set of skills mounted alongside this bundle
  varies per consumer. The marginal delta is exact; the ambient one is
  representative.

## 8. Census safety

No `amplifier` invocation with a scratch `AMPLIFIER_HOME` at any point (§4 records
why the one path that would have needed it was rejected). Post-run verification:
`grep -l /tmp/ ~/.local/share/uv/tools/amplifier/lib/python3.13/site-packages/*.pth`
returns nothing.
