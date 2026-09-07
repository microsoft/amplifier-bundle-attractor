"""Catalog-hygiene guard for every rendered description this repo ships.

**What this guards, and why it is a SET sweep rather than per-file asserts.**

Two surfaces are injected into the head of *every* Amplifier session on *every*
turn, whether or not anything in this bundle is ever used:

  * the ``delegate`` tool's "Available agents:" catalog, built from each
    agent's ``meta.description``; and
  * the ``<system-reminder source="hooks-skills-visibility">`` block, built
    from each registered skill's ``description``.

A description is therefore **pay-per-turn**, while an agent/skill **body** is
pay-per-use.  Prose that belongs in the body -- worked examples, tutorials,
rationale -- costs every session that never invokes the agent at all.  Measured
on this repo (lane ``kp79-catalog-attractor``): moving ``attractor-expert``'s
three ``<example>``/``<commentary>`` blocks out of its description took the
rendered delegate catalog from 39,133 B to 37,632 B, and the two skill
descriptions took the skills-visibility block from 20,900 B to 20,708 B --
1,693 B off the head of every turn.

The sweep walks ``agents/*.md`` and ``skills/*/SKILL.md`` **as a set**,
because that is the only shape that catches an agent or skill added *later*.
A per-file test can only ever guard the files that existed when it was
written, which is exactly how the policy drifted in the first place.

**Empty-glob tripwire.**  A set sweep whose set is empty passes vacuously and
reports a false green.  ``test_the_sweep_is_not_vacuous`` fails loudly if
either glob stops matching -- a rename or a directory move must break this
guard, not silently disarm it.

**The thresholds are the ones the ecosystem policy states**, not local
invention: agent descriptions <= 600 chars with an explicit USE WHEN /
DO NOT USE WHEN, skill descriptions <= 400 chars and a single paragraph, and
zero ``<example>``/``<commentary>`` anywhere in either.  The 100-char floor
mirrors ``validate-agents``' own ``MIN_DESCRIPTION_LENGTH`` so a description
cannot be "shortened" into uselessness to satisfy the ceiling.

Retirement condition: this guard retires if and when the catalog renderer
itself strips example blocks and enforces the budgets at render time (tracked
upstream) -- at that point content hygiene is enforced by the thing doing the
rendering and no longer needs a per-repo guard.
"""

from pathlib import Path

import pytest
import yaml


