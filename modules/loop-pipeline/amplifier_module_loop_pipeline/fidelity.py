"""Context fidelity modes for controlling inter-node context carryover.

Controls how much prior conversation and state is carried into each
node's LLM session. This is the core mechanism for managing context
window usage across multi-stage pipelines.

Spec coverage: FID-001–010, Section 5.4.

Modes:
    full           – Fresh spawn, prior node exchanges replayed as ``parent_messages``.
                     NOT session reuse: continuity is a transcript replayed into a new
                     spawn, at node-exchange granularity, branch-local. See
                     ``backend.py`` module docstring and EXTENSIONS.md §12–13.
    truncate       – Fresh session, minimal: only graph goal and run ID.
    compact        – Fresh session, structured bullet-point summary.
    summary:low    – Fresh session, ~600 token text summary.
    summary:medium – Fresh session, ~1500 token text summary.
    summary:high   – Fresh session, ~3000 token text summary.

Resolution precedence (highest to lowest):
    1. Edge fidelity attribute
    2. Target node fidelity attribute
    3. Graph default_fidelity attribute
    4. System default: "compact"

Thread resolution (for full mode):
    1. Target node thread_id
    2. Edge thread_id
    3. Graph-level default_thread_id
    4. Previous node ID (fallback)
"""

from __future__ import annotations

import logging

from .context import PipelineContext
from .graph import Edge, Graph, Node
from .outcome import Outcome

logger = logging.getLogger(__name__)

# All valid fidelity mode strings
VALID_FIDELITY_MODES: frozenset[str] = frozenset(
    {
        "full",
        "truncate",
        "compact",
        "summary:low",
        "summary:medium",
        "summary:high",
    }
)

_DEFAULT_FIDELITY = "compact"

#: Reserved ONE-HOP context key carrying the spec §5.3 rule-6 resume fidelity
#: cap.  The engine sets it immediately before the first node executed after a
#: resume (and only when that node resolves to ``full``), and clears it as soon
#: as that node's handler returns — so it can never leak into a later hop or
#: into a checkpoint's context snapshot.  Backends honor it by substituting the
#: capped mode for ``full``; see ``backend.py`` step 3b.
#:
#: It lives here rather than in ``engine.py`` so both the engine and the
#: backends can import it without a cycle (engine -> handlers -> backend).
RESUME_FIDELITY_CAP_KEY: str = "resume.fidelity_cap"


def resolve_fidelity(
    node: Node,
    incoming_edge: Edge | None,
    graph: Graph,
) -> str:
    """Resolve the fidelity mode for a node.

    Precedence: edge > node > graph default > system default ("compact").

    Args:
        node: The target node.
        incoming_edge: The edge leading to this node (if any).
        graph: The pipeline graph.

    Returns:
        A valid fidelity mode string.
    """
    # 1. Edge fidelity (highest priority)
    if incoming_edge is not None:
        edge_fidelity = incoming_edge.attrs.get("fidelity")
        if edge_fidelity:
            if edge_fidelity in VALID_FIDELITY_MODES:
                return edge_fidelity
            # M-22: warn on invalid fidelity instead of silent fallback
            logger.warning(
                "Invalid fidelity mode '%s' on edge %s->%s, "
                "falling back to '%s'. Valid modes: %s",
                edge_fidelity,
                incoming_edge.from_node,
                incoming_edge.to_node,
                _DEFAULT_FIDELITY,
                ", ".join(sorted(VALID_FIDELITY_MODES)),
            )

    # 2. Node fidelity
    node_fidelity = node.attrs.get("fidelity")
    if node_fidelity:
        if node_fidelity in VALID_FIDELITY_MODES:
            return node_fidelity
        # M-22: warn on invalid fidelity instead of silent fallback
        logger.warning(
            "Invalid fidelity mode '%s' on node '%s', "
            "falling back to '%s'. Valid modes: %s",
            node_fidelity,
            node.id,
            _DEFAULT_FIDELITY,
            ", ".join(sorted(VALID_FIDELITY_MODES)),
        )

    # 3. Graph default_fidelity
    graph_fidelity = graph.graph_attrs.get("default_fidelity")
    if graph_fidelity:
        if graph_fidelity in VALID_FIDELITY_MODES:
            return graph_fidelity
        # M-22: warn on invalid fidelity instead of silent fallback
        logger.warning(
            "Invalid graph default_fidelity '%s', "
            "falling back to '%s'. Valid modes: %s",
            graph_fidelity,
            _DEFAULT_FIDELITY,
            ", ".join(sorted(VALID_FIDELITY_MODES)),
        )

    # 4. System default
    return _DEFAULT_FIDELITY


def resolve_thread_key(
    node: Node,
    incoming_edge: Edge | None,
    graph: Graph,
    previous_node_id: str | None = None,
) -> str:
    """Resolve the thread key for session reuse (full fidelity).

    Precedence: node thread_id > edge thread_id > graph default > previous node ID.

    Args:
        node: The target node.
        incoming_edge: The edge leading to this node (if any).
        graph: The pipeline graph.
        previous_node_id: ID of the previously executed node.

    Returns:
        A thread key string.
    """
    # 1. Node thread_id (highest priority)
    node_thread = node.attrs.get("thread_id")
    if node_thread:
        return str(node_thread)

    # 2. Edge thread_id
    if incoming_edge is not None:
        edge_thread = incoming_edge.attrs.get("thread_id")
        if edge_thread:
            return str(edge_thread)

    # 3. Graph-level default_thread_id
    graph_thread = graph.graph_attrs.get("default_thread_id")
    if graph_thread:
        return str(graph_thread)

    # 4. Fallback: previous node ID, or own node ID
    return previous_node_id or node.id


