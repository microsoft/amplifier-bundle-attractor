"""End-to-end resume: a real process, really killed, really resumed (issue #224).

The interruption here is not simulated. The test launches `attractor run` as a
child process in its own process group, waits until a mid-graph node has
actually started (and therefore the previous node's checkpoint has landed),
SIGKILLs the whole group, and then invokes `attractor resume` as a genuinely
separate process.

Equivalence is asserted against an uninterrupted CONTROL run that this gate
executes itself, at gate runtime — never against a committed golden.

Fixture graphs are deterministic and tool-only (no LLM, no network) except the
rule-6 fidelity test, which drives LLM-shaped nodes through a stub backend
supplied via the stack's public backend-injection surface (a coordinator whose
`session.spawn` capability is the stub, driven through `drive_engine`).
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture graph
# ---------------------------------------------------------------------------
#
#   start -> a -> b -> c -> d -> exit      (all tool nodes)
#
#   a  appends one line to a_runs.log            -> "did a's handler run again?"
#   b  violates its must_write= contract on the first attempt and satisfies it
#      on the second (max_retries=2)             -> a REAL consumed retry to
#                                                   restore (a plain FAIL is
#                                                   never retried by design)
#      and emits context.b_value via parse_json  -> context to restore
#   c  blocks forever while ./BLOCK exists       -> the interruption point
#   d  writes $context.b_value to d_out.txt      -> restored context OBSERVABLY
#                                                   affecting a post-resume node

FIXTURE_DOT = """digraph resume_e2e {
    graph [goal="engine resume e2e fixture"]
    start [shape=Mdiamond]
    a [shape=parallelogram,
       tool_command="echo ran >> a_runs.log; echo a-done"]
    b [shape=parallelogram, max_retries=2, parse_json=true, must_write="b_artifact.txt",
       tool_command="echo ran >> b_runs.log; if [ -f b_try ]; then echo made > b_artifact.txt; printf '{\\"context.b_value\\":\\"beta-42\\"}'; else touch b_try; printf '{}'; fi"]
    c [shape=parallelogram,
       tool_command="echo ran >> c_runs.log; if [ -f BLOCK ]; then touch c_started; sleep 300; fi; echo c-done"]
    d [shape=parallelogram,
       tool_command="echo ran >> d_runs.log; printf '%s' \\"$context.b_value\\" > d_out.txt; echo d-done"]
    exit [shape=Msquare]
    start -> a -> b -> c -> d -> exit
}
"""

CLI = [sys.executable, "-m", "amplifier_module_pipeline_runner"]


def _env():
    env = dict(os.environ)
    # Tool-only fixture: no provider is ever called, but the CLI preflights the
    # key's presence before running anything.
    env.setdefault("ANTHROPIC_API_KEY", "not-used-by-a-tool-only-graph")
    return env


def _cli(*args, cwd=None, timeout=300):
    return subprocess.run(
        [*CLI, *args],
        env=_env(),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wait_for(path: Path, timeout: float = 180.0, poll: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(poll)
    return False


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if ln.strip()]


def _trace(logs: Path) -> list[dict]:
    p = logs / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _normalized_context(logs: Path) -> dict:
    """Checkpoint context minus inherently run-varying values."""
    ctx = dict(json.loads((logs / "checkpoint.json").read_text())["context"])
    # Absolute paths and per-run stdout blobs differ between two runs of the
    # same graph in different directories; they are not what equivalence means.
    for key in ("context.target_dir", "tool.output", "last_response"):
        ctx.pop(key, None)
    return ctx


@pytest.fixture
def workspace(tmp_path):
    """A run: work dir (tool cwd) + logs dir + the fixture graph."""

    def _make(name: str, *, blocked: bool):
        work = tmp_path / name / "work"
        logs = tmp_path / name / "logs"
        work.mkdir(parents=True)
        logs.mkdir(parents=True)
        dot = tmp_path / name / "fixture.dot"
        dot.write_text(FIXTURE_DOT)
        if blocked:
            (work / "BLOCK").write_text("")
        return dot, work, logs

    return _make


# ---------------------------------------------------------------------------
# AC-1 / AC-2: interrupt for real, resume for real, equal the control
# ---------------------------------------------------------------------------


@pytest.fixture
def interrupted_run(workspace):
    """Start a run, wait until node c is actually executing, SIGKILL the group."""
    dot, work, logs = workspace("interrupted", blocked=True)

    proc = subprocess.Popen(
        [*CLI, "run", str(dot), "--logs-root", str(logs), "--cwd", str(work)],
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group, so killpg hits the tree
    )
    try:
        assert _wait_for(work / "c_started"), (
            "node c never started; the run did not reach the interruption point"
        )
        # b's checkpoint necessarily landed before c was dispatched.
        checkpoint = json.loads((logs / "checkpoint.json").read_text())
        assert checkpoint["current_node"] == "b", checkpoint["current_node"]
        assert checkpoint["run_state"] == "in_flight"

        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=60)

    assert proc.returncode != 0, "the process was killed, not finished"
    (work / "BLOCK").unlink()  # the block is what the crash was standing in for
    return dot, work, logs, _trace(logs), checkpoint


@pytest.fixture
def control_run(workspace):
    """An uninterrupted run of the same graph bytes, executed at gate runtime."""
    dot, work, logs = workspace("control", blocked=False)
    result = _cli("run", str(dot), "--logs-root", str(logs), "--cwd", str(work))
    assert result.returncode == 0, result.stderr
    return dot, work, logs


def test_ac1_resumed_run_completes_and_matches_the_control(
    interrupted_run, control_run
):
    dot, work, logs, interrupted_trace, pre_checkpoint = interrupted_run
    _, control_work, control_logs = control_run

    resumed = _cli("resume", str(logs), "--cwd", str(work))
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
    assert '"status": "success"' in resumed.stdout

    # -- final outcome status -------------------------------------------------
    final = json.loads(
        [ln for ln in resumed.stdout.splitlines() if ln.startswith("{")][-1]
    )
    assert final["status"] == "success"

    # -- final context equivalence -------------------------------------------
    assert _normalized_context(logs) == _normalized_context(control_logs)

    # -- artifact equivalence -------------------------------------------------
    # Every artifact of a node the checkpoint recorded as COMPLETE, plus the
    # final output, is byte-identical to the control's.
    for artifact in (
        "a_runs.log",
        "b_runs.log",
        "b_artifact.txt",
        "d_runs.log",
        "d_out.txt",
    ):
        assert (work / artifact).read_text() == (control_work / artifact).read_text(), (
            f"{artifact} differs between the resumed run and the control"
        )

    # c is the one node that legitimately ran twice: the crash hit it mid-flight,
    # so it was never recorded as complete and re-executes from its start. That
    # is the documented contract (the engine makes no idempotency promise for a
    # node it never recorded as done — that is precisely where the graph-owned
    # file-guard pattern of examples/pipelines/12-graph-resume.dot is the
    # complementary tool). Asserted explicitly rather than excused as noise.
    assert len(_lines(work / "c_runs.log")) == 2
    assert len(_lines(control_work / "c_runs.log")) == 1

    # -- executed-node union == the control's executed-node sequence ----------
    full_trace = _trace(logs)
    resumed_trace = full_trace[len(interrupted_trace):]
    interrupted_nodes = [r["node_id"] for r in interrupted_trace]
    resumed_nodes = [r["node_id"] for r in resumed_trace]
    control_nodes = [r["node_id"] for r in _trace(control_logs)]

    assert interrupted_nodes + resumed_nodes == control_nodes, (
        f"interrupted={interrupted_nodes} + resumed={resumed_nodes} "
        f"!= control={control_nodes}"
    )
    # ... and the resumed traversal routed off the resume point exactly as the
    # uninterrupted one did.
    assert resumed_nodes == control_nodes[len(interrupted_nodes):]


def test_ac2_completed_nodes_are_not_re_executed(interrupted_run):
    dot, work, logs, interrupted_trace, _ = interrupted_run

    a_before = _lines(work / "a_runs.log")
    b_before = _lines(work / "b_runs.log")
    assert len(a_before) == 1
    assert len(b_before) == 2, "b consumed one retry before succeeding"

    resumed = _cli("resume", str(logs), "--cwd", str(work))
    assert resumed.returncode == 0, resumed.stderr

    # The handlers' own side effects are the oracle: they did not run again.
    assert _lines(work / "a_runs.log") == a_before
    assert _lines(work / "b_runs.log") == b_before

    # ... corroborated by the run's own records: no trace record for a or b
    # was appended by the resumed process.
    resumed_trace = _trace(logs)[len(interrupted_trace):]
    resumed_nodes = [r["node_id"] for r in resumed_trace]
    assert "a" not in resumed_nodes
    assert "b" not in resumed_nodes
    assert "start" not in resumed_nodes
    assert resumed_nodes == ["c", "d"]


def test_ac2_restored_context_reaches_and_affects_a_post_resume_node(
    interrupted_run,
):
    dot, work, logs, _, pre_checkpoint = interrupted_run

    # b wrote it before the interruption; the checkpoint carries it.
    assert pre_checkpoint["context"]["context.b_value"] == "beta-42"
    assert not (work / "d_out.txt").exists()

    resumed = _cli("resume", str(logs), "--cwd", str(work))
    assert resumed.returncode == 0, resumed.stderr

    # d ran only in the resumed process, and its output is the restored value —
    # visibility AND behavioral effect, in one artifact.
    assert (work / "d_out.txt").read_text() == "beta-42"


def test_ac2_retry_counters_restore_rather_than_reset(interrupted_run):
    dot, work, logs, _, pre_checkpoint = interrupted_run

    # §5.3 rule 4: b consumed one retry before the crash.
    assert pre_checkpoint["node_retries"]["b"] == 1
    pre_counts = pre_checkpoint["engine_state"]["node_execution_counts"]

    resumed = _cli("resume", str(logs), "--cwd", str(work))
    assert resumed.returncode == 0, resumed.stderr

    post = json.loads((logs / "checkpoint.json").read_text())
    # Restored, not reset: the resumed run carries b's consumed retry forward.
    assert post["node_retries"]["b"] == 1
    # execution_index continuity — a's and b's pre-crash counts survived, so
    # per-node ordinals stay monotonic across the process boundary.
    for node_id, count in pre_counts.items():
        assert post["engine_state"]["node_execution_counts"][node_id] >= count
    assert post["engine_state"]["node_execution_counts"]["a"] == pre_counts["a"]
    assert post["engine_state"]["node_execution_counts"]["b"] == pre_counts["b"]


def test_resume_records_itself_and_finishes_the_run(interrupted_run):
    dot, work, logs, _, _ = interrupted_run
    manifest_before = json.loads((logs / "manifest.json").read_text())

    assert _cli("resume", str(logs), "--cwd", str(work)).returncode == 0

    manifest = json.loads((logs / "manifest.json").read_text())
    assert manifest["start_time"] == manifest_before["start_time"]
    assert [r["from_node"] for r in manifest["resumes"]] == ["b"]

    final = json.loads((logs / "checkpoint.json").read_text())
    assert final["run_state"] == "completed"


def test_ac6_resuming_a_finished_run_is_refused(interrupted_run):
    """The liveness rung, proven against a genuinely finished run."""
    dot, work, logs, _, _ = interrupted_run
    assert _cli("resume", str(logs), "--cwd", str(work)).returncode == 0

    a_before = _lines(work / "a_runs.log")
    again = _cli("resume", str(logs), "--cwd", str(work))
    assert again.returncode != 0
    assert "already completed" in again.stderr
    # Nothing re-ran: not a silent restart from the start node.
    assert _lines(work / "a_runs.log") == a_before


# ---------------------------------------------------------------------------
# AC-3: §5.3 rule 6 across a real process boundary, with a stub backend
# ---------------------------------------------------------------------------

FIDELITY_DOT = """digraph resume_fidelity {
    graph [goal="fidelity resume fixture", default_fidelity="full", default_thread_id="t"]
    start [shape=Mdiamond]
    l1 [prompt="one"]
    l2 [prompt="two"]
    l3 [prompt="three"]
    exit [shape=Msquare]
    start -> l1 -> l2 -> l3 -> exit
}
"""

# A driver that reaches the engine through the stack's PUBLIC backend-injection
# surface: drive_engine takes a caller-built coordinator, and AmplifierBackend
# takes its spawn capability from it. No private symbols, no monkeypatching.
DRIVER = '''
import asyncio, json, os, sys, time
from pathlib import Path

from amplifier_module_pipeline_runner.runner import drive_engine

MODE, DOT_PATH, LOGS, WORK = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
work = Path(WORK)


class StubSession:
    config = {}


class StubCoordinator:
    """Coordinator whose session.spawn is a stub: no LLM, no network."""

    hooks = None
    session = StubSession()
    config = {
        "agents": {
            "attractor-agent-anthropic": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            }
        }
    }

    def get_capability(self, name):
        return self._spawn if name == "session.spawn" else None

    async def _spawn(self, **kwargs):
        instruction = kwargs.get("instruction", "")
        # Record what each node was actually asked, so the capped hop's
        # preamble is inspectable from outside the process.
        node = "l1" if "one" in instruction else "l2" if "two" in instruction else "l3"
        (work / (node + ".instruction")).write_text(instruction)
        if node == "l2" and (work / "BLOCK").exists():
            (work / "l2_started").touch()
            time.sleep(300)
        return {"output": json.dumps({"status": "success"}), "session_id": node}


class FileHooks:
    """Durable event record, since a stub bypasses any backend-side logging."""

    async def emit(self, name, data):
        with open(work / "events.jsonl", "a") as f:
            f.write(json.dumps({"event": name, "data": data}) + "\\n")


async def main():
    dot_source = Path(DOT_PATH).read_text()
    if MODE == "run":
        outcome = await drive_engine(
            dot_source, StubCoordinator(), cwd=WORK, logs_root=LOGS,
            hooks=FileHooks(), transform=True,
        )
    else:
        from amplifier_module_loop_pipeline.checkpoint import (
            load_checkpoint_for_resume,
        )

        cp = load_checkpoint_for_resume(str(Path(LOGS) / "checkpoint.json"))
        outcome = await drive_engine(
            cp.graph_dot_source, StubCoordinator(), cwd=WORK, logs_root=LOGS,
            hooks=FileHooks(), transform=True, resume_checkpoint=cp,
        )
    print(outcome.status.value)


asyncio.run(main())
'''


def _events(work: Path) -> list[dict]:
    p = work / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_ac3_fidelity_full_degrades_for_exactly_one_hop_across_a_kill(tmp_path):
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    work.mkdir()
    logs.mkdir()
    dot = tmp_path / "fidelity.dot"
    dot.write_text(FIDELITY_DOT)
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    (work / "BLOCK").write_text("")

    proc = subprocess.Popen(
        [sys.executable, str(driver), "run", str(dot), str(logs), str(work)],
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert _wait_for(work / "l2_started"), "l2 never started"
        checkpoint = json.loads((logs / "checkpoint.json").read_text())
        assert checkpoint["current_node"] == "l1"
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=60)

    (work / "BLOCK").unlink()
    (work / "events.jsonl").unlink(missing_ok=True)  # keep only resumed records

    resumed = subprocess.run(
        [sys.executable, str(driver), "resume", str(dot), str(logs), str(work)],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    # (a) it did not crash
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
    assert "success" in resumed.stdout

    # (b) the run's own records show the degraded treatment for exactly l2
    degrades = [
        e["data"]
        for e in _events(work)
        if e["event"] == "pipeline:resume_fidelity_degrade"
    ]
    assert len(degrades) == 1, degrades
    assert degrades[0] == {"node_id": "l2", "from": "full", "to": "summary:high"}

    resume_events = [e for e in _events(work) if e["event"] == "pipeline:resume"]
    assert len(resume_events) == 1
    assert resume_events[0]["data"]["fidelity_degrade_armed"] is True

    # (c) ... and durably, in the run directory itself
    logs_lines = json.loads((logs / "checkpoint.json").read_text())["logs"]
    degrade_lines = [ln for ln in logs_lines if "fidelity degraded" in ln]
    assert len(degrade_lines) == 1
    assert "l2" in degrade_lines[0] and "summary:high" in degrade_lines[0]

    # (d) exactly one hop: l2 got a summary preamble, l3 was free to be `full`
    #     again and got the bare prompt.
    assert "Goal:" in (work / "l2.instruction").read_text()
    assert (work / "l3.instruction").read_text().strip() == "three"
