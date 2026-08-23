"""Graph validation and lint rules for Attractor pipelines.

Validates parsed Graph models against the rules defined in
spec Section 7 (Validation and Linting). Produces Diagnostic objects
with severity ERROR (blocks execution) or WARNING (informational).

Spec coverage: LINT-001–018.  TOPO-001–010 are topological basin-lint rules
implemented here beyond the canonical spec; they are lint-only (exposed via
``lint()``, not ``validate()``) and do not change run-time behaviour.
TOPO-009 warns when an ``outcome=<status word>`` edge condition shares a
vocabulary with a ``preferred_label`` the same node can emit -- ``outcome``
resolves the label BEFORE the status here (EXTENSIONS.md §22 / ATX-5).
TOPO-008 is the ``attractor lint`` sibling of the authoring checker's A10
(``examples/authoring/check_authored_pipeline.py``): an evidence gate routing
two different answers into the exit is structurally inert.

CMD-001–002 are command-content lint rules that inspect ``tool_command``
strings for two specific hazard shapes: pipe-masked exit codes (CMD-001) and
always-true trailing sentinels (CMD-002).  Both are lint-only (WARNING
severity) and do not change run-time behaviour.

VOCAB-001 is an inert-vocabulary lint rule: an LLM node that carries no
``prompt=`` at all but does carry an invented spelling the parser keeps and no
handler ever reads (``instruction=``, ``agent=``, ...).  It is lint-only
(WARNING severity) and does not change run-time behaviour.

RENDER-001 and RENDER-002 are render-compliance lint rules.  They read ``graph.dot_source``
with the engine's own tokenizer and flag the two shapes this parser accepts but
GraphViz refuses: an unescaped inner ``"`` that closes an attribute string early
(RENDER-001) and a bare identifier containing ``.`` (RENDER-002).  Such a graph
RUNS -- it just cannot be drawn.  Both are lint-only, WARNING severity, take no
GraphViz dependency, and do not change run-time behaviour.  See
docs/designs/2026-08-23-dot-render-compliance.md.
"""

from __future__ import annotations

import os
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .conditions import evaluate_condition, parse_condition
from .context import PipelineContext

# `_normalize_label` is edge_selection's own label normaliser -- imported (not
# reimplemented) so TOPO-009 asks "is this label a status word?" exactly the
# way spec §3.3 Step 2 asks it at run time, and the two cannot drift.
from .edge_selection import _normalize_label
from .fidelity import VALID_FIDELITY_MODES
from .graph import Edge, Graph, Node, resolve_bool_attr
from .outcome import Outcome, StageStatus
from .stylesheet import parse_stylesheet

# Shape-to-handler-type mapping (spec Section 2.8)
SHAPE_TO_HANDLER: dict[str, str] = {
    "Mdiamond": "start",
    "Msquare": "exit",
    "box": "codergen",
    "diamond": "conditional",
    "hexagon": "wait.human",
    "component": "parallel",
    "tripleoctagon": "parallel.fan_in",
    "parallelogram": "tool",
    "house": "stack.manager_loop",  # experimental — future form TBD
    "folder": "pipeline",
}

# Shapes that map to LLM/codergen handler
_LLM_SHAPES = {"box"}


@dataclass
class Diagnostic:
    """A single validation diagnostic.

    Spec Section 7.1: rule, severity, message, optional node_id/edge/fix.
    """

    rule: str
    severity: str  # "ERROR", "WARNING", "INFO"
    message: str
    node_id: str = ""
    edge: tuple[str, str] | None = None
    fix: str = ""


class ValidationError(Exception):
    """Raised by validate_or_raise when ERROR diagnostics are found."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        messages = [d.message for d in diagnostics if d.severity == "ERROR"]
        super().__init__(f"Validation failed: {'; '.join(messages)}")


def validate(
    graph: Graph,
    extra_rules: list[Callable[[Graph], list[Diagnostic]]] | None = None,
) -> list[Diagnostic]:
    """Run all built-in lint rules against a graph.

    Returns a list of Diagnostic objects. ERROR-severity diagnostics
    indicate the pipeline will not execute.

    Args:
        graph: The graph to validate.
        extra_rules: Optional list of additional validation functions.
            Each function receives a Graph and returns a list of Diagnostics.
            L-19: Spec Section 7.3 ``validate(graph, extra_rules=NONE)``.

    Spec Section 7.3: validate API.
    """
    diags: list[Diagnostic] = []
    _check_start_node(graph, diags)
    _check_terminal_node(graph, diags)
    _check_edge_targets(graph, diags)
    _check_start_no_incoming(graph, diags)
    _check_exit_no_outgoing(graph, diags)
    _check_reachability(graph, diags)
    _check_goal_gate_has_retry(graph, diags)
    _check_prompt_on_llm_nodes(graph, diags)
    _check_condition_syntax(graph, diags)
    _check_stylesheet_syntax(graph, diags)
    _check_type_known(graph, diags)
    _check_shape_resolvable(graph, diags)
    _check_fidelity_valid(graph, diags)
    _check_retry_target_exists(graph, diags)
    _check_response_schema(graph, diags)
    _check_tool_command_handler(graph, diags)
    _check_retry_budgets(graph, diags)

    # L-19: Run user-supplied extra rules
    for rule in extra_rules or []:
        diags.extend(rule(graph))

    return diags


def lint(graph: Graph) -> list[Diagnostic]:
    """Run topological (basin-lint) and command-content rules in addition to structural rules.

    This is the entry point for the ``attractor lint`` CLI command.  It runs
    the full structural ``validate()`` suite plus the ten topological rules
    (TOPO-001–010) that reason about cycle structure, handler semantics,
    evidence-routing patterns and condition-key hazards, plus the two
    command-content rules (CMD-001–002)
    that inspect ``tool_command`` strings for hazard shapes, plus the
    inert-vocabulary rule (VOCAB-001) that catches an LLM node configured
    entirely in attribute spellings the engine does not read.

    All lint-only rules do not change run-time validation behaviour.  Existing
    graphs that execute today will not start failing at ``run`` time because of
    new WARNINGs produced here.

    Exit-code contract (for CLI use):
        ERROR-severity diagnostics → non-zero exit.
        WARNING-only (or clean) → zero exit.

    Returns the combined list of all Diagnostic objects.
    """
    diags = validate(graph)
    _check_dead_conditional_edge(graph, diags)
    _check_stale_label_collision(graph, diags)
    _check_acyclic_graph(graph, diags)
    _check_cycle_no_conditional_exit(graph, diags)
    _check_cycle_no_deterministic_exit(graph, diags)
    _check_fail_routed_to_exit(graph, diags)
    _check_gate_retry_budget_dead(graph, diags)
    _check_inert_evidence_gate(graph, diags)
    _check_outcome_label_shadowing(graph, diags)
    _check_folder_dot_file_absent(graph, diags)
    _check_pipe_masked_exit_code(graph, diags)
    _check_always_true_sentinel(graph, diags)
    _check_inert_prompt_vocabulary(graph, diags)
    _check_unescaped_inner_quote(graph, diags)
    _check_dotted_bare_identifier(graph, diags)
    return diags


def validate_or_raise(graph: Graph) -> list[Diagnostic]:
    """Validate and raise ValidationError if any ERROR diagnostics found.

    Returns non-error diagnostics (warnings/info) on success.

    Spec Section 7.3: validate_or_raise API.
    """
    diags = validate(graph)
    errors = [d for d in diags if d.severity == "ERROR"]
    if errors:
        raise ValidationError(errors)
    return diags


# --- Individual lint rules ---


def _check_start_node(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: start_node — exactly one start node.

    Detected by: shape=Mdiamond, type="start" attr, or id="start".
    """
    start_nodes = [n for n in graph.nodes.values() if n.is_start_node()]
    if len(start_nodes) == 0:
        diags.append(
            Diagnostic(
                rule="start_node",
                severity="ERROR",
                message=(
                    "Pipeline must have exactly one start node "
                    '(shape=Mdiamond, type="start", or id="start")'
                ),
                fix='Add a start node (shape=Mdiamond, type="start" attr, or id="start")',
            )
        )
    elif len(start_nodes) > 1:
        ids = ", ".join(n.id for n in start_nodes)
        diags.append(
            Diagnostic(
                rule="start_node",
                severity="ERROR",
                message=f"Pipeline has {len(start_nodes)} start nodes ({ids}); exactly one is required",
                fix="Remove extra start nodes so only one is detected as a start node",
            )
        )


