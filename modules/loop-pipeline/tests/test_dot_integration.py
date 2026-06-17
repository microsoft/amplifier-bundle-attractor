"""DOT-based integration tests for unified-llm-client + hook bridge.

Verifies the full stack end-to-end:
    DOT parsing -> transforms -> validation -> PipelineEngine
    -> DirectProviderBackend -> unified_llm.generate() -> mock client
    -> hook bridge -> hook events emitted

Uses mock adapters (no real API calls) for fast, reliable testing.

Test coverage:
- Simple pipeline: 1 LLM node, basic event emission
- Multi-step pipeline: 3 LLM nodes, per-node event verification
- Conditional retry: self-loop produces extra events on each attempt
- Model routing: stylesheet resolves different providers per node
- Parallel fan-out: events from all parallel branches
- Deny hook: hook deny prevents LLM call, node outcome is FAIL
- Existing spec fixtures: spec_simple_linear.dot, spec_stylesheet.dot
- Production semport.dot: 2-provider loop with conditional branching
- Production consensus_task.dot: 3-provider fan-out with retry loop
"""

import json
import os
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# amplifier_core stub (same pattern as test_hook_bridge.py)
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

from amplifier_module_loop_pipeline import DirectProviderBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_COMPLETE,
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_START,
    PIPELINE_START,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)
from amplifier_module_loop_pipeline.transforms import apply_transforms
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Fixture directory helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
INTEGRATION_DIR = os.path.join(FIXTURES_DIR, "integration")


def _load_fixture(name: str, subdir: str = "") -> str:
    """Load a DOT fixture file by name."""
    base = os.path.join(FIXTURES_DIR, subdir) if subdir else FIXTURES_DIR
    path = os.path.join(base, name)
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# RecordingHooks -- records all emitted events with configurable deny
# ---------------------------------------------------------------------------


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

    def count(self, event_name: str) -> int:
        return sum(1 for e, _ in self.events if e == event_name)


# ---------------------------------------------------------------------------
# MockUnifiedClient -- returns canned responses without real API calls
# ---------------------------------------------------------------------------


def _make_response(
    text: str = "Done",
    model: str = "test-model",
    provider: str = "anthropic",
) -> unified_llm.Response:
    """Create a unified_llm Response with text content."""
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model=model,
        provider=provider,
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


class MockUnifiedClient:
    """Mock unified_llm.Client that returns canned Response objects.

    unified_llm.generate() calls client.complete(request), so this mock
    only needs a complete() method.  Responses are served in order; the
    last response repeats if the caller makes more calls than responses.
    """

    def __init__(self, responses: list[unified_llm.Response] | None = None) -> None:
        self._responses = list(responses or [_make_response()])
        self._idx = 0
        self.call_count = 0
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> unified_llm.Response:
        self.call_count += 1
        self.requests.append(request)
        idx = min(self._idx, len(self._responses) - 1)
        self._idx += 1
        return self._responses[idx]


# ---------------------------------------------------------------------------
# Integration engine builder
# ---------------------------------------------------------------------------


def _make_integration_engine(
    dot_source: str,
    mock_client: MockUnifiedClient,
    hooks: RecordingHooks,
    logs_root: str,
) -> PipelineEngine:
    """Build a full integration engine from DOT source.

    Stack: DOT -> parse -> transform -> validate -> DirectProviderBackend
           -> HandlerRegistry -> PipelineEngine (all with shared hooks).
    """
    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)

    # Backend that routes through unified_llm.generate() -> mock client
    backend = DirectProviderBackend(
        provider=object(),  # truthy sentinel
        unified_client=mock_client,
        hooks=hooks,
    )

    # Engine passes itself to handlers via execute(engine=...) — no closure needed.
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=backend, hooks=hooks)),
        logs_root=logs_root,
        hooks=hooks,
    )

    return engine


# ===========================================================================
# Test 1: Simple pipeline -- unified_llm_simple.dot
# start -> implement -> done  (1 LLM node)
# ===========================================================================


