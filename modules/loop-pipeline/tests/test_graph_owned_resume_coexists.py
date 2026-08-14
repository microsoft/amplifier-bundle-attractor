"""AC-5 guard: graph-owned idempotency keeps working, unmodified.

``examples/pipelines/12-graph-resume.dot`` is the sanctioned graph-level
resume pattern that shipped when engine-level resume was removed in PR #66:
guard nodes test for a stage's artifact and self-skip when it exists, on a
plain fresh run from Start. Engine resume does not replace it and must not
disturb it — the two are complements, not competitors:

  * graph-owned skip-through answers "this work is already done on disk",
    which the engine cannot know;
  * engine resume answers "this process died mid-graph", which the graph
    cannot know.

This test executes the shipped example byte-for-byte as committed (it never
writes to it) and asserts its documented semantics still hold on a fresh run.
"""

import json
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_RESUME
from amplifier_module_loop_pipeline.validation import lint, validate_or_raise

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "examples" / "pipelines" / "12-graph-resume.dot"

pytestmark = pytest.mark.skipif(
    not _EXAMPLE.is_file(),
    reason="examples/ tree not present (installed-package test run)",
)


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        self.calls.append(node.id)
        return "done"


class MockHooks:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name, data):
        self.events.append((name, data))

    def names(self):
        return [n for n, _ in self.events]


def _seed_artifacts(work: Path) -> None:
    """Every stage's guard file present — the 'everything already done' state."""
    ai = work / ".ai"
    ai.mkdir()
    (ai / "smells.md").write_text("smells\n")
    (ai / "refactor-plan.md").write_text("plan\n")
    (ai / "snapshot.txt").write_text("snapshot\n")
    (ai / "STATE.json").write_text(json.dumps({"tests_passed": True}))


def test_example_still_parses_and_lints_clean():
    graph = parse_dot(_EXAMPLE.read_text())
    validate_or_raise(graph)
    errors = [d for d in lint(graph) if d.severity == "ERROR"]
    assert errors == [], errors


@pytest.mark.asyncio
async def test_guard_nodes_self_skip_on_a_fresh_run_from_start(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _seed_artifacts(work)

    graph = parse_dot(_EXAMPLE.read_text())
    validate_or_raise(graph)
    context = PipelineContext()
    context.set("context.target_dir", str(work))
    backend = RecordingBackend()
    hooks = MockHooks()
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(
            HandlerContext(backend=backend, hooks=hooks)
        ),
        logs_root=str(tmp_path / "logs"),
        hooks=hooks,
    )

    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    # Every guarded stage self-skipped via file state; only the unguarded
    # final review ran. This is the example's documented behavior, unchanged.
    assert backend.calls == ["diff_review"]
    for skipped in (
        "analyze_smells",
        "plan_refactor",
        "snapshot_tests",
        "implement_refactor",
        "run_tests",
    ):
        assert skipped not in backend.calls

    # Engine resume did not participate: this was a fresh run, and a fresh run
    # has no path to a checkpoint.
    assert PIPELINE_RESUME not in hooks.names()


@pytest.mark.asyncio
async def test_rewinding_a_stage_by_deleting_its_artifact_still_works(tmp_path):
    """The example's documented rewind: delete an artifact, that stage re-runs."""
    work = tmp_path / "work"
    work.mkdir()
    _seed_artifacts(work)
    (work / ".ai" / "refactor-plan.md").unlink()

    graph = parse_dot(_EXAMPLE.read_text())
    context = PipelineContext()
    context.set("context.target_dir", str(work))
    backend = RecordingBackend()
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path / "logs"),
    )

    await engine.run()

    # Stage 2 re-executes because its artifact is gone; stage 1 still skips.
    assert "plan_refactor" in backend.calls
    assert "analyze_smells" not in backend.calls
