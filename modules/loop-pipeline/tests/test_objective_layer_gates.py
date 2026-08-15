"""Guards for the objective-layer exemplar's two gate scripts.

`examples/objective/objective-runner.dot` routes on machine artifacts rather
than on any worker's self-report, which means two small stdlib scripts carry
real authority:

  * ``validate_triage.py``     -- admits or rejects the intake record, and is
                                 the thing that decides the run's first route.
  * ``check_child_contract.py`` -- the structural gate on a GENERATED child
                                 pipeline, enforcing the shape checks
                                 ``attractor lint`` deliberately does not own.

Both are gates, so both must **fail closed**: a missing file, an unreadable
file, or a graph neither tool understands has to produce the rejecting token,
never the admitting one, and never a traceback (a crashing gate is a nonzero
exit, which routes to the postmortem path rather than admitting the artifact).

The examples tree lives at the repository root, outside the installed package,
so these tests run against a source checkout and skip gracefully when the
examples directory is absent (e.g. an installed-package test run) -- the same
pattern as ``test_examples_lint_clean.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
    path = _OBJECTIVE_DIR / script_name
    spec = importlib.util.spec_from_file_location(f"_objective_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixtures: a child that satisfies the contract, and ways of breaking it
# ---------------------------------------------------------------------------

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

# Lint-clean enough to pass `attractor lint` with warnings only, and still not an
# attractor: acyclic, the gate is colocated inside the worker, no budget wall, no
# fail-loud terminal, no failure route.  This is the case that justifies shipping
# a contract checker in addition to the linter.
_BROKEN_CHILD = """
digraph BrokenChild {
    graph [goal="$goal"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    work  [shape=box, goal_gate=true,
           prompt="Do the work for $goal and decide for yourself when it is done."]
    start -> work -> done
}
"""


@pytest.fixture(scope="module")
def contract() -> ModuleType:
    return _load("check_child_contract.py")


@pytest.fixture(scope="module")
def triage() -> ModuleType:
    return _load("validate_triage.py")


#: A definition of done that is genuinely RED before the work exists: the
#: sentinel is absent, so ``test -f`` exits 1.  C9 executes the DoD at admission
#: and requires exactly that, so every conforming fixture has to be honestly red
#: rather than merely plausible-looking.
_RED_DOD = "#!/usr/bin/env bash\n# red until the work lands\ntest -f {sentinel}\n"

#: The vacuous DoD from the adversarial review: structurally perfect, and a lie.
_VACUOUS_DOD = "#!/usr/bin/env bash\nexit 0\n"

#: Nonzero, but not a red check -- a script that could not run at all.
_BROKEN_DOD = "#!/usr/bin/env bash\nthis-command-does-not-exist\n"


def _write_child(
    tmp_path: Path,
    dot_text: str,
    *,
    with_dod: bool = True,
    dod_body: str | None = None,
) -> tuple[Path, Path]:
    gen = tmp_path / ".objective" / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    child = gen / "child.dot"
    child.write_text(dot_text, encoding="utf-8")
    dod = gen / "dod.sh"
    if with_dod:
        body = dod_body or _RED_DOD.format(sentinel=tmp_path / "work-landed")
        dod.write_text(body, encoding="utf-8")
    return child, dod


def _run_contract(
    contract: ModuleType,
    child: Path,
    dod: Path,
    report: Path,
    pin: Path | None = None,
) -> tuple[int, str]:
    argv = ["--child", str(child), "--dod", str(dod), "--report", str(report)]
    # Always pin somewhere hermetic: the default is workspace-relative, and a
    # test must never write into the repo it is run from.
    argv += ["--pin", str(pin or report.parent / "dod.sha256")]
    rc = contract.main(argv)
    return rc, report.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# check_child_contract.py
# ---------------------------------------------------------------------------


def test_contract_admits_a_conforming_child(contract, tmp_path, capsys):
    child, dod = _write_child(tmp_path, _GOOD_CHILD)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_ok"
    assert "verdict: contract_ok" in text
    assert "[FAIL]" not in text


def test_contract_blocks_a_child_that_is_not_an_attractor(contract, tmp_path, capsys):
    """The eight checks must each be load-bearing, not decorative."""
    child, dod = _write_child(tmp_path, _BROKEN_CHILD)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    failed = {line.split()[1] for line in text.splitlines() if line.startswith("[FAIL]")}
    # C2 no external DoD gate, C3 goal_gate on the worker, C4 no cycle,
    # C5 no budget wall, C6 no fail-loud terminal, C7 no failure route.
    assert {"C2", "C3", "C4", "C5", "C6", "C7"} <= failed
    # ...and the checks that genuinely hold must still pass, or the report is noise.
    assert "[PASS] C1" in text
    assert "[PASS] C8" in text


@pytest.mark.parametrize(
    ("mutation", "expected_fail"),
    [
        # C1: a second exit node -- the engine refuses to run this at all.
        (lambda t: t.replace("start -> work -> dod_gate", "extra [shape=Msquare]\n    start -> work -> dod_gate"), "C1"),
        # C2: the gate stops running the provided definition of done.
        (lambda t: t.replace("bash .objective/gen/dod.sh > .objective/gen/dod.log 2>&1", "true"), "C2"),
        # C4: remove the back-edge -- a straight line is a recipe, not an attractor.
        (lambda t: t.replace('critique -> work       [loop_restart="true"]', ""), "C4"),
        # C6: the escalation terminal stops exiting nonzero.
        (lambda t: t.replace('tool_command="printf escalated; exit 1"', 'tool_command="printf escalated"'), "C6"),
        # C8: the child no longer consumes the objective.
        (lambda t: t.replace("$goal", "a fixed task"), "C8"),
    ],
    ids=["C1_two_exits", "C2_gate_skips_dod", "C4_no_cycle", "C6_escalation_exits_zero", "C8_ignores_goal"],
)
def test_contract_catches_each_single_mutation(contract, tmp_path, capsys, mutation, expected_fail):
    child, dod = _write_child(tmp_path, mutation(_GOOD_CHILD))
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    failed = {line.split()[1] for line in text.splitlines() if line.startswith("[FAIL]")}
    assert expected_fail in failed, text


def test_contract_fails_closed_when_the_child_was_never_written(contract, tmp_path, capsys):
    """Lazy dot_file resolution means 'the composer wrote nothing' is a real case."""
    _, dod = _write_child(tmp_path, _GOOD_CHILD)
    missing = tmp_path / ".objective" / "gen" / "nope.dot"
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, missing, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "not found" in text


def test_contract_fails_closed_when_the_dod_script_is_missing_or_empty(contract, tmp_path, capsys):
    child, dod = _write_child(tmp_path, _GOOD_CHILD, with_dod=False)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)
    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "C0b" in text

    dod.write_text("", encoding="utf-8")
    rc, text = _run_contract(contract, child, dod, report)
    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "empty" in text


def test_contract_fails_closed_on_an_unparseable_child(contract, tmp_path, capsys):
    """A graph the checker cannot read is rejected, not admitted, and never crashes."""
    child, dod = _write_child(tmp_path, "this is not a graph at all")
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "C0" in text


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
# C9 -- the DoD must be RED before the work exists
#
# The adversarial review on PR #232 verified the hole this closes: the rule used
# to live only in the composer's prompt (`objective-runner.dot`'s compose node,
# `compose-contract.md`), and a prompt instruction is a suggestion.  A composer
# writing `exit 0` passed C1-C8, its child converged on the first attempt, and
# the parent's evidence gate re-ran the same vacuous script and agreed:
# `rc=0 && delta=changed` is `evidence_ok`.  A false green, in 2.4 hours, with
# zero work product -- the exact incident shape the exemplar exists to prevent.
# ---------------------------------------------------------------------------


def test_c9_blocks_a_vacuous_dod(contract, tmp_path, capsys):
    """`exit 0` before the work exists is not a definition of done."""
    child, dod = _write_child(tmp_path, _GOOD_CHILD, dod_body=_VACUOUS_DOD)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    failed = {line.split()[1] for line in text.splitlines() if line.startswith("[FAIL]")}
    # C9 alone -- every structural check still passes, which is precisely why
    # C9 had to exist: nothing about the graph was wrong.
    assert failed == {"C9"}, text
    assert "exited 0 BEFORE any work was done" in text


def test_c9_admits_a_genuinely_red_dod(contract, tmp_path, capsys):
    """The conforming fixture's DoD exits 1, and C9 says so explicitly."""
    child, dod = _write_child(tmp_path, _GOOD_CHILD)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_ok"
    assert "[PASS] C9" in text
    assert "exited 1 before the work exists" in text


def test_c9_names_a_broken_dod_separately_from_a_green_one(contract, tmp_path, capsys):
    """rc>=2 is a script that could not run, not a red check -- say which.

    Conflating the two would teach the composer that shipping a crash is an
    acceptable definition of done, because a crash is also "nonzero".
    """
    child, dod = _write_child(tmp_path, _GOOD_CHILD, dod_body=_BROKEN_DOD)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "[FAIL] C9" in text
    assert "BROKEN script, not a red check" in text
    assert "exited 0 BEFORE" not in text


def test_c9_names_a_dod_that_never_terminates(contract, tmp_path, capsys):
    """The DoD is re-run at least three times per iteration; it must finish."""
    child, dod = _write_child(
        tmp_path, _GOOD_CHILD, dod_body="#!/usr/bin/env bash\nsleep 30\n"
    )
    report = tmp_path / "contract-report.txt"
    rc = contract.main(
        [
            "--child", str(child),
            "--dod", str(dod),
            "--report", str(report),
            "--pin", str(tmp_path / "dod.sha256"),
            "--dod-timeout", "1",
        ]
    )
    text = report.read_text(encoding="utf-8")

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "did not finish within 1s" in text


def test_c9_is_not_reported_when_there_is_no_dod_to_run(contract, tmp_path, capsys):
    """A missing DoD is C0b's judgement; running nothing would prove nothing."""
    child, dod = _write_child(tmp_path, _GOOD_CHILD, with_dod=False)
    report = tmp_path / "contract-report.txt"
    rc, text = _run_contract(contract, child, dod, report)

    assert rc == 0
    assert capsys.readouterr().out == "contract_bad"
    assert "C0b" in text
    assert "C9" not in text


# ---------------------------------------------------------------------------
# The sha-pin -- "the DoD I admitted" vs "the DoD that got re-run"
# ---------------------------------------------------------------------------


def test_admission_pins_the_dod_and_rejection_leaves_no_pin(contract, tmp_path, capsys):
    """The pin records exactly the bytes that passed C9, and only those."""
    child, dod = _write_child(tmp_path, _GOOD_CHILD)
    report = tmp_path / "contract-report.txt"
    pin = tmp_path / "dod.sha256"

    _run_contract(contract, child, dod, report, pin)
    assert capsys.readouterr().out == "contract_ok"
    expected = hashlib.sha256(dod.read_bytes()).hexdigest()
    assert pin.read_text(encoding="utf-8").strip() == expected

    # A rejected child must not leave a pin behind for a later run to match.
    dod.write_text(_VACUOUS_DOD, encoding="utf-8")
    _run_contract(contract, child, dod, report, pin)
    assert capsys.readouterr().out == "contract_bad"
    assert not pin.exists()


def test_the_pin_matches_what_sha256sum_prints(contract, tmp_path, capsys):
    """The graph compares this pin using shell `sha256sum`; the two must agree."""
    child, dod = _write_child(tmp_path, _GOOD_CHILD)
    report = tmp_path / "contract-report.txt"
    pin = tmp_path / "dod.sha256"
    _run_contract(contract, child, dod, report, pin)
    assert capsys.readouterr().out == "contract_ok"

    shell = subprocess.run(
        ["sha256sum", str(dod)], capture_output=True, text=True, check=True
    ).stdout.split()[0]
    assert pin.read_text(encoding="utf-8").strip() == shell


def test_triage_pins_the_evidence_command_it_published(triage, tmp_path, capsys):
    """Lane runs get the same protection: the gate pins what it published."""
    _run_triage(triage, tmp_path, _VALID_TRIAGE)
    assert capsys.readouterr().out == "bugfix"
    state = tmp_path / ".objective"
    published = state / "evidence-command"
    pin = state / "evidence-command.sha256"
    assert pin.read_text(encoding="utf-8").strip() == hashlib.sha256(
        published.read_bytes()
    ).hexdigest()


def test_triage_redirect_clears_the_evidence_command_pin(triage, tmp_path, capsys):
    """No command to re-run means no pin left lying around to match against."""
    _run_triage(triage, tmp_path, _VALID_TRIAGE)
    capsys.readouterr()
    assert (tmp_path / ".objective" / "evidence-command.sha256").exists()

    _run_triage(triage, tmp_path, dict(_VALID_TRIAGE, shape="redirect", evidence_command="NONE"))
    assert capsys.readouterr().out == "redirect"
    assert not (tmp_path / ".objective" / "evidence-command.sha256").exists()


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
        shell=True, cwd=tmp_path, check=True,
    )
    # triage_gate admitted a compose shape (CF-2 pins this exact command).
    (tmp_path / ".objective" / "evidence-command").write_text(
        "bash .objective/gen/dod.sh\n", encoding="utf-8"
    )
    (tmp_path / ".objective" / "gen" / "child.dot").write_text(_GOOD_CHILD, encoding="utf-8")
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
        shell=True, cwd=workspace, env=env, capture_output=True, text=True, check=False,
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
    (workspace / ".objective" / "gen" / "dod.sh").write_text(_VACUOUS_DOD, encoding="utf-8")
    (workspace / "src" / "app.py").write_text("def f(): pass\n# touched\n", encoding="utf-8")

    result = _run_gate("evidence_gate", workspace)

    assert result.returncode != 0, "a tampered pin must be a LOUD nonzero, not a token"
    assert result.stdout == "tampered"
    assert ".objective/gen/dod.sh was altered after contract_gate admitted it" in result.stderr
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
    (workspace / ".objective" / "evidence-command").write_text("true\n", encoding="utf-8")
    result = _run_gate("evidence_gate", workspace)

    assert result.returncode != 0
    assert result.stdout == "tampered"
    assert ".objective/evidence-command was altered after triage_gate admitted it" in result.stderr


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
# validate_triage.py -- the intake gate
# ---------------------------------------------------------------------------

