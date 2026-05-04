"""Tests for the AmplifierBackend (CodergenBackend adapter).

This adapter spawns coding agent sub-sessions via the Amplifier
session.spawn capability. Tests mock the spawn function since it's
an app-layer capability.

Also tests the Path B fallback: a direct provider mini tool loop
when session.spawn is not available.

Spec coverage: Section 4.5 (CodergenBackend Interface), Section 1.4.
"""

import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub so the backend's lazy imports work
# in the test environment where amplifier_core may not be installed.
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

# Provide a minimal amplifier_foundation stub so the backend's ProviderPreference
# import works in the test environment where amplifier_foundation is not installed.
if "amplifier_foundation" not in sys.modules:
    from dataclasses import dataclass as _dc

    @_dc
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockUnifiedClient:
    """Mock unified_llm.Client for testing."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0
        self.requests = []

    async def complete(self, request):
        self.call_count += 1
        self.requests.append(request)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            if isinstance(resp, Exception):
                raise resp
            return resp
        return _make_text_response("fallback")


def _make_text_response(text):
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def _make_tool_call_response(calls):
    """calls = [{"id": "tc-1", "name": "write_file", "args": {"path": "a.py"}}]"""
    content = []
    for c in calls:
        content.append(
            unified_llm.ContentPart(
                kind=unified_llm.ContentKind.TOOL_CALL,
                tool_call=unified_llm.ToolCallData(
                    id=c["id"],
                    name=c["name"],
                    arguments=c.get("args", {}),
                ),
            )
        )
    return unified_llm.Response(
        id="resp-tool",
        model="test-model",
        provider="test",
        message=unified_llm.Message(role=unified_llm.Role.ASSISTANT, content=content),
        finish_reason=unified_llm.FinishReason(reason="tool_calls"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class _MockSession:
    """Minimal stand-in for AmplifierSession."""

    config: dict[str, Any] = {}


class MockCoordinator:
    """Mock coordinator that tracks spawn calls."""

    def __init__(
        self,
        spawn_result: dict | None = None,
        agents: dict[str, Any] | None = None,
    ):
        self._spawn_result = spawn_result or {"output": "done", "session_id": "child-1"}
        self.spawn_called = False
        self.spawn_call_count = 0
        self.last_spawn_kwargs: dict = {}
        self._capabilities: dict = {}
        # Provide session and config like a real coordinator
        self.session = _MockSession()
        self.config: dict[str, Any] = {"agents": agents or {}}

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return self._capabilities.get(name)

    async def _spawn_fn(self, **kwargs):
        self.spawn_called = True
        self.spawn_call_count += 1
        self.last_spawn_kwargs = kwargs
        return self._spawn_result


class FailingCoordinator:
    """Coordinator whose spawn raises an exception."""

    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        raise RuntimeError("Spawn failed: connection refused")


class NoSpawnCoordinator:
    """Coordinator that does not have session.spawn capability."""

    session = _MockSession()
    config: dict[str, Any] = {"agents": {}}

    def get_capability(self, name: str):
        return None


@dataclass
class _MockToolResult:
    """Minimal ToolResult replacement."""

    output: str = "tool output"
    success: bool = True


@dataclass
class _MockTextBlock:
    text: str
    type: str = "text"


@dataclass
class _MockToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _MockChatResponse:
    content: list[Any] = field(default_factory=list)
    tool_calls: list[Any] | None = None


class _MockTool:
    def __init__(self, name: str, result: str = "tool done"):
        self._name = name
        self._result = result
        self.call_count = 0
        self.last_input: dict[str, Any] = {}
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.description = f"Mock tool {name}"

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, input: dict[str, Any]) -> _MockToolResult:
        self.call_count += 1
        self.last_input = input
        return _MockToolResult(output=self._result)


class _MockProvider:
    """Mock provider that returns canned responses."""

    name = "mock"

    def __init__(self, responses: list[_MockChatResponse] | None = None):
        self._responses = (
            list(responses)
            if responses
            else [_MockChatResponse(content=[_MockTextBlock(text="done")])]
        )
        self._call_idx = 0

    async def complete(self, request: Any) -> _MockChatResponse:
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        return _MockChatResponse(content=[_MockTextBlock(text="done")])

    def parse_tool_calls(self, response: Any) -> list[Any]:
        return list(response.tool_calls) if response.tool_calls else []


def _make_node(**kwargs) -> Node:
    defaults = {"id": "implement", "prompt": "Build it"}
    defaults.update(kwargs)
    return Node(**defaults)


def _make_context() -> PipelineContext:
    return PipelineContext()


# ---------------------------------------------------------------------------
# Core spawn tests (Path A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_spawns_session():
    """Backend uses coordinator session.spawn to create child session."""
    coordinator = MockCoordinator(
        spawn_result={
            "output": json.dumps({"status": "success", "notes": "done"}),
            "session_id": "child-1",
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "Build the feature", _make_context())
    assert coordinator.spawn_called
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_backend_selects_profile_by_provider():
    """Different providers select different profile bundles."""
    coordinator = MockCoordinator(spawn_result={"output": "ok", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={
            "anthropic": "attractor-anthropic",
            "openai": "attractor-openai",
        },
    )
    node_anthropic = _make_node(id="n1", attrs={"llm_provider": "anthropic"})
    node_openai = _make_node(id="n2", attrs={"llm_provider": "openai"})

    await backend.run(node_anthropic, "task", _make_context())
    first_profile = coordinator.last_spawn_kwargs.get("agent_name")

    await backend.run(node_openai, "task", _make_context())
    second_profile = coordinator.last_spawn_kwargs.get("agent_name")

    assert first_profile == "attractor-anthropic"
    assert second_profile == "attractor-openai"


@pytest.mark.asyncio
async def test_backend_default_provider_is_anthropic():
    """If node has no llm_provider, defaults to anthropic."""
    coordinator = MockCoordinator(spawn_result={"output": "ok", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={})  # No llm_provider
    await backend.run(node, "task", _make_context())
    assert coordinator.last_spawn_kwargs.get("agent_name") == "attractor-anthropic"


# --- Spawn signature tests (parent_session / agent_configs / sub_session_id) ---


@pytest.mark.asyncio
async def test_backend_passes_parent_session():
    """Spawn kwargs include parent_session from coordinator.session."""
    coordinator = MockCoordinator(spawn_result={"output": "ok", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    await backend.run(_make_node(attrs={}), "task", _make_context())
    assert "parent_session" in coordinator.last_spawn_kwargs
    assert coordinator.last_spawn_kwargs["parent_session"] is coordinator.session


@pytest.mark.asyncio
async def test_backend_passes_agent_configs():
    """Spawn kwargs include agent_configs from coordinator.config."""
    agents = {"my-agent": {"description": "Test agent"}}
    coordinator = MockCoordinator(
        spawn_result={"output": "ok", "session_id": "c-1"},
        agents=agents,
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    await backend.run(_make_node(attrs={}), "task", _make_context())
    assert coordinator.last_spawn_kwargs.get("agent_configs") == agents


@pytest.mark.asyncio
async def test_backend_uses_sub_session_id_not_session_id():
    """Session reuse passes 'sub_session_id', not 'session_id'."""
    coordinator = MockCoordinator(
        spawn_result={"output": "ok", "session_id": "sess-abc"},
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    from amplifier_module_loop_pipeline.graph import Edge, Graph

    node1 = _make_node(
        id="step1",
        attrs={"llm_provider": "anthropic", "fidelity": "full", "thread_id": "t"},
    )
    node2 = _make_node(
        id="step2",
        attrs={"llm_provider": "anthropic", "fidelity": "full", "thread_id": "t"},
    )
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "step1": node1,
            "step2": node2,
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="step1"),
            Edge(from_node="step1", to_node="step2"),
            Edge(from_node="step2", to_node="exit"),
        ],
    )
    edge = Edge(from_node="start", to_node="step1")

    await backend.run(node1, "First", _make_context(), incoming_edge=edge, graph=graph)
    await backend.run(node2, "Second", _make_context(), incoming_edge=edge, graph=graph)

    # Must use sub_session_id (not session_id) for the CLI spawn capability
    assert "sub_session_id" in coordinator.last_spawn_kwargs
    assert "session_id" not in coordinator.last_spawn_kwargs
    assert coordinator.last_spawn_kwargs["sub_session_id"] == "sess-abc"


# --- Outcome parsing tests ---


@pytest.mark.asyncio
async def test_backend_parses_json_outcome():
    """If child returns JSON with status field, parse it as Outcome."""
    json_output = json.dumps({"status": "fail", "failure_reason": "3 tests failing"})
    coordinator = MockCoordinator(
        spawn_result={"output": json_output, "session_id": "c-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "3 tests failing"


@pytest.mark.asyncio
async def test_backend_plain_text_returns_success():
    """Per spec Section 4.5: plain text (non-JSON) child output returns SUCCESS."""
    coordinator = MockCoordinator(
        spawn_result={"output": "Implementation complete", "session_id": "c-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS
    assert "Plain text response" in (result.notes or "")


@pytest.mark.asyncio
async def test_backend_parses_partial_success():
    """JSON outcome with partial_success status is parsed correctly."""
    json_output = json.dumps({"status": "partial_success", "notes": "some tests pass"})
    coordinator = MockCoordinator(
        spawn_result={"output": json_output, "session_id": "c-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    result = await backend.run(
        _make_node(attrs={"llm_provider": "anthropic"}), "task", _make_context()
    )
    assert result.status == StageStatus.PARTIAL_SUCCESS


# --- Error handling tests ---


@pytest.mark.asyncio
async def test_backend_handles_spawn_failure():
    """Spawn failure returns Outcome(status=FAIL) instead of raising."""
    coordinator = FailingCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL
    assert "connection refused" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_backend_handles_no_spawn_no_provider():
    """No session.spawn and no provider returns FAIL."""
    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL
    assert "available" in (result.failure_reason or "").lower()


# --- Config forwarding tests ---


@pytest.mark.asyncio
async def test_backend_forwards_reasoning_effort():
    """reasoning_effort from node attrs is forwarded to spawn call."""
    coordinator = MockCoordinator(spawn_result={"output": "ok", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(attrs={"llm_provider": "anthropic", "reasoning_effort": "low"})
    await backend.run(node, "task", _make_context())
    orch_config = coordinator.last_spawn_kwargs.get("orchestrator_config", {})
    assert orch_config.get("reasoning_effort") == "low"


@pytest.mark.asyncio
async def test_backend_forwards_model():
    """llm_model from node attrs is forwarded to spawn call."""
    coordinator = MockCoordinator(spawn_result={"output": "ok", "session_id": "c-1"})
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = _make_node(
        attrs={"llm_provider": "anthropic", "llm_model": "claude-sonnet-4-5"}
    )
    await backend.run(node, "task", _make_context())
    prefs = coordinator.last_spawn_kwargs.get("provider_preferences")
    assert prefs is not None
    assert any(getattr(p, "model", None) == "claude-sonnet-4-5" for p in prefs)


# ---------------------------------------------------------------------------
# Path B: Direct provider mini tool loop fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_falls_back_to_tool_loop():
    """When spawn is unavailable but provider is given, uses direct tool loop."""
    coordinator = NoSpawnCoordinator()
    mock_client = _MockUnifiedClient(
        [_make_text_response(json.dumps({"status": "success", "notes": "done"}))]
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),  # truthy sentinel — no longer called
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS
    assert mock_client.call_count >= 1


@pytest.mark.asyncio
async def test_tool_loop_executes_tools_then_returns():
    """Tool loop calls tools and feeds results back until model stops."""
    tool = _MockTool("write_file", result="file written")
    mock_client = _MockUnifiedClient(
        [
            # Round 1: model requests a tool call
            _make_tool_call_response(
                [{"id": "tc-1", "name": "write_file", "args": {"path": "a.py"}}]
            ),
            # Round 2: model returns JSON outcome (done)
            _make_text_response(json.dumps({"status": "success", "notes": "All done"})),
        ]
    )
    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel
        tools={"write_file": tool},
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "Write a file", _make_context())

    assert tool.call_count == 1
    assert tool.last_input == {"path": "a.py"}
    assert result.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_tool_loop_handles_unknown_tool():
    """Tool loop gracefully handles calls to unknown tools."""
    mock_client = _MockUnifiedClient(
        [
            _make_tool_call_response(
                [{"id": "tc-1", "name": "nonexistent", "args": {}}]
            ),
            _make_text_response("ok, no tool"),
        ]
    )
    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel
        tools={},
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())
    assert result.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_tool_loop_handles_provider_failure():
    """Tool loop returns FAIL when unified_llm client raises."""
    mock_client = _MockUnifiedClient([unified_llm.SDKError("API unreachable")])
    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "task", _make_context())
    assert result.status == StageStatus.FAIL
    assert "unreachable" in (result.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# Path B: reasoning_effort passthrough via unified_llm.generate()
# ---------------------------------------------------------------------------


def _make_generate_result(text: str = "done") -> "unified_llm.GenerateResult":
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


@pytest.mark.asyncio
async def test_reasoning_effort_passed_to_tool_loop(monkeypatch):
    """reasoning_effort='low' in node attrs is forwarded to unified_llm.generate()."""
    captured_kwargs: dict[str, Any] = {}

    async def _fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result(json.dumps({"status": "success"}))

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel to enable Path B
    )
    node = _make_node(attrs={"llm_provider": "test", "reasoning_effort": "low"})
    result = await backend.run(node, "task", _make_context())

    assert result.status == StageStatus.SUCCESS
    assert captured_kwargs.get("reasoning_effort") == "low"


@pytest.mark.asyncio
async def test_reasoning_effort_defaults_to_none(monkeypatch):
    """Without reasoning_effort in node attrs, None is passed to unified_llm.generate()."""
    captured_kwargs: dict[str, Any] = {}

    async def _fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_generate_result(json.dumps({"status": "success"}))

    monkeypatch.setattr(unified_llm, "generate", _fake_generate)

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),  # truthy sentinel to enable Path B
    )
    node = _make_node(attrs={"llm_provider": "test"})
    result = await backend.run(node, "task", _make_context())

    assert result.status == StageStatus.SUCCESS
    assert captured_kwargs.get("reasoning_effort") is None


# ---------------------------------------------------------------------------
# Task 4: _parse_outcome returns SUCCESS for plain string responses (spec 4.5)
# ---------------------------------------------------------------------------


def test_parse_outcome_plain_text_returns_success():
    """Spec 4.5: `_parse_outcome` should return `Outcome(status=SUCCESS)` for plain (non-JSON) string input."""
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    result = _parse_outcome("I finished the task successfully")
    assert result.status == StageStatus.SUCCESS
    assert result.notes is not None


def test_parse_outcome_valid_json_still_works():
    """_parse_outcome returns SUCCESS when valid JSON with status key is given."""
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    result = _parse_outcome('{"status": "success", "notes": "done"}')
    assert result.status == StageStatus.SUCCESS
    assert result.notes == "done"


def test_parse_outcome_empty_string_returns_fail():
    """_parse_outcome returns FAIL with No output from LLM for empty string."""
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    result = _parse_outcome("")
    assert result.status == StageStatus.FAIL
    assert result.notes == "No output from LLM"
    assert result.failure_reason == "Empty LLM response"


def test_parse_outcome_json_fenced_with_json_tag():
    """_parse_outcome extracts context_updates from ```json-fenced JSON.

    Issue 17: LLMs emit ```json...``` fences despite explicit "no fences"
    instructions.  The fence-stripping fallback must recover context_updates
    (specifically gate_feedback) so the next ask turn shows eval's feedback.
    """
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    payload = (
        "```json\n"
        '{"status": "success", "preferred_label": "need_more",'
        ' "context_updates": {"gate_feedback": "Your response lacks specifics."}}\n'
        "```"
    )
    result = _parse_outcome(payload)
    assert result.status == StageStatus.SUCCESS
    assert result.preferred_label == "need_more"
    assert result.context_updates is not None
    assert result.context_updates["gate_feedback"] == "Your response lacks specifics."


