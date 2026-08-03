"""Worker-session observability seam (EXTENSIONS.md Section 26).

The codergen handler sets ``current_worker_sessions_dir`` to
``<stage_dir>/sessions`` for the duration of each backend call.  Because the
worker's child session runs in-process, within the same task context, any
session-scoped observer (see ``hooks-pipeline-observability``'s session-event
persister) can read this variable while the child session's events are being
emitted and persist them under the node's stage directory:

    <logs_root>/<node_id>/sessions/<session_id>/events.jsonl

A ``ContextVar`` (not a global or an env var) is deliberate: parallel branches
execute nodes in separate asyncio tasks, and context variables are task-local
and inherited by tasks created inside the handler's await chain -- each
worker's events land under the node that spawned it, with no cross-talk.

This module owns only the seam.  The consumer side lives in
``hooks-pipeline-observability`` (mounted into every spawned worker session via
bundle composition -- see ``behaviors/attractor-core.yaml``), which imports
this variable lazily and no-ops when it is unset.  The engine has no runtime
dependency on the hooks module; the hooks module degrades gracefully when this
module is absent.  Either side can be missing and nothing breaks.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Destination directory for persisted worker-session event streams, set by
#: the codergen handler around each backend call.  ``None`` (the default)
#: means "not inside a worker-spawning node execution" -- observers must
#: treat that as "do not persist".
current_worker_sessions_dir: ContextVar[str | None] = ContextVar(
    "current_worker_sessions_dir", default=None
)
