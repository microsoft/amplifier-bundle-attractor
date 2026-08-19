"""LEAK SCAN — no real corpus data or maintainer identity may ship in this skill.

The engine layer was built against a real personal corpus on a real machine.
Nothing from either may be committed: no real session UUIDs, no workspace slugs,
no personal paths, and — the load-bearing case this guard exists for — **no
identity of whoever built or runs it** (hostname, username, home path, git
identity). This test is the enforcement, not the promise; it runs on every
suite invocation over every shipped file.

Three deliberately-separated layers, because a naive deny-list is
self-defeating in a public repo — adding a secret name to the deny-list
*publishes that name*:

  Layer 1 — GENERIC SHAPES (committed, safe).
    Regexes and substrings that match the *shape* of private data without
    naming any instance of it: v4-UUID session ids, `-home-<user>` workspace
    slugs, `/home/` paths, `localhost:<port>` / `bolt://` graph endpoints,
    `gc-NN` internal cluster ids, e-mail shape, common secret-key prefixes.
    The literals here reveal nothing.

  Layer 2 — DERIVED IDENTITY (committed code, zero committed values). ★
    At RUNTIME, derive the identity of the current environment — hostname
    (and its short form), login user, home directory, and git user.name /
    user.email — and assert none of them appear in any shipped file. This
    catches the builder's identity WITHOUT ever committing a name: on the
    maintainer's machine it catches the real hostname by construction; on a CI
    runner it harmlessly checks the runner's identity. Values shorter than
    `MIN_IDENTITY_LEN` are skipped so a two-letter username cannot flag the
    whole corpus.

  Layer 3 — LOCAL DENY-LIST (never committed).
    If `$ATTRACTOR_SCOUT_LEAK_DENYLIST` points at a file, or
    `~/.amplifier/leak-denylist.txt` exists, load extra forbidden substrings
    from it (one per line, `#` comments). Silently skipped when absent (CI).
    Identity-specific terms that Layer 2 cannot derive — OTHER machines' names,
    project codenames, collaborators' handles — belong THERE, on the
    maintainer's disk, and NEVER in this repo.

A false positive here costs one rename; a false negative ships someone's
private data into a public repo.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent

SCAN_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".yaml", ".yml", ".html", ".txt", ".ini", ".cfg"}
SKIP_DIRS = {"__pycache__", ".ruff_cache", ".pytest_cache"}

# ------------------------------------------------------------------ Layer 1
#: A real session id is a v4 UUID. Synthetic ids are `syn...-NNNN-4000-8000-...`
#: and never match this, because they do not start with 8 hex characters.
REAL_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)

#: Internal cluster ids from the real mining run (gc-01 ... gc-54).
CLUSTER_ID_RE = re.compile(r"\bgc-\d{2}\b")

#: Real workspace slugs in the calibration corpus are `-home-<user>`-shaped.
HOME_SLUG_RE = re.compile(r"[\"'`]-home-[a-z]")

#: E-mail shape (any address is a personal identifier we do not ship).
EMAIL_RE = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)

#: Common secret-key prefixes followed by key-like material.
SECRET_KEY_RE = re.compile(r"\b(sk-[a-z0-9]{16,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")

#: Generic personal-path / endpoint shapes. Values reveal nothing.
FORBIDDEN_SUBSTRINGS = (
    "/home/",
    "/Users/",
    "~/.amplifier/projects/-",
    "localhost:7687",
    "bolt://",
    ".internal",
)

# ------------------------------------------------------------------ Layer 2
#: Identity values shorter than this are ignored — a 3-letter username or
#: hostname would flag half the corpus. 4 keeps real hostnames, login names,
#: and home paths in scope while dropping trivially-short noise. (No identity
#: literal is written here on purpose — that is the whole point of Layer 2.)
MIN_IDENTITY_LEN = 4

# ------------------------------------------------------------------ Layer 3
DENYLIST_ENV = "ATTRACTOR_SCOUT_LEAK_DENYLIST"
DEFAULT_DENYLIST = Path.home() / ".amplifier" / "leak-denylist.txt"

#: Only this file legitimately carries the generic-shape *patterns* above.
ALLOWED_PATTERN_MENTIONS = {"test_no_real_data_leak.py"}


def _shipped_files() -> list[Path]:
    out = []
    for path in SKILL_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


def _git_config(field: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--get", field],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=SKILL_DIR,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    val = out.stdout.strip()
    return val or None


def _derived_identity_terms() -> list[str]:
    """Identity of the CURRENT environment — computed, never committed.

    Returns a de-duplicated, case-normalised list of terms of length
    >= MIN_IDENTITY_LEN. Never raises: an unavailable source is skipped.
    """
    raw: list[str] = []
    try:
        host = socket.gethostname()
        if host:
            raw.append(host)
            raw.append(host.split(".", 1)[0])  # short form before any dot
    except OSError:
        pass
    try:
        raw.append(getpass.getuser())
    except (OSError, KeyError):
        pass
    try:
        raw.append(str(Path.home()))
    except (OSError, RuntimeError):
        pass
    for field in ("user.name", "user.email"):
        val = _git_config(field)
        if val:
            raw.append(val)

    seen: set[str] = set()
    terms: list[str] = []
    for term in raw:
        norm = term.strip().lower()
        if len(norm) < MIN_IDENTITY_LEN or norm in seen:
            continue
        seen.add(norm)
        terms.append(norm)
    return terms


def _local_denylist() -> list[str]:
    """Extra forbidden substrings from the maintainer's local, uncommitted list."""
    path_str = os.environ.get(DENYLIST_ENV)
    path = Path(path_str) if path_str else DEFAULT_DENYLIST
    if not path.is_file():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


