"""No-re-flip guard for `session.orchestrator` sources -- OSP-001..OSP-003.

Every orchestrator-class ``source:`` in this bundle's composition surfaces must
stay an absolute ``git+`` pin.  Flipping one to a repo-relative path (``./`` or
``../``) is a change that *looks* obviously right, passes review, and breaks
every install of the bundle.  It has already happened once, on the very PR that
added this guard.

**The measurement this guard exists to preserve.**  PR #255's first build
(commit ``af29e80``) flipped the same-repo orchestrator pins to
``./modules/loop-agent``, reasoning by analogy from ``tools:``/``hooks:``, where
relative paths are correct.  It was pushed and installed into a clean DTU the
documented way.  The session refused to start::

    5 of 117 modules failed to activate (strict mode):
      - loop-agent: File not found:
        .../amplifier_app_cli/_bundle/behaviors/modules/loop-agent   (x4)
      - loop-pipeline: File not found:
        .../amplifier_app_cli/_bundle/behaviors/modules/loop-pipeline

The analogy is false, and the asymmetry is in foundation:

  * ``tools:`` / ``hooks:`` / ``providers:`` sources are resolved at **parse**
    time, against **the declaring file's own directory**.  Relative is correct
    under any later composition.  (10 such pins *were* flipped in #255, and
    work.)
  * ``session.orchestrator.source`` is kept **raw** at parse time and resolved
    **late**, against the **composed** bundle's ``base_path`` -- which in a real
    amplifier session is the host *application's* bundle directory, not this
    repo's installed snapshot.  No relative path written here can reach this
    repo's modules from there.

There is no third option today: foundation's source resolver mounts file / git /
zip / http handlers and **no namespace handler**, so ``attractor:modules/X`` is
not a form that can be written instead.  Tracked as issue #256; until that
lands, the ``git+`` self-pin is load-bearing, not laziness.

Checks:

  OSP-001  every orchestrator-class ``source:`` across the composition surfaces
           -> is an absolute ``git+`` form, never ``./`` or ``../``
  OSP-002  each named surface glob matched at least one file, and every file it
           matched parsed -> the scan reaches what it claims to reach
  OSP-003  the scan found a non-trivial number of orchestrator sources
           -> the walker still works; a guard that finds nothing passes forever

Surfaces scanned: ``behaviors/*.yaml``, ``bundles/*.yaml``, ``agents/*.yaml``,
``agents/*.md`` (frontmatter), ``profiles/*.yaml``, and ``bundle.md``
(frontmatter).  This is a superset of the surfaces the #255 review named, on
purpose -- ``profiles/`` and ``agents/*.yaml`` declare orchestrator sources too,
and an unscanned surface is exactly where the next re-flip would land.

Honest limits:
  - "Orchestrator-class" is decided by key name (any mapping key containing
    ``orchestrator`` whose value is a mapping with a ``source``).  A future
    foundation key that carries an orchestrator source under an unrelated name
    would be invisible here.  The failure direction is safe: over-broad key
    matching scans more, never less.
  - OSP-001 requires ``git+`` specifically rather than merely "not relative".
    If a deliberately non-git absolute form is ever introduced (a ``zip+`` or
    ``https://`` module source), widen this guard in the same PR rather than
    deleting it -- the point is that *someone decides*, not that git is sacred.
  - OSP-003's floor is an anti-vacuity check, not an inventory pin.  It does not
    care which files carry the pins, so legitimate removals do not break it; it
    fails when the walker stops finding anything, which is the way this guard
    would otherwise rot into a no-op.
  - This module skips wholesale when the composition surfaces are absent, so the
    loop-pipeline suite still runs in a module-only/partial checkout.

Reference: PR #255 (the composition fix and its as-built record,
``docs/designs/2026-08-15-composition-fix.md``, "Two resolution classes"),
issue #256 (the foundation-level defect), issue #251 (the composition defect).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Locating the bundle repo root
# ---------------------------------------------------------------------------


def _find_bundle_root() -> Path | None:
    """Walk up from this file looking for the bundle repo root.

    Walks rather than hardcoding a parent count, so the guard survives the
    module being vendored or re-nested, and returns None (-> module skip)
    rather than pointing at a plausible-but-wrong directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "bundle.md").is_file() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    return None


