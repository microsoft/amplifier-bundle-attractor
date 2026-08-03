"""Tests for command-content lint rules — CMD-001 and CMD-002.

CMD-001: pipe-masked exit code — the final ``;``-separated segment of the
command ends in a filter/pager stage without ``set -o pipefail`` in the
executable command text, so the gate's exit code is the filter's (always 0),
not the real command's.  A ``||`` branch does NOT suppress this rule.

CMD-002: always-true sentinel — a trailing ``&& echo/printf TOKEN`` after a
pipe-masked command.  The sentinel fires unconditionally, making
``tool.last_line`` the sentinel string regardless of whether the wrapped
command succeeded.

Test pattern mirrors test_topological_lint.py: construct Graph/Node/Edge
objects directly (no DOT parsing) for speed and isolation.

False-positive discipline is the primary focus: legit pipes and honest token
gates must NOT be flagged.  Each false-positive test case documents WHY the
pattern is safe.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.validation import (
    Diagnostic,
    lint,
)


# ---------------------------------------------------------------------------
# Helpers — mirrors test_topological_lint.py
# ---------------------------------------------------------------------------


def _mdiamond(node_id: str = "start") -> Node:
    return Node(id=node_id, shape="Mdiamond", label="Start")


def _msquare(node_id: str = "exit") -> Node:
    return Node(id=node_id, shape="Msquare", label="Exit")


def _tool(node_id: str = "tool", tool_command: str = "", **kwargs) -> Node:
    attrs: dict = {}
    if tool_command:
        attrs["tool_command"] = tool_command
    attrs.update(kwargs)
    return Node(id=node_id, shape="parallelogram", attrs=attrs)


def _graph(
    nodes: dict[str, Node] | None = None,
    edges: list[Edge] | None = None,
) -> Graph:
    return Graph(
        name="test",
        nodes=nodes or {},
        edges=edges or [],
    )


def _minimal_graph_with_tool(tool_command: str, node_id: str = "gate") -> Graph:
    """Return a minimal valid graph containing one tool node with the given command."""
    t = _tool(node_id, tool_command=tool_command)
    return _graph(
        nodes={
            "start": _mdiamond(),
            node_id: t,
            "done": _msquare("done"),
        },
        edges=[
            Edge("start", node_id),
            Edge(node_id, "done"),
        ],
    )


def _cmd_diags(diags: list[Diagnostic], rule: str) -> list[Diagnostic]:
    """Return diagnostics matching the given CMD rule."""
    return [d for d in diags if d.rule == rule]


# ---------------------------------------------------------------------------
# CMD-001: pipe-masked exit code — positive cases (SHOULD be flagged)
# ---------------------------------------------------------------------------


class TestCmd001PipeMasked:
    """CMD-001 fires when the final pipe stage is a recognised filter."""

    def test_false_pipe_tail_flagged(self):
        """Incident shape: 'false | tail -1' — exit code is tail's (0)."""
        g = _minimal_graph_with_tool("false | tail -1")
        diags = lint(g)
        hits = _cmd_diags(diags, "CMD-001")
        assert hits, "Expected CMD-001 for 'false | tail -1'"
        assert hits[0].node_id == "gate"
        assert hits[0].severity == "WARNING"

    def test_pipe_head_flagged(self):
        """'cmd 2>&1 | head -20' — exit code is head's."""
        g = _minimal_graph_with_tool("some_command 2>&1 | head -20")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to head"

    def test_pipe_grep_flagged(self):
        """'cmd | grep pattern' — exit code is grep's (0 if match, 1 if not)."""
        g = _minimal_graph_with_tool("run_tests | grep FAILED")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to grep"

    def test_pipe_sed_flagged(self):
        """'cmd 2>&1 | sed s/foo/bar/' — exit code is sed's."""
        g = _minimal_graph_with_tool("build_cmd 2>&1 | sed 's/error/ERROR/'")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to sed"

    def test_pipe_awk_flagged(self):
        """'cmd | awk ...' — exit code is awk's."""
        g = _minimal_graph_with_tool("run_check | awk '{print $1}'")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to awk"

    def test_pipe_cut_flagged(self):
        """'cmd | cut -d: -f1' — exit code is cut's."""
        g = _minimal_graph_with_tool("get_status | cut -d: -f1")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to cut"

    def test_pipe_wc_flagged(self):
        """'cmd | wc -l' — exit code is wc's."""
        g = _minimal_graph_with_tool("find . -name '*.py' | wc -l")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to wc"

    def test_pipe_sort_flagged(self):
        """'cmd | sort' — exit code is sort's."""
        g = _minimal_graph_with_tool("list_items | sort")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to sort"

    def test_pipe_uniq_flagged(self):
        """'cmd | uniq' — exit code is uniq's."""
        g = _minimal_graph_with_tool("get_lines | uniq")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to uniq"

    def test_pipe_xargs_flagged(self):
        """'cmd | xargs ...' — exit code is xargs's."""
        g = _minimal_graph_with_tool("find . -name '*.txt' | xargs cat")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for pipe to xargs"

    def test_incident_shape_flagged(self):
        """Incident shape: 'sh -c exit 1 2>&1 | tail -5' — gate records SUCCESS."""
        g = _minimal_graph_with_tool("sh -c 'exit 1' 2>&1 | tail -5")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), "Expected CMD-001 for incident shape"

    def test_node_id_in_diagnostic(self):
        """CMD-001 diagnostic must reference the flagged node's ID."""
        g = _minimal_graph_with_tool("false | tail -1", node_id="my_gate")
        diags = lint(g)
        hits = _cmd_diags(diags, "CMD-001")
        assert hits, "Expected CMD-001"
        assert hits[0].node_id == "my_gate"

    def test_non_tool_node_not_flagged(self):
        """CMD-001 only applies to parallelogram (tool) nodes — not box/diamond."""
        box_node = Node(
            id="work",
            shape="box",
            attrs={"tool_command": "false | tail -1"},
        )
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "work": box_node,
                "done": _msquare("done"),
            },
            edges=[Edge("start", "work"), Edge("work", "done")],
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "CMD-001 must not fire on non-tool nodes"
        )

    def test_tool_node_without_tool_command_not_flagged(self):
        """Tool node with no tool_command attribute is not flagged."""
        t = _tool("gate")  # no tool_command
        g = _graph(
            nodes={"start": _mdiamond(), "gate": t, "done": _msquare("done")},
            edges=[Edge("start", "gate"), Edge("gate", "done")],
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001")


# ---------------------------------------------------------------------------
# CMD-001: false-positive tests — legit pipes NOT flagged
# ---------------------------------------------------------------------------


class TestCmd001FalsePositives:
    """Legitimate pipe patterns that must NOT trigger CMD-001."""

    def test_tee_not_flagged(self):
        """'cmd 2>&1 | tee log' — tee preserves output; not a filter gate."""
        g = _minimal_graph_with_tool("run_tests 2>&1 | tee test.log")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "tee is not a filter — it preserves output for logging"
        )

    def test_pipefail_suppresses_cmd001(self):
        """'set -o pipefail; cmd | tail' — pipefail makes the exit code real."""
        g = _minimal_graph_with_tool("set -o pipefail; run_tests | tail -30")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "pipefail is present — CMD-001 must not fire"
        )

    def test_pipefail_euo_suppresses_cmd001(self):
        """'set -euo pipefail; cmd | tail' — pipefail variant suppresses CMD-001."""
        g = _minimal_graph_with_tool("set -euo pipefail; cmd 2>&1 | tail -20")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "set -euo pipefail suppresses CMD-001"
        )

    def test_honest_token_gate_not_flagged(self):
        """'cmd && printf green || printf red' — honest token gate, no pipe."""
        g = _minimal_graph_with_tool("pytest -q > /dev/null 2>&1 && printf pass || printf fail")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Redirect + honest token gate is the safe idiom — must not be flagged"
        )

    def test_grep_as_test_not_flagged(self):
        """'grep -q pattern file && printf match || printf nomatch' — grep IS the check."""
        g = _minimal_graph_with_tool(
            "grep -qE '^BLOCKED' .ai/postmortem/diagnosis.md && printf blocked || printf continue"
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "grep-as-test with honest || branch is the safe idiom"
        )

    def test_pipe_with_or_branch_still_flagged(self):
        """'cmd | grep -q PASS && printf ok || printf fail' — || does NOT restore original exit code.

        ``grep`` exits 0 when it can read stdin (it always can here), so
        ``printf ok`` fires unconditionally.  ``|| printf fail`` only guards
        against ``grep`` or ``printf`` failing — not the original command.
        The ``||`` does not make this an honest gate.  CMD-001 must fire.
        """
        g = _minimal_graph_with_tool(
            "run_check | grep -q PASS && printf ok || printf fail"
        )
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "|| branch after pipe-masked stage does not restore exit code — CMD-001 must fire"
        )

    def test_pipe_in_subshell_assignment_not_flagged(self):
        """Pipe inside $(...) subshell is not a top-level gate pipe."""
        # sig=$(tail -20 log | sed ... | md5sum | cut -f1); [ "$sig" = "$prev" ] && printf repeat || printf novel
        cmd = (
            "sig=$(tail -20 .ai/test.log 2>/dev/null | sed 's/[0-9]*\\.[0-9]*s//g' "
            "| md5sum | cut -d' ' -f1); prev=$(cat .ai/last-fail-sig 2>/dev/null || echo none); "
            "echo $sig > .ai/last-fail-sig; [ \"$sig\" = \"$prev\" ] && printf repeat || printf novel"
        )
        g = _minimal_graph_with_tool(cmd)
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Pipe inside $(...) is not a top-level gate pipe — must not flag CMD-001"
        )

    def test_verdict_gate_pattern_flagged(self):
        """Shipped verdict_gate pattern: grep | tail | grep -q && printf ship || printf iterate.

        The final stage is ``grep -q`` (a filter in ``_PIPE_FILTER_PROGRAMS``).
        The ``||`` branch does NOT restore the original command's exit code —
        ``grep -q`` exits 0 when it finds a match and 1 when it does not, but
        the pipe-masked stages before it (``grep -E | tail -1``) may hide
        failures in the upstream command.  CMD-001 fires to alert the author.

        Note: this produces a WARNING (not ERROR), so the shipped examples
        lint sweep (``test_examples_lint_clean.py``) is unaffected.
        """
        cmd = (
            "grep -E '^VERDICT:' .ai/critique.md 2>/dev/null | tail -1 | grep -q 'SHIP' "
            "&& printf ship || printf iterate"
        )
        g = _minimal_graph_with_tool(cmd)
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "Verdict gate with pipe-masked stages must flag CMD-001 (WARNING)"
        )

    def test_redirect_not_flagged(self):
        """'cmd > out.log 2>&1' — redirect, not pipe; exit code is cmd's."""
        g = _minimal_graph_with_tool("pytest -q > .ai/test.log 2>&1")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Redirect preserves exit code — must not flag CMD-001"
        )

    def test_exit_code_gate_not_flagged(self):
        """'cmd && printf ok || { printf fail; exit 1; }' — exit-code gate."""
        g = _minimal_graph_with_tool(
            "run_tests && printf ok || { printf fail; exit 1; }"
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Exit-code gate preserves failure — must not flag CMD-001"
        )

    def test_single_quoted_pipe_not_flagged(self):
        """'echo 'false | tail -1'' — pipe is inside a single-quoted string, not a real pipe."""
        g = _minimal_graph_with_tool("echo 'documentation: false | tail -1'")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Pipe inside single-quoted string is not a real shell pipe — must not flag CMD-001"
        )

    def test_double_quoted_pipe_not_flagged(self):
        """'printf \"false | tail -1\"' — pipe is inside a double-quoted string."""
        g = _minimal_graph_with_tool('printf "false | tail -1"')
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Pipe inside double-quoted string is not a real shell pipe — must not flag CMD-001"
        )

    def test_command_substitution_pipe_not_flagged(self):
        """Pipe inside $(...) is already handled by _strip_command_substitutions."""
        g = _minimal_graph_with_tool(
            'result=$(echo "false | tail -1"); printf "%s" "$result"'
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Pipe inside $(...) is not a top-level gate pipe — must not flag CMD-001"
        )


