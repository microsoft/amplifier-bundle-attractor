# SubagentManager Spawn Rewrite Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix `SubagentManager` in `loop-agent` to use `coordinator.get_capability("session.spawn")` instead of the nonexistent `coordinator.spawn()` method, and handle the spawn capability's dict return type correctly.

**Architecture:** The `WaitTool.execute()` method currently calls `self._manager._coordinator.spawn(**spawn_kwargs)` which fails at runtime because coordinators have no `.spawn()` method. The correct pattern (already used by `AmplifierBackend` in `loop-pipeline/backend.py` line 105 and by `tool-delegate` in amplifier-foundation) is `coordinator.get_capability("session.spawn")` which returns a callable. The spawn function returns `{"output": str, "session_id": str}`, not a plain string. We must also pass the required kwargs: `agent_name`, `instruction`, `parent_session`, `agent_configs`.

**Tech Stack:** Python, amplifier_core (ToolResult, coordinator protocol), session.spawn capability

---

## Problem Statement

`WaitTool.execute()` at line 295 of `subagent_tools.py` calls:
```python
result = await self._manager._coordinator.spawn(**spawn_kwargs)
```

This is wrong for two reasons:
1. **`coordinator.spawn()` does not exist** -- coordinators expose capabilities via `get_capability()`, not direct methods
2. **Wrong kwargs** -- the current `spawn_kwargs` contains `instruction`, `working_dir`, `max_turns` but the `session.spawn` capability expects `agent_name`, `instruction`, `parent_session`, `agent_configs`, plus optional `orchestrator_config`, `provider_preferences`
3. **Wrong return handling** -- the code does `str(result)` but spawn returns `{"output": str, "session_id": str}`

## Root Cause

The subagent tools were written against an assumed coordinator API that doesn't match the actual Amplifier coordinator protocol. The correct pattern is capability-based: acquire the function via `get_capability`, then call it with the documented signature.

## Dependencies

- **Depends on:** Nothing -- this is a self-contained fix in one file
- **Depended on by:** Track2-2b1 (E2E spawn test) -- that test cannot pass until this fix lands
- **Related:** Track2-2a2 (profile routing) and Track2-2a3 (agent bundles) provide the `agent_configs` and `agent_name` values that this code passes to spawn, but this fix can land first using sensible defaults

---

### Task 1: Add spawn capability resolution to SubagentManager

**Files:**
- Modify: `modules/loop-agent/amplifier_module_loop_agent/subagent_tools.py`

**Step 1: Add lazy spawn capability resolution to SubagentManager.__init__**

Add two new instance variables and a resolution method. The spawn function is resolved lazily (on first use) to avoid import-time issues.

In `subagent_tools.py`, replace the `SubagentManager` class (lines 51-68):

```python
class SubagentManager:
    """Manages interactive subagent lifecycle.

    Holds state for all spawned agents and creates the four lifecycle
    tools. Uses the coordinator's ``session.spawn`` capability for actual
    execution when wait() is called.
    """

    def __init__(
        self,
        coordinator: Any,
        max_depth: int = 1,
        current_depth: int = 0,
    ) -> None:
        self._coordinator = coordinator
        self._max_depth = max_depth
        self._current_depth = current_depth
        self._agents: dict[str, SubagentState] = {}
        self._spawn_fn: Any | None = None
        self._spawn_checked: bool = False

    def _get_spawn_fn(self) -> Any | None:
        """Lazily resolve the session.spawn capability."""
        if not self._spawn_checked:
            if hasattr(self._coordinator, "get_capability"):
                try:
                    self._spawn_fn = self._coordinator.get_capability(
                        "session.spawn"
                    )
                except Exception:
                    pass
            self._spawn_checked = True
        return self._spawn_fn

    def create_tools(self) -> list[Any]:
        """Return the 4 subagent tools for registration."""
        return [
            SpawnAgentTool(self),
            SendInputTool(self),
            WaitTool(self),
            CloseAgentTool(self),
        ]
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
cd modules/loop-agent && python -c "from amplifier_module_loop_agent.subagent_tools import SubagentManager; print('OK')"
```
Expected: `OK` with no import errors.

**Step 3: Commit**
```
fix(loop-agent): add lazy spawn capability resolution to SubagentManager

SubagentManager now resolves session.spawn via coordinator.get_capability()
instead of assuming coordinator has a .spawn() method. Resolution is lazy
(first use) to avoid import-time coordinator dependency issues.

Part of Track 2: sessions-all-the-way-down spawn path fix.
```

---

### Task 2: Rewrite WaitTool.execute() to use session.spawn capability

**Files:**
- Modify: `modules/loop-agent/amplifier_module_loop_agent/subagent_tools.py`

**Step 1: Rewrite the execute method of WaitTool**

Replace the `WaitTool.execute()` method (lines 258-324) with the corrected version that:
- Resolves spawn via `self._manager._get_spawn_fn()`
- Builds kwargs matching the `session.spawn` signature (`agent_name`, `instruction`, `parent_session`, `agent_configs`)
- Handles the dict return type (`result["output"]`, `result["session_id"]`)

