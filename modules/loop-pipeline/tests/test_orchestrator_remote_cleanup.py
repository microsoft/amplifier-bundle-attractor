"""Regression test for a leak-window bug in the mounted PipelineOrchestrator.

Covers a bug found during Task 12 verification: in the ``git+https://`` branch
of ``PipelineOrchestrator.execute()``, the materialize call and the parse of
the materialized entry happened *outside* the outer try/finally that runs
``_source_cleanup()``. If ``materialize_remote_dot`` succeeded (real cleanup
callback assigned) but the subsequent ``parse_dot(...)`` call raised, the
exception propagated from outside the try/finally and cleanup never ran,
leaking the per-run materialized view directory on disk.

The fix wraps just the materialize->parse window in its own try/except that
calls cleanup and re-raises. That sequence now lives in the single shared
``remote_dot.load_remote_or_local_graph`` helper used by both this mounted
hook and the sibling direct-engine hook in
``amplifier_module_pipeline_runner.runner._load_graph`` -- see that
function's docstring for why the two were unified.
"""

import shutil

import pytest

import amplifier_module_loop_pipeline.remote_dot as remote_dot_mod
from amplifier_module_loop_pipeline import PipelineOrchestrator


@pytest.mark.asyncio
async def test_cleanup_called_when_parse_fails_after_materialize(tmp_path, monkeypatch):
    """If parse_dot raises after a successful materialize, cleanup must still run."""
    view_dir = tmp_path / "materialized-view"
    view_dir.mkdir()
    entry_path = view_dir / "main.dot"
    entry_path.write_text(
        "digraph { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }",
        encoding="utf-8",
    )

    cleanup_calls: list[bool] = []

    def _cleanup() -> None:
        cleanup_calls.append(True)
        # Simulate the real materialize_remote_dot cleanup: remove the
        # per-run materialized view directory from disk.
        shutil.rmtree(view_dir, ignore_errors=True)

    async def _fake_materialize(dot_source: str):
        return entry_path, _cleanup

    def _raising_parse_dot(_source: str):
        raise ValueError("boom: simulated parse failure after materialize")

    # materialize_remote_dot and parse_dot are both referenced as module-level
    # globals inside remote_dot.load_remote_or_local_graph (which execute()
    # now delegates to), so patching them on the remote_dot module is what
    # that shared helper's calls will see.
    monkeypatch.setattr(remote_dot_mod, "materialize_remote_dot", _fake_materialize)
    monkeypatch.setattr(remote_dot_mod, "parse_dot", _raising_parse_dot)

    orchestrator = PipelineOrchestrator(
        config={"dot_source": "git+https://github.com/acme/samples@main#pipelines/main.dot"}
    )

    with pytest.raises(ValueError, match="boom"):
        await orchestrator.execute(
            prompt="test goal",
            context=None,
            providers={},
            tools={},
            hooks=None,
        )

    assert cleanup_calls == [True], (
        "cleanup must be called exactly once when parse fails after a "
        "successful materialize"
    )
    assert not view_dir.exists(), (
        "materialized view directory must not leak on disk after the "
        "parse failure propagates"
    )
