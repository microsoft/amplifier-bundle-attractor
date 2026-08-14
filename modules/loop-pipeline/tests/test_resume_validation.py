"""Resume validation ladder — AC-6 table tests (issue #224).

Spec §5.3 rule 1 ("Load the checkpoint from ``{logs_root}/checkpoint.json``")
is the only sanctioned way into a resume, and every way that load can go wrong
must fail LOUD: named cause, offending value, actionable remedy — and NEVER a
silent fallback to restarting from the start node.

The ladder's ORDER is the contract:

    1. exists   2. parses   3. version   4. liveness
    5. identity 6. structure  ->  then (and only then) restore

Each rung is exercised here in isolation, plus the ordering property (an
earlier rung wins over a later one when a checkpoint is broken in two ways at
once) and the no-mutation property (a refused resume leaves the run directory
exactly as it found it).
"""

import json

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    RUN_STATE_COMPLETED,
    SCHEMA_VERSION,
    CheckpointAlreadyCompletedError,
    CheckpointCorruptError,
    CheckpointGraphMismatchError,
    CheckpointMissingError,
    CheckpointResumeError,
    CheckpointSchemaVersionError,
    CheckpointStructureError,
    fingerprint_dot_source,
    load_checkpoint_for_resume,
    verify_checkpoint_structure,
)
from amplifier_module_loop_pipeline.dot_parser import parse_dot

DOT = """
digraph resume_fixture {
    start [shape=Mdiamond]
    a [shape=parallelogram, tool_command="echo a"]
    b [shape=parallelogram, tool_command="echo b"]
    exit [shape=Msquare]
    start -> a -> b -> exit
}
"""

OTHER_DOT = """
digraph resume_fixture {
    start [shape=Mdiamond]
    a [shape=parallelogram, tool_command="echo CHANGED"]
    b [shape=parallelogram, tool_command="echo b"]
    exit [shape=Msquare]
    start -> a -> b -> exit
}
"""


def _valid_payload(**overrides):
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
        "graph": {
            "fingerprint": fingerprint_dot_source(DOT),
            "dot_source": DOT,
        },
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload) -> str:
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(payload, indent=2))
    return str(path)


# ---------------------------------------------------------------------------
# Happy path — the ladder passes a well-formed checkpoint through untouched
# ---------------------------------------------------------------------------


def test_valid_checkpoint_passes_every_rung(tmp_path):
    path = _write(tmp_path, _valid_payload())
    cp = load_checkpoint_for_resume(path)
    assert cp.current_node == "a"
    assert cp.schema_version == SCHEMA_VERSION
    assert cp.graph_dot_source == DOT
    verify_checkpoint_structure(cp, parse_dot(cp.graph_dot_source))


def test_supplied_matching_dot_source_is_accepted(tmp_path):
    """--dot-file is allowed for provenance when it fingerprint-matches."""
    path = _write(tmp_path, _valid_payload())
    cp = load_checkpoint_for_resume(path, dot_source=DOT)
    assert cp.current_node == "a"


# ---------------------------------------------------------------------------
# Rung-by-rung refusals (AC-6): named cause + remedy, and always loud
# ---------------------------------------------------------------------------


def test_rung1_missing_checkpoint(tmp_path):
    with pytest.raises(CheckpointMissingError) as exc:
        load_checkpoint_for_resume(str(tmp_path / "checkpoint.json"))
    msg = str(exc.value)
    assert "nothing to resume" in msg
    assert "checkpoint.json" in msg
    assert "interrupted run" in msg  # remedy


def test_rung2_truncated_json(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text('{"current_node": "a", "completed_nod')
    with pytest.raises(CheckpointCorruptError) as exc:
        load_checkpoint_for_resume(str(path))
    msg = str(exc.value)
    assert "corrupted checkpoint" in msg
    assert "not valid JSON" in msg
    assert "re-run the pipeline from the start" in msg.lower()


def test_rung2_json_but_not_a_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"totally": "unrelated"}))
    with pytest.raises(CheckpointCorruptError) as exc:
        load_checkpoint_for_resume(str(path))
    assert "not a checkpoint" in str(exc.value)


