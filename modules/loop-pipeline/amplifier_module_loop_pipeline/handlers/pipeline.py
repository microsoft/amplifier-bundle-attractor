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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DotPathCandidate:
    """One tier of the ``dot_file=`` precedence chain (EXTENSIONS.md §10).

    ``resolve_dot_path()`` is a *precedence chain*, not a search path: the
    first tier that yields a non-empty base wins and the rest are never
    consulted.  Recording every tier — the winner, the tiers that were
    skipped because an earlier one won, and the tiers that had nothing to
    offer at all — is what makes "resolved against the wrong base directory"
    readable in a diagnostic instead of something an author has to infer.
    """

    #: Tier name as EXTENSIONS.md §10 names it (``graph.source_dir``, …).
    tier: str
    #: The path this tier would produce, or None when the tier is not
    #: applicable at all (an absolute ``dot_file=``; an empty base dir).
    path: str | None
    #: True for the tier ``resolve_dot_path()`` actually returned.
    chosen: bool
    #: Why this tier produced no path (only set when ``path`` is None).
    unavailable_reason: str = ""

    @property
    def exists(self) -> bool:
        """True when this tier's path names a file that exists right now."""
        return bool(self.path) and os.path.exists(self.path)


def resolve_dot_path_candidates(
    dot_file: str, source_dir: str, context: PipelineContext
) -> list[DotPathCandidate]:
    """Enumerate every tier of the ``dot_file=`` precedence chain.

    Returns the four EXTENSIONS.md §10 tiers in precedence order, with
    exactly one marked ``chosen`` — the same path ``resolve_dot_path()``
    returns.  This is the diagnostic sibling of ``resolve_dot_path()``: it
    exists so a failed resolution can name *where it looked*, not only
    *what it picked*.
    """
    expanded = _expand_path_variables(dot_file, context)
    is_abs = os.path.isabs(expanded)
    target_dir = context.get("context.target_dir") if context else None

    # Build each tier's would-be path (None when the tier cannot apply).
    # Note: for an absolute `expanded`, tiers 2-4 are genuinely inapplicable —
    # os.path.join(base, "/abs") returns "/abs" regardless of base, so
    # reporting them as candidates would be a lie dressed as a diagnostic.
    _ABS = "dot_file= is absolute; the precedence chain stops at tier 1"
    raw: list[tuple[str, str | None, str]] = []

    raw.append(
        (
            "absolute path",
            expanded if is_abs else None,
            "dot_file= is relative after $variable expansion",
        )
    )
    raw.append(
        (
            "graph.source_dir",
            os.path.join(source_dir, expanded) if (source_dir and not is_abs) else None,
            _ABS
            if is_abs
            else "graph.source_dir is empty (an inline DOT source has no backing file)",
        )
    )
    raw.append(
        (
            "context.target_dir",
            os.path.join(str(target_dir), expanded)
            if (target_dir and not is_abs)
            else None,
            _ABS
            if is_abs
            else "context.target_dir is unset (no --cwd; the mounted orchestrator skips this tier)",
        )
    )
    raw.append(
        (
            "os.getcwd()",
            None if is_abs else os.path.join(os.getcwd(), expanded),
            _ABS,
        )
    )

    chosen_index = next((i for i, (_, path, _) in enumerate(raw) if path), -1)
    return [
        DotPathCandidate(
            tier=tier,
            path=path,
            chosen=(i == chosen_index),
            unavailable_reason="" if path else reason,
        )
        for i, (tier, path, reason) in enumerate(raw)
    ]


def resolve_dot_path(dot_file: str, source_dir: str, context: PipelineContext) -> str:
    """Resolve a dot_file path.

    1. Expand $variable tokens from context values.
    2. If path is absolute (starts with /), return as-is.
    3. Otherwise resolve relative to source_dir.
    4. If source_dir is empty, resolve relative to context.target_dir, then cwd.

    Resolution stays LAZY by design (EXTENSIONS.md §10): the first non-empty
    candidate wins with NO existence check here, which is what makes
    write-then-run composition possible (a node writes the child .dot mid-run
    and a later shape=folder node executes it).  Existence is asserted at node
    ENTRY instead — see ``ChildDotResolutionError``.
    """
    for candidate in resolve_dot_path_candidates(dot_file, source_dir, context):
        if candidate.chosen and candidate.path:
            return candidate.path
    # Unreachable: the os.getcwd() tier always yields a path for a relative
    # dot_file, and the absolute tier always yields one for an absolute value.
    return _expand_path_variables(dot_file, context)


