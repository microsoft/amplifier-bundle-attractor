"""Catalog guard: the hand-run skills stay OUT of the always-injected catalog.

# --- Root guard harness (DESIGN-repo-split.md Track A, section 1.4/5#2). This
# guard asserts on the OPINIONATED layer (repo-root docs/examples/skills/
# agents/context/bundles/behaviors), not on engine behavior, so it lives in
# the repo-root `tests/` suite and installs nothing but pytest + pyyaml. ---

**What this protects, and why it is worth a test.**  Both skills in this repo
are ~30 KB authoring tools that a human starts by hand; neither had ever been
model-invoked (zero all-time loads in the program usage table).
`disable-model-invocation: true` is what makes that permanent.  In
`amplifier_module_tool_skills` the field is read by `discovery.py` and consumed
by `hooks.py`, which partitions the visibility reminder into two sections:

    hooks.py:412  not meta.disable_model_invocation -> "Available skills"
    hooks.py:417      meta.disable_model_invocation -> "User-invoked skills"

**Do not sell this as a catalog-byte saving -- it measurably is not one.**  The
lane rendered the live `SkillsVisibilityHook._format_skills_list` over the real
catalog with the flag off and on: **0 bytes** difference over a 40-skill
catalog, **+6 bytes** over this repo's two skills alone (the second section's
header appears).  Both sections emit one line per skill under the same
180-char cap, so moving between them is free, and the 2,500-token budget that
bounds only the regular section was not binding at either catalog size.
Evidence and the reproduction script:
``docs/lanes/lswd-attractor-catalog-reduction/evidence/``.

What the field actually buys is the *tail*: the model can no longer decide on
its own to load a ~30 KB body (~7.5k tokens) inline in the middle of an
unrelated task.  And it costs nothing, because the flag is not read anywhere on
the load path -- `SkillsTool._load_skill` gates fork execution on
`metadata.context == "fork"` and nothing else, so a hand-run skill carrying
this flag still loads in full, inline, when it is named directly.  That is the
whole trade: unreachable by the model, unchanged for the human.

Owner directive, 2026-09-07: *"all of those authoring ones are ones we run by
hand, so they can be hidden."*  Work item `model_performance-lswd`.

**The assertions, and what each would catch.**

  S-300  Both hand-run skills carry `disable-model-invocation: true`.
         Catches the flag being dropped in an edit -- the regression that puts
         a 30 KB hand-run skill back within the model's autonomous reach.

  S-301  The flag is a real YAML boolean, never a string.
         `discovery.py:297` coerces a non-bool with `bool(...)`, under which
         the *string* `"false"` is truthy.  A quoted value would therefore
         "work" today and silently invert the day someone tries to turn the
         flag off.  Fail on the quote, not on the inversion.

  S-302  Both stay `user-invocable: true`.
         Hiding a skill from the model is only acceptable because the human
         retains a slash command.  Dropping this field alongside the new one
         would strand the skill with no invocation path at all.

  S-303  Neither declares `context: fork`.
         Both SKILL.md bodies state that they must run inline to see the
         current session (attractorify's whole job is analysing it).  A future
         edit to `context: fork` would combine with S-300 into a skill that is
         invisible to the model *and* blind when invoked -- the failure mode
         worth naming before it happens.

  S-304  The set of skills is exactly the two known hand-run ones.
         Decision-forcing, deliberately.  A model-reachable surface regrows one
         well-intentioned skill at a time; a third SKILL.md appearing here
         should make its author state, in this file, whether the model is
         meant to reach for it.  Adding a genuinely model-invocable skill is a
         one-line edit to `MODEL_INVOCABLE` below.

Honest limits:
  - This guard reads frontmatter, not the running `tool-skills` module.  CI for
    this repo installs pytest and pyyaml only (see `.github/workflows/ci.yml`,
    `opinionated-guards`), and the repo does not depend on the skills bundle.
    The behavioural half -- that both skills still load by name post-change --
    was verified in a real session against the live module and recorded in
    `docs/lanes/lswd-attractor-catalog-reduction/`.
  - S-300..S-303 are one-sided (they assert on this repo's own files, with no
    second source to cross-check against) because the value under guard IS an
    owner policy decision about these files, not a number derived from code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Locating the repo root and the skills
# ---------------------------------------------------------------------------


def _find_bundle_root() -> Path | None:
    """Walk up from this file looking for the bundle repo root.

    Walks rather than hardcoding a parent count so the guard survives being
    vendored or re-nested, and returns None (-> module skip) rather than
    pointing at a plausible-but-wrong directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "skills").is_dir() and (candidate / "bundle.md").is_file():
            return candidate
    return None


_ROOT = _find_bundle_root()

if _ROOT is None:  # pragma: no cover - partial checkout
    pytest.skip("bundle root not found", allow_module_level=True)

