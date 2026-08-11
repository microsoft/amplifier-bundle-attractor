---
id: loop-agent-working-dir-capability
title: "loop-agent: spawned agent working_dir falls back to process cwd instead of pipeline cwd"
red_signal: working_dir capability not propagated to agent session
base_sha: faec8f21d16060fe035232f4500afe9d39f1f782
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

When a pipeline is run with a working directory that differs from the runner process's own
current directory, a spawned agent's declared working directory — used both in its
system-prompt environment context (the `Working directory:` line) and in project-doc
discovery (`discover_project_docs`) — must equal the pipeline's actual working directory,
not the runner process's `os.getcwd()`.

The `session.working_dir` capability is already registered on the coordinator by the
pipeline runner (the same seam that tool nodes already honour). The `loop-agent` module
must read that capability and use it as the working directory when no explicit
`working_dir` is present in the orchestrator config, before falling back to `os.getcwd()`.

The required resolution order:
1. An explicit `working_dir` in the orchestrator config wins.
2. `coordinator.get_capability("session.working_dir")` is used when (1) is absent.
3. `os.getcwd()` is strictly the last resort when neither exists.

## Why this matters

Tool nodes (`tool_command=`) already resolve correctly against the pipeline's real working
directory via `context.target_dir` / the `session.working_dir` capability. Box (LLM/agent)
nodes do not receive the same treatment for their system-prompt environment context, so in
any invocation where the runner process's cwd differs from the pipeline's intended working
directory, a box node's system prompt reports the wrong working directory and fails to
discover project docs (`AGENTS.md`, etc.) that live beside the files its tools are
correctly operating on.

This asymmetry is documented in `modules/pipeline-runner/KNOWN_ISSUES.md` ("Box/agent
nodes: run from within `--cwd`") as a deferred fix. This issue exists to un-defer it.

## Definition of done

1. **Capability propagation (environment context):** when a coordinator returns a path from
   `get_capability("session.working_dir")` and no explicit `working_dir` is present in the
   orchestrator config, the `Working directory:` line in the agent's system prompt equals
   the capability value — not `os.getcwd()`.

2. **Capability propagation (project-doc discovery):** under the same conditions,
   `discover_project_docs` walks from the capability path, so an `AGENTS.md` placed in
   the capability directory is found and included in the system prompt.

3. **Fallback preserved:** when the coordinator has no `session.working_dir` capability
   and no explicit `working_dir` is configured, the session still builds without error and
   uses `os.getcwd()` as before.

4. **Explicit config wins:** when `working_dir` is explicitly set in the orchestrator
   config, it is used regardless of what the coordinator capability returns.

5. **Regression test shipped:** the fix includes at least one test in
   `modules/loop-agent/tests/` that exercises the capability-propagation path through the
   public `AgentOrchestrator.execute()` surface and passes under `uv run pytest` inside
   `modules/loop-agent/`. The test may live in any file under that directory — in a new
   file, or as an extension of an existing file such as `test_system_prompt_wiring.py`.
   The verify script runs the full `modules/loop-agent/tests/` suite so any such test is
   exercised regardless of its filename.

The verify script drives `AgentOrchestrator.execute()` directly through behavioral probes
that confirm the capability value reaches both consumers (environment-context line and
project-doc content), without prescribing which internal surface (orchestrator execute,
session construction, or prompt-build time) the fix uses.

## Non-goals

- Changing how tool nodes resolve their working directory (already correct).
- Runner-side injection: no pipeline-runner code should write loop-agent-internal config
  keys; the fix lives on the loop-agent side of the spawn boundary.
- Deterministic concurrent isolation proof: a best-effort concurrent isolation probe is
  acceptable; the gate does not require thread barriers or interleaving guarantees.
- Changing the `os.getcwd()` last-resort fallback when neither capability nor explicit
  config is present.
