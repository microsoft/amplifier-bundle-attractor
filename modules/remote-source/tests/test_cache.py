import pytest

from amplifier_module_remote_source.cache import BlobCache, is_immutable
from amplifier_module_remote_source.errors import RemoteFetchError
from amplifier_module_remote_source.fetch import FetchResult, git_blob_sha
from amplifier_module_remote_source.uri import Origin

IMMUT = "a" * 40


def _origin(ref="main", path="a.dot", host="github.com"):
    return Origin(host, "acme", "widgets", ref, path)


class FakeFetcher:
    def __init__(self, content=b"digraph G {}", server_sha=None, etag='"e1"'):
        self.content = content
        self.server_sha = server_sha if server_sha is not None else git_blob_sha(content)
        self.etag = etag
        self.calls = 0
        self.next_not_modified = False

    async def __call__(self, origin, *, token=None, base_url=None, etag=None):
        self.calls += 1
        if self.next_not_modified:
            return FetchResult(None, None, None, etag, not_modified=True)
        return FetchResult(self.content, git_blob_sha(self.content), self.server_sha, self.etag)


def test_is_immutable():
    assert is_immutable(IMMUT)
    assert not is_immutable("main")
    assert not is_immutable("v1.2.3")


@pytest.mark.asyncio
async def test_first_fetch_then_immutable_zero_network(tmp_path):
    cache = BlobCache(tmp_path)
    f = FakeFetcher()
    content, sha = await cache.get(_origin(ref=IMMUT), fetch_fn=f)
    assert content == f.content
    assert f.calls == 1
    # Second call for an immutable ref must not fetch again.
    await cache.get(_origin(ref=IMMUT), fetch_fn=f)
    assert f.calls == 1


@pytest.mark.asyncio
async def test_mutable_revalidates_but_reuses_on_304(tmp_path):
    cache = BlobCache(tmp_path)
    f = FakeFetcher()
    await cache.get(_origin(ref="main"), fetch_fn=f)
    assert f.calls == 1
    f.next_not_modified = True  # server says unchanged
    content, _ = await cache.get(_origin(ref="main"), fetch_fn=f)
    assert content == f.content       # served from cache
    assert f.calls == 2               # but it DID revalidate


@pytest.mark.asyncio
async def test_dedup_identical_bytes_one_blob(tmp_path):
    cache = BlobCache(tmp_path)
    f = FakeFetcher()
    await cache.get(_origin(path="a.dot", ref=IMMUT), fetch_fn=f)
    await cache.get(_origin(path="b.dot", ref=IMMUT), fetch_fn=f)
    blobs = list((tmp_path / "blobs").iterdir())
    assert len(blobs) == 1  # same content -> single blob


@pytest.mark.asyncio
async def test_integrity_reject_on_sha_mismatch(tmp_path):
    cache = BlobCache(tmp_path)
    f = FakeFetcher(server_sha="deadbeef" * 5)  # server declares a wrong sha
    with pytest.raises(RemoteFetchError):
        await cache.get(_origin(ref=IMMUT), fetch_fn=f)


@pytest.mark.asyncio
async def test_reload_forces_revalidation(tmp_path):
    cache = BlobCache(tmp_path)
    f = FakeFetcher()
    await cache.get(_origin(ref=IMMUT), fetch_fn=f)
    assert f.calls == 1
    await cache.get(_origin(ref=IMMUT), fetch_fn=f, reload=True)
    assert f.calls == 2  # reload bypasses the immutable fast path


def _assert_nothing_escaped(tmp_path, cache_root):
    """Every file under tmp_path must remain inside cache_root."""
    cache_root_str = str(cache_root)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert str(p).startswith(cache_root_str), f"traversal escaped to {p}"


@pytest.mark.asyncio
async def test_traversal_via_crafted_ref_rejected(tmp_path):
    """A crafted Origin with '..' in ref must not escape the cache root.

    parse_uri() rejects this at construction time, but BlobCache must also
    defend itself (belt-and-suspenders) against any Origin that reaches it
    directly — e.g. a future caller that bypasses parse_uri. The ref here
    has enough '../' segments to walk above the cache root entirely
    (root/refs/host/owner/repo/<ref-segments>/...).
    """
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    cache = BlobCache(cache_root)
    f = FakeFetcher()
    traversal_ref = "/".join([".."] * 8)
    evil_ref_origin = Origin("github.com", "acme", "widgets", traversal_ref, "a.dot")
    with pytest.raises(RemoteFetchError):
        await cache.get(evil_ref_origin, fetch_fn=f)
    _assert_nothing_escaped(tmp_path, cache_root)


@pytest.mark.asyncio
async def test_traversal_via_crafted_path_rejected(tmp_path):
    """A crafted Origin with '..' in path must not escape the cache root."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    cache = BlobCache(cache_root)
    f = FakeFetcher()
    traversal_path = "/".join([".."] * 8) + "/etc/passwd"
    evil_path_origin = Origin("github.com", "acme", "widgets", "main", traversal_path)
    with pytest.raises(RemoteFetchError):
        await cache.get(evil_path_origin, fetch_fn=f)
    _assert_nothing_escaped(tmp_path, cache_root)


def test_write_blob_loser_does_not_delete_winner_lock(tmp_path):
    """Simulate a losing writer: pre-create the lock file (as if another
    process holds it), then call _write_blob. The loser must return without
    deleting the lock or writing the blob — it must not tear down a lock it
    does not own.
    """
    cache = BlobCache(tmp_path)
    content = b"digraph G {}"
    sha = git_blob_sha(content)
    blob_path = cache._blob_path(sha)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = blob_path.with_suffix(".lock")
    lock_path.write_text("held-by-winner")

    # Loser attempts to write while the lock is held.
    cache._write_blob(sha, content)

    # The loser must not have deleted the winner's lock, nor written the blob.
    assert lock_path.exists(), "loser deleted the winner's lock file"
    assert not blob_path.exists(), "loser wrote the blob despite losing the lock"

    # Now the "winner" finishes: releases the lock and writes the blob itself.
    lock_path.unlink()
    cache._write_blob(sha, content)
    assert blob_path.exists()
    assert blob_path.read_bytes() == content
    # Lock and tmp are cleaned up after a real winning write.
    assert not lock_path.exists()
    tmp_candidates = list(blob_path.parent.glob(f"{sha}.tmp.*"))
    assert tmp_candidates == []
