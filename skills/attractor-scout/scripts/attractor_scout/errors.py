"""Named, fail-loud error types.

Design contract (design.md §6, evaluation-scenarios.md Scenario 7 Layer 1):
a stale vendored copy must never be able to return a wrong answer, and an
empty/mis-pointed root must never yield a fabricated count. Both stop the run
with a NAMED exception carrying the searched root in its message.
"""

from __future__ import annotations


class AttractorScoutError(Exception):
    """Base class for every fail-loud condition in this library."""


class JsonlSchemaMismatch(AttractorScoutError):
    """A `metadata.json` did not declare the expected format/version.

    Raised BEFORE any event line of the offending session is read, so a
    schema-mismatched corpus cannot contribute a single record. Scenario 7
    Layer 1b machine-checks the exception type AND that the post-mismatch
    processed-record counter is exactly 0.
    """

    def __init__(self, path: str, detail: str, *, expected: str) -> None:
        self.path = path
        self.detail = detail
        self.expected = expected
        super().__init__(f"schema mismatch at {path}: {detail} (expected {expected})")


class EmptyCorpusError(AttractorScoutError):
    """Discovery found nothing under the resolved root.

    The message contains the exact substring `looked in <root>, found 0`
    required by Scenario 7 Layer 1a.
    """

    def __init__(self, root: str, what: str = "sessions") -> None:
        self.root = root
        self.what = what
        super().__init__(
            f"looked in {root}, found 0 {what}. "
            f"Expected the canonical marker "
            f"<root>/<workspace>/sessions/<id>/context-intelligence/events.jsonl"
        )


class GraphUnavailable(AttractorScoutError):
    """The Tier-A/B graph endpoint did not answer the liveness probe.

    Never surfaced to a caller in `mode=auto` (it silently falls back to the
    Tier-C JSONL path with an honest note); only raised for `mode=graph`,
    where the caller explicitly demanded the unproven path.
    """
