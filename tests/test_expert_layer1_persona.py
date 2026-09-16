"""Layer-1 persona guards for ``attractor-expert`` -- XP-001..XP-007.

WHY THIS TEST EXISTS
--------------------
The packaged expert could not start.  ``agents/attractor-expert.md`` declared
its Layer-1 persona as::

    system_prompt_file: context/system-attractor-expert.md

and loop-agent resolves a RELATIVE ``system_prompt_file`` against **its own
installed bundle root** -- ``parents[3]`` of its ``__init__.py``, then an
upward ancestor walk -- never against the bundle that declared the value.
Because this agent's ``session.orchestrator.source`` deliberately mounts
loop-agent from the SEPARATE ``amplifier-bundle-dot-runner`` package (that
mount is what stops a pipeline parent's loop-pipeline from recursing into the
child), the anchor is the dot-runner install, where an Attractor-owned
``context/`` asset does not exist.  Measured, against the real installed
resolver::

    FileNotFoundError: system_prompt_file 'context/system-attractor-expert.md'
    (relative) could not be resolved to an existing file. Expected it at the
    bundle root: <dot-runner-install-root>/context/system-attractor-expert.md

The repair uses the channel loop-agent already supports at HIGHER precedence
(explicit ``system_prompt`` > explicit ``system_prompt_file`` > provider
default > fail-loud): the persona text is carried INLINE, so there is no path
left to anchor wrongly.  The side-file is retired rather than kept beside it --
two independently-maintained Layer-1 owners is the drift this repo names as a
recurring bug class ("lossy reconstruction" / "partial-coverage symmetry",
``docs/designs/RECURRING-BUG-CLASSES.md``).

WHAT THESE CHECKS CAN AND CANNOT PROVE
--------------------------------------
Honest scope, stated up front because the distinction is the whole point of
this repo's verification gradient (``AGENTS.md``): these are **parse-level**
guards.  They prove the packaged profile PARSES to the exact intended persona
and body, from outside the repo, with no host path baked in.  Parsing was
never the broken thing, so they do **not** prove the packaged expert now
SPAWNS.  That claim needs a real packaged public-path run; the DTU probe for
it is the manager's, and is recorded in this lane's handoff.  A green suite
here is necessary, not sufficient -- "the test passes" is not "it works"
(``docs/VISION.md``).

Checks:

  XP-001  the expert declares an INLINE ``system_prompt`` (a literal string),
          and declares NO ``system_prompt_file`` -> the un-anchorable channel
  XP-002  the parsed persona is byte-identical to the recorded asset -> the
          persona was MOVED, never paraphrased or re-typed
  XP-003  the persona is the non-coding consultant base, not a provider coding
          default -> the explicitly rejected substitution cannot pass silently
  XP-004  the retired side-file is gone, and no live surface still DECLARES
          it as Layer-1 -> one Layer-1 owner, no stale declaration
  XP-005  the orchestrator still mounts loop-agent by an absolute ``git+``
          source -> the anti-recursion mount survives the repair
  XP-006  the Markdown expert body survives beside the frontmatter persona ->
          the knowledge body is a separate concern and was not swallowed
  XP-007  parsing works with CWD outside the repo and embeds no absolute
          host/cache path -> packaged installs, not this checkout

The persona is pinned by a recorded SHA-256 rather than by comparison against
a second file ON PURPOSE.  Comparing the shipped value against a copy in the
tree would re-create the two-owner drift this change removes, and would pass
vacuously if both sides were edited together.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_PATH = REPO_ROOT / "agents" / "attractor-expert.md"
RETIRED_SIDE_FILE = REPO_ROOT / "context" / "system-attractor-expert.md"

# The persona as it stood at fa630e0, immediately before the move.  A change to
# the persona MUST update this constant in the same commit -- that is the point:
# an accidental edit, a re-wrap, a "helpful" tidy, or a provider-coding-base
# substitution all fail here loudly instead of shipping silently.
PERSONA_SHA256 = "07e24526081f0b76888e984895f44d753b381f4cf1de66e4f2c4c8c01404b487"
PERSONA_BYTES = 12120
PERSONA_FIRST_LINE = "# Attractor Expert — System Prompt"

pytestmark = pytest.mark.skipif(
    not AGENT_PATH.is_file(),
    reason=f"expert profile absent at {AGENT_PATH} (partial checkout)",
)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, markdown_body) for an agent .md file."""
    assert text.startswith("---\n"), "agent file must open with YAML frontmatter"
    end = text.index("\n---\n", 3)
    return text[4 : end + 1], text[end + len("\n---\n") :]