_VALID_TRIAGE = {
    "shape": "bugfix",
    "three_question": {
        "cycle": "yes -- fix, run the suite, re-fix",
        "evidence_gate": "yes -- pytest exits 0 only when the regression test passes",
        "bad_day": "yes -- a plausible-but-wrong fix leaves the test red",
    },
    "evidence_command": "pytest -q",
    "rationale": "A reproducible crash with a failing path maps onto the bug-fix lane.",
}


def _run_triage(triage: ModuleType, tmp_path: Path, record, *, max_frames: int = 2) -> str:
    state = tmp_path / ".objective"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "triage.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
    rc = triage.main(
        [
            "--triage", str(path),
            "--schema", str(_OBJECTIVE_DIR / "triage-schema.json"),
            "--state-dir", str(state),
            "--max-frames", str(max_frames),
        ]
    )
    assert rc == 0
    return rc


def test_triage_admits_a_valid_record_and_publishes_the_evidence_command(triage, tmp_path, capsys):
    _run_triage(triage, tmp_path, _VALID_TRIAGE)
    assert capsys.readouterr().out == "bugfix"
    state = tmp_path / ".objective"
    assert (state / "shape").read_text(encoding="utf-8").strip() == "bugfix"
    # The gate -- not the worker -- publishes what the evidence gate will re-run.
    assert (state / "evidence-command").read_text(encoding="utf-8").strip() == "pytest -q"


