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

# --- Relocated (majority) from modules/loop-pipeline/tests/test_drift_review_gate.py as part of the repo split's Track A (root guard
# harness, DESIGN-repo-split.md §1.4/§5#2). This half asserts on the OPINIONATED
# layer (examples/drift-review/check_findings.py and its corpus fixtures) via
# the checker's own in-process logic -- no engine import -- so it runs from the
# repo-root `tests/` suite. The report_gate shell-command end-to-end tests, the
# shipped-graph structural contract test (which uses parse_dot + lint), and the
# dead-end/routing checks stayed with the engine (test_drift_review_gate.py in
# modules/loop-pipeline/tests/). ---

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = (
    Path(__file__).resolve().parent.parent
)  # <repo_root>/tests/ -> parent.parent
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

#: NOT in NORMATIVE_PREFIXES -- this is where the traversal cases try to land.
_QUALITY_PROTOCOL = """# Quality Protocol

## 5. Drift layers
Layer 3 is the periodic holistic semantic review of the whole repository.
Filler line.
"""

#: README carries a real sentence so a traversal case can quote it at file:line.
_README = """# Readme

The repository ships a convergence loop exemplar for new authors.
"""


#: The inventory the `inventory` node writes before any reviewer runs -- and the
#: other half of every coverage number ``check_findings.py`` reports. Class
#: membership here mirrors the shipped graph's own `git ls-files` patterns:
#: `docs/*` and the root convention files are core-docs, `examples/` is
#: examples, `agents|context|...` is guidance, `SPEC_CONFORMANCE.md|specs/` is
#: ledgers. `examples` is deliberately the biggest class, because that is the
#: one the first live run under-swept.
_INVENTORY: dict[str, tuple[str, ...]] = {
    "core-docs": (
        "README.md",
        "docs/SOME-GUIDE.md",
        "docs/VISION.md",
        "docs/QUALITY_PROTOCOL.md",
    ),
    "examples": (
        "examples/00-loop.dot",
        "examples/00-loop.md",
        "examples/01-linear.dot",
        "examples/01-linear.md",
    ),
    "guidance": ("agents/reviewer.md", "context/engine-semantics.md"),
    "ledgers": ("SPEC_CONFORMANCE.md", "specs/canonical/spec.md"),
}

#: Filler for the inventoried files that carry no citation of their own.
_FILLER = "# placeholder\n\nA line of real text so a citation could resolve here.\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tiny tree with one drifting surface, two normative sources, and an inventory."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "specs" / "canonical").mkdir(parents=True)
    (root / "docs" / "SOME-GUIDE.md").write_text(_DRIFTING_DOC, encoding="utf-8")
    (root / "docs" / "VISION.md").write_text(_VISION, encoding="utf-8")
    (root / "docs" / "QUALITY_PROTOCOL.md").write_text(
        _QUALITY_PROTOCOL, encoding="utf-8"
    )
    (root / "specs" / "canonical" / "spec.md").write_text(
        _NORMATIVE_SPEC, encoding="utf-8"
    )
    (root / "README.md").write_text(_README, encoding="utf-8")

    for paths in _INVENTORY.values():
        for rel in paths:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(_FILLER, encoding="utf-8")

    _write_inventory(root, _INVENTORY)
    return root


def _write_inventory(repo: Path, inventory: dict[str, tuple[str, ...]]) -> Path:
    """Write the per-class surface lists exactly as the `inventory` node does."""
    directory = repo / ".drift-review" / "inventory"
    directory.mkdir(parents=True, exist_ok=True)
    for cls, paths in inventory.items():
        (directory / f"{cls}.txt").write_text(
            "\n".join(sorted(paths)) + "\n", encoding="utf-8"
        )
    return directory


