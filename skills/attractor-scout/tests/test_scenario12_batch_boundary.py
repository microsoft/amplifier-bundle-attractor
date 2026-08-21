"""SCENARIO 12 — THE BATCH BOUNDARY: the labeller does not get to invent ids.

The `fast`-tier label/cluster pass is the only place in this skill where a
model produces session ids rather than consuming them. A live run of 2,299
sessions across 64 batches measured it returning **4 ids (0.17%) that were in
no supplied batch**. Downstream gates contained all four — but only after they
had travelled through the merge, and the discipline SKILL.md pinned ("zero
invented ids") was simply not what the machine measured.

This file is the enforcement of the replacement discipline: **an id the
labeller returns is admissible only against the ids THAT batch was handed**,
and the ones that are not are dropped, counted, and said out loud.

Three layers.

Layer 1 — THE RED PROOFS. A gate that has never been shown failing is not a
gate:

* `test_red_unvalidated_collect_carries_an_invented_id_into_the_merge`
  reproduces the OLD step-2 behaviour (plain concatenation of batch responses)
  on the same fixture and shows the invented id sailing through, then shows
  the shipped path rejecting it.
* `test_red_an_id_real_elsewhere_but_not_in_this_batch_is_still_rejected` is
  the exact shape the live run hit: the id is well-formed and plausible, and
  the corpus-wide check would have admitted it. The batch boundary does not.
* `test_red_a_blank_id_resolves_to_nothing_not_to_everything` pins the prefix
  rule's own failure mode — `""` is a prefix of every id.

Layer 2 — CONTAINMENT IS COUNTED, NEVER SILENT. The counter is emitted on
every merge, including the clean case as `0`, and it survives the hand-off
into `rank` and into the run summary.

Layer 3 — NO FALSE POSITIVES. A clean batch is byte-identical to the
unvalidated collect, and legitimate short/prefix ids still resolve.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from attractor_scout import clustering, pipeline
from attractor_scout.errors import AttractorScoutError

SKILL_DIR = Path(__file__).resolve().parent.parent
CLI = [sys.executable, str(SKILL_DIR / "scripts" / "attractor_scout_cli.py")]


def _sid(prefix: str, n: int) -> str:
    """A deterministic synthetic session id. Never a real UUID."""
    return f"{prefix}-{n:04d}-4000-8000-{n:012d}"


BATCH_A_IDS = [_sid("synbatcha", i) for i in range(1, 5)]
BATCH_B_IDS = [_sid("synbatchb", i) for i in range(1, 5)]

#: Shaped exactly like a real id, supplied by NO batch. This is the artefact.
INVENTED_ID = _sid("syninvent", 9)


def _clean_batch_a() -> dict:
    return {
        "batch_id": "batch-a",
        "session_ids": list(BATCH_A_IDS),
        "clusters": [
            {"id": "a1", "name": "report handoff", "members": [BATCH_A_IDS[0], BATCH_A_IDS[1]]},
            {"id": "a2", "name": "flaky retry loop", "members": [BATCH_A_IDS[2], BATCH_A_IDS[3]]},
        ],
    }


def _clean_batch_b() -> dict:
    return {
        "batch_id": "batch-b",
        "session_ids": list(BATCH_B_IDS),
        "clusters": [{"id": "b1", "name": "doc sweep", "members": list(BATCH_B_IDS)}],
    }


def _unvalidated_collect(batches: list[dict]) -> list[dict]:
    """THE OLD STEP 2, reproduced: concatenate what every batch returned.

    This is what "collect them into an intermediate fast-clusters.json" meant
    before the batch boundary existed — no check at all between the model's
    output and the merge.
    """
    out: list[dict] = []
    for batch in batches:
        out.extend(copy.deepcopy(batch.get("clusters") or []))
    return out


# ------------------------------------------------------------------ layer 1
def test_red_unvalidated_collect_carries_an_invented_id_into_the_merge():
    """RED: the pre-fix path admits an id no batch ever supplied."""
    dirty = _clean_batch_a()
    dirty["clusters"][0]["members"].append(INVENTED_ID)

    old = _unvalidated_collect([dirty])
    old_members = [m for cluster in old for m in cluster["members"]]
    assert INVENTED_ID in old_members, (
        "RED proof is not proving anything: the unvalidated collect must carry the invented id"
    )

    merged = clustering.merge_fast_batches([dirty])
    new_members = [m for cluster in merged.clusters for m in cluster["members"]]
    assert INVENTED_ID not in new_members, "the batch boundary must not pass an unsupplied id downstream"
    assert merged.invented_ids_rejected == 1
    assert merged.rejected_by_batch == {"batch-a": [INVENTED_ID]}


def test_red_an_id_real_elsewhere_but_not_in_this_batch_is_still_rejected():
    """RED: the corpus-wide check would admit this id. The batch check will not.

    This is the live failure shape — a returned id that is a perfectly real
    session, just not one THIS batch was handed. Only the tighter boundary
    catches it, which is why the two checks are not redundant.
    """
    bleed = _clean_batch_a()
    borrowed = BATCH_B_IDS[0]
    bleed["clusters"][1]["members"].append(borrowed)

    # The corpus-wide backstop resolves it happily: it IS in the extract.
    records = [{"session_id": sid} for sid in BATCH_A_IDS + BATCH_B_IDS]
    _, unknown = clustering.units_from_clusters(records, _unvalidated_collect([bleed]))
    assert unknown == [], "the corpus-wide check cannot see a batch-bleed - that is the point"

    merged = clustering.merge_fast_batches([bleed])
    assert merged.invented_ids_rejected == 1
    assert merged.rejected_by_batch == {"batch-a": [borrowed]}


def test_red_a_blank_id_resolves_to_nothing_not_to_everything():
    """RED: `""` is a prefix of every id; prefix expansion must not launder it."""
    blank = _clean_batch_a()
    blank["clusters"] = [{"id": "a1", "name": "blank", "members": ["", "   ", BATCH_A_IDS[0]]}]

    merged = clustering.merge_fast_batches([blank])
    assert merged.clusters[0]["members"] == [BATCH_A_IDS[0]], "a blank id must not expand to the whole batch"
    assert merged.invented_ids_rejected == 2


# ------------------------------------------------------------------ layer 2
def test_invented_ids_are_dropped_counted_and_named_per_batch():
    a, b = _clean_batch_a(), _clean_batch_b()
    a["clusters"][0]["members"].append(INVENTED_ID)
    b["clusters"][0]["members"].append(_sid("syninvent", 7))

    merged = clustering.merge_fast_batches([a, b])
    assert merged.invented_ids_rejected == 2
    assert sorted(merged.rejected_by_batch) == ["batch-a", "batch-b"]
    assert merged.n_ids_placed == len(BATCH_A_IDS) + len(BATCH_B_IDS)
    assert merged.n_ids_supplied == len(BATCH_A_IDS) + len(BATCH_B_IDS)


def test_the_counter_is_emitted_even_when_it_is_zero():
    """A counter that only appears when non-zero is a counter nobody watches."""
    summary = clustering.merge_fast_batches([_clean_batch_a(), _clean_batch_b()]).as_dict()
    assert summary["invented_ids_rejected"] == 0
    assert "invented_ids_rejected" in summary
    assert summary["rejected_by_batch"] == {}
    assert summary["n_batches"] == 2


def test_a_wholly_invented_cluster_is_dropped_and_counted_not_shipped_empty():
    ghost = _clean_batch_a()
    ghost["clusters"].append({"id": "a3", "name": "ghost unit", "members": [INVENTED_ID, _sid("syninvent", 8)]})

    merged = clustering.merge_fast_batches([ghost])
    assert [c["id"] for c in merged.clusters] == ["a1", "a2"], "an all-invented cluster is not a cluster"
    assert merged.n_clusters_emptied == 1
    assert merged.invented_ids_rejected == 2


def test_a_batch_with_no_supplied_id_set_is_fail_loud():
    """Vacuous validation would reject everything - that is a broken caller."""
    with pytest.raises(AttractorScoutError) as exc:
        clustering.merge_fast_batches([{"batch_id": "batch-a", "clusters": _clean_batch_a()["clusters"]}])
    assert "batch-a" in str(exc.value)
    assert "EMPTY supplied id set" in str(exc.value)


# ------------------------------------------------------------------ layer 3
def test_a_clean_batch_rejects_nothing_and_matches_the_unvalidated_collect():
    """No false positives: validation is invisible when the labeller behaved."""
    batches = [_clean_batch_a(), _clean_batch_b()]
    merged = clustering.merge_fast_batches(batches)

    assert merged.invented_ids_rejected == 0
    assert merged.n_clusters_emptied == 0
    old = _unvalidated_collect(batches)
    assert [c["id"] for c in merged.clusters] == [c["id"] for c in old]
    for got, want in zip(merged.clusters, old, strict=True):
        assert got["members"] == want["members"], "a clean batch must pass through unchanged"
        assert got["name"] == want["name"]


def test_a_legitimate_short_id_still_resolves_by_prefix_expansion():
    short = _clean_batch_a()
    short["clusters"] = [{"id": "a1", "name": "short", "members": [BATCH_A_IDS[0][:14]]}]

    merged = clustering.merge_fast_batches([short])
    assert merged.invented_ids_rejected == 0
    assert merged.clusters[0]["members"] == [BATCH_A_IDS[0]]


def test_an_ambiguous_prefix_expands_to_every_match_rather_than_merging_silently():
    ambiguous = _clean_batch_a()
    ambiguous["clusters"] = [{"id": "a1", "name": "ambiguous", "members": ["synbatcha-"]}]

    merged = clustering.merge_fast_batches([ambiguous])
    assert merged.invented_ids_rejected == 0
    assert merged.clusters[0]["members"] == BATCH_A_IDS


# ------------------------------------------------------------ the CLI seam
def _write_batches(tmp_path: Path, batches: list[dict]) -> Path:
    batch_dir = tmp_path / "label-batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        (batch_dir / f"{batch['batch_id']}.json").write_text(json.dumps(batch), encoding="utf-8")
    return batch_dir


def _label_merge(
    tmp_path: Path, batches: list[dict], *, strict: bool = False
) -> tuple[subprocess.CompletedProcess, Path]:
    batch_dir = _write_batches(tmp_path, batches)
    out = tmp_path / "fast-clusters.json"
    argv = [*CLI, "label-merge", "--batches", str(batch_dir), "--out", str(out)]
    if strict:
        argv.append("--strict")
    return subprocess.run(argv, capture_output=True, text=True, cwd=SKILL_DIR, check=False), out


def test_cli_label_merge_surfaces_the_counter_on_stderr_and_in_the_file(tmp_path: Path):
    dirty = _clean_batch_a()
    dirty["clusters"][0]["members"].append(INVENTED_ID)
    proc, out = _label_merge(tmp_path, [dirty, _clean_batch_b()])

    assert proc.returncode == 0, proc.stderr
    assert "invented_ids_rejected=1" in proc.stderr, "the counter must be said out loud, not buried in a file"
    assert "REJECTED (not in batch batch-a)" in proc.stderr

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["labelling"]["invented_ids_rejected"] == 1
    assert payload["labelling"]["rejected_by_batch"] == {"batch-a": [INVENTED_ID]}
    assert INVENTED_ID not in json.dumps(payload["clusters"])


def test_cli_label_merge_is_quietly_zero_on_a_clean_run(tmp_path: Path):
    proc, out = _label_merge(tmp_path, [_clean_batch_a(), _clean_batch_b()])
    assert proc.returncode == 0, proc.stderr
    assert "invented_ids_rejected=0" in proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["labelling"]["invented_ids_rejected"] == 0


def test_cli_label_merge_strict_is_fatal(tmp_path: Path):
    dirty = _clean_batch_a()
    dirty["clusters"][0]["members"].append(INVENTED_ID)
    proc, _ = _label_merge(tmp_path, [dirty], strict=True)
    assert proc.returncode == 2, "--strict must exit 2, the same posture as `rank --strict`"
    assert "FAIL-LOUD" in proc.stderr


# ------------------------------------------------- it reaches the run summary
def _write_extracts(tmp_path: Path, ids: list[str]) -> Path:
    path = tmp_path / "extracts.jsonl"
    lines = [
        json.dumps(
            {
                "session_id": sid,
                "workspace": "syn-ws-001",
                "n_prompts": 2,
                "n_tool_calls": 4,
                "tool_seq": ["read_file", "edit_file"],
                "tool_all": ["read_file", "edit_file"],
                "tool_tail": ["edit_file"],
                "author": "human",
            }
        )
        for sid in ids
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_rank_carries_the_labelling_ledger_into_its_own_output(tmp_path: Path):
    dirty = _clean_batch_a()
    dirty["clusters"][0]["members"].append(INVENTED_ID)
    _, clusters_path = _label_merge(tmp_path, [dirty])
    extracts = _write_extracts(tmp_path, BATCH_A_IDS)
    ranked = tmp_path / "ranked.json"

    proc = subprocess.run(
        [
            *CLI,
            "rank",
            "--strict",
            "--extracts",
            str(extracts),
            "--clusters",
            str(clusters_path),
            "--out",
            str(ranked),
        ],
        capture_output=True,
        text=True,
        cwd=SKILL_DIR,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "invented_ids_rejected=1" in proc.stderr
    payload = json.loads(ranked.read_text(encoding="utf-8"))
    assert payload["labelling"]["invented_ids_rejected"] == 1


def test_the_run_summary_always_carries_invented_ids_rejected(tmp_path: Path):
    dirty = _clean_batch_a()
    dirty["clusters"][0]["members"].append(INVENTED_ID)
    _, clusters_path = _label_merge(tmp_path, [dirty])
    extracts = _write_extracts(tmp_path, BATCH_A_IDS)

    result = pipeline.run(extracts_path=extracts, clusters_path=clusters_path)
    summary = result.as_dict()
    assert summary["invented_ids_rejected"] == 1
    assert summary["labelling"]["rejected_by_batch"] == {"batch-a": [INVENTED_ID]}

    # And the A-rung floor, where no labelling pass ran at all, still reports
    # the counter rather than omitting it.
    floor = pipeline.run(extracts_path=extracts).as_dict()
    assert floor["invented_ids_rejected"] == 0
    assert floor["labelling"] == {}