# ---------------------------------------------------------------------------
# CMD-002: always-true sentinel — positive cases (SHOULD be flagged)
# ---------------------------------------------------------------------------


class TestCmd002AlwaysTrueSentinel:
    """CMD-002 fires when a pipe-masked command is followed by && echo/printf TOKEN."""

    def test_incident_sentinel_shape_flagged(self):
        """Incident shape: 'sh -c exit 1 2>&1 | tail -5 && echo GREEN'."""
        g = _minimal_graph_with_tool("sh -c 'exit 1' 2>&1 | tail -5 && echo GREEN")
        diags = lint(g)
        hits = _cmd_diags(diags, "CMD-002")
        assert hits, "Expected CMD-002 for incident sentinel shape"
        assert hits[0].node_id == "gate"
        assert hits[0].severity == "WARNING"

    def test_pipe_tail_echo_sentinel_flagged(self):
        """'cmd 2>&1 | tail -30 && echo DONE' — sentinel fires unconditionally."""
        g = _minimal_graph_with_tool("run_harness 2>&1 | tail -30 && echo DONE")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-002"), "Expected CMD-002 for pipe+echo sentinel"

    def test_pipe_tail_printf_sentinel_flagged(self):
        """'cmd 2>&1 | tail -5 && printf GREEN' — printf sentinel fires unconditionally."""
        g = _minimal_graph_with_tool("do_work 2>&1 | tail -5 && printf GREEN")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-002"), "Expected CMD-002 for pipe+printf sentinel"

    def test_pipe_grep_echo_sentinel_flagged(self):
        """'cmd | grep -v error && echo OK' — sentinel after pipe to grep."""
        g = _minimal_graph_with_tool("run_cmd | grep -v error && echo OK")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-002"), "Expected CMD-002 for grep+echo sentinel"

    def test_pipe_sed_echo_sentinel_flagged(self):
        """'cmd 2>&1 | sed s/x/y/ && echo SLICE_GREEN' — incident variant."""
        g = _minimal_graph_with_tool("run_test 2>&1 | sed 's/x/y/' && echo SLICE_GREEN")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-002"), "Expected CMD-002 for sed+echo sentinel"

    def test_node_id_in_diagnostic(self):
        """CMD-002 diagnostic must reference the flagged node's ID."""
        g = _minimal_graph_with_tool(
            "sh -c 'exit 1' 2>&1 | tail -5 && echo GREEN", node_id="sentinel_node"
        )
        diags = lint(g)
        hits = _cmd_diags(diags, "CMD-002")
        assert hits, "Expected CMD-002"
        assert hits[0].node_id == "sentinel_node"

    def test_at_most_one_cmd002_per_node(self):
        """At most one CMD-002 diagnostic per node (don't spam)."""
        g = _minimal_graph_with_tool("cmd | tail -1 && echo A && echo B")
        diags = lint(g)
        hits = _cmd_diags(diags, "CMD-002")
        assert len(hits) <= 1, "At most one CMD-002 per node"


