"""Guard: the engine core stays network-free at import time.

Importing amplifier_module_loop_pipeline must not import httpx. The network logic
lives only in Layer A (amplifier_module_remote_source), reached lazily.
"""

import subprocess
import sys


def test_importing_loop_pipeline_does_not_import_httpx():
    code = (
        "import sys; import amplifier_module_loop_pipeline; "
        "assert 'httpx' not in sys.modules, "
        "'engine core must not import httpx at import time'; "
        "print('network-free')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "network-free" in result.stdout


def test_httpx_not_a_core_dependency():
    """httpx must not be a *core* dependency of loop-pipeline (only the optional
    'remote' extra pulls Layer A, which owns httpx)."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    core_deps = " ".join(data["project"]["dependencies"])
    assert "httpx" not in core_deps
