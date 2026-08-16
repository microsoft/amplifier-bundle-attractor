"""Guards for the authoring-layer exemplar's structural gate.

``examples/authoring/pipeline-author.dot`` lets an LLM node WRITE a new reusable
attractor pipeline, so the thing that decides whether the draft is any good has
to live outside the author's context. Two machine gates do, in order:
``attractor lint`` (the engine's own linter, already guarded by
``test_examples_lint_clean.py``) and ``check_authored_pipeline.py`` -- the
*doctrine* checks lint deliberately does not own, or owns only as advice.

This file holds that second gate to the same bar the objective layer's gates are
held to:

  * it must **fail closed** -- a missing, unreadable or unparseable draft
    produces the rejecting token, never the admitting one, and never a traceback;
  * every check must be seen **RED**, by mutation, not just green (a check never
    seen red is an unproven check);
  * its stdlib-only DOT reader must **agree with the engine's parser** on the
    graphs this repo actually ships;
  * it must be **calibrated** -- it admits the repo's own battle-tested
    exemplars, including the authoring attractor itself. A gate that rejects
    ``task-runner.dot`` is a wrong gate, not a strict one.

The examples tree lives at the repository root, outside the installed package,
so these tests run against a source checkout and skip gracefully when the
examples directory is absent (e.g. an installed-package test run) -- the same
pattern as ``test_examples_lint_clean.py``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTHORING_DIR = _REPO_ROOT / "examples" / "authoring"

pytestmark = pytest.mark.skipif(
    not _AUTHORING_DIR.is_dir(),
    reason="examples/authoring/ not present (installed-package run)",
)


def _load(script_name: str) -> ModuleType:
    """Import a script from examples/authoring/ by path, without polluting sys.path."""
    path = _AUTHORING_DIR / script_name
    spec = importlib.util.spec_from_file_location(f"_authoring_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load("check_authored_pipeline.py")


# ---------------------------------------------------------------------------
# Fixtures: a draft that satisfies the contract, and ways of breaking it
# ---------------------------------------------------------------------------

#: A conforming authored pipeline. Deliberately small -- it carries exactly the
#: skeleton A1-A9 require and nothing else, so a mutation below can only be
#: breaking the property it names.
_GOOD_PIPELINE = """
// An authored pipeline that satisfies the authoring contract.
digraph Authored {
    graph [goal="$goal", default_max_retries=2]

    start [shape=Mdiamond]
    done  [shape=Msquare]

    work [shape=box,
          prompt="Advance the objective: $goal. Read .run/feedback.md if it exists."]

    dod_gate [shape=parallelogram, max_retries=0, goal_gate=true, retry_target="work",
        tool_command="mi=$max_iterations; B=${mi:-6}; n=$(($(cat .run/iter 2>/dev/null || echo 0)+1)); echo $n > .run/iter; if [ \\"$n\\" -gt \\"$B\\" ]; then printf exhausted; else bash dod.sh > .run/dod.log 2>&1 && printf green || { printf red; exit 1; }; fi"]

    critique [shape=box, prompt="Write .run/feedback.md from .run/dod.log."]

    postmortem [shape=box, prompt="Write .run/postmortem.md."]

    escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]

    start -> work
    work -> dod_gate
    work -> postmortem [condition="outcome=fail"]

    dod_gate -> done       [condition="context.tool.last_line=green && outcome=success"]
    dod_gate -> critique   [condition="outcome=fail", label="not yet -- iterate"]
    dod_gate -> postmortem [condition="context.tool.last_line=exhausted && outcome=success", label="budget spent"]

    critique -> work       [loop_restart="true"]
    critique -> postmortem [condition="outcome=fail"]

    postmortem -> escalated
    postmortem -> escalated [condition="outcome=fail"]
}
"""

#: A companion that names every worker in _GOOD_PIPELINE.
_GOOD_COMPANION = """# Authored pipeline

