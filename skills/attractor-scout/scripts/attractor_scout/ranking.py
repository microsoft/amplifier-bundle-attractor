"""Concept 1 — the ranked opportunity list, and the gate that guards it.

    score = n_sessions x leverage x fit / 100
    leverage = med_tool_calls + med_llm_cycles + med_span_capped/60 + 2*(errs/n)

Three properties of this formula are load-bearing and each was measured:

* `n_sessions` is the DISTINCT-session reach, and frequency is an ADMISSION
  floor (>= 2) that then enters the score as a multiplier — it is never a
  ranking signal on its own. Ranked by frequency alone, the top two units in
  the calibration corpus were the machine talking to itself.
* `fit` is BINARY {0, 1}. A Fit-failing unit scores 0 and leaves the ranking
  entirely — but it is still EMITTED, as an honest-NO with its verdict. Zero
  score means "not an automation candidate", never "deleted".
* The **AUTHOR ADMISSION GATE RUNS BEFORE SCORING**. Only human/mixed units
  are ranked. Harness units are not dropped — they are routed to a separate
  waste-findings channel, because the machine's own recurring ceremony is a
  real finding (time to reclaim), just not an opportunity to hand back.

Risk trajectory (a Phase-1 default, adjustable per O2) promotes units whose
recurrence is ACCELERATING: a worsening problem is worth more attention than
a steady one of the same size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import author as author_mod
from . import frequency_signature, honest_no, provenance
from . import leverage as leverage_mod

SCORE_DIVISOR = 100.0

ADMITTED_AUTHORS = frozenset({author_mod.HUMAN, author_mod.MIXED})

ESCALATING = "escalating"
CHRONIC_STABLE = "chronic-stable"
FADING = "fading"
STRUCTURAL = "structural"

#: Recent window for the trajectory classifier, in days.
TRAJECTORY_WINDOW_DAYS = 30
#: Recent-share above this => escalating; below the fading line => fading.
ESCALATING_RATIO = 1.5
FADING_RATIO = 0.5
#: Below this many sessions the trend is noise, not a trajectory.
TRAJECTORY_MIN_N = 4


@dataclass
class RankedUnit:
    unit_id: str
    name: str
    n_sessions: int
    leverage: float
    fit: int
    score: float
    author: str
    verdict: str
    no_class: str | None
    failed_subtest: str | None
    remediation: str | None
    recovery: str
    confidence: str
    provisional: bool
    trajectory: str
    rung: str
    members: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "name": self.name,
            "n_sessions": self.n_sessions,
            "leverage": round(self.leverage, 3),
            "fit": self.fit,
            "score": round(self.score, 3),
            "author": self.author,
            "verdict": self.verdict,
            "no_class": self.no_class,
            "failed_subtest": self.failed_subtest,
            "remediation": self.remediation,
            "recovery": self.recovery,
            "confidence": self.confidence,
            "provisional": self.provisional,
            "trajectory": self.trajectory,
            "rung": self.rung,
            "n_members": len(self.members),
            "members": list(self.members),
            **self.detail,
        }


def score_unit(n_sessions: int, leverage: float, fit: int) -> float:
    return n_sessions * leverage * fit / SCORE_DIVISOR


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_trajectory(
    members: list[dict],
    *,
    now: datetime | None = None,
    window_days: int = TRAJECTORY_WINDOW_DAYS,
) -> str:
    """Is this unit's recurrence accelerating, steady, or fading?

    Compares distinct-session density in the most recent window against the
    unit's own lifetime density. Returns `structural` when there is not
    enough signal to claim a trend — an honest "no trajectory measured",
    never a fabricated one.
    """
    stamps = sorted(t for t in (_parse_ts(m.get("started_at")) for m in members) if t)
    if len(stamps) < TRAJECTORY_MIN_N:
        return STRUCTURAL
    now = now or max(stamps)
    span_days = max((stamps[-1] - stamps[0]).days, 1)
    if span_days <= window_days:
        return STRUCTURAL
    cutoff = now - timedelta(days=window_days)
    recent = sum(1 for t in stamps if t >= cutoff)
    lifetime_rate = len(stamps) / span_days
    recent_rate = recent / window_days
    if lifetime_rate <= 0:
        return STRUCTURAL
    ratio = recent_rate / lifetime_rate
    if ratio >= ESCALATING_RATIO:
        return ESCALATING
    if ratio <= FADING_RATIO:
        return FADING
    return CHRONIC_STABLE


def apply_admission_gate(units: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split units into (admitted, waste_findings, unattributed) by AUTHOR, before scoring.

    A unit's authoritative author is `author_adjudicated` when the
    cluster-level adjudication supplied one, else the deterministic prior.
    The prior over-calls human by design; the adjudication is what recovers
    that, and this function deliberately prefers it when present rather than
    averaging the two into something neither measured.

    **FAIL-HONEST, not fail-open.** This chain used to end in `or HUMAN`: a
    unit that carried no author verdict at all was silently ranked as the
    user's own work. That default is exactly backwards — nothing in the
    corpus positively marks a session as human (see `provenance`), so an
    absent verdict is an absence of evidence, not evidence of a person. Such
    a unit is now routed to `unattributed`: excluded from the ranking,
    reported, and never counted as harness ceremony either, because calling
    it the machine's would be the same unearned claim in the other direction.
    """
    admitted: list[dict] = []
    waste: list[dict] = []
    unattributed: list[dict] = []
    for unit in units:
        label = unit.get("author_adjudicated") or unit.get("author_prior") or unit.get("author")
        if not label:
            unit["author"] = provenance.UNKNOWN
            unattributed.append(unit)
            continue
        unit["author"] = label
        (admitted if label in ADMITTED_AUTHORS else waste).append(unit)
    return admitted, waste, unattributed


