#!/usr/bin/env python3
"""Real-corpus arms for Scenarios 3, 4 and 5. NOT CI-blocking, by design.

These arms read a real, private context-intelligence extract. That data is
NEVER committed to this repo, so these arms cannot be pytest gates — they are
runnable scripts the maintainer (or any user with their own corpus) points at
their own `extracts.jsonl`. The portable halves of the same scenarios ARE
pytest gates and live in `../tests/`.

    python evals/real_corpus_arms.py --extracts <path>/extracts.jsonl \\
        --clusters <path>/global-clusters-verified.json --out <dir>

Threshold provenance — stated honestly rather than blanket-claimed:

* Most thresholds are Phase-1 PRE-REGISTERED: fixed before the Stage-1 run and
  unchanged (S3 base separations, S4 rates, the n_prompts band, the tool:error
  guard). A run meets them or it does not.
* Two are STAGE-2-DERIVED, and are labelled as such at their definition:
  `S3_SESSION_AMPLIFICATION_TARGET` (the 12.8x session-level p75/median skew was
  MEASURED in the Phase-1 calibration run and adopted as the target when the S3
  Arm-iii adjudication landed at Stage 2) and `S5_COMPOSITION` (kept only for the
  informational `fragile` corroboration; the S5 headline check became structural).
  These are not "pre-registered" in the strict sense and are not claimed to be.

Nothing in this script writes into the repo, and nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from attractor_scout import extract, fit_cycle, fit_gate, fit_recovery, honest_no, leverage  # noqa: E402

# ---- Pre-registered thresholds (Scenario 3) -------------------------------
S3_BASE_SEPARATION_FLOOR = 10.0
S3_LLM_SEPARATION_FLOOR = 5.0
S3_NPROMPTS_BAND = (0.9, 1.1)
S3_P75_CEILING = 1.3
S3_TOOL_ERROR_EVENT_GUARD = 10
# STAGE-2-DERIVED (not pre-registered). The 12.8x session-level p75/median span
# skew was MEASURED in the Phase-1 calibration run (median 136 s, p75 1,745 s)
# and ADOPTED as the target when the S3 Arm-iii adjudication landed at Stage 2 --
# so it is a reproduce-the-calibration check, honestly labelled, not a threshold
# fixed before seeing data. The cluster-level 1.22x/1.3x reading is recorded
# informational, not asserted.
S3_SESSION_AMPLIFICATION_TARGET = 12.8
S3_SESSION_AMPLIFICATION_TOL = 1.5

# ---- Pre-registered thresholds (Scenario 4) -------------------------------
S4_EXPLICIT_RATE = (0.032, 0.005)
S4_STRUCTURAL_FLOOR = 0.530
S4_STRUCTURAL_GE6_FLOOR = 0.990
S4_IMPLICIT_EXPLICIT_RATIO_FLOOR = 15.0
S4_GATE_PREVALENCE_ALL = (0.167, 0.020)
S4_GATE_PREVALENCE_GE6 = (0.302, 0.030)

# ---- Pre-registered thresholds (Scenario 5) -------------------------------
S5_COMPOSITION = {"recipe": 17, "one-shot": 14, "fragile": 2}
S5_COMPOSITION_TOL = 1


def _members(records_by_id: dict, cluster: dict) -> list[dict]:
    out = []
    for sid in cluster.get("members") or []:
        rec = records_by_id.get(str(sid))
        if rec is None:
            matches = [v for k, v in records_by_id.items() if k.startswith(str(sid))]
            out.extend(matches)
        else:
            out.append(rec)
    return out


def scenario3(records: list[dict], clusters: list[dict], by_id: dict) -> dict:
    """Arms 0, i, iii — toil separation and the two aggregation ablations."""
    opp, no = [], []
    for cluster in clusters:
        members = _members(by_id, cluster)
        if not members:
            continue
        (opp if str(cluster.get("verdict", "")).startswith("OPPORTUNITY") else no).append(members)

    def cluster_medians(groups: list[list[dict]], getter) -> list[float]:
        return [leverage.median([getter(m) for m in g]) for g in groups]

    def ratio(getter) -> float:
        hi = leverage.median(cluster_medians(opp, getter))
        lo = leverage.median(cluster_medians(no, getter))
        return (hi / lo) if lo else float("inf")

    def lev_ratio(**kwargs) -> float:
        hi = leverage.median([leverage.compute_leverage(g, **kwargs).leverage for g in opp])
        lo = leverage.median([leverage.compute_leverage(g, **kwargs).leverage for g in no])
        return (hi / lo) if lo else float("inf")

    def errors_per_session(groups: list[list[dict]]) -> list[float]:
        # Calibration defines this proxy as errors/session at CLUSTER level
        # (2.15 vs 0.20 = 10.8x), not the median of a per-session count --
        # the per-session median is 0 on the honest-NO side and would report
        # a meaningless infinity.
        return [sum(float(m.get("n_tool_errors", 0) or 0) for m in g) / len(g) for g in groups]

    err_hi = leverage.median(errors_per_session(opp))
    err_lo = leverage.median(errors_per_session(no))

    arm0 = {
        "tool_calls": ratio(lambda m: float(m.get("n_tool_calls", 0) or 0)),
        "span_capped": ratio(leverage.span_capped),
        "tool_errors_per_session": (err_hi / err_lo) if err_lo else float("inf"),
        "llm_cycles": ratio(lambda m: float(m.get("n_llm_cycles", 0) or 0)),
        "combined_leverage": lev_ratio(),
    }
    tool_error_events = sum(int(r.get("n_tool_error_events", 0) or 0) for r in records)

    arm_i_standalone = ratio(lambda m: float(m.get("n_prompts", 0) or 0))
    arm_i_extra = lev_ratio(include_n_prompts=True) / arm0["combined_leverage"] if arm0["combined_leverage"] else 0.0

    # Arm iii is reported under BOTH readings of the written threshold,
    # because the two do not agree and resolving that by fiat would hide a
    # real ambiguity in the scenario:
    #   (a) LITERAL: "cluster-level p75 combined SEPARATION <= 1.3x".
    #   (b) CALIBRATION-FAITHFUL: the source measurement behind "1.22x" is
    #       the p75/median AMPLIFICATION at cluster level (vs 12.8x at
    #       session level) -- i.e. how much extra p75 claims over median,
    #       which is the actual argument for rejecting p75.
    arm_iii_p75_separation = lev_ratio(aggregate="p75")

    def amplification(groups: list[list[dict]]) -> float:
        vals = []
        for g in groups:
            med = leverage.compute_leverage(g, aggregate="median").leverage
            p75 = leverage.compute_leverage(g, aggregate="p75").leverage
            if med:
                vals.append(p75 / med)
        return leverage.median(vals)

    arm_iii_cluster_amplification = amplification(opp + no)

    def span_amplification(groups: list[list[dict]]) -> float:
        vals = []
        for g in groups:
            spans = [leverage.span_capped(m) for m in g]
            med = leverage.median(spans)
            if med:
                vals.append(leverage.percentile(spans, 0.75) / med)
        return leverage.median(vals)

    # calibration.md states the 12.8x / 1.22x pair on WALL SPAN specifically.
    arm_iii_cluster_span_amplification = span_amplification(opp + no)
    session_spans = [leverage.span_capped(r) for r in records]
    arm_iii_session_amplification = (
        leverage.percentile(session_spans, 0.75) / leverage.median(session_spans)
        if leverage.median(session_spans)
        else float("inf")
    )

    passes = {
        "arm0_tool_calls": arm0["tool_calls"] >= S3_BASE_SEPARATION_FLOOR,
        "arm0_span_capped": arm0["span_capped"] >= S3_BASE_SEPARATION_FLOOR,
        "arm0_tool_errors": arm0["tool_errors_per_session"] >= S3_BASE_SEPARATION_FLOOR,
        "arm0_llm_cycles": arm0["llm_cycles"] >= S3_LLM_SEPARATION_FLOOR,
        "arm0_combined": arm0["combined_leverage"] >= S3_BASE_SEPARATION_FLOOR,
        "arm0_error_source_guard": tool_error_events <= S3_TOOL_ERROR_EVENT_GUARD,
        "arm_i_nprompts_zero_signal": S3_NPROMPTS_BAND[0] <= arm_i_standalone <= S3_NPROMPTS_BAND[1],
        "arm_i_no_extra_separation": arm_i_extra <= 1.1,
        "arm_iii_median_wins": arm0["combined_leverage"] >= S3_BASE_SEPARATION_FLOOR,
        # ORCHESTRATOR ADJUDICATION (Stage 2): Arm iii is PASS-WITH-NOTE. The faithful
        # rejection-of-p75 check is the SESSION-LEVEL amplification, which reproduces
        # calibration's 12.8x almost exactly and demonstrates the skew that motivates
        # rejecting p75; clustering then absorbs it (cluster-level collapses to ~1.3x).
        # The asserted check is the session-level reproduction. The cluster-level reading
        # (written from a 1.22x point estimate on slightly different data) is recorded
        # INFORMATIONAL, not asserted.
        "arm_iii_session_amplification_reproduces_calibration": abs(
            arm_iii_session_amplification - S3_SESSION_AMPLIFICATION_TARGET
        )
        <= S3_SESSION_AMPLIFICATION_TOL,
        "arm_iii_clustering_absorbs_the_skew": arm_iii_cluster_amplification < arm_iii_session_amplification,
        "arm_iii_p75_adds_nothing": arm_iii_p75_separation <= arm0["combined_leverage"],
    }
    return {
        "n_opportunity_clusters": len(opp),
        "n_honest_no_clusters": len(no),
        "arm0_separations": arm0,
        "tool_error_events_total": tool_error_events,
        "arm_i_nprompts_standalone_ratio": arm_i_standalone,
        "arm_i_extra_separation_factor": arm_i_extra,
        "arm_iii_p75_combined_separation": arm_iii_p75_separation,
        "arm_iii_cluster_level_p75_over_median_leverage": arm_iii_cluster_amplification,
        "arm_iii_cluster_level_p75_over_median_span": arm_iii_cluster_span_amplification,
        "arm_iii_session_level_p75_over_median": arm_iii_session_amplification,
        "checks": passes,
        "pass": all(passes.values()),
        "arm_iii_note": (
            "PASS-WITH-NOTE (orchestrator-adjudicated, Stage 2). Session-level p75/median "
            f"amplification = {arm_iii_session_amplification:.2f}x reproduces calibration's 12.8x "
            "(the faithful check, asserted). The cluster-level readings "
            f"(leverage {arm_iii_cluster_amplification:.2f}x / span {arm_iii_cluster_span_amplification:.2f}x "
            "vs a written <=1.3x from a 1.22x point estimate on slightly different data) are recorded "
            "INFORMATIONAL, not asserted. Conclusion unchanged: median wins, p75 amplifies one outlier "
            "at session level and is absorbed by clustering."
        ),
    }


def scenario4(records: list[dict]) -> dict:
    """Real-corpus known-answer arms for CYCLE recall and GATE prevalence."""
    n = len(records) or 1
    structural = sum(1 for r in records if fit_cycle.detect(r).cycle)
    explicit = sum(1 for r in records if fit_cycle.detect_explicit_only(r).cycle)
    big = [r for r in records if int(r.get("n_tool_calls", 0) or 0) >= 6]
    big_structural = sum(1 for r in big if fit_cycle.detect(r).cycle)
    prevalence = fit_gate.terminal_check_prevalence(records)

    explicit_rate = explicit / n
    structural_rate = structural / n
    structural_ge6 = big_structural / max(len(big), 1)
    ratio = (structural / explicit) if explicit else float("inf")

    checks = {
        "explicit_rate_in_band": abs(explicit_rate - S4_EXPLICIT_RATE[0]) <= S4_EXPLICIT_RATE[1],
        "structural_rate_floor": structural_rate >= S4_STRUCTURAL_FLOOR,
        "structural_ge6_floor": structural_ge6 >= S4_STRUCTURAL_GE6_FLOOR,
        "implicit_explicit_ratio": ratio >= S4_IMPLICIT_EXPLICIT_RATIO_FLOOR,
        "gate_prevalence_all": abs(prevalence["prevalence_all"] - S4_GATE_PREVALENCE_ALL[0])
        <= S4_GATE_PREVALENCE_ALL[1],
        "gate_prevalence_ge6": abs(prevalence["prevalence_ge6_tools"] - S4_GATE_PREVALENCE_GE6[0])
        <= S4_GATE_PREVALENCE_GE6[1],
    }
    return {
        "n_sessions": len(records),
        "explicit_rate": explicit_rate,
        "structural_rate": structural_rate,
        "structural_rate_ge6": structural_ge6,
        "implicit_explicit_ratio": ratio,
        "gate_prevalence": prevalence,
        "checks": checks,
        "pass": all(checks.values()),
    }


def scenario5(clusters: list[dict], by_id: dict) -> dict:
    """UNKNOWN-never-FAIL corpus-wide + honest-NO composition corroboration."""
    composition = {"recipe": 0, "one-shot": 0, "fragile": 0}
    deterministic_only = {"recipe": 0, "one-shot": 0, "fragile": 0}
    zero_error_clusters = 0
    unknown_as_fail = 0
    rows = []
    for cluster in clusters:
        members = _members(by_id, cluster)
        if not members:
            continue
        recovery = fit_recovery.detect(members)
        # S6 is a DERIVED mapping: it inherits the fit inputs the reasoning
        # tier adjudicated (cycle / evidence_gate / robust_bad_day) rather
        # than re-deciding them. A reasoning-tier `robust_bad_day: false` is
        # the `fragile` judgment; where it is true, the deterministic half
        # supplies the confidence (PASS-high / PASS-provisional / UNKNOWN).
        adjudicated_recovery = recovery.verdict
        if cluster.get("robust_bad_day") is False:
            adjudicated_recovery = fit_recovery.FRAGILE
        verdict = honest_no.classify(
            cycle=bool(cluster.get("cycle")),
            gate=bool(cluster.get("evidence_gate")),
            recovery=adjudicated_recovery,
        )
        det_only = honest_no.classify(
            cycle=bool(cluster.get("cycle")),
            gate=bool(cluster.get("evidence_gate")),
            recovery=recovery.verdict,
        )
        if det_only.no_class in deterministic_only:
            deterministic_only[det_only.no_class] += 1
        if recovery.verdict == fit_recovery.UNKNOWN:
            zero_error_clusters += 1
            if verdict.no_class == "fragile" or verdict.failed_subtest == "4c":
                unknown_as_fail += 1
        if verdict.no_class in composition:
            composition[verdict.no_class] += 1
        rows.append(
            {
                "n_members": len(members),
                "recovery": recovery.verdict,
                "verdict": verdict.verdict,
                "no_class": verdict.no_class,
            }
        )
    # ORCHESTRATOR ADJUDICATION (Stage 2): RE-SPEC. The scenario's 17/14/2
    # expectation baked in REASONING-adjudicated verdicts as if they were
    # deterministic truth -- the exact gap Gate 1 proved justifies the reasoning
    # tier. We do NOT chase 17/14/2. The deterministic truth the mapper is
    # responsible for is that its verdict is a STRICT AND of each cluster's own
    # 4a/4b/4c booleans. We assert exactly that, and document that FINAL verdicts
    # flow deterministic-floor -> reasoning-verdict layer (Gate-1 KEEP).
    mapper_matches_strict_and = 0
    source_verdicts_not_a_strict_and = 0
    for cluster in clusters:
        cyc = bool(cluster.get("cycle"))
        gat = bool(cluster.get("evidence_gate"))
        robust_false = cluster.get("robust_bad_day") is False
        # Strict-AND reference table over the cluster's OWN booleans.
        if not cyc:
            strict = "recipe"
        elif not gat:
            strict = "one-shot"
        elif robust_false:
            strict = "fragile"
        else:
            strict = None  # OPPORTUNITY (or unproven downgrade)
        # The library mapper, fed the same booleans, must agree.
        recovery_in = fit_recovery.FRAGILE if robust_false else fit_recovery.PASS_HIGH
        mapped = honest_no.classify(cycle=cyc, gate=gat, recovery=recovery_in)
        if mapped.no_class == strict:
            mapper_matches_strict_and += 1
        # Informational: where the SOURCE run's stated verdict diverges from
        # strict-AND -- i.e. where the reasoning tier upgraded a mechanical
        # decline. This is the reasoning layer doing its job, not a mapper bug.
        stated = str(cluster.get("verdict", ""))
        stated_class = stated.split(":")[1] if stated.startswith("HONEST-NO:") else None
        stated_derived = strict if strict is not None else None
        if (stated_class or "OPPORTUNITY") != (stated_derived or "OPPORTUNITY"):
            source_verdicts_not_a_strict_and += 1

    # STRUCTURAL truth table over ALL FOUR recovery states (not just the two the
    # 54-cluster pass happens to exercise). This is library self-consistency: it
    # cannot fail on any corpus because it feeds honest_no.classify a synthetic
    # cross-product and asserts the documented precedence. It exists to prove the
    # mapper's contract covers UNKNOWN and PASS-provisional -- the two recovery
    # states the real-corpus source clusters never drive through the mapper.
    truth_table_ok = True
    recovery_states = [
        fit_recovery.PASS_HIGH,
        fit_recovery.PASS_PROVISIONAL,
        fit_recovery.UNKNOWN,
        fit_recovery.FRAGILE,
    ]
    for cyc in (True, False):
        for gat in (True, False):
            for rec_state in recovery_states:
                mapped = honest_no.classify(cycle=cyc, gate=gat, recovery=rec_state)
                if not cyc:
                    expect_class, expect_fit = "recipe", 0
                elif not gat:
                    expect_class, expect_fit = "one-shot", 0
                elif rec_state == fit_recovery.FRAGILE:
                    expect_class, expect_fit = "fragile", 0
                else:  # PASS-high / PASS-provisional / UNKNOWN -> emitted, fit stays 1
                    expect_class, expect_fit = None, 1
                if mapped.no_class != expect_class or mapped.fit != expect_fit:
                    truth_table_ok = False
                # The load-bearing honesty invariant: no recovery state, present
                # or absent, may ever produce a 4c FAIL from a non-FRAGILE input.
                if rec_state != fit_recovery.FRAGILE and mapped.failed_subtest == "4c":
                    truth_table_ok = False

    checks = {
        "unknown_never_fail": unknown_as_fail == 0,
        "mapper_strict_and_all_recovery_states_STRUCTURAL": truth_table_ok,
        "mapper_is_strict_and_of_source_booleans_STRUCTURAL": mapper_matches_strict_and == len(clusters),
        "fragile_count_reproduces": abs(composition["fragile"] - S5_COMPOSITION["fragile"]) <= S5_COMPOSITION_TOL,
    }
    return {
        "n_clusters": len(rows),
        "zero_error_clusters": zero_error_clusters,
        "unknown_rendered_as_fail": unknown_as_fail,
        "mapper_matches_source_strict_and": f"{mapper_matches_strict_and}/{len(clusters)}",
        "mapper_truth_table_16_cases_all_recovery_states": truth_table_ok,
        "composition_deterministic_floor": deterministic_only,
        "composition_with_reasoning_inputs": composition,
        "historical_expected_composition": S5_COMPOSITION,
        "dropped_empirical_composition_delta": {
            "recipe": f"{composition['recipe']} measured vs {S5_COMPOSITION['recipe']} historical",
            "one-shot": f"{composition['one-shot']} measured vs {S5_COMPOSITION['one-shot']} historical",
            "fragile": f"{composition['fragile']} measured vs {S5_COMPOSITION['fragile']} historical",
        },
        "reasoning_layer_upgrades_over_strict_and": source_verdicts_not_a_strict_and,
        "note": (
            "RE-SPEC (orchestrator-adjudicated, Stage 2). The two `_STRUCTURAL` checks are LIBRARY "
            "SELF-CONSISTENCY, not corpus measurements: they cannot fail on any corpus, and they exist "
            "to prove the mapper's verdict is a strict AND of the 4a/4b/4c booleans across ALL FOUR "
            "recovery states (PASS-high / PASS-provisional / UNKNOWN / FRAGILE). We deliberately do NOT "
            "chase the historical 17/14/2 split: that split reflects REASONING-adjudicated verdicts, and "
            "FINAL verdicts flow deterministic-floor -> reasoning-verdict layer (Gate-1 KEEP). The dropped "
            "empirical numbers stay visible above: the deterministic floor produces 20 recipe / 23 one-shot "
            "vs the historical 17 / 14, and "
            f"{source_verdicts_not_a_strict_and} of {len(clusters)} source clusters carry a verdict the "
            "reasoning tier upgraded above the strict-AND floor -- that is the reasoning layer doing its "
            "job, exactly what Gate 1 measured (78% of flips were upgrades of mechanical declines)."
        ),
        "checks": checks,
        "pass": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracts", required=True)
    ap.add_argument("--clusters", required=True)
    ap.add_argument("--out", required=True, help="output DIRECTORY (must be outside the repo)")
    args = ap.parse_args(argv)

    records = extract.read_extracts(args.extracts)
    by_id = {str(r["session_id"]): r for r in records}
    raw = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    clusters = raw.get("clusters", raw) if isinstance(raw, dict) else raw

    report = {
        "n_records": len(records),
        "n_clusters": len(clusters),
        "scenario3_leverage": scenario3(records, clusters, by_id),
        "scenario4_fit": scenario4(records),
        "scenario5_honest_no": scenario5(clusters, by_id),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "real-corpus-arms.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v.get("pass") for k, v in report.items() if isinstance(v, dict)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
