"""Guard tests: preferred_label must not cross context-isolation boundaries.

Design note: preferred_label staleness across subgraph/iteration boundaries
(option a). Rule: *a routing verdict never crosses an engine/context-isolation
boundary implicitly.* The clear at each clone site is symmetric with the
loop_restart clear in ``PipelineEngine.run()`` (engine.py Step 6b).

Four clone sites covered:
  1. ``handlers/pipeline.py``     -- folder-node child pipeline
  2. ``handlers/manager_loop.py`` -- manager-loop child subgraph
  3. ``handlers/parallel.py``     -- parallel handler branch
  4. ``engine.py``                -- multi-edge parallel fan-out branch

Plus the headline production repro: a drain-loop parent WITHOUT loop_restart
on its loop-back edge (so the #89 clear cannot mask the clone-boundary leak),
where a no-verdict child assess must fall to the else edge instead of routing
on a stale "converged" inherited from the previous source.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.manager_loop import ManagerLoopHandler
from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler
from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise


class RecordingBackend:
    """Backend that records, per call, the node id and the preferred_label
    the (child) context resolved at the moment the node executed."""

    def __init__(self):
        self.calls: list[str] = []
        self.seen_labels: dict[str, list[object]] = {}

    def _record(self, node, context) -> None:
        self.calls.append(node.id)
        self.seen_labels.setdefault(node.id, []).append(context.get("preferred_label"))

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self._record(node, context)
        return Outcome(status=StageStatus.SUCCESS)


# ---------------------------------------------------------------------------
# Headline test: the production repro (no loop_restart to mask the leak)
# ---------------------------------------------------------------------------


class TwoSourceAssessBackend(RecordingBackend):
    """Mirrors the production incident's assess node across two folder-node
    invocations sharing one parent context.

    Source A's assess converges cleanly (preferred_label="converged").
    Source B's assess produces NO verdict at all -- the no-verdict backend
    shapes (empty final message / prose fallback) both come back as
    ``Outcome(SUCCESS, preferred_label=None)``.
    """

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self._record(node, context)
        if node.id != "assess":
            return Outcome(status=StageStatus.SUCCESS)
        if self.calls.count("assess") == 1:
            # Source A: genuine verdict.
            return Outcome(
                status=StageStatus.SUCCESS,
                preferred_label="converged",
                context_updates={"more_sources": "true"},
            )
        # Source B: no verdict (the production no-verdict shape).
        return Outcome(
            status=StageStatus.SUCCESS,
            context_updates={"more_sources": "false"},
        )


CHILD_ASSESS_DOT = """
digraph childassess {
    start [shape=Mdiamond]
    assess [shape=box, prompt="Assess this source"]
    refine_work [shape=box, prompt="Refine the source"]
    fallback_work [shape=box, prompt="Fallback refine"]
    done [shape=Msquare]
    pending [shape=Msquare]
    start -> assess
    assess -> done          [condition="context.preferred_label=converged"]
    assess -> refine_work   [condition="context.preferred_label=refine"]
    assess -> fallback_work [label="else"]
    refine_work -> pending
    fallback_work -> pending
}
"""


@pytest.mark.asyncio
async def test_no_verdict_child_falls_to_else_edge_without_loop_restart(tmp_path):
    """THE PRODUCTION REPRO. A drain-loop parent runs a child pipeline per
    source via a folder node, looping back WITHOUT ``loop_restart`` -- so the
    #89 clear in ``run()`` never fires and only the clone-boundary clear can
    protect the child.

    Source A's child converges ("converged" propagates into the parent
    context via the engine's conditional write). Source B's child assess
    produces NO verdict. Without the clone-boundary clear, source B's child
    inherits the parent's live "converged" at the folder-node clone, the
    child's ``context.preferred_label=converged`` edge matches a ghost, and
    the child skips its refine loop entirely (the incident: 4 good sources
    quarantined). With the fix, the child starts with no inherited verdict
    and the no-verdict assess falls to the safe else edge (fallback_work).
    """
    child_dot_path = tmp_path / "child.dot"
    child_dot_path.write_text(CHILD_ASSESS_DOT)

    # NOTE: the loop-back edge deliberately has NO loop_restart attribute.
    parent_dot = f"""
    digraph parentassess {{
        start [shape=Mdiamond]
        run_synthesize [shape=folder, dot_file="{child_dot_path}", outputs="more_sources"]
        done [shape=Msquare]
        start -> run_synthesize
        run_synthesize -> run_synthesize [condition="context.more_sources=true"]
        run_synthesize -> done [condition="context.more_sources=false"]
    }}
    """
    graph = parse_dot(parent_dot)
    validate_or_raise(graph)
    graph.source_dir = str(tmp_path)

    backend = TwoSourceAssessBackend()
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # assess ran exactly twice: source A, then source B.
    assert backend.calls.count("assess") == 2
    # THE FIX (entry state): source B's child context must NOT have inherited
    # source A's "converged" at the folder-node clone boundary.
    assert backend.seen_labels["assess"] == [None, None]
    # THE FIX (routing): source B's no-verdict assess must fall to the else
    # edge (fallback_work), NOT route to done on the stale "converged".
    assert backend.calls.count("fallback_work") == 1
    # And it must not have matched the refine edge either -- no verdict
    # resolves to "", which matches neither condition.
    assert "refine_work" not in backend.calls
    # The run drained via source B's genuine context_updates.
    assert engine.context.get("more_sources") == "false"


# ---------------------------------------------------------------------------
# Per-site tests: child/branch clone starts clean, parent copy preserved
# ---------------------------------------------------------------------------

CHILD_PROBE_DOT = """
digraph childprobe {
    start [shape=Mdiamond]
    probe [shape=box, prompt="Probe the context"]
    done [shape=Msquare]
    start -> probe -> done
}
"""


def _folder_parent_graph(tmp_path, folder_attrs: dict | None = None) -> Graph:
    child_dot = tmp_path / "child.dot"
    child_dot.write_text(CHILD_PROBE_DOT)
    attrs = {"dot_file": str(child_dot)}
    attrs.update(folder_attrs or {})
    return Graph(
        name="folder-parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "sub": Node(id="sub", shape="folder", type="pipeline", attrs=attrs),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="sub"),
            Edge(from_node="sub", to_node="done"),
        ],
        source_dir=str(tmp_path),
    )


def _registry_factory(backend):
    def factory():
        return HandlerRegistry(HandlerContext(backend=backend))

    return factory


@pytest.mark.asyncio
async def test_folder_child_clone_starts_without_preferred_label(tmp_path):
    """Site 1 (handlers/pipeline.py): the folder-node child context must not
    inherit the parent's preferred_label; the parent's own copy survives."""
    backend = RecordingBackend()
    graph = _folder_parent_graph(tmp_path)
    context = PipelineContext()
    context.set("preferred_label", "converged")

    handler = PipelineHandler(handler_registry_factory=_registry_factory(backend))
    outcome = await handler.execute(
        graph.nodes["sub"], context, graph, str(tmp_path / "logs")
    )

    assert outcome.status == StageStatus.SUCCESS
    # Child saw NO inherited verdict.
    assert backend.seen_labels["probe"] == [None]
    # Parent's own copy is preserved for the parent's continued routing.
    assert context.get("preferred_label") == "converged"


@pytest.mark.asyncio
async def test_folder_context_attr_seeding_still_works(tmp_path):
    """Deliberate seeding via a ``context.preferred_label`` node attribute
    must still reach the child (pins clear-BEFORE-inject ordering)."""
    backend = RecordingBackend()
    graph = _folder_parent_graph(
        tmp_path, folder_attrs={"context.preferred_label": "seed"}
    )
    context = PipelineContext()
    context.set("preferred_label", "converged")

    handler = PipelineHandler(handler_registry_factory=_registry_factory(backend))
    outcome = await handler.execute(
        graph.nodes["sub"], context, graph, str(tmp_path / "logs")
    )

    assert outcome.status == StageStatus.SUCCESS
    # Explicit seeding wins over the boundary clear.
    assert backend.seen_labels["probe"] == ["seed"]


@pytest.mark.asyncio
async def test_manager_loop_child_clone_starts_without_preferred_label():
    """Site 2 (handlers/manager_loop.py): the manager-loop child context
    must not inherit the parent's preferred_label."""
    captured: list[object] = []

    class CapturingEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            assert context is not None
            captured.append(context.get("preferred_label"))
            return Outcome(status=StageStatus.SUCCESS)

    graph = Graph(
        name="manager-parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "manager": Node(
                id="manager",
                shape="house",
                attrs={"manager.max_cycles": "1"},
            ),
            "child_task": Node(id="child_task", shape="box", label="Do work"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="manager"),
            Edge(from_node="manager", to_node="child_task"),
            Edge(from_node="child_task", to_node="exit"),
        ],
    )
    context = PipelineContext()
    context.set("preferred_label", "converged")

    handler = ManagerLoopHandler()
    # Duck-typed mock engine (same pattern as test_manager_loop._MockEngine).
    outcome = await handler.execute(
        graph.nodes["manager"],
        context,
        graph,
        "/tmp",
        engine=cast(Any, CapturingEngine()),
    )

    assert outcome.status == StageStatus.SUCCESS
    assert captured == [None]
    # Parent's own copy is preserved.
    assert context.get("preferred_label") == "converged"


