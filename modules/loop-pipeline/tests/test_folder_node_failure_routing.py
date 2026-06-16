"""Tests for folder/subgraph-node failure routing.

Fills a coverage gap in the engine test suite: no prior test exercises
the parent engine's edge-selection and retry-target chain when a
shape=folder (dot_file=) node's child pipeline terminates with FAIL.

Three cases:

  1. condition="outcome=fail" edge on the folder node fires — the parent engine
     routes via the explicit edge, NOT via graph-level retry_target.

  2. Node-level retry_target on the folder node overrides graph-level
     retry_target when no condition edge is present.

  3. Baseline: graph-level retry_target fires when neither (1) nor (2) is
     present — confirms the default routing behavior for folder-node failures.

The child pipeline always fails via a shape=parallelogram tool node with
``tool_command="exit 1"``.  ToolHandler runs the subprocess; non-zero exit
produces StageStatus.FAIL, which PipelineHandler propagates to the parent engine.

Spec coverage: EXEC-015–018, Section 3.3 (folder-node failure path extension).
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome


# ---------------------------------------------------------------------------
# CountingBackend — mirrors the definition in test_failure_routing.py.
# Inlined per suite convention: no shared helpers module exists; each test file
# carries its own local stubs (cf. _MockBackend in test_pipeline_handler.py).
# ---------------------------------------------------------------------------


class CountingBackend:
    """Backend that tracks call count per node and returns configurable outcomes."""

    def __init__(self, outcomes: dict[str, list[Outcome | str]] | None = None):
        self._outcomes = outcomes or {}
        self._call_counts: dict[str, int] = {}

    async def run(
        self, node, prompt, context, incoming_edge=None, graph=None
    ) -> str | Outcome:
        count = self._call_counts.get(node.id, 0)
        self._call_counts[node.id] = count + 1
        seq = self._outcomes.get(node.id, ["done"])
        if count < len(seq):
            return seq[count]
        return seq[-1]  # repeat last

    def call_count(self, node_id: str) -> int:
        return self._call_counts.get(node_id, 0)


# ---------------------------------------------------------------------------
# Child DOT — always fails.
# shape=parallelogram dispatches to ToolHandler; exit 1 -> StageStatus.FAIL.
# fail_work has no outgoing edges and no retry_target, so the child engine
# terminates with FAIL.  PipelineHandler propagates FAIL to the parent engine.
# ---------------------------------------------------------------------------

_CHILD_FAIL_DOT = """\
digraph child_fail {
    graph [goal="always-failing child pipeline"]
    start [shape=Mdiamond]
    fail_work [shape=parallelogram, tool_command="exit 1"]
    done [shape=Msquare]
    start -> fail_work
}
"""


def _write_child_fail_dot(tmp_path) -> str:
    """Write the always-failing child DOT to a temp file and return the path."""
    dot_file = tmp_path / "child_fail.dot"
    dot_file.write_text(_CHILD_FAIL_DOT)
    return str(dot_file)


# ---------------------------------------------------------------------------
# Engine factory — mirrors _make_engine() in test_failure_routing.py exactly.
# ---------------------------------------------------------------------------


def _make_engine(
    graph: Graph,
    backend: object | None = None,
    logs_root: str = "/tmp/test-folder-fail-routing",
    hooks: object | None = None,
) -> PipelineEngine:
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestFolderNodeFailEdgeRouting:
    """condition="outcome=fail" edge on the folder node fires when the child fails."""

    @pytest.mark.asyncio
    async def test_folder_fail_routes_via_outcome_fail_edge_not_retry_target(
        self, tmp_path
    ):
        """condition="outcome=fail" edge on a folder node fires when the child fails.

        When a shape=folder node's child pipeline terminates with FAIL and the
        parent graph has a condition="outcome=fail" edge on that node, the parent
        engine must route via the explicit edge — NOT via graph-level retry_target.

        This is the primary untested gap: the failure-routing chain must check
        condition-matched edges before falling to the retry_target cascade.

        Graph:
            start -> reality_check(folder) -[outcome=fail]-> verdict_gate
                                           -[outcome=success]-> good_path
            graph_attrs = {retry_target: "implement_trap"}

        Expected: verdict_gate reached; implement_trap never entered.
        Bug scenario (engine fell to graph retry_target): implement_trap entered.
        """
        child_dot = _write_child_fail_dot(tmp_path)

        graph = Graph(
            name="parent_test1",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "reality_check": Node(
                    id="reality_check", shape="folder", attrs={"dot_file": child_dot}
                ),
                "verdict_gate": Node(id="verdict_gate", prompt="verdict gate"),
                "good_path": Node(id="good_path", prompt="good path — child succeeded"),
                "implement_trap": Node(
                    id="implement_trap", prompt="TRAP — must never run"
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="reality_check"),
                Edge(
                    from_node="reality_check",
                    to_node="verdict_gate",
                    condition="outcome=fail",
                ),
                Edge(
                    from_node="reality_check",
                    to_node="good_path",
                    condition="outcome=success",
                ),
                Edge(from_node="verdict_gate", to_node="exit"),
                Edge(from_node="good_path", to_node="exit"),
                Edge(from_node="implement_trap", to_node="exit"),
            ],
            graph_attrs={"retry_target": "implement_trap"},
            source_dir=str(tmp_path),
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs1"))
        engine_outcome = await engine.run()

        # Primary assertion: condition="outcome=fail" edge must have fired.
        assert backend.call_count("verdict_gate") >= 1, (
            f"verdict_gate NOT reached — condition=outcome=fail edge did not fire. "
            f"verdict_gate={backend.call_count('verdict_gate')}, "
            f"implement_trap={backend.call_count('implement_trap')}, "
            f"engine status={engine_outcome.status}, "
            f"reason={engine_outcome.failure_reason!r}"
        )

        # Guard: graph-level retry_target must NOT have fired.
        assert backend.call_count("implement_trap") == 0, (
            f"ROUTING BUG: implement_trap entered {backend.call_count('implement_trap')} "
            f"time(s) — engine fell to graph retry_target instead of following the "
            f"condition=outcome=fail edge on the folder node."
        )

        # Sanity: good_path must not be reached (child failed, not succeeded).
        assert backend.call_count("good_path") == 0, (
            f"good_path must not be reached when child fails "
            f"(count={backend.call_count('good_path')})"
        )


class TestFolderNodeRetryTargetOverride:
    """Node-level retry_target on a folder node beats graph-level retry_target."""

    @pytest.mark.asyncio
    async def test_folder_fail_node_retry_target_overrides_graph_retry_target(
        self, tmp_path
    ):
        """Node-level retry_target on a folder node takes priority over graph-level.

        When a shape=folder node carries retry_target="verdict_gate" (node attr) and
        the parent graph has retry_target="implement_trap" (graph attr), the failure
        fallback chain must honour the node-level target first per the chain defined
        in test_failure_routing.py: node.retry_target > graph.retry_target.

        No condition="outcome=fail" edge is needed — the attr alone routes the failure.

        Graph:
            start -> reality_check(folder, attrs={retry_target: "verdict_gate"})
            [no condition="outcome=fail" edge on reality_check]
            graph_attrs = {retry_target: "implement_trap"}

        Expected: verdict_gate reached via node-level retry_target;
                  implement_trap never entered.
        """
        child_dot = _write_child_fail_dot(tmp_path)

        graph = Graph(
            name="parent_test2",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "reality_check": Node(
                    id="reality_check",
                    shape="folder",
                    attrs={
                        "dot_file": child_dot,
                        "retry_target": "verdict_gate",
                    },
                ),
                "verdict_gate": Node(id="verdict_gate", prompt="verdict gate"),
                "implement_trap": Node(
                    id="implement_trap", prompt="TRAP — must never run"
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="reality_check"),
                # No condition="outcome=fail" edge from reality_check.
                Edge(from_node="verdict_gate", to_node="exit"),
                Edge(from_node="implement_trap", to_node="exit"),
            ],
            graph_attrs={"retry_target": "implement_trap"},
            source_dir=str(tmp_path),
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs2"))
        engine_outcome = await engine.run()

        assert backend.call_count("verdict_gate") >= 1, (
            f"verdict_gate NOT reached — node-level retry_target did not override "
            f"graph-level retry_target. "
            f"verdict_gate={backend.call_count('verdict_gate')}, "
            f"implement_trap={backend.call_count('implement_trap')}, "
            f"engine status={engine_outcome.status}, "
            f"reason={engine_outcome.failure_reason!r}"
        )
        assert backend.call_count("implement_trap") == 0, (
            f"ROUTING BUG: implement_trap entered {backend.call_count('implement_trap')} "
            f"time(s) — graph-level retry_target overrode node-level retry_target "
            f"(wrong priority in fallback chain)."
        )


class TestFolderNodeBaselineRouting:
    """Baseline: graph-level retry_target fires when no other routing applies."""

    @pytest.mark.asyncio
    async def test_baseline_graph_retry_target_fires_for_folder_fail(self, tmp_path):
        """Graph-level retry_target fires when a folder child fails with no other routing.

        Without a condition="outcome=fail" edge or node-level retry_target on the
        folder node, the parent engine falls through to the graph-level retry_target
        when the child pipeline terminates with FAIL.

        This confirms the default/baseline behavior for folder-node failures and
        establishes the anchor for the override tests above: the engine DOES reach
        the graph retry_target under these conditions, so tests 1 and 2 above are
        genuinely testing that their respective mechanisms suppress that fallback.

        Graph:
            start -> reality_check(folder)  [no fail edge, no node retry_target]
            graph_attrs = {retry_target: "implement_trap"}

        Expected: implement_trap IS entered.
        If this test fails, engine baseline behavior has changed — re-evaluate
        whether tests 1 and 2 above still test the right thing.
        """
        child_dot = _write_child_fail_dot(tmp_path)

        graph = Graph(
            name="parent_test3",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "reality_check": Node(
                    id="reality_check", shape="folder", attrs={"dot_file": child_dot}
                ),
                "implement_trap": Node(id="implement_trap", prompt="implement trap"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="reality_check"),
                # No condition="outcome=fail" edge, no node-level retry_target.
                Edge(from_node="implement_trap", to_node="exit"),
            ],
            graph_attrs={"retry_target": "implement_trap"},
            source_dir=str(tmp_path),
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs3"))
        engine_outcome = await engine.run()

        assert backend.call_count("implement_trap") >= 1, (
            f"implement_trap NOT entered — graph-level retry_target did not fire "
            f"for folder-node failure. "
            f"engine status={engine_outcome.status}, "
            f"reason={engine_outcome.failure_reason!r}. "
            f"Check whether the child DOT actually fails or engine behavior has changed."
        )
