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
        # Provide session and config like a real coordinator.
        # Default agent config satisfies the recursion guard: any pipeline-node
        # agent must have session.orchestrator so the spawner doesn't inherit
        # loop-pipeline and recurse.  Tests that explicitly pass agents= override this.
        self.session = _MockSession()
        _default_agents: dict[str, Any] = {
            "attractor-anthropic": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            },
            "attractor-openai": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            },
        }
        self.config: dict[str, Any] = {
            "agents": agents if agents is not None else _default_agents
        }

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
    """Coordinator whose spawn raises an exception.

    Provides a minimal 'attractor-anthropic' agent with a non-pipeline
    session.orchestrator so the identity recursion guard does not fire before
    spawn is reached (the test exercises spawn-failure behavior, not guard behavior).
    """

    session = _MockSession()
    config: dict[str, Any] = {
        "agents": {
            "attractor-anthropic": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            },
        }
    }

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
    # Include the resolved profile name ('attractor-anthropic') with a valid
    # session.orchestrator so the identity recursion guard does not fire.
    agents = {
        "attractor-anthropic": {
            "description": "Test agent",
            "session": {"orchestrator": {"module": "loop-agent"}},
        }
    }
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


# ---------------------------------------------------------------------------
# Recursion guard tests (identity-based)
#
# The guard lives in _run_with_spawn and checks the EFFECTIVE orchestrator
# module of the child agent, not the presence of any bundle reference.
# It fires when session.orchestrator.module is absent (None) or is
# "loop-pipeline"; it passes when the module is any other string.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recursion_guard_raises_on_missing_session_orchestrator():
    """Guard raises ValueError when child agent has no session.orchestrator.

    A child with no session.orchestrator inherits the parent's loop-pipeline
    orchestrator and re-executes the same DOT graph — infinite recursion.
    """
    # Agent config with no session key at all: effective module is None.
    agents = {
        "attractor-anthropic": {"description": "Agent missing session.orchestrator"},
    }
    coordinator = MockCoordinator(
        spawn_result={"output": "ok", "session_id": "c-1"},
        agents=agents,
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    with pytest.raises(ValueError, match="recursion guard"):
        await backend.run(_make_node(attrs={}), "task", _make_context())


@pytest.mark.asyncio
async def test_recursion_guard_raises_on_loop_pipeline_module():
    """Guard raises ValueError when child agent explicitly sets loop-pipeline.

    A child whose session.orchestrator.module is 'loop-pipeline' would
    re-execute the same DOT graph — infinite recursion.
    """
    agents = {
        "attractor-anthropic": {
            "description": "Explicit self-nest",
            "session": {"orchestrator": {"module": "loop-pipeline"}},
        },
    }
    coordinator = MockCoordinator(
        spawn_result={"output": "ok", "session_id": "c-1"},
        agents=agents,
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    with pytest.raises(ValueError, match="recursion guard"):
        await backend.run(_make_node(attrs={}), "task", _make_context())


@pytest.mark.asyncio
async def test_recursion_guard_passes_on_non_pipeline_module():
    """Guard does NOT raise when child has an explicit non-pipeline orchestrator.

    A child with session.orchestrator.module='loop-agent' (or any module that
    is not 'loop-pipeline') is safe to spawn.
    """
    agents = {
        "attractor-anthropic": {
            "description": "Safe child agent",
            "session": {"orchestrator": {"module": "loop-agent"}},
        },
    }
    coordinator = MockCoordinator(
        spawn_result={"output": "ok", "session_id": "c-1"},
        agents=agents,
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    # Must not raise; spawn must be called normally.
    result = await backend.run(_make_node(attrs={}), "task", _make_context())
    assert coordinator.spawn_called
    assert isinstance(result, Outcome)


@pytest.mark.asyncio
async def test_backend_uses_parent_messages_not_sub_session_id():
    """Full-fidelity continuity uses parent_messages, never sub_session_id.

    The former _session_pool re-passed session_id as sub_session_id — a type
    confusion (an id where a conversation belongs).  The fix (backend.py:398-406)
    carries the accumulated node-exchange history in _thread_transcripts and
    passes it as parent_messages to a FRESH spawn.  sub_session_id is NEVER set.

    After node1 executes on thread "t", its (instruction, output) exchange is
    appended to _thread_transcripts["t"].  When node2 runs on the same thread,
    _get_parent_messages_for_thread returns two messages that seed the new spawn.
    """
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

    # sub_session_id must NEVER appear — the old re-pass mechanism is gone
    assert "sub_session_id" not in coordinator.last_spawn_kwargs
    assert "session_id" not in coordinator.last_spawn_kwargs

    # Instead, node1's exchange is carried as parent_messages into node2's spawn
    assert "parent_messages" in coordinator.last_spawn_kwargs
    messages = coordinator.last_spawn_kwargs["parent_messages"]
    # First turn: node1 received instruction "First", output was "ok"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "First"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "ok"


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


# ---------------------------------------------------------------------------
# Bug 2: spawn path must honor the child's outcome, not gate on final text
#
# A child that completes its work via tool calls + report_outcome (or whose
# orchestrator:complete status is success) but emits NO closing prose returns
# empty `output`. The spawn path must NOT silently fall back in that case --
# it must honor the same outcome sources the direct tool loop already uses
# (report_outcome args + completion status). It should fall back / fail loud
# ONLY when there is genuinely no text AND no report_outcome AND no success
# status.
# ---------------------------------------------------------------------------


def _install_fallback_spy(backend: AmplifierBackend) -> dict[str, Any]:
    """Replace _run_with_tool_loop with a spy that records if it was called.

    Returns a mutable dict whose ``called`` flag flips True if the spawn path
    falls back to the direct tool loop.
    """
    state: dict[str, Any] = {"called": False}

    async def _spy(*_args: Any, **_kwargs: Any) -> Outcome:
        state["called"] = True
        return Outcome(status=StageStatus.SUCCESS, notes="fallback ran")

    backend._run_with_tool_loop = _spy  # type: ignore[method-assign]
    return state


@pytest.mark.asyncio
async def test_spawn_empty_output_with_report_outcome_does_not_fall_back():
    """Empty final text + a report_outcome in the spawn result => honor it.

    The child did its work and reported an outcome via the report_outcome
    tool; its final assistant message was just empty. The spawn path must
    use that outcome and MUST NOT fall back to the direct tool loop.
    """
    coordinator = MockCoordinator(
        spawn_result={
            "output": "",  # no closing prose
            "session_id": "c-1",
            "status": "success",
            "metadata": {
                "report_outcome": {
                    "status": "success",
                    "notes": "Integrated source 2 into Foo and Bar pages.",
                }
            },
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),  # truthy => fallback is POSSIBLE if code chooses it
    )
    spy = _install_fallback_spy(backend)

    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert spy["called"] is False, (
        "spawn path fell back to the direct tool loop even though the child "
        "reported a valid outcome -- the report_outcome was discarded."
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS
    assert "Integrated source 2" in (result.notes or "")


@pytest.mark.asyncio
async def test_spawn_empty_output_with_success_status_does_not_fall_back():
    """Empty final text but status=success => treat as a successful completion.

    A child that finished cleanly (orchestrator:complete status=success) but
    ended on a tool call with no closing prose must be treated as SUCCESS,
    not silently re-routed through the fallback path.
    """
    coordinator = MockCoordinator(
        spawn_result={
            "output": "",
            "session_id": "c-1",
            "status": "success",
            "metadata": {},
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),
    )
    spy = _install_fallback_spy(backend)

    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert spy["called"] is False, (
        "spawn path fell back despite a success completion status."
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_spawn_truly_empty_fails_loud():
    """No text, no report_outcome, no success status => FAIL (fail-loud).

    The fallback has been removed: a genuinely-empty spawn result must now
    return Outcome(FAIL) regardless of whether a direct provider is available,
    so the engine can route via FAIL-edge → retry_target / goal_gate rather
    than silently re-running the node in a different in-process harness.
    """
    coordinator = MockCoordinator(
        spawn_result={
            "output": "",
            "session_id": "c-1",
            "status": "error",  # not a success status
            "metadata": {},
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),  # provider present — must NOT trigger fallback any more
    )
    spy = _install_fallback_spy(backend)

    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert spy["called"] is False, (
        "genuinely-empty spawn output must now fail loud (FAIL outcome), "
        "not silently fall back to the direct tool loop."
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_spawn_truly_empty_no_provider_still_fails():
    """No text, no outcome, no success status, no provider => FAIL (loud)."""
    coordinator = MockCoordinator(
        spawn_result={
            "output": "",
            "session_id": "c-1",
            "status": "error",
            "metadata": {},
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        # no provider => no fallback available
    )
    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL


# ---------------------------------------------------------------------------
# Fallback removal: spawn failures / empty output must FAIL loud (fail-loud
# spec compliance). The direct tool loop must NOT be silently substituted for
# a failed spawn — the engine needs to see the FAIL so it can route via
# FAIL-edge → retry_target / goal_gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_raises_with_provider_returns_fail_not_fallback():
    """Spawn raises + provider set => Outcome(FAIL), NOT a silent fallback.

    Previously the code re-ran the task via _run_with_tool_loop when the
    spawn raised and self._provider was truthy.  That hid the infrastructure
    failure from the engine's retry/goal machinery.  Now it must always fail
    loud, regardless of whether a direct provider is available.
    """
    coordinator = FailingCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),  # truthy — previously triggered the in-process retry
    )
    spy = _install_fallback_spy(backend)

    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert spy["called"] is False, (
        "spawn raised an exception but the code fell back to the direct tool loop "
        "instead of returning FAIL — the engine never saw the infrastructure failure."
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_spawn_truly_empty_with_provider_returns_fail_not_fallback():
    """Empty spawn + no recoverable outcome + provider set => FAIL, NOT fallback.

    Previously the code re-ran the task via _run_with_tool_loop when spawn
    returned empty output with no report_outcome / success status and
    self._provider was truthy.  That silently masked a real spawn
    misconfiguration.  Now it must always fail loud so the engine can route
    via FAIL-edge → retry_target / goal_gate.
    """
    coordinator = MockCoordinator(
        spawn_result={
            "output": "",
            "session_id": "c-1",
            "status": "error",  # not a success status
            "metadata": {},
        }
    )
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
        provider=object(),  # truthy — previously triggered the in-process retry
    )
    spy = _install_fallback_spy(backend)

    node = _make_node(attrs={"llm_provider": "anthropic"})
    result = await backend.run(node, "task", _make_context())

    assert spy["called"] is False, (
        "spawn returned empty output with no recoverable outcome but the code "
        "fell back to the direct tool loop instead of returning FAIL."
    )
    assert isinstance(result, Outcome)
    assert result.status == StageStatus.FAIL
    assert "Empty spawn output" in (result.failure_reason or ""), (
        f"Expected 'Empty spawn output' in failure_reason, got: {result.failure_reason!r}"
    )


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
    node = _make_node(
        attrs={
            "llm_provider": "test",
            "llm_model": "test-model",
            "reasoning_effort": "low",
        }
    )
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
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
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


# ---------------------------------------------------------------------------
# report_outcome tool integration with _run_with_tool_loop  (issue #238)
# ---------------------------------------------------------------------------


class _MockReportOutcomeTool:
    """Minimal stand-in for ReportOutcomeTool.

    The backend extracts outcome from result.steps[i].tool_calls (immutable,
    race-free) rather than from last_outcome on the tool object.  execute()
    only needs to return a truthy result so unified_llm.generate() can
    complete the tool loop — the call arguments are read from the step record.
    """

    last_outcome: dict | None = None
    name = "report_outcome"
    description = "Report structured outcome for pipeline routing."
    parameters = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "failure_reason": {"type": "string"},
            "context_updates": {"type": "object"},
        },
        "required": ["status"],
    }

    async def execute(self, input: dict) -> _MockToolResult:
        return _MockToolResult(output=f"recorded: {input.get('status', '?')}")


@pytest.mark.asyncio
async def test_tool_loop_report_outcome_terminal_action_empty_text():
    """generate() calls report_outcome as terminal tool; result.text empty → step args used.

    The backend extracts the outcome from result.steps[i].tool_calls (immutable after
    generate() returns) — not from a mutable last_outcome field on the tool object.
    This is race-free even when backend.clone() shares tool instances across parallel branches.
    """
    report_tool = _MockReportOutcomeTool()

    mock_client = _MockUnifiedClient(
        [
            # Round 1: model calls report_outcome as its terminal action
            _make_tool_call_response(
                [
                    {
                        "id": "tc-1",
                        "name": "report_outcome",
                        "args": {
                            "status": "fail",
                            "failure_reason": "quality gate failed",
                            "context_updates": {"quality_feedback": "fix X"},
                        },
                    }
                ]
            ),
            # Round 2: empty text — no follow-up turn (extended thinking)
            _make_text_response(""),
        ]
    )

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "evaluate quality", _make_context())

    assert result.status == StageStatus.FAIL
    assert result.failure_reason == "quality gate failed"
    assert result.context_updates == {"quality_feedback": "fix X"}


@pytest.mark.asyncio
async def test_tool_loop_report_outcome_no_cross_node_bleed():
    """Each generate() call has its own result.steps — node 2 cannot see node 1's tool call.

    Since outcome is read from result.steps (per-generate() immutable data) rather than
    from shared mutable tool state, there is no cross-node bleed even when the same tool
    object is registered in multiple backends.
    """
    report_tool = _MockReportOutcomeTool()

    coordinator = NoSpawnCoordinator()

    # Node 1: report_outcome called as terminal tool, result.text empty
    mock_client_1 = _MockUnifiedClient(
        [
            _make_tool_call_response(
                [
                    {
                        "id": "tc-1",
                        "name": "report_outcome",
                        "args": {
                            "status": "fail",
                            "failure_reason": "first node failed",
                        },
                    }
                ]
            ),
            _make_text_response(""),
        ]
    )
    backend1 = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=mock_client_1,
    )
    node1 = _make_node(
        id="node1", attrs={"llm_provider": "test", "llm_model": "test-model"}
    )
    result1 = await backend1.run(node1, "task 1", _make_context())

    assert result1.status == StageStatus.FAIL
    assert result1.failure_reason == "first node failed"

    # Node 2: no tool call, plain text response — must NOT see node 1's result
    mock_client_2 = _MockUnifiedClient([_make_text_response("plain text done")])
    backend2 = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=mock_client_2,
    )
    node2 = _make_node(
        id="node2", attrs={"llm_provider": "test", "llm_model": "test-model"}
    )
    result2 = await backend2.run(node2, "task 2", _make_context())

    # Plain text → SUCCESS (spec 4.5); must NOT inherit node1's FAIL
    assert result2.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_build_unified_tools_falls_back_to_input_schema():
    """_build_unified_tools resolves input_schema when parameters and schema are absent.

    ReportOutcomeTool exposes its schema via the input_schema property.
    Without this fallback it was registered with an empty schema, meaning
    the provider had no declared parameters to enforce.
    """
    from amplifier_module_loop_pipeline.backend import _build_unified_tools

    class _ToolWithInputSchema:
        name = "report_outcome"
        description = "Report outcome"

        # Deliberately omit "parameters" and "schema" — only input_schema
        @property
        def input_schema(self) -> dict:
            return {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            }

        async def execute(self, input):
            return _MockToolResult(output="ok")

    tools = _build_unified_tools({"report_outcome": _ToolWithInputSchema()})
    assert len(tools) == 1
    assert tools[0].name == "report_outcome"
    assert "properties" in tools[0].parameters
    assert "status" in tools[0].parameters["properties"]
    assert tools[0].parameters.get("required") == ["status"]


