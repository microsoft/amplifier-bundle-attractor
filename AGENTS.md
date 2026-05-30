# AGENTS.md — amplifier-bundle-attractor

Conventions for AI coding agents (Amplifier, Claude Code, Cursor, etc.) and human contributors using them. Read this before making changes.

## What this repo is

DOT-graph pipeline engine and handler bundle. Implements the **attractor nlspec** — graph-as-program execution where DOT nodes are computation, edges are dispatch, and clusters are subgraphs. The canonical spec reference lives at `github.com/strongdm/attractor`; this repo extends it but does not contradict it.

## Phase-specific files

Before designing changes, read [`PRINCIPLES.md`](PRINCIPLES.md) — upstream-spec linkage to `strongdm/attractor`, intentional deltas, and the "walk upstream first" discipline.

## Key directories

- `modules/loop-pipeline/amplifier_module_loop_pipeline/` — engine and handlers. `engine.py` is the dispatch core; handlers/ contains node-type implementations.
- `modules/loop-pipeline/tests/` — unit tests (1049+ passing as of recent `main`).
- `examples/pipelines/` — canonical pipeline patterns. Useful as live test fixtures when verifying engine changes.
- `specs/` — our spec extensions and the canonical attractor reference.
- `docs/CONTRACTS.md` — engine-level contracts: M5 substitution, fail-fast policy, structural concurrency, and cross-consumer guidance.
- `docs/PIPELINE_PATTERNS.md` — design discipline for pipeline authors: when to use LLM nodes vs. tool nodes, the Direct Work + Code Verification pattern (SF), the Validation + Retry pattern (V+R), and the anti-pattern catalog.

## Test commands

Run these before opening a PR. The reviewer expects evidence in the PR body, not just "tests pass."

- **Unit tests**: `pytest modules/loop-pipeline/` (full suite).
- **Targeted unit tests**: `pytest modules/loop-pipeline/tests/test_<specific>.py -v` while iterating.
- **Live pipeline run** (required when touching `engine.py` or any handler): construct or pick a graph that exercises the changed code path and run it through any attractor-compatible resolver. A representative pipeline from `examples/pipelines/` is acceptable when it covers the path; otherwise build a minimal graph that does. Capture the resulting `events.jsonl` and include the relevant slice in the PR.

## Verification gradient

| Change type | Required verification |
|---|---|
| `engine.py`, handler code, dispatch logic | Unit tests **and** a live pipeline run exercising the changed path. Paste the relevant `events.jsonl` slice or run output. |
| Spec extensions in `specs/` | Unit tests **and** a live pipeline run that demonstrates the new semantics. |
| Test fixtures, examples, docs | Unit tests sufficient. |

Unit tests alone are insufficient for engine and handler changes. Past bugs have shipped with green unit tests and failed on first real-graph run, specifically at the boundary between the engine's main loop and handler dispatch. The live-run gate exists because of that pattern.

## Common pitfalls (from session experience)

- **Two parallel fan-out paths**: the engine has both `ParallelHandler` (for `component`-shape nodes) and an engine-level `_execute_parallel_fan_out` (for non-component nodes with multi-edge fan-out). When touching either, check the dispatch logic around `engine.py:555-595`. It is easy to introduce a duplicate-dispatch path where both fire for the same node.
- **`tripleoctagon` (fan-in) special-case**: `engine.py` (around line 704) special-cases `tripleoctagon` such that the subgraph runner stops there. If you change subgraph termination semantics, this is the place that breaks first.
- **Per-branch event contract**: per-branch events emitted from `ParallelHandler` bubble to the main events stream as `pipeline:node_start` / `pipeline:node_complete` with a `via_parallel=True` marker. Downstream observability (and at least one bundle outside this repo) relies on this marker. Don't break that contract silently.
- **False-positive `ContractViolation`**: there is a known historical class of false-positive ContractViolation events triggered by the main loop re-firing after a handler-internal dispatch. Tests in `tests/test_contract_violation_event.py` and `tests/test_parallel_branch_observability.py` exist to lock this down — read them before changing the affected paths.
- **`HandlerRegistry` construction requires a `HandlerContext`** (#37): a new handler dependency is a new FIELD on `HandlerContext`, never a `**kwargs` entry. This exact wiring was bitten 5 times before it was made type-required.
- **Routing-termination outcomes go through `engine.terminate_pipeline()`** (#37): never construct a fresh `Outcome(FAIL, ...)` inline at a no-matching-edge boundary — it drops the handler's `failure_reason`.
- **Checkpoint resume is identity-gated via `RunIdentity`** (#39): never read run state keyed only by `logs_root`. Mismatched identity must hard-fail, never silently restart — side-effecting nodes would double-apply.
- **DOT parser normalizes IDs once at the tokenizer entry**, not per-consumer.

## Recurring bug classes — read before designing or reviewing

This ecosystem has five documented recurring bug species: incomplete assembly, lossy reconstruction, unscoped shared state, partial-coverage symmetry, and aspirational contract. The pitfalls above are scar-tissue instances of them.

- **Designing a change**: read [`docs/designs/RECURRING-BUG-CLASSES.md`](docs/designs/RECURRING-BUG-CLASSES.md).
- **Reviewing a PR**: read [`docs/designs/CODE-REVIEW-CHECKLIST.md`](docs/designs/CODE-REVIEW-CHECKLIST.md).

## Spec authority

When behavior is ambiguous, the canonical spec at `github.com/strongdm/attractor` is authoritative. Our implementation extends but does not contradict the spec. If you find yourself "fixing" something that is spec-conformant, stop and check `specs/` first.

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

## PR checklist

`.github/PULL_REQUEST_TEMPLATE.md` will appear automatically when you open a PR. Honor it. The boxes are not decorative.