def _coverage(repo: Path) -> str:
    return (repo / ".drift-review" / "coverage.txt").read_text(encoding="utf-8")


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
    """One admissible raw file per class: findings on core-docs, clean elsewhere.

    Every class sweeps its whole inventory, so the baseline is 100% coverage
    and a mutation below can only be moving the number it means to move.
    """
    corpus: dict[str, dict] = {
        "core-docs": {
            "class": "core-docs",
            "swept": list(_INVENTORY["core-docs"]),
            "findings": [_good_finding()],
        }
    }
    for cls in _CLASSES[1:]:
        corpus[cls] = {"class": cls, "swept": list(_INVENTORY[cls]), "findings": []}
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
            "--raw-dir",
            str(repo / ".drift-review" / "raw"),
            "--repo-root",
            str(repo),
            "--state-dir",
            str(repo / ".drift-review"),
            "--classes",
            ",".join(_CLASSES),
            "--max-revisions",
            str(max_revisions),
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

    written = json.loads(
        (repo / ".drift-review" / "findings.json").read_text(encoding="utf-8")
    )
    assert written["finding_count"] == 1
    assert written["findings"][0]["id"] == "DR-CORE-001"
    # The swept record survives into the corpus: "clean" and "never looked" must
    # stay distinguishable downstream.
    assert written["swept"]["guidance"] == list(_INVENTORY["guidance"])
    assert "FINDINGS ADMITTED" in _report(repo)


def test_zero_findings_is_a_result_not_a_failure(checker, repo, capsys):
    corpus = {
        cls: {"class": cls, "swept": list(_INVENTORY[cls]), "findings": []}
        for cls in _CLASSES
    }
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"
    written = json.loads(
        (repo / ".drift-review" / "findings.json").read_text(encoding="utf-8")
    )
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
        (
            _set("severity", "spicy"),
            "is not one of ['critical', 'high', 'medium', 'low']",
        ),
        (_set("class", "examples"), "does not match this file's class 'core-docs'"),
        (_set("id", "x"), "is not a short slug matching"),
        (_set("title", "short"), "at least 12 characters"),
        (_set("why", "too short"), "at least 40 characters"),
        (_set("drift.file", "docs/NO-SUCH-FILE.md"), "does not exist in the tree"),
        (_set("drift.file", "/etc/passwd"), "citations must be repo-relative"),
        (_set("drift.file", "../outside.md"), "resolves outside the repository root"),
        (_set("drift.line", 9999), "cited line 9999 is out of range"),
        (_set("drift.line", "four"), "expected an integer line number"),
        (
            _set("drift.quote", "the engine invents this sentence entirely"),
            "does not appear at",
        ),
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


def test_rejects_a_finding_that_cites_the_same_file_on_both_sides(
    checker, repo, capsys
):
    """A finding names a drifting surface AND the separate passage it moved from."""
    corpus = _corpus()
    finding = corpus["core-docs"]["findings"][0]
    finding["drift"] = copy.deepcopy(finding["contradicts"])
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert "are the same file" in _report(repo)


# ---------------------------------------------------------------------------
# Path traversal -- the closed set is only as closed as the path it tests
# ---------------------------------------------------------------------------
#
# Both cases below were REPRODUCED as admissions by an adversarial review while
# `check_finding` tested the raw citation STRING. `resolve_in_tree` now hands
# back the resolved repo-relative path and both rules judge that instead.


_QP_QUOTE = "Layer 3 is the periodic holistic semantic review of the whole repository."
_README_QUOTE = "The repository ships a convergence loop exemplar for new authors."


def test_rejects_a_contradicts_citation_that_traverses_out_of_the_normative_set(
    checker, repo, capsys
):
    """`specs/canonical/../../docs/QUALITY_PROTOCOL.md` satisfies startswith(), lands outside.

    Pre-fix this was ADMITTED: the raw string begins with `specs/canonical/`, so
    the closed-set test passed while the citation pointed at a doc that is not
    normative at all -- which would let a Layer-3 finding measure drift against
    a surface that is itself drifting.
    """
    corpus = _corpus()
    corpus["core-docs"]["findings"][0]["contradicts"] = {
        "file": "specs/canonical/../../docs/QUALITY_PROTOCOL.md",
        "line": 4,
        "quote": _QP_QUOTE,
    }
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    report = _report(repo)
    assert "is not a normative source" in report
    # And it names the traversal, so the human triaging the report sees the dodge
    # rather than a confusing complaint about a path that looks compliant.
    assert "which resolves to 'docs/QUALITY_PROTOCOL.md'" in report


