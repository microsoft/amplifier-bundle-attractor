"""Graph data model for Attractor pipelines.

Defines Node, Edge, and Graph dataclasses that represent a parsed DOT
digraph. These are the core data structures used throughout the pipeline
engine.

Spec coverage: DOT-001..017, NATTR-001..017, EDGE-001..006
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def resolve_bool_attr(value: Any, attr_name: str) -> bool:
    """Interpret a DOT boolean attribute value, tolerant of parser coercion.

    The DOT parser (``dot_parser.py::_parse_value``) coerces only the exact
    *unquoted* tokens ``true``/``false`` to a real Python ``bool``; a quoted
    ``"true"`` stays the string ``"true"``. Every call site that reads a
    boolean-ish attribute (``continue_on_fail``, ``parse_json``, ``goal_gate``,
    ``auto_status``, ``allow_partial``, ``loop_restart``, ...) must treat both
    forms identically. This function is the single, shared place that does
    that -- callers should never hand-roll ``== "true"`` or
    ``in (True, "true")`` again.

    Args:
        value: The raw attribute value as stored on ``Node``/``Edge``/
            ``Graph`` (typically ``bool | str | None``).
        attr_name: Name of the attribute, used only to identify the
            offending attribute in the warning below.

    Returns:
        ``True`` for ``True`` or a case-insensitive ``"true"`` string.
        ``False`` for ``False``, ``None``, a case-insensitive ``"false"``
        string, or any other value that can't be confidently interpreted.

    Note:
        An unrecognized value (e.g. ``"maybe"``, ``"1"``, ``"yes"``) is
        treated as falsy -- but *loudly*: a warning is logged naming the
        attribute and the offending value, so a typo'd attribute value
        doesn't silently disable a feature the way the original bug did.
        This is deliberately a warning, not a raised exception: the DOT
        parser's output is valid (any string is a legal attribute value),
        so this is a downstream interpretation concern, not a parse error.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    logger.warning(
        "Attribute %r has an unrecognized boolean value %r; treating as false. "
        "Expected true/false (bare or quoted).",
        attr_name,
        value,
    )
    return False


# Node attributes that are promoted to first-class fields (M-10).
_NODE_PROMOTED_ATTRS: frozenset[str] = frozenset(
    {
        "max_retries",
        "goal_gate",
        "retry_target",
        "fallback_retry_target",
        "fidelity",
        "thread_id",
        "timeout",
        "llm_model",
        "llm_provider",
        "reasoning_effort",
        "auto_status",
        "allow_partial",
        # Extension: structured output (EXTENSIONS.md §23).
        # Raw string from DOT; resolved to a dict by apply_transforms().
        "response_schema",
    }
)


