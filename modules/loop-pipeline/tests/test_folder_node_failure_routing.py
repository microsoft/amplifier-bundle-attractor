"""Tests for folder-node failure routing per spec §3.7.

A shape=folder node runs a child sub-pipeline via PipelineHandler.  When
the child fails, PipelineHandler propagates FAIL verbatim.  The parent
engine then applies standard §3.7 routing:

  1. condition="outcome=fail" edge on the folder node (step 1 — cornerstone)
  2. node-level retry_target on the folder node (step 2)
  3. TERMINATE FAIL if neither — graph-level retry_target must NOT fire here

This supersedes the version proposed in PR #54.

Spec coverage: §3.7 (per-node failure routing), §4.11/§9.4 (subgraph semantics).
Regression lock for: fix/folder-failure-routing-conformance (graph-level drift).
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A minimal child pipeline that always fails.  shape=parallelogram uses
# ToolHandler; exit 1 → non-zero exit code → Outcome(status=FAIL).
# No outgoing edge from fail_step so the child engine terminates FAIL
# (FAIL outcomes do not traverse unconditional edges per §3.7 / edge_selection.py).
_CHILD_FAIL_DOT = """\
digraph child_fail {
    start [shape=Mdiamond]
    fail_step [shape=parallelogram, tool_command="exit 1"]
    done [shape=Msquare]
    start -> fail_step
}
"""


class CountingBackend:
    """Backend that tracks call count per node and returns configurable outcomes."""

    def __init__(self, outcomes: dict[str, list[Outcome | str]] | None = None):
        self._outcomes = outcomes or {}
        self._call_counts: dict[str, int] = {}

    async def run(self, node, prompt, context, incoming_edge=None, graph=None) -> str | Outcome:
        count = self._call_counts.get(node.id, 0)
        self._call_counts[node.id] = count + 1
        seq = self._outcomes.get(node.id, ["done"])
        if count < len(seq):
            return seq[count]
        return seq[-1]

    def call_count(self, node_id: str) -> int:
        return self._call_counts.get(node_id, 0)


def _make_engine(
    graph: Graph,
    backend: object | None = None,
    logs_root: str = "/tmp/test-folder-failure-routing",
) -> PipelineEngine:
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


# ---------------------------------------------------------------------------
# Case (a): KEEP from #54 — fail-edge on folder node fires (§3.7 step 1 cornerstone)
# ---------------------------------------------------------------------------


class TestFolderNodeFailEdgeFires:
    """§3.7 step 1 cornerstone: condition=\"outcome=fail\" edge routes folder failure."""

    @pytest.mark.asyncio
    async def test_folder_fail_edge_routes_to_handler_not_graph_target(self, tmp_path):
        """Folder child fails; condition=\"outcome=fail\" edge fires → handler, NOT graph_target.

        With graph.retry_target set but the folder node having a
        condition="outcome=fail" edge to handler, the fail-edge must take
        precedence (§3.7 step 1).  The graph_target node must never be entered.
        """
        child_dot_path = tmp_path / "child_fail.dot"
        child_dot_path.write_text(_CHILD_FAIL_DOT)

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "folder_node": Node(
                    id="folder_node",
                    shape="folder",
                    attrs={"dot_file": str(child_dot_path)},
                ),
                "handler": Node(id="handler", prompt="handle failure"),
                "graph_target": Node(
                    id="graph_target", prompt="graph retry — must NOT run"
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="folder_node"),
                # fail-edge: explicit opt-in per §3.7 step 1
                Edge(
                    from_node="folder_node",
                    to_node="handler",
                    condition="outcome=fail",
                ),
                Edge(from_node="handler", to_node="exit"),
                Edge(from_node="graph_target", to_node="exit"),
            ],
            graph_attrs={"retry_target": "graph_target"},
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs"))
        await engine.run()

        # fail-edge fires → handler must run
        assert backend.call_count("handler") >= 1, (
            "handler must run via the condition='outcome=fail' edge (§3.7 step 1)"
        )
        # graph_target must NEVER be entered — it's not in the §3.7 per-node path
        assert backend.call_count("graph_target") == 0, (
            "graph_target must NOT run — graph-level retry_target is goal-gate-exit "
            "only (§3.4), not consulted on per-node failure (§3.7)"
        )


