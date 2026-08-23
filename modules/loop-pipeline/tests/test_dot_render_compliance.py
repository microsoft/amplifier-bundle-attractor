"""Render-compliance tests -- RENDER-001, RENDER-002, and the corpus render sweep.

A `.dot` file is read by TWO validators with different strictness: this
engine's ``dot_parser`` (lenient) and ``dot -Tsvg`` (strict, the real GraphViz
grammar).  They disagree.  Measured on this repository at ``696c080``, six of
sixty-three git-tracked ``.dot`` files parsed and linted cleanly while failing
``dot -Tsvg`` with a syntax error.  They ran fine; they just could not be drawn.

This module holds both halves of the fix:

*Lint-rule unit tests* -- RENDER-001 (unescaped inner quote) and RENDER-002
(dotted bare identifier) fire on the bad shapes, stay silent on the corrected
ones, and are **WARNING** severity, never ERROR.  The severity assertion is
load-bearing, not decorative: a non-rendering graph is CONFORMING to the
runtime contract (this parser accepts it, the engine runs it), so promoting
renderability to an ERROR would break a community author's working pipeline.
See ``docs/designs/2026-08-23-dot-render-compliance.md``.

*Render sweep* -- every git-tracked ``*.dot`` must satisfy ``dot -Tsvg`` with
exit 0.  This is the "CI never surprises us" guard: it goes red locally the
moment any shipped ``.dot`` stops rendering.  It SKIPS with an explicit reason
when the ``dot`` binary is not on PATH -- a developer without GraphViz sees a
skip, never a false green.  CI installs graphviz, so in CI it always runs.
``test_render_sweep_detects_a_planted_broken_file`` keeps that skip honest by
proving the sweep's detector is not vacuously true.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import Diagnostic, lint

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Path prefixes excluded from the render sweep and from the CI gate that mirrors
# it.  These hold runtime scratch and captured artifacts, not authored corpus.
# Kept identical to `.github/workflows/ci.yml`'s `dot-render-gate` step -- if
# one list changes the other must too.
_SWEEP_EXCLUDED_PREFIXES = (".ai/", ".amplifier/", "evals/")

# `dot` is the *renderer*, not a test dependency.  Absence must skip loudly.
_DOT_BIN = shutil.which("dot")
_NO_DOT_REASON = (
    "graphviz `dot` is not on PATH -- the render sweep cannot run here. "
    "Install graphviz (`apt-get install graphviz`) to exercise it locally; "
    "CI installs it, so this test always runs there."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_findings(dot_source: str) -> list[Diagnostic]:
    """Lint ``dot_source`` and return only the RENDER-family diagnostics."""
    return [d for d in lint(parse_dot(dot_source)) if d.rule.startswith("RENDER-")]


def _wrap(body: str) -> str:
    """Wrap an attribute-block fragment in a minimal valid pipeline graph."""
    return (
        "digraph t {\n"
        "    start [shape=Mdiamond]\n"
        f"    {body}\n"
        "    done [shape=Msquare]\n"
        "    start -> work\n"
        "    work -> done\n"
        "}\n"
    )


def _renders(path: Path) -> tuple[bool, str]:
    """Run ``dot -Tsvg`` on ``path``.  Returns ``(exit_zero, stderr)``."""
    assert _DOT_BIN is not None, "caller must gate on _DOT_BIN"
    proc = subprocess.run(
        [_DOT_BIN, "-Tsvg", str(path), "-o", "/dev/null"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # a non-zero exit IS the signal -- never raise on it
    )
    return proc.returncode == 0, proc.stderr.strip()


def _tracked_dot_files() -> list[Path]:
    """Every git-tracked ``*.dot`` in the repo, minus the sweep exclusions.

    Git-tracked is the contract, deliberately: a developer's untracked scratch
    ``.dot`` in the working tree must never turn this suite red.
    """
    if not (_REPO_ROOT / ".git").exists():
        return []
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.dot"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,  # handled explicitly below
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [
        _REPO_ROOT / rel
        for rel in out.stdout.split()
        if not rel.startswith(_SWEEP_EXCLUDED_PREFIXES)
    ]


_TRACKED_DOT_FILES = _tracked_dot_files()


# ---------------------------------------------------------------------------
# The six known-bad shapes, each paired with its corrected form.
#
# These are the literal shapes that shipped non-rendering in this repository.
# They are embedded as fixtures rather than read from git history so the RED
# proof survives the very commit that fixes the files.
# ---------------------------------------------------------------------------

_KNOWN_BAD_SHAPES: list[tuple[str, str, str, str]] = [
    (
        # examples/patterns/task-runner.dot:207
        "task-runner-inner-quote",
        "RENDER-001",
        """work [shape=parallelogram, tool_command="n=$(grep -c '"gate": "verify"' log.jsonl); printf $n"]""",
        """work [shape=parallelogram, tool_command="n=$(grep -c '\\"gate\\": \\"verify\\"' log.jsonl); printf $n"]""",
    ),
    (
        # examples/patterns/demo-combined.dot:15
        "demo-combined-context-gate-topic",
        "RENDER-002",
        """work [shape=folder, dot_file="c.dot", context.gate_topic="What artifact?"]""",
        """work [shape=folder, dot_file="c.dot", "context.gate_topic"="What artifact?"]""",
    ),
    (
        # examples/patterns/demo-convergence-factory.dot:15
        "demo-convergence-factory-context-artifact-goal",
        "RENDER-002",
        """work [shape=folder, dot_file="c.dot", context.artifact_goal="Create a file"]""",
        """work [shape=folder, dot_file="c.dot", "context.artifact_goal"="Create a file"]""",
    ),
    (
        # examples/patterns/demo-conversational-gates.dot:15
        "demo-conversational-gates-context-gate-topic",
        "RENDER-002",
        """work [shape=folder, dot_file="c.dot", context.gate_topic="Rate this project"]""",
        """work [shape=folder, dot_file="c.dot", "context.gate_topic"="Rate this project"]""",
    ),
    (
        # examples/pipelines/09-manager-supervisor.dot:40
        "manager-supervisor-max-cycles",
        "RENDER-002",
        """work [shape=house, prompt="Supervise", manager.max_cycles=5]""",
        """work [shape=house, prompt="Supervise", "manager.max_cycles"=5]""",
    ),
    (
        # examples/pipelines/11-manager-child-dotfile-hitl/parent.dot:55
        "parent-manager-max-cycles-and-child-dotfile",
        "RENDER-002",
        """work [shape=house, prompt="Supervise", manager.max_cycles=1, stack.child_dotfile="child.dot"]""",
        """work [shape=house, prompt="Supervise", "manager.max_cycles"=1, "stack.child_dotfile"="child.dot"]""",
    ),
]


# ---------------------------------------------------------------------------
# RENDER-001 -- unescaped inner quote
# ---------------------------------------------------------------------------


def test_render_001_fires_on_unescaped_inner_quote() -> None:
    """A raw `"` inside an attribute string closes it early -- RENDER-001 fires."""
    src = _wrap(
        """work [shape=parallelogram, tool_command="grep -c '"gate": "verify"' log"]"""
    )
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-001"]
    assert findings, "RENDER-001 did not fire on an unescaped inner double-quote"


def test_render_001_severity_is_warning_never_error() -> None:
    """A non-rendering graph is conforming and still runs -- advisory only."""
    src = _wrap(
        """work [shape=parallelogram, tool_command="grep -c '"gate": "verify"' log"]"""
    )
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-001"]
    assert findings
    assert all(d.severity == "WARNING" for d in findings), (
        "RENDER-001 must be WARNING: promoting renderability to an ERROR would "
        "break a conforming community graph that runs correctly today."
    )


def test_render_001_emits_a_fix_hint() -> None:
    src = _wrap(
        """work [shape=parallelogram, tool_command="grep -c '"gate": "verify"' log"]"""
    )
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-001"]
    assert all(d.fix for d in findings), (
        "every RENDER-001 finding must carry a fix hint"
    )


def test_render_001_silent_on_correctly_escaped_inner_quote() -> None:
    r"""A properly escaped ``\"`` is consumed inside the string token -- no finding."""
    src = _wrap(
        """work [shape=parallelogram, tool_command="grep -c '\\"gate\\": \\"verify\\"' log"]"""
    )
    assert not [d for d in _render_findings(src) if d.rule == "RENDER-001"]