BUNDLE_ROOT = _find_bundle_root()

# (directory, glob) pairs plus repo-root files, all relative to BUNDLE_ROOT.
SURFACE_GLOBS: tuple[tuple[str, str], ...] = (
    ("behaviors", "*.yaml"),
    ("bundles", "*.yaml"),
    ("agents", "*.yaml"),
    ("agents", "*.md"),
    ("profiles", "*.yaml"),
)
ROOT_FILES: tuple[str, ...] = ("bundle.md",)

# Anti-vacuity floor for OSP-003.  The tree carried 34 orchestrator sources when
# this guard landed (#255); the floor sits well below that so ordinary churn
# never trips it, and well above zero so a broken walker does.
MIN_ORCHESTRATOR_SOURCES = 20

REQUIRED_SOURCE_PREFIX = "git+"

pytestmark = pytest.mark.skipif(
    BUNDLE_ROOT is None,
    reason=(
        "bundle composition surfaces not present in this checkout -- they ship "
        "in the bundle repo, not in the loop-pipeline module distribution. "
        "Nothing to guard here; the rest of the module suite is unaffected."
    ),
)


def _root() -> Path:
    assert BUNDLE_ROOT is not None  # guaranteed by pytestmark
    return BUNDLE_ROOT


# ---------------------------------------------------------------------------
# Reading the surfaces
# ---------------------------------------------------------------------------


def _frontmatter(text: str) -> str:
    """Return the YAML frontmatter block of a markdown file, or "".

    Only the frontmatter is parsed for ``.md`` surfaces: the prose body carries
    illustrative YAML in fenced code blocks (``bundle.md`` documents an
    ``orchestrator: config: dot_file:`` snippet, for one), and a documentation
    example is not a composition declaration.
    """
    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def _surface_files() -> list[Path]:
    """Every composition file this guard scans, sorted, existing."""
    found: list[Path] = []
    for directory, pattern in SURFACE_GLOBS:
        found.extend(sorted((_root() / directory).glob(pattern)))
    found.extend(_root() / name for name in ROOT_FILES if (_root() / name).is_file())
    return found


