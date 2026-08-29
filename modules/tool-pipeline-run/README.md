# tool-pipeline-run

Agent-facing `run_pipeline` tool: lets an interactive session invoke a DOT
graph pipeline at runtime via `session.spawn`, wait for completion, and
return a structured result.

## Config

Mounted like any other Amplifier module, with a `config` dict supplied at
mount time. All keys are optional; every default below reproduces this
module's pre-existing (attractor-bundle) behavior exactly, so an
unconfigured mount is unaffected by anything in this section.

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `runner_agent` | `str` | `"attractor-pipeline-runner"` | Name of the agent `session.spawn` is called with to actually execute the pipeline. |
| `profiles` | `dict[str, str]` | `{}` (unset) | Maps required `llm_provider` names (parsed from the pipeline's DOT source) to the agent name that provides them, for pre-spawn provider-availability validation. Forwarded to the spawned child's `orchestrator_config["profiles"]`. |
| `mention_example` | `str` | `"@attractor:examples/pipelines/01-simple-linear.dot"` | Illustrative `@mention` example shown in this tool's own `description` and `input_schema["dot_file"]["description"]` (i.e. what the calling LLM sees). Purely cosmetic -- it does not affect actual `@mention` resolution, which is generic and handled by the coordinator's `mention_resolver` capability regardless of namespace. Only the text before the first `:` (the namespace) is reused in the shorter `description` form. |

### Why `mention_example` exists

This module is a sanctioned exception to the compat-window freeze ahead of
its planned move to a namespace-neutral module (see
`DESIGN-worker-registry-core-split.md` §6.3). Before that move, every
hard-coded reference to the `attractor` bundle name had to be pulled out of
the module's source so mounting it from a differently-named bundle would
not show that bundle's LLM a `run_pipeline` tool that advertises an
`@attractor:...` mention no such bundle has registered. The `runner_agent`
and `profiles` keys were already config-driven before this change; only
their *default values* remain attractor-specific, by design, so today's
attractor bundles keep working unconfigured.

## Tests

```bash
cd modules/tool-pipeline-run
uv sync
uv run pytest -q
```
