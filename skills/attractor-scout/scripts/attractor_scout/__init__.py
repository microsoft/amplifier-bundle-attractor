"""attractor-scout engine layer — the deterministic shared library.

All on-rail logic lives here ONCE. The CLI (`attractor_scout_cli.py`) and the
skill's pipeline steps are thin wrappers over these functions; nothing
downstream re-implements a detector.

Public surface:

    discover.enumerate_sessions / qualify   discovery spine (E1/E2/E3, fail-loud)
    extract.extract_all                     per-session records
    author.classify_authors                 S8 deterministic prior
    frequency_signature                     S1 (A-rung dedup floor)
    leverage                                S2 (cost-now toil)
    fit_cycle / fit_gate / fit_recovery     S3 / S4 / S5
    honest_no                               S6 (the complement, first-class)
    ranking                                 Concept 1 (admission gate + score)
    render                                  deterministic self-contained HTML
    graph                                   mode=auto|graph|jsonl seam
    pipeline                                the composed end-to-end run

Tier C (local JSONL only) is the universal floor and expresses every signal.
The graph is a sharpener, never a precondition.
"""

from __future__ import annotations

from .errors import AttractorScoutError, EmptyCorpusError, GraphUnavailable, JsonlSchemaMismatch
from .naming import SKILL_NAME, SKILL_TITLE

__all__ = [
    "AttractorScoutError",
    "EmptyCorpusError",
    "GraphUnavailable",
    "JsonlSchemaMismatch",
    "SKILL_NAME",
    "SKILL_TITLE",
    "__version__",
]

__version__ = "0.1.0"