`work` advances the objective; `critique` distils the failing DoD output into
feedback the next attempt reads; `postmortem` salvages the analysis when the
budget is spent. Each states objective, constraints, capabilities and evidence.
"""


def _write_draft(
    tmp_path: Path,
    dot_text: str = _GOOD_PIPELINE,
    *,
    companion: str | None = _GOOD_COMPANION,
) -> tuple[Path, Path]:
    draft = tmp_path / "draft"
    draft.mkdir(parents=True, exist_ok=True)
    pipeline = draft / "pipeline.dot"
    pipeline.write_text(dot_text, encoding="utf-8")
    companion_path = draft / "pipeline.md"
    if companion is not None:
        companion_path.write_text(companion, encoding="utf-8")
    return pipeline, companion_path


def _run_checker(
    checker: ModuleType, pipeline: Path, companion: Path, report: Path
) -> tuple[int, str]:
    rc = checker.main(
        ["--pipeline", str(pipeline), "--companion", str(companion), "--report", str(report)]
    )
    return rc, report.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_checker_admits_a_conforming_pipeline(checker, tmp_path, capsys):
    pipeline, companion = _write_draft(tmp_path)
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_ok"
    assert "verdict:   doctrine_ok" in text
    assert "[FAIL]" not in text


# ---------------------------------------------------------------------------
# RED cases -- one mutation per check.  A check never seen red is an unproven
# check, so each of A1-A9 is broken here in isolation and observed to fire.
# ---------------------------------------------------------------------------

_A1_SECOND_EXIT = ('done  [shape=Msquare]', 'done  [shape=Msquare]\n    done2 [shape=Msquare]')
_A2_NO_CYCLE = ('critique -> work       [loop_restart="true"]', "")
_A3_HOLLOW_GATE = (
    'tool_command="mi=$max_iterations; B=${mi:-6}; n=$(($(cat .run/iter 2>/dev/null || echo 0)+1)); echo $n > .run/iter; if [ \\"$n\\" -gt \\"$B\\" ]; then printf exhausted; else bash dod.sh > .run/dod.log 2>&1 && printf green || { printf red; exit 1; }; fi"',
    'tool_command="printf green"',
)
_A4_BYPASS = ("start -> work", "start -> work\n    work -> done")
_A5_NO_WALL = (
    'dod_gate -> postmortem [condition="context.tool.last_line=exhausted && outcome=success", label="budget spent"]',
    "",
)
_A6_UNROUTED_WORKER = ('work -> postmortem [condition="outcome=fail"]', "")
_A7_STALE_LABEL = (
    'condition="context.tool.last_line=green && outcome=success"',
    'condition="context.tool.last_line=green"',
)
_A8_FAIL_TO_EXIT = (
    "critique -> work       [loop_restart=\"true\"]",
    'critique -> work       [loop_restart="true"]\n    critique -> done [condition="outcome=fail"]',
)
#: A10 -- give the gate a SECOND token that also ends at the exit. Nothing is
#: removed, so A5's exhaustion route survives, the conjunction keeps A7 quiet
#: and `red` is a token rather than an outcome so A8 stays green: exactly the
#: single property under test, and nothing else.
_A10_SECOND_TOKEN_TO_EXIT = (
    'dod_gate -> critique   [condition="outcome=fail", label="not yet -- iterate"]',
    (
        'dod_gate -> critique   [condition="outcome=fail", label="not yet -- iterate"]\n'
        '    dod_gate -> done   [condition="context.tool.last_line=red && outcome=success"]'
    ),
)


@pytest.mark.parametrize(
    ("mutation", "expected_fail"),
    [
        pytest.param(_A1_SECOND_EXIT, "A1", id="A1-two-exit-nodes"),
        pytest.param(_A2_NO_CYCLE, "A2", id="A2-acyclic-should-have-been-a-recipe"),
        pytest.param(_A3_HOLLOW_GATE, "A3", id="A3-gate-that-only-printfs-a-constant"),
        pytest.param(_A4_BYPASS, "A4", id="A4-exit-reachable-without-a-gate"),
        pytest.param(_A5_NO_WALL, "A5", id="A5-no-budget-wall"),
        pytest.param(_A6_UNROUTED_WORKER, "A6", id="A6-worker-with-no-failure-route"),
        pytest.param(_A7_STALE_LABEL, "A7", id="A7-label-edge-without-the-conjunction"),
        pytest.param(_A8_FAIL_TO_EXIT, "A8", id="A8-failure-routed-into-the-exit"),
        pytest.param(_A10_SECOND_TOKEN_TO_EXIT, "A10", id="A10-both-gate-answers-end-at-the-exit"),
    ],
)
def test_checker_catches_each_single_mutation(checker, tmp_path, capsys, mutation, expected_fail):
    """Break exactly one property; the check that owns it must fire."""
    old, new = mutation
    assert old in _GOOD_PIPELINE, "mutation anchor drifted out of the fixture"
    pipeline, companion = _write_draft(tmp_path, _GOOD_PIPELINE.replace(old, new, 1))
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_bad"
    assert f"[FAIL] {expected_fail}" in text, text


def test_a9_fires_when_the_companion_is_missing(checker, tmp_path, capsys):
    pipeline, companion = _write_draft(tmp_path, companion=None)
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_bad"
    assert "[FAIL] A9" in text
    assert "not found" in text


def test_a9_fires_when_the_companion_skips_a_worker(checker, tmp_path, capsys):
    """A companion that documents some nodes and quietly drops one still fails."""
    pipeline, companion = _write_draft(
        tmp_path, companion="# Authored pipeline\n\nThe work node and the critique node.\n"
    )
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_bad"
    assert "[FAIL] A9" in text
    assert "postmortem" in text


def test_a9_fires_on_an_empty_companion(checker, tmp_path, capsys):
    pipeline, companion = _write_draft(tmp_path, companion="   \n")
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_bad"
    assert "[FAIL] A9" in text
    assert "empty" in text


# ---------------------------------------------------------------------------
# A10 -- the hollow gate (issue #245)
#
# Construction B1, rebuilt here from the issue rather than copied from a run:
# a pipeline that is doctrinally sound in every respect EXCEPT that its evidence
# gate's answer is discarded.  Both tokens route into the exit, so the run ends
# green whether the tests passed or failed.  Against the checker as shipped in
# PR #239 this printed `doctrine_ok` with A1-A9 all green, and `attractor lint`
# had nothing to say about it either.
# ---------------------------------------------------------------------------

_B1_IGNORED_GATE = """
digraph b1_ignored_gate {
    graph [default_max_retries=1]

    start [shape=circle, label="START"]
    done  [shape=Msquare, label="DONE"]

    work [
        shape=box, label="Do the work",
        prompt="Make the change the brief asks for. Report success when the edit is written."
    ]

    // A REAL evidence gate: runs pytest for real, emits green/red on its last line.
    gate [
        shape=parallelogram, label="Test Gate", max_retries=0,
        tool_command="if python3 -m pytest -q >/tmp/b1probe/test.log 2>&1; then printf green; else printf red; fi"
    ]

    // Budget wall: counts iterations, routes exhaustion to a salvage path.
    budget [
        shape=parallelogram, label="Budget Wall", max_retries=0,
        tool_command="n=$(cat /tmp/b1probe/iter 2>/dev/null || echo 0); n=$((n+1)); echo $n > /tmp/b1probe/iter; if [ $n -gt 5 ]; then printf exhausted; else printf continue; fi"
    ]

    postmortem [
        shape=box, label="Postmortem",
        prompt="The budget is spent. Write an honest postmortem of what the loop tried and why it did not converge."
    ]

    escalate [
        shape=parallelogram, label="Escalate LOUD", max_retries=0,
        tool_command="echo 'budget exhausted -- escalating' >&2; exit 1"
    ]

    start -> work
    work  -> budget     [condition="outcome=success"]
    work  -> postmortem [condition="outcome=fail"]

    budget -> gate       [condition="context.tool.last_line=continue"]
    budget -> postmortem [condition="context.tool.last_line=exhausted"]

    // ---- THE DEFECT: both gate tokens route into the exit ----
    gate -> done [condition="context.tool.last_line=green"]
    gate -> done [condition="context.tool.last_line=red"]

    postmortem -> work     [condition="outcome=success"]
    postmortem -> escalate [condition="outcome=fail"]
}
"""

_B1_COMPANION = """# B1 probe