def _orchestrator_config() -> dict:
    fm, _ = _split_frontmatter(AGENT_PATH.read_text(encoding="utf-8"))
    data = yaml.safe_load(fm)
    return data["session"]["orchestrator"]


def test_xp001_inline_system_prompt_and_no_system_prompt_file() -> None:
    """XP-001: Layer-1 travels as an inline literal, not a resolvable path."""
    config = _orchestrator_config()["config"]

    assert "system_prompt" in config, (
        "\nattractor-expert declares no inline system_prompt.\n"
        "Layer-1 must travel as literal text: loop-agent resolves a relative\n"
        "system_prompt_file against ITS OWN install (the separate dot-runner\n"
        "package), where this bundle's context/ assets do not exist."
    )
    assert isinstance(config["system_prompt"], str), (
        f"system_prompt must be a literal string, got {type(config['system_prompt'])}"
    )
    assert "system_prompt_file" not in config, (
        "\nattractor-expert re-declares system_prompt_file.\n"
        "Even though explicit system_prompt wins the precedence contest, a\n"
        "second Layer-1 owner is exactly the drift this change removed."
    )


def test_xp002_persona_is_byte_identical_to_recorded_asset() -> None:
    """XP-002: the persona was MOVED, byte for byte -- never paraphrased."""
    persona = _orchestrator_config()["config"]["system_prompt"]
    encoded = persona.encode("utf-8")
    actual = hashlib.sha256(encoded).hexdigest()

    assert actual == PERSONA_SHA256, (
        "\nThe expert's Layer-1 persona changed.\n"
        f"  expected sha256: {PERSONA_SHA256}\n"
        f"  actual sha256:   {actual}\n"
        f"  expected bytes:  {PERSONA_BYTES}\n"
        f"  actual bytes:    {len(encoded)}\n\n"
        "If the persona was changed DELIBERATELY, update PERSONA_SHA256 and\n"
        "PERSONA_BYTES in this file in the SAME commit, and say so in the PR.\n"
        "If it was not, this is the regression this guard exists to catch:\n"
        "YAML block-scalar indentation, trailing-newline clipping, and\n"
        "well-meaning reflows all corrupt the persona silently."
    )
    assert len(encoded) == PERSONA_BYTES
    # Significant newlines: exactly one trailing, and interior blank lines kept.
    assert persona.endswith("\n") and not persona.endswith("\n\n"), (
        "persona must end with exactly one newline (YAML `|` clips to one)"
    )
    assert "\n\n" in persona, "interior blank lines must survive the block scalar"
    assert persona.split("\n")[0] == PERSONA_FIRST_LINE


def test_xp003_persona_is_the_non_coding_consultant_base() -> None:
    """XP-003: the rejected provider-coding-base substitution cannot pass."""
    persona = _orchestrator_config()["config"]["system_prompt"]

    assert "Attractor Expert" in persona, (
        "persona no longer identifies as the Attractor Expert -- a provider\n"
        "coding base was substituted. docs/designs/"
        "layer-1-profile-owned-system-prompt.md records this agent as the ONE\n"
        "agent deliberately keeping a non-coding persona override."
    )
    assert len(persona.splitlines()) > 150, (
        "persona collapsed to a stub; the full consultant base is expected"
    )


