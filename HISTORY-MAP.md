# History map — where this repo's modules went

`amplifier-bundle-attractor` is the **pattern layer**: doctrine, spec extensions,
authoring guidance, exemplar graphs, skills, agent profiles, capsule lanes,
evals, and the composing bundles/profiles. It is not the runtime.

The runtime moved out in three history-preserving extractions to
[`microsoft/amplifier-bundle-dot-runner`](https://github.com/microsoft/amplifier-bundle-dot-runner).
For a compat window, this repo kept a copy of each extracted module under
`modules/` so external consumers installing from `@main` would not break
mid-flip. **The P4 slim (`attractor-28x`, this file's first commit) deleted
those copies.** This page says where each one went.

## The mechanical record lives in dot-runner, not here

Commit-level resolution — "what is pre-extraction commit `X` called now?" —
is [`HISTORY-MAP.tsv`](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/main/HISTORY-MAP.tsv)
in dot-runner, with
[`HISTORY-MAP.md`](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/main/HISTORY-MAP.md)
as its human-readable index. **Read that for SHA translation**, including its
"Known gap in the `new` column" note about rebase-merge rewriting SHAs.

This page deliberately does not copy that table. One rule, one home — the same
reason `docs/VISION.md` is the decision matrix's only home here.

## Directory index

Every directory that was under `modules/` in this repo, and its disposition.
"Landed at" is the first commit touching that path in dot-runner; "latest" is
its most recent commit there as of this slim (dot-runner `main` @ `1dfc78b`,
2026-09-06).

| Was here | Extraction | Now at | Landed at | Latest | Commits there |
|---|---|---|---|---|---|
| `modules/loop-pipeline` | 1 | `amplifier-bundle-dot-runner` `modules/loop-pipeline` | `9957f7f` (2026-02-09, the root commit — Extraction 1 became that repo's initial history) | `b8e4777` | 308 |
| `modules/pipeline-runner` | 1 | same path there | `38d99a6` (2026-07-13) | `7aa7cbe` | 52 |
| `modules/unified-llm-client` | 1 | same path there | `1f1e71e` (2026-02-25) | `fb0dcfb` | 24 |
| `modules/remote-source` | 1 | same path there | `098a1dc` (2026-07-27) | `fb0dcfb` | 4 |
| `modules/loop-agent` | 2 | same path there | `21c6a1f` (2026-08-28) | `fb0dcfb` | 58 |
| `modules/hooks-pipeline-observability` | 2 | same path there | `a904404` (2026-08-28) | `08bb363` | 20 |
| `modules/hooks-pipeline-progress` | 2 | same path there | `4e8e47a` (2026-08-28) | `2e52900` | 6 |
| `modules/hooks-tool-truncation` | 2 | same path there | `21c6a1f` (2026-08-28) | `2e52900` | 4 |
| `modules/tool-apply-patch` | 2 | same path there | `21c6a1f` (2026-08-28) | `2e52900` | 5 |
| `modules/tool-dashboard-query` | 2 | same path there | `3ba32d8` (2026-08-28) | `2e52900` | 5 |
| `modules/tool-pipeline-status` | 2 | same path there | `ba2a67f` (2026-08-28) | `2e52900` | 4 |
| `modules/tool-pipeline-run` | 3 | same path there | `8ba930b` (2026-09-06) | `2c372ed` | 10 |
| `modules/tool-report-outcome` | — | **STILL HERE.** See below. | — | — | — |

The three extraction events, with dot-runner's own record of each:

| Extraction | What | Recorded in dot-runner by |
|---|---|---|
| 1 — the engine (`DESIGN-repo-split.md`) | `loop-pipeline`, `pipeline-runner`, `unified-llm-client`, `remote-source`, `tool-report-outcome`, `specs/`, `SPEC_CONFORMANCE.md`; source cut at this repo's `d634fc5` (2026-08-26) | became dot-runner's initial history, root `9957f7f` |
| 2 — the worker/hook modules (`DESIGN-worker-registry-core-split.md` Phase 2, `attractor-79z`) | the seven P2 modules; source cut at this repo's `4bdc47a` (2026-08-28) | `4d7aa2f` |
| 3 — `tool-pipeline-run` (`attractor-24e`) | de-attractorized here first (this repo's `1b7bac5`), then moved | `bbe3ba9`, SHAs corrected by `1dfc78b` |

## `tool-report-outcome` stayed, and why

It was part of Extraction 1, so dot-runner has its history — and then **dot-runner
deleted it on purpose**: commit `4a3a4da` (2026-08-29), *"fix(spec-repair): remove
report_outcome tool, full stop (Part 1)"*. dot-runner's `bundle.md` states the
reason in its own description: `status.json` (canonical §4.5 / Appendix C) is the
taught, spec-native verdict channel there.

So there is no dot-runner home to point at, and there are live consumers of the
tool through this bundle:

- `behaviors/attractor-core.yaml` mounts it by relative source — the one
  same-repo module source left in this repo.
- `profiles/attractor-e2e-anthropic.yaml` and `profiles/attractor-e2e-gemini.yaml`
  mount it.
- Downstream, `amplifier-app-actions`' `bundles/github-tools.bundle.md` includes
  this bundle with the comment *"Attractor core — provides report_outcome tool +
  attractor: namespace"*.

Deleting it would have broken those with nowhere to send them. It is the one
module this repo owns.

## Nothing was lost in the slim — how that was checked

Comparing this repo's copies at `1b7bac5` (the commit before deletion) with
dot-runner @ `1dfc78b`:

- `tool-pipeline-run` is **byte-identical**.
- `remote-source`, `hooks-pipeline-progress`, `hooks-tool-truncation`,
  `tool-apply-patch`, `tool-dashboard-query`, `tool-pipeline-status` differ only
  by dot-runner committing a `uv.lock` (this repo's `.gitignore` excludes them) —
  content-identical.
- `loop-pipeline`, `pipeline-runner`, `unified-llm-client`, `loop-agent`,
  `hooks-pipeline-observability` differ because **dot-runner moved ahead**: new
  files there (`default_worker.py`, `provider_detection.py`, `prompts/`, new
  tests) that this frozen copy never had. That divergence is precisely why the
  compat window had to close.
- Fourteen files existed only in this repo's frozen copies (e.g. `feedback.py`,
  `test_spec_conformance_matrix.py`). **Every one was added before Extraction 1's
  cut** (newest: 2026-08-18, cut: 2026-08-26), so every one was carried into
  dot-runner's history and subsequently refactored, relocated, or retired *there*
  — dot-runner's decisions, in dot-runner's history, not losses here.
- The two substantive edits this repo made to a frozen copy *after* Extraction 1
  (`316aec9` loop-agent history hydration, `9b4fb5b` worker-parity-kit wiring)
  were both inside Extraction 2's range: their test files are byte-identical in
  dot-runner, and `_hydrate_history_from_context` is present in
  dot-runner's `agent_session.py`.

## Related pointers

- Guards that could not be decoupled from the live engine went with it and now
  run in dot-runner's CI — named in [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
  section 5, Layer 1.
- The conformance matrix's executing home is dot-runner's
  `ledger/rows.yaml` + `ledger/checks/` — see `docs/OPERATIONS.md` Layer 2.
- The vendored canonical nlspec is byte-identical here (`specs/canonical/`) and
  in dot-runner (`contracts/external/`); see [`specs/README-DISPOSITION.md`](specs/README-DISPOSITION.md)
  for why this repo still keeps its copy.