def test_triage_rejects_a_lane_with_no_machine_evidence(triage, tmp_path, capsys):
    """CF-1: no attractor without machine evidence. The honest shape is redirect."""
    record = dict(_VALID_TRIAGE, evidence_command="NONE")
    _run_triage(triage, tmp_path, record)
    assert capsys.readouterr().out == "triage_bad"
    assert "CF-1" in (tmp_path / ".objective" / "triage-report.txt").read_text(encoding="utf-8")


def test_triage_pins_the_composed_child_dod_path(triage, tmp_path, capsys):
    """CF-2: the parent re-runs the DoD from a fixed path, so compose cannot move it."""
    record = dict(_VALID_TRIAGE, shape="compose", evidence_command="bash somewhere/else.sh")
    _run_triage(triage, tmp_path, record)
    assert capsys.readouterr().out == "triage_bad"
    assert "CF-2" in (tmp_path / ".objective" / "triage-report.txt").read_text(encoding="utf-8")

    ok = dict(_VALID_TRIAGE, shape="compose", evidence_command="bash .objective/gen/dod.sh")
    _run_triage(triage, tmp_path, ok)
    assert capsys.readouterr().out == "compose"


def test_triage_redirect_publishes_no_evidence_command(triage, tmp_path, capsys):
    record = dict(_VALID_TRIAGE, shape="redirect", evidence_command="NONE")
    _run_triage(triage, tmp_path, record)
    assert capsys.readouterr().out == "redirect"
    assert not (tmp_path / ".objective" / "evidence-command").exists()


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        ({**_VALID_TRIAGE, "shape": "improvise"}, "off-vocabulary shape"),
        ({k: v for k, v in _VALID_TRIAGE.items() if k != "rationale"}, "missing required field"),
        ("{ not json", "malformed JSON"),
        (
            {**_VALID_TRIAGE, "three_question": {**_VALID_TRIAGE["three_question"], "cycle": "it depends"}},
            "CF-3: answer does not begin with yes/no",
        ),
    ],
    ids=["bad_shape", "missing_field", "malformed_json", "unanchored_answer"],
)
def test_triage_rejects_malformed_records(triage, tmp_path, capsys, record, reason):
    _run_triage(triage, tmp_path, record)
    assert capsys.readouterr().out == "triage_bad", reason


