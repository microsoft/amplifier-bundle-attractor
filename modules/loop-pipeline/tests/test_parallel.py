"""Tests for parallel and fan-in handlers.

Spec coverage: PAR-001–013, FANIN-001–005, CONC-001–004,
Sections 4.8 (Parallel Handler) and 4.9 (Fan-In Handler).
"""

import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.fan_in import FanInHandler
from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


def _make_graph(
    nodes: dict[str, Node] | None = None,
    edges: list[Edge] | None = None,
    **kwargs,
) -> Graph:
    return Graph(
        name="test",
        nodes=nodes or {"start": Node(id="start", shape="Mdiamond")},
        edges=edges or [],
        **kwargs,
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# =====================================================================
# ParallelHandler tests
# =====================================================================


class FakeSubgraphRunner:
    """Simulates executing a subgraph from a target node.

    Returns configurable outcomes per node and tracks execution.
    """

    def __init__(self, outcomes: dict[str, Outcome] | None = None, delay: float = 0):
        self._outcomes = outcomes or {}
        self._delay = delay
        self.calls: list[str] = []
        self._default_outcome = Outcome(
            status=StageStatus.SUCCESS, notes="Default success"
        )

    async def run_subgraph(
        self,
        node_id: str,
        *,
        context: PipelineContext | None = None,
        emit_node_events: bool = True,
    ) -> Outcome:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        self.calls.append(node_id)
        return self._outcomes.get(node_id, self._default_outcome)


@pytest.mark.asyncio
async def test_parallel_fans_out_to_all_branches():
    """Parallel handler executes all outgoing edges concurrently."""
    runner = FakeSubgraphRunner()
    handler = ParallelHandler()

    par_node = Node(id="parallel", shape="component")
    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "branch_a": Node(id="branch_a", prompt="A"),
            "branch_b": Node(id="branch_b", prompt="B"),
            "branch_c": Node(id="branch_c", prompt="C"),
        },
        edges=[
            Edge(from_node="parallel", to_node="branch_a"),
            Edge(from_node="parallel", to_node="branch_b"),
            Edge(from_node="parallel", to_node="branch_c"),
        ],
    )

    outcome = await handler.execute(
        par_node, _make_context(), graph, "/tmp", engine=runner
    )
    assert outcome.is_success
    assert sorted(runner.calls) == ["branch_a", "branch_b", "branch_c"]


@pytest.mark.asyncio
async def test_parallel_clones_context_per_branch():
    """Each branch gets an isolated context clone."""
    branch_contexts: dict[str, PipelineContext] = {}

    class CapturingEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            branch_contexts[node_id] = context
            if context is not None:
                context.set(f"branch.{node_id}", "was_here")
            return Outcome(status=StageStatus.SUCCESS)

    capturing_engine = CapturingEngine()
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component")
    parent_context = _make_context()
    parent_context.set("shared_key", "shared_value")

    graph = _make_graph(
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

    await handler.execute(
        par_node, parent_context, graph, "/tmp", engine=capturing_engine
    )

    # Each branch should have gotten the shared_key
    assert branch_contexts["branch_a"].get("shared_key") == "shared_value"
    assert branch_contexts["branch_b"].get("shared_key") == "shared_value"

    # Branches should be isolated from each other
    assert branch_contexts["branch_a"].get("branch.branch_b") is None
    assert branch_contexts["branch_b"].get("branch.branch_a") is None

    # Parent context should not see branch writes
    assert parent_context.get("branch.branch_a") is None


@pytest.mark.asyncio
async def test_parallel_respects_max_parallel():
    """Bounded parallelism limits concurrent branch execution."""
    concurrency_tracker: list[int] = []
    current_concurrent = 0
    lock = asyncio.Lock()

    class TrackingEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            nonlocal current_concurrent
            async with lock:
                current_concurrent += 1
                concurrency_tracker.append(current_concurrent)
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            return Outcome(status=StageStatus.SUCCESS)

    tracking_engine = TrackingEngine()
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component", attrs={"max_parallel": "2"})

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
            "b3": Node(id="b3", prompt="3"),
            "b4": Node(id="b4", prompt="4"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
            Edge(from_node="parallel", to_node="b3"),
            Edge(from_node="parallel", to_node="b4"),
        ],
    )

    outcome = await handler.execute(
        par_node, _make_context(), graph, "/tmp", engine=tracking_engine
    )
    assert outcome.is_success
    # Max concurrent should never exceed 2
    assert max(concurrency_tracker) <= 2