```python
    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        agent_id = arguments.get("agent_id", "")

        state = self._manager._agents.get(agent_id)
        if state is None:
            return ToolResult(
                success=False,
                output=f"Agent not found: {agent_id}",
            )

        # If already completed or failed, return cached result
        if state.status in ("completed", "failed"):
            return ToolResult(
                success=state.status == "completed",
                output=state.result or "",
            )

        # Resolve the spawn capability
        spawn_fn = self._manager._get_spawn_fn()
        if spawn_fn is None:
            return ToolResult(
                success=False,
                output=(
                    "session.spawn capability not available. "
                    "Subagent execution requires an app host that "
                    "registers this capability (e.g. amplifier CLI)."
                ),
            )

        # Build the instruction from task + pending messages
        instruction_parts = [state.task]
        if state.pending_messages:
            instruction_parts.append("\n\nAdditional instructions:")
            for msg in state.pending_messages:
                instruction_parts.append(f"- {msg}")
        instruction = "\n".join(instruction_parts)

        # Obtain parent_session and agent_configs from coordinator
        coordinator = self._manager._coordinator
        parent_session = getattr(coordinator, "session", None)
        coordinator_config = getattr(coordinator, "config", None) or {}
        agent_configs: dict[str, Any] = coordinator_config.get("agents", {})

        # Build spawn kwargs matching session.spawn signature
        spawn_kwargs: dict[str, Any] = {
            "agent_name": state.agent_id,
            "instruction": instruction,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
        }
        # Pass max_turns via orchestrator_config if set
        if state.max_turns:
            spawn_kwargs["orchestrator_config"] = {
                "max_turns": state.max_turns,
            }

        # Execute via session.spawn capability
        state.status = "running"
        try:
            result = await spawn_fn(**spawn_kwargs)

            # spawn returns {"output": str, "session_id": str}
            output = (
                result.get("output", "")
                if isinstance(result, dict)
                else str(result)
            )
            session_id = (
                result.get("session_id")
                if isinstance(result, dict)
                else None
            )

            state.status = "completed"
            state.result = output
            state.pending_messages.clear()

            logger.info(
                "Subagent %s completed (session=%s)", agent_id, session_id
            )
            return ToolResult(
                success=True,
                output=json.dumps(
                    {
                        "agent_id": agent_id,
                        "status": "completed",
                        "output": output,
                        "session_id": session_id,
                    }
                ),
            )
        except Exception as e:
            state.status = "failed"
            state.result = str(e)
            logger.error("Subagent %s failed: %s", agent_id, e)
            return ToolResult(
                success=False,
                output=json.dumps(
                    {
                        "agent_id": agent_id,
                        "status": "failed",
                        "error": str(e),
                    }
                ),
            )
```

**Step 2: Verify the edit is syntactically valid**

Run:
```bash
cd modules/loop-agent && python -c "from amplifier_module_loop_agent.subagent_tools import WaitTool; print('OK')"
```
Expected: `OK` with no import errors.

**Step 3: Commit**
```
fix(loop-agent): rewrite WaitTool.execute() to use session.spawn capability

- Resolves spawn fn via coordinator.get_capability("session.spawn")
  instead of the nonexistent coordinator.spawn()
- Passes correct kwargs: agent_name, instruction, parent_session,
  agent_configs, orchestrator_config
- Handles dict return type: result["output"] and result["session_id"]
- Returns early with clear error if session.spawn is not available

Fixes C-2: SubagentManager uses coordinator.spawn() which doesn't exist.
```

---

### Task 3: Update docstring and add unit test for spawn resolution

**Files:**
- Modify: `modules/loop-agent/amplifier_module_loop_agent/subagent_tools.py` (docstring only)
- Create: `modules/loop-agent/tests/test_subagent_spawn.py`

**Step 1: Update the module docstring**

Replace the module docstring (lines 1-13) to reflect the capability-based pattern:

```python
"""Interactive subagent lifecycle tools (GAP-AL-02).

Spec coverage: Section 7 (Subagents) -- spawn_agent, send_input, wait, close_agent.

Provides four tools for interactive subagent management:
  - spawn_agent: Register a task without executing immediately
  - send_input: Queue a message for a pending agent
  - wait: Trigger execution via session.spawn capability and block until completion
  - close_agent: Mark agent as closed, clean up state

These are registered by the AgentOrchestrator alongside mounted tools.
They use coordinator.get_capability("session.spawn") for actual execution.
"""
```

**Step 2: Create a unit test for the spawn resolution and WaitTool flow**

Create `modules/loop-agent/tests/test_subagent_spawn.py`:

```python
"""Tests for SubagentManager spawn capability resolution."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_loop_agent.subagent_tools import (
    SubagentManager,
    SpawnAgentTool,
    WaitTool,
)


def _make_coordinator(spawn_fn=None, session=None, agents=None):
    """Create a mock coordinator with optional session.spawn capability."""
    coordinator = MagicMock()
    coordinator.session = session

    config = {}
    if agents is not None:
        config["agents"] = agents
    coordinator.config = config

    if spawn_fn is not None:
        coordinator.get_capability = MagicMock(return_value=spawn_fn)
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    return coordinator


@pytest.mark.asyncio
async def test_wait_uses_session_spawn_capability():
    """WaitTool must call session.spawn via get_capability, not coordinator.spawn."""
    mock_spawn = AsyncMock(return_value={
        "output": "Task completed successfully",
        "session_id": "child-session-123",
    })
    coordinator = _make_coordinator(
        spawn_fn=mock_spawn,
        session="parent-session-abc",
        agents={"test-agent": {"session": {"orchestrator": {"module": "loop-agent"}}}},
    )

    manager = SubagentManager(coordinator, max_depth=2, current_depth=0)

    # Spawn an agent
    spawn_tool = SpawnAgentTool(manager)
    spawn_result = await spawn_tool.execute({"task": "Write hello.py"})
    assert spawn_result.success
    agent_id = json.loads(spawn_result.output)["agent_id"]

    # Wait for it
    wait_tool = WaitTool(manager)
    wait_result = await wait_tool.execute({"agent_id": agent_id})

    assert wait_result.success
    result_data = json.loads(wait_result.output)
    assert result_data["status"] == "completed"
    assert result_data["output"] == "Task completed successfully"
    assert result_data["session_id"] == "child-session-123"

    # Verify get_capability was called, NOT coordinator.spawn
    coordinator.get_capability.assert_called_with("session.spawn")
    mock_spawn.assert_called_once()

    # Verify spawn kwargs
    call_kwargs = mock_spawn.call_args[1]
    assert call_kwargs["instruction"] == "Write hello.py"
    assert call_kwargs["parent_session"] == "parent-session-abc"
    assert call_kwargs["agent_configs"] == {"test-agent": {"session": {"orchestrator": {"module": "loop-agent"}}}}


@pytest.mark.asyncio
async def test_wait_returns_error_when_no_spawn_capability():
    """WaitTool must return a clear error if session.spawn is not available."""
    coordinator = _make_coordinator(spawn_fn=None)
    manager = SubagentManager(coordinator)

    spawn_tool = SpawnAgentTool(manager)
    spawn_result = await spawn_tool.execute({"task": "Do something"})
    agent_id = json.loads(spawn_result.output)["agent_id"]

    wait_tool = WaitTool(manager)
    wait_result = await wait_tool.execute({"agent_id": agent_id})

    assert not wait_result.success
    assert "session.spawn capability not available" in wait_result.output


@pytest.mark.asyncio
async def test_wait_handles_spawn_exception():
    """WaitTool must handle spawn failures gracefully."""
    mock_spawn = AsyncMock(side_effect=RuntimeError("Connection lost"))
    coordinator = _make_coordinator(spawn_fn=mock_spawn)

    manager = SubagentManager(coordinator)

    spawn_tool = SpawnAgentTool(manager)
    spawn_result = await spawn_tool.execute({"task": "Fail task"})
    agent_id = json.loads(spawn_result.output)["agent_id"]

    wait_tool = WaitTool(manager)
    wait_result = await wait_tool.execute({"agent_id": agent_id})

    assert not wait_result.success
    result_data = json.loads(wait_result.output)
    assert result_data["status"] == "failed"
    assert "Connection lost" in result_data["error"]
```

**Step 3: Run the unit tests**

Run:
```bash
cd modules/loop-agent && python -m pytest tests/test_subagent_spawn.py -v
```
Expected: All 3 tests pass.

**Step 4: Commit**
```
test(loop-agent): add unit tests for SubagentManager spawn resolution

Tests verify:
- WaitTool calls session.spawn via get_capability (not coordinator.spawn)
- Correct kwargs passed: agent_name, instruction, parent_session, agent_configs
- Dict return type handled: output and session_id extracted
- Clear error returned when session.spawn capability is not available
- Spawn exceptions handled gracefully with failed status

Covers C-2 fix validation.
```

---

## Summary

| Task | What | Est. Time |
|------|------|-----------|
| 1 | Add lazy spawn capability resolution to SubagentManager | 2 min |
| 2 | Rewrite WaitTool.execute() with correct spawn pattern | 5 min |
| 3 | Update docstring + add unit tests | 4 min |

**Total: ~11 minutes, 3 atomic commits**

## PR Details

**Title:** `fix(loop-agent): use session.spawn capability in SubagentManager`
**Branch:** `track2/2a1-subagent-spawn-rewrite`
**Labels:** `track2`, `bug-fix`, `sessions-all-the-way-down`
**Description:** Fixes C-2 -- SubagentManager was calling the nonexistent `coordinator.spawn()` method. Now correctly uses `coordinator.get_capability("session.spawn")` matching the pattern established by `AmplifierBackend` and `tool-delegate`.
