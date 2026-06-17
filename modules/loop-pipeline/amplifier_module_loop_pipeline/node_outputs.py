"""Handler-side output inference table for node failure propagation.

M1 (R12): Defines which context keys each handler type contributes on a
successful execution.  Pipeline authors may also declare keys explicitly via
the node's ``outputs="key1,key2"`` DOT attribute.  Effective output set is
``inferred ∪ explicit``.

SC-2 (COE Phase 4 resolution) — closed inference table:
  ``tool``      → ``tool.output``, ``tool.last_line``
                  (+ keys from ``parse_json`` when declared via ``outputs=``)
  ``wait.human``→ absent by design (R12 R12.5): emits mode-specific keys
                  (``human.gate.selected`` or ``human.gate.text``) that cannot
                  be statically inferred without false-positive
                  PIPELINE_NODE_CONTRACT_VIOLATION events
  ``parallel``  → ``branch.{idx}.outcome`` (per outgoing branch, computed
                  dynamically from the graph's outgoing edges at build time)
  other         → empty set (use explicit ``outputs=`` declaration)

Only these handlers contribute inferred keys.  Any other handler type
contributes nothing unless the author explicitly declares ``outputs=``.
This is intentional: magic inference creates hidden contracts.

Usage::

    from amplifier_module_loop_pipeline.node_outputs import build_output_table

    # At engine initialisation time:
    output_table = build_output_table(graph)
    # output_table: dict[node_id, frozenset[output_key]]
"""

from __future__ import annotations

from .graph import Graph

# ---------------------------------------------------------------------------
# Closed handler inference table (SC-2)
# ---------------------------------------------------------------------------

#: Maps handler-type key (as returned by HandlerRegistry.get) to the set of
#: context keys that handler contributes on success.  This is the *closed*
#: table: adding a new slot requires explicit SC-2 revision.
HANDLER_INFERRED_OUTPUTS: dict[str, frozenset[str]] = {
    # tool handler (shape=parallelogram or type=tool)
    "tool": frozenset({"tool.output", "tool.last_line"}),
    # human gate handler (shape=hexagon or type=wait.human):
    #   wait.human is intentionally absent from inference. The actual
    #   handler emits a runtime-dependent set: always {human.gate.label,
    #   last_response, last_stage}, plus EITHER human.gate.selected (in
    #   selection mode) OR human.gate.text (in text-input mode). A
    #   static inference produces false-positive PIPELINE_NODE_CONTRACT_VIOLATION
    #   events for the mode-specific key. R12 R12.5 fix: drop the
    #   inferred set; pipeline authors who want contract checking on a
    #   human-gate node should declare `outputs="..."` explicitly. The
    #   M2 eager-scan substitution path is unaffected — it consumes the
    #   actual emitted context, not the inferred set.
    # parallel handler: branch keys are dynamic — see build_output_table()
    # all other handlers: empty
}

# ---------------------------------------------------------------------------
# Substitutable attribute names (M2 eager scan registry)
# ---------------------------------------------------------------------------

#: Attribute names that may carry ``${key}`` / ``$key`` token references.
#: The engine's eager pre-execution scan reads exactly these attributes.
#: Adding a new substitutable attribute is a one-line addition here.
SUBSTITUTABLE_ATTRS: frozenset[str] = frozenset(
    {
        "tool_command",
        "prompt",
        "description",
        "tool_env",
    }
)


# ---------------------------------------------------------------------------
# Output table builder
# ---------------------------------------------------------------------------


def _resolve_handler_type(node_type: str, node_shape: str) -> str:
    """Map node type/shape to the canonical handler-type key."""
    from .validation import SHAPE_TO_HANDLER

    if node_type:
        return node_type
    return SHAPE_TO_HANDLER.get(node_shape, "codergen")


def build_output_table(graph: Graph) -> dict[str, frozenset[str]]:
    """Build a mapping from node_id → effective output key set.

    Called once at engine initialisation.  For each node in *graph*:

    1. Determine the handler type (``type`` attr or shape mapping).
    2. Look up static inferred outputs from :data:`HANDLER_INFERRED_OUTPUTS`.
    3. For ``parallel`` handler nodes, compute dynamic ``branch.{idx}.outcome``
       keys from the graph's outgoing edge list.
    4. Parse the node's ``outputs=`` attribute for explicit declarations.
    5. Return the union of inferred + explicit keys.

    Args:
        graph: The parsed pipeline graph.

    Returns:
        Dict mapping every node_id to its effective output frozenset.
        Nodes with no declared or inferred outputs map to an empty frozenset.
    """
    table: dict[str, frozenset[str]] = {}

    for node_id, node in graph.nodes.items():
        handler_type = _resolve_handler_type(node.type, node.shape)

        # Static inferred outputs
        inferred: frozenset[str] = HANDLER_INFERRED_OUTPUTS.get(
            handler_type, frozenset()
        )

        # Dynamic parallel branch keys
        if handler_type == "parallel":
            branches = graph.outgoing_edges(node_id)
            branch_keys: frozenset[str] = frozenset(
                f"branch.{idx}.outcome" for idx in range(len(branches))
            )
            inferred = inferred | branch_keys

        # Explicit outputs= declaration
        outputs_attr = node.attrs.get("outputs", "") or ""
        explicit: frozenset[str] = frozenset()
        if outputs_attr:
            explicit = frozenset(
                k.strip() for k in str(outputs_attr).split(",") if k.strip()
            )

        table[node_id] = inferred | explicit

    return table