def test_render_001_silent_on_string_abutting_the_edge_operator() -> None:
    """``"a"->"b"`` is legal DOT -- the `->` neighbour must not be flagged."""
    src = 'digraph t {\n    "start"->"done"\n    start [shape=Mdiamond]\n    done [shape=Msquare]\n}\n'
    assert not [d for d in _render_findings(src) if d.rule == "RENDER-001"]


# ---------------------------------------------------------------------------
# RENDER-002 -- dotted bare identifier
# ---------------------------------------------------------------------------


def test_render_002_fires_on_dotted_bare_key() -> None:
    """GraphViz's NAME production forbids `.` in a bare identifier."""
    src = _wrap("""work [shape=house, prompt="Supervise", manager.max_cycles=5]""")
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-002"]
    assert findings, "RENDER-002 did not fire on a bare dotted attribute key"
    assert "manager.max_cycles" in findings[0].message


def test_render_002_severity_is_warning_never_error() -> None:
    """The qualified-ident form is a deliberate engine extension -- advisory only."""
    src = _wrap("""work [shape=house, prompt="Supervise", manager.max_cycles=5]""")
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-002"]
    assert findings
    assert all(d.severity == "WARNING" for d in findings), (
        "RENDER-002 must be WARNING: `context.*` / `manager.*` / `stack.*` keys "
        "are load-bearing engine extensions that run correctly today."
    )


