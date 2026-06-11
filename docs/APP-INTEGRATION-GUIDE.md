# Application Integration Guide

How to use Attractor pipelines from a custom Python application.

## Overview

There are two execution paths for running Attractor pipelines programmatically:

| Path | Backend | Tools? | Best For |
|------|---------|--------|----------|
| **A: Direct LLM** | `DirectProviderBackend` | No | Analysis, reasoning, planning -- no file I/O |
| **B: Amplifier Session** | `AmplifierBackend` | Yes | Coding pipelines -- file edits, shell commands, full agent loop |

Both paths use the same DOT graph format and pipeline engine. The difference is
what happens at each LLM node: Path A makes a single LLM call; Path B spawns a
full Amplifier child session with tools.

See [examples/programmatic_usage.py](../examples/programmatic_usage.py) for a
complete, runnable example covering both paths.

## Path A: DirectProviderBackend (No Session)

Best for analysis and reasoning pipelines where nodes only generate text.
No Amplifier session, no tools, no file operations.

### Complete Working Example

```python
import asyncio
import tempfile

from amplifier_module_loop_pipeline import DirectProviderBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.transforms import apply_transforms
from amplifier_module_loop_pipeline.validation import validate_or_raise

DOT = """
digraph {
    graph [goal="Explain the trade-offs between microservices and monoliths"]

    start     [shape=Mdiamond]
    research  [prompt="List the key trade-offs for: $goal", llm_provider="anthropic"]
    synthesis [prompt="Synthesize the research into a concise 3-paragraph summary"]
    done      [shape=Msquare]

    start -> research -> synthesis -> done
}
"""

async def main():
    # 1. Parse the DOT graph
    graph = parse_dot(DOT)
    context = PipelineContext()

    # 2. Apply transforms (variable expansion, stylesheet)
    apply_transforms(graph, context)

    # 3. Validate structure (start/exit nodes, edges, attributes)
    validate_or_raise(graph)

    # 4. Create backend -- provider=None auto-creates unified_llm.Client from env vars
    backend = DirectProviderBackend(provider=None)

    # 5. Build and run the engine
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(backend=backend),
        logs_root=tempfile.mkdtemp(prefix="attractor-"),
    )

    outcome = await engine.run()
    print(f"Status: {outcome.status.value}")
    if outcome.notes:
        print(f"Result: {outcome.notes[:500]}")

asyncio.run(main())
```

### Requirements

```
pip install "amplifier-module-loop-pipeline @ git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-pipeline"
```

This automatically installs `unified-llm-client` (bundled at `modules/unified-llm-client/`
in the same repo). If you need `unified-llm-client` standalone:

```
pip install "unified-llm-client @ git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/unified-llm-client"
```

Plus at least one API key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`.

### When to Use Path A

- Analysis and reasoning tasks (no file I/O needed)
- Quick prototyping of pipeline logic
- Batch processing where each node produces text output
- Integration into existing systems that manage their own file operations

### Limitations

- No tools -- nodes cannot read/write files or run shell commands
- No agent loop -- each node gets a single LLM call (with tool-use rounds if
  tools are passed, but none are by default)
- `last_response` is truncated to 200 characters in context between nodes
  (use `fidelity="full"` to preserve full conversation history)

## Path B: AmplifierSession with session.spawn (Full Tools)

Best for coding pipelines where nodes need file operations, shell commands,
and the full agent tool loop. Each pipeline node gets its own child session.

### Complete Working Example

```python
import asyncio
from pathlib import Path
from typing import Any

from amplifier_foundation import Bundle, load_bundle
from amplifier_foundation.bundle import PreparedBundle

ATTRACTOR_BUNDLE = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
    "#subdirectory=profiles/attractor-profile-anthropic"
)

DOT = """
digraph {
    graph [goal="Create a Python function that checks if a number is prime"]
    start     [shape=Mdiamond]
    implement [prompt="$goal. Write it to prime.py with type hints.", goal_gate=true]
    test      [prompt="Write pytest tests for prime.py and run them."]
    done      [shape=Msquare]
    start -> implement -> test -> done
}
"""


