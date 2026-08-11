#!/usr/bin/env python3
"""Scrub secret-shaped material from run-evidence directories, then gate uploads.

WHY THIS EXISTS (incident, 2026-08): a worker agent inside a pipeline run
executed a tool that dumped its environment; the session-observability
persister wrote the tool:post payload VERBATIM to
.ai/<stage>/sessions/<id>/events.jsonl -- including a literal
`OPENAI_API_KEY=sk-proj-...` value -- and both capsule workflows then
uploaded `.ai` (and the runner-temp capsule dir) as run-evidence artifacts
on a PUBLIC repository. GitHub's live-log secret masking does NOT touch
files written to disk, so the artifact carried the real key. This script is
the mechanism that makes that unrepeatable:

  scrub  -- walk the evidence roots and redact secret-shaped material IN
            PLACE (best-effort cleaning, surgical: only the secret value is
            replaced with `[REDACTED:<shape>]`; surrounding bytes -- JSON
            structure, log text -- are untouched).
  scan   -- re-scan the evidence roots and exit 1 if ANY secret-shaped
            material remains. The workflows run this immediately before the
            upload step and BLOCK the upload on a non-zero exit. scan
            deliberately detects MORE than scrub redacts (it adds a
            high-entropy-token heuristic), so a secret shape the redaction
            patterns don't know about still blocks the upload instead of
            riding out in an artifact. An artifact with no evidence is
            safe; an artifact with a leaked key is not -- fail toward
            not-uploading.

This file is NATIVE to this repository (not one of the vendored pipeline
files in this directory -- see README.md's provenance section; the
"do not hand-edit" rule there applies to the vendored copies, not to this).

Stdlib only, on purpose: it must run on a bare GitHub Actions runner with
nothing installed beyond python3, and be trivially unit-testable
(`python3 -m unittest test_scrub_secrets.py` from this directory).

Detection layers:
  1. Known token shapes (regex): OpenAI-style `sk-...` (covers sk-proj-,
     sk-ant-), GitHub fine-grained `github_pat_...`, GitHub classic/app
     tokens `ghp_/gho_/ghs_/ghu_/ghr_...`.
  2. Assignments: `<NAME>=<value>` where NAME contains API_KEY / _TOKEN /
     _SECRET / PASSWORD / CREDENTIAL (case-insensitive) -- the exact shape
     the incident's env dump produced. Only the VALUE is redacted; the
     name and `=` survive so evidence still shows WHICH variable leaked.
  3. Literal values of the secrets this job actually holds: the watched
     env vars below (plus any named in $SCRUB_WATCH_ENV, comma-separated)
     are read from the environment and their VALUES are redacted/detected
     wherever they appear, regardless of shape. This is why the workflow
     steps pass the real secrets into this script's env.
  4. (scan only) High-entropy token heuristic: long random-looking tokens
     that match none of the above still FIRE THE GATE. Deliberately biased
     toward false positives over false negatives -- a false positive costs
     one run's evidence; a false negative is a published credential. Pure
     hex (git SHAs, digests), pure digits, and pure letter runs are
     excluded so routine log content stays uploadable.

Findings are reported as file:line + shape/variable-name ONLY -- a matched
secret value is never printed (printing it would leak it into the job log).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Env vars whose literal VALUES are redacted (scrub) and detected (scan)
# wherever they appear. Extend per-invocation with SCRUB_WATCH_ENV=A,B,C.
DEFAULT_WATCH_ENV = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CAPSULE_PR_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

# Values shorter than this are never treated as literal secrets (avoids
# scrubbing e.g. a watched var someone set to "true" out of every file).
MIN_LITERAL_LEN = 8

# Layer 1: known token shapes. Character classes stop at backslash and
# quote, so a token embedded in a JSON string (`"...\nsk-proj-abc..."`)
# is redacted without touching the string's escapes or closing quote.
TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github-token", re.compile(r"gh[posur]_[A-Za-z0-9]{20,}")),
]

# Layer 2: NAME=value assignments (the incident's env-dump shape). The
# negative lookahead keeps an already-redacted value from being re-redacted
# into a less specific shape.
ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9_]*(?:API_KEY|_TOKEN|_SECRET|PASSWORD|CREDENTIAL)[A-Za-z0-9_]*)"
    r"(?P<sep>\s*=\s*)"
    r"(?P<quote>[\"']?)"
    r"(?!\[REDACTED:)"
    r"(?P<value>[^\s\"'\\]{4,})",
    re.IGNORECASE,
)

# Layer 4 (scan only): candidate runs for the entropy heuristic.
ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-=]{28,}")
ENTROPY_THRESHOLD = 4.5  # bits/char; random base64-ish material sits above


def _shannon_entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _entropy_suspicious(token: str) -> bool:
    """True if `token` looks like random secret material.

    Exclusions (a documented false-negative bias -- each excluded shape is
    something routine evidence is full of): pure hex (git SHAs, sha256
    digests), pure digits (ids, timestamps), and letters-only runs (long
    identifiers/words).
    """
    core = token.strip("=").replace("-", "").replace("_", "")
    if not core:
        return False
    lowered = core.lower()
    if all(ch in "0123456789abcdef" for ch in lowered):
        return False  # hex: git SHAs / digests
    if core.isdigit() or core.isalpha():
        return False
    return _shannon_entropy(token) >= ENTROPY_THRESHOLD


def _watched_literals() -> dict[str, str]:
    names = list(DEFAULT_WATCH_ENV)
    extra = os.environ.get("SCRUB_WATCH_ENV", "")
    names += [n.strip() for n in extra.split(",") if n.strip()]
    out: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name, "")
        if len(value) >= MIN_LITERAL_LEN:
            out[name] = value
    return out


def _iter_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        p = Path(root)
        if not p.exists():
            print(f"scrub_secrets: root does not exist, skipping: {root}")
            continue
        if p.is_file():
            files.append(p)
            continue
        for dirpath, _dirnames, filenames in os.walk(p, followlinks=False):
            for fn in filenames:
                fp = Path(dirpath) / fn
                if fp.is_symlink():
                    continue
                files.append(fp)
    return files


def _read_text(path: Path) -> str:
    # surrogateescape round-trips arbitrary bytes, so binary-ish files are
    # scanned/scrubbed safely and unmatched content is byte-preserved.
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def _write_text(path: Path, text: str) -> None:
    data = text.encode("utf-8", errors="surrogateescape")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".scrub-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def scrub_text(text: str, literals: dict[str, str]) -> tuple[str, list[str]]:
    """Redact secret-shaped material in `text`. Returns (new_text, shapes)."""
    shapes: list[str] = []

    # Most specific first: the exact secret values this job holds.
    for name, value in literals.items():
        if value in text:
            text = text.replace(value, f"[REDACTED:env:{name}]")
            shapes.append(f"env:{name}")

    for shape, pattern in TOKEN_PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{shape}]", text)
        if n:
            shapes.append(shape)

    def _assignment_sub(m: re.Match[str]) -> str:
        shapes.append(f"assignment:{m.group('name')}")
        return f"{m.group('name')}{m.group('sep')}{m.group('quote')}[REDACTED:assignment]"

    text = ASSIGNMENT_PATTERN.sub(_assignment_sub, text)
    return text, shapes


def scan_text(text: str, literals: dict[str, str]) -> list[str]:
    """Return the shapes of any secret-shaped material found. Values are
    NEVER returned -- shapes and variable names only."""
    findings: list[str] = []
    for name, value in literals.items():
        if value in text:
            findings.append(f"env:{name}")
    for shape, pattern in TOKEN_PATTERNS:
        if pattern.search(text):
            findings.append(shape)
    for m in ASSIGNMENT_PATTERN.finditer(text):
        findings.append(f"assignment:{m.group('name')}")
    for m in ENTROPY_CANDIDATE.finditer(text):
        if _entropy_suspicious(m.group(0)):
            findings.append("high-entropy-token")
            break
    return findings


def cmd_scrub(roots: list[str]) -> int:
    literals = _watched_literals()
    files = _iter_files(roots)
    changed = 0
    for path in files:
        try:
            original = _read_text(path)
        except OSError as e:
            print(f"::warning::scrub_secrets: could not read {path}: {e}")
            continue
        new_text, shapes = scrub_text(original, literals)
        if new_text != original:
            _write_text(path, new_text)
            changed += 1
            print(f"scrubbed {path}: {', '.join(sorted(set(shapes)))}")
    print(
        f"scrub_secrets: scrubbed {changed} of {len(files)} file(s) "
        f"under {len(roots)} root(s); watching {len(literals)} literal secret value(s)."
    )
    return 0


def cmd_scan(roots: list[str]) -> int:
    literals = _watched_literals()
    files = _iter_files(roots)
    failed = False
    for path in files:
        try:
            text = _read_text(path)
        except OSError as e:
            # Cannot attest this file is clean -> fail closed.
            print(f"scan FINDING {path}: unreadable ({e}) -- cannot attest clean")
            failed = True
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for shape in scan_text(line, literals):
                print(f"scan FINDING {path}:{lineno}: shape={shape}")
                failed = True
    if failed:
        print(
            "scrub_secrets: RESIDUAL SECRET-SHAPED MATERIAL FOUND "
            f"(scanned {len(files)} file(s)). The upload must be blocked."
        )
        return 1
    print(f"scrub_secrets: clean -- scanned {len(files)} file(s), no secret-shaped material.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_scrub = sub.add_parser("scrub", help="redact secret-shaped material in place")
    p_scrub.add_argument("roots", nargs="+")
    p_scan = sub.add_parser("scan", help="exit 1 if any secret-shaped material remains")
    p_scan.add_argument("roots", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "scrub":
        return cmd_scrub(args.roots)
    return cmd_scan(args.roots)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