@pytest.mark.asyncio
async def test_parallel_wait_all_all_success():
    """wait_all policy: all success → SUCCESS."""
    runner = FakeSubgraphRunner(
        outcomes={
            "b1": Outcome(status=StageStatus.SUCCESS),
            "b2": Outcome(status=StageStatus.SUCCESS),
        }
    )
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component", attrs={"join_policy": "wait_all"})

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
        ],
    )

    outcome = await handler.execute(
        par_node, _make_context(), graph, "/tmp", engine=runner
    )
    assert outcome.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_parallel_wait_all_with_failure():
    """wait_all policy: any failure → PARTIAL_SUCCESS (per spec)."""
    runner = FakeSubgraphRunner(
        outcomes={
            "b1": Outcome(status=StageStatus.SUCCESS),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
        }
    )
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component", attrs={"join_policy": "wait_all"})

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
        ],
    )

    outcome = await handler.execute(
        par_node, _make_context(), graph, "/tmp", engine=runner
    )
    assert outcome.status == StageStatus.PARTIAL_SUCCESS


@pytest.mark.asyncio
async def test_parallel_stores_results_in_context():
    """Parallel results are stored in context for downstream fan-in."""
    runner = FakeSubgraphRunner(
        outcomes={
            "b1": Outcome(status=StageStatus.SUCCESS, notes="B1 done"),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="B2 broken"),
        }
    )
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component")
    context = _make_context()

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
        ],
    )

    await handler.execute(par_node, context, graph, "/tmp", engine=runner)

    # Results should be stored in context
    results = context.get("parallel.results")
    assert results is not None
    assert isinstance(results, list)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_parallel_no_branches_returns_success():
    """Parallel node with no outgoing edges returns SUCCESS."""
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component")

    graph = _make_graph(
        nodes={"parallel": par_node},
        edges=[],
    )

    # No engine needed: no branches execute, so engine.run_subgraph is never called
    outcome = await handler.execute(par_node, _make_context(), graph, "/tmp")
    assert outcome.status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_parallel_continue_error_policy():
    """continue error policy: collects all results even on failures."""
    runner = FakeSubgraphRunner(
        outcomes={
            "b1": Outcome(status=StageStatus.FAIL, failure_reason="fail 1"),
            "b2": Outcome(status=StageStatus.SUCCESS),
            "b3": Outcome(status=StageStatus.FAIL, failure_reason="fail 3"),
        }
    )
    handler = ParallelHandler()
    par_node = Node(
        id="parallel",
        shape="component",
        attrs={"error_policy": "continue"},
    )
    context = _make_context()

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
            "b3": Node(id="b3", prompt="3"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
            Edge(from_node="parallel", to_node="b3"),
        ],
    )

    await handler.execute(par_node, context, graph, "/tmp", engine=runner)
    # All branches were executed
    assert sorted(runner.calls) == ["b1", "b2", "b3"]
    # Results stored
    results = context.get("parallel.results")
    assert len(results) == 3


@pytest.mark.asyncio
async def test_parallel_exception_in_branch_becomes_fail():
    """Exception in a branch is caught and converted to FAIL outcome."""

    class FailingEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            if node_id == "b2":
                raise RuntimeError("Branch crashed")
            return Outcome(status=StageStatus.SUCCESS)

    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component")

    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "b1": Node(id="b1", prompt="1"),
            "b2": Node(id="b2", prompt="2"),
        },
        edges=[
            Edge(from_node="parallel", to_node="b1"),
            Edge(from_node="parallel", to_node="b2"),
        ],
    )
    context = _make_context()

    outcome = await handler.execute(
        par_node, context, graph, "/tmp", engine=FailingEngine()
    )
    # Should still complete (continue policy)
    assert outcome.status == StageStatus.PARTIAL_SUCCESS
    results = context.get("parallel.results")
    assert len(results) == 2