def _find_bundle_root() -> Path | None:
    """Walk up from this file looking for the bundle repo root.

    Walks rather than hardcoding a parent count so the guard survives being
    re-nested, and returns None (-> module skip) rather than pointing at a
    plausible-but-wrong directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "agents").is_dir() and (candidate / "bundle.md").is_file():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()

pytestmark = pytest.mark.skipif(
    BUNDLE_ROOT is None,
    reason="not running inside the bundle repo checkout; nothing to guard",
)

# Budgets, in characters of the PARSED description (not of the file).
AGENT_MAX_CHARS = 600
SKILL_MAX_CHARS = 400
MIN_CHARS = 100  # validate-agents' MIN_DESCRIPTION_LENGTH

# Blocks that must never appear in a rendered description. They are worked
# examples: body material, and the single largest source of catalog bloat.
BANNED_MARKUP = ("<example>", "</example>", "<commentary>", "</commentary>")


def _agent_files() -> list[Path]:
    return sorted((BUNDLE_ROOT / "agents").glob("*.md"))


def _skill_files() -> list[Path]:
    return sorted((BUNDLE_ROOT / "skills").glob("*/SKILL.md"))


def _frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter of a markdown file, or {} if it has none."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}
    return yaml.safe_load(text[4 : end + 1]) or {}


def _description(path: Path) -> str | None:
    """The description a catalog would render for this file.

    Agent files nest it under ``meta:``; skill files put it at the top level.
    Returns None when the file declares none at all, which the caller treats
    as "not a catalog-rendered file" rather than as a violation.
    """
    data = _frontmatter(path)
    if not isinstance(data, dict):
        return None
    meta = data.get("meta")
    source = meta if isinstance(meta, dict) else data
    value = source.get("description")
    return value.strip() if isinstance(value, str) else None


def _rel(path: Path) -> str:
    return str(path.relative_to(BUNDLE_ROOT))


# ---------------------------------------------------------------------------
# The tripwire: a sweep over an empty set is a false green, not a pass.
# ---------------------------------------------------------------------------


def test_the_sweep_is_not_vacuous():
    agents, skills = _agent_files(), _skill_files()
    assert agents, (
        "agents/*.md matched NOTHING -- the agent sweep below would pass "
        "vacuously. Either the directory moved (re-anchor this guard) or the "
        "agents were deleted (delete this guard deliberately)."
    )
    assert skills, (
        "skills/*/SKILL.md matched NOTHING -- the skill sweep below would pass "
        "vacuously. Either the layout changed (re-anchor this guard) or the "
        "skills were deleted (delete this guard deliberately)."
    )


# ---------------------------------------------------------------------------
# Agent descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _agent_files(), ids=_rel)
def test_agent_description_is_within_budget(path: Path):
    description = _description(path)
    assert description is not None, f"{_rel(path)}: no meta.description to render"
    length = len(description)
    assert MIN_CHARS <= length <= AGENT_MAX_CHARS, (
        f"{_rel(path)}: description is {length} chars; the rendered delegate "
        f"catalog budget is {MIN_CHARS}-{AGENT_MAX_CHARS}. Tutorials, worked "
        f"examples and rationale belong in the BODY (pay-per-use); the "
        f"description is pay-per-turn."
    )


@pytest.mark.parametrize("path", _agent_files(), ids=_rel)
def test_agent_description_carries_both_routing_clauses(path: Path):
    description = _description(path)
    assert description is not None, f"{_rel(path)}: no meta.description to render"
    upper = description.upper()
    assert "USE WHEN" in upper, (
        f"{_rel(path)}: description has no USE WHEN clause. A catalog row that "
        f"says what an agent IS but not when to reach for it cannot route."
    )
    assert "DO NOT USE WHEN" in upper, (
        f"{_rel(path)}: description has no DO NOT USE WHEN clause. The negative "
        f"boundary is what stops a near-miss delegation, and it is the routing "
        f"fact most descriptions omit."
    )


# ---------------------------------------------------------------------------
# Skill descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _skill_files(), ids=_rel)
def test_skill_description_is_within_budget(path: Path):
    description = _description(path)
    assert description is not None, f"{_rel(path)}: no description to render"
    length = len(description)
    assert MIN_CHARS <= length <= SKILL_MAX_CHARS, (
        f"{_rel(path)}: description is {length} chars; the rendered "
        f"hooks-skills-visibility budget is {MIN_CHARS}-{SKILL_MAX_CHARS}. The "
        f"skill BODY is loaded on demand and is where detail belongs."
    )


@pytest.mark.parametrize("path", _skill_files(), ids=_rel)
def test_skill_description_is_a_single_paragraph(path: Path):
    description = _description(path)
    assert description is not None, f"{_rel(path)}: no description to render"
    assert "\n\n" not in description, (
        f"{_rel(path)}: description contains a blank line, so it renders as "
        f"multiple paragraphs in the skills-visibility catalog. A skill "
        f"description is one paragraph; structure belongs in the body."
    )


# ---------------------------------------------------------------------------
# Both surfaces: no worked examples in a pay-per-turn string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _agent_files() + _skill_files(), ids=_rel)
def test_description_carries_no_example_markup(path: Path):
    description = _description(path)
    if description is None:
        pytest.skip(f"{_rel(path)} declares no description")
    found = [tag for tag in BANNED_MARKUP if tag in description]
    assert not found, (
        f"{_rel(path)}: description contains {', '.join(found)}. "
        f"<example>/<commentary> blocks are worked examples -- they are "
        f"structural ERRORs under validate-agents and they are billed to every "
        f"session on every turn. Move them into the body."
    )
