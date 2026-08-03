"""Worker-session event persistence (attractor EXTENSIONS.md Section 26).

Persists the REAL event stream of pipeline worker sessions to durable
``events.jsonl`` files under the pipeline run directory, so that a run is
forensically traceable after the fact: which tools did the worker call, when
did the session start and end, where did it get stuck.

How it plugs in
---------------
This module is mounted by ``hooks-pipeline-observability`` (see
``__init__.mount``), which the attractor-core behavior mounts into the parent
pipeline session AND — via foundation bundle composition in
``PreparedBundle.spawn`` — into every spawned worker (box-node) session.  The
handlers registered here therefore receive each worker session's OWN events,
emitted by its orchestrator and kernel as they happen (e.g. loop-agent's
``tool:pre``/``tool:post`` emissions).

Where it writes
---------------
The destination is task-scoped, not global: the loop-pipeline codergen
handler sets ``current_worker_sessions_dir`` (a ``ContextVar`` in
``amplifier_module_loop_pipeline.worker_observability``) to
``<stage_dir>/sessions`` for the duration of each backend call.  Worker
sessions run in-process within that same task context, so at emit time this
persister resolves:

    <stage_dir>/sessions/<session_id>/events.jsonl

``session_id`` comes from the event payload itself — the amplifier-core
kernel merges ``session_id``/``parent_id`` into every emitted event's data
via ``hooks.set_default_fields`` at session construction.

Graceful degradation (all no-ops, never errors):
- loop-pipeline not importable (module mounted outside a pipeline context),
- ContextVar unset (session not spawned by a codergen node),
- event carries no ``session_id`` (bare registry without default fields).

Records are standard-shaped, append-only JSONL, one event per line:
``{"event": <name>, "timestamp": <utc-iso>, "data": {...}}`` — the same
shape session observers write for ordinary Amplifier sessions, so existing
session tooling can read these files unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Events persisted per worker session.  Curated for forensic value (the
#: questions post-mortems actually ask) rather than raw volume: session
#: lifecycle brackets, the instruction, every tool invocation with its
#: arguments and result, and the orchestrator's completion.  Streaming deltas
#: (``*_delta``, ``content_block:*``) are deliberately excluded — they are
#: UI-cadence noise; the durable response already lands in the node's
#: ``response.md``.
PERSISTED_SESSION_EVENTS: tuple[str, ...] = (
    "session:start",
    "session:resume",
    "session:end",
    "prompt:submit",
    "prompt:complete",
    "tool:pre",
    "tool:post",
    "orchestrator:complete",
)


def _default_sessions_dir_resolver() -> str | None:
    """Resolve the current worker-sessions destination directory.

    Reads loop-pipeline's ``current_worker_sessions_dir`` ContextVar.  Lazy
    import: this hooks module must mount fine in sessions where loop-pipeline
    is not importable (returns None -> persister no-ops).
    """
    try:
        from amplifier_module_loop_pipeline.worker_observability import (
            current_worker_sessions_dir,
        )
    except ImportError:
        return None
    return current_worker_sessions_dir.get()


class SessionEventPersister:
    """Appends session events to ``<sessions_dir>/<session_id>/events.jsonl``.

    ``sessions_dir_resolver`` is injectable for tests; the default resolves
    loop-pipeline's ContextVar seam (see module docstring).
    """

    def __init__(
        self, sessions_dir_resolver: Callable[[], str | None] | None = None
    ) -> None:
        self._resolve_sessions_dir = (
            sessions_dir_resolver or _default_sessions_dir_resolver
        )

    def make_handler(self, event_name: str):
        """Build the hook handler for one event name.

        The registry dispatches ``handler(event, data)`` (same signature the
        StateAggregator handlers use); the name is also bound per-handler so
        the persisted record never depends on the dispatch argument.
        """

        async def handler(
            event: str | None = None, data: dict[str, Any] | None = None
        ) -> None:
            try:
                self._persist(event_name, data or {})
            except Exception:  # never break the session for observability
                logger.debug(
                    "session-event persistence failed for %s", event_name, exc_info=True
                )

        return handler

    def _persist(self, event_name: str, data: dict[str, Any]) -> None:
        sessions_dir = self._resolve_sessions_dir()
        if not sessions_dir:
            return  # not inside a worker-spawning node execution
        session_id = data.get("session_id")
        if not session_id:
            return  # cannot key the stream without a session identity
        session_dir = os.path.join(sessions_dir, str(session_id))
        os.makedirs(session_dir, exist_ok=True)
        record = {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        path = os.path.join(session_dir, "events.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def register_session_event_persister(
    hooks: Any, persister: SessionEventPersister | None = None
) -> SessionEventPersister:
    """Register a persister handler for every event in PERSISTED_SESSION_EVENTS."""
    persister = persister or SessionEventPersister()
    for event_name in PERSISTED_SESSION_EVENTS:
        hooks.register(
            event_name,
            persister.make_handler(event_name),
            name="session-event-persister",
        )
    return persister