def build_preamble(
    fidelity: str,
    context: PipelineContext,
    completed_nodes: dict[str, Outcome],
) -> str:
    """Build a context preamble string for a fresh session.

    The preamble synthesizes prior execution state for nodes that
    don't use full fidelity (which carries prior state as replayed
    ``parent_messages`` on a fresh spawn instead of as a preamble).

    Args:
        fidelity: The resolved fidelity mode.
        context: The current pipeline context.
        completed_nodes: Map of node_id -> Outcome for completed nodes.

    Returns:
        A preamble string. Empty for "full" mode.
    """
    if fidelity == "full":
        return ""

    if fidelity == "truncate":
        return _build_truncate_preamble(context)

    if fidelity == "compact":
        return _build_compact_preamble(context, completed_nodes)

    if fidelity.startswith("summary:"):
        level = fidelity.split(":", 1)[1]
        return _build_summary_preamble(level, context, completed_nodes)

    # M-22: Warn on unrecognized mode in build_preamble, fall back to compact
    logger.warning(
        "Unrecognized fidelity mode '%s' in build_preamble, "
        "falling back to compact. Valid modes: %s",
        fidelity,
        ", ".join(sorted(VALID_FIDELITY_MODES)),
    )
    return _build_compact_preamble(context, completed_nodes)


def _build_truncate_preamble(context: PipelineContext) -> str:
    """Minimal preamble: graph goal and run ID only."""
    goal = context.get_string("graph.goal", "No goal set")
    run_id = context.get_string("internal.run_id", "unknown")
    return f"Goal: {goal}\nRun ID: {run_id}"


def _build_compact_preamble(
    context: PipelineContext,
    completed_nodes: dict[str, Outcome],
) -> str:
    """Structured bullet-point summary of execution state."""
    lines: list[str] = []

    # Goal
    goal = context.get_string("graph.goal", "No goal set")
    lines.append(f"Goal: {goal}")
    lines.append("")

    # Completed stages
    if completed_nodes:
        lines.append("Completed stages:")
        for node_id, outcome in completed_nodes.items():
            status = outcome.status.value
            note = f" - {outcome.notes}" if outcome.notes else ""
            lines.append(f"  - {node_id}: {status}{note}")
        lines.append("")

    # Last LLM response (spec Section 5.1 built-in key, already truncated by handler)
    last_response = context.get("last_response")
    if last_response:
        lines.append("Last response:")
        lines.append(f"  {last_response}")
        lines.append("")

    # Key context values (context.* namespace)
    snapshot = context.snapshot()
    ctx_values = {k: v for k, v in sorted(snapshot.items()) if k.startswith("context.")}
    if ctx_values:
        lines.append("Context values:")
        for key, value in ctx_values.items():
            lines.append(f"  - {key}: {value}")

    return "\n".join(lines)


def _build_summary_preamble(
    level: str,
    context: PipelineContext,
    completed_nodes: dict[str, Outcome],
) -> str:
    """Text summary at varying detail levels."""
    goal = context.get_string("graph.goal", "No goal set")
    lines: list[str] = [f"Goal: {goal}", ""]

    if level == "low":
        # ~600 tokens: brief summary with minimal event counts
        if completed_nodes:
            success_count = sum(1 for o in completed_nodes.values() if o.is_success)
            total = len(completed_nodes)
            lines.append(
                f"Progress: {success_count}/{total} stages completed successfully."
            )
            # List just the stage names
            stage_names = ", ".join(completed_nodes.keys())
            lines.append(f"Stages: {stage_names}")

    elif level == "medium":
        # ~1500 tokens: recent stage outcomes and active context
        if completed_nodes:
            lines.append("Stage outcomes:")
            for node_id, outcome in completed_nodes.items():
                lines.append(f"  - {node_id}: {outcome.status.value}")
                if outcome.notes:
                    lines.append(f"    Notes: {outcome.notes}")
            lines.append("")

        # Last LLM response (spec Section 5.1 built-in key)
        last_response = context.get("last_response")
        if last_response:
            lines.append("Last response:")
            lines.append(f"  {last_response}")
            lines.append("")

        # Include context.* values
        snapshot = context.snapshot()
        ctx_values = {
            k: v for k, v in sorted(snapshot.items()) if k.startswith("context.")
        }
        if ctx_values:
            lines.append("Active context:")
            for key, value in ctx_values.items():
                lines.append(f"  - {key}: {value}")

    elif level == "high":
        # ~3000 tokens: comprehensive detail including failures
        if completed_nodes:
            lines.append("Detailed stage outcomes:")
            for node_id, outcome in completed_nodes.items():
                lines.append(f"  - {node_id}: {outcome.status.value}")
                if outcome.notes:
                    lines.append(f"    Notes: {outcome.notes}")
                if outcome.failure_reason:
                    lines.append(f"    Failure: {outcome.failure_reason}")
                if outcome.context_updates:
                    for k, v in outcome.context_updates.items():
                        lines.append(f"    Update: {k} = {v}")
            lines.append("")

        # Last LLM response (spec Section 5.1 built-in key)
        last_response = context.get("last_response")
        if last_response:
            lines.append("Last response:")
            lines.append(f"  {last_response}")
            lines.append("")

        # Include all context.* values
        snapshot = context.snapshot()
        ctx_values = {
            k: v for k, v in sorted(snapshot.items()) if k.startswith("context.")
        }
        if ctx_values:
            lines.append("Full context state:")
            for key, value in ctx_values.items():
                lines.append(f"  - {key}: {value}")

    return "\n".join(lines)
