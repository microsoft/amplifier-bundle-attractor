# Profile Routing Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix the `profiles={}` bug in `_build_backend()` so that `AmplifierBackend` receives a populated profiles dict that maps provider names to agent bundle names, enabling the pipeline to spawn the correct agent profile for each node.

**Architecture:** When `_build_backend()` constructs an `AmplifierBackend`, it hardcodes `profiles={}` (line 185 of `__init__.py`). The backend uses this dict in `_run_with_spawn()` to resolve which agent bundle to spawn for a given node's `llm_provider` attribute (e.g., `"anthropic"` -> `"attractor-anthropic"`). With an empty dict, `profile_name` resolves to `""` and spawn fails. The fix reads profiles from two sources: (1) explicit `profiles` key in orchestrator config, and (2) auto-discovery from `coordinator.config["agents"]`. The orchestrator config approach is primary (explicit is better than implicit); agents auto-discovery is the fallback.

**Tech Stack:** Python, Amplifier coordinator protocol, YAML bundle config

---

## Problem Statement

In `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py` at line 183-185:

```python
return AmplifierBackend(
    coordinator,
    profiles={},          # <-- BUG: always empty!
    provider=first_provider,
    tools=tools,
)
```

`AmplifierBackend._run_with_spawn()` (backend.py line 114) uses this dict:
```python
profile_name = self._profiles.get(
    provider, next(iter(self._profiles.values()), "")
)
```

With `profiles={}`, `profile_name` is always `""`, so `session.spawn` receives `agent_name=""` which will either fail or spawn the wrong agent.

## Root Cause

The `_build_backend()` function was written as a scaffold -- it correctly detects `session.spawn` but never wires the profiles configuration. The orchestrator config (`self.config` in `PipelineOrchestrator`) contains the user's config dict but `_build_backend()` is a standalone function that doesn't have direct access to it. The config must be threaded through.

## Dependencies

- **Depends on:** Nothing (self-contained fix)
- **Depended on by:** Track2-2a3 (creates the actual agent entries referenced by profiles) and Track2-2b1 (E2E test)
- **Related:** Track2-2a1 (SubagentManager spawn fix) -- independent, can land in parallel

---

### Task 1: Thread orchestrator config into _build_backend()

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py`

**Step 1: Add `orchestrator_config` parameter to `_build_backend()`**

Replace the `_build_backend` function signature and docstring (lines 151-168):

```python
def _build_backend(
    providers: dict[str, Any],
    tools: dict[str, Any],
    hooks: Any,
    coordinator: Any | None,
    orchestrator_config: dict[str, Any] | None = None,
) -> Any | None:
    """Auto-construct a backend from the available providers.

    Resolution order:
    1. If coordinator exposes ``session.spawn`` -> use AmplifierBackend
       (full "sessions all the way down").  Profiles are resolved from
       ``orchestrator_config["profiles"]`` or auto-discovered from
       ``coordinator.config["agents"]``.
    2. Else if at least one provider is available -> use
       DirectProviderBackend (mini agentic tool loop per node).
    3. Otherwise -> return None (codergen handler falls through to
       simulation mode).
    """
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
cd modules/loop-pipeline && python -c "import amplifier_module_loop_pipeline; print('OK')"
```
Expected: `OK` (no import errors).

**Step 3: Commit**
```
refactor(loop-pipeline): add orchestrator_config param to _build_backend

Prepares for profile routing by threading the orchestrator config
into the backend construction function. No behavioral change yet.
```

---

### Task 2: Populate profiles from config and agent auto-discovery

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py`

**Step 1: Replace the spawn backend construction block**

Replace lines 171-188 (the spawn detection + AmplifierBackend construction) with profile resolution logic:

```python
    # Try the full spawn-based backend first
    if coordinator is not None:
        spawn_fn = None
        if hasattr(coordinator, "get_capability"):
            try:
                spawn_fn = coordinator.get_capability("session.spawn")
            except Exception:
                pass
        if spawn_fn is not None:
            from .backend import AmplifierBackend

            # Resolve profiles: explicit config > auto-discovery from agents
            cfg = orchestrator_config or {}
            profiles: dict[str, str] = {}

            # Source 1: Explicit profiles mapping in orchestrator config
            # e.g. config.profiles = {"anthropic": "attractor-anthropic"}
            explicit_profiles = cfg.get("profiles")
            if isinstance(explicit_profiles, dict):
                profiles.update(explicit_profiles)

            # Source 2: Auto-discover from coordinator.config["agents"]
            # Each agent entry may have a "providers" list hinting which
            # provider names it supports. Fall back to using the agent
            # name itself as both key and value if no explicit profiles.
            if not profiles:
                coordinator_config = (
                    getattr(coordinator, "config", None) or {}
                )
                agents = coordinator_config.get("agents", {})
                for agent_name, agent_cfg in agents.items():
                    if isinstance(agent_cfg, dict):
                        # Use agent name as profile name
                        profiles[agent_name] = agent_name

            if profiles:
                logger.info(
                    "Using AmplifierBackend (session.spawn available, "
                    "profiles=%s)",
                    list(profiles.keys()),
                )
            else:
                logger.warning(
                    "Using AmplifierBackend but profiles dict is empty. "
                    "Pipeline nodes may fail to resolve agent profiles. "
                    "Add 'profiles' to orchestrator config or 'agents' "
                    "to the bundle."
                )

            return AmplifierBackend(
                coordinator,
                profiles=profiles,
                provider=first_provider,
                tools=tools,
            )
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
cd modules/loop-pipeline && python -c "import amplifier_module_loop_pipeline; print('OK')"
```
Expected: `OK`.