# =====================================================================
# FanInHandler tests
# =====================================================================


@pytest.mark.asyncio
async def test_parallel_handler_emits_events():
    """ParallelHandler must emit parallel lifecycle events."""
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_PARALLEL_BRANCH_COMPLETED,
        PIPELINE_PARALLEL_BRANCH_STARTED,
        PIPELINE_PARALLEL_COMPLETED,
        PIPELINE_PARALLEL_STARTED,
    )

    emitted: list[tuple[str, dict]] = []

    class MockHooks:
        async def emit(self, event_name, data):
            emitted.append((event_name, data))

    class MockEngine:
        async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
            return Outcome(status=StageStatus.SUCCESS, notes="ok")

    handler = ParallelHandler(hooks=MockHooks())

    graph = _make_graph(
        nodes={
            "par": Node(id="par", shape="component"),
            "b1": Node(id="b1", shape="box"),
            "b2": Node(id="b2", shape="box"),
        },
        edges=[
            Edge(from_node="par", to_node="b1"),
            Edge(from_node="par", to_node="b2"),
        ],
    )

    ctx = _make_context()
    await handler.execute(
        graph.nodes["par"], ctx, graph, "/tmp/test", engine=MockEngine()
    )

    event_names = [e[0] for e in emitted]
    assert PIPELINE_PARALLEL_STARTED in event_names
    assert PIPELINE_PARALLEL_BRANCH_STARTED in event_names
    assert PIPELINE_PARALLEL_BRANCH_COMPLETED in event_names
    assert PIPELINE_PARALLEL_COMPLETED in event_names


@pytest.mark.asyncio
async def test_fan_in_selects_best_by_status():
    """Fan-in selects candidate with best status (SUCCESS > FAIL)."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "fail", "notes": "broken"},
            {"node_id": "b2", "status": "success", "notes": "good"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.is_success
    assert context.get("parallel.fan_in.best_id") == "b2"


@pytest.mark.asyncio
async def test_fan_in_selects_success_over_partial():
    """Fan-in prefers SUCCESS over PARTIAL_SUCCESS."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "partial_success", "notes": "partial"},
            {"node_id": "b2", "status": "success", "notes": "full"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.is_success
    assert context.get("parallel.fan_in.best_id") == "b2"


@pytest.mark.asyncio
async def test_fan_in_no_results_returns_fail():
    """Fan-in with no parallel results returns FAIL."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.status == StageStatus.FAIL
    assert "no parallel results" in (outcome.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_fan_in_empty_results_returns_fail():
    """Fan-in with empty results list returns FAIL."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set("parallel.results", [])

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_fan_in_all_fail_returns_fail():
    """Fan-in returns FAIL when all candidates failed."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "fail", "notes": "broken 1"},
            {"node_id": "b2", "status": "fail", "notes": "broken 2"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.status == StageStatus.FAIL


@pytest.mark.asyncio
async def test_fan_in_records_best_outcome():
    """Fan-in stores the best outcome status in context."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "success", "notes": "winner"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")
    assert outcome.is_success
    assert context.get("parallel.fan_in.best_id") == "b1"
    assert context.get("parallel.fan_in.best_status") == "success"


@pytest.mark.asyncio
async def test_fan_in_tiebreak_by_node_id():
    """Fan-in breaks ties between equal-status candidates by node ID."""
    handler = FanInHandler()
    node = Node(id="fan_in", shape="tripleoctagon")
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b2", "status": "success", "notes": "b2"},
            {"node_id": "b1", "status": "success", "notes": "b1"},
        ],
    )

    await handler.execute(node, context, _make_graph(), "/tmp")
    # b1 comes first lexicographically
    assert context.get("parallel.fan_in.best_id") == "b1"


# --- M-17: Fan-in LLM-based evaluation ---