class TestSimplePipeline:
    """Tests using unified_llm_simple.dot (start -> implement -> done)."""

    def _dot(self) -> str:
        return _load_fixture("unified_llm_simple.dot", "integration")

    @pytest.mark.asyncio
    async def test_simple_pipeline_completes_successfully(self, tmp_path):
        """Simple pipeline runs to completion with unified-llm-client."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Hello, world!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_simple_pipeline_emits_hook_events(self, tmp_path):
        """Simple pipeline: 1 LLM node = 1 provider:request + 1 provider:response."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Hello, world!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        assert hooks.count(PROVIDER_REQUEST) == 1
        assert hooks.count(PROVIDER_RESPONSE) == 1

    @pytest.mark.asyncio
    async def test_simple_pipeline_event_ordering(self, tmp_path):
        """provider:request always comes before provider:response."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Hello, world!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        names = hooks.event_names
        req_idx = names.index(PROVIDER_REQUEST)
        resp_idx = names.index(PROVIDER_RESPONSE)
        assert req_idx < resp_idx

    @pytest.mark.asyncio
    async def test_simple_pipeline_event_payloads(self, tmp_path):
        """Event payloads contain model, provider, node_id, usage data."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Hello, world!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        # Check provider:request payload
        req_data = hooks.get_data(PROVIDER_REQUEST)[0]
        assert req_data["node_id"] == "implement"
        assert "model" in req_data
        assert "provider" in req_data

        # Check provider:response payload
        resp_data = hooks.get_data(PROVIDER_RESPONSE)[0]
        assert resp_data["node_id"] == "implement"
        assert "usage" in resp_data
        assert resp_data["usage"]["input_tokens"] == 10
        assert resp_data["usage"]["output_tokens"] == 20
        assert resp_data["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_simple_pipeline_emits_pipeline_events(self, tmp_path):
        """Engine emits pipeline:start/complete alongside provider events."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Hello, world!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        assert PIPELINE_START in hooks.event_names
        assert PIPELINE_COMPLETE in hooks.event_names
        assert PIPELINE_NODE_START in hooks.event_names
        assert PIPELINE_NODE_COMPLETE in hooks.event_names


# ===========================================================================
# Test 2: Multi-step pipeline -- unified_llm_multi_step.dot
# start -> plan -> implement -> review -> done  (3 LLM nodes)
# ===========================================================================


class TestMultiStepPipeline:
    """Tests using unified_llm_multi_step.dot (plan -> implement -> review)."""

    def _dot(self) -> str:
        return _load_fixture("unified_llm_multi_step.dot", "integration")

    @pytest.mark.asyncio
    async def test_multi_step_completes_successfully(self, tmp_path):
        """Multi-step pipeline runs all 3 LLM nodes."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response(
                    '{"status": "success", "notes": "Plan: build calculator"}'
                ),
                _make_response(
                    '{"status": "success", "notes": "def add(a, b): return a + b"}'
                ),
                _make_response('{"status": "success", "notes": "Code looks good"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 3

    @pytest.mark.asyncio
    async def test_multi_step_emits_events_per_node(self, tmp_path):
        """3 LLM nodes = 3 provider:request + 3 provider:response events."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Plan"}'),
                _make_response('{"status": "success", "notes": "Implement"}'),
                _make_response('{"status": "success", "notes": "Review"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        assert hooks.count(PROVIDER_REQUEST) == 3
        assert hooks.count(PROVIDER_RESPONSE) == 3

    @pytest.mark.asyncio
    async def test_multi_step_events_have_correct_node_ids(self, tmp_path):
        """Each event payload has the correct node_id for its LLM node."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Plan"}'),
                _make_response('{"status": "success", "notes": "Implement"}'),
                _make_response('{"status": "success", "notes": "Review"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        req_node_ids = [d["node_id"] for d in hooks.get_data(PROVIDER_REQUEST)]
        assert req_node_ids == ["plan", "implement", "review"]

        resp_node_ids = [d["node_id"] for d in hooks.get_data(PROVIDER_RESPONSE)]
        assert resp_node_ids == ["plan", "implement", "review"]

    @pytest.mark.asyncio
    async def test_multi_step_event_ordering_per_node(self, tmp_path):
        """For each node, request comes before response."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Plan"}'),
                _make_response('{"status": "success", "notes": "Implement"}'),
                _make_response('{"status": "success", "notes": "Review"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        for node_id in ["plan", "implement", "review"]:
            req_indices = [
                i
                for i, (e, d) in enumerate(hooks.events)
                if e == PROVIDER_REQUEST and d.get("node_id") == node_id
            ]
            resp_indices = [
                i
                for i, (e, d) in enumerate(hooks.events)
                if e == PROVIDER_RESPONSE and d.get("node_id") == node_id
            ]
            assert len(req_indices) == 1, f"Expected 1 request for {node_id}"
            assert len(resp_indices) == 1, f"Expected 1 response for {node_id}"
            assert req_indices[0] < resp_indices[0], (
                f"Request should precede response for {node_id}"
            )


# ===========================================================================
# Test 3: Conditional retry -- unified_llm_conditional.dot
# start -> fix -> fix (self-loop on fail) -> done
# ===========================================================================


class TestConditionalRetry:
    """Tests using unified_llm_conditional.dot (fix self-loops on failure)."""

    def _dot(self) -> str:
        return _load_fixture("unified_llm_conditional.dot", "integration")

    @pytest.mark.asyncio
    async def test_conditional_completes_on_first_success(self, tmp_path):
        """When fix succeeds on first try, pipeline completes immediately."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "Fixed!"}')]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_conditional_retry_emits_events_on_each_attempt(self, tmp_path):
        """Retry loop: fail then succeed = 2 request + 2 response events."""
        hooks = RecordingHooks()
        fail_json = json.dumps({"status": "fail", "failure_reason": "tests failing"})
        client = MockUnifiedClient(
            [
                _make_response(fail_json),
                _make_response('{"status": "success", "notes": "Fixed!"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        assert client.call_count == 2
        assert hooks.count(PROVIDER_REQUEST) == 2
        assert hooks.count(PROVIDER_RESPONSE) == 2

    @pytest.mark.asyncio
    async def test_conditional_retry_events_all_for_fix_node(self, tmp_path):
        """All retry events reference the 'fix' node."""
        hooks = RecordingHooks()
        fail_json = json.dumps({"status": "fail"})
        client = MockUnifiedClient(
            [
                _make_response(fail_json),
                _make_response('{"status": "success", "notes": "Fixed!"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        req_node_ids = [d["node_id"] for d in hooks.get_data(PROVIDER_REQUEST)]
        assert all(nid == "fix" for nid in req_node_ids)


# ===========================================================================
# Test 4: Model routing -- unified_llm_model_routing.dot
# Stylesheet assigns different providers to different nodes.
# ===========================================================================


class TestModelRouting:
    """Tests using unified_llm_model_routing.dot (stylesheet with providers)."""

    def _dot(self) -> str:
        return _load_fixture("unified_llm_model_routing.dot", "integration")

    @pytest.mark.asyncio
    async def test_model_routing_completes(self, tmp_path):
        """Pipeline with model stylesheet completes successfully."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Quick analysis"}'),
                _make_response('{"status": "success", "notes": "Deep review"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_model_routing_events_include_correct_provider(self, tmp_path):
        """Events reflect the provider resolved from the stylesheet."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Quick analysis"}'),
                _make_response('{"status": "success", "notes": "Deep review"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        req_events = hooks.get_data(PROVIDER_REQUEST)
        assert len(req_events) == 2

        # analyze node has class="fast" -> openai from stylesheet
        assert req_events[0]["provider"] == "openai"
        assert req_events[0]["node_id"] == "analyze"

        # deep_review uses default * selector -> anthropic from stylesheet
        assert req_events[1]["provider"] == "anthropic"
        assert req_events[1]["node_id"] == "deep_review"

    @pytest.mark.asyncio
    async def test_model_routing_different_providers_in_events(self, tmp_path):
        """The two nodes produce events with different provider values."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "A"}'),
                _make_response('{"status": "success", "notes": "B"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        providers = [d["provider"] for d in hooks.get_data(PROVIDER_REQUEST)]
        assert "openai" in providers
        assert "anthropic" in providers


# ===========================================================================
# Test 5: Parallel fan-out -- unified_llm_parallel.dot
# start -> parallel -> {bugs, style_check, security} -> collect -> summarize -> done
# ===========================================================================


class TestParallelPipeline:
    """Tests using unified_llm_parallel.dot (3 parallel branches + summarize)."""

    def _dot(self) -> str:
        return _load_fixture("unified_llm_parallel.dot", "integration")

    @pytest.mark.asyncio
    async def test_parallel_completes(self, tmp_path):
        """Parallel pipeline runs all branches and summarize node."""
        hooks = RecordingHooks()
        # Provide enough responses for parallel branches (3) + re-execution
        # of one branch by engine main loop (1) + summarize (1) = 5+
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "No bugs found"}'),
                _make_response('{"status": "success", "notes": "Style is clean"}'),
                _make_response('{"status": "success", "notes": "No security issues"}'),
                _make_response('{"status": "success", "notes": "Branch re-exec"}'),
                _make_response('{"status": "success", "notes": "Summary: all clear"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

    @pytest.mark.asyncio
    async def test_parallel_emits_events_for_all_branches(self, tmp_path):
        """Events are emitted for all 3 parallel branch node IDs."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "No bugs"}'),
                _make_response('{"status": "success", "notes": "Style OK"}'),
                _make_response('{"status": "success", "notes": "Secure"}'),
                _make_response('{"status": "success", "notes": "Re-exec"}'),
                _make_response('{"status": "success", "notes": "Summary"}'),
            ]
        )
        engine = _make_integration_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        # Verify events exist for all 3 branch nodes
        req_node_ids = {d["node_id"] for d in hooks.get_data(PROVIDER_REQUEST)}
        assert "bugs" in req_node_ids
        assert "style_check" in req_node_ids
        assert "security" in req_node_ids
        assert "summarize" in req_node_ids

        # At least 4 request events (3 branches + summarize; possibly more
        # due to engine re-executing one branch after parallel handler)
        assert hooks.count(PROVIDER_REQUEST) >= 4
        assert hooks.count(PROVIDER_RESPONSE) >= 4


# ===========================================================================
# Test 6: Deny hook prevents LLM call
# ===========================================================================


class TestDenyHook:
    """Tests that deny from hooks prevents LLM calls."""

    @pytest.mark.asyncio
    async def test_deny_hook_prevents_llm_call(self, tmp_path):
        """When hooks return deny, LLM call is aborted and node FAILs."""
        hooks = RecordingHooks()
        hooks.set_deny("budget exceeded")
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "should not reach"}')]
        )

        dot = _load_fixture("unified_llm_simple.dot", "integration")
        engine = _make_integration_engine(dot, client, hooks, str(tmp_path))

        await engine.run()

        # LLM call should have been blocked
        assert client.call_count == 0
        # The implement node's outcome should be FAIL with deny reason
        assert "implement" in engine.node_outcomes
        assert engine.node_outcomes["implement"].status == StageStatus.FAIL
        assert "budget exceeded" in (
            engine.node_outcomes["implement"].failure_reason or ""
        )

    @pytest.mark.asyncio
    async def test_deny_hook_emits_request_but_no_response(self, tmp_path):
        """Deny produces a provider:request but no provider:response."""
        hooks = RecordingHooks()
        hooks.set_deny("blocked")
        client = MockUnifiedClient(
            [_make_response('{"status": "success", "notes": "should not reach"}')]
        )

        dot = _load_fixture("unified_llm_simple.dot", "integration")
        engine = _make_integration_engine(dot, client, hooks, str(tmp_path))

        await engine.run()

        # Request event should exist (deny is checked after emit)
        assert hooks.count(PROVIDER_REQUEST) == 1
        # Response should NOT exist (call was aborted)
        assert hooks.count(PROVIDER_RESPONSE) == 0


# ===========================================================================
# Test 7: Existing spec fixtures with unified-llm integration
# ===========================================================================


class TestExistingFixtures:
    """Tests using existing spec DOT fixtures with unified-llm backend."""

    @pytest.mark.asyncio
    async def test_spec_simple_linear_works_with_unified_llm(self, tmp_path):
        """spec_simple_linear.dot runs through unified-llm-client.

        Graph: start -> run_tests -> report -> exit  (2 LLM nodes)
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Tests passed"}'),
                _make_response('{"status": "success", "notes": "All tests passed"}'),
            ]
        )

        dot = _load_fixture("spec_simple_linear.dot")
        engine = _make_integration_engine(dot, client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 2  # run_tests + report
        assert hooks.count(PROVIDER_REQUEST) == 2
        assert hooks.count(PROVIDER_RESPONSE) == 2

        # Verify node IDs in events
        req_node_ids = [d["node_id"] for d in hooks.get_data(PROVIDER_REQUEST)]
        assert req_node_ids == ["run_tests", "report"]

    @pytest.mark.asyncio
    async def test_spec_stylesheet_routes_models_correctly(self, tmp_path):
        """spec_stylesheet.dot resolves provider from stylesheet.

        Graph: start -> plan -> implement -> critical_review -> exit
        Stylesheet:
            * { llm_provider: anthropic }
            .code { llm_provider: anthropic }
            #critical_review { llm_provider: openai }
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _make_response('{"status": "success", "notes": "Plan ready"}'),
                _make_response('{"status": "success", "notes": "Code written"}'),
                _make_response('{"status": "success", "notes": "Review complete"}'),
            ]
        )

        dot = _load_fixture("spec_stylesheet.dot")
        engine = _make_integration_engine(dot, client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 3  # plan, implement, critical_review

        # Verify events exist for all 3 nodes
        req_events = hooks.get_data(PROVIDER_REQUEST)
        assert len(req_events) == 3

        node_ids = [d["node_id"] for d in req_events]
        assert node_ids == ["plan", "implement", "critical_review"]

        # plan (no class, matches *) -> anthropic
        assert req_events[0]["provider"] == "anthropic"

        # implement (class=code, matches .code) -> anthropic
        assert req_events[1]["provider"] == "anthropic"

        # critical_review (id selector #critical_review) -> openai
        assert req_events[2]["provider"] == "openai"


# ===========================================================================
# Helpers for production DOT integration tests
# ===========================================================================


def _routing_response(
    preferred_label: str | None = None,
    suggested_next_ids: list[str] | None = None,
) -> unified_llm.Response:
    """Create a mock response with routing hints for edge selection.

    Production DOT files use custom condition values (outcome=process,
    outcome=done) that aren't valid StageStatus values. The engine
    routes via preferred_label (Step 2) or suggested_next_ids (Step 3)
    in the edge selection algorithm instead.
    """
    data: dict[str, Any] = {"status": "success"}
    if preferred_label is not None:
        data["preferred_label"] = preferred_label
    if suggested_next_ids is not None:
        data["suggested_next_ids"] = suggested_next_ids
    return _make_response(json.dumps(data))


def _normalize_node_types(graph: Any) -> None:
    """Normalize node_type attributes for production DOT files.

    Production DOT files use:
    - node_type="start"/"exit" instead of shape=Mdiamond/Msquare
    - node_type="stack.steer"/"stack.observe" for handler hints
    - String-valued ``timeout``, ``max_retries`` (parser doesn't coerce)

    The parser stores ``node_type`` in ``attrs`` (not ``Node.type``,
    which maps to the DOT ``type`` attribute key). This function:

    1. Copies ``attrs["node_type"]`` → ``node.type`` for handler selection
    2. Sets ``shape="Msquare"`` for exit nodes so the engine recognises
       them as terminal (the engine only checks shape, not type)
    3. Coerces ``timeout`` and ``max_retries`` from str to int (the
       graph promotion copies raw values without type conversion)
    """
    for node in graph.nodes.values():
        nt = node.attrs.get("node_type", "")
        if nt:
            node.type = nt
        if nt == "exit":
            node.shape = "Msquare"
        # Coerce promoted fields that the engine expects as int
        if isinstance(node.timeout, str):
            try:
                node.timeout = int(node.timeout)
            except (ValueError, TypeError):
                node.timeout = None
        if isinstance(node.max_retries, str):
            try:
                node.max_retries = int(node.max_retries)
            except (ValueError, TypeError):
                node.max_retries = None


def _make_production_engine(
    dot_source: str,
    mock_client: MockUnifiedClient,
    hooks: RecordingHooks,
    logs_root: str,
) -> PipelineEngine:
    """Build engine from production DOT files.

    Applies node_type normalization and skips strict validation
    (which requires Mdiamond/Msquare shapes that production DOTs
    don't use).
    """
    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    _normalize_node_types(graph)
    # Skip validate_or_raise — production DOTs use non-standard shapes

    backend = DirectProviderBackend(
        provider=object(),  # truthy sentinel
        unified_client=mock_client,
        hooks=hooks,
    )

    # Engine passes itself to handlers via execute(engine=...) — no closure needed.
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=backend, hooks=hooks)),
        logs_root=logs_root,
        hooks=hooks,
    )
    return engine