@pytest.mark.asyncio
async def test_parallel_branch_clone_starts_without_preferred_label():
    """Site 3 (handlers/parallel.py): each parallel branch context must not
    inherit the parent's preferred_label."""
    captured: dict[str, object] = {}

    class CapturingEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            assert context is not None
            captured[node_id] = context.get("preferred_label")
            return Outcome(status=StageStatus.SUCCESS)

    par_node = Node(id="parallel", shape="component")
    graph = Graph(
        name="parallel-parent",
        nodes={
            "parallel": par_node,
            "branch_a": Node(id="branch_a", prompt="A"),
            "branch_b": Node(id="branch_b", prompt="B"),
        },
        edges=[
            Edge(from_node="parallel", to_node="branch_a"),
            Edge(from_node="parallel", to_node="branch_b"),
        ],
    )
    context = PipelineContext()
    context.set("preferred_label", "converged")

    handler = ParallelHandler()
    # Duck-typed mock engine (same pattern as test_parallel.FakeSubgraphRunner).
    outcome = await handler.execute(
        par_node, context, graph, "/tmp", engine=cast(Any, CapturingEngine())
    )

    assert outcome.is_success
    assert captured == {"branch_a": None, "branch_b": None}
    # Parent's own copy is preserved.
    assert context.get("preferred_label") == "converged"


