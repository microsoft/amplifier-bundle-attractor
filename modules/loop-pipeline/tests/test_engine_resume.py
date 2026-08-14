"""Engine-level resume — spec §5.3 rules 2–6 (issue #224).

These are in-process unit tests of ``PipelineEngine.resume()``: the restore
set, the single resume-hop edge selection, and the fidelity arm.  The *real*
interrupted-process proof (a separate process, actually SIGKILLed, then a
genuinely separate resume invocation) lives in the pipeline-runner e2e suite —
this file exercises the mechanism, not the process boundary.

The interruption here is a ``BaseException`` raised from the backend, which the
retry ladder does not catch: the engine unwinds mid-node exactly as a crash
would, leaving a checkpoint on disk whose last entry is the previously
completed node and whose ``run_state`` is still ``in_flight``.
"""

import json
from typing import Any

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    load_checkpoint,
    load_checkpoint_for_resume,
    verify_checkpoint_structure,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_NODE_START,
    PIPELINE_RESUME,
    PIPELINE_RESUME_FIDELITY_DEGRADE,
)
from amplifier_module_loop_pipeline.validation import validate_or_raise


class _HardStop(BaseException):
    """Stand-in for the process dying mid-node: not caught by the retry ladder."""


class RecordingBackend:
    """Records which nodes it was asked to run; optionally hard-stops on one."""

    def __init__(
        self,
        outcomes: dict[str, Any] | None = None,
        stop_at: str | None = None,
    ) -> None:
        self._outcomes = outcomes or {}
        self._stop_at = stop_at
        self.calls: list[str] = []

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ):
        if node.id == self._stop_at:
            self.calls.append(node.id)
            raise _HardStop(f"process died during {node.id}")
        self.calls.append(node.id)
        return self._outcomes.get(node.id, "ok")


class MockHooks:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, data))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]

    def get(self, event_name: str) -> list[dict[str, Any]]:
        return [d for n, d in self.events if n == event_name]


LINEAR = """
digraph linear {
    start [shape=Mdiamond]
    a [prompt="A"]
    b [prompt="B"]
    c [prompt="C"]
    exit [shape=Msquare]
    start -> a -> b -> c -> exit
}
"""

BRANCHING = """
digraph branching {
    start [shape=Mdiamond]
    a [prompt="A"]
    left  [prompt="L"]
    right [prompt="R"]
    exit [shape=Msquare]
    start -> a
    a -> left  [condition="context.route=left"]
    a -> right [condition="context.route=right"]
    left -> exit
    right -> exit
}
"""


def _engine(dot: str, backend, logs_root, hooks=None) -> PipelineEngine:
    graph = parse_dot(dot)
    validate_or_raise(graph)
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend, hooks=hooks)),
        logs_root=str(logs_root),
        hooks=hooks,
    )


def _validated_checkpoint(logs_root, dot: str):
    """Walk the real ladder, exactly as the CLI resume path does."""
    cp = load_checkpoint_for_resume(str(logs_root / "checkpoint.json"))
    graph = parse_dot(cp.graph_dot_source)
    verify_checkpoint_structure(cp, graph)
    assert cp.graph_dot_source == dot
    return cp


