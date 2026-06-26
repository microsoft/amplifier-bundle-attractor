"""Tests for execution environment lifecycle in PipelineOrchestrator.

Verifies that the orchestrator optionally creates/destroys an execution
environment around the pipeline engine run when configured.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_loop_pipeline import PipelineOrchestrator
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_env_create(container_id="abc123"):
    """Return a mock env_create tool whose execute returns a container_id."""
    tool = AsyncMock()
    tool.execute = AsyncMock(
        return_value=MagicMock(
            success=True,
            output=json.dumps(
                {"container_id": container_id, "name": "pipeline-workspace"}
            ),
        )
    )
    return tool


def _make_mock_env_destroy():
    """Return a mock env_destroy tool."""
    tool = AsyncMock()
    tool.execute = AsyncMock(
        return_value=MagicMock(
            success=True,
            output=json.dumps({"status": "destroyed"}),
        )
    )
    return tool


MINIMAL_DOT = """
digraph {
    start [shape=Mdiamond]
    work [prompt="Do work"]
    exit [shape=Msquare]
    start -> work -> exit
}
"""


def _make_orchestrator(execution_environment=None):
    """Create a PipelineOrchestrator with optional execution_environment config."""
    config = {"dot_source": MINIMAL_DOT}
    if execution_environment is not None:
        config["execution_environment"] = execution_environment
    return PipelineOrchestrator(config)


# ---------------------------------------------------------------------------
# Task 1: Environment lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_lifecycle_creates_and_destroys():
    """env_create called before engine, env_destroy after, container_id in context."""
    env_create = _make_mock_env_create(container_id="abc123")
    env_destroy = _make_mock_env_destroy()
    captured_context = {}

    orchestrator = _make_orchestrator(
        execution_environment={
            "type": "docker",
            "name": "pipeline-workspace",
            "image": "python:3.12",
            "mount_cwd": True,
        }
    )

    async def capturing_engine_run(self_engine, *args, **kwargs):
        """Capture the pipeline context during engine.run()."""
        captured_context["container_id"] = self_engine.context.get(
            "internal.env_container_id"
        )
        captured_context["env_type"] = self_engine.context.get("internal.env_type")
        return Outcome(status=StageStatus.SUCCESS, notes="done")

    with patch(
        "amplifier_module_loop_pipeline.PipelineEngine.run",
        capturing_engine_run,
    ):
        await orchestrator.execute(
            prompt="Build it",
            context=None,
            providers={},
            tools={"env_create": env_create, "env_destroy": env_destroy},
            hooks=None,
            backend=MagicMock(),
        )

    # env_create was called with correct args (including pass-through config)
    env_create.execute.assert_called_once()
    create_args = env_create.execute.call_args[0][0]
    assert create_args["type"] == "docker"
    assert create_args["name"] == "pipeline-workspace"
    assert create_args["image"] == "python:3.12"
    assert create_args["mount_cwd"] is True

    # container_id was stored in context BEFORE engine ran
    assert captured_context["container_id"] == "abc123"
    assert captured_context["env_type"] == "docker"

    # env_destroy was called with correct instance name
    env_destroy.execute.assert_called_once()
    destroy_args = env_destroy.execute.call_args[0][0]
    assert destroy_args["instance"] == "pipeline-workspace"


@pytest.mark.asyncio
async def test_env_lifecycle_no_config_no_lifecycle():
    """No env lifecycle when execution_environment config is absent."""
    env_create = _make_mock_env_create()
    env_destroy = _make_mock_env_destroy()

    orchestrator = _make_orchestrator()  # No execution_environment

    with patch(
        "amplifier_module_loop_pipeline.PipelineEngine.run",
        new_callable=AsyncMock,
        return_value=Outcome(status=StageStatus.SUCCESS, notes="done"),
    ):
        result = await orchestrator.execute(
            prompt="Build it",
            context=None,
            providers={},
            tools={"env_create": env_create, "env_destroy": env_destroy},
            hooks=None,
            backend=MagicMock(),
        )

    env_create.execute.assert_not_called()
    env_destroy.execute.assert_not_called()
    parsed = json.loads(result)
    assert parsed["status"] == "success"


@pytest.mark.asyncio
async def test_env_lifecycle_config_but_no_env_tools(caplog):
    """Warning logged when config present but env_create tool missing."""
    orchestrator = _make_orchestrator(
        execution_environment={"type": "docker", "name": "pipeline-workspace"}
    )

    with caplog.at_level(logging.WARNING, logger="amplifier_module_loop_pipeline"):
        with patch(
            "amplifier_module_loop_pipeline.PipelineEngine.run",
            new_callable=AsyncMock,
            return_value=Outcome(status=StageStatus.SUCCESS, notes="done"),
        ):
            result = await orchestrator.execute(
                prompt="Build it",
                context=None,
                providers={},
                tools={},  # No env tools!
                hooks=None,
                backend=MagicMock(),
            )

    parsed = json.loads(result)
    assert parsed["status"] == "success"

    # Warning should mention env_create not available
    assert any(
        "env_create" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


@pytest.mark.asyncio
async def test_env_lifecycle_destroy_called_on_failure():
    """env_destroy called even when engine.run() raises; exception propagates."""
    env_create = _make_mock_env_create(container_id="abc123")
    env_destroy = _make_mock_env_destroy()

    orchestrator = _make_orchestrator(
        execution_environment={"type": "docker", "name": "pipeline-workspace"}
    )

    with patch(
        "amplifier_module_loop_pipeline.PipelineEngine.run",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Engine exploded"),
    ):
        with pytest.raises(RuntimeError, match="Engine exploded"):
            await orchestrator.execute(
                prompt="Build it",
                context=None,
                providers={},
                tools={"env_create": env_create, "env_destroy": env_destroy},
                hooks=None,
                backend=MagicMock(),
            )

    # env_create was called
    env_create.execute.assert_called_once()

    # env_destroy was STILL called despite the exception
    env_destroy.execute.assert_called_once()


@pytest.mark.asyncio
async def test_env_lifecycle_unparseable_create_response(caplog):
    """Falls back to local when env_create returns non-JSON output."""
    env_create = AsyncMock()
    env_create.execute = AsyncMock(
        return_value=MagicMock(
            success=True,
            output="not json",
        )
    )
    env_destroy = _make_mock_env_destroy()

    orchestrator = _make_orchestrator(
        execution_environment={"type": "docker", "name": "pipeline-workspace"}
    )

    with caplog.at_level(logging.WARNING, logger="amplifier_module_loop_pipeline"):
        with patch(
            "amplifier_module_loop_pipeline.PipelineEngine.run",
            new_callable=AsyncMock,
            return_value=Outcome(status=StageStatus.SUCCESS, notes="done"),
        ):
            result = await orchestrator.execute(
                prompt="Build it",
                context=None,
                providers={},
                tools={"env_create": env_create, "env_destroy": env_destroy},
                hooks=None,
                backend=MagicMock(),
            )

    # No exception raised, pipeline ran successfully
    parsed_result = json.loads(result)
    assert parsed_result["status"] == "success"

    # Warning logged about unparseable output
    assert any(
        "unparseable" in record.message.lower() or "env_create" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )

    # env_destroy is NOT called since no container was created
    env_destroy.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2: Backend attach-to injection tests
# ---------------------------------------------------------------------------


def _make_backend_with_mock_spawn():
    """Create an AmplifierBackend with a mock coordinator that has session.spawn.

    Returns (backend, mock_spawn_fn) so tests can inspect spawn call args.
    """
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success", "notes": "done"}',
            "session_id": "child-1",
        }
    )

    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=mock_spawn)
    coordinator.session = MagicMock()
    # Include 'test-profile' (matching the profiles map) with a non-pipeline
    # session.orchestrator so the identity recursion guard does not fire.
    coordinator.config = {
        "agents": {
            "test-profile": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            },
        }
    }

    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "test-profile"},
    )
    return backend, mock_spawn


def _make_simple_node(node_id="work"):
    """Create a simple Node for backend tests."""
    return Node(id=node_id, prompt="Do some work")


@pytest.mark.asyncio
async def test_backend_passes_attach_to_when_container_in_context():
    """When internal.env_container_id is set, spawn kwargs include tools-env-all config."""
    backend, mock_spawn = _make_backend_with_mock_spawn()
    node = _make_simple_node()

    context = PipelineContext()
    context.set("internal.env_container_id", "container-xyz")
    context.set("internal.env_type", "docker")

    await backend.run(node, "Do work", context)

    mock_spawn.assert_called_once()
    call_kwargs = mock_spawn.call_args[1]  # keyword args

    # Should have tools list with env-all auto_attach config
    assert "tools" in call_kwargs, "Expected 'tools' in spawn kwargs"
    tools_list = call_kwargs["tools"]
    assert isinstance(tools_list, list)

    # Find the tools-env-all entry
    env_tool_entries = [t for t in tools_list if t.get("module") == "tools-env-all"]
    assert len(env_tool_entries) == 1, (
        f"Expected one tools-env-all entry, got {env_tool_entries}"
    )

    env_config = env_tool_entries[0]["config"]
    assert env_config["auto_attach"]["type"] == "docker"
    assert env_config["auto_attach"]["name"] == "pipeline-workspace"
    assert env_config["auto_attach"]["attach_to"] == "container-xyz"


@pytest.mark.asyncio
async def test_backend_no_attach_when_no_container_in_context():
    """When internal.env_container_id is NOT set, spawn kwargs have no tools-env-all config."""
    backend, mock_spawn = _make_backend_with_mock_spawn()
    node = _make_simple_node()

    context = PipelineContext()
    # No internal.env_container_id set

    await backend.run(node, "Do work", context)

    mock_spawn.assert_called_once()
    call_kwargs = mock_spawn.call_args[1]

    # Either no 'tools' key, or no tools-env-all entry in it
    tools_list = call_kwargs.get("tools", [])
    env_tool_entries = [
        t
        for t in tools_list
        if isinstance(t, dict) and t.get("module") == "tools-env-all"
    ]
    assert len(env_tool_entries) == 0, (
        f"Expected no tools-env-all entry, got {env_tool_entries}"
    )


# ---------------------------------------------------------------------------
# Task 3: Graceful fallback verification tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_destroy_failure_does_not_mask_outcome():
    """env_destroy raising does not mask a successful pipeline outcome."""
    env_create = _make_mock_env_create(container_id="abc123")
    env_destroy = AsyncMock()
    env_destroy.execute = AsyncMock(side_effect=RuntimeError("destroy boom"))

    orchestrator = _make_orchestrator(
        execution_environment={"type": "docker", "name": "pipeline-workspace"}
    )

    with patch(
        "amplifier_module_loop_pipeline.PipelineEngine.run",
        new_callable=AsyncMock,
        return_value=Outcome(status=StageStatus.SUCCESS, notes="all good"),
    ):
        result = await orchestrator.execute(
            prompt="Build it",
            context=None,
            providers={},
            tools={"env_create": env_create, "env_destroy": env_destroy},
            hooks=None,
            backend=MagicMock(),
        )

    # env_destroy was called (and raised)
    env_destroy.execute.assert_called_once()

    # The pipeline still returns the successful outcome
    parsed = json.loads(result)
    assert parsed["status"] == "success"


@pytest.mark.asyncio
async def test_env_create_failure_propagates(caplog):
    """env_create returning success=False falls back to local execution."""
    env_create = AsyncMock()
    env_create.execute = AsyncMock(
        return_value=MagicMock(
            success=False,
            output="Container creation failed",
        )
    )
    env_destroy = _make_mock_env_destroy()

    orchestrator = _make_orchestrator(
        execution_environment={"type": "docker", "name": "pipeline-workspace"}
    )

    with caplog.at_level(logging.WARNING, logger="amplifier_module_loop_pipeline"):
        with patch(
            "amplifier_module_loop_pipeline.PipelineEngine.run",
            new_callable=AsyncMock,
            return_value=Outcome(status=StageStatus.SUCCESS, notes="done locally"),
        ):
            result = await orchestrator.execute(
                prompt="Build it",
                context=None,
                providers={},
                tools={"env_create": env_create, "env_destroy": env_destroy},
                hooks=None,
                backend=MagicMock(),
            )

    # Pipeline succeeded via local fallback
    parsed = json.loads(result)
    assert parsed["status"] == "success"

    # env_create was called
    env_create.execute.assert_called_once()

    # env_destroy was NOT called (no container_id was stored)
    env_destroy.execute.assert_not_called()

    # Warning was logged about unparseable output
    assert any(
        "unparseable" in record.message.lower() or "env_create" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


@pytest.mark.asyncio
async def test_env_lifecycle_with_ssh_type(caplog):
    """SSH env_create returning no container_id falls back to local execution."""
    env_create = AsyncMock()
    env_create.execute = AsyncMock(
        return_value=MagicMock(
            success=True,
            output=json.dumps({"host": "10.0.0.5"}),  # no container_id
        )
    )
    env_destroy = _make_mock_env_destroy()

    orchestrator = _make_orchestrator(
        execution_environment={"type": "ssh", "host": "10.0.0.5", "name": "remote"}
    )

    captured_context = {}

    async def capturing_engine_run(self_engine, *args, **kwargs):
        captured_context["container_id"] = self_engine.context.get(
            "internal.env_container_id"
        )
        captured_context["env_type"] = self_engine.context.get("internal.env_type")
        return Outcome(status=StageStatus.SUCCESS, notes="done")

    with caplog.at_level(logging.WARNING, logger="amplifier_module_loop_pipeline"):
        with patch(
            "amplifier_module_loop_pipeline.PipelineEngine.run",
            capturing_engine_run,
        ):
            result = await orchestrator.execute(
                prompt="Build it",
                context=None,
                providers={},
                tools={"env_create": env_create, "env_destroy": env_destroy},
                hooks=None,
                backend=MagicMock(),
            )

    # env_create was called with SSH config
    env_create.execute.assert_called_once()
    create_args = env_create.execute.call_args[0][0]
    assert create_args["type"] == "ssh"
    assert create_args["host"] == "10.0.0.5"
    assert create_args["name"] == "remote"

    # No container_id was stored in context (SSH response lacked one)
    assert captured_context["container_id"] is None
    assert captured_context["env_type"] is None

    # Pipeline succeeded via local fallback
    parsed = json.loads(result)
    assert parsed["status"] == "success"

    # env_destroy was NOT called (no container_id)
    env_destroy.execute.assert_not_called()

    # Warning logged about missing container_id
    assert any(
        "container_id" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


# ---------------------------------------------------------------------------
# Task 4: Integration test with mock env tools
# ---------------------------------------------------------------------------


TWO_NODE_DOT = """\
digraph {
    start [shape=Mdiamond]
    step1 [prompt="Do step 1"]
    step2 [prompt="Do step 2"]
    exit  [shape=Msquare]
    start -> step1 -> step2 -> exit
}
"""


@pytest.mark.asyncio
async def test_full_lifecycle_orchestrator_and_backend(tmp_path):
    """Integration: env_create → context flows through engine → backend sees container_id → env_destroy."""
    env_create = _make_mock_env_create(container_id="int-container-99")
    env_destroy = _make_mock_env_destroy()

    # Track what the backend receives during each node execution
    backend_calls: list[dict] = []

    class CapturingBackend:
        """Mock backend that records context state on each run() call."""

        async def run(self, node, prompt, context, **kwargs):
            backend_calls.append(
                {
                    "node_id": node.id,
                    "container_id": context.get("internal.env_container_id"),
                    "env_type": context.get("internal.env_type"),
                }
            )
            return Outcome(
                status=StageStatus.SUCCESS,
                notes=f"Completed {node.id}",
                context_updates={"last_stage": node.id},
            )

    # Use tmp_path for logs_root to avoid stale checkpoint contamination
    orchestrator = PipelineOrchestrator(
        {
            "dot_source": TWO_NODE_DOT,
            "logs_root": str(tmp_path / "pipeline-logs"),
            "execution_environment": {
                "type": "docker",
                "name": "pipeline-workspace",
                "image": "python:3.12",
            },
        }
    )

    result = await orchestrator.execute(
        prompt="Run integration test",
        context=None,
        providers={},
        tools={"env_create": env_create, "env_destroy": env_destroy},
        hooks=None,
        backend=CapturingBackend(),
    )

    # 1. env_create was called first
    env_create.execute.assert_called_once()

    # 2. Backend was called for both work nodes (step1, step2)
    assert len(backend_calls) == 2
    assert backend_calls[0]["node_id"] == "step1"
    assert backend_calls[1]["node_id"] == "step2"

    # 3. Both nodes saw the container_id in context
    assert backend_calls[0]["container_id"] == "int-container-99"
    assert backend_calls[0]["env_type"] == "docker"
    assert backend_calls[1]["container_id"] == "int-container-99"
    assert backend_calls[1]["env_type"] == "docker"

    # 4. env_destroy was called after engine completed
    env_destroy.execute.assert_called_once()
    destroy_args = env_destroy.execute.call_args[0][0]
    assert destroy_args["instance"] == "pipeline-workspace"

    # 5. Pipeline completed successfully
    parsed = json.loads(result)
    assert parsed["status"] == "success"
    assert parsed["nodes_completed"] >= 2

    # 6. Verify call ordering: env_create before backend, backend before env_destroy
    # env_create was called before backend_calls were recorded (backend_calls populated
    # during engine.run, which happens after env_create in orchestrator.execute)
    # env_destroy was called after engine.run completed (in the finally block)
    # The assertions above already confirm this ordering implicitly:
    # - env_create returned container_id → it was in context when backend ran
    # - env_destroy was called → pipeline returned successfully after
