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

Write-time redaction (issue #198)
---------------------------------
Every record passes through :func:`redaction.redact_text` **at the moment it
is serialized**, before a byte reaches disk.  A worker's ``tool:post``
payload is arbitrary tool output — the 2026-08-11 incident was an ``env``
dump carrying a live ``OPENAI_API_KEY`` — and this file is uploaded as CI
run evidence.  Redaction runs on the SERIALIZED LINE rather than by walking
the payload, so nesting depth, container type, and ``default=str`` coercions
cannot route a credential around it (the leak was inside a nested result
string, not at the top level).

The redaction is LOUD, never silent: a scrubbed record carries a top-level
``"redaction": {"count": N, "shapes": [...]}`` block alongside the inline
``[REDACTED:<shape>]`` markers, so a scrubbed dump is distinguishable from a
clean one.  Clean records are byte-identical to what this persister wrote
before — the key appears only when something was actually removed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .redaction import redact_text

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
        line = self._serialize(record, session_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def _serialize(record: dict[str, Any], session_id: Any) -> str:
        """Serialize ONE record to the exact line that will hit disk.

        This is the write seam, and the only place event bytes become file
        bytes -- so it is where redaction belongs (issue #198).  Redacting the
        SERIALIZED text rather than walking the payload is deliberate: the
        2026-08-11 leak was a credential nested inside a ``tool:post`` result
        string, and a walker has to be right about every nesting depth,
        container type and ``default=str`` coercion to catch that.  The
        serialized line has no such blind spots -- whatever is about to be
        written is exactly what is inspected.

        Safe by construction: every pattern's character class stops at quote
        and backslash, so a match can never cross a JSON string boundary or
        eat an escape.  That is then VERIFIED rather than trusted -- the
        redacted line is re-parsed before it is returned, and a line that no
        longer parses is treated as a redaction-machinery failure (below),
        never written.

        FAIL-LOUD, NEVER FALL BACK TO UNSAFE.  If redaction (or the
        serialization it depends on) raises, writing the raw payload would
        resurrect the exact leak this seam exists to close, so the payload is
        WITHHELD: a marker record is written in its place recording that an
        event occurred, which session it belonged to, and that its payload
        could not be attested clean.  Only the exception TYPE is recorded --
        an exception *message* can quote the very bytes that failed to
        redact -- while the full traceback goes to the process log, which is
        not the artifact that gets uploaded.
        """
        try:
            serialized = json.dumps(record, ensure_ascii=False, default=str)
            cleaned, findings = redact_text(serialized)
            if not findings:
                return cleaned
            payload = json.loads(cleaned)  # proves the redaction kept it valid
            payload["redaction"] = {
                "count": len(findings),
                "shapes": sorted(set(findings)),
            }
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            logger.error(
                "session-event redaction FAILED for %s (session %s) -- payload "
                "WITHHELD from events.jsonl rather than written unredacted",
                record.get("event"),
                session_id,
                exc_info=True,
            )
            marker = {
                "event": record.get("event"),
                "timestamp": record.get("timestamp"),
                "data": {"session_id": str(session_id)},
                "redaction": {
                    "error": type(exc).__name__,
                    "payload_withheld": True,
                },
            }
            return json.dumps(marker, ensure_ascii=False)


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
