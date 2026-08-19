"""Dual-path seam: `mode=auto|graph|jsonl`.

**The JSONL path (Tier C) is COMPLETE and proven.** The graph path (Tiers A/B)
is a clean seam that probes, and on failure falls back with an HONEST NOTE.

Read this before touching it: the graph vector is **designed but never run**
(open item O3 / signal-gaps Gap 2). A-vs-C parity is *reasoned, not measured* —
the entire calibration run was Tier-C JSONL-only by construction. So this
module deliberately does NOT implement a graph traversal it cannot verify.
Faking one would produce exactly the failure the design forbids: the graph
quietly becoming a precondition for seeing a rung.

The contract that IS enforced here:

* `mode="auto"` probes, then silently uses whichever path answers — the probe
  is what resolves the `[]`-vs-unreachable ambiguity.
* `mode="jsonl"` never probes and never opens a socket.
* `mode="graph"` fails loud if the graph is unavailable — the caller
  explicitly demanded the unproven path, so degrading silently would hide
  that they got Tier C anyway.
* Tier B dedups against local sessions by FULL `session_id`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .errors import GraphUnavailable

MODE_AUTO = "auto"
MODE_GRAPH = "graph"
MODE_JSONL = "jsonl"
VALID_MODES = (MODE_AUTO, MODE_GRAPH, MODE_JSONL)

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

PROBE_TIMEOUT_S = 2.0
PROBE_CACHE_S = 60.0

_probe_cache: dict[str, tuple[float, bool]] = {}


@dataclass
class PathDecision:
    """Which path was taken, at which tier, and why — always reported."""

    mode: str
    tier: str
    via_graph: bool
    note: str

    def as_dict(self) -> dict:
        return {"mode": self.mode, "tier": self.tier, "via_graph": self.via_graph, "note": self.note}


def probe_graph(server_url: str | None, *, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """Liveness probe for a personal/team CI graph endpoint.

    Returns False for a missing URL without touching the network at all. A
    real probe is a bounded HTTP/bolt round trip; this implementation is the
    honest stub for it — it reports "not reachable" rather than pretending to
    have talked to a server that was never exercised in calibration.
    """
    if not server_url:
        return False
    now = time.monotonic()
    cached = _probe_cache.get(server_url)
    if cached and (now - cached[0]) < PROBE_CACHE_S:
        return cached[1]
    reachable = False  # unexercised vector (O3/Gap 2) — never claimed live
    _probe_cache[server_url] = (now, reachable)
    return reachable


def resolve_path(mode: str = MODE_AUTO, *, server_url: str | None = None) -> PathDecision:
    """Decide which path a public function should take."""
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {VALID_MODES})")

    if mode == MODE_JSONL:
        return PathDecision(
            mode, TIER_C, False, "Tier C (local JSONL only) - the universal floor; every signal expresses here."
        )

    reachable = probe_graph(server_url)
    if mode == MODE_GRAPH:
        if not reachable:
            raise GraphUnavailable(
                f"mode='graph' was demanded but no graph answered at {server_url!r}. "
                f"The graph vector is UNEXERCISED (open item O3); use mode='auto' to fall "
                f"back to the proven Tier-C JSONL path."
            )
        return PathDecision(mode, TIER_A, True, "Tier A (personal CI graph) - graph sharpens joins and counts.")

    if reachable:
        return PathDecision(
            mode,
            TIER_A,
            True,
            "Tier A (personal CI graph) - graph sharpens joins and counts; JSONL still authoritative for rungs.",
        )
    return PathDecision(
        mode,
        TIER_C,
        False,
        "No graph answered the probe, so this ran at Tier C on local JSONL only. "
        "That is the proven floor and loses no rung - the graph is a sharpener, never a precondition.",
    )


def dedup_tier_b(graph_sessions: list[dict], local_sessions: list[dict]) -> list[dict]:
    """Tier-B overlap dedup, keyed on FULL `session_id`.

    Never an 8-char prefix: a measured 3.0% of sessions in the calibration
    corpus collide on their first 8 characters, and a prefix key silently
    merges distinct sessions into one.
    """
    seen = {str(s["session_id"]) for s in local_sessions if s.get("session_id")}
    merged = list(local_sessions)
    for sess in graph_sessions:
        sid = str(sess.get("session_id") or "")
        if sid and sid not in seen:
            seen.add(sid)
            merged.append(sess)
    return merged