class ChildDotResolutionError(Exception):
    """A ``shape=folder`` node's ``dot_file=`` named no existing child graph.

    Issue #200.  This is deliberately a *distinct* error class, raised at node
    ENTRY, because the failure it describes is a child-graph RESOLUTION fault
    — not the edge-routing fault the engine's ``no_matching_edge`` termination
    used to frame it as.  A FAIL Outcome returned from here reaches edge
    selection, where FAIL is fail-fast (no plain-edge drift), so a graph with
    no failure edge terminated with "No matching edge from node 'X'" and the
    real cause (a missing file, usually resolved against the wrong base
    directory) was buried in an error_type that pointed at the wrong subsystem.

    The message names the node, the literal ``dot_file=`` value, and every
    tier of the EXTENSIONS.md §10 precedence chain, so a wrong-base-directory
    resolution is readable rather than inferred.
    """

    def __init__(
        self,
        *,
        node_id: str,
        dot_file: str,
        expanded: str,
        resolved_path: str,
        candidates: list[DotPathCandidate],
    ) -> None:
        self.node_id = node_id
        self.dot_file = dot_file
        self.expanded = expanded
        self.resolved_path = resolved_path
        self.candidates = candidates
        super().__init__(self._format())

    def _format(self) -> str:
        expansion_note = (
            ""
            if self.expanded == self.dot_file
            else f' (expanded to "{self.expanded}")'
        )
        lines = [
            (
                f"Child DOT file not found for node '{self.node_id}': "
                f'dot_file="{self.dot_file}"{expansion_note} resolved to '
                f"{self.resolved_path!r}, which does not exist."
            ),
            "This is a child-graph RESOLUTION failure, not an edge-routing failure.",
            (
                "Candidate paths tried (specs/EXTENSIONS.md §10 precedence chain — "
                "the first applicable tier wins):"
            ),
        ]
        for index, candidate in enumerate(self.candidates, start=1):
            if candidate.path is None:
                lines.append(
                    f"  {index}. {candidate.tier}: n/a — {candidate.unavailable_reason}"
                )
                continue
            marks = []
            marks.append("CHOSEN" if candidate.chosen else "not consulted")
            marks.append("EXISTS" if candidate.exists else "missing")
            lines.append(
                f"  {index}. {candidate.tier}: {candidate.path}  [{', '.join(marks)}]"
            )

        elsewhere = [c for c in self.candidates if c.exists and not c.chosen]
        if elsewhere:
            found = "; ".join(f"{c.tier} -> {c.path}" for c in elsewhere)
            lines.append(
                "NOTE: the child DOT DOES exist under a lower-precedence tier "
                f"({found}) — dot_file= resolved against the wrong base directory."
            )
        lines.append(
            "Fix: create the child DOT at the CHOSEN path, correct the dot_file= "
            "value, or have an upstream node write it before this node runs "
            "(write-then-run composition — resolution is intentionally lazy, so a "
            "runtime-generated child is supported)."
        )
        return "\n".join(lines)


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
        # Per-invocation counter used when engine is None (test-harness path).
        # Key: (logs_root, node.id) so distinct run roots don't share counts.
        # When engine is not None, the counter lives on the engine itself so
        # it persists correctly across loop iterations within one parent run.
        self._folder_invocation_counts: dict[tuple[str, str], int] = {}

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
        3. Assert the child DOT exists at node ENTRY; raise
           ChildDotResolutionError (issue #200) naming every candidate path if
           it does not, then read it.
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

        # (2) Resolve path.  Resolution itself stays LAZY (EXTENSIONS.md §10):
        # the candidate chain is walked with no existence check, so a child DOT
        # written earlier in THIS run (write-then-run composition) resolves
        # exactly as it always has.
        candidates = resolve_dot_path_candidates(dot_file, graph.source_dir, context)
        resolved_path = resolve_dot_path(dot_file, graph.source_dir, context)

        # (3) Assert existence at node ENTRY, then read the DOT file.
        #
        # Issue #200: returning Outcome(FAIL, ...) here sends the failure into
        # edge selection, where FAIL is fail-fast — so a graph with no failure
        # edge terminated as `no_matching_edge` / "No matching edge from node
        # 'X'", framing a missing FILE as a ROUTING fault.  Raising a distinct
        # resolution error instead keeps the fault in its own class: the engine
        # reports it as a child-graph resolution failure, and the message names
        # every tier of the precedence chain so a wrong-base-directory
        # resolution is readable rather than inferred.
        def _resolution_error() -> ChildDotResolutionError:
            return ChildDotResolutionError(
                node_id=node.id,
                dot_file=str(dot_file),
                expanded=_expand_path_variables(str(dot_file), context),
                resolved_path=resolved_path,
                candidates=candidates,
            )

        if not os.path.isfile(resolved_path):
            raise _resolution_error()

        try:
            with open(resolved_path) as f:
                dot_source = f.read()
        except FileNotFoundError:
            # The file vanished between the check above and the open (or the
            # path is a dangling symlink) — same fault, same diagnostic.
            raise _resolution_error() from None

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

        # (6a) Clear the per-cycle routing signal so the child pipeline starts
        # with NO inherited routing verdict. A stale preferred_label (e.g.
        # "converged" from a previous iteration's child) would otherwise be
        # matched by the child's own `context.preferred_label=` edge
        # conditions when a child node produces no verdict of its own.
        # Symmetric with the loop_restart clear in engine.py run(). Done
        # BEFORE the context.* attr injection below so deliberate seeding
        # via a `context.preferred_label` node attribute still works.
        child_context.set("preferred_label", None)

        # (6b) Inject context.* attributes from this folder node into child context.
        for attr_key, attr_value in node.attrs.items():
            if attr_key.startswith("context."):
                child_key = attr_key[len("context.") :]
                child_context.set(child_key, str(attr_value))

        # (7) Create child logs dir — namespaced per invocation so that a folder
        # node re-entered across loop iterations always gets a FRESH checkpoint
        # directory instead of resuming the completed state from iteration 1.
        #
        # When engine is not None (production path), track the count on the
        # engine itself; the engine object persists across all loop iterations
        # of one parent run, so the counter increments correctly.
        #
        # When engine is None (test-harness path), fall back to a counter on
        # this handler instance, keyed by (logs_root, node.id), so distinct
        # run roots don't share counts and the existing single-dir behaviour is
        # preserved for callers that never re-enter the same (root, node.id).
        #
        # First invocation uses the canonical name subgraph_{node.id} for
        # back-compat; subsequent invocations append __iter{n}.
        if engine is not None:
            if not hasattr(engine, "_folder_invocation_counts"):
                engine._folder_invocation_counts: dict[str, int] = {}  # type: ignore[attr-defined]
            _inv = engine._folder_invocation_counts.get(node.id, 0)
            engine._folder_invocation_counts[node.id] = _inv + 1
        else:
            _key = (logs_root, node.id)
            _inv = self._folder_invocation_counts.get(_key, 0)
            self._folder_invocation_counts[_key] = _inv + 1

        if _inv == 0:
            child_logs = os.path.join(logs_root, f"subgraph_{node.id}")
        else:
            child_logs = os.path.join(logs_root, f"subgraph_{node.id}__iter{_inv}")
        os.makedirs(child_logs, exist_ok=True)

        # (8) Create child HandlerRegistry.
        # Move 2: seed the child backend from the EXECUTING ENGINE's registry
        # rather than the captured self._backend.  This ensures that a folder
        # node inside a parallel branch inherits the branch's isolated backend
        # (from clone_for_branch) instead of the original parent backend.
        # Verified safe: a folder handler always receives the same backend as
        # its parent (per-node model selection uses _ProviderPreference on
        # that one backend, not distinct instances); _handler_registry_factory
        # is None on every production path.
        if self._handler_registry_factory is not None:
            child_registry = self._handler_registry_factory()
        else:
            from .context import HandlerContext

            # Prefer the executing engine's backend so branch isolation propagates
            # into child pipelines.  Fall back to self._backend when engine is None
            # (should not happen in production; kept for test-harness compatibility).
            effective_backend = (
                engine.handler_registry.get_backend()
                if engine is not None
                else self._backend
            )
            child_registry = HandlerRegistry(
                HandlerContext(
                    backend=effective_backend,
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
        # support#379 (fix 3): thread a scope discriminator onto child-engine
        # events, reusing the existing _branch_id mechanism (S4). Without
        # this, concurrent folder subgraphs under parallel fan-out emit
        # events that are ambiguous about which subgraph they came from.
        # Prefix with the parent's own branch_id (if any) so nesting under a
        # parallel branch stays disambiguated too.
        _parent_branch = getattr(engine, "_branch_id", None) if engine else None
        child_engine._branch_id = (
            f"{_parent_branch}>subgraph:{node.id}"
            if _parent_branch
            else f"subgraph:{node.id}"
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
            # support#379 (fix 4, additive): a normalized, 0-based repetition
            # counter that means the same thing here as it does in
            # manager_loop.py's _subgraph_runs entries (see cycle_index
            # there). This does NOT rename subgraph_{node.id} /
            # subgraph_{node.id}__iter{N} directories (_inv, above) — only
            # adds a consistent field for consumers that want to compare
            # "which repetition" across both handlers without knowing each
            # handler's own on-disk numbering convention.
            "cycle_index": _inv,
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

        # (12) Return child outcome verbatim. EXTENSIONS.md §25: this
        # propagates the child's is_explicit — a folder node's outcome CAN
        # carry a defaulted LLM completion (the child's terminal outcome), so
        # it must NOT be blanket-classified as explicit; explicitness flows
        # from the child's own verdict mechanism.
        return outcome
