"""Guards for the two exemplars whose loud terminals had no test home.

``examples/pipelines/practical/bug-fix.dot`` and
``examples/patterns/task-runner.dot`` are shipped, copy-me exemplars, and both
carried the dead-end designed-terminal shape that #248 removed from
``examples/objective/objective-runner.dot``:

  * ``bug-fix.dot``   -- ``escalated``
  * ``task-runner.dot`` -- ``bad_input`` AND ``abandon``

The engine's MAIN loop has no designed-terminus concept.  ``run_subgraph()``
distinguishes "no outgoing edges at all" (a designed terminus) from a
conditional-mismatch dead end; ``run()`` does NOT -- it reports
``no_matching_edge`` as a PIPELINE_ERROR whatever the node's exit status.  So
each of these deliberately-loud terminals *errored as unroutable* when it was
reached, and the author's intent ("exit red, loudly, having salvaged a
postmortem") was delivered to the operator as "the pipeline is broken".
Measured on the shipped CLI before the fix::

    [PIPELINE] X Error at bad_input (no_matching_edge): ...
    notes: No matching edge from node 'bad_input'

The second half of this file holds ``bug-fix.dot``'s ``test_gate`` -- which
used to run a bare workspace-root ``pytest -q`` -- to the bar an evidence gate
has to clear: it must find the tests in a nested-package workspace, and it must
not report "there is nothing here to run" as an ordinary RED that the fixer is
then sent to grind against.  That grind is what actually failed in the #243
incident.

Every structural assertion reads the shipped graph through the ENGINE'S OWN
PARSER, and every behavioural assertion drives the graph's REAL ``tool_command``
text -- not a paraphrase of either.  A property that only holds in a
hand-written approximation of the gate is not a property of the pipeline.

The examples tree lives at the repository root, outside the installed package,
so these tests run against a source checkout and skip gracefully when the
examples directory is absent (e.g. an installed-package test run) -- the same
pattern as ``test_examples_lint_clean.py`` and ``test_objective_layer_gates.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.graph import Graph

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES = _REPO_ROOT / "examples"
_BUG_FIX = _EXAMPLES / "pipelines" / "practical" / "bug-fix.dot"
_TASK_RUNNER = _EXAMPLES / "patterns" / "task-runner.dot"

pytestmark = pytest.mark.skipif(
    not _EXAMPLES.is_dir(),
    reason="examples/ not present (installed-package run)",
)


def _graph(path: Path) -> Graph:
    return parse_dot(path.read_text(encoding="utf-8"))


def _exit_id(graph: Graph) -> str:
    exits = [n.id for n in graph.nodes.values() if n.is_exit_node()]
    assert len(exits) == 1, f"validate_or_raise requires exactly one exit node, got {exits}"
    return exits[0]


# ---------------------------------------------------------------------------
# The routing fix: a designed loud terminal must ROUTE, it cannot dead-end.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dot_path", "terminal"),
    [
        (_BUG_FIX, "escalated"),
        (_TASK_RUNNER, "bad_input"),
        (_TASK_RUNNER, "abandon"),
    ],
    ids=["bug-fix:escalated", "task-runner:bad_input", "task-runner:abandon"],
)
def test_the_loud_terminal_routes_to_the_exit_instead_of_dead_ending(dot_path, terminal):
    """Issue #252: the main loop has no designed-terminus concept.

    ``run_subgraph()`` gives the subgraph path an explicit "no outgoing edges at
    all is a designed terminus" branch; ``run()`` has no such branch and reports
    ``error_type=no_matching_edge`` regardless of exit status.  A dead-ended
    terminal therefore surfaces the graph's most important honest outcome in the
    vocabulary of an authoring bug.  One edge per terminal --
    ``<terminal> -> <exit> [outcome=fail]`` -- is the convergence-factory idiom
    proven in #248.
    """
    graph = _graph(dot_path)
    exit_id = _exit_id(graph)

    outgoing = graph.outgoing_edges(terminal)
    assert outgoing, (
        f"'{terminal}' has no outgoing edge, so the engine reports the designed "
        f"terminal as PIPELINE_ERROR error_type=no_matching_edge (issue #252)"
    )
    assert [e.to_node for e in outgoing] == [exit_id]
    assert "outcome=fail" in (outgoing[0].condition or ""), outgoing[0].condition


@pytest.mark.parametrize(
    ("dot_path", "terminal"),
    [
        (_BUG_FIX, "escalated"),
        (_TASK_RUNNER, "bad_input"),
        (_TASK_RUNNER, "abandon"),
    ],
    ids=["bug-fix:escalated", "task-runner:bad_input", "task-runner:abandon"],
)
def test_the_routed_terminal_is_still_red(dot_path, terminal):
    """The routing fix must not turn a loud terminal into a quiet one.

    ``<terminal> -> <exit> [outcome=fail]`` is only honest because the
    terminal's own FAIL is what ``_check_goal_gates`` returns from the exit.  A
    terminal that exited 0 would convert the escalation into a GREEN run -- the
    exact hazard TOPO-006 names -- and ``max_retries`` above 0 would let the
    engine re-run a node whose failure is the point.
    """
    node = _graph(dot_path).nodes[terminal]
    command = str(node.attrs["tool_command"])
    assert command.rstrip().endswith("exit 1"), command
    assert str(node.attrs.get("max_retries")) == "0", node.attrs.get("max_retries")


@pytest.mark.parametrize(
    "dot_path",
    [_BUG_FIX, _TASK_RUNNER],
    ids=["bug-fix", "task-runner"],
)
def test_no_node_in_these_graphs_dead_ends(dot_path):
    """The whole-graph form of the same rule -- a future terminal cannot regress.

    Anything that is not the exit node and has no outgoing edge is, on this
    engine, an unroutable node waiting to be reached.
    """
    graph = _graph(dot_path)
    exit_id = _exit_id(graph)
    dead_ends = [
        node_id
        for node_id in graph.nodes
        if node_id != exit_id and not graph.outgoing_edges(node_id)
    ]
    assert dead_ends == [], (
        f"{dot_path.name}: nodes with no outgoing edge report no_matching_edge "
        f"when reached: {dead_ends}"
    )


def test_task_runners_exit_time_retry_can_no_longer_overrule_an_abandonment():
    """#252's corollary, which #252 itself does not mention (#248 found it).

    ``retry_target`` on a goal gate is consulted in exactly ONE place --
    ``_check_goal_gates()`` at the exit node -- and the graph-level attribute is
    that same mechanism's fallback (spec 3.4; it is deliberately NOT consulted
    on per-node failure, spec 3.7, ``_resolve_failure_retry_target``).  It never
    was a corrective loop.  The one question it answers is "the run is trying to
    LEAVE with a goal gate unsatisfied -- where does it go instead?"

    While ``abandon`` dead-ended, the exit was unreachable with a gate
    unsatisfied, so the answer never mattered.  ``abandon -> done`` makes it
    matter, and ``attempt`` became the WRONG answer: ``abandon`` is reached with
    ``verify`` red (verify -> triage -> postmortem) or ``verdict`` red, so an
    abandonment a HUMAN had just chosen would be overruled and the maker paid
    for again, with ``completed_nodes`` cleared.  Measured on the shipped engine
    with a faithful reduction of this shape::

        retry_target="attempt"  -> attempt/verify/postmortem/abandon x51 each
        retry_target="abandon"  -> 1 / 1 / 1 / 2

    So: no node-level value (it would take precedence and re-open the hazard),
    and the graph-level answer is the abandonment terminal.
    """
    graph = _graph(_TASK_RUNNER)

    gates = [n for n in graph.nodes.values() if str(n.attrs.get("goal_gate", "")).lower() == "true"]
    assert {n.id for n in gates} == {"verify", "verdict"}, [n.id for n in gates]
    for gate in gates:
        assert not gate.attrs.get("retry_target"), gate.id
        assert not gate.attrs.get("fallback_retry_target"), gate.id

    # Whatever the graph-level answer is, it must not be a maker: a run that a
    # human has just abandoned may not be sent back to do more work.
    exit_time_retry = graph.graph_attrs.get("retry_target") or graph.graph_attrs.get(
        "fallback_retry_target"
    )
    assert exit_time_retry in (None, "abandon"), exit_time_retry
    if exit_time_retry is not None:
        target = graph.nodes[exit_time_retry]
        assert str(target.attrs["tool_command"]).rstrip().endswith("exit 1"), target.id

    # The corrective loops are unchanged -- they are the graph's own edges,
    # which is where iteration was always actually happening.
    pairs = {(e.from_node, e.to_node) for e in graph.edges}
    assert ("triage", "attempt") in pairs
    assert ("feedback", "attempt") in pairs


# ---------------------------------------------------------------------------
# bug-fix.dot's test_gate: find the tests, and never grind against a gate that
# cannot go green.  These drive the REAL tool_command text.
# ---------------------------------------------------------------------------

_SHELL_GATE = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("pytest")),
    reason="the shipped test_gate command needs bash and pytest on PATH",
)

_NESTED_PYPROJECT = """[project]
name = "mypkg"
version = "0.1.0"