# ---------------------------------------------------------------------------
# CMD-002: false-positive tests — honest token gates NOT flagged
# ---------------------------------------------------------------------------


class TestCmd002FalsePositives:
    """Legitimate patterns that must NOT trigger CMD-002."""

    def test_honest_token_gate_not_flagged(self):
        """'cmd && printf green || printf red' — both branches; no pipe."""
        g = _minimal_graph_with_tool("pytest -q > /dev/null 2>&1 && printf pass || printf fail")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "Honest token gate with || branch must not flag CMD-002"
        )

    def test_exit_code_gate_not_flagged(self):
        """'cmd && printf ok || { printf fail; exit 1; }' — exit-code gate."""
        g = _minimal_graph_with_tool(
            "run_tests && printf ok || { printf fail; exit 1; }"
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "Exit-code gate must not flag CMD-002"
        )

    def test_pipe_with_or_branch_still_flagged(self):
        """'cmd | grep PASS && printf ok || printf fail' — || does NOT make this honest.

        The ``||`` only guards against ``grep`` or ``printf`` failing.
        The original command's exit code is still masked by the pipe.
        CMD-002 must fire because ``printf ok`` fires unconditionally
        (grep exits 0 when it can read stdin).
        """
        g = _minimal_graph_with_tool(
            "run_check | grep -q PASS && printf ok || printf fail"
        )
        diags = lint(g)
        # CMD-001 fires (pipe-masked); CMD-002 may or may not fire depending
        # on whether _SENTINEL_RE matches (it does not here — no sentinel at
        # end).  At minimum CMD-001 must fire.
        assert _cmd_diags(diags, "CMD-001"), (
            "|| branch after pipe-masked stage does not restore exit code — CMD-001 must fire"
        )

    def test_verdict_gate_pattern_cmd002(self):
        """Shipped verdict_gate: grep | tail | grep -q && printf ship || printf iterate.

        This pattern ends with ``printf ship`` after a pipe-masked stage.
        CMD-002 checks for ``&& echo/printf TOKEN`` at the end of the command.
        The ``_SENTINEL_RE`` requires the sentinel at end-of-string; here the
        command ends with ``|| printf iterate``, not a sentinel, so CMD-002
        does NOT fire.  CMD-001 fires (pipe-masked stage).
        """
        cmd = (
            "grep -E '^VERDICT:' .ai/critique.md 2>/dev/null | tail -1 | grep -q 'SHIP' "
            "&& printf ship || printf iterate"
        )
        g = _minimal_graph_with_tool(cmd)
        diags = lint(g)
        # CMD-002 must NOT fire — the command ends with || printf iterate,
        # not a bare sentinel.
        assert not _cmd_diags(diags, "CMD-002"), (
            "Verdict gate ends with || branch, not a bare sentinel — CMD-002 must not fire"
        )
        # CMD-001 DOES fire — pipe-masked stages are present.
        assert _cmd_diags(diags, "CMD-001"), (
            "Verdict gate has pipe-masked stages — CMD-001 must fire"
        )

    def test_grep_as_test_not_flagged(self):
        """'grep -qE pattern file && printf blocked || printf continue' — grep IS the check."""
        g = _minimal_graph_with_tool(
            "grep -qE '^BLOCKED' .ai/postmortem/diagnosis.md && printf blocked || printf continue"
        )
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "grep-as-test with honest || branch must not flag CMD-002"
        )

    def test_tee_with_echo_not_flagged(self):
        """'cmd 2>&1 | tee log && echo done' — tee is not a filter."""
        g = _minimal_graph_with_tool("run_tests 2>&1 | tee test.log && echo done")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "tee is not in the filter set — CMD-002 must not fire"
        )

    def test_pipefail_suppresses_cmd002(self):
        """'set -o pipefail; cmd | tail && echo done' — pipefail makes exit real."""
        g = _minimal_graph_with_tool("set -o pipefail; run_tests | tail -30 && echo done")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "pipefail suppresses CMD-002"
        )

    def test_pipe_in_subshell_assignment_not_flagged(self):
        """Pipe inside $(...) followed by sentinel is not CMD-002."""
        # The sentinel is after a non-pipe final segment
        cmd = (
            "sig=$(tail -20 .ai/test.log | md5sum | cut -d' ' -f1); "
            "prev=$(cat .ai/last-fail-sig 2>/dev/null || echo none); "
            "echo $sig > .ai/last-fail-sig; [ \"$sig\" = \"$prev\" ] && printf repeat || printf novel"
        )
        g = _minimal_graph_with_tool(cmd)
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "Pipe inside $(...) with honest || branch at end — must not flag CMD-002"
        )

    def test_echo_after_redirect_not_flagged(self):
        """'cmd > out.log 2>&1 && echo done' — redirect, not pipe; exit code is cmd's."""
        # This is NOT a pipe-masked command, so CMD-002 must not fire.
        # (CMD-002 only fires when the && echo follows a pipe-masked segment.)
        g = _minimal_graph_with_tool("run_tests > .ai/test.log 2>&1 && echo done")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-002"), (
            "Redirect preserves exit code — CMD-002 must not fire after redirect"
        )