@pytest.mark.asyncio
async def test_tool_loop_report_outcome_json_text_wins_over_last_outcome():
    """JSON text response takes precedence over last_outcome; stale tool state is reset.

    A model without extended thinking may call report_outcome AND produce a JSON
    text response.  The text is authoritative; last_outcome from the tool call
    must be discarded (reset to None) and not returned as the outcome.
    """
    report_tool = _MockReportOutcomeTool()

    mock_client = _MockUnifiedClient(
        [
            # Round 1: model calls report_outcome (sets last_outcome via execute())
            _make_tool_call_response(
                [
                    {
                        "id": "tc-1",
                        "name": "report_outcome",
                        "args": {"status": "fail", "failure_reason": "from tool call"},
                    }
                ]
            ),
            # Round 2: model also produces a JSON text response — this must win
            _make_text_response(
                json.dumps({"status": "success", "notes": "text wins"})
            ),
        ]
    )

    coordinator = NoSpawnCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=mock_client,
    )
    node = _make_node(attrs={"llm_provider": "test", "llm_model": "test-model"})
    result = await backend.run(node, "evaluate", _make_context())

    # Text JSON wins — must not return the "fail" from the tool call args
    assert result.status == StageStatus.SUCCESS
    assert result.notes == "text wins"


def test_clone_isolates_stateful_tool_instances():
    """clone() gives each branch its own shallow copy with last_outcome reset.

    Covers two sub-cases:
    1. Object identity — clone holds a different tool instance than the original.
    2. Stale-state isolation — even if last_outcome was set by a prior run before
       clone() is called, the cloned branch starts with last_outcome=None and
       mutations on the clone do not propagate back to the original.
    """
    report_tool = _MockReportOutcomeTool()
    # Simulate the tool having been used before clone() is called
    report_tool.last_outcome = {"status": "fail", "failure_reason": "prior run"}

    backend = AmplifierBackend(
        coordinator=NoSpawnCoordinator(),
        profiles={},
        provider=object(),
        tools={"report_outcome": report_tool},
        unified_client=_MockUnifiedClient([]),
    )
    cloned = backend.clone()

    # 1. Different instances — not the same object
    assert cloned._tools["report_outcome"] is not backend._tools["report_outcome"]

    # 2. Clone starts with clean state regardless of prior use
    assert cloned._tools["report_outcome"].last_outcome is None

    # 3. Mutations on the clone do not affect the original
    cloned._tools["report_outcome"].last_outcome = {"status": "success"}
    assert backend._tools["report_outcome"].last_outcome == {
        "status": "fail",
        "failure_reason": "prior run",
    }