# ------------------------------------------------------------------ tests
def test_there_are_files_to_scan():
    assert len(_shipped_files()) >= 10


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_layer1_no_real_uuid_session_ids(path: Path):
    if path.name in ALLOWED_PATTERN_MENTIONS:
        pytest.skip("the scanner itself carries the pattern")
    hits = REAL_UUID_RE.findall(path.read_text(encoding="utf-8", errors="replace"))
    assert not hits, f"{path.relative_to(SKILL_DIR)} carries real-looking session UUID(s): {hits[:3]}"


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_layer1_no_internal_cluster_ids(path: Path):
    if path.name in ALLOWED_PATTERN_MENTIONS:
        pytest.skip("the scanner itself carries the pattern")
    hits = CLUSTER_ID_RE.findall(path.read_text(encoding="utf-8", errors="replace"))
    assert not hits, f"{path.relative_to(SKILL_DIR)} carries internal cluster id(s): {sorted(set(hits))[:5]}"


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_layer1_no_slugs_paths_emails_or_keys(path: Path):
    if path.name in ALLOWED_PATTERN_MENTIONS:
        pytest.skip("the scanner itself carries the pattern")
    text = path.read_text(encoding="utf-8", errors="replace")
    assert not HOME_SLUG_RE.search(text), f"{path.relative_to(SKILL_DIR)} carries a real workspace slug"
    assert not EMAIL_RE.search(text), f"{path.relative_to(SKILL_DIR)} carries an e-mail address"
    assert not SECRET_KEY_RE.search(text), f"{path.relative_to(SKILL_DIR)} carries a secret-key-shaped token"
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in text, f"{path.relative_to(SKILL_DIR)} carries forbidden substring {needle!r}"


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_layer2_no_current_environment_identity(path: Path):
    """★ The load-bearing check: no term identifying THIS environment ships.

    Derived at runtime from hostname / user / home / git identity; the guard
    scans its own file too, because it never legitimately contains any of those
    literal values (it computes them). On the maintainer's machine this catches
    the real hostname in any file that hardcodes it.
    """
    terms = _derived_identity_terms()
    if not terms:
        pytest.skip("no derivable identity terms of sufficient length in this environment")
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    hits = sorted({term for term in terms if term in text})
    assert not hits, (
        f"{path.relative_to(SKILL_DIR)} contains {len(hits)} term(s) identifying the current "
        f"environment (hostname/user/home/git identity). Genericize the content; identity must "
        f"never ship. (Matched-term values are withheld from this message on purpose.)"
    )


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: str(p.relative_to(SKILL_DIR)))
def test_layer3_local_denylist(path: Path):
    """Optional, never-committed extra deny-list (OTHER machines, codenames)."""
    if path.name in ALLOWED_PATTERN_MENTIONS:
        pytest.skip("the scanner itself carries the pattern")
    denied = _local_denylist()
    if not denied:
        pytest.skip("no local deny-list configured (expected on CI)")
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    hits = sorted({needle for needle in denied if needle.lower() in text})
    assert not hits, (
        f"{path.relative_to(SKILL_DIR)} matches {len(hits)} local deny-list term(s). "
        f"(Values withheld — they live only in the local list, never in the repo.)"
    )


def test_no_committed_corpus_data():
    """No `.jsonl` corpus may be committed under this skill."""
    stray = [p for p in SKILL_DIR.rglob("*.jsonl") if "__pycache__" not in p.parts]
    assert not stray, f"corpus data committed: {[str(p.relative_to(SKILL_DIR)) for p in stray]}"


def test_synthetic_ids_are_recognisably_synthetic():
    from fixtures.synthetic_corpus import synth_id

    sid = synth_id("synu00", 7)
    assert sid.startswith("syn")
    assert not REAL_UUID_RE.match(sid), "a synthetic id must not be mistakable for a real UUID"