def test_render_002_emits_a_fix_hint() -> None:
    src = _wrap("""work [shape=house, prompt="Supervise", manager.max_cycles=5]""")
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-002"]
    assert all(d.fix for d in findings)
    assert '"manager.max_cycles"' in findings[0].fix


def test_render_002_silent_on_quoted_dotted_key() -> None:
    """Quoting the key is the fix -- it must not re-fire."""
    src = _wrap("""work [shape=house, prompt="Supervise", "manager.max_cycles"=5]""")
    assert not [d for d in _render_findings(src) if d.rule == "RENDER-002"]


@pytest.mark.parametrize("numeral", ["1.5", ".5", "-2.25", "3."])
def test_render_002_silent_on_legal_numerals(numeral: str) -> None:
    """A `.` inside a GraphViz NUMERAL is legal and must never be flagged."""
    src = _wrap(f"""work [shape=box, prompt="hi", weight={numeral}]""")
    assert not [d for d in _render_findings(src) if d.rule == "RENDER-002"], (
        f"RENDER-002 false-positive on the legal numeral {numeral}"
    )


def test_render_002_silent_on_dotted_key_mentioned_only_in_a_comment() -> None:
    """`parent.dot` documents the form in a comment -- documentation is not code.

    This is a real regression guard: the file that carried the defect also
    carried a `//` comment naming `manager.max_cycles`, so a rule scanning raw
    source would flag the prose instead of the attribute.
    """
    src = (
        "digraph t {\n"
        "    // DOT parser note: keys like manager.max_cycles must be quoted.\n"
        "    /* Wrong: stack.child_dotfile=x\n"
        '       Right: "stack.child_dotfile"=x */\n'
        "    start [shape=Mdiamond]\n"
        '    work [shape=house, prompt="Supervise", "manager.max_cycles"=1]\n'
        "    done [shape=Msquare]\n"
        "    start -> work\n"
        "    work -> done\n"
        "}\n"
    )
    assert not _render_findings(src)


