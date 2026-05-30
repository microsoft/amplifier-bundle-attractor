"""Tests for parallel-branch nested backend/session isolation.

TDD: These tests are written BEFORE the implementation.

Test group descriptions:
  G1  Core regression — component parallel (ParallelHandler) backend isolation
  G2  Three nested topologies inside a component branch
  G3  Multi-edge fan-out (_execute_parallel_fan_out) branch-engine isolation
  G4  Artifact store shared across branches (critic C1)
  G5  S5 guard — clone_for_branch engine raises on run()
  G6  Fan-in correctness on both fan-out paths (critic S3)

RED on current main (pre-fix): G1, G2a, G2b, G2c, G3, G5.
GREEN on baseline AND after fix: G4, G6a, G6b, regression tests.

Spec coverage: PAR-001–013, ISOL-001–005, NEST-001–003.

Implementation note on select_all_matching_edges:
  Multi-edge fan-out requires conditional edges (condition="outcome=success").
  Unconditional edges are not returned by select_all_matching_edges and do NOT
  trigger _execute_parallel_fan_out.  Tests that probe the multi-edge path
  must use edges with explicit conditions.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise


# ---------------------------------------------------------------------------
# Shared helpers / backends
# ---------------------------------------------------------------------------


class _TrackingBackend:
    """Records which instance handled each node call.

    Each branch gets its own clone after the fix, so id(self) differs per branch.
    On baseline (shared parent), id(self) is the same for all branches.
    """

    def __init__(self, label: str = "root") -> None:
        self.label = label
        self._session_pool: dict[str, str] = {}
        self._completed_nodes: dict[str, Any] = {}
        self.calls: list[tuple[str, int]] = []  # (node_id, id(self))

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        **_kwargs: Any,
    ) -> str:
        self.calls.append((node.id, id(self)))
        return "done"

    def clone(self) -> "_TrackingBackend":
        # Share the calls list so the root backend can observe all branch calls.
        # id(self) in each call still distinguishes which clone ran the node.
        cloned = _TrackingBackend(label=f"{self.label}@clone{id(self)}")
        cloned.calls = self.calls
        return cloned


_RECORDING_BACKEND_COUNTER: int = 0  # module-level monotone counter


class _RecordingBackend:
    """Writes (node_id → stable_uid) into a shared dict on every call.

    Uses a module-level monotone counter for each instance so that UIDs
    are guaranteed unique even when objects are garbage-collected and their
    memory addresses reused between sequential asyncio tasks.
    """

    def __init__(
        self,
        record: dict[str, int],
        label: str = "root",
        _uid: int | None = None,
    ) -> None:
        global _RECORDING_BACKEND_COUNTER
        self._record = record
        self.label = label
        self._session_pool: dict[str, str] = {}
        self._completed_nodes: dict[str, Any] = {}
        if _uid is None:
            _RECORDING_BACKEND_COUNTER += 1
            self._uid = _RECORDING_BACKEND_COUNTER
        else:
            self._uid = _uid

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        **_kwargs: Any,
    ) -> str:
        self._record[node.id] = self._uid
        return "done"

    def clone(self) -> "_RecordingBackend":
        global _RECORDING_BACKEND_COUNTER
        _RECORDING_BACKEND_COUNTER += 1
        uid = _RECORDING_BACKEND_COUNTER
        return _RecordingBackend(self._record, label=f"{self.label}@c{uid}", _uid=uid)


def _build_engine(dot_source: str, backend: Any, tmp_path: Any) -> PipelineEngine:
    """Parse DOT, validate, and build a PipelineEngine."""
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# G1 — Core regression: component parallel (ParallelHandler) backend isolation
# ---------------------------------------------------------------------------

_DOT_COMPONENT_PARALLEL = """\
digraph {
    start [shape=Mdiamond]
    par   [shape=component]
    work_a [prompt="Branch A codergen"]
    work_b [prompt="Branch B codergen"]
    fanin [shape=tripleoctagon]
    done  [shape=Msquare]

    start -> par
    par -> work_a
    par -> work_b
    work_a -> fanin
    work_b -> fanin
    fanin -> done
}
"""


@pytest.mark.asyncio
async def test_g1_component_parallel_codergen_backends_are_isolated(tmp_path):
    """G1 core: each component-parallel branch gets its own backend clone.

    RED on baseline: ParallelHandler calls engine.run_subgraph() on the parent
    → parent handler_registry → same CodergenHandler → same backend instance.

    GREEN after fix: each branch runs on clone_for_branch() engine → cloned
    registry → distinct backend instance.
    """
    record: dict[str, int] = {}
    backend = _RecordingBackend(record, label="root")

    engine = _build_engine(_DOT_COMPONENT_PARALLEL, backend, tmp_path)
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "work_a" in record, "work_a codergen must have run"
    assert "work_b" in record, "work_b codergen must have run"

    assert record["work_a"] != record["work_b"], (
        "Parallel branches must use DIFFERENT backend instances "
        "(current code shares the parent backend — that is the bug)"
    )


# ---------------------------------------------------------------------------
# G2 — Three nested topologies inside a component parallel branch
# ---------------------------------------------------------------------------

# --- G2a: Manager loop inside a component branch ---

_DOT_PARALLEL_WITH_MANAGER = """\
digraph {
    start [shape=Mdiamond]
    par   [shape=component]

    mgr_a [shape=house, "manager.max_cycles"="1", "manager.actions"="observe"]
    coder_a [prompt="Branch A nested work"]

    mgr_b [shape=house, "manager.max_cycles"="1", "manager.actions"="observe"]
    coder_b [prompt="Branch B nested work"]

    fanin [shape=tripleoctagon]
    done  [shape=Msquare]

    start -> par
    par -> mgr_a
    par -> mgr_b
    mgr_a -> coder_a -> fanin
    mgr_b -> coder_b -> fanin
    fanin -> done
}
"""


@pytest.mark.asyncio
async def test_g2a_manager_loop_inside_parallel_is_isolated(tmp_path):
    """G2a: manager-loop children run on isolated backends per branch.

    Manager loop calls engine.run_subgraph(child, engine=<passed engine>).
    Before fix: engine is the parent → shared backend.
    After fix: engine is the branch engine → isolated backend.
    """
    record: dict[str, int] = {}
    backend = _RecordingBackend(record, label="root")

    engine = _build_engine(_DOT_PARALLEL_WITH_MANAGER, backend, tmp_path)
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert "coder_a" in record, "coder_a (nested inside manager) must have run"
    assert "coder_b" in record, "coder_b (nested inside manager) must have run"

    assert record["coder_a"] != record["coder_b"], (
        "Nested codergen inside manager loop must use isolated backends per branch"
    )


# --- G2b: Folder (pipeline) node inside a component branch ---

_CHILD_DOT_A = """\
digraph child_a {
    start        [shape=Mdiamond]
    child_work_a [prompt="Child pipeline work A"]
    done         [shape=Msquare]
    start -> child_work_a -> done
}
"""

_CHILD_DOT_B = """\
digraph child_b {
    start        [shape=Mdiamond]
    child_work_b [prompt="Child pipeline work B"]
    done         [shape=Msquare]
    start -> child_work_b -> done
}
"""


@pytest.mark.asyncio
async def test_g2b_folder_inside_parallel_is_isolated(tmp_path):
    """G2b: folder-node (nested pipeline) runs on isolated backend per branch.

    PipelineHandler builds a child registry from self._backend (the captured
    original).  Before fix: SAME backend for both branches.
    After fix (Move 2): child registry is seeded from engine.handler_registry
    → branch-isolated backend.
    """
    child_a = str(tmp_path / "child_a.dot")
    child_b = str(tmp_path / "child_b.dot")
    with open(child_a, "w", encoding="utf-8") as f:
        f.write(_CHILD_DOT_A)
    with open(child_b, "w", encoding="utf-8") as f:
        f.write(_CHILD_DOT_B)

    dot_source = f"""\
