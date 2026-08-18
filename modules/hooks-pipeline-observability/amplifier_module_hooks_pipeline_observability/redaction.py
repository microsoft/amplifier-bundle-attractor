"""Write-time secret redaction for persisted session events (issue #198).

WHY THIS EXISTS (incident, 2026-08-11).  A worker agent inside a pipeline run
executed a tool that dumped its environment.  The ``SessionEventPersister``
(EXTENSIONS.md Section 26) wrote the ``tool:post`` payload VERBATIM to
``<stage_dir>/sessions/<id>/events.jsonl`` -- including a literal
``OPENAI_API_KEY`` value of the ``sk-proj-...`` shape (spelled apart here so
this file is not itself secret-shaped material) -- and CI uploaded it inside a
PUBLIC run-evidence artifact.  A workflow-level scrub-before-upload was added
separately (``.github/capsule-pipeline/scrub_secrets.py``), but that guard
sits at the UPLOAD door, one hop downstream of the leak: it can only clean a
file that already has the credential on disk, and it protects exactly one
consumer.  Anything else that reads the run dir -- a maintainer tailing the
file, a bug report pasting it, a sandbox that syncs it -- was still reading a
live key.  Defense in depth belongs at the WRITE seam, and this module is the
redaction machinery the persister applies to each event line *before* it
reaches disk.

WHY SHAPE-TARGETED AND NOT ENTROPY -- the load-bearing scoping decision.
The canonical detection set (``.github/capsule-pipeline/scrub_secrets.py``)
has four layers.  This module ports layers 1 and 2 -- the KNOWN CREDENTIAL
SHAPES -- and deliberately does NOT port layer 4, the high-entropy-token
heuristic, because that heuristic was MEASURED WRONG on this exact file
class.  From that script's own docstring (issue #206): worker-session
payloads in ``sessions/*/events.jsonl`` "are legitimately full of
high-entropy runs (digests, base64 fragments, request ids), so the gate
blocked the evidence upload on EVERY run" -- 4 real runs out of 4, and 487
findings on run 31689374533, every one entropy-shaped and not one a
credential.  At the upload door an over-eager heuristic costs one run's
evidence.  At the WRITE seam it costs that evidence PERMANENTLY: the original
bytes are never written, so there is nothing left to recover.  Section 26
exists to make a run forensically traceable; a redactor that eats request ids
and correlation tokens on suspicion of randomness defeats the extension it is
supposed to protect.  Measured with the canonical heuristic itself:

    43-char alphanumeric request id   H = 4.83-4.99  ->  WOULD be redacted
    40-char git SHA / sha256 digest   H = 3.28       ->  survives (hex-excluded)
    session UUID                      H = 3.97       ->  survives

The middle rows are why the heuristic looks safe in a spot check; the first
row is the one that matters, and it is ordinary observability.  So: named
credential shapes only.  A credential's whole job is to be recognizable to
the service that accepts it, which is exactly what makes it shape-matchable
without guessing at randomness.

WHY A LOCAL COPY AND NOT AN IMPORT.  ``scrub_secrets.py`` lives under
``.github/`` -- a workflow script, deliberately stdlib-only so it runs on a
bare Actions runner, and not a package on any import path this module could
depend on.  This module must not depend on ``.github/``.  Vendoring a shared
distribution for four regexes would be heavier than the problem.  Instead the
patterns are copied here and a DRIFT TRIPWIRE test
(``tests/test_session_events_redaction.py``) loads the canonical script BY
PATH and asserts the two sets are still identical, so "two copies" cannot
silently become "two behaviors".

HONEST RESIDUALS, stated rather than implied:
  - Layer 3 of the canonical set (redacting the literal VALUES of the env
    vars the CI job holds) is NOT ported.  It is a workflow-context
    mechanism -- the workflow passes its own secrets into that script's env
    on purpose -- and making write-time redaction depend on the ambient
    process environment would be non-deterministic and untestable at this
    seam.  The incident's own credential is covered here twice over, by
    shape (layer 1) and by assignment (layer 2).
  - The assignment rule matches ``NAME=value``, the env-dump shape from the
    incident.  It does NOT match a JSON *key* spelled ``"api_key": "..."``
    (JSON separates with ``:``, not ``=``).  A structured field holding a
    credential is still caught whenever the value itself carries a known
    token shape, which is the usual case; a shapeless credential in such a
    field is a KNOWN GAP, left to the upload gate rather than closed by
    widening a pattern that has already corrupted a shipped artifact once
    (see the end-anchor note below).
"""

