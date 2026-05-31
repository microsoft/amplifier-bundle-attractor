"""Handler registry and base protocol for pipeline node handlers.

Each node type (start, exit, codergen, tool, etc.) has a handler that
implements the NodeHandler protocol. The HandlerRegistry maps nodes to
their handlers based on the node's type attribute or shape-to-handler-type
mapping.

Spec coverage: HAND-001–007, Section 4.1–4.2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome
from ..validation import SHAPE_TO_HANDLER
from .context import HandlerContext

if TYPE_CHECKING:
    from ..engine import PipelineEngine


@runtime_checkable
class NodeHandler(Protocol):
    """Protocol for pipeline node handlers.

    Spec Section 4.1: Handler Interface.
    """

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome: ...


class HandlerRegistry:
    """Maps nodes to their handlers.

    Resolution order:
    1. Node's explicit ``type`` attribute (e.g. type="conditional")
    2. Shape-to-handler-type mapping (spec Section 2.8)
    3. Default: codergen

    Spec Section 4.2: Handler Registry.
    """

    def __init__(self, ctx: HandlerContext) -> None:
        from .codergen import CodergenHandler
        from .conditional import ConditionalHandler
        from .exit import ExitHandler
        from .fan_in import FanInHandler
        from .human import HumanGateHandler
        from .manager_loop import ManagerLoopHandler
        from .parallel import ParallelHandler
        from .pipeline import PipelineHandler
        from .start import StartHandler
        from .tool import ToolHandler

        self._ctx = ctx

        self._handlers: dict[str, NodeHandler] = {
            "start": StartHandler(),
            "exit": ExitHandler(),
            "codergen": CodergenHandler(backend=ctx.backend),
            "conditional": ConditionalHandler(),
            "tool": ToolHandler(),
            "wait.human": HumanGateHandler(
                interviewer=ctx.interviewer,
                hooks=ctx.hooks,
            ),
            "stack.manager_loop": ManagerLoopHandler(
                backend=ctx.backend,
                hooks=ctx.hooks,
                cancel_event=ctx.cancel_event,
                interviewer=ctx.interviewer,
            ),
            "parallel": ParallelHandler(
                hooks=ctx.hooks,
            ),
            "parallel.fan_in": FanInHandler(),
            "pipeline": PipelineHandler(
                hooks=ctx.hooks,
                cancel_event=ctx.cancel_event,
                backend=ctx.backend,
                interviewer=ctx.interviewer,
            ),
        }

    def get(self, node: Node) -> NodeHandler:
        """Resolve the handler for a node.

        Resolution order:
        1. Node's explicit ``type`` attribute if it matches a registered handler
        2. ``node_type`` attribute from node.attrs if it matches a registered handler
        3. Shape-to-handler-type mapping (SHAPE_TO_HANDLER, spec §2.8)

        Raises:
            ValueError: If the node's shape is not in SHAPE_TO_HANDLER or the
                resolved handler type is not registered.  No silent fallback to
                codergen — unknown shape = clear error, not surprise LLM call.
        """
        if node.type and node.type in self._handlers:
            handler_type = node.type
        else:
            node_type_attr = node.attrs.get("node_type") if node.attrs else None
            if node_type_attr and node_type_attr in self._handlers:
                handler_type = node_type_attr
            else:
                if node.shape not in SHAPE_TO_HANDLER:
                    raise ValueError(
                        f"Unknown node shape '{node.shape}' for node '{node.id}'. "
                        f"Supported shapes: {sorted(SHAPE_TO_HANDLER.keys())}. "
                        f"To use an LLM-driven node, use shape=box (codergen handler)."
                    )
                handler_type = SHAPE_TO_HANDLER[node.shape]

        if handler_type not in self._handlers:
            raise ValueError(
                f"Handler '{handler_type}' for node '{node.id}' is not registered. "
                f"This is an engine misconfiguration — '{handler_type}' appears in "
                f"SHAPE_TO_HANDLER but was not added to HandlerRegistry._handlers."
            )
        return self._handlers[handler_type]

    def get_backend(self) -> "object | None":
        """Return the backend from the codergen handler, if present.

        Used by child-engine builders (PipelineHandler, ManagerLoopHandler)
        to seed child registries from the currently executing engine's backend
        rather than a captured ``self._backend`` reference.  This ensures that
        folder nodes inside a parallel branch inherit the branch-isolated
        backend rather than the original parent backend (Move 2).
        """
        from .codergen import CodergenHandler

        codergen = self._handlers.get("codergen")
        if isinstance(codergen, CodergenHandler):
            return codergen._backend
        return None

    def clone_for_branch(self) -> "HandlerRegistry":
        """Create a branch-isolated copy of this registry.

        Shallow-copies the handlers dict and replaces the codergen handler
        with a new instance backed by a cloned backend.  The pipeline handler
        is also replaced with a fresh instance (mutable ``_subgraph_runs``).
        All other handlers are shared (they are stateless or their backend
        dependency is severed by Move 2).

        Called by ``PipelineEngine.clone_for_branch`` so each concurrent
        parallel branch gets its own backend mutable state
        (``_thread_transcripts``, ``_completed_nodes``).
        """
        from .codergen import CodergenHandler
        from .pipeline import PipelineHandler

        new = HandlerRegistry.__new__(HandlerRegistry)
        new._ctx = self._ctx  # frozen dataclass — safe to share
        new._handlers = dict(self._handlers)

        # Replace codergen with a clone that has its own backend state.
        # The cloned backend carries fresh _thread_transcripts and _completed_nodes
        # so concurrent branches cannot cross-pollute thread transcript or session state.
        original_codergen = self._handlers.get("codergen")
        if isinstance(original_codergen, CodergenHandler):
            backend = original_codergen._backend
            if backend is not None and hasattr(backend, "clone"):
                cloned_backend = backend.clone()
                new._handlers["codergen"] = CodergenHandler(backend=cloned_backend)

        # Replace pipeline handler with a fresh instance (has mutable _subgraph_runs).
        # Note: after Move 2, PipelineHandler.execute seeds its child registry
        # from the executing engine's handler_registry.get_backend() instead of
        # self._backend, so the backend field here is only a fallback for the
        # no-engine path.
        original_pipeline = self._handlers.get("pipeline")
        if isinstance(original_pipeline, PipelineHandler):
            new._handlers["pipeline"] = PipelineHandler(
                hooks=original_pipeline._hooks,
                cancel_event=original_pipeline._cancel_event,
                backend=original_pipeline._backend,
                interviewer=original_pipeline._interviewer,
            )

        return new

    def register(self, handler_type: str, handler: NodeHandler) -> None:
        """Register a custom handler for a handler type."""
        self._handlers[handler_type] = handler
