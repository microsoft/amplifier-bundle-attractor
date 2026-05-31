"""Tests for handler registry and core handlers.

Spec coverage: HAND-001–007, HSTART-001–002, HEXIT-001–003,
CODER-001–011, COND-001, TOOL-001–004.
"""

import json

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler
from amplifier_module_loop_pipeline.handlers.exit import ExitHandler
from amplifier_module_loop_pipeline.handlers.start import StartHandler
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


def _make_graph(**kwargs) -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
        **kwargs,
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# --- HandlerRegistry ---


def test_registry_resolves_start_handler():
    """Registry maps shape=Mdiamond to StartHandler."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="s", shape="Mdiamond")
    handler = registry.get(node)
    assert isinstance(handler, StartHandler)


def test_registry_resolves_exit_handler():
    """Registry maps shape=Msquare to ExitHandler."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="e", shape="Msquare")
    handler = registry.get(node)
    assert isinstance(handler, ExitHandler)


def test_registry_resolves_codergen_handler():
    """Registry maps shape=box to CodergenHandler."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="c", shape="box")
    handler = registry.get(node)
    assert isinstance(handler, CodergenHandler)


def test_registry_explicit_type_overrides_shape():
    """Node type attribute overrides shape-based resolution."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="x", shape="box", type="tool")
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


# --- StartHandler ---


@pytest.mark.asyncio
async def test_start_handler_returns_success():
    """Start handler returns SUCCESS immediately."""
    handler = StartHandler()
    node = Node(id="start", shape="Mdiamond")
    outcome = await handler.execute(node, _make_context(), _make_graph(), "/tmp")
    assert outcome.status == StageStatus.SUCCESS


# --- ExitHandler ---


@pytest.mark.asyncio
async def test_exit_handler_returns_success():
    """Exit handler returns SUCCESS immediately."""
    handler = ExitHandler()
    node = Node(id="exit", shape="Msquare")
    outcome = await handler.execute(node, _make_context(), _make_graph(), "/tmp")
    assert outcome.status == StageStatus.SUCCESS


# --- CodergenHandler ---


class MockBackend:
    """Mock backend that returns a fixed string."""

    def __init__(self, return_value: str = "done"):
        self._return_value = return_value
        self.last_prompt: str = ""
        self.call_count = 0

    async def run(
        self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
    ) -> str | Outcome:
        self.last_prompt = prompt
        self.call_count += 1
        return self._return_value


