"""The agent-facing ``run_pipeline`` tool must preserve a file-backed
``dot_file``'s directory through to the spawned child session.

This is the third of the three sites the root-graph-source-dir bug touches
(the standalone CLI and the mounted PipelineOrchestrator's own local
``dot_file`` are the other two). The spawned child session only receives
resolved TEXT via ``orchestrator_config["dot_source"]`` -- the file path
itself never crosses the spawn boundary. Without the directory travelling
alongside it, a relative ``dot_file=`` child reference in the pipeline
resolves under the child session's ``--cwd`` instead of beside the pipeline
that was actually invoked, even though the exact same pipeline works fine
when run via the standalone CLI.

This module owns only the EMITTING side of that contract: given a
``dot_file`` (plain path or ``@mention``) or an inline ``dot_source``, does
``execute()`` build an ``orchestrator_config`` that carries the right
``source_dir`` (or correctly omits it)? The tests below assert on the real
config this tool produces, using a fake ``session.spawn`` that only captures
its kwargs -- they do not construct or run a ``PipelineOrchestrator``, so
this suite has no dependency on ``amplifier-module-loop-pipeline`` (this
module's ``pyproject.toml`` intentionally declares only ``amplifier-core``;
see AGENTS.md's dependency-awareness rule).

The CONSUMING side of the contract -- that ``PipelineOrchestrator.execute()``
actually resolves and runs a relative ``dot_file=`` child when handed the
exact ``{"dot_source": ..., "source_dir": ...}`` shape this tool builds -- is
proven from ``loop-pipeline``'s own suite, which can import its own engine:
see ``modules/loop-pipeline/tests/test_orchestrator_source_dir.py::
test_execute_resolves_relative_child_via_dot_source_and_source_dir``. A
prior version of this file additionally re-verified that same consuming-side
behavior here, by importing ``PipelineOrchestrator`` directly and driving a
fake ``session.spawn`` that constructed one -- that import made this test
uncollectable in CI, where ``tool-pipeline-run``'s isolated ``uv`` venv has
no ``loop-pipeline`` package (it only appeared to pass locally because an
unrelated editable install had leaked it into this developer's shared venv).
That duplicate coverage is not reintroduced here; the two modules each test
their own side, and together they prove the contract end to end without
either importing the other.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from amplifier_module_tool_pipeline_run import PipelineRunTool

MINIMAL_DOT = (
    "digraph Test { start [shape=Mdiamond]; done [shape=Msquare]; start -> done }"
)

# ---------------------------------------------------------------------------
# _resolve_dot_source_with_dir: unit tests
# ---------------------------------------------------------------------------


def test_inline_dot_source_has_no_directory():
    """Inline dot_source has no file, so source_dir is None."""
    tool = PipelineRunTool(config={})
    text, source_dir = tool._resolve_dot_source_with_dir(
        dot_file=None, dot_source=MINIMAL_DOT
    )
    assert text == MINIMAL_DOT
    assert source_dir is None


def test_dot_file_path_returns_its_own_directory():
    """A dot_file path returns (text, that file's resolved parent directory)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        dot_path = os.path.join(tmp_dir, "pipeline.dot")
        with open(dot_path, "w", encoding="utf-8") as f:
            f.write(MINIMAL_DOT)

        tool = PipelineRunTool(config={})
        text, source_dir = tool._resolve_dot_source_with_dir(
            dot_file=dot_path, dot_source=None
        )
        assert text == MINIMAL_DOT
        assert source_dir == str(tmp_dir)


def test_dot_source_takes_precedence_and_still_returns_no_directory():
    """When both are provided, dot_source wins and there is still no directory."""
    tool = PipelineRunTool(config={})
    text, source_dir = tool._resolve_dot_source_with_dir(
        dot_file="/some/file.dot", dot_source=MINIMAL_DOT
    )
    assert text == MINIMAL_DOT
    assert source_dir is None


def test_at_mention_path_returns_resolved_directory():
    """@mention resolution still yields the resolved file's directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        dot_path = os.path.join(tmp_dir, "pipeline.dot")
        with open(dot_path, "w", encoding="utf-8") as f:
            f.write(MINIMAL_DOT)

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = dot_path
        mock_coordinator = MagicMock()
        mock_coordinator.get_capability.return_value = mock_resolver

        tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
        text, source_dir = tool._resolve_dot_source_with_dir(
            dot_file="@attractor:examples/pipelines/01-simple-linear.dot",
            dot_source=None,
        )
        assert text == MINIMAL_DOT
        assert source_dir == str(tmp_dir)


def test_missing_both_raises_value_error():
    tool = PipelineRunTool(config={})
    with pytest.raises(ValueError, match="dot_file or dot_source"):
        tool._resolve_dot_source_with_dir(dot_file=None, dot_source=None)


def test_missing_dot_file_raises_file_not_found():
    tool = PipelineRunTool(config={})
    with pytest.raises(FileNotFoundError):
        tool._resolve_dot_source_with_dir(
            dot_file="/nonexistent/path.dot", dot_source=None
        )


def test_resolve_dot_source_backward_compat_still_returns_plain_text():
    """The existing tested contract of _resolve_dot_source (text only) is unchanged."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(dot_file=None, dot_source=MINIMAL_DOT)
    assert resolved == MINIMAL_DOT
    assert isinstance(resolved, str)


# ---------------------------------------------------------------------------
# execute(): orchestrator_config carries (or omits) source_dir
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_forwards_source_dir_for_dot_file(tmp_path):
    """dot_file input -> orchestrator_config carries the file's own directory."""
    dot_path = tmp_path / "pipeline.dot"
    dot_path.write_text(MINIMAL_DOT, encoding="utf-8")

    captured: dict = {}

    async def fake_spawn(**spawn_kwargs):
        captured["orchestrator_config"] = spawn_kwargs.get("orchestrator_config")
        return {"output": json.dumps({"status": "success", "notes": "ok"})}

    mock_coordinator = MagicMock()
    mock_coordinator.get_capability = lambda name: (
        fake_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute({"goal": "test goal", "dot_file": str(dot_path)})

    assert result.success
    assert captured["orchestrator_config"]["source_dir"] == str(tmp_path)


@pytest.mark.asyncio(loop_scope="session")
async def test_execute_omits_source_dir_for_inline_dot_source():
    """Inline dot_source input -> no source_dir key at all (nothing to forward)."""
    captured: dict = {}

    async def fake_spawn(**spawn_kwargs):
        captured["orchestrator_config"] = spawn_kwargs.get("orchestrator_config")
        return {"output": json.dumps({"status": "success", "notes": "ok"})}

    mock_coordinator = MagicMock()
    mock_coordinator.get_capability = lambda name: (
        fake_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute({"goal": "test goal", "dot_source": MINIMAL_DOT})

    assert result.success
    assert "source_dir" not in captured["orchestrator_config"]


# NOTE: A prior version of this file also contained a "true e2e" test here
# that constructed a real PipelineOrchestrator inside a fake session.spawn
# to prove a relative dot_file= child actually loads and executes. That
# test required `from amplifier_module_loop_pipeline import
# PipelineOrchestrator`, a cross-module import this package does not (and
# should not) depend on -- see the module docstring above for where that
# consuming-side coverage now lives instead.