# ---------------------------------------------------------------------------
# rules 2–5: restore, don't re-execute, route once from the recorded outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_does_not_re_execute_completed_nodes(tmp_path):
    """§5.3 rule 3 — completed work is never revisited, so it cannot re-run."""
    b1 = RecordingBackend(stop_at="b")
    e1 = _engine(LINEAR, b1, tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    assert b1.calls == ["a", "b"]  # died inside b

    cp = load_checkpoint(str(tmp_path / "checkpoint.json"))
    assert cp.current_node == "a"  # last COMPLETED node
    assert cp.run_state == "in_flight"

    b2 = RecordingBackend()
    e2 = _engine(LINEAR, b2, tmp_path)
    outcome = await e2.resume(_validated_checkpoint(tmp_path, LINEAR))

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # b re-executes (it never completed); a does not (it did).
    assert b2.calls == ["b", "c"]
    assert "a" not in b2.calls


@pytest.mark.asyncio
async def test_resume_emits_no_node_start_for_completed_nodes(tmp_path):
    """AC-2's oracle: the resumed run's OWN records show a never ran again."""
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    hooks = MockHooks()
    e2 = _engine(LINEAR, RecordingBackend(), tmp_path, hooks=hooks)
    await e2.resume(_validated_checkpoint(tmp_path, LINEAR))

    started = [d["node_id"] for d in hooks.get(PIPELINE_NODE_START)]
    assert "a" not in started
    assert "start" not in started
    assert started == ["b", "c"]


@pytest.mark.asyncio
async def test_resume_restores_context_and_it_affects_later_nodes(tmp_path):
    """§5.3 rule 2 — a pre-interrupt context_updates value drives post-resume routing."""
    stop = RecordingBackend(
        outcomes={
            "a": Outcome(
                status=StageStatus.SUCCESS,
                context_updates={"context.route": "right"},
                is_explicit=True,
            )
        },
        stop_at="right",
    )
    e1 = _engine(BRANCHING, stop, tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    b2 = RecordingBackend()
    e2 = _engine(BRANCHING, b2, tmp_path)
    await e2.resume(_validated_checkpoint(tmp_path, BRANCHING))

    # The restored context value is what routes a -> right on the resume hop.
    assert e2.context.get("context.route") == "right"
    assert b2.calls == ["right"]
    assert "left" not in b2.calls


@pytest.mark.asyncio
async def test_resume_routing_matches_an_uninterrupted_control_run(tmp_path):
    """AC-1's routing clause, at unit scale: same decision, same inputs."""
    outcomes = {
        "a": Outcome(
            status=StageStatus.SUCCESS,
            context_updates={"context.route": "left"},
            is_explicit=True,
        )
    }
    control_backend = RecordingBackend(outcomes=outcomes)
    control = _engine(BRANCHING, control_backend, tmp_path / "control")
    await control.run()

    e1 = _engine(BRANCHING, RecordingBackend(outcomes=outcomes, stop_at="left"), tmp_path / "run")
    with pytest.raises(_HardStop):
        await e1.run()
    resumed_backend = RecordingBackend(outcomes=outcomes)
    e2 = _engine(BRANCHING, resumed_backend, tmp_path / "run")
    await e2.resume(_validated_checkpoint(tmp_path / "run", BRANCHING))

    # union of executed nodes across the two processes == the control's
    interrupted = ["a", "left"]  # 'left' started but never completed
    assert control_backend.calls == ["a", "left"]
    assert sorted(set(interrupted) | set(resumed_backend.calls)) == sorted(
        set(control_backend.calls)
    )


@pytest.mark.asyncio
async def test_resume_restores_retry_counters_and_execution_index(tmp_path):
    """§5.3 rule 4 — counters restore rather than reset."""
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    pre = load_checkpoint(str(tmp_path / "checkpoint.json"))
    assert pre.engine_state["node_execution_counts"]["a"] == 1

    e2 = _engine(LINEAR, RecordingBackend(), tmp_path)
    cp = _validated_checkpoint(tmp_path, LINEAR)
    await e2.resume(cp)

    # a's pre-crash execution count survived into the resumed engine, so
    # execution_index stays monotonic across the process boundary.
    assert e2._node_execution_counts["a"] == 1
    post = load_checkpoint(str(tmp_path / "checkpoint.json"))
    assert post.node_retries["a"] == 0
    assert post.engine_state["node_execution_counts"]["a"] == 1


@pytest.mark.asyncio
async def test_resume_emits_pipeline_resume_event(tmp_path):
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    hooks = MockHooks()
    e2 = _engine(LINEAR, RecordingBackend(), tmp_path, hooks=hooks)
    await e2.resume(_validated_checkpoint(tmp_path, LINEAR))

    resumes = hooks.get(PIPELINE_RESUME)
    assert len(resumes) == 1
    assert resumes[0]["checkpoint_node"] == "a"
    assert resumes[0]["completed_count"] == 2  # start, a
    assert resumes[0]["fidelity_degrade_armed"] is True


@pytest.mark.asyncio
async def test_resume_records_itself_in_the_manifest(tmp_path):
    """§5.6 — same run directory, same execution; start_time is not rewritten."""
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    manifest_before = json.loads((tmp_path / "manifest.json").read_text())

    e2 = _engine(LINEAR, RecordingBackend(), tmp_path)
    await e2.resume(_validated_checkpoint(tmp_path, LINEAR))

    manifest_after = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest_after["start_time"] == manifest_before["start_time"]
    assert len(manifest_after["resumes"]) == 1
    assert manifest_after["resumes"][0]["from_node"] == "a"


@pytest.mark.asyncio
async def test_resumed_run_is_marked_completed(tmp_path):
    """A resumed run that finishes is not resumable again (ladder rung 4)."""
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    e2 = _engine(LINEAR, RecordingBackend(), tmp_path)
    await e2.resume(_validated_checkpoint(tmp_path, LINEAR))

    final = load_checkpoint(str(tmp_path / "checkpoint.json"))
    assert final.run_state == "completed"


@pytest.mark.asyncio
async def test_resume_from_terminal_checkpoint_re_runs_the_gate_check(tmp_path):
    """A crash during finalization resumes at the exit node — no special case."""
    dot = """
    digraph gated {
        start [shape=Mdiamond]
        work [prompt="W", goal_gate=true]
        exit [shape=Msquare]
        start -> work -> exit
    }
    """
    backend = RecordingBackend(
        outcomes={
            "work": Outcome(
                status=StageStatus.SUCCESS, notes="done", is_explicit=True
            )
        }
    )
    e1 = _engine(dot, backend, tmp_path)
    await e1.run()

    # Rewind the finished run's checkpoint to the terminal-save moment: the
    # engine writes exactly this shape at the exit node, before the gate check.
    path = tmp_path / "checkpoint.json"
    data = json.loads(path.read_text())
    data["run_state"] = "in_flight"
    data["current_node"] = "exit"
    path.write_text(json.dumps(data))

    b2 = RecordingBackend()
    e2 = _engine(dot, b2, tmp_path)
    outcome = await e2.resume(_validated_checkpoint(tmp_path, dot))

    # The gate re-evaluates over RESTORED node_outcomes (is_explicit survived
    # the round trip, so the fail-closed gate stays satisfied) and nothing
    # re-executes.
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert b2.calls == []


# ---------------------------------------------------------------------------
# rule 6: the one-shot fidelity degrade
# ---------------------------------------------------------------------------


FULL_FIDELITY = """
digraph fid {
    graph [default_fidelity="full", default_thread_id="t"]
    start [shape=Mdiamond]
    l1 [prompt="1"]
    l2 [prompt="2"]
    l3 [prompt="3"]
    exit [shape=Msquare]
    start -> l1 -> l2 -> l3 -> exit
}
"""


@pytest.mark.asyncio
async def test_resume_degrades_first_full_hop_once(tmp_path):
    e1 = _engine(FULL_FIDELITY, RecordingBackend(stop_at="l2"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    hooks = MockHooks()
    e2 = _engine(FULL_FIDELITY, RecordingBackend(), tmp_path, hooks=hooks)
    await e2.resume(_validated_checkpoint(tmp_path, FULL_FIDELITY))

    degrades = hooks.get(PIPELINE_RESUME_FIDELITY_DEGRADE)
    assert len(degrades) == 1, "exactly one hop degrades"
    assert degrades[0] == {"node_id": "l2", "from": "full", "to": "summary:high"}


@pytest.mark.asyncio
async def test_degrade_is_recorded_in_the_runs_own_logs(tmp_path):
    """AC-3's durable record: the line lands in the next checkpoint's logs."""
    e1 = _engine(FULL_FIDELITY, RecordingBackend(stop_at="l2"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    e2 = _engine(FULL_FIDELITY, RecordingBackend(), tmp_path)
    await e2.resume(_validated_checkpoint(tmp_path, FULL_FIDELITY))

    logs = load_checkpoint(str(tmp_path / "checkpoint.json")).logs
    degrade_lines = [line for line in logs if "fidelity degraded" in line]
    assert len(degrade_lines) == 1
    assert "l2" in degrade_lines[0]
    assert "summary:high" in degrade_lines[0]


@pytest.mark.asyncio
async def test_cap_never_leaks_into_a_checkpoint_or_a_later_hop(tmp_path):
    """One hop only: the reserved key is cleared before any checkpoint write."""
    e1 = _engine(FULL_FIDELITY, RecordingBackend(stop_at="l2"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    e2 = _engine(FULL_FIDELITY, RecordingBackend(), tmp_path)
    await e2.resume(_validated_checkpoint(tmp_path, FULL_FIDELITY))

    assert e2.context.get("resume.fidelity_cap") is None
    cp = load_checkpoint(str(tmp_path / "checkpoint.json"))
    assert cp.context_snapshot.get("resume.fidelity_cap") in (None, "")


@pytest.mark.asyncio
async def test_no_degrade_when_the_first_hop_is_not_full(tmp_path):
    """Nothing to degrade: a non-full hop already gets a fresh session."""
    dot = FULL_FIDELITY.replace('default_fidelity="full"', 'default_fidelity="compact"')
    e1 = _engine(dot, RecordingBackend(stop_at="l2"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()

    hooks = MockHooks()
    e2 = _engine(dot, RecordingBackend(), tmp_path, hooks=hooks)
    await e2.resume(_validated_checkpoint(tmp_path, dot))

    assert hooks.get(PIPELINE_RESUME_FIDELITY_DEGRADE) == []


@pytest.mark.asyncio
async def test_fresh_run_never_arms_the_degrade(tmp_path):
    hooks = MockHooks()
    engine = _engine(FULL_FIDELITY, RecordingBackend(), tmp_path, hooks=hooks)
    await engine.run()
    assert hooks.get(PIPELINE_RESUME_FIDELITY_DEGRADE) == []
    assert hooks.get(PIPELINE_RESUME) == []
    assert engine._resume_fidelity_armed is False


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_refuses_on_a_branch_clone(tmp_path):
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    cp = _validated_checkpoint(tmp_path, LINEAR)

    e2 = _engine(LINEAR, RecordingBackend(), tmp_path)
    clone = e2.clone_for_branch(context=e2.context.clone())
    with pytest.raises(RuntimeError, match="branch-clone engine"):
        await clone.resume(cp)


@pytest.mark.asyncio
async def test_resume_refuses_an_unvalidated_checkpoint(tmp_path):
    """Defence in depth: bypassing the ladder is a programming error, loudly."""
    e1 = _engine(LINEAR, RecordingBackend(stop_at="b"), tmp_path)
    with pytest.raises(_HardStop):
        await e1.run()
    cp = load_checkpoint(str(tmp_path / "checkpoint.json"))
    cp.current_node = "ghost"

    backend = RecordingBackend()
    e2 = _engine(LINEAR, backend, tmp_path)
    with pytest.raises(RuntimeError, match="unvalidated checkpoint"):
        await e2.resume(cp)
    assert backend.calls == []  # nothing ran — never a silent restart