def test_parse_outcome_json_fenced_without_json_tag():
    """_parse_outcome extracts context_updates from plain-fenced JSON (no 'json' tag)."""
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    payload = (
        "```\n"
        '{"status": "success", "preferred_label": "scored",'
        ' "context_updates": {"gate_feedback": ""}}\n'
        "```"
    )
    result = _parse_outcome(payload)
    assert result.status == StageStatus.SUCCESS
    assert result.preferred_label == "scored"
    assert result.context_updates is not None
    assert result.context_updates["gate_feedback"] == ""


# ---------------------------------------------------------------------------
# Task 5: ProviderPreference import — lazy placeholder when foundation missing
# ---------------------------------------------------------------------------


def test_provider_preference_module_imports_when_foundation_missing(monkeypatch):
    """Module import must succeed even if amplifier_foundation is unavailable.

    Only *instantiation* of _ProviderPreference should raise ImportError,
    not the module-level import itself.
    """
    import importlib
    import sys

    # Remove the module from the cache so re-import triggers the except branch
    monkeypatch.delitem(sys.modules, "amplifier_foundation", raising=False)
    monkeypatch.delitem(
        sys.modules, "amplifier_module_loop_pipeline.backend", raising=False
    )

    # Block the import so the except branch fires
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
    )

    def _blocking_import(name, *args, **kwargs):
        if name == "amplifier_foundation":
            raise ImportError("mocked missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocking_import)

    # Import must succeed — no ImportError at module level
    backend_module = importlib.import_module("amplifier_module_loop_pipeline.backend")
    assert backend_module is not None


def test_provider_preference_placeholder_raises_on_instantiation(monkeypatch):
    """When amplifier_foundation is missing, instantiating _ProviderPreference raises ImportError."""
    import importlib
    import sys

    monkeypatch.delitem(sys.modules, "amplifier_foundation", raising=False)
    monkeypatch.delitem(
        sys.modules, "amplifier_module_loop_pipeline.backend", raising=False
    )

    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__
    )

    def _blocking_import(name, *args, **kwargs):
        if name == "amplifier_foundation":
            raise ImportError("mocked missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocking_import)

    # Import succeeds
    importlib.import_module("amplifier_module_loop_pipeline.backend")

    # Re-import to get the freshly loaded module's _ProviderPreference
    import amplifier_module_loop_pipeline.backend as backend_mod

    _PP = backend_mod._ProviderPreference  # type: ignore[attr-defined]

    # Instantiation must raise a helpful ImportError
    with pytest.raises(ImportError, match="amplifier.foundation is required"):
        _PP(provider="anthropic", model="test")


# ---------------------------------------------------------------------------
# Human gate text injection (consume-once)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_injects_human_gate_text_into_instruction():
    """When context has human.gate.text, backend prepends it to instruction and clears the key."""
    coordinator = MockCoordinator(
        spawn_result={"output": json.dumps({"status": "success"}), "session_id": "c-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    context = _make_context()
    context.set("human.gate.text", "I think we should focus on the API")
    context.set("human.gate.label", "Brainstorm with Human")

    node = _make_node(attrs={"llm_provider": "anthropic"})
    await backend.run(node, "Refine the understanding", context)

    # Verify the human's text was injected into the instruction
    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "I think we should focus on the API" in instruction
    assert "Brainstorm with Human" in instruction
    assert "Refine the understanding" in instruction

    # Verify consume-once: key should be cleared after injection
    assert context.get("human.gate.text") is None


@pytest.mark.asyncio
async def test_backend_no_injection_without_human_gate_text():
    """When context lacks human.gate.text, backend runs normally without injection."""
    coordinator = MockCoordinator(
        spawn_result={"output": json.dumps({"status": "success"}), "session_id": "c-1"}
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )

    context = _make_context()
    # No human.gate.text set — this is the normal path for all non-freeform flows

    node = _make_node(attrs={"llm_provider": "anthropic"})
    await backend.run(node, "Do the work", context)

    # Instruction should NOT contain injection prefix
    instruction = coordinator.last_spawn_kwargs.get("instruction", "")
    assert "Human response at gate" not in instruction

    # human.gate.text should still be None (never set, never cleared)
    assert context.get("human.gate.text") is None
