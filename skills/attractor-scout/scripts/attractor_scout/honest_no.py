"""S6 — the HONEST-NO boundary: a first-class output, not a rejection bin.

61% of clusters land here (33 of 54: 17 recipe / 14 one-shot / 2 fragile).
**The classification IS the value.** A shortlist is trusted precisely because
the tool is willing to decline — so every Fit-failing unit is emitted WITH
its verdict, which sub-test it failed, and what to do about it. Nothing is
silently dropped.

Four outcomes, and the distinction between the last two is the whole point:

    fails 4a  -> `recipe`     linear pipeline; author it as a recipe
    fails 4b  -> `one-shot`   real loop, no gate; ADD ONE GATE and it converts
    fails 4c  -> `fragile`    errors WITHOUT recoveries -- a real 4c failure
    4c unobs. -> `unproven`   a DOWNGRADE, never a NO, never a FAIL

A unit that fails FREQUENCY is out of scope entirely — it is not an
honest-NO, because there is nothing recurring to decline.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import fit_recovery

OPPORTUNITY = "OPPORTUNITY"
OPPORTUNITY_UNPROVEN = "OPPORTUNITY(unproven)"
HONEST_NO = "HONEST-NO"

RECIPE = "recipe"
ONE_SHOT = "one-shot"
FRAGILE = "fragile"
UNPROVEN = "unproven"

REMEDIATION = {
    RECIPE: (
        "This is a linear pipeline, not an attractor: the work does not iterate. "
        "Author it as a recipe — you get the repeatability without the loop machinery."
    ),
    ONE_SHOT: (
        "This really does loop, but nothing checks the exit. It is one gate away from "
        "converting: add a machine-checkable terminal verification (a test, a lint, a "
        "readback) and this becomes an attractor."
    ),
    FRAGILE: (
        "Errors were observed with no recovery. Before automating this, make the failing "
        "step re-runnable — an attractor that cannot survive a bad day will strand you "
        "mid-loop."
    ),
    UNPROVEN: (
        "Shape qualifies, but no bad day was ever observed, so resilience is UNPROVEN, not "
        "failed. Treat the first automated run as the stress test."
    ),
}


@dataclass
class FitVerdict:
    """Composed Concept-4 verdict for one unit."""

    verdict: str
    fit: int
    no_class: str | None
    failed_subtest: str | None
    remediation: str | None
    cycle: bool
    gate: bool
    recovery: str
    confidence: str
    provisional_frequency: bool = False

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "fit": self.fit,
            "no_class": self.no_class,
            "failed_subtest": self.failed_subtest,
            "remediation": self.remediation,
            "cycle": self.cycle,
            "gate": self.gate,
            "recovery": self.recovery,
            "confidence": self.confidence,
            "provisional_frequency": self.provisional_frequency,
        }


def classify(
    *,
    cycle: bool,
    gate: bool,
    recovery: str,
    provisional_frequency: bool = False,
) -> FitVerdict:
    """Deterministic mapping over the S3 / S4 / S5 outcomes.

    Precedence is 4a -> 4b -> 4c, matching the order in which a unit stops
    being an attractor: no loop at all is a stronger disqualifier than an
    ungated loop, which is stronger than an unproven one. Only the FIRST
    failed sub-test is reported as `failed_subtest` so the remediation is
    actionable rather than a list of everything wrong.
    """
    conf = fit_recovery.CONFIDENCE.get(recovery, "low")

    if not cycle:
        return FitVerdict(
            HONEST_NO, 0, RECIPE, "4a", REMEDIATION[RECIPE], cycle, gate, recovery, conf, provisional_frequency
        )
    if not gate:
        return FitVerdict(
            HONEST_NO, 0, ONE_SHOT, "4b", REMEDIATION[ONE_SHOT], cycle, gate, recovery, conf, provisional_frequency
        )
    if recovery == fit_recovery.FRAGILE:
        return FitVerdict(
            HONEST_NO, 0, FRAGILE, "4c", REMEDIATION[FRAGILE], cycle, gate, recovery, conf, provisional_frequency
        )
    if recovery == fit_recovery.UNKNOWN:
        # A DOWNGRADE, not a NO. fit stays 1 — the unit still ranks; the
        # caveat rides along. There is deliberately NO path from "no errors
        # observed" to a FAIL verdict.
        return FitVerdict(
            OPPORTUNITY_UNPROVEN,
            1,
            None,
            None,
            REMEDIATION[UNPROVEN],
            cycle,
            gate,
            recovery,
            conf,
            provisional_frequency,
        )
    return FitVerdict(OPPORTUNITY, 1, None, None, None, cycle, gate, recovery, conf, provisional_frequency)


def is_fit_failing(verdict: FitVerdict) -> bool:
    return verdict.verdict == HONEST_NO


def summarize(verdicts: list[FitVerdict]) -> dict:
    """Composition of the honest-NO branch (the corroboration measurement)."""
    counts = {RECIPE: 0, ONE_SHOT: 0, FRAGILE: 0}
    n_opportunity = 0
    n_unproven = 0
    for verdict in verdicts:
        if verdict.no_class in counts:
            counts[verdict.no_class] += 1
        elif verdict.verdict == OPPORTUNITY_UNPROVEN:
            n_unproven += 1
            n_opportunity += 1
        elif verdict.verdict == OPPORTUNITY:
            n_opportunity += 1
    total = len(verdicts) or 1
    n_no = sum(counts.values())
    return {
        "n_units": len(verdicts),
        "n_opportunity": n_opportunity,
        "n_unproven": n_unproven,
        "n_honest_no": n_no,
        "honest_no_rate": n_no / total,
        "by_class": counts,
    }