# ---------------------------------------------------------------------------
# Bug 1: AmplifierBackend.close() releases the cached unified client
#
# The per-article asyncio.run() lifecycle (engine_runner) means the cached
# AsyncAnthropic/httpx client created in _get_or_create_unified_client must be
# closed WITHIN its loop before the loop ends; otherwise GC later runs aclose()
# on a closed loop -> "RuntimeError: Event loop is closed". The spec mandates
# resource close on finalize (attractor-spec.md:333; unified-llm-spec.md:183).
# ---------------------------------------------------------------------------


class _ClosableClient:
    """Mock unified client exposing the spec-mandated async close()."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_backend_close_closes_unified_client():
    """AmplifierBackend.close() must call Client.close() on the cached client."""
    client = _ClosableClient()
    backend = AmplifierBackend(
        coordinator=MockCoordinator(),
        profiles={},
        unified_client=client,
    )
    await backend.close()
    assert client.close_calls == 1, (
        "AmplifierBackend.close() must close the cached unified client "
        "(spec finalize contract) -- the leaked AsyncAnthropic is the source "
        "of the 'Event loop is closed' RuntimeError at corpus scale."
    )


@pytest.mark.asyncio
async def test_backend_close_is_noop_without_client():
    """close() must be safe when no unified client was ever created."""
    backend = AmplifierBackend(coordinator=MockCoordinator(), profiles={})
    # Must not raise even though _unified_client is None.
    await backend.close()


@pytest.mark.asyncio
async def test_orchestrator_execute_closes_backend(tmp_path):
    """The orchestrator's finalize path must close the backend it ran with.

    This is the wiring assertion: after PipelineOrchestrator.execute() runs a
    pipeline, the backend's close() must have been awaited (within the same
    event loop), satisfying the spec's finalize contract and preventing the
    client leak.
    """
    from amplifier_module_loop_pipeline import PipelineOrchestrator

    class _SpyBackend:
        def __init__(self) -> None:
            self.closed = 0

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return f"Completed: {node.id}"

        async def close(self) -> None:
            self.closed += 1

    spy = _SpyBackend()
    orch = PipelineOrchestrator(
        {
            "dot_source": (
                "digraph { "
                "start [shape=Mdiamond]; "
                'impl [label="Impl", prompt="do it"]; '
                "exit [shape=Msquare]; "
                "start -> impl -> exit }"
            ),
            "logs_root": str(tmp_path),
        }
    )
    await orch.execute(
        prompt="goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=spy,
    )
    assert spy.closed == 1, (
        "PipelineOrchestrator.execute() must close the backend in its finalize "
        "path so the unified client is released within the event loop."
    )
