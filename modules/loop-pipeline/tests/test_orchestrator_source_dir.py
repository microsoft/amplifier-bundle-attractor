"""The mounted PipelineOrchestrator must preserve a file-backed root graph's
source directory, the second of the two remaining partial-coverage-symmetry
(AGENTS.md S4) sites for this bug -- the standalone CLI path was already
fixed (see ``pipeline-runner``'s ``test_root_source_dir.py``).

This file also owns the CONSUMING-side end-to-end proof for the third site,
the agent-facing ``run_pipeline`` tool (``tool-pipeline-run``). That tool
does not depend on this package (AGENTS.md's dependency-awareness rule --
``tool-pipeline-run``'s ``pyproject.toml`` declares only ``amplifier-core``),
so it cannot construct or run a real ``PipelineOrchestrator`` in its own
suite. Its own tests instead assert on the ``orchestrator_config`` dict it
builds -- ``{"dot_source": <text>, "source_dir": <dir>}`` -- for a file-backed
``dot_file`` input; see
``modules/tool-pipeline-run/tests/test_source_dir_propagation.py``.
``test_execute_resolves_relative_child_via_dot_source_and_source_dir`` below
closes the loop from this side: it hands ``PipelineOrchestrator`` that exact
config shape directly (no cross-module import needed, since it's just a
dict) and proves the relative ``dot_file=`` child actually loads and
executes -- the same proof the two modules previously duplicated via a
cross-module import that broke CI (see that file's docstring for the
incident).

The bug: ``resolve_dot_path`` (``handlers/pipeline.py``) is a precedence
chain, not a search path -- the first non-empty candidate wins:

    absolute -> graph.source_dir -> context.target_dir -> os.getcwd()

``PipelineOrchestrator.execute()`` reads a local ``dot_file`` as text via
``self.config.get("dot_file")`` and discarded the directory, so a mounted
pipeline's relative ``dot_file=`` children were looked for under
``os.getcwd()`` (there is no ``context.target_dir`` at this layer) instead
of beside the pipeline that was actually invoked.

Child graphs were never affected (``PipelineHandler.execute`` sets
``child_graph.source_dir``), nor were remote packages (they derive theirs
from the materialized entry). Only the root's first hop -- same shape as the
CLI bug, different entry point.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_loop_pipeline import PipelineOrchestrator
from amplifier_module_loop_pipeline.outcome import StageStatus

# ---------------------------------------------------------------------------
# _resolve_dot_source: where the directory is actually derived
# ---------------------------------------------------------------------------


def test_dot_file_config_returns_its_own_directory(tmp_path):
    """A dot_file-backed config returns (text, that file's absolute directory)."""
    dot_path = tmp_path / "pipeline.dot"
    dot_path.write_text("digraph G { s [shape=Mdiamond]; }", encoding="utf-8")

    orchestrator = PipelineOrchestrator(config={"dot_file": str(dot_path)})
    text, source_dir = orchestrator._resolve_dot_source()

    assert "digraph G" in text
    assert source_dir == str(tmp_path.resolve())


def test_inline_dot_source_returns_no_source_dir():
    """Inline dot_source has no file, so there is no directory to claim."""
    orchestrator = PipelineOrchestrator(
        config={"dot_source": "digraph G { s [shape=Mdiamond]; }"}
    )
    text, source_dir = orchestrator._resolve_dot_source()

    assert "digraph G" in text
    assert source_dir is None


def test_inline_dot_source_honors_explicit_source_dir_override():
    """A caller that already resolved dot_file to text itself (tool-pipeline-run)
    can still forward the directory via the explicit 'source_dir' config key."""
    orchestrator = PipelineOrchestrator(
        config={
            "dot_source": "digraph G { s [shape=Mdiamond]; }",
            "source_dir": "/pkg/pipeline-package",
        }
    )
    text, source_dir = orchestrator._resolve_dot_source()

    assert "digraph G" in text
    assert source_dir == "/pkg/pipeline-package"


def test_missing_both_raises_value_error():
    orchestrator = PipelineOrchestrator(config={})
    with pytest.raises(ValueError, match="No DOT source configured"):
        orchestrator._resolve_dot_source()


# ---------------------------------------------------------------------------
# execute(): the directory is applied to the loaded graph, never clobbered
# ---------------------------------------------------------------------------


class _MockBackend:
    """Minimal mock backend -- returns JSON success outcome for any node."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return json.dumps({"status": "success", "notes": f"mock: {node.id}"})


_PARENT_DOT_RELATIVE_CHILD = """\
digraph parent {
    graph [goal="verify relative child resolution"]
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


def _write_parent_and_child(tmp_path):
    """Write a parent DOT with a relative dot_file= child into the same dir."""
    package = tmp_path / "package"
    package.mkdir()
    (package / "child.dot").write_text(_CHILD_DOT_TOOL_NODE, encoding="utf-8")
    parent_dot = package / "parent.dot"
    parent_dot.write_text(_PARENT_DOT_RELATIVE_CHILD, encoding="utf-8")
    return package, parent_dot


@pytest.mark.asyncio
async def test_execute_resolves_and_runs_relative_child_from_dot_file(
    tmp_path, monkeypatch
):
    """True e2e: a dot_file-backed root graph's relative dot_file= child
    actually loads AND executes -- not merely that source_dir was threaded.

    Before the fix: graph.source_dir stays "" after load_remote_or_local_graph,
    resolve_dot_path falls through to os.getcwd() (the pytest invocation
    directory, NOT the package dir), child.dot is not found there, and the
    child pipeline -- and therefore the parent -- FAILS.

    After the fix: graph.source_dir is seeded from the dot_file's own
    directory, resolve_dot_path finds the sibling child.dot, and the child's
    own tool node actually runs (proven by the canary file it writes).
    """
    package, parent_dot = _write_parent_and_child(tmp_path)

    # Pin cwd somewhere that is NOT the package dir, so a false-positive
    # cwd-fallback resolution would be caught by this test.
    decoy_cwd = tmp_path / "decoy_cwd"
    decoy_cwd.mkdir()
    monkeypatch.chdir(decoy_cwd)

    orchestrator = PipelineOrchestrator(config={"dot_file": str(parent_dot)})
    result_json = await orchestrator.execute(
        prompt="test goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=_MockBackend(),
    )
    result = json.loads(result_json)

    assert result["status"] == StageStatus.SUCCESS.value, result
    # The child's own tool node ran for real -- proves the child DOT was
    # actually loaded and executed, not merely located.
    canary = package / "child_ran.txt"
    assert canary.exists(), (
        f"Expected {canary} to exist -- the child pipeline's tool node must "
        "have actually executed for the parent to succeed"
    )
    assert canary.read_text().strip() == "child-executed"


@pytest.mark.asyncio
async def test_execute_never_clobbers_an_already_set_source_dir(tmp_path, monkeypatch):
    """A remote package (or any pre-parsed Graph) carries its own, more
    specific source_dir -- the dot_file-derived directory must never
    overwrite it."""
    package, parent_dot = _write_parent_and_child(tmp_path)

    import amplifier_module_loop_pipeline.remote_dot as remote_dot_mod

    real_load = remote_dot_mod.load_remote_or_local_graph
    captured_graph: dict = {}

    async def _load_and_pin_source_dir(source):
        graph, cleanup = await real_load(source)
        graph.source_dir = "/already/materialized/view"
        captured_graph["graph"] = graph
        return graph, cleanup

    monkeypatch.setattr(
        remote_dot_mod, "load_remote_or_local_graph", _load_and_pin_source_dir
    )

    orchestrator = PipelineOrchestrator(config={"dot_file": str(parent_dot)})
    # The pinned source_dir has no child.dot, so the child pipeline node
    # fails -- but that FAILURE is itself the proof the pinned value won,
    # not the dot_file's own directory.
    result_json = await orchestrator.execute(
        prompt="test goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=_MockBackend(),
    )
    result = json.loads(result_json)

    assert captured_graph["graph"].source_dir == "/already/materialized/view"
    assert result["status"] == StageStatus.FAIL.value, (
        "Expected the pinned (non-package) source_dir to be honored, causing "
        f"the child lookup to fail against it instead of {package}: {result}"
    )


@pytest.mark.asyncio
async def test_execute_resolves_relative_child_via_dot_source_and_source_dir(
    tmp_path, monkeypatch
):
    """True e2e for the exact config shape ``tool-pipeline-run`` builds:
    inline ``dot_source`` text plus an explicit ``source_dir`` override (as
    opposed to a local ``dot_file`` path, covered above).

    ``tool-pipeline-run``'s ``run_pipeline`` tool reads a dot_file itself,
    then forwards only the resolved TEXT plus this directory across the
    session.spawn boundary -- see that module's ``_resolve_dot_source_with_dir``
    and the ``orchestrator_config["source_dir"]`` it builds. That module
    cannot depend on this package (AGENTS.md dependency-awareness rule), so
    it cannot construct a real PipelineOrchestrator to prove the far side of
    that boundary end to end. This test proves it from here instead, using
    only a plain config dict -- the same shape, no cross-module import.
    """
    package, _parent_dot = _write_parent_and_child(tmp_path)
    parent_text = _PARENT_DOT_RELATIVE_CHILD

    # Pin cwd somewhere that is NOT the package dir, so a false-positive
    # cwd-fallback resolution would be caught by this test.
    decoy_cwd = tmp_path / "decoy_cwd"
    decoy_cwd.mkdir()
    monkeypatch.chdir(decoy_cwd)

    orchestrator = PipelineOrchestrator(
        config={"dot_source": parent_text, "source_dir": str(package)}
    )
    result_json = await orchestrator.execute(
        prompt="test goal",
        context=None,
        providers={},
        tools={},
        hooks=None,
        backend=_MockBackend(),
    )
    result = json.loads(result_json)

    assert result["status"] == StageStatus.SUCCESS.value, result
    canary = package / "child_ran.txt"
    assert canary.exists(), (
        f"Expected {canary} to exist -- the child pipeline's tool node must "
        "have actually executed, proving the relative dot_file= reference "
        "resolved via the explicit source_dir override rather than under cwd"
    )
    assert canary.read_text().strip() == "child-executed"