from __future__ import annotations

import re

#: Layer 1 -- known token shapes.  Copied verbatim from
#: ``.github/capsule-pipeline/scrub_secrets.py``'s ``TOKEN_PATTERNS``; the
#: drift tripwire test asserts they stay identical.
#:
#: The character classes stop at backslash and quote.  That is what makes
#: these patterns safe to apply to an ALREADY-SERIALIZED JSON line: a match
#: can never span a string boundary or eat an escape, so the redacted line
#: still parses as the same JSON object with one string value shortened.
TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github-token", re.compile(r"gh[posur]_[A-Za-z0-9]{20,}")),
]

#: Layer 2 -- ``NAME=value`` assignments, the incident's env-dump shape.
#:
#: THE END-ANCHOR IS LOAD-BEARING and is not a style choice: matching any
#: name *containing* one of these words once rewrote 54 ``input_tokens=`` /
#: ``total_tokens=`` assignments inside a shipped, later-executed artifact
#: (2026-08-13, PR #205).  Credential names put the sensitive word at the
#: END (``OPENAI_API_KEY``, ``GITHUB_TOKEN``, ``CLIENT_SECRET``); ordinary
#: identifiers that merely contain it (``input_tokens``, ``max_tokens``) do
#: not.  ``_TOKEN`` is singular-only for the same reason.
SENSITIVE_NAME_TAILS = (
    "API_KEYS?",
    "SECRET_ACCESS_KEY",
    "_TOKEN",
    "_SECRET",
    "PASSWORD",
    "CREDENTIALS?",
)

#: THE VALUE GRAMMAR (issue #289), copied from the canonical set with the
#: rest of layer 2.  The pre-#289 rule spelled a value as ONE character class
#: -- ``[^\s"'\\]{4,}`` -- which stops at the FIRST whitespace, quote or
#: backslash, so a secret containing one of those within its first few
#: characters escaped BOTH doors: ``PASSWORD=abc\<tail>`` and
#: ``PASSWORD=abc'<tail>`` redacted NOTHING (the class dies at char 3),
#: ``PASSWORD=abcd"<tail>`` left the tail, and ``API_KEY="secret value"``
#: redacted only up to the first space.
#:
#: A value is now one of three shapes, tried IN THIS ORDER, each anchored so
#: that widening what a value MAY CONTAIN never widens how far a value may
#: REACH.  That anchoring is what keeps this rule safe HERE in particular:
#: this seam applies it to an ALREADY-SERIALIZED JSON LINE, and a match that
#: crossed a JSON string boundary would destroy the record instead of the
#: secret (``SessionEventPersister`` re-parses the redacted line and WITHHOLDS
#: the payload when it no longer parses -- an over-reaching rule here costs
#: the whole event, permanently).
#:
#:   (a) DOUBLE-QUOTED -- ``NAME="..."`` and the JSON-escaped ``NAME=\"...\"``
#:       form this seam actually sees.  The content class excludes ``"``
#:       outright, so the match STOPS at the first closing quote and can never
#:       leave the string it started in; it is non-greedy and refuses to end
#:       on a backslash (``(?<!\\)``), so the closing ``\"``'s escape is never
#:       eaten.  The closing quote is matched by lookahead, never consumed,
#:       and the opening quote is re-emitted by the substitution:
#:       ``API_KEY="[REDACTED:assignment]"``.
#:
#:   (b) SINGLE-QUOTED -- ``NAME='...'``.  Its content additionally excludes
#:       ``"``: an UNTERMINATED single quote inside a JSON string would
#:       otherwise let the match run past that string's own closing ``"`` to
#:       the next apostrophe on the line (``"note": "don't"``), silently
#:       deleting an unrelated field.  Stated cost: a single-quoted value that
#:       itself contains a double quote is only partly covered.
#:
#:   (c) UNQUOTED -- the pre-#289 class plus two fenced joiners: a quote joins
#:       only when the NEXT character is ordinary value material (in JSON a
#:       string-terminating quote is ALWAYS followed by ``,`` ``}`` ``]``
#:       ``:`` or whitespace, so this joiner provably cannot consume a string
#:       terminator).  A backslash joins in two forms: an ESCAPED PAIR
#:       (``\\``) joins ATOMICALLY -- that is how a literal backslash inside
#:       a secret reaches this seam once the record is serialized -- while a
#:       LONE backslash joins UNLESS it is followed by ANOTHER backslash
#:       (that case belongs to the pair -- see the fence below), or it opens
#:       one of the escapes that encode a record/field SEPARATOR (``\n``,
#:       ``\r``, ``\t``) or an arbitrary
#:       code point (``\u``).  Stopping at those is what keeps a serialized
#:       env dump (``MY_PASSWORD=<secret>\nPATH=/usr/bin`` on ONE line) from
#:       being swallowed whole.  The run may not END on a backslash, so it
#:       can never leave a dangling escape that would corrupt the string.
#:
#: THE FENCE ON THE LONE JOINER (``[\s\\]``, not ``\s``) IS NOT COSMETIC --
#: it is what makes this rule TERMINATE, and it matters most HERE, where the
#: input is attacker-influenced tool output arriving on the hot write path.
#: Without it a backslash is AMBIGUOUS (half of the atomic pair ``\\``, or
#: the lone alternative), so a run of N backslashes has Fibonacci-many
#: tilings and the trailing ``(?<!\\)`` forces the engine to enumerate every
#: one.  Measured at THIS seam before the fence: a serialized event whose
#: secret carried 20 trailing backslashes took 18.4s to redact -- one such
#: event stalls the persister.  After: a 40,000-backslash run returns in
#: ~4ms.  The SAME ambiguity also over-redacted: an odd tiling shifted
#: PARITY across a following ``\n`` and swallowed the ``PATH=/usr/bin`` line
#: the paragraph above promises will survive.  The fence leaves exactly ONE
#: tiling of any run, which is what makes the scan linear AND keeps the
#: ``\n`` parity intact.  It cannot under-redact: a backslash before a
#: backslash is still consumed, by the pair alternative, atomically.
#:
#: RESIDUAL, named rather than implied: in PLAIN text a lone backslash
#: followed by ``n``/``r``/``t``/``u`` still ends the value -- the rule cannot
#: tell ``\n``-the-newline from ``\n``-the-two-characters without knowing
#: whether the line is JSON.  At THIS seam the input is always JSON, so the
#: stop is the CORRECT reading here; the residual belongs to the shared
#: canonical rule, and it is always a non-redaction, never an over-redaction.
#:
#: Unchanged: the end-anchored name, the 4-character floor, and the fact that
#: no branch crosses a newline -- an unquoted value still stops dead at
#: whitespace, so a redaction can never swallow a following token.
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

