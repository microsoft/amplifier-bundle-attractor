"""Tests for DOT node attribute passthrough across all backend paths.

Covers:
- **reasoning_effort**: Passes through all three execution paths with all
  valid values ("low", "medium", "high") and default (None).
- **max_agent_turns**: Passes through all three execution paths as
  ``max_tool_rounds`` (int-converted) for generate() and ``max_turns``
  (int-converted) in ``orchestrator_config`` for spawn.
- **allow_partial**: Already implemented in ``retry.py`` (lines 264-269)
  and tested in ``test_retry.py::test_allow_partial_on_exhaustion``.
  No additional tests needed here — see RETRY-005.

Execution paths tested:
- **Path A (spawn)**: ``AmplifierBackend._run_with_spawn()``
- **Path B (tool loop)**: ``AmplifierBackend._run_with_tool_loop()``
- **DirectProviderBackend**: ``DirectProviderBackend.run()``
"""

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub (same pattern as test_backend.py)
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline import DirectProviderBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockSession:
    config: dict[str, Any] = {}


class MockCoordinator:
    """Coordinator with session.spawn capability that records kwargs."""

    def __init__(self) -> None:
        self.spawn_called = False
        self.last_spawn_kwargs: dict[str, Any] = {}
        self.session = _MockSession()
        self.config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs: Any) -> dict[str, Any]:
        self.spawn_called = True
        self.last_spawn_kwargs = kwargs
        return {"output": "done", "session_id": "child-1"}


class NoSpawnCoordinator:
    """Coordinator without session.spawn (forces Path B / DirectProvider)."""

    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


def _make_generate_result(text: str = "done") -> unified_llm.GenerateResult:
    """Build a minimal unified_llm.GenerateResult for mocking generate()."""
    usage = unified_llm.Usage(
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    )
    response = unified_llm.Response(
        id="resp-mock",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
    )
    return unified_llm.GenerateResult(
        text=text,
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=usage,
        total_usage=usage,
        steps=[],
        response=response,
    )


def _make_node(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {"id": "implement", "prompt": "Build it"}
    defaults.update(kwargs)
    return Node(**defaults)


def _make_context() -> PipelineContext:
    return PipelineContext()


# ===================================================================
# Path A: AmplifierBackend spawn — reasoning_effort in orchestrator_config
# ===================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_spawn_passes_reasoning_effort_all_values(effort: str) -> None:
    """reasoning_effort '{effort}' in node attrs reaches orchestrator_config."""
    coordinator = MockCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic", "reasoning_effort": effort})
    await backend.run(node, "task", _make_context())

    assert coordinator.spawn_called
    orch_config = coordinator.last_spawn_kwargs.get("orchestrator_config", {})
    assert orch_config.get("reasoning_effort") == effort


@pytest.mark.asyncio
async def test_spawn_reasoning_effort_defaults_to_none() -> None:
    """Without reasoning_effort in node attrs, None reaches orchestrator_config."""
    coordinator = MockCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    await backend.run(node, "task", _make_context())

    assert coordinator.spawn_called
    orch_config = coordinator.last_spawn_kwargs.get("orchestrator_config", {})
    assert orch_config.get("reasoning_effort") is None


# ===================================================================
# Path B: AmplifierBackend tool loop — reasoning_effort to generate()
# ===================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_tool_loop_passes_reasoning_effort_all_values(
    effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reasoning_effort '{effort}' in node attrs reaches unified_llm.generate()."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel enables Path B
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model", "reasoning_effort": effort})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("reasoning_effort") == effort


@pytest.mark.asyncio
async def test_tool_loop_reasoning_effort_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without reasoning_effort in node attrs, None reaches unified_llm.generate()."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("reasoning_effort") is None


# ===================================================================
# DirectProviderBackend — reasoning_effort to generate()
# ===================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_direct_backend_passes_reasoning_effort_all_values(
    effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DirectProviderBackend forwards reasoning_effort '{effort}' to generate()."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(provider=object())
    node = _make_node(
        attrs={
            "llm_provider": "test",
            "llm_model": "test-model",
            "reasoning_effort": effort,
        }
    )
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("reasoning_effort") == effort


@pytest.mark.asyncio
async def test_direct_backend_reasoning_effort_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DirectProviderBackend passes None when reasoning_effort is not set."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(provider=object())
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("reasoning_effort") is None


# ===================================================================
# Path A: AmplifierBackend spawn — max_agent_turns in orchestrator_config
# ===================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", ["2", "8", "25"])
async def test_spawn_passes_max_agent_turns(turns: str) -> None:
    """max_agent_turns from node attrs reaches orchestrator_config as int."""
    coordinator = MockCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic", "max_agent_turns": turns})
    await backend.run(node, "task", _make_context())

    assert coordinator.spawn_called
    orch_config = coordinator.last_spawn_kwargs.get("orchestrator_config", {})
    assert orch_config.get("max_turns") == int(turns)


@pytest.mark.asyncio
async def test_spawn_max_agent_turns_defaults_to_none() -> None:
    """Without max_agent_turns in node attrs, None reaches orchestrator_config."""
    coordinator = MockCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    await backend.run(node, "task", _make_context())

    assert coordinator.spawn_called
    orch_config = coordinator.last_spawn_kwargs.get("orchestrator_config", {})
    assert orch_config.get("max_turns") is None


# ===================================================================
# Path B: AmplifierBackend tool loop — max_agent_turns as max_tool_rounds
# ===================================================================


@pytest.mark.asyncio
async def test_tool_loop_passes_max_agent_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_agent_turns='8' in node attrs reaches generate() as max_tool_rounds=8."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model", "max_agent_turns": "8"})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("max_tool_rounds") == 8


@pytest.mark.asyncio
async def test_tool_loop_max_agent_turns_defaults_to_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without max_agent_turns, max_tool_rounds defaults to _MAX_TOOL_LOOP_ROUNDS (20)."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("max_tool_rounds") == 20


# ===================================================================
# DirectProviderBackend — max_agent_turns as max_tool_rounds
# ===================================================================


@pytest.mark.asyncio
async def test_direct_backend_passes_max_agent_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DirectProviderBackend forwards max_agent_turns='8' as max_tool_rounds=8."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(provider=object())
    node = _make_node(
        attrs={
            "llm_provider": "test",
            "llm_model": "test-model",
            "max_agent_turns": "8",
        }
    )
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("max_tool_rounds") == 8


@pytest.mark.asyncio
async def test_direct_backend_max_agent_turns_defaults_to_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DirectProviderBackend uses _MAX_TOOL_LOOP_ROUNDS (20) when not set."""
    captured: dict[str, Any] = {}

    async def _fake_generate(**kwargs: Any) -> unified_llm.GenerateResult:
        captured.update(kwargs)
        return _make_generate_result('{"status": "success", "notes": "done"}')

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    backend = DirectProviderBackend(provider=object())
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())

    assert result.status.value == "success"
    assert captured.get("max_tool_rounds") == 20
