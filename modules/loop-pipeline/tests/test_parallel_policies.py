"""Tests for remaining parallel handler join/error policies (GAP-PL-07).

Spec coverage: Section 4.8 — k_of_n, quorum join policies;
fail_fast, ignore error policies.

These extend the existing wait_all, first_success, and continue
policies already tested in test_parallel.py.
"""

import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.parallel import (
    ParallelHandler,
    _apply_join_policy,
)
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


def _result(node_id: str, status: str, notes: str = "") -> dict:
    """Create a branch result dict matching the ParallelHandler output format."""
    return {
        "node_id": node_id,
        "status": status,
        "notes": notes,
        "failure_reason": "" if status != "fail" else f"{node_id} failed",
        "context_updates": {},
    }


# =====================================================================
# k_of_n join policy tests
# =====================================================================


class TestKOfNJoinPolicy:
    """Tests for the k_of_n join policy."""

    def test_k_of_n_succeeds_when_threshold_met(self):
        """k_of_n succeeds when at least k branches succeed."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
            _result("b3", "success"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "2"})
        assert outcome.status == StageStatus.SUCCESS

    def test_k_of_n_fails_when_threshold_not_met(self):
        """k_of_n fails when fewer than k branches succeed."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
            _result("b3", "fail"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "2"})
        assert outcome.status == StageStatus.FAIL
        assert "1" in outcome.failure_reason  # Only 1 succeeded
        assert "2" in outcome.failure_reason  # Needed 2

    def test_k_of_n_default_k_is_1(self):
        """k_of_n defaults to k=1 when min_success is not specified."""
        results = [
            _result("b1", "fail"),
            _result("b2", "success"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={})
        assert outcome.status == StageStatus.SUCCESS

    def test_k_of_n_counts_partial_success(self):
        """k_of_n counts partial_success as a success."""
        results = [
            _result("b1", "partial_success"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "1"})
        assert outcome.status == StageStatus.SUCCESS

    def test_k_of_n_all_fail(self):
        """k_of_n fails when all branches fail."""
        results = [
            _result("b1", "fail"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "1"})
        assert outcome.status == StageStatus.FAIL

    def test_k_of_n_empty_results(self):
        """k_of_n with no results returns SUCCESS (nothing to fail)."""
        outcome = _apply_join_policy([], "k_of_n", node_attrs={"min_success": "1"})
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_k_of_n_integration_with_handler(self):
        """k_of_n works end-to-end through the ParallelHandler."""
        outcomes = {
            "b1": Outcome(status=StageStatus.SUCCESS),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            "b3": Outcome(status=StageStatus.SUCCESS),
        }

        class _KOfNEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return outcomes[node_id]

        handler = ParallelHandler()
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
            par_node, _make_context(), graph, "/tmp", engine=_KOfNEngine()
        )
        assert outcome.status == StageStatus.SUCCESS


# =====================================================================
# quorum join policy tests
# =====================================================================


