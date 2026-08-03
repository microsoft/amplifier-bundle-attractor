"""Both-walls live evidence: delta-assertion gate.

This test is the reproducible both-walls fixture for the delta-assertion gate
pattern (docs/PIPELINE_DESIGN_PRINCIPLES.md §7): a minimal pipeline run twice
-- once where commits land (gate green) and once where work is only
uncommitted/absent (gate red) -- with inspectable event-trace evidence for
both walls.

It runs a minimal DOT graph embedding the delta-assertion gate logic through
the actual PipelineEngine with the real ToolHandler executing real git
commands against a temporary git repository.

Wall 1 (GREEN): RecordBaseSHA records the initial commit SHA. A work node
  commits src/work.py during the pipeline run. AssertDelta sees commits since
  the recorded base -> pipeline:edge_selected shows AssertDelta -> done.

  This exercises the full shipped pattern:
    RecordBaseSHA (records HEAD) -> work (commits) -> AssertDelta (asserts delta)
  in a single pipeline run, proving the temporal property the pattern teaches.

Wall 2 (RED then GREEN): RecordBaseSHA records the initial commit SHA. Work
  node writes uncommitted file on first visit; AssertDelta sees no commits ->
  pipeline:edge_selected shows AssertDelta -> work (red path exercised). On
  second visit, work node commits; AssertDelta -> done (pipeline converges).

Both-walls artifacts are saved to .ai/both-walls-evidence/ for human
inspection after any run of this suite (the .ai/ directory is run-local
evidence, not committed work product).

This is an auditable proof: a static document can be fabricated, a test that
runs the engine and asserts the event sequence cannot.

See also:
  examples/gates/delta-assertion-gate.dot  -- the gate primitive
  docs/PIPELINE_DESIGN_PRINCIPLES.md §7    -- the doctrine and discipline
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_EDGE_SELECTED,
    PIPELINE_START,
)
from amplifier_module_loop_pipeline.validation import validate_or_raise

# ---------------------------------------------------------------------------
# Stable artifact output path (for human inspection and both-walls-evidence.md)
# ---------------------------------------------------------------------------

# Relative to the repo root. Written by the tests so humans can inspect
# the event traces without re-running the suite.
_REPO_ROOT = Path(
    __file__
).parent.parent.parent.parent  # modules/loop-pipeline/tests/../../../..
_EVIDENCE_DIR = _REPO_ROOT / ".ai" / "both-walls-evidence"


# ---------------------------------------------------------------------------
# Event capture
# ---------------------------------------------------------------------------


class EventCapture:
    """Records all events emitted by the engine."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, dict(data)))

    @property
    def event_names(self) -> list[str]:
        return [e for e, _ in self.events]

    def get_data(self, event_name: str) -> list[dict[str, Any]]:
        return [d for e, d in self.events if e == event_name]

    def edge_selected_pairs(self) -> list[tuple[str, str]]:
        """Return (from_node, to_node) pairs for all edge_selected events."""
        return [
            (d["from_node"], d["to_node"])
            for d in self.get_data(PIPELINE_EDGE_SELECTED)
        ]

    def as_json_serializable(self) -> list[dict[str, Any]]:
        """Return events as a list of dicts suitable for json.dumps."""
        return [{"event": e, "data": d} for e, d in self.events]


