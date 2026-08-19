"""GATE 0 — FOUNDATIONAL. Nothing else counts until this is green.

Scenario 7 Layer 1 (fail-loud + schema-mismatch + own-data/egress). Discovery
and version-check must be correct before ANY mining ships, because every
downstream signal reads what this stage selected. A wrong answer here is not
a bad ranking — it is a confident lie about the user's own data.

Machine-checks, in the scenario's own terms:
  1a  exact string `looked in <root>, found 0` AND a non-zero exit code
  1b  `JsonlSchemaMismatch` raised, run STOPS, 0 records processed after it
  1c  planted foreign-source session mined = 0, blocked endpoints touched = 0,
      egress = 0 B
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from attractor_scout import discover, extract, pipeline
from attractor_scout.errors import EmptyCorpusError, JsonlSchemaMismatch

from fixtures.synthetic_corpus import (
    build_empty_root,
    build_frequency_corpus,
    build_own_data_scope_corpus,
    build_wrong_version_corpus,
)

CLI = Path(__file__).resolve().parent.parent / "scripts" / "attractor_scout_cli.py"


# ---------------------------------------------------------------- Layer 1a
def test_empty_root_raises_with_exact_message(corpus_root: Path):
    build_empty_root(corpus_root)
    with pytest.raises(EmptyCorpusError) as excinfo:
        discover.enumerate_sessions(corpus_root)
    assert f"looked in {corpus_root}, found 0" in str(excinfo.value)


def test_empty_root_cli_exits_nonzero_with_exact_message(corpus_root: Path):
    """The exact-string + exit-code contract, through the real CLI."""
    build_empty_root(corpus_root)
    proc = subprocess.run(
        [sys.executable, str(CLI), "--root", str(corpus_root), "enumerate"],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, "an empty root must never exit 0"
    assert f"looked in {corpus_root}, found 0" in combined


def test_empty_root_never_fabricates_a_count(corpus_root: Path):
    """A shallower glob must not be allowed to invent a non-zero count."""
    build_empty_root(corpus_root)
    proc = subprocess.run(
        [sys.executable, str(CLI), "--root", str(corpus_root), "run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert '"n_records"' not in proc.stdout


def test_missing_root_directory_is_also_fail_loud(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(EmptyCorpusError) as excinfo:
        discover.enumerate_sessions(missing)
    assert f"looked in {missing}, found 0" in str(excinfo.value)


# ---------------------------------------------------------------- Layer 1b
def test_schema_mismatch_raises_named_error(corpus_root: Path):
    truth = build_wrong_version_corpus(corpus_root)
    with pytest.raises(JsonlSchemaMismatch) as excinfo:
        discover.enumerate_sessions(corpus_root)
    assert "version mismatch" in str(excinfo.value)
    assert truth.expected["bad_version"] in str(excinfo.value)


def test_schema_mismatch_stops_the_run_with_zero_records_after(corpus_root: Path):
    """0 silent-continue: no record is produced once a mismatch is seen."""
    build_wrong_version_corpus(corpus_root)
    processed_after = 0
    try:
        disc = discover.enumerate_sessions(corpus_root)
        refs = discover.qualify(disc)
        processed_after = len(extract.extract_corpus(disc, refs))
    except JsonlSchemaMismatch:
        pass
    else:  # pragma: no cover - only reached if the guard regressed
        pytest.fail("schema mismatch did not stop the run")
    assert processed_after == 0


def test_format_mismatch_is_also_fail_loud(corpus_root: Path):
    from fixtures.synthetic_corpus import write_session

    write_session(corpus_root, "syn-fmt", "syn-0001-4000-8000-000000000001", prompts=["x"], fmt="something-else")
    with pytest.raises(JsonlSchemaMismatch) as excinfo:
        discover.enumerate_sessions(corpus_root)
    assert "format mismatch" in str(excinfo.value)


def test_schema_mismatch_cli_exit_code(corpus_root: Path):
    build_wrong_version_corpus(corpus_root)
    proc = subprocess.run(
        [sys.executable, str(CLI), "--root", str(corpus_root), "extract", "--out", str(corpus_root / "x.jsonl")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "JsonlSchemaMismatch" in proc.stderr


# ---------------------------------------------------------------- Layer 1c
def test_foreign_source_sessions_are_never_mined(corpus_root: Path):
    truth = build_own_data_scope_corpus(corpus_root)
    disc = discover.enumerate_sessions(corpus_root)
    mined = {s.session_id for s in disc.sessions}
    assert disc.scope.foreign_sessions_mined == 0
    assert disc.scope.foreign_sessions_seen == len(truth.expected["foreign_ids"])
    for sid in truth.expected["foreign_ids"]:
        assert sid not in mined, "a foreign-source session reached the mining set"
    for sid in truth.expected["own_ids"]:
        assert sid in mined


def test_blocked_endpoints_are_declared_but_never_touched(corpus_root: Path):
    truth = build_own_data_scope_corpus(corpus_root)
    disc = discover.enumerate_sessions(corpus_root)
    assert disc.scope.blocked_endpoints_declared == truth.expected["blocked_endpoints"]
    assert disc.scope.blocked_endpoints_touched == 0


def test_zero_egress_no_socket_is_ever_opened(corpus_root: Path, monkeypatch):
    """Egress is enforced by construction, and this proves it.

    Every socket constructor is poisoned for the duration of a full mining
    run. If any code path in discovery, extraction, detection, ranking or
    rendering tried to reach the network, the run would raise instead of
    quietly succeeding.
    """
    build_frequency_corpus(
        corpus_root,
        seed=0,
        n_unit_roots=8,
        n_workspaces=4,
        n_children=2,
        total_occurrence_lines=12,
        decoy_workspaces=1,
        decoy_size=3,
    )

    def _poisoned(*_args, **_kwargs):
        raise AssertionError("network egress attempted during a local mining run")

    monkeypatch.setattr(socket, "socket", _poisoned)
    monkeypatch.setattr(socket, "create_connection", _poisoned)

    result = pipeline.run(root=corpus_root, mode="jsonl", render_to=corpus_root / "report.html")
    assert result.n_records > 0
    assert result.scope["egress_bytes"] == 0
    assert Path(result.artifact or "").is_file()


def test_jsonl_mode_never_probes_the_graph(corpus_root: Path, monkeypatch):
    from attractor_scout import graph

    def _boom(*_a, **_k):
        raise AssertionError("mode='jsonl' probed the graph")

    monkeypatch.setattr(graph, "probe_graph", _boom)
    decision = graph.resolve_path("jsonl")
    assert decision.tier == "C"
    assert decision.via_graph is False


def test_graph_mode_fails_loud_rather_than_silently_degrading():
    from attractor_scout import graph
    from attractor_scout.errors import GraphUnavailable

    with pytest.raises(GraphUnavailable):
        graph.resolve_path("graph", server_url="http://example.invalid:7687")


def test_auto_mode_falls_back_to_tier_c_with_an_honest_note():
    from attractor_scout import graph

    decision = graph.resolve_path("auto", server_url=None)
    assert decision.tier == "C"
    assert decision.via_graph is False
    assert "never a precondition" in decision.note
