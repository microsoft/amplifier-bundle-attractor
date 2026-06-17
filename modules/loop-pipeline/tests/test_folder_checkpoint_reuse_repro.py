"""FIX 3B — folder/subgraph checkpoint reuse across loop iterations.

After the fix (handlers/pipeline.py PipelineHandler.execute): the child logs
directory is namespaced per invocation of the folder node within a parent run.
Each loop iteration receives a FRESH checkpoint directory (e.g.
subgraph_{id}__iter1, subgraph_{id}__iter2, …), so the child engine cannot
find and resume the completed checkpoint from a previous iteration.

Strategy:
  Call PipelineHandler.execute() twice with the SAME stub engine and the same
  logs_root + node.id.  This directly mirrors the real loop scenario where the
  parent engine re-enters the folder node on every iteration.  A CountingBackend
  records backend.run() calls.  After both executions the worker node's call
  count must be 2 (one per iteration).

  The control test uses separate logs_root dirs per execution — verifies that
  the counting mechanism and child pipeline are correct independently of the fix.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.pipeline import PipelineHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Child DOT pipeline — one observable box (worker) between start and exit
# ---------------------------------------------------------------------------

_CHILD_DOT = """\
digraph child_work {
    start [shape=Mdiamond]
    worker [prompt="Do the work for this iteration"]
    done [shape=Msquare]
    start -> worker
    worker -> done
}
"""


# ---------------------------------------------------------------------------
# Counting backend — tracks backend.run() calls per node id
# ---------------------------------------------------------------------------


class CountingBackend:
    """Stub backend that counts run() calls per node and returns SUCCESS."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge: Any = None,
        graph: Any = None,
    ) -> Outcome:
        self.calls[node.id] = self.calls.get(node.id, 0) + 1
        return Outcome(
            status=StageStatus.SUCCESS,
            notes=f"{node.id} ran (count {self.calls[node.id]})",
        )

    def n(self, node_id: str) -> int:
        return self.calls.get(node_id, 0)


# ---------------------------------------------------------------------------
# Minimal parent graph (needed by PipelineHandler.execute signature)
# ---------------------------------------------------------------------------


def _make_parent_graph(folder_node: Node) -> Graph:
    """Build a minimal parent graph containing only the folder node."""
    start = Node(id="start", shape="Mdiamond")
    exit_ = Node(id="exit", shape="Msquare")
    return Graph(
        name="parent",
        nodes={
            "start": start,
            "folder_check": folder_node,
            "exit": exit_,
        },
        edges=[
            Edge(from_node="start", to_node="folder_check"),
            Edge(from_node="folder_check", to_node="exit"),
        ],
    )


# ---------------------------------------------------------------------------
# Stub engine — mirrors what the real PipelineEngine provides to execute()
#
# In production, PipelineHandler.execute() receives the parent engine and
# calls engine.handler_registry.get_backend() to obtain the backend for the
# child HandlerRegistry.  The fix also lazily attaches
# engine._folder_invocation_counts on the engine so that the per-node counter
# persists across loop iterations within one parent run.
# ---------------------------------------------------------------------------


class _StubRegistry:
    """Minimal HandlerRegistry stub: exposes get_backend()."""

    def __init__(self, backend: CountingBackend) -> None:
        self._backend = backend

    def get_backend(self) -> CountingBackend:
        return self._backend


class _StubEngine:
    """Minimal engine stub that satisfies the requirements of execute().

    - Provides handler_registry.get_backend() so the child HandlerRegistry can
      be seeded with the shared CountingBackend.
    - Has NO _folder_invocation_counts attribute initially; the handler adds it
      lazily on first execute() call (via hasattr guard in pipeline.py).
    - The SAME instance is passed to both execute() calls, mirroring the real
      loop where the parent engine object is identical across iterations.
    """

    def __init__(self, backend: CountingBackend) -> None:
        self.handler_registry = _StubRegistry(backend)