def rank(
    units: list[dict],
    *,
    now: datetime | None = None,
) -> dict:
    """Rank pre-assembled units.

    Each input unit needs: `unit_id`, `name`, `members` (extract records),
    optionally `author_prior` / `author_adjudicated`, `rung`, and the fit
    inputs `cycle` / `gate` (booleans) — supplied either by the deterministic
    detectors or by the reasoning-tier verdict pass.

    Returns opportunities (score-ordered), honest-NOs (emitted with verdicts,
    never dropped), waste findings, and units below the frequency floor
    (out of scope — NOT honest-NOs).
    """
    from . import fit_cycle, fit_gate, fit_recovery

    admitted, waste_units, unattributed_units = apply_admission_gate(units)

    opportunities: list[RankedUnit] = []
    honest_nos: list[RankedUnit] = []
    below_floor: list[dict] = []

    for unit in admitted:
        members = unit.get("members") or []
        n = frequency_signature.count_distinct_sessions(members)
        if not frequency_signature.passes_floor(n):
            below_floor.append({"unit_id": unit.get("unit_id"), "name": unit.get("name"), "n_sessions": n})
            continue

        prof = leverage_mod.compute_leverage(members)
        cycle = unit["cycle"] if "cycle" in unit else fit_cycle.cluster_cycle(members)["cycle"]
        gate = unit["gate"] if "gate" in unit else fit_gate.cluster_gate(members)["gate"]
        recovery = fit_recovery.detect(members)
        verdict = honest_no.classify(
            cycle=bool(cycle),
            gate=bool(gate),
            recovery=recovery.verdict,
            provisional_frequency=frequency_signature.is_provisional(n),
        )

        ranked = RankedUnit(
            unit_id=str(unit.get("unit_id") or unit.get("id") or "unit"),
            name=str(unit.get("name") or unit.get("unit_id") or "unnamed unit"),
            n_sessions=n,
            leverage=prof.leverage,
            fit=verdict.fit,
            score=score_unit(n, prof.leverage, verdict.fit),
            author=str(unit.get("author")),
            verdict=verdict.verdict,
            no_class=verdict.no_class,
            failed_subtest=verdict.failed_subtest,
            remediation=verdict.remediation,
            recovery=recovery.verdict,
            confidence=verdict.confidence,
            provisional=frequency_signature.is_provisional(n),
            trajectory=classify_trajectory(members, now=now),
            rung=str(unit.get("rung") or "B"),
            members=sorted({str(m["session_id"]) for m in members if m.get("session_id")}),
            detail={
                "gist": unit.get("gist"),
                "leverage_detail": prof.as_dict(),
                "recovery_detail": recovery.as_dict(),
                "fit_detail": verdict.as_dict(),
            },
        )
        (honest_nos if verdict.verdict == honest_no.HONEST_NO else opportunities).append(ranked)

    opportunities.sort(key=lambda u: (-_trajectory_boost(u), -u.score, u.unit_id))
    honest_nos.sort(key=lambda u: (-u.n_sessions * u.leverage, u.unit_id))

    waste = []
    for unit in waste_units:
        members = unit.get("members") or []
        prof = leverage_mod.compute_leverage(members)
        waste.append(
            {
                "unit_id": unit.get("unit_id") or unit.get("id"),
                "name": unit.get("name"),
                "author": unit.get("author"),
                "n_sessions": frequency_signature.count_distinct_sessions(members),
                "leverage": round(prof.leverage, 3),
                "reclaimable_hours": round(
                    sum(leverage_mod.span_capped(m) for m in members) / 3600.0,
                    2,
                ),
                "note": "harness ceremony - a waste finding to eliminate, not an opportunity to act on",
            }
        )
    waste.sort(key=lambda w: (-(w["n_sessions"] or 0), str(w["unit_id"])))

    # Units with NO author verdict at all. Reported, never ranked, and never
    # relabelled as harness: "we do not know" is the finding.
    unattributed = [
        {
            "unit_id": unit.get("unit_id") or unit.get("id"),
            "name": unit.get("name"),
            "author": provenance.UNKNOWN,
            "n_sessions": frequency_signature.count_distinct_sessions(unit.get("members") or []),
            "note": "no author verdict was produced for this unit - excluded from the ranking rather than presumed human",
        }
        for unit in unattributed_units
    ]
    unattributed.sort(key=lambda u: (-(u["n_sessions"] or 0), str(u["unit_id"])))

    return {
        "opportunities": [u.as_dict() for u in opportunities],
        "honest_no": [u.as_dict() for u in honest_nos],
        "waste_findings": waste,
        "unattributed": unattributed,
        "below_frequency_floor": below_floor,
        "summary": {
            "n_units_in": len(units),
            "n_admitted": len(admitted),
            "n_waste": len(waste),
            "n_unattributed": len(unattributed),
            "n_opportunities": len(opportunities),
            "n_honest_no": len(honest_nos),
            "n_below_floor": len(below_floor),
            "honest_no_rate": (len(honest_nos) / max(len(opportunities) + len(honest_nos), 1)),
        },
    }


def _trajectory_boost(unit: RankedUnit) -> int:
    """Escalating units jump the queue; nothing else reorders."""
    return 1 if unit.trajectory == ESCALATING else 0


def sample_simple_to_complex(opportunities: list[dict], *, k: int = 5) -> list[dict]:
    """Pick a spread across the GRAIN axis, orthogonal to score.

    The top-of-report sample is deliberately NOT "the top k by score" — it
    walks simple -> complex so the user sees the RANGE of what was found
    (one cheap A-rung dedup win next to one expensive multi-session B-rung
    habit). Ranking still orders the full list behind the modals.
    """
    if not opportunities:
        return []
    ordered = sorted(opportunities, key=lambda u: (u.get("leverage", 0.0), u.get("n_sessions", 0)))
    if len(ordered) <= k:
        return ordered
    step = (len(ordered) - 1) / (k - 1) if k > 1 else 0
    picked: list[dict] = []
    seen: set[str] = set()
    for i in range(k):
        cand = ordered[min(len(ordered) - 1, round(i * step))]
        uid = str(cand.get("unit_id"))
        if uid not in seen:
            seen.add(uid)
            picked.append(cand)
    return picked
