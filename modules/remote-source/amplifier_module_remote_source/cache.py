"""Greenfield content-addressed blob cache (Layer A).

Layout:
  <root>/blobs/<blob-sha>                       -- content, dedup + integrity
  <root>/refs/<host>/<owner>/<repo>/<ref>/<path>.json -> {blob_sha, etag, fetched_at}

Freshness:
  * immutable ref (40-hex sha) -> served from cache, zero network ever
  * mutable ref (branch/tag name) -> revalidated via If-None-Match (304 = reuse)
  * $ATTRACTOR_RELOAD forces revalidation even for immutable entries
Integrity: fetched bytes MUST hash to the server-declared sha, else rejected.
Concurrency: per-blob lock file + atomic rename.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .errors import RemoteFetchError
from .fetch import FetchResult, fetch_blob, git_blob_sha
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

    def _blob_path(self, blob_sha: str) -> Path:
        return self.root / "blobs" / blob_sha

    def _ref_path(self, origin: Origin) -> Path:
        return (
            self.root / "refs" / origin.host / origin.owner / origin.repo
            / origin.ref / (origin.path + ".json")
        )

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
        p = self._blob_path(blob_sha)
        if p.exists():
            return  # content-addressed: identical bytes stored once
        p.parent.mkdir(parents=True, exist_ok=True)
        lock = p.with_suffix(".lock")
        tmp = p.with_suffix(f".tmp.{os.getpid()}")
        try:
            # per-blob lock: exclusive create; if held, another writer wins.
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                return
            tmp.write_bytes(content)
            os.replace(tmp, p)  # atomic rename
        finally:
            tmp.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)

    def _write_ref(self, origin: Origin, blob_sha: str, etag: str | None) -> None:
        p = self._ref_path(origin)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp.{os.getpid()}")
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
    ) -> tuple[bytes, str]:
        """Return (content, blob_sha) for ``origin``, honoring the cache."""
        fetch_fn = fetch_fn or fetch_blob
        reload = reload if reload is not None else bool(os.environ.get("ATTRACTOR_RELOAD"))
        entry = self._read_ref(origin)

        # Fast path: immutable ref already cached -> zero network.
        if entry and not reload and is_immutable(origin.ref):
            blob = self._read_blob(entry["blob_sha"])
            if blob is not None:
                return blob, entry["blob_sha"]

        etag = entry["etag"] if entry else None
        result = await fetch_fn(origin, token=token, base_url=base_url, etag=etag)

        if result.not_modified and entry:
            blob = self._read_blob(entry["blob_sha"])
            if blob is not None:
                return blob, entry["blob_sha"]

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
        self._write_ref(origin, result.blob_sha, result.etag)
        return result.content, result.blob_sha
