"""Wait-for-human gate handler.

Maps to the spec's ``wait.human`` node (shape=hexagon). The handler
derives choices from outgoing edge labels, presents the question to
a human via the Interviewer interface, and routes based on the
human's selection.

Spec coverage: HUMAN-001–008, Section 4.10.
"""

from __future__ import annotations

import logging
import pathlib
import re
from typing import Any

from ..context import PipelineContext
from ..graph import Graph, Node
from ..interviewer import (
    Answer,
    AnswerValue,
    Interviewer,
    Option,
    Question,
    QuestionType,
)
from ..outcome import Outcome, StageStatus
from ..pipeline_events import (
    PIPELINE_INTERVIEW_COMPLETED,
    PIPELINE_INTERVIEW_STARTED,
    PIPELINE_INTERVIEW_TIMEOUT,
)
from ..substitution import substitute_context
from ..transforms import expand_goal_variable

logger = logging.getLogger(__name__)

# Maximum bytes read from any single attachment file before truncating.
_MAX_ATTACHMENT_BYTES = 100_000  # 100 KB


def _expand_description(
    description: str, graph: Graph, context: PipelineContext
) -> str:
    """Expand $variable tokens in a description string.

    Uses the same expansion as codergen prompts: $goal, $context / $last_response,
    and all plain (dot-free) context keys such as $task, $spec, $message_summary.

    Args:
        description: Raw description string from the node attribute.
        graph: The pipeline graph (provides graph.goal).
        context: Current pipeline context (provides runtime values).

    Returns:
        Description with all resolvable $tokens replaced.
    """
    context_goal = context.get("graph.goal") or ""
    result = expand_goal_variable(description, graph.goal, context_goal)

    # $context — runtime alias for last_response (special alias; must be
    # resolved before the general substitution pass to avoid clobbering).
    if "$context" in result:
        last_response = context.get("last_response", "") or ""
        result = result.replace("$context", str(last_response))

    # M5 (R12): Unified substitution — handles both $key and ${key} forms,
    # including dotted keys (e.g. ${tool.output}, $tool.output).
    # Missing keys are left as literal tokens (same "literal-on-miss" policy
    # as the other substitution sites).
    if "$" in result:
        result = substitute_context(result, context.snapshot())

    return result


def _resolve_attachments(patterns_str: str, workspace_dir: str) -> list[dict[str, Any]]:
    """Resolve comma-separated glob patterns to file envelope dicts.

    Each envelope contains: path, filename, directory, size, content.
    Files larger than _MAX_ATTACHMENT_BYTES are truncated with a [truncated] marker.
    Unreadable files are logged and skipped.

    Args:
        patterns_str: Comma-separated glob patterns, e.g. ".ai/*.md,.ai/**/*.txt"
        workspace_dir: Absolute path to resolve globs against.  Falls back to
                       the current working directory when empty.

    Returns:
        List of file envelope dicts, sorted by path within each pattern.
        Empty list when patterns_str is blank or no files match.
    """
    if not patterns_str.strip():
        return []

    workspace = pathlib.Path(workspace_dir) if workspace_dir else pathlib.Path(".")
    envelopes: list[dict[str, Any]] = []

    for pattern in patterns_str.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        try:
            matches = sorted(workspace.glob(pattern))
        except Exception as exc:
            logger.warning("Skipping invalid glob pattern %r: %s", pattern, exc)
            continue

        for match in matches:
            if not match.is_file():
                continue
            try:
                raw = match.read_bytes()
                if len(raw) > _MAX_ATTACHMENT_BYTES:
                    content = (
                        raw[:_MAX_ATTACHMENT_BYTES].decode("utf-8", errors="replace")
                        + "\n\n[truncated]"
                    )
                else:
                    content = raw.decode("utf-8", errors="replace")

                try:
                    rel = match.relative_to(workspace)
                    path_str = str(rel)
                    parent = rel.parent
                    dir_str = str(parent) if str(parent) != "." else "."
                except ValueError:
                    path_str = str(match)
                    dir_str = str(match.parent)

                envelopes.append(
                    {
                        "path": path_str,
                        "filename": match.name,
                        "directory": dir_str,
                        "size": match.stat().st_size,
                        "content": content,
                    }
                )
            except Exception as exc:
                logger.warning("Skipping unreadable attachment %s: %s", match, exc)

    return envelopes


# L-11: Patterns for accelerator key extraction from edge labels.
# Matches: "[Y] Yes", "[OK] Okay", "1) Option One", "2. Option Two"

_BRACKET_KEY_RE = re.compile(r"^\[([^\]]+)\]\s+")
_NUMBER_KEY_RE = re.compile(r"^(\d+)[.)]\s+")