**Step 3: Commit**
```
fix(loop-pipeline): populate profiles dict from config and agents

_build_backend() now resolves profiles from two sources:
1. Explicit orchestrator config: config.profiles = {"anthropic": "attractor-anthropic"}
2. Auto-discovery from coordinator.config["agents"]

Fixes C-1: profiles={} was always empty, causing AmplifierBackend to
spawn with agent_name="" which fails at runtime.
```

---

### Task 3: Thread orchestrator config from PipelineOrchestrator.execute()

**Files:**
- Modify: `modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py`

**Step 1: Pass orchestrator config to _build_backend() in execute()**

In `PipelineOrchestrator.execute()` (line 263), change the `_build_backend` call to pass the orchestrator config:

Replace:
```python
            backend = _build_backend(providers, tools, hooks, coordinator)
```

With:
```python
            backend = _build_backend(
                providers, tools, hooks, coordinator, self.config
            )
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
cd modules/loop-pipeline && python -c "from amplifier_module_loop_pipeline import PipelineOrchestrator; print('OK')"
```
Expected: `OK`.

**Step 3: Commit**
```
fix(loop-pipeline): pass orchestrator config to _build_backend

PipelineOrchestrator.execute() now threads self.config into
_build_backend() so the profiles mapping is available for
AmplifierBackend construction.

Completes C-1 fix: the config -> profiles -> AmplifierBackend pipeline
is now fully wired.
```

---

### Task 4: Add unit tests for profile resolution

**Files:**
- Create: `modules/loop-pipeline/tests/test_profile_routing.py`

**Step 1: Create the test file**

```python
"""Tests for profile routing in _build_backend()."""

from unittest.mock import MagicMock, patch

from amplifier_module_loop_pipeline import _build_backend


def _make_coordinator(has_spawn=True, agents=None):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    if has_spawn:
        coordinator.get_capability = MagicMock(
            return_value=MagicMock()  # mock spawn_fn
        )
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    config = {}
    if agents is not None:
        config["agents"] = agents
    coordinator.config = config
    return coordinator


def test_profiles_from_explicit_config():
    """Profiles in orchestrator config should be used directly."""
    coordinator = _make_coordinator(has_spawn=True)
    providers = {"anthropic": MagicMock()}

    orchestrator_config = {
        "profiles": {
            "anthropic": "attractor-anthropic",
            "openai": "attractor-openai",
        }
    }

    backend = _build_backend(
        providers, {}, None, coordinator, orchestrator_config
    )

    assert backend is not None
    assert backend._profiles == {
        "anthropic": "attractor-anthropic",
        "openai": "attractor-openai",
    }


def test_profiles_auto_discovered_from_agents():
    """When no explicit profiles, auto-discover from coordinator agents."""
    coordinator = _make_coordinator(
        has_spawn=True,
        agents={
            "attractor-anthropic": {"session": {"orchestrator": {"module": "loop-agent"}}},
            "attractor-openai": {"session": {"orchestrator": {"module": "loop-agent"}}},
        },
    )
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    assert "attractor-anthropic" in backend._profiles
    assert "attractor-openai" in backend._profiles


def test_explicit_profiles_override_auto_discovery():
    """Explicit profiles should take priority over auto-discovery."""
    coordinator = _make_coordinator(
        has_spawn=True,
        agents={"auto-agent": {"session": {"orchestrator": {"module": "loop-agent"}}}},
    )
    providers = {"anthropic": MagicMock()}

    orchestrator_config = {
        "profiles": {"anthropic": "my-custom-agent"}
    }

    backend = _build_backend(
        providers, {}, None, coordinator, orchestrator_config
    )

    assert backend._profiles == {"anthropic": "my-custom-agent"}
    # Auto-discovered agent should NOT be present
    assert "auto-agent" not in backend._profiles


def test_empty_profiles_still_creates_backend():
    """Backend should be created even with empty profiles (with warning)."""
    coordinator = _make_coordinator(has_spawn=True, agents={})
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    assert backend._profiles == {}


def test_no_spawn_falls_back_to_direct_provider():
    """Without session.spawn, should use DirectProviderBackend."""
    coordinator = _make_coordinator(has_spawn=False)
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    # Should be DirectProviderBackend, not AmplifierBackend
    assert not hasattr(backend, "_profiles")
```

**Step 2: Run the unit tests**

Run:
```bash
cd modules/loop-pipeline && python -m pytest tests/test_profile_routing.py -v
```
Expected: All 5 tests pass.

**Step 3: Commit**
```
test(loop-pipeline): add unit tests for profile routing

Tests verify:
- Explicit profiles from orchestrator config used directly
- Auto-discovery from coordinator.config["agents"] as fallback
- Explicit profiles take priority over auto-discovery
- Backend still created with empty profiles (graceful degradation)
- No spawn capability falls back to DirectProviderBackend

Covers C-1 fix validation.
```

---

## Summary

| Task | What | Est. Time |
|------|------|-----------|
| 1 | Add orchestrator_config param to _build_backend() | 2 min |
| 2 | Populate profiles from config + agent auto-discovery | 4 min |
| 3 | Thread config from PipelineOrchestrator.execute() | 2 min |
| 4 | Add unit tests for profile resolution | 4 min |

**Total: ~12 minutes, 4 atomic commits**

## PR Details

**Title:** `fix(loop-pipeline): populate profiles dict for AmplifierBackend`
**Branch:** `track2/2a2-profile-routing`
**Labels:** `track2`, `bug-fix`, `sessions-all-the-way-down`
**Description:** Fixes C-1 -- `_build_backend()` was hardcoding `profiles={}` when constructing `AmplifierBackend`. Profiles are now resolved from explicit orchestrator config or auto-discovered from `coordinator.config["agents"]`. This enables the pipeline to spawn the correct agent profile for each node based on its `llm_provider` attribute.