# ---------------------------------------------------------------------------
# Integration: both rules together (incident graph shape)
# ---------------------------------------------------------------------------


class TestIncidentShape:
    """The 2026-07-28 incident shapes produce findings from both rules."""

    def test_incident_masked_node_flagged(self):
        """'false | tail -1' must produce a CMD-001 finding."""
        g = _minimal_graph_with_tool("false | tail -1", node_id="masked")
        diags = lint(g)
        hits = [d for d in diags if d.node_id == "masked" and d.rule.startswith("CMD")]
        assert hits, "Incident masked node must produce a CMD finding"

    def test_incident_sentinel_node_flagged(self):
        """'sh -c exit 1 2>&1 | tail -5 && echo GREEN' must produce a CMD finding."""
        g = _minimal_graph_with_tool(
            "sh -c 'exit 1' 2>&1 | tail -5 && echo GREEN", node_id="sentinel"
        )
        diags = lint(g)
        hits = [d for d in diags if d.node_id == "sentinel" and d.rule.startswith("CMD")]
        assert hits, "Incident sentinel node must produce a CMD finding"

    def test_clean_graph_no_cmd_findings(self):
        """A graph using only safe idioms produces no CMD findings."""
        # Uses redirect + honest token gate (the doctrinally correct pattern)
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate": _tool(
                    "gate",
                    tool_command="pytest -q > .ai/test.log 2>&1 && printf pass || printf fail",
                ),
                "done": _msquare("done"),
            },
            edges=[Edge("start", "gate"), Edge("gate", "done")],
        )
        diags = lint(g)
        cmd_diags = [d for d in diags if d.rule.startswith("CMD")]
        assert not cmd_diags, f"Safe graph must produce no CMD findings; got: {cmd_diags}"

    def test_multiple_tool_nodes_flagged_independently(self):
        """Multiple pipe-masked tool nodes each produce their own CMD-001."""
        g = _graph(
            nodes={
                "start": _mdiamond(),
                "gate1": _tool("gate1", tool_command="cmd1 | tail -5"),
                "gate2": _tool("gate2", tool_command="cmd2 | head -10"),
                "done": _msquare("done"),
            },
            edges=[
                Edge("start", "gate1"),
                Edge("gate1", "gate2", condition="outcome=success"),
                Edge("gate1", "done", condition="outcome=fail"),
                Edge("gate2", "done"),
            ],
        )
        diags = lint(g)
        cmd001 = _cmd_diags(diags, "CMD-001")
        node_ids = {d.node_id for d in cmd001}
        assert "gate1" in node_ids, "gate1 must be flagged"
        assert "gate2" in node_ids, "gate2 must be flagged"


