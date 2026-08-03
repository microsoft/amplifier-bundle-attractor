"""Tests for worker-session event persistence (EXTENSIONS.md Section 26).

Covers the SessionEventPersister in isolation (injected destination
resolver), its registration in mount(), and the real amplifier-core
HookRegistry integration — including the kernel's default-field merge that
supplies ``session_id`` on every emitted event.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from amplifier_core.hooks import HookRegistry

from amplifier_module_hooks_pipeline_observability import mount
from amplifier_module_hooks_pipeline_observability.session_events import (
    PERSISTED_SESSION_EVENTS,
    SessionEventPersister,
    register_session_event_persister,
)


def _read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Persister unit behavior (injected resolver)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persists_tool_events_with_real_tool_names(tmp_path):
    """tool:pre/tool:post events land in <sessions>/<id>/events.jsonl verbatim."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))

    pre = persister.make_handler("tool:pre")
    post = persister.make_handler("tool:post")
    await pre(
        "tool:pre",
        {
            "session_id": "sid-123",
            "tool_name": "bash",
            "tool_input": {"command": "pytest -q"},
        },
    )
    await post(
        "tool:post",
        {
            "session_id": "sid-123",
            "tool_name": "bash",
            "tool_input": {"command": "pytest -q"},
            "result": "4 passed",
            "call_id": "c1",
        },
    )

    records = _read_events(sessions / "sid-123" / "events.jsonl")
    assert [r["event"] for r in records] == ["tool:pre", "tool:post"]
    assert records[0]["data"]["tool_name"] == "bash"
    assert records[0]["data"]["tool_input"]["command"] == "pytest -q"
    assert records[1]["data"]["result"] == "4 passed"
    assert all("timestamp" in r for r in records)


@pytest.mark.asyncio
async def test_appends_across_events_and_separates_sessions(tmp_path):
    """Events append per session; distinct session ids get distinct dirs."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))

    start = persister.make_handler("session:start")
    end = persister.make_handler("session:end")
    await start("session:start", {"session_id": "sid-a"})
    await end("session:end", {"session_id": "sid-a", "status": "completed"})
    await start("session:start", {"session_id": "sid-b"})

    a = _read_events(sessions / "sid-a" / "events.jsonl")
    b = _read_events(sessions / "sid-b" / "events.jsonl")
    assert [r["event"] for r in a] == ["session:start", "session:end"]
    assert [r["event"] for r in b] == ["session:start"]


@pytest.mark.asyncio
async def test_noop_when_destination_unset(tmp_path):
    """No ContextVar destination -> nothing written anywhere."""
    persister = SessionEventPersister(lambda: None)
    handler = persister.make_handler("tool:pre")
    await handler("tool:pre", {"session_id": "sid-123", "tool_name": "bash"})
    assert list(tmp_path.rglob("events.jsonl")) == []


@pytest.mark.asyncio
async def test_noop_without_session_id(tmp_path):
    """Events without a session identity cannot be keyed -> skipped."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))
    handler = persister.make_handler("tool:pre")
    await handler("tool:pre", {"tool_name": "bash"})
    await handler("tool:pre", None)
    assert not sessions.exists()


@pytest.mark.asyncio
async def test_handler_never_raises(tmp_path):
    """Persistence failures must never break the session."""

    def _boom():
        raise RuntimeError("resolver exploded")

    persister = SessionEventPersister(_boom)
    handler = persister.make_handler("tool:pre")
    await handler("tool:pre", {"session_id": "sid-123"})  # must not raise


@pytest.mark.asyncio
async def test_non_serializable_payload_falls_back_to_str(tmp_path):
    """Arbitrary objects in event data are stringified, not fatal."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))
    handler = persister.make_handler("tool:post")
    await handler("tool:post", {"session_id": "sid-123", "result": object()})
    records = _read_events(sessions / "sid-123" / "events.jsonl")
    assert len(records) == 1
    assert "object object" in records[0]["data"]["result"]


def test_default_resolver_noop_outside_pipeline_context():
    """Default resolver returns None when the loop-pipeline seam is unset.

    (When loop-pipeline is importable, the ContextVar default is None; when
    it is not importable, the lazy import fails -> None either way.)
    """
    assert SessionEventPersister()._resolve_sessions_dir() is None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_registers_session_event_persister():
    """mount() registers a persister handler for every curated session event."""
    hooks_mock = MagicMock()
    coordinator = MagicMock()
    coordinator.get.return_value = hooks_mock
    coordinator.get_capability.return_value = []

    await mount(coordinator)

    registered = [
        (c.args[0], c.kwargs.get("name"))
        for c in hooks_mock.register.call_args_list
        if c.kwargs.get("name") == "session-event-persister"
    ]
    assert {name for name, _ in registered} == set(PERSISTED_SESSION_EVENTS)
    assert {"tool:pre", "tool:post", "session:start", "session:end"} <= {
        name for name, _ in registered
    }


# ---------------------------------------------------------------------------
# Real HookRegistry integration (the kernel default-field merge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_registry_merges_session_id_default_field(tmp_path):
    """With a real HookRegistry + set_default_fields (exactly what
    AmplifierSession does at construction), emitted tool events carry
    session_id and are persisted under that id — the production wiring.
    """
    sessions = tmp_path / "sessions"
    hooks = HookRegistry()
    hooks.set_default_fields(session_id="worker-xyz", parent_id="parent-1")
    register_session_event_persister(
        hooks, SessionEventPersister(lambda: str(sessions))
    )

    await hooks.emit("tool:pre", {"tool_name": "edit_file", "tool_input": {"p": 1}})
    await hooks.emit(
        "tool:post",
        {"tool_name": "edit_file", "tool_input": {"p": 1}, "result": "ok"},
    )
    await hooks.emit("session:end", {"status": "completed"})

    records = _read_events(sessions / "worker-xyz" / "events.jsonl")
    assert [r["event"] for r in records] == ["tool:pre", "tool:post", "session:end"]
    # The forensic question: which tools did the worker call?
    tool_calls = [r["data"]["tool_name"] for r in records if r["event"] == "tool:pre"]
    assert tool_calls == ["edit_file"]
    assert records[0]["data"]["session_id"] == "worker-xyz"


@pytest.mark.asyncio
async def test_started_session_never_yields_empty_events_file(tmp_path):
    """Empty events.jsonl != idle worker.

    ``session:start`` is in the curated persist set, so every session the
    persister observes writes at least its start record -- even a worker that
    calls no tools. A well-formed capture therefore can NEVER produce an
    empty events.jsonl for a session that actually started: an empty (or
    absent) file under a recorded session id signals capture
    failure/corruption, not an idle worker. This test pins that
    distinguishing property.
    """
    sessions = tmp_path / "sessions"
    hooks = HookRegistry()
    hooks.set_default_fields(session_id="worker-idle")
    register_session_event_persister(
        hooks, SessionEventPersister(lambda: str(sessions))
    )

    # The minimal production lifecycle: the kernel emits session:start when
    # the session begins -- this worker then does nothing at all.
    await hooks.emit("session:start", {"parent_id": "parent-1"})
    await hooks.emit("session:end", {"status": "completed"})

    path = sessions / "worker-idle" / "events.jsonl"
    assert path.exists() and path.stat().st_size > 0
    records = _read_events(path)
    assert records[0]["event"] == "session:start"
