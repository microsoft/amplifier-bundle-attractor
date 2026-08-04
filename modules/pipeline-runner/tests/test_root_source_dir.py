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

import pytest

import amplifier_module_pipeline_runner.runner as runner_mod
from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner.runner import PipelineResult

_DOT = "digraph G { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }"


# --- _load_graph: where the directory is actually applied -------------------


@pytest.mark.asyncio
async def test_local_root_takes_the_supplied_source_dir():
    graph, cleanup = await runner_mod._load_graph(_DOT, source_dir="/pkg/pipeline-package")
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
