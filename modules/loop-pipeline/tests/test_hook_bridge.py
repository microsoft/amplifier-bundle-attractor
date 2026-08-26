"""Tests for the hook bridge middleware (Phase 2).

Validates that create_hook_bridge() returns a middleware function that
bridges unified-llm-client middleware to Amplifier's hook system.
"""

import logging
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# amplifier_core stub (same as test_provider_hooks.py)
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

from amplifier_module_loop_pipeline.hook_bridge import (
    _current_node_context,
    create_hook_bridge,
    set_node_context,
)


def test_create_hook_bridge_returns_callable():
    """create_hook_bridge() returns a middleware function."""

    class _Hooks:
        async def emit(self, event, data):
            return type("R", (), {"action": "continue", "data": None})()

    middleware = create_hook_bridge(hooks=_Hooks())
    assert callable(middleware)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class RecordingHooks:
    """Records emitted events and returns configurable, kernel-faithful HookResults.

    amplifier-core's HookRegistry.emit() (hooks.rs:230-274) never returns
    action="modify" to its caller -- Modify only chains `current_data`
    between handlers *inside* emit()'s own dispatch loop; the action
    returned to the caller always collapses to "continue" (or deny/
    ask_user/inject_context, per hooks.rs's documented precedence) once no
    handler requests one of those. A double that fabricates action="modify"
    back to the caller is unfaithful to the kernel and can mask a broken
    consumer -- which is the exact anti-pattern amplifier-support#485 /
    PR #318 indicts (and which this class's own now-removed `set_modify()`
    used to model).

    For a "no handler touched this event" default, emit() echoes the event
    data back unchanged under action="continue" -- the real no-op shape.
    Use `set_deny()` for deny, or `set_tool_post_data()` to model a tool:post
    handler that replaced `current_data` (mirrors
    modules/loop-agent/tests/test_truncation_wiring.py's
    `_make_modifying_hooks`).
    """

    def __init__(self, action: str = "continue"):
        self.events: list[tuple[str, dict]] = []
        self._action = action
        self._reason: str | None = None
        self._tool_post_data: dict | None = None

    def set_deny(self, reason: str = "blocked"):
        self._action = "deny"
        self._reason = reason

    def set_tool_post_data(self, data: dict) -> None:
        """Model a tool:post handler that returned Modify.

        Kernel-faithful: the caller-visible action is still "continue" --
        only the returned `data` for the tool:post event differs from what
        was emitted (current_data is REPLACED by the handler's dict, not
        merged with the emitted event data).
        """
        self._tool_post_data = data

    async def emit(self, event: str, data: dict) -> Any:
        self.events.append((event, data))
        if self._action == "deny":
            return type(
                "HookResult",
                (),
                {"action": "deny", "data": dict(data), "reason": self._reason},
            )()
        result_data = (
            self._tool_post_data
            if event == "tool:post" and self._tool_post_data is not None
            else dict(data)
        )
        return type(
            "HookResult",
            (),
            {"action": "continue", "data": result_data, "reason": None},
        )()

    @property
    def event_names(self) -> list[str]:
        return [e[0] for e in self.events]

    def get_data(self, event_name: str) -> list[dict]:
        return [d for e, d in self.events if e == event_name]


def _make_request(
    model: str = "test-model", provider: str = "test"
) -> unified_llm.Request:
    return unified_llm.Request(
        model=model,
        messages=[unified_llm.Message.user("Hello")],
        provider=provider,
    )


