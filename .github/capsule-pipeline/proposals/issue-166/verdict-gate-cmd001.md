---
id: verdict-gate-cmd001
title: "bug-fix.dot verdict_gate triggers CMD-001: tool_command ends in a pipe to grep"
red_signal: [CMD-001] [verdict_gate] Tool node 'verdict_gate' tool_command ends in a pipe to 'grep' without pipefail
base_sha: 339637e6191ccddf5ab02f8066e44d624efaa661
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`examples/pipelines/practical/bug-fix.dot` produces zero CMD-001 findings when linted. Specifically, the `verdict_gate` node's `tool_command` must not end in a pipe to a recognised filter program (`grep`) as its final stage. The pipeline's documented routing behavior (emitting `ship` or `iterate` as `tool.last_line`, always exiting 0) must be preserved.

## Why this matters

CMD-001 flags a real hazard class: when a pipeline's exit code is determined by a filter (`grep`, `tail`, etc.) at the end of a pipe rather than by the command whose outcome matters, the gate can record SUCCESS even when the wrapped command failed. The `bug-fix.dot` pipeline is the canonical convergence exemplar in the shipped examples corpus — the pipeline that exists to verify bug fixes. A CMD-001 finding on its verdict gate is a credibility problem: the example that authors are most likely to copy demonstrates the hazard the guide documents.

The lint rule (`CMD-001` in `validation.py`) fires a WARNING (not ERROR), so `test_examples_lint_clean.py` currently passes. However, running lint in strict mode (warnings treated as errors) fails, and the finding is a genuine structural issue regardless of severity level.

## Definition of done

- `lint()` called on `examples/pipelines/practical/bug-fix.dot` produces **zero CMD-001 findings**.
- The `verdict_gate` node's `tool_command` still emits `ship` when `.ai/critique.md` contains a `^VERDICT:.*SHIP` line and emits `iterate` otherwise (including when the file does not exist).
- The command still exits 0 in both branches (required by the `&& outcome=success` edge conditions on the `ship` and `iterate` edges).
- The existing test suite (`test_examples_lint_clean.py`, `test_command_content_lint.py`) continues to pass without modification.

## Non-goals

- Changing the CMD-001 lint rule itself or its severity.
- Modifying `test_command_content_lint.py::test_verdict_gate_pattern_flagged` — that test uses a synthetic graph with the old command string and remains a valid positive-case example for the rule regardless of what ships in the DOT file.
- Altering any other node in `bug-fix.dot`.
- Changing the pipeline's observable routing behavior (edge conditions, `tool.last_line` values, or exit codes).
