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


def parse_uri(uri: str) -> Origin:
    """Parse ``git+https://<host>/<owner>/<repo>[@<ref>]#subdirectory=<file-path>``.

    ``ref`` defaults to ``main``. Raises ``RemoteFetchPathError`` on malformed
    input. Any well-formed host is accepted (host allow-listing is not done
    here — the fetch layer controls which base URL is actually contacted).
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
        path = unquote(value).lstrip("/")
    if not path:
        raise RemoteFetchPathError(
            f"Entry URI must name a file via #subdirectory=: {uri!r}"
        )

    return Origin(host=host, owner=owner, repo=repo, ref=ref, path=path)