def _make_response(text: str = "Hi") -> unified_llm.Response:
    return unified_llm.Response(
        id="resp-1",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


# ---------------------------------------------------------------------------
# Task 12: pre-request emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_emits_provider_request():
    """Hook bridge middleware emits provider:request before calling next_fn."""
    hooks = RecordingHooks()
    middleware = create_hook_bridge(hooks=hooks)

    token = set_node_context({"node_id": "step1"})
    try:
        request = _make_request()
        response = _make_response()

        async def next_fn(req):
            return response

        result = await middleware(request, next_fn)
        assert result is response

        assert "provider:request" in hooks.event_names
        data = hooks.get_data("provider:request")[0]
        assert data["model"] == "test-model"
        assert data["provider"] == "test"
        assert data["node_id"] == "step1"
        assert data["message_count"] == 1
    finally:
        _current_node_context.reset(token)


# ---------------------------------------------------------------------------
# Task 13: deny, post-response, error emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_deny_raises_abort_error():
    """Hook bridge raises AbortError when hooks return deny."""
    hooks = RecordingHooks()
    hooks.set_deny("cost limit")
    middleware = create_hook_bridge(hooks=hooks)

    token = set_node_context({"node_id": "step1"})
    try:
        request = _make_request()
        call_count = 0

        async def next_fn(req):
            nonlocal call_count
            call_count += 1
            return _make_response()

        with pytest.raises(unified_llm.AbortError, match="cost limit"):
            await middleware(request, next_fn)

        # next_fn should never have been called
        assert call_count == 0
    finally:
        _current_node_context.reset(token)


@pytest.mark.asyncio
async def test_middleware_emits_provider_response():
    """Hook bridge emits provider:response after successful call."""
    hooks = RecordingHooks()
    middleware = create_hook_bridge(hooks=hooks)

    token = set_node_context({"node_id": "step1"})
    try:
        request = _make_request()
        response = _make_response("Hello!")

        async def next_fn(req):
            return response

        await middleware(request, next_fn)

        assert "provider:response" in hooks.event_names
        data = hooks.get_data("provider:response")[0]
        assert data["model"] == "test-model"
        assert data["provider"] == "test"
        assert data["node_id"] == "step1"
        assert data["usage"]["input_tokens"] == 10
        assert data["usage"]["output_tokens"] == 5
        assert data["finish_reason"] == "stop"
    finally:
        _current_node_context.reset(token)


@pytest.mark.asyncio
async def test_middleware_emits_provider_error():
    """Hook bridge emits provider:error when next_fn raises SDKError."""
    hooks = RecordingHooks()
    middleware = create_hook_bridge(hooks=hooks)

    token = set_node_context({"node_id": "step1"})
    try:
        request = _make_request()

        async def next_fn(req):
            raise unified_llm.ServerError(
                message="Internal error", provider="test", status_code=500
            )

        with pytest.raises(unified_llm.ServerError):
            await middleware(request, next_fn)

        assert "provider:error" in hooks.event_names
        data = hooks.get_data("provider:error")[0]
        assert data["error_type"] == "ServerError"
        assert data["retryable"] is True
    finally:
        _current_node_context.reset(token)


# ---------------------------------------------------------------------------
# Task 14: create_middleware_client (provider-copy pattern)
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.hook_bridge import create_middleware_client


@pytest.mark.asyncio
async def test_create_middleware_client_copies_providers():
    """create_middleware_client copies providers from base client and adds middleware."""
    hooks = RecordingHooks()

    class _MockAdapter:
        name = "test"

        async def complete(self, request):
            return _make_response()

        def stream(self, request):
            raise NotImplementedError

    base_client = unified_llm.Client(
        providers={"test": _MockAdapter()},
        default_provider="test",
    )

    client = create_middleware_client(base_client, hooks=hooks)

    # Client should have the same providers
    assert "test" in client.providers
    assert client.default_provider == "test"
    # Client should have middleware
    assert len(client._middleware) > 0


def test_create_middleware_client_preserves_default_provider():
    """create_middleware_client preserves the base client's default_provider."""
    hooks = RecordingHooks()

    class _MockAdapter:
        name = "mock"

        async def complete(self, request):
            return _make_response()

        def stream(self, request):
            raise NotImplementedError

    base_client = unified_llm.Client(
        providers={"anthropic": _MockAdapter(), "openai": _MockAdapter()},
        default_provider="anthropic",
    )

    client = create_middleware_client(base_client, hooks=hooks)

    assert client.default_provider == "anthropic"
    assert set(client.providers.keys()) == {"anthropic", "openai"}


# ---------------------------------------------------------------------------
# Task 15: wrap_tool_with_hooks (tool:pre / tool:post)
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.hook_bridge import wrap_tool_with_hooks


@pytest.mark.asyncio
async def test_wrap_tool_emits_tool_pre_and_post():
    """wrap_tool_with_hooks emits tool:pre before and tool:post after execution."""
    hooks = RecordingHooks()
    call_log: list[str] = []

    original_tool = unified_llm.Tool(
        name="write_file",
        description="Write a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=None,
    )

    async def original_execute(**kwargs):
        call_log.append("executed")
        return "file written"

    original_tool.execute = original_execute

    token = set_node_context({"node_id": "step1"})
    try:
        wrapped = wrap_tool_with_hooks(original_tool, hooks)

        # Name and schema should be preserved
        assert wrapped.name == "write_file"
        assert wrapped.description == "Write a file"
        assert wrapped.parameters == original_tool.parameters

        # Execute the wrapped tool
        result = await wrapped.execute(path="/tmp/test.py")
        assert result == "file written"
        assert call_log == ["executed"]

        # Hook events should have been emitted
        assert "tool:pre" in hooks.event_names
        assert "tool:post" in hooks.event_names

        pre_data = hooks.get_data("tool:pre")[0]
        assert pre_data["tool_name"] == "write_file"
        assert pre_data["args"] == {"path": "/tmp/test.py"}

        post_data = hooks.get_data("tool:post")[0]
        assert post_data["tool_name"] == "write_file"
        assert post_data["result"] == "file written"
    finally:
        _current_node_context.reset(token)


@pytest.mark.asyncio
async def test_wrap_tool_with_no_execute_returns_none():
    """wrap_tool_with_hooks with execute=None preserves None."""
    hooks = RecordingHooks()
    original_tool = unified_llm.Tool(
        name="read_file",
        description="Read a file",
        parameters={},
        execute=None,
    )
    wrapped = wrap_tool_with_hooks(original_tool, hooks)
    assert wrapped.execute is None


# ---------------------------------------------------------------------------
# wrap_tool_with_hooks honors a tool:post hook modification
# (amplifier-support#485 / PR #318 -- same defect class as loop-agent's
# agent_session.py, but more severe here: pre-fix, wrap_tool_with_hooks
# never read post_result at all). Doubles are kernel-faithful
# (action="continue", data replaced) -- see RecordingHooks above and
# modules/loop-agent/tests/test_truncation_wiring.py's `_make_modifying_hooks`,
# whose pattern this copies.
# ---------------------------------------------------------------------------


async def _wrap_and_run(
    hooks: RecordingHooks, raw_output: Any, **exec_kwargs: Any
) -> Any:
    """Build a wrapped tool returning `raw_output` and execute it once."""
    original_tool = unified_llm.Tool(
        name="read_file",
        description="Read a file",
        parameters={},
        execute=None,
    )

    async def original_execute(**kwargs):
        return raw_output

    original_tool.execute = original_execute

    token = set_node_context({"node_id": "step1"})
    try:
        wrapped = wrap_tool_with_hooks(original_tool, hooks)
        return await wrapped.execute(**exec_kwargs)
    finally:
        _current_node_context.reset(token)


@pytest.mark.asyncio
async def test_wrap_tool_post_modification_reaches_output():
    """REGRESSION (amplifier-support#485): a tool:post hook's modified
    `result` must reach wrap_tool_with_hooks's returned output.

    This is the dead-branch proof for wrap_tool_with_hooks specifically:
    pre-fix, the wrapper never read `post_result` at all (unlike
    agent_session.py, it wasn't even gated on the wrong action -- it simply
    discarded the hook's return value), so the raw, untruncated output was
    always what the caller received. After the fix (reading
    `post_result.data` directly, guarded by isinstance/!=), a kernel-faithful
    modification reaches the output.
    """
    big_output = "x" * 100_000
    truncated = "truncated_version"
    hooks = RecordingHooks()
    hooks.set_tool_post_data(
        {"tool_name": "read_file", "node_id": "step1", "result": truncated}
    )

    result = await _wrap_and_run(hooks, big_output, path="/tmp/big.txt")

    assert result == truncated


@pytest.mark.asyncio
async def test_wrap_tool_post_missing_result_key_no_crash_raw_preserved():
    """post_result.data without a "result" key: no crash, raw output used.

    Modify REPLACES current_data rather than merging it, so a hook that
    returns a partial data dict can legitimately drop "result" entirely.
    """
    hooks = RecordingHooks()
    hooks.set_tool_post_data({"tool_name": "read_file"})  # no "result" key at all

    result = await _wrap_and_run(hooks, "raw output")

    assert result == "raw output"


@pytest.mark.asyncio
async def test_wrap_tool_post_non_string_result_ignored():
    """A non-string "result" in post_result.data must be ignored, not returned."""
    hooks = RecordingHooks()
    hooks.set_tool_post_data({"tool_name": "read_file", "result": 12345})

    result = await _wrap_and_run(hooks, "raw output")

    assert result == "raw output"


@pytest.mark.asyncio
async def test_wrap_tool_post_identical_result_no_spurious_log(caplog):
    """A hook that echoes back an identical "result" must not log a modification."""
    hooks = RecordingHooks()
    hooks.set_tool_post_data({"tool_name": "read_file", "result": "unchanged output"})

    with caplog.at_level(
        logging.DEBUG, logger="amplifier_module_loop_pipeline.hook_bridge"
    ):
        result = await _wrap_and_run(hooks, "unchanged output")

    assert result == "unchanged output"
    assert not any("modified output" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Task 16: Wire ContextVar into AmplifierBackend
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.backend import AmplifierBackend
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


class _TrackingClient:
    """Client that tracks whether middleware was active."""

    def __init__(self, response: unified_llm.Response, middleware: list | None = None):
        self._response = response
        self._middleware = middleware or []
        self.call_count = 0
        self.providers: dict = {}
        self.default_provider: str | None = None

    async def complete(self, request):
        self.call_count += 1
        return self._response


def _make_node_helper(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "implement",
        "prompt": "Build it",
        "attrs": {"llm_model": "test-model", "llm_provider": "test"},
    }
    defaults.update(kwargs)
    return Node(**defaults)


@pytest.mark.asyncio
async def test_amplifier_backend_sets_node_context():
    """AmplifierBackend sets _current_node_context before generate() call."""
    hooks = RecordingHooks()

    # Use a middleware-aware approach: check that provider:request
    # events include the correct node_id from context
    mock_client = _TrackingClient(_make_response("done"))

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node_helper(id="my-node")
    await backend.run(node, "Build it", PipelineContext())

    # The provider:request event should carry the correct node_id
    assert "provider:request" in hooks.event_names
    data = hooks.get_data("provider:request")[0]
    assert data["node_id"] == "my-node"


# ---------------------------------------------------------------------------
# Task 17: Wire ContextVar into DirectProviderBackend
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline import DirectProviderBackend


@pytest.mark.asyncio
async def test_direct_backend_lazy_client_gets_middleware():
    """When DirectProviderBackend has hooks, events are emitted."""
    hooks = RecordingHooks()
    mock_client = _TrackingClient(_make_response("done"))

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node_helper(id="step1")
    await backend.run(node, "work", PipelineContext())

    # Should have emitted events (Phase 1 manual emit)
    assert "provider:request" in hooks.event_names
    assert "provider:response" in hooks.event_names


@pytest.mark.asyncio
async def test_direct_backend_sets_node_context():
    """DirectProviderBackend sets ContextVar before generate()."""
    hooks = RecordingHooks()
    mock_client = _TrackingClient(_make_response("done"))

    backend = DirectProviderBackend(
        provider=object(),
        unified_client=mock_client,
        hooks=hooks,
    )
    node = _make_node_helper(id="ctx-test-node")
    await backend.run(node, "work", PipelineContext())

    # Verify the provider:request event has the correct node_id
    data = hooks.get_data("provider:request")[0]
    assert data["node_id"] == "ctx-test-node"


# ---------------------------------------------------------------------------
# Task 18: ContextVar setup + middleware client in AmplifierBackend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_amplifier_backend_middleware_client_emits_events():
    """AmplifierBackend with middleware client emits events via the middleware chain."""
    hooks = RecordingHooks()

    class _MockAdapter:
        name = "test"

        async def complete(self, request):
            return _make_response("result")

        def stream(self, request):
            raise NotImplementedError

    # Create a real unified_llm.Client with our hook bridge middleware
    base_client = unified_llm.Client(
        providers={"test": _MockAdapter()},
        default_provider="test",
    )
    mw_client = create_middleware_client(base_client, hooks=hooks)

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=mw_client,
        hooks=hooks,
    )
    node = _make_node_helper(id="build-step")
    await backend.run(node, "Build it", PipelineContext())

    # Events should be emitted (from manual Phase 1 calls AND/OR middleware)
    assert "provider:request" in hooks.event_names
    assert "provider:response" in hooks.event_names


# ---------------------------------------------------------------------------
# Task 20: End-to-end integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_middleware_with_real_client():
    """Full integration: middleware-equipped Client emits events through Amplifier hooks."""
    hooks = RecordingHooks()

    class _MockAdapter:
        name = "test"

        async def complete(self, request):
            return _make_response(
                '{"status": "success", "notes": "Implementation complete"}'
            )

        def stream(self, request):
            raise NotImplementedError

    # Build a middleware-equipped client
    middleware_fn = create_hook_bridge(hooks=hooks)
    client = unified_llm.Client(
        providers={"test": _MockAdapter()},
        default_provider="test",
        middleware=[middleware_fn],
    )

    # Use the client in a backend
    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=client,
        hooks=hooks,
    )
    node = _make_node_helper(id="e2e-test")
    result = await backend.run(node, "Build it", PipelineContext())

    assert result.status == StageStatus.SUCCESS

    # Verify events were emitted by BOTH layers:
    # - Manual orchestrator-level emits (Phase 1, from backend._emit)
    # - Middleware-level emits (Phase 2, from hook_bridge_middleware)
    request_events = hooks.get_data("provider:request")
    response_events = hooks.get_data("provider:response")
    assert len(request_events) >= 1
    assert len(response_events) >= 1

    # At least one event should have usage data
    has_usage = any("usage" in d and d["usage"] for d in response_events)
    assert has_usage


@pytest.mark.asyncio
async def test_end_to_end_deny_prevents_llm_call():
    """Full integration: deny from hooks prevents the LLM call entirely."""
    hooks = RecordingHooks()
    hooks.set_deny("budget exceeded")

    class _MockAdapter:
        name = "test"
        call_count = 0

        async def complete(self, request):
            self.call_count += 1
            return _make_response("should not reach")

        def stream(self, request):
            raise NotImplementedError

    adapter = _MockAdapter()
    client = unified_llm.Client(
        providers={"test": adapter},
        default_provider="test",
    )

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        unified_client=client,
        hooks=hooks,
    )
    node = _make_node_helper()
    result = await backend.run(node, "Build it", PipelineContext())

    # LLM call should have been blocked
    assert result.status == StageStatus.FAIL
    assert "budget exceeded" in (result.failure_reason or "")
    # The adapter should never have been called
    assert adapter.call_count == 0
