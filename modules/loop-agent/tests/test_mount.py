"""Tests for module mount function and AgentOrchestrator creation."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_mount_registers_orchestrator():
    """mount() should register an orchestrator with the coordinator."""
    coordinator = AsyncMock()
    from amplifier_module_loop_agent import mount

    await mount(coordinator, config={})
    coordinator.mount.assert_called_once()
    args = coordinator.mount.call_args
    assert args[0][0] == "orchestrator"


@pytest.mark.asyncio
async def test_mount_passes_config_to_orchestrator():
    """mount() should pass config to the AgentOrchestrator."""
    coordinator = AsyncMock()
    from amplifier_module_loop_agent import mount, AgentOrchestrator

    await mount(coordinator, config={"max_tool_rounds_per_input": 50})
    args = coordinator.mount.call_args
    orchestrator = args[0][1]
    assert isinstance(orchestrator, AgentOrchestrator)


@pytest.mark.asyncio
async def test_mount_with_no_config():
    """mount() should work with None config."""
    coordinator = AsyncMock()
    from amplifier_module_loop_agent import mount

    await mount(coordinator, config=None)
    coordinator.mount.assert_called_once()


@pytest.mark.asyncio
async def test_orchestrator_has_execute_method():
    """AgentOrchestrator must have an execute method (Orchestrator protocol)."""
    from amplifier_module_loop_agent import AgentOrchestrator

    orchestrator = AgentOrchestrator(coordinator=MagicMock(), config={"system_prompt": "You are a test coding agent."})
    assert hasattr(orchestrator, "execute")
    assert callable(orchestrator.execute)