@pytest.mark.asyncio
async def test_engine_fanout_branch_starts_without_preferred_label(tmp_path):
    """Site 4 (shape=component fan-out via ParallelHandler): parallel
    branches must not inherit the parent's preferred_label."""
    backend = RecordingBackend()
    graph = Graph(
        name="fanout-parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "fork": Node(id="fork", shape="component"),
            "branch_a": Node(id="branch_a", shape="box", prompt="A"),
            "branch_b": Node(id="branch_b", shape="box", prompt="B"),
            "gather": Node(id="gather", shape="tripleoctagon"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="fork"),
            Edge(from_node="fork", to_node="branch_a"),
            Edge(from_node="fork", to_node="branch_b"),
            Edge(from_node="branch_a", to_node="gather"),
            Edge(from_node="branch_b", to_node="gather"),
            Edge(from_node="gather", to_node="exit"),
        ],
    )
    context = PipelineContext()
    context.set("preferred_label", "converged")

    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # Both branches executed and neither saw the parent's label.
    assert backend.seen_labels["branch_a"] == [None]
    assert backend.seen_labels["branch_b"] == [None]


# ---------------------------------------------------------------------------
# Child -> parent propagation unchanged (the b3 surface stays byte-stable)
# ---------------------------------------------------------------------------


class ConvergingAssessBackend(RecordingBackend):
    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self._record(node, context)
        if node.id == "assess":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


CHILD_CONVERGE_DOT = """
digraph childconverge {
    start [shape=Mdiamond]
    assess [shape=box, prompt="Assess this source"]
    done [shape=Msquare]
    pending [shape=Msquare]
    start -> assess
    assess -> done [condition="context.preferred_label=converged"]
    assess -> pending
}
"""


@pytest.mark.asyncio
async def test_child_converged_verdict_still_propagates_to_parent(tmp_path):
    """A child's genuine final verdict still crosses UP the boundary: the
    parent imports the truthy label (conditional write) and the parent's
    ``outcome=converged`` edge (fresh-object resolution) still matches."""
    child_dot_path = tmp_path / "child.dot"
    child_dot_path.write_text(CHILD_CONVERGE_DOT)

    parent_dot = f"""
    digraph parentconverge {{
        start [shape=Mdiamond]
        run_child [shape=folder, dot_file="{child_dot_path}"]
        after_converged [shape=box, prompt="Post-convergence step"]
        fail_marker [shape=box, prompt="Should not run"]
        done [shape=Msquare]
        start -> run_child
        run_child -> after_converged [condition="outcome=converged"]
        run_child -> fail_marker [label="else"]
        after_converged -> done
        fail_marker -> done
    }}
    """
    graph = parse_dot(parent_dot)
    validate_or_raise(graph)
    graph.source_dir = str(tmp_path)

    backend = ConvergingAssessBackend()
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # Parent routed on the child's genuine verdict.
    assert "after_converged" in backend.calls
    assert "fail_marker" not in backend.calls
    # Parent context imported the truthy label (W2 unchanged).
    assert engine.context.get("preferred_label") == "converged"