def _parse_accelerator_key(label: str) -> str:
    """Extract accelerator key from a label, or return the full label.

    Supports patterns:
        [Y] Yes       -> "Y"
        [OK] Okay     -> "OK"
        1) Option One -> "1"
        2. Option Two -> "2"
        Approve       -> "Approve"  (no accelerator)
    """
    if not label:
        return label
    m = _BRACKET_KEY_RE.match(label)
    if m:
        return m.group(1)
    m = _NUMBER_KEY_RE.match(label)
    if m:
        return m.group(1)
    return label


class HumanGateHandler:
    """Handler for human gate nodes (shape=hexagon).

    Derives choices from outgoing edge labels, presents a
    multiple-choice question to the interviewer, and returns
    an Outcome with suggested_next_ids for unambiguous routing.

    Requires an explicit Interviewer instance. Pass
    AutoApproveInterviewer() for CI/testing environments.
    """

    def __init__(
        self,
        interviewer: Interviewer | None = None,
        hooks: object | None = None,
    ) -> None:
        self._interviewer = interviewer
        self._hooks = hooks

    def _get_stage_id(self, node: Node, context: PipelineContext) -> str:
        """Return a unique stage ID for this node, incrementing per re-entrant call.

        First invocation returns ``node.id`` unchanged (e.g. ``"HumanBrainstorm"``)
        for backward compatibility.  Subsequent invocations in the same pipeline
        run return ``"{node.id}-{n}"`` (e.g. ``"HumanBrainstorm-2"``,
        ``"HumanBrainstorm-3"``).

        The iteration count is stored in context under the key
        ``_gate_iter.{node.id}`` so it persists across loop re-entries and is
        visible to downstream nodes via the context snapshot.
        """
        iter_key = f"_gate_iter.{node.id}"
        iteration = int(context.get(iter_key) or 0) + 1
        context.set(iter_key, iteration)
        return node.id if iteration == 1 else f"{node.id}-{iteration}"

    async def _emit(self, event_name: str, data: dict) -> None:  # type: ignore[type-arg]
        """Emit an event via hooks, if provided."""
        if self._hooks is not None:
            await self._hooks.emit(event_name, data)  # type: ignore[union-attr]

    async def _dispatch_ask(self, question: Question) -> Answer:
        """Ask the interviewer, preferring async_ask to avoid sync/async bridge deadlock.

        Prefers ``async_ask`` when present to avoid the sync/async bridge
        deadlock caused by ``nest_asyncio`` not being installed in the worker
        container.  Falls back to the synchronous ``ask`` otherwise.
        """
        assert self._interviewer is not None  # guaranteed by execute() guard
        if hasattr(self._interviewer, "async_ask"):
            return await self._interviewer.async_ask(question)  # type: ignore[attr-defined]
        return self._interviewer.ask(question)

    async def _check_special_answer(
        self,
        answer: Answer,
        node: Node,
        text_key: str,
        prompt: str,
    ) -> Outcome | None:
        """Handle TIMEOUT and SKIPPED answers, returning a terminal Outcome or None.

        Emits ``PIPELINE_INTERVIEW_TIMEOUT`` for TIMEOUT answers, then returns
        ``None`` so the caller falls through to the normal success path.

        Returns a FAIL ``Outcome`` for SKIPPED answers per spec M-13.

        Returns ``None`` if no special handling is needed.

        Args:
            answer:   The answer received from the interviewer.
            node:     The current pipeline node (used for node_id / label).
            text_key: The context-update key for ``None`` on SKIPPED
                      (``"human.gate.selected"`` or ``"human.gate.text"``).
            prompt:   The prompt text sent to the interviewer (used in TIMEOUT payload).
        """
        if (
            isinstance(answer.value, AnswerValue)
            and answer.value == AnswerValue.TIMEOUT
        ):
            await self._emit(
                PIPELINE_INTERVIEW_TIMEOUT,
                {"node_id": node.id, "prompt": prompt, "timeout": True},
            )
            return None

        if (
            isinstance(answer.value, AnswerValue)
            and answer.value == AnswerValue.SKIPPED
        ):
            return Outcome(
                status=StageStatus.FAIL,
                context_updates={
                    text_key: None,
                    "human.gate.label": node.label,
                },
                notes=f"Human gate '{node.id}': interaction was skipped",
            )

        return None

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
    ) -> Outcome:
        """Present choices to a human and route based on selection.

        1. Derive choices from outgoing edge labels.
        2. Build a Question with those choices.
        3. Ask the interviewer.
        4. Map the answer to suggested_next_ids for edge selection.
        """
        if self._interviewer is None:
            raise ValueError(
                "HumanGateHandler requires an Interviewer but none was provided. "
                "Pass interviewer=AutoApproveInterviewer() explicitly if you want "
                "auto-approve behavior for CI/testing."
            )
        # Freeform mode: text input instead of edge-derived choices
        if node.attrs.get("mode") == "freeform":
            return await self._execute_freeform(node, context, graph)
        # 1. Derive choices from outgoing edges and build label-to-node mapping
        edges = graph.outgoing_edges(node.id)
        choices: list[str] = []
        # Map each choice label to the list of target node IDs (M-12)
        label_to_targets: dict[str, list[str]] = {}
        for edge in edges:
            label = edge.label or edge.to_node
            if label not in choices:
                choices.append(label)
            label_to_targets.setdefault(label, []).append(edge.to_node)

        # 2. Build the question with accelerator keys (L-11)
        # Increment the iteration counter BEFORE prompt expansion so that
        # pattern prompts can reference ${_gate_iter.<node_id>} to show
        # turn-N indicators to the user (Issue 17b).
        stage_id = self._get_stage_id(node, context)

        # node.prompt is a first-class Node field populated by the DOT parser (the
        # parser pops "prompt" from attrs into node.prompt, so node.attrs.get("prompt")
        # always returns None for DOT-parsed nodes).  Fall back to attrs for nodes
        # constructed directly in tests or legacy callers that set attrs["prompt"].
        raw_prompt = (
            node.prompt
            or node.attrs.get("prompt")
            or node.label
            or f"Human gate: {node.id}"
        )
        # Expand $variable tokens in the prompt (e.g. variables injected by a
        # parent folder node via context.* attrs) using the same expansion pipeline
        # as the description field.
        prompt = (
            _expand_description(raw_prompt, graph, context)
            if "$" in raw_prompt
            else raw_prompt
        )

        # Read description for edge-choice gates (same as freeform path)
        description = node.attrs.get("description", "")
        if description:
            description = _expand_description(description, graph, context)

        metadata: dict[str, Any] = {}
        if description:
            metadata["description"] = description

        # Extract accelerator keys from labels for option keys
        key_to_label: dict[str, str] = {}
        options: list[Option] = []
        for c in choices:
            key = _parse_accelerator_key(c)
            key_to_label[key] = c
            options.append(Option(key=key, label=c))
        if choices:
            question = Question(
                text=prompt,
                type=QuestionType.MULTIPLE_CHOICE,
                options=options,
                stage=stage_id,
                metadata=metadata,
            )
        else:
            # No labeled edges — use a simple confirmation
            question = Question(
                text=prompt,
                type=QuestionType.CONFIRMATION,
                stage=stage_id,
                metadata=metadata,
            )

        # 3. Emit interview started event
        await self._emit(
            PIPELINE_INTERVIEW_STARTED,
            {"node_id": node.id, "question": question.text},
        )

        # 4. Ask the interviewer
        answer = await self._dispatch_ask(question)

        await self._emit(
            PIPELINE_INTERVIEW_COMPLETED,
            {
                "node_id": node.id,
                "answer": str(answer.value)
                if hasattr(answer, "value")
                else str(answer),
            },
        )

        # 5. Handle TIMEOUT and SKIPPED answers
        special = await self._check_special_answer(
            answer, node, "human.gate.selected", prompt
        )
        if special is not None:
            return special

        # 6. Determine the selected label and map to target node IDs (M-12)
        selected = self._resolve_selection(answer, choices, key_to_label)
        target_ids = label_to_targets.get(selected or "", []) if selected else []

        return Outcome(
            status=StageStatus.SUCCESS,
            suggested_next_ids=target_ids if target_ids else None,
            context_updates={
                "human.gate.selected": selected,
                "human.gate.label": node.label,
                # Set last_response and last_stage so $context works in downstream nodes.
                # Mirrors the codergen convention: context_updates={"last_stage": ..., "last_response": ...[:200]}
                "last_response": (selected or "")[:200],
                "last_stage": node.id,
            },
            notes=f"Human gate '{node.id}': selected '{selected}'",
        )

    async def _execute_freeform(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
    ) -> Outcome:
        """Handle a freeform text input gate (mode='freeform').

        Instead of deriving choices from outgoing edges, presents a text
        input question.  Stores the human's text in ``context_updates`` as
        ``human.gate.text`` for downstream agent injection.

        When the node defines ``description``, ``attachments_inline``, or
        ``attachments_ref`` attributes, the Question is enriched with a
        ``metadata`` dict so that the Interviewer (e.g.
        InputRequestInterviewer) can build a rich three-zone A2UI schema:
        instructions, file attachments for review, and a text input area.
        """
        assert self._interviewer is not None  # guaranteed by execute() guard

        # Increment the iteration counter BEFORE prompt expansion so that
        # pattern prompts can reference ${_gate_iter.<node_id>} to show
        # turn-N indicators to the user (Issue 17b).
        stage_id = self._get_stage_id(node, context)

        # node.prompt is a first-class Node field (DOT parser pops "prompt" from
        # attrs into node.prompt).  Fall back to attrs for directly-constructed nodes.
        raw_prompt = (
            node.prompt
            or node.attrs.get("prompt")
            or node.label
            or f"Human gate: {node.id}"
        )
        # Expand $variable tokens (e.g. $gate_topic injected by a parent folder node).
        prompt = (
            _expand_description(raw_prompt, graph, context)
            if "$" in raw_prompt
            else raw_prompt
        )

        # --- Rich input request: read new attributes ---
        description = node.attrs.get("description", "")
        inline_patterns = node.attrs.get("attachments_inline", "")
        ref_patterns = node.attrs.get("attachments_ref", "")

        # Expand $variables in description (same pattern as codergen._expand_variables)
        if description:
            description = _expand_description(description, graph, context)

        # Resolve glob patterns to file envelopes (point-in-time snapshots).
        # Prefer context.target_dir (the session's actual working directory, e.g.
        # /workspace/project/) over graph.source_dir (the DOT file's parent, which
        # is empty for built-in pipelines like "quick").
        workspace_dir = context.get("context.target_dir") or graph.source_dir or ""
        inline_envelopes = _resolve_attachments(inline_patterns, workspace_dir)
        ref_envelopes = _resolve_attachments(ref_patterns, workspace_dir)

        # Build metadata dict — only include keys that have values
        metadata: dict[str, Any] = {}
        if description:
            metadata["description"] = description
        if inline_envelopes:
            metadata["attachments_inline"] = inline_envelopes
        if ref_envelopes:
            metadata["attachments_ref"] = ref_envelopes

        question = Question(
            text=prompt,
            type=QuestionType.FREEFORM,
            stage=stage_id,
            metadata=metadata,
        )

        await self._emit(
            PIPELINE_INTERVIEW_STARTED,
            {"node_id": node.id, "question": question.text},
        )

        answer = await self._dispatch_ask(question)

        await self._emit(
            PIPELINE_INTERVIEW_COMPLETED,
            {
                "node_id": node.id,
                "answer": answer.text if answer.text else str(answer.value),
            },
        )

        # Handle TIMEOUT and SKIPPED answers
        special = await self._check_special_answer(
            answer, node, "human.gate.text", prompt
        )
        if special is not None:
            return special

        # Route via outgoing edges (freeform gates typically have a single edge)
        edges = graph.outgoing_edges(node.id)
        target_ids = [edge.to_node for edge in edges]

        return Outcome(
            status=StageStatus.SUCCESS,
            suggested_next_ids=target_ids if target_ids else None,
            context_updates={
                "human.gate.text": answer.text,
                "human.gate.label": node.label,
                # Set last_response and last_stage so $context works in downstream nodes.
                # Mirrors the codergen convention: context_updates={"last_stage": ..., "last_response": ...[:200]}
                "last_response": (answer.text or "")[:200],
                "last_stage": node.id,
            },
            notes=f"Human gate '{node.id}': freeform response received",
        )

    def _resolve_selection(
        self,
        answer: Answer,
        choices: list[str],
        key_to_label: dict[str, str] | None = None,
    ) -> str | None:
        """Map an Answer to a choice label.

        Falls back to the first choice on SKIPPED, TIMEOUT, or
        unrecognized answers. Supports accelerator key lookup via
        key_to_label mapping (L-11).
        """
        default = choices[0] if choices else None
        key_map = key_to_label or {}

        # Handle enum AnswerValues that mean "no real selection"
        if isinstance(answer.value, AnswerValue):
            if answer.value in (
                AnswerValue.SKIPPED,
                AnswerValue.TIMEOUT,
                AnswerValue.YES,
            ):
                return default
            if answer.value == AnswerValue.NO:
                # "No" maps to the second choice if available, else default
                return choices[1] if len(choices) > 1 else default

        # String value — try to match against choices or accelerator keys
        value_str = str(answer.value)
        # Check selected_option key -> map back to full label
        if answer.selected_option:
            opt_key = answer.selected_option.key
            if opt_key in key_map:
                return key_map[opt_key]
            if opt_key in choices:
                return opt_key
        # Check if value matches an accelerator key (L-11)
        if value_str in key_map:
            return key_map[value_str]
        if value_str in choices:
            return value_str

        return default
