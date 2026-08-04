"""A file-backed ROOT graph must carry the directory it was read from.

The bug: ``resolve_dot_path`` (loop-pipeline ``handlers/pipeline.py``) is a
precedence chain, not a search path -- it never checks existence, so the first
non-empty candidate wins:

    absolute -> graph.source_dir -> context.target_dir -> os.getcwd()

A root graph loaded from a file arrived with ``source_dir`` empty, because the
CLI reads the DOT as text and the path is discarded. ``--cwd`` sets
``context.target_dir``. So a root graph's relative ``dot_file=`` children were
looked for under the WORKING DIRECTORY rather than beside the pipeline, and
running a multi-file pipeline against a separate workspace required flattening
its DOT tree into that workspace.

Child graphs were never affected (``PipelineHandler.execute`` sets
``child_graph.source_dir``), nor were remote packages (they derive theirs from
the materialized entry). Only the root's first hop.

SCOPE: this covers the standalone CLI path -- ``attractor run <file>``. The
mounted ``PipelineOrchestrator`` also reads a local ``dot_file`` into text and
discards the path; that path is deliberately NOT changed here.

The directory is applied in the runner's ``_load_graph`` rather than passed
into the engine helper on purpose -- see the note there. Runner and engine are
separately resolved packages, and ``compat.py`` asserts symbol presence, not
signatures, so a new engine parameter would be a skew the gate cannot see.
"""

from __future__ import annotations

from pathlib import Path

import amplifier_module_pipeline_runner.runner as runner_mod
import pytest
from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner.runner import PipelineResult

_DOT = "digraph G { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }"


# --- _load_graph: where the directory is actually applied -------------------


