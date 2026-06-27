# Getting Started with Attractor

Multi-stage AI pipelines for code, built on Amplifier.

## Prerequisites

- **Amplifier CLI** installed (`pip install amplifier` or via `uv`)
- At least one provider API key set in your environment:
  - `ANTHROPIC_API_KEY` for Anthropic/Claude profiles
  - `OPENAI_API_KEY` for OpenAI profiles
  - `GEMINI_API_KEY` for Gemini profiles

## Install the Bundle

Add an attractor profile to your Amplifier config. Pick the provider you want:

```yaml
# .amplifier/config.yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=profiles/attractor-profile-anthropic
```

Available profiles: `attractor-profile-anthropic`, `attractor-profile-openai`, `attractor-profile-gemini`.

## Run Your First Pipeline

The simplest pipeline is a linear start-implement-done. Point the pipeline
orchestrator at a `.dot` file via bundle config:

```yaml
# .amplifier/config.yaml (or any bundle file)
includes:
  - bundle: attractor:bundles/attractor-pipeline
session:
  orchestrator:
    config:
      dot_file: examples/pipelines/01-simple-linear.dot   # or dot_source: "digraph { ... }"
```

Then run the configured bundle -- there are no pipeline-specific CLI flags. The
goal is carried by the DOT graph attribute
`graph [goal="Create a Python script that prints hello world"]` (or via
`params`), not a `--goal` flag. Running parses the DOT graph, spawns an LLM agent
for the `implement` node, and runs it to completion.

### Try a Multi-Stage Pipeline

The plan-implement-test pipeline adds structure -- point the orchestrator at its
`.dot` file:

```yaml
# .amplifier/config.yaml (or any bundle file)
includes:
  - bundle: attractor:bundles/attractor-pipeline
session:
  orchestrator:
    config:
      dot_file: examples/pipelines/02-plan-implement-test.dot
```

Set the goal in the DOT graph attribute
`graph [goal="Build a Python add(a,b) function with pytest tests"]` (or via
`params`).

### Try a Practical Pipeline

Fix a bug systematically (reproduce, diagnose, fix, regression test, verify):

```yaml
# .amplifier/config.yaml (or any bundle file)
includes:
  - bundle: attractor:bundles/attractor-pipeline
session:
  orchestrator:
    config:
      dot_file: examples/pipelines/practical/bug-fix.dot
```

Set the goal in the DOT graph attribute
`graph [goal="Fix the NullPointerError in UserService.getProfile()"]` (or via
`params`).

## Run Interactively

The interactive bundle gives you a conversational agent that can also invoke
pipelines on demand via the `run_pipeline` tool. Compose it into your config:

```yaml
# .amplifier/config.yaml (or any bundle file)
includes:
  - bundle: attractor:bundles/attractor-interactive
```

Then run the bundle and talk to it -- the agent drives pipelines for you via the
`run_pipeline` tool:

> "Run the plan-implement-test pipeline to add input validation to the login endpoint"

> "Build a test suite for the auth module using a parallel pipeline"

The agent can pick from the included example pipelines or generate a pipeline
inline from your description.

## Choosing a Provider Profile

Each profile wires a provider, tools aligned to that provider's conventions,
and a system prompt:

| Profile | Provider | Tools | Style |
|---------|----------|-------|-------|
| `attractor-profile-anthropic` | Claude | `edit_file`, `bash` (120s), `search` | Claude Code conventions |
| `attractor-profile-openai` | GPT/o-series | `apply_patch` (v4a diffs), `bash` (10s), `search` | codex-rs conventions |
| `attractor-profile-gemini` | Gemini | `filesystem`, `bash` (10s), `search`, `web` | gemini-cli conventions |

For multi-provider pipelines (different models per node), use the pipeline
bundle which wires all three:

```yaml
# .amplifier/config.yaml (or any bundle file)
includes:
  - bundle: attractor:bundles/attractor-pipeline
session:
  orchestrator:
    config:
      dot_file: examples/pipelines/06-model-stylesheet.dot
```

Set the goal in the DOT graph attribute `graph [goal="Refactor the auth
module"]` (or via `params`).

