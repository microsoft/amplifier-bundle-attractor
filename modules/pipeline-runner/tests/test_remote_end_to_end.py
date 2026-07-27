"""Standalone proof: a remote git+https:// pipeline runs through the engine with
only the loop-pipeline module loaded — no external resolver or orchestrator.
Real execution — env-gated + offline-skippable."""

import os
from pathlib import Path

import pytest

from amplifier_module_pipeline_runner.runner import run_pipeline


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ATTRACTOR_TEST_REMOTE_ENTRY"),
    reason="set ATTRACTOR_TEST_REMOTE_ENTRY to a pinned public no-LLM pipeline URI",
)
async def test_remote_pipeline_runs_standalone(tmp_path: Path):
    result = await run_pipeline(
        os.environ["ATTRACTOR_TEST_REMOTE_ENTRY"],
        cwd=tmp_path,
        logs_root=tmp_path / "logs",
        transform=False,
    )
    assert result.status == "success", result
    if (tmp_path / "logs" / "pipeline.dot").exists():
        assert "git+https://" not in (tmp_path / "logs" / "pipeline.dot").read_text()
