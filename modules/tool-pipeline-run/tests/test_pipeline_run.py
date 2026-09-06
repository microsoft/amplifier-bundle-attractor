"""Tests for tool-pipeline-run."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_pipeline_run import PipelineRunTool, mount


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


def test_tool_name():
    """Tool has correct name."""
    tool = PipelineRunTool(config={})
    assert tool.name == "run_pipeline"


def test_tool_description_mentions_pipeline():
    """Tool description mentions pipeline."""
    tool = PipelineRunTool(config={})
    assert "pipeline" in tool.description.lower()


def test_tool_input_schema_has_required_fields():
    """Tool exposes correct input schema."""
    tool = PipelineRunTool(config={})
    schema = tool.input_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "dot_file" in props
    assert "dot_source" in props
    assert "goal" in props
    assert "goal" in schema["required"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_goal_rejected():
    """Missing goal parameter returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"dot_source": "digraph { start -> done }"})
    assert not result.success
    assert "goal" in result.error["message"].lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_dot_source_rejected():
    """Neither dot_file nor dot_source returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"goal": "test goal"})
    assert not result.success
    assert (
        "dot_file" in result.error["message"] or "dot_source" in result.error["message"]
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_goal_rejected():
    """Empty string goal returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"goal": "", "dot_source": "digraph { start -> done }"})
    assert not result.success
    assert "goal" in result.error["message"].lower()


# ---------------------------------------------------------------------------
# DOT source resolution
# ---------------------------------------------------------------------------

MINIMAL_DOT = (
    "digraph Test { start [shape=Mdiamond]; done [shape=Msquare]; start -> done }"
)


def test_resolve_inline_dot_source():
    """Inline dot_source is used directly."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(dot_file=None, dot_source=MINIMAL_DOT)
    assert resolved == MINIMAL_DOT


def test_resolve_dot_file_path():
    """dot_file path reads the file contents."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(MINIMAL_DOT)
        f.flush()
        tmp_path = f.name
    try:
        tool = PipelineRunTool(config={})
        resolved = tool._resolve_dot_source(dot_file=tmp_path, dot_source=None)
        assert resolved == MINIMAL_DOT
    finally:
        os.unlink(tmp_path)


def test_resolve_dot_file_not_found():
    """Non-existent dot_file raises FileNotFoundError."""
    tool = PipelineRunTool(config={})
    with pytest.raises(FileNotFoundError):
        tool._resolve_dot_source(dot_file="/nonexistent/path.dot", dot_source=None)


def test_resolve_at_mention_path():
    """@mention path is resolved via coordinator mention_resolver capability."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(MINIMAL_DOT)
        f.flush()
        tmp_path = f.name
    try:
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = Path(tmp_path)

        mock_coordinator = MagicMock()
        mock_coordinator.get_capability.return_value = mock_resolver

        tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
        resolved = tool._resolve_dot_source(
            dot_file="@attractor:examples/pipelines/01-simple-linear.dot",
            dot_source=None,
        )
        assert resolved == MINIMAL_DOT
        mock_resolver.resolve.assert_called_once_with(
            "@attractor:examples/pipelines/01-simple-linear.dot"
        )
    finally:
        os.unlink(tmp_path)


def test_resolve_at_mention_no_resolver():
    """@mention path with no mention_resolver raises ValueError."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    with pytest.raises(ValueError, match="mention_resolver"):
        tool._resolve_dot_source(
            dot_file="@attractor:some/path.dot",
            dot_source=None,
        )


def test_dot_source_takes_precedence_over_dot_file():
    """When both dot_source and dot_file are provided, dot_source wins."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(
        dot_file="/some/file.dot",
        dot_source=MINIMAL_DOT,
    )
    assert resolved == MINIMAL_DOT


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------

# DOT source with model_stylesheet that requires anthropic and openai
DOT_WITH_STYLESHEET = """digraph Test {
    graph [
        goal="test",
        model_stylesheet="
            * { llm_provider: anthropic; llm_model: claude-sonnet-4-20250514; }
            .planning { llm_provider: openai; llm_model: o3; }
        "
    ]
    start [shape=Mdiamond]
    plan [class="planning", prompt="Plan"]
    impl [prompt="Implement"]
    done [shape=Msquare]
    start -> plan -> impl -> done
}"""

# DOT source with explicit llm_provider on a node (no stylesheet)
DOT_WITH_NODE_PROVIDER = """digraph Test {
    start [shape=Mdiamond]
    impl [llm_provider="gemini", prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}"""

# DOT source with no providers specified at all
DOT_NO_PROVIDERS = """digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}"""


def test_extract_required_providers_from_stylesheet():
    """Extract providers from model_stylesheet rules."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_WITH_STYLESHEET)
    assert "anthropic" in providers
    assert "openai" in providers


