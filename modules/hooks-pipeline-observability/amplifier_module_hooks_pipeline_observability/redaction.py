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

ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9_]*(?:" + "|".join(SENSITIVE_NAME_TAILS) + r"))"
    r"(?P<sep>\s*=\s*)"
    r"(?P<quote>[\"']?)"
    r"(?!\[REDACTED:)"
    r"(?P<value>[^\s\"'\\]{4,})",
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
        return (
            f"{m.group('name')}{m.group('sep')}{m.group('quote')}"
            f"{REDACTION_MARKER_PREFIX}assignment]"
        )

    text = ASSIGNMENT_PATTERN.sub(_assignment_sub, text)
    return text, findings
