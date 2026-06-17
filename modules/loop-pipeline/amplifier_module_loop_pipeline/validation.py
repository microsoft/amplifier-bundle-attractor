"""Graph validation and lint rules for Attractor pipelines.

Validates parsed Graph models against the rules defined in
spec Section 7 (Validation and Linting). Produces Diagnostic objects
with severity ERROR (blocks execution) or WARNING (informational).

Spec coverage: LINT-001–018
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .conditions import evaluate_condition
from .context import PipelineContext
from .fidelity import VALID_FIDELITY_MODES
from .graph import Graph
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
    _check_fidelity_valid(graph, diags)
    _check_retry_target_exists(graph, diags)

    # L-19: Run user-supplied extra rules
    for rule in extra_rules or []:
        diags.extend(rule(graph))

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
    """LINT: goal_gate_has_retry — goal gates should have retry targets."""
    for node in graph.nodes.values():
        if node.attrs.get("goal_gate") in (True, "true"):
            has_retry = bool(
                node.attrs.get("retry_target")
                or node.attrs.get("fallback_retry_target")
                or graph.graph_attrs.get("retry_target")
            )
            if not has_retry:
                diags.append(
                    Diagnostic(
                        rule="goal_gate_has_retry",
                        severity="WARNING",
                        message=f"Node '{node.id}' has goal_gate=true but no retry_target",
                        node_id=node.id,
                        fix="Add retry_target or fallback_retry_target attribute",
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