def test_rejects_the_same_file_dodged_through_a_traversal(checker, repo, capsys):
    """`README.md` vs `specs/canonical/../../README.md`: two strings, one file.

    Pre-fix this was ADMITTED twice over -- the raw strings differ, so the
    different-files rule passed, and the second one begins with
    `specs/canonical/`, so the closed-set rule passed too. A finding could
    therefore cite one file against itself and call it drift.
    """
    corpus = _corpus()
    finding = corpus["core-docs"]["findings"][0]
    finding["drift"] = {"file": "README.md", "line": 3, "quote": _README_QUOTE}
    finding["contradicts"] = {
        "file": "specs/canonical/../../README.md",
        "line": 3,
        "quote": _README_QUOTE,
    }
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert "are the same file" in _report(repo)


def test_the_different_files_rule_holds_inside_the_normative_set_too(
    checker, repo, capsys
):
    """A traversal that lands back on the same *normative* file: only one rule can fire.

    Both sides resolve to `specs/canonical/spec.md`, so the closed-set rule is
    satisfied and cannot be what rejects this. That isolates the different-files
    half of the fix from the closed-set half.
    """
    corpus = _corpus()
    finding = corpus["core-docs"]["findings"][0]
    spec_cite = {
        "file": "specs/canonical/spec.md",
        "line": 12,
        "quote": "No matching edge is a hard failure and the run stops loudly.",
    }
    finding["drift"] = copy.deepcopy(spec_cite)
    finding["contradicts"] = copy.deepcopy(spec_cite)
    finding["contradicts"]["file"] = "specs/canonical/../canonical/spec.md"
    _write(repo, corpus)
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    report = _report(repo)
    assert "are the same file" in report
    assert "is not a normative source" not in report


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
        (
            {"class": "core-docs", "swept": [], "findings": []},
            "'swept' must be a non-empty list",
        ),
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


def test_a_reviewer_that_wrote_nothing_is_named_not_silently_skipped(
    checker, repo, capsys
):
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


def test_the_repair_loop_is_bounded_and_exhaustion_is_a_distinct_token(
    checker, repo, capsys
):
    """Three malformed rounds at --max-revisions 2 is a decision point, not a retry."""
    _write(repo, _mutate(_set("drift.line", 9999)))
    for _ in range(2):
        assert _run(checker, repo, max_revisions=2) == 0
        assert capsys.readouterr().out == "findings_bad"
    assert _run(checker, repo, max_revisions=2) == 0
    assert capsys.readouterr().out == "revise_exhausted"


# ---------------------------------------------------------------------------
# Coverage: `swept` reconciled against the inventory (issue #244)
#
# `swept` used to be an attestation nothing ever checked. A reviewer that read
# half its class and reported half its class passed every rule, and the run
# published a clean four-class sweep -- the first live run swept 62 of 114
# inventoried `examples` files and said "129 surfaces swept", a number larger
# than the honest one. These cases pin the three ways that number lied.
# ---------------------------------------------------------------------------


