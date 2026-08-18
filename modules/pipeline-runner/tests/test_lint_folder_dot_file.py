"""`attractor lint` + TOPO-010 (issue #200): advisory, never blocking.

The rule warns when a ``shape=folder`` node's STATIC relative ``dot_file=``
target is absent at lint time.  The exit-code contract is the whole point:
the linter genuinely cannot tell "the author typo'd the path" from "a node
upstream writes this file during the run", so it must never fail the build of
a composition graph.  These tests pin rc=0 in both cases.

They also pin the seam the rule depends on: ``cmd_lint`` seeds
``graph.source_dir`` from the .dot file's own directory, the same way
``cmd_run`` does (EXTENSIONS.md §10 tier 2).  Without it the rule cannot know
where a relative target points and stays silent.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_module_pipeline_runner import cli

_PARENT_DOT = """\
digraph parent {{
    start [shape=Mdiamond];
    child [shape=folder, dot_file="{target}", label="composed child"];
    done  [shape=Msquare];
    start -> child -> done;
}}
"""

_CHILD_DOT = """\
digraph child {
    cstart [shape=Mdiamond];
    cdone  [shape=Msquare];
    cstart -> cdone;
}
"""


def _write_parent(tmp_path: Path, target: str) -> str:
    parent = tmp_path / "parent.dot"
    parent.write_text(_PARENT_DOT.format(target=target), encoding="utf-8")
    return str(parent)


class TestFolderDotFileAbsentIsAdvisory:
    def test_missing_static_child_warns_and_exits_zero(self, tmp_path, capsys):
        """The issue's own graph: WARNING named, rc=0 -- lint does NOT fail."""
        rc = cli.main(["lint", _write_parent(tmp_path, "missing-child.dot")])
        out = capsys.readouterr().out

        assert rc == 0, "an advisory warning must never fail the exit code"
        assert "WARNING: [folder_dot_file_absent]" in out
        assert "[child]" in out  # names the node
        assert 'dot_file="missing-child.dot"' in out  # names the literal target
        assert str(tmp_path / "missing-child.dot") in out  # names where it looked
        assert "ERROR:" not in out

    def test_write_then_run_graph_still_exits_zero(self, tmp_path, capsys):
        """Target absent at lint time but written at run time: advisory, rc=0.

        This is the case the rule cannot distinguish from a typo -- which is
        exactly why it is a WARNING.  It must never block a composition graph.
        """
        rc = cli.main(["lint", _write_parent(tmp_path, "gen/child.dot")])
        out = capsys.readouterr().out

        assert rc == 0
        assert "folder_dot_file_absent" in out
        assert "ERROR:" not in out

    def test_present_child_produces_no_finding(self, tmp_path, capsys):
        (tmp_path / "child.dot").write_text(_CHILD_DOT, encoding="utf-8")
        rc = cli.main(["lint", _write_parent(tmp_path, "child.dot")])
        out = capsys.readouterr().out

        assert rc == 0
        assert "folder_dot_file_absent" not in out

    def test_absolute_and_variable_targets_are_skipped(self, tmp_path, capsys):
        rc_abs = cli.main(["lint", _write_parent(tmp_path, str(tmp_path / "nope.dot"))])
        out_abs = capsys.readouterr().out
        assert rc_abs == 0
        assert "folder_dot_file_absent" not in out_abs

        rc_var = cli.main(
            ["lint", _write_parent(tmp_path, "$target_dir/.gen/child.dot")]
        )
        out_var = capsys.readouterr().out
        assert rc_var == 0
        assert "folder_dot_file_absent" not in out_var

    def test_strict_mode_still_honours_its_own_contract(self, tmp_path, capsys):
        """--strict fails on ANY diagnostic; the default run must not."""
        parent = _write_parent(tmp_path, "missing-child.dot")

        assert cli.main(["lint", parent]) == 0
        capsys.readouterr()
        assert cli.main(["lint", parent, "--strict"]) == 1
