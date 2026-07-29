"""Tests for the 'attractor trace' CLI subcommand (T1-1 / Extension #24).

Covers:
  - attractor trace --help exits 0
  - attractor trace <nonexistent> exits 1
  - attractor trace on a dir with no trace.jsonl exits 0 with a message
  - attractor trace on a dir with trace.jsonl exits 0
  - attractor trace output shows at least 2 distinct iterations
  - 'trace' subcommand registered in the parser

These tests live here (pipeline-runner tests) because the trace subcommand
is implemented in amplifier_module_pipeline_runner.cli.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_pipeline_runner import cli


class TestAttractorTraceCLI:
    """attractor trace subcommand: help, missing dir, no trace, real trace."""

    def test_trace_help_exits_0(self):
        """attractor trace --help exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["trace", "--help"])
        assert exc_info.value.code == 0

    def test_trace_missing_dir_exits_1(self, tmp_path):
        """attractor trace <nonexistent> exits 1."""
        nonexistent = str(tmp_path / "does_not_exist")
        result = cli.main(["trace", nonexistent])
        assert result == 1

    def test_trace_no_trace_jsonl_exits_0(self, tmp_path):
        """attractor trace on a dir with no trace.jsonl exits 0 with a message."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()
        result = cli.main(["trace", str(run_dir)])
        assert result == 0

    def test_trace_real_trace_exits_0(self, tmp_path):
        """attractor trace on a dir with trace.jsonl exits 0."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        trace_path = run_dir / "trace.jsonl"
        records = [
            {
                "iteration": 0,
                "node_id": "start",
                "status": "success",
                "preferred_label": None,
                "duration_ms": 1.0,
                "ts": "2024-01-01T00:00:00Z",
            },
            {
                "iteration": 0,
                "node_id": "work",
                "status": "success",
                "preferred_label": "go",
                "duration_ms": 5.0,
                "ts": "2024-01-01T00:00:01Z",
            },
            {
                "iteration": 1,
                "node_id": "work",
                "status": "success",
                "preferred_label": "stop",
                "duration_ms": 4.0,
                "ts": "2024-01-01T00:00:02Z",
            },
        ]
        with open(trace_path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = cli.main(["trace", str(run_dir)])
        assert result == 0

    def test_trace_shows_multiple_iterations(self, tmp_path, capsys):
        """attractor trace output mentions at least 2 distinct iterations."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        trace_path = run_dir / "trace.jsonl"
        records = [
            {
                "iteration": 0,
                "node_id": "work",
                "status": "success",
                "preferred_label": "go",
                "duration_ms": 5.0,
                "ts": "2024-01-01T00:00:00Z",
            },
            {
                "iteration": 1,
                "node_id": "work",
                "status": "success",
                "preferred_label": "stop",
                "duration_ms": 4.0,
                "ts": "2024-01-01T00:00:01Z",
            },
        ]
        with open(trace_path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        result = cli.main(["trace", str(run_dir)])
        assert result == 0
        captured = capsys.readouterr()
        # The output should mention both iteration 0 and iteration 1
        assert "iteration 0" in captured.out, (
            f"Expected 'iteration 0' in output:\n{captured.out}"
        )
        assert "iteration 1" in captured.out, (
            f"Expected 'iteration 1' in output:\n{captured.out}"
        )

    def test_trace_subcommand_in_parser(self):
        """'trace' is registered as a subcommand in the CLI parser."""
        parser = cli.build_parser()
        # Find the subparser action
        sub_actions = [
            a for a in parser._actions if hasattr(a, "_name_parser_map")
        ]
        assert sub_actions, "No subparser action found in attractor CLI"
        choices = sub_actions[0]._name_parser_map
        assert "trace" in choices, (
            f"'trace' not in subcommand choices: {list(choices.keys())}"
        )

    def test_trace_run_dir_argument(self):
        """trace subparser accepts a positional run_dir argument."""
        parser = cli.build_parser()
        # Parse a trace command with a path argument — should not raise
        args = parser.parse_args(["trace", "/some/run/dir"])
        assert args.command == "trace"
        assert args.run_dir == "/some/run/dir"

    def test_trace_no_trace_jsonl_prints_message(self, tmp_path, capsys):
        """attractor trace with no trace.jsonl prints a human-readable message."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()
        cli.main(["trace", str(run_dir)])
        captured = capsys.readouterr()
        # Should mention "no trace data" or similar
        assert "no trace" in captured.out.lower() or "trace.jsonl" in captured.out, (
            f"Expected a 'no trace data' message, got:\n{captured.out}"
        )

    def test_trace_shows_node_ids(self, tmp_path, capsys):
        """attractor trace output includes node IDs from the trace."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        trace_path = run_dir / "trace.jsonl"
        records = [
            {
                "iteration": 0,
                "node_id": "my_special_node",
                "status": "success",
                "preferred_label": None,
                "duration_ms": 3.0,
                "ts": "2024-01-01T00:00:00Z",
            },
        ]
        with open(trace_path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        cli.main(["trace", str(run_dir)])
        captured = capsys.readouterr()
        assert "my_special_node" in captured.out, (
            f"Expected node id in output:\n{captured.out}"
        )
