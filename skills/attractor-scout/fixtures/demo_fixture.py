"""Canned demonstration artifacts — ground truth BY CONSTRUCTION.

CI cannot run the authoring delegation, so what CI proves is *everything
around it*: brief assembly, narrative validation, the verification ladder, the
publish-after-gates rule, and rendering. All of that needs a draft to act on,
and this module is that draft — a deterministic, doctrine-clean pipeline in
the convergence-factory shape, its companion, and a matching narrative.

**ZERO real data.** Every name here is synthetic and marked as such; the unit
name carries the `SYNTHETIC-` marker discipline the corpus fixtures use, and
`tests/test_no_real_data_leak.py` re-checks that claim on every run.

The mutation helpers are the red-proof half. A gate that has never been shown
failing is not a gate, so each one breaks exactly one property:

* `without_gate()`      — deletes the evidence gate: A4 goes red.
* `with_invented_count()` — plants a number the ranking never produced.
* `with_unknown_node()` — walks a node that is not in the graph.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The planted unit's marker. Obviously synthetic, and never a real name.
DEMO_UNIT_NAME = "SYNTHETIC-UNIT-D verify and repair the generated report"
DEMO_UNIT_ID = "d1"

#: A doctrine-clean pipeline: 9 nodes, one exit, one evidence gate, a budget
#: wall, a failure route on every worker, and one loud terminal that routes
#: its own nonzero exit into the single exit node.
DEMO_DOT = """\
// SYNTHETIC demonstration pipeline (test fixture). Not mined from anyone.
digraph SyntheticDemo {
    graph [
        goal="Converge the recurring repair loop: run the project check, fix what it reports, and stop only when the check is green.",
        params="target_dir, max_iterations",
        default_max_retries=2,
        max_pipeline_duration="3600s"
    ]

    start [shape=Mdiamond]
    done  [shape=Msquare]

    preflight [shape=parallelogram, max_retries=0,
        tool_command="mkdir -p .demo && B=$max_iterations; echo ${B:-3} > .demo/budget && echo 0 > .demo/iter && [ -d \\"$target_dir\\" ] && printf ready || printf blocked"]

    repair [shape=box,
        prompt="Advance the repair. Read .demo/check.log if it exists -- it holds the latest failure output -- and fix exactly what it reports in $target_dir. Objective: the project check exits clean. Constraints: change nothing the check does not force you to change. Required evidence: the check's own output, which the next node runs. Never edit the check to make it pass."]

    check [shape=parallelogram, max_retries=0, goal_gate=true,
        tool_command="n=$(cat .demo/iter 2>/dev/null || echo 0); n=$((n+1)); echo $n > .demo/iter; B=$(cat .demo/budget 2>/dev/null); B=${B:-3}; if [ \\"$n\\" -gt \\"$B\\" ]; then printf exhausted; else pytest -q > .demo/check.log 2>&1; rc=$?; [ $rc -eq 0 ] && printf green || { printf red; exit 1; }; fi"]

    triage [shape=parallelogram, max_retries=0,
        tool_command="B=$(cat .demo/budget 2>/dev/null); B=${B:-3}; n=$(cat .demo/iter 2>/dev/null || echo 0); if [ \\"$n\\" -ge \\"$B\\" ]; then printf spent; else printf retry; fi"]

    salvage [shape=box,
        prompt="The loop did not converge inside its budget. Objective: salvage the run's value. Read .demo/check.log and .demo/iter, then write .demo/report.md naming what each attempt tried, whether the failures were descending or repeating, and the single most likely root cause. Constraints: do not attempt another repair here. Required evidence: quote the check output you are reasoning from."]

    salvage_check [shape=parallelogram, max_retries=0,
        tool_command="[ -s .demo/report.md ] && printf ok || { echo 'salvage report missing' > .demo/report.md; printf stub; }"]

    give_up [shape=parallelogram, max_retries=0,
        tool_command="echo 'NOT CONVERGED: see .demo/report.md' >&2; exit 1"]

    start -> preflight
    preflight -> repair  [condition="context.tool.last_line=ready && outcome=success"]
    preflight -> give_up [condition="context.tool.last_line=blocked && outcome=success"]

    repair -> check
    repair -> triage [condition="outcome=fail"]

    check -> done    [condition="context.tool.last_line=green && outcome=success"]
    check -> triage  [condition="outcome=fail"]
    check -> salvage [condition="context.tool.last_line=exhausted && outcome=success", label="budget wall"]

    triage -> repair  [condition="context.tool.last_line=retry && outcome=success", label="fix"]
    triage -> salvage [condition="context.tool.last_line=spent && outcome=success", label="budget spent"]

    salvage -> salvage_check
    salvage -> give_up [condition="outcome=fail"]
    salvage_check -> give_up

    give_up -> done [condition="outcome=fail", label="loud exit -- the run's status is give_up's own nonzero exit"]
}
"""

#: A9 requires the companion to NAME EVERY box node id: `repair`, `salvage`.
DEMO_COMPANION = """\
# SYNTHETIC demonstration pipeline — companion