The `work` node makes the change the brief asks for. The `postmortem` node
writes an honest account of a loop that did not converge.
"""


def test_a10_catches_the_gate_whose_answer_is_discarded(checker, tmp_path, capsys):
    """The reproduction from issue #245, verbatim in shape.

    Everything A1-A9 asks for is present: a real command (A3), an exit that is
    unreachable without the gate (A4), a budget wall (A5), failure routes on
    every worker (A6), and no failure *outcome* anywhere near the exit (A8).
    The one thing missing is the only thing that matters -- somewhere for a red
    answer to go that is not the success door.
    """
    pipeline, companion = _write_draft(tmp_path, _B1_IGNORED_GATE, companion=_B1_COMPANION)
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_bad", text

    # A10 is the ONLY thing that fires -- if any sibling check also went red the
    # construction would be proving something other than the hole it was built
    # to prove.
    failed = [line for line in text.splitlines() if line.startswith("[FAIL]")]
    assert len(failed) == 1, text
    assert failed[0].startswith("[FAIL] A10"), text

    # The message names the gate, both tokens, and the shared target, in the
    # style of A7/A8 -- the contract is stated, not left to be guessed at.
    assert "gate:" in text
    assert "'green'" in text and "'red'" in text
    assert "the exit 'done'" in text


def test_a10_sees_through_a_relay_no_op(checker, tmp_path, capsys):
    """Putting a forwarding `diamond` in the middle launders nothing.

    A diamond with a single unconditional edge runs nothing and decides
    nothing, so entering it and leaving it is indistinguishable from taking the
    edge directly.  If A10 stopped at the first hop, the cheapest way past it
    would be to add two of these.
    """
    laundered = _B1_IGNORED_GATE.replace(
        '    gate -> done [condition="context.tool.last_line=green"]\n'
        '    gate -> done [condition="context.tool.last_line=red"]',
        '    relay_green [shape=diamond]\n'
        '    relay_red   [shape=diamond]\n'
        '    gate -> relay_green [condition="context.tool.last_line=green"]\n'
        '    gate -> relay_red   [condition="context.tool.last_line=red"]\n'
        "    relay_green -> done\n"
        "    relay_red   -> done",
    )
    assert laundered != _B1_IGNORED_GATE, "relay mutation anchor drifted"

    pipeline, companion = _write_draft(tmp_path, laundered, companion=_B1_COMPANION)
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_bad", text
    assert "[FAIL] A10" in text, text


def test_a10_admits_a_gate_whose_answers_go_to_different_places(checker, tmp_path, capsys):
    """The fix the failure message asks for, applied -- and it passes.

    A check whose only demonstrated behaviour is rejection has not been shown
    to be satisfiable. Routing `red` back into the corrective loop is exactly
    what the message tells the author to do, so it has to be enough.
    """
    repaired = _B1_IGNORED_GATE.replace(
        'gate -> done [condition="context.tool.last_line=red"]',
        'gate -> work [condition="context.tool.last_line=red"]',
    )
    assert repaired != _B1_IGNORED_GATE, "repair anchor drifted"

    pipeline, companion = _write_draft(tmp_path, repaired, companion=_B1_COMPANION)
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_ok", text
    assert "[PASS] A10" in text


def test_a10_admits_two_tokens_converging_on_an_ordinary_node(checker, tmp_path, capsys):
    """The deliberate boundary: only the EXIT fires A10.

    Sending several distinct diagnoses to one node that writes them up is a
    real pattern in this repo's own graphs -- there the token is *recorded*
    rather than routed on, which is legitimate. Only the exit makes the
    convergence a lie, because only there does the run end green either way.
    """
    converging = _B1_IGNORED_GATE.replace(
        'gate -> done [condition="context.tool.last_line=red"]',
        'gate -> postmortem [condition="context.tool.last_line=red"]',
    ).replace(
        'gate -> done [condition="context.tool.last_line=green"]',
        'gate -> done [condition="context.tool.last_line=green"]\n'
        '    gate -> postmortem [condition="context.tool.last_line=flaky"]',
    )

    pipeline, companion = _write_draft(tmp_path, converging, companion=_B1_COMPANION)
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_ok", text
    assert "[PASS] A10" in text


def test_a10_admits_a_re_gate_loop(checker, tmp_path, capsys):
    """A gate whose failing token re-enters the loop that ends at the same gate.

    Every token in a convergent attractor *eventually* reaches the exit -- that
    is what convergence means. A10 must not read that as inertness, so the rule
    is about where an answer LANDS, never about what it can eventually reach.
    """
    re_gated = _B1_IGNORED_GATE.replace(
        'gate -> done [condition="context.tool.last_line=red"]',
        'gate -> budget [condition="context.tool.last_line=red"]',
    )

    pipeline, companion = _write_draft(tmp_path, re_gated, companion=_B1_COMPANION)
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_ok", text
    assert "[PASS] A10" in text


def test_a10_ignores_an_inequality_because_it_is_not_an_answer(checker, tmp_path, capsys):
    """`last_line!=green` selects a complement, not a token.

    A10 reasons about which *answer* sends the run where. "Anything but green"
    is not an answer, and reading it as one would make the ordinary
    green-or-otherwise gate look like two tokens into the exit.
    """
    complement = _B1_IGNORED_GATE.replace(
        'gate -> done [condition="context.tool.last_line=red"]',
        'gate -> work [condition="context.tool.last_line!=green"]',
    )

    pipeline, companion = _write_draft(tmp_path, complement, companion=_B1_COMPANION)
    _, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert capsys.readouterr().out == "doctrine_ok", text
    assert "[PASS] A10" in text


# ---------------------------------------------------------------------------
# Fail closed: a gate that cannot read the artifact must REJECT it
# ---------------------------------------------------------------------------


def test_checker_fails_closed_when_the_pipeline_was_never_written(checker, tmp_path, capsys):
    report = tmp_path / "report.txt"
    rc = checker.main(
        [
            "--pipeline",
            str(tmp_path / "draft" / "pipeline.dot"),
            "--companion",
            str(tmp_path / "draft" / "pipeline.md"),
            "--report",
            str(report),
        ]
    )

    assert rc == 0, "an absent draft is a judgement about the author, not a crash"
    assert capsys.readouterr().out == "doctrine_bad"
    assert "[FAIL] A0" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("this is not a graph at all\n", id="prose"),
        pytest.param("digraph Broken { a -> b [label=\n", id="unterminated-attribute-list"),
        pytest.param("", id="empty-file"),
    ],
)
def test_checker_fails_closed_on_an_unparseable_pipeline(checker, tmp_path, capsys, body):
    """A graph this reader cannot understand is rejected, never admitted."""
    pipeline, companion = _write_draft(tmp_path, body)
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_bad"
    assert "[FAIL] A0" in text


# ---------------------------------------------------------------------------
# The stdlib DOT reader must agree with the engine's own parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shipped",
    [
        "examples/authoring/pipeline-author.dot",
        "examples/patterns/task-runner.dot",
        "examples/pipelines/practical/bug-fix.dot",
        "examples/objective/objective-runner.dot",
    ],
)
def test_minimal_parser_agrees_with_the_engine_on_shipped_graphs(checker, shipped):
    """The checker ships its own DOT reader; keep it honest against the real one.

    It is stdlib-only so the gate runs under whatever ``python3`` is on PATH in
    the target workspace (not the ``attractor`` CLI's own virtualenv). That
    freedom is only safe if the two parsers agree on node ids, edge count, and
    the attributes the checks actually read.
    """
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    text = (_REPO_ROOT / shipped).read_text(encoding="utf-8")
    engine_graph = parse_dot(text)
    mini_graph = checker.parse_dot_min(text)

    assert set(mini_graph.nodes) == set(engine_graph.nodes)
    assert len(mini_graph.edges) == len(engine_graph.edges)
    # The checks read tool_command, shape and goal_gate off nodes; a reader that
    # agreed on ids while losing an attribute would pass the two asserts above
    # and still gate on nothing.
    #
    # Compared after `str().lower()` normalisation, deliberately: the engine's
    # parser coerces boolean-valued attributes (`goal_gate=true` -> `True`)
    # while the stdlib reader keeps the source text and resolves truthiness at
    # use (`_is_truthy`).  That is a representation difference, not a
    # disagreement about the graph -- and normalising is what makes the
    # assertion test the thing it claims to.
    for node_id, engine_node in engine_graph.nodes.items():
        for key in ("shape", "tool_command", "goal_gate", "must_write", "retry_target"):
            engine_value = (engine_node.attrs or {}).get(key)
            if engine_value is None:
                continue
            assert str(mini_graph.attr(node_id, key)).lower() == str(engine_value).lower(), (
                f"{shipped}: {node_id}.{key} disagrees between the two parsers"
            )


# ---------------------------------------------------------------------------
# Calibration: the contract admits the repo's own battle-tested exemplars
#
# A gate that rejects `task-runner.dot` is a wrong gate, not a strict one -- the
# exemplar is shipped, documented and has run real work.  This is what keeps the
# checks honest against doctrine rather than against one author's taste.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shipped",
    [
        "examples/authoring/pipeline-author.dot",
        "examples/patterns/task-runner.dot",
        "examples/pipelines/practical/bug-fix.dot",
    ],
)
def test_shipped_exemplars_satisfy_the_authoring_contract(checker, tmp_path, capsys, shipped):
    dot_path = _REPO_ROOT / shipped
    companion = dot_path.with_suffix(".md")
    assert companion.is_file(), f"{shipped} has no paired guide"

    report = tmp_path / "report.txt"
    rc = checker.main(
        [
            "--pipeline",
            str(dot_path),
            "--companion",
            str(companion),
            "--report",
            str(report),
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_ok", report.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "shipped",
    [
        "examples/authoring/pipeline-author.dot",
        "examples/patterns/task-runner.dot",
        "examples/pipelines/practical/bug-fix.dot",
        "examples/drift-review/drift-review.dot",
        "examples/objective/objective-runner.dot",
    ],
)
def test_a10_admits_every_shipped_gate_bearing_graph(checker, tmp_path, capsys, shipped):
    """A10's calibration set, stated separately because it is wider than A1-A9's.

    ``drift-review.dot`` and ``objective-runner.dot`` do not carry a paired
    ``.md`` at the path A9 looks for, so they cannot be in the ``doctrine_ok``
    set above -- but they are dense, real, gate-heavy graphs and they are
    exactly where a mis-calibrated A10 would show up. Asserting the A10 line
    specifically keeps them in the calibration set without pretending they
    satisfy checks they do not.
    """
    dot_path = _REPO_ROOT / shipped
    if not dot_path.is_file():  # pragma: no cover - defensive
        pytest.skip(f"{shipped} not present")

    report = tmp_path / "report.txt"
    checker.main(
        [
            "--pipeline",
            str(dot_path),
            "--companion",
            str(dot_path.with_suffix(".md")),
            "--report",
            str(report),
        ]
    )
    capsys.readouterr()

    text = report.read_text(encoding="utf-8")
    assert "[PASS] A10" in text, text

    # And the graph really does exercise the rule: a graph with no evidence
    # gate at all would pass A10 vacuously and calibrate nothing.
    graph = checker.parse_dot_min(dot_path.read_text(encoding="utf-8"))
    assert checker.evidence_gates(graph), f"{shipped} has no evidence gate to calibrate against"


def test_the_authoring_attractor_obeys_the_contract_it_enforces(checker, tmp_path, capsys):
    """Self-application, stated as its own test because it is the load-bearing claim.

    ``pipeline-author.dot`` hands out A1-A9 to everything it writes.  An
    exemplar that could not satisfy its own contract would be teaching the
    anti-pattern by example -- which is exactly what this repo says it resists.
    """
    report = tmp_path / "report.txt"
    rc = checker.main(
        [
            "--pipeline",
            str(_AUTHORING_DIR / "pipeline-author.dot"),
            "--companion",
            str(_AUTHORING_DIR / "pipeline-author.md"),
            "--report",
            str(report),
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_ok", report.read_text(encoding="utf-8")


def test_the_minimal_teaching_graph_is_correctly_not_production_shaped(checker, tmp_path, capsys):
    """`00-convergence-loop.dot` is a teaching graph, and the gate says so.

    It carries the bowl and nothing else -- no budget wall, no failure route on
    its single worker -- which its own guide is explicit about.  Pinning that
    here documents where the contract's bar actually sits: at pipelines meant to
    be run on real work, not at the four-node illustration of the shape.
    """
    dot_path = _REPO_ROOT / "examples" / "pipelines" / "00-convergence-loop.dot"
    if not dot_path.is_file():  # pragma: no cover - defensive
        pytest.skip("00-convergence-loop.dot not present")

    report = tmp_path / "report.txt"
    checker.main(
        [
            "--pipeline",
            str(dot_path),
            "--companion",
            str(dot_path.with_suffix(".md")),
            "--report",
            str(report),
        ]
    )

    assert capsys.readouterr().out == "doctrine_bad"
    text = report.read_text(encoding="utf-8")
    assert "[FAIL] A5" in text  # no budget wall
    assert "[FAIL] A6" in text  # its single worker has no failure route
    assert "[PASS] A4" in text  # but its exit IS gated on evidence


# ---------------------------------------------------------------------------
# The substantive-command reader -- "is this gate checking, or just typing?"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "substantive"),
    [
        pytest.param("printf gate_pass", False, id="constant-emitter"),
        pytest.param("echo ok > f; printf green", False, id="echo-into-a-file"),
        pytest.param("mkdir -p .run && printf ready", False, id="arrangement-only"),
        pytest.param("printf escalated; exit 1", False, id="loud-terminal-is-not-a-gate"),
        pytest.param("[ -s report.md ] && printf ok || printf missing", True, id="file-predicate"),
        pytest.param("pytest -q > out.txt 2>&1 && printf green || printf red", True, id="test-suite"),
        pytest.param("attractor lint child.dot && printf pass", True, id="linter"),
        pytest.param(
            'n=$(cat .run/iter 2>/dev/null || echo 0); if [ "$n" -gt 3 ]; then printf exhausted; fi',
            True,
            id="counter-with-a-predicate",
        ),
    ],
)
def test_substantive_command_reader(checker, command, substantive):
    assert bool(checker.substantive_commands(command)) is substantive


def test_a_printf_only_gate_is_not_an_evidence_gate(checker):
    """The whole point of A3: a parallelogram is not a gate, a check is."""
    graph = checker.parse_dot_min(_GOOD_PIPELINE.replace(*_A3_HOLLOW_GATE, 1))
    assert checker.evidence_gates(graph) == []


# ---------------------------------------------------------------------------
# End-to-end: the REAL tool_command text, taken from the graph the engine runs
#
# These drive the shell the pipeline actually executes -- extracted from
# pipeline-author.dot by the engine's own parser -- rather than a paraphrase of
# it.  A property that only holds in a hand-written approximation of the gate is
# not a property of the pipeline.
# ---------------------------------------------------------------------------

_SHELL_GATES = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("python3")),
    reason="the shipped gate commands need bash and python3",
)


def _tool_command(node_id: str) -> str:
    """The verbatim tool_command of a node, read through the engine's parser."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot((_AUTHORING_DIR / "pipeline-author.dot").read_text(encoding="utf-8"))
    return graph.nodes[node_id].attrs["tool_command"]


def _run_gate(node_id: str, workspace: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "authoring_dir": str(_AUTHORING_DIR),
        "target_dir": str(workspace),
        "max_iterations": "4",
        "max_frames": "2",
        **env_overrides,
    }
    return subprocess.run(
        _tool_command(node_id),
        shell=True,
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _authoring_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".authoring" / "draft").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".authoring" / "postmortem").mkdir(parents=True, exist_ok=True)
    return tmp_path