@pytest.mark.asyncio
async def test_fan_in_uses_backend_when_prompt_and_backend_available():
    """M-17: When node has prompt and backend, use LLM to evaluate results."""

    class MockFanInBackend:
        """Mock backend that returns the node_id of the 'best' candidate."""

        def __init__(self, pick_id: str):
            self._pick_id = pick_id
            self.called = False
            self.prompt_received = ""
            self.results_received: list = []

        async def evaluate(self, prompt: str, results: list[dict], node: Node) -> str:
            self.called = True
            self.prompt_received = prompt
            self.results_received = results
            return self._pick_id

    backend = MockFanInBackend(pick_id="b2")
    handler = FanInHandler(backend=backend)
    node = Node(
        id="fan_in",
        shape="tripleoctagon",
        prompt="Pick the best implementation based on code quality",
    )
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "success", "notes": "quick and dirty"},
            {"node_id": "b2", "status": "partial_success", "notes": "clean code"},
        ],
    )

    await handler.execute(node, context, _make_graph(), "/tmp")

    # Backend should have been called
    assert backend.called
    assert "code quality" in backend.prompt_received
    assert len(backend.results_received) == 2
    # Backend picked b2 despite it being partial_success (heuristic would pick b1)
    assert context.get("parallel.fan_in.best_id") == "b2"


@pytest.mark.asyncio
async def test_fan_in_falls_back_to_heuristic_without_backend():
    """M-17: Without backend, fan-in uses heuristic even with prompt."""
    handler = FanInHandler()  # no backend
    node = Node(
        id="fan_in",
        shape="tripleoctagon",
        prompt="Pick the best implementation",
    )
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "partial_success", "notes": "partial"},
            {"node_id": "b2", "status": "success", "notes": "full"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")

    # Without backend, heuristic picks b2 (success > partial_success)
    assert outcome.is_success
    assert context.get("parallel.fan_in.best_id") == "b2"


@pytest.mark.asyncio
async def test_fan_in_falls_back_to_heuristic_without_prompt():
    """M-17: Without prompt, fan-in uses heuristic even with backend."""

    class MockFanInBackend:
        async def evaluate(self, prompt, results, node):
            raise AssertionError("Backend should not be called without prompt")

    handler = FanInHandler(backend=MockFanInBackend())
    node = Node(id="fan_in", shape="tripleoctagon")  # no prompt
    context = _make_context()
    context.set(
        "parallel.results",
        [
            {"node_id": "b1", "status": "success", "notes": "good"},
        ],
    )

    outcome = await handler.execute(node, context, _make_graph(), "/tmp")

    assert outcome.is_success
    assert context.get("parallel.fan_in.best_id") == "b1"


# --- Task 3: ParallelHandler returns FAIL when no engine ---


@pytest.mark.asyncio
async def test_parallel_no_runner_returns_fail():
    """ParallelHandler without engine returns FAIL outcome — no silent simulation."""
    handler = ParallelHandler()
    par_node = Node(
        id="parallel",
        shape="component",
        attrs={"join_policy": "first_success"},
    )
    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "branch_a": Node(id="branch_a", prompt="A"),
        },
        edges=[Edge(from_node="parallel", to_node="branch_a")],
    )
    context = _make_context()
    # engine=None (default) → branches fail with "No engine configured"
    outcome = await handler.execute(par_node, context, graph, "/tmp")
    assert outcome.status == StageStatus.FAIL
    results = context.get("parallel.results") or []
    failure_text = " ".join(
        str(r.get("notes") or "") + " " + str(r.get("failure_reason") or "")
        for r in results
    )
    assert "engine" in failure_text


@pytest.mark.asyncio
async def test_parallel_no_runner_branch_returns_fail():
    """ParallelHandler without engine returns FAIL outcomes for branches — no silent simulation."""
    handler = ParallelHandler()
    par_node = Node(id="parallel", shape="component")
    graph = _make_graph(
        nodes={
            "parallel": par_node,
            "branch_a": Node(id="branch_a", prompt="A"),
        },
        edges=[Edge(from_node="parallel", to_node="branch_a")],
    )
    context = _make_context()
    # engine=None (default) → branches fail
    await handler.execute(par_node, context, graph, "/tmp")
    results = context.get("parallel.results")
    assert results is not None
    assert len(results) == 1
    assert results[0]["status"] == "fail"
    assert "engine" in (results[0]["notes"] or "")
