"""``feedback_from=`` feedback-accumulation contract (EXTENSIONS.md §29).

A node may declare ``feedback_from="<critic_node_id>"``: on every
``loop_restart`` edge traversal the engine collects the named critic node's
output from the just-completed iteration, appends it to an accumulated channel
keyed on the **target node** (the generator), and injects the accumulated
history — with iteration numbering (``"Iteration N critique: …"``) — into the
target node's execution context as the plain key
``prior_critiques_<target_node_id>``.

**Per-target key scoping:** Both the persistent channel and the injected key
are scoped to the target node ID.  This prevents feedback leakage when two
generator nodes each declare a different critic in the same pipeline:

- Channel (dotted, not prompt-expanded): ``feedback.channel.<target_node_id>``
- Injection key (plain, prompt-expanded): ``prior_critiques_<target_node_id>``

**Delivery is guaranteed, placement is optional.**  Pipeline authors MAY
reference ``$prior_critiques_<target_node_id>`` in their ``prompt`` attribute
— e.g. ``$prior_critiques_generate`` for a node whose ``id`` is ``generate`` —
to control WHERE the accumulated history appears.  The P7 ``$key``
substitution path in ``handlers/codergen.py:_expand_variables`` expands plain
(non-dotted) keys, so no new substitution wiring is needed.  If the prompt
does NOT reference the placeholder, the handler appends a labeled critique
block automatically (``ensure_feedback_placeholder()``, called from the
codergen handler before variable expansion).  Declaring ``feedback_from=``
is therefore sufficient on its own: forgetting the placeholder cannot
silently sever the feedback loop — that failure mode is exactly the
prompt-convention fragility this contract exists to eliminate.

**Curation / token discipline:**
The accumulated channel is bounded to ``MAX_CRITIQUES`` entries (default 5).
When the channel exceeds this limit, the oldest entry is dropped, keeping the
window of injected critiques at most ``MAX_CRITIQUES`` entries.  The critique
node itself is the natural curator (pipeline authors write the critique node's
prompt to emit a single-highest-leverage observation), so the window bound is
a safety net, not the primary curation mechanism.  The token cost per iteration
is bounded: at most ``MAX_CRITIQUES × MAX_CRITIQUE_CHARS`` characters (defaults:
5 × 500 = 2 500 chars, well within typical prompt budgets).

**Timing contract:**
``collect_and_inject_feedback()`` is called at ``loop_restart`` time, AFTER the
critic node has completed (its output is in ``node_outcomes``) and BEFORE
``node_outcomes.clear()`` erases it.  The injected ``prior_critiques`` key
survives the restart because ``context_updates`` are intentionally left
untouched by the loop_restart block (see ``engine.py`` comment at Step 6).

**Backward compatibility:**
Nodes without ``feedback_from=`` are completely untouched.  The file-based
``.ai/feedback/`` convention used by existing pipelines continues to work.

**Walk-upstream note:**
The canonical attractor spec has no feedback-accumulation vocabulary.  This
extension should be proposed upstream: the mathematical heart of the attractor
(retry-with-accumulated-critique is descent, not re-flip) is a spec-level claim
that deserves a spec-level mechanism.  Until then, this extension documents the
behavior here.

Shared here (not inlined in ``engine.py``) so both ``engine`` and future
consumers can use it without a circular import — same shape as ``must_write.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import PipelineContext
    from .graph import Graph, Node
    from .outcome import Outcome

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Maximum number of prior critiques injected into the target node's prompt.
#: Oldest entries are dropped when the channel exceeds this limit, bounding
#: the token cost to at most ``MAX_CRITIQUES × MAX_CRITIQUE_CHARS`` chars.
MAX_CRITIQUES: int = 5

#: Maximum characters per individual critique entry.  Longer outputs are
#: truncated with a ``[…truncated]`` suffix.  This mirrors the ``last_response``
#: truncation convention and prevents a single verbose critic from flooding the
#: accumulated channel.
MAX_CRITIQUE_CHARS: int = 500

#: Prefix for the per-target plain injection key.  The full key for a target
#: node with id ``"generate"`` is ``"prior_critiques_generate"``, referenced
#: in prompts as ``$prior_critiques_generate``.  Plain (no ".") so it expands
#: in ``prompt`` attributes via the P7 ``$key`` substitution path.
#: Scoped per target to prevent feedback leakage between multiple generator
#: nodes that each declare a different critic in the same pipeline.
PRIOR_CRITIQUES_KEY_PREFIX: str = "prior_critiques_"

#: The UNSCOPED key name from the initial (pre-review) design.  The engine
#: never writes it: per-target scoping (``PRIOR_CRITIQUES_KEY_PREFIX +
#: node_id``) replaced it to prevent cross-target leakage.  Retained as a
#: named constant so tests can assert the unscoped key is never written
#: (regression guard for the scoping fix).
PRIOR_CRITIQUES_KEY: str = "prior_critiques"  # unscoped; never written by the engine

#: Prefix for the per-target internal (dotted) context key used to persist the
#: accumulated channel list across loop restarts.  The full key for a target
#: node with id ``"generate"`` is ``"feedback.channel.generate"``.
#: Dotted keys survive ``loop_restart`` (context_updates are intentionally left
#: untouched) but are NOT expanded in prompts, so there is no collision with
#: the injected plain key.
_CHANNEL_KEY_PREFIX: str = "feedback.channel."

#: The UNSCOPED channel key from the initial (pre-review) design.  Never
#: written by the engine (same scoping rationale as ``PRIOR_CRITIQUES_KEY``).
_CHANNEL_KEY: str = "feedback.channel"  # unscoped; never written by the engine


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_feedback_placeholder(
    node: Node,
    prompt: str,
    context: PipelineContext,
) -> str:
    """Guarantee delivery of the accumulated critique channel to the prompt.

    Called by the codergen handler AFTER the raw prompt is assembled and
    BEFORE variable expansion.  ``feedback_from=`` is a delivery contract:
    declaring it must be sufficient on its own.  The
    ``$prior_critiques_<node_id>`` placeholder controls WHERE the history
    appears, never WHETHER it appears.

    Behavior:

    - Node has no ``feedback_from=`` -> prompt returned unchanged.
    - Accumulated channel is empty (e.g. iteration 0, nothing collected
      yet) -> prompt returned unchanged (no empty boilerplate block).
    - Prompt already references ``$prior_critiques_<node_id>`` -> prompt
      returned unchanged (the author placed the history; the normal P7
      expansion substitutes it in place).
    - Otherwise -> a labeled critique-history block carrying the
      placeholder token is appended, so the same P7 expansion path injects
      the iteration-numbered history.  Forgetting the placeholder cannot
      silently sever the feedback loop.

    Args:
        node: The node about to execute.
        prompt: The raw prompt (pre-expansion).
        context: The pipeline context (read-only here).

    Returns:
        The prompt, with the critique-history block appended when needed.
    """
    critic_id = node.attrs.get("feedback_from") if node.attrs else None
    if not critic_id or not str(critic_id).strip() or not prompt:
        return prompt

    injection_key = PRIOR_CRITIQUES_KEY_PREFIX + node.id
    injected = context.get(injection_key)
    if not injected or not str(injected).strip():
        return prompt  # nothing collected yet — leave the prompt untouched

    token = "$" + injection_key
    if token in prompt:
        return prompt  # author controls placement; expansion delivers it

    return (
        f"{prompt}\n\n"
        f"## Prior critique history (engine-accumulated via "
        f'feedback_from="{critic_id}")\n'
        f"Address the most recent critique first.\n\n"
        f"{token}\n"
    )


def collect_and_inject_feedback(
    *,
    graph: Graph,
    node_outcomes: dict[str, Outcome],
    context: PipelineContext,
    iteration_count: int,
    logs_root: str,
) -> None:
    """Collect critic output and inject accumulated feedback into context.

    Called at ``loop_restart`` time, BEFORE ``node_outcomes.clear()``.

    For every node in ``graph`` that declares ``feedback_from=<critic_id>``:

    1. Read the critic node's output from ``node_outcomes`` (the
       just-completed iteration's results).
    2. Truncate to ``MAX_CRITIQUE_CHARS`` if needed.
    3. Append ``"Iteration N critique: <text>"`` to the accumulated channel
       stored in context under ``_CHANNEL_KEY_PREFIX + node_id``
       (e.g. ``feedback.channel.generate`` for target node ``generate``).
       Each target gets its own channel key, preventing leakage between
       multiple generator nodes in the same pipeline.
    4. Trim the channel to ``MAX_CRITIQUES`` entries (oldest-first drop).
    5. Compose the channel into a multi-line string and write it to the
       plain context key ``PRIOR_CRITIQUES_KEY_PREFIX + node_id``
       (e.g. ``prior_critiques_generate``), which expands in prompts as
       ``$prior_critiques_generate``.
    6. Write the accumulated channel to a durable file under ``logs_root``
       so it is observable as a run artifact.

    Nodes without ``feedback_from=`` are untouched.

    Args:
        graph: The parsed pipeline graph (read-only).
        node_outcomes: The current iteration's node outcomes, keyed by node id.
            Must be called BEFORE ``node_outcomes.clear()``.
        context: The pipeline context.  Updated in-place.
        iteration_count: The iteration number AFTER the upcoming increment
            (i.e., the iteration that just completed — the one whose critique
            we are collecting).  Passed as the pre-incremented value from the
            engine's ``loop_restart`` block.
        logs_root: The pipeline run directory root.  The durable channel file
            is written here.
    """
    for node_id, node in graph.nodes.items():
        critic_id = node.attrs.get("feedback_from") if node.attrs else None
        if not critic_id:
            continue  # opt-in only

        critic_id = str(critic_id).strip()
        if not critic_id:
            continue

        # --- Step 1: Read critic output from this iteration ---
        critic_text = _read_critic_output(critic_id, node_outcomes)
        if critic_text is None:
            logger.debug(
                "feedback_from: critic node '%s' not found in node_outcomes "
                "(declared on '%s'); skipping collection for this iteration",
                critic_id,
                node_id,
            )
            continue

        # --- Step 2: Truncate ---
        if len(critic_text) > MAX_CRITIQUE_CHARS:
            critic_text = critic_text[:MAX_CRITIQUE_CHARS] + " […truncated]"

        # --- Step 3: Append to accumulated channel (per-target key) ---
        # The channel is stored as a list of strings under a dotted key
        # scoped to this target node.  Dotted keys survive loop_restart
        # and are not expanded in prompts.  Per-target scoping prevents
        # feedback leakage when multiple generators each declare a critic.
        channel_key = _CHANNEL_KEY_PREFIX + node_id
        raw_channel = context.get(channel_key)
        if isinstance(raw_channel, list):
            channel: list[str] = list(raw_channel)
        else:
            channel = []

        entry = f"Iteration {iteration_count} critique: {critic_text}"
        channel.append(entry)

        # --- Step 4: Trim to MAX_CRITIQUES (oldest-first drop) ---
        if len(channel) > MAX_CRITIQUES:
            channel = channel[-MAX_CRITIQUES:]

        # --- Step 5: Store channel and compose per-target plain injection key ---
        # The injection key is plain (no ".") so it expands in prompts.
        # It is scoped to this target node to prevent cross-target leakage.
        injection_key = PRIOR_CRITIQUES_KEY_PREFIX + node_id
        context.set(channel_key, channel)
        injected = "\n".join(channel)
        context.set(injection_key, injected)

        logger.info(
            "feedback_from: collected iteration %d critique from '%s' -> '%s'; "
            "injection key '%s'; channel depth %d/%d",
            iteration_count,
            critic_id,
            node_id,
            injection_key,
            len(channel),
            MAX_CRITIQUES,
        )

        # --- Step 6: Write durable artifact ---
        _write_channel_artifact(
            logs_root=logs_root,
            target_node_id=node_id,
            critic_node_id=critic_id,
            channel=channel,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_critic_output(
    critic_id: str,
    node_outcomes: dict[str, Outcome],
) -> str | None:
    """Extract the critic node's text output from ``node_outcomes``.

    Resolution order (most informative first):
    1. ``context_updates["tool.output"]`` — full stdout of a tool node.
    2. ``context_updates["tool.last_line"]`` — routing label of a tool node.
    3. ``outcome.notes`` — codergen handler summary.
    4. ``outcome.failure_reason`` — if the critic itself failed (still useful
       as feedback: "the critic failed because …").

    Returns ``None`` if the critic node did not run this iteration.
    """
    outcome = node_outcomes.get(critic_id)
    if outcome is None:
        return None

    # Prefer the full tool output when available
    if outcome.context_updates:
        tool_output = outcome.context_updates.get("tool.output")
        if tool_output and str(tool_output).strip():
            return str(tool_output).strip()

        last_line = outcome.context_updates.get("tool.last_line")
        if last_line and str(last_line).strip():
            return str(last_line).strip()

    # Fall back to notes (codergen summary)
    if outcome.notes and outcome.notes.strip():
        return outcome.notes.strip()

    # Fall back to failure_reason (critic itself failed — still informative)
    if outcome.failure_reason and outcome.failure_reason.strip():
        return f"[critic failed] {outcome.failure_reason.strip()}"

    return ""


def _write_channel_artifact(
    *,
    logs_root: str,
    target_node_id: str,
    critic_node_id: str,
    channel: list[str],
) -> None:
    """Write the accumulated channel to a durable file under ``logs_root``.

    Path: ``<logs_root>/feedback/<target_node_id>.md``

    The file is overwritten on every ``loop_restart`` so it always reflects
    the current accumulated window.  It is the canonical durable artifact
    that the DoD co-location check reads.
    """
    feedback_dir = Path(logs_root) / "feedback"
    try:
        feedback_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = feedback_dir / f"{target_node_id}.md"
        header = (
            f"# Accumulated feedback for node '{target_node_id}'\n"
            f"# Critic: '{critic_node_id}'\n"
            f"# Entries: {len(channel)} (max {MAX_CRITIQUES})\n\n"
        )
        body = "\n\n".join(channel)
        artifact_path.write_text(header + body, encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "feedback_from: could not write channel artifact for '%s': %s",
            target_node_id,
            exc,
        )
