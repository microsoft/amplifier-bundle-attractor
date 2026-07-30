"""Sweep the shipped examples/ corpus through lint() — no ERRORs allowed.

This is the enforcement arm for the examples corpus: the dead-corrective-edge
bug class (TOPO-001) shipped in 8 examples for months because nothing could
see topology.  This test keeps the corpus honest — if a future example
reintroduces a dead diamond edge or a stale-label collision, the suite goes
red at author time instead of the bug shipping.

WARNING-severity diagnostics are allowed: deliberately linear examples
(TOPO-003) and LLM-gated loops (TOPO-004/005) are legitimate patterns and
warnings are informational.  ERROR-severity diagnostics are provable defects.

The examples tree lives at the repository root, outside the installed
package, so this test runs against a source checkout and skips gracefully
when the examples directory is not present (e.g. installed-package test runs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES_DIR = _REPO_ROOT / "examples"


def _example_dot_files() -> list[Path]:
    if not _EXAMPLES_DIR.is_dir():
        return []
    return sorted(_EXAMPLES_DIR.rglob("*.dot"))


_DOT_FILES = _example_dot_files()


@pytest.mark.skipif(
    not _DOT_FILES,
    reason="examples/ directory not present (installed-package run)",
)
@pytest.mark.parametrize(
    "dot_path",
    _DOT_FILES,
    ids=lambda p: str(p.relative_to(_EXAMPLES_DIR)),
)
def test_example_lints_without_errors(dot_path: Path) -> None:
    """Every shipped example must be free of ERROR-severity lint findings."""
    graph = parse_dot(dot_path.read_text(encoding="utf-8"))
    diags = lint(graph)
    errors = [d for d in diags if d.severity == "ERROR"]
    assert not errors, (
        f"{dot_path.relative_to(_REPO_ROOT)} has ERROR-severity lint findings:\n"
        + "\n".join(f"  [{d.rule}] {d.message}" for d in errors)
    )
