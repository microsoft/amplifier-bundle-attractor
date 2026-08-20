"""SCENARIO 11 — session PROVENANCE: whose work reached the ranking.

The artifact this skill produces is only worth reading if it separates what
the user did from what an agent did on their behalf. Before the provenance
layer it could not: qualification's sole test was "a `prompt:submit` exists",
and a harness-fired root emits a byte-identical event.

Four things are machine-checked here.

**The ladder lands where it was planted.** Every calibration class in
`build_provenance_corpus` is pinned to its rung. The classes share one tool
sequence and one cost profile, so any difference in what reaches the ranking
is attributable to provenance and to nothing else.

**The RED proofs.** A gate that has never been shown failing is not a gate:

* `test_red_harness_root_previously_ranked_is_now_excluded` reproduces the OLD
  behaviour on the same corpus (a tmp-workspace harness one-shot ranked as the
  user's own work) and then shows the shipped path excluding it.
* `test_red_ranking_fail_open_absent_author_never_ranks_as_human` pins the
  `or HUMAN` fail-open that used to end the admission chain.
* `test_red_renderer_stays_byte_identical_without_provenance` pins additivity.

**Nothing is dropped.** Excluded sessions are counted and sampled, because
"we excluded 40% of your history" is a finding, not a silence.

**Nothing leaks.** The panel carries counts, classes and shapes — never a
session id, a path, or a line of prompt text.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from attractor_scout import author as author_mod
from attractor_scout import clustering, discover, extract, pipeline, provenance, ranking, render

from fixtures.synthetic_corpus import build_provenance_corpus

PINNED = "2020-01-01T00:00:00+00:00"

SKILL_DIR = Path(__file__).resolve().parent.parent
CLI = SKILL_DIR / "scripts" / "attractor_scout_cli.py"

#: Substrings that exist ONLY because a provenance panel was rendered.
PROVENANCE_MARKERS = (
    "Session provenance",
    'class="prov"',
    ".prov{",
    "Already automated",
    "What is still unknown",
    "human-presumed",
)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """The provenance corpus, mined through the real discovery/extract spine."""
    root: Path = tmp_path_factory.mktemp("provenance") / "projects"
    truth = build_provenance_corpus(root)
    disc = discover.enumerate_sessions(root)
    refs = discover.qualify(disc)
    records = extract.extract_corpus(disc, refs)
    return truth, disc, refs, records


def _by_id(records: list[dict]) -> dict[str, dict]:
    return {str(r["session_id"]): r for r in records}


# --------------------------------------------------------------- the ladder
@pytest.mark.parametrize(
    "class_name",
    [
        "human_multi_turn",
        "pipeline_root",
        "harness_oneshot_tmp",
        "goal_lane_worktree",
        "templated_brief_root",
        "stable_single_prompt",
    ],
)
def test_each_planted_class_lands_on_its_rung(planted, class_name):
    """The pinned verdict table. A rung that moves must move this test first."""
    truth, _disc, _refs, records = planted
    spec = truth.expected["classes"][class_name]
    by_id = _by_id(records)
    seen = [by_id[sid] for sid in spec["ids"] if sid in by_id]
    assert seen, f"{class_name} did not survive qualification at all"
    for rec in seen:
        prov = rec["provenance"]
        assert prov["rung"] == spec["rung"], f"{class_name}: expected {spec['rung']}, got {prov['rung']}"
        assert prov["verdict"] == spec["verdict"]


def test_delegate_sub_sessions_classify_r0_even_though_they_are_not_roots(planted):
    """R0 fires on lineage alone — parent id, delegate id shape, fork opener.

    Delegate sessions are not roots, so they never reach qualification. That
    is not a reason for the ladder to have no answer about them: the fold and
    census paths both see them.
    """
    truth, disc, _refs, _records = planted
    spec = truth.expected["classes"]["delegate_sub_session"]
    refs = [s for s in disc.sessions if s.session_id in set(spec["ids"])]
    assert len(refs) == len(spec["ids"]), "the delegate sessions must still be DISCOVERED"
    for ref in refs:
        assert ref.is_root is False
        rec = extract.extract_session(ref)
        assert rec["provenance"]["rung"] == provenance.R0
        assert rec["provenance"]["verdict"] == provenance.AGENT
        assert rec["provenance"]["signal"] == "parent_id"


def test_every_verdict_names_the_rung_that_fired_and_carries_its_evidence(planted):
    _truth, _disc, _refs, records = planted
    for rec in records:
        prov = rec["provenance"]
        assert prov["rung"] in provenance.RUNGS
        assert prov["signal"], "a verdict with no named signal is not auditable"
        for key in ("prompt_count", "span_s", "workspace_class", "first_prompt_shape"):
            assert key in prov["evidence"], f"evidence is missing {key}"


def test_r5_is_the_floor_and_never_resolves_to_human():
    """No positive human marker exists, so absence of evidence is never human."""
    bare = provenance.classify(provenance.SessionSignals(session_id="syn-bare"))
    assert bare.rung == provenance.R5
    assert bare.verdict == provenance.UNKNOWN

    # Two prompts, but back to back: no thinking gap, so R4 must DECLINE
    # rather than round up to human.
    no_gap = provenance.classify(provenance.SessionSignals(session_id="syn-nogap", n_prompts=2, prompt_gaps_s=(1.0,)))
    assert no_gap.verdict == provenance.UNKNOWN


def test_r4_gap_threshold_is_pinned_scripted_band_stays_unknown(corpus_root: Path):
    """RED-PROOF for `HUMAN_GAP_MIN_S`: a scripted-cadence multi-turn is NOT human.

    Two workspaces, byte-identical work, differing ONLY in prompt cadence:

    * scripted band — three prompts with gaps of 8 s (all <= 10 s). A machine
      driving prompts back to back looks like this, and it must land on R5.
    * human band — three prompts with gaps of 60 s and 120 s. A person
      thinking between turns looks like this, and it must land on R4.

    Weaken the constant (45.0 -> anything <= 8) and the scripted band starts
    reading as human — this test goes RED. Verified red at 5.0 during
    development, then restored.
    """
    from fixtures.synthetic_corpus import synth_id, write_session

    for i in range(3):
        write_session(
            corpus_root,
            "syn-prov-scripted",
            synth_id("synscripted", i),
            prompts=["kick off", "next step", "final step"],
            prompt_offsets_s=[0.0, 8.0, 16.0],  # gaps: 8 s, 8 s
            tools=["read_file", "edit_file", "edit_file", "edit_file", "bash", "python_check"],
            span_s=600,
            started_offset_s=i * 7200,
            working_dir="/synthetic-workspace/proj-alpha",
        )
    for i in range(3):
        write_session(
            corpus_root,
            "syn-prov-paced-human",
            synth_id("synpaced", i),
            prompts=["kick off", "next step", "final step"],
            prompt_offsets_s=[0.0, 60.0, 180.0],  # gaps: 60 s, 120 s
            tools=["read_file", "edit_file", "edit_file", "edit_file", "bash", "python_check"],
            span_s=600,
            started_offset_s=i * 7200,
            working_dir="/synthetic-workspace/proj-alpha",
        )

    disc = discover.enumerate_sessions(corpus_root)
    records = extract.extract_corpus(disc, discover.qualify(disc))
    by_ws: dict[str, set[str]] = {}
    for rec in records:
        by_ws.setdefault(rec["workspace"], set()).add(rec["provenance"]["rung"])

    assert by_ws["syn-prov-scripted"] == {provenance.R5}, "an <=10 s-cadence multi-turn must NOT be human-presumed"
    assert by_ws["syn-prov-paced-human"] == {provenance.R4}, "a genuinely paced multi-turn must reach R4"


def test_r4_min_prompts_is_pinned_a_single_prompt_is_never_human():
    """RED-PROOF for `HUMAN_MIN_PROMPTS`: one prompt is never human, gap or not.

    A single-prompt session cannot produce an inter-prompt gap from real data,
    so this pins the guard directly at the classifier: even handed a gap value
    that clears `HUMAN_GAP_MIN_S`, a one-prompt session must decline to R5.
    Lower the constant (2 -> 1) and this reads R4 — RED.
    """
    one_prompt_with_gap = provenance.classify(
        provenance.SessionSignals(
            session_id="syn-oneprompt",
            n_prompts=1,
            prompt_gaps_s=(provenance.HUMAN_GAP_MIN_S + 100.0,),
        )
    )
    assert one_prompt_with_gap.rung == provenance.R5
    assert one_prompt_with_gap.verdict == provenance.UNKNOWN

    # And the boundary itself: exactly HUMAN_MIN_PROMPTS prompts WITH a real
    # gap is the smallest thing that earns R4, so the constant is the hinge.
    at_boundary = provenance.classify(
        provenance.SessionSignals(
            session_id="syn-boundary",
            n_prompts=provenance.HUMAN_MIN_PROMPTS,
            prompt_gaps_s=(provenance.HUMAN_GAP_MIN_S,),
        )
    )
    assert at_boundary.rung == provenance.R4


def test_false_friends_are_not_treated_as_signals():
    """Three measured-and-rejected signals must stay rejected.

    A `deprecation:warning` first event (a bundle/time artifact), a
    UUID-shaped id (thousands of which carry parents), and a session with a
    constant host field are each NOT evidence of anything, and a session
    carrying all three still resolves UNKNOWN.
    """
    verdict = provenance.classify(
        provenance.SessionSignals(
            session_id="synaaaa-bbbb-cccc-dddd-eeeeffff0000",
            first_event="deprecation:warning",
            n_prompts=1,
            working_dir="/synthetic-workspace/proj-alpha",
        )
    )
    assert verdict.rung == provenance.R5
    assert verdict.verdict == provenance.UNKNOWN


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/tmp/synthetic-run.k3f9zq", provenance.WS_EPHEMERAL),
        ("/var/tmp/synthetic-run", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/proj/.scratch/run", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/proj/worktrees/lane-2", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/proj/lanes/lane-2", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/batch-20260814T091500Z", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/proj.a1b2c3", provenance.WS_EPHEMERAL),
        ("/synthetic-workspace/proj-alpha", provenance.WS_STABLE),
        ("", provenance.WS_UNRECORDED),
        (None, provenance.WS_UNRECORDED),
    ],
)
def test_structural_path_pattern_set_is_documented_and_conservative(path, expected):
    ws_class, _pattern = provenance.classify_workspace(path)
    assert ws_class == expected


# ------------------------------------------------------------ plumbing fixes
def test_working_dir_is_plumbed_through_the_session_ref(planted):
    """Leak #2: SessionRef used to DROP the most discriminating field it had."""
    truth, disc, _refs, _records = planted
    harness_ids = set(truth.expected["classes"]["harness_oneshot_tmp"]["ids"])
    harness_refs = [s for s in disc.sessions if s.session_id in harness_ids]
    assert harness_refs
    for ref in harness_refs:
        assert ref.working_dir, "working_dir must survive discovery"
        assert provenance.classify_workspace(ref.working_dir)[0] == provenance.WS_EPHEMERAL


