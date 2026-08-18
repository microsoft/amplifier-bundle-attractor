"""Tests for worker-session observability (EXTENSIONS.md Section 26).

Two behaviors, both previously absent on the production path:

1. **Full-response durability** — when the backend returns an ``Outcome``
   (the AmplifierBackend spawn path always does), the codergen handler must
   still write the node's full final response to ``<stage_dir>/response.md``
   instead of letting it survive only as ~200-char scraps.

2. **Session-event persistence seam** — the handler exposes
   ``<stage_dir>/sessions`` via the ``current_worker_sessions_dir``
   ContextVar for the duration of the backend call, so the session-event
   persister mounted in the spawned worker session (see
   ``hooks-pipeline-observability``) can append the worker's REAL event
   stream to ``<stage_dir>/sessions/<session_id>/events.jsonl``.

The end-to-end test loads the shipped persister from
``modules/hooks-pipeline-observability`` by file path (the module is not an
install-time dependency of loop-pipeline — the runtime coupling is
deliberately lazy in BOTH directions) and drives the real engine with a
backend that emits tool events through a real amplifier-core HookRegistry,
exactly as a spawned loop-agent worker does.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from amplifier_core.hooks import HookRegistry

from amplifier_module_loop_pipeline.backend import _parse_outcome
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.worker_observability import (
    current_worker_sessions_dir,
)

TAIL_MARKER = "FULL-RESPONSE-TAIL-MARKER"
# Marker sits ~495 chars in — beyond the 200-char truncation horizon.
LONG_RESPONSE = ("worker analysis filler sentence. " * 15) + TAIL_MARKER

SIMPLE_DOT = """
digraph WorkerObs {
    graph [goal="worker observability fixture"]
    start [shape=Mdiamond]
    work  [shape=box, prompt="produce the analysis"]
    done  [shape=Msquare]
    start -> work
    work -> done
}
"""


def _make_engine(backend, logs_root: str) -> PipelineEngine:
    graph = parse_dot(SIMPLE_DOT)
    validate_or_raise(graph)
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs_root,
    )


def _load_shipped_persister_module():
    """Load the REAL shipped session_events.py by file path.

    Loaded UNDER a synthetic parent package rather than as a bare top-level
    module: ``session_events`` imports its sibling ``redaction`` (the
    write-time secret redaction added for issue #198) with a relative
    import, which cannot resolve without a package context.  The synthetic
    parent is a bare namespace whose ``__path__`` points at the real shipped
    directory -- so the sibling that gets imported is the real shipped
    ``redaction.py``, not a stub -- while the module's own ``__init__.py``
    (which pulls in the rest of the hooks module) is deliberately NOT
    executed, preserving this test's point: loop-pipeline does not install
    hooks-pipeline-observability, and the runtime coupling stays lazy in
    both directions.
    """
    pkg_dir = (
        Path(__file__).resolve().parents[2]
        / "hooks-pipeline-observability"
        / "amplifier_module_hooks_pipeline_observability"
    )
    path = pkg_dir / "session_events.py"
    pkg_name = "_shipped_observability_pkg"
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(f"{pkg_name}.session_events", path)
    assert spec is not None and spec.loader is not None, f"missing shipped file: {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Full-response durability on the Outcome path
# ---------------------------------------------------------------------------


class ProductionShapedBackend:
    """Returns an Outcome via _parse_outcome — like the real spawn path."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return _parse_outcome(LONG_RESPONSE)


class OutcomeBackend:
    """Returns a pre-built Outcome (bypasses _parse_outcome)."""

    def __init__(self, outcome: Outcome) -> None:
        self._outcome = outcome

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return self._outcome


@pytest.mark.asyncio
async def test_outcome_path_writes_full_response_md(tmp_path):
    """The production (Outcome-returning) path writes full response.md."""
    engine = _make_engine(ProductionShapedBackend(), str(tmp_path))
    await engine.run()
    response = (tmp_path / "work" / "response.md").read_text()
    assert TAIL_MARKER in response
    assert response == LONG_RESPONSE


@pytest.mark.asyncio
async def test_outcome_without_response_text_skips_response_md(tmp_path):
    """No response_text (e.g. infrastructure outcome) -> no response.md."""
    outcome = Outcome(status=StageStatus.SUCCESS, notes="tool-style outcome")
    engine = _make_engine(OutcomeBackend(outcome), str(tmp_path))
    await engine.run()
    assert not (tmp_path / "work" / "response.md").exists()


def test_parse_outcome_carries_full_text_on_every_path():
    """All _parse_outcome return paths set response_text to the verbatim text."""
    plain = _parse_outcome(LONG_RESPONSE)
    assert plain.response_text == LONG_RESPONSE

    json_verdict = json.dumps({"status": "success", "notes": "done"}) + "\ntrailing"
    explicit = _parse_outcome(json_verdict)
    assert explicit.response_text == json_verdict


@pytest.mark.asyncio
async def test_response_text_not_leaked_into_status_json(tmp_path):
    """response_text is a file-write concern — it must not bloat status.json."""
    engine = _make_engine(ProductionShapedBackend(), str(tmp_path))
    await engine.run()
    status = json.loads((tmp_path / "work" / "status.json").read_text())
    assert TAIL_MARKER not in json.dumps(status)


@pytest.mark.asyncio
async def test_codergen_status_json_carries_session_id(tmp_path):
    """The codergen early-writer records session_id (the forensic join key)."""
    outcome = _parse_outcome(LONG_RESPONSE)
    outcome.session_id = "sid-join-key"
    engine = _make_engine(OutcomeBackend(outcome), str(tmp_path))
    await engine.run()
    status = json.loads((tmp_path / "work" / "status.json").read_text())
    assert status["session_id"] == "sid-join-key"


# ---------------------------------------------------------------------------
# 2. The ContextVar seam
# ---------------------------------------------------------------------------


class SeamProbeBackend:
    """Captures the ContextVar value observed during the backend call."""

    def __init__(self) -> None:
        self.observed: str | None = None

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.observed = current_worker_sessions_dir.get()
        return _parse_outcome("ok")


class RaisingBackend:
    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        raise RuntimeError("backend exploded")


@pytest.mark.asyncio
async def test_sessions_dir_exposed_during_backend_call(tmp_path):
    """During backend.run the seam points at <stage_dir>/sessions; after the
    node completes it is reset to None."""
    backend = SeamProbeBackend()
    engine = _make_engine(backend, str(tmp_path))
    await engine.run()
    assert backend.observed == str(tmp_path / "work" / "sessions")
    assert current_worker_sessions_dir.get() is None


@pytest.mark.asyncio
async def test_sessions_dir_reset_even_when_backend_raises(tmp_path):
    """try/finally: the seam never leaks past a failing backend call."""
    engine = _make_engine(RaisingBackend(), str(tmp_path))
    await engine.run()
    assert current_worker_sessions_dir.get() is None


@pytest.mark.asyncio
async def test_no_synthetic_session_record_is_fabricated(tmp_path):
    """With no persister mounted, the handler writes NO session files — the
    engine does not invent a session ledger after the fact (that was the
    rejected synthetic-record design)."""
    outcome = _parse_outcome(LONG_RESPONSE)
    outcome.session_id = "sid-no-fabrication"
    engine = _make_engine(OutcomeBackend(outcome), str(tmp_path))
    await engine.run()
    assert not (tmp_path / "work" / "sessions").exists()
    assert list(tmp_path.rglob("events.jsonl")) == []


# ---------------------------------------------------------------------------
# 3. Capture-integrity invariant: empty events.jsonl != idle worker
# ---------------------------------------------------------------------------


def _session_capture_anomaly(stage_dir: Path) -> str | None:
    """Distinguish "idle worker" from "capture failure/corruption".

    The shipped persister's curated event set includes ``session:start``, so
    every session it observes writes at least its start record -- even a
    worker that called no tools. A well-formed capture therefore can NEVER
    leave an empty (or absent) events.jsonl for a session id recorded in
    status.json: such a file signals capture failure or corruption, not an
    idle worker. Returns a description of the anomaly, or None when the
    capture is well-formed. (When status.json records no session_id there is
    nothing to cross-check -- that is the documented "no persister mounted"
    degradation, not an anomaly.)
    """
    status = json.loads((stage_dir / "status.json").read_text())
    session_id = status.get("session_id")
    if not session_id:
        return None
    events_path = stage_dir / "sessions" / str(session_id) / "events.jsonl"
    if not events_path.exists():
        return (
            f"session {session_id} recorded in status.json but its "
            "events.jsonl is absent"
        )
    if events_path.stat().st_size == 0:
        return f"events.jsonl for session {session_id} is empty (zero-byte)"
    first = json.loads(events_path.read_text().splitlines()[0])
    if first["event"] != "session:start":
        return f"first persisted record is {first['event']!r}, not session:start"
    return None


@pytest.mark.asyncio
async def test_empty_or_absent_events_file_is_detectably_abnormal(tmp_path):
    """A zero-byte or absent events.jsonl under a recorded session id is
    detectable as capture corruption -- it cannot be mistaken for a worker
    that simply did nothing (an idle worker still persists session:start).
    """
    outcome = _parse_outcome(LONG_RESPONSE)
    outcome.session_id = "sid-capture-lost"
    engine = _make_engine(OutcomeBackend(outcome), str(tmp_path))
    await engine.run()
    work = tmp_path / "work"

    # Absent file for a recorded session id -> anomaly.
    anomaly = _session_capture_anomaly(work)
    assert anomaly is not None and "absent" in anomaly

    # Zero-byte file for a recorded session id -> anomaly.
    events_path = work / "sessions" / "sid-capture-lost" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.touch()
    anomaly = _session_capture_anomaly(work)
    assert anomaly is not None and "zero-byte" in anomaly


# ---------------------------------------------------------------------------
# 4. End-to-end: real engine + shipped persister + real HookRegistry
# ---------------------------------------------------------------------------


class ToolCallingWorkerBackend:
    """Simulates the spawn path: a worker session whose hook registry has the
    shipped persister registered (as it does via bundle composition) emits
    REAL tool events during the backend call, then the backend returns an
    Outcome carrying the worker's session_id — exactly the production shape.
    """

    def __init__(self, hooks: HookRegistry, session_id: str) -> None:
        self._hooks = hooks
        self._session_id = session_id

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        # What AmplifierSession does at construction (amplifier-core
        # session.py): every event carries the session identity.
        self._hooks.set_default_fields(session_id=self._session_id)
        await self._hooks.emit("session:start", {"parent_id": None})
        await self._hooks.emit(
            "tool:pre",
            {"tool_name": "bash", "tool_input": {"command": "pytest -q"}},
        )
        await self._hooks.emit(
            "tool:post",
            {
                "tool_name": "bash",
                "tool_input": {"command": "pytest -q"},
                "result": "14 passed",
                "call_id": "call-1",
            },
        )
        await self._hooks.emit("session:end", {"status": "completed"})
        outcome = _parse_outcome(LONG_RESPONSE)
        outcome.session_id = self._session_id
        return outcome


@pytest.mark.asyncio
async def test_end_to_end_real_events_persisted_and_locatable(tmp_path):
    """The money path: engine run -> worker emits real tool events -> the
    shipped persister writes them under <stage_dir>/sessions/<session_id>/
    events.jsonl -> the file is locatable from the session_id recorded in
    status.json and answers "which tools did the worker call?"."""
    session_events = _load_shipped_persister_module()

    hooks = HookRegistry()
    session_events.register_session_event_persister(hooks)

    session_id = "worker-e2e-1"
    engine = _make_engine(ToolCallingWorkerBackend(hooks, session_id), str(tmp_path))
    await engine.run()

    # Start from ONLY the run dir: read the recorded session_id...
    status = json.loads((tmp_path / "work" / "status.json").read_text())
    recorded_id = status["session_id"]
    assert recorded_id == session_id

    # ...locate the session file by that id...
    events_path = tmp_path / "work" / "sessions" / recorded_id / "events.jsonl"
    assert events_path.exists()
    records = [json.loads(line) for line in events_path.read_text().splitlines()]

    # ...and answer the forensic question with REAL events.
    assert [r["event"] for r in records] == [
        "session:start",
        "tool:pre",
        "tool:post",
        "session:end",
    ]
    tools_called = [r["data"]["tool_name"] for r in records if r["event"] == "tool:pre"]
    assert tools_called == ["bash"]
    assert records[2]["data"]["result"] == "14 passed"

    # The full response is durable beside it.
    assert TAIL_MARKER in (tmp_path / "work" / "response.md").read_text()
