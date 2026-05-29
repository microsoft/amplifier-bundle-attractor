"""Manager loop handler — supervisor pattern over a child subgraph.

The manager loop (shape=house) orchestrates sprint-based iteration by
supervising a child subgraph. Each cycle the manager:

1. **Observes** — runs the child subgraph via a subgraph_runner callback.
2. **Evaluates** — checks a guard condition (manager.stop_condition) to
   decide whether to stop or continue.
3. **Acts** — optionally injects steering context, then waits before the
   next cycle.

The loop terminates when the guard is satisfied, the child succeeds
(default guard), or max_cycles is exhausted.

Node attributes:
    manager.max_cycles      — Maximum observation cycles (default 10).
    manager.poll_interval   — Delay between cycles, e.g. "45s" (default "0s").
    manager.stop_condition  — Condition expression for early exit (default "").
    manager.actions         — Comma-separated: observe, steer, wait (default
                              "observe,wait").

Spec coverage: MGR-001-010, COMP-001-002, Section 4.11.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

from ..conditions import evaluate_condition
from ..context import PipelineContext
from ..dot_parser import parse_dot
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus

if TYPE_CHECKING:
    from ..engine import PipelineEngine

logger = logging.getLogger(__name__)

# Pattern for parsing duration strings like "45s", "2m", "500ms".
_DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m)?\s*$",
    re.IGNORECASE,
)


def _parse_duration(raw: str) -> float:
    """Parse a duration string to seconds.

    Supports: "45s", "2m", "500ms", or plain number (treated as seconds).
    Returns 0.0 on parse failure.
    """
    m = _DURATION_RE.match(raw)
    if not m:
        return 0.0
    value = float(m.group("value"))
    unit = (m.group("unit") or "s").lower()
    if unit == "ms":
        return value / 1000.0
    if unit == "m":
        return value * 60.0
    return value  # seconds


def _build_steering_message(
    prev_cycle: int,
    max_cycles: int,
    outcome: Outcome,
) -> str:
    """Build a structured steering message for the next child cycle.

    Includes previous cycle status, failure details, cycle budget,
    and actionable instruction. Uses a multi-line format so child
    agents can parse and act on the structured context.
    """
    remaining = max_cycles - prev_cycle
    lines: list[str] = [
        f"[Manager Steering — Cycle {prev_cycle} of {max_cycles}]",
        f"Status: {outcome.status.value}",
    ]
    if outcome.failure_reason:
        lines.append(f"Failure reason: {outcome.failure_reason}")
    if outcome.notes:
        lines.append(f"Notes: {outcome.notes}")
    lines.append(f"Cycles remaining: {remaining}")
    lines.append("Adjust your approach based on the failure details above.")
    return "\n".join(lines)


class ManagerLoopHandler:
    """Handler for manager loop nodes (shape=house).

    Runs an observe/evaluate/act cycle over a child subgraph,
    delegating child execution to the provided subgraph_runner.
    """

    def __init__(
        self,
        backend: Any = None,
        hooks: Any = None,
        cancel_event: Any = None,
        handler_registry_factory: Any | None = None,
    ) -> None:
        self._backend = backend
        self._hooks = hooks
        self._cancel_event = cancel_event
        self._handler_registry_factory = handler_registry_factory
        self._subgraph_runs: dict[str, dict[str, Any]] = {}

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Execute a manager loop node.

        Reads configuration from node attributes, then enters the
        observe/evaluate/act cycle until a stop condition is met or
        max_cycles is exhausted.
        """
        # -- Parse configuration ------------------------------------------------
        max_cycles = int(node.attrs.get("manager.max_cycles", 1000))  # spec default
        poll_interval_s = _parse_duration(
            str(node.attrs.get("manager.poll_interval", "0s"))
        )
        stop_condition = str(node.attrs.get("manager.stop_condition", ""))
        actions_raw = str(node.attrs.get("manager.actions", "observe,wait"))
        actions = [a.strip() for a in actions_raw.split(",")]

        # -- Validate prerequisites ---------------------------------------------
        child_edges = graph.outgoing_edges(node.id)

        child_start_id = child_edges[0].to_node if child_edges else ""

        # Resolve child_dotfile: node-level first, then graph-level
        child_dotfile = (
            node.attrs.get("stack.child_dotfile")
            or graph.graph_attrs.get("stack.child_dotfile")
            or ""
        )

        if not child_dotfile:
            # Without child_dotfile, require outgoing edges and engine
            if not child_edges:
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason="Manager loop has no child to supervise",
                )
            if engine is None:
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason="ManagerLoopHandler requires engine to be passed via execute(engine=...)",
                )

        logger.info(
            "Manager '%s': max_cycles=%d, poll=%.1fs, actions=%s, child=%s, child_dotfile=%s",
            node.id,
            max_cycles,
            poll_interval_s,
            actions,
            child_start_id,
            child_dotfile or "(none)",
        )

        # -- Observation loop ---------------------------------------------------
        last_outcome: Outcome | None = None
        for cycle in range(1, max_cycles + 1):
            # 1. OBSERVE — build child context and run child subgraph
            child_context = context.clone()

            # (1b) Inject context.* attributes from this house node into child context.
            for attr_key, attr_value in node.attrs.items():
                if attr_key.startswith("context."):
                    child_key = attr_key[len("context.") :]
                    child_context.set(child_key, str(attr_value))

            if "steer" in actions and last_outcome is not None:
                # M-15: Structured steering with full cycle context
                steering = _build_steering_message(
                    prev_cycle=cycle - 1,
                    max_cycles=max_cycles,
                    outcome=last_outcome,
                )
                child_context.set("manager.steering", steering)

            try:
                if child_dotfile:
                    child_outcome = await self._run_child_dotfile(
                        child_dotfile, child_context, graph, logs_root, node.id, cycle
                    )
                else:
                    assert engine is not None
                    child_outcome = await engine.run_subgraph(
                        child_start_id, context=child_context
                    )
            except Exception as exc:
                logger.warning(
                    "Manager '%s' cycle %d: child raised %s",
                    node.id,
                    cycle,
                    exc,
                )
                child_outcome = Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=str(exc),
                )

            last_outcome = child_outcome

            # Record cycle telemetry in parent context
            context.set(f"manager.cycle_{cycle}.status", child_outcome.status.value)
            context.set("manager.last_child_status", child_outcome.status.value)
            context.set("manager.cycles_completed", cycle)

            # 2. EVALUATE — check stop / guard condition
            if stop_condition:
                if evaluate_condition(stop_condition, child_outcome, context):
                    return Outcome(
                        status=child_outcome.status,
                        notes=f"Manager completed in {cycle} cycle(s) — stop condition satisfied",
                        context_updates={
                            "last_stage": node.id,
                            "manager.cycles": cycle,
                        },
                    )
            else:
                # Default guard: stop on success or partial_success
                if child_outcome.is_success:
                    return Outcome(
                        status=child_outcome.status,
                        notes=f"Manager completed in {cycle} cycle(s)",
                        context_updates={
                            "last_stage": node.id,
                            "manager.cycles": cycle,
                        },
                    )

            # 3. ACT — wait before next cycle
            if "wait" in actions and cycle < max_cycles and poll_interval_s > 0:
                await asyncio.sleep(poll_interval_s)

        # -- Max cycles exhausted -----------------------------------------------
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=f"Manager exhausted {max_cycles} cycle(s)",
            notes=f"Last child status: {last_outcome.status.value if last_outcome else 'none'}",
            context_updates={
                "last_stage": node.id,
                "manager.cycles": max_cycles,
            },
        )

    async def _run_child_dotfile(
        self,
        child_dotfile: str,
        child_context: PipelineContext,
        graph: Graph,
        logs_root: str,
        manager_node_id: str,
        cycle: int,
    ) -> Outcome:
        """Run a child pipeline from an external DOT file.

        Mirrors PipelineHandler-style child engine execution:
        resolve path, read DOT, parse, create child engine, run.
        """
        # Lazy imports to avoid circular dependencies
        from ..engine import PipelineEngine
        from . import HandlerRegistry
        from .pipeline import resolve_dot_path

        # Resolve the DOT file path
        resolved_path = resolve_dot_path(child_dotfile, graph.source_dir, child_context)

        # Read the DOT file
        try:
            with open(resolved_path) as f:
                dot_source = f.read()
        except FileNotFoundError:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Child DOT file not found: {resolved_path}",
            )

        # Parse DOT source
        try:
            child_graph = parse_dot(dot_source)
        except ValueError as exc:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Failed to parse child DOT: {exc}",
            )

        # Set source_dir for nested resolution
        child_graph.source_dir = os.path.dirname(resolved_path)

        # Create child logs directory
        child_logs = os.path.join(
            logs_root, f"subgraph_{manager_node_id}_cycle_{cycle}"
        )
        os.makedirs(child_logs, exist_ok=True)

        # Create child HandlerRegistry and PipelineEngine
        if self._handler_registry_factory is not None:
            child_registry = self._handler_registry_factory()
        else:
            from .context import HandlerContext

            child_registry = HandlerRegistry(
                HandlerContext(
                    backend=self._backend,
                    hooks=self._hooks,
                    cancel_event=self._cancel_event,
                )
            )
        child_engine = PipelineEngine(
            graph=child_graph,
            context=child_context,
            handler_registry=child_registry,
            logs_root=child_logs,
        )

        # Run child engine
        try:
            t0 = time.monotonic()
            outcome = await child_engine.run()
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            # No subgraph_runs entry on exception — no engine state to capture
            logger.exception(
                "Manager '%s' cycle %d: child dotfile pipeline failed",
                manager_node_id,
                cycle,
            )
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=f"Child pipeline exception: {exc}",
            )

        # Cycle-indexed observability
        node_outcomes_summary: dict[str, dict[str, str | None]] = {}
        for nid, node_out in child_engine.node_outcomes.items():
            node_outcomes_summary[nid] = {
                "status": node_out.status.value,
                "notes": node_out.notes,
                "failure_reason": node_out.failure_reason,
            }

        self._subgraph_runs[f"{manager_node_id}_cycle_{cycle}"] = {
            "dot_file": resolved_path,
            "pipeline_id": child_graph.name,
            "goal": child_graph.goal or "",
            "status": outcome.status.value,
            "execution_path": list(child_engine.completed_nodes),
            "node_outcomes": node_outcomes_summary,
            "total_elapsed_ms": elapsed_ms,
            "nodes_completed": len(child_engine.completed_nodes),
            "nodes_total": len(child_graph.nodes),
        }

        return outcome