def test_qualification_stamps_the_provenance_markers_it_reads(planted):
    """Leak #1: the qualifier's single read now returns what discriminates."""
    truth, _disc, refs, _records = planted
    pipeline_ids = set(truth.expected["classes"]["pipeline_root"]["ids"])
    stamped = [r for r in refs if r.session_id in pipeline_ids]
    assert stamped
    for ref in stamped:
        assert ref.prescan is not None
        assert ref.prescan.has_prompt is True
        assert "pipeline:start" in ref.prescan.orchestration_events


def test_extract_drops_the_dead_machine_launched_flag(planted):
    """Leak #3: `machine_launched` was measured dead; the ladder subsumes it."""
    _truth, _disc, _refs, records = planted
    for rec in records:
        assert "machine_launched" not in rec, "the dead flag must not come back"
        assert "provenance" in rec


def test_absent_provenance_reads_unknown_never_human():
    """FAIL-HONEST at the record level: an unstamped record is not a person."""
    assert provenance.verdict_of({}) == provenance.UNKNOWN
    assert provenance.is_opportunity_eligible({}) is False
    assert provenance.rung_of({}) == provenance.R5


def test_ensure_stamped_classifies_records_read_back_from_disk(planted):
    _truth, _disc, _refs, records = planted
    stripped = [{k: v for k, v in rec.items() if k != "provenance"} for rec in records]
    provenance.ensure_stamped(stripped)
    original = {r["session_id"]: r["provenance"]["rung"] for r in records}
    assert {r["session_id"]: r["provenance"]["rung"] for r in stripped} == original


