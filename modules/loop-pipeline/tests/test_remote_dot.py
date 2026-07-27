import base64
import os
import posixpath
import re

import httpx
import pytest
import respx

from amplifier_module_loop_pipeline.remote_dot import (
    load_remote_or_local_graph,
    materialize_remote_dot,
)
from amplifier_module_remote_source import (
    BlobCache,
    FetchLimits,
    RemoteFetchPathError,
    parse_uri,
)

API = "https://api.github.com/repos"


def _contents(owner, repo, path, body: str):
    url = f"{API}/{owner}/{repo}/contents/{path}"
    return respx.get(url__regex=re_escape(url)).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": base64.b64encode(body.encode()).decode(),
                "encoding": "base64",
                "sha": _blob_sha(body.encode()),
            },
        )
    )


def re_escape(s: str) -> str:
    import re

    return re.escape(s)


def _blob_sha(data: bytes) -> str:
    from amplifier_module_remote_source import git_blob_sha

    return git_blob_sha(data)


ENTRY = "git+https://github.com/acme/samples#subdirectory=pipelines/main.dot"


@pytest.mark.asyncio
@respx.mock
async def test_recursive_walk_in_origin(tmp_path):
    # main.dot -> child.dot (same repo, relative), child -> leaf.dot
    _contents("acme", "samples", "pipelines/main.dot",
              'digraph G { a [dot_file="child.dot"]; }')
    _contents("acme", "samples", "pipelines/child.dot",
              'digraph G { b [dot_file="leaf.dot"]; }')
    _contents("acme", "samples", "pipelines/leaf.dot", "digraph G { c; }")

    entry_path, cleanup = await materialize_remote_dot(
        ENTRY, cache=BlobCache(tmp_path)
    )
    try:
        assert entry_path.exists()
        text = entry_path.read_text()
        assert 'dot_file="child.dot"' in text  # in-origin ref left as-is
        # sibling file materialized next to the entry
        assert (entry_path.parent / "child.dot").exists()
        assert (entry_path.parent / "leaf.dot").exists()
    finally:
        cleanup()
    assert not entry_path.exists()  # cleanup removed the per-run view


@pytest.mark.asyncio
@respx.mock
async def test_cross_repo_rewrite(tmp_path):
    other = "git+https://github.com/acme/lib#subdirectory=shared/util.dot"
    _contents("acme", "samples", "pipelines/main.dot",
              f'digraph G {{ a [dot_file="{other}"]; }}')
    _contents("acme", "lib", "shared/util.dot", "digraph G { u; }")

    entry_path, cleanup = await materialize_remote_dot(ENTRY, cache=BlobCache(tmp_path))
    try:
        text = entry_path.read_text()
        assert other not in text  # the URL was rewritten...

        match = re.search(r'dot_file\s*=\s*"([^"]+)"', text)
        assert match, f"expected a rewritten dot_file= attribute, got: {text!r}"
        rewritten_ref = match.group(1)
        assert not rewritten_ref.startswith("git+https://")  # ...to a local relpath

        # Resolve the rewritten ref exactly as the engine will: relative to the
        # entry file's own directory.
        rewritten_path = (entry_path.parent / rewritten_ref).resolve()
        assert rewritten_path.exists()

        # Derive the per-run view root from the entry's own repo-root-relative
        # path (owner/repo/ref/path) instead of a hardcoded parents[N] index, so
        # this stays correct regardless of how deep the entry's `path` is.
        entry_origin = parse_uri(ENTRY)
        entry_relpath = posixpath.join(
            entry_origin.owner, entry_origin.repo, entry_origin.ref, entry_origin.path
        )
        view_dir = entry_path.parents[len(entry_relpath.split("/")) - 1]

        # the cross-repo file exists under the mirrored layout
        expected_cross_repo_path = (
            view_dir / "acme" / "lib" / "main" / "shared" / "util.dot"
        )
        assert expected_cross_repo_path.exists()

        # The rewritten ref must point at exactly that file, not just *some* file,
        # so this genuinely proves the cross-repo rewrite rather than merely that
        # a file happens to exist somewhere.
        assert rewritten_path == expected_cross_repo_path.resolve()
    finally:
        cleanup()


@pytest.mark.asyncio
@respx.mock
async def test_variable_ref_skipped(tmp_path):
    _contents("acme", "samples", "pipelines/main.dot",
              'digraph G { a [dot_file="$dynamic.dot"]; }')
    entry_path, cleanup = await materialize_remote_dot(ENTRY, cache=BlobCache(tmp_path))
    try:
        assert 'dot_file="$dynamic.dot"' in entry_path.read_text()  # left untouched
    finally:
        cleanup()


@pytest.mark.asyncio
@respx.mock
async def test_escape_rejected(tmp_path):
    _contents("acme", "samples", "pipelines/main.dot",
              'digraph G { a [dot_file="../../etc/passwd"]; }')
    with pytest.raises(RemoteFetchPathError):
        await materialize_remote_dot(ENTRY, cache=BlobCache(tmp_path))


@pytest.mark.asyncio
@respx.mock
async def test_depth_limit_fail_fast(tmp_path):
    _contents("acme", "samples", "pipelines/main.dot",
              'digraph G { a [dot_file="child.dot"]; }')
    _contents("acme", "samples", "pipelines/child.dot", "digraph G { c; }")
    from amplifier_module_remote_source import RemoteFetchLimitError

    with pytest.raises(RemoteFetchLimitError):
        await materialize_remote_dot(
            ENTRY, cache=BlobCache(tmp_path), limits=FetchLimits(max_depth=1)
        )


