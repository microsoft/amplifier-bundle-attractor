#!/usr/bin/env python3
"""GATE 1 — verdict-tier necessity A/B (Scenario 6). Sizes the LLM layer.

The question this gate answers is a BUILD question, not a quality question:
do we build ONE model tier or TWO? The reasoning-tier merge/verdict sub-agent
only earns its existence if it changes enough verdicts to matter.

Decision rule (pre-registered, applied without adjustment):

    KEEP the reasoning tier   iff  flip-rate 95% CI LOWER bound >= 15%
    KILL the reasoning tier   iff  flip-rate 95% CI UPPER bound < 10%
    otherwise                      KEEP, and flag as under-separated

The held-out split is a PURE FUNCTION of cluster ids -- `sha256(id) % 100 < 30`
-- so anyone can regenerate it and check the recorded hash. Nothing about the
split depends on the run that produced it.

This script does the deterministic work only: freeze the split, build the
evidence payload, and score the arms. The two model arms are run OUTSIDE it,
as `fast` and `reasoning` sub-agents over the payload, and their verdict JSON
is fed back to `score`. Keeping the model calls out of the script is what
makes the scoring reproducible from stored artifacts.

    python evals/gate1_verdict_tier_ab.py prepare --run <dir> --out <dir>
    python evals/gate1_verdict_tier_ab.py score   --out <dir>
    python evals/gate1_verdict_tier_ab.py shatter --run <dir> --out <dir>
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from attractor_scout import extract, frequency_signature, leverage  # noqa: E402

HELD_OUT_PERCENT = 30
CI_KEEP_LOWER = 0.15
CI_KILL_UPPER = 0.10
POINT_ESTIMATE_BAND = (0.18, 0.30)
N_TRIALS = 5

# B-rung degradation guard (Scenario 6, structural).
SHATTER_DOMINANT_SHARE_CEILING = 0.05
SHATTER_FRAGMENT_FLOOR = 100


def held_out(cluster_id: str) -> bool:
    """Frozen, reproducible partition. A pure function of the id."""
    return int(hashlib.sha256(cluster_id.encode()).hexdigest(), 16) % 100 < HELD_OUT_PERCENT


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval. Returns (point, lower, upper)."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return p, max(0.0, centre - margin), min(1.0, centre + margin)


def _expand(members: list, by_id: dict) -> list[dict]:
    """Resolve batch member ids (8-char) to full records.

    An ambiguous prefix expands to ALL matches rather than picking one --
    the measured 3.0% collision rate means picking would silently attribute
    another session's toil to this cluster.
    """
    out: list[dict] = []
    for raw in members:
        sid = str(raw)
        rec = by_id.get(sid)
        if rec is not None:
            out.append(rec)
            continue
        out.extend(v for k, v in by_id.items() if k.startswith(sid))
    return out


def observables(members: list[dict]) -> dict:
    """Compact, deterministic evidence — identical for BOTH arms."""
    n = len(members) or 1
    prof = leverage.compute_leverage(members)
    return {
        "n": len(members),
        "med_tools": round(prof.med_tool_calls, 1),
        "med_llm": round(prof.med_llm_cycles, 1),
        "med_span_s": round(prof.med_span_capped_s, 1),
        "sum_errs": sum(int(m.get("n_tool_errors", 0) or 0) for m in members),
        "sum_recov": sum(int(m.get("n_err_recover", 0) or 0) for m in members),
        "iloop_share": round(sum(1 for m in members if m.get("implicit_loop")) / n, 2),
        "tcheck_share": round(sum(1 for m in members if m.get("terminal_check")) / n, 2),
        "completed_share": round(sum(1 for m in members if str(m.get("status")) == "completed") / n, 2),
        "med_prompts": round(leverage.median([float(m.get("n_prompts", 0) or 0) for m in members]), 1),
    }


def cmd_prepare(args) -> int:
    run = Path(args.run)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    records = extract.read_extracts(run / "extracts.jsonl")
    by_id = {str(r["session_id"]): r for r in records}

    clusters: list[dict] = []
    for path in sorted(glob.glob(str(run / "clusters" / "*.json"))):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        clusters.extend(data.get("clusters", []))

    payload, ids = [], []
    for cluster in clusters:
        cid = str(cluster.get("id"))
        if not held_out(cid):
            continue
        ids.append(cid)
        members = _expand(cluster.get("members") or [], by_id)
        payload.append(
            {
                "id": cid,
                "name": cluster.get("name"),
                "gist": (cluster.get("gist") or "")[:280],
                "obs": observables(members),
            }
        )

    ids.sort()
    ids_text = "\n".join(ids) + "\n"
    (out / "heldout-verdict-ab.txt").write_text(ids_text, encoding="utf-8")
    split_hash = hashlib.sha256(ids_text.encode()).hexdigest()

    payload.sort(key=lambda p: p["id"])
    (out / "gate1-payload.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    manifest = {
        "n_total_batch_clusters": len(clusters),
        "n_held_out": len(ids),
        "held_out_fraction": len(ids) / max(len(clusters), 1),
        "split_rule": "int(sha256(id).hexdigest(),16) % 100 < 30",
        "heldout_sha256": split_hash,
        "n_trials": N_TRIALS,
        "decision_rule": {
            "keep_if_ci_lower_gte": CI_KEEP_LOWER,
            "kill_if_ci_upper_lt": CI_KILL_UPPER,
            "point_estimate_band": POINT_ESTIMATE_BAND,
        },
    }
    (out / "gate1-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest, indent=1))
    return 0


def _load_arm(out: Path, arm: str, trial: int) -> dict[str, str]:
    path = out / f"gate1-{arm}-trial{trial}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("verdicts", raw) if isinstance(raw, dict) else raw
    return {str(i["id"]): str(i["verdict"]) for i in items}


def cmd_score(args) -> int:
    out = Path(args.out)
    held = [line.strip() for line in (out / "heldout-verdict-ab.txt").read_text().splitlines() if line.strip()]

    flips = 0
    compared = 0
    per_trial = []
    flip_kinds: dict[str, int] = {}
    missing: list[str] = []

    for trial in range(1, N_TRIALS + 1):
        try:
            fast = _load_arm(out, "fast", trial)
            reasoning = _load_arm(out, "reasoning", trial)
        except FileNotFoundError as exc:
            missing.append(str(exc))
            continue
        t_flips = t_n = 0
        for cid in held:
            if cid not in fast or cid not in reasoning:
                continue
            t_n += 1
            if fast[cid] != reasoning[cid]:
                t_flips += 1
                flip_kinds[f"{fast[cid]} -> {reasoning[cid]}"] = (
                    flip_kinds.get(f"{fast[cid]} -> {reasoning[cid]}", 0) + 1
                )
        flips += t_flips
        compared += t_n
        per_trial.append({"trial": trial, "n": t_n, "flips": t_flips, "rate": (t_flips / t_n) if t_n else None})

    point, lower, upper = wilson_ci(flips, compared)
    if lower >= CI_KEEP_LOWER:
        decision = "KEEP"
        rationale = f"95% CI lower bound {lower:.3f} >= {CI_KEEP_LOWER}"
    elif upper < CI_KILL_UPPER:
        decision = "KILL"
        rationale = f"95% CI upper bound {upper:.3f} < {CI_KILL_UPPER}"
    else:
        decision = "KEEP (flagged)"
        rationale = (
            f"95% CI [{lower:.3f}, {upper:.3f}] straddles the decision band: neither "
            f">= {CI_KEEP_LOWER} nor < {CI_KILL_UPPER}. Default is KEEP, flagged as under-separated."
        )

    report = {
        "n_held_out": len(held),
        "n_trials_scored": len(per_trial),
        "n_comparisons": compared,
        "n_flips": flips,
        "flip_rate_point": point,
        "flip_rate_ci95": [lower, upper],
        "point_in_preregistered_band": POINT_ESTIMATE_BAND[0] <= point <= POINT_ESTIMATE_BAND[1],
        "per_trial": per_trial,
        "flip_directions": dict(sorted(flip_kinds.items(), key=lambda kv: -kv[1])),
        "DECISION": decision,
        "rationale": rationale,
        "correction_fraction": None,
        "correction_fraction_status": (
            "DEFERRED - signal-gaps Gap 4: there is no independent frozen human-adjudicated gold. "
            "Scoring corrections against the reasoning run itself would be the reasoning run "
            "adjudicating its own flips, which the scenario explicitly voids. Reported as unmeasured "
            "rather than manufactured."
        ),
        "missing_arm_files": missing,
    }
    (out / "gate1-decision.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_trial"}, indent=1))
    return 0


def cmd_shatter(args) -> int:
    """B-rung degradation guard: would a sequence matcher shatter the unit?"""
    run = Path(args.run)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records = extract.read_extracts(run / "extracts.jsonl")
    by_id = {str(r["session_id"]): r for r in records}
    raw = json.loads((run / "global-clusters-verified.json").read_text(encoding="utf-8"))
    clusters = raw.get("clusters", raw)

    ranked = sorted(clusters, key=lambda c: -int(c.get("n_sessions") or 0))
    target = next((c for c in ranked if str(c.get("author")) in ("human", "mixed")), ranked[0])
    members = _expand(target.get("members") or [], by_id)
    _, share, fragments = frequency_signature.dominant_signature_share(members)

    report = {
        "target": "largest human/mixed cluster (id withheld - internal)",
        "n_members": len(members),
        "dominant_signature_share": share,
        "fragment_count": fragments,
        "checks": {
            "dominant_share_at_or_below_ceiling": share <= SHATTER_DOMINANT_SHARE_CEILING,
            "fragment_count_at_or_above_floor": fragments >= SHATTER_FRAGMENT_FLOOR,
        },
        "interpretation": (
            "A pure tool-call sequence matcher covers only this fraction of the unit's members and "
            "would split it into this many fragments. That is the proof the semantic B-rung pass is "
            "the mechanism, and that the merge path did not silently degrade to A-rung matching."
        ),
    }
    report["pass"] = all(report["checks"].values())
    (out / "gate1-shatter-guard.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("score")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_score)
    p = sub.add_parser("shatter")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_shatter)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