@_SHELL_GATES
def test_real_doctrine_gate_command_blocks_a_gateless_draft(tmp_path):
    """The graph's own doctrine_gate shell, on a draft whose exit is ungated."""
    workspace = _authoring_workspace(tmp_path)
    (workspace / ".authoring" / "draft" / "pipeline.dot").write_text(
        _GOOD_PIPELINE.replace(*_A4_BYPASS, 1), encoding="utf-8"
    )
    (workspace / ".authoring" / "draft" / "pipeline.md").write_text(
        _GOOD_COMPANION, encoding="utf-8"
    )

    result = _run_gate("doctrine_gate", workspace)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "doctrine_bad"
    assert "[FAIL] A4" in (workspace / ".authoring" / "doctrine-report.txt").read_text(
        encoding="utf-8"
    )


@_SHELL_GATES
def test_real_doctrine_gate_command_admits_a_conforming_draft(tmp_path):
    workspace = _authoring_workspace(tmp_path)
    (workspace / ".authoring" / "draft" / "pipeline.dot").write_text(
        _GOOD_PIPELINE, encoding="utf-8"
    )
    (workspace / ".authoring" / "draft" / "pipeline.md").write_text(
        _GOOD_COMPANION, encoding="utf-8"
    )

    result = _run_gate("doctrine_gate", workspace)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "doctrine_ok"
    assert (workspace / ".authoring" / "convergence.jsonl").read_text(encoding="utf-8").strip()