@pytest.mark.asyncio
async def test_codergen_handler_calls_backend(tmp_path):
    """Codergen calls backend.run() and returns SUCCESS."""
    backend = MockBackend("Implementation complete")
    handler = CodergenHandler(backend=backend)
    node = Node(id="implement", prompt="Build the feature for $goal")
    graph = _make_graph(goal="user auth")
    outcome = await handler.execute(node, _make_context(), graph, str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS
    assert backend.call_count == 1
    # Verify $goal was expanded in prompt
    assert "user auth" in backend.last_prompt


@pytest.mark.asyncio
async def test_codergen_writes_stage_files(tmp_path):
    """Codergen writes prompt.md, response.md, status.json."""
    backend = MockBackend("done")
    handler = CodergenHandler(backend=backend)
    node = Node(id="step1", prompt="Do the thing")
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS
    assert (tmp_path / "step1" / "prompt.md").exists()
    assert (tmp_path / "step1" / "response.md").exists()
    assert (tmp_path / "step1" / "status.json").exists()


@pytest.mark.asyncio
async def test_codergen_status_json_content(tmp_path):
    """status.json contains the outcome fields."""
    backend = MockBackend("all good")
    handler = CodergenHandler(backend=backend)
    node = Node(id="s1", prompt="test prompt")
    await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
    status_data = json.loads((tmp_path / "s1" / "status.json").read_text())
    assert status_data["status"] == "success"


@pytest.mark.asyncio
async def test_codergen_backend_returns_outcome(tmp_path):
    """If backend returns an Outcome directly, use it."""

    class OutcomeBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return Outcome(status=StageStatus.FAIL, failure_reason="tests failing")

    handler = CodergenHandler(backend=OutcomeBackend())
    node = Node(id="failing", prompt="test")
    outcome = await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.FAIL
    assert outcome.failure_reason == "tests failing"


@pytest.mark.asyncio
async def test_codergen_uses_label_when_no_prompt(tmp_path):
    """Falls back to label if no prompt attribute."""
    backend = MockBackend("ok")
    handler = CodergenHandler(backend=backend)
    node = Node(id="step", label="Plan the implementation")
    await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
    assert "Plan the implementation" in backend.last_prompt


@pytest.mark.asyncio
async def test_codergen_uses_llm_prompt_from_attrs(tmp_path):
    """CodergenHandler reads llm_prompt from node.attrs as fallback."""
    backend = MockBackend("ok")
    handler = CodergenHandler(backend=backend)
    # No prompt set, but llm_prompt in attrs (as DOT files like semport.dot use)
    node = Node(
        id="test",
        label="TestNode",
        prompt="",
        attrs={"llm_prompt": "Do the thing in detail"},
    )
    await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
    # Should use llm_prompt, NOT fall back to label
    assert backend.last_prompt == "Do the thing in detail"


# --- ToolHandler ---


@pytest.mark.asyncio
async def test_tool_handler_runs_command(tmp_path):
    """ToolHandler runs tool_command and returns SUCCESS (TOOL-001)."""
    node = Node(id="lint", attrs={"tool_command": "echo hello"})
    handler = ToolHandler()
    ctx = _make_context()
    outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS
    assert "hello" in ctx.get("tool.output", "")


@pytest.mark.asyncio
async def test_tool_handler_no_command_returns_fail(tmp_path):
    """ToolHandler with no tool_command returns FAIL (TOOL-002)."""
    node = Node(id="lint", attrs={})
    handler = ToolHandler()
    ctx = _make_context()
    outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.FAIL
    assert "tool_command" in (outcome.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_tool_handler_failed_command(tmp_path):
    """ToolHandler returns FAIL when command exits non-zero (TOOL-003)."""
    node = Node(id="bad", attrs={"tool_command": "false"})
    handler = ToolHandler()
    ctx = _make_context()
    outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_tool_handler_captures_stdout(tmp_path):
    """ToolHandler puts stdout into context as tool.output (TOOL-004)."""
    node = Node(id="echo", attrs={"tool_command": "echo 'test output'"})
    handler = ToolHandler()
    ctx = _make_context()
    await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert "test output" in ctx.get("tool.output", "")


@pytest.mark.asyncio
async def test_tool_handler_writes_log_files(tmp_path):
    """ToolHandler writes command.txt and output.txt to stage dir."""
    node = Node(id="step", attrs={"tool_command": "echo logged"})
    handler = ToolHandler()
    ctx = _make_context()
    await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert (tmp_path / "step" / "command.txt").exists()
    assert (tmp_path / "step" / "output.txt").exists()


# --- M-16: ToolHandler timeout support ---


@pytest.mark.asyncio
async def test_tool_handler_respects_node_timeout(tmp_path):
    """M-16: ToolHandler enforces node timeout attribute."""
    # sleep 10 should be killed by the 1-second timeout
    node = Node(id="slow", attrs={"tool_command": "sleep 10"}, timeout=1)
    handler = ToolHandler()
    ctx = _make_context()
    outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.FAIL
    assert "timeout" in (outcome.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_tool_handler_no_timeout_runs_normally(tmp_path):
    """M-16: When no timeout, command runs to completion."""
    node = Node(id="fast", attrs={"tool_command": "echo done"})
    handler = ToolHandler()
    ctx = _make_context()
    outcome = await handler.execute(node, ctx, _make_graph(), str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS


# --- HandlerRegistry with ToolHandler ---


def test_registry_resolves_tool_handler():
    """Registry maps shape=parallelogram to ToolHandler."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="t", shape="parallelogram")
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


# --- $context variable expansion ---


@pytest.mark.asyncio
async def test_codergen_expands_context_variable(tmp_path):
    """$context in prompt is replaced with last_response from context."""
    backend = MockBackend("review complete")
    handler = CodergenHandler(backend=backend)
    node = Node(id="review", prompt="Review this: $context")
    graph = _make_graph()
    ctx = _make_context()
    ctx.set("last_response", "The draft output from previous node")
    outcome = await handler.execute(node, ctx, graph, str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS
    # $context should be replaced with the last_response value
    assert "The draft output from previous node" in backend.last_prompt
    assert "$context" not in backend.last_prompt


@pytest.mark.asyncio
async def test_codergen_context_variable_empty_when_no_last_response(tmp_path):
    """$context resolves to empty string when no last_response in context."""
    backend = MockBackend("ok")
    handler = CodergenHandler(backend=backend)
    node = Node(id="first", prompt="Do this: $context")
    graph = _make_graph()
    ctx = _make_context()
    # No last_response set in context
    await handler.execute(node, ctx, graph, str(tmp_path))
    assert "$context" not in backend.last_prompt
    assert "Do this: " in backend.last_prompt


@pytest.mark.asyncio
async def test_codergen_expands_both_goal_and_context(tmp_path):
    """Both $goal and $context are expanded in the same prompt."""
    backend = MockBackend("done")
    handler = CodergenHandler(backend=backend)
    node = Node(id="step", prompt="Goal: $goal, Previous: $context")
    graph = _make_graph(goal="build auth")
    ctx = _make_context()
    ctx.set("last_response", "draft plan")
    await handler.execute(node, ctx, graph, str(tmp_path))
    assert "build auth" in backend.last_prompt
    assert "draft plan" in backend.last_prompt
    assert "$goal" not in backend.last_prompt
    assert "$context" not in backend.last_prompt


# --- Integration: $context flows across pipeline nodes ---


@pytest.mark.asyncio
async def test_context_variable_flows_between_pipeline_nodes(tmp_path):
    """In a 2-node pipeline, the second node's $context contains the first node's response."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    class CapturingBackend:
        """Backend that returns a fixed response and captures prompts per node."""

        def __init__(self):
            self.prompts: dict[str, str] = {}

        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            self.prompts[node.id] = prompt
            if node.id == "draft":
                return "Here is the draft content about fibonacci"
            return "review done"

    backend = CapturingBackend()
    dot_source = """
    digraph {
        graph [goal="test context flow"]
        start [shape=Mdiamond]
        draft [shape=box, prompt="Write a draft"]
        review [shape=box, prompt="Review this: $context"]
        done [shape=Msquare]
        start -> draft -> review -> done
    }
    """
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.SUCCESS
    # The review node's prompt should contain the draft node's response
    assert "Here is the draft content about fibonacci" in backend.prompts["review"]
    assert "$context" not in backend.prompts["review"]


def test_registry_unknown_shape_raises_value_error():
    """Unknown shape raises ValueError — no silent fallback to codergen.

    Changed from the old behavior where unknown shapes silently fell back
    to CodergenHandler.  The new behavior is a clear, actionable error:
    unknown shape = loud failure, not a surprise LLM call.

    Authors who want LLM-driven nodes must use shape=box explicitly.
    """
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="u", shape="trapezoid")
    with pytest.raises(ValueError, match="trapezoid"):
        registry.get(node)


def test_registry_custom_handler_registration():
    """Custom handlers can be registered and resolved (HAND-005)."""

    class CustomHandler:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            return Outcome(status=StageStatus.SUCCESS, notes="custom")

    registry = HandlerRegistry(HandlerContext())
    registry.register("my_custom", CustomHandler())
    node = Node(id="x", type="my_custom")
    handler = registry.get(node)
    assert isinstance(handler, CustomHandler)


# --- node_type fallback dispatch ---


def test_registry_node_type_fallback():
    """node_type attribute is used as fallback when type is empty."""
    registry = HandlerRegistry(HandlerContext())
    # node_type="tool" should resolve to ToolHandler
    node = Node(id="x", shape="box", type="", attrs={"node_type": "tool"})
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


def test_registry_node_type_unknown_falls_to_shape():
    """Unknown node_type (e.g. stack.observe) falls through to shape-based lookup."""
    registry = HandlerRegistry(HandlerContext())
    # node_type="stack.observe" is NOT a registered handler type
    # shape=box -> codergen
    node = Node(id="x", shape="box", type="", attrs={"node_type": "stack.observe"})
    handler = registry.get(node)
    assert isinstance(handler, CodergenHandler)


def test_registry_node_type_steer_falls_to_shape():
    """Unknown node_type (stack.steer) falls through to shape-based lookup."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="x", shape="box", type="", attrs={"node_type": "stack.steer"})
    handler = registry.get(node)
    assert isinstance(handler, CodergenHandler)


def test_registry_type_takes_priority_over_node_type():
    """Explicit type= attribute takes priority over node_type."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="x", shape="box", type="tool", attrs={"node_type": "codergen"})
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


def test_registry_node_type_takes_priority_over_shape():
    """node_type takes priority over shape-based lookup."""
    registry = HandlerRegistry(HandlerContext())
    # shape=box -> codergen, but node_type="tool" should override to ToolHandler
    node = Node(id="x", shape="box", type="", attrs={"node_type": "tool"})
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


def test_registry_no_type_no_node_type_uses_shape():
    """When neither type nor node_type is set, shape-based lookup works as before."""
    registry = HandlerRegistry(HandlerContext())
    node = Node(id="x", shape="parallelogram")
    handler = registry.get(node)
    assert isinstance(handler, ToolHandler)


def test_registry_register_replaces_existing():
    """Registering for an existing type replaces the handler (HAND-006)."""
    registry = HandlerRegistry(HandlerContext())
    original = registry.get(Node(id="s", shape="Mdiamond"))
    assert isinstance(original, StartHandler)

    class NewStart:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            return Outcome(status=StageStatus.SUCCESS, notes="new start")

    registry.register("start", NewStart())
    replaced = registry.get(Node(id="s", shape="Mdiamond"))
    assert isinstance(replaced, NewStart)


# --- Task 2: CodergenHandler raises when no backend ---


@pytest.mark.asyncio
async def test_codergen_no_backend_raises_valueerror(tmp_path):
    """CodergenHandler requires a backend — raises ValueError when none provided."""
    handler = CodergenHandler(backend=None)
    node = Node(id="step", prompt="Do work")
    with pytest.raises(ValueError, match="CodergenHandler requires a backend"):
        await handler.execute(node, _make_context(), _make_graph(), str(tmp_path))
