"""DOT digraph parser for Attractor pipelines.

Parses the strict DOT subset defined in spec Section 2 into a Graph model.
Handles: digraph keyword, node/edge declarations, chained edges, attribute
blocks, value types, node/edge defaults, subgraphs, and comments.

Spec coverage: DOT-001–017
"""

from __future__ import annotations

import re
import warnings
from typing import Any

from .graph import Edge, Graph, Node

# Duration unit multipliers (to milliseconds)
_DURATION_UNITS: dict[str, int] = {
    "ms": 1,
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
}

# Graph-level attributes that get promoted to Graph fields
_GRAPH_FIELD_ATTRS = {
    "goal",
    "default_max_retry",
    "model_stylesheet",
    "max_pipeline_duration",
}

# Node attributes that get promoted to Node fields
_NODE_FIELD_MAP = {"label", "shape", "type", "prompt"}

# Edge attributes that get promoted to Edge fields
_EDGE_FIELD_MAP = {"label", "condition", "weight"}

# Known graph-level attribute names (not node IDs)
_KNOWN_GRAPH_ATTRS = {
    "goal",
    "label",
    "model_stylesheet",
    "default_max_retry",
    "max_pipeline_duration",
    "retry_target",
    "fallback_retry_target",
    "default_fidelity",
    "rankdir",
    "bgcolor",
    "fontname",
    "fontsize",
    "compound",
    "concentrate",
    "splines",
    "overlap",
    "nodesep",
    "ranksep",
    "size",
    "ratio",
}


def parse_dot(source: str) -> Graph:
    """Parse a DOT digraph string into a Graph model.

    Args:
        source: DOT language string (digraph only).

    Returns:
        Parsed Graph with nodes, edges, and attributes.

    Raises:
        ValueError: If the source is not a valid digraph or uses
            unsupported features (undirected graphs, strict modifier).
    """
    # Strip comments
    cleaned = _strip_comments(source)

    # Reject unsupported constructs
    stripped = cleaned.strip()
    if re.match(r"strict\s+", stripped, re.IGNORECASE):
        raise ValueError("strict modifier is not supported; use plain 'digraph'")
    if re.match(r"graph\s+", stripped, re.IGNORECASE) and not re.match(
        r"digraph\s+", stripped, re.IGNORECASE
    ):
        raise ValueError("Only digraph is supported; undirected 'graph' is rejected")

    # Check for undirected edges (-- outside of quotes)
    if _has_undirected_edges(cleaned):
        raise ValueError(
            "Undirected edges (--) are not supported; use directed edges (->)"
        )

    # Reject multiple digraphs in one source (L-2)
    digraph_matches = re.findall(r"\bdigraph\b", cleaned, re.IGNORECASE)
    if len(digraph_matches) > 1:
        raise ValueError(
            f"Multiple digraph definitions found ({len(digraph_matches)}); "
            f"only a single digraph per source is supported"
        )

    # Extract digraph name and body
    m = re.match(r"\s*digraph\s+(\w+)\s*\{(.*)\}\s*$", cleaned, re.DOTALL)
    if not m:
        # Try unnamed digraph
        m = re.match(r"\s*digraph\s*\{(.*)\}\s*$", cleaned, re.DOTALL)
        if not m:
            raise ValueError(
                "Could not parse DOT source; expected 'digraph name { ... }'"
            )
        graph_name = "unnamed"
        body = m.group(1)
    else:
        graph_name = m.group(1)
        body = m.group(2)

    # Parse the body
    ctx = _ParseContext()
    _parse_body(body, ctx)

    # Build Graph
    graph = Graph(
        name=graph_name,
        nodes=ctx.nodes,
        edges=ctx.edges,
        goal=ctx.graph_fields.get("goal", ""),
        dot_source=source,
        default_max_retry=ctx.graph_fields.get(
            "default_max_retry", 0
        ),  # spec default: 0
        model_stylesheet=ctx.graph_fields.get("model_stylesheet", ""),
        max_pipeline_duration=ctx.graph_fields.get("max_pipeline_duration"),
        graph_attrs=ctx.graph_attrs,
    )
    return graph