# ---------------------------------------------------------------------------
# FIX 3B regression test — same engine, same logs_root, same node.id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_folder_checkpoint_reuse_across_runs(tmp_path):
    """FIX 3B: each loop iteration of a folder node runs the child pipeline fresh.

    FIXED BEHAVIOR:
        PipelineHandler.execute() now tracks an invocation counter keyed on
        node.id on the parent engine object.

        * Invocation 0 → child_logs = logs_root/subgraph_{node.id}
        * Invocation 1 → child_logs = logs_root/subgraph_{node.id}__iter1

        Because each iteration writes to a different directory, the child
        engine started for iteration 1 finds no checkpoint and executes the
        child pipeline from scratch.  The worker node is called twice in total.

    This test mirrors the real loop scenario: the SAME stub engine (and the
    same logs_root + node.id) is passed to both execute() calls, exactly as the
    parent PipelineEngine would behave when visiting the folder node on
    successive iterations.
    """
    # Write child DOT to a temp file
    child_dot_path = tmp_path / "child_work.dot"
    child_dot_path.write_text(_CHILD_DOT)

    # Shared counting backend — persists across both handler invocations
    backend = CountingBackend()

    # The folder node — same node.id used in both calls
    folder_node = Node(
        id="folder_check",
        shape="folder",
        attrs={"dot_file": str(child_dot_path)},
    )
    parent_graph = _make_parent_graph(folder_node)
    parent_graph.source_dir = str(tmp_path)

    # Stub engine — same object passed to both execute() calls (the real loop)
    stub_engine = _StubEngine(backend)

    handler = PipelineHandler(backend=backend)

    # ── Run 1 (iteration 0) ──────────────────────────────────────────────────
    ctx1 = PipelineContext()
    outcome1 = await handler.execute(
        folder_node, ctx1, parent_graph, str(tmp_path), engine=stub_engine
    )
    worker_after_run1 = backend.n("worker")

    assert outcome1.status == StageStatus.SUCCESS, (
        f"Run 1 expected SUCCESS, got {outcome1.status!r} "
        f"(failure_reason: {outcome1.failure_reason!r})"
    )
    assert worker_after_run1 == 1, (
        f"Run 1: worker should have been called once, got {worker_after_run1}"
    )

    # Verify first-iteration checkpoint is in the canonical (back-compat) dir
    child_logs_iter0 = os.path.join(str(tmp_path), f"subgraph_{folder_node.id}")
    assert os.path.isdir(child_logs_iter0), (
        f"Iteration-0 child logs dir not created: {child_logs_iter0}"
    )
    assert os.path.isfile(os.path.join(child_logs_iter0, "checkpoint.json")), (
        f"Checkpoint not written after iteration 0: {child_logs_iter0}/checkpoint.json"
    )

    # ── Run 2 (iteration 1) ──────────────────────────────────────────────────
    ctx2 = PipelineContext()
    outcome2 = await handler.execute(
        folder_node, ctx2, parent_graph, str(tmp_path), engine=stub_engine
    )
    worker_after_run2 = backend.n("worker")

    assert outcome2.status == StageStatus.SUCCESS, (
        f"Run 2 expected SUCCESS, got {outcome2.status!r} "
        f"(failure_reason: {outcome2.failure_reason!r})"
    )

    # Core assertion: worker ran on BOTH iterations — fix confirmed
    assert worker_after_run2 == 2, (
        f"FIX 3B REGRESSION: worker ran only {worker_after_run2} time(s) across "
        "two iterations.  The second execution likely reused the stale checkpoint "
        "from iteration 0 and exited without re-executing the child pipeline.\n\n"
        "Expected worker call count: 2 (once per execute() call)\n"
        f"Actual worker call count:   {worker_after_run2}"
    )

    # Verify second-iteration checkpoint is in the namespaced dir
    child_logs_iter1 = os.path.join(str(tmp_path), f"subgraph_{folder_node.id}__iter1")
    assert os.path.isdir(child_logs_iter1), (
        f"Iteration-1 child logs dir not created: {child_logs_iter1}.  "
        "FIX 3B did not namespace the child_logs dir for the second invocation."
    )


@pytest.mark.asyncio
async def test_folder_checkpoint_reuse_different_logs_root_runs_twice(tmp_path):
    """Control test: separate logs_root per run → worker executes twice.

    This verifies that the counting mechanism and child pipeline itself are
    correct.  With distinct logs_root directories the checkpoint from run 1
    cannot be found by run 2, so both runs must execute worker independently.
    """
    child_dot_path = tmp_path / "child_work.dot"
    child_dot_path.write_text(_CHILD_DOT)

    backend = CountingBackend()
    folder_node = Node(
        id="folder_check",
        shape="folder",
        attrs={"dot_file": str(child_dot_path)},
    )
    parent_graph = _make_parent_graph(folder_node)
    parent_graph.source_dir = str(tmp_path)

    handler = PipelineHandler(backend=backend)

    # Run 1 with logs_root_1
    logs_root_1 = tmp_path / "run1"
    logs_root_1.mkdir()
    outcome1 = await handler.execute(
        folder_node, PipelineContext(), parent_graph, str(logs_root_1), engine=None
    )
    assert outcome1.status == StageStatus.SUCCESS, f"Control run 1 failed: {outcome1!r}"

    # Run 2 with logs_root_2 (no shared checkpoint)
    logs_root_2 = tmp_path / "run2"
    logs_root_2.mkdir()
    outcome2 = await handler.execute(
        folder_node, PipelineContext(), parent_graph, str(logs_root_2), engine=None
    )
    assert outcome2.status == StageStatus.SUCCESS, f"Control run 2 failed: {outcome2!r}"

    worker_count = backend.n("worker")
    assert worker_count == 2, (
        f"Control test failed: expected worker to run twice (distinct logs_root), "
        f"got count={worker_count}.  The child pipeline or counting backend is broken."
    )