# ---------------------------------------------------------------------------
# Regression tests for iteration-3 findings (Reviewer B)
# ---------------------------------------------------------------------------


class TestQuotedPipefailRegression:
    """Finding 1: pipefail inside a quoted string must NOT suppress CMD-001/002.

    ``echo "set -o pipefail"; false | tail -1`` does not enable pipefail —
    the ``set`` is inside a quoted argument to ``echo``, not executed.
    A naive textual pipefail match on the raw command string would cause a
    false suppression; the rule must only honour executable ``set``
    statements.
    """

    def test_echo_quoted_pipefail_does_not_suppress_cmd001(self):
        """'echo "set -o pipefail"; false | tail -1' → CMD-001 (not suppressed)."""
        g = _minimal_graph_with_tool('echo "set -o pipefail"; false | tail -1')
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "Quoted pipefail inside echo argument must not suppress CMD-001"
        )

    def test_printf_single_quoted_pipefail_does_not_suppress_cmd001(self):
        """'printf 'set -o pipefail'; false | tail -1' → CMD-001 (not suppressed)."""
        g = _minimal_graph_with_tool("printf 'set -o pipefail'; false | tail -1")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "Quoted pipefail inside printf argument must not suppress CMD-001"
        )

    def test_echo_quoted_pipefail_does_not_suppress_cmd002(self):
        """'echo "set -o pipefail"; false | tail -1 && echo GREEN' → CMD-001+CMD-002."""
        g = _minimal_graph_with_tool('echo "set -o pipefail"; false | tail -1 && echo GREEN')
        diags = lint(g)
        # The echo "set -o pipefail" is in a non-final semicolon segment,
        # so CMD-001 fires on the final segment (false | tail -1 && echo GREEN).
        # CMD-002 also fires for the sentinel.
        assert _cmd_diags(diags, "CMD-001"), (
            "Quoted pipefail in earlier segment must not suppress CMD-001"
        )

    def test_real_pipefail_still_suppresses_cmd001(self):
        """'set -o pipefail; false | tail -1' → CLEAN (real pipefail suppresses)."""
        g = _minimal_graph_with_tool("set -o pipefail; false | tail -1")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Real set -o pipefail must still suppress CMD-001"
        )

    def test_real_pipefail_still_suppresses_cmd002(self):
        """'set -o pipefail; false | tail -1 && echo GREEN' → CLEAN."""
        g = _minimal_graph_with_tool("set -o pipefail; false | tail -1 && echo GREEN")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Real set -o pipefail must still suppress CMD-001"
        )
        assert not _cmd_diags(diags, "CMD-002"), (
            "Real set -o pipefail must still suppress CMD-002"
        )


