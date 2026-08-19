"""S1 — Frequency: the A-rung dedup floor (NOT the discovery mechanism).

Measured on the calibration corpus: 965 distinct tool-sequence signatures, only
82 shared by >=2 sessions, and 25.5% of sessions have no tool calls at all
(structurally invisible to A-rung matching). 48 of 54 global clusters are
findable ONLY by the semantic B-rung pass. So this module is a cheap dedup
pre-pass, not the yield engine — a fact this docstring states so nobody
promotes it later by accident.

The frequency COUNT itself (`count_distinct_sessions`) is load-bearing
everywhere: it is the admission floor for Concept 1 and the `n` in the
ranking formula. It counts DISTINCT FULL session ids — never occurrence
lines, never 8-char prefixes, and children are folded into their root before
counting (C1/C2 discipline).
"""

from __future__ import annotations

from collections import defaultdict

#: Admission floor. Never a ranking signal (calibration (i): moving to >=3
#: costs 2 of 19 human opportunities for a 15% cluster reduction).
FREQUENCY_FLOOR = 2

#: n in this range is admitted but flagged so a human reads it with the
#: right prior.
PROVISIONAL_MAX_N = 3


def count_distinct_sessions(members: list[dict]) -> int:
    """Distinct-session reach of a unit.

    Keyed on the FULL `session_id`. A record that was folded into a root
    contributes its parent's identity, not its own, so a child session can
    never inflate reach.
    """
    return len({str(m["session_id"]) for m in members if m.get("session_id")})


def is_provisional(n: int) -> bool:
    return FREQUENCY_FLOOR <= n <= PROVISIONAL_MAX_N


def passes_floor(n: int) -> bool:
    return n >= FREQUENCY_FLOOR


def group_by_signature(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by A-rung tool-sequence signature.

    Sessions with no tool calls carry `seq_sig=None` and are EXCLUDED — they
    are structurally invisible to A-rung matching (25.5% of the corpus), and
    bucketing them all under a single `None` key would fabricate the largest
    "cluster" in the corpus out of nothing.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        sig = rec.get("seq_sig")
        if sig:
            groups[str(sig)].append(rec)
    return dict(groups)


def signature_clusters(records: list[dict], *, floor: int = FREQUENCY_FLOOR) -> list[dict]:
    """A-rung clusters that clear the admission floor."""
    out: list[dict] = []
    for sig, members in sorted(group_by_signature(records).items()):
        n = count_distinct_sessions(members)
        if n < floor:
            continue
        out.append(
            {
                "id": f"sig-{sig}",
                "rung": "A",
                "seq_sig": sig,
                "n_sessions": n,
                "provisional": is_provisional(n),
                "members": sorted(str(m["session_id"]) for m in members),
            }
        )
    out.sort(key=lambda c: (-c["n_sessions"], c["id"]))
    return out


def dominant_signature_share(members: list[dict]) -> tuple[str | None, float, int]:
    """Coverage of a cluster's single most common tool-sequence signature.

    Returns `(signature, share, n_fragments)`. This is the B-rung degradation
    guard: if a semantic cluster's dominant signature covers only a few
    percent of its members, a pure sequence matcher would shatter it into
    ~n_fragments pieces — proving the semantic pass is the mechanism, not an
    enhancement.
    """
    sigs = [str(m.get("seq_sig")) if m.get("seq_sig") else f"__none__{i}" for i, m in enumerate(members)]
    if not sigs:
        return None, 0.0, 0
    counts: dict[str, int] = defaultdict(int)
    for sig in sigs:
        counts[sig] += 1
    top_sig, top_n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    share = top_n / len(sigs)
    if top_sig.startswith("__none__"):
        top_sig_out = None
    else:
        top_sig_out = top_sig
    return top_sig_out, share, len(counts)
