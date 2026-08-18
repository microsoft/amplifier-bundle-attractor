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

THAT FENCED-ENTROPY ARM IS NOT A BUG, and it has now fired in production
(run 31689374533, issue #204). Recorded here because the log looks alarming
and the investigation should not be repeated: that run produced 487
findings, EVERY ONE of them shape=high-entropy-token (no known credential
shape anywhere), and the gate still returned 1. Three of them sat inside
the fence, and the gate said so in as many words:

    scan FINDING .../out/llm-cost-exposure-204.verify.sh:605:
      shape=high-entropy-token is inside a --never-redact subtree (the
      capsule pair) -- quarantine does not apply there; this BLOCKS.

Because `blocking` was non-zero, the quarantine pass never ran at all --
which is why no `quarantined ...` line appears in that log. That is the
`if blocking: ... return 1` short-circuit below doing its job, not a
failed re-scan and not a quarantine defect on JSON-escaped spans. The 484
unfenced findings (the `logs/*/sessions/*/events.jsonl` model transcripts,
and the in-workspace `.ai/` copies) would all have been quarantined had
the fenced three not blocked first. The three fenced findings are exactly
the three the capsule-artifacts `scan` step had already failed on one step
earlier -- same file:line triple -- i.e. ONE root cause reaching two
doors: the gate author had embedded a pinned oracle in the capsule's
verify.sh as a base64-ish blob, because nothing shipped plain sibling
files with the pair. Fixed at the source (the specify pipeline now ships
`.ai/capsule/` fixtures as readable files); nothing in this file changed.

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
SENSITIVE_NAME_TAILS = (
    "API_KEYS?",
    "SECRET_ACCESS_KEY",
    "_TOKEN",
    "_SECRET",
    "PASSWORD",
    "CREDENTIALS?",
)

# THE VALUE GRAMMAR (issue #289). The pre-#289 rule spelled a value as ONE
# character class -- `[^\s"'\\]{4,}` -- which stops at the FIRST whitespace,
# quote or backslash. A secret that merely CONTAINS one of those within its
# first few characters therefore escaped:
#
#   PASSWORD=abc\<tail>      class dies at char 3 -> NOTHING is redacted
#   PASSWORD=abc'<tail>      same -- the whole value survives
#   PASSWORD=abcd"<tail>     lead redacted, the tail after the quote survives
#   API_KEY="secret value"   redacted to the first space; ` value` survives
#
# (Measured in PR #288's adversarial review: 0 of 500 20-char-tail escapees
# caught by this door.)
#
# A value is now one of three shapes, tried IN THIS ORDER. Every one of them
# is anchored so that widening what a value MAY CONTAIN never widens how far
# a value may REACH. Over-redaction is not cosmetic here: this same rule is
# ported verbatim to the session-event write seam
# (`modules/hooks-pipeline-observability/.../redaction.py`, pinned identical
# by that module's drift tripwire), where the input is a SERIALIZED JSON LINE
# and a match that crosses a JSON string boundary destroys the record instead
# of the secret -- the persister re-parses the redacted line and WITHHOLDS the
# payload if it no longer parses.
#
#   (a) DOUBLE-QUOTED -- `NAME="..."`, and the JSON-escaped `NAME=\"...\"`
#       form the write seam actually sees. The content class excludes `"`
#       outright, so the match STOPS at the first closing quote and can never
#       leave the string it started in; it is non-greedy and refuses to end
#       on a backslash (`(?<!\\)`), so the closing `\"`'s escape is never
#       eaten (eating it would terminate the JSON string early). The closing
#       quote is matched by lookahead, never consumed, and the substitution
#       re-emits the opening quote -- so the structure stays readable:
#       `API_KEY="[REDACTED:assignment]"`.
#
#   (b) SINGLE-QUOTED -- `NAME='...'`. Its content additionally excludes `"`,
#       because an UNTERMINATED single quote inside a JSON string would
#       otherwise let the match run past that string's own closing `"` to the
#       next apostrophe on the line (`"note": "don't"`), silently deleting an
#       unrelated field. The cost of that guard is stated honestly: a
#       single-quoted value that itself contains a double quote is only
#       partly covered (it falls through to (c)).
#
#   (c) UNQUOTED -- the pre-#289 class plus two fenced joiners:
#         * a quote joins only when the NEXT character is ordinary value
#           material (`[^\s"'\\,:;)\]}]`). In JSON a string-terminating quote
#           is ALWAYS followed by `,` `}` `]` `:` or whitespace, so this
#           joiner provably cannot consume a string terminator -- while in a
#           plain log line `PASSWORD=abcd"<tail>` is one token and is
#           redacted whole.
#         * a backslash joins in two forms. An ESCAPED PAIR (`\\`) always
#           joins ATOMICALLY -- that is how a literal backslash inside a
#           secret reaches this rule once the line has been JSON-serialized,
#           and consuming it whole is what keeps the pair from being split.
#           A LONE backslash joins UNLESS it is followed by ANOTHER
#           backslash (that case belongs to the pair -- see the fence
#           below), or it opens one of the escapes that encode a
#           record/field SEPARATOR (`\n`, `\r`, `\t`) or an
#           arbitrary code point (`\u`); the class is matched
#           case-sensitively, hence the `(?-i:...)`, because JSON escapes are
#           lower-case and `\N`/`\T` in a Windows-path-shaped value are
#           ordinary value material. Stopping at the separator escapes is
#           what keeps a serialized env dump -- an entire
#           `MY_PASSWORD=<secret>\nPATH=/usr/bin\nHOME=/root` on ONE line --
#           from being swallowed whole: the `\n` ends the value and PATH/HOME
#           survive byte-identical. `\b`/`\f` are NOT separators and do not
#           occur in this evidence, so they are treated as value interior --
#           which is what makes `PASSWORD=abc\<tail>` redact for any tail,
#           not just the lucky ones.
#       The run may not END on a backslash (`(?<!\\)`), so it can never leave
#       a dangling escape that would corrupt the enclosing JSON string.
#
# THE FENCE ON THE LONE JOINER (`[\s\\]`, not `\s`) IS NOT COSMETIC -- it is
# what makes this rule terminate, and it was added after #292's own
# adversarial review. Without it a backslash is AMBIGUOUS: the engine may
# read it as half of the atomic pair `\\` OR as the lone alternative, so a
# run of N backslashes has Fibonacci-many tilings. ONE ambiguity, TWO
# measured defects, both on this door and its ported twin:
#
#   * CATASTROPHIC BACKTRACKING. The trailing `(?<!\\)` fails every tiling
#     that ends on a backslash, so the engine enumerates all of them.
#     Measured before the fence: `PASSWORD=` + 40 backslashes -> 15.8s;
#     a serialized event whose secret carried 20 trailing backslashes
#     (`json.dumps` doubles each one) -> 18.4s. After: a 40,000-backslash
#     run returns in ~4ms. The vector is not exotic -- any tool output
#     with a sensitive `NAME=` and a Windows path or an escaped blob is
#     exactly the env-dump class this module exists to scrub.
#   * OVER-REDACTION, from the SAME root. An odd tiling shifts PARITY across
#     a following `\n`: on `MY_PASSWORD=<secret>\` + `\n` + `PATH=/usr/bin`
#     the engine paired the SECOND and THIRD backslashes and then ate the
#     separator's `n` as ordinary value material -- swallowing the PATH
#     line, i.e. breaking the very invariant the paragraph above states.
#
# Fencing the lone alternative off a following backslash leaves EXACTLY ONE
# tiling of any run (pairs, then at most one trailing lone backslash). That
# is what makes the scan linear AND what keeps the `\n` parity intact. It
# cannot under-redact: a backslash followed by a backslash is still
# consumed -- by the pair alternative, atomically, which is the reading this
# grammar always intended.
#
# RESIDUAL, named rather than implied: in PLAIN (unserialized) text, a value
# whose lone backslash is followed by `n`/`r`/`t`/`u` still ends there -- the
# rule cannot tell `\n`-the-newline from `\n`-the-two-characters without
# knowing whether the line is JSON, and between under-redacting a rare shape
# and swallowing a whole env dump, this file has already learned which
# mistake costs more (2026-08-13). The residual is always a NON-redaction,
# never an over-redaction, and layer 4 (scan/gate) remains the backstop.
#
# WHAT DID NOT CHANGE, and each is load-bearing: the end-anchored name
# (`total_tokens=` remains untouchable), the 4-character floor, and the fact
# that no branch crosses a newline -- an unquoted value still stops dead at
# whitespace, so a redaction can never swallow a following token or the rest
# of a log line.
_ASSIGNMENT_VALUE_CHAR = r"[^\s\"'\\]"
_ASSIGNMENT_VALUE_CONT = r"[^\s\"'\\,:;)\]}]"
_ASSIGNMENT_VALUE = (
    # (a) double-quoted, including the JSON-escaped \"...\" form
    r"(?P<quote>\\?\")[^\"\r\n]{4,}?(?<!\\)(?=\\?\")"
    # (b) single-quoted
    r"|(?P<squote>')[^'\"\r\n]{4,}?(?<!\\)(?=')"
    # (c) unquoted run, with the two fenced joiners
    r"|(?:" + _ASSIGNMENT_VALUE_CHAR + r"|[\"'](?=" + _ASSIGNMENT_VALUE_CONT + r")"
    r"|\\\\|\\(?![\s\\]|(?-i:[nrtu]))){4,}(?<!\\)"
)

# The negative lookahead keeps an already-redacted value -- bare, quoted, or
# JSON-escaped-quoted -- from being re-redacted into a less specific shape.
ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9_]*(?:" + "|".join(SENSITIVE_NAME_TAILS) + r"))"
    r"(?P<sep>\s*=\s*)"
    r"(?!\\?[\"']?\[REDACTED:)"
    r"(?:" + _ASSIGNMENT_VALUE + r")",
    re.IGNORECASE,
)

