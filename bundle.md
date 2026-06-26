---
bundle:
  name: attractor
  version: 0.1.0
  description: >
    Attractor coding agent and pipeline framework for Amplifier.
    Implements the StrongDM Attractor nlspec — a non-interactive coding agent
    structured as a graph of phases, sufficient for use in a Software Factory.

    Entry points:
      bundles/attractor-pipeline    — Multi-provider pipeline (route nodes to
                                      Anthropic, OpenAI, or Gemini via model stylesheet)
      bundles/attractor-interactive — Interactive agent with pipeline tool
      bundles/attractor-agent       — Standalone coding agent (defaults to Anthropic)
      profiles/*                    — Provider-specific single-agent profiles

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: attractor:behaviors/attractor-core

agents:
  # IMPORTANT: each child agent MUST declare an inline session.orchestrator running a
  # non-pipeline loop (e.g. loop-agent).  The spawn capability merges this agent's session:
  # key onto the parent config; without an explicit orchestrator the child would inherit the
  # parent's orchestrator (e.g. loop-pipeline) and recurse infinitely.
  attractor-profile-anthropic:
    description: Attractor coding agent with Anthropic provider
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config:
          default_command_timeout_ms: 120000
  attractor-profile-openai:
    description: Attractor coding agent with OpenAI provider
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config:
          default_command_timeout_ms: 10000
  attractor-profile-gemini:
    description: Attractor coding agent with Gemini provider
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config:
          default_command_timeout_ms: 10000
  attractor-pipeline-runner:
    description: Pipeline execution agent spawned by run_pipeline tool
    session:
      orchestrator:
        module: loop-pipeline
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-pipeline
        config:
          profiles:
            anthropic: attractor-agent-anthropic
            openai: attractor-agent-openai
            gemini: attractor-agent-gemini
---

# Attractor

A non-interactive coding agent and pipeline framework built on Amplifier.

## Entry Points

### Multi-Provider Pipeline (`bundles/attractor-pipeline`)

The **primary entry point** for running Attractor pipelines. Wires all three
providers (Anthropic, OpenAI, Gemini) so the DOT graph's model stylesheet can
route different nodes to different providers.

```yaml
includes:
  - bundle: attractor:bundles/attractor-pipeline
```

Provide a DOT graph via orchestrator config:

```yaml
session:
  orchestrator:
    config:
      dot_file: ./my-pipeline.dot
```

See `examples/pipelines/06-model-stylesheet.dot` for a multi-provider pipeline
example using CSS-like selectors to assign models per node.

### Standalone Coding Agent (`bundles/attractor-agent`)

A simpler entry point for using Attractor as a single coding agent (no
pipeline). Defaults to Anthropic but the user can override via
`provider_preferences` at the CLI.

```yaml
includes:
  - bundle: attractor:bundles/attractor-agent
```

Includes `amplifier-foundation` so it works standalone.

### Provider-Specific Profiles (`profiles/`)

Individual single-agent profiles for when you want a specific provider with
its aligned tool configuration:

- `attractor:profiles/attractor-profile-anthropic` — Anthropic (Claude Code aligned, edit_file)
- `attractor:profiles/attractor-profile-openai` — OpenAI (codex-rs aligned, apply_patch)
- `attractor:profiles/attractor-profile-gemini` — Gemini (gemini-cli aligned, web tools)

```yaml
includes:
  - bundle: attractor:profiles/attractor-profile-anthropic
```

## Architecture

```
attractor/
├── agents/                     # Spawnable agent definitions
│   └── pipeline-runner.yaml    # Pipeline execution agent (spawned by tool)
├── bundles/                    # Composed entry points
│   ├── attractor-pipeline.yaml     # Multi-provider pipeline (primary)
│   ├── attractor-interactive.yaml  # Interactive agent with pipeline tool
│   └── attractor-agent.yaml        # Standalone single agent
├── profiles/                   # Provider-specific agent profiles
│   ├── attractor-profile-anthropic.yaml
│   ├── attractor-profile-openai.yaml
│   └── attractor-profile-gemini.yaml
├── behaviors/
│   └── attractor-core.yaml     # Core tools + hooks (provider-agnostic)
├── modules/                    # Custom Amplifier modules
│   ├── loop-pipeline/          # DOT graph-driven pipeline orchestrator
│   ├── loop-agent/             # Agent loop orchestrator
│   ├── tool-pipeline-run/      # Runtime pipeline invocation tool
│   ├── tool-report-outcome/    # Pipeline outcome reporting tool
│   ├── tool-apply-patch/       # Patch-based file editing (OpenAI)
│   ├── hooks-tool-truncation/  # Tool output truncation hook
│   ├── hooks-pipeline-progress/ # Pipeline progress reporting hook
│   ├── hooks-pipeline-observability/ # Pipeline observability hooks
│   ├── tool-dashboard-query/   # Pipeline status queries via HTTP API
│   ├── tool-pipeline-status/   # Returns pipeline execution state
│   └── unified-llm-client/     # Multi-provider LLM client library
├── context/                    # System prompts per provider
│   ├── system-anthropic.md
│   ├── system-openai.md
│   ├── system-gemini.md
│   ├── pipeline-awareness.md   # Pipeline tool usage context
│   ├── dot-reference.md        # DOT syntax reference
│   └── isolated-environment-guidance.md  # Isolated execution guidance
└── examples/pipelines/         # Example DOT pipeline graphs
```
