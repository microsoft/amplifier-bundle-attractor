"""Tests for runner-owned manifest provenance stamping."""

from __future__ import annotations

import json

from amplifier_module_pipeline_runner import runner


def test_augment_manifest_adds_runner_owned_provenance(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"graph_name": "existing", "engine_commit": "unknown"}')
    monkeypatch.setattr(
        runner,
        "_get_runner_provenance",
        lambda: {"runner_version": "1.2.3", "runner_commit": "abc"},
    )

    runner._augment_manifest_provenance(tmp_path, "openai")

    manifest = json.loads(manifest_path.read_text())
    assert manifest == {
        "graph_name": "existing",
        "engine_commit": "unknown",
        "runner_version": "1.2.3",
        "runner_commit": "abc",
        "provider": "openai",
    }


def test_augment_manifest_is_a_noop_when_manifest_missing(tmp_path):
    runner._augment_manifest_provenance(tmp_path, "anthropic")

    assert not (tmp_path / "manifest.json").exists()