# Layer 4 (scan/gate only): candidate runs for the entropy heuristic.
ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_\-=]{28,}")
ENTROPY_THRESHOLD = 4.5  # bits/char; random base64-ish material sits above

# THE PURE-HEX EXCLUSION IS STRUCTURAL, NOT WHOLE-RUN (incident, run
# 31754414275 -- the second consecutive CONVERGED capsule destroyed at the
# publication door, six findings, every one shape=high-entropy-token).
#
# `_entropy_suspicious` has always excluded a candidate that is ENTIRELY
# hex, and that exclusion is not a bias -- it is arithmetic: 16 symbols
# cannot carry more than log2(16) = 4.0 bits/char, so a git SHA or a sha256
# digest is PROVABLY below the 4.5 threshold and can never be honest
# evidence of randomness. But ENTROPY_CANDIDATE's character class contains
# `/` (base64 uses it as DATA, so it cannot simply be dropped), which means
# a SHA sitting inside a URL or a path is never a candidate BY ITSELF: the
# regex swallows the entire path into ONE run, and the all-hex test then
# fails on the merged string. Measured, from the incident's own shapes:
#
#   dae6d114d7821e2081a05d6e4bcd350c88dc2a41                     H = 3.63  clean
#   com/microsoft/amplifier-module-provider-anthropic/
#     amplifier_module_provider_anthropic/_cost                  H = 4.14  clean
#   ...the same two, merged across `/` by the character class     H = 4.62  FIRES
#
# Neither half is random; the MIXTURE reads as random. Shannon entropy of a
# concatenation is not a weighted mean of its parts' entropies: hex digits
# and path words draw from near-disjoint alphabets (14 distinct symbols +
# 20 distinct symbols -> 30), so the union histogram is FLATTER than either
# part's and the plug-in estimator reports more bits/char than either
# constituent (the length-weighted mean of the two parts is 3.98). Alphabet
# heterogeneity across a separator is not evidence of randomness. It is,
# however, exactly what a capsule looks like: the criteria REQUIRE the gate
# to vendor its provider-rates oracle as a plain file and to record "its
# exact version/commit in capsule provenance", so every artifact that names
# that oracle -- DEFINITION.md, the vendored oracle's own header, the gate
# script, and the gate's log output quoted into the rival finding -- carries
# a 40-hex commit SHA inside a long URL path. One structural requirement,
# six findings, one blocked capsule.
#
# So the exclusion this file already makes is applied where it actually
# lives: a digest-length pure-hex SEGMENT is removed from the run before the
# entropy estimate, exactly as `-` and `_` already are. A run containing no
# such segment is scored BYTE-IDENTICALLY to before -- this narrows nothing
# else, and it does not touch the threshold.
#
# The length floor is what keeps this from being a hiding place. A hex-only
# span this long is a SHA/digest by construction, not a coincidence: the
# chance that N characters of genuinely random base64 are all hex digits is
# (16/64)**N = 4**-N, i.e. ~2.3e-10 at N=16. Random secret material cannot
# be smuggled through this exclusion at any useful length.
#
# REAL HEX-SHAPED CREDENTIALS ARE UNAFFECTED, and were never this layer's
# job: an all-hex API key is ALREADY excluded here today by the whole-run
# test. They are covered by layer 1 (the `sk-` / `ghp_` / `github_pat_`
# prefixes), layer 2 (end-anchored sensitive assignments -- `API_KEY=<hex>`
# still fires, and its value is redacted), and layer 3 (the literal values
# of this job's own secrets, matched regardless of shape). This change
# touches ONLY the shape GUESS in layer 4, and only where that guess was
# arithmetically wrong.
HEX_SEGMENT_MIN = 16

