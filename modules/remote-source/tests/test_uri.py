import pytest

from amplifier_module_remote_source.errors import RemoteFetchPathError
from amplifier_module_remote_source.uri import Origin, parse_uri


def test_parse_basic_defaults_ref_to_main():
    o = parse_uri("git+https://github.com/acme/widgets#subdirectory=pipelines/main.dot")
    assert o == Origin("github.com", "acme", "widgets", "main", "pipelines/main.dot")
    assert o.dir == "pipelines"


def test_parse_explicit_ref():
    o = parse_uri("git+https://github.com/acme/widgets@v2#subdirectory=a.dot")
    assert o.ref == "v2"
    assert o.path == "a.dot"
    assert o.dir == ""


def test_parse_non_github_host_accepted():
    o = parse_uri("git+https://gitea.example.com/acme/widgets#subdirectory=a.dot")
    assert o.host == "gitea.example.com"


def test_key_includes_host():
    o = parse_uri("git+https://h/o/r@ref#subdirectory=p/f.dot")
    assert o.key() == ("h", "o", "r", "ref", "p/f.dot")


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/acme/widgets#subdirectory=a.dot",
        "git+https://github.com/acme#subdirectory=a.dot",
        "git+https://github.com/acme/widgets",
        "git+https://github.com/acme/widgets#branch=main",
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(RemoteFetchPathError):
        parse_uri(bad)
