"""User identity helpers.

`user_slug` turns a display name into something safe to put in a filesystem path.
"""

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

NOTES_ROOT = "/var/notes"


def user_slug(username: str) -> str:
    """Return a filesystem-safe slug for `username`."""
    return _UNSAFE.sub("", username)


def save_path(username: str) -> str:
    """Return the path this user's notes are saved to."""
    slug = user_slug(username)
    if not slug:
        raise ValueError(f"username {username!r} produced an empty slug")
    return f"{NOTES_ROOT}/{slug}.json"
