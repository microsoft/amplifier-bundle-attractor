"""Tests for topological (basin-lint) rules — TOPO-001 through TOPO-010.

These rules reason about cycle structure and handler semantics, not just
graph topology.  They are exposed via ``lint()`` (not ``validate()``) so
they remain lint-only and do not affect run-time validation behaviour.

Test pattern follows test_validation.py: construct Graph/Node/Edge objects
directly (no DOT parsing) for speed and isolation.
"""

from __future__ import annotations

from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.validation import (
    Diagnostic,
    lint,
    validate,
)

# ---------------------------------------------------------------------------
# Helpers — mirrors test_validation.py's pattern
# ---------------------------------------------------------------------------


def _mdiamond(node_id: str = "start") -> Node:
    return Node(id=node_id, shape="Mdiamond", label="Start")


def _msquare(node_id: str = "exit") -> Node:
    return Node(id=node_id, shape="Msquare", label="Exit")


def _box(node_id: str = "work", **kwargs) -> Node:
    return Node(id=node_id, shape="box", **kwargs)


def _diamond(node_id: str = "gate", **kwargs) -> Node:
    return Node(id=node_id, shape="diamond", **kwargs)


def _tool(node_id: str = "tool", **kwargs) -> Node:
    return Node(id=node_id, shape="parallelogram", **kwargs)


def _graph(
    nodes: dict[str, Node] | None = None,
    edges: list[Edge] | None = None,
    **kwargs,
) -> Graph:
    return Graph(
        name="test",
        nodes=nodes or {},
        edges=edges or [],
        **kwargs,
    )


def _diag(diags: list[Diagnostic], rule: str) -> list[Diagnostic]:
    """Return diagnostics matching the given rule name."""
    return [d for d in diags if d.rule == rule]


# ---------------------------------------------------------------------------
# TOPO-001: dead_conditional_edge
# ---------------------------------------------------------------------------