def _load(path: Path) -> Any:
    """Parse a surface file into Python data, or raise yaml.YAMLError."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        text = _frontmatter(text)
    if not text.strip():
        return None
    return yaml.safe_load(text)


def _is_orchestrator_key(key: Any) -> bool:
    """Any mapping key that names an orchestrator, not just ``orchestrator``.

    Deliberately broad: an unscanned orchestrator-class key is exactly where the
    next re-flip would hide.
    """
    return isinstance(key, str) and "orchestrator" in key.lower()


def _walk_sources(node: Any, trail: str) -> list[tuple[str, Any]]:
    """Yield (dotted-path, source-value) for every orchestrator-class mapping.

    Recurses through the whole document, so nested declarations -- agent dicts
    inside ``agents:``, spawn children inside ``spawn:``, anything a future
    foundation adds -- are caught without this guard knowing their shapes.
    """
    hits: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_trail = f"{trail}.{key}" if trail else str(key)
            if (
                _is_orchestrator_key(key)
                and isinstance(value, dict)
                and "source" in value
            ):
                hits.append((f"{child_trail}.source", value["source"]))
            hits.extend(_walk_sources(value, child_trail))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            hits.extend(_walk_sources(item, f"{trail}[{index}]"))
    return hits


def _all_orchestrator_sources() -> list[tuple[str, str, Any]]:
    """(relative file path, dotted path, source value) across every surface."""
    results: list[tuple[str, str, Any]] = []
    for path in _surface_files():
        try:
            data = _load(path)
        except yaml.YAMLError:
            continue  # OSP-002 owns parse failures
        rel = path.relative_to(_root()).as_posix()
        results.extend((rel, dotted, value) for dotted, value in _walk_sources(data, ""))
    return results


# ---------------------------------------------------------------------------
# OSP-001: every orchestrator source stays a git+ pin
# ---------------------------------------------------------------------------


def test_osp001_every_orchestrator_source_is_a_git_pin():
    """No orchestrator ``source:`` may be a relative path. Measured, not assumed."""
    offenders = [
        f"{rel} :: {dotted} = {value!r}"
        for rel, dotted, value in _all_orchestrator_sources()
        if not (
            isinstance(value, str) and value.startswith(REQUIRED_SOURCE_PREFIX)
        )
    ]
    assert not offenders, (
        "orchestrator `source:` is no longer an absolute `git+` pin:\n"
        + "".join(f"  - {o}\n" for o in offenders)
        + "\n"
        "  This exact change was made once and measured: PR #255's first build\n"
        "  (commit af29e80) flipped these to `./modules/loop-agent`, and the DTU\n"
        "  install refused to start -- \"5 of 117 modules failed to activate\n"
        "  (strict mode): loop-agent: File not found:\n"
        "  .../amplifier_app_cli/_bundle/behaviors/modules/loop-agent\" -- because a\n"
        "  `session.orchestrator` source is kept RAW at parse time and resolved LATE\n"
        "  against the COMPOSED root's base_path, which in a real session is the host\n"
        "  APP's bundle directory, not this repo's installed snapshot, so no relative\n"
        "  path written here can reach this repo's modules; `tools:`/`hooks:` (and\n"
        "  `providers:`) sources ARE parse-time-anchored against the declaring file's\n"
        "  own directory and so are safe to keep relative, but orchestrator sources\n"
        "  are not, and foundation ships no namespaced module-source form to write\n"
        "  instead -- so keep the\n"
        "  `git+https://github.com/microsoft/amplifier-bundle-attractor@main"
        "#subdirectory=modules/...`\n"
        "  form until issue #256 lands a ref-free/namespaced module source upstream.\n"
        "\n"
        "  (If a deliberately non-git absolute form is being introduced, widen\n"
        "  REQUIRED_SOURCE_PREFIX in this guard in the same PR -- do not delete it.)"
    )


# ---------------------------------------------------------------------------
# OSP-002 / OSP-003: the scan is real
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("directory", "pattern"), SURFACE_GLOBS)
def test_osp002_every_named_surface_glob_matches_files(directory: str, pattern: str):
    """Each scanned surface still exists and still contains files.

    Without this, a directory rename turns OSP-001 into a vacuous pass over an
    empty set -- green, and guarding nothing.
    """
    matched = sorted((_root() / directory).glob(pattern))
    assert matched, (
        f"{directory}/{pattern} matched no files. OSP-001 can only guard what it\n"
        "  scans, so a renamed or emptied surface silently shrinks the guarded set\n"
        "  to nothing while staying green. If the surface genuinely moved, update\n"
        "  SURFACE_GLOBS in this guard in the same PR."
    )


def test_osp002b_every_scanned_file_parses():
    """Every surface file parses, so none is skipped silently by OSP-001."""
    broken: list[str] = []
    for path in _surface_files():
        try:
            _load(path)
        except yaml.YAMLError as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            broken.append(f"{path.relative_to(_root()).as_posix()}: {first_line}")
    assert not broken, (
        "composition files failed to parse:\n"
        + "".join(f"  - {b}\n" for b in broken)
        + "  OSP-001 skips unparseable files, so an unparseable composition file is\n"
        "  an unguarded one -- and, separately, a file amplifier itself cannot load."
    )


def test_osp003_scan_is_not_vacuous():
    """The walker still finds orchestrator sources at all."""
    found = _all_orchestrator_sources()
    assert len(found) >= MIN_ORCHESTRATOR_SOURCES, (
        f"only {len(found)} orchestrator `source:` entries found across "
        f"{len(_surface_files())} composition files; expected at least "
        f"{MIN_ORCHESTRATOR_SOURCES}.\n"
        "  This is an anti-vacuity floor, not an inventory pin: it does not care\n"
        "  which files carry the pins. A count this low means either the walker\n"
        "  stopped matching foundation's shape (in which case OSP-001 is now a\n"
        "  no-op that passes forever) or the pins were removed wholesale (in which\n"
        "  case say so, and lower the floor deliberately in the same PR).\n"
        f"  Found: {[f'{rel}::{dotted}' for rel, dotted, _ in found]}"
    )
