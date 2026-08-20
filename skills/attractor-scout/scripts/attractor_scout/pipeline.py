"""The composed end-to-end run: discover -> extract -> units -> rank -> render.

This is the ONLY place the stages are wired together, so the CLI and the
skill's steps cannot drift apart. Every stage is a call into the modules
above; nothing is re-implemented here.

Where the LLM layer fits: `clusters_path` accepts the semantic clusters the
skill's `fast` label/cluster pass produced (and the `reasoning` tier's
verdicts / `general` tier's author adjudication, if present as fields on
those clusters). With no clusters supplied, the pipeline runs the
DETERMINISTIC A-RUNG FLOOR and says so — it degrades to less coverage
honestly rather than pretending the semantic pass ran.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import clustering, discover, extract, graph, provenance, ranking, render


@dataclass
class RunResult:
    root: str
    tier: str
    tier_note: str
    n_sessions_discovered: int
    n_sessions_qualified: int
    n_records: int
    unknown_cluster_members: list[str] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    ranked: dict = field(default_factory=dict)
    artifact: str | None = None
    source: str = "signatures"

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "tier": self.tier,
            "tier_note": self.tier_note,
            "source": self.source,
            "n_sessions_discovered": self.n_sessions_discovered,
            "n_sessions_qualified": self.n_sessions_qualified,
            "n_records": self.n_records,
            "unknown_cluster_members": self.unknown_cluster_members,
            "own_data_scope": self.scope,
            "artifact": self.artifact,
            **self.ranked,
        }


def run(
    *,
    root: str | Path | None = None,
    mode: str = graph.MODE_AUTO,
    server_url: str | None = None,
    clusters_path: str | Path | None = None,
    extracts_path: str | Path | None = None,
    selector: str = "prompt-carrying",
    top_n_workspaces: int | None = None,
    render_to: str | Path | None = None,
) -> RunResult:
    """Run the full pipeline. Fail-loud on empty root and schema mismatch."""
    decision = graph.resolve_path(mode, server_url=server_url)

    if extracts_path:
        # Re-use an existing extract (the calibration corpus, or a prior run).
        records = extract.read_extracts(extracts_path)
        disc_root, n_disc, n_qual, scope = str(extracts_path), len(records), len(records), {}
    else:
        disc = discover.enumerate_sessions(root)
        refs = discover.qualify(disc, selector=selector, top_n_workspaces=top_n_workspaces)
        records = extract.extract_corpus(disc, refs)
        disc_root, n_disc, n_qual = str(disc.root), len(disc.sessions), len(refs)
        scope = disc.scope.as_dict()

    # Provenance is stamped during extraction; a record read back from an
    # existing extracts.jsonl (or produced by an older miner) is stamped here
    # so nothing can reach the ranking unclassified.
    provenance.ensure_stamped(records)

    unknown: list[str] = []
    if clusters_path:
        raw = json.loads(Path(clusters_path).read_text(encoding="utf-8"))
        clusters = raw.get("clusters", raw) if isinstance(raw, dict) else raw
        units, unknown = clustering.units_from_clusters(records, clusters)
        source = "semantic-clusters"
    else:
        units = clustering.units_from_signatures(records)
        source = "signatures"

    # THE MINING BOUNDARY. Cluster membership has already been re-verified
    # against the extract above, so an invented member id is still caught;
    # only now is membership narrowed to R4 human-presumed sessions. Agent and
    # unattributable sessions are counted in the panel, never ranked.
    gate = provenance.gate_units(units)
    ranked = ranking.rank(gate.admitted)
    ranked["provenance"] = provenance.summarize(records, gate=gate)
    result = RunResult(
        root=disc_root,
        tier=decision.tier,
        tier_note=decision.note,
        n_sessions_discovered=n_disc,
        n_sessions_qualified=n_qual,
        n_records=len(records),
        unknown_cluster_members=unknown,
        scope=scope,
        ranked=ranked,
        source=source,
    )
    if render_to is not None:
        result.artifact = str(render.write_report(ranked, render_to, tier_note=decision.note))
    return result