class TestDeadConditionalEdge:
    """TOPO-001: outcome!=success / outcome=fail edges out of diamond are dead."""

    def test_outcome_not_success_flagged(self):
        """ERROR: outcome!=success edge out of a diamond is dead."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "gate": _diamond("gate"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate"),
                Edge("gate", "exit", condition="outcome=success"),
                Edge("gate", "fix", condition="outcome!=success"),
                Edge("fix", "work"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert dead, "Expected dead_conditional_edge diagnostic"
        assert all(d.severity == "ERROR" for d in dead)
        assert any(d.node_id == "gate" for d in dead)

    def test_outcome_fail_flagged(self):
        """ERROR: outcome=fail edge out of a diamond is dead."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "ok": _box("ok"),
                "bad": _box("bad"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "ok", condition="outcome=success"),
                Edge("gate", "bad", condition="outcome=fail"),
                Edge("ok", "exit"),
                Edge("bad", "exit"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert dead, "Expected dead_conditional_edge diagnostic"
        assert any(d.node_id == "gate" for d in dead)

    def test_outcome_success_not_flagged(self):
        """No false-positive: outcome=success edge out of a diamond is fine."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "ok": _box("ok"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "ok", condition="outcome=success"),
                Edge("ok", "exit"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert not dead, f"False positive: {dead}"

    def test_outcome_not_success_on_box_not_flagged(self):
        """No false-positive: outcome!=success on a box (LLM) node is legitimate."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "exit", condition="outcome=success"),
                Edge("work", "fix", condition="outcome!=success"),
                Edge("fix", "work"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert not dead, f"False positive on box node: {dead}"

    def test_outcome_not_success_on_tool_not_flagged(self):
        """No false-positive: outcome!=success on a parallelogram (tool) node is fine."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "tool": _tool("tool"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "tool"),
                Edge("tool", "exit", condition="outcome=success"),
                Edge("tool", "fix", condition="outcome!=success"),
                Edge("fix", "tool"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert not dead, f"False positive on tool node: {dead}"

    def test_context_condition_on_diamond_not_flagged(self):
        """No false-positive: context.* condition on a diamond is fine (evidence-routing)."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "done": _box("done"),
                "retry": _box("retry"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.preferred_label=done"),
                Edge("gate", "retry", condition="context.preferred_label=retry"),
                Edge("done", "exit"),
                Edge("retry", "gate"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert not dead, f"False positive on context condition: {dead}"

    def test_conjunction_with_outcome_not_success_flagged(self):
        """ERROR: outcome!=success in a conjunction on a diamond is still dead."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "exit", condition="outcome=success"),
                Edge("gate", "fix", condition="context.x=y && outcome!=success"),
                Edge("fix", "exit"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert dead, (
            "Expected dead_conditional_edge for conjunction with outcome!=success"
        )

    def test_no_condition_on_diamond_not_flagged(self):
        """No false-positive: unconditional edge out of a diamond is fine."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "next": _box("next"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "next"),
                Edge("next", "exit"),
            ],
        )
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert not dead, f"False positive on unconditional edge: {dead}"


# ---------------------------------------------------------------------------
# TOPO-002: stale_label_collision
# ---------------------------------------------------------------------------


class TestStaleLabelCollision:
    """TOPO-002: tool node with last_line edge (no && outcome=success) + outcome=fail edge."""

    def test_collision_flagged(self):
        """WARNING: context.tool.last_line=X edge without && outcome=success + outcome=fail sibling."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "stalegate": _tool("stalegate"),
                "fix": _tool("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "stalegate"),
                Edge("stalegate", "exit", condition="context.tool.last_line=green"),
                Edge("stalegate", "fix", condition="outcome=fail"),
                Edge("fix", "stalegate"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert stale, "Expected stale_label_collision diagnostic"
        assert all(d.severity == "WARNING" for d in stale)
        assert any(d.node_id == "stalegate" for d in stale)

    def test_collision_with_outcome_not_success_flagged(self):
        """WARNING: outcome!=success sibling also triggers stale-label check."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _tool("gate"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "exit", condition="context.tool.last_line=done"),
                Edge("gate", "fix", condition="outcome!=success"),
                Edge("fix", "gate"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert stale, "Expected stale_label_collision diagnostic"

    def test_conjunction_with_outcome_success_not_flagged(self):
        """No false-positive: context.tool.last_line=X && outcome=success is safe."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _tool("gate"),
                "fix": _tool("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge(
                    "gate",
                    "exit",
                    condition="context.tool.last_line=green && outcome=success",
                ),
                Edge("gate", "fix", condition="outcome=fail"),
                Edge("fix", "gate"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert not stale, f"False positive: {stale}"

    def test_last_line_only_no_fail_sibling_not_flagged(self):
        """No false-positive: last_line edge without outcome=fail sibling is fine."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _tool("gate"),
                "done": _box("done"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.tool.last_line=green"),
                Edge("done", "exit"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert not stale, f"False positive (no fail sibling): {stale}"

    def test_on_box_node_not_flagged(self):
        """No false-positive: stale-label rule only applies to tool (parallelogram) nodes."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _box("gate"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "exit", condition="context.tool.last_line=green"),
                Edge("gate", "fix", condition="outcome=fail"),
                Edge("fix", "gate"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert not stale, f"False positive on box node: {stale}"

    def test_clean_convergence_loop_not_flagged(self):
        """No false-positive: the canonical clean-loop pattern passes clean."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge(
                    "work",
                    "exit",
                    condition="context.tool.last_line=stop && outcome=success",
                ),
                Edge(
                    "work",
                    "work",
                    condition="context.tool.last_line=go && outcome=success",
                ),
                Edge("work", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        stale = _diag(diags, "stale_label_collision")
        assert not stale, f"False positive on clean loop: {stale}"


# ---------------------------------------------------------------------------
# TOPO-003: acyclic_graph
# ---------------------------------------------------------------------------


class TestAcyclicGraph:
    """TOPO-003: linear pipeline with no cycle should warn."""

    def test_linear_pipeline_warns(self):
        """WARNING: acyclic pipeline (no back-edge) should warn."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "a": _tool("a"),
                "b": _tool("b"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "a"),
                Edge("a", "b"),
                Edge("b", "exit"),
            ],
        )
        diags = lint(g)
        acyclic = _diag(diags, "acyclic_graph")
        assert acyclic, "Expected acyclic_graph warning"
        assert all(d.severity == "WARNING" for d in acyclic)

    def test_graph_with_cycle_not_warned(self):
        """No false-positive: graph with a back-edge should not warn."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge(
                    "work",
                    "exit",
                    condition="context.tool.last_line=done && outcome=success",
                ),
                Edge("work", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        acyclic = _diag(diags, "acyclic_graph")
        assert not acyclic, f"False positive: {acyclic}"

    def test_self_loop_is_cyclic(self):
        """No false-positive: a self-loop counts as a cycle."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge(
                    "work",
                    "exit",
                    condition="context.tool.last_line=stop && outcome=success",
                ),
                Edge("work", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        acyclic = _diag(diags, "acyclic_graph")
        assert not acyclic, f"Self-loop not recognized as cycle: {acyclic}"


# ---------------------------------------------------------------------------
# TOPO-004: cycle_no_conditional_exit
# ---------------------------------------------------------------------------


class TestCycleNoConditionalExit:
    """TOPO-004: cycle with no conditional exit edge."""

    def test_unconditional_cycle_warns(self):
        """WARNING: a cycle where no exit edge has a condition."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "check": _box("check"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "check"),
                Edge("check", "work"),  # back-edge (no condition)
                Edge("check", "exit"),  # exit (no condition)
            ],
        )
        diags = lint(g)
        no_exit = _diag(diags, "cycle_no_conditional_exit")
        assert no_exit, "Expected cycle_no_conditional_exit warning"
        assert all(d.severity == "WARNING" for d in no_exit)

    def test_conditional_exit_not_warned(self):
        """No false-positive: cycle with a conditional exit edge is fine."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge(
                    "work",
                    "exit",
                    condition="context.tool.last_line=done && outcome=success",
                ),
                Edge("work", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        no_exit = _diag(diags, "cycle_no_conditional_exit")
        assert not no_exit, f"False positive: {no_exit}"

    def test_acyclic_graph_not_warned(self):
        """No false-positive: acyclic graph should not trigger this rule."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "a": _box("a"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "a"),
                Edge("a", "exit"),
            ],
        )
        diags = lint(g)
        no_exit = _diag(diags, "cycle_no_conditional_exit")
        assert not no_exit, f"False positive on acyclic graph: {no_exit}"


# ---------------------------------------------------------------------------
# TOPO-005: cycle_no_deterministic_exit
# ---------------------------------------------------------------------------


class TestCycleNoDeterministicExit:
    """TOPO-005: cycle with no deterministic exit predicate (LLM-only gating)."""

    def test_llm_only_cycle_warns(self):
        """WARNING: cycle with only LLM (box) nodes and no evidence-based exit."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "generate": _box("generate"),
                "assess": _box("assess"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "generate"),
                Edge("generate", "assess"),
                Edge("assess", "exit", condition="outcome=success"),  # LLM-gated exit
                Edge("assess", "generate", condition="outcome!=success"),  # back-edge
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert no_det, "Expected cycle_no_deterministic_exit warning"
        assert all(d.severity == "WARNING" for d in no_det)

    def test_tool_on_cycle_not_warned(self):
        """No false-positive: a tool node on the cycle provides deterministic evidence."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "validate": _tool("validate"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "validate"),
                Edge(
                    "validate",
                    "exit",
                    condition="context.tool.last_line=pass && outcome=success",
                ),
                Edge("validate", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert not no_det, f"False positive: {no_det}"

    def test_llm_only_cycle_with_context_exit_warns(self):
        """WARNING: cycle with only LLM (box) nodes, even with context.* exit condition.

        context.preferred_label set by an LLM node via report_outcome is still
        LLM say-so — it is not mechanically verified evidence.  A deterministic
        evidence gate requires a tool node on the cycle whose outcome/output
        actually gates control flow.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "assess": _box("assess"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "assess"),
                Edge("assess", "exit", condition="context.preferred_label=done"),
                Edge("assess", "work", condition="context.preferred_label=retry"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert no_det, (
            "Expected cycle_no_deterministic_exit: LLM-only cycle with context.* "
            "exit is still LLM say-so (no tool node on cycle)"
        )
        assert all(d.severity == "WARNING" for d in no_det)

    def test_acyclic_graph_not_warned(self):
        """No false-positive: acyclic graph does not trigger this rule."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "exit"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert not no_det, f"False positive on acyclic graph: {no_det}"

    def test_noop_tool_on_cycle_llm_set_context_exit_warns(self):
        """WARNING: a no-op tool on the cycle does not make an LLM-gated loop deterministic.

        The tool exists on the cycle, but its own evidence (outcome / tool.*)
        never gates routing: every outgoing edge is conditioned on
        context.preferred_label, which is set by the LLM ``assess`` node via
        report_outcome.  The loop's exit is still LLM say-so.  This is the
        false-negative case a naive "tool anywhere on the SCC" check misses.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "generate": _box("generate"),
                "assess": _box("assess"),
                "check": _tool("check"),  # no-op router: evidence unused
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "generate"),
                Edge("generate", "assess"),
                Edge("assess", "check"),
                Edge("check", "exit", condition="context.preferred_label=converged"),
                Edge("check", "generate", condition="context.preferred_label=refine"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert no_det, (
            "Expected cycle_no_deterministic_exit: the only tool on the cycle "
            "routes solely on LLM-set context keys — its own evidence gates nothing"
        )
        assert all(d.severity == "WARNING" for d in no_det)

    def test_tool_outcome_routed_cycle_not_warned(self):
        """No false-positive: a tool whose outcome routes the cycle is a mechanical gate.

        A parallelogram's outcome is its command's exit status — mechanical
        evidence, not LLM say-so.  Routing the exit on outcome=success and the
        back-edge on outcome=fail is deterministic gating.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "generate": _box("generate"),
                "validate": _tool("validate"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "generate"),
                Edge("generate", "validate"),
                Edge("validate", "exit", condition="outcome=success"),
                Edge("validate", "generate", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert not no_det, (
            f"False positive: tool outcome routing is mechanical: {no_det}"
        )

    def test_convergence_factory_shape_not_warned(self):
        """No false-positive: the convergence-factory pattern passes for the right reason.

        The cycle contains a real validation tool with a plain out-edge: plain
        edges only traverse on SUCCESS (FAIL is fail-fast), so a failing
        validation mechanically halts the loop — an implicit outcome=success
        gate.  The LLM-routed check node downstream does not undo that.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "generate": _box("generate"),
                "validate": _tool("validate"),  # real gate: plain edge = fail-fast
                "assess": _box("assess"),
                "check": _tool("check"),
                "feedback": _box("feedback"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "generate"),
                Edge("generate", "validate"),
                Edge("validate", "assess"),  # plain edge — traverses only on SUCCESS
                Edge("assess", "check"),
                Edge("check", "done", condition="context.preferred_label=converged"),
                Edge("check", "feedback", condition="context.preferred_label=refine"),
                Edge("feedback", "generate"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert not no_det, (
            f"False positive on convergence-factory shape (validate's plain edge "
            f"is an implicit outcome=success gate): {no_det}"
        )

    def test_tool_plain_edge_to_runs_on_failure_target_warns(self):
        """WARNING: a plain edge to a runs_on=failure/always target is not a gate.

        Plain edges normally traverse only on SUCCESS, but a target with
        runs_on=always or runs_on=failure opts into FAIL routing — the tool
        no longer halts the loop on failure, so it gates nothing.
        """
        sink = _box("sink")
        sink.attrs["runs_on"] = "always"
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "generate": _box("generate"),
                "check": _tool("check"),
                "sink": sink,
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "generate"),
                Edge("generate", "check"),
                Edge("check", "sink"),  # plain edge, but target opts into FAIL routing
                Edge("sink", "exit", condition="context.preferred_label=done"),
                Edge("sink", "generate", condition="context.preferred_label=retry"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert no_det, (
            "Expected cycle_no_deterministic_exit: plain edge to a runs_on=always "
            "target traverses on FAIL too — the tool does not gate the loop"
        )

    def test_tool_on_cycle_exit_gated_on_tool_context_key_not_warned(self):
        """No false-positive: tool on cycle AND exit gated on context key set by tool."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "validate": _tool("validate"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "validate"),
                Edge(
                    "validate",
                    "exit",
                    condition="context.tool.last_line=pass && outcome=success",
                ),
                Edge("validate", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        assert not no_det, (
            f"False positive: tool on cycle with evidence-gated exit: {no_det}"
        )


# ---------------------------------------------------------------------------
# TOPO-004 and TOPO-005: per-SCC analysis
# ---------------------------------------------------------------------------


class TestPerSCCAnalysis:
    """TOPO-004 and TOPO-005 must check each SCC independently.

    A compliant SCC must not suppress diagnostics for a separate non-compliant
    SCC in the same graph.
    """

    def test_topo004_two_sccs_one_compliant_one_not(self):
        """TOPO-004: two SCCs — one with conditional exit, one without.

        The non-compliant SCC (no conditional exit) must still be flagged even
        though the other SCC has a conditional exit.
        """
        # SCC-1: work1 <-> check1 with a conditional exit (compliant)
        # SCC-2: work2 <-> check2 with NO conditional exit (non-compliant)
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work1": _box("work1"),
                "check1": _box("check1"),
                "work2": _box("work2"),
                "check2": _box("check2"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work1"),
                Edge("work1", "check1"),
                Edge("check1", "work1", condition="outcome=fail"),  # back-edge SCC-1
                Edge(
                    "check1", "work2", condition="outcome=success"
                ),  # SCC-1 conditional exit
                Edge("work2", "check2"),
                Edge("check2", "work2"),  # back-edge SCC-2 (no condition)
                Edge("check2", "exit"),  # SCC-2 exit (no condition)
            ],
        )
        diags = lint(g)
        no_exit = _diag(diags, "cycle_no_conditional_exit")
        # SCC-2 must be flagged; SCC-1 must not be
        assert no_exit, "Expected cycle_no_conditional_exit for non-compliant SCC-2"
        # Only one diagnostic (for SCC-2), not two
        assert len(no_exit) == 1, (
            f"Expected 1 diagnostic, got {len(no_exit)}: {no_exit}"
        )
        # The flagged SCC should contain work2/check2
        flagged_msg = no_exit[0].message
        assert "work2" in flagged_msg or "check2" in flagged_msg, (
            f"Expected SCC-2 nodes in diagnostic, got: {flagged_msg}"
        )

    def test_topo005_two_sccs_one_compliant_one_not(self):
        """TOPO-005: two SCCs — one with deterministic exit, one without.

        The non-compliant SCC (LLM-only exit) must still be flagged even
        though the other SCC has a tool + evidence-gated exit.
        """
        # SCC-1: work1 -> validate1 with tool + context exit (compliant)
        # SCC-2: work2 <-> assess2, LLM-only nodes, outcome= exit (non-compliant)
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work1": _box("work1"),
                "validate1": _tool("validate1"),
                "work2": _box("work2"),
                "assess2": _box("assess2"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work1"),
                Edge("work1", "validate1"),
                Edge(
                    "validate1",
                    "work2",
                    condition="context.tool.last_line=pass && outcome=success",
                ),
                Edge("validate1", "work1", condition="outcome=fail"),  # SCC-1 back-edge
                Edge("work2", "assess2"),
                Edge(
                    "assess2", "work2", condition="outcome!=success"
                ),  # SCC-2 back-edge
                Edge("assess2", "exit", condition="outcome=success"),  # LLM-gated exit
            ],
        )
        diags = lint(g)
        no_det = _diag(diags, "cycle_no_deterministic_exit")
        # SCC-2 must be flagged; SCC-1 must not be
        assert no_det, "Expected cycle_no_deterministic_exit for non-compliant SCC-2"
        assert len(no_det) == 1, f"Expected 1 diagnostic, got {len(no_det)}: {no_det}"
        flagged_msg = no_det[0].message
        assert "work2" in flagged_msg or "assess2" in flagged_msg, (
            f"Expected SCC-2 nodes in diagnostic, got: {flagged_msg}"
        )


# ---------------------------------------------------------------------------
# lint() API contract
# ---------------------------------------------------------------------------


class TestLintAPI:
    """lint() runs structural + topological rules; validate() does not run topo rules."""

    def test_lint_includes_structural_rules(self):
        """lint() runs structural rules (e.g. missing start node)."""
        g = _graph(
            nodes={"exit": _msquare()},
            edges=[],
        )
        diags = lint(g)
        assert any(d.rule == "start_node" for d in diags)

    def test_validate_does_not_include_topo_rules(self):
        """validate() does NOT run topological rules — lint-only."""
        # A graph with a dead diamond edge
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _diamond("gate"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "exit", condition="outcome=success"),
                Edge("gate", "fix", condition="outcome!=success"),
                Edge("fix", "exit"),
            ],
        )
        validate_diags = validate(g)
        lint_diags = lint(g)

        validate_rules = {d.rule for d in validate_diags}
        lint_rules = {d.rule for d in lint_diags}

        assert "dead_conditional_edge" not in validate_rules, (
            "validate() must not run topological rules"
        )
        assert "dead_conditional_edge" in lint_rules, (
            "lint() must include topological rules"
        )

    def test_clean_graph_exits_clean(self):
        """A correct convergence loop produces no diagnostics from lint()."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge(
                    "work",
                    "exit",
                    condition="context.tool.last_line=stop && outcome=success",
                ),
                Edge(
                    "work",
                    "work",
                    condition="context.tool.last_line=go && outcome=success",
                ),
                Edge("work", "work", condition="outcome=fail"),
            ],
        )
        diags = lint(g)
        topo_diags = [
            d
            for d in diags
            if d.rule
            in {
                "dead_conditional_edge",
                "stale_label_collision",
                "acyclic_graph",
                "cycle_no_conditional_exit",
                "cycle_no_deterministic_exit",
            }
        ]
        assert not topo_diags, f"False positives on clean loop: {topo_diags}"

    def test_lint_returns_list_of_diagnostics(self):
        """lint() returns a list of Diagnostic objects."""
        g = _graph(
            nodes={"start": _mdiamond(), "exit": _msquare()},
            edges=[Edge("start", "exit")],
        )
        result = lint(g)
        assert isinstance(result, list)
        for d in result:
            assert isinstance(d, Diagnostic)


# ---------------------------------------------------------------------------
# Regression: the 8-example dead-diamond pattern
# ---------------------------------------------------------------------------


class TestDeadDiamondRegressions:
    """Regression tests for the dead-diamond bug class that shipped in 8 examples.

    Each test constructs the pattern found in the affected example and asserts
    that dead_conditional_edge fires on the diamond node.
    """

    def _make_diamond_gate_graph(self, gate_id: str) -> Graph:
        """Minimal graph with a diamond gate routing on outcome=."""
        return _graph(
            nodes={
                "start": _mdiamond(),
                gate_id: _diamond(gate_id),
                "work": _box("work"),
                "fix": _box("fix"),
                "exit": _msquare(),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", gate_id),
                Edge(gate_id, "exit", condition="outcome=success"),
                Edge(gate_id, "fix", condition="outcome!=success"),
                Edge("fix", "work"),
            ],
        )

    def test_gate_pattern(self):
        """Pattern from 03-conditional-routing and 09-manager-supervisor."""
        g = self._make_diamond_gate_graph("gate")
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert dead
        assert any(d.node_id == "gate" for d in dead)

    def test_test_gate_pattern(self):
        """Pattern from 10-full-attractor, 12-graph-resume, bug-fix, feature-build, refactor, test-gen."""
        g = self._make_diamond_gate_graph("test_gate")
        diags = lint(g)
        dead = _diag(diags, "dead_conditional_edge")
        assert dead
        assert any(d.node_id == "test_gate" for d in dead)


# ---------------------------------------------------------------------------
# TOPO-006: fail_routed_to_exit
# ---------------------------------------------------------------------------


def _tool_with_runs_on(node_id: str, runs_on: str | None = None) -> Node:
    attrs = {"tool_command": "echo ok"}
    if runs_on is not None:
        attrs["runs_on"] = runs_on
    return Node(id=node_id, shape="parallelogram", attrs=attrs)


class TestFailRoutedToExit:
    """TOPO-006: a failure outcome routed into the terminal success node.

    Issue #173.  The isolated-probe structure required by the maintainer
    ruling: hazard graphs assert the diagnostic fires AND names the
    failure-conditioned edge + source node; marked/corrective graphs assert
    no fail_routed_to_exit diagnostic appears at all.
    """

    # -- direct form ---------------------------------------------------------

    def test_direct_fail_to_done_flagged(self):
        """Hazard probe: verify -> done [outcome=fail] fires, names edge+source."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "build": _tool("build"),
                "verify": _tool("verify"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "build"),
                Edge("build", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "done", condition="outcome=fail"),
            ],
        )
        diags = _diag(lint(g), "fail_routed_to_exit")
        assert diags, "Expected fail_routed_to_exit diagnostic"
        assert all(d.severity == "WARNING" for d in diags)
        d = diags[0]
        assert d.node_id == "verify"
        assert d.edge == ("verify", "done")
        assert "verify" in d.message and "done" in d.message
        assert "outcome=fail" in d.message

    def test_direct_fires_on_llm_source(self):
        """Ruling 1: fires regardless of source shape — box (LLM) source."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "judge": _box("judge", prompt="judge it"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "judge"),
                Edge("judge", "done", condition="outcome=success"),
                Edge("judge", "done", condition="outcome=fail"),
            ],
        )
        diags = _diag(lint(g), "fail_routed_to_exit")
        assert diags and diags[0].node_id == "judge"

    def test_direct_outcome_not_success_flagged(self):
        """outcome!=success targeting the exit is a failure-conditioned edge."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "done", condition="outcome!=success"),
            ],
        )
        assert _diag(lint(g), "fail_routed_to_exit")

    def test_success_edge_to_exit_not_flagged(self):
        """No false positive: the normal success exit edge stays silent."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _tool("work"),
                "verify": _tool("verify"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "work", condition="outcome=fail"),
            ],
        )
        assert not _diag(lint(g), "fail_routed_to_exit")

    # -- indirect form (required, not stretch) -------------------------------

    def _recorder_graph(self, recorder_runs_on: str | None) -> Graph:
        """The issue's own runtime demonstration: fail -> recorder -> done."""
        return _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "record_failure": _tool_with_runs_on(
                    "record_failure", recorder_runs_on
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "record_failure", condition="outcome=fail"),
                Edge("record_failure", "done"),
            ],
        )

    def test_indirect_unmarked_passthrough_flagged(self):
        """Hazard probe: unmarked recorder before done — the sharper hazard
        (failed gate, status success, exit 0)."""
        g = self._recorder_graph(recorder_runs_on=None)
        diags = _diag(lint(g), "fail_routed_to_exit")
        assert diags, "Expected fail_routed_to_exit on unmarked pass-through"
        d = diags[0]
        assert d.node_id == "verify"
        assert d.edge == ("verify", "record_failure")
        # Encouraged by the ruling: the pass-through path is named.
        assert "record_failure" in d.message and "done" in d.message

    def test_indirect_runs_on_always_not_flagged(self):
        """Ruling 2 probe: runs_on=always recorder is a deliberately declared
        handled-failure termination — MUST NOT be flagged."""
        g = self._recorder_graph(recorder_runs_on="always")
        assert not _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_runs_on_failure_not_flagged(self):
        """Ruling 2 probe: runs_on=failure intermediary — MUST NOT be flagged."""
        g = self._recorder_graph(recorder_runs_on="failure")
        assert not _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_unrecognized_runs_on_flagged(self):
        """engine.py::_get_runs_on normalizes anything but always/failure to
        the default 'success' — an unrecognized marker is unmarked."""
        g = self._recorder_graph(recorder_runs_on="sometimes")
        assert _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_regating_intermediary_with_plain_exit_flagged(self):
        """An intermediary with a conditional edge plus a plain edge to the exit
        is the hazard shape: the plain edge is a silent escape route — flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="do it"),
                "verify": _tool("verify"),
                "triage": _tool("triage"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "triage", condition="outcome=fail"),
                Edge("triage", "work", condition="context.tool.last_line=retry"),
                Edge("triage", "done"),
            ],
        )
        # triage has a plain edge to done (exit) -- this IS the hazard shape.
        assert _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_true_regate_all_conditional_not_flagged(self):
        """An intermediary whose ALL outgoing edges are conditional (no plain
        escape to exit or anywhere) truly re-gates the flow -- not flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="do it"),
                "verify": _tool("verify"),
                "triage": _tool("triage"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "triage", condition="outcome=fail"),
                Edge("triage", "work", condition="context.tool.last_line=retry"),
                Edge("triage", "done", condition="context.tool.last_line=escalate"),
            ],
        )
        # triage has ONLY conditional outgoing edges — a true re-gate, not flagged.
        assert not _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_plain_escape_via_nonexit_intermediary_flagged(self):
        """An intermediary with a plain edge to a non-exit node whose downstream
        path reaches the exit without a re-gate is still the hazard shape — flagged.

        triage -> record (plain) -> done (plain): the plain escape is one hop
        away from exit but the silent path still exists.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="do it"),
                "verify": _tool("verify"),
                "triage": _tool("triage"),
                "record": _tool("record"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "triage", condition="outcome=fail"),
                Edge("triage", "work", condition="context.tool.last_line=retry"),
                Edge("triage", "record"),
                Edge("record", "done"),
            ],
        )
        # triage has a plain edge to record (non-exit), which continues plainly
        # to done.  The silent exit path exists; must be flagged.
        assert _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_multihop_one_unmarked_flagged(self):
        """Multi-hop path with one unmarked intermediary among marked ones
        is still the accident class — flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "cleanup": _tool_with_runs_on("cleanup", "always"),
                "notify": _tool_with_runs_on("notify", None),  # unmarked
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "cleanup", condition="outcome=fail"),
                Edge("cleanup", "notify"),
                Edge("notify", "done"),
            ],
        )
        diags = _diag(lint(g), "fail_routed_to_exit")
        assert diags and diags[0].edge == ("verify", "cleanup")

    def test_indirect_multihop_all_marked_not_flagged(self):
        """Every intermediary marked runs_on=always/failure — deliberate."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "cleanup": _tool_with_runs_on("cleanup", "always"),
                "notify": _tool_with_runs_on("notify", "failure"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "cleanup", condition="outcome=fail"),
                Edge("cleanup", "notify"),
                Edge("notify", "done"),
            ],
        )
        assert not _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_no_path_to_exit_not_flagged(self):
        """Failure routed to a corrective back-edge target (never reaches the
        exit unconditionally) — the healthy pattern, not flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="do it"),
                "verify": _tool("verify"),
                "fix": _box("fix", prompt="fix it"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "fix", condition="outcome=fail"),
                Edge("fix", "verify"),
            ],
        )
        assert not _diag(lint(g), "fail_routed_to_exit")

    def test_indirect_mixed_paths_flagged(self):
        """A marked path AND an unmarked path both exist from the receiving
        node — the unmarked pass-through path is the hazard, flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "recorder": _tool_with_runs_on("recorder", None),  # unmarked
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "recorder", condition="outcome=fail"),
                Edge("recorder", "done"),
            ],
        )
        assert _diag(lint(g), "fail_routed_to_exit")

    # -- DOT-text isolated probes (issue #173's own fixtures) -----------------

    def test_issue_repro_dot_fires(self):
        """The issue's fail_to_done.dot repro, via the real DOT parser."""
        from amplifier_module_loop_pipeline.dot_parser import parse_dot

        dot = """
        digraph fail_to_done {
            graph [goal="Implement the change and verify it"]
            start  [shape=Mdiamond]
            build  [shape=parallelogram, tool_command="echo built"]
            verify [shape=parallelogram, tool_command="./run_verify.sh"]
            done   [shape=Msquare]
            start -> build -> verify
            verify -> done [condition="outcome=success"]
            verify -> done [condition="outcome=fail"]
        }
        """
        diags = _diag(lint(parse_dot(dot)), "fail_routed_to_exit")
        assert diags, "Expected fail_routed_to_exit on the issue repro"
        assert diags[0].node_id == "verify"
        assert diags[0].edge == ("verify", "done")

    def test_issue_legit_recorder_dot_silent(self):
        """The issue's legitimate runs_on=always recorder variant stays silent."""
        from amplifier_module_loop_pipeline.dot_parser import parse_dot

        dot = """
        digraph handled_failure {
            graph [goal="Implement the change and verify it"]
            start  [shape=Mdiamond]
            build  [shape=parallelogram, tool_command="echo built"]
            verify [shape=parallelogram, tool_command="./run_verify.sh"]
            record_failure [shape=parallelogram, tool_command="echo recorded the failure", runs_on=always]
            done   [shape=Msquare]
            start -> build -> verify
            verify -> done [condition="outcome=success"]
            verify -> record_failure [condition="outcome=fail"]
            record_failure -> done
        }
        """
        assert not _diag(lint(parse_dot(dot)), "fail_routed_to_exit")

    def test_indirect_human_gate_intermediary_not_flagged(self):
        """A hexagon (wait.human) intermediary re-gates via external human
        judgment — TOPO-004/005 human-gate precedent — not flagged."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "verify": _tool("verify"),
                "approve": Node(id="approve", shape="hexagon", label="Approve?"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "verify"),
                Edge("verify", "done", condition="outcome=success"),
                Edge("verify", "approve", condition="outcome=fail"),
                Edge("approve", "done"),
            ],
        )
        assert not _diag(lint(g), "fail_routed_to_exit")


# ---------------------------------------------------------------------------
# TOPO-007: gate_retry_budget_dead
# ---------------------------------------------------------------------------


class TestGateRetryBudgetDead:
    """TOPO-007: goal-gate retry budget structurally dead under loop_restart.

    Issue #253.  ``loop_restart`` resets ``goal_gate_retries`` (engine.py,
    run() Step 6) -- correct for ATX-12's fresh-attempt semantics, but when
    EVERY success-path walk from the gate's retry target back to the exit
    crosses a loop_restart edge, the budget resets on every gate-retry cycle
    and can never bind: the loop is bounded only by the global step cap.

    Measured on the shipped engine (4-node reduction): without loop_restart
    the gate ran 51 times and stopped at the 50-retry budget; with
    loop_restart on the retry walk it ran 66 times, the counter pinned at 1,
    and only the step cap (nodes x 50 = 200) ended the run.
    """

    # -- hazard probes -------------------------------------------------------

    def test_minimal_hazard_fires(self):
        """Hazard probe: retry walk crosses a loop_restart edge -> fires,
        names gate, retry target, and the resetting edge."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "retry_target": "work"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        diags = _diag(lint(g), "gate_retry_budget_dead")
        assert diags, "Expected gate_retry_budget_dead diagnostic"
        d = diags[0]
        assert d.severity == "WARNING"
        assert d.node_id == "gate"
        assert "work" in d.message
        assert "loop_restart" in d.message
        assert "step cap" in d.message

    def test_objective_runner_reduction_fires(self):
        """Hazard probe: the pre-#248 objective-runner shape.  The retry
        target's only success-path edge is the loop_restart edge; a
        fail-conditioned escape to the exit exists but does not keep the
        budget alive.  Measured shape: evidence gate executed 67 times in an
        8-node reduction (its retry target 66) before the step cap."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "triage": _tool("triage"),
                "evidence_gate": Node(
                    id="evidence_gate",
                    shape="parallelogram",
                    attrs={
                        "tool_command": "./dod.sh",
                        "goal_gate": True,
                        "retry_target": "feedback",
                    },
                ),
                "feedback": _box("feedback", prompt="teach the next attempt"),
                "postmortem": _box("postmortem", prompt="salvage"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "triage"),
                Edge("triage", "evidence_gate"),
                Edge("evidence_gate", "done", condition="outcome=success"),
                Edge("evidence_gate", "feedback", condition="outcome=fail"),
                # iteration protocol: fresh attempt re-enters through triage
                Edge("feedback", "triage", loop_restart=True),
                # hard-failure escape reaches the exit WITHOUT loop_restart,
                # but only on feedback's own failure -- the success walk
                # still crosses the resetting edge every cycle.
                Edge("feedback", "postmortem", condition="outcome=fail"),
                Edge("postmortem", "done"),
            ],
        )
        diags = _diag(lint(g), "gate_retry_budget_dead")
        assert diags, "Expected gate_retry_budget_dead on the objective-runner shape"
        assert diags[0].node_id == "evidence_gate"
        assert "feedback" in diags[0].message

    def test_graph_level_retry_target_fires(self):
        """The engine resolves graph-level retry_target for gate retries
        (node > node fallback > graph > graph fallback); a dead walk from a
        graph-level target fires too."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box("gate", prompt="g", attrs={"goal_gate": True}),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
            ],
            graph_attrs={"retry_target": "work"},
        )
        diags = _diag(lint(g), "gate_retry_budget_dead")
        assert diags and diags[0].node_id == "gate"

    def test_fallback_retry_target_fires(self):
        """node fallback_retry_target is consulted when retry_target absent."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "fallback_retry_target": "work"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        assert _diag(lint(g), "gate_retry_budget_dead")

    # -- benign probes (the shipped-graph shapes) ----------------------------

    def test_fail_backedge_silent(self):
        """02-plan-implement-test shape: loop_restart rides the gate's own
        fail-conditioned back-edge; the success walk implement -> gate ->
        exit never crosses it.  The budget stays live -- silent."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "implement": _box("implement", prompt="i"),
                "test_gate": Node(
                    id="test_gate",
                    shape="parallelogram",
                    attrs={
                        "tool_command": "pytest",
                        "goal_gate": True,
                        "retry_target": "implement",
                    },
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "implement"),
                Edge("implement", "test_gate"),
                Edge("test_gate", "done", condition="outcome=success"),
                Edge(
                    "test_gate",
                    "implement",
                    condition="outcome=fail",
                    loop_restart=True,
                ),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_iterate_backedge_silent(self):
        """task-runner shape: the loop_restart back-edge is the iteration
        protocol (feedback -> attempt); the forward success walk attempt ->
        gate -> exit is loop_restart-free -- silent."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "attempt": _box("attempt", prompt="a"),
                "gate": Node(
                    id="gate",
                    shape="parallelogram",
                    attrs={
                        "tool_command": "check",
                        "goal_gate": True,
                        "retry_target": "attempt",
                    },
                ),
                "feedback": _box("feedback", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "attempt"),
                Edge("attempt", "gate"),
                Edge("gate", "done", condition="outcome=success"),
                Edge("gate", "feedback", condition="outcome=fail"),
                Edge("feedback", "attempt", loop_restart=True),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_no_loop_restart_silent(self):
        """Same hazard topology minus loop_restart: the budget binds (measured
        51 executions, then 'Unsatisfied goal gates') -- silent."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "retry_target": "work"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate"),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_no_retry_target_silent(self):
        """A gate with no effective retry target has no budget to kill --
        silent here (goal_gate_has_retry owns that report)."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box("gate", prompt="g", attrs={"goal_gate": True}),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_nonexistent_retry_target_silent(self):
        """A retry target that is not a node is a different defect
        (retry_target_exists owns it) -- silent here."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "retry_target": "ghost"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_context_conditioned_escape_silent(self):
        """A context-conditioned escape (statically unknowable) counts as a
        live walk to the exit -- conservative toward silence."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "router": _tool("router"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "retry_target": "work"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "router"),
                Edge("router", "gate", loop_restart=True),
                Edge(
                    "router",
                    "done",
                    condition="context.tool.last_line=exhausted",
                ),
                Edge("gate", "done", condition="outcome=fail"),
            ],
        )
        assert not _diag(lint(g), "gate_retry_budget_dead")

    def test_retry_target_dead_end_silent(self):
        """If the projected retry walk never meets a loop_restart edge, this
        rule has nothing to say (reachability/other rules own dead ends)."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work", prompt="w"),
                "sink": _box("sink", prompt="s"),
                "gate": _box(
                    "gate",
                    prompt="g",
                    attrs={"goal_gate": True, "retry_target": "sink"},
                ),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate", loop_restart=True),
                Edge("gate", "done", condition="outcome=fail"),
                Edge("sink", "sink2", condition="outcome=fail"),
            ],
        )
        # sink's only edge is fail-conditioned; the projected walk from sink
        # meets no loop_restart edge -> silent.
        assert not _diag(lint(g), "gate_retry_budget_dead")

    # -- enforcement ---------------------------------------------------------

    def test_documented_budget_matches_engine(self):
        """The diagnostic message mirrors the engine's budget constant
        (importing engine from validation would create an import cycle);
        this pin keeps the mirror honest."""
        from amplifier_module_loop_pipeline.engine import PipelineEngine
        from amplifier_module_loop_pipeline.validation import (
            _GOAL_GATE_RETRY_BUDGET,
        )

        assert _GOAL_GATE_RETRY_BUDGET == PipelineEngine._MAX_GOAL_GATE_RETRIES

    def test_calibration_zero_findings_on_shipped_graphs(self):
        """Calibration: TOPO-007 is silent on every shipped graph.

        The shipped corpus deliberately pairs goal_gate + retry_target with
        loop_restart iterate back-edges (task-runner, the capsule pipelines,
        02-plan-implement-test, 04-retry-with-fallback) -- a naive
        coexistence rule would fire on all of them.  This sweep pins the
        calibrated predicate: zero findings across examples/ and
        .github/capsule-pipeline/.  Skips gracefully on installed-package
        runs where the corpus is not present (the test_examples_lint_clean
        precedent).
        """
        from pathlib import Path as _Path

        import pytest as _pytest

        from amplifier_module_loop_pipeline.dot_parser import parse_dot as _parse

        repo_root = _Path(__file__).resolve().parents[3]
        sweep_dirs = [repo_root / "examples", repo_root / ".github" / "capsule-pipeline"]
        dot_files = [p for d in sweep_dirs if d.is_dir() for p in sorted(d.rglob("*.dot"))]
        if not dot_files:
            _pytest.skip("shipped corpus not present (installed-package run)")
        fired: list[str] = []
        for dot_path in dot_files:
            graph = _parse(dot_path.read_text(encoding="utf-8"))
            for d in _diag(lint(graph), "gate_retry_budget_dead"):
                fired.append(f"{dot_path.relative_to(repo_root)}: {d.message}")
        assert not fired, (
            "TOPO-007 fired on shipped graphs (calibration regression):\n"
            + "\n".join(fired)
        )