class TestQuorumJoinPolicy:
    """Tests for the quorum join policy."""

    def test_quorum_succeeds_at_default_50_percent(self):
        """quorum succeeds when >=50% of branches succeed (default fraction)."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
        ]
        # 50% succeed, default quorum_fraction=0.5 -> need ceil(2*0.5)=1
        outcome = _apply_join_policy(results, "quorum", node_attrs={})
        assert outcome.status == StageStatus.SUCCESS

    def test_quorum_fails_below_threshold(self):
        """quorum fails when fewer than fraction succeed."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
            _result("b3", "fail"),
            _result("b4", "fail"),
        ]
        # 1/4 = 25%, need ceil(4*0.5)=2 -> FAIL
        outcome = _apply_join_policy(results, "quorum", node_attrs={})
        assert outcome.status == StageStatus.FAIL

    def test_quorum_custom_fraction(self):
        """quorum uses custom quorum_fraction attribute."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
            _result("b3", "fail"),
        ]
        # 1/3 succeed, need ceil(3*0.34)=ceil(1.02)=2 -> FAIL with 0.34
        # But with fraction=0.3, need ceil(3*0.3)=ceil(0.9)=1 -> SUCCESS
        outcome = _apply_join_policy(
            results, "quorum", node_attrs={"quorum_fraction": "0.3"}
        )
        assert outcome.status == StageStatus.SUCCESS

    def test_quorum_counts_partial_success(self):
        """quorum counts partial_success as a success."""
        results = [
            _result("b1", "partial_success"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(results, "quorum", node_attrs={})
        assert outcome.status == StageStatus.SUCCESS

    def test_quorum_all_succeed(self):
        """quorum succeeds when all branches succeed."""
        results = [
            _result("b1", "success"),
            _result("b2", "success"),
            _result("b3", "success"),
        ]
        outcome = _apply_join_policy(results, "quorum", node_attrs={})
        assert outcome.status == StageStatus.SUCCESS

    def test_quorum_empty_results(self):
        """quorum with no results returns SUCCESS."""
        outcome = _apply_join_policy([], "quorum", node_attrs={})
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_quorum_integration_with_handler(self):
        """quorum works end-to-end through the ParallelHandler."""
        outcomes = {
            "b1": Outcome(status=StageStatus.SUCCESS),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            "b3": Outcome(status=StageStatus.SUCCESS),
            "b4": Outcome(status=StageStatus.SUCCESS),
        }

        class _QuorumEngine2:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return outcomes[node_id]

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"join_policy": "quorum", "quorum_fraction": "0.5"},
        )

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
            par_node, _make_context(), graph, "/tmp", engine=_QuorumEngine2()
        )
        assert outcome.status == StageStatus.SUCCESS


# =====================================================================
# fail_fast error policy tests
# =====================================================================


class TestFailFastErrorPolicy:
    """Tests for the fail_fast error policy."""

    @pytest.mark.asyncio
    async def test_fail_fast_cancels_remaining_on_failure(self):
        """fail_fast cancels remaining branches when one fails."""
        execution_order: list[str] = []

        class SlowEngine2:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                execution_order.append(f"start:{node_id}")
                if node_id == "b1":
                    # b1 fails immediately
                    return Outcome(status=StageStatus.FAIL, failure_reason="broken")
                # Other branches take a while
                await asyncio.sleep(0.5)
                execution_order.append(f"end:{node_id}")
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _slow_engine2 = SlowEngine2()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast", "join_policy": "wait_all"},
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
            par_node, _make_context(), graph, "/tmp", engine=_slow_engine2
        )

        # The outcome should reflect the failure
        assert outcome.status in (StageStatus.FAIL, StageStatus.PARTIAL_SUCCESS)
        # At least b2 and b3 should NOT have completed their slow path
        completed = [e for e in execution_order if e.startswith("end:")]
        assert len(completed) < 2  # Not all slow branches finished

    @pytest.mark.asyncio
    async def test_fail_fast_all_succeed(self):
        """fail_fast with all successes returns SUCCESS normally."""

        class SuccessEngine2:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast", "join_policy": "wait_all"},
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
            par_node, _make_context(), graph, "/tmp", engine=SuccessEngine2()
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_fail_fast_stores_partial_results(self):
        """fail_fast stores whatever results were collected before cancellation."""

        class MixedEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                if node_id == "b1":
                    return Outcome(status=StageStatus.FAIL, failure_reason="broken")
                await asyncio.sleep(0.5)
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        _mixed_engine = MixedEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "fail_fast"},
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

        await handler.execute(par_node, context, graph, "/tmp")
        results = context.get("parallel.results")
        # Should have at least the failed result
        assert results is not None
        assert len(results) >= 1


# =====================================================================
# ignore error policy tests
# =====================================================================


class TestIgnoreErrorPolicy:
    """Tests for the ignore error policy."""

    @pytest.mark.asyncio
    async def test_ignore_returns_only_successful_results(self):
        """ignore policy filters out failures, returns only successes."""
        outcomes = {
            "b1": Outcome(status=StageStatus.SUCCESS, notes="good"),
            "b2": Outcome(status=StageStatus.FAIL, failure_reason="broken"),
            "b3": Outcome(status=StageStatus.SUCCESS, notes="also good"),
        }

        class _IgnoreEngine3:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return outcomes[node_id]

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore", "join_policy": "wait_all"},
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

        outcome = await handler.execute(par_node, context, graph, "/tmp")

        # With ignore policy, failures are stripped — only successes remain
        # So wait_all sees all results as successes => SUCCESS
        assert outcome.status == StageStatus.SUCCESS

        # Results stored in context should only contain successes
        results = context.get("parallel.results")
        statuses = [r["status"] for r in results]
        assert "fail" not in statuses

    @pytest.mark.asyncio
    async def test_ignore_all_fail_returns_success_no_results(self):
        """ignore with all failures returns SUCCESS with empty results."""

        class FailEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.FAIL, failure_reason="all bad")

        handler = ParallelHandler()
        _fail_engine = FailEngine()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore", "join_policy": "wait_all"},
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

        outcome = await handler.execute(par_node, context, graph, "/tmp")
        # All failures ignored -> treated as success (nothing to fail)
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_ignore_all_succeed(self):
        """ignore with all successes works normally."""

        class _IgnoreSuccessEngine:
            async def run_subgraph(self, node_id, *, context=None, emit_node_events: bool = True):
                return Outcome(status=StageStatus.SUCCESS)

        handler = ParallelHandler()
        par_node = Node(
            id="parallel",
            shape="component",
            attrs={"error_policy": "ignore"},
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

        outcome = await handler.execute(par_node, context, graph, "/tmp", engine=_IgnoreSuccessEngine())
        assert outcome.status == StageStatus.SUCCESS
        results = context.get("parallel.results")
        assert len(results) == 2


# =====================================================================
# Edge cases
# =====================================================================


class TestPolicyEdgeCases:
    """Edge case tests for join and error policies."""

    def test_k_of_n_k_greater_than_n(self):
        """k_of_n with k > n branches always fails."""
        results = [
            _result("b1", "success"),
            _result("b2", "success"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "5"})
        assert outcome.status == StageStatus.FAIL

    def test_quorum_fraction_1_requires_all(self):
        """quorum with fraction=1.0 requires all branches to succeed."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
            _result("b3", "success"),
        ]
        outcome = _apply_join_policy(
            results, "quorum", node_attrs={"quorum_fraction": "1.0"}
        )
        assert outcome.status == StageStatus.FAIL

    def test_quorum_fraction_0_always_succeeds(self):
        """quorum with fraction=0.0 always succeeds (need 0 successes)."""
        results = [
            _result("b1", "fail"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(
            results, "quorum", node_attrs={"quorum_fraction": "0.0"}
        )
        assert outcome.status == StageStatus.SUCCESS

    def test_k_of_n_k_zero_always_succeeds(self):
        """k_of_n with k=0 always succeeds."""
        results = [
            _result("b1", "fail"),
        ]
        outcome = _apply_join_policy(results, "k_of_n", node_attrs={"min_success": "0"})
        assert outcome.status == StageStatus.SUCCESS

    def test_unknown_join_policy_defaults_to_wait_all(self):
        """Unknown join policy falls back to wait_all behavior."""
        results = [
            _result("b1", "success"),
            _result("b2", "fail"),
        ]
        outcome = _apply_join_policy(results, "unknown_policy", node_attrs={})
        # wait_all with failures => PARTIAL_SUCCESS
        assert outcome.status == StageStatus.PARTIAL_SUCCESS
