"""Tests for ConditionalHandler — spec §4.7.

A diamond node (shape=diamond) should dispatch to the ConditionalHandler,
which is a no-op that returns SUCCESS immediately.  The engine's edge-selection
algorithm (§3.3) handles routing from the diamond node — the handler itself
does no work.

Spec coverage: §2.8 (shape-to-handler mapping), §4.7 (ConditionalHandler).

Two contract assertions:

1. SHAPE_TO_HANDLER["diamond"] == "conditional"
   (spec §2.8 registers diamond → conditional)

2. ConditionalHandler.execute() returns SUCCESS immediately.
   No LLM call, no tool invocation, completes in microseconds.
"""

import time

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Contract 1: SHAPE_TO_HANDLER registration
# ---------------------------------------------------------------------------


def test_diamond_shape_registered_as_conditional():
    """SHAPE_TO_HANDLER must map 'diamond' to 'conditional' (spec §2.8)."""
    assert "diamond" in SHAPE_TO_HANDLER, (
        "shape='diamond' is missing from SHAPE_TO_HANDLER. "
        "Add 'diamond': 'conditional' per spec §2.8."
    )
    assert SHAPE_TO_HANDLER["diamond"] == "conditional", (
        f"shape='diamond' maps to '{SHAPE_TO_HANDLER['diamond']}', "
        f"expected 'conditional' (spec §2.8)."
    )


def test_diamond_shape_dispatches_to_conditional_handler():
    """HandlerRegistry.get() for a diamond node must return ConditionalHandler.

    Before the fix, diamond silently falls through to CodergenHandler
    (because 'diamond' is absent from SHAPE_TO_HANDLER and the old
    code defaulted to 'codergen').  After the fix, it must return the
    no-op ConditionalHandler.
    """
    from amplifier_module_loop_pipeline.handlers.conditional import ConditionalHandler

    registry = HandlerRegistry(HandlerContext())
    diamond_node = Node(id="MyGate", shape="diamond")
    handler = registry.get(diamond_node)

    assert isinstance(handler, ConditionalHandler), (
        f"HandlerRegistry.get() for shape='diamond' returned "
        f"{type(handler).__name__}, expected ConditionalHandler. "
        f"Register 'diamond' in SHAPE_TO_HANDLER and HandlerRegistry."
    )


# ---------------------------------------------------------------------------
# Contract 2: ConditionalHandler.execute() is a no-op SUCCESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conditional_handler_returns_success_immediately(tmp_path):
    """ConditionalHandler.execute() must return SUCCESS without LLM or tool call.

    Completing in under 100ms is the implementation contract (the handler
    has zero I/O — any LLM call would take orders of magnitude longer).
    """
    from amplifier_module_loop_pipeline.handlers.conditional import ConditionalHandler

    handler = ConditionalHandler()
    node = Node(id="MyGate", shape="diamond")
    context = PipelineContext()
    graph = Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond"), "MyGate": node},
        edges=[Edge(from_node="start", to_node="MyGate")],
    )

    start = time.monotonic()
    outcome = await handler.execute(node, context, graph, str(tmp_path))
    elapsed_ms = (time.monotonic() - start) * 1000

    assert outcome.status == StageStatus.SUCCESS, (
        f"ConditionalHandler must return SUCCESS, got {outcome.status}. "
        f"Failure reason: {outcome.failure_reason}"
    )
    assert elapsed_ms < 100, (
        f"ConditionalHandler took {elapsed_ms:.1f}ms; must complete in < 100ms. "
        f"An LLM call was likely made — the handler should be a pure no-op."
    )


@pytest.mark.asyncio
async def test_diamond_node_completes_without_llm_in_pipeline(tmp_path):
    """A pipeline with a diamond node must complete without invoking the LLM backend.

    This is the integration-level proof: a diamond followed by a conditional
    edge must route correctly and not silently spin up a codergen agent.
    """
    execution_counts: dict[str, int] = {}

    class TrackingBackend:
        async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
            execution_counts[node.id] = execution_counts.get(node.id, 0) + 1
            return "done"

    from amplifier_module_loop_pipeline.engine import PipelineEngine

    # Simple graph: start → diamond → leaf → exit
    graph = Graph(
        name="test-diamond-no-llm",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(id="gate", shape="diamond"),
            "leaf": Node(id="leaf", shape="box", prompt="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="leaf"),
            Edge(from_node="leaf", to_node="exit"),
        ],
    )

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=TrackingBackend())),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline failed: {outcome.failure_reason}"
    )
    # Diamond gate itself must NOT have triggered the LLM backend
    assert execution_counts.get("gate", 0) == 0, (
        f"gate (shape=diamond) called the LLM backend "
        f"{execution_counts['gate']} time(s). ConditionalHandler must be a no-op."
    )
    # Downstream leaf must have executed (routing was correct)
    assert execution_counts.get("leaf", 0) == 1, (
        f"leaf must execute exactly once after diamond gate, "
        f"got {execution_counts.get('leaf', 0)}"
    )