def test_comment_blanking_preserves_reported_line_numbers() -> None:
    """A multi-line block comment must not shift the reported line number."""
    src = (
        "digraph t {\n"  # 1
        "    /* a\n"  # 2
        "       multi-line\n"  # 3
        "       comment */\n"  # 4
        "    start [shape=Mdiamond]\n"  # 5
        '    work [shape=house, prompt="s", manager.max_cycles=1]\n'  # 6
        "    done [shape=Msquare]\n"  # 7
        "    start -> work\n"
        "    work -> done\n"
        "}\n"
    )
    findings = [d for d in _render_findings(src) if d.rule == "RENDER-002"]
    assert findings
    assert "Line 6:" in findings[0].message, findings[0].message


# ---------------------------------------------------------------------------
# Clean graphs, and the no-source guard
# ---------------------------------------------------------------------------


def test_clean_graph_has_zero_render_findings() -> None:
    src = _wrap("""work [shape=box, prompt="Do the thing", label="Work"]""")
    assert _render_findings(src) == []


def test_render_rules_are_silent_without_dot_source() -> None:
    """A programmatically-built Graph has no source text -- inventing a finding would lie."""
    from amplifier_module_loop_pipeline.graph import Graph, Node

    graph = Graph(
        name="t",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[],
    )
    assert graph.dot_source == ""
    assert not [d for d in lint(graph) if d.rule.startswith("RENDER-")]


# ---------------------------------------------------------------------------
# The six known-bad shapes: each fires, each corrected form is clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape_id", "rule", "bad", "good"),
    _KNOWN_BAD_SHAPES,
    ids=[s[0] for s in _KNOWN_BAD_SHAPES],
)
def test_known_bad_shape_is_caught(
    shape_id: str, rule: str, bad: str, good: str
) -> None:
    """RED proof: every shape that actually shipped non-rendering must fire."""
    del shape_id, good
    findings = [d for d in _render_findings(_wrap(bad)) if d.rule == rule]
    assert findings, f"{rule} did not fire on a shape that shipped non-rendering"
    assert all(d.severity == "WARNING" for d in findings)


@pytest.mark.parametrize(
    ("shape_id", "rule", "bad", "good"),
    _KNOWN_BAD_SHAPES,
    ids=[s[0] for s in _KNOWN_BAD_SHAPES],
)
def test_known_bad_shape_corrected_is_clean(
    shape_id: str, rule: str, bad: str, good: str
) -> None:
    """GREEN proof: the applied fix silences the rule completely."""
    del shape_id, rule, bad
    assert _render_findings(_wrap(good)) == []


@pytest.mark.skipif(_DOT_BIN is None, reason=_NO_DOT_REASON)
@pytest.mark.parametrize(
    ("shape_id", "rule", "bad", "good"),
    _KNOWN_BAD_SHAPES,
    ids=[s[0] for s in _KNOWN_BAD_SHAPES],
)
def test_known_bad_shape_really_fails_graphviz(
    tmp_path: Path, shape_id: str, rule: str, bad: str, good: str
) -> None:
    """The rules are not guessing: GraphViz genuinely refuses the bad shapes.

    This is what ties the pure-lexer static check to the ground truth it
    claims to predict.  Without it, the rules could drift into flagging
    something GraphViz is perfectly happy with.
    """
    del rule
    bad_file = tmp_path / f"{shape_id}-bad.dot"
    good_file = tmp_path / f"{shape_id}-good.dot"
    bad_file.write_text(_wrap(bad), encoding="utf-8")
    good_file.write_text(_wrap(good), encoding="utf-8")

    bad_ok, _ = _renders(bad_file)
    good_ok, good_err = _renders(good_file)
    assert not bad_ok, f"{shape_id}: GraphViz accepted a shape the rule flags"
    assert good_ok, f"{shape_id}: corrected form still fails to render: {good_err}"


