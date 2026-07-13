# Known Issues — pipeline-runner

## Box/agent nodes: run from within `--cwd` (process-cwd alignment)

**Constraint:** for a pipeline that contains **box (LLM/agent) nodes**, invoke `attractor run` with the
process working directory equal to `--cwd` — e.g.

```sh
cd <workdir> && attractor run pipeline.dot --cwd .
```

rather than running from some other directory with `--cwd /elsewhere`.

**Why:** the runner threads `--cwd` into every spawned agent as `session_cwd`, and the agent's
**tools** (filesystem, bash) are correctly rooted there. But the `loop-agent` orchestrator that drives
a spawned agent currently derives the agent's *declared* working directory from `os.getcwd()` (the
runner's process cwd) instead of honoring the `session.working_dir` capability the way its sibling
`tool-bash` / `tool-filesystem` modules do. When the process cwd differs from `--cwd`, the agent's
system prompt (and its project-doc discovery) point at the wrong directory, so the agent can look in
the wrong place and fail to find files that tool nodes wrote at `--cwd`.

Tool-only pipelines are unaffected (tool nodes always root at `--cwd` via `context.target_dir`).

**Status:** this is a `loop-agent` bug, not a runner bug. The fix (have `loop-agent`'s `mount()` honor
`coordinator.get_capability("session.working_dir")`) is **deferred** — `loop-agent` is a shared
orchestrator and the change needs downstream-impact analysis before it lands. Tracked as a focused
follow-up task. Until then, use process-cwd alignment as above.
