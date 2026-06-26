# Profile Agent Bundles Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Create `agents:` entries in the pipeline E2E profile so that `session.spawn` can resolve agent names to actual agent bundles (loop-agent profiles), and add a `profiles` mapping in the orchestrator config to map provider names to agent names.

**Architecture:** The pipeline's `AmplifierBackend` spawns child sessions by `agent_name`. The `session.spawn` capability (registered at the app layer) looks up this name in `agent_configs` to find the bundle to load. Currently, no `agents:` entries exist in the pipeline profile, so spawn has nothing to resolve against. We need to: (1) add `agents:` entries mapping agent names to provider-specific loop-agent profiles, and (2) add `profiles:` in the orchestrator config mapping provider names (used in DOT node `llm_provider` attrs) to agent names.

**Tech Stack:** YAML bundle config, Amplifier agent resolution protocol

---

## Problem Statement

The pipeline E2E profile (`profiles/attractor-e2e-pipeline-anthropic.yaml`) currently has:
- No `agents:` section -- `session.spawn` cannot resolve any agent names
- No `profiles:` in the orchestrator config -- `AmplifierBackend` cannot map `llm_provider="anthropic"` to an agent name

The spawn call chain requires:
1. DOT node has `llm_provider="anthropic"` (or defaults to `"anthropic"`)
2. `AmplifierBackend._run_with_spawn()` looks up `self._profiles["anthropic"]` -> `"attractor-anthropic"`
3. Spawn is called with `agent_name="attractor-anthropic"`, `agent_configs={...}`
4. The app host resolves `"attractor-anthropic"` in `agent_configs` to find the bundle
5. The bundle is loaded as a child session with `loop-agent` orchestrator

Without steps 2 and 4 configured, the whole chain fails.

## Root Cause

The pipeline profile was written before the spawn integration was complete. The `agents:` and `profiles:` configuration was never added.

## Dependencies

- **Depends on:** Track2-2a2 (profile routing) -- without the code fix, the YAML config has no effect
- **Depended on by:** Track2-2b1 (E2E test) -- the test needs working agent resolution
- **Related:** Track2-2a1 (SubagentManager fix) -- independent

---

### Task 1: Add agents entries to the pipeline E2E profile

**Files:**
- Modify: `profiles/attractor-e2e-pipeline-anthropic.yaml`

**Step 1: Add the agents section**

Add an `agents:` block after the `includes:` section. Each entry maps an agent name to a bundle path (the provider-specific loop-agent profile).

The current file content (lines 1-36):
```yaml
bundle:
  name: attractor-e2e-pipeline-anthropic
  version: 0.1.0
  description: E2E test profile - Anthropic pipeline (spawns agent sessions)

includes:
  - bundle: attractor:behaviors/attractor-core

providers:
  ...
```

Insert the `agents:` block between `includes:` and `providers:`:

```yaml
bundle:
  name: attractor-e2e-pipeline-anthropic
  version: 0.1.0
  description: E2E test profile - Anthropic pipeline (spawns agent sessions)

includes:
  - bundle: attractor:behaviors/attractor-core

agents:
  attractor-anthropic:
    description: Anthropic coding agent (Claude) for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 120000}

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-20250514
  - module: provider-mock
    source: git+https://github.com/microsoft/amplifier-module-provider-mock@main

session:
  orchestrator:
    module: loop-pipeline
    source: ./modules/loop-pipeline
    config:
      dot_file: ./tests/e2e/fixtures/simple_file_creation.dot

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 120
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main

context:
  - path: context/system-anthropic.md
    role: system
```

**Step 2: Validate YAML syntax**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('profiles/attractor-e2e-pipeline-anthropic.yaml')); print('OK')"
```
Expected: `OK`.

**Step 3: Commit**
```
feat(profiles): add agents entry to pipeline E2E profile

Adds attractor-anthropic agent entry pointing to the Anthropic
loop-agent profile bundle. This enables session.spawn to resolve
the agent name to a loadable bundle for child session creation.

Part of Track 2: sessions-all-the-way-down agent resolution.
```

---

### Task 2: Add profiles mapping to orchestrator config

**Files:**
- Modify: `profiles/attractor-e2e-pipeline-anthropic.yaml`

**Step 1: Add profiles to the orchestrator config section**

Update the `session.orchestrator.config` to include a `profiles` mapping that connects DOT node `llm_provider` values to agent names.

Replace the orchestrator config block:

```yaml
session:
  orchestrator:
    module: loop-pipeline
    source: ./modules/loop-pipeline
    config:
      dot_file: ./tests/e2e/fixtures/simple_file_creation.dot
```

With:

```yaml
session:
  orchestrator:
    module: loop-pipeline
    source: ./modules/loop-pipeline
    config:
      dot_file: ./tests/e2e/fixtures/simple_file_creation.dot
      profiles:
        anthropic: attractor-anthropic
```

This means: when a DOT node has `llm_provider="anthropic"` (the default), `AmplifierBackend` will resolve it to `agent_name="attractor-anthropic"`, which is then looked up in the `agents:` section to find the bundle.

**Step 2: Validate YAML syntax**

Run:
```bash
python -c "import yaml; data = yaml.safe_load(open('profiles/attractor-e2e-pipeline-anthropic.yaml')); print(data['session']['orchestrator']['config']['profiles'])"
```
Expected: `{'anthropic': 'attractor-anthropic'}`.

**Step 3: Commit**
```
feat(profiles): add profiles mapping to pipeline orchestrator config

