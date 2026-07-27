"""Unconditional CI proof of the materialize -> parse -> engine-run boundary.

The review that shipped alongside this test (PR #96,
https://github.com/microsoft/amplifier-bundle-attractor/pull/96#issuecomment-5096226946)
flagged that nothing in CI actually proves the materializer hands off to the
engine correctly: the only test that drives a remote ``git+https://`` entry
through to real completion
(``test_remote_end_to_end.py::test_remote_pipeline_runs_standalone``) is
gated behind ``ATTRACTOR_TEST_REMOTE_ENTRY`` and skips by default, so CI never
touches that seam. Everything else is a respx-mocked unit test of the
materializer in isolation -- and this exact class of bug (mocked tests all
green, real graph fails) already bit this PR once: ``d220243`` fixed a
missing ``graph.source_dir`` assignment that every mocked test missed because
none of them exercised a *real* fetch through to a *real* engine run.

This test closes that gap without needing an external fixture repo or any
credentials: this repository is itself public on GitHub, so it can fetch one
of its own files via ``git+https://`` -- a genuinely real, unmocked HTTP round
trip through the Contents API -- and run it through the engine to real
completion, unconditionally, on every CI run.

Drives ``drive_engine`` directly (with a bare dummy coordinator) rather than
the higher-level ``run_pipeline``. ``run_pipeline`` resolves the mounted
``loop-pipeline`` orchestrator module via the ``attractor-pipeline`` bundle's
own module manifest (``bundles/attractor-pipeline.yaml``), whose ``source:``
is hardcoded to ``git+https://...@main`` -- a real, pre-existing, and already
documented gap (see runner.py's ``# TODO(slice-1/§8.6)`` note) where that
resolution doesn't yet fall back to the local checkout, so it always
activates whatever is on ``main`` regardless of which branch is under test.
That's an orthogonal bundle-activation concern, not something this test is
about. ``drive_engine`` needs no bundle/session machinery at all for a
pipeline with no LLM/``box`` nodes (no ``session.spawn`` capability is ever
invoked), so it runs entirely against the already-imported, already-correct
in-process ``amplifier_module_loop_pipeline`` -- which is exactly the code
path (``_load_graph`` -> ``remote_dot.load_remote_or_local_graph`` ->
materialize -> parse -> engine run) this test needs to prove out.

Fixture choice: ``fixture_tool_reads_param.dot`` (this module's own runner
proof fixture) is a single deterministic ``tool_command`` node with no
LLM/box node, so this test needs no API keys, no coordinator capabilities,
and can't flake on model non-determinism -- it only proves the remote-source
plumbing.

Pinned to an immutable 40-hex commit SHA already merged to ``main`` (not this
feature branch) so the reference can never become unreachable/GC'd once this
branch is deleted post-merge, and so the fetched content can never drift out
from under this test (``is_immutable()`` in
``amplifier_module_remote_source.cache`` treats a 40-hex ref as
zero-network-after-first-fetch, but the *first* fetch in a clean CI cache is
still a real, unmocked network call).
"""

from pathlib import Path

import pytest

from amplifier_module_pipeline_runner.runner import drive_engine

# Pinned to a commit already on `main` (not this feature branch) -- see the
# module docstring for why. `fixture_tool_reads_param.dot` has been
# unchanged at this path since it landed in #87.
_PINNED_SHA = "a7369965615629ece6e5eb1724dd15095de8043e"
_ENTRY = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor"
    f"@{_PINNED_SHA}"
    "#subdirectory=modules/pipeline-runner/tests/fixtures/fixture_tool_reads_param.dot"
)


@pytest.mark.asyncio
async def test_remote_ci_smoke_real_public_repo_fetch(tmp_path: Path, monkeypatch):
    """Real (non-mocked) git+https:// fetch -> parse -> engine completion.

    Points ``$ATTRACTOR_CACHE_DIR`` at a fresh directory under ``tmp_path``
    so this test always exercises a real first-fetch (never silently
    short-circuited by a warm cache from a previous run/test on the same
    machine) and never pollutes -- or is polluted by -- the ambient cache.

    ``coordinator=object()``: a bare object is sufficient because
    ``fixture_tool_reads_param.dot`` has only a deterministic ``tool_command``
    node (``ToolHandler`` never touches ``coordinator``) -- no LLM/``box``
    node means ``AmplifierBackend``'s ``session.spawn`` capability lookup is
    never exercised (mirrors ``test_drive_engine_remote_cleanup.py``'s use of
    the same bare-coordinator pattern).
    """
    monkeypatch.setenv("ATTRACTOR_CACHE_DIR", str(tmp_path / "remote-cache"))

    outcome = await drive_engine(
        _ENTRY,
        coordinator=object(),
        params={"outfile": "proof.txt", "content": "remote-ci-smoke-ok"},
        cwd=tmp_path,
        logs_root=tmp_path / "logs",
        transform=False,
    )

    assert outcome.status.value == "success", outcome
    proof = tmp_path / "proof.txt"
    assert proof.exists()
    assert proof.read_text().strip() == "remote-ci-smoke-ok"