def test_xp004_retired_side_file_is_gone_and_undeclared() -> None:
    """XP-004: one Layer-1 owner; no live surface DECLARES the old path.

    Scope note, because the distinction is load-bearing. This asserts on a
    live *declaration* (``system_prompt_file: context/system-attractor-expert.md``),
    NOT on every mention of the string. Prose that explains the retirement is
    desirable -- ``behaviors/attractor-core.yaml`` and the repaired profile's
    own comment both name the old path precisely so the next reader learns why
    it went away. A guard that banned the words would delete the explanation
    along with the defect.

    Frozen ledgers (``specs/``) and dated design records (``docs/designs/``)
    are history: they describe what was true when written, and this repo's
    scope rules forbid rewriting them to make a test pass.
    """
    assert not RETIRED_SIDE_FILE.exists(), (
        f"\n{RETIRED_SIDE_FILE.relative_to(REPO_ROOT)} still exists.\n"
        "It was retired into the inline system_prompt. Keeping both creates\n"
        "two independently-maintained Layer-1 owners that silently drift."
    )

    declaration = re.compile(
        r"^\s*system_prompt_file\s*:\s*context/system-attractor-expert\.md\s*$"
    )
    frozen_or_historical = ("specs/", "docs/designs/", ".git/")
    self_rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(frozen_or_historical) or rel == self_rel:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if declaration.match(line):
                offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "\nLive surfaces still DECLARE the retired side-file as Layer-1:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse the inline system_prompt in agents/attractor-expert.md instead:\n"
        "loop-agent anchors a relative system_prompt_file on its own install."
    )


def test_xp005_loop_agent_mount_survives_the_repair() -> None:
    """XP-005: the explicit anti-recursion mount is preserved."""
    orchestrator = _orchestrator_config()

    assert orchestrator["module"] == "loop-agent", (
        "attractor-expert must mount loop-agent explicitly. Without it, a child\n"
        "spawned from a pipeline parent inherits loop-pipeline and recurses."
    )
    assert orchestrator["source"].startswith("git+"), (
        "orchestrator source must stay an absolute git+ pin (see\n"
        "tests/test_orchestrator_source_pin_guard.py -- a relative path here\n"
        "breaks every install of the bundle)."
    )


def test_xp006_markdown_expert_body_survives() -> None:
    """XP-006: the knowledge body is separate from the persona, and intact."""
    _, body = _split_frontmatter(AGENT_PATH.read_text(encoding="utf-8"))

    assert body.lstrip().startswith("# Attractor Pipeline Expert"), (
        "the Markdown expert body was lost or displaced by the frontmatter move"
    )
    assert len(body.encode("utf-8")) > 20_000, (
        "the expert knowledge body shrank unexpectedly; it is a separate\n"
        "concern from the Layer-1 persona and should not have been folded in"
    )
    assert "@attractor:context/attractor-expert-defenses.md" in body, (
        "the defenses transclusion is part of the shipped body"
    )


def test_xp007_parses_outside_repo_with_no_absolute_host_path() -> None:
    """XP-007: packaged parsing, not this checkout's CWD or cache layout."""
    raw = AGENT_PATH.read_text(encoding="utf-8")

    for needle in (str(Path.home()), "/home/", ".amplifier/cache", "site-packages"):
        assert needle not in raw, (
            f"\nThe expert profile embeds an absolute host path ({needle!r}).\n"
            "A packaged install must carry no machine-specific path."
        )

    # Re-parse with CWD outside the repo entirely: proves the value is carried,
    # not resolved relative to wherever the process happened to start.
    script = (
        "import sys, yaml, hashlib\n"
        "t = open(sys.argv[1], encoding='utf-8').read()\n"
        "e = t.index('\\n---\\n', 3)\n"
        "d = yaml.safe_load(t[4:e+1])\n"
        "p = d['session']['orchestrator']['config']['system_prompt']\n"
        "print(hashlib.sha256(p.encode()).hexdigest())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(AGENT_PATH)],
        cwd=Path(tempdir := "/tmp"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"parse outside repo failed: {result.stderr}"
    assert result.stdout.strip() == PERSONA_SHA256, (
        f"persona differs when parsed from {tempdir}: {result.stdout.strip()}"
    )
