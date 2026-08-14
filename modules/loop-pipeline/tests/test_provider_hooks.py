"""Tests for provider-level hook event emission (provider:request/response/error).

Validates that AmplifierBackend and DirectProviderBackend emit hook events
around unified_llm.generate() calls, and that deny hook results abort LLM calls.
"""

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub (same pattern as test_unified_llm_wiring.py)
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
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

from amplifier_module_loop_pipeline.pipeline_events import (
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
    PROVIDER_ERROR,
)


def test_provider_event_constants_exist():
    """Provider event constants are defined and follow naming convention."""
    assert PROVIDER_REQUEST == "provider:request"
    assert PROVIDER_RESPONSE == "provider:response"
    assert PROVIDER_ERROR == "provider:error"


# ---------------------------------------------------------------------------
# Task 2: Wire hooks parameter to AmplifierBackend
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.backend import AmplifierBackend


def test_amplifier_backend_accepts_hooks_param():
    """AmplifierBackend constructor accepts and stores a hooks parameter."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    hooks = object()  # any truthy value
    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=hooks,
    )
    assert backend._hooks is hooks


def test_amplifier_backend_hooks_defaults_to_none():
    """AmplifierBackend.hooks defaults to None when not provided."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
    )
    assert backend._hooks is None


# ---------------------------------------------------------------------------
# Task 3: _emit helper on AmplifierBackend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emit_helper_fires_event():
    """AmplifierBackend._emit() delegates to hooks.emit() when hooks provided."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    class _RecordingHooks:
        def __init__(self):
            self.events: list[tuple[str, dict]] = []

        async def emit(self, event: str, data: dict) -> Any:
            self.events.append((event, data))
            return type("HookResult", (), {"action": "continue", "data": None})()

    hooks = _RecordingHooks()
    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=hooks,
    )
    await backend._emit("test:event", {"key": "value"})
    assert len(hooks.events) == 1
    assert hooks.events[0] == ("test:event", {"key": "value"})


@pytest.mark.asyncio
async def test_amplifier_backend_emit_helper_noop_without_hooks():
    """AmplifierBackend._emit() is a no-op when hooks is None."""

    class _MockSession:
        config: dict[str, Any] = {}

    class _Coordinator:
        session = _MockSession()
        config: dict[str, Any] = {"agents": {}}

        def get_capability(self, name: str) -> Any:
            return None

    backend = AmplifierBackend(
        coordinator=_Coordinator(),
        profiles={},
        hooks=None,
    )
    # Should not raise
    result = await backend._emit("test:event", {"key": "value"})
    assert result is None


# ---------------------------------------------------------------------------
# Task 4+: Shared test helpers (used by remaining tests)
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus


class _MockSession:
    config: dict[str, Any] = {}


class NoSpawnCoordinator:
    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


class RecordingHooks:
    """Records emitted events and returns configurable HookResults."""

    def __init__(self, action: str = "continue"):
        self.events: list[tuple[str, dict]] = []
        self._action = action
        self._reason: str | None = None

    def set_deny(self, reason: str = "blocked"):
        self._action = "deny"
        self._reason = reason

    async def emit(self, event: str, data: dict) -> Any:
        self.events.append((event, data))
        return type(
            "HookResult",
            (),
            {
                "action": self._action,
                "data": None,
                "reason": self._reason,
            },
        )()

    @property
    def event_names(self) -> list[str]:
        return [e[0] for e in self.events]

    def get_data(self, event_name: str) -> list[dict]:
        return [d for e, d in self.events if e == event_name]


def _make_text_response(text: str) -> unified_llm.Response:
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class MockUnifiedClient:
    def __init__(self, responses: list[unified_llm.Response]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0

    async def complete(self, request: unified_llm.Request) -> unified_llm.Response:
        self.call_count += 1
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return _make_text_response("fallback")


def _make_node(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "implement",
        "prompt": "Build it",
        "attrs": {"llm_model": "test-model", "llm_provider": "test"},
    }
    defaults.update(kwargs)
    return Node(**defaults)


# ---------------------------------------------------------------------------
# provider:request emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_request():
    """AmplifierBackend emits provider:request before unified_llm.generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    assert "provider:request" in hooks.event_names
    data = hooks.get_data("provider:request")[0]
    assert data["model"] == "test-model"
    assert data["provider"] == "test"
    assert data["node_id"] == "implement"


# ---------------------------------------------------------------------------
# provider:response emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_response():
    """AmplifierBackend emits provider:response after successful generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    assert "provider:response" in hooks.event_names
    data = hooks.get_data("provider:response")[0]
    assert data["model"] == "test-model"
    assert data["provider"] == "test"
    assert data["node_id"] == "implement"
    assert "usage" in data
    assert data["usage"]["input_tokens"] == 10
    assert data["usage"]["output_tokens"] == 20
    assert data["usage"]["total_tokens"] == 30
    assert data["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_amplifier_backend_response_includes_step_count():
    """provider:response includes the number of tool loop steps."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    data = hooks.get_data("provider:response")[0]
    assert "step_count" in data
    assert isinstance(data["step_count"], int)