def register_spawn_capability(session: Any, prepared: PreparedBundle) -> None:
    """Register session.spawn so pipeline nodes get full sub-sessions.

    This wires the AmplifierBackend: when the pipeline engine encounters
    an LLM node, it calls session.spawn to create a child session with
    the full tool set (filesystem, bash, search).
    """

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # Resolve agent name to config
        if agent_name in agent_configs:
            config = agent_configs[agent_name]
        elif agent_name in prepared.bundle.agents:
            config = prepared.bundle.agents[agent_name]
        else:
            available = list(agent_configs.keys()) + list(
                prepared.bundle.agents.keys()
            )
            raise ValueError(
                f"Agent '{agent_name}' not found. Available: {available}"
            )

        # Agent configs arrive in two shapes -- build the child bundle for each:
        #
        #   1. Lazy bundle-ref: a single-key dict {"bundle": "<uri>"}. It has
        #      no inline fields, so you MUST load_bundle() it. Building a
        #      Bundle(...) from config.get(...) here would produce a
        #      structurally empty child that inherits the parent's
        #      orchestrator and falls to a no-tools backend (an
        #      ImportError / silent fallback at runtime).
        #
        #   2. Inline config: a dict of bundle fields (tools/providers/
        #      instruction/...). Build the child Bundle(...) from those fields.
        if "bundle" in config and len(config) == 1:
            child_bundle = await load_bundle(config["bundle"])
        else:
            child_bundle = Bundle(
                name=agent_name,
                version="1.0.0",
                session=config.get("session", {}),
                providers=config.get("providers", []),
                tools=config.get("tools", []),
                hooks=config.get("hooks", []),
                instruction=config.get("instruction")
                or config.get("system", {}).get("instruction"),
            )

        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


async def main():
    # 1. Load the attractor profile bundle
    bundle = await load_bundle(ATTRACTOR_BUNDLE)

    # 2. Overlay with pipeline config containing our DOT source
    overlay = Bundle(
        name="my-pipeline",
        session={
            "orchestrator": {
                "module": "loop-pipeline",
                "config": {"dot_source": DOT},
            }
        },
    )
    composed = bundle.compose(overlay)

    # 3. Prepare (downloads modules, resolves dependencies)
    prepared = await composed.prepare()

    # 4. Create session
    session = await prepared.create_session(session_cwd=Path.cwd())

    # 5. Register session.spawn capability
    register_spawn_capability(session, prepared)

    # 6. Execute
    async with session:
        result = await session.execute("Run the pipeline")
        print(result)

asyncio.run(main())
```

### Requirements

```
pip install amplifier-foundation
```

Plus `ANTHROPIC_API_KEY` (or whichever provider your profile uses).

### The Session Lifecycle

```
load_bundle()       # Load attractor profile from git or local path
    |
compose(overlay)    # Overlay DOT source / pipeline config
    |
prepare()           # Download modules, resolve deps (EXPENSIVE -- do once)
    |
create_session()    # Create a session instance (CHEAP -- do per-request)
    |
register_spawn()    # Wire session.spawn for child sessions
    |