# ===========================================================================
# Test 8: Semport pipeline — semport.dot
# Semantic Port Tracking Loop: 2 providers, conditional + restart loops
# ===========================================================================


class TestSemportPipeline:
    """Tests for semport.dot (Semantic Port Tracking Loop).

    Key features exercised:
    - Two providers: anthropic (claude-sonnet-4-5) and openai (gpt-5.1)
    - Conditional branching via preferred_label routing
    - Retry/failure loop: TestValidate -> AnalyzeFailureSonnet
    - Restart loop: FinalizeAndUpdateLedger -> FetchUpstreamSonnet
    - Non-standard shapes (circle/doublecircle) with node_type attribute
    """

    def _dot(self) -> str:
        return _load_fixture("semport.dot", "integration")

    # --- Parse-only tests (no execution) ---

    def test_semport_parses_successfully(self):
        """semport.dot parses into a valid pipeline graph with all 9 nodes."""
        graph = parse_dot(self._dot())

        assert len(graph.nodes) == 9
        expected_ids = {
            "Start",
            "FetchUpstreamSonnet",
            "AnalyzePlanSonnet",
            "FinalizePlanGPT",
            "ImplementPort",
            "TestValidate",
            "AnalyzeFailureSonnet",
            "FinalizeAndUpdateLedger",
            "Exit",
        }
        assert set(graph.nodes.keys()) == expected_ids

        # Non-standard shapes are preserved by the parser
        assert graph.nodes["Start"].shape == "circle"
        assert graph.nodes["Exit"].shape == "doublecircle"

        # Parser promotes DOT "type" attribute to node.type directly
        assert graph.nodes["Start"].type == "start"
        assert graph.nodes["Exit"].type == "exit"
        assert graph.nodes["FetchUpstreamSonnet"].type == "stack.steer"
        assert graph.nodes["FinalizePlanGPT"].type == "stack.observe"

        # Graph-level attributes
        assert graph.default_fidelity == "truncate"
        assert graph.graph_attrs.get("default_thread_id") == "semport-tracking"

        # Verify edge count — 11 edges total
        assert len(graph.edges) == 11

    def test_semport_identifies_providers(self):
        """Parsed nodes have correct llm_provider attributes."""
        graph = parse_dot(self._dot())

        # Anthropic nodes (claude-sonnet-4-5)
        assert graph.nodes["FetchUpstreamSonnet"].llm_provider == "anthropic"
        assert graph.nodes["AnalyzePlanSonnet"].llm_provider == "anthropic"
        assert graph.nodes["AnalyzeFailureSonnet"].llm_provider == "anthropic"

        # OpenAI nodes (gpt-5.1)
        assert graph.nodes["FinalizePlanGPT"].llm_provider == "openai"
        assert graph.nodes["ImplementPort"].llm_provider == "openai"
        assert graph.nodes["TestValidate"].llm_provider == "openai"
        assert graph.nodes["FinalizeAndUpdateLedger"].llm_provider == "openai"

        # Start and Exit also have openai
        assert graph.nodes["Start"].llm_provider == "openai"
        assert graph.nodes["Exit"].llm_provider == "openai"

    # --- Execution tests (with normalization, skip validation) ---

    @pytest.mark.asyncio
    async def test_semport_happy_path_executes(self, tmp_path):
        """Happy path: process one commit, loop, then exit on done.

        Start -> Fetch(process) -> Analyze(port) -> Finalize -> Implement
        -> Test(pass) -> FinalizeUpdate -> Fetch(done) -> Exit

        With normalization: Start uses StartHandler (no LLM call),
        Exit is terminal (Msquare). All other nodes use codergen.
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                # 1. FetchUpstreamSonnet — found a new commit
                _routing_response(preferred_label="process"),
                # 2. AnalyzePlanSonnet — commit is relevant, port it
                _routing_response(preferred_label="port"),
                # 3. FinalizePlanGPT — plan finalized
                _routing_response(),
                # 4. ImplementPort — port implemented
                _routing_response(),
                # 5. TestValidate — tests pass (edge condition: outcome=yes)
                _routing_response(preferred_label="yes"),
                # 6. FinalizeAndUpdateLedger — ledger updated, loop back
                _routing_response(),
                # 7. FetchUpstreamSonnet (2nd) — no more commits
                _routing_response(preferred_label="done"),
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 7

        # Verify all LLM nodes executed
        executed = [d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)]
        assert "FetchUpstreamSonnet" in executed
        assert "AnalyzePlanSonnet" in executed
        assert "FinalizePlanGPT" in executed
        assert "ImplementPort" in executed
        assert "TestValidate" in executed
        assert "FinalizeAndUpdateLedger" in executed

        # FetchUpstreamSonnet appears twice (loop restart)
        assert executed.count("FetchUpstreamSonnet") == 2

    @pytest.mark.asyncio
    async def test_semport_skip_path(self, tmp_path):
        """Skip path: Analyze says skip -> loops back to Fetch -> done.

        Start -> Fetch(process) -> Analyze(skip) -> Fetch(done) -> Exit
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="process"),  # Fetch
                _routing_response(preferred_label="skip"),  # Analyze -> skip
                _routing_response(preferred_label="done"),  # Fetch (2nd) -> done
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 3

        executed = [d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)]
        assert "AnalyzePlanSonnet" in executed
        # Should NOT have gone through implementation
        assert "FinalizePlanGPT" not in executed
        assert "ImplementPort" not in executed
        assert "TestValidate" not in executed

    @pytest.mark.asyncio
    async def test_semport_done_path(self, tmp_path):
        """Done path: first Fetch returns done -> go directly to Exit.

        Start -> Fetch(done) -> Exit
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="done"),  # Fetch -> done
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
        assert client.call_count == 1

        executed = [d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)]
        assert "FetchUpstreamSonnet" in executed
        # No other LLM nodes executed
        assert "AnalyzePlanSonnet" not in executed
        assert "FinalizePlanGPT" not in executed

    @pytest.mark.asyncio
    async def test_semport_hook_events_include_both_providers(self, tmp_path):
        """Hook events show both anthropic and openai as providers."""
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                # Happy path exercises both providers
                _routing_response(preferred_label="process"),  # Fetch (anthropic)
                _routing_response(preferred_label="port"),  # Analyze (anthropic)
                _routing_response(),  # Finalize (openai)
                _routing_response(),  # Implement (openai)
                _routing_response(preferred_label="pass"),  # Test (openai)
                _routing_response(),  # FinalizeUpdate (openai)
                _routing_response(preferred_label="done"),  # Fetch 2nd (anthropic)
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        providers = {d["provider"] for d in hooks.get_data(PROVIDER_REQUEST)}
        assert "anthropic" in providers
        assert "openai" in providers


# ===========================================================================
# Test 9: Consensus Task pipeline — consensus_task.dot
# Three providers, multi-stage fan-out, retry loop
# ===========================================================================


class TestConsensusPipeline:
    """Tests for consensus_task.dot (Consensus Task Workflow).

    Key features exercised:
    - Three providers: anthropic (claude-opus-4-5), openai (gpt-5.2),
      gemini (gemini-3-flash-preview)
    - Conditional fan-out from CheckDoD (needs_dod vs has_dod)
    - Fan-in: multiple Review* nodes -> ReviewConsensus
    - Retry loop: ReviewConsensus -> Postmortem -> Plan*
    - Graph-level retry_target and fallback_retry_target
    - Variable expansion ($task, $definition_of_done)
    """

    def _dot(self) -> str:
        return _load_fixture("consensus_task.dot", "integration")

    # --- Parse-only tests ---

    def test_consensus_parses_successfully(self):
        """consensus_task.dot parses into a valid graph with all 17 nodes."""
        graph = parse_dot(self._dot())

        assert len(graph.nodes) == 17

        expected_ids = {
            "Start",
            "CheckDoD",
            "DefineDoD_Gemini",
            "DefineDoD_GPT",
            "DefineDoD_Opus",
            "ConsolidateDoD",
            "PlanGemini",
            "PlanGPT",
            "PlanOpus",
            "DebateConsolidate",
            "Implement",
            "ReviewGemini",
            "ReviewGPT",
            "ReviewOpus",
            "ReviewConsensus",
            "Postmortem",
            "Exit",
        }
        assert set(graph.nodes.keys()) == expected_ids

        # Graph-level attributes
        assert graph.graph_attrs.get("retry_target") == "CheckDoD"
        assert graph.graph_attrs.get("fallback_retry_target") == "Start"
        assert graph.default_fidelity == "truncate"

        # Verify edge count
        assert len(graph.edges) > 20  # 24 edges in total

    def test_consensus_identifies_three_providers(self):
        """Nodes span three providers: anthropic, openai, gemini."""
        graph = parse_dot(self._dot())

        providers = {n.llm_provider for n in graph.nodes.values() if n.llm_provider}
        assert providers == {"anthropic", "openai", "gemini"}

        # Spot-check specific nodes
        assert graph.nodes["CheckDoD"].llm_provider == "anthropic"
        assert graph.nodes["DefineDoD_GPT"].llm_provider == "openai"
        assert graph.nodes["DefineDoD_Gemini"].llm_provider == "gemini"
        assert graph.nodes["PlanGPT"].llm_provider == "openai"
        assert graph.nodes["ReviewGemini"].llm_provider == "gemini"
        assert graph.nodes["Implement"].llm_provider == "anthropic"

    # --- Execution tests ---

    @pytest.mark.asyncio
    async def test_consensus_has_dod_path(self, tmp_path):
        """has_dod: CheckDoD routes to Plan nodes, skipping DoD definition.

        Start -> CheckDoD(has_dod) -> PlanGemini+PlanGPT+PlanOpus (parallel)
        -> DebateConsolidate -> Implement -> ReviewGPT (lexical first)
        -> ReviewConsensus(yes) -> Exit

        preferred_label="has_dod" matches condition="outcome=has_dod" on all
        three Plan edges, triggering parallel fan-out.
        ReviewConsensus uses preferred_label="yes" to match condition="outcome=yes".
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="has_dod"),  # CheckDoD -> 3 Plan nodes parallel
                _routing_response(),  # PlanGemini (parallel)
                _routing_response(),  # PlanGPT (parallel)
                _routing_response(),  # PlanOpus (parallel)
                _routing_response(),  # DebateConsolidate
                _routing_response(),  # Implement -> ReviewGPT (lexical: ReviewGPT < ReviewGemini)
                _routing_response(),  # ReviewGPT
                _routing_response(preferred_label="yes"),  # ReviewConsensus -> Exit
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        executed = {d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)}
        # Plan node present, DoD definition nodes absent
        assert "PlanGemini" in executed
        assert "DefineDoD_Gemini" not in executed
        assert "DefineDoD_GPT" not in executed
        assert "ConsolidateDoD" not in executed

    @pytest.mark.asyncio
    async def test_consensus_needs_dod_path(self, tmp_path):
        """needs_dod: CheckDoD -> DefineDoD (parallel) -> Consolidate -> Plan -> Exit.

        Start -> CheckDoD(needs_dod) -> DefineDoD_Gemini+GPT+Opus (parallel)
        -> ConsolidateDoD -> PlanGPT (lexical: PlanGPT < PlanGemini) -> Debate
        -> Implement -> ReviewGPT (lexical first) -> ReviewConsensus(yes) -> Exit

        preferred_label="needs_dod" matches condition="outcome=needs_dod" on all
        three DoD-definition edges, triggering parallel fan-out.
        ConsolidateDoD has three unconditional edges to Plan nodes; engine picks
        PlanGPT (lexically first: 'G'+'P' < 'G'+'e' in ASCII).
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="needs_dod"),  # CheckDoD -> 3 DoD nodes parallel
                _routing_response(),  # DefineDoD_Gemini (parallel)
                _routing_response(),  # DefineDoD_GPT (parallel)
                _routing_response(),  # DefineDoD_Opus (parallel)
                _routing_response(),  # ConsolidateDoD -> PlanGPT (lexical first)
                _routing_response(),  # PlanGPT -> DebateConsolidate
                _routing_response(),  # DebateConsolidate
                _routing_response(),  # Implement -> ReviewGPT (lexical first)
                _routing_response(),  # ReviewGPT
                _routing_response(preferred_label="yes"),  # ReviewConsensus -> Exit
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        executed = {d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)}
        assert "DefineDoD_Gemini" in executed
        assert "ConsolidateDoD" in executed
        # Plan nodes should follow DoD consolidation
        assert "DebateConsolidate" in executed

    @pytest.mark.asyncio
    async def test_consensus_review_pass(self, tmp_path):
        """ReviewConsensus with yes -> exits pipeline successfully.

        has_dod path: CheckDoD -> Plans (parallel) -> Debate -> Implement
        -> ReviewGPT -> ReviewConsensus(yes) -> Exit
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="has_dod"),  # CheckDoD -> 3 Plans parallel
                _routing_response(),  # PlanGemini (parallel)
                _routing_response(),  # PlanGPT (parallel)
                _routing_response(),  # PlanOpus (parallel)
                _routing_response(),  # DebateConsolidate
                _routing_response(),  # Implement -> ReviewGPT (lexical first)
                _routing_response(),  # ReviewGPT
                _routing_response(preferred_label="yes"),  # ReviewConsensus -> Exit
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        # ReviewConsensus executed exactly once
        executed = [d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)]
        assert executed.count("ReviewConsensus") == 1

    @pytest.mark.asyncio
    async def test_consensus_review_retry(self, tmp_path):
        """ReviewConsensus retry -> Postmortem -> Plan* -> pass on 2nd try.

        First pass:
          CheckDoD(has_dod) -> Plans(parallel) -> Debate -> Implement
          -> ReviewGPT -> ReviewConsensus(retry) -> Postmortem
        Second pass:
          Postmortem -> PlanGPT (lexical first via unconditional loop_restart)
          -> Debate -> Implement -> ReviewGPT -> ReviewConsensus(yes) -> Exit

        preferred_label="retry" matches condition="outcome=retry" on the
        ReviewConsensus -> Postmortem edge.
        Postmortem has three unconditional loop_restart edges to Plan nodes;
        engine picks PlanGPT (lexically first).
        """
        hooks = RecordingHooks()
        client = MockUnifiedClient(
            [
                # --- First pass ---
                _routing_response(preferred_label="has_dod"),  # CheckDoD -> 3 Plans parallel
                _routing_response(),  # PlanGemini (parallel)
                _routing_response(),  # PlanGPT (parallel)
                _routing_response(),  # PlanOpus (parallel)
                _routing_response(),  # DebateConsolidate
                _routing_response(),  # Implement -> ReviewGPT (lexical first)
                _routing_response(),  # ReviewGPT
                _routing_response(preferred_label="retry"),  # ReviewConsensus -> Postmortem
                _routing_response(),  # Postmortem -> PlanGPT (lexical first, loop_restart)
                # --- Second pass ---
                _routing_response(),  # PlanGPT -> DebateConsolidate
                _routing_response(),  # DebateConsolidate
                _routing_response(),  # Implement -> ReviewGPT (lexical first)
                _routing_response(),  # ReviewGPT
                _routing_response(preferred_label="yes"),  # ReviewConsensus -> Exit
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        outcome = await engine.run()

        assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

        executed = [d["node_id"] for d in hooks.get_data(PIPELINE_NODE_START)]
        assert "Postmortem" in executed
        # ReviewConsensus executed twice (retry + pass)
        assert executed.count("ReviewConsensus") == 2

    @pytest.mark.asyncio
    async def test_consensus_hook_events_include_all_three_providers(self, tmp_path):
        """Hook events show anthropic, openai, AND gemini."""
        hooks = RecordingHooks()
        # has_dod parallel path:
        #   CheckDoD (anthropic) -> PlanGemini (gemini) + PlanGPT (openai) + PlanOpus (anthropic)
        #   -> DebateConsolidate (anthropic) -> Implement (anthropic)
        #   -> ReviewGPT (openai) -> ReviewConsensus (anthropic)
        # All three providers covered: anthropic, openai, gemini.
        client = MockUnifiedClient(
            [
                _routing_response(preferred_label="has_dod"),  # CheckDoD (anthropic)
                _routing_response(),  # PlanGemini (gemini, parallel)
                _routing_response(),  # PlanGPT (openai, parallel)
                _routing_response(),  # PlanOpus (anthropic, parallel)
                _routing_response(),  # DebateConsolidate (anthropic)
                _routing_response(),  # Implement (anthropic) -> ReviewGPT (lexical first)
                _routing_response(),  # ReviewGPT (openai)
                _routing_response(preferred_label="yes"),  # ReviewConsensus (anthropic)
            ]
        )
        engine = _make_production_engine(self._dot(), client, hooks, str(tmp_path))

        await engine.run()

        providers = {d["provider"] for d in hooks.get_data(PROVIDER_REQUEST)}
        assert "anthropic" in providers
        assert "openai" in providers
        assert "gemini" in providers
