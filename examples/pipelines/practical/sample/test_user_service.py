"""Thin, happy-path-only tests for user_service.

These pass out of the box (green baseline) but deliberately leave gaps:
  - get_display_name() is only tested WITH an avatar (the None path -- the
    planted bug -- is untested)
  - get_user() miss (unknown username) is untested
  - validate_user() is entirely untested

The test-gen pipeline exists to close gaps like these; the bug-fix pipeline
adds the missing None-avatar regression test; the refactor pipeline uses this
suite as its behavior-preservation snapshot.
"""

from user_service import User, UserService


def test_add_and_get_user():
    svc = UserService()
    user = User(username="alice", email="alice@example.com", avatar="a1b2c3d4")
    svc.add_user(user)
    assert svc.get_user("alice") is user


def test_display_name_with_avatar():
    svc = UserService()
    svc.add_user(User(username="bob", email="bob@example.com", avatar="deadbeef"))
    assert svc.get_display_name("bob") == "bob [avatar: dead]"