def test_an_under_sweep_is_measured_named_and_published(checker, repo, capsys):
    """RED before: 1-of-4 was indistinguishable from 4-of-4. Now it is on the record.

    Deliberately NOT a rejection. The gate can compare the array to the
    inventory; it cannot check the reading, so a pass/fail bar there would buy
    a full-looking array rather than a full sweep. An honest partial sweep is a
    fine outcome -- an unmarked one is not.
    """
    corpus = _corpus()
    corpus["examples"]["swept"] = ["examples/00-loop.dot"]
    _write(repo, corpus)

    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"

    report = _report(repo)
    assert "examples: 1/4 (25%)" in report
    assert "NOT swept (3)" in report
    assert "examples/01-linear.dot" in report

    # The honest fraction rides into the contract file the report must carry,
    # so the deliverable cannot publish a different number than the record.
    assert "examples: 1/4 (25%)" in _coverage(repo)

    # And the headline is the in-class total against the inventory, never the
    # sum of the array lengths.
    assert "swept:    9 of 12 inventoried in-class surfaces (75%)" in report


def test_out_of_class_reads_are_flagged_not_counted_as_swept_surfaces(
    checker, repo, capsys
):
    """RED before: reading the spec for context inflated the class's own count.

    Every reviewer is TOLD to open the canonical spec, the vision and the
    ledgers before it reads for drift, so these paths belong in `swept`. They
    are simply not surfaces of the class under review, and counting them as
    such is what turned 118 real surfaces into a reported 129.
    """
    corpus = _corpus()
    corpus["examples"]["swept"] = [*_INVENTORY["examples"], "specs/canonical/spec.md"]
    _write(repo, corpus)

    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"

    report = _report(repo)
    assert "examples: 4/4 (100%)" in report, report
    assert "read but out of class (1" in report
    assert "specs/canonical/spec.md" in report
    assert "1 out-of-class source(s) read" in report

    written = json.loads(
        (repo / ".drift-review" / "findings.json").read_text(encoding="utf-8")
    )
    examples = next(c for c in written["coverage"] if c["class"] == "examples")
    assert examples["in_class"] == 4
    assert examples["out_of_class"] == ["specs/canonical/spec.md"]


def test_a_duplicated_path_is_counted_once_and_named(checker, repo, capsys):
    """RED before: `context/engine-semantics.md` twice counted twice.

    Bookkeeping rather than dishonesty -- and it inflated the headline just as
    effectively, so it is deduplicated and reported rather than ignored.
    """
    corpus = _corpus()
    corpus["guidance"]["swept"] = [
        *_INVENTORY["guidance"],
        "context/engine-semantics.md",
    ]
    _write(repo, corpus)

    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"

    report = _report(repo)
    assert "guidance: 2/2 (100%)" in report
    assert "listed more than once (1, counted once)" in report
    assert "1 duplicate entr" in report

    written = json.loads(
        (repo / ".drift-review" / "findings.json").read_text(encoding="utf-8")
    )
    guidance = next(c for c in written["coverage"] if c["class"] == "guidance")
    assert guidance["reported"] == 3
    assert guidance["in_class"] == 2
    assert guidance["duplicates"] == ["context/engine-semantics.md"]


def test_a_class_that_swept_none_of_its_own_inventory_is_rejected(
    checker, repo, capsys
):
    """The one coverage rule with teeth -- and it is the rule that was already there.

    "A class must say what it swept" was always the contract; it was measured
    against the array's length rather than against the class. A reviewer whose
    whole array is out-of-class context files read nothing of its own class,
    and that is not a partial sweep, it is an absent one.
    """
    corpus = _corpus()
    corpus["examples"]["swept"] = ["specs/canonical/spec.md", "docs/VISION.md"]
    _write(repo, corpus)

    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"

    report = _report(repo)
    assert "NONE of them are in" in report
    assert "examples.txt" in report
    assert "no review of 'examples' to report" in report


