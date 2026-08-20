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

# Directory (beside the HTML artifact) where demonstrated pipelines are
# published: `<output_dir>/<DEMO_DIR_STEM>/<slug>.dot` + `.md`. Derived from
# SKILL_NAME so the single-naming-source rule survives the demo layer.
DEMO_DIR_STEM = f"{SKILL_NAME}-demos"

__all__ = ["ARTIFACT_STEM", "DEMO_DIR_STEM", "SKILL_NAME", "SKILL_TITLE"]