#: The marker written in place of redacted material.  Readers may search for
#: this prefix to tell a scrubbed record from a clean one.
REDACTION_MARKER_PREFIX = "[REDACTED:"


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact known credential shapes in ``text``.

    Returns ``(redacted_text, findings)`` where ``findings`` holds ONE shape
    string per redacted span (so ``len(findings)`` is a span count, not a
    pattern count).  A shape string is the credential class -- ``openai-key``,
    ``github-token`` -- or ``assignment:<NAME>`` for a sensitive assignment,
    which keeps WHICH variable leaked visible in the evidence while the value
    itself does not survive.

    Surgical by construction: only the matched span is replaced with
    ``[REDACTED:<shape>]``; every surrounding byte is untouched.  Token
    patterns run before assignments (most specific first), and the
    assignment pattern's negative lookahead keeps an already-redacted value
    from being re-redacted into a less specific shape.

    A value the patterns do not match is returned VERBATIM.  That is the
    contract this seam is held to, not merely a side effect -- see the
    module docstring's entropy discussion.
    """
    findings: list[str] = []

    for shape, pattern in TOKEN_PATTERNS:
        text, n = pattern.subn(f"{REDACTION_MARKER_PREFIX}{shape}]", text)
        findings.extend([shape] * n)

    def _assignment_sub(m: re.Match[str]) -> str:
        findings.append(f"assignment:{m.group('name')}")
        # Exactly one of the two quote groups participates when the value was
        # quoted (issue #289); the opening quote is re-emitted so the line
        # keeps its shape -- `API_KEY="[REDACTED:assignment]"` -- and the
        # closing quote was matched by lookahead, so it is still in the text.
        quote = m.group("quote") or m.group("squote") or ""
        return (
            f"{m.group('name')}{m.group('sep')}{quote}"
            f"{REDACTION_MARKER_PREFIX}assignment]"
        )

    text = ASSIGNMENT_PATTERN.sub(_assignment_sub, text)
    return text, findings