@_SHELL_GATES
@pytest.mark.parametrize(
    ("critique", "expected"),
    [
        pytest.param("findings\n\nVERDICT: SHIP\n", "ship", id="ship"),
        pytest.param("findings\n\nVERDICT: ITERATE\n", "iterate", id="iterate"),
        pytest.param("findings\n\nverdict: ship\n", "ship", id="ship-lowercase"),
        pytest.param(
            "End with a final line reading exactly 'VERDICT: SHIP' or 'VERDICT: ITERATE'.\n"
            "The gate looks fine to me.\n",
            "noverdict",
            id="quoted-instructions-must-not-false-ship",
        ),
        pytest.param("VERDICT: SHIP\n\nand then some more prose\n", "noverdict", id="verdict-not-last"),
        pytest.param("", "noverdict", id="empty-critique"),
        pytest.param(None, "noverdict", id="critique-never-written"),
    ],
)
def test_real_verdict_gate_command_fails_closed(tmp_path, critique, expected):
    """The verdict gate ships only on a last-line, exact, anchored SHIP.

    The quoted-instructions case is a real observed false-SHIP in this repo: a
    critique that reproduces its own instructions contains both keywords, and a
    bare ``grep -qi SHIP`` matches the instruction text.  ``tail -n 1`` plus
    ``grep -qix`` is immune.
    """
    workspace = _authoring_workspace(tmp_path)
    if critique is not None:
        (workspace / ".authoring" / "critique.md").write_text(critique, encoding="utf-8")

    result = _run_gate("verdict_gate", workspace)

    assert result.stdout == expected
    # Idiom B: only `ship` exits 0, so a red verdict is a genuine FAIL the
    # engine's exit-time goal-gate check can see.
    assert (result.returncode == 0) is (expected == "ship")
    record = (workspace / ".authoring" / "convergence.jsonl").read_text(encoding="utf-8")
    assert f'"verdict": "{expected}"' in record


