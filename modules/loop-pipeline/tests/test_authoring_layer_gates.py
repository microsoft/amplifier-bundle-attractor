"""Engine-dependent residual of the authoring-layer exemplar's structural gate guard.

The majority of this file's guards (the doctrine-checker mutation/fail-closed
tests, calibration against shipped exemplars) moved to the repo-root
opinionated-layer harness at tests/test_authoring_layer_gates.py (Track A of the repo split,
DESIGN-repo-split.md §1.4/§5#2). What stayed here needs the LIVE engine:
  * test_minimal_parser_agrees_with_the_engine_on_shipped_graphs cross-checks
    the checker's stdlib-only DOT reader against amplifier_module_loop_pipeline's
    real parser.
  * _author_graph()-based tests assert the shipped pipeline-author.dot's real
    parsed Graph has no dead ends and no live retry_target on its goal gate.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTHORING_DIR = _REPO_ROOT / "examples" / "authoring"

pytestmark = pytest.mark.skipif(
    not _AUTHORING_DIR.is_dir(),
    reason="examples/authoring/ not present (installed-package run)",
)


def _load(script_name: str):
    """Import a script from examples/authoring/ by path, without polluting sys.path."""
    import importlib.util
    import sys

    path = _AUTHORING_DIR / script_name
    spec = importlib.util.spec_from_file_location(f"_authoring_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load("check_authored_pipeline.py")


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

_GOOD_COMPANION = """# Authored pipeline

`work` advances the objective; `critique` distils the failing DoD output into
feedback the next attempt reads; `postmortem` salvages the analysis when the
budget is spent. Each states objective, constraints, capabilities and evidence.
"""

_A4_BYPASS = ("start -> work", "start -> work\n    work -> done")


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
            assert (
                str(mini_graph.attr(node_id, key)).lower() == str(engine_value).lower()
            ), f"{shipped}: {node_id}.{key} disagrees between the two parsers"


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

    graph = parse_dot(
        (_AUTHORING_DIR / "pipeline-author.dot").read_text(encoding="utf-8")
    )
    return graph.nodes[node_id].attrs["tool_command"]


def _run_gate(
    node_id: str, workspace: Path, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
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
    assert (
        (workspace / ".authoring" / "convergence.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )


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
        pytest.param(
            "VERDICT: SHIP\n\nand then some more prose\n",
            "noverdict",
            id="verdict-not-last",
        ),
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
        (workspace / ".authoring" / "critique.md").write_text(
            critique, encoding="utf-8"
        )

    result = _run_gate("verdict_gate", workspace)

    assert result.stdout == expected
    # Idiom B: only `ship` exits 0, so a red verdict is a genuine FAIL the
    # engine's exit-time goal-gate check can see.
    assert (result.returncode == 0) is (expected == "ship")
    record = (workspace / ".authoring" / "convergence.jsonl").read_text(
        encoding="utf-8"
    )
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
    (workspace / ".authoring" / "triage.md").write_text(
        "no verdict line here\n", encoding="utf-8"
    )

    seen = [
        _run_gate("triage_gate", workspace, max_frames="2").stdout for _ in range(4)
    ]

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

    seen = [
        _run_gate("lint_gate", workspace, max_iterations="2").stdout for _ in range(3)
    ]

    assert seen == ["lint_pass", "lint_pass", "exhausted"]
    assert (workspace / ".authoring" / "iter").read_text(
        encoding="utf-8"
    ).strip() == "3"


@_SHELL_GATES
def test_real_finalize_refuses_to_open_the_exit_without_a_disposition(tmp_path):
    workspace = _authoring_workspace(tmp_path)

    empty = _run_gate("finalize", workspace)
    assert empty.returncode != 0
    assert empty.stdout == "no_disposition"

    (workspace / ".authoring" / "redirect.md").write_text(
        "the honest no\n", encoding="utf-8"
    )
    redirected = _run_gate("finalize", workspace)
    assert redirected.returncode == 0
    assert redirected.stdout == "finalized"
    assert (workspace / ".authoring" / "disposition").read_text(
        encoding="utf-8"
    ).strip() == "redirected"

    (workspace / ".authoring" / "published").write_text(
        "out/x.dot out/x.md\n", encoding="utf-8"
    )
    authored = _run_gate("finalize", workspace)
    assert authored.stdout == "finalized"
    assert (workspace / ".authoring" / "disposition").read_text(
        encoding="utf-8"
    ).strip() == "authored"


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
        "preflight",
        workspace,
        pipeline_name="doc-claims-verified",
        PATH=_path_without("attractor"),
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
    assert (workspace / ".authoring" / "name").read_text(
        encoding="utf-8"
    ).strip() == "doc-claims-verified"


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

    return parse_dot(
        (_AUTHORING_DIR / "pipeline-author.dot").read_text(encoding="utf-8")
    )


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
    assert ".authoring/disposition" in command, (
        "the terminal must say which terminal it was"
    )


def test_no_node_in_the_authoring_graph_dead_ends():
    """The whole-graph form of the rule, so a future terminal cannot regress."""
    graph = _author_graph()
    exits = {n.id for n in graph.nodes.values() if n.is_exit_node()}
    dead_ends = [
        n for n in graph.nodes if n not in exits and not graph.outgoing_edges(n)
    ]
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
