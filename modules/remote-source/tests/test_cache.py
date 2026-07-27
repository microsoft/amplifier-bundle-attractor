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
        self.seen_limits = []

    async def __call__(self, origin, *, token=None, base_url=None, etag=None, limits=None):
        self.calls += 1
        self.seen_limits.append(limits)
        if self.next_not_modified:
            return FetchResult(None, None, None, etag, not_modified=True)
        return FetchResult(self.content, git_blob_sha(self.content), self.server_sha, self.etag)


class ThreeOhFourThenContentFetcher:
    """304 when an etag is sent (any etag), full 200+content when etag is None.

    Models a server that still has the ref unchanged (etag matches) but is
    used to exercise the self-heal path where the cached blob file has been
    externally deleted -- the cache must force a no-etag refetch to recover
    real bytes rather than raising on the empty 304 result.
    """

    def __init__(self, content=b"digraph G {}", server_sha=None, etag='"e1"'):
        self.content = content
        self.server_sha = server_sha if server_sha is not None else git_blob_sha(content)
        self.etag = etag
        self.calls = 0
        self.etags_seen = []
        self.always_not_modified = False

    async def __call__(self, origin, *, token=None, base_url=None, etag=None, limits=None):
        self.calls += 1
        self.etags_seen.append(etag)
        if self.always_not_modified or etag is not None:
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
async def test_reuses_on_304_even_when_cached_blob_deleted_by_self_heal(tmp_path):
    """304 + externally-deleted blob must self-heal via a forced no-etag
    refetch, not raise RemoteFetchError("empty fetch result...").

    Fail-before: with the old code, deleting the blob file and then hitting
    the 304 branch would call _read_blob() -> None and fall through to
    ``raise RemoteFetchError("empty fetch result...")`` because ``result``
    still carries ``content=None`` from the 304 response.
    """
    cache = BlobCache(tmp_path)
    f = ThreeOhFourThenContentFetcher()

    # Prime the cache: first fetch (no etag on disk yet) gets full content.
    content, sha = await cache.get(_origin(ref="main"), fetch_fn=f)
    assert content == f.content
    assert f.calls == 1
    assert f.etags_seen == [None]

    blob_path = cache._blob_path(sha)
    assert blob_path.exists()

    # Externally delete the blob file -- ref on disk still points at it.
    blob_path.unlink()
    assert not blob_path.exists()

    # get() must self-heal: revalidation request (with etag) returns 304,
    # then a forced no-etag refetch returns full content, which gets
    # rewritten to disk. No RemoteFetchError.
    content2, sha2 = await cache.get(_origin(ref="main"), fetch_fn=f)

    assert content2 == f.content
    assert sha2 == sha
    assert f.calls == 3  # 1 (prime) + 1 (etag'd 304) + 1 (forced no-etag refetch)
    assert f.etags_seen == [None, '"e1"', None]
    assert blob_path.exists(), "blob must be rewritten to disk after self-heal"
    assert blob_path.read_bytes() == f.content


@pytest.mark.asyncio
async def test_reuses_on_304_even_when_cached_blob_corrupted_on_disk(tmp_path):
    """304 + an on-disk blob whose bytes no longer hash to its own filename
    (e.g. bitrot/external corruption) must self-heal via a forced no-etag
    refetch, not silently hand back the wrong bytes.

    Fail-before: with the old code, the 304 branch did ``_read_blob() ->
    return blob`` unconditionally whenever the file existed, with no
    ``git_blob_sha(blob) == entry["blob_sha"]`` check -- corrupted content
    would be returned as if it were valid, forever, since a 304 never
    touches the network.
    """
    cache = BlobCache(tmp_path)
    f = ThreeOhFourThenContentFetcher()

    # Prime the cache: first fetch (no etag on disk yet) gets full content.
    content, sha = await cache.get(_origin(ref="main"), fetch_fn=f)
    assert content == f.content
    assert f.calls == 1

    blob_path = cache._blob_path(sha)
    assert blob_path.exists()

    # Corrupt the on-disk blob in place -- the ref still points at `sha`,
    # but the bytes under that path no longer hash to it.
    blob_path.write_bytes(b"corrupted, does not match its own sha")

    # get() must detect the mismatch on the 304 path and self-heal: the
    # revalidation request (with etag) returns 304, then a forced no-etag
    # refetch returns full (correct) content, which gets rewritten to disk.
    content2, sha2 = await cache.get(_origin(ref="main"), fetch_fn=f)

    assert content2 == f.content
    assert sha2 == sha
    assert f.calls == 3  # 1 (prime) + 1 (etag'd 304) + 1 (forced no-etag refetch)
    assert f.etags_seen == [None, '"e1"', None]
    assert blob_path.read_bytes() == f.content, "corrupted blob must be overwritten with correct bytes"


