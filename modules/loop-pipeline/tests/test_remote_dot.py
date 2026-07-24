import base64
import os

import httpx
import pytest
import respx

from amplifier_module_loop_pipeline.remote_dot import materialize_remote_dot
from amplifier_module_remote_source import BlobCache, FetchLimits, RemoteFetchPathError

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
        assert other not in text                 # the URL was rewritten...
        assert 'dot_file="' in text and ".dot" in text  # ...to a local relpath
        # the cross-repo file exists under the mirrored layout
        assert (entry_path.parents[3] / "acme" / "lib" / "main" / "shared" / "util.dot").exists()
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
