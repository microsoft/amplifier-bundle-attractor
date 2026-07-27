"""Greenfield content-addressed blob cache (Layer A).

Layout:
  <root>/blobs/<blob-sha>                       -- content, dedup + integrity
  <root>/refs/<host>/<owner>/<repo>/<ref>/<path>.json -> {blob_sha, etag, fetched_at}

Freshness:
  * immutable ref (40-hex sha) -> served from cache, zero network ever
  * mutable ref (branch/tag name) -> revalidated via If-None-Match (304 = reuse)
  * $ATTRACTOR_RELOAD forces revalidation even for immutable entries
Integrity: fetched bytes MUST hash to the server-declared sha, else rejected.
Concurrency: blobs are content-addressed (path == blob sha; bytes are
  identical by construction), so no lock is needed for correctness -- every
  writer writes to its own unique temp path, then ``os.replace``s it into
  place atomically. Concurrent writers of identical content race harmlessly;
  the last ``os.replace`` wins and produces byte-identical content. A ref is
  only ever written after its blob is confirmed present on disk, so a
  crash mid-write can never leave a dangling ref pointing at a missing blob.
Containment: every filesystem path derived from an Origin (refs/*) or a
  blob-sha (blobs/*) is verified to stay under ``self.root`` before any
  read/write/mkdir — defense in depth against a crafted Origin escaping the
  cache root (see uri.py's traversal rejection for the first line of defense).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .errors import RemoteFetchError, RemoteFetchPathError
from .fetch import FetchResult, fetch_blob, git_blob_sha
from .limits import FetchLimits
from .uri import Origin

_IMMUTABLE_RE = re.compile(r"^[0-9a-f]{40}$")

FetchFn = Callable[..., Awaitable[FetchResult]]


def default_cache_root() -> Path:
    env = os.environ.get("ATTRACTOR_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "amplifier-attractor"


def is_immutable(ref: str) -> bool:
    """A 40-hex commit/blob SHA is immutable. Named refs (branches, tags) are
    treated as mutable in v1 (tag-as-immutable optimization is deferred)."""
    return bool(_IMMUTABLE_RE.match(ref))


class BlobCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_root()

    def _ensure_within_root(self, path: Path) -> Path:
        """Containment check: reject any path that resolves outside ``root``.

        Belt-and-suspenders alongside uri.py's traversal rejection — this
        catches any Origin/blob-sha that somehow still produces an escaping
        path (e.g. a future caller that bypasses ``parse_uri``). ``resolve()``
        normalizes '..' segments and symlinks without requiring the path to
        exist; we compare against it but return the original (unresolved)
        path so callers still mkdir/read/write at the intended location.
        """
        root_resolved = self.root.resolve()
        resolved = path.resolve()
        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            raise RemoteFetchPathError(f"Path escapes cache root: {path} (root={self.root})")
        return path

    def _blob_path(self, blob_sha: str) -> Path:
        p = self.root / "blobs" / blob_sha
        return self._ensure_within_root(p)

    def _ref_path(self, origin: Origin) -> Path:
        p = (
            self.root / "refs" / origin.host / origin.owner / origin.repo
            / origin.ref / (origin.path + ".json")
        )
        return self._ensure_within_root(p)

    def _read_ref(self, origin: Origin) -> dict | None:
        p = self._ref_path(origin)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _read_blob(self, blob_sha: str) -> bytes | None:
        p = self._blob_path(blob_sha)
        return p.read_bytes() if p.exists() else None

    def _write_blob(self, blob_sha: str, content: bytes) -> None:
        """Write ``content`` to the content-addressed blob path.

        No lock is needed: blobs are content-addressed, so any concurrent
        writer of this ``blob_sha`` is writing byte-identical content by
        construction. Each writer writes to its own unique temp path, then
        atomically ``os.replace``s it into place -- concurrent writers race
        harmlessly and the last rename simply wins with identical bytes.

        The "already exists, skip" fast path is only trustworthy if the
        existing file's bytes still match ``content`` -- an on-disk blob can
        become corrupted (bitrot, external tampering) independent of any
        writer here. Without this check, ``get()``'s 304-path self-heal
        (which re-verifies via ``git_blob_sha`` and re-fetches on mismatch)
        would fetch correct bytes but then silently fail to persist them,
        since this method would see the (corrupted) path already exists and
        return without ever overwriting it -- leaving the cache permanently
        corrupted for that blob and forcing a repeat self-heal fetch on every
        subsequent call.
        """
        p = self._blob_path(blob_sha)
        if p.exists():
            try:
                if p.read_bytes() == content:
                    return  # content-addressed: identical bytes stored once
            except OSError:
                pass  # unreadable existing file -- fall through and rewrite
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            tmp.write_bytes(content)
            os.replace(tmp, p)  # atomic rename; idempotent across writers
        finally:
            tmp.unlink(missing_ok=True)

    def _write_ref(self, origin: Origin, blob_sha: str, etag: str | None) -> None:
        p = self._ref_path(origin)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(
            json.dumps({"blob_sha": blob_sha, "etag": etag, "fetched_at": time.time()}),
            encoding="utf-8",
        )
        os.replace(tmp, p)

    async def get(
        self,
        origin: Origin,
        *,
        token: str | None = None,
        base_url: str | None = None,
        fetch_fn: FetchFn | None = None,
        reload: bool | None = None,
        limits: FetchLimits | None = None,
    ) -> tuple[bytes, str]:
        """Return (content, blob_sha) for ``origin``, honoring the cache.

        ``limits`` (e.g. ``per_request_timeout``) is forwarded to ``fetch_fn``
        so a caller-configured safety envelope actually reaches the HTTP
        request, not just the fetch-walk's own depth/file/byte bookkeeping.
        """
        fetch_fn = fetch_fn or fetch_blob
        reload = reload if reload is not None else bool(os.environ.get("ATTRACTOR_RELOAD"))
        entry = self._read_ref(origin)

        # Fast path: immutable ref already cached -> zero network.
        if entry and not reload and is_immutable(origin.ref):
            blob = self._read_blob(entry["blob_sha"])
            if blob is not None:
                return blob, entry["blob_sha"]

        etag = entry["etag"] if entry else None
        result = await fetch_fn(
            origin, token=token, base_url=base_url, etag=etag, limits=limits
        )

        if result.not_modified and entry:
            blob = self._read_blob(entry["blob_sha"])
            # Re-verify on every 304, not just on fresh 200s: a blob's path
            # *is* its sha, so re-hashing on read is cheap, and it's the only
            # thing standing between an on-disk bitrot/corruption event and
            # silently handing back wrong bytes forever (the 304 path never
            # touches the network, so nothing else would ever catch it).
            if blob is not None and git_blob_sha(blob) == entry["blob_sha"]:
                return blob, entry["blob_sha"]
            # Either the blob file is gone from disk (e.g. externally
            # deleted) or its content no longer hashes to its own filename
            # (corrupted). Self-heal with a single forced refetch that skips
            # the etag, guaranteeing a full 200 with content instead of
            # another 304. No loop: if this second fetch still yields no
            # content, fall through to the empty-result error below.
            result = await fetch_fn(
                origin, token=token, base_url=base_url, etag=None, limits=limits
            )

        if result.content is None or result.blob_sha is None:
            raise RemoteFetchError(f"empty fetch result for {origin.key()}")

        # Integrity: local blob-sha must match the server-declared sha.
        if result.server_sha and result.blob_sha != result.server_sha:
            raise RemoteFetchError(
                f"integrity check failed for {origin.owner}/{origin.repo}"
                f"#{origin.path}: local {result.blob_sha} != server {result.server_sha}"
            )
        # Defense in depth: recompute from bytes.
        if git_blob_sha(result.content) != result.blob_sha:
            raise RemoteFetchError(f"blob-sha recompute mismatch for {origin.key()}")

        self._write_blob(result.blob_sha, result.content)
        # Only write the ref once the blob is confirmed present on disk --
        # never write a ref pointing at a blob that isn't there (a dangling
        # ref would poison the cache permanently: is_immutable() fast-path
        # reads would keep resolving to a blob_sha with no file).
        if not self._blob_path(result.blob_sha).exists():
            raise RemoteFetchError(
                f"blob {result.blob_sha} missing on disk after write for "
                f"{origin.key()}; refusing to write a dangling ref"
            )
        self._write_ref(origin, result.blob_sha, result.etag)
        return result.content, result.blob_sha