# ------------------------------------------------------------- the RED proofs
def test_red_harness_root_previously_ranked_is_now_excluded(planted):
    """RED PROOF 1 — the leak, reproduced, then closed.

    The harness one-shot class is a prompt-carrying root running in a tmp
    workspace. Stripping its provenance stamp reproduces the pre-provenance
    world exactly: the author prior reads it as human and it lands in the
    ranked opportunities. With the stamp, the mining boundary keeps it out.
    """
    truth, disc, _refs, records = planted
    harness_ids = set(truth.expected["classes"]["harness_oneshot_tmp"]["ids"])

    # (a) The OLD sole qualifier still admits it — that is the whole problem.
    harness_refs = [s for s in disc.sessions if s.session_id in harness_ids]
    assert harness_refs
    for ref in harness_refs:
        assert discover.carries_prompt(ref.events_path) is True

    # (b) The pre-provenance world: no stamp, prior reads human, unit ranks.
    before = copy.deepcopy(records)
    for rec in before:
        rec.pop("provenance", None)
    author_mod.classify_authors(before)
    before_by_id = _by_id(before)
    assert all(before_by_id[sid]["author"] == author_mod.HUMAN for sid in harness_ids)
    old_units = clustering.units_from_signatures(before)
    old_result = ranking.rank(old_units)
    old_ranked_members = {
        sid for u in old_result["opportunities"] + old_result["honest_no"] for sid in u.get("members", [])
    }
    assert harness_ids & old_ranked_members, "fixture must reproduce the leak it is proving closed"

    # (c) The shipped path: gate, then rank. The harness unit cannot reach it.
    units = clustering.units_from_signatures(records)
    gate = provenance.gate_units(units)
    new_result = ranking.rank(gate.admitted)
    new_ranked_members = {
        sid for u in new_result["opportunities"] + new_result["honest_no"] for sid in u.get("members", [])
    }
    assert not (harness_ids & new_ranked_members), "an R2 agent session reached the ranking"

    # (d) Excluded, but COUNTED — the exclusion is a finding, not a silence.
    panel = provenance.summarize(records, gate=gate)
    assert panel["by_rung"][provenance.R2] >= len(harness_ids)
    assert panel["already_automated"] >= len(harness_ids)