class _NodeAttrsProxy(dict):
    """Dict subclass that proxies promoted keys to Node first-class fields.

    Ensures backward compatibility: ``node.attrs.get("goal_gate")`` returns
    the value of ``node.goal_gate`` for promoted attributes.
    """

    def __init__(self, node: Node, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._node = node

    def get(self, key: str, default: Any = None) -> Any:
        if key in _NODE_PROMOTED_ATTRS:
            val = getattr(self._node, key, None)
            return val if val is not None else default
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in _NODE_PROMOTED_ATTRS:
            val = getattr(self._node, key, None)
            if val is not None:
                return val
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in _NODE_PROMOTED_ATTRS:
            object.__setattr__(self._node, key, value)
        else:
            super().__setitem__(key, value)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and key in _NODE_PROMOTED_ATTRS:
            return getattr(self._node, key, None) is not None
        return super().__contains__(key)


# Edge attributes that are promoted to first-class fields (L-5).
_EDGE_PROMOTED_ATTRS: frozenset[str] = frozenset(
    {
        "fidelity",
        "thread_id",
        "loop_restart",
    }
)


class _EdgeAttrsProxy(dict):
    """Dict subclass that proxies promoted keys to Edge first-class fields."""

    def __init__(self, edge: Edge, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._edge = edge

    def get(self, key: str, default: Any = None) -> Any:
        if key in _EDGE_PROMOTED_ATTRS:
            val = getattr(self._edge, key, None)
            return val if val is not None else default
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in _EDGE_PROMOTED_ATTRS:
            val = getattr(self._edge, key, None)
            if val is not None:
                return val
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in _EDGE_PROMOTED_ATTRS:
            object.__setattr__(self._edge, key, value)
        else:
            super().__setitem__(key, value)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and key in _EDGE_PROMOTED_ATTRS:
            return getattr(self._edge, key, None) is not None
        return super().__contains__(key)


# Graph attributes that are promoted to first-class fields (L-6).
_GRAPH_PROMOTED_ATTRS: frozenset[str] = frozenset(
    {
        "retry_target",
        "fallback_retry_target",
        "default_fidelity",
        "label",
        "max_pipeline_duration",
    }
)


class _GraphAttrsProxy(dict):
    """Dict subclass that proxies promoted keys to Graph first-class fields."""

    def __init__(self, graph: Graph, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._graph = graph

    def get(self, key: str, default: Any = None) -> Any:
        if key in _GRAPH_PROMOTED_ATTRS:
            val = getattr(self._graph, key, None)
            return val if val is not None else default
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in _GRAPH_PROMOTED_ATTRS:
            val = getattr(self._graph, key, None)
            if val is not None:
                return val
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in _GRAPH_PROMOTED_ATTRS:
            object.__setattr__(self._graph, key, value)
        else:
            super().__setitem__(key, value)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and key in _GRAPH_PROMOTED_ATTRS:
            return getattr(self._graph, key, None) is not None
        return super().__contains__(key)


@dataclass
class Node:
    """A node in the pipeline graph.

    Attributes map to spec Section 2.6 (Node Attributes).
    The shape determines the default handler type via the
    shape-to-handler-type mapping (spec Section 2.8).

    First-class fields (M-10): max_retries, goal_gate, retry_target,
    fallback_retry_target, fidelity, thread_id, timeout, llm_model,
    llm_provider, reasoning_effort, auto_status, allow_partial.
    These are also accessible via ``attrs`` dict for backward compatibility.

    Extension field (EXTENSIONS.md §23): response_schema.
    Stores the raw DOT attribute string initially (either inline JSON or a
    file path); resolved to a ``dict`` by ``apply_transforms()`` before
    validation and execution.
    """

    id: str
    label: str = ""
    shape: str = "box"
    type: str = ""
    prompt: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    handler_type: str = ""  # Resolved from type or shape

    # Promoted node attributes (M-10) -- all optional, default None
    max_retries: int | None = None
    goal_gate: bool | None = None
    retry_target: str | None = None
    fallback_retry_target: str | None = None
    fidelity: str | None = None
    thread_id: str | None = None
    timeout: int | None = None
    llm_model: str | None = None
    llm_provider: str | None = None
    reasoning_effort: str | None = None
    auto_status: bool | None = None
    allow_partial: bool | None = None

    # Extension: structured-output schema (EXTENSIONS.md §23).
    # Initially a raw string from the DOT attribute; resolve_response_schemas()
    # in transforms.py replaces it with the parsed dict before execution.
    # Type annotation is dict for post-transform consumers; pre-transform the
    # field may hold a string — Python does not enforce annotations at runtime.
    response_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.id
        # Promote any promoted keys from the raw attrs dict into fields,
        # then wrap attrs with the proxy for backward compatibility.
        raw = self.attrs
        for key in _NODE_PROMOTED_ATTRS:
            if key in raw:
                val = raw.pop(key)
                if getattr(self, key, None) is None:
                    object.__setattr__(self, key, val)
        # Replace plain dict with proxy
        proxy = _NodeAttrsProxy(self, raw)
        object.__setattr__(self, "attrs", proxy)

    def is_start_node(self) -> bool:
        """Check if this node is a start node.

        Resolution order (spec Section 3.2, NLSpec line 344):
          1. shape=Mdiamond
          2. type="start" attribute
          3. id matches "start" (case-insensitive)
        """
        if self.shape == "Mdiamond":
            return True
        if self.type == "start":
            return True
        if self.id.lower() == "start":
            return True
        return False

    def is_exit_node(self) -> bool:
        """Check if this node is an exit/terminal node.

        Resolution order:
          1. shape=Msquare
          2. type="exit" attribute
          3. id matches "exit" or "end" (case-insensitive)
        """
        if self.shape == "Msquare":
            return True
        if self.type == "exit":
            return True
        if self.id.lower() in ("exit", "end"):
            return True
        return False


@dataclass
class Edge:
    """A directed edge in the pipeline graph.

    Attributes map to spec Section 2.7 (Edge Attributes).

    First-class fields (L-5): fidelity, thread_id, loop_restart.
    These are also accessible via ``attrs`` dict for backward compatibility.
    """

    from_node: str
    to_node: str
    label: str = ""
    condition: str = ""
    weight: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)

    # Promoted edge attributes (L-5) -- all optional, default None
    fidelity: str | None = None
    thread_id: str | None = None
    loop_restart: bool | None = None

    def __post_init__(self) -> None:
        raw = self.attrs
        for key in _EDGE_PROMOTED_ATTRS:
            if key in raw:
                val = raw.pop(key)
                if getattr(self, key, None) is None:
                    object.__setattr__(self, key, val)
        proxy = _EdgeAttrsProxy(self, raw)
        object.__setattr__(self, "attrs", proxy)


@dataclass
class Graph:
    """A parsed pipeline graph.

    Contains nodes, edges, and graph-level attributes from
    spec Section 2.5 (Graph-Level Attributes).

    First-class fields (L-6): retry_target, fallback_retry_target,
    default_fidelity, label, max_pipeline_duration.
    These are also accessible via ``graph_attrs`` dict for backward compat.
    """

    name: str
    nodes: dict[str, Node]
    edges: list[Edge]
    goal: str = ""
    dot_source: str = ""
    default_max_retry: int = 0
    model_stylesheet: str = ""
    source_dir: str = ""  # Directory of the DOT file that produced this graph
    graph_attrs: dict[str, str] = field(default_factory=dict)

    # Promoted graph attributes (L-6) -- all optional, default None
    retry_target: str | None = None
    fallback_retry_target: str | None = None
    default_fidelity: str | None = None
    label: str | None = None
    max_pipeline_duration: int | None = None  # milliseconds, from DOT graph attribute

    def __post_init__(self) -> None:
        raw = self.graph_attrs
        for key in _GRAPH_PROMOTED_ATTRS:
            if key in raw:
                val = raw.pop(key)
                if getattr(self, key, None) is None:
                    object.__setattr__(self, key, val)
        proxy = _GraphAttrsProxy(self, raw)
        object.__setattr__(self, "graph_attrs", proxy)

    def outgoing_edges(self, node_id: str) -> list[Edge]:
        """Return all edges originating from the given node."""
        return [e for e in self.edges if e.from_node == node_id]

    def incoming_edges(self, node_id: str) -> list[Edge]:
        """Return all edges targeting the given node."""
        return [e for e in self.edges if e.to_node == node_id]
