import pytest

from notesvc import save_path


def test_ascii_username_is_unchanged():
    """The behavior a fix must not break."""
    assert save_path("brian.k") == "/var/notes/brian.k.json"


def test_emoji_only_username_still_has_a_save_path():
    """A user whose whole display name is emoji still has to be able to save."""
    assert save_path("\U0001f389\U0001f389").endswith(".json")


def test_distinct_usernames_do_not_collide():
    """Two different non-ASCII usernames must not map to the same file."""
    assert save_path("\U0001f389\U0001f389") != save_path("\U0001f680\U0001f680")


def test_ascii_and_emoji_do_not_collide():
    """Stripping must not silently merge a unicode name into an ASCII one."""
    assert save_path("party") != save_path("party\U0001f389")


@pytest.mark.parametrize("name", ["a", "a-b", "a_b", "a.b"])
def test_simple_ascii_names_round_trip(name):
    assert save_path(name) == f"/var/notes/{name}.json"