@_SHELL_GATES
def test_real_triage_gate_command_routes_on_the_anchored_verdict(tmp_path):
    workspace = _authoring_workspace(tmp_path)
    triage = workspace / ".authoring" / "triage.md"

    triage.write_text("## Brief\n...\n\nVERDICT: ATTRACTOR\n", encoding="utf-8")
    assert _run_gate("triage_gate", workspace).stdout == "attractor"

    triage.write_text("## Brief\n...\n\nVERDICT: REDIRECT\n", encoding="utf-8")
    assert _run_gate("triage_gate", workspace).stdout == "redirect"


@_SHELL_GATES
def test_real_triage_gate_fuse_stops_reframing_forever(tmp_path):
    """An unreadable triage is corrective -- but only up to max_frames."""
    workspace = _authoring_workspace(tmp_path)
    (workspace / ".authoring" / "triage.md").write_text("no verdict line here\n", encoding="utf-8")

    seen = [_run_gate("triage_gate", workspace, max_frames="2").stdout for _ in range(4)]

    assert seen[:2] == ["triage_bad", "triage_bad"]
    assert seen[2:] == ["triage_exhausted", "triage_exhausted"]


@_SHELL_GATES
def test_real_lint_gate_owns_the_one_counter_that_spans_every_leg(tmp_path):
    """The budget is spent on ENTRY, by every corrective leg, in one ledger.

    'Individually-bounded legs do not compose' -- so the wall lives at the node
    every leg re-enters through, and is checked before the linter runs.
    """
    if not shutil.which("attractor"):
        pytest.skip("the attractor CLI is not on PATH")
    workspace = _authoring_workspace(tmp_path)
    (workspace / ".authoring" / "draft" / "pipeline.dot").write_text(
        _GOOD_PIPELINE, encoding="utf-8"
    )

    seen = [_run_gate("lint_gate", workspace, max_iterations="2").stdout for _ in range(3)]

    assert seen == ["lint_pass", "lint_pass", "exhausted"]
    assert (workspace / ".authoring" / "iter").read_text(encoding="utf-8").strip() == "3"


@_SHELL_GATES
def test_real_finalize_refuses_to_open_the_exit_without_a_disposition(tmp_path):
    workspace = _authoring_workspace(tmp_path)

    empty = _run_gate("finalize", workspace)
    assert empty.returncode != 0
    assert empty.stdout == "no_disposition"

    (workspace / ".authoring" / "redirect.md").write_text("the honest no\n", encoding="utf-8")
    redirected = _run_gate("finalize", workspace)
    assert redirected.returncode == 0
    assert redirected.stdout == "finalized"
    assert (workspace / ".authoring" / "disposition").read_text(encoding="utf-8").strip() == "redirected"

    (workspace / ".authoring" / "published").write_text("out/x.dot out/x.md\n", encoding="utf-8")
    authored = _run_gate("finalize", workspace)
    assert authored.stdout == "finalized"
    assert (workspace / ".authoring" / "disposition").read_text(encoding="utf-8").strip() == "authored"


def _path_without(tool: str) -> str:
    """The caller's PATH with every directory containing *tool* removed.

    Deterministic in both directions: on a machine where the tool is installed
    this actually hides it, and on one where it was never installed (CI) the
    PATH comes back unchanged and the assertion still holds.
    """
    kept = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not (Path(d) / tool).exists()
    ]
    return os.pathsep.join(kept)


@_SHELL_GATES
def test_real_preflight_refuses_loudly_when_a_required_tool_is_missing(tmp_path):
    """Preflight's whole purpose: refuse before an LLM is ever paid.

    `attractor` absent means `lint_gate` could never run, so there is no honest
    way to author anything -- and finding that out at preflight costs nothing,
    while finding it out at `lint_gate` costs the author node's whole call. The
    refusal has to NAME the missing tool: "blocked" with no reason sends the
    operator to read a graph instead of their PATH.
    """
    workspace = _authoring_workspace(tmp_path)

    result = _run_gate(
        "preflight", workspace, pipeline_name="doc-claims-verified", PATH=_path_without("attractor")
    )

    assert result.returncode != 0
    assert result.stdout == "blocked"
    assert "attractor" in result.stderr
    assert "PATH" in result.stderr
    # And it refused BEFORE recording anything: a blocked run leaves no admitted
    # name behind for a later node to read.
    assert not (workspace / ".authoring" / "name").exists()


@pytest.mark.skipif(
    not (shutil.which("attractor") and shutil.which("sha256sum")),
    reason=(
        "preflight checks tool availability before it validates the publish name, so this "
        "assertion needs the tools it checks for actually present -- the missing-tool branch "
        "is covered unconditionally by the test above"
    ),
)
@_SHELL_GATES
def test_real_preflight_refuses_an_unsafe_publish_name(tmp_path):
    """`pipeline_name` becomes a path under out/; preflight is the last free refusal."""
    workspace = _authoring_workspace(tmp_path)

    bad = _run_gate("preflight", workspace, pipeline_name="../../etc/passwd")
    assert bad.returncode != 0
    assert bad.stdout == "blocked"
    assert "pipeline_name" in bad.stderr

    good = _run_gate("preflight", workspace, pipeline_name="doc-claims-verified")
    assert good.returncode == 0
    assert good.stdout == "ready"
    assert (workspace / ".authoring" / "name").read_text(encoding="utf-8").strip() == "doc-claims-verified"