session.execute()   # Run the pipeline
```

**Key principle:** `prepare()` is expensive (module downloads, dependency
resolution). Call it once. `create_session()` is cheap -- call it for each
pipeline run.

### Offline / Pinned Execution

If the environment already has every module installed (CI runners, air-gapped
hosts, reproducible builds), use the supported offline path:

```python
prepared = await composed.prepare(install_deps=False)
```

`prepare(install_deps=False)` skips all network access: it discovers the paths
of already-installed modules and builds the resolver from them, but installs
nothing. The modules must already be importable in the active environment.

**Trap:** do not try to skip `prepare()` entirely by hand-feeding a raw
mount-plan to `create_session()`. The resolver (module path map, dependency
graph) is computed *inside* `prepare()` and cannot be precomputed or supplied
externally -- a hand-built mount-plan leaves the resolver unset and the session
fails to wire its modules. Always go through `prepare()`; use
`install_deps=False` when you need it to stay offline.

### How Backend Selection Works

The pipeline orchestrator auto-selects the backend at runtime:

1. If `session.spawn` capability is registered --> **AmplifierBackend** (full
   child sessions per node with tools)
2. Else if a provider is available --> **DirectProviderBackend** (LLM-only calls,
   no tools)
3. Otherwise --> simulation mode (for testing)

This means: if you forget to call `register_spawn_capability()`, the pipeline
silently falls back to DirectProviderBackend. Nodes will run but without tools.

### Composing DOT Source at Runtime

You can generate DOT graphs dynamically and overlay them:

```python
def build_pipeline(task_description: str, subtasks: list[str]) -> str:
    nodes = []
    edges = ["start -> plan"]
    for i, subtask in enumerate(subtasks):
        node_id = f"task_{i}"
        nodes.append(f'    {node_id} [prompt="{subtask}"]')
        if i == 0:
            edges.append(f"plan -> {node_id}")
        else:
            edges.append(f"task_{i-1} -> {node_id}")
    edges.append(f"task_{len(subtasks)-1} -> done")

    return f"""digraph {{
    graph [goal="{task_description}"]
    start [shape=Mdiamond]
    plan [prompt="Plan the approach for: $goal"]
{chr(10).join(nodes)}
    done [shape=Msquare]
    {chr(10).join(f"    {e}" for e in edges)}
}}"""

dot_source = build_pipeline("Build a REST API", ["Create models", "Add endpoints", "Write tests"])
overlay = Bundle(
    name="dynamic-pipeline",
    session={"orchestrator": {"module": "loop-pipeline", "config": {"dot_source": dot_source}}},
)
```

### Passing Provider Preferences

To override the default model at runtime:

```python
session = await prepared.create_session(
    session_cwd=Path.cwd(),
    provider_preferences=[
        {"provider": "anthropic", "model": "claude-opus-4-20250514"},
    ],
)
```

### Error Handling and Pipeline Outcomes

The pipeline engine returns a JSON string from `session.execute()`. Parse it
to get structured results:

```python
import json

async with session:
    result = await session.execute("Run the pipeline")
    data = json.loads(result)

    print(f"Status: {data['status']}")           # "success", "partial_success", "fail"
    print(f"Notes: {data.get('notes', '')}")
    print(f"Nodes completed: {data.get('nodes_completed', 0)}")
    print(f"Node statuses: {data.get('node_statuses', {})}")

    if data.get("failure_reason"):
        print(f"Failure: {data['failure_reason']}")
```

## Alternative: The Attractor Recipe

The `@recipes:examples/attractor/` recipe in the Amplifier recipes bundle
provides a **spec-to-code factory** approach. Instead of defining workflow
structure in DOT, you provide a spec and scenarios (acceptance tests), and the
recipe loops until the code passes all scenarios.

### When to Use Each

| Approach | Best For |
|----------|---------|
| **DOT Pipelines** | Visual, declarative workflows with explicit control flow, conditional routing, parallel execution, human gates, multi-provider routing |
| **Attractor Recipe** | Spec-driven code generation with convergence testing loops, where the workflow is always "generate code, test, fix, repeat until passing" |

The recipe approach is more opinionated (4 fixed stages: seed, generate,
validate, converge) but requires less pipeline design. DOT pipelines give you
full control over the workflow structure.

### Recipe Quick Reference

```bash
amplifier recipe run @recipes:examples/attractor/attractor.yaml \
    --context spec_path=./spec.md \
    --context scenarios_path=./scenarios/
