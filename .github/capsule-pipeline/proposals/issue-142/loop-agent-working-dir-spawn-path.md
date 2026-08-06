---
id: loop-agent-working-dir-spawn-path
title: "loop-agent: spawned agent working_dir falls back to os.getcwd() on pipeline spawn path"
red_signal: working_dir falls back to os.getcwd(): Working directory: /fake/process/cwd
base_sha: 12261deccc9da76cf0ff2d8b4270f30e8f1a335d
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

When `pipeline-runner` drives a pipeline that contains a box (LLM/agent) node,
the spawned agent's declared working directory — the `Working directory:` line in
its system prompt and the root used for project-doc discovery (`AGENTS.md`,
`CLAUDE.md`, etc.) — must match the pipeline's intended `--cwd` value
(`context.target_dir`), regardless of the runner process's own `os.getcwd()`.

Today, `backend.py` never includes `working_dir` in the `orchestrator_config`
dict it passes at spawn time, so `AgentSession._build_system_prompt_text()`
always falls back to `os.getcwd()` (the runner's process cwd) when building
Layer-2 environment context. Tool nodes are unaffected — they already root
correctly against `context.target_dir`. Box nodes must receive the same
treatment so that the agent's self-described environment and its project-doc
discovery point at the same directory its tools are actually operating in.

## Why this matters

The asymmetry is invisible when the process cwd happens to equal `--cwd` (the
common `cd /target && attractor run pipeline.dot --cwd .` workaround documented
in `modules/pipeline-runner/KNOWN_ISSUES.md`). It becomes observable — and
wrong — in any invocation where the two differ:

- A CI wrapper that drives multiple pipelines from one long-lived process
- Any caller that passes `--cwd /some/target` from a different working directory
- Future tooling that does not `cd` before invoking the runner

When it fires, the agent's system prompt reports the wrong working directory and
its project-doc discovery (`discover_project_docs` in
`modules/loop-agent/amplifier_module_loop_agent/system_prompt.py`) walks from
the wrong root, so the agent may fail to find `AGENTS.md` or other instruction
files that tool nodes are correctly writing and reading under `--cwd`.

## Definition of done

**What the verify script checks (automated):**

The script constructs an `AgentOrchestrator` with no `working_dir` key in
`orchestrator_config` (reproducing the backend.py spawn path) and patches
`os.getcwd()` to return a sentinel value distinct from any intended pipeline
cwd. It then calls `execute()` and inspects the `Working directory:` line in
the resulting system prompt.

- **Red (exit 1):** the system prompt's `Working directory:` line contains the
  patched `os.getcwd()` sentinel — the fallback is firing and the agent would
  report the wrong directory in a real invocation.
- **Green (exit 0):** the system prompt does not contain the process-cwd
  sentinel — the agent's declared working directory is not sourced from
  `os.getcwd()`.

The green condition is bound to the observable end-state behavior (what appears
in the system prompt), not to any particular fix approach. Both a backend-side
fix (inject `working_dir` into `orchestrator_config` from `context.target_dir`)
and an agent-side fix (resolve `working_dir` from a coordinator capability
before falling back) satisfy the check.

**Human-reviewer criteria (not automated):**

- The fix should not change the behavior of direct `AgentSession` construction
  where `working_dir` is already explicitly set (existing tests in
  `tests/test_system_prompt_wiring.py` cover this path and must remain green).
- If the fix reads `working_dir` from a coordinator capability, confirm that
  the capability is actually registered on all spawn paths (not only the
  pipeline-runner path), or that the fallback chain degrades gracefully when
  the capability is absent.
- The `KNOWN_ISSUES.md` workaround note in `modules/pipeline-runner/` should
  be removed or updated once the fix lands.

## Non-goals

- Changing how tool nodes (`tool_command=` nodes) resolve their working
  directory — they already root correctly against `context.target_dir`.
- Fixing `discover_project_docs` itself — the function is correct; the bug is
  that it receives the wrong `working_dir` argument.
- Addressing any other gap in the `session.working_dir` capability propagation
  beyond the pipeline-runner → loop-agent spawn path described here.