SKILLS_DIR = _ROOT / "skills"

#: Skills a human starts by hand. Out of the model's autonomous reach, still
#: reachable by name / slash command. Owner directive 2026-09-07.
HAND_RUN = ("attractor-scout", "attractorify")

#: Skills the model is meant to reach for on its own. Empty today, and that is
#: the point of S-304: a new entry here is a deliberate statement that the
#: model may load this body, unprompted, in the middle of someone else's task.
MODEL_INVOCABLE: tuple[str, ...] = ()


def _frontmatter(skill_name: str) -> dict:
    """Parse the YAML frontmatter block of skills/<name>/SKILL.md."""
    path = SKILLS_DIR / skill_name / "SKILL.md"
    assert path.is_file(), f"{path} is missing"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter block"
    _, _, rest = text.partition("---\n")
    block, sep, _ = rest.partition("\n---")
    assert sep, f"{path} frontmatter block is unterminated"
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict), f"{path} frontmatter did not parse to a mapping"
    return parsed


# ---------------------------------------------------------------------------
# S-300 / S-301 -- hidden from the model-facing catalog, and hidden honestly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", HAND_RUN)
def test_s300_hand_run_skill_is_not_model_invocable(skill_name: str) -> None:
    fm = _frontmatter(skill_name)
    assert "disable-model-invocation" in fm, (
        f"skills/{skill_name}/SKILL.md must carry `disable-model-invocation: true`. "
        f"It is a hand-run authoring skill (owner directive 2026-09-07, "
        f"model_performance-lswd); without the field the model may load its "
        f"~30 KB body inline, on its own initiative, mid-task."
    )
    assert fm["disable-model-invocation"] is True, (
        f"skills/{skill_name}/SKILL.md sets `disable-model-invocation: "
        f"{fm['disable-model-invocation']!r}`; it must be the boolean `true`."
    )


@pytest.mark.parametrize("skill_name", HAND_RUN)
def test_s301_flag_is_a_real_boolean_not_a_string(skill_name: str) -> None:
    fm = _frontmatter(skill_name)
    # Report the absence plainly and point at the check that owns it, rather
    # than surfacing a KeyError that names nothing (the PRN-003/004 lesson).
    assert "disable-model-invocation" in fm, (
        f"`disable-model-invocation` is absent from skills/{skill_name}/"
        f"SKILL.md -- see S-300, which is the check that owns this."
    )
    raw = fm["disable-model-invocation"]
    assert isinstance(raw, bool), (
        f"skills/{skill_name}/SKILL.md quotes `disable-model-invocation` as "
        f"{raw!r}. discovery.py coerces a non-bool with bool(...), under which "
        f"the string 'false' is TRUE -- a quoted value works today and inverts "
        f"silently the day someone tries to turn it off. Unquote it."
    )


# ---------------------------------------------------------------------------
# S-302 / S-303 -- the human's invocation path survives the hiding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", HAND_RUN)
def test_s302_hand_run_skill_stays_user_invocable(skill_name: str) -> None:
    fm = _frontmatter(skill_name)
    assert fm.get("user-invocable") is True, (
        f"skills/{skill_name}/SKILL.md must keep `user-invocable: true`. It is "
        f"hidden from the model; the slash command is the only invocation path "
        f"left, and dropping it would strand the skill entirely."
    )


@pytest.mark.parametrize("skill_name", HAND_RUN)
def test_s303_hand_run_skill_stays_inline(skill_name: str) -> None:
    fm = _frontmatter(skill_name)
    assert fm.get("context") != "fork", (
        f"skills/{skill_name}/SKILL.md declares `context: fork`. Both skills "
        f"document that they must run INLINE to see the current session "
        f"(attractorify analyses it; attractor-scout resolves the repo's "
        f"AGENTS.md and output path). Forking it while it is also hidden from "
        f"the model leaves a skill that is invisible AND blind."
    )


# ---------------------------------------------------------------------------
# S-304 -- the model-reachable surface does not regrow by accident
# ---------------------------------------------------------------------------


def test_s304_every_shipped_skill_has_a_declared_visibility() -> None:
    on_disk = {
        entry.name
        for entry in SKILLS_DIR.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    }
    declared = set(HAND_RUN) | set(MODEL_INVOCABLE)
    assert on_disk == declared, (
        f"skills/ on disk is {sorted(on_disk)}, declared here is "
        f"{sorted(declared)}. Every shipped skill is loadable by the model on "
        f"its own initiative unless it sets `disable-model-invocation: true`. "
        f"Add a new skill to HAND_RUN (slash-command / by-name only) or to "
        f"MODEL_INVOCABLE (the model may reach for it, unprompted, mid-task) "
        f"-- deliberately, not by default."
    )
