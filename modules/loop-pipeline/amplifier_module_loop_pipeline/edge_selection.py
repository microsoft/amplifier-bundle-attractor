"""Five-step edge selection algorithm for pipeline routing.

After a node completes, the engine selects the next edge using a
deterministic priority order:

1. Condition-matching edges (evaluated against outcome + context)
2. Preferred label match (from outcome.preferred_label)
3. Suggested next IDs (from outcome.suggested_next_ids)
4. Highest weight among unconditional edges
5. Lexical tiebreak on target node ID

Spec coverage: ESEL-001–010, Section 3.3.
"""

from __future__ import annotations

import re

from .conditions import evaluate_condition
from .context import PipelineContext
from .graph import Edge, Graph
from .outcome import Outcome, StageStatus


def select_edge(
    node_id: str,
    outcome: Outcome,
    context: PipelineContext,
    graph: Graph,
) -> Edge | None:
    """Select the next edge from a node's outgoing edges.

    Returns None if no outgoing edges exist.
    """
    edges = graph.outgoing_edges(node_id)
    if not edges:
        return None

    # Step 1: Condition-matching edges
    condition_matched = [
        e
        for e in edges
        if e.condition and evaluate_condition(e.condition, outcome, context)
    ]
    if condition_matched:
        return _best_by_weight_then_lexical(condition_matched)

    # Step 2: Preferred label match
    if outcome.preferred_label:
        norm_pref = _normalize_label(outcome.preferred_label)
        for e in edges:
            if e.label and _normalize_label(e.label) == norm_pref:
                return e

    # Step 3: Suggested next IDs
    if outcome.suggested_next_ids:
        for suggested_id in outcome.suggested_next_ids:
            for e in edges:
                if e.to_node == suggested_id:
                    return e

    # Step 4 & 5: Weight with lexical tiebreak (unconditional edges only)
    #
    # Spec §3.7 fail-fast intent: a FAIL outcome does NOT traverse unconditional
    # edges to default-routing nodes.  The engine will then check retry_target /
    # fallback_retry_target and terminate if neither is configured.
    #
    # Pipeline authors who want fail-forward have three explicit opt-in paths
    # (all remain fully supported):
    #   - continue_on_fail="true" on the node  → engine converts FAIL→SUCCESS
    #     before calling select_edge, so unconditional edges work normally
    #   - runs_on=always / runs_on=failure on a DOWNSTREAM node  → the
    #     downstream node's attribute opts that edge in for FAIL routing;
    #     select_edge routes there and the engine's skip-gate decides execution
    #   - condition="outcome=fail" edge  → matched in Step 1 above
    if outcome.status != StageStatus.FAIL:
        unconditional = [e for e in edges if not e.condition]
        if unconditional:
            return _best_by_weight_then_lexical(unconditional)
    else:
        # FAIL: only follow unconditional edges to nodes that explicitly opt in
        # to failure routing (runs_on=always or runs_on=failure).  Default nodes
        # (runs_on=success) are NOT reached — that is the fail-fast behavior.
        fail_forward = [
            e
            for e in edges
            if not e.condition
            and _target_runs_on(e.to_node, graph) in ("always", "failure")
        ]
        if fail_forward:
            return _best_by_weight_then_lexical(fail_forward)

    # Spec §3.3 final step: RETURN NONE.
    # Reached when:
    #   (a) outcome is FAIL and no downstream node opts in via runs_on, or
    #   (b) no unconditional edges exist and no conditional edge matched.
    # The engine halts this branch with a FAIL outcome.
    return None


def _target_runs_on(node_id: str, graph: Graph) -> str:
    """Return the runs_on value of the target node (default 'success')."""
    node = graph.nodes.get(node_id)
    if node is None:
        return "success"
    raw = node.attrs.get("runs_on", "success") or "success"
    return str(raw).strip().lower()


def select_all_matching_edges(
    node_id: str,
    outcome: Outcome,
    context: PipelineContext,
    graph: Graph,
) -> list[Edge]:
    """Return ALL condition-matching edges from a node's outgoing edges.

    Unlike select_edge() which returns the single best edge, this returns
    every edge whose condition evaluates to True. Used by the engine to
    detect multi-edge fan-out patterns (parallel execution).

    Returns an empty list if no edges have matching conditions.
    """
    edges = graph.outgoing_edges(node_id)
    if not edges:
        return []

    # All condition-matching edges
    condition_matched = [
        e
        for e in edges
        if e.condition and evaluate_condition(e.condition, outcome, context)
    ]
    return condition_matched


def _best_by_weight_then_lexical(edges: list[Edge]) -> Edge:
    """Sort by weight descending, then target node ID ascending."""
    return sorted(edges, key=lambda e: (-e.weight, e.to_node))[0]


# Accelerator key patterns: "[Y] Label", "Y) Label", "Y - Label"
_ACCELERATOR_RE = re.compile(r"^\[.\]\s*|^.\)\s*|^.\s*-\s*")


def _normalize_label(label: str) -> str:
    """Normalize a label for matching: lowercase, strip accelerators, trim."""
    s = label.strip().lower()
    s = _ACCELERATOR_RE.sub("", s)
    return s.strip()