# ---------------------------------------------------------------------------
# Class B is semantically inert -- quoting a dotted key changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape_id", "rule", "bad", "good"),
    [s for s in _KNOWN_BAD_SHAPES if s[1] == "RENDER-002"],
    ids=[s[0] for s in _KNOWN_BAD_SHAPES if s[1] == "RENDER-002"],
)
def test_quoting_a_dotted_key_is_semantically_inert(
    shape_id: str, rule: str, bad: str, good: str
) -> None:
    """Bare and quoted dotted keys must parse to a byte- AND type-identical dict.

    ``_unquote_key`` strips the quotes without coercing types, so
    ``manager.max_cycles`` stays the int ``5``.  This is the assertion that
    makes the corpus fix safe to apply without re-testing every pipeline.
    """
    del shape_id, rule
    bare = parse_dot(_wrap(bad)).nodes["work"]
    quoted = parse_dot(_wrap(good)).nodes["work"]
    assert bare.attrs == quoted.attrs
    assert [(k, type(v)) for k, v in sorted(bare.attrs.items())] == [
        (k, type(v)) for k, v in sorted(quoted.attrs.items())
    ]


# ---------------------------------------------------------------------------
# Render sweep -- the "CI never surprises us" guard
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_DOT_BIN is None, reason=_NO_DOT_REASON)
@pytest.mark.skipif(
    not _TRACKED_DOT_FILES,
    reason="no git-tracked .dot files resolvable (installed-package run, or git unavailable)",
)
@pytest.mark.parametrize(
    "dot_path",
    _TRACKED_DOT_FILES,
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_shipped_dot_renders(dot_path: Path) -> None:
    """Every git-tracked ``*.dot`` must satisfy ``dot -Tsvg`` with exit 0.

    The spec chose DOT *because* it renders -- "free visualization,
    PR-reviewable".  A shipped graph that cannot be drawn has stopped paying
    that rent.  This test goes red the moment that happens again.
    """
    ok, err = _renders(dot_path)
    assert ok, (
        f"{dot_path.relative_to(_REPO_ROOT)} does not render:\n  {err}\n"
        f"Run `attractor lint` on it -- RENDER-001/RENDER-002 name the cause "
        f"and the fix.  See docs/DOT-AUTHORING-GUIDE.md."
    )


@pytest.mark.skipif(_DOT_BIN is None, reason=_NO_DOT_REASON)
def test_render_sweep_detects_a_planted_broken_file(tmp_path: Path) -> None:
    """Keeps the sweep honest: prove the detector is not vacuously true.

    Without this, a bug in ``_renders`` (wrong flag, swallowed exit code)
    would make ``test_shipped_dot_renders`` pass unconditionally and look
    exactly like coverage.
    """
    planted = tmp_path / "planted-bad.dot"
    planted.write_text(
        _wrap("""work [shape=house, prompt="Supervise", manager.max_cycles=1]"""),
        encoding="utf-8",
    )
    ok, err = _renders(planted)
    assert not ok, "the render sweep's detector accepted a file GraphViz rejects"
    assert "syntax error" in err.lower(), err


@pytest.mark.skipif(_DOT_BIN is None, reason=_NO_DOT_REASON)
@pytest.mark.skipif(
    not _TRACKED_DOT_FILES,
    reason="no git-tracked .dot files resolvable (installed-package run, or git unavailable)",
)
def test_render_findings_across_corpus_are_never_errors() -> None:
    """Whatever the rules find in the shipped corpus, it must stay advisory."""
    for dot_path in _TRACKED_DOT_FILES:
        for d in lint(parse_dot(dot_path.read_text(encoding="utf-8"))):
            if d.rule.startswith("RENDER-"):
                assert d.severity == "WARNING", (
                    f"{dot_path.relative_to(_REPO_ROOT)}: {d.rule} is "
                    f"{d.severity}, must be WARNING"
                )