```

The recipe handles:
- Spec analysis and scenario parsing
- Code generation with architectural planning
- Holdout scenario validation (scenarios are never shown to the code generator)
- Convergence loops with configurable iteration limits

See `@recipes:examples/attractor/README.md` for full documentation.

## Isolated Execution Environments

When pipeline nodes should execute inside Docker containers or remote hosts
rather than the local filesystem, use the **isolated agent profiles**. These
replace host-local tools (`tool-filesystem`, `tool-bash`, `tool-search`,
`tool-apply-patch`) with `env_*` tools from the `env-all` behavior bundle that
route operations through the configured execution environment.

### Using Isolated Profiles

Reference the isolated agent profiles in your bundle configuration:

```python
ATTRACTOR_BUNDLE = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
    "#subdirectory=agents/attractor-agent-anthropic-isolated"
)
```

Or when composing at runtime:

```python
from amplifier_foundation import Bundle

isolated_overlay = Bundle(
    name="isolated-pipeline",
    session={
        "orchestrator": {
            "module": "loop-pipeline",
            "config": {
                "dot_source": DOT,
                "execution_environment": {
                    "type": "docker",
                    "image": "python:3.12-slim",
                },
            },
        }
    },
)
```

### Available Isolated Profiles

| Standard Profile | Isolated Variant | Notes |
|-----------------|------------------|-------|
| `attractor-agent-anthropic` | `attractor-agent-anthropic-isolated` | env_* tools only |
| `attractor-agent-openai` | `attractor-agent-openai-isolated` | env_* replaces apply_patch too |
| `attractor-agent-gemini` | `attractor-agent-gemini-isolated` | Retains tool-web for grounding |

### Composition Rules

When composing bundles for isolated execution, follow these rules:

1. **Include `env-all` OR standard file/bash/search tools, never both.** Having
   both tool sets creates ambiguity -- the LLM may use host-local tools instead
   of environment tools, breaking isolation.

2. **`tool-report-outcome` and `hooks-tool-truncation` are environment-agnostic**
   and safe to include alongside either tool set. They are already included via
   the `attractor-core` behavior.

3. **`tool-web` is environment-agnostic** -- it makes HTTP requests, not
   filesystem operations. The Gemini isolated profile retains it.

4. **If building custom profiles**, start from the isolated variants and add
   only environment-agnostic modules. Do not add `tool-filesystem`, `tool-bash`,
   `tool-search`, or `tool-apply-patch` to an isolated profile.

## Known Limitations

| Limitation | Impact | Workaround |
|-----------|--------|------------|
| `last_response` truncated to 200 chars | Nodes lose full prior output under `compact`/`truncate` fidelity | Use `fidelity="full"` for nodes needing complete prior context |
| `DirectProviderBackend` nodes have no tools | Cannot read/write files or run commands | Use Path B (AmplifierSession) for coding pipelines |
| Provider model in global settings overrides bundle | Unexpected model selection | Use project-level `.amplifier/settings.yaml` |
| `PreparedBundle.spawn()` returns string | Requires JSON-in-string parsing for structured results | The engine handles this internally |
| Box node whose final assistant message is only tool-calls returns empty | The engine sees an empty result and treats it as a silent failure / fallback | Nudge the node prompt so the final message is non-empty text (e.g. "After running tools, end with a one-line summary -- the final message must not be empty") |
| LLM node has no model when the agent bundle carries no `default_model` | The node cannot select a provider/model and fails | Set an explicit model -- via the node's `llm_provider`/model attribute, `provider_preferences` on `create_session()`, or a `default_model` in the bundle |

## Further Reading

- [GETTING-STARTED.md](GETTING-STARTED.md) -- Installation and first run
- [DOT-AUTHORING-GUIDE.md](DOT-AUTHORING-GUIDE.md) -- Designing effective pipelines
- [DOT-SYNTAX.md](DOT-SYNTAX.md) -- Complete syntax reference
- [examples/programmatic_usage.py](../examples/programmatic_usage.py) -- Full runnable example
- `amplifier-foundation/examples/07_full_workflow.py` -- Foundation reference for spawn capability
