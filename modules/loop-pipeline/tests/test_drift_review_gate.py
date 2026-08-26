"""Engine-dependent residual of the drift-review exemplar's Layer-3 executor guard.

The majority of this file's guards (check_findings.py mutation/fail-closed
tests, corpus/coverage bookkeeping) moved to the repo-root opinionated-layer
harness at tests/test_drift_review_gate.py (Track A of the repo split,
DESIGN-repo-split.md §1.4/§5#2). What stayed here needs the LIVE engine:
  * _report_gate_command()-based tests drive the exact shell text the engine's
    own parser reads out of drift-review.dot's report_gate node.
  * test_the_shipped_graph_keeps_its_layer_three_contract asserts against the
    real parsed Graph AND the engine's lint().
  * _drift_graph()-based tests assert the shipped graph has no dead ends and no
    live retry_target on its goal gate.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRIFT_DIR = _REPO_ROOT / "examples" / "drift-review"
_DOT_PATH = _DRIFT_DIR / "drift-review.dot"

pytestmark = pytest.mark.skipif(
    not _DRIFT_DIR.is_dir(),
    reason="examples/drift-review/ not present (installed-package run)",
)


_CLASSES = ("core-docs", "examples", "guidance", "ledgers")


# ---------------------------------------------------------------------------
# The report-repair budget, executed through the shell the pipeline really runs
# ---------------------------------------------------------------------------

_SHELL_GATE = pytest.mark.skipif(
    not shutil.which("sh"), reason="the shipped report_gate command needs a POSIX shell"
)


def _report_gate_command() -> str:
    """The verbatim tool_command of `report_gate`, read through the engine's parser."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot(_DOT_PATH.read_text(encoding="utf-8"))
    return str(graph.nodes["report_gate"].attrs["tool_command"])


#: What check_findings.py writes beside findings.json on admission, and what
#: report_gate now requires report.md to carry verbatim.
_COVERAGE_TXT = "core-docs: 4/4 (100%)\nexamples: 2/4 (50%)\nguidance: 2/2 (100%)\nledgers: 2/2 (100%)\n"