# ---------------------------------------------------------------------------
# Case (b): NEW (replaces #54 baseline) — loud FAIL when no fail-edge and no
#           node-level retry, even with graph.retry_target present
# ---------------------------------------------------------------------------


class TestFolderNodeLoudFailWhenNoFailEdgeNoNodeRetry:
    """§3.7 step 4 regression lock: no fail-edge + no node-level retry → TERMINATE FAIL.

    graph.retry_target must NOT fire.  This is the core bug fixed by
    fix/folder-failure-routing-conformance — the silent loop-restart.
    """

    @pytest.mark.asyncio
    async def test_folder_no_fail_edge_with_graph_retry_target_terminates_fail(
        self, tmp_path
    ):
        """Folder fails; no fail-edge; no node-level retry_target; graph retry_target
        present → engine TERMINATES FAIL with child's failure reason.

        Regression lock: graph.retry_target must NOT enter the graph_target node
        and must NOT restart the loop.
        """
        child_dot_path = tmp_path / "child_fail.dot"
        child_dot_path.write_text(_CHILD_FAIL_DOT)

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "folder_node": Node(
                    id="folder_node",
                    shape="folder",
                    # No node-level retry_target
                    attrs={"dot_file": str(child_dot_path)},
                ),
                "graph_target": Node(
                    id="graph_target", prompt="graph retry — must NOT run"
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="folder_node"),
                # No fail-edge on folder_node
                Edge(from_node="graph_target", to_node="exit"),
            ],
            graph_attrs={"retry_target": "graph_target"},
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs"))
        outcome = await engine.run()

        # Engine must terminate FAIL — not silently route to graph_target
        assert outcome.status == StageStatus.FAIL, (
            "pipeline must TERMINATE FAIL loud — graph.retry_target must NOT fire "
            "on per-node failure (spec §3.7; graph-level is §3.4 goal-gate-exit only)"
        )
        # graph_target must NEVER be entered (regression lock for the drift)
        assert backend.call_count("graph_target") == 0, (
            "graph_target must NOT run — this was the silent-restart bug being fixed"
        )


# ---------------------------------------------------------------------------
# Case (c): node-level retry_target on folder node IS honored (§3.7 step 2)
# ---------------------------------------------------------------------------


class TestFolderNodeLevelRetryTargetHonored:
    """§3.7 step 2: node-level retry_target on a folder node fires even when
    graph-level retry_target also exists — proving graph-level is ignored on failure.
    """

    @pytest.mark.asyncio
    async def test_folder_node_level_retry_target_fires_not_graph_target(self, tmp_path):
        """Folder fails; node has retry_target; graph also has retry_target →
        node-level retry_target fires; graph-level is ignored.

        Proves both that node-level still works (§3.7 step 2) and that
        graph-level is not consulted (§3.4 scope boundary).
        """
        child_dot_path = tmp_path / "child_fail.dot"
        child_dot_path.write_text(_CHILD_FAIL_DOT)

        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "folder_node": Node(
                    id="folder_node",
                    shape="folder",
                    attrs={
                        "dot_file": str(child_dot_path),
                        "retry_target": "node_recovery",  # §3.7 step 2
                    },
                ),
                "node_recovery": Node(id="node_recovery", prompt="node-level recovery"),
                "graph_target": Node(
                    id="graph_target", prompt="graph retry — must NOT run"
                ),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="folder_node"),
                Edge(from_node="node_recovery", to_node="exit"),
                Edge(from_node="graph_target", to_node="exit"),
            ],
            graph_attrs={"retry_target": "graph_target"},
        )

        backend = CountingBackend()
        engine = _make_engine(graph, backend=backend, logs_root=str(tmp_path / "logs"))
        await engine.run()

        # node-level retry_target fires → node_recovery runs
        assert backend.call_count("node_recovery") >= 1, (
            "node_recovery must run — node.retry_target is §3.7 step 2"
        )
        # graph_target must NOT run — graph-level not consulted on per-node failure
        assert backend.call_count("graph_target") == 0, (
            "graph_target must NOT run — graph-level retry_target not consulted on "
            "per-node failure (§3.7); it is goal-gate-exit only (§3.4)"
        )
