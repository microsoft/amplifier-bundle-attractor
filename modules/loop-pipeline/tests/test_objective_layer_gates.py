"""Engine-dependent residual of the objective-layer exemplar's two gate-script guards.

The majority of this file's guards (the doctrine-checker mutation/fail-closed
tests, calibration against shipped exemplars) moved to the repo-root
opinionated-layer harness at tests/test_objective_layer_gates.py (Track A of the repo split,
DESIGN-repo-split.md §1.4/§5#2). What stayed here needs the LIVE engine:
  * test_minimal_parser_agrees_with_the_engine_on_shipped_graphs cross-checks
    the checker's stdlib-only DOT reader against the real engine parser.
  * The `_tool_command`/`_run_gate`-based `test_real_*` tests drive the exact
    shell text read out of objective-runner.dot by the engine's own parser.
  * `_graph()`-based tests and test_escalation_terminates_the_run_without_a_
    routing_error assert against the real parsed Graph / a live PipelineEngine run.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OBJECTIVE_DIR = _REPO_ROOT / "examples" / "objective"

pytestmark = pytest.mark.skipif(
    not _OBJECTIVE_DIR.is_dir(),
    reason="examples/objective/ not present (installed-package run)",
)


def _load(script_name: str) -> ModuleType:
    """Import a script from examples/objective/ by path, without polluting sys.path."""
    import importlib.util
    import sys

    path = _OBJECTIVE_DIR / script_name
    spec = importlib.util.spec_from_file_location(f"_objective_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contract() -> ModuleType:
    return _load("check_child_contract.py")


@pytest.fixture(scope="module")
def triage() -> ModuleType:
    return _load("validate_triage.py")


_GOOD_CHILD = """
// A generated child that satisfies the composed-child contract.
digraph ComposedChild {
    graph [goal="$goal", default_max_retries=2]

    start [shape=Mdiamond]
    done  [shape=Msquare]

    work [shape=box,
          prompt="Advance this objective: $goal. Read .objective/gen/feedback.md if present."]

    dod_gate [shape=parallelogram, max_retries=0, goal_gate=true, retry_target="work",
        tool_command="mi=$max_iterations; B=${mi:-6}; n=$(($(cat .objective/gen/iter 2>/dev/null || echo 0)+1)); echo $n > .objective/gen/iter; if [ \\"$n\\" -gt \\"$B\\" ]; then printf exhausted; else bash .objective/gen/dod.sh > .objective/gen/dod.log 2>&1 && printf green || printf red; fi"]

    critique [shape=box, prompt="Write .objective/gen/feedback.md from .objective/gen/dod.log."]

    postmortem [shape=box, prompt="Write .objective/gen/postmortem.md."]

    escalated [shape=parallelogram, max_retries=0, tool_command="printf escalated; exit 1"]

    start -> work -> dod_gate
    dod_gate -> done       [condition="context.tool.last_line=green && outcome=success"]
    dod_gate -> critique   [condition="context.tool.last_line=red && outcome=success"]
    dod_gate -> postmortem [condition="context.tool.last_line=exhausted && outcome=success"]
    dod_gate -> postmortem [condition="outcome=fail"]
    critique -> work       [loop_restart="true"]
    work       -> postmortem [condition="outcome=fail"]
    critique   -> postmortem [condition="outcome=fail"]
    postmortem -> escalated  [condition="outcome=fail"]
    postmortem -> escalated
}
"""

_VACUOUS_DOD = "#!/usr/bin/env bash\nexit 0\n"


@pytest.mark.parametrize(
    "shipped",
    [
        "examples/pipelines/practical/bug-fix.dot",
        "examples/pipelines/practical/test-gen.dot",
        "examples/patterns/task-runner.dot",
        "examples/objective/objective-runner.dot",
    ],
)
def test_minimal_parser_agrees_with_the_engine_on_shipped_graphs(contract, shipped):
    """The checker ships its own DOT reader; keep it honest against the real one.

    It is stdlib-only so the gate runs under whatever ``python3`` is on PATH in
    the target workspace (not the ``attractor`` CLI's virtualenv). That freedom
    is only safe if the two parsers agree on node ids and edge count for the
    graphs this repo actually ships.
    """
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    text = (_REPO_ROOT / shipped).read_text(encoding="utf-8")
    engine_graph = parse_dot(text)
    mini_graph = contract.parse_dot_min(text)

    assert set(mini_graph.nodes) == set(engine_graph.nodes)
    assert len(mini_graph.edges) == len(engine_graph.edges)


# ---------------------------------------------------------------------------
# End-to-end: the REAL tool_command text, taken from the graph the engine runs
#
# These drive the shell the pipeline actually executes -- extracted from
# objective-runner.dot by the engine's own parser -- rather than a paraphrase of
# it.  A fix that only holds in a hand-written approximation of the gate is not
# a fix.
# ---------------------------------------------------------------------------

_SHELL_GATES = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("sha256sum")),
    reason="the shipped gate commands need bash and sha256sum",
)


def _tool_command(node_id: str) -> str:
    """The verbatim tool_command of a node, read through the engine's parser."""
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot(
        (_OBJECTIVE_DIR / "objective-runner.dot").read_text(encoding="utf-8")
    )
    return graph.nodes[node_id].attrs["tool_command"]


def _objective_workspace(tmp_path: Path, dod_body: str) -> Path:
    """A workspace at the point `contract_gate` is about to run on a compose route."""
    (tmp_path / ".objective" / "gen").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("def f(): pass\n", encoding="utf-8")
    # preflight's anchor, over the workspace as it was before any work.
    subprocess.run(
        "find . -name .git -prune -o -name .objective -prune -o -type f -exec md5sum {} + "
        "2>/dev/null | LC_ALL=C sort | md5sum | cut -d' ' -f1 > .objective/anchor",
        shell=True,
        cwd=tmp_path,
        check=True,
    )
    # triage_gate admitted a compose shape (CF-2 pins this exact command).
    (tmp_path / ".objective" / "evidence-command").write_text(
        "bash .objective/gen/dod.sh\n", encoding="utf-8"
    )
    (tmp_path / ".objective" / "gen" / "child.dot").write_text(
        _GOOD_CHILD, encoding="utf-8"
    )
    (tmp_path / ".objective" / "gen" / "dod.sh").write_text(dod_body, encoding="utf-8")
    return tmp_path


def _run_gate(node_id: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "runner_dir": str(_OBJECTIVE_DIR),
        "target_dir": str(workspace),
        "max_iterations": "3",
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


@_SHELL_GATES
def test_real_contract_gate_command_blocks_the_vacuous_dod(tmp_path):
    """The review's exact scenario, through the shell the pipeline really runs.

    Before C9 this printed `contract_ok`, `run_child` ran a child that converged
    instantly, and `evidence_gate` returned `evidence_ok`.
    """
    workspace = _objective_workspace(tmp_path, _VACUOUS_DOD)
    result = _run_gate("contract_gate", workspace)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "contract_bad"
    # contract_bad routes back to `compose`; `run_child` is never reached, so
    # the vacuous DoD never gets to be re-run by the evidence gate at all.
    assert not (workspace / ".objective" / "dod.sha256").exists()
    assert "[FAIL] C9" in (workspace / ".objective" / "contract-report.txt").read_text(
        encoding="utf-8"
    )


@_SHELL_GATES
def test_real_evidence_gate_command_refuses_a_dod_rewritten_after_admission(tmp_path):
    """The rewrite-after-admission dodge, closed by the pin.

    An honestly-red DoD is admitted; the child then overwrites it with `exit 0`
    and touches a file, which used to be enough for `rc=0 && delta=changed`.
    """
    red = "#!/usr/bin/env bash\ngrep -q MARKER_WORK_LANDED src/app.py\n"
    workspace = _objective_workspace(tmp_path, red)

    admitted = _run_gate("contract_gate", workspace)
    assert admitted.stdout == "contract_ok", admitted.stderr
    assert (workspace / ".objective" / "dod.sha256").is_file()

    # The child does not do the work. It rewrites the check instead.
    (workspace / ".objective" / "gen" / "dod.sh").write_text(
        _VACUOUS_DOD, encoding="utf-8"
    )
    (workspace / "src" / "app.py").write_text(
        "def f(): pass\n# touched\n", encoding="utf-8"
    )

    result = _run_gate("evidence_gate", workspace)

    assert result.returncode != 0, "a tampered pin must be a LOUD nonzero, not a token"
    assert result.stdout == "tampered"
    assert (
        ".objective/gen/dod.sh was altered after contract_gate admitted it"
        in result.stderr
    )
    # Nonzero means tool.last_line is NOT refreshed, so every `&& outcome=success`
    # edge is dead and only `outcome=fail -> postmortem` can match.
    assert not (workspace / ".objective" / "evidence-pass").exists()


@_SHELL_GATES
def test_real_evidence_gate_command_refuses_a_rewritten_evidence_command(tmp_path):
    """Same close, on the lane route: the evidence command is pinned too."""
    workspace = _objective_workspace(tmp_path, "#!/usr/bin/env bash\nexit 1\n")
    pin = hashlib.sha256(
        (workspace / ".objective" / "evidence-command").read_bytes()
    ).hexdigest()
    (workspace / ".objective" / "evidence-command.sha256").write_text(
        pin + "\n", encoding="utf-8"
    )

    # A lane child overwrites what the parent is about to re-run.
    (workspace / ".objective" / "evidence-command").write_text(
        "true\n", encoding="utf-8"
    )
    result = _run_gate("evidence_gate", workspace)

    assert result.returncode != 0
    assert result.stdout == "tampered"
    assert (
        ".objective/evidence-command was altered after triage_gate admitted it"
        in result.stderr
    )


@_SHELL_GATES
def test_real_evidence_gate_command_still_passes_an_untampered_run(tmp_path):
    """The pin must not break the honest path -- a negative control for the guard."""
    workspace = _objective_workspace(tmp_path, "#!/usr/bin/env bash\ntest -f DONE\n")
    assert _run_gate("contract_gate", workspace).stdout == "contract_ok"

    # The child does the work the DoD asks for, and touches nothing else.
    (workspace / "DONE").write_text("", encoding="utf-8")
    result = _run_gate("evidence_gate", workspace)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "evidence_ok"
    assert (workspace / ".objective" / "evidence-pass").exists()


# ---------------------------------------------------------------------------
# Issue #243 -- the two defects two independent live runs found
#
# (a) The designed loud terminal dead-ended, so the engine reported the run's
#     most important honest outcome as an authoring bug:
#     `[PIPELINE] X Error at escalated (no_matching_edge)`.
# (b) The objective was ALREADY satisfied and the run escalated anyway: the
#     lane's own hardcoded gate could not pass, and its self-reported FAIL
#     routed straight past `evidence_gate` -- so the admitted evidence command
#     was never re-run by the parent even once.
#
# These read the shipped graph through the engine's own parser and drive the
# engine and the real command text, not a paraphrase of either.
# ---------------------------------------------------------------------------


def _graph():
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    return parse_dot(
        (_OBJECTIVE_DIR / "objective-runner.dot").read_text(encoding="utf-8")
    )


def test_escalated_routes_to_the_exit_instead_of_dead_ending():
    """243(a): a loud terminal must ROUTE; the main loop has no designed terminus.

    `run_subgraph()` distinguishes "no outgoing edges at all" from a
    conditional-mismatch dead end; `run()` does not.  A dead-ended `escalated`
    therefore terminates through `terminate_pipeline()` with
    `error_type=no_matching_edge` -- the same class a genuinely mis-authored
    graph produces -- whatever its exit status.
    """
    graph = _graph()
    exits = [n.id for n in graph.nodes.values() if n.is_exit_node()]
    assert exits == ["done"], exits

    outgoing = graph.outgoing_edges("escalated")
    assert outgoing, (
        "`escalated` has no outgoing edge, so the engine reports the designed "
        "escalation as PIPELINE_ERROR error_type=no_matching_edge (issue #243a)"
    )
    assert [e.to_node for e in outgoing] == ["done"]
    assert "outcome=fail" in (outgoing[0].condition or ""), outgoing[0].condition


def test_escalated_still_exits_nonzero_so_the_exit_it_reaches_is_red():
    """The routing fix must not turn the loud terminal into a quiet one.

    `escalated -> done` is only honest because `escalated`'s own FAIL is what
    `_check_goal_gates` returns from the exit.  An `escalated` that exited 0
    would convert the escalation into a green run -- the exact hazard TOPO-006
    names.
    """
    command = _graph().nodes["escalated"].attrs["tool_command"]
    assert command.rstrip().endswith("exit 1"), command
    assert (
        "> .objective/disposition" in command
        or "echo escalated > .objective/disposition" in command
    )


def test_the_goal_gate_carries_no_retry_target():
    """243(a), corollary: a retry_target on the goal gate is now reachable.

    `retry_target` on a goal gate is consulted in exactly one place --
    `_check_goal_gates()` at the exit node.  Before `escalated -> done` the
    exit was unreachable with the gate unsatisfied, so the attribute was dead;
    with it, the only way the gate is unsatisfied there is the `tampered`
    refusal, whose cause survives every retry.  Measured on the shipped engine
    with a faithful reduction of this graph: 51 gate executions before the step
    cap, versus one with the attribute gone.
    """
    graph = _graph()
    gate = graph.nodes["evidence_gate"]
    assert gate.attrs.get("goal_gate") in (True, "true"), gate.attrs.get("goal_gate")
    assert not gate.attrs.get("retry_target"), gate.attrs.get("retry_target")
    assert not gate.attrs.get("fallback_retry_target")
    assert not graph.graph_attrs.get("retry_target")
    assert not graph.graph_attrs.get("fallback_retry_target")


@pytest.mark.parametrize(
    "child",
    [
        "lane_bugfix",
        "lane_feature",
        "lane_refactor",
        "lane_testgen",
        "lane_review",
        "run_child",
    ],
)
def test_a_childs_terminal_fail_is_re_verified_not_believed(child):
    """243(b): the parent does not trust a child's self-report in EITHER direction.

    These edges used to run straight to `postmortem`, which made a lane's own
    terminal outcome the parent's verdict.  In the 2026-08-15 exemplar-01 runs
    the shipped bug-fix lane's `test_gate` runs a hardcoded `pytest -q` from the
    workspace root, which cannot pass for a package nested one level down; it
    exhausted its own budget and reported FAIL.  The objective was satisfied --
    the admitted `cd <pkg> && python3 -m pytest tests/ -v` exits 0 -- but
    `evidence_gate` never ran once, so the parent escalated an objective it had
    never checked.
    """
    graph = _graph()
    fail_edges = [
        e
        for e in graph.outgoing_edges(child)
        if e.condition and "outcome=fail" in e.condition
    ]
    assert fail_edges, f"{child} has no failure route at all"
    assert [e.to_node for e in fail_edges] == ["evidence_gate"], (
        f"{child}'s FAIL must enter the parent's own gate, not bypass it; "
        f"got {[e.to_node for e in fail_edges]}"
    )
    # postmortem stays reachable -- through the budget wall, where giving up belongs.
    assert any(
        e.to_node == "postmortem" and "exhausted" in (e.condition or "")
        for e in graph.outgoing_edges("evidence_gate")
    )


def test_escalation_terminates_the_run_without_a_routing_error(tmp_path):
    """243(a), end to end on the SHIPPED graph, with no LLM node executed.

    `preflight -> escalated [outcome=fail]` is a tool-to-tool route, so a bad
    `runner_dir` walks the real file from `start` to the real `escalated` and
    out through the real exit.  Pre-fix this returned FAIL carrying
    `PIPELINE_ERROR error_type=no_matching_edge` and notes "No matching edge
    from node 'escalated'"; post-fix the FAIL is `escalated`'s own exit code.
    """
    import asyncio

    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext
    from amplifier_module_loop_pipeline.outcome import StageStatus
    from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_ERROR

    class _Hooks:
        def __init__(self):
            self.events = []

        async def emit(self, name, data):
            self.events.append((name, data))

    context = PipelineContext()
    context.set("context.target_dir", str(tmp_path))
    context.set("runner_dir", str(tmp_path / "not-the-objective-dir"))
    context.set("target_dir", str(tmp_path))

    hooks = _Hooks()
    engine = PipelineEngine(
        graph=_graph(),
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=None)),
        logs_root=str(tmp_path / "logs"),
        hooks=hooks,
    )
    outcome = asyncio.run(engine.run())

    routing_errors = [
        d
        for n, d in hooks.events
        if n == PIPELINE_ERROR and d.get("error_type") == "no_matching_edge"
    ]
    assert not routing_errors, (
        "the designed escalation is being reported as an authoring bug: "
        f"{routing_errors}"
    )
    assert outcome.status is StageStatus.FAIL, "escalation must stay LOUD (CLI exit 1)"
    assert "No matching edge" not in (
        (outcome.notes or "") + (outcome.failure_reason or "")
    )
    assert (tmp_path / ".objective" / "disposition").read_text(
        encoding="utf-8"
    ).strip() == "escalated"
    assert (tmp_path / ".objective" / "postmortem" / "escalation.md").is_file()


