"""Pipeline handler — DOT file path resolution and nested pipeline execution.

Resolves dot_file paths by expanding $variable tokens from context,
then resolving absolute or relative paths against a source directory.
PipelineHandler.execute() parses a child DOT file, creates a child
engine, runs it, and captures the outcome.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..engine import PipelineEngine

from ..context import PipelineContext
from ..dot_parser import parse_dot
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)


def _expand_path_variables(path: str, context: PipelineContext) -> str:
    """Replace $variable tokens using context.get().

    Unknown $tokens are left unchanged. Context values are coerced to str.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = context.get(name)
        if value is None:
            return match.group(0)  # leave unknown token unchanged
        return str(value)

    return re.sub(r"\$(\w+)", _replace, path)


def resolve_dot_path(dot_file: str, source_dir: str, context: PipelineContext) -> str:
    """Resolve a dot_file path.

    1. Expand $variable tokens from context values.
    2. If path is absolute (starts with /), return as-is.
    3. Otherwise resolve relative to source_dir.
    4. If source_dir is empty, resolve relative to cwd.
    """
    expanded = _expand_path_variables(dot_file, context)

    if os.path.isabs(expanded):
        return expanded

    if source_dir:
        return os.path.join(source_dir, expanded)

    # Try context.target_dir before falling back to os.getcwd()
    target_dir = context.get("context.target_dir") if context else None
    if target_dir:
        return os.path.join(target_dir, expanded)

    return os.path.join(os.getcwd(), expanded)


class PipelineHandler:
    """Handler for nested pipeline execution via DOT file references.

    Parses a child DOT file, creates a child engine, runs it, and
    captures the outcome. Used when a node's type is "pipeline".
    """

    def __init__(
        self,
        handler_registry_factory: Any = None,
        cancel_event: Any = None,
        hooks: Any = None,
        backend: Any = None,
        interviewer: Any = None,
    ) -> None:
        self._handler_registry_factory = handler_registry_factory
        self._cancel_event = cancel_event
        self._hooks = hooks
        self._backend = backend
        self._interviewer = interviewer
        self._subgraph_runs: dict[str, Any] = {}

    async def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit an event via hooks, if provided."""
        if self._hooks is not None:
            await self._hooks.emit(event_name, data)

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Execute a nested pipeline from a child DOT file.

        Steps:
        1. Get dot_file from node.attrs, FAIL if missing.
        2. Resolve path via resolve_dot_path().
        3. Read the DOT file, FAIL if not found.
        4. Parse DOT source, FAIL if invalid.
        5. Set child_graph.source_dir for nested resolution.
        6. Clone parent context.
        7. Create child logs dir.
        8. Create child HandlerRegistry.
        9. Create child PipelineEngine.
        10. Determine child goal.
        11. Run child engine, FAIL on exception.
        12. Return child outcome.
        """
        # Lazy imports to avoid circular dependencies
        from ..engine import PipelineEngine
        from . import HandlerRegistry

        # (1) Get dot_file from node.attrs
        dot_file = node.attrs.get("dot_file")
        if not dot_file:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason="Missing dot_file attribute on pipeline node",
            )

        # (2) Resolve path
        resolved_path = resolve_dot_path(dot_file, graph.source_dir, context)

        # (3) Read the DOT file
        try:
            with open(resolved_path) as f:
                dot_source = f.read()
        except FileNotFoundError:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Child DOT file not found: {resolved_path}",
            )

        # (4) Parse DOT source
        try:
            child_graph = parse_dot(dot_source)
        except ValueError as exc:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Failed to parse child DOT: {exc}",
            )

        # (5) Set child_graph.source_dir for nested resolution
        child_graph.source_dir = os.path.dirname(resolved_path)

        # (6) Clone parent context
        child_context = context.clone()

        # (6b) Inject context.* attributes from this folder node into child context.
        for attr_key, attr_value in node.attrs.items():
            if attr_key.startswith("context."):
                child_key = attr_key[len("context.") :]
                child_context.set(child_key, str(attr_value))

        # (7) Create child logs dir
        child_logs = os.path.join(logs_root, f"subgraph_{node.id}")
        os.makedirs(child_logs, exist_ok=True)

        # (8) Create child HandlerRegistry (no closure, no rewire — engine passes self via execute(engine=...))
        if self._handler_registry_factory is not None:
            child_registry = self._handler_registry_factory()
        else:
            from .context import HandlerContext

            child_registry = HandlerRegistry(
                HandlerContext(
                    backend=self._backend,
                    hooks=self._hooks,
                    cancel_event=self._cancel_event,
                    interviewer=self._interviewer,
                )
            )

        # (9) Create child PipelineEngine
        child_engine = PipelineEngine(
            graph=child_graph,
            context=child_context,
            handler_registry=child_registry,
            logs_root=child_logs,
            hooks=self._hooks,
            cancel_event=self._cancel_event,
        )

        # (10) Determine child goal
        child_goal = child_graph.goal or context.get("graph.goal")

        # (10b) Emit pipeline:subgraph_start event
        pipeline_id = child_graph.name or ""
        if not pipeline_id:
            logger.debug(
                "Child graph for node '%s' has no name; pipeline_id is empty", node.id
            )
        await self._emit(
            "pipeline:subgraph_start",
            {
                "node_id": node.id,
                "dot_file": dot_file,
                "pipeline_id": pipeline_id,
                "goal": child_goal or "",
            },
        )

        # (11) Run child engine
        subgraph_start_time = time.monotonic()
        try:
            outcome = await child_engine.run(goal=child_goal)
        except Exception as exc:
            logger.exception("Child pipeline failed for node '%s'", node.id)
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Child pipeline exception: {exc}",
            )
        subgraph_elapsed_ms = (time.monotonic() - subgraph_start_time) * 1000

        # (11b) Populate _subgraph_runs with observability data
        self._subgraph_runs[node.id] = {
            "dot_file": dot_file,
            "dot_source": dot_source,
            "pipeline_id": pipeline_id,
            "goal": child_goal or "",
            "status": outcome.status.value,
            "execution_path": list(child_engine.completed_nodes),
            "node_outcomes": {
                nid: {
                    "status": o.status.value,
                    "notes": o.notes,
                    "failure_reason": o.failure_reason,
                }
                for nid, o in child_engine.node_outcomes.items()
            },
            "total_elapsed_ms": subgraph_elapsed_ms,
            "nodes_completed": len(child_engine.completed_nodes),
            "nodes_total": len(child_graph.nodes),
        }

        # (11b2) Merge declared outputs from child context back to parent
        if outcome.is_success:
            outputs_str = node.attrs.get("outputs", "")
            output_keys = [k.strip() for k in outputs_str.split(",") if k.strip()]
            if output_keys:
                child_snapshot = child_context.snapshot()
                for key in output_keys:
                    val = child_snapshot.get(key)
                    if val is not None:
                        context.set(key, str(val))

        # (11c) Emit pipeline:subgraph_complete event
        await self._emit(
            "pipeline:subgraph_complete",
            {
                "node_id": node.id,
                "pipeline_id": pipeline_id,
                "status": outcome.status.value,
                "duration_ms": subgraph_elapsed_ms,
                "nodes_completed": len(child_engine.completed_nodes),
                "nodes_total": len(child_graph.nodes),
            },
        )

        # (12) Return child outcome
        return outcome
