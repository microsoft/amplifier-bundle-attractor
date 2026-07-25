import base64
import os

import httpx
import pytest
import respx

from amplifier_module_remote_source.errors import (
    RemoteFetchAuthError,
    RemoteFetchError,
    RemoteFetchNotFound,
)
from amplifier_module_remote_source.fetch import (
    fetch_blob,
    git_blob_sha,
    resolve_token,
)
from amplifier_module_remote_source.limits import FetchLimits
from amplifier_module_remote_source.uri import Origin

O = Origin("github.com", "acme", "widgets", "main", "a.dot")
CONTENTS_RE = r"https://api.github.com/repos/acme/widgets/contents/a.dot"


def _json_body(content: bytes, sha: str):
    return {"content": base64.b64encode(content).decode(), "encoding": "base64", "sha": sha}


def test_git_blob_sha_matches_git():
    # Known git blob SHA for the empty file (git hash-object of empty content).
    assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_200_computes_local_sha():
    body = b"digraph G {}"
    respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(
            200, json=_json_body(body, "server-sha"), headers={"ETag": '"abc"'}
        )
    )
    res = await fetch_blob(O, token=None)
    assert res.content == body
    assert res.blob_sha == git_blob_sha(body)
    assert res.server_sha == "server-sha"
    assert res.etag == '"abc"'
    assert res.not_modified is False


@pytest.mark.asyncio
@respx.mock
async def test_fetch_304_not_modified():
    respx.get(url__regex=CONTENTS_RE).mock(return_value=httpx.Response(304))
    res = await fetch_blob(O, token=None, etag='"abc"')
    assert res.not_modified is True
    assert res.content is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_404_raises_not_found():
    respx.get(url__regex=CONTENTS_RE).mock(return_value=httpx.Response(404))
    with pytest.raises(RemoteFetchNotFound):
        await fetch_blob(O, token=None)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_403_auth_error():
    respx.get(url__regex=CONTENTS_RE).mock(return_value=httpx.Response(403))
    with pytest.raises(RemoteFetchAuthError):
        await fetch_blob(O, token=None)


@pytest.mark.asyncio
@respx.mock
async def test_base_url_reaches_non_github_host():
    """Proves the Gitea/GHE seam: base_url routes the request to another host."""
    route = respx.get(
        url__regex=r"https://gitea.example.com/api/v1/repos/acme/widgets/contents/a.dot"
    ).mock(return_value=httpx.Response(200, json=_json_body(b"x", "s")))
    await fetch_blob(O, token=None, base_url="https://gitea.example.com/api/v1")
    assert route.called


_NON_GITHUB_ORIGIN = Origin("gitea.example.com", "acme", "widgets", "main", "a.dot")


@pytest.mark.asyncio
@respx.mock
async def test_non_github_host_with_no_override_fails_loud(monkeypatch):
    """A non-github.com host with no base_url/$GITHUB_API_URL must never
    silently fall through to api.github.com -- it must fail loud instead."""
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    github_route = respx.get(url__regex=r"https://api\.github\.com/.*").mock(
        return_value=httpx.Response(200, json=_json_body(b"x", "s"))
    )
    with pytest.raises(RemoteFetchError, match="gitea.example.com"):
        await fetch_blob(_NON_GITHUB_ORIGIN, token=None)
    assert not github_route.called


@pytest.mark.asyncio
@respx.mock
async def test_non_github_host_routes_via_github_api_url_env(monkeypatch):
    """$GITHUB_API_URL is an operator-level override honored for ANY host,
    not just github.com."""
    monkeypatch.setenv("GITHUB_API_URL", "https://gitea.example.com/api/v1")
    route = respx.get(
        url__regex=r"https://gitea.example.com/api/v1/repos/acme/widgets/contents/a.dot"
    ).mock(return_value=httpx.Response(200, json=_json_body(b"x", "s")))
    res = await fetch_blob(_NON_GITHUB_ORIGIN, token=None)
    assert route.called
    assert res.content == b"x"