@pytest.mark.asyncio
async def test_second_fetch_still_empty_after_self_heal_raises(tmp_path):
    """If the forced no-etag refetch ALSO yields no content, get() must
    still raise -- self-heal is a single attempt, not a retry loop."""
    cache = BlobCache(tmp_path)
    f = ThreeOhFourThenContentFetcher()

    await cache.get(_origin(ref="main"), fetch_fn=f)
    blob_path = cache._blob_path(f.server_sha)
    blob_path.unlink()

    # Force even the no-etag refetch to return not_modified/empty.
    f.always_not_modified = True

    with pytest.raises(RemoteFetchError):
        await cache.get(_origin(ref="main"), fetch_fn=f)


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


@pytest.mark.asyncio
async def test_limits_passed_through_to_fetch_fn(tmp_path):
    """A caller-supplied FetchLimits must reach fetch_fn -- not be dropped
    silently on the way through BlobCache.get()."""
    from amplifier_module_remote_source.limits import FetchLimits

    cache = BlobCache(tmp_path)
    f = FakeFetcher()
    custom_limits = FetchLimits(per_request_timeout=5.0)
    await cache.get(_origin(ref="main"), fetch_fn=f, limits=custom_limits)
    assert f.seen_limits == [custom_limits]


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


def test_write_blob_concurrent_identical_writes_produce_one_blob(tmp_path):
    """Two writes of identical content must not raise and must produce
    exactly one blob file on disk (atomic os.replace, no lock needed —
    blobs are content-addressed so concurrent writers of the same blob_sha
    are writing byte-identical content by construction)."""
    cache = BlobCache(tmp_path)
    content = b"digraph G {}"
    sha = git_blob_sha(content)
    blob_path = cache._blob_path(sha)

    # Simulate two "concurrent" writers racing to write the same blob —
    # both must succeed without raising, and no stray tmp files remain.
    cache._write_blob(sha, content)
    cache._write_blob(sha, content)

    assert blob_path.exists()
    assert blob_path.read_bytes() == content
    tmp_candidates = list(blob_path.parent.glob(f"{sha}.*.tmp"))
    assert tmp_candidates == [], "no tmp files should remain after writes"
    blob_candidates = [p for p in blob_path.parent.iterdir() if p.is_file()]
    assert blob_candidates == [blob_path], "exactly one blob file, no duplicates"


@pytest.mark.asyncio
async def test_get_never_writes_ref_pointing_at_missing_blob(tmp_path, monkeypatch):
    """If the blob write somehow leaves no file on disk (e.g. a crash
    mid-write in another process), get() must NOT write a ref pointing at
    that missing blob — a dangling ref would permanently poison the cache
    (the is_immutable() fast path would keep resolving to a blob_sha with
    no backing file, forever).
    """
    cache = BlobCache(tmp_path)
    f = FakeFetcher()

    # Simulate _write_blob silently failing to persist the blob (e.g. a
    # concurrent crash-mid-write elsewhere left nothing on disk).
    monkeypatch.setattr(cache, "_write_blob", lambda blob_sha, content: None)

    with pytest.raises(RemoteFetchError):
        await cache.get(_origin(ref=IMMUT), fetch_fn=f)

    # No ref file should exist pointing at the (non-existent) blob.
    ref_path = cache._ref_path(_origin(ref=IMMUT))
    assert not ref_path.exists(), "a ref must never be written for a missing blob"
