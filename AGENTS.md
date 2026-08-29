# AGENTS.md — amplifier-bundle-attractor

Conventions for AI coding agents (Amplifier, Claude Code, Cursor, etc.) and human contributors using them. Read this before making changes.

## What this repo is

DOT-graph pipeline engine and handler bundle. Implements the **attractor nlspec** — graph-as-program execution where DOT nodes are computation, edges are dispatch, and clusters are subgraphs. The canonical spec reference lives at `github.com/strongdm/attractor`; this repo extends it but does not contradict it.

## Phase-specific files

Before designing changes, read [`PRINCIPLES.md`](PRINCIPLES.md) — upstream-spec linkage to `strongdm/attractor`, intentional deltas, and the "walk upstream first" discipline.

## Quality protocol

[`docs/QUALITY_PROTOCOL.md`](docs/QUALITY_PROTOCOL.md) is binding for contributors and for agents working here. The arc every non-trivial change follows: **design** (with empirical probes where the engine's behavior is load-bearing) → **build** → **live proof** (real runs, real environments — "the test passes" is not "it works") → **independent adversarial review** (a fresh session that re-executes the evidence and tries to break it) → **the maintainer's explicit word**.

**No merge without the maintainer's explicit word.** The gate mechanics are unchanged — see "Merge discipline" below.

That document also carries the per-change-class evidence table, the five-layer drift defense against the upstream nlspec, and the meta-protocol governing how those rules are themselves amended and retired.

**The decision matrix governs every change here** -- code, docs, examples, philosophy, design-thinking, process. Moving *toward* the strongdm/attractor nlspec is supported; moving *away* from it is really hard and readily pushed back on; going into territory the nlspec is silent about is relatively resisted. Each tier owes a different toll before merge: [`docs/QUALITY_PROTOCOL.md`](docs/QUALITY_PROTOCOL.md) section 3. State your change's tier in the PR -- an unstated tier defaults in practice to the cheapest one.

**If you see something, do something.** During *any* work here, including work unrelated to the task at hand, watch for observations against the captured vision in [`docs/VISION.md`](docs/VISION.md) and capture them without derailing what you are doing: a GitHub issue labeled `vision-observation` citing the passage it bears on, plus an `## Observations` heading in the PR body if one arises mid-PR. Observations are non-blocking; "none arose" is an honest answer. Convention: [`docs/QUALITY_PROTOCOL.md`](docs/QUALITY_PROTOCOL.md) section 4.

## Key directories

- `modules/loop-pipeline/amplifier_module_loop_pipeline/` — engine and handlers. `engine.py` is the dispatch core; handlers/ contains node-type implementations.
- `modules/loop-pipeline/tests/` — unit tests (1049+ passing as of recent `main`).
- `examples/pipelines/` — canonical pipeline patterns. Useful as live test fixtures when verifying engine changes.
- `specs/` — our spec extensions and the canonical attractor reference.
- `docs/CONTRACTS.md` — engine-level contracts: M5 substitution, fail-fast policy, structural concurrency, and cross-consumer guidance.
- `docs/PIPELINE_PATTERNS.md` — design discipline for pipeline authors: when to use LLM nodes vs. tool nodes, the Direct Work + Code Verification pattern (SF), the Validation + Retry pattern (V+R), and the anti-pattern catalog.

## Test commands

Run these before opening a PR. The reviewer expects evidence in the PR body, not just "tests pass." CI (`.github/workflows/ci.yml`) runs all 13 modules per-directory (each has its own `uv`-managed environment — do not try to run them in one shared pytest process; that produces `--import-mode` collisions, cross-module state pollution, and bypasses each module's own `addopts`) plus a dedicated live-graph gate job (see below); this is the automated baseline, not a replacement for the manual verification below when it applies.

- **Unit tests**: `pytest modules/loop-pipeline/` (full suite).
- **Targeted unit tests**: `pytest modules/loop-pipeline/tests/test_<specific>.py -v` while iterating.
- **Live pipeline run** (required when touching `engine.py` or any handler): a baseline instance of this now runs automatically in CI — see `modules/loop-pipeline/tests/test_live_graph_gate.py`, which drives real DOT text through the real parser, engine, and handler dispatch and is the permanent, hermetic form of this check. It covers four specific, previously-regressed behaviors (parallel fan-out event counts, `attempt_count`/`auto_status` interaction, `attempt_count`+`failed_step`/`continue_on_fail` interaction, manager-loop child-engine event propagation). For changes that touch a code path NOT covered by that file, still construct or pick a graph that exercises the changed path and run it through any attractor-compatible resolver — a representative pipeline from `examples/pipelines/` is acceptable when it covers the path; otherwise build a minimal graph that does. Capture the resulting `events.jsonl` and include the relevant slice in the PR.

## Verification gradient

| Change type | Required verification |
|---|---|
| `engine.py`, handler code, dispatch logic | Unit tests **and** the automated live-graph gate (`test_live_graph_gate.py`, runs in CI). If the changed path isn't one of the four behaviors that file covers, **also** do a manual live pipeline run exercising it and paste the relevant `events.jsonl` slice or run output — the automated gate is a floor, not a ceiling. |
| Spec extensions in `specs/` | Unit tests **and** a live pipeline run that demonstrates the new semantics. |
| Test fixtures, examples, docs | Unit tests sufficient. |

Unit tests alone are insufficient for engine and handler changes. Past bugs have shipped with green unit tests and failed on first real-graph run, specifically at the boundary between the engine's main loop and handler dispatch. The live-run gate exists because of that pattern — it is now enforced by `test_live_graph_gate.py` running in CI on every PR, not solely by human memory.

## Common pitfalls (from session experience)

- **One parallel fan-out path**: `ParallelHandler` (for `component`-shape nodes) is the ONLY fan-out mechanism. The engine-level `_execute_parallel_fan_out` (multi-edge fan-out for non-component nodes) was retired when spec §3.3 single-best-edge selection was restored (T0-4) — when several conditional edges match, the engine deterministically picks one (weight, then lexical tiebreak). Do not reintroduce a second fan-out path; historical bugs came from duplicate dispatch between the two.
- **`tripleoctagon` (fan-in) special-case**: `engine.py` (around line 704) special-cases `tripleoctagon` such that the subgraph runner stops there. If you change subgraph termination semantics, this is the place that breaks first.
- **Per-branch event contract**: per-branch events emitted from `ParallelHandler` bubble to the main events stream as `pipeline:node_start` / `pipeline:node_complete` with a `via_parallel=True` marker. Downstream observability (and at least one bundle outside this repo) relies on this marker. Don't break that contract silently.
- **False-positive `ContractViolation`**: there is a known historical class of false-positive ContractViolation events triggered by the main loop re-firing after a handler-internal dispatch. Tests in `tests/test_contract_violation_event.py` and `tests/test_parallel_branch_observability.py` exist to lock this down — read them before changing the affected paths.
- **`HandlerRegistry` construction requires a `HandlerContext`** (#37): a new handler dependency is a new FIELD on `HandlerContext`, never a `**kwargs` entry. This exact wiring was bitten 5 times before it was made type-required.
- **Routing-termination outcomes go through `engine.terminate_pipeline()`** (#37): never construct a fresh `Outcome(FAIL, ...)` inline at a no-matching-edge boundary — it drops the handler's `failure_reason`.
- **Checkpoint resume is identity-gated via `RunIdentity`** (#39): never read run state keyed only by `logs_root`. Mismatched identity must hard-fail, never silently restart — side-effecting nodes would double-apply.
- **DOT parser normalizes IDs once at the tokenizer entry**, not per-consumer.
- **A shared dev venv hides module-boundary and environment bugs**: every module declares its own dependencies and CI runs each one isolated (`cd modules/<name> && uv sync && uv run pytest`). A dev venv that has accumulated other modules — or a shell carrying provider API keys — lets a test reach past what its own module declares, pass locally, and fail on a clean runner. Two instances landed in one day (#123, #126): 21 `loop-pipeline` tests passed only because the shell had provider keys set and the code fell through to a live client constructor, and a `tool-pipeline-run` test imported `amplifier_module_loop_pipeline`, which that module does not depend on. Before claiming a suite is green, run the exact command CI runs, in a fresh environment, with keys unset (`env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY`). When a contract genuinely spans two modules, split the test along the boundary so each side asserts its own half — do not import across it.

## Recurring bug classes — read before designing or reviewing

This ecosystem has five documented recurring bug species: incomplete assembly, lossy reconstruction, unscoped shared state, partial-coverage symmetry, and aspirational contract. The pitfalls above are scar-tissue instances of them.

- **Designing a change**: read [`docs/designs/RECURRING-BUG-CLASSES.md`](docs/designs/RECURRING-BUG-CLASSES.md).
- **Reviewing a PR**: read [`docs/designs/CODE-REVIEW-CHECKLIST.md`](docs/designs/CODE-REVIEW-CHECKLIST.md).

## Before you design, file, or submit

Before any design proposal, issue report, or code change touching engine or
spec-adjacent behavior, ask: **"Did you check what the strongdm/attractor
nlspec has to say about this first?"** This is "Walk upstream first"
(`PRINCIPLES.md`) made explicit as a gate:

1. **Check the nlspec first.** The canonical, pinned copies and the
   compatibility doctrine now live in
   [`amplifier-bundle-dot-runner`](https://github.com/microsoft/amplifier-bundle-dot-runner)
   (`specs/canonical/`) — this repo's own `specs/` copy is a temporary
   compat window, not the source of truth. Cite the section in your issue,
   PR, or design doc.
2. **Conform-fixes are easy yeses.** If the nlspec clearly defines the
   behavior and we implement it wrong or not at all, that's a "yes, fix it"
   (recent examples: support#497, support#498 — both spec-behavior holes,
   both sailed through review).
3. **Need it at all?** If the nlspec is silent, ask whether the need can be
   met *outside* the engine first: an extension/wrapper, pre/post-processing,
   or composing pipelines into something larger. Prefer those.
4. **True extensions/divergences face the hard bar.**
   [`SPEC_CONFORMANCE.md`](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/main/SPEC_CONFORMANCE.md)'s
   Compatibility doctrine (including rule 5, "Anchoring survives scope")
   plus a ledgered
   [`specs/EXTENSIONS.md`](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/main/specs/EXTENSIONS.md)
   entry — both authoritative in `amplifier-bundle-dot-runner` now — are
   required. Not the wild-west; "the spec didn't anticipate this shape"
   files an entry, it doesn't skip one.
5. **Evidence bar.** Cite the section(s) *and* state what the surrounding
   context says — proof the nlspec was read holistically, not a
   cherry-picked line wielded as leverage to push an agenda or design
   change. Spec silence is not support for a change: it routes to point 3
   above and to `amplifier-bundle-dot-runner`'s SPEC_CONFORMANCE rule 5 /
   `specs/EXTENSIONS.md` (point 4), and the preferred first answer to "the
   spec doesn't do X" is a different pipeline design, not a new feature.

When behavior is ambiguous, the canonical spec is authoritative. Our
implementation extends but does not contradict it. If you find yourself
"fixing" something that is spec-conformant, stop and check first.

## Dependency awareness rule

Attractor depends on `amplifier-core`, its own internal modules, and the strongdm/attractor nlspec (spec-only, no code import). Per
[REPOSITORY_RULES.md](https://github.com/microsoft/amplifier-foundation/blob/main/docs/REPOSITORY_RULES.md):
do not introduce references to other repositories in code, comments, docs, or test names. This
includes resolvers, orchestration platforms, application bundles, or any downstream consumer.

Exception: `amplifier-bundle-recipes` may be cited as historical inspiration — attractor is a
follow-up to that recipe-bundle work, and specific recipe patterns are a legitimate prior-art
reference.

If you need to describe *where* to run a pipeline, say "any attractor-compatible resolver" or
"any Amplifier session with the loop-pipeline module loaded" — not the name of a specific
downstream resolver.

## Merge discipline: CI Gate is required, never bypass it

Branch protection on `main` requires exactly one status check: **`CI Gate (all checks passed)`** (the aggregate job in `.github/workflows/ci.yml` — fails if the unit-test matrix, live-graph gate, or type-check is failure, cancelled, or skipped). Before running `gh pr merge` — auto, manual/UI, or `--admin` — check it:

    gh pr checks <n>

Confirm `CI Gate (all checks passed)` reports `pass`. If it's still pending, use `--auto` (merges once it's green); if it's red, fix it — don't merge past it.

**`--admin` bypasses the code-owner review requirement only.** That's a legitimate, routine use here: branch protection requires 1 code-owner approval, and a solo/sole-code-owner author cannot approve their own PR — `--admin` is how that PR still ships. **`--admin` must never be used to bypass a red or pending `CI Gate`.** GitHub does not technically stop you from doing so today (`enforce_admins` is off) — this is a discipline rule backed by a required check, not an unbypassable technical control, so it holds regardless of whether `enforce_admins` ever gets flipped on.

This rule exists because this repo previously shipped a fake type-check test (`skipif`'d away on every CI runner, so it always "passed") and ran the rest of CI as advisory-only — no required status check at all. On `CI Gate`'s first run as a required check, it caught a real red on `main` (a flaky timing assertion in `loop-agent`'s parallel-gating tests). Don't reopen either gap: don't let a check go back to advisory, and don't let `--admin` become the way around it.

## PR checklist

`.github/PULL_REQUEST_TEMPLATE.md` will appear automatically when you open a PR. Honor it. The boxes are not decorative.