def test_red_ranking_fail_open_absent_author_never_ranks_as_human(planted):
    """RED PROOF 2 — the `or HUMAN` fail-open at the end of the author chain.

    A unit carrying no author verdict at all used to default to HUMAN and be
    ranked as the user's own work. It must now be held out, and it must NOT
    be relabelled harness either — the honest answer is "unattributed".
    """
    truth, _disc, _refs, records = planted
    human_ids = truth.expected["classes"]["human_multi_turn"]["ids"]
    by_id = _by_id(records)
    members = [by_id[sid] for sid in human_ids]

    unit = {"unit_id": "no-author", "name": "no author verdict", "members": members}
    result = ranking.rank([unit])

    ranked_ids = {u["unit_id"] for u in result["opportunities"]} | {u["unit_id"] for u in result["honest_no"]}
    assert "no-author" not in ranked_ids, "an unattributed unit was ranked as human work"
    assert {u["unit_id"] for u in result["waste_findings"]} == set(), "nor may it be called harness ceremony"
    assert [u["unit_id"] for u in result["unattributed"]] == ["no-author"]
    assert result["unattributed"][0]["author"] == provenance.UNKNOWN
    assert result["summary"]["n_unattributed"] == 1

    # The admission gate itself, directly: three channels, no default.
    admitted, waste, unattributed = ranking.apply_admission_gate([{"unit_id": "x", "members": []}])
    assert (admitted, waste) == ([], [])
    assert unattributed[0]["author"] == provenance.UNKNOWN


def test_red_renderer_stays_byte_identical_without_provenance(planted):
    """RED PROOF 3 — additivity, the same discipline as the demos layer.

    A ranked result with no provenance data renders the byte-identical
    artifact it rendered before this panel existed; every new byte, CSS
    included, is behind the guard.
    """
    truth, _disc, _refs, records = planted
    human_ids = truth.expected["classes"]["human_multi_turn"]["ids"]
    by_id = _by_id(records)
    unit = {
        "unit_id": "u1",
        "name": "planted human unit",
        "members": [by_id[sid] for sid in human_ids],
        "author_adjudicated": author_mod.HUMAN,
    }
    baseline_result = ranking.rank([unit])
    baseline_result.pop("unattributed", None)
    baseline = render.render_html(baseline_result, generated_at=PINNED)

    for marker in PROVENANCE_MARKERS:
        assert marker not in baseline, f"a provenance marker leaked into the no-provenance artifact: {marker!r}"
    assert render.render_html(dict(baseline_result), generated_at=PINNED) == baseline

    # An explicitly-empty panel is still no panel: falsy means zero bytes.
    empty = dict(baseline_result)
    empty["provenance"] = {}
    empty["unattributed"] = []
    assert render.render_html(empty, generated_at=PINNED) == baseline

    # ...and with a panel, the artifact grows and says the honest part out loud.
    with_panel = dict(baseline_result)
    with_panel["provenance"] = provenance.summarize(records)
    rendered = render.render_html(with_panel, generated_at=PINNED)
    assert rendered != baseline
    for heading in (
        "A range of what you already do",
        "Ranked opportunities",
        "Honest NOs",
        "Waste findings",
    ):
        assert heading in rendered, f"the frozen half lost a section: {heading!r}"
    assert rendered.index("Waste findings") < rendered.index("Session provenance"), (
        "the panel is APPENDED, never interleaved"
    )
    assert provenance.UNKNOWN_NOTE in rendered
    assert provenance.UPSTREAM_FIX_NOTE in rendered


