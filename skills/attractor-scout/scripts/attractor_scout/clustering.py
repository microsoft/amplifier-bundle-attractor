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

There are TWO id checks here, at two different boundaries, and they are not
redundant:

* `validate_batch_labels` — THE BATCH BOUNDARY, run as each `fast`-tier
  labelling batch comes back. An id is admissible only against the ~40 ids
  THAT batch was handed. This is the tightest set the id can be checked
  against, and it is checked at the moment the model emits it.
* `units_from_clusters` — the corpus boundary, run at rank time against the
  whole extract. Wider by construction, and later.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from . import author as author_mod
from . import frequency_signature
from .errors import AttractorScoutError


def _index(records: list[dict]) -> dict[str, dict]:
    return {str(r["session_id"]): r for r in records if r.get("session_id")}


def _expand(sid: str, candidates: Iterable[str]) -> list[str]:
    """Resolve one labeller-emitted id against a candidate id set.

    Full id first. A short id is resolved by unique-prefix expansion, and an
    AMBIGUOUS prefix expands to ALL matches — the measured 3.0% collision rate
    means a prefix key would otherwise silently merge distinct sessions. An
    empty list means the id is not in this set at all.

    An empty/whitespace id resolves to NOTHING rather than to everything: `""`
    is a prefix of every id, so treating it as a prefix would launder a blank
    into the whole corpus.
    """
    if not sid.strip():
        return []
    if sid in candidates:
        return [sid]
    return [full for full in candidates if full.startswith(sid)]


# ------------------------------------------------------- THE BATCH BOUNDARY
@dataclass
class BatchLabelMerge:
    """What the labelling pass actually produced, and what it invented.

    `invented_ids_rejected` is the fail-loud counter: it is emitted on every
    merge, including the clean case (as `0`), because a counter that only
    appears when it is non-zero is a counter nobody is watching.
    """

    clusters: list[dict] = field(default_factory=list)
    n_batches: int = 0
    n_ids_supplied: int = 0
    n_ids_placed: int = 0
    invented_ids_rejected: int = 0
    rejected_by_batch: dict[str, list[str]] = field(default_factory=dict)
    n_clusters_emptied: int = 0

    def as_dict(self) -> dict:
        return {
            "n_batches": self.n_batches,
            "n_ids_supplied": self.n_ids_supplied,
            "n_ids_placed": self.n_ids_placed,
            "invented_ids_rejected": self.invented_ids_rejected,
            "n_clusters_emptied": self.n_clusters_emptied,
            "rejected_by_batch": {k: list(v) for k, v in self.rejected_by_batch.items()},
        }


def validate_batch_labels(supplied_ids: Iterable[str], clusters: list[dict]) -> tuple[list[dict], list[str]]:
    """Hold ONE labelling batch to its OWN supplied id set.

    Returns `(kept_clusters, rejected_ids)`. Every member id the `fast` tier
    returned must resolve against `supplied_ids` — the ids that batch was
    actually handed. An id that does not is DROPPED from the cluster and
    RETURNED for counting; it is never passed downstream and never silently
    swallowed.

    This is deterministic containment at the earliest possible moment. A live
    run of 2,299 sessions across 64 batches saw the fast tier return 4 ids
    (0.17%) that were in no supplied batch — downstream gates caught them, but
    only after they had travelled through the merge. Here they do not travel.
    """
    # An ORDERED de-duplicating set: prefix expansion must land in the order
    # the batch supplied its ids, or two identical runs disagree.
    supplied: dict[str, None] = {}
    for raw_id in supplied_ids:
        sid = str(raw_id)
        if sid.strip():
            supplied.setdefault(sid, None)
    if not supplied:
        raise AttractorScoutError(
            "batch-boundary validation was asked to check a batch with an EMPTY supplied id set. "
            "Every id would be rejected, which is a broken caller, not a labelling failure. "
            "Record the ids each batch was handed alongside its response."
        )

    kept: list[dict] = []
    rejected: list[str] = []
    for cluster in clusters or []:
        members: list[str] = []
        seen: set[str] = set()
        for raw in cluster.get("members") or []:
            sid = str(raw)
            matches = _expand(sid, supplied)
            if not matches:
                rejected.append(sid)
                continue
            for full in matches:
                if full not in seen:
                    seen.add(full)
                    members.append(full)
        out = dict(cluster)
        out["members"] = members
        kept.append(out)
    return kept, rejected


def merge_fast_batches(batches: list[dict]) -> BatchLabelMerge:
    """Collect the `fast` tier's per-batch responses THROUGH the batch boundary.

    Each batch is `{"batch_id": ..., "session_ids": [...supplied...],
    "clusters": [...returned...]}` (`supplied_ids`/`ids` and `assignments` are
    accepted as aliases). Clusters left with no surviving member are dropped
    and counted in `n_clusters_emptied` — an all-invented cluster is not a
    cluster, and shipping it as an empty one would put a fabricated name into
    the merge.
    """
    merged = BatchLabelMerge()
    for index, batch in enumerate(batches or [], start=1):
        if not isinstance(batch, dict):
            raise AttractorScoutError(f"labelling batch #{index} is {type(batch).__name__}, expected an object")
        batch_id = str(batch.get("batch_id") or batch.get("id") or f"batch-{index}")
        supplied = batch.get("session_ids")
        if supplied is None:
            supplied = batch.get("supplied_ids")
        if supplied is None:
            supplied = batch.get("ids")
        raw_clusters = batch.get("clusters")
        if raw_clusters is None:
            raw_clusters = batch.get("assignments")
        try:
            kept, rejected = validate_batch_labels(supplied or [], raw_clusters or [])
        except AttractorScoutError as exc:
            raise AttractorScoutError(f"labelling batch {batch_id!r}: {exc}") from exc

        merged.n_batches += 1
        merged.n_ids_supplied += len({str(s) for s in (supplied or []) if str(s).strip()})
        for cluster in kept:
            if not cluster["members"]:
                merged.n_clusters_emptied += 1
                continue
            cluster.setdefault("batch_id", batch_id)
            merged.n_ids_placed += len(cluster["members"])
            merged.clusters.append(cluster)
        if rejected:
            merged.rejected_by_batch[batch_id] = rejected
            merged.invented_ids_rejected += len(rejected)
    return merged


def units_from_clusters(records: list[dict], clusters: list[dict]) -> tuple[list[dict], list[str]]:
    """Attach extract records to externally-supplied semantic clusters.

    Returns `(units, unknown_member_ids)`. Member ids that do not resolve are
    REPORTED, never silently dropped — an invented id is the exact failure
    mode the deterministic re-verification exists to catch, and a live run of
    64 batches proved the fast tier does invent them (4 ids, 0.17%). This is
    the corpus-wide backstop; `validate_batch_labels` is the tight one.

    Member ids are matched by `_expand`: FULL session id first, then
    unique-prefix expansion, with an AMBIGUOUS prefix expanding to ALL
    matches — the measured 3.0% collision rate means a prefix key would
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
            matches = _expand(sid, by_id)
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
        #
        # ADJUDICATION INVARIANT (mirrored verbatim in SKILL.md step 4): the
        # general tier may move a cluster AWAY from human, and may NEVER move a
        # cluster toward human. A supplied `author_adjudicated` label is
        # trusted here, but the prompt that produces it is bound by that rule,
        # so a session the provenance ladder called agent (R0-R3) cannot be
        # laundered into human work by a re-reading of its prompt text.
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