def _check_terminal_node(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: terminal_node — exactly one exit node (M-11).

    Detected by: shape=Msquare, type="exit" attr, or id="exit"/"end".
    """
    exit_nodes = [n for n in graph.nodes.values() if n.is_exit_node()]
    if len(exit_nodes) == 0:
        diags.append(
            Diagnostic(
                rule="terminal_node",
                severity="ERROR",
                message=(
                    "Pipeline must have exactly one exit node "
                    '(shape=Msquare, type="exit", or id="exit"/"end")'
                ),
                fix='Add an exit node (shape=Msquare, type="exit" attr, or id="exit")',
            )
        )
    elif len(exit_nodes) > 1:
        ids = ", ".join(n.id for n in exit_nodes)
        diags.append(
            Diagnostic(
                rule="terminal_node",
                severity="ERROR",
                message=(
                    f"Pipeline has {len(exit_nodes)} exit nodes ({ids}); "
                    f"exactly one is required"
                ),
                fix="Remove extra exit nodes so only one is detected as an exit node",
            )
        )


def _check_edge_targets(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: edge_target_exists — all edge endpoints must reference existing nodes."""
    node_ids = set(graph.nodes.keys())
    for edge in graph.edges:
        if edge.from_node not in node_ids:
            diags.append(
                Diagnostic(
                    rule="edge_target_exists",
                    severity="ERROR",
                    message=f"Edge source '{edge.from_node}' does not reference an existing node",
                    edge=(edge.from_node, edge.to_node),
                    fix=f"Add a node declaration for '{edge.from_node}'",
                )
            )
        if edge.to_node not in node_ids:
            diags.append(
                Diagnostic(
                    rule="edge_target_exists",
                    severity="ERROR",
                    message=f"Edge target '{edge.to_node}' does not reference an existing node",
                    edge=(edge.from_node, edge.to_node),
                    fix=f"Add a node declaration for '{edge.to_node}'",
                )
            )


def _check_start_no_incoming(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: start_no_incoming — start node must have no incoming edges."""
    start_nodes = [n for n in graph.nodes.values() if n.is_start_node()]
    for start in start_nodes:
        incoming = graph.incoming_edges(start.id)
        if incoming:
            sources = ", ".join(e.from_node for e in incoming)
            diags.append(
                Diagnostic(
                    rule="start_no_incoming",
                    severity="ERROR",
                    message=f"Start node '{start.id}' has incoming edges from: {sources}",
                    node_id=start.id,
                    fix="Remove edges targeting the start node",
                )
            )


def _check_exit_no_outgoing(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: exit_no_outgoing — exit node must have no outgoing edges."""
    exit_nodes = [n for n in graph.nodes.values() if n.is_exit_node()]
    for exit_node in exit_nodes:
        outgoing = graph.outgoing_edges(exit_node.id)
        if outgoing:
            targets = ", ".join(e.to_node for e in outgoing)
            diags.append(
                Diagnostic(
                    rule="exit_no_outgoing",
                    severity="ERROR",
                    message=f"Exit node '{exit_node.id}' has outgoing edges to: {targets}",
                    node_id=exit_node.id,
                    fix="Remove edges originating from the exit node",
                )
            )


def _check_reachability(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: reachability — all nodes reachable from start via BFS."""
    start_nodes = [n for n in graph.nodes.values() if n.is_start_node()]
    if not start_nodes:
        return  # start_node rule already flagged

    start = start_nodes[0]
    visited: set[str] = set()
    queue: deque[str] = deque([start.id])

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        for edge in graph.outgoing_edges(node_id):
            if edge.to_node in graph.nodes:
                queue.append(edge.to_node)

    # Retry/fallback targets are reachable by the engine even without an
    # explicit edge, so include them before flagging orphans.
    for node in graph.nodes.values():
        for attr in ("retry_target", "fallback_retry_target"):
            target = node.attrs.get(attr) or getattr(node, attr, None)
            if target and target in graph.nodes:
                visited.add(target)
    for attr in ("retry_target", "fallback_retry_target"):
        target = graph.graph_attrs.get(attr) or getattr(graph, attr, None)
        if target and target in graph.nodes:
            visited.add(target)

    unreachable = set(graph.nodes.keys()) - visited
    for node_id in sorted(unreachable):
        diags.append(
            Diagnostic(
                rule="reachability",
                severity="ERROR",
                message=f"Node '{node_id}' is not reachable from the start node",
                node_id=node_id,
                fix=f"Add an edge path from start to '{node_id}'",
            )
        )


def _check_goal_gate_has_retry(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: goal_gate_has_retry — goal gates should have a retry mechanism.

    A goal-gate node satisfies this rule when any of the following is true:

    * The node carries a ``retry_target`` or ``fallback_retry_target``
      attribute pointing to a node in the graph.
    * The graph carries a top-level ``retry_target`` attribute.
    * The node has at least one outgoing edge with ``loop_restart=true``,
      which is the canonical retry mechanism for convergence-loop patterns.
    """
    for node in graph.nodes.values():
        if resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate"):
            has_loop_restart_edge = any(
                resolve_bool_attr(e.loop_restart, "loop_restart")
                for e in graph.outgoing_edges(node.id)
            )
            has_retry = bool(
                node.attrs.get("retry_target")
                or node.attrs.get("fallback_retry_target")
                or graph.graph_attrs.get("retry_target")
                or has_loop_restart_edge
            )
            if not has_retry:
                diags.append(
                    Diagnostic(
                        rule="goal_gate_has_retry",
                        severity="WARNING",
                        message=f"Node '{node.id}' has goal_gate=true but no retry_target",
                        node_id=node.id,
                        fix=(
                            "Add retry_target or fallback_retry_target attribute, "
                            "or add an outgoing edge with loop_restart=true"
                        ),
                    )
                )


def _check_prompt_on_llm_nodes(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: prompt_on_llm_nodes — codergen nodes should have prompt or meaningful label."""
    for node in graph.nodes.values():
        # Skip start/exit nodes — they are not LLM nodes regardless of shape
        if node.is_start_node() or node.is_exit_node():
            continue

        # Determine if this is an LLM/codergen node
        handler = node.type or SHAPE_TO_HANDLER.get(node.shape, "codergen")
        if handler != "codergen":
            continue

        has_prompt = bool(node.prompt)
        # label == id means no explicit label was set
        has_explicit_label = node.label != node.id

        if not has_prompt and not has_explicit_label:
            diags.append(
                Diagnostic(
                    rule="prompt_on_llm_nodes",
                    severity="WARNING",
                    message=f"LLM node '{node.id}' has no prompt and no explicit label",
                    node_id=node.id,
                    fix="Add a prompt attribute or a descriptive label",
                )
            )


# All known handler types (values from SHAPE_TO_HANDLER mapping)
_KNOWN_HANDLER_TYPES: frozenset[str] = frozenset(SHAPE_TO_HANDLER.values())


def _effective_handler_type(node: Node) -> str | None:
    """Mirror built-in runtime precedence without blocking custom handlers."""
    explicit_types = (node.type, node.attrs.get("node_type"))
    for explicit_type in explicit_types:
        if explicit_type in _KNOWN_HANDLER_TYPES:
            return explicit_type
    if any(explicit_types):
        return None
    return SHAPE_TO_HANDLER.get(node.shape)


def _check_tool_command_handler(graph: Graph, diags: list[Diagnostic]) -> None:
    """Reject commands that a recognized non-tool handler would silently ignore."""
    for node in graph.nodes.values():
        command = node.attrs.get("tool_command")
        if command is None or not str(command).strip():
            continue

        handler_type = _effective_handler_type(node)
        if handler_type is not None and handler_type != "tool":
            diags.append(
                Diagnostic(
                    rule="tool_command_requires_tool_handler",
                    severity="ERROR",
                    message=(
                        f"Node '{node.id}' has tool_command but resolves to "
                        f"recognized built-in non-tool handler '{handler_type}'"
                    ),
                    node_id=node.id,
                    fix="Use shape=parallelogram or type=tool, or remove tool_command",
                )
            )


def _check_retry_budgets(graph: Graph, diags: list[Diagnostic]) -> None:
    """Reject retry values that cannot safely form an attempt count."""
    for node in graph.nodes.values():
        if node.max_retries is not None and _retry_value(node.max_retries) is None:
            diags.append(
                Diagnostic(
                    rule="retry_budget_non_negative",
                    severity="ERROR",
                    message=(
                        f"Node '{node.id}' has invalid max_retries="
                        f"{node.max_retries!r}; expected a non-negative integer"
                    ),
                    node_id=node.id,
                    fix="Set max_retries to zero or a positive integer",
                )
            )

    if _retry_value(graph.default_max_retry) is None:
        diags.append(
            Diagnostic(
                rule="retry_budget_non_negative",
                severity="ERROR",
                message=(
                    "Graph default_max_retry/default_max_retries must be zero "
                    f"or a positive integer, got {graph.default_max_retry!r}"
                ),
                fix="Set the graph retry default to zero or a positive integer",
            )
        )


def _retry_value(value: object) -> int | None:
    """Return a safe non-negative retry integer, accepting quoted integers."""
    try:
        from .retry import _parse_non_negative_retry_count

        return _parse_non_negative_retry_count(value)
    except ValueError:
        return None


def _check_condition_syntax(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: condition_syntax -- edge condition expressions must parse correctly.

    Validates each non-empty condition by checking clause structure and
    attempting evaluation with dummy values. Catches both exceptions and
    structurally invalid clauses (e.g. empty keys).
    """
    dummy_outcome = Outcome(status=StageStatus.SUCCESS)
    dummy_context = PipelineContext()

    for edge in graph.edges:
        if not edge.condition or not edge.condition.strip():
            continue

        # Structural check: each clause must have a non-empty key
        error_msg = _validate_condition_structure(edge.condition)
        if error_msg:
            diags.append(
                Diagnostic(
                    rule="condition_syntax",
                    severity="ERROR",
                    message=(
                        f"Edge {edge.from_node} -> {edge.to_node}: "
                        f"invalid condition expression '{edge.condition}': {error_msg}"
                    ),
                    edge=(edge.from_node, edge.to_node),
                    fix="Fix the condition expression syntax (supported: key=value, key!=value, &&)",
                )
            )
            continue

        # Runtime check: attempt evaluation
        try:
            evaluate_condition(edge.condition, dummy_outcome, dummy_context)
        except Exception as exc:
            diags.append(
                Diagnostic(
                    rule="condition_syntax",
                    severity="ERROR",
                    message=(
                        f"Edge {edge.from_node} -> {edge.to_node}: "
                        f"invalid condition expression '{edge.condition}': {exc}"
                    ),
                    edge=(edge.from_node, edge.to_node),
                    fix="Fix the condition expression syntax (supported: key=value, key!=value, &&)",
                )
            )


def _validate_condition_structure(condition: str) -> str | None:
    """Check condition clause structure. Returns error message or None if valid."""
    clauses = condition.split("&&")
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if "!=" in clause:
            key, _ = clause.split("!=", maxsplit=1)
            if not key.strip():
                return f"empty key in clause '{clause}'"
        elif "=" in clause:
            key, _ = clause.split("=", maxsplit=1)
            if not key.strip():
                return f"empty key in clause '{clause}'"
    return None


def _check_stylesheet_syntax(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: stylesheet_syntax -- model_stylesheet must parse as valid rules.

    Attempts to parse the stylesheet. If parsing produces no rules from
    non-empty input, the stylesheet has invalid syntax.
    """
    css = graph.model_stylesheet
    if not css or not css.strip():
        return

    try:
        rules = parse_stylesheet(css)
    except Exception as exc:
        diags.append(
            Diagnostic(
                rule="stylesheet_syntax",
                severity="ERROR",
                message=f"model_stylesheet failed to parse: {exc}",
                fix="Fix the stylesheet syntax. Format: selector { property: value; }",
            )
        )
        return

    # If there was non-trivial content but no rules extracted, it's invalid
    if not rules and len(css.strip()) > 5:
        diags.append(
            Diagnostic(
                rule="stylesheet_syntax",
                severity="ERROR",
                message="model_stylesheet contains content but no valid rules were parsed",
                fix="Fix the stylesheet syntax. Format: selector { property: value; }",
            )
        )


def _check_type_known(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: type_known -- node type values should be recognized handler types."""
    for node in graph.nodes.values():
        if not node.type:
            continue  # empty type uses shape-based resolution, always valid
        if node.type not in _KNOWN_HANDLER_TYPES:
            diags.append(
                Diagnostic(
                    rule="type_known",
                    severity="WARNING",
                    message=(
                        f"Node '{node.id}' has unknown type '{node.type}'. "
                        f"Known types: {', '.join(sorted(_KNOWN_HANDLER_TYPES))}"
                    ),
                    node_id=node.id,
                    fix=f"Use a recognized type or register a custom handler for '{node.type}'",
                )
            )


def _check_shape_resolvable(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: shape_resolvable -- nodes without an explicit type must have a known shape.

    When a node has no explicit ``type`` attribute, the engine resolves its
    handler via ``SHAPE_TO_HANDLER[node.shape]``.  If the shape is not in
    that mapping the dispatch layer raises a ``ValueError`` at runtime
    (``HandlerRegistry.get()`` in ``handlers/__init__.py``).

    This rule surfaces that error at lint time -- before any node executes --
    so a pipeline author with a typo'd shape (e.g. ``shape=parallelgram``
    instead of ``shape=parallelogram``) gets an ERROR from ``attractor lint``
    rather than a ``ValueError`` mid-run.

    Conditions checked (all three must hold to emit an ERROR):

    * ``node.type`` is empty -- nodes with an explicit ``type`` attribute use
      type-based dispatch; the ``type_known`` rule already handles unknown
      type values, so this rule must not double-diagnose them.
    * ``node.shape not in SHAPE_TO_HANDLER`` -- the shape is unrecognised.
    * Not a start or exit node -- ``is_start_node()`` / ``is_exit_node()``
      identify these by ``id``, ``type`` attr, or structural shape regardless
      of the ``shape`` field value; they are always valid.

    Severity: ERROR -- the runtime dispatch raise is unconditional; the
    pipeline cannot execute the node.
    """
    for node in graph.nodes.values():
        if node.type:
            continue  # explicit type= uses type-based dispatch; type_known owns that path
        if node.is_start_node() or node.is_exit_node():
            continue  # start/exit nodes are always valid regardless of shape
        if node.shape not in SHAPE_TO_HANDLER:
            diags.append(
                Diagnostic(
                    rule="shape_resolvable",
                    severity="ERROR",
                    message=(
                        f"Node '{node.id}' has no explicit type and its shape "
                        f"'{node.shape}' is not in SHAPE_TO_HANDLER; "
                        f"shape-based dispatch will raise ValueError at runtime. "
                        f"Known shapes: {', '.join(sorted(SHAPE_TO_HANDLER))}."
                    ),
                    node_id=node.id,
                    fix=(
                        f"Correct the shape typo (e.g. 'parallelogram' not 'parallelgram') "
                        f"or add an explicit type= attribute. "
                        f"Known shapes: {', '.join(sorted(SHAPE_TO_HANDLER))}."
                    ),
                )
            )


def _check_fidelity_valid(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: fidelity_valid -- fidelity mode values must be recognized."""
    # Check node-level fidelity
    for node in graph.nodes.values():
        fidelity = node.attrs.get("fidelity")
        if fidelity and fidelity not in VALID_FIDELITY_MODES:
            diags.append(
                Diagnostic(
                    rule="fidelity_valid",
                    severity="WARNING",
                    message=(
                        f"Node '{node.id}' has unrecognized fidelity mode '{fidelity}'. "
                        f"Valid modes: {', '.join(sorted(VALID_FIDELITY_MODES))}"
                    ),
                    node_id=node.id,
                    fix=f"Use one of: {', '.join(sorted(VALID_FIDELITY_MODES))}",
                )
            )

    # Check graph-level default_fidelity
    graph_fidelity = graph.graph_attrs.get("default_fidelity")
    if graph_fidelity and graph_fidelity not in VALID_FIDELITY_MODES:
        diags.append(
            Diagnostic(
                rule="fidelity_valid",
                severity="WARNING",
                message=(
                    f"Graph attribute default_fidelity has unrecognized value '{graph_fidelity}'. "
                    f"Valid modes: {', '.join(sorted(VALID_FIDELITY_MODES))}"
                ),
                fix=f"Use one of: {', '.join(sorted(VALID_FIDELITY_MODES))}",
            )
        )

    # Check edge-level fidelity
    for edge in graph.edges:
        edge_fidelity = edge.attrs.get("fidelity")
        if edge_fidelity and edge_fidelity not in VALID_FIDELITY_MODES:
            diags.append(
                Diagnostic(
                    rule="fidelity_valid",
                    severity="WARNING",
                    message=(
                        f"Edge {edge.from_node} -> {edge.to_node} has unrecognized "
                        f"fidelity mode '{edge_fidelity}'. "
                        f"Valid modes: {', '.join(sorted(VALID_FIDELITY_MODES))}"
                    ),
                    edge=(edge.from_node, edge.to_node),
                    fix=f"Use one of: {', '.join(sorted(VALID_FIDELITY_MODES))}",
                )
            )


def _check_retry_target_exists(graph: Graph, diags: list[Diagnostic]) -> None:
    """LINT: retry_target_exists -- retry targets must reference existing nodes."""
    node_ids = set(graph.nodes.keys())

    # Check node-level retry targets
    for node in graph.nodes.values():
        for attr_name in ("retry_target", "fallback_retry_target"):
            target = node.attrs.get(attr_name)
            if target and target not in node_ids:
                diags.append(
                    Diagnostic(
                        rule="retry_target_exists",
                        severity="WARNING",
                        message=(
                            f"Node '{node.id}' has {attr_name}='{target}' "
                            f"but no node with ID '{target}' exists"
                        ),
                        node_id=node.id,
                        fix=f"Set {attr_name} to a valid node ID or remove it",
                    )
                )

    # Check graph-level retry targets
    for attr_name in ("retry_target", "fallback_retry_target"):
        target = graph.graph_attrs.get(attr_name)
        if target and target not in node_ids:
            diags.append(
                Diagnostic(
                    rule="retry_target_exists",
                    severity="WARNING",
                    message=(
                        f"Graph attribute {attr_name}='{target}' "
                        f"references nonexistent node '{target}'"
                    ),
                    fix=f"Set graph {attr_name} to a valid node ID or remove it",
                )
            )


def _check_response_schema(graph: Graph, diags: list[Diagnostic]) -> None:
    """EXT-23: response_schema values must be dicts after apply_transforms resolves them.

    This is a defensive post-transform lint: ``apply_transforms()`` calls
    ``resolve_response_schemas()`` which raises loudly on bad values, so
    under normal execution flow this rule fires only if the graph was
    constructed programmatically with an unresolved string value or if
    transforms were intentionally skipped.

    EXTENSIONS.md §23 — response_schema Node Attribute (Structured Output).
    """
    for node in graph.nodes.values():
        rs = node.response_schema
        if rs is None:
            continue
        if not isinstance(rs, dict):
            diags.append(
                Diagnostic(
                    rule="response_schema_valid",
                    severity="ERROR",
                    message=(
                        f"Node '{node.id}': response_schema must be a JSON object "
                        f"(dict) after apply_transforms() resolution, "
                        f"got {type(rs).__name__!r}. "
                        f"Ensure apply_transforms() ran before validate(), or "
                        f"provide a dict directly when constructing nodes programmatically."
                    ),
                    node_id=node.id,
                    fix=(
                        "Provide inline JSON starting with '{' or a valid path to "
                        "a JSON schema file as the response_schema attribute value"
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Topological (basin-lint) rules — TOPO-001 through TOPO-010
#
# These rules reason about cycle structure and handler semantics, not just
# graph topology.  They are exposed via ``lint()`` (not ``validate()``) so
# they remain lint-only and do not change run-time validation behaviour.
#
# Every rule is traceable to a real, observed failure mode (dead corrective
# edges shipped in 8 examples; the stale-label collision; acyclic "attractor"
# pipelines).  Speculative rules are intentionally excluded.
#
# Condition expressions are parsed with ``conditions.parse_condition`` — the
# same grammar entry point the runtime evaluator uses — so lint analysis and
# engine routing cannot drift apart.
# ---------------------------------------------------------------------------

# Shape set for diamond (ConditionalHandler) nodes.
_DIAMOND_SHAPES: frozenset[str] = frozenset({"diamond"})

# Shape set for parallelogram (ToolHandler) nodes.
_TOOL_SHAPES: frozenset[str] = frozenset({"parallelogram"})


def _is_diamond(node: Node) -> bool:
    """Return True if the node is a ConditionalHandler (diamond) node."""
    return node.shape in _DIAMOND_SHAPES or node.type == "conditional"


def _is_tool(node: Node) -> bool:
    """Return True if the node is a ToolHandler (parallelogram) node."""
    return node.shape in _TOOL_SHAPES or node.type == "tool"


def _is_human_gate(node: Node) -> bool:
    """Return True if the node is a human-gate (hexagon / wait.human) node."""
    return node.shape == "hexagon" or node.type == "wait.human"


def _find_back_edges(graph: Graph) -> set[tuple[str, str]]:
    """Return the set of back-edges (source, target) in the graph using DFS.

    A back-edge is an edge from a node to an ancestor in the DFS tree,
    indicating a cycle.  Uses iterative DFS to avoid recursion limits on
    large graphs.
    """
    visited: set[str] = set()
    in_stack: set[str] = set()
    back_edges: set[tuple[str, str]] = set()

    def dfs(start: str) -> None:
        stack: list[tuple[str, list[str]]] = [(start, [])]
        while stack:
            node_id, neighbors_iter_state = stack[-1]
            if node_id not in visited:
                visited.add(node_id)
                in_stack.add(node_id)
                # Build neighbor list on first visit
                neighbors = [
                    e.to_node
                    for e in graph.outgoing_edges(node_id)
                    if e.to_node in graph.nodes
                ]
                stack[-1] = (node_id, neighbors)
            else:
                # Continuing after returning from a child
                neighbors = neighbors_iter_state

            # Find next unprocessed neighbor
            found_child = False
            while neighbors:
                neighbor = neighbors.pop(0)
                stack[-1] = (node_id, neighbors)
                if neighbor in in_stack:
                    back_edges.add((node_id, neighbor))
                elif neighbor not in visited:
                    stack.append((neighbor, []))
                    found_child = True
                    break

            if not found_child:
                stack.pop()
                in_stack.discard(node_id)

    for node_id in graph.nodes:
        if node_id not in visited:
            dfs(node_id)

    return back_edges


def _has_cycle(graph: Graph) -> bool:
    """Return True if the graph contains at least one cycle."""
    return bool(_find_back_edges(graph))


def _compute_sccs(graph: Graph) -> list[set[str]]:
    """Return a list of strongly-connected components (SCCs) with size >= 2,
    or size == 1 with a self-loop.  Uses Kosaraju's two-pass algorithm.

    Each returned SCC is a set of node IDs that form a cycle together.
    SCCs of size 1 with no self-loop are trivial (no cycle) and are excluded.

    This is the correct granularity for per-cycle analysis: TOPO-004 and
    TOPO-005 must check each SCC independently so that a compliant SCC does
    not suppress diagnostics for a non-compliant sibling SCC.
    """
    node_ids = list(graph.nodes.keys())
    if not node_ids:
        return []

    # Build adjacency and reverse adjacency
    adj: dict[str, list[str]] = {n: [] for n in node_ids}
    radj: dict[str, list[str]] = {n: [] for n in node_ids}
    for edge in graph.edges:
        if edge.from_node in graph.nodes and edge.to_node in graph.nodes:
            adj[edge.from_node].append(edge.to_node)
            radj[edge.to_node].append(edge.from_node)

    # Self-loop check helper
    self_loop_nodes: set[str] = {
        edge.from_node
        for edge in graph.edges
        if edge.from_node == edge.to_node and edge.from_node in graph.nodes
    }

    # Pass 1: DFS on original graph, collect finish order
    visited: set[str] = set()
    finish_order: list[str] = []

    def dfs1(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, idx = stack[-1]
            if node not in visited:
                visited.add(node)
            neighbors = adj[node]
            if idx < len(neighbors):
                stack[-1] = (node, idx + 1)
                nxt = neighbors[idx]
                if nxt not in visited:
                    stack.append((nxt, 0))
            else:
                stack.pop()
                finish_order.append(node)

    for n in node_ids:
        if n not in visited:
            dfs1(n)

    # Pass 2: DFS on reversed graph in reverse finish order
    visited2: set[str] = set()
    sccs: list[set[str]] = []

    def dfs2(start: str) -> set[str]:
        component: set[str] = set()
        stack: list[str] = [start]
        while stack:
            node = stack.pop()
            if node in visited2:
                continue
            visited2.add(node)
            component.add(node)
            for nxt in radj[node]:
                if nxt not in visited2:
                    stack.append(nxt)
        return component

    for n in reversed(finish_order):
        if n not in visited2:
            scc = dfs2(n)
            # Include SCCs with a cycle: size >= 2, or size == 1 with self-loop
            if len(scc) >= 2 or (len(scc) == 1 and next(iter(scc)) in self_loop_nodes):
                sccs.append(scc)

    return sccs


def _nodes_on_cycles(graph: Graph) -> set[str]:
    """Return the set of node IDs that participate in at least one cycle.

    Delegates to ``_compute_sccs`` for correctness: any node in a non-trivial
    SCC (size >= 2 or self-loop) is on a cycle.
    """
    result: set[str] = set()
    for scc in _compute_sccs(graph):
        result.update(scc)
    return result


def _check_dead_conditional_edge(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-001: Dead conditional edge out of a diamond node.

    ConditionalHandler (shape=diamond) always returns SUCCESS unconditionally
    (handlers/conditional.py:47).  Additionally, FAIL is fail-fast: it never
    reaches a diamond node via plain edges (edge_selection.py:79-101).

    Therefore any edge out of a diamond that conditions on ``outcome!=success``
    can NEVER fire — the diamond always emits SUCCESS, so the negation is
    always false.  Similarly, ``outcome=fail`` edges are dead for the same
    reason.

    This is the root cause of the dead-corrective-edge bug class that shipped
    in 8 examples (fixed upstream).  The correct pattern is to route on
    evidence (e.g. ``context.tool.last_line=X`` or ``context.preferred_label``
    set by a preceding tool/LLM node) rather than on ``outcome=`` through a
    diamond.

    Severity: ERROR — the edge is provably unreachable; the corrective branch
    will never execute.
    """
    for node in graph.nodes.values():
        if not _is_diamond(node):
            continue
        if node.is_start_node() or node.is_exit_node():
            continue

        for edge in graph.outgoing_edges(node.id):
            cond = edge.condition.strip() if edge.condition else ""
            if not cond:
                continue

            # Check each clause for outcome!=success or outcome=fail patterns
            for key, op, val in parse_condition(cond):
                dead = (op == "!=" and key == "outcome" and val == "success") or (
                    op == "=" and key == "outcome" and val in ("fail", "error")
                )

                if dead:
                    diags.append(
                        Diagnostic(
                            rule="dead_conditional_edge",
                            severity="ERROR",
                            message=(
                                f"Node '{node.id}' (diamond/ConditionalHandler) has a "
                                f"dead outgoing edge to '{edge.to_node}' with condition "
                                f"'{cond}': ConditionalHandler always returns SUCCESS "
                                f"unconditionally, so outcome!=success / outcome=fail "
                                f"edges from a diamond can never fire."
                            ),
                            node_id=node.id,
                            edge=(edge.from_node, edge.to_node),
                            fix=(
                                f"Replace the outcome= condition on the edge from "
                                f"'{node.id}' to '{edge.to_node}' with an evidence-based "
                                f"condition (e.g. context.tool.last_line=X or "
                                f"context.preferred_label=Y set by a preceding tool or "
                                f"LLM node). Diamond nodes are pure routing hubs — they "
                                f"do not execute logic and cannot observe upstream "
                                f"outcomes. See DOT-AUTHORING-GUIDE.md for the "
                                f"evidence-routing pattern."
                            ),
                        )
                    )
                    break  # one diagnostic per edge is enough


def _check_stale_label_collision(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-002: Stale-label ambiguity on a tool node.

    When a ToolHandler (shape=parallelogram) fails, it returns FAIL early
    (handlers/tool.py:158-176) BEFORE setting ``context.tool.last_line``
    (tool.py:220).  On the second visit after a failure, ``tool.last_line``
    still holds the stale value from the prior successful run.

    If the same source tool node has BOTH:
      - an outgoing edge conditioned on ``context.tool.last_line=X`` (without
        also asserting ``&& outcome=success``), AND
      - an outgoing edge conditioned on ``outcome=fail``

    then on the second visit after a failure, BOTH edges match simultaneously:
    the ``last_line`` edge matches the stale value AND the ``outcome=fail``
    edge matches the current FAIL outcome.

    Historical note (T0-4): prior to spec-conformance restoration, the engine
    fanned out to both targets — a silent double-dispatch.  The engine now
    conforms to attractor-spec.md §3.3 (best_by_weight_then_lexical): when
    multiple conditional edges match, exactly ONE is selected — the highest-
    weight edge, with lexical target-id tiebreak.  The fan-out consequence is
    gone; the ambiguity is not.  A stale ``last_line`` + FAIL still resolves
    to one edge deterministically, but that edge may not be the one the author
    intended.  Adding ``&& outcome=success`` makes the intent explicit and
    removes the ambiguity entirely.

    Severity: WARNING — the deterministic pick can still be the wrong edge;
    ``&& outcome=success`` is good explicitness discipline, not a safety
    requirement.  Downgraded from ERROR (T0-4 spec-conformance restoration).

    Traceable to: handlers/tool.py (early FAIL return precedes the
    context.set of tool.last_line); context/engine-semantics.md.
    """
    for node in graph.nodes.values():
        if not _is_tool(node):
            continue
        if node.is_start_node() or node.is_exit_node():
            continue

        outgoing = graph.outgoing_edges(node.id)
        if not outgoing:
            continue

        # Collect edges with context.tool.last_line= conditions (without && outcome=success)
        last_line_edges_without_success: list = []
        has_outcome_fail_edge = False

        for edge in outgoing:
            cond = edge.condition.strip() if edge.condition else ""
            if not cond:
                continue

            clauses = parse_condition(cond)
            has_outcome_success = False
            has_last_line = False

            for key, op, val in clauses:
                if op == "=" and key == "outcome" and val == "success":
                    has_outcome_success = True
                if key in ("context.tool.last_line", "tool.last_line"):
                    has_last_line = True

            if has_last_line and not has_outcome_success:
                last_line_edges_without_success.append(edge)

            # Check for outcome=fail edge (or outcome!=success equivalent)
            for key, op, val in clauses:
                if key == "outcome" and (
                    (op == "=" and val == "fail") or (op == "!=" and val == "success")
                ):
                    has_outcome_fail_edge = True

        if last_line_edges_without_success and has_outcome_fail_edge:
            for edge in last_line_edges_without_success:
                diags.append(
                    Diagnostic(
                        rule="stale_label_collision",
                        severity="WARNING",
                        message=(
                            f"Node '{node.id}' (tool/parallelogram) has a "
                            f"stale-label ambiguity: edge to '{edge.to_node}' "
                            f"conditions on 'context.tool.last_line=...' without "
                            f"'&& outcome=success', while another outgoing edge "
                            f"conditions on 'outcome=fail'. On the second visit "
                            f"after a failure, tool.last_line holds a stale value "
                            f"from the prior success, so both edges match "
                            f"simultaneously. The engine resolves this "
                            f"deterministically (weight desc, lexical tiebreak on "
                            f"target id) but the selected edge may not be the one "
                            f"intended."
                        ),
                        node_id=node.id,
                        edge=(edge.from_node, edge.to_node),
                        fix=(
                            f"Add '&& outcome=success' to the condition on the edge "
                            f"from '{node.id}' to '{edge.to_node}' so it reads "
                            f"'context.tool.last_line=X && outcome=success'. This "
                            f"ensures the label edge only fires when the tool "
                            f"succeeded and the label is fresh, making the intent "
                            f"explicit. The 'outcome=fail' edge handles the failure "
                            f"case exclusively. See "
                            f"DOT-AUTHORING-GUIDE.md for the evidence-routing pattern."
                        ),
                    )
                )


def _check_acyclic_graph(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-003: Acyclic graph warning — no corrective cycle found.

    An attractor pipeline should have at least one back-edge (cycle) that
    allows it to retry, correct, or converge.  A pipeline with no cycle is
    a linear one-pass analysis — which may be deliberate (a single-pass
    review is a legitimate shape) but is more likely a recipe that should
    not be an attractor.

    Half of the originally-shipped examples were acyclic.  This warning
    surfaces the question at author time.

    Severity: WARNING — deliberate one-pass pipelines are legitimate.  The
    fix text acknowledges this.
    """
    if _has_cycle(graph):
        return

    diags.append(
        Diagnostic(
            rule="acyclic_graph",
            severity="WARNING",
            message=(
                "This graph has no cycle (no back-edge): it is a linear, "
                "one-pass pipeline.  An attractor should have at least one "
                "corrective loop that allows it to retry, self-correct, or "
                "converge.  If this is intentional (a deliberate single-pass "
                "analysis), this warning can be ignored — but consider whether "
                "this pipeline should be a recipe instead."
            ),
            fix=(
                "Add a corrective back-edge from a validation/gate node back to "
                "an earlier work node so the pipeline can retry on failure.  "
                "Use evidence-based conditions (context.tool.last_line or "
                "context.preferred_label) on the exit edge to gate convergence.  "
                "If this is a deliberate one-pass pipeline, no change is needed — "
                "this is a WARNING, not an error."
            ),
        )
    )


def _check_cycle_no_conditional_exit(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-004: Cycle with no explicitly-gated exit edge.

    A cycle (SCC) where NO edge leaving the cycle carries an explicit gate
    has no stated convergence criterion.  Termination then rests on implicit
    routing mechanics — unconditional-edge weight/lexical tiebreaks,
    fail-fast halts — or on budget caps (max_retries,
    max_pipeline_duration).  That may work, but the convergence criterion
    is invisible to a reader of the graph.  This is a design smell: make
    the exit explicit.

    Two edge forms count as an explicitly-gated exit:
      - an exit edge with a ``condition`` expression, or
      - a *labeled* exit edge whose source is a human-gate (hexagon /
        wait.human) node — the human's selection routes on edge labels,
        which is an explicit (human) gate even without a condition attr.

    The check runs per strongly-connected component (SCC) so that a compliant
    SCC does not suppress diagnostics for a separate non-compliant SCC.

    Note: ``goal_gate_has_retry`` (an existing WARNING) already covers the
    case where a goal_gate node lacks retry_target.  This rule covers the
    orthogonal case where no gated exit exists on the cycle at all.

    Severity: WARNING — implicitly-routed and budget-capped loops are
    legitimate in some contexts (e.g. bounded exploration).
    """
    sccs = _compute_sccs(graph)
    if not sccs:
        return

    for scc in sccs:
        # Find edges that exit this SCC (from an SCC node to a non-SCC node)
        # and check if any of them are explicitly gated.
        has_gated_exit = False
        for node_id in scc:
            node = graph.nodes.get(node_id)
            for edge in graph.outgoing_edges(node_id):
                if edge.to_node not in scc and edge.to_node in graph.nodes:
                    if edge.condition and edge.condition.strip():
                        has_gated_exit = True
                        break
                    # Labeled exit from a human gate: the human's selection
                    # routes on edge labels — an explicit gate.
                    if (
                        node is not None
                        and _is_human_gate(node)
                        and edge.label
                        and edge.label.strip()
                    ):
                        has_gated_exit = True
                        break
            if has_gated_exit:
                break

        if not has_gated_exit:
            cycle_list = ", ".join(sorted(scc))
            diags.append(
                Diagnostic(
                    rule="cycle_no_conditional_exit",
                    severity="WARNING",
                    message=(
                        f"The cycle involving nodes [{cycle_list}] has no explicitly-"
                        f"gated exit edge: no edge leaving the cycle carries a "
                        f"condition expression (or a labeled human-gate choice).  "
                        f"Termination rests on implicit routing mechanics "
                        f"(unconditional-edge tiebreaks, fail-fast halts) or budget "
                        f"caps (max_retries, max_pipeline_duration) — the convergence "
                        f"criterion is invisible to a reader of the graph."
                    ),
                    fix=(
                        "Add a condition expression to the cycle's exit edge(s) so the "
                        "pipeline exits based on evidence (e.g. "
                        "context.tool.last_line=done or context.preferred_label=converged). "
                        "This makes convergence explicit and independent of budget "
                        "caps.  See DOT-AUTHORING-GUIDE.md for the evidence-routing pattern."
                    ),
                )
            )


def _tool_evidence_gates_flow(graph: Graph, node_id: str) -> bool:
    """Return True if a tool node's own evidence can gate control flow.

    A parallelogram (ToolHandler) node counts as a deterministic evidence
    gate when its outcome or output actually participates in routing.  Two
    engine-semantics-grounded ways this happens:

    (i)  An outgoing edge whose condition references the tool's own evidence:
         ``outcome`` (a tool's outcome is its command's exit status —
         mechanical, not LLM say-so) or a ``tool.*`` / ``context.tool.*``
         key (set from the tool's output).

    (ii) A plain (unconditional) outgoing edge to a default node.  Plain
         edges only traverse on SUCCESS — FAIL is fail-fast
         (edge_selection.py, spec §3.7) — so the tool mechanically halts
         the pipeline on failure: an implicit ``outcome=success`` gate.
         Exception: a plain edge to a node with ``runs_on=always`` or
         ``runs_on=failure`` traverses on FAIL too, so it gates nothing.

    A tool whose outgoing edges are all conditioned solely on non-tool
    context keys (e.g. ``context.preferred_label`` set by an LLM node via
    report_outcome) does NOT gate anything: its own evidence is unused, and
    those context conditions can even match on FAIL against stale context
    values (the stale-label trap, TOPO-002).

    Note the honest limit of static analysis: lint credits the topology,
    not the command.  A tool whose command is a no-op that always succeeds
    (e.g. ``echo ok``) satisfies (ii) syntactically; whether the command
    performs a meaningful check is not statically decidable.
    """
    for edge in graph.outgoing_edges(node_id):
        cond = edge.condition.strip() if edge.condition else ""
        if not cond:
            # (ii) plain edge — implicit outcome=success gate via fail-fast,
            # unless the target opts into failure routing via runs_on.
            target = graph.nodes.get(edge.to_node)
            runs_on = "success"
            if target is not None:
                runs_on = str(target.attrs.get("runs_on", "success") or "success")
                runs_on = runs_on.strip().lower()
            if runs_on not in ("always", "failure"):
                return True
            continue
        # (i) conditional edge referencing the tool's own evidence
        for key, _op, _val in parse_condition(cond):
            if key == "outcome" or key.startswith(("tool.", "context.tool.")):
                return True
    return False


def _check_cycle_no_deterministic_exit(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-005: Loop with no deterministic evidence gate on the cycle.

    A cycle (SCC) whose continuation/exit decisions rest solely on LLM
    say-so lacks a deterministic convergence criterion: the LLM can claim
    success prematurely (wrong-but-plausible work exits the loop) or loop
    forever.  The corrective loop only descends when a mechanical gate on
    the cycle forces bad work back around (or halts it loudly).

    An SCC is compliant when it contains at least one parallelogram
    (ToolHandler) node whose evidence actually gates control flow — see
    ``_tool_evidence_gates_flow`` for the two engine-grounded forms this
    takes (evidence-conditioned edges, or a plain edge whose traversal is
    itself gated by fail-fast semantics).

    A tool merely *being present* on the cycle is NOT enough: a no-op tool
    whose outgoing edges route solely on LLM-set context keys (e.g.
    ``context.preferred_label``) leaves the loop LLM-gated, and this rule
    fires.

    A human-gate (hexagon / wait.human) node on the cycle also counts as a
    real gate: every iteration passes through a human decision, which is
    external judgment — precisely the check that catches wrong-but-plausible
    LLM output.  Warning on the canonical conversational-gate pattern would
    be a false positive that trains authors to ignore the rule.

    The check runs per strongly-connected component (SCC) so that a compliant
    SCC does not suppress diagnostics for a separate non-compliant SCC.

    Severity: WARNING — LLM-gated loops are legitimate in some contexts
    (e.g. goal_gate nodes with retry_target).  This is a design smell, not
    a hard error.
    """
    sccs = _compute_sccs(graph)
    if not sccs:
        return

    for scc in sccs:
        # A human gate on the cycle is real external judgment — not LLM
        # say-so.  The loop is human-gated by design; do not warn.
        if any(_is_human_gate(graph.nodes[n]) for n in scc if n in graph.nodes):
            continue

        tools_on_scc = [n for n in scc if n in graph.nodes and _is_tool(graph.nodes[n])]

        if any(_tool_evidence_gates_flow(graph, n) for n in tools_on_scc):
            continue  # This SCC has a deterministic evidence gate — clean.

        cycle_list = ", ".join(sorted(scc))
        if not tools_on_scc:
            detail = (
                "no parallelogram (tool) node on the cycle provides "
                "mechanically-verifiable evidence for convergence"
            )
        else:
            # Tool(s) exist but their evidence never gates routing
            detail = (
                "parallelogram (tool) node(s) exist on the cycle but their "
                "evidence never gates routing: every outgoing edge is "
                "conditioned on non-tool context keys (e.g. LLM-set "
                "context.preferred_label), so the tool outcome/output is "
                "unused and the loop remains LLM-gated"
            )

        diags.append(
            Diagnostic(
                rule="cycle_no_deterministic_exit",
                severity="WARNING",
                message=(
                    f"The cycle involving nodes [{cycle_list}] has no deterministic "
                    f"evidence gate: {detail}.  The loop's "
                    f"continuation/exit relies solely on LLM judgment."
                ),
                fix=(
                    "Put a parallelogram (tool) node on the cycle whose evidence "
                    "gates routing: either route on its outcome/output "
                    "(condition='context.tool.last_line=done && outcome=success' "
                    "to exit, condition='outcome=fail' back to the work node), or "
                    "give it a plain edge so a failing check halts the loop via "
                    "fail-fast instead of letting unverified work continue.  "
                    "See DOT-AUTHORING-GUIDE.md (TOPO-005) and "
                    "examples/patterns/convergence-factory.dot."
                ),
            )
        )


def _node_runs_on(node: Node | None) -> str:
    """Static mirror of ``engine.py::PipelineEngine._get_runs_on``.

    The engine normalizes the ``runs_on`` node attribute to exactly one of
    ``"success"`` (default), ``"always"``, or ``"failure"``; any other value
    normalizes to the default ``"success"``.  Lint applies the identical
    normalization so that an unrecognized marker (e.g. ``runs_on=sometimes``)
    is treated as unmarked, exactly as the engine will treat it at run time.
    """
    if node is None:
        return "success"
    raw = node.attrs.get("runs_on", "success") or "success"
    val = str(raw).strip().lower()
    if val in ("always", "failure"):
        return val
    return "success"


def _condition_matches_fail(cond: str) -> bool:
    """Return True if a condition expression matches a FAIL outcome.

    A clause of the form ``outcome=fail``, ``outcome=error``, or
    ``outcome!=success`` makes the edge failure-conditioned.  Mirrors the
    clause patterns recognized by TOPO-001 (``_check_dead_conditional_edge``).
    """
    for key, op, val in parse_condition(cond):
        if key == "outcome" and (
            (op == "=" and val in ("fail", "error"))
            or (op == "!=" and val == "success")
        ):
            return True
    return False


def _node_regates(graph: Graph, node_id: str) -> bool:
    """Return True if the node re-gates the flow.

    A node truly re-gates only when ALL its outgoing edges are conditional.
    If the node has any plain (unconditional) outgoing edge — whether to an
    exit node directly or to a non-exit intermediary — that plain edge is a
    potential silent escape route: BFS must be allowed to follow it and
    evaluate the continuation on its own merits.  Such a node does NOT
    re-gate, even if other outgoing edges are conditional.

    A human-gate (hexagon / wait.human) node always re-gates: routing beyond
    it passes through external human judgment, so a failure cannot exit the
    pipeline green without a human seeing it.  This follows the TOPO-004/
    TOPO-005 precedent (a labeled human-gate exit is an explicit gate; a
    human gate on a cycle is a real gate) — warning on a failure route that
    a human explicitly adjudicates would be a false positive that trains
    authors to ignore the rule.
    """
    node = graph.nodes.get(node_id)
    if node is not None and _is_human_gate(node):
        return True
    outgoing = list(graph.outgoing_edges(node_id))
    has_any_conditional = any(e.condition and e.condition.strip() for e in outgoing)
    if not has_any_conditional:
        return False
    has_plain_escape = any(
        not (e.condition and e.condition.strip())
        for e in outgoing
    )
    return not has_plain_escape


def _unmarked_passthrough_path_to_exit(
    graph: Graph, start_id: str, exit_ids: set[str]
) -> list[str] | None:
    """Find a silent pass-through path from ``start_id`` to an exit node.

    Returns a node-ID path ``[start_id, ..., exit_id]`` such that:

    - every hop is an unconditional (plain) edge,
    - no node on the path re-gates the flow (``_node_regates``), and
    - at least one intermediary on the path is UNMARKED — its ``runs_on``
      normalizes to the default ``"success"`` (``_node_runs_on``).

    Returns ``None`` when no such path exists: either the exit is not
    reachable through pass-through intermediaries, or every reaching path
    has all intermediaries marked ``runs_on="always"``/``"failure"`` (a
    deliberately declared handled-failure termination — issue #173
    Ruling 2).

    BFS over ``(node, has_unmarked)`` states so a node may be revisited when
    a later path reaches it with a different marked/unmarked prefix.
    """
    if start_id not in graph.nodes:
        return None

    start_unmarked = _node_runs_on(graph.nodes.get(start_id)) == "success"
    queue: deque[tuple[str, bool, list[str]]] = deque(
        [(start_id, start_unmarked, [start_id])]
    )
    seen: set[tuple[str, bool]] = {(start_id, start_unmarked)}

    while queue:
        node_id, has_unmarked, path = queue.popleft()
        if _node_regates(graph, node_id):
            # Re-gating intermediary: corrective routing — stop this branch.
            continue
        for edge in graph.outgoing_edges(node_id):
            if edge.condition and edge.condition.strip():
                continue  # skip conditional edges — BFS follows only plain edges
            if edge.to_node in exit_ids:
                if has_unmarked:
                    return path + [edge.to_node]
                continue  # all-marked path to exit: deliberate; keep searching
            if edge.to_node not in graph.nodes:
                continue
            next_unmarked = (
                has_unmarked
                or _node_runs_on(graph.nodes.get(edge.to_node)) == "success"
            )
            state = (edge.to_node, next_unmarked)
            if state not in seen:
                seen.add(state)
                queue.append((edge.to_node, next_unmarked, path + [edge.to_node]))
    return None


def _check_fail_routed_to_exit(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-006: A failure outcome routed into the terminal success node.

    An edge whose condition matches a FAIL outcome (``outcome=fail``,
    ``outcome=error``, ``outcome!=success``) that targets the exit node —
    or whose receiving path reaches the exit with every hop unconditional
    and no re-gating in between — structurally converts a failed gate into
    a completed, green-looking run.  This is the graph-topology sibling of
    the CMD-001/CMD-002 hazard class (a gate whose failure is converted into
    success) and the "silent-success exit" incident class: a pipeline runs
    for hours, its verification gate fails, a bookkeeping step succeeds
    afterward, and the run exits 0 reporting success.

    Two forms are detected (issue #173, Ruling 1):

    - **Direct:** the failure-conditioned edge targets the exit node.
      Always flagged — there is no intermediary to mark.
    - **Indirect:** the failure-conditioned edge's receiving path reaches
      the exit through silent pass-through intermediaries — every hop
      unconditional, no node re-gating the flow, and at least one
      intermediary unmarked.

    The exception (issue #173, Ruling 2), grounded in the engine's own
    semantics:

    - ``engine.py::_get_runs_on``: a node's ``runs_on`` attribute
      normalizes to ``"always"``, ``"failure"``, or the default
      ``"success"`` (any other value normalizes to ``"success"``).
    - ``edge_selection.py::select_edge``: on a FAIL outcome, plain
      (unconditional) edges are followed ONLY to targets whose ``runs_on``
      is ``always`` or ``failure``; default ``runs_on=success`` targets are
      not reached — the documented fail-fast behavior.  ``runs_on`` is
      therefore the engine's first-class failure-routing opt-in.

    A failure route in which EVERY intermediary between the failure edge
    and the exit carries ``runs_on="always"`` or ``runs_on="failure"`` is a
    deliberately declared handled-failure termination (e.g. a
    ``runs_on=always`` recorder before ``done``) and is NOT flagged.
    Likewise, an intermediary whose outgoing edges are ALL condition-bearing
    re-gates the flow (retry-vs-escalate routing) — corrective routing, not
    flagged.  One conditional edge is not enough: a node that ALSO has a
    plain (unconditional) outgoing edge does NOT re-gate (``_node_regates``)
    — the plain edge is a silent escape the failure can still take, so such
    a node stays flagged.  A human-gate (hexagon / wait.human) intermediary
    always re-gates — external human judgment on the failure path, per the
    TOPO-004/TOPO-005 human-gate precedent.  An UNMARKED pass-through
    intermediary (default ``runs_on``, a plain outgoing edge the flow can
    follow, exit reachable) is exactly the accident class this rule catches:
    flagged.

    The rule fires regardless of the source node's shape.  On a diamond
    source, TOPO-001 (ERROR — the edge is provably dead) additionally
    fires and takes precedence; this rule still reports the declared
    routing intent.

    Severity: WARNING (issue #173, Ruling 3) — joins the CMD-001/CMD-002
    family: the hazard is real but intent is not statically provable,
    deliberate finish-through-done designs exist, and ERROR would hard-fail
    deliberate graphs via ``validate_or_raise``.  The ``runs_on`` opt-in
    gives authors a first-class way to declare intent and silence the
    diagnostic entirely.
    """
    exit_ids = {n.id for n in graph.nodes.values() if n.is_exit_node()}
    if not exit_ids:
        return

    for node in graph.nodes.values():
        if node.is_exit_node():
            continue
        for edge in graph.outgoing_edges(node.id):
            cond = edge.condition.strip() if edge.condition else ""
            if not cond or not _condition_matches_fail(cond):
                continue

            if edge.to_node in exit_ids:
                # Direct form: failure-conditioned edge targeting the exit.
                diags.append(
                    Diagnostic(
                        rule="fail_routed_to_exit",
                        severity="WARNING",
                        message=(
                            f"Node '{node.id}' routes a failure outcome "
                            f"directly into the terminal success node: edge to "
                            f"'{edge.to_node}' with condition '{cond}'. The "
                            f"failure leaves through the pipeline's success "
                            f"door — no corrective loop, no retry, no distinct "
                            f"failure terminal."
                        ),
                        node_id=node.id,
                        edge=(edge.from_node, edge.to_node),
                        fix=(
                            f"Route the failure to a corrective target instead "
                            f"(e.g. '{node.id}' -> fix "
                            f'[condition="outcome=fail"] with a back-edge to '
                            f"retry), or — if finishing after a handled failure "
                            f"is deliberate — route it through a "
                            f"recorder/cleanup node marked "
                            f'runs_on="always" or runs_on="failure" so the '
                            f"intent is declared. See DOT-AUTHORING-GUIDE.md "
                            f"(TOPO-006)."
                        ),
                    )
                )
                continue

            path = _unmarked_passthrough_path_to_exit(graph, edge.to_node, exit_ids)
            if path is not None:
                path_str = " -> ".join(path)
                diags.append(
                    Diagnostic(
                        rule="fail_routed_to_exit",
                        severity="WARNING",
                        message=(
                            f"Node '{node.id}' routes a failure outcome into "
                            f"the terminal success node via an unmarked "
                            f"pass-through path: edge to '{edge.to_node}' with "
                            f"condition '{cond}', then {path_str} — every hop "
                            f"is unconditional, no node re-gates the flow, and "
                            f"at least one intermediary lacks "
                            f'runs_on="always"/"failure". A failed gate '
                            f"followed by one succeeding step exits the "
                            f"pipeline green (status success, exit code 0) — "
                            f"the silent-success exit incident class."
                        ),
                        node_id=node.id,
                        edge=(edge.from_node, edge.to_node),
                        fix=(
                            f"Either route the failure to a corrective target "
                            f"with a back-edge to retry; make EVERY outgoing "
                            f"edge of an intermediary condition-bearing "
                            f"(remove or condition its plain escape) so the "
                            f"flow is re-gated — a node with any plain "
                            f"outgoing edge does not re-gate; or — if this "
                            f"handled-failure termination is deliberate — "
                            f"mark every intermediary on the path ({path_str}) with "
                            f'runs_on="always" or runs_on="failure" (the '
                            f"engine's failure-routing opt-in) so the intent "
                            f"is declared. See DOT-AUTHORING-GUIDE.md "
                            f"(TOPO-006)."
                        ),
                    )
                )


# Documented engine constant, mirrored for the diagnostic message.  The
# engine's budget lives at ``engine.py::PipelineEngine._MAX_GOAL_GATE_RETRIES``;
# importing engine here would create a validation -> engine import cycle, so
# the message cites the mirrored value instead (kept honest by
# test_topological_lint.py::TestGateRetryBudgetDead::
# test_documented_budget_matches_engine).
_GOAL_GATE_RETRY_BUDGET = 50


def _effective_gate_retry_target(node: Node, graph: Graph) -> str | None:
    """Resolve the retry target the exit-time gate check would use.

    Mirrors ``engine.py::_check_goal_gates()`` exactly: first truthy of
    node ``retry_target`` > node ``fallback_retry_target`` > graph
    ``retry_target`` > graph ``fallback_retry_target``, counted only if it
    names a real node (the engine fails instead of retrying otherwise;
    ``retry_target_exists`` owns reporting that).
    """
    target = (
        node.attrs.get("retry_target")
        or node.attrs.get("fallback_retry_target")
        or graph.graph_attrs.get("retry_target")
        or graph.graph_attrs.get("fallback_retry_target")
    )
    if target and target in graph.nodes:
        return str(target)
    return None


def _check_gate_retry_budget_dead(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-007: goal-gate retry budget structurally dead under loop_restart.

    The engine bounds exit-time goal-gate retries with a budget
    (``engine.py::_MAX_GOAL_GATE_RETRIES``, 50) — but every traversal of a
    ``loop_restart`` edge resets that counter to zero (``run()`` Step 6),
    which is the fresh-attempt semantics ledgered as ATX-12
    (``specs/EXTENSIONS.md`` §24: a restart is an in-process stand-in for
    terminate-and-relaunch, so per-run budgets start over).  The two
    interact badly in one specific shape: when EVERY success-path walk from
    the gate's retry target back to the exit crosses a ``loop_restart``
    edge, the budget resets on every gate-retry cycle and can never bind.
    The retry loop is then bounded only by the global step cap
    (nodes × 50), the counter stays pinned at 1, and the author's
    belt-and-suspenders budget is silently dead (issue #253).

    Measured on the shipped engine (issue #253, 4-node reduction): without
    ``loop_restart`` on the retry walk the gate executed 51 times and the
    run stopped at the 50-retry budget ("Unsatisfied goal gates"); with
    ``loop_restart`` on the retry walk the gate executed 66 times, the
    retry counter never advanced past 1, and only the step cap (200) ended
    the run.  The same shape shipped in ``objective-runner.dot`` until
    PR #248 dropped its ``retry_target`` (its in-file note records 51/50
    executions measured on a 5-node reduction; #248's review measured 67
    on an 8-node one).

    Detection is the success projection of the graph: drop ``loop_restart``
    edges and failure-conditioned edges (``outcome=fail`` /
    ``outcome=error`` / ``outcome!=success`` — the TOPO-006 classifier,
    ``_condition_matches_fail``), then ask whether any exit node is
    reachable from the retry target.  Context-conditioned and
    success-conditioned edges stay in the projection — statically
    unknowable routing counts as a live escape (conservative toward
    silence).  If no exit is reachable AND the projected walk meets at
    least one ``loop_restart`` edge, the budget is dead: whenever the
    retried nodes succeed at their jobs, the walk crosses the resetting
    edge before it can reach the exit's gate check again.

    What this rule deliberately does NOT flag: the shipped convergence
    pattern where ``loop_restart`` rides a fail-conditioned or iterate
    back-edge (task-runner, 02-plan-implement-test, the capsule pipelines)
    — there the forward success walk re-reaches the exit without a reset,
    so the budget stays live for the exit-time retry loop even though an
    explicit iterate cycle also exists.  A run that keeps CHOOSING the
    iterate back-edge also keeps resetting the counter, but that cycle is
    the author's declared iteration protocol — bounded by its own budget
    wall per the design doctrine — not the gate-retry loop this budget
    exists to bound.

    Severity: WARNING — same reasoning as TOPO-006: the hazard is real and
    was measured on a shipped graph, but run-time routing is not statically
    provable, and the engine still terminates (at the step cap), so ERROR
    would hard-fail deliberate designs.
    """
    loop_edges = [
        e for e in graph.edges if resolve_bool_attr(e.loop_restart, "loop_restart")
    ]
    if not loop_edges:
        return
    exit_ids = {n.id for n in graph.nodes.values() if n.is_exit_node()}
    if not exit_ids:
        return

    # Success projection: adjacency over edges that are neither
    # loop_restart nor failure-conditioned.
    projected: dict[str, list[str]] = {}
    for e in graph.edges:
        if resolve_bool_attr(e.loop_restart, "loop_restart"):
            continue
        cond = e.condition.strip() if e.condition else ""
        if cond and _condition_matches_fail(cond):
            continue
        projected.setdefault(e.from_node, []).append(e.to_node)

    for node in graph.nodes.values():
        if not resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate"):
            continue
        retry_target = _effective_gate_retry_target(node, graph)
        if retry_target is None:
            continue

        reachable = {retry_target}
        stack = [retry_target]
        while stack:
            for nxt in projected.get(stack.pop(), []):
                if nxt not in reachable:
                    reachable.add(nxt)
                    stack.append(nxt)

        if reachable & exit_ids:
            continue  # a reset-free success walk back to the exit exists

        crossing = [e for e in loop_edges if e.from_node in reachable]
        if not crossing:
            continue  # projected retry walk never meets a resetting edge

        edge = crossing[0]
        diags.append(
            Diagnostic(
                rule="gate_retry_budget_dead",
                severity="WARNING",
                message=(
                    f"Node '{node.id}' (goal_gate=true) retries from "
                    f"'{retry_target}', but every success-path walk from "
                    f"'{retry_target}' back to the exit crosses a "
                    f"loop_restart edge ({edge.from_node} -> {edge.to_node}), "
                    f"and loop_restart resets the goal-gate retry counter. "
                    f"The {_GOAL_GATE_RETRY_BUDGET}-retry budget can never "
                    f"bind: the retry loop is bounded only by the global "
                    f"step cap (nodes × {_GOAL_GATE_RETRY_BUDGET}) "
                    f"(TOPO-007)."
                ),
                node_id=node.id,
                edge=(edge.from_node, edge.to_node),
                fix=(
                    "Point retry_target at a node whose success path reaches "
                    "the exit without crossing a loop_restart edge, bound the "
                    "loop_restart cycle with an explicit budget wall, or drop "
                    "the retry_target if the gate's failure cause survives "
                    "retries (the PR #248 resolution)"
                ),
            )
        )


# ---------------------------------------------------------------------------
# TOPO-008 — an evidence gate whose answer cannot change where the run goes
#
# The authoring checker (``examples/authoring/check_authored_pipeline.py``)
# grew this rule as **A10** after issue #245: a pipeline that satisfied every
# other doctrine check while routing BOTH of its evidence gate's answers into
# the exit, so the run ended green whether the tests passed or failed.  A10
# lives in the authoring layer, which only machine-authored graphs pass
# through; a hand-authored graph with the identical shape got nothing.  This is
# the same question, asked by ``attractor lint``, so hand-authored graphs are
# covered too (issue #254 item 2).
#
# The semantics are A10's, deliberately — same evidence-gate definition, same
# ``context.tool.last_line`` token extraction, same relay-transparent landing
# chase, same exit-only scope — so the two layers cannot drift into disagreeing
# about the same graph.  Two narrowings carry over unchanged, and both were
# measured rather than assumed:
#
#   • **Only the exit.**  The general "two distinct tokens into ANY node" form
#     was REJECTED on measurement: it fires on three of this repository's own
#     deliberate ``.github/`` patterns, where several distinct diagnoses
#     converge on one node that WRITES THEM UP rather than routes on them.
#     Recording a token is legitimate; ending the run green either way is not.
#   • **Only through relay no-ops.**  The landing chase sees through a node
#     that merely forwards (a ``diamond``/``point`` with a single unconditional
#     outgoing edge decides nothing, so entering and leaving it is
#     indistinguishable from taking the edge directly).  It stops at any node
#     that DOES something: if the two answers ran different work before
#     converging, the gate's answer demonstrably changed what happened, and
#     whether that path should still end green is a judgement this rule does
#     not have.
#
# Severity: WARNING — joins the TOPO-002–007 / CMD-001–002 family.  The hazard
# is real but intent is not statically provable, and ERROR would hard-fail
# graphs through ``validate_or_raise``.  Calibrated over every ``.dot`` in the
# repository (``examples/``, ``patterns/``, ``.github/``, and the test
# fixtures): it fires on the issue-#245 B1 construction and on ZERO shipped
# graphs.
# ---------------------------------------------------------------------------

#: Shapes that route without doing anything.  A node of one of these shapes
#: with exactly one unconditional outgoing edge is a pure relay: the DOT
#: authoring guide documents ``diamond`` as a no-op whose "outgoing edges do
#: the deciding", so a diamond with nothing to decide forwards and no more.
#: TOPO-008 sees through exactly these and nothing else, so "both answers
#: landed in the same place" can never quietly mean "the two branches ran
#: different work and then converged".
_RELAY_SHAPES: frozenset[str] = frozenset({"diamond", "point"})

#: Shell head-words that emit a constant or rearrange files without checking
#: anything.  A ``tool_command`` whose every command position is one of these
#: cannot come back with an answer the author did not already write, so the
#: node is not an evidence gate and TOPO-008 has no opinion about it.
_CONSTANT_EMITTER_WORDS: frozenset[str] = frozenset(
    {
        "printf", "echo", "exit", "return", "true", "false", ":", "cd", "mkdir",
        "touch", "sleep", "shift", "set", "umask", "export", "unset", "break",
        "continue", "rm", "cp", "mv", "chmod",
    }
)

#: Shell keywords and grouping tokens — skipped past to reach the real head word.
_SHELL_KEYWORD_WORDS: frozenset[str] = frozenset(
    {
        "if", "then", "else", "elif", "fi", "while", "until", "do", "done",
        "case", "esac", "for", "select", "function", "time", "!", "in",
    }
)

_GATE_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_GATE_REDIRECT_RE = re.compile(r"^\d*[<>]")

#: The context key an evidence gate publishes its verdict on.
_TOOL_LAST_LINE_KEY = "context.tool.last_line"


def _gate_command_segments(command: str) -> list[str]:
    """Split a shell command into command positions, respecting quotes.

    Deliberately approximate — a lint-grade reader in the same spirit as
    CMD-001's, not a shell.  It errs toward MORE segments, which can only make
    the substantive-command search more generous, never less.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue
            current.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(current))
            current = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n", "(", ")", "{", "}", "`"):
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return segments


def _gate_head_word(segment: str) -> str:
    """The command word a segment actually runs, or '' if it runs nothing."""
    for word in segment.split():
        if word in _SHELL_KEYWORD_WORDS:
            continue
        if _GATE_ASSIGNMENT_RE.match(word) or _GATE_REDIRECT_RE.match(word):
            continue
        return word.strip('"').strip("'")
    return ""


def _runs_a_substantive_command(command: str) -> bool:
    """Does *command* check or compute something real, anywhere in it?

    ``[``, ``test``, ``grep``, ``pytest``, ``python3``, ``git`` — anything that
    is not purely an emitter or a file arrangement.  ``printf gate_pass`` is
    not a gate: it cannot fail, so nothing behind it is gated.
    """
    return any(
        head and head not in _CONSTANT_EMITTER_WORDS
        for head in (_gate_head_word(seg) for seg in _gate_command_segments(command))
    )


def _reachable_node_ids(graph: Graph) -> set[str]:
    """Node ids reachable from any start node by following edges."""
    visited: set[str] = set()
    queue: deque[str] = deque(n.id for n in graph.nodes.values() if n.is_start_node())
    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        for edge in graph.outgoing_edges(node_id):
            if edge.to_node in graph.nodes and edge.to_node not in visited:
                queue.append(edge.to_node)
    return visited


def _is_evidence_gate(graph: Graph, node: Node, reachable: set[str]) -> bool:
    """Is this a reachable tool node that runs a real command AND routes on it?

    Three conditions, each load-bearing (A10's definition, unchanged):

    * **a tool node** — one carrying a ``tool_command``.  An LLM node's opinion
      of its own work is not evidence, however confidently it is phrased, and
      ``_check_tool_command_handler`` already ERRORs when a recognized non-tool
      handler carries a command, so the command IS the tool-node test here.
    * **a substantive command** — something that can come back with an answer
      the author did not already write.
    * **it routes** — at least two outgoing edges, at least one conditional.
      A node whose result changes nothing is not deciding anything.
    """
    if node.id not in reachable:
        return False
    command = str(node.attrs.get("tool_command") or "").strip()
    if not command or not _runs_a_substantive_command(command):
        return False
    out = graph.outgoing_edges(node.id)
    if len(out) < 2:
        return False
    return any(e.condition and e.condition.strip() for e in out)


def _routed_last_line_token(condition: str) -> str | None:
    """The ``tool.last_line`` value this edge condition selects ON, if any.

    ``context.tool.last_line=green && outcome=success`` yields ``green``.
    Parsed through ``conditions.parse_condition`` — the same grammar entry
    point the engine routes with — so lint and routing cannot drift.

    An INEQUALITY deliberately yields nothing: TOPO-008 reasons about which
    ANSWER sends the run where, and "anything but green" is not an answer.
    """
    for key, op, value in parse_condition(condition or ""):
        if key == _TOOL_LAST_LINE_KEY and op == "=":
            return value.strip().strip('"').strip("'")
    return None


def _relay_forward_edge(graph: Graph, node_id: str) -> Edge | None:
    """The single edge out of a pure routing no-op, or ``None`` if not one."""
    node = graph.nodes.get(node_id)
    if node is None or node.is_exit_node() or node.is_start_node():
        return None
    if node.shape not in _RELAY_SHAPES:
        return None
    out = graph.outgoing_edges(node_id)
    if len(out) != 1 or (out[0].condition and out[0].condition.strip()):
        return None
    return out[0]


def _token_landing(graph: Graph, edge: Edge) -> str:
    """Where a token edge actually puts the run, seeing through relay no-ops."""
    node_id = edge.to_node
    seen = {edge.from_node}
    while node_id not in seen:
        seen.add(node_id)
        relay = _relay_forward_edge(graph, node_id)
        if relay is None:
            break
        node_id = relay.to_node
    return node_id


def _check_inert_evidence_gate(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-008: an evidence gate that answers into the exit no matter what it finds.

    The shape::

        gate -> done [condition="context.tool.last_line=green"]
        gate -> done [condition="context.tool.last_line=red"]

    The command is real, the exit is reached only through the gate, and no
    FAILURE outcome is routed anywhere near the exit — so TOPO-004, TOPO-005
    and TOPO-006 are all green while the run ends successfully whether the
    tests passed or not.  The gate is decorative: it runs, it prints a verdict,
    and the graph goes to the exit either way.

    This is the ``attractor lint`` sibling of the authoring checker's A10
    (``examples/authoring/check_authored_pipeline.py``), which protects
    machine-authored graphs only.  Same reach semantics, so a graph cannot pass
    one layer and fail the other.

    Severity: WARNING.  See the section header above for the calibration and
    for the two narrowings (exit-only; relay-no-ops-only) that were measured
    against this repository's own shipped graphs.
    """
    reachable = _reachable_node_ids(graph)
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        if not _is_evidence_gate(graph, node, reachable):
            continue

        # landing node id -> token -> the edge as written
        landed: dict[str, dict[str, str]] = {}
        for edge in graph.outgoing_edges(node.id):
            token = _routed_last_line_token(edge.condition)
            if token is None:
                continue
            landing_id = _token_landing(graph, edge)
            landing = graph.nodes.get(landing_id)
            if landing is None or not landing.is_exit_node():
                continue
            landed.setdefault(landing_id, {})[token] = (
                f"{node.id} -> {edge.to_node} [condition=\"{edge.condition}\"]"
            )

        for landing_id, by_token in sorted(landed.items()):
            if len(by_token) < 2:
                continue
            tokens = sorted(by_token)
            written = "; ".join(by_token[t] for t in tokens)
            diags.append(
                Diagnostic(
                    rule="inert_evidence_gate",
                    severity="WARNING",
                    message=(
                        f"Evidence gate '{node.id}' routes {len(tokens)} different "
                        f"answers ({', '.join(repr(t) for t in tokens)}) into the "
                        f"terminal success node '{landing_id}': {written}. Every one "
                        f"of those answers ends the run green, so the gate's answer "
                        f"decided nothing — it runs, it prints a verdict, and the "
                        f"graph reaches the exit either way (TOPO-008)."
                    ),
                    node_id=node.id,
                    fix=(
                        f"Route the failing token somewhere that is not the success "
                        f"door — back into the corrective loop, to a postmortem, or "
                        f"to a LOUD escalation. If '{node.id}' is genuinely not a "
                        f"decision point, drop the conditions and let it record "
                        f"instead of routing on it. Same rule as the authoring "
                        f"checker's A10 "
                        f"(examples/authoring/check_authored_pipeline.py)."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# TOPO-009 — an `outcome=` condition whose word the node can also emit as a label
#
# `outcome=` does not mean what canonical §10.4 says it means here.  Canonical
# defines it as `outcome.status` only; this engine resolves it to
# `preferred_label` FIRST and falls back to `status.value` only when no label
# is set (`conditions.py::_resolve_key`).  That divergence is deliberate,
# load-bearing (it is how a node steers its own routing through
# `report_outcome`) and ledgered — `specs/EXTENSIONS.md` §22, `SPEC_CONFORMANCE`
# ATX-5 (disposition DIVERGE, decided).  The ledger is explicit that it is not
# behaviour-neutral.  What it has never had is a way for an author to find out
# they walked into it (issue #226).
#
# The hazard is a vocabulary overlap.  `preferred_label` is free-form, and the
# status words are exactly the words an author reaches for as a label, so on a
# node that emits labels an `outcome=<status word>` edge answers a question the
# author did not ask:
#
#   * a node reporting `status="success", preferred_label="retry"` MATCHES
#     `condition="outcome=retry"` — its status is SUCCESS; and
#   * a node reporting `status="retry", preferred_label="needs_work"` does NOT
#     match it — its status is RETRY.
#
# Nothing is logged either way.  The condition is well-formed, the graph lints
# clean, and the route is simply not the one on the page.
#
# **Calibration — the narrowing is measured, not asserted.**  Run over every
# `.dot` in this repository (63 files: `examples/`, `.github/`, `skills/`, and
# every test fixture), scoring the shapes the issue proposed:
#
#   * the issue's condition (1) alone — *any* `outcome=<status word>` edge —
#     fires on **23 of 63** shipped graphs.  `outcome=success` is the ordinary,
#     correct way to route a graph whose nodes never emit labels; flagging it
#     is wolf-crying.
#   * the issue's own suggested conservative form — condition (1) plus *any*
#     status-word edge `label=` anywhere in the graph — still fires on **6**.
#     Shipped graphs put status words on CONDITIONAL edges as documentation
#     (`gate -> fix [condition="context.tool.last_line=fail", label="fail"]`),
#     where `preferred_label` matching cannot reach them at all.
#   * what ships below — the collision scoped to ONE node's own out-edges, and
#     to the edges `preferred_label` can actually select — fires on **ZERO**
#     shipped graphs, and on the issue's constructed hazard.
#
# The scoping is not a fudge to reach zero; it is the routing semantics.
# `select_edge` resolves a node's outcome against THAT node's out-edges, and
# its Step 2 (`preferred_label` match) considers only UNCONDITIONAL labelled
# edges.  So an unconditional labelled edge out of a node is the documented,
# statically-visible evidence that the node is steered by `preferred_label`;
# a label on a conditional edge is inert decoration that no label can select.
# The sweep test `test_topological_lint.py::TestOutcomeLabelShadowing::
# test_silent_on_every_shipped_dot` pins the zero.
#
# Severity: WARNING — joins the TOPO-002–008 / CMD-001–002 family.  The pattern
# is legal and sometimes exactly what the author wants; ERROR would hard-fail
# deliberate graphs through `validate_or_raise`.  `lint()`-only.
# ---------------------------------------------------------------------------

#: The status vocabulary `outcome=` falls back to, derived from `StageStatus`
#: itself (not a hand-copied list) so a new status value cannot silently leave
#: this rule behind.  Normalised through the engine's own label normaliser so
#: "what counts as a status word" is asked exactly once, the same way routing
#: asks it.
_STATUS_WORDS: frozenset[str] = frozenset(
    _normalize_label(s.value) for s in StageStatus
)


def _outcome_status_words(condition: str) -> set[str]:
    """Status words this condition routes on via the `outcome` key.

    Parsed through ``conditions.parse_condition`` — the same grammar entry
    point the engine routes with — so lint and routing cannot drift.  Both
    ``=`` and ``!=`` count: an inequality resolves the same overloaded key and
    is shadowed by a label just as silently.
    """
    words: set[str] = set()
    for key, op, value in parse_condition(condition or ""):
        if key != "outcome" or op not in ("=", "!="):
            continue
        word = _normalize_label(value.strip().strip('"').strip("'"))
        if word in _STATUS_WORDS:
            words.add(word)
    return words


def _label_selectable_edges(graph: Graph, node_id: str) -> list[Edge]:
    """Out-edges ``preferred_label`` can actually select — spec §3.3 Step 2.

    Step 2 considers only UNCONDITIONAL labelled edges: a conditional edge
    whose condition failed in Step 1 must not then be picked by label.  A
    status word on a conditional edge is therefore documentation, not a
    routing target, and is deliberately not evidence here.
    """
    return [
        e
        for e in graph.outgoing_edges(node_id)
        if not (e.condition or "").strip() and (e.label or "").strip()
    ]


def _check_outcome_label_shadowing(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-009: an `outcome=<status word>` edge on a node that also routes by label.

    The shape::

        review -> fix    [condition="outcome=retry"]   // author means STATUS
        review -> rework [label="retry"]               // node steers by LABEL

    Both edges leave the same node, and `outcome=` reads `preferred_label`
    before `status` (EXTENSIONS §22 / ATX-5).  So `review` emitting
    ``preferred_label="retry"`` takes the *condition* edge to `fix` whatever
    its status was — Step 1 runs before Step 2 — and `review` emitting
    ``status="retry"`` with any other label does not take it at all.

    Fires only when one node carries both halves, and only when the label is
    on an edge ``preferred_label`` can actually select.  See the section
    header above for the measurement behind that scoping.

    Severity: WARNING, ``lint()``-only.  Routing on ``outcome=success`` in a
    graph whose nodes never emit labels is normal and correct; this rule has
    no opinion about it.
    """
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        condition_edges: list[tuple[Edge, set[str]]] = []
        for edge in graph.outgoing_edges(node.id):
            words = _outcome_status_words(edge.condition)
            if words:
                condition_edges.append((edge, words))
        if not condition_edges:
            continue

        label_edges = [
            e
            for e in _label_selectable_edges(graph, node.id)
            if _normalize_label(e.label) in _STATUS_WORDS
        ]
        if not label_edges:
            continue

        labels = sorted({_normalize_label(e.label) for e in label_edges})
        label_written = "; ".join(
            f'{node.id} -> {e.to_node} [label="{e.label}"]'
            for e in sorted(label_edges, key=lambda e: (e.to_node, e.label))
        )
        condition_written = "; ".join(
            f'{node.id} -> {e.to_node} [condition="{e.condition}"]'
            for e, _ in sorted(condition_edges, key=lambda pair: pair[0].to_node)
        )
        all_words = sorted(set().union(*(w for _, w in condition_edges)))
        collisions = sorted(set(all_words).intersection(labels))

        # Report against the edge that actually collides when one does; that is
        # the edge whose route the label decides.  Otherwise the first by
        # target id, so the diagnostic is always pinpointed at a real edge.
        by_target = sorted(condition_edges, key=lambda pair: pair[0].to_node)
        primary = next(
            (e for e, w in by_target if w.intersection(labels)),
            by_target[0][0],
        )
        word = (collisions or all_words)[0]

        if collisions:
            overlap = (
                f"'{word}' is in both vocabularies at once, so "
                f"'{node.id}' reporting status=success with "
                f"preferred_label=\"{word}\" matches "
                f"'{node.id}' -> '{primary.to_node}' anyway, while "
                f"'{node.id}' reporting status={word} under any other label "
                f"does not match it at all."
            )
        else:
            overlap = (
                f"The label vocabulary here ({', '.join(repr(x) for x in labels)}) "
                f"is drawn from the status vocabulary, so any label "
                f"'{node.id}' emits is what these conditions compare against - "
                f"'{node.id}' reporting status={word} under one of those labels "
                f"does not match '{node.id}' -> '{primary.to_node}' at all."
            )

        diags.append(
            Diagnostic(
                rule="outcome_label_shadowing",
                severity="WARNING",
                message=(
                    f"Node '{node.id}' routes on the 'outcome' key against "
                    f"status words ({condition_written}) while also steering "
                    f"itself by label ({label_written}). 'outcome' resolves "
                    f"preferred_label BEFORE status (EXTENSIONS.md §22 / "
                    f"SPEC_CONFORMANCE ATX-5) - not status alone, as canonical "
                    f"§10.4 defines it. {overlap} Neither case is logged: the "
                    f"condition is well-formed and the graph is otherwise clean "
                    f"(TOPO-009)."
                ),
                node_id=node.id,
                edge=(primary.from_node, primary.to_node),
                fix=(
                    f"Say which one you mean - both keys are exact and "
                    f"unambiguous. For the status, write "
                    f'condition="status={word}" on '
                    f"'{node.id}' -> '{primary.to_node}'; for the label, write "
                    f'condition="preferred_label={word}". If \'{node.id}\' is '
                    f"not meant to steer itself by label, take the status word "
                    f"off its labelled edge instead. See "
                    f"DOT-AUTHORING-GUIDE.md (TOPO-009) and "
                    f"docs/ROUTING-REFERENCE.md §3."
                ),
            )
        )


# ---------------------------------------------------------------------------
# TOPO-010 — a shape=folder node whose STATIC relative dot_file= target is
# absent at lint time.
#
# Issue #200.  The complaint there is real: a missing child DOT used to
# surface mid-run as a `no_matching_edge` termination.  The node-entry fix
# (handlers/pipeline.py's ChildDotResolutionError) makes that failure legible,
# and this rule offers the author the same information one step earlier.
#
# Severity: WARNING, and deliberately ADVISORY — never ERROR, never in
# validate()/validate_or_raise().  The linter genuinely cannot tell "the
# author typo'd the path" from "a node upstream writes this file during the
# run": write-then-run composition (a node generates a child .dot mid-run and
# a later folder node executes it) is a supported, shipped shape —
# examples/objective/objective-runner.dot does exactly this, and
# EXTENSIONS.md §10's resolution is lazy precisely so it can.  An ERROR here
# would block every composition graph; per §32's entry-point discriminator a
# lint-only WARNING blocks nothing and fails no exit code.
#
# What it deliberately does NOT flag:
#   • an ABSOLUTE dot_file= — nothing about a lint-time absolute path tells
#     you which machine/workspace it will resolve against at run time.
#   • any value containing `$` — a $variable target is resolved from run-time
#     context (EXTENSIONS.md §21); its lint-time spelling is not the path.
#     This is also the exact shape a composition graph uses for a generated
#     child (`dot_file="$target_dir/.objective/gen/child.dot"`).
#   • any graph with no `source_dir` — an inline DOT source (--dot-source, a
#     library caller, the examples sweep) has no backing file, so there is no
#     honest base directory to resolve a relative target against.  Guessing
#     one would manufacture false positives.
# ---------------------------------------------------------------------------


def _check_folder_dot_file_absent(graph: Graph, diags: list[Diagnostic]) -> None:
    """TOPO-010: a static relative ``dot_file=`` target that is absent on disk.

    Advisory only.  See the section comment above for the full skip list and
    the reason this can never be an ERROR.
    """
    if not graph.source_dir:
        return

    for node in graph.nodes.values():
        if not (node.shape == "folder" or node.type == "pipeline"):
            continue
        dot_file = node.attrs.get("dot_file")
        if not dot_file or not isinstance(dot_file, str):
            continue
        # Runtime-substituted target: its lint-time spelling is not a path.
        if "$" in dot_file:
            continue
        # Absolute target: lint-time absence says nothing about run time.
        if os.path.isabs(dot_file):
            continue

        resolved = os.path.join(graph.source_dir, dot_file)
        if os.path.exists(resolved):
            continue

        diags.append(
            Diagnostic(
                rule="folder_dot_file_absent",
                severity="WARNING",
                message=(
                    f"Node '{node.id}' (shape=folder) has "
                    f'dot_file="{dot_file}", which resolves to {resolved!r} — '
                    f"no such file at lint time.  If an upstream node writes "
                    f"this child graph during the run (write-then-run "
                    f"composition), this is expected and can be ignored: "
                    f"dot_file= resolution is lazy by design (EXTENSIONS.md "
                    f"§10).  If not, this node will fail at execution with a "
                    f"child-DOT resolution error (TOPO-010)."
                ),
                node_id=node.id,
                fix=(
                    f"Ship the child graph at {resolved!r}, or correct the "
                    f"dot_file= value — a relative dot_file= resolves against "
                    f"the parent .dot file's own directory FIRST "
                    f"(EXTENSIONS.md §10 precedence chain), not against --cwd.  "
                    f"If an upstream node generates this file at run time, no "
                    f"change is needed — this is a WARNING, not an error, and "
                    f"it does not affect the exit code."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Command-content lint rules — CMD-001 and CMD-002
#
# These rules inspect ``tool_command`` attribute strings on parallelogram
# (ToolHandler) nodes for two specific hazard shapes that cause the gate's
# exit code to lie: pipe-masked exit codes (CMD-001) and always-true trailing
# sentinels (CMD-002).
#
# Both rules are lint-only (WARNING severity) and do not change run-time
# behaviour.  They are conservative by design: a regex/tokenizer catching the
# two named shapes with low false positives beats an ambitious parser that
# misfires.  Each rule's docstring states what it does NOT catch.
#
# Real-world incident (2026-07-28): a 20-node pipeline ran 2.4 h and exited
# success with zero work product.  Every one of its 5 tool nodes was shaped
# ``cmd 2>&1 | tail -N``, and 4 ended ``&& echo SENTINEL``.  The incident
# graph linted "OK, no findings" on the pre-CMD main — these rules exist to
# close that gap.
#
# Severity decision: WARNING (not ERROR).
#   • Consistent with TOPO-002–005 (design smells, not provable defects).
#   • Does not break existing users' lint runs or force fixing shipped examples.
#   • The hazard is real but command content is not fully statically analysable;
#     conservative analysis may miss complex cases.
#   • ``test_examples_lint_clean.py`` only blocks on ERRORs, so WARNING leaves
#     the sweep untouched.
#
# Engine pipefail-default recommendation: DEFER.
#   • ``create_subprocess_shell`` targets ``/bin/sh``; ``pipefail`` is not
#     POSIX sh and is unavailable on some platforms.
#   • A behaviour change for every existing graph requires an EXTENSIONS.md
#     ledger entry and a compat inventory — that is a separate ledgered change.
#   • These lint rules are valuable either way: even under pipefail, a trailing
#     ``&& echo SENTINEL`` still masks nothing-happened cases, and authors
#     reading lint output learn the hazard.
# ---------------------------------------------------------------------------

# Recognised filter/pager programs whose presence as the final pipeline stage
# masks the real command's exit code.  ``tee`` is intentionally excluded: it
# preserves output (and is typically combined with redirection for logging).
_PIPE_FILTER_PROGRAMS: frozenset[str] = frozenset(
    {"tail", "head", "grep", "sed", "awk", "cut", "sort", "uniq", "wc", "xargs"}
)

# Regex for pipefail options following a standalone ``set`` builtin.  Detection
# is deliberately limited to a top-level command statement whose first word is
# ``set``; arbitrary text such as an ``echo`` argument or shell comment must
# not suppress a real finding.
_PIPEFAIL_OPTIONS_RE = re.compile(r"^set\s+-(?:[A-Za-z]*o\s+pipefail|o\s+pipefail)\b")

# Regex: matches ``&& echo TOKEN`` or ``&& printf TOKEN`` (with optional
# whitespace) at the end of a command segment.  The sentinel may be followed
# only by whitespace or end-of-string.
_SENTINEL_RE = re.compile(r"&&\s*(?:echo|printf)\s+\S+\s*$")


def _strip_command_substitutions(cmd: str) -> str:
    """Remove ``$(...)`` command substitutions from a shell command string.

    This is a conservative, non-recursive strip: it removes the innermost
    ``$(...)`` groups first (depth-1 only) so that pipes inside substitutions
    do not confuse the top-level pipeline analysis.  Nested substitutions are
    replaced with a placeholder that cannot match any pipe pattern.

    This is NOT a full shell parser — it handles the common case of
    ``sig=$(... | ...)`` without misidentifying the inner pipe as a top-level
    gate pipe.
    """
    # Iteratively strip innermost $(...) groups (no nested $() inside)
    # until no more are found.  Limit iterations to avoid pathological inputs.
    placeholder = "__SUBST__"
    for _ in range(20):
        new_cmd, count = re.subn(r"\$\([^()]*\)", placeholder, cmd)
        if count == 0:
            break
        cmd = new_cmd
    return cmd


def _strip_quoted_strings(cmd: str) -> str:
    """Return the command with all quoted string contents replaced by a placeholder.

    Removes the contents of single-quoted (``'...'``) and double-quoted
    (``"..."``) strings, replacing them with ``__QUOTED__``.  Backslash
    escapes inside double-quoted strings are respected.

    Used to make ``_has_executable_pipefail`` quote-aware: ``echo "set -o pipefail"``
    should not suppress CMD-001 because the ``set`` is inside a quoted
    argument to ``echo``, not an executable shell statement.

    This is NOT a full shell parser.  It does not handle ``$'...'`` ANSI-C
    quoting, heredocs, or nested quoting.  Conservative: when in doubt,
    the quoted content is stripped (reducing false suppressions).
    """
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if in_single:
            if ch == "'":
                in_single = False
                result.append("__QUOTED__")
            # else: skip quoted content
        elif in_double:
            if ch == "\\":
                i += 2  # skip escaped character
                continue
            elif ch == "\"":
                in_double = False
                result.append("__QUOTED__")
            # else: skip quoted content
        else:
            if ch == "'":
                in_single = True
            elif ch == "\"":
                in_double = True
            else:
                result.append(ch)
        i += 1
    return "".join(result)


def _has_executable_pipefail(cmd: str) -> bool:
    """Return whether a top-level ``set -o pipefail`` statement is present.

    This is intentionally narrower than searching for the words ``pipefail``.
    It recognizes a standalone ``set`` statement at the beginning of the
    command or immediately after a top-level semicolon/newline.  Thus quoted
    output, comments, and conditional text such as ``false && set -o
    pipefail`` cannot suppress a finding when the setting may not execute.
    This conservative scanner is not a general shell parser.
    """
    unquoted = _strip_quoted_strings(cmd)
    for statement in re.split(r"[;\n]", unquoted):
        statement = statement.strip()
        if not statement or statement.startswith("#"):
            continue
        if _PIPEFAIL_OPTIONS_RE.match(statement):
            return True
    return False


def _final_semicolon_segment(cmd: str) -> str:
    """Return the final ``;``-separated segment of a shell command string.

    Splits only on top-level ``;`` (not ``&&`` or ``||``) outside quotes and
    parentheses, and returns the last segment.  This is the segment that
    unconditionally executes last and whose exit code determines the overall
    command's exit code when the command uses ``;`` as a separator.

    Deliberately does NOT split on ``&&`` or ``||`` — those chains are
    preserved within the final segment.  That is important for CMD-001:
    ``cmd | tail && echo SENTINEL``
    is a single semicolon-segment whose pipe is still the hazard, whereas
    ``cmd | tail; echo done`` has a clean final segment (``echo done``) that
    determines the exit code.

    Conservative: does not handle here-docs, process substitution, or deeply
    nested constructs.  Returns the whole command if no ``;`` is found.
    """
    segments: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    current: list[str] = []
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if in_single:
            if ch == "'":
                in_single = False
            current.append(ch)
        elif in_double:
            if ch == "\\":
                current.append(ch)
                i += 1
                if i < len(cmd):
                    current.append(cmd[i])
            elif ch == "\"":
                in_double = False
                current.append(ch)
            else:
                current.append(ch)
        else:
            if ch == "'":
                in_single = True
                current.append(ch)
            elif ch == "\"":
                in_double = True
                current.append(ch)
            elif ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1)
                current.append(ch)
            elif depth == 0 and ch == ";":
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
            else:
                current.append(ch)
        i += 1
    seg = "".join(current).strip()
    if seg:
        segments.append(seg)
    return segments[-1] if segments else cmd.strip()


def _last_pipe_stage_program(segment: str) -> str | None:
    """Return the program name of the last pipe stage in a command segment.

    Given a segment like ``cmd 2>&1 | tail -30``, returns ``"tail"``.
    Returns ``None`` if the segment contains no pipe.

    Conservative: splits on ``|`` but excludes ``||`` (logical OR).
    Does not handle pipes inside subshells or quotes.
    """
    # Split on | but not ||
    # Replace || with a placeholder to avoid splitting on it
    safe = segment.replace("||", "\x00\x00")
    if "|" not in safe:
        return None
    stages = safe.split("|")
    if len(stages) < 2:
        return None
    last_stage = stages[-1].strip()
    if not last_stage:
        return None
    # Extract the first word (program name), ignoring leading env vars (VAR=val)
    # and redirections (2>&1, >/dev/null, etc.)
    tokens = last_stage.split()
    for token in tokens:
        # Skip redirections and env-var assignments
        if re.match(r"^\d*[<>]", token) or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            continue
        # Return the base program name (strip path prefix)
        return token.split("/")[-1]
    return None


def _check_pipe_masked_exit_code(graph: Graph, diags: list[Diagnostic]) -> None:
    """CMD-001: Tool node whose exit code is a filter's, not the real command's.

    Detects parallelogram (ToolHandler) nodes whose ``tool_command`` ends in a
    filter/pager stage (``tail``, ``head``, ``grep``, ``sed``, ``awk``,
    ``cut``, ``sort``, ``uniq``, ``wc``, ``xargs``) without ``set -o pipefail``.

    In plain ``/bin/sh`` (the engine's execution environment), a pipeline's
    exit status is the LAST stage's.  ``false | tail -1`` exits 0 whenever
    ``tail`` succeeds — always.  The gate records SUCCESS no matter what the
    real command did.

    What this rule does NOT catch:
    - Pipes inside ``$(...)`` command substitutions (intentionally excluded —
      the substitution result is captured, not used as the gate's exit code).
    - Pipes inside ``$'...'`` ANSI-C quoting or backtick substitutions.
    - Complex nested subshells or here-docs.
    - Filter programs not in the recognised set (e.g. custom scripts).
    - ``bash -o pipefail -c '...'`` wrappers (pipefail not detected inside
      the quoted string argument — ``bash -o pipefail`` wrapping is a valid
      but undetected suppression).
    - Pipes inside single- or double-quoted strings ARE correctly skipped by
      the quote-aware scanner (e.g. ``echo 'false | tail -1'`` is safe).
    - Explicit exit-code capture (``cmd | tail; rc=$?; ...``) is not
      recognised as a suppressor — use ``set -o pipefail`` or the redirect
      idiom (``cmd > out.log 2>&1``) for a suppression that lint detects.
    - Commands where the pipe appears in a non-final ``;``-separated segment
      (e.g. ``false | tail -1; echo done``) are NOT flagged — the final
      segment ``echo done`` determines the exit code.

    Severity: WARNING — the hazard is real but static analysis cannot prove
    the command is a meaningful gate; conservative analysis may miss cases.
    """
    for node in graph.nodes.values():
        if not _is_tool(node):
            continue
        raw_cmd: str = str(node.attrs.get("tool_command") or "").strip()
        if not raw_cmd:
            continue

        # If the command explicitly sets pipefail, the hazard is mitigated.
        # _has_executable_pipefail matches only standalone ``set`` statements
        # in quote-stripped text, so ``echo "set -o pipefail"; false | tail -1``
        # is NOT suppressed — the ``set`` is inside a quoted argument to
        # ``echo``, not an executable shell statement.
        if _has_executable_pipefail(raw_cmd):
            continue

        # Strip command substitutions so inner pipes don't confuse analysis.
        cmd = _strip_command_substitutions(raw_cmd)

        # Scope the analysis to the final ``;``-separated segment.
        # ``false | tail -1; echo done`` — the final segment is ``echo done``
        # (exit code 0, no pipe hazard), so CMD-001 must NOT fire.
        # ``false | tail -1 && echo SENTINEL`` — the whole command is one
        # semicolon-segment; the pipe is still the exit-code hazard, so
        # CMD-001 DOES fire (and CMD-002 catches the sentinel separately).
        final_seg = _final_semicolon_segment(cmd)

        # Find the last non-|| pipe position in the final segment.
        last_pipe_pos = _find_last_bare_pipe(final_seg)
        if last_pipe_pos is None:
            continue

        # Extract the stage after the last bare pipe.
        after_pipe = final_seg[last_pipe_pos + 1 :]
        program = _last_pipe_stage_program("|" + after_pipe)  # re-use helper
        if program is None or program not in _PIPE_FILTER_PROGRAMS:
            continue

        # NOTE: a ``||`` branch after the pipe does NOT suppress CMD-001.
        # ``false | tail -1 && printf green || printf red`` prints ``green``
        # unconditionally — ``tail`` exits 0, so ``|| printf red`` never fires
        # for the original failure.  The only genuinely honest ``||`` shapes
        # are those WITHOUT a masking pipe: ``cmd && printf green || printf
        # red`` (no pipe), which are already not flagged because
        # ``_find_last_bare_pipe`` finds no filter in that position.

        diags.append(
            Diagnostic(
                rule="CMD-001",
                severity="WARNING",
                message=(
                    f"Tool node '{node.id}' tool_command ends in a pipe to "
                    f"'{program}' without pipefail: the gate's "
                    f"exit code is '{program}'s (always 0 on success), not the "
                    f"real command's.  In /bin/sh a pipeline's exit status is "
                    f"the last stage's — so 'false | tail -1' exits 0.  The "
                    f"gate may record SUCCESS even when the wrapped command "
                    f"failed.  Fix: redirect output to a file "
                    f"('cmd > out.log 2>&1') to preserve exit code, or capture "
                    f"exit code explicitly ('cmd; rc=$?; ... && printf ok || "
                    f"printf fail').  See DOT-AUTHORING-GUIDE.md (CMD-001)."
                ),
                node_id=node.id,
                fix=(
                    "Replace 'cmd 2>&1 | tail -N' with 'cmd > out.log 2>&1' "
                    "to preserve the real exit code.  Alternatively, capture "
                    "the exit code: 'cmd; rc=$?; [ $rc -eq 0 ] && printf ok "
                    "|| { printf fail; exit 1; }'.  If you need to see the "
                    "last N lines, write to a file and read it separately."
                ),
            )
        )


def _find_last_bare_pipe(cmd: str) -> int | None:
    """Return the index of the last ``|`` that is not part of ``||``.

    Quote-aware: pipes inside single-quoted or double-quoted strings are
    skipped so that ``echo 'false | tail -1'`` does not produce a false
    positive.  Backslash escapes inside double-quoted strings are respected.

    Returns ``None`` if no bare pipe is found outside of quotes.
    """
    in_single = False
    in_double = False
    last_pipe: int | None = None
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2  # skip escaped character
                continue
            elif ch == "\"":
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == "\"":
                in_double = True
            elif ch == "|":
                # Check it's not part of ||
                prev_is_pipe = i > 0 and cmd[i - 1] == "|"
                next_is_pipe = i + 1 < n and cmd[i + 1] == "|"
                if not prev_is_pipe and not next_is_pipe:
                    last_pipe = i
        i += 1
    return last_pipe


def _check_always_true_sentinel(graph: Graph, diags: list[Diagnostic]) -> None:
    """CMD-002: Trailing ``&& echo/printf TOKEN`` after a pipe-masked command.

    Detects parallelogram (ToolHandler) nodes whose ``tool_command`` contains
    a pipe to a filter/pager followed by ``&& echo TOKEN`` or
    ``&& printf TOKEN`` at the end of the command.  The sentinel fires
    regardless of whether the wrapped command succeeded, making
    ``tool.last_line`` the sentinel string rather than evidence.

    Example hazard: ``sh -c 'exit 1' 2>&1 | tail -5 && echo GREEN``
    - ``tail`` exits 0 (it read stdin fine), so ``&& echo GREEN`` fires.
    - ``tool.last_line`` becomes ``GREEN`` regardless of the inner command.
    - The routing channel says "success" unconditionally.

    Contrast with the honest token-gate idiom (NOT flagged):
    - ``cmd && printf green || printf red`` — no pipe; the token gate is
      honest because ``cmd``'s exit code gates the ``&&``.
    - ``cmd && printf green || { printf red; exit 1; }`` — exit-code gate;
      failure is preserved.  Neither has a masking pipe before the sentinel.

    NOTE: a ``||`` branch does NOT suppress CMD-002.  For example,
    ``false | tail -1 && printf green || printf red`` is still hazardous:
    ``tail`` exits 0 so ``printf green`` fires unconditionally.

    What this rule does NOT catch:
    - Sentinels inside ``$(...)`` substitutions.
    - Sentinels after non-pipe-masked commands (where ``&& echo TOKEN`` is the
      honest token-gate idiom and is safe — CMD-002 only fires when the
      command is already pipe-masked).
    - Multi-line or heredoc command structures.
    - Variable-interpolated filter names (e.g. ``| $FILTER``).
    - Commands where the sentinel is followed by ``|| ...`` at the end (those
      end with the ``||`` branch, not the sentinel, so ``_SENTINEL_RE`` does
      not match).

    Severity: WARNING — consistent with CMD-001 and the TOPO rule family.
    """
    for node in graph.nodes.values():
        if not _is_tool(node):
            continue
        raw_cmd: str = str(node.attrs.get("tool_command") or "").strip()
        if not raw_cmd:
            continue

        # If the command explicitly sets pipefail, the hazard is mitigated.
        # _has_executable_pipefail matches only standalone ``set`` statements
        # in quote-stripped text, so ``echo "set -o pipefail"; false | tail -1
        # && echo GREEN`` is NOT suppressed — the ``set`` is inside a quoted
        # argument, not executed.
        if _has_executable_pipefail(raw_cmd):
            continue

        # Strip command substitutions so inner pipes don't confuse analysis.
        cmd = _strip_command_substitutions(raw_cmd)

        # Scope the analysis to the final ``;``-separated segment.
        # ``false | tail -1; echo done && echo SENTINEL`` — the final segment
        # is ``echo done && echo SENTINEL`` (no pipe), so CMD-002 must NOT
        # fire.  ``false | tail -1 && echo SENTINEL`` — the whole command is
        # one semicolon-segment; the pipe+sentinel hazard is present.
        final_seg = _final_semicolon_segment(cmd)

        # CMD-002 pattern: a pipe to a filter FOLLOWED BY && echo/printf TOKEN
        # at the end of the final segment.
        #
        # We look for: ... | <filter> [args] && (echo|printf) TOKEN [end]
        # where [end] means end-of-string or only whitespace.
        #
        # NOTE: a || branch does NOT suppress this rule.  The sentinel fires
        # unconditionally when the pipe stage is a filter that always exits 0.
        #
        # The sentinel must NOT be followed by || (that would be an honest
        # token gate).

        # Find positions of all bare pipes in the final segment.
        pipe_positions = _find_all_bare_pipes(final_seg)
        if not pipe_positions:
            continue

        for pipe_pos in pipe_positions:
            after_pipe = final_seg[pipe_pos + 1 :]
            program = _last_pipe_stage_program("|" + after_pipe)
            if program is None or program not in _PIPE_FILTER_PROGRAMS:
                continue

            # There's a pipe to a filter.  Now check if the remainder of the
            # final segment (after this pipe's stage) ends with
            # && echo/printf TOKEN.
            #
            # Extract the text from this pipe to the end of the final segment.
            remainder = final_seg[pipe_pos:]

            # Does the remainder contain a sentinel?
            sentinel_match = _SENTINEL_RE.search(remainder)
            if not sentinel_match:
                continue

            # NOTE: a ``||`` branch does NOT suppress CMD-002.
            # ``false | tail -1 && printf green || printf red`` is still a
            # hazard: ``tail`` exits 0 so ``printf green`` fires unconditionally
            # and ``|| printf red`` never fires for the original failure.
            # The honest token-gate idiom is ``cmd && printf green || printf
            # red`` WITHOUT a masking pipe — those are not flagged because
            # ``_find_all_bare_pipes`` finds no filter stage in that position.

            diags.append(
                Diagnostic(
                    rule="CMD-002",
                    severity="WARNING",
                    message=(
                        f"Tool node '{node.id}' tool_command has a trailing "
                        f"'&& echo/printf TOKEN' sentinel after a pipe-masked "
                        f"command (pipe to '{program}').  The sentinel fires "
                        f"unconditionally because '{program}' always exits 0 "
                        f"when it can read its input — so tool.last_line "
                        f"becomes the sentinel string regardless of whether the "
                        f"wrapped command succeeded.  The gate always says yes.  "
                        f"Fix: use the honest token-gate idiom "
                        f"'cmd && printf ok || printf fail' (no pipe), or "
                        f"redirect output to a file and test the exit code "
                        f"explicitly.  See DOT-AUTHORING-GUIDE.md (CMD-002)."
                    ),
                    node_id=node.id,
                    fix=(
                        "Replace '... | tail -N && echo TOKEN' with the honest "
                        "token-gate idiom: 'cmd && printf ok || printf fail' "
                        "(no pipe; exit code gates the token).  Or redirect to "
                        "a file: 'cmd > out.log 2>&1 && printf ok || printf "
                        "fail'.  The || branch is what makes the gate honest."
                    ),
                )
            )
            break  # One CMD-002 diagnostic per node is enough


def _find_all_bare_pipes(cmd: str) -> list[int]:
    """Return indices of all ``|`` characters that are not part of ``||``.

    Quote-aware: pipes inside single-quoted or double-quoted strings are
    skipped so that ``printf "false | tail -1"`` does not produce a false
    positive.  Backslash escapes inside double-quoted strings are respected.

    Scans left-to-right.
    """
    positions: list[int] = []
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2  # skip escaped character
                continue
            elif ch == "\"":
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == "\"":
                in_double = True
            elif ch == "|":
                prev_is_pipe = i > 0 and cmd[i - 1] == "|"
                next_is_pipe = i + 1 < n and cmd[i + 1] == "|"
                if not prev_is_pipe and not next_is_pipe:
                    positions.append(i)
        i += 1
    return positions


# ---------------------------------------------------------------------------
# Inert-vocabulary rule — VOCAB-001
#
# The failure this catches is silent by construction.  `_NODE_FIELD_MAP` in
# `dot_parser.py` promotes exactly {label, shape, type, prompt} to Node
# fields; every other attribute survives as an inert `node.attrs` entry that
# no handler ever reads.  So a `.dot` authored with `instruction=` instead of
# `prompt=` parses cleanly, validates cleanly, and runs its LLM nodes with no
# prompt at all — there is no error, no warning, and no diff between
# "configured" and "unconfigured" that a reader can see.
#
# Measured (issue #261): two graded sessions authored twelve-node pipelines
# carrying `instruction=` on all twelve nodes and `prompt=` on none.  One of
# them linted rc=0 with a single unrelated warning.
#
# `prompt_on_llm_nodes` (in `validate()`) is the near-miss sibling: it fires
# only when a codergen node has NO prompt *and* NO explicit label.  Both
# evidence files label every node, so it stayed silent on all twenty-four.
# This rule asks the sharper question — is the prompt *missing*, and is there
# an inert attribute here that the author plainly wrote as the prompt?
# ---------------------------------------------------------------------------

# Invented spellings the reference card (`context/dot-reference.md`) names as
# inert, mapped to the attribute the engine actually reads.  Deliberately
# limited to the card's own table: the card is declared the single attribute
# vocabulary, so the lint rule and the card cannot drift into two lists.
#
# `goal` is node-scoped here.  Graph-level `goal=` is real (it is in
# `dot_parser._GRAPH_FIELD_ATTRS` and backs `$goal` substitution); a
# *node*-level `goal=` is not read by anything.  This rule only ever inspects
# `node.attrs`, so the real graph attribute is never touched.
_INERT_PROMPT_SPELLINGS: dict[str, str] = {
    "instruction": "prompt",
    "goal": "prompt",
    "attractor_goal": "prompt",
}

# Invented spellings for "who executes this node".  A node carrying one of
# these was written as though naming an executor were the same as configuring
# one; the engine picks the executor from `shape=` alone.  They are reported
# by the same rule because the *consequence* is identical and worse: the node
# has no prompt AND the delegation the author wrote is inert.
_INERT_HANDLER_SPELLINGS: dict[str, str] = {
    "agent": "shape=box",
    "handler": "shape=box",
    "attractor_handler": "shape=box",
}


def _is_codergen_node(node: Node) -> bool:
    """True when this node will actually dispatch to the LLM (codergen) handler.

    Deliberately stricter than ``prompt_on_llm_nodes``'s
    ``SHAPE_TO_HANDLER.get(node.shape, "codergen")``: an *unrecognized* shape
    is NOT treated as an LLM node here.  Since PR #19 the engine refuses at
    dispatch rather than falling back to codergen (``HandlerRegistry.get()``
    raises; specs/EXTENSIONS.md §38), and ``shape_resolvable`` already emits
    an ERROR for exactly that node.  Treating an unknown shape as codergen
    would double-diagnose a node the author is already being told to fix.

    A missing ``shape=`` is codergen: ``Node.shape`` defaults to ``"box"``.
    """
    if node.is_start_node() or node.is_exit_node():
        return False
    if node.type:
        return node.type == "codergen"
    return SHAPE_TO_HANDLER.get(node.shape) == "codergen"


def _check_inert_prompt_vocabulary(graph: Graph, diags: list[Diagnostic]) -> None:
    """VOCAB-001: an LLM node with no ``prompt=`` that carries an inert spelling.

    Fires when ALL of the following hold:

    * the node dispatches to the codergen (LLM) handler — see
      ``_is_codergen_node``;
    * ``node.prompt`` is empty — the node will run with no prompt at all;
    * ``node.attrs`` carries at least one spelling from the reference card's
      invented-attribute table (``instruction=``, ``agent=``, …).

    Deliberately silent on:

    * a node that has ``prompt=`` and *also* carries some other attribute —
      the prompt is real, so the node is configured.  Carrying an extra
      attribute is not by itself a defect, and warning about it would make
      the rule noise on every graph using a custom passthrough attribute.
    * a tool / human-gate / conditional / fan-in node with no prompt — those
      never take one, and their real configuration lives in ``tool_command=``,
      ``goal_gate=``, ``condition=``.
    * a node with a typo'd shape — ``shape_resolvable`` owns that node.
    * a node with no prompt and no inert spelling — that is
      ``prompt_on_llm_nodes``'s existing territory, not this rule's.

    Severity: WARNING.  It must never be an ERROR: the diagnosis rests on
    reading the author's *intent* from an attribute the engine is entitled to
    ignore, and a graph can legitimately carry passthrough attributes this
    rule does not know about.  Advisory keeps ``attractor lint`` at rc=0 and
    keeps ``validate_or_raise()`` (the admission gate) untouched.
    """
    for node in graph.nodes.values():
        if not _is_codergen_node(node):
            continue
        if node.prompt:
            continue  # configured — an extra attribute alongside a real prompt is not a defect

        found_prompt = [a for a in _INERT_PROMPT_SPELLINGS if a in node.attrs]
        found_handler = [a for a in _INERT_HANDLER_SPELLINGS if a in node.attrs]
        if not found_prompt and not found_handler:
            continue  # no prompt, but nothing here claims to be one

        carried = ", ".join(f"`{a}=`" for a in found_prompt + found_handler)
        if found_prompt:
            reads = "`prompt=`"
            fix = (
                f"Rename {', '.join(f'`{a}=`' for a in found_prompt)} to `prompt=` on "
                f"node '{node.id}'."
            )
        else:
            reads = "`prompt=`, and picks the handler from `shape=` alone"
            fix = (
                f"Add a `prompt=` attribute to node '{node.id}'; "
                f"{', '.join(f'`{a}=`' for a in found_handler)} does not select a handler "
                f"(use `shape=box` for an LLM node)."
            )

        diags.append(
            Diagnostic(
                rule="VOCAB-001",
                severity="WARNING",
                message=(
                    f"LLM node '{node.id}' will run with no prompt: it carries "
                    f"{carried} but the engine reads {reads}.  The parser keeps "
                    f"unknown attributes on the node and no handler ever looks at "
                    f"them, so this node is inert while reading as configured.  "
                    f"See context/dot-reference.md for the full attribute "
                    f"vocabulary and DOT-AUTHORING-GUIDE.md (VOCAB-001)."
                ),
                node_id=node.id,
                fix=fix,
            )
        )


# ---------------------------------------------------------------------------
# Render-compliance rules -- RENDER-001 and RENDER-002
#
# A `.dot` file is read by TWO validators with different strictness: our own
# `dot_parser` (lenient -- a hand-written tokenizer over the spec Section 2
# subset) and `dot -Tsvg` (strict -- the real GraphViz grammar).  They do not
# agree.  Measured on this repository: 6 of 63 git-tracked `.dot` files parsed
# and linted cleanly while failing `dot -Tsvg` with a syntax error.  They ran
# fine.  They just could not be drawn.
#
# That matters because the spec chose DOT *because* it renders -- "free
# visualization, PR-reviewable" is the stated reason the format was picked.  A
# shipped graph that cannot be drawn has quietly stopped paying that rent.
#
# Both divergence classes are statically decidable from the tokens the engine's
# own tokenizer already produces, so these rules take NO GraphViz dependency:
# they never shell out, and they work on a machine with no `dot` binary.
#
# Severity is WARNING and must stay WARNING.  A non-rendering graph is
# CONFORMING to the runtime contract -- our parser accepts it and the engine
# runs it.  Promoting renderability to an ERROR would break a community
# author's working pipeline for a reason unrelated to whether it works.  See
# docs/designs/2026-08-23-dot-render-compliance.md for the tier split (runtime
# contract unchanged / lint advisory for everyone / CI render gate for this
# repo only).
# ---------------------------------------------------------------------------

# Characters that may legally abut a quoted string in DOT.  `->` is handled
# separately as a two-character sequence.
#
# `:` (port syntax, `node:port:compass`) and `+` (string concatenation,
# `"a" + "b"`) are included for false-positive discipline: both are legal DOT
# that this repo's subset does not use, and neither our tokenizer nor this rule
# should claim a community graph is broken for using them.
_RENDER_SEPARATOR_CHARS = frozenset("=,;[]{}:+")

# GraphViz's own NAME production (lexer, `graphviz/lib/cgraph/scan.l`):
# an alphabetic character or underscore, followed by alphanumerics or
# underscores.  Notably NO `.`.  The \200-\377 range is GraphViz's allowance
# for high-byte characters in the C locale.
_GRAPHVIZ_NAME_RE = re.compile(r"^[A-Za-z_\u0080-\u00ff][A-Za-z_\u0080-\u00ff0-9]*$")

# GraphViz's NUMERAL production: an optional sign, then digits with an optional
# fractional part, or a leading-dot fraction.  `1.5`, `.5` and `1.` are legal
# NUMERALs -- a `.` in a NUMERAL is not a defect.
_GRAPHVIZ_NUMERAL_RE = re.compile(r"^-?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)$")


def _blank_comments(source: str) -> str:
    """Blank out `//` and `/* */` comments, preserving every byte offset.

    ``dot_parser._strip_comments`` DELETES comment text, which shifts every
    subsequent offset and would make reported line numbers wrong.  This helper
    replaces comment characters with spaces instead (newlines are preserved
    verbatim), so ``source`` and the returned string are the same length and a
    match offset maps to the same line in the original file.

    Blanking is required, not cosmetic: ``parent.dot`` documents the dotted-key
    form in a ``//`` comment, and a rule that scanned raw source would flag the
    documentation rather than the code.

    Quoted-string handling mirrors ``_strip_comments`` exactly -- a ``//``
    inside a string (a URL, a shell command) is content, not a comment.
    """
    out: list[str] = []
    i = 0
    length = len(source)
    while i < length:
        ch = source[i]
        if ch == '"':
            # Inside a quoted string -- copy verbatim, honouring backslash escapes.
            j = i + 1
            while j < length:
                if source[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if source[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(source[i:j])
            i = j
        elif source[i : i + 2] == "//":
            j = source.find("\n", i)
            if j == -1:
                j = length
            out.append(" " * (j - i))
            i = j
        elif source[i : i + 2] == "/*":
            j = source.find("*/", i + 2)
            end = length if j == -1 else j + 2
            # Preserve newlines so line numbers after a multi-line block
            # comment stay correct.
            out.append("".join("\n" if c == "\n" else " " for c in source[i:end]))
            i = end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _line_of(source: str, offset: int) -> int:
    """1-based line number of a byte offset in ``source``."""
    return source.count("\n", 0, offset) + 1


def _abuts_separator(source: str, before: int, after: int) -> tuple[bool, bool]:
    """Report whether the characters bracketing ``[before, after)`` are legal.

    Returns ``(left_ok, right_ok)``.  A boundary is OK when it is the start/end
    of the source, whitespace, one of ``_RENDER_SEPARATOR_CHARS``, or part of
    the two-character ``->`` edge operator.
    """
    left_ok = True
    if before > 0:
        c = source[before - 1]
        left_ok = (
            c.isspace()
            or c in _RENDER_SEPARATOR_CHARS
            # `a->"b"` -- the `>` of the edge operator is a legal neighbour.
            or (before >= 2 and source[before - 2 : before] == "->")
        )

    right_ok = True
    if after < len(source):
        c = source[after]
        right_ok = (
            c.isspace()
            or c in _RENDER_SEPARATOR_CHARS
            # `"a"->b` -- the `-` opening the edge operator is legal.
            or source[after : after + 2] == "->"
        )
    return left_ok, right_ok


def _check_unescaped_inner_quote(graph: Graph, diags: list[Diagnostic]) -> None:
    """RENDER-001: a raw ``"`` inside a quoted attribute string closes it early.

    GraphViz requires an interior double-quote to be escaped as ``\\"``.  An
    unescaped one terminates the string, and the grammar then sees a bare
    identifier where it expects ``,`` or ``]``::

        tool_command="n=$(grep -c '"gate": "verify"' log.jsonl)"
                                   ^ closes the string here

    Our tokenizer survives this because it never re-checks that the token
    stream following a string is grammatical.  GraphViz does, and refuses.

    Detection is purely lexical: tokenize with the engine's own ``_TOKEN_RE``
    and flag any STRING token that ABUTS a non-separator character on either
    side (no whitespace, no ``= , ; [ ] { } : +``, no ``->``).  A correctly
    escaped ``\\"`` is consumed INSIDE the string token by the same regex, so
    legitimate escapes are structurally incapable of firing this rule.

    One finding per source line -- a mangled command string produces a cascade
    of abutting tokens, and reporting each one would bury the actual location.

    Honest limit: this flags the common single-abutment case but does NOT catch
    every GraphViz string-quoting violation -- a value that re-opens a string
    mid-attribute (``prompt="a " b "c"``) leaves every token separator-clean
    and so stays silent here even though ``dot -Tsvg`` rejects the file; Tier
    3's ``dot -Tsvg`` CI render gate is the exhaustive backstop.

    Silent when ``graph.dot_source`` is empty: a programmatically-constructed
    Graph has no source text, and inventing a finding there would be a lie.

    Severity: WARNING.  See the family comment above.
    """
    source = graph.dot_source
    if not source:
        return

    from .dot_parser import _TOKEN_RE  # local import -- avoids an import cycle

    scan = _blank_comments(source)
    seen_lines: set[int] = set()
    for m in _TOKEN_RE.finditer(scan):
        if not m.group("string"):
            continue
        left_ok, right_ok = _abuts_separator(scan, m.start(), m.end())
        if left_ok and right_ok:
            continue
        line = _line_of(scan, m.start())
        if line in seen_lines:
            continue
        seen_lines.add(line)
        # Honest limit (see docstring): this abutment test flags the common
        # single-abutment case but does NOT catch every GraphViz string-quoting
        # violation -- a value that re-opens a string mid-attribute
        # (`prompt="a " b "c"`) keeps every token separator-clean and slips
        # through; Tier 3's `dot -Tsvg` CI render gate is the exhaustive
        # backstop.
        diags.append(
            Diagnostic(
                rule="RENDER-001",
                severity="WARNING",
                message=(
                    f"Line {line}: a quoted attribute string is closed early by an "
                    f"unescaped inner double-quote, so the text immediately after it "
                    f"is read as a bare identifier.  The engine's tokenizer tolerates "
                    f"this, but `dot -Tsvg` rejects the file, so this graph runs and "
                    f"cannot be drawn.  See DOT-AUTHORING-GUIDE.md (RENDER-001)."
                ),
                fix=(
                    "Escape every interior double-quote inside the attribute "
                    "value as backslash-quote.  A raw inner quote ends the "
                    "string; an escaped one is part of it.  Escaping does not "
                    "change the value the engine parses -- it repairs it, since "
                    "the engine currently stores the truncated prefix."
                ),
            )
        )


def _check_dotted_bare_identifier(graph: Graph, diags: list[Diagnostic]) -> None:
    """RENDER-002: a bare identifier containing ``.`` is not a legal GraphViz ID.

    GraphViz's ``NAME`` production is ``[A-Za-z_][A-Za-z_0-9]*`` -- no dot.  A
    dotted attribute key is legal ONLY when quoted.  This engine's tokenizer
    deliberately diverges: its ident pattern allows the qualified form
    ``ident(.ident)*``, which is load-bearing for the folder-node
    ``context.*`` injection mechanism and the ``manager.*`` / ``stack.*``
    manager-loop attributes.  So ``manager.max_cycles=5`` parses for us and is
    a syntax error for GraphViz::

        manager [shape=house, manager.max_cycles=5]      // WRONG -- no picture
        manager [shape=house, "manager.max_cycles"=5]    // CORRECT

    Quoting the key is semantically inert: ``dot_parser._unquote_key`` strips
    the quotes and does NOT coerce types, so the attribute dict is byte- and
    type-identical either way (``manager.max_cycles`` stays the int ``5``).

    A dot inside a NUMERAL (``1.5``, ``.5``, ``1.``) is legal and never flagged.

    Honest limit: this sees what the tokenizer sees.  A pathological form the
    tokenizer splits into two individually-legal tokens (``a.5`` -> ident ``a``
    + numeral ``.5``) is not caught here.  Tier 3's ``dot -Tsvg`` CI sweep is
    the backstop for anything static analysis of our own token stream misses.

    Silent when ``graph.dot_source`` is empty.

    Severity: WARNING.  See the family comment above.
    """
    source = graph.dot_source
    if not source:
        return

    from .dot_parser import _TOKEN_RE  # local import -- avoids an import cycle

    scan = _blank_comments(source)
    seen: set[tuple[int, str]] = set()
    for m in _TOKEN_RE.finditer(scan):
        text = m.group("ident") or m.group("number")
        if not text or "." not in text:
            continue
        if _GRAPHVIZ_NAME_RE.match(text) or _GRAPHVIZ_NUMERAL_RE.match(text):
            continue

        line = _line_of(scan, m.start())
        if (line, text) in seen:
            continue
        seen.add((line, text))

        # Position hint -- the fix is the same either way (quote it), but
        # naming the role makes the finding actionable at a glance.
        after = scan[m.end() :].lstrip()
        before = scan[: m.start()].rstrip()
        if after[:1] == "=":
            role = "attribute key"
        elif before[-1:] == "=":
            role = "attribute value"
        else:
            role = "identifier"

        diags.append(
            Diagnostic(
                rule="RENDER-002",
                severity="WARNING",
                message=(
                    f"Line {line}: bare {role} `{text}` contains a `.`, which "
                    f"GraphViz's NAME production does not allow (it is neither a "
                    f"legal NAME nor a NUMERAL).  This engine's tokenizer accepts "
                    f"the qualified form, so the graph runs -- but `dot -Tsvg` "
                    f"rejects the file, so it cannot be drawn.  See "
                    f"DOT-AUTHORING-GUIDE.md (RENDER-002)."
                ),
                fix=(
                    f'Quote it: write "{text}"=... instead of {text}=... .  '
                    f"Quoting an attribute key is semantically inert -- "
                    f"`_unquote_key` strips the quotes without coercing types, so "
                    f"the parsed attribute dict is identical."
                ),
            )
        )
