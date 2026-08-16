"""Calibration guard for TOPO-008 (``inert_evidence_gate``) -- issue #254 item 2.

A doctrine rule that over-fires teaches authors to route around it, so this
rule was **measured before it was shipped**, not reasoned about.  The general
"two distinct tokens into ANY node" form was REJECTED on that measurement: it
fires on this repository's own deliberate ``.github/`` capsule patterns, where
several distinct diagnoses converge on one node that WRITES THEM UP rather
than routes on them.  Narrowing the rule to the EXIT -- where every answer
ends the run green and there is no such reading -- brought it to zero.

This file pins that measurement so it cannot silently rot:

  * **the calibration set is green** -- every ``.dot`` in the repository
    (``examples/``, ``.github/``, ``skills/``, and the test fixtures under
    ``modules/`` and ``tests/``) is free of ``inert_evidence_gate``;
  * **the rule is not vacuous** -- it fires on the issue-#245 B1 construction,
    proven in ``test_topological_lint.py::TestInertEvidenceGate``;
  * **the two layers agree** -- ``attractor lint``'s TOPO-008 and the authoring
    checker's A10 return the same verdict on every shipped graph, so a graph
    cannot pass one layer and fail the other.

The repository tree lives above the installed package, so these tests run
against a source checkout and skip gracefully when it is absent (the same
pattern as ``test_examples_lint_clean.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTHORING_CHECKER = (
    _REPO_ROOT / "examples" / "authoring" / "check_authored_pipeline.py"
)

#: Directories whose ``.dot`` files this repository ships or tests against.
#: Named explicitly rather than globbing the root so a stray scratch file in
#: an untracked directory cannot quietly widen or narrow the calibration set.
_SWEPT_DIRS = ("examples", ".github", "skills", "modules", "tests")


def _repo_dot_files() -> list[Path]:
    if not (_REPO_ROOT / "examples").is_dir():
        return []
    found: list[Path] = []
    for rel in _SWEPT_DIRS:
        root = _REPO_ROOT / rel
        if root.is_dir():
            found.extend(root.rglob("*.dot"))
    return sorted(found)


_DOT_FILES = _repo_dot_files()

pytestmark = pytest.mark.skipif(
    not _DOT_FILES,
    reason="repository .dot corpus not present (installed-package run)",
)


@pytest.mark.parametrize(
    "dot_path",
    _DOT_FILES,
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_shipped_graph_is_free_of_inert_evidence_gates(dot_path: Path) -> None:
    """No graph in this repository routes two answers into its own exit."""
    diags = lint(parse_dot(dot_path.read_text(encoding="utf-8")))
    hits = [d for d in diags if d.rule == "inert_evidence_gate"]
    assert not hits, (
        f"{dot_path.relative_to(_REPO_ROOT)} tripped TOPO-008:\n"
        + "\n".join(f"  {d.message}" for d in hits)
    )


def test_the_calibration_set_is_not_empty() -> None:
    """A sweep over nothing proves nothing.

    ``test_examples_lint_clean.py`` learned this the hard way: a parametrised
    sweep that collects zero files is a green test that measures nothing.
    """
    assert len(_DOT_FILES) >= 50, (
        f"calibration set collapsed to {len(_DOT_FILES)} files"
    )


def _load_authoring_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_calib_check_authored_pipeline", _AUTHORING_CHECKER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    not _AUTHORING_CHECKER.is_file(),
    reason="examples/authoring/check_authored_pipeline.py not present",
)
def test_topo_008_and_a10_agree_on_every_shipped_graph() -> None:
    """The two layers must not disagree about the same graph.

    TOPO-008 reuses A10's reach semantics deliberately.  If they ever drift,
    a hand-authored graph could clear ``attractor lint`` and then be rejected
    by the authoring gate (or the reverse) on the identical shape -- which is
    exactly the confusion having one rule in two places is meant to avoid.
    """
    a10 = _load_authoring_checker()

    disagreements: list[str] = []
    for dot_path in _DOT_FILES:
        text = dot_path.read_text(encoding="utf-8")
        topo8 = bool(
            [d for d in lint(parse_dot(text)) if d.rule == "inert_evidence_gate"]
        )
        a10_hit = bool(a10.inert_gate_routes(a10.parse_dot_min(text)))
        if topo8 != a10_hit:
            disagreements.append(
                f"{dot_path.relative_to(_REPO_ROOT)}: TOPO-008={topo8}, A10={a10_hit}"
            )

    assert not disagreements, "lint and the authoring checker disagree:\n" + "\n".join(
        disagreements
    )