See [DOT-AUTHORING-GUIDE.md](DOT-AUTHORING-GUIDE.md) for how model stylesheets
route nodes to providers.

## Entry Points Summary

| Entry Point | Use Case |
|------------|----------|
| `bundles/attractor-pipeline` | Multi-provider DOT pipeline (primary) |
| `bundles/attractor-interactive` | Conversational agent + pipeline tool |
| `bundles/attractor-agent` | Standalone coding agent (no pipeline) |
| `profiles/attractor-profile-*` | Single-provider agent profiles |

## Running in Isolated Environments

When running pipelines inside Docker containers or remote hosts (via
`execution_environment` configuration), use the **isolated agent profiles**
instead of the standard ones. These profiles replace host-local file, bash, and
search tools with `env_*` tools that operate inside the isolated environment.

### Available Isolated Profiles

| Standard Profile | Isolated Variant |
|-----------------|------------------|
| `attractor-agent-anthropic` | `attractor-agent-anthropic-isolated` |
| `attractor-agent-openai` | `attractor-agent-openai-isolated` |
| `attractor-agent-gemini` | `attractor-agent-gemini-isolated` |

### Configuration Example

```yaml
# .amplifier/config.yaml -- isolated execution
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=agents/attractor-agent-anthropic-isolated
```

Or when configuring profiles for a pipeline:

```yaml
profiles:
  anthropic: attractor-agent-anthropic-isolated
  openai: attractor-agent-openai-isolated
  gemini: attractor-agent-gemini-isolated
```

### The Rule

Include `env-all` (isolated tools) **or** standard file/bash/search tools,
**never both**. Having both tool sets available creates ambiguity -- the LLM may
use host-local tools instead of environment tools, breaking isolation.

The `tool-report-outcome` and `hooks-tool-truncation` modules are
environment-agnostic and safe to include alongside either set. The Gemini
isolated profile also retains `tool-web` since it is not a file or execution
tool.

## Common Gotchas

### Use `uv run pytest` for testing modules

Each module has its own virtual environment managed by `uv`. Always test with:

```bash
cd modules/loop-pipeline && uv run pytest tests/ -q
```

Not `python -m pytest` -- that uses the wrong environment.

### Global settings can override bundle configuration

Your `~/.amplifier/settings.yaml` may override the model or orchestrator
specified in the bundle. If you see unexpected behavior (wrong model, missing
tools), check for project-level overrides:

```yaml
# .amplifier/settings.yaml (in the project directory)
# This overrides global settings for this project only
settings:
  provider:
    model: claude-sonnet-4-6
```

### `last_response` is truncated to 200 characters

By default, the prior node's response is available to the next node as context
but truncated to 200 characters. If a node needs the full output from a
previous node, set fidelity to `full`:

```dot
plan -> implement [fidelity="full"]
```

Or set it at the graph level:

```dot
graph [default_fidelity="full"]
```

See [DOT-AUTHORING-GUIDE.md](DOT-AUTHORING-GUIDE.md) for all fidelity modes.

### The superpowers behavior can override the orchestrator

If your Amplifier config includes the `superpowers` behavior (from foundation),
it may override the pipeline orchestrator with its own modes. The fix is merged
upstream. If you hit this, add a project-level `.amplifier/settings.yaml` that
excludes superpowers, or update your foundation bundle.

## What Next

- [DOT-AUTHORING-GUIDE.md](DOT-AUTHORING-GUIDE.md) -- How to design effective pipelines
- [DOT-SYNTAX.md](DOT-SYNTAX.md) -- Complete DOT syntax reference
- [APP-INTEGRATION-GUIDE.md](APP-INTEGRATION-GUIDE.md) -- Using pipelines from Python applications
- [examples/pipelines/](../examples/pipelines/) -- 15 example pipelines to study and reuse
- [examples/programmatic_usage.py](../examples/programmatic_usage.py) -- Programmatic integration example

## Development

Run all module tests:

```bash
for mod in modules/*/; do
    echo "=== $mod ===" && (cd "$mod" && uv run pytest tests/ -q)
done
```

End-to-end tests against live APIs:

```bash
cd tests/e2e && python live_pipeline_test.py
```

Requires at least `ANTHROPIC_API_KEY` set. See `tests/e2e/` for details.