def test_the_arithmetic_matches_the_first_live_run(checker):
    """Re-derived from the archived 2026-08-15 run, not carried over from prose.

    `examples`: 68 reported entries over a 114-file inventory, of which 62 were
    in class and 6 were normative sources read for context -- so 52 files were
    never opened and the report said nothing. This pins the arithmetic that
    turns that into a published `62/114 (54%)`.
    """
    inventory = {f"examples/f{i}.md" for i in range(114)}
    swept = [f"examples/f{i}.md" for i in range(62)] + [
        "specs/canonical/attractor-spec-canonical.md",
        "docs/VISION.md",
        "specs/EXTENSIONS.md",
        "SPEC_CONFORMANCE.md",
        "docs/QUALITY_PROTOCOL.md",
        "docs/PIPELINE_PATTERNS.md",
    ]
    entry = checker.class_coverage("examples", swept, inventory)

    assert entry["reported"] == 68
    assert entry["inventory"] == 114
    assert entry["in_class"] == 62
    assert len(entry["out_of_class"]) == 6
    assert entry["duplicates"] == []
    assert len(entry["unswept"]) == 52
    assert checker.coverage_line(entry) == "examples: 62/114 (54%)"


def test_the_unswept_listing_is_bounded_but_the_count_never_is(checker):
    """A 52-file gap must be legible without burying the rest of the report."""
    inventory = {f"examples/f{i}.md" for i in range(60)}
    entry = checker.class_coverage("examples", ["examples/f0.md"], inventory)
    lines = checker.coverage_report_lines([entry])
    body = "\n".join(lines)

    assert "NOT swept (59)" in body
    assert f"and {59 - checker.MAX_UNSWEPT_NAMED} more" in body


def test_coverage_is_reported_on_a_rejected_round_too(checker, repo, capsys):
    """The postmortem reads findings-report.txt; coverage is evidence, not a reward."""
    corpus = _mutate(_set("drift.line", 9999))
    corpus["examples"]["swept"] = ["examples/00-loop.dot"]
    _write(repo, corpus)

    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert "examples: 1/4 (25%)" in _report(repo)


def test_a_rejected_round_removes_a_stale_coverage_contract(checker, repo, capsys):
    """report_gate holds report.md to coverage.txt, so a stale copy would admit
    this round's report against last round's numbers."""
    _write(repo, _corpus())
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_ok"
    coverage_path = repo / ".drift-review" / "coverage.txt"
    assert coverage_path.is_file()

    _write(repo, _mutate(_set("drift.line", 9999)))
    assert _run(checker, repo) == 0
    assert capsys.readouterr().out == "findings_bad"
    assert not coverage_path.exists()


def test_a_missing_inventory_is_a_machinery_failure_not_a_verdict(
    checker, repo, capsys
):
    """Reconciliation without the inventory is not reconciliation.

    Degrading quietly back to attestation-only is precisely the state issue
    #244 describes, so its absence exits nonzero with no token and routes to
    the loud terminal -- the same treatment a missing raw directory gets.
    """
    _write(repo, _corpus())
    (repo / ".drift-review" / "inventory" / "examples.txt").unlink()

    assert _run(checker, repo) != 0
    assert capsys.readouterr().out == ""


def test_an_empty_inventory_class_is_also_a_machinery_failure(checker, repo, capsys):
    """An empty list would make every class trivially 0/0 (0%) and say nothing."""
    _write(repo, _corpus())
    (repo / ".drift-review" / "inventory" / "guidance.txt").write_text(
        "\n", encoding="utf-8"
    )

    assert _run(checker, repo) != 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Cannot-run is not the same as bad-findings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken", ["raw_dir", "repo_root"], ids=["raw_dir", "repo_root"]
)
def test_a_gate_that_cannot_run_exits_nonzero_with_no_token(
    checker, repo, capsys, broken
):
    """Nonzero exit routes to the loud terminal, not into the repair loop."""
    _write(repo, _corpus())
    argv = [
        "--raw-dir",
        str(repo / ".drift-review" / "raw"),
        "--repo-root",
        str(repo),
        "--state-dir",
        str(repo / ".drift-review"),
    ]
    index = argv.index("--raw-dir" if broken == "raw_dir" else "--repo-root") + 1
    argv[index] = str(repo / "no-such-directory")
    assert checker.main(argv) != 0
    assert capsys.readouterr().out == ""
