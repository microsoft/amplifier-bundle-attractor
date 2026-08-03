"""Tests for the runner's fail-loud engine compatibility gate."""

from __future__ import annotations

import importlib

import pytest

from amplifier_module_pipeline_runner import compat
from amplifier_module_pipeline_runner.compat import IncompatibleEngineError


def test_compatibility_gate_passes_for_available_required_symbol(monkeypatch):
    monkeypatch.setattr(
        compat,
        "_REQUIRED_ENGINE_SYMBOLS",
        [("amplifier_module_loop_pipeline.remote_dot", "load_remote_or_local_graph")],
    )

    compat.check_engine_compatibility()


def test_compatibility_gate_fails_loud_with_reinstall_instruction(monkeypatch):
    def missing_module(_name: str):
        raise ImportError("simulated stale engine")

    monkeypatch.setattr(importlib, "import_module", missing_module)
    monkeypatch.setattr(compat, "_REQUIRED_ENGINE_SYMBOLS", [("missing_engine_module", "feature")])

    with pytest.raises(IncompatibleEngineError) as exc_info:
        compat.check_engine_compatibility()

    error = str(exc_info.value)
    assert "INCOMPATIBLE ENGINE" in error
    assert "missing_engine_module (module not found)" in error
    assert "uv tool install --reinstall" in error


def test_cli_converts_incompatible_engine_error_to_exit_1(monkeypatch):
    """CLI must convert IncompatibleEngineError to sys.exit(1) for the shell."""
    import sys
    from io import StringIO

    from amplifier_module_pipeline_runner import compat as compat_mod
    from amplifier_module_pipeline_runner.cli import main

    def missing_module(_name: str):
        raise ImportError("simulated stale engine")

    monkeypatch.setattr(importlib, "import_module", missing_module)
    monkeypatch.setattr(
        compat_mod, "_REQUIRED_ENGINE_SYMBOLS", [("missing_engine_module", "feature")]
    )

    captured_err = StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    result = main([])
    assert result == 1
    assert "INCOMPATIBLE ENGINE" in captured_err.getvalue()


def test_drive_engine_api_path_raises_incompatible_engine_error_not_bare_import_error(
    monkeypatch,
):
    """drive_engine() must raise IncompatibleEngineError, not a bare ModuleNotFoundError.

    Regression test for the incident-class failure: a consumer using drive_engine()
    as a library seam against a stale cached engine must receive an actionable
    IncompatibleEngineError (with INCOMPATIBLE ENGINE diagnostic and reinstall
    instruction) rather than a bare ModuleNotFoundError mid-pipeline.

    Simulates the incident: patches _REQUIRED_ENGINE_SYMBOLS so the compat gate
    sees a missing symbol, then calls drive_engine() directly (not via CLI).
    """
    import asyncio

    from amplifier_module_pipeline_runner import compat as compat_mod
    from amplifier_module_pipeline_runner.runner import drive_engine

    def missing_module(_name: str):
        raise ImportError("simulated stale engine — remote_dot absent")

    monkeypatch.setattr(importlib, "import_module", missing_module)
    monkeypatch.setattr(
        compat_mod,
        "_REQUIRED_ENGINE_SYMBOLS",
        [("amplifier_module_loop_pipeline.remote_dot", "load_remote_or_local_graph")],
    )

    with pytest.raises(IncompatibleEngineError) as exc_info:
        asyncio.run(
            drive_engine(
                "digraph { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
                object(),  # fake coordinator — never reached
                logs_root="/tmp/test-drive-engine-compat",
                transform=False,
                validate=False,
            )
        )

    error = str(exc_info.value)
    assert "INCOMPATIBLE ENGINE" in error
    assert "uv tool install --reinstall" in error
