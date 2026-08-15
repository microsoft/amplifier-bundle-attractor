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
