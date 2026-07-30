"""Tests for topological (basin-lint) rules — TOPO-001 through TOPO-005.

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
        """ERROR: context.tool.last_line=X edge without && outcome=success + outcome=fail sibling."""
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
        assert all(d.severity == "ERROR" for d in stale)
        assert any(d.node_id == "stalegate" for d in stale)

    def test_collision_with_outcome_not_success_flagged(self):
        """ERROR: outcome!=success sibling also triggers stale-label check."""
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