# ------------------------------------------------------------------- policy
def test_opportunities_are_mined_from_r4_only(planted):
    """The settled policy, end to end through the composed pipeline."""
    truth, _disc, _refs, _records = planted
    result = pipeline.run(root=truth.root, mode="jsonl")
    human_ids = set(truth.expected["classes"]["human_multi_turn"]["ids"])

    ranked_members = {
        sid for u in result.ranked["opportunities"] + result.ranked["honest_no"] for sid in u.get("members", [])
    }
    assert ranked_members, "the human class must still produce a ranked unit"
    assert ranked_members <= human_ids, f"non-R4 sessions reached the ranking: {sorted(ranked_members - human_ids)}"


def test_agent_and_unknown_sessions_are_counted_never_silently_dropped(planted):
    truth, _disc, _refs, records = planted
    result = pipeline.run(root=truth.root, mode="jsonl")
    panel = result.ranked["provenance"]

    assert panel["n_sessions"] == len(records)
    assert sum(panel["by_rung"].values()) == panel["n_sessions"]
    assert panel["already_automated"] > 0, "the already-automated footprint must be surfaced"
    assert panel["unknown_excluded"] > 0, "the honest UNKNOWN residual must be surfaced"
    assert panel["opportunity_pool"] + panel["already_automated"] + panel["unknown_excluded"] == panel["n_sessions"], (
        "every mined session must land in exactly one pool"
    )
    assert panel["samples"], "counts without samples are not auditable"


def test_the_panel_carries_no_ids_paths_or_prompt_text(planted):
    """§7: the SHIPPED artifact ships counts, classes and shapes — nothing identifying.

    Non-vacuous by construction: the gate runs over the REAL fixture units (a
    populated already-automated + unattributed set), the panel is summarized
    with that real gate, and the assertion is made against the RENDERED HTML —
    the thing that actually reaches disk — not just the summary dict.
    """
    truth, _disc, _refs, records = planted

    units = clustering.units_from_signatures(records)
    gate = provenance.gate_units(units)
    assert gate.already_automated and gate.unattributed, "the leak scan must run over a POPULATED gate, not []"
    panel = provenance.summarize(records, gate=gate)
    result = ranking.rank(gate.admitted)
    result["provenance"] = panel
    html = render.render_html(result, generated_at=PINNED)

    prompts = {"fix the failing check", "Run the scheduled check", "Lane mission", "kick off"}
    for surface, label in ((json.dumps(panel), "panel dict"), (html, "rendered HTML")):
        for spec in truth.expected["classes"].values():
            for sid in spec["ids"]:
                assert sid not in surface, f"{label} leaked a session id"
        assert truth.expected["stable_workdir"] not in surface, f"{label} leaked a workspace path"
        assert "/tmp/" not in surface, f"{label} leaked a tmp path"
        for prompt in prompts:
            assert prompt not in surface, f"{label} leaked prompt text: {prompt!r}"


def test_unit_names_are_the_only_thing_that_reaches_the_html_and_they_are_labels(planted):
    """DISCLOSURE (mirrored in design doc §5): a unit NAME reaches the HTML via
    the unattributed table. That is intentional and safe — a name is an
    LLM-authored cluster LABEL, never a path, id, or prompt body. This asserts
    the channel exists and that what flows through it is a label, so a future
    change that starts piping a path or id into a name breaks here.
    """
    truth, _disc, _refs, records = planted
    human_members = [r for r in records if r["session_id"] in set(truth.expected["classes"]["human_multi_turn"]["ids"])]
    # A unit with NO author verdict → ranking routes it to `unattributed`,
    # whose NAME the renderer prints. Give it a label-shaped name on purpose.
    label = "cross-workspace report handoff"
    result = ranking.rank([{"unit_id": "u-unattr", "name": label, "members": human_members}])
    assert [u["name"] for u in result["unattributed"]] == [label]
    html = render.render_html(result, generated_at=PINNED)
    assert label in html, "the unattributed unit name is expected to reach the HTML"
    # ...and the name is the ONLY member-derived string that does: no id, path.
    for member in human_members:
        assert member["session_id"] not in html