def test_triage_fuse_stops_reframing_forever(triage, tmp_path, capsys):
    """The re-frame loop is bounded, and exhaustion is a distinct token."""
    bad = dict(_VALID_TRIAGE, evidence_command="NONE")
    for _ in range(2):
        _run_triage(triage, tmp_path, bad, max_frames=2)
        assert capsys.readouterr().out == "triage_bad"
    _run_triage(triage, tmp_path, bad, max_frames=2)
    assert capsys.readouterr().out == "triage_exhausted"


def test_triage_missing_record_is_a_judgement_not_a_crash(triage, tmp_path, capsys):
    state = tmp_path / ".objective"
    state.mkdir(parents=True, exist_ok=True)
    rc = triage.main(
        [
            "--triage", str(state / "never-written.json"),
            "--schema", str(_OBJECTIVE_DIR / "triage-schema.json"),
            "--state-dir", str(state),
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out == "triage_bad"


def test_triage_cannot_run_is_distinct_from_a_bad_record(triage, tmp_path, capsys):
    """An unreadable schema is a TOOL failure: nonzero exit, no token.

    The graph routes that through ``outcome=fail`` to the postmortem path,
    deliberately not through the ``triage_bad`` corrective loop.
    """
    state = tmp_path / ".objective"
    state.mkdir(parents=True, exist_ok=True)
    (state / "triage.json").write_text(json.dumps(_VALID_TRIAGE), encoding="utf-8")
    rc = triage.main(
        [
            "--triage", str(state / "triage.json"),
            "--schema", str(tmp_path / "no-such-schema.json"),
            "--state-dir", str(state),
        ]
    )
    assert rc != 0
    assert capsys.readouterr().out == ""
