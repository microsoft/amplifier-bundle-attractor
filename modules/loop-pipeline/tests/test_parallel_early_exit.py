"""Tests for parallel handler early-exit join policies (Fix 2.7).

Spec coverage: Section 4.8 — first_success and k_of_n join policies
should cancel remaining branches early when the policy is satisfied,
rather than waiting for all branches to complete.

For first_success: return as soon as one branch succeeds, cancel others.
For k_of_n: return when min_success branches succeed, cancel others.
"""

import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.parallel import ParallelHandler
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# first_success early-exit tests
# =====================================================================


class TestFirstSuccessEarlyExit:
    """first_success should cancel remaining branches when one succeeds."""

    @pytest.mark.asyncio
    async def test_first_success_does_not_wait_for_slow_branches(self):
        """first_success returns quickly when a fast branch succeeds.

        The slow branches should be cancelled rather than waited on.
        Verify by checking that the total execution time is much less
        than the slow branch duration.
        """
        completed_branches: list[str] = []

        class SlowFastEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id == "fast":
                    completed_branches.append(node_id)
                    return Outcome(status=StageStatus.SUCCESS, notes="fast done")
                # Slow branches take 5 seconds
                await asyncio.sleep(5.0)
                completed_branches.append(node_id)
                return Outcome(status=StageStatus.SUCCESS, notes="slow done")

        handler = ParallelHandler()
        _engine = SlowFastEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "fast": Node(id="fast", prompt="fast"),
                "slow1": Node(id="slow1", prompt="slow"),
                "slow2": Node(id="slow2", prompt="slow"),
            },
            edges=[
                Edge(from_node="parallel", to_node="fast"),
                Edge(from_node="parallel", to_node="slow1"),
                Edge(from_node="parallel", to_node="slow2"),
            ],
        )

        import time

        start = time.monotonic()
        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        elapsed = time.monotonic() - start

        assert outcome.status == StageStatus.SUCCESS
        # Should complete well under 5 seconds (the slow branch timeout)
        assert elapsed < 2.0, f"Took {elapsed:.1f}s — slow branches were not cancelled"

    @pytest.mark.asyncio
    async def test_first_success_returns_success_when_any_succeeds(self):
        """first_success returns SUCCESS even if some branches fail first."""
        call_count = 0

        class FirstSuccessEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                nonlocal call_count
                call_count += 1
                if node_id == "b1":
                    return Outcome(status=StageStatus.FAIL, failure_reason="b1 broke")
                return Outcome(status=StageStatus.SUCCESS, notes="b2 ok")

        _engine = FirstSuccessEngine()
        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

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
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_first_success_fails_when_all_fail(self):
        """first_success returns FAIL when no branches succeed."""

        class FailEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(
                    status=StageStatus.FAIL, failure_reason=f"{node_id} failed"
                )

        handler = ParallelHandler()
        _engine = FailEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "first_success"},
        )

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
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        assert outcome.status == StageStatus.FAIL


# =====================================================================
# k_of_n early-exit tests
# =====================================================================


class TestKOfNEarlyExit:
    """k_of_n should cancel remaining branches when threshold is met."""

    @pytest.mark.asyncio
    async def test_k_of_n_does_not_wait_for_slow_branches(self):
        """k_of_n returns early when min_success branches have succeeded.

        With k=2 and 2 fast successes + 1 slow branch, should not wait
        for the slow branch.
        """
        completed_branches: list[str] = []

        class KOfNFastEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id in ("fast1", "fast2"):
                    completed_branches.append(node_id)
                    return Outcome(status=StageStatus.SUCCESS, notes=f"{node_id} done")
                # Slow branch
                await asyncio.sleep(5.0)
                completed_branches.append(node_id)
                return Outcome(status=StageStatus.SUCCESS, notes="slow done")

        handler = ParallelHandler()
        _engine = KOfNFastEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "k_of_n", "min_success": "2"},
        )

        graph = _make_graph(
            nodes={
                "parallel": par_node,
                "fast1": Node(id="fast1", prompt="1"),
                "fast2": Node(id="fast2", prompt="2"),
                "slow": Node(id="slow", prompt="slow"),
            },
            edges=[
                Edge(from_node="parallel", to_node="fast1"),
                Edge(from_node="parallel", to_node="fast2"),
                Edge(from_node="parallel", to_node="slow"),
            ],
        )

        import time

        start = time.monotonic()
        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        elapsed = time.monotonic() - start

        assert outcome.status == StageStatus.SUCCESS
        # Should complete well under 5 seconds
        assert elapsed < 2.0, f"Took {elapsed:.1f}s — slow branch was not cancelled"

    @pytest.mark.asyncio
    async def test_k_of_n_waits_when_threshold_not_yet_met(self):
        """k_of_n waits for more branches when threshold not met yet."""

        class KOfNThresholdEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id == "b1":
                    return Outcome(status=StageStatus.FAIL, failure_reason="broke")
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _engine = KOfNThresholdEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "k_of_n", "min_success": "2"},
        )

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

        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_k_of_n_fails_when_threshold_impossible(self):
        """k_of_n fails when not enough remaining branches can meet threshold."""

        class ImpossibleThresholdEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id in ("b1", "b2"):
                    return Outcome(status=StageStatus.FAIL, failure_reason="broke")
                await asyncio.sleep(5.0)
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _engine = ImpossibleThresholdEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "k_of_n", "min_success": "2"},
        )

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

        import time

        start = time.monotonic()
        outcome = await handler.execute(
            par_node, _make_context(), graph, "/tmp", engine=_engine
        )
        elapsed = time.monotonic() - start

        assert outcome.status == StageStatus.FAIL
        # Should fail fast when threshold becomes impossible
        assert elapsed < 2.0, f"Took {elapsed:.1f}s — should fail fast"

    @pytest.mark.asyncio
    async def test_k_of_n_stores_results_in_context(self):
        """k_of_n stores collected results in parent context."""

        class StoreResultsEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.SUCCESS, notes=f"{node_id} ok")

        handler = ParallelHandler()
        _engine = StoreResultsEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "k_of_n", "min_success": "1"},
        )
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

        await handler.execute(par_node, context, graph, "/tmp", engine=_engine)
        results = context.get("parallel.results")
        assert results is not None
        assert len(results) >= 1
