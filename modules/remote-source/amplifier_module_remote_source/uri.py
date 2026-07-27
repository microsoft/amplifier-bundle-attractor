"""git+https:// URI grammar (Layer A, resource-agnostic — no DOT knowledge)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from urllib.parse import unquote

from .errors import RemoteFetchPathError

GIT_HTTPS_PREFIX = "git+https://"


@dataclass(frozen=True)
class Origin:
    """A fetched file's origin. ``path`` is the file path within the repo
    (POSIX, no leading slash). ``dir`` is its parent dir within the repo."""

    host: str
    owner: str
    repo: str
    ref: str
    path: str

    @property
    def dir(self) -> str:
        return posixpath.dirname(self.path)

    def key(self) -> tuple[str, str, str, str, str]:
        """Cycle/dedupe identity: a specific file in a specific host/repo@ref."""
        return (self.host, self.owner, self.repo, self.ref, self.path)


def _reject_single_segment_traversal(uri: str, name: str, value: str) -> None:
    """Validate a single path-tree component (host/owner/repo) — no '/'."""
    if not value or value in (".", ".."):
        raise RemoteFetchPathError(f"Invalid {name} segment {value!r} in {uri!r}")
    if "\x00" in value or "\\" in value:
        raise RemoteFetchPathError(f"Invalid characters in {name} {value!r} in {uri!r}")


def _reject_multi_segment_traversal(uri: str, name: str, value: str) -> None:
    """Validate a possibly multi-segment (POSIX '/'-separated) field.

    Rejects absolute paths, NUL/backslash, and any '..' path segment. Plain
    '/'-separated relative paths like ``a/b/c.dot`` remain valid.
    """
    if not value:
        raise RemoteFetchPathError(f"Missing {name} in {uri!r}")
    if value.startswith("/"):
        raise RemoteFetchPathError(f"{name} must be relative, got {value!r} in {uri!r}")
    if "\x00" in value or "\\" in value:
        raise RemoteFetchPathError(f"Invalid characters in {name} {value!r} in {uri!r}")
    for seg in value.split("/"):
        if seg == "..":
            raise RemoteFetchPathError(
                f"Path traversal ('..') rejected in {name} {value!r} of {uri!r}"
            )


def parse_uri(uri: str) -> Origin:
    """Parse ``git+https://<host>/<owner>/<repo>[@<ref>]#subdirectory=<file-path>``.

    ``ref`` defaults to ``main``. Raises ``RemoteFetchPathError`` on malformed
    input. Any well-formed host is accepted (host allow-listing is not done
    here) -- the host is captured on the ``Origin``, but the fetch layer does
    NOT auto-derive a per-host API base URL. For ``github.com`` it defaults to
    the public GitHub API; for any other host it requires an explicit base
    URL (``$GITHUB_API_URL`` or ``base_url=``) and raises loudly otherwise --
    it never silently falls back to contacting github.com.

    Every component (host, owner, repo, ref, path) is validated to reject
    path traversal ('..' segments), absolute paths, and NUL/backslash
    characters before an ``Origin`` is constructed — those components are
    later joined directly into cache filesystem paths (see ``cache.py``), so
    they must never be able to escape the cache root.
    """
    if not uri.startswith(GIT_HTTPS_PREFIX):
        raise RemoteFetchPathError(f"Not a git+https:// URI: {uri!r}")
    rest = uri[len(GIT_HTTPS_PREFIX):]

    base, _, fragment = rest.partition("#")
    segments = base.split("/")
    if len(segments) != 3 or not all(segments):
        raise RemoteFetchPathError(
            f"Malformed git+https:// URI (need exactly host/owner/repo): {uri!r}"
        )

    host, owner, repo_and_ref = segments
    repo, at, ref = repo_and_ref.partition("@")
    if not at:
        ref = "main"
    if not repo:
        raise RemoteFetchPathError(f"Missing repo in git+https:// URI: {uri!r}")

    path = ""
    if fragment:
        key, _, value = fragment.partition("=")
        if key != "subdirectory":
            raise RemoteFetchPathError(
                f"Only #subdirectory= is supported, got {fragment!r} in {uri!r}"
            )
        raw_path = unquote(value)
        if raw_path.startswith("/"):
            raise RemoteFetchPathError(
                f"subdirectory path must be relative, got {raw_path!r} in {uri!r}"
            )
        path = raw_path.lstrip("/")
    if not path:
        raise RemoteFetchPathError(
            f"Entry URI must name a file via #subdirectory=: {uri!r}"
        )

    _reject_single_segment_traversal(uri, "host", host)
    _reject_single_segment_traversal(uri, "owner", owner)
    _reject_single_segment_traversal(uri, "repo", repo)
    _reject_multi_segment_traversal(uri, "ref", ref)
    _reject_multi_segment_traversal(uri, "path", path)

    return Origin(host=host, owner=owner, repo=repo, ref=ref, path=path)
