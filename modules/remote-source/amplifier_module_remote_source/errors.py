"""Resource-agnostic fetch error taxonomy (Layer A)."""

from __future__ import annotations


class RemoteFetchError(Exception):
    """Base for all remote-fetch failures.

    Also raised directly for transport/timeout errors, retry exhaustion, and
    integrity (blob-SHA mismatch) failures.
    """


class RemoteFetchAuthError(RemoteFetchError):
    """401/403 (non-rate-limit): names the repo and reminds about token scope."""


class RemoteFetchNotFound(RemoteFetchError):
    """404: names the exact owner/repo@ref#path (bad ref vs bad path)."""


class RemoteFetchLimitError(RemoteFetchError):
    """A FetchLimits budget breach (depth / files / bytes)."""


class RemoteFetchParseError(RemoteFetchError):
    """A fetched document failed to parse; names the origin.

    Layer A never parses DOT itself — this is raised by Layer B consumers, but
    the type lives here so the taxonomy is single-sourced.
    """


class RemoteFetchPathError(RemoteFetchError):
    """Rejected reference: malformed URI, disallowed scheme, absolute path, or a
    relative ref that escapes the origin root."""
