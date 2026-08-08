---
id: cmd001-ship-check-pipe-masked-exit
title: "examples/patterns/task-runner.dot: ship_check CMD-001 pipe-masked exit code"
red_signal: CMD-001 ship_check pipe-masked exit detected
base_sha: 339637e6191ccddf5ab02f8066e44d624efaa661
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint(parse_dot("examples/patterns/task-runner.dot"))` returns **zero CMD-001 diagnostics** for the `ship_check` node. The `ship_check` gate's `tool_command` preserves the real exit code of the wrapped command rather than delegating it to a downstream `grep` filter stage.

## Why this matters

`examples/patterns/task-runner.dot` is the canonical exemplar for convergence-style task runners. Its `ship_check` gate currently ends its routing-determining command with:

```sh
git log --oneline -1 | grep -q .
```

In `/bin/sh` (the engine's execution environment) a pipeline's exit status is the **last stage's**. `grep -q .` exits 0 whenever it can read at least one non-empty line — effectively always. If the preceding command in the chain fails, `grep` may still exit 0 on whatever it received, and the gate records SUCCESS. This is the hazard `DOT-AUTHORING-GUIDE.md` (CMD-001) documents explicitly.

Because this file is the exemplar users copy, the defect propagates into every pipeline derived from it. The lint rule `CMD-001` fires on `ship_check` at the current SHA; there is no existing test that enforces CMD-001-clean status on this file (the existing `test_examples_lint_clean.py` only asserts no `ERROR`-severity diagnostics; CMD-001 is `WARNING`-severity).

## Definition of done

`DEFINITION.verify.sh` checks two things, both of which must pass:

1. **Existing test suites pass** — `tests/test_examples_lint_clean.py` and `tests/test_command_content_lint.py` both pass. These are the repo's own tests for `lint()` and the CMD-001/CMD-002 rules. A fix must not break the lint machinery.

2. **Zero CMD-001 findings on `ship_check`** — `lint(parse_dot("examples/patterns/task-runner.dot"))` returns no diagnostic with `rule == "CMD-001"` and `node_id == "ship_check"`. The fix may use any approach that eliminates the pipe-masked exit code: redirecting output (`cmd > /dev/null 2>&1`), using command substitution (`[ -n "$(cmd)" ]`), or capturing the exit code explicitly — as long as `lint()` no longer fires CMD-001 on this node.

The `verdict` node in the same file uses `ok=$?` to capture exit code before the final routing segment and is correctly not flagged; the fix must not disturb that node.

## Non-goals

- Changing `test_examples_lint_clean.py` to enforce WARNING-free status across the whole examples corpus (other legitimate WARNING patterns exist, e.g. TOPO-003 on linear examples).
- Adding a `--strict` CLI subcommand (the lint function is a Python API at this SHA; the CLI is out of scope).
- Fixing CMD-001 findings in any file other than `examples/patterns/task-runner.dot`.
- Changing the semantic behavior of the `ship_check` gate (it must still exit 0 on a clean, committed working tree and nonzero otherwise).