# ---------------------------------------------------------------------------
# Issue #252 -- the dead-end designed terminal, and its goal-gate corollary
#
# The engine's MAIN loop has no designed-terminus concept.  `run_subgraph()`
# distinguishes "no outgoing edges at all" (a designed terminus) from a
# conditional-mismatch dead end; `run()` does NOT -- it reports
# `error_type=no_matching_edge` as a PIPELINE_ERROR whatever the exit status.
# So `escalated` -- a tool node that exits 1 on purpose -- was reported as an
# authoring bug when it was reached.  Measured on the shipped CLI against this
# very file, with a blocked preflight:
#
#     [PIPELINE] X Error at escalated (no_matching_edge): Command exited with
#     code 1: escalated
#     notes: No matching edge from node 'escalated'
#
# These read the shipped graph through the engine's own parser, not a
# paraphrase of it.
# ---------------------------------------------------------------------------


def _author_graph():
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    return parse_dot((_AUTHORING_DIR / "pipeline-author.dot").read_text(encoding="utf-8"))


def test_escalated_routes_to_the_exit_instead_of_dead_ending():
    """A loud terminal must ROUTE; the main loop has no designed terminus.

    One edge -- `escalated -> done [outcome=fail]` -- is the convergence-factory
    idiom proven in #248.  `_check_goal_gates` then returns the LAST COMPLETED
    node's outcome, so `escalated`'s own nonzero exit becomes the run's
    status=fail / CLI exit 1, with no routing error.
    """
    graph = _author_graph()
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
    """The routing fix must not turn the loud terminal into a quiet one.

    An `escalated` that exited 0 would convert the escalation into a green run
    -- the exact hazard TOPO-006 names -- because the exit returns the last
    completed node's outcome.
    """
    node = _author_graph().nodes["escalated"]
    command = str(node.attrs["tool_command"])
    assert command.rstrip().endswith("exit 1"), command
    assert str(node.attrs.get("max_retries")) == "0"
    assert ".authoring/disposition" in command, "the terminal must say which terminal it was"


def test_no_node_in_the_authoring_graph_dead_ends():
    """The whole-graph form of the rule, so a future terminal cannot regress."""
    graph = _author_graph()
    exits = {n.id for n in graph.nodes.values() if n.is_exit_node()}
    dead_ends = [n for n in graph.nodes if n not in exits and not graph.outgoing_edges(n)]
    assert dead_ends == [], dead_ends


def test_the_goal_gate_carries_no_retry_target():
    """#252's corollary, which #252 does not mention and #248 discovered.

    `retry_target` on a goal gate is consulted in exactly one place --
    `_check_goal_gates()` at the exit node.  While `escalated` dead-ended, the
    exit was unreachable with `verdict_gate` red, so the attribute was dead;
    `escalated -> done` makes it reachable, which turns it live and wrong -- an
    escalation that has already written its postmortem and its handoff would be
    sent back to `author` to be paid for again, with `completed_nodes` cleared.
    Measured on the shipped engine with a faithful reduction of this shape: 51
    executions of the retry target before the step cap, versus one without.

    The corrective loop is untouched: it is the `loop_restart` back-edge to
    `author`, which is also what keeps `goal_gate_has_retry` satisfied.
    """
    graph = _author_graph()
    gate = graph.nodes["verdict_gate"]
    assert str(gate.attrs.get("goal_gate", "")).lower() == "true"
    assert not gate.attrs.get("retry_target"), gate.attrs.get("retry_target")
    assert not gate.attrs.get("fallback_retry_target")
    assert "retry_target" not in graph.graph_attrs

    restarts = [
        e
        for e in graph.outgoing_edges("verdict_gate")
        if str(e.loop_restart).lower() == "true" and e.to_node == "author"
    ]
    assert restarts, "the corrective loop must survive removing the exit-time retry"


# ---------------------------------------------------------------------------
# A8's one exemption: a DESIGNED LOUD TERMINAL (issue #252)
#
# A8 blocked `escalated -> done [outcome=fail]` -- the shape #248 merged into
# `examples/objective/objective-runner.dot`, and the ONLY shape in which a
# deliberately red terminal can exist on this engine at all (the main loop has
# no designed-terminus concept, so a dead-ended terminal is reported as
# PIPELINE_ERROR error_type=no_matching_edge, not as a loud red).  A gate that
# rejects the repo's own merged flagship is a wrong gate, not a strict one --
# the same calibration rule this file already applies to `task-runner.dot`.
#
# The exemption is narrow on purpose, and every clause below is held RED by a
# mutation: a recorder that exits 0, a node with somewhere else to go, a
# retried node, and an LLM worker all still block.
# ---------------------------------------------------------------------------

#: Route the fixture's existing loud terminal into the exit -- the #248 shape.
_LOUD_TERMINAL_TO_EXIT = (
    '    postmortem -> escalated [condition="outcome=fail"]',
    (
        '    postmortem -> escalated [condition="outcome=fail"]\n'
        '    escalated -> done [condition="outcome=fail"]'
    ),
)


def _with_loud_terminal(*more: tuple[str, str]) -> str:
    text = _GOOD_PIPELINE.replace(*_LOUD_TERMINAL_TO_EXIT, 1)
    for old, new in more:
        assert old in text, f"mutation anchor drifted: {old!r}"
        text = text.replace(old, new, 1)
    return text


def test_a8_admits_a_designed_loud_terminal(checker, tmp_path, capsys):
    """The whole point: routing a terminal's own FAIL into the exit is legal.

    At the exit node, with every goal gate satisfied, `_check_goal_gates()`
    returns `node_outcomes[completed_nodes[-1]]` -- the LAST COMPLETED node's
    outcome.  `escalated` is that node and it exits 1, so the run ends
    status=fail / CLI exit 1.  Nothing is converted into a successful run,
    which is the only thing A8 was ever protecting.
    """
    pipeline, companion = _write_draft(tmp_path, _with_loud_terminal())
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_ok", text


