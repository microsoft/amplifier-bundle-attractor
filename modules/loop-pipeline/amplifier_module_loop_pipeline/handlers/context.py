"""HandlerContext — dependency bundle for HandlerRegistry.

Replaces the ``**kwargs: Any`` slurp in HandlerRegistry.__init__ with an
explicit, typed, frozen dataclass.  All handler dependencies are named fields
rather than blind keyword arguments; pyright catches missing or misspelled
deps at write-time rather than at runtime.

Spec coverage: T2.1 (HandlerContext noun), Section 4.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HandlerContext:
    """Dependency bundle passed to HandlerRegistry at construction time.

    All fields are typed ``| None`` — an explicit, documented decision about
    which dependencies are always present vs situational.  This is NOT the
    same as the old ``kwargs.get()`` accident: every field is named, every
    absence is intentional.

    Required for a full production pipeline run:
        backend       — AI backend for codergen nodes.
        hooks         — Event-emission bus.
        cancel_event  — Cross-thread cancellation signal (threading.Event).

    Situational:
        interviewer   — Human-gate approval driver; None outside human-gate
                        context.

    Design note: making all fields ``| None = None`` keeps test ergonomics
    clean (callers that only care about shape routing need only
    ``HandlerContext()``).  The improvement over ``**kwargs: Any`` is that
    every field is NAMED and TYPED — no silent typo swallowing, no unknown
    kwargs accepted, IDE autocomplete works.
    """

    backend: Any | None = None
    hooks: Any | None = None
    cancel_event: Any | None = None
    interviewer: Any | None = None
