"""``must_write=`` fail-closed artifact contract (EXTENSIONS.md §27).

A node may declare ``must_write=<path>``: completing normally then requires a
fresh, non-trivial artifact at ``<path>``.  The check runs in two places:

1. **Per-attempt, inside the retry ladder** (``retry.execute_with_retry``): a
   completed attempt (SUCCESS / PARTIAL_SUCCESS) that violates the contract
   consumes a retry attempt exactly like a RETRY outcome — the same shape as
   the fail-closed goal-gate verdict retries (EXTENSIONS.md §25).  A no-write
   completion is precisely the flaky-failure class where an in-place retry
   helps: re-invoking the handler gives it another chance to produce the
   artifact.
2. **As the engine's final backstop, after all outcome overrides**
   (``PipelineEngine._check_must_write`` call site): the check runs AFTER
   ``auto_status`` promotion and the ``continue_on_fail`` override, so no
   override can convert a contract violation into a silent success.  This is
   the non-overridable, fail-closed guarantee.

Shared here so both ``engine`` and ``retry`` can use it without a circular
import (``engine`` imports ``retry``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .context import PipelineContext
from .graph import Node
from .outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)


def check_must_write(
    node: Node,
    outcome: Outcome,
    node_start_wall: float,
    context: PipelineContext,
) -> Outcome | None:
    """Artifact contract check for the ``must_write=`` attribute (EXTENSIONS.md §27).

    If the node declares ``must_write=<path>`` and ``outcome`` is a completed
    result (SUCCESS / PARTIAL_SUCCESS) produced without a fresh, non-trivial
    artifact at ``<path>``, this returns a FAIL ``Outcome`` that overrides the
    handler's result.  Nodes without ``must_write=`` are completely untouched.
    FAIL outcomes pass through (no double-wrapping); SKIPPED outcomes pass
    through because the node did not execute — the contract applies only to
    completed executions (EXTENSIONS.md §27).

    **Freshness floor (REQUIRED):** the artifact's mtime must be strictly
    greater than ``node_start_wall`` (a ``time.time()`` wall-clock snapshot
    taken immediately before the node's first attempt ran).  A pre-planted
    file whose mtime predates OR equals the node start time FAILS even if it
    has content.  The equality case is rejected explicitly: an adversary (or a
    coarse-resolution filesystem) could set an artifact's mtime via
    ``os.utime`` to match the recorded start time, bypassing a ``>=`` check.
    Strictly-greater-than closes that boundary.

    **Non-trivial:** the artifact must contain at least one non-whitespace
    byte.  An empty file or a whitespace-only file does not satisfy the
    contract.

    **Path resolution:** the path is interpreted as-is when absolute.
    Relative paths are resolved against ``context.target_dir`` if set,
    falling back to ``os.getcwd()``.  This mirrors the ``requires=``
    convention (see ``PipelineEngine._check_requires``).  Document the
    resolution base in the graph's comments or pipeline invocation so
    consumers know which cwd is the anchor.

    **Interaction with retries / goal_gate / continue_on_fail:**
    - A violation on a completed attempt consumes ``max_retries`` attempts
      in-place (see ``retry.execute_with_retry``); when attempts are
      exhausted, the FAIL routes through the node's failure edges
      (``retry_target``, ``condition="outcome=fail"`` edges, etc.) exactly
      like any other FAIL.  ``allow_partial`` does not soften it.
    - ``is_explicit`` is ``False``: the node never asserted a verdict; the
      engine is forcing a FAIL.  A goal_gate=true node whose must_write
      check fires cannot satisfy its own gate (correct — it produced no
      artifact).
    - ``continue_on_fail=true`` cannot suppress the FAIL: the engine's final
      check runs after the override.

    **Residual (delayed-replant window):** mtime-after-start alone leaves
    a narrow window where an external process writes a content-bearing file
    after node start but before the check runs, and the node's own session
    never wrote.  Session attribution (correlating the write to this node's
    session via ``session_id``) is the preferred closing mechanism but is
    not yet implemented; this residual is documented honestly in the
    EXTENSIONS.md entry.

    Returns:
        A FAIL ``Outcome`` if the contract is violated, ``None`` if the
        contract is satisfied or the node has no ``must_write=`` attribute.
    """
    raw_path = node.attrs.get("must_write") if node.attrs else None
    if not raw_path:
        return None  # opt-in only; nodes without must_write= are untouched

    # Only intercept non-FAIL outcomes — if the handler already failed,
    # leave its failure reason intact (no double-wrapping).
    if outcome.status == StageStatus.FAIL:
        return None

    # SKIPPED — the node did not execute; the artifact contract applies only
    # to completed executions (EXTENSIONS.md §27).  A legitimately-skipped
    # node (runs_on mismatch, failed dependencies, handler-side skip) passes
    # through unconverted.  Note: auto_status=true promotion (SKIPPED →
    # SUCCESS) runs BEFORE the engine's final backstop, so a promoted node
    # IS treated as a completed execution and the contract applies to it —
    # that is exactly the narration-without-artifact class this contract
    # exists to catch.
    if outcome.status == StageStatus.SKIPPED:
        return None

    # Resolve path: absolute paths pass through; relative paths are
    # anchored to context.target_dir (falling back to os.getcwd()).
    raw_path = str(raw_path).strip()
    artifact = Path(raw_path)
    if not artifact.is_absolute():
        base_dir_raw = context.get("context.target_dir")
        base_dir = Path(str(base_dir_raw)) if base_dir_raw else Path(os.getcwd())
        artifact = base_dir / raw_path

    # --- Freshness floor: artifact must exist AND have mtime > node start ---
    try:
        stat = artifact.stat()
    except FileNotFoundError:
        # No artifact at all — the most common failure shape
        logger.warning(
            "Node '%s' must_write= contract violated: artifact absent (%s)",
            node.id,
            artifact,
        )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Node '{node.id}' declared must_write={raw_path!r} but "
                f"no artifact was written (path: {artifact})"
            ),
            notes=(
                f"must_write= contract: the artifact at '{artifact}' does not "
                f"exist. The node must write a non-trivial file at this path "
                f"during its own execution."
            ),
        )

    if stat.st_mtime <= node_start_wall:
        # File exists but was not written strictly after this node started —
        # either planted before node start OR written at the exact same
        # clock tick (equality bypass).  Both are rejected: the contract
        # requires mtime STRICTLY GREATER THAN node_start_wall.
        logger.warning(
            "Node '%s' must_write= freshness floor violated: artifact mtime "
            "%.3f predates node start %.3f (%s)",
            node.id,
            stat.st_mtime,
            node_start_wall,
            artifact,
        )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Node '{node.id}' declared must_write={raw_path!r} but the "
                f"artifact was not written by this execution (mtime "
                f"{stat.st_mtime:.3f} <= node_start {node_start_wall:.3f}; "
                f"planted before or at node start)"
            ),
            notes=(
                f"must_write= freshness floor: the artifact at '{artifact}' "
                f"exists but its mtime does not strictly post-date this node's "
                f"start (mtime must be strictly greater than node_start_wall). "
                f"A pre-planted file — including one whose mtime equals the "
                f"recorded start time — does not satisfy the contract."
            ),
        )

    # --- Non-trivial: artifact must contain at least one non-whitespace byte ---
    try:
        content = artifact.read_bytes()
    except OSError as exc:
        logger.warning(
            "Node '%s' must_write= read error for '%s': %s",
            node.id,
            artifact,
            exc,
        )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Node '{node.id}' declared must_write={raw_path!r} but the "
                f"artifact could not be read: {exc}"
            ),
            notes=f"must_write= contract: unreadable artifact at '{artifact}'.",
        )

    if not content.strip():
        logger.warning(
            "Node '%s' must_write= non-trivial check failed: artifact is "
            "empty or whitespace-only (%s)",
            node.id,
            artifact,
        )
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason=(
                f"Node '{node.id}' declared must_write={raw_path!r} but the "
                f"artifact is empty or whitespace-only (non-trivial content required)"
            ),
            notes=(
                f"must_write= non-trivial check: the artifact at '{artifact}' "
                f"contains no non-whitespace bytes. "
                f"The node must write substantive content."
            ),
        )

    # Contract satisfied: fresh, non-trivial artifact exists
    return None