Maps llm_provider="anthropic" to agent_name="attractor-anthropic"
in the orchestrator config. This completes the resolution chain:
DOT node llm_provider -> profiles -> agent_name -> agents entry -> bundle.
```

---

### Task 3: Verify the complete resolution chain on paper

**Files:**
- No file changes (validation task)

**Step 1: Trace the full resolution chain**

With Tasks 1 and 2 applied, verify the config chain:

1. DOT node: `implement [shape=box, prompt="..."]` -- no `llm_provider` attr, defaults to `"anthropic"`
2. `AmplifierBackend._run_with_spawn()` (backend.py line 114):
   ```python
   provider = node.attrs.get("llm_provider", "anthropic")  # -> "anthropic"
   profile_name = self._profiles.get("anthropic", ...)      # -> "attractor-anthropic"
   ```
3. Spawn call: `agent_name="attractor-anthropic"`, `agent_configs={"attractor-anthropic": {"bundle": "attractor:profiles/attractor-profile-anthropic", ...}}`
4. App host resolves `"attractor-anthropic"` in configs -> loads `attractor:profiles/attractor-profile-anthropic`
5. That bundle configures `loop-agent` + `provider-anthropic` + tools -> child session runs

Run:
```bash
python -c "
import yaml

# Load pipeline profile
with open('profiles/attractor-e2e-pipeline-anthropic.yaml') as f:
    pipeline = yaml.safe_load(f)

profiles = pipeline['session']['orchestrator']['config']['profiles']
agents = pipeline.get('agents', {})

# Simulate resolution
provider = 'anthropic'
agent_name = profiles.get(provider)
agent_entry = agents.get(agent_name, {})

print(f'provider={provider}')
print(f'agent_name={agent_name}')
print(f'agent_bundle={agent_entry.get(\"bundle\", \"NOT FOUND\")}')

assert agent_name == 'attractor-anthropic'
assert 'attractor-profile-anthropic' in agent_entry.get('bundle', '')
print('Resolution chain: OK')
"
```
Expected: Resolution chain verified, `OK`.

**Step 2: Commit** (no commit needed -- validation only)

---

### Task 4: Create a multi-provider pipeline profile template

**Files:**
- Create: `profiles/attractor-e2e-pipeline-multi.yaml`

This is an optional but useful reference profile showing how a multi-provider pipeline would be configured.

**Step 1: Create the multi-provider profile**

```yaml
bundle:
  name: attractor-e2e-pipeline-multi
  version: 0.1.0
  description: >
    Multi-provider pipeline profile template.
    Shows how to configure multiple agent profiles for different
    llm_provider values in DOT node attributes.

includes:
  - bundle: attractor:behaviors/attractor-core

agents:
  attractor-anthropic:
    description: Anthropic coding agent (Claude) for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 120000}
  attractor-openai:
    description: OpenAI coding agent for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 10000}

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-20250514
  - module: provider-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
  - module: provider-mock
    source: git+https://github.com/microsoft/amplifier-module-provider-mock@main

session:
  orchestrator:
    module: loop-pipeline
    source: ./modules/loop-pipeline
    config:
      dot_file: ./tests/e2e/fixtures/simple_file_creation.dot
      profiles:
        anthropic: attractor-anthropic
        openai: attractor-openai

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 120
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main

context:
  - path: context/system-anthropic.md
    role: system
```

**Step 2: Validate YAML**

Run:
```bash
python -c "
import yaml
data = yaml.safe_load(open('profiles/attractor-e2e-pipeline-multi.yaml'))
print('agents:', list(data.get('agents', {}).keys()))
print('profiles:', data['session']['orchestrator']['config']['profiles'])
print('OK')
"
```
Expected:
```
agents: ['attractor-anthropic', 'attractor-openai']
profiles: {'anthropic': 'attractor-anthropic', 'openai': 'attractor-openai'}
OK
```

**Step 3: Commit**
```
feat(profiles): add multi-provider pipeline profile template

Reference profile showing how to configure multiple agent profiles
for different llm_provider values. Maps anthropic -> attractor-anthropic
and openai -> attractor-openai with corresponding agents entries.
```

---

## Summary

| Task | What | Est. Time |
|------|------|-----------|
| 1 | Add agents entries to pipeline E2E profile | 3 min |
| 2 | Add profiles mapping to orchestrator config | 2 min |
| 3 | Verify the complete resolution chain | 2 min |
| 4 | Create multi-provider pipeline profile template | 3 min |

**Total: ~10 minutes, 3 atomic commits**

## PR Details

**Title:** `feat(profiles): add agent bundles and profile routing to pipeline profiles`
**Branch:** `track2/2a3-profile-agent-bundles`
**Labels:** `track2`, `config`, `sessions-all-the-way-down`
**Description:** Adds `agents:` entries and `profiles:` config mapping to the pipeline E2E profile, completing the agent resolution chain: DOT node `llm_provider` -> orchestrator `profiles` -> `agents` entry -> bundle path -> child session. Also includes a multi-provider template showing the pattern for multiple providers.