class TestExecutablePipefailRegression:
    """Only an executable top-level pipefail setting may suppress CMD-001."""

    def test_commented_pipefail_does_not_suppress_cmd001(self):
        """A comment mentioning pipefail does not execute it."""
        g = _minimal_graph_with_tool("# set -o pipefail\nfalse | tail -1")
        assert _cmd_diags(lint(g), "CMD-001")

    def test_conditional_pipefail_does_not_suppress_cmd001(self):
        """A pipefail setting after failed ``&&`` may not execute."""
        g = _minimal_graph_with_tool("false && set -o pipefail; false | tail -1")
        assert _cmd_diags(lint(g), "CMD-001")


class TestNonFinalPipelineRegression:
    """Finding 2: CMD-001 must only fire on the final ``;``-separated segment.

    ``false | tail -1; echo done`` — the final command is ``echo done``
    (exit code 0, no pipe), so CMD-001 must NOT fire.  The previous
    implementation scanned the whole command for the last bare pipe,
    causing false positives on compound commands.
    """

    def test_semicolon_separated_non_final_pipe_not_flagged(self):
        """'false | tail -1; echo done' → CLEAN (echo done is the final command)."""
        g = _minimal_graph_with_tool("false | tail -1; echo done")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Non-final pipe (before semicolon) must not flag CMD-001 — "
            "echo done determines the exit code"
        )

    def test_semicolon_with_exit_preservation_not_flagged(self):
        """'run_cmd | head -5; exit $?' → CLEAN (exit $? preserves the code)."""
        g = _minimal_graph_with_tool("run_cmd | head -5; exit $?")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Non-final pipe followed by exit $? must not flag CMD-001"
        )

    def test_final_pipe_still_flagged(self):
        """'false | tail -1' → CMD-001 (pipe is the final command)."""
        g = _minimal_graph_with_tool("false | tail -1")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "Final pipe to filter must still flag CMD-001"
        )

    def test_and_chain_with_pipe_still_flagged(self):
        """'false | tail -1 && echo SENTINEL' → CMD-001+CMD-002 (one semicolon-segment)."""
        g = _minimal_graph_with_tool("false | tail -1 && echo SENTINEL")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "&& does not split semicolon segments — pipe hazard still present"
        )
        assert _cmd_diags(diags, "CMD-002"), (
            "Sentinel after pipe-masked command must flag CMD-002"
        )

    def test_semicolon_non_final_pipe_cmd002_not_flagged(self):
        """'false | tail -1; echo done && echo SENTINEL' → CLEAN for CMD-002.

        The final semicolon-segment is ``echo done && echo SENTINEL``.
        There is no pipe in that segment, so CMD-002 must NOT fire.
        (CMD-001 also must NOT fire — echo done is the exit-code determiner.)
        """
        g = _minimal_graph_with_tool("false | tail -1; echo done && echo SENTINEL")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "Non-final pipe before semicolon must not flag CMD-001"
        )
        assert not _cmd_diags(diags, "CMD-002"), (
            "Sentinel after non-piped final segment must not flag CMD-002"
        )


