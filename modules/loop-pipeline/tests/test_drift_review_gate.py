"""Guards for the drift-review exemplar -- the QUALITY_PROTOCOL Layer-3 executor.

`examples/drift-review/drift-review.dot` runs four LLM reviewers whose entire
output is *judgement*, and then decides -- outside every one of their contexts --
which of their findings are shaped. The thing that decides is
``check_findings.py``, so that script carries the whole weight of the design:

  * A finding must cite ``file:line`` on **both** sides, the drifting surface and
    the normative passage it contradicts, and the gate **re-opens both files**
    rather than believing either citation.
  * "Drift" is measured against a closed set of normative sources, so a Layer-3
    finding means the same thing Layers 0-2 mean, one level up.
  * Rejection is a *judgement* (exit 0 plus a token, routed back to a repair
    worker); an unrunnable gate is a *tool failure* (nonzero exit, no token,
    routed to the loud terminal). Collapsing those two is how a broken
    instrument comes to look like a clean repo.

Every case below is the mutation form: take a corpus the gate admits, break one
thing, and assert the gate rejects it **naming that thing**. A gate never seen
red is an unproven gate.

The examples tree lives at the repository root, outside the installed package,
so these tests run against a source checkout and skip gracefully when the
examples directory is absent (e.g. an installed-package test run) -- the same
pattern as ``test_examples_lint_clean.py`` and ``test_objective_layer_gates.py``.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRIFT_DIR = _REPO_ROOT / "examples" / "drift-review"
_DOT_PATH = _DRIFT_DIR / "drift-review.dot"

pytestmark = pytest.mark.skipif(
    not _DRIFT_DIR.is_dir(),
    reason="examples/drift-review/ not present (installed-package run)",
)

_CLASSES = ("core-docs", "examples", "guidance", "ledgers")


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    """Import check_findings.py by path, without polluting sys.path."""
    path = _DRIFT_DIR / "check_findings.py"
    spec = importlib.util.spec_from_file_location("_drift_check_findings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# A miniature repository whose citations really resolve
# ---------------------------------------------------------------------------

_DRIFTING_DOC = """# Some Guide

Line three is filler.
The engine picks the alphabetically first edge when nothing matches.
More filler below.
Still more filler.
And a closing line.
"""

_NORMATIVE_SPEC = """# Canonical Spec

## 1. Overview
Filler paragraph one, standing in for the spec's front matter.
Filler paragraph two.

## 2. Nodes
Filler paragraph three.
Filler paragraph four.

## 3.3 Edge selection
No matching edge is a hard failure and the run stops loudly.
Filler paragraph five.
Filler paragraph six.
"""

_VISION = """# Vision

Fail loud; never fall back silently.
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tiny tree with one drifting surface and two normative sources."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "specs" / "canonical").mkdir(parents=True)
    (root / "docs" / "SOME-GUIDE.md").write_text(_DRIFTING_DOC, encoding="utf-8")
    (root / "docs" / "VISION.md").write_text(_VISION, encoding="utf-8")
    (root / "specs" / "canonical" / "spec.md").write_text(_NORMATIVE_SPEC, encoding="utf-8")
    (root / "README.md").write_text("# Readme\n\nfiller\n", encoding="utf-8")
    return root


def _good_finding() -> dict:
    return {
        "id": "DR-CORE-001",
        "class": "core-docs",
        "severity": "high",
        "title": "Guide teaches alphabetical fallback the spec removed",
        "drift": {
            "file": "docs/SOME-GUIDE.md",
            "line": 4,
            "quote": "The engine picks the alphabetically first edge when nothing matches.",
        },
        "contradicts": {
            "file": "specs/canonical/spec.md",
            "line": 12,
            "quote": "No matching edge is a hard failure and the run stops loudly.",
        },
        "why": "A reader following the guide would expect a silent fallback that no longer exists.",
    }


def _corpus() -> dict[str, dict]:
    """One admissible raw file per class: findings on core-docs, clean elsewhere."""
    corpus: dict[str, dict] = {
        "core-docs": {
            "class": "core-docs",
            "swept": ["README.md", "docs/SOME-GUIDE.md"],
            "findings": [_good_finding()],
        }
    }
    for cls in _CLASSES[1:]:
        corpus[cls] = {"class": cls, "swept": ["README.md"], "findings": []}
    return corpus


def _write(repo: Path, corpus: dict[str, dict]) -> Path:
    raw = repo / ".drift-review" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for cls, body in corpus.items():
        (raw / f"{cls}.json").write_text(
            body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
        )
    return raw


def _run(checker: ModuleType, repo: Path, *, max_revisions: int = 2) -> int:
    return checker.main(
        [
            "--raw-dir", str(repo / ".drift-review" / "raw"),
            "--repo-root", str(repo),
            "--state-dir", str(repo / ".drift-review"),
            "--classes", ",".join(_CLASSES),
            "--max-revisions", str(max_revisions),
        ]
    )


