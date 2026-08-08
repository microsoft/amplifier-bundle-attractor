"""Built-in transforms for pipeline graph preprocessing.

Transforms modify the pipeline graph after parsing and before execution.
They run in a defined order: variable expansion first, then stylesheet
application, then any custom transforms.

Spec coverage: XFORM-001-006, Section 9.2

Built-in transforms:
    expand_variables        — Replace $goal in node prompts with the graph goal.
    resolve_response_schemas — Resolve response_schema attrs to dicts (EXT §23).
    apply_transforms        — Run all built-in transforms in order.

M-20: Formal Transform protocol for custom transforms.
L-17: Shared expand_goal_variable utility (single source of truth).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol, runtime_checkable

from .context import PipelineContext
from .graph import Graph
from .stylesheet import apply_stylesheet, parse_stylesheet

# ---------------------------------------------------------------------------
# L-17: Shared variable expansion utility
# ---------------------------------------------------------------------------


def expand_params(text: str, params: dict[str, str]) -> str:
    """Replace ``$param_name`` tokens in *text* with values from *params*.

    Only expands params that are explicitly provided.  Unknown ``$``-prefixed
    tokens are left unchanged (backward compatible with ``$goal`` handling).

    Args:
        text: The text containing ``$param`` tokens.
        params: Dict mapping param names to replacement values.

    Returns:
        Text with known ``$param`` tokens replaced.
    """
    # Keys that have a longer dotted sibling actually present in params (e.g.
    # "tool" when "tool.last_line" is also a params key) need "." excluded
    # from the boundary lookahead, to avoid partially corrupting the still-
    # literal longer token. Any other key's "." is ordinary text (a filename
    # extension, a sentence-ending period) and must not block substitution.
    keys_with_dotted_sibling = {
        key
        for key in params
        if any(other != key and other.startswith(key + ".") for other in params)
    }

    for key, value in params.items():
        # Negative lookahead's word-character class (A-Za-z0-9_) ensures
        # token-boundary awareness unconditionally: "$name" only matches when
        # the next character is not a valid key-name character, so
        # "$name_suffix" is never corrupted when only "name" is in params.
        # "." is additionally excluded only when a longer dotted sibling key
        # exists (see keys_with_dotted_sibling above).
        _value = str(value)
        boundary_chars = (
            "A-Za-z0-9_." if key in keys_with_dotted_sibling else "A-Za-z0-9_"
        )
        text = re.sub(
            r"\$" + re.escape(key) + r"(?![" + boundary_chars + r"])",
            lambda m: _value,
            text,
        )
    return text


def expand_goal_variable(text: str, graph_goal: str, context_goal: str | Any) -> str:
    """Replace ``$goal`` in *text* with the goal value.

    Resolution order:
    1. *context_goal* (from ``context.get("graph.goal")``).
    2. *graph_goal* (the graph-level goal attribute).

    If neither is truthy, *text* is returned unchanged.

    This is the **single** location for ``$goal`` expansion (L-17).
    """
    goal_value = context_goal or graph_goal
    if not goal_value:
        return text
    return text.replace("$goal", str(goal_value))


# ---------------------------------------------------------------------------
# M-20: Transform protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Transform(Protocol):
    """Interface for graph transforms.

    Spec Section 9.2: Transform protocol.

    Implementors must provide an ``apply`` method that takes a Graph
    and returns the (possibly modified) Graph.
    """

    def apply(self, graph: Graph) -> Graph: ...


# ---------------------------------------------------------------------------
# Built-in transforms
# ---------------------------------------------------------------------------


def expand_variables(graph: Graph, context: PipelineContext) -> Graph:
    """Replace ``$goal`` and ``$param`` tokens in node prompts.

    Resolution order for the goal value:
    1. ``context.get("graph.goal")`` — set during engine initialization.
    2. ``graph.goal`` — the graph-level goal attribute (fallback).

    Params are resolved from ``context.get("graph.params_values")``,
    which is set by tool-pipeline-run when the caller provides params.

    Args:
        graph: The pipeline graph to transform (modified in place).
        context: The pipeline context with runtime values.

    Returns:
        The same graph, with ``$goal`` and ``$param`` tokens replaced.
    """
    context_goal = context.get("graph.goal") or ""
    graph_goal = graph.goal or ""

    # Resolve params from context (set by tool-pipeline-run)
    params: dict[str, str] = context.get("graph.params_values") or {}

    for node in graph.nodes.values():
        if not node.prompt:
            continue
        if "$goal" in node.prompt:
            node.prompt = expand_goal_variable(node.prompt, graph_goal, context_goal)
        if params and "$" in node.prompt:
            node.prompt = expand_params(node.prompt, params)

    return graph


def _resolve_response_schema_value(
    raw: str,
    source_dir: str,
    node_id: str,
) -> dict[str, Any]:
    """Resolve a ``response_schema`` DOT attribute value to a Python dict.

    Accepts two forms (EXTENSIONS.md §23):

    * **Inline JSON** — trimmed value starts with ``{``.  Parsed with
      ``json.loads``; must be a JSON object (not a list or scalar).
    * **File path** — any other value.  Resolved relative to *source_dir*
      (i.e., the directory of the ``.dot`` file) when not absolute; falls
      back to the current working directory when *source_dir* is empty.
      The file must contain a valid JSON object.

    Raises:
        ValueError: If the value is neither valid inline JSON nor a
            readable file containing a valid JSON object.  Always raised
            with a clear, actionable message — never silently skipped.

    Args:
        raw: The raw string value from the DOT attribute.
        source_dir: Directory of the ``.dot`` source file (may be empty).
        node_id: Node ID for use in error messages.

    Returns:
        The parsed JSON object as a Python dict.
    """
    trimmed = raw.strip()

    # --- Inline JSON ---
    if trimmed.startswith("{"):
        try:
            result = json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Node '{node_id}': response_schema is not valid inline JSON: {exc}. "
                f"Value: {raw!r}"
            ) from exc
        if not isinstance(result, dict):
            raise ValueError(
                f"Node '{node_id}': response_schema inline JSON must be a JSON object "
                f"(dict), got {type(result).__name__}"
            )
        return result

    # --- File path ---
    base = source_dir if source_dir else os.getcwd()
    path = trimmed if os.path.isabs(trimmed) else os.path.join(base, trimmed)
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        raise ValueError(
            f"Node '{node_id}': response_schema file '{path}' could not be read: {exc}. "
            f"Ensure the path is relative to the .dot file directory or is absolute."
        ) from exc
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Node '{node_id}': response_schema file '{path}' "
            f"does not contain valid JSON: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError(
            f"Node '{node_id}': response_schema file '{path}' "
            f"JSON must be a JSON object (dict), got {type(result).__name__}"
        )
    return result


def resolve_response_schemas(graph: Graph) -> Graph:
    """Resolve raw ``response_schema`` string values on nodes to parsed dicts.

    EXTENSIONS.md §23 — Structured Output extension.

    Called from ``apply_transforms()`` before validation and execution.
    Modifies each node in-place: the ``response_schema`` field transitions
    from its raw DOT string to the parsed JSON object.

    If *source_dir* is empty on the graph (e.g., DOT was loaded from an
    inline string), file-path schemas are resolved relative to ``os.getcwd()``.

    Args:
        graph: The pipeline graph to transform (modified in place).

    Returns:
        The same graph.

    Raises:
        ValueError: If any node's ``response_schema`` value is neither
            valid inline JSON nor a readable file containing a valid JSON
            object.  Always loud — never silently skipped.
    """
    source_dir = graph.source_dir or ""
    for node in graph.nodes.values():
        raw = node.response_schema
        if raw is None:
            continue
        if isinstance(raw, dict):
            continue  # already a dict (e.g., programmatically constructed node)
        node.response_schema = _resolve_response_schema_value(
            str(raw), source_dir, node.id
        )
    return graph


def apply_transforms(
    graph: Graph,
    context: PipelineContext,
    *,
    extra_transforms: list[Transform] | None = None,
) -> Graph:
    """Run all built-in transforms on the graph, then any custom transforms.

    Order:
    1. Variable expansion (``$goal`` → goal value).
    2. Response-schema resolution (EXT §23): resolve ``response_schema``
       DOT attr strings to parsed dicts, fail-loud on any parse error.
    3. Stylesheet application (CSS-like model config rules).
    4. Custom transforms (in order provided).

    Args:
        graph: The pipeline graph to transform (modified in place).
        context: The pipeline context with runtime values.
        extra_transforms: Optional list of additional Transform objects
            to run after the built-in transforms (M-20).

    Returns:
        The same graph, fully transformed.
    """
    # 1. Variable expansion
    expand_variables(graph, context)

    # 2. Resolve response_schema attrs (EXTENSIONS.md §23)
    resolve_response_schemas(graph)

    # 3. Stylesheet application
    if graph.model_stylesheet:
        rules = parse_stylesheet(graph.model_stylesheet)
        apply_stylesheet(graph, rules)

    # 4. Custom transforms (M-20)
    if extra_transforms:
        for transform in extra_transforms:
            graph = transform.apply(graph)

    return graph