@pytest.mark.asyncio
@respx.mock
async def test_diamond_dependency_fetched_once(tmp_path):
    """main.dot -> {a.dot, b.dot}; both a.dot and b.dot -> shared.dot (same
    origin+path). shared.dot must be fetched exactly once, and both
    rewritten dot_file= refs must resolve to the identical materialized path.
    """
    main_route = _contents(
        "acme", "samples", "pipelines/main.dot",
        'digraph G { a [dot_file="a.dot"]; b [dot_file="b.dot"]; }',
    )
    a_route = _contents(
        "acme", "samples", "pipelines/a.dot",
        'digraph G { s [dot_file="shared.dot"]; }',
    )
    b_route = _contents(
        "acme", "samples", "pipelines/b.dot",
        'digraph G { s [dot_file="shared.dot"]; }',
    )
    shared_route = _contents(
        "acme", "samples", "pipelines/shared.dot", "digraph G { leaf; }"
    )

    entry_path, cleanup = await materialize_remote_dot(ENTRY, cache=BlobCache(tmp_path))
    try:
        # shared.dot must be fetched exactly once despite two referrers.
        assert shared_route.call_count == 1
        assert main_route.call_count == 1
        assert a_route.call_count == 1
        assert b_route.call_count == 1

        a_text = (entry_path.parent / "a.dot").read_text()
        b_text = (entry_path.parent / "b.dot").read_text()

        match_a = re.search(r'dot_file\s*=\s*"([^"]+)"', a_text)
        match_b = re.search(r'dot_file\s*=\s*"([^"]+)"', b_text)
        assert match_a and match_b

        # Both refs are same-origin (in-repo relative refs), left as-is;
        # both must resolve (relative to their own file) to the same file.
        resolved_from_a = (entry_path.parent / match_a.group(1)).resolve()
        resolved_from_b = (entry_path.parent / match_b.group(1)).resolve()
        assert resolved_from_a == resolved_from_b
        assert resolved_from_a.exists()
    finally:
        cleanup()


# --- ONE REAL recursive fetch against a PINNED public fixture -----------------
# Fill ATTRACTOR_TEST_REMOTE_ENTRY with a real, immutable (SHA-pinned) entry URI
# whose tree fetches cleanly. Skipped when unset.
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ATTRACTOR_TEST_REMOTE_ENTRY"),
    reason="set ATTRACTOR_TEST_REMOTE_ENTRY for the live recursive fetch",
)
async def test_real_recursive_fetch(tmp_path):
    entry_path, cleanup = await materialize_remote_dot(
        os.environ["ATTRACTOR_TEST_REMOTE_ENTRY"], cache=BlobCache(tmp_path)
    )
    try:
        assert entry_path.exists()
        assert entry_path.read_text().strip()
    finally:
        cleanup()


# --- load_remote_or_local_graph: the shared materialize/parse/cleanup hook ---
# used by both `pipeline_runner.runner._load_graph` and the mounted
# `PipelineOrchestrator.execute()` -- see that function's docstring for why
# the two hand-synced copies were unified into this one.


@pytest.mark.asyncio
async def test_load_local_string_parses_and_noop_cleanup():
    graph, cleanup = await load_remote_or_local_graph(
        "digraph G { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }"
    )
    assert "s" in graph.nodes
    assert "d" in graph.nodes
    cleanup()  # must not raise -- no-op for the local path


@pytest.mark.asyncio
async def test_load_local_graph_object_passthrough():
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    original = parse_dot("digraph G { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }")
    graph, cleanup = await load_remote_or_local_graph(original)
    assert graph is original
    cleanup()


@pytest.mark.asyncio
@respx.mock
async def test_load_remote_sets_source_dir(tmp_path, monkeypatch):
    # load_remote_or_local_graph -> materialize_remote_dot doesn't take an
    # explicit cache override (matching both production call sites), so
    # point the default cache root at tmp_path to keep this hermetic.
    monkeypatch.setenv("ATTRACTOR_CACHE_DIR", str(tmp_path / "cache"))
    _contents("acme", "samples", "pipelines/main.dot",
              "digraph G { s [shape=Mdiamond]; d [shape=Msquare]; s -> d }")
    graph, cleanup = await load_remote_or_local_graph(ENTRY)
    try:
        assert graph.source_dir is not None
        assert os.path.isdir(graph.source_dir)
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_load_remote_cleanup_called_when_parse_fails(tmp_path, monkeypatch):
    """If parse_dot raises after a successful materialize, cleanup must still
    run and the per-run view must not leak -- this is the exact regression
    both call sites (runner.drive_engine and the mounted orchestrator) guard
    against, now exercised once against the shared implementation."""
    import amplifier_module_loop_pipeline.remote_dot as remote_dot_mod

    view_dir = tmp_path / "materialized-view"
    view_dir.mkdir()
    entry_path = view_dir / "main.dot"
    entry_path.write_text("not valid dot", encoding="utf-8")

    cleanup_calls: list[bool] = []

    def _cleanup() -> None:
        cleanup_calls.append(True)
        import shutil

        shutil.rmtree(view_dir, ignore_errors=True)

    async def _fake_materialize(_source: str):
        return entry_path, _cleanup

    def _raising_parse_dot(_text: str):
        raise ValueError("boom: simulated parse failure after materialize")

    monkeypatch.setattr(remote_dot_mod, "materialize_remote_dot", _fake_materialize)
    monkeypatch.setattr(remote_dot_mod, "parse_dot", _raising_parse_dot)

    with pytest.raises(ValueError, match="boom"):
        await load_remote_or_local_graph(
            "git+https://github.com/acme/samples@main#subdirectory=pipelines/main.dot"
        )

    assert cleanup_calls == [True]
    assert not view_dir.exists()
