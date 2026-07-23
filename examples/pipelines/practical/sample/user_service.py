"""A tiny in-memory user service -- the shared target for the practical pipelines.

This module is intentionally imperfect so the example pipelines have something
real to work on. It ships with three planted problems:

  1. A latent BUG   -- `get_display_name()` raises TypeError when a user's
     avatar is None (see bug-fix.dot). The shipped tests never exercise that
     path, so the suite is green and the bug stays hidden until reproduced.
  2. A CODE SMELL   -- `validate_user()` is a long, deeply-nested method with
     duplicated validation blocks (see refactor.dot).
  3. THIN COVERAGE  -- test_user_service.py covers only the happy path, leaving
     the None-avatar path, the missing-user path, and all of validate_user()
     untested (see test-gen.dot).

Keep it flat (no __init__.py) so `import user_service` works when pytest is run
from inside a copy of this directory.
"""

from dataclasses import dataclass, field


@dataclass
class User:
    username: str
    email: str
    avatar: str | None = None
    roles: list[str] = field(default_factory=list)


class UserService:
    """A minimal username-keyed user store."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def add_user(self, user: User) -> None:
        self._users[user.username] = user

    def get_user(self, username: str) -> User | None:
        return self._users.get(username)

    def get_display_name(self, username: str) -> str:
        """Return a display string like ``alice [avatar: a1b2]``.

        BUG: this assumes ``avatar`` is always set. When a user was created
        without an avatar (``avatar=None``), ``user.avatar[:4]`` raises
        ``TypeError: 'NoneType' object is not subscriptable``.
        """
        user = self._users[username]
        return f"{user.username} [avatar: {user.avatar[:4]}]"

    def validate_user(self, user: User) -> list[str]:
        """Validate a user and return a list of human-readable error strings.

        CODE SMELL: long method, deep nesting, and duplicated shape between the
        username and email validation blocks. A good refactor extracts the
        repeated "required / non-empty / rule" ladder into a helper without
        changing behavior.
        """
        errors: list[str] = []

        if user.username is not None:
            if len(user.username) > 0:
                if len(user.username) < 3:
                    errors.append("username too short")
                else:
                    if not user.username.isalnum():
                        errors.append("username must be alphanumeric")
            else:
                errors.append("username is empty")
        else:
            errors.append("username is required")

        if user.email is not None:
            if len(user.email) > 0:
                if "@" not in user.email:
                    errors.append("email must contain @")
                else:
                    if "." not in user.email.split("@")[1]:
                        errors.append("email domain invalid")
            else:
                errors.append("email is empty")
        else:
            errors.append("email is required")

        return errors
