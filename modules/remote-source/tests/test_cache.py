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
