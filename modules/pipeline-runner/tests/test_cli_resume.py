"""`attractor resume` CLI surface — AC-6 fail-loud contract (issue #224).

Every way a checkpoint can be unusable must exit non-zero with a message that
names the cause and says what to do, and must leave the run directory
untouched — never a silent restart from the start node presented as a
successful resume.

These drive the real argv front door (``cli.main``), not the library.
"""

import json

import pytest

from amplifier_module_pipeline_runner import cli
from amplifier_module_loop_pipeline.checkpoint import (
    SCHEMA_VERSION,
    fingerprint_dot_source,
)

DOT = """
digraph cli_resume {
    start [shape=Mdiamond]
    a [shape=parallelogram, tool_command="echo a"]
    b [shape=parallelogram, tool_command="echo b"]
    exit [shape=Msquare]
    start -> a -> b -> exit
}
"""


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _checkpoint(**overrides):
    payload = {
        "current_node": "a",
        "completed_nodes": ["start", "a"],
        "context": {"outcome": "success"},
        "timestamp": "2026-08-14T00:00:00Z",
        "node_retries": {"a": 0},
        "logs": [],
        "schema_version": SCHEMA_VERSION,
        "run_state": "in_flight",
        "node_outcomes": {
            "a": {
                "status": "success",
                "preferred_label": None,
                "suggested_next_ids": None,
                "is_explicit": True,
                "failure_reason": None,
                "notes": None,
            }
        },
        "engine_state": {
            "iteration_count": 0,
            "node_execution_counts": {"a": 1},
            "goal_gate_retries": 0,
            "failure_routing_retries": 0,
            "steps": 2,
        },
        "graph": {"fingerprint": fingerprint_dot_source(DOT), "dot_source": DOT},
    }
    payload.update(overrides)
    return payload


def _run_dir(tmp_path, payload=None):
    d = tmp_path / "run"
    d.mkdir()
    if payload is not None:
        (d / "checkpoint.json").write_text(json.dumps(payload, indent=2))
    return d


def test_resume_is_a_documented_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["resume", "/some/run"])
    assert args.command == "resume"
    assert args.run_dir == "/some/run"
    # Defaults mirror `run` so the two verbs are operationally interchangeable.
    assert args.provider == "anthropic"
    assert args.on_human_gate == "fail"
    assert args.dot_file is None


def test_missing_run_directory(tmp_path, capsys):
    rc = cli.main(["resume", str(tmp_path / "nope")])
    assert rc == 1
    assert "run directory not found" in capsys.readouterr().err


def test_missing_checkpoint(tmp_path, capsys):
    run_dir = _run_dir(tmp_path)
    rc = cli.main(["resume", str(run_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "nothing to resume" in err
    assert "checkpoint.json" in err
    # Nothing executed, nothing created.
    assert list(run_dir.iterdir()) == []


def test_corrupted_checkpoint(tmp_path, capsys):
    run_dir = _run_dir(tmp_path)
    (run_dir / "checkpoint.json").write_text('{"current_node": "a", "compl')
    rc = cli.main(["resume", str(run_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "corrupted checkpoint" in err
    assert [p.name for p in run_dir.iterdir()] == ["checkpoint.json"]


def test_v1_checkpoint_is_refused(tmp_path, capsys):
    payload = _checkpoint()
    del payload["schema_version"]
    run_dir = _run_dir(tmp_path, payload)
    rc = cli.main(["resume", str(run_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not resumable" in err
    assert "pre-resume observability records" in err


def test_already_completed_run_is_refused(tmp_path, capsys):
    run_dir = _run_dir(tmp_path, _checkpoint(run_state="completed"))
    rc = cli.main(["resume", str(run_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "already completed" in err
    assert "attractor run" in err


def test_graph_mismatch_is_refused(tmp_path, capsys):
    run_dir = _run_dir(tmp_path, _checkpoint())
    other = tmp_path / "other.dot"
    other.write_text(DOT.replace('echo a', 'echo CHANGED'))
    rc = cli.main(["resume", str(run_dir), "--dot-file", str(other)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "different graph" in err
    assert "resume refused" in err


def test_current_node_not_in_graph_is_refused(tmp_path, capsys):
    """AC-6's named case, through the CLI."""
    run_dir = _run_dir(tmp_path, _checkpoint(current_node="ghost"))
    rc = cli.main(["resume", str(run_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "'ghost'" in err
    assert "not a node of the graph being resumed" in err
    # No node ran: the engine never got a chance to write anything.
    assert [p.name for p in run_dir.iterdir()] == ["checkpoint.json"]


def test_param_colliding_with_restored_context_is_refused(tmp_path, capsys):
    run_dir = _run_dir(tmp_path, _checkpoint())
    rc = cli.main(["resume", str(run_dir), "--param", "outcome=fail"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "collide with context restored from the checkpoint" in err