# ---------------------------------------------------------------------------
# TOPO-008: inert_evidence_gate
# ---------------------------------------------------------------------------


def _gate(node_id: str = "gate", command: str = "pytest -q") -> Node:
    """A tool node whose command actually checks something."""
    return Node(id=node_id, shape="parallelogram", attrs={"tool_command": command})


class TestInertEvidenceGate:
    """TOPO-008: an evidence gate whose answer cannot change where the run goes.

    Issue #254 item 2 -- the ``attractor lint`` sibling of the authoring
    checker's A10 (issue #245).  A10 protects machine-authored graphs only;
    this rule asks the same question of hand-authored ones.

    Structure mirrors ``TestFailRoutedToExit``: hazard graphs assert the
    diagnostic fires AND names the gate, the tokens and the exit; legitimate
    graphs assert no ``inert_evidence_gate`` diagnostic appears at all.
    """

    # -- the reproduction ----------------------------------------------------

    def test_b1_construction_fires(self):
        """Construction B1 from issue #245, through the real DOT parser.

        The SAME source the authoring checker's A10 tests use, imported rather
        than copied, so the two layers can never be shown green on different
        graphs while claiming to ask the same question.
        """
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from tests.test_authoring_layer_gates import _B1_IGNORED_GATE

        diags = _diag(lint(parse_dot(_B1_IGNORED_GATE)), "inert_evidence_gate")
        assert diags, "Expected inert_evidence_gate on the B1 construction"
        assert len(diags) == 1
        d = diags[0]
        assert d.severity == "WARNING"
        assert d.node_id == "gate"
        # The message names the gate, both tokens, and the shared exit.
        assert "'green'" in d.message and "'red'" in d.message
        assert "'done'" in d.message

    def test_b1_laundered_through_relay_no_ops_still_fires(self):
        """Two forwarding diamonds launder nothing.

        A ``diamond`` with a single unconditional edge runs nothing and decides
        nothing, so entering and leaving it is indistinguishable from taking
        the edge directly.  If the landing chase stopped at the first hop, the
        cheapest way past this rule would be to add two of these.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "gate": _gate(),
                "relay_green": _diamond("relay_green"),
                "relay_red": _diamond("relay_red"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate"),
                Edge("gate", "relay_green", condition="context.tool.last_line=green"),
                Edge("gate", "relay_red", condition="context.tool.last_line=red"),
                Edge("relay_green", "done"),
                Edge("relay_red", "done"),
            ],
        )
        diags = _diag(lint(g), "inert_evidence_gate")
        assert diags, "relay no-ops must not hide the shared landing"
        assert diags[0].node_id == "gate"
        assert "'done'" in diags[0].message

    def test_b1_repaired_is_clean(self):
        """The fix the message asks for, applied -- and it passes.

        A rule whose only demonstrated behaviour is rejection has not been
        shown to be satisfiable.  Routing the failing token back into the
        corrective loop is exactly what the ``fix`` text tells the author to
        do, so it has to be enough.
        """
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from tests.test_authoring_layer_gates import _B1_IGNORED_GATE

        repaired = _B1_IGNORED_GATE.replace(
            'gate -> done [condition="context.tool.last_line=red"]',
            'gate -> work [condition="context.tool.last_line=red"]',
        )
        assert repaired != _B1_IGNORED_GATE, "repair anchor drifted"
        assert not _diag(lint(parse_dot(repaired)), "inert_evidence_gate")

    # -- the measured boundaries --------------------------------------------

    def test_two_tokens_into_an_ordinary_node_is_clean(self):
        """The exit-only narrowing, which was measured rather than assumed.

        Several distinct diagnoses converging on one node that WRITES THEM UP
        is legitimate and shipped -- ``.github/capsule-pipeline/*.dot`` do it
        on purpose.  There the token is recorded rather than routed on.  Two
        tokens into the EXIT has no such reading: the run ends green either
        way.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "criteria_gate": _gate("criteria_gate", "python3 check_criteria.py"),
                "write_finding": _box("write_finding"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "criteria_gate"),
                Edge(
                    "criteria_gate",
                    "write_finding",
                    condition="context.tool.last_line=malformed_criteria",
                ),
                Edge(
                    "criteria_gate",
                    "write_finding",
                    condition="context.tool.last_line=no_criteria",
                ),
                Edge("write_finding", "done"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_chase_stops_at_a_node_that_does_something(self):
        """The relay narrowing: an LLM worker between gate and exit is not a relay.

        If the two answers ran different work before converging, the gate's
        answer demonstrably changed what happened, and whether that path
        should still end green is a judgement this rule does not have.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate(),
                "celebrate": _box("celebrate"),
                "postmortem": _box("postmortem"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "celebrate", condition="context.tool.last_line=green"),
                Edge("gate", "postmortem", condition="context.tool.last_line=red"),
                Edge("celebrate", "done"),
                Edge("postmortem", "done"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_a_branching_diamond_is_not_a_relay(self):
        """A diamond that actually decides is not transparent.

        ``_RELAY_SHAPES`` membership is not enough: a relay is a diamond with
        exactly ONE unconditional outgoing edge.  A diamond that routes is
        doing the deciding the rule is looking for.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box("work"),
                "gate": _gate(),
                "triage": _diamond("triage"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "work"),
                Edge("work", "gate"),
                Edge("gate", "triage", condition="context.tool.last_line=green"),
                Edge("gate", "done", condition="context.tool.last_line=red"),
                Edge("triage", "done", condition="outcome=success"),
                Edge("triage", "work"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_constant_emitter_is_not_an_evidence_gate(self):
        """``printf gate_pass`` cannot fail, so nothing behind it is gated.

        The rule has no opinion about a node that only emits a constant -- it
        was never evidence, so its answer never decided anything to begin
        with.  TOPO-004/005 own that shape.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate("gate", "printf green"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.tool.last_line=green"),
                Edge("gate", "done", condition="context.tool.last_line=red"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_inequality_is_not_an_answer(self):
        """``last_line!=green`` selects no token.

        The rule reasons about which ANSWER sends the run where, and "anything
        but green" is not an answer.  Only one distinct token is routed here,
        so there is nothing to compare.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate(),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.tool.last_line=green"),
                Edge("gate", "done", condition="context.tool.last_line!=green"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_same_token_twice_is_not_two_answers(self):
        """Two edges carrying the SAME token are one answer, not two."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate(),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.tool.last_line=green"),
                Edge(
                    "gate",
                    "done",
                    condition="context.tool.last_line=green && outcome=success",
                ),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    def test_conjunction_still_yields_the_routed_token(self):
        """``last_line=green && outcome=success`` routes on ``green``.

        Parsed through ``conditions.parse_condition`` -- the same grammar the
        engine routes with -- so lint and routing cannot drift.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate(),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge(
                    "gate",
                    "done",
                    condition="context.tool.last_line=green && outcome=success",
                ),
                Edge(
                    "gate",
                    "done",
                    condition="outcome=success && context.tool.last_line=red",
                ),
            ],
        )
        diags = _diag(lint(g), "inert_evidence_gate")
        assert diags
        assert "'green'" in diags[0].message and "'red'" in diags[0].message

    def test_unreachable_gate_is_ignored(self):
        """A gate the run can never enter decides nothing either way."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "done": _msquare("done"),
                "orphan_gate": _gate("orphan_gate"),
            },
            edges=[
                Edge("start", "done"),
                Edge("orphan_gate", "done", condition="context.tool.last_line=green"),
                Edge("orphan_gate", "done", condition="context.tool.last_line=red"),
            ],
        )
        assert not _diag(lint(g), "inert_evidence_gate")

    # -- lint-only, WARNING-severity ----------------------------------------

    def test_rule_is_lint_only_and_warning_severity(self):
        """``validate()`` stays silent; ``lint()`` warns.

        TOPO-008 must not change run-time validation behaviour -- a graph that
        executes today cannot start failing ``validate_or_raise`` because of
        it.
        """
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from tests.test_authoring_layer_gates import _B1_IGNORED_GATE

        graph = parse_dot(_B1_IGNORED_GATE)
        assert not _diag(validate(graph), "inert_evidence_gate")
        diags = _diag(lint(graph), "inert_evidence_gate")
        assert diags and all(d.severity == "WARNING" for d in diags)


# ---------------------------------------------------------------------------
# TOPO-009: outcome_label_shadowing (issue #226)
# ---------------------------------------------------------------------------


def _hazard_graph() -> Graph:
    """The issue-#226 shape: an `outcome=retry` edge on a label-steered node."""
    return _graph(
        nodes={
            "start": _mdiamond(),
            "review": _box("review", prompt="review it"),
            "fix": _box("fix", prompt="fix it"),
            "rework": _box("rework", prompt="rework it"),
            "done": _msquare("done"),
        },
        edges=[
            Edge("start", "review"),
            Edge("review", "fix", condition="outcome=retry"),
            Edge("review", "rework", label="retry"),
            Edge("review", "done", condition="outcome=success"),
            Edge("fix", "review"),
            Edge("rework", "review"),
        ],
    )


class TestOutcomeLabelShadowing:
    """TOPO-009 -- `outcome=` reads preferred_label before status (EXTENSIONS §22)."""

    # -- fires ---------------------------------------------------------------

    def test_fires_on_the_constructed_hazard(self):
        diags = _diag(lint(_hazard_graph()), "outcome_label_shadowing")
        assert len(diags) == 1
        d = diags[0]
        assert d.node_id == "review"
        assert d.edge == ("review", "fix")

    def test_message_names_the_rule_the_ledger_and_both_edges(self):
        d = _diag(lint(_hazard_graph()), "outcome_label_shadowing")[0]
        assert "TOPO-009" in d.message
        assert "EXTENSIONS.md §22" in d.message
        assert "ATX-5" in d.message
        assert 'review -> fix [condition="outcome=retry"]' in d.message
        assert 'review -> rework [label="retry"]' in d.message

    def test_fix_offers_both_unambiguous_keys(self):
        d = _diag(lint(_hazard_graph()), "outcome_label_shadowing")[0]
        assert 'condition="status=retry"' in d.fix
        assert 'condition="preferred_label=retry"' in d.fix

    def test_one_diagnostic_per_node_not_per_edge(self):
        """`review` carries two `outcome=` edges; the node is warned about once."""
        assert len(_diag(lint(_hazard_graph()), "outcome_label_shadowing")) == 1

    def test_inequality_counts_too(self):
        """`outcome!=success` resolves the same overloaded key."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "fix", condition="outcome!=success"),
                Edge("review", "done", label="success"),
                Edge("fix", "review"),
            ],
        )
        assert _diag(lint(g), "outcome_label_shadowing")

    def test_accelerator_stripped_label_still_collides(self):
        """The engine strips accelerators before matching; so does this rule."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "fix", condition="outcome=retry"),
                Edge("review", "done", label="[R] Retry"),
                Edge("fix", "review"),
            ],
        )
        assert _diag(lint(g), "outcome_label_shadowing")

    # -- the narrowings, each load-bearing -----------------------------------

    def test_silent_on_an_outcome_condition_with_no_labelled_edge(self):
        """Routing on `outcome=success` in a label-free graph is normal and correct.

        This is the shape the issue's condition (1) would have flagged; it is
        23 of this repository's 63 shipped graphs.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "done", condition="outcome=success"),
                Edge("review", "fix", condition="outcome=fail"),
                Edge("fix", "review"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    def test_silent_when_the_status_word_label_is_on_a_conditional_edge(self):
        """Spec §3.3 Step 2 skips conditional edges, so such a label is inert.

        This is the shipped convention -- `gate -> fix
        [condition="context.tool.last_line=fail", label="fail"]` -- and is why
        the issue's own suggested conservative form still fires on 6 graphs.
        """
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "done", condition="outcome=success"),
                Edge("review", "fix", condition="outcome=fail", label="fail"),
                Edge("fix", "review"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    def test_silent_when_the_label_is_outside_the_status_vocabulary(self):
        """`label="needs_work"` cannot be confused with a status value."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "done", condition="outcome=success"),
                Edge("review", "fix", label="needs_work"),
                Edge("fix", "review"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    def test_silent_when_the_label_edge_belongs_to_a_different_node(self):
        """`select_edge` resolves a node's outcome against THAT node's out-edges."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "other": _box("other", prompt="o"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "done", condition="outcome=retry"),
                Edge("review", "other"),
                Edge("other", "fix", label="retry"),
                Edge("fix", "review"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    def test_silent_on_a_non_outcome_condition_key(self):
        """`context.tool.last_line=fail` does not go through the overloaded key."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _gate("gate"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate"),
                Edge("gate", "done", condition="context.tool.last_line=green"),
                Edge("gate", "fix", label="fail"),
                Edge("fix", "gate"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    def test_silent_on_an_unlabelled_unconditional_edge(self):
        """An unconditional edge with no label is not evidence of label steering."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "review": _box("review", prompt="r"),
                "fix": _box("fix", prompt="f"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "review"),
                Edge("review", "done", condition="outcome=success"),
                Edge("review", "fix"),
                Edge("fix", "review"),
            ],
        )
        assert not _diag(lint(g), "outcome_label_shadowing")

    # -- lint-only, WARNING severity ----------------------------------------

    def test_rule_is_lint_only_and_warning_severity(self):
        """`validate()` stays silent; `lint()` warns. No graph starts failing at run time."""
        g = _hazard_graph()
        assert not _diag(validate(g), "outcome_label_shadowing")
        diags = _diag(lint(g), "outcome_label_shadowing")
        assert diags and all(d.severity == "WARNING" for d in diags)

    # -- vocabulary is derived, not hand-copied ------------------------------

    def test_status_vocabulary_tracks_stage_status(self):
        """A new StageStatus value cannot silently leave TOPO-009 behind."""
        from amplifier_module_loop_pipeline.edge_selection import _normalize_label
        from amplifier_module_loop_pipeline.outcome import StageStatus
        from amplifier_module_loop_pipeline.validation import _STATUS_WORDS

        assert _STATUS_WORDS == {_normalize_label(s.value) for s in StageStatus}


class TestOutcomeLabelShadowingCalibration:
    """TOPO-009 must be silent on every graph this repository ships.

    A lint rule that fires on shipped, correct graphs is wolf-crying: authors
    learn to ignore it and it stops protecting anything.  This sweep pins the
    measurement the rule's narrowing was derived from, over EVERY `.dot` in the
    repository -- `examples/`, `.github/`, `skills/`, and every test fixture --
    not just the `examples/` corpus `test_examples_lint_clean.py` covers.
    """

    @staticmethod
    def _repo_dots() -> list:
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        if not (root / "examples").is_dir():
            return []
        return sorted(p for p in root.rglob("*.dot") if ".git/" not in str(p))

    def test_silent_on_every_shipped_dot(self):
        import pytest

        from amplifier_module_loop_pipeline.dot_parser import parse_dot

        dots = self._repo_dots()
        if not dots:
            pytest.skip("repository .dot corpus not present (installed-package run)")

        fired = []
        for path in dots:
            graph = parse_dot(path.read_text(encoding="utf-8"))
            for d in _diag(lint(graph), "outcome_label_shadowing"):
                fired.append(f"{path}: {d.message}")

        assert not fired, (
            f"TOPO-009 fired on {len(fired)} of {len(dots)} shipped graphs -- it is "
            f"wolf-crying and must be narrowed until it is silent on all of them:\n"
            + "\n".join(fired)
        )

    def test_the_corpus_is_actually_large_enough_to_mean_something(self):
        """Guard the guard: a sweep over an empty corpus proves nothing."""
        import pytest

        dots = self._repo_dots()
        if not dots:
            pytest.skip("repository .dot corpus not present (installed-package run)")
        assert len(dots) >= 60, f"only {len(dots)} .dot files found; corpus shrank?"

    def test_the_rejected_broader_shapes_really_do_fire_on_shipped_graphs(self):
        """Why the narrowing exists, measured rather than asserted.

        The issue proposed keying on an `outcome=<status word>` condition plus a
        status-word edge `label=` anywhere in the graph.  Both of those broader
        forms are demonstrably noisy on this repository's own corpus -- that
        measurement is what moved the rule to per-node, Step-2-eligible label
        edges.  If a future change ever makes the broad forms silent too, this
        test goes red and the narrowing should be revisited.
        """
        import pytest

        from amplifier_module_loop_pipeline.conditions import parse_condition
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.edge_selection import _normalize_label
        from amplifier_module_loop_pipeline.validation import _STATUS_WORDS

        dots = self._repo_dots()
        if not dots:
            pytest.skip("repository .dot corpus not present (installed-package run)")

        condition_only = 0
        plus_any_status_label = 0
        for path in dots:
            graph = parse_dot(path.read_text(encoding="utf-8"))
            has_cond = any(
                key == "outcome"
                and op in ("=", "!=")
                and _normalize_label(val) in _STATUS_WORDS
                for e in graph.edges
                for key, op, val in parse_condition(e.condition or "")
            )
            has_label = any(
                e.label and _normalize_label(e.label) in _STATUS_WORDS
                for e in graph.edges
            )
            condition_only += 1 if has_cond else 0
            plus_any_status_label += 1 if (has_cond and has_label) else 0

        assert condition_only >= 20, (
            f"the issue's condition (1) alone fires on only {condition_only} graphs now "
            f"(was 23 of 63) -- recheck whether TOPO-009 still needs narrowing"
        )
        assert plus_any_status_label >= 5, (
            f"the issue's suggested conservative form fires on only "
            f"{plus_any_status_label} graphs now (was 6 of 63) -- recheck the narrowing"
        )


# ---------------------------------------------------------------------------
# TOPO-010: folder_dot_file_absent
# ---------------------------------------------------------------------------


def _folder(node_id: str = "child", dot_file: str = "child.dot") -> Node:
    return Node(id=node_id, shape="folder", attrs={"dot_file": dot_file})


def _folder_graph(source_dir: str, dot_file: str = "child.dot") -> Graph:
    return _graph(
        nodes={
            "start": _mdiamond(),
            "child": _folder(dot_file=dot_file),
            "exit": _msquare(),
        },
        edges=[
            Edge(from_node="start", to_node="child"),
            Edge(from_node="child", to_node="exit"),
        ],
        source_dir=source_dir,
    )


class TestFolderDotFileAbsent:
    """TOPO-010 (issue #200): advisory warning for a STATIC relative dot_file=.

    The rule exists to give an author with a typo'd path the same information
    the node-entry ChildDotResolutionError gives them, one step earlier.  It
    is advisory because the linter cannot distinguish a typo from a child
    graph an upstream node writes during the run.
    """

    def test_absent_static_relative_target_warns(self, tmp_path):
        diags = _diag(lint(_folder_graph(str(tmp_path))), "folder_dot_file_absent")
        assert len(diags) == 1
        assert diags[0].node_id == "child"
        assert 'dot_file="child.dot"' in diags[0].message
        assert str(tmp_path / "child.dot") in diags[0].message

    def test_severity_is_warning_never_error(self, tmp_path):
        """ERROR here would block every composition graph from starting."""
        diags = _diag(lint(_folder_graph(str(tmp_path))), "folder_dot_file_absent")
        assert diags and all(d.severity == "WARNING" for d in diags)

    def test_rule_is_lint_only_validate_stays_silent(self, tmp_path):
        """Admission stays LAZY: validate() must not learn about existence."""
        g = _folder_graph(str(tmp_path))
        assert not _diag(validate(g), "folder_dot_file_absent")

    def test_present_target_does_not_warn(self, tmp_path):
        (tmp_path / "child.dot").write_text("digraph c { a [shape=Mdiamond] }")
        diags = _diag(lint(_folder_graph(str(tmp_path))), "folder_dot_file_absent")
        assert not diags

    def test_absolute_target_is_skipped(self, tmp_path):
        """A lint-time absolute path says nothing about the run-time machine."""
        g = _folder_graph(str(tmp_path), dot_file=str(tmp_path / "nope" / "child.dot"))
        assert not _diag(lint(g), "folder_dot_file_absent")

    def test_variable_target_is_skipped(self, tmp_path):
        """`$var` targets are the exact shape a composition graph uses."""
        g = _folder_graph(str(tmp_path), dot_file="$target_dir/.gen/child.dot")
        assert not _diag(lint(g), "folder_dot_file_absent")

    def test_empty_source_dir_is_skipped(self):
        """An inline DOT source has no backing file -- no honest base to resolve."""
        g = _folder_graph("", dot_file="child.dot")
        assert not _diag(lint(g), "folder_dot_file_absent")

    def test_non_folder_nodes_are_ignored(self, tmp_path):
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": _box(attrs={"dot_file": "child.dot"}),
                "exit": _msquare(),
            },
            edges=[
                Edge(from_node="start", to_node="work"),
                Edge(from_node="work", to_node="exit"),
            ],
            source_dir=str(tmp_path),
        )
        assert not _diag(lint(g), "folder_dot_file_absent")

    def test_type_pipeline_node_is_covered(self, tmp_path):
        """type="pipeline" is the non-shape spelling of the same handler."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "child": Node(
                    id="child",
                    shape="component",
                    type="pipeline",
                    attrs={"dot_file": "child.dot"},
                ),
                "exit": _msquare(),
            },
            edges=[
                Edge(from_node="start", to_node="child"),
                Edge(from_node="child", to_node="exit"),
            ],
            source_dir=str(tmp_path),
        )
        assert len(_diag(lint(g), "folder_dot_file_absent")) == 1

    def test_write_then_run_graph_is_advisory_only(self, tmp_path):
        """The warning may fire on a legitimate composition graph -- and must

        never be more than advisory when it does.  This is the case the rule
        genuinely cannot tell apart from a typo, which is exactly why it is a
        WARNING and lives only in lint().
        """
        g = _folder_graph(str(tmp_path), dot_file="gen/child.dot")
        diags = lint(g)
        assert _diag(diags, "folder_dot_file_absent")
        # No ERROR anywhere -- the CLI's exit-code contract stays 0.
        assert not [d for d in diags if d.severity == "ERROR"]