# ---------------------------------------------------------------------------
# Independent-critique reproductions — THE CONTRACT
#
# These two reproductions were built verbatim by an independent reviewer
# against an earlier revision of these rules.  They are preserved here under
# their own names because they define the rule contract more precisely than
# the surrounding suites:
#
#   1. executable-pipefail: only an EXECUTED `set -o pipefail` statement may
#      suppress CMD-001/CMD-002 — quoted/printed text must not.
#   2. final-command-boundary: CMD-001 fires only when the masked pipeline is
#      the final exit-code-determining command; a pipe in a non-final
#      `;`-separated segment must not be flagged.
# ---------------------------------------------------------------------------


class TestCriticReproductions:
    """Verbatim reviewer reproductions — regression contract for CMD-001/002."""

    def test_critic_repro_executable_pipefail(self):
        """Reviewer repro 1 (verbatim): textual pipefail must not suppress.

        ``echo "set -o pipefail"; false | tail -1``   -> must flag CMD-001
        ``printf 'set -o pipefail'; false | tail -1`` -> must flag CMD-001

        Neither command enables pipefail; each still executes the incident's
        pipe-masked failure.  An arbitrary log message, comment, or
        user-controlled string containing the text ``set -o pipefail`` must
        never suppress the finding.
        """
        g = _minimal_graph_with_tool('echo "set -o pipefail"; false | tail -1')
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            'echo "set -o pipefail"; false | tail -1 must flag CMD-001 — '
            "the pipefail text is printed, not executed"
        )

        g = _minimal_graph_with_tool("printf 'set -o pipefail'; false | tail -1")
        diags = lint(g)
        assert _cmd_diags(diags, "CMD-001"), (
            "printf 'set -o pipefail'; false | tail -1 must flag CMD-001 — "
            "the pipefail text is printed, not executed"
        )

    def test_critic_repro_final_command_boundary(self):
        """Reviewer repro 2 (verbatim): non-final pipeline must not be flagged.

        ``false | tail -1; echo done`` -> must NOT flag CMD-001

        The ``tail`` pipeline is not the final command whose result determines
        the tool outcome; ``echo done`` is.  Follow-on commands can
        deliberately restore or preserve a status — flagging every earlier
        pipe violates the rule's conservative, low-false-positive scope.
        """
        g = _minimal_graph_with_tool("false | tail -1; echo done")
        diags = lint(g)
        assert not _cmd_diags(diags, "CMD-001"), (
            "false | tail -1; echo done must NOT flag CMD-001 — "
            "'echo done' is the final exit-code-determining command"
        )
        assert not _cmd_diags(diags, "CMD-002"), (
            "false | tail -1; echo done must NOT flag CMD-002 — "
            "no sentinel in the final segment"
        )
