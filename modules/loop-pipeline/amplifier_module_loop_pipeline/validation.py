"""Graph validation and lint rules for Attractor pipelines.

Validates parsed Graph models against the rules defined in
spec Section 7 (Validation and Linting). Produces Diagnostic objects
with severity ERROR (blocks execution) or WARNING (informational).

Spec coverage: LINT-001–018.  TOPO-001–005 are topological basin-lint rules
implemented here beyond the canonical spec; they are lint-only (exposed via
``lint()``, not ``validate()``) and do not change run-time behaviour.

CMD-001–002 are command-content lint rules that inspect ``tool_command``
strings for two specific hazard shapes: pipe-masked exit codes (CMD-001) and
always-true trailing sentinels (CMD-002).  Both are lint-only (WARNING
severity) and do not change run-time behaviour.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .conditions import evaluate_condition, parse_condition
from .context import PipelineContext
from .fidelity import VALID_FIDELITY_MODES
from .graph import Graph, Node, resolve_bool_attr
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
    the full structural ``validate()`` suite plus the five topological rules
    (TOPO-001–005) that reason about cycle structure, handler semantics, and
    evidence-routing patterns, plus the two command-content rules (CMD-001–002)
    that inspect ``tool_command`` strings for hazard shapes.

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
    _check_pipe_masked_exit_code(graph, diags)
    _check_always_true_sentinel(graph, diags)
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
        if resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate"):
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
# Topological (basin-lint) rules — TOPO-001 through TOPO-005
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
