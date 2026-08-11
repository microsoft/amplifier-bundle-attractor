"""drive_engine startup provider preflight (issue #155).

The incident invoker was exactly this path: `attractor run` -> run_pipeline ->
drive_engine with DEFAULT_PROFILES mapping all three providers.  The 'openai'
PROFILE existed, but no OPENAI_API_KEY did -- so the critique_b node crashed
on every visit (`resolve_latest_for: no adapter found for provider 'openai'`)
and the graph's transient-recovery routing drained the entire iteration
budget against a defect that had nothing to do with the work.

These tests pin the fix: drive_engine now refuses AT STARTUP -- before any
node executes, before any LLM call -- naming the node, the provider, and the
missing credential.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from amplifier_module_pipeline_runner.runner import drive_engine

_DOT_DUAL_CRITIQUE = """\
digraph dual {
    graph [goal="dual-family critique fixture"]
    start [shape=Mdiamond]
    critique_b [shape=box, llm_provider="openai", prompt="independent review"]
    done [shape=Msquare]
    start -> critique_b -> done
}
"""


class _StubCoordinator:
    """Coordinator stub with a recording spawn capability."""

    def __init__(self) -> None:
        self.spawn_called = False
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-agent-openai": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
                "attractor-agent-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.spawn_called = True
        return {
            "output": json.dumps({"status": "success", "notes": "stub"}),
            "session_id": "child-1",
        }


def test_drive_engine_refuses_at_startup_missing_credential(
    tmp_path, monkeypatch
) -> None:
    """Incident configuration: openai profile mounted (DEFAULT_PROFILES),
    OPENAI_API_KEY absent -> refuse before ANY node executes."""
    from amplifier_module_loop_pipeline.preflight import ProviderPreflightError

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    coordinator = _StubCoordinator()

    with pytest.raises(ProviderPreflightError) as exc_info:
        asyncio.run(
            drive_engine(
                _DOT_DUAL_CRITIQUE,
                coordinator,
                cwd=tmp_path,
                logs_root=tmp_path / "logs",
                transform=True,
            )
        )

    msg = str(exc_info.value)
    assert "critique_b" in msg  # the failing node
    assert 'llm_provider="openai"' in msg  # its provider
    assert "OPENAI_API_KEY" in msg  # the missing credential
    assert not coordinator.spawn_called, (
        "refusal must happen before any node executes -- zero budget spent"
    )


def test_drive_engine_runs_when_declared_provider_is_serviceable(
    tmp_path, monkeypatch
) -> None:
    """Control: with the credential present, the same graph starts and runs
    to completion unaffected (presence is checked, never validity -- a
    hermetic harness sets the env var and mocks spawn)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-preflight")
    coordinator = _StubCoordinator()

    outcome = asyncio.run(
        drive_engine(
            _DOT_DUAL_CRITIQUE,
            coordinator,
            cwd=tmp_path,
            logs_root=tmp_path / "logs",
            transform=True,
        )
    )

    assert outcome.status.value == "success", outcome
    assert coordinator.spawn_called