# ---------------------------------------------------------------------------
# provider:error emission tests
# ---------------------------------------------------------------------------


class FailingUnifiedClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, request: unified_llm.Request) -> Any:
        raise self._error


@pytest.mark.asyncio
async def test_amplifier_backend_emits_provider_error_on_sdk_error():
    """AmplifierBackend emits provider:error when unified_llm.generate() raises SDKError."""
    hooks = RecordingHooks()
    mock_client = FailingUnifiedClient(
        unified_llm.ServerError(
            message="Internal server error",
            provider="test",
            status_code=500,
        )
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    assert result.status == StageStatus.FAIL
    assert "provider:error" in hooks.event_names
    data = hooks.get_data("provider:error")[0]
    assert data["provider"] == "test"
    assert data["model"] == "test-model"
    assert data["node_id"] == "implement"
    assert data["error_type"] == "ServerError"
    assert data["retryable"] is True
    assert "Internal server error" in data["message"]


# ---------------------------------------------------------------------------
# deny hook aborts LLM call tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_deny_hook_aborts_llm_call():
    """When hooks return deny on provider:request, the LLM call is skipped."""
    hooks = RecordingHooks()
    hooks.set_deny("cost limit exceeded")
    mock_client = MockUnifiedClient([_make_text_response("should not reach")])

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    # LLM call should never have been made
    assert mock_client.call_count == 0
    # Outcome should be FAIL with the denial reason
    assert result.status == StageStatus.FAIL
    assert "cost limit exceeded" in (result.failure_reason or "")
    # Only provider:request should have been emitted (no response/error)
    assert hooks.event_names == ["provider:request"]


# ---------------------------------------------------------------------------
# Task 8: DirectProviderBackend emit pattern
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline import DirectProviderBackend


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_request():
    """DirectProviderBackend emits provider:request before generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    await backend.run(node, "do work", PipelineContext())

    assert "provider:request" in hooks.event_names
    data = hooks.get_data("provider:request")[0]
    assert data["provider"] == "test"
    assert data["model"] == "test-model"
    assert data["node_id"] == "step1"


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_response():
    """DirectProviderBackend emits provider:response after generate()."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    await backend.run(node, "do work", PipelineContext())

    assert "provider:response" in hooks.event_names
    data = hooks.get_data("provider:response")[0]
    assert "usage" in data
    assert data["finish_reason"] == "stop"
    # Cost is exposed as a top-level key (value is None for an unpriced model,
    # but the key must always be present for consumers to read).
    assert "cost_usd" in data


@pytest.mark.asyncio
async def test_direct_backend_emits_provider_error():
    """DirectProviderBackend emits provider:error on SDKError."""
    hooks = RecordingHooks()
    mock_client = FailingUnifiedClient(
        unified_llm.RateLimitError(
            message="Too many requests",
            provider="test",
            status_code=429,
        )
    )

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert result.status == StageStatus.FAIL
    assert "provider:error" in hooks.event_names
    data = hooks.get_data("provider:error")[0]
    assert data["error_type"] == "RateLimitError"
    assert data["retryable"] is True


@pytest.mark.asyncio
async def test_direct_backend_deny_hook_aborts_llm_call():
    """When hooks return deny on provider:request, DirectProviderBackend skips the LLM call."""
    hooks = RecordingHooks()
    hooks.set_deny("not approved")
    mock_client = MockUnifiedClient([_make_text_response("should not reach")])

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert mock_client.call_count == 0
    assert result.status == StageStatus.FAIL
    assert "not approved" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Task 9: Backward compatibility (hooks=None) and event ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_works_without_hooks():
    """AmplifierBackend still works when hooks is None (backward compat)."""
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=None,  # explicitly None
    )
    node = _make_node()
    result = await backend.run(node, "Build it", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_direct_backend_works_without_hooks():
    """DirectProviderBackend still works when hooks is None."""
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=None,
    )
    node = _make_node(id="step1")
    result = await backend.run(node, "do work", PipelineContext())

    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count > 0


@pytest.mark.asyncio
async def test_event_ordering_request_before_response():
    """provider:request is emitted before provider:response."""
    hooks = RecordingHooks()
    mock_client = MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "done"}')]
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node()
    await backend.run(node, "Build it", PipelineContext())

    names = hooks.event_names
    req_idx = names.index("provider:request")
    resp_idx = names.index("provider:response")
    assert req_idx < resp_idx