# A maximal alphanumeric segment of a candidate run that is entirely hex and
# at least HEX_SEGMENT_MIN long. The lookarounds are what make it MAXIMAL:
# inside a candidate the only non-alphanumeric characters are the structural
# separators `/ + - _ =`, so requiring a non-alphanumeric boundary on both
# sides means `dae6...a41` in `.../dae6...a41/...` matches whole, while the
# leading hex of a mixed segment (`abcdef1234567890xyz`) matches nothing.
STRUCTURAL_HEX_SEGMENT = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{%d,}(?![A-Za-z0-9])" % HEX_SEGMENT_MIN)

# The one shape `gate` may quarantine instead of blocking on. Named, not
# inlined, because the whole split verdict turns on this exact string: it
# is the shape `scan_text` reports for layer 4 and nothing else.
ENTROPY_SHAPE = "high-entropy-token"


def _shannon_entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _without_structural_hex(token: str) -> str:
    """`token` with its digest-length pure-hex segments removed.

    A git SHA / sha256 digest cannot exceed 4.0 bits/char (16 symbols), so
    it is never itself evidence of randomness -- but concatenated with
    neighbouring path text across the `/` in ENTROPY_CANDIDATE's character
    class it INFLATES the plug-in Shannon estimate above what either part
    scores alone (see the STRUCTURAL_HEX_SEGMENT block above). Removing
    those segments before the estimate is the same exclusion the whole-run
    hex test already makes, applied to the segment it actually describes.

    A token with no such segment comes back unchanged, so every other run
    in the corpus is scored exactly as before.
    """
    return STRUCTURAL_HEX_SEGMENT.sub("", token)


