# Known Issues — pipeline-runner

## Box/agent nodes: run from within `--cwd` (process-cwd alignment)

**Resolved in issue-142.** The `loop-agent` orchestrator now reads the
`session.working_dir` capability from the coordinator in `AgentOrchestrator.execute()`
and injects it into the session config before the session is created. Both consumers
— the `Working directory:` line in the environment context and `discover_project_docs`
— are driven from the same resolved value. The resolution order is:

1. Explicit `working_dir` in the orchestrator config wins outright.
2. `coordinator.get_capability("session.working_dir")` when (1) is absent.
3. `os.getcwd()` as the last resort when neither exists.

Tool-only pipelines remain unaffected (tool nodes always root at `--cwd` via
`context.target_dir`).

**Historical note:** prior to issue-142, the workaround was to invoke `attractor run`
with the process working directory equal to `--cwd`:

```sh
cd <workdir> && attractor run pipeline.dot --cwd .
```

This workaround is no longer required for box/agent nodes.