def _report(repo: Path) -> str:
    return (repo / ".drift-review" / "findings-report.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The green path
# ---------------------------------------------------------------------------


def test_admits_a_shaped_corpus_and_writes_the_findings_file(checker, repo, capsys):
    _write(repo, _corpus())
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"

    written = json.loads((repo / ".drift-review" / "findings.json").read_text(encoding="utf-8"))
    assert written["finding_count"] == 1
    assert written["findings"][0]["id"] == "DR-CORE-001"
    # The swept record survives into the corpus: "clean" and "never looked" must
    # stay distinguishable downstream.
    assert written["swept"]["guidance"] == ["README.md"]
    assert "FINDINGS ADMITTED" in _report(repo)


def test_zero_findings_is_a_result_not_a_failure(checker, repo, capsys):
    corpus = {cls: {"class": cls, "swept": ["README.md"], "findings": []} for cls in _CLASSES}
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"
    written = json.loads((repo / ".drift-review" / "findings.json").read_text(encoding="utf-8"))
    assert written["finding_count"] == 0
    assert "clean sweep is a result" in _report(repo)


def test_a_reflowed_quote_still_resolves(checker, repo, capsys):
    """Whitespace differences are tolerated; invented text is not (next test)."""
    corpus = _corpus()
    corpus["core-docs"]["findings"][0]["drift"]["quote"] = (
        "The   engine picks the alphabetically\n  first edge when nothing matches."
    )
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"


def test_quote_resolves_from_a_neighbouring_line(checker, repo, capsys):
    """An off-by-a-couple line number resolves; a wholly wrong one does not."""
    corpus = _corpus()
    corpus["core-docs"]["findings"][0]["drift"]["line"] = 3
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"


# ---------------------------------------------------------------------------
# The mutations -- each rejection must name what it rejected
# ---------------------------------------------------------------------------


def _mutate(mutator) -> dict[str, dict]:
    corpus = _corpus()
    mutator(corpus)
    return corpus


def _drop(finding_key: str):
    def _apply(corpus: dict) -> None:
        del corpus["core-docs"]["findings"][0][finding_key]

    return _apply


def _set(path: str, value):
    def _apply(corpus: dict) -> None:
        node = corpus["core-docs"]["findings"][0]
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

    return _apply


@pytest.mark.parametrize(
    ("mutator", "expected_fragment"),
    [
        (_drop("drift"), "missing required field 'drift'"),
        (_drop("why"), "missing required field 'why'"),
        (_set("severity", "spicy"), "is not one of ['critical', 'high', 'medium', 'low']"),
        (_set("class", "examples"), "does not match this file's class 'core-docs'"),
        (_set("id", "x"), "is not a short slug matching"),
        (_set("title", "short"), "at least 12 characters"),
        (_set("why", "too short"), "at least 40 characters"),
        (_set("drift.file", "docs/NO-SUCH-FILE.md"), "does not exist in the tree"),
        (_set("drift.file", "/etc/passwd"), "citations must be repo-relative"),
        (_set("drift.file", "../outside.md"), "resolves outside the repository root"),
        (_set("drift.line", 9999), "cited line 9999 is out of range"),
        (_set("drift.line", "four"), "expected an integer line number"),
        (_set("drift.quote", "the engine invents this sentence entirely"), "does not appear at"),
        (_set("drift.quote", "short"), "characters after whitespace normalization"),
        (
            _set("contradicts.file", "docs/SOME-GUIDE.md"),
            "is not a normative source",
        ),
        (_set("contradicts.line", 1), "the quote does not appear at"),
    ],
    ids=[
        "missing_drift_side",
        "missing_why",
        "off_vocabulary_severity",
        "class_mismatch",
        "malformed_id",
        "stub_title",
        "stub_why",
        "cited_file_absent",
        "absolute_path",
        "path_escapes_tree",
        "line_out_of_range",
        "non_integer_line",
        "fabricated_quote",
        "quote_too_short",
        "non_normative_contradicts",
        "quote_at_wrong_line",
    ],
)
def test_rejects_unshaped_findings_naming_the_reason(
    checker, repo, capsys, mutator, expected_fragment
):
    _write(repo, _mutate(mutator))
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert expected_fragment in _report(repo)


def test_rejects_a_finding_that_cites_the_same_file_on_both_sides(checker, repo, capsys):
    """A finding names a drifting surface AND the separate passage it moved from."""
    corpus = _corpus()
    finding = corpus["core-docs"]["findings"][0]
    finding["drift"] = copy.deepcopy(finding["contradicts"])
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert "are the same file" in _report(repo)


def test_rejects_duplicate_finding_ids_across_classes(checker, repo, capsys):
    """Ids are the handle a human triages by; a collision silently merges two."""
    corpus = _corpus()
    clash = copy.deepcopy(corpus["core-docs"]["findings"][0])
    clash["class"] = "examples"
    corpus["examples"]["findings"] = [clash]
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert "is used more than once" in _report(repo)


@pytest.mark.parametrize(
    ("body", "expected_fragment"),
    [
        ({"class": "core-docs", "findings": []}, "'swept' must be a non-empty list"),
        ({"class": "core-docs", "swept": [], "findings": []}, "'swept' must be a non-empty list"),
        (
            {"class": "core-docs", "swept": ["docs/GONE.md"], "findings": []},
            "does not exist in the tree",
        ),
        ({"class": "wrong", "swept": ["README.md"], "findings": []}, "'class' must be"),
        (
            {"class": "core-docs", "swept": ["README.md"], "findings": {}},
            "'findings' must be a list",
        ),
        ("{ not json", "not valid JSON"),
        ("[]", "expected a JSON object"),
    ],
    ids=[
        "swept_absent",
        "swept_empty",
        "swept_path_absent",
        "class_label_wrong",
        "findings_not_a_list",
        "malformed_json",
        "not_an_object",
    ],
)
def test_rejects_malformed_class_files(checker, repo, capsys, body, expected_fragment):
    corpus = _corpus()
    corpus["core-docs"] = body
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert expected_fragment in _report(repo)


def test_a_reviewer_that_wrote_nothing_is_named_not_silently_skipped(checker, repo, capsys):
    """A crashed branch must not read as a clean class."""
    corpus = _corpus()
    del corpus["guidance"]
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    report = _report(repo)
    assert "guidance.json: not written" in report
    assert "produced no output at all" in report


def test_a_rejected_round_removes_a_stale_findings_file(checker, repo, capsys):
    """The report gate reads findings.json; a stale corpus would be believed."""
    _write(repo, _corpus())
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"
    findings_path = repo / ".drift-review" / "findings.json"
    assert findings_path.exists()

    _write(repo, _mutate(_set("drift.line", 9999)))
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert not findings_path.exists()


def test_the_repair_loop_is_bounded_and_exhaustion_is_a_distinct_token(checker, repo, capsys):
    """Three malformed rounds at --max-revisions 2 is a decision point, not a retry."""
    _write(repo, _mutate(_set("drift.line", 9999)))
    for _ in range(2):
        assert _run(checker, repo, max_revisions=2) == 0
        assert capsys.readouterr().out == "findings_bad"
    assert _run(checker, repo, max_revisions=2) == 0
    assert capsys.readouterr().out == "revise_exhausted"


# ---------------------------------------------------------------------------
# Cannot-run is not the same as bad-findings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("broken", ["raw_dir", "repo_root"], ids=["raw_dir", "repo_root"])
def test_a_gate_that_cannot_run_exits_nonzero_with_no_token(checker, repo, capsys, broken):
    """Nonzero exit routes to the loud terminal, not into the repair loop."""
    _write(repo, _corpus())
    argv = [
        "--raw-dir", str(repo / ".drift-review" / "raw"),
        "--repo-root", str(repo),
        "--state-dir", str(repo / ".drift-review"),
    ]
    index = argv.index("--raw-dir" if broken == "raw_dir" else "--repo-root") + 1
    argv[index] = str(repo / "no-such-directory")
    assert checker.main(argv) != 0
    assert capsys.readouterr().out == ""


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

    # Exactly one exit, and it is reachable only through the report gate.
    exits = [n for n in graph.nodes.values() if n.is_exit_node()]
    assert len(exits) == 1
    into_exit = graph.incoming_edges(exits[0].id)
    assert [e.from_node for e in into_exit] == ["report_gate"]

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
    assert "check_findings.py" in str(graph.nodes["findings_gate"].attrs.get("tool_command"))

    # Both corrective cycles exist: repair findings, and repair the report.
    pairs = {(e.from_node, e.to_node) for e in graph.edges}
    assert ("findings_gate", "revise") in pairs
    assert ("revise", "findings_gate") in pairs
    assert ("report_gate", "consolidate") in pairs
    assert ("consolidate", "report_gate") in pairs

    # Every reviewer carries an artifact contract that success cannot declare away.
    for cls in _CLASSES:
        node_id = "review_" + cls.replace("-", "_")
        assert graph.nodes[node_id].attrs.get("must_write") == f".drift-review/raw/{cls}.json"

    # No outcome=fail edge may reach the exit: a machinery failure leaves loudly.
    for edge in graph.edges:
        if edge.to_node == exits[0].id:
            assert "outcome=fail" not in str(edge.condition or "")