## What it converges on

The project's own check command exits clean. Nothing else ends the loop: the
exit is reachable only through `check`, and `check` only prints `green` when
the command it ran actually succeeded.

## The LLM worker nodes

### `repair`

Contract: advance the repair using the latest check output as its evidence.
It is told the objective and the constraints, never the algorithm. It carries a
failure route to `triage`, so one bad provider response does not end the run.

### `salvage`

Contract: when the budget is spent, write the run's postmortem instead of
attempting another repair. It exists so that a non-converging run still produces
something worth reading.

## What the gate proves

`check` runs the project's own check command and routes on its exit status. It
is the only way to reach the exit, and it walls the iteration budget before the
command runs, so a persistently failing loop drains the budget and routes to
`salvage` rather than spinning.

## The honest failure exit

`give_up` prints why and exits nonzero. Its own failure becomes the run's
status, which is why it routes into the single exit node rather than dead-ending.
"""

#: The six named prose slots. Deliberately digit-free except for structural
#: references, so the fixture passes the whitelist for ANY unit's stats.
DEMO_NARRATIVE: dict = {
    "scenario_gist": (
        "You keep running the project's own check, reading what it complains about, fixing that, "
        "and running it again — until it finally comes back clean."
    ),
    "q1_cycle_note": (
        "Yes: the work is attempted, checked, and re-attempted. The check output from one round is "
        "what the next round acts on, which is exactly the shape a loop is for."
    ),
    "q2_gate_note": (
        "Yes: the project's own check command is the definition of done. It is red before the work "
        "lands and green only after, so the exit can gate on it rather than on anybody's say-so."
    ),
    "q3_recovery_note": (
        "A bad day here is one attempt that makes things worse or a provider hiccup mid-repair. The "
        "gate catches both, the loop pays for another attempt, and the budget wall stops it spinning."
    ),
    "pipeline_walk": [
        {"node": "preflight", "note": "sets the budget and refuses early if the target is not there."},
        {"node": "repair", "note": "the worker: reads the last failure and fixes exactly that."},
        {"node": "check", "note": "the evidence gate: runs the real check and routes on its exit status."},
        {"node": "triage", "note": "decides whether another attempt is affordable."},
        {"node": "salvage", "note": "writes the postmortem when the budget is spent."},
        {"node": "give_up", "note": "the loud terminal: says why, and exits nonzero."},
    ],
    "payoff_note": (
        "Running this loop hands back the read-fix-recheck rhythm you have been doing by hand, and "
        "stops only on evidence you already trust."
    ),
}


def ranked_fixture(
    *,
    unit_id: str = DEMO_UNIT_ID,
    name: str = DEMO_UNIT_NAME,
    verdict: str = "OPPORTUNITY",
    n_sessions: int = 7,
    med_tool_calls: float = 12.0,
    med_llm_cycles: float = 4.0,
    med_span_s: float = 930.0,
    err_rate: float = 0.33,
    provisional: bool = False,
    recovery: str = "PASS",
    extra_opportunities: list[dict] | None = None,
) -> dict:
    """A minimal `ranked.json` carrying one demonstrable opportunity."""
    unit = {
        "unit_id": unit_id,
        "name": name,
        "n_sessions": n_sessions,
        "leverage": 32.5,
        "fit": 1,
        "score": 22.75,
        "author": "human",
        "verdict": verdict,
        "no_class": None,
        "failed_subtest": None,
        "remediation": None,
        "recovery": recovery,
        "confidence": "high",
        "provisional": provisional,
        "trajectory": "escalating",
        "rung": "B",
        "n_members": n_sessions,
        "members": [f"synd-{i:04d}-4000-8000-{i:012d}" for i in range(n_sessions)],
        "gist": "SYNTHETIC scenario: run the check, fix what it reports, run it again.",
        "leverage_detail": {
            "n_sessions": n_sessions,
            "med_tool_calls": med_tool_calls,
            "med_llm_cycles": med_llm_cycles,
            "med_span_capped_s": med_span_s,
            "errors_per_session": err_rate,
            "leverage": 32.5,
        },
        "recovery_detail": {"verdict": recovery},
        "fit_detail": {
            "verdict": verdict,
            "fit": 1,
            "cycle": True,
            "gate": True,
            "recovery": recovery,
            "confidence": "high",
        },
    }
    opportunities = [unit, *(extra_opportunities or [])]
    return {
        "opportunities": opportunities,
        "honest_no": [],
        "waste_findings": [],
        "below_frequency_floor": [],
        "summary": {
            "n_units_in": len(opportunities),
            "n_admitted": len(opportunities),
            "n_waste": 0,
            "n_opportunities": len(opportunities),
            "n_honest_no": 0,
            "n_below_floor": 0,
            "honest_no_rate": 0.0,
        },
    }


def write_draft(
    workdir: str | Path,
    *,
    dot_text: str | None = None,
    companion: str | None = None,
    narrative: dict | None = None,
    omit: tuple[str, ...] = (),
) -> Path:
    """Materialize what the authoring delegate is contracted to write.

    `omit` drops files by name, so a test can prove the missing-file paths.
    """
    target = Path(workdir)
    target.mkdir(parents=True, exist_ok=True)
    if "pipeline.dot" not in omit:
        (target / "pipeline.dot").write_text(dot_text if dot_text is not None else DEMO_DOT, encoding="utf-8")
    if "pipeline.md" not in omit:
        (target / "pipeline.md").write_text(companion if companion is not None else DEMO_COMPANION, encoding="utf-8")
    if "narrative.json" not in omit:
        payload = narrative if narrative is not None else DEMO_NARRATIVE
        (target / "narrative.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return target


# ------------------------------------------------------------------ mutations
def without_gate() -> str:
    """Delete the evidence gate, and let the exit hang off a worker instead.

    A4 must go red: the exit becomes reachable by a path that never touches a
    gate, which is the one property the whole doctrine rests on. A1-A3 still
    pass on this graph — that is exactly why A4 exists and why this red-proof
    is worth having.
    """
    out: list[str] = []
    skipping = False
    for line in DEMO_DOT.splitlines(keepends=True):
        if line.lstrip().startswith("check [shape=parallelogram"):
            skipping = True
            continue
        if skipping:
            # the gate declaration runs until its closing bracket line
            if line.rstrip().endswith("]"):
                skipping = False
            continue
        if line.lstrip().startswith("check ->"):
            continue
        if line.strip() == "start -> preflight":
            out.append(line.replace("start -> preflight", "start -> repair"))
            continue
        if line.strip() == "repair -> check":
            out.append(line.replace("repair -> check", "repair -> done"))
            continue
        out.append(line)
    return "".join(out)


def narrative_without(node: str = "check") -> dict:
    """The canned narrative with one node dropped from its walk.

    Pairs with `without_gate()`: the mutated graph no longer carries that
    node, and the point of the gate red-proof is to reach the LADDER, not to
    trip the (separately proven) node-name check on the way there.
    """
    ok = json.loads(json.dumps(DEMO_NARRATIVE))
    ok["pipeline_walk"] = [step for step in ok["pipeline_walk"] if step["node"] != node]
    return ok


def with_invented_count(field: str = "payoff_note", token: str = "97") -> dict:
    """A narrative that states a number the ranking never produced."""
    bad = json.loads(json.dumps(DEMO_NARRATIVE))
    bad[field] = f"This loop would have saved you {token} separate hand-runs of the same check."
    return bad


def with_unknown_node(node: str = "nonexistent_node") -> dict:
    """A `pipeline_walk` step naming a node that is not in the graph."""
    bad = json.loads(json.dumps(DEMO_NARRATIVE))
    bad["pipeline_walk"] = [{"node": node, "note": "this node was never in the pipeline."}]
    return bad


def without_slot(slot: str = "payoff_note") -> dict:
    """A narrative missing one of the six required slots."""
    bad = json.loads(json.dumps(DEMO_NARRATIVE))
    bad.pop(slot, None)
    return bad
