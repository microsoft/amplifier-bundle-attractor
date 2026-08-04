"""Regression test for a leak-window bug in ``drive_engine``.

Before the fix, only ``engine.run()`` was wrapped in the outer try/finally
that calls ``_source_cleanup()``. Everything between ``_load_graph`` returning
and ``engine.run()`` being reached -- context seeding, ``apply_transforms``,
``validate_or_raise``, and the backend/registry/engine construction -- ran
OUTSIDE that try/finally. If ``validate_or_raise`` (or any of that setup)
raised after a successful remote materialize, the exception propagated
without ``_source_cleanup()`` ever running, leaking the per-run materialized
view directory on disk.

The fix moves the ``try:`` up to immediately after ``_load_graph`` returns,
wrapping everything through ``return await engine.run()``.
"""

import shutil

import pytest

import amplifier_module_pipeline_runner.runner as runner_mod


@pytest.mark.asyncio
async def test_drive_engine_cleanup_called_when_validate_fails_after_materialize(
    tmp_path, monkeypatch
):
    """If validate_or_raise raises after a successful materialize, cleanup must still run."""
    view_dir = tmp_path / "materialized-view"
    view_dir.mkdir()

    cleanup_calls: list[bool] = []

    def _cleanup() -> None:
        cleanup_calls.append(True)
        # Simulate the real materialize_remote_dot cleanup: remove the
        # per-run materialized view directory from disk.
        shutil.rmtree(view_dir, ignore_errors=True)

    class _FakeGraph:
        source_dir = str(view_dir)

    async def _fake_load_graph(_graph_or_dot, **_kwargs):
        return _FakeGraph(), _cleanup

    def _raising_validate_or_raise(_graph):
        raise ValueError("boom: simulated validate failure after materialize")

    monkeypatch.setattr(runner_mod, "_load_graph", _fake_load_graph)
    monkeypatch.setattr(
        "amplifier_module_loop_pipeline.validation.validate_or_raise",
        _raising_validate_or_raise,
    )

    with pytest.raises(ValueError, match="boom"):
        await runner_mod.drive_engine(
            "git+https://github.com/acme/samples@main#pipelines/main.dot",
            coordinator=object(),
            logs_root=tmp_path / "logs",
            transform=False,
            validate=True,
        )

    assert cleanup_calls == [True], (
        "cleanup must be called exactly once when validate_or_raise fails "
        "after a successful materialize"
    )
    assert not view_dir.exists(), (
        "materialized view directory must not leak on disk after the "
        "validate failure propagates"
    )
