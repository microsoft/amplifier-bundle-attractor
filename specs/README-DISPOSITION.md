# `specs/` — disposition after the P4 slim

The P4 slim (`attractor-28x`) deleted this repo's compat-window copies of the
engine modules. The Wave-2 deferral parked on that item asked the same question
of `specs/`: **retire the copies here IF the only readers were the deleted
modules' tests.** This file records the re-grep that answered it, and the
answer.

**Answer: they stay. The readers outlived the modules.** Every path below is
still read by something this repo ships. This is not a decision to keep a copy
"just in case" — it is the measured result of asking who reads it.

## The re-grep (run after the deletion, not before)

```
grep -rn -E "specs/(canonical|conformance|EXTENSIONS)" .
```

| Path | Verdict | Live readers in this repo after the slim |
|---|---|---|
| `specs/canonical/*-canonical.md` | **STAYS** | `tests/test_doc_consistency.py` (reads the retry-ceiling default out of it), `tests/test_explainer_doc_guard.py` D-215 (reads the lifecycle line out of it), `examples/drift-review/drift-review.dot` (its preflight hard-fails if `specs/canonical/attractor-spec-canonical.md` is missing), `tests/test_quality_protocol_guard.py` Q-302, plus `README.md`, `docs/DOT-AUTHORING-GUIDE.md`, `docs/ROUTING-REFERENCE.md`, `evals/guidance/rubric.md` |
| `specs/EXTENSIONS.md` | **STAYS** | `examples/drift-review/drift-review.dot` preflight, `examples/drift-review/check_findings.py`'s closed citation set, `.github/PULL_REQUEST_TEMPLATE.md`, `SPEC_CONFORMANCE.md`, `docs/OPERATIONS.md`, `README.md`, `context/engine-semantics.md` |
| `specs/conformance/attractor-matrix.yaml` | **STAYS, frozen** | `tests/test_quality_protocol_guard.py` Q-301b, `SPEC_CONFORMANCE.md` (cites rows `ATX-M-004n`, `ATX-M-022`, `ATX-M-F01`, `ATX-M-016` by id), `docs/VISION.md`, and the Layer-3 drift-review ledgers class, which scopes it explicitly |
| `specs/attractor-spec.md`, `specs/coding-agent-loop-spec.md`, `specs/unified-llm-spec.md` | **STAYS** | Already-retired working copies. Each carries its own banner saying the `canonical/` file is normative; `SPEC_CONFORMANCE.md` records the retirement. They are pointers, not a second source of truth |

## What is genuinely duplicated, and why that is tolerated for now

- The three `specs/canonical/*-canonical.md` files are **byte-identical** to
  dot-runner's `contracts/external/` copies (verified with `diff` at dot-runner
  `1dfc78b`). Retiring this copy would mean re-aiming two opinionated guards
  (`test_doc_consistency.py`, `test_explainer_doc_guard.py`) and an exemplar
  pipeline's preflight at a file in **another repository**, which CI here cannot
  read. That is a real design change, not a deletion, and it does not belong in
  a deletion event.
- `specs/EXTENSIONS.md` is **not** a duplicate: dot-runner's is larger and is the
  live ledger there. This one is this repo's own extensions record.
- `specs/conformance/attractor-matrix.yaml` has **no executing runner here any
  more** — that runner left with `loop-pipeline` and was superseded by
  dot-runner's `ledger/rows.yaml` + `ledger/checks/`, carrying the same
  `ATX-M-*` row ids. `docs/OPERATIONS.md` Layer 2 states this, and Q-301 now
  requires the status line to name that executing home so the file cannot go on
  claiming a defense it does not run.

## Retirement condition

Retire a path here when its last in-repo reader goes — not before. Concretely:

- `specs/canonical/` retires when `test_doc_consistency.py`,
  `test_explainer_doc_guard.py`, and `examples/drift-review/`'s preflight no
  longer need a local normative text (most likely by moving those claims to
  dot-runner, next to the code they describe).
- `specs/conformance/attractor-matrix.yaml` retires when `SPEC_CONFORMANCE.md`'s
  row citations and `docs/VISION.md`'s reference are re-pointed at dot-runner's
  ledger, and Q-301b is re-aimed in the same PR.
