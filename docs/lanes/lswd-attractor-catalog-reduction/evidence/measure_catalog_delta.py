"""Measure what `disable-model-invocation: true` actually costs and saves.

Runs the LIVE `SkillsVisibilityHook._format_skills_list` (the renderer that
produces the `hooks-skills-visibility` system-reminder in every session) over a
real catalog, once with the two attractor skills model-invocable and once with
them hidden, and reports the byte/token delta plus which section each lands in.

Zero spend: no LLM call, no `amplifier` invocation, no network. It imports the
already-installed `amplifier_module_tool_skills` and reads SKILL.md files.

Run with the amplifier tool venv's interpreter:

    /home/bkrabach/.local/share/uv/tools/amplifier/bin/python3 \
        docs/lanes/lswd-attractor-catalog-reduction/evidence/measure_catalog_delta.py

Ambient catalog: the skills bundle's own `skills/` directory (the ~49 skills a
stock session carries) plus this repo's `skills/`. Marginal catalog: this
repo's two skills alone. Both are reported, because the marginal delta is the
honest number for "what did this change do" and the ambient one shows it in the
context where it is actually paid.
"""

from __future__ import annotations

import copy
import glob
import sys
from pathlib import Path

from amplifier_module_tool_skills.discovery import discover_skills
from amplifier_module_tool_skills.hooks import SkillsVisibilityHook

REPO = Path(__file__).resolve().parents[4]
REPO_SKILLS = REPO / "skills"

_bundle_matches = sorted(
    glob.glob(
        str(
            Path.home()
            / ".amplifier"
            / "cache"
            / "amplifier-bundle-skills-*"
            / "skills"
        )
    )
)
BUNDLE_SKILLS = Path(_bundle_matches[0]) if _bundle_matches else None

TARGETS = ("attractor-scout", "attractorify")


def render(catalog: dict) -> str:
    """Render the visibility block exactly as a live session would."""
    hook = SkillsVisibilityHook(skills=catalog, config={})
    return hook._format_skills_list(catalog)


def flipped(catalog: dict, value: bool) -> dict:
    """Copy the catalog with the two target skills' flag forced to `value`."""
    out = {name: copy.copy(meta) for name, meta in catalog.items()}
    for name in TARGETS:
        if name in out:
            out[name].disable_model_invocation = value
    return out


def report(label: str, catalog: dict) -> None:
    if not catalog:
        print(f"\n## {label}: EMPTY -- skipped")
        return

    missing = [n for n in TARGETS if n not in catalog]
    if missing:
        print(f"\n## {label}: missing {missing} -- skipped")
        return

    shown = render(flipped(catalog, False))
    hidden = render(flipped(catalog, True))

    print(f"\n## {label} ({len(catalog)} skills in catalog)")
    print(f"  block bytes, flag ABSENT (model-invocable) : {len(shown):>6}")
    print(f"  block bytes, flag TRUE   (user-invoked)    : {len(hidden):>6}")
    delta = len(hidden) - len(shown)
    print(f"  delta                                      : {delta:>+6} bytes "
          f"({delta // 4:+d} tokens at the hook's own 4 chars/token estimate)")

    for name in TARGETS:
        in_regular = f"- **{name}**" in shown.split("User-invoked skills")[0]
        after = hidden.split("User-invoked skills")
        in_user_invoked = len(after) > 1 and f"- **{name}**" in after[1]
        print(f"  {name:<16} before: "
              f"{'Available skills' if in_regular else 'NOT in Available'}"
              f"   after: "
              f"{'User-invoked skills' if in_user_invoked else 'NOT in User-invoked'}")


def main() -> int:
    print("# Catalog-visibility delta for disable-model-invocation: true")
    print(f"# repo   : {REPO}")
    print(f"# bundle : {BUNDLE_SKILLS}")

    repo_only = discover_skills(REPO_SKILLS)
    report("MARGINAL (this repo's skills only)", repo_only)

    if BUNDLE_SKILLS is not None:
        ambient = discover_skills(BUNDLE_SKILLS)
        ambient.update(repo_only)
        report("AMBIENT (stock skills bundle + this repo)", ambient)

    print("\n## Per-skill frontmatter, as the live discovery parses it")
    for name in TARGETS:
        meta = repo_only.get(name)
        if meta is None:
            print(f"  {name}: NOT DISCOVERED")
            continue
        print(
            f"  {name:<16} disable_model_invocation={meta.disable_model_invocation!r} "
            f"user_invocable={meta.user_invocable!r} context={meta.context!r} "
            f"description_chars={len(meta.description)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