@pytest.mark.parametrize(
    ("mutation", "why"),
    [
        pytest.param(
            ('escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]',
             'escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated"]'),
            "a terminal that can exit 0 hands the exit a SUCCESS -- the escalation goes green",
            id="terminal-that-exits-zero",
        ),
        pytest.param(
            ('    escalated -> done [condition="outcome=fail"]',
             '    escalated -> done [condition="outcome=fail"]\n    escalated -> work [condition="outcome=success"]'),
            "a node with somewhere else to go is a step on a path, not a terminal",
            id="terminal-with-a-second-route",
        ),
        pytest.param(
            ('escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]',
             'escalated [shape=parallelogram, max_retries=2, tool_command="printf escalated; exit 1"]'),
            "the failure is the point, not a flake to retry",
            id="terminal-that-is-retried",
        ),
        pytest.param(
            ('escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]',
             'escalated [shape=box, max_retries=0, prompt="Escalate this to a human, loudly."]'),
            "a worker's failure is a provider verdict, not a guaranteed process exit",
            id="terminal-that-is-an-llm-worker",
        ),
    ],
)
def test_a8_still_blocks_everything_that_is_not_one(checker, tmp_path, capsys, mutation, why):
    """The exemption must not be wearable as a costume by the A8 hazard itself."""
    pipeline, companion = _write_draft(tmp_path, _with_loud_terminal(mutation))
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_bad", why
    assert "[FAIL] A8" in text, (why, text)


def test_a4_is_not_bypassed_by_a_path_that_can_only_end_red(checker, tmp_path, capsys):
    """A4's companion correction: a loud terminal is not an evidence-free finish.

    A4 asks whether the run can FINISH WITHOUT EVIDENCE.  A path that reaches
    the exit only through a node that exits nonzero cannot finish at all in
    that sense -- it fails, loudly.  Counting it as a bypass would leave an
    author no legal way to fail loudly: the only shape A8 admits would be the
    shape A4 forbids.  The green door is untouched, which is asserted by the
    A4 mutation in the table above still going red.
    """
    pipeline, companion = _write_draft(tmp_path, _with_loud_terminal())
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == "doctrine_ok", text
    assert "[PASS] A4" in text, text


#: The reviewer's laundering shape (PR #259 adversarial review): a node whose
#: command only TEXTUALLY ends in `exit 1` -- `rm -f scratch.tmp || exit 1` exits
#: 0 whenever the `rm` succeeds, which is almost always -- wired to the exit off
#: an ungated fast path.  Clauses 1-4 all match it on the text of the graph, so
#: without clause 5 A4 blocks it as a "loud terminal" and reports the exit as
#: unreachable-without-evidence.  The reviewer ran this shape on the live engine:
#: it finished `status=success`, CLI exit 0 -- an evidence-free GREEN finish that
#: main's A4 catches and the un-hardened exemption laundered.
_LAUNDERING_SNEAK = (
    '    escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]',
    (
        '    escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]\n'
        '    sneak [shape=parallelogram, max_retries=0, tool_command="rm -f scratch.tmp || exit 1"]'
    ),
)


def _sneak_wired(edge: str) -> tuple[str, str]:
    """Put `sneak` on the ungated fast path, reaching the exit via `edge`."""
    return ("    work -> dod_gate", f"    work -> dod_gate\n    work -> sneak\n{edge}")


@pytest.mark.parametrize(
    ("edge", "verdict", "a4", "why"),
    [
        pytest.param(
            "    sneak -> done",
            "doctrine_bad",
            "[FAIL] A4",
            "an UNCONDITIONAL edge into the exit can carry the node's SUCCESS -- "
            "`rm -f scratch.tmp || exit 1` exits 0, so this is an evidence-free green finish",
            id="unconditional-edge-is-laundering",
        ),
        pytest.param(
            '    sneak -> done [condition="outcome=fail"]',
            "doctrine_ok",
            "[PASS] A4",
            "an outcome=fail edge can never carry a SUCCESS into the exit, whatever "
            "the command does -- this is the shipped exemplars' own shape",
            id="outcome-fail-edge-stays-exempt",
        ),
    ],
)
def test_a4_exempts_a_loud_terminal_only_when_its_edge_is_conditioned_on_failure(
    checker, tmp_path, capsys, edge, verdict, a4, why
):
    """Clause 5: the exemption is RUNTIME-sound, not merely textual.

    These two cases differ in exactly ONE character sequence -- the condition on
    `sneak -> done`.  The node, its command, its `max_retries=0` and its single
    outgoing edge are identical, and clauses 1-4 therefore cannot tell them
    apart: clause 2's regex matches `|| exit 1` on the text while the command
    exits 0 at runtime.  Only the edge's condition distinguishes a terminal
    whose FAIL becomes the run's status from a fast path that quietly finishes
    green without ever touching a gate.
    """
    dot = _with_loud_terminal(_LAUNDERING_SNEAK, _sneak_wired(edge))
    pipeline, companion = _write_draft(tmp_path, dot)
    rc, text = _run_checker(checker, pipeline, companion, tmp_path / "report.txt")

    assert rc == 0
    assert capsys.readouterr().out == verdict, (why, text)
    assert a4 in text, (why, text)


def test_a8_admits_the_shape_248_merged_into_the_objective_runner(checker, tmp_path, capsys):
    """Calibration, stated against the real file rather than a fixture.

    `objective-runner.dot` is the repo's own merged, reviewed exemplar of this
    idiom.  A8 rejected it -- so A8 was mis-calibrated, and this is the
    assertion that keeps it calibrated.  (A9 is evaluated separately for that
    file; this test is about A8 alone.)
    """
    dot_path = _REPO_ROOT / "examples" / "objective" / "objective-runner.dot"
    if not dot_path.is_file():
        pytest.skip("examples/objective/ not present")

    report = tmp_path / "report.txt"
    checker.main(
        [
            "--pipeline",
            str(dot_path),
            "--companion",
            str(dot_path.with_suffix(".md")),
            "--report",
            str(report),
        ]
    )
    capsys.readouterr()
    text = report.read_text(encoding="utf-8")
    assert "[PASS] A8" in text, text
    assert "[PASS] A4" in text, text
