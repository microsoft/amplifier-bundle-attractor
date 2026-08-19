"""S5 / Fit-4c — RECOVERY: would this survive one node having a bad day?

**The critical rule: never render 4c as FAIL from absence.** 65.2% of sessions
simply never hit an error, and "no bad day observed" is not "would not survive
a bad day". Fabricating a FAIL from an unobserved condition is the single
easiest way for this tool to lose the user's trust, and it is the failure this
module is built to make structurally impossible: `UNKNOWN` is a distinct
verdict value that the honest-NO mapper and the renderer both handle
explicitly, and there is no code path from "zero errors" to `FAIL`.

Measured shape of the evidence:

    >=1 tool:post error                       34.8% of sessions
    >=1 error -> same-tool retry              32.3%
    error + retry + completed (full proof)    31.9%
    zero errors -- 4c unobservable            65.2%
    clusters with SOME 4c evidence            40/54 (74%)
    clusters with literally zero errors       13/54

Only `fragile` — errors WITHOUT recoveries — is a genuine 4c failure. Just 2
of 54 clusters earned it.

This module is the DETERMINISTIC HALF. The judgment half (does the clustered
goal tolerate a flaky sub-step: re-runnable, no irreversible mid-loop side
effect?) belongs to the reasoning-tier verdict sub-agent, which receives this
module's structured features as input.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS_HIGH = "PASS-high"
PASS_PROVISIONAL = "PASS-provisional"
UNKNOWN = "UNKNOWN"
FRAGILE = "FRAGILE"

CONFIDENCE = {
    PASS_HIGH: "high",
    PASS_PROVISIONAL: "medium",
    UNKNOWN: "low",
    FRAGILE: "medium",
}

SUCCESS_STATUSES = frozenset({"completed", "complete", "success", "succeeded"})


@dataclass
class RecoveryVerdict:
    verdict: str
    confidence: str
    n_errors: int
    n_recoveries: int
    n_full_proof_members: int
    note: str

    @property
    def is_fail(self) -> bool:
        """ONLY `fragile` is a real 4c failure. UNKNOWN is never a failure."""
        return self.verdict == FRAGILE

    @property
    def unobserved(self) -> bool:
        return self.verdict == UNKNOWN

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "n_errors": self.n_errors,
            "n_recoveries": self.n_recoveries,
            "n_full_proof_members": self.n_full_proof_members,
            "note": self.note,
            "is_fail": self.is_fail,
            "unobserved": self.unobserved,
        }


def member_full_proof(rec: dict) -> bool:
    """One member showing error AND recovery AND a completed status."""
    errs = int(rec.get("n_tool_errors", 0) or 0)
    recov = int(rec.get("n_err_recover", 0) or 0)
    status = str(rec.get("status") or "").lower()
    return errs > 0 and recov > 0 and status in SUCCESS_STATUSES


def detect(members: list[dict]) -> RecoveryVerdict:
    """Cluster-level 4c verdict.

    PASS-high         >=1 member: error AND recovery AND completed
    PASS-provisional  cluster errors>0 and recoveries>0, no single member
                      shows all three
    UNKNOWN           zero error events anywhere -> 4c UNOBSERVED.
                      NOT a failure. Downgrades the cluster verdict to
                      OPPORTUNITY(unproven).
    FRAGILE           errors WITHOUT recoveries -- the only true 4c FAIL
    """
    n_errors = sum(int(m.get("n_tool_errors", 0) or 0) for m in members)
    n_recoveries = sum(int(m.get("n_err_recover", 0) or 0) for m in members)
    n_full = sum(1 for m in members if member_full_proof(m))

    if n_errors == 0:
        return RecoveryVerdict(
            UNKNOWN,
            CONFIDENCE[UNKNOWN],
            0,
            n_recoveries,
            0,
            "no error events observed in any member - 4c UNOBSERVED, not failed. "
            "'No bad day observed' is not 'would not survive a bad day'.",
        )
    if n_full > 0:
        return RecoveryVerdict(
            PASS_HIGH,
            CONFIDENCE[PASS_HIGH],
            n_errors,
            n_recoveries,
            n_full,
            f"{n_full} member(s) hit a real error, retried the same tool, and still completed.",
        )
    if n_recoveries > 0:
        return RecoveryVerdict(
            PASS_PROVISIONAL,
            CONFIDENCE[PASS_PROVISIONAL],
            n_errors,
            n_recoveries,
            0,
            "errors and recoveries both present at cluster level, but no single member "
            "shows error + retry + completion together.",
        )
    return RecoveryVerdict(
        FRAGILE,
        CONFIDENCE[FRAGILE],
        n_errors,
        0,
        0,
        f"{n_errors} error(s) observed with zero same-tool recoveries - the one shape "
        f"that justifies a genuine 4c failure.",
    )


def zero_error_cluster(members: list[dict]) -> bool:
    return sum(int(m.get("n_tool_errors", 0) or 0) for m in members) == 0
