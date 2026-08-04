"""Codergen handler — the default handler for LLM task nodes.

Reads the node's prompt, expands template variables, calls the LLM
backend, writes prompt/response/status to the logs directory, and
returns the outcome.

Spec coverage: CODER-001-011, Section 4.5
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..engine import PipelineEngine

from ..context import PipelineContext
from ..feedback import ensure_feedback_placeholder
from ..graph import Graph, Node, resolve_bool_attr
from ..outcome import Outcome, StageStatus
from ..transforms import expand_goal_variable, expand_params
from ..worker_observability import current_worker_sessions_dir


@runtime_checkable
class CodergenBackend(Protocol):
    """Interface for LLM execution backends.

    Spec Section 4.5: CodergenBackend Interface.

    ``graph`` (and ``incoming_edge`` where available) MUST be forwarded by the
    handler: the backend's fidelity resolution and fidelity=full transcript
    store/read gates require ``graph`` to resolve the thread key.  Omitting it
    silently disables full-fidelity continuity (see
    docs/designs/fidelity-full-session-continuity.md).
    """

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge: Any | None = None,
        graph: Graph | None = None,
    ) -> str | Outcome: ...


class CodergenHandler:
    """Handler for codergen (LLM task) nodes.

    Spec Section 4.5: Codergen Handler.
    """

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine: "PipelineEngine | None" = None,
    ) -> Outcome:
        """Execute a codergen node.

        1. Build prompt (expand $goal)
        2. Write prompt to logs
        3. Call backend
        4. Write response and status to logs
        5. Return outcome
        """
        # 1. Build prompt
        prompt = (
            node.prompt
            or (node.attrs.get("llm_prompt") if node.attrs else None)
            or node.label
        )
        # EXTENSIONS.md §29: feedback_from= is a delivery contract, not a
        # prompt convention. If this node declares feedback_from= and the
        # accumulated critique channel has content but the prompt does not
        # reference $prior_critiques_<node_id>, append a labeled block
        # carrying the placeholder so the P7 expansion below injects the
        # iteration-numbered history regardless. The placeholder controls
        # WHERE the history appears, never WHETHER it appears — forgetting
        # it cannot silently sever the feedback loop.
        prompt = ensure_feedback_placeholder(node, prompt, context)
        prompt = _expand_variables(prompt, graph, context)

        # 2. Write prompt to logs
        stage_dir = os.path.join(logs_root, node.id)
        os.makedirs(stage_dir, exist_ok=True)
        _write_file(os.path.join(stage_dir, "prompt.md"), prompt)

        # 3. Call LLM backend
        if self._backend is None:
            raise ValueError(
                "CodergenHandler requires a backend but none was provided. "
                "Pass backend=MockBackend() explicitly if you want simulated responses for testing."
            )
        try:
            # Forward `graph` into the backend so fidelity resolution and the
            # fidelity=full transcript store/read gates (which require
            # `graph is not None`) actually fire on the production path.
            #
            # Without this, full-fidelity continuity is silently dead: the
            # backend's gates skip and seed→recall loses history (proven by a
            # live DTU run — seeds wrote codewords, recall came back empty).
            #
            # `incoming_edge` is NOT available here: execute_with_retry (the sole
            # invoker, retry.py) threads `graph` but not the edge, and the engine
            # call sites (engine.py) don't pass it either. The edge only affects
            # EDGE-level `thread_id`/`fidelity` overrides; node-level and
            # graph-level thread/fidelity resolution — which the DTU and the vast
            # majority of pipelines use — work from `graph` alone. We pass
            # `incoming_edge=None` explicitly; threading the edge end-to-end is a
            # separate, larger change tracked for when edge-level overrides are
            # needed.
            #
            # `graph`/`incoming_edge` are OPTIONAL CodergenBackend params (declared
            # with defaults), so this unconditional call is the whole contract:
            # the production AmplifierBackend accepts them and all conforming test
            # doubles match this signature.
            # EXTENSIONS.md §26: while the backend call is in flight, expose
            # <stage_dir>/sessions as the destination for worker-session event
            # persistence.  The spawned child session runs in-process within
            # this same task context, so the session-event persister mounted in
            # the child (hooks-pipeline-observability) reads this ContextVar
            # and appends the child's REAL event stream (session lifecycle,
            # tool:pre/tool:post, ...) to
            # <stage_dir>/sessions/<session_id>/events.jsonl as it happens.
            # try/finally guarantees the reset even when the backend raises.
            sessions_token = current_worker_sessions_dir.set(
                os.path.join(stage_dir, "sessions")
            )
            try:
                result = await self._backend.run(
                    node, prompt, context, incoming_edge=None, graph=graph
                )
            finally:
                current_worker_sessions_dir.reset(sessions_token)
            if isinstance(result, Outcome):
                # EXTENSIONS.md §26: write response.md from the full text carried
                # on the Outcome (set by _parse_outcome before any truncation).
                # The production AmplifierBackend spawn path always returns an
                # Outcome, so this is the only place response.md is written on
                # that path.  If response_text is None (infrastructure failures,
                # tool handlers) we skip the write — there is no text to save.
                if result.response_text:
                    _write_file(
                        os.path.join(stage_dir, "response.md"), result.response_text
                    )
                _write_status(stage_dir, result)
                return result
            response_text = str(result)
        except Exception as e:
            outcome = Outcome(
                status=StageStatus.FAIL,
                failure_reason=str(e),
                # support#381: generalize failed_step (previously ToolHandler-only)
                # to CodergenHandler — the highest-value gap since it covers
                # every LLM node in every pipeline. Outcome.notes/failure_reason
                # alone gave consumers no prompt/response context to diagnose
                # an LLM-node failure with.
                failed_step=_build_failed_step(
                    prompt=prompt,
                    response_text=None,
                    error=str(e),
                ),
            )
            _write_status(stage_dir, outcome)
            return outcome

        # 4. Write response to logs
        _write_file(os.path.join(stage_dir, "response.md"), response_text)

        # 5. Build and write outcome.
        #
        # EXTENSIONS.md §25 — fail-closed goal-gate contract: when the node
        # carries goal_gate=true, a string response from the backend must go
        # through the verdict-recovery ladder (_parse_outcome). JSON / fenced
        # JSON / embedded verdicts are honored (is_explicit=True); plain prose
        # returns RETRY so the gate is never satisfied by a defaulted response.
        # This is the exact goal_gate check that PRINCIPLES.md Delta 1
        # recommends adding to the spec's CodergenHandler pseudocode.
        #
        # Non-goal_gate nodes keep the spec §4.5 unconditional-SUCCESS wrap
        # (is_explicit defaults to False — the status is defaulted, not
        # asserted; that is only load-bearing for goal_gate nodes).
        if resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate"):
            # Deferred import: avoid a handlers <-> backend import cycle at
            # module load time.
            from ..backend import _parse_outcome

            outcome = _parse_outcome(response_text, node=node)
            merged_updates: dict[str, Any] = {
                "last_stage": node.id,
                "last_response": response_text[:200],
            }
            merged_updates.update(outcome.context_updates or {})
            outcome.context_updates = merged_updates
            # support#381: a goal_gate=true node's verdict-recovery ladder can
            # return FAIL (see _parse_outcome); attach failed_step here too so
            # the failure carries the same prompt/response diagnostic detail
            # as the exception path above, instead of only notes/failure_reason.
            if outcome.status == StageStatus.FAIL and outcome.failed_step is None:
                outcome.failed_step = _build_failed_step(
                    prompt=prompt,
                    response_text=response_text,
                    error=outcome.failure_reason,
                )
        else:
            outcome = Outcome(
                status=StageStatus.SUCCESS,
                notes=f"Stage completed: {node.id}",
                context_updates={
                    "last_stage": node.id,
                    "last_response": response_text[:200],
                },
            )
        _write_status(stage_dir, outcome)
        return outcome


def _expand_variables(prompt: str, graph: Graph, context: PipelineContext) -> str:
    """Expand template variables in a prompt string.

    L-17: Delegates to the shared expand_goal_variable utility for $goal.
    Runtime variable: $context resolves to the previous node's response
    (stored as ``last_response`` in the pipeline context).
    P7: Plain context keys (no "." in name) are expanded as $param tokens,
    enabling context.* attrs injected by parent folder/house nodes.

    Spec Section 4.5: Variable expansion.
    """
    # $goal — static for the whole pipeline (also expanded at parse time in transforms)
    context_goal = context.get("graph.goal") or ""
    result = expand_goal_variable(prompt, graph.goal, context_goal)

    # $context — runtime, changes after each node completes
    if "$context" in result:
        last_response = context.get("last_response", "") or ""
        result = result.replace("$context", str(last_response))

    # P7: Expand plain context keys injected via context.* parent node attrs.
    # Only expands keys without "." (namespaced keys like graph.goal are excluded).
    if "$" in result:
        plain_params = {
            k: str(v) for k, v in context.snapshot().items() if "." not in k
        }
        if plain_params:
            result = expand_params(result, plain_params)

    return result


def _write_file(path: str, content: str) -> None:
    """Write content to a file."""
    with open(path, "w") as f:
        f.write(content)


_PROMPT_TAIL_CHARS = 500
_RESPONSE_TAIL_CHARS = 2000
_TOTAL_CAP_BYTES = 8192


def _build_failed_step(
    *,
    prompt: str,
    response_text: str | None,
    error: str | None,
) -> dict[str, Any]:
    """Build the ``failed_step`` payload for a failed codergen (LLM) node.

    support#381: generalizes the ``failed_step`` structured-detail pattern
    (previously ToolHandler-only, see handlers/tool.py's
    ``_build_failed_step``) to CodergenHandler — the highest-value gap since
    it covers every LLM node in every pipeline. ``Outcome.notes`` /
    ``failure_reason`` alone give consumers no prompt/response context to
    diagnose an LLM-node failure with; this mirrors ToolHandler's
    command/stdout/stderr shape with the LLM-appropriate analogs
    (prompt/response/error) plus the same bounded-size discipline.

    Empty/absent response text produces ``""``, never ``None`` (mirrors
    ToolHandler's stdout_tail/stderr_tail convention).
    """
    failed_step: dict[str, Any] = {
        "prompt": prompt[:_PROMPT_TAIL_CHARS],
        "response_tail": (response_text or "")[-_RESPONSE_TAIL_CHARS:],
        "error": error,
    }

    # Bounded total size, mirroring ToolHandler's 8 KiB cap discipline:
    # drop response_tail first (least useful once the prompt/error are known).
    if len(json.dumps(failed_step)) > _TOTAL_CAP_BYTES:
        failed_step = dict(failed_step)
        del failed_step["response_tail"]
        failed_step["verification_gap"] = {"log_filtered": True}

    return failed_step


def _write_status(stage_dir: str, outcome: Outcome) -> None:
    """Write status.json for a stage outcome.

    M-19: Uses 'outcome' as the primary field name per spec Appendix C.
    Keeps 'status' as backward-compat alias.
    """
    data = {
        "outcome": outcome.status.value,
        "status": outcome.status.value,  # backward compat
        "preferred_next_label": outcome.preferred_label,
        "suggested_next_ids": outcome.suggested_next_ids,
        "context_updates": outcome.context_updates,
        "notes": outcome.notes,
        "failure_reason": outcome.failure_reason,
        # EXTENSIONS.md §25: is_explicit is durable audit data; the codergen
        # early-writer must carry it too, not just the engine's writers.
        "is_explicit": outcome.is_explicit,
        # EXTENSIONS.md §26: session_id is the join key from this node's
        # status record to the persisted worker-session event stream at
        # <stage_dir>/sessions/<session_id>/events.jsonl — the engine's
        # writers carry it (engine.py _write_node_status); the early-writer
        # must too, or the Outcome path leaves a dangling trail.
        "session_id": outcome.session_id,
    }
    _write_file(os.path.join(stage_dir, "status.json"), json.dumps(data, indent=2))
