"""Layer A: resource-agnostic content-addressed caching git+https:// fetcher.

Public surface. No DOT knowledge lives here (that is Layer B, inside
loop-pipeline). httpx is the only runtime dependency.
"""

from __future__ import annotations

from .cache import BlobCache, default_cache_root, is_immutable
from .errors import (
    RemoteFetchAuthError,
    RemoteFetchError,
    RemoteFetchLimitError,
    RemoteFetchNotFound,
    RemoteFetchParseError,
    RemoteFetchPathError,
)
from .fetch import FetchResult, fetch_blob, git_blob_sha, resolve_token
from .limits import FetchLimits
from .uri import GIT_HTTPS_PREFIX, Origin, parse_uri

__all__ = [
    "BlobCache",
    "FetchLimits",
    "FetchResult",
    "GIT_HTTPS_PREFIX",
    "Origin",
    "RemoteFetchAuthError",
    "RemoteFetchError",
    "RemoteFetchLimitError",
    "RemoteFetchNotFound",
    "RemoteFetchParseError",
    "RemoteFetchPathError",
    "default_cache_root",
    "fetch_blob",
    "git_blob_sha",
    "is_immutable",
    "parse_uri",
    "resolve_token",
]