def _entropy_suspicious(token: str) -> bool:
    """True if `token` looks like random secret material.

    Exclusions (a documented false-negative bias -- each excluded shape is
    something routine evidence is full of): pure hex (git SHAs, sha256
    digests), pure digits (ids, timestamps), and letters-only runs (long
    identifiers/words). The hex exclusion applies both to a whole run and
    to a digest-length hex SEGMENT of one (a SHA inside a URL path).
    """
    core = token.strip("=").replace("-", "").replace("_", "")
    if not core:
        return False
    lowered = core.lower()
    if all(ch in "0123456789abcdef" for ch in lowered):
        return False  # hex: git SHAs / digests
    if core.isdigit() or core.isalpha():
        return False
    # Score the run with its provably-sub-threshold hex segments removed.
    # Identity for any run that has none; for a provenance URL it is the
    # difference between measuring the path and measuring path+SHA mixed.
    return _shannon_entropy(_without_structural_hex(token)) >= ENTROPY_THRESHOLD


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
        # Exactly one of the two quote groups participates when the value was
        # quoted (issue #289); the opening quote is re-emitted so the line
        # keeps its shape -- `API_KEY="[REDACTED:assignment]"` -- and the
        # closing quote was matched by lookahead, so it is still in the text.
        quote = m.group("quote") or m.group("squote") or ""
        return f"{m.group('name')}{m.group('sep')}{quote}[REDACTED:assignment]"

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
