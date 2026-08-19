"""S3 / Fit-4a — CYCLE: does the unit actually iterate, or is it one straight shot?

**Detection is STRUCTURAL, never lexical.** This is the single most consequential
implementation decision in the fit detectors. Measured on the calibration
corpus:

    explicit loop markers   3.2%   (69 sessions)
    implicit loops         53.0%   (1,147 sessions)
    linear                 46.2%
    implicit : explicit  = 16.6 : 1

A detector that keys on `recipe:loop_*` events or retry vocabulary in prompt
text finds **3.2% of the loops that exist** and collapses Fit to 0 across
nearly the whole corpus. Among sessions that do real work (>=6 tool calls),
implicit-loop density is 99.8% — once a session is non-trivial it essentially
always loops. The discriminator is not *whether* it loops but whether anything
CHECKS it (-> S4).

`LOOP_MARKER_RE` prompt hits are kept as a weak corroborator and author
signal. They are never the detector.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Same tool re-invoked this many times...
REPEAT_THRESHOLD = 3
#: ...within a window of this many consecutive tool calls.
WINDOW = 6


@dataclass
class CycleVerdict:
    cycle: bool
    explicit: bool
    implicit: bool
    error_retry: bool
    evidence: str

    def as_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "explicit": self.explicit,
            "implicit": self.implicit,
            "error_retry": self.error_retry,
            "evidence": self.evidence,
        }


def windowed_repeat(tools: list[str], *, threshold: int = REPEAT_THRESHOLD, window: int = WINDOW) -> str | None:
    """Return the tool that repeats `threshold`x inside any `window`-call span.

    This is the primary structural loop detector: a sliding window over the
    tool stream. It is strictly stronger evidence than a whole-session
    frequency count, because a tool used three times across a hundred calls
    is not a loop — three times inside six calls is.
    """
    if len(tools) < threshold:
        return None
    for start in range(len(tools) - threshold + 1):
        chunk = tools[start : start + window]
        counts: dict[str, int] = {}
        for tool in chunk:
            counts[tool] = counts.get(tool, 0) + 1
            if counts[tool] >= threshold:
                return tool
    return None


def detect(rec: dict) -> CycleVerdict:
    """Structural cycle detection for one session record.

    Evidence precedence: windowed tool repetition > error->same-tool retry >
    explicit `recipe:loop_*` events. A record extracted by an older miner
    that carries only a precomputed `implicit_loop` boolean (and no full
    `tool_all` stream) is honoured via that field rather than silently
    reported as linear — an absent stream is a missing observation, not
    evidence of linearity.
    """
    tools = rec.get("tool_all") or []
    explicit = bool(rec.get("n_explicit_loop_events", 0)) or bool(rec.get("explicit_loop"))
    error_retry = int(rec.get("n_err_recover", 0) or 0) > 0

    if tools:
        repeated = windowed_repeat([str(t) for t in tools])
        implicit = repeated is not None
        evidence = f"tool {repeated!r} repeated >={REPEAT_THRESHOLD}x within <={WINDOW} calls" if repeated else ""
    elif "implicit_loop" in rec:
        implicit = bool(rec["implicit_loop"])
        evidence = "inherited implicit_loop flag (no full tool stream in record)" if implicit else ""
    else:
        implicit = False
        evidence = ""

    if not evidence and error_retry:
        evidence = "error -> same-tool retry"
    if not evidence and explicit:
        evidence = "explicit recipe:loop_* event"

    return CycleVerdict(
        cycle=bool(implicit or explicit or error_retry),
        explicit=explicit,
        implicit=implicit,
        error_retry=error_retry,
        evidence=evidence or "strictly linear tool sequence",
    )


def detect_explicit_only(rec: dict) -> CycleVerdict:
    """Scenario-4 CONTROL ARM: the lexical/explicit-only detector.

    Kept runnable ONLY so the 16.6:1 implicit:explicit ratio stays a
    measurement rather than a claim. Never a production path.
    """
    explicit = bool(rec.get("n_explicit_loop_events", 0)) or bool(rec.get("explicit_loop"))
    lexical = int(rec.get("loop_markers", 0) or 0) > 0
    return CycleVerdict(
        cycle=bool(explicit or lexical),
        explicit=explicit,
        implicit=False,
        error_retry=False,
        evidence="explicit marker" if explicit else ("lexical prompt marker" if lexical else "none"),
    )


def cluster_cycle(members: list[dict]) -> dict:
    """Cluster-level 4a: does the unit's work loop?

    A cluster loops if a MAJORITY of members do. Majority, not any-member,
    so one accidentally-looping session cannot certify a linear unit.
    """
    verdicts = [detect(m) for m in members]
    n = len(verdicts) or 1
    n_cycle = sum(1 for v in verdicts if v.cycle)
    return {
        "cycle": n_cycle * 2 > n,
        "cycle_share": n_cycle / n,
        "n_members": len(verdicts),
        "n_cycle": n_cycle,
        "n_explicit": sum(1 for v in verdicts if v.explicit),
        "n_implicit": sum(1 for v in verdicts if v.implicit),
    }
