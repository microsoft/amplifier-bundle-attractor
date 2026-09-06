# tool-pipeline-run

Agent-facing `run_pipeline` tool: lets an interactive session invoke a DOT
graph pipeline at runtime via `session.spawn`, wait for completion, and
return a structured result.

## Config

Mounted like any other Amplifier module, with a `config` dict supplied at
mount time. All keys are optional. **The defaults name no bundle** -- this
module knows nothing about the bundle mounting it, so a bundle that wants
its own runner agent or its own `@mention` namespace advertised to the LLM
supplies them here.

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `runner_agent` | `str` | `"pipeline-runner"` | Name of the agent `session.spawn` is called with to actually execute the pipeline. |
| `profiles` | `dict[str, str]` | `{}` (unset) | Maps required `llm_provider` names (parsed from the pipeline's DOT source) to the agent name that provides them, for pre-spawn provider-availability validation. Forwarded to the spawned child's `orchestrator_config["profiles"]`. |
| `mention_example` | `str` | `"@<bundle>:path/to/pipeline.dot"` | Illustrative `@mention` example shown in this tool's own `description` and `input_schema["dot_file"]["description"]` (i.e. what the calling LLM sees). Purely cosmetic -- it does not affect actual `@mention` resolution, which is generic and handled by the coordinator's `mention_resolver` capability regardless of namespace. Only the text before the first `:` (the namespace) is reused in the shorter `description` form. |

A mounting bundle typically sets both name-bearing keys:

```yaml
  - module: tool-pipeline-run
    config:
      runner_agent: my-pipeline-runner
      mention_example: "@my-bundle:pipelines/01-simple-linear.dot"
```

### Why the defaults name no bundle

This module's source used to carry one specific bundle's name in two
places: the `@mention` namespace in the LLM-facing text above, and the
spawn default for `runner_agent`. Neither is resolution logic -- `@mention`
resolution is generic -- but LLM-facing text that confidently names a
namespace the mounting bundle has not registered is how a tool teaches its
caller to write a mention that resolves nowhere, and it fails at resolution
time rather than at read time.

The defaults are therefore neutral, and the placeholder default for
`mention_example` is deliberately an *obvious* placeholder: an unconfigured
mount should read as unconfigured. Bundles that relied on the old
bundle-specific defaults pass those same values as config instead and get
byte-identical behavior (pinned by
`test_prior_consumer_config_reproduces_its_llm_text_byte_identical` and
`test_prior_consumer_config_still_spawns_its_own_runner_agent`).

This is stage 1 of the module's planned move to a namespace-neutral home
(see `DESIGN-worker-registry-core-split.md` §6.3): de-attractorize in
place first, so the move itself can ride byte-identical.

## Tests

```bash
cd modules/tool-pipeline-run
uv sync
uv run pytest -q
```