digraph {{
    start    [shape=Mdiamond]
    par      [shape=component]
    folder_a [shape=folder, dot_file="{child_a}"]
    folder_b [shape=folder, dot_file="{child_b}"]
    fanin    [shape=tripleoctagon]
    done     [shape=Msquare]

    start -> par
    par -> folder_a
    par -> folder_b
    folder_a -> fanin
    folder_b -> fanin
    fanin -> done
}}
"""

    record: dict[str, int] = {}
    backend = _RecordingBackend(record, label="root")

    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    graph.source_dir = str(tmp_path)

    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    a_id = record.get("child_work_a")
    b_id = record.get("child_work_b")
    assert a_id is not None, "child_work_a (inside folder_a) must have run"
    assert b_id is not None, "child_work_b (inside folder_b) must have run"

    assert a_id != b_id, (
        "Folder nodes in parallel branches must run child pipelines "
        "with isolated (branch-clone) backends"
    )


# --- G2c: Nested fan-out inside a component branch ---

_DOT_NESTED_FANOUT = """\
digraph {
    start   [shape=Mdiamond]
    outer_par [shape=component]

    inner_par_a [shape=component]
    inner_a1 [prompt="Inner A branch 1"]
    inner_a2 [prompt="Inner A branch 2"]
    fanin_a  [shape=tripleoctagon]

    inner_par_b [shape=component]
    inner_b1 [prompt="Inner B branch 1"]
    inner_b2 [prompt="Inner B branch 2"]
    fanin_b  [shape=tripleoctagon]

    outer_fanin [shape=tripleoctagon]
    done [shape=Msquare]

    start -> outer_par
    outer_par -> inner_par_a
    outer_par -> inner_par_b

    inner_par_a -> inner_a1
    inner_par_a -> inner_a2
    inner_a1 -> fanin_a
    inner_a2 -> fanin_a

    inner_par_b -> inner_b1
    inner_par_b -> inner_b2
    inner_b1 -> fanin_b
    inner_b2 -> fanin_b

    fanin_a -> outer_fanin
    fanin_b -> outer_fanin
    outer_fanin -> done
}
"""


@pytest.mark.asyncio
async def test_g2c_nested_fanout_inside_parallel_is_isolated(tmp_path):
    """G2c: nested parallel fan-out inside a branch uses isolated backends.

    Before fix: inner branches share the outer-parent engine's handler_registry.
    After fix: each outer branch gets a branch engine; inner branches from that
    branch engine also get further branch engines (composition by construction).
    """
    record: dict[str, int] = {}
    backend = _RecordingBackend(record, label="root")

    engine = _build_engine(_DOT_NESTED_FANOUT, backend, tmp_path)
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)

    # Inner A branches must be isolated from inner B branches
    for inner_a in ("inner_a1", "inner_a2"):
        for inner_b in ("inner_b1", "inner_b2"):
            if inner_a in record and inner_b in record:
                assert record[inner_a] != record[inner_b], (
                    f"Nested branch '{inner_a}' and '{inner_b}' must use "
                    "different backend instances (outer branches must isolate)"
                )


# ---------------------------------------------------------------------------
# G3 — Multi-edge fan-out (_execute_parallel_fan_out) branch-engine isolation
#
# Key notes:
#   - select_all_matching_edges returns edges ONLY when e.condition is truthy
#     AND matches.  Unconditional edges do NOT trigger _execute_parallel_fan_out.
#     Edges must have condition="outcome=success" (or similar) to be selected.
#   - The bug: _execute_parallel_fan_out passes engine=self (parent) to
#     execute_with_retry.  A CapturingHandler records the received engine.
#     Before fix: both branches receive the SAME parent engine.
#     After fix: each branch receives a distinct clone_for_branch() engine.
# ---------------------------------------------------------------------------


class _CapturingHandler:
    """Records the engine object passed to each execute() call."""

    def __init__(self, captured: list[Any]) -> None:
        self._captured = captured

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: Any = None,
    ) -> Outcome:
        self._captured.append(engine)
        return Outcome(status=StageStatus.SUCCESS, notes=f"captured {node.id}")


def _make_multiedge_graph() -> Graph:
    """Graph with a codergen gate that fans out (condition=outcome=success)
    to two custom-type branch nodes, both converging on a codergen node.

    Structure:
        start -> gate (box/codergen)
        gate -> branch_c (condition="outcome=success")
        gate -> branch_d (condition="outcome=success")
        branch_c -> converge (box/codergen)
        branch_d -> converge
        converge -> done (exit)
    """
    return Graph(
        name="multiedge-isolation-test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "gate": Node(id="gate", shape="box", prompt="Gate triggers fan-out"),
            "branch_c": Node(id="branch_c", shape="box", type="capturing"),
            "branch_d": Node(id="branch_d", shape="box", type="capturing"),
            "converge": Node(id="converge", shape="box", prompt="Converge"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="gate"),
            Edge(from_node="gate", to_node="branch_c", condition="outcome=success"),
            Edge(from_node="gate", to_node="branch_d", condition="outcome=success"),
            Edge(from_node="branch_c", to_node="converge"),
            Edge(from_node="branch_d", to_node="converge"),
            Edge(from_node="converge", to_node="done"),
        ],
    )


@pytest.mark.asyncio
async def test_g3_multiedge_fanout_passes_branch_engine_to_handlers(tmp_path):
    """G3: _execute_parallel_fan_out must pass a branch engine to each handler.

    Before fix: execute_with_retry is called with engine=self (parent) for all
    branches → both capturing handlers see the SAME engine object.

    After fix: each branch gets clone_for_branch() → distinct branch engine →
    handlers see DIFFERENT engine objects per branch.
    """
    captured_engines: list[Any] = []

    backend = _TrackingBackend()  # needed for gate + converge codergen nodes
    graph = _make_multiedge_graph()

    registry = HandlerRegistry(HandlerContext(backend=backend))
    registry.register("capturing", _CapturingHandler(captured_engines))

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Multi-edge fan-out must complete: {outcome.failure_reason}"
    )

    assert len(captured_engines) == 2, (
        f"Both branch handlers must have executed; got {len(captured_engines)}"
    )

    # Before fix: both branches receive the parent engine → same object → FAILS here
    assert captured_engines[0] is not captured_engines[1], (
        "_execute_parallel_fan_out must pass a distinct branch engine to each "
        "handler, not the parent engine (pre-fix: same object for all branches)"
    )


# ---------------------------------------------------------------------------
# G4 — Artifact store shared across branches (critic C1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g4_artifact_store_shared_across_branches(tmp_path):
    """G4 (C1): clone_for_branch() must share the parent's ArtifactStore.

    After the fix, Engine.clone_for_branch() must explicitly set:
        clone.artifact_store = self.artifact_store

    This preserves:
    1. The L-12 asyncio.Lock (concurrent-write safety).
    2. Cross-branch artifact visibility (fan-in can read any branch's artifacts).

    GREEN on baseline (trivially — no branch engines exist pre-fix).
    Must STAY GREEN after fix.
    Regression: omitting the store-sharing line gives each branch its own
    store → fan-in reads miss other branches' artifacts.
    """

    class SimpleBranchBackend:
        def __init__(self) -> None:
            self._session_pool: dict[str, str] = {}
            self._completed_nodes: dict[str, Any] = {}

        async def run(
            self,
            node: Node,
            prompt: str,
            context: PipelineContext,
            **_kwargs: Any,
        ) -> str:
            return "done"

        def clone(self) -> "SimpleBranchBackend":
            return SimpleBranchBackend()

    backend = SimpleBranchBackend()
    graph = parse_dot(_DOT_COMPONENT_PARALLEL)
    validate_or_raise(graph)
    registry = HandlerRegistry(HandlerContext(backend=backend))
    parent_engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    parent_store_id = id(parent_engine.artifact_store)

    # If clone_for_branch exists (post-fix), verify the clone shares the parent store.
    clone_fn = getattr(parent_engine, "clone_for_branch", None)
    if clone_fn is not None:
        branch = clone_fn(context=PipelineContext())
        assert id(branch.artifact_store) == parent_store_id, (
            "clone_for_branch() must share the parent's ArtifactStore "
            "(C1: preserves L-12 lock and cross-branch visibility)"
        )

    outcome = await parent_engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)


# ---------------------------------------------------------------------------
# G5 — S5 guard: clone_for_branch engine raises on run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g5_branch_clone_engine_raises_on_run(tmp_path):
    """G5 (S5): calling run() on a branch-clone engine must raise RuntimeError.

    RED on baseline: Engine.clone_for_branch() does not exist → AttributeError.
    GREEN after fix: clone_for_branch() exists, run() guards on _is_branch_clone.
    """
    backend = _TrackingBackend()
    graph = parse_dot(_DOT_COMPONENT_PARALLEL)
    validate_or_raise(graph)
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    # AttributeError on baseline (clone_for_branch doesn't exist yet) → test RED.
    branch_engine = engine.clone_for_branch(context=PipelineContext())  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match=r"run\(\).*branch"):
        await branch_engine.run()


# ---------------------------------------------------------------------------
# G6 — Fan-in correctness on both fan-out paths (critic S3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g6a_component_parallel_fan_in_aggregates_results(tmp_path):
    """G6a (S3-component): branch results reach the fan-in node correctly.

    After the fix, parallel.results must be populated and the fan-in must
    succeed.  Regression: if branch results stop flowing to the parent context
    the fan-in gets no data and returns FAIL.
    """
    backend = _TrackingBackend()
    engine = _build_engine(_DOT_COMPONENT_PARALLEL, backend, tmp_path)
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Component parallel fan-in must aggregate results; got {outcome.status}"
    )
    results = engine.context.get("parallel.results")
    assert results is not None, "parallel.results must be set after component fan-out"
    assert len(results) == 2, f"Expected 2 branch results, got {len(results)}"


@pytest.mark.asyncio
async def test_g6b_multiedge_fanout_branch_outcomes_reach_parent(tmp_path):
    """G6b (S3-multiedge): branch top-node outcomes reach parent node_outcomes.

    _execute_parallel_fan_out records each branch outcome in
    self.node_outcomes[target_node_id].  After the fix (branch engine), this
    recording must still happen on the PARENT engine, not the branch engine.
    """
    captured_engines: list[Any] = []

    backend = _TrackingBackend()
    graph = _make_multiedge_graph()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    registry.register("capturing", _CapturingHandler(captured_engines))

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Multi-edge fan-out must complete: {outcome.failure_reason}"
    )

    # branch_c and branch_d are the top-level branch nodes
    assert "branch_c" in engine.node_outcomes, (
        "branch_c outcome must be in parent engine.node_outcomes (S3)"
    )
    assert "branch_d" in engine.node_outcomes, (
        "branch_d outcome must be in parent engine.node_outcomes (S3)"
    )
    assert engine.node_outcomes["branch_c"].status in (
        StageStatus.SUCCESS,
        StageStatus.PARTIAL_SUCCESS,
    )
    assert engine.node_outcomes["branch_d"].status in (
        StageStatus.SUCCESS,
        StageStatus.PARTIAL_SUCCESS,
    )


# ---------------------------------------------------------------------------
# Regression guard — existing behaviour must be unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_basic_linear_pipeline_unaffected(tmp_path):
    """Non-parallel pipelines must be unaffected by the fix."""
    backend = _TrackingBackend()
    engine = _build_engine(
        """\
digraph {
    start [shape=Mdiamond]
    work  [prompt="Sequential work"]
    done  [shape=Msquare]
    start -> work -> done
}
""",
        backend,
        tmp_path,
    )
    outcome = await engine.run()
    assert outcome.status == StageStatus.SUCCESS
    assert any(nid == "work" for nid, _ in backend.calls), "work must have run"


@pytest.mark.asyncio
async def test_regression_component_parallel_still_runs_all_branches(tmp_path):
    """Component parallel must still execute all branches after the fix."""
    backend = _TrackingBackend()
    engine = _build_engine(_DOT_COMPONENT_PARALLEL, backend, tmp_path)
    await engine.run()

    called_nodes = {nid for nid, _ in backend.calls}
    assert "work_a" in called_nodes, "work_a must run"
    assert "work_b" in called_nodes, "work_b must run"