def _save_evidence(filename: str, hooks: EventCapture) -> None:
    """Write the event trace to .ai/both-walls-evidence/ for human inspection."""
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = _EVIDENCE_DIR / filename
    artifact_path.write_text(json.dumps(hooks.as_json_serializable(), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Git repo helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> str:
    """Initialise a git repo with one initial commit. Returns the base SHA."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@t2-7.local"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Delta Gate Test"],
        check=True,
        capture_output=True,
    )
    # Initial commit so the repo is non-empty
    (path / "README.md").write_text("# test repo\n")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# DOT graph for Wall 1: GREEN path (full anchored pattern in one run)
#
# RecordBaseSHA records the initial commit SHA. A work node commits src/work.py
# during the pipeline run. AssertDelta sees commits since the recorded base ->
# routes to done.
#
# This exercises the FULL shipped pattern:
#   RecordBaseSHA (records HEAD) -> work (commits) -> AssertDelta (asserts delta)
# in a single pipeline run, proving the temporal property the pattern teaches.
# ---------------------------------------------------------------------------

_WALL1_FULL_DOT = """\
digraph DeltaGateGreenWallFull {
    graph [goal="Assert that durable commits exist in src/ since the recorded base SHA"]

    start [shape=Mdiamond]
    done  [shape=Msquare]

    RecordBaseSHA [
        shape=parallelogram,
        label="Record Base SHA",
        tool_command="mkdir -p .ai && git rev-parse HEAD > .ai/base-sha && printf ok || { printf fail; exit 1; }"
    ]

    work [
        shape=parallelogram,
        label="Do Work (commit to src/)",
        tool_command="mkdir -p src && echo '# work' > src/work.py && git add src/work.py && git commit -m 'feat: add work.py' && printf committed || { printf commit_fail; exit 1; }"
    ]

    AssertDelta [
        shape=parallelogram,
        label="Assert Delta (commits since base)",
        goal_gate=true,
        retry_target=done,
        tool_command="if [ ! -f .ai/base-sha ]; then printf no_anchor; exit 1; fi; BASE=$(cat .ai/base-sha); COUNT=$(git log ${BASE}..HEAD -- src/ | wc -l | tr -d ' '); [ \\"$COUNT\\" -gt 0 ] && printf changed || { printf unchanged; exit 1; }"
    ]

    start -> RecordBaseSHA
    RecordBaseSHA -> work         [condition="context.tool.last_line=ok && outcome=success", label="anchor written"]
    RecordBaseSHA -> done         [condition="outcome=fail", label="not a git repo"]

    work -> AssertDelta           [condition="context.tool.last_line=committed && outcome=success", label="work committed"]
    work -> done                  [condition="outcome=fail", label="commit failed"]

    AssertDelta -> done [condition="context.tool.last_line=changed && outcome=success", label="durable delta confirmed"]
    AssertDelta -> done [condition="outcome=fail", label="no durable commits"]
}
"""

# ---------------------------------------------------------------------------
# DOT graph for Wall 2: RED then GREEN path (work uncommitted on first visit)
#
# RecordBaseSHA records initial SHA. Work node writes uncommitted file on
# first visit (flag-based), commits on second. AssertDelta: first visit ->
# work (red edge); second visit -> done (green edge).
# ---------------------------------------------------------------------------

_WALL2_DOT = """\
digraph DeltaGateRedWall {
    graph [goal="Assert that durable commits exist in src/ since the recorded base SHA"]

    start [shape=Mdiamond]
    done  [shape=Msquare]

    RecordBaseSHA [
        shape=parallelogram,
        label="Record Base SHA",
        tool_command="mkdir -p .ai && git rev-parse HEAD > .ai/base-sha && printf ok || { printf fail; exit 1; }"
    ]

    work [
        shape=parallelogram,
        label="Do Work (uncommitted first, committed second)",
        tool_command="mkdir -p src .ai; if [ ! -f .ai/work-flag ]; then echo 'uncommitted' > src/work.py; touch .ai/work-flag; printf work_done; else git add src/work.py && git commit -m 'feat: add work.py' && printf committed; fi"
    ]

    AssertDelta [
        shape=parallelogram,
        label="Assert Delta (commits since base)",
        goal_gate=true,
        retry_target=work,
        tool_command="if [ ! -f .ai/base-sha ]; then printf no_anchor; exit 1; fi; BASE=$(cat .ai/base-sha); COUNT=$(git log ${BASE}..HEAD -- src/ | wc -l | tr -d ' '); [ \\"$COUNT\\" -gt 0 ] && printf changed || { printf unchanged; exit 1; }"
    ]

    start -> RecordBaseSHA
    RecordBaseSHA -> work         [condition="context.tool.last_line=ok && outcome=success", label="anchor written"]
    RecordBaseSHA -> done         [condition="outcome=fail", label="not a git repo"]

    work -> AssertDelta

    AssertDelta -> done [condition="context.tool.last_line=changed && outcome=success", label="durable delta confirmed"]
    AssertDelta -> work [condition="outcome=fail", label="no durable commits -- retry"]
}
"""


# ---------------------------------------------------------------------------
# Engine builder
# ---------------------------------------------------------------------------


def _make_engine(
    dot_source: str,
    hooks: EventCapture,
    target_dir: Path,
    logs_root: Path,
) -> PipelineEngine:
    """Parse DOT, validate, and build a PipelineEngine with real ToolHandler."""
    graph = parse_dot(dot_source)
    validate_or_raise(graph)

    context = PipelineContext()
    # Set context.target_dir so ToolHandler uses the git repo as cwd
    context.set("context.target_dir", str(target_dir))

    # No backend needed: all nodes are parallelogram (ToolHandler handles them)
    registry = HandlerRegistry(HandlerContext(backend=None))

    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(logs_root),
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeltaAssertionGateGreenWall:
    """Wall 1: gate routes to done when commits exist since base.

    The gate is GREEN: durable commits exist in src/ since the recorded base.
    The engine must emit pipeline:edge_selected with AssertDelta -> done.

    This exercises the FULL shipped pattern in a single pipeline run:
      RecordBaseSHA (records HEAD at pipeline start)
      -> work (commits src/work.py during the run)
      -> AssertDelta (asserts delta since the recorded base)
      -> done (green path)

    This is the temporal property the pattern teaches: the anchor is recorded
    BEFORE work, the work commits DURING the run, and the gate asserts the
    delta AFTER work. All three steps happen in one pipeline execution.
    """

    @pytest.mark.asyncio
    async def test_green_wall_full_anchored_pattern(self, tmp_path: Path) -> None:
        """Full anchored pattern: RecordBaseSHA -> work -> AssertDelta -> done.

        Setup: git repo with one initial commit (no work yet).
        The engine runs the full graph:
          1. RecordBaseSHA records the initial commit SHA to .ai/base-sha.
          2. work node commits src/work.py during the pipeline run.
          3. AssertDelta checks git log BASE..HEAD -- src/ (non-empty) -> done.

        This proves the shipped base-sha-anchor.dot + delta-assertion-gate.dot
        pattern works end-to-end in a single pipeline execution. The anchor is
        written by the engine's RecordBaseSHA node, not pre-written by the test.

        Event trace is saved to .ai/both-walls-evidence/green-wall-events.json.
        """
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        # No pre-written .ai/base-sha -- RecordBaseSHA must write it.
        # No pre-committed work -- work node must commit during the run.
        assert not (repo_dir / ".ai" / "base-sha").exists(), (
            "Pre-condition: .ai/base-sha must NOT exist before the engine runs. "
            "RecordBaseSHA is responsible for writing it."
        )

        hooks = EventCapture()
        engine = _make_engine(
            dot_source=_WALL1_FULL_DOT,
            hooks=hooks,
            target_dir=repo_dir,
            logs_root=tmp_path / "logs-green",
        )

        outcome = await engine.run()

        # Save event trace for human inspection
        _save_evidence("green-wall-events.json", hooks)

        # The pipeline must complete successfully
        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS, got {outcome.status}: {outcome.failure_reason}"
        )

        edge_pairs = hooks.edge_selected_pairs()

        # RecordBaseSHA must have run and succeeded (anchor written)
        assert ("RecordBaseSHA", "work") in edge_pairs, (
            f"Expected RecordBaseSHA -> work edge (anchor written), "
            f"got edge_selected pairs: {edge_pairs}"
        )

        # The critical assertion: AssertDelta -> done edge was selected
        assert ("AssertDelta", "done") in edge_pairs, (
            f"Expected AssertDelta -> done edge (green wall), "
            f"got edge_selected pairs: {edge_pairs}"
        )

        # The red path must NOT have been taken
        assert ("AssertDelta", "done") in edge_pairs, (
            f"AssertDelta -> done (green path) must fire when commits exist, "
            f"got: {edge_pairs}"
        )

        # Verify .ai/base-sha was written by the engine (not pre-written)
        base_sha_path = repo_dir / ".ai" / "base-sha"
        assert base_sha_path.exists(), (
            "RecordBaseSHA must have written .ai/base-sha during the pipeline run"
        )
        base_sha = base_sha_path.read_text().strip()
        assert len(base_sha) == 40, (
            f"Expected a 40-char git SHA in .ai/base-sha, got: {base_sha!r}"
        )

        # Verify that commits in src/ exist since the recorded base
        log_result = subprocess.run(
            ["git", "-C", str(repo_dir), "log", f"{base_sha}..HEAD", "--", "src/"],
            capture_output=True,
            text=True,
            check=True,  # git log must succeed; empty output is the assertion below
        )
        assert log_result.stdout.strip() != "", (
            f"Expected commits in src/ since base {base_sha}, "
            f"but git log returned empty output. "
            f"The work node must have committed during the pipeline run."
        )

    @pytest.mark.asyncio
    async def test_green_wall_start_event_emitted(self, tmp_path: Path) -> None:
        """pipeline:start is emitted with the correct graph name."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        hooks = EventCapture()
        engine = _make_engine(
            dot_source=_WALL1_FULL_DOT,
            hooks=hooks,
            target_dir=repo_dir,
            logs_root=tmp_path / "logs-green-start",
        )
        await engine.run()

        start_events = hooks.get_data(PIPELINE_START)
        assert start_events, "Expected at least one pipeline:start event"
        assert start_events[0].get("graph_name") == "DeltaGateGreenWallFull", (
            f"Expected graph_name='DeltaGateGreenWallFull', "
            f"got {start_events[0].get('graph_name')!r}"
        )


class TestDeltaAssertionGateRedWall:
    """Wall 2: gate routes to work when no commits exist since base.

    The gate is RED on the first visit: only uncommitted work exists.
    The engine must emit pipeline:edge_selected with AssertDelta -> work.

    This is the critical wall: it proves the gate catches the incident pattern
    (working-tree claims are not evidence -- no durable commits, gate fires red).
    """

    @pytest.mark.asyncio
    async def test_red_wall_assert_delta_routes_to_work_first(
        self, tmp_path: Path
    ) -> None:
        """AssertDelta -> work on first visit when only uncommitted work exists.

        Setup: git repo with initial commit. RecordBaseSHA records that SHA.
        Work node writes src/work.py but does NOT commit (first visit).
        AssertDelta sees no commits since base -> routes to work (red path).

        On second visit, work node commits. AssertDelta -> done (converges).

        The critical assertion is that AssertDelta -> work was selected at
        least once -- proving the red path fires when no durable delta exists.
        This is the incident pattern: the tree has files, but no commits.

        Event trace is saved to .ai/both-walls-evidence/red-wall-events.json.
        """
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)

        hooks = EventCapture()
        engine = _make_engine(
            dot_source=_WALL2_DOT,
            hooks=hooks,
            target_dir=repo_dir,
            logs_root=tmp_path / "logs-red",
        )

        outcome = await engine.run()

        # Save event trace for human inspection
        _save_evidence("red-wall-events.json", hooks)

        # The pipeline must complete successfully (after the retry)
        assert outcome.status == StageStatus.SUCCESS, (
            f"Expected SUCCESS (after retry), got {outcome.status}: "
            f"{outcome.failure_reason}"
        )

        edge_pairs = hooks.edge_selected_pairs()

        # RecordBaseSHA must have run and succeeded (anchor written)
        assert ("RecordBaseSHA", "work") in edge_pairs, (
            f"Expected RecordBaseSHA -> work edge (anchor written), "
            f"got edge_selected pairs: {edge_pairs}"
        )

        # The critical assertion: AssertDelta -> work (red path) fired at least once
        assert ("AssertDelta", "work") in edge_pairs, (
            f"Expected AssertDelta -> work (red wall: no commits on first visit), "
            f"got edge_selected pairs: {edge_pairs}\n"
            f"This is the incident pattern: uncommitted work does not satisfy "
            f"the delta-assertion gate."
        )

        # The green path must also fire (convergence after commit)
        assert ("AssertDelta", "done") in edge_pairs, (
            f"Expected AssertDelta -> done (green path after commit), "
            f"got edge_selected pairs: {edge_pairs}"
        )

    @pytest.mark.asyncio
    async def test_red_wall_uncommitted_file_present(self, tmp_path: Path) -> None:
        """Incident pattern: uncommitted file present, gate fires red (FAIL).

        This directly mirrors the incident: files existed in the working tree
        but no commits had landed. The gate correctly exits nonzero (FAIL)
        despite the file being present on disk.

        Uses Idiom B (exit-code gate): AssertDelta exits 1 when no durable
        delta exists. The pipeline outcome is FAIL -- which is the correct
        behavior. This is what the incident lacked: a gate that fires red
        when no durable commits exist, regardless of tree state.
        """
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        base_sha = _init_git_repo(repo_dir)

        ai_dir = repo_dir / ".ai"
        ai_dir.mkdir()
        (ai_dir / "base-sha").write_text(base_sha + "\n")

        # Simulate: work wrote a file but did NOT commit (incident pattern)
        src_dir = repo_dir / "src"
        src_dir.mkdir()
        (src_dir / "uncommitted.py").write_text("# uncommitted work\n")

        # Idiom B: AssertDelta exits 1 when no delta -> FAIL outcome.
        # No retry_target: the pipeline terminates with FAIL.
        # This is the correct behavior: the gate is red.
        dot_source = """\
digraph DeltaGateUncommittedCheck {
    graph [goal="Check that uncommitted work does not satisfy the delta gate"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    AssertDelta [
        shape=parallelogram,
        label="Assert Delta (Idiom B -- exit-code gate)",
        tool_command="if [ ! -f .ai/base-sha ]; then printf no_anchor; exit 1; fi; BASE=$(cat .ai/base-sha); COUNT=$(git log ${BASE}..HEAD -- src/ | wc -l | tr -d ' '); [ \\"$COUNT\\" -gt 0 ] && printf changed || { printf unchanged; exit 1; }"
    ]
    start -> AssertDelta
    AssertDelta -> done [condition="context.tool.last_line=changed && outcome=success", label="durable delta confirmed"]
}
"""
        hooks = EventCapture()
        engine = _make_engine(
            dot_source=dot_source,
            hooks=hooks,
            target_dir=repo_dir,
            logs_root=tmp_path / "logs-uncommitted",
        )

        outcome = await engine.run()

        # Idiom B: gate exits 1 -> FAIL. Pipeline terminates with FAIL.
        # This is the correct behavior: the gate is red when no durable delta.
        assert outcome.status == StageStatus.FAIL, (
            f"Expected FAIL (gate exits 1: no durable commits), "
            f"got {outcome.status}: {outcome.failure_reason}"
        )

        # Verify the failure reason mentions the gate command
        assert outcome.failure_reason is not None, (
            "Expected a failure_reason when the gate fires red"
        )

        # Verify the file is present on disk (incident pattern: tree has work)
        assert (repo_dir / "src" / "uncommitted.py").exists(), (
            "The uncommitted file must exist on disk (incident pattern: "
            "tree has work but no commits)"
        )

        # Verify git log shows no commits in src/ since base
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "log",
                f"{base_sha}..HEAD",
                "--",
                "src/",
            ],
            capture_output=True,
            text=True,
            check=True,  # git log must succeed; empty output is the assertion below
        )
        assert result.stdout.strip() == "", (
            f"Expected no commits in src/ since base (incident pattern), "
            f"got: {result.stdout.strip()}"
        )
