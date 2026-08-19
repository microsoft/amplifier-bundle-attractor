"""Single source of truth for the skill's working name.

The name `attractor-scout` is a WORKING NAME (Phase-4 Stage-1). Renaming the
skill means changing `SKILL_NAME` here and renaming the `skills/<name>/`
directory + the `name:` field in SKILL.md. Nothing else in the library, CLI,
tests, fixtures, or renderer hardcodes the string.
"""

from __future__ import annotations

SKILL_NAME = "attractor-scout"
SKILL_TITLE = "Attractor Scout"

# Artifact filename stem used by render.py (cwd-relative by default).
ARTIFACT_STEM = SKILL_NAME

__all__ = ["ARTIFACT_STEM", "SKILL_NAME", "SKILL_TITLE"]