@_SHELL_GATES
def test_evidence_gate_runs_a_cd_carrying_command_exactly_as_admitted(tmp_path):
    """243(b): the shipped gate command, on the exemplar-01 workspace shape.

    The run's postmortem reconstructed this as "the pipeline evaluated the
    evidence command from the wrong working directory... rather than
    `cd /workspace/notesvc && python3 -m pytest tests/ -v`".  Driven against the
    real `tool_command`, that reconstruction does not hold: `bash -c "$ev"`
    hands the admitted string to a shell verbatim and the `cd` is honoured.
    What was missing is the receipt -- nothing recorded WHAT the gate ran, so a
    divergence could only ever be inferred from a postmortem at budget
    exhaustion.  This pins both halves.
    """
    workspace = tmp_path
    state = workspace / ".objective"
    state.mkdir()
    pkg = workspace / "notesvc" / "notesvc"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "users.py").write_text(
        "def user_slug(u):\n    return u.lower()\n", encoding="utf-8"
    )
    tests = workspace / "notesvc" / "tests"
    tests.mkdir()
    (tests / "test_users.py").write_text(
        "from notesvc.users import user_slug\n\n\ndef test_slug():\n    assert user_slug('AB') == 'ab'\n",
        encoding="utf-8",
    )
    # A stale anchor stands in for preflight's pre-work digest, so delta=changed.
    (state / "anchor").write_text("0" * 32 + "\n", encoding="utf-8")

    admitted = f"cd {workspace}/notesvc && python3 -m pytest tests/ -q"
    (state / "evidence-command").write_text(admitted + "\n", encoding="utf-8")
    (state / "evidence-command.sha256").write_text(
        hashlib.sha256((state / "evidence-command").read_bytes()).hexdigest() + "\n",
        encoding="utf-8",
    )

    result = _run_gate("evidence_gate", workspace)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "evidence_ok", (
        "a cd-carrying admitted command must run as admitted: "
        f"{(state / 'evidence-1.log').read_text(encoding='utf-8')}"
    )
    record = json.loads(
        (state / "convergence.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["dod_exit"] == 0 and record["delta"] == "changed", record

    # The receipt: what ran, recorded next to what was admitted.
    log = (state / "evidence-1.log").read_text(encoding="utf-8")
    assert log.splitlines()[0] == f"RAN AS ADMITTED: {admitted}", log.splitlines()[:1]
    assert (
        record["evidence_command_sha"]
        == hashlib.sha256(admitted.encode()).hexdigest()[:12]
    ), record


@_SHELL_GATES
def test_preflight_refuses_when_the_shell_the_gate_uses_is_absent(tmp_path):
    """243(b), the same class one layer earlier.

    `evidence_gate` executes the admitted command with `bash -c`.  Preflight
    verified `sha256sum`, `md5sum` and `find` but not `bash`, so a workspace
    without bash would have returned `ready` and then failed EVERY evidence
    iteration for a reason no code change could affect -- a gate that could
    never go green, which is exactly the shape #243(b) reports.
    """
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    # Everything preflight itself needs, and deliberately NOT bash.
    for tool in ("mkdir", "sha256sum", "md5sum", "find", "sort", "cut"):
        real = shutil.which(tool)
        if real is None:
            pytest.skip(f"{tool} is not available to build a bash-free PATH")
        (stub_bin / tool).symlink_to(real)
    assert shutil.which("bash", path=str(stub_bin)) is None

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = subprocess.run(
        _tool_command("preflight"),
        shell=True,
        cwd=workspace,
        env={
            "PATH": str(stub_bin),
            "runner_dir": str(_OBJECTIVE_DIR),
            "target_dir": str(workspace),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout == "blocked", (
        "preflight admitted a workspace whose shell the evidence gate cannot use; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode != 0
    assert "bash" in result.stderr, result.stderr