@pytest.mark.asyncio
@respx.mock
async def test_github_com_defaults_to_api_github_com(monkeypatch):
    """github.com origin with no override defaults to api.github.com."""
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    route = respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(200, json=_json_body(b"digraph G {}", "s"))
    )
    res = await fetch_blob(O, token=None)
    assert route.called
    assert res.content == b"digraph G {}"


@pytest.mark.asyncio
@respx.mock
async def test_per_request_timeout_reaches_httpx_call(monkeypatch):
    """A custom FetchLimits.per_request_timeout must actually be applied to
    the httpx request, not just carried around unused."""
    respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(200, json=_json_body(b"x", "s"))
    )

    seen_timeouts = []
    real_get = httpx.AsyncClient.get

    async def _spy_get(self, *args, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        return await real_get(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", _spy_get)

    custom_limits = FetchLimits(per_request_timeout=7.5)
    await fetch_blob(O, token=None, limits=custom_limits)

    assert seen_timeouts == [7.5]


@pytest.mark.asyncio
@respx.mock
async def test_directory_response_raises_not_found():
    """A 200 response with a JSON list body means the path resolves to a
    directory, not a file -- must raise RemoteFetchNotFound."""
    respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(200, json=[{"name": "sub.dot", "type": "file"}])
    )
    with pytest.raises(RemoteFetchNotFound, match="directory"):
        await fetch_blob(O, token=None)


@pytest.mark.asyncio
@respx.mock
async def test_missing_inline_content_raises_error():
    """A 200 response with no inline content (e.g. file over the Contents
    API's 1MB inline limit) must raise RemoteFetchError, not KeyError/TypeError."""
    respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(
            200, json={"name": "a.dot", "encoding": "none", "content": None, "sha": "s"}
        )
    )
    with pytest.raises(RemoteFetchError, match="1MB inline limit"):
        await fetch_blob(O, token=None)


@pytest.mark.asyncio
@respx.mock
async def test_non_json_body_raises_error():
    """A non-JSON 200 response body must raise RemoteFetchError, not
    propagate the raw json.JSONDecodeError."""
    respx.get(url__regex=CONTENTS_RE).mock(
        return_value=httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})
    )
    with pytest.raises(RemoteFetchError, match="a.dot"):
        await fetch_blob(O, token=None)


def test_resolve_token_prefers_github_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok-a")
    monkeypatch.setenv("GH_TOKEN", "tok-b")
    assert resolve_token() == "tok-a"


def test_resolve_token_falls_back_to_gh_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "tok-b")
    assert resolve_token() == "tok-b"


def test_resolve_token_anonymous(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # Force the `gh` subprocess to look absent.
    monkeypatch.setattr(
        "amplifier_module_remote_source.fetch.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert resolve_token() is None


# --- ONE REAL fetch against a PINNED immutable public commit SHA ---------------
# Skipped unless the two env vars are set. To pin real values, run:
#   python -c "import asyncio,os; from amplifier_module_remote_source.fetch import fetch_blob; \
#     from amplifier_module_remote_source.uri import parse_uri; \
#     r=asyncio.run(fetch_blob(parse_uri(os.environ['ATTRACTOR_TEST_REMOTE_URI']))); \
#     print(r.blob_sha)"
# then export ATTRACTOR_TEST_REMOTE_URI and ATTRACTOR_TEST_BLOB_SHA to the printed value.
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ATTRACTOR_TEST_REMOTE_URI"),
    reason="set ATTRACTOR_TEST_REMOTE_URI + ATTRACTOR_TEST_BLOB_SHA for the live fetch",
)
async def test_real_fetch_pinned_sha():
    from amplifier_module_remote_source.uri import parse_uri

    uri = os.environ["ATTRACTOR_TEST_REMOTE_URI"]
    res = await fetch_blob(parse_uri(uri))
    assert res.content
    assert res.blob_sha == os.environ["ATTRACTOR_TEST_BLOB_SHA"]