def test_extract_required_providers_from_node_attrs():
    """Extract providers from explicit node llm_provider attributes."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_WITH_NODE_PROVIDER)
    assert "gemini" in providers


def test_extract_required_providers_empty_when_none():
    """No providers extracted when none specified."""
    tool = PipelineRunTool(config={})
    providers = tool._extract_required_providers(DOT_NO_PROVIDERS)
    assert len(providers) == 0


def test_validate_providers_all_present():
    """Validation passes when all required providers are available."""
    tool = PipelineRunTool(config={})
    available = {"anthropic", "openai", "gemini"}
    required = {"anthropic", "openai"}
    missing = tool._check_missing_providers(required, available)
    assert len(missing) == 0


def test_validate_providers_some_missing():
    """Validation reports missing providers."""
    tool = PipelineRunTool(config={})
    available = {"anthropic"}
    required = {"anthropic", "openai", "gemini"}
    missing = tool._check_missing_providers(required, available)
    assert "openai" in missing
    assert "gemini" in missing
    assert "anthropic" not in missing


# ---------------------------------------------------------------------------
# Spawn execution
# ---------------------------------------------------------------------------

SIMPLE_DOT = """digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Do the thing"]
    done [shape=Msquare]
    start -> impl -> done
}"""


@pytest.mark.asyncio(loop_scope="session")
async def test_no_spawn_capability_returns_error():
    """When session.spawn is not available, returns a clear error."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None
    mock_coordinator.config = {}

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert not result.success
    assert "session.spawn" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_successful_spawn_returns_result():
    """Successful pipeline spawn returns structured result."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success", "notes": "Pipeline completed"}',
            "session_id": "child-session-123",
        }
    )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    assert result.output["status"] == "success"
    assert result.output["session_id"] == "child-session-123"
    mock_spawn.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_spawn_passes_correct_orchestrator_config():
    """Spawn is called with dot_source and goal in orchestrator_config."""
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "build a widget",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert spawn_kwargs_capture["agent_name"] == "attractor-pipeline-runner"
    assert spawn_kwargs_capture["instruction"] == "build a widget"
    orch_config = spawn_kwargs_capture["orchestrator_config"]
    assert orch_config["dot_source"] == SIMPLE_DOT


@pytest.mark.asyncio(loop_scope="session")
async def test_spawn_failure_returns_error():
    """When session.spawn raises an exception, tool returns error."""
    mock_spawn = AsyncMock(side_effect=RuntimeError("spawn failed"))

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert not result.success
    assert "spawn failed" in result.error["message"]


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_display_system_start_message():
    """DisplaySystem receives a start message when pipeline begins."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_display = MagicMock()
    mock_display.show_message = MagicMock()

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()
    mock_coordinator.display_system = mock_display

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    # DisplaySystem should have been called at least for start
    assert mock_display.show_message.call_count >= 1
    first_call_msg = mock_display.show_message.call_args_list[0][0][0]
    assert "pipeline" in first_call_msg.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_hook_events_emitted():
    """Hook events are emitted for pipeline start and complete."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_hooks = MagicMock()
    mock_hooks.emit = AsyncMock()

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()
    mock_coordinator.hooks = mock_hooks
    # No display_system to test hooks independently
    mock_coordinator.display_system = None

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    # Should have emitted start and complete events
    event_names = [call[0][0] for call in mock_hooks.emit.call_args_list]
    assert "pipeline:tool:start" in event_names
    assert "pipeline:tool:complete" in event_names


@pytest.mark.asyncio(loop_scope="session")
async def test_no_crash_without_display_or_hooks():
    """Progress reporting is graceful when display_system and hooks are absent."""
    mock_spawn = AsyncMock(
        return_value={
            "output": '{"status": "success"}',
            "session_id": "child-123",
        }
    )

    mock_coordinator = MagicMock(spec=[])  # empty spec = no attributes
    mock_coordinator.get_capability = lambda name: (
        mock_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    # Should not crash
    result = await tool.execute(
        {
            "goal": "test",
            "dot_source": SIMPLE_DOT,
        }
    )
    assert result.success


# ---------------------------------------------------------------------------
# Pipeline output parsing (Bug 1 Fix B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_null_notes_produces_meaningful_output():
    """Tool synthesizes a summary when pipeline returns null notes."""
    pipeline_output = json.dumps(
        {
            "status": "success",
            "notes": None,
            "failure_reason": None,
            "nodes_completed": 3,
            "node_statuses": {
                "plan": "success",
                "implement": "success",
                "test": "success",
            },
        }
    )
    mock_spawn = AsyncMock(
        return_value={
            "output": pipeline_output,
            "session_id": "child-null-notes",
        }
    )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    notes = result.output["notes"]
    assert notes  # Must not be empty
    assert len(notes) > 10  # Must be meaningful
    assert "3 nodes executed" in notes
    assert "plan=success" in notes


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_string_notes_synthesizes_summary():
    """Tool synthesizes a summary when pipeline returns empty-string notes."""
    pipeline_output = json.dumps(
        {
            "status": "success",
            "notes": "",
            "failure_reason": None,
            "nodes_completed": 2,
            "node_statuses": {"a": "success", "b": "fail"},
        }
    )
    mock_spawn = AsyncMock(
        return_value={
            "output": pipeline_output,
            "session_id": "child-empty-notes",
        }
    )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    notes = result.output["notes"]
    assert notes  # Must not be empty
    assert "2 nodes executed" in notes


@pytest.mark.asyncio(loop_scope="session")
async def test_result_includes_message_field():
    """Tool result includes a 'message' field signaling pipeline completion."""
    pipeline_output = json.dumps(
        {
            "status": "success",
            "notes": "All good",
            "failure_reason": None,
            "nodes_completed": 1,
            "node_statuses": {"impl": "success"},
        }
    )
    mock_spawn = AsyncMock(
        return_value={
            "output": pipeline_output,
            "session_id": "child-msg",
        }
    )

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    assert "message" in result.output
    assert "complete" in result.output["message"].lower()


# ---------------------------------------------------------------------------
# $param support (Task 3.2)
# ---------------------------------------------------------------------------


def test_input_schema_includes_params():
    """Tool input schema should include a 'params' property."""
    tool = PipelineRunTool(config={})
    schema = tool.input_schema
    assert "params" in schema["properties"]
    assert schema["properties"]["params"]["type"] == "object"


@pytest.mark.asyncio(loop_scope="session")
async def test_params_forwarded_in_orchestrator_config():
    """Params from tool input are forwarded to orchestrator_config in spawn."""
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success", "notes": "done"}',
            "session_id": "child-params",
        }

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "build a web app",
            "dot_source": SIMPLE_DOT,
            "params": {"language": "Python", "framework": "FastAPI"},
        }
    )

    orch_config = spawn_kwargs_capture["orchestrator_config"]
    assert orch_config["params"] == {"language": "Python", "framework": "FastAPI"}


@pytest.mark.asyncio(loop_scope="session")
async def test_no_params_omits_key_from_orchestrator_config():
    """When no params are provided, orchestrator_config has no params key."""
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success", "notes": "done"}',
            "session_id": "child-no-params",
        }

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    await tool.execute(
        {
            "goal": "build a thing",
            "dot_source": SIMPLE_DOT,
        }
    )

    orch_config = spawn_kwargs_capture["orchestrator_config"]
    assert "params" not in orch_config


# ---------------------------------------------------------------------------
# Namespace neutrality: "mention_example" + "runner_agent" config
# (attractor-24e stage 1)
#
# This module used to name one specific bundle in its own source: the
# mention namespace shown to the calling LLM (in `description` and
# `input_schema["dot_file"]["description"]`) and the agent name spawned to
# execute the pipeline. Both are now parameters whose DEFAULTS name no
# bundle at all; a mounting bundle supplies its own values.
#
# None of this ever touched @mention *resolution* -- mention_resolver
# .resolve() handles any namespace generically (see
# test_resolve_at_mention_path above, and
# test_configured_namespace_resolves_end_to_end below, which proves it with
# a namespace this module has never heard of). The coupling was in
# LLM-facing text and in a spawn default, and text that confidently names
# some other bundle's namespace is exactly how a tool teaches its caller to
# write a mention that resolves nowhere.
# ---------------------------------------------------------------------------

# The exact LLM-facing text and runner agent this module produced for its
# original consumer, back when both were baked into the source. That
# consumer now passes these same values as config (see the tool-pipeline-run
# mount in bundles/attractor-interactive.yaml), so this pair is the
# consumer-compat proof: same values in, byte-identical behavior out.
PRIOR_CONSUMER_MENTION_EXAMPLE = "@attractor:examples/pipelines/01-simple-linear.dot"
PRIOR_CONSUMER_RUNNER_AGENT = "attractor-pipeline-runner"


def test_custom_mention_example_lands_in_input_schema():
    """A configured mention_example overrides the dot_file schema description.

    RED pre-change: input_schema's dot_file description was a fixed string
    hard-coding "@attractor:examples/pipelines/01-simple-linear.dot" with no
    config indirection at all, so this assertion could not pass no matter
    what config was supplied.
    """
    tool = PipelineRunTool(config={"mention_example": "@my-bundle:pipelines/demo.dot"})
    schema = tool.input_schema
    description = schema["properties"]["dot_file"]["description"]
    assert "@my-bundle:pipelines/demo.dot" in description
    assert "attractor" not in description.lower()


def test_custom_mention_example_lands_in_tool_description():
    """A configured mention_example's namespace overrides the tool description.

    RED pre-change: `description` was a class-level constant hard-coding
    "@attractor:... mentions", unreachable via config.
    """
    tool = PipelineRunTool(config={"mention_example": "@my-bundle:pipelines/demo.dot"})
    assert "@my-bundle:... mentions" in tool.description
    assert "attractor" not in tool.description.lower()


def test_default_mention_example_names_no_bundle():
    """An unconfigured mount advertises a placeholder, never a real namespace.

    The de-attractorization pin. An unconfigured mount must read as
    unconfigured: a default that named a real bundle would point every
    other bundle's LLM at a namespace it has not registered, and the
    resulting mention fails at resolution time rather than at read time.

    RED pre-change: the default was
    "@attractor:examples/pipelines/01-simple-linear.dot", so both the
    placeholder assertions and the "names no bundle" assertions failed.
    """
    tool = PipelineRunTool(config={})
    schema_description = tool.input_schema["properties"]["dot_file"]["description"]

    assert "@<bundle>:path/to/pipeline.dot" in schema_description
    assert "@<bundle>:... mentions" in tool.description

    # Two-sided: no bundle name may creep back into either surface.
    assert "attractor" not in tool.description.lower()
    assert "attractor" not in schema_description.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_default_runner_agent_names_no_bundle():
    """An unconfigured mount spawns the neutral runner name, not a bundle's.

    RED pre-change: the default was "attractor-pipeline-runner".
    """
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success"}',
            "session_id": "child-default-runner",
        }

    mock_coordinator = MagicMock()
    mock_coordinator.get_capability = lambda name: (
        mock_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute({"goal": "test goal", "dot_source": SIMPLE_DOT})

    assert result.success
    assert spawn_kwargs_capture["agent_name"] == "pipeline-runner"
    assert "attractor" not in spawn_kwargs_capture["agent_name"]


def test_prior_consumer_config_reproduces_its_llm_text_byte_identical():
    """The original consumer's values reproduce its original text exactly.

    Consumer-compat proof for the LLM-facing half: the bundle that used to
    get this text from the module's baked-in defaults now passes the same
    values as config, and gets the same two strings byte-for-byte.
    """
    tool = PipelineRunTool(config={"mention_example": PRIOR_CONSUMER_MENTION_EXAMPLE})
    assert tool.description == (
        "Run a DOT graph pipeline. Provide a pipeline definition via "
        "'dot_file' (path to a .dot file, supports @attractor:... mentions) "
        "or 'dot_source' (inline DOT digraph string), plus a 'goal' "
        "describing the task. The pipeline executes as a child session "
        "and returns the result when complete."
    )
    schema = tool.input_schema
    assert schema["properties"]["dot_file"]["description"] == (
        "Path to a .dot pipeline file. Supports @mention "
        "syntax (e.g. @attractor:examples/pipelines/01-simple-linear.dot)."
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_prior_consumer_config_still_spawns_its_own_runner_agent():
    """The original consumer's runner_agent value still reaches session.spawn.

    Consumer-compat proof for the spawn half. That bundle already passed
    runner_agent explicitly before this change, so this asserts the path it
    depends on is the one that survived when the default moved.
    """
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success"}',
            "session_id": "child-prior-consumer",
        }

    mock_coordinator = MagicMock()
    mock_coordinator.get_capability = lambda name: (
        mock_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {PRIOR_CONSUMER_RUNNER_AGENT: {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": PRIOR_CONSUMER_RUNNER_AGENT},
        coordinator=mock_coordinator,
    )
    result = await tool.execute({"goal": "test goal", "dot_source": SIMPLE_DOT})

    assert result.success
    assert spawn_kwargs_capture["agent_name"] == PRIOR_CONSUMER_RUNNER_AGENT
    assert result.output["runner_agent"] == PRIOR_CONSUMER_RUNNER_AGENT


@pytest.mark.asyncio(loop_scope="session")
async def test_configured_namespace_resolves_end_to_end():
    """A mount configured for a foreign namespace resolves mentions in it.

    The parameterization proof end-to-end: mount() with a namespace this
    module has never heard of, hand the mounted tool a dot_file in that
    namespace, and the file behind it is what reaches the spawned pipeline
    -- while the tool advertises that same namespace to its own LLM.

    RED pre-change: the advertised-example assertion failed (the mounted
    tool advertised "@attractor:..." regardless of the namespace in play),
    which is the whole hazard -- resolution worked while the text told the
    LLM to write a mention into a bundle that was not there.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(SIMPLE_DOT)
        f.flush()
        tmp_path = f.name
    try:
        mention = "@neutral-bundle:pipelines/demo.dot"

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = Path(tmp_path)

        spawn_kwargs_capture = {}

        async def mock_spawn(**kwargs):
            spawn_kwargs_capture.update(kwargs)
            return {
                "output": '{"status": "success"}',
                "session_id": "child-neutral-namespace",
            }

        capabilities = {
            "mention_resolver": mock_resolver,
            "session.spawn": mock_spawn,
        }
        mock_coordinator = MagicMock()
        mock_coordinator.get_capability = capabilities.get
        mock_coordinator.config = {"agents": {"neutral-runner": {}}}
        mock_coordinator.session = MagicMock()
        mock_coordinator.mount = AsyncMock()

        await mount(
            mock_coordinator,
            {"mention_example": mention, "runner_agent": "neutral-runner"},
        )
        tool = mock_coordinator.mount.call_args[0][1]

        # What the mounted tool tells its LLM to write ...
        assert mention in tool.input_schema["properties"]["dot_file"]["description"]

        # ... is what it then resolves and runs.
        result = await tool.execute({"goal": "test goal", "dot_file": mention})

        assert result.success
        mock_resolver.resolve.assert_called_once_with(mention)
        assert spawn_kwargs_capture["orchestrator_config"]["dot_source"] == SIMPLE_DOT
        assert spawn_kwargs_capture["agent_name"] == "neutral-runner"
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio(loop_scope="session")
async def test_custom_runner_agent_lands_in_spawn_call():
    """A configured runner_agent overrides the neutral default at spawn."""
    spawn_kwargs_capture = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs_capture.update(kwargs)
        return {
            "output": '{"status": "success"}',
            "session_id": "child-custom-runner",
        }

    mock_coordinator = MagicMock()
    mock_coordinator.get_capability = lambda name: (
        mock_spawn if name == "session.spawn" else None
    )
    mock_coordinator.config = {"agents": {"my-custom-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "my-custom-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    result = await tool.execute(
        {
            "goal": "test goal",
            "dot_source": SIMPLE_DOT,
        }
    )

    assert result.success
    assert spawn_kwargs_capture["agent_name"] == "my-custom-pipeline-runner"
    assert result.output["runner_agent"] == "my-custom-pipeline-runner"