@pytest.mark.asyncio
async def test_local_root_takes_the_supplied_source_dir():
    graph, cleanup = await runner_mod._load_graph(
        _DOT, source_dir="/pkg/pipeline-package"
    )
    try:
        assert graph.source_dir == "/pkg/pipeline-package"
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_without_a_source_dir_the_root_is_unchanged():
    """``--dot-source`` has no file, so it must keep the old cwd-relative behaviour."""
    graph, cleanup = await runner_mod._load_graph(_DOT)
    try:
        assert not graph.source_dir
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_an_existing_source_dir_is_never_clobbered():
    """A remote package derives its own from the materialized entry; that wins."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    already = parse_dot(_DOT)
    already.source_dir = "/materialized/view"

    graph, cleanup = await runner_mod._load_graph(already, source_dir="/ignored")
    try:
        assert graph.source_dir == "/materialized/view"
    finally:
        cleanup()


# --- propagation: CLI -> run_pipeline --------------------------------------
# The unit above proves the directory is applied once it arrives. These prove
# it actually travels from the command line, which is the hop that was broken.


def _capture_run_pipeline(monkeypatch, tmp_path) -> dict:
    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["source_dir"] = kwargs.get("source_dir")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return captured


def test_cmd_run_passes_the_dot_files_own_directory(monkeypatch, tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    dot_file = package / "pipeline.dot"
    dot_file.write_text(_DOT, encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    captured = _capture_run_pipeline(monkeypatch, tmp_path)

    args = cli.build_parser().parse_args(
        ["run", str(dot_file), "--cwd", str(workspace)]
    )
    assert cli.cmd_run(args) == 0

    # The pipeline's own directory -- NOT --cwd, which is the whole point.
    assert captured["source_dir"] == str(package.resolve())
    assert captured["source_dir"] != str(workspace)


def test_cmd_run_resolves_a_relative_dot_path(monkeypatch, tmp_path):
    """``source_dir`` must be absolute; the engine joins it with a relative ref."""
    package = tmp_path / "package"
    package.mkdir()
    (package / "pipeline.dot").write_text(_DOT, encoding="utf-8")

    captured = _capture_run_pipeline(monkeypatch, tmp_path)

    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args(
        ["run", "package/pipeline.dot", "--cwd", str(tmp_path)]
    )
    assert cli.cmd_run(args) == 0

    assert Path(captured["source_dir"]).is_absolute()
    assert captured["source_dir"] == str(package.resolve())


def test_dot_source_passes_no_source_dir(monkeypatch, tmp_path):
    """There is no file, so there is no directory to claim."""
    captured = _capture_run_pipeline(monkeypatch, tmp_path)

    args = cli.build_parser().parse_args(
        ["run", "--dot-source", _DOT, "--cwd", str(tmp_path)]
    )
    assert cli.cmd_run(args) == 0

    assert captured["source_dir"] is None


# --- true e2e: the relative child actually loads AND executes ---------------
# Every test above stops at "was source_dir threaded" -- they all mock
# run_pipeline itself, so nothing proves resolve_dot_path actually finds the
# sibling file. This drives cmd_run's real argv-parsing/source_dir-derivation
# code, then a fake run_pipeline that -- instead of being a black-box mock --
# forwards straight into the real drive_engine() (real parser, real engine,
# real PipelineHandler.resolve_dot_path). Only the outer bundle/session
# composition in the real run_pipeline() is skipped (irrelevant to this bug:
# it never touches source_dir). AmplifierBackend is never invoked because
# both DOT files use only tool nodes (shape=parallelogram), so no mocking of
# the LLM boundary is needed -- fully hermetic, no network, no API keys.

_PARENT_DOT_RELATIVE_CHILD = """\
digraph parent {
    graph [goal="verify relative child resolution via the CLI"]
    start [shape=Mdiamond]
    sub   [shape=folder, dot_file="child.dot"]
    done  [shape=Msquare]
    start -> sub -> done
}
"""

_CHILD_DOT_TOOL_NODE = """\
digraph child {
    start [shape=Mdiamond]
    work  [shape=parallelogram, tool_command="echo child-executed > child_ran.txt"]
    done  [shape=Msquare]
    start -> work -> done
}
"""


def test_cmd_run_e2e_resolves_and_executes_relative_child(monkeypatch, tmp_path):
    """True e2e for the CLI path: a relative dot_file= child must actually
    load AND execute -- not merely that source_dir was captured as a kwarg.

    Before the fix: cmd_run's source_dir is None (this test predates that --
    see the stash/pop proof in the PR), so drive_engine's _load_graph leaves
    graph.source_dir empty, resolve_dot_path falls through to
    context.target_dir (--cwd, pinned below to a decoy dir with no
    child.dot), and the child pipeline -- and therefore the whole run --
    FAILS with "Child DOT file not found".

    After the fix: source_dir is the package directory, the sibling
    child.dot is found, and its own tool node actually runs (proven by the
    canary file it writes).
    """
    package = tmp_path / "package"
    package.mkdir()
    (package / "child.dot").write_text(_CHILD_DOT_TOOL_NODE, encoding="utf-8")
    parent_dot = package / "parent.dot"
    parent_dot.write_text(_PARENT_DOT_RELATIVE_CHILD, encoding="utf-8")

    # --cwd points at a DECOY workspace with no child.dot -- if resolution
    # falls back to context.target_dir instead of the package dir, the run
    # fails here instead of silently "working" by accident.
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    logs_dir = tmp_path / "logs"

    async def fake_run_pipeline(dot_source, **kwargs):
        outcome = await runner_mod.drive_engine(
            dot_source,
            coordinator=None,
            logs_root=logs_dir,
            cwd=kwargs.get("cwd"),
            transform=True,
            source_dir=kwargs.get("source_dir"),
        )
        return PipelineResult(
            status=outcome.status.value,
            notes=outcome.notes or "",
            logs_dir=logs_dir,
            raw="{}",
            failure_reason=outcome.failure_reason,
        )

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    args = cli.build_parser().parse_args(
        ["run", str(parent_dot), "--cwd", str(workspace)]
    )
    assert cli.cmd_run(args) == 0

    # The child's tool node runs with cwd=context.target_dir (--cwd), per
    # ToolHandler's documented cwd contract (test_tool_cwd.py) -- that
    # contract is independent of this fix and deliberately left alone. The
    # canary landing in the workspace is proof the child's own node actually
    # executed (not merely that the parent "succeeded" trivially): if
    # resolve_dot_path had fallen through to context.target_dir instead of
    # finding child.dot beside parent.dot, the child pipeline -- and the
    # whole run -- would have FAILED with "Child DOT file not found" instead
    # of reaching this node at all.
    canary = workspace / "child_ran.txt"
    assert canary.exists(), (
        f"Expected {canary} to exist -- the child pipeline's tool node must "
        "have actually executed, proving the relative dot_file= reference "
        "resolved beside the invoked pipeline rather than failing to be "
        "found at all"
    )
    assert canary.read_text().strip() == "child-executed"
