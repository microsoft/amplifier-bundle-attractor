---
id: box-node-working-dir-forwarding
title: "Box node spawn does not forward context.target_dir as working_dir into orchestrator_config"
red_signal: working_dir missing from orchestrator_config passed to spawn
base_sha: 69ca97934ad998bb93b8161aff12d2e719b55295
target_repo: amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

When the pipeline engine executes a box (LLM/agent) node and spawns a child agent session, the `orchestrator_config` dict passed to the spawn function must include `working_dir` set to the pipeline's `context.target_dir` (the `--cwd` value). After the fix, a spawned agent's declared working directory — used to build the `Working directory:` line in its system-prompt environment block and to discover project docs (`AGENTS.md`, etc.) — matches the directory the pipeline was told to operate in, regardless of what the runner process's own `os.getcwd()` happens to be.

## Why this matters

Tool nodes (`tool_command=`) already resolve their subprocess working directory from `context.target_dir` (see `handlers/tool.py`). Box nodes do not receive the same treatment for their system-prompt environment context. In any invocation where the runner's process cwd differs from the pipeline's `--cwd` — a CI wrapper, a long-lived service driving multiple pipelines, or any caller that doesn't `cd` first — a spawned agent's system prompt reports the wrong working directory and may fail to discover the project docs that live beside the files its tools are correctly operating on.

The gap is documented in `modules/pipeline-runner/KNOWN_ISSUES.md` ("Box/agent nodes: run from within `--cwd`") as a deferred fix. The asymmetry between tool nodes and box nodes is the core of the issue.

**Defect site:** `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`, in `_run_with_spawn()`, the `orchestrator_config` dict built for the spawn call omits `working_dir` entirely. As a result, `SessionConfig.working_dir` (defined in `modules/loop-agent/amplifier_module_loop_agent/config.py:44`) remains `""` (its default), and `agent_session.py:596` falls back to `os.getcwd()`:

```python
working_dir = self._config.working_dir or os.getcwd()
```

**Contrast with the working case:** `context.target_dir` is already available at the `_run_with_spawn()` call site — it is used by `handlers/tool.py` and `handlers/pipeline.py` for the same purpose. It just isn't threaded into `orchestrator_config`.

## Definition of done

**What the verification script checks (automated):**

The script constructs an `AmplifierBackend` with a recording coordinator, calls `backend.run()` with a `PipelineContext` that has `context.target_dir` set to a sentinel path, and asserts that `orchestrator_config["working_dir"]` in the recorded spawn kwargs equals that sentinel path. The script exits 0 when the assertion holds (defect not present) and exits 1 when `working_dir` is absent or wrong (defect present).

**Human reviewer criteria:**

1. The fix does not break existing tests in `modules/loop-pipeline/tests/` or `modules/loop-agent/tests/` — run both suites and confirm they are green.
2. If `context.target_dir` is not set (e.g., the pipeline was run without `--cwd`), the fix must not inject an empty or `None` `working_dir` into `orchestrator_config` in a way that overrides the `SessionConfig` default with a worse value. The existing `{k: v for k, v in {...}.items() if v is not None}` filter in `backend.py` is the correct guard.
3. The fix is symmetric with how `handlers/tool.py` resolves its working directory from `context.target_dir`.

## Non-goals

- Fixing the `os.getcwd()` fallback in `agent_session.py` itself is not required; the correct fix is to ensure `working_dir` is populated before `AgentSession` is constructed.
- Changing how `session_cwd` is passed to `PreparedBundle.spawn()` in `pipeline-runner` is out of scope; that path correctly roots the child agent's filesystem/bash tools and is not the subject of this defect.
- Adding `working_dir` forwarding to the direct-provider tool-loop path (`_run_with_tool_loop`) is out of scope; that path does not construct an `AgentSession` with a `SessionConfig`.
