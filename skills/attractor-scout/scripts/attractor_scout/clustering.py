"""Unit assembly — turn extract records into rankable UNITS.

Two sources, and the design is explicit about which one is the mechanism:

* **B-rung (semantic clusters)** — supplied from OUTSIDE this library by the
  skill's `fast` label/cluster pass, as JSON. 48 of 54 global clusters in the
  calibration corpus are findable ONLY this way, and 17 of 19 human
  opportunities are B-rung EXCLUSIVE. **The semantic pass is not an
  enhancement, it is the mechanism.**
* **A-rung (tool-sequence signatures)** — computed here, deterministically.
  A cheap dedup floor that works with no model at all. It is what the library
  can produce alone, and the skill degrades to it honestly rather than
  claiming semantic coverage it did not compute.

This module never calls a model. It only ATTACHES member records to
cluster definitions and re-verifies membership deterministically — which is
the whole point of the seam: everything the LLM pass produces gets checked
against `extracts.jsonl` before it can influence a ranking.
"""

from __future__ import annotations

from . import author as author_mod
from . import frequency_signature


def _index(records: list[dict]) -> dict[str, dict]:
    return {str(r["session_id"]): r for r in records if r.get("session_id")}


def units_from_clusters(records: list[dict], clusters: list[dict]) -> tuple[list[dict], list[str]]:
    """Attach extract records to externally-supplied semantic clusters.

    Returns `(units, unknown_member_ids)`. Member ids that do not resolve are
    REPORTED, never silently dropped — an invented id is the exact failure
    mode the deterministic re-verification exists to catch (the calibration
    run measured 0 invented ids across 57 batches; this is how we keep
    knowing that).

    Member ids are matched on the FULL session id first. A short id is
    resolved by unique-prefix expansion, and an AMBIGUOUS prefix expands to
    ALL matches — the measured 3.0% collision rate means a prefix key would
    otherwise silently merge distinct sessions.
    """
    by_id = _index(records)
    units: list[dict] = []
    unknown: list[str] = []

    for cluster in clusters:
        members: list[dict] = []
        seen: set[str] = set()
        for raw in cluster.get("members") or []:
            sid = str(raw)
            if sid in by_id:
                if sid not in seen:
                    seen.add(sid)
                    members.append(by_id[sid])
                continue
            matches = [full for full in by_id if full.startswith(sid)]
            if not matches:
                unknown.append(sid)
                continue
            for full in matches:
                if full not in seen:
                    seen.add(full)
                    members.append(by_id[full])

        prior = author_mod.cluster_author_prior(members)
        unit = {
            "unit_id": str(cluster.get("id") or cluster.get("unit_id") or f"unit-{len(units) + 1}"),
            "name": cluster.get("name") or cluster.get("id"),
            "gist": cluster.get("gist"),
            "rung": cluster.get("rung", "B"),
            "members": members,
            "author_prior": prior["author_prior"],
            "author_mix": prior["author_mix"],
        }
        # An adjudicated author (general-tier cluster read) overrides the
        # prior when supplied. Fit verdicts from the reasoning tier likewise
        # override the deterministic detectors — that is the whole reason
        # those tiers exist.
        for key_in, key_out in (
            ("author", "author_adjudicated"),
            ("author_adjudicated", "author_adjudicated"),
            ("cycle", "cycle"),
            ("evidence_gate", "gate"),
            ("gate", "gate"),
        ):
            if key_in in cluster and cluster[key_in] is not None:
                unit[key_out] = cluster[key_in]
        units.append(unit)

    return units, unknown


def units_from_signatures(records: list[dict], *, floor: int = frequency_signature.FREQUENCY_FLOOR) -> list[dict]:
    """Deterministic A-rung units — the no-model floor."""
    by_id = _index(records)
    units: list[dict] = []
    for cluster in frequency_signature.signature_clusters(records, floor=floor):
        members = [by_id[sid] for sid in cluster["members"] if sid in by_id]
        prior = author_mod.cluster_author_prior(members)
        tools = members[0].get("tool_seq") if members else []
        units.append(
            {
                "unit_id": cluster["id"],
                "name": f"Repeated tool sequence: {' -> '.join(str(t) for t in (tools or [])[:4]) or 'unknown'}",
                "gist": "A-rung dedup floor: sessions sharing an identical opening tool-call signature.",
                "rung": "A",
                "members": members,
                "author_prior": prior["author_prior"],
                "author_mix": prior["author_mix"],
            }
        )
    return units
