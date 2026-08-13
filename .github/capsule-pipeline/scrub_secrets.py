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
  scan   -- re-scan the roots and exit 1 if ANY secret-shaped material
            remains. READ-ONLY BY CONSTRUCTION: `scan` cannot write a
            byte, which is exactly why it is the verb the CAPSULE PAIR
            gets (see the scope rule under `gate`). scan deliberately
            detects MORE than scrub redacts (it adds a high-entropy-token
            heuristic), so a secret shape the redaction patterns don't
            know about still fails instead of riding out in an artifact.
  gate   -- the RUN-EVIDENCE upload gate. Same detection set as `scan`,
            but it SPLITS the verdict by finding class instead of
            blocking on every class equally, and it may REDACT (see "The
            gate's split verdict" below). The workflows run this
            immediately before the upload step and BLOCK the upload on a
            non-zero exit. An artifact with no evidence is safe; an
            artifact with a leaked key is not -- fail toward
            not-uploading.

The gate's split verdict (issue #206). The entropy heuristic in layer 4 is
a shape GUESS, not a credential match, and it was measured wrong on 4 of 4
real runs: worker-session payloads in logs/*/sessions/*/events.jsonl are
legitimately full of high-entropy runs (digests, base64 fragments, request
ids), so the gate blocked the evidence upload on EVERY run. Evidence that
never survives cannot debug a failed run, and a gate that is always red
teaches maintainers to ignore red. So `gate` treats the two finding
classes as the different things they are:

  KNOWN-SHAPE findings -- the layer 1 token prefixes, the layer 2
    end-anchored sensitive assignments, and the layer 3 literal values of
    THIS job's own secrets -- are real-credential shapes. They HARD-BLOCK:
    the upload is skipped and the job goes red, exactly as before.

  ENTROPY-ONLY findings -- when `high-entropy-token` is the ONLY class
    present -- are QUARANTINED instead: the offending spans are redacted
    in place as `[REDACTED:entropy]`, the roots are re-scanned, and the
    upload proceeds only if that re-scan is CLEAN. This is not a
    weakening: nothing entropy-shaped is uploaded either way. The old
    behavior shipped NOTHING and lost the evidence; this ships the
    evidence with the suspicious spans removed. If the re-scan still
    finds anything, the gate blocks exactly as it always did.

SCOPE RULE, and it is load-bearing (PR #207, incident 2026-08-13): the
quarantine's redaction applies ONLY to run evidence. The capsule pair
destined for a PR is NEVER mutated -- its proofs attach to its exact
bytes. Two independent mechanisms enforce that: the workflows scan the
pair with the read-only `scan` verb (which has no redaction path at all),
and `gate` additionally takes --never-redact <path> for any root subtree
that must keep the old semantics, where ANY finding -- entropy included --
hard-blocks and no byte is ever rewritten.

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
  2. Assignments: `<NAME>=<value>` where NAME *ENDS WITH* API_KEY /
     SECRET_ACCESS_KEY / _TOKEN / _SECRET / PASSWORD / CREDENTIAL(S)
     (case-insensitive) -- the exact shape the incident's env dump
     produced. Only the VALUE is redacted; the name and `=` survive so
     evidence still shows WHICH variable leaked.

     THE END-ANCHOR IS LOAD-BEARING (second incident, 2026-08-13). This
     rule originally matched any name CONTAINING one of those words, and
     that CORRUPTED A SHIPPED ARTIFACT: a capsule whose subject was LLM
     cost/token math had its judge-approved gate rewritten by this
     scrubber -- 54 `input_tokens=` / `output_tokens=` / `total_tokens=` /
     `cache_read_tokens=` / `reasoning_tokens=` assignments across 31
     lines replaced with `[REDACTED:assignment]` (swallowing the trailing
     comma with the value) -- and the corrupted, no-longer-parseable
     script is what got pushed (PR #205). Credential variables put the
     sensitive word at the END of the name (`GITHUB_TOKEN`,
     `OPENAI_API_KEY`, `CLIENT_SECRET`, `AWS_SECRET_ACCESS_KEY`,
     `GOOGLE_APPLICATION_CREDENTIALS`); ordinary identifiers that merely
     CONTAIN it do not (`input_tokens`, `max_tokens`, `token_count`).
     Anchoring at the end keeps every credential shape above and drops
     that entire false-positive class. ACCEPTED, DOCUMENTED NARROWING:
     names where the word is genuinely interior (`password_hash=`,
     `token_bucket=`) are no longer redacted by THIS layer -- layers 1 and
     3 still cover every credential this job actually holds by shape and
     by literal value, and layer 4 still BLOCKS the upload on anything
     secret-shaped that survives. (Scrubbing is best-effort cleaning; the
     scan is the guarantee. Narrowing the cleaner does not widen the gate.)
  3. Literal values of the secrets this job actually holds: the watched
     env vars below (plus any named in $SCRUB_WATCH_ENV, comma-separated)
     are read from the environment and their VALUES are redacted/detected
     wherever they appear, regardless of shape. This is why the workflow
     steps pass the real secrets into this script's env.
  4. (scan/gate only, never scrub) High-entropy token heuristic: long
     random-looking tokens that match none of the above. Pure hex (git
     SHAs, digests), pure digits, and pure letter runs are excluded so
     routine log content does not trip it.

     THE ORIGINAL BIAS, AND ITS MEASURED PRICE (issue #206). This layer
     was deliberately biased toward false positives -- "a false positive
     costs one run's evidence; a false negative is a published
     credential." The bill came in at 4 real runs out of 4: every one
     tripped this layer on `logs/*/sessions/*/events.jsonl` (e.g. run
     31657343281, findings at lines 5/6/10/11/14, all
     shape=high-entropy-token), so the evidence artifact never survived a
     single run and no failed run could be diagnosed. The premise was
     wrong in one place: the choice was never "block or publish". A
     high-entropy span can simply be REDACTED, which costs neither the
     credential nor the evidence. `gate` does exactly that (see "The
     gate's split verdict"); this layer stays as detect-only for `scan`,
     whose job is to have no opinion and never write.

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

# Layer 2: NAME=value assignments (the incident's env-dump shape).
#
# SENSITIVE_NAME_TAILS are matched at the END of the variable name -- the
# name group has NO trailing `[A-Za-z0-9_]*`, so the tail must butt directly
# against the `=`. That anchor is the fix for the 2026-08-13 artifact
# corruption (see the module docstring, layer 2): a CONTAINS match turns
# every `input_tokens=`/`max_tokens=`/`total_tokens=` in ordinary LLM
# accounting code into a redaction, and the pipeline scrubs artifacts that
# are later executed as code.
#
# Each tail is a real credential-name ending, not a guess:
#   API_KEYS?          OPENAI_API_KEY, ANTHROPIC_API_KEY (2 of the 5 watched
#                      vars), MY_API_KEY
#   SECRET_ACCESS_KEY  AWS_SECRET_ACCESS_KEY -- listed explicitly because it
#                      ends in _KEY, and a bare `_KEY` tail would swallow
#                      cache_key=/sort_key=/primary_key= (the same
#                      false-positive class this fix exists to remove)
#   _TOKEN             GITHUB_TOKEN, GH_TOKEN, CAPSULE_PR_TOKEN (3 of the 5
#                      watched vars). SINGULAR ONLY -- `_TOKENS` is the
#                      corruption class, never a credential name
#   _SECRET            CLIENT_SECRET, X_SECRET
#   PASSWORD           PASSWORD, DB_PASSWORD, PGPASSWORD
#   CREDENTIALS?       GOOGLE_APPLICATION_CREDENTIALS
#
# The negative lookahead keeps an already-redacted value from being
# re-redacted into a less specific shape.
SENSITIVE_NAME_TAILS = (
    "API_KEYS?",
    "SECRET_ACCESS_KEY",
    "_TOKEN",
    "_SECRET",
    "PASSWORD",
    "CREDENTIALS?",
)

ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9_]*(?:" + "|".join(SENSITIVE_NAME_TAILS) + r"))"
    r"(?P<sep>\s*=\s*)"
    r"(?P<quote>[\"']?)"
    r"(?!\[REDACTED:)"
    r"(?P<value>[^\s\"'\\]{4,})",
    re.IGNORECASE,
)

# Layer 4 (scan/gate only): candidate runs for the entropy heuristic.
ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-=]{28,}")
ENTROPY_THRESHOLD = 4.5  # bits/char; random base64-ish material sits above

# The one shape `gate` may quarantine instead of blocking on. Named, not
# inlined, because the whole split verdict turns on this exact string: it
# is the shape `scan_text` reports for layer 4 and nothing else.
ENTROPY_SHAPE = "high-entropy-token"


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


def redact_entropy_text(text: str) -> tuple[str, int]:
    """Redact every entropy-suspicious span in `text`. Returns (text, count).

    This is the one place the layer-4 heuristic becomes SCRUB-CAPABLE
    rather than detect-only, and it exists so the evidence gate has a
    third option besides "block the upload" and "publish the span"
    (issue #206).

    Surgical, exactly like the other redactions: only the matched run is
    replaced with `[REDACTED:entropy]`; every surrounding byte survives.
    ENTROPY_CANDIDATE's character class excludes `"` and `\\`, so a match
    can never span a JSON string boundary or an escape -- a redacted
    events.jsonl line still parses as the same JSON with one string
    value shortened. The replacement text is not itself an entropy
    candidate (it contains `:` and `[`, and its longest candidate run,
    `REDACTED`, is 8 chars of pure alpha), so re-running this is a no-op
    and the confirming re-scan cannot fire on the redaction itself.
    """
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        token = m.group(0)
        if _entropy_suspicious(token):
            count += 1
            return "[REDACTED:entropy]"
        return token

    return ENTROPY_CANDIDATE.sub(_sub, text), count


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
            findings.append(ENTROPY_SHAPE)
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


def _fence_set(never_redact: list[str]) -> list[Path]:
    """Resolved absolute paths whose subtrees may never be rewritten."""
    return [Path(p).resolve() for p in never_redact]


def _is_fenced(path: Path, fences: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == f or f in resolved.parents for f in fences)


def _file_findings(path: Path, literals: dict[str, str]) -> list[tuple[int, str]] | None:
    """[(lineno, shape)] for one file, or None if it could not be read."""
    try:
        text = _read_text(path)
    except OSError:
        return None
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for shape in scan_text(line, literals):
            found.append((lineno, shape))
    return found


BLOCK_MESSAGE = (
    "scrub_secrets: RESIDUAL SECRET-SHAPED MATERIAL FOUND "
    "(scanned {n} file(s)). The upload must be blocked."
)


def cmd_gate(roots: list[str], never_redact: list[str]) -> int:
    """The run-evidence upload gate: hard-block known shapes, quarantine entropy.

    Returns 0 when the evidence may be uploaded, 1 when it may not. See the
    module docstring's "The gate's split verdict" for the why.
    """
    literals = _watched_literals()
    files = _iter_files(roots)
    fences = _fence_set(never_redact)

    blocking = 0
    entropy_files: dict[Path, int] = {}

    for path in files:
        findings = _file_findings(path, literals)
        if findings is None:
            # Cannot attest this file is clean -> fail closed (as `scan` does).
            print(f"scan FINDING {path}: unreadable -- cannot attest clean")
            blocking += 1
            continue
        fenced = _is_fenced(path, fences)
        for lineno, shape in findings:
            print(f"scan FINDING {path}:{lineno}: shape={shape}")
            if shape != ENTROPY_SHAPE:
                blocking += 1
            elif fenced:
                # PR #207 semantics, preserved byte-for-byte: inside a fenced
                # subtree (the capsule pair) EVERY finding blocks, entropy
                # included, and nothing is ever rewritten. The pair is the
                # run's reviewed output; silently mutating it invalidates
                # every proof the run just established.
                print(
                    f"scan FINDING {path}:{lineno}: shape={shape} is inside a "
                    "--never-redact subtree (the capsule pair) -- quarantine "
                    "does not apply there; this BLOCKS."
                )
                blocking += 1
            else:
                entropy_files[path] = entropy_files.get(path, 0) + 1

    if blocking:
        print(BLOCK_MESSAGE.format(n=len(files)))
        return 1

    if not entropy_files:
        print(f"scrub_secrets: clean -- scanned {len(files)} file(s), no secret-shaped material.")
        return 0

    # Entropy-ONLY findings, all outside every fence: quarantine them.
    redacted_spans = 0
    quarantined: list[Path] = []
    for path in sorted(entropy_files, key=str):
        try:
            original = _read_text(path)
        except OSError as e:
            print(f"scan FINDING {path}: unreadable during quarantine ({e}) -- cannot attest clean")
            print(BLOCK_MESSAGE.format(n=len(files)))
            return 1
        new_text, n = redact_entropy_text(original)
        if n and new_text != original:
            try:
                _write_text(path, new_text)
            except OSError as e:
                # Could not remove the span -> cannot attest this file is
                # clean -> fail closed, exactly as an unreadable file does.
                print(f"scan FINDING {path}: quarantine write failed ({e}) -- cannot attest clean")
                print(BLOCK_MESSAGE.format(n=len(files)))
                return 1
            redacted_spans += n
            quarantined.append(path)
            print(f"quarantined {path}: {n} high-entropy span(s) -> [REDACTED:entropy]")

    # The guarantee is the RE-SCAN, not the redaction: only a clean second
    # pass over every root licenses the upload. Anything still standing --
    # including an entropy span the redactor somehow failed to remove --
    # blocks exactly as it did before this split existed.
    residual = 0
    for path in files:
        findings = _file_findings(path, literals)
        if findings is None:
            print(f"scan FINDING {path}: unreadable on re-scan -- cannot attest clean")
            residual += 1
            continue
        for lineno, shape in findings:
            print(f"scan FINDING (post-quarantine) {path}:{lineno}: shape={shape}")
            residual += 1

    if residual:
        print(
            "scrub_secrets: QUARANTINE DID NOT CLEAR -- "
            f"{residual} finding(s) survive the entropy redaction pass. "
            "The upload must be blocked."
        )
        return 1

    file_list = ", ".join(str(p) for p in quarantined)
    print(
        f"::notice::Residual secret gate: QUARANTINED {redacted_spans} high-entropy span(s) "
        f"across {len(quarantined)} run-evidence file(s) instead of blocking the upload -- no "
        "known credential shape was found, the spans were redacted in place as "
        "[REDACTED:entropy], and the confirming re-scan is clean. The run-evidence artifact "
        f"is uploaded with those spans removed. Files: {file_list}"
    )
    print(
        f"scrub_secrets: clean after quarantine -- scanned {len(files)} file(s); "
        f"{redacted_spans} entropy span(s) redacted across {len(quarantined)} file(s); "
        "no known credential shape found."
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_scrub = sub.add_parser("scrub", help="redact secret-shaped material in place")
    p_scrub.add_argument("roots", nargs="+")
    p_scan = sub.add_parser("scan", help="exit 1 if any secret-shaped material remains")
    p_scan.add_argument("roots", nargs="+")
    p_gate = sub.add_parser(
        "gate",
        help=(
            "run-evidence upload gate: hard-block known credential shapes, "
            "quarantine (redact + re-scan) entropy-only findings"
        ),
    )
    p_gate.add_argument(
        "--never-redact",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "a path (file or directory subtree) that must keep the strict "
            "scan semantics: ANY finding there -- entropy included -- blocks, "
            "and nothing under it is ever rewritten. Use for the capsule pair."
        ),
    )
    p_gate.add_argument("roots", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "scrub":
        return cmd_scrub(args.roots)
    if args.command == "gate":
        return cmd_gate(args.roots, args.never_redact)
    return cmd_scan(args.roots)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