[tool.pytest.ini_options]
pythonpath = ["src"]
"""

_PASSING_TEST = "def test_ok():\n    assert True\n"
_IMPORTING_TEST = "from mypkg import VALUE\n\n\ndef test_value():\n    assert VALUE == 42\n"


def _test_gate_command() -> str:
    """The verbatim test_gate tool_command, read through the engine's parser."""
    return str(_graph(_BUG_FIX).nodes["test_gate"].attrs["tool_command"])


def _run_test_gate(workspace: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _test_gate_command(),
        shell=True,
        cwd=workspace,
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        check=False,
    )


def _flat_workspace(root: Path) -> Path:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_flat.py").write_text(_PASSING_TEST, encoding="utf-8")
    return root


def _nested_workspace(root: Path) -> Path:
    """A package one level down, with its own pytest rootdir -- the #243 shape.

    ``pytest -q`` from ``root`` collects ``mypkg/tests/test_value.py`` and then
    dies importing ``mypkg``, because the ini options that make the import work
    live in ``mypkg/pyproject.toml`` and a root-level run never reads them.
    """
    pkg = root / "mypkg"
    (pkg / "src" / "mypkg").mkdir(parents=True, exist_ok=True)
    (pkg / "tests").mkdir(parents=True, exist_ok=True)
    (pkg / "pyproject.toml").write_text(_NESTED_PYPROJECT, encoding="utf-8")
    (pkg / "src" / "mypkg" / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    (pkg / "tests" / "test_value.py").write_text(_IMPORTING_TEST, encoding="utf-8")
    return root


@_SHELL_GATE
def test_the_old_hardcoded_gate_could_not_pass_a_nested_workspace(tmp_path):
    """The defect itself, held in place so the fix cannot be undone silently.

    This is the shipped-before command, verbatim.  It is not "strict" -- it is
    UNPASSABLE for this layout, and the branch it lands in reports the same
    token as a genuinely failing test, which is what sent the #243 lane child
    to its budget wall.
    """
    workspace = _nested_workspace(tmp_path)
    before = subprocess.run(
        "mkdir -p .ai; pytest -q > .ai/test.log 2>&1; [ $? -eq 0 ] && printf pass || printf fail",
        shell=True,
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.stdout == "fail", before.stdout


@_SHELL_GATE
def test_the_gate_finds_the_tests_in_a_flat_workspace(tmp_path):
    """Discovery must not regress the ordinary case: root wins when root works."""
    workspace = _flat_workspace(tmp_path)

    result = _run_test_gate(workspace)

    assert result.stdout == "pass", (result.stdout, result.stderr)
    assert result.returncode == 0
    assert (workspace / ".ai" / "test-target").read_text(encoding="utf-8").strip() == "pytest -q"


@_SHELL_GATE
def test_the_gate_finds_the_tests_in_a_nested_workspace(tmp_path):
    """Issue #252 / #243: the workspace-root assumption is what actually broke.

    The gate now asks pytest itself where the tests are, so the same graph that
    could never go green on this layout goes green on the first entry.
    """
    workspace = _nested_workspace(tmp_path)

    result = _run_test_gate(workspace)

    assert result.stdout == "pass", (result.stdout, result.stderr)
    target = (workspace / ".ai" / "test-target").read_text(encoding="utf-8").strip()
    assert "mypkg" in target, target
    # The receipt: the log opens by naming what was actually run, so "the gate
    # tested something other than what you think" is visible on iteration 1.
    assert (workspace / ".ai" / "test.log").read_text(encoding="utf-8").startswith(f"RAN: {target}")


@_SHELL_GATE
def test_a_gate_with_nothing_to_run_is_not_reported_as_a_red_test(tmp_path):
    """"Nothing collected" is a broken instrument, not a fixable failure.

    pytest exits 5 for "no tests collected".  The old gate folded that into
    ``fail`` and sent the fixer round the loop again; no patch can make a
    non-existent suite green, so the run burned its whole budget.  The gate now
    says ``no_target`` and the graph routes that to the decision point.
    """
    workspace = tmp_path
    (workspace / "src.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = _run_test_gate(workspace)

    assert result.stdout == "no_target", (result.stdout, result.stderr)
    assert result.returncode == 0, "the token carries the verdict; the gate itself ran fine"
    # The refusal has to be actionable, not just loud.
    assert "test_command" in result.stderr
    assert not (workspace / ".ai" / "test-target").exists()


@_SHELL_GATE
def test_an_explicit_test_command_is_run_as_given(tmp_path):
    """The escape hatch has to survive being a COMPOUND command.

    ``bash -c "$tc"`` hands the parameter to a shell verbatim, so ``cd <pkg> &&
    ...``, an env prefix, or an ``&&`` chain runs exactly as passed.  A gate
    that silently rewrote the operator's command would be a worse bug than the
    one being fixed.
    """
    workspace = _nested_workspace(tmp_path)

    result = _run_test_gate(workspace, test_command="cd mypkg && pytest -q tests/test_value.py")

    assert result.stdout == "pass", (result.stdout, result.stderr)
    log = (workspace / ".ai" / "test.log").read_text(encoding="utf-8")
    assert log.startswith("RAN: cd mypkg && pytest -q tests/test_value.py"), log[:200]


@_SHELL_GATE
def test_a_genuinely_failing_suite_is_still_an_ordinary_red(tmp_path):
    """The distinction only means something if a real red still routes to triage."""
    workspace = _flat_workspace(tmp_path)
    (workspace / "tests" / "test_flat.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")

    result = _run_test_gate(workspace)

    assert result.stdout == "fail", (result.stdout, result.stderr)


@_SHELL_GATE
def test_the_budget_wall_still_fires_before_any_discovery(tmp_path):
    """Exhaustion is checked first, so a spent budget never pays for a pytest run."""
    workspace = _flat_workspace(tmp_path)
    (workspace / ".ai").mkdir(exist_ok=True)
    (workspace / ".ai" / "iter").write_text("5\n", encoding="utf-8")
    (workspace / ".ai" / "budget").write_text("5\n", encoding="utf-8")

    result = _run_test_gate(workspace)

    assert result.stdout == "exhausted", (result.stdout, result.stderr)
    assert not (workspace / ".ai" / "test.log").exists()


def test_no_target_routes_to_the_decision_point_not_back_into_the_fix_loop():
    """The token is only half the fix; the routing is the other half."""
    graph = _graph(_BUG_FIX)
    targets = {
        e.to_node
        for e in graph.outgoing_edges("test_gate")
        if "no_target" in str(e.condition or "")
    }
    assert targets == {"postmortem"}, targets

    # ...and the fix loop is still reachable for an ordinary red.
    fail_targets = {
        e.to_node
        for e in graph.outgoing_edges("test_gate")
        if "last_line=fail" in str(e.condition or "")
    }
    assert fail_targets == {"triage"}, fail_targets


def test_the_gate_no_longer_hardcodes_a_workspace_root_pytest():
    """The shipped text itself, so a revert cannot pass the behavioural tests by luck."""
    command = _test_gate_command()
    assert "$test_command" in command, "the honest escape hatch is a param"
    assert "--collect-only" in command, "the default target is discovered, not assumed"
    assert "no_target" in command
    assert "; pytest -q > .ai/test.log" not in command, "the bare workspace-root run is back"