def test_rung2_json_scalar(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(CheckpointCorruptError):
        load_checkpoint_for_resume(str(path))


@pytest.mark.parametrize(
    "version_override,expected_fragment",
    [
        ({}, "pre-resume observability records"),  # v1: schema_version absent
        ({"schema_version": 1}, "pre-resume observability records"),
        ({"schema_version": 99}, "newer engine"),
    ],
)
def test_rung3_schema_version(tmp_path, version_override, expected_fragment):
    payload = _valid_payload()
    payload.pop("schema_version", None)
    payload.update(version_override)
    path = _write(tmp_path, payload)
    with pytest.raises(CheckpointSchemaVersionError) as exc:
        load_checkpoint_for_resume(path)
    msg = str(exc.value)
    assert "not resumable" in msg
    assert f"v{SCHEMA_VERSION} required" in msg
    assert expected_fragment in msg


def test_rung4_already_completed(tmp_path):
    path = _write(tmp_path, _valid_payload(run_state=RUN_STATE_COMPLETED))
    with pytest.raises(CheckpointAlreadyCompletedError) as exc:
        load_checkpoint_for_resume(path)
    msg = str(exc.value)
    assert "already completed" in msg
    assert "nothing to resume" in msg
    assert "attractor run" in msg  # remedy


def test_rung5_graph_fingerprint_mismatch(tmp_path):
    """A checkpoint binds to the graph that wrote it; no override flag exists."""
    path = _write(tmp_path, _valid_payload())
    with pytest.raises(CheckpointGraphMismatchError) as exc:
        load_checkpoint_for_resume(path, dot_source=OTHER_DOT)
    msg = str(exc.value)
    assert "different graph" in msg
    assert fingerprint_dot_source(DOT)[:15] in msg
    assert fingerprint_dot_source(OTHER_DOT)[:15] in msg
    assert "resume refused" in msg


def test_rung5_v2_without_embedded_graph(tmp_path):
    payload = _valid_payload(graph={})
    path = _write(tmp_path, payload)
    with pytest.raises(CheckpointStructureError) as exc:
        load_checkpoint_for_resume(path)
    assert "no graph identity" in str(exc.value)


def test_rung6_current_node_not_in_graph(tmp_path):
    """AC-6's named case: current_node is not a node of the graph."""
    path = _write(tmp_path, _valid_payload(current_node="ghost"))
    cp = load_checkpoint_for_resume(path)
    with pytest.raises(CheckpointStructureError) as exc:
        verify_checkpoint_structure(cp, parse_dot(DOT))
    msg = str(exc.value)
    assert "'ghost'" in msg
    assert "not a node of the graph being resumed" in msg
    assert "'a'" in msg and "'b'" in msg  # names the available nodes


def test_rung6_completed_nodes_unknown_id(tmp_path):
    path = _write(tmp_path, _valid_payload(completed_nodes=["start", "a", "phantom"]))
    cp = load_checkpoint_for_resume(path)
    with pytest.raises(CheckpointStructureError) as exc:
        verify_checkpoint_structure(cp, parse_dot(DOT))
    assert "'phantom'" in str(exc.value)


def test_rung6_node_outcomes_unknown_id(tmp_path):
    payload = _valid_payload()
    payload["node_outcomes"]["nowhere"] = {"status": "success"}
    path = _write(tmp_path, payload)
    cp = load_checkpoint_for_resume(path)
    with pytest.raises(CheckpointStructureError) as exc:
        verify_checkpoint_structure(cp, parse_dot(DOT))
    assert "'nowhere'" in str(exc.value)


def test_rung6_invalid_status_value(tmp_path):
    payload = _valid_payload()
    payload["node_outcomes"]["a"]["status"] = "vibes"
    path = _write(tmp_path, payload)
    cp = load_checkpoint_for_resume(path)
    with pytest.raises(CheckpointStructureError) as exc:
        verify_checkpoint_structure(cp, parse_dot(DOT))
    msg = str(exc.value)
    assert "'vibes'" in msg
    assert "not a valid StageStatus" in msg


def test_rung6_node_outcome_not_an_object(tmp_path):
    payload = _valid_payload()
    payload["node_outcomes"]["a"] = "success"
    path = _write(tmp_path, payload)
    cp = load_checkpoint_for_resume(path)
    with pytest.raises(CheckpointStructureError) as exc:
        verify_checkpoint_structure(cp, parse_dot(DOT))
    assert "is not an object" in str(exc.value)


# ---------------------------------------------------------------------------
# Ladder properties
# ---------------------------------------------------------------------------


def test_ladder_order_earlier_rung_wins(tmp_path):
    """A checkpoint broken at rungs 3 AND 4 reports the EARLIER rung."""
    payload = _valid_payload(schema_version=1, run_state=RUN_STATE_COMPLETED)
    path = _write(tmp_path, payload)
    with pytest.raises(CheckpointSchemaVersionError):
        load_checkpoint_for_resume(path)


def test_ladder_order_version_before_identity(tmp_path):
    payload = _valid_payload(schema_version=1, graph={})
    path = _write(tmp_path, payload)
    with pytest.raises(CheckpointSchemaVersionError):
        load_checkpoint_for_resume(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version=1),
        lambda p: p.update(run_state=RUN_STATE_COMPLETED),
        lambda p: p.update(current_node="ghost"),
    ],
)
def test_refusal_never_mutates_the_run_directory(tmp_path, mutate):
    """A refused resume touches nothing — no restart-from-scratch side effects."""
    payload = _valid_payload()
    mutate(payload)
    path = _write(tmp_path, payload)
    before = (tmp_path / "checkpoint.json").read_bytes()
    entries_before = sorted(p.name for p in tmp_path.iterdir())

    with pytest.raises(CheckpointResumeError):
        cp = load_checkpoint_for_resume(path)
        verify_checkpoint_structure(cp, parse_dot(DOT))

    assert (tmp_path / "checkpoint.json").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == entries_before


def test_every_rung_error_is_a_checkpoint_resume_error():
    """One family, so a caller can catch the whole ladder in one except."""
    for cls in (
        CheckpointMissingError,
        CheckpointCorruptError,
        CheckpointSchemaVersionError,
        CheckpointAlreadyCompletedError,
        CheckpointGraphMismatchError,
        CheckpointStructureError,
    ):
        assert issubclass(cls, CheckpointResumeError)