def _report_gate_workspace(tmp_path: Path) -> Path:
    """A workspace holding an admitted corpus and a report that drops its finding."""
    state = tmp_path / ".drift-review"
    state.mkdir()
    (state / "findings.json").write_text(
        '{"finding_count": 1, "findings": [{"id": "DR-001"}]}\n', encoding="utf-8"
    )
    (state / "coverage.txt").write_text(_COVERAGE_TXT, encoding="utf-8")
    (state / "report.md").write_text(
        "a report naming neither the admitted finding nor the swept classes\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_report_gate(workspace: Path, max_reports: str = "2") -> str:
    result = subprocess.run(
        _report_gate_command(),
        shell=True,
        cwd=workspace,
        env={**os.environ, "max_reports": max_reports},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _complete_report(workspace: Path) -> None:
    """A report that names the finding, all four classes, AND the measured coverage.

    The coverage lines are what stop the deliverable and the admission record
    publishing two different numbers for the same run, so a report without them
    is exactly as incomplete as one that dropped a finding.
    """
    (workspace / ".drift-review" / "report.md").write_text(
        "DR-001 across core-docs, examples, guidance, ledgers\n\n" + _COVERAGE_TXT,
        encoding="utf-8",
    )


@_SHELL_GATE
def test_the_report_repair_budget_is_the_one_the_header_documents(tmp_path):
    """`max_reports=2` buys 2 repairs across at most 3 gate passes -- as documented.

    The header and README both say "default 2 -> at most 3 gate passes", and
    `check_findings.py` spends `--max-revisions` exactly that way. This gate
    tested its budget BEFORE judging (`n -gt B+1`), which granted a third repair
    and landed exhaustion on pass 4.
    """
    workspace = _report_gate_workspace(tmp_path)
    verdicts = [_run_report_gate(workspace) for _ in range(3)]
    assert verdicts == ["report_bad", "report_bad", "report_exhausted"]


@_SHELL_GATE
def test_the_last_permitted_repair_is_still_judged(tmp_path):
    """The budget is spent on the verdict, never instead of it.

    A wall that fires before reading `report.md` throws away the work of the
    repair it just paid for. The final permitted pass must still be able to say
    `report_ok`.
    """
    workspace = _report_gate_workspace(tmp_path)
    assert _run_report_gate(workspace) == "report_bad"
    assert _run_report_gate(workspace) == "report_bad"
    _complete_report(workspace)
    assert _run_report_gate(workspace) == "report_ok"


# ---------------------------------------------------------------------------
# The graph's own contract
# ---------------------------------------------------------------------------


def test_the_shipped_graph_keeps_its_layer_three_contract():
    """The structural promises the header makes, asserted against the file."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import lint

    graph = parse_dot(_DOT_PATH.read_text(encoding="utf-8"))

    # Zero ERROR diagnostics -- the bar every shipped example is held to.
    assert [d for d in lint(graph) if d.severity == "ERROR"] == []

    # Exactly one exit node, with exactly two doors into it: the report gate is
    # the only GREEN one, and `escalated` is the only RED one (issue #252 -- a
    # designed loud terminal has to route, because the main loop has no
    # designed-terminus concept).
    exits = [n for n in graph.nodes.values() if n.is_exit_node()]
    assert len(exits) == 1
    into_exit = graph.incoming_edges(exits[0].id)
    assert sorted(e.from_node for e in into_exit) == ["escalated", "report_gate"]
    green = [e for e in into_exit if e.from_node == "report_gate"]
    assert all("outcome=success" in str(e.condition or "") for e in green), green

    # The verdict is owned by a code-tier node, never by a worker.
    gates = [
        n
        for n in graph.nodes.values()
        if str(n.attrs.get("goal_gate", "")).lower() in ("true", "1", "yes")
    ]
    assert [n.id for n in gates] == ["report_gate"]
    assert graph.nodes["report_gate"].shape == "parallelogram"
    assert graph.nodes["findings_gate"].shape == "parallelogram"

    # The gate that judges findings runs the checker shipped next to the graph.
    assert "check_findings.py" in str(
        graph.nodes["findings_gate"].attrs.get("tool_command")
    )

    # The measured coverage is carried into the deliverable BY A GATE, not by a
    # worker's good intentions: report_gate requires coverage.txt to exist and
    # requires report.md to carry every line of it verbatim. Without both, the
    # honest fraction is advice and the headline can drift back to the reported
    # array length.
    report_gate_command = str(graph.nodes["report_gate"].attrs.get("tool_command"))
    assert "coverage.txt" in report_gate_command
    assert "read -r cl" in report_gate_command
    consolidate_prompt = str(graph.nodes["consolidate"].prompt or "")
    assert "coverage.txt" in consolidate_prompt
    assert "VERBATIM" in consolidate_prompt

    # Both corrective cycles exist: repair findings, and repair the report.
    pairs = {(e.from_node, e.to_node) for e in graph.edges}
    assert ("findings_gate", "revise") in pairs
    assert ("revise", "findings_gate") in pairs
    assert ("report_gate", "consolidate") in pairs
    assert ("consolidate", "report_gate") in pairs

    # Every reviewer carries an artifact contract that success cannot declare away.
    for cls in _CLASSES:
        node_id = "review_" + cls.replace("-", "_")
        assert (
            graph.nodes[node_id].attrs.get("must_write")
            == f".drift-review/raw/{cls}.json"
        )

    # Exactly ONE outcome=fail edge reaches the exit, and it is the loud
    # terminal's own (issue #252).  Before that edge existed the invariant here
    # was "no failure edge may reach the exit" -- which read as a safety
    # property but was actually the bug: `escalated` dead-ended, and the main
    # loop, which has no designed-terminus concept, reported the review's most
    # important honest outcome as PIPELINE_ERROR error_type=no_matching_edge.
    # The property that actually matters is preserved and asserted below: the
    # failure that reaches the exit is the LAST node to complete and exits
    # nonzero, so `_check_goal_gates` returns ITS fail -- status=fail, exit 1 --
    # rather than a machinery failure leaving through the success door green.
    fail_into_exit = [
        edge
        for edge in graph.edges
        if edge.to_node == exits[0].id and "outcome=fail" in str(edge.condition or "")
    ]
    assert [e.from_node for e in fail_into_exit] == ["escalated"], fail_into_exit
    escalated_command = str(graph.nodes["escalated"].attrs["tool_command"])
    assert escalated_command.rstrip().endswith("exit 1"), escalated_command
    assert graph.outgoing_edges("escalated") == fail_into_exit, (
        "the loud terminal must have nowhere else to go -- a node with another "
        "route is a step on a path, not a terminal"
    )


# ---------------------------------------------------------------------------
# Issue #252 -- the dead-end designed terminal, and its goal-gate corollary
#
# The engine's MAIN loop has no designed-terminus concept.  `run_subgraph()`
# distinguishes "no outgoing edges at all" (a designed terminus) from a
# conditional-mismatch dead end; `run()` does NOT -- it reports
# `error_type=no_matching_edge` as a PIPELINE_ERROR whatever the exit status.
# So `escalated` -- a tool node that exits 1 on purpose, having just written the
# handoff -- was reported as an authoring bug when it was reached.  Measured on
# the shipped CLI against this very file, with a blocked preflight:
#
#     [PIPELINE] X Error at escalated (no_matching_edge): Command exited with
#     code 1: escalated
#     notes: No matching edge from node 'escalated'
#
# Read through the engine's own parser, not a paraphrase of the file.
# ---------------------------------------------------------------------------


def _drift_graph():
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    return parse_dot(_DOT_PATH.read_text(encoding="utf-8"))


def test_escalated_routes_to_the_exit_instead_of_dead_ending():
    """A loud terminal must ROUTE; the main loop has no designed terminus.

    One edge -- `escalated -> done [outcome=fail]` -- is the convergence-factory
    idiom proven in #248.  `_check_goal_gates` then returns the LAST COMPLETED
    node's outcome, so `escalated`'s own nonzero exit becomes the run's
    status=fail / CLI exit 1, with no routing error, and
    `.drift-review/disposition` still says which terminal it was.
    """
    graph = _drift_graph()
    exits = [n.id for n in graph.nodes.values() if n.is_exit_node()]
    assert exits == ["done"], exits

    outgoing = graph.outgoing_edges("escalated")
    assert outgoing, (
        "`escalated` has no outgoing edge, so the engine reports the designed "
        "escalation as PIPELINE_ERROR error_type=no_matching_edge (issue #252)"
    )
    assert [e.to_node for e in outgoing] == ["done"]
    assert "outcome=fail" in (outgoing[0].condition or ""), outgoing[0].condition


def test_escalated_still_exits_nonzero_so_the_exit_it_reaches_is_red():
    """The routing fix must not turn the loud terminal into a quiet one."""
    node = _drift_graph().nodes["escalated"]
    command = str(node.attrs["tool_command"])
    assert command.rstrip().endswith("exit 1"), command
    assert str(node.attrs.get("max_retries")) == "0"
    assert ".drift-review/disposition" in command


def test_no_node_in_the_drift_review_graph_dead_ends():
    """The whole-graph form of the rule, so a future terminal cannot regress."""
    graph = _drift_graph()
    exits = {n.id for n in graph.nodes.values() if n.is_exit_node()}
    dead_ends = [
        n for n in graph.nodes if n not in exits and not graph.outgoing_edges(n)
    ]
    assert dead_ends == [], dead_ends


def test_the_report_gate_carries_no_retry_target():
    """#252's corollary, which #252 does not mention and #248 discovered.

    `retry_target` on a goal gate is consulted in exactly one place --
    `_check_goal_gates()` at the exit node.  While `escalated` dead-ended, the
    exit was unreachable with `report_gate` unsatisfied, so the attribute was
    dead.  `escalated -> done` makes it reachable -- and in THIS graph
    `report_gate -> escalated [outcome=fail]` is the shortest path there, so the
    attribute becomes live on the very route it must not fire on.  Measured on
    the shipped engine with a faithful reduction of exactly that shape:
    `consolidate`, `report_gate` and `escalated` executed 51 times each before
    the step cap with `retry_target="consolidate"`, and once each without it.
    `no_corpus` means the findings gate never admitted a corpus -- a cause no
    number of re-consolidations can change.

    The corrective cycle is untouched: it is the `report_bad` edge back to
    `consolidate`, walled by $max_reports.
    """
    graph = _drift_graph()
    gate = graph.nodes["report_gate"]
    assert str(gate.attrs.get("goal_gate", "")).lower() == "true"
    assert not gate.attrs.get("retry_target"), gate.attrs.get("retry_target")
    assert not gate.attrs.get("fallback_retry_target")
    assert "retry_target" not in graph.graph_attrs

    pairs = {(e.from_node, e.to_node) for e in graph.edges}
    assert ("report_gate", "consolidate") in pairs
