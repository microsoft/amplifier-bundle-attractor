"""Tests for T1-1: Convergence observability — make iteration N-1 survive.

Covers:
  (a) Per-iteration node records surviving across loop_restart
  (b) $iteration / $loop_count substitution in prompts and tool commands
  (c) Append-only trace.jsonl written per node completion

The attractor trace CLI subcommand tests live in:
  modules/pipeline-runner/tests/test_trace_subcommand.py

Spec extension: Extension #24 (specs/EXTENSIONS.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class MockBackend:
    """Returns a fixed string for every node."""

    def __init__(self, return_value: str = "done") -> None:
        self._return_value = return_value

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        return self._return_value


class SequenceBackend:
    """Returns different outcomes per node id; falls back to SUCCESS."""

    def __init__(self, outcomes: dict[str, str | Outcome]) -> None:
        self._outcomes = outcomes

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str | Outcome:
        return self._outcomes.get(node.id, "ok")


class CapturingBackend:
    """Records the prompt text seen by each node; converges on 'assess'."""

    def __init__(self) -> None:
        self.prompts: dict[str, list[str]] = {}

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str | Outcome:
        self.prompts.setdefault(node.id, []).append(prompt)
        if node.id == "assess":
            return Outcome(status=StageStatus.SUCCESS, preferred_label="converged")
        return Outcome(status=StageStatus.SUCCESS)


class CountingRestartBackend:
    """Drives a loop_restart pipeline for N iterations then stops.

    - 'work' node: tool-like — returns last_line=go for the first
      (stop_after - 1) calls, then last_line=stop.
    - Other nodes: plain SUCCESS.

    Captures context snapshots at each 'work' invocation so tests can
    inspect the $iteration value seen by the node.
    """

    def __init__(self, stop_after: int = 3) -> None:
        self._stop_after = stop_after
        self._call_count = 0
        self.iteration_values: list[str] = []  # $iteration seen per call

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str | Outcome:
        if node.id == "work":
            self._call_count += 1
            # Capture the $iteration value as seen in context
            self.iteration_values.append(context.get("iteration") or "")
            if self._call_count >= self._stop_after:
                return Outcome(
                    status=StageStatus.SUCCESS,
                    preferred_label="stop",
                )
            return Outcome(
                status=StageStatus.SUCCESS,
                preferred_label="go",
            )
        return Outcome(status=StageStatus.SUCCESS)


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str | None = None,
    tmp_path: Path | None = None,
) -> PipelineEngine:
    """Parse DOT, validate, and build a PipelineEngine."""
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    root = logs_root or str(tmp_path / "logs")
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=root,
    )


# ---------------------------------------------------------------------------
# (a) Per-iteration node records surviving across loop_restart
# ---------------------------------------------------------------------------

# Minimal 3-iteration loop pipeline (tool-only, no LLM needed):
# start -> work -> done  (on preferred_label=stop)
# work  -> work          (on preferred_label=go, loop_restart=true)
_LOOP_DOT = """\
digraph LoopFixture {
    graph [goal="loop fixture"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    work  [prompt="iteration $iteration"]
    start -> work
    work -> done [condition="preferred_label=stop"]
    work -> work [condition="preferred_label=go", loop_restart="true"]
}
"""


class TestPerIterationNodeRecords:
    """Iteration-scoped node records survive loop_restart."""

    @pytest.mark.asyncio
    async def test_iteration_dirs_created(self, tmp_path):
        """iteration_N/ directories are created for each loop_restart."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        outcome = await engine.run()
        assert outcome.status == StageStatus.SUCCESS

        logs = tmp_path / "logs"
        # Iterations 1 and 2 are the restart iterations; iteration 0 is the
        # initial pass.  After 3 calls to 'work': calls 1 and 2 trigger
        # loop_restart (go), call 3 triggers done (stop).
        # So iteration_1/ and iteration_2/ must exist.
        assert (logs / "iteration_1").is_dir(), "iteration_1/ not created"
        assert (logs / "iteration_2").is_dir(), "iteration_2/ not created"

    @pytest.mark.asyncio
    async def test_iteration_scoped_status_json_exists(self, tmp_path):
        """iteration_N/<node_id>/status.json exists for each iteration."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        logs = tmp_path / "logs"
        # 'work' runs in iteration 0, 1, and 2; all three must be recorded.
        for iteration in (0, 1, 2):
            status_path = logs / f"iteration_{iteration}" / "work" / "status.json"
            assert status_path.exists(), (
                f"iteration_{iteration}/work/status.json not found"
            )

    @pytest.mark.asyncio
    async def test_iteration_scoped_status_json_has_correct_iteration(self, tmp_path):
        """iteration_N/work/status.json records the correct iteration number."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        logs = tmp_path / "logs"
        for iteration in (0, 1, 2):
            status_path = logs / f"iteration_{iteration}" / "work" / "status.json"
            data = json.loads(status_path.read_text())
            assert data["iteration"] == iteration, (
                f"iteration_{iteration}/work/status.json has wrong iteration: "
                f"{data['iteration']!r}"
            )

    @pytest.mark.asyncio
    async def test_flat_status_json_still_exists(self, tmp_path):
        """Flat logs_root/<node_id>/status.json still exists (backward compat)."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        logs = tmp_path / "logs"
        flat_path = logs / "work" / "status.json"
        assert flat_path.exists(), "Flat work/status.json not found (backward compat)"

    @pytest.mark.asyncio
    async def test_distinct_iteration_records_survive(self, tmp_path):
        """All three iterations' records coexist — N-1 is not overwritten by N."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        logs = tmp_path / "logs"
        # Collect all status.json files under iteration_* directories
        iteration_statuses = list(logs.glob("iteration_*/work/status.json"))
        assert len(iteration_statuses) >= 3, (
            f"Expected at least 3 per-iteration status.json files, "
            f"found {len(iteration_statuses)}: {iteration_statuses}"
        )

        # Verify distinct iteration numbers across all records
        iterations_seen = set()
        for p in iteration_statuses:
            data = json.loads(p.read_text())
            iterations_seen.add(data["iteration"])
        assert len(iterations_seen) >= 3, (
            f"Expected at least 3 distinct iteration numbers, got: {iterations_seen}"
        )


# ---------------------------------------------------------------------------
# (b) $iteration / $loop_count substitution
# ---------------------------------------------------------------------------


class TestIterationSubstitution:
    """$iteration and $loop_count expand in prompts and tool commands."""

    @pytest.mark.asyncio
    async def test_iteration_seeded_in_context_at_start(self, tmp_path):
        """iteration is set to '0' in context at pipeline start."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                done  [shape=Msquare]
                start -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        # Seed context is applied in engine.run() -> _initialize_context()
        # We can read it from context after run()
        await engine.run()
        assert engine.context.get("iteration") == "0", (
            f"Expected iteration='0', got {engine.context.get('iteration')!r}"
        )

    @pytest.mark.asyncio
    async def test_loop_count_seeded_in_context_at_start(self, tmp_path):
        """loop_count is set to '0' in context at pipeline start."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                done  [shape=Msquare]
                start -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        assert engine.context.get("loop_count") == "0"

    @pytest.mark.asyncio
    async def test_iteration_increments_on_loop_restart(self, tmp_path):
        """$iteration in context increments with each loop_restart."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        # The backend captured the iteration value seen by 'work' on each call.
        # Call 1: iteration 0 (initial pass)
        # Call 2: iteration 1 (after first loop_restart)
        # Call 3: iteration 2 (after second loop_restart)
        assert backend.iteration_values == ["0", "1", "2"], (
            f"Expected iteration values ['0', '1', '2'], "
            f"got {backend.iteration_values}"
        )

    @pytest.mark.asyncio
    async def test_iteration_in_prompt_expands(self, tmp_path):
        """$iteration in a node prompt expands to the current iteration number."""
        # The _LOOP_DOT fixture has prompt="iteration $iteration"
        # We use CapturingBackend to record what prompt text was received.
        capturing = CapturingBackend()
        engine = _make_engine(
            """\
            digraph {
                graph [goal="test"]
                start  [shape=Mdiamond]
                done   [shape=Msquare]
                assess [prompt="current iteration is $iteration"]
                start -> assess
                assess -> done [condition="preferred_label=converged"]
            }
            """,
            backend=capturing,
            tmp_path=tmp_path,
        )
        await engine.run()

        prompts = capturing.prompts.get("assess", [])
        assert prompts, "assess node was never called"
        assert "current iteration is 0" in prompts[0], (
            f"Expected '$iteration' to expand to '0' in prompt, got: {prompts[0]!r}"
        )
        assert "$iteration" not in prompts[0], (
            f"Expected '$iteration' to be expanded (not raw), got: {prompts[0]!r}"
        )


# ---------------------------------------------------------------------------
# (c) Append-only trace.jsonl
# ---------------------------------------------------------------------------


class TestTraceJsonl:
    """trace.jsonl is written per node completion and is append-only."""

    @pytest.mark.asyncio
    async def test_trace_jsonl_created(self, tmp_path):
        """trace.jsonl is created in the run directory after a pipeline run."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                work  [prompt="do work"]
                done  [shape=Msquare]
                start -> work -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        trace_path = tmp_path / "logs" / "trace.jsonl"
        assert trace_path.exists(), "trace.jsonl not created"

    @pytest.mark.asyncio
    async def test_trace_jsonl_is_valid_jsonl(self, tmp_path):
        """Each line of trace.jsonl is valid JSON."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                work  [prompt="do work"]
                done  [shape=Msquare]
                start -> work -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        trace_path = tmp_path / "logs" / "trace.jsonl"
        records = []
        for line in trace_path.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        assert records, "trace.jsonl has no records"

    @pytest.mark.asyncio
    async def test_trace_jsonl_has_required_fields(self, tmp_path):
        """Each trace record has iteration, node_id, status, ts fields."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                work  [prompt="do work"]
                done  [shape=Msquare]
                start -> work -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        trace_path = tmp_path / "logs" / "trace.jsonl"
        for line in trace_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            assert "iteration" in rec, f"Missing 'iteration' in trace record: {rec}"
            assert "node_id" in rec, f"Missing 'node_id' in trace record: {rec}"
            assert "status" in rec, f"Missing 'status' in trace record: {rec}"
            assert "ts" in rec, f"Missing 'ts' in trace record: {rec}"

    @pytest.mark.asyncio
    async def test_trace_jsonl_records_all_nodes(self, tmp_path):
        """trace.jsonl has one record per executed node."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                a     [prompt="step a"]
                b     [prompt="step b"]
                done  [shape=Msquare]
                start -> a -> b -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        trace_path = tmp_path / "logs" / "trace.jsonl"
        records = [
            json.loads(line)
            for line in trace_path.read_text().splitlines()
            if line.strip()
        ]
        node_ids = {r["node_id"] for r in records}
        # start, a, b are executed (done/exit node is not executed via handler)
        assert "start" in node_ids, f"'start' missing from trace: {node_ids}"
        assert "a" in node_ids, f"'a' missing from trace: {node_ids}"
        assert "b" in node_ids, f"'b' missing from trace: {node_ids}"

    @pytest.mark.asyncio
    async def test_trace_jsonl_records_multiple_iterations(self, tmp_path):
        """trace.jsonl contains records for all iterations (append-only)."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        trace_path = tmp_path / "logs" / "trace.jsonl"
        records = [
            json.loads(line)
            for line in trace_path.read_text().splitlines()
            if line.strip()
        ]
        work_records = [r for r in records if r["node_id"] == "work"]
        assert len(work_records) >= 3, (
            f"Expected at least 3 'work' records in trace.jsonl, got {len(work_records)}"
        )
        iterations_seen = {r["iteration"] for r in work_records}
        assert len(iterations_seen) >= 3, (
            f"Expected records for at least 3 distinct iterations, got: {iterations_seen}"
        )

    @pytest.mark.asyncio
    async def test_trace_jsonl_iteration_numbers_are_correct(self, tmp_path):
        """trace.jsonl records carry the correct iteration number."""
        backend = CountingRestartBackend(stop_after=3)
        engine = _make_engine(_LOOP_DOT, backend=backend, tmp_path=tmp_path)
        await engine.run()

        trace_path = tmp_path / "logs" / "trace.jsonl"
        work_records = [
            json.loads(line)
            for line in trace_path.read_text().splitlines()
            if line.strip() and json.loads(line)["node_id"] == "work"
        ]
        # Sort by iteration and verify they are 0, 1, 2
        iterations = sorted(r["iteration"] for r in work_records)
        assert iterations == [0, 1, 2], (
            f"Expected work iterations [0, 1, 2], got {iterations}"
        )

    @pytest.mark.asyncio
    async def test_trace_jsonl_has_duration_ms(self, tmp_path):
        """trace.jsonl records include duration_ms."""
        backend = MockBackend()
        engine = _make_engine(
            """\
            digraph {
                start [shape=Mdiamond]
                work  [prompt="do work"]
                done  [shape=Msquare]
                start -> work -> done
            }
            """,
            backend=backend,
            tmp_path=tmp_path,
        )
        await engine.run()
        trace_path = tmp_path / "logs" / "trace.jsonl"
        for line in trace_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            assert "duration_ms" in rec, f"Missing 'duration_ms' in trace record: {rec}"
            assert isinstance(rec["duration_ms"], (int, float)), (
                f"duration_ms should be numeric, got {rec['duration_ms']!r}"
            )