class _ParseContext:
    """Accumulates parse results."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.graph_attrs: dict[str, str] = {}
        self.graph_fields: dict[str, Any] = {}
        self.node_defaults: dict[str, Any] = {}
        self.edge_defaults: dict[str, Any] = {}

    def ensure_node(self, node_id: str) -> Node:
        """Get or create a node, applying defaults."""
        if node_id not in self.nodes:
            attrs = dict(self.node_defaults)
            shape = attrs.pop("shape", "box")
            label = attrs.pop("label", "")
            node_type = attrs.pop("type", "")
            prompt = attrs.pop("prompt", "")
            self.nodes[node_id] = Node(
                id=node_id,
                label=label,
                shape=shape,
                type=node_type,
                prompt=prompt,
                attrs=attrs,
            )
        return self.nodes[node_id]


def _parse_body(body: str, ctx: _ParseContext) -> None:
    """Parse the body of a digraph block."""
    tokens = _tokenize(body)
    pos = 0
    while pos < len(tokens):
        pos = _skip_semis(tokens, pos)
        if pos >= len(tokens):
            break

        token = tokens[pos]

        # Subgraph
        if token == "subgraph":
            pos = _parse_subgraph(tokens, pos, ctx)
            continue

        # graph/node/edge default blocks
        if token == "graph" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            for key, val in attrs.items():
                _set_graph_attr(ctx, key, val)
            pos = end + 1
            continue

        if token == "node" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            ctx.node_defaults.update(attrs)
            pos = end + 1
            continue

        if token == "edge" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            ctx.edge_defaults.update(attrs)
            pos = end + 1
            continue

        # Look ahead to determine statement type
        if _is_identifier(token):
            # Check for edge: id -> id ...
            if pos + 1 < len(tokens) and tokens[pos + 1] == "->":
                pos = _parse_edge_stmt(tokens, pos, ctx)
                continue

            # Check for graph-level attr: id = value
            if pos + 1 < len(tokens) and tokens[pos + 1] == "=":
                key = token
                val = _parse_value(tokens[pos + 2])
                _set_graph_attr(ctx, key, val)
                pos = pos + 3
                continue

            # Node declaration: id or id [attrs]
            pos = _parse_node_stmt(tokens, pos, ctx)
            continue

        pos += 1


def _parse_subgraph(tokens: list[str], pos: int, ctx: _ParseContext) -> int:
    """Parse a subgraph block, inheriting and scoping node defaults."""
    pos += 1  # skip 'subgraph'

    # Optional subgraph name
    if pos < len(tokens) and _is_identifier(tokens[pos]):
        pos += 1  # skip name

    if pos < len(tokens) and tokens[pos] == "{":
        # Save parent defaults and create scoped copy
        saved_defaults = dict(ctx.node_defaults)
        nodes_before = set(ctx.nodes.keys())
        end = _find_matching_brace(tokens, pos)
        sub_body_tokens = tokens[pos + 1 : end]
        # Parse sub-body using same context but with scoped defaults
        _parse_token_list(sub_body_tokens, ctx)
        # Derive class from subgraph label and apply to new nodes (L-3)
        subgraph_label = ctx.graph_attrs.pop("label", None)
        if subgraph_label:
            derived_class = _derive_class(subgraph_label)
            if derived_class:
                new_nodes = set(ctx.nodes.keys()) - nodes_before
                for nid in new_nodes:
                    node = ctx.nodes[nid]
                    if not node.attrs.get("class"):
                        node.attrs["class"] = derived_class
        # Restore parent defaults
        ctx.node_defaults = saved_defaults
        return end + 1

    return pos


def _derive_class(label: str) -> str:
    """Derive a CSS-like class name from a subgraph label (L-3).

    Lowercase, replace spaces with hyphens, strip non-alphanumeric chars.
    """
    result = label.lower().replace(" ", "-")
    result = re.sub(r"[^a-z0-9-]", "", result)
    # Strip leading/trailing hyphens
    return result.strip("-")


def _parse_token_list(tokens: list[str], ctx: _ParseContext) -> None:
    """Parse a list of tokens (used for subgraph bodies)."""
    pos = 0
    while pos < len(tokens):
        pos = _skip_semis(tokens, pos)
        if pos >= len(tokens):
            break

        token = tokens[pos]

        if token == "subgraph":
            pos = _parse_subgraph(tokens, pos, ctx)
            continue

        if token == "graph" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            for key, val in attrs.items():
                _set_graph_attr(ctx, key, val)
            pos = end + 1
            continue

        if token == "node" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            ctx.node_defaults.update(attrs)
            pos = end + 1
            continue

        if token == "edge" and pos + 1 < len(tokens) and tokens[pos + 1] == "[":
            attrs = _parse_attr_block(tokens, pos + 1)
            end = _find_closing_bracket(tokens, pos + 1)
            ctx.edge_defaults.update(attrs)
            pos = end + 1
            continue

        if _is_identifier(token):
            if pos + 1 < len(tokens) and tokens[pos + 1] == "->":
                pos = _parse_edge_stmt(tokens, pos, ctx)
                continue

            if pos + 1 < len(tokens) and tokens[pos + 1] == "=":
                key = token
                val = _parse_value(tokens[pos + 2])
                _set_graph_attr(ctx, key, val)
                pos = pos + 3
                continue

            pos = _parse_node_stmt(tokens, pos, ctx)
            continue

        pos += 1


def _parse_node_stmt(tokens: list[str], pos: int, ctx: _ParseContext) -> int:
    """Parse a node declaration: id or id [attrs]."""
    node_id = tokens[pos]
    pos += 1

    attrs: dict[str, Any] = {}
    if pos < len(tokens) and tokens[pos] == "[":
        attrs = _parse_attr_block(tokens, pos)
        pos = _find_closing_bracket(tokens, pos) + 1

    _apply_node(ctx, node_id, attrs)
    return pos


def _parse_edge_stmt(tokens: list[str], pos: int, ctx: _ParseContext) -> int:
    """Parse an edge statement: id -> id (-> id)* [attrs]."""
    chain: list[str] = [tokens[pos]]
    pos += 1

    while pos + 1 < len(tokens) and tokens[pos] == "->":
        pos += 1  # skip ->
        chain.append(tokens[pos])
        pos += 1

    # Optional attr block
    attrs: dict[str, Any] = {}
    if pos < len(tokens) and tokens[pos] == "[":
        attrs = _parse_attr_block(tokens, pos)
        pos = _find_closing_bracket(tokens, pos) + 1

    # Expand chain into edges, applying defaults + explicit attrs
    for i in range(len(chain) - 1):
        merged = dict(ctx.edge_defaults)
        merged.update(attrs)
        from_id = chain[i]
        to_id = chain[i + 1]

        # Ensure nodes exist
        ctx.ensure_node(from_id)
        ctx.ensure_node(to_id)

        # Extract edge fields from merged attrs
        label = str(merged.pop("label", ""))
        condition = str(merged.pop("condition", ""))
        weight = merged.pop("weight", 0)
        if isinstance(weight, str):
            weight = int(weight)

        ctx.edges.append(
            Edge(
                from_node=from_id,
                to_node=to_id,
                label=label,
                condition=condition,
                weight=weight,
                attrs=merged,
            )
        )

    return pos


def _apply_node(
    ctx: _ParseContext, node_id: str, explicit_attrs: dict[str, Any]
) -> None:
    """Create or update a node with defaults and explicit attributes."""
    merged = dict(ctx.node_defaults)
    merged.update(explicit_attrs)

    # Extract node fields
    shape = str(merged.pop("shape", "box"))
    label = str(merged.pop("label", ""))
    node_type = str(merged.pop("type", ""))
    prompt = str(merged.pop("prompt", ""))

    if node_id in ctx.nodes:
        node = ctx.nodes[node_id]
        # Update with explicit attrs (merge, don't replace)
        if shape != "box" or "shape" in explicit_attrs:
            node.shape = shape
        if label:
            node.label = label
        if node_type:
            node.type = node_type
        if prompt:
            node.prompt = prompt
        # Merge remaining attrs
        for k, v in merged.items():
            node.attrs[k] = v
        # Re-apply defaults for attrs not already set
        for k, v in ctx.node_defaults.items():
            if k not in _NODE_FIELD_MAP and k not in node.attrs:
                node.attrs[k] = v
    else:
        ctx.nodes[node_id] = Node(
            id=node_id,
            label=label,
            shape=shape,
            type=node_type,
            prompt=prompt,
            attrs=merged,
        )


def _set_graph_attr(ctx: _ParseContext, key: str, val: Any) -> None:
    """Set a graph-level attribute, promoting known fields."""
    if key == "goal":
        ctx.graph_fields["goal"] = str(val)
    elif key in ("default_max_retry", "default_max_retries"):
        ctx.graph_fields["default_max_retry"] = (
            int(val) if not isinstance(val, int) else val
        )
    elif key == "model_stylesheet":
        ctx.graph_fields["model_stylesheet"] = str(val)
    elif key == "max_pipeline_duration":
        ctx.graph_fields["max_pipeline_duration"] = (
            int(val) if not isinstance(val, int) else val
        )
    else:
        ctx.graph_attrs[key] = str(val) if not isinstance(val, str) else val


# --- Tokenizer ---

# Regex for tokenizing DOT body
_TOKEN_RE = re.compile(
    r"""
    (?P<string>"(?:[^"\\]|\\.)*")     # Quoted string
    | (?P<arrow>->)                    # Directed edge
    | (?P<punct>[{}\[\]=,;])           # Punctuation
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)  # Identifier (possibly qualified)
    | (?P<number>-?(?:[0-9]+\.?[0-9]*|\.[0-9]+))  # Number (int, float, or .5-style)
    | (?P<ws>\s+)                      # Whitespace (skip)
    """,
    re.VERBOSE,
)


def _tokenize(body: str) -> list[str]:
    """Tokenize the body of a digraph into a flat list of tokens."""
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(body):
        if m.group("ws"):
            continue
        if m.group("string"):
            tokens.append(m.group("string"))
        elif m.group("arrow"):
            tokens.append("->")
        elif m.group("punct"):
            tokens.append(m.group("punct"))
        elif m.group("ident"):
            tokens.append(m.group("ident"))
        elif m.group("number"):
            tokens.append(m.group("number"))
    return tokens


# --- Attribute block parser ---


def _parse_attr_block(tokens: list[str], pos: int) -> dict[str, Any]:
    """Parse [key=val, key=val, ...] starting at the '[' token.

    Warns when consecutive key=value pairs are separated by whitespace
    only (no comma or semicolon), since the spec requires commas.
    """
    assert tokens[pos] == "["
    end = _find_closing_bracket(tokens, pos)
    attrs: dict[str, Any] = {}
    found_missing_comma = False
    i = pos + 1
    prev_was_value = False
    while i < end:
        if tokens[i] in (",", ";"):
            prev_was_value = False
            i += 1
            continue
        # key = value
        if i + 2 <= end and tokens[i + 1] == "=":
            if prev_was_value:
                found_missing_comma = True
            key = tokens[i]
            raw_val = tokens[i + 2]
            attrs[key] = _parse_value(raw_val)
            prev_was_value = True
            i += 3
        else:
            prev_was_value = False
            i += 1
    if found_missing_comma:
        warnings.warn(
            "DOT attribute block has space-separated attributes without commas; "
            "the spec requires comma-separated key=value pairs",
            stacklevel=2,
        )
    return attrs


def _find_closing_bracket(tokens: list[str], pos: int) -> int:
    """Find the matching ']' for a '[' at pos."""
    depth = 0
    for i in range(pos, len(tokens)):
        if tokens[i] == "[":
            depth += 1
        elif tokens[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return len(tokens) - 1


def _find_matching_brace(tokens: list[str], pos: int) -> int:
    """Find the matching '}' for a '{' at pos."""
    depth = 0
    for i in range(pos, len(tokens)):
        if tokens[i] == "{":
            depth += 1
        elif tokens[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(tokens) - 1


# --- Value parsing ---


def _parse_value(raw: str) -> Any:
    """Parse a DOT attribute value into a typed Python value.

    Handles: strings, integers, floats, booleans, durations.
    """
    # Quoted string
    if raw.startswith('"') and raw.endswith('"'):
        inner = raw[1:-1]
        # Process escape sequences.
        #
        # ORDER MATTERS. ``\\`` -> ``\`` MUST run first; otherwise an input of
        # ``\\n`` (two literal backslashes followed by n, used by reality_check
        # and semport pipelines as a logical line separator) is mis-processed:
        # ``\n`` -> LF runs first, leaving ``\<LF>`` (backslash + real newline,
        # which dash interprets as a line continuation) and the trailing
        # ``\\`` -> ``\`` step finds nothing to replace.
        #
        # Correct order collapses the double backslash first, so the residual
        # ``\n`` is then interpreted as a real newline -- which is what
        # pipeline authors expect when they write ``\\n`` between script lines.
        inner = inner.replace("\\\\", "\\")
        inner = inner.replace('\\"', '"')
        inner = inner.replace("\\n", "\n")
        inner = inner.replace("\\t", "\t")
        # Check if it's a duration string
        dur = _try_parse_duration(inner)
        if dur is not None:
            return dur
        return inner

    # Unquoted boolean
    if raw == "true":
        return True
    if raw == "false":
        return False

    # Unquoted duration (e.g., 30s without quotes)
    dur = _try_parse_duration(raw)
    if dur is not None:
        return dur

    # Number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass

    # Bare identifier (return as string)
    return raw


def _try_parse_duration(s: str) -> int | None:
    """Try to parse a duration string into milliseconds.

    Returns None if not a valid duration.
    """
    m = re.match(r"^(-?\d+)(ms|s|m|h|d)$", s)
    if m:
        value = int(m.group(1))
        unit = m.group(2)
        return value * _DURATION_UNITS[unit]
    return None


# --- Comment stripping ---


def _strip_comments(source: str) -> str:
    """Remove // line comments and /* block comments */ from source.

    Preserves content inside quoted strings.
    """
    result: list[str] = []
    i = 0
    length = len(source)
    while i < length:
        # Inside a quoted string — copy verbatim
        if source[i] == '"':
            j = i + 1
            while j < length:
                if source[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if source[j] == '"':
                    j += 1
                    break
                j += 1
            result.append(source[i:j])
            i = j
        # Line comment
        elif source[i : i + 2] == "//":
            j = source.find("\n", i)
            if j == -1:
                break
            i = j  # keep the newline
        # Block comment
        elif source[i : i + 2] == "/*":
            j = source.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
        else:
            result.append(source[i])
            i += 1
    return "".join(result)


# --- Helpers ---


def _is_identifier(token: str) -> bool:
    """Check if a token looks like a DOT identifier."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", token))


def _has_undirected_edges(source: str) -> bool:
    """Check if the source contains undirected edges (--) outside quotes."""
    in_string = False
    i = 0
    while i < len(source):
        if in_string:
            # Inside a quoted string: skip escape sequences (e.g. \", \\)
            # so that \" is not mistaken for a closing quote.
            if source[i] == "\\" and i + 1 < len(source):
                i += 2
                continue
            if source[i] == '"':
                in_string = False
        else:
            if source[i] == '"':
                in_string = True
            elif source[i : i + 2] == "--":
                # Check it's not part of ->
                if i > 0 and source[i - 1] == "<":
                    i += 2
                    continue
                return True
        i += 1
    return False


def _skip_semis(tokens: list[str], pos: int) -> int:
    """Skip semicolons."""
    while pos < len(tokens) and tokens[pos] == ";":
        pos += 1
    return pos