def test_gate_units_never_mutates_its_input(planted):
    _truth, _disc, _refs, records = planted
    units = clustering.units_from_signatures(records)
    before = [len(u["members"]) for u in units]
    provenance.gate_units(units)
    assert [len(u["members"]) for u in units] == before


def test_units_with_no_r4_member_are_reported_not_deleted(planted):
    _truth, _disc, _refs, records = planted
    units = clustering.units_from_signatures(records)
    gate = provenance.gate_units(units)
    reported = gate.already_automated + gate.unattributed
    assert reported, "gated-out units must still be reported somewhere"
    for entry in reported:
        assert entry["n_sessions"] >= 1
        assert entry["provenance_mix"], "an exclusion without its rung mix is not auditable"
        assert entry["note"]


# --------------------------------------------------------- the CLI boundary
def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_red_cli_rank_enforces_the_mining_boundary(tmp_path: Path):
    """RED PROOF 4 — the CLI `rank` path is gated too, not just `pipeline.run`.

    `pipeline.run` and `cmd_rank` are two independent wirings of the mining
    boundary, and only the first was guarded: mutating `cmd_rank`'s
    `ranking.rank(gate.admitted)` back to `rank(units)` left the whole suite
    green. This drives the real CLI (`extract` then `rank`) end to end over the
    provenance corpus and pins the boundary there.

    The load-bearing class is `stable_single_prompt` (R5, UNKNOWN): its author
    PRIOR reads human (one unique prompt, no harness markers), so the author
    admission gate would happily admit it. ONLY the provenance mining boundary
    keeps it out — which is exactly why an ungated `cmd_rank` ranks it, and
    this test goes red. Verified red against the mutated form during
    development, then restored.
    """
    truth = build_provenance_corpus(tmp_path / "projects")
    classes = truth.expected["classes"]
    id2class = {sid: name for name, spec in classes.items() for sid in spec["ids"]}
    human_ids = set(classes["human_multi_turn"]["ids"])

    extracts = tmp_path / "ex.jsonl"
    ranked = tmp_path / "ranked.json"
    ex = _cli("--root", str(tmp_path / "projects"), "extract", "--out", str(extracts))
    assert ex.returncode == 0, ex.stderr
    rk = _cli("rank", "--extracts", str(extracts), "--out", str(ranked))
    assert rk.returncode == 0, rk.stderr

    result = json.loads(ranked.read_text(encoding="utf-8"))
    ranked_members = {sid for u in result["opportunities"] + result["honest_no"] for sid in u.get("members", [])}
    assert ranked_members, "the human class must still produce a ranked unit through the CLI"
    leaked = {id2class.get(sid, sid) for sid in ranked_members - human_ids}
    assert not leaked, f"non-R4 sessions reached the CLI ranking: {sorted(leaked)}"

    # The UNKNOWN class was not merely absent from the ranking — it was
    # positively counted in the panel the same run emits, so the exclusion is
    # a visible finding and not a silence.
    panel = result["provenance"]
    assert panel["by_rung"][provenance.R5] >= len(classes["stable_single_prompt"]["ids"])
    assert panel["unknown_excluded"] >= len(classes["stable_single_prompt"]["ids"])


def test_the_unknown_class_authors_human_so_only_the_boundary_excludes_it(planted):
    """The premise of RED PROOF 4, asserted directly: the author gate alone is
    NOT enough. If the prior ever stops calling this class human, the CLI test
    above would pass for the wrong reason — so pin the premise separately."""
    truth, _disc, _refs, records = planted
    by_id = _by_id(records)
    for sid in truth.expected["classes"]["stable_single_prompt"]["ids"]:
        rec = by_id[sid]
        assert rec["provenance"]["rung"] == provenance.R5
        assert rec["author"] == author_mod.HUMAN, (
            "the UNKNOWN class must author HUMAN, or the CLI boundary test proves nothing"
        )
